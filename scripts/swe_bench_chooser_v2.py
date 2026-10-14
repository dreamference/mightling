#!/usr/bin/env python3
"""The chooser test, version 2: a hybrid verifier, R2E-Gym's way, on the patches on disk.

Version 1 (``scripts/swe_bench_chooser_test.py``, backlog item 1) chose among the patches of
nights 2-4's default runs with the regression filter and a pairwise judge that saw only the
issue and two diffs. On the 44 tasks it scored 33 (single runs 29/29/31, any run 36); on the 13
tasks where the runs disagree, 10 against the filter's 9.33 and a random run's 6.67. The filter
was right 3 times out of 3, the judge 7 out of 10 (wrong on django 11477, 12663 and 13807).
Version 2 adds evidence from execution before the judge, as R2E-Gym's hybrid verifier does, and
runs its stages in this order:

1. **Reproduction tests.** The served model writes one to three tests from the issue text
   only: it is shown the issue, the repository's name and its version, and nothing else (no
   hidden test, no reference patch, no candidate patch, no hint; `writer_payload`). Each test is
   a standalone script, `python <test>` run from ``/testbed``, whose exit status is the verdict:
   0 when the behaviour the issue asks for is there. Standalone scripts, not test-suite files,
   because the tasks' repositories run their suites in different ways (Django's runtests.py,
   sympy's bin/test, pytest); a script is run the same way everywhere and lives outside
   ``/testbed``, where no reset can delete it. A test is kept only if it compiles and FAILS on
   the unpatched repository in the task's own container; when none is kept, the model is asked
   once more (``--generation-attempts``), told that its tests passed there. Every candidate is
   then run against the kept tests, each in a fresh container.
2. **Existing tests.** The regression filter as version 1 runs it: a candidate that broke a
   PASS_TO_PASS test in the grading logs on disk, or did not apply, is dropped; if every
   candidate breaks something, all go on.
3. **Groups by behaviour.** The survivors with the same pass/fail vector on the kept tests form
   one group. The group that passes the most reproduction tests is preferred, then the largest
   group, counted in runs (a patch two runs wrote alike counts twice: it is two samples).
4. **The judge, last.** Only when several candidates remain (tied groups, or a group of several
   distinct patches) is the judge called. It sees the issue, the kept tests, both diffs and each
   one's test results (exit status and output), and every pair is asked three times, the patch
   order swapped between the calls (A/B, B/A, A/B, each with its own seed); the majority of the
   three wins the pair. With three runs that is at most three pairs, nine calls.

Which stage decided each task is recorded: `only candidate`, `regression filter`,
`reproduction tests` (one candidate passed the most), `behaviour groups` (one largest group of
one patch), `judge`, `judge (no repro test)` (no test was kept, so stages 1 and 3 said nothing),
and `time cap` (the task's time ran out before a decision: a fair coin among the survivors).

**The chooser never sees whether a patch fixed the issue,** as in version 1: no FAIL_TO_PASS
result, no `resolved` flag, no hidden test. The environment set-up run before the tests is the
grader's own: the dataset row's `eval_script` up to (not including) the line that resets the
hidden tests' files (`setup_preamble`); the test patch that follows it is never run or shown.

Containers. One task at a time, one container at a time, from the image the harness grades in
(`SweBenchImages.image_for`), started as the image's own user (root, as the grader runs) with
version 1's per-task memory and CPU caps and **no network** (``--container-network``), never
the run network: the model is called from the host and never by a test. A candidate is applied
with the harness's own chain (`GIT_APPLY_CMDS`, `git apply`, then `--3way`, then `--reject`,
then `patch --fuzz=5`, the tree reset between attempts). Without a network the grader's
`pip install -e .` fails for most repositories (build isolation fetches setuptools); that is
recorded (`setup_exit`, `setup_output`) and harmless: the image's environment is installed
already, editable, and the tests run with ``/testbed`` first on ``PYTHONPATH``, so they import
the patched sources. Every container is removed when its
step ends, and an image this test pulled is removed with the harness's `SweBenchImages.remove`
when its task ends (``--keep-images`` keeps it). Each task has a wall-clock cap
(``--task-timeout``, default 40 min) that also bounds every docker call and model call in it.

Output, under ``--out`` (never under the runs, which are only read): ``tasks/<instance>.json``
(the generated tests, which were kept, every run's exit status and output, written step by step
so a rerun goes on from it), ``judgments.jsonl`` (every judge call), ``summary.json`` (version 1
and version 2 side by side, from version 1's ``--v1-summary``). Run the same command again after
an interruption: nothing done is redone, and no model call is repeated.

Runbook (about 2.5-3 hours, at most 4.5; NOT while a run needs the server)
--------------------------------------------------------------------------

1. Dry run here, no server and no container::

       .venv/bin/python scripts/swe_bench_chooser_v2.py --dry-run

   It lists the 13 tasks, their candidates and the filter's verdict, which images are already
   local, and the model calls at most (13 x 2 test-writer calls, 3 per judged pair).
2. A container smoke with the built-in fake model (no model contact) on one task::

       .venv/bin/python scripts/swe_bench_chooser_v2.py --fake-model --limit 1 \\
           --out /tmp/chooser-v2-smoke

3. Check the server is free (version 1's runbook, steps 2 and 3): no SWE-bench run, Night Shift
   task or ling-engine test may be using second-puffin's server; never `night pause` for this.
4. Run, with version 1's runs, seed and server, so the result compares with its 33/44::

       .venv/bin/python scripts/swe_bench_chooser_v2.py \\
           --server http://second-puffin.local:8000 \\
           --runs n2-default,n3-default,n4-default --seed 20261013 \\
           --v1-summary ~/swe-bench-chooser-2026-10/summary.json \\
           --out ~/swe-bench-chooser-v2-2026-10

Time, measured with the fake model on 2026-10-14: pulling a task's image and running its four
containers took 40-76 s (django 11433, astropy 14369, sphinx 9673, sympy 18698), so the
containers come to about 15 min for the 13 tasks. The model calls dominate: version 1 made 26
judge calls in about an hour, about 2.3 min each. Here 13 test-writer calls (plus one more for
each task where no test is kept: say 4) at 2-3 min is 35-50 min, and the judge, called only on
ties, 3 calls per pair: at most 78 calls (the 26 pairs of version 1) at about 2.5 min, about
3.3 h if every judged task still ties, about 1.5 h if half do. Expected 2.5-3 h, at most about
4.5 h; never more than 13 x 40 min (the cap).

Tests: ``tests/test_swe_bench_chooser_v2.py`` (fake docker on scratch git repositories, a fake
server on loopback).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

SCRIPTS: Final[Path] = Path(__file__).resolve().parent
if str(SCRIPTS.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPTS.parent))

from dreamference.swe_bench.swe_bench_docker import SweBenchDocker  # noqa: E402
from dreamference.swe_bench.swe_bench_harness import FORBIDDEN_FIELDS  # noqa: E402
from dreamference.swe_bench.swe_bench_images import SweBenchImages  # noqa: E402
from dreamference.swe_bench.swe_bench_instance_run import CONTAINER_PATH  # noqa: E402
from dreamference.swe_bench.swe_bench_settings import DEFAULT_TASK_CPUS, DEFAULT_TASK_MEMORY  # noqa: E402


def _load_v1() -> Any:
    """Version 1's module, beside this one: its run loading, filter, client and scoring are reused."""
    name = "swe_bench_chooser_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "swe_bench_chooser_test.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    return module


