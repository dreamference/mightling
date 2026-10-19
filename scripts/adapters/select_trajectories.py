#!/usr/bin/env python3
"""Rejection sampling over `ling-admin swe-bench` runs, and the task-overlap guard.

`select` reads run directories (`~/.local/share/dreamference/swe-bench/runs/<name>/`, read only)
and keeps every attempt the grader resolved: `eval/<n>/grading.json` (the latest plain series,
never a `drop-test-hunks` regrade) has `resolved: true`, its fail-to-pass tests ran and its
pass-to-pass tests still pass. For each kept attempt it writes one manifest line: the run, the
instance, the session ids and rollout files under `scratch/<id>/codex-home/sessions/`, the
recorded compactions, the final context length and the patch size. `--per-task K` keeps at most K
attempts per task (the shortest first), so an easy task cannot fill the set.

`overlap` refuses a task file (SWE-bench-format JSONL rows with `instance_id` and `repo`) that
shares an instance or a repository with the lists our nights score on (`~/.cache/dreamference/
swe-bench/*.txt` and the Verified snapshot there) or with a held-out evaluation set (the Rust
subset of SWE-bench Multilingual), and with `--write` writes the rows that are left.

Standard library only; nothing runs at import.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

RUNS_DIR: str = "~/.local/share/dreamference/swe-bench/runs"
LISTS_DIR: str = "~/.cache/dreamference/swe-bench"


# -- rejection sampling ---------------------------------------------------------------------------

def latest_grading(run: Path) -> Dict[str, Any]:
    """The newest plain grading series of a run (`eval/<n>/grading.json`), or {}."""
    series = sorted((p for p in (run / "eval").glob("*/grading.json") if p.parent.name.isdigit()),
                    key=lambda p: int(p.parent.name))
    return json.loads(series[-1].read_text()).get("results", {}) if series else {}


def rollout_stats(path: Path) -> Dict[str, Any]:
    """Requests, compactions and the last request's context length of one rollout file."""
    requests = compactions = last_tokens = 0
    with open(path) as handle:
        for line in handle:
            if '"token_usage_record"' in line:
                usage = json.loads(line)["payload"]["usage"]
                requests += 1
                last_tokens = usage["input_tokens"] + usage["output_tokens"]
            elif '"compacted"' in line and json.loads(line).get("type") == "compacted":
                compactions += 1
    return {"requests": requests, "compactions": compactions, "last_tokens": last_tokens}


def accepted_attempts(run: Path) -> Iterable[Dict[str, Any]]:
    """Every resolved attempt of one run, with its rollouts."""
    results = latest_grading(run)
    manifest = json.loads((run / "manifest.json").read_text()) if (run / "manifest.json").exists() else {}
    for instance_id, verdict in sorted(results.items()):
        if not verdict.get("resolved") or verdict.get("test_patch_failed"):
            continue
        if verdict.get("f2p", 0) < 1 or verdict.get("p2p_failed", 0) or verdict.get("f2p_failed", 0):
            continue
        state_path = run / "instances" / f"{instance_id}.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        rollouts = sorted((run / "scratch" / instance_id / "codex-home" / "sessions").glob("*/*/*/rollout-*.jsonl"))
        if not rollouts:
            continue
        stats = [rollout_stats(p) for p in rollouts]
        yield {
            "run": run.name,
            "instance_id": instance_id,
            "model": manifest.get("model") or manifest.get("served_model"),
            "dataset": manifest.get("dataset"),
            "rollouts": [str(p.relative_to(run)) for p in rollouts],
            "session": state.get("session"),
            "compactions": sum(s["compactions"] for s in stats),
            "requests": sum(s["requests"] for s in stats),
            "last_tokens": max(s["last_tokens"] for s in stats),
            "patch_bytes": state.get("patch_bytes"),
            "wall_s": state.get("wall_s"),
            "f2p": verdict.get("f2p"),
            "p2p": verdict.get("p2p"),
        }


def select(runs: List[Path], per_task: int, max_tokens: int) -> List[Dict[str, Any]]:
    """Resolved attempts of all runs, at most `per_task` per task, the shortest first."""
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        for attempt in accepted_attempts(run):
            if attempt["last_tokens"] <= max_tokens:
                by_task.setdefault(attempt["instance_id"], []).append(attempt)
    kept: List[Dict[str, Any]] = []
    for instance_id in sorted(by_task):
        attempts = sorted(by_task[instance_id], key=lambda a: (a["last_tokens"], a["run"]))
        kept.extend(attempts[:per_task])
    return kept


# -- the overlap guard ----------------------------------------------------------------------------

