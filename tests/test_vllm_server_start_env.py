"""The environment `start_server` hands the engine: the HuggingFace token and the non-Docker path.

The token reaches the container through the environment of `docker run` and never through its
command line or the console; with no token the engine runs cache-only. A command that is not
Docker runs directly, without the memory-pressure watchdog that only a container can be killed by.
"""

import pytest

from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.vllm_server import DEFAULT_VLLM_IMAGE, psi_watchdog
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


class _Proc:
    stdout = None
    pid = 1234


def _start(monkeypatch, cmd, **kwargs):
    """Runs `start_server` in the background with `cmd` as the launch; returns what it ran."""
    mgr = VLLMServerManager()
    runs, popens = [], []
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda img=DEFAULT_VLLM_IMAGE: True)
    monkeypatch.setattr("dreamference.hardware.download_model", lambda *a, **k: None)
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(
        "dreamference.hardware.hardware_manager.HardwareManager.detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True,
                gpu_name="NVIDIA GB10",
                driver_version="0",
                arch="aarch64",
                total_unified_memory_gb=128.0,
                available_memory_gb=128.0,
                used_memory_gb=0.0,
                vram_gb=0.0,
            )
        ),
    )
    monkeypatch.setattr(mgr, "build_launch_command", lambda **kw: list(cmd))
    monkeypatch.setattr("subprocess.run", lambda c, **k: runs.append((list(c), k)))
    monkeypatch.setattr("subprocess.Popen", lambda c, **k: popens.append((list(c), k)) or _Proc())
    monkeypatch.setattr(mgr.streamer, "start_streaming", lambda stream: None)
    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full", lambda: None)
    mgr.start_server(model="qwen2.5-coder-32b", background=True, **kwargs)
    return mgr, runs, popens


def _docker_run(runs):
    (launch,) = [(c, k) for c, k in runs if c == ["docker", "run", "-d", DEFAULT_VLLM_IMAGE]]
    return launch


@pytest.fixture(autouse=True)
def _no_token_in_the_environment(monkeypatch):
    for name in ("HF_TOKEN", "DREAMFERENCE_HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_the_token_reaches_docker_run_by_environment_and_is_never_printed(monkeypatch, capsys):
    _, runs, _ = _start(monkeypatch, ["docker", "run", DEFAULT_VLLM_IMAGE], hf_token="hf_secret")
    cmd, kwargs = _docker_run(runs)
    assert kwargs["env"]["HF_TOKEN"] == "hf_secret"
    assert kwargs["env"]["HUGGING_FACE_HUB_TOKEN"] == "hf_secret"
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "0"
    assert not any("hf_secret" in part for part in cmd)
    out = capsys.readouterr().out
    assert "HuggingFace Token: Configured" in out and "hf_secret" not in out


def test_the_projects_own_token_variable_stands_in_when_no_token_is_given(monkeypatch):
    monkeypatch.setenv("DREAMFERENCE_HF_TOKEN", "hf_from_env")
    _, runs, _ = _start(monkeypatch, ["docker", "run", DEFAULT_VLLM_IMAGE])
    _, kwargs = _docker_run(runs)
    assert kwargs["env"]["HF_TOKEN"] == "hf_from_env"
    assert kwargs["env"]["HUGGING_FACE_HUB_TOKEN"] == "hf_from_env"


def test_without_a_token_the_engine_runs_cache_only(monkeypatch, capsys):
    _, runs, _ = _start(monkeypatch, ["docker", "run", DEFAULT_VLLM_IMAGE])
    _, kwargs = _docker_run(runs)
    assert "HF_TOKEN" not in kwargs["env"] and "HUGGING_FACE_HUB_TOKEN" not in kwargs["env"]
    assert "Not configured (cache-only mode)" in capsys.readouterr().out


def test_a_launch_that_is_not_docker_runs_directly_without_the_watchdog(monkeypatch):
    mgr, runs, popens = _start(monkeypatch, ["vllm", "serve", "some/model"])
    assert mgr.watchdog is None
    assert not [c for c, _ in runs if c[:2] == ["docker", "run"]]
    assert [c for c, _ in popens] == [["vllm", "serve", "some/model"]]
