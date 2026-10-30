"""`ling-admin forge`: the exit code of each subcommand, its refusals, and the status it prints.

Every operation a subcommand hands on to is a stand-in returning its own exit code; the state file
lives in the test's HOME.
"""

import argparse
import io
from pathlib import Path

import pytest

from dreamference.forge import forge_command
from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_backup import ForgeBackup
from dreamference.forge.forge_command import USAGE_EXIT, ForgeCommand
from dreamference.forge.forge_github_mirror import ForgeGithubMirror
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_move import ForgeMove
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_service import ForgeService
from dreamference.forge.forge_settings import ForgeSettings


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


# Keys, the GitHub mirror and the password


def test_a_key_subcommand_other_than_add_prints_the_usage(monkeypatch, capsys):
    monkeypatch.setattr(ForgeAccounts, "add_key", classmethod(never))
    args = argparse.Namespace(forge_command="key", key_command=None)
    assert ForgeCommand.dispatch(args) == USAGE_EXIT
    assert "ling-admin forge" in capsys.readouterr().out


def test_a_key_file_that_cannot_be_read_adds_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ForgeAccounts, "add_key", classmethod(never))
    args = argparse.Namespace(forge_command="key", key_command="add", file=str(tmp_path / "x.pub"))
    assert ForgeCommand.dispatch(args) == 1
    assert "x.pub" in capsys.readouterr().out


@pytest.fixture
def github(monkeypatch):
    saved = []
    monkeypatch.setattr(
        ForgeGithubMirror, "settings", classmethod(lambda cls: {"account": "acct-old"})
    )
    monkeypatch.setattr(
        ForgeGithubMirror,
        "save_settings",
        classmethod(lambda cls, enabled, account: saved.append((enabled, account))),
    )
    monkeypatch.setattr(ForgeGithubMirror, "destinations", classmethod(lambda cls: []))
    return saved


def mirror_args(run=False, off=False, account=None):
    return argparse.Namespace(forge_command="github-mirror", run=run, off=off, account=account)


def test_the_github_mirror_runs_now_or_turns_off_keeping_the_account(github, monkeypatch, capsys):
    monkeypatch.setattr(ForgeGithubMirror, "run", classmethod(lambda cls: 4))
    assert ForgeCommand.dispatch(mirror_args(run=True)) == 4
    assert github == []

    assert ForgeCommand.dispatch(mirror_args(off=True)) == 0
    assert github == [(False, "acct-old")]
    assert "nothing on GitHub was changed" in capsys.readouterr().out


def test_the_github_mirror_turns_on_for_the_account_given_or_the_one_saved(github, capsys):
    assert ForgeCommand.dispatch(mirror_args(account="acct-new")) == 0
    assert ForgeCommand.dispatch(mirror_args()) == 0
    assert github == [(True, "acct-new"), (True, "acct-old")]
    assert "Destinations: none yet" in capsys.readouterr().out


class Terminal(io.StringIO):
    def isatty(self):
        return True


def on_terminal(monkeypatch):
    """Put standard input and the captured standard output on a terminal, as on the node itself."""
    monkeypatch.setattr(forge_command.sys, "stdin", Terminal())
    monkeypatch.setattr(forge_command.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)


def test_the_password_is_not_printed_to_a_terminal_reached_over_ssh(monkeypatch, capsys):
    on_terminal(monkeypatch)
    ForgeSettings.write_secret("owner-password", "q" * 24)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "forge password --show")
    assert ForgeCommand.dispatch(argparse.Namespace(forge_command="password")) == 1
    out = capsys.readouterr().out
    assert "q" * 24 not in out and "own terminal only" in out


def test_the_password_is_printed_on_the_nodes_own_terminal_or_its_absence_said(monkeypatch, capsys):
    on_terminal(monkeypatch)
    assert ForgeCommand.dispatch(argparse.Namespace(forge_command="password")) == 1
    assert "never set up here" in capsys.readouterr().out

    ForgeSettings.write_secret("owner-password", "q" * 24)
    assert ForgeCommand.dispatch(argparse.Namespace(forge_command="password")) == 0
    assert capsys.readouterr().out == "q" * 24 + "\n"
