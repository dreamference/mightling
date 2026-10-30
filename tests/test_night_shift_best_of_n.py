"""Night Shift's best-of-N (`NightShiftBestOfN`, backlog item 6) and chooser v1 in the package.

A scripted stand-in for `ling` acts per attempt (read from the worktree's `-a<k>` suffix), the
judge is a stand-in (nothing here reaches a model server; `server_judge` is tried against a fake
on a free loopback port), and no real scope is created (the `setup` fixture switches it off).
"""

import importlib.util
import json
import stat
import sys
import textwrap
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_night_shift import (  # noqa: F401 - fixtures
    HOSTILE_HOOK,
    FakeHost,
    fake_host,
    git,
    queue,
    setup,
)

from dreamference.night_shift import (
    ChooserCandidate,
    NightShiftBestOfN,
    NightShiftChooser,
    NightShiftQueue,
    NightShiftReport,
    NightShiftRunner,
    NightShiftSettings,
    NightShiftTaskRun,
)

ROOT = Path(__file__).resolve().parent.parent

# Attempt k does what FAKE_BON_PLAN[k-1] says: `good` (hello.txt says hi: the test passes), `bad`
# (it says bye: the test fails), `GOOD`/`fine` (passing changes the stand-in judge tells apart),
# `none` (no change), `hang` or `error`.
FAKE_BON = textwrap.dedent("""\
    #!{python}
    import json, os, re, sys, time, uuid
    args = sys.argv[1:]
    if args[0] == "sandbox":
        command = args[args.index("--") + 1:]
        os.environ["FAKE_MIGHTLING_SANDBOXED"] = "1"
        os.execvp(command[0], command)
    with open(os.environ["FAKE_MIGHTLING_CALLS"], "a") as log:
        log.write(json.dumps(args) + "\\n")
    cwd = args[args.index("-C") + 1]
    out = args[args.index("-o") + 1]
    match = re.search(r"-a(\\d+)$", cwd)
    plan = json.loads(os.environ.get("FAKE_BON_PLAN", "[]"))
    mode = plan[int(match.group(1)) - 1] if match and plan else "good"
    print(json.dumps({{"type": "thread.started", "thread_id": str(uuid.uuid4())}}), flush=True)
    if mode == "hang":
        time.sleep(600)
    if mode == "error":
        sys.exit(1)
    if mode != "none":
        text = {{"good": "hi", "bad": "bye", "GOOD": "hi GOOD", "fine": "hi fine"}}[mode]
        with open(os.path.join(cwd, "hello.txt"), "w") as handle:
            handle.write(text + "\\n")
    with open(out, "w") as handle:
        handle.write("Nothing needed changing." if mode == "none" else "Wrote hello.txt.")
    print(json.dumps({{"type": "turn.completed"}}), flush=True)
""")

TEST = "grep -q hi hello.txt"


@pytest.fixture
def bon(setup, monkeypatch):
    """`setup` with the per-attempt stand-in for `ling` and a stand-in judge that prefers GOOD."""
    ling = Path(setup["ling"])
    ling.write_text(FAKE_BON.format(python=sys.executable))
    ling.chmod(ling.stat().st_mode | stat.S_IEXEC)
    judged = []

    def judge(server, issue, seed=None, log=None):
        def compare(a, b):
            judged.append((a.id, b.id, issue))
            rate = lambda candidate: 9.0 if "GOOD" in candidate.patch else 3.0
            if log is not None:
                log.append({"a": a.id, "b": b.id, "ratings": [rate(a), rate(b)]})
            return rate(a), rate(b)

        return compare

    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", staticmethod(judge))
    return {**setup, "judged": judged}


