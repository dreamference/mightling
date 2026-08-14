import pytest
import time
from dreamference.vllm_server import VLLMServerManager, VLLMStartupMonitor, VLLMServerStatus
from dreamference.vllm_server.vllm_server_manager import DEFAULT_VLLM_IMAGE

def test_vllm_build_launch_command_default():
    mgr = VLLMServerManager(host="http://localhost:8000")
    cmd = mgr.build_launch_command(
        model="qwen2.5-coder-32b",
        port=8000,
        max_model_len=16384,
        gpu_memory_utilization=0.5
    )
    assert "--host" in cmd
    assert "0.0.0.0" in cmd
    assert "--port" in cmd
    assert "8000" in cmd
    assert "--max-model-len" in cmd
    assert "16384" in cmd
    assert "--gpu-memory-utilization" in cmd
    assert "0.5" in cmd
    assert "--enable-prefix-caching" in cmd
    assert "--enable-chunked-prefill" in cmd
    assert "--tool-call-parser" in cmd
    idx = cmd.index("--tool-call-parser")
    assert cmd[idx + 1] == "hermes"

def test_nvfp4_model_applies_registry_launch_recipe():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")
    assert cmd[cmd.index("--max-model-len") + 1] == "131072"
    assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.3"
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"
    assert cmd[cmd.index("--attention-backend") + 1] == "flashinfer"
    assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_xml"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "qwen3"
    
    import json
    spec_config_json = cmd[cmd.index("--speculative-config") + 1]
    spec_config = json.loads(spec_config_json)
    assert spec_config["num_speculative_tokens"] == 3

def test_nvfp4_recipe_env_crosses_container_boundary():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")
    assert "VLLM_MARLIN_USE_ATOMIC_ADD=1" in cmd
    # -e must precede the image name, or docker treats it as a container argument
    for var in ("VLLM_MARLIN_USE_ATOMIC_ADD=1",):
        assert cmd[cmd.index(var) - 1] == "-e"
        assert cmd.index(var) < cmd.index(DEFAULT_VLLM_IMAGE)

def test_nvfp4_recipe_emits_self_speculation_and_extra_args():
    import json
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")
    spec = json.loads(cmd[cmd.index("--speculative-config") + 1])
    assert spec["method"] == "mtp"
    assert spec["moe_backend"] == "triton"
    assert "--speculative-model" not in cmd
    assert cmd[cmd.index("--max-num-seqs") + 1] == "4"

def test_explicit_arguments_win_over_registry_recipe():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(
        model="qwen3.6-35b-a3b-nvfp4",
        max_model_len=8192,
        tool_call_parser="hermes",
        moe_backend="marlin",
        kv_cache_dtype="auto",
    )
    assert cmd[cmd.index("--max-model-len") + 1] == "8192"
    assert cmd[cmd.index("--tool-call-parser") + 1] == "hermes"
    assert cmd[cmd.index("--moe-backend") + 1] == "marlin"
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "auto"

def test_model_without_recipe_keeps_global_defaults():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b")
    assert cmd[cmd.index("--max-model-len") + 1] == "16384"
    assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.5"
    assert "--moe-backend" not in cmd
    assert "--reasoning-parser" not in cmd
    assert "--speculative-config" not in cmd
    assert "CUTE_DSL_ARCH=sm_121a" in cmd

def test_self_declaring_quantization_is_not_overridden():
    mgr = VLLMServerManager()
    # The name contains no 70b/72b marker, but the guard must hold for any self-declaring checkpoint.
    assert "--quantization" not in mgr.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")

def test_nvfp4_model_opts_out_of_tensorizer(monkeypatch):
    from dreamference.hardware.model_downloader import ModelDownloader
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "image_has_tensorizer", lambda img: True)
    monkeypatch.setattr(ModelDownloader, "is_model_tensorized", lambda key: True)
    monkeypatch.setattr(ModelDownloader, "get_tensorized_path", lambda key: "/tmp/model.tensors")
    cmd = mgr.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")
    assert "--load-format" not in cmd

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
    assert DEFAULT_VLLM_IMAGE in cmd
    img_idx = cmd.index(DEFAULT_VLLM_IMAGE)
    assert cmd[img_idx + 1] == "serve"
    assert cmd[img_idx + 2] == "Qwen/Qwen2.5-Coder-32B-Instruct"

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

