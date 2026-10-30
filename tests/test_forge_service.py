"""`ForgeService`: finding the record, starting the container, a finished setup, and the tick.

systemd, docker, the network browse and `exec` are stand-ins; the state file lives in the test's
own HOME.
"""

import subprocess

import pytest

from dreamference.forge import forge_service
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_service import (
    UNIT,
    ForgeService,
)
from dreamference.forge.forge_settings import ForgeSettings
from dreamference.node.node_browser import NodeBrowser
from dreamference.node.node_identity import NodeIdentity


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
