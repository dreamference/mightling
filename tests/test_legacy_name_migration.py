"""Puffin became Mightling: `mling-admin`'s one-time migration of a 1.4.x node
(specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2). Every test runs in conftest's scratch HOME, with
systemctl and sudo stubbed."""

import os
import shutil
import subprocess
from pathlib import Path

from dreamference.cli.legacy_name_migration import LegacyNameMigration
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
    (venv / "mling-admin").write_text("#!/bin/sh\n")
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
    return venv, legacy_file


def test_a_puffin_node_is_moved_to_the_new_names_and_a_second_run_does_nothing(monkeypatch, capsys):
    tmp_home = Path(os.path.expanduser("~"))
    venv, legacy_file = _legacy_node(tmp_home)
    calls, enabled_windows, privileged = [], [], []
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl(calls))
    monkeypatch.setattr(NightShiftScheduler, "enable", classmethod(lambda cls, w: enabled_windows.append(w) or True))

    def fake_privileged(cls, command, purpose):
        privileged.append(command)
        shutil.move(command[1], command[2])
        return True
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(fake_privileged))
    monkeypatch.setattr(NodeServe, "admin_executable", classmethod(lambda cls: str(venv / "mling-admin")))

    done = LegacyNameMigration.run()

    assert len(done) == 5, done
    assert ["disable", "--now", "puffin-night.timer"] in calls
    assert enabled_windows == ["01:00-07:00"], "the timer comes back in the same window"
    assert not (NightShiftScheduler.unit_dir() / "puffin-night.timer").exists()
    assert privileged == [["mv", str(legacy_file), str(NodeServiceFile.service_path)]]
    assert f"<type>{SERVICE_TYPE}</type>" in NodeServiceFile.service_path.read_text()
    keys = NodeServe.authorized_keys().read_text()
    assert f"{venv}/mling-admin node serve-job --key k1" in keys and keys.rstrip().endswith(KEY_COMMENT)
    assert "ssh-ed25519 AAAAmine me@laptop\n" in keys, "the user's own lines are untouched"
    assert "puffin" not in keys
    assert 'mightling_airgapped = "on"' in (tmp_home / ".config/dreamference/config.toml").read_text()
    assert (tmp_home / ".local/share/dev.dreamference.mightling/cookies").read_text() == "session"
    assert "Puffin is now Mightling" in capsys.readouterr().err

    assert LegacyNameMigration.run() == [], "a second run finds nothing to do"


def test_a_machine_with_nothing_old_says_nothing(capsys):
    assert LegacyNameMigration.run() == []
    assert capsys.readouterr().err == ""


def test_a_project_file_with_old_keys_is_named_not_rewritten(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dreamference.toml").write_text('puffin_prompt = "high-swe"\n')
    LegacyNameMigration.run()
    assert (tmp_path / "dreamference.toml").read_text() == 'puffin_prompt = "high-swe"\n'
    assert "no longer read" in capsys.readouterr().err


def test_without_a_terminal_the_advertisement_keeps_working_and_the_sudo_line_is_printed(monkeypatch):
    tmp_home = Path(os.path.expanduser("~"))
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl([], enabled=False))
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(lambda cls, command, purpose: False))
    LegacyNameMigration.run()
    assert f"<type>{SERVICE_TYPE}</type>" in legacy_file.read_text(), "clients see the node under the new type"
    assert not NodeServiceFile.service_path.exists()
