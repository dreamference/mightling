"""The release marker and the after-merge trigger.

The marker (`~/.local/share/dreamference/messenger-live/blocking`) is written by every run with a
bridge failure and removed by the next green run; a 1.7.x patch release is not cut while it exists
(the coordinator's morning check reads it). A run that only needs the owner (exit 3) leaves it as
it is.

The after-merge trigger runs every quarter hour: when origin/main has commits touching the bridges
(`ling-rs/chat`, `ling-rs/signal`) since the last tested commit, it waits until the installed `ling`
was built from a checkout that contains them, then starts the suite's unit. The installed build is
known by its stamp (`~/.local/share/dreamference/mightling/build-key`), which hashes every file
under `ling-rs/`: when the checkout's own build key equals the stamp, the installed `ling` was built
from the checkout's HEAD, and that pair is remembered until the stamp changes.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

WATCHED: Final = ("ling-rs/chat", "ling-rs/signal")
SUITE_UNIT: Final = "mightling-messenger-live.service"


def state_dir() -> Path:
    """`~/.local/share/dreamference/messenger-live`: the marker and the trigger's state."""
    return Path.home() / ".local" / "share" / "dreamference" / "messenger-live"


def marker_path() -> Path:
    """The release marker: present while the last finished run had a bridge failure."""
    return state_dir() / "blocking"


def stamp_path() -> Path:
    """The installed `ling`'s build stamp, written by `ling-admin codex build`."""
    return Path.home() / ".local" / "share" / "dreamference" / "mightling" / "build-key"


def installed_stamp() -> str | None:
    """The installed `ling`'s build key, or None when nothing was built here."""
    try:
        return stamp_path().read_text().strip() or None
    except OSError:
        return None


def update_marker(code: int, report: Path, failed: list[str], build: dict[str, str]) -> str:
    """Writes the marker on a bridge failure (exit 1), removes it on a green run (exit 0).

    Args:
        code: The run's exit code.
        report: The run's Markdown report.
        failed: The failed bridge checks, as `messenger: check`.
        build: What was tested (the installed build).

    Returns:
        str: "set", "cleared" or "kept".
    """
    marker = marker_path()
    if code == 1:
        marker.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "The live messenger test failed: no 1.7.x patch release until a green run.",
            f"report: {report}",
            f"failed: {'; '.join(failed)}",
            f"at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        ]
        lines += [f"{key}: {value}" for key, value in sorted(build.items())]
        marker.write_text("\n".join(lines) + "\n")
        return "set"
    if code == 0 and marker.exists():
        marker.unlink()
        return "cleared"
    return "kept"


def decide(
    state: dict[str, Any],
    changed: list[str],
    stamp: str | None,
    is_ancestor: Callable[[str, str], bool],
) -> tuple[str, str]:
    """What the trigger does now.

    Args:
        state: The trigger's state (`last_tested`, `installed`).
        changed: Commits on origin/main since `last_tested` that touch the bridges, newest first.
        stamp: The installed build's key.
        is_ancestor: `is_ancestor(a, b)` is true when commit `a` is in commit `b`'s history.

    Returns:
        tuple[str, str]: An action ("init", "idle", "wait" or "run") and why.
    """
    if not state.get("last_tested"):
        return "init", "first check: origin/main is the starting point"
    if not changed:
        return "idle", "no change to the bridges since the last tested commit"
    installed = state.get("installed") or {}
    if not stamp or installed.get("key") != stamp:
        return "wait", "the installed ling was not built from a commit of this checkout yet"
    if not is_ancestor(changed[0], installed["commit"]):
        return "wait", f"the installed ling ({installed['commit'][:12]}) predates {changed[0][:12]}"
    return "run", f"{len(changed)} bridge commit(s) up to {changed[0][:12]} are installed"


class AfterMerge:
    """The quarter-hourly check against one checkout (the main one)."""

    def __init__(self, repo: Path) -> None:
        """Remembers the checkout and where the state is kept."""
        self.repo = repo
        self.state_file = state_dir() / "after-merge.json"

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Runs git in the checkout."""
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=300,
        )

    def is_ancestor(self, older: str, newer: str) -> bool:
        """True when `older` is in `newer`'s history."""
        return self.git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def checkout_key(self) -> str | None:
        """The checkout's own build key, computed by its package (the builder's `build_key`)."""
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from dreamference.runner.codex_branded_builder import CodexBrandedBuilder as B; "
            "print(B.build_key() or '')"
        )
        python = self.repo / ".venv" / "bin" / "python"
        done = subprocess.run(
            [str(python), "-I", "-c", code, str(self.repo)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
            cwd=self.repo,
        )
        if done.returncode != 0:
            return None
        return done.stdout.strip() or None

    def load(self) -> dict[str, Any]:
        """The saved state, or an empty one."""
        try:
            state: dict[str, Any] = json.loads(self.state_file.read_text())
            return state
        except (OSError, ValueError):
            return {}

    def save(self, state: dict[str, Any]) -> None:
        """Saves the state."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=1) + "\n")

    def check(self, start_suite: Callable[[], int]) -> int:
        """One check: fetch, note the installed build, and run the suite when it is due.

        Args:
            start_suite: Runs the suite (its unit) and returns its exit status.

        Returns:
            int: 0, or the suite's status when it ran.
        """
        if self.git("fetch", "--quiet", "origin", "main", check=False).returncode != 0:
            print("⚠️ git fetch failed; trying again at the next check")
            return 0
        target = self.git("rev-parse", "origin/main").stdout.strip()
        state = self.load()
        stamp = installed_stamp()
        if stamp and stamp == self.checkout_key():
            head = self.git("rev-parse", "HEAD").stdout.strip()
            state["installed"] = {"key": stamp, "commit": head}
        last = state.get("last_tested")
        changed = (
            self.git("log", "--format=%H", f"{last}..{target}", "--", *WATCHED).stdout.split()
            if last
            else []
        )
        action, why = decide(state, changed, stamp, self.is_ancestor)
        print(f"after-merge: {action}: {why}")
        if action in ("init", "idle"):
            state["last_tested"] = target
        elif action == "run":
            status = start_suite()
            state["last_tested"] = target
            state["last_run"] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "commits": changed,
                "status": status,
            }
        state["checked"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.save(state)
        return 0 if action != "run" else state["last_run"]["status"]
