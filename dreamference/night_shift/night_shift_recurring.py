"""Night Shift's repeating tasks (DESKTOP_PARITY §9.5, the owner's decision 7 of 2026-10-21).

A repeating task is a template under ``$CODEX_HOME/night/recurring/<id>.json``: a repository, the
task's words, an optional test command and a cadence, every night or every week on one day. At the
start of each run, before the queue is read, the runner queues every template that is due tonight
through the launcher's own ``ling night add`` in its repository, so a repeating task becomes an
ordinary queued task (best-of-N, the window, the report all as for any other) and the queue keeps
one writer. A template is queued at most once a day; templates are written whole and atomically.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

#: The days a weekly template can name, as ``date.weekday()`` numbers them.
WEEKDAYS: Final[list[str]] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

#: The cadences a template can have.
DAILY: Final[str] = "daily"
WEEKLY: Final[str] = "weekly"
CADENCES: Final[list[str]] = [DAILY, WEEKLY]

#: A template's id: what ``add`` makes, and all ``drop`` accepts.
TEMPLATE_ID: Final[re.Pattern[str]] = re.compile(r"^r[0-9a-f]{12}$")

#: The longest task a template keeps, as ``ling night add`` takes it from the app.
MAX_TASK_CHARS: Final[int] = 8000

#: How ``ling night add`` starts the line that says it queued the task.
QUEUED: Final[str] = "Queued "

#: How long ``ling night add`` may take, in seconds.
ADD_TIMEOUT_S: Final[int] = 60

#: How much of a refusal a template keeps, and how much of the task a line of the report shows.
REASON_CHARS: Final[int] = 500
SHOWN_TASK_CHARS: Final[int] = 80

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class NightShiftRecurring:
    """Repeating Night Shift tasks: their templates, and queueing the ones due tonight."""

    @classmethod
    def folder(cls, night_dir: Path) -> Path:
        """The folder holding the templates.

        Args:
            night_dir: Night Shift's folder, ``$CODEX_HOME/night``.

        Returns:
            ``<night_dir>/recurring``.
        """
        return night_dir / "recurring"

    @classmethod
    def add(
        cls,
        night_dir: Path,
        repo: Path,
        task: str,
        cadence: str,
        weekday: str | None = None,
        test: str | None = None,
    ) -> dict[str, Any]:
        """Writes a new template.

        Args:
            night_dir: Night Shift's folder.
            repo: The repository the task runs in (an absolute path to a folder).
            task: What to do, in words.
            cadence: ``daily`` or ``weekly``.
            weekday: For ``weekly``, the day (``mon`` … ``sun``).
            test: The test command the run uses, if any.

        Returns:
            The template as written.

        Raises:
            ValueError: When an argument is not one a template can hold.
        """
        words = task.strip()
        if not words or len(words) > MAX_TASK_CHARS:
            raise ValueError(f"describe the task in 1 to {MAX_TASK_CHARS} characters")
        if not repo.is_absolute() or not repo.is_dir():
            raise ValueError(f"{repo} is not a folder")
        if cadence not in CADENCES:
            raise ValueError(f"the cadence is daily or weekly, not {cadence}")
        if cadence == WEEKLY and weekday not in WEEKDAYS:
            raise ValueError(f"a weekly task names its day: {', '.join(WEEKDAYS)}")
        template: dict[str, Any] = {
            "id": f"r{secrets.token_hex(6)}",
            "repo": str(repo),
            "task": words,
            "test": test.strip() if test and test.strip() else None,
            "cadence": cadence,
            "weekday": weekday if cadence == WEEKLY else None,
            "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "last_queued": None,
            "last_error": None,
        }
        cls._write(night_dir, template)
        return template

    @classmethod
    def templates(cls, night_dir: Path) -> list[dict[str, Any]]:
        """Every template, oldest first; a file that is not one is skipped.

        Args:
            night_dir: Night Shift's folder.

        Returns:
            The templates.
        """
        folder = cls.folder(night_dir)
        if not folder.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in sorted(folder.glob("r*.json")):
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(value, dict) and TEMPLATE_ID.match(str(value.get("id", ""))):
                found.append(value)
        return sorted(found, key=lambda entry: str(entry.get("created") or ""))

    @classmethod
    def drop(cls, night_dir: Path, template_id: str) -> bool:
        """Removes a template; the tasks it already queued stay.

        Args:
            night_dir: Night Shift's folder.
            template_id: The template's id.

        Returns:
            Whether a template was removed.
        """
        if not TEMPLATE_ID.match(template_id):
            return False
        path = cls.folder(night_dir) / f"{template_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    @classmethod
    def due(cls, template: dict[str, Any], today: date) -> bool:
        """Whether a template is to be queued on ``today``: not yet queued that day, and its day.

        Args:
            template: A template.
            today: The night's date.

        Returns:
            Whether to queue it.
        """
        if template.get("last_queued") == today.isoformat():
            return False
        if template.get("cadence") == DAILY:
            return True
        if template.get("cadence") == WEEKLY:
            return template.get("weekday") == WEEKDAYS[today.weekday()]
        return False

    @classmethod
    def queue_due(
        cls, night_dir: Path, mightling_bin: str, today: date, run: Runner = subprocess.run
    ) -> list[str]:
        """Queues every template due on ``today`` with ``ling night add`` in its repository.

        A template whose repository is gone, or that ``ling night add`` refuses, keeps the reason in
        ``last_error`` and is tried again the next night.

        Args:
            night_dir: Night Shift's folder.
            mightling_bin: The ``ling`` executable.
            today: The night's date.
            run: How a command runs (``subprocess.run``; tests pass a stand-in).

        Returns:
            The lines that say what was queued or why not, for the run's output and report.
        """
        said: list[str] = []
        for template in cls.templates(night_dir):
            if not cls.due(template, today):
                continue
            repo = Path(str(template.get("repo", "")))
            if not repo.is_dir():
                template["last_error"] = f"{repo} is not there"
                cls._write(night_dir, template)
                said.append(f"Repeating task {template['id']} not queued: {repo} is not there.")
                continue
            args = [mightling_bin, "night", "add"]
            if template.get("test"):
                args += ["--test", str(template["test"])]
            args.append(str(template["task"]))
            try:
                done = run(
                    args,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=ADD_TIMEOUT_S,
                    env={**os.environ},
                )
            except (OSError, subprocess.SubprocessError) as error:
                template["last_error"] = str(error)[:REASON_CHARS]
                cls._write(night_dir, template)
                said.append(f"Repeating task {template['id']} not queued: {error}.")
                continue
            # `ling night add` says what it did: a refusal (no git repository, no commit yet) is printed
            # like a success, so only its "Queued …" line counts as queued.
            if done.returncode != 0 or not (done.stdout or "").lstrip().startswith(QUEUED):
                reason = (done.stderr or done.stdout or f"exit {done.returncode}").strip()[
                    :REASON_CHARS
                ]
                template["last_error"] = reason
                cls._write(night_dir, template)
                said.append(f"Repeating task {template['id']} not queued: {reason}")
                continue
            template["last_queued"] = today.isoformat()
            template["last_error"] = None
            cls._write(night_dir, template)
            shown = str(template["task"])[:SHOWN_TASK_CHARS]
            said.append(
                f"Repeating task {template['id']} queued for tonight in {repo.name}: {shown}"
            )
        return said

    @classmethod
    def _write(cls, night_dir: Path, template: dict[str, Any]) -> None:
        """Writes a template whole, through a temporary file renamed over the old one."""
        folder = cls.folder(night_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{template['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(template, indent=2) + "\n")
        temporary.replace(path)