v1 = _load_v1()
Candidate = v1.Candidate
Task = v1.Task

DEFAULT_V1_SUMMARY: Final[Path] = Path.home() / "swe-bench-chooser-2026-10" / "summary.json"

# The upstream harness's patch chain (swebench `run_evaluation.GIT_APPLY_CMDS`, the pinned
# version), the tree reset between attempts included.
GIT_APPLY_CMDS: Final[Tuple[str, ...]] = (
    "git apply --verbose",
    "git apply --verbose --3way",
    "git apply --verbose --reject",
    "patch --batch --forward --fuzz=5 -p1 -i",
)

MAX_TESTS: Final[int] = 3
MAX_TEST_CHARS: Final[int] = 20_000
TEST_TIMEOUT_S: Final[int] = 120          # one test script
SETUP_TIMEOUT_S: Final[int] = 900         # the grader's set-up (pip install -e . for some)
START_TIMEOUT_S: Final[int] = 300         # docker run of a local image
OUTPUT_KEPT_CHARS: Final[int] = 4_000     # a test's output, as kept and as the judge sees it
JUDGE_QUERIES: Final[int] = 3
CONTAINER_LABEL: Final[str] = "ling.swe-bench.chooser=v2"
MARK: Final[str] = "@@CHOOSER"

WRITER_SYSTEM: Final[str] = (
    "You write reproduction tests for bug reports in Python repositories. A reproduction test "
    "fails while the reported problem is there and passes once it is fixed. You cannot run "
    "anything: write the tests from the issue alone."
)

WRITER_TEMPLATE: Final[str] = """The repository {repo} (version {version}) is checked out at /testbed with its dependencies installed; `python` is the repository's interpreter. A user filed this issue:

<issue>
{issue}
</issue>

Write between 1 and 3 reproduction tests for this issue. Each test is a standalone Python script that:
- is run as `python <script>` with /testbed as the working directory, with no arguments, no network and nothing else installed;
- exits with status 0 when the behaviour the issue asks for is present, and with a non-zero status (a failed assert or an uncaught exception) while the issue's problem is still there;
- checks what the issue describes through the repository's own behaviour, and does not depend on how a fix is written (for example, the exact wording of a new message);
- sets up what the library needs by itself (for Django: `settings.configure(...)` with only the apps it needs, then `django.setup()`, and an in-memory SQLite database if it needs one);
- writes nothing inside /testbed (use the `tempfile` module) and finishes in well under a minute.
{feedback}
Give each test as its own fenced ```python code block, and put nothing else in code blocks."""

RETRY_FEEDBACK: Final[str] = """
Your earlier tests were run on the repository as it is, with the problem still there, and none of them failed, so none reproduces the issue. Their results:
{results}
Write new tests that fail on the repository as it is."""

JUDGE_SYSTEM: Final[str] = (
    "You are an expert code reviewer. Compare two solutions to a programming problem and rate their "
    "correctness. The problem is a GitHub issue in a Python repository; each solution is a patch "
    "(a diff) written to fix it. Both were run against reproduction tests written from the issue "
    "alone, which fail on the unpatched repository; the tests may themselves be wrong or too strict, "
    "so weigh their results against the issue. A correct patch fixes the behaviour the issue "
    "describes and breaks no existing behaviour. If both look correct, prefer the more robust and "
    "fault-tolerant one. Ignore style, comments and the tests a patch adds."
)

JUDGE_TEMPLATE: Final[str] = """<issue>
{issue}
</issue>

<reproduction_tests>
{tests}
</reproduction_tests>

<solution_A>
{a}
</solution_A>

<test_results_A>
{results_a}
</test_results_A>

<solution_B>
{b}
</solution_B>

<test_results_B>
{results_b}
</test_results_B>

Rate the correctness of each solution from 1 (wrong) to 10 (certainly correct). End your answer with exactly these two lines:
RATING_A: <1-10>
RATING_B: <1-10>"""

PYTHON_FENCES: Final[Tuple[str, ...]] = ("", "python", "py", "python3")


class TaskTimeout(Exception):
    """The task's time cap was reached."""


