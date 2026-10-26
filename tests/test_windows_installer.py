"""The Windows desktop app's release path (1.7.0): `windows.yml` builds an NSIS installer per
architecture, the release workflow publishes whatever its artifacts hold and names the installers in
its notes, and install.ps1 installs it silently beside the command-line client (the owner's
decision of 2026-10-26: the one-line installers are the supported way to install), recording it so
that `ling update` updates it too. No release ever carried the earlier zip.

The workflow files and the NSIS script are read as text: nothing here runs makensis unless one is
named in `MIGHTLING_TEST_MAKENSIS` (with `NSISDIR` for a bundle such as electron-builder's).
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_YML = ROOT / ".github" / "workflows" / "windows.yml"
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
INSTALLER_NSI = ROOT / "desktop" / "electron" / "windows" / "installer.nsi"
INSTALL_PS1 = ROOT / "install.ps1"
UPDATE_RS = ROOT / "ling-rs" / "src" / "update.rs"


def installer_name(version: str, arch: str) -> str:
    """The name windows.yml gives the installer: `Mightling-Setup-$env:MIGHTLING_VERSION-<arch>.exe`."""
    text = WINDOWS_YML.read_text()
    assert '"Mightling-Setup-$env:MIGHTLING_VERSION-${{ matrix.arch }}.exe"' in text
    return f"Mightling-Setup-{version}-{arch}.exe"


def numeric(version: str) -> str:
    """What windows.yml hands makensis as NUMERIC_VERSION: `($env:MIGHTLING_VERSION -split '[-+]')[0]`."""
    assert "$numeric = ($env:MIGHTLING_VERSION -split '[-+]')[0]" in WINDOWS_YML.read_text()
    return re.split(r"[-+]", version)[0]


@pytest.mark.parametrize("version", ["1.7.0", "1.7.0-rc.1", "2.0.0+build.5"])
def test_the_release_notes_name_the_installers_the_desktop_job_builds(version):
    release = RELEASE_YML.read_text()
    match = re.search(r"grep -q -- '(Mightling-Setup-[^']*)'", release)
    assert match, "release.yml looks for the installers by name"
    for arch in ("arm64", "x64"):
        name = installer_name(version, arch)
        assert re.search(match.group(1), name), name
    # The notes spell the same two names, and say what the installer does.
    assert "Mightling-Setup-${VERSION}-arm64.exe" in release
    assert "Mightling-Setup-${VERSION}-x64.exe" in release
    assert "Mightling-win32-" not in release


def test_the_installers_are_the_desktop_artifacts_the_release_collects():
    windows = WINDOWS_YML.read_text()
    assert "name: desktop-windows-${{ matrix.arch }}" in windows
    assert "path: dist-desktop/Mightling-Setup-*.exe" in windows
    # The release downloads every artifact of the run, the called Windows workflow's included,
    # and checksums every file it carries in the signed SHA256SUMS.
    release = RELEASE_YML.read_text()
    assert "uses: ./.github/workflows/windows.yml" in release
    assert "merge-multiple: true" in release
    assert 'mapfile -t files < <(find artifacts -type f | sort)' in release


@pytest.mark.parametrize("version, expected", [("1.7.0", "1.7.0"), ("1.7.0-rc.1", "1.7.0"), ("1.7.1+ci", "1.7.1")])
def test_a_pre_release_version_still_gives_windows_a_four_number_file_version(version, expected):
    assert numeric(version) == expected
    nsi = INSTALLER_NSI.read_text()
    # VIProductVersion takes X.X.X.X only: "1.7.0-rc.1.0" stopped makensis.
    assert 'VIProductVersion "${NUMERIC_VERSION}.0"' in nsi
    assert re.fullmatch(r"\d+\.\d+\.\d+\.0", f"{expected}.0")
    # The full version is still what the Apps list shows and CI checks.
    assert 'WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"' in nsi
    assert '"/DVERSION=$env:MIGHTLING_VERSION" "/DNUMERIC_VERSION=$numeric"' in WINDOWS_YML.read_text()


def test_ci_checks_the_entry_the_installer_writes():
    nsi = INSTALLER_NSI.read_text()
    key = re.search(r'!define UNINSTALL_KEY "([^"]+)"', nsi).group(1)
    windows = WINDOWS_YML.read_text()
    assert f'$key = "HKCU:\\{key}"' in windows
    assert re.search(r'InstallDir "\$LOCALAPPDATA\\Programs\\Mightling\\App"', nsi)
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Mightling\\App"' in windows
    assert 'WriteUninstaller "$INSTDIR\\Uninstall Mightling.exe"' in nsi
    assert '"Uninstall Mightling.exe"' in windows


@pytest.mark.parametrize("version", ["1.7.0", "1.7.2", "1.8.0-rc.1"])
def test_install_ps1_finds_the_installer_windows_yml_names_for_its_own_machine(version):
    text = INSTALL_PS1.read_text()
    pattern = re.search(r'\$_ -match "(\^Mightling-Setup-[^"]+)"', text).group(1)
    assert '$AppArch = if ($Arch -eq "aarch64") { "arm64" } else { "x64" }' in text
    for arch, other in (("arm64", "x64"), ("x64", "arm64")):
        own = pattern.replace("$AppArch", arch)
        assert re.fullmatch(own, installer_name(version, arch), re.IGNORECASE)
        assert not re.fullmatch(own, installer_name(version, other), re.IGNORECASE)
    assert "Mightling-win32" not in text


def test_install_ps1_checks_the_installer_then_runs_it_silently_with_no_mark_of_the_web():
    text = INSTALL_PS1.read_text()
    check = text.index('if ($Actual -ne $AllSums[$SetupName])')
    unblock = text.index("Unblock-File -Path $AppSetup")
    run = text.index('Start-Process -FilePath $AppSetup -ArgumentList "/S" -PassThru -Wait')
    assert check < unblock < run
    # The installer runs after the client's files are in place and before the download folder goes.
    placed = text.index("Move-Item -Force -Path (Join-Path $Work")
    cleaned = text.index("} finally {\n    Remove-Item -Recurse -Force -Path $Work")
    assert placed < run < cleaned
    assert "[switch]$NoApp" in text and "if (-not $NoApp)" in text
    # The same switch, run silently, as windows.yml's own install checks it.
    assert 'Start-Process -FilePath $setup.FullName -ArgumentList "/S" -PassThru -Wait' in WINDOWS_YML.read_text()


def test_ling_update_updates_the_app_install_ps1_recorded():
    # The record install.ps1 writes beside its bin folder is the one update.rs reads, by name and
    # by its two keys, and update.rs looks for the installer by the names windows.yml gives it.
    update = UPDATE_RS.read_text()
    install = INSTALL_PS1.read_text()
    assert 'pub const APP_RECORD: &str = "desktop-app";' in update
    assert '$Record = Join-Path (Split-Path -Parent $InstallDir) "desktop-app"' in install
    assert '@("path=$AppDir", "version=$Tag")' in install
    assert 'name.starts_with("Mightling-Setup-") && name.ends_with(&format!("-{arch}.exe"))' in update
    assert installer_name("1.7.2", "x64").startswith("Mightling-Setup-")
    # Run the same way: silently, after the same SHA256SUMS check.
    assert '.arg("/S")' in update
    assert "Mightling-win32" not in update


@pytest.mark.skipif(not os.environ.get("MIGHTLING_TEST_MAKENSIS"), reason="no makensis named in MIGHTLING_TEST_MAKENSIS")
@pytest.mark.parametrize("version", ["1.7.0", "1.7.0-rc.1"])
def test_makensis_builds_the_installer(tmp_path, version):
    app = tmp_path / "out" / "Mightling-win32-x64"
    (app / "resources").mkdir(parents=True)
    (app / "Mightling.exe").write_bytes(b"MZ")
    resources = tmp_path / "resources"
    resources.mkdir()
    # A one-image .ico: the header, one directory entry and a 1x1 BMP-less PNG would do; NSIS reads the header only.
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c10000000049454e44ae426082")
    entry = bytes([1, 1, 0, 0, 1, 0, 32, 0]) + len(png).to_bytes(4, "little") + (22).to_bytes(4, "little")
    (resources / "icon.ico").write_bytes(bytes([0, 0, 1, 0, 1, 0]) + entry + png)
    out = tmp_path / "Mightling-Setup.exe"
    result = subprocess.run(
        [os.environ["MIGHTLING_TEST_MAKENSIS"], "-V2", f"-DVERSION={version}", f"-DNUMERIC_VERSION={numeric(version)}",
         "-DARCH=x64", f"-DSOURCE={app}", f"-DOUTFILE={out}", str(INSTALLER_NSI)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.stat().st_size > 0
