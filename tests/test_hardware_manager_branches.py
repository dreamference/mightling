"""HardwareManager: a host that answers little, and a pair of unknown models."""

import subprocess

from dreamference.hardware import hardware_manager
from dreamference.hardware.hardware_manager import HardwareManager, MemoryMetrics


def _quiet_host(monkeypatch, tmp_path, *, smi_output):
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        HardwareManager,
        "get_system_memory",
        classmethod(lambda cls: MemoryMetrics(total_gb=16.0, available_gb=8.0, used_gb=8.0)),
    )
    monkeypatch.setattr(
        hardware_manager.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, smi_output, ""),
    )


def test_a_host_without_proc_meminfo_reports_zero_memory(monkeypatch):
    monkeypatch.setattr(hardware_manager.os.path, "exists", lambda path: False)
    metrics = HardwareManager.get_system_memory()
    assert (metrics.total_gb, metrics.available_gb, metrics.used_gb) == (0.0, 0.0, 0.0)


def test_an_nvidia_smi_that_names_only_the_gpu_leaves_the_driver_unknown(monkeypatch, tmp_path):
    (tmp_path / "pci").mkdir()
    _quiet_host(monkeypatch, tmp_path, smi_output="Some Card\n")
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.driver_version == "N/A"
    assert hw.is_gb10 is False


def test_a_gb10_on_the_bus_under_another_gpu_name_keeps_the_name_smi_gave(monkeypatch, tmp_path):
    (tmp_path / "pci").mkdir()
    _quiet_host(monkeypatch, tmp_path, smi_output="Some Card, 580.95, 0\n")
    monkeypatch.setattr(HardwareManager, "gb10_on_pci", classmethod(lambda cls: True))
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.is_gb10 is True
    assert hw.gpu_name == "Some Card"


def test_a_pair_of_models_the_matrix_does_not_know_is_accepted_without_a_memory_check():
    valid, message = HardwareManager.check_speculative_compatibility("nope/main", "nope/draft")
    assert valid is True
    assert "Speculative decoding enabled" in message