class Deadline:
    """A task's wall-clock cap."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.end = clock() + seconds

    def left(self) -> float:
        """Seconds left; negative once past."""
        return self.end - self.clock()

    def bound(self, seconds: float) -> float:
        """
        A step's own timeout, cut to what the task has left.

        Raises:
            TaskTimeout: When nothing is left.
        """
        left = self.left()
        if left <= 1:
            raise TaskTimeout()
        return min(seconds, left)


# --------------------------------------------------------------------------- the test writer


def writer_payload(row: Dict[str, Any], model: str, args: argparse.Namespace, attempt: int,
                   feedback: str = "") -> Dict[str, Any]:
    """
    The test writer's request: the issue, the repository and its version, nothing else.

    Args:
        row: The task's dataset row (only `problem_statement`, `repo` and `version` are read).
        model: The served model's id.
        args: The command line (sampling and seed).
        attempt: 0 for the first request; the seed moves with it.
        feedback: What the earlier attempt's tests did on the unpatched repository.

    Returns:
        Dict[str, Any]: The chat completion payload.
    """
    user = WRITER_TEMPLATE.format(repo=row.get("repo") or "", version=row.get("version") or "",
                                  issue=v1.clip(str(row.get("problem_statement") or ""), v1.MAX_ISSUE_CHARS),
                                  feedback=feedback)
    payload = {"model": model, "max_tokens": args.max_tokens, "temperature": args.temperature,
               "top_p": args.top_p,
               "messages": [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": user}]}
    if args.seed is not None:
        payload["seed"] = args.seed + 1000 * attempt
    return payload


def parse_tests(answer: str) -> List[str]:
    """
    The tests in a test writer's answer: its fenced Python blocks, at most three, each cut to
    `MAX_TEST_CHARS`. Blocks marked as another language are not tests.
    """
    tests: List[str] = []
    language: Optional[str] = None
    block: List[str] = []
    for line in answer.splitlines():
        fence = line.strip()
        if language is None and fence.startswith("```"):
            language, block = fence[3:].strip().lower(), []
        elif language is not None and fence == "```":
            if language in PYTHON_FENCES and "\n".join(block).strip():
                tests.append("\n".join(block).strip() + "\n")
            language = None
        elif language is not None:
            block.append(line)
    return [test[:MAX_TEST_CHARS] for test in tests[:MAX_TESTS]]


def retry_feedback(tests: List[Dict[str, Any]]) -> str:
    """What the second request is told about the first one's tests."""
    results = "\n".join(f"- test {n + 1}: exit status {t.get('base_exit')}, output: "
                        f"{v1.clip(str(t.get('base_output') or '').strip(), 600) or '(none)'}"
                        for n, t in enumerate(tests))
    return RETRY_FEEDBACK.format(results=results or "- (no test could be read from the answer)")


# --------------------------------------------------------------------------- the container


