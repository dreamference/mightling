"""Puffin (1.4.x) became Ragon: `ragon-admin`'s one-time migration of a 1.4.x node
(specs/DREAMFERENCE_RENAME_RAGON.md §4.2). Every test runs in conftest's scratch HOME, with
systemctl and sudo stubbed. The suite imports the package from the checkout, where the migration
refuses by itself, so the tests of what it does opt in with RAGON_LEGACY_MIGRATION=1, and the
tests of the guard leave it unset."""

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


PAIRED_NODE = "11111111-2222-4333-8444-555555555555"


def _fake_systemctl(calls, enabled=True):
    def systemctl(args):
        calls.append(list(args))
        code = 0 if (args[0] != "is-enabled" or enabled) else 1
        return subprocess.CompletedProcess(args, code, "", "")

    return classmethod(lambda cls, args: systemctl(args))


def _legacy_node(tmp_home: Path):
    units = NightShiftScheduler.unit_dir()
    units.mkdir(parents=True, exist_ok=True)
    (units / "puffin-night.timer").write_text(
        f"{WINDOW_MARKER}01:00-07:00\n[Timer]\nOnCalendar=*-*-* 01:00:00\n"
    )
    (units / "puffin-night.service").write_text(
        "[Service]\nExecStart=/x/puffin-admin night run --until 07:00\n"
    )
    legacy_file = NodeServiceFile.service_path.parent / "puffin-node.service"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        "<service-group><service><type>_puffin-node._tcp</type><port>8000</port>"
        "<txt-record>node=abc</txt-record></service></service-group>\n"
    )
    venv = tmp_home / ".local/share/dreamference/venv/bin"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "ragon-admin").write_text("#!/bin/sh\n")
    keys = NodeServe.authorized_keys()
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text(
        "ssh-ed25519 AAAAmine me@laptop\n"
        f'command="{venv}/puffin-admin node serve-job --key k1",no-pty ssh-ed25519 AAAAsender puffin-node\n'
    )
    config = tmp_home / ".config/dreamference/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('puffin_airgapped = "on"\n[night]\nwindow = "01:00-07:00"\n')
    # A node paired under Puffin: its host key is pinned under the old alias, in a hashed file,
    # as ssh-keygen leaves it.
    nodes = tmp_home / ".config/dreamference/nodes"
    nodes.mkdir(parents=True, exist_ok=True)
    (nodes / f"{PAIRED_NODE}.json").write_text(
        f'{{"node": "{PAIRED_NODE}", "name": "gx10-test", "address": "gx10-test.local", "user": "u"}}\n'
    )
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_home / "nodekey")], check=True
    )
    public = (tmp_home / "nodekey.pub").read_text().split()[:2]
    (nodes / "known_hosts").write_text(f"puffin-node-{PAIRED_NODE} {' '.join(public)}\n")
    subprocess.run(
        ["ssh-keygen", "-q", "-H", "-f", str(nodes / "known_hosts")],
        check=True,
        capture_output=True,
    )
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
    # the suite runs on machines that may have a SWE-bench run or Night Shift going
    monkeypatch.setattr(LegacyNameMigration, "busy_units", classmethod(lambda cls: []))


@pytest.fixture
def tmp_home():
    return Path.home()


def test_a_puffin_node_is_moved_to_the_new_names_and_a_second_run_does_nothing(
    opted_in, monkeypatch, capsys
):
    tmp_home = Path.home()
    venv, legacy_file = _legacy_node(tmp_home)
    calls, enabled_windows, privileged = [], [], []
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl(calls))
    monkeypatch.setattr(
        NightShiftScheduler, "enable", classmethod(lambda cls, w: enabled_windows.append(w) or True)
    )

    def fake_privileged(cls, command, purpose, yes=False):
        privileged.append(command)
        shutil.move(command[1], command[2])
        return True

    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(fake_privileged))
    monkeypatch.setattr(
        NodeServe, "admin_executable", classmethod(lambda cls: str(venv / "ragon-admin"))
    )

    done = LegacyNameMigration.run()

    assert len(done) == 8, done
    known_hosts = tmp_home / ".config/dreamference/nodes/known_hosts"

    def pinned(alias):
        found = subprocess.run(
            ["ssh-keygen", "-F", alias, "-f", str(known_hosts)], capture_output=True, text=True
        )
        return [line for line in found.stdout.splitlines() if not line.startswith("#")]

    assert len(pinned(f"ragon-node-{PAIRED_NODE}")) == 1, (
        "the paired node's key is found under the new alias"
    )
    assert pinned(f"puffin-node-{PAIRED_NODE}") == [], "and no longer under the old one"
    assert not (known_hosts.parent / "known_hosts.old").exists()
    from dreamference.runner.codex_branded_builder import INSTALL_DIR

    new_bin = Path(INSTALL_DIR) / "bin"
    assert (new_bin / "ragon").is_file() and (new_bin / "ragon-code").is_file()
    assert (new_bin / "codex-code-mode-host").is_file() and (
        Path(INSTALL_DIR) / "indexers"
    ).is_dir()
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
    assert f"{venv}/ragon-admin node serve-job --key k1" in keys and keys.rstrip().endswith(
        KEY_COMMENT
    )
    assert "ssh-ed25519 AAAAmine me@laptop\n" in keys, "the user's own lines are untouched"
    assert "puffin" not in keys
    assert 'ragon_airgapped = "on"' in (tmp_home / ".config/dreamference/config.toml").read_text()
    assert (tmp_home / ".local/share/dev.dreamference.ragon/cookies").read_text() == "session"
    assert "Mightling is now Ragon" in capsys.readouterr().err

    assert LegacyNameMigration.run() == [], "a second run finds nothing to do"


