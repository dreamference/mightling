#!/usr/bin/env python3
"""The chooser test on disk: would a best-of-N chooser pick a resolving patch?

Backlog item 1 (``specs/DREAMFERENCE_BACKLOG.md``), from the Together AI survey
(``specs/research/together-ai-survey-2026-10-13.md`` §3.1, next step 2). The
default arm ran on the same 44 tasks in nights 2, 3 and 4: one run resolves
29-31, *some* run resolves 36. Best-of-N is worth building only if a chooser,
shown the runs' patches and nothing else, picks a resolving one where the runs
disagree. This script measures that on the patches already on disk, with no
agent run and no container: only the model, judging.

What it does:

1. Reads each run's ``manifest.json``, ``predictions.jsonl`` and the latest
   plain grading (``eval/<n>/grading.json``) under ``--runs-dir``, read only,
   and the issue texts from the local dataset snapshot.
2. Keeps the tasks graded in every run where the runs disagree (some resolved,
   not all): about 13 for nights 2-4. ``--all`` keeps every task.
3. For each task, the distinct non-empty patches are the candidates. Every pair
   is judged twice, in both orders (the model's position bias cancels), by the
   model at ``--server``: the issue, patch A, patch B, and "which one fixes the
   issue without breaking anything". The judge never sees a verdict.
4. A win is a point, an unreadable answer half a point to each. The chosen
   patch is the one with the most points; a tie at the top is scored as the
   share of the tied patches that resolve (a fair coin among them).
5. Reports, over the kept tasks: the chooser's resolved count against a
   random pick of a run (the single-run expectation) and the oracle (any run),
   and the projected best-of-N on the whole list (the tasks every run resolved,
   plus the chooser's share of the rest).

Every judgment is appended to ``<out>/judgments.jsonl`` as it is made, so an
interrupted test resumes where it stopped; ``<out>/summary.json`` is written at
the end. Nothing is written anywhere else.

Runbook (about an hour of model time; NOT for a night with a run in progress)
------------------------------------------------------------------------------

The test runs on this machine (the runs are here) against second-puffin's
production server, at a time when nothing else needs that server.

1. Dry run here, no server contacted::

       .venv/bin/python scripts/swe_bench_chooser_test.py --dry-run

   It lists the kept tasks, their candidates and the number of model calls
   (two per pair; about 70-80 for nights 2-4).
2. Check the server is free. No SWE-bench run, Night Shift task or ling-engine
   test may be using second-puffin's model server: a run's model gate refuses
   outside requests (the script then stops with the refusal), and a shared
   server would also slow the run. ``ssh second-puffin ling-admin night status``
   and ``ling-admin swe-bench status`` there; ``ling-admin night pause`` only
   if the owner agreed to share it.
3. Name the server by its node name, never an address (the node's mDNS name;
   ``ling node list`` shows it), and check it answers::

       curl -s http://second-puffin.local:8000/v1/models   # or ling-fetch

4. Run::

       .venv/bin/python scripts/swe_bench_chooser_test.py \\
           --server http://second-puffin.local:8000 \\
           --runs n2-default,n3-default,n4-default \\
           --out ~/swe-bench-chooser-2026-10

   The model id comes from the server's ``/v1/models``. The defaults are
   Qwen3.8's recommended sampling (temperature 0.6, top-p 0.95) and room for
   its thinking (``--max-tokens 16384``). Interrupted, run the same command
   again: judged pairs are not asked twice.
5. Read ``summary.json`` (the printed summary is the same). The decision rule
   (survey §3.1): the chooser is worth building into Night Shift (backlog item
   6) if it resolves clearly more than the random pick on the kept tasks; the
   estimate to beat is +3 to +4 tasks of the ~6.3 headroom.

Tests: ``tests/test_swe_bench_chooser.py`` (a fake server on loopback).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

DEFAULT_RUNS_DIR: Final[Path] = Path.home() / ".local" / "share" / "dreamference" / "swe-bench" / "runs"
DEFAULT_DATASET: Final[Path] = Path.home() / ".cache" / "dreamference" / "swe-bench" / "datasets" / "SWE-bench_Verified.jsonl"
DEFAULT_RUNS: Final[str] = "n2-default,n3-default,n4-default"
# A patch longer than this is cut, with a note, so two patches and the issue fit the context.
MAX_PATCH_CHARS: Final[int] = 40_000
MAX_ISSUE_CHARS: Final[int] = 20_000
WINNER: Final[re.Pattern] = re.compile(r"WINNER:\s*\**\s*([AB])\b")

SYSTEM_PROMPT: Final[str] = (
    "You review patches for a Python repository. You are shown a GitHub issue and two candidate "
    "patches written for it. Decide which patch is more likely to be accepted: it fixes the "
    "behaviour the issue describes, in the place the maintainers would fix it, and breaks no "
    "existing behaviour or test. Ignore style, comments and test files the patch adds. Neither "
    "patch has been run; judge by reading."
)

USER_TEMPLATE: Final[str] = """<issue>
{issue}
</issue>

