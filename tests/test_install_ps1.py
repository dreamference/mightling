"""install.ps1's elevated step: the mDNS rules, the audit's user, a session with no desktop.

specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md section 16.4, bugs W6, W7 and W10 of section 23.4:
one mDNS firewall rule per program path, named after the path, removed only when its program is
in this install's folders; the egress audit run with the user's CODEX_HOME, home folder and
variables rather than the administrator's; a session with no desktop said as such, and a sandbox
set up by an earlier install recognised.

No PowerShell runs here: the script is read as text, as tests/test_windows_installer.py reads it,
and windows.yml runs it under Windows PowerShell 5.1 as an administrator.
"""

import ast
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


@pytest.mark.parametrize(("program", "name"), VECTORS)
def test_ling_uninstall_names_the_rules_the_same_way(program, name):
    """`ling uninstall` (ling-rs/src/uninstall.rs) removes what install.ps1 made: its tests hold these pairs."""
    source = (ROOT / "ling-rs" / "src" / "uninstall.rs").read_text(encoding="utf-8")
    assert f'rule_name(r"{program}"), "{name}"' in source


def test_ling_uninstall_removes_rules_by_program_as_install_ps1_does():
    source = (ROOT / "ling-rs" / "src" / "uninstall.rs").read_text(encoding="utf-8")
    assert "$_.Name -like '{RULE_PREFIX}*'" in source
    assert "$Program.StartsWith($_, 'OrdinalIgnoreCase')" in source
    assert 'pub const RULE_PREFIX: &str = "Mightling-mDNS-";' in source


# -- the release's signature (specs/DREAMFERENCE_RELEASE_SIGNING.md) --------------------------------

RELEASE_KEYS_PS1 = re.compile(r"^\$ReleaseKeys = @\((.*)\)$", re.MULTILINE)


def signature_block() -> str:
    text = script()
    return text[text.index('if ($Assets.ContainsKey("SHA256SUMS.sig"))') :]


