"""
Refreshing a repository's code index before its night tasks start
(specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md §6.3).

A night task's `ling exec` starts `ling-code session`, which re-indexes while the task is
already working. Running `ling-code index --exact --wait` first means the task begins with a
fresh index, and it is the one moment the executing indexers (Rust, Java, .NET, in a trusted
repository) are worth their minutes: nobody is waiting. `ling-code` does its own admission
against the host-wide memory budget and runs every indexer in its sandbox, so nothing here
decides whether a run fits.
"""

import os
import signal
import subprocess
from pathlib import Path
from typing import Dict, Final, List, Optional

from dreamference.night_shift.night_shift_host import NIGHT_RUN_ENV
from dreamference.runner.codex_branded_builder import INSTALL_DIR

# The slice every index scope runs in (`ling-code-rs/src/index/host.rs`).
INDEX_SLICE: Final[str] = "mightling-index.slice"

KILL_GRACE_S: Final[int] = 20


class NightShiftIndex:
    """Runs `ling-code index` for the night run."""

    @classmethod
    def executable(cls) -> Optional[str]:
        """
        Locates the installed `ling-code`, in Mightling's own install directory only.

        Returns:
            Optional[str]: Absolute path to the binary, or None if it has not been built.
        """
        path = os.path.join(INSTALL_DIR, "bin", "ling-code")
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None

    @classmethod
    def refresh(cls, binary: str, repo: Path, timeout_s: float) -> str:
        """
        Re-indexes one repository in the foreground and says what happened.

        Args:
            binary: The `ling-code` executable.
            repo: The repository's main checkout.
            timeout_s: Seconds the run may take; past it the run is stopped.

        Returns:
            str: A one-line outcome for the morning report.
        """
        env = dict(os.environ)
        env[NIGHT_RUN_ENV] = "1"
        try:
            process = subprocess.Popen(
                [binary, "index", "--exact", "--wait"], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        except OSError as error:
            return f"not refreshed: {error}"
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            cls._stop(process)
            return f"stopped after {int(timeout_s // 60)} min ([night] index_timeout); the tasks start on the index as it was"
        if process.returncode != 0:
            last = (output.strip().splitlines() or ["no output"])[-1]
            return f"not refreshed (exit {process.returncode}): {last[:200]}"
        return cls.summarise(output)

    @classmethod
    def summarise(cls, output: str) -> str:
        """
        Condenses `ling-code index --wait` output: one `<unit>: <status>` line per run, and
        `skipped: <why>` lines.

        Args:
            output: What the command printed.

        Returns:
            str: Counts by status, with the runs that did not finish named.
        """
        counts: Dict[str, int] = {}
        problems: List[str] = []
        for line in output.splitlines():
            if line.startswith("skipped:") or ": " not in line:
                continue
            unit, status = line.rsplit(": ", 1) if line.count(": ") == 1 else line.split(": ", 1)
            kind = status.split(":")[0].strip()
            counts[kind] = counts.get(kind, 0) + 1
            if kind != "ok":
                problems.append(f"{unit.split('-', 3)[-1]} {status.strip()}")
        if not counts:
            return "nothing to index"
        summary = ", ".join(f"{count} {kind}" for kind, count in counts.items())
        return summary + (f" ({'; '.join(problems[:3])})" if problems else "")

    @classmethod
    def _stop(cls, process: subprocess.Popen) -> None:
        """Stops the command and the index scopes it started, which outlive their starter."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=KILL_GRACE_S)
                break
            except subprocess.TimeoutExpired:
                continue
        # The night run started with no index scope active (admission refuses otherwise), so
        # whatever is in the slice now is this run's.
        subprocess.run(["systemctl", "--user", "stop", INDEX_SLICE], capture_output=True, timeout=60)