<patch_A>
{a}
</patch_A>

<patch_B>
{b}
</patch_B>

Compare the two patches against the issue. Think about what each changes and whether it fixes the reported behaviour without breaking anything else. End your answer with one line, exactly `WINNER: A` or `WINNER: B`."""


# --------------------------------------------------------------------------- the runs on disk


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Reads a JSON object; None when the file is missing or not an object."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def load_run(runs_dir: Path, name: str) -> Dict[str, Any]:
    """
    Reads one run, read only.

    Args:
        runs_dir: The directory holding the runs.
        name: The run's name.

    Returns:
        Dict[str, Any]: `name`, `instances` (the manifest's list), `patches` (instance to its
        last prediction's patch) and `resolved` (instance to the latest plain grading's verdict;
        graded instances only).

    Raises:
        SystemExit: When the run, or a grading of it, is missing.
    """
    directory = runs_dir / name
    manifest = read_json(directory / "manifest.json")
    if manifest is None:
        raise SystemExit(f"❌ No run {name} under {runs_dir}")
    patches: Dict[str, str] = {}
    try:
        lines = (directory / "predictions.jsonl").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a torn last line
        if isinstance(record, dict) and record.get("instance_id"):
            patches[record["instance_id"]] = str(record.get("model_patch") or "")
    numbers = sorted(int(entry.name) for entry in (directory / "eval").glob("*") if entry.name.isdigit())
    if not numbers:
        raise SystemExit(f"❌ {name} has no grading: run `ling-admin swe-bench eval {name}` first")
    grading = read_json(directory / "eval" / str(numbers[-1]) / "grading.json") or {}
    resolved = {i: bool(r.get("resolved")) for i, r in (grading.get("results") or {}).items()}
    return {"name": name, "instances": list(manifest.get("instances") or []), "patches": patches,
            "resolved": resolved}


def load_issues(dataset: Path, ids: Sequence[str]) -> Dict[str, str]:
    """
    Reads the issue texts of `ids` from the local dataset snapshot (one JSON row per line).

    Returns:
        Dict[str, str]: Instance to its `problem_statement`.

    Raises:
        SystemExit: When an instance is not in the snapshot.
    """
    wanted, issues = set(ids), {}
    try:
        lines = dataset.read_text().splitlines()
    except OSError:
        raise SystemExit(f"❌ No dataset snapshot at {dataset} (ling-admin swe-bench setup writes it)")
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("instance_id") in wanted:
            issues[row["instance_id"]] = str(row.get("problem_statement") or "")
    missing = sorted(wanted - set(issues))
    if missing:
        raise SystemExit(f"❌ Not in {dataset}: {', '.join(missing)}")
    return issues


def digest(patch: str) -> str:
    """A short name for a patch's text (trailing whitespace ignored)."""
    return hashlib.sha256(patch.strip().encode()).hexdigest()[:12]


