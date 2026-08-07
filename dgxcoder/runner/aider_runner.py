"""
Aider Session Runner for DGXCoder.

This module provides the AiderRunner class which checks local vLLM endpoint health,
provisions Aider CLI, and launches interactive or automated Aider sessions.
"""

import os
import subprocess
from typing import Optional, List

from dgxcoder.config import DGXCoderConfig
from dgxcoder.vllm_server import VLLMServerManager
from dgxcoder.runner.aider_installer import AiderInstaller
from dgxcoder.hardware import resolve_model_hf_repo

class AiderRunner:
    """
    Runner class orchestrating Aider sessions connected to local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DGXCoderConfig] = None):
        """
        Initializes AiderRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DGXCoderConfig]): Configuration instance (defaults to DGXCoderConfig()).
        """
        self.config: DGXCoderConfig = config or DGXCoderConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Ensures vLLM server is online, provisions Aider CLI if missing, and launches Aider task/session.

        Args:
            prompt (Optional[str]): Optional task prompt for non-interactive execution (`--message <prompt>`).
            debug (bool): Enable verbose debug output (`--verbose`).

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure local vLLM endpoint is online
        if not self.vllm_manager.check_health():
            from dgxcoder.runner.goose_runner import GooseRunner
            goose_runner = GooseRunner(config=self.config)
            if not goose_runner.wait_for_vllm():
                return 1

        # Step 2: Ensure Aider CLI is installed
        if not AiderInstaller.is_installed():
            AiderInstaller.install_if_missing()

        aider_bin = AiderInstaller.get_aider_executable()
        if not aider_bin:
            print("❌ Aider CLI (`aider`) is not installed or available in PATH.")
            print("💡 Install Aider via: `pip install aider-chat`")
            return 1

        hf_model = resolve_model_hf_repo(self.config.model)
        api_base = f"{self.config.vllm_host.rstrip('/')}/v1/"

        cmd: List[str] = [
            aider_bin,
            "--openai-api-base", api_base,
            "--openai-api-key", "gb10-local-token",
            "--model", f"openai/{hf_model}",
            "--no-auto-commits" if self.config.sandbox != "none" else "--auto-commits",
        ]

        if self.config.draft_model:
            draft_hf = resolve_model_hf_repo(self.config.draft_model)
            cmd.extend(["--editor-model", f"openai/{hf_model}", "--architect", "--model", f"openai/{draft_hf}"])

        if prompt:
            cmd.extend(["--message", prompt])

        if debug:
            cmd.append("--verbose")

        print(f"🚀 Launching Aider Git Pair-Programmer on GB10 local endpoint ({self.config.model})...")
        try:
            return subprocess.call(cmd)
        except Exception as e:
            print(f"❌ Failed to run Aider: {e}")
            return 1
