"""`ling-admin searxng status`: each search engine's state, read from SearXNG's own logs.

SearXNG keeps an engine's suspension in the worker's memory and publishes it nowhere: neither
`/config` nor `/stats/errors` says which engine rests until when. Every suspension is logged,
though, with the length SearXNG gave it (`... (suspended_time=900)`), so the logs since the
container started say which engines are resting and until when. Nothing here sends a search: a
probe query per engine is exactly the traffic that gets an address refused.

What it cannot see is success: SearXNG does not log a query that worked. "ok" means no error was
logged for the engine since the container started, or none recently.
"""

import json
import re
import subprocess
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Final

from dreamference.chat.searxng_engine_event import SearxngEngineEvent
from dreamference.chat.searxng_settings import GENERAL_ENGINES

# An engine whose last error is older than this, and whose suspension is over, counts as ok.
RECENT_ERROR: Final[timedelta] = timedelta(minutes=15)

# Encyclopedias answer facts, not web searches: they alone do not make search work.
NOT_WEB_SEARCH: Final[frozenset[str]] = frozenset({"wikipedia", "wikidata"})

# The states `state` returns, and the mark `report_lines` prints for each.
OK: Final[str] = "ok"
MARKS: Final[dict[str, str]] = {
    OK: "✅",
    "suspended": "⏸️ ",
    "failing": "⚠️ ",
    "not loaded": "❌",
}

# What `docker inspect --format "{{.State.Running}} {{.State.StartedAt}}"` prints for a running one.
RUNNING: Final[str] = "true"
INSPECT_FIELDS: Final[int] = 2

# `<docker timestamp> <searxng timestamp> WARNING:searx.engines.<name>: <message>`
_ENGINE_LINE = re.compile(r"^(\S+) .*?(?:WARNING|ERROR):searx\.engines\.([^:]+): (.*)$")
# `... ERROR:searx.searx.search.processor: (PID 850) wikidata: engine INIT failed, exception: <why>`
_INIT_LINE = re.compile(
    r"^(\S+) .*?searx\.search\.processor: \(PID \d+\) ([^:]+): engine INIT failed, exception: (.*)$"
)
_SUSPENDED = re.compile(r"\s*\(suspended_time=(\d+)\)")
# The message an ErrorContext line carries last: `..., ('CAPTCHA (us-en) (suspended_time=0)',)) False`
_CONTEXT_MESSAGE = re.compile(r"\('((?:[^'\\]|\\.)*)',?\)\) (?:True|False)$")
# The exception an ErrorContext without a message names: `'httpx.TimeoutException'`
_CONTEXT_EXCEPTION = re.compile(r"'([\w.]+(?:Exception|Error))'")
# Docker's RFC 3339 timestamp, with up to nanoseconds and `Z` or an offset.
_STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$")


