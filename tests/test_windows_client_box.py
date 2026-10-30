r"""The Windows client as the AMD box runs it: where install.ps1 puts ling, -NoSandbox, the rule.

specs/DREAMFERENCE_MIGHTLING_AMD_NODES.md section 5.1 (what exists for Windows today) and 5.3 (the
setup checklist): install.ps1 installs into %LOCALAPPDATA%\Programs\Mightling\bin with no
administrator rights and asks once for elevation, which -NoSandbox skips; the discovery rule it
adds is for Private (and Domain) networks, never Public; its header says Smart App Control blocks
the unsigned preview; and `ling web` is not on Windows.

No PowerShell or Rust runs here: the files are read as text, as tests/test_install_ps1.py does.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_PS1 = ROOT / "install.ps1"
LAUNCHER_RS = ROOT / "ling-rs" / "src" / "lib.rs"


def script() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def elevated() -> str:
    """The script install.ps1 writes and runs elevated: the here-string between @' and '@."""
    text = script()
    return text[text.index("@'\n") + 3 : text.index("\n'@")]


def test_ling_goes_to_the_users_programs_folder_with_no_administrator_rights():
    text = script()
    assert (
        "$InstallDir = if ($env:MIGHTLING_INSTALL_DIR) { $env:MIGHTLING_INSTALL_DIR } "
        'else { Join-Path $env:LOCALAPPDATA "Programs\\Mightling\\bin" }'
    ) in text
    # The one Administrator prompt belongs to the sandbox, firewall and audit step, after the install.
    step = text.index("# -- the sandbox, the firewall and the audit: one Administrator prompt")
    assert text.count("-Verb RunAs") == 1
    assert text.index("-Verb RunAs") > step


def test_no_sandbox_skips_the_administrator_step_and_says_so():
    text = script()
    assert "[switch]$NoSandbox" in text
    skip = text.index("if ($NoSandbox) {")
    said = text.index(
        'Say "Skipped the Windows sandbox (-NoSandbox): commands will run without one."'
    )
    otherwise = text.index("} else {\n    # Named now, as the user who ran this", skip)
    assert skip < said < otherwise
    # Both ways into the elevated script, already elevated or through the prompt, are past the skip.
    starts = [
        i
        for i in range(len(text))
        if text.startswith('Start-Process -FilePath "powershell.exe"', i)
    ]
    assert len(starts) == 2
    assert all(start > otherwise for start in starts)


def test_the_discovery_rule_is_inbound_mdns_for_ling_and_never_on_public_networks():
    text = elevated()
    rules = [line for line in text.splitlines() if "New-NetFirewallRule" in line]
    assert len(rules) == 1
    rule = rules[0]
    for part in (
        "-Direction Inbound",
        "-Action Allow",
        "-Protocol UDP -LocalPort 5353",
        "-Program $Program",
        "-Profile Private, Domain ",
    ):
        assert part in rule, part
    assert "Public" not in text
    assert "-Profile Any" not in text


def test_the_header_says_smart_app_control_blocks_the_unsigned_preview():
    header = script()[: script().index("param(")]
    assert "PREVIEW. Windows builds are not signed yet. Smart App Control" in header
    assert "blocks unsigned programs outright: it must be off to run Mightling" in header


def test_ling_web_is_refused_on_windows_with_the_reason():
    text = LAUNCHER_RS.read_text(encoding="utf-8")
    start = text.index('user_args[index] == "web"')
    branch = text[start : text.index("// `chat` is the messenger bridge", start)]
    assert "#[cfg(unix)]" in branch
    refusal = branch[branch.index("#[cfg(not(unix))]") :]
    assert (
        "`ling web` is not available on Windows yet: the web server reaches the app-server "
        "over a Unix socket."
    ) in refusal
    assert "std::process::exit(2);" in refusal
