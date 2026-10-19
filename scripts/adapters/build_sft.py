#!/usr/bin/env python3
"""Training samples from the exact tokens the served model saw.

During a generation run the model server dumps every finished request (SGLang's
`/configure_logging` with `dump_requests_folder`; see the pipeline doc). Each dump is a pickle
of `(GenerateReqInput, out_dict, created, finished)` tuples, and `GenerateReqInput.input_ids` is
the prompt after SGLang applied the served (patched) chat template to ling's Responses request:
Codex's system prompt, the tool schemas, the no-think form. Training on these ids means never
re-implementing SGLang's Responses-to-template translation.

`extract` turns dumps into JSONL, one request per line (`input_ids`, `output_ids` when the dump
holds the whole completion, `output_text`, `created`). It unpickles with stand-in classes, so it
runs without SGLang installed; dumps are written by our own server only, but a pickle is code,
so run it in a throwaway container when in doubt.

`build` groups requests into segments (a request whose prompt extends an earlier one belongs to
the same segment; a compaction starts a new one), keeps only each segment's last request, matches
segments to the accepted attempts of `select_trajectories.py select` by text only that attempt's
prompts hold (its tool outputs with their timings: a failed attempt has the same issue), and writes one sample per segment: `input_ids` = last prompt + its completion, `labels` = the
assistant tokens only (everything after each `<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n`
up to and including its `<|im_end|>`), the rest -100.

Nothing runs at import; `build` needs `transformers` for the tokenizer only.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PREFIX_KEY_TOKENS: int = 2048
IGNORE: int = -100


# -- extract --------------------------------------------------------------------------------------

class _Stub:
    """Stands in for any class in a dump; keeps the pickled state as attributes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self.__dict__.update(kwargs)

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, tuple) and len(state) == 2:
            state = {**(state[0] or {}), **(state[1] or {})}
        if isinstance(state, dict):
            self.__dict__.update(state)

    def get(self, key: str, default: Any = None) -> Any:
        return self.__dict__.get(key, default)


class _PermissiveUnpickler(pickle.Unpickler):
    SAFE = {("builtins", n) for n in ("dict", "list", "tuple", "set", "frozenset", "int", "float", "str",
                                      "bytes", "bool", "NoneType", "complex")}

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) in self.SAFE or module in ("collections", "datetime"):
            return super().find_class(module, name)
        return type(name, (_Stub,), {"__module__": "stub." + module})


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def extract(paths: Iterable[Path]) -> Iterable[Dict[str, Any]]:
    """Every finished request of the dump files, oldest first."""
    for path in sorted(paths):
        with open(path, "rb") as handle:
            entries = _PermissiveUnpickler(handle).load()
        for entry in entries:
            request, out = entry[0], entry[1]
            created = entry[2] if len(entry) > 2 else None
            input_ids = _field(request, "input_ids")
            if not input_ids or not isinstance(input_ids, (list, tuple)) or isinstance(input_ids[0], (list, tuple)):
                continue  # text-only, batched or embedding requests are not ling's turns
            meta = _field(out, "meta_info") or {}
            output_ids = _field(out, "output_ids")
            completion = meta.get("completion_tokens")
            whole = isinstance(output_ids, (list, tuple)) and completion is not None and len(output_ids) == completion
            yield {
                "rid": _field(request, "rid"),
                "created": created,
                "input_ids": list(input_ids),
                "output_ids": list(output_ids) if whole else None,
                "output_text": _field(out, "text"),
                "finish_reason": meta.get("finish_reason"),
                "dump": path.name,
            }


# -- build ----------------------------------------------------------------------------------------

