"""The session monitor of the Claude plugin spec (Part B, phase M0): what Python and the launcher share.

The behaviour itself is tested in Rust (`ling-rs/monitor/`, `cargo test` in a copy of the folder);
nothing here reads a real transcript (specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md §7, §10).
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "ling-rs" / "monitor"


def test_ling_monitor_is_not_an_open_session_to_night_shift():
    # The timer runs `ling monitor check` every 5 minutes; it must never hold a night back.
    from dreamference.night_shift import NightShiftHost

    assert NightShiftHost.is_interactive(["monitor", "check"]) is False


def test_the_launcher_dispatches_monitor_before_it_looks_for_a_model():
    launcher = (REPO / "ling-rs" / "src" / "lib.rs").read_text()
    without_model = re.search(
        r"const COMMANDS_WITHOUT_MODEL: &\[&str\] = &\[(.*?)\];", launcher, re.S
    ).group(1)
    subcommands = re.search(
        r"pub const LAUNCHER_SUBCOMMANDS: &\[&str\] = &\[(.*?)\];", launcher, re.S
    ).group(1)
    assert '"monitor"' in without_model and '"monitor"' in subcommands
    assert launcher.index('user_args[index] == "monitor"') < launcher.index(
        "if !needs_model(&user_args, subcommand)"
    )
    assert 'ling-monitor = { path = "monitor" }' in (REPO / "ling-rs" / "Cargo.toml").read_text()


def test_the_crate_builds_inside_the_codex_workspace_without_a_second_copy_of_a_crate():
    # The crate pins versions itself so its tests run alone; they have to be the workspace's.
    manifest = (CRATE / "Cargo.toml").read_text()
    workspace = REPO / "codex" / "codex-rs" / "Cargo.toml"
    if not workspace.exists():  # the submodule is not checked out
        return
    declared = re.findall(r'^([\w-]+) = (?:"([^"]+)"|\{ version = "([^"]+)")', manifest, re.M)
    pinned = workspace.read_text()
    for name, plain, in_table in declared:
        if name in ("name", "version", "edition", "license", "path"):
            continue
        found = re.search(rf'^{name} = (?:"([^"]+)"|\{{ version = "([^"]+)")', pinned, re.M)
        assert found, name
        assert (found.group(1) or found.group(2)).lstrip("=") == (plain or in_table).lstrip("="), (
            name
        )


def test_the_monitor_never_calls_a_model_or_opens_a_socket():
    # M0 is plain code (D12): no HTTP client, no socket, no `ling exec`.
    manifest = (CRATE / "Cargo.toml").read_text()
    for crate in ("reqwest", "tokio", "hyper", "ureq"):
        assert f"\n{crate} =" not in manifest, crate
    source = "\n".join(p.read_text() for p in (CRATE / "src").glob("*.rs"))
    for forbidden in ("TcpStream", "UdpSocket", "TcpListener", '"exec"'):
        assert forbidden not in source, forbidden


def test_the_owners_rules_are_not_in_the_repository():
    # C5's commit identity and forbidden trailers live only in the user's config (§8).
    source = "\n".join(p.read_text() for p in (CRATE / "src").glob("*.rs"))
    assert "Co-Authored-By" not in source
    assert "@dreamference" not in source


def test_alert_templates_never_carry_the_task_description():
    # D13: an alert names the agent's ordinal, type and id, never its `.meta.json` description.
    checks = (CRATE / "src" / "checks.rs").read_text()
    assert ".description" not in checks.split("#[cfg(test)]")[0]
