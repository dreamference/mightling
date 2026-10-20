"""Reads a session's rollout (`$CODEX_HOME/sessions/**/*.jsonl`) into turns.

A rollout is one JSON record per line: `{timestamp, type, payload}`. A turn runs from an
`event_msg` `task_started` to its `task_complete`; between them, `response_item`s carry the user's
message, the assistant's messages, and each tool call (`function_call` with JSON `arguments`, or
`custom_tool_call` with a raw `input`). `exec resume` appends to the same file, so one file holds
every turn of a scenario.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class ToolCall:
    """One tool call: its name, its arguments as text, and when it was made."""

    name: str
    arguments: str
    at: float
    output: str = ""

    def command(self) -> str:
        """The shell command a call runs (`exec_command`'s `cmd`), else its whole argument text."""
        try:
            value = json.loads(self.arguments)
        except (json.JSONDecodeError, TypeError):
            return self.arguments or ""
        if isinstance(value, dict):
            cmd = value.get("cmd") or value.get("command")
            if isinstance(cmd, list):
                return " ".join(str(part) for part in cmd)
            if isinstance(cmd, str):
                return cmd
        return self.arguments or ""


@dataclass
class Turn:
    """One turn of a session."""

    user: str = ""
    answer: str = ""
    commentary: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    started: float | None = None
    ended: float | None = None
    first_output: float | None = None
    output_tokens: int | None = None
    completed: bool = False

    def seconds(self) -> float | None:
        if self.started is None or self.ended is None:
            return None
        return round(self.ended - self.started, 1)

    def seconds_to_first_output(self) -> float | None:
        if self.started is None or self.first_output is None:
            return None
        return round(self.first_output - self.started, 1)


def _time(stamp: object) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(item: dict) -> str:
    return "".join(
        part.get("text", "") for part in item.get("content", []) if isinstance(part, dict)
    )


_CONTEXT = re.compile(r"^\s*<(environment_context|user_instructions|turn_aborted|world_state)\b")


def read(path: Path) -> list[Turn]:
    """The turns of one rollout file, in order."""
    turns: list[Turn] = []
    current: Turn | None = None
    tokens_before = 0
    last_total = 0
    calls_by_id: dict[str, ToolCall] = {}
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        at = _time(record.get("timestamp"))
        kind, sub = record.get("type"), payload.get("type")
        if kind == "event_msg" and sub == "task_started":
            # Output tokens are counted from the session's running total at this turn's start.
            tokens_before = last_total
            current = Turn(started=at)
            turns.append(current)
            continue
        if current is None:
            continue
        if kind == "event_msg" and sub == "task_complete":
            current.ended, current.completed = at, True
            continue
        if kind == "event_msg" and sub == "token_count" and payload.get("info"):
            total = payload["info"].get("total_token_usage") or {}
            if isinstance(total.get("output_tokens"), int):
                current.output_tokens = total["output_tokens"] - tokens_before
                last_total = total["output_tokens"]
            continue
        if kind != "response_item":
            continue
        if sub == "message" and payload.get("role") == "user":
            text = _text(payload)
            if not _CONTEXT.match(text):
                current.user = (current.user + "\n" + text).strip()
        elif sub == "message" and payload.get("role") == "assistant":
            current.first_output = current.first_output or at
            if current.answer:
                current.commentary.append(current.answer)
            current.answer = _text(payload)
        elif sub in ("function_call", "custom_tool_call"):
            current.first_output = current.first_output or at
            if current.answer:
                current.commentary.append(current.answer)
                current.answer = ""
            call = ToolCall(
                name=str(payload.get("name") or ""),
                arguments=str(payload.get("arguments") or payload.get("input") or ""),
                at=at or 0.0,
            )
            current.calls.append(call)
            if payload.get("call_id"):
                calls_by_id[str(payload["call_id"])] = call
        elif sub in ("function_call_output", "custom_tool_call_output"):
            call = calls_by_id.get(str(payload.get("call_id")))
            output = payload.get("output")
            if call is not None:
                call.output = output if isinstance(output, str) else json.dumps(output)
    return turns


def read_home(codex_home: Path) -> list[Turn]:
    """Every turn under a `CODEX_HOME`'s sessions, oldest rollout first."""
    files = sorted(glob.glob(str(codex_home / "sessions" / "**" / "*.jsonl"), recursive=True))
    turns: list[Turn] = []
    for name in files:
        turns.extend(read(Path(name)))
    return turns
