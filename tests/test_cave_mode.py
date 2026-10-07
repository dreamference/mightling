"""Cave mode (`/cavemode`, specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md): the Python side of the setting, and
the two things Python and Rust must agree on -- the default level and the level texts."""

import re
from pathlib import Path

import pytest

import dreamference.config.dreamference_config as cfg_mod
from dreamference.config import DreamferenceConfig

REPO = Path(__file__).resolve().parent.parent
CAVE_RS = REPO / "ling-rs" / "src" / "cave.rs"
CRATE_TEXTS = REPO / "ling-rs" / "cave"
BENCH_TEXTS = REPO / "scripts" / "cave_mode_bench" / "levels"


def test_the_level_resolves_through_the_four_tiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_CAVE_MODE", raising=False)
    cfg_file = tmp_path / "tiers.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "ultra"

    cfg_file.write_text("mightling_cave_mode: lite\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "lite"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_CAVE_MODE", "FULL")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "full"
    assert DreamferenceConfig(config_file=str(cfg_file), mightling_cave_mode="off").mightling_cave_mode == "off"


def test_an_invalid_value_is_skipped_not_adopted(tmp_path, monkeypatch):
    # As in the launcher: a typo in one tier falls through to the next instead of becoming the level.
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text("mightling_cave_mode: lite\n")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_CAVE_MODE", "loud")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "lite"
    assert DreamferenceConfig(config_file=str(cfg_file), mightling_cave_mode="max").mightling_cave_mode == "lite"
    cfg_file.write_text("mightling_cave_mode: true\n")
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_CAVE_MODE")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "ultra"


def test_save_config_writes_only_a_non_default_level(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_CAVE_MODE", raising=False)
    cfg_file = tmp_path / "save.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "mightling_cave_mode" not in cfg_file.read_text()

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.mightling_cave_mode = "full"
    config.save_config()
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_cave_mode == "full"


def test_the_default_matches_the_launcher():
    match = re.search(r'pub const DEFAULT_MIGHTLING_CAVE_MODE: &str = "(\w+)";', CAVE_RS.read_text())
    assert match, "DEFAULT_MIGHTLING_CAVE_MODE not found in ling-rs/src/cave.rs"
    assert match.group(1) == cfg_mod.DEFAULT_MIGHTLING_CAVE_MODE
    assert cfg_mod.DEFAULT_MIGHTLING_CAVE_MODE in cfg_mod.MIGHTLING_CAVE_MODE_LEVELS


@pytest.mark.parametrize("name", ["lite", "full", "ultra"])
def test_the_shipped_texts_are_the_measured_ones(name):
    # The benchmark's verdict (spec §1.1) is about these exact bytes; editing a level means editing
    # both copies and re-running scripts/cave_mode_bench, which this test makes impossible to skip.
    for suffix in (".txt", ".reminder.txt"):
        shipped = (CRATE_TEXTS / f"{name}{suffix}").read_bytes()
        assert shipped == (BENCH_TEXTS / f"{name}{suffix}").read_bytes(), f"{name}{suffix} differs from the benchmark's"


def test_every_level_names_its_exemptions():
    # Spec §4: what never changes is carried in the level text itself.
    for name in ("lite", "full", "ultra"):
        text = (CRATE_TEXTS / f"{name}.txt").read_text()
        assert text.startswith("<cave_mode>\n") and text.rstrip().endswith("</cave_mode>")
        assert "Answer every question" in text
        assert "outlives the chat or that another model will read" in text
        assert "If the user asks for more detail" in text
    off = (CRATE_TEXTS / "off.txt").read_text()
    assert "Cave mode is off." in off and "no longer apply" in off
