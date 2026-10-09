"""The sidecars are created on a user-defined network, never Docker's default bridge.

After the reboot of 2026-10-01 SearXNG and the speech-to-text server, both created on the default
bridge, held an empty copy of the host's DNS servers and could resolve nothing. Every docker
command here goes to a fake that records it; nothing touches the real daemon.
"""

import subprocess
from types import SimpleNamespace

import pytest

from dreamference.chat import searxng_sidecar, sidecar_network
from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME, SearxngSidecar
from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork


class FakeDocker:
    """Stands in for `subprocess.run`: answers inspections from `modes`/`networks`, records all."""

    def __init__(self, modes=None, networks=None, existing_networks=()):
        self.modes = dict(modes or {})          # container -> HostConfig.NetworkMode
        self.networks = dict(networks or {})    # container -> attached network names
        self.existing_networks = set(existing_networks)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")
        fail = SimpleNamespace(returncode=1, stdout="", stderr="no such object")
        if argv[:3] == ["docker", "network", "inspect"]:
            return ok(argv[3]) if argv[3] in self.existing_networks else fail
        if argv[:3] == ["docker", "network", "create"]:
            self.existing_networks.add(argv[3])
            return ok()
        if argv[:2] == ["docker", "inspect"]:
            name = argv[2]
            if name not in self.modes:
                return fail
            if "NetworkMode" in argv[-1]:
                return ok(self.modes[name] + "\n")
            return ok(" ".join(self.networks.get(name, [])) + " \n")
        if argv[:3] == ["docker", "rm", "-f"]:
            self.modes.pop(argv[3], None)
            return ok()
        if argv[:2] == ["docker", "ps"]:
            return ok("\n".join(self.modes) + "\n")
        return ok()

    def commands(self, *prefix):
        return [call for call in self.calls if call[:len(prefix)] == list(prefix)]


@pytest.fixture
def docker(monkeypatch):
    def install(**state):
        fake = FakeDocker(**state)
        for module in (sidecar_network, searxng_sidecar):
            monkeypatch.setattr(module.subprocess, "run", fake)
        return fake
    return install


def test_the_network_is_created_once(docker):
    fake = docker()
    assert SidecarNetwork.ensure() and SidecarNetwork.ensure()
    assert fake.commands("docker", "network", "create") == [["docker", "network", "create", SIDECAR_NETWORK]]


def test_only_a_container_made_without_a_network_counts_as_on_the_default_bridge(docker):
    docker(modes={"a": "bridge", "b": "default", "c": "other_net", "d": SIDECAR_NETWORK})
    assert SidecarNetwork.created_on_default_bridge("a")
    assert SidecarNetwork.created_on_default_bridge("b")
    assert not SidecarNetwork.created_on_default_bridge("c")
    assert not SidecarNetwork.created_on_default_bridge("d")
    assert not SidecarNetwork.created_on_default_bridge("absent")


def test_searxng_is_created_on_the_sidecar_network_and_published_on_loopback_only(docker):
    fake = docker()
    assert SearxngSidecar.start()
    (run,) = fake.commands("docker", "run")
    assert run[run.index("--network") + 1] == SIDECAR_NETWORK
    assert run[run.index("-p") + 1] == "127.0.0.1:8888:8080"
    assert "--dns" not in run
    # The network exists before the container that names it.
    assert fake.calls.index(["docker", "network", "create", SIDECAR_NETWORK]) < fake.calls.index(run)
    assert fake.commands("docker", "rm") == []


def test_a_searxng_on_the_default_bridge_is_replaced_and_rejoins_its_other_networks(docker):
    fake = docker(modes={SEARXNG_CONTAINER_NAME: "bridge"},
                  networks={SEARXNG_CONTAINER_NAME: ["bridge", "other_net"]})
    assert SearxngSidecar.start()
    order = [call[:3] for call in fake.calls if call[1] in ("rm", "run") or call[1:3] == ["network", "connect"]]
    assert order == [["docker", "rm", "-f"], ["docker", "run", "-d"], ["docker", "network", "connect"]]
    assert fake.commands("docker", "network", "connect") == [
        ["docker", "network", "connect", "other_net", SEARXNG_CONTAINER_NAME]]


def test_a_searxng_already_on_a_user_defined_network_is_left_alone(docker):
    fake = docker(modes={SEARXNG_CONTAINER_NAME: SIDECAR_NETWORK})
    assert SearxngSidecar.start()
    assert fake.commands("docker", "rm") == [] and fake.commands("docker", "run") == []
    assert fake.commands("docker", "start") == [["docker", "start", SEARXNG_CONTAINER_NAME]]
