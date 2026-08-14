"""
Codex Session Runner for Dreamference.

This module provides the CodexRunner class which checks local vLLM endpoint health,
provisions Codex CLI, and launches interactive or automated Codex sessions.
"""

import os
import subprocess
from typing import Optional, List

from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager
from dreamference.runner.codex_installer import CodexInstaller
from dreamference.hardware import resolve_model_hf_repo

class CodexRunner:
    """
    Runner class orchestrating Codex sessions connected to local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes CodexRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Ensures vLLM server is online, provisions Codex CLI if missing, and launches Codex task/session.

        Args:
            prompt (Optional[str]): Optional task prompt for non-interactive execution.
            debug (bool): Enable verbose debug output.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure local vLLM endpoint is online
        if not self.vllm_manager.check_health():
            from dreamference.runner.goose_runner import GooseRunner
            goose_runner = GooseRunner(config=self.config)
            if not goose_runner.wait_for_vllm():
                return 1

        # Step 2: Ensure Codex CLI is installed
        if not CodexInstaller.is_installed():
            CodexInstaller.install_if_missing()

        codex_bin = CodexInstaller.get_codex_executable()
        if not codex_bin:
            print("❌ Codex CLI (`codex`) is not installed or available in PATH.")
            print("💡 Install Codex via: `npm install -g @openai/codex`")
            return 1

        hf_model = resolve_model_hf_repo(self.config.model)
        api_base = f"{self.config.vllm_host.rstrip('/')}/v1"

        # Step 3: Configure Codex CLI to use local vLLM as an OSS provider
        codex_config_dir = os.path.expanduser("~/.codex")
        os.makedirs(codex_config_dir, exist_ok=True)
        codex_config_path = os.path.join(codex_config_dir, "config.toml")
        
        provider_config = f"""
oss = true
oss_provider = "openai-custom"

[model_providers.openai-custom]
name = "openai-custom"
base_url = "{api_base}"
"""
        
        if os.path.exists(codex_config_path):
            with open(codex_config_path, "r") as f:
                existing_config = f.read()
            if "openai-custom" not in existing_config:
                with open(codex_config_path, "a") as f:
                    f.write("\n" + provider_config)
        else:
            with open(codex_config_path, "w") as f:
                f.write(provider_config)

        env = os.environ.copy()
        # Codex CLI doesn't use OPENAI_API_KEY natively for custom providers, but we set it just in case
        env["OPENAI_API_KEY"] = "sk-gb10-local-token"
        
        cmd: List[str] = [
            codex_bin,
            "--oss",
            "--local-provider", "openai-custom",
            "--model", f"openai-custom/{hf_model}"
        ]

        if prompt:
            cmd.extend(["--message", prompt])

        if debug:
            cmd.append("--debug")

        print(f"🚀 Launching Codex Pair-Programmer on GB10 local endpoint ({self.config.model})...")
        try:
            return subprocess.call(cmd, env=env)
        except Exception as e:
            print(f"❌ Failed to run Codex: {e}")
            return 1
