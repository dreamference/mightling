"""`ling-admin`'s exit codes (docs/dev/cli.md).

What a command's work answers is what the process exits with, a failure is 1, a usage error 2,
and a command that has nothing to fail exits 0.

The work itself is stubbed: these tests read only what the dispatch does with its answer.
"""

import importlib
import io
import sqlite3
import sys

import pytest

from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.cli.legacy_name_migration import LegacyNameMigration

# What a stubbed piece of work answers, where the command exits with that answer unchanged.
ANSWER = 5


@pytest.fixture(autouse=True)
def _no_migration(monkeypatch):
    monkeypatch.setattr(LegacyNameMigration, "run", classmethod(lambda cls: None))


def _stub(monkeypatch, target: str, result: object) -> None:
    """Makes `module:Owner.name` (or `module:name`) answer `result`, whatever it is given."""
    module_name, _, attribute = target.partition(":")
    owner_name, _, name = attribute.rpartition(".")
    module = importlib.import_module(module_name)
    if owner_name:
        monkeypatch.setattr(getattr(module, owner_name), name, staticmethod(lambda *a, **k: result))
    else:
        monkeypatch.setattr(module, name, lambda *a, **k: result)


def _exit_code(argv: list[str]) -> object:
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(argv)
    return exit_info.value.code


CONTROLLER = "dreamference.cli.dreamference_cli_controller:DreamferenceCLIController"


@pytest.mark.parametrize(
    "argv, target",
    [
        (["google", "start"], f"{CONTROLLER}._google_start"),
        (["google", "status"], f"{CONTROLLER}._google_status"),
        (["desktop", "install"], "dreamference.chat:DesktopRunner.install"),
        (["desktop", "run"], "dreamference.chat:DesktopRunner.run"),
        (["desktop", "build"], "dreamference.chat:DesktopRunner.build"),
        (["desktop", "status"], "dreamference.chat:DesktopRunner.status"),
        (["gmail", "status"], f"{CONTROLLER}.handle_gmail"),
        (["audit", "egress", "--docs"], "dreamference.audit:DocsEgressAudit.run"),
        (["audit", "egress", "--sync"], "dreamference.audit:SyncEgressAudit.from_cli"),
        (["audit", "egress"], "dreamference.audit:EgressAudit.run"),
        (
            ["swe-bench", "status"],
            "dreamference.swe_bench.swe_bench_command:SweBenchCommand.dispatch",
        ),
        (["node", "status", "n1"], "dreamference.node:NodeRemote.status"),
        (["node", "set", "n1", "--model", "m"], "dreamference.node:NodeRemote.set_model"),
        (["node", "start", "n1"], "dreamference.node:NodeRemote.start"),
        (["node", "stop", "n1"], "dreamference.node:NodeRemote.stop"),
        (["node", "run", "n1", "--", "true"], "dreamference.node:NodeJobSender.run"),
        (["node", "jobs", "n1"], "dreamference.node:NodeJobSender.jobs"),
        (["node", "logs", "j1"], "dreamference.node:NodeJobSender.logs"),
        (["node", "cancel", "j1"], "dreamference.node:NodeJobSender.cancel"),
        (["node", "fetch", "j1"], "dreamference.node:NodeJobSender.fetch"),
        (["node", "job-exec", "j1"], "dreamference.node:NodeJob.execute"),
        (
            ["node", "power-cycle", "n1"],
            "dreamference.node.node_power_settings:NodePowerSettings.dispatch",
        ),
        (["node", "provision", "h1"], "dreamference.node.node_provisioner:NodeProvisioner.run"),
        (["node", "askpass"], "dreamference.node.fleet_askpass:FleetAskpass.ask"),
        (["node", "serve-job", "--key", "k"], "dreamference.node:NodeServe.serve"),
        (["forge", "on"], "dreamference.forge.forge_command:ForgeCommand.dispatch"),
        (["night", "run"], "dreamference.night_shift:NightShiftRunner.run"),
        (["night", "autofill"], f"{CONTROLLER}.handle_night_autofill"),
        (["night", "archive"], f"{CONTROLLER}.handle_night_archive"),
        (["codex", "test"], "dreamference.runner.codex_test_runner:CodexTestRunner.run"),
        (["searxng", "status"], "dreamference.cli.searxng_command:SearxngCommand.run"),
        (["images", "start"], f"{CONTROLLER}._run_images"),
        (["voice", "status"], f"{CONTROLLER}._run_voice"),
        (["matrix", "status"], "dreamference.chat.matrix_homeserver:MatrixHomeserver.status"),
        (["remote", "status"], "dreamference.remote.remote_access:RemoteAccess.status"),
    ],
)
def test_a_command_exits_with_what_its_work_answers(monkeypatch, argv, target):
    from dreamference.node import NodeIdentity

    monkeypatch.setattr(NodeIdentity, "ensure", classmethod(lambda cls: "node-id"))
    _stub(monkeypatch, target, ANSWER)
    assert _exit_code(argv) == ANSWER