def test_vllm_build_launch_command_tensorizer(monkeypatch, tmp_path):
    from dreamference.hardware.model_downloader import ModelDownloader
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "image_has_tensorizer", lambda img: True)

    # Mock tensorized file
    tfile = tmp_path / "model.tensors"
    tfile.write_text("dummy tensors content")
    monkeypatch.setattr(ModelDownloader, "is_model_tensorized", lambda key: True)
    monkeypatch.setattr(ModelDownloader, "get_tensorized_path", lambda key: tfile)

    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", use_tensorizer=True)
    assert "--load-format" in cmd
    fmt_idx = cmd.index("--load-format")
    assert cmd[fmt_idx + 1] == "tensorizer"
    assert "--model-loader-extra-config" in cmd
    cfg_idx = cmd.index("--model-loader-extra-config")
    assert "tensorizer_uri" in cmd[cfg_idx + 1]
    assert str(tfile) in cmd[cfg_idx + 1]

def test_vllm_build_launch_command_tensorizer_docker(monkeypatch, tmp_path):
    import os
    from dreamference.hardware.model_downloader import ModelDownloader
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "image_has_tensorizer", lambda img: True)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(mgr, "is_vllm_installed", lambda: False)
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)

    tpath = os.path.expanduser("~/.cache/dreamference/tensorizer/test/model.tensors")
    monkeypatch.setattr(ModelDownloader, "is_model_tensorized", lambda key: True)
    monkeypatch.setattr(ModelDownloader, "get_tensorized_path", lambda key: tpath)

    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", use_tensorizer=True)
    assert DEFAULT_VLLM_IMAGE in cmd
    assert "--load-format" in cmd
    assert "--model-loader-extra-config" in cmd
    cfg_idx = cmd.index("--model-loader-extra-config")
    assert "/root/.cache/dreamference/tensorizer/test/model.tensors" in cmd[cfg_idx + 1]



def test_guided_decoding_omitted_by_default():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b")
    assert "--guided-decoding-backend" not in cmd
    assert "--structured-outputs-config.backend" not in cmd

def test_guided_decoding_modern_flag(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: (0, 12))
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", guided_decoding_backend="xgrammar")
    assert "--structured-outputs-config.backend" in cmd
    idx = cmd.index("--structured-outputs-config.backend")
    assert cmd[idx + 1] == "xgrammar"
    assert "--guided-decoding-backend" not in cmd

def test_guided_decoding_legacy_flag_for_old_vllm(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: (0, 11))
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", guided_decoding_backend="outlines")
    assert "--guided-decoding-backend" in cmd
    idx = cmd.index("--guided-decoding-backend")
    assert cmd[idx + 1] == "outlines"
    assert "--structured-outputs-config.backend" not in cmd

def test_guided_decoding_undetectable_version_uses_modern_flag(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: None)
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b", guided_decoding_backend="auto")
    assert "--structured-outputs-config.backend" in cmd

def test_start_server_forwards_guided_decoding_backend(monkeypatch):
    mgr = VLLMServerManager()
    captured = {}
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda img=DEFAULT_VLLM_IMAGE: True)
    monkeypatch.setattr("dreamference.hardware.download_model", lambda *a, **k: None)
    # Host-safety and memory gates depend on machine state (sysstat, OOM handler, page cache, and
    # how much RAM happens to be free right now) that says nothing about argument forwarding, so
    # pin them rather than letting the test track whichever box it runs on.
    from dreamference.hardware.hardware_telemetry import HardwareTelemetry
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(
        "dreamference.hardware.hardware_manager.HardwareManager.detect_gb10_hardware",
        classmethod(lambda cls: HardwareTelemetry(
            is_gb10=True, gpu_name="NVIDIA GB10", driver_version="0", arch="aarch64",
            total_unified_memory_gb=128.0, available_memory_gb=128.0,
            used_memory_gb=0.0, vram_gb=0.0,
        )),
    )

    def fake_build(**kwargs):
        captured.update(kwargs)
        return ["docker", "run", DEFAULT_VLLM_IMAGE]

    class FakeProc:
        stdout = None
        pid = 1234

    monkeypatch.setattr(mgr, "build_launch_command", fake_build)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(mgr.streamer, "start_streaming", lambda stream: None)

    mgr.start_server(model="qwen2.5-coder-32b", guided_decoding_backend="guidance", background=True)
    assert captured["guided_decoding_backend"] == "guidance"

