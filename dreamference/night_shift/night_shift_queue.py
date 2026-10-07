"""
The Night Shift queue on disk, as the `mling` launcher writes it.

The launcher (`mling-rs/src/night.rs`) adds, lists and drops tasks; the night run reads them and
records what happened. Both sides change a task only under `tasks/<id>.lock` (flock on both sides)
and replace its JSON file whole, so a `/night drop` and a status change never overwrite each other.
See specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §4.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Final, Iterator, List, Optional

# Statuses a night run picks up: new tasks, and tasks a previous window cut off.
RUNNABLE_STATUSES: Final[tuple] = ("queued", "interrupted")

# Statuses that end a task for good.
FINAL_STATUSES: Final[tuple] = ("done", "no-change", "stalled", "failed", "cancelled")


class NightShiftQueue:
    """The files under `$CODEX_HOME/night/`."""

    @classmethod
    def night_dir(cls) -> Path:
        """
        Resolves the queue directory the way the launcher does.

        Returns:
            Path: `$CODEX_HOME/night`, with `CODEX_HOME` defaulting to `~/.mightling`.
        """
        codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.mightling")
        return Path(codex_home) / "night"

    @classmethod
    def tasks(cls, night_dir: Path) -> List[Dict[str, Any]]:
        """
        Reads every task, oldest first.

        Args:
            night_dir: The queue directory.

        Returns:
            List[Dict[str, Any]]: The task records; unreadable files are skipped.
        """
        tasks_dir = night_dir / "tasks"
        if not tasks_dir.is_dir():
            return []
        records = []
        for path in sorted(tasks_dir.glob("*.json")):
            record = cls._read(path)
            if record is not None:
                records.append(record)
        return sorted(records, key=lambda task: task.get("id", ""))

    @classmethod
    def read(cls, night_dir: Path, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Reads one task.

        Args:
            night_dir: The queue directory.
            task_id: The task id.

        Returns:
            Optional[Dict[str, Any]]: The record, or None if it cannot be read.
        """
        return cls._read(night_dir / "tasks" / f"{task_id}.json")

    @classmethod
    def update(cls, night_dir: Path, task_id: str,
               change: Callable[[Dict[str, Any]], Any]) -> Any:
        """
        Changes one task under its lock and writes it back whole.

        Args:
            night_dir: The queue directory.
            task_id: The task id.
            change: Called with the current record, which it edits in place; its return value is
                passed through.

        Returns:
            Any: What `change` returned.
        """
        tasks_dir = night_dir / "tasks"
        with cls._locked(tasks_dir / f"{task_id}.lock"):
            path = tasks_dir / f"{task_id}.json"
            record = cls._read(path)
            if record is None:
                raise FileNotFoundError(path)
            outcome = change(record)
            cls._write_atomically(path, record)
            return outcome

    @classmethod
    def set_status(cls, record: Dict[str, Any], status: str, note: Optional[str] = None) -> None:
        """
        Sets a record's status and appends it to the history, as the launcher does.

        Args:
            record: The task record, edited in place.
            status: The new status.
            note: An optional note kept with the history entry.
        """
        record["status"] = status
        entry: Dict[str, Any] = {"at": cls.now(), "status": status}
        if note:
            entry["note"] = note
        record.setdefault("history", []).append(entry)

    @classmethod
    def transition(cls, night_dir: Path, task_id: str, status: str,
                   note: Optional[str] = None, **fields: Any) -> str:
        """
        Moves a task to `status` unless it was cancelled meanwhile.

        A `/night drop` of a running task leaves it `cancel-requested`; the runner must not write
        over that with its own next status, so a requested cancellation wins and becomes
        `cancelled`.

        Args:
            night_dir: The queue directory.
            task_id: The task id.
            status: The status the runner wants.
            note: An optional history note.
            **fields: Other top-level fields to set (`result`, `session`, ...).

        Returns:
            str: The status actually written.
        """
        def change(record: Dict[str, Any]) -> str:
            for key, value in fields.items():
                record[key] = value
            wanted = status
            if record.get("status") in ("cancel-requested", "cancelled") and status != "cancelled":
                wanted = "cancelled"
            if record.get("status") != wanted or note:
                cls.set_status(record, wanted, note)
            return wanted
        return cls.update(night_dir, task_id, change)

    @classmethod
    @contextmanager
    def runner_lock(cls, night_dir: Path, holder: str = "a Night Shift run") -> Iterator[bool]:
        """
        Holds `runner.lock` for a run, without waiting. A night run and a SWE-bench run take the
        same lock, so the two never work the model server at once.

        Args:
            night_dir: The queue directory.
            holder: What holds the lock, written into the file so a refusal can name it.

        Yields:
            bool: True if this process holds the lock, False if another run does.
        """
        night_dir.mkdir(parents=True, exist_ok=True)
        # Opened for appending: truncating here would erase the name of a run that holds it.
        with open(night_dir / "runner.lock", "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                handle.truncate(0)
                handle.write(holder + "\n")
                handle.flush()
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @classmethod
    def runner_holder(cls, night_dir: Optional[Path] = None) -> Optional[str]:
        """
        Names what holds `runner.lock` right now.

        Args:
            night_dir: The queue directory; defaults to `night_dir()`.

        Returns:
            Optional[str]: The holder as it named itself ("a Night Shift run", "a SWE-bench
            run"), or None when nothing holds the lock.
        """
        directory = night_dir or cls.night_dir()
        if not cls.runner_active(directory):
            return None
        try:
            return (directory / "runner.lock").read_text().strip() or "a run"
        except OSError:
            return "a run"

    @classmethod
    def runner_active(cls, night_dir: Optional[Path] = None) -> bool:
        """
        Tells whether a night run holds `runner.lock` right now.

        `mling-admin index`, `codex build` and `server start` ask this and refuse while it is
        true (spec §6.2): each of them is heavy enough to put the model server at risk.

        Args:
            night_dir: The queue directory; defaults to `night_dir()`.

        Returns:
            bool: True while a night run is in progress.
        """
        path = (night_dir or cls.night_dir()) / "runner.lock"
        if not path.exists():
            return False
        with open(path, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False

    @classmethod
    def now(cls) -> str:
        """
        Returns:
            str: The local time as RFC 3339 with its offset, the launcher's history format.
        """
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @classmethod
    @contextmanager
    def _locked(cls, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @classmethod
    def _read(cls, path: Path) -> Optional[Dict[str, Any]]:
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    @classmethod
    def _write_atomically(cls, path: Path, record: Dict[str, Any]) -> None:
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        os.replace(staging, path)
