"""Cave-mode benchmark: `mling exec` over tasks x levels x repetitions, one run at a time.

    python run.py --reps 1,2,3 --levels off,lite,full,ultra [--tasks all] [--out results/new.jsonl]

Each run gets a fresh CODEX_HOME and a fresh git workspace copied from tasks/<task>/ws. A level
other than `off` is passed as `developer_instructions` from levels/<level>.txt (or, for an older or
candidate text such as `ultra_v5`, levels/history/<level>.txt), which is the text the cave_mode
World State fragment would add. `mling`'s own cave mode is switched off for every run
(DREAMFERENCE_MIGHTLING_CAVE_MODE=off), so the level under test is the only one the model sees. The rollout is split into final answer, commentary
between tool calls and tool-call arguments, counted with the served model's tokenizer, and the
task's check.py decides pass or fail. Results are appended as JSON lines; finished cells are
skipped, so an interrupted run resumes.
"""
import argparse
import glob
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tok import n

HERE = Path(__file__).resolve().parent
TASKS = sorted(p.name for p in (HERE / "tasks").iterdir() if p.is_dir())
QUESTION_TASKS = {"howto", "debug", "safety", "openq", "repoq"}


def text_of(item):
    return "".join(c.get("text", "") for c in item.get("content", []))


def style(text):
    prose = re.sub(r"```.*?```", " ", text, flags=re.S)
    prose = re.sub(r"`[^`]*`", " X ", prose)
    words = re.findall(r"[A-Za-z']+", prose)
    sentences = [s for s in re.split(r"[.!?]\s+|\n+", prose) if re.search(r"[A-Za-z]", s)]
    return {
        "words": len(words),
        "article_rate": round(100 * sum(w.lower() in ("a", "an", "the") for w in words) / max(1, len(words)), 1),
        "words_per_sentence": round(len(words) / max(1, len(sentences)), 1),
        "code_blocks": text.count("```") // 2,
        "closing_offer": bool(re.search(
            r"(let me know|want me to|shall i|if you want|if you'd like|feel free|happy to help|hope this helps)",
            text, re.I)),
        "menu": bool(re.search(r"(option \d|alternatively|another approach|approach \d)", text, re.I)),
        "lines": len([line for line in text.splitlines() if line.strip()]),
    }


def git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)


def workspace(task, root):
    ws = root / "ws"
    shutil.copytree(HERE / "tasks" / task / "ws", ws)
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "-c", "user.email=bench@localhost", "-c", "user.name=bench", "commit", "-qm", "init")
    setup = HERE / "tasks" / task / "setup.sh"
    if setup.exists():
        env = {**os.environ, "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@localhost",
               "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@localhost"}
        subprocess.run(["bash", str(setup)], cwd=ws, check=True, capture_output=True, env=env)
    return ws


def level_file(level, suffix=".txt"):
    """levels/<level><suffix>, or the same name under levels/history for an older or candidate text."""
    current = HERE / "levels" / f"{level}{suffix}"
    return current if current.exists() else HERE / "levels" / "history" / f"{level}{suffix}"


def exec_args(ws, level):
    args = ["-s", "workspace-write", "-C", str(ws), "--skip-git-repo-check"]
    if level != "off":
        args += ["-c", "developer_instructions=" + json.dumps(level_file(level).read_text())]
    return args


def bench_env(home):
    # Since 2026-10-01 `mling` adds its own cave_mode section (ultra by default); without this the
    # model would get the shipped level on top of the one under test, and `off` would not be off.
    return {**os.environ, "CODEX_HOME": str(home), "DREAMFERENCE_MIGHTLING_CAVE_MODE": "off"}


