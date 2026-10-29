"""The image search and speech-to-text sidecars, and the image_search MCP tool, without Onyx
(specs/DREAMFERENCE_MIGHTLING_ASK.md §6, §7). No container is started: docker is a recorder."""

import io
import json
import os
import re
import subprocess

import pytest

from dreamference.chat import image_search_mcp, image_search_sidecar, speech_sidecar
from dreamference.chat.google_service import GOOGLE_SERVICE_IMAGE
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

    def __init__(self, states=None, networks=None, failing=None, network_exists=True):
        self.states = dict(states or {})
        self.networks = dict(networks or {})
        self.failing = dict(failing or {})  # argv prefix -> stderr of a command that fails
        self.network_exists = network_exists
        self.commands = []
        self.environments = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.commands.append(argv)
        self.environments.append(kwargs.get("env"))
        failed = [stderr for prefix, stderr in self.failing.items() if argv[: len(prefix)] == list(prefix)]
        if failed:
            return subprocess.CompletedProcess(argv, 1, "", failed[0])
        if argv[:2] == ["docker", "inspect"]:
            name, fmt = argv[2], argv[4]
            value = self.states.get(name, "") if "State" in fmt else self.networks.get(name, "")
            return subprocess.CompletedProcess(argv, 0 if name in self.states else 1, value + "\n", "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if self.network_exists else 1, f"{SIDECAR_NETWORK}\n", "")
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
    # The secret reaches the container through docker's environment, never its argv: any local
    # user can read a process's argv in /proc.
    assert "MIGHTLING_IMAGE_SECRET" in env
    assert not [arg for command in fake.commands for arg in command if secret in arg]
    assert fake.environments[fake.commands.index(run)]["MIGHTLING_IMAGE_SECRET"] == secret
    assert (store / "secret").stat().st_mode & 0o777 == 0o600
    assert (store / "service.py").is_file() and (store / "data" / "images").is_dir()
    # A second start keeps the secret: the MCP server and a running session read the same file.
    assert ImageSearchSidecar.secret() == secret


def test_the_python_sidecars_run_an_image_pinned_by_digest():
    # The Google service holds the user's OAuth tokens: a moving tag would run whatever the
    # registry serves next with them (specs/DREAMFERENCE_DOCKER.md).
    for image in (GOOGLE_SERVICE_IMAGE, image_search_sidecar.IMAGE_SEARCH_SERVICE_IMAGE):
        name, _, digest = image.partition("@sha256:")
        assert name == "python:3-slim", image
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), image


def test_the_image_search_service_installs_pillow_only_as_pinned_wheels(monkeypatch, store):
    # The digest pins the image, but Pillow comes from PyPI at every boot: unpinned, the next
    # release anyone publishes there would run beside the service's secret and image store.

    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured", siglip=False) is True
    (run,) = fake.runs(IMAGE_SEARCH_CONTAINER_NAME)
    script = run[run.index("-c") + 1]
    installs = [part for part in script.split(";") if "pip install" in part]
    assert installs and all("--require-hashes" in p and "--only-binary=:all:" in p for p in installs)
    requirement = image_search_sidecar.PILLOW_REQUIREMENT
    assert re.fullmatch(r"pillow==[0-9.]+( --hash=sha256:[0-9a-f]{64}){2}", requirement)
    assert f"'{requirement}'" in script


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
    # Only a container on another network is removed first.
    replaced = networks.get(STT_CONTAINER_NAME, SIDECAR_NETWORK) != SIDECAR_NETWORK
    assert (["docker", "rm", "-f", STT_CONTAINER_NAME] in fake.commands) is replaced


def test_speech_to_text_creates_the_sidecar_network_before_its_container(monkeypatch):
    fake = FakeDocker(network_exists=False)
    monkeypatch.setattr(subprocess, "run", fake)
    assert SpeechSidecar.start() is True
    create = ["docker", "network", "create", SIDECAR_NETWORK]
    assert fake.commands.index(create) < fake.commands.index(fake.runs(STT_CONTAINER_NAME)[0])


