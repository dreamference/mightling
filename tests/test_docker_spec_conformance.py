"""specs/DREAMFERENCE_DOCKER.md, the parts no other test pinned down.

The `docker run` lines of §4.2 for each engine, the loading monitor that follows a launch, what
`server stop` and `server remove` take down, and the failure paths of §1 and §2. Nothing here runs
Docker, downloads or reaches a model server: commands are built and compared, and every call that
would leave the process is stubbed.
"""

import itertools

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, ENGINE_LING
from dreamference.vllm_server import (
    DiffusionServerManager,
    VLLMServerManager,
    model_loading_monitor,
    vllm_server_manager,
)
from dreamference.vllm_server.model_loading_monitor import (
    ModelLoadingMonitor,
    create_model_loading_monitor,
)

# The vLLM-engine entry (tests/test_flash_next_recipe.py) and its repository.
VLLM_MODEL = "qwen3.8-flash-next-nvfp4-mtp"
VLLM_REPO = "local-inference-lab/Qwen3.8-Flash-Next-NVFP4"
TOKEN = "hf_conformance_token"


def _gb10(cls):
    return HardwareTelemetry(
        is_gb10=True,
        gpu_name="NVIDIA GB10",
        driver_version="0",
        arch="aarch64",
        total_unified_memory_gb=121.6,
        available_memory_gb=115.0,
        used_memory_gb=6.6,
        vram_gb=0.0,
    )


@pytest.fixture
def commands(monkeypatch):
    """The three engines' launch commands, each built with a HuggingFace token."""
    from dreamference.hardware.hardware_manager import HardwareManager

    monkeypatch.setattr(HardwareManager, "detect_gb10_hardware", classmethod(_gb10))
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: None)
    return {
        "sglang": mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, hf_token=TOKEN),
        "ling": mgr.build_launch_command(
            model=DEFAULT_MODEL_ALIAS, hf_token=TOKEN, engine=ENGINE_LING
        ),
        "vllm": mgr.build_launch_command(model=VLLM_MODEL, hf_token=TOKEN),
    }


