"""Blind judging for default_study.py, with the model server's own model as the judge (the model that
wrote the answers, so it shares their blind spots and may prefer its own register; read the
answers too).

    python default_study_judge.py results/default-study.jsonl [--out results/default-study-judge.jsonl]

Two kinds of request, one at a time:
- `single`: one answer alone (the question, the parts it must cover, the answer; never the level).
  Position cannot bias it. Its `parts` and `errors` are the usable output.
- `rank`: every level's answer for the item, shuffled and labelled A, B, C..., ranked best to worst
  for a phone reader. A secondary signal: in a test run it ranked by length and by position.
"""
import argparse
import json
import random
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

SINGLE = """You grade one answer an assistant gave to a person who reads it on a phone.

- parts: for each listed part of the question, in order, whether the answer actually answers it (true/false). A vague gesture at a part is false.
- errors: factual errors, contradictions, or claims the assistant could not know (for example invented timings, measurements, quotations or sources). Empty list if none.
- notes: one short sentence on what is good or bad.
- score: 1 to 10. 10 = answers every part, correct, enough detail to act on, nothing wasted. Penalise missing parts and errors most; an answer too terse to be useful as much as a padded one.

Reply with JSON only: {"parts": [true, false], "errors": [], "notes": "...", "score": 7}"""

RANK = """You compare answers different versions of an assistant gave to the same question, for a person who reads them on a phone. Judge each on its merits: what it answers, whether it is right, and whether it is the right length for the question. Longer is not better by itself, and the order the answers are listed in means nothing.

Reply with JSON only: {"ranking": ["B", "A", ...], "why": "one sentence"}"""

NO_NETWORK = ("The assistant had no web access in this conversation: do not penalise missing sources, but do "
              "penalise anything presented as freshly checked or measured.")


def ask(host, model, system, prompt):
    body = {"model": model, "temperature": 0, "max_tokens": 1500,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    request = urllib.request.Request(f"{host}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        text = json.load(response)["choices"][0]["message"]["content"]
    found = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(found.group(0)) if found else {"raw": text}
    except json.JSONDecodeError:
        return {"raw": text}


def conversations(results):
    """{item: {level: [(question, answer), ...]}}, the parts per item, and each item's condition."""
    from default_study import ITEMS
    questions = {i[0]: i[3] for i in ITEMS}
    tasks = Path(__file__).resolve().parent / "tasks"
    turns = {}
    for line in open(results):
        r = json.loads(line)
        turns[(r["item"], r["level"], r["turn"])] = r
    out, parts, condition = defaultdict(dict), {}, {}
    for (item, level, turn), r in sorted(turns.items()):
        qs = questions[item] or [(tasks / item / "prompt.txt").read_text().strip()]
        out[item].setdefault(level, []).append((qs[turn - 1], r.get("answer", "")))
        if r.get("parts"):
            parts[item] = r["parts"]
        condition[item] = r.get("condition", "search")
    return out, parts, condition


def transcript(conversation):
    lines = []
    if len(conversation) > 1:
        lines.append("A two-turn conversation: grade the last reply, using the earlier one as context.")
    for number, (question, answer) in enumerate(conversation, start=1):
        lines.append(f"[Question {number}] {question}\n[Reply {number}]\n{answer}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="RadixArk/Qwen3.8-27B-NVFP4")
    parser.add_argument("--items", default="all", help="comma-separated items (default: every item)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    out = Path(args.out or Path(args.results).with_name(Path(args.results).stem + "-judge.jsonl"))
    done = set()
    if out.exists():
        done = {(r["kind"], r["item"], r.get("level")) for r in map(json.loads, open(out))}
    convs, parts, condition = conversations(args.results)

    def write(record):
        with open(out, "a") as f:
            f.write(json.dumps(record) + "\n")

    for item, by_level in sorted(convs.items()):
        if args.items != "all" and item not in args.items.split(","):
            continue
        note = NO_NETWORK if condition[item] == "no-network" else ""
        need = "Parts the final reply must cover, in order: " + "; ".join(parts.get(item, []))
        for level, conversation in sorted(by_level.items()):
            if ("single", item, level) in done:
                continue
            verdict = ask(args.host, args.model, SINGLE, "\n".join(x for x in (note, need, transcript(conversation)) if x))
            write({"kind": "single", "item": item, "level": level, "verdict": verdict})
            print("single", item, level, verdict.get("parts"), verdict.get("score"), flush=True)
        if ("rank", item, None) in done:
            continue
        levels = sorted(by_level)
        random.Random(f"rank-{item}").shuffle(levels)
        labels = {chr(65 + k): level for k, level in enumerate(levels)}
        text = [x for x in (note, need) if x]
        for label, level in labels.items():
            text.append(f"\n=== Answer {label} ===\n{transcript(by_level[level])}")
        verdict = ask(args.host, args.model, RANK, "\n".join(text))
        write({"kind": "rank", "item": item, "level": None, "labels": labels, "verdict": verdict})
        print("rank", item, [labels.get(x) for x in verdict.get("ranking", [])], flush=True)


if __name__ == "__main__":
    main()
