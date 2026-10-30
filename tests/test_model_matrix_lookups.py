"""The registry's lookups, on a matrix of made-up entries so a model change cannot hide a regression."""

import pytest

from dreamference.hardware import model_matrix_registry
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
from dreamference.hardware.model_spec import ModelSpec


def _spec(name, repo, precisions=("BF16",), **kwargs):
    return ModelSpec(
        name=name,
        params_b=7.0,
        supported_precisions=list(precisions),
        min_memory_gb=10.0,
        max_memory_gb=20.0,
        compatible_gb10=True,
        notes="",
        hf_repo_id=repo,
        **kwargs,
    )


@pytest.fixture
def matrix(monkeypatch):
    entries = {
        # Listed first, so a fuzzy match on it would shadow an exact match on a later entry.
        "a-b": _spec("Shadow", "org/shadow"),
        "fake-model": _spec(
            "Fake Model 7B",
            "Org/Fake-Model",
            launch_overrides={"engine": "vllm"},
            engine_recipes={
                "sglang": {
                    "engine": "sglang",
                    "revision": "sglang-pin",
                    "lora": {"max_rank": 64},
                    "speculative_config": {"model": "Org/Drafter", "revision": "drafter-pin"},
                }
            },
            swe_bench_max_parallel=0,
        ),
        "by-name": _spec("A B", "org/by-name"),
        "by-repo": _spec("By Repo", "a/b"),
        "b": _spec("Display B", "org/b"),
        "named-b": _spec("B", "org/named-b"),
        "vision": _spec("Vision", "org/vision", supports_vision=True),
        "plain": _spec("Plain", "org/plain", precisions=()),
        "mixed": _spec("Mixed", "org/mixed", precisions=("NVFP4", "BF16")),
        "awq": _spec("Awq", "org/awq", precisions=("awq",)),
        "int4": _spec("Int4", "org/int4", precisions=("INT4",)),
    }
    monkeypatch.setattr(ModelMatrixRegistry, "MATRIX", entries)
    return entries


def test_an_entry_is_found_by_its_key_repo_or_name_exactly_before_any_fuzzy_match(matrix):
    assert ModelMatrixRegistry.get_spec("  Fake-Model  ") is matrix["fake-model"]
    # "a b" and "a/b" both normalise to the first entry's key, but each names a later entry exactly.
    assert ModelMatrixRegistry.get_spec("a b") is matrix["by-name"]
    assert ModelMatrixRegistry.get_spec(" A/B ") is matrix["by-repo"]
    # A key wins over another entry's display name.
    assert ModelMatrixRegistry.get_spec("b") is matrix["b"]


@pytest.mark.parametrize("query", ["fake_model", "org fake model", "fake_model_7b"])
def test_an_entry_is_found_by_its_key_repo_or_name_without_separators(matrix, query):
    assert ModelMatrixRegistry.get_spec(query) is matrix["fake-model"]


def test_a_served_id_no_entry_serves_names_no_entry(matrix):
    assert ModelMatrixRegistry.key_for_served_id("org/unknown", preferred="fake-model") is None
    assert (
        ModelMatrixRegistry.key_for_served_id("Org/Fake-Model+adapter", preferred="b")
        == "fake-model"
    )


def test_a_recipe_for_another_engine_is_found_in_any_case_with_its_lora_and_pin(matrix):
    assert ModelMatrixRegistry.engines("fake-model") == ["vllm", "sglang"]
    assert ModelMatrixRegistry.get_launch_overrides("fake-model", "SGLang")["engine"] == "sglang"
    # The default recipe has no LoRA settings and no pin: the other engine's are found.
    assert ModelMatrixRegistry.get_lora_recipe("fake-model") == {"max_rank": 64}
    assert ModelMatrixRegistry.pinned_revision("fake-model") == "sglang-pin"


def test_a_drafter_named_by_any_recipe_is_pinned_whatever_the_case(matrix):
    assert ModelMatrixRegistry.pinned_revision("ORG/DRAFTER") == "drafter-pin"


def test_a_swe_bench_cap_of_zero_still_lets_one_instance_run(matrix):
    assert ModelMatrixRegistry.swe_bench_max_parallel("fake-model") == 1


def test_only_an_entry_marked_for_vision_supports_it(matrix):
    assert ModelMatrixRegistry.supports_vision("vision") is True
    assert ModelMatrixRegistry.supports_vision("plain") is False


@pytest.mark.parametrize(
    "key, declares",
    [
        ("plain", False),  # no precision listed says nothing about the checkpoint
        ("mixed", False),  # a BF16 variant carries no quantization config
        ("awq", True),  # matched in any case
        ("int4", False),  # a bare bit width names no format the engine could read
    ],
)
def test_a_checkpoint_declares_its_own_quantization_only_when_every_precision_does(
    matrix, key, declares
):
    assert ModelMatrixRegistry.declares_own_quantization(key) is declares


def test_a_release_hosted_model_is_found_in_any_case_and_handed_out_as_a_copy(monkeypatch):
    monkeypatch.setattr(
        model_matrix_registry,
        "RELEASE_HOSTED_MODELS",
        {"Org/Release": {"revision": "v1", "files": {"model.gguf": {"sha256": "abc"}}}},
    )
    source = ModelMatrixRegistry.get_release_source("ORG/RELEASE")
    assert source == {"revision": "v1", "files": {"model.gguf": {"sha256": "abc"}}}
    source["files"]["model.gguf"]["sha256"] = "tampered"
    assert (
        ModelMatrixRegistry.get_release_source("org/release")["files"]["model.gguf"]["sha256"]
        == "abc"
    )
    assert ModelMatrixRegistry.get_release_source("") is None


def test_a_removed_model_is_recognised_with_stray_spaces(monkeypatch):
    monkeypatch.setattr(model_matrix_registry, "REMOVED_MODELS", {"old-model": "v1.2.0"})
    assert ModelMatrixRegistry.removed_in("  Old-Model ") == "v1.2.0"
