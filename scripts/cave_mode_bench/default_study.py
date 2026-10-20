"""Which cave level should be the default? The shipped levels through the shipped path, on the kinds
of questions people send from a phone or the app, and a few terminal questions.

    python default_study.py --levels off,lite,full,ultra [--out results/default-study.jsonl]

Unlike run.py, the level is not passed as developer_instructions: each run sets
DREAMFERENCE_MIGHTLING_CAVE_MODE=<level>, so the model sees exactly what `ling` sends (the full text
before the first turn, the one-line reminder before each later one). Ask items run with the `ask`
prompt (DREAMFERENCE_MIGHTLING_PROMPT=ask) in an empty scratch folder, as an Ask thread in the app,
`ling web` or a phone bridge does; terminal items run with the default prompt in run.py's
workspaces and keep their checks. One run at a time, items and levels shuffled together, a fresh
CODEX_HOME per run. Unattended: MIGHTLING_NIGHT_RUN=1 and the model server named in
DREAMFERENCE_VLLM_HOST (no browse). Finished cells are skipped, so an interrupted run resumes.
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from run import HERE, dirty, style, text_of, workspace, wrote_files
from tok import n

# (id, kind, surface, turns, parts the answer must cover). Written for this study; none is a real
# user's message.
ITEMS = [
    ("label", "product text", "ask", [
        "The box of a humidifier I bought says: \"Ultrasonic cool mist, 4 L tank, timer 1-24 h, "
        "sleep mode 26 dB, auto shut-off when empty, do not add essential oils to the tank.\" Can I "
        "run it all night next to my bed, and can I put lavender oil in it?"],
     ["all night by the bed", "lavender oil"]),
    ("battery", "screenshot text", "ask", [
        "My phone's settings screen says: \"Battery Health: Service. Maximum Capacity 79%. Your "
        "battery's health is significantly degraded.\" What does that mean, and do I need to "
        "replace it right now?"],
     ["what it means", "replace now or not"]),
    ("router", "screenshot text", "ask", [
        "The router app shows: \"Internet: connected. 2.4 GHz: channel 6, interference high. "
        "5 GHz: channel 100 (DFS). Firmware: update available.\" Is anything wrong, and what should "
        "I change?"],
     ["anything wrong", "what to change"]),
    ("song-hotel", "song meaning", "ask", [
        "What is the song Hotel California by the Eagles actually about?"],
     ["the meaning"]),
    ("song-vincent", "song meaning", "ask", [
        "What is Don McLean's song Vincent about, and who is it addressed to?"],
     ["what it is about", "who it is addressed to"]),
    ("python", "lookup needing search", "ask", [
        "What is the latest stable release of Python, and when did it come out?"],
     ["version", "release date"]),
    ("clocks", "lookup needing search", "ask", [
        "When do the clocks go back this autumn in the US and in the EU, and is it the same day?"],
     ["US date", "EU date", "same day or not"]),
    ("vacuum", "multi-part comparison", "ask", [
        "Robot vacuums for a flat with two cats: Roborock, Roomba or Eufy. Which is quieter, which "
        "picks up cat hair better, and which is more reliable running every day?"],
     ["quieter", "cat hair", "reliable every day"]),
    ("homeserver", "multi-part comparison", "ask", [
        "For a small home server that runs 24/7: a Raspberry Pi 5, an Intel N100 mini PC, or an old "
        "laptop? Which uses the least power, which is fastest, and which is most reliable?"],
     ["least power", "fastest", "most reliable"]),
    ("heatpump", "two-part explanation", "ask", [
        "What is the difference between a heat pump and an air conditioner, and is a heat pump worth "
        "it where winters reach -20 C?"],
     ["difference", "worth it at -20 C"]),
    ("descale", "everyday how-to", "ask", [
        "How do I descale a Nespresso machine without the official descaling kit?"],
     ["the steps"]),
    ("charging", "everyday advice", "ask", [
        "Is it bad to leave my phone charging overnight every night?"],
     ["bad or not"]),
    ("slow", "why-was-it-slow meta question", "ask", [
        "What is the latest stable Linux kernel version?",
        "Why did that answer take so long?"],
     ["kernel version", "why it was slow"]),
    ("dishwasher", "multi-part comparison, follow-up", "ask", [
        "Bosch or Miele dishwasher: which is quieter, which cleans better, and which lasts longer?",
        "Which one would you buy for a small flat, and why?"],
     ["quieter", "cleans better", "lasts longer", "which to buy and why"]),
    ("song-queen", "song meaning, follow-up for detail", "ask", [
        "What is Bohemian Rhapsody about?",
        "Explain the opera section in more detail."],
     ["what it is about", "the opera section in detail"]),
    ("repoq", "code question (terminal)", "tui", None, ["the variable", "the default port"]),
    ("debug", "traceback (terminal)", "tui", None, ["why it fails", "the fix in code"]),
    ("howto", "how-to (terminal)", "tui", None, ["frozen=True", "hashable"]),
]


def environment(home, level, surface, condition):
    env = {**os.environ, "CODEX_HOME": str(home), "DREAMFERENCE_MIGHTLING_CAVE_MODE": level,
           "MIGHTLING_NIGHT_RUN": "1",
           "DREAMFERENCE_VLLM_HOST": os.environ.get("STUDY_VLLM_HOST", "http://127.0.0.1:8000")}
    if surface == "ask":
        env["DREAMFERENCE_MIGHTLING_PROMPT"] = "ask"
    else:
        env.pop("DREAMFERENCE_MIGHTLING_PROMPT", None)
    # `no-network`: /airgapped on, so the model answers from what it knows and is told it cannot
    # search. Forced on 2026-10-20 when every engine behind SearXNG began refusing requests.
    env["DREAMFERENCE_MIGHTLING_AIRGAPPED"] = "on" if condition == "no-network" else "off"
    return env


def records(rollout):
    return [json.loads(line) for line in open(rollout)]


def turn_metrics(rollout, items_before, out_before):
    rs = records(rollout)
    items = [r["payload"] for r in rs if r.get("type") == "response_item"]
    usage = [r["payload"]["info"]["total_token_usage"] for r in rs if r.get("type") == "event_msg"
             and r["payload"].get("type") == "token_count" and r["payload"].get("info")]
    out_total = usage[-1].get("output_tokens", 0) if usage else 0
    new = items[items_before:]
    answers = [text_of(i) for i in new if i.get("type") == "message" and i.get("role") == "assistant"]
    calls = [i for i in new if i.get("type") in ("function_call", "custom_tool_call")]
    commands = []
    for call in calls:
        raw = call.get("arguments") or call.get("input") or ""
        commands.append(raw[:160])
    developer = [text_of(i) for i in new if i.get("type") == "message" and i.get("role") == "developer"]
    final = answers[-1] if answers else ""
    meta = next((r["payload"] for r in rs if r.get("type") == "session_meta"), {})
    base = json.dumps(meta)[:200000]
    return final, len(items), out_total, {
        "final_tok": n(final), "out_tok": out_total - out_before, "calls": len(calls),
        "commands": commands, "cave_fragments": [m for d in developer for m in re.findall(r"Cave mode (?:is \w+|\w+ still applies)", d)],
        "ask_prompt": "research assistant" in base, **style(final),
    }


def run_cell(item, level, runs, out, condition):
    item_id, kind, surface, turns, parts = item
    root = runs / f"{item_id}-{level}"
    shutil.rmtree(root, ignore_errors=True)
    home = root / "home"
    home.mkdir(parents=True)
    if surface == "ask":
        cwd = root / "scratch"
        cwd.mkdir()
        baseline = {}
        turns = list(turns)
    else:
        cwd = workspace(item_id, root)
        baseline = dirty(cwd)
        turns = [(HERE / "tasks" / item_id / "prompt.txt").read_text().strip()]
    env = environment(home, level, surface, condition)
    items_before, out_before = 0, 0
    for number, prompt in enumerate(turns, start=1):
        tail = [prompt] if number == 1 else ["resume", "--last", prompt]
        start = time.time()
        try:
            rc = subprocess.run(["ling", "exec", "-s", "workspace-write", "-C", str(cwd), "--skip-git-repo-check", *tail],
                                cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=600).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        wall = round(time.time() - start, 1)
        record = {"item": item_id, "condition": condition, "kind": kind, "surface": surface, "level": level, "turn": number,
                  "turns": len(turns), "rc": rc, "wall_s": wall, "parts": parts if number == len(turns) else None}
        rollouts = sorted(glob.glob(str(home / "sessions/**/*.jsonl"), recursive=True))
        final = ""
        if rollouts:
            final, items_before, out_now, metrics = turn_metrics(rollouts[0], items_before, out_before)
            out_before = out_now
            record.update(metrics)
        (root / f"turn{number}.txt").write_text(final)
        if surface == "ask":
            record["wrote_files"] = sorted(str(p.relative_to(cwd)) for p in cwd.rglob("*") if p.is_file())
        else:
            record["wrote_files"] = wrote_files(cwd, baseline)
            final_file = root / "final.txt"
            final_file.write_text(final)
            check = subprocess.run([sys.executable, str(HERE / "tasks" / item_id / "check.py"), str(cwd), str(final_file)],
                                   capture_output=True)
            record["check_pass"] = check.returncode == 0
        record["answer"] = final
        with open(out, "a") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps({k: v for k, v in record.items() if k not in ("answer", "commands")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--levels", default="off,lite,full,ultra")
    parser.add_argument("--items", default="all")
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--condition", choices=("search", "no-network"), default="search")
    parser.add_argument("--out", default=str(HERE / "results" / "default-study.jsonl"))
    parser.add_argument("--runs", default=os.environ.get("CAVE_RUNS", "/tmp/cave_mode_bench_runs/default-study"))
    args = parser.parse_args()
    out, runs = Path(args.out), Path(args.runs)
    runs.mkdir(parents=True, exist_ok=True)
    chosen = [i for i in ITEMS if args.items == "all" or i[0] in args.items.split(",")]
    done = set()
    if out.exists():
        for line in open(out):
            r = json.loads(line)
            if r["turn"] == r["turns"]:
                done.add((r["item"], r["level"]))
    cells = [(i, lv) for i in chosen for lv in args.levels.split(",") if (i[0], lv) not in done]
    random.Random(args.seed).shuffle(cells)
    for item, level in cells:
        run_cell(item, level, runs, out, args.condition)


if __name__ == "__main__":
    main()
