"""ling-engine's ling-serve beside SGLang for the default model, opt-in in 1.7.2.

ling-engine specs/DREAMFERENCE_LING_ENGINE_INTEGRATION.md §12: the registry names the engine, the
launch builder emits only what follows the image, and the container comes from the same `docker run`
prefix as every engine, so host safety does not depend on the engine. Nothing here runs Docker or
reaches a model server: the commands are built and compared, and start_server's steps are stubbed.
"""

import re
from pathlib import Path

import pytest

from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.hardware import (
    ModelMatrixRegistry,
    get_model_launch_overrides,
    model_key_for_served_id,
)
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.hardware.model_matrix_registry import (
    DEFAULT_MODEL_ALIAS,
    ENGINE_LING,
    ENGINE_SGLANG,
    QWEN38_LING_ENGINE_COMMIT,
    QWEN38_LING_FLAT_CONTEXT,
    QWEN38_LING_IMAGE,
    QWEN38_SGLANG_IMAGE,
)
from dreamference.vllm_server.ling_serve_launch_builder import (
    LING_SERVE_PROGRAM,
    LingServeLaunchBuilder,
)
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

SERVED_ID = "RadixArk/Qwen3.8-27B-NVFP4"
DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "ling-serve" / "Dockerfile"


def split_at_image(cmd: list[str], image: str) -> tuple[list[str], list[str]]:
    """The docker prefix before the image, and the server's command after it."""
    at = cmd.index(image)
    return cmd[:at], cmd[at + 1 :]


def flag(args: list[str], name: str) -> str:
    """The value after `name`."""
    return args[args.index(name) + 1]


def test_ling_serve_is_the_default_and_sglang_is_the_fallback():
    # The owner's decisions (2026-10-28, 2026-10-29): ling-serve is the default in 1.8.0 with four
    # sessions at the full context; image input and LoRA stay on SGLang until 1.8.1.
    assert ModelMatrixRegistry.engines(DEFAULT_MODEL_ALIAS) == [ENGINE_LING, ENGINE_SGLANG]
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    assert recipe["engine"] == ENGINE_LING and recipe["docker_image"] == QWEN38_LING_IMAGE
    assert "lora" not in recipe
    # Naming the default's own engine is the default recipe.
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING) == recipe
    sglang = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG)
    assert sglang["engine"] == ENGINE_SGLANG and sglang["docker_image"] == QWEN38_SGLANG_IMAGE
    assert sglang["chat_template_patches"] and sglang["runtime_patches"] and sglang["lora"]
    with pytest.raises(ValueError, match="engines: ling, sglang"):
        get_model_launch_overrides(DEFAULT_MODEL_ALIAS, "vllm")


def test_the_image_is_pinned_by_digest_and_the_engine_commit_is_a_full_hash():
    # Another agent fetches files by this field (only for engines at or after a commit), so it
    # must be the full hash an ancestry check takes.
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert recipe["engine_commit"] == QWEN38_LING_ENGINE_COMMIT
    # 1.8.0's engine: ling-engine main with P4d and fair pairing merged (4853a93), paging and fair
    # pairing off by default; it has the page pool and the sparse head.
    assert QWEN38_LING_ENGINE_COMMIT.startswith("4853a93")
    assert recipe["engine_features"] == ["kv-pages", "sparse-head", "paging-switch", "fair-pairing"]
    # The owner's decision of 2026-10-29: the recipe does not turn paging on at 1.8.0.
    assert recipe["kv_pages"] is True  # this variant: the pool on
    assert re.fullmatch(r"[0-9a-f]{40}", QWEN38_LING_ENGINE_COMMIT)
    assert re.fullmatch(r"ghcr\.io/dreamference/ling-serve@sha256:[0-9a-f]{64}", QWEN38_LING_IMAGE)
    assert recipe["docker_image"] == QWEN38_LING_IMAGE


def test_both_engines_serve_the_same_checkpoint_and_context():
    ling = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    sglang = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG)
    assert ling["revision"] == sglang["revision"]
    assert sglang["max_model_len"] == 262144
    # Flat at 1.8.0 (the pool off), ling-serve's four sessions share three full contexts' positions.
    assert ling["kv_pages"] is True and ling["max_model_len"] == 262144
    assert QWEN38_LING_FLAT_CONTEXT * 4 == 262144 * 3 and QWEN38_LING_FLAT_CONTEXT % 2048 == 0
    assert ling["speculative_config"]["num_speculative_tokens"] == 12
    # The same memory envelope: the container cap does not change with the engine.
    assert ling["gpu_memory_utilization"] == sglang["gpu_memory_utilization"]
    assert ling["container_headroom_gb"] == sglang["container_headroom_gb"]


