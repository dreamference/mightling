#!/usr/bin/env python3
"""Turn-end compaction, measured in the `ling` TUI (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §4.3, §12.1).

§12.1 asks, before `mightling_turn_end_compaction` is switched on: a TUI session that grows past
65K, with and without it, recording the wall time of each turn-end compaction, whether a message
typed during one waits or interrupts it, and how many mid-turn compactions it saves; masking off.
This script runs that experiment and writes `results.json` and `report.md`.

v2 (2026-10-14). The first run (`docs/dev/turn-end-compaction/2026-10-13.md`) kept it off and found
why compactions were slow: every compaction request missed the server's prefix cache, because Codex
sends it without tools and the chat template puts them first. The owner approved a re-run with three
arms, all on a `ling` built with patch 0027 (the compaction request offers the turn's tools) and with
the summary capped at about 800 words (`DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS`, Codex's
`compact_prompt`): `off`; `on`, turn-end compaction at 20% of the usable window (not 26); and `drop`,
the same plus drop-old-outputs (`DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS`, patch 0026). The decision
rule is unchanged and applied to each of `on` and `drop` against `off`; each compaction's cache hit
(cached / input tokens of its request) is reported. The binary is always named with `--ling`.

Each session is a `ling` (`--ling`) on a pseudo-terminal (rendered by pyte), with a throwaway
CODEX_HOME and a throwaway Dreamference config file, in a scratch repository built for the task, fed
a scripted list of user turns. Nothing reads or writes `~/.mightling` or the user's config. The two
arms differ only in `model_post_turn_compact_threshold_percent` (0, or the launcher's value: 26 on
the default model); both pin `model_auto_compact_token_limit` to 94,144 (the §4.2 limit on this
machine's server) so a server with a different KV pool does not move the limit, and both run with
masking off and the ledger at its default (on). The setting is passed to Codex directly as well as
through `DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION`, so a binary built before the switch was merged
still runs the "on" arm.

Everything measured is read back from the session's rollout file afterwards: each compaction's wall
time (`ContextCompaction` item: `started_at_ms`, `completed_at_ms`), the context before and after,
whether it ran before a turn's first request (pre-turn), between two requests of a turn (mid-turn)
or after the turn's final answer (turn-end), every turn's wall time, and what the nightly audit
(`NightShiftCompactionAudit`, §10.3) says each summary kept. In the "on" arm the first turn-end
compaction of a session is probed: while the TUI shows "Compacting context", the next scripted turn
is typed and submitted (Enter in repetition 1, Tab, the queue key, in repetition 2), and the rollout
says whether the compaction finished, whether the turn was aborted, and when and whether the typed
message was taken up.

Runbook (2026-10-13; the report repeats it). From the checkout of `bench/turn-end-compaction`
(the worktree `~/PycharmProjects/dgxcoder-wt-tec`), with the project's interpreter::

    cd ~/PycharmProjects/dgxcoder-wt-tec && PY=~/PycharmProjects/dgxcoder/.venv/bin/python
    # 0. second-puffin's reference server must be up and alone (one model server at a time there):
    ssh second-puffin 'docker ps --format "{{.Names}}"'      # ling-serve stopped, m0-prod8000 listed
    ssh second-puffin 'docker start m0-prod8000'               # if it is not; then post a crew note
    # 1. pre-flight (talks to the server, clones click, checks the binary):
    $PY scripts/turn_end_compaction_bench.py preflight --host http://second-puffin.local:8000
    # 2. the run, in the background, with a log (about 3.5 h for 2 tasks x 2 arms x 2 repetitions):
    setsid nohup $PY scripts/turn_end_compaction_bench.py run \\
        --host http://second-puffin.local:8000 --reps 2 \\
        --out ~/.cache/dreamference/turn-end-compaction/2026-10-13 \\
        > ~/.cache/dreamference/turn-end-compaction/2026-10-13.log 2>&1 < /dev/null &
    # 3. the report is rewritten after every session; re-render at any time with
    $PY scripts/turn_end_compaction_bench.py report --out ~/.cache/dreamference/turn-end-compaction/2026-10-13

`dry-run` runs both arms against a stand-in model server on loopback (no model involved) to check
that the TUI is driven, both kinds of compaction are seen and the probe fires.
"""

import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Iterable

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
SCRIPTS: Final[Path] = ROOT / "scripts"

# The launcher's numbers (ling-rs/src/compaction.rs): the interactive limit is 60% of the KV pool,
# 94,144 with the default model on this machine's server; turn-end compaction sits at 70% of it,
# expressed as a percentage of Codex's usable window (95% of the model's window).
DEFAULT_LIMIT: Final[int] = 94_144
TURN_END_SHARE_PERCENT: Final[int] = 70
EFFECTIVE_WINDOW_PERCENT: Final[int] = 95
MAX_LIMIT_PERCENT_OF_WINDOW: Final[int] = 90
DEFAULT_WINDOW: Final[int] = 262_144
LIMIT_KEY: Final[str] = "model_auto_compact_token_limit"
TURN_END_KEY: Final[str] = "model_post_turn_compact_threshold_percent"
TURN_END_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION"
MASK_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_MASK"
# v2 (2026-10-14): three arms. "on" is turn-end compaction tuned as the owner approved (threshold 20,
# summary capped); "drop" is the same plus drop-old-outputs (patch 0026). Every arm runs on a `ling`
# with patch 0027 (the compaction request offers the turn's tools, so it hits the prefix cache) and
# with the summary cap, so the arms differ only in the two switches.
ARMS: Final[tuple] = ("off", "on", "drop")
TURN_END_ARMS: Final[tuple] = ("on", "drop")
ARM_TITLES: Final[dict] = {"off": "off", "on": "turn-end", "drop": "turn-end + drop old outputs"}
DROP_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS"
DROP_LOG: Final[str] = "masking/dropped/log.jsonl"
SUMMARY_WORDS_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS"
COMPACTION_TOOLS_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS"
# The owner's values for v2: turn-end at 20% of the usable window (49,808 tokens on the default model,
# not the launcher's 26 = 64,750), and a summary of about 800 words.
TURN_END_PERCENT_V2: Final[int] = 20
SUMMARY_WORDS: Final[int] = 800

# What the TUI draws (codex-rs/tui): the composer, the busy line, the compaction status header.
READY_MARKERS: Final[tuple] = (">_ Mightling", "›")
BUSY_MARKER: Final[str] = "esc to interrupt"
COMPACTING_MARKER: Final[str] = "Compacting context"
SIGN_IN_MARKER: Final[str] = "Sign in with ChatGPT"
ROWS: Final[int] = 60
COLS: Final[int] = 200
PASTE_START: Final[str] = "\x1b[200~"
PASTE_END: Final[str] = "\x1b[201~"
PROBE_KEYS: Final[dict] = {"enter": "\r", "tab": "\t"}

# The decision rule's numbers (report "Decision rule").
MAX_MEDIAN_TURN_END_S: Final[float] = 60.0
MAX_TURN_END_S: Final[float] = 120.0
MAX_WALL_INCREASE: Final[float] = 0.25
MAX_HIDDEN_DROP: Final[float] = 1.0
MISSED_MARGIN_PERCENT: Final[int] = 5

COMPACTION_PROMPT_MARK: Final[str] = "CONTEXT CHECKPOINT COMPACTION"
TITLE_REQUEST_MARK: Final[str] = "task title"
STRAY_TOOL_CALL: Final[str] = "<tool_call>"
TEST_SUMMARY: Final[re.Pattern] = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")
DEFAULT_OUT_PARENT: Final[Path] = Path.home() / ".cache" / "dreamference" / "turn-end-compaction"
SOURCE_CACHE: Final[Path] = DEFAULT_OUT_PARENT / "sources"


# --------------------------------------------------------------------------------------------------
# The launcher's arithmetic