def run_best_of(bon, monkeypatch, plan, n=3, test=TEST, window_s=None, **table):
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(plan))
    record = queue(bon["night"], bon["repo"], test=test)
    window_end = time.time() + window_s if window_s else None
    run = NightShiftBestOfN.task_run(
        bon["night"],
        record,
        NightShiftSettings({"best_of": n, **table}),
        bon["ling"],
        time.time() + (window_s or 60),
        model_host="http://x",
        window_end=window_end,
    )
    status = run.run()
    return status, NightShiftQueue.read(bon["night"], record["id"]), run


def branches(repo):
    return sorted(
        git(repo, "branch", "--list", "night/*", "--format=%(refname:short)").stdout.split()
    )


def test_the_default_is_three_attempts_and_one_runs_as_before(tmp_path):
    # Three since the owner's decision of 2026-10-19 (night 13: 31/50 against a single run's 22/50).
    assert NightShiftSettings({}).best_of == 3
    assert (
        NightShiftBestOfN.attempts_for(
            {"id": "t", "repo": str(tmp_path), "base": "b", "task": "x"}, NightShiftSettings({})
        )
        == 3
    )
    assert NightShiftSettings({"best_of": 1}).best_of == 1
    assert NightShiftSettings({"best_of": 0}).best_of == 1
    settings = NightShiftSettings({"best_of": 3})
    task = {"id": "t", "repo": str(tmp_path), "base": "b", "task": "x"}
    assert NightShiftBestOfN.attempts_for(task, settings) == 3
    assert (
        NightShiftBestOfN.attempts_for({**task, "best_of": 5}, settings) == 5
    )  # the task's own wins
    assert NightShiftBestOfN.attempts_for({**task, "best_of": 1}, settings) == 1
    # `/night add` without `--best-of` writes the key as null: the table's count applies.
    assert NightShiftBestOfN.attempts_for({**task, "best_of": None}, settings) == 3
    assert (
        NightShiftBestOfN.attempts_for({**task, "session": "s"}, settings) == 1
    )  # resumed: one attempt
    assert NightShiftBestOfN.attempts_for({**task, "remote": {"from": "n"}}, settings) == 1
    (tmp_path / "worktrees" / "t").mkdir(parents=True)  # an earlier single run's
    assert NightShiftBestOfN.attempts_for(task, settings, tmp_path) == 1
    assert isinstance(
        NightShiftBestOfN.task_run(tmp_path, task, NightShiftSettings({"best_of": 1}), "ling", 0.0),
        NightShiftTaskRun,
    )


def test_the_regression_filter_picks_the_attempt_whose_tests_pass_and_only_it_is_committed(
    bon, monkeypatch
):
    head = git(bon["repo"], "rev-parse", "HEAD").stdout
    status, record, _ = run_best_of(bon, monkeypatch, ["bad", "good", "bad"])
    assert status == "done"
    result = record["result"]
    assert result["branch"] == "night/20261001-0100-abc"
    assert result["test_result"] == "passed"
    best = result["best_of"]
    assert (best["n"], best["ran"], best["chosen"], best["stage"]) == (3, 3, 2, "regression filter")
    assert [a["status"] for a in best["attempts"]] == ["candidate"] * 3
    assert bon["judged"] == []  # one survivor: no judge call
    # Only the chosen attempt was committed, as night/<id>; the others are gone, their diffs kept.
    assert branches(bon["repo"]) == ["night/20261001-0100-abc"]
    assert git(bon["repo"], "show", "night/20261001-0100-abc:hello.txt").stdout == "hi\n"
    assert (
        git(bon["repo"], "log", "-1", "--format=%s", "night/20261001-0100-abc").stdout.strip()
        == "night: Add hello.txt"
    )
    assert not any((bon["night"] / "worktrees").iterdir())
    assert "bye" in (bon["night"] / "logs" / "20261001-0100-abc-a1.diff").read_text()
    # Nothing merged, the checkout untouched.
    assert git(bon["repo"], "rev-parse", "HEAD").stdout == head
    assert git(bon["repo"], "status", "--porcelain").stdout == ""
    # Three sessions of their own, each in its own worktree.
    execs = [json.loads(line) for line in bon["calls"].read_text().splitlines()]
    assert [c[c.index("-C") + 1].rsplit("-", 1)[1] for c in execs] == ["a1", "a2", "a3"]
    assert not any("resume" in c for c in execs)