@pytest.mark.parametrize("states, failing, problem", [
    ({}, {("docker", "network", "create"): "denied"}, f"Could not create the Docker network {SIDECAR_NETWORK}."),
    ({}, {("docker", "run"): "pull access denied"}, "Could not start the speech-to-text server: pull access denied"),
    ({STT_CONTAINER_NAME: "exited"}, {("docker", "start"): "port is taken"},
     f"Could not start {STT_CONTAINER_NAME}: port is taken"),
])
def test_a_speech_to_text_start_that_fails_says_why(monkeypatch, states, failing, problem):
    fake = FakeDocker(states, dict.fromkeys(states, SIDECAR_NETWORK), failing, network_exists=False)
    monkeypatch.setattr(subprocess, "run", fake)
    assert SpeechSidecar.start() is False
    assert SpeechSidecar.problem == problem
    assert not any(c[:2] == ["docker", "exec"] for c in fake.commands)


def test_a_speech_to_text_start_forgets_the_last_failure(monkeypatch):
    monkeypatch.setattr(SpeechSidecar, "problem", "Could not start the speech-to-text server: earlier")
    monkeypatch.setattr(subprocess, "run", FakeDocker())
    assert SpeechSidecar.start() is True
    assert SpeechSidecar.problem == ""


def test_server_start_starts_speech_to_text_only_on_a_node_without_one(monkeypatch):
    from dreamference.node.node_identity import NodeIdentity

    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: None))
    assert SpeechSidecar.ensure_on_node() is None
    assert not fake.runs(STT_CONTAINER_NAME)
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: "node-1"))
    assert SpeechSidecar.ensure_on_node() is True
    # The model downloads in the container's background: the model server must not wait for it.
    assert fake.commands[-1][:4] == ["docker", "exec", "-d", STT_CONTAINER_NAME]
    assert SpeechSidecar.ensure_on_node() is None
    assert len(fake.runs(STT_CONTAINER_NAME)) == 1


def test_speech_to_text_stop_removes_the_container_and_says_when_one_is_left(monkeypatch):
    fake = FakeDocker({STT_CONTAINER_NAME: "running"}, {STT_CONTAINER_NAME: SIDECAR_NETWORK})
    monkeypatch.setattr(subprocess, "run", fake)
    assert "running on " + SIDECAR_NETWORK in SpeechSidecar.status_lines()[0]
    assert SpeechSidecar.stop() is True
    assert ["docker", "rm", "-f", STT_CONTAINER_NAME] in fake.commands
    stuck = FakeDocker({STT_CONTAINER_NAME: "running"}, failing={("docker", "rm"): "device busy"})
    monkeypatch.setattr(subprocess, "run", stuck)
    assert SpeechSidecar.stop() is False


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
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    # The one reader is asked for this server's parent; how it reads a seal is the crate's case.
    log = tmp_path / "asked"
    monkeypatch.setenv("FAKE_LING_AIRGAPPED_LOG", str(log))
    assert ImageSearchMcp.live_air_gapped() is True
    assert log.read_text() == f"airgapped --json --sealed-by {os.getppid()}\n"
    (runtime / "ling-airgapped" / "thread-1").write_text(f"{os.getppid() + 100000}\n")
    assert ImageSearchMcp.live_air_gapped() is False


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


def test_images_status_names_a_session_ling_admin_that_cannot_serve_the_tool(tmp_path, monkeypatch):
    # The launcher declares `~/.local/bin/ling-admin images mcp` whenever the link exists; one to an
    # installation without `images mcp` dropped the tool from every session in silence (2026-10-15).
    from dreamference.audit import EgressAudit

    secret = tmp_path / "secret"
    monkeypatch.setattr(image_search_sidecar, "IMAGE_SEARCH_SECRET_FILE", str(secret))
    link = tmp_path / "bin" / "ling-admin"
    monkeypatch.setattr(image_search_sidecar, "SESSION_ADMIN_LINK", str(link))
    assert "not set up" in ImageSearchSidecar.tool_line()
    secret.write_text("s3cret")
    assert "is missing" in ImageSearchSidecar.tool_line()
    link.parent.mkdir()
    link.write_text("#!/bin/sh\necho \"ling-admin: error: argument command: invalid choice: 'images'\" >&2\nexit 2\n")
    link.chmod(0o755)
    line = ImageSearchSidecar.tool_line()
    assert line.startswith("Tool: offered but broken:") and "invalid choice: 'images'" in line and "codex build" in line
    EgressAudit.write_admin(str(link))
    assert ImageSearchSidecar.tool_line() == "Tool: image_search is offered to ling sessions on this machine"


def test_the_probe_reads_only_a_tool_list_naming_image_search(tmp_path):
    other = tmp_path / "other"
    other.write_text('#!/bin/sh\nread line\necho \'{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"gmail_search"}]}}\'\n')
    other.chmod(0o755)
    assert "does not serve the image_search tool (exit 0)" in ImageSearchMcp.probe(str(other))
    assert "did not run" in ImageSearchMcp.probe(str(tmp_path / "absent"))


