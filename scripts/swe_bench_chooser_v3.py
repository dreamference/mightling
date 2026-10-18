#!/usr/bin/env python3
"""The chooser test, version 3: chooser v1 with a differential search before its judge.

Version 1 (``scripts/swe_bench_chooser_test.py``, Night Shift's ``NightShiftChooser``) drops the
candidates that break existing tests, then lets the served model compare the survivors in pairs from
the issue and the two diffs. Version 2 (reproduction tests written blind, behaviour groups) scored
below it on nights 4-6 (32.33 against 33.0 of 44) and was not adopted. Version 3 is the first stage
of the research note's design (``specs/research/chooser-v3-moonshot-2026-10-14.md`` §3, stage 4;
the owner, 2026-10-14: "differential-search stage first, from whichever of v1/v2 scores higher"):
it keeps version 1 whole and changes only what one comparison of two candidates asks.

**One comparison, version 3's way.** The model is shown the issue and the two diffs (as fix X and
fix Y, the order drawn per pair) and asked not which is right but where they *behave* differently:
it writes one standalone script that exercises that difference, prints what it observes, and exits
0 when the behaviour is the one the issue requires and 1 when it is not, the requirement being
written into the script before anything runs (the pre-registered expectation of CodeMonkeys'
selection and PatchDiff's differentiating tests). The script runs in the task's own container, with
no network, on the unpatched repository and with each candidate applied (version 2's containers:
the grader's set-up up to the hidden tests, the harness's patch chain, a fresh container each).

- One candidate meets the expectation and the other does not: it wins the pair outright.
- Neither meets it but their outputs differ: the model is shown the issue, the script and only
  the outputs (unpatched, P and Q; never a diff) and asked which behaviour the issue wants.
- Otherwise (the same result on both, both meet it, no script, a verdict of neither) the round is
  void, and the next round's request is told what the earlier scripts printed.
- After ``--rounds`` void rounds (default 3), or when the task's time cap is reached, the pair
  goes to version 1's judge on the diffs, the very call version 1 made: its answer is read from
  version 1's ``judgments.jsonl`` when that run made it (``--v1-judgments``), so a pair the search
  cannot separate is decided exactly as version 1 decided it.

A win counts as ratings 10 against 1 in version 1's tournament (full weight), so the filter, the
pairing, the points and the pick are version 1's code, unchanged (``NightShiftChooser.choose``).

**The chooser never sees whether a patch fixed the issue,** as in versions 1 and 2: no FAIL_TO_PASS,
no ``resolved``, no hidden test, no reference patch (``FORBIDDEN_FIELDS`` are removed from the rows
when they are read). The verdicts are read only by the scoring, after every choice is made, and by
the per-pair accuracy that says how often each way of deciding a pair was right.

**Never beside a SWE-bench night.** Before every model call and every container the script checks
that no ``ling-swe-night*`` unit is active; if one is, it stops with exit status 75 (``PAUSED``) and
everything done so far is kept: ``calls.jsonl`` (every model answer) and ``containers.jsonl`` (every
container's result) under ``--out``, so the same command resumes without asking or running anything
twice. A refusal by the model gate (a run holds the server) stops it the same way, with exit 1.

Runbook (the model server of this machine, through its gate)
------------------------------------------------------------

1. Dry run, no server and no container::

       .venv/bin/python scripts/swe_bench_chooser_v3.py --dry-run \\
           --runs n11-default-1,n11-default-2,n11-default-3

2. Run on a trio version 1 has chosen over, against its summary and judgments::

       .venv/bin/python scripts/swe_bench_chooser_v3.py --server http://localhost:8000 \\
           --runs n11-default-1,n11-default-2,n11-default-3 --seed 20261013 \\
           --v1-summary ~/.local/share/dreamference/swe-bench/night11/chooser-v1/summary.json \\
           --out ~/swe-bench-chooser-v3-2026-10/n11

3. The verdict over several trios (version 3 is "not worse" when it is at least version 1 on every
   trio and loses no task version 1 got; "better" when it also gains one)::

       .venv/bin/python scripts/swe_bench_chooser_v3.py --verdict <summary.json>,<summary.json> \\
           --verdict-out ~/.local/share/dreamference/chooser-v3-offline.json

Tests: ``tests/test_swe_bench_chooser_v3.py`` (fake docker on scratch git repositories, a fake
server on loopback, no unit check against the real systemd).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

SCRIPTS: Final[Path] = Path(__file__).resolve().parent
if str(SCRIPTS.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPTS.parent))

from dreamference.night_shift.night_shift_chooser import NightShiftChooser  # noqa: E402
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker  # noqa: E402
from dreamference.swe_bench.swe_bench_images import SweBenchImages  # noqa: E402


def _load(name: str, filename: str) -> Any:
    """A script module beside this one (version 2's, which loads version 1's)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    return module


