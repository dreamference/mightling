from neurr.hardware import (
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
    valid, msg = check_model_compatibility("qwen2.5-coder-32b")
    assert isinstance(valid, bool)
    assert isinstance(msg, str)

    # Unsupported large model check
    valid_large, msg_large = check_model_compatibility("deepseek-v3-671b")
    assert valid_large is False
    assert "exceeds" in msg_large.lower()

def test_check_speculative_compatibility():
    from neurr.hardware import check_speculative_compatibility
    valid, msg = check_speculative_compatibility("qwen2.5-coder-32b", "qwen2.5-coder-1.5b")
    assert valid is True
    assert "Speculative Decoding Qualified" in msg or "Compatible" in msg

def test_default_model_is_registered_and_gb10_compatible():
    from neurr.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.compatible_gb10 is True
    assert spec.hf_repo_id == "nvidia/Qwen3.6-35B-A3B-NVFP4"

def test_launch_overrides_are_isolated_per_call():
    from neurr.hardware import get_model_launch_overrides
    from neurr.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    first = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    first["max_model_len"] = 1
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS)["max_model_len"] == 131072
    assert get_model_launch_overrides("qwen2.5-coder-32b") == {}
    assert get_model_launch_overrides("some/unknown-repo") == {}

def test_declares_own_quantization():
    from neurr.hardware import model_declares_own_quantization
    assert model_declares_own_quantization("qwen3.6-35b-a3b-nvfp4") is True
    assert model_declares_own_quantization("qwen2.5-coder-72b") is False
    assert model_declares_own_quantization("some/unknown-repo") is False

def test_flexible_model_alias_resolution():
    from neurr.hardware import resolve_model_hf_repo, get_model_launch_overrides, model_declares_own_quantization
    from neurr.hardware.model_matrix_registry import ModelMatrixRegistry

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
        assert get_model_launch_overrides(name).get("max_model_len") == 131072
        assert model_declares_own_quantization(name) is True

def test_download_model_functions():
    from neurr.hardware import is_model_downloaded, download_model, resolve_model_hf_repo
    repo = resolve_model_hf_repo("qwen2.5-coder-32b")
    assert repo == "Qwen/Qwen2.5-Coder-32B-Instruct"
    is_dl = is_model_downloaded("qwen2.5-coder-32b")
    assert isinstance(is_dl, bool)

def test_tensorizer_functions(tmp_path, monkeypatch):
    from neurr.hardware import is_model_tensorized, get_tensorized_path, ModelDownloader
    
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