def test_the_probe_drops_pythonpath_as_codex_does(tmp_path, monkeypatch):
    # Run from a checkout with PYTHONPATH set, the stale link answered for that checkout's code
    # while every session (whose environment Codex filters) got main's: status said "offered".
    seen = tmp_path / "seen"
    admin = tmp_path / "ling-admin"
    admin.write_text(f'#!/bin/sh\necho "${{PYTHONPATH:-unset}}" > {seen}\nexit 2\n')
    admin.chmod(0o755)
    monkeypatch.setenv("PYTHONPATH", "/some/checkout")
    assert ImageSearchMcp.probe(str(admin))
    assert seen.read_text().strip() == "unset"
    assert ImageSearchMcp.probe(str(admin), {"PATH": os.environ["PATH"], "PYTHONPATH": "/x"})
    assert seen.read_text().strip() == "unset"


def test_a_secret_another_start_created_first_is_the_one_kept(monkeypatch, store):
    # Two first starts at once must agree on one secret, or the service and the MCP server differ.
    real = image_search_sidecar.secrets_module.token_urlsafe

    def racing(size):
        store.mkdir(parents=True, exist_ok=True)
        (store / "secret").write_text("theirs")
        return real(size)

    monkeypatch.setattr(image_search_sidecar.secrets_module, "token_urlsafe", racing)
    assert ImageSearchSidecar.secret() == "theirs"
    assert (store / "secret").read_text() == "theirs"


def test_a_request_whose_params_are_a_list_does_not_stop_the_server():
    # JSON-RPC allows positional params; one such message must not end the session's tool.
    server = ImageSearchMcp(search=lambda body: (200, {}), air_gapped=lambda: False)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["x"]},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = io.StringIO()
    server.run(io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]


# -- the image search sidecar's decisions, one by one ------------------------------------------


def _env(run):
    return [run[i + 1] for i, arg in enumerate(run) if arg == "-e"]


def test_an_existing_secret_is_read_and_no_new_one_made(monkeypatch, store):
    store.mkdir()
    (store / "secret").write_text("s3cret\n")

    def no_new_secret(size):
        pytest.fail("made a new secret although one was stored")

    monkeypatch.setattr(image_search_sidecar.secrets_module, "token_urlsafe", no_new_secret)
    assert ImageSearchSidecar.secret() == "s3cret"


def test_a_new_secret_lives_in_a_private_folder_without_a_temporary_file(store):
    assert ImageSearchSidecar.secret()
    assert store.stat().st_mode & 0o777 == 0o700
    assert sorted(path.name for path in store.iterdir()) == ["secret"]


def test_the_served_model_is_the_first_one_the_server_names(monkeypatch):
    def urlopen(url, timeout):
        assert url == "http://localhost:8000/v1/models"
        return io.BytesIO(json.dumps({"data": [{"id": "served/one"}, {"id": "other"}]}).encode())

    monkeypatch.setattr(image_search_sidecar.urllib.request, "urlopen", urlopen)
    assert ImageSearchSidecar.served_model("http://localhost:8000/", "fallback") == "served/one"