def _envs(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-e"]


def _mounts(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-v"]


# -- §4.2: the docker run blocks -------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["sglang", "ling", "vllm"])
def test_every_engine_runs_in_the_same_guarded_container(commands, engine):
    cmd = commands[engine]
    assert cmd[:2] == ["docker", "run"]
    for literal in ("--ipc=host", "--oom-score-adj=800"):
        assert literal in cmd
    for flag, value in (
        ("--network", "host"),
        ("--restart", "unless-stopped"),
        ("--name", "dreamference-vllm-8000"),
        ("--gpus", "all"),
    ):
        assert cmd[cmd.index(flag) + 1] == value, flag
    assert any(a.startswith("--cpus=") for a in cmd)
    memory = next(a for a in cmd if a.startswith("--memory="))
    # Swap capped at the memory cap: the container cannot spill into swap.
    assert f"--memory-swap={memory.split('=', 1)[1]}" in cmd
    mounts = _mounts(cmd)
    assert any(m.endswith(":/root/.cache/huggingface") for m in mounts)
    assert any(m.endswith(":/root/.cache/dreamference") for m in mounts)
    envs = _envs(cmd)
    assert "VLLM_NO_USAGE_STATS=1" in envs and "DO_NOT_TRACK=1" in envs


@pytest.mark.parametrize("engine", ["sglang", "ling", "vllm"])
def test_the_token_value_is_never_on_a_command_line(commands, engine):
    assert not any(TOKEN in a for a in commands[engine])


def test_the_token_goes_by_name_to_sglang_and_vllm_and_not_to_ling_serve(commands):
    # ling-serve reads only the snapshot it is pointed at; it has no use for the token.
    assert "HF_TOKEN" in _envs(commands["sglang"])
    assert "HF_TOKEN" in _envs(commands["vllm"])
    assert not any(e.startswith("HF_TOKEN") for e in _envs(commands["ling"]))


def test_sglang_runs_offline_with_its_compile_cache_under_the_mount(commands):
    envs = _envs(commands["sglang"])
    assert "HF_HUB_OFFLINE=1" in envs
    assert "TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor" in envs
    cmd = commands["sglang"]
    assert cmd[cmd.index("-m") - 1 : cmd.index("-m") + 2] == [
        "python3",
        "-m",
        "sglang.launch_server",
    ]


def test_vllm_gets_its_kernel_and_logging_env_and_serves_through_its_entrypoint(commands):
    cmd = commands["vllm"]
    envs = _envs(cmd)
    for expected in (
        "CUTE_DSL_ARCH=sm_121a",
        "VLLM_LOGGING_LEVEL=DEBUG",
        "VLLM_DEBUG_LOG_API_SERVER_RESPONSE=1",
        "VLLM_DEBUG_LOG_API_SERVER_REQUEST=1",
    ):
        assert expected in envs
    assert any(e.startswith("VLLM_CACHE_ROOT=/root/.cache/dreamference/") for e in envs)
    at = cmd.index("--entrypoint")
    assert cmd[at + 1] == "vllm" and cmd[at + 3 : at + 5] == ["serve", VLLM_REPO]
    # Every option precedes the image; the -e flags all sit in the docker part.
    assert max(i for i, a in enumerate(cmd) if a == "-e") < at


# -- §4.2: after the launch ------------------------------------------------------------------------


class _Clock:
    """A clock whose sleep moves time on, and that ends the loop after `stop_at` seconds."""

    def __init__(self, monitor, stop_at):
        self.now = 1000.0
        self.monitor = monitor
        self.stop_at = self.now + stop_at

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.now >= self.stop_at:
            self.monitor.running = False


def test_the_monitor_prints_memory_every_ten_seconds_and_polls_health(monkeypatch, capsys):
    printed_at, health_at = [], []
    monitor = ModelLoadingMonitor(
        get_logs_fn=list,
        check_health_fn=lambda: health_at.append(clock.now) or False,
        get_memory_fn=lambda: printed_at.append(clock.now) or "12.3 GiB",
    )
    clock = _Clock(monitor, stop_at=35)
    monkeypatch.setattr(model_loading_monitor, "time", clock)
    monitor.running = True
    monitor._monitor_loop()

    gaps = [b - a for a, b in itertools.pairwise(printed_at)]
    assert len(printed_at) >= 3 and all(10 <= g < 11 for g in gaps), gaps
    assert "📊 Reserved memory (Docker): 12.3 GiB" in capsys.readouterr().out
    # Health is asked far more often than memory is printed.
    assert len(health_at) > 2 * len(printed_at)


def test_the_monitor_asks_the_server_its_models_then_for_one_token(monkeypatch):
    import contextlib
    import urllib.request

    import requests

    asked = []

    class Response:
        status_code = 200
        status = 200

        def json(self):
            return {"data": [{"id": "org/model"}]}

    monkeypatch.setattr(requests, "get", lambda url, timeout: asked.append(url) or Response())
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: asked.append(req.full_url) or contextlib.nullcontext(Response()),
    )
    mgr = VLLMServerManager(host="http://localhost:8000")
    monitor = create_model_loading_monitor(mgr)
    assert monitor._check_server_health() is True and monitor.server_ready
    assert asked == ["http://localhost:8000/v1/models", "http://localhost:8000/v1/chat/completions"]


# -- §4.2: stop and remove -------------------------------------------------------------------------


def _record_docker(monkeypatch):
    from dreamference.vllm_server.model_gate import ModelGate

    calls = []
    monkeypatch.setattr(vllm_server_manager.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(
        ModelGate, "stop", classmethod(lambda cls, port: calls.append(("gate stop", port)))
    )
    monkeypatch.setattr(
        ModelGate, "remove", classmethod(lambda cls, port: calls.append(("gate remove", port)))
    )
    return calls


def test_stop_stops_the_container_and_its_gate(monkeypatch):
    calls = _record_docker(monkeypatch)
    VLLMServerManager().stop_server(port=8000)
    assert calls == [["docker", "stop", "dreamference-vllm-8000"], ("gate stop", 8000)]


def test_remove_removes_the_container_and_its_gate(monkeypatch):
    calls = _record_docker(monkeypatch)
    VLLMServerManager().remove_server(port=8000)
    assert calls == [["docker", "rm", "-f", "dreamference-vllm-8000"], ("gate remove", 8000)]


def test_server_remove_also_removes_a_leftover_diffusion_sidecar(monkeypatch):
    removed = []
    monkeypatch.setattr(VLLMServerManager, "remove_server", lambda self, port=8000: None)
    monkeypatch.setattr(
        DiffusionServerManager,
        "remove_leftover",
        classmethod(lambda cls, port=8001: removed.append(port)),
    )
    DreamferenceCLIController.run_cli(["server", "remove"])
    assert removed == [8001]


# -- §1 and §2: downloads and clearing -------------------------------------------------------------


def test_model_list_downloads_nothing(monkeypatch, capsys):
    import huggingface_hub

    def no_download(*a, **k):
        raise AssertionError("model list downloaded")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", no_download)
    monkeypatch.setattr(ModelDownloader, "download_model", classmethod(no_download))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "list"])
    assert exit_info.value.code == 0


