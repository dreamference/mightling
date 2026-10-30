"""Where `SandboxPrerequisite`'s fix gives up, and how its question takes an answer it does not know."""

import pytest

from dreamference.vllm_server import sandbox_prerequisite
from dreamference.vllm_server.host_safety_setup import HostSafetySetup
from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite

COMMANDS = [
    ["install", "-m", "0644", "profile", "/etc/apparmor.d/bwrap"],
    ["apparmor_parser", "-r"],
]


@pytest.fixture
def fix(monkeypatch):
    """`fix()` over two profile commands; `ran` records what ran as root."""
    ran = []
    monkeypatch.setattr(SandboxPrerequisite, "fix_commands", classmethod(lambda cls: COMMANDS))
    monkeypatch.setattr(sandbox_prerequisite.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        HostSafetySetup, "_run_as_root", classmethod(lambda cls, argv: ran.append(argv) or True)
    )
    monkeypatch.setattr(
        SandboxPrerequisite,
        "_forget_saying_how_to_restore_night",
        classmethod(lambda cls, file=None: pytest.fail("the Night Shift note was dropped")),
    )
    return ran


# -- fix -----------------------------------------------------------------------------------------------


def test_without_sudo_the_fix_prints_the_commands_for_root_and_runs_none(fix, monkeypatch, capsys):
    monkeypatch.setattr(sandbox_prerequisite.shutil, "which", lambda name: None)

    assert SandboxPrerequisite.fix() is False
    assert fix == []
    out = capsys.readouterr().out
    assert "sudo is not installed" in out
    assert all(" ".join(command) in out for command in COMMANDS)


def test_a_profile_that_installs_but_does_not_help_is_not_a_fix(fix, monkeypatch, capsys):
    monkeypatch.setattr(HostSafetySetup, "sandbox_works", classmethod(lambda cls: False))

    assert SandboxPrerequisite.fix() is False
    assert fix == COMMANDS
    assert "bubblewrap is still refused" in capsys.readouterr().out


# -- _ask ----------------------------------------------------------------------------------------------


def test_an_answer_that_is_not_a_choice_is_asked_again(monkeypatch):
    answers = iter(["9", "yes", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert SandboxPrerequisite._ask() == "off"
    assert next(answers, None) is None