def test_the_chosen_attempts_commit_runs_its_hooks_in_the_sandbox(bon, monkeypatch, tmp_path):
    # The repository's hooks are its own tracked files (`.husky/_`): every attempt's worktree has
    # them, and the runner's `worktree add` and commit on the host must not run them.
    escaped = tmp_path / "escaped.txt"
    monkeypatch.setenv("NIGHT_HOOK_ESCAPED", str(escaped))
    hooks = bon["repo"] / ".husky" / "_"
    hooks.mkdir(parents=True)
    for name in ("pre-commit", "post-checkout", "reference-transaction"):
        (hooks / name).write_text(HOSTILE_HOOK)
        (hooks / name).chmod(0o755)
    git(bon["repo"], "add", "-A")
    git(bon["repo"], "commit", "-q", "-m", "hooks")
    git(bon["repo"], "config", "core.hooksPath", ".husky/_")
    escaped.unlink(missing_ok=True)  # The setup's own commit ran them, outside the runner.
    status, record, _ = run_best_of(bon, monkeypatch, ["bad", "good", "bad"])
    assert status == "done", record["result"]
    assert not escaped.exists(), escaped.read_text()
    branch = record["result"]["branch"]
    assert git(bon["repo"], "show", f"{branch}:hooked.txt").stdout == "hooked\n"
    assert git(bon["repo"], "show", f"{branch}:hello.txt").stdout == "hi\n"
    assert not any((bon["night"] / "worktrees").iterdir())


def test_when_every_attempt_passes_the_judge_picks_and_the_report_names_every_attempt(
    bon, monkeypatch
):
    status, record, _ = run_best_of(bon, monkeypatch, ["fine", "GOOD", "fine"])
    assert status == "done"
    best = record["result"]["best_of"]
    assert best["stage"] == "judge" and best["chosen"] == 2
    assert best["judge_calls"] == 3 and len(bon["judged"]) == 3  # the three pairs
    assert all(issue == "Add hello.txt" for _, _, issue in bon["judged"])  # the task is the issue
    assert best["points"]["a2"] > best["points"]["a1"]
    assert git(bon["repo"], "show", "night/20261001-0100-abc:hello.txt").stdout == "hi GOOD\n"
    assert len(Path(best["judge_log"]).read_text().splitlines()) == 3
    report = NightShiftReport.render(datetime.now(), [record], [])
    assert "- Best of 3: 3 of 3 attempts ran; chose attempt 2 by judge; points a1" in report
    assert "  - Judge: 3 call(s)" in report
    assert "  - Attempt 2: candidate, 1 file changed, 1 insertion(+), tests passed" in report
    assert "chosen: the branch" in report
    assert "  - Attempt 1: candidate" in report and "20261001-0100-abc-a1.diff`" in report
    assert (
        "  - Why: the judge compared the attempts the regression filter kept (1, 2, 3) in pairs "
        "and gave attempt 2 the most points"
    ) in report
    # Every attempt keeps its own event log and test log, named in the record and the report.
    for k in (1, 2, 3):
        entry = best["attempts"][k - 1]
        assert (
            entry["log"].endswith(f"20261001-0100-abc-a{k}.jsonl") and Path(entry["log"]).is_file()
        )
        assert (
            entry["test_log"].endswith(f"20261001-0100-abc-a{k}.test.log")
            and Path(entry["test_log"]).is_file()
        )
        assert f"log `{entry['log']}`" in report and f"test log `{entry['test_log']}`" in report
    assert best["test_command"] == TEST


