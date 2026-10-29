"""Night Shift's repeating tasks (DESKTOP_PARITY §9.5, the owner's decision 7 of 2026-10-21).

A repeating task is a template under ``$CODEX_HOME/night/recurring/<id>.json``: a repository,
the task's words, an optional test command and a cadence (every night, every week on one day,
on chosen weekdays, every N nights, or on a day of the month). At the start of each run,
before the queue is read, the runner queues every template that is due tonight through the
launcher's own ``ling night add`` in its repository, so a repeating task becomes an ordinary
queued task (best-of-N, the window, the report all as for any other) and the queue keeps
one writer. A template is queued at most once a day; templates are written whole and atomically.
"""

from __future__ import annotations

import calendar
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
DAYS: Final[str] = "days"
EVERY: Final[str] = "every"
MONTHLY: Final[str] = "monthly"
CADENCES: Final[list[str]] = [DAILY, WEEKLY, DAYS, EVERY, MONTHLY]

#: The nights an ``every`` template may have between runs, and the days of the month.
MIN_EVERY: Final[int] = 1
MAX_EVERY: Final[int] = 365
MIN_MONTHLY: Final[int] = 1
MAX_MONTHLY: Final[int] = 31

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
    def _parse_days(cls, value: object) -> list[str]:
        """The weekdays a ``days`` template names, in the order given, each once.

        Args:
            value: ``"mon,wed,fri"`` or ``["mon", "wed", "fri"]``.

        Returns:
            The days.

        Raises:
            ValueError: When no day is named, or one is not ``mon`` … ``sun``.
        """
        if isinstance(value, str):
            raw = [day.strip().lower() for day in value.split(",") if day.strip()]
        elif isinstance(value, list):
            raw = [str(day).strip().lower() for day in value if str(day).strip()]
        else:
            raw = []
        if not raw or any(day not in WEEKDAYS for day in raw):
            raise ValueError(f"name the days from {', '.join(WEEKDAYS)}, comma-separated")
        return list(dict.fromkeys(raw))

    @classmethod
    def _parse_count(cls, value: object, low: int, high: int, what: str) -> int:
        """A whole number from ``low`` to ``high``: the nights of ``every``, the day of ``monthly``.

        Args:
            value: The number, or its digits.
            low: The smallest allowed.
            high: The largest allowed.
            what: What the number counts, for the refusal.

        Returns:
            The number.

        Raises:
            ValueError: When ``value`` is not a whole number in the range.
        """
        if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
            value = int(value.strip())
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{what} is a whole number from {low} to {high}")
        return value

    @classmethod
    def add(
        cls,
        night_dir: Path,
        repo: Path,
        task: str,
        cadence: str,
        on: str | int | list[str] | None = None,
        test: str | None = None,
    ) -> dict[str, Any]:
        """Writes a new template.

        Args:
            night_dir: Night Shift's folder.
            repo: The repository the task runs in (an absolute path to a folder).
            task: What to do, in words.
            cadence: ``daily``, ``weekly``, ``days``, ``every`` or ``monthly``.
            on: The cadence's parameter: for ``weekly`` the day (``mon`` … ``sun``), for ``days``
                the days (``"mon,wed,fri"`` or a list), for ``every`` the nights between runs
                (1 … 365, the first run the night it is set), for ``monthly`` the day of the month
                (1 … 31, the month's last day where it is shorter).
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
            raise ValueError(f"the cadence is {', '.join(CADENCES)}, not {cadence}")
        if cadence == WEEKLY and on not in WEEKDAYS:
            raise ValueError(f"a weekly task names its day: {', '.join(WEEKDAYS)}")
        days = cls._parse_days(on) if cadence == DAYS else None
        every = (
            cls._parse_count(on, MIN_EVERY, MAX_EVERY, "the nights between runs")
            if cadence == EVERY
            else None
        )
        day = (
            cls._parse_count(on, MIN_MONTHLY, MAX_MONTHLY, "the day of the month")
            if cadence == MONTHLY
            else None
        )

        template: dict[str, Any] = {
            "id": f"r{secrets.token_hex(6)}",
            "repo": str(repo),
            "task": words,
            "test": test.strip() if test and test.strip() else None,
            "cadence": cadence,
            "weekday": on if cadence == WEEKLY else None,
            "days": days,
            "every": every,
            "day": day,
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
        weekday = WEEKDAYS[today.weekday()]
        match template.get("cadence"):
            case "daily":
                on_its_night = True
            case "weekly":
                on_its_night = template.get("weekday") == weekday
            case "days":
                days = template.get("days")
                on_its_night = isinstance(days, list) and weekday in days
            case "every":
                on_its_night = cls._due_every(template, today)
            case "monthly":
                on_its_night = cls._due_monthly(template, today)
            case _:
                on_its_night = False
        return on_its_night

    @classmethod
    def _due_monthly(cls, template: dict[str, Any], today: date) -> bool:
        """Whether ``today`` is a ``monthly`` template's day of the month.

        A day the month does not have (the 31st in a 30-day month, the 29th to 31st in February)
        is the month's last day.

        Args:
            template: A ``monthly`` template.
            today: The night's date.

        Returns:
            Whether it is its day.
        """
        day = template.get("day")
        if (
            not isinstance(day, int)
            or isinstance(day, bool)
            or not MIN_MONTHLY <= day <= MAX_MONTHLY
        ):
            return False
        return today.day == min(day, calendar.monthrange(today.year, today.month)[1])

    @classmethod
    def _due_every(cls, template: dict[str, Any], today: date) -> bool:
        """Whether an ``every`` template is due: its first night, then N nights after it was last queued.

        Counted from the night it was set, which is the first night it is due: a night the run
        did not happen, or that ``ling night add`` refused, moves the count to the night it
        was queued in the end, so a refused task is tried again the next night.

        Args:
            template: An ``every`` template.
            today: The night's date.

        Returns:
            Whether to queue it.
        """
        every = template.get("every")
        if (
            not isinstance(every, int)
            or isinstance(every, bool)
            or not MIN_EVERY <= every <= MAX_EVERY
        ):
            return False
        try:
            last = date.fromisoformat(str(template.get("last_queued")))
        except ValueError:
            return True
        return (today - last).days >= every

    @classmethod
    def describe(cls, template: dict[str, Any]) -> str:
        """A template's cadence in words, as ``night every add`` prints it.

        Args:
            template: A template.

        Returns:
            For example ``every night``, ``every fri``, ``on mon,wed,fri``, ``every 3 nights``.
        """
        match template.get("cadence"):
            case "weekly":
                return f"every {template.get('weekday')}"
            case "days":
                return f"on {','.join(template.get('days') or [])}"
            case "every":
                return (
                    "every night"
                    if template.get("every") == 1
                    else f"every {template.get('every')} nights"
                )
            case "monthly":
                return f"on day {template.get('day')} of each month"
        return "every night"

    @classmethod
    def queue_due(
        cls, night_dir: Path, mightling_bin: str, today: date, run: Runner = subprocess.run
    ) -> list[str]:
        """Queues every template due on ``today`` with ``ling night add`` in its repository.

        A template whose repository is gone, or that ``ling night add`` refuses, keeps the reason in
        ``last_error`` and is tried again on its next due night.

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
                cls._write(night_dir, template, existing_only=True)
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
                cls._write(night_dir, template, existing_only=True)
                said.append(f"Repeating task {template['id']} not queued: {error}.")
                continue
            # `ling night add` says what it did: a refusal (no git repository, no commit yet) is printed
            # like a success, so only its "Queued …" line counts as queued.
            if done.returncode != 0 or not (done.stdout or "").lstrip().startswith(QUEUED):
                reason = (done.stderr or done.stdout or f"exit {done.returncode}").strip()[
                    :REASON_CHARS
                ]
                template["last_error"] = reason
                cls._write(night_dir, template, existing_only=True)
                said.append(f"Repeating task {template['id']} not queued: {reason}")
                continue
            template["last_queued"] = today.isoformat()
            template["last_error"] = None
            cls._write(night_dir, template, existing_only=True)
            shown = str(template["task"])[:SHOWN_TASK_CHARS]
            said.append(
                f"Repeating task {template['id']} queued for tonight in {repo.name}: {shown}"
            )
        return said

    @classmethod
    def _write(cls, night_dir: Path, template: dict[str, Any], existing_only: bool = False) -> None:
        """Writes a template whole, through a temporary file renamed over the old one.

        With ``existing_only``, a template dropped meanwhile is not written back.
        """
        folder = cls.folder(night_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{template['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(template, indent=2) + "\n")
        if existing_only and not path.exists():
            temporary.unlink(missing_ok=True)
            return
        temporary.replace(path)
