"""The installed set of adapters (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §6.3).

`~/.local/share/dreamference/adapters/installed.json`, mode 0600, written by `ling-admin` only and
atomically (a temporary file and a rename). Each entry records where the adapter came from (repo,
commit, the manifest's sha256), where its files are (its snapshot in the Hugging Face hub cache,
relative to the hub so the engine's container finds it at the same place under its own mount),
its trust and what it was checked against. Which adapters a person installed is the user's choice,
so it lives under the home, never in a repository.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

INSTALLED_FILE: Final[str] = "installed.json"
LOCK_FILE: Final[str] = ".installed.json.lock"
FILE_MODE: Final[int] = 0o600
TRUST_DREAMFERENCE: Final[str] = "dreamference"
TRUST_THIRD_PARTY: Final[str] = "third-party"


class AdapterStore:
    """Reads and writes the installed set."""

    @classmethod
    def folder(cls) -> Path:
        """The adapters' own folder (the files themselves are in the hub cache).

        Returns:
            Path: `~/.local/share/dreamference/adapters`, under the current home.
        """
        return Path("~/.local/share/dreamference/adapters").expanduser()

    @classmethod
    def path(cls) -> Path:
        """The installed set's file.

        Returns:
            Path: `installed.json` in `folder()`.
        """
        return cls.folder() / INSTALLED_FILE

    @classmethod
    def entries(cls) -> dict[str, dict[str, object]]:
        """Every installed adapter.

        Returns:
            dict[str, dict[str, object]]: Name to entry; empty when nothing is installed or the
            file cannot be read.
        """
        try:
            record = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        adapters = record.get("adapters") if isinstance(record, dict) else None
        if not isinstance(adapters, dict):
            return {}
        return {name: entry for name, entry in adapters.items() if isinstance(entry, dict)}

    @classmethod
    def get(cls, name: str) -> dict[str, object] | None:
        """One installed adapter.

        Args:
            name: Its name.

        Returns:
            dict[str, object] | None: Its entry, or None when it is not installed.
        """
        return cls.entries().get(name)

    @classmethod
    def put(cls, entry: dict[str, object]) -> None:
        """Records an adapter (replacing an entry of the same name).

        Args:
            entry: The entry; its `name` is the key.
        """
        with cls._locked():
            entries = cls._entries_to_change()
            entries[str(entry["name"])] = entry
            cls._write(entries)

    @classmethod
    def drop(cls, name: str) -> bool:
        """Removes an adapter's entry.

        Args:
            name: Its name.

        Returns:
            bool: Whether there was one.
        """
        with cls._locked():
            entries = cls._entries_to_change()
            if entries.pop(name, None) is None:
                return False
            cls._write(entries)
        return True

    @classmethod
    @contextmanager
    def _locked(cls) -> Iterator[None]:
        """Holds the installed set's lock while a change reads, edits and rewrites the file.

        An install and a node's adapter sync may run at once; unlocked, the later write dropped
        the other's entry.

        Yields:
            None: While the lock is held.
        """
        import fcntl

        folder = cls.folder()
        folder.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(folder / LOCK_FILE, os.O_WRONLY | os.O_CREAT, FILE_MODE)
        with os.fdopen(descriptor, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @classmethod
    def _entries_to_change(cls) -> dict[str, dict[str, object]]:
        """The installed set about to be rewritten, refusing a file that cannot be read.

        `entries()` reads such a file as empty, and rewriting that would forget every adapter it
        records.

        Returns:
            dict[str, dict[str, object]]: Name to entry; empty when there is no file yet.

        Raises:
            ValueError: When the file exists but is not an installed set.
        """
        path = cls.path()
        if not path.exists():
            return {}
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(
                f"{path} could not be read, so it was not changed ({error})"
            ) from error
        if not isinstance(record, dict) or not isinstance(record.get("adapters"), dict):
            raise ValueError(f"{path} is not an installed set, so it was not changed")
        return cls.entries()

    @classmethod
    def _write(cls, entries: dict[str, dict[str, object]]) -> None:
        """Replaces the file whole, 0600 from its first byte.

        Args:
            entries: Name to entry.
        """
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema": 1, "adapters": entries}, handle, indent=2, sort_keys=True)
                handle.write("\n")
            Path(staging).chmod(FILE_MODE)
            Path(staging).replace(path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
