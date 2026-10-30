"""Behaviour found wrong in the 2026-10-29 review of `dreamference/vllm_server/`."""

import asyncio

import pytest

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
from dreamference.vllm_server import VLLMServerManager

SECRET = "hf_review_secret_value"


@pytest.mark.parametrize(
    ("model", "engine"), [("qwen2.5-coder-32b", None), (DEFAULT_MODEL_ALIAS, "sglang")]
)
def test_the_launch_command_names_the_token_but_never_carries_it(monkeypatch, model, engine):
    # start_server prints the whole command; the token reaches the container through the
    # environment of the `docker run` it spawns, which start_server sets.
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model=model, hf_token=SECRET, engine=engine)
    assert not any(SECRET in part for part in cmd)
    at = cmd.index("HF_TOKEN")
    assert cmd[at - 1] == "-e"


def test_ling_serve_s_launch_command_never_carries_the_token(monkeypatch):
    # ling-serve, the default since 1.8.0, reads the mounted cache and is given no token at all.
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, hf_token=SECRET)
    assert not any(SECRET in part for part in cmd)


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
    monkeypatch.setattr(
        mgr, "side_service_allowance", lambda: [("the forge", side)] if side else []
    )
    monkeypatch.setattr(vsm.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True,
                gpu_name="NVIDIA GB10",
                driver_version="0",
                arch="aarch64",
                total_unified_memory_gb=128.0,
                available_memory_gb=available,
                used_memory_gb=128.0 - available,
                vram_gb=0.0,
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


def test_the_advised_utilization_leaves_the_host_its_reserve(monkeypatch, capsys):
    import re

    import dreamference.hardware as hardware
    from dreamference.vllm_server.vllm_server_manager import HOST_MEMORY_RESERVE_GB

    mgr = _gb10_manager(monkeypatch, available=120.0)
    recipe = {"gpu_memory_utilization": 0.95}
    monkeypatch.setattr(hardware, "get_model_launch_overrides", lambda model, engine=None: recipe)
    with pytest.raises(SystemExit):
        mgr.start_server(model="qwen2.5-coder-32b", background=False, gate=False)
    out = capsys.readouterr().out
    advised = float(re.search(r"gpu_memory_utilization to at most (\d\.\d+)", out).group(1))
    # Rounded to the nearest hundredth, 116/128 became 0.91, which the same check refuses again.
    assert 128.0 - advised * 128.0 >= HOST_MEMORY_RESERVE_GB


@pytest.mark.parametrize(
    "argv, fault",
    [
        (["earlyoom", "-m", "5", "-s", "100", "-s", "10"], "swap"),
        (["earlyoom", "-m", "5", "-s", "100", "-m", "20"], "memory"),
        (["earlyoom", "-m", "20", "-m", "5", "-s", "100"], None),
    ],
)
def test_a_repeated_earlyoom_flag_is_read_as_earlyoom_reads_it(argv, fault):
    # earlyoom parses its flags with getopt, so the last occurrence of a flag is the one in force.
    result = VLLMServerManager._earlyoom_fault(argv)
    if fault is None:
        assert result is None
    else:
        assert result is not None and fault in result


def _docker_targets(monkeypatch, module):
    """Records the container each docker command names, and answers as if none were running."""
    import subprocess

    targets = []

    def run(argv, *args, **kwargs):
        targets.extend(arg for arg in argv if arg.startswith("dreamference-vllm-"))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name, raising=False)
    return targets


@pytest.mark.parametrize("host", ["http://localhost", "http://localhost:8000/"])
def test_the_container_name_comes_from_the_port_of_any_host_url(monkeypatch, host):
    from dreamference.vllm_server import diagnostics

    # One subprocess module serves both callers, so the one patch covers the manager too.
    targets = _docker_targets(monkeypatch, diagnostics)
    diagnostics.ContainerDiagnostics(host).get_diagnostics_line()
    mgr = VLLMServerManager(host=host)
    monkeypatch.setattr(mgr, "check_health", lambda: False)
    mgr.get_server_status()
    assert targets and set(targets) == {"dreamference-vllm-8000"}


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


