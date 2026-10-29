"""Behaviour found wrong in the 2026-10-29 review of `dreamference/vllm_server/`."""

import pytest

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
from dreamference.vllm_server import VLLMServerManager

SECRET = "hf_review_secret_value"


@pytest.mark.parametrize("model", ["qwen2.5-coder-32b", DEFAULT_MODEL_ALIAS])
def test_the_launch_command_names_the_token_but_never_carries_it(monkeypatch, model):
    # start_server prints the whole command; the token reaches the container through the
    # environment of the `docker run` it spawns, which start_server sets.
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model=model, hf_token=SECRET)
    assert not any(SECRET in part for part in cmd)
    at = cmd.index("HF_TOKEN")
    assert cmd[at - 1] == "-e"


def test_a_failed_compile_cache_clear_is_tried_again_on_the_next_launch(monkeypatch, tmp_path):
    import subprocess

    from dreamference.vllm_server import vllm_server_manager as vsm

    monkeypatch.setattr(vsm, "VLLM_CACHE_HOME", tmp_path)
    (tmp_path / "torch_compile_cache").mkdir()
    (tmp_path / ".compile_signature").write_text("old-model|mtp|3")
    attempts = []

    def failing_run(cmd, **kwargs):
        attempts.append(cmd)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vsm.subprocess, "run", failing_run)
    assert VLLMServerManager._reset_stale_compile_cache("qwen2.5-coder-32b") is False
    # The cache still holds the old graph, so the old signature has to stay to say so.
    assert (tmp_path / ".compile_signature").read_text() == "old-model|mtp|3"
    VLLMServerManager._reset_stale_compile_cache("qwen2.5-coder-32b")
    assert len(attempts) == 2


def _gb10_manager(monkeypatch, available, side=0.0, transient=5.0):
    """A manager whose start_server reaches the launch on a 128 GB GB10, touching nothing real."""
    from dreamference.hardware.hardware_manager import HardwareManager, HardwareTelemetry
    from dreamference.vllm_server import vllm_server_manager as vsm

    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda image: True)
    monkeypatch.setattr(mgr, "_stop_index_scopes", lambda: None)
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(mgr, "_estimate_model_weights_gb", lambda model: 20.0)
    monkeypatch.setattr(mgr, "load_transient_gb", lambda model, weights: (transient, "test"))
    monkeypatch.setattr(mgr, "side_service_allowance", lambda: [("the forge", side)] if side else [])
    monkeypatch.setattr(vsm.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True, gpu_name="NVIDIA GB10", driver_version="0", arch="aarch64",
                total_unified_memory_gb=128.0, available_memory_gb=available,
                used_memory_gb=128.0 - available, vram_gb=0.0,
            )
        ),
    )
    return mgr


def test_the_advised_utilization_fits_beside_the_side_services(monkeypatch, capsys):
    import re

    available, transient, side = 70.0, 5.0, 10.0
    mgr = _gb10_manager(monkeypatch, available, side=side, transient=transient)
    with pytest.raises(SystemExit):
        mgr.start_server(model="qwen2.5-coder-32b", background=False, gate=False)
    out = capsys.readouterr().out
    advised = float(re.search(r"gpu_memory_utilization to at most (\d\.\d+)", out).group(1))
    # Taking the advice must pass the same check that refused this launch.
    assert advised * 128.0 + transient + side <= available


class _FakeWatchdog:
    def __init__(self, container, on_trip):
        self.stopped = False

    def start(self):
        return True

    def stop(self):
        self.stopped = True


@pytest.mark.parametrize("background", [True, False])
def test_a_failed_docker_run_takes_down_the_gate_and_the_watchdog(monkeypatch, background):
    import subprocess

    from dreamference.vllm_server import vllm_server_manager as vsm
    from dreamference.vllm_server.adapter_serving import AdapterServing
    from dreamference.vllm_server.model_gate import ModelGate

    mgr = _gb10_manager(monkeypatch, available=120.0)
    monkeypatch.setattr("dreamference.hardware.download_model", lambda *a, **k: True)
    monkeypatch.setattr(mgr, "_reset_stale_compile_cache", lambda *a, **k: False)
    monkeypatch.setattr(AdapterServing, "prepare", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(mgr, "build_launch_command", lambda **kwargs: ["docker", "run", "image"])
    monkeypatch.setattr(vsm, "MemoryPressureWatchdog", _FakeWatchdog)
    monkeypatch.setattr(ModelGate, "start", classmethod(lambda cls, port, image: True))
    stopped = []
    monkeypatch.setattr(ModelGate, "stop", classmethod(lambda cls, port: stopped.append(port)))

    def run(cmd, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(125, cmd)

    monkeypatch.setattr(vsm.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        mgr.start_server(model="qwen2.5-coder-32b", background=background, gate=True)
    # Left behind, the gate holds the public port and answers every request 502.
    assert stopped == [8000]
    assert mgr.watchdog is None or mgr.watchdog.stopped


def test_the_gate_pipe_keeps_going_while_a_read_ends_inside_the_id(tmp_path):
    import asyncio

    from dreamference.vllm_server.model_gate_service import ModelGateService

    old, new = b'"base:my-adapter"', b'"my-adapter-xyz1"'
    chunks = [b'data: {"model": ', b'"base:', b"my-", b'adapter", "x": 1}\n\n']

    class Source:
        async def read(self, n):
            return chunks.pop(0) if chunks else b""

    class Sink:
        sent = b""

        def write(self, data):
            Sink.sent += data

        async def drain(self):
            pass

    asyncio.run(ModelGateService.pipe(Source(), Sink(), (old, new)))
    # The third read ends inside the id again, so it alone has nothing to send yet.
    assert Sink.sent == b'data: {"model": "my-adapter-xyz1", "x": 1}\n\n'


def test_a_failed_engine_call_closes_its_connection(tmp_path, monkeypatch):
    import asyncio

    from dreamference.vllm_server.model_gate_adapters import ModelGateAdapters

    closed = []

    class Writer:
        def write(self, data):
            pass

        async def drain(self):
            raise ConnectionResetError

        def close(self):
            closed.append(True)

    async def connect(host, port):
        return object(), Writer()

    monkeypatch.setattr(asyncio, "open_connection", connect)
    adapters = ModelGateAdapters(str(tmp_path), ("127.0.0.1", 1))
    assert asyncio.run(adapters.engine_call("GET", "/v1/models", None)) == (0, b"")
    assert closed == [True]


@pytest.mark.parametrize("error", ["BadStatusLine", "IncompleteRead", "LineTooLong"])
def test_a_port_that_does_not_speak_http_has_no_gate(monkeypatch, error):
    import http.client

    from conftest import REAL_GATE_PROBE

    from dreamference.vllm_server.model_gate import ModelGate

    monkeypatch.setattr(ModelGate, "probe", REAL_GATE_PROBE)

    def broken(*args, **kwargs):
        raise getattr(http.client, error)("x" if error != "IncompleteRead" else b"x")

    monkeypatch.setattr("urllib.request.urlopen", broken)
    assert ModelGate.probe("http://127.0.0.1:8000") is None
