"""Night Shift's runner under faults: the agent killed, the worktree gone, the disk full, a
corrupt queue file, noisy agent output.

Whatever goes wrong, no queue entry is lost, nothing is merged or pushed, and the report says
what happened to each task. The stand-in `ling` and the repository come from test_night_shift.py.
"""

import json
import os
import signal
import subprocess
import time

import pytest
from test_night_shift import FakeHost, calls, fake_host, git, queue  # noqa: F401 - fixtures
from test_night_shift import setup  # noqa: F401 - the fixture

from dreamference.night_shift import NightShiftQueue, NightShiftReport, NightShiftRunner, NightShiftSettings


def _run(setup, minutes=5):
    return NightShiftRunner.run(minutes=minutes, idle_minutes=0, night_dir=setup["night"], mightling_bin=setup["ling"],
                                vllm_host="http://x", settings=NightShiftSettings({}))


def _reports(setup):
    return sorted((setup["night"] / "reports").glob("*.md"))


def _nothing_published(setup, task_id):
    """No remote, no merge of the task's branch into the base branch, no push."""
    main = git(setup["repo"], "branch", "--show-current").stdout.strip()
    assert not (setup["repo"] / "hello.txt").exists()
    assert not git(setup["repo"], "branch", "--merged", main, "--list", f"night/{task_id}").stdout.replace("*", "").strip() \
        or git(setup["repo"], "rev-parse", f"night/{task_id}").stdout == git(setup["repo"], "rev-parse", main).stdout
    assert git(setup["repo"], "remote").stdout.strip() == ""
    assert git(setup["repo"], "log", "--format=%s", main).stdout.split() == ["init"]


def test_an_agent_killed_mid_task_fails_the_task_and_the_report_says_so(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "error")  # exits at once, as a killed agent's wait status does
    record = queue(setup["night"], setup["repo"])
    assert _run(setup) == 0
    stored = NightShiftQueue.read(setup["night"], record["id"])
    assert stored is not None and stored["status"] in ("failed", "stalled", "interrupted")
    report = _reports(setup)[0].read_text()
    assert record["id"] in report
    _nothing_published(setup, record["id"])


def test_a_signal_killed_agent_is_a_failure_not_a_crash(setup, fake_host, monkeypatch, tmp_path):
    killer = tmp_path / "ling-killed"
    killer.write_text("#!/bin/sh\nkill -9 $$\n")
    killer.chmod(0o755)
    record = queue(setup["night"], setup["repo"])
    setup["ling"] = str(killer)
    assert _run(setup) == 0
    stored = NightShiftQueue.read(setup["night"], record["id"])
    assert stored is not None and stored["status"] != "running"
    assert record["id"] in _reports(setup)[0].read_text()
    _nothing_published(setup, record["id"])


def test_a_corrupt_queue_line_does_not_hide_the_other_tasks(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    good = queue(setup["night"], setup["repo"], task_id="20261001-0100-aaa")
    bad = setup["night"] / "tasks" / "20261001-0100-bbb.json"
    bad.write_text('{"id": "20261001-0100-bbb", "task": "half writ')
    assert _run(setup) == 0
    assert NightShiftQueue.read(setup["night"], good["id"])["status"] == "done"
    assert bad.read_text().startswith('{"id"')  # not deleted, not rewritten


def test_a_task_that_prints_binary_and_huge_lines_still_finishes(setup, fake_host, monkeypatch, tmp_path):
    noisy = tmp_path / "ling-noisy"
    noisy.write_text(
        "#!/bin/sh\n"
        "python3 - \"$@\" <<'PY'\n"
        "import sys, os\n"
        "sys.stdout.buffer.write(os.urandom(100000) + bytes([255, 254, 0]) + b'x' * 10_000_000 + b'\\n')\n"
        "PY\n"
        f"exec {setup['ling']} \"$@\"\n"
    )
    noisy.chmod(0o755)
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    record = queue(setup["night"], setup["repo"])
    setup["ling"] = str(noisy)
    assert _run(setup) == 0
    assert NightShiftQueue.read(setup["night"], record["id"])["status"] != "running"
    assert _reports(setup)


def test_a_full_disk_on_the_report_leaves_every_task_recorded(setup, fake_host, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    record = queue(setup["night"], setup["repo"])

    def full(cls, night_dir, started, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(NightShiftReport, "write", classmethod(full))
    assert _run(setup) == 1  # a clear refusal, not a traceback
    stored = NightShiftQueue.read(setup["night"], record["id"])
    assert stored is not None and stored["status"] == "done"
    assert "No space left on device" in capsys.readouterr().out


def test_the_worktree_removed_under_a_running_task_fails_it(setup, fake_host, monkeypatch):
    import shutil
    from dreamference.night_shift import NightShiftTaskRun
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    record = queue(setup["night"], setup["repo"])
    original = NightShiftTaskRun._cancelled
    seen = {"n": 0}

    def cancelled(self):
        seen["n"] += 1
        if seen["n"] == 3 and self.worktree.exists():
            shutil.rmtree(self.worktree)
        return original(self)

    monkeypatch.setattr(NightShiftTaskRun, "_cancelled", cancelled)
    assert _run(setup) == 0
    stored = NightShiftQueue.read(setup["night"], record["id"])
    assert stored is not None and stored["status"] != "running"
    assert record["id"] in _reports(setup)[0].read_text()
    _nothing_published(setup, record["id"])


def test_a_clock_that_jumps_back_does_not_lose_the_task(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    record = queue(setup["night"], setup["repo"])
    real = time.time
    ticks = {"n": 0}

    def jumpy():
        ticks["n"] += 1
        return real() - (3600 if ticks["n"] % 2 else 0)

    monkeypatch.setattr("dreamference.night_shift.night_shift_runner.time.time", jumpy)
    assert _run(setup) == 0
    stored = NightShiftQueue.read(setup["night"], record["id"])
    assert stored is not None and stored["status"] != "running"


def test_a_second_run_while_one_works_is_refused_and_touches_nothing(setup, fake_host):
    record = queue(setup["night"], setup["repo"])
    before = (setup["night"] / "tasks" / f"{record['id']}.json").read_text()
    with NightShiftQueue.runner_lock(setup["night"]) as held:
        assert held
        assert _run(setup) == 1
    assert (setup["night"] / "tasks" / f"{record['id']}.json").read_text() == before
    assert not (setup["night"] / "reports").exists()
