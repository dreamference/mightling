"""The air-gap level as the one reader decides it (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §6.5).

Python does not read the level's files itself: it asks `ling airgapped --json`, whose resolver is
the crate `ling-rs/airgapped` every Rust component links, and takes `on` whenever it gets no
well-formed answer. The answers it accepts are pinned by the `answer` cases of
`ling-rs/airgapped/cases.json`, which the desktop app runs too.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Final

AIRGAPPED_OFF: Final[str] = "off"
AIRGAPPED_ON: Final[str] = "on"
# What the resolver answers in; no other value is a level.
AIRGAPPED_LEVELS: Final[tuple[str, ...]] = (AIRGAPPED_OFF, AIRGAPPED_ON)
# `ling airgapped` reads a few small files and opens no socket; longer than this is a fault.
RESOLVE_TIMEOUT_SECONDS: Final[float] = 5.0


class AirgappedResolver:
    """Asks the built `ling` for the level in force, and fails closed."""

    @classmethod
    def binary(cls) -> str:
        """The `ling` that answers: the built one, never one found on PATH.

        Returns:
            str: The executable's path, whether or not it exists.
        """
        # Imported here: the runner package imports this one.
        from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

        return CodexBrandedBuilder.executable_path()

    @classmethod
    def command(
        cls,
        binary: str,
        cwd: Path | None = None,
        thread: str | None = None,
        sealed_by: int | None = None,
    ) -> list[str]:
        """The command line that asks for the level.

        Args:
            binary: The `ling` to run.
            cwd: The repository whose `dreamference.toml` counts; the current folder if None.
            thread: A session's id, whose level file and seal count first.
            sealed_by: A process whose seal holds its session at `on` (an MCP server's parent).

        Returns:
            list[str]: The arguments, the binary first.
        """
        command = [binary, "airgapped", "--json"]
        if thread:
            command += ["--thread", thread]
        if cwd is not None:
            command += ["--cwd", str(cwd)]
        if sealed_by is not None:
            command += ["--sealed-by", str(sealed_by)]
        return command

    @classmethod
    def parse(cls, stdout: str | None, status: int | None) -> str:
        """The level an answer gives: `off` only from a well-formed answer that says so.

        Args:
            stdout: What the command printed, or None when it did not run.
            status: Its exit status, or None when it did not finish.

        Returns:
            str: `off` or `on`.
        """
        if status != 0 or stdout is None:
            return AIRGAPPED_ON
        try:
            answer = json.loads(stdout)
        except ValueError:
            return AIRGAPPED_ON
        if not isinstance(answer, dict) or answer.get("level") not in AIRGAPPED_LEVELS:
            return AIRGAPPED_ON
        return str(answer["level"])

    @classmethod
    def level(
        cls,
        cwd: Path | None = None,
        thread: str | None = None,
        sealed_by: int | None = None,
        environment: dict[str, str] | None = None,
        binary: str | None = None,
    ) -> str:
        """Resolves the level in force; `on` when `ling` cannot say.

        Args:
            cwd: The repository whose `dreamference.toml` counts; the current folder if None.
            thread: A session's id, whose level file and seal count first.
            sealed_by: A process whose seal holds its session at `on`.
            environment: The environment `ling` runs in (its `CODEX_HOME` names the session
                files); this process's if None.
            binary: The `ling` to ask; the built one if None.

        Returns:
            str: `off` or `on`.
        """
        command = cls.command(binary or cls.binary(), cwd=cwd, thread=thread, sealed_by=sealed_by)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=RESOLVE_TIMEOUT_SECONDS,
                env=environment if environment is not None else dict(os.environ),
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return AIRGAPPED_ON
        return cls.parse(result.stdout, result.returncode)