def test_an_adapter_command_exits_with_what_it_answers(monkeypatch):
    from dreamference.cli.dreamference_cli_controller import ADAPTER_COMMAND, AdapterCommand

    monkeypatch.setattr(AdapterCommand, "run", staticmethod(lambda *a, **k: ANSWER))
    assert _exit_code([ADAPTER_COMMAND, "list"]) == ANSWER


def test_node_sync_model_exits_with_the_adapter_syncs_verdict(monkeypatch):
    from dreamference.node import NodeModelSync
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    monkeypatch.setattr(NodeModelSync, "sync", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(
        NodeAdapterSync, "after", staticmethod(lambda *a, **k: (ANSWER, ["synced"]))
    )
    assert _exit_code(["node", "sync-model", "n1", "m"]) == ANSWER


@pytest.mark.parametrize(
    "argv, target",
    [
        (["node", "enable", "--no-forge"], "dreamference.node:NodeAdvertiser.enable"),
        (["node", "disable"], "dreamference.node:NodeAdvertiser.disable"),
        (["node", "prepare"], "dreamference.node.node_prepare:NodePrepare.run"),
        (["host", "check"], "dreamference.vllm_server:HostSafetySetup.check"),
        (["host", "setup"], "dreamference.vllm_server:HostSafetySetup.setup"),
        (["night", "enable"], "dreamference.night_shift:NightShiftScheduler.enable"),
        (["night", "disable"], "dreamference.night_shift:NightShiftScheduler.disable"),
        (["code", "setup"], "dreamference.cli.code_index_setup:CodeIndexSetup.install"),
        (["docs", "setup"], "dreamference.runner.docs_index_setup:DocsIndexSetup.install"),
        (
            ["clear", "model-cache"],
            "dreamference.cli.dreamference_cli_controller:clear_model_cache",
        ),
        (
            ["clear", "tensorize-cache"],
            "dreamference.cli.dreamference_cli_controller:clear_tensorizer_cache",
        ),
        (["images", "start"], "dreamference.chat.image_search_sidecar:ImageSearchSidecar.start"),
        (["images", "stop"], "dreamference.chat.image_search_sidecar:ImageSearchSidecar.stop"),
        (["voice", "start"], "dreamference.chat.speech_sidecar:SpeechSidecar.start"),
        (["voice", "stop"], "dreamference.chat.speech_sidecar:SpeechSidecar.stop"),
        (["google", "start"], "dreamference.chat.google_service:GoogleService.start"),
        (["google", "stop"], "dreamference.chat.google_service:GoogleService.stop"),
    ],
)
@pytest.mark.parametrize("done, code", [(True, 0), (False, 1)])
def test_a_command_that_did_its_work_exits_0_and_one_that_did_not_exits_1(
    monkeypatch, argv, target, done, code
):
    from dreamference.forge.forge_service import ForgeService

    monkeypatch.setattr(ForgeService, "rebind", classmethod(lambda cls, *a, **k: True))
    _stub(monkeypatch, target, done)
    assert _exit_code(argv) == code


@pytest.mark.parametrize("done, code", [(True, 0), (False, 1)])
def test_node_authorize_exits_with_whether_the_key_was_taken(monkeypatch, done, code):
    from dreamference.node import NodeServe

    monkeypatch.setattr(sys, "stdin", io.StringIO("ssh-ed25519 AAAA key"))
    monkeypatch.setattr(NodeServe, "authorize", classmethod(lambda cls, key: done))
    assert _exit_code(["node", "authorize"]) == code


@pytest.mark.parametrize(
    "argv",
    [
        ["remote", "certificate"],
        ["audit"],
        ["host"],
        ["night"],
        ["night", "every"],
        ["code"],
    ],
)
def test_a_command_without_the_subcommand_it_needs_is_a_usage_error(argv, capsys):
    assert _exit_code(argv) == 2
    assert "usage: ling-admin" in capsys.readouterr().out


def test_no_command_prints_the_help_and_exits_0(capsys):
    assert _exit_code([]) == 0
    assert "usage: ling-admin" in capsys.readouterr().out


def test_the_status_commands_exit_0(monkeypatch, capsys):
    from dreamference.chat.retired_web_chat import RetiredWebChat
    from dreamference.chat.speech_sidecar import SpeechSidecar
    from dreamference.night_shift import NightShiftScheduler
    from dreamference.vllm_server.model_gate import ModelGate

    monkeypatch.setattr(RetiredWebChat, "describe", classmethod(lambda cls: ["nothing left"]))
    monkeypatch.setattr(SpeechSidecar, "status_lines", classmethod(lambda cls: ["speech: stopped"]))
    monkeypatch.setattr(NightShiftScheduler, "status", classmethod(lambda cls: "night: off"))
    monkeypatch.setattr(ModelGate, "describe", classmethod(lambda cls, host: ["gate: open"]))
    for argv in (["chat", "status"], ["voice", "status"], ["night", "status"]):
        assert _exit_code(argv) == 0, argv
    out = capsys.readouterr().out
    assert "nothing left" in out and "speech: stopped" in out and "night: off" in out


def test_night_every_add_that_is_refused_exits_1(monkeypatch, capsys):
    from dreamference.night_shift import NightShiftRecurring

    def refuse(*args, **kwargs):
        raise ValueError("not a repository")

    monkeypatch.setattr(NightShiftRecurring, "add", staticmethod(refuse))
    assert _exit_code(["night", "every", "--daily", "x"]) == 1
    assert "not a repository" in capsys.readouterr().out


def test_night_autofill_exits_1_without_ling_and_0_once_it_ran(monkeypatch):
    from dreamference.night_shift import NightShiftAutofill
    from dreamference.runner.codex_installer import CodexInstaller

    monkeypatch.delenv("MIGHTLING_NIGHT_MIGHTLING_BIN", raising=False)
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", staticmethod(lambda *a, **k: None))
    assert _exit_code(["night", "autofill"]) == 1
    monkeypatch.setenv("MIGHTLING_NIGHT_MIGHTLING_BIN", "/opt/ling")
    monkeypatch.setattr(NightShiftAutofill, "repo_for", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        NightShiftAutofill, "fill", staticmethod(lambda *a, **k: ["queued nothing"])
    )
    assert _exit_code(["night", "autofill", "--dry-run"]) == 0


def test_night_archive_while_a_run_holds_the_queue_exits_1(monkeypatch, capsys):
    from dreamference.night_shift import NightShiftArchive, NightShiftQueue

    monkeypatch.setattr(NightShiftArchive, "archive", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        NightShiftQueue, "runner_holder", staticmethod(lambda *a, **k: "a night run")
    )
    assert _exit_code(["night", "archive"]) == 1
    assert "A night run is working through the queue" in capsys.readouterr().out


def test_a_pause_for_no_time_is_a_usage_error(monkeypatch):
    from dreamference.vllm_server.model_gate import ModelGate

    monkeypatch.setattr(ModelGate, "pause", classmethod(lambda cls, seconds: pytest.fail("paused")))
    assert _exit_code(["night", "pause", "--for", "0"]) == 2


@pytest.mark.parametrize(
    "argv, payload, code",
    [
        (["gmail", "status", "--json"], {"connected": False}, 0),
        (["gmail", "status"], {"connected": False}, 1),
        (["gmail", "status"], {"connected": True, "email": "a@x.com"}, 0),
    ],
)
def test_gmail_status_exits_1_only_when_no_account_is_connected(monkeypatch, argv, payload, code):
    from dreamference.chat.gmail_client import GmailClient

    monkeypatch.setattr(GmailClient, "status", classmethod(lambda cls: payload))
    assert _exit_code(argv) == code


@pytest.mark.parametrize("failed, code", [(["a@x.com"], 0), (["a@x.com", "b@x.com"], 1)])
def test_a_gmail_search_fails_only_when_every_account_failed(monkeypatch, failed, code):
    from dreamference.chat.gmail_client import GmailClient

    errors = [{"account": account, "error": "token expired"} for account in failed]
    monkeypatch.setattr(
        GmailClient,
        "search",
        classmethod(lambda cls, query, limit: {"messages": [], "errors": errors}),
    )
    monkeypatch.setattr(
        GmailClient, "status", classmethod(lambda cls: {"email": "a@x.com, b@x.com"})
    )
    assert _exit_code(["gmail", "search", "from:x"]) == code


def test_logs_mcp_exits_1_without_a_readable_log_database(monkeypatch, tmp_path, capsys):
    from dreamference.runner.codex_installer import CodexInstaller

    monkeypatch.setattr(CodexInstaller, "home_dir", staticmethod(lambda *a, **k: str(tmp_path)))
    assert _exit_code(["logs", "mcp"]) == 1
    assert "No ling log database" in capsys.readouterr().out
    sqlite3.connect(tmp_path / "logs_2.sqlite").close()  # no `logs` table: the query fails
    assert _exit_code(["logs", "mcp"]) == 1
    assert "Could not read" in capsys.readouterr().out


def test_main_model_inspect_exits_1_with_no_model_set_and_no_server(monkeypatch, tmp_path, capsys):
    import requests

    class Unreachable:
        def get(self, *args, **kwargs):
            raise requests.exceptions.ConnectionError("refused")

    config = tmp_path / "dreamference.toml"
    config.write_text('model = ""\n')
    monkeypatch.setattr(requests, "Session", Unreachable)
    assert _exit_code(["--config", str(config), "main-model", "inspect"]) == 1
    assert "No main model set" in capsys.readouterr().out


def test_codex_exits_1_when_ling_cannot_be_installed(monkeypatch):
    from dreamference.runner.codex_installer import CodexInstaller

    monkeypatch.setattr(CodexInstaller, "install_if_missing", staticmethod(lambda *a, **k: False))
    assert _exit_code(["codex", "start"]) == 1


def test_benchmark_server_exits_1_without_a_dataset(monkeypatch):
    monkeypatch.setattr(
        DreamferenceCLIController,
        "_provision_sonnet_dataset",
        classmethod(lambda cls, container: None),
    )
    assert _exit_code(["benchmark_server"]) == 1


def test_ctrl_c_says_goodbye_and_exits_0(monkeypatch, capsys):
    def interrupted(cls, argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(DreamferenceCLIController, "run_cli", classmethod(interrupted))
    with pytest.raises(SystemExit) as exit_info:
        controller.main([])
    assert exit_info.value.code == 0
    assert "Goodbye" in capsys.readouterr().out
