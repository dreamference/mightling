#!/usr/bin/env python3
"""Replays recorded `mling` rollouts to measure what fills the context.

This is Phase 0 of specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md. It reads the session
rollouts a SWE-bench run leaves under its scratch directory and needs nothing running.

    context_budget_replay.py kinds   RUN_DIR...   tool output per kind of call (§1.1)
    context_budget_replay.py rereads RUN_DIR...   re-reads of file regions (§1.4)
    context_budget_replay.py mask    RUN_DIR...   compactions and mask moves under a
                                                  masking policy, per limit (§4.1)
    context_budget_replay.py mask --policy HIGH,LOW,STEP RUN_DIR...
                                                  the same for one policy (fractions of
                                                  the limit, step in tokens), repeatable

RUN_DIR is a run directory such as ~/.local/share/dreamference/swe-bench/runs/idx14b-on.
"""

import collections
import glob
import json
import re
import sys
from typing import Dict, Final, Iterator, List, Optional, Tuple

# Prefill rate of Qwen3.8-27B on SGLang measured by the cache probe of §1.7 (idle server).
PREFILL_TOKENS_PER_S: Final[int] = 1450
# Median compaction request (96 s) plus the first request after it (8 s), measured from the
# rollouts of idx14b-on/off at three instances at once (§1.8). The 24 s of COMPACTION §9.2
# was one stream at a 14K context.
COMPACTION_S: Final[int] = 104
SUMMARY_TOKENS: Final[int] = 3000
# A placeholder of §4.1: one line naming the saved copy, the exit code and the last line.
PLACEHOLDER_CHARS: Final[int] = 200
KEEP_RECENT: Final[int] = 10
MIN_MASKABLE_CHARS: Final[int] = 600
MIN_STEP_TOKENS: Final[int] = 8000
# (high, low, step): the default of §4.1 first, then v1's policy, its comparison arm.
POLICIES: Final[Tuple[Tuple[float, float, int], ...]] = ((0.85, 0.50, 16000), (0.82, 0.55, 8000))
LIMITS: Final[Tuple[int, ...]] = (44000, 49152, 65536, 94144)


def rollouts(run_dir: str) -> List[str]:
    """Returns the rollout files of every instance in a run, sorted."""
    pattern = f"{run_dir}/scratch/*/codex-home/sessions/**/rollout*.jsonl"
    return sorted(glob.glob(pattern, recursive=True))


def records(path: str) -> Iterator[dict]:
    """Yields each JSON record of a rollout file."""
    with open(path) as handle:
        for line in handle:
            yield json.loads(line)


def output_text(payload: dict) -> str:
    """Returns a function_call_output's body as the model received it."""
    output = payload.get("output")
    return output if isinstance(output, str) else json.dumps(output)


def command_of(arguments: str) -> str:
    """Returns the shell command of an exec_command call, or an empty string."""
    try:
        command = json.loads(arguments).get("cmd", "")
    except (ValueError, AttributeError):
        return ""
    return command if isinstance(command, str) else " ".join(command)


def kind_of(name: str, arguments: str) -> str:
    """Classifies a call into the kinds of §1.1."""
    if name != "exec_command":
        return f"index: {name}" if name.startswith("code_") else name
    command = re.sub(r"^\s*(cd \S+\s*(&&|;)\s*)+", "", command_of(arguments))
    if re.search(r"pytest|runtests\.py|sympy\.test\(|bin/test|unittest|tox|doctest", command):
        return "shell: tests"
    if re.match(r"(sed -n|cat |head |tail |nl |awk )", command):
        return "shell: file reads"
    if re.match(r"(grep|rg|git grep|find|ls)\b", command):
        return "shell: search and listing"
    if re.match(r"git\b", command):
        return "shell: git"
    if re.match(r"python3? (-c|-|<<)", command):
        return "shell: python snippets"
    return "shell: other"


def kinds(run_dir: str) -> None:
    """Prints tool output per kind of call for one run."""
    chars: collections.Counter = collections.Counter()
    calls: collections.Counter = collections.Counter()
    compactions = 0
    for path in rollouts(run_dir):
        names: Dict[str, str] = {}
        for record in records(path):
            if record.get("type") == "compacted":
                compactions += 1
            if record.get("type") != "response_item":
                continue
            payload = record["payload"]
            if payload.get("type") == "function_call":
                names[payload["call_id"]] = kind_of(payload["name"], payload.get("arguments") or "")
            elif payload.get("type") == "function_call_output":
                kind = names.get(payload["call_id"], "?")
                chars[kind] += len(output_text(payload))
                calls[kind] += 1
    total = sum(chars.values())
    print(f"== {run_dir}: {sum(calls.values())} calls, {total} chars, {compactions} compactions")
    for kind, count in chars.most_common():
        print(f"   {kind:30s} {calls[kind]:5d} calls {count:9d} chars {100 * count / total:5.1f}%")


