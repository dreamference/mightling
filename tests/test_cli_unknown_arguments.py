"""Every `ling-admin` command refuses an argument it does not know (AGENTS.md).

The commands are walked from the parser itself, so a new subcommand is covered the day it is
added. Each one is given the least it parses with, then an unknown flag or one word too many,
and must exit non-zero, naming the stray argument, before it runs anything.
"""

import argparse
import os
import socket
import subprocess
from collections.abc import Iterator

import pytest

from dreamference.chat.retired_web_chat import RetiredWebChat
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.cli.legacy_name_migration import LegacyNameMigration
from dreamference.node import NodeJobSender

UNKNOWN_FLAG = "--no-such-flag-anywhere"
EXTRA_WORD = "no-such-word-anywhere"


def _subcommands(parser: argparse.ArgumentParser):
    return next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )


def _value(action: argparse.Action) -> str:
    """The first value an argument accepts: its first choice, or one its metavar lists."""
    if action.choices:
        return str(next(iter(action.choices)))
    metavar = action.metavar if isinstance(action.metavar, str) else ""
    if metavar.startswith("{") and metavar.endswith("}"):
        return metavar[1:-1].split(",")[0]
    return "1" if action.type in (int, float) else "x"


def _option(action: argparse.Action) -> list[str]:
    return [action.option_strings[0]] + ([_value(action)] if action.nargs != 0 else [])


def _least(parser: argparse.ArgumentParser) -> tuple[list[str], bool]:
    """The fewest arguments `parser` accepts, and whether it takes free words by design.

    A visible positional with `*`, `+` or `...` takes them (a query, a task, a job's command); a
    hidden catch-all only collects the words a refusal names.
    """
    least: list[str] = []
    takes_words = False
    for action in parser._actions:
        if action.option_strings:
            if action.required:
                least += _option(action)
        elif not isinstance(action, argparse._SubParsersAction):
            if action.nargs in ("*", "+", argparse.REMAINDER) and action.help != argparse.SUPPRESS:
                takes_words = True
            if action.nargs not in ("*", argparse.REMAINDER):
                least.append(_value(action))
    for group in parser._mutually_exclusive_groups:
        if group.required:
            least += _option(group._group_actions[0])
    return least, takes_words


def _commands() -> Iterator[tuple[list[str], list[str], bool, bool]]:
    """Every command and subcommand: (path, least arguments, takes free words, needs a subcommand)."""
    pending = [([], DreamferenceCLIController.build_parser())]
    while pending:
        path, parser = pending.pop()
        least, takes_words = _least(parser)
        subcommands = _subcommands(parser)
        yield path, least, takes_words, bool(subcommands and subcommands.required)
        names = {} if subcommands is None else subcommands.choices
        first_names = {id(subparser): name for name, subparser in reversed(names.items())}
        pending += [
            ([*path, first_names[id(subparser)]], subparser)  # an alias is the same parser
            for subparser in {id(subparser): subparser for subparser in names.values()}.values()
        ]


COMMANDS = sorted(_commands())
CASES = [
    pytest.param(path + least, stray, needs_subcommand, id=" ".join([*path, "+", stray]))
    for path, least, takes_words, needs_subcommand in COMMANDS
    for stray in ([UNKNOWN_FLAG] if takes_words else [UNKNOWN_FLAG, EXTRA_WORD])
]


class _Ran(BaseException):
    """Raised by anything the command must not reach; a BaseException so no handler swallows it."""


def test_the_walk_reaches_every_command_and_each_parses_without_the_stray_argument():
    parser = DreamferenceCLIController.build_parser()
    paths = {" ".join(path) for path, *_ in COMMANDS}
    assert {
        "status",
        "server start",
        "node run",
        "night every add",
        "chat",
        "gmail search",
    } <= paths
    assert len(paths) > 150
    for path, least, _, needs_subcommand in COMMANDS:
        if not needs_subcommand:
            parser.parse_args(DreamferenceCLIController.night_every_shorthand(path + least))


@pytest.mark.parametrize("argv, stray, needs_subcommand", CASES)
def test_every_command_refuses_an_unknown_argument(
    argv, stray, needs_subcommand, monkeypatch, capsys
):
    ran: list[str] = []

    def refuse(what):
        def refused(*args, **kwargs):
            ran.append(what)
            raise _Ran(what)

        return refused

    monkeypatch.setattr(subprocess, "Popen", refuse("a process"))
    for name in ("system", "execv", "execvp", "execve", "execvpe", "fork", "posix_spawn"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse(f"os.{name}"))
    monkeypatch.setattr(socket, "create_connection", refuse("a connection"))
    monkeypatch.setattr(socket.socket, "connect", refuse("a connection"))
    monkeypatch.setattr(
        LegacyNameMigration, "run", classmethod(lambda cls: None)
    )  # every command's first step
    # The two commands whose stray words reach their own code, past argparse.
    monkeypatch.setattr(RetiredWebChat, "remove", classmethod(refuse("chat remove")))
    monkeypatch.setattr(RetiredWebChat, "describe", classmethod(refuse("chat status")))
    monkeypatch.setattr(NodeJobSender, "run", classmethod(refuse("node run")))
    monkeypatch.setattr(os, "environ", dict(os.environ))  # run_cli sets NO_PROXY

    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli([*argv, stray])
    output = capsys.readouterr()
    assert exit_info.value.code not in (0, None), [*argv, stray]
    # A group that needs a subcommand names the missing subcommand instead.
    assert stray in output.out + output.err or needs_subcommand, [*argv, stray]
    assert not ran, ran


@pytest.mark.parametrize(
    "argv",
    [
        ["chat", "--yes", "--delete-data", "remove"],
        ["chat", "remove", "--yes", "--delete-data"],
        ["chat", "remove", "--delete-data", "--yes"],
    ],
)
def test_chat_remove_takes_its_flags_before_or_after_the_subcommand(monkeypatch, argv):
    # After `remove` the catch-all takes them; they still reach the removal rather than being dropped.
    removed = []
    monkeypatch.setattr(LegacyNameMigration, "run", classmethod(lambda cls: None))
    monkeypatch.setattr(
        RetiredWebChat, "remove", classmethod(lambda cls, **flags: removed.append(flags) or 0)
    )
    monkeypatch.setattr(os, "environ", dict(os.environ))  # run_cli sets NO_PROXY
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(argv)
    assert exit_info.value.code == 0
    assert removed == [{"yes": True, "delete_data": True}]


@pytest.mark.parametrize(
    "argv",
    [
        ["node", "run", "--time", "2h", "--memory", "8G", "n1", "--", "true"],
        ["node", "run", "n1", "--time", "2h", "--memory", "8G", "--", "true"],
    ],
)
def test_node_run_takes_its_options_before_or_after_the_nodes_name(monkeypatch, argv):
    sent = []
    monkeypatch.setattr(LegacyNameMigration, "run", classmethod(lambda cls: None))
    monkeypatch.setattr(
        NodeJobSender,
        "run",
        classmethod(
            lambda cls, name, command, **options: sent.append((name, command, options)) or 0
        ),
    )
    monkeypatch.setattr(os, "environ", dict(os.environ))  # run_cli sets NO_PROXY
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(argv)
    assert exit_info.value.code == 0
    [(name, command, options)] = sent
    assert (name, command) == ("n1", ["true"])
    assert (options["time_limit"], options["memory"]) == ("2h", "8G")
