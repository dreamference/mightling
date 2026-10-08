"""Skills from other agents (specs/DREAMFERENCE_MIGHTLING_SKILLS.md): what the Python side and the docs
have to agree on with the launcher. The behaviour itself is tested in Rust (`ling-rs/skills/`,
`ling-rs/src/skills.rs`); nothing here reads a real skills folder.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "ling-rs" / "skills"


def test_mightling_skill_is_not_an_open_session_to_night_shift():
    # `ling skill list` in another terminal must not make a night run wait for a "session".
    from dreamference.night_shift import NightShiftHost
    assert NightShiftHost.is_interactive(["skill", "list"]) is False
    assert NightShiftHost.is_interactive(["skill", "add", "anthropic/pdf", "--yes"]) is False


def test_the_launcher_dispatches_skill_before_it_looks_for_a_model():
    launcher = (REPO / "ling-rs" / "src" / "lib.rs").read_text()
    without_model = re.search(r"const COMMANDS_WITHOUT_MODEL: &\[&str\] = &\[(.*?)\];", launcher, re.S).group(1)
    assert '"skill"' in without_model
    assert launcher.index('user_args[index] == "skill"') < launcher.index("if !needs_model(&user_args, subcommand)")


def test_the_user_guide_names_every_command_the_launcher_parses():
    usage = re.search(r'pub const USAGE: &str = "\\\n(.*?)";', (CRATE / "src" / "lib.rs").read_text(), re.S).group(1)
    commands = re.findall(r"^  (\w+)", usage, re.M)
    assert commands == ["list", "show", "add", "remove", "search", "enable", "disable", "source", "adopt"]
    guide = (REPO / "docs" / "ling.md").read_text()
    for command in commands:
        assert f"ling skill {command}" in guide or f"/ `{command} <name>`" in guide, command


def test_the_crate_builds_inside_the_codex_workspace_without_a_second_copy_of_a_crate():
    # The crate pins versions itself so its tests run alone; they have to be the workspace's, or
    # building ling compiles each of these twice. The launcher's manifest is how it gets in.
    # The Windows-only `junction` (directory junctions for the `from-*` links) is left out: the
    # workspace has no copy of it at all, so it adds one, not a second, and only on Windows.
    manifest = (CRATE / "Cargo.toml").read_text().split("[target.'cfg(windows)'.dependencies]")[0]
    workspace = (REPO / "codex" / "codex-rs" / "Cargo.toml")
    if not workspace.exists():  # the submodule is not checked out
        return
    declared = re.findall(r'^([\w-]+) = (?:"([^"]+)"|\{ version = "([^"]+)")', manifest, re.M)
    wanted = {name: plain or in_table for name, plain, in_table in declared}
    pinned = workspace.read_text()
    for name, version in wanted.items():
        if name in ("name", "version", "edition", "license", "path"):  # the [package] and [lib] keys
            continue
        found = re.search(rf'^{name} = (?:"([^"]+)"|\{{ version = "([^"]+)")', pinned, re.M)
        assert found, name
        assert (found.group(1) or found.group(2)).lstrip("=") == version.lstrip("="), name
    assert 'ling-skills = { path = "skills" }' in (REPO / "ling-rs" / "Cargo.toml").read_text()


def test_the_glossary_is_off_by_default_and_the_spec_says_why():
    glossary = (CRATE / "src" / "glossary.rs").read_text()
    assert "pub const DEFAULT_ON: bool = false;" in glossary
    spec = (REPO / "specs" / "DREAMFERENCE_MIGHTLING_SKILLS.md").read_text()
    assert "## 15. As built" in spec and "off by default" in spec
