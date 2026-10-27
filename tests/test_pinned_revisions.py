"""Every fetch of a model takes the commit the model registry pins, for the drafter too.

On 2026-10-27 `ling-admin model download` on a new node fetched the Hub's default branch
(319f741) instead of the pinned 52d1adc, because only `server start` passed the recipe's
revision. Here the Hub is a recorder in place of `huggingface_hub`; nothing reaches the network,
and the hub cache is the test's own folder.
"""

import os
import sys
import types

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware import ModelDownloader
from dreamference.hardware.model_matrix_registry import (
    DEFAULT_MODEL_ALIAS,
    QWEN38_SGLANG_RECIPE,
    ModelMatrixRegistry,
)

MODEL_PIN = QWEN38_SGLANG_RECIPE["revision"]
DRAFTER_REPO = QWEN38_SGLANG_RECIPE["speculative_config"]["model"]
DRAFTER_PIN = QWEN38_SGLANG_RECIPE["speculative_config"]["revision"]
DRAFTER_KEY = "qwen3.8-27b-dflash2-draft"
HUB_DEFAULT_BRANCH = "319f741"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """The test's own hub cache and a stand-in for huggingface_hub that records every fetch."""
    cache = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    fetched = []

    def snapshot_download(repo_id, token=None, revision=None):
        fetched.append((repo_id, revision))
        snapshot = (
            cache
            / ("models--" + repo_id.replace("/", "--"))
            / "snapshots"
            / (revision or HUB_DEFAULT_BRANCH)
        )
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "config.json").write_text("{}")
        return str(snapshot)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download)
    )
    return types.SimpleNamespace(cache=cache, fetched=fetched)


def snapshot_of(cache, repo, revision):
    folder = cache / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{}")
    return folder


def test_the_registry_pins_the_default_model_and_its_drafter():
    assert ModelMatrixRegistry.pinned_revision(DEFAULT_MODEL_ALIAS) == MODEL_PIN
    assert (
        ModelMatrixRegistry.pinned_revision(
            ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS)
        )
        == MODEL_PIN
    )
    # The drafter's entry carries no recipe: its pin is the one the recipes naming it give.
    assert ModelMatrixRegistry.pinned_revision(DRAFTER_KEY) == DRAFTER_PIN
    assert ModelMatrixRegistry.pinned_revision(DRAFTER_REPO) == DRAFTER_PIN
    assert ModelMatrixRegistry.pinned_revision("someone/unpinned-model") is None


def test_every_recipe_naming_a_drafter_pins_it_at_one_commit():
    # pinned_revision takes the first recipe that names a drafter; that is safe only while they agree.
    pins: dict[str, set] = {}
    for spec in ModelMatrixRegistry.MATRIX.values():
        for recipe in (spec.launch_overrides, *spec.engine_recipes.values()):
            spec_config = recipe.get("speculative_config") or {}
            if spec_config.get("model") and spec_config.get("revision"):
                pins.setdefault(spec_config["model"].lower(), set()).add(spec_config["revision"])
    assert pins and all(len(commits) == 1 for commits in pins.values()), pins


def test_a_download_without_a_revision_fetches_the_pinned_commit(hub):
    assert ModelDownloader.download_model(DEFAULT_MODEL_ALIAS) is True
    assert ModelDownloader.download_model(DRAFTER_KEY) is True
    assert hub.fetched == [
        (ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS), MODEL_PIN),
        (DRAFTER_REPO, DRAFTER_PIN),
    ]


def test_a_cache_holding_only_the_hubs_default_branch_is_fetched_again_at_the_pin(hub):
    repo = ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS)
    snapshot_of(hub.cache, repo, HUB_DEFAULT_BRANCH)
    assert not ModelDownloader.is_model_downloaded(DEFAULT_MODEL_ALIAS)
    assert ModelDownloader.download_model(DEFAULT_MODEL_ALIAS) is True
    assert hub.fetched == [(repo, MODEL_PIN)]
    assert ModelDownloader.is_model_downloaded(DEFAULT_MODEL_ALIAS)
    # A second download finds it and fetches nothing.
    assert ModelDownloader.download_model(DEFAULT_MODEL_ALIAS) is True
    assert len(hub.fetched) == 1


def test_the_snapshot_used_is_the_pinned_one_even_when_another_is_newer(hub):
    repo = ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS)
    pinned = snapshot_of(hub.cache, repo, MODEL_PIN)
    newer = snapshot_of(hub.cache, repo, HUB_DEFAULT_BRANCH)
    os.utime(pinned, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    assert ModelDownloader.get_model_snapshot_dir(DEFAULT_MODEL_ALIAS) == pinned


def test_model_download_fetches_the_model_and_its_drafter_at_their_pins(hub):
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "download", "--model", DEFAULT_MODEL_ALIAS])
    assert exit_info.value.code == 0
    assert hub.fetched == [
        (ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS), MODEL_PIN),
        (DRAFTER_REPO, DRAFTER_PIN),
    ]


def test_download_all_fetches_every_pinned_entry_at_its_pin(hub):
    ModelDownloader.download_all_models()
    fetched = dict(hub.fetched)
    for key, spec in ModelMatrixRegistry.MATRIX.items():
        if spec.compatible_gb10 and ModelMatrixRegistry.is_offered(key):
            assert fetched[spec.hf_repo_id] == ModelMatrixRegistry.pinned_revision(key), key
    assert fetched[DRAFTER_REPO] == DRAFTER_PIN
