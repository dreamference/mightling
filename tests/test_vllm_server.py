import pytest
import signal
import subprocess
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



def test_guided_decoding_default_backend(monkeypatch):
    from dreamference.config.dreamference_config import DEFAULT_GUIDED_DECODING_BACKEND
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: (0, 12))
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b")
    assert "--guided-decoding-backend" not in cmd
    assert "--structured-outputs-config.backend" in cmd
    idx = cmd.index("--structured-outputs-config.backend")
    assert cmd[idx + 1] == DEFAULT_GUIDED_DECODING_BACKEND

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
    """
    Builds a watchdog whose pressure samples come from `readings`, and records docker calls.

    `readings` are (avg10, avg60) pairs. Once exhausted the host reads as healthy, so a watchdog
    that has not tripped by then simply keeps idling rather than replaying the last sample.
    """
    from dreamference.vllm_server import psi_watchdog

    seq = list(readings)
    monkeypatch.setattr(
        psi_watchdog, "read_memory_pressure_full",
        lambda: seq.pop(0) if seq else (0.0, 0.0),
    )
    docker_calls = []

    def _fake_run(cmd, **kw):
        docker_calls.append(cmd)
        # Stand in for `docker inspect`: no such container, so the cgroup never resolves and the
        # watchdog falls back to `docker kill`. Cgroup resolution is covered separately.
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(psi_watchdog.subprocess, "run", _fake_run)
    wd = psi_watchdog.MemoryPressureWatchdog(
        "test-container", limit_pct=60.0, trip_duration_s=0.05,
        sustained_pct=25.0, sample_interval_s=0.01,
    )
    return wd, docker_calls

def test_watchdog_kills_container_on_sustained_pressure(monkeypatch):
    wd, docker_calls = _watchdog_with_readings(monkeypatch, [(90.0, 0.0)] * 40)
    assert wd.start() is True
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert ["docker", "kill", "test-container"] in docker_calls

def test_watchdog_ignores_pressure_that_does_not_persist(monkeypatch):
    # Alternating spikes with a calm minute behind them: never stays above the limit long enough
    # to trip the fast rule, and avg60 stays clear of the sustained rule.
    wd, docker_calls = _watchdog_with_readings(monkeypatch, [(90.0, 0.0), (0.0, 0.0)] * 40)
    assert wd.start() is True
    wd._thread.join(timeout=1)
    wd.stop()
    assert wd.tripped is False
    assert ["docker", "kill", "test-container"] not in docker_calls

def test_watchdog_trips_on_sustained_avg60_that_never_spikes(monkeypatch):
    # The shape the fast rule cannot see: avg10 dips below the limit on every other sample, so its
    # clock resets forever, while avg60 shows a minute of the machine getting nothing done. This
    # is the case that made a second rule necessary rather than just a shorter timer.
    wd, docker_calls = _watchdog_with_readings(monkeypatch, [(70.0, 30.0), (10.0, 30.0)] * 40)
    assert wd.start() is True
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert ["docker", "kill", "test-container"] in docker_calls

def test_watchdog_reports_which_rule_fired(monkeypatch):
    from dreamference.vllm_server import psi_watchdog

    wd, _ = _watchdog_with_readings(monkeypatch, [(75.0, 0.0)] * 40)
    seen = []
    wd._on_trip = lambda reason, pressure, window: seen.append((reason, pressure, window))
    wd.start()
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert seen and seen[0][0] == psi_watchdog.TRIP_REASON_SPIKE
    assert seen[0][1] == 75.0
    assert seen[0][2] >= wd.trip_duration_s

def test_watchdog_kills_cgroup_pids_without_forking(monkeypatch, tmp_path):
    # The point of resolving the cgroup ahead of time: the trip path must not need docker, which
    # under a reclaim stall may never get scheduled.
    from dreamference.vllm_server import psi_watchdog

    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n4243\n")

    wd, docker_calls = _watchdog_with_readings(monkeypatch, [(90.0, 0.0)] * 40)
    wd._cgroup_procs = str(procs)
    signalled = []
    monkeypatch.setattr(psi_watchdog.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert wd.start() is True
    wd._thread.join(timeout=5)
    assert wd.tripped is True
    assert signalled == [(4242, signal.SIGKILL), (4243, signal.SIGKILL)]
    assert ["docker", "kill", "test-container"] not in docker_calls

def test_watchdog_stops_retrying_cgroup_resolution(monkeypatch):
    # Resolution costs a fork and the loop runs for the life of the server, so a container whose
    # cgroup never appears must not mean `docker inspect` once a second for hours.
    from dreamference.vllm_server import psi_watchdog

    wd, docker_calls = _watchdog_with_readings(monkeypatch, [(0.0, 0.0)] * 500)
    assert wd.start() is True
    time.sleep(0.5)
    wd.stop()
    inspects = [c for c in docker_calls if "inspect" in c]
    assert len(inspects) == psi_watchdog.MAX_CGROUP_RESOLVE_ATTEMPTS

def test_watchdog_survives_a_raising_sample(monkeypatch):
    # An exception must cost one reading, not the whole guard: a watchdog that has silently
    # stopped watching is worse than one that never started, because nothing reports its absence.
    from dreamference.vllm_server import psi_watchdog

    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("transient")
        return (90.0, 0.0)

    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full", lambda: (0.0, 0.0))
    wd = psi_watchdog.MemoryPressureWatchdog(
        "test-container", limit_pct=60.0, trip_duration_s=0.05,
        sustained_pct=25.0, sample_interval_s=0.01,
    )
    monkeypatch.setattr(psi_watchdog.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""))
    assert wd.start() is True
    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full", _flaky)
    wd._thread.join(timeout=5)
    assert wd.tripped is True

