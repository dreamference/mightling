"""The package layout specs/DREAMFERENCE_CODEBASE.md describes.

The fourteen facades, the import contract (§4), the module tree (§2), the MCP server's tools
(§3.7) and the main model's launch command (§5.1).
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

import dreamference
from dreamference.mcp_server import MCPServer
from dreamference.vllm_server import VLLMServerManager

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "specs" / "DREAMFERENCE_CODEBASE.md"

# §1.1: thirteen subsystems and `cli/`.
PACKAGES = (
    "audit",
    "chat",
    "cli",
    "config",
    "context_engine",
    "forge",
    "hardware",
    "mcp_server",
    "night_shift",
    "node",
    "remote",
    "runner",
    "swe_bench",
    "vllm_server",
)


def test_the_package_is_the_fourteen_subsystems_of_section_1_1():
    found = {p.parent.name for p in (REPO / "dreamference").glob("*/__init__.py")}
    assert found == set(PACKAGES)


@pytest.mark.parametrize("package", PACKAGES)
def test_every_facade_exports_only_names_it_has(package):
    module = importlib.import_module(f"dreamference.{package}")
    exported = module.__all__
    assert exported, f"dreamference/{package}/__init__.py has an empty __all__"
    assert [name for name in exported if not hasattr(module, name)] == []


def test_the_root_package_holds_the_version_and_nothing_else():
    tree = ast.parse((REPO / "dreamference" / "__init__.py").read_text())
    body = [node for node in tree.body if not isinstance(node, ast.Expr)]
    assert len(body) == 1 and isinstance(body[0], ast.Assign)
    assert [ast.unparse(t) for t in body[0].targets] == ["__version__"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", dreamference.__version__)


def test_classes_come_from_the_subpackage_facades_not_the_root():
    with pytest.raises(ImportError):
        from dreamference import DreamferenceConfig
    # The imports §4 shows.
    from dreamference.config import DreamferenceConfig
    from dreamference.context_engine import ContextEngine
    from dreamference.hardware import HardwareManager, resolve_model_hf_repo
    from dreamference.runner import ClineRunner, CodexRunner

    facades = (DreamferenceConfig, ContextEngine, HardwareManager, resolve_model_hf_repo)
    assert all((*facades, CodexRunner, ClineRunner, VLLMServerManager))


def _modules_the_tree_names() -> list[Path]:
    """Every `.py` file §2's tree names under `dreamference/`."""
    tree = SPEC.read_text().split("```\ndreamference/\n", 1)[1].split("\nscripts/", 1)[0]
    package = ""
    paths = []
    for line in tree.splitlines():
        folder = re.match(r"^[├└]── (\w+)/", line)
        if folder:
            package = folder.group(1)
            continue
        entry = re.match(r"^(│   |    )?[├└]── ([\w. /]+?\.py)\b", line)
        if entry:
            where = REPO / "dreamference" / (package if entry.group(1) else "")
            paths += [where / name.strip() for name in entry.group(2).split("/")]
    return paths


def test_every_module_the_tree_names_exists():
    named = _modules_the_tree_names()
    assert len(named) > 150, "the tree in §2 was not found"
    assert [str(p.relative_to(REPO)) for p in named if not p.is_file()] == []


def test_the_mcp_server_offers_exactly_the_tools_of_section_3_7():
    response = MCPServer().handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert sorted(tool["name"] for tool in response["result"]["tools"]) == [
        "ide_apply_diff",
        "ide_get_active_editor",
        "ide_get_diagnostics",
        "ide_get_open_files",
        "ide_open_file",
        "web_fetch",
        "web_search",
        "workspace_search_code",
    ]


@pytest.fixture
def sglang_command(monkeypatch):
    """The main model's launch command, split at the image into the container and the server."""
    manager = VLLMServerManager()
    monkeypatch.setattr(manager, "is_docker_available", lambda: True)
    command = manager.build_launch_command(model="qwen3.8-27b-nvfp4-dflash2")
    image = next(a for a in command if a.startswith("lmsysorg/sglang@sha256:"))
    return command[: command.index(image)], command[command.index(image) + 1 :]


def test_the_main_models_container_is_limited_and_restarts_as_section_5_1_says(sglang_command):
    container, _ = sglang_command
    flag = {
        a.split("=", 1)[0]: a.split("=", 1)[1] for a in container if a.startswith("--") and "=" in a
    }
    # The numbers come from the host; their shape and the swap cap equal to memory do not.
    assert re.fullmatch(r"\d+\.\d", flag["--cpus"])
    assert re.fullmatch(r"\d+g", flag["--memory"]) and flag["--memory-swap"] == flag["--memory"]
    assert container[container.index("--restart") + 1] == "unless-stopped"
    env = [container[i + 1] for i, a in enumerate(container) if a == "-e"]
    assert "TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor" in env
    assert {"VLLM_NO_USAGE_STATS=1", "DO_NOT_TRACK=1", "HF_HUB_OFFLINE=1"} <= set(env)
    mounts = [container[i + 1] for i, a in enumerate(container) if a == "-v"]
    patched = [m for m in mounts if "/sglang-patches/serving_responses.py" in m]
    assert len(patched) == 1 and patched[0].endswith("/openai/serving_responses.py:ro")


def test_the_main_models_server_flags_are_the_recipe_of_section_5_1(sglang_command):
    _, server = sglang_command

    def value(name: str) -> str:
        return server[server.index(name) + 1]

    assert value("--context-length") == "262144"
    assert value("--tp-size") == "1"
    assert value("--reasoning-parser") == "qwen3"
    assert value("--speculative-draft-model-quantization") == "modelopt_fp4"
    assert value("--chat-template").startswith("/root/.cache/dreamference/chat-templates/")
    assert value("--attention-backend") == "flashinfer"
    assert [value(f) for f in ("--chunked-prefill-size", "--max-running-requests")] == ["8192", "8"]
    assert [
        value(f)
        for f in (
            "--cuda-graph-max-bs-decode",
            "--torch-compile-max-bs",
            "--num-continuous-decode-steps",
        )
    ] == ["8", "4", "2"]
    assert [
        value(f)
        for f in ("--mamba-radix-cache-strategy", "--mamba-ssm-dtype", "--max-mamba-cache-size")
    ] == ["extra_buffer", "bfloat16", "96"]
    for switch in (
        "--trust-remote-code",
        "--disable-prefill-cuda-graph",
        "--disable-flashinfer-autotune",
        "--enable-torch-compile",
        "--sleep-on-idle",
        "--enable-metrics",
    ):
        assert switch in server