def test_ling_serve_runs_inside_the_same_guarded_container_with_the_nights_flags():
    cmd = VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, engine=ENGINE_LING)
    prefix, server = split_at_image(cmd, QWEN38_LING_IMAGE)
    sglang_prefix, _ = split_at_image(
        VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, engine=ENGINE_SGLANG),
        QWEN38_SGLANG_IMAGE,
    )

    # The engine-independent part is the same: name, cgroup cap, CPU limit, OOM score, mounts.
    shared = [
        a
        for a in sglang_prefix
        if not a.startswith(("HF_HUB_OFFLINE", "TORCHINDUCTOR", "HF_TOKEN"))
    ]
    assert all(a in prefix for a in shared if not a.endswith(":ro") and "sglang-patches" not in a)
    assert prefix[:2] == ["docker", "run"] and flag(prefix, "--name") == "dreamference-vllm-8000"
    assert "--oom-score-adj=800" in prefix and "--gpus" in prefix
    assert "--entrypoint" not in prefix

    # Night 15's launch (scripts/engine_ab_remote.sh), on Mightling's pinned files.
    assert server[0] == LING_SERVE_PROGRAM
    assert flag(server, "--model").endswith(
        "models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/52d1adc5f38aa5ebf099c29ed7025ba34cfbb854"
    )
    assert flag(server, "--served-model-name") == SERVED_ID
    assert flag(server, "--max-context") == "262144"
    assert flag(server, "--draft-block") == "12"
    assert "--graphs" in server and "--pdl" in server
    # Flags ling-serve does not have exit it with status 2 (`unknown argument`).
    for absent in (
        "--chat-template",
        "--api-key",
        "--sleep-on-idle",
        "--mem-fraction-static",
        "--tool-call-parser",
        "--revision",
        "--trust-remote-code",
    ):
        assert absent not in server


def test_every_flag_the_builder_emits_is_one_ling_serve_parses():
    # ling-serve's own argument parser at the shipped commit (src/serve/main.cpp), from its spec.
    known = {
        "--model",
        "--host",
        "--port",
        "--served-model-name",
        "--max-context",
        "--draft",
        "--draft-block",
        "--tree",
        "--lookup",
        "--lookup-min",
        "--pretokenizer",
        "--prefix-checkpoints",
        "--graphs",
        "--pdl",
        "--attn-bulk",
        "--attn-prefetch",
        "--keepalive-seconds",
        "--prefill-bf16",
        "--draft-bf16",
        "--sessions",
        "--session-protect-seconds",
        "--batch",
        "--prefill-chunk-contended",
        "--side-prefill",
        "--kv-pages",
        "--no-kv-pages",
        "--fair-pairing",
        "--vision",
    }
    for gate in (False, True):
        cmd = VLLMServerManager().build_launch_command(
            model=DEFAULT_MODEL_ALIAS, gate=gate, engine=ENGINE_LING
        )
        _, server = split_at_image(cmd, QWEN38_LING_IMAGE)
        assert {a for a in server if a.startswith("--")} <= known


def test_behind_the_gate_ling_serve_listens_on_loopback_and_the_gate_runs_in_its_image():
    from dreamference.vllm_server.model_gate import ModelGate

    cmd = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, engine=ENGINE_LING
    )
    _, server = split_at_image(cmd, QWEN38_LING_IMAGE)
    assert flag(server, "--host") == "127.0.0.1"
    assert flag(server, "--port") == str(ModelGate.engine_port(8000))
    # The gate runs `python3` in the engine's image, which is why the image carries python3.
    gate = ModelGate.run_command(8000, QWEN38_LING_IMAGE)
    assert gate[gate.index(QWEN38_LING_IMAGE) - 1] == "python3"
    assert re.search(r"^\s+python3 \\$", DOCKERFILE.read_text(), re.M)

    open_cmd = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=False, engine=ENGINE_LING
    )
    _, open_server = split_at_image(open_cmd, QWEN38_LING_IMAGE)
    assert flag(open_server, "--host") == "0.0.0.0" and flag(open_server, "--port") == "8000"


def test_an_api_key_is_refused_on_ling_serve_and_names_the_fallback():
    # Since the 1.8.0 switch the default engine refuses one too; SGLang takes it.
    for engine in (ENGINE_LING, None):
        with pytest.raises(RuntimeError, match="--engine sglang"):
            VLLMServerManager().build_launch_command(
                model=DEFAULT_MODEL_ALIAS, api_key="secret", engine=engine
            )
    cmd = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, api_key="secret", engine=ENGINE_SGLANG
    )
    assert "secret" in cmd


def test_the_served_id_names_the_default_entry():
    assert model_key_for_served_id(SERVED_ID) == DEFAULT_MODEL_ALIAS
    assert model_key_for_served_id(SERVED_ID, DEFAULT_MODEL_ALIAS) == DEFAULT_MODEL_ALIAS


def test_sglang_serves_adapters_and_ling_serve_none(monkeypatch):
    from dreamference.vllm_server.adapter_serving import AdapterServing

    # Adapters are verified against the checkpoint whichever engine serves it.
    assert ModelMatrixRegistry.get_lora_recipe(DEFAULT_MODEL_ALIAS) is not None
    assert ModelMatrixRegistry.get_lora_recipe(DEFAULT_MODEL_ALIAS, ENGINE_LING) is None
    assert ModelMatrixRegistry.get_lora_recipe(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG) is not None

    cleared: list[bool] = []
    monkeypatch.setattr(
        AdapterServing, "launch_adapters", classmethod(lambda cls, key: [{"name": "x"}])
    )
    monkeypatch.setattr(AdapterServing, "clear", classmethod(lambda cls: cleared.append(True)))
    assert AdapterServing.prepare(DEFAULT_MODEL_ALIAS, QWEN38_LING_IMAGE, True, ENGINE_LING) is True
    assert cleared == [True]


def test_server_args_of_finds_ling_serves_own_arguments():
    cmd = VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, engine=ENGINE_LING)
    args = LingServeLaunchBuilder.server_args_of(cmd)
    assert args is not None and args[0] == "--model"
    sglang = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, engine=ENGINE_SGLANG
    )
    assert LingServeLaunchBuilder.server_args_of(sglang) is None


