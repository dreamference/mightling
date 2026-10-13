#!/usr/bin/env python3
"""The chooser test on disk: would a best-of-N chooser pick a resolving patch?

Backlog item 1 (``specs/DREAMFERENCE_BACKLOG.md``), from the Together AI survey
(``specs/research/together-ai-survey-2026-10-13.md`` §3.1, next step 2, and the
design of §8.4). The default arm ran on the same 44 tasks in nights 2, 3 and 4:
one run resolves 29-31, *some* run resolves 36. Best-of-N is worth building only
if a chooser picks a resolving patch where the runs disagree. This script
measures that on the patches already on disk: no agent run, no container, and
about an hour of model time.

The chooser, R2E-Gym's and V1's way (survey §8.4):

1. **Regression filter.** A candidate that breaks the repository's existing
   tests (a PASS_TO_PASS failure in the grading logs already on disk), or whose
   patch did not apply, is dropped. Nothing is re-run. If every candidate
   breaks something, all of them go on to the judge. R2E-Gym: this filter alone
   took Best@26 from 42.9% to 47.4%.
2. **Pairwise judge.** The survivors are compared in pairs by the served model
   (V1: the generator judges its own candidates). One call per pair shows the
   issue text and the two diffs, never the agent's reasoning, and rates both
   1-10. A win scores 1, a tie 0.5, weighted by the rating gap,
   max(|r_a - r_b| / 9, 0.1). Pairing: random disjoint pairs until every
   candidate has been compared twice, then Swiss rounds among neighbours in
   score within a window of 8. With three candidates that is the three pairs.
   V1 on SWE-bench Lite: pairwise 33.3%, pointwise 28.3%, one run 26.3%.
3. **The pick** is the candidate with the most points; a tie at the top is
   scored as the share of the tied candidates that resolve (a fair coin).

**The chooser never sees whether a patch fixed the issue.** It reads no
FAIL_TO_PASS result and no `resolved` flag: those are what the test is scored
on, and a chooser that saw them would grade itself. In the code, the candidates
handed to the chooser (`Candidate`) carry no verdict; the verdicts are only
read by `score`.

What is reported, over the tasks where the runs disagree (``--all``: every task):
the chooser's expected resolved count, the regression filter's alone (a fair
coin among the survivors), a run picked at random, and the oracle (any run);
which stage made each choice; and the projected best-of-N on the whole list
(the tasks every run resolved, plus the chooser's share of the rest).

Every judgment is appended to ``<out>/judgments.jsonl`` as it is made, so an
interrupted test resumes where it stopped; ``<out>/summary.json`` is written at
the end. Nothing is written anywhere else; the runs are only read.

Runbook (about an hour of model time; NOT while a run needs the server)
-----------------------------------------------------------------------

The test runs on this machine (the runs are here) against second-puffin's
production server, at a time when nothing else needs that server.

1. Dry run here, no server contacted::

       .venv/bin/python scripts/swe_bench_chooser_test.py --dry-run

   It lists the kept tasks, each candidate with what the regression filter
   does to it, and the number of judge calls. For nights 2-4: 13 tasks, the
   filter decides 3, the judge gets 10 at 26 calls at most.
2. Check the server is free. No SWE-bench run, Night Shift task or ling-engine
   test may be using second-puffin's model server: a run's model gate refuses
   outside requests (the script then stops with the refusal), and a shared
   server would slow the run. ``ssh second-puffin ling-admin night status``
   and ``ssh second-puffin ling-admin swe-bench status``; ``ling-admin night
   pause`` only if the owner agreed to share it.
3. Name the server by its node name, never an address (the node's mDNS name;
   ``ling node list`` shows it), and check it answers::

       ling-fetch http://second-puffin.local:8000/v1/models

4. Run::

       .venv/bin/python scripts/swe_bench_chooser_test.py \\
           --server http://second-puffin.local:8000 \\
           --runs n2-default,n3-default,n4-default --seed 20261013 \\
           --out ~/swe-bench-chooser-2026-10

   The model id comes from the server's ``/v1/models``. The defaults are
   Qwen3.8's recommended sampling (temperature 0.6, top-p 0.95) and room for
   its thinking (``--max-tokens 16384``); ``--seed`` pins the sampling and the
   pairing. Interrupted, run the same command again: judged pairs are not
   asked twice.
5. Read ``summary.json`` (the printed summary is the same). The decision rule
   (survey §3.1): best-of-N in Night Shift (backlog item 6) goes ahead if the
   chooser resolves clearly more than a random run on the kept tasks; the
   estimate to beat is +3 to +4 tasks. The filter-alone figure says how much
   of that the judge adds.

Tests: ``tests/test_swe_bench_chooser.py`` (a fake server on loopback).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

DEFAULT_RUNS_DIR: Final[Path] = Path.home() / ".local" / "share" / "dreamference" / "swe-bench" / "runs"
DEFAULT_DATASET: Final[Path] = Path.home() / ".cache" / "dreamference" / "swe-bench" / "datasets" / "SWE-bench_Verified.jsonl"
DEFAULT_RUNS: Final[str] = "n2-default,n3-default,n4-default"
HARNESS_LOG_DIR: Final[str] = "logs/run_evaluation"
# A patch longer than this is cut, with a note, so two patches and the issue fit the context.
MAX_PATCH_CHARS: Final[int] = 40_000
MAX_ISSUE_CHARS: Final[int] = 20_000

# V1's tournament (arXiv 2603.04304): every candidate compared at least twice, then Swiss
# rounds among neighbours in score; the weight floor of a decided comparison.
MIN_COMPARISONS: Final[int] = 2
SWISS_WINDOW: Final[int] = 8
WEIGHT_FLOOR: Final[float] = 0.1
RATING: Final[re.Pattern] = re.compile(r"RATING_([AB]):\s*\**\s*(\d+(?:\.\d+)?)")

SYSTEM_PROMPT: Final[str] = (
    "You are an expert code reviewer. Compare two solutions to a programming problem and rate their "
    "correctness. The problem is a GitHub issue in a Python repository; each solution is a patch "
    "(a diff) written to fix it. Neither patch has been run. A correct patch fixes the behaviour the "
    "issue describes and breaks no existing behaviour. If both look correct, prefer the more robust "
    "and fault-tolerant one. Ignore style, comments and the tests a patch adds."
)

USER_TEMPLATE: Final[str] = """<issue>
{issue}
</issue>