def scored_lists(lists_dir: Path) -> Set[str]:
    """Every instance id named by a task list our nights use."""
    ids: Set[str] = set()
    for path in lists_dir.glob("*.txt"):
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line.split()[0])
    return ids


def verified_repos(lists_dir: Path) -> Set[str]:
    """The repositories of the SWE-bench Verified snapshot in the cache."""
    repos: Set[str] = set()
    for path in lists_dir.glob("datasets/*Verified*.jsonl"):
        with open(path) as handle:
            for line in handle:
                repos.add(json.loads(line)["repo"])
    return repos


def read_rows(path: Path) -> List[Dict[str, Any]]:
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def overlap(rows: List[Dict[str, Any]], banned_ids: Set[str], banned_repos: Set[str],
            held_out_ids: Set[str], held_out_repos_commits: Set[str]) -> Dict[str, List[str]]:
    """
    Sorts task rows into kept and refused.

    A row is refused when its instance id is on a night's list or in the held-out set, when its
    repository is a Verified repository, or when it is the held-out set's repository at the same
    base commit (the same task under another id).
    """
    out: Dict[str, List[str]] = {"kept": [], "night_list": [], "verified_repo": [], "held_out": []}
    for row in rows:
        instance_id, repo = row["instance_id"], row.get("repo", "")
        if instance_id in banned_ids:
            out["night_list"].append(instance_id)
        elif repo in banned_repos:
            out["verified_repo"].append(instance_id)
        elif instance_id in held_out_ids or f"{repo}@{row.get('base_commit', '')}" in held_out_repos_commits:
            out["held_out"].append(instance_id)
        else:
            out["kept"].append(instance_id)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sel = sub.add_parser("select", help="keep the resolved attempts of runs")
    sel.add_argument("runs", nargs="+", help="run names or directories (glob patterns allowed)")
    sel.add_argument("--per-task", type=int, default=2)
    sel.add_argument("--max-tokens", type=int, default=65536, help="drop attempts longer than this")
    sel.add_argument("--out", type=Path, help="manifest JSONL (default: stdout summary only)")
    ov = sub.add_parser("overlap", help="refuse tasks that overlap the scored lists or the eval set")
    ov.add_argument("tasks", type=Path, help="SWE-bench-format JSONL of candidate tasks")
    ov.add_argument("--held-out", type=Path, action="append", default=[],
                    help="JSONL of the evaluation set (e.g. SWE-bench Multilingual's Rust rows)")
    ov.add_argument("--lists-dir", default=LISTS_DIR)
    ov.add_argument("--write", type=Path, help="write the kept rows here")
    args = parser.parse_args(argv)

    if args.command == "select":
        base = Path(os.path.expanduser(RUNS_DIR))
        runs: List[Path] = []
        for spec in args.runs:
            pattern = spec if os.sep in spec else str(base / spec)
            runs.extend(Path(p) for p in sorted(glob.glob(os.path.expanduser(pattern))) if Path(p).is_dir())
        kept = select(runs, args.per_task, args.max_tokens)
        tasks = {a["instance_id"] for a in kept}
        tokens = sorted(a["last_tokens"] for a in kept)
        print(f"🚀 {len(runs)} runs, {len(kept)} attempts kept on {len(tasks)} tasks")
        if tokens:
            print(f"  final context: median {tokens[len(tokens) // 2]}, max {tokens[-1]}, "
                  f"sum {sum(tokens) / 1e6:.2f}M tokens; with compaction: {sum(1 for a in kept if a['compactions'])}")
        if args.out:
            args.out.write_text("".join(json.dumps(a) + "\n" for a in kept))
            print(f"✅ wrote {args.out}")
        return 0

    lists_dir = Path(os.path.expanduser(args.lists_dir))
    rows = read_rows(args.tasks)
    held_ids: Set[str] = set()
    held_commits: Set[str] = set()
    for path in args.held_out:
        for row in read_rows(path):
            held_ids.add(row["instance_id"])
            held_commits.add(f"{row['repo']}@{row.get('base_commit', '')}")
    result = overlap(rows, scored_lists(lists_dir), verified_repos(lists_dir), held_ids, held_commits)
    print(f"🚀 {len(rows)} candidate tasks: kept {len(result['kept'])}, refused "
          f"{len(result['night_list'])} on a night's list, {len(result['verified_repo'])} from a Verified "
          f"repository, {len(result['held_out'])} in the held-out evaluation set")
    if args.write:
        keep = set(result["kept"])
        args.write.write_text("".join(json.dumps(r) + "\n" for r in rows if r["instance_id"] in keep))
        print(f"✅ wrote {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
