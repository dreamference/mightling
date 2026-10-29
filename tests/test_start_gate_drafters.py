"""The start gate counts a drafter's weights with the target's against the arena.

MIGHTLING_ADVISOR_NODE §6.3 builds on it: "`start_server()` checks that weights plus drafters fit
the arena". A drafter is resident beside the target, whether the caller names it or the recipe
brings it (vllm_server_manager.start_server).
"""

import pytest

from dreamference.vllm_server import VLLMServerManager

MODEL = "qwen2.5-coder-32b"
ARENA_MESSAGE = "do not fit the configured memory arena"


def _gate(monkeypatch, weights, recipe=None, available=10.0):
    """A manager on a 128 GB GB10 whose start_server stops at a gate, touching nothing real."""
    import dreamference.hardware as hardware
    from dreamference.hardware.hardware_manager import HardwareManager, HardwareTelemetry
    from dreamference.vllm_server import vllm_server_manager as vsm

    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda image: True)
    monkeypatch.setattr(mgr, "_stop_index_scopes", lambda: None)
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(mgr, "_estimate_model_weights_gb", lambda model: weights[model])
    monkeypatch.setattr(mgr, "load_transient_gb", lambda model, weights_gb: (5.0, "test"))
    monkeypatch.setattr(mgr, "side_service_allowance", list)
    monkeypatch.setattr(vsm.subprocess, "check_output", lambda *a, **k: "")
    if recipe is not None:
        monkeypatch.setattr(
            hardware, "get_model_launch_overrides", lambda model, engine=None: recipe
        )
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True,
                gpu_name="NVIDIA GB10",
                driver_version="0",
                arch="aarch64",
                total_unified_memory_gb=128.0,
                available_memory_gb=available,
                used_memory_gb=128.0 - available,
                vram_gb=0.0,
            )
        ),
    )
    return mgr


def _refusal(mgr, capsys, **kwargs):
    with pytest.raises(SystemExit):
        mgr.start_server(model=MODEL, background=False, gate=False, **kwargs)
    return capsys.readouterr().out


RECIPE = {"gpu_memory_utilization": 0.5}  # a 64 GB arena on 128 GB


def test_the_target_alone_fits_the_arena(monkeypatch, capsys):
    # The control: 40 GB in a 64 GB arena passes the arena check and is refused later, on free memory.
    out = _refusal(_gate(monkeypatch, {MODEL: 40.0}, recipe=RECIPE), capsys)
    assert ARENA_MESSAGE not in out


def test_a_drafter_the_caller_names_is_counted_against_the_arena(monkeypatch, capsys):
    mgr = _gate(monkeypatch, {MODEL: 40.0, "a-drafter": 30.0}, recipe=RECIPE, available=120.0)
    out = _refusal(mgr, capsys, draft_model="a-drafter")
    assert ARENA_MESSAGE in out and "Weights:     70.00 GB" in out


def test_a_drafter_the_recipe_brings_is_counted_against_the_arena(monkeypatch, capsys):
    recipe = dict(RECIPE, speculative_config={"model": "the-recipe-drafter"})
    mgr = _gate(
        monkeypatch, {MODEL: 40.0, "the-recipe-drafter": 30.0}, recipe=recipe, available=120.0
    )
    out = _refusal(mgr, capsys)
    assert ARENA_MESSAGE in out and "Weights:     70.00 GB" in out