def test_the_service_runs_as_the_invoking_user_and_siglip_on_loopback_only(monkeypatch, store):
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    (run,) = fake.runs(IMAGE_SEARCH_CONTAINER_NAME)
    assert run[run.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    (siglip,) = fake.runs(SIGLIP_CONTAINER_NAME)
    assert siglip[siglip.index("-p") + 1] == "127.0.0.1:9100:9100"
    # Nothing was there to replace.
    assert ["docker", "rm", "-f", SIGLIP_CONTAINER_NAME] not in fake.commands


def test_a_running_siglip_on_the_sidecar_network_is_left_alone(monkeypatch, store):
    fake = FakeDocker({SIGLIP_CONTAINER_NAME: "running"}, {SIGLIP_CONTAINER_NAME: SIDECAR_NETWORK})
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    assert [c for c in fake.commands if SIGLIP_CONTAINER_NAME in c and c[:2] != ["docker", "inspect"]] == []
    (run,) = fake.runs(IMAGE_SEARCH_CONTAINER_NAME)
    assert f"MIGHTLING_SIGLIP_URL=http://{SIGLIP_CONTAINER_NAME}:9100" in _env(run)


@pytest.mark.parametrize("starts", [True, False])
def test_a_stopped_siglip_is_started_not_created_again(monkeypatch, store, starts):
    failing = {} if starts else {("docker", "start"): "port is taken"}
    fake = FakeDocker({SIGLIP_CONTAINER_NAME: "exited"}, {SIGLIP_CONTAINER_NAME: SIDECAR_NETWORK}, failing)
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    assert ["docker", "start", SIGLIP_CONTAINER_NAME] in fake.commands
    assert fake.runs(SIGLIP_CONTAINER_NAME) == []
    # Without it the service skips the pre-filter rather than calling a server that is not there.
    url = f"http://{SIGLIP_CONTAINER_NAME}:9100" if starts else ""
    assert f"MIGHTLING_SIGLIP_URL={url}" in _env(fake.runs(IMAGE_SEARCH_CONTAINER_NAME)[0])


def _health(monkeypatch, answers):
    calls, sleeps = [], []

    def urlopen(url, timeout):
        calls.append(url)
        if not answers.pop(0):
            raise OSError("connection refused")
        return io.BytesIO(b"ok")

    monkeypatch.setattr(image_search_sidecar.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(image_search_sidecar.time, "sleep", sleeps.append)
    return calls, sleeps


def test_the_health_check_asks_at_least_once(monkeypatch):
    calls, _ = _health(monkeypatch, [True])
    assert ImageSearchSidecar.healthy(wait_seconds=0) is True
    assert calls == ["http://127.0.0.1:8768/health"]


def test_the_health_check_waits_between_tries_but_not_after_the_last(monkeypatch):
    calls, sleeps = _health(monkeypatch, [False, False])
    assert ImageSearchSidecar.healthy(wait_seconds=2) is False
    assert len(calls) == 2 and sleeps == [1]


def test_a_start_forgets_the_last_failure(monkeypatch, store):
    monkeypatch.setattr(ImageSearchSidecar, "problem", "Could not start the image search service: earlier")
    monkeypatch.setattr(subprocess, "run", FakeDocker())
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is True
    assert ImageSearchSidecar.problem == ""


def test_a_start_without_a_secret_runs_nothing(monkeypatch, store):
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(ImageSearchSidecar, "secret", classmethod(lambda cls: None))
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is False
    assert fake.commands == [] and not (store / "service.py").exists()


@pytest.mark.parametrize("failing, problem", [
    ({("docker", "network", "create"): "denied"}, f"Could not create the Docker network {SIDECAR_NETWORK}."),
    ({("docker", "run", "-d", "--name", IMAGE_SEARCH_CONTAINER_NAME): "pull access denied"},
     "Could not start the image search service: pull access denied"),
])
def test_an_image_search_start_that_fails_says_why(monkeypatch, store, failing, problem):
    fake = FakeDocker(failing=failing, network_exists=False)
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured", siglip=False) is False
    assert ImageSearchSidecar.problem == problem


@pytest.mark.parametrize("wait", [True, False])
def test_only_a_start_that_waits_checks_the_health_route(monkeypatch, store, wait):
    monkeypatch.setattr(subprocess, "run", FakeDocker())
    monkeypatch.setattr(ImageSearchSidecar, "healthy", classmethod(lambda cls, wait_seconds=30: False))
    assert ImageSearchSidecar.start("http://localhost:8000", "configured", siglip=False, wait=wait) is not wait
    assert (ImageSearchSidecar.problem == "The image search service did not answer its health check.") is wait


def test_server_start_starts_no_image_search_off_a_node(monkeypatch, store):
    from dreamference.node.node_identity import NodeIdentity

    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: None))
    assert ImageSearchSidecar.ensure_on_node("http://localhost:8000", "m") is None
    assert fake.runs(IMAGE_SEARCH_CONTAINER_NAME) == []


def test_stop_removes_both_containers_and_says_when_siglip_is_left(monkeypatch):
    states = {IMAGE_SEARCH_CONTAINER_NAME: "running", SIGLIP_CONTAINER_NAME: "running"}
    fake = FakeDocker(states)
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.stop() is True
    assert ["docker", "rm", "-f", SIGLIP_CONTAINER_NAME] in fake.commands
    stuck = FakeDocker(states, failing={("docker", "rm", "-f", SIGLIP_CONTAINER_NAME): "device busy"})
    monkeypatch.setattr(subprocess, "run", stuck)
    assert ImageSearchSidecar.stop() is False


