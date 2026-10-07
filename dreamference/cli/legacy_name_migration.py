"""
Puffin became Mightling (specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2): `mling-admin`'s half of the
one-time migration.

`mling` moves the binaries, the agent's home, the links and the user's configuration keys. What
only the Python side knows how to redo is moved here, the first time any `mling-admin` command runs
on a machine that still has it: the Night Shift timer, the Avahi service file, the pairing lines in
`authorized_keys`, the user-level configuration keys and the desktop app's data. Each step checks
before it acts, so on a machine with nothing old it does nothing and says nothing. The old names
are migrated, never kept working beside the new ones.
"""

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Final, List, Optional

LEGACY_KEY_PREFIX: Final[str] = "puffin_"
KEY_PREFIX: Final[str] = "mightling_"
LEGACY_NIGHT_UNIT: Final[str] = "puffin-night"
LEGACY_SERVICE_FILE: Final[str] = "puffin-node.service"
LEGACY_SERVICE_TYPE: Final[str] = "_puffin-node._tcp"
LEGACY_KEY_COMMENT: Final[str] = "puffin-node"
LEGACY_ADMIN: Final[str] = "puffin-admin"
LEGACY_DESKTOP_DATA: Final[str] = "~/.local/share/dev.dreamference.puffin"
DESKTOP_DATA: Final[str] = "~/.local/share/dev.dreamference.mightling"