def test_a_machine_with_nothing_old_says_nothing(opted_in, capsys, monkeypatch, tmp_path):
    # An interpreter without a ragon-admin beside it: the suite's own environment may have one
    # (an installed renamed tree), and the test home would then get a fresh link to it.
    (tmp_path / "bin").mkdir()
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin/python"))
    assert LegacyNameMigration.run() == []
    assert capsys.readouterr().err == ""


def test_a_project_file_with_old_keys_is_named_not_rewritten(
    opted_in, monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dreamference.toml").write_text('puffin_prompt = "high-swe"\n')
    LegacyNameMigration.run()
    assert (tmp_path / "dreamference.toml").read_text() == 'puffin_prompt = "high-swe"\n'
    assert "no longer read" in capsys.readouterr().err


def test_without_a_terminal_the_advertisement_keeps_working_and_the_sudo_line_is_printed(
    opted_in, monkeypatch
):
    tmp_home = Path.home()
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl([], enabled=False))
    monkeypatch.setattr(
        NodeAdvertiser,
        "run_privileged",
        classmethod(lambda cls, command, purpose, yes=False: False),
    )
    LegacyNameMigration.run()
    assert f"<type>{SERVICE_TYPE}</type>" in legacy_file.read_text(), (
        "clients see the node under the new type"
    )
    assert not NodeServiceFile.service_path.exists()


def _untouched(tmp_home: Path, legacy_file: Path):
    old_bin = tmp_home / ".local/share/dreamference/puffin/bin"
    assert (old_bin / "puffin").is_file(), "the live install folder stays where it is"
    assert (tmp_home / ".local/bin/puffin").is_symlink() and (
        tmp_home / ".local/bin/puffin-admin"
    ).is_symlink()
    assert (NightShiftScheduler.unit_dir() / "puffin-night.timer").is_file()
    assert legacy_file.is_file() and "_puffin-node._tcp" in legacy_file.read_text()
    assert "puffin-node" in NodeServe.authorized_keys().read_text()
    assert "puffin_airgapped" in (tmp_home / ".config/dreamference/config.toml").read_text()
    assert (tmp_home / ".local/share/dev.dreamference.puffin/cookies").is_file()


def _no_sudo(cls, command, purpose, yes=False):
    pytest.fail("no sudo when the migration refuses")


def test_from_a_source_tree_a_puffin_node_is_left_alone_and_nothing_is_said(
    monkeypatch, tmp_path, capsys
):
    """The 2026-10-08 incident: the package imported from a worktree, beside a live Puffin install."""
    tmp_home = Path.home()
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dreamference.toml").write_text('puffin_prompt = "high-swe"\n')
    calls = []
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl(calls))
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(_no_sudo))

    assert not LegacyNameMigration.release_install(), (
        "the suite imports the package from the checkout"
    )
    assert LegacyNameMigration.run() == []

    assert calls == [], "not even systemctl is asked"
    assert capsys.readouterr().err == ""
    _untouched(tmp_home, legacy_file)


def test_the_switch_set_to_0_keeps_even_a_release_from_migrating(monkeypatch):
    tmp_home = Path.home()
    _venv, legacy_file = _legacy_node(tmp_home)
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(_no_sudo))
    monkeypatch.setattr(
        LegacyNameMigration, "release_install", classmethod(lambda cls, package_dir=None: True)
    )
    monkeypatch.setenv(OPT_IN_ENV, "0")
    assert LegacyNameMigration.run() == []
    _untouched(tmp_home, legacy_file)


def test_an_installed_release_migrates_without_the_switch(monkeypatch):
    tmp_home = Path.home()
    _legacy_node(tmp_home)
    monkeypatch.setattr(NightShiftScheduler, "systemctl", _fake_systemctl([], enabled=False))
    monkeypatch.setattr(
        NodeAdvertiser,
        "run_privileged",
        classmethod(lambda cls, command, purpose, yes=False: False),
    )
    monkeypatch.setattr(
        LegacyNameMigration, "release_install", classmethod(lambda cls, package_dir=None: True)
    )
    monkeypatch.setattr(LegacyNameMigration, "busy_units", classmethod(lambda cls: []))
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
    (checkout / "ragon-rs").mkdir()
    assert not LegacyNameMigration.release_install(checkout / "dreamference"), (
        "a checkout or a PYTHONPATH folder"
    )
    bare = tmp_path / "copied"
    (bare / "dreamference").mkdir(parents=True)
    assert not LegacyNameMigration.release_install(bare / "dreamference"), (
        "a copy outside site-packages"
    )

    for marker in legacy_name_migration.SOURCE_MARKERS:
        tree = tmp_path / f"odd-{marker}" / "site-packages"
        (tree / "dreamference").mkdir(parents=True)
        (tree / marker).mkdir()
        assert not LegacyNameMigration.release_install(tree / "dreamference"), (
            f"{marker} beside the package"
        )