def test_the_diffusion_sidecar_answers_a_body_that_is_not_an_object_with_400():
    import requests
    from test_diffusion_server import _serve, _StubRunner

    server, base = _serve(_StubRunner())
    try:
        resp = requests.post(f"{base}/v1/completions", data=b"[1]", timeout=5)
        assert resp.status_code == 400
    finally:
        server.shutdown()


def test_the_canary_reads_utf8_events_whatever_charset_the_stream_names():
    import io
    import json

    import requests
    from test_tool_call_canary import call_item, check, message_item, stream

    # U+0445 is D1 85 in UTF-8; read as ISO-8859-1 (requests' default for text/*), the 0x85 is NEL,
    # a line break to str.splitlines(), and the event it was in came apart.
    events = stream(message_item("weather: \u0445"), call_item())
    body = "".join(
        f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events
    )
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/event-stream"
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.raw = io.BytesIO(body.encode("utf-8"))
    ok, detail = check(response)
    assert ok, detail


def test_a_rejected_recipe_variable_stops_the_loading_monitor_and_says_why():
    from dreamference.vllm_server.model_loading_monitor import ModelLoadingMonitor

    # vLLM's own wording: "Unknown vLLM environment variable detected: %s".
    lines = ["WARNING envs.py:120] Unknown vLLM environment variable detected: VLLM_KERNEL_PIN\n"]
    monitor = ModelLoadingMonitor(
        get_logs_fn=lambda: [lines.pop()] if lines else [],
        check_health_fn=lambda: False,
        recipe_env_keys={"VLLM_KERNEL_PIN"},
    )
    monitor.start()
    monitor.monitor_thread.join(timeout=5)
    # sys.exit in the monitor's thread ended only that thread, and the launch went on waiting.
    assert not monitor.monitor_thread.is_alive()
    assert monitor.fatal and "VLLM_KERNEL_PIN" in monitor.fatal


def _fake_engine(adapters, unload_status=200):
    """An engine that yields on every call and records which adapters it holds."""
    held = set()

    async def engine_call(method, path, payload):
        await asyncio.sleep(0)
        if path == "/v1/models":
            return 200, b'{"data": []}'
        if path == "/load_lora_adapter":
            # A load takes a while; the other request's checks finish meanwhile.
            await asyncio.sleep(0.05)
            held.add(payload["lora_name"])
            return 200, b""
        if unload_status == 200:
            held.discard(payload["lora_name"])
        return unload_status, b""

    adapters.engine_call = engine_call
    adapters.files_problem = lambda entry: None
    return held


def _listing(*names):
    return {"max_loaded": 1, "adapters": {name: {"path": "/" + name} for name in names}}


def test_concurrent_loads_of_different_adapters_keep_to_the_limit(tmp_path):
    from dreamference.vllm_server.model_gate_adapters import ModelGateAdapters

    adapters = ModelGateAdapters(str(tmp_path), ("127.0.0.1", 1))
    held = _fake_engine(adapters)
    listing = _listing("a", "b")

    async def both():
        return await asyncio.gather(
            adapters.ensure_loaded("a", listing), adapters.ensure_loaded("b", listing)
        )

    assert asyncio.run(both()) == [None, None]
    # Each load made room against a count the other had not joined yet, and both stayed.
    assert len(held) <= 1 and len(adapters.loaded) <= 1


def test_an_unload_the_engine_refuses_makes_the_gate_ask_again(tmp_path):
    from dreamference.vllm_server.model_gate_adapters import ModelGateAdapters

    adapters = ModelGateAdapters(str(tmp_path), ("127.0.0.1", 1))
    _fake_engine(adapters, unload_status=500)
    listing = _listing("a", "b")
    asyncio.run(adapters.ensure_loaded("a", listing))
    asyncio.run(adapters.ensure_loaded("b", listing))
    # The gate forgot "a" while the engine still holds it; only a fresh look at the engine knows.
    assert adapters.synced is False