def test_without_a_test_command_the_judge_alone_decides_and_the_report_says_so(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["fine", "bad", "GOOD"], test=None)
    assert status == "done"
    best = record["result"]["best_of"]
    assert best["stage"] == "judge" and best["chosen"] == 3
    assert [a["test_result"] for a in best["attempts"]] == ["untested"] * 3
    assert len(bon["judged"]) == 3  # nothing filtered: all three judged
    report = NightShiftReport.render(datetime.now(), [record], [])
    assert (
        "  - Why: no test command, so the judge alone decided: it compared all 3 attempts" in report
    )


def test_the_regression_filter_says_why(bon, monkeypatch):
    _, record, _ = run_best_of(bon, monkeypatch, ["bad", "good", "bad"])
    assert record["result"]["best_of"]["reason"].startswith(
        "attempt 2 was the only one the regression filter kept"
    )


def test_the_only_candidate_says_why(bon, monkeypatch):
    _, record, _ = run_best_of(bon, monkeypatch, ["none", "good", "none"])
    best = record["result"]["best_of"]
    assert (best["stage"], best["reason"]) == (
        "only candidate",
        "attempt 2 was the only one that left a change",
    )


def test_no_candidate_still_records_every_attempt(bon, monkeypatch):
    _, record, _ = run_best_of(bon, monkeypatch, ["none", "none", "none"])
    best = record["result"]["best_of"]
    assert (best["ran"], best["chosen"]) == (3, None)
    assert [a["status"] for a in best["attempts"]] == ["no-change"] * 3
    report = NightShiftReport.render(datetime.now(), [record], [])
    assert "- Best of 3: 3 of 3 attempts ran; none chosen" in report


def test_an_attempt_that_would_not_fit_the_window_is_not_started(tmp_path):
    task = {"id": "t", "repo": str(tmp_path), "base": "b", "task": "x"}
    run = NightShiftBestOfN(
        tmp_path,
        task,
        NightShiftSettings({}),
        "ling",
        time.time() + 60,
        best_of=3,
        window_end=time.time() + 19.5 * 60,
    )

    def took(*walls):
        return [
            {"attempt": i + 1, "status": "candidate", "result": {"wall_s": w}}
            for i, w in enumerate(walls)
        ]

    assert run.wont_fit(1, []) is None  # the first attempt always starts
    assert run.wont_fit(2, took(10 * 60)) is None  # 19.5 min left, it took 10
    assert run.wont_fit(3, took(10 * 60, 25 * 60)) == (
        "attempt 3 was not started: 19 min of the window were left, less than the 25 min the longest attempt took"
    )
    # A cut-off attempt says nothing about how long one takes; without a window nothing is refused.
    assert (
        run.wont_fit(2, [{"attempt": 1, "status": "interrupted", "result": {"wall_s": 3600}}])
        is None
    )
    run.window_end = None
    assert run.wont_fit(3, took(10 * 3600)) is None


def test_the_first_attempt_keeps_the_schedulers_deadline_and_a_later_one_gets_its_own(tmp_path):
    task = {"id": "t", "repo": str(tmp_path), "base": "b", "task": "x"}
    deadline, window_end = time.time() + 60, time.time() + 3600
    run = NightShiftBestOfN(tmp_path, task, NightShiftSettings({"task_timeout": "10m"}), "ling",
                            deadline, best_of=3, window_end=window_end)
    assert run._attempt_deadline(1) == deadline
    assert time.time() + 590 <= run._attempt_deadline(2) <= time.time() + 600
    run.window_end = time.time() + 120
    assert run._attempt_deadline(2) == run.window_end


def test_no_attempt_starts_once_the_window_has_closed(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "good", "good"], window_s=-1)
    assert status == "interrupted"
    assert not bon["calls"].exists()
    best = record["result"]["best_of"]
    assert best["ran"] == 0 and best["why"] == "the window closed before attempt 1 could start"


def test_an_attempt_that_would_not_fit_is_not_started_and_the_record_says_why(bon, monkeypatch):
    monkeypatch.setattr(NightShiftBestOfN, "wont_fit",
                        lambda self, number, outcomes: "too late" if number == 2 else None)
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "good", "good"])
    assert status == "done"
    best = record["result"]["best_of"]
    assert (best["ran"], best["why"]) == (1, "too late")


