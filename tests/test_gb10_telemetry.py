import io
import subprocess
import types

import pytest

from dreamference.hardware import hardware_manager
from dreamference.hardware.hardware_manager import HardwareManager
from dreamference.hardware.memory_metrics import MemoryMetrics
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

GIB_KB = 1024 * 1024


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_system_memory_reads_total_and_available_from_meminfo_in_gib(monkeypatch):
    # MemFree leaves out the page cache the kernel gives back on demand: available is MemAvailable.
    meminfo = (
        f"MemTotal:       {124 * GIB_KB} kB\n"
        f"MemFree:        {10 * GIB_KB} kB\n"
        f"MemAvailable:   {100 * GIB_KB} kB\n"
        f"Buffers:        {1 * GIB_KB} kB\n"
    )
    real_exists, real_open = hardware_manager.os.path.exists, open
    monkeypatch.setattr(
        hardware_manager.os.path, "exists", lambda p: p == "/proc/meminfo" or real_exists(p)
    )
    monkeypatch.setattr(
        hardware_manager,
        "open",
        lambda p, *a, **k: io.StringIO(meminfo) if p == "/proc/meminfo" else real_open(p, *a, **k),
        raising=False,
    )
    assert HardwareManager.get_system_memory() == MemoryMetrics(
        total_gb=124.0, available_gb=100.0, used_gb=24.0
    )


def _detect(monkeypatch, tmp_path, stdout, returncode=0):
    # Detection with nvidia-smi answering `stdout`, nothing on the PCI bus and a known memory reading.
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    (tmp_path / "pci").mkdir(exist_ok=True)
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        HardwareManager,
        "get_system_memory",
        classmethod(lambda cls: MemoryMetrics(total_gb=124.0, available_gb=100.0, used_gb=24.0)),
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(hardware_manager.subprocess, "run", run)
    return HardwareManager.detect_gb10_hardware(), calls


def test_nvidia_smi_names_the_gpu_its_driver_and_its_memory_in_gib(monkeypatch, tmp_path):
    hw, calls = _detect(monkeypatch, tmp_path, "NVIDIA GB10, 580.95, 122880\n")
    assert (hw.is_gb10, hw.gpu_name, hw.driver_version, hw.vram_gb) == (
        True,
        "NVIDIA GB10",
        "580.95",
        120.0,
    )
    # Unified memory is the whole of it, not what happens to be free at the moment.
    assert (hw.total_unified_memory_gb, hw.available_memory_gb, hw.used_memory_gb) == (
        124.0,
        100.0,
        24.0,
    )
    # A hung driver must not hang every `ling` start behind it.
    assert 0 < calls[0]["timeout"] <= 10


def test_a_failed_nvidia_smi_names_no_gpu_even_with_output(monkeypatch, tmp_path):
    hw, _ = _detect(monkeypatch, tmp_path, "NVIDIA GB10, 580.95, 0\n", returncode=1)
    assert (hw.is_gb10, hw.gpu_name, hw.driver_version) == (False, "N/A", "N/A")


def test_the_first_gpu_nvidia_smi_lists_is_the_one_that_counts(monkeypatch, tmp_path):
    hw, _ = _detect(
        monkeypatch, tmp_path, "NVIDIA GB10, 580.95, 0\nNVIDIA RTX PRO 6000 Blackwell, 575.10, 0\n"
    )
    assert (hw.is_gb10, hw.gpu_name, hw.driver_version) == (True, "NVIDIA GB10", "580.95")


@pytest.mark.parametrize(
    "stdout, name, driver",
    [
        ("NVIDIA GB10, 580.95\n", "NVIDIA GB10", "580.95"),  # no memory column
        ("nvidia gb10, 580.95, 0\n", "nvidia gb10", "580.95"),  # the name matched in any case
    ],
)
def test_a_short_or_lowercase_nvidia_smi_line_still_reads(
    monkeypatch, tmp_path, stdout, name, driver
):
    hw, _ = _detect(monkeypatch, tmp_path, stdout)
    assert (hw.is_gb10, hw.gpu_name, hw.driver_version, hw.vram_gb) == (True, name, driver, 0.0)


def test_a_machine_without_a_pci_tree_is_not_a_gb10(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        HardwareManager,
        "get_system_memory",
        classmethod(lambda cls: MemoryMetrics(total_gb=16.0, available_gb=8.0, used_gb=8.0)),
    )
    assert HardwareManager.gb10_on_pci() is False
    assert HardwareManager.detect_gb10_hardware().is_gb10 is False


@pytest.mark.parametrize(
    "os_release, dgx_release, expected",
    [
        # An image never updated over the air has only its build version.
        (
            "PRETTY_NAME='Ubuntu 24.04 LTS'\n",
            'DGX_SWBUILD_VERSION="7.2.3"\n',
            "DGX OS 7.2.3 (Ubuntu 24.04 LTS)",
        ),
        (None, 'DGX_OTA_VERSION="7.5.0"\n', "DGX OS 7.5.0"),
    ],
)
def test_the_os_name_falls_back_to_the_build_version_and_reads_single_quotes(
    monkeypatch, tmp_path, os_release, dgx_release, expected
):
    monkeypatch.setattr(hardware_manager, "OS_RELEASE", str(tmp_path / "os-release"))
    monkeypatch.setattr(hardware_manager, "DGX_RELEASE", str(tmp_path / "dgx-release"))
    if os_release is not None:
        _write(tmp_path / "os-release", os_release)
    _write(tmp_path / "dgx-release", dgx_release)
    assert HardwareManager.os_name() == expected


def _specs(monkeypatch, total_gb, **specs):
    # Compatibility against made-up models on a machine reporting `total_gb` of unified memory.
    table = {
        key: types.SimpleNamespace(
            name=key, notes="", min_memory_gb=min_gb, compatible_gb10=compatible
        )
        for key, (min_gb, compatible) in specs.items()
    }
    monkeypatch.setattr(
        ModelMatrixRegistry, "get_spec", classmethod(lambda cls, key: table.get(key))
    )
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(lambda cls: types.SimpleNamespace(total_unified_memory_gb=total_gb)),
    )


@pytest.mark.parametrize(
    "total_gb, min_gb, compatible, fits",
    [
        (128.0, 10.0, False, False),  # marked unfit for a GB10 whatever its size
        (100.0, 95.0, True, True),  # exactly 95% still fits
        (100.0, 95.5, True, False),
        (0.0, 125.0, True, False),  # an unread memory size is taken as the GB10's 128 GB
    ],
)
def test_a_model_fits_within_95_percent_of_unified_memory(
    monkeypatch, total_gb, min_gb, compatible, fits
):
    _specs(monkeypatch, total_gb, model=(min_gb, compatible))
    assert HardwareManager.check_model_compatibility("model")[0] is fits


@pytest.mark.parametrize(
    "total_gb, main, draft, fits",
    [
        (128.0, (10.0, False), (1.0, True), False),  # the main model is unfit on its own
        (128.0, (1.0, True), (10.0, False), False),  # the drafter is unfit on its own
        (125.0, (62.0, True), (58.0, True), False),  # each fits, the pair is past 95%
        (0.0, (60.0, True), (60.0, True), True),  # an unread memory size is taken as 128 GB
    ],
)
def test_a_main_model_and_its_drafter_must_fit_together(monkeypatch, total_gb, main, draft, fits):
    _specs(monkeypatch, total_gb, main=main, draft=draft)
    assert HardwareManager.check_speculative_compatibility("main", "draft")[0] is fits
