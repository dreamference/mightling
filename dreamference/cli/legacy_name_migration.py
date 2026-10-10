"""
Puffin became Mightling (specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2): `ling-admin`'s half of the
one-time migration.

`ling` moves the binaries, the agent's home, the links and the user's configuration keys. What
only the Python side knows how to redo is moved here, the first time any `ling-admin` command runs
on a machine that still has it: the Night Shift timer, the Avahi service file, the pairing lines in
`authorized_keys`, the user-level configuration keys and the desktop app's data. Each step checks
before it acts, so on a machine with nothing old it does nothing and says nothing. The old names
are migrated, never kept working beside the new ones.

It runs only from an installed release (`release_install`): the package imported from a
`site-packages` folder with no source tree beside it. On 2026-10-08 the package was imported from a
source worktree (`PYTHONPATH=<worktree> python -m ...`) with the machine's real HOME; the migration
moved the live install folder of a machine still running Puffin, removed its links and replaced its
Night Shift units with ones naming a `ling-admin` that did not exist yet, and a running benchmark
broke. A developer's checkout, editable or on PYTHONPATH, never migrates by itself; a developer who
means to migrate sets `MIGHTLING_LEGACY_MIGRATION=1` (and `=0` switches it off on an installed
release too).
"""

import os
import re
import shutil
import subprocess
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
LEGACY_INSTALL_DIR: Final[str] = "~/.local/share/dreamference/puffin"
# The binaries the old install folder held, by their old and new names (`codex-code-mode-host` keeps its).
LEGACY_BINARIES: Final[tuple] = (("puffin", "ling"), ("puffin-search", "ling-search"),
                                 ("puffin-fetch", "ling-fetch"), ("puffin-code", "ling-code"))
LEGACY_LINKS: Final[tuple] = ("puffin", "puffin-search", "puffin-fetch", "puffin-code", "puffin-app",
                              "puffin-admin")
# `1` migrates even from a source tree (to test the migration on purpose, in a scratch HOME); `0`
# never migrates. Unset, the migration runs only from an installed release. `ling` reads the same.
OPT_IN_ENV: Final[str] = "MIGHTLING_LEGACY_MIGRATION"
# A folder pip installs packages into; a checkout or a PYTHONPATH folder is never named so.
INSTALL_FOLDERS: Final[tuple] = ("site-packages", "dist-packages")
# What sits beside the package in a source tree and never in an installed one.
SOURCE_MARKERS: Final[tuple] = (".git", "setup.py", "pyproject.toml", "codex-patches", "ling-rs")


