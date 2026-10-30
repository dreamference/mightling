"""Launch recipes: the registry's lookups, the speculative config a launch uses, and the compile cache reset."""

import subprocess

import pytest

from dreamference.hardware import model_key_for_served_id
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
from dreamference.vllm_server import vllm_server_manager
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


@pytest.fixture
def cache_home(tmp_path, monkeypatch):
    home = tmp_path / "vllm"
    monkeypatch.setattr(vllm_server_manager, "VLLM_CACHE_HOME", home)
    return home


@pytest.fixture
def docker_runs(monkeypatch):
    """Records the `docker run` the reset would make; the test sets `fail` to make it fail."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if docker_runs_state["fail"]:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    docker_runs_state = {"fail": False}
    monkeypatch.setattr(vllm_server_manager.subprocess, "run", run)
    return calls, docker_runs_state


def _stale_cache(home, signature):
    (home / "torch_compile_cache").mkdir(parents=True)
    (home / ".compile_signature").write_text(signature)


def test_an_unchanged_signature_keeps_the_cache(vllm_recipes, cache_home, docker_runs):
    signature = VLLMServerManager._compile_cache_signature("test-vllm-dflash")
    _stale_cache(cache_home, signature + "\n")
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert docker_runs[0] == []


def test_a_first_launch_records_the_signature_without_clearing(
    vllm_recipes, cache_home, docker_runs
):
    (cache_home / "torch_compile_cache").mkdir(parents=True)
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert docker_runs[0] == []
    assert (
        cache_home / ".compile_signature"
    ).read_text() == VLLMServerManager._compile_cache_signature("test-vllm-dflash")


def test_a_changed_depth_clears_the_cache_inside_the_image(vllm_recipes, cache_home, docker_runs):
    _stale_cache(
        cache_home, "example/vllm-dflash-int4-AutoRound|dflash|example/vllm-dflash-drafter|8"
    )
    assert (
        VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash", docker_image="img:1")
        is True
    )
    (cmd,) = docker_runs[0]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[cmd.index("--entrypoint") + 1 : cmd.index("--entrypoint") + 3] == ["rm", "img:1"]
    assert cmd[-1].endswith("/torch_compile_cache")
    assert (cache_home / ".compile_signature").read_text().endswith("|12")


def test_a_failed_clear_is_tried_again_on_the_next_launch(
    vllm_recipes, cache_home, docker_runs, capsys
):
    # The new signature used to be recorded whether or not the clear worked, so after one failure
    # the stale graph counted as current and was never cleared.
    old = "example/vllm-dflash-int4-AutoRound|dflash|example/vllm-dflash-drafter|8"
    _stale_cache(cache_home, old)
    docker_runs[1]["fail"] = True
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert "Could not clear the compile cache" in capsys.readouterr().out
    assert (cache_home / ".compile_signature").read_text() == old
    docker_runs[1]["fail"] = False
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is True
    assert len(docker_runs[0]) == 2


def test_a_changed_signature_without_a_cache_has_nothing_to_clear(
    vllm_recipes, cache_home, docker_runs
):
    # No graph was ever compiled here, so there is no container to start; the signature moves on.
    cache_home.mkdir(parents=True)
    (cache_home / ".compile_signature").write_text(
        "example/vllm-dflash-int4-AutoRound|dflash|example/vllm-dflash-drafter|8"
    )
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert docker_runs[0] == []
    assert (
        cache_home / ".compile_signature"
    ).read_text() == VLLMServerManager._compile_cache_signature("test-vllm-dflash")


def test_an_rm_that_exits_nonzero_is_a_failed_clear(vllm_recipes, cache_home, monkeypatch):
    # `docker run` reports a failed rm only through its exit status, not by raising.
    old = "example/vllm-dflash-int4-AutoRound|dflash|example/vllm-dflash-drafter|8"
    _stale_cache(cache_home, old)

    def run(cmd, check=False, **kwargs):
        result = subprocess.CompletedProcess(cmd, 1)
        if check:
            result.check_returncode()
        return result

    monkeypatch.setattr(vllm_server_manager.subprocess, "run", run)
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert (cache_home / ".compile_signature").read_text() == old


def test_an_unreadable_signature_counts_as_none(vllm_recipes, cache_home, docker_runs):
    # A directory where the file should be: reading it fails, which must not stop the launch.
    (cache_home / ".compile_signature").mkdir(parents=True)
    assert VLLMServerManager._reset_stale_compile_cache("test-vllm-dflash") is False
    assert docker_runs[0] == []


def test_the_signature_names_the_drafter(vllm_recipes):
    # vLLM's hash leaves the drafter out as it does the depth, so a different drafter at the same
    # depth needs a different signature.
    sig = VLLMServerManager._compile_cache_signature
    assert sig("test-vllm-dflash", draft_model="example/other-drafter") != sig("test-vllm-dflash")
    assert sig("test-vllm-dflash", draft_model="example/other-drafter").endswith("|12")
    assert sig("test-vllm-nvfp4") == "example/vllm-moe-NVFP4|mtp|none|3"


def test_without_a_draft_model_the_recipe_is_used_as_it_is(vllm_recipes):
    resolve = VLLMServerManager.resolve_speculative_config
    recipe = ModelMatrixRegistry.get_launch_overrides("test-vllm-dflash")["speculative_config"]
    # The depth argument alone changes nothing: it is the CLI's default, not a choice.
    assert resolve("test-vllm-dflash", num_speculative_tokens=4) == recipe
    assert resolve("test-vllm-nvfp4")["method"] == "mtp"
    assert resolve("not-a-registered-model") is None


def test_a_draft_model_replaces_a_self_speculation_recipe(vllm_recipes):
    config = VLLMServerManager.resolve_speculative_config("test-vllm-nvfp4", "example/drafter", 5)
    assert config == {"model": "example/drafter", "num_speculative_tokens": 5}


def test_a_draft_model_is_layered_onto_a_drafter_recipe_without_touching_the_registry(vllm_recipes):
    config = VLLMServerManager.resolve_speculative_config("test-vllm-dflash", "example/drafter", 6)
    assert config["method"] == "dflash" and config["attention_backend"] == "FLASH_ATTN"
    assert config["model"] == "example/drafter" and config["num_speculative_tokens"] == 6
    recipe = ModelMatrixRegistry.MATRIX["test-vllm-dflash"].launch_overrides["speculative_config"]
    assert (
        recipe["model"] == "example/vllm-dflash-drafter" and recipe["num_speculative_tokens"] == 12
    )
    # A registry alias for the drafter resolves to its repository.
    named = VLLMServerManager.resolve_speculative_config(
        "test-vllm-dflash", "test-vllm-dflash-draft"
    )
    assert named["model"] == "example/vllm-dflash-drafter" and named["num_speculative_tokens"] == 12


def test_a_served_id_finds_its_registry_entry(vllm_recipes):
    assert model_key_for_served_id("example/vllm-moe-NVFP4") == "test-vllm-nvfp4"
    # An adapter's id names its base.
    assert model_key_for_served_id("example/vllm-moe-NVFP4+my-adapter") == "test-vllm-nvfp4"
    assert model_key_for_served_id("example/not-served-here") is None


def test_a_preferred_key_wins_only_when_it_serves_that_checkpoint(vllm_recipes, monkeypatch):
    twin = ModelMatrixRegistry.MATRIX["test-vllm-nvfp4"]
    monkeypatch.setitem(ModelMatrixRegistry.MATRIX, "test-vllm-nvfp4-twin", twin)
    served = "example/vllm-moe-NVFP4"
    assert (
        model_key_for_served_id(served, preferred="test-vllm-nvfp4-twin") == "test-vllm-nvfp4-twin"
    )
    assert model_key_for_served_id(served, preferred="test-vllm-dflash") == "test-vllm-nvfp4"


def test_an_engine_the_model_has_no_recipe_for_is_refused(vllm_recipes):
    with pytest.raises(ValueError, match="no recipe for the engine 'sglang'"):
        ModelMatrixRegistry.get_launch_overrides("test-vllm-nvfp4", "sglang")
    # The default recipe's own engine, named explicitly, is the default recipe.
    assert (
        ModelMatrixRegistry.get_launch_overrides("test-vllm-nvfp4", "VLLM")["kv_cache_dtype"]
        == "fp8"
    )
    assert ModelMatrixRegistry.get_launch_overrides("") == {}
    assert ModelMatrixRegistry.get_launch_overrides("not-a-registered-model") == {}


def test_a_key_is_found_whatever_its_case_padding_and_separators(vllm_recipes):
    spec = ModelMatrixRegistry.MATRIX["test-vllm-nvfp4"]
    assert ModelMatrixRegistry.get_spec("\ttest-vllm-nvfp4\n") is spec
    assert ModelMatrixRegistry.get_spec("TEST_VLLM_NVFP4") is spec
    # A repository id with its separators swapped, and a display name without its spaces.
    assert ModelMatrixRegistry.get_spec("example/vllm_moe_nvfp4") is spec
    assert ModelMatrixRegistry.get_spec("TestvLLMrecipe(NVFP4+self-speculation)") is spec
    assert ModelMatrixRegistry.get_spec("example/vllm-moe") is None


def test_an_exact_repository_or_display_name_wins_over_a_loose_match(monkeypatch):
    from dreamference.hardware.model_spec import ModelSpec

    def spec(name, hf_repo_id):
        return ModelSpec(
            name=name,
            params_b=1.0,
            supported_precisions=["BF16"],
            min_memory_gb=1.0,
            max_memory_gb=2.0,
            compatible_gb10=True,
            notes="Test only.",
            hf_repo_id=hf_repo_id,
        )

    # The loose entries come first, so a lookup that skipped the exact match would stop at them.
    loose_repo, loose_name = spec("Loose A", "example/loose-a"), spec("Loose B", "example/loose-b")
    exact = spec("Repo B", "Example/Repo-A")
    monkeypatch.setattr(
        ModelMatrixRegistry,
        "MATRIX",
        {"example-repo-a": loose_repo, "repo-b": loose_name, "exact": exact},
    )
    assert ModelMatrixRegistry.get_spec("Example/Repo-A") is exact
    assert ModelMatrixRegistry.get_spec("Repo B") is exact
    assert ModelMatrixRegistry.get_spec("example_repo_a") is loose_repo


def test_an_engine_is_named_in_any_case():
    recipe = ModelMatrixRegistry.get_launch_overrides("qwen3.8-27b-nvfp4-dflash2", "LING")
    assert ModelMatrixRegistry.engine_of(recipe) == "ling"


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


def test_a_caller_value_wins_over_the_recipe_even_when_it_is_empty(vllm_recipes):
    build = VLLMServerManager().build_launch_command
    assert _flag(build(model="test-vllm-nvfp4"), "--reasoning-parser") == "qwen3"
    assert "--reasoning-parser" not in build(model="test-vllm-nvfp4", reasoning_parser="")


def test_an_auto_attention_backend_leaves_the_choice_to_the_recipe(vllm_recipes):
    cmd = VLLMServerManager().build_launch_command(
        model="test-vllm-nvfp4", attention_backend="auto"
    )
    assert _flag(cmd, "--attention-backend") == "flashinfer"


def test_a_70b_checkpoint_that_declares_its_quantization_gets_no_guess(vllm_recipes, monkeypatch):
    from dreamference.hardware.model_spec import ModelSpec

    for key, precision in (("test-vllm-70b-awq", "AWQ"), ("test-vllm-70b-bf16", "BF16")):
        monkeypatch.setitem(
            ModelMatrixRegistry.MATRIX,
            key,
            ModelSpec(
                name=f"Test 70B {precision}",
                params_b=70.0,
                supported_precisions=[precision],
                min_memory_gb=40.0,
                max_memory_gb=80.0,
                compatible_gb10=True,
                notes="Test only.",
                hf_repo_id=f"example/vllm-70B-{precision}",
            ),
        )
    build = VLLMServerManager().build_launch_command
    assert "--quantization" not in build(model="test-vllm-70b-awq")
    # The same name in BF16 is the one the guess exists for.
    assert _flag(build(model="test-vllm-70b-bf16"), "--quantization") == "fp8"


@pytest.mark.parametrize("engine", ["sglang", "ling"])
def test_an_engine_that_only_runs_in_docker_is_refused_without_it(engine, monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: False)
    with pytest.raises(RuntimeError, match="Docker is required"):
        mgr.build_launch_command(model="qwen3.8-27b-nvfp4-dflash2", engine=engine)


def test_a_swe_bench_cap_is_never_below_one(vllm_recipes, monkeypatch):
    import dataclasses

    spec = ModelMatrixRegistry.MATRIX["test-vllm-nvfp4"]
    monkeypatch.setitem(
        ModelMatrixRegistry.MATRIX,
        "test-vllm-nvfp4",
        dataclasses.replace(spec, swe_bench_max_parallel=0),
    )
    assert ModelMatrixRegistry.swe_bench_max_parallel("test-vllm-nvfp4") == 1


def test_vllm_is_given_the_api_key(vllm_recipes):
    build = VLLMServerManager().build_launch_command
    assert _flag(build(model="test-vllm-nvfp4", api_key="test-key"), "--api-key") == "test-key"
    assert "--api-key" not in build(model="test-vllm-nvfp4")


def test_vllm_is_given_no_attention_backend_when_nothing_chooses_one(vllm_recipes):
    cmd = VLLMServerManager().build_launch_command(model="test-vllm-dflash-draft")
    assert "--attention-backend" not in cmd


def test_a_batch_token_budget_below_one_is_left_to_vllm(vllm_recipes):
    build = VLLMServerManager().build_launch_command
    assert _flag(build(model="test-vllm-nvfp4"), "--max-num-batched-tokens") == "8192"
    assert "--max-num-batched-tokens" not in build(
        model="test-vllm-nvfp4", max_num_batched_tokens=0
    )


def test_only_an_entry_marked_for_vision_supports_it(vllm_recipes):
    assert ModelMatrixRegistry.supports_vision("test-vllm-dflash")
    assert not ModelMatrixRegistry.supports_vision("test-vllm-nvfp4")
    assert not ModelMatrixRegistry.supports_vision("not-a-registered-model")


def test_a_removed_model_is_recognised_with_padding_and_capitals():
    assert ModelMatrixRegistry.removed_in(" Qwen3.5-122B-A10B-Hybrid-DFlash\n") == "1.5.1"


def test_only_a_checkpoint_whose_every_precision_declares_itself_skips_the_guess(monkeypatch):
    import dataclasses

    from dreamference.hardware.model_spec import ModelSpec

    base = ModelSpec(
        name="Test precisions",
        params_b=1.0,
        supported_precisions=["BF16"],
        min_memory_gb=1.0,
        max_memory_gb=2.0,
        compatible_gb10=True,
        notes="Test only.",
        hf_repo_id="example/precisions",
    )
    for precisions, declares in ((["nvfp4"], True), (["NVFP4", "BF16"], False)):
        spec = dataclasses.replace(base, supported_precisions=precisions)
        monkeypatch.setitem(ModelMatrixRegistry.MATRIX, "test-precisions", spec)
        assert ModelMatrixRegistry.declares_own_quantization("test-precisions") is declares