def test_a_cancel_during_the_last_attempt_commits_none_of_the_earlier_ones(bon, monkeypatch):
    real_run = NightShiftTaskRun.run

    def run(self):
        if self.attempt == 2:  # `/night drop` while attempt 2 works
            NightShiftQueue.transition(self.night_dir, self.task_id, "cancel-requested")
            return NightShiftQueue.transition(self.night_dir, self.task_id, "cancelled")
        return real_run(self)

    monkeypatch.setattr(NightShiftTaskRun, "run", run)
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "good"], n=2)
    assert status == "cancelled" and record["status"] == "cancelled"
    assert "best_of" not in record["result"]
    assert branches(bon["repo"]) == []
    assert not any((bon["night"] / "worktrees").iterdir())


@pytest.mark.parametrize("statuses, ends", [
    (["no-change", "stalled"], "stalled"),
    (["failed", "timed-out"], "no-change"),
    (["timed-out", "timed-out"], "no-change"),
    (["failed", "failed"], "failed"),
])
def test_with_no_candidate_the_task_ends_as_its_most_telling_attempt(bon, statuses, ends):
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"],
                            time.time() + 60, best_of=len(statuses))
    outcomes = [{"attempt": i + 1, "status": s, "result": {"last_message": f"attempt {i + 1} said so"}}
                for i, s in enumerate(statuses)]
    assert run._no_candidate(outcomes) == ends
    result = NightShiftQueue.read(bon["night"], record["id"])["result"]
    # The last word is kept only where it explains the end: a stall or a failure.
    assert result.get("last_message") == (f"attempt {len(statuses)} said so" if ends in ("stalled", "failed") else None)


def test_without_a_model_server_the_judge_is_skipped_not_asked(bon, monkeypatch):
    asked = []
    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", None)
    monkeypatch.setattr(NightShiftChooser, "server_judge", staticmethod(lambda *a, **k: asked.append(a)))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"], time.time() + 60, best_of=2)
    passed = [{"attempt": i, "status": "candidate", "result": {"test_result": "passed"}} for i in (1, 2)]
    chosen, choice = run._choose(passed)
    assert choice["stage"] == "judge skipped: no model server" and chosen["attempt"] == 1
    assert asked == []


def test_the_server_judge_never_waits_past_the_window_and_always_gets_a_minute(bon, monkeypatch):
    from dreamference.night_shift.night_shift_best_of_n import MIN_JUDGE_TIMEOUT_S
    timeouts = []
    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", None)
    monkeypatch.setattr(NightShiftChooser, "server_judge",
                        staticmethod(lambda server, issue, seed, timeout, log: timeouts.append(timeout)))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"], time.time() + 60,
                            best_of=2, model_host="http://x", window_end=time.time() + 100)
    run._judge(0, [])
    run.window_end = time.time() - 10
    run._judge(0, [])
    assert 90 <= timeouts[0] <= 100 and timeouts[1] == MIN_JUDGE_TIMEOUT_S


def test_a_branch_that_cannot_take_the_tasks_name_keeps_the_attempts(bon, monkeypatch):
    real_git = NightShiftBestOfN._git

    def git_(self, cwd, *args):
        return (1, "locked") if args[:2] == ("branch", "-M") else real_git(self, cwd, *args)

    monkeypatch.setattr(NightShiftBestOfN, "_git", git_)
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "none"], n=2)
    assert status == "done"
    assert record["result"]["branch"] == "night/20261001-0100-abc-a1"
    assert branches(bon["repo"]) == ["night/20261001-0100-abc-a1"]


