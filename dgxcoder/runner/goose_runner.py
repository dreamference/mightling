"""
Goose AI Agent Session Runner & vLLM Readiness Supervisor.

This module provides the GooseRunner class which auto-launches local vLLM servers when offline,
polls server health endpoints while streaming real-time startup logs, verifies Goose installation,
and executes interactive pair-programming sessions or autonomous coding tasks.
"""

import os
import subprocess
import sys
import time
from typing import List, Optional
from dgxcoder.config import DGXCoderConfig
from dgxcoder.vllm_server import VLLMServerManager, VLLMStartupMonitor
from dgxcoder.runner.goose_installer import GooseInstaller
from dgxcoder.runner.sandbox_manager import SandboxManager

class GooseRunner:
    """
    Supervisor class managing vLLM endpoint polling, Goose installation, container sandbox prefixes,
    and agent task execution.
    """

    def __init__(self, config: Optional[DGXCoderConfig] = None):
        """
        Initializes GooseRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DGXCoderConfig]): Configuration instance (defaults to DGXCoderConfig()).
        """
        self.config: DGXCoderConfig = config or DGXCoderConfig()
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

    def wait_for_vllm(self, poll_interval: float = 1.0, max_wait: Optional[float] = None, auto_launch: bool = True) -> bool:
        """
        Waits for local vLLM HTTP endpoint to become healthy. If offline and auto_launch=True,
        automatically starts local vLLM server and streams startup logs in real time.
        Uses a dedicated VLLMStartupMonitor thread to detect and log stalls instead of printing dots.

        Args:
            poll_interval (float): Polling loop interval in seconds.
            max_wait (Optional[float]): Maximum wait duration in seconds before timing out.
            auto_launch (bool): Automatically launch vLLM server if not running.

        Returns:
            bool: True if vLLM endpoint becomes online and healthy, False on failure/cancel.
        """
        try:
            if self.vllm_manager.check_health(timeout=0.5):
                return True
        except (KeyboardInterrupt, SystemExit):
            print("\n🛑 Cancelled waiting for vLLM server.")
            return False

        print(f"⚠️ Local vLLM server at {self.config.vllm_host} is not responding.")
        if auto_launch:
            print(f"🚀 Automatically launching local GB10 vLLM server ({self.config.model})...")
            try:
                self.vllm_manager.start_server(
                    model=self.config.model,
                    draft_model=self.config.draft_model,
                    num_speculative_tokens=self.config.num_speculative_tokens,
                    hf_token=self.config.hf_token,
                    enable_prefix_caching=self.config.enable_prefix_caching,
                    enable_chunked_prefill=self.config.enable_chunked_prefill,
                    num_scheduler_steps=self.config.num_scheduler_steps,
                    attention_backend=self.config.attention_backend,
                    kv_cache_dtype=self.config.kv_cache_dtype,
                    background=True
                )
            except (KeyboardInterrupt, SystemExit):
                print("\n🛑 Cancelled launching vLLM server.")
                return False
            except Exception as e:
                print(f"⚠️ Failed to auto-launch vLLM server: {e}")
                print(f"💡 You can manually start vLLM via: `dgxcoder serve --model {self.config.model}`")
        else:
            print(f"💡 Start vLLM in another terminal via: `dgxcoder serve --model {self.config.model}`")

        print("⏳ Waiting for local vLLM endpoint to become online... (Press Ctrl+C to cancel)\n")

        monitor = VLLMStartupMonitor(warn_timeout_sec=30.0, stuck_threshold_sec=90.0)
        monitor.start()
        start_time = time.time()
        try:
            while True:
                # Retrieve and print newly accumulated startup logs from vLLM stdout
                new_logs = self.vllm_manager.get_new_logs()
                if new_logs:
                    monitor.notify_log_received()
                    for log_line in new_logs:
                        print(f"  [vLLM] {log_line}")

                if self.vllm_manager.check_health(timeout=0.5):
                    monitor.stop()
                    print("\n✅ vLLM endpoint is online and responding!")
                    return True

                if self.vllm_manager.process and self.vllm_manager.process.poll() is not None:
                    monitor.stop()
                    exit_code = self.vllm_manager.process.poll()
                    print(f"\n❌ vLLM process terminated unexpectedly (exit code {exit_code}).")
                    for log_line in self.vllm_manager.get_new_logs():
                        print(f"  [vLLM] {log_line}")
                    return False

                if max_wait and (time.time() - start_time) >= max_wait:
                    monitor.stop()
                    print("\n❌ Timed out waiting for vLLM server.")
                    return False

                time.sleep(poll_interval)
        except (KeyboardInterrupt, SystemExit):
            monitor.stop()
            print("\n🛑 Cancelled waiting for vLLM server.")
            return False

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

        self.config.ensure_goose_config()

        valid, msg = self.config.validate_model()
        if not valid:
            print(f"⚠️ Warning: {msg}")

        # Auto-launch or wait for local vLLM server
        if not self.vllm_manager.check_health():
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

        cmd.append(goose_bin)

        if prompt:
            cmd.extend(["run", "--text", prompt])
        else:
            cmd.append("session")

        if debug:
            cmd.append("--debug")

        print(f"🚀 Launching Goose AI Agent on GB10 local endpoint ({self.config.model})...")
        try:
            return subprocess.call(cmd, env=env)
        except Exception as e:
            print(f"❌ Failed to run Goose: {e}")
            return 1
