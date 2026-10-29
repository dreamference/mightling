"""Nightly backups of the forge of record, their restore, and the restore check.

The spec: specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §6.

A backup set is a folder `forge-<UTC time>` holding Forgejo's own dump (`forgejo dump`: the
database as SQL, the data folder, the repositories), a manifest of every repository's refs, the
forge's secrets (0600: the accounts' passwords and tokens, the repository settings) and a
`git bundle --all` of each repository. The forge of record keeps only the latest set; it sends
each set over the pairing (SSH, the forced command's `forge-backup-receive`) to two other nodes,
which keep seven daily and four weekly sets, bundles in the latest only. Private repositories are
in the dump: that is the only way they leave the forge of record, and only to the owner's nodes.

A restore rebuilds the SQLite database from the dump's SQL, puts the data folder and the
repositories in place, keeps the secrets, and compares every ref with the manifest. It never
deletes the data folder it replaces: that is moved aside.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import IO, Any, Final

from dreamference.forge.forge_anonymity import DENYLIST, ForgeAnonymity
from dreamference.forge.forge_app_ini import APP_INI_RELATIVE, DATABASE_RELATIVE, ForgeAppIni
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_settings import (
    BACKUP_COPIES,
    DAILY_KEPT,
    ROLE_RECORD,
    WEEKLY_KEPT,
    ForgeSettings,
)
from dreamference.forge.forge_storage import ForgeStorage

SET_NAME: Final[re.Pattern] = re.compile(r"forge-\d{8}-\d{6}")
SECRET_FILES: Final[tuple] = (
    "owner-password",
    "admin-password",
    "admin-token",
    "mirror-token",
    "repositories.json",
    "github-mirror.json",
)
STATUS_NAME: Final[str] = "backup-status.json"
DENYLIST_KEY: Final[str] = "anonymity-denylist"
BUNDLE_LIMIT_BYTES: Final[int] = 200 * 1000**2
UNISTR_ESCAPE: Final[re.Pattern] = re.compile(
    r"\\\\|\\(?:u?([0-9A-Fa-f]{4})|\+([0-9A-Fa-f]{6})|U([0-9A-Fa-f]{8}))"
)
# `unistr()`'s escaped backslash, which decodes to one backslash.
ESCAPED_BACKSLASH: Final[str] = "\\\\"
# `git for-each-ref --format="%(refname) %(objectname)"` separates the two with a space.
REF_SEPARATOR: Final[str] = " "


class ForgeBackup:
    """Takes, sends, keeps, restores and checks backup sets."""

    @classmethod
    def local_dir(cls) -> Path:
        """Return the folder where the forge of record keeps its latest set.

        Returns:
            Path: Where the forge of record keeps its latest set.
        """
        return ForgeSettings.share_dir() / "dumps"

    @classmethod
    def received_dir(cls) -> Path:
        """Return the folder where a node keeps the sets other nodes sent.

        Returns:
            Path: Where a node keeps the sets other nodes sent (0700).
        """
        return ForgeSettings.share_dir() / "backups"

    @classmethod
    def repository_refs(cls, root: Path) -> dict[str, dict[str, str]]:
        """Read every ref of every repository in a repositories folder.

        Args:
            root: A repositories folder (`<owner>/<name>.git` below it).

        Returns:
            dict[str, dict[str, str]]: `owner/name` → ref → object.
        """
        found: dict[str, dict[str, str]] = {}
        for repository in sorted(root.glob("*/*.git")) if root.is_dir() else []:
            result = subprocess.run(
                ["git", "-C", str(repository), "for-each-ref", "--format=%(refname) %(objectname)"],
                capture_output=True,
                text=True,
                check=False,
            )
            refs = dict(
                line.split(REF_SEPARATOR, 1)
                for line in result.stdout.splitlines()
                if REF_SEPARATOR in line
            )
            found[f"{repository.parent.name}/{repository.name.removesuffix('.git')}"] = refs
        return found

    @classmethod
    def take(cls) -> Path | None:
        """Take a backup set on this node's forge.

        Returns:
            Path | None: The set's folder, or None when the dump failed.
        """
        name = time.strftime("forge-%Y%m%d-%H%M%S", time.gmtime())
        data = ForgeSettings.data_dir()
        (data / "dumps").mkdir(parents=True, exist_ok=True)
        result = ForgeContainer.forgejo(
            [
                "dump",
                "--quiet",
                "--type",
                "tar.gz",
                "--skip-log",
                "--skip-index",
                "--file",
                f"/var/lib/gitea/dumps/{name}.tar.gz",
            ],
            timeout=3600,
        )
        produced = data / "dumps" / f"{name}.tar.gz"
        if result.returncode != 0 or not produced.is_file():
            print(f"❌ forgejo dump failed: {(result.stderr or result.stdout).strip()[-300:]}")
            return None
        folder = cls.local_dir() / name
        (folder / "bundles").mkdir(parents=True, exist_ok=True)
        Path(cls.local_dir()).chmod(0o700)
        shutil.move(str(produced), folder / "dump.tar.gz")
        refs = cls.repository_refs(data / "git" / "repositories")
        cls.snapshot_database(data / DATABASE_RELATIVE, folder / "forgejo.db")
        manifest = {"set": name, "node": cls.node_id(), "taken": int(time.time()), "refs": refs}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
        secrets = {key: ForgeSettings.read_secret(key) for key in SECRET_FILES}
        # The push hook's denylist goes with the role, so a moved forge checks what this one did.
        secrets[DENYLIST_KEY] = ForgeAnonymity.read_denylist()
        descriptor = os.open(folder / "secrets.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({key: value for key, value in secrets.items() if value}, handle)
        for full_name, repository_refs in refs.items():
            owner, repo = full_name.split("/", 1)
            # The dump holds every repository already; a bundle is the copy any git can read,
            # kept for the small ones (a fork with upstream's whole history would double the set).
            if (
                repository_refs
                and ForgeStorage.size(data / "git" / "repositories" / owner / f"{repo}.git")
                < BUNDLE_LIMIT_BYTES
            ):
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(data / "git" / "repositories" / owner / f"{repo}.git"),
                        "bundle",
                        "create",
                        "--quiet",
                        str(folder / "bundles" / f"{owner}__{repo}.bundle"),
                        "--all",
                    ],
                    capture_output=True,
                    check=False,
                )
        for older in cls.local_dir().iterdir():
            if older.is_dir() and older.name != name and SET_NAME.fullmatch(older.name):
                shutil.rmtree(older, ignore_errors=True)
        return folder

    @classmethod
    def node_id(cls) -> str:
        """Return this node's id, made when it has none.

        Returns:
            str: This node's id.
        """
        from dreamference.node.node_identity import NodeIdentity

        return NodeIdentity.ensure()

    @classmethod
    def send(cls, folder: Path, record: dict[str, Any]) -> bool:
        """Stream a set to a paired node.

        Args:
            folder: The set.
            record: The paired node's record.

        Returns:
            bool: Whether the node stored it.
        """
        from dreamference.node.node_pairing import NodePairing

        try:
            command = NodePairing.ssh_command(record, f"forge-backup-receive {folder.name}")
        except LookupError as error:
            print(f"⚠️  {error}")
            return False
        packer = subprocess.Popen(
            ["tar", "-C", str(folder.parent), "-cf", "-", folder.name], stdout=subprocess.PIPE
        )
        try:
            result = subprocess.run(
                command,
                stdin=packer.stdout,
                capture_output=True,
                text=True,
                timeout=7200,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # ssh is killed by now; the set counts as not sent, so the nightly status says so.
            print(f"⚠️  {record.get('name')} did not store the backup in time.")
            return False
        finally:
            if packer.stdout:
                packer.stdout.close()
            packer.wait()
        if result.returncode != 0:
            print(
                f"⚠️  {record.get('name')} did not store the backup: {(result.stderr or '').strip()[-300:]}"
            )
        return result.returncode == 0 and packer.returncode == 0

    @classmethod
    def run(cls) -> int:
        """Run the nightly backup: take a set and send it to two other nodes.

        Returns:
            int: 0 when the set reached two nodes, 1 otherwise.
        """
        from dreamference.node.node_pairing import NodePairing

        if ForgeSettings.role() != ROLE_RECORD:
            print("💡 Only the forge of record takes backups; this node's forge is a mirror.")
            return 0
        folder = cls.take()
        if folder is None:
            cls.write_status({"ok": False, "error": "the dump failed"})
            return 1
        sent: list[str] = []
        for record in NodePairing.paired():
            if len(sent) >= BACKUP_COPIES:
                break
            if cls.send(folder, record):
                sent.append(record.get("name") or record["node"])
        ok = len(sent) >= BACKUP_COPIES
        cls.write_status(
            {
                "ok": ok,
                "set": folder.name,
                "sent_to": sent,
                "error": "" if ok else f"reached {len(sent)} of {BACKUP_COPIES} nodes",
            }
        )
        print(
            f"{'✅' if ok else '⚠️ '} Backup {folder.name} kept here and sent to {', '.join(sent) or 'no node'}"
            f"{'' if ok else f' (wanted {BACKUP_COPIES})'}."
        )
        return 0 if ok else 1

    @classmethod
    def write_status(cls, status: dict[str, Any]) -> None:
        """Record the last backup's outcome, with the time, in the secrets folder.

        Args:
            status: The last backup's outcome.
        """
        ForgeSettings.write_secret(STATUS_NAME, json.dumps({"at": int(time.time()), **status}))

    @classmethod
    def status(cls) -> dict[str, Any] | None:
        """Read the last backup's outcome.

        Returns:
            dict[str, Any] | None: The last backup's outcome, or None.
        """
        try:
            return json.loads(ForgeSettings.secret_path(STATUS_NAME).read_text())
        except (OSError, ValueError):
            return None

    @classmethod
    def receive(cls, name: str, stream: IO[bytes]) -> int:
        """Store a set another node sends (the forced command's `forge-backup-receive`).

        Args:
            name: The set's name.
            stream: The tar stream (binary standard input).

        Returns:
            int: 0 when stored, 2 when refused.
        """
        if not SET_NAME.fullmatch(name):
            print("refused: not a backup set's name", flush=True)
            return 2
        root = cls.received_dir()
        root.mkdir(parents=True, exist_ok=True)
        Path(root).chmod(0o700)
        staging = Path(tempfile.mkdtemp(prefix=".receiving-", dir=root))
        try:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                archive.extractall(staging, filter="data")
            if not (staging / name / "manifest.json").is_file():
                print("refused: the stream held no backup set", flush=True)
                return 2
            target = root / name
            if target.exists():
                shutil.rmtree(target)
            Path(staging / name).replace(target)
        except (tarfile.TarError, OSError) as error:
            print(f"refused: {error}", flush=True)
            return 2
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        cls.keep(root)
        return 0

    @classmethod
    def kept(cls, names: list[str]) -> list[str]:
        """Choose which received sets the retention keeps.

        Args:
            names: Set names (`forge-YYYYmmdd-HHMMSS`).

        Returns:
            list[str]: The sets to keep: the newest of each of the last seven days, then the
            newest of each of the four weeks before.
        """
        newest_per_day: dict[str, str] = {}
        for name in sorted(names, reverse=True):
            newest_per_day.setdefault(name[6:14], name)
        days = sorted(newest_per_day.values(), reverse=True)
        keep = days[:DAILY_KEPT]
        weeks: dict[str, str] = {}
        for name in days[DAILY_KEPT:]:
            stamp = time.strptime(name[6:14], "%Y%m%d")
            week = time.strftime("%G-%V", stamp)
            weeks.setdefault(week, name)
        return keep + sorted(weeks.values(), reverse=True)[:WEEKLY_KEPT]

    @classmethod
    def keep(cls, root: Path) -> None:
        """Apply the retention to the received sets, and keep bundles in the newest only.

        Args:
            root: The received sets' folder.
        """
        names = sorted(
            item.name for item in root.iterdir() if item.is_dir() and SET_NAME.fullmatch(item.name)
        )
        keep = set(cls.kept(names))
        newest = max(names) if names else None
        for name in names:
            if name not in keep:
                shutil.rmtree(root / name, ignore_errors=True)
            elif name != newest:
                shutil.rmtree(root / name / "bundles", ignore_errors=True)

    @classmethod
    def latest(cls) -> Path | None:
        """Find the newest set this node holds, received or its own.

        Returns:
            Path | None: The newest set this node holds, received or its own.
        """
        sets = [
            item
            for root in (cls.received_dir(), cls.local_dir())
            if root.is_dir()
            for item in root.iterdir()
            if item.is_dir() and SET_NAME.fullmatch(item.name)
        ]
        return max(sets, key=lambda item: item.name) if sets else None

    @classmethod
    def unpack(cls, folder: Path, target: Path) -> dict[str, Any]:
        """Build a data folder from a backup set.

        The dump's data and repositories go in place, the database is rebuilt from its SQL, and
        the dump's `app.ini` (and so its secrets) is kept.

        Args:
            folder: The set.
            target: An empty folder to build into.

        Returns:
            dict[str, Any]: The set's manifest.

        Raises:
            RuntimeError: The set is incomplete or the database could not be rebuilt.
        """
        manifest = json.loads((folder / "manifest.json").read_text())
        # A received set is another node's word: its refs are compared after the data is replaced.
        refs = manifest.get("refs") if isinstance(manifest, dict) else None
        if not isinstance(refs, dict) or not all(isinstance(r, dict) for r in refs.values()):
            raise RuntimeError("the set's manifest lists no refs")
        target.parent.mkdir(parents=True, exist_ok=True)
        extract = Path(tempfile.mkdtemp(prefix="forge-restore-", dir=target.parent))
        try:
            with tarfile.open(folder / "dump.tar.gz", "r:gz") as archive:
                archive.extractall(extract, filter="data")
            sql = extract / "forgejo-db.sql"
            if not sql.is_file():
                raise RuntimeError("the dump holds no database")
            target.mkdir(parents=True, exist_ok=True)
            if (extract / "data").is_dir():
                shutil.move(str(extract / "data"), target / "data")
            for stale in (target / "data").glob("forgejo.db*"):
                stale.unlink()
            (target / "git").mkdir(exist_ok=True)
            if (extract / "repos").is_dir():
                shutil.move(str(extract / "repos"), target / "git" / "repositories")
            else:
                (target / "git" / "repositories").mkdir()
            ini = extract / APP_INI_RELATIVE
            ini = ini if ini.is_file() else extract / "app.ini"
            (target / APP_INI_RELATIVE).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ini, target / APP_INI_RELATIVE)
            database = target / DATABASE_RELATIVE
            database.parent.mkdir(parents=True, exist_ok=True)
            if (folder / "forgejo.db").is_file():
                shutil.copy2(folder / "forgejo.db", database)
            else:
                cls.rebuild_database(sql, database)
        finally:
            shutil.rmtree(extract, ignore_errors=True)
        return manifest

    @classmethod
    def unistr(cls, text: str | None) -> str | None:
        r"""Decode text as SQLite's `unistr()` (3.50) does.

        The dump's SQL calls it for escaped characters, and older SQLite libraries lack it.

        Args:
            text: The escaped text.

        Returns:
            str | None: The text with `\XXXX`, `\uXXXX`, `\+XXXXXX`, `\UXXXXXXXX` and `\\` decoded.
        """
        if text is None:
            return None
        return UNISTR_ESCAPE.sub(
            lambda match: (
                "\\"
                if match.group(0) == ESCAPED_BACKSLASH
                else chr(int(match.group(1) or match.group(2) or match.group(3), 16))
            ),
            text,
        )

    @classmethod
    def rebuild_database(cls, sql: Path, database: Path) -> None:
        """Rebuild the SQLite database from the dump's SQL.

        Args:
            sql: The dump's `forgejo-db.sql`.
            database: The SQLite file to create.

        Raises:
            RuntimeError: The SQL did not run.
        """
        connection = sqlite3.connect(database)
        connection.create_function("unistr", 1, cls.unistr, deterministic=True)
        try:
            connection.executescript(sql.read_text(encoding="utf-8"))
            connection.commit()
        except sqlite3.Error as error:
            raise RuntimeError(f"the database could not be rebuilt: {error}") from error
        finally:
            connection.close()

    @classmethod
    def snapshot_database(cls, source: Path, target: Path) -> bool:
        """Copy the live database consistently.

        SQLite's backup API reads a snapshot while Forgejo keeps writing in WAL mode.

        Args:
            source: The live database.
            target: The copy.

        Returns:
            bool: Whether the copy was made.
        """
        try:
            with (
                sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live,
                sqlite3.connect(target) as copy,
            ):
                live.backup(copy)
        except sqlite3.Error:
            target.unlink(missing_ok=True)
            return False
        return True

    @classmethod
    def compare(cls, manifest: dict[str, Any], repositories: Path) -> list[str]:
        """Compare the refs in a repositories folder with a set's manifest.

        Args:
            manifest: A set's manifest.
            repositories: A repositories folder.

        Returns:
            list[str]: Every difference (`owner/name: ref`), empty when every ref matches.
        """
        actual = cls.repository_refs(repositories)
        differences = []
        for full_name, refs in manifest.get("refs", {}).items():
            have = actual.get(full_name, {})
            differences += [
                f"{full_name}: {ref}" for ref, sha in refs.items() if have.get(ref) != sha
            ]
            differences += [f"{full_name}: {ref} (extra)" for ref in have if ref not in refs]
        return differences

    @classmethod
    def check(cls, folder: Path | None = None) -> int:
        """Check that a set restores (`forge restore --check`), touching nothing running.

        The set is restored into a scratch folder, its database rebuilt, and every ref compared
        with the manifest.

        Args:
            folder: The set; the newest this node holds when None.

        Returns:
            int: 0 when the set restores with every ref, 1 otherwise.
        """
        folder = folder or cls.latest()
        if folder is None:
            print("❌ This node holds no backup set.")
            return 1
        ForgeSettings.share_dir().mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="forge-check-", dir=ForgeSettings.share_dir()))
        try:
            manifest = cls.unpack(folder, scratch / "data")
            differences = cls.compare(manifest, scratch / "data" / "git" / "repositories")
        except (RuntimeError, OSError, ValueError, tarfile.TarError) as error:
            print(f"❌ {folder.name} does not restore: {error}")
            return 1
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if differences:
            print(
                f"❌ {folder.name} restores with {len(differences)} refs different: {', '.join(differences[:10])}"
            )
            return 1
        print(
            f"✅ {folder.name} restores: {sum(len(r) for r in manifest['refs'].values())} refs in "
            f"{len(manifest['refs'])} repositories match."
        )
        return 0

    @classmethod
    def restore_secrets(cls, folder: Path) -> None:
        """Write the set's secrets as this node's (a restore makes this node the set's forge).

        Args:
            folder: The set.
        """
        try:
            secrets = json.loads((folder / "secrets.json").read_text())
        except (OSError, ValueError):
            return
        for key in SECRET_FILES:
            if secrets.get(key):
                ForgeSettings.write_secret(key, secrets[key])
        denylist = Path(DENYLIST).expanduser()
        if secrets.get(DENYLIST_KEY) and not denylist.exists():
            denylist.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(denylist, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(secrets[DENYLIST_KEY])

    @classmethod
    def install_data(cls, folder: Path) -> tuple[dict[str, Any] | None, Path | None]:
        """Replace this node's data folder with the set's, moving the current one aside.

        Args:
            folder: The set.

        Returns:
            tuple[dict[str, Any] | None, Path | None]: The manifest (None on failure) and
            where the previous data folder was moved.
        """
        data = ForgeSettings.data_dir()
        building = data.with_name(f"data.restoring-{int(time.time())}")
        try:
            manifest = cls.unpack(folder, building)
        except (RuntimeError, OSError, ValueError, tarfile.TarError) as error:
            print(f"❌ {folder.name} does not restore: {error}")
            shutil.rmtree(building, ignore_errors=True)
            return None, None
        aside = None
        if data.exists():
            aside = data.with_name(f"data.before-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}")
            Path(data).replace(aside)
        Path(building).replace(data)
        cls.restore_secrets(folder)
        ForgeAppIni.write(data, cls.host_name(), ROLE_RECORD)
        return manifest, aside

    @classmethod
    def host_name(cls) -> str:
        """Return this machine's short host name.

        Returns:
            str: This machine's name, as clients reach it (`<name>.local`).
        """
        import socket

        return socket.gethostname().split(".")[0]