class LegacyNameMigration:
    """Moves what a Puffin 1.4.x node left under the old names to the new ones, once."""

    @classmethod
    def release_install(cls, package_dir: Optional[Path] = None) -> bool:
        """
        Tells an installed release from a source tree, by where the package was imported from.

        A release install (`install.sh`) puts the wheel in a virtualenv of its own, so the package
        is in that virtualenv's `site-packages`, with no source beside it. A checkout imports it
        from the repository, through an editable install or PYTHONPATH, with `setup.py`,
        `codex-patches/` and `ling-rs/` beside it (`CodexBrandedBuilder.has_source()` is the same
        test). Both conditions are required, so a copy of the package placed anywhere else is not a
        release either.

        Args:
            package_dir (Optional[Path]): The `dreamference` package folder; None means the one
                this module was imported from.

        Returns:
            bool: True only for a package installed by pip into a `site-packages` folder.
        """
        package_dir = package_dir or Path(__file__).resolve().parent.parent
        parent = package_dir.parent
        if parent.name not in INSTALL_FOLDERS:
            return False
        return not any((parent / marker).exists() for marker in SOURCE_MARKERS)

    @classmethod
    def allowed(cls) -> bool:
        """
        Whether this process may migrate: from an installed release, unless `MIGHTLING_LEGACY_MIGRATION`
        says otherwise.

        Returns:
            bool: True when the migration may run.
        """
        choice = os.environ.get(OPT_IN_ENV, "").strip()
        if choice in ("0", "1"):
            return choice == "1"
        return cls.release_install()

    @classmethod
    def run(cls) -> List[str]:
        """
        Runs every step and prints a notice when one did something. From a source tree it does
        nothing and says nothing (`allowed`).

        Returns:
            List[str]: What was done, one line per step; empty when there was nothing old.
        """
        if not cls.allowed():
            return []
        done: List[str] = []
        for step in (cls.move_install_dir, cls.remove_old_links, cls.rewrite_user_config,
                     cls.replace_night_units, cls.replace_service_file, cls.rewrite_authorized_keys,
                     cls.rekey_paired_nodes, cls.move_desktop_data):
            try:
                line = step()
            except OSError as error:
                line = f"could not finish {step.__name__}: {error}"
            if line:
                done.append(line)
        stale = cls.stale_project_config()
        if done or stale:
            print("🐦 Puffin is now Mightling: the commands are `ling` and `ling-admin`.", file=sys.stderr)
            for line in done + ([stale] if stale else []):
                print(f"   {line}", file=sys.stderr)
        return done

    @classmethod
    def move_install_dir(cls) -> Optional[str]:
        """
        Moves the old install folder, with the code index's tools in it, to the new one, before a
        `codex build` or `code setup` would start an empty one beside it.

        Returns:
            Optional[str]: A line when the folder was moved.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR

        legacy, new = Path(os.path.expanduser(LEGACY_INSTALL_DIR)), Path(INSTALL_DIR)
        if not legacy.is_dir() or new.exists():
            return None
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(new))
        for old, current in LEGACY_BINARIES:
            if (new / "bin" / old).is_file() and not (new / "bin" / current).exists():
                (new / "bin" / old).rename(new / "bin" / current)
        return f"moved {legacy} to {new}"

    @classmethod
    def remove_old_links(cls) -> Optional[str]:
        """
        Removes the old command links from `~/.local/bin`, links only: a real file of that name is
        someone else's. `codex build` and `ling` make the new ones.

        Returns:
            Optional[str]: A line when old links were removed.
        """
        links = Path(os.path.expanduser("~/.local/bin"))
        removed = [name for name in LEGACY_LINKS if (links / name).is_symlink()]
        for name in removed:
            (links / name).unlink()
        return f"removed the links {', '.join(removed)} from {links}" if removed else None

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
        A project's own `dreamference.toml` is only read here, never rewritten: `ling` rewrites it
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
                    f"them mightling_*, or run `ling` once in that folder")
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
            "removed the old Night Shift timer; turn it on again with `ling-admin night enable`"

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
        Points paired senders' forced command at `ling-admin` and gives their lines the new marker,
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
                    sibling = Path(old_admin.group(1)).with_name("ling-admin")
                    admin = str(sibling) if sibling.exists() else admin
                    body = body.replace(old_admin.group(1), admin, 1)
                body = body[: -len(LEGACY_KEY_COMMENT)] + KEY_COMMENT
                changed += 1
                line = body + ("\n" if line.endswith("\n") else "")
            out.append(line)
        if not changed:
            return None
        path.write_text("".join(out))
        return f"pointed {changed} paired sender(s) at ling-admin in {path}"

    @classmethod
    def rekey_paired_nodes(cls) -> Optional[str]:
        """
        Moves each paired node's pinned host key from the old alias (`puffin-node-<id>`) to the
        new one (`mightling-node-<id>`), so a connection to a node paired under Puffin finds its
        key. The alias is a `HostKeyAlias`, never a name on the wire, and the pairing's known-hosts
        file may be hashed, so the lines are found and removed with `ssh-keygen -F` and `-R`
        rather than by text. Found missing on 2026-10-10: the renamed sender asked for the new
        alias, the file held the old one, and every paired node "did not answer".

        Returns:
            Optional[str]: A line when a key was moved.
        """
        from dreamference.node.node_pairing import NodePairing

        path = NodePairing.known_hosts()
        if not path.is_file():
            return None
        moved = 0
        for record in NodePairing.paired():
            node_id = record["node"]
            old_alias = f"{LEGACY_KEY_COMMENT}-{node_id}"
            new_alias = NodePairing.host_alias(node_id)
            if cls._pinned_keys(path, new_alias):
                continue
            keys = cls._pinned_keys(path, old_alias)
            if not keys:
                continue
            with open(path, "a") as handle:
                handle.writelines(f"{new_alias} {key}\n" for key in keys)
            subprocess.run(["ssh-keygen", "-q", "-R", old_alias, "-f", str(path)],
                           capture_output=True, text=True, check=False)
            Path(f"{path}.old").unlink(missing_ok=True)
            moved += 1
        if not moved:
            return None
        return f"moved {moved} paired node(s)' host key to the new alias in {path}"

    @classmethod
    def _pinned_keys(cls, path: Path, alias: str) -> List[str]:
        """
        Args:
            path: A known-hosts file, hashed or plain.
            alias: A `HostKeyAlias`.

        Returns:
            List[str]: The `<type> <key>` pairs stored under that alias.
        """
        found = subprocess.run(["ssh-keygen", "-F", alias, "-f", str(path)], capture_output=True, text=True,
                               check=False)
        keys = []
        for line in found.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#"):
                keys.append(" ".join(fields[1:3]))
        return keys

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
