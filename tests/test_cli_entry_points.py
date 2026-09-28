"""The `puffin` command is `puffin-admin chat` under another name, not a copy of it."""

import sys
from unittest.mock import patch

from dreamference.cli import main, puffin_main
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController


def test_puffin_is_puffin_admin_chat_with_its_arguments_passed_through():
    with patch.object(sys, "argv", ["puffin", "--agent", "codex", "--debug"]), \
            patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        puffin_main()
    run_cli.assert_called_once_with(["chat", "--agent", "codex", "--debug"], chat_prog="puffin")


def test_puffin_and_puffin_admin_chat_parse_to_the_same_arguments():
    admin = DreamferenceCLIController.build_parser().parse_args(["chat", "--cave", "--model", "m"])
    puffin = DreamferenceCLIController.build_parser(chat_prog="puffin").parse_args(["chat", "--cave", "--model", "m"])
    assert vars(admin) == vars(puffin)
    assert admin.command == "chat"


def test_puffin_help_names_puffin(capsys):
    try:
        DreamferenceCLIController.run_cli(["chat", "--help"], chat_prog="puffin")
    except SystemExit:
        pass
    assert capsys.readouterr().out.startswith("usage: puffin ")


def test_puffin_admin_still_reads_sys_argv():
    with patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        main()
    run_cli.assert_called_once_with(None, chat_prog=None)


# A Codex user's command line reaches puffin-codex. run_session is replaced so nothing is launched.

import pytest

from dreamference.runner import CodexRunner


def _chat(argv):
    with patch.object(CodexRunner, "run_session", return_value=0) as run_session, \
            pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["chat", "--agent", "codex", *argv], chat_prog="puffin")
    return run_session


def test_codex_options_and_prompt_pass_through_in_order():
    run_session = _chat(["-a", "on-request", "-c", "model_verbosity=low", "--search", "fix the tests"])
    assert run_session.call_args.kwargs["agent_args"] == [
        "-a", "on-request", "-c", "model_verbosity=low", "--search", "fix the tests",
    ]


def test_codex_subcommands_pass_through():
    assert _chat(["exec", "--json", "do it"]).call_args.kwargs["agent_args"] == ["exec", "--json", "do it"]
    assert _chat(["resume", "--last"]).call_args.kwargs["agent_args"] == ["resume", "--last"]


def test_a_codex_sandbox_policy_goes_to_codex_and_an_engine_stays_here():
    assert _chat(["-s", "workspace-write"]).call_args.kwargs["agent_args"] == ["--sandbox", "workspace-write"]
    with patch("dreamference.cli.dreamference_cli_controller.DreamferenceConfig", wraps=__import__(
            "dreamference.config", fromlist=["DreamferenceConfig"]).DreamferenceConfig) as config:
        run_session = _chat(["--sandbox", "podman"])
    assert config.call_args.kwargs["sandbox"] == "podman"
    assert "agent_args" not in run_session.call_args.kwargs


def test_help_after_a_subcommand_is_codex_help(capsys):
    assert _chat(["exec", "--help"]).call_args.kwargs["agent_args"] == ["exec", "--help"]
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["chat", "--help"], chat_prog="puffin")
    assert "passed to puffin-codex" in capsys.readouterr().out


def test_options_are_not_abbreviated_into_puffin_options():
    # `--ag` would otherwise be read as `--agent`.
    assert _chat(["--ag", "x"]).call_args.kwargs["agent_args"] == ["--ag", "x"]


def test_pass_through_is_refused_for_other_agents_and_other_commands(capsys):
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["chat", "--agent", "aider", "--full-auto"])
    assert "only the codex agent" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["status", "--bogus"])
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err
