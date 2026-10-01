"""`/airgapped` (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md): the Python side of the setting, and what
Python, the launcher and the web commands must agree on -- the default level, and one resolver."""

import re
from pathlib import Path

import dreamference.config.dreamference_config as cfg_mod
from dreamference.config import DreamferenceConfig

REPO = Path(__file__).resolve().parent.parent
LEAF_RS = REPO / "puffin-rs" / "airgapped" / "src" / "lib.rs"
WEB_COPY_RS = REPO / "puffin-web-rs" / "src" / "airgapped.rs"
LAUNCHER_RS = REPO / "puffin-rs" / "src" / "airgapped.rs"


def test_the_level_resolves_through_the_tiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "tiers.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "off"

    cfg_file.write_text("puffin_airgapped: duckduckgo\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "duckduckgo"
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "ON")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"
    assert DreamferenceConfig(config_file=str(cfg_file), puffin_airgapped="ddg").puffin_airgapped == "duckduckgo"


def test_an_invalid_value_is_skipped_not_adopted(tmp_path, monkeypatch):
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text("puffin_airgapped: duckduckgo\n")
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "sealed")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "duckduckgo"
    assert DreamferenceConfig(config_file=str(cfg_file), puffin_airgapped="max").puffin_airgapped == "duckduckgo"


def test_a_yaml_boolean_is_the_level_it_spells(tmp_path, monkeypatch):
    # YAML reads a bare `on` as true; taking that for "invalid" would silently leave the network on.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "bool.yaml"
    cfg_file.write_text("puffin_airgapped: on\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"


def test_save_config_writes_only_a_non_default_level(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "save.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "puffin_airgapped" not in cfg_file.read_text()

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.puffin_airgapped = "on"
    config.save_config()
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"


def test_the_default_and_the_level_names_match_the_rust_side():
    text = LEAF_RS.read_text()
    match = re.search(r'pub const DEFAULT_PUFFIN_AIRGAPPED: &str = "(\w+)";', text)
    assert match, "DEFAULT_PUFFIN_AIRGAPPED not found in puffin-rs/airgapped/src/lib.rs"
    assert match.group(1) == cfg_mod.DEFAULT_PUFFIN_AIRGAPPED
    for name in cfg_mod.PUFFIN_AIRGAPPED_LEVELS:
        assert f'=> "{name}"' in text, name


def test_the_web_commands_carry_the_same_resolver():
    # puffin-web-rs is built on its own, outside the Codex workspace, so it holds a copy; a level
    # the sandbox and the web commands resolved differently would be a restriction with a hole.
    assert WEB_COPY_RS.read_bytes() == LEAF_RS.read_bytes()


def test_only_on_is_described_as_having_no_network():
    # PUFFIN_EGRESS: nothing may call `off` or `duckduckgo` air-gapped.
    text = LAUNCHER_RS.read_text()
    duckduckgo = re.search(r'pub const DUCKDUCKGO_TEXT: &str = "([^"]+)";', text).group(1)
    assert "no network" not in duckduckgo and "air-gapped" not in duckduckgo
    assert "a preference, not a barrier" in text
