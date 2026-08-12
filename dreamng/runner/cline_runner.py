"""
Cline VS Code Session Runner & Configurator for Dreamng.

This module provides the ClineRunner class which configures workspace `.clinerules`,
ensures local vLLM server readiness, provisions the Cline extension, and launches VS Code.
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from dreamng.config import DreamngConfig, CAVE_MODE_PROMPT
from dreamng.vllm_server import VLLMServerManager
from dreamng.runner.cline_installer import ClineInstaller
from dreamng.hardware import resolve_model_hf_repo

class ClineRunner:
    """
    Runner class orchestrating Cline VS Code agent sessions on local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DreamngConfig] = None):
        """
        Initializes ClineRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamngConfig]): Configuration instance (defaults to DreamngConfig()).
        """
        self.config: DreamngConfig = config or DreamngConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def ensure_clinerules(self) -> Path:
        """
        Creates or updates workspace `.clinerules` file to configure GB10 vLLM local backend rules.
        When cave_mode is enabled, appends the strict Cave Mode prompt.

        Returns:
            Path: Path to written `.clinerules` file.
        """
        rules_path = Path(os.getcwd()) / ".clinerules"
        hf_model = resolve_model_hf_repo(self.config.model)
        
        content = (
            "# Dreamng Local GB10 AI Agent Rules for Cline\n\n"
            "## Model & Endpoint Configuration\n"
            f"- API Provider: OpenAI Compatible\n"
            f"- Base URL: {self.config.vllm_host}/v1\n"
            f"- Model ID: {hf_model}\n"
            "- Target Hardware: NVIDIA GB10 Blackwell (128GB Unified Memory)\n\n"
            "## Autonomous Execution Guidelines\n"
            "- Always execute linter diagnostics and test suites after modifying code.\n"
            "- Leverage local vLLM prefix caching by reusing system prompt context.\n"
            "- Operate completely offline with zero data egress.\n"
        )
        if self.config.cave_mode:
            content += f"\n## Cave Mode\n{CAVE_MODE_PROMPT}\n"

        if not rules_path.exists():
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write(content)

        return rules_path

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Configures workspace, checks vLLM health, installs Cline extension if missing,
        and launches VS Code.

        Args:
            prompt (Optional[str]): Task prompt text.
            debug (bool): Enable debug output.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure vLLM local server is online
        if not self.vllm_manager.check_health():
            from dreamng.runner.goose_runner import GooseRunner
            goose_runner = GooseRunner(config=self.config)
            if not goose_runner.wait_for_vllm():
                return 1

        # Step 2: Ensure VS Code and Cline extension are installed
        if not ClineInstaller.is_vscode_installed():
            print("❌ VS Code CLI (`code` or `codium`) is not installed or not in PATH.")
            print("💡 Install VS Code or add `code` to PATH to launch Cline.")
            return 1

        ClineInstaller.install_cline_if_missing()

        # Step 3: Write .clinerules in workspace
        rules_file = self.ensure_clinerules()
        print(f"✅ Configured Cline rules at {rules_file}")

        # Step 4: Display connection details for user
        hf_model = resolve_model_hf_repo(self.config.model)
        print("\n[Cline Configuration Details]")
        print(f"  API Provider: OpenAI Compatible")
        print(f"  Base URL:     {self.config.vllm_host}/v1")
        print(f"  Model ID:     {hf_model}")
        print(f"  API Key:      gb10-local-token")
        if prompt:
            print(f"  Prompt:       {prompt}")

        # Step 5: Launch VS Code in workspace directory
        code_bin = ClineInstaller.get_vscode_executable()
        print(f"\n🚀 Launching VS Code with Cline extension for {self.config.model}...")
        try:
            return subprocess.call([code_bin, os.getcwd()])
        except Exception as e:
            print(f"❌ Failed to launch VS Code: {e}")
            return 1
