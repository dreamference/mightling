"""install.ps1's elevated step: the mDNS rules, the audit's user, a session with no desktop.

specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md section 16.4, bugs W6, W7 and W10 of section 23.4:
one mDNS firewall rule per program path, named after the path, removed only when its program is
in this install's folders; the egress audit run with the user's CODEX_HOME, home folder and
variables rather than the administrator's; a session with no desktop said as such, and a sandbox
set up by an earlier install recognised.

No PowerShell runs here: the script is read as text, as tests/test_windows_installer.py reads it,
and windows.yml runs it under Windows PowerShell 5.1 as an administrator.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_PS1 = ROOT / "install.ps1"
WINDOWS_YML = ROOT / ".github" / "workflows" / "windows.yml"
INSTALLER_NSI = ROOT / "desktop" / "electron" / "windows" / "installer.nsi"
CODEX_SETUP_RS = ROOT / "codex" / "codex-rs" / "windows-sandbox-rs" / "src" / "setup.rs"

# One path with a non-ASCII letter (a profile such as C:\Users\Émile) and one plain, with the names
# the scheme gives them. ling-rs/src/uninstall.rs's tests carry the same pairs.
VECTORS = [
    (
        "C:\\Users\\Émile Test\\AppData\\Local\\Programs\\Mightling\\bin\\ling.exe",
        "Mightling-mDNS-ed821dee",
    ),
    (
        "C:\\Users\\Jane\\AppData\\Local\\Programs\\Mightling\\App\\Mightling.exe",
        "Mightling-mDNS-557757bc",
    ),
]


def rule_name(program: str) -> str:
    """The scheme of section 16.4: the first 8 hex digits of the SHA-256 of the lowercased path."""
    return "Mightling-mDNS-" + hashlib.sha256(program.lower().encode("utf-8")).hexdigest()[:8]


def script() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def elevated() -> str:
    """The script install.ps1 writes and runs elevated: the here-string between @' and '@."""
    text = script()
    return text[text.index("@'\n") + 3 : text.index("\n'@")]


@pytest.mark.parametrize(("program", "name"), VECTORS)
def test_the_vectors_follow_the_scheme(program, name):
    assert rule_name(program) == name
    assert rule_name(program.upper()) == name


def test_install_ps1_stays_ascii():
    """Windows PowerShell 5.1 reads a file without a byte-order mark in the system's code page."""
    script().encode("ascii")


def test_the_elevated_script_names_each_rule_by_the_scheme():
    text = elevated()
    assert "[Security.Cryptography.SHA256]::Create()" in text
    assert "[Text.Encoding]::UTF8.GetBytes($Program.ToLowerInvariant())" in text
    assert (
        '"Mightling-mDNS-" + (($Hash[0..3] | ForEach-Object { $_.ToString("x2") }) -join "")'
        in text
    )
    assert "New-NetFirewallRule -Name (RuleName $Program)" in text


def test_no_rule_is_removed_or_made_by_a_fixed_name():
    """1.7.1 removed and recreated Mightling-mDNS-ling by name, taking another user's rule away."""
    text = elevated()
    assert '"Mightling-mDNS-$Name"' not in text
    assert "-Name $Rule" not in text
    assert '"ling-app"' not in text and "ling-app.exe" not in script()
    # Removal is by program, in this install's folders only; a failure is reported, not swallowed.
    assert "$Program.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase)" in text
    assert "$_ | Remove-NetFirewallRule -ErrorAction Stop" in text
    assert "@($InstallDir, $AppDir)" in text


def test_the_apps_programs_get_rules_when_the_app_is_installed():
    text = script()
    key = re.search(r'!define UNINSTALL_KEY "([^"]+)"', INSTALLER_NSI.read_text()).group(1)
    assert f'"HKCU:\\{key}"' in text
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Mightling\\App"' in text
    assert 'if (Test-Path (Join-Path $AppDir "Mightling.exe")) {' in text
    assert '$Programs += Join-Path $AppDir "Mightling.exe"' in text
    assert '$Programs += Join-Path $AppDir "resources\\ling.exe"' in text
    # In the same run as the app's install: this script installs it before the elevated step.
    assert text.index('Start-Process -FilePath $AppSetup -ArgumentList "/S"') < text.index(
        "$Programs = @($Mightling)"
    )
    # The app's installer runs without administrator rights, so it adds no rule itself.
    assert "RequestExecutionLevel user" in INSTALLER_NSI.read_text()
    assert "firewall" not in INSTALLER_NSI.read_text().lower()


