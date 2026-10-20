"""The compatibility mode's session for `ling-admin audit egress --compat claude`.

This module provides the CompatPrintSession class: the print-mode session the audit traces,
`ling --compat claude -p "<prompt>" --output-format json`, run in a throwaway repository that holds
Claude Code's files, so every path of the mode that reads them runs inside the trace
(specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §8.2). It also says whether the user's new sessions
start in the mode, which is when `ling-admin codex build` traces it after a build.
"""

import json
import os
import signal
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Final

# The launcher's variable for the compatibility mode, its key in the user's own config file, and
# the one mode there is (ling-rs/src/compat.rs).
COMPAT_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_COMPAT"
COMPAT_KEY: Final[str] = "mightling_compat"
CLAUDE_MODE: Final[str] = "claude"

# What a print session's last line must be to count as an answer.
RESULT_TYPE: Final[str] = "result"

# Claude Code's files in the session's repository: memory, a settings file with one hook and one
# permission rule, a custom command and a subagent.
FIXTURE: Final[dict[str, str]] = {
    "CLAUDE.md": "# Audit repository\n\nAnswer in one word.\n",
    ".claude/settings.json": json.dumps(
        {
            "permissions": {"deny": ["Read(./secret.txt)"]},
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]},
                ],
            },
        },
        indent=2,
    )
    + "\n",
    ".claude/commands/hello.md": "---\ndescription: Say hello\n---\nSay hello to $ARGUMENTS.\n",
    ".claude/agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews a diff\n---\nReview the diff.\n",
}


class CompatPrintSession:
    """The print-mode session of the compatibility mode, as the egress audit runs it."""

    @classmethod
    def write_fixture(cls, repo: Path) -> None:
        """Writes Claude Code's files (FIXTURE) into the session's repository.

        Args:
            repo (Path): The throwaway repository.
        """
        for relative, text in FIXTURE.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    @classmethod
    def command(cls, strace: list[str], prompt: str) -> list[str]:
        """The traced command line: the session after strace and the `ling` it runs.

        Args:
            strace (list[str]): strace and its options, ending with the `ling` executable.
            prompt (str): The prompt to send.

        Returns:
            list[str]: The command line.
        """
        return [*strace, "--compat", CLAUDE_MODE, "-p", prompt, "--output-format", "json"]

    @classmethod
    def run(
        cls, command: list[str], repo: Path, env: dict[str, str], out: Path, timeout_s: float
    ) -> bool:
        """Runs the session with its output in a file and says whether it answered.

        The session gets its own process group, so one that never answers is stopped with
        everything it started.

        Args:
            command (list[str]): The traced command line (command).
            repo (Path): The repository it runs in, which gets the fixture first.
            env (dict[str, str]): Its environment.
            out (Path): Where its standard output goes.
            timeout_s (float): How long it may take.

        Returns:
            bool: True when it ended with a successful result carrying an answer (replied).
        """
        cls.write_fixture(repo)
        with out.open("w", encoding="utf-8") as handle:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=repo,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                return False
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                cls.stop(process)
        return cls.replied(out)

    @classmethod
    def stop(cls, process: subprocess.Popen[bytes]) -> None:
        """Stops a session that did not end in time, with its process group.

        Args:
            process (subprocess.Popen[bytes]): The session.
        """
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
                process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue
            return

    @classmethod
    def replied(cls, out: Path) -> bool:
        """Whether a print session's output ends with a successful result that has an answer.

        Args:
            out (Path): The file its standard output went to.

        Returns:
            bool: True for a `result` message that is not an error and carries a non-empty `result`.
        """
        try:
            lines = [
                line
                for line in out.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
            result = json.loads(lines[-1]) if lines else {}
        except (OSError, ValueError):
            return False
        return (
            isinstance(result, dict)
            and result.get("type") == RESULT_TYPE
            and result.get("is_error") is False
            and bool(str(result.get("result", "")).strip())
        )

    @classmethod
    def on_by_config(cls, environ: dict[str, str] | None = None, home: Path | None = None) -> bool:
        """Whether the user's new sessions start in the mode, as the launcher decides it.

        The variable, then `mightling_compat` in the user's own
        `~/.config/dreamference/config.toml`; a repository's file never counts.

        Args:
            environ (dict[str, str] | None): The environment; the process's by default.
            home (Path | None): The home folder; the user's by default.

        Returns:
            bool: True when new sessions start in `claude` mode.
        """
        environ = dict(os.environ) if environ is None else environ
        value = environ.get(COMPAT_ENV, "").strip().lower()
        if value:
            return value == CLAUDE_MODE
        path = (home or Path.home()) / ".config" / "dreamference" / "config.toml"
        try:
            setting = tomllib.loads(path.read_text(encoding="utf-8")).get(COMPAT_KEY)
        except (OSError, ValueError):
            return False
        return isinstance(setting, str) and setting.strip().lower() == CLAUDE_MODE

    @classmethod
    def after_build(cls, trace: Callable[[], int]) -> list[int]:
        """The audit's code for this session after a build, when the user's sessions use the mode.

        Args:
            trace (Callable[[], int]): Runs the audit of this session and returns its code.

        Returns:
            list[int]: The one code, or nothing when the mode is off.
        """
        return [trace()] if cls.on_by_config() else []
