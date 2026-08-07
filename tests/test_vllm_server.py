import time
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
    assert "--tool-call-parser" in cmd
    idx = cmd.index("--tool-call-parser")
    assert cmd[idx + 1] == "hermes"

def test_vllm_build_launch_command_auto_tool_call_parser_llama():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="llama-3.3-70b")
    assert "--tool-call-parser" in cmd
    idx = cmd.index("--tool-call-parser")
    assert cmd[idx + 1] == "hermes"

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

def test_vllm_build_launch_command_docker(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(mgr, "is_vllm_installed", lambda: False)
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", port=8000)
    assert "docker" in cmd
    assert "vllm/vllm-openai:latest" in cmd
    img_idx = cmd.index("vllm/vllm-openai:latest")
    assert cmd[img_idx + 1] == "Qwen/Qwen2.5-Coder-32B-Instruct"

def test_vllm_environment_checks_real():
    mgr = VLLMServerManager()
    is_installed = mgr.is_vllm_installed()
    docker_available = mgr.is_docker_available()
    assert isinstance(is_installed, bool)
    assert isinstance(docker_available, bool)

def test_vllm_real_health_check():
    mgr = VLLMServerManager(host="http://localhost:8000")
    health = mgr.check_health(timeout=0.2)
    assert isinstance(health, bool)

def test_vllm_server_status_reporting_real():
    mgr = VLLMServerManager(host="http://localhost:8000")
    status = mgr.get_server_status()
    assert "host" in status
    assert "healthy" in status
    assert "models" in status
    assert "pid" in status

def test_vllm_startup_monitor():
    logs = []
    monitor = VLLMStartupMonitor(
        warn_timeout_sec=0.05,
        stuck_threshold_sec=0.1,
        check_interval_sec=0.02,
        progress_interval_sec=10.0,
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
        progress_interval_sec=10.0,
        log_callback=lambda m: logs.append(m)
    )
    monitor.start()
    time.sleep(0.05)
    monitor.notify_log_received()
    assert monitor.warned is False
    monitor.stop()

def test_vllm_startup_monitor_memory_progress_percent():
    logs = []
    used = {"gb": 0.0}
    monitor = VLLMStartupMonitor(
        warn_timeout_sec=60.0,
        stuck_threshold_sec=120.0,
        check_interval_sec=0.02,
        progress_interval_sec=0.05,
        expected_memory_gb=10.0,
        log_callback=lambda m: logs.append(m)
    )
    monitor._read_used_memory_gb = lambda: used["gb"]  # type: ignore[method-assign]
    used["gb"] = 0.0
    monitor.start()
    used["gb"] = 4.0
    pct, delta, expected = monitor.estimate_progress()
    assert pct == 40
    assert delta == 4.0
    assert expected == 10.0
    assert "~40%" in monitor._format_progress_suffix()
    time.sleep(0.15)
    monitor.stop()
    assert any("~40%" in line for line in logs)
