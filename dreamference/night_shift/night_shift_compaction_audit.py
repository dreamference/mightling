"""The nightly audit of the day's compactions (specs/DREAMFERENCE_PUFFIN_COMPACTION.md §10.3).

When Codex compacts a session it drops every tool call and output and keeps a model-written
summary. This audit asks, by rule and with no model call, what each summary lost: for every
`compacted` record in the rollouts written since the last audit, it collects the identifiers of the
span the summary replaced (the files the commands changed, the commands that failed, the last test
result) and checks whether each appears in the summary, and in the rule-built ledger handed to the
model after it (§10.1). The losses go into the morning report.
"""

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Final

STATE_FILE: Final[str] = "compaction-audit.json"
# The first audit looks back this far; later ones start where the previous one stopped.
FIRST_LOOKBACK_S: Final[int] = 24 * 3600
MAX_ROLLOUTS: Final[int] = 400
MAX_ROLLOUT_BYTES: Final[int] = 64 * 1024 * 1024
MAX_REPORT_LINES: Final[int] = 20
MAX_NAMED_LOSSES: Final[int] = 3
MAX_COMMAND_CHARS: Final[int] = 80

EXIT_MARKER: Final[re.Pattern] = re.compile(r"Process exited with code (-?\d+)")
LEDGER_FIRST_LINE: Final[str] = "Ledger built from the tool history by rule"
# A "summary" that is the model's text-format tool call, which Codex keeps as the summary (§11.2).
STRAY_TOOL_CALL: Final[str] = "<tool_call>"
PATCH_HEADER: Final[re.Pattern] = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
# Where a shell command writes: `> path`, `>> path`, `tee [-a] path`, and `sed -i … path`.
REDIRECT: Final[re.Pattern] = re.compile(r"(?:>>?|\btee(?:\s+-a)?)\s+([^\s;&|<>()'\"`]+)")
SED_IN_PLACE: Final[re.Pattern] = re.compile(r"\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|<>()'\"`]+)")
PATH_WORD: Final[re.Pattern] = re.compile(r"[\w./-]*[/.][\w./-]+")
# A file name: a path ending in an extension (`sympy/solvers/solveset.py`, `tests/test_x.py::name`).
EXTENSIONS: Final[str] = ("py|pyi|rs|ts|tsx|js|jsx|mjs|go|java|kt|c|h|cc|cpp|hpp|cs|rb|php|swift|sh|sql|"
                          "md|rst|txt|toml|json|yaml|yml|cfg|ini|html|css")
FILE_WORD: Final[re.Pattern] = re.compile(rf"[\w./-]*\w\.(?:{EXTENSIONS})\b(?:::[\w\[\]-]+)?")
# A script that writes a file it names: `open(p, "w")`, `.write_text(`, `.write_bytes(`.
WRITES_FILE: Final[re.Pattern] = re.compile(r"open\([^)]*['\"][wa]\+?['\"]|\.write_(?:text|bytes)\(")
QUOTED_FILE: Final[re.Pattern] = re.compile(rf"['\"]([\w./-]+\.(?:{EXTENSIONS}))['\"]")
ERROR_NAME: Final[re.Pattern] = re.compile(r"\b([A-Z]\w*(?:Error|Exception|Failure))\b")
LEADING_CD: Final[re.Pattern] = re.compile(r"^cd\s+\S+\s*&&\s*")
HAS_LETTER: Final[re.Pattern] = re.compile(r"[A-Za-z]")
# Rollout record types (codex-rs/protocol, `RolloutItem`), and the payload types of tool calls.
SESSION_META: Final[str] = "session_meta"
COMPACTED: Final[str] = "compacted"
RESPONSE_ITEM: Final[str] = "response_item"
MESSAGE: Final[tuple] = (RESPONSE_ITEM, "message")
CALLS: Final[tuple] = ("function_call", "custom_tool_call")
OUTPUTS: Final[tuple] = ("function_call_output", "custom_tool_call_output")
SUCCESS: Final[str] = "0"
TEST_SUMMARY_PREFIXES: Final[tuple] = ("test result: ", "Tests: ")
TEST_DURATION: Final[str] = " in "
TEST_COUNT: Final[re.Pattern] = re.compile(r"\d+ (?:passed|failed|errors?|skipped)")
# Basenames too common to show that a summary meant this file.
GENERIC_NAMES: Final[frozenset] = frozenset({"__init__.py", "setup.py", "test.py", "main.py", "mod.rs",
                                              "lib.rs", "index.js", "README.md"})


