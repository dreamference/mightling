import time
from unittest.mock import MagicMock, patch
from dgxcoder.vllm_server import VLLMServerManager, VLLMStartupMonitor, VLLMServerStatus

def test_vllm_build_launch_command_default():
    mgr = VLLMServerManager(host="http://localhost:8000")
    cmd = mgr.build_launch_command(
        model="qwen2.5-coder-32b",
        port=8000,
        max_model_len=16384,
        gpu_memory_utilization=0.90
    )
    assert "--host" in cmd
    assert "0.0.0.0" in cmd
    assert "--port" in cmd
    assert "8000" in cmd
    assert "--max-model-len" in cmd
    assert "16384" in cmd
    assert "--gpu-memory-utilization" in cmd
    assert "0.9" in cmd
    assert "--enable-prefix-caching" in cmd
    assert "--enable-chunked-prefill" in cmd

def test_vllm_build_launch_command_speculative():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(
        model="qwen2.5-coder-32b",
        draft_model="qwen2.5-coder-1.5b",
        num_speculative_tokens=5
    )
    assert "--speculative-model" in cmd
    idx = cmd.index("--speculative-model")
    assert cmd[idx + 1] == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    assert "--num-speculative-tokens" in cmd
    tokens_idx = cmd.index("--num-speculative-tokens")
    assert cmd[tokens_idx + 1] == "5"

def test_vllm_build_launch_command_auto_fp8_for_70b():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen2.5-coder-72b")
    assert "--quantization" in cmd
    idx = cmd.index("--quantization")
    assert cmd[idx + 1] == "fp8"

def test_vllm_build_launch_command_attention_and_kv_cache():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(
        model="qwen2.5-coder-32b",
        attention_backend="FLASHINFER",
        kv_cache_dtype="fp8"
    )
    assert "--attention-backend" in cmd
    att_idx = cmd.index("--attention-backend")
    assert cmd[att_idx + 1] == "FLASHINFER"
    assert "--kv-cache-dtype" in cmd
    kv_idx = cmd.index("--kv-cache-dtype")
    assert cmd[kv_idx + 1] == "fp8"

def test_vllm_docker_fallback_command(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_vllm_installed", lambda: False)
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", port=8000)
    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    assert "dgxcoder-vllm-8000" in cmd
    assert "vllm/vllm-openai:latest" in cmd

def test_vllm_health_check_mock():
    mgr = VLLMServerManager(host="http://localhost:8000")
    
    mock_resp_success = MagicMock()
    mock_resp_success.status_code = 200
    with patch("requests.get", return_value=mock_resp_success):
        assert mgr.check_health() is True

    mock_resp_failure = MagicMock()
    mock_resp_failure.status_code = 500
    with patch("requests.get", return_value=mock_resp_failure):
        assert mgr.check_health() is False

def test_vllm_server_status_reporting():
    mgr = VLLMServerManager(host="http://localhost:8000")
    with patch.object(mgr, "check_health", return_value=True), \
         patch.object(mgr, "get_models", return_value=["Qwen/Qwen2.5-Coder-32B-Instruct"]):
        status = mgr.get_server_status()
        assert status["healthy"] is True
        assert "Qwen/Qwen2.5-Coder-32B-Instruct" in status["models"]

def test_vllm_startup_monitor():
    logs = []
    monitor = VLLMStartupMonitor(
        warn_timeout_sec=0.05,
        stuck_threshold_sec=0.1,
        check_interval_sec=0.02,
        log_callback=lambda m: logs.append(m)
    )
    monitor.start()
    time.sleep(0.15)
    monitor.stop()
    assert len(logs) > 0
    assert "vLLM Monitor" in logs[0]

def test_vllm_startup_monitor_reset_on_log():
    logs = []
    monitor = VLLMStartupMonitor(
        warn_timeout_sec=0.1,
        stuck_threshold_sec=0.2,
        check_interval_sec=0.02,
        log_callback=lambda m: logs.append(m)
    )
    monitor.start()
    time.sleep(0.05)
    monitor.notify_log_received()
    assert monitor.warned is False
    monitor.stop()
