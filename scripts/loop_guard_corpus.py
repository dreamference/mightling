"""Writes the loop guard's corpus of normal model output (docs/dev/loop-guard.md).

Every text the model wrote that is saved on this machine, read-only: the assistant messages, tool
inputs and reasoning of the saved sessions (`~/.mightling/sessions`) and of every SWE-bench
instance's scratch `codex-home`, and the messages and commands of the SWE-bench exec logs. One
JSON line per distinct text, `{"source", "kind", "text"}`, for the detector's corpus tests:

    .venv/bin/python scripts/loop_guard_corpus.py /tmp/corpus.jsonl
    LOOP_GUARD_CORPUS=/tmp/corpus.jsonl cargo test --release -p ling-loop-guard --test corpus -- --ignored --nocapture
"""

import argparse
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Final

# Shorter texts cannot hold a loop the detector would see.
MIN_LENGTH: Final[int] = 40
SWE_BENCH_RUNS: Final[str] = ".local/share/dreamference/swe-bench/runs"
ROLLOUT_MARK: Final[str] = '"response_item"'
MESSAGE: Final[str] = "message"
ASSISTANT: Final[str] = "assistant"
REASONING: Final[str] = "reasoning"
# The field of each tool-call item that holds what the model wrote.
TOOL_INPUT_FIELDS: Final[dict[str, str]] = {
    "function_call": "arguments",
    "custom_tool_call": "input",
}


def joined(parts: object) -> str:
    """The text of a content list."""
    return (
        "".join(p.get("text", "") for p in parts or [] if isinstance(p, dict))
        if isinstance(parts, list)
        else ""
    )


def rollout_texts(path: Path) -> Iterator[tuple[str, str]]:
    """The model's texts in one Codex rollout: (kind, text)."""
    with path.open(errors="replace") as handle:
        for line in handle:
            if ROLLOUT_MARK not in line:
                continue
            try:
                payload = json.loads(line).get("payload") or {}
            except ValueError:
                continue
            kind = payload.get("type")
            if kind == MESSAGE and payload.get("role") == ASSISTANT:
                yield "message", joined(payload.get("content"))
            elif kind == REASONING:
                yield "reasoning", joined(payload.get("content"))
            elif kind in TOOL_INPUT_FIELDS:
                yield "tool_input", payload.get(TOOL_INPUT_FIELDS[kind]) or ""


def exec_log_texts(path: Path) -> Iterator[tuple[str, str]]:
    """The model's texts in one `ling exec --json` log: (kind, text)."""
    kinds = {
        "agent_message": ("message", "text"),
        "command_execution": ("command", "command"),
        "reasoning": ("reasoning", "text"),
    }
    with path.open(errors="replace") as handle:
        for line in handle:
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            item = record.get("item") or {}
            if record.get("type") == "item.completed" and item.get("type") in kinds:
                kind, field = kinds[item["type"]]
                yield kind, item.get(field) or ""


def main() -> int:
    """Writes the corpus; prints how many texts it holds."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    home = Path.home()
    runs = home / SWE_BENCH_RUNS
    sources = [
        (path, rollout_texts)
        for path in sorted((home / ".mightling" / "sessions").rglob("*.jsonl"))
    ]
    sources += [
        (path, rollout_texts)
        for path in sorted(runs.glob("*/scratch/*/codex-home/sessions/**/*.jsonl"))
    ]
    sources += [(path, exec_log_texts) for path in sorted(runs.glob("*/logs/*.jsonl"))]
    seen: set[str] = set()
    with args.output.open("w") as out:
        for path, reader in sources:
            for kind, text in reader(path):
                digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
                if len(text) < MIN_LENGTH or digest in seen:
                    continue
                seen.add(digest)
                out.write(json.dumps({"source": str(path), "kind": kind, "text": text}) + "\n")
    print(f"{len(seen)} texts from {len(sources)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