def turn_end_percent(window: int, limit: int) -> int | None:
    """The percentage the launcher gives `model_post_turn_compact_threshold_percent` (compaction.rs).

    Args:
        window: The model's context window, as `/v1/models` reports it.
        limit: The compaction limit the session uses.

    Returns:
        int | None: 26 for 262,144 and 94,144; None for a window of 0 or a share at or above 100.
    """
    usable = window * EFFECTIVE_WINDOW_PERCENT // 100
    if usable == 0:
        return None
    target = min(limit, window * MAX_LIMIT_PERCENT_OF_WINDOW // 100) * TURN_END_SHARE_PERCENT // 100
    percent = max(target * 100 // usable, 1)
    return percent if percent < 100 else None


def turn_end_threshold_tokens(window: int, percent: int) -> int:
    """The context, in tokens, at which a turn ends in a compaction (Codex: `active * 100 >= usable * p`).

    Args:
        window: The model's context window.
        percent: The turn-end percentage.

    Returns:
        int: The smallest context that triggers it: 64,750 for 262,144 at 26.
    """
    usable = window * EFFECTIVE_WINDOW_PERCENT // 100
    return -(-usable * percent // 100)


# --------------------------------------------------------------------------------------------------
# Reading a rollout

def parse_time_ms(stamp: str) -> int | None:
    """Epoch milliseconds of a rollout timestamp (`2026-10-01T18:02:28.753Z`), or None."""
    if not stamp:
        return None
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def read_records(path: Path) -> list[dict[str, Any]]:
    """The JSON records of a rollout file, skipping a torn last line.

    Args:
        path: The rollout.

    Returns:
        list[dict[str, Any]]: Each record, with `_ms` (epoch milliseconds) and `_i` (position) added.
    """
    records = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                record["_ms"] = parse_time_ms(str(record.get("timestamp", "")))
                record["_i"] = len(records)
                records.append(record)
    return records


def find_rollout(codex_home: Path) -> Path | None:
    """The session's rollout under `codex_home/sessions` (the most recently written, if several)."""
    found = sorted((codex_home / "sessions").rglob("rollout-*.jsonl"), key=lambda path: path.stat().st_mtime)
    return found[-1] if found else None


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event(record: dict[str, Any]) -> str | None:
    return _payload(record).get("type") if record.get("type") == "event_msg" else None


def is_model_output(record: dict[str, Any]) -> bool:
    """Whether a record is something the model produced in a turn: an assistant message or a tool call."""
    if record.get("type") != "response_item":
        return False
    payload = _payload(record)
    kind = payload.get("type")
    if kind == "message":
        return payload.get("role") == "assistant"
    return kind in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call")


def is_final_answer(record: dict[str, Any]) -> bool:
    """Whether a record is an assistant message (as opposed to a tool call or its output)."""
    payload = _payload(record)
    return record.get("type") == "response_item" and payload.get("type") == "message" \
        and payload.get("role") == "assistant"


def message_text(payload: dict[str, Any]) -> str:
    """The text of a message item's content parts."""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content or [] if isinstance(part, dict))


def last_response_item(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest `response_item` record, or None."""
    for record in reversed(records):
        if record.get("type") == "response_item":
            return record
    return None


def turn_end_compaction_showing(records: list[dict[str, Any]], shown_ms: int | None) -> bool:
    """Whether the compaction on screen is a turn-end one: the newest response item is an assistant
    message written before "Compacting context" appeared.

    A mid-turn compaction also ends in an assistant message, its summary, written as the compaction
    finishes; the marker can still be on screen then. On 2026-10-13 the probe took that summary for a
    final answer and typed the next turn into the middle of one (night-shift-on-1).

    Args:
        records: The rollout read so far.
        shown_ms: When the marker was first seen for this compaction (epoch milliseconds).

    Returns:
        bool: True for a turn-end compaction.
    """
    last = last_response_item(records)
    if last is None or not is_final_answer(last) or shown_ms is None:
        return False
    return last.get("_ms") is not None and last["_ms"] < shown_ms


def context_tokens(record: dict[str, Any]) -> tuple[int, int] | None:
    """`(input, total)` of a `token_count` event's last usage, or None for another record."""
    if _event(record) != "token_count":
        return None
    info = _payload(record).get("info") or {}
    last = info.get("last_token_usage") or {}
    if not last:
        return None
    return int(last.get("input_tokens") or 0), int(last.get("total_tokens") or 0)


def model_window(records: list[dict[str, Any]]) -> int | None:
    """The usable window Codex reports in its `token_count` events (95% of the model's)."""
    for record in records:
        if _event(record) == "token_count":
            window = (_payload(record).get("info") or {}).get("model_context_window")
            if window:
                return int(window)
    return None


def turns_of(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turns of a rollout, in order.

    Args:
        records: The rollout's records.

    Returns:
        list[dict[str, Any]]: Per turn: `turn_id`, `start_i`, `end_i` (None while running), `status`
            (`complete`, `aborted` or `running`), `started_ms`, `ended_ms`, `wall_s` (Codex's own
            `duration_ms` where given), `context_at_end` (the context when it ended: the last request's
            total, or Codex's estimate after a compaction).
    """
    turns: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    compacted_in: dict[str, Any] | None = None
    for record in records:
        event = _event(record)
        payload = _payload(record)
        if event == "task_started":
            turn = {"turn_id": payload.get("turn_id"), "start_i": record["_i"], "end_i": None,
                    "status": "running", "started_ms": record["_ms"], "ended_ms": None, "wall_s": None,
                    "context_at_end": None, "reason": None}
            turns.append(turn)
            by_id[str(payload.get("turn_id"))] = turn
        elif event in ("task_complete", "turn_aborted"):
            turn = by_id.get(str(payload.get("turn_id"))) or (turns[-1] if turns and turns[-1]["end_i"] is None else None)
            if turn is None:
                continue
            turn["end_i"] = record["_i"]
            turn["ended_ms"] = record["_ms"]
            turn["status"] = "complete" if event == "task_complete" else "aborted"
            turn["reason"] = payload.get("reason")
            duration = payload.get("duration_ms")
            if duration is not None:
                turn["wall_s"] = round(int(duration) / 1000, 1)
            elif turn["started_ms"] and record["_ms"]:
                turn["wall_s"] = round((record["_ms"] - turn["started_ms"]) / 1000, 1)
        elif record.get("type") == "compacted" and turns and turns[-1]["end_i"] is None:
            compacted_in = turns[-1]
        else:
            tokens = context_tokens(record)
            running = turns[-1] if turns and turns[-1]["end_i"] is None else None
            if tokens and running is not None and (tokens[0] > 0 or compacted_in is running):
                # After a compaction, Codex's own estimate (input 0) is the context until the next request.
                running["context_at_end"] = tokens[1]
                compacted_in = None
    return turns


def _turn_at(turns: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    for turn in reversed(turns):
        if turn["start_i"] <= index:
            return turn
    return None


def compactions_of(records: list[dict[str, Any]], turns: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every compaction in a rollout, classified and timed.

    A compaction is a `compacted` record. Its wall time comes from the `ContextCompaction` item that
    completes with it. Its class comes from where it sits in its turn: with no model output before it
    in the turn it is `pre-turn`; when it began right after an assistant message (the turn's final
    answer) and recorded no summary message of its own, `turn-end` (Codex runs it after the final
    answer, and commits a post-turn summary only on success); else `mid-turn`. A message the user
    typed during a turn-end compaction can continue the same turn after it, so what follows the
    compaction does not decide its class.

    Args:
        records: The rollout's records.
        turns: `turns_of(records)`, when already computed.

    Returns:
        list[dict[str, Any]]: Per compaction: `number`, `turn_id`, `kind`, `started_ms`,
            `completed_ms`, `wall_s`, `context_before`, `context_after`, `summary_chars`,
            `summary_output_tokens`, `input_tokens`, `cached_tokens`, `cache_hit` (cached / input of
            the compaction request; None when the rollout holds no usage of its own), `stray_tool_call`.
    """
    turns = turns_of(records) if turns is None else turns
    items = [record for record in records if _event(record) == "item_completed"
             and (_payload(record).get("item") or {}).get("type") == "ContextCompaction"]
    found = []
    for number, (index, record) in enumerate(
            ((record["_i"], record) for record in records if record.get("type") == "compacted"), start=1):
        payload = _payload(record)
        # The `ContextCompaction` item that completes with it: the nearest one not yet taken.
        item = min(items, key=lambda candidate: abs(candidate["_i"] - index), default=None)
        if item is not None:
            items.remove(item)
            started_ms = _payload(item).get("started_at_ms")
            completed_ms = _payload(item).get("completed_at_ms")
        else:
            started_ms = completed_ms = None
        if started_ms is None:
            # No item: the compaction began after the last record that is not part of it.
            earlier = [other for other in records[:index] if other.get("type") not in ("token_usage_record",)
                       and _event(other) != "token_count"]
            if earlier and is_final_answer(earlier[-1]) and earlier[-1]["_ms"] and record["_ms"] \
                    and record["_ms"] - earlier[-1]["_ms"] < 1000:
                earlier = earlier[:-1]  # a mid-turn summary is recorded just before `compacted`
            started_ms = earlier[-1]["_ms"] if earlier else record["_ms"]
            completed_ms = record["_ms"]
        turn = _turn_at(turns, index)
        lower = turn["start_i"] if turn else 0
        upper = turn["end_i"] if turn and turn["end_i"] is not None else len(records)
        before = [other for other in records[lower:index] if is_model_output(other)
                  and other["_ms"] is not None and other["_ms"] < started_ms]
        # Only the local path's mid-turn and pre-turn compactions record their summary as an assistant
        # message before `compacted`; a post-turn one commits it only on success (compact.rs).
        summary_recorded = any(is_final_answer(other) for other in records[lower:index]
                               if other["_ms"] is not None and other["_ms"] >= started_ms)
        if not before:
            kind = "pre-turn"
        elif not summary_recorded and is_final_answer(before[-1]):
            kind = "turn-end"
        else:
            kind = "mid-turn"
        usage_record = payload.get("latest_token_usage_record") or {}
        usage = usage_record.get("usage") or {}
        is_compaction_usage = bool(usage_record.get("response_id")) \
            and usage_record.get("response_id") == payload.get("compaction_response_id")
        prior = [tokens for other in records[:index] if other["_ms"] is not None and other["_ms"] <= started_ms
                 and (tokens := context_tokens(other)) and tokens[0] > 0]
        context_before = int(usage.get("input_tokens") or 0) if is_compaction_usage else (prior[-1][1] if prior else None)
        # The compaction request's own usage: how much of its prompt the server's prefix cache served
        # (on 2026-10-13, 0 in 12 of 13: the request had no tools, and the template puts them first).
        input_tokens = int(usage.get("input_tokens") or 0) if is_compaction_usage else None
        cached_tokens = int(usage.get("cached_input_tokens") or 0) if is_compaction_usage else None
        following = [tokens for other in records[index + 1:] if (tokens := context_tokens(other))]
        summary = str(payload.get("message", ""))
        found.append({
            "number": number,
            "turn_id": turn["turn_id"] if turn else None,
            "kind": kind,
            "started_ms": started_ms,
            "completed_ms": completed_ms,
            "wall_s": round((completed_ms - started_ms) / 1000, 1) if started_ms and completed_ms else None,
            "context_before": context_before,
            "context_after": following[0][1] if following else None,
            "summary_chars": len(summary),
            "summary_output_tokens": int(usage.get("output_tokens") or 0) if is_compaction_usage else None,
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit": round(cached_tokens / input_tokens, 3) if input_tokens else None,
            "stray_tool_call": STRAY_TOOL_CALL in summary,
        })
    return found


def missed_turn_ends(turns: list[dict[str, Any]], compactions: list[dict[str, Any]], threshold: int) -> list[str]:
    """Completed turns that ended above the turn-end threshold with no compaction in them.

    In the "on" arm each is a sign the setting did not reach Codex (or input was queued at that
    moment); in the "off" arm they are where a turn-end compaction would have run.

    Args:
        turns: `turns_of(...)`.
        compactions: `compactions_of(...)`.
        threshold: `turn_end_threshold_tokens(...)`.

    Returns:
        list[str]: The turn ids.
    """
    compacted = {compaction["turn_id"] for compaction in compactions}
    return [str(turn["turn_id"]) for turn in turns if turn["status"] == "complete"
            and (turn["context_at_end"] or 0) >= threshold and turn["turn_id"] not in compacted]


def probe_outcome(records: list[dict[str, Any]], turns: list[dict[str, Any]], compactions: list[dict[str, Any]],
                  probe: dict[str, Any]) -> dict[str, Any]:
    """What happened to a message typed while a turn-end compaction ran.

    Args:
        records: The rollout's records.
        turns: `turns_of(records)`.
        compactions: `compactions_of(records)`.
        probe: The driver's note: `sent_ms` (epoch ms), `key` (`enter` or `tab`), `text`.

    Returns:
        dict[str, Any]: `compaction` (its number, or None when none was running at `sent_ms`),
            `compaction_kind`, `compaction_finished`, `compaction_wall_s`, `typed_after_s` (into the
            compaction), `turn_aborted` (the compacting turn ended aborted), `delivered`,
            `delivered_in` (`same turn`, `next turn` or None), `waited_s` (from typing to the turn
            that took the message up), `verdict` (`waited`: the compaction finished and the message
            was taken up after it; `interrupted`: the compaction did not finish; `lost`: the message
            never reached the model).
    """
    sent = int(probe["sent_ms"])
    running = [c for c in compactions if c["started_ms"] and c["started_ms"] - 1000 <= sent
               and (c["completed_ms"] is None or sent <= c["completed_ms"] + 1000)]
    compaction = running[0] if running else None
    compacting_turn = next((turn for turn in turns if compaction and turn["turn_id"] == compaction["turn_id"]), None)
    if compacting_turn is None:
        compacting_turn = next((turn for turn in reversed(turns) if turn["started_ms"] and turn["started_ms"] <= sent), None)
    needle = " ".join(str(probe["text"]).split())[:60]
    delivered = None
    for record in records:
        if record["_ms"] is None or record["_ms"] < sent - 1000:
            continue
        payload = _payload(record)
        if record.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            if needle and needle in " ".join(message_text(payload).split()):
                delivered = record
                break
    delivered_turn = _turn_at(turns, delivered["_i"]) if delivered else None
    if delivered_turn is None:
        delivered_in = None
    elif compacting_turn is not None and delivered_turn["turn_id"] == compacting_turn["turn_id"]:
        delivered_in = "same turn"
    else:
        delivered_in = "next turn"
    aborted = bool(compacting_turn and compacting_turn["status"] == "aborted")
    finished = bool(compaction and compaction["completed_ms"])
    # The driver types only while the TUI shows "Compacting context", so a compaction that left no
    # record around that moment was cut short.
    if delivered is None:
        verdict = "lost"
    elif aborted or not finished:
        verdict = "interrupted"
    else:
        verdict = "waited"
    taken_ms = (delivered_turn["started_ms"] if delivered_in == "next turn" else delivered["_ms"]) if delivered else None
    return {
        "key": probe.get("key"),
        "compaction": compaction["number"] if compaction else None,
        "compaction_kind": compaction["kind"] if compaction else None,
        "compaction_finished": finished,
        "compaction_wall_s": compaction["wall_s"] if compaction else None,
        "typed_after_s": round((sent - compaction["started_ms"]) / 1000, 1) if compaction else None,
        "turn_aborted": aborted,
        "delivered": delivered is not None,
        "delivered_in": delivered_in,
        "waited_s": round((taken_ms - sent) / 1000, 1) if taken_ms else None,
        "verdict": verdict,
    }


def inputs_missing(records: list[dict[str, Any]], typed: dict[int, str]) -> list[int]:
    """The typed turns whose whole text is not in any user message of the rollout (a paste cut short).

    Args:
        records: The rollout's records.
        typed: Turn index to the text typed.

    Returns:
        list[int]: The indices missing, in order.
    """
    seen = [" ".join(message_text(_payload(record)).split()) for record in records
            if record.get("type") == "response_item" and _payload(record).get("type") == "message"
            and _payload(record).get("role") == "user"]
    return [index for index, text in sorted(typed.items()) if not any(" ".join(text.split()) in message for message in seen)]


def audit_verdicts(rollout: Path) -> list[dict[str, Any]]:
    """The nightly compaction audit's judgement of each summary in a rollout (§10.3, §12.2).

    Args:
        rollout: The rollout file.

    Returns:
        list[dict[str, Any]]: One per `compacted` record, in order: `kept` ({group: [kept, of]}),
            `lost` ({group: [identifier]}), `ledger_covered`, `stray_tool_call`, `verdict` (text).
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from dreamference.night_shift.night_shift_compaction_audit import NightShiftCompactionAudit

    verdicts = []
    for finding in NightShiftCompactionAudit.audit_rollout(rollout):
        lost = {group: items for group, items in finding["lost"].items() if items}
        kept = {group: list(counts) for group, counts in finding["kept"].items()}
        if finding["stray_tool_call"]:
            verdict = "stray tool call, not a summary"
        elif not lost:
            verdict = "kept all" if any(of for _, of in kept.values()) else "nothing to keep"
        else:
            verdict = "lost " + "; ".join(f"{group} {len(items)}/{kept[group][1]}" for group, items in lost.items())
            if finding["ledger_covered"]:
                verdict += f" (ledger covered {finding['ledger_covered']})"
        verdicts.append({"kept": kept, "lost": lost, "ledger_covered": finding["ledger_covered"],
                         "stray_tool_call": finding["stray_tool_call"], "verdict": verdict})
    return verdicts


def drops_of(codex_home: Path | None) -> dict[str, int]:
    """What drop-first compaction did in a session: the log `ling_masking::dropping` keeps in its
    CODEX_HOME (`masking/dropped/log.jsonl`, one line per attempt, taken or declined).

    A drop that is taken replaces a summary (no `compacted` record); a declined one lets the summary
    run as before.

    Args:
        codex_home: The session's CODEX_HOME, or None.

    Returns:
        dict[str, int]: `attempts`, `drops` (taken), `declined`, `dropped_outputs`.
    """
    found = {"attempts": 0, "drops": 0, "declined": 0, "dropped_outputs": 0}
    if codex_home is None:
        return found
    for name in (DROP_LOG + ".old", DROP_LOG):
        path = codex_home / name
        if not path.is_file():
            continue
        for line in path.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            found["attempts"] += 1
            if entry.get("accepted"):
                found["drops"] += 1
                found["dropped_outputs"] += int(entry.get("outputs") or 0)
            else:
                found["declined"] += 1
    return found


def analyze_session(rollout: Path, threshold: int | None, probe: dict[str, Any] | None,
                    codex_home: Path | None = None) -> dict[str, Any]:
    """Everything the report needs from one session's rollout.

    Args:
        rollout: The rollout file.
        threshold: The turn-end threshold in tokens (for `missed_turn_ends`), or None.
        probe: The driver's probe note, or None when no probe was typed.
        codex_home: The session's CODEX_HOME, for drop-first compaction's log.

    Returns:
        dict[str, Any]: `turns`, `compactions` (each with its audit verdict), `counts` by kind,
            `peak_context`, `usable_window`, `missed_turn_ends`, `probe`, `drops`.
    """
    records = read_records(rollout)
    turns = turns_of(records)
    compactions = compactions_of(records, turns)
    try:
        verdicts = audit_verdicts(rollout)
    except Exception as error:  # noqa: BLE001 - the audit must not lose the measurement
        verdicts = [{"verdict": f"audit failed: {error}"}] * len(compactions)
    for compaction, verdict in zip(compactions, verdicts):
        compaction["audit"] = verdict
    peaks = [tokens[1] for record in records if (tokens := context_tokens(record)) and tokens[0] > 0]
    counts = {kind: sum(1 for c in compactions if c["kind"] == kind) for kind in ("pre-turn", "mid-turn", "turn-end")}
    return {
        "rollout": str(rollout),
        "turns": [{key: turn[key] for key in ("turn_id", "status", "reason", "wall_s", "context_at_end")} for turn in turns],
        "compactions": compactions,
        "counts": counts,
        "peak_context": max(peaks) if peaks else None,
        "usable_window": model_window(records),
        "missed_turn_ends": missed_turn_ends(turns, compactions, threshold) if threshold else [],
        # Clearly above it: my reading of the context and Codex's estimate can differ near the line.
        "missed_turn_ends_clear": missed_turn_ends(turns, compactions, threshold * (100 + MISSED_MARGIN_PERCENT) // 100)
        if threshold else [],
        "probe": probe_outcome(records, turns, compactions, probe) if probe else None,
        "drops": drops_of(codex_home),
    }


# --------------------------------------------------------------------------------------------------
# Tasks

@dataclass
class Task:
    """One scripted session: how to build its repository, the user's turns, and how to grade it."""

    name: str
    title: str
    turns: list[str]
    setup: Callable[[Path, Path], None]
    grade: Callable[[Path, str], dict[str, Any]]
    sources: list[str] = field(default_factory=list)


NIGHT_SHIFT_BASE: Final[str] = "ac160aca0b3b0c6bbd49588bc5963d16d8e2373c"  # main, 2026-10-02: Phase 0's base
NIGHT_SHIFT_PATHS: Final[tuple] = ("dreamference", "tests/conftest.py", "tests/test_night_shift.py", "setup.py", ".gitignore")
NIGHT_SHIFT_ACCEPTANCE: Final[Path] = SCRIPTS / "turn_end_compaction_night_shift_acceptance.py"
CLICK_URL: Final[str] = "https://github.com/pallets/click.git"
CLICK_TAG: Final[str] = "8.1.7"
CLICK_COMMIT: Final[str] = "874ca2bc1c30d93a4ac6e36a15ed685eafe89097"
CLICK_ACCEPTANCE: Final[Path] = SCRIPTS / "turn_end_compaction_click_acceptance.py"

NIGHT_SHIFT_TEST = "`{python} -m pytest tests/test_night_shift.py -q -p no:cacheprovider`"
NIGHT_SHIFT_TURNS: Final[list] = [
    "Read every module under dreamference/night_shift/ in full, then tests/test_night_shift.py, then the part "
    "of dreamference/cli/dreamference_cli_controller.py that parses and dispatches `puffin-admin night`. Then "
    "explain to me how a queued task goes from the queue through the runner and the task run to the morning "
    "report: the classes and methods involved, in order, and where the settings come in. Do not change "
    "anything yet.",
    "First change, a dry run. Add a keyword argument `dry_run: bool = False` to `NightShiftRunner.run` in "
    "dreamference/night_shift/night_shift_runner.py. When it is true the run must not start any task, must not "
    "change any task file, must not write a report and must not wait for the model to be idle; it prints one "
    "line per pending task, in the order the tasks would start (the round-robin order), each line containing "
    "the task's id, its repository and the test command that would be detected for it, and returns 0. Add a "
    "test for it to tests/test_night_shift.py (do not rename or remove any existing test or helper in that "
    f"file) and run the file with {NIGHT_SHIFT_TEST}.",
    "Second change, a cap on tasks per night. Add a setting `max_tasks` to `NightShiftSettings` (key "
    "`max_tasks` in the `[night]` table, an integer, default 0 meaning no cap). When it is greater than 0, one "
    "night run starts at most that many tasks; the others stay `queued`, and the report's notes say how many "
    f"were left for the cap. Add a test and run the file with {NIGHT_SHIFT_TEST}.",
    "Two smaller changes. Totals in the report: `NightShiftReport.render` adds one line that begins with "
    "`Totals:` and lists the count of tasks per status, for example `Totals: 2 done, 1 stalled`, in the order "
    "the statuses first appear in the task list. And the command line: `puffin-admin night run` gains a "
    "`--dry-run` flag (in dreamference/cli/dreamference_cli_controller.py) that passes `dry_run=True` to "
    f"`NightShiftRunner.run`. Add a test for each and run the file with {NIGHT_SHIFT_TEST}.",
    "Last change, a machine-readable report. Add `NightShiftReport.to_json(started, tasks, notes)`, which "
    "returns a JSON string with the keys `date` (the run's date as `YYYY-MM-DD`), `totals` (an object mapping "
    "each status to its count), `tasks` (a list with, for each task, its `id`, `repo`, `status`, and where the "
    "task has them `branch` and `test_result`) and `notes` (the list of notes). A night run that writes the "
    "Markdown report also writes this beside it, under the same name with the extension `.json`. Add a test "
    f"and run the file with {NIGHT_SHIFT_TEST}.",
    f"Run the whole file again with {NIGHT_SHIFT_TEST} and fix whatever fails until every test passes. Then "
    "review your work: show `git diff --stat`, read each file you changed again in full, and check that every "
    "new public method has a Google-style docstring with Args and Returns like the existing ones. Fix what "
    "does not match. Do not commit.",
    "Thanks. List the files you changed, with one line each on what changed in it.",
]

CLICK_TEST = "`{python} -m pytest tests -q -p no:cacheprovider`"
CLICK_TURNS: Final[list] = [
    "Read src/click/core.py, src/click/parser.py, src/click/decorators.py and src/click/types.py in full. Then "
    "explain to me how a value given on the command line for an option travels from `@click.option` to the "
    "command's callback: the classes and methods involved, in order, and where type conversion, defaults and "
    "environment variables come in. Do not change anything yet.",
    "Refactor: move the contents of src/click/parser.py into a new private module src/click/_optparse.py. Keep "
    "src/click/parser.py as a thin module that re-exports the same public names from the new module and emits "
    "a DeprecationWarning when it is imported. Update every import of the parser inside src/click to use the "
    "new module, so that `import click` emits no warning. Add tests in tests/test_parser.py that importing "
    "click.parser warns and that `import click` does not. Run the whole suite with "
    f"{CLICK_TEST} and fix what fails.",
    "Add a `deprecated` parameter to `click.Option` (False, True, or a string). When an option marked "
    "deprecated is given on the command line, print `DeprecationWarning: The option '<name>' is deprecated.` "
    "to stderr, followed by the string when one was given, and start its help text in `--help` with "
    "`(DEPRECATED)`. Make the change in src/click/core.py and make sure `@click.option(..., deprecated=...)` "
    "passes it through. Add tests in tests/test_options.py for the message, the custom string and the help "
    f"text. Run the whole suite with {CLICK_TEST} and fix what fails.",
    "Add support for an environment variable `CLICK_HELP_WIDTH`: when it is set to an integer, help output is "
    "wrapped at that width, unless the context was given an explicit `terminal_width` or `max_content_width`. "
    "Find where the width is decided (src/click/core.py and src/click/formatting.py), make the change there, "
    "document it in the docstring of `Context`, and add tests in tests/test_formatting.py. Run the whole suite "
    f"with {CLICK_TEST} and fix what fails.",
    f"Run the whole suite once more with {CLICK_TEST}. Then review your work: show `git diff --stat`, read "
    "every file you changed again in full and look for leftovers (unused imports, stale docstrings, the old "
    "module name in comments). Fix what you find and run the suite again. Do not commit.",
    "Thanks. List the files you changed, with one line each on what changed in it.",
]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Runs git in `repo` with a fixed identity."""
    return subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost", *args],
                          cwd=repo, check=check, capture_output=True, text=True)


def setup_night_shift(repo: Path, cache: Path) -> None:
    """Task A's repository: this repository's `dreamference/` and Night Shift tests at Phase 0's base.

    Args:
        repo: Where to build it (created).
        cache: Unused: the source is this checkout's own history.
    """
    repo.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", NIGHT_SHIFT_BASE, *NIGHT_SHIFT_PATHS],
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(repo)], input=archive.stdout, check=True)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")


def click_mirror(cache: Path) -> Path:
    """A local clone of click at `CLICK_TAG`, made once under `cache` and checked against `CLICK_COMMIT`."""
    mirror = cache / f"click-{CLICK_TAG}"
    if not (mirror / ".git").is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(mirror, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", CLICK_TAG, CLICK_URL, str(mirror)],
                       check=True, capture_output=True, timeout=300)
    head = git(mirror, "rev-parse", "HEAD").stdout.strip()
    if head != CLICK_COMMIT:
        raise RuntimeError(f"click {CLICK_TAG} is {head}, expected {CLICK_COMMIT}: refusing to measure on other code")
    return mirror


def setup_click(repo: Path, cache: Path) -> None:
    """Task B's repository: click 8.1.7, with pytest pointed at `src/` and warnings not raised as errors.

    Args:
        repo: Where to build it (created).
        cache: Where the clone of click is kept between sessions.
    """
    mirror = click_mirror(cache)
    subprocess.run(["git", "clone", "-q", str(mirror), str(repo)], check=True, capture_output=True)
    config = repo / "setup.cfg"
    text = config.read_text()
    text = text.replace("filterwarnings =\n    error\n", "filterwarnings =\n    default\npythonpath = src\n")
    config.write_text(text)
    # 8.1.7's light-import test lists the standard modules `import click` may load, and Python 3.12
    # loads others (`collections.abc`, `__future__`): a failure the task did not cause, and one the
    # agent would stop to chase. Skipped in the base.
    imports = repo / "tests" / "test_imports.py"
    imports.write_text("import pytest\n" + imports.read_text().replace(
        "def test_light_imports", '@pytest.mark.skip(reason="bench base: depends on the interpreter")\ndef test_light_imports', 1))
    git(repo, "commit", "-qam", "bench base: pytest imports src/, warnings are not errors, Python 3.12 imports")


def _pytest_counts(output: str) -> dict[str, int]:
    lines = [line for line in output.splitlines() if TEST_SUMMARY.search(line)]
    counts: dict[str, int] = {}
    for number, word in TEST_SUMMARY.findall(lines[-1] if lines else ""):
        counts[word.rstrip("s") if word.startswith("error") else word] = int(number)
    return counts


def _run_pytest(repo: Path, python: str, *args: str) -> dict[str, Any]:
    try:
        done = subprocess.run([python, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"], cwd=repo,
                              capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"rc": None, "counts": {}, "tail": "timed out"}
    output = done.stdout + done.stderr
    return {"rc": done.returncode, "counts": _pytest_counts(output), "tail": output.strip().splitlines()[-1:] or [""]}


def grade_with(acceptance: Path, own: tuple) -> Callable[[Path, str], dict[str, Any]]:
    """A grader: the hidden acceptance tests (copied in after the session, then removed), and the agent's own."""
    def grade(repo: Path, python: str) -> dict[str, Any]:
        hidden_path = repo / "tests" / "test_bench_hidden.py"
        shutil.copy(acceptance, hidden_path)
        try:
            hidden = _run_pytest(repo, python, str(hidden_path.relative_to(repo)))
        finally:
            hidden_path.unlink(missing_ok=True)
        return {"hidden": hidden, "own": _run_pytest(repo, python, *own),
                "changed_files": [line[3:] for line in git(repo, "status", "--porcelain", check=False).stdout.splitlines()]}
    return grade


TASKS: Final[dict] = {
    "night-shift": Task("night-shift", "Night Shift: five changes with tests (Phase 0's task, in six turns)",
                        NIGHT_SHIFT_TURNS, setup_night_shift,
                        grade_with(NIGHT_SHIFT_ACCEPTANCE, ("tests/test_night_shift.py",))),
    "click": Task("click", "click 8.1.7: a module move, a feature and an option, with tests",
                  CLICK_TURNS, setup_click, grade_with(CLICK_ACCEPTANCE, ("tests",)), [CLICK_URL]),
}


# --------------------------------------------------------------------------------------------------
# The TUI

class RolloutTail:
    """Reads a session's rollout as it grows, so the driver can see turns start and end."""

    def __init__(self, codex_home: Path):
        self.codex_home = codex_home
        self.path: Path | None = None
        self.offset = 0
        self.partial = b""
        self.records: list[dict[str, Any]] = []

    def poll(self) -> None:
        """Reads whatever was appended since the last call."""
        if self.path is None:
            self.path = find_rollout(self.codex_home)
            if self.path is None:
                return
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
        self.offset += len(chunk)
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record["_ms"] = parse_time_ms(str(record.get("timestamp", "")))
            record["_i"] = len(self.records)
            self.records.append(record)

    def count(self, *events: str) -> int:
        """How many `event_msg` records of these types have been read."""
        return sum(1 for record in self.records if _event(record) in events)


class TuiSession:
    """One `ling` TUI on a pseudo-terminal, its screen rendered by pyte."""

    def __init__(self, ling: str, args: list[str], cwd: Path, env: dict[str, str], log: Path):
        import pexpect
        import pyte

        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.log = log.open("ab")
        self.child = pexpect.spawn(ling, args, cwd=str(cwd), env=env, dimensions=(ROWS, COLS))
        self._pexpect = pexpect

    def pump(self, seconds: float) -> bool:
        """Renders output for `seconds`; False once the process has exited."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self.child.read_nonblocking(65536, timeout=0.2)
            except self._pexpect.TIMEOUT:
                continue
            except self._pexpect.EOF:
                return False
            self.log.write(data)
            self.stream.feed(data)
        return True

    def text(self) -> str:
        """The screen as text.

        Read from the buffer, not `Screen.display`: pyte 0.8.2's `display` raises IndexError on the
        empty cell that follows a wide character (the model answers in Chinese at times, 2026-10-13).
        """
        columns = range(self.screen.columns)
        return "\n".join("".join(self.screen.buffer[y][x].data for x in columns).rstrip()
                         for y in range(self.screen.lines))

    def wait_for(self, predicate: Callable[[str], bool], timeout: float) -> bool:
        """Pumps until `predicate(screen)` holds; False on timeout or exit."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            alive = self.pump(0.5)
            if predicate(self.text()):
                return True
            if not alive:
                return False
        return False

    def type_text(self, text: str, key: str = "\r") -> None:
        """Pastes `text` into the composer (as a bracketed paste, so no character opens a popup) and presses `key`."""
        self.child.send(PASTE_START + text + PASTE_END)
        self.pump(1.0)
        self.child.send(key)

    def close(self) -> None:
        """Leaves the TUI and ends the process."""
        if self.child.isalive():
            self.child.sendcontrol("c")
            self.pump(0.5)
            self.child.sendcontrol("c")
            self.pump(1.5)
        self.child.terminate(force=True)
        self.log.close()


def ling_binary(explicit: str | None) -> Path:
    """The `ling` to run: `--ling`, else the one on PATH, else `~/.local/bin/ling`."""
    candidate = explicit or shutil.which("ling") or str(Path.home() / ".local" / "bin" / "ling")
    path = Path(candidate).expanduser()
    if not path.exists():
        raise SystemExit(f"❌ no ling at {path}: build it (ling-admin codex build) or pass --ling")
    return path


# What v2 needs compiled in: each switch's name is a string only the code that reads it carries.
# The compaction-tools switch is read inside ling-tools' `same_tools`, which only patch 0027 calls,
# so the linker keeps it only when the patch is in.
REQUIRED_SWITCHES: Final[dict] = {
    "knows_turn_end_switch": TURN_END_ENV,
    "knows_compaction_tools": COMPACTION_TOOLS_ENV,
    "knows_summary_cap": SUMMARY_WORDS_ENV,
    "knows_drop_old_outputs": DROP_ENV,
}


def binary_info(path: Path) -> dict[str, Any]:
    """Which binary a session ran, and which of v2's switches it knows (`REQUIRED_SWITCHES`)."""
    real = path.resolve()
    digest = hashlib.sha256()
    known = {key: False for key in REQUIRED_SWITCHES}
    with real.open("rb") as handle:
        tail = b""
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            for key, name in REQUIRED_SWITCHES.items():
                if not known[key] and name.encode() in tail + chunk:
                    known[key] = True
            tail = chunk[-64:]
    stat = real.stat()
    host = real.with_name("codex-code-mode-host")
    return {"path": str(path), "resolved": str(real), "sha256": digest.hexdigest()[:16],
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "code_mode_host_beside": host.is_file(), **known}


@dataclass
class SessionPlan:
    """One session to run: the task, the arm, the repetition and where it lives."""

    task: Task
    arm: str
    rep: int
    root: Path
    probe_key: str | None

    @property
    def label(self) -> str:
        return f"{self.task.name}-{self.arm}-{self.rep}"


def session_env(plan: SessionPlan, host: str, home: Path | None, code_tools: bool, docs: bool,
                summary_words: int = SUMMARY_WORDS) -> dict[str, str]:
    """The environment of one session: throwaway homes, the target server, masking off, the arm's
    switches (turn-end for "on" and "drop", drop-old-outputs for "drop"), and the summary cap in
    every arm (0 leaves Codex's own prompt)."""
    env = dict(os.environ)
    for key in [key for key in env if key.startswith(("DREAMFERENCE_MIGHTLING_", "CODEX_")) or key == "MIGHTLING_NODE"]:
        env.pop(key)
    env.update({
        "CODEX_HOME": str(plan.root / "home"),
        "DREAMFERENCE_CONFIG_PATH": str(plan.root / "dreamference.toml"),
        "DREAMFERENCE_VLLM_HOST": host,
        MASK_ENV: "0",
        TURN_END_ENV: "1" if plan.arm in TURN_END_ARMS else "0",
        DROP_ENV: "1" if plan.arm == "drop" else "0",
        SUMMARY_WORDS_ENV: str(summary_words),
        "DREAMFERENCE_MIGHTLING_GMAIL": "0",
        "TERM": "xterm-256color",
    })
    if not code_tools:
        env["DREAMFERENCE_MIGHTLING_CODE_TOOLS"] = "0"
    if not docs:
        env["DREAMFERENCE_MIGHTLING_DOCS"] = "0"
    if home is not None:
        env["HOME"] = str(home)
    return env


def session_args(arm: str, limit: int, percent: int | None, sandbox: str) -> list[str]:
    """`ling`'s arguments: no approvals, the sandbox, the pinned limit and the arm's turn-end percentage
    (passed to Codex directly; the launcher leaves a percentage on the command line alone)."""
    return ["-a", "never", "-s", sandbox, "-c", f"{LIMIT_KEY}={limit}",
            "-c", f"{TURN_END_KEY}={percent if arm in TURN_END_ARMS and percent else 0}"]


def prepare_session(plan: SessionPlan, python: str, cache: Path) -> Path:
    """Builds the session's directory: the scratch repository, CODEX_HOME and the Dreamference config.

    Args:
        plan: The session.
        python: The interpreter the task's test commands name.
        cache: Where task sources are kept between sessions.

    Returns:
        Path: The repository.
    """
    if plan.root.exists():
        shutil.rmtree(plan.root)
    plan.root.mkdir(parents=True)
    repo = plan.root / "repo"
    plan.task.setup(repo, cache)
    home = plan.root / "home"
    home.mkdir()
    # Only the trust entry: the launcher writes the provider itself (test_a_fresh_home_opens_on_the_composer).
    (home / "config.toml").write_text(f'[projects."{repo}"]\ntrust_level = "trusted"\n')
    (plan.root / "dreamference.toml").write_text("# Empty on purpose: the bench runs on the product's defaults.\n")
    return repo


def wait_turns_done(session: TuiSession, tail: RolloutTail, before: int, timeout: float, settle: float,
                    on_compacting: Callable[[], None] | None, events: list) -> str:
    """Waits until the turn just submitted, and any turn it queued, has ended and the TUI is idle.

    Args:
        session: The TUI.
        tail: The rollout being read.
        before: `task_started` events seen before the turn was submitted.
        timeout: Seconds before the turn is interrupted.
        settle: Seconds the TUI must stay idle.
        on_compacting: Called on every poll while "Compacting context" is on screen.
        events: The driver's log, appended to.

    Returns:
        str: `done`, `no start`, `timeout` or `exited`.
    """
    start = time.monotonic()
    nudged = False
    while tail.count("task_started") <= before:
        if not session.pump(0.5):
            return "exited"
        tail.poll()
        waited = time.monotonic() - start
        if waited > 60 and not nudged:
            session.child.send("\r")  # a paste the composer held back
            nudged = True
            events.append({"t": time.time(), "event": "nudge"})
        if waited > 180:
            return "no start"
    quiet_since: float | None = None
    while time.monotonic() - start < timeout:
        if not session.pump(0.5):
            return "exited"
        tail.poll()
        screen = session.text()
        if on_compacting is not None and COMPACTING_MARKER in screen:
            on_compacting()
        running = tail.count("task_started") > tail.count("task_complete", "turn_aborted")
        if running or BUSY_MARKER in screen or COMPACTING_MARKER in screen:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= settle:
            return "done"
    session.child.send("\x1b")  # Esc interrupts the turn
    session.pump(5.0)
    return "timeout"


def run_session(plan: SessionPlan, *, ling: Path, host: str, limit: int, window: int, python: str, cache: Path,
                sandbox: str = "workspace-write", home: Path | None = None, code_tools: bool = True,
                docs: bool = True, turn_timeout: float = 1800, session_timeout: float = 5400,
                think_s: float = 3.0, settle: float = 4.0, ready_timeout: float = 300,
                turns: list[str] | None = None, percent: int | None = None,
                summary_words: int = SUMMARY_WORDS, stop_by: float | None = None) -> dict[str, Any]:
    """Runs one scripted TUI session and returns its measurements.

    Args:
        plan: The session.
        ling: The binary.
        host: The model server URL (`DREAMFERENCE_VLLM_HOST`).
        limit: The compaction limit pinned on both arms.
        window: The model's context window, for the turn-end percentage.
        python: The interpreter the task's test commands name.
        cache: Where task sources are kept.
        sandbox: `ling -s`.
        home: A HOME for the session (the dry run's), or None for the user's.
        code_tools: Whether `ling-code`'s tools are offered (the product's default).
        docs: Whether `ling-docs`'s tools are offered (the product's default).
        turn_timeout: Seconds before a turn is interrupted.
        session_timeout: Seconds after which no further turn is sent.
        think_s: Pause before each turn is typed.
        settle: Seconds the TUI must stay idle for a turn to count as finished.
        ready_timeout: Seconds to wait for the composer.
        turns: The user's turns (default: the task's), with `{python}` filled in.
        percent: The turn-end percentage of the turn-end arms (default: the launcher's arithmetic).
        summary_words: The summary cap every arm runs with (0: Codex's own prompt).
        stop_by: A wall-clock time (epoch seconds) by which the session ends: no turn is typed after
            it, and a running turn is interrupted at it.

    Returns:
        dict[str, Any]: The session's record for `results.json`.
    """
    percent = turn_end_percent(window, limit) if percent is None else percent
    threshold = turn_end_threshold_tokens(window, percent) if percent else None
    repo = prepare_session(plan, python, cache)
    texts = [turn.replace("{python}", python) for turn in (turns or plan.task.turns)]
    args = session_args(plan.arm, limit, percent, sandbox)
    env = session_env(plan, host, home, code_tools, docs, summary_words)
    events: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "label": plan.label, "task": plan.task.name, "arm": plan.arm, "rep": plan.rep,
        "args": args, "limit": limit, "window": window, "turn_end_percent": percent if plan.arm in TURN_END_ARMS else 0,
        "drop_old_outputs": plan.arm == "drop", "summary_words": summary_words,
        "turn_end_threshold": threshold, "probe_key": plan.probe_key, "repo": str(repo), "events": events,
        "binary": binary_info(ling),
        "started": datetime.now().isoformat(timespec="seconds"), "status": "running",
    }
    session = TuiSession(str(ling), args, repo, env, plan.root / "pty.log")
    tail = RolloutTail(plan.root / "home")
    probe: dict[str, Any] = {}
    begin = time.monotonic()
    try:
        ready = session.wait_for(lambda text: all(marker in text for marker in READY_MARKERS)
                                 or SIGN_IN_MARKER in text, ready_timeout)
        if not ready or SIGN_IN_MARKER in session.text():
            result["status"] = "not ready"
            return result
        session.pump(3.0)
        index = 0
        while index < len(texts):
            if time.monotonic() - begin > session_timeout:
                result["status"] = "session timeout"
                break
            if stop_by is not None and time.time() >= stop_by:
                result["status"] = "stopped at the window's end"
                break
            session.pump(think_s)
            before = tail.count("task_started")
            events.append({"t": time.time(), "event": "turn", "index": index})
            session.type_text(texts[index])
            index += 1
            consumed = {"next": index, "shown_ms": None, "seen_ms": None}

            def maybe_probe() -> None:
                # Fire once per session, only on a turn-end compaction (the turn's last output, written
                # before "Compacting context" appeared, is an assistant message) and only when a
                # scripted turn is left to type.
                now_ms = int(time.time() * 1000)
                if consumed["seen_ms"] is None or now_ms - consumed["seen_ms"] > 3000:
                    consumed["shown_ms"] = now_ms  # a new compaction on screen
                consumed["seen_ms"] = now_ms
                if probe or plan.probe_key is None or consumed["next"] >= len(texts):
                    return
                if not turn_end_compaction_showing(tail.records, consumed["shown_ms"]):
                    return
                (plan.root / "probe-screen.txt").write_text(session.text())
                text = texts[consumed["next"]]
                session.type_text(text, PROBE_KEYS[plan.probe_key])
                probe.update({"sent_ms": int(time.time() * 1000), "key": plan.probe_key, "text": text,
                              "turn_index": consumed["next"]})
                events.append({"t": time.time(), "event": "probe", "index": consumed["next"], "key": plan.probe_key})
                consumed["next"] += 1

            timeout = turn_timeout if stop_by is None else max(min(turn_timeout, stop_by - time.time()), 30.0)
            outcome = wait_turns_done(session, tail, before, timeout, settle,
                                      maybe_probe if plan.arm in TURN_END_ARMS else None, events)
            index = consumed["next"]
            events.append({"t": time.time(), "event": "turn end", "outcome": outcome})
            if outcome in ("exited", "no start"):
                result["status"] = outcome
                break
        else:
            result["status"] = "complete"
    finally:
        (plan.root / "screen.txt").write_text(session.text())
        session.close()
        result["ended"] = datetime.now().isoformat(timespec="seconds")
        result["wall_s"] = round(time.monotonic() - begin, 1)
    result["probe_note"] = probe or None
    rollout = find_rollout(plan.root / "home")
    if rollout is not None:
        result.update(analyze_session(rollout, threshold, probe or None, plan.root / "home"))
        typed = sorted({event["index"] for event in events if event["event"] in ("turn", "probe")})
        result["inputs_missing"] = inputs_missing(read_records(rollout), {index: texts[index] for index in typed})
        result["inputs_intact"] = not result["inputs_missing"]
    result["grade"] = plan.task.grade(repo, python)
    return result


# --------------------------------------------------------------------------------------------------
# A stand-in model server, for the dry run

DRY_RUN_TURNS: Final[list] = [
    # As long as the real turns (600-950 characters), so the composer's handling of a long paste is tested.
    "DRYRUN-1 Say done. " + " ".join(f"Filler sentence {n} checks that a long paste reaches the model whole."
                                     for n in range(1, 15)),
    "DRYRUN-2 Run one command, then say done.",
    "DRYRUN-3 Say goodbye.",
]
SHELL_TOOLS: Final[tuple] = ("shell_command", "exec_command", "shell")


class FakeModelServer:
    """A loopback stand-in for the model server that steers a session into both kinds of compaction.

    It answers `/v1/models` (one model, a 262,144 window), `/metrics` (an SGLang pool) and
    `/v1/responses` (streamed). It reports inflated `usage` so Codex believes its context is large:
    the answer to `DRYRUN-1` reports 70,000 tokens (above the 64,750 turn-end threshold, below the
    94,144 limit), the first request of `DRYRUN-2` answers with a shell call and 100,000 tokens (above
    the limit: a mid-turn compaction follows the call), and a compaction request (recognised by
    Codex's compaction prompt) is answered after `compaction_delay` seconds, so a probe can be typed.

    Every request's `tools` are recorded whole, in order, so the dry run can check that a compaction
    request offers exactly the tools of the turn request before it (patch 0027). A stand-in for the
    prefix cache: a request whose tools are those of the previous request reports all but 1,000 of
    its input tokens as cached, any other none, so the report's cache-hit column is exercised.
    """

    def __init__(self, compaction_delay: float = 8.0, window: int = DEFAULT_WINDOW):
        self.compaction_delay = compaction_delay
        self.window = window
        self.served: dict[str, int] = {}
        self.requests: list[dict[str, Any]] = []
        self.tools: list[str] = []
        self.lock = threading.Lock()
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> str:
        """Starts serving on a free loopback port and returns the URL."""
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
                path = self.path.rstrip("/")
                if path == "/v1/models":
                    model = {"id": "fake-model", "object": "model", "owned_by": "sglang", "max_model_len": server.window}
                    self.reply(200, json.dumps({"object": "list", "data": [model]}).encode(), "application/json")
                elif path == "/metrics":
                    self.reply(200, b'sglang:max_total_num_tokens{model_name="fake-model"} 156907.0\n', "text/plain")
                else:
                    self.reply(404, b"{}", "application/json")

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.rstrip("/") != "/v1/responses":
                    self.reply(404, b"{}", "application/json")
                    return
                try:
                    request = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    request = {}
                self.reply(200, server.respond(request), "text/event-stream")

            def reply(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the stdlib's name
                return

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Stops serving."""
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    @staticmethod
    def sse(item: dict[str, Any], input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> bytes:
        """One streamed response holding `item`, with the given usage."""
        rid = f"resp-{time.time_ns()}"
        usage = {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": cached_tokens},
                 "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": 0},
                 "total_tokens": input_tokens + output_tokens}
        events = [{"type": "response.created", "response": {"id": rid}},
                  {"type": "response.output_item.done", "item": item},
                  {"type": "response.completed", "response": {"id": rid, "usage": usage}}]
        return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()

    @staticmethod
    def message(text: str) -> dict[str, Any]:
        """An assistant message item."""
        return {"type": "message", "role": "assistant", "id": f"msg-{time.time_ns()}",
                "content": [{"type": "output_text", "text": text}]}

    def shell_call(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """A call of the request's shell tool running `echo dry-run`, or None when it offers none."""
        names = [str(tool.get("name") or (tool.get("function") or {}).get("name") or "")
                 for tool in request.get("tools") or [] if isinstance(tool, dict)]
        self.tools = names
        name = next((candidate for candidate in SHELL_TOOLS if candidate in names), None)
        if name is None:
            return None
        arguments = {"shell_command": {"command": "echo dry-run"}, "exec_command": {"cmd": "echo dry-run"},
                     "shell": {"command": ["bash", "-lc", "echo dry-run"]}}[name]
        return {"type": "function_call", "id": f"fc-{time.time_ns()}", "call_id": f"call-{time.time_ns()}",
                "name": name, "arguments": json.dumps(arguments)}

    def respond(self, request: dict[str, Any]) -> bytes:
        """The streamed answer to one `/v1/responses` request."""
        items = request.get("input") or []
        tail_text = json.dumps(items[-2:])
        received = round(time.time(), 2)
        tools = json.dumps(request.get("tools") or [])
        if TITLE_REQUEST_MARK in json.dumps(items[-1:]):
            # Codex's side request for the session's title: answered, not counted as a turn's request.
            with self.lock:
                self.requests.append({"t": received, "kind": "title", "tools": tools})
            return self.sse(self.message("Dry run"), 500, 5)
        with self.lock:
            previous = next((r["tools"] for r in reversed(self.requests) if r["kind"] != "title"), None)
        cache = (lambda tokens: max(tokens - 1000, 0)) if previous == tools else (lambda tokens: 0)
        key = ""
        for item in reversed(items):
            if isinstance(item, dict) and item.get("role") == "user":
                found = re.search(r"DRYRUN-\d", json.dumps(item))
                if found:
                    key = found.group(0)
                    break
        with self.lock:
            self.requests.append({"t": received, "kind": "compaction" if COMPACTION_PROMPT_MARK in tail_text else "turn",
                                  "key": key, "items": [str(item.get("role") or item.get("type"))
                                                        for item in items if isinstance(item, dict)],
                                  "tools": tools, "tail": tail_text[-400:]})
            if COMPACTION_PROMPT_MARK in tail_text:
                compaction = True
            else:
                compaction = False
                self.served[key] = self.served.get(key, 0) + 1
            count = self.served.get(key, 0)
        if compaction:
            time.sleep(self.compaction_delay)
            return self.sse(self.message("Summary: the user asked for short answers; one command ran, `echo dry-run`."),
                            70_000, 200, cache(70_000))
        if key == "DRYRUN-1":
            return self.sse(self.message("done"), 70_000, 50, cache(70_000))
        if key == "DRYRUN-2" and count == 1:
            call = self.shell_call(request)
            if call is not None:
                return self.sse(call, 100_000, 30, cache(100_000))
        if key == "DRYRUN-2":
            return self.sse(self.message("done"), 20_000, 20, cache(20_000))
        return self.sse(self.message("goodbye"), 5_000, 10, cache(5_000))


def compaction_tools_checks(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether each compaction request offered exactly the tools of the turn request before it.

    Args:
        requests: What `FakeModelServer` recorded, in order.

    Returns:
        dict[str, Any]: `compactions` (how many), `matching` (how many offered the previous turn
            request's tools, same order, not empty), `capped` (how many carry the summary bound).
    """
    compactions = matching = capped = 0
    last_turn_tools = None
    for request in requests:
        if request["kind"] == "turn":
            last_turn_tools = request.get("tools")
        elif request["kind"] == "compaction":
            compactions += 1
            tools = request.get("tools")
            if tools and tools != "[]" and tools == last_turn_tools:
                matching += 1
            if f"under {SUMMARY_WORDS} words" in request.get("tail", ""):
                capped += 1
    return {"compactions": compactions, "matching": matching, "capped": capped}


def dry_run(out: Path, ling: Path, compaction_delay: float = 8.0, probe_key: str = "enter") -> dict[str, Any]:
    """Every arm against `FakeModelServer`, with a throwaway HOME: proves the driver and the binary's
    switches, not the model.

    Args:
        out: Where the sessions go (avoid `/tmp`: Codex refuses its helper links under a temporary
            CODEX_HOME).
        ling: The binary.
        compaction_delay: How long the stand-in takes to answer a compaction request.
        probe_key: The key the probe presses.

    Returns:
        dict[str, Any]: `sessions` (each session's record), `requests` (what the stand-in was asked),
            `tools` (the tool names it was offered), `compaction_tools` (per arm,
            `compaction_tools_checks`) and `checks` (name to bool).
    """
    home = out / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    task = Task("dry-run", "stand-in server", DRY_RUN_TURNS, lambda repo, cache: _tiny_repo(repo),
                lambda repo, python: {})
    sessions = []
    requests: dict[str, list] = {}
    tools: list[str] = []
    for arm in ARMS:
        server = FakeModelServer(compaction_delay=compaction_delay)
        host = server.start()
        try:
            plan = SessionPlan(task, arm, 1, out / f"dry-run-{arm}-1", probe_key if arm in TURN_END_ARMS else None)
            # The fake's 70,000-token answer to DRYRUN-1 must cross the turn-end threshold: the
            # launcher's 26 (64,750), as in v1; v2's 20 (49,808) lies below it too.
            sessions.append(run_session(plan, ling=ling, host=host, limit=DEFAULT_LIMIT, window=DEFAULT_WINDOW,
                                        python=sys.executable, cache=out / "sources", home=home, code_tools=False,
                                        docs=False, turn_timeout=120, session_timeout=600, think_s=1.0, settle=3.0,
                                        ready_timeout=120, percent=TURN_END_PERCENT_V2))
        finally:
            server.stop()
        requests[arm] = server.requests
        tools = server.tools or tools
        (plan.root / "session.json").write_text(json.dumps(sessions[-1], indent=1, default=str))
    by_arm = {session["arm"]: session for session in sessions}
    off = by_arm.get("off", {})
    tool_checks = {arm: compaction_tools_checks(requests.get(arm, [])) for arm in ARMS}
    checks = {
        "every session reached the composer and finished": all(s["status"] == "complete" for s in sessions),
        "off: no turn-end compaction": off.get("counts", {}).get("turn-end", -1) == 0,
        "off: a mid-turn compaction": off.get("counts", {}).get("mid-turn", 0) >= 1,
    }
    for arm in TURN_END_ARMS:
        on = by_arm.get(arm, {})
        checks.update({
            f"{arm}: a turn-end compaction": on.get("counts", {}).get("turn-end", 0) >= 1,
            f"{arm}: a mid-turn compaction": on.get("counts", {}).get("mid-turn", 0) >= 1,
            f"{arm}: every compaction timed": all(c["wall_s"] is not None for c in on.get("compactions", [])),
            f"{arm}: the probe was typed during a turn-end compaction": bool(on.get("probe"))
            and on["probe"]["compaction_kind"] == "turn-end",
            f"{arm}: the probe's message was not lost": bool(on.get("probe")) and on["probe"]["delivered"],
        })
    checks.update({
        "every compaction request offers the tools of the turn request before it, same order (patch 0027)":
            all(t["compactions"] >= 1 and t["matching"] == t["compactions"] for t in tool_checks.values()),
        f"every compaction request carries the {SUMMARY_WORDS}-word bound (compact_prompt)":
            all(t["capped"] == t["compactions"] for t in tool_checks.values()),
        "every compaction's cache hit is read from the rollout, and is above 0": all(
            c.get("cache_hit") for s in sessions for c in s.get("compactions", [])),
        "drop: drop-first compaction was attempted (its log is written)":
            (by_arm.get("drop", {}).get("drops") or {}).get("attempts", 0) >= 1,
        "off and on: drop-first compaction never ran": all(
            (by_arm.get(arm, {}).get("drops") or {}).get("attempts", 1) == 0 for arm in ("off", "on")),
        "every compaction has an audit verdict": all("audit" in c for s in sessions for c in s.get("compactions", [])),
        "every typed turn reached the model whole (a long paste included)": all(s.get("inputs_intact") for s in sessions),
    })
    return {"sessions": sessions, "requests": requests, "tools": tools, "compaction_tools": tool_checks, "checks": checks}


def _tiny_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("dry run\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")


# --------------------------------------------------------------------------------------------------
# The report

def _median(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(statistics.median(present), 1) if present else None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(statistics.fmean(present), 2) if present else None


def aggregate(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per task and arm: compactions by kind, turn-end wall times, session wall, peaks, grades.

    Args:
        sessions: Session records.

    Returns:
        dict[str, dict[str, Any]]: Keyed `<task>/<arm>`; besides the counts, `cache_hit_mean` (the
            compaction requests' cached share) and drop-first compaction's drops and declined
            attempts per session.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        groups.setdefault(f"{session['task']}/{session['arm']}", []).append(session)
    table = {}
    for key, members in sorted(groups.items()):
        compactions = [c for s in members for c in s.get("compactions", [])]
        turn_end = [c["wall_s"] for c in compactions if c["kind"] == "turn-end"]
        mid = [c["wall_s"] for c in compactions if c["kind"] != "turn-end"]
        table[key] = {
            "sessions": len(members),
            "complete": sum(1 for s in members if s.get("status") == "complete"),
            "mid_turn_per_session": _mean(s.get("counts", {}).get("mid-turn", 0) + s.get("counts", {}).get("pre-turn", 0)
                                          for s in members),
            "turn_end_per_session": _mean(s.get("counts", {}).get("turn-end", 0) for s in members),
            "turn_end_wall_median_s": _median(turn_end),
            "turn_end_wall_max_s": max(turn_end) if turn_end else None,
            "mid_turn_wall_median_s": _median(mid),
            "session_wall_mean_s": _mean(s.get("wall_s") for s in members),
            "peak_context_max": max((s.get("peak_context") or 0 for s in members), default=0) or None,
            "hidden_passed_mean": _mean((s.get("grade") or {}).get("hidden", {}).get("counts", {}).get("passed", 0)
                                        for s in members),
            "missed_turn_ends": sum(len(s.get("missed_turn_ends", [])) for s in members),
            "missed_turn_ends_clear": sum(len(s.get("missed_turn_ends_clear", [])) for s in members),
            "inputs_intact": all(s.get("inputs_intact", True) for s in members),
            "audit_lossless": sum(1 for c in compactions if (c.get("audit") or {}).get("verdict") in ("kept all", "nothing to keep")),
            "audited": len(compactions),
            "stray_summaries": sum(1 for c in compactions if c.get("stray_tool_call")),
            "cache_hit_mean": _mean(c.get("cache_hit") for c in compactions),
            "cache_measured": sum(1 for c in compactions if c.get("cache_hit") is not None),
            "drops_per_session": _mean((s.get("drops") or {}).get("drops", 0) for s in members),
            "declined_per_session": _mean((s.get("drops") or {}).get("declined", 0) for s in members),
        }
    return table


def decide_arm(sessions: list[dict[str, Any]], table: dict[str, dict[str, Any]], arm: str = "on") -> tuple[str, list[str]]:
    """Applies the decision rule to one turn-end arm (`on`, or `drop`), paired against `off`.

    Hard conditions, all needed to switch the arm's settings on by default: (1) at least one probe,
    and no typed message lost; (2) turn-end compactions took at most `MAX_MEDIAN_TURN_END_S` at the
    median and `MAX_TURN_END_S` at worst; (3) for every task whose "off" arm compacted mid-turn, fewer
    mid-turn compactions per session in the arm (a drop that replaced a summary is not a compaction);
    (4) no turn of the arm ended clearly (`MISSED_MARGIN_PERCENT` above) over the threshold without
    compacting. Reported, not deciding, because two sessions per arm cannot resolve them (Phase 0 saw
    the same task vary twofold): a task more than `MAX_WALL_INCREASE` slower per session in the arm, or
    passing more than `MAX_HIDDEN_DROP` fewer hidden tests. Inconclusive: no "off" session compacted
    mid-turn (the tasks were too small, not the setting useless), or a typed turn did not reach the
    model intact (the driver failed, not the setting).

    Args:
        sessions: Session records.
        table: `aggregate(sessions)`.
        arm: The arm judged against "off".

    Returns:
        tuple[str, list[str]]: `switch on`, `keep off` or `inconclusive`, and one line per condition.
    """
    lines = []
    passed = True
    mine = [s for s in sessions if s["arm"] == arm]
    probes = [s["probe"] for s in mine if s.get("probe")]
    lost = [p for p in probes if p["verdict"] == "lost"]
    interrupted = [p for p in probes if p["verdict"] == "interrupted"]
    lines.append(f"{'✅' if not lost and probes else '❌'} typed messages kept: {len(probes) - len(lost)} of {len(probes)} probes"
                 + (f" ({len(interrupted)} cut the compaction short)" if interrupted else "")
                 + ("" if probes else " (no probe fired: not shown)"))
    passed &= bool(probes) and not lost
    walls = [c["wall_s"] for s in mine for c in s.get("compactions", [])
             if c["kind"] == "turn-end" and c["wall_s"] is not None]
    median = _median(walls)
    fast = bool(walls) and median <= MAX_MEDIAN_TURN_END_S and max(walls) <= MAX_TURN_END_S
    lines.append(f"{'✅' if fast else '❌'} turn-end compaction wall time: median {median} s, max "
                 f"{max(walls) if walls else None} s (limits {MAX_MEDIAN_TURN_END_S:.0f} s / {MAX_TURN_END_S:.0f} s)")
    passed &= fast
    reached = False
    for task in sorted({s["task"] for s in sessions}):
        off, on = table.get(f"{task}/off"), table.get(f"{task}/{arm}")
        if not off or not on:
            lines.append(f"❔ {task}: one arm has no session")
            passed = False
            continue
        if (off["mid_turn_per_session"] or 0) > 0:
            reached = True
            saves = (on["mid_turn_per_session"] or 0) < off["mid_turn_per_session"]
            lines.append(f"{'✅' if saves else '❌'} {task}: mid-turn compactions per session {off['mid_turn_per_session']} "
                         f"off, {on['mid_turn_per_session']} {arm}")
            passed &= saves
        else:
            lines.append(f"❔ {task}: the off arm never compacted mid-turn (peak {off['peak_context_max']}), so it shows "
                         "no saving to measure")
        missed = on["missed_turn_ends_clear"]
        lines.append(f"{'❌' if missed else '✅'} {task}: {arm}-arm turns ending clearly above the threshold with no compaction: "
                     f"{missed}" + (f" ({on['missed_turn_ends']} within {MISSED_MARGIN_PERCENT}% of it)"
                                    if on["missed_turn_ends"] > missed else ""))
        passed &= not missed
        slower = bool(off["session_wall_mean_s"] and on["session_wall_mean_s"]
                      and on["session_wall_mean_s"] > off["session_wall_mean_s"] * (1 + MAX_WALL_INCREASE))
        worse = (on["hidden_passed_mean"] or 0) < (off["hidden_passed_mean"] or 0) - MAX_HIDDEN_DROP
        lines.append(f"{'⚠️' if slower or worse else '➖'} {task} (reported, not deciding): session wall "
                     f"{off['session_wall_mean_s']} s off, {on['session_wall_mean_s']} s {arm}; hidden tests passed "
                     f"{off['hidden_passed_mean']} off, {on['hidden_passed_mean']} {arm}")
    intact = all(row["inputs_intact"] for key, row in table.items() if key.endswith(("/off", f"/{arm}")))
    if not intact:
        lines.append("❔ a typed turn did not reach the model intact: the driver failed, see `inputs_missing`")
    if not reached or not intact:
        return "inconclusive", lines
    return ("switch on" if passed else "keep off"), lines


def cache_line(sessions: list[dict[str, Any]]) -> str:
    """The prefix cache on compaction requests, across all arms (reported, not deciding)."""
    hits = [c["cache_hit"] for s in sessions for c in s.get("compactions", []) if c.get("cache_hit") is not None]
    cold = sum(1 for hit in hits if hit < 0.5)
    stray = sum(1 for s in sessions for c in s.get("compactions", []) if c.get("stray_tool_call"))
    return (f"{'✅' if hits and not cold else '⚠️' if hits else '❔'} (reported, not deciding) compaction requests served from "
            f"the prefix cache: median {_percent(statistics.median(hits)) if hits else None} of the prompt over {len(hits)} "
            f"compactions, {cold} below half "
            f"(2026-10-13: 0 in 12 of 13); summaries that are a stray tool call: {stray}")


def decide(sessions: list[dict[str, Any]], table: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    """The decision rule (`decide_arm`) for every turn-end arm present, each paired against "off".

    With one such arm the verdict is that arm's (`switch on`, `keep off`, `inconclusive`); with
    several it names each (`on: keep off; drop: switch on`). The cache line is added, reported only.

    Args:
        sessions: Session records.
        table: `aggregate(sessions)`.

    Returns:
        tuple[str, list[str]]: The verdict and one line per condition.
    """
    arms = [arm for arm in TURN_END_ARMS if any(s["arm"] == arm for s in sessions)] or ["on"]
    verdicts, lines = [], []
    for arm in arms:
        verdict, reasons = decide_arm(sessions, table, arm)
        verdicts.append((arm, verdict))
        if len(arms) > 1:
            lines.append(f"**{arm}** ({ARM_TITLES[arm]}) against off: **{verdict}**")
            lines += [f"  {line}" for line in reasons]
        else:
            lines += reasons
    if any(c.get("cache_hit") is not None for s in sessions for c in s.get("compactions", [])):
        lines.append(cache_line(sessions))
    if len(verdicts) == 1:
        return verdicts[0][1], lines
    return "; ".join(f"{arm}: {verdict}" for arm, verdict in verdicts), lines


RUNBOOK: Final[str] = """\
## Runbook (v2, 2026-10-14)

Target: second-puffin's reference server (`m0-prod8000`, the default model, 16 draft tokens), named by
its mDNS name, never an address. Speed differs from this machine's server; compaction behaviour does not.

0. On second-puffin only one model server runs at a time: `ling-serve` stopped, `m0-prod8000` up and
   answering `/v1/models` on port 8000 (`ssh second-puffin 'docker ps --format "{{.Names}}"'`).
1. The binary: a `ling` built from branch `compaction/cache-hit` (patches 0026 and 0027, the launcher's
   summary cap), never installed, with `codex-code-mode-host` beside it: `~/mightling-bench-build/bin/ling`.
   From the checkout of that branch (`cd ~/PycharmProjects/wt-cache-hit`), with
   `PY=~/PycharmProjects/dgxcoder/.venv/bin/python`. Pre-flight (server, model, window, KV pool, the
   binary's four switches, sandbox label, click clone):
   `$PY scripts/turn_end_compaction_bench.py preflight --host http://second-puffin.local:8000 --ling ~/mightling-bench-build/bin/ling`
2. The run, as a user unit (outside PyCharm, so the sandbox runs as it does for users):

       systemd-run --user --unit tec-bench-v2-<date> --working-directory=$PWD \\
           -p StandardOutput=append:$HOME/.cache/dreamference/turn-end-compaction/<date>.log \\
           -p StandardError=append:$HOME/.cache/dreamference/turn-end-compaction/<date>.log \\
           $PY -u scripts/turn_end_compaction_bench.py run --host http://second-puffin.local:8000 \\
           --reps 2 --ling $HOME/mightling-bench-build/bin/ling --stop-by <ISO time> \\
           --out $HOME/.cache/dreamference/turn-end-compaction/<date>

   Order: repetition 1 runs night-shift off, on, drop, then click off, on, drop; repetition 2 the arms
   reversed. The probe (in the on and drop arms) presses Enter in repetition 1 and Tab (queue) in 2.
3. Expected duration: 12 sessions at ~22 min (2026-10-13: 18.5 min off, 23.6–31 min on, before the
   cache fix) ≈ 4.5 h; ask for a 6 h window. Ceilings: 45 min per turn (`--turn-timeout`), 105 min per
   session (`--session-timeout`); `--stop-by` starts no session within 20 min of it and interrupts a turn
   at it, so a short window ends with whole sessions. Do not rebuild the binary during the run.
4. Watch: `tail -f` the log; `results.json` and this report are rewritten after every session.
"""


OWNER_RULE: Final[str] = (
    "Owner's decision (2026-10-13, before the result was known): if the Night Shift task never reaches the "
    "limit in either repetition of its off arm, the verdict rests on click plus the other hard conditions. "
    "No typed message lost, turn-end compactions at most 60 s at the median and 120 s at worst, and no "
    "on-arm turn clearly above the threshold without compacting are checked across all sessions; the "
    "mid-turn saving is judged on click alone. This is what the rule above computes: a task whose off "
    "arm never compacted mid-turn is marked ❔ and neither passes nor fails. If the verdict is "
    "`switch on`, turn-end compaction becomes on by default. v2 (2026-10-14, the owner's): the same rule, applied "
    "to each turn-end arm against off; every arm runs with the cache fix (patch 0027) and the 800-word summary "
    "cap, the turn-end threshold is 20, and the compaction requests' cache hit is reported.")


def _percent(share: float | None) -> str:
    return "—" if share is None else f"{share:.0%}"


def render_report(meta: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    """The Markdown report: setup, per-session table, per-arm table, probes, audit, decision, runbook.

    Args:
        meta: Run metadata (`host`, `model`, `binary`, `limit`, `window`, `percent`, `threshold`, ...).
        sessions: Session records.

    Returns:
        str: The report.
    """
    table = aggregate(sessions)
    decision, reasons = decide(sessions, table)
    binary = meta.get("binary") or {}
    arms = [arm for arm in ARMS if any(s.get("arm") == arm for s in sessions)] or list(ARMS)
    switches = ", ".join(f"{key.removeprefix('knows_')}: {binary.get(key)}" for key in REQUIRED_SWITCHES)
    words = meta.get("summary_words")
    out = [
        "# Turn-end compaction in the TUI: measurement" + (" (v2)" if len(arms) > 2 else ""),
        "",
        f"Run {meta.get('started', '?')} against `{meta.get('host', '?')}` (model `{meta.get('model', '?')}`, window "
        f"{meta.get('window')}, KV pool {meta.get('pool')}). Binary `{binary.get('resolved', '?')}` "
        f"(sha256 {binary.get('sha256', '?')}…, built {binary.get('mtime', '?')}; {switches}). Arms: "
        + ", ".join(f"`{arm}` ({ARM_TITLES.get(arm, arm)})" for arm in arms)
        + f". Limit pinned at {meta.get('limit')} in every arm; the turn-end arms set `{TURN_END_KEY}={meta.get('percent')}`, "
        f"a turn-end compaction once the context passes {meta.get('threshold')} tokens"
        + (f"; the drop arm also sets `{DROP_ENV}=1`" if "drop" in arms else "")
        + (f". Every arm caps the summary at {words} words (`{SUMMARY_WORDS_ENV}`, Codex's `compact_prompt`)"
           if words else "")
        + f". Masking off, ledger on (its default), approvals off, sandbox `{meta.get('sandbox', 'workspace-write')}`.",
        "",
        f"**Decision: {decision}.**",
        "",
        *[f"  - {line.strip()}" if line.startswith("  ") else f"- {line}" for line in reasons],
        "",
        OWNER_RULE,
        "",
        "## Sessions",
        "",
        "| Session | Status | Wall | Turn walls (s) | Peak context | Pre / mid / turn-end | Turn-end walls (s) | Mid-turn walls (s) | Drops / declined | Hidden tests | Own tests |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in sessions:
        counts = s.get("counts", {})
        compactions = s.get("compactions", [])
        grade = s.get("grade") or {}
        hidden = (grade.get("hidden") or {}).get("counts", {})
        own = (grade.get("own") or {}).get("counts", {})
        out.append(
            f"| {s['label']} | {s.get('status')} | {round((s.get('wall_s') or 0) / 60, 1)} min "
            f"| {', '.join(str(t['wall_s']) + ('' if t['status'] == 'complete' else ' ' + t['status']) for t in s.get('turns', [])) or '—'} "
            f"| {s.get('peak_context')} | {counts.get('pre-turn', 0)} / {counts.get('mid-turn', 0)} / {counts.get('turn-end', 0)} "
            f"| {', '.join(str(c['wall_s']) for c in compactions if c['kind'] == 'turn-end') or '—'} "
            f"| {', '.join(str(c['wall_s']) for c in compactions if c['kind'] != 'turn-end') or '—'} "
            f"| {(s.get('drops') or {}).get('drops', 0)} / {(s.get('drops') or {}).get('declined', 0)} "
            f"| {hidden.get('passed', 0)} passed, {hidden.get('failed', 0)} failed "
            f"| {own.get('passed', 0)} passed, {own.get('failed', 0)} failed |")
    out += ["", "## Per task and arm", "",
            "| Task / arm | Sessions | Mid-turn per session | Turn-end per session | Turn-end wall median / max (s) "
            "| Mid-turn wall median (s) | Session wall mean (min) | Peak | Hidden passed (mean) | Summaries lossless (audit) "
            "| Compaction cache hit (mean) | Drops / declined per session |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, row in table.items():
        wall = round(row["session_wall_mean_s"] / 60, 1) if row["session_wall_mean_s"] else None
        out.append(f"| {key} | {row['sessions']} ({row['complete']} complete) | {row['mid_turn_per_session']} "
                   f"| {row['turn_end_per_session']} | {row['turn_end_wall_median_s']} / {row['turn_end_wall_max_s']} "
                   f"| {row['mid_turn_wall_median_s']} | {wall} | {row['peak_context_max']} | {row['hidden_passed_mean']} "
                   f"| {row['audit_lossless']} of {row['audited']} | {_percent(row.get('cache_hit_mean'))} "
                   f"| {row.get('drops_per_session')} / {row.get('declined_per_session')} |")
    out += ["", "## Typing during a turn-end compaction", "",
            "| Session | Key | Typed after (s) | Compaction finished | Turn aborted | Message taken up | Waited (s) | Verdict |",
            "|---|---|---|---|---|---|---|---|"]
    for s in sessions:
        p = s.get("probe")
        if p:
            out.append(f"| {s['label']} | {p['key']} | {p['typed_after_s']} | {p['compaction_finished']} | {p['turn_aborted']} "
                       f"| {p['delivered_in'] or 'no'} | {p['waited_s']} | {p['verdict']} |")
    if not any(s.get("probe") for s in sessions):
        out.append("| — | | | | | | | no probe fired |")
    out += ["", "## Compactions and the nightly audit", "",
            "| Session | # | Kind | Wall (s) | Context before → after | Cached / input (hit) | Summary tokens / chars | Audit |",
            "|---|---|---|---|---|---|---|---|"]
    for s in sessions:
        for c in s.get("compactions", []):
            out.append(f"| {s['label']} | {c['number']} | {c['kind']} | {c['wall_s']} | {c['context_before']} → {c['context_after']} "
                       f"| {c.get('cached_tokens')} / {c.get('input_tokens')} ({_percent(c.get('cache_hit'))}) "
                       f"| {c.get('summary_output_tokens')} / {c['summary_chars']} | {(c.get('audit') or {}).get('verdict', '—')} |")
    out += ["", "## Decision rule", "",
            "Each turn-end arm is judged against `off` on its own (v2: `on`, and `drop`, which adds drop-old-outputs). "
            f"Switch the arm's settings on by default only if all hold: at least one probe and no typed "
            f"message lost; turn-end compactions at most {MAX_MEDIAN_TURN_END_S:.0f} s at the median and "
            f"{MAX_TURN_END_S:.0f} s at worst (about the time it takes to read an answer); for each task whose off arm "
            "compacted mid-turn, fewer mid-turn compactions per session in the arm (a drop that replaced a summary is "
            "not a compaction); and no turn of the arm ended "
            f"clearly ({MISSED_MARGIN_PERCENT}% or more) above the threshold without compacting. Reported but not deciding, "
            "since two sessions per arm cannot resolve them (Phase 0 saw one task vary twofold): an on arm more than "
            f"{MAX_WALL_INCREASE:.0%} slower per session, or passing more than {MAX_HIDDEN_DROP:.0f} fewer hidden tests. "
            "Inconclusive when no off session compacted mid-turn (the tasks were too small, not the setting useless) or a "
            "typed turn did not reach the model intact. An interrupted compaction is not a failure if the typed message "
            "was kept; it is reported. The compaction requests' cache hit (cached / input tokens of each, from its usage "
            "in the rollout) is reported, not deciding.",
            "", RUNBOOK]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------------
# Commands

def fetch_json(url: str, timeout: float = 10) -> Any:
    """GET a JSON document."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - a URL the user gave
        return json.loads(response.read())


def server_facts(host: str) -> dict[str, Any]:
    """What the model server says about itself: model id, owner, window, KV pool.

    Args:
        host: The server URL.

    Returns:
        dict[str, Any]: `model`, `owned_by`, `window`, `pool` (None when `/metrics` names none).
    """
    base = host.rstrip("/")
    models = fetch_json(f"{base}/v1/models")
    card = (models.get("data") or [{}])[0]
    pool = None
    try:
        with urllib.request.urlopen(f"{base}/metrics", timeout=10) as response:  # noqa: S310
            metrics = response.read().decode(errors="replace")
        found = re.search(r"^sglang:max_total_num_tokens\{[^}]*\}\s+([\d.e+]+)", metrics, re.M)
        pool = int(float(found.group(1))) if found else None
    except OSError:
        pass
    return {"model": card.get("id"), "owned_by": card.get("owned_by"),
            "window": int(card.get("max_model_len") or 0) or None, "pool": pool}


LOOPBACK_NAMES: Final[tuple] = ("localhost", "127.", "0.0.0.0", "[::1]", "::1")


def require_host(host: str | None) -> str:
    """The target server, named. The bench never falls back to a default, and refuses this machine's
    own server (a loopback name): on 2026-10-13 it is held by the benchmark nights.

    Args:
        host: `--host`, or the inherited `DREAMFERENCE_VLLM_HOST`.

    Returns:
        str: The URL without a trailing slash.
    """
    if not host:
        raise SystemExit("❌ name the model server: --host http://<node>.local:8000 (or DREAMFERENCE_VLLM_HOST)")
    name = host.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0] if "]" not in host else host.split("://", 1)[-1].split("]")[0] + "]"
    if name.lower().startswith(LOOPBACK_NAMES):
        raise SystemExit(f"❌ {host} is this machine's own server; name the node to measure on (--host http://<node>.local:8000)")
    return host.rstrip("/")


def command_preflight(options: argparse.Namespace) -> int:
    """Checks the server, the binary, the sandbox label and the task sources; prints what it found."""
    host = require_host(options.host)
    ok = True
    try:
        facts = server_facts(host)
        print(f"✅ {host}: model {facts['model']} ({facts['owned_by']}), window {facts['window']}, KV pool {facts['pool']}")
        if options.expect_model and options.expect_model not in str(facts["model"]):
            print(f"❌ the served model is not {options.expect_model}")
            ok = False
        window = facts["window"] or DEFAULT_WINDOW
        percent = options.turn_end_percent or turn_end_percent(window, options.limit)
        print(f"💡 limit {options.limit}, turn-end percentage {percent}, threshold "
              f"{turn_end_threshold_tokens(window, percent) if percent else None} tokens; the server's own pool would give "
              f"{facts['pool'] * 60 // 100 if facts['pool'] else 'no'} limit")
    except OSError as error:
        print(f"❌ {host} does not answer: {error}")
        ok = False
    info = binary_info(ling_binary(options.ling))
    missing = [key for key in REQUIRED_SWITCHES if not info[key]]
    print(f"{'❌' if missing else '✅'} ling {info['resolved']} (built {info['mtime']}, sha256 {info['sha256']}…): "
          + ", ".join(f"{key.removeprefix('knows_')} {info[key]}" for key in REQUIRED_SWITCHES)
          + (": a binary without them cannot run v2's arms (build compaction/cache-hit)" if missing else ""))
    print(f"{'✅' if info['code_mode_host_beside'] else '❌'} codex-code-mode-host beside it: {info['code_mode_host_beside']}")
    ok &= not missing and info["code_mode_host_beside"]
    label = Path("/proc/self/attr/current").read_text(errors="replace").strip() if Path("/proc/self/attr/current").exists() else "?"
    print(f"{'⚠️ ' if 'pycharm' in label else '✅'} AppArmor label {label!r}"
          + (": a PyCharm shell sandboxes more permissively than a user's terminal" if "pycharm" in label else ""))
    if not Path("/etc/apparmor.d/puffin-bwrap").exists():
        print("⚠️  no /etc/apparmor.d/puffin-bwrap: sandboxed commands may fail outside PyCharm (ling-admin host setup)")
    try:
        click_mirror(Path(options.cache))
        print(f"✅ click {CLICK_TAG} at {CLICK_COMMIT[:12]} in {options.cache}")
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"❌ click: {error}")
        ok = False
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", NIGHT_SHIFT_BASE], capture_output=True).returncode:
        print(f"❌ this checkout lacks {NIGHT_SHIFT_BASE[:12]} (Night Shift's base)")
        ok = False
    else:
        print(f"✅ Night Shift base {NIGHT_SHIFT_BASE[:12]}")
    return 0 if ok else 1


def load_sessions(out: Path) -> list[dict[str, Any]]:
    """The session records written so far under `out`."""
    sessions = []
    for path in sorted(out.glob("*/session.json")):
        try:
            sessions.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    order = json.loads((out / "meta.json").read_text()).get("order", []) if (out / "meta.json").exists() else []
    return sorted(sessions, key=lambda s: order.index(s["label"]) if s["label"] in order else len(order))


def write_outputs(out: Path, meta: dict[str, Any], sessions: list[dict[str, Any]]) -> None:
    """Writes `results.json` and `report.md`."""
    table = aggregate(sessions)
    decision, reasons = decide(sessions, table)
    (out / "results.json").write_text(json.dumps({"meta": meta, "aggregate": table, "decision": decision,
                                                  "reasons": reasons, "sessions": sessions}, indent=1, default=str))
    (out / "report.md").write_text(render_report(meta, sessions))


def plans_for(tasks: list[str], reps: int, root: Path, probe_keys: list[str], arms: tuple = ARMS) -> list[SessionPlan]:
    """The sessions in run order: per repetition every task, its arms alternating which goes first.

    Args:
        tasks: Task names.
        reps: Repetitions.
        root: The run's directory.
        probe_keys: The probe's key per repetition (cycled).
        arms: The arms, in repetition 1's order.

    Returns:
        list[SessionPlan]: In order.
    """
    plans = []
    order = tuple(arms)
    for rep in range(1, reps + 1):
        arms = order if rep % 2 else tuple(reversed(order))
        key = probe_keys[(rep - 1) % len(probe_keys)] if probe_keys else None
        for name in tasks:
            for arm in arms:
                plans.append(SessionPlan(TASKS[name], arm, rep, root / f"{name}-{arm}-{rep}",
                                         key if arm in TURN_END_ARMS else None))
    return plans


def command_run(options: argparse.Namespace) -> int:
    """Runs every session in order, rewriting the outputs after each."""
    host = require_host(options.host)
    out = Path(options.out).expanduser()
    if str(out).startswith(tempfile.gettempdir() + os.sep):
        raise SystemExit("❌ --out under the temporary directory: Codex refuses its helper links there; use ~/.cache/…")
    out.mkdir(parents=True, exist_ok=True)
    ling = ling_binary(options.ling)
    facts = server_facts(host)
    if options.expect_model and options.expect_model not in str(facts["model"]):
        raise SystemExit(f"❌ {host} serves {facts['model']}, not {options.expect_model}")
    window = options.window or facts["window"] or DEFAULT_WINDOW
    percent = options.turn_end_percent or turn_end_percent(window, options.limit)
    info = binary_info(ling)
    missing = [key for key in REQUIRED_SWITCHES if not info[key]]
    if missing and not options.allow_old_binary:
        raise SystemExit(f"❌ {ling} lacks {', '.join(missing)}: build compaction/cache-hit (or pass --allow-old-binary)")
    stop_by = datetime.fromisoformat(options.stop_by).timestamp() if options.stop_by else None
    plans = [plan for plan in plans_for(options.tasks, options.reps, out, options.probe_keys, tuple(options.arms))
             if not options.only or plan.label in options.only]
    meta = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": host, **facts,
            "window": window, "limit": options.limit, "percent": percent,
            "threshold": turn_end_threshold_tokens(window, percent) if percent else None,
            "summary_words": options.summary_words, "arms": list(options.arms), "stop_by": options.stop_by,
            "binary": info, "sandbox": options.sandbox, "order": [plan.label for plan in plans],
            "python": options.python, "code_tools": not options.no_code_tools, "docs": not options.no_docs}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    for plan in plans:
        if (plan.root / "session.json").exists() and not options.redo:
            print(f"⏭️  {plan.label}: already measured")
            continue
        if stop_by is not None and time.time() > stop_by - options.min_session_minutes * 60:
            print(f"⏹️  {datetime.now():%H:%M:%S} {plan.label}: not started, under {options.min_session_minutes} min "
                  f"left before {options.stop_by}", flush=True)
            continue
        print(f"🚀 {datetime.now():%H:%M:%S} {plan.label}", flush=True)
        try:
            result = run_session(plan, ling=ling, host=host, limit=options.limit, window=window, python=options.python,
                                 cache=Path(options.cache), sandbox=options.sandbox, code_tools=not options.no_code_tools,
                                 docs=not options.no_docs, turn_timeout=options.turn_timeout,
                                 session_timeout=options.session_timeout, percent=percent,
                                 summary_words=options.summary_words, stop_by=stop_by)
        except Exception as error:  # noqa: BLE001 - one broken session must not end the run
            result = {"label": plan.label, "task": plan.task.name, "arm": plan.arm, "rep": plan.rep,
                      "status": f"driver error: {error!r}"}
        (plan.root / "session.json").write_text(json.dumps(result, indent=1, default=str))
        counts = result.get("counts", {})
        hits = [c.get("cache_hit") for c in result.get("compactions", [])]
        print(f"   {result.get('status')}: {round((result.get('wall_s') or 0) / 60, 1)} min, peak {result.get('peak_context')}, "
              f"compactions {counts}, cache hits {hits}, drops {(result.get('drops') or {}).get('drops')}, "
              f"probe {(result.get('probe') or {}).get('verdict')}", flush=True)
        write_outputs(out, meta, load_sessions(out))
    print(f"✅ {out / 'report.md'}")
    return 0


def command_report(options: argparse.Namespace) -> int:
    """Re-analyzes every session's rollout under `--out` and rewrites the outputs."""
    out = Path(options.out).expanduser()
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    sessions = load_sessions(out)
    for session in sessions:
        rollout = find_rollout(out / session["label"] / "home")
        if rollout is not None:
            session.update(analyze_session(rollout, session.get("turn_end_threshold"), session.get("probe_note"),
                                           out / session["label"] / "home"))
    write_outputs(out, meta, sessions)
    print(out / "report.md")
    return 0


def command_dry_run(options: argparse.Namespace) -> int:
    """Both arms against the stand-in server; prints the checks."""
    out = Path(options.out).expanduser() if options.out else DEFAULT_OUT_PARENT / f"dry-run-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    ling = ling_binary(options.ling)
    meta = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": "stand-in", "model": "fake-model",
            "window": DEFAULT_WINDOW, "limit": DEFAULT_LIMIT, "percent": TURN_END_PERCENT_V2,
            "threshold": turn_end_threshold_tokens(DEFAULT_WINDOW, TURN_END_PERCENT_V2), "binary": binary_info(ling),
            "summary_words": SUMMARY_WORDS, "order": [f"dry-run-{arm}-1" for arm in ARMS]}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    result = dry_run(out, ling, probe_key=options.probe_key)
    (out / "dry-run.json").write_text(json.dumps(result, indent=1, default=str))
    write_outputs(out, meta, load_sessions(out))
    for name, passed in result["checks"].items():
        print(f"{'✅' if passed else '❌'} {name}")
    for arm, counts in result["compaction_tools"].items():
        print(f"💡 {arm}: {counts['matching']} of {counts['compactions']} compaction requests offered the turn's tools; "
              f"{counts['capped']} carried the summary bound")
    print(f"💡 {out}")
    return 0 if all(result["checks"].values()) else 1


def parser() -> argparse.ArgumentParser:
    """The command line."""
    top = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = top.add_subparsers(dest="command", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--ling", help="the binary (default: ling on PATH)")

    def target(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--host", default=os.environ.get("DREAMFERENCE_VLLM_HOST"),
                         help="the model server, e.g. http://second-puffin.local:8000 (required)")
        sub.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="the compaction limit pinned on both arms")
        sub.add_argument("--expect-model", default="Qwen3.8-27B", help="refuse a server serving another model ('' to skip)")
        sub.add_argument("--cache", default=str(SOURCE_CACHE), help="where task sources are kept")
        sub.add_argument("--turn-end-percent", type=int, default=TURN_END_PERCENT_V2,
                         help="the turn-end arms' percentage (v2: 20; 0 for the launcher's arithmetic, 26)")

    preflight = commands.add_parser("preflight", help="check the server, binary, sandbox and task sources")
    common(preflight)
    target(preflight)
    run = commands.add_parser("run", help="run the sessions")
    common(run)
    target(run)
    run.add_argument("--out", default=str(DEFAULT_OUT_PARENT / datetime.now().strftime("%Y-%m-%d")))
    run.add_argument("--tasks", nargs="+", default=list(TASKS), choices=list(TASKS))
    run.add_argument("--reps", type=int, default=2)
    run.add_argument("--probe-keys", nargs="+", default=["enter", "tab"], choices=list(PROBE_KEYS))
    run.add_argument("--only", nargs="*", help="run only these session labels (e.g. click-on-1)")
    run.add_argument("--redo", action="store_true", help="rerun sessions already measured")
    run.add_argument("--window", type=int, help="override the window the server reports")
    run.add_argument("--sandbox", default="workspace-write")
    run.add_argument("--python", default=sys.executable, help="the interpreter the tasks' tests use (default: this one)")
    run.add_argument("--no-code-tools", action="store_true", help="switch ling-code's tools off (default: the product's)")
    run.add_argument("--no-docs", action="store_true", help="switch ling-docs's tools off (default: the product's)")
    run.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS), help="the arms, in repetition 1's order")
    run.add_argument("--summary-words", type=int, default=SUMMARY_WORDS,
                     help=f"the summary cap every arm runs with ({SUMMARY_WORDS_ENV}; 0: Codex's own prompt)")
    run.add_argument("--stop-by", help="ISO time by which the run ends: no session starts within --min-session-minutes "
                                       "of it, and a running turn is interrupted at it")
    run.add_argument("--min-session-minutes", type=float, default=20.0)
    run.add_argument("--allow-old-binary", action="store_true", help="run a binary that lacks v2's switches")
    run.add_argument("--turn-timeout", type=float, default=2700, help="seconds before a turn is interrupted")
    run.add_argument("--session-timeout", type=float, default=6300, help="seconds after which no further turn is typed")
    report = commands.add_parser("report", help="re-analyze the rollouts and rewrite the report")
    report.add_argument("--out", required=True)
    dry = commands.add_parser("dry-run", help="every arm against a stand-in server on loopback")
    common(dry)
    dry.add_argument("--out", help="where to put it (not under /tmp)")
    dry.add_argument("--probe-key", default="enter", choices=list(PROBE_KEYS))
    return top


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    options = parser().parse_args(argv)
    return {"preflight": command_preflight, "run": command_run, "report": command_report,
            "dry-run": command_dry_run}[options.command](options)


if __name__ == "__main__":
    sys.exit(main())
