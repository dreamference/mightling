"""Single-stream decode and prefill speed of the running model server, with a correctness check.

The method of the production figures (specs/DREAMFERENCE_INFERENCE.md §5.3): one stream,
temperature 0, thinking off, a warm-up request per prompt, then the median of three, decode counted
net of time to first token. The prompts behind those figures (prose 25.5, code 50.3, JSON 87.0
tok/s) were not recorded, so a comparison measures every arm with this script on the same night
rather than against them. Night 2 (specs/DREAMFERENCE_MODELS.md §2.2) runs it on both checkpoints.

Speed is worthless without evidence the output is still right (on SM121 a wrong kernel corrupts
silently), so each answer is checked: the prose must be words, the code must compile, the JSON must
parse as the asked-for array. A failed check is reported and makes the exit status 1.

    python3 scripts/decode_speed.py [--host http://localhost:8000] [--runs 3] [--out result.json]

Standard library only, so it runs from any Python. It talks to the model server and nothing else.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
import uuid
from typing import Any, Dict, Final, List, Optional, Tuple

PROMPTS: Final[Dict[str, str]] = {
    "prose": "Write a 500-word essay on the history of the printing press, in plain paragraphs.",
    "code": ("Write a Python implementation of a red-black tree with insert, delete and search, "
             "as one module. Reply with the code only, in one ```python block."),
    "json": ("Return a JSON array of 25 objects describing fictional people, each with the keys id "
             "(an integer), name, email, city and age (an integer). Reply with the JSON only."),
}
MAX_TOKENS: Final[int] = 512
# The prefill probe: 1,100 sentences are about 13,400 fresh tokens with Qwen3.8's tokenizer, the size
# of the production figure's prompt. A random first line keeps the prefix cache from serving any of it.
PREFILL_SENTENCES: Final[int] = 1_100


def post_stream(host: str, model: str, content: str, max_tokens: int) -> Dict[str, Any]:
    """One streamed chat completion: TTFT, decode time, tokens and the text."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    request = urllib.request.Request(f"{host}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    start = time.monotonic()
    first: Optional[float] = None
    text, usage = [], {}
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    if first is None:
                        first = time.monotonic()
                    text.append(piece)
    end = time.monotonic()
    first = first or end
    tokens = int(usage.get("completion_tokens") or 0)
    decode_s = end - first
    return {
        "ttft_s": round(first - start, 3),
        "decode_s": round(decode_s, 3),
        "completion_tokens": tokens,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "decode_tps": round((tokens - 1) / decode_s, 2) if tokens > 1 and decode_s > 0 else 0.0,
        "text": "".join(text),
    }


def correct(kind: str, text: str) -> Tuple[bool, str]:
    """Whether an answer is plausibly right, and why not."""
    if kind == "prose":
        words = re.findall(r"[A-Za-z]{2,}", text)
        if len(words) < 200 or "print" not in text.lower():
            return False, f"{len(words)} words, or the topic is missing"
        return True, ""
    if kind == "code":
        match = re.search(r"```(?:python)?\n(.*?)(?:```|$)", text, re.S)
        source = match.group(1) if match else text
        # A reply cut at max_tokens may end mid-statement; compile what is complete.
        lines = source.splitlines()
        for cut in range(len(lines), max(len(lines) - 15, 0), -1):
            try:
                compile("\n".join(lines[:cut]), "<answer>", "exec")
            except SyntaxError:
                continue
            if "class" in source and "def" in source:
                return True, ""
            return False, "no class or function definitions"
        return False, "the code does not compile"
    match = re.search(r"\[.*", text, re.S)
    candidate = match.group(0) if match else text
    candidate = candidate.split("```")[0]
    try:
        data = json.loads(candidate)
    except ValueError:
        # Cut at max_tokens: the objects that are complete must still parse.
        cut = candidate.rfind("},")
        try:
            data = json.loads(candidate[:cut + 1] + "]") if cut > 0 else None
        except ValueError:
            data = None
    if not isinstance(data, list) or not data or not all(isinstance(o, dict) and "name" in o for o in data):
        return False, "not a JSON array of people"
    return True, ""


def server_info(host: str) -> Dict[str, Any]:
    """SGLang's own speculative acceptance figure, when the server reports one."""
    try:
        with urllib.request.urlopen(f"{host}/get_server_info", timeout=10) as response:
            info = json.loads(response.read())
    except Exception:
        return {}
    states = info.get("internal_states") or [{}]
    value = (states[0] or {}).get("avg_spec_accept_length")
    return {"avg_spec_accept_length": value} if value is not None else {}


def served_model(host: str) -> Tuple[str, Optional[int]]:
    with urllib.request.urlopen(f"{host}/v1/models", timeout=10) as response:
        entry = json.loads(response.read())["data"][0]
    return entry["id"], entry.get("max_model_len")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default=None, help="write the full result as JSON here")
    parser.add_argument("--no-prefill", action="store_true", help="skip the 13K-token prefill probe")
    args = parser.parse_args(argv)
    model, context = served_model(args.host)
    result: Dict[str, Any] = {"model": model, "max_model_len": context, "host": args.host,
                              "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": args.runs, "kinds": {}}
    failures = 0
    print(f"Model {model}: single stream, temperature 0, thinking off, median of {args.runs} after a warm-up")
    for kind, prompt in PROMPTS.items():
        post_stream(args.host, model, prompt, MAX_TOKENS)
        before = server_info(args.host)
        runs = [post_stream(args.host, model, prompt, MAX_TOKENS) for _ in range(args.runs)]
        ok, why = correct(kind, runs[-1]["text"])
        failures += 0 if ok else 1
        identical = len({r["text"] for r in runs}) == 1
        entry = {
            "decode_tps": statistics.median(r["decode_tps"] for r in runs),
            "ttft_s": statistics.median(r["ttft_s"] for r in runs),
            "completion_tokens": [r["completion_tokens"] for r in runs],
            "correct": ok, "why": why, "identical_runs": identical,
            "accept": server_info(args.host) or before,
            "sample": runs[-1]["text"][:400],
        }
        result["kinds"][kind] = entry
        print(f"  {kind:<6} {entry['decode_tps']:6.1f} tok/s  TTFT {entry['ttft_s']:.2f} s  "
              f"tokens {entry['completion_tokens']}  {'correct' if ok else 'WRONG: ' + why}"
              f"{'' if identical else '  (runs differ)'}")
    if not args.no_prefill:
        filler = " ".join(f"item{i} is followed by item{i + 1}." for i in range(PREFILL_SENTENCES))
        prompt = f"Session {uuid.uuid4()}.\n{filler}\nWhich item follows item41? Answer with the item only."
        run = post_stream(args.host, model, prompt, 8)
        tps = run["prompt_tokens"] / run["ttft_s"] if run["ttft_s"] > 0 else 0.0
        ok = "item42" in run["text"]
        failures += 0 if ok else 1
        result["prefill"] = {"prompt_tokens": run["prompt_tokens"], "ttft_s": run["ttft_s"],
                             "prefill_tps": round(tps, 1), "answer": run["text"][:80], "correct": ok}
        print(f"  prefill {run['prompt_tokens']} tokens in {run['ttft_s']:.1f} s: {tps:,.0f} tok/s  "
              f"{'correct' if ok else 'WRONG: ' + run['text'][:40]!r}")
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