def setup_preamble(row: Dict[str, Any]) -> str:
    """
    The grader's environment set-up for a task: the row's `eval_script` up to the line that resets
    the hidden tests' files (`git checkout <base_commit> <files>`) or applies the test patch,
    whichever comes first, without the lines that only print (`git status`, `git show`, `git
    diff`). The hidden tests are after that line and never reach a container.

    Returns:
        str: Shell lines; the conda activation alone when the row has no eval script.
    """
    script = str(row.get("eval_script") or "")
    base = str(row.get("base_commit") or "")
    kept: List[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if (base and stripped.startswith(f"git checkout {base}")) or stripped.startswith("git apply"):
            break
        if (stripped.startswith("#!") or stripped.startswith("set -") or stripped in ("git status", "git show")
                or (stripped.startswith("git ") and " diff" in stripped)):
            continue
        kept.append(line)
    if not any(line.strip() for line in kept):
        kept = ["source /opt/miniconda3/bin/activate", "conda activate testbed"]
    return "\n".join(kept)


def _file(path: str, text: str) -> str:
    """A shell line that writes `text` to `path` (base64, so no quoting can break it)."""
    return f"echo '{base64.b64encode(text.encode()).decode()}' | base64 -d > {path}"


def container_script(preamble: str, tests: List[str], patch: Optional[str]) -> str:
    """
    What one container runs, fed to `bash -s`: the candidate applied by the harness's chain (none
    for the unpatched repository), the grader's set-up, then each test with its own timeout. The
    results come back on standard output between `@@CHOOSER` marks.
    """
    lines = ["set +eu", 'TESTBED="${TESTBED:-/testbed}"', 'WORK="${WORK:-/tmp/chooser-v2}"',
             'mkdir -p "$WORK"', 'cd "$TESTBED" || exit 3']
    for number, test in enumerate(tests, 1):
        lines.append(_file(f'"$WORK/test_{number}.py"', test))
    if patch is not None:
        commands = " ".join(f"'{command}'" for command in GIT_APPLY_CMDS)
        lines += [
            _file('"$WORK/candidate.diff"', patch),
            f"commands=({commands})", "applied=''",
            'for i in "${!commands[@]}"; do',
            '  if [ "$i" -gt 0 ]; then git checkout -- . >/dev/null 2>&1; git clean -fd >/dev/null 2>&1; fi',
            '  if ${commands[$i]} "$WORK/candidate.diff" >> "$WORK/apply.log" 2>&1; then applied="${commands[$i]}"; break; fi',
            "done",
            'if [ -z "$applied" ] && git apply --check --reverse "$WORK/candidate.diff" >/dev/null 2>&1; then '
            'applied="already applied"; fi',
            f'if [ -z "$applied" ]; then echo "{MARK} apply failed"; tail -c 1500 "$WORK/apply.log"; exit 0; fi',
            f'echo "{MARK} apply $applied"',
        ]
    lines += ["{", preamble, '} > "$WORK/setup.log" 2>&1 < /dev/null', 'code=$?',
              f'echo "{MARK} setup output"', 'tail -c 1500 "$WORK/setup.log"; echo', f'echo "{MARK} setup $code"',
              'cd "$TESTBED" || exit 3']
    for number in range(1, len(tests) + 1):
        test = f'"$WORK/test_{number}.py"'
        lines += [
            f'python -m py_compile {test} > "$WORK/compile.log" 2>&1; echo "{MARK} test {number} compile $?"',
            # The repository first on the path, as for a script run from /testbed itself.
            f'PYTHONPATH="$TESTBED${{PYTHONPATH:+:$PYTHONPATH}}" timeout -k 5 {TEST_TIMEOUT_S} python {test} '
            '< /dev/null > "$WORK/out.log" 2>&1; code=$?',
            f'echo "{MARK} test {number} output"',
            f'tail -c {OUTPUT_KEPT_CHARS} "$WORK/out.log"; echo',
            f'echo "{MARK} test {number} exit $code"',
        ]
    return "\n".join(lines) + "\n"


def parse_container(stdout: str, count: int) -> Dict[str, Any]:
    """
    Reads `container_script`'s output.

    Returns:
        Dict[str, Any]: `applied` (the command that applied the patch, `None` when it did not
        apply, `""` when there was no patch), `setup` (its exit status), and `tests`: per test,
        `compiled`, `exit` (None when it did not run) and `output`.
    """
    result: Dict[str, Any] = {"applied": "", "setup": None, "setup_output": "",
                              "tests": [{"compiled": False, "exit": None, "output": ""} for _ in range(count)]}
    current: Optional[int] = None
    collected: List[str] = []
    for line in stdout.splitlines():
        if not line.startswith(MARK):
            if current is not None:
                collected.append(line)
            continue
        words = line[len(MARK):].split()
        if words[:2] == ["apply", "failed"]:
            result["applied"] = None
        elif words[:1] == ["apply"]:
            result["applied"] = " ".join(words[1:]) or "applied"
        elif words[:2] == ["setup", "output"]:
            current, collected = 0, []
        elif words[:1] == ["setup"] and len(words) > 1:
            result["setup"] = int(words[1]) if words[1].lstrip("-").isdigit() else None
            result["setup_output"] = "\n".join(collected).strip()
            current = None
        elif words[:1] == ["test"] and len(words) >= 3 and words[1].isdigit() and 1 <= int(words[1]) <= count:
            entry = result["tests"][int(words[1]) - 1]
            if words[2] == "compile" and len(words) > 3:
                entry["compiled"] = words[3] == "0"
            elif words[2] == "output":
                current, collected = int(words[1]), []
            elif words[2] == "exit" and len(words) > 3:
                entry["output"] = "\n".join(collected).strip()[-OUTPUT_KEPT_CHARS:]
                entry["exit"] = int(words[3]) if words[3].lstrip("-").isdigit() else None
                current = None
    return result


class Containers:
    """The task's containers: started one at a time, each removed when its step ends."""

    def __init__(self, image: str, instance_id: str, args: argparse.Namespace) -> None:
        self.image = image
        self.prefix = "ling-chooser-v2-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", instance_id)
        self.args = args
        self.count = 0

    def run(self, preamble: str, tests: List[str], patch: Optional[str], deadline: Deadline) -> Dict[str, Any]:
        """
        One fresh container from the task's image: apply `patch` (None: the unpatched repository),
        set up as the grader does, run the tests, remove the container.

        Raises:
            TaskTimeout: When the task's time ran out (the container is removed all the same).
        """
        self.count += 1
        name = f"{self.prefix}-{self.count}"
        SweBenchDocker.run(["rm", "-f", name], timeout=120)
        try:
            started = SweBenchDocker.run(
                ["run", "-d", "--init", "--name", name, "--label", CONTAINER_LABEL,
                 "--network", self.args.container_network,
                 "--memory", self.args.memory, "--memory-swap", self.args.memory,
                 "--cpus", str(self.args.cpus), "--pids-limit", "4096",
                 "-e", f"PATH={CONTAINER_PATH}", "-w", "/testbed", self.image, "sleep", "infinity"],
                timeout=deadline.bound(START_TIMEOUT_S))
            if started.returncode == 124:
                raise TaskTimeout()
            if started.returncode != 0:
                return {"error": f"docker run failed: {started.stderr.strip()[-300:]}", "applied": None,
                        "setup": None, "tests": []}
            budget = SETUP_TIMEOUT_S + len(tests) * (TEST_TIMEOUT_S + 15) + 120
            executed = SweBenchDocker.run(["exec", "-i", name, "bash", "-s"], timeout=deadline.bound(budget),
                                          input_text=container_script(preamble, tests, patch))
            if executed.returncode == 124 and deadline.left() <= 1:
                raise TaskTimeout()
            result = parse_container(executed.stdout or "", len(tests))
            if patch is None:
                result["applied"] = ""
            if executed.returncode not in (0,) and not any(t["exit"] is not None for t in result["tests"]):
                result["error"] = f"docker exec exited {executed.returncode}: {(executed.stderr or '').strip()[-300:]}"
            return result
        finally:
            SweBenchDocker.run(["rm", "-f", name], timeout=120)


# --------------------------------------------------------------------------- the decision


def vector_of(result: Dict[str, Any], kept: List[int]) -> List[bool]:
    """A run's pass/fail vector on the kept tests (a patch that did not apply fails them all)."""
    if result.get("applied") is None or result.get("error"):
        return [False] * len(kept)
    tests = result.get("tests") or []
    return [index < len(tests) and tests[index].get("exit") == 0 for index in kept]


def judge_pair(a: Any, b: Any, ask: Callable[[Any, Any, int], Optional[Tuple[float, float]]],
               order_rng: random.Random) -> Dict[str, Any]:
    """
    One pair, asked `JUDGE_QUERIES` times with the order swapped between calls; the majority wins.

    Args:
        a, b: The candidates.
        ask: One judge call: (shown as A, shown as B, query number) -> their ratings, or None
            when the answer is unreadable.
        order_rng: Which of the two is shown first in the first call.

    Returns:
        Dict[str, Any]: `votes` (candidate id to calls won), `winner` (None for a tie), `calls`
        (per call: who was A, who was B, the ratings).
    """
    first, second = (a, b) if order_rng.random() < 0.5 else (b, a)
    votes = {a.id: 0, b.id: 0}
    calls = []
    for query in range(JUDGE_QUERIES):
        shown_a, shown_b = (first, second) if query % 2 == 0 else (second, first)
        rated = ask(shown_a, shown_b, query)
        calls.append({"a": shown_a.id, "b": shown_b.id, "ratings": list(rated) if rated else None})
        if rated and rated[0] != rated[1]:
            votes[(shown_a if rated[0] > rated[1] else shown_b).id] += 1
    winner = None if votes[a.id] == votes[b.id] else max(votes, key=votes.get)
    return {"votes": votes, "winner": winner, "calls": calls}


