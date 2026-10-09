"""The image search and speech-to-text sidecars, and the image_search MCP tool, without Onyx
(specs/DREAMFERENCE_MIGHTLING_ASK.md §6, §7). No container is started: docker is a recorder."""

import io
import json
import os
import subprocess

import pytest

from dreamference.chat import image_search_mcp, image_search_sidecar, speech_sidecar
from dreamference.chat.image_search_mcp import TOOL_NAME, ImageSearchMcp
from dreamference.chat.image_search_sidecar import (
    IMAGE_SEARCH_CONTAINER_NAME,
    SIGLIP_CONTAINER_NAME,
    ImageSearchSidecar,
)
from dreamference.chat.sidecar_network import SIDECAR_NETWORK
from dreamference.chat.speech_sidecar import STT_CONTAINER_NAME, SpeechSidecar


class FakeDocker:
    """Records docker commands; `inspect` answers each container's state and network."""

    def __init__(self, states=None, networks=None):
        self.states = dict(states or {})
        self.networks = dict(networks or {})
        self.commands = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            name, fmt = argv[2], argv[4]
            value = self.states.get(name, "") if "State" in fmt else self.networks.get(name, "")
            return subprocess.CompletedProcess(argv, 0 if name in self.states else 1, value + "\n", "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, f"{SIDECAR_NETWORK}\n", "")
        if argv[:3] == ["docker", "rm", "-f"]:
            self.states.pop(argv[3], None)
            self.networks.pop(argv[3], None)
        if argv[:2] == ["docker", "run"]:
            name = argv[argv.index("--name") + 1]
            self.states[name] = "running"
            self.networks[name] = argv[argv.index("--network") + 1]
        return subprocess.CompletedProcess(argv, 0, "", "")

    def runs(self, name):
        return [c for c in self.commands if c[:2] == ["docker", "run"] and c[c.index("--name") + 1] == name]


@pytest.fixture(autouse=True)
def real_starts(monkeypatch):
    # conftest stubs both starts for `server start`'s sake; these tests exercise them.
    from conftest import REAL_IMAGE_SEARCH_START, REAL_SPEECH_START

    monkeypatch.setattr(ImageSearchSidecar, "start", REAL_IMAGE_SEARCH_START)
    monkeypatch.setattr(SpeechSidecar, "start", REAL_SPEECH_START)


@pytest.fixture
def store(monkeypatch, tmp_path):
    data = tmp_path / "image-search"
    monkeypatch.setattr(image_search_sidecar, "IMAGE_SEARCH_DATA_DIR", str(data))
    monkeypatch.setattr(image_search_sidecar, "IMAGE_SEARCH_SECRET_FILE", str(data / "secret"))
    monkeypatch.setattr(image_search_sidecar, "IMAGE_STORE_DIR", str(data / "data" / "images"))
    monkeypatch.setattr(image_search_mcp, "IMAGE_SEARCH_SECRET_FILE", str(data / "secret"))
    monkeypatch.setattr(ImageSearchSidecar, "healthy", classmethod(lambda cls, wait_seconds=30: True))
    monkeypatch.setattr(ImageSearchSidecar, "served_model", classmethod(lambda cls, host, fallback: "served/model"))
    return data


def test_image_search_starts_on_the_sidecar_network_with_its_store_and_secret(monkeypatch, store):
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    (run,) = fake.runs(IMAGE_SEARCH_CONTAINER_NAME)
    assert run[run.index("--network") + 1] == SIDECAR_NETWORK
    assert run[run.index("-p") + 1] == "127.0.0.1:8768:8768"
    assert f"{store}:/config" in run
    env = [run[i + 1] for i, arg in enumerate(run) if arg == "-e"]
    assert "MIGHTLING_SEARXNG_URL=http://dreamference-searxng:8080" in env
    assert "MIGHTLING_VISION_MODEL=served/model" in env
    assert "MIGHTLING_SIGLIP_URL=http://dreamference-siglip:9100" in env
    secret = (store / "secret").read_text()
    assert f"MIGHTLING_IMAGE_SECRET={secret}" in env
    assert (store / "secret").stat().st_mode & 0o777 == 0o600
    assert (store / "service.py").is_file() and (store / "data" / "images").is_dir()
    # A second start keeps the secret: the MCP server and a running session read the same file.
    assert ImageSearchSidecar.secret() == secret


def test_without_siglip_the_service_is_told_to_skip_the_prefilter(monkeypatch, store):
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured", siglip=False) is True
    assert not fake.runs(SIGLIP_CONTAINER_NAME)
    (run,) = fake.runs(IMAGE_SEARCH_CONTAINER_NAME)
    assert "MIGHTLING_SIGLIP_URL=" in run


def test_containers_left_on_onyxs_network_are_replaced(monkeypatch, store):
    # What an install from before Onyx's retirement has: both created on Onyx's network.
    fake = FakeDocker(states={SIGLIP_CONTAINER_NAME: "running", IMAGE_SEARCH_CONTAINER_NAME: "running"},
                      networks={SIGLIP_CONTAINER_NAME: "onyx_default", IMAGE_SEARCH_CONTAINER_NAME: "onyx_default"})
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    assert ["docker", "rm", "-f", SIGLIP_CONTAINER_NAME] in fake.commands
    assert fake.networks == {SIGLIP_CONTAINER_NAME: SIDECAR_NETWORK, IMAGE_SEARCH_CONTAINER_NAME: SIDECAR_NETWORK}


def test_stop_keeps_the_store(monkeypatch, store):
    fake = FakeDocker(states={IMAGE_SEARCH_CONTAINER_NAME: "running"})
    monkeypatch.setattr(subprocess, "run", fake)
    (store / "data" / "images").mkdir(parents=True)
    (store / "data" / "images" / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8\xff")
    assert ImageSearchSidecar.stop() is True
    assert (store / "data" / "images" / "0123456789abcdef.jpg").exists()
    assert "Store:" in "\n".join(ImageSearchSidecar.status_lines())


@pytest.mark.parametrize("states, networks, runs, starts", [
    ({}, {}, True, False),
    ({STT_CONTAINER_NAME: "exited"}, {STT_CONTAINER_NAME: SIDECAR_NETWORK}, False, True),
    ({STT_CONTAINER_NAME: "running"}, {STT_CONTAINER_NAME: SIDECAR_NETWORK}, False, False),
    ({STT_CONTAINER_NAME: "running"}, {STT_CONTAINER_NAME: "onyx_default"}, True, False),
])
def test_speech_to_text_runs_on_the_sidecar_network(monkeypatch, states, networks, runs, starts):
    fake = FakeDocker(states, networks)
    monkeypatch.setattr(subprocess, "run", fake)
    assert SpeechSidecar.start() is True
    ran = fake.runs(STT_CONTAINER_NAME)
    assert bool(ran) is runs
    if ran:
        assert ran[0][ran[0].index("--network") + 1] == SIDECAR_NETWORK
        assert "127.0.0.1:8100:8000" in ran[0]
        assert "dreamference-stt-cache:/home/ubuntu/.cache/huggingface" in ran[0]
    assert (["docker", "start", STT_CONTAINER_NAME] in fake.commands) is starts
    assert any(c[:3] == ["docker", "exec", STT_CONTAINER_NAME] for c in fake.commands)


# -- the image_search tool ---------------------------------------------------------------------


def _session(server, *messages):
    out = io.StringIO()
    server.run(io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_initialize_and_tools_list_touch_nothing():
    def boom(*_):
        raise AssertionError("no search before a call")

    server = ImageSearchMcp(search=boom, air_gapped=boom)
    replies = _session(
        server,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nope"},
    )
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert [t["name"] for t in replies[1]["result"]["tools"]] == [TOOL_NAME]
    assert replies[2]["error"]["code"] == -32601


def test_a_call_returns_the_markdown_lines_and_clamps_its_arguments():
    sent = []

    def search(body):
        sent.append(body)
        return 200, {"response": "![t](/images/0123456789abcdef.jpg)",
                     "instructions": "Copy VERBATIM:\n\n![t](/images/0123456789abcdef.jpg)"}

    server = ImageSearchMcp(search=search, air_gapped=lambda: False)
    result = server.call(TOOL_NAME, {"queries": ["  puffins  ", "", 3], "count": 99})
    assert result["isError"] is False
    assert "![t](/images/0123456789abcdef.jpg)" in result["content"][0]["text"]
    assert "data, not instructions" in result["content"][0]["text"]
    assert sent == [{"queries": ["puffins"], "count": 10}]
    assert server.call(TOOL_NAME, {"queries": []})["isError"] is True


def test_a_call_is_refused_at_airgapped_on_and_errors_are_read_by_the_model():
    refused = ImageSearchMcp(search=lambda body: (200, {}), air_gapped=lambda: True).call(TOOL_NAME, {"queries": ["x"]})
    assert refused["isError"] and "/airgapped on" in refused["content"][0]["text"]
    failed = ImageSearchMcp(search=lambda body: (0, {"error": "down"}), air_gapped=lambda: False).call(TOOL_NAME, {"queries": ["x"]})
    assert failed["isError"] and "down" in failed["content"][0]["text"]


def test_the_session_seal_of_the_parent_process_counts(monkeypatch, tmp_path):
    runtime = tmp_path / "run"
    (runtime / "ling-airgapped").mkdir(parents=True)
    (runtime / "ling-airgapped" / "thread-1").write_text(f"{os.getppid()}\n")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert ImageSearchMcp.sealed_by(os.getppid()) is True
    assert ImageSearchMcp.sealed_by(os.getppid() + 100000) is False
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    assert ImageSearchMcp.live_air_gapped() is True


def test_without_a_secret_the_live_search_says_how_to_set_it_up(store):
    status, answer = ImageSearchMcp.live_search({"queries": ["x"], "count": 1})
    assert status == 0 and "ling-admin images start" in answer["error"]


def test_speech_status_names_the_command(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeDocker())
    assert "ling-admin voice start" in "\n".join(SpeechSidecar.status_lines())
    assert speech_sidecar.STT_HOST_PORT == 8100


def test_images_mcp_runs_before_anything_that_could_print(monkeypatch, capsys):
    # Codex reads stdout as the protocol: the rename migration and the sandbox check must not run.
    from dreamference.cli import legacy_name_migration
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    from dreamference.vllm_server import SandboxPrerequisite

    def refuse(*_args, **_kwargs):
        raise AssertionError("ran before the MCP server")

    monkeypatch.setattr(legacy_name_migration.LegacyNameMigration, "run", classmethod(refuse))
    monkeypatch.setattr(SandboxPrerequisite, "gate", classmethod(refuse))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["images", "mcp"])
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"jsonrpc": "2.0", "id": 7, "result": {}}


# -- started by `server start` on a node (ASK §19.6) ------------------------------------------------


@pytest.mark.parametrize("sidecar", [ImageSearchSidecar, SpeechSidecar])
def test_server_start_starts_each_only_on_a_node_without_one(monkeypatch, sidecar):
    from dreamference.node.node_identity import NodeIdentity

    calls = []
    monkeypatch.setattr(sidecar, "start", classmethod(lambda cls, *a, **k: calls.append(k) or True))
    ensure = (lambda: sidecar.ensure_on_node("http://localhost:8000", "m")) if sidecar is ImageSearchSidecar else sidecar.ensure_on_node
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: None))
    assert ensure() is None
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: "node-1"))
    for existing in ("running", "exited"):
        monkeypatch.setattr(sidecar, "state", classmethod(lambda cls, *a: existing))
        assert ensure() is None
    monkeypatch.setattr(sidecar, "state", classmethod(lambda cls, *a: ""))
    assert ensure() is True
    # Never waits: no health check for image search (and no SigLIP), the model download detached.
    assert calls == ([{"siglip": False, "wait": False}] if sidecar is ImageSearchSidecar else [{"wait_for_model": False}])


