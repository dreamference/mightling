"""`NodePrepare`: applying the steps as root, and what each reading decides.

Nothing runs as root and nothing changes the machine: every command, group, account, profile and
systemd reading is a stand-in.
"""

import pytest

from dreamference.node import node_prepare
from dreamference.node.node_prepare import NodePrepare


@pytest.fixture
def as_root(monkeypatch):
    """Root acting for `owner`, every command recorded and answered from `fails`."""
    state = {"ran": [], "fails": set()}
    monkeypatch.setattr(node_prepare.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "owner")

    def execute(cls, command):
        state["ran"].append(command)
        return tuple(command) not in state["fails"]

    monkeypatch.setattr(NodePrepare, "_execute", classmethod(execute))
    return state


def readings(monkeypatch, *answers):
    """`steps` answering in turn: what is missing before, then after."""
    queue = list(answers)
    monkeypatch.setattr(NodePrepare, "steps", classmethod(lambda cls, user: queue.pop(0)))


MANUAL = {"name": "advertising", "why": "no Avahi", "manual": "install avahi-daemon"}
TWO = {"name": "two", "why": "w", "commands": [["first"], ["second"]]}
ONE = {"name": "one", "why": "w", "commands": [["only"]]}


def test_a_failed_command_skips_the_rest_of_its_step_but_not_the_next_step(
    monkeypatch, as_root, capsys
):
    as_root["fails"].add(("first",))
    readings(monkeypatch, [MANUAL, TWO, ONE], [])

    assert NodePrepare.run() is True

    assert as_root["ran"] == [["first"], ["only"]], "a manual step runs nothing"
    out = capsys.readouterr().out
    assert "install avahi-daemon" in out
    assert 'the rest of "two" was skipped' in out
    assert "ready for owner" in out


def test_a_step_still_missing_afterwards_fails_and_is_named(monkeypatch, as_root, capsys):
    readings(monkeypatch, [MANUAL, ONE], [MANUAL])
    assert NodePrepare.run() is False
    assert "Still to do: advertising." in capsys.readouterr().out