def test_the_package_this_suite_imports_is_a_source_tree():
    """The rule, read against where this very suite imports the package from."""
    from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

    assert CodexBrandedBuilder.has_source()
    assert not LegacyNameMigration.release_install()
    assert not LegacyNameMigration.allowed()


def test_a_mightling_node_is_moved_to_ragon(opted_in, monkeypatch, tmp_home):
    """The 1.5 to 1.8 generation: install folder, links, keys, pairing line, desktop data, caches."""
    from dreamference.runner.codex_branded_builder import INSTALL_DIR

    monkeypatch.setattr(
        NightShiftScheduler,
        "systemctl",
        staticmethod(lambda args: subprocess.CompletedProcess(args, 1, "", "")),
    )
    old_bin = tmp_home / ".local/share/dreamference/mightling/bin"
    old_bin.mkdir(parents=True)
    for name in ("ling", "ling-search", "ling-docs", "codex-code-mode-host"):
        (old_bin / name).write_text(name)
    links = tmp_home / ".local/bin"
    links.mkdir(parents=True, exist_ok=True)
    (links / "ling").symlink_to(old_bin / "ling")
    (links / "ling-docs").symlink_to(old_bin / "ling-docs")
    config = tmp_home / ".config/dreamference/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('mightling_airgapped = "on"\nserver_engine = "ling"\n# mightling_x = 1\n')
    keys = NodeServe.authorized_keys()
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text(
        f'command="{tmp_home}/venv/bin/ling-admin node serve-job --key k",no-pty ssh-ed25519 AAAA mightling-node\n'
    )
    data = tmp_home / ".local/share/dev.dreamference.mightling"
    data.mkdir(parents=True)
    (data / "cookies").write_text("session")
    cache = tmp_home / ".cache/dreamference/puffin-codex"
    cache.mkdir(parents=True)

    done = LegacyNameMigration.run()

    new_bin = Path(INSTALL_DIR) / "bin"
    assert all(
        (new_bin / name).is_file()
        for name in ("ragon", "ragon-search", "ragon-docs", "codex-code-mode-host")
    )
    assert not (tmp_home / ".local/share/dreamference/mightling").exists()
    assert not (links / "ling").is_symlink() and not (links / "ling-docs").is_symlink()
    text = config.read_text()
    assert (
        'ragon_airgapped = "on"' in text
        and 'server_engine = "ragon"' in text
        and "# mightling_x" in text
    )
    assert (
        keys.read_text().rstrip().endswith(f" {KEY_COMMENT}")
        and "ling-admin" not in keys.read_text()
    )
    assert (tmp_home / ".local/share/dev.dreamference.ragon/cookies").is_file()
    assert (tmp_home / ".cache/dreamference/ragon-codex").is_dir() and not cache.exists()
    assert done
    assert LegacyNameMigration.run() == [], "a second run finds nothing to do"


def test_the_migration_waits_while_a_swe_bench_run_is_active(
    opted_in, monkeypatch, tmp_home, capsys
):
    old_bin = tmp_home / ".local/share/dreamference/mightling/bin"
    old_bin.mkdir(parents=True)
    (old_bin / "ling").write_text("x")
    monkeypatch.setattr(
        LegacyNameMigration, "busy_units", classmethod(lambda cls: ["ling-swe-night1.service"])
    )
    assert LegacyNameMigration.run() == []
    assert (old_bin / "ling").is_file()
    assert "waits for ling-swe-night1.service" in capsys.readouterr().err


def test_the_old_switch_set_to_0_still_blocks_it(monkeypatch):
    monkeypatch.delenv(OPT_IN_ENV, raising=False)
    monkeypatch.setenv("MIGHTLING_LEGACY_MIGRATION", "0")
    monkeypatch.setattr(
        LegacyNameMigration, "release_install", classmethod(lambda cls, package_dir=None: True)
    )
    assert not LegacyNameMigration.allowed()


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_the_old_engine_containers_are_removed_and_the_sidecars_are_left(monkeypatch):
    from dreamference.forge.forge_container import ForgeContainer

    calls = []

    def docker(argv, timeout=120, input_text=None):
        calls.append(list(argv))
        listing = "ling-serve\nling-serve-scan-3\ndreamference-gate\nragon-serve\n"
        return _Result(listing if argv[0] == "ps" else "")

    monkeypatch.setattr(ForgeContainer, "docker", staticmethod(docker))

    line = LegacyNameMigration.remove_old_engine_containers()

    removed = [call[-1] for call in calls if call[0] == "rm"]
    assert removed == ["ling-serve", "ling-serve-scan-3"]
    assert "ling-serve" in line and "server start" in line


