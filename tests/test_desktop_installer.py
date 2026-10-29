"""Tests for the desktop toolchain checks and installs.

Every probe of the machine (which, node, rustup, unshare, sudo) is replaced; nothing here runs a
real command or reads /etc.
"""

import subprocess
from unittest.mock import MagicMock, patch

from dreamference.chat import desktop_installer
from dreamference.chat.desktop_installer import DesktopInstaller


def _which(present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _run_returning(stdout="", returncode=0):
    return MagicMock(return_value=MagicMock(stdout=stdout, returncode=returncode))


def test_node_version_is_none_when_node_is_missing():
    with patch("shutil.which", return_value=None):
        assert DesktopInstaller.node_version() is None


def test_node_version_reads_major_and_minor():
    with (
        patch("shutil.which", _which({"node"})),
        patch("subprocess.run", _run_returning("v22.13.1\n")),
    ):
        assert DesktopInstaller.node_version() == (22, 13)


def test_node_version_with_only_a_major_reads_minor_as_zero():
    with patch("shutil.which", _which({"node"})), patch("subprocess.run", _run_returning("v24\n")):
        assert DesktopInstaller.node_version() == (24, 0)


def test_node_version_that_is_not_a_number_is_none():
    with (
        patch("shutil.which", _which({"node"})),
        patch("subprocess.run", _run_returning("not node\n")),
    ):
        assert DesktopInstaller.node_version() is None


def test_node_version_is_none_when_node_cannot_be_run():
    for error in (OSError("no exec"), subprocess.TimeoutExpired("node", 30)):
        with patch("shutil.which", _which({"node"})), patch("subprocess.run", side_effect=error):
            assert DesktopInstaller.node_version() is None


def test_an_old_node_is_reported_missing():
    with (
        patch("shutil.which", _which({"npm"})),
        patch.object(DesktopInstaller, "node_version", return_value=(18, 0)),
    ):
        assert DesktopInstaller.missing_prerequisites() == ["node"]


def test_a_build_needs_dpkg_and_dpkg_deb_but_a_dev_run_does_not():
    with (
        patch("shutil.which", _which({"npm", "dpkg"})),
        patch.object(DesktopInstaller, "node_version", return_value=(24, 0)),
    ):
        assert DesktopInstaller.missing_prerequisites(build=True) == ["dpkg"]
        assert DesktopInstaller.missing_prerequisites(build=False) == []


def test_cargo_is_found_in_the_rustup_folder_before_the_path(tmp_path):
    cargo = tmp_path / "cargo"
    cargo.write_text("#!/bin/sh\n")
    cargo.chmod(0o755)
    with (
        patch.object(desktop_installer, "CARGO_BIN", str(tmp_path)),
        patch("shutil.which", return_value="/other/cargo"),
    ):
        assert DesktopInstaller.cargo_path() == str(cargo)


def test_a_non_executable_cargo_file_is_skipped_for_the_path_one(tmp_path):
    (tmp_path / "cargo").write_text("data")
    (tmp_path / "cargo").chmod(0o644)
    with (
        patch.object(desktop_installer, "CARGO_BIN", str(tmp_path)),
        patch("shutil.which", return_value="/other/cargo"),
    ):
        assert DesktopInstaller.cargo_path() == "/other/cargo"


def test_rust_is_not_installed_again_when_it_is_present():
    with (
        patch.object(DesktopInstaller, "has_rust", return_value=True),
        patch("subprocess.run") as run,
    ):
        assert DesktopInstaller.install_rust() is True
    run.assert_not_called()


def test_rust_install_needs_curl(capsys):
    with (
        patch.object(DesktopInstaller, "has_rust", return_value=False),
        patch("shutil.which", return_value=None),
    ):
        assert DesktopInstaller.install_rust() is False
    assert "curl" in capsys.readouterr().out


def test_rust_install_fetches_rustup_over_https_only():
    with (
        patch.object(DesktopInstaller, "has_rust", side_effect=[False, True]),
        patch("shutil.which", _which({"curl"})),
        patch("subprocess.run", _run_returning(returncode=0)) as run,
    ):
        assert DesktopInstaller.install_rust() is True
    command = run.call_args.args[0]
    assert "--proto '=https'" in command and "--tlsv1.2" in command
    assert desktop_installer.RUSTUP_URL.startswith("https://")


def test_a_failing_rustup_is_reported_not_trusted(capsys):
    with (
        patch.object(DesktopInstaller, "has_rust", return_value=False),
        patch("shutil.which", _which({"curl"})),
        patch("subprocess.run", _run_returning(returncode=1)),
    ):
        assert DesktopInstaller.install_rust() is False
    assert "non-zero" in capsys.readouterr().out


def test_a_rustup_that_cannot_start_is_reported(capsys):
    with (
        patch.object(DesktopInstaller, "has_rust", return_value=False),
        patch("shutil.which", _which({"curl"})),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sh", 1800)),
    ):
        assert DesktopInstaller.install_rust() is False
    assert "rustup failed" in capsys.readouterr().out


def test_user_namespaces_are_assumed_allowed_without_unshare():
    with patch("shutil.which", return_value=None):
        assert DesktopInstaller.userns_allowed() is True


def test_user_namespaces_are_asked_as_an_unconfined_program_when_aa_exec_exists():
    with (
        patch("shutil.which", _which({"unshare", "aa-exec"})),
        patch("subprocess.run", _run_returning(returncode=1)) as run,
    ):
        assert DesktopInstaller.userns_allowed() is False
    assert run.call_args.args[0][:3] == ["aa-exec", "-p", "unconfined"]


def test_user_namespaces_ask_unshare_directly_without_aa_exec():
    with (
        patch("shutil.which", _which({"unshare"})),
        patch("subprocess.run", _run_returning(returncode=0)) as run,
    ):
        assert DesktopInstaller.userns_allowed() is True
    assert run.call_args.args[0] == ["unshare", "-U", "true"]


def test_an_unshare_that_cannot_run_counts_as_allowed():
    with (
        patch("shutil.which", _which({"unshare"})),
        patch("subprocess.run", side_effect=OSError("boom")),
    ):
        assert DesktopInstaller.userns_allowed() is True


def test_the_restriction_flag_is_read_from_the_sysctl_file(tmp_path):
    flag = tmp_path / "flag"
    with patch.object(desktop_installer, "USERNS_SYSCTL", str(flag)):
        assert DesktopInstaller.userns_restricted() is False  # no such file
        flag.write_text("1\n")
        assert DesktopInstaller.userns_restricted() is True
        flag.write_text("0\n")
        assert DesktopInstaller.userns_restricted() is False


def test_a_missing_profile_folder_covers_nothing(tmp_path):
    with patch.object(desktop_installer, "APPARMOR_DIR", str(tmp_path / "absent")):
        assert DesktopInstaller.covering_profile("/opt/x/Mightling") is None


def test_a_profile_without_a_userns_rule_does_not_cover_the_binary(tmp_path):
    (tmp_path / "p").write_text('profile p "/opt/x/Mightling" flags=(unconfined) {\n}\n')
    (tmp_path / "subdir").mkdir()  # directories in the folder are skipped
    with patch.object(desktop_installer, "APPARMOR_DIR", str(tmp_path)):
        assert DesktopInstaller.covering_profile("/opt/x/Mightling") is None


def test_an_unreadable_profile_file_is_skipped(tmp_path):
    (tmp_path / "p").write_text('profile p "/opt/x/Mightling" {\n  userns,\n}\n')
    with (
        patch.object(desktop_installer, "APPARMOR_DIR", str(tmp_path)),
        patch("builtins.open", side_effect=PermissionError("denied")),
    ):
        assert DesktopInstaller.covering_profile("/opt/x/Mightling") is None


def test_the_profile_names_each_binary_in_its_own_stanza():
    text = DesktopInstaller.apparmor_profile(["/opt/a", "/opt/b"])
    assert 'profile mightling-desktop-dev "/opt/a"' in text
    assert 'profile mightling-desktop-dev-1 "/opt/b"' in text
    assert text.count("userns,") == 2


def test_the_profile_install_with_only_relative_paths_just_asks_the_kernel():
    with (
        patch.object(DesktopInstaller, "userns_allowed", return_value=False),
        patch("subprocess.run") as run,
    ):
        assert DesktopInstaller.install_apparmor_profile(["relative/electron"]) is False
    run.assert_not_called()


def test_the_profile_is_printed_instead_of_installed_when_sudo_is_missing(capsys):
    with patch("shutil.which", _which({"apparmor_parser"})), patch("subprocess.run") as run:
        assert DesktopInstaller.install_apparmor_profile(["/opt/x/Mightling"]) is False
    run.assert_not_called()
    out = capsys.readouterr().out
    assert "profile mightling-desktop-dev" in out and "write it yourself" in out


def test_the_profile_is_not_loaded_when_writing_it_failed(capsys):
    with (
        patch("shutil.which", return_value="/usr/bin/x"),
        patch("subprocess.run", _run_returning(returncode=1)) as run,
    ):
        assert DesktopInstaller.install_apparmor_profile(["/opt/x/Mightling"]) is False
    assert run.call_count == 1
    assert "not written" in capsys.readouterr().out


def test_a_failing_profile_load_is_reported_false():
    results = [MagicMock(returncode=0), MagicMock(returncode=1)]
    with (
        patch("shutil.which", return_value="/usr/bin/x"),
        patch("subprocess.run", side_effect=results),
    ):
        assert DesktopInstaller.install_apparmor_profile(["/opt/x/Mightling"]) is False


def test_a_sudo_that_cannot_run_is_reported(capsys):
    with (
        patch("shutil.which", return_value="/usr/bin/x"),
        patch("subprocess.run", side_effect=OSError("no sudo")),
    ):
        assert DesktopInstaller.install_apparmor_profile(["/opt/x/Mightling"]) is False
    assert "Could not install" in capsys.readouterr().out


def test_report_is_quiet_about_a_complete_toolchain(capsys):
    with patch.object(DesktopInstaller, "missing_prerequisites", return_value=[]):
        assert DesktopInstaller.report(build=True) == (True, [])
    assert "dpkg" in capsys.readouterr().out


def test_report_says_how_to_get_each_missing_piece(capsys):
    with patch.object(
        DesktopInstaller, "missing_prerequisites", return_value=["node", "npm", "dpkg"]
    ):
        ok, missing = DesktopInstaller.report(build=True)
    out = capsys.readouterr().out
    assert ok is False and missing == ["node", "npm", "dpkg"]
    assert (
        "Node.js 22.13" in out
        and "npm is needed" in out
        and "dpkg-deb" in out
        and "nodejs.org" in out
    )
