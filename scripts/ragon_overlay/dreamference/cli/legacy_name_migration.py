"""Puffin and Mightling became Ragon (specs/DREAMFERENCE_RENAME_RAGON.md §4): `ragon-admin`'s half of
the one-time migration.

`ragon` moves the binaries, the agent's home, the links and the user's configuration keys. What
only the Python side knows how to redo is moved here, the first time any `ragon-admin` command runs
on a machine that still has it: the install folder and the build caches, the Night Shift timer, the
Avahi service file, the pairing lines in `authorized_keys`, the pinned host keys and the desktop
app's data. Two generations are migrated, 1.4.x (Puffin) and 1.5 to 1.8 (Mightling). Each step
checks before it acts, so on a machine with nothing old it does nothing and says nothing. The old
names are migrated, never kept working beside the new ones. The root-only part (the Avahi file, the
AppArmor profiles) is printed as one line to run, never run silently.

It runs only from an installed release (`release_install`): the package imported from a
`site-packages` folder with no source tree beside it. A developer's checkout, editable or on
PYTHONPATH, never migrates by itself; a developer who means to migrate sets
`RAGON_LEGACY_MIGRATION=1` (and `=0` switches it off on an installed release too; the old
`MIGHTLING_LEGACY_MIGRATION=0` still does). It also waits while a SWE-bench run or a Night Shift
run is active, because it moves the install folder those use.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from http import HTTPStatus
from pathlib import Path
from typing import Final

LEGACY_KEY_PREFIXES: Final[tuple] = ("puffin_", "mightling_")
KEY_PREFIX: Final[str] = "ragon_"
LEGACY_NIGHT_UNITS: Final[tuple] = ("mightling-night", "puffin-night")
LEGACY_SERVICE_FILES: Final[tuple] = ("mightling-node.service", "puffin-node.service")
LEGACY_SERVICE_TYPES: Final[tuple] = ("_mightling-node._tcp", "_puffin-node._tcp")
LEGACY_FORGE_TYPES: Final[tuple] = ("_mightling-forge._tcp", "_puffin-forge._tcp")
# The forge's container and network, its user units and the state folder inside its data folder.
LEGACY_FORGE_NAME: Final[str] = "mightling-forge"
LEGACY_FORGE_STATE: Final[str] = "mightling"
LEGACY_PHONE_ACCOUNT: Final[str] = "mightling-phone"
# The engine's containers: `ling-serve` and, while it scans, `ling-serve-scan-*`.
LEGACY_ENGINE_CONTAINER: Final[str] = "ling-serve"
# A Signal bridge's system account and unit (the sudo line renames the account; its uid stays).
LEGACY_SIGNAL_UNIT: Final[Path] = Path("/etc/systemd/system/mightling-signal.service")
LEGACY_SIGNAL_ACCOUNT: Final[str] = "mightling-signal"
# The `main` record of an advertised node that serves a model a coding client can use.
MAIN_RECORD_ON: Final[str] = "1"
FORGE_RENAME_TRIES: Final[int] = 6
FORGE_RENAME_WAIT_S: Final[int] = 5
# (key comment, admin command) of the pairing lines each generation wrote.
LEGACY_PAIRING: Final[tuple] = (("mightling-node", "ling-admin"), ("puffin-node", "puffin-admin"))
LEGACY_DESKTOP_DATA: Final[tuple] = (
    "~/.local/share/dev.dreamference.mightling",
    "~/.local/share/dev.dreamference.puffin",
)
DESKTOP_DATA: Final[str] = "~/.local/share/dev.dreamference.ragon"
LEGACY_INSTALL_DIRS: Final[tuple] = (
    "~/.local/share/dreamference/mightling",
    "~/.local/share/dreamference/puffin",
)
# The build caches whose names still say Puffin, moved to their Ragon names.
BUILD_CACHES: Final[tuple] = (
    ("puffin-codex", "ragon-codex"),
    ("puffin-web", "ragon-web"),
    ("puffin-code-build", "ragon-code-build"),
)
# The binaries the old install folders held, by their old and new names (`codex-code-mode-host` keeps its).
LEGACY_BINARIES: Final[tuple] = (
    ("puffin", "ragon"),
    ("puffin-search", "ragon-search"),
    ("puffin-fetch", "ragon-fetch"),
    ("puffin-code", "ragon-code"),
    ("ling", "ragon"),
    ("ling-search", "ragon-search"),
    ("ling-fetch", "ragon-fetch"),
    ("ling-code", "ragon-code"),
    ("ling-docs", "ragon-docs"),
    ("ling-app", "ragon-app"),
    ("ling-signal", "ragon-signal"),
)
LEGACY_LINKS: Final[tuple] = (
    "puffin",
    "puffin-search",
    "puffin-fetch",
    "puffin-code",
    "puffin-app",
    "puffin-admin",
    "ling",
    "ling-search",
    "ling-fetch",
    "ling-code",
    "ling-docs",
    "ling-app",
    "ling-admin",
)
# Units that must not be running while the install folder moves.
BUSY_UNIT_PATTERNS: Final[tuple] = ("ling-swe-*", "ragon-swe-*", "mightling-night*", "ragon-night*")
# `1` migrates even from a source tree (to test the migration on purpose, in a scratch HOME); `0`
# never migrates. Unset, the migration runs only from an installed release. `ragon` reads the same.
OPT_IN_ENV: Final[str] = "RAGON_LEGACY_MIGRATION"
OLD_OPT_IN_ENV: Final[str] = "MIGHTLING_LEGACY_MIGRATION"
OFF_VALUES: Final[tuple] = ("0",)
# A folder pip installs packages into; a checkout or a PYTHONPATH folder is never named so.
INSTALL_FOLDERS: Final[tuple] = ("site-packages", "dist-packages")
# What sits beside the package in a source tree and never in an installed one.
SOURCE_MARKERS: Final[tuple] = (".git", "setup.py", "pyproject.toml", "codex-patches", "ragon-rs")


class LegacyNameMigration:
    """Moves what a Puffin or Mightling node left under the old names to the new ones, once."""

    @classmethod
    def release_install(cls, package_dir: Path | None = None) -> bool:
        """Tells an installed release from a source tree, by where the package was imported from.

        A release install (`install.sh`) puts the wheel in a virtualenv of its own, so the package
        is in that virtualenv's `site-packages`, with no source beside it. A checkout imports it
        from the repository, through an editable install or PYTHONPATH, with `setup.py`,
        `codex-patches/` and `ragon-rs/` beside it (`CodexBrandedBuilder.has_source()` is the same
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
        """Whether this process may migrate: an installed release, unless the switch says otherwise.

        The old `MIGHTLING_LEGACY_MIGRATION=0` blocks it when the new one is unset.

        Returns:
            bool: True when the migration may run.
        """
        choice = os.environ.get(OPT_IN_ENV, "").strip()
        if choice in ("0", "1"):
            return choice == "1"
        if os.environ.get(OLD_OPT_IN_ENV, "").strip() in OFF_VALUES:
            return False
        return cls.release_install()

    @classmethod
    def busy_units(cls) -> list[str]:
        """The running user units of a SWE-bench run or Night Shift.

        Returns:
            list[str]: The unit names; empty when none (or when systemctl cannot be asked).
        """
        try:
            found = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "list-units",
                    "--state=running",
                    "--no-legend",
                    "--plain",
                    *BUSY_UNIT_PATTERNS,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [line.split()[0] for line in found.stdout.splitlines() if line.strip()]

    @classmethod
    def run(cls) -> list[str]:
        """Runs every step and prints a notice when one did something.

        From a source tree it does nothing and says nothing (`allowed`); while a SWE-bench run or Night Shift
        is active it waits for the next command.

        Returns:
            List[str]: What was done, one line per step; empty when there was nothing old.
        """
        if not cls.allowed():
            return []
        busy = cls.busy_units()
        if busy and cls.anything_old():
            print(
                f"Mightling is now Ragon: the migration waits for {', '.join(busy)} to finish.",
                file=sys.stderr,
            )
            return []
        done: list[str] = []
        for step in (
            cls.move_install_dir,
            cls.move_build_caches,
            cls.remove_old_links,
            cls.rewrite_user_config,
            cls.replace_night_units,
            cls.remove_old_engine_containers,
            cls.migrate_forge,
            cls.replace_service_file,
            cls.rewrite_authorized_keys,
            cls.rekey_paired_nodes,
            cls.move_desktop_data,
        ):
            try:
                line = step()
            except OSError as error:
                line = f"could not finish {step.__name__}: {error}"
            if line:
                done.append(line)
        stale = cls.stale_project_config()
        if done or stale:
            print(
                "Mightling is now Ragon: the commands are `ragon` and `ragon-admin`.",
                file=sys.stderr,
            )
            for line in done + ([stale] if stale else []):
                print(f"   {line}", file=sys.stderr)
            privileged = cls.privileged_line()
            if privileged:
                print(f"   once, as root: {privileged}", file=sys.stderr)
        return done

    @classmethod
    def anything_old(cls) -> bool:
        """Returns: bool: Whether an old install folder, link or unit is still on this machine."""
        paths = [Path(p).expanduser() for p in LEGACY_INSTALL_DIRS]
        links = Path("~/.local/bin").expanduser()
        paths += [links / name for name in LEGACY_LINKS]
        return any(path.is_dir() or path.is_symlink() for path in paths)

    @classmethod
    def privileged_line(cls) -> str | None:
        """The line the owner runs with sudo for the root-owned leftovers.

        Returns:
            str | None: The command (AppArmor profiles, the Avahi file), or None when there are none.
        """
        old = [Path("/etc/apparmor.d") / name for name in ("puffin-bwrap", "mightling-bwrap")]
        lines = ["sudo ragon-admin host setup"] if any(path.exists() for path in old) else []
        if LEGACY_SIGNAL_UNIT.exists():
            # The account keeps its uid, so the bridge's state and the device link survive; `ragon signal
            # start` installs the unit under its new name.
            lines.append(
                f"sudo sh -c 'systemctl disable --now {LEGACY_SIGNAL_UNIT.name}; "
                f"usermod -l ragon-signal {LEGACY_SIGNAL_ACCOUNT}; "
                f"groupmod -n ragon-signal {LEGACY_SIGNAL_ACCOUNT}; rm -f {LEGACY_SIGNAL_UNIT}'"
            )
        return "; ".join(lines) if lines else None

    @classmethod
    def move_install_dir(cls) -> str | None:
        """Moves the old install folder, with the code index's tools, to the new one.

        Returns:
            Optional[str]: A line when the folder was moved.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR

        new = Path(INSTALL_DIR)
        moved = None
        for name in LEGACY_INSTALL_DIRS:
            legacy = Path(name).expanduser()
            if legacy.is_dir() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(new))
                moved = f"moved {legacy} to {new}"
        for old, current in LEGACY_BINARIES:
            if (new / "bin" / old).is_file() and not (new / "bin" / current).exists():
                (new / "bin" / old).rename(new / "bin" / current)
        return moved

    @classmethod
    def move_build_caches(cls) -> str | None:
        """Moves the build caches that still carry the Puffin name to their Ragon names.

        Returns:
            Optional[str]: A line when a cache was moved.
        """
        base = Path("~/.cache/dreamference").expanduser()
        moved = []
        for old, new in BUILD_CACHES:
            if (base / old).is_dir() and not (base / new).exists():
                shutil.move(str(base / old), str(base / new))
                moved.append(old)
        return (
            f"moved the build caches {', '.join(moved)} to their new names in {base}"
            if moved
            else None
        )

    @classmethod
    def remove_old_links(cls) -> str | None:
        """Removes the old command links from `~/.local/bin`, links only (a real file is someone else's).

        `codex build` and `ragon` make the new ones.

        Returns:
            Optional[str]: A line when old links were removed.
        """
        links = Path("~/.local/bin").expanduser()
        removed = [name for name in LEGACY_LINKS if (links / name).is_symlink()]
        for name in removed:
            (links / name).unlink()
        return f"removed the links {', '.join(removed)} from {links}" if removed else None

    @classmethod
    def rewrite_keys(cls, path: Path) -> bool:
        """Rewrites `puffin_`/`mightling_` keys to `ragon_` (whole lines) and the engine value `ling` to `ragon`.

        Args:
            path: A `dreamference.toml`-style file; a missing one is not an error.

        Returns:
            bool: Whether the file changed.
        """
        if not path.is_file():
            return False
        text = path.read_text()
        prefixes = "|".join(LEGACY_KEY_PREFIXES)
        new = re.sub(
            rf"^(\s*)(?:{prefixes})([A-Za-z0-9_]+)(\s*=)",
            rf"\g<1>{KEY_PREFIX}\g<2>\g<3>",
            text,
            flags=re.M,
        )
        new = re.sub(r'^(\s*server_engine\s*=\s*)"ling"', r'\g<1>"ragon"', new, flags=re.M)
        if new == text:
            return False
        path.write_text(new)
        return True

    @classmethod
    def rewrite_user_config(cls) -> str | None:
        """Returns: Optional[str]: A line when `~/.config/dreamference/config.toml` had old keys."""
        path = Path("~/.config/dreamference/config.toml").expanduser()
        return (
            f"renamed the puffin_* and mightling_* settings in {path}"
            if cls.rewrite_keys(path)
            else None
        )

    @classmethod
    def stale_project_config(cls) -> str | None:
        """Names a project's own `dreamference.toml` that still has old keys; it is never rewritten here.

        Returns:
            Optional[str]: A hint when the file in the current folder still has old keys.
        """
        path = Path("dreamference.toml")
        try:
            text = path.read_text() if path.is_file() else ""
        except OSError:
            return None
        prefixes = "|".join(LEGACY_KEY_PREFIXES)
        if re.search(rf"^\s*(?:{prefixes})[A-Za-z0-9_]+\s*=", text, re.M):
            return (
                f"{path.resolve()} still has puffin_* or mightling_* settings, which are no longer "
                f"read: rename them ragon_*, or run `ragon` once in that folder"
            )
        return None

    @staticmethod
    def timer_window(timer: Path, marker: str) -> str | None:
        """Reads the window a Night Shift timer file records after `marker`, if it has one."""
        window = None
        if timer.is_file():
            for line in timer.read_text().splitlines():
                if line.startswith(marker):
                    window = line[len(marker) :].strip()
        return window

    @classmethod
    def replace_night_units(cls) -> str | None:
        """Replaces the old Night Shift timer with the new one, in the same window, if it was on.

        Returns:
            Optional[str]: A line when old units were found.
        """
        from dreamference.night_shift.night_shift_scheduler import (
            WINDOW_MARKER,
            NightShiftScheduler,
        )

        directory = NightShiftScheduler.unit_dir()
        lines = []
        for unit in LEGACY_NIGHT_UNITS:
            timer = directory / f"{unit}.timer"
            service = directory / f"{unit}.service"
            if not timer.exists() and not service.exists():
                continue
            window = cls.timer_window(timer, WINDOW_MARKER)
            enabled = NightShiftScheduler.systemctl(["is-enabled", f"{unit}.timer"]).returncode == 0
            NightShiftScheduler.systemctl(["disable", "--now", f"{unit}.timer"])
            for path in (timer, service):
                if path.exists():
                    path.unlink()
            NightShiftScheduler.systemctl(["daemon-reload"])
            if enabled and window and NightShiftScheduler.enable(window):
                lines.append(f"replaced the Night Shift timer ({window})")
            elif enabled:
                lines.append(
                    "removed the old Night Shift timer; turn it on again with `ragon-admin night enable`"
                )
            else:
                lines.append("removed the old Night Shift timer (it was off)")
        return "; ".join(lines) if lines else None

    @classmethod
    def with_both_types(cls, text: str) -> str:
        """Renders an old service file's records again, so the new file carries both service types.

        Args:
            text: The old file's text, its types already renamed.

        Returns:
            str: The text of a file that advertises the node (and the forge) under both types; the
            given text when its records cannot be read.
        """
        from dreamference.node.node_service_file import NodeServiceFile

        records = NodeServiceFile.parse(text)
        if not records or not records.get("node"):
            return text

        def number(key: str) -> int | None:
            return int(records[key]) if records.get(key, "").isdigit() else None

        try:
            return NodeServiceFile.render(
                port=int(records["port"]),
                node_id=records["node"],
                version=records.get("version", ""),
                state=records.get("state") or "stopped",
                web_port=number("web"),
                search_port=number("search"),
                main=records.get("main") == MAIN_RECORD_ON,
                remote=records.get("remote") or None,
                forge=NodeServiceFile.parse_forge(text),
            )
        except ValueError:
            return text

    @classmethod
    def remove_old_engine_containers(cls) -> str | None:
        """Removes the model server's containers that still carry the old name.

        The engine starts again under its new name at the next `ragon-admin server start`. This
        runs by day only: the migration waits while a Night Shift or SWE-bench run is active.

        Returns:
            str | None: A line when a container was removed.
        """
        from dreamference.forge.forge_container import ForgeContainer

        listed = ForgeContainer.docker(["ps", "-a", "--format", "{{.Names}}"], timeout=30)
        names = [
            name
            for name in listed.stdout.split()
            if name == LEGACY_ENGINE_CONTAINER or name.startswith(f"{LEGACY_ENGINE_CONTAINER}-scan-")
        ]
        removed = [name for name in names if ForgeContainer.docker(["rm", "-f", name]).returncode == 0]
        if not removed:
            return None
        return f"removed the container {', '.join(removed)}; `ragon-admin server start` starts the engine again"

    @classmethod
    def forge_leftovers(cls) -> tuple[list[Path], Path]:
        """Finds what an older forge left behind.

        Returns:
            tuple[list[Path], Path]: The old user unit files, and the old state folder inside the
            forge's data folder (which may not exist).
        """
        from dreamference.forge.forge_service import ForgeService
        from dreamference.forge.forge_settings import ForgeSettings

        units = sorted(path for path in ForgeService.unit_dir().glob(f"{LEGACY_FORGE_NAME}*") if path.is_file())
        return units, ForgeSettings.data_dir() / LEGACY_FORGE_STATE

    @classmethod
    def migrate_forge(cls) -> str | None:
        """Moves the forge to its new names: units, container, network, state folder and phone account.

        The forge's data (repositories, database) is host data and stays where it is. The old
        container and network are removed and the new ones are made when the units start again,
        if the forge was enabled.

        Returns:
            str | None: A line when anything of the old forge was found.
        """
        from dreamference.forge.forge_container import ForgeContainer
        from dreamference.forge.forge_service import TIMERS, ForgeService
        from dreamference.forge.forge_settings import UNIT, ForgeSettings

        units, old_state = cls.forge_leftovers()
        if not units and not old_state.is_dir():
            return None
        old_service = f"{LEGACY_FORGE_NAME}.service"
        enabled = ForgeService.systemctl("is-enabled", old_service).returncode == 0
        if units:
            ForgeService.systemctl("disable", "--now", *[path.name for path in units])
            for path in units:
                path.unlink()
        ForgeContainer.docker(["rm", "-f", LEGACY_FORGE_NAME], timeout=60)
        ForgeContainer.docker(["network", "rm", LEGACY_FORGE_NAME], timeout=30)
        new_state = ForgeSettings.data_dir() / "ragon"
        if old_state.is_dir() and not new_state.exists():
            shutil.move(str(old_state), str(new_state))
        if not enabled:
            ForgeService.systemctl("daemon-reload")
            return "moved the forge to its new names (it was off)"
        ForgeService.install_units()
        ForgeService.systemctl("enable", "--now", UNIT, *TIMERS)
        renamed = cls.rename_phone_account()
        return "moved the forge to its new names" + (f"; {renamed}" if renamed else "")

    @classmethod
    def rename_phone_account(cls) -> str | None:
        """Renames the forge's phone account through Forgejo's admin API (its token survives).

        Returns:
            str | None: A line when the account was renamed, or why it was not.
        """
        from dreamference.forge.forge_accounts import ForgeAccounts
        from dreamference.forge.forge_container import ForgeContainer
        from dreamference.forge.forge_phone import PHONE_ACCOUNT

        admin = ForgeAccounts.admin_token()
        if not admin:
            return None
        status = 0
        for _ in range(FORGE_RENAME_TRIES):
            status, _body = ForgeContainer.api("GET", f"/users/{LEGACY_PHONE_ACCOUNT}", admin, timeout=10)
            if status:
                break
            time.sleep(FORGE_RENAME_WAIT_S)
        if status != HTTPStatus.OK:
            return None
        status, _body = ForgeContainer.api(
            "POST", f"/admin/users/{LEGACY_PHONE_ACCOUNT}/rename", admin, {"new_username": PHONE_ACCOUNT}
        )
        if status in (HTTPStatus.OK, HTTPStatus.NO_CONTENT):
            return f"renamed the forge account {LEGACY_PHONE_ACCOUNT} to {PHONE_ACCOUNT}"
        return f"could not rename the forge account {LEGACY_PHONE_ACCOUNT} (answer {status})"

    @classmethod
    def replace_service_file(cls) -> str | None:
        """Points the old Avahi file at the new service type, then moves it to its new name.

        The folder is root's, the file this user's: the content is rewritten in place at once (clients see the
        node again), and the rename goes through the advertiser's visible sudo, or is printed.

        Returns:
            Optional[str]: A line when the old file was found.
        """
        from dreamference.node.node_advertiser import NodeAdvertiser
        from dreamference.node.node_service_file import (
            FORGE_SERVICE_TYPE,
            SERVICE_TYPE,
            NodeServiceFile,
        )

        new = NodeServiceFile.service_path
        line = None
        for name in LEGACY_SERVICE_FILES:
            legacy = new.parent / name
            if not legacy.is_file():
                continue
            text = legacy.read_text()
            for old_type in LEGACY_SERVICE_TYPES:
                text = text.replace(old_type, SERVICE_TYPE)
            for old_type in LEGACY_FORGE_TYPES:
                text = text.replace(old_type, FORGE_SERVICE_TYPE)
            text = cls.with_both_types(text)
            try:
                legacy.write_text(text)
            except OSError:
                pass
            if new.exists():
                NodeAdvertiser.run_privileged(
                    ["rm", "-f", str(legacy)], "remove the old advertisement file"
                )
                line = f"the node is advertised as {SERVICE_TYPE} ({new})"
            elif NodeAdvertiser.run_privileged(
                ["mv", str(legacy), str(new)], "rename the advertisement file"
            ):
                line = f"the node is advertised as {SERVICE_TYPE} ({new})"
            else:
                line = (
                    f"the node is advertised as {SERVICE_TYPE}; its file keeps the old name until the "
                    f"line above is run"
                )
        return line

    @classmethod
    def rewrite_authorized_keys(cls) -> str | None:
        """Points paired senders at `ragon-admin` and gives their lines the new marker.

        Returns:
            str | None: A line when old pairing lines were found.
        """
        from dreamference.node.node_serve import NodeServe

        path = NodeServe.authorized_keys()
        if not path.is_file():
            return None
        out = []
        changed = 0
        for line in path.read_text().splitlines(keepends=True):
            new = cls._repoint_pairing_line(line)
            changed += new != line
            out.append(new)
        if not changed:
            return None
        path.write_text("".join(out))
        return f"pointed {changed} paired sender(s) at ragon-admin in {path}"

    @classmethod
    def _repoint_pairing_line(cls, line: str) -> str:
        """Gives one `authorized_keys` line of an older generation the new admin command and marker.

        Args:
            line: A line of `authorized_keys`, with its newline.

        Returns:
            str: The line, rewritten when an older generation wrote it, otherwise as it was.
        """
        from dreamference.node.node_pairing import KEY_COMMENT
        from dreamference.node.node_serve import NodeServe

        body = line.rstrip("\n")
        for comment, legacy_admin in LEGACY_PAIRING:
            if not (body.endswith(f" {comment}") and f"/{legacy_admin} node serve-job" in body):
                continue
            admin = NodeServe.admin_executable()
            old_admin = re.search(rf'command="(\S*/{legacy_admin}) ', body)
            if old_admin:
                sibling = Path(old_admin.group(1)).with_name("ragon-admin")
                admin = str(sibling) if sibling.exists() else admin
                body = body.replace(old_admin.group(1), admin, 1)
            body = body[: -len(comment)] + KEY_COMMENT
            return body + ("\n" if line.endswith("\n") else "")
        return line

    @classmethod
    def rekey_paired_nodes(cls) -> str | None:
        """Moves each paired node's pinned host key from the old aliases to `ragon-node-<id>`.

        The alias is a `HostKeyAlias`, never a name on the wire, and the pairing's known-hosts file may
        be hashed, so the lines are found and removed with `ssh-keygen -F` and `-R` rather than by text.

        Returns:
            str | None: A line when a key was moved.
        """
        from dreamference.node.node_pairing import NodePairing

        path = NodePairing.known_hosts()
        if not path.is_file():
            return None
        moved = sum(cls._move_alias(path, record["node"]) for record in NodePairing.paired())
        if not moved:
            return None
        return f"moved {moved} paired node(s)' host key to the new alias in {path}"

    @classmethod
    def _move_alias(cls, path: Path, node_id: str) -> int:
        """Moves one node's pinned key to its new alias, from the first older alias that has one.

        Args:
            path: The pairing's known-hosts file.
            node_id: The paired node's id.

        Returns:
            int: 1 when a key was moved, otherwise 0.
        """
        from dreamference.node.node_pairing import NodePairing

        new_alias = NodePairing.host_alias(node_id)
        if cls._pinned_keys(path, new_alias):
            return 0
        for comment, _ in LEGACY_PAIRING:
            old_alias = f"{comment}-{node_id}"
            keys = cls._pinned_keys(path, old_alias)
            if not keys:
                continue
            with path.open("a") as handle:
                handle.writelines(f"{new_alias} {key}\n" for key in keys)
            subprocess.run(
                ["ssh-keygen", "-q", "-R", old_alias, "-f", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            Path(f"{path}.old").unlink(missing_ok=True)
            return 1
        return 0

    @classmethod
    def _pinned_keys(cls, path: Path, alias: str) -> list[str]:
        """The `<type> <key>` pairs stored under a `HostKeyAlias` in a known-hosts file, hashed or plain.

        Args:
            path: A known-hosts file.
            alias: A `HostKeyAlias`.

        Returns:
            list[str]: The pairs; empty when none.
        """
        found = subprocess.run(
            ["ssh-keygen", "-F", alias, "-f", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        keys = []
        for line in found.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#"):
                keys.append(" ".join(fields[1:3]))
        return keys

    @classmethod
    def move_desktop_data(cls) -> str | None:
        """Moves the desktop app's old data folder (it holds the web chat's sign-in).

        Returns:
            str | None: A line when the folder was moved.
        """
        new = Path(DESKTOP_DATA).expanduser()
        for name in LEGACY_DESKTOP_DATA:
            legacy = Path(name).expanduser()
            if legacy.is_dir() and not new.exists():
                shutil.move(str(legacy), str(new))
                return f"moved the desktop app's data to {new}"
        return None