def test_no_old_engine_container_means_no_line(monkeypatch):
    from dreamference.forge.forge_container import ForgeContainer

    monkeypatch.setattr(
        ForgeContainer, "docker", staticmethod(lambda argv, **kw: _Result("ragon-serve\n"))
    )
    assert LegacyNameMigration.remove_old_engine_containers() is None


def test_an_old_forge_is_moved_to_its_new_names(monkeypatch, tmp_home):
    from dreamference.forge.forge_accounts import ForgeAccounts
    from dreamference.forge.forge_container import ForgeContainer
    from dreamference.forge.forge_service import ForgeService
    from dreamference.forge.forge_settings import ForgeSettings

    units = tmp_home / ".config/systemd/user"
    units.mkdir(parents=True)
    for name in ("mightling-forge.service", "mightling-forge-tick.timer", "other.service"):
        (units / name).write_text("[Unit]\n")
    state = ForgeSettings.data_dir() / "mightling"
    state.mkdir(parents=True)
    (state / "frozen").write_text("1")
    systemctl, docker, api = [], [], []
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(lambda cls, *args: systemctl.append(args) or _Result()),
    )
    monkeypatch.setattr(ForgeService, "install_units", classmethod(lambda cls: None))
    monkeypatch.setattr(
        ForgeContainer,
        "docker",
        staticmethod(lambda argv, **kw: docker.append(list(argv)) or _Result()),
    )

    def fake_api(method, path, token, body=None, timeout=0):
        api.append((method, path, body))
        return 200, {}

    monkeypatch.setattr(ForgeContainer, "api", staticmethod(fake_api))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "token"))

    line = LegacyNameMigration.migrate_forge()

    assert line and "renamed the forge account" in line
    assert sorted(path.name for path in units.iterdir()) == ["other.service"]
    assert (ForgeSettings.data_dir() / "ragon/frozen").read_text() == "1" and not state.exists()
    assert ["rm", "-f", "mightling-forge"] in docker and [
        "network",
        "rm",
        "mightling-forge",
    ] in docker
    assert any(call[0] == "enable" for call in systemctl)
    assert ("POST", "/admin/users/mightling-phone/rename", {"new_username": "ragon-phone"}) in api
    assert LegacyNameMigration.migrate_forge() is None, "a second run finds nothing to do"


def _fake_forge(monkeypatch, answers):
    """Stubs the forge's systemctl, docker and API; `answers` are the GET statuses in turn."""
    from dreamference.forge.forge_accounts import ForgeAccounts
    from dreamference.forge.forge_container import ForgeContainer
    from dreamference.forge.forge_service import ForgeService

    events = []
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(lambda cls, *args: events.append(("systemctl", *args)) or _Result()),
    )
    monkeypatch.setattr(ForgeService, "install_units", classmethod(lambda cls: None))
    monkeypatch.setattr(
        ForgeContainer,
        "docker",
        staticmethod(lambda argv, **kw: events.append(("docker", *argv)) or _Result()),
    )
    monkeypatch.setattr(ForgeContainer, "wait_until_answering", classmethod(lambda cls, s=0: True))
    pending = list(answers)

    def fake_api(method, path, token, body=None, timeout=0):
        events.append((method, path))
        if method == "GET":
            return (pending.pop(0) if len(pending) > 1 else pending[0]), {}
        return 204, None

    monkeypatch.setattr(ForgeContainer, "api", staticmethod(fake_api))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "token"))
    monkeypatch.setattr(legacy_name_migration.time, "sleep", lambda seconds: None)
    return events


def test_an_existing_new_forge_state_is_kept_and_the_old_one_set_aside_once(monkeypatch, tmp_home):
    from dreamference.forge.forge_settings import ForgeSettings

    events = _fake_forge(monkeypatch, [200])
    data = ForgeSettings.data_dir()
    (data / "mightling").mkdir(parents=True)
    (data / "mightling/frozen").write_text("old")
    (data / "ragon").mkdir()
    (data / "ragon/frozen").write_text("new")
    (data / "mightling.pre-ragon").mkdir()

    line = LegacyNameMigration.migrate_forge()

    assert line and "kept as" in line
    assert (data / "ragon/frozen").read_text() == "new"
    assert (data / "mightling.pre-ragon.2/frozen").read_text() == "old"
    assert not (data / "mightling").exists()
    events.clear()
    assert LegacyNameMigration.migrate_forge() is None, "a second run finds nothing to do"
    assert events == [], "and touches neither docker nor systemd"