def test_watchdog_trip_deadline_fits_inside_the_freeze_window(monkeypatch):
    # The 2026-08-14 reset came 20s after the first stalled frame. avg10 is itself a 10s decaying
    # average and needs ~10s to climb to the limit, so anything but a short hold cannot act in
    # time — which is exactly why the previous 15s hold never fired.
    from dreamference.vllm_server import psi_watchdog

    assert 10.0 + psi_watchdog.PSI_TRIP_DURATION_S <= 20.0

def test_watchdog_declines_to_start_without_psi(monkeypatch):
    from dreamference.vllm_server import psi_watchdog

    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full", lambda: None)
    wd = psi_watchdog.MemoryPressureWatchdog("test-container")
    assert wd.start() is False
    assert wd.tripped is False

# --- host safety gate ---

def _pass_all_host_checks(monkeypatch):
    """Makes every host safety check pass, so individual tests can fail exactly one."""
    from dreamference.vllm_server import vllm_server_manager as vsm

    monkeypatch.setattr(vsm.shutil, "which", lambda name: "/usr/bin/sar")
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_problem", classmethod(lambda cls: None))
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
    monkeypatch.setattr(
        VLLMServerManager, "_oom_handler_problem", classmethod(lambda cls: "no handler")
    )
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()

def test_host_safety_reports_every_failure_at_once(monkeypatch, capsys):
    vsm = _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(vsm.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        VLLMServerManager, "_oom_handler_problem",
        classmethod(lambda cls: "No userspace OOM handler is running."),
    )
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: 1.0))
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: 1))
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()
    out = capsys.readouterr().out
    assert "5 host safety checks failed" in out
    for expected in ("sysstat", "OOM handler", "Swap", "vm.min_free_kbytes", "vm.watermark_scale_factor"):
        assert expected in out

# --- earlyoom arming ---
#
# earlyoom was active and silent through the 2026-08-14 14:11 freeze: it acts only when available
# memory AND free swap are both low, and driver-pinned pages never reach swap. A running unit is
# therefore not evidence of an armed one, and only the swap gate distinguishes them.

@pytest.mark.parametrize("argv, fault", [
    (["/usr/bin/earlyoom", "-r", "3600"], "swap"),              # the config that froze the host
    (["/usr/bin/earlyoom"], "swap"),                             # no flags: defaults to -s 10
    (["/usr/bin/earlyoom", "-m", "5", "-s", "10"], "swap"),      # explicitly gated
    (["/usr/bin/earlyoom", "-s", "50,100"], "swap"),             # SIGTERM point still gated
    (["/usr/bin/earlyoom", "-s", "wat"], "swap"),                # unparseable: assume gated
    # Swap gate open, but the memory threshold kills healthy loads. This is the configuration
    # that SIGTERMed EngineCore three times on 2026-08-14 at 9.82%, 9.79% and 9.90% available.
    (["/usr/bin/earlyoom", "-m", "10", "-s", "100"], "memory"),
    (["/usr/bin/earlyoom", "-s", "100"], "memory"),              # -m absent: defaults to 10
    # Both right.
    (["/usr/bin/earlyoom", "-m", "5,2", "-s", "100", "-r", "60"], None),
    (["/usr/bin/earlyoom", "-m5,2", "-s100"], None),             # attached-value form
    (["/usr/bin/earlyoom", "-m", "5", "-s", "100,100"], None),   # SIGTERM point governs
])
def test_earlyoom_faults_cover_both_thresholds(argv, fault):
    result = VLLMServerManager._earlyoom_fault(argv)
    if fault is None:
        assert result is None
    else:
        assert result is not None and fault in result

