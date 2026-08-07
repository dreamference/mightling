import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional
from dgxcoder.config import DGXCoderConfig
from dgxcoder.vllm_server import VLLMServerManager

class GooseRunner:
    """Orchestrates Goose AI agent sessions on NVIDIA GB10 hardware."""

    def __init__(self, config: Optional[DGXCoderConfig] = None):
        self.config = config or DGXCoderConfig()
        self.vllm_manager = VLLMServerManager(host=self.config.vllm_host)

    def get_goose_executable(self) -> Optional[str]:
        """Returns path or command name for goose CLI if installed."""
        which_goose = shutil.which("goose")
        if which_goose:
            return which_goose

        candidate_paths = [
            os.path.expanduser("~/.local/bin/goose"),
            os.path.expanduser("~/.goose/bin/goose"),
            os.path.join(sys.prefix, "bin", "goose"),
            os.path.join(sys.prefix, "bin", "goose-ai"),
        ]
        for path in candidate_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
        return None

    def is_goose_installed(self) -> bool:
        """Check if goose CLI binary is installed and accessible."""
        return self.get_goose_executable() is not None

    def install_goose(self) -> bool:
        """Automatically installs official Goose CLI from AAIF repository if missing."""
        if self.is_goose_installed():
            return True

        print("📦 Goose CLI (`goose`) is not installed. Initiating automatic installation...")
        try:
            # Install official AAIF Goose binary
            res = subprocess.run(
                "curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes || true",
                shell=True,
                check=False
            )
            if self.is_goose_installed():
                print("✅ Official Goose CLI (1.45+) installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Installation failed: {e}")

        return self.is_goose_installed()

    def wait_for_vllm(self, poll_interval: float = 1.0, max_wait: Optional[float] = None, auto_launch: bool = True) -> bool:
        """Waits for local vLLM server to start responding, auto-launching if not active."""
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

        start_time = time.time()
        try:
            while True:
                # Stream any new logs from vLLM server
                new_logs = self.vllm_manager.get_new_logs()
                for log_line in new_logs:
                    print(f"  [vLLM] {log_line}")

                if self.vllm_manager.check_health(timeout=0.5):
                    print("\n✅ vLLM endpoint is online and responding!")
                    return True

                # Check if vLLM process crashed
                if self.vllm_manager.process and self.vllm_manager.process.poll() is not None:
                    exit_code = self.vllm_manager.process.poll()
                    print(f"\n❌ vLLM process terminated unexpectedly (exit code {exit_code}).")
                    for log_line in self.vllm_manager.get_new_logs():
                        print(f"  [vLLM] {log_line}")
                    return False

                if max_wait and (time.time() - start_time) >= max_wait:
                    print("\n❌ Timed out waiting for vLLM server.")
                    return False

                # Sleep in 0.1s slices to react instantly to Ctrl+C (KeyboardInterrupt)
                for _ in range(int(poll_interval * 10)):
                    time.sleep(0.1)

                if not new_logs:
                    sys.stdout.write(".")
                    sys.stdout.flush()
        except (KeyboardInterrupt, SystemExit):
            print("\n🛑 Cancelled waiting for vLLM server.")
            return False

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """Launches an interactive or non-interactive Goose agent session connected to local GB10 vLLM endpoint."""
        env = os.environ.copy()
        env.update(self.config.get_env_vars())
        
        # Ensure ~/.local/bin is in PATH for env
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        # Ensure ~/.config/goose/config.yaml is prepared
        self.config.ensure_goose_config()

        # Check model compatibility
        valid, msg = self.config.validate_model()
        if not valid:
            print(f"⚠️ Warning: {msg}")

        # Check vLLM server status and wait until responsive
        if not self.vllm_manager.check_health():
            if not self.wait_for_vllm():
                return 1

        # Auto-install Goose if missing
        if not self.is_goose_installed():
            self.install_goose()

        if not self.is_goose_installed():
            print("⚠️ Goose CLI (`goose`) could not be automatically installed.")
            print("💡 Install Goose manually via: `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash`")
            return 1

        goose_bin = self.get_goose_executable()
        cmd: List[str] = [goose_bin]
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