def test_the_phone_account_is_renamed_after_the_forge_answers_and_before_the_timers_start(
    monkeypatch, tmp_home
):
    from dreamference.forge.forge_service import ForgeService

    units = ForgeService.unit_dir()
    units.mkdir(parents=True)
    (units / "mightling-forge.service").write_text("[Unit]\n")
    events = _fake_forge(monkeypatch, [0, 502, 503, 200])

    line = LegacyNameMigration.migrate_forge()

    assert line and "renamed the forge account mightling-phone to ragon-phone" in line
    gets = [event for event in events if event[0] == "GET"]
    assert len(gets) == 4, "errors while Forgejo starts are asked again"
    rename = events.index(("POST", "/admin/users/mightling-phone/rename"))
    timers = next(
        i
        for i, event in enumerate(events)
        if event[:2] == ("systemctl", "enable") and "timer" in str(event)
    )
    assert rename < timers, "the tick would make a new phone account before the rename"


def test_no_old_phone_account_means_no_rename(monkeypatch, tmp_home):
    events = _fake_forge(monkeypatch, [503, 404])

    assert LegacyNameMigration.rename_phone_account() is None
    assert not [event for event in events if event[0] == "POST"]


def test_a_forge_that_never_answers_is_reported_not_skipped(monkeypatch, tmp_home):
    events = _fake_forge(monkeypatch, [503])

    line = LegacyNameMigration.rename_phone_account()

    assert line and "could not rename" in line and "503" in line
    assert not [event for event in events if event[0] == "POST"]


def test_an_old_service_file_is_rendered_again_with_both_types():
    text = (
        "<service-group><name>%h</name>"
        "<service><type>_ragon-node._tcp</type><port>8000</port>"
        "<txt-record>node=n-1</txt-record><txt-record>version=1.8</txt-record>"
        "<txt-record>state=ready</txt-record></service>"
        "<service><type>_ragon-forge._tcp</type><port>3200</port>"
        "<txt-record>role=record</txt-record></service></service-group>"
    )

    out = LegacyNameMigration.with_both_types(text)

    assert out.count("<service>") == 4
    assert "_mightling-node._tcp" in out and "_mightling-forge._tcp" in out
    assert LegacyNameMigration.with_both_types("not a service file") == "not a service file"


def test_the_sudo_line_moves_every_old_signal_bridge_and_says_how_to_start_it_again(
    monkeypatch, tmp_path
):
    assert LegacyNameMigration.privileged_line(tmp_path) is None
    for folder in (
        "mightling-signal",
        "mightling-signal-work",
        "mightling-signal-Bad",
        "postgresql",
    ):
        (tmp_path / "var/lib" / folder).mkdir(parents=True)
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "mightling-signal.service").write_text("[Unit]\n")
    (units / "mightling-signal@.service").write_text("[Unit]\n")
    (tmp_path / "opt/mightling").mkdir(parents=True)
    asked = []
    monkeypatch.setattr(
        LegacyNameMigration,
        "system_unit_enabled",
        classmethod(lambda cls, unit: asked.append(unit) or unit == "mightling-signal.service"),
    )

    line = LegacyNameMigration.privileged_line(tmp_path)

    root, _sep, after = line.partition("'; ")
    assert root.startswith("sudo sh -c 'systemctl disable --now ")
    assert "mightling-signal.service mightling-signal@work.service;" in root
    for old, new in (
        ("mightling-signal", "ragon-signal"),
        ("mightling-signal-work", "ragon-signal-work"),
    ):
        assert f"usermod -l {new} {old};" in root and f"groupmod -n {new} {old};" in root
        assert f"[ -e /var/lib/{new} ] || mv /var/lib/{old} /var/lib/{new};" in root
    assert "Bad" not in root and "postgresql" not in root
    assert "mv /opt/mightling /opt/ragon" in root
    assert "rm -f /usr/local/lib/mightling/ling-signal;" in root
    assert (
        "rm -f /etc/systemd/system/mightling-signal.service /etc/systemd/system/mightling-signal@.service"
        in root
    )
    assert root.endswith("systemctl daemon-reload")
    assert after == "ragon signal setup --refresh; ragon signal start --name default", (
        "only the bridge that was on starts again"
    )
    assert asked == ["mightling-signal.service", "mightling-signal@work.service"]


