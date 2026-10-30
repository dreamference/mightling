"""The host-safety options every engine's `docker run` gets: the memory cap, swap and CPU limit."""

import pytest

from dreamference.hardware.hardware_manager import HardwareManager
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.vllm_server import VLLMServerManager
from dreamference.vllm_server.vllm_server_manager import HOST_MEMORY_RESERVE_GB

TOTAL_GB = 121.6


@pytest.fixture
def host(monkeypatch):
    """A GB10 with TOTAL_GB of unified memory and twenty CPUs."""
    telemetry = HardwareTelemetry(
        is_gb10=True,
        gpu_name="NVIDIA GB10",
        driver_version="0",
        arch="aarch64",
        total_unified_memory_gb=TOTAL_GB,
        available_memory_gb=TOTAL_GB,
        used_memory_gb=0.0,
        vram_gb=0.0,
    )
    monkeypatch.setattr(HardwareManager, "detect_gb10_hardware", classmethod(lambda cls: telemetry))
    monkeypatch.setattr("os.cpu_count", lambda: 20)


def _prefix(recipe, gpu_memory_utilization=0.5):
    return VLLMServerManager()._docker_run_prefix(8000, gpu_memory_utilization, recipe, None)


def _option(prefix, name):
    return next(a for a in prefix if a.startswith(name + "=")).split("=", 1)[1]


def test_a_reckless_headroom_cannot_raise_the_memory_cap_into_the_host_reserve(host):
    prefix = _prefix({"container_headroom_gb": 100.0})
    assert _option(prefix, "--memory") == f"{TOTAL_GB - HOST_MEMORY_RESERVE_GB:.0f}g"


def test_the_container_gets_no_swap_beyond_its_memory_cap(host):
    prefix = _prefix({"container_headroom_gb": 4.0})
    assert _option(prefix, "--memory") == f"{TOTAL_GB * 0.5 + 4.0:.0f}g"
    assert _option(prefix, "--memory-swap") == _option(prefix, "--memory")


def test_the_container_leaves_the_host_a_share_of_its_cpus(host, monkeypatch):
    assert _option(_prefix({}), "--cpus") == "14.0"
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    assert _option(_prefix({}), "--cpus") == "1.0"
