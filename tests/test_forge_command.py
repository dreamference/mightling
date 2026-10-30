"""`ling-admin forge`: the exit code of each subcommand, its refusals, and the status it prints.

Every operation a subcommand hands on to is a stand-in returning its own exit code; the state file
lives in the test's HOME.
"""

import argparse
from pathlib import Path

import pytest

from dreamference.forge.forge_backup import ForgeBackup
from dreamference.forge.forge_command import USAGE_EXIT, ForgeCommand
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_move import ForgeMove
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_service import ForgeService


def never(*_, **__):
    pytest.fail("called")


@pytest.mark.parametrize(
    ("command", "owner", "method", "options", "given"),
    [
        ("on", ForgeService, "on", {}, ()),
        ("off", ForgeService, "off", {}, ()),
        ("run", ForgeService, "run", {}, ()),
        ("tick", ForgeService, "tick", {}, ()),
        ("promote", ForgeMove, "promote", {"node": "n-2"}, ("n-2",)),
        ("backup", ForgeBackup, "run", {}, ()),
        ("restore", ForgeBackup, "check", {}, ()),
        ("mirrors", ForgeMirrors, "sync", {}, ()),
    ],
)
def test_each_subcommand_returns_the_exit_code_of_the_operation_it_runs(
    monkeypatch, command, owner, method, options, given
):
    seen = []

    def operation(cls, *args):
        seen.append(args)
        return 3

    monkeypatch.setattr(owner, method, classmethod(operation))
    code = ForgeCommand.dispatch(argparse.Namespace(forge_command=command, **options))
    assert (code, seen) == (3, [given])


@pytest.mark.parametrize(("result", "code"), [(True, 0), (False, 1)])
def test_an_import_or_archive_fails_exactly_when_it_imported_nothing(monkeypatch, result, code):
    bundles = []
    monkeypatch.setattr(ForgeRepositories, "import_github", classmethod(lambda cls, *a: result))
    monkeypatch.setattr(
        ForgeRepositories,
        "import_bundles",
        classmethod(lambda cls, paths, name: bundles.append(paths) or result),
    )
    imported = argparse.Namespace(
        forge_command="import", source="o/r", name="r", anonymity="full", exempt=[], mirror=False
    )
    archived = argparse.Namespace(forge_command="archive", bundles=["~/r.bundle"], name="r")
    assert ForgeCommand.dispatch(imported) == code
    assert ForgeCommand.dispatch(archived) == code
    assert bundles == [[Path("~/r.bundle").expanduser()]]


@pytest.mark.parametrize(("node", "back"), [(None, False), ("n-2", True)])
def test_a_move_needs_exactly_one_of_a_node_or_back(monkeypatch, capsys, node, back):
    monkeypatch.setattr(ForgeMove, "move", classmethod(never))
    args = argparse.Namespace(forge_command="move", node=node, back=back)
    assert ForgeCommand.dispatch(args) == USAGE_EXIT
    assert "usage: ling-admin forge move" in capsys.readouterr().out


def test_a_move_to_a_node_or_back_is_handed_on(monkeypatch):
    moves = []
    monkeypatch.setattr(
        ForgeMove, "move", classmethod(lambda cls, node, back: moves.append((node, back)) or 0)
    )
    ForgeCommand.dispatch(argparse.Namespace(forge_command="move", node="n-2", back=False))
    ForgeCommand.dispatch(argparse.Namespace(forge_command="move", node=None, back=True))
    assert moves == [("n-2", False), (None, True)]
