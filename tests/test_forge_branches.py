"""Branch coverage for `dreamference.forge`: what the other forge tests leave unreached.

Refusals, fallbacks and exit codes. Nothing here runs docker, ssh or Forgejo: every call that
would leave the process is a stand-in.
"""

import io
import sys

import pytest

from dreamference.forge.forge_backup import ForgeBackup
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_move import ForgeMove
from dreamference.forge.forge_serve import REFUSED, ForgeServe

# -- forge serve [key]


@pytest.fixture
def handlers(monkeypatch):
    """Every forge operation's handler records what it was asked and answers 0."""
    seen = []

    def record(name):
        return classmethod(lambda cls, *arguments: seen.append((name, *arguments)) or 0)

    for owner, name in [
        (ForgeMirrors, "serve_plan"),
        (ForgeMirrors, "serve_apply"),
        (ForgeBackup, "receive"),
        (ForgeMove, "serve_restore"),
        (ForgeMove, "serve_promote"),
        (ForgeMove, "serve_hand_over"),
        (ForgeMove, "freeze"),
        (ForgeMove, "serve_become_mirror"),
    ]:
        monkeypatch.setattr(owner, name, record(name))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"plan text")))
    return seen


@pytest.mark.parametrize(
    ("operation", "arguments", "called"),
    [
        ("forge-mirror-apply", [], ("serve_apply", "plan text")),
        ("forge-restore", ["set-1"], ("serve_restore", "set-1", None)),
        ("forge-restore", ["set-1", "node-2"], ("serve_restore", "set-1", "node-2")),
        ("forge-promote", [], ("serve_promote",)),
        ("forge-hand-over", [], ("serve_hand_over",)),
        ("forge-unfreeze", [], ("freeze", False)),
        (
            "forge-become-mirror",
            ["node-2", "forge-b"],
            ("serve_become_mirror", "node-2", "forge-b"),
        ),
    ],
)
def test_each_forge_operation_reaches_only_its_own_handler(handlers, operation, arguments, called):
    assert ForgeServe.serve(operation, arguments) == 0
    assert handlers == [called]


def test_a_backup_set_is_received_from_the_raw_input(handlers):
    assert ForgeServe.serve("forge-backup-receive", ["set-1"]) == 0
    assert handlers[0][:2] == ("receive", "set-1")
    assert handlers[0][2] is sys.stdin.buffer


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("forge-mirror-apply", ["extra"]),
        ("forge-restore", []),
        ("forge-restore", ["set-1", "node-2", "extra"]),
        ("forge-promote", ["extra"]),
        ("forge-hand-over", ["extra"]),
        ("forge-unfreeze", ["extra"]),
        ("forge-become-mirror", ["node-2", "forge-b", "extra"]),
    ],
)
def test_a_forge_operation_with_the_wrong_arguments_is_refused_and_runs_nothing(
    handlers, operation, arguments, capsys
):
    assert ForgeServe.serve(operation, arguments) == REFUSED
    assert handlers == []
    assert "forge operations are" in capsys.readouterr().err
