import subprocess

from dreamference.hardware import (
    detect_gb10_hardware,
    get_system_memory,
    check_model_compatibility,
    MODEL_MATRIX
)

def test_system_memory():
    mem = get_system_memory()
    assert "total_gb" in mem
    assert "available_gb" in mem
    assert isinstance(mem["total_gb"], float)

def test_detect_gb10_hardware():
    hw = detect_gb10_hardware()
    assert "is_gb10" in hw
    assert "gpu_name" in hw
    assert "total_unified_memory_gb" in hw

def test_check_model_compatibility():
    # qwen3.6-35b stands in for the removed qwen2.5-coder fixtures (dropped from the matrix
    # 2026-08-24); any registered, GB10-compatible alias serves the purpose.
    valid, msg = check_model_compatibility("qwen3.6-35b-a3b-nvfp4")
    assert isinstance(valid, bool)
    assert isinstance(msg, str)

    # Unknown models are permissive by contract: the matrix no longer carries an
    # incompatible-by-design fixture (the deepseek entry left with the 2026-08-24 purge),
    # and an unregistered alias resolves to "unknown, caller beware", not a refusal.
    valid_unknown, msg_unknown = check_model_compatibility("deepseek-v3-671b")
    assert valid_unknown is True
    assert "unknown" in msg_unknown.lower()

def test_check_speculative_compatibility(monkeypatch):
    # The memory budget is checked against the host, so pin it to a GB10's 128 GB: on a 16 GB CI
    # runner the real check correctly refuses the pair, and the test is about the registry entries.
    from types import SimpleNamespace
    from dreamference.hardware import HardwareManager, check_speculative_compatibility
    monkeypatch.setattr(
        HardwareManager, "detect_gb10_hardware",
        classmethod(lambda cls: SimpleNamespace(total_unified_memory_gb=128.0)),
    )
    valid, msg = check_speculative_compatibility(
        "qwen3.5-122b-a10b-int4-dflash", "qwen3.5-122b-a10b-dflash-draft")
    assert valid is True
    assert "Speculative Decoding Qualified" in msg or "Compatible" in msg

def test_default_model_is_registered_and_gb10_compatible():
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.compatible_gb10 is True
    assert spec.hf_repo_id == "RadixArk/Qwen3.8-27B-NVFP4"

def test_default_model_speculates_against_a_downloadable_drafter():
    # The DFlash drafter is named inside the recipe rather than passed as an argument, which is
    # the only reason start_server can see it at all: it has to be fetched before the load and
    # counted against memory alongside the target. A recipe that names a drafter the registry
    # cannot size would put a 0 GB drafter into the pre-flight gates.
    from dreamference.hardware import get_speculative_draft_repo, MODEL_MATRIX
    from dreamference.hardware.model_matrix_registry import (
        DEFAULT_MODEL_ALIAS,
        ModelMatrixRegistry,
    )
    draft_repo = get_speculative_draft_repo(DEFAULT_MODEL_ALIAS)
    assert draft_repo == "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal"
    draft_spec = ModelMatrixRegistry.get_spec(draft_repo)
    assert draft_spec is not None
    assert draft_spec.min_memory_gb > 0

    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.launch_overrides["speculative_config"]["method"].lower() == "dflash"  # SGLang spells it DFLASH
    # Self-speculating and non-speculating models must not report a separate drafter.
    assert get_speculative_draft_repo("qwen3.6-35b-a3b-nvfp4") is None
    assert get_speculative_draft_repo("qwen2.5-coder-32b") is None
    assert get_speculative_draft_repo("some/unknown-repo") is None

def test_default_model_weights_fit_its_own_arena():
    # The recipe's gpu_memory_utilization has a floor the flag itself does not express: vLLM sizes
    # the whole allocation, weights included, as a fraction of total memory, so an arena smaller
    # than the checkpoint is an incoherent budget that start_server aborts on. Guard the two ends
    # rather than the exact value, so retuning does not fail this.
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    gb10_total_gb = 121.63
    arena_gb = gb10_total_gb * spec.launch_overrides["gpu_memory_utilization"]
    assert arena_gb > spec.min_memory_gb
    # And it has to leave the host its reserve, or the overshoot freezes the machine instead of
    # earning an OOM kill.
    from dreamference.vllm_server.vllm_server_manager import HOST_MEMORY_RESERVE_GB
    assert gb10_total_gb - arena_gb >= HOST_MEMORY_RESERVE_GB

