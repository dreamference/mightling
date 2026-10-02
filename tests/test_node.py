"""The node half of the client/server split (specs/DREAMFERENCE_PUFFIN_NODE.md §4, §5, §16).

Nothing here writes /etc, runs sudo or touches a container: conftest points the service file at a
scratch folder and refuses sudo, and the two binds are checked through the commands and the
`.env` values they would apply.
"""

import json
import os
import subprocess
import uuid

import pytest

from dreamference.chat.onyx_runner import ONYX_LOOPBACK_ENV, OnyxRunner
from dreamference.chat.searxng_sidecar import SearxngSidecar
from dreamference.chat.sidecar_network import SidecarNetwork
from dreamference.node import (
    NodeAdvertiser, NodeBrowser, NodeIdentity, NodeServiceFile, NodeSettings,
)

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"


# -- the service file ------------------------------------------------------------------------------

def test_the_service_file_is_exactly_this_text():
    text = NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1.3.0", state="ready",
                                  web_port=3000, search_port=8888, main=True)
    assert text == """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by `puffin-admin node enable`; rewritten by `puffin-admin server start|stop`. -->
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service>
    <type>_puffin-node._tcp</type>
    <port>8000</port>
    <txt-record>proto=1</txt-record>
    <txt-record>node=7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001</txt-record>
    <txt-record>version=1.3.0</txt-record>
    <txt-record>web=3000</txt-record>
    <txt-record>search=8888</txt-record>
    <txt-record>state=ready</txt-record>
    <txt-record>main=1</txt-record>
  </service>
</service-group>
"""


def test_what_is_not_shared_is_not_advertised():
    text = NodeServiceFile.render(port=8010, node_id=NODE_ID, version="1.3.0")
    records = NodeServiceFile.parse(text)
    assert records == {"port": "8010", "proto": "1", "node": NODE_ID, "version": "1.3.0", "state": "stopped"}
    # No control record exists: control between nodes goes over the SSH pairing (§15.1).
    assert "control" not in text and "web=" not in text and "search=" not in text and "main" not in text
    with pytest.raises(ValueError):
        NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1", state="busy")


def test_an_update_changes_one_record_and_keeps_the_rest():
    path = NodeServiceFile.service_path
    path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.3.0", "stopped", 3000, 8888, main=True))
    assert NodeServiceFile.update(state="loading", port=8010, main=False) is True
    assert NodeServiceFile.read() == {"port": "8010", "proto": "1", "node": NODE_ID, "version": "1.3.0",
                                      "web": "3000", "search": "8888", "state": "loading"}
    assert NodeServiceFile.update(state="ready") is True
    assert NodeServiceFile.read()["state"] == "ready" and NodeServiceFile.read()["port"] == "8010"


def test_a_node_that_was_never_enabled_is_never_advertised_by_a_side_effect():
    assert not NodeServiceFile.service_path.exists()
    assert NodeServiceFile.update(state="ready") is None
    NodeAdvertiser.on_server_starting("qwen3.8-27b-nvfp4-dflash2", 8000)
    NodeAdvertiser.on_server_ready()
    NodeAdvertiser.on_server_stopped()
    assert not NodeServiceFile.service_path.exists()
    # ...but loading a model does make the machine a node: the launcher then uses loopback.
    assert NodeIdentity.read() is not None


def test_server_start_and_stop_move_the_advertised_state(capsys):
    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.0.0"))
    NodeAdvertiser.on_server_starting("qwen3.8-27b-nvfp4-dflash2", 8001)
    records = NodeServiceFile.read()
    assert (records["state"], records["port"], records.get("main")) == ("loading", "8001", "1")
    assert records["version"] == NodeAdvertiser.version()   # an update is picked up at the next start
    NodeAdvertiser.on_server_ready()
    assert NodeServiceFile.read()["state"] == "ready"
    NodeAdvertiser.on_server_starting("tiny-a2d-coder-0.5b-diffusion", 8001)
    assert "main" not in NodeServiceFile.read()              # not a model a coding client can use
    NodeAdvertiser.on_server_stopped()
    assert NodeServiceFile.read()["state"] == "stopped"
    assert capsys.readouterr().out == ""


def test_a_file_this_user_cannot_write_is_reported_not_ignored(capsys, monkeypatch):
    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.0.0"))
    monkeypatch.setattr(NodeServiceFile, "write", classmethod(lambda cls, text: False))
    NodeAdvertiser.on_server_ready()
    assert "puffin-admin node enable` again" in capsys.readouterr().out


# -- identity and settings -------------------------------------------------------------------------

def test_the_node_id_is_written_once_and_kept():
    assert NodeIdentity.read() is None
    first = NodeIdentity.ensure()
    assert str(uuid.UUID(first)) == first
    assert NodeIdentity.ensure() == first
    assert NodeIdentity.path().read_text() == first + "\n"
    NodeIdentity.path().write_text("not an id\n")
    assert NodeIdentity.read() is None


