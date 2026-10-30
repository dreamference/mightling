"""The Night Shift timer: what `enable` warns about, whether systemd lingers, and what `status` says.

No test here runs systemctl or loginctl: both are replaced, and HOME is a throwaway directory.
"""

import subprocess

from dreamference.night_shift import NightShiftScheduler
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_scheduler import UNIT


def _systemctl(monkeypatch, stdout: str = "") -> list:
    commands: list = []

    def systemctl(args):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(NightShiftScheduler, "systemctl", staticmethod(systemctl))
    return commands


def test_enable_without_lingering_warns_that_the_timer_needs_a_login(monkeypatch, capsys):
    _systemctl(monkeypatch)
    monkeypatch.setattr(NightShiftScheduler, "lingering", classmethod(lambda cls: False))
    # The timer is still installed: the warning says how to make it fire after logout.
    assert NightShiftScheduler.enable("01:30-06:15")
    out = capsys.readouterr().out
    assert "Lingering is off" in out and "sudo loginctl enable-linger" in out
    assert (NightShiftScheduler.unit_dir() / f"{UNIT}.timer").is_file()


def test_lingering_is_on_only_when_loginctl_says_yes(monkeypatch):
    answers = iter(["yes\n", "no\n", ""])
    seen = []

    def loginctl(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, next(answers), "")

    monkeypatch.setattr(subprocess, "run", loginctl)
    assert [NightShiftScheduler.lingering() for _ in range(3)] == [True, False, False]
    assert all(args[:2] == ["loginctl", "show-user"] for args in seen)


def test_lingering_is_off_when_loginctl_cannot_answer(monkeypatch):
    for error in (FileNotFoundError("loginctl"), subprocess.TimeoutExpired("loginctl", 10)):

        def loginctl(args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(subprocess, "run", loginctl)
        assert NightShiftScheduler.lingering() is False


def test_status_shows_the_next_run_a_run_in_progress_and_each_repositorys_queue(monkeypatch):
    _systemctl(
        monkeypatch, stdout="Fri 2026-10-31 01:30:00 UTC  17h left  -  -  mightling-night.timer"
    )
    monkeypatch.setattr(NightShiftScheduler, "lingering", classmethod(lambda cls: True))
    assert NightShiftScheduler.enable("01:30-06:15")
    monkeypatch.setattr(NightShiftQueue, "runner_active", classmethod(lambda cls: True))
    tasks = [
        {"repo": "/src/app", "status": "queued"},
        {"repo": "/src/app", "status": "done"},
        {"repo": "/src/app", "status": "queued"},
        {"repo": "/src/lib", "status": "failed"},
        {},
    ]
    monkeypatch.setattr(NightShiftQueue, "tasks", classmethod(lambda cls, night_dir: tasks))
    assert NightShiftScheduler.status().splitlines() == [
        "Timer: enabled, window 01:30-06:15",
        "Next: Fri 2026-10-31 01:30:00 UTC",
        "A night run is in progress.",
        "/src/app: 1 done, 2 queued",
        "/src/lib: 1 failed",
        "?: 1 ?",
    ]


def test_status_without_a_timer_says_how_to_enable_it_and_that_the_queue_is_empty(monkeypatch):
    commands = _systemctl(monkeypatch)
    monkeypatch.setattr(NightShiftQueue, "runner_active", classmethod(lambda cls: False))
    monkeypatch.setattr(NightShiftQueue, "tasks", classmethod(lambda cls, night_dir: []))
    assert NightShiftScheduler.status().splitlines() == [
        "Timer: not enabled (ling-admin night enable --window 01:00-07:00)",
        "Queue: empty",
    ]
    # Without a timer there is no next run to ask systemd for.
    assert commands == []