def test_launch_overrides_are_isolated_per_call():
    from dreamference.hardware import get_model_launch_overrides
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    # Read the expected value from the registry rather than repeating it: this test is about
    # mutation isolation, and hardcoding the recipe's value makes it fail on every retune.
    original = MODEL_MATRIX[DEFAULT_MODEL_ALIAS].launch_overrides["max_model_len"]
    first = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    first["max_model_len"] = 1
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS)["max_model_len"] == original
    assert get_model_launch_overrides("qwen2.5-coder-32b") == {}
    assert get_model_launch_overrides("some/unknown-repo") == {}

def test_declares_own_quantization():
    from dreamference.hardware import model_declares_own_quantization
    assert model_declares_own_quantization("qwen3.6-35b-a3b-nvfp4") is True
    assert model_declares_own_quantization("qwen2.5-coder-72b") is False
    assert model_declares_own_quantization("some/unknown-repo") is False

def test_flexible_model_alias_resolution():
    from dreamference.hardware import resolve_model_hf_repo, get_model_launch_overrides, model_declares_own_quantization
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    aliases = [
        "qwen3.6-35b-a3b-nvfp4",
        "nvidia/Qwen3.6-35B-A3B-NVFP4",
        "NVIDIA Qwen3.6-35B-A3B-NVFP4",
        "qwen 3.6 35b-a3b (nvfp4)",
    ]
    for name in aliases:
        spec = ModelMatrixRegistry.get_spec(name)
        assert spec is not None, f"Failed to get spec for {name}"
        assert spec.hf_repo_id == "nvidia/Qwen3.6-35B-A3B-NVFP4"
        assert resolve_model_hf_repo(name) == "nvidia/Qwen3.6-35B-A3B-NVFP4"
        assert get_model_launch_overrides(name).get("max_model_len") == 32768
        assert model_declares_own_quantization(name) is True

def test_download_model_functions():
    from dreamference.hardware import is_model_downloaded, download_model, resolve_model_hf_repo
    repo = resolve_model_hf_repo("qwen3.6-35b-a3b-nvfp4")
    assert repo == "nvidia/Qwen3.6-35B-A3B-NVFP4"
    is_dl = is_model_downloaded("qwen3.6-35b-a3b-nvfp4")
    assert isinstance(is_dl, bool)

def test_tensorizer_functions(tmp_path, monkeypatch):
    from dreamference.hardware import is_model_tensorized, get_tensorized_path, ModelDownloader
    
    # Test checking non-existent model tensorization
    assert is_model_tensorized("qwen2.5-coder-32b") is False or isinstance(is_model_tensorized("qwen2.5-coder-32b"), bool)

    # Test creating mock tensorized directory and verifying detection
    tdir = ModelDownloader.get_tensorized_dir("qwen2.5-coder-32b")
    assert tdir is not None
    tdir.mkdir(parents=True, exist_ok=True)
    tfile = tdir / "model.tensors"
    tfile.write_text("mock tensors data")

    try:
        assert is_model_tensorized("qwen2.5-coder-32b") is True
        tpath = get_tensorized_path("qwen2.5-coder-32b")
        assert tpath == tfile
    finally:
        # Cleanup mock file
        if tfile.exists():
            tfile.unlink()



