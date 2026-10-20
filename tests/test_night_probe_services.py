"""Night Shift's open-session probe never counts a `ling` that runs as a service.

Every launcher subcommand (ling-rs/src/lib.rs) is a one-shot command, a hook or a server with no
person at a TUI, and the systemd units the launcher writes start `ling <subcommand> serve`. A
subcommand missing from `NON_INTERACTIVE` holds every night back while its service runs: `ling apps
serve` did until 2026-10-19, and night 12 (run from a checkout older than that) stalled on
`ling chat serve`, the messenger bridge's unit.
"""

import re
from pathlib import Path

import pytest

from dreamference.night_shift.night_shift_host import NON_INTERACTIVE, NightShiftHost

LING_RS = Path(__file__).resolve().parent.parent / "ling-rs"


def launcher_subcommands() -> set[str]:
    """The subcommands the launcher answers itself, read from its dispatch."""
    text = (LING_RS / "src" / "lib.rs").read_text(encoding="utf-8")
    return set(re.findall(r'user_args\[index\] == "([a-z-]+)"', text))


def unit_command_lines() -> list[list[str]]:
    """The `ling …` command lines of the units the launcher's crates write, program left out."""
    lines = []
    for path in sorted(LING_RS.rglob("*.rs")):
        for match in re.finditer(r"ExecStart=\{\}([^\\\n\"]*)", path.read_text(encoding="utf-8")):
            words = re.sub(r"\{[a-z_]*\}", "", match.group(1)).split()
            if words:
                lines.append(words)
    return lines


def test_every_launcher_subcommand_is_no_session():
    found = launcher_subcommands()
    assert {"app", "apps", "chat", "signal", "web", "node", "night"} <= found
    assert sorted(found - NON_INTERACTIVE) == []


def test_the_units_command_lines_are_found():
    assert ["chat", "serve"] in unit_command_lines()
    assert ["web", "serve"] in unit_command_lines()


@pytest.mark.parametrize(
    "args",
    [
        *unit_command_lines(),
        ["chat", "serve"],
        ["web", "serve", "--lan"],
        ["web", "serve", "--lan", "--port", "3101"],
        ["apps", "serve", "gmail"],
        ["signal", "serve"],
    ],
)
def test_a_service_command_line_is_no_session(args):
    assert not NightShiftHost.is_interactive(args)


def test_the_compatibility_mode_flag_is_read_with_its_value():
    """`--compat claude` is the launcher's option with a value (ling-rs/src/compat.rs), never a subcommand."""
    assert NightShiftHost.is_interactive(["--compat", "claude"])
    assert NightShiftHost.is_interactive(["--compat", "claude", "fix the bug"])
    assert not NightShiftHost.is_interactive(["--compat", "claude", "exec", "task"])
    assert not NightShiftHost.is_interactive(["compat", "status"])
    assert not NightShiftHost.is_interactive(["compat", "use", "claude"])
