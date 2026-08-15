"""
Codex Session Runner for Dreamference.

This module provides the CodexRunner class which checks local vLLM endpoint health,
provisions Codex CLI, and launches interactive or automated Codex sessions.
"""

import json
import os
import subprocess
from typing import Optional, List

from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager
from dreamference.runner.codex_installer import CodexInstaller
from dreamference.hardware import resolve_model_hf_repo, get_model_launch_overrides

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
        
        # Codex will not talk to a model it has no catalog entry for, and the entry has to match
        # this schema exactly — it is parsed by serde with named structs, so a field of the wrong
        # shape is a hard startup failure rather than an ignored key. The shape below was
        # established against codex-cli 0.147.0 by feeding it candidates until it stopped
        # complaining; the non-obvious parts are:
        #
        #   * supported_reasoning_levels is a list of {effort, description} structs, not strings.
        #   * visibility is one of list|hide|none — not "public".
        #   * truncation_policy is a {mode, limit} struct, where mode is bytes|tokens.
        #   * base_instructions is required in practice: without it (or
        #     model_messages.instructions_template) Codex rejects the model after the JSON parses.
        #
        # Rewritten on every launch rather than written once. It used to be created only when
        # absent, so changing the served model left a catalog still advertising the previous one —
        # after the default moved to the DFlash entry, Codex was being handed a stale NVFP4 id and
        # a 32k context window for a model serving 131k.
        catalog_path = os.path.join(codex_config_dir, "model_catalog.json")
        context_window = int(
            get_model_launch_overrides(self.config.model).get("max_model_len", 32768)
        )
        catalog = {
            "models": [
                {
                    "id": hf_model,
                    "slug": hf_model,
                    "display_name": hf_model,
                    "max_context_window": context_window,
                    # This model runs with thinking disabled, so it advertises exactly one level.
                    "supported_reasoning_levels": [
                        {"effort": "none", "description": "No reasoning"}
                    ],
                    "default_reasoning_level": "none",
                    "shell_type": "default",
                    "visibility": "list",
                    "auto_compact_token_limit": context_window,
                    "supported_in_api": False,
                    "priority": 0,
                    "support_verbosity": False,
                    "supports_parallel_tool_calls": False,
                    "truncation_policy": {"mode": "tokens", "limit": context_window},
                    "experimental_supported_tools": [],
                    "base_instructions": "You are a helpful coding assistant.",
                }
            ]
        }
        with open(catalog_path, "w") as f:
            json.dump(catalog, f, indent=2)

        # Two pieces of config, and they cannot be written the same way. `model_catalog_json` is a
        # top-level key; a table header is not. TOML scopes every bare key to the most recent
        # `[section]` above it, so appending a top-level key to a file that already has sections
        # silently reparents it. That is exactly what happened here: appending
        # `model_catalog_json = "..."` after Codex's own `[tui.model_availability_nux]` table made
        # it a member of that table, whose values must be integers, and Codex then refused to start
        # with `invalid type: string ... expected u32`. A key that belongs at the top has to be
        # written at the top.
        catalog_key = f'model_catalog_json = "{catalog_path}"\n'
        provider_block = f"""
[model_providers.openai-custom]
name = "openai-custom"
base_url = "{api_base}"
"""

        if os.path.exists(codex_config_path):
            with open(codex_config_path, "r") as f:
                existing_config = f.read()

            lines = existing_config.splitlines(keepends=True)
            changed = False

            if "model_catalog_json" not in existing_config:
                # Before the first table header, which is the only region where a bare key is
                # unambiguously top-level.
                first_table = next(
                    (i for i, line in enumerate(lines) if line.lstrip().startswith("[")),
                    len(lines),
                )
                lines.insert(first_table, catalog_key)
                changed = True

            if "openai-custom" not in existing_config:
                # A table header carries its own scope, so appending one is safe.
                lines.append(provider_block)
                changed = True

            if changed:
                with open(codex_config_path, "w") as f:
                    f.writelines(lines)
        else:
            with open(codex_config_path, "w") as f:
                f.write(catalog_key + provider_block)


        env = os.environ.copy()
        # Codex CLI doesn't use OPENAI_API_KEY natively for custom providers, but we set it just in case
        env["OPENAI_API_KEY"] = "sk-gb10-local-token"
        
        cmd: List[str] = [
            codex_bin,
            "--oss",
            "--local-provider", "openai-custom",
            "--model", hf_model
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
