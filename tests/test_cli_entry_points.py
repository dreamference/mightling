"""`puffin-admin` entry point. `puffin`, the terminal agent, is the Rust binary (see puffin-rs/ and
tests/test_codex_branded_builder.py); there is no Python entry point of that name, and no
`puffin-admin chat` either.
"""

import sys
from unittest.mock import patch

from dreamference.cli import main
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController


def test_puffin_admin_still_reads_sys_argv():
    with patch.object(DreamferenceCLIController, "run_cli") as run_cli:
        main()
    run_cli.assert_called_once_with(None)


def test_there_is_no_python_puffin_entry_point():
    import dreamference.cli as cli

    assert not hasattr(cli, "puffin_main")
    assert "puffin=" not in open("setup.py").read()


def test_there_is_no_chat_subcommand(capsys):
    # `puffin` is the terminal agent; `puffin-admin chat` was retired with the Python launcher.
    import pytest

    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["chat"])
    assert "invalid choice: 'chat'" in capsys.readouterr().err


def test_unknown_arguments_are_rejected(capsys):
    import pytest

    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["status", "--bogus"])
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_a_command_group_without_a_subcommand_prints_its_help(capsys):
    # These used to match no dispatch branch and exit 0 having printed nothing (desktop and
    # puffin hit a NameError instead).
    import pytest

    for group in ("server", "clear", "model", "main-model", "diffusion-model", "desktop", "puffin", "onyx"):
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli([group])
        assert exit_info.value.code == 1, group
        name = "puffin" if group == "onyx" else group  # the alias prints the command's own name
        assert f"usage: puffin-admin {name}" in capsys.readouterr().out, group


def test_model_list_runs(capsys):
    # A `from rich.table import Table` inside another branch of run_cli made `Table` local to the
    # whole function, so `model list` failed with UnboundLocalError before printing anything.
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "list"])
    assert exit_info.value.code == 0
    assert "Available Puffin Models" in capsys.readouterr().out
