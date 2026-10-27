"""Reads the agent's side of a test: the rollouts of the threads the suite's questions opened.

A rollout (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`) is one JSON record per line. Only
rollouts written since the run began are opened, and a rollout whose first user message does not
carry the run's marker is closed again at that line, so the owner's other threads are never read
past their first question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Session:
    """What a test needs from one thread's rollout."""

    path: Path
    first_user: str = ""
    users: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    aborted: int = 0
    completed: int = 0


def user_text(payload: dict[str, Any]) -> str | None:
    """The text of a `response_item` user message, or None for anything else."""
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    parts = payload.get("content") or []
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def call_command(payload: dict[str, Any]) -> tuple[str, str] | None:
    """A tool call's name and the command (or input) it ran, or None for anything else."""
    kind = payload.get("type")
    if kind == "function_call":
        arguments = payload.get("arguments") or ""
        try:
            value = json.loads(arguments)
        except (ValueError, TypeError):
            value = None
        if isinstance(value, dict):
            cmd = value.get("cmd") or value.get("command")
            if isinstance(cmd, list):
                return payload.get("name", ""), " ".join(map(str, cmd))
            if isinstance(cmd, str):
                return payload.get("name", ""), cmd
        return payload.get("name", ""), arguments
    if kind == "custom_tool_call":
        return payload.get("name", ""), payload.get("input") or ""
    return None


def read_session(lines: list[str], marker: str, path: Path) -> Session | None:
    """Parses one rollout's lines; None when its first user message does not carry `marker`.

    The first user message is the first one that is not an environment or instructions preamble
    (those start with `<`).
    """
    session = Session(path=path)
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload") or {}
        kind = record.get("type")
        if kind == "response_item":
            text = user_text(payload)
            if text is not None and not text.lstrip().startswith("<"):
                if not session.first_user:
                    if marker not in text:
                        return None
                    session.first_user = text
                session.users.append(text)
                continue
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                session.answers.append(
                    "".join(
                        part.get("text", "")
                        for part in payload.get("content") or []
                        if isinstance(part, dict)
                    )
                )
                continue
            call = call_command(payload)
            if call is not None:
                session.tools.append(call[0])
                session.commands.append(call[1])
        elif kind == "event_msg":
            if payload.get("type") == "turn_aborted":
                session.aborted += 1
            elif payload.get("type") == "task_complete":
                session.completed += 1
    return session if session.first_user else None


def recent_sessions(root: Path, since: float, marker: str) -> list[Session]:
    """The rollouts under `root` written since `since` whose first question carries `marker`."""
    found = []
    for path in sorted(root.rglob("rollout-*.jsonl")):
        try:
            if path.stat().st_mtime < since:
                continue
            with path.open(errors="replace") as handle:
                lines = []
                for line in handle:
                    lines.append(line)
                    if '"role":"user"' in line.replace(" ", "") and not read_partial_has_marker(
                        lines, marker
                    ):
                        break
        except OSError:
            continue
        session = read_session(lines, marker, path)
        if session is not None:
            found.append(session)
    return found


def read_partial_has_marker(lines: list[str], marker: str) -> bool:
    """Says whether reading should go on: the first real user message so far carries `marker` (or none yet)."""
    session = read_session(lines, marker, Path())
    if session is not None:
        return True
    # No real user message yet (only a preamble) also means "go on".
    for line in lines:
        try:
            payload = json.loads(line).get("payload") or {}
        except ValueError:
            continue
        text = user_text(payload)
        if text is not None and not text.lstrip().startswith("<"):
            return False
    return True