def test_a_commit_that_fails_fails_the_task_and_keeps_no_attempt(bon, monkeypatch):
    real_git = NightShiftBestOfN._git

    def git_(self, cwd, *args):
        return (1, "hook said no") if args[:1] == ("commit",) else real_git(self, cwd, *args)

    monkeypatch.setattr(NightShiftBestOfN, "_git", git_)
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "good"], n=2)
    assert status == "failed"
    assert record["history"][-1]["note"] == "git commit failed: hook said no"
    assert branches(bon["repo"]) == []
    assert not any((bon["night"] / "worktrees").iterdir())


def test_a_task_dropped_before_it_starts_counts_no_attempt(bon, monkeypatch):
    record = queue(bon["night"], bon["repo"], test=TEST)
    NightShiftQueue.update(bon["night"], record["id"],
                           lambda task: NightShiftQueue.set_status(task, "cancel-requested"))
    run = NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"], time.time() + 60, best_of=3)
    assert run.run() == "cancelled"
    after = NightShiftQueue.read(bon["night"], record["id"])
    assert (after["attempts"], after["result"]) == (0, None)
    assert not bon["calls"].exists()


def test_a_skipped_judge_names_no_tie_at_the_top():
    stage = "judge skipped: no model server"
    record = {"chosen": 1, "stage": stage, "attempts": []}
    assert NightShiftBestOfN.reason(record, {"chosen": ["a1", "a2"]}) == (
        f"{stage}, so the tie went to the attempt whose tests passed, then the earliest"
    )


def test_stopping_the_run_stops_the_attempt_that_is_working(bon, monkeypatch):
    import threading
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(["hang", "good"]))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"], time.time() + 60,
                            best_of=2, model_host="http://x")
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.update(status=run.run()), daemon=True)
    worker.start()
    for _ in range(200):
        if bon["calls"].exists():
            break
        time.sleep(0.05)
    run.stop_event.set()
    worker.join(20)
    assert outcome.get("status") == "interrupted"


def test_a_run_has_at_least_one_attempt(tmp_path):
    task = {"id": "t", "repo": str(tmp_path), "base": "b", "task": "x"}
    assert NightShiftBestOfN(tmp_path, task, NightShiftSettings({}), "ling", time.time() + 60, best_of=0).best_of == 1


def test_the_judges_seed_is_the_tasks_own(bon, monkeypatch):
    seeds = []

    def judge(server, issue, seed=None, log=None):
        seeds.append(seed)
        return lambda a, b: (5.0, 5.0)

    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", staticmethod(judge))
    passed = [{"attempt": i, "status": "candidate", "result": {"test_result": "passed"}} for i in (1, 2)]
    for task_id in ("20261001-0100-abc", "20261001-0100-abd", "20261001-0100-abc"):
        record = queue(bon["night"], bon["repo"], test=TEST, task_id=task_id)
        NightShiftBestOfN(bon["night"], record, NightShiftSettings({}), bon["ling"], time.time() + 60,
                          best_of=2, model_host="http://x")._choose(passed)
    assert seeds[0] == seeds[2] != seeds[1]


def test_clip_keeps_a_text_of_exactly_the_limit_whole():
    assert NightShiftChooser.clip("abc", 3) == "abc"
    assert NightShiftChooser.clip("abcd", 3) == "abc\n[... cut: 1 more characters]"


def test_a_swiss_round_pairs_only_neighbours_within_the_window():
    import random
    ids = [f"x{i}" for i in range(11)]
    scores = {cid: 10.0 - i for i, cid in enumerate(ids)}
    # x0 has met each of its eight neighbours; x9 is ninth, beyond the window.
    played = {frozenset(("x0", f"x{i}")) for i in range(1, 9)}
    pairs = NightShiftChooser.pairings(ids, scores, played, {cid: 2 for cid in ids}, random.Random(0))
    assert not any("x0" in pair for pair in pairs)