<solution_A>
{a}
</solution_A>

<solution_B>
{b}
</solution_B>

Rate the correctness of each solution from 1 (wrong) to 10 (certainly correct). End your answer with exactly these two lines:
RATING_A: <1-10>
RATING_B: <1-10>"""


@dataclass
class Candidate:
    """One distinct patch for a task, as the chooser sees it: no verdict on the issue."""

    id: str
    patch: str
    runs: List[str]
    # The regression filter's input: the patch applied, and how many PASS_TO_PASS tests it broke
    # (None when no test results are on disk). Never FAIL_TO_PASS, never `resolved`.
    applied: bool = True
    p2p_failed: Optional[int] = None

    @property
    def breaks(self) -> bool:
        """True when the regression filter drops it."""
        return not self.applied or bool(self.p2p_failed)


@dataclass
class Task:
    """One task: its candidates for the chooser, and the verdicts kept apart for scoring."""

    instance_id: str
    candidates: List[Candidate]
    run_verdicts: Dict[str, bool]
    resolves: Dict[str, bool] = field(default_factory=dict)  # candidate id -> resolved (scoring only)
    flaky: bool = False


# --------------------------------------------------------------------------- the runs on disk


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Reads a JSON object; None when the file is missing or not an object."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def regression_record(directory: Path, run: str, number: int, model: str, instance_id: str,
                      result: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
    """
    What the regression filter may know about one graded patch: whether it applied and how many
    PASS_TO_PASS tests it broke. Taken from the grading record when it has the count, otherwise
    from the harness's `report.json` in the grading's directory. FAIL_TO_PASS is not read.

    Returns:
        Tuple[bool, Optional[int]]: (applied, PASS_TO_PASS failures or None when unknown).
    """
    applied = result.get("applied") is not False and not result.get("empty")
    if "p2p_failed" in result:
        return applied, int(result.get("p2p_failed") or 0)
    report = read_json(directory / "eval" / str(number) / HARNESS_LOG_DIR / f"{run}-{number}"
                       / model.replace("/", "__") / instance_id / "report.json")
    verdict = (report or {}).get(instance_id) or {}
    status = verdict.get("tests_status") if isinstance(verdict, dict) else None
    if not isinstance(status, dict) or "PASS_TO_PASS" not in status:
        return applied, None
    return applied, len((status["PASS_TO_PASS"] or {}).get("failure") or [])


def load_run(runs_dir: Path, name: str) -> Dict[str, Any]:
    """
    Reads one run, read only.

    Args:
        runs_dir: The directory holding the runs.
        name: The run's name.

    Returns:
        Dict[str, Any]: `name`, `instances` (the manifest's list), `patches` (instance to its
        last prediction's patch), `regression` (instance to `regression_record`'s pair) and
        `resolved` (instance to the latest plain grading's verdict, for scoring only).

    Raises:
        SystemExit: When the run, or a grading of it, is missing.
    """
    directory = runs_dir / name
    manifest = read_json(directory / "manifest.json")
    if manifest is None:
        raise SystemExit(f"❌ No run {name} under {runs_dir}")
    patches: Dict[str, str] = {}
    models: Dict[str, str] = {}
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
            models[record["instance_id"]] = str(record.get("model_name_or_path") or "")
    numbers = sorted(int(entry.name) for entry in (directory / "eval").glob("*") if entry.name.isdigit())
    if not numbers:
        raise SystemExit(f"❌ {name} has no grading: run `ling-admin swe-bench eval {name}` first")
    grading = read_json(directory / "eval" / str(numbers[-1]) / "grading.json") or {}
    results = grading.get("results") or {}
    model = str(manifest.get("model_name_or_path") or "")
    return {
        "name": name, "instances": list(manifest.get("instances") or []), "patches": patches,
        "regression": {i: regression_record(directory, name, numbers[-1], models.get(i) or model, i, r)
                       for i, r in results.items()},
        "resolved": {i: bool(r.get("resolved")) for i, r in results.items()},
    }


def load_issues(dataset: Path, ids: Sequence[str]) -> Dict[str, str]:
    """
    Reads the issue texts of `ids` from the local dataset snapshot (one JSON row per line).

    Returns:
        Dict[str, str]: Instance to its `problem_statement`.

    Raises:
        SystemExit: When the snapshot is missing or an instance is not in it.
    """
    wanted, issues = set(ids), {}
    try:
        lines = dataset.read_text().splitlines()
    except OSError:
        raise SystemExit(f"❌ No dataset snapshot at {dataset} (ling-admin swe-bench setup writes it)")
    for line in lines:
        if line.strip():
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


def select_tasks(runs: List[Dict[str, Any]], every: bool = False) -> List[Task]:
    """
    The tasks to judge, with their candidates.

    Args:
        runs: The loaded runs.
        every: Keep every task graded in all runs, not only those where they disagree.

    Returns:
        List[Task]: One per task, sorted by instance id. The candidates are the distinct
        non-empty patches; one written by several runs breaks if any of its gradings broke a
        PASS_TO_PASS test, and is `flaky` if its gradings disagree on the verdict.

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
        candidates: Dict[str, Candidate] = {}
        resolved: Dict[str, List[bool]] = {}
        for run in runs:
            patch = run["patches"].get(instance_id, "")
            if not patch.strip():
                continue  # an empty patch is never chosen
            cid = digest(patch)
            candidate = candidates.setdefault(cid, Candidate(id=cid, patch=patch, runs=[]))
            candidate.runs.append(run["name"])
            applied, broken = run["regression"].get(instance_id, (True, None))
            candidate.applied = candidate.applied and applied
            if broken is not None:
                candidate.p2p_failed = max(candidate.p2p_failed or 0, broken)
            resolved.setdefault(cid, []).append(verdicts[run["name"]])
        tasks.append(Task(instance_id=instance_id, candidates=sorted(candidates.values(), key=lambda c: c.runs),
                          run_verdicts=verdicts, resolves={cid: all(v) for cid, v in resolved.items()},
                          flaky=any(len(set(v)) > 1 for v in resolved.values())))
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
        request = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
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


def ratings(answer: str) -> Optional[Tuple[float, float]]:
    """The last `RATING_A` and `RATING_B` of an answer, clamped to 1-10; None when either is missing."""
    found: Dict[str, float] = {}
    for side, value in RATING.findall(answer):
        found[side] = min(10.0, max(1.0, float(value)))
    return (found["A"], found["B"]) if "A" in found and "B" in found else None


# --------------------------------------------------------------------------- the chooser


def pairings(ids: List[str], scores: Dict[str, float], played: set, counts: Dict[str, int],
             rng: random.Random) -> List[Tuple[str, str]]:
    """
    The next round of V1's tournament: random disjoint pairs while some candidate has fewer
    than two comparisons, then a Swiss round (neighbours in score, within a window of 8). A pair
    is never played twice.

    Returns:
        List[Tuple[str, str]]: The round's pairs; empty when the tournament is over.
    """
    def unplayed(a: str, b: str) -> bool:
        return frozenset((a, b)) not in played

    if any(counts[i] < MIN_COMPARISONS for i in ids):
        order = sorted(ids, key=lambda i: (counts[i], rng.random()))
        round_pairs, used = [], set()
        for a in order:
            if a in used or counts[a] >= MIN_COMPARISONS:
                continue
            partner = next((b for b in order if b != a and b not in used and unplayed(a, b)), None)
            if partner is not None:
                round_pairs.append((a, partner))
                used.update((a, partner))
        if round_pairs:
            return round_pairs
    ranked = sorted(ids, key=lambda i: -scores[i])
    round_pairs, used = [], set()
    for position, a in enumerate(ranked):
        if a in used:
            continue
        partner = next((b for b in ranked[position + 1:position + 1 + SWISS_WINDOW]
                        if b not in used and unplayed(a, b)), None)
        if partner is not None:
            round_pairs.append((a, partner))
            used.update((a, partner))
    return round_pairs


def tournament(candidates: List[Candidate], compare: Callable[[Candidate, Candidate], Optional[Tuple[float, float]]],
               rng: random.Random, swiss_rounds: int = 1) -> Dict[str, float]:
    """
    V1's pairwise tournament over the candidates.

    Args:
        candidates: The survivors of the regression filter.
        compare: One judge call: (A, B) -> (rating of A, rating of B), or None when unreadable.
        rng: The pairing's randomness.
        swiss_rounds: Swiss rounds after every candidate has its two comparisons.

    Returns:
        Dict[str, float]: Candidate id to points: per comparison, 1 / 0.5 / 0 for a win, tie or
        loss, times max(|rating gap| / 9, 0.1); an unreadable answer scores 0.5 each at the floor.
    """
    by_id = {c.id: c for c in candidates}
    ids = sorted(by_id)
    scores = {i: 0.0 for i in ids}
    counts = {i: 0 for i in ids}
    played: set = set()
    swiss = 0
    while len(ids) > 1:
        before_swiss = any(counts[i] < MIN_COMPARISONS for i in ids)
        if not before_swiss:
            if swiss >= swiss_rounds:
                break
            swiss += 1
        round_pairs = pairings(ids, scores, played, counts, rng)
        if not round_pairs:
            break
        for a, b in round_pairs:
            played.add(frozenset((a, b)))
            counts[a] += 1
            counts[b] += 1
            rated = compare(by_id[a], by_id[b])
            if rated is None:
                scores[a] += 0.5 * WEIGHT_FLOOR
                scores[b] += 0.5 * WEIGHT_FLOOR
                continue
            weight = max(abs(rated[0] - rated[1]) / 9, WEIGHT_FLOOR)
            outcome = 1.0 if rated[0] > rated[1] else 0.0 if rated[0] < rated[1] else 0.5
            scores[a] += weight * outcome
            scores[b] += weight * (1 - outcome)
    return scores


def choose(candidates: List[Candidate], compare: Callable[[Candidate, Candidate], Optional[Tuple[float, float]]],
           rng: random.Random, swiss_rounds: int = 1) -> Dict[str, Any]:
    """
    The chooser: the regression filter, then the pairwise judge over the survivors. It is given
    no verdict on the issue.

    Returns:
        Dict[str, Any]: `stage` (`none`, `only candidate`, `regression filter`, `judge`, or
        `judge, all broke`), `survivors`, `points`, and `chosen` (the top candidate ids).
    """
    if not candidates:
        return {"stage": "none", "survivors": [], "points": {}, "chosen": []}
    if len(candidates) == 1:
        return {"stage": "only candidate", "survivors": [candidates[0].id], "points": {}, "chosen": [candidates[0].id]}
    survivors = [c for c in candidates if not c.breaks]
    if len(survivors) == 1:
        return {"stage": "regression filter", "survivors": [survivors[0].id], "points": {},
                "chosen": [survivors[0].id]}
    stage = "judge" if survivors else "judge, all broke"
    pool = survivors or candidates
    points = tournament(pool, compare, rng, swiss_rounds)
    top = max(points.values())
    return {"stage": stage, "survivors": [c.id for c in survivors], "points": points,
            "chosen": sorted(i for i, p in points.items() if p == top)}


def score(task: Task, choice: Dict[str, Any]) -> Dict[str, float]:
    """
    Scores a choice against the verdicts the chooser never saw.

    Returns:
        Dict[str, float]: `chooser` (the share of the chosen that resolve), `filter` (the share
        of the survivors that resolve, i.e. the filter alone and a fair coin; all candidates when
        none survived), `random_run` (the share of runs that resolved).
    """
    def share(ids: List[str]) -> float:
        return sum(1 for i in ids if task.resolves.get(i)) / len(ids) if ids else 0.0

    pool = choice["survivors"] or [c.id for c in task.candidates]
    return {"chooser": share(choice["chosen"]), "filter": share(pool),
            "random_run": sum(task.run_verdicts.values()) / len(task.run_verdicts)}


def summarise(runs: List[Dict[str, Any]], tasks: List[Task], scores: Dict[str, Dict[str, float]],
              choices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    The test's figures, over the judged tasks and projected onto the whole list.

    Returns:
        Dict[str, Any]: `tasks`, `chooser`, `filter_alone`, `random_run`, `oracle`, `stages`
        (stage to tasks), `listed`, `single_runs`, `all_resolved`, `projected`, `flaky`.
    """
    listed = sorted(set(runs[0]["instances"]))
    graded_everywhere = [i for i in listed if all(i in run["resolved"] for run in runs)]
    all_resolved = [i for i in graded_everywhere if all(run["resolved"][i] for run in runs)]
    judged_ids = {t.instance_id for t in tasks}
    disagree = [t for t in tasks if len(set(t.run_verdicts.values())) == 2]
    stages: Dict[str, int] = {}
    for choice in choices.values():
        stages[choice["stage"]] = stages.get(choice["stage"], 0) + 1
    total = lambda key, among: round(sum(scores[t.instance_id][key] for t in among), 2)
    projected = sum(1 for i in all_resolved if i not in judged_ids) + total("chooser", tasks)
    return {
        "runs": [run["name"] for run in runs], "tasks": len(tasks), "disagreeing": len(disagree),
        "chooser": total("chooser", tasks), "filter_alone": total("filter", tasks),
        "random_run": total("random_run", tasks), "oracle": sum(1 for t in tasks if any(t.run_verdicts.values())),
        "stages": stages, "listed": len(listed),
        "single_runs": {run["name"]: sum(1 for i in listed if run["resolved"].get(i)) for run in runs},
        "all_resolved": len(all_resolved), "projected": round(projected, 2),
        "flaky": sorted(t.instance_id for t in tasks if t.flaky),
    }


# --------------------------------------------------------------------------- main


def describe(candidate: Candidate) -> str:
    """One candidate as the dry run lists it, without its verdict."""
    if not candidate.applied:
        state = "did not apply: dropped"
    elif candidate.p2p_failed:
        state = f"breaks {candidate.p2p_failed} PASS_TO_PASS: dropped"
    elif candidate.p2p_failed is None:
        state = "no test results: kept"
    else:
        state = "kept"
    return f"{candidate.id} [{'+'.join(candidate.runs)}] {state}"


def run_test(args: argparse.Namespace, client_factory: Callable[[str, float], Client] = Client) -> int:
    """The test, as `main` runs it; the client is replaceable for the tests."""
    runs = [load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    if len(runs) < 2:
        raise SystemExit("❌ Name at least two runs")
    tasks = select_tasks(runs, args.all)
    if args.limit:
        tasks = tasks[:args.limit]
    pools = {t.instance_id: len([c for c in t.candidates if not c.breaks] or t.candidates) for t in tasks}
    judged = [t for t in tasks if pools[t.instance_id] > 1]
    calls = sum(pools[t.instance_id] * (pools[t.instance_id] - 1) // 2 for t in judged)
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; {len(judged)} go to the judge, "
          f"{calls} judge call(s) at most")
    for task in tasks:
        print(f"  {task.instance_id:<34} " + ("; ".join(describe(c) for c in task.candidates) or "no non-empty patch"))
    if args.dry_run:
        return 0
    if not args.server:
        raise SystemExit("❌ --server is required (the model server's URL, by node name)")

    issues = load_issues(args.dataset, [t.instance_id for t in judged])
    client = client_factory(args.server, args.timeout)
    model = args.model or client.model()
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "judgments.jsonl"
    done: Dict[Tuple[str, str, str], Optional[Tuple[float, float]]] = {}
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            done[(record["instance_id"], record["a"], record["b"])] = \
                tuple(record["ratings"]) if record.get("ratings") else None

    def judge_for(instance_id: str) -> Callable[[Candidate, Candidate], Optional[Tuple[float, float]]]:
        def compare(a: Candidate, b: Candidate) -> Optional[Tuple[float, float]]:
            key = (instance_id, a.id, b.id)
            if key in done:
                return done[key]
            payload = {"model": model, "max_tokens": args.max_tokens, "temperature": args.temperature,
                       "top_p": args.top_p,
                       "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(
                                        issue=clip(issues[instance_id], MAX_ISSUE_CHARS),
                                        a=clip(a.patch, MAX_PATCH_CHARS), b=clip(b.patch, MAX_PATCH_CHARS))}]}
            if args.seed is not None:
                payload["seed"] = args.seed
            answer = client.chat(payload)
            done[key] = ratings(answer)
            with open(log, "a") as handle:
                handle.write(json.dumps({"instance_id": instance_id, "a": a.id, "b": b.id, "ratings": done[key],
                                         "model": model, "answer_tail": answer[-600:]}) + "\n")
            return done[key]
        return compare

    choices, scores = {}, {}
    for task in tasks:
        rng = random.Random(f"{args.seed}:{task.instance_id}")
        choice = choose(task.candidates, judge_for(task.instance_id), rng, args.swiss_rounds)
        choices[task.instance_id], scores[task.instance_id] = choice, score(task, choice)
        print(f"  {task.instance_id:<34} {choice['stage']}: chose {', '.join(choice['chosen']) or 'nothing'}, "
              f"credit {scores[task.instance_id]['chooser']:.2f}")
    summary = summarise(runs, tasks, scores, choices)
    summary.update({"model": model, "server": args.server, "per_task": {
        i: {**choices[i], **scores[i]} for i in choices}})
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nOn the {summary['tasks']} task(s): chooser {summary['chooser']}, the regression filter alone "
          f"{summary['filter_alone']}, a random run {summary['random_run']}, any run (oracle) {summary['oracle']}")
    print("Chosen by: " + ", ".join(f"{stage} {count}" for stage, count in sorted(summary["stages"].items())))
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
    p.add_argument("--seed", type=int, help="pins the pairing, and the sampling if the server takes a seed")
    p.add_argument("--swiss-rounds", type=int, default=1, help="Swiss rounds after two comparisons each (V1)")
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
