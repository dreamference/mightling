"""
Continue IDE Session Runner & Configurator for Dreamference.

This module provides the ContinueRunner class which writes `~/.continue/config.json` with
vLLM OpenAI endpoint settings (including tab autocomplete models), verifies readiness,
and launches VS Code.
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager
from dreamference.runner.continue_installer import ContinueInstaller
from dreamference.hardware import resolve_model_hf_repo

class ContinueRunner:
    """
    Runner class orchestrating Continue sessions connected to local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes ContinueRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def ensure_continue_config(self) -> Path:
        """
        Ensures `~/.continue/config.json` is updated with local vLLM OpenAI endpoint settings
        and tab completion configuration.

        Returns:
            Path: Path object pointing to `~/.continue/config.json`.
        """
        continue_dir = Path.home() / ".continue"
        continue_dir.mkdir(parents=True, exist_ok=True)
        config_path = continue_dir / "config.json"

        hf_model = resolve_model_hf_repo(self.config.model)
        # The endpoint serves exactly one model. The draft model is a speculative head vLLM uses
        # internally, not a model it serves, and the old 1.5B fallback was never served either, so
        # every autocomplete request named a model that did not exist.
        tab_model = hf_model

        config_data: Dict[str, Any] = {
            "models": [
                {
                    "title": f"Puffin local ({self.config.model})",
                    "provider": "openai",
                    "model": hf_model,
                    "apiBase": f"{self.config.vllm_host.rstrip('/')}/v1/",
                    "apiKey": "gb10-local-token"
                }
            ],
            "tabAutocompleteModel": {
                "title": "Puffin Tab Autocomplete",
                "provider": "openai",
                "model": tab_model,
                "apiBase": f"{self.config.vllm_host.rstrip('/')}/v1/",
                "apiKey": "gb10-local-token"
            }
        }

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    existing["models"] = config_data["models"]
                    existing["tabAutocompleteModel"] = config_data["tabAutocompleteModel"]
                    config_data = existing
            except Exception:
                pass

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        return config_path

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Configures Continue, checks vLLM health, installs extension, and opens VS Code.

        Args:
            prompt (Optional[str]): Optional prompt message.
            debug (bool): Enable debug logging.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure local vLLM endpoint is online
        if not self.vllm_manager.check_health():
            from dreamference.runner.vllm_readiness_waiter import VLLMReadinessWaiter
            if not VLLMReadinessWaiter(config=self.config).wait_for_vllm():
                return 1

        # Step 2: Ensure VS Code and Continue extension are installed
        if not ContinueInstaller.is_vscode_installed():
            print("❌ VS Code CLI (`code` or `codium`) is not installed or not in PATH.")
            print("💡 Install VS Code or add `code` to PATH to launch Continue.")
            return 1

        ContinueInstaller.install_continue_if_missing()

        # Step 3: Write ~/.continue/config.json
        cfg_path = self.ensure_continue_config()
        print(f"✅ Configured Continue IDE settings at {cfg_path}")

        code_bin = ContinueInstaller.get_vscode_executable()
        print(f"🚀 Launching VS Code with Continue extension ({self.config.model} + Tab Autocomplete)...")
        try:
            return subprocess.call([code_bin, os.getcwd()])
        except Exception as e:
            print(f"❌ Failed to launch VS Code: {e}")
            return 1