def select_tasks(runs: List[Dict[str, Any]], every: bool = False) -> List[Dict[str, Any]]:
    """
    The tasks to judge, with their candidates.

    Args:
        runs: The loaded runs.
        every: Keep every task graded in all runs, not only those where they disagree.

    Returns:
        List[Dict[str, Any]]: Per task: `instance_id`, `verdicts` (run to resolved), and
        `candidates` (the distinct non-empty patches: `id`, `patch`, `runs`, `resolved`, and
        `flaky` when the same text was graded both ways), sorted by instance id.

    Raises:
        SystemExit: When the runs cover different task lists.
    """
    lists = {tuple(sorted(run["instances"])) for run in runs}
    if len(lists) != 1:
        raise SystemExit("❌ The runs cover different task lists; a chooser over them would compare nothing")
    tasks = []
    for instance_id in sorted(lists.pop()):
        if not all(instance_id in run["resolved"] for run in runs):
            continue
        verdicts = {run["name"]: run["resolved"][instance_id] for run in runs}
        if not every and len(set(verdicts.values())) != 2:
            continue
        candidates: Dict[str, Dict[str, Any]] = {}
        for run in runs:
            patch = run["patches"].get(instance_id, "")
            if not patch.strip():
                continue  # an empty patch is never chosen
            entry = candidates.setdefault(digest(patch), {"id": digest(patch), "patch": patch, "runs": [],
                                                          "verdicts": []})
            entry["runs"].append(run["name"])
            entry["verdicts"].append(verdicts[run["name"]])
        for entry in candidates.values():
            entry["resolved"] = all(entry["verdicts"])
            entry["flaky"] = len(set(entry.pop("verdicts"))) > 1
        tasks.append({"instance_id": instance_id, "verdicts": verdicts,
                      "candidates": sorted(candidates.values(), key=lambda c: c["runs"])})
    return tasks


# --------------------------------------------------------------------------- the model server


