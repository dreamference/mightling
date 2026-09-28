"""The `puffin` command is `dream chat` under another name, not a copy of it."""

import sys
from unittest.mock import patch

from dreamference.cli import main, puffin_main
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController


def test_puffin_is_dream_chat_with_its_arguments_passed_through():
    with patch.object(sys, "argv", ["puffin", "--agent", "codex", "--debug"]), \
            patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        puffin_main()
    run_cli.assert_called_once_with(["chat", "--agent", "codex", "--debug"], chat_prog="puffin")


def test_puffin_and_dream_chat_parse_to_the_same_arguments():
    dream = DreamferenceCLIController.build_parser().parse_args(["chat", "--cave", "--model", "m"])
    puffin = DreamferenceCLIController.build_parser(chat_prog="puffin").parse_args(["chat", "--cave", "--model", "m"])
    assert vars(dream) == vars(puffin)
    assert dream.command == "chat"


def test_puffin_help_names_puffin(capsys):
    try:
        DreamferenceCLIController.build_parser(chat_prog="puffin").parse_args(["chat", "--help"])
    except SystemExit:
        pass
    assert capsys.readouterr().out.startswith("usage: puffin ")


def test_dream_still_reads_sys_argv():
    with patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        main()
    run_cli.assert_called_once_with(None, chat_prog=None)