def decide(candidates: List[Any], vectors: Optional[Dict[str, List[bool]]],
           ask: Callable[[Any, Any, int], Optional[Tuple[float, float]]], rng: random.Random,
           timed_out: bool = False) -> Dict[str, Any]:
    """
    Version 2's choice, the stages in order. Given no verdict on the issue.

    Args:
        candidates: The task's distinct non-empty patches.
        vectors: Candidate id to its pass/fail vector on the kept tests; None when the tests
            could not be run (the image could not be had), empty vectors when no test was kept.
        ask: One judge call (see `judge_pair`); may raise `TaskTimeout`.
        rng: The judge's pair order.
        timed_out: The task's time ran out before its tests finished.

    Returns:
        Dict[str, Any]: `stage`, `survivors` (the filter's), `groups`, `pool` (the candidates
        still tied before the judge), `pairs` (the judge's), `points` and `chosen`.
    """
    choice: Dict[str, Any] = {"stage": "none", "survivors": [], "groups": [], "pool": [], "pairs": [],
                              "points": {}, "chosen": []}
    if not candidates:
        return choice
    if len(candidates) == 1:
        only = [candidates[0].id]
        return dict(choice, stage="only candidate", survivors=only, pool=only, chosen=only)
    survivors = [c for c in candidates if not c.breaks]
    choice["survivors"] = [c.id for c in survivors]
    if len(survivors) == 1:
        return dict(choice, stage="regression filter", pool=choice["survivors"], chosen=choice["survivors"])
    pool = survivors or candidates
    if timed_out:
        ids = [c.id for c in pool]
        return dict(choice, stage="time cap", pool=ids, chosen=ids)
    no_tests = vectors is None or not any(vectors.get(c.id) for c in pool)
    if not no_tests:
        groups: Dict[Tuple[bool, ...], List[Any]] = {}
        for candidate in pool:
            groups.setdefault(tuple(vectors.get(candidate.id) or ()), []).append(candidate)
        size = lambda members: sum(len(c.runs) for c in members)
        choice["groups"] = [{"vector": [int(v) for v in key], "passes": sum(key), "runs": size(members),
                             "candidates": [c.id for c in members]}
                            for key, members in sorted(groups.items(), key=lambda kv: (-sum(kv[0]), -size(kv[1])))]
        most = max(sum(key) for key in groups)
        top = [members for key, members in groups.items() if sum(key) == most]
        if sum(len(members) for members in top) == 1:
            ids = [top[0][0].id]
            return dict(choice, stage="reproduction tests", pool=ids, chosen=ids)
        largest = max(size(members) for members in top)
        top = [members for members in top if size(members) == largest]
        pool = [c for members in top for c in members]
        if len(pool) == 1:
            return dict(choice, stage="behaviour groups", pool=[pool[0].id], chosen=[pool[0].id])
    choice["pool"] = sorted(c.id for c in pool)
    stage = "judge (no repro test)" if no_tests else "judge"
    points = {c.id: 0.0 for c in pool}
    ordered = sorted(pool, key=lambda c: c.id)
    try:
        for index, a in enumerate(ordered):
            for b in ordered[index + 1:]:
                pair = judge_pair(a, b, ask, rng)
                choice["pairs"].append(pair)
                if pair["winner"] is None:
                    points[a.id] += 0.5
                    points[b.id] += 0.5
                else:
                    points[pair["winner"]] += 1.0
    except TaskTimeout:
        return dict(choice, stage="time cap", chosen=choice["pool"])
    top_points = max(points.values())
    return dict(choice, stage=stage, points=points, chosen=sorted(i for i, p in points.items() if p == top_points))


def score(task: Any, choice: Dict[str, Any]) -> Dict[str, float]:
    """Version 1's scores, plus `tests_alone`: the share of the candidates still tied before the judge that resolve."""
    scores = v1.score(task, choice)
    pool = choice.get("pool") or choice.get("survivors") or [c.id for c in task.candidates]
    scores["tests_alone"] = sum(1 for i in pool if task.resolves.get(i)) / len(pool) if pool else 0.0
    return scores


# --------------------------------------------------------------------------- the task


class FakeClient:
    """The built-in stand-in for the model (``--fake-model``): no network, canned answers."""

    TESTS: Final[str] = (
        "```python\nimport sys\nsys.exit(1)  # fails everywhere: kept, and every candidate fails it\n```\n"
        "```python\nprint('passes on the unpatched repository: dropped')\n```\n"
        "```python\nimport subprocess, sys\n"
        "changed = subprocess.run(['git', 'diff', '--quiet']).returncode != 0\n"
        "print('tree changed' if changed else 'tree as checked out')\nsys.exit(0 if changed else 1)\n```\n"
    )

    def __init__(self, *args: Any) -> None:
        self.calls = 0

    def model(self) -> str:
        return "fake-model"

    def chat(self, payload: Dict[str, Any]) -> str:
        self.calls += 1
        if payload["messages"][0]["content"] == WRITER_SYSTEM:
            return self.TESTS
        text = payload["messages"][1]["content"]
        rate = lambda tag: 1 + int(hashlib.sha256(text.split(f"<{tag}>")[1].encode()).hexdigest(), 16) % 10
        return f"RATING_A: {rate('solution_A')}\nRATING_B: {rate('solution_B')}"


