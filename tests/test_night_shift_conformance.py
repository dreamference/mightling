"""Night Shift requirements that had no test of their own (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md).

Each test names the section it pins. Nothing here starts a systemd scope, a model server or a real
`ling`: the scope and the process are stand-ins, and every queue lives under `tmp_path`.
"""

import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import pytest

from dreamference.night_shift import (
    NightShiftHost,
    NightShiftQueue,
    NightShiftReport,
    NightShiftRunner,
    NightShiftSettings,
    NightShiftTaskRun,
    night_shift_task_run,
)
from dreamference.night_shift.night_shift_recurring import NightShiftRecurring
from dreamference.night_shift.night_shift_runner import GIB, IDLE_POLL_S, MEMORY_RESERVE
from dreamference.night_shift.night_shift_task_run import KILL_GRACE_S, TEST_TAIL_LINES

MODEL_HOST = "http://model.test:8000"


def task_run(tmp_path: Path, settings: NightShiftSettings | None = None) -> NightShiftTaskRun:
    record = {
        "id": "20261001-0100-abc",
        "repo": str(tmp_path / "repo"),
        "base": "b" * 40,
        "branch": "night/20261001-0100-abc",
        "task": "Add hello.txt",
        "test": None,
        "status": "queued",
        "history": [{"at": "2026-10-01T01:00:00+01:00", "status": "queued"}],
        "attempts": 0,
        "nudges": 0,
        "session": None,
        "result": None,
    }
    return NightShiftTaskRun(
        tmp_path / "night",
        record,
        settings or NightShiftSettings({}),
        "ling",
        deadline=time.time() + 60,
        model_host=MODEL_HOST,
    )


# -- §7: the defaults of the `[night]` table -------------------------------------------------------


def test_the_night_table_defaults_are_the_ones_the_spec_lists():
    settings = NightShiftSettings({})
    assert settings.window == "01:00-07:00"
    assert settings.max_parallel == 3
    assert settings.task_timeout_s == 90 * 60
    assert settings.test_timeout_s == 20 * 60
    assert settings.task_memory == "8G"
    assert settings.nudges == 2
    assert settings.idle_minutes == 10
    assert settings.index is True and settings.index_timeout_s == 20 * 60
    assert settings.best_of == 3
    assert settings.task_context == 65_536
    assert settings.test_sandbox is True
    assert settings.test is None and settings.airgapped is None and settings.compact_at is None
    assert settings.compaction_audit is True
    assert settings.nodes == "paired"
    assert settings.autofill is False and settings.autofill_max == 5


def test_autofill_max_stays_between_one_and_five():
    assert NightShiftSettings({"autofill_max": 9}).autofill_max == 5
    assert NightShiftSettings({"autofill_max": 0}).autofill_max == 1


# -- §6.1: each command runs capped, with no terminal, marked as the night run's -------------------


class FakeProcess:
    def __init__(self, waits=()):
        self.pid = 4242
        self.waits = list(waits)
        self.wait_timeouts = []

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.waits:
            outcome = self.waits.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return 0