class LegacyNameMigration:
    """Moves what a Puffin 1.4.x node left under the old names to the new ones, once."""

    @classmethod
    def run(cls) -> List[str]:
        """
        Runs every step and prints a notice when one did something.

        Returns:
            List[str]: What was done, one line per step; empty when there was nothing old.
        """
        done: List[str] = []
        for step in (cls.rewrite_user_config, cls.replace_night_units, cls.replace_service_file,
                     cls.rewrite_authorized_keys, cls.move_desktop_data):
            try:
                line = step()
            except OSError as error:
                line = f"could not finish {step.__name__}: {error}"
            if line:
                done.append(line)
        stale = cls.stale_project_config()
        if done or stale:
            print("🐦 Puffin is now Mightling: the commands are `mling` and `mling-admin`.", file=sys.stderr)
            for line in done + ([stale] if stale else []):
                print(f"   {line}", file=sys.stderr)
        return done

    @classmethod
    def rewrite_keys(cls, path: Path) -> bool:
        """
        Rewrites `puffin_<key> =` lines to `mightling_<key> =`, whole-line key matches only.

        Args:
            path: A `dreamference.toml`-style file; a missing one is not an error.

        Returns:
            bool: Whether the file changed.
        """
        if not path.is_file():
            return False
        text = path.read_text()
        pattern = re.compile(rf"^(\s*){LEGACY_KEY_PREFIX}([A-Za-z0-9_]+)(\s*=)", re.M)
        new = pattern.sub(rf"\g<1>{KEY_PREFIX}\g<2>\g<3>", text)
        if new == text:
            return False
        path.write_text(new)
        return True

    @classmethod
    def rewrite_user_config(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: A line when `~/.config/dreamference/config.toml` had old keys.
        """
        path = Path(os.path.expanduser("~/.config/dreamference/config.toml"))
        return f"renamed the puffin_* settings in {path}" if cls.rewrite_keys(path) else None

    @classmethod
    def stale_project_config(cls) -> Optional[str]:
        """
        A project's own `dreamference.toml` is only read here, never rewritten: `mling` rewrites it
        when it runs in that project, and an old key in it would otherwise be silently ignored.

        Returns:
            Optional[str]: A hint when the file in the current folder still has old keys.
        """
        path = Path("dreamference.toml")
        try:
            text = path.read_text() if path.is_file() else ""
        except OSError:
            return None
        if re.search(rf"^\s*{LEGACY_KEY_PREFIX}[A-Za-z0-9_]+\s*=", text, re.M):
            return (f"{path.resolve()} still has puffin_* settings, which are no longer read: rename "
                    f"them mightling_*, or run `mling` once in that folder")
        return None

    @classmethod
    def replace_night_units(cls) -> Optional[str]:
        """
        Replaces the old Night Shift timer with the new one, in the same window, if it was on.

        Returns:
            Optional[str]: A line when old units were found.
        """
        from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler, WINDOW_MARKER

        directory = NightShiftScheduler.unit_dir()
        timer = directory / f"{LEGACY_NIGHT_UNIT}.timer"
        service = directory / f"{LEGACY_NIGHT_UNIT}.service"
        if not timer.exists() and not service.exists():
            return None
        window = None
        if timer.is_file():
            for line in timer.read_text().splitlines():
                if line.startswith(WINDOW_MARKER):
                    window = line[len(WINDOW_MARKER):].strip()
        enabled = NightShiftScheduler.systemctl(["is-enabled", f"{LEGACY_NIGHT_UNIT}.timer"]).returncode == 0
        NightShiftScheduler.systemctl(["disable", "--now", f"{LEGACY_NIGHT_UNIT}.timer"])
        for path in (timer, service):
            if path.exists():
                path.unlink()
        NightShiftScheduler.systemctl(["daemon-reload"])
        if enabled and window and NightShiftScheduler.enable(window):
            return f"replaced the Night Shift timer ({window})"
        return "removed the old Night Shift timer (it was off)" if not enabled else \
            "removed the old Night Shift timer; turn it on again with `mling-admin night enable`"

    @classmethod
    def replace_service_file(cls) -> Optional[str]:
        """
        Points the old Avahi file at the new service type, then moves it to its new name. The folder
        is root's, the file this user's: the content is rewritten in place at once (clients see the
        node again), and the rename goes through the advertiser's visible sudo, or is printed.

        Returns:
            Optional[str]: A line when the old file was found.
        """
        from dreamference.node.node_advertiser import NodeAdvertiser
        from dreamference.node.node_service_file import NodeServiceFile, SERVICE_TYPE

        new = NodeServiceFile.service_path
        legacy = new.parent / LEGACY_SERVICE_FILE
        if not legacy.is_file():
            return None
        text = legacy.read_text()
        if LEGACY_SERVICE_TYPE in text:
            text = text.replace(LEGACY_SERVICE_TYPE, SERVICE_TYPE)
            try:
                legacy.write_text(text)
            except OSError:
                pass
        if new.exists():
            NodeAdvertiser.run_privileged(["rm", "-f", str(legacy)], "remove the old advertisement file")
            return f"the node is advertised as {SERVICE_TYPE} ({new})"
        if NodeAdvertiser.run_privileged(["mv", str(legacy), str(new)], "rename the advertisement file"):
            return f"the node is advertised as {SERVICE_TYPE} ({new})"
        return f"the node is advertised as {SERVICE_TYPE}; its file keeps the old name until the line above is run"

    @classmethod
    def rewrite_authorized_keys(cls) -> Optional[str]:
        """
        Points paired senders' forced command at `mling-admin` and gives their lines the new marker,
        so a node paired under Puffin keeps answering its senders.

        Returns:
            Optional[str]: A line when old pairing lines were found.
        """
        from dreamference.node.node_pairing import KEY_COMMENT
        from dreamference.node.node_serve import NodeServe

        path = NodeServe.authorized_keys()
        if not path.is_file():
            return None
        lines = path.read_text().splitlines(keepends=True)
        changed = 0
        out = []
        for line in lines:
            body = line.rstrip("\n")
            if body.endswith(f" {LEGACY_KEY_COMMENT}") and f"/{LEGACY_ADMIN} node serve-job" in body:
                old_admin = re.search(rf'command="(\S*/{LEGACY_ADMIN}) ', body)
                admin = NodeServe.admin_executable()
                if old_admin:
                    sibling = Path(old_admin.group(1)).with_name("mling-admin")
                    admin = str(sibling) if sibling.exists() else admin
                    body = body.replace(old_admin.group(1), admin, 1)
                body = body[: -len(LEGACY_KEY_COMMENT)] + KEY_COMMENT
                changed += 1
                line = body + ("\n" if line.endswith("\n") else "")
            out.append(line)
        if not changed:
            return None
        path.write_text("".join(out))
        return f"pointed {changed} paired sender(s) at mling-admin in {path}"

    @classmethod
    def move_desktop_data(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: A line when the desktop app's old data folder was moved (it holds the
            web chat's sign-in, which would otherwise be lost).
        """
        legacy = Path(os.path.expanduser(LEGACY_DESKTOP_DATA))
        new = Path(os.path.expanduser(DESKTOP_DATA))
        if not legacy.is_dir() or new.exists():
            return None
        shutil.move(str(legacy), str(new))
        return f"moved the desktop app's data to {new}"
