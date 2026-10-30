"""The Night Shift timer: what `enable` warns about, whether systemd lingers, and what `status` says.

No test here runs systemctl or loginctl: both are replaced, and HOME is a throwaway directory.
"""

import subprocess

from dreamference.night_shift import NightShiftScheduler
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
