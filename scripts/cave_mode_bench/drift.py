"""Does a level hold over a long session, and does its way out work? Nine turns through `puffin exec`
and `exec resume --last`: eight questions, then "Give me the full explanation of your last answer."

    python drift.py --levels off,full,ultra,ultra+R --rep 1 [--out results/drift-new.jsonl]

A `+R` suffix adds levels/<level>.reminder.txt before every user message after the first, the way
the cave_mode section's per-turn reminder reaches the model (a <system-reminder> in the user turn).
`+RS` does the same but skips the reminder on a turn whose message asks for more detail
(`ASKS_FOR_MORE`), the second half of the fix proposed in spec §9.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import re

from run import HERE, bench_env, exec_args, level_file, style, text_of, workspace
from tok import n

QUESTIONS = [
    "Read svc/settings.py and tell me what listen_address returns.",
    "How would I add a timeout setting that can also be overridden from the environment?",
    "Go ahead and add it, following the existing pattern, with SVC_TIMEOUT_S as the variable.",
    "Why might int() on an environment variable be a problem here?",
    "What's the difference between os.environ.get and os.getenv?",
    "Write a short test for listen_address that sets SVC_LISTEN_PORT, and run it.",
    "Is there anything else in this small project you would change? Keep it to what matters.",
    "Explain how Python resolves `from svc import defaults` when I run python -m svc.server from the repo root.",
    # The way out every level promises (first asked by hand in rep 4 of 2026-10-01).
    "Give me the full explanation of your last answer.",
]

ASKS_FOR_MORE = re.compile(r"\b(full explanation|in full|more detail|in detail|elaborate|explain more)\b", re.I)


def session(variant, rep, runs, out):
    level = variant.removesuffix("+RS").removesuffix("+R")
    remind, skip = variant.endswith(("+R", "+RS")), variant.endswith("+RS")
    root = runs / f"drift-{variant}-{rep}"
    shutil.rmtree(root, ignore_errors=True)
    home = root / "home"
    home.mkdir(parents=True)
    ws = workspace("repoq", root)
    reminder = level_file(level, ".reminder.txt").read_text().strip() if remind else ""
    for turn, question in enumerate(QUESTIONS, start=1):
        if reminder and turn > 1 and not (skip and ASKS_FOR_MORE.search(question)):
            question = f"<system-reminder>\n{reminder}\n</system-reminder>\n\n{question}"
        tail = [question] if turn == 1 else ["resume", "--last", question]
        start = time.time()
        rc = subprocess.run(["puffin", "exec", *exec_args(ws, level), *tail], cwd=ws,
                            env=bench_env(home), stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=900).returncode
        wall = round(time.time() - start, 1)
        rollout = sorted(glob.glob(str(home / "sessions/**/*.jsonl"), recursive=True))[0]
        items = [json.loads(line)["payload"] for line in open(rollout) if json.loads(line).get("type") == "response_item"]
        answers = [text_of(i) for i in items if i.get("type") == "message" and i.get("role") == "assistant"]
        final = answers[-1] if answers else ""
        record = {"variant": variant, "rep": rep, "turn": turn, "rc": rc, "wall_s": wall, "final_tok": n(final), **style(final)}
        (root / f"turn{turn}.txt").write_text(final)
        with open(out, "a") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--levels", default="off,full,ultra,full+R,ultra+R")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", default=str(HERE / "results" / "drift-new.jsonl"))
    parser.add_argument("--runs", default=os.environ.get("CAVE_RUNS", "/tmp/cave_mode_bench_runs"))
    args = parser.parse_args()
    for variant in args.levels.split(","):
        session(variant, args.rep, Path(args.runs), Path(args.out))


if __name__ == "__main__":
    main()