class Client:
    """The model server's OpenAI-compatible API, over plain HTTP."""

    def __init__(self, server: str, timeout: float = 900.0) -> None:
        """
        Args:
            server: The server's base URL (`http://<node>.local:8000`), with or without `/v1`.
            timeout: Seconds to wait for one answer.
        """
        self.base = server.rstrip("/").removesuffix("/v1") + "/v1"
        self.timeout = timeout

    def request(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        One request; a POST when there is a payload.

        Raises:
            SystemExit: When the server refuses or cannot be reached (a model gate in force
            answers with a refusal; the test must not go on against it).
        """
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")[:400]
            raise SystemExit(f"❌ {self.base}{path} answered {error.code}: {body}")
        except (urllib.error.URLError, OSError) as error:
            raise SystemExit(f"❌ {self.base}{path} could not be reached: {error}")

    def model(self) -> str:
        """The served model's id, as the server names it."""
        models = self.request("/models").get("data") or []
        if not models:
            raise SystemExit(f"❌ {self.base}/models lists no model")
        return str(models[0]["id"])

    def chat(self, payload: Dict[str, Any]) -> str:
        """The answer's text: its content, or the reasoning when the content is empty."""
        choice = (self.request("/chat/completions", payload).get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return str(message.get("content") or message.get("reasoning_content") or "")


def clip(text: str, limit: int) -> str:
    """Cuts a text at `limit` characters, saying so."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... cut: {len(text) - limit} more characters]"


def winner(answer: str) -> Optional[str]:
    """The last `WINNER: A|B` line of an answer; None when there is none."""
    found = WINNER.findall(answer)
    return found[-1] if found else None


def judge(task: Dict[str, Any], issue: str, ask: Callable[[str, str, str, str], Optional[str]],
          done: Dict[Tuple[str, str, str], Optional[str]]) -> Dict[str, Any]:
    """
    Judges one task's candidates pairwise, both orders, and picks one.

    Args:
        task: One of `select_tasks`'s tasks.
        issue: Its issue text.
        ask: `(instance, issue, patch A, patch B) -> "A" | "B" | None`, one model call.
        done: Judgments already made, by (instance, A's id, B's id); added to.

    Returns:
        Dict[str, Any]: `points` (candidate to points), `chosen` (the top ids), `credit` (the
        share of the top that resolves), `calls` (model calls made now).
    """
    candidates = {c["id"]: c for c in task["candidates"]}
    points = {cid: 0.0 for cid in candidates}
    calls = 0
    for first, second in itertools.permutations(sorted(candidates), 2):
        key = (task["instance_id"], first, second)
        if key not in done:
            done[key] = ask(task["instance_id"], issue, candidates[first]["patch"], candidates[second]["patch"])
            calls += 1
        verdict = done[key]
        if verdict == "A":
            points[first] += 1
        elif verdict == "B":
            points[second] += 1
        else:
            points[first] += 0.5
            points[second] += 0.5
    if not points:
        return {"points": {}, "chosen": [], "credit": 0.0, "calls": calls}
    top = max(points.values())
    chosen = sorted(cid for cid, p in points.items() if p == top)
    credit = sum(1 for cid in chosen if candidates[cid]["resolved"]) / len(chosen)
    return {"points": points, "chosen": chosen, "credit": credit, "calls": calls}


def summarise(runs: List[Dict[str, Any]], tasks: List[Dict[str, Any]], outcomes: Dict[str, Dict[str, Any]],
              every: bool) -> Dict[str, Any]:
    """
    The test's figures.

    Returns:
        Dict[str, Any]: Over the judged tasks: `tasks`, `chooser` (expected resolved),
        `random_run` (the expected resolved of a run picked at random), `oracle`; over the whole
        list: `listed`, `single_runs` (each run's resolved), `all_resolved` (tasks every run
        resolved), `projected` (best-of-N with this chooser).
    """
    names = [run["name"] for run in runs]
    listed = sorted(set(runs[0]["instances"]))
    graded_everywhere = [i for i in listed if all(i in run["resolved"] for run in runs)]
    all_resolved = [i for i in graded_everywhere if all(run["resolved"][i] for run in runs)]
    disagree = [t for t in tasks if len(set(t["verdicts"].values())) == 2]
    judged = disagree if not every else tasks
    chooser = sum(outcomes[t["instance_id"]]["credit"] for t in judged)
    random_run = sum(sum(t["verdicts"].values()) / len(names) for t in judged)
    oracle = sum(1 for t in judged if any(t["verdicts"].values()))
    projected = len(all_resolved) + sum(outcomes[t["instance_id"]]["credit"] for t in disagree)
    if every:
        # Over every task the chooser can also lose a task every run resolved (it never does
        # here: every candidate resolves), so the projection is its own total.
        projected = chooser
    return {
        "runs": names, "tasks": len(judged), "chooser": round(chooser, 2), "random_run": round(random_run, 2),
        "oracle": oracle, "listed": len(listed),
        "single_runs": {run["name"]: sum(1 for i in listed if run["resolved"].get(i)) for run in runs},
        "all_resolved": len(all_resolved), "projected": round(projected, 2),
        "flaky": sorted(t["instance_id"] for t in tasks if any(c["flaky"] for c in t["candidates"])),
    }


# --------------------------------------------------------------------------- main


def run_test(args: argparse.Namespace, client_factory: Callable[[str, float], Client] = Client) -> int:
    """The test, as `main` runs it; the client is replaceable for the tests."""
    runs = [load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    if len(runs) < 2:
        raise SystemExit("❌ Name at least two runs")
    tasks = select_tasks(runs, args.all)
    if args.limit:
        tasks = tasks[:args.limit]
    pairs = sum(len(t["candidates"]) * (len(t["candidates"]) - 1) for t in tasks)
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; {pairs} model call(s) at most")
    for task in tasks:
        marks = ", ".join(f"{c['id']} {'resolved' if c['resolved'] else 'unresolved'}"
                          + (" (flaky)" if c["flaky"] else "") + f" [{'+'.join(c['runs'])}]"
                          for c in task["candidates"])
        print(f"  {task['instance_id']:<34} {marks or 'no non-empty patch'}")
    if args.dry_run:
        return 0
    if not args.server:
        raise SystemExit("❌ --server is required (the model server's URL, by node name)")

    issues = load_issues(args.dataset, [t["instance_id"] for t in tasks])
    client = client_factory(args.server, args.timeout)
    model = args.model or client.model()
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "judgments.jsonl"
    done: Dict[Tuple[str, str, str], Optional[str]] = {}
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            done[(record["instance_id"], record["a"], record["b"])] = record["winner"]

    def ask(instance_id: str, issue: str, a: str, b: str) -> Optional[str]:
        payload = {"model": model, "max_tokens": args.max_tokens, "temperature": args.temperature,
                   "top_p": args.top_p,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": USER_TEMPLATE.format(
                                    issue=clip(issue, MAX_ISSUE_CHARS), a=clip(a, MAX_PATCH_CHARS),
                                    b=clip(b, MAX_PATCH_CHARS))}]}
        if args.seed is not None:
            payload["seed"] = args.seed
        answer = client.chat(payload)
        verdict = winner(answer)
        with open(log, "a") as handle:
            handle.write(json.dumps({"instance_id": instance_id, "a": digest(a), "b": digest(b), "winner": verdict,
                                     "model": model, "answer_tail": answer[-600:]}) + "\n")
        return verdict

    outcomes = {}
    for task in tasks:
        outcomes[task["instance_id"]] = judge(task, issues[task["instance_id"]], ask, done)
        outcome = outcomes[task["instance_id"]]
        print(f"  {task['instance_id']:<34} chose {', '.join(outcome['chosen']) or 'nothing'}: "
              f"credit {outcome['credit']:.2f} ({outcome['calls']} new call(s))")
    summary = summarise(runs, tasks, outcomes, args.all)
    summary.update({"model": model, "server": args.server, "per_task": {
        i: {"chosen": o["chosen"], "credit": o["credit"], "points": o["points"]} for i, o in outcomes.items()}})
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nOn the {summary['tasks']} judged task(s): chooser {summary['chooser']}, a random run "
          f"{summary['random_run']}, any run (oracle) {summary['oracle']}")
    print(f"On all {summary['listed']}: single runs {summary['single_runs']}; best-of-{len(runs)} with this chooser "
          f"{summary['projected']} (every run resolved {summary['all_resolved']})")
    if summary["flaky"]:
        print(f"Graded both ways for the same patch text (flaky tests): {', '.join(summary['flaky'])}")
    return 0


def parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", help="the model server's URL, by node name (http://second-puffin.local:8000)")
    p.add_argument("--runs", default=DEFAULT_RUNS, help=f"comma-separated run names (default {DEFAULT_RUNS})")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help="where the runs are (read only)")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="the local dataset snapshot")
    p.add_argument("--out", type=Path, default=Path("swe-bench-chooser"), help="where judgments and the summary go")
    p.add_argument("--model", help="the model id (default: the server's /v1/models)")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--temperature", type=float, default=0.6)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--seed", type=int, help="a sampling seed, if the server takes one")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for one answer")
    p.add_argument("--all", action="store_true", help="judge every task, not only those where the runs disagree")
    p.add_argument("--limit", type=int, help="judge only the first N tasks")
    p.add_argument("--dry-run", action="store_true", help="list the tasks and candidates; contact no server")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the test."""
    return run_test(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