def test_settings_default_to_loopback_and_live_outside_the_working_directory(tmp_path, monkeypatch):
    assert NodeSettings.load() == {"advertise": False, "web": False}
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == ("127.0.0.1", "127.0.0.1")
    NodeSettings.save(advertise=True)
    monkeypatch.chdir(tmp_path)          # a different folder, a different dreamference.toml: same answer
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == ("0.0.0.0", "0.0.0.0")
    NodeSettings.save(advertise=True, web=False)
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == ("0.0.0.0", "127.0.0.1")
    NodeSettings.save(advertise=False, web=True)
    assert NodeSettings.load() == {"advertise": False, "web": False}
    NodeSettings.path().write_text("{broken")
    assert NodeSettings.advertised() is False


# -- the two binds ---------------------------------------------------------------------------------

def test_the_web_ui_leaves_loopback_only_on_an_advertised_node_and_port_80_never_does():
    assert OnyxRunner.web_bind_env() == ONYX_LOOPBACK_ENV
    NodeSettings.save(advertise=True)
    assert OnyxRunner.web_bind_env() == {"HOST_PORT_80": "127.0.0.1:80", "HOST_PORT": "0.0.0.0:3000"}
    NodeSettings.save(advertise=True, web=False)
    assert OnyxRunner.web_bind_env() == ONYX_LOOPBACK_ENV


def test_configure_keeps_the_advertised_bind_and_recreates_nginx_only_on_a_change(monkeypatch):
    recreated = []
    monkeypatch.setattr(OnyxRunner, "_recreate_service",
                        classmethod(lambda cls, service, wait_healthy: recreated.append(service) or True))
    runner = OnyxRunner()
    assert runner.bind_to_loopback() is True
    NodeSettings.save(advertise=True)
    assert runner.bind_to_loopback() is True
    assert runner.bind_to_loopback() is True          # already as wanted: no second recreate
    from dreamference.chat import onyx_runner
    env = open(onyx_runner.ONYX_ENV_FILE).read()
    assert 'HOST_PORT="0.0.0.0:3000"' in env and 'HOST_PORT_80="127.0.0.1:80"' in env
    NodeSettings.save(advertise=False)
    assert runner.bind_to_loopback() is True
    assert 'HOST_PORT="127.0.0.1:3000"' in open(onyx_runner.ONYX_ENV_FILE).read()
    assert recreated.count("nginx") in (2, 3)         # 3 if the scratch .env did not start on loopback


def test_searxng_is_published_where_the_settings_say():
    assert "127.0.0.1:8888:8080" in SearxngSidecar.run_command()
    NodeSettings.save(advertise=True, web=False)      # --no-web does not hide web search
    assert "0.0.0.0:8888:8080" in SearxngSidecar.run_command()


def test_searxng_is_recreated_when_its_address_no_longer_matches(monkeypatch):
    commands = []

    def run(argv, **_):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(SidecarNetwork, "network_mode", classmethod(lambda cls, name: "dreamference-sidecars"))
    monkeypatch.setattr(SidecarNetwork, "created_on_default_bridge", classmethod(lambda cls, name: False))
    monkeypatch.setattr(SidecarNetwork, "ensure", classmethod(lambda cls: True))
    monkeypatch.setattr(SearxngSidecar, "extra_networks", classmethod(lambda cls: ["onyx_default"]))
    monkeypatch.setattr(SearxngSidecar, "published_address", classmethod(lambda cls: "127.0.0.1"))

    assert SearxngSidecar.start() is True              # on loopback, wanted on loopback: only started
    assert [c[:2] for c in commands] == [["docker", "start"]]

    commands.clear()
    NodeSettings.save(advertise=True)
    assert SearxngSidecar.start() is True
    verbs = [c[1] for c in commands]
    assert verbs == ["rm", "run", "network"]
    assert "0.0.0.0:8888:8080" in commands[1]
    assert commands[2][:4] == ["docker", "network", "connect", "onyx_default"]


# -- enable, disable, status -----------------------------------------------------------------------

@pytest.fixture
def machine(monkeypatch):
    """A node with the web UI and SearXNG installed, neither touched for real."""
    applied = []
    monkeypatch.setattr(NodeAdvertiser, "is_gb10", classmethod(lambda cls: True))
    monkeypatch.setattr(NodeAdvertiser, "model_answers", classmethod(lambda cls, port: True))
    monkeypatch.setattr(NodeAdvertiser, "search_port", classmethod(lambda cls: 8888))
    monkeypatch.setattr(NodeAdvertiser, "searxng_published_address",
                        classmethod(lambda cls: NodeSettings.search_bind_address()))
    monkeypatch.setattr(OnyxRunner, "_recreate_service",
                        classmethod(lambda cls, service, wait_healthy: applied.append(service) or True))
    monkeypatch.setattr(SidecarNetwork, "network_mode", classmethod(lambda cls, name: "dreamference-sidecars"))
    monkeypatch.setattr(SearxngSidecar, "start", classmethod(lambda cls: applied.append("searxng") or True))
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    return applied


