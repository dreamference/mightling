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


def test_sglang_stays_the_default_and_ling_serve_is_opt_in():
    # The owner's decision (2026-10-26): ling-serve becomes the default once it reads images,
    # serves adapters and runs more than two requests at once.
    assert ModelMatrixRegistry.engines(DEFAULT_MODEL_ALIAS) == [ENGINE_SGLANG, ENGINE_LING]
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    assert recipe["engine"] == ENGINE_SGLANG and recipe["docker_image"] == QWEN38_SGLANG_IMAGE
    assert recipe["chat_template_patches"] and recipe["runtime_patches"] and recipe["lora"]
    # Naming the default's own engine is the default recipe.
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_SGLANG) == recipe
    ling = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert ling["engine"] == ENGINE_LING and "lora" not in ling
    with pytest.raises(ValueError, match="engines: sglang, ling"):
        get_model_launch_overrides(DEFAULT_MODEL_ALIAS, "vllm")


def test_the_image_is_pinned_by_digest_and_the_engine_commit_is_a_full_hash():
    # Another agent fetches files by this field (only for engines at or after a commit), so it
    # must be the full hash an ancestry check takes.
    recipe = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    assert recipe["engine_commit"] == QWEN38_LING_ENGINE_COMMIT
    # 1.7.2's engine: b8e9451 plus the parse-only tool-call fix; no sparse head yet (ec9dce8).
    assert QWEN38_LING_ENGINE_COMMIT.startswith("bdda3a2")
    assert recipe["engine_features"] == []
    assert re.fullmatch(r"[0-9a-f]{40}", QWEN38_LING_ENGINE_COMMIT)
    assert re.fullmatch(r"ghcr\.io/dreamference/ling-serve@sha256:[0-9a-f]{64}", QWEN38_LING_IMAGE)
    assert recipe["docker_image"] == QWEN38_LING_IMAGE


def test_both_engines_serve_the_same_checkpoint_and_context():
    ling = get_model_launch_overrides(DEFAULT_MODEL_ALIAS, ENGINE_LING)
    sglang = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    assert ling["revision"] == sglang["revision"]
    assert ling["max_model_len"] == sglang["max_model_len"] == 262144
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
    with pytest.raises(RuntimeError, match="--engine sglang"):
        VLLMServerManager().build_launch_command(
            model=DEFAULT_MODEL_ALIAS, api_key="secret", engine=ENGINE_LING
        )
    cmd = VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, api_key="secret")
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
    assert "Engine: SGLang; also: --engine ling" in capsys.readouterr().out
    assert Cli._server_engine(DEFAULT_MODEL_ALIAS, "ling", from_flag=True) == "ling"
    assert "Engine: ling-serve" in capsys.readouterr().out
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
    # On SGLang (the default), or a model without ling-serve, nothing is suggested.
    Cli._suggest_engine_fallback(DEFAULT_MODEL_ALIAS, None, "to start")
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


def _stub_start(monkeypatch, mgr, calls):
    """Stubs every step of start_server that would touch the machine."""
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(
        mgr, "ensure_docker_image", lambda image: calls.setdefault("image", image) and True
    )
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
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
    sglang = ToolCallCanary.parsers(DEFAULT_MODEL_ALIAS)
    assert sglang["tool"] == "--tool-call-parser qwen3_coder"
