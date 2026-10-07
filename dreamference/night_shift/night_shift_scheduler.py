"""
The systemd user timer that starts the night run (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5.1):
`mling-admin night enable|disable|status`.

A user service has neither `~/.local/bin` nor the virtualenv on its PATH, so the unit names
`mling-admin` by its absolute path and sets a PATH that reaches `mling-search`, `mling-fetch`
and Cargo for the agent's shell. Without lingering, a user timer stops at logout; `enable` says so.
"""

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Final, List

from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_settings import NightShiftSettings

UNIT: Final[str] = "mightling-night"

# The comment line the launcher reads to show the enabled window in `/night list`.
WINDOW_MARKER: Final[str] = "# Night Shift window: "


class NightShiftScheduler:
    """Installs, removes and reports the `mightling-night` timer."""

    # Seam: tests replace it so nothing in the suite talks to the real systemd.
    systemctl: Callable[[List[str]], subprocess.CompletedProcess] = staticmethod(
        lambda args: subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True))

    @classmethod
    def unit_dir(cls) -> Path:
        """
        Returns:
            Path: `~/.config/systemd/user`.
        """
        return Path(os.path.expanduser("~/.config/systemd/user"))

    @classmethod
    def admin_executable(cls) -> str:
        """
        Returns:
            str: The absolute path of this environment's `mling-admin`.
        """
        candidate = Path(sys.executable).parent / "mling-admin"
        return str(candidate) if candidate.exists() else (shutil.which("mling-admin") or str(candidate))

    @classmethod
    def render_units(cls, window: str, admin: str) -> tuple:
        """
        Renders the timer and service.

        Args:
            window: `HH:MM-HH:MM`.
            admin: The absolute path of `mling-admin`.

        Returns:
            tuple: (timer text, service text).
        """
        start, end = NightShiftSettings.parse_window(window)
        home = os.path.expanduser("~")
        path = ":".join([f"{home}/.local/bin", f"{home}/.cargo/bin", str(Path(admin).parent),
                         "/usr/local/bin", "/usr/bin", "/bin"])
        timer = (f"{WINDOW_MARKER}{window}\n"
                 "[Unit]\nDescription=Mightling Night Shift window\n\n"
                 f"[Timer]\nOnCalendar=*-*-* {start:%H:%M}:00\nPersistent=false\nUnit={UNIT}.service\n\n"
                 "[Install]\nWantedBy=timers.target\n")
        service = ("[Unit]\nDescription=Mightling Night Shift run\n\n"
                   "[Service]\nType=oneshot\n"
                   f"Environment=PATH={path}\n"
                   f"WorkingDirectory={home}\n"
                   f"ExecStart={admin} night run --until {end:%H:%M}\n")
        return timer, service

    @classmethod
    def enable(cls, window: str) -> bool:
        """
        Installs and starts the timer.

        Args:
            window: `HH:MM-HH:MM`.

        Returns:
            bool: True on success.
        """
        try:
            timer, service = cls.render_units(window, cls.admin_executable())
        except ValueError as error:
            print(f"❌ {error}")
            return False
        directory = cls.unit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{UNIT}.timer").write_text(timer)
        (directory / f"{UNIT}.service").write_text(service)
        for args in (["daemon-reload"], ["enable", "--now", f"{UNIT}.timer"]):
            result = cls.systemctl(args)
            if result.returncode != 0:
                print(f"❌ systemctl --user {' '.join(args)}: {result.stderr.strip()}")
                return False
        print(f"✅ Night Shift enabled: {window} every night.")
        if not cls.lingering():
            print("⚠️  Lingering is off for this user, so the timer only fires while you are logged in.\n"
                  f"   To run it after logout: sudo loginctl enable-linger {getpass.getuser()}")
        return True

    @classmethod
    def disable(cls) -> bool:
        """
        Stops and removes the timer and service.

        Returns:
            bool: True on success.
        """
        cls.systemctl(["disable", "--now", f"{UNIT}.timer"])
        for suffix in ("timer", "service"):
            path = cls.unit_dir() / f"{UNIT}.{suffix}"
            if path.exists():
                path.unlink()
        cls.systemctl(["daemon-reload"])
        print("✅ Night Shift disabled.")
        return True

    @classmethod
    def status(cls) -> str:
        """
        Describes the timer, the window and the queue of every repository.

        Returns:
            str: Lines to print.
        """
        timer_path = cls.unit_dir() / f"{UNIT}.timer"
        lines = []
        if timer_path.exists():
            window = next((line[len(WINDOW_MARKER):] for line in timer_path.read_text().splitlines()
                           if line.startswith(WINDOW_MARKER)), "?")
            next_run = cls.systemctl(["list-timers", f"{UNIT}.timer", "--no-legend"]).stdout.strip()
            lines.append(f"Timer: enabled, window {window}")
            if next_run:
                lines.append(f"Next: {next_run.split('  ')[0]}")
        else:
            lines.append("Timer: not enabled (mling-admin night enable --window 01:00-07:00)")
        if NightShiftQueue.runner_active():
            lines.append("A night run is in progress.")
        tasks = NightShiftQueue.tasks(NightShiftQueue.night_dir())
        by_repo = {}
        for task in tasks:
            counts = by_repo.setdefault(task.get("repo", "?"), {})
            counts[task.get("status", "?")] = counts.get(task.get("status", "?"), 0) + 1
        if not by_repo:
            lines.append("Queue: empty")
        for repo, counts in by_repo.items():
            lines.append(f"{repo}: " + ", ".join(f"{count} {status}" for status, count in sorted(counts.items())))
        return "\n".join(lines)

    @classmethod
    def lingering(cls) -> bool:
        """
        Returns:
            bool: True if systemd keeps this user's services running after logout.
        """
        try:
            result = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.stdout.strip() == "yes"