def test_the_judge_stops_when_the_window_closes(bon, monkeypatch):
    holder = {}

    def judge(server, issue, seed=None, log=None):
        def compare(a, b):
            holder["run"].stop_event.set()  # the window closes during the first call
            log.append({"a": a.id, "b": b.id, "ratings": [3.0, 9.0]})
            return 3.0, 9.0

        return compare

    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", staticmethod(judge))
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(["fine", "good", "fine"]))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN.task_run(
        bon["night"],
        record,
        NightShiftSettings({"best_of": 3}),
        bon["ling"],
        time.time() + 60,
        model_host="http://x",
    )
    holder["run"] = run
    assert run.run() == "done"
    best = NightShiftQueue.read(bon["night"], record["id"])["result"]["best_of"]
    assert best["judge_calls"] == 1 and best["judge_unmade"] == 2
    assert "the window closed while it judged, so 2 comparison(s) were not made" in best["reason"]


def test_a_tie_goes_to_the_earliest_attempt_and_a_broken_judge_never_stops_the_task(
    bon, monkeypatch
):
    monkeypatch.setattr(
        NightShiftBestOfN,
        "JUDGE",
        staticmethod(lambda server, issue, seed=None, log=None: lambda a, b: None),
    )  # every answer unreadable
    status, record, _ = run_best_of(bon, monkeypatch, ["fine", "good", "fine"])
    assert status == "done"
    assert record["result"]["best_of"]["chosen"] == 1
    assert branches(bon["repo"]) == ["night/20261001-0100-abc"]


def test_when_every_attempt_breaks_all_go_to_the_judge(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["bad", "bad", "bad"])
    assert status == "done"
    assert record["result"]["best_of"]["stage"] == "judge, all broke"
    assert record["result"]["test_result"].startswith("failed")


def test_no_change_in_any_attempt_leaves_no_branch(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["none", "none", "error"])
    assert status == "no-change"
    assert branches(bon["repo"]) == []
    assert not any((bon["night"] / "worktrees").iterdir())


def test_attempts_out_of_time_or_unchanged_leave_no_worktree_and_no_branch(bon, monkeypatch):
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(["hang", "none", "hang"]))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN.task_run(
        bon["night"],
        record,
        NightShiftSettings({"best_of": 3, "task_timeout": "3s"}),
        bon["ling"],
        time.time() + 3,
        model_host="http://x",
    )
    assert run.run() == "no-change"
    assert not any((bon["night"] / "worktrees").iterdir())
    assert branches(bon["repo"]) == []


def test_the_window_closing_chooses_among_the_attempts_that_finished(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["good", "hang", "good"], window_s=6)
    assert status == "done"
    best = record["result"]["best_of"]
    assert (best["ran"], best["chosen"]) == (2, 1)
    assert [a["status"] for a in best["attempts"]] == ["candidate", "interrupted"]
    assert branches(bon["repo"]) == ["night/20261001-0100-abc"]
    assert not any((bon["night"] / "worktrees").iterdir())  # the cut-off attempt is not kept
    report = NightShiftReport.render(datetime.now(), [record], [])
    assert "2 of 3 attempts ran (the window closed during attempt 2, which was discarded)" in report


def test_with_no_attempt_finished_the_task_is_interrupted_and_starts_over(bon, monkeypatch):
    status, record, _ = run_best_of(bon, monkeypatch, ["hang", "good", "good"], window_s=4)
    assert status == "interrupted"
    assert record["session"] is None  # nothing to resume: it starts over
    assert branches(bon["repo"]) == []
    assert (
        NightShiftBestOfN.attempts_for(record, NightShiftSettings({"best_of": 3}), bon["night"])
        == 3
    )


def test_the_judge_counts_as_a_model_request_while_it_runs(bon, monkeypatch):
    seen = []

    def judge(server, issue, seed=None, log=None):
        def compare(a, b):
            seen.append(holder["run"].in_model)
            return 5.0, 5.0

        return compare

    holder = {}
    monkeypatch.setattr(NightShiftBestOfN, "JUDGE", staticmethod(judge))
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(["fine", "good"]))
    record = queue(bon["night"], bon["repo"], test=TEST)
    run = NightShiftBestOfN.task_run(
        bon["night"],
        record,
        NightShiftSettings({"best_of": 2}),
        bon["ling"],
        time.time() + 60,
        model_host="http://x",
    )
    holder["run"] = run
    assert run.run() == "done"
    assert seen == [True] and run.in_model is False