def test_a_failed_download_prints_a_note_and_returns_false(monkeypatch, capsys):
    import huggingface_hub

    def offline(**kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a: False))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", offline)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert ModelDownloader.download_model("org/model") is False
    out = capsys.readouterr().out
    assert "⚠️ HuggingFace snapshot_download note: network unreachable" in out


def test_clear_model_cache_names_what_it_could_not_remove_and_exits_1(
    tmp_path, monkeypatch, capsys
):
    hf = tmp_path / "huggingface"
    (hf / "hub" / "models--x").mkdir(parents=True)
    (hf / "token").write_text("hf_secret")
    monkeypatch.setenv("HF_HOME", str(hf))
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    # Root-owned leftovers: nothing the user may delete goes.
    monkeypatch.setattr("shutil.rmtree", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["clear", "model-cache"])
    assert exit_info.value.code == 1
    assert f"sudo rm -rf {hf / 'hub'}" in capsys.readouterr().out
    assert (hf / "token").read_text() == "hf_secret"


# -- §6: the gate and the sidecars -----------------------------------------------------------------


def test_the_gate_is_small_unswappable_and_runs_as_the_user():
    import os

    from dreamference.vllm_server.model_gate import ModelGate

    cmd = ModelGate.run_command(8000, "lmsysorg/sglang@sha256:abc")
    assert "--memory=256m" in cmd and "--memory-swap=256m" in cmd and "--cpus=2" in cmd
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def _sidecar_commands(directory):
    from dreamference.chat.google_service import GoogleService
    from dreamference.chat.image_search_sidecar import ImageSearchSidecar
    from dreamference.chat.sidecar_network import SIDECAR_NETWORK
    from dreamference.chat.speech_sidecar import SpeechSidecar

    return SIDECAR_NETWORK, {
        "google": GoogleService.run_command(str(directory)),
        "image-search": ImageSearchSidecar.run_command(
            "http://127.0.0.1:8000/v1", "org/model", True
        ),
        "siglip": ImageSearchSidecar.siglip_command(),
        "speech": SpeechSidecar.run_command(),
    }


@pytest.mark.parametrize("name", ["google", "image-search", "siglip", "speech"])
def test_a_sidecar_is_on_its_network_published_on_loopback_and_keeps_dockers_dns(name, tmp_path):
    # Loopback is hard-coded: advertising the node opens SearXNG and the web UI, never these.
    network, commands = _sidecar_commands(tmp_path / "google")
    cmd = commands[name]
    assert cmd[cmd.index("--network") + 1] == network
    publishes = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-p"]
    assert publishes and all(p.startswith("127.0.0.1:") for p in publishes), publishes
    assert "--dns" not in cmd
