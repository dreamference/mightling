"""
bubblewrap's sandbox, checked on every `puffin-admin` run.

Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`: only a program with an
AppArmor profile may create a user namespace with capabilities in it, and `/usr/bin/bwrap` has
none. Every sandbox Puffin starts goes through that `bwrap` -- `puffin`'s command sandbox (Codex
prefers the system's bubblewrap on PATH), Night Shift's tasks and their test runs, the code
indexers and jobs from other nodes -- so from a plain terminal, an SSH login or a systemd timer
all of them fail. On the machine this was written on they had only ever worked because every
shell was a child of the PyCharm snap, whose AppArmor label allows it (measured 2026-10-02).

The fix needs root once, so when the check fails `puffin-admin` asks: fix it now (sudo asks for
the password), or turn off what depends on it. The fix is the narrowest one: an AppArmor profile
for `/usr/bin/bwrap` alone, the same shape as the profiles Ubuntu itself ships for programs that
build sandboxes (`/etc/apparmor.d/chrome`, `linux-sandbox`): `flags=(unconfined)` plus `userns,`.
Setting the sysctl to 0 would also work, for every program on the machine, and is not offered.
Ubuntu's stricter `bwrap-userns-restrict` (apparmor-profiles) confines what runs inside the
sandbox as well; it was not tried with Codex's sandbox, which sets up a loopback interface in it.

The user's answer to "turn it off" is kept in `~/.config/dreamference/sandbox.json`, so the
question is not asked again; `puffin-admin host setup` applies the fix later and the file is then
removed. Nothing here runs from a command `puffin` itself starts: that command is inside a
sandbox already, and nobody is at its terminal to answer.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Final, List, Optional, Tuple

PROFILE_NAME: Final[str] = "puffin-bwrap"
PROFILE_PATH: Final[str] = f"/etc/apparmor.d/{PROFILE_NAME}"
USERNS_SYSCTL: Final[str] = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"

# Commands that are never asked about: `mcp` and `node serve-job` speak a protocol on stdio, and
# `host` reports and fixes the sandbox itself.
QUIET_COMMANDS: Final[Tuple[Tuple[str, Optional[str]], ...]] = (
    ("mcp", None), ("host", None), ("node", "serve-job"),
)

# What cannot work without the sandbox, refused while it is missing and the user has turned it
# off (and, with nobody at a terminal to ask, while it is missing at all): a night task is a
# sandboxed `puffin exec`, and its tests run under `puffin sandbox`.
NEEDS_SANDBOX: Final[Tuple[Tuple[str, str], ...]] = (("night", "enable"), ("night", "run"))


class SandboxPrerequisite:
    """Checks that bubblewrap can sandbox from an ordinary login, and asks for the fix when not."""

    @classmethod
    def gate(cls, command: Optional[str], subcommand: Optional[str]) -> bool:
        """
        Runs the check for one `puffin-admin` invocation.

        Args:
            command: The top-level command (`night`, `server`, …).
            subcommand: Its subcommand, or None.

        Returns:
            bool: False if the command must not run (it needs the sandbox, and the sandbox is
            missing); True otherwise.
        """
        if not command or cls._quiet(command, subcommand) or cls._inside_puffin():
            return True
        from dreamference.vllm_server.host_safety_setup import HostSafetySetup
        works = HostSafetySetup.sandbox_works()
        if works is not False:
            if works and cls.turned_off():
                cls._forget()
            return True
        needed = (command, subcommand) in NEEDS_SANDBOX
        if cls.turned_off():
            if needed:
                cls._refuse(command, subcommand, "you turned off what needs it")
            return not needed
        if not cls._interactive():
            print("⚠️  bubblewrap cannot create a sandbox from an ordinary login on this machine "
                  "(AppArmor); `puffin-admin host setup` fixes it.", file=sys.stderr)
            if needed:
                cls._refuse(command, subcommand, "nobody is at a terminal to fix it")
            return not needed
        choice = cls._ask()
        if choice == "fix":
            if cls.fix():
                return True
        elif choice == "off":
            cls.turn_off()
        if needed:
            cls._refuse(command, subcommand, "it is still missing")
            return False
        return True

    @classmethod
    def fix(cls) -> bool:
        """
        Installs the AppArmor profile through sudo, then tries bubblewrap again.

        Returns:
            bool: True if bubblewrap works afterwards.
        """
        from dreamference.vllm_server.host_safety_setup import HostSafetySetup
        commands = cls.fix_commands()
        if commands is None:
            print("❌ The sandbox is refused here for a reason other than Ubuntu's AppArmor "
                  "restriction, so there is no profile to install. `puffin-admin host check` "
                  "says what it found.")
            return False
        if shutil.which("sudo") is None:
            print("❌ sudo is not installed. As root, run:")
            for command in commands:
                print(f"     {' '.join(command)}")
            return False
        for command in commands:
            if not HostSafetySetup._run_as_root(command):
                print("❌ That command failed, so the sandbox is still missing. "
                      "`puffin-admin host setup` tries again.")
                return False
        if HostSafetySetup.sandbox_works() is False:
            print(f"❌ The profile is installed, but bubblewrap is still refused. "
                  f"`sudo aa-status | grep {PROFILE_NAME}` shows whether it loaded.")
            return False
        was_off = cls.turned_off()
        cls._forget()
        print("✅ bubblewrap can now sandbox from any login: puffin's commands, Night Shift and node jobs.")
        if was_off:
            print("💡 Night Shift was turned off for this; `puffin-admin night enable` puts the timer back.")
        return True

    @classmethod
    def turn_off(cls) -> None:
        """Records the choice and stops what cannot work without the sandbox: Night Shift's timer."""
        from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler, UNIT
        timer_was_on = (NightShiftScheduler.unit_dir() / f"{UNIT}.timer").exists()
        if timer_was_on:
            NightShiftScheduler.disable()
        path = cls.decision_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sandbox": "off", "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "night_timer_was_on": timer_was_on}, indent=2) + "\n")
        print("Turned off: Night Shift (`night enable` and `night run` are refused).\n"
              "   Still affected: commands `puffin` runs in its sandbox outside the PyCharm terminal fail,\n"
              "   and jobs sent from other nodes are refused here.\n"
              "💡 `puffin-admin host setup` installs the fix later; you will not be asked again until then.")

    @classmethod
    def fix_commands(cls) -> Optional[List[List[str]]]:
        """
        The root commands that let bubblewrap create its sandbox.

        Returns:
            Optional[List[List[str]]]: argv lists without `sudo`; None when the refusal is not
            Ubuntu's AppArmor restriction (the profile would not help).
        """
        if cls._userns_restricted() is not True:
            return None
        bwrap = os.path.realpath(shutil.which("bwrap") or "/usr/bin/bwrap")
        staged = cls.staged_profile(bwrap)
        return [
            ["install", "-m", "644", "-o", "root", "-g", "root", str(staged), PROFILE_PATH],
            ["apparmor_parser", "-r", PROFILE_PATH],
        ]

    @classmethod
    def profile_text(cls, bwrap: str) -> str:
        """
        Args:
            bwrap: The absolute path of the bubblewrap executable.

        Returns:
            str: The AppArmor profile that gives it user namespaces and confines nothing else.
        """
        return (
            "# Written by `puffin-admin host setup` (Puffin by Dreamference). It lets bubblewrap\n"
            "# create the user namespace its sandbox needs, which Ubuntu otherwise refuses to\n"
            "# programs without a profile (kernel.apparmor_restrict_unprivileged_userns=1).\n"
            "# Same shape as Ubuntu's own profiles for sandboxing programs (chrome, linux-sandbox).\n"
            "\n"
            "abi <abi/4.0>,\n"
            "include <tunables/global>\n"
            "\n"
            f"profile {PROFILE_NAME} {bwrap} flags=(unconfined) {{\n"
            "  userns,\n"
            "\n"
            "  # Site-specific additions and overrides. See local/README for details.\n"
            f"  include if exists <local/{PROFILE_NAME}>\n"
            "}\n"
        )

    @classmethod
    def staged_profile(cls, bwrap: str) -> Path:
        """
        Writes the profile where `sudo install` copies it from, so the printed command is short.

        Args:
            bwrap: The absolute path of the bubblewrap executable.

        Returns:
            Path: `~/.cache/dreamference/apparmor/puffin-bwrap`.
        """
        path = Path(os.path.expanduser("~/.cache/dreamference/apparmor")) / PROFILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cls.profile_text(bwrap))
        return path

    @classmethod
    def decision_path(cls) -> Path:
        """
        Returns:
            Path: `~/.config/dreamference/sandbox.json`, present while the user has turned off
            what needs the sandbox.
        """
        return Path(os.path.expanduser("~/.config/dreamference/sandbox.json"))

    @classmethod
    def turned_off(cls) -> bool:
        """
        Returns:
            bool: True if the user chose to do without the sandbox.
        """
        try:
            return json.loads(cls.decision_path().read_text()).get("sandbox") == "off"
        except (OSError, ValueError, AttributeError):
            return False

    # -- helpers ---------------------------------------------------------------------------------

    @classmethod
    def _ask(cls) -> str:
        print("\n⚠️  puffin's sandbox cannot start from an ordinary login on this machine.\n"
              "   Ubuntu lets only programs with an AppArmor profile create a user namespace\n"
              "   (kernel.apparmor_restrict_unprivileged_userns=1), and bubblewrap has none. Commands\n"
              "   puffin runs in its sandbox, Night Shift and jobs from other nodes need it; it works\n"
              "   in the PyCharm terminal only because that terminal carries the snap's AppArmor label.\n\n"
              "   1) Fix it now: install an AppArmor profile for bubblewrap alone (needs your password for sudo)\n"
              "   2) Turn off what needs it: Night Shift (`puffin-admin host setup` fixes it later)\n"
              "   3) Not now (asked again next time)\n")
        while True:
            try:
                answer = input("   Choose 1, 2 or 3 [3]: ").strip()
            except EOFError:
                answer = "3"
            if answer in ("", "3"):
                return "later"
            if answer == "1":
                return "fix"
            if answer == "2":
                return "off"

    @classmethod
    def _refuse(cls, command: str, subcommand: Optional[str], reason: str) -> None:
        print(f"❌ `puffin-admin {command} {subcommand}` needs bubblewrap's sandbox, which this machine "
              f"refuses from an ordinary login, and {reason}.\n"
              "   `puffin-admin host setup` fixes it (sudo asks for your password).", file=sys.stderr)

    @classmethod
    def _quiet(cls, command: str, subcommand: Optional[str]) -> bool:
        return any(command == quiet and (sub is None or subcommand == sub) for quiet, sub in QUIET_COMMANDS)

    @classmethod
    def _inside_puffin(cls) -> bool:
        # Codex exports these to every command it runs; such a command is sandboxed already and
        # its output goes to the model, not to a person who could type a password.
        return any(os.environ.get(name) for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX"))

    @classmethod
    def _interactive(cls) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    @classmethod
    def _userns_restricted(cls) -> Optional[bool]:
        try:
            with open(USERNS_SYSCTL) as handle:
                return handle.read().strip() == "1"
        except OSError:
            return None

    @classmethod
    def _forget(cls) -> None:
        try:
            cls.decision_path().unlink()
        except OSError:
            pass