def test_old_user_units_move_with_their_text_links_and_running_state(monkeypatch, tmp_home):
    units = LegacyNameMigration.user_unit_dir()
    wants = units / "default.target.wants"
    wants.mkdir(parents=True)
    bin_dir = tmp_home / ".local/share/dreamference/mightling/bin"
    (units / "mightling-web.service").write_text(
        f"[Unit]\nDescription=Mightling web server (ling web)\n[Service]\nExecStart={bin_dir}/ling web serve --lan\n"
    )
    (units / "mightling-chat.service").write_text("[Unit]\nAfter=mightling-web.service\n")
    (units / "mightling-remote-certificate.service").write_text(
        "[Service]\nSlice=mightling-batch.slice\nExecStart=%h/.local/bin/ling-admin remote certificate renew\n"
    )
    (units / "mightling-remote-tunnel.service").write_text(
        "[Service]\nExecStart=/usr/bin/ssh -N -o UserKnownHostsFile=/k mightling-tunnel@relay.example.com\n"
    )
    (units / "mightling-night.timer").write_text("left to replace_night_units")
    (wants / "mightling-web.service").symlink_to(units / "mightling-web.service")
    calls = []

    def systemctl(cls, *args):
        calls.append(" ".join(args))
        return args != ("is-active", "--quiet", "mightling-chat.service")

    monkeypatch.setattr(LegacyNameMigration, "user_systemctl", classmethod(systemctl))
    assert LegacyNameMigration.anything_old()

    moved, to_start = LegacyNameMigration.move_user_units()
    assert not any(call.startswith("start") for call in calls), (
        "nothing starts before the programs move"
    )
    line = LegacyNameMigration.start_user_units(moved, to_start)

    new_bin = tmp_home / ".local/share/dreamference/ragon/bin"
    assert (units / "ragon-web.service").read_text() == (
        f"[Unit]\nDescription=Ragon web server (ling web)\n[Service]\nExecStart={new_bin}/ragon web serve --lan\n"
    )
    assert (units / "ragon-chat.service").read_text() == "[Unit]\nAfter=ragon-web.service\n"
    assert (units / "ragon-remote-certificate.service").read_text() == (
        "[Service]\nSlice=ragon-batch.slice\nExecStart=%h/.local/bin/ragon-admin remote certificate renew\n"
    )
    assert (
        " mightling-tunnel@relay.example.com\n"
        in (units / "ragon-remote-tunnel.service").read_text()
    )
    assert (units / "mightling-night.timer").is_file()
    assert not (units / "mightling-web.service").exists()
    assert not (wants / "mightling-web.service").is_symlink()
    assert (wants / "ragon-web.service").resolve() == units / "ragon-web.service"
    running = (
        "mightling-remote-certificate.service mightling-remote-tunnel.service mightling-web.service"
    )
    assert calls.index(f"stop {running}") < calls.index("daemon-reload")
    assert (
        calls[-1]
        == "start ragon-remote-certificate.service ragon-remote-tunnel.service ragon-web.service"
    )
    assert line.startswith("moved the user units mightling-chat.service, ") and "started" in line
    assert not LegacyNameMigration.anything_old()
    assert LegacyNameMigration.move_user_units() == ([], [])


def test_a_second_run_after_a_full_migration_with_delegate_and_app_changes_nothing(
    opted_in, monkeypatch, tmp_home, tmp_path, capsys
):
    from dreamference.runner.codex_branded_builder import INSTALL_DIR

    monkeypatch.setattr(
        NightShiftScheduler,
        "systemctl",
        staticmethod(lambda args: subprocess.CompletedProcess(args, 1, "", "")),
    )
    venv_bin = tmp_path / "venv/bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("")
    (venv_bin / "ragon-admin").write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.executable", str(venv_bin / "python"))

    old_bin = tmp_home / ".local/share/dreamference/mightling/bin"
    old_bin.mkdir(parents=True)
    for name in (
        "ling",
        "ling-search",
        "ling-fetch",
        "ling-code",
        "ling-docs",
        "ling-app",
        "ling-delegate",
        "ling-signal",
        "codex-code-mode-host",
    ):
        (old_bin / name).write_text(name)
    links = tmp_home / ".local/bin"
    links.mkdir(parents=True, exist_ok=True)
    for name in (
        "ling",
        "ling-search",
        "ling-fetch",
        "ling-code",
        "ling-docs",
        "ling-app",
        "ling-delegate",
    ):
        (links / name).symlink_to(old_bin / name)
    (links / "ling-admin").symlink_to(venv_bin / "ling-admin")

    first = LegacyNameMigration.run()
    assert first
    capsys.readouterr()

    new_bin = Path(INSTALL_DIR) / "bin"
    for name in ("ragon-delegate", "ragon-app", "ragon-signal"):
        assert (new_bin / name).is_file()
    for name in ("ragon-delegate", "ragon-app"):
        assert (links / name).resolve() == (new_bin / name).resolve()
    assert not (links / "ragon-signal").exists() and not (links / "ragon-signal").is_symlink()

    second = LegacyNameMigration.run()
    assert second == []
    assert capsys.readouterr().err == ""
    for name in ("ragon-delegate", "ragon-app"):
        assert (links / name).resolve() == (new_bin / name).resolve()
    assert not (links / "ragon-signal").exists() and not (links / "ragon-signal").is_symlink()


