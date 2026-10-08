"""Puffin became Mightling: `ling-admin`'s one-time migration of a 1.4.x node
(specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2). Every test runs in conftest's scratch HOME, with
systemctl and sudo stubbed. The suite imports the package from the checkout, where the migration
refuses by itself, so the tests of what it does opt in with MIGHTLING_LEGACY_MIGRATION=1, and the
tests of the guard leave it unset."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dreamference.cli import legacy_name_migration
from dreamference.cli.legacy_name_migration import OPT_IN_ENV, LegacyNameMigration
from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler, WINDOW_MARKER
from dreamference.node.node_advertiser import NodeAdvertiser
from dreamference.node.node_pairing import KEY_COMMENT
from dreamference.node.node_serve import NodeServe
from dreamference.node.node_service_file import NodeServiceFile, SERVICE_TYPE


def _fake_systemctl(calls, enabled=True):
    def systemctl(args):
        calls.append(list(args))
        code = 0 if (args[0] != "is-enabled" or enabled) else 1
        return subprocess.CompletedProcess(args, code, "", "")
    return classmethod(lambda cls, args: systemctl(args))


def _legacy_node(tmp_home: Path):
    units = NightShiftScheduler.unit_dir()
    units.mkdir(parents=True, exist_ok=True)
    (units / "puffin-night.timer").write_text(f"{WINDOW_MARKER}01:00-07:00\n[Timer]\nOnCalendar=*-*-* 01:00:00\n")
    (units / "puffin-night.service").write_text("[Service]\nExecStart=/x/puffin-admin night run --until 07:00\n")
    legacy_file = NodeServiceFile.service_path.parent / "puffin-node.service"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        "<service-group><service><type>_puffin-node._tcp</type><port>8000</port>"
        "<txt-record>node=abc</txt-record></service></service-group>\n")
    venv = tmp_home / ".local/share/dreamference/venv/bin"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "ling-admin").write_text("#!/bin/sh\n")
    keys = NodeServe.authorized_keys()
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text(
        "ssh-ed25519 AAAAmine me@laptop\n"
        f'command="{venv}/puffin-admin node serve-job --key k1",no-pty ssh-ed25519 AAAAsender puffin-node\n')
    config = tmp_home / ".config/dreamference/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('puffin_airgapped = "on"\n[night]\nwindow = "01:00-07:00"\n')
    desktop = tmp_home / ".local/share/dev.dreamference.puffin"
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "cookies").write_text("session")
    old_bin = tmp_home / ".local/share/dreamference/puffin/bin"
    old_bin.mkdir(parents=True, exist_ok=True)
    for name in ("puffin", "puffin-code", "codex-code-mode-host"):
        (old_bin / name).write_text(name)
    (tmp_home / ".local/share/dreamference/puffin/indexers").mkdir()
    links = tmp_home / ".local/bin"
    links.mkdir(parents=True, exist_ok=True)
    (links / "puffin").symlink_to(old_bin / "puffin")
    (links / "puffin-admin").symlink_to(venv / "puffin-admin")
    (links / "puffin-code").write_text("a real file someone else put here")
    return venv, legacy_file


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setenv(OPT_IN_ENV, "1")


def test_a_puffin_node_is_moved_to_the_new_names_and_a_second_run_does_nothing(opted_in, monkeypatch, capsys):
    tmp_home = Path(os.path.expanduser("~"))
    venv, legacy_file = _legacy_node(tmp_home)
    calls, enabled_windows, privileged = [], [], []
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl(calls))
    monkeypatch.setattr(NightShiftScheduler, "enable", classmethod(lambda cls, w: enabled_windows.append(w) or True))

    def fake_privileged(cls, command, purpose, yes=False):
        privileged.append(command)
        shutil.move(command[1], command[2])
        return True
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(fake_privileged))
    monkeypatch.setattr(NodeServe, "admin_executable", classmethod(lambda cls: str(venv / "ling-admin")))

    done = LegacyNameMigration.run()

    assert len(done) == 7, done
    from dreamference.runner.codex_branded_builder import INSTALL_DIR
    new_bin = Path(INSTALL_DIR) / "bin"
    assert (new_bin / "ling").is_file() and (new_bin / "ling-code").is_file()
    assert (new_bin / "codex-code-mode-host").is_file() and (Path(INSTALL_DIR) / "indexers").is_dir()
    assert not (tmp_home / ".local/share/dreamference/puffin").exists()
    links = tmp_home / ".local/bin"
    assert not (links / "puffin").is_symlink() and not (links / "puffin-admin").is_symlink()
    assert (links / "puffin-code").read_text() == "a real file someone else put here"
    assert ["disable", "--now", "puffin-night.timer"] in calls
    assert enabled_windows == ["01:00-07:00"], "the timer comes back in the same window"
    assert not (NightShiftScheduler.unit_dir() / "puffin-night.timer").exists()
    assert privileged == [["mv", str(legacy_file), str(NodeServiceFile.service_path)]]
    assert f"<type>{SERVICE_TYPE}</type>" in NodeServiceFile.service_path.read_text()
    keys = NodeServe.authorized_keys().read_text()
    assert f"{venv}/ling-admin node serve-job --key k1" in keys and keys.rstrip().endswith(KEY_COMMENT)
    assert "ssh-ed25519 AAAAmine me@laptop\n" in keys, "the user's own lines are untouched"
    assert "puffin" not in keys
    assert 'mightling_airgapped = "on"' in (tmp_home / ".config/dreamference/config.toml").read_text()
    assert (tmp_home / ".local/share/dev.dreamference.mightling/cookies").read_text() == "session"
    assert "Puffin is now Mightling" in capsys.readouterr().err

    assert LegacyNameMigration.run() == [], "a second run finds nothing to do"


def test_a_machine_with_nothing_old_says_nothing(opted_in, capsys):
    assert LegacyNameMigration.run() == []
    assert capsys.readouterr().err == ""


def test_a_project_file_with_old_keys_is_named_not_rewritten(opted_in, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dreamference.toml").write_text('puffin_prompt = "high-swe"\n')
    LegacyNameMigration.run()
    assert (tmp_path / "dreamference.toml").read_text() == 'puffin_prompt = "high-swe"\n'
    assert "no longer read" in capsys.readouterr().err


def test_without_a_terminal_the_advertisement_keeps_working_and_the_sudo_line_is_printed(opted_in, monkeypatch):
    tmp_home = Path(os.path.expanduser("~"))
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl([], enabled=False))
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(lambda cls, command, purpose, yes=False: False))
    LegacyNameMigration.run()
    assert f"<type>{SERVICE_TYPE}</type>" in legacy_file.read_text(), "clients see the node under the new type"
    assert not NodeServiceFile.service_path.exists()


def _untouched(tmp_home: Path, legacy_file: Path):
    old_bin = tmp_home / ".local/share/dreamference/puffin/bin"
    assert (old_bin / "puffin").is_file(), "the live install folder stays where it is"
    assert (tmp_home / ".local/bin/puffin").is_symlink() and (tmp_home / ".local/bin/puffin-admin").is_symlink()
    assert (NightShiftScheduler.unit_dir() / "puffin-night.timer").is_file()
    assert legacy_file.is_file() and "_puffin-node._tcp" in legacy_file.read_text()
    assert "puffin-node" in NodeServe.authorized_keys().read_text()
    assert "puffin_airgapped" in (tmp_home / ".config/dreamference/config.toml").read_text()
    assert (tmp_home / ".local/share/dev.dreamference.puffin/cookies").is_file()


def _no_sudo(cls, command, purpose, yes=False):
    pytest.fail("no sudo when the migration refuses")


def test_from_a_source_tree_a_puffin_node_is_left_alone_and_nothing_is_said(monkeypatch, tmp_path, capsys):
    """The 2026-10-08 incident: the package imported from a worktree, beside a live Puffin install."""
    tmp_home = Path(os.path.expanduser("~"))
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dreamference.toml").write_text('puffin_prompt = "high-swe"\n')
    calls = []
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl(calls))
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(_no_sudo))

    assert not LegacyNameMigration.release_install(), "the suite imports the package from the checkout"
    assert LegacyNameMigration.run() == []

    assert calls == [], "not even systemctl is asked"
    assert capsys.readouterr().err == ""
    _untouched(tmp_home, legacy_file)


def test_the_switch_set_to_0_keeps_even_a_release_from_migrating(monkeypatch):
    tmp_home = Path(os.path.expanduser("~"))
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(_no_sudo))
    monkeypatch.setattr(LegacyNameMigration, "release_install", classmethod(lambda cls, package_dir=None: True))
    monkeypatch.setenv(OPT_IN_ENV, "0")
    assert LegacyNameMigration.run() == []
    _untouched(tmp_home, legacy_file)


def test_an_installed_release_migrates_without_the_switch(monkeypatch):
    tmp_home = Path(os.path.expanduser("~"))
    _legacy_node(tmp_home)
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl([], enabled=False))
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(lambda cls, command, purpose, yes=False: False))
    monkeypatch.setattr(LegacyNameMigration, "release_install", classmethod(lambda cls, package_dir=None: True))
    assert LegacyNameMigration.run(), "a release with an old layout migrates"
    assert not (tmp_home / ".local/share/dreamference/puffin").exists()


def test_only_a_package_in_site_packages_with_no_source_beside_it_is_a_release(tmp_path):
    venv = tmp_path / "venv/lib/python3.12/site-packages"
    (venv / "dreamference").mkdir(parents=True)
    assert LegacyNameMigration.release_install(venv / "dreamference")
    debian = tmp_path / "usr/lib/python3/dist-packages"
    (debian / "dreamference").mkdir(parents=True)
    assert LegacyNameMigration.release_install(debian / "dreamference")

    checkout = tmp_path / "checkout"
    (checkout / "dreamference").mkdir(parents=True)
    for marker in ("setup.py", "pyproject.toml"):
        (checkout / marker).write_text("")
    (checkout / "ling-rs").mkdir()
    assert not LegacyNameMigration.release_install(checkout / "dreamference"), "a checkout or a PYTHONPATH folder"
    bare = tmp_path / "copied"
    (bare / "dreamference").mkdir(parents=True)
    assert not LegacyNameMigration.release_install(bare / "dreamference"), "a copy outside site-packages"

    for marker in legacy_name_migration.SOURCE_MARKERS:
        tree = tmp_path / f"odd-{marker}" / "site-packages"
        (tree / "dreamference").mkdir(parents=True)
        (tree / marker).mkdir()
        assert not LegacyNameMigration.release_install(tree / "dreamference"), f"{marker} beside the package"


def test_the_package_this_suite_imports_is_a_source_tree():
    """The rule, read against where this very suite imports the package from."""
    from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
    assert CodexBrandedBuilder.has_source()
    assert not LegacyNameMigration.release_install()
    assert not LegacyNameMigration.allowed()
