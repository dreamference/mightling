"""
Goose AI Agent Session Runner & vLLM Readiness Supervisor.

This module provides the GooseRunner class which auto-launches local vLLM servers when offline,
polls server health endpoints while streaming real-time startup logs, verifies Goose installation,
and executes interactive pair-programming sessions or autonomous coding tasks.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import List, Optional
from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager, VLLMStartupMonitor
from dreamference.runner.goose_installer import GooseInstaller
from dreamference.runner.sandbox_manager import SandboxManager

class GooseRunner:
    """
    Supervisor class managing vLLM endpoint polling, Goose installation, container sandbox prefixes,
    and agent task execution.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes GooseRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def get_goose_executable(self) -> Optional[str]:
        """Delegates to GooseInstaller."""
        return GooseInstaller.get_goose_executable()

    def is_goose_installed(self) -> bool:
        """Delegates to GooseInstaller."""
        return GooseInstaller.is_installed()

    def install_goose(self) -> bool:
        """Delegates to GooseInstaller."""
        return GooseInstaller.install_if_missing()

    def get_sandbox_command_prefix(self) -> List[str]:
        """Delegates to SandboxManager."""
        return SandboxManager.get_prefix(self.config.sandbox, os.getcwd())

    def estimate_vllm_load_memory_gb(self) -> Optional[float]:
        """
        Estimates expected unified-memory growth while loading configured primary/draft models.

        Returns:
            Optional[float]: Sum of MODEL_MATRIX min_memory_gb values, or None if unknown.
        """
        from dreamference.hardware import MODEL_MATRIX
        total = 0.0
        known = False
        for key in (self.config.model, self.config.draft_model):
            if not key:
                continue
            spec = MODEL_MATRIX.get(str(key).lower())
            if spec:
                total += float(spec.min_memory_gb)
                known = True
        return total if known else None

    def wait_for_vllm(self, poll_interval: float = 1.0, max_wait: Optional[float] = None, auto_launch: bool = False) -> bool:
        """
        Checks if local vLLM HTTP endpoint is online and healthy.
        If offline, prints an error and instructions to start vLLM, then returns False immediately.

        Returns:
            bool: True if vLLM endpoint is online and healthy, False otherwise.
        """
        try:
            if self.vllm_manager.check_health(timeout=1.0):
                self._pre_warm_goose_prompt()
                return True
        except (KeyboardInterrupt, SystemExit):
            print("\n🛑 Cancelled checking vLLM server.")
            return False

        print(f"❌ Local vLLM server at {self.config.vllm_host} is not running.")
        print(f"💡 Start vLLM in another terminal via: `dream start_server`")
        return False


    def _pre_warm_goose_prompt(self) -> None:
        """
        Silently pre-warms the FP8 KV cache with Goose's static system prompt + MCP tool schemas.
        Sends a max_tokens=1 chat completion immediately after vLLM health check succeeds.
        This eliminates first-turn TTFT delay for the massive Goose context.
        """
        try:
            url = f"{self.config.vllm_host}/v1/chat/completions"
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": "You are Goose, an autonomous coding agent. You have access to the local filesystem and can execute shell commands."},
                    {"role": "user", "content": "Pre-warm system prompt and MCP tools."}
                ],
                "tools": [{"type": "function", "function": {"name": "dreamference_mcp", "description": "Dreamference MCP tool", "parameters": {"type": "object", "properties": {}}}}],
                "max_tokens": 1,
                "temperature": 0.0
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10):
                pass  # silent success; KV cache now primed
        except Exception:
            pass  # pre-warm is best-effort; never block startup

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Executes an interactive Goose pair-programming session or autonomous coding task.

        Args:
            prompt (Optional[str]): Task prompt text. If provided, executes non-interactive `goose run --text <prompt>`.
            debug (bool): Enable verbose Goose debug logging.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        env = os.environ.copy()
        env.update(self.config.get_env_vars())
        
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        # Use a fresh temporary Goose config on every launch (never touches ~/.config/goose)
        temp_goose_config = self.config.write_temporary_goose_config()

        valid, msg = self.config.validate_model()
        if not valid:
            print(f"⚠️ Warning: {msg}")

        # Verify local vLLM server is running; exit immediately if offline
        if not self.wait_for_vllm():
            return 1


        # Provision Goose CLI binary if missing
        if not self.is_goose_installed():
            self.install_goose()

        if not self.is_goose_installed():
            print("⚠️ Goose CLI (`goose`) could not be automatically installed.")
            print("💡 Install Goose manually via: `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash`")
            return 1

        goose_bin = self.get_goose_executable()
        if not goose_bin:
            return 1
            
        sandbox_prefix = self.get_sandbox_command_prefix()

        cmd: List[str] = []
        if sandbox_prefix:
            print(f"🛡️  Enforcing Rootless Subagent Sandbox ({self.config.sandbox.upper()})...")
            cmd.extend(sandbox_prefix)

        env["GOOSE_CONFIG_PATH"] = str(temp_goose_config)

        cmd.append(goose_bin)

        if prompt:
            cmd.extend(["run", "--text", prompt])
        else:
            cmd.append("session")

        if debug:
            cmd.append("--debug")


        print(f"🚀 Launching Goose AI Agent on GB10 local endpoint ({self.config.model})...")
        
        # Display active Goose configuration in a single clean line
        try:
            from rich.console import Console
            from dreamference.hardware import resolve_model_hf_repo
            console = Console()
            resolved = resolve_model_hf_repo(self.config.model)
            endpoint = self.config.vllm_host.rstrip("/") + "/v1"
            exts = "developer, jetbrains_mcp"
            cave = " | [bold red]CAVE MODE[/bold red]" if self.config.cave_mode else ""
            console.print(
                f"[bold cyan]🪿 Goose Config:[/bold cyan] "
                f"[bold green]Provider:[/bold green] openai | "
                f"[bold green]Model:[/bold green] {resolved} | "
                f"[bold green]Endpoint:[/bold green] {endpoint} | "
                f"[bold green]Extensions:[/bold green] {exts} | "
                f"[bold green]Telemetry:[/bold green] OFF{cave}"
            )
        except Exception:
            print(f"🪿 Goose Config: provider=openai | model={self.config.model} | endpoint={self.config.vllm_host}/v1 | telemetry=OFF")

        try:
            return subprocess.call(cmd, env=env)
        except Exception as e:
            print(f"❌ Failed to run Goose: {e}")
            return 1
        finally:
            # Clean up the per-session temporary config
            try:
                temp_goose_config.unlink(missing_ok=True)
            except Exception:
                pass