def test_the_engine_is_a_config_key_through_the_usual_tiers(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.delenv("DREAMFERENCE_SERVER_ENGINE", raising=False)
    assert DreamferenceConfig(config_file=str(path)).server_engine is None

    monkeypatch.setenv("DREAMFERENCE_SERVER_ENGINE", "SGLang")
    assert DreamferenceConfig(config_file=str(path)).server_engine == "sglang"
    monkeypatch.setenv("DREAMFERENCE_SERVER_ENGINE", "sglnag")  # a typo falls through
    assert DreamferenceConfig(config_file=str(path)).server_engine is None
    monkeypatch.delenv("DREAMFERENCE_SERVER_ENGINE")

    path.write_text('server_engine = "sglang"\n')
    assert DreamferenceConfig(config_file=str(path)).server_engine == "sglang"
    assert DreamferenceConfig(config_file=str(path), server_engine="ling").server_engine == "ling"


def test_a_default_engine_is_not_written_back_to_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_SERVER_ENGINE", raising=False)
    path = tmp_path / "config.toml"
    DreamferenceConfig(config_file=str(path)).save_config(path)
    assert "server_engine" not in (path.read_text() if path.exists() else "")
    DreamferenceConfig(config_file=str(path), server_engine="sglang").save_config(path)
    assert 'server_engine = "sglang"' in path.read_text()


def test_server_start_names_the_engine_and_refuses_one_the_model_lacks(capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli

    assert Cli._server_engine(DEFAULT_MODEL_ALIAS, None, from_flag=False) is None
    assert "Engine: ling-serve (ling-engine); also: --engine sglang" in capsys.readouterr().out
    assert Cli._server_engine(DEFAULT_MODEL_ALIAS, "sglang", from_flag=True) == "sglang"
    assert "Engine: SGLang" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        Cli._server_engine("qwen3.8-27b-minima-nvfp4-dflash2", "ling", from_flag=True)
    assert stop.value.code == 2
    assert "cannot be served with ling" in capsys.readouterr().out


def test_a_failed_check_on_ling_serve_names_the_sglang_fallback_and_switches_nothing(capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli

    Cli._suggest_engine_fallback(DEFAULT_MODEL_ALIAS, "ling", "the tool-call check")
    out = capsys.readouterr().out
    assert "ling-serve failed the tool-call check" in out
    assert "ling-admin server start --engine sglang" in out
    # ling-serve as the default (since 1.8.0) gets the same suggestion.
    Cli._suggest_engine_fallback(DEFAULT_MODEL_ALIAS, None, "to start")
    assert "ling-serve failed to start" in capsys.readouterr().out
    # On SGLang, or a model without ling-serve, nothing is suggested.
    Cli._suggest_engine_fallback(DEFAULT_MODEL_ALIAS, "sglang", "to start")
    Cli._suggest_engine_fallback("qwen3.8-27b-minima-nvfp4-dflash2", None, "to start")
    assert capsys.readouterr().out == ""


def test_the_drift_check_compares_ling_serve_with_its_own_recipe():
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli

    running = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, engine=ENGINE_LING
    )
    assert Cli._detect_recipe_drift(DEFAULT_MODEL_ALIAS, 8000, running) == {
        "Recipe Match": "Running flags match the registry recipe"
    }
    # SGLang, the default, is compared with its own recipe, not reported as all drift.
    sglang = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, engine=ENGINE_SGLANG
    )
    assert Cli._detect_recipe_drift(DEFAULT_MODEL_ALIAS, 8000, sglang) == {
        "Recipe Match": "Running flags match the registry recipe"
    }
    drifted = [a if a != "262144" else "65536" for a in running]
    rows = Cli._detect_recipe_drift(DEFAULT_MODEL_ALIAS, 8000, drifted)
    assert "  drift --max-context" in rows


def test_the_drift_check_reads_a_flag_given_as_one_token_with_its_value():
    # A server launched by hand with `--max-context=262144` was reported as two drifts: no
    # `--max-context`, and an unknown flag `--max-context=262144`.
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli

    running = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, engine=ENGINE_LING
    )
    at = running.index("--max-context")
    joined = [*running[:at], f"--max-context={running[at + 1]}", *running[at + 2 :]]
    assert Cli._detect_recipe_drift(DEFAULT_MODEL_ALIAS, 8000, joined) == {
        "Recipe Match": "Running flags match the registry recipe"
    }


def _stub_start(monkeypatch, mgr, calls):
    """Stubs every step of start_server that would touch the machine."""
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(
        mgr, "ensure_docker_image", lambda image: calls.setdefault("image", image) and True
    )
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(
        mgr,
        "ling_serve_plan",
        lambda *a, **k: calls.get(
            "plan",
            {"sessions": 3, "batch": 3, "max_context": 262144, "planned_bytes": 82_400_000_000},
        ),
    )
    monkeypatch.setattr(
        mgr, "_reset_stale_compile_cache", lambda *a, **k: calls.setdefault("reset", True)
    )
    monkeypatch.setattr("dreamference.hardware.download_model", lambda *a, **k: True)
    monkeypatch.setattr(
        "dreamference.hardware.hardware_manager.HardwareManager.detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True,
                gpu_name="NVIDIA GB10",
                driver_version="0",
                arch="aarch64",
                total_unified_memory_gb=121.0,
                available_memory_gb=110.0,
                used_memory_gb=11.0,
                vram_gb=0.0,
            )
        ),
    )
    monkeypatch.setattr(
        mgr,
        "build_launch_command",
        lambda **kwargs: calls.setdefault("engine", kwargs.get("engine")) and None,
    )
    monkeypatch.setattr(
        "dreamference.vllm_server.chat_template_patcher.ChatTemplatePatcher.prepare",
        classmethod(
            lambda cls, *a: calls.setdefault("template", True) and Path("/nonexistent/t.jinja")
        ),
    )
    monkeypatch.setattr(
        "dreamference.vllm_server.sglang_runtime_patcher.SGLangRuntimePatcher.prepare",
        classmethod(lambda cls, image, patches: calls.setdefault("runtime_patch", True) and None),
    )