def test_status_names_each_containers_network_and_counts_only_stored_images(monkeypatch, store):
    fake = FakeDocker({IMAGE_SEARCH_CONTAINER_NAME: "running"}, {IMAGE_SEARCH_CONTAINER_NAME: SIDECAR_NETWORK})
    monkeypatch.setattr(subprocess, "run", fake)
    images = store / "data" / "images"
    images.mkdir(parents=True)
    (images / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8\xff")
    (images / "0123456789abcdef.jpg.tmp").write_bytes(b"\xff")
    lines = ImageSearchSidecar.status_lines()
    assert lines[0] == f"Image search ({IMAGE_SEARCH_CONTAINER_NAME}): running on {SIDECAR_NETWORK}"
    assert lines[1] == f"SigLIP pre-filter ({SIGLIP_CONTAINER_NAME}): absent"
    assert lines[2] == f"Store: {images} (1 images)"


# -- the image_search tool's decisions, one by one ----------------------------------------------


@pytest.mark.parametrize("stored, secret", [("s3cret\n", "s3cret"), (" \n", None)])
def test_the_tools_secret_is_read_without_its_line_end(store, stored, secret):
    store.mkdir()
    (store / "secret").write_text(stored)
    assert ImageSearchMcp.secret() == secret


def test_a_search_goes_to_loopback_with_the_secret(monkeypatch, store):
    store.mkdir()
    (store / "secret").write_text("s3cret\n")
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        response = io.BytesIO(b'{"response": "none"}')
        response.status = 200
        return response

    monkeypatch.setattr(image_search_mcp.urllib.request, "urlopen", urlopen)
    assert ImageSearchMcp.live_search({"queries": ["x"], "count": 1}) == (200, {"response": "none"})
    (request,) = sent
    assert request.full_url == "http://127.0.0.1:8768/search" and request.get_method() == "POST"
    assert request.get_header(image_search_mcp.AUTH_HEADER.capitalize()) == "s3cret"
    assert json.loads(request.data) == {"queries": ["x"], "count": 1}


def test_a_refused_search_keeps_its_status(monkeypatch, store):
    import urllib.error

    store.mkdir()
    (store / "secret").write_text("s3cret")

    def refused(request, timeout):
        body = io.BytesIO(b'{"error": "bad token"}')
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    monkeypatch.setattr(image_search_mcp.urllib.request, "urlopen", refused)
    assert ImageSearchMcp.live_search({"queries": ["x"], "count": 1}) == (401, {"error": "bad token"})


def test_no_seal_folder_without_an_absolute_runtime_folder(monkeypatch, tmp_path):
    # A relative XDG_RUNTIME_DIR names a folder in whatever directory the session runs in.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run").mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "run")
    monkeypatch.setattr(image_search_mcp.os, "getuid", lambda: 4000000000)
    assert ImageSearchMcp.seal_dir() is None
    assert ImageSearchMcp.sealed_by(os.getppid()) is False


def test_a_seal_folder_that_is_not_there_seals_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ImageSearchMcp.seal_dir() == str(tmp_path / "ling-airgapped")
    assert ImageSearchMcp.sealed_by(os.getppid()) is False


def test_without_a_usable_runtime_folder_the_seals_are_looked_for_where_rust_puts_them(monkeypatch, tmp_path):
    # ling-rs/airgapped's runtime_dir: XDG_RUNTIME_DIR if absolute and a folder, else /run/user/<uid>.
    real = os.path.isdir
    monkeypatch.setattr(image_search_mcp.os, "getuid", lambda: 4000000000)
    monkeypatch.setattr(image_search_mcp.os.path, "isdir", lambda p: p == "/run/user/4000000000" or real(p))
    for runtime in ("run", str(tmp_path / "missing")):
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        assert ImageSearchMcp.seal_dir() == "/run/user/4000000000/ling-airgapped"


def test_an_unreadable_seal_does_not_hide_the_sessions_own(monkeypatch, tmp_path):
    seals = tmp_path / "ling-airgapped"
    (seals / "a-folder").mkdir(parents=True)
    (seals / "thread-1").write_text(f"{os.getppid()}\n")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    listdir = os.listdir
    monkeypatch.setattr(image_search_mcp.os, "listdir", lambda path=".": sorted(listdir(path)))
    assert ImageSearchMcp.sealed_by(os.getppid()) is True


def _tool(answer=None):
    sent = []

    def search(body):
        sent.append(body)
        return 200, answer or {"response": "![t](/images/0123456789abcdef.jpg)"}

    return ImageSearchMcp(search=search, air_gapped=lambda: False), sent


def test_an_unknown_tool_searches_nothing():
    server, sent = _tool()
    result = server.call("gmail_search", {"queries": ["x"]})
    assert result == {"content": [{"type": "text", "text": "Unknown tool gmail_search."}], "isError": True}
    assert sent == []


@pytest.mark.parametrize("arguments, body", [
    ({"queries": "puffins"}, {"queries": ["puffins"], "count": 4}),
    ({"queries": [str(n) for n in range(7)]}, {"queries": ["0", "1", "2", "3", "4"], "count": 4}),
    ({"queries": ["x"], "count": 0}, {"queries": ["x"], "count": 1}),
    ({"queries": ["x"], "count": -3}, {"queries": ["x"], "count": 1}),
    ({"queries": ["x"], "count": True}, {"queries": ["x"], "count": 4}),
])
def test_a_calls_arguments_are_made_into_a_search_the_service_takes(arguments, body):
    server, sent = _tool()
    assert server.call(TOOL_NAME, arguments)["isError"] is False
    assert sent == [body]


def test_the_note_on_titles_comes_only_with_images():
    server, _ = _tool({"response": "No image matched."})
    assert server.call(TOOL_NAME, {"queries": ["x"]})["content"][0]["text"] == "No image matched."


def test_arguments_that_are_not_an_object_count_as_none():
    server, sent = _tool()
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": TOOL_NAME, "arguments": ["x"]}})
    assert reply["result"]["isError"] and "at least one search text" in reply["result"]["content"][0]["text"]
    assert sent == []


def test_blank_lines_and_messages_that_are_not_objects_get_no_reply():
    server, _ = _tool()
    source = io.StringIO("\n42\n" + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
    out = io.StringIO()
    server.run(source, out)
    assert [json.loads(line) for line in out.getvalue().splitlines()] == [{"jsonrpc": "2.0", "id": 7, "result": {}}]


def _probe(monkeypatch, outcome):
    def run(argv, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, outcome, "")

    monkeypatch.setattr(image_search_mcp.subprocess, "run", run)
    return ImageSearchMcp.probe("/opt/ling-admin", {})


def test_a_probe_that_times_out_says_it_did_not_run(monkeypatch):
    problem = _probe(monkeypatch, subprocess.TimeoutExpired(["ling-admin"], 30))
    assert problem.startswith("`/opt/ling-admin images mcp` did not run")


@pytest.mark.parametrize("stdout", [
    "not json\n",
    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": 5}}) + "\n",
])
def test_a_probe_answered_without_a_tool_list_says_so(monkeypatch, stdout):
    assert "does not serve the image_search tool (exit 0)" in _probe(monkeypatch, stdout)


def test_a_secret_that_cannot_be_stored_is_none_with_the_reason(monkeypatch, tmp_path):
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    monkeypatch.setattr(image_search_sidecar, "IMAGE_SEARCH_DATA_DIR", str(blocked / "image-search"))
    monkeypatch.setattr(image_search_sidecar, "IMAGE_SEARCH_SECRET_FILE", str(blocked / "image-search" / "secret"))
    monkeypatch.setattr(ImageSearchSidecar, "problem", "")
    assert ImageSearchSidecar.secret() is None
    assert ImageSearchSidecar.problem.startswith("Could not store the image search secret")


def test_an_empty_secret_another_start_left_is_replaced_not_kept(monkeypatch, store):
    real = image_search_sidecar.secrets_module.token_urlsafe
    made = []

    def racing(size):
        store.mkdir(parents=True, exist_ok=True)
        (store / "secret").write_text("\n")
        made.append(real(size))
        return made[0]

    monkeypatch.setattr(image_search_sidecar.secrets_module, "token_urlsafe", racing)
    assert ImageSearchSidecar.secret() == made[0]
    assert (store / "secret").read_text() == made[0]
    assert sorted(path.name for path in store.iterdir()) == ["secret"]


def test_a_service_that_cannot_be_staged_is_not_started(monkeypatch, store):
    store.mkdir()
    (store / "data").write_text("")  # where the image store should be made
    monkeypatch.setattr(ImageSearchSidecar, "problem", "")
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    assert ImageSearchSidecar.start("http://localhost:8000", "configured") is False
    assert ImageSearchSidecar.problem.startswith("Could not stage the image search service")
    assert fake.commands == []
