"""What `start_server` refuses before a load, and what it takes down when a launch is given up."""
import os
from types import SimpleNamespace

import pytest

from dreamference.hardware import ModelDownloader, ModelMatrixRegistry
from dreamference.hardware.hardware_manager import HardwareManager
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.vllm_server import VLLMServerManager, vllm_server_manager
from dreamference.vllm_server.model_gate import ModelGate
from dreamference.vllm_server.vllm_server_manager import HOST_MEMORY_RESERVE_GB

TOTAL_GB = 121.6
IMAGE = "registry.example/engine:1"


class Stop(Exception):
    """Raised where a test has seen what it came for."""


def _telemetry(available, is_gb10=True):
    return HardwareTelemetry(
        is_gb10=is_gb10, gpu_name="NVIDIA GB10", driver_version="0", arch="aarch64",
        total_unified_memory_gb=TOTAL_GB, available_memory_gb=available,
        used_memory_gb=TOTAL_GB - available, vram_gb=0.0,
    )


@pytest.fixture
def launch(monkeypatch):
    """A manager whose host, image and downloads are stand-ins; `seen` records what ran."""
    mgr = VLLMServerManager()
    seen = {"calls": [], "downloads": [], "readings": [_telemetry(100.0)]}
    state = {"docker": True, "image": True, "weights": {"model": 40.0},
             "recipe": {"gpu_memory_utilization": 0.5}, "evicted": 0.0}

    def record(name, result=None):
        return lambda *a, **k: seen["calls"].append(name) or result

    monkeypatch.setattr(mgr, "is_docker_available", lambda: seen["calls"].append("docker") or state["docker"])
    monkeypatch.setattr(mgr, "ensure_docker_image",
                        lambda image: seen["calls"].append(("image", image)) or state["image"])
    monkeypatch.setattr(HardwareManager, "detect_gb10_hardware",
                        classmethod(lambda cls: seen["readings"].pop(0) if len(seen["readings"]) > 1
                                    else seen["readings"][0]))
    monkeypatch.setattr(mgr, "_stop_index_scopes", record("index scopes"))
    monkeypatch.setattr(mgr, "check_host_safety", record("host safety"))
    monkeypatch.setattr("dreamference.hardware.get_model_launch_overrides",
                        lambda model, engine=None: dict(state["recipe"]))
    monkeypatch.setattr(mgr, "_estimate_model_weights_gb", lambda model: state["weights"].get(model, 0.0))
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: state["evicted"])
    monkeypatch.setattr(mgr, "load_transient_gb", lambda model, weights_gb: (0.0, "test"))
    monkeypatch.setattr(mgr, "side_service_allowance", lambda: [])
    monkeypatch.setattr(vllm_server_manager.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr("dreamference.hardware.download_model",
                        lambda model, **k: seen["downloads"].append((model, k.get("auto_tensorize"))) or True)
    monkeypatch.setattr(ModelMatrixRegistry, "get_engine_assets",
                        classmethod(lambda cls, model, engine=None: (_ for _ in ()).throw(Stop())))

    def go(**kwargs):
        kwargs.setdefault("docker_image", IMAGE)
        return mgr.start_server(model="model", background=False, gate=False, **kwargs)

    return SimpleNamespace(go=go, seen=seen, state=state, mgr=mgr)


# -- what is refused before anything is loaded -------------------------------------------------------


def test_no_docker_is_refused_before_the_image_is_looked_at(launch):
    launch.state["docker"] = False

    with pytest.raises(RuntimeError, match="Docker is required"):
        launch.go()
    assert launch.seen["calls"] == ["docker"]


def test_an_image_that_cannot_be_had_is_refused_before_the_host_is_read(launch, monkeypatch):
    launch.state["image"] = False
    monkeypatch.setattr(HardwareManager, "detect_gb10_hardware",
                        classmethod(lambda cls: pytest.fail("the host was read")))

    with pytest.raises(RuntimeError, match=f"Docker image '{IMAGE}' is missing locally"):
        launch.go()
    assert launch.seen["calls"] == ["docker", ("image", IMAGE)]


def test_a_machine_that_is_not_a_gb10_is_refused_before_its_safety_checks(launch):
    launch.seen["readings"] = [_telemetry(100.0, is_gb10=False)]

    with pytest.raises(RuntimeError, match="requires an NVIDIA GB10"):
        launch.go()
    assert "host safety" not in launch.seen["calls"] and "index scopes" not in launch.seen["calls"]


def test_weights_larger_than_the_machine_less_its_reserve_are_refused(launch, capsys):
    launch.state["weights"] = {"model": TOTAL_GB - HOST_MEMORY_RESERVE_GB + 0.5}

    with pytest.raises(SystemExit) as stopped:
        launch.go()
    assert stopped.value.code == 1
    assert "'model' is too large to load on this machine." in capsys.readouterr().out
    assert launch.seen["downloads"] == []


def test_a_named_drafter_counts_towards_the_weights_that_must_fit(launch, capsys):
    launch.state["weights"] = {"model": 60.0, "drafter": 55.0}

    with pytest.raises(SystemExit):
        launch.go(draft_model="drafter")
    assert "too large to load" in capsys.readouterr().out


def test_weights_that_fill_the_recipes_arena_are_refused(launch, capsys):
    launch.state["recipe"] = {"gpu_memory_utilization": 0.3}
    launch.state["weights"] = {"model": TOTAL_GB * 0.3}

    with pytest.raises(SystemExit) as stopped:
        launch.go()
    assert stopped.value.code == 1
    out = capsys.readouterr().out
    assert "Model weights do not fit the configured memory arena for 'model'." in out
    assert launch.seen["downloads"] == []