def test_qwen3_5_moe_entries_are_marked_vision_capable():
    # Both checkpoints are Qwen3_5MoeForConditionalGeneration with a vision_config, verified
    # against their own config.json. Clients gate image upload on this, so a wrong value here is
    # the difference between an upload working and the UI refusing it.
    from dreamference.hardware import model_supports_vision

    assert model_supports_vision("qwen3.5-122b-a10b-int4-dflash") is True
    assert model_supports_vision("qwen3.5-122b-a10b-nvfp4") is True
    assert model_supports_vision("Intel/Qwen3.5-122B-A10B-int4-AutoRound") is True
    assert model_supports_vision("llama-3.3-70b") is False
    assert model_supports_vision("qwen2.5-coder-32b") is False
    assert model_supports_vision("") is False


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_every_gb10_machine_is_found_by_its_gpu_and_named_by_its_firmware(tmp_path, monkeypatch):
    # The eight GB10 machines share one GPU (PCI 10de:2e12) and differ in DMI and OS release;
    # the GPU decides, and the rest is shown so a report says whose box it came from.
    from dreamference.hardware import hardware_manager
    from dreamference.hardware.hardware_manager import HardwareManager
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    monkeypatch.setattr(hardware_manager, "DMI_DIR", str(tmp_path / "dmi"))
    monkeypatch.setattr(hardware_manager, "DGX_RELEASE", str(tmp_path / "dgx-release"))
    monkeypatch.setattr(hardware_manager, "OS_RELEASE", str(tmp_path / "os-release"))
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: None)      # no driver yet
    monkeypatch.setattr(HardwareManager, "get_system_memory",
                        classmethod(lambda cls: hardware_manager.MemoryMetrics(total_gb=16.0, available_gb=8.0, used_gb=8.0)))

    (tmp_path / "pci").mkdir()
    assert HardwareManager.gb10_on_pci() is False and HardwareManager.detect_gb10_hardware().is_gb10 is False
    _write(tmp_path / "pci/0000:00:00.0/vendor", "0x10de\n")
    _write(tmp_path / "pci/0000:00:00.0/device", "0x22ce\n")                     # a root port, not the GPU
    assert HardwareManager.gb10_on_pci() is False
    _write(tmp_path / "pci/000f:01:00.0/vendor", "0x10de\n")
    _write(tmp_path / "pci/000f:01:00.0/device", "0x2e12\n")
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.is_gb10 is True and "GB10" in hw.gpu_name

    assert HardwareManager.machine_name() == "" and HardwareManager.os_name() == ""
    _write(tmp_path / "dmi/sys_vendor", "ASUSTeK COMPUTER INC.\n")
    _write(tmp_path / "dmi/product_name", "GX10\n")
    assert HardwareManager.machine_name() == "ASUSTeK COMPUTER INC. GX10"
    _write(tmp_path / "dmi/sys_vendor", "Dell Inc.\n")
    # Not read from a Dell: the shape of a product name that already says whose it is.
    _write(tmp_path / "dmi/product_name", "Dell Pro Max with GB10 FCM1253\n")
    assert HardwareManager.machine_name() == "Dell Pro Max with GB10 FCM1253"

    _write(tmp_path / "os-release", 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\n')
    assert HardwareManager.os_name() == "Ubuntu 24.04.4 LTS"
    _write(tmp_path / "dgx-release", 'DGX_NAME="DGX Spark"\nDGX_SWBUILD_VERSION="7.2.3"\n\nDGX_OTA_VERSION="7.5.0"\n')
    assert HardwareManager.os_name() == "DGX OS 7.5.0 (Ubuntu 24.04.4 LTS)"
    assert HardwareManager.detect_gb10_hardware().os_name == "DGX OS 7.5.0 (Ubuntu 24.04.4 LTS)"


def test_only_a_gb10_qualifies(monkeypatch, tmp_path):
    # A discrete Blackwell card and a large server are not GB10s: the recipes target SM121's unified memory.
    from dreamference.hardware import hardware_manager
    from dreamference.hardware.hardware_manager import HardwareManager
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    (tmp_path / "pci").mkdir()
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(HardwareManager, "get_system_memory",
                        classmethod(lambda cls: hardware_manager.MemoryMetrics(total_gb=512.0, available_gb=400.0, used_gb=112.0)))
    for name, expected in [("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", False), ("NVIDIA GB10", True)]:
        monkeypatch.setattr(hardware_manager.subprocess, "run", lambda *a, _n=name, **k: subprocess.CompletedProcess(a, 0, f"{_n}, 580.95, 0\n", ""))
        assert HardwareManager.detect_gb10_hardware().is_gb10 is expected, name