def test_detect_image_vllm_version_parses_and_caches(monkeypatch):
    mgr = VLLMServerManager()
    VLLMServerManager._image_probe_cache.clear()
    calls = []

    class Result:
        returncode = 0
        stdout = "vllm=0.12.1+cu129\ntensorizer=True\n"

    def fake_run(*a, **k):
        calls.append(a)
        return Result()

    monkeypatch.setattr(mgr, "ensure_docker_image", lambda img: True)
    monkeypatch.setattr("subprocess.run", fake_run)
    assert mgr.detect_image_vllm_version("some-image:tag") == (0, 12)
    assert mgr.detect_image_vllm_version("some-image:tag") == (0, 12)
    assert len(calls) == 1  # cached, probed only once
    VLLMServerManager._image_probe_cache.clear()

def test_default_image_is_pinned_not_latest():
    assert DEFAULT_VLLM_IMAGE == "dreamference-vllm-tensorizer:26.07-py3"
    assert not DEFAULT_VLLM_IMAGE.endswith(":latest")

def test_probe_image_reports_tensorizer(monkeypatch):
    mgr = VLLMServerManager()
    VLLMServerManager._image_probe_cache.clear()

    class Result:
        returncode = 0
        stdout = "vllm=0.13.0\ntensorizer=False\n"

    monkeypatch.setattr(mgr, "ensure_docker_image", lambda img: True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: Result())
    assert mgr.image_has_tensorizer("img:tag") is False
    assert mgr.detect_image_vllm_version("img:tag") == (0, 13)
    VLLMServerManager._image_probe_cache.clear()

def test_probe_image_failure_is_conservative(monkeypatch):
    mgr = VLLMServerManager()
    VLLMServerManager._image_probe_cache.clear()

    def boom(*a, **k):
        raise OSError("docker missing")

    monkeypatch.setattr(mgr, "ensure_docker_image", lambda img: True)
    monkeypatch.setattr("subprocess.run", boom)
    assert mgr.image_has_tensorizer("img:tag") is False
    assert mgr.detect_image_vllm_version("img:tag") is None
    VLLMServerManager._image_probe_cache.clear()