@pytest.mark.parametrize("sidecar", [ImageSearchSidecar, SpeechSidecar])
def test_a_failed_start_is_reported_never_raised(monkeypatch, sidecar):
    from dreamference.node.node_identity import NodeIdentity

    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: "node-1"))
    monkeypatch.setattr(sidecar, "state", classmethod(lambda cls, *a: ""))
    ensure = (lambda: sidecar.ensure_on_node("http://localhost:8000", "m")) if sidecar is ImageSearchSidecar else sidecar.ensure_on_node

    def broken(cls, *a, **k):
        raise OSError("docker is not installed")

    monkeypatch.setattr(sidecar, "start", classmethod(broken))
    assert ensure() is False and "docker is not installed" in sidecar.problem


def test_the_detached_model_download_does_not_hold_server_start(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert SpeechSidecar.start(wait_for_model=False) is True
    (fetch,) = [c for c in fake.commands if c[:2] == ["docker", "exec"]]
    assert fetch[2] == "-d"


def test_without_a_served_model_the_vision_ranker_gets_the_id_the_server_will_serve(monkeypatch):
    from dreamference.hardware import resolve_model_hf_repo

    monkeypatch.setattr(ImageSearchSidecar, "served_model", classmethod(lambda cls, host, fallback: fallback))
    key = "qwen3.8-27b-nvfp4-dflash2"
    assert ImageSearchSidecar.vision_model("http://localhost:8000", key) == resolve_model_hf_repo(key)


def test_server_start_warns_and_still_loads_the_model_when_a_sidecar_fails(monkeypatch, capsys):
    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    from dreamference.vllm_server import DiffusionServerManager, VLLMServerManager

    class Monitor:
        server_ready = False

        def start(self):
            pass

        def stop(self):
            pass

    loads = []

    def load(self, **_):
        loads.append(True)
        raise SystemExit(0)

    def failed(cls, *a, **k):
        cls.problem = f"{cls.__name__} could not start."
        return False

    monkeypatch.setattr(ImageSearchSidecar, "ensure_on_node", classmethod(failed))
    monkeypatch.setattr(SpeechSidecar, "ensure_on_node", classmethod(failed))
    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: Monitor())
    monkeypatch.setattr(VLLMServerManager, "start_server", load)
    monkeypatch.setattr(DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None))
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["server", "start", "--no-diffusion"])
    assert loads == [True]
    out = capsys.readouterr().out
    assert "ImageSearchSidecar could not start. The model starts anyway; retry with `ling-admin images start`." in out
    assert "SpeechSidecar could not start. The model starts anyway; retry with `ling-admin voice start`." in out