def regions(name: str, arguments: str, output: str) -> List[Tuple[str, int, int]]:
    """Returns the (file, first line, last line) regions a read call showed."""
    found = []
    if name == "code_show":
        match = re.search(r"\(([^:()]+\.\w+):(\d+)-(\d+)\)", output)
        if match:
            found.append((match.group(1), int(match.group(2)), int(match.group(3))))
    elif name == "exec_command":
        for match in re.finditer(r"sed -n ['\"]?(\d+),(\d+)p['\"]? (\S+)", arguments):
            path = re.sub(r"[\"'\\}]+$", "", match.group(3)).split("/testbed/")[-1]
            found.append((path, int(match.group(1)), int(match.group(2))))
        for match in re.finditer(r"\bcat (\S+\.py)", arguments):
            path = re.sub(r"[\"'\\}]+$", "", match.group(1)).split("/testbed/")[-1]
            found.append((path, 1, 10**6))
    return found


def written_files(name: str, arguments: str) -> set:
    """Returns the files a call plausibly wrote."""
    files = set()
    if name in ("apply_patch", "edit_file"):
        files |= set(re.findall(r"(?:Update|Add) File: (\S+)", arguments))
        files |= set(re.findall(r'"path":\s*"([^"]+)"', arguments))
    elif name == "exec_command" and re.search(r"open\([^)]*['\"]w['\"]|write_text|sed -i|cat >|> \S+\.py|tee ", arguments):
        files |= set(re.findall(r"([\w./-]+\.py)", arguments))
    return {path.split("/testbed/")[-1] for path in files}


def rereads(run_dir: str) -> None:
    """Prints re-read characters, split as in §1.4, for one run."""
    read_chars = same = after_write = across = recent_hits = total = 0
    for path in rollouts(run_dir):
        names: Dict[str, Tuple[str, str]] = {}
        seen: List[Tuple[str, int, int, int, int]] = []
        recent: List[Tuple[str, int, int, int]] = []
        versions: Dict[str, int] = {}
        segment = 0
        for record in records(path):
            if record.get("type") == "compacted":
                segment += 1
                recent = []
                continue
            if record.get("type") != "response_item":
                continue
            payload = record["payload"]
            if payload.get("type") == "function_call":
                arguments = payload.get("arguments") or ""
                names[payload["call_id"]] = (payload["name"], arguments)
                for written in written_files(payload["name"], arguments):
                    versions[written] = versions.get(written, 0) + 1
                continue
            if payload.get("type") != "function_call_output":
                continue
            output = output_text(payload)
            total += len(output)
            name, arguments = names.get(payload["call_id"], ("?", ""))
            shown = regions(name, arguments, output)
            if not shown:
                recent.append(("", 0, 0, -1))
                continue
            read_chars += len(output)
            hits = [old for new in shown for old in seen if old[0] == new[0] and old[1] <= new[2] and old[2] >= new[1]]
            if hits:
                if all(old[3] != segment for old in hits):
                    across += len(output)
                elif any(old[4] != versions.get(old[0], 0) for old in hits):
                    after_write += len(output)
                else:
                    same += len(output)
            if all(any(old[0] == new[0] and old[1] <= new[1] and old[2] >= new[2] and old[3] == versions.get(new[0], 0)
                       for old in recent[-KEEP_RECENT:]) for new in shown):
                recent_hits += len(output)
            seen += [(new[0], new[1], new[2], segment, versions.get(new[0], 0)) for new in shown]
            recent.append((shown[0][0], shown[0][1], shown[0][2], versions.get(shown[0][0], 0)))
    print(f"== {run_dir}: file-read chars {read_chars}; re-read in the same segment, no write {same}; "
          f"after a write {after_write}; across a compaction {across}; "
          f"fully inside one of the last {KEEP_RECENT} outputs {recent_hits} ({100 * recent_hits / total:.1f}% of all output)")


def trajectory(path: str) -> Tuple[int, List[Tuple[str, int]], float]:
    """Returns a rollout's first input tokens, its items and its chars-per-token ratio.

    The ratio is calibrated on the stretch before the first compaction: the characters
    of the items recorded there against the growth of the server's input token count.
    """
    items: List[Tuple[str, int]] = []
    first: Optional[int] = None
    last: Optional[int] = None
    calibrated = 0
    chars = 0
    compacted = False
    for record in records(path):
        kind = record.get("type")
        payload = record.get("payload") or {}
        if kind == "compacted":
            compacted = True
        if kind == "event_msg" and payload.get("type") == "token_count":
            tokens = ((payload.get("info") or {}).get("last_token_usage") or {}).get("input_tokens")
            if tokens:
                first = first or tokens
                if not compacted:
                    last, calibrated = tokens, chars
        if kind != "response_item":
            continue
        item_type = payload.get("type")
        if item_type == "function_call_output":
            size, label = len(output_text(payload)), "out"
        elif item_type == "function_call":
            size, label = len(payload.get("arguments") or "") + 40, "args"
        elif item_type == "message" and payload.get("role") == "assistant":
            size, label = len(json.dumps(payload.get("content"))), "msg"
        else:
            continue
        items.append((label, size))
        if not compacted:
            chars += size
    ratio = calibrated / (last - first) if first and last and last > first and calibrated else 3.5
    return first or 0, items, ratio


