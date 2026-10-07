"""The check `mling-admin` makes on every run: can bubblewrap sandbox from an ordinary login?

Nothing here runs sudo, `systemd-run` or `apparmor_parser -r`: the probe, the root commands and
the Night Shift timer are replaced, and the decision file lives in the test's home.
"""

import json
import shutil
import subprocess

import pytest

from conftest import REAL_SANDBOX_GATE
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler, UNIT
from dreamference.vllm_server import HostSafetySetup, SandboxPrerequisite
from dreamference.vllm_server import sandbox_prerequisite


@pytest.fixture
def machine(monkeypatch):
    """A GB10 under Ubuntu's AppArmor restriction; the test sets what the probe answers."""
    state = {"works": False, "answers": [], "root": [], "systemctl": [], "tty": True}
    monkeypatch.setattr(SandboxPrerequisite, "gate", classmethod(REAL_SANDBOX_GATE.__func__))
    monkeypatch.setattr(HostSafetySetup, "sandbox_works", classmethod(lambda cls: state["works"]))
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: True))
    monkeypatch.setattr(SandboxPrerequisite, "_interactive", classmethod(lambda cls: state["tty"]))
    for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX"):
        monkeypatch.delenv(name, raising=False)

    def answer(prompt=""):
        if not state["answers"]:
            pytest.fail("asked a question nobody was meant to see")
        return state["answers"].pop(0)

    monkeypatch.setattr("builtins.input", answer)

    def run_as_root(cls, command):
        state["root"].append(command)
        if command[0] == "apparmor_parser":
            state["works"] = True
        return True

    monkeypatch.setattr(HostSafetySetup, "_run_as_root", classmethod(run_as_root))
    monkeypatch.setattr(sandbox_prerequisite.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(NightShiftScheduler, "systemctl",
                        staticmethod(lambda args: state["systemctl"].append(args) or subprocess.CompletedProcess(args, 0)))
    return state


def test_a_working_sandbox_asks_nothing(machine):
    machine["works"] = True
    assert SandboxPrerequisite.gate("server", "start") is True
    assert machine["root"] == []


def test_a_sandbox_that_cannot_be_tried_asks_nothing(machine):
    machine["works"] = None          # no bwrap, or no user systemd
    assert SandboxPrerequisite.gate("night", "run") is True


@pytest.mark.parametrize("command, subcommand", [("mcp", None), ("host", "check"), ("node", "serve-job")])
def test_protocol_and_host_commands_are_never_asked(machine, command, subcommand):
    assert SandboxPrerequisite.gate(command, subcommand) is True


def test_a_command_mightling_runs_is_never_asked(machine, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "019a-test")
    assert SandboxPrerequisite.gate("night", "run") is True


def test_fixing_it_installs_the_profile_through_sudo_and_goes_on(machine, capsys):
    machine["answers"] = ["1"]
    assert SandboxPrerequisite.gate("night", "run") is True
    install, load = machine["root"]
    assert install[0] == "install" and install[-1] == "/etc/apparmor.d/puffin-bwrap"
    assert load == ["apparmor_parser", "-r", "/etc/apparmor.d/puffin-bwrap"]
    out = capsys.readouterr().out
    assert "AppArmor" in out and "✅" in out


def test_a_fix_that_does_not_take_still_refuses_what_needs_the_sandbox(machine, monkeypatch, capsys):
    machine["answers"] = ["1"]
    monkeypatch.setattr(HostSafetySetup, "_run_as_root", classmethod(lambda cls, command: False))
    assert SandboxPrerequisite.gate("night", "enable") is False
    assert "host setup" in capsys.readouterr().err


def test_turning_it_off_removes_the_night_timer_and_is_not_asked_again(machine, capsys):
    timer = NightShiftScheduler.unit_dir() / f"{UNIT}.timer"
    timer.parent.mkdir(parents=True)
    timer.write_text("[Timer]\n")
    machine["answers"] = ["2"]
    assert SandboxPrerequisite.gate("status", None) is True
    assert ["disable", "--now", f"{UNIT}.timer"] in machine["systemctl"] and not timer.exists()
    decision = json.loads(SandboxPrerequisite.decision_path().read_text())
    assert decision["sandbox"] == "off" and decision["night_timer_was_on"] is True
    # Next runs: no question (the answers list is empty, so asking would fail the test).
    assert SandboxPrerequisite.gate("server", "start") is True
    assert SandboxPrerequisite.gate("night", "enable") is False
    assert SandboxPrerequisite.gate("night", "run") is False
    assert "you turned off what needs it" in capsys.readouterr().err
    assert machine["root"] == []


def test_once_the_sandbox_works_the_off_answer_is_forgotten(machine):
    machine["answers"] = ["2"]
    SandboxPrerequisite.gate("status", None)
    machine["works"] = True
    assert SandboxPrerequisite.gate("night", "run") is True
    assert not SandboxPrerequisite.decision_path().exists()


@pytest.mark.parametrize("answer", ["", "3"])
def test_not_now_goes_on_and_asks_again_next_time(machine, answer):
    machine["answers"] = [answer, answer]
    assert SandboxPrerequisite.gate("server", "start") is True
    assert SandboxPrerequisite.gate("night", "run") is False      # the night run still cannot work
    assert machine["answers"] == [] and not SandboxPrerequisite.decision_path().exists()


def test_with_nobody_at_a_terminal_it_warns_and_refuses_only_the_night_run(machine, capsys):
    machine["tty"] = False             # the Night Shift timer, a script, an SSH command
    assert SandboxPrerequisite.gate("status", None) is True
    assert SandboxPrerequisite.gate("night", "run") is False
    err = capsys.readouterr().err
    assert "host setup" in err and "nobody is at a terminal" in err


def test_no_profile_is_offered_when_apparmor_is_not_the_cause(machine, monkeypatch, capsys):
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: False))
    assert SandboxPrerequisite.fix_commands() is None
    assert SandboxPrerequisite.fix() is False
    assert machine["root"] == [] and "other than" in capsys.readouterr().out


def test_every_run_of_mightling_admin_passes_through_the_check(monkeypatch):
    seen = []
    monkeypatch.setattr(SandboxPrerequisite, "gate",
                        classmethod(lambda cls, command, subcommand: seen.append((command, subcommand)) or False))
    with pytest.raises(SystemExit) as stopped:
        DreamferenceCLIController.run_cli(["night", "run"])
    assert stopped.value.code == 1 and seen == [("night", "run")]


@pytest.mark.skipif(shutil.which("apparmor_parser") is None, reason="AppArmor's parser is not installed")
def test_the_profile_compiles(tmp_path):
    # -Q compiles without loading anything into the kernel; -K skips the binary cache.
    profile = tmp_path / "puffin-bwrap"
    profile.write_text(SandboxPrerequisite.profile_text("/usr/bin/bwrap"))
    result = subprocess.run(["apparmor_parser", "-Q", "-K", "-I", "/etc/apparmor.d", str(profile)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