def test_start_server_on_ling_serve_pulls_its_image_and_prepares_nothing_of_sglangs(monkeypatch):
    mgr = VLLMServerManager()
    calls: dict = {}
    _stub_start(monkeypatch, mgr, calls)
    # The launch itself is stubbed out (no command to run); what happened before it is the point.
    with pytest.raises(TypeError):
        mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
    assert calls["image"] == QWEN38_LING_IMAGE
    assert "template" not in calls and "runtime_patch" not in calls and "reset" not in calls


def test_the_pre_load_check_follows_the_engines_plan_not_the_recipes_arena(monkeypatch, capsys):
    # 110 GB available: the recipe's 0.50 arena (~60 GB) plus the load peak would pass, the engine's
    # 100 GB plan plus the peak does not.
    mgr = VLLMServerManager()
    calls: dict = {
        "plan": {"sessions": 4, "batch": 4, "max_context": 262144, "planned_bytes": 109_000_000_000}
    }
    _stub_start(monkeypatch, mgr, calls)
    with pytest.raises(SystemExit):
        mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
    out = capsys.readouterr().out
    assert "Not enough memory to start ling-serve." in out
    assert "4 sessions x 262,144 tokens" in out


def test_the_pre_load_check_admits_a_plan_that_fits(monkeypatch):
    mgr = VLLMServerManager()
    calls: dict = {}  # the stub's 3 x 262,144 plan, 82.4 GB
    _stub_start(monkeypatch, mgr, calls)
    with pytest.raises(TypeError):  # the launch itself is stubbed out
        mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
    assert calls["engine"] == ENGINE_LING


def test_without_a_plan_the_check_falls_back_to_the_recipes_arena(monkeypatch, capsys):
    mgr = VLLMServerManager()
    calls: dict = {}
    _stub_start(monkeypatch, mgr, calls)
    monkeypatch.setattr(mgr, "ling_serve_plan", lambda *a, **k: None)
    with pytest.raises(TypeError):
        mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
    assert "gave no plan" in capsys.readouterr().out


def test_ling_serve_plan_runs_plan_only_in_the_image_and_reads_its_json_line(monkeypatch):
    import subprocess

    seen: list = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='noise\n{"sessions":3,"batch":3,"max_context":262144,"planned_bytes":82400000000}\n',
            stderr="",
        )

    monkeypatch.setattr("dreamference.vllm_server.vllm_server_manager.subprocess.run", run)
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    plan = VLLMServerManager.ling_serve_plan(QWEN38_LING_IMAGE, DEFAULT_MODEL_ALIAS, recipe)
    assert plan == {
        "sessions": 3,
        "batch": 3,
        "max_context": 262144,
        "planned_bytes": 82_400_000_000,
    }
    cmd = seen[0]
    assert (
        cmd[-1] == "--plan-only"
        and "--rm" in cmd
        and cmd[cmd.index("--gpus") + 1] == "all"  # libcuda comes from the container toolkit
        and cmd[cmd.index(QWEN38_LING_IMAGE) + 1] == "--model"
    )
    assert "--max-context" in cmd
    # The plan is asked of the launch the server will get: a paged engine ("kv-pages" in the pinned
    # recipe's features) is asked with the recipe's sessions, the pool and image input on (1.8.1), so
    # the tower's scratch and cache are in the plan; an engine before the pool with none of them, so a
    # pin moving across LING_ENGINE_KV_PAGES_SINCE changes both.
    paged = "kv-pages" in (recipe.get("engine_features") or [])
    pooled = paged and bool(recipe.get("kv_pages"))
    assert ("--sessions" in cmd) == paged and ("--batch" in cmd) == paged
    assert ("--kv-pages" in cmd) == pooled
    assert ("--vision" in cmd and cmd[cmd.index("--vision") + 1] == "on") == paged


def _plan_only_stdout(monkeypatch, stdout: str) -> dict | None:
    """Runs ling_serve_plan against a stubbed `docker run` that prints `stdout`."""
    import subprocess

    monkeypatch.setattr(
        "dreamference.vllm_server.vllm_server_manager.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=""),
    )
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    return VLLMServerManager.ling_serve_plan(QWEN38_LING_IMAGE, DEFAULT_MODEL_ALIAS, recipe)


def test_ling_serve_plan_returns_a_paged_engines_line_whole(monkeypatch):
    # ling-engine P4d (BATCHING §5.6) adds the pool to the line; the four keys the check reads are
    # unchanged, and the rest is kept for whoever reports the plan.
    import json

    p4d = {
        "kv_pages": True,
        "sessions": 4,
        "batch": 4,
        "max_context": 262144,
        "planned_bytes": 94_000_000_000,
        "kv_pool_bytes": 66_000_000_000,
        "kv_pool_pages": 15_800,
        "host_reserve_bytes": 16_000_000_000,
    }
    assert _plan_only_stdout(monkeypatch, f"loading\n{json.dumps(p4d)}\n") == p4d


def test_ling_serve_plan_is_none_when_the_line_lacks_a_key_the_check_reads(monkeypatch):
    line = '{"kv_pages":true,"sessions":4,"batch":4,"max_context":262144,"kv_pool_bytes":1}\n'
    assert _plan_only_stdout(monkeypatch, line) is None
    # A key present but not an integer is as good as missing.
    line = '{"sessions":4,"batch":4,"max_context":262144,"planned_bytes":"94 GB"}\n'
    assert _plan_only_stdout(monkeypatch, line) is None