def test_a_whole_night_runs_best_of_three_through_the_scheduler(bon, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_BON_PLAN", json.dumps(["bad", "GOOD", "fine"]))
    for index in range(2):
        queue(
            bon["night"],
            bon["repo"],
            task_text=f"Task {index}",
            test=TEST,
            task_id=f"20261001-0100-c{index}0",
        )
    assert (
        NightShiftRunner.run(
            minutes=5,
            idle_minutes=0,
            night_dir=bon["night"],
            mightling_bin=bon["ling"],
            vllm_host="http://x",
            settings=NightShiftSettings({"best_of": 3}),
        )
        == 0
    )
    tasks = NightShiftQueue.tasks(bon["night"])
    assert [t["status"] for t in tasks] == ["done", "done"]
    assert [t["result"]["best_of"]["chosen"] for t in tasks] == [2, 2]
    assert branches(bon["repo"]) == ["night/20261001-0100-c00", "night/20261001-0100-c10"]
    report = next((bon["night"] / "reports").glob("*.md")).read_text()
    assert report.count("- Best of 3: 3 of 3 attempts ran; chose attempt 2 by judge") == 2


def test_an_attempt_out_of_time_is_a_candidate_the_filter_drops():
    assert NightShiftBestOfN.regression({"test_result": "passed"}) == 0
    assert NightShiftBestOfN.regression({"test_result": "failed (exit 1)"}) == 1
    assert NightShiftBestOfN.regression({"test_result": "timed out after 20 min"}) == 1
    assert NightShiftBestOfN.regression({"test_result": "untested"}) is None
    assert NightShiftBestOfN.regression({"timed_out": True, "test_result": "untested (…)"}) == 1


# -- chooser v1, shared with the chooser test on disk ---------------------------------------------


def test_the_chooser_test_on_disk_runs_this_chooser():
    spec = importlib.util.spec_from_file_location(
        "swe_bench_chooser_test_for_bon", ROOT / "scripts" / "swe_bench_chooser_test.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        for name in ("choose", "tournament", "pairings", "ratings", "clip"):
            assert getattr(module, name).__func__ is getattr(NightShiftChooser, name).__func__
        assert module.Candidate is ChooserCandidate
    finally:
        sys.modules.pop(spec.name, None)


def test_server_judge_asks_the_server_without_a_proxy_and_survives_a_refusal(monkeypatch):
    seen, state = [], {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, code=200):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"data": [{"id": "served-model"}]})

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if state["code"] != 200:
                return self.reply({"error": "busy"}, state["code"])
            self.reply(
                {"choices": [{"message": {"content": "Thinking.\nRATING_A: 8\nRATING_B: 2"}}]}
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # a leftover proxy is never used
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    try:
        log = []
        compare = NightShiftChooser.server_judge(
            f"http://127.0.0.1:{server.server_port}", "Fix the bug", seed=7, log=log
        )
        a, b = ChooserCandidate("a1", "diff A", ["a1"]), ChooserCandidate("a2", "diff B", ["a2"])
        assert compare(a, b) == (8.0, 2.0)
        payload = seen[0]
        assert payload["model"] == "served-model" and payload["seed"] == 7
        assert (payload["temperature"], payload["top_p"], payload["max_tokens"]) == (
            0.6,
            0.95,
            16384,
        )
        assert "<issue>\nFix the bug\n</issue>" in payload["messages"][1]["content"]
        state["code"] = 503
        assert compare(a, b) is None  # unreadable, never raised
        assert log[1]["error"] and log[0]["ratings"] == (8.0, 2.0)
    finally:
        server.shutdown()
