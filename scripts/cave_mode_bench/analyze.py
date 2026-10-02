"""Summarise benchmark results against `off`, within one results file.

    python analyze.py results/batch3.jsonl [more.jsonl ...]
    python analyze.py --drift results/drift.jsonl

Compare levels only within a file: the server's load differed between batches (the median `off`
run took 15.5, 21.2 and 22.7 s in the three batches of 2026-09-30), so a level measured in one
batch must not be compared with `off` from another. Ratios are the geometric mean over tasks of
(median for the level / median for off), with a 95% bootstrap interval that resamples runs
within each task and then tasks.
"""
import argparse
import collections
import json
import math
import random
import statistics

QUESTIONS = {"howto", "debug", "safety", "openq", "repoq"}
CODING = {"bugfix", "implement", "rename", "addflag"}


def ratio(by, level, key, tasks, rng=None):
    logs = []
    for task in sorted(tasks):
        a, b = by.get((task, level)), by.get((task, "off"))
        if not a or not b:
            continue
        if rng:
            a, b = [rng.choice(a) for _ in a], [rng.choice(b) for _ in b]
        x, y = statistics.median(r[key] for r in a), statistics.median(r[key] for r in b)
        if x > 0 and y > 0:
            logs.append(math.log(x / y))
    if not logs:
        return float("nan")
    if rng:
        logs = [rng.choice(logs) for _ in logs]
    return math.exp(sum(logs) / len(logs))


def summary(path):
    records = [json.loads(line) for line in open(path)]
    by = collections.defaultdict(list)
    for r in records:
        by[(r["task"], r["variant"])].append(r)
    rng = random.Random(7)
    off = [r for r in records if r["variant"] == "off"]
    print(f"== {path}: off {len(off)} runs, median wall {statistics.median(r['wall_s'] for r in off):.1f} s")
    for level in dict.fromkeys(r["variant"] for r in records):
        runs = [r for r in records if r["variant"] == level]
        line = f"  {level:11} pass {sum(r['pass'] for r in runs)}/{len(runs)}"
        if any("pass_strict" in r and "wrote_files" in r for r in runs):
            line += f" strict {sum(r.get('pass_strict', r['pass']) for r in runs)}/{len(runs)}"
        line += f"  articles {statistics.mean(r['article_rate'] for r in runs):.1f}%"
        line += f"  offers {sum(r['closing_offer'] for r in runs)}"
        for name, tasks in (("questions", QUESTIONS), ("coding", CODING)):
            for key in ("final_tok", "wall_s"):
                point = ratio(by, level, key, tasks)
                boot = sorted(ratio(by, level, key, tasks, rng) for _ in range(2000))
                line += f" | {name} {key} {point:.2f} [{boot[50]:.2f}, {boot[1949]:.2f}]"
        print(line)


def drift(path):
    records = [json.loads(line) for line in open(path)]
    sessions = collections.defaultdict(dict)
    for r in records:
        sessions[(r["variant"], r["rep"])][r["turn"]] = r
    print("level        rep  final-answer tokens by turn                  turns 1-4  5-8  closing offers")
    for (variant, rep), turns in sorted(sessions.items()):
        tokens = [turns[t]["final_tok"] for t in sorted(turns)]
        offers = sum(turns[t]["closing_offer"] for t in turns)
        print(f"{variant:14} {rep}   {' '.join(f'{x:4}' for x in tokens):49} {sum(tokens[:4]):5} {sum(tokens[4:8]):5}  {offers}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--drift", action="store_true")
    args = parser.parse_args()
    for f in args.files:
        (drift if args.drift else summary)(f)
