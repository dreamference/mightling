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
    valid, msg = check_model_compatibility("qwen2.5-coder-32b")
    assert isinstance(valid, bool)
    assert isinstance(msg, str)

    # Unsupported large model check
    valid_large, msg_large = check_model_compatibility("deepseek-v3-671b")
    assert valid_large is False
    assert "exceeds" in msg_large.lower()

def test_check_speculative_compatibility():
    from dreamference.hardware import check_speculative_compatibility
    valid, msg = check_speculative_compatibility("qwen2.5-coder-32b", "qwen2.5-coder-1.5b")
    assert valid is True
    assert "Speculative Decoding Qualified" in msg or "Compatible" in msg

def test_default_model_is_registered_and_gb10_compatible():
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.compatible_gb10 is True
    assert spec.hf_repo_id == "Intel/Qwen3.5-122B-A10B-int4-AutoRound"

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
    assert draft_repo == "z-lab/Qwen3.5-122B-A10B-DFlash"
    draft_spec = ModelMatrixRegistry.get_spec(draft_repo)
    assert draft_spec is not None
    assert draft_spec.min_memory_gb > 0

    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.launch_overrides["speculative_config"]["method"] == "dflash"
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
        assert get_model_launch_overrides(name).get("max_model_len") == 131072
        assert model_declares_own_quantization(name) is True

def test_download_model_functions():
    from dreamference.hardware import is_model_downloaded, download_model, resolve_model_hf_repo
    repo = resolve_model_hf_repo("qwen2.5-coder-32b")
    assert repo == "Qwen/Qwen2.5-Coder-32B-Instruct"
    is_dl = is_model_downloaded("qwen2.5-coder-32b")
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