class Runner:
    """Runs the test over the tasks, keeping every step on disk under `--out`."""

    def __init__(self, args: argparse.Namespace, client: Any, model: str, rows: Dict[str, Dict[str, Any]]) -> None:
        self.args, self.client, self.model, self.rows = args, client, model, rows
        self.tasks_dir = args.out / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.log = args.out / "judgments.jsonl"
        self.judged: Dict[Tuple[str, str, str, int], Optional[Tuple[float, float]]] = {}
        if self.log.exists():
            for line in self.log.read_text().splitlines():
                try:
                    record = json.loads(line)
                    self.judged[(record["instance_id"], record["a"], record["b"], int(record["query"]))] = \
                        tuple(record["ratings"]) if record.get("ratings") else None
                except (ValueError, KeyError, TypeError):
                    continue

    # -- the record --------------------------------------------------------------------------

    def record_path(self, instance_id: str) -> Path:
        return self.tasks_dir / f"{instance_id}.json"

    def load(self, instance_id: str) -> Dict[str, Any]:
        return v1.read_json(self.record_path(instance_id)) or {"instance_id": instance_id}

    def save(self, record: Dict[str, Any]) -> None:
        path = self.record_path(record["instance_id"])
        staging = path.with_name(f".{path.name}.tmp")
        staging.write_text(json.dumps(record, indent=2) + "\n")
        staging.replace(path)

    # -- the model ---------------------------------------------------------------------------

    def chat(self, payload: Dict[str, Any], deadline: Deadline) -> str:
        """One model call bounded by the task's time; a refusal stops the test, as in version 1."""
        if hasattr(self.client, "timeout"):
            self.client.timeout = deadline.bound(self.args.timeout)
        else:
            deadline.bound(self.args.timeout)
        try:
            return self.client.chat(payload)
        except SystemExit:
            if deadline.left() <= 1:
                raise TaskTimeout()
            raise

    def judge_for(self, task: Any, record: Dict[str, Any], deadline: Deadline) -> Callable[[Any, Any, int], Optional[Tuple[float, float]]]:
        """The judge call of one task: issue, kept tests, two diffs and their test results."""
        issue = v1.clip(str(self.rows[task.instance_id].get("problem_statement") or ""), v1.MAX_ISSUE_CHARS)
        kept = record.get("kept") or []
        tests = record.get("tests") or []
        shown_tests = "\n\n".join(f"test_{n + 1}.py:\n{tests[i]['code']}" for n, i in enumerate(kept)) \
            or "(no reproduction test failed on the unpatched repository, so none is shown)"

        def results(candidate_id: str) -> str:
            run = (record.get("candidates") or {}).get(candidate_id) or {}
            if run.get("applied") is None:
                return "The patch did not apply."
            if not kept:
                return "(no tests)"
            lines = []
            for n, i in enumerate(kept):
                entry = (run.get("tests") or [{}] * (i + 1))[i] if i < len(run.get("tests") or []) else {}
                status = "PASSED" if entry.get("exit") == 0 else f"FAILED (exit status {entry.get('exit')})"
                lines.append(f"test_{n + 1}.py: {status}\n{v1.clip(str(entry.get('output') or ''), OUTPUT_KEPT_CHARS)}")
            return "\n\n".join(lines)

        def ask(a: Any, b: Any, query: int) -> Optional[Tuple[float, float]]:
            key = (task.instance_id, a.id, b.id, query)
            if key in self.judged:
                return self.judged[key]
            payload = {"model": self.model, "max_tokens": self.args.max_tokens, "temperature": self.args.temperature,
                       "top_p": self.args.top_p,
                       "messages": [{"role": "system", "content": JUDGE_SYSTEM},
                                    {"role": "user", "content": JUDGE_TEMPLATE.format(
                                        issue=issue, tests=shown_tests,
                                        a=v1.clip(a.patch, v1.MAX_PATCH_CHARS), results_a=results(a.id),
                                        b=v1.clip(b.patch, v1.MAX_PATCH_CHARS), results_b=results(b.id))}]}
            if self.args.seed is not None:
                payload["seed"] = self.args.seed + query
            answer = self.chat(payload, deadline)
            self.judged[key] = v1.ratings(answer)
            with open(self.log, "a") as handle:
                handle.write(json.dumps({"instance_id": task.instance_id, "a": a.id, "b": b.id, "query": query,
                                         "ratings": self.judged[key], "model": self.model,
                                         "answer_tail": answer[-600:]}) + "\n")
            return self.judged[key]

        return ask

    # -- stage 1 -----------------------------------------------------------------------------

    def run_tests(self, task: Any, record: Dict[str, Any], image: str, deadline: Deadline) -> None:
        """Writes, checks and runs the reproduction tests, saving after every step."""
        row = self.rows[task.instance_id]
        containers = Containers(image, task.instance_id, self.args)
        generations = record.setdefault("generations", [])
        for attempt in range(self.args.generation_attempts):
            if attempt == len(generations):
                feedback = retry_feedback(record.get("tests") or []) if attempt else ""
                answer = self.chat(writer_payload(row, self.model, self.args, attempt, feedback), deadline)
                generations.append({"attempt": attempt, "tests": parse_tests(answer), "answer_tail": answer[-1500:]})
                self.save(record)
            generation = generations[attempt]
            if generation.get("base") is None:
                generation["base"] = containers.run(row["setup"], generation["tests"], None, deadline) \
                    if generation["tests"] else {"applied": "", "setup": None, "tests": []}
                self.save(record)
            base_tests = generation["base"].get("tests") or []
            record["tests"] = []
            for number, code in enumerate(generation["tests"]):
                ran = base_tests[number] if number < len(base_tests) else {}
                record["tests"].append({"code": code, "base_compiled": bool(ran.get("compiled")),
                                        "base_exit": ran.get("exit"), "base_output": ran.get("output") or ""})
            # Kept: compiles, and fails (a timeout too) on the repository as it is.
            record["kept"] = [n for n, t in enumerate(record["tests"])
                              if t["base_compiled"] and t["base_exit"] not in (None, 0)]
            # The grader's set-up may fail here (no network for `pip install -e .`); the image's
            # environment is already installed, editable, so the tests still see the patch.
            record["setup_exit"] = generation["base"].get("setup")
            record["setup_output"] = str(generation["base"].get("setup_output") or "")[-1500:]
            self.save(record)
            if record["kept"]:
                break
        kept_code = [record["tests"][i]["code"] for i in record["kept"]]
        runs = record.setdefault("candidates", {})
        for candidate in task.candidates:
            if candidate.id in runs:
                continue
            if not kept_code:
                runs[candidate.id] = {"applied": "", "tests": [], "skipped": "no kept test"}
                continue
            result = containers.run(row["setup"], kept_code, candidate.patch, deadline)
            # Indexed like `record["tests"]`, so a kept test has the same position everywhere.
            spread: List[Dict[str, Any]] = [{} for _ in record["tests"]]
            for position, index in enumerate(record["kept"]):
                if position < len(result.get("tests") or []):
                    spread[index] = result["tests"][position]
            runs[candidate.id] = dict(result, tests=spread)
            self.save(record)
        record["tests_done"] = True
        self.save(record)

    # -- one task ----------------------------------------------------------------------------

    def run_task(self, task: Any, image: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        One task, bounded by `--task-timeout`: stage 1 in containers, then the decision.

        Returns:
            Tuple[Dict[str, Any], Dict[str, Any]]: The task's record and the choice.
        """
        record = self.load(task.instance_id)
        started = time.monotonic()
        deadline = Deadline(self.args.task_timeout)
        timed_out = False
        if not record.get("tests_done") and not record.get("timed_out"):
            if image is None:
                record["note"] = "no arm64 image for this task"
            else:
                pulled = SweBenchDocker.image_digest(image) is None
                try:
                    if not SweBenchDocker.ensure_image(image):
                        record["note"] = f"could not pull {image}"
                    else:
                        record["image"] = image
                        self.run_tests(task, record, image, deadline)
                except TaskTimeout:
                    timed_out = True
                    record["timed_out"] = True
                finally:
                    if pulled and not self.args.keep_images:
                        SweBenchImages.remove([image])
            record["test_seconds"] = round(time.monotonic() - started)
            self.save(record)
        timed_out = timed_out or bool(record.get("timed_out"))
        kept = record.get("kept") or []
        vectors = None
        if record.get("tests_done"):
            vectors = {cid: vector_of(run, kept) for cid, run in (record.get("candidates") or {}).items()}
        choice = decide(task.candidates, vectors, self.judge_for(task, record, deadline),
                        random.Random(f"{self.args.seed}:{task.instance_id}"), timed_out=timed_out)
        choice["seconds"] = round(time.monotonic() - started)
        choice["kept_tests"] = len(kept)
        choice["vectors"] = {cid: [int(v) for v in vec] for cid, vec in (vectors or {}).items()}
        return record, choice


# --------------------------------------------------------------------------- the report


def side_by_side(tasks: List[Any], choices: Dict[str, Dict[str, Any]], scores: Dict[str, Dict[str, float]],
                 v1_summary: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per task: version 1's stage and credit (from its summary) beside version 2's."""
    previous = (v1_summary or {}).get("per_task") or {}
    rows = []
    for task in tasks:
        old = previous.get(task.instance_id) or {}
        rows.append({"instance_id": task.instance_id, "v1_stage": old.get("stage"), "v1": old.get("chooser"),
                     "v2_stage": choices[task.instance_id]["stage"], "v2": scores[task.instance_id]["chooser"],
                     "tests_alone": scores[task.instance_id]["tests_alone"],
                     "filter": scores[task.instance_id]["filter"], "random_run": scores[task.instance_id]["random_run"],
                     "kept_tests": choices[task.instance_id].get("kept_tests", 0)})
    return rows


def by_stage(rows: List[Dict[str, Any]], version: str) -> Dict[str, Dict[str, float]]:
    """Per stage that decided: how many tasks, and how many of them it got right (expected)."""
    stages: Dict[str, Dict[str, float]] = {}
    for row in rows:
        stage = row[f"{version}_stage"]
        if stage is None or row[version] is None:
            continue
        entry = stages.setdefault(stage, {"tasks": 0, "right": 0.0})
        entry["tasks"] += 1
        entry["right"] = round(entry["right"] + row[version], 2)
    return stages


def print_report(summary: Dict[str, Any]) -> None:
    """The printed form of `summary.json`."""
    rows = summary["side_by_side"]
    print(f"\n{'task':<30} {'v1 stage':<20} {'v1':>4}  {'v2 stage':<22} {'v2':>4} {'tests':>5} {'kept':>4}")
    fmt = lambda value: "  - " if value is None else f"{value:4.2f}"
    for row in rows:
        print(f"{row['instance_id']:<30} {str(row['v1_stage'] or '-'):<20} {fmt(row['v1'])}  "
              f"{row['v2_stage']:<22} {fmt(row['v2'])} {fmt(row['tests_alone'])} {row['kept_tests']:>4}")
    v1s = summary.get("v1") or {}
    print(f"\nOn the {summary['tasks']} task(s): v1 {v1s.get('chooser', '-')}, v2 {summary['chooser']} "
          f"(stages 1-3 without the judge {summary['tests_alone']}); the regression filter alone "
          f"{summary['filter_alone']}, a random run {summary['random_run']}, any run (oracle) {summary['oracle']}")
    for version in ("v1", "v2"):
        stages = summary["stages_" + version]
        print(f"Decided by ({version}): " + (", ".join(f"{stage} {e['right']}/{e['tasks']}"
                                                         for stage, e in sorted(stages.items())) or "-"))
    print(f"On all {summary['listed']}: single runs {summary['single_runs']}; best-of-{len(summary['runs'])} "
          f"v1 {v1s.get('projected', '-')}, v2 {summary['projected']} (every run resolved {summary['all_resolved']})")
    if summary.get("timed_out"):
        print(f"Time cap reached: {', '.join(summary['timed_out'])}")
    if summary["flaky"]:
        print(f"Graded both ways for the same patch text (flaky tests): {', '.join(summary['flaky'])}")


# --------------------------------------------------------------------------- main


def image_of(instance_id: str, runs: List[Dict[str, Any]], runs_dir: Path) -> Optional[str]:
    """The image the runs graded the task in (their manifest), else the community image's name."""
    for run in runs:
        manifest = v1.read_json(runs_dir / run["name"] / "manifest.json") or {}
        image = ((manifest.get("images") or {}).get(instance_id) or {}).get("image")
        if image:
            return str(image)
    return SweBenchImages.image_for(instance_id)


def load_rows(dataset: Path, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """
    The tasks' dataset rows as the chooser may hold them: the fields that give the answer away
    (`FORBIDDEN_FIELDS`) are removed at once, and the eval script, which carries the hidden test
    patch, is replaced by the set-up before it (`setup`).
    """
    wanted, rows = set(ids), {}
    try:
        lines = dataset.read_text().splitlines()
    except OSError:
        raise SystemExit(f"❌ No dataset snapshot at {dataset} (ling-admin swe-bench setup writes it)")
    for line in lines:
        if line.strip():
            row = json.loads(line)
            if row.get("instance_id") in wanted:
                kept = {k: v for k, v in row.items() if k not in FORBIDDEN_FIELDS and k != "eval_script"}
                rows[row["instance_id"]] = dict(kept, setup=setup_preamble(row))
    missing = sorted(wanted - set(rows))
    if missing:
        raise SystemExit(f"❌ Not in {dataset}: {', '.join(missing)}")
    return rows


def run_test(args: argparse.Namespace, client_factory: Callable[[str, float], Any] = None) -> int:
    """The test, as `main` runs it; the client is replaceable for the tests."""
    runs = [v1.load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    if len(runs) < 2:
        raise SystemExit("❌ Name at least two runs")
    tasks = v1.select_tasks(runs, args.all)
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t.instance_id in wanted]
    if args.limit:
        tasks = tasks[:args.limit]
    images = {t.instance_id: image_of(t.instance_id, runs, args.runs_dir) for t in tasks}
    pools = [len([c for c in t.candidates if not c.breaks] or t.candidates) for t in tasks]
    pairs = sum(n * (n - 1) // 2 for n in pools if n > 1)
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; at most "
          f"{len(tasks) * args.generation_attempts} test-writer call(s) and {pairs * JUDGE_QUERIES} judge call(s)")
    for task in tasks:
        local = "local" if images[task.instance_id] and SweBenchDocker.image_digest(images[task.instance_id]) else "to pull"
        print(f"  {task.instance_id:<34} [{local}] " +
              ("; ".join(v1.describe(c) for c in task.candidates) or "no non-empty patch"))
    if args.dry_run:
        return 0
    if args.fake_model:
        client, model = FakeClient(), "fake-model"
    else:
        if not args.server:
            raise SystemExit("❌ --server is required (the model server's URL, by node name), or --fake-model")
        client = (client_factory or v1.Client)(args.server, args.timeout)
        model = args.model or client.model()
    rows = load_rows(args.dataset, [t.instance_id for t in tasks])
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in SweBenchDocker.run(["ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"], timeout=60).stdout.split():
        SweBenchDocker.run(["rm", "-f", stale], timeout=120)
    runner = Runner(args, client, model, rows)
    choices, scores, timed_out = {}, {}, []
    for task in tasks:
        record, choice = runner.run_task(task, images[task.instance_id])
        choices[task.instance_id], scores[task.instance_id] = choice, score(task, choice)
        if record.get("timed_out") or choice["stage"] == "time cap":
            timed_out.append(task.instance_id)
        print(f"  {task.instance_id:<34} {choice['stage']}: chose {', '.join(choice['chosen']) or 'nothing'}, "
              f"credit {scores[task.instance_id]['chooser']:.2f} ({choice['kept_tests']} test(s) kept, "
              f"{choice['seconds']} s)", flush=True)
    summary = v1.summarise(runs, tasks, scores, choices)
    summary["tests_alone"] = round(sum(s["tests_alone"] for s in scores.values()), 2)
    v1_summary = v1.read_json(args.v1_summary) if args.v1_summary else None
    if v1_summary and v1_summary.get("runs") != summary["runs"]:
        print(f"⚠️  {args.v1_summary} was made from {v1_summary.get('runs')}: not comparable, left out")
        v1_summary = None
    rows_side = side_by_side(tasks, choices, scores, v1_summary)
    v1_here = None
    if v1_summary and all(row["v1"] is not None for row in rows_side):
        # Version 1 on the same tasks, projected onto the same list the same way.
        v1_total = round(sum(row["v1"] for row in rows_side), 2)
        v1_here = {"chooser": v1_total, "projected": round(summary["projected"] - summary["chooser"] + v1_total, 2),
                   "summary": str(args.v1_summary)}
    summary.update({
        "version": 2, "model": model, "server": "fake" if args.fake_model else args.server, "seed": args.seed,
        "v1": v1_here,
        "side_by_side": rows_side, "stages_v1": by_stage(rows_side, "v1"), "stages_v2": by_stage(rows_side, "v2"),
        "timed_out": timed_out,
        "per_task": {i: {**choices[i], **scores[i]} for i in choices},
    })
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print_report(summary)
    return 0


def parser() -> argparse.ArgumentParser:
    """The command line: version 1's, plus the containers' and the time cap's."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", help="the model server's URL, by node name (http://second-puffin.local:8000)")
    p.add_argument("--runs", default=v1.DEFAULT_RUNS, help=f"comma-separated run names (default {v1.DEFAULT_RUNS})")
    p.add_argument("--runs-dir", type=Path, default=v1.DEFAULT_RUNS_DIR, help="where the runs are (read only)")
    p.add_argument("--dataset", type=Path, default=v1.DEFAULT_DATASET, help="the local dataset snapshot")
    p.add_argument("--out", type=Path, default=Path("swe-bench-chooser-v2"), help="where records and the summary go")
    p.add_argument("--v1-summary", type=Path, default=DEFAULT_V1_SUMMARY, help="version 1's summary.json, for the comparison")
    p.add_argument("--model", help="the model id (default: the server's /v1/models)")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--temperature", type=float, default=0.6)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--seed", type=int, help="pins the sampling (if the server takes a seed) and the pair order")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for one answer")
    p.add_argument("--task-timeout", type=float, default=2400.0, help="seconds one task may take in all (default 40 min)")
    p.add_argument("--generation-attempts", type=int, default=2, help="test-writer requests when no test is kept")
    p.add_argument("--memory", default=DEFAULT_TASK_MEMORY, help=f"each container's memory cap (default {DEFAULT_TASK_MEMORY})")
    p.add_argument("--cpus", type=int, default=DEFAULT_TASK_CPUS, help=f"each container's CPUs (default {DEFAULT_TASK_CPUS})")
    p.add_argument("--container-network", default="none", help="the containers' network (default none)")
    p.add_argument("--keep-images", action="store_true", help="keep the images this test pulled")
    p.add_argument("--all", action="store_true", help="every task, not only those where the runs disagree")
    p.add_argument("--only", help="comma-separated instance ids")
    p.add_argument("--limit", type=int, help="only the first N tasks")
    p.add_argument("--dry-run", action="store_true", help="list the tasks; contact no server, start no container")
    p.add_argument("--fake-model", action="store_true", help="the built-in fake model instead of a server (a smoke)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the test."""
    return run_test(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