def test_earlyoom_absolute_swap_floor_counts_as_ungated(monkeypatch):
    # -S is an absolute KiB floor rather than a percentage; it opens the gate once it exceeds
    # total swap, since free swap can then never be above it.
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: 64.0))
    opened = VLLMServerManager._earlyoom_fault(["earlyoom", "-m", "5", "-S", str(65 * 1024 ** 2)])
    gated = VLLMServerManager._earlyoom_fault(["earlyoom", "-m", "5", "-S", str(1024 ** 2)])
    assert opened is None
    assert gated is not None and "swap" in gated

def test_shipped_earlyoom_args_pass_the_check():
    # The arguments the hint tells the operator to install must themselves satisfy the check, or
    # following the instructions leaves the gate failing.
    import shlex
    from dreamference.vllm_server.vllm_server_manager import EARLYOOM_ARGS

    assert VLLMServerManager._earlyoom_fault(["earlyoom", *shlex.split(EARLYOOM_ARGS)]) is None

def test_earlyoom_threshold_clears_the_measured_load_trough():
    # Three consecutive loads on 2026-08-14 troughed at 9.82%, 9.79% and 9.90% available while
    # perfectly healthy. The threshold has to sit below all of them with room to spare, or the
    # handler kills what it is meant to protect.
    from dreamference.vllm_server.vllm_server_manager import MAX_EARLYOOM_MEM_PCT

    assert MAX_EARLYOOM_MEM_PCT < 9.79

def test_running_but_gated_earlyoom_is_reported_as_a_problem(monkeypatch):
    monkeypatch.setattr(VLLMServerManager, "_unit_is_active", staticmethod(lambda unit: False))
    monkeypatch.setattr(
        VLLMServerManager, "_process_argv",
        staticmethod(lambda name: ["/usr/bin/earlyoom", "-r", "3600"]),
    )
    problem = VLLMServerManager._oom_handler_problem()
    assert problem is not None
    assert "misconfigured" in problem
    # The operator has to be able to see what it is running with, or the claim is unfalsifiable.
    assert "-r 3600" in problem

def test_armed_earlyoom_satisfies_the_gate(monkeypatch):
    monkeypatch.setattr(VLLMServerManager, "_unit_is_active", staticmethod(lambda unit: False))
    monkeypatch.setattr(
        VLLMServerManager, "_process_argv",
        staticmethod(lambda name: ["/usr/bin/earlyoom", "-m", "5,2", "-s", "100"]),
    )
    assert VLLMServerManager._oom_handler_problem() is None

def test_systemd_oomd_satisfies_the_gate_without_earlyoom(monkeypatch):
    monkeypatch.setattr(
        VLLMServerManager, "_unit_is_active", staticmethod(lambda unit: unit == "systemd-oomd")
    )
    monkeypatch.setattr(VLLMServerManager, "_process_argv", staticmethod(lambda name: None))
    assert VLLMServerManager._oom_handler_problem() is None

def test_earlyoom_configure_command_is_valid_shell():
    # The first version of this hint was `sed -i 's|^EARLYOOM_ARGS=.*|...--prefer \x27(a|b)$\x27...|'`,
    # which fails two ways at once: `|` is both the sed delimiter and the regex alternation, and
    # `\x27` is not a quote escape in sed or in bash. It looked right and errored on paste. Parse
    # it the way a shell would, and check it survives a round trip through the file it writes.
    import shlex
    from dreamference.vllm_server.vllm_server_manager import (
        EARLYOOM_ARGS, EARLYOOM_CONFIGURE_CMD,
    )

    tokens = shlex.split(EARLYOOM_CONFIGURE_CMD)  # raises ValueError on unbalanced quotes
    assert tokens[0] == "printf"
    assert "sudo" in tokens and "tee" in tokens
    assert tokens[-1] == "/etc/default/earlyoom"

    # The line printf writes must be exactly what /etc/default/earlyoom is sourced for.
    written = next(t for t in tokens if t.startswith("EARLYOOM_ARGS="))
    assert written == f'EARLYOOM_ARGS="{EARLYOOM_ARGS}"'
    # And those args must be the ones that actually arm it, or the hint is cosmetic.
    assert VLLMServerManager._earlyoom_fault(["earlyoom", *shlex.split(EARLYOOM_ARGS)]) is None

def test_no_handler_at_all_is_reported(monkeypatch):
    monkeypatch.setattr(VLLMServerManager, "_unit_is_active", staticmethod(lambda unit: False))
    monkeypatch.setattr(VLLMServerManager, "_process_argv", staticmethod(lambda name: None))
    problem = VLLMServerManager._oom_handler_problem()
    assert problem is not None
    assert "No userspace OOM handler" in problem

def test_host_safety_ignores_sysctls_the_kernel_does_not_expose(monkeypatch):
    _pass_all_host_checks(monkeypatch)
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: None))
    VLLMServerManager.check_host_safety()  # must not raise
