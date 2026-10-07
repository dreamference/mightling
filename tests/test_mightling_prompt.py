"""
Named system prompts (specs/DREAMFERENCE_MIGHTLING_PROMPT.md): the Python side of the choice, the
shipped `high-swe` text, and where Night Shift passes it. The launcher's own behaviour is tested in
Rust (mling-rs/src/prompt.rs).
"""

import re
from pathlib import Path

import pytest

from dreamference.config import dreamference_config as cfg_mod
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.night_shift import NightShiftHost, NightShiftSettings

REPO = Path(__file__).resolve().parent.parent
PROMPT_RS = REPO / "mling-rs" / "src" / "prompt.rs"
HIGH_SWE = REPO / "mling-rs" / "prompts" / "high-swe.md"
SPEC = REPO / "specs" / "DREAMFERENCE_MIGHTLING_PROMPT.md"

# The two edits §6.4 of the spec made to Appendix A's v1 for the text that ships (v2).
V2_EDITS = (
    ("Delete scratch files.",
     "Remove anything you created inside the repository; scratch files in /tmp can stay."),
    ("which exercise the behaviour through the public interface.",
     "which exercise the behaviour through the public interface. When your script shows the fix "
     "and the area's tests pass, stop: do not keep adding checks."),
)


def test_the_python_default_is_the_launchers():
    match = re.search(r'pub const DEFAULT_PROMPT: &str = "([a-z0-9-]+)";', PROMPT_RS.read_text())
    assert match, "DEFAULT_PROMPT not found in mling-rs/src/prompt.rs"
    assert match.group(1) == cfg_mod.DEFAULT_MIGHTLING_PROMPT


def test_the_shipped_high_swe_is_the_specs_v1_with_the_two_recorded_edits():
    # The pilot ran Appendix A verbatim; what ships is that text with §6.4's edits and nothing else.
    spec = SPEC.read_text()
    appendix = spec[spec.index("## Appendix A."):]
    v1 = re.search(r"````markdown\n(.*?)````\n", appendix, re.S).group(1)
    # 4,229 chars as the pilot ran it, when the product was named Puffin; the rename to Mightling
    # (2026-10-07) adds three characters to its one self-name.
    assert len(v1) == 4_232, "Appendix A is the text the pilot ran, renamed"
    v2 = v1
    for old, new in V2_EDITS:
        assert v2.count(old) == 1, old
        v2 = v2.replace(old, new)
    assert HIGH_SWE.read_text() == v2


def test_the_choice_takes_the_same_tiers_as_the_launcher(tmp_path, monkeypatch):
    config = tmp_path / "dreamference.toml"
    config.write_text('mightling_prompt = "high-swe"\n')
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT", raising=False)
    assert DreamferenceConfig(config_file=str(config)).mightling_prompt == "high-swe"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_PROMPT", "mine")
    assert DreamferenceConfig(config_file=str(config)).mightling_prompt == "mine"
    assert DreamferenceConfig(config_file=str(config), mightling_prompt="default").mightling_prompt == "default"
    # Something that cannot be a prompt's name falls through, as in the launcher.
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_PROMPT", "../etc/passwd")
    assert DreamferenceConfig(config_file=str(config)).mightling_prompt == "high-swe"
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT")
    empty = tmp_path / "empty.toml"
    empty.write_text("")
    assert DreamferenceConfig(config_file=str(empty)).mightling_prompt == "default"


@pytest.mark.parametrize("value, expected", [
    (" high-swe ", "high-swe"), ("v2", "v2"), ("default", "default"),
    ("High", None), ("a_b", None), ("-x", None), ("", None), (None, None), (1, None), ("x" * 65, None),
])
def test_prompt_names_are_read_as_the_launcher_reads_them(value, expected):
    assert DreamferenceConfig.parse_prompt_name(value) == expected


def test_save_config_keeps_a_chosen_prompt_and_writes_no_default(tmp_path, monkeypatch):
    # save_config() writes only the flat keys it knows: without the field, `mling-admin main-model
    # set` would drop a `mightling_prompt` that `mling prompt use` wrote.
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT", raising=False)
    path = tmp_path / "dreamference.toml"
    path.write_text('mightling_prompt = "high-swe"\n')
    DreamferenceConfig(config_file=str(path)).save_config(path)
    assert 'mightling_prompt = "high-swe"' in path.read_text()
    other = tmp_path / "other.toml"
    other.write_text("")
    DreamferenceConfig(config_file=str(other)).save_config(other)
    assert "mightling_prompt" not in other.read_text()


def test_night_shift_reads_its_prompt_and_mightling_prompt_is_not_a_session():
    assert NightShiftSettings({}).prompt is None
    assert NightShiftSettings({"prompt": " high-swe "}).prompt == "high-swe"
    assert NightShiftSettings({"prompt": ""}).prompt is None
    assert NightShiftHost.is_interactive(["prompt", "list"]) is False
    assert NightShiftHost.is_interactive(["prompt", "use", "high-swe"]) is False
