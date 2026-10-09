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
from dreamference.night_shift.refine_prompt import NIGHT_WRITES, PIECES, VERSIONS, RefinePrompt
from dreamference.swe_bench.swe_bench_instance_run import SCRATCH_MOUNT, SweBenchInstanceRun

REPO = Path(__file__).resolve().parent.parent
TEXTS = REPO / "ling-rs" / "prompts" / "refine.md"
LAUNCHER = REPO / "ling-rs" / "src" / "refine.rs"
SPEC = REPO / "specs" / "DREAMFERENCE_MIGHTLING_REFINE.md"


def test_refine_mode_is_off_by_default_on_both_sides():
    # Switching it on for everyone is one line on each side; the two must move together. The same
    # holds for the texts' version, which stays v1, the measured one, until an A/B night decides.
    assert cfg_mod.DEFAULT_MIGHTLING_REFINE is False
    assert cfg_mod.DEFAULT_MIGHTLING_REFINE_VERSION == "v1"
    assert tuple(VERSIONS) == cfg_mod.MIGHTLING_REFINE_VERSIONS
    if LAUNCHER.exists():
        text = LAUNCHER.read_text()
        match = re.search(r"pub const DEFAULT: bool = (true|false);", text)
        assert match and (match.group(1) == "true") == cfg_mod.DEFAULT_MIGHTLING_REFINE
        match = re.search(r'pub const DEFAULT_VERSION: &str = "([a-z0-9]+)";', text)
        assert match and match.group(1) == cfg_mod.DEFAULT_MIGHTLING_REFINE_VERSION
        match = re.search(r"pub const VERSIONS: &\[&str\] = &\[([^\]]*)\];", text)
        assert match and tuple(re.findall(r'"([a-z0-9]+)"', match.group(1))) == tuple(VERSIONS)


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


def test_the_version_resolves_through_the_same_tiers_and_skips_what_is_not_one(tmp_path, monkeypatch):
    config = tmp_path / "dreamference.toml"
    config.write_text('mightling_refine_version = "v2"\n')
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION", raising=False)
    assert DreamferenceConfig(config_file=str(config)).mightling_refine_version == "v2"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION", "v1")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine_version == "v1"
    assert DreamferenceConfig(config_file=str(config), mightling_refine_version="v2").mightling_refine_version == "v2"
    # A value that is not a version passes to the next tier, as in the launcher.
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION", "v3")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine_version == "v2"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION", " V2 ")
    config.write_text("mightling_refine_version = 2\n")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine_version == "v2"
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION")
    assert DreamferenceConfig(config_file=str(config)).mightling_refine_version == "v1"


def test_only_a_non_default_setting_is_saved(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE", raising=False)
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_REFINE_VERSION", raising=False)
    path = tmp_path / "out.toml"
    DreamferenceConfig(config_file=str(tmp_path / "none.toml")).save_config(path)
    assert "mightling_refine" not in (path.read_text() if path.exists() else "")
    DreamferenceConfig(config_file=str(tmp_path / "none.toml"), mightling_refine=True,
                       mightling_refine_version="v2").save_config(path)
    assert "mightling_refine = true" in path.read_text()
    assert 'mightling_refine_version = "v2"' in path.read_text()


def test_the_launchers_texts_are_the_pythons_byte_for_byte():
    if not TEXTS.exists():
        pytest.skip("a release install has no ling-rs/ beside the package")
    pieces = RefinePrompt.parse_pieces(TEXTS.read_text())
    assert pieces == PIECES
    # refine-v2's own pieces are there, on both sides, and differ from v1's.
    assert pieces["study-sections-v2"] == VERSIONS["v2"]["sections"] != VERSIONS["v1"]["sections"]
    assert pieces["fix-rules-v2"] == VERSIONS["v2"]["rules"] != VERSIONS["v1"]["rules"]


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


# refine-v2's prompts as built for its first A/B night (spec §10). Not measured yet; pinned so that a
# change to v2 after a night has run it is a deliberate one, with a new version or new pins.
REFINE_V2 = {
    ("study", False): "3af9aab2b53706e30b0283fbdd2a38f978933b9f2a894af99df10bd6404888f3",
    ("study", True): "d9efd5d1bd424200a3784ddf303aab76c2d2af2b1382f005391f7eb5c7d83079",
    ("fix", False): "c64b79208eb9388fd99de907829451351d95c98563cb329330448a88aa07b125",
    ("fix", True): "fa4d3ec27fef04c0bafa3218e4620cb294fb998186ce5abf1da5c1faa5702256",
    ("fix-empty", False): "7116d5536cae0ab1e5286049f286274546f15bf51232e638c4809c987c9e2be4",
    ("fix-empty", True): "f60282608c6b86a4231af59547b35a8f58a231396d70de20706861c4a27e94dd",
}


@pytest.mark.parametrize("kind,code_index", sorted(REFINE_V2))
def test_the_benchmarks_v2_prompts_are_the_pinned_ones(kind, code_index):
    issue = "ISSUE {x}"
    if kind == "study":
        prompt = SweBenchInstanceRun.compose_refine_prompt(issue, code_index, version="v2")
    else:
        prompt = SweBenchInstanceRun.compose_fix_prompt(issue, "REFINED" if kind == "fix" else "  ", code_index,
                                                        version="v2")
    prompt = prompt.replace(SCRATCH_MOUNT, "<scratch>")
    assert hashlib.sha256(prompt.encode()).hexdigest() == REFINE_V2[(kind, code_index)]


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


def test_the_products_v2_prompts_differ_from_v1_in_the_sections_and_the_rules_alone():
    one, two = (RefinePrompt.compose_study("Add a `{task}` flag.", NIGHT_WRITES, True, version=v) for v in ("v1", "v2"))
    assert one.replace(VERSIONS["v1"]["sections"], "S") == two.replace(VERSIONS["v2"]["sections"], "S")
    assert "{" not in two.replace("{task}", "") and '"Expected to change"' in two
    one, two = (RefinePrompt.compose_fix("Task:\nAdd a flag.", "D", version=v) for v in ("v1", "v2"))
    assert one.replace(RefinePrompt.subject(VERSIONS["v1"]["rules"], "task"), "R") == \
        two.replace(RefinePrompt.subject(VERSIONS["v2"]["rules"], "task"), "R")
    assert "the task is authoritative" in two and "{subject}" not in two
    with pytest.raises(ValueError):
        RefinePrompt.compose_study("x", NIGHT_WRITES, version="v3")


def test_the_spec_records_the_default_and_the_patch_bytes():
    if not SPEC.exists():
        pytest.skip("specs are not shipped")
    text = SPEC.read_text()
    assert "off by default" in text and "No Codex patch" in text and "Patch bytes: 0" in text