def test_install_ps1_trusts_the_keys_ling_update_compiles_in():
    keys = [
        line
        for line in (ROOT / "ling-rs" / "release-signing.pub").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    embedded = re.findall(r"'([^']*)'", RELEASE_KEYS_PS1.search(script()).group(1))
    assert keys and embedded == keys


def test_install_ps1_requires_signatures_from_the_same_release_as_install_sh():
    since = re.search(r'^SIGNED_SINCE="([^"]+)"$', (ROOT / "install.sh").read_text(), re.MULTILINE)
    assert f'$SignedSince = "{since.group(1)}"' in script()


def test_the_token_goes_only_to_asset_urls_under_the_api_root():
    # As in install.sh (security review 2026-10, finding 1): the release JSON names each asset's
    # URL, and a token that followed it to another host would reach whoever controls that host.
    text = script()
    download = re.search(r"^function Get-Asset\(.*?^}$", text, re.MULTILINE | re.DOTALL).group(0)
    assert (
        'if ($Token -and $Assets[$Name].StartsWith("$Api/", [StringComparison]::Ordinal)) {'
        in download
    )
    assert "-Headers $Sent " in download
    # The release itself is read from the API root; nothing else ever carries the token.
    setters = [line.strip() for line in text.splitlines() if '["Authorization"]' in line]
    assert setters == [
        'if ($Token) { $Headers["Authorization"] = "Bearer $Token" }',
        '$Sent["Authorization"] = "Bearer $Token"',
    ]
    assert "$ReleaseUrl = " in text and '"$Api/repos/' in text


def test_install_ps1_checks_sha256sums_against_the_release_key():
    block = signature_block()
    assert "-Y verify" in script()
    assert 'Test-Signature $Keygen $Signers "mightling-release" $Signature $SignedSums' in block
    assert 'Test-Signature $Keygen $Signers "mightling-release-key"' in block
    assert "failed its signature check" in block


def test_an_unsigned_release_since_signing_began_installs_nothing():
    block = signature_block()
    unsigned = block.index("} elseif (Test-SigningRequired $Tag) {")
    assert block[unsigned:].split("\n", 2)[1].strip().startswith('Fail "release $Tag is not signed')


def test_an_unsigned_latest_release_installs_nothing_whatever_its_tag():
    # As install.sh: the tag of the latest release is only a name, so an unsigned one is refused
    # before the pre-signing branch can read it; only a release named with -Version may predate it.
    block = signature_block()
    latest = block.index("} elseif (-not $Version) {")
    assert latest < block.index("} elseif (Test-SigningRequired $Tag) {")
    assert (
        'Fail "the latest release ($Tag) is not signed'
        in block[latest:].split("} elseif (Test-SigningRequired", 1)[0]
    )


def test_the_per_target_checksums_are_tied_to_the_signed_sha256sums():
    block = signature_block()
    assert "$Signed[$SumsName]" in block
    assert 'Fail "$SumsName does not match its checksum in SHA256SUMS' in block


def test_nothing_is_unpacked_or_run_before_the_signature_is_checked():
    text = script()
    checked = text.index('if ($Assets.ContainsKey("SHA256SUMS.sig"))')
    for step in (
        "Expand-Gzip $Archive",
        "Unblock-File -Path $AppSetup",
        "Start-Process -FilePath $AppSetup",
    ):
        assert checked < text.index(step), step


def test_the_desktop_app_is_checked_against_the_signed_sha256sums():
    text = script()
    app = text[text.index("$AppSetup = $null") :]
    assert "$AllSums = $Signed" in app


def ps_function(name: str) -> str:
    """The body of install.ps1's function `name`, up to its closing brace at the start of a line."""
    text = script()
    start = text.index(f"function {name}(")
    return text[start : text.index("\n}\n", start)]


def test_signing_is_required_from_the_cut_over_on_and_for_a_tag_that_is_not_a_version():
    # install.sh's signing_required: a name that is no version needs a signature, and so does
    # the cut-over release itself; only a release older than it may come unsigned.
    body = ps_function("Test-SigningRequired")
    returns = re.findall(r"(?:if \((.*)\) \{ )?return \$(true|false)", body)
    assert returns == [
        ('$Name.TrimStart("v") -notmatch "^(\\d+)\\.(\\d+)\\.(\\d+)"', "true"),
        ("$Have[$I] -gt $Since[$I]", "true"),
        ("$Have[$I] -lt $Since[$I]", "false"),
        ("", "true"),
    ]


def test_each_binary_is_unpacked_only_after_its_own_checksum_matches():
    text = script()
    loop = text[text.index("foreach ($Name in $Wanted) {") :]
    unpacked = loop.index("Expand-Gzip $Archive")
    for guard in (
        'if (-not $Sums.ContainsKey($Asset)) { Fail "$SumsName has no checksum for $Asset',
        'if ($Actual -ne $Sums[$Asset]) { Fail "$Asset does not match its checksum',
    ):
        assert loop.index(guard) < unpacked, guard


def test_the_signed_sha256sums_must_list_the_per_target_sums():
    block = signature_block()
    assert 'if (-not $Signed.ContainsKey($SumsName)) { Fail "SHA256SUMS has no checksum' in block


def test_a_new_key_is_trusted_only_when_endorsed_and_beside_the_current_keys():
    block = signature_block()
    transition = block[block.index('if ($Assets.ContainsKey("release-key-transition.pub"))') :]
    missing = transition.index('if (-not $Assets.ContainsKey("release-key-transition.pub.sig")) {')
    refused = transition[missing:].split("\n", 2)[1].strip()
    assert refused.startswith('Fail "release $Tag names a new release key')
    endorsed = transition.index('$Signers "mightling-release-key" $NewKeySig $NewKey')
    added = transition.index("$Lines += Get-Signers (Get-Content -Path $NewKey)")
    assert missing < endorsed < added
    assert "$Lines = Get-Signers (Get-Content" not in block


def test_a_signature_counts_only_when_ssh_keygen_accepts_it():
    body = ps_function("Test-Signature")
    assert re.findall(r"return .*", body) == ["return ($Process.ExitCode -eq 0)"]


def code_lines() -> list:
    """install.ps1's lines without comments, the elevated script's included."""
    lines = []
    for line in script().splitlines():
        code = line.split(" #", 1)[0].rstrip()
        if code.strip() and not code.lstrip().startswith("#"):
            lines.append(code)
    return lines


def test_install_ps1_uses_only_what_windows_powershell_5_1_has():
    """Section 6: a standard Windows 11 machine has only 5.1, and windows.yml runs it on Windows only.

    No null-coalescing (`??`), null-conditional (`?.`), ternary, pipeline chain (`&&`, `||`) or
    `ForEach-Object -Parallel`, all PowerShell 7; and every web call with `-UseBasicParsing`, without
    which 5.1 needs Internet Explorer's engine (gone on Windows 11) to parse the page.
    """
    lines = code_lines()
    for line in lines:
        assert "??" not in line and "?." not in line, line
        assert "&&" not in line and "||" not in line, line
        assert "-Parallel" not in line, line
        # `<condition> ? <a> : <b>`; `| ? { … }` (Where-Object's alias) is 5.1 as well.
        assert not re.search(r"[^|\s]\s+\?\s+\S.*\s:\s", line), line
    calls = [
        line
        for line in lines
        if re.search(r"Invoke-(WebRequest|RestMethod)\b", line, re.IGNORECASE)
    ]
    assert calls
    for call in calls:
        assert "-UseBasicParsing" in call, call


def test_every_windows_release_step_carries_the_same_sandbox_helpers():
    """Section 16.2: what the builder builds, the release packages, install.ps1 and `ling update` fetch."""
    from dreamference.runner.codex_branded_builder import WINDOWS_SANDBOX_HELPERS

    package = (ROOT / ".github" / "scripts" / "package_mightling.py").read_text()
    packaged = ast.literal_eval(re.search(r"^WINDOWS_ONLY = (\(.*\))$", package, re.M).group(1))
    update_rs = (ROOT / "ling-rs" / "src" / "update.rs").read_text()
    helpers = re.search(r"pub const WINDOWS_SANDBOX_HELPERS: [^=]+= \[(.*)\];", update_rs).group(1)
    updated = re.findall(r'"([^"]+)"', helpers)
    fetched = re.findall(
        r'"([^"]+)"', re.search(r"^\$Optional = @\((.*)\)$", script(), re.M).group(1)
    )
    assert set(WINDOWS_SANDBOX_HELPERS) <= set(fetched)
    assert tuple(packaged) == tuple(updated) == tuple(WINDOWS_SANDBOX_HELPERS)
