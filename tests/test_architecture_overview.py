"""What specs/DREAMFERENCE_ARCHITECTURE.md states about the tree, held to the code.

The overview names versions, models, commands and crates that other specs describe in depth; these
tests cover the statements no other test checked, so the overview cannot drift from what ships.
"""

import argparse
import re
import tomllib
from pathlib import Path

import pytest

import dreamference
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.context_engine import ContextEngine
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
from dreamference.mcp_server import MCPServer

REPO = Path(__file__).resolve().parent.parent


def _subcommands(parser: argparse.ArgumentParser, *path: str) -> dict[str, argparse.ArgumentParser]:
    """The subparsers below `path` (an empty dict at a leaf)."""
    for name in path:
        parser = _subcommands(parser)[name]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _options(parser: argparse.ArgumentParser, *path: str) -> set[str]:
    for name in path:
        parser = _subcommands(parser)[name]
    return {option for action in parser._actions for option in action.option_strings}


def test_setup_py_and_the_package_carry_the_same_version():
    # The header names both as where the version lives; a release bumps them together.
    setup = (REPO / "setup.py").read_text()
    assert (
        re.search(r'^\s*version="([^"]+)"', setup, re.MULTILINE).group(1)
        == dreamference.__version__
    )
    assert 'license="AGPL-3.0-or-later"' in setup


def test_the_model_table_matches_the_matrix():
    # §2: alias, weights and engine of every entry, and the main model's context and vision.
    expected = {
        "qwen3.8-27b-nvfp4-dflash2": ("RadixArk/Qwen3.8-27B-NVFP4", "ling"),
        "qwen3.8-27b-minima-nvfp4-dflash2": ("minima-ai/mnma_qwen3.8_27b_nvfp4", "sglang"),
        "qwen3.8-flash-next-nvfp4-mtp": ("local-inference-lab/Qwen3.8-Flash-Next-NVFP4", "vllm"),
        "qwen3.8-27b-dflash2-draft": ("dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch", None),
        "tiny-a2d-coder-0.5b-diffusion": (
            "dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1",
            None,
        ),
    }
    matrix = ModelMatrixRegistry.MATRIX
    assert set(matrix) == set(expected)
    for alias, (weights, engine) in expected.items():
        assert matrix[alias].hf_repo_id == weights, alias
        assert (matrix[alias].launch_overrides or {}).get("engine") == engine, alias
    main = matrix["qwen3.8-27b-nvfp4-dflash2"]
    assert main.launch_overrides["max_model_len"] == 262144
    assert main.engine_recipes["sglang"]["max_model_len"] == 262144
    assert main.supports_vision and "sglang" in main.engine_recipes
    assert not matrix["qwen3.8-27b-minima-nvfp4-dflash2"].supports_vision
    assert matrix["tiny-a2d-coder-0.5b-diffusion"].is_diffusion


def test_goose_and_aider_are_gone_and_four_agents_remain():
    # §3.4: four runner/installer pairs; Goose and Aider were removed on 2026-09-30.
    runner = REPO / "dreamference" / "runner"
    runners = {path.name.removesuffix("_runner.py") for path in runner.glob("*_runner.py")}
    installers = {path.name.removesuffix("_installer.py") for path in runner.glob("*_installer.py")}
    assert runners & installers == {"codex", "cline", "continue", "openhands"}
    parser = DreamferenceCLIController.build_parser()
    for agent in ("goose", "aider"):
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "--agent", agent])