class SearxngStatus:
    """Reports each engine's state from SearXNG's logs and configuration."""

    @classmethod
    def parse_time(cls, stamp: str) -> datetime | None:
        """Parses one of Docker's log timestamps.

        Args:
            stamp (str): Docker's RFC 3339 log timestamp, with up to nanoseconds.

        Returns:
            datetime | None: The time, in UTC, or None when it does not parse.
        """
        match = _STAMP.match(stamp)
        if not match:
            return None
        base, fraction, zone = match.groups()
        offset = "+00:00" if zone.upper().endswith("Z") else zone
        try:
            return datetime.fromisoformat(
                f"{base}.{(fraction or '0')[:6].ljust(6, '0')}{offset}"
            ).astimezone(UTC)
        except ValueError:
            return None

    @classmethod
    def message_of(cls, message: str) -> str:
        """Takes the reason out of an engine's log message.

        Args:
            message (str): What follows `searx.engines.<name>: ` in the line.

        Returns:
            str: The message itself, or for an `ErrorContext(...)` line the message it carries
            last, or the exception it names when it carries none (a timeout, a dropped connection).
        """
        if not message.startswith("ErrorContext("):
            return message
        context = _CONTEXT_MESSAGE.search(message)
        if context:
            return context.group(1)
        exception = _CONTEXT_EXCEPTION.search(message)
        return exception.group(1).rsplit(".", 1)[-1] if exception else "error"

    @classmethod
    def parse_log(cls, lines: Iterable[str]) -> dict[str, SearxngEngineEvent]:
        """Finds each engine's last logged error.

        Args:
            lines (Iterable[str]): `docker logs --timestamps` output, oldest first.

        Returns:
            dict[str, SearxngEngineEvent]: Engine name to its last error. An engine that failed
            at start keeps that error: it was never loaded, so nothing later can change it.
        """
        events: dict[str, SearxngEngineEvent] = {}
        for line in lines:
            init = _INIT_LINE.match(line.rstrip())
            match = init or _ENGINE_LINE.match(line.rstrip())
            at = cls.parse_time(match.group(1)) if match else None
            if not match or not at:
                continue
            name = match.group(2).strip()
            if name in events and events[name].init_failed:
                continue
            message = match.group(3) if init else cls.message_of(match.group(3))
            suspended = _SUSPENDED.search(message)
            events[name] = SearxngEngineEvent(
                at=at,
                reason=_SUSPENDED.sub("", message).strip(),
                suspended_seconds=0 if init or not suspended else int(suspended.group(1)),
                init_failed=bool(init),
            )
        return events

    @classmethod
    def state(cls, event: SearxngEngineEvent | None, now: datetime) -> tuple[str, str]:
        """Says what an engine's last error means now.

        Args:
            event (SearxngEngineEvent | None): The engine's last logged error, if any.
            now (datetime): The current time, in UTC.

        Returns:
            tuple[str, str]: ("ok" | "suspended" | "failing" | "not loaded", the detail to print).
        """
        if event is None:
            return OK, "no error since SearXNG started"
        if event.init_failed:
            return "not loaded", f"failed at start: {event.reason}"
        until = event.at + timedelta(seconds=event.suspended_seconds)
        if event.suspended_seconds and until > now:
            return "suspended", f"until {cls.local(until)}: {event.reason}"
        if now - event.at < RECENT_ERROR:
            return "failing", f"{event.reason} (at {cls.local(event.at)})"
        return OK, f"last error at {cls.local(event.at)}: {event.reason}"

    @classmethod
    def local(cls, moment: datetime) -> str:
        """Formats a time for the report.

        Args:
            moment (datetime): A time with its zone.

        Returns:
            str: Hours and minutes in this machine's zone.
        """
        return moment.astimezone().strftime("%H:%M")

    @classmethod
    def enabled_engines(cls, base_url: str) -> dict[str, bool] | None:
        """Asks SearXNG which general web engines it has and which are on.

        Args:
            base_url (str): SearXNG's address on this machine.

        Returns:
            dict[str, bool] | None: Each general web engine SearXNG knows (and the ones the
            settings name), and whether it is on; None when SearXNG does not answer. `/config`
            sends nothing upstream.
        """
        try:
            with urllib.request.urlopen(f"{base_url.rstrip('/')}/config", timeout=10) as response:
                config = json.load(response)
        except (OSError, ValueError):
            return None
        return {
            engine["name"]: bool(engine.get("enabled"))
            for engine in config.get("engines", [])
            if {"general", "web"} <= set(engine.get("categories", []))
            or engine.get("name") in GENERAL_ENGINES
        }

    @classmethod
    def container_log(cls, container: str) -> list[str] | None:
        """Reads the container's log since it last started.

        Args:
            container (str): The SearXNG container's name.

        Returns:
            list[str] | None: Its log lines, with Docker's timestamps; None when there is no such
            container or it is not running.
        """
        started = subprocess.run(
            ["docker", "inspect", container, "--format", "{{.State.Running}} {{.State.StartedAt}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        fields = started.stdout.split()
        if started.returncode != 0 or len(fields) != INSPECT_FIELDS or fields[0] != RUNNING:
            return None
        logs = subprocess.run(
            ["docker", "logs", "--timestamps", "--since", fields[1], container],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return (logs.stdout + logs.stderr).splitlines()

    @classmethod
    def report_lines(cls, container: str, base_url: str) -> tuple[list[str], int]:
        """Describes each general engine's state.

        Args:
            container (str): The SearXNG container's name.
            base_url (str): SearXNG's address on this machine.

        Returns:
            tuple[list[str], int]: The lines to print, and the exit code: 0 when at least one web
            engine is ok, 1 when none is, 2 when SearXNG is not running.
        """
        lines = cls.container_log(container)
        enabled = cls.enabled_engines(base_url)
        if lines is None or enabled is None:
            return ["❌ SearXNG is not running. Start it with: ling-admin searxng start"], 2
        events = cls.parse_log(lines)
        now = datetime.now(UTC)
        names = [name for name in GENERAL_ENGINES if name in enabled]
        names += sorted(name for name in enabled if name not in names)
        report = [f"SearXNG at {base_url}, general search engines:"]
        working = 0
        for name in names:
            if not enabled[name]:
                report.append(f"   ·  {name:16} off")
                continue
            state, detail = cls.state(events.get(name), now)
            working += state == OK and name not in NOT_WEB_SEARCH
            report.append(f"   {MARKS[state]} {name:16} {state}: {detail}")
        report.append(
            "💡 SearXNG logs failures only, so ok means no recent error, not a recent success."
        )
        if not working:
            report.append(
                "❌ No web engine is answering. Search will return nothing until one recovers."
            )
            return report, 1
        return report, 0
