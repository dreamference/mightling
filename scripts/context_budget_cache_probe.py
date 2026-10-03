#!/usr/bin/env python3
"""Measures what a masking move costs the model server's prefix cache.

This is the probe of specs/DREAMFERENCE_PUFFIN_CONTEXT_BUDGET.md §1.7. It sends a synthetic
conversation to the local model server's `/v1/responses` endpoint, one request per turn,
then three masking moves that replace the oldest turns with one-line placeholders, and
prints the input and cached tokens the server reports for each request. Every request
asks for two output tokens, so the run takes about a minute on an idle server.

    context_budget_cache_probe.py [BASE_URL]      default http://127.0.0.1:8000
"""

import json
import random
import sys
import time
import urllib.request
from typing import Final, Set

TURNS: Final[int] = 30
LINES_PER_TURN: Final[int] = 40
SYSTEM_LINES: Final[int] = 250


def segment(name: str, lines: int, tag: str) -> str:
    """Returns deterministic, code-like filler text unique to this run."""
    rng = random.Random(name + tag)
    return "".join(f"{name} line {i}: value_{rng.randint(0, 10**6)} = compute({rng.randint(0, 999)})\n"
                   for i in range(lines))


def send(base_url: str, model: str, label: str, prompt: str) -> None:
    """Sends one request and prints the server's input and cached token counts."""
    body = json.dumps({"model": model, "input": prompt, "max_output_tokens": 2, "temperature": 0}).encode()
    request = urllib.request.Request(f"{base_url}/v1/responses", body, {"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(request) as response:
        usage = json.load(response)["usage"]
    cached = usage["input_tokens_details"]["cached_tokens"]
    print(f"{label:24s} input {usage['input_tokens']:6d}  cached {cached:6d}  "
          f"prefilled {usage['input_tokens'] - cached:6d}  {time.time() - started:5.2f} s", flush=True)


def main() -> int:
    """Runs the probe against the server named on the command line."""
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    with urllib.request.urlopen(f"{base_url}/v1/models") as response:
        model = json.load(response)["data"][0]["id"]
    tag = str(random.randint(0, 10**9))
    system = f"SYSTEM {tag}\n" + segment("sys", SYSTEM_LINES, tag)
    turns = [segment(f"turn{k}", LINES_PER_TURN, tag) for k in range(TURNS)]

    def view(count: int, masked: Set[int]) -> str:
        return system + "".join(f"[output of turn{i} removed]\n" if i in masked else turns[i]
                                for i in range(count))

    for count in range(1, 21):
        send(base_url, model, f"turn {count}", view(count, set()))
    send(base_url, model, "move 1: mask 0-7", view(20, set(range(8))))
    for count in range(21, 27):
        send(base_url, model, f"turn {count}", view(count, set(range(8))))
    send(base_url, model, "move 2: mask 0-13", view(26, set(range(14))))
    for count in range(27, 30):
        send(base_url, model, f"turn {count}", view(count, set(range(14))))
    send(base_url, model, "move 3: mask 0-19", view(29, set(range(20))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
