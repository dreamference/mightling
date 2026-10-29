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


def test_the_readers_only_open_inputs_for_reading_and_state_is_written_through_a_rename():
    # §7.1: every input is opened read-only; the monitor never locks, rotates, renames or writes it (D6).
    agents = (CRATE / "src" / "agents.rs").read_text().split("#[cfg(test)]")[0]
    for forbidden in (
        "OpenOptions",
        "fs::write",
        "remove_file",
        "fs::rename",
        "File::create",
        "flock",
        "set_permissions",
    ):
        assert forbidden not in agents, forbidden
    assert "File::open" in agents
    # §7.5: the summary and the state are written to a temporary file and renamed.
    summary = (CRATE / "src" / "summary.rs").read_text().split("#[cfg(test)]")[0]
    assert "fs::rename(&tmp, path)" in summary
    cli = (CRATE / "src" / "cli.rs").read_text().split("#[cfg(test)]")[0]
    assert cli.count("write_atomic(") >= 3  # summary.md, summary.json, state.json


def test_the_state_lives_under_the_dreamference_share_folder_and_the_units_are_user_units():
    # §7.6: ~/.local/share/dreamference/monitor/, two user units named mightling-monitor*.
    config = (CRATE / "src" / "config.rs").read_text()
    assert '".local/share/dreamference/monitor"' in config
    units = (CRATE / "src" / "units.rs").read_text()
    for name in (
        "mightling-monitor.service",
        "mightling-monitor.timer",
        "mightling-monitor-digest.service",
        "mightling-monitor-digest.timer",
    ):
        assert f'"{name}"' in units, name
    assert '".config/systemd/user"' in units
    assert '"--user"' in (CRATE / "src" / "cli.rs").read_text()
