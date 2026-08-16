"""
Codex Session Runner for Dreamference.

This module provides the CodexRunner class which checks local vLLM endpoint health,
provisions Codex CLI, and launches interactive or automated Codex sessions.
"""

import json
import os
import shutil
import subprocess
import sys
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

    @staticmethod
    def extract_codex_base_instructions(codex_bin: str) -> Optional[str]:
        """
        Recovers Codex's own system prompt from the installed binary.

        The model catalog must supply `base_instructions` (or
        `model_messages.instructions_template`) or Codex rejects the model outright, and what goes
        there is the entire system prompt — it is not a label. Writing a one-line placeholder there,
        as this runner first did, replaces several thousand words describing the harness, the
        available tools and how to call them. The visible result is a model that has no idea what
        it can do: it guesses tool names that do not exist, and falls back to shelling out to curl
        for work a tool was provided for.

        Codex ships those instructions inside its own binary as JSON-escaped strings, so they are
        read back out rather than copied into this repository. That keeps them matched to whatever
        Codex version is installed instead of pinned to whatever was current when this was written,
        and costs about 0.1s to scan a 222 MB binary.

        Args:
            codex_bin (str): Path to the codex executable.

        Returns:
            Optional[str]: The longest embedded instruction template, or None if none is found —
                in which case the caller should fall back rather than ship an empty prompt.
        """
        import re

        try:
            with open(codex_bin, "rb") as handle:
                blob = handle.read()
        except OSError:
            return None

        # Captured with escapes intact so the JSON decoder can undo them; a 500-char floor skips
        # the short format strings that share the key name.
        candidates = re.findall(
            rb'"instructions_template"\s*:\s*"((?:[^"\\]|\\.){500,})"', blob
        )
        decoded: List[str] = []
        for candidate in candidates:
            try:
                decoded.append(json.loads(b'"' + candidate + b'"'))
            except ValueError:
                continue
        if not decoded:
            return None
        # Longest wins: the shorter variants are trimmed prompts for narrower modes, and a missing
        # section costs more here than an irrelevant one.
        return max(decoded, key=len)

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
                    # Codex's own prompt, read out of the installed binary. The one-line
                    # placeholder that used to sit here was the whole system prompt, which left
                    # the model with no description of its tools at all.
                    "base_instructions": (
                        self.extract_codex_base_instructions(codex_bin)
                        or "You are a helpful coding assistant."
                    ),
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

        # Web access has to come through MCP, not Codex's own `--search`. That flag enables the
        # native Responses `web_search` tool, which the *provider* executes — against a local vLLM
        # there is nothing on the other end, and Codex reports `unsupported call: web_search` no
        # matter how it is configured. Setting supports_standalone_web_search on the provider and
        # the standalone_web_search/web_search_request feature flags does not change that; all
        # three were tried on codex-cli 0.147.0 and the call stayed unsupported.
        #
        # An MCP server is executed by Codex itself, so it works regardless of what the model
        # provider can do. This project already ships one, and it now carries web_search and
        # web_fetch alongside the IDE tools — see mcp_server/web_tools.py, which is also where the
        # departure from the air-gapped premise is argued.
        dream_bin = os.path.join(os.path.dirname(sys.executable), "dream")
        if not os.path.exists(dream_bin):
            dream_bin = shutil.which("dream") or dream_bin
        mcp_block = f"""
[mcp_servers.dreamference]
command = "{dream_bin}"
args = ["mcp"]
"""

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

            if "mcp_servers.dreamference" not in existing_config:
                lines.append(mcp_block)
                changed = True

            if changed:
                with open(codex_config_path, "w") as f:
                    f.writelines(lines)
        else:
            with open(codex_config_path, "w") as f:
                f.write(catalog_key + provider_block + mcp_block)


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