# ling-serve keeps 16 GB of the host's MemAvailable for the host (ling-engine BATCHING §5.6, the
# default --host-reserve-mb), so the plan it reports is about MemAvailable - 16 GB. The pre-load check
# adds the load peak and the side services to it: a plan that takes all the engine may take therefore
# starts only when those two together stay under the engine's 16 GB.
ENGINE_HOST_RESERVE_GB = 16.0


@pytest.mark.parametrize(
    ("transient_gb", "side_gb", "starts"),
    [(8.0, 2.0, True), (12.0, 3.9, True), (14.0, 4.0, False), (16.5, 0.0, False)],
)
def test_the_pre_load_check_on_a_full_plan_turns_on_the_engines_16_gb_reserve(
    monkeypatch, capsys, transient_gb, side_gb, starts
):
    mgr = VLLMServerManager()
    available_gb = 110.0  # _stub_start's telemetry
    planned_gb = available_gb - ENGINE_HOST_RESERVE_GB
    calls: dict = {
        "plan": {
            "sessions": 4,
            "batch": 4,
            "max_context": 262144,
            "planned_bytes": int(planned_gb * 1e9),
        }
    }
    _stub_start(monkeypatch, mgr, calls)
    monkeypatch.setattr(mgr, "load_transient_gb", lambda model, weights_gb: (transient_gb, "test"))
    monkeypatch.setattr(
        mgr,
        "side_service_allowance",
        lambda: [("the forge, mightling-forge", side_gb)] if side_gb else [],
    )
    assert (transient_gb + side_gb < ENGINE_HOST_RESERVE_GB) == starts
    if starts:
        with pytest.raises(TypeError):  # past the check; the launch itself is stubbed out
            mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
        assert calls["engine"] == ENGINE_LING
        assert "Not enough memory" not in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit):
            mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_LING)
        out = capsys.readouterr().out
        assert "Not enough memory to start ling-serve." in out
        assert f"Plan:        {planned_gb:.2f} GB" in out
        assert f"Required:    {planned_gb + transient_gb + side_gb:.2f} GB" in out
        assert ("the forge, mightling-forge" in out) == bool(side_gb)


def test_ling_serve_plan_is_none_when_the_engine_refuses_or_the_image_lacks_the_flag(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        "dreamference.vllm_server.vllm_server_manager.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr="refused to start"),
    )
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert VLLMServerManager.ling_serve_plan(QWEN38_LING_IMAGE, DEFAULT_MODEL_ALIAS, recipe) is None


def test_start_server_on_sglang_prepares_its_patches(monkeypatch):
    mgr = VLLMServerManager()
    calls: dict = {}
    _stub_start(monkeypatch, mgr, calls)
    # The launch itself is stubbed out (no command to run); what happened before it is the point.
    with pytest.raises(TypeError):
        mgr.start_server(model=DEFAULT_MODEL_ALIAS, background=True, engine=ENGINE_SGLANG)
    assert calls["image"] == QWEN38_SGLANG_IMAGE
    assert calls.get("template") and calls.get("runtime_patch") and "reset" not in calls
    assert calls["engine"] == ENGINE_SGLANG


def test_the_image_records_the_engine_commit_in_its_labels():
    text = DOCKERFILE.read_text()
    assert 'org.opencontainers.image.revision="${ENGINE_COMMIT}"' in text
    assert 'ai.dreamference.ling-engine.commit="${ENGINE_COMMIT}"' in text
    # The base is pinned by digest, as every image Mightling runs.
    assert re.search(r"^ARG CUDA_BASE=nvidia/cuda@sha256:[0-9a-f]{64}$", text, re.M)


def test_the_drafter_is_one_pinned_constant_away_from_the_scalesearch_one():
    from dreamference.hardware.model_matrix_registry import (
        QWEN38_LING_DRAFTER,
        QWEN38_RTN_DRAFTER,
        QWEN38_SCALESEARCH_DRAFTER,
    )

    drafter = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)["speculative_config"]
    assert drafter["model"] == QWEN38_LING_DRAFTER["model"]
    assert drafter["revision"] == QWEN38_LING_DRAFTER["revision"]
    # Whatever ling-serve runs is pinned (by commit on the Hub, by SHA-256 as release assets) and
    # sized by a matrix entry.
    source = ModelMatrixRegistry.get_release_source(drafter["model"])
    assert re.fullmatch(r"[0-9a-f]{40}", drafter["revision"]) or (
        source is not None and source["revision"] == drafter["revision"]
    )
    assert ModelMatrixRegistry.get_spec(drafter["model"]) is not None
    assert QWEN38_LING_DRAFTER in (QWEN38_RTN_DRAFTER, QWEN38_SCALESEARCH_DRAFTER)
    # 1.7.2 serves the ScaleSearch drafter on both engines.
    assert QWEN38_LING_DRAFTER == QWEN38_SCALESEARCH_DRAFTER
    _, server = split_at_image(
        VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, engine=ENGINE_LING),
        QWEN38_LING_IMAGE,
    )
    repo = QWEN38_LING_DRAFTER["model"].replace("/", "--")
    assert flag(server, "--draft").endswith(f"models--{repo}/snapshots/{drafter['revision']}")


def test_the_image_digest_is_a_real_pin_not_the_placeholder():
    # The registry holds a placeholder digest until the image is pushed to GHCR; a node's
    # `server start --engine ling` cannot pull it, so this stays red until the pushed digest is pinned.
    digest = QWEN38_LING_IMAGE.rsplit(":", 1)[1]
    assert len(set(digest)) > 1, "pin the digest GHCR gave the push in QWEN38_LING_IMAGE"