def test_ensure_docker_image_present(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_image_present", lambda img: True)
    assert mgr.ensure_docker_image("dreamference-vllm-tensorizer:26.07-py3") is True

def test_ensure_docker_image_builds_if_missing(monkeypatch):
    mgr = VLLMServerManager()
    present_state = {"present": False}
    build_calls = []

    def mock_is_image_present(img):
        return present_state["present"]

    def mock_run(cmd, check=False, **kwargs):
        if cmd[0] == "docker" and cmd[1] == "build":
            build_calls.append(cmd)
            present_state["present"] = True
            class Proc:
                returncode = 0
            return Proc()
        raise RuntimeError("Unexpected command")

    monkeypatch.setattr(mgr, "is_image_present", mock_is_image_present)
    monkeypatch.setattr("subprocess.run", mock_run)

    assert mgr.ensure_docker_image("dreamference-vllm-tensorizer:26.07-py3") is True
    assert len(build_calls) == 1
    assert "docker" in build_calls[0]
    assert "build" in build_calls[0]
    assert "dreamference-vllm-tensorizer:26.07-py3" in build_calls[0]

# --- memory pressure watchdog ---

def _watchdog_with_readings(monkeypatch, readings):
    """Builds a watchdog whose pressure samples come from `readings`, and records docker kills."""
    from dreamference.vllm_server import psi_watchdog

    seq = list(readings)
    monkeypatch.setattr(
        psi_watchdog, "read_memory_pressure_full_avg10",
        lambda: seq.pop(0) if seq else 0.0,
    )
    killed = []
    monkeypatch.setattr(
        psi_watchdog.subprocess, "run",
        lambda cmd, **kw: killed.append(cmd),
    )
    wd = psi_watchdog.MemoryPressureWatchdog(
        "test-container", limit_pct=60.0, trip_duration_s=0.05, sample_interval_s=0.01,
    )
    return wd, killed

def test_watchdog_kills_container_on_sustained_pressure(monkeypatch):
    wd, killed = _watchdog_with_readings(monkeypatch, [90.0] * 40)
    assert wd.start() is True
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert killed and killed[0] == ["docker", "kill", "test-container"]

def test_watchdog_ignores_pressure_that_does_not_persist(monkeypatch):
    # Alternating spikes: never stays above the limit long enough to trip.
    wd, killed = _watchdog_with_readings(monkeypatch, [90.0, 0.0] * 40)
    assert wd.start() is True
    wd._thread.join(timeout=1)
    wd.stop()
    assert wd.tripped is False
    assert killed == []

def test_watchdog_reports_pressure_and_duration_before_killing(monkeypatch):
    from dreamference.vllm_server import psi_watchdog

    wd, killed = _watchdog_with_readings(monkeypatch, [75.0] * 40)
    seen = []
    wd._on_trip = lambda pressure, held: seen.append((pressure, held))
    wd.start()
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert seen and seen[0][0] == 75.0
    assert seen[0][1] >= wd.trip_duration_s

def test_watchdog_declines_to_start_without_psi(monkeypatch):
    from dreamference.vllm_server import psi_watchdog

    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full_avg10", lambda: None)
    wd = psi_watchdog.MemoryPressureWatchdog("test-container")
    assert wd.start() is False
    assert wd.tripped is False

# --- host safety gate ---

def _pass_all_host_checks(monkeypatch):
    """Makes every host safety check pass, so individual tests can fail exactly one."""
    from dreamference.vllm_server import vllm_server_manager as vsm

    monkeypatch.setattr(vsm.shutil, "which", lambda name: "/usr/bin/sar")
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_active", staticmethod(lambda: True))
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: vsm.MIN_SWAP_GB))
    monkeypatch.setattr(
        VLLMServerManager, "_sysctl_int",
        staticmethod(lambda name: {
            "vm.min_free_kbytes": vsm.MIN_FREE_KBYTES,
            "vm.watermark_scale_factor": vsm.WATERMARK_SCALE_FACTOR,
        }[name]),
    )
    return vsm

def test_host_safety_passes_when_everything_is_configured(monkeypatch):
    _pass_all_host_checks(monkeypatch)
    VLLMServerManager.check_host_safety()  # must not raise

def test_host_safety_tolerates_mkswap_header_shortfall(monkeypatch):
    # `fallocate -l 64G` + mkswap reports a hair under the nominal size; that must still pass,
    # or the check can never be satisfied however large the swapfile is made.
    vsm = _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(
        VLLMServerManager, "_swap_total_gb", staticmethod(lambda: vsm.MIN_SWAP_GB - 0.001)
    )
    VLLMServerManager.check_host_safety()  # must not raise

def test_host_safety_blocks_on_genuinely_small_swap(monkeypatch):
    _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: 16.0))
    with pytest.raises(SystemExit) as exc:
        VLLMServerManager.check_host_safety()
    assert exc.value.code == 1

def test_host_safety_blocks_without_oom_handler(monkeypatch):
    _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_active", staticmethod(lambda: False))
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()

def test_host_safety_reports_every_failure_at_once(monkeypatch, capsys):
    vsm = _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(vsm.shutil, "which", lambda name: None)
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_active", staticmethod(lambda: False))
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: 1.0))
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: 1))
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()
    out = capsys.readouterr().out
    assert "5 host safety checks failed" in out
    for expected in ("sysstat", "OOM handler", "Swap", "vm.min_free_kbytes", "vm.watermark_scale_factor"):
        assert expected in out

def test_host_safety_ignores_sysctls_the_kernel_does_not_expose(monkeypatch):
    _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: None))
    VLLMServerManager.check_host_safety()  # must not raise
