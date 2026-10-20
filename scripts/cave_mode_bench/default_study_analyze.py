"""Summarise default_study.py's results and default_study_judge.py's verdicts per level.

    python default_study_analyze.py results/default-study-nonet.jsonl [--judge results/default-study-nonet-judge.jsonl]

Ratios are the geometric mean over items of (value at the level / value at `off`), as analyze.py
computes them. One run per cell, so they carry the run-to-run noise run.py's batches measured
(`off` against itself spans about [0.7, 1.4] on questions, §1.1).
"""
import argparse
import json
import math
import re
import statistics as st
from collections import defaultdict

LEVELS = ["off", "lite", "full", "ultra"]


def geo(values):
    values = [v for v in values if v and v > 0]
    return math.exp(sum(math.log(v) for v in values) / len(values)) if values else float("nan")


def mean(xs):
    return st.mean(xs) if xs else float("nan")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("--judge", default=None)
    parser.add_argument("--items", default="all")
    args = parser.parse_args()
    turns = {}
    for line in open(args.results):
        r = json.loads(line)
        if args.items == "all" or r["item"] in args.items.split(","):
            turns[(r["item"], r["level"], r["turn"])] = r
    cells = defaultdict(list)
    for (item, level, _), r in sorted(turns.items()):
        cells[(item, level)].append(r)
    items = sorted({item for item, _ in cells})
    single, ranks = defaultdict(dict), defaultdict(list)
    if args.judge:
        for line in open(args.judge):
            v = json.loads(line)
            if v["item"] not in items:
                continue
            if v["kind"] == "single":
                single[v["level"]][v["item"]] = v["verdict"]
            elif v["kind"] == "rank":
                order = [v["labels"].get(x) for x in v["verdict"].get("ranking", [])]
                for level in LEVELS:
                    if level in order:
                        ranks[level].append(order.index(level) + 1)
    wall = lambda i, lv: sum(r["wall_s"] for r in cells[(i, lv)])
    out = lambda i, lv: sum(r.get("out_tok") or 0 for r in cells[(i, lv)])
    print(f"{len(items)} items: {', '.join(items)}")
    print(f"{'level':6} {'cells':>5} {'answer tok med':>14} {'vs off':>6} {'output vs off':>13} {'wall vs off':>11} {'wall med s':>10} "
          f"{'over 3 sent.':>12} {'offers':>6} {'files':>5} {'checks':>6} | {'parts':>5} {'all parts':>9} {'errors':>6} {'score':>5} {'rank':>4}")
    for level in LEVELS:
        mine = [i for i in items if (i, level) in cells]
        both = [i for i in mine if (i, "off") in cells]
        last = [cells[(i, level)][-1]["final_tok"] for i in mine]
        tok_ratio = geo([cells[(i, level)][-1]["final_tok"] / cells[(i, "off")][-1]["final_tok"] for i in both if cells[(i, "off")][-1]["final_tok"]])
        out_ratio = geo([out(i, level) / out(i, "off") for i in both if out(i, "off")])
        wall_ratio = geo([wall(i, level) / wall(i, "off") for i in both])
        walls = [wall(i, level) for i in mine]
        # prose sentences outside code over the three ultra allows, last turn
        over = 0
        for i in mine:
            text = cells[(i, level)][-1].get("answer", "")
            prose = re.sub(r"```.*?```", " ", text, flags=re.S)
            prose = "\n".join(line for line in prose.splitlines() if not re.match(r"\s*(\[\d+\]|-\s*\[\d+\]|Sources?:)", line))
            sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", prose) if re.search(r"[A-Za-z]{3}", s)]
            over += len(sentences) > 3
        offers = sum(r.get("closing_offer", False) for i in mine for r in cells[(i, level)])
        files = sum(bool(cells[(i, level)][-1].get("wrote_files")) for i in mine)
        checks = [cells[(i, level)][-1]["check_pass"] for i in mine if "check_pass" in cells[(i, level)][-1]]
        verdicts = [single[level][i] for i in mine if i in single[level]]
        parts = [sum(map(bool, v.get("parts") or [])) / max(1, len(v.get("parts") or [])) for v in verdicts]
        allp = sum(bool(v.get("parts")) and all(v["parts"]) for v in verdicts)
        errors = sum(len(v.get("errors") or []) for v in verdicts)
        scores = [v.get("score") for v in verdicts if isinstance(v.get("score"), (int, float))]
        print(f"{level:6} {len(mine):>5} {st.median(last) if last else 0:>14} {tok_ratio:>6.2f} {out_ratio:>13.2f} {wall_ratio:>11.2f} "
              f"{st.median(walls) if walls else 0:>10.1f} {over:>5}/{len(mine):<6} {offers:>6} {files:>5} "
              f"{sum(checks)}/{len(checks):<4} | {mean(parts):>5.2f} {allp:>4}/{len(verdicts):<4} {errors:>6} {mean(scores):>5.1f} {mean(ranks[level]):>4.2f}")
    print()
    print("per item: last-turn answer tokens (whole-conversation wall s); judge parts covered")
    for i in items:
        row = []
        for level in LEVELS:
            if (i, level) in cells:
                c = cells[(i, level)]
                v = single[level].get(i, {})
                p = v.get("parts") or []
                row.append(f"{level} {c[-1]['final_tok']:>4} ({sum(r['wall_s'] for r in c):>4.0f}s) {sum(map(bool, p))}/{len(p)}")
        print(f"  {i:12} " + "  ".join(row))


if __name__ == "__main__":
    main()