def test_the_tool_call_checks_hints_name_ling_serves_own_parsing():
    from dreamference.vllm_server.tool_call_canary import ToolCallCanary

    ling = ToolCallCanary.parsers(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert "ling-serve's built-in qwen3_coder" in ling["tool"]
    assert "compiled into ling-serve" in ling["template"]
    # The default engine is ling-serve since 1.8.0; SGLang's hint names its parser flag.
    assert ToolCallCanary.parsers(DEFAULT_MODEL_ALIAS) == ling
    sglang = ToolCallCanary.parsers(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG)
    assert sglang["tool"] == "--tool-call-parser qwen3_coder"


# --- 1.8.0: four paged sessions, the default switch and the pin plumbing -------------------------


def test_image_input_is_on_on_a_paged_engine_and_untouched_on_an_older_one():
    # 1.8.1 reads images on ling-serve (1.8.0 said `--vision off`): `--vision on`, parsed since
    # ling-engine 689690c, an ancestor of the page pool, so only a "kv-pages" engine is given it, and
    # given it explicitly, so neither the start nor its plan depends on the engine's default.
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert recipe["vision"] is True
    older = {**recipe, "engine_features": []}
    flat = LingServeLaunchBuilder.server_args("RadixArk/Qwen3.8-27B-NVFP4", older, 8000, 262144)
    assert "--vision" not in flat
    # The pin has the flag, with the pool off (1.8.0's flat sessions) or on.
    pinned = LingServeLaunchBuilder.server_args("RadixArk/Qwen3.8-27B-NVFP4", recipe, 8000, 262144)
    assert flag(pinned, "--vision") == "on" and "--kv-pages" in pinned
    paged = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", _paged_recipe(), 8000, 262144
    )
    assert flag(paged, "--vision") == "on"
    assert LingServeLaunchBuilder.facts("img", {}, paged)["Image input"] == "On"
    assert "Image input" not in LingServeLaunchBuilder.facts("img", {}, flat)
    # 1.8.0's state stays one key away: `--vision off`, and status names SGLang for images.
    off = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", _paged_recipe(vision=False), 8000, 262144
    )
    assert flag(off, "--vision") == "off"
    assert LingServeLaunchBuilder.facts("img", {}, off)["Image input"].startswith("Off")
    # A recipe without the key leaves the engine's default.
    recipe_without = {k: v for k, v in _paged_recipe().items() if k != "vision"}
    assert "--vision" not in LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", recipe_without, 8000, 262144
    )
    # SGLang's recipe has no such key and its launch reads images.
    assert "vision" not in get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG)


def test_server_start_names_sglang_for_images_only_while_ling_serve_reads_none(capsys, monkeypatch):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli

    Cli._server_engine(DEFAULT_MODEL_ALIAS, None, from_flag=False)
    assert "reads no images" not in capsys.readouterr().out
    real = ModelMatrixRegistry.get_launch_overrides

    def without_vision(model, engine=None):
        overrides = real(model, engine)
        return {**overrides, "vision": False} if engine == ENGINE_LING else overrides

    monkeypatch.setattr(ModelMatrixRegistry, "get_launch_overrides", staticmethod(without_vision))
    Cli._server_engine(DEFAULT_MODEL_ALIAS, None, from_flag=False)
    assert "ling-serve reads no images; --engine sglang" in capsys.readouterr().out
    Cli._server_engine(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG, from_flag=True)
    assert "reads no images" not in capsys.readouterr().out


def test_the_vision_switch_is_older_than_the_page_pool():
    # The builder gates `--vision` on "kv-pages"; that is right only while the switch's commit is an
    # ancestor of LING_ENGINE_KV_PAGES_SINCE (checked against the submodule in a checkout).
    import subprocess

    from dreamference.hardware.model_matrix_registry import LING_ENGINE_KV_PAGES_SINCE

    engine = Path(__file__).resolve().parent.parent / "ling-engine"
    vision = "689690cd97cda7f54ff3a34cc6f602ea85bb706e"
    known = [
        subprocess.run(
            ["git", "-C", str(engine), "cat-file", "-e", c + "^{commit}"], capture_output=True
        )
        for c in (vision, LING_ENGINE_KV_PAGES_SINCE)
    ]
    if any(r.returncode != 0 for r in known):
        pytest.skip("the ling-engine submodule lacks the commits (fetch it)")
    ancestry = subprocess.run(
        [
            "git",
            "-C",
            str(engine),
            "merge-base",
            "--is-ancestor",
            vision,
            LING_ENGINE_KV_PAGES_SINCE,
        ],
        capture_output=True,
    )
    assert ancestry.returncode == 0


def _paged_recipe(**changes):
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    recipe["engine_features"] = ["kv-pages"]
    recipe["kv_pages"] = True  # the recipe at 1.8.1: the pool on
    return {**recipe, **changes}


