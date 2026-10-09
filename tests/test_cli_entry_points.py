"""`ling-admin` entry point. `ling`, the terminal agent, is the Rust binary (see ling-rs/ and
tests/test_codex_branded_builder.py); there is no Python entry point of that name, and no
`ling-admin chat` either.
"""

import sys
from unittest.mock import patch

from dreamference.cli import main
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController


def test_mightling_admin_still_reads_sys_argv():
    with patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        main()
    run_cli.assert_called_once_with(None)


def test_there_is_no_python_mightling_entry_point():
    import dreamference.cli as cli

    assert not hasattr(cli, "mightling_main")
    assert "ling=" not in open("setup.py").read()


def test_chat_is_the_retired_web_chat_not_the_agent(capsys):
    # `ling` is the terminal agent; the `chat` that started it was retired with the Python
    # launcher. Since the rename `chat` named the web chat's group (it was `puffin-admin puffin`),
    # and since that web chat's retirement it only says so and starts nothing.
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["chat"])
    assert exit_info.value.code == 2
    assert "retired" in capsys.readouterr().out


def test_unknown_arguments_are_rejected(capsys):
    import pytest

    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["status", "--bogus"])
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_a_command_group_without_a_subcommand_prints_its_help(capsys):
    # These used to match no dispatch branch and exit 0 having printed nothing (desktop and
    # the web chat's group hit a NameError instead).
    import pytest

    # `diffusion-model` is absent while diffusion is switched off (tests/test_diffusion_switched_off.py).
    for group in ("server", "clear", "model", "main-model", "desktop", "images", "voice"):
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli([group])
        assert exit_info.value.code == 1, group
        assert f"usage: ling-admin {group}" in capsys.readouterr().out, group


def test_the_retired_web_chat_commands_say_where_their_job_went(capsys):
    # The Onyx web chat is retired (specs/DREAMFERENCE_MIGHTLING_ASK.md §10): every former
    # subcommand, flags included, answers with its replacements instead of argparse's error.
    import pytest

    for argv in (["chat"], ["onyx"], ["chat", "start", "--no-wait"], ["chat", "configure", "--no-web"], ["onyx", "password"]):
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(argv)
        assert exit_info.value.code == 2, argv
        out = capsys.readouterr().out
        assert "retired" in out and "ling web" in out and "ling-admin chat remove" in out, argv


def test_model_list_runs(capsys):
    # A `from rich.table import Table` inside another branch of run_cli made `Table` local to the
    # whole function, so `model list` failed with UnboundLocalError before printing anything.
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "list"])
    assert exit_info.value.code == 0
    assert "Available Mightling Models" in capsys.readouterr().out