def last_of_segments(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Requests whose prompt no later request extends: the end of each segment."""
    by_key: Dict[str, List[int]] = {}
    for index, request in enumerate(requests):
        ids = request["input_ids"]
        # Requests sharing a segment share its first tokens; shorter prompts share one bucket.
        key = hashlib.sha1(json.dumps(ids[:PREFIX_KEY_TOKENS]).encode()).hexdigest() \
            if len(ids) >= PREFIX_KEY_TOKENS else "short"
        by_key.setdefault(key, []).append(index)
    ends: List[Dict[str, Any]] = []
    for indices in by_key.values():
        prompts = [requests[i]["input_ids"] for i in indices]
        for position, i in enumerate(indices):
            mine = prompts[position]
            extended = any(len(other) > len(mine) and other[:len(mine)] == mine
                           for j, other in enumerate(prompts) if j != position)
            if not extended:
                ends.append(requests[i])
    return ends


def assistant_labels(ids: Sequence[int], start_header: Sequence[int], empty_think: Sequence[int],
                     end_id: int) -> List[int]:
    """Labels for the assistant spans of a rendered conversation, -100 elsewhere."""
    labels = [IGNORE] * len(ids)
    n, i = len(ids), 0
    header, think = list(start_header), list(empty_think)
    while i < n:
        if list(ids[i:i + len(header)]) == header:
            j = i + len(header)
            if list(ids[j:j + len(think)]) == think:
                j += len(think)
            k = j
            while k < n and ids[k] != end_id:
                k += 1
            for t in range(j, min(k + 1, n)):
                labels[t] = ids[t]
            i = k + 1
        else:
            i += 1
    return labels


def rollout_needles(rollout: Path, every: int = 5, chars: int = 300) -> List[str]:
    """
    Text that only this attempt's prompts contain: tool outputs with their `Wall time` line.

    A failed attempt at the same task has the same issue text, so the issue cannot tell attempts
    apart; the timings of its tool outputs can.
    """
    texts: List[str] = []
    with open(rollout) as handle:
        for line in handle:
            if '_output"' not in line:
                continue
            payload = json.loads(line).get("payload", {})
            if payload.get("type") not in ("function_call_output", "custom_tool_call_output"):
                continue
            output = payload.get("output")
            parts = output if isinstance(output, list) else [{"text": output}]
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if "Wall time" in text and len(text) > 80:
                texts.append(text[:chars])
    return texts[::every] or texts


def build(requests: List[Dict[str, Any]], attempts: List[Dict[str, Any]], runs_dir: Path,
          tokenizer: Any) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """One sample per segment of an accepted attempt."""
    start_header = tokenizer.encode("<|im_start|>assistant\n", add_special_tokens=False)
    empty_think = tokenizer.encode("<think>\n\n</think>\n\n", add_special_tokens=False)
    end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
    needles: Dict[str, List[List[int]]] = {}
    for attempt in attempts:
        key = f"{attempt['run']}/{attempt['instance_id']}"
        for rollout in attempt["rollouts"]:
            for text in rollout_needles(runs_dir / attempt["run"] / rollout):
                # Inner tokens only: the ends of a cut string may tokenize differently in context.
                tokens = tokenizer.encode(text, add_special_tokens=False)[4:-4]
                if len(tokens) >= 12:
                    needles.setdefault(key, []).append(tokens[:32])
    samples: List[Dict[str, Any]] = []
    counts = {"segments": 0, "matched": 0, "no_completion": 0}
    for request in last_of_segments(requests):
        counts["segments"] += 1
        prompt = request["input_ids"]
        owner = next((key for key, group in needles.items()
                      if any(_contains(prompt, needle) for needle in group)), None)
        if owner is None:
            continue
        counts["matched"] += 1
        completion = request["output_ids"]
        if completion is None:
            if not request.get("output_text"):
                counts["no_completion"] += 1
                continue
            completion = tokenizer.encode(request["output_text"], add_special_tokens=False) + [end_id]
        ids = list(prompt) + list(completion)
        if ids[-1] != end_id:
            ids.append(end_id)
        labels = assistant_labels(ids, start_header, empty_think, end_id)
        # The completion follows the generation prompt directly: label it whole.
        for t in range(len(prompt), len(ids)):
            labels[t] = ids[t]
        samples.append({"attempt": owner, "input_ids": ids, "labels": labels,
                        "tokens": len(ids), "trained": sum(1 for x in labels if x != IGNORE)})
    return samples, counts


def _contains(haystack: Sequence[int], needle: Sequence[int]) -> bool:
    first, m = needle[0], len(needle)
    for i, token in enumerate(haystack):
        if token == first and list(haystack[i:i + m]) == list(needle):
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="SGLang request dumps -> requests JSONL")
    ex.add_argument("dumps", nargs="+", help="dump .pkl files or globs")
    ex.add_argument("--out", type=Path, required=True)
    bd = sub.add_parser("build", help="requests JSONL + accepted attempts -> training samples")
    bd.add_argument("requests", type=Path)
    bd.add_argument("--attempts", type=Path, required=True, help="manifest from select_trajectories.py select")
    bd.add_argument("--runs-dir", type=Path, default=Path("~/.local/share/dreamference/swe-bench/runs").expanduser())
    bd.add_argument("--tokenizer", required=True, help="the served checkpoint's directory")
    bd.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "extract":
        paths = [Path(p) for spec in args.dumps for p in sorted(glob.glob(spec))]
        count = 0
        with open(args.out, "w") as handle:
            for request in extract(paths):
                handle.write(json.dumps(request) + "\n")
                count += 1
        print(f"✅ {count} requests from {len(paths)} dumps -> {args.out}")
        return 0

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    requests = [json.loads(line) for line in open(args.requests)]
    attempts = [json.loads(line) for line in open(args.attempts)]
    samples, counts = build(requests, attempts, args.runs_dir, tokenizer)
    with open(args.out, "w") as handle:
        for sample in samples:
            handle.write(json.dumps(sample) + "\n")
    trained = sum(s["trained"] for s in samples)
    total = sum(s["tokens"] for s in samples)
    print(f"✅ {len(samples)} samples ({counts['segments']} segments, {counts['matched']} matched, "
          f"{counts['no_completion']} without a completion): {total / 1e6:.2f}M tokens, "
          f"{trained / 1e6:.2f}M trained -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