v2 = _load("swe_bench_chooser_v2", "swe_bench_chooser_v2.py")
v1 = v2.v1

ROUNDS: Final[int] = 3
WIN: Final[Tuple[float, float]] = (10.0, 1.0)
PAUSED: Final[int] = 75
CONTAINER_NAME: Final[str] = "ling-chooser-v3-"
CONTAINER_LABEL: Final[str] = "ling.swe-bench.chooser=v3"
OUTPUT_SHOWN_CHARS: Final[int] = 3_000
FEEDBACK_CHARS: Final[int] = 900
# What may differ between two runs of one script without the behaviour differing.
NOISE: Final[Tuple[Tuple[re.Pattern, str], ...]] = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0x?"),
    # A traceback through a patched file has other line numbers with each patch.
    (re.compile(r"\bline \d+"), "line ?"),
    (re.compile(r"/tmp/[\w.-]+"), "/tmp/?"),
    (re.compile(r"\d+\.\d+ ?(s|ms|seconds)\b"), "?s"),
    (re.compile(r"[ \t]+"), " "),
)
EXPECTED: Final[re.Pattern] = re.compile(r"^\s*\**EXPECTED:?\**:?\s*(.+)$", re.MULTILINE)
BEHAVIOUR: Final[re.Pattern] = re.compile(r"BEHAVIOUR:\s*\**\s*(P|Q|NEITHER)\b", re.IGNORECASE)

WRITER_SYSTEM: Final[str] = (
    "You review two candidate fixes for a bug report in a Python repository. Your job is not to say "
    "which diff looks right: it is to find a concrete case on which the two fixes behave differently, "
    "and to decide from the issue, before anything runs, which behaviour the issue requires there. "
    "Your script will be run on the unpatched repository and with each fix applied."
)

WRITER_TEMPLATE: Final[str] = """The repository {repo} (version {version}) is checked out at /testbed with its dependencies installed; `python` is the repository's interpreter. A user filed this issue:

<issue>
{issue}
</issue>

Two candidate fixes were written for it:

<fix_X>
{x}
</fix_X>

<fix_Y>
{y}
</fix_Y>

Find where the two fixes behave differently on something the issue cares about: an input, a call or a case that one fix handles differently from the other. Then write ONE standalone Python script that exercises exactly that, and that:
- is run as `python <script>` with /testbed as the working directory, with no arguments, no network and nothing else installed;
- prints what it observes (the values, the exception, the output), one observation per line;
- holds the behaviour the issue requires, decided from the issue alone, and exits with status 0 when the observed behaviour is that one and with status 1 when it is not;
- tests the repository's behaviour only, never which fix is applied (no reading of the source, no looking at the diff);
- sets up what the library needs by itself (for Django: `settings.configure(...)` with only the apps it needs, then `django.setup()`, and an in-memory SQLite database if it needs one);
- writes nothing inside /testbed (use the `tempfile` module) and finishes in well under a minute.
{feedback}
First explain in a few sentences where the two fixes differ. Then give the behaviour the issue requires on one line starting with `EXPECTED:`. Then give the script as one fenced ```python code block, and put nothing else in code blocks."""

FEEDBACK_TEMPLATE: Final[str] = """
Your earlier script(s) did not separate the two fixes. What they did:
{rounds}
Write a different script, one whose result differs between fix X and fix Y on what the issue requires."""

VERDICT_SYSTEM: Final[str] = (
    "You decide which observed behaviour a bug report asks for. You are shown a GitHub issue, a script "
    "that exercises the repository, and what the script printed on the unpatched repository and with two "
    "candidate fixes applied. You are not shown the fixes. Judge only from the issue and the outputs."
)

VERDICT_TEMPLATE: Final[str] = """<issue>
{issue}
</issue>

<script>
{script}
</script>

The script's author expected: {expected}

<output_unpatched exit_status="{base_exit}">
{base}
</output_unpatched>

<output_P exit_status="{p_exit}">
{p}
</output_P>

<output_Q exit_status="{q_exit}">
{q}
</output_Q>

Which output shows the behaviour the issue asks for? The script's own exit status may be wrong: judge the printed behaviour against the issue. End your answer with exactly one of these lines:
BEHAVIOUR: P
BEHAVIOUR: Q
BEHAVIOUR: NEITHER"""


