"""
Refine mode (specs/DREAMFERENCE_MIGHTLING_REFINE.md): the setting's tiers on the Python side, the one
text the product and the benchmark share, and the benchmark's prompts pinned to the ones it was
measured with. The launcher's half is tested in Rust (ling-rs/src/refine.rs).
"""

import hashlib
import re
from pathlib import Path

import pytest

import dreamference.config.dreamference_config as cfg_mod
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.night_shift.refine_prompt import NIGHT_WRITES, PIECES, RefinePrompt
from dreamference.swe_bench.swe_bench_instance_run import SCRATCH_MOUNT, SweBenchInstanceRun

REPO = Path(__file__).resolve().parent.parent
TEXTS = REPO / "ling-rs" / "prompts" / "refine.md"
LAUNCHER = REPO / "ling-rs" / "src" / "refine.rs"
SPEC = REPO / "specs" / "DREAMFERENCE_MIGHTLING_REFINE.md"


def test_refine_mode_is_off_by_default_on_both_sides():
    # Switching it on for everyone is one line on each side; the two must move together.
    assert cfg_mod.DEFAULT_MIGHTLING_REFINE is False
    if LAUNCHER.exists():
        match = re.search(r"pub const DEFAULT: bool = (true|false);", LAUNCHER.read_text())
        assert match and (match.group(1) == "true") == cfg_mod.DEFAULT_MIGHTLING_REFINE


def test_the_setting_resolves_through_the_same_tiers_as_the_launchers(tmp_path, monkeypatch):
    config = tmp_path / "dreamference.toml"
    config.write_text("mightling_refine = true\n")
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE", raising=False)
    assert DreamferenceConfig(config_file=str(config)).mightling_refine is True
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", "off")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine is False
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", "on")
    config.write_text("mightling_refine = false\n")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine is True
    assert DreamferenceConfig(config_file=str(config), mightling_refine=False).mightling_refine is False
    # An empty variable is unset, as in the launcher; a non-boolean in the file is ignored.
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", "")
    config.write_text('mightling_refine = "yes"\n')
    assert DreamferenceConfig(config_file=str(config)).mightling_refine is False


def test_only_a_non_default_setting_is_saved(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE", raising=False)
    path = tmp_path / "out.toml"
    DreamferenceConfig(config_file=str(tmp_path / "none.toml")).save_config(path)
    assert "mightling_refine" not in (path.read_text() if path.exists() else "")
    DreamferenceConfig(config_file=str(tmp_path / "none.toml"), mightling_refine=True).save_config(path)
    assert "mightling_refine = true" in path.read_text()


def test_the_launchers_texts_are_the_pythons_byte_for_byte():
    if not TEXTS.exists():
        pytest.skip("a release install has no ling-rs/ beside the package")
    pieces = RefinePrompt.parse_pieces(TEXTS.read_text())
    assert pieces == PIECES


# The benchmark's two prompts as the `im-refine` round sent them (bench/refine at 360ad99): a
# sha256 of each, composed for a fixed issue. A change here changes what the 100-task pair measures.
# The scratch mount's path is replaced by `<scratch>` first: the product's rename changes that path
# (and nothing else in these prompts), and must not fail this test.
MEASURED = {
    ("study", False): "b6710d383faf54bd8bfc5337496748ec8b251682ed9679661e6ee440d6ad7453",
    ("study", True): "a021b9025ff93deb6390cb55b547126fb141c7f5fbea69e3e90c2d59c4f40c7c",
    ("fix", False): "1076205584d145f158c649bb90471e24801df26c7987813a907bd3f62550727f",
    ("fix", True): "8403a1157db53213e49dbc8168f58bc79ce510e2fe660293e2132d3bc3ff3129",
    ("fix-empty", False): "4fccd5afd202174e94e3c46310eecd2af047e423dcd8f10c27056f84e0861d7b",
    ("fix-empty", True): "85f58a51ea6b5e42b66fe82292fb06cdc953f93ebf686e75a18ec1dc56328425",
}


@pytest.mark.parametrize("kind,code_index", sorted(MEASURED))
def test_the_benchmarks_prompts_are_the_ones_it_was_measured_with(kind, code_index):
    issue = "ISSUE {x}"
    if kind == "study":
        prompt = SweBenchInstanceRun.compose_refine_prompt(issue, code_index)
    else:
        prompt = SweBenchInstanceRun.compose_fix_prompt(issue, "REFINED" if kind == "fix" else "  ", code_index)
    prompt = prompt.replace(SCRATCH_MOUNT, "<scratch>")
    assert hashlib.sha256(prompt.encode()).hexdigest() == MEASURED[(kind, code_index)]


def test_the_products_study_prompt_shares_the_benchmarks_sections():
    study = RefinePrompt.compose_study("Add a `{task}` flag.", NIGHT_WRITES)
    bench = SweBenchInstanceRun.compose_refine_prompt("x")
    sections = PIECES["study-sections"]
    assert sections in study and sections in bench
    assert study.startswith("This is the first of two steps. Do not fix anything yet: study the task below")
    assert study.endswith("Task:\nAdd a `{task}` flag."), "a task's own braces stay as written"
    assert NIGHT_WRITES in study and "code_callers" not in study
    assert "`code_search` with the task's words" in RefinePrompt.compose_study("x", NIGHT_WRITES, code_index=True)
    assert "{" not in study.replace("{task}", "")


def test_the_products_fix_prompt_is_the_task_then_the_rules_then_the_description():
    prompt = RefinePrompt.compose_fix("Task:\nAdd a flag.", "1. Intent: a flag.")
    assert prompt.startswith("Task:\nAdd a flag.\n\n- A first step studied the task")
    assert "the task is authoritative: where the two disagree, follow the task." in prompt
    assert prompt.endswith("Refined description (written by the first step; it may be incomplete or wrong):\n"
                           "1. Intent: a flag.")
    assert RefinePrompt.fix_block(" ").endswith("(The first step wrote no description: work from the task alone.)")


def test_the_spec_records_the_default_and_the_patch_bytes():
    if not SPEC.exists():
        pytest.skip("specs are not shipped")
    text = SPEC.read_text()
    assert "off by default" in text and "No Codex patch" in text and "Patch bytes: 0" in text