def test_a_command_runs_in_a_memory_capped_scope_with_no_stdin_and_the_night_marker(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(NightShiftTaskRun, "USE_SCOPE", True)
    started = {}

    def popen(argv, **kwargs):
        started.update(argv=argv, **kwargs)
        return FakeProcess()

    monkeypatch.setattr(night_shift_task_run.subprocess, "Popen", popen)
    run = task_run(tmp_path, NightShiftSettings({"task_memory": "6G"}))
    assert run._run_capped(["true"], tmp_path, tmp_path / "out.log", timeout=10) == 0
    argv = started["argv"]
    assert argv[:3] == ["systemd-run", "--user", "--scope"]
    assert "MemoryMax=6G" in argv and "MemorySwapMax=0" in argv and "CPUQuota=400%" in argv
    assert argv[argv.index("choom") :] == ["choom", "-n", "500", "--", "true"]
    assert started["stdin"] is subprocess.DEVNULL and started["start_new_session"] is True
    assert started["env"]["MIGHTLING_NIGHT_RUN"] == "1"
    assert started["env"]["DREAMFERENCE_VLLM_HOST"] == MODEL_HOST


def test_a_command_past_its_time_gets_sigterm_then_sigkill_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(NightShiftTaskRun, "USE_SCOPE", True)
    order = []
    monkeypatch.setattr(
        night_shift_task_run.subprocess,
        "run",
        lambda argv, **kwargs: order.append(("scope", argv[3])),
    )
    monkeypatch.setattr(
        night_shift_task_run.os, "killpg", lambda pid, sig: order.append(("group", pid, sig))
    )
    process = FakeProcess([subprocess.TimeoutExpired("ling", KILL_GRACE_S), 0])
    task_run(tmp_path)._terminate(process, "night-unit")
    assert KILL_GRACE_S == 30
    assert order == [
        ("scope", "--signal=SIGTERM"),
        ("group", 4242, signal.SIGTERM),
        ("scope", "--signal=SIGKILL"),
        ("group", 4242, signal.SIGKILL),
    ]
    assert process.wait_timeouts == [KILL_GRACE_S, KILL_GRACE_S]


# -- §5.3: the test run has its own timeout, and the report keeps the last 200 lines ---------------


def test_the_test_run_is_capped_at_test_timeout_and_keeps_the_last_200_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(NightShiftTaskRun, "USE_SANDBOX", False)
    run = task_run(tmp_path, NightShiftSettings({"test_timeout": "7m"}))
    monkeypatch.setattr(run, "_fix_level", lambda: "off")
    seen = {}

    def capped(command, cwd, output, timeout, append=True, extra_env=None):
        seen["timeout"] = timeout
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text("".join(f"line {n}\n" for n in range(250)))
        return 0

    monkeypatch.setattr(run, "_run_capped", capped)
    result = run._run_tests("pytest")
    assert seen["timeout"] == 7 * 60
    tail = result["tail"].splitlines()
    assert TEST_TAIL_LINES == 200 and len(tail) == 200
    assert tail[0] == "line 50" and tail[-1] == "line 249"


# -- §5.5: the night run's own sessions are not interactive use -------------------------------------


def _wait_for_exe(process, installed):
    for _ in range(100):
        try:
            if Path(f"/proc/{process.pid}/exe").readlink() == installed:
                return
        except OSError:
            pass
        time.sleep(0.02)


def test_a_session_the_night_run_started_is_not_counted_as_interactive(tmp_path):
    installed = tmp_path / "bin" / "ling"
    installed.parent.mkdir()
    shutil.copy("/bin/bash", installed)
    marked = dict(os.environ, MIGHTLING_NIGHT_RUN="1")
    # `sleep 30; :` keeps bash itself running: a lone command would be exec'd in its place.
    night = subprocess.Popen([str(installed), "-c", "sleep 30; :"], env=marked)
    user = subprocess.Popen([str(installed), "-c", "sleep 30; :"])
    try:
        for process in (night, user):
            _wait_for_exe(process, installed)
        pids = NightShiftHost.interactive_mightling_pids(str(installed))
        assert user.pid in pids and night.pid not in pids
    finally:
        for process in (night, user):
            process.kill()
            process.wait()


# -- §5.2: admission runs its checks in order, and the first failure is the reason -----------------


class OrderedHost:
    model = ("m", 262144)
    safe = True
    available = 40 * GIB
    heavy: ClassVar[list[str]] = []

    @classmethod
    def served_model(cls, host):
        return cls.model

    @classmethod
    def host_safety_ok(cls):
        return cls.safe

    @classmethod
    def mem_available_bytes(cls):
        return cls.available

    @classmethod
    def heavy_jobs(cls):
        return list(cls.heavy)


@pytest.fixture
def ordered_host(monkeypatch):
    monkeypatch.setattr(NightShiftRunner, "host", OrderedHost)
    monkeypatch.setattr(
        NightShiftRunner, "wait_for_idle", classmethod(lambda cls, *args: "idle check")
    )
    return OrderedHost


def test_admission_names_the_first_check_that_fails(ordered_host, monkeypatch):
    def admit():
        return NightShiftRunner.admit(MODEL_HOST, "ling", 0, datetime.now().astimezone())

    # Every check fails at first; passing them one at a time, in order, moves the reason along.
    failing = [
        ("model", None, ("m", 262144), "not answering"),
        ("safe", False, True, "host-safety"),
        ("available", GIB, 40 * GIB, "GiB of memory is available"),
        ("heavy", ["a ling build holds the build lock"], [], "build lock"),
    ]
    for name, bad, _, _ in failing:
        monkeypatch.setattr(OrderedHost, name, bad)
    for name, _, good, reason in failing:
        assert reason in admit()
        monkeypatch.setattr(OrderedHost, name, good)
    assert admit() == "idle check"


def test_admission_needs_at_least_8_gib_available(ordered_host, monkeypatch):
    assert MEMORY_RESERVE == 8 * GIB
    monkeypatch.setattr(OrderedHost, "available", 8 * GIB)
    assert (
        NightShiftRunner.admit(MODEL_HOST, "ling", 0, datetime.now().astimezone()) == "idle check"
    )
    monkeypatch.setattr(OrderedHost, "available", 8 * GIB - 1)
    assert "a night run needs 8" in NightShiftRunner.admit(
        MODEL_HOST, "ling", 0, datetime.now().astimezone()
    )


def test_the_idle_wait_polls_every_30_seconds():
    assert IDLE_POLL_S == 30.0


# -- §6.2: heavy commands refuse while a night run holds the lock ----------------------------------


@pytest.mark.parametrize("argv", [["index"], ["codex", "build"], ["server", "start"]])
def test_heavy_commands_refuse_naming_the_night_run(tmp_path, monkeypatch, capsys, argv):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    with NightShiftQueue.runner_lock(NightShiftQueue.night_dir()) as held:
        assert held
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(
                ["--config", str(tmp_path / "dreamference.toml"), *argv]
            )
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert f"A Night Shift run is in progress, so `ling-admin {' '.join(argv)}` waits" in out
    assert "see `ling-admin night status`" in out


# -- §8: the morning report -------------------------------------------------------------------------


def test_the_report_gives_attempts_nudges_and_the_kept_worktrees():
    task = {
        "id": "a",
        "repo": "/r",
        "base": "b" * 40,
        "task": "Fix it",
        "status": "done",
        "result": {"branch": "night/a", "attempts": 2, "nudges": 1, "wall_s": 75},
    }
    text = NightShiftReport.render(datetime(2026, 10, 1, 1, 0), [task], [])
    assert "- Attempts: 2, nudges: 1, wall time: 1 min 15 s" in text
    assert "- Branch: `night/a`, not merged; `ling night apply a`" in text
    assert "`git worktree list` shows the worktrees kept for interrupted tasks." in text


# -- §4: recurring templates have ids of their own --------------------------------------------------


def test_a_recurring_template_id_is_r_and_twelve_hex_digits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    template = NightShiftRecurring.add(tmp_path / "night", repo, "Bump the dependencies", "daily")
    assert re.fullmatch(r"r[0-9a-f]{12}", template["id"])