class Paused(Exception):
    """A SWE-bench night became active: stop, keep everything, resume later."""


def night_units() -> List[str]:
    """The active `ling-swe-night*` units (a night holds the model server and Docker)."""
    try:
        listed = subprocess.run(["systemctl", "--user", "list-units", "--state=active", "--no-legend", "--plain",
                                 "ling-swe-night*"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in listed.splitlines() if line.strip()]


def parse_script(answer: str) -> Tuple[str, Optional[str]]:
    """
    The writer's answer: its last `EXPECTED:` line and its first Python block.

    Returns:
        Tuple[str, Optional[str]]: (the expectation, "" when not given; the script, None when none).
    """
    found = EXPECTED.findall(answer)
    tests = v2.parse_tests(answer)
    return (found[-1].strip() if found else ""), (tests[0] if tests else None)


def normalised(output: str) -> str:
    """An output with what changes between two runs (addresses, temporary paths, timings) masked."""
    text = output.strip()
    for pattern, mask in NOISE:
        text = pattern.sub(mask, text)
    return text


def ends(text: str, limit: int = FEEDBACK_CHARS) -> str:
    """A long output's head and tail (a traceback's last line is what says why it failed)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + f"\n[... {len(text) - limit} characters ...]\n" + text[-(limit - head):]


def outcome(result: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """A container's result as the round reads it: (exit status, None when it did not run; output)."""
    if result.get("error"):
        return None, "(the container failed: " + str(result["error"])[:300] + ")"
    if result.get("applied") is None:
        return None, "(the patch did not apply)"
    tests = result.get("tests") or [{}]
    return tests[0].get("exit"), str(tests[0].get("output") or "")


def met(result: Dict[str, Any]) -> bool:
    """True when the script exited 0 with the candidate applied: the expectation was met."""
    return outcome(result)[0] == 0


class TaskChooser:
    """Version 3's comparison for one task's candidates; everything it asks or runs is cached on disk."""

    def __init__(self, runner: "Runner", task: Any, image: Optional[str]) -> None:
        self.runner, self.task, self.image = runner, task, image
        self.args = runner.args
        self.row = runner.rows.get(task.instance_id) or {}
        self.deadline = v2.Deadline(self.args.task_timeout)
        self.pairs: List[Dict[str, Any]] = []
        self.pulled = False
        self.image_ready: Optional[bool] = None
        self.containers = v2.Containers(image or "", task.instance_id, self.args, name=CONTAINER_NAME,
                                        label=CONTAINER_LABEL)

    # -- the image ---------------------------------------------------------------------------

    def ensure_image(self) -> bool:
        """The task's image, pulled once if missing (and removed by `close`); False when it cannot be had."""
        if self.image_ready is not None:
            return self.image_ready
        self.image_ready = False
        if not self.image:
            return False
        if SweBenchDocker.image_digest(self.image) is None:
            self.runner.check_night()
            self.pulled = True
            SweBenchDocker.run(["pull", "-q", self.image], timeout=self.deadline.bound(v2.PULL_TIMEOUT_S))
        self.image_ready = SweBenchDocker.image_digest(self.image) is not None
        return self.image_ready

    def close(self) -> None:
        """Removes the image this task pulled, unless `--keep-images`."""
        if self.pulled and not self.args.keep_images and self.image:
            SweBenchImages.remove([self.image])

    # -- one container -----------------------------------------------------------------------

    def run(self, script: str, candidate: Optional[Any]) -> Dict[str, Any]:
        """The script on the unpatched repository (`candidate` None) or with a candidate applied; cached."""
        target = candidate.id if candidate is not None else "base"
        key = json.dumps(["container", self.task.instance_id, hashlib.sha256(script.encode()).hexdigest()[:16], target])
        if key in self.runner.results:
            return self.runner.results[key]
        self.runner.check_night()
        raw = self.containers.run(self.row.get("setup") or "", [script], candidate.patch if candidate else None,
                                  self.deadline)
        tests = raw.get("tests") or []
        result = {"applied": raw.get("applied"), "setup": raw.get("setup"), "error": raw.get("error"),
                  "tests": [{"exit": t.get("exit"), "compiled": t.get("compiled"), "output": t.get("output")}
                            for t in tests[:1]]}
        self.runner.store_result(key, result)
        return result

    # -- the comparison ----------------------------------------------------------------------

    def writer_payload(self, x: Any, y: Any, feedback: str, round_number: int) -> Dict[str, Any]:
        """One writer request: the issue, the repository, the two diffs; never a verdict."""
        user = WRITER_TEMPLATE.format(
            repo=self.row.get("repo") or "", version=self.row.get("version") or "",
            issue=v1.clip(str(self.row.get("problem_statement") or ""), v1.MAX_ISSUE_CHARS),
            x=v1.clip(x.patch, v1.MAX_PATCH_CHARS), y=v1.clip(y.patch, v1.MAX_PATCH_CHARS), feedback=feedback)
        payload = self.runner.sampling([{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": user}])
        if self.args.seed is not None:
            payload["seed"] = self.args.seed + 100 * round_number
        return payload

    def verdict_payload(self, script: str, expected: str, base: Dict[str, Any], p: Dict[str, Any],
                        q: Dict[str, Any]) -> Dict[str, Any]:
        """The outputs-only verdict: the issue, the script and what it printed; never a diff."""
        shown = lambda result: ends(outcome(result)[1], OUTPUT_SHOWN_CHARS) or "(no output)"
        user = VERDICT_TEMPLATE.format(
            issue=v1.clip(str(self.row.get("problem_statement") or ""), v1.MAX_ISSUE_CHARS),
            script=v1.clip(script, v2.MAX_TEST_CHARS), expected=expected or "(not stated)",
            base_exit=outcome(base)[0], base=shown(base), p_exit=outcome(p)[0], p=shown(p),
            q_exit=outcome(q)[0], q=shown(q))
        payload = self.runner.sampling([{"role": "system", "content": VERDICT_SYSTEM}, {"role": "user", "content": user}])
        if self.args.seed is not None:
            payload["seed"] = self.args.seed + 7
        return payload

    def search(self, a: Any, b: Any, record: Dict[str, Any]) -> Optional[Any]:
        """
        The differential search over one pair, at most `--rounds` rounds.

        Returns:
            Optional[Any]: The winning candidate, or None when no round separated them.

        Raises:
            v2.TaskTimeout: When the task's time ran out.
            Paused: When a SWE-bench night became active.
        """
        rng = random.Random(f"{self.args.seed}:{self.task.instance_id}:{a.id}:{b.id}")
        x, y = (a, b) if rng.random() < 0.5 else (b, a)
        record.update({"x": x.id, "y": y.id})
        void: List[str] = []
        for number in range(self.args.rounds):
            feedback = FEEDBACK_TEMPLATE.format(rounds="\n".join(void)) if void else ""
            answer = self.runner.ask(["writer", self.task.instance_id, x.id, y.id, number],
                                     self.writer_payload(x, y, feedback, number), self.deadline)
            expected, script = parse_script(answer)
            entry: Dict[str, Any] = {"round": number, "expected": expected}
            record["rounds"].append(entry)
            if script is None:
                entry["void"] = "no script in the answer"
                void.append(f"- round {number + 1}: your answer had no ```python block.")
                continue
            entry["script"] = script
            if not self.ensure_image():
                entry["void"] = "no image"
                record["note"] = f"the image {self.image} could not be had"
                return None
            base, rx, ry = self.run(script, None), self.run(script, x), self.run(script, y)
            entry["exits"] = {"base": outcome(base)[0], "x": outcome(rx)[0], "y": outcome(ry)[0]}
            if met(rx) != met(ry):
                winner = x if met(rx) else y
                entry.update({"winner": winner.id, "by": "expectation"})
                return winner
            ox, oy = outcome(rx), outcome(ry)
            if not met(rx) and (ox[0], normalised(ox[1])) != (oy[0], normalised(oy[1])):
                p, q = (x, y) if rng.random() < 0.5 else (y, x)
                verdict = self.runner.ask(["verdict", self.task.instance_id, x.id, y.id, number],
                                          self.verdict_payload(script, expected, base, rx if p is x else ry,
                                                               ry if p is x else rx), self.deadline)
                found = BEHAVIOUR.findall(verdict)
                said = found[-1].upper() if found else ""
                entry["verdict"] = said or "unreadable"
                if said in ("P", "Q"):
                    winner = p if said == "P" else q
                    entry.update({"winner": winner.id, "by": "outputs verdict"})
                    return winner
                entry["void"] = "outputs differ, the verdict named neither"
            else:
                entry["void"] = "both met the expectation" if met(rx) else "the same result with both fixes"
            void.append(f"- round {number + 1} (EXPECTED: {expected or 'not stated'}): "
                        f"unpatched exit {outcome(base)[0]}, output {ends(outcome(base)[1])!r}; "
                        f"fix X exit {ox[0]}, output {ends(ox[1])!r}; "
                        f"fix Y exit {oy[0]}, output {ends(oy[1])!r}")
        return None

    def compare(self, a: Any, b: Any) -> Optional[Tuple[float, float]]:
        """Version 1's `Compare`: the search first, version 1's diff judge when it cannot separate the pair."""
        record: Dict[str, Any] = {"a": a.id, "b": b.id, "rounds": []}
        self.pairs.append(record)
        winner = None
        try:
            winner = self.search(a, b, record)
        except v2.TaskTimeout:
            record["timed_out"] = True
        if winner is not None:
            record["decided_by"] = record["rounds"][-1]["by"]
            record["winner"] = winner.id
            return WIN if winner is a else (WIN[1], WIN[0])
        rated = self.runner.v1_judge(self.task.instance_id, a, b)
        record["decided_by"] = "diff judge"
        record["ratings"] = list(rated) if rated else None
        if rated and rated[0] != rated[1]:
            record["winner"] = a.id if rated[0] > rated[1] else b.id
        return rated


class Runner:
    """The model and the caches on disk under `--out`."""

    def __init__(self, args: argparse.Namespace, client: Any, model: str, rows: Dict[str, Dict[str, Any]],
                 night_check: Callable[[], List[str]] = night_units) -> None:
        self.args, self.client, self.model, self.rows, self.night_check = args, client, model, rows, night_check
        args.out.mkdir(parents=True, exist_ok=True)
        self.calls_path = args.out / "calls.jsonl"
        self.results_path = args.out / "containers.jsonl"
        self.calls = self.read_cache(self.calls_path, "answer")
        self.results = self.read_cache(self.results_path, "result")
        self.v1_judged: Dict[Tuple[str, str, str], Optional[Tuple[float, float]]] = {}
        if args.v1_judgments and args.v1_judgments.exists():
            for line in args.v1_judgments.read_text().splitlines():
                try:
                    record = json.loads(line)
                    self.v1_judged[(record["instance_id"], record["a"], record["b"])] = \
                        tuple(record["ratings"]) if record.get("ratings") else None
                except (ValueError, KeyError, TypeError):
                    continue
        self.v1_reused = 0

    @staticmethod
    def read_cache(path: Path, field: str) -> Dict[str, Any]:
        """A cache file: one `{"key": ..., field: ...}` per line; a torn last line is skipped."""
        cache: Dict[str, Any] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    record = json.loads(line)
                    cache[record["key"]] = record[field]
                except (ValueError, KeyError, TypeError):
                    continue
        return cache

    def check_night(self) -> None:
        """Raises `Paused` when a SWE-bench night is active."""
        active = self.night_check()
        if active:
            raise Paused(", ".join(active))

    def sampling(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """A chat payload with the measured sampling."""
        return {"model": self.model, "max_tokens": self.args.max_tokens, "temperature": self.args.temperature,
                "top_p": self.args.top_p, "messages": messages}

    def ask(self, key: List[Any], payload: Dict[str, Any], deadline: Optional[Any]) -> str:
        """One model call, cached under `key`; bounded by the task's deadline when there is one."""
        name = json.dumps(key)
        if name in self.calls:
            return self.calls[name]
        self.check_night()
        bound = deadline.bound(self.args.timeout) if deadline is not None else self.args.timeout
        if hasattr(self.client, "timeout"):
            self.client.timeout = bound
        answer = self.client.chat(payload)
        self.calls[name] = answer
        with open(self.calls_path, "a") as handle:
            handle.write(json.dumps({"key": name, "answer": answer, "model": self.model}) + "\n")
        return answer

    def store_result(self, key: str, result: Dict[str, Any]) -> None:
        """Keeps one container's result."""
        self.results[key] = result
        with open(self.results_path, "a") as handle:
            handle.write(json.dumps({"key": key, "result": result}) + "\n")

    def v1_judge(self, instance_id: str, a: Any, b: Any) -> Optional[Tuple[float, float]]:
        """Version 1's call on the diffs: from its judgments when it made it, else asked as it asked."""
        if (instance_id, a.id, b.id) in self.v1_judged:
            self.v1_reused += 1
            return self.v1_judged[(instance_id, a.id, b.id)]
        payload = self.sampling(NightShiftChooser.messages(str(self.rows[instance_id].get("problem_statement") or ""),
                                                           a.patch, b.patch))
        if self.args.seed is not None:
            payload["seed"] = self.args.seed
        return v1.ratings(self.ask(["v1 judge", instance_id, a.id, b.id], payload, None))


# --------------------------------------------------------------------------- the report


def task_stage(choice: Dict[str, Any], pairs: List[Dict[str, Any]]) -> str:
    """Version 3's stage: version 1's, with `judge` told apart by how its pairs were decided."""
    if not choice["stage"].startswith("judge"):
        return choice["stage"]
    ways = {pair["decided_by"] for pair in pairs}
    if ways <= {"expectation", "outputs verdict"}:
        return "differential"
    return "diff judge" if ways == {"diff judge"} else "differential + diff judge"


def pair_accuracy(task: Any, pairs: List[Dict[str, Any]], v1_judged: Dict[Tuple[str, str, str], Any]) -> List[Dict[str, Any]]:
    """
    For each pair whose candidates differ in the hidden verdict: who version 3 and version 1's judge
    picked, and whether each was right. Read after every choice is made (scoring only).
    """
    rows = []
    for pair in pairs:
        ra, rb = task.resolves.get(pair["a"]), task.resolves.get(pair["b"])
        if ra == rb:
            continue
        right = pair["a"] if ra else pair["b"]
        old = v1_judged.get((task.instance_id, pair["a"], pair["b"]))
        v1_winner = None if not old or old[0] == old[1] else (pair["a"] if old[0] > old[1] else pair["b"])
        rows.append({"instance_id": task.instance_id, "a": pair["a"], "b": pair["b"], "decided_by": pair["decided_by"],
                     "v3_right": pair.get("winner") == right if pair.get("winner") else None,
                     "v1_right": v1_winner == right if v1_winner else None})
    return rows


def print_report(summary: Dict[str, Any]) -> None:
    """The printed form of `summary.json`."""
    fmt = lambda value: "  - " if value is None else f"{value:4.2f}"
    print(f"\n{'task':<30} {'v1 stage':<20} {'v1':>4}  {'v3 stage':<26} {'v3':>4}")
    for row in summary["side_by_side"]:
        print(f"{row['instance_id']:<30} {str(row['v1_stage'] or '-'):<20} {fmt(row['v1'])}  "
              f"{row['v3_stage']:<26} {fmt(row['v3'])}")
    v1s = summary.get("v1") or {}
    print(f"\nOn the {summary['tasks']} task(s): v1 {v1s.get('chooser', '-')}, v3 {summary['chooser']}; the regression "
          f"filter alone {summary['filter_alone']}, a random run {summary['random_run']}, any run (oracle) {summary['oracle']}")
    print("Decided by (v3): " + ", ".join(f"{stage} {count}" for stage, count in sorted(summary["stages_v3"].items())))
    ways = summary["pairs_by"]
    print("Pairs: " + ", ".join(f"{way} {count}" for way, count in sorted(ways.items())) +
          f" (version 1's own answer reused for {summary['v1_reused']})")
    acc = summary["pair_accuracy"]
    print(f"Pairs whose candidates differ in the verdict: {acc['pairs']}; v3 right {acc['v3_right']} "
          f"(differential {acc['differential_right']}/{acc['differential']}), v1's judge right {acc['v1_right']}")
    print(f"On all {summary['listed']}: single runs {summary['single_runs']}; best-of-{len(summary['runs'])} "
          f"v1 {v1s.get('projected', '-')}, v3 {summary['projected']} (every run resolved {summary['all_resolved']})")
    if summary.get("timed_out"):
        print(f"Time cap reached: {', '.join(summary['timed_out'])}")
    if summary["flaky"]:
        print(f"Graded both ways for the same patch text (flaky tests): {', '.join(summary['flaky'])}")


def verdict(paths: Sequence[Path], out: Optional[Path]) -> int:
    """
    Version 3 against version 1 over several trios' summaries: `not_worse` when version 3 is at
    least version 1 on every trio and loses no task version 1 got, `better` when it also gains.
    """
    trios, lost, gained = {}, [], []
    for path in paths:
        summary = v1.read_json(Path(path))
        if not summary or not summary.get("v1"):
            raise SystemExit(f"❌ {path} is not a version 3 summary with version 1 beside it")
        name = ",".join(summary["runs"])
        trios[name] = {"v1": summary["v1"]["chooser"], "v3": summary["chooser"], "tasks": summary["tasks"],
                       "oracle": summary["oracle"], "summary": str(path)}
        for row in summary["side_by_side"]:
            if row["v1"] is None:
                continue
            if row["v3"] < row["v1"]:
                lost.append(row["instance_id"])
            elif row["v3"] > row["v1"]:
                gained.append(row["instance_id"])
    not_worse = all(t["v3"] >= t["v1"] for t in trios.values()) and not lost
    result = {"trios": trios, "lost": lost, "gained": gained, "not_worse": not_worse,
              "better": not_worse and bool(gained), "go": not_worse,
              "rule": "go = v3 >= v1 on every trio and no task lost (report-only use in a night)",
              "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    text = json.dumps(result, indent=2) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        staging = out.with_name(f".{out.name}.tmp")
        staging.write_text(text)
        staging.replace(out)
    print(text, end="")
    return 0


# --------------------------------------------------------------------------- main


def run_test(args: argparse.Namespace, client_factory: Callable[[str, float], Any] = None,
             night_check: Callable[[], List[str]] = night_units) -> int:
    """The test, as `main` runs it; the client and the night check are replaceable for the tests."""
    if args.verdict:
        return verdict([Path(p).expanduser() for p in args.verdict.split(",") if p], args.verdict_out)
    runs = [v1.load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    if len(runs) < 2:
        raise SystemExit("❌ Name at least two runs")
    tasks = v1.select_tasks(runs, args.all)
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t.instance_id in wanted]
    if args.limit:
        tasks = tasks[:args.limit]
    pools = {t.instance_id: len([c for c in t.candidates if not c.breaks] or t.candidates) for t in tasks}
    judged = [t for t in tasks if pools[t.instance_id] > 1]
    pairs = sum(pools[t.instance_id] * (pools[t.instance_id] - 1) // 2 for t in judged)
    images = {t.instance_id: v2.image_of(t.instance_id, runs, args.runs_dir) for t in judged}
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; {len(judged)} go to the comparison, "
          f"{pairs} pair(s), at most {pairs * args.rounds * 2} search call(s) and {pairs * args.rounds * 3} container(s)")
    for task in tasks:
        where = ""
        if task.instance_id in images:
            where = "[local] " if images[task.instance_id] and SweBenchDocker.image_digest(images[task.instance_id]) else "[to pull] "
        print(f"  {task.instance_id:<34} {where}" + ("; ".join(v1.describe(c) for c in task.candidates) or "no non-empty patch"))
    if args.dry_run:
        return 0
    if not args.server:
        raise SystemExit("❌ --server is required (this machine's model server through its gate: http://localhost:8000)")
    v1_summary = v1.read_json(args.v1_summary) if args.v1_summary else None
    if v1_summary and v1_summary.get("runs") != [r["name"] for r in runs]:
        print(f"⚠️  {args.v1_summary} was made from {v1_summary.get('runs')}: not comparable, left out")
        v1_summary = None
    if args.v1_judgments is None and args.v1_summary:
        args.v1_judgments = args.v1_summary.with_name("judgments.jsonl") if v1_summary else None
    if night_check():
        print(f"⏸️  A SWE-bench night is active ({', '.join(night_check())}): nothing asked, nothing run")
        return PAUSED
    client = (client_factory or v1.Client)(args.server, args.timeout)
    model = args.model or client.model()
    rows = v2.load_rows(args.dataset, [t.instance_id for t in judged])
    runner = Runner(args, client, model, rows, night_check)
    for stale in SweBenchDocker.run(["ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"], timeout=60).stdout.split():
        SweBenchDocker.run(["rm", "-f", stale], timeout=120)
    choices, scores, per_pairs, timed_out = {}, {}, {}, []
    for task in tasks:
        started = time.monotonic()
        chooser = TaskChooser(runner, task, images.get(task.instance_id))
        try:
            choice = NightShiftChooser.choose(task.candidates, chooser.compare,
                                              random.Random(f"{args.seed}:{task.instance_id}"), args.swiss_rounds)
        except Paused as paused:
            print(f"⏸️  A SWE-bench night became active ({paused}): stopped at {task.instance_id}; "
                  "run the same command again to go on")
            return PAUSED
        finally:
            chooser.close()
        choice["stage_v3"] = task_stage(choice, chooser.pairs)
        choice["pairs"] = chooser.pairs
        choice["seconds"] = round(time.monotonic() - started)
        choices[task.instance_id], scores[task.instance_id] = choice, v1.score(task, choice)
        per_pairs[task.instance_id] = chooser.pairs
        if any(p.get("timed_out") for p in chooser.pairs):
            timed_out.append(task.instance_id)
        print(f"  {task.instance_id:<34} {choice['stage_v3']}: chose {', '.join(choice['chosen']) or 'nothing'}, "
              f"credit {scores[task.instance_id]['chooser']:.2f} ({choice['seconds']} s)", flush=True)
    summary = v1.summarise(runs, tasks, scores, choices)
    previous = (v1_summary or {}).get("per_task") or {}
    side = [{"instance_id": t.instance_id, "v1_stage": (previous.get(t.instance_id) or {}).get("stage"),
             "v1": (previous.get(t.instance_id) or {}).get("chooser"), "v3_stage": choices[t.instance_id]["stage_v3"],
             "v3": scores[t.instance_id]["chooser"], "filter": scores[t.instance_id]["filter"],
             "random_run": scores[t.instance_id]["random_run"]} for t in tasks]
    v1_here = None
    if v1_summary and all(row["v1"] is not None for row in side):
        v1_total = round(sum(row["v1"] for row in side), 2)
        v1_here = {"chooser": v1_total, "projected": round(summary["projected"] - summary["chooser"] + v1_total, 2),
                   "summary": str(args.v1_summary)}
    stages: Dict[str, int] = {}
    for choice in choices.values():
        stages[choice["stage_v3"]] = stages.get(choice["stage_v3"], 0) + 1
    ways: Dict[str, int] = {}
    for pairs_of in per_pairs.values():
        for pair in pairs_of:
            ways[pair["decided_by"]] = ways.get(pair["decided_by"], 0) + 1
    accuracy = [row for t in tasks for row in pair_accuracy(t, per_pairs.get(t.instance_id) or [], runner.v1_judged)]
    differential = [row for row in accuracy if row["decided_by"] != "diff judge"]
    summary.update({
        "version": 3, "model": model, "server": args.server, "seed": args.seed, "rounds": args.rounds,
        "v1": v1_here, "side_by_side": side, "stages_v3": stages, "pairs_by": ways, "v1_reused": runner.v1_reused,
        "pair_accuracy": {"pairs": len(accuracy), "v3_right": sum(1 for r in accuracy if r["v3_right"]),
                          "v1_right": sum(1 for r in accuracy if r["v1_right"]),
                          "differential": len(differential),
                          "differential_right": sum(1 for r in differential if r["v3_right"]),
                          "rows": accuracy},
        "timed_out": timed_out,
        "per_task": {i: {**choices[i], **scores[i]} for i in choices},
    })
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print_report(summary)
    return 0


def parser() -> argparse.ArgumentParser:
    """The command line: version 2's containers and caps, version 1's judge, the search's rounds."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", help="the model server's URL (this machine's, through its gate: http://localhost:8000)")
    p.add_argument("--runs", default=v1.DEFAULT_RUNS, help="comma-separated run names (one arm's attempts)")
    p.add_argument("--runs-dir", type=Path, default=v1.DEFAULT_RUNS_DIR, help="where the runs are (read only)")
    p.add_argument("--dataset", type=Path, default=v1.DEFAULT_DATASET, help="the local dataset snapshot")
    p.add_argument("--out", type=Path, default=Path("swe-bench-chooser-v3"), help="where the caches and the summary go")
    p.add_argument("--v1-summary", type=Path, help="version 1's summary.json over the same runs, for the comparison")
    p.add_argument("--v1-judgments", type=Path,
                   help="version 1's judgments.jsonl (default: beside --v1-summary), reused for the pairs the search leaves")
    p.add_argument("--model", help="the model id (default: the server's /v1/models)")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--temperature", type=float, default=0.6)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--seed", type=int, help="pins the pairing, the order shown and the sampling")
    p.add_argument("--swiss-rounds", type=int, default=1, help="Swiss rounds after two comparisons each (v1)")
    p.add_argument("--rounds", type=int, default=ROUNDS, help=f"search rounds per pair before v1's judge (default {ROUNDS})")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for one answer")
    p.add_argument("--task-timeout", type=float, default=2400.0, help="seconds one task's search may take (default 40 min)")
    p.add_argument("--memory", default=v2.DEFAULT_TASK_MEMORY, help="each container's memory cap")
    p.add_argument("--cpus", type=int, default=v2.DEFAULT_TASK_CPUS, help="each container's CPUs")
    p.add_argument("--container-network", default="none", help="the containers' network (default none)")
    p.add_argument("--keep-images", action="store_true", help="keep the images this test pulled")
    p.add_argument("--all", action="store_true", help="every task, not only those where the runs disagree")
    p.add_argument("--only", help="comma-separated instance ids")
    p.add_argument("--limit", type=int, help="only the first N tasks")
    p.add_argument("--dry-run", action="store_true", help="list the tasks; contact no server, start no container")
    p.add_argument("--verdict", help="comma-separated version 3 summaries: print (and --verdict-out) v3 against v1")
    p.add_argument("--verdict-out", type=Path, help="where --verdict writes its JSON")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the test."""
    return run_test(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
