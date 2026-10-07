"""
Codex Session Runner for Dreamference.

This module provides the CodexRunner class, which `ling-admin run` uses when the configured
agent is Codex. It builds `ling` if needed and hands over to it.

It no longer sets up the session. Waiting for the model server, writing the model catalog and
`~/.mightling/config.toml`, the system prompt with its web-access section, and the local-model options
all happen inside `ling` itself, in the launcher crate `ling-rs/`. Keeping a Python copy of that
logic here would give the two entry points two setups to keep in step.
"""

import os
import subprocess
from typing import List, Optional, Sequence

from dreamference.config import DreamferenceConfig
from dreamference.runner.codex_installer import CodexInstaller


class CodexRunner:
    """
    Runner class that launches the Rust `ling` agent.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes CodexRunner with a configuration instance.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()

    def run_session(
        self,
        prompt: Optional[str] = None,
        debug: bool = False,
        agent_args: Optional[Sequence[str]] = None,
    ) -> int:
        """
        Builds `ling` if it is missing or stale, then runs it.

        Args:
            prompt (Optional[str]): Optional initial prompt, passed as Codex's positional PROMPT.
            debug (bool): Enable verbose debug logging.
            agent_args (Optional[Sequence[str]]): Codex command-line arguments forwarded verbatim.

        Returns:
            int: The agent's exit code.
        """
        if not CodexInstaller.install_if_missing():
            return 1
        ling = CodexInstaller.get_codex_executable()
        if not ling:
            print("❌ `ling` is not built.")
            print("💡 Build it with: `ling-admin codex build`")
            return 1

        cmd: List[str] = [ling, *(agent_args or [])]
        # Codex takes the initial prompt as a positional argument; it has no --message option.
        if prompt:
            cmd.append(prompt)

        env = os.environ.copy()
        # The launcher reads the vLLM URL from the environment first, so a host given to
        # ling-admin on the command line or in a non-default config file reaches it.
        env["DREAMFERENCE_VLLM_HOST"] = self.config.vllm_host
        # Same for the Gmail prompt opt-out, which the launcher reads the same way.
        env["DREAMFERENCE_MIGHTLING_GMAIL"] = "true" if self.config.mightling_gmail else "false"
        if debug:
            # Codex has no --debug flag; verbosity is RUST_LOG. The TUI owns the terminal, so its
            # log goes to logs_2.sqlite in ling's home folder rather than to a file.
            env["RUST_LOG"] = os.getenv(
                "RUST_LOG", "codex_mcp=trace,codex_core=debug,codex_app_server=debug,info"
            )
            print(f"🐞 Debug logging on (RUST_LOG={env['RUST_LOG']})")
            print(f"   TUI logs go to: {os.path.join(CodexInstaller.home_dir(), 'logs_2.sqlite')}")
            print("   Read MCP lifecycle with: ling-admin logs mcp")

        try:
            return subprocess.call(cmd, env=env)
        except OSError as error:
            print(f"❌ Failed to run ling: {error}")
            return 1