class NightShiftCompactionAudit:
    """Audits the compactions in the rollouts written since the previous audit.

    A *span* is what one compaction replaced: `summary`, `changed` (files the commands changed),
    `failed` (first lines of the commands that failed), `last_test` (the last test summary line,
    or None) and `ledger` (the ledger handed over after it, or ""). A *finding* is one span
    judged: `session`, `cwd`, `started`, `number`, `total`, `kept` ({group: (kept, of)}), `lost`
    ({group: [identifier]}), `ledger_covered` and `stray_tool_call` (the summary was a tool call).
    """

    @classmethod
    def run(cls, sessions_dir: Path, night_dir: Path, now: float | None = None) -> list[str]:
        """Audits the rollouts changed since the previous audit and records how far it got.

        Args:
            sessions_dir: `$CODEX_HOME/sessions`.
            night_dir: The queue directory, where the audit keeps its state.
            now: The time to audit up to; defaults to now.

        Returns:
            list[str]: The report's lines, empty when there was no compaction.
        """
        now = time.time() if now is None else now
        since = cls._since(night_dir, now)
        findings = [finding for path in cls.rollouts(sessions_dir, since, now)
                    for finding in cls.audit_rollout(path)]
        cls._save_state(night_dir, now)
        return cls.render(findings, since)

    @classmethod
    def rollouts(cls, sessions_dir: Path, since: float, until: float) -> list[Path]:
        """Rollout files last written in `[since, until)`, oldest first, capped at `MAX_ROLLOUTS`.

        Args:
            sessions_dir: `$CODEX_HOME/sessions`.
            since: Start of the period, epoch seconds.
            until: End of the period, epoch seconds.

        Returns:
            list[Path]: The files.
        """
        if not sessions_dir.is_dir():
            return []
        found: list[tuple[float, Path]] = []
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if since <= stat.st_mtime < until and stat.st_size <= MAX_ROLLOUT_BYTES:
                found.append((stat.st_mtime, path))
        return [path for _, path in sorted(found)[-MAX_ROLLOUTS:]]

    @classmethod
    def audit_rollout(cls, path: Path) -> list[dict[str, Any]]:
        """Audits every compaction in one rollout.

        Args:
            path: The rollout file.

        Returns:
            list[dict[str, Any]]: One per compaction, in order.
        """
        meta: dict[str, Any] = {}
        spans = list(cls.spans(cls._records(path), meta))
        session = str(meta.get("id") or path.stem.rsplit("-", 5)[-5:][0])[:8]
        return [cls.judge(span, session, str(meta.get("cwd", "?")), str(meta.get("timestamp", ""))[:16],
                          number, len(spans))
                for number, span in enumerate(spans, start=1)]

    @classmethod
    def spans(cls, records: Iterator[dict[str, Any]], meta: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Splits a rollout at its compactions.

        Args:
            records: The rollout's JSON records.
            meta: Filled with the session's `session_meta` payload.

        Yields:
            dict[str, Any]: For each compaction, the span since the previous one, with the ledger that
                followed it, if any.
        """
        calls: dict[str, dict[str, str]] = {}
        order: list[str] = []
        pending: dict[str, Any] | None = None
        for record in records:
            payload = record.get("payload") or {}
            kind = (record.get("type"), payload.get("type") if isinstance(payload, dict) else None)
            if kind[0] == SESSION_META:
                meta.update(payload)
            elif kind[0] == COMPACTED:
                if pending:
                    yield pending
                pending = cls._span([calls[call_id] for call_id in order], str(payload.get("message", "")))
                calls, order = {}, []
            elif kind == MESSAGE and pending and LEDGER_FIRST_LINE in json.dumps(payload):
                pending["ledger"] = json.dumps(payload)
            else:
                cls._collect_call(kind, payload, calls, order)
        if pending:
            yield pending

    @classmethod
    def judge(cls, span: dict[str, Any], session: str, cwd: str, started: str, number: int,
              total: int) -> dict[str, Any]:
        """Checks which of a span's identifiers its summary, or the ledger after it, still names.

        Args:
            span: The span.
            session: Short session id.
            cwd: The session's working directory.
            started: When the session started.
            number: Which compaction of the session this is.
            total: How many the session had.

        Returns:
            dict[str, Any]: Counts and the identifiers lost.
        """
        failed = span["failed"]
        groups = {
            "files changed": (cls.workspace_files(span["changed"], cwd), cls.names_path),
            "failed commands": (list(failed), lambda text, item: cls.names_failure(text, failed[item])),
            "last test result": ([span["last_test"]] if span["last_test"] else [], cls.names_test),
        }
        kept: dict[str, tuple[int, int]] = {}
        lost: dict[str, list[str]] = {}
        covered = 0
        for name, (items, names) in groups.items():
            missing = [item for item in items if not names(span["summary"], item)]
            kept[name] = (len(items) - len(missing), len(items))
            lost[name] = missing
            covered += sum(1 for item in missing if span["ledger"] and names(span["ledger"], item))
        return {"session": session, "cwd": cwd, "started": started, "number": number, "total": total,
                "kept": kept, "lost": lost, "ledger_covered": covered,
                "stray_tool_call": STRAY_TOOL_CALL in span["summary"]}

    @classmethod
    def workspace_files(cls, paths: list[str], cwd: str) -> list[str]:
        """The changed files that belong to the work.

        Relative paths, and absolute ones under `cwd` (made relative). Scratch files elsewhere
        (`/tmp/repro.py`) are dropped: a summary that leaves them out loses nothing the next turn
        needs.

        Args:
            paths: Paths the commands wrote.
            cwd: The session's working directory.

        Returns:
            list[str]: The files, without duplicates.
        """
        root = cwd.rstrip("/") + "/"
        kept = [path[len(root):] if path.startswith(root) else path
                for path in paths if not path.startswith("/") or path.startswith(root)]
        return list(dict.fromkeys(path for path in kept if path))

    @classmethod
    def names_path(cls, text: str, path: str) -> bool:
        """Whether `text` names `path`: whole, by its last two parts, or by a distinctive basename.

        Args:
            text: A summary or ledger.
            path: The file.

        Returns:
            bool: True when named.
        """
        parts = path.strip("/").split("/")
        if path in text or "/".join(parts[-2:]) in text:
            return True
        basename = re.compile(rf"(?<![\w.]){re.escape(parts[-1])}\b")
        return parts[-1] not in GENERIC_NAMES and basename.search(text) is not None

    @classmethod
    def names_failure(cls, text: str, keys: list[str]) -> bool:
        """Whether `text` names a failed command: by a file it named or the error it raised.

        Args:
            text: A summary or ledger.
            keys: The command's identifiers (see `_failure_keys`).

        Returns:
            bool: True when any is named.
        """
        return any(cls.names_path(text, key) if FILE_WORD.fullmatch(key) else key in text for key in keys)

    @classmethod
    def names_test(cls, text: str, summary: str) -> bool:
        """Whether `text` carries a test result: every count in it (`3 failed`, `12 passed`).

        Args:
            text: A summary or ledger.
            summary: The test run's summary line.

        Returns:
            bool: True when every count appears.
        """
        counts = TEST_COUNT.findall(summary)
        return all(count in text for count in counts) if counts else summary in text

    @classmethod
    def render(cls, findings: list[dict[str, Any]], since: float) -> list[str]:
        """The morning report's lines: totals, then the compactions that lost something.

        Args:
            findings: Every compaction audited.
            since: Start of the period, epoch seconds.

        Returns:
            list[str]: Markdown lines; empty when there was no compaction.
        """
        if not findings:
            return []
        sessions = len({finding["session"] for finding in findings})
        totals = {name: [0, 0] for name in findings[0]["kept"]}
        for finding in findings:
            for name, (kept, of) in finding["kept"].items():
                totals[name][0] += kept
                totals[name][1] += of
        kept_text = "; ".join(f"{name}: {kept} of {of}" for name, (kept, of) in totals.items())
        lines = [f"{len(findings)} compaction(s) in {sessions} session(s) since "
                 f"{datetime.fromtimestamp(since):%Y-%m-%d %H:%M}. What the summaries still named: {kept_text}."]
        losing = [finding for finding in findings if finding["stray_tool_call"] or any(finding["lost"].values())]
        lines += [cls._finding_line(finding) for finding in losing[:MAX_REPORT_LINES]]
        if len(losing) > MAX_REPORT_LINES:
            lines.append(f"… and {len(losing) - MAX_REPORT_LINES} more compaction(s) that lost something.")
        return lines

    @classmethod
    def _finding_line(cls, finding: dict[str, Any]) -> str:
        parts = ["the summary is a stray tool call, not a summary"] if finding["stray_tool_call"] else []
        for name, items in finding["lost"].items():
            if items:
                shown = ", ".join(f"`{item}`" for item in items[:MAX_NAMED_LOSSES])
                more = f" and {len(items) - MAX_NAMED_LOSSES} more" if len(items) > MAX_NAMED_LOSSES else ""
                parts.append(f"{name} lost: {shown}{more}")
        covered = finding["ledger_covered"]
        ledger = f"; the ledger named {covered} of them" if covered else ""
        return (f"Session {finding['session']} ({finding['cwd']}, {finding['started']}), compaction "
                f"{finding['number']} of {finding['total']}: " + "; ".join(parts) + ledger + ".")

    @classmethod
    def _span(cls, calls: list[dict[str, str]], summary: str) -> dict[str, Any]:
        span: dict[str, Any] = {"summary": summary, "changed": [], "failed": {}, "last_test": None, "ledger": ""}
        for call in calls:
            for path in cls._changed_paths(call["command"]):
                if path not in span["changed"]:
                    span["changed"].append(path)
            code = EXIT_MARKER.search(call["output"])
            keys = cls._failure_keys(call) if code and code.group(1) != SUCCESS else []
            if keys:
                span["failed"][cls._first_line(call["command"])] = keys
            span["last_test"] = cls._test_summary(call["output"]) or span["last_test"]
        return span

    @classmethod
    def _collect_call(cls, kind: tuple[Any, Any], payload: dict[str, Any], calls: dict[str, dict[str, str]],
                      order: list[str]) -> None:
        if kind[0] != RESPONSE_ITEM:
            return
        call_id = str(payload.get("call_id", ""))
        if kind[1] in CALLS:
            calls[call_id] = {"command": cls._command(payload), "output": ""}
            order.append(call_id)
        elif kind[1] in OUTPUTS and call_id in calls:
            calls[call_id]["output"] = cls._text(payload.get("output"))

    @classmethod
    def _command(cls, payload: dict[str, Any]) -> str:
        if payload.get("input") is not None:
            return str(payload["input"])
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except (TypeError, ValueError):
            return ""
        if not isinstance(arguments, dict):
            return ""
        command = arguments.get("cmd") or arguments.get("command") or ""
        return " ".join(command) if isinstance(command, list) else str(command)

    @classmethod
    def _text(cls, output: object) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            return "\n".join(str(item.get("text", "")) for item in output if isinstance(item, dict))
        return json.dumps(output)

    @classmethod
    def _failure_keys(cls, call: dict[str, str]) -> list[str]:
        """The files a failed command named and the error it raised.

        A command with neither (a bare `python - <<EOF` that printed nothing named) cannot be judged
        and is left out.
        """
        files = FILE_WORD.findall(call["command"])[:MAX_NAMED_LOSSES]
        errors = ERROR_NAME.findall(call["output"])[-1:]
        return list(dict.fromkeys(files + errors))

    @classmethod
    def _changed_paths(cls, command: str) -> list[str]:
        written = [path for path in REDIRECT.findall(command) + SED_IN_PLACE.findall(command)
                   if PATH_WORD.fullmatch(path) and HAS_LETTER.search(path)]
        if WRITES_FILE.search(command):
            written += [path for path in QUOTED_FILE.findall(command) if PATH_WORD.fullmatch(path)]
        paths = [path.strip() for path in PATCH_HEADER.findall(command) + written]
        return [path for path in paths if path and not path.startswith(("/dev/", "&", "-"))]

    @classmethod
    def _test_summary(cls, output: str) -> str | None:
        for line in reversed(output.splitlines()):
            line = line.strip().strip("=").strip()
            if (TEST_DURATION in line and TEST_COUNT.search(line)) or line.startswith(TEST_SUMMARY_PREFIXES):
                return line
        return None

    @classmethod
    def _first_line(cls, command: str) -> str:
        line = LEADING_CD.sub("", next((line.strip() for line in command.splitlines() if line.strip()), ""))
        return line if len(line) <= MAX_COMMAND_CHARS else line[:MAX_COMMAND_CHARS - 1] + "…"

    @classmethod
    def _records(cls, path: Path) -> Iterator[dict[str, Any]]:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            return
        for line in lines:
            record = cls._parse(line)
            if record is not None:
                yield record

    @classmethod
    def _parse(cls, line: str) -> dict[str, Any] | None:
        try:
            record = json.loads(line)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None

    @classmethod
    def _since(cls, night_dir: Path, now: float) -> float:
        try:
            return float(json.loads((night_dir / STATE_FILE).read_text())["until"])
        except (OSError, ValueError, KeyError, TypeError):
            return now - FIRST_LOOKBACK_S

    @classmethod
    def _save_state(cls, night_dir: Path, now: float) -> None:
        night_dir.mkdir(parents=True, exist_ok=True)
        temporary = night_dir / f".{STATE_FILE}.tmp"
        temporary.write_text(json.dumps({"until": now}))
        temporary.replace(night_dir / STATE_FILE)