def test_the_audit_runs_with_the_users_home_and_variables():
    text = elevated()
    audit = text.index("& $Mightling audit egress")
    for line in (
        "$env:CODEX_HOME = $CodexHome",
        "if ($UserHome) { $env:HOME = $UserHome }",
        '@("DREAMFERENCE_VLLM_HOST", $VllmHost)',
        '@("MIGHTLING_NODE", $Node)',
        '@("DREAMFERENCE_CONFIG_PATH", $ConfigPath)',
    ):
        assert 0 <= text.index(line) < audit, line
    # The values are the user's, read before the elevation.
    outer = script()
    assert "$UserHome = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }" in outer
    assert (
        "VllmHost = $env:DREAMFERENCE_VLLM_HOST; Node = $env:MIGHTLING_NODE; ConfigPath = $env:DREAMFERENCE_CONFIG_PATH"
        in outer
    )


def test_every_value_handed_to_the_elevated_script_is_a_parameter_of_it():
    outer = script()
    handed = re.search(r"\$Pass = \[ordered\]@\{(.*?)\n    \}", outer, re.S).group(1)
    keys = re.findall(r"(\w+) = ", handed)
    params = re.findall(r"\[string\]\$(\w+)", elevated().split("\n$ErrorActionPreference")[0])
    assert sorted(keys) == sorted(params)
    # An empty value is left out rather than passed as "" (5.1 can drop it), and a trailing
    # backslash cannot escape the closing quote.
    assert 'if ($Value) { $Arguments += " -$Key `"$Value`"" }' in outer
    assert 'if ($Value.EndsWith("\\")) { $Value += "\\" }' in outer


def test_a_session_without_a_desktop_is_said_as_such_and_only_for_the_prompt():
    """Over SSH (session 0) the prompt cannot appear; an administrator there needs none (row 4)."""
    text = script()
    admin = text.index(
        "if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {"
    )
    no_desktop = text.index("} elseif (-not [Environment]::UserInteractive) {")
    prompt = text.index("-Verb RunAs")
    assert admin < no_desktop < prompt
    assert "UserInteractive" not in text[:admin]
    assert "The sandbox setup needs a desktop session." in text
    # The command an administrator runs for this user: Codex's setup only runs elevated.
    assert 'sandbox setup --elevated --user `"$User`" --codex-home `"$CodexHome`"' in text


def test_a_sandbox_set_up_earlier_is_not_reported_missing():
    text = script()
    found = text.index("} elseif (Test-SandboxSetUp $CodexHome) {")
    assert (
        text.index('if ($Lines -contains "sandbox-setup-exit=0") {')
        < found
        < text.index("Windows sandbox: NOT set up.")
    )
    assert '".sandbox\\setup_marker.json", ".sandbox-secrets\\sandbox_users.json"' in text


def test_the_setup_version_is_codexs():
    """install.ps1 reads the setup marker as Codex does.

    A Codex bump that moves the version or the files must move install.ps1 too.
    """
    if not CODEX_SETUP_RS.is_file():
        pytest.skip("the codex submodule is not checked out")
    codex = CODEX_SETUP_RS.read_text()
    version = re.search(r"pub const SETUP_VERSION: u32 = (\d+);", codex).group(1)
    assert f"$SandboxSetupVersion = {version}\n" in script()
    assert 'codex_home.join(".sandbox")' in codex and 'codex_home.join(".sandbox-secrets")' in codex
    assert 'join("setup_marker.json")' in codex and 'join("sandbox_users.json")' in codex


@pytest.mark.parametrize(("program", "name"), VECTORS)
def test_ci_computes_the_same_names(program, name):
    """windows.yml checks the rule install.ps1 made against a name Python computes on the runner."""
    line = next(
        line for line in WINDOWS_YML.read_text().splitlines() if "$expected = python -c" in line
    )
    code = re.search(r'python -c "(.*)" \$program', line).group(1)
    result = subprocess.run(
        [sys.executable, "-c", code, program], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == name
