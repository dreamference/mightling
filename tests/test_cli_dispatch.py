"""What `ling-admin`'s dispatch does before a command runs (docs/dev/cli.md).

Where stdout is a protocol (`mcp`, `images mcp`, the node commands a paired sender's SSH session
runs) nothing may print or ask first: no rename migration, no offer to remove the retired web
chat. Every other command gets both, and the sandbox check is given the command and its
subcommand as typed.
"""

import pytest

from dreamference.chat.image_search_mcp import ImageSearchMcp
from dreamference.chat.image_search_sidecar import ImageSearchSidecar
from dreamference.chat.retired_web_chat import RetiredWebChat
from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.cli.legacy_name_migration import LegacyNameMigration
from dreamference.node import NodeJob, NodeServe
from dreamference.vllm_server import SandboxPrerequisite


@pytest.fixture
def before_the_command(monkeypatch):
    """What ran before the command: `migration`, `offer` and `(gate, command, subcommand)`."""
    seen = []
    monkeypatch.setattr(
        LegacyNameMigration, "run", classmethod(lambda cls: seen.append("migration"))
    )
    monkeypatch.setattr(
        RetiredWebChat, "offer", classmethod(lambda cls, command: seen.append("offer"))
    )
    monkeypatch.setattr(
        SandboxPrerequisite,
        "gate",
        classmethod(
            lambda cls, command, subcommand: seen.append(("gate", command, subcommand)) or True
        ),
    )
    return seen


def _exit_code(argv: list[str]) -> object:
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(argv)
    return exit_info.value.code


def test_mcp_neither_migrates_nor_offers_before_it_serves(before_the_command, monkeypatch):
    monkeypatch.setattr(controller, "run_mcp_server", lambda: None)
    assert _exit_code(["mcp"]) == 0
    assert before_the_command == [("gate", "mcp", None)]


@pytest.mark.parametrize(
    "argv, served",
    [
        (["node", "job-exec", "job-1"], (NodeJob, "execute")),
        (["node", "serve-job", "--key", "k"], (NodeServe, "serve")),
    ],
)
def test_the_node_commands_an_ssh_session_runs_neither_migrate_nor_offer(
    before_the_command, monkeypatch, argv, served
):
    monkeypatch.setattr(*served, classmethod(lambda cls, *a, **k: 0))
    assert _exit_code(argv) == 0
    assert before_the_command == [("gate", "node", argv[1])]


def test_a_command_a_person_types_migrates_and_offers(before_the_command, monkeypatch):
    monkeypatch.setattr(ImageSearchSidecar, "status_lines", classmethod(lambda cls: ["stopped"]))
    assert _exit_code(["images", "status"]) == 0
    assert before_the_command == ["migration", ("gate", "images", "status"), "offer"]


def test_images_status_is_not_the_image_search_mcp_server(before_the_command, monkeypatch, capsys):
    monkeypatch.setattr(ImageSearchMcp, "serve", classmethod(lambda cls: pytest.fail("served MCP")))
    monkeypatch.setattr(
        ImageSearchSidecar, "status_lines", classmethod(lambda cls: ["image search: stopped"])
    )
    assert _exit_code(["images", "status"]) == 0
    assert "image search: stopped" in capsys.readouterr().out


def test_images_mcp_serves_before_anything_can_print(before_the_command, monkeypatch):
    monkeypatch.setattr(ImageSearchMcp, "serve", classmethod(lambda cls: 3))
    assert _exit_code(["images", "mcp"]) == 3
    assert before_the_command == []


def test_the_sandbox_check_gets_a_hyphenated_commands_subcommand(before_the_command, monkeypatch):
    from dreamference.swe_bench.swe_bench_command import SweBenchCommand

    monkeypatch.setattr(SweBenchCommand, "dispatch", classmethod(lambda cls, args: 0))
    assert _exit_code(["swe-bench", "status"]) == 0
    assert ("gate", "swe-bench", "status") in before_the_command
