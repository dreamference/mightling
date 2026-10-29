"""Behaviour found wrong in the 2026-10-29 review of `dreamference/vllm_server/`."""

import pytest

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
from dreamference.vllm_server import VLLMServerManager

SECRET = "hf_review_secret_value"


@pytest.mark.parametrize("model", ["qwen2.5-coder-32b", DEFAULT_MODEL_ALIAS])
def test_the_launch_command_names_the_token_but_never_carries_it(monkeypatch, model):
    # start_server prints the whole command; the token reaches the container through the
    # environment of the `docker run` it spawns, which start_server sets.
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    cmd = mgr.build_launch_command(model=model, hf_token=SECRET)
    assert not any(SECRET in part for part in cmd)
    at = cmd.index("HF_TOKEN")
    assert cmd[at - 1] == "-e"


def test_a_failed_compile_cache_clear_is_tried_again_on_the_next_launch(monkeypatch, tmp_path):
    import subprocess

    from dreamference.vllm_server import vllm_server_manager as vsm

    monkeypatch.setattr(vsm, "VLLM_CACHE_HOME", tmp_path)
    (tmp_path / "torch_compile_cache").mkdir()
    (tmp_path / ".compile_signature").write_text("old-model|mtp|3")
    attempts = []

    def failing_run(cmd, **kwargs):
        attempts.append(cmd)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vsm.subprocess, "run", failing_run)
    assert VLLMServerManager._reset_stale_compile_cache("qwen2.5-coder-32b") is False
    # The cache still holds the old graph, so the old signature has to stay to say so.
    assert (tmp_path / ".compile_signature").read_text() == "old-model|mtp|3"
    VLLMServerManager._reset_stale_compile_cache("qwen2.5-coder-32b")
    assert len(attempts) == 2


def test_the_advised_utilization_fits_beside_the_side_services(monkeypatch, capsys):
    import re

    from dreamference.hardware.hardware_manager import HardwareManager, HardwareTelemetry
    from dreamference.vllm_server import vllm_server_manager as vsm

    total, available, transient, side = 128.0, 70.0, 5.0, 10.0
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda image: True)
    monkeypatch.setattr(mgr, "_stop_index_scopes", lambda: None)
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(mgr, "_estimate_model_weights_gb", lambda model: 20.0)
    monkeypatch.setattr(mgr, "load_transient_gb", lambda model, weights: (transient, "test"))
    monkeypatch.setattr(mgr, "side_service_allowance", lambda: [("the forge", side)])
    monkeypatch.setattr(vsm.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True, gpu_name="NVIDIA GB10", driver_version="0", arch="aarch64",
                total_unified_memory_gb=total, available_memory_gb=available,
                used_memory_gb=total - available, vram_gb=0.0,
            )
        ),
    )
    with pytest.raises(SystemExit):
        mgr.start_server(model="qwen2.5-coder-32b", background=False, gate=False)
    out = capsys.readouterr().out
    advised = float(re.search(r"gpu_memory_utilization to at most (\d\.\d+)", out).group(1))
    # Taking the advice must pass the same check that refused this launch.
    assert advised * total + transient + side <= available
