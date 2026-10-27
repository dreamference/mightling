"""Long-prompt correctness probes for a running model server: greedy determinism, and needle retrieval.

Two failures a speed figure cannot see, both silent on SM121:

- `determinism`: the same greedy request, sent several times, must come back identical. vLLM #54521
  (open) makes greedy output nondeterministic, and corrupts it, once a prompt passes the sparse
  attention's indexer budget of 2,048 tokens, which every agent prompt does. The probe runs a
  control prompt under the budget and a long one over it, so a failure says which side it is on.
- `needle`: one fact hidden at several depths of a long filler prompt must be retrieved. A community
  build saw long-context output corrupted silently on GB10.

    python3 scripts/long_prompt_probe.py determinism [--host http://localhost:8000] [--tokens 6000] [--runs 5]
    python3 scripts/long_prompt_probe.py needle [--host http://localhost:8000] [--tokens 120000]

Prompt sizes are calibrated against the server's own token count (one short request first), and
each prompt starts with a random line so the prefix cache cannot answer for it. Thinking is off and
sampling greedy. Exit status 1 when any check fails. Standard library only; it talks to the model
server and nothing else.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Final, List, Optional, Tuple

# Under the indexer budget (2,048 tokens), with room for the template and the instruction.
CONTROL_TOKENS: Final[int] = 1_500
CALIBRATION_LINES: Final[int] = 200
TIMEOUT_S: Final[int] = 1_800
ADJECTIVES: Final[Tuple[str, ...]] = ("amber", "brisk", "cobalt", "dusty", "eager", "frozen", "gilded", "hollow")
NOUNS: Final[Tuple[str, ...]] = ("crate", "barrel", "pallet", "drum", "carton", "sack", "spool", "tank", "bale")


def filler_line(index: int) -> str:
    """One distinct, deterministic record line."""
    return (f"record {index:06d}: the {ADJECTIVES[index % len(ADJECTIVES)]} "
            f"{NOUNS[(index * 7) % len(NOUNS)]} moved to bay {(index * 37) % 1000:03d}.")


def filler(lines: int, salt: str) -> List[str]:
    """`lines` record lines after a random first line that keeps the prefix cache out."""
    return [f"run {salt}"] + [filler_line(i) for i in range(lines)]


def chat(host: str, model: str, content: str, max_tokens: int) -> Dict[str, Any]:
    """One greedy, non-streamed chat completion: the text and the token counts."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    request = urllib.request.Request(f"{host}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        data = json.load(response)
    usage = data.get("usage") or {}
    return {
        "text": (data["choices"][0]["message"].get("content") or ""),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "seconds": round(time.monotonic() - start, 2),
    }


def served_model(host: str) -> Tuple[str, int]:
    """The model the server serves, and its context length (0 if it does not say)."""
    with urllib.request.urlopen(f"{host}/v1/models", timeout=10) as response:
        model = json.load(response)["data"][0]
    return str(model["id"]), int(model.get("max_model_len") or 0)


def tokens_per_line(host: str, model: str) -> float:
    """Tokens per filler line, from the server's count of a short prompt."""
    base = chat(host, model, "Reply with OK.", 1)["prompt_tokens"]
    lines = filler(CALIBRATION_LINES, uuid.uuid4().hex)
    longer = chat(host, model, "\n".join(lines) + "\nReply with OK.", 1)["prompt_tokens"]
    return max(1.0, (longer - base) / CALIBRATION_LINES)


