"""A dropped task stops at its next step; a runner bug never leaves it `running`.

Specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5.3. The stand-in `ling` and the repository come
from test_night_shift.py; no model server is asked.
"""

import time

import pytest
from test_night_shift import calls, git, queue
from test_night_shift import setup as night_repo  # noqa: F401 - the fixture

from dreamference.night_shift import NightShiftQueue, NightShiftSettings, NightShiftTaskRun
from dreamference.night_shift.night_shift_task_run import NUDGE


@pytest.fixture
def night(request):
    """The repository, queue directory and stand-in `ling` of test_night_shift.py."""
    return request.getfixturevalue("night_repo")


def _execs(night) -> list:
    """The `ling exec` calls made, none when the stand-in never ran."""
    return calls(night) if night["calls"].exists() else []


def _drop_at(monkeypatch, check: int) -> None:
    """Has the queue say the task was dropped from its `check`-th cancel check on."""
    seen = {"checks": 0}

    def cancelled(self):
        seen["checks"] += 1
        return seen["checks"] >= check

    monkeypatch.setattr(NightShiftTaskRun, "_cancelled", cancelled)


def _run(night, monkeypatch, mode: str, attempt=None):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", mode)
    record = queue(night["night"], night["repo"])
    run = NightShiftTaskRun(
        night["night"],
        record,
        NightShiftSettings({}),
        night["ling"],
        deadline=time.time() + 60,
        attempt=attempt,
    )
    status = run.run()
    return status, NightShiftQueue.read(night["night"], record["id"]), run


def _left_behind(night, run) -> list[str]:
    branches = git(night["repo"], "branch", "--list", run.branch).stdout.strip()
    return [
        name for name, there in (("worktree", run.worktree.exists()), ("branch", branches)) if there
    ]


@pytest.mark.parametrize(
    ("check", "execs"),
    [
        (2, 0),  # after the worktree was made, before `ling exec` starts
        (3, 1),  # after `ling exec` changed something, before it is staged
        (4, 1),  # after the change was staged, before it is committed
    ],
)
def test_a_task_dropped_while_it_runs_stops_at_the_next_step_and_keeps_nothing(
    night, monkeypatch, check, execs
):
    _drop_at(monkeypatch, check)
    status, record, run = _run(night, monkeypatch, "change")
    assert status == "cancelled" and record["status"] == "cancelled"
    assert len(_execs(night)) == execs
    assert _left_behind(night, run) == []
    # Nothing was committed on the repository's own branch either.
    assert git(night["repo"], "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_a_task_dropped_while_it_stalls_is_not_nudged(night, monkeypatch):
    # The third check is the one before the first nudge: a dropped task gets no further `ling exec`.
    _drop_at(monkeypatch, 3)
    status, record, run = _run(night, monkeypatch, "stall")
    assert status == "cancelled" and record["status"] == "cancelled"
    assert NUDGE not in [args[-1] for args in _execs(night)]
    assert len(_execs(night)) == 1
    assert _left_behind(night, run) == []


def test_a_nudge_cut_off_by_the_window_leaves_the_task_interrupted_for_the_next_night(
    night, monkeypatch
):
    exec_ = NightShiftTaskRun._exec

    def exec_until_the_nudge(self, prompt, *args, **kwargs):
        if prompt == NUDGE:
            return "interrupted"
        return exec_(self, prompt, *args, **kwargs)

    monkeypatch.setattr(NightShiftTaskRun, "_exec", exec_until_the_nudge)
    status, record, run = _run(night, monkeypatch, "stall")
    assert status == "interrupted" and record["status"] == "interrupted"
    # The worktree and branch are kept for the session to resume.
    assert _left_behind(night, run) == ["worktree", "branch"]
    assert record["result"]["branch"] == run.branch


@pytest.mark.parametrize("attempt", [None, 1])
def test_a_runner_bug_fails_the_task_instead_of_leaving_it_running(night, monkeypatch, attempt):
    def broken(self):
        raise RuntimeError("worktree helper broke")

    monkeypatch.setattr(NightShiftTaskRun, "_prepare_worktree", broken)
    status, record, run = _run(night, monkeypatch, "change", attempt=attempt)
    assert status == "failed"
    if attempt is None:
        assert record["status"] == "failed"
        assert record["result"]["last_message"] == "night run error: worktree helper broke"
    else:
        # One attempt of a best-of-N task: the coordinator owns the record, the attempt its outcome.
        assert record["status"] == "queued"
        assert run.outcome["status"] == "failed"
        assert run.outcome["result"]["last_message"] == "night run error: worktree helper broke"
