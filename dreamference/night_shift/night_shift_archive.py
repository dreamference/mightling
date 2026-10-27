"""Night Shift's archive (DESKTOP §16 B41, DESKTOP_PARITY §9.5 decision 7).

``ling-admin night archive [--weeks N]`` moves what finished runs left behind once they are older
than N weeks out of ``$CODEX_HOME/night/`` into ``night/archive/``, in the same layout: a finished
task's record (``tasks/<id>.json``) with its transcripts and other logs (``logs/<id>.*`` and each
attempt's ``logs/<id>-a<k>.*``), logs older than that whose task is gone, and the morning reports
(``reports/*.md``). A queued, running or interrupted task is never touched, the branches stay in
their repositories, and nothing is deleted: a file already in the archive under the same name is
left where it was. The desktop app's Scheduled view searches the archive; its inbox no longer
lists what was moved. The run holds ``runner.lock`` throughout, so a night run never writes a
report while it moves, and a task is moved under its own lock, as the launcher changes one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from dreamference.night_shift.archive_outcome import ArchiveOutcome
from dreamference.night_shift.night_shift_queue import FINAL_STATUSES, NightShiftQueue

#: How old, in weeks, a run is before ``night archive`` moves it when no ``--weeks`` is given.
DEFAULT_ARCHIVE_WEEKS: Final[int] = 4

#: The archive's folder under the queue directory.
ARCHIVE_DIR_NAME: Final[str] = "archive"

#: The folders of the queue directory the archive mirrors.
TASKS: Final[str] = "tasks"
LOGS: Final[str] = "logs"
REPORTS: Final[str] = "reports"

#: A log file's task: ``<id>.<kind>`` or an attempt's ``<id>-a<k>.<kind>``, the id as the launcher
#: makes it (``YYYYMMDD-HHMM-xxx``, ``ling-rs/src/night.rs`` ``create_task``) or an older one without
#: the suffix.
LOG_NAME: Final[re.Pattern[str]] = re.compile(
    r"^(\d{8}-\d{4}(?:-[0-9a-f]{3})?)(?:-a\d+)?\.[A-Za-z0-9.]+$"
)


class NightShiftArchive:
    """Moves old runs' records, transcripts and reports into ``night/archive/``."""

    @classmethod
    def archive_dir(cls, night_dir: Path) -> Path:
        """Names the archive of a queue directory.

        Args:
            night_dir: The queue directory.

        Returns:
            Path: ``<night_dir>/archive``.
        """
        return night_dir / ARCHIVE_DIR_NAME

    @classmethod
    def archive(
        cls, night_dir: Path, weeks: int, now: datetime | None = None
    ) -> ArchiveOutcome | None:
        """Moves every finished run older than ``weeks`` weeks into the archive.

        Args:
            night_dir: The queue directory.
            weeks: How old a run must be, in weeks; 1 or more.
            now: The time to count from; the current time when None (tests pass one).

        Returns:
            ArchiveOutcome | None: What was moved, or None when a night run (or a SWE-bench run)
            holds ``runner.lock`` and nothing was touched.

        Raises:
            ValueError: When ``weeks`` is less than 1.
        """
        if weeks < 1:
            raise ValueError("archive runs older than one week or more")
        cutoff = (now or datetime.now().astimezone()) - timedelta(weeks=weeks)
        with NightShiftQueue.runner_lock(night_dir, holder="Night Shift's archive") as held:
            if not held:
                return None
            outcome = ArchiveOutcome()
            moved_ids = cls._archive_tasks(night_dir, cutoff, outcome)
            cls._archive_logs(night_dir, cutoff, moved_ids, outcome)
            cls._archive_reports(night_dir, cutoff, outcome)
            return outcome

    @classmethod
    def finished_at(cls, record: dict[str, Any], path: Path) -> datetime | None:
        """Tells when a finished task last changed: its history's last time, else its file's.

        Args:
            record: The task record.
            path: Its file, whose modification time stands in for a history without times.

        Returns:
            datetime | None: The time, with its offset; None when neither can be read.
        """
        history = record.get("history")
        if isinstance(history, list) and history and isinstance(history[-1], dict):
            stamp = history[-1].get("at")
            if isinstance(stamp, str):
                try:
                    return cls._aware(datetime.fromisoformat(stamp))
                except ValueError:
                    pass
        return cls._modified(path)

    @classmethod
    def _archive_tasks(cls, night_dir: Path, cutoff: datetime, outcome: ArchiveOutcome) -> set[str]:
        tasks_dir = night_dir / TASKS
        moved: set[str] = set()
        if not tasks_dir.is_dir():
            return moved
        for path in sorted(tasks_dir.glob("*.json")):
            task_id = path.stem
            if not LOG_NAME.match(f"{task_id}.json"):
                continue
            with NightShiftQueue._locked(tasks_dir / f"{task_id}.lock"):
                record = NightShiftQueue.read(night_dir, task_id)
                if record is None or record.get("status") not in FINAL_STATUSES:
                    continue
                finished = cls.finished_at(record, path)
                if finished is None or finished >= cutoff:
                    continue
                if not cls._move(path, cls.archive_dir(night_dir) / TASKS / path.name, outcome):
                    continue
                moved.add(task_id)
                outcome.tasks.append(task_id)
            (tasks_dir / f"{task_id}.lock").unlink(missing_ok=True)
        return moved

    @classmethod
    def _archive_logs(
        cls, night_dir: Path, cutoff: datetime, moved_ids: set[str], outcome: ArchiveOutcome
    ) -> None:
        logs_dir = night_dir / LOGS
        if not logs_dir.is_dir():
            return
        live = (
            {path.stem for path in (night_dir / TASKS).glob("*.json")}
            if (night_dir / TASKS).is_dir()
            else set()
        )
        for path in sorted(logs_dir.iterdir()):
            match = LOG_NAME.match(path.name)
            if not match or not path.is_file():
                continue
            task_id = match.group(1)
            # A moved task's logs go with it; a log whose task is gone (dropped, or archived by hand)
            # goes once it is as old as the cutoff; a live task's logs stay with it.
            if task_id not in moved_ids:
                modified = cls._modified(path)
                if task_id in live or modified is None or modified >= cutoff:
                    continue
            if cls._move(path, cls.archive_dir(night_dir) / LOGS / path.name, outcome):
                outcome.logs += 1

    @classmethod
    def _archive_reports(cls, night_dir: Path, cutoff: datetime, outcome: ArchiveOutcome) -> None:
        reports_dir = night_dir / REPORTS
        if not reports_dir.is_dir():
            return
        for path in sorted(reports_dir.glob("*.md")):
            modified = cls._modified(path)
            if modified is None or modified >= cutoff:
                continue
            if cls._move(path, cls.archive_dir(night_dir) / REPORTS / path.name, outcome):
                outcome.reports += 1

    @classmethod
    def _move(cls, source: Path, target: Path, outcome: ArchiveOutcome) -> bool:
        if target.exists():
            outcome.skipped.append(str(source))
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(source).replace(target)
        return True

    @classmethod
    def _modified(cls, path: Path) -> datetime | None:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            return None

    @classmethod
    def _aware(cls, moment: datetime) -> datetime:
        return moment if moment.tzinfo else moment.astimezone()
