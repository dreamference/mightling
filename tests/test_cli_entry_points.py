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


def test_chat_status_rejects_extra_arguments(capsys):
    # `chat status <anything>` used to ignore the rest and exit 0.
    import pytest

    from dreamference.chat.retired_web_chat import RetiredWebChat

    with patch.object(RetiredWebChat, "describe", side_effect=AssertionError("must not run")):
        for argv in (["chat", "status", "extra"], ["chat", "status", "--bogus"], ["onyx", "status", "--yes"]):
            with pytest.raises(SystemExit) as exit_info:
                DreamferenceCLIController.run_cli(argv)
            assert exit_info.value.code == 2, argv
            assert f"usage: ling-admin chat status (not {argv[2]})" in capsys.readouterr().out, argv


def test_audit_egress_docs_takes_json_and_refuses_a_prompt(capsys):
    # `--docs` used to drop `--prompt` and `--json` silently: `--json` now writes its result, and
    # `--prompt`, which no part of that scenario could use, is refused.
    import pytest

    from dreamference.audit import DocsEgressAudit, EgressAudit

    with patch.object(DocsEgressAudit, "run", return_value=0) as docs_run, \
            patch.object(EgressAudit, "run", side_effect=AssertionError("not the session audit")):
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(["audit", "egress", "--docs", "--json"])
        assert exit_info.value.code == 0
        docs_run.assert_called_once_with(ling_docs=None, write_json=True)
        docs_run.reset_mock()
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(["audit", "egress", "--docs", "--prompt", "hello"])
        assert exit_info.value.code == 2
        docs_run.assert_not_called()
        assert "--prompt has no effect with --docs" in capsys.readouterr().out


def test_logs_mcp_prints_its_heading_without_markup(tmp_path, capsys):
    # The heading went through the builtin print with rich markup, which printed `[bold]` as is.
    import sqlite3

    import pytest

    from dreamference.runner import CodexInstaller

    with sqlite3.connect(tmp_path / "logs_2.sqlite") as conn:
        conn.execute("CREATE TABLE logs (message TEXT)")
        conn.execute("INSERT INTO logs VALUES ('mcp{server_name=search} Service initialized as client')")
    conn.close()
    with patch.object(CodexInstaller, "home_dir", return_value=str(tmp_path)), pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["logs", "mcp"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "MCP server lifecycle (most recent first)" in out and "[bold]" not in out
    assert "search" in out and "Service initialized as client" in out


def test_a_failed_benchmark_exits_with_an_error(capsys):
    # `vllm bench serve` failing was printed and the command still exited 0, so a script or a
    # night job took the failed run for a finished one.
    import subprocess

    import pytest

    failed = subprocess.CalledProcessError(1, ["docker", "exec"])
    with patch("dreamference.hardware.resolve_model_hf_repo", return_value="org/model"), \
            patch.object(subprocess, "run", side_effect=failed) as run, \
            pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["benchmark_server", "--dataset-path", "/data/sonnet.txt"])
    assert run.call_count == 1 and exit_info.value.code == 1
    assert "Benchmark failed" in capsys.readouterr().out


def test_server_start_exits_with_an_error_when_the_server_stopped_before_it_answered(monkeypatch, capsys):
    # The failure was printed and `server start` still exited 0, so a script went on to use a
    # server that was not there.
    import pytest

    from dreamference.chat.google_service import GoogleService
    from dreamference.chat.image_search_sidecar import ImageSearchSidecar
    from dreamference.chat.speech_sidecar import SpeechSidecar
    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.node import NodeAdvertiser
    from dreamference.vllm_server import DiffusionServerManager, VLLMServerManager

    class Monitor:
        server_ready = False

        def start(self):
            pass

        def stop(self):
            pass

    stopped = []
    for sidecar in (GoogleService, SpeechSidecar):
        monkeypatch.setattr(sidecar, "ensure_on_node", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(ImageSearchSidecar, "ensure_on_node", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(NodeAdvertiser, "on_server_starting", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(NodeAdvertiser, "on_server_stopped", classmethod(lambda cls, *a, **k: stopped.append(True)))
    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: Monitor())
    # The container ran and its log stream ended without the server answering.
    monkeypatch.setattr(VLLMServerManager, "start_server", lambda self, **_: None)
    monkeypatch.setattr(DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["server", "start", "--no-diffusion"])
    assert exit_info.value.code == 1 and stopped == [True]
    assert "The model server stopped before it answered" in capsys.readouterr().out


def test_codex_stop_reports_a_daemon_that_did_not_stop(capsys):
    # The stop command's exit code was ignored: "stopped" was printed and the command exited 0.
    import subprocess

    import pytest

    from dreamference.runner import CodexInstaller

    with patch.object(CodexInstaller, "install_if_missing", return_value=True), \
            patch.object(CodexInstaller, "get_codex_executable", return_value="/opt/ling/bin/ling"), \
            patch.object(subprocess, "call", return_value=3) as call, \
            pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["codex", "stop"])
    call.assert_called_once_with(["/opt/ling/bin/ling", "app-server", "daemon", "stop"])
    assert exit_info.value.code == 3
    assert "daemon stopped" not in capsys.readouterr().out


def test_a_global_hf_token_reaches_the_subcommands_that_also_take_one():
    # Each subcommand's own --hf-token defaulted to None, which overwrote the token given before
    # the subcommand: `ling-admin --hf-token T server start` started anonymously.
    parser = DreamferenceCLIController.build_parser()
    for command in (["init"], ["server", "start"], ["adapter", "install", "rust", "--repo", "o/r"]):
        assert parser.parse_args(["--hf-token", "T", *command]).hf_token == "T", command
        assert parser.parse_args([*command, "--hf-token", "U"]).hf_token == "U", command
        assert parser.parse_args(command).hf_token is None, command
    assert parser.parse_args(["--hf-token", "T", "run", "hello"]).hf_token == "T"