def corrupted(text: str) -> Optional[str]:
    """Why an answer looks corrupted (the SM121 symptoms: runs of '!', control bytes), or None."""
    if not text.strip():
        return "empty"
    bangs = text.count("!")
    if bangs > max(8, len(text) // 10):
        return f"{bangs} '!' in {len(text)} characters"
    if any(ord(c) < 9 or 13 < ord(c) < 32 for c in text):
        return "control characters"
    return None


def first_difference(a: str, b: str) -> int:
    """Index of the first character where two texts differ (the shorter's length if one is a prefix)."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def determinism_case(host: str, model: str, lines: int, runs: int, parallel: int,
                     max_tokens: int) -> Dict[str, Any]:
    """The same greedy request `runs` times: are the answers identical and clean?"""
    prompt = ("\n".join(filler(lines, uuid.uuid4().hex))
              + "\n\nContinue this list with the next 30 records, in exactly the same format, "
                "one per line, and nothing else.")
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        answers = list(pool.map(lambda _: chat(host, model, prompt, max_tokens), range(runs)))
    texts = [a["text"] for a in answers]
    reference = texts[0]
    differing = [i for i, t in enumerate(texts) if t != reference]
    problems = {i: corrupted(t) for i, t in enumerate(texts) if corrupted(t)}
    return {
        "prompt_tokens": answers[0]["prompt_tokens"],
        "runs": runs,
        "parallel": parallel,
        "identical": not differing,
        "differing_runs": differing,
        "first_difference": [first_difference(reference, texts[i]) for i in differing],
        "corrupted": problems,
        "seconds": [a["seconds"] for a in answers],
        "sample": reference[:400],
        "passed": not differing and not problems,
    }


def run_determinism(args: argparse.Namespace) -> int:
    model, context = served_model(args.host)
    per_line = tokens_per_line(args.host, model)
    target = args.tokens if not context else min(args.tokens, context - args.max_tokens - 256)
    result = {"model": model, "tokens_per_line": round(per_line, 2), "cases": {}}
    for name, tokens in (("control", CONTROL_TOKENS), ("long", target)):
        lines = max(1, int(tokens / per_line))
        case = determinism_case(args.host, model, lines, args.runs, args.parallel, args.max_tokens)
        result["cases"][name] = case
        verdict = "✅" if case["passed"] else "❌"
        print(f"{verdict} {name}: {case['prompt_tokens']} prompt tokens, {case['runs']} runs "
              f"({case['parallel']} at once): {'identical' if case['identical'] else 'runs ' + str(case['differing_runs']) + ' differ at ' + str(case['first_difference'])}"
              f"{'; corrupted: ' + json.dumps(case['corrupted']) if case['corrupted'] else ''}", flush=True)
    return finish(result, args.out, all(c["passed"] for c in result["cases"].values()))


def run_needle(args: argparse.Namespace) -> int:
    model, context = served_model(args.host)
    per_line = tokens_per_line(args.host, model)
    result = {"model": model, "context": context, "tokens_per_line": round(per_line, 2), "cases": []}
    passed = True
    for tokens in args.tokens:
        if context and tokens + 512 > context:
            print(f"⚠️  {tokens} tokens: beyond the served context of {context}; skipped.", flush=True)
            result["cases"].append({"tokens": tokens, "skipped": f"context {context}"})
            passed = False
            continue
        lines = max(1, int(tokens / per_line))
        for depth in args.depths:
            code = f"{random.randint(0, 9_999_999):07d}"
            body = filler(lines, uuid.uuid4().hex)
            body.insert(1 + int(lines * depth), f"Note for the reader: the vault code is {code}.")
            prompt = "\n".join(body) + "\n\nWhat is the vault code given in the text above? Reply with the code only."
            answer = chat(args.host, model, prompt, 32)
            found = code in answer["text"]
            problem = corrupted(answer["text"])
            ok = found and not problem
            passed = passed and ok
            result["cases"].append({"tokens": tokens, "depth": depth, "prompt_tokens": answer["prompt_tokens"],
                                    "found": found, "corrupted": problem, "answer": answer["text"][:200],
                                    "seconds": answer["seconds"], "passed": ok})
            print(f"{'✅' if ok else '❌'} {answer['prompt_tokens']} prompt tokens, needle at {depth:.0%}: "
                  f"{'found' if found else 'not found'} in {answer['seconds']} s"
                  f"{'; corrupted: ' + problem if problem else ''} ({answer['text'][:60]!r})", flush=True)
    return finish(result, args.out, passed)


def finish(result: Dict[str, Any], out: Optional[str], passed: bool) -> int:
    result["passed"] = passed
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return 0 if passed else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="probe", required=True)
    det = sub.add_parser("determinism", help="Greedy determinism under and over the indexer budget")
    det.add_argument("--tokens", type=int, default=6_000, help="Size of the long prompt (default 6,000)")
    det.add_argument("--runs", type=int, default=5)
    det.add_argument("--parallel", type=int, default=1, help="Requests in flight at once (batching can change kernels)")
    det.add_argument("--max-tokens", type=int, default=600)
    needle = sub.add_parser("needle", help="Needle retrieval in a long prompt")
    needle.add_argument("--tokens", type=int, nargs="+", default=[120_000], help="Prompt sizes (default 120,000)")
    needle.add_argument("--depths", type=float, nargs="+", default=[0.1, 0.5, 0.9])
    for probe in (det, needle):
        probe.add_argument("--host", default="http://localhost:8000")
        probe.add_argument("--out", default=None, help="Write the result as JSON here")
    args = parser.parse_args(argv)
    args.host = args.host.rstrip("/")
    return run_determinism(args) if args.probe == "determinism" else run_needle(args)


if __name__ == "__main__":
    sys.exit(main())