def dirty(ws):
    """{path: content hash} of every file that differs from the workspace's commit."""
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ws,
                            capture_output=True, text=True).stdout
    paths = [line[3:] for line in status.splitlines() if line.strip()]
    found = {}
    for path in paths:
        if "__pycache__" in path or ".pytest_cache" in path:
            continue
        target = Path(ws) / path
        found[path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "gone"
    return found


def wrote_files(ws, baseline):
    """Files the run created or changed, not counting what the task's setup left uncommitted."""
    return sorted(path for path, digest in dirty(ws).items() if baseline.get(path) != digest)


def measure(rollout):
    records = [json.loads(line) for line in open(rollout)]
    items = [r["payload"] for r in records if r.get("type") == "response_item"]
    usage = [r["payload"]["info"] for r in records if r.get("type") == "event_msg"
             and r["payload"].get("type") == "token_count" and r["payload"].get("info")]
    total = usage[-1]["total_token_usage"] if usage else {}
    final, commentary, calls, call_tokens = "", [], 0, 0
    for i, item in enumerate(items):
        if item.get("type") == "message" and item.get("role") == "assistant":
            later = [r for r in items[i + 1:] if r.get("type") in ("function_call", "message")]
            if later:
                commentary.append(text_of(item))
            else:
                final = text_of(item)
        elif item.get("type") in ("function_call", "custom_tool_call"):
            calls += 1
            call_tokens += n(item.get("arguments") or item.get("input") or "")
    return final, commentary, {
        "out_tok": total.get("output_tokens"), "in_tok": total.get("input_tokens"),
        "cached": total.get("cached_input_tokens"), "final_tok": n(final),
        "commentary_tok": sum(n(c) for c in commentary), "commentary_n": len(commentary),
        "calls": calls, "call_tok": call_tokens, **style(final),
    }


def run_one(task, level, rep, runs, out):
    root = runs / f"{task}-{level}-{rep}"
    shutil.rmtree(root, ignore_errors=True)
    home = root / "home"
    home.mkdir(parents=True)
    ws = workspace(task, root)
    baseline = dirty(ws)  # `safety` starts with uncommitted work on purpose
    prompt = (HERE / "tasks" / task / "prompt.txt").read_text().strip()
    start = time.time()
    try:
        rc = subprocess.run(["mling", "exec", *exec_args(ws, level), prompt],
                            env=bench_env(home), stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=900).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    record = {"task": task, "variant": level, "rep": rep, "rc": rc, "wall_s": round(time.time() - start, 1)}
    rollouts = glob.glob(str(home / "sessions/**/*.jsonl"), recursive=True)
    final = ""
    if rollouts:
        final, commentary, metrics = measure(rollouts[0])
        record.update(metrics)
        (root / "commentary.txt").write_text("\n---\n".join(commentary))
    (root / "final.txt").write_text(final)
    check = subprocess.run([sys.executable, str(HERE / "tasks" / task / "check.py"), str(ws), str(root / "final.txt")],
                           capture_output=True)
    record["pass"] = check.returncode == 0
    # A question is answered in the message. An answer moved into a file the user did not ask for
    # passes the fact check and is still a miss (spec §9, "content displaced"): `pass_strict`.
    record["wrote_files"] = wrote_files(ws, baseline)
    record["pass_strict"] = record["pass"] and not (task in QUESTION_TASKS and record["wrote_files"])
    with open(out, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", required=True, help="comma-separated repetition numbers, e.g. 1,2,3")
    parser.add_argument("--levels", default="off,lite,full,ultra")
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--out", default=str(HERE / "results" / "new.jsonl"))
    parser.add_argument("--runs", default=os.environ.get("CAVE_RUNS", "/tmp/cave_mode_bench_runs"),
                        help="where workspaces and sessions are kept (large; not committed)")
    args = parser.parse_args()
    tasks = TASKS if args.tasks == "all" else args.tasks.split(",")
    levels = args.levels.split(",")
    out, runs = Path(args.out), Path(args.runs)
    done = set()
    if out.exists():
        for line in open(out):
            r = json.loads(line)
            done.add((r["task"], r["variant"], r["rep"]))
    for rep in (int(x) for x in args.reps.split(",")):
        cells = [(t, v) for t in tasks for v in levels if (t, v, rep) not in done]
        random.Random(rep).shuffle(cells)  # interleave levels, so server load drifts evenly across them
        for task, level in cells:
            run_one(task, level, rep, runs, out)


if __name__ == "__main__":
    main()
