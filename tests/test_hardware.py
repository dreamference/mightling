from dgxcoder.hardware import (
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
    from dgxcoder.hardware import check_speculative_compatibility
    valid, msg = check_speculative_compatibility("qwen2.5-coder-32b", "qwen2.5-coder-1.5b")
    assert valid is True
    assert "Speculative Decoding Qualified" in msg or "Compatible" in msg