@pytest.mark.parametrize(
    "path, names",
    [
        # §3.9; repeating tasks are `night every` (the overview's word for them is "recurring").
        (
            ("night",),
            {
                "enable",
                "disable",
                "status",
                "run",
                "pause",
                "resume",
                "archive",
                "autofill",
                "every",
            },
        ),
        (
            ("swe-bench",),
            {
                "setup",
                "smoke",
                "run",
                "eval",
                "report",
                "status",
                "clean",
                "shard",
                "merge",
                "build-images",
            },
        ),
        (
            ("node",),
            {
                "enable",
                "disable",
                "status",
                "id",
                "list",
                "add",
                "remove",
                "set",
                "start",
                "stop",
                "sync-model",
                "run",
                "jobs",
                "logs",
                "cancel",
                "fetch",
                "prepare",
                "provision",
                "power-cycle",
                "power-config",
            },
        ),
        (("audit",), {"egress"}),
        (("google",), {"start", "stop", "status"}),
        (("images",), {"start", "stop", "status", "mcp"}),
        (("voice",), {"start", "stop", "status"}),
        (("searxng",), {"start"}),
        (("codex",), {"test"}),
        (("docs",), {"setup"}),
        (("code",), {"setup"}),
        (("main-model",), {"inspect"}),
    ],
)
def test_ling_admin_offers_the_subcommands_the_overview_lists(path, names):
    assert names <= set(_subcommands(DreamferenceCLIController.build_parser(), *path))


def test_ling_admin_offers_the_options_the_overview_names():
    parser = DreamferenceCLIController.build_parser()
    assert {"--tui", "--docs", "--sync"} <= _options(parser, "audit", "egress")  # §3.11
    assert {"--code-index", "--strip-names"} <= _options(parser, "swe-bench", "run")  # §3.10
    assert "--drop-test-hunks" in _options(parser, "swe-bench", "eval")
    assert "--deep" in _options(parser, "main-model", "inspect")  # §3.8


def test_the_mcp_server_lists_the_tools_the_overview_names():
    # §3.7: the `ide_*` tools, the two web tools and the code search.
    listed = MCPServer().handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"web_search", "web_fetch", "workspace_search_code"} <= names
    assert {
        "ide_get_active_editor",
        "ide_get_open_files",
        "ide_get_diagnostics",
        "ide_open_file",
        "ide_apply_diff",
    } <= names


def test_the_context_engine_writes_under_the_workspaces_dreamference_folder(tmp_path):
    # §3.6: everything the context engine writes goes to `.dreamference/`.
    engine = ContextEngine(workspace_root=str(tmp_path))
    assert engine.dreamference_dir == tmp_path / ".dreamference"
    assert engine.dreamference_dir.is_dir()
    assert engine.index_file.parent == engine.dreamference_dir


@pytest.mark.parametrize("crate", ["airgapped", "node-locator"])
def test_the_two_leaf_crates_need_nothing_beyond_the_standard_library(crate):
    # §3.15: the sandbox helper and ling-web-rs carry byte-identical copies of these sources, which
    # only works while they compile on their own.
    manifest = tomllib.loads((REPO / "ling-rs" / crate / "Cargo.toml").read_text())
    assert not manifest.get("dependencies") and not manifest.get("build-dependencies")


def test_the_launchers_member_crates_are_the_ones_the_overview_lists():
    # Some are linked on Unix or Linux only (`ling web`, `ling signal`), so every target table counts.
    manifest = tomllib.loads((REPO / "ling-rs" / "Cargo.toml").read_text())
    tables = [
        manifest["dependencies"],
        *(target["dependencies"] for target in manifest["target"].values()),
    ]
    paths = {
        spec["path"]
        for table in tables
        for spec in table.values()
        if isinstance(spec, dict) and "path" in spec
    }
    assert paths == {
        "airgapped",
        "node-locator",
        "safegit",
        "loop-guard",
        "masking",
        "skills",
        "apps",
        "tools",
        "monitor",
        "delegate",
        "sync",
        "web",
        "signal",
        "chat",
    }


def test_cavemode_is_gone_from_the_patches_and_the_launcher():
    # §3.15: `/cavemode` was removed on 2026-10-28, its patch 0017 with it.
    sources = [*(REPO / "codex-patches").glob("*.patch"), *(REPO / "ling-rs").rglob("*.rs")]
    assert sources
    assert [
        path for path in sources if "cavemode" in path.read_text(errors="replace").lower()
    ] == []
