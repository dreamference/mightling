"""`ForgeService`: finding the record, starting the container, a finished setup, and the tick.

systemd, docker, the network browse and `exec` are stand-ins; the state file lives in the test's
own HOME.
"""

import subprocess
from types import SimpleNamespace

import pytest
from conftest import REAL_FORGE_SETUP

from dreamference.forge import forge_service
from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_bind import ForgeBind
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_service import (
    TIMERS,
    UNIT,
    ForgeService,
)
from dreamference.forge.forge_settings import HTTP_PORT, ForgeSettings
from dreamference.node.node_browser import NodeBrowser
from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_service_file import NodeServiceFile


def test_systemctl_that_cannot_run_reads_as_a_failure_with_the_reason(monkeypatch):
    def broken(*_, **__):
        raise subprocess.TimeoutExpired(["systemctl"], 120)

    monkeypatch.setattr(forge_service.subprocess, "run", broken)
    result = ForgeService.systemctl("restart", UNIT)
    assert result.returncode == 1 and "timed out" in result.stderr


def test_the_record_found_is_another_nodes_forge_of_record(monkeypatch):
    entries = [
        {"role": "record", "node": "n-mine", "name": "here"},
        {"role": "mirror", "node": "n-2", "name": "a mirror"},
        {"role": "record", "name": "no node id"},
        {"role": "record", "node": "n-3", "name": "the record"},
    ]
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: "n-mine"))
    monkeypatch.setattr(NodeBrowser, "browse_service", classmethod(lambda cls, kind: entries))
    assert ForgeService.find_record() == entries[3]

    monkeypatch.setattr(NodeBrowser, "browse_service", classmethod(lambda cls, kind: entries[:3]))
    assert ForgeService.find_record() is None


def test_a_forge_with_no_record_node_resolves_no_address_and_browses_nothing(monkeypatch):
    def never(*_, **__):
        pytest.fail("browsed")

    monkeypatch.setattr(NodeBrowser, "browse_service", classmethod(never))
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(never))
    ForgeSettings.update_state(role="mirror")
    assert ForgeService.record_address() is None


# -- run


@pytest.fixture
def container(monkeypatch):
    state = {"network": True, "image": True, "exec": []}
    monkeypatch.setattr(ForgeContainer, "ensure_network", classmethod(lambda cls: state["network"]))
    monkeypatch.setattr(ForgeContainer, "ensure_image", classmethod(lambda cls: state["image"]))
    monkeypatch.setattr(
        ForgeService, "run_arguments", classmethod(lambda cls: ["docker", "run", "forge"])
    )
    monkeypatch.setattr(
        forge_service.os, "execvp", lambda file, argv: state["exec"].append((file, argv))
    )
    return state


@pytest.mark.parametrize("missing", ["network", "image"])
def test_the_unit_does_not_start_a_container_docker_cannot_prepare(container, capsys, missing):
    container[missing] = False
    assert ForgeService.run() == 1
    assert container["exec"] == []
    assert "could not prepare the forge's network or image" in capsys.readouterr().err


def test_the_unit_becomes_the_container(container):
    ForgeService.run()
    assert container["exec"] == [("docker", ["docker", "run", "forge"])]


# -- a setup that finishes


@pytest.fixture
def finished_setup(monkeypatch):
    """The real setup with every step succeeding; records what it started and advertised."""
    seen = {"systemctl": [], "advertised": [], "hooks": [], "phone": 0, "shown": []}
    monkeypatch.setattr(ForgeService, "setup", classmethod(REAL_FORGE_SETUP.__func__))
    monkeypatch.setattr(ForgeSettings, "enabled", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeContainer, "ensure_image", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeContainer, "ensure_network", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeContainer, "wait_until_answering", classmethod(lambda cls, s=0: True))
    monkeypatch.setattr(ForgeService, "install_units", classmethod(lambda cls: None))
    monkeypatch.setattr(ForgeService, "host_name", classmethod(lambda cls: "node-a"))
    monkeypatch.setattr(ForgeBind, "advertised_addresses", classmethod(lambda cls: []))
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(
            lambda cls, *a: seen["systemctl"].append(a) or SimpleNamespace(returncode=0, stderr="")
        ),
    )
    monkeypatch.setattr(
        ForgeService, "advertise", classmethod(lambda cls, role: seen["advertised"].append(role))
    )
    monkeypatch.setattr(
        ForgeRepositories,
        "install_hooks",
        classmethod(lambda cls, mirror_trigger: seen["hooks"].append(mirror_trigger) or 0),
    )
    monkeypatch.setattr(
        ForgeService, "ensure_phone", classmethod(lambda cls: seen.__setitem__("phone", 1))
    )
    monkeypatch.setattr(ForgeAccounts, "ensure", classmethod(lambda cls: (True, "pw-new")))
    monkeypatch.setattr(ForgeAccounts, "owner_is_admin", classmethod(lambda cls: False))
    monkeypatch.setattr(
        ForgeAccounts, "show_password_once", classmethod(lambda cls, p: seen["shown"].append(p))
    )
    return seen


def test_a_finished_record_setup_installs_the_hooks_with_the_trigger_and_advertises(
    finished_setup, capsys
):
    ForgeSettings.update_state(role="record")
    assert ForgeService.setup() is True
    assert finished_setup["hooks"] == [True] and finished_setup["phone"] == 1
    assert ("start", *TIMERS) in finished_setup["systemctl"]
    assert finished_setup["advertised"] == ["record"]
    assert finished_setup["shown"] == ["pw-new"]
    assert "loopback only" in capsys.readouterr().out


def test_a_finished_mirror_setup_installs_no_hooks_and_shows_no_password_when_asked_not_to(
    finished_setup, monkeypatch, capsys
):
    monkeypatch.setattr(ForgeAccounts, "owner_is_admin", classmethod(lambda cls: True))
    ForgeSettings.update_state(role="mirror", record_name="the record")
    assert ForgeService.setup(show_password=False) is True
    assert finished_setup["hooks"] == [] and finished_setup["phone"] == 0
    assert finished_setup["advertised"] == ["mirror"]
    assert finished_setup["shown"] == []
    out = capsys.readouterr().out
    assert "owner account is an administrator" in out and "mirror of the record" in out


@pytest.mark.parametrize(("role", "forge"), [("record", ("record", HTTP_PORT)), (None, None)])
def test_the_advertisement_names_the_role_and_port_or_drops_the_forge(monkeypatch, role, forge):
    updates = []
    monkeypatch.setattr(NodeServiceFile, "update", classmethod(lambda cls, **k: updates.append(k)))
    ForgeService.advertise(role)
    assert updates == [{"forge": forge}]


def test_housekeeping_reports_the_pruned_storage_and_the_old_data_removed(monkeypatch, capsys):
    from dreamference.forge.forge_move import ForgeMove
    from dreamference.forge.forge_storage import ForgeStorage

    monkeypatch.setattr(ForgeStorage, "prune", classmethod(lambda cls: ["🧹 pruned a dump"]))
    monkeypatch.setattr(ForgeMove, "prune_old_data", classmethod(lambda cls: ["data-old"]))
    ForgeService.housekeeping()
    out = capsys.readouterr().out
    assert "pruned a dump" in out and "Removed data-old" in out