def test_ragon_admin_before_ragon_links_all_new_binaries_and_leaves_real_files_alone(
    opted_in, monkeypatch, tmp_home, tmp_path
):
    from dreamference.runner.codex_branded_builder import INSTALL_DIR

    monkeypatch.setattr(
        NightShiftScheduler,
        "systemctl",
        staticmethod(lambda args: subprocess.CompletedProcess(args, 1, "", "")),
    )
    no_admin_bin = tmp_path / "no-admin-venv/bin"
    no_admin_bin.mkdir(parents=True)
    (no_admin_bin / "python").write_text("")
    monkeypatch.setattr("sys.executable", str(no_admin_bin / "python"))

    old_bin = tmp_home / ".local/share/dreamference/mightling/bin"
    old_bin.mkdir(parents=True)
    for name in (
        "ling",
        "ling-search",
        "ling-fetch",
        "ling-code",
        "ling-docs",
        "ling-app",
        "ling-delegate",
        "ling-signal",
    ):
        (old_bin / name).write_text(name)
    links = tmp_home / ".local/bin"
    links.mkdir(parents=True, exist_ok=True)
    for name in (
        "ling",
        "ling-search",
        "ling-fetch",
        "ling-code",
        "ling-docs",
        "ling-app",
        "ling-delegate",
        "ling-admin",
    ):
        (links / name).symlink_to(old_bin / name)
    (links / "ragon-app").write_text("real file")

    done = LegacyNameMigration.run()
    assert done
    new_bin = Path(INSTALL_DIR) / "bin"
    for name in (
        "ragon",
        "ragon-search",
        "ragon-fetch",
        "ragon-code",
        "ragon-docs",
        "ragon-delegate",
    ):
        assert (links / name).is_symlink()
        assert (links / name).resolve() == (new_bin / name).resolve()
    assert not (links / "ragon-signal").exists() and not (links / "ragon-signal").is_symlink()
    assert not (links / "ragon-admin").exists() and not (links / "ragon-admin").is_symlink()
    assert (
        not (links / "ragon-app").is_symlink() and (links / "ragon-app").read_text() == "real file"
    )

    with_admin_bin = tmp_path / "with-admin-venv/bin"
    with_admin_bin.mkdir(parents=True)
    (with_admin_bin / "python").write_text("")
    (with_admin_bin / "ragon-admin").write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.executable", str(with_admin_bin / "python"))
    (links / "ragon-app").unlink()

    line = LegacyNameMigration.remove_old_links()
    assert line and "linked ragon-app, ragon-admin" in line and "removed" not in line
    assert (links / "ragon-app").resolve() == (new_bin / "ragon-app").resolve()
    assert (links / "ragon-admin").resolve() == (with_admin_bin / "ragon-admin").resolve()
    assert not (links / "ragon-signal").exists() and not (links / "ragon-signal").is_symlink()
    assert LegacyNameMigration.remove_old_links() is None


def test_an_active_unit_prints_the_wait_line_only_when_something_old_is_left(
    opted_in, monkeypatch, tmp_home, capsys
):
    monkeypatch.setattr(
        LegacyNameMigration, "busy_units", classmethod(lambda cls: ["ling-swe-night1.service"])
    )
    assert not LegacyNameMigration.anything_old()
    assert LegacyNameMigration.run() == []
    assert capsys.readouterr().err == ""

    old_bin = tmp_home / ".local/share/dreamference/mightling/bin"
    old_bin.mkdir(parents=True)
    (old_bin / "ling").write_text("ling")
    links = tmp_home / ".local/bin"
    links.mkdir(parents=True, exist_ok=True)
    (links / "ling").symlink_to(old_bin / "ling")

    assert LegacyNameMigration.anything_old()
    assert LegacyNameMigration.run() == []
    assert (old_bin / "ling").is_file() and (links / "ling").is_symlink()
    assert "waits for ling-swe-night1.service" in capsys.readouterr().err


def _write_units(units, files):
    units.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (units / name).write_text(text)


def test_puffin_units_move_to_ragon_names_with_a_socket_and_a_path_unit(monkeypatch, tmp_home):
    units = LegacyNameMigration.user_unit_dir()
    _write_units(
        units,
        {
            "puffin-web.service": "[Service]\nExecStart=%h/.local/bin/puffin-admin web\n",
            "puffin-web.socket": "[Socket]\nListenStream=3100\nService=puffin-web.service\n",
            "puffin-watch.path": "[Path]\nPathChanged=%h/.puffin/x\nUnit=puffin-web.service\n",
        },
    )
    monkeypatch.setattr(LegacyNameMigration, "user_systemctl", classmethod(lambda cls, *args: False))

    moved, to_start = LegacyNameMigration.move_user_units()

    assert moved == ["puffin-watch.path", "puffin-web.service", "puffin-web.socket"]
    assert to_start == [], "none was running, so none starts again"
    assert sorted(path.name for path in units.iterdir()) == [
        "ragon-watch.path",
        "ragon-web.service",
        "ragon-web.socket",
    ]
    assert "ExecStart=%h/.local/bin/ragon-admin web" in (units / "ragon-web.service").read_text()
    assert "Service=ragon-web.service" in (units / "ragon-web.socket").read_text()
    assert "Unit=ragon-web.service" in (units / "ragon-watch.path").read_text()


