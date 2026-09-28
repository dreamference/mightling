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