def test_enable_without_root_changes_the_binds_and_prints_the_file(machine, capsys):
    assert NodeAdvertiser.enable() is False           # sudo cannot prompt here: nothing is installed
    out = capsys.readouterr().out
    assert "<type>_puffin-node._tcp</type>" in out and "save the following as" in out
    assert "one account" in out and "Gmail" in out    # what sharing the web UI means, said at enable
    assert NodeSettings.load() == {"advertise": True, "web": True}
    assert "searxng" in machine and "nginx" in machine
    assert not NodeServiceFile.service_path.exists()
    assert NodeIdentity.read() is not None


def test_enable_installs_through_sudo_once_and_rewrites_without_it_afterwards(machine, monkeypatch, capsys):
    asked = []

    def privileged(cls, command, purpose):
        asked.append(command)
        if command[0] == "install":
            NodeServiceFile.service_path.write_text(open(command[-2]).read())
        else:
            NodeServiceFile.service_path.unlink()
        return True

    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(privileged))
    assert NodeAdvertiser.enable() is True
    assert asked[0][:3] == ["install", "-m", "644"] and asked[0][-1] == str(NodeServiceFile.service_path)
    records = NodeServiceFile.read()
    assert records["node"] == NodeIdentity.read()
    assert (records["web"], records["search"], records["state"], records["main"]) == ("3000", "8888", "ready", "1")

    assert NodeAdvertiser.enable(no_web=True) is True  # the file is this user's now: no sudo
    assert len(asked) == 1
    assert "web" not in NodeServiceFile.read() and NodeServiceFile.read()["search"] == "8888"
    assert "one account" not in capsys.readouterr().out.split("advertised as")[-1]

    assert NodeAdvertiser.disable() is True
    assert asked[-1] == ["rm", "-f", str(NodeServiceFile.service_path)]
    assert not NodeServiceFile.service_path.exists()
    assert NodeSettings.load() == {"advertise": False, "web": False}
    assert OnyxRunner.web_bind_env() == ONYX_LOOPBACK_ENV


def test_disable_without_root_still_stops_the_advertisement(machine, capsys):
    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.0.0", "ready"))
    NodeSettings.save(advertise=True)
    assert NodeAdvertiser.disable() is True
    assert NodeServiceFile.service_path.read_text() == ""
    assert NodeServiceFile.read() is None
    assert "emptied" in capsys.readouterr().out


def test_status_says_what_clients_see(machine, monkeypatch):
    text = NodeAdvertiser.status()
    assert "Advertised: no" in text and "none at" in text and "no node" in text
    node_id = NodeIdentity.ensure()
    NodeSettings.save(advertise=True)
    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, node_id, "1.3.0", "ready", 3000, 8888))
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [
        {"name": "gx10-1a2b", "address": "192.168.50.105", "port": "8000", "node": node_id,
         "state": "ready", "version": "1.3.0"}]))
    text = NodeAdvertiser.status()
    assert "Advertised: yes" in text and "state=ready" in text
    assert "gx10-1a2b at 192.168.50.105:8000 state=ready version=1.3.0 (this node)" in text
    assert "SearXNG published on: 0.0.0.0" in text


def test_the_browse_output_is_read_and_docker_interfaces_are_left_out():
    output = "\n".join([
        '+;wlP9s9;IPv4;gx10-1a2b;_puffin-node._tcp;local',
        '=;br-8544a6cf391a;IPv4;gx10-1a2b;_puffin-node._tcp;local;gx10-1a2b.local;172.20.0.1;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
        '=;vethb68d363;IPv6;gx10-1a2b;_puffin-node._tcp;local;gx10-1a2b.local;fe80::1;8000;"state=ready" "node=abc" "proto=1"',
        '=;wlP9s9;IPv4;gx10-1a2b;_puffin-node._tcp;local;gx10-1a2b.local;192.168.50.105;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
        '=;wlP9s9;IPv4;gx10-1a2b;_puffin-node._tcp;local;gx10-1a2b.local;192.168.50.105;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
        '=;wlP9s9;IPv4;other;_ssh._tcp;local;other.local;192.168.50.9;22;""',
    ])
    assert NodeBrowser.parse(output) == [{
        "interface": "wlP9s9", "name": "gx10-1a2b", "host": "gx10-1a2b.local", "address": "192.168.50.105",
        "port": "8000", "main": "1", "state": "ready", "version": "1.3.0", "node": "abc", "proto": "1"}]


# -- the command line ------------------------------------------------------------------------------

def test_the_node_commands_reach_the_advertiser(machine, monkeypatch, capsys):
    from dreamference.cli import main
    calls = []
    monkeypatch.setattr(NodeAdvertiser, "enable", classmethod(lambda cls, no_web=False: calls.append(("enable", no_web)) or True))
    monkeypatch.setattr(NodeAdvertiser, "disable", classmethod(lambda cls: calls.append(("disable",)) or True))
    for argv, code in ((["node", "enable", "--no-web"], 0), (["node", "disable"], 0), (["node", "status"], 0), (["node"], 2)):
        monkeypatch.setattr("sys.argv", ["puffin-admin", *argv])
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == code
    assert calls == [("enable", True), ("disable",)]
    assert "Node id:" in capsys.readouterr().out


def test_puffin_node_is_not_an_open_session_to_night_shift():
    from dreamference.night_shift import NightShiftHost
    assert NightShiftHost.is_interactive(["node", "list"]) is False