def test_a_unit_already_under_its_new_name_is_kept_and_a_unit_that_was_off_stays_off(
    monkeypatch, tmp_home
):
    units = LegacyNameMigration.user_unit_dir()
    wants = units / "default.target.wants"
    wants.mkdir(parents=True)
    _write_units(
        units,
        {
            "mightling-web.service": "[Service]\nExecStart=old\n",
            "ragon-web.service": "[Service]\nExecStart=new\n",
        },
    )
    (wants / "mightling-web.service").symlink_to(units / "mightling-web.service")
    (wants / "ragon-web.service").symlink_to(units / "ragon-web.service")
    (wants / "mightling-gone.service").symlink_to(units / "mightling-gone.service")
    calls = []

    def systemctl(cls, *args):
        calls.append(" ".join(args))
        return args[0] != "is-active"

    monkeypatch.setattr(LegacyNameMigration, "user_systemctl", classmethod(systemctl))

    moved, to_start = LegacyNameMigration.move_user_units()

    assert (units / "ragon-web.service").read_text() == "[Service]\nExecStart=new\n"
    assert not (units / "mightling-web.service").exists()
    assert not (wants / "mightling-web.service").is_symlink()
    assert (wants / "ragon-web.service").resolve() == units / "ragon-web.service"
    assert (wants / "mightling-gone.service").is_symlink(), "a link of a unit that is gone is not ours"
    assert moved == ["mightling-web.service"] and to_start == []
    assert not any(call.startswith(("stop", "start")) for call in calls)
    assert "started" not in LegacyNameMigration.start_user_units(moved, to_start)


@pytest.mark.parametrize(
    ("enabled", "after"),
    [
        ((), "ragon signal setup --refresh"),
        (("mightling-signal.service",), "ragon signal setup --refresh; ragon signal start"),
        (
            ("mightling-signal.service", "mightling-signal@work.service"),
            "ragon signal setup --refresh; ragon signal start --name default; ragon signal start --name work",
        ),
    ],
)
def test_the_signal_line_starts_each_enabled_bridge_and_none_that_was_off(
    monkeypatch, tmp_path, enabled, after
):
    (tmp_path / "var/lib/mightling-signal").mkdir(parents=True)
    if len(enabled) > 1:
        (tmp_path / "var/lib/mightling-signal-work").mkdir()
    monkeypatch.setattr(
        LegacyNameMigration, "system_unit_enabled", classmethod(lambda cls, unit: unit in enabled)
    )

    line = LegacyNameMigration.signal_line(tmp_path)

    assert line.endswith(after)
    assert line.count("ragon signal start") == len(enabled)


def test_the_signal_line_guards_an_existing_runtime_and_a_state_folder(monkeypatch, tmp_path):
    (tmp_path / "var/lib/mightling-signal").mkdir(parents=True)
    (tmp_path / "opt/ragon").mkdir(parents=True)
    monkeypatch.setattr(LegacyNameMigration, "system_unit_enabled", classmethod(lambda cls, unit: False))

    line = LegacyNameMigration.signal_line(tmp_path)

    assert "[ -e /opt/ragon ] || [ ! -d /opt/mightling ] || mv /opt/mightling /opt/ragon;" in line
    assert "[ -e /var/lib/ragon-signal ] || mv /var/lib/mightling-signal /var/lib/ragon-signal;" in line


def test_an_old_unit_file_alone_still_gets_a_line_without_accounts(monkeypatch, tmp_path):
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "mightling-signal.service").write_text("[Unit]\n")
    asked = []
    monkeypatch.setattr(
        LegacyNameMigration, "system_unit_enabled", classmethod(lambda cls, unit: asked.append(unit))
    )

    line = LegacyNameMigration.signal_line(tmp_path)

    assert line.startswith("sudo sh -c 'systemctl disable --now mightling-signal.service;")
    assert "usermod" not in line and "groupmod" not in line
    assert "ragon signal start" not in line and asked == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("ExecStart=%h/.local/bin/ling-admin night", "ExecStart=%h/.local/bin/ragon-admin night"),
        ("ExecStart=%h/.local/bin/puffin-admin night", "ExecStart=%h/.local/bin/ragon-admin night"),
        ("Slice=puffin-batch.slice", "Slice=ragon-batch.slice"),
        ("Slice=mightling.slice", "Slice=ragon.slice"),
        ("Environment=MIGHTLING_X=1", "Environment=RAGON_X=1"),
        ("Description=Mightling web server", "Description=Ragon web server"),
        (
            "ExecStart=/usr/bin/ssh mightling-tunnel@relay.example.com",
            "ExecStart=/usr/bin/ssh mightling-tunnel@relay.example.com",
        ),
        ("ExecStart=sh -c a=b", "ExecStart=sh -c a=b"),
        ("WorkingDirectory=%h/.mightling", "WorkingDirectory=%h/.ragon"),
        ("Environment=CODEX_HOME=%h/.mightling/x", "Environment=CODEX_HOME=%h/.ragon/x"),
    ],
)
def test_rewrite_unit_text_table(old, new):
    assert LegacyNameMigration.rewrite_unit_text(old + "\n") == new + "\n"