def replay(first: int, items: List[Tuple[str, int]], ratio: float, limit: int,
           high: Optional[int], low: Optional[int], mask_args: bool = False,
           step: int = MIN_STEP_TOKENS) -> Tuple[int, int, float, float]:
    """Replays one trajectory; returns compactions, mask moves and two re-prefill estimates.

    The first estimate re-prefills from the oldest item a move masks. The second, which
    the cache probe of §1.7 supports, re-prefills from the first masked item of the
    context: SGLang keeps the hybrid model's state at the branch point the first move
    made and at request ends, none of which lies between it and a later move's items.
    """
    context: List[List] = []
    compactions = moves = 0
    refill_newest = refill_first = 0.0
    next_trigger = high

    def tokens(entry: List) -> float:
        return (PLACEHOLDER_CHARS if entry[2] else entry[1]) / ratio

    def size() -> float:
        return first + sum(tokens(entry) for entry in context)

    maskable = ("out", "args") if mask_args else ("out",)
    for label, chars in items:
        context.append([label, chars, False])
        if high is not None and size() > next_trigger:
            candidates = [i for i, entry in enumerate(context)
                          if entry[0] in maskable and not entry[2] and entry[1] >= MIN_MASKABLE_CHARS]
            oldest = None
            for index in candidates[:max(0, len(candidates) - KEEP_RECENT)]:
                if size() <= low:
                    break
                context[index][2] = True
                oldest = index if oldest is None else oldest
            if oldest is not None:
                moves += 1
                refill_newest += sum(tokens(entry) for entry in context[oldest:]) / PREFILL_TOKENS_PER_S
                first_masked = next(i for i, entry in enumerate(context) if entry[2])
                refill_first += sum(tokens(entry) for entry in context[first_masked:]) / PREFILL_TOKENS_PER_S
            next_trigger = max(high, size() + step)
        if size() > limit:
            compactions += 1
            context = [["msg", SUMMARY_TOKENS * ratio, False]]
            next_trigger = high
    return compactions, moves, refill_newest, refill_first


def mask(run_dir: str, policies: Tuple[Tuple[float, float, int], ...] = POLICIES) -> None:
    """Prints compactions, mask moves and time per limit, unmasked and masked, for one run."""
    runs = [trajectory(path) for path in rollouts(run_dir)]
    print(f"== {run_dir}")
    for limit in LIMITS:
        variants = [("unmasked", None, None, False, MIN_STEP_TOKENS)]
        for high_share, low_share, step in policies:
            name = f"masked {high_share:.2f}/{low_share:.2f}/{step // 1000}K"
            variants.append((name, int(limit * high_share), int(limit * low_share), False, step))
        first_high, first_low, first_step = policies[0]
        variants.append((f"+ old commands {first_high:.2f}/{first_low:.2f}/{first_step // 1000}K",
                         int(limit * first_high), int(limit * first_low), True, first_step))
        for label, high, low, mask_args, step in variants:
            results = [replay(*run, limit, high, low, mask_args, step) for run in runs]
            compactions = sum(result[0] for result in results)
            moves = sum(result[1] for result in results)
            newest = sum(result[2] for result in results) / 60
            first_masked = sum(result[3] for result in results) / 60
            compacting = compactions * COMPACTION_S / 60
            print(f"   limit {limit:6d} {label:30s} compactions {compactions:3d} in "
                  f"{sum(1 for r in results if r[0]):2d} instances; mask moves {moves:3d}; "
                  f"re-prefill {newest:4.1f}-{first_masked:4.1f} min; compaction {compacting:4.1f} min; "
                  f"total {compacting + newest:4.1f}-{compacting + first_masked:4.1f} min")


def main() -> int:
    """Runs one of the three reports over the run directories given."""
    commands = {"kinds": kinds, "rereads": rereads, "mask": mask}
    arguments = sys.argv[1:]
    if len(arguments) < 2 or arguments[0] not in commands:
        print(__doc__)
        return 1
    command, rest = arguments[0], arguments[1:]
    policies: List[Tuple[float, float, int]] = []
    while command == "mask" and len(rest) >= 2 and rest[0] == "--policy":
        high, low, step = rest[1].split(",")
        policies.append((float(high), float(low), int(step)))
        rest = rest[2:]
    for run_dir in rest:
        if policies:
            mask(run_dir.rstrip("/"), tuple(policies))
        else:
            commands[command](run_dir.rstrip("/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
