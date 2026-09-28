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

# Appended to Codex's own system prompt, because web access has to travel with the session rather
# than with the directory. The equivalent text lives in this repo's AGENTS.md, but AGENTS.md is
# workspace-scoped: run `puffin-admin chat` anywhere else and the instruction is gone. The repo scripts
# these subcommands replaced had the same problem one level down — a relative path that only
# resolved here. `puffin-admin` is on PATH wherever the venv is, so the commands work from any workspace
# and the instruction naming them has to travel with the session too.
#
# Not an MCP tool: Codex exposes MCP tools only inside its `exec` JS runtime as
# `tools.mcp__server__tool(...)`, and this model does not reliably wrap calls that way. The shell it
# always uses correctly.
WEB_ACCESS_INSTRUCTIONS: str = """

# Web access

You have web access through two shell commands, run like any other command:

    puffin-admin search "your query here"        # search; -n N for more results (default 5)
    puffin-admin fetch "https://example.com"     # fetch a page as readable text

Use them whenever the answer depends on something you cannot know from training or from the files
in front of you: today's weather or tides, current events, release versions, live documentation,
anything dated. Search first, then `puffin-admin fetch` a promising URL when the snippets are not enough.

Do not say you cannot browse the web. You can, through these commands.

Do not use `curl` or `wget` for this. They are frequently blocked by the sandbox and return nothing,
which looks like the site being down rather than the command being unavailable.

There is no web search *tool* — do not look for one. Search is a shell command, shown above.

Queries go to a SearXNG instance on this machine, which contacts upstream engines on your behalf:
no API key, no account, and no query addressed to a search company. If it reports the instance is
unreachable, the error names the command that restarts it.
"""


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

        # Step 2: Ensure the branded Codex is built from the current submodule and patches
        if not CodexInstaller.is_installed():
            CodexInstaller.install_if_missing()

        codex_bin = CodexInstaller.get_codex_executable()
        if not codex_bin:
            print("❌ Puffin Codex (`puffin-codex`) is not built.")
            print("💡 Build it with: `puffin-admin codex build`")
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
                    # Declares Code Mode support. Without it Codex warns "model ... does
                    # not advertise Code Mode support" and withholds the `exec` tool, which
                    # is the only surface MCP tools are reachable through — they appear
                    # inside its JS runtime as tools.mcp__<server>__<tool>(args).
                    # ToolExposureSurface accepts "code_mode" or "direct"; "direct" is the
                    # one that leaves MCP unreachable.
                    "tool_mode": "code_mode",
                    # Codex's own prompt, read out of the installed binary. The one-line
                    # placeholder that used to sit here was the whole system prompt, which left
                    # the model with no description of its tools at all.
                    # Appended, not substituted: the web section has to survive whichever prompt
                    # the installed Codex ships, and it is the only part of the prompt that
                    # describes capabilities this harness adds rather than ones Codex provides.
                    "base_instructions": (
                        (
                            self.extract_codex_base_instructions(codex_bin)
                            or "You are a helpful coding assistant."
                        )
                        + WEB_ACCESS_INSTRUCTIONS
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
        # The update check compares this build against openai/codex releases and offers to install
        # theirs; for the branded build compiled from the submodule that offer would replace Puffin
        # with upstream Codex. Another top-level key, so it goes in with the catalog key.
        update_check_key = "check_for_update_on_startup = false\n"
        catalog_key = (
            f'model_catalog_json = "{catalog_path}"\n'
            "suppress_unstable_features_warning = true\n"
            + update_check_key
        )

        # No MCP servers are registered for Codex, deliberately.
        #
        # This project's own MCP server exists for JetBrains/VS Code, and its IDE tools read a
        # `global_ide_state` that lives inside the server process. Codex spawns its own copy,
        # so nothing ever populates that state: ide_get_active_editor returns "No active
        # file", ide_get_open_files returns [], ide_get_diagnostics returns []. ide_open_file
        # and ide_apply_diff are worse — they report success while mutating private memory no
        # editor reads. workspace_search_code does work, but Codex already has rg and
        # ast-grep through the shell and uses them reliably.
        #
        # The web tools were the other reason to register it, and they hit the same wall as
        # searxng did: Codex exposes MCP tools only inside its `exec` JS runtime, and this
        # model does not wrap calls that way. Search is reached through the `puffin-admin search` and
        # `puffin-admin fetch` subcommands, described in WEB_ACCESS_INSTRUCTIONS above.
        #
        # `puffin-admin mcp` still runs for IDE clients — it is just not wired into Codex.

        # SearXNG is deliberately NOT registered as an MCP server. It was, via the
        # mcp-searxng wrapper, and the wiring worked: the server initialised and its four
        # tools reached the model. What did not work is the model calling them — Codex
        # exposes MCP tools only inside its `exec` JS runtime as
        # tools.mcp__searxng__searxng_web_search(...), and this model calls the namespace
        # directly, gets `unsupported call`, and gives up. Search is reached through the
        # `puffin-admin search` and `puffin-admin fetch` subcommands instead, which WEB_ACCESS_INSTRUCTIONS
        # puts in the prompt and the model uses correctly. The SearXNG container is still
        # required — only the MCP wrapper is gone.

        # Code mode is what puts MCP tools in reach of this model at all. Without it Codex offers
        # exec_command/write_stdin plus namespace *descriptions*, and the model tries to call the
        # namespaces directly -- every spelling of which the router rejects as `unsupported call`.
        # With it, Codex adds an `exec` tool whose JS runtime exposes each MCP tool as
        # tools.mcp__<server>__<tool>(args), which is the mechanism an OpenAI-backed session was
        # observed using successfully.
        # The suppression is a top-level key, so it is written with the catalog key rather than
        # appended after a table -- same TOML scoping rule as model_catalog_json. code_mode is
        # flagged under-development by Codex; the warning is expected, not a symptom.
        # Without this the workspace-write sandbox blocks all network from shell commands,
        # including DNS -- the search helpers in AGENTS.md then fail with "Temporary failure in
        # name resolution" and the model concludes it has no web access. The sandbox still governs
        # what the agent may write; this only restores outbound network.
        sandbox_block = """
[sandbox_workspace_write]
network_access = true
"""

        features_block = """
[features]
code_mode = true
enable_mcp_apps = false
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
            # Before the first table header, which is the only region where a bare key is
            # unambiguously top-level.
            first_table = next(
                (i for i, line in enumerate(lines) if line.lstrip().startswith("[")),
                len(lines),
            )

            if "model_catalog_json" not in existing_config:
                lines.insert(first_table, catalog_key)
                changed = True
            elif "check_for_update_on_startup" not in existing_config:
                # A config written before the branded build already has the catalog key.
                lines.insert(first_table, update_check_key)
                changed = True

            if "openai-custom" not in existing_config:
                # A table header carries its own scope, so appending one is safe.
                lines.append(provider_block)
                changed = True

            if "code_mode" not in existing_config:
                lines.append(features_block)
                changed = True

            if "sandbox_workspace_write" not in existing_config:
                lines.append(sandbox_block)
                changed = True

            if changed:
                with open(codex_config_path, "w") as f:
                    f.writelines(lines)
        else:
            with open(codex_config_path, "w") as f:
                f.write(catalog_key + provider_block + features_block + sandbox_block)


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
            # Codex has no --debug flag; `debug` is a subcommand (`codex debug models`), so
            # appending it here made the launch fail outright. Verbosity is controlled by RUST_LOG,
            # and because the TUI owns stdout the output lands in a file rather than the terminal.
            #
            # codex_mcp at trace is the useful part: it is what reports which MCP servers connected,
            # which were cancelled, and the tool catalog it built from them.
            env["RUST_LOG"] = os.getenv(
                "RUST_LOG", "codex_mcp=trace,codex_core=debug,codex_app_server=debug,info"
            )
            # Where the output lands depends on which front end runs. `codex exec` writes to
            # stderr; the TUI owns the terminal, so it writes into ~/.codex/logs_2.sqlite instead
            # of a file — there is no codex-tui.log to tail.
            db_path = os.path.join(codex_config_dir, "logs_2.sqlite")
            print(f"🐞 Debug logging on (RUST_LOG={env['RUST_LOG']})")
            print(f"   TUI logs go to: {db_path}")
            print(f"   Read MCP lifecycle with: puffin-admin logs mcp")

        print(f"🚀 Launching Puffin on GB10 local endpoint ({self.config.model})...")
        try:
            return subprocess.call(cmd, env=env)
        except Exception as e:
            print(f"❌ Failed to run Codex: {e}")
            return 1