def test_the_recipe_asks_for_four_sessions_but_an_engine_without_the_pool_does_not_get_them():
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert recipe["sessions"] == recipe["batch"] == 4
    # At the 1.7.2 pin (no "kv-pages"): the engine's own sessions, no flag it may not parse.
    older = {**recipe, "engine_features": []}
    flat = LingServeLaunchBuilder.server_args("RadixArk/Qwen3.8-27B-NVFP4", older, 8000, 262144)
    assert not {"--sessions", "--batch", "--kv-pages"} & set(flat)
    # At the 1.8.0 pin (paged engine, recipe's kv_pages off): four flat slots at the recipe's flat
    # context, nothing that turns the pool on.
    off = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, engine=ENGINE_LING
    )
    assert flag(off, "--sessions") == flag(off, "--batch") == "4"
    assert flag(off, "--max-context") == "262144" and "--kv-pages" in off
    assert "--vision" in off
    paged = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", _paged_recipe(), 8000, 262144
    )
    assert flag(paged, "--sessions") == flag(paged, "--batch") == "4"
    assert "--kv-pages" in paged and flag(paged, "--max-context") == "262144"
    assert paged.index("--kv-pages") < paged.index("--graphs")


def test_paging_is_on_and_fair_pairing_off_in_the_recipe_and_switchable_per_launch(monkeypatch):
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert recipe["kv_pages"] is True and recipe["fair_pairing"] is False
    monkeypatch.delenv("DREAMFERENCE_LING_PAGING", raising=False)
    monkeypatch.delenv("DREAMFERENCE_LING_FAIR_PAIRING", raising=False)
    features = ["kv-pages", "paging-switch", "fair-pairing"]

    def args(**changes):
        return LingServeLaunchBuilder.server_args(
            "RadixArk/Qwen3.8-27B-NVFP4",
            {**recipe, "engine_features": features, **changes},
            8000,
            262144,
        )

    off = args(kv_pages=False)
    # Flat and unfair, said explicitly; the sessions are the recipe's, flat or paged.
    assert "--no-kv-pages" in off and "--kv-pages" not in off and "--sessions" in off
    assert flag(off, "--fair-pairing") == "0"
    # The recipe turns either on; the environment of a night arm turns it back, or on, per launch.
    on = args(kv_pages=True, fair_pairing=True)
    assert "--kv-pages" in on and "--no-kv-pages" not in on and flag(on, "--sessions") == "4"
    assert flag(on, "--fair-pairing") == "1"
    monkeypatch.setenv("DREAMFERENCE_LING_PAGING", "1")
    monkeypatch.setenv("DREAMFERENCE_LING_FAIR_PAIRING", "1")
    armed = args()
    assert "--kv-pages" in armed and flag(armed, "--fair-pairing") == "1"
    monkeypatch.setenv("DREAMFERENCE_LING_PAGING", "0")
    monkeypatch.setenv("DREAMFERENCE_LING_FAIR_PAIRING", "0")
    assert "--no-kv-pages" in args(kv_pages=True, fair_pairing=True)
    assert flag(args(kv_pages=True, fair_pairing=True), "--fair-pairing") == "0"
    # A value that is not 0 or 1 is ignored; an engine without the features gets neither flag.
    monkeypatch.setenv("DREAMFERENCE_LING_PAGING", "yes")
    assert "--kv-pages" in args(kv_pages=True)
    old = args(engine_features=["kv-pages"], kv_pages=False)
    assert not {"--no-kv-pages", "--fair-pairing", "--kv-pages"} & set(old)


def test_the_paged_launch_uses_only_flags_ling_serve_parses():
    # ling-engine main's parser (src/serve/main.cpp) beside the shipped commit's.
    known = {
        "--model",
        "--host",
        "--port",
        "--served-model-name",
        "--max-context",
        "--draft",
        "--draft-block",
        "--graphs",
        "--pdl",
        "--sessions",
        "--batch",
        "--kv-pages",
        "--vision",
        "--plan-only",
    }
    args = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", _paged_recipe(), 8000, 262144
    )
    assert {a for a in args if a.startswith("--")} <= known


def test_server_status_reports_sessions_and_paging():
    args = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4", _paged_recipe(), 8000, 262144
    )
    facts = LingServeLaunchBuilder.facts("img", {}, args)
    assert facts["Sessions"] == "4 (batch 4)" and facts["KV cache"] == "Paged"
    flat = LingServeLaunchBuilder.server_args(
        "RadixArk/Qwen3.8-27B-NVFP4",
        {**get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING), "kv_pages": False},
        8000,
        262144,
    )
    flat_facts = LingServeLaunchBuilder.facts("img", {}, flat)
    assert flat_facts["Sessions"] == "4 (batch 4)" and flat_facts["KV cache"] == "Flat"
    assert flat_facts["Fair pairing"] == "Off"


