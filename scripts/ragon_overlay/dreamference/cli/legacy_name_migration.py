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
FORGE_STATE: Final[str] = "ragon"
# Where an old state folder goes when the new one already exists: kept, never merged or deleted.
FORGE_STATE_ASIDE: Final[str] = "mightling.pre-ragon"
LEGACY_PHONE_ACCOUNT: Final[str] = "mightling-phone"
# The engine's containers: `ling-serve` and, while it scans, `ling-serve-scan-*`.
LEGACY_ENGINE_CONTAINER: Final[str] = "ling-serve"
# A Signal bridge's system accounts, units, state folders, runtime and binary (the sudo line renames
# the accounts, whose uids stay, and moves the rest; `ragon signal setup --refresh` writes the units).
# Relative to the root `privileged_line` is given.
LEGACY_SIGNAL_UNIT: Final[str] = "etc/systemd/system/mightling-signal.service"
LEGACY_SIGNAL_TEMPLATE: Final[str] = "etc/systemd/system/mightling-signal@.service"
LEGACY_SIGNAL_ACCOUNT: Final[str] = "mightling-signal"
SIGNAL_ACCOUNT: Final[str] = "ragon-signal"
SIGNAL_STATE_ROOT: Final[str] = "var/lib"
LEGACY_SIGNAL_RUNTIME: Final[str] = "opt/mightling"
SIGNAL_RUNTIME: Final[str] = "opt/ragon"
LEGACY_SIGNAL_BRIDGE: Final[str] = "usr/local/lib/mightling/ling-signal"
# The user units of every other subsystem (web, chat, sync, the monitor, the Matrix proxy, remote
# access…): moved to their new names with their text rewritten, by `ragon` and here, whichever runs
# first. The Night Shift and forge units have steps of their own (the timer's window, the forge's state).
LEGACY_UNIT_PREFIXES: Final[tuple] = ("mightling-", "puffin-")
LEGACY_SLICES: Final[tuple] = ("mightling.slice", "puffin.slice")
UNITS_WITH_OWN_STEPS: Final[tuple] = ("mightling-night", "puffin-night", "mightling-forge")
UNIT_SUFFIXES: Final[tuple] = (".service", ".timer", ".socket", ".path", ".slice", ".target")
# The folders an old unit's program may sit in, and the new install folder's.
LEGACY_UNIT_BIN_DIRS: Final[tuple] = ("/dreamference/mightling/bin/", "/dreamference/puffin/bin/")
UNIT_BIN_DIR: Final[str] = "/dreamference/ragon/bin/"
LINK_DIR_SUFFIX: Final[str] = "/.local/bin/"
LEGACY_ENV_PREFIX: Final[str] = "MIGHTLING_"
ENV_PREFIX: Final[str] = "RAGON_"
LEGACY_PRODUCT_WORDS: Final[tuple] = ("Mightling", "Puffin")
PRODUCT_WORD: Final[str] = "Ragon"
# The `main` record of an advertised node that serves a model a coding client can use.
MAIN_RECORD_ON: Final[str] = "1"
# How long the forge may take to answer after its first start under the new names, then how often
# the phone account is asked for while the answer is neither found nor not found.
FORGE_ANSWER_WAIT_S: Final[float] = 180.0
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
    ("ling-delegate", "ragon-delegate"),
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
    "ling-delegate",
    "ling-admin",
)
# A program named in an old unit, by its old name and its new one (the binaries, and the admin command).
UNIT_PROGRAMS: Final[dict] = {
    **dict(LEGACY_BINARIES),
    "puffin-app": "ragon-app",
    "puffin-admin": "ragon-admin",
    "ling-admin": "ragon-admin",
}
# Units that must not be running while anything moves: SWE-bench runs, Night Shift and node jobs (`ragon`
# waits for the same ones, rename.rs).
BUSY_UNIT_PATTERNS: Final[tuple] = (
    "ling-swe-*",
    "mightling-swe-*",
    "ragon-swe-*",
    "mightling-night*",
    "ragon-night*",
    "puffin-night*",
    "mightling-job-*",
    "ragon-job-*",
)
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
        """The running user units of a SWE-bench run, Night Shift or a node job.

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

        From a source tree it does nothing and says nothing (`allowed`); while a SWE-bench run, Night Shift
        or a node job is active it changes nothing and waits for the next command (saying so when anything
        old is left).

        Returns:
            List[str]: What was done, one line per step; empty when there was nothing old.
        """
        if not cls.allowed():
            return []
        busy = cls.busy_units()
        if busy:
            if cls.anything_old():
                print(
                    f"Mightling is now Ragon: the migration waits for {', '.join(busy)} to finish.",
                    file=sys.stderr,
                )
            return []
        done = cls.run_steps()
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
                print(f"   once (the sudo parts as root): {privileged}", file=sys.stderr)
        return done

    @classmethod
    def run_steps(cls) -> list[str]:
        """Runs every step once; a step that fails on the file system is reported and the others still run.

        The old user units stop before anything they run from moves, and start again under their new
        names only once it has moved.

        Returns:
            list[str]: What was done, one line per step.
        """
        done: list[str] = []
        try:
            moved_units, to_start = cls.move_user_units()
        except OSError as error:
            moved_units, to_start = [], []
            done.append(f"could not finish move_user_units: {error}")
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
        units_line = cls.start_user_units(moved_units, to_start)
        if units_line:
            done.append(units_line)
        return done

    @classmethod
    def anything_old(cls) -> bool:
        """Returns: bool: Whether an old install folder, link or user unit is still on this machine."""
        paths = [Path(p).expanduser() for p in LEGACY_INSTALL_DIRS]
        links = Path("~/.local/bin").expanduser()
        paths += [links / name for name in LEGACY_LINKS]
        return any(path.is_dir() or path.is_symlink() for path in paths) or bool(
            cls.old_user_units()
        )

    @classmethod
    def legacy_signal_accounts(cls, root: Path) -> list[str]:
        """The system accounts of the old Signal bridges, by their state folders under `/var/lib`.

        Args:
            root: The file system's root (a scratch folder in the tests).

        Returns:
            list[str]: `mightling-signal` and every `mightling-signal-<name>`, sorted; empty when none.
        """
        try:
            names = [path.name for path in (root / SIGNAL_STATE_ROOT).iterdir() if path.is_dir()]
        except OSError:
            return []
        prefix = f"{LEGACY_SIGNAL_ACCOUNT}-"
        return sorted(
            name
            for name in names
            if name == LEGACY_SIGNAL_ACCOUNT
            or (
                name.startswith(prefix)
                and re.fullmatch(r"[a-z][a-z0-9-]{0,11}", name[len(prefix) :])
            )
        )

    @classmethod
    def system_unit_enabled(cls, unit: str) -> bool:
        """Whether a system unit is enabled (`systemctl is-enabled` needs no root).

        Args:
            unit: The unit's name.

        Returns:
            bool: True when systemctl says it is enabled; False when it is not or cannot be asked.
        """
        try:
            found = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", unit],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return found.returncode == 0

    @classmethod
    def signal_line(cls, root: Path) -> str | None:
        """The commands that move the old Signal bridges to their new names, run once by the owner.

        As root: stop the old units, rename each account and its group (the uid stays, so the Signal
        keys and the device link in its state folder survive), move its state folder to the new name,
        move the shared runtime (`/opt`) so it is not fetched again, and remove the old bridge binary
        and unit files. Then, as the owner, `ragon signal setup --refresh` installs the bridge under its
        new name, points each bridge's settings at the moved runtime and writes the new units, and
        `ragon signal start` starts each bridge that was on.

        Args:
            root: The file system's root (a scratch folder in the tests).

        Returns:
            str | None: The line, or None when no old bridge is here.
        """
        accounts = cls.legacy_signal_accounts(root)
        unit, template = root / LEGACY_SIGNAL_UNIT, root / LEGACY_SIGNAL_TEMPLATE
        if not accounts and not unit.exists() and not template.exists():
            return None
        units = {
            account: (
                unit.name
                if account == LEGACY_SIGNAL_ACCOUNT
                else f"{LEGACY_SIGNAL_ACCOUNT}@{account[len(LEGACY_SIGNAL_ACCOUNT) + 1 :]}.service"
            )
            for account in accounts
        }
        steps = [f"systemctl disable --now {' '.join(units.values() or [unit.name])}"]
        state = f"/{SIGNAL_STATE_ROOT}"
        for account in accounts:
            new = SIGNAL_ACCOUNT + account[len(LEGACY_SIGNAL_ACCOUNT) :]
            steps += [
                f"usermod -l {new} {account}",
                f"groupmod -n {new} {account}",
                f"[ -e {state}/{new} ] || mv {state}/{account} {state}/{new}",
            ]
        runtime, new_runtime = f"/{LEGACY_SIGNAL_RUNTIME}", f"/{SIGNAL_RUNTIME}"
        bridge = f"/{LEGACY_SIGNAL_BRIDGE}"
        steps += [
            f"[ -e {new_runtime} ] || [ ! -d {runtime} ] || mv {runtime} {new_runtime}",
            f"rm -f {bridge}",
            f"rmdir {Path(bridge).parent} 2>/dev/null",
            f"rm -f /{LEGACY_SIGNAL_UNIT} /{LEGACY_SIGNAL_TEMPLATE}",
            "systemctl daemon-reload",
        ]
        line = f"sudo sh -c '{'; '.join(steps)}'; ragon signal setup --refresh"
        several = len(accounts) > 1
        for account, name in units.items():
            if cls.system_unit_enabled(name):
                instance = account[len(LEGACY_SIGNAL_ACCOUNT) + 1 :] or "default"
                line += "; ragon signal start" + (f" --name {instance}" if several else "")
        return line

    @classmethod
    def privileged_line(cls, root: Path = Path("/")) -> str | None:
        """The line the owner runs for the root-owned leftovers (each root part says `sudo`).

        Args:
            root: The file system's root (a scratch folder in the tests).

        Returns:
            str | None: The commands (AppArmor profiles, the Avahi file, the Signal bridges), or None
            when there are none.
        """
        old = [root / "etc/apparmor.d" / name for name in ("puffin-bwrap", "mightling-bwrap")]
        lines = ["sudo ragon-admin host setup"] if any(path.exists() for path in old) else []
        signal = cls.signal_line(root)
        if signal:
            lines.append(signal)
        return "; ".join(lines) if lines else None

    @classmethod
    def new_unit_name(cls, name: str, own_steps: bool = False) -> str | None:
        """The new name of an old user unit.

        Args:
            name: A unit's name.
            own_steps: Whether a unit with a step of its own (Night Shift, the forge) is renamed too, as
                it is where another unit names it; the generic step leaves those files to their steps.

        Returns:
            str | None: `ragon-…` (or `ragon.slice`), or None for a name that is not an old unit's (or
            that has a step of its own, unless `own_steps`).
        """
        if not name.endswith(UNIT_SUFFIXES) or (
            name.startswith(UNITS_WITH_OWN_STEPS) and not own_steps
        ):
            return None
        if name in LEGACY_SLICES:
            return "ragon.slice"
        for prefix in LEGACY_UNIT_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                return "ragon-" + name[len(prefix) :]
        return None

    @classmethod
    def rewrite_unit_word(cls, word: str) -> str:
        """Renames one word of a unit file; a word with no old name in it is kept.

        The old names are a unit's, a program's path, a path into the old home, an old variable's
        and the product's.

        Args:
            word: The text between spaces and `=` signs.

        Returns:
            str: The word as the new version writes it.
        """
        if word in LEGACY_PRODUCT_WORDS:
            return PRODUCT_WORD
        if word.startswith(LEGACY_ENV_PREFIX):
            return ENV_PREFIX + word[len(LEGACY_ENV_PREFIX) :]
        renamed = cls.new_unit_name(word, own_steps=True) or cls.new_program_path(word)
        if renamed:
            return renamed
        if word.endswith("/.mightling"):
            word += "/"
            return word.replace("/.mightling/", "/.ragon/")[:-1]
        return word.replace("/.mightling/", "/.ragon/")

    @classmethod
    def new_program_path(cls, word: str) -> str | None:
        """The new path of a program an old unit runs.

        Args:
            word: A word of a unit file.

        Returns:
            str | None: The path in the new install folder or the new link, or None when the word is
            not an old program's path.
        """
        folder, slash, program = word.rpartition("/")
        folder += slash
        for legacy in LEGACY_UNIT_BIN_DIRS:
            if folder.endswith(legacy):
                return folder[: -len(legacy)] + UNIT_BIN_DIR + UNIT_PROGRAMS.get(program, program)
        if folder.endswith(LINK_DIR_SUFFIX) and program in UNIT_PROGRAMS:
            return folder + UNIT_PROGRAMS[program]
        return None

    @classmethod
    def rewrite_unit_text(cls, text: str) -> str:
        """Rewrites an old unit file's text for its new name, word by word (`rename.rs` does the same).

        Only names the migration moves are changed: a name it keeps (the relay box's tunnel account in
        an `ssh` command, an overlay peer's DNS name) does not end like a unit and is left as it was.

        Args:
            text: The old unit file's text.

        Returns:
            str: The text of the unit under its new name.
        """
        return "\n".join(
            " ".join(
                "=".join(cls.rewrite_unit_word(part) for part in word.split("="))
                for word in line.split(" ")
            )
            for line in text.split("\n")
        )

    @classmethod
    def user_unit_dir(cls) -> Path:
        """Returns: Path: `~/.config/systemd/user`, computed at call time (tests move `HOME`)."""
        return Path("~/.config/systemd/user").expanduser()

    @classmethod
    def old_user_units(cls) -> list[tuple[str, str]]:
        """The old user unit files the generic step moves.

        Returns:
            list[tuple[str, str]]: (old name, new name) of each, sorted.
        """
        directory = cls.user_unit_dir()
        try:
            names = sorted(path.name for path in directory.iterdir() if path.is_file())
        except OSError:
            return []
        return [(name, new) for name in names if (new := cls.new_unit_name(name))]

    @classmethod
    def user_systemctl(cls, *args: str) -> bool:
        """Runs `systemctl --user` with `args`.

        Args:
            *args: The arguments.

        Returns:
            bool: Whether it succeeded; False when systemctl could not be run.
        """
        try:
            done = subprocess.run(
                ["systemctl", "--user", *args], capture_output=True, check=False, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0

    @classmethod
    def move_user_units(cls) -> tuple[list[str], list[str]]:
        """Stops the old user units and moves them to their new names (A2), before anything moves.

        Each unit file is written again under its new name with its text rewritten (the programs it
        runs, the units it names), and the links `systemctl enable` made follow it, so an enabled unit
        stays enabled. A unit already written under its new name is kept as it is. The units that ran
        are started by `start_user_units`, after the programs have moved.

        Returns:
            tuple[list[str], list[str]]: The old names moved, and the new names to start.
        """
        directory = cls.user_unit_dir()
        units = cls.old_user_units()
        if not units:
            return [], []
        running = [old for old, _new in units if cls.user_systemctl("is-active", "--quiet", old)]
        if running:
            cls.user_systemctl("stop", *running)
        moved, to_start = [], []
        for old, new in units:
            if not (directory / new).exists():
                (directory / new).write_text(cls.rewrite_unit_text((directory / old).read_text()))
            (directory / old).unlink()
            cls.move_enable_links(directory, old, new)
            moved.append(old)
            if old in running:
                to_start.append(new)
        cls.user_systemctl("daemon-reload")
        return moved, to_start

    @classmethod
    def move_enable_links(cls, directory: Path, old: str, new: str) -> None:
        """Moves the links `systemctl enable` made for an old unit (`*.wants/`, `*.requires/`) to the new one.

        Args:
            directory: The user unit folder.
            old: The old unit's name.
            new: The new unit's name.
        """
        for folder in directory.iterdir():
            if folder.suffix not in (".wants", ".requires") or not (folder / old).is_symlink():
                continue
            (folder / old).unlink()
            if not (folder / new).is_symlink():
                (folder / new).symlink_to(directory / new)

    @classmethod
    def start_user_units(cls, moved: list[str], to_start: list[str]) -> str | None:
        """Starts again, under their new names, the old units that ran.

        Args:
            moved: The old names `move_user_units` moved.
            to_start: The new names of those that ran.

        Returns:
            str | None: A line when units were moved.
        """
        if not moved:
            return None
        if to_start:
            cls.user_systemctl("start", *to_start)
        started = f"; started {', '.join(to_start)} again" if to_start else ""
        return f"moved the user units {', '.join(moved)} to their new names{started}"

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
        """Replaces the old command links in `~/.local/bin` with the new ones.

        Only links are removed (a real file is someone else's). A new link is made wherever it is missing
        and its target exists: the binaries of the install folder whose old name had a link (never
        `ragon-signal`), and `ragon-admin` beside this interpreter. `ragon` makes the same links, so either
        may run first.

        Returns:
            Optional[str]: A line when a link was removed or made.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR

        links = Path("~/.local/bin").expanduser()
        removed = [name for name in LEGACY_LINKS if (links / name).is_symlink()]
        for name in removed:
            (links / name).unlink()
        targets: dict[str, Path] = {}
        for old, new in LEGACY_BINARIES:
            if old in LEGACY_LINKS:
                targets.setdefault(new, Path(INSTALL_DIR) / "bin" / new)
        targets["ragon-admin"] = Path(sys.executable).parent / "ragon-admin"
        linked = []
        for name, target in targets.items():
            path = links / name
            if target.is_file() and not path.is_symlink() and not path.exists():
                links.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target)
                linked.append(name)
        parts = [f"removed the links {', '.join(removed)}"] if removed else []
        parts += [f"linked {', '.join(linked)}"] if linked else []
        return f"{'; '.join(parts)} in {links}" if parts else None

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
            if name == LEGACY_ENGINE_CONTAINER
            or name.startswith(f"{LEGACY_ENGINE_CONTAINER}-scan-")
        ]
        removed = [
            name for name in names if ForgeContainer.docker(["rm", "-f", name]).returncode == 0
        ]
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

        units = sorted(
            path for path in ForgeService.unit_dir().glob(f"{LEGACY_FORGE_NAME}*") if path.is_file()
        )
        return units, ForgeSettings.data_dir() / LEGACY_FORGE_STATE

    @classmethod
    def migrate_forge(cls) -> str | None:
        """Moves the forge to its new names: units, container, network, state folder and phone account.

        The forge's data (repositories, database) is host data and stays where it is. The old
        container and network are removed and the new ones are made when the units start again,
        if the forge was enabled. A new state folder that already exists is kept and the old one is
        moved aside, so the next command finds nothing to do. The forge starts before its timers:
        the ten-minute tick would otherwise make a new phone account before the old one is renamed.

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
        new_state = ForgeSettings.data_dir() / FORGE_STATE
        aside = ""
        if old_state.is_dir():
            if not new_state.exists():
                shutil.move(str(old_state), str(new_state))
            else:
                spare = cls.free_name(old_state.with_name(FORGE_STATE_ASIDE))
                shutil.move(str(old_state), str(spare))
                aside = f"; {new_state} was there already, so {old_state} is kept as {spare}"
        if not enabled:
            ForgeService.systemctl("daemon-reload")
            return f"moved the forge to its new names (it was off){aside}"
        ForgeService.install_units()
        ForgeService.systemctl("enable", "--now", UNIT)
        renamed = cls.rename_phone_account()
        ForgeService.systemctl("enable", "--now", *TIMERS)
        return "moved the forge to its new names" + aside + (f"; {renamed}" if renamed else "")

    @staticmethod
    def free_name(path: Path) -> Path:
        """Returns `path`, or `path.2`, `path.3`… when it is taken.

        Args:
            path: The name wanted.

        Returns:
            Path: The first of them that does not exist.
        """
        candidate, number = path, 1
        while candidate.exists() or candidate.is_symlink():
            number += 1
            candidate = path.with_name(f"{path.name}.{number}")
        return candidate

    @classmethod
    def rename_phone_account(cls) -> str | None:
        """Renames the forge's phone account through Forgejo's admin API (its token survives).

        Waits for the forge to answer after its start, then asks for the old account until the
        answer is that it exists or that it does not: an error while Forgejo starts is not taken
        for either.

        Returns:
            str | None: A line when the account was renamed, or why it was not; None when there is
            no old account (or no admin token to ask with).
        """
        from dreamference.forge.forge_accounts import ForgeAccounts
        from dreamference.forge.forge_container import ForgeContainer
        from dreamference.forge.forge_phone import PHONE_ACCOUNT

        admin = ForgeAccounts.admin_token()
        if not admin:
            return None
        ForgeContainer.wait_until_answering(FORGE_ANSWER_WAIT_S)
        status = 0
        for attempt in range(FORGE_RENAME_TRIES):
            if attempt:
                time.sleep(FORGE_RENAME_WAIT_S)
            status, _body = ForgeContainer.api(
                "GET", f"/users/{LEGACY_PHONE_ACCOUNT}", admin, timeout=10
            )
            if status in (HTTPStatus.OK, HTTPStatus.NOT_FOUND):
                break
        if status == HTTPStatus.NOT_FOUND:
            return None
        if status != HTTPStatus.OK:
            return (
                f"could not rename the forge account {LEGACY_PHONE_ACCOUNT} to {PHONE_ACCOUNT}: "
                f"the forge did not answer (last answer {status}); its ten-minute timer makes a new "
                f"{PHONE_ACCOUNT}, and {LEGACY_PHONE_ACCOUNT} can then be removed in the forge's "
                "site administration"
            )
        status, _body = ForgeContainer.api(
            "POST",
            f"/admin/users/{LEGACY_PHONE_ACCOUNT}/rename",
            admin,
            {"new_username": PHONE_ACCOUNT},
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