def test_the_plan_only_check_follows_the_same_arguments_as_the_launch(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class Done:
            returncode = 0
            stdout = '{"sessions": 4, "batch": 4, "max_context": 262144, "planned_bytes": 1}\n'

        return Done()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    plan = VLLMServerManager.ling_serve_plan(
        QWEN38_LING_IMAGE, DEFAULT_MODEL_ALIAS, _paged_recipe()
    )
    assert plan and plan["sessions"] == 4
    cmd = seen["cmd"]
    assert cmd[-1] == "--plan-only" and "--kv-pages" in cmd and flag(cmd, "--sessions") == "4"
    assert flag(cmd, "--vision") == "on"


def test_flipping_the_default_engine_is_one_constant_and_keeps_sglang_as_the_fallback():
    from dreamference.hardware.model_matrix_registry import (
        QWEN38_DEFAULT_ENGINE,
        QWEN38_LING_RECIPE,
        QWEN38_SGLANG_RECIPE,
        split_engine_recipes,
    )

    # The switch position since 1.8.0: ling-serve (docs/dev/release-1.8.0.md).
    assert QWEN38_DEFAULT_ENGINE == ENGINE_LING
    recipes = {ENGINE_SGLANG: dict(QWEN38_SGLANG_RECIPE), ENGINE_LING: QWEN38_LING_RECIPE}
    default, others = split_engine_recipes(ENGINE_LING, recipes)
    assert default is QWEN38_LING_RECIPE and list(others) == [ENGINE_SGLANG]
    default, others = split_engine_recipes(ENGINE_SGLANG, recipes)
    assert default["engine"] == ENGINE_SGLANG and list(others) == [ENGINE_LING]


def test_with_ling_serve_as_the_default_sglang_is_still_one_flag_away(monkeypatch):
    # The matrix as it would read after the switch: the same two recipes, the roles swapped.
    from dreamference.hardware import model_matrix_registry as registry

    default, others = registry.split_engine_recipes(
        ENGINE_LING,
        {
            ENGINE_SGLANG: {**registry.QWEN38_SGLANG_RECIPE, "lora": registry.QWEN38_LORA_RECIPE},
            ENGINE_LING: registry.QWEN38_LING_RECIPE,
        },
    )
    spec = registry.ModelMatrixRegistry.MATRIX[DEFAULT_MODEL_ALIAS]
    swapped = type(spec)(**{**spec.__dict__, "launch_overrides": default, "engine_recipes": others})
    monkeypatch.setitem(registry.ModelMatrixRegistry.MATRIX, DEFAULT_MODEL_ALIAS, swapped)

    assert ModelMatrixRegistry.engines(DEFAULT_MODEL_ALIAS) == [ENGINE_LING, ENGINE_SGLANG]
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS)["engine"] == ENGINE_LING
    assert (
        get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG)["docker_image"]
        == QWEN38_SGLANG_IMAGE
    )
    cmd = VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS)
    assert QWEN38_LING_IMAGE in cmd
    rollback = VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, engine=ENGINE_SGLANG
    )
    assert QWEN38_SGLANG_IMAGE in rollback and QWEN38_LING_IMAGE not in rollback


def test_the_pin_script_moves_the_three_constants_and_refuses_bad_input():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "pin_ling_serve_image.py"
    spec = importlib.util.spec_from_file_location("pin_ling_serve_image", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.REGISTRY.read_text()
    commit, digest = "a" * 40, "sha256:" + "b" * 64
    new = module.repin(text, commit, digest, ["kv-pages"])
    assert f'QWEN38_LING_ENGINE_COMMIT: Final[str] = "{commit}"' in new
    assert f"ling-serve@{digest}" in new
    assert 'QWEN38_LING_ENGINE_FEATURES: Final[tuple[str, ...]] = ("kv-pages",)' in new
    assert new.count("\n") == text.count("\n")
    assert (
        'QWEN38_LING_ENGINE_FEATURES: Final[tuple[str, ...]] = ("kv-pages", "sparse-head")'
        in module.repin(text, commit, digest, ["kv-pages", "sparse-head"])
    )
    for bad in (("abc", digest, []), (commit, "sha256:12", []), (commit, digest, ["nope"])):
        with pytest.raises(ValueError):
            module.repin(text, *bad)


def test_server_status_names_the_engine_commit_drafter_and_step_graphs():
    from dreamference.vllm_server.ling_serve_launch_builder import (
        LING_ENGINE_COMMIT_LABEL,
        STEP_GRAPHS_FLAG,
    )

    recipe = {"speculative_config": {"model": "example/drafter", "revision": "d" * 40}}
    args = LingServeLaunchBuilder.server_args("example/target", recipe, 8000, None)
    facts = LingServeLaunchBuilder.facts("img", {LING_ENGINE_COMMIT_LABEL: "c" * 40}, args)
    assert facts["Engine"] == f"ling-serve (ling-engine {'c' * 12})"
    assert facts["Speculative"] == "DFlash2, block default, via models--example--drafter"
    assert facts["Step graphs"] == "Off"
    assert LingServeLaunchBuilder.facts("img", {}, [*args, STEP_GRAPHS_FLAG])["Step graphs"] == "On"
    # A flag left without its value, last on the line, reads as unset rather than failing.
    assert LingServeLaunchBuilder.facts("img", {}, ["--draft"])["Speculative"] == "Disabled"


def test_the_ling_engine_is_named_in_any_case():
    assert LingServeLaunchBuilder.is_ling({"engine": "Ling"})
    assert not LingServeLaunchBuilder.is_ling({"engine": "sglang"})


def _plan_from(monkeypatch, returncode, stdout):
    import subprocess

    monkeypatch.setattr(
        "dreamference.vllm_server.vllm_server_manager.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=""),
    )
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    return VLLMServerManager.ling_serve_plan(QWEN38_LING_IMAGE, DEFAULT_MODEL_ALIAS, recipe)


def test_a_ling_serve_plan_missing_a_figure_is_no_plan(monkeypatch):
    assert _plan_from(monkeypatch, 0, '{"sessions":3}\n') is None
    assert (
        _plan_from(
            monkeypatch, 0, '{"sessions":3,"batch":3,"max_context":"262144","planned_bytes":1}\n'
        )
        is None
    )


def test_the_last_plan_ling_serve_prints_is_the_one_taken(monkeypatch):
    early = '{"sessions":1,"batch":1,"max_context":4096,"planned_bytes":1}'
    final = '{"sessions":3,"batch":3,"max_context":262144,"planned_bytes":82400000000}'
    assert _plan_from(monkeypatch, 0, f"{early}\nnoise\n{final}\n")["sessions"] == 3


def test_a_plan_from_a_ling_serve_that_failed_is_not_taken(monkeypatch):
    plan = '{"sessions":3,"batch":3,"max_context":262144,"planned_bytes":82400000000}'
    assert _plan_from(monkeypatch, 1, f"{plan}\n") is None
