"""The node half of the client/server split (specs/DREAMFERENCE_MIGHTLING_NODE.md §4, §5, §16).

Nothing here writes /etc, runs sudo or touches a container: conftest points the service file at a
scratch folder and refuses sudo, and the two binds are checked through the commands and the
`.env` values they would apply.
"""

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from dreamference.chat.searxng_sidecar import SearxngSidecar
from dreamference.chat.sidecar_network import SidecarNetwork
from dreamference.node import (
    NodeAdvertiser,
    NodeBrowser,
    NodeIdentity,
    NodeServiceFile,
    NodeSettings,
)

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"


# -- the service file ------------------------------------------------------------------------------


def test_the_service_file_is_exactly_this_text():
    text = NodeServiceFile.render(
        port=8000,
        node_id=NODE_ID,
        version="1.3.0",
        state="ready",
        web_port=3000,
        search_port=8888,
        main=True,
    )
    assert (
        text
        == """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by `ling-admin node enable`; rewritten by `ling-admin server start|stop`. -->
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service>
    <type>_mightling-node._tcp</type>
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
    )


def test_what_is_not_shared_is_not_advertised():
    text = NodeServiceFile.render(port=8010, node_id=NODE_ID, version="1.3.0")
    records = NodeServiceFile.parse(text)
    assert records == {
        "port": "8010",
        "proto": "1",
        "node": NODE_ID,
        "version": "1.3.0",
        "state": "stopped",
    }
    # No control record exists: control between nodes goes over the SSH pairing (§15.1).
    assert (
        "control" not in text
        and "web=" not in text
        and "search=" not in text
        and "main" not in text
    )
    with pytest.raises(ValueError):
        NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1", state="busy")


def test_an_update_changes_one_record_and_keeps_the_rest():
    path = NodeServiceFile.service_path
    path.write_text(
        NodeServiceFile.render(8000, NODE_ID, "1.3.0", "stopped", 3000, 8888, main=True)
    )
    assert NodeServiceFile.update(state="loading", port=8010, main=False) is True
    assert NodeServiceFile.read() == {
        "port": "8010",
        "proto": "1",
        "node": NODE_ID,
        "version": "1.3.0",
        "web": "3000",
        "search": "8888",
        "state": "loading",
    }
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
    assert (
        records["version"] == NodeAdvertiser.version()
    )  # an update is picked up at the next start
    NodeAdvertiser.on_server_ready()
    assert NodeServiceFile.read()["state"] == "ready"
    NodeAdvertiser.on_server_starting("tiny-a2d-coder-0.5b-diffusion", 8001)
    assert "main" not in NodeServiceFile.read()  # not a model a coding client can use
    NodeAdvertiser.on_server_stopped()
    assert NodeServiceFile.read()["state"] == "stopped"
    assert capsys.readouterr().out == ""


def test_a_file_this_user_cannot_write_is_reported_not_ignored(capsys, monkeypatch):
    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.0.0"))
    monkeypatch.setattr(NodeServiceFile, "write", classmethod(lambda cls, text: False))
    NodeAdvertiser.on_server_ready()
    assert "ling-admin node enable` again" in capsys.readouterr().out


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
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == (
        "127.0.0.1",
        "127.0.0.1",
    )
    NodeSettings.save(advertise=True)
    monkeypatch.chdir(tmp_path)  # a different folder, a different dreamference.toml: same answer
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == (
        "0.0.0.0",
        "0.0.0.0",
    )
    NodeSettings.save(advertise=True, web=False)
    assert (NodeSettings.search_bind_address(), NodeSettings.web_bind_address()) == (
        "0.0.0.0",
        "127.0.0.1",
    )
    NodeSettings.save(advertise=False, web=True)
    assert NodeSettings.load() == {"advertise": False, "web": False}
    NodeSettings.path().write_text("{broken")
    assert NodeSettings.advertised() is False


# -- the two binds ---------------------------------------------------------------------------------


def test_searxng_is_published_where_the_settings_say():
    assert "127.0.0.1:8888:8080" in SearxngSidecar.run_command()
    NodeSettings.save(advertise=True, web=False)  # --no-web does not hide web search
    assert "0.0.0.0:8888:8080" in SearxngSidecar.run_command()


def test_searxng_is_recreated_when_its_address_no_longer_matches(monkeypatch):
    commands = []

    def run(argv, **_):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        SidecarNetwork, "network_mode", classmethod(lambda cls, name: "dreamference-sidecars")
    )
    monkeypatch.setattr(
        SidecarNetwork, "created_on_default_bridge", classmethod(lambda cls, name: False)
    )
    monkeypatch.setattr(SidecarNetwork, "ensure", classmethod(lambda cls: True))
    monkeypatch.setattr(SearxngSidecar, "extra_networks", classmethod(lambda cls: ["onyx_default"]))
    monkeypatch.setattr(SearxngSidecar, "published_address", classmethod(lambda cls: "127.0.0.1"))
    from dreamference.chat.searxng_settings import SearxngSettings

    SearxngSettings.ensure(SearxngSidecar.config_dir())  # current settings: no restart for them

    assert SearxngSidecar.start() is True  # on loopback, wanted on loopback: only started
    assert [c[:2] for c in commands] == [["docker", "start"]]

    commands.clear()
    NodeSettings.save(advertise=True)
    assert SearxngSidecar.start() is True
    verbs = [c[1] for c in commands]
    assert verbs == ["rm", "run", "network"]
    assert "0.0.0.0:8888:8080" in commands[1]
    assert commands[2][:4] == ["docker", "network", "connect", "onyx_default"]


def test_the_advert_follows_searxng_started_after_enable(monkeypatch):
    path = NodeServiceFile.service_path
    path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.3.0", "ready"))
    monkeypatch.setattr(NodeAdvertiser, "search_port", classmethod(lambda cls: 8888))
    monkeypatch.setattr(NodeAdvertiser, "ling_web_available", classmethod(lambda cls: True))
    NodeAdvertiser.on_searxng_started()  # not advertised: the file is not touched
    assert "search" not in NodeServiceFile.read()
    NodeSettings.save(advertise=True)
    NodeAdvertiser.on_searxng_started()
    assert NodeServiceFile.read()["search"] == "8888"


# -- enable, disable, status -----------------------------------------------------------------------


@pytest.fixture
def ling_web(tmp_path, monkeypatch):
    """A stand-in for the installed `ling`: records each `ling web` command it is given."""
    log = tmp_path / "ling-web.log"
    ling = tmp_path / "ling"
    ling.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    ling.chmod(0o755)
    monkeypatch.setattr(NodeAdvertiser, "ling_executable", classmethod(lambda cls: str(ling)))
    return lambda: log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def machine(monkeypatch, ling_web):
    """A node with `ling` and SearXNG installed, none touched for real."""
    applied = []
    monkeypatch.setattr(NodeAdvertiser, "is_gb10", classmethod(lambda cls: True))
    monkeypatch.setattr(NodeAdvertiser, "model_answers", classmethod(lambda cls, port: True))
    monkeypatch.setattr(NodeAdvertiser, "search_port", classmethod(lambda cls: 8888))
    monkeypatch.setattr(
        NodeAdvertiser,
        "searxng_published_address",
        classmethod(lambda cls: NodeSettings.search_bind_address()),
    )
    monkeypatch.setattr(
        SidecarNetwork, "network_mode", classmethod(lambda cls, name: "dreamference-sidecars")
    )
    monkeypatch.setattr(
        SearxngSidecar, "start", classmethod(lambda cls: applied.append("searxng") or True)
    )
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    return applied


def test_enable_without_root_publishes_nothing_and_prints_the_file(machine, capsys):
    assert NodeAdvertiser.enable() is False  # sudo cannot prompt here: nothing is installed
    out = capsys.readouterr().out
    assert "<type>_mightling-node._tcp</type>" in out and "save the following as" in out
    assert "Nothing was published" in out
    # A web UI on the LAN that nobody can find would be the worst of both states: no bind moved.
    assert NodeSettings.load() == {"advertise": False, "web": False}
    assert machine == []
    assert not NodeServiceFile.service_path.exists()
    assert NodeIdentity.read() is not None


def test_enable_installs_through_sudo_once_and_rewrites_without_it_afterwards(
    machine, monkeypatch, capsys, ling_web, tmp_path
):
    asked = []

    def privileged(cls, command, purpose, yes=False):
        asked.append(command)
        if command[0] == "install":
            NodeServiceFile.service_path.write_text(Path(command[-2]).open().read())
        else:
            NodeServiceFile.service_path.unlink()
        return True

    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(privileged))
    assert NodeAdvertiser.enable() is True
    out = capsys.readouterr().out
    assert "paired" in out and "Onyx" not in out  # what sharing the web UI means, said at enable
    assert "searxng" in machine
    assert asked[0][:3] == ["install", "-m", "644"] and asked[0][-1] == str(
        NodeServiceFile.service_path
    )
    records = NodeServiceFile.read()
    assert records["node"] == NodeIdentity.read()
    # The advert names `ling web`, which enable put on the network (pairing still guards it).
    assert (records["web"], records["search"], records["state"], records["main"]) == (
        "3100",
        "8888",
        "ready",
        "1",
    )
    assert ling_web() == ["web start --lan"]
    assert "ling web pair" in out

    # `ling web start` wrote its unit with --lan; a stand-in for it, since the fake ling writes none.
    unit = tmp_path / "mightling-web.service"
    unit.write_text("[Service]\nExecStart=/x/ling web serve --lan\n")
    monkeypatch.setattr("dreamference.node.node_advertiser.WEB_UNIT", str(unit))
    assert NodeAdvertiser.enable(no_web=True) is True  # the file is this user's now: no sudo
    assert len(asked) == 1
    assert ling_web()[-1] == "web start"  # back on loopback
    assert "web" not in NodeServiceFile.read() and NodeServiceFile.read()["search"] == "8888"
    assert "ling web pair" not in capsys.readouterr().out.split("advertised as")[-1]

    assert NodeAdvertiser.disable() is True
    assert asked[-1] == ["rm", "-f", str(NodeServiceFile.service_path)]
    assert not NodeServiceFile.service_path.exists()
    assert NodeSettings.load() == {"advertise": False, "web": False}


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
    NodeServiceFile.service_path.write_text(
        NodeServiceFile.render(8000, node_id, "1.3.0", "ready", 3000, 8888)
    )
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "gx10-1a2b",
                    "address": "192.168.50.105",
                    "port": "8000",
                    "node": node_id,
                    "state": "ready",
                    "version": "1.3.0",
                }
            ]
        ),
    )
    text = NodeAdvertiser.status()
    assert "Advertised: yes" in text and "state=ready" in text
    assert "Web UI (ling web, port 3100): the local network, paired devices only" in text
    assert "gx10-1a2b at 192.168.50.105:8000 state=ready version=1.3.0 (this node)" in text
    assert "SearXNG published on: 0.0.0.0" in text


def test_the_browse_output_is_read_and_docker_interfaces_are_left_out():
    output = "\n".join(
        [
            "+;wlP9s9;IPv4;gx10-1a2b;_mightling-node._tcp;local",
            '=;br-8544a6cf391a;IPv4;gx10-1a2b;_mightling-node._tcp;local;gx10-1a2b.local;172.20.0.1;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
            '=;vethb68d363;IPv6;gx10-1a2b;_mightling-node._tcp;local;gx10-1a2b.local;fe80::1;8000;"state=ready" "node=abc" "proto=1"',
            '=;wlP9s9;IPv4;gx10-1a2b;_mightling-node._tcp;local;gx10-1a2b.local;192.168.50.105;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
            '=;wlP9s9;IPv4;gx10-1a2b;_mightling-node._tcp;local;gx10-1a2b.local;192.168.50.105;8000;"main=1" "state=ready" "version=1.3.0" "node=abc" "proto=1"',
            '=;wlP9s9;IPv4;other;_ssh._tcp;local;other.local;192.168.50.9;22;""',
        ]
    )
    assert NodeBrowser.parse(output) == [
        {
            "interface": "wlP9s9",
            "name": "gx10-1a2b",
            "host": "gx10-1a2b.local",
            "address": "192.168.50.105",
            "port": "8000",
            "main": "1",
            "state": "ready",
            "version": "1.3.0",
            "node": "abc",
            "proto": "1",
        }
    ]


def test_a_node_is_kept_once_at_ipv4_and_a_link_local_address_carries_its_interface():
    # 2026-10-08: `node add` took the node's link-local IPv6 address from the browse, with no
    # scope, and ssh answered "Invalid argument"; `node list` showed http://[fe80::…]:8000/v1.
    v6 = '=;enP7s7;IPv6;spark-2;_mightling-node._tcp;local;spark-2.local;fe80::4ab0:2dff:fe01:203;8000;"node=n2" "proto=1"'
    v4 = '=;enP7s7;IPv4;spark-2;_mightling-node._tcp;local;spark-2.local;192.168.1.20;8000;"node=n2" "proto=1"'
    for output in ("\n".join([v6, v4]), "\n".join([v4, v6])):
        (node,) = NodeBrowser.parse(output)
        assert node["address"] == "192.168.1.20"
    # IPv6 only: the address is usable as it stands, by ssh and by Python's sockets and URLs.
    (node,) = NodeBrowser.parse(v6)
    assert node["address"] == "fe80::4ab0:2dff:fe01:203%enP7s7"
    import urllib.parse

    from dreamference.node.node_lanes import NodeLanes

    url = urllib.parse.urlsplit(f"http://{NodeLanes.url_host(node['address'])}:8000/v1")
    assert url.hostname == "fe80::4ab0:2dff:fe01:203%enP7s7" and url.port == 8000
    # A global IPv6 address beats a link-local one, and needs no scope.
    global_v6 = v6.replace("fe80::4ab0:2dff:fe01:203", "2001:db8::20")
    (node,) = NodeBrowser.parse("\n".join([v6, global_v6]))
    assert node["address"] == "2001:db8::20"


def test_rsync_brackets_an_ipv6_host(tmp_path, monkeypatch):
    from dreamference.node.fleet_session import FleetSession

    session = FleetSession("fe80::1%enP7s7", "owner", tmp_path)
    ran = []
    monkeypatch.setattr(
        session,
        "_spawn",
        lambda command, capture=False: (
            ran.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    monkeypatch.setattr(session, "_log", lambda command, result: None)
    session.rsync(tmp_path, "~/bundle")
    assert ran[0][-1] == "owner@[fe80::1%enP7s7]:~/bundle/"


# -- the locator crate ------------------------------------------------------------------------------


def test_the_web_crates_locator_is_a_byte_identical_copy():
    # ling-web-rs is built on its own, outside the Codex workspace, so it holds a copy; a
    # launcher and a `ling-search` that read node.json differently would talk to two machines.
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    leaf = repo / "ling-rs" / "node-locator" / "src" / "lib.rs"
    assert (repo / "ling-web-rs" / "src" / "node_locator.rs").read_bytes() == leaf.read_bytes()
    # ling-app finds no node itself: the `ling` it bundles does, through the crate.
    # ...and it agrees with the node about the service type and the contract's version.
    from dreamference.node import PROTO, SERVICE_TYPE

    text = leaf.read_text()
    assert f'pub const SERVICE_TYPE: &str = "{SERVICE_TYPE}.local.";' in text
    assert f"pub const PROTO: u32 = {PROTO};" in text
    assert str(NodeIdentity.path()).endswith(".config/dreamference/node-id")
    assert 'join(".config").join("dreamference").join("node-id")' in text


# -- the command line ------------------------------------------------------------------------------


def test_the_node_commands_reach_the_advertiser(machine, monkeypatch, capsys):
    from dreamference.cli import main

    calls = []
    monkeypatch.setattr(
        NodeAdvertiser,
        "enable",
        classmethod(lambda cls, no_web=False, yes=False: calls.append(("enable", no_web)) or True),
    )
    monkeypatch.setattr(
        NodeAdvertiser, "disable", classmethod(lambda cls: calls.append(("disable",)) or True)
    )
    for argv, code in (
        (["node", "enable", "--no-web"], 0),
        (["node", "disable"], 0),
        (["node", "status"], 0),
        (["node"], 2),
    ):
        monkeypatch.setattr("sys.argv", ["ling-admin", *argv])
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == code
    assert calls == [("enable", True), ("disable",)]
    assert "Node id:" in capsys.readouterr().out


def test_mightling_node_is_not_an_open_session_to_night_shift():
    from dreamference.night_shift import NightShiftHost

    assert NightShiftHost.is_interactive(["node", "list"]) is False


# -- callers that run `ling` name the model server, so the launcher never browses for a node -----


def test_a_night_task_names_its_model_server_to_mightling(tmp_path, monkeypatch):
    import time

    from dreamference.night_shift import NightShiftSettings, NightShiftTaskRun

    monkeypatch.setattr(NightShiftTaskRun, "USE_SCOPE", False)
    monkeypatch.delenv("DREAMFERENCE_VLLM_HOST", raising=False)
    task = {"id": "20261002-0100-abc", "repo": str(tmp_path), "base": "0" * 40, "task": "x"}
    output = tmp_path / "out.txt"
    run = NightShiftTaskRun(
        tmp_path / "night",
        task,
        NightShiftSettings({}),
        "ling",
        deadline=time.time() + 30,
        model_host="http://localhost:8000",
    )
    assert (
        run._run_capped(
            ["bash", "-c", "echo host=$DREAMFERENCE_VLLM_HOST"], tmp_path, output, timeout=20
        )
        == 0
    )
    assert output.read_text().strip() == "host=http://localhost:8000"


def test_the_other_unattended_callers_name_it_too():
    # The egress audit counts a DNS query as a failure, and a browse is one on the wire; a
    # SWE-bench container reaches nothing but the model server. Both must stay on tier 1.
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for path in (
        "dreamference/audit/egress_audit.py",
        "dreamference/swe_bench/swe_bench_instance_run.py",
        "dreamference/runner/codex_runner.py",
        "dreamference/runner/codex_test_runner.py",
    ):
        assert "DREAMFERENCE_VLLM_HOST" in (repo / path).read_text(), path


def test_a_start_that_fails_does_not_leave_the_node_saying_loading(monkeypatch):
    # The pre-flight exits, docker fails, or the user presses Ctrl-C: none of them passes the
    # "server is ready" branch, and a client that reads `loading` waits ten minutes.
    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.vllm_server import VLLMServerManager

    class Monitor:
        server_ready = False

        def start(self):
            pass

        def stop(self):
            pass

    def refused(self, **_):
        assert NodeServiceFile.read()["state"] == "loading"
        raise SystemExit(1)

    NodeServiceFile.service_path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.0.0", "ready"))
    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: Monitor())
    monkeypatch.setattr(VLLMServerManager, "start_server", refused)
    from dreamference.vllm_server import DiffusionServerManager

    monkeypatch.setattr(
        DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None)
    )
    monkeypatch.setattr("sys.argv", ["ling-admin", "server", "start", "--no-diffusion"])
    with pytest.raises(SystemExit):
        controller.main()
    assert NodeServiceFile.read()["state"] == "stopped"


# -- Part 2: pairing, and managing a node from another (§12.4, §13.2, §15.1) ------------------------

PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBodyForTestsOnly0000000000000000000 owner@laptop"
)


def test_the_authorised_line_forces_one_command_and_forbids_the_rest():
    from dreamference.node import NodePairing

    line = NodePairing.authorized_line(
        PUBLIC_KEY, "/home/u/.local/bin/ling-admin node serve-job --key abc"
    )
    assert line.startswith('command="/home/u/.local/bin/ling-admin node serve-job --key abc",')
    for restriction in (
        "restrict",
        "no-pty",
        "no-port-forwarding",
        "no-agent-forwarding",
        "no-X11-forwarding",
        "no-user-rc",
    ):
        assert restriction in line.split(" ssh-ed25519 ")[0]
    assert line.endswith(" mightling-node")  # the sender's own comment is not kept
    assert "owner@laptop" not in line
    for bad in ("", "not a key", PUBLIC_KEY + "\nssh-ed25519 AAAA second", "rm -rf /"):
        with pytest.raises(ValueError):
            NodePairing.authorized_line(bad, "/x node serve-job")
    with pytest.raises(ValueError):
        NodePairing.authorized_line(PUBLIC_KEY, 'x" ,no-such')


def test_authorising_adds_one_restricted_line_and_unpairing_removes_only_it(capsys):
    from dreamference.node import NodeServe

    path = NodeServe.authorized_keys()
    path.parent.mkdir(parents=True)
    path.write_text("ssh-rsa AAAAB3own the-users-own-key\n")
    assert NodeServe.authorize(PUBLIC_KEY) is True
    assert NodeServe.authorize(PUBLIC_KEY) is True  # pairing twice does not add twice
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and lines[0] == "ssh-rsa AAAAB3own the-users-own-key"
    tag = NodeServe.key_tag(PUBLIC_KEY)
    assert f'node serve-job --key {tag}"' in lines[1] and "no-pty" in lines[1]
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert NodeIdentity.read() is not None  # a machine that accepts pairing is a node
    assert NodeServe.authorize("garbage") is False
    assert NodeServe.unauthorize("0" * 16) is False
    assert NodeServe.unauthorize("not-a-tag") is False
    assert NodeServe.unauthorize(tag) is True
    assert path.read_text() == "ssh-rsa AAAAB3own the-users-own-key\n"


def test_serve_job_refuses_everything_that_is_not_an_operation(monkeypatch, capsys):
    from dreamference.node import NodeServe

    ran = []
    monkeypatch.setattr(
        NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0)
    )
    for request in (
        None,
        "",
        "bash",
        "bash -i",
        "python train.py",
        "git push",
        "scp -t /etc",
        "status; rm -rf ~",
        "status extra",
        "set-model",
        "set-model a b",
        "start now",
        "info --all",
        "'unterminated",
    ):
        assert NodeServe.serve(request) == 2, request
    assert ran == []
    assert "may only ask for Mightling node operations" in capsys.readouterr().err


def test_serve_job_runs_this_nodes_own_mightling_admin(monkeypatch, capsys):
    from dreamference.node import NodeServe

    ran = []
    monkeypatch.setattr(
        NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0)
    )
    monkeypatch.setattr(NodeServe, "linger", classmethod(lambda cls: False))
    assert (
        NodeServe.serve("status") == 0
        and NodeServe.serve("start") == 0
        and NodeServe.serve("stop") == 0
    )
    assert ran == [["status"], ["server", "start"], ["server", "stop"]]
    node_id = NodeIdentity.ensure()
    capsys.readouterr()
    assert NodeServe.serve("info") == 0
    info = json.loads(capsys.readouterr().out)
    assert info["node"] == node_id and info["linger"] is False and info["version"]


def test_only_a_key_of_the_nodes_own_matrix_is_loaded(monkeypatch, capsys):
    from dreamference.node import NodeServe

    ran = []
    monkeypatch.setattr(
        NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0)
    )
    for bad in (
        "RadixArk/Qwen3.8-27B-NVFP4",
        "../etc/passwd",
        "--model",
        "no-such-model",
        "QWEN",
        "a b",
    ):
        assert NodeServe.serve(f"set-model {bad}") == 2, bad
    assert NodeServe.serve("set-model tiny-a2d-coder-0.5b-diffusion") == 2  # not a main model
    assert ran == []
    assert NodeServe.serve("set-model qwen3.8-27b-nvfp4-dflash2") == 0
    # The node's own commands, in order: its config keeps the assignment, then its own start,
    # with its own host-safety checks.
    assert ran == [
        ["main-model", "set", "qwen3.8-27b-nvfp4-dflash2"],
        ["server", "stop"],
        ["server", "start"],
    ]
    ran.clear()
    monkeypatch.setattr(
        NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 3)
    )
    assert (
        NodeServe.serve("set-model qwen3.8-27b-nvfp4-dflash2") == 3
    )  # a refusal comes back as it is
    assert len(ran) == 1
    # A node with no server running: the stop fails, and the start still happens.
    ran.clear()
    monkeypatch.setattr(
        NodeServe,
        "run_admin",
        classmethod(
            lambda cls, arguments: (
                ran.append(arguments) or (1 if arguments == ["server", "stop"] else 0)
            )
        ),
    )
    assert NodeServe.serve("set-model qwen3.8-27b-nvfp4-dflash2") == 0 and ran[-1] == [
        "server",
        "start",
    ]


def paired_record(node_id="2222-bbbb", address="192.168.50.106"):
    from dreamference.node import NodePairing

    record = {
        "node": node_id,
        "name": "spark-2",
        "address": address,
        "user": "owner",
        "ssh_port": 22,
    }
    NodePairing._save(record)
    return record


def test_every_connection_uses_the_pairing_key_and_the_pinned_host_key(monkeypatch):
    from dreamference.node import NodePairing

    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.106",
                    "port": "8000",
                }
            ]
        ),
    )
    record = paired_record()
    command = NodePairing.ssh_command(record, "status")
    assert command[0] == "ssh" and command[-2:] == ["owner@192.168.50.106", "status"]
    options = " ".join(command)
    assert f"-i {NodePairing.key_path()}" in options and "IdentitiesOnly=yes" in options
    assert "BatchMode=yes" in options  # never a password prompt mid-command
    assert (
        "StrictHostKeyChecking=yes" in options
        and "HostKeyAlias=mightling-node-2222-bbbb" in options
    )
    assert f"UserKnownHostsFile={NodePairing.known_hosts()}" in options
    assert "accept-new" in " ".join(NodePairing.ssh_options(record, accept_new=True))
    for name in ("spark-2", "SPARK-2", "192.168.50.106", "2222-bbbb", "2222"):
        assert NodePairing.find(name)["node"] == "2222-bbbb", name
    assert NodePairing.find("spark-9") is None and NodePairing.find("222") is None


def test_a_paired_node_is_followed_to_its_new_address(monkeypatch):
    from dreamference.node import NodePairing

    paired_record()
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.200",
                    "port": "8000",
                }
            ]
        ),
    )
    assert NodePairing.find("spark-2")["address"] == "192.168.50.200"
    assert NodePairing.paired()[0]["address"] == "192.168.50.200"


def test_managing_a_node_needs_the_pairing_and_sends_one_operation(monkeypatch, capsys):
    from dreamference.node import NodePairing, NodeRemote

    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert NodeRemote.status("spark-2") == 1
    assert "ling-admin node add spark-2" in capsys.readouterr().out
    paired_record()
    sent = []

    def run(cls, record, request, capture=True, input_text=None):
        sent.append((record["name"], request, capture))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    assert NodeRemote.set_model("spark-2", "qwen3.8-27b-nvfp4-dflash2") == 0
    assert (
        NodeRemote.stop("spark-2") == 0
        and NodeRemote.start("spark-2") == 0
        and NodeRemote.status("spark-2") == 0
    )
    assert sent == [
        ("spark-2", "set-model qwen3.8-27b-nvfp4-dflash2", False),
        ("spark-2", "stop", False),
        ("spark-2", "start", False),
        ("spark-2", "status", False),
    ]


def test_pairing_checks_that_the_machine_that_answered_is_the_advertised_node(monkeypatch, capsys):
    from dreamference.node import NodePairing

    advert = {"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.106", "port": "8000"}
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [advert]))
    sent = []
    monkeypatch.setattr(
        NodePairing,
        "authorize_on_node",
        classmethod(lambda cls, record, public_key: sent.append(public_key) or True),
    )
    answers = {"info": json.dumps({"node": "2222-bbbb", "linger": False})}
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: subprocess.CompletedProcess(
                [], 0, answers[request], ""
            )
        ),
    )
    assert NodePairing.add("spark-2", user="owner") is True
    assert sent[0].startswith("ssh-ed25519 ") and NodePairing.key_path().is_file()
    assert oct(NodePairing.key_path().stat().st_mode & 0o777) == "0o600"
    assert NodePairing.paired()[0]["node"] == "2222-bbbb"
    assert "loginctl enable-linger" in capsys.readouterr().out  # a job would die with its sender
    # Another machine answering at that address: refused, and nothing is left paired.
    answers["info"] = json.dumps({"node": "9999-other"})
    assert NodePairing.add("spark-2", user="owner") is False
    assert NodePairing.paired() == []
    # Not on the network: pairing by address is tried instead (FLEET §7.6), over one login.
    tried = []
    monkeypatch.setattr(
        NodePairing,
        "add_by_login",
        classmethod(lambda cls, address, user, port: tried.append(address) or False),
    )
    assert NodePairing.add("spark-9") is False
    assert tried == ["spark-9"]


def test_a_typed_address_is_matched_only_against_the_address_a_node_answers_at(monkeypatch):
    from dreamference.node import NodePairing

    # Another machine names its service after the real node's address: `node add` with that
    # address must not send the node password there.
    spoof = {"name": "192.168.50.106", "node": "6666-evil", "address": "192.168.50.66", "port": "8000"}
    real = {"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.106", "port": "8000"}
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [spoof, real]))
    assert NodePairing.discover("192.168.50.106") == real
    scoped = dict(real, address="fe80::1%wlP9s9")
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [scoped]))
    assert NodePairing.discover("fe80::1") == scoped
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [spoof]))
    assert NodePairing.discover("192.168.50.106") is None


def test_a_txt_record_cannot_replace_where_a_node_answers():
    line = (
        "=;wlP9s9;IPv4;spark-2;_mightling-node._tcp;local;spark-2.local;192.168.50.66;8000;"
        '"node=6666-evil" "address=192.168.50.106" "name=spark-1" "interface=lo" "host=x" "port=22"'
    )
    (node,) = NodeBrowser.parse(line)
    assert (node["address"], node["name"], node["interface"], node["host"], node["port"]) == (
        "192.168.50.66",
        "spark-2",
        "wlP9s9",
        "spark-2.local",
        "8000",
    )
    assert node["node"] == "6666-evil"


def _ssh_stand_in(calls, ssh_g="", authorize=(0, "")):
    """Stand in for subprocess.run in pairing, recording every call.

    `ssh -G` prints `ssh_g`, `node authorize` answers `authorize`, `info` answers as the node.
    """

    def run(argv, **kwargs):
        calls.append(list(argv))
        if list(argv[:2]) == ["ssh", "-G"]:
            return subprocess.CompletedProcess(argv, 0 if ssh_g else 255, ssh_g, "")
        if argv[-1].endswith("node authorize"):
            return subprocess.CompletedProcess(argv, authorize[0], "", authorize[1])
        if argv[-1] == "info":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"node": "2222-bbbb"}), "")
        raise AssertionError(f"unexpected command {argv!r}")

    return run


def _advertise(monkeypatch):
    from dreamference.node import NodePairing

    NodePairing.forget_resolved()
    advert = {"name": "spark-2", "node": "2222-bbbb", "address": "spark-2.local", "port": "8000"}
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [advert]))
    monkeypatch.setattr(
        NodePairing, "ensure_key", classmethod(lambda cls: "ssh-ed25519 AAAAtest mightling-node")
    )


def test_node_add_takes_the_user_from_ssh_config_before_the_local_user(monkeypatch):
    import getpass

    from dreamference.node import NodePairing

    _advertise(monkeypatch)
    calls = []
    monkeypatch.setattr(
        subprocess, "run", _ssh_stand_in(calls, ssh_g="host spark-2\nuser remote-acct\n")
    )
    assert NodePairing.add("spark-2") is True
    assert ["ssh", "-G", "spark-2"] in calls
    assert NodePairing.paired()[0]["user"] == "remote-acct"
    assert calls[-1][-2] == "remote-acct@spark-2.local"
    # ssh -G gives nothing: this machine's user name.
    calls.clear()
    monkeypatch.setattr(subprocess, "run", _ssh_stand_in(calls))
    assert NodePairing.add("spark-2") is True
    assert NodePairing.paired()[0]["user"] == getpass.getuser()
    # --user wins over both, and ssh -G is not asked for a user.
    calls.clear()
    monkeypatch.setattr(subprocess, "run", _ssh_stand_in(calls, ssh_g="user remote-acct\n"))
    assert NodePairing.add("spark-2", user="given") is True
    assert NodePairing.paired()[0]["user"] == "given" and ["ssh", "-G", "spark-2"] not in calls


def test_node_add_names_the_user_tried_when_the_login_is_refused(monkeypatch, capsys):
    from dreamference.node import NodePairing
    from dreamference.node.fleet_session import FleetSession

    _advertise(monkeypatch)
    calls = []
    refused = (255, "remote-acct@spark-2.local: Permission denied (publickey,password).\n")
    monkeypatch.setattr(
        subprocess, "run", _ssh_stand_in(calls, ssh_g="user remote-acct\n", authorize=refused)
    )
    assert NodePairing.add("spark-2") is False
    err = capsys.readouterr().err
    assert "Permission denied" in err and "'remote-acct'" in err and "--user" in err
    # By address, when no browse shows it: the same hint from the provisioning login.
    monkeypatch.setattr(NodePairing, "discover", classmethod(lambda cls, name: None))

    def refuse(self):
        self.last_error = refused[1]
        return False

    monkeypatch.setattr(FleetSession, "open", refuse)
    assert NodePairing.add("spark-9") is False
    out = capsys.readouterr().out
    assert "Could not log in to remote-acct@spark-9" in out and "--user" in out
    # Another failure gives no hint.
    monkeypatch.setattr(
        subprocess,
        "run",
        _ssh_stand_in(calls, ssh_g="user remote-acct\n", authorize=(255, "timeout")),
    )
    monkeypatch.setattr(
        NodePairing,
        "discover",
        classmethod(
            lambda cls, name: {"name": "spark-2", "node": "2222-bbbb", "address": "spark-2.local"}
        ),
    )
    assert NodePairing.add("spark-2") is False
    assert "--user" not in str(capsys.readouterr())


def test_every_pairing_ssh_call_offers_one_explicit_key_only(monkeypatch):
    from dreamference.node import NodePairing
    from dreamference.node.fleet_session import FleetSession

    _advertise(monkeypatch)
    own_key = Path.home() / ".ssh" / "own_key"
    own_key.parent.mkdir(parents=True, exist_ok=True)
    own_key.write_text("key\n")
    calls = []
    ssh_g = "user remote-acct\nidentityfile ~/.ssh/missing_key\nidentityfile ~/.ssh/own_key\n"
    monkeypatch.setattr(subprocess, "run", _ssh_stand_in(calls, ssh_g=ssh_g))
    assert NodePairing.add("spark-2") is True
    logins = [call for call in calls if call[0] == "ssh" and call[1] != "-G"]
    assert len(logins) == 2  # node authorize (first login), then info (the pairing key)
    for call in logins:
        assert call[call.index("-i") + 1] and "IdentitiesOnly=yes" in call
    assert logins[0][logins[0].index("-i") + 1] == str(own_key)  # the key ~/.ssh/config names
    assert logins[1][logins[1].index("-i") + 1] == str(NodePairing.key_path())
    # Nothing configured: the default key.
    monkeypatch.setattr(subprocess, "run", _ssh_stand_in([]))
    assert NodePairing.ssh_config("elsewhere") == (None, Path.home() / ".ssh" / "id_ed25519")
    monkeypatch.setattr(subprocess, "run", _ssh_stand_in(calls, ssh_g=ssh_g))
    assert NodePairing.ssh_config("elsewhere")[1] == own_key
    # Pairing by address: the provisioning login offers the same key.
    monkeypatch.setattr(NodePairing, "discover", classmethod(lambda cls, name: None))
    opened = []
    monkeypatch.setattr(
        FleetSession, "open", lambda self: opened.append(self.options(first_contact=True)) or False
    )
    assert NodePairing.add("spark-9") is False
    assert (
        opened[0][opened[0].index("-i") + 1] == str(own_key) and "IdentitiesOnly=yes" in opened[0]
    )


def test_the_node_list_shows_what_each_node_serves_without_any_pairing(monkeypatch):
    from dreamference.night_shift import NightShiftHost
    from dreamference.node import NodeRemote

    mine = NodeIdentity.ensure()
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-1",
                    "node": mine,
                    "address": "192.168.50.105",
                    "port": "8000",
                    "main": "1",
                    "version": "1.3.0",
                    "state": "ready",
                },
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.106",
                    "port": "8000",
                    "version": "1.3.0",
                    "state": "stopped",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        NightShiftHost,
        "served_model",
        classmethod(
            lambda cls, host, timeout=3.0: (
                ("RadixArk/Qwen3.8-27B-NVFP4", 262144) if "105" in host else None
            )
        ),
    )
    monkeypatch.setattr(
        NightShiftHost,
        "metrics",
        classmethod(
            lambda cls, host, timeout=3.0: {"running": 2.0, "served": 1.0, "kv_pool": 156907.0}
        ),
    )
    monkeypatch.setattr(
        NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 40 * 1024**3)
    )
    monkeypatch.setattr(NightShiftHost, "mem_total_bytes", classmethod(lambda cls: 120 * 1024**3))
    lines = NodeRemote.list_lines()
    assert lines[0] == (
        "spark-1  http://192.168.50.105:8000/v1  RadixArk/Qwen3.8-27B-NVFP4 (262144 tokens), "
        "2 request(s) running, KV pool 156907 tokens, 40.0 of 120.0 GiB free  Mightling 1.3.0  (this machine)"
    )
    # Memory is not on the open model port: an unpaired node shows none.
    assert lines[1] == (
        "spark-2  http://192.168.50.106:8000/v1  model server stopped  Mightling 1.3.0  "
        "(not paired: `ling-admin node add spark-2` to manage it; not a coding model)"
    )
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert "No Mightling node answers" in NodeRemote.list_lines()[0]


def test_a_paired_nodes_memory_is_asked_over_the_pairing(monkeypatch):
    from dreamference.night_shift import NightShiftHost
    from dreamference.node import NodePairing, NodeRemote

    paired_record()
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.106",
                    "port": "8000",
                    "main": "1",
                    "version": "1.3.0",
                    "state": "stopped",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        NightShiftHost, "served_model", classmethod(lambda cls, host, timeout=3.0: None)
    )
    asked = []
    info = {"node": "2222-bbbb", "mem_available": 60 * 1024**3, "mem_total": 120 * 1024**3}
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: (
                asked.append(request)
                or subprocess.CompletedProcess([], 0, json.dumps(info) + "\n", "")
            )
        ),
    )
    assert NodeRemote.list_lines() == [
        "spark-2  http://192.168.50.106:8000/v1  model server stopped, "
        "60.0 of 120.0 GiB free  Mightling 1.3.0  (paired)"
    ]
    assert asked == ["info"]
    # A paired node that does not answer is listed without memory, not left out.
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: subprocess.CompletedProcess(
                [], 255, "", "timeout"
            )
        ),
    )
    assert NodeRemote.list_lines() == [
        "spark-2  http://192.168.50.106:8000/v1  model server stopped  Mightling 1.3.0  (paired)"
    ]


def test_info_says_what_a_lane_and_the_list_need(monkeypatch, capsys):
    from dreamference.night_shift import NightShiftHost
    from dreamference.node import NodeServe

    monkeypatch.setattr(NodeServe, "linger", classmethod(lambda cls: True))
    monkeypatch.setattr(NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 1))
    monkeypatch.setattr(NightShiftHost, "mem_total_bytes", classmethod(lambda cls: 2))
    assert NodeServe.serve("info") == 0
    info = json.loads(capsys.readouterr().out)
    assert (info["mem_available"], info["mem_total"], info["model_port"], info["runner"]) == (
        1,
        2,
        8000,
        None,
    )


# -- Part 3: jobs on another node (§13) ------------------------------------------------------------


def _real_sandbox_blocker():
    from dreamference.node import NodeJob

    return NodeJob.__dict__["sandbox_blocker"]


# The job fixture replaces the probe; the test of the probe itself puts it back.
REAL_SANDBOX_BLOCKER = _real_sandbox_blocker()


def _real_sandbox_command():
    from dreamference.node import NodeJob

    return NodeJob.__dict__["sandbox_command"]


REAL_SANDBOX_COMMAND = _real_sandbox_command()


def job_request(**changes):
    request = {
        "id": "20261002-1200-abc",
        "repo": "calc-0123456789",
        "commit": "a" * 40,
        "command": ["python3", "train.py"],
        "memory": "8G",
        "time": "90m",
    }
    request.update(changes)
    return request


def test_a_job_cannot_be_accepted_without_a_memory_cap_and_a_time_limit():
    from dreamference.node import NodeJob

    record = NodeJob.validate(job_request())
    assert (record["memory_bytes"], record["time_s"], record["status"]) == (
        8 * 1024**3,
        5400,
        "queued",
    )
    for missing in ("memory", "time"):
        request = job_request()
        del request[missing]
        with pytest.raises(ValueError, match="memory cap and a time limit"):
            NodeJob.validate(request)
    for changes in ({"memory": "0"}, {"time": "0m"}, {"memory": "lots"}, {"time": "soon"}):
        with pytest.raises(ValueError):
            NodeJob.validate(job_request(**changes))


def test_the_node_sets_the_ceilings_and_refuses_what_came_from_outside_malformed():
    from dreamference.node import NodeJob

    with pytest.raises(ValueError, match="at most 32G"):
        NodeJob.validate(job_request(memory="64G"))
    with pytest.raises(ValueError, match="at most 8h"):
        NodeJob.validate(job_request(time="24h"))
    with pytest.raises(ValueError, match="GPU jobs are not allowed"):
        NodeJob.validate(job_request(gpu=True))
    for changes in (
        {"id": "../../x"},
        {"id": "20261002-1200-ABC"},
        {"repo": "../etc"},
        {"repo": "a/b"},
        {"repo": ""},
        {"commit": "HEAD"},
        {"commit": "a" * 39},
        {"command": "rm -rf /"},
        {"command": []},
        {"command": ["ok", 3]},
        {"test": ["x"]},
    ):
        with pytest.raises(ValueError):
            NodeJob.validate(job_request(**changes))
    with pytest.raises(ValueError):
        NodeJob.validate(["not", "an", "object"])


def test_the_stricter_of_the_two_airgap_levels_applies(monkeypatch):
    from dreamference.node import NodeJob

    assert NodeJob.stricter("off", "on") == "on" and NodeJob.stricter("on", "off") == "on"
    assert NodeJob.stricter("off", "off") == "off" and NodeJob.stricter("nonsense", "off") == "on"
    monkeypatch.setattr(NodeJob, "node_airgap_level", classmethod(lambda cls: "on"))
    assert NodeJob.validate(job_request(airgapped="off"))["airgapped"] == "on"
    monkeypatch.setattr(NodeJob, "node_airgap_level", classmethod(lambda cls: "off"))
    assert NodeJob.validate(job_request(airgapped="on"))["airgapped"] == "on"
    assert NodeJob.validate(job_request())["airgapped"] == "off"


def test_the_sandbox_writes_only_the_worktree_hides_the_home_folder_and_the_gpu(tmp_path):
    from dreamference.node import NodeJob

    tree = tmp_path / "jobs" / "20261002-1200-abc" / "tree"
    argv = NodeJob.sandbox_command(
        tree, ["python3", "train.py"], network=True, job_id="20261002-1200-abc"
    )
    assert argv[0] == "bwrap" and argv[-3:] == ["--", "python3", "train.py"]
    # One read-write bind, and it is the worktree; the system is read-only.
    binds = [argv[i + 1] for i, word in enumerate(argv) if word == "--bind"]
    assert binds == [str(tree)]
    assert argv[argv.index("--ro-bind") + 1 : argv.index("--ro-bind") + 3] == ["/", "/"]
    hidden = [argv[i + 1] for i, word in enumerate(argv) if word == "--tmpfs"]
    assert os.path.expanduser("~") in hidden and "/run" in hidden and "/tmp" in hidden
    # bubblewrap hides a bind made under a later tmpfs: every tmpfs comes first.
    assert max(i for i, word in enumerate(argv) if word == "--tmpfs") < argv.index("--bind")
    # The environment is built from nothing, and CUDA sees no device.
    assert "--clearenv" in argv
    environment = {argv[i + 1]: argv[i + 2] for i, word in enumerate(argv) if word == "--setenv"}
    assert (
        environment["CUDA_VISIBLE_DEVICES"] == ""
        and environment["MIGHTLING_JOB"] == "20261002-1200-abc"
    )
    assert "SSH_AUTH_SOCK" not in environment
    # A script at `on` has no network at all; otherwise it keeps it.
    assert "--unshare-net" not in argv
    assert "--unshare-net" in NodeJob.sandbox_command(
        tree, ["true"], network=False, job_id="20261002-1200-abc"
    )


def test_a_job_is_a_capped_unit_of_its_own_not_a_child_of_the_connection():
    from dreamference.node import NodeJob

    record = NodeJob.validate(job_request(memory="16G", time="2h"))
    argv = NodeJob.unit_command(record, "/home/u/.local/bin/ling-admin")
    assert argv[:2] == ["systemd-run", "--user"] and "--scope" not in argv
    assert "--unit=mightling-job-20261002-1200-abc" in argv
    properties = [argv[i + 1] for i, word in enumerate(argv) if word == "-p"]
    assert f"MemoryMax={16 * 1024**3}" in properties and "MemorySwapMax=0" in properties
    assert "RuntimeMaxSec=7200" in properties
    assert argv[-4:] == ["/home/u/.local/bin/ling-admin", "node", "job-exec", "20261002-1200-abc"]


@pytest.fixture
def job_node(tmp_path, monkeypatch):
    """This machine as the node: a job repository holding one commit, no unit, no sandbox."""
    from dreamference.night_shift import NightShiftHost
    from dreamference.node import NodeJob

    monkeypatch.setattr(NodeJob, "USE_UNIT", False)
    monkeypatch.setattr(NodeJob, "sandbox_blocker", classmethod(lambda cls: None))
    monkeypatch.setattr(
        NodeJob,
        "sandbox_command",
        classmethod(
            lambda cls, tree, command, network, job_id, writable=(), readable=(), environment=None: [
                "env",
                *[f"{key}={value}" for key, value in (environment or {}).items()],
                *command,
            ]
        ),
    )
    monkeypatch.setattr(NightShiftHost, "heavy_jobs", classmethod(lambda cls: []))
    monkeypatch.setattr(
        NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 64 * 1024**3)
    )
    source = tmp_path / "source"
    source.mkdir()
    run = lambda *args: subprocess.run(
        ["git", "-C", str(source), *args], capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "T")
    (source / "data.txt").write_text("one\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    commit = run("rev-parse", "HEAD").stdout.strip()
    repo = NodeJob.repo_path("calc-0123456789")
    repo.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
    run("push", "-q", str(repo), f"{commit}:refs/jobs/x")
    return {"commit": commit, "repo": repo}


def test_a_job_runs_in_a_worktree_and_its_changes_come_back_as_a_branch(job_node, capsys):
    from dreamference.node import NodeJob

    record = NodeJob.submit(
        job_request(
            commit=job_node["commit"],
            command=["bash", "-c", "echo hello; echo two >> data.txt; echo new > out.txt"],
            test="grep -q two data.txt",
            author={"name": "Owner", "email": "s@example.org"},
        ),
        "/x/ling-admin",
    )
    assert NodeJob.execute(record["id"]) == 0
    done = NodeJob.read(record["id"])
    assert (done["status"], done["exit_code"], done["test_exit_code"], done["branch"]) == (
        "done",
        0,
        0,
        "job/20261002-1200-abc",
    )
    git = lambda *args: (
        subprocess.run(
            ["git", "--git-dir", str(job_node["repo"]), *args], capture_output=True, text=True
        ).stdout
    )
    assert (
        git("log", "-1", "--format=%an <%ae> %s", "job/20261002-1200-abc").strip()
        == "Owner <s@example.org> job: bash -c 'echo hello; echo two >> data.txt; echo new > out.txt'"
    )
    assert sorted(git("show", "--name-only", "--format=", "job/20261002-1200-abc").split()) == [
        "data.txt",
        "out.txt",
    ]
    assert not (
        NodeJob.job_dir(record["id"]) / "tree"
    ).exists()  # the worktree is gone, the branch stays
    # The output is kept on the node, and reading it again is only a view.
    capsys.readouterr()
    assert NodeJob.follow(record["id"]) == 0
    assert "hello" in capsys.readouterr().out
    with pytest.raises(ValueError, match="already exists"):
        NodeJob.submit(job_request(commit=job_node["commit"]), "/x/ling-admin")


def test_a_failing_job_and_a_job_that_changes_nothing(job_node, capsys):
    from dreamference.node import NodeJob

    failing = NodeJob.submit(
        job_request(
            id="20261002-1201-aaa",
            commit=job_node["commit"],
            command=["bash", "-c", "echo boom; exit 7"],
        ),
        "/x",
    )
    assert NodeJob.execute(failing["id"]) == 7
    record = NodeJob.read(failing["id"])
    assert (record["status"], record["exit_code"], record["branch"]) == ("failed", 7, None)
    assert NodeJob.follow(failing["id"]) == 7 and "boom" in capsys.readouterr().out
    quiet = NodeJob.submit(
        job_request(id="20261002-1202-bbb", commit=job_node["commit"], command=["true"]), "/x"
    )
    assert NodeJob.execute(quiet["id"]) == 0
    assert NodeJob.read(quiet["id"])["branch"] is None  # no change: only the log and the code
    test_fails = NodeJob.submit(
        job_request(
            id="20261002-1203-ccc", commit=job_node["commit"], command=["true"], test="exit 3"
        ),
        "/x",
    )
    assert NodeJob.execute(test_fails["id"]) == 3
    assert NodeJob.read(test_fails["id"])["status"] == "failed"
    assert [job["id"][-3:] for job in NodeJob.records()] == ["aaa", "bbb", "ccc"]


def test_the_working_node_decides_whether_it_can_take_the_job(job_node, monkeypatch):
    from dreamference.night_shift import NightShiftHost, NightShiftQueue
    from dreamference.node import NodeJob

    with pytest.raises(ValueError, match="was not pushed"):
        NodeJob.submit(job_request(commit="b" * 40), "/x")
    monkeypatch.setattr(
        NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 10 * 1024**3)
    )
    with pytest.raises(ValueError, match="10.0 GiB of memory available"):
        NodeJob.submit(job_request(commit=job_node["commit"]), "/x")
    monkeypatch.setattr(
        NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 64 * 1024**3)
    )
    monkeypatch.setattr(
        NightShiftHost, "heavy_jobs", classmethod(lambda cls: ["a ling build holds the build lock"])
    )
    with pytest.raises(ValueError, match="build lock"):
        NodeJob.submit(job_request(commit=job_node["commit"]), "/x")
    monkeypatch.setattr(NightShiftHost, "heavy_jobs", classmethod(lambda cls: []))
    monkeypatch.setattr(
        NightShiftQueue, "runner_active", classmethod(lambda cls, night_dir=None: True)
    )
    with pytest.raises(ValueError, match="night run"):
        NodeJob.submit(job_request(commit=job_node["commit"]), "/x")
    assert NodeJob.records() == []  # a refused job leaves nothing behind


def test_a_node_that_cannot_sandbox_refuses_the_job_and_never_runs_it_without(
    job_node, monkeypatch
):
    from dreamference.node import NodeJob

    # The GB10's own failure, 2026-10-02: AppArmor denies bubblewrap's user namespace outside a
    # profiled program, so a job's unit could not have sandboxed anything.
    failed = subprocess.CompletedProcess(
        [], 1, "", "bwrap: setting up uid map: Permission denied\n"
    )
    monkeypatch.setattr(NodeJob, "sandbox_blocker", REAL_SANDBOX_BLOCKER)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_: (
            failed
            if argv[0] in ("bwrap", "systemd-run")
            else subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )
    reason = NodeJob.sandbox_blocker()
    assert (
        "cannot sandbox a job" in reason
        and "setting up uid map: Permission denied" in reason
        and "userns" in reason
    )
    monkeypatch.setattr(NodeJob, "sandbox_blocker", classmethod(lambda cls: reason))
    record = NodeJob.validate(job_request(commit=job_node["commit"]))
    assert NodeJob.admission_blocker(record) == reason


def test_a_job_stopped_by_its_limits_is_settled_and_a_cancel_is_kept(job_node, monkeypatch):
    from dreamference.node import NodeJob

    record = NodeJob.submit(job_request(commit=job_node["commit"]), "/x")
    record["status"] = "running"
    NodeJob.write(record)
    monkeypatch.setattr(NodeJob, "USE_UNIT", True)
    monkeypatch.setattr(NodeJob, "unit_active", classmethod(lambda cls, job_id: False))
    settled = NodeJob.reconcile(record["id"])
    assert (
        settled["status"] == "failed"
        and "time limit (90m)" in settled["note"]
        and "memory cap (8G)" in settled["note"]
    )
    monkeypatch.setattr(NodeJob, "USE_UNIT", False)
    other = NodeJob.submit(job_request(id="20261002-1205-ddd", commit=job_node["commit"]), "/x")
    assert NodeJob.cancel(other["id"]) is True and NodeJob.cancel(other["id"]) is False
    assert NodeJob.read(other["id"])["status"] == "cancelled"


def test_only_a_job_repository_can_be_pushed_to_or_fetched_from(monkeypatch, capsys):
    from dreamference.node import NodeJob, NodeServe

    ran = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    for path in (
        "/home/user/mightling",
        "jobs/../../.ssh.git",
        "jobs/a/b.git",
        "jobs/x",
        "/etc/passwd",
        "~/.mightling",
    ):
        assert NodeServe.serve(f"git-receive-pack '{path}'") == 2, path
        assert NodeServe.serve(f"git-upload-pack '{path}'") == 2, path
    assert ran == []
    assert (
        NodeServe.serve("git-upload-pack 'jobs/calc-0123456789.git'") == 2
    )  # nothing was ever pushed
    assert NodeServe.serve("git-receive-pack '/jobs/calc-0123456789.git'") == 0
    repo = str(NodeJob.repo_path("calc-0123456789"))
    assert ran == [["git", "init", "--quiet", "--bare", repo], ["git-receive-pack", repo]]


def test_job_requests_through_serve_job(job_node, monkeypatch, capsys):
    import base64

    from dreamference.node import NodeJob, NodeServe

    encode = lambda request: base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
    assert NodeServe.serve("job-submit not-base64-json") == 2
    assert (
        NodeServe.serve(
            f"job-submit {encode(job_request(commit=job_node['commit'], memory='64G'))}"
        )
        == 2
    )
    assert "refused the job" in capsys.readouterr().err and NodeJob.records() == []
    monkeypatch.setattr(
        NodeJob,
        "follow",
        classmethod(lambda cls, job_id, offset=0, poll_s=0.5: NodeJob.execute(job_id)),
    )
    assert (
        NodeServe.serve(
            f"job-submit {encode(job_request(commit=job_node['commit'], command=['true']))}"
        )
        == 0
    )
    assert "Job 20261002-1200-abc started" in capsys.readouterr().out
    assert NodeServe.serve("job-list") == 0
    listed = json.loads(capsys.readouterr().out.strip())
    assert (listed["id"], listed["status"]) == ("20261002-1200-abc", "done")
    for request in (
        "job-logs",
        "job-logs ../../etc",
        "job-logs 20261002-1200-zzz",
        "job-cancel",
        "job-cancel x; y",
        "job-logs 20261002-1200-abc --follow",
        "job-exec 20261002-1200-abc",
        "job-list all",
        "job-logs 20261002-1200-abc \u00b2",  # a digit to str.isdigit, not to int()
        "job-logs 20261002-1200-abc " + "1" * 5000,  # past int()'s digit limit
        f"model-receive qwen3.8-27b-nvfp4-dflash2 {chr(0xb2)}",
        "model-receive qwen3.8-27b-nvfp4-dflash2 " + "1" * 5000,
        f"adapter-receive helper org/helper {'0' * 40} {chr(0xb2)}",
    ):
        assert NodeServe.serve(request) == 2, request
    assert NodeServe.serve("job-cancel 20261002-1200-abc") == 1  # already finished


def test_a_jobs_environment_is_built_once_per_lock_file_content_and_bound_read_only(
    job_node, capsys
):
    from dreamference.node import NodeJob

    setup = (
        'mkdir -p "$MIGHTLING_ENV/bin" && printf "#!/bin/sh\\necho from-the-env\\n" > "$MIGHTLING_ENV/bin/tool" '
        '&& chmod +x "$MIGHTLING_ENV/bin/tool" && echo built >> "$MIGHTLING_ENV/../builds"'
    )
    first = NodeJob.submit(
        job_request(
            id="20261002-1300-aaa", commit=job_node["commit"], command=["tool"], setup=setup
        ),
        "/x",
    )
    assert NodeJob.execute(first["id"]) == 0
    record = NodeJob.read(first["id"])
    assert record["environment"].endswith("(built)") and record["environment"].startswith(
        "calc-0123456789-"
    )
    assert "from-the-env" in (NodeJob.job_dir(first["id"]) / "output.log").read_text()
    second = NodeJob.submit(
        job_request(
            id="20261002-1301-bbb", commit=job_node["commit"], command=["tool"], setup=setup
        ),
        "/x",
    )
    assert NodeJob.execute(second["id"]) == 0
    assert NodeJob.read(second["id"])["environment"].endswith("(reused)")
    assert (NodeJob.jobs_dir() / "envs" / "builds").read_text() == "built\n"  # built once
    # The real sandbox binds the environment read-only and puts its bin first on PATH.
    tree = NodeJob.job_dir("20261002-1300-aaa") / "tree"
    env_dir = NodeJob.jobs_dir() / "envs" / "calc-0123456789-0123"
    argv = REAL_SANDBOX_COMMAND.__func__(
        NodeJob,
        tree,
        ["tool"],
        True,
        "20261002-1300-aaa",
        readable=[str(env_dir)],
        environment=NodeJob.environment_variables(env_dir),
    )
    assert [argv[i + 1] for i, word in enumerate(argv) if word == "--bind"] == [str(tree)]
    assert [argv[i + 1] for i, word in enumerate(argv) if word == "--ro-bind"] == [
        "/",
        str(env_dir),
    ]
    variables = {argv[i + 1]: argv[i + 2] for i, word in enumerate(argv) if word == "--setenv"}
    assert variables["PATH"].startswith(f"{env_dir}/bin:") and variables["MIGHTLING_ENV"] == str(
        env_dir
    )


def test_a_changed_lock_file_or_setup_command_is_a_new_environment(tmp_path):
    from dreamference.node import NodeJob

    (tmp_path / "requirements.txt").write_text("numpy==2.1\n")
    first = NodeJob.environment_key("calc", "pip install -r requirements.txt", tmp_path)
    assert NodeJob.environment_key("calc", "pip install -r requirements.txt", tmp_path) == first
    (tmp_path / "notes.md").write_text("not a lock file")
    assert NodeJob.environment_key("calc", "pip install -r requirements.txt", tmp_path) == first
    (tmp_path / "requirements.txt").write_text("numpy==2.2\n")
    assert NodeJob.environment_key("calc", "pip install -r requirements.txt", tmp_path) != first
    assert NodeJob.environment_key("calc", "uv sync", tmp_path) != first


def test_a_failed_setup_fails_the_job_and_a_missing_module_is_called_an_environment_failure(
    job_node,
):
    from dreamference.node import NodeJob

    failed = NodeJob.submit(
        job_request(
            id="20261002-1302-ccc", commit=job_node["commit"], command=["true"], setup="exit 4"
        ),
        "/x",
    )
    assert NodeJob.execute(failed["id"]) == 1
    record = NodeJob.read(failed["id"])
    assert record["status"] == "failed" and "setup command failed (exit 4)" in record["note"]
    assert not any(
        path.is_dir() for path in (NodeJob.jobs_dir() / "envs").glob("calc-*")
    )  # nothing half-built kept
    bare = NodeJob.submit(
        job_request(
            id="20261002-1303-ddd",
            commit=job_node["commit"],
            command=["bash", "-c", "echo \"ModuleNotFoundError: No module named 'numpy'\"; exit 1"],
        ),
        "/x",
    )
    assert NodeJob.execute(bare["id"]) == 1
    record = NodeJob.read(bare["id"])
    assert "environment failure" in record["note"] and record["environment"].startswith("none")


def test_a_setup_cannot_make_the_node_write_through_a_link_it_left(job_node, tmp_path):
    from dreamference.node import NodeJob

    # The setup runs sandboxed with its environment folder writable; the node writes the folder's
    # `.complete` marker after it, outside the sandbox. A link left there must not be followed:
    # with the setup text in the marker, it would write a key line into `authorized_keys`.
    victim = tmp_path / "authorized_keys"
    victim.write_text("restricted line\n")
    setup = f'ln -s {victim} "$MIGHTLING_ENV/.complete"\nssh-ed25519 AAAA attacker || true'
    job = NodeJob.submit(
        job_request(commit=job_node["commit"], command=["true"], setup=setup), "/x"
    )
    assert NodeJob.execute(job["id"]) == 0
    assert victim.read_text() == "restricted line\n"
    (env_dir,) = [path for path in (NodeJob.jobs_dir() / "envs").glob("calc-*") if path.is_dir()]
    marker = env_dir / ".complete"
    assert not marker.is_symlink() and marker.read_text() == setup + "\n"


def test_out_files_are_kept_beside_the_job_and_never_committed(job_node, capsysbinary):
    import io
    import tarfile

    from dreamference.node import NodeJob, NodeServe

    job = NodeJob.submit(
        job_request(
            commit=job_node["commit"],
            out="ckpt",
            command=[
                "bash",
                "-c",
                "mkdir -p ckpt/sub && echo w > ckpt/sub/w.bin && echo two >> data.txt",
            ],
        ),
        "/x",
    )
    assert NodeJob.execute(job["id"]) == 0
    record = NodeJob.read(job["id"])
    assert record["out_files"] == 1
    git = lambda *args: (
        subprocess.run(
            ["git", "--git-dir", str(job_node["repo"]), *args], capture_output=True, text=True
        ).stdout
    )
    assert git("show", "--name-only", "--format=", f"job/{job['id']}").split() == ["data.txt"]
    capsysbinary.readouterr()
    assert NodeServe.serve(f"job-out {job['id']}") == 0
    with tarfile.open(fileobj=io.BytesIO(capsysbinary.readouterr().out)) as archive:
        assert archive.getnames() == ["ckpt", "ckpt/sub", "ckpt/sub/w.bin"]
    for changes in ({"out": "../x"}, {"out": "/etc"}, {"out": "a b"}):
        with pytest.raises(ValueError, match="--out"):
            NodeJob.validate(job_request(**changes))


def test_a_job_cannot_make_the_node_run_its_git_hooks(job_node, tmp_path):
    from dreamference.node import NodeJob

    # The job's changes are committed by the node, outside the sandbox. A job that replaced the
    # worktree's `.git` with a repository of its own once had its hooks run there, as the node's
    # user: an escape from the sandbox.
    ran = tmp_path / "hook-ran"
    hook = f"#!/bin/sh\ntouch {ran}\n"
    script = (
        "rm .git && git init -q . && "
        f"for name in pre-commit post-checkout; do printf '{hook}' > .git/hooks/$name; "
        "chmod +x .git/hooks/$name; done; echo two >> data.txt"
    )
    job = NodeJob.submit(
        job_request(commit=job_node["commit"], command=["bash", "-c", script]), "/x"
    )
    assert NodeJob.execute(job["id"]) == 0
    assert not ran.exists()
    assert NodeJob.read(job["id"])["branch"] == f"job/{job['id']}"
    changed = subprocess.run(
        ["git", "--git-dir", str(job_node["repo"]), "show", "--name-only", "--format=",
         f"job/{job['id']}"],
        capture_output=True, text=True,
    ).stdout.split()
    assert changed == ["data.txt"]


def test_out_cannot_reach_outside_the_worktree_through_a_link(job_node, tmp_path):
    from dreamference.node import NodeJob

    # `--out x/.ssh` with `x` a link the job left (or committed) to the node user's home: the node
    # once moved the folder it named out of the home and `job-out` sent it to the client.
    home = tmp_path / "home-of-the-node"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("private\n")
    job = NodeJob.submit(
        job_request(
            commit=job_node["commit"], out="x/.ssh", command=["ln", "-s", str(home), "x"]
        ),
        "/x",
    )
    assert NodeJob.execute(job["id"]) == 0
    assert (home / ".ssh" / "id_ed25519").read_text() == "private\n"
    assert NodeJob.read(job["id"])["out_files"] == 0
    assert not (NodeJob.job_dir(job["id"]) / "out").exists()


def test_out_files_come_back_and_a_hostile_archive_cannot_write_outside(
    tmp_path, monkeypatch, capsys
):
    import io
    import tarfile

    from dreamference.node import NodeJobSender, NodePairing

    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    record = paired_record()
    NodeJobSender._remember("20261002-1200-abc", record, str(tmp_path), "calc-0123456789")
    stream = tmp_path / "stream.tar"
    with tarfile.open(stream, "w") as archive:
        for name, data in (("ckpt/model.bin", b"weights"), ("../../escape.txt", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(
        NodePairing, "ssh_command", classmethod(lambda cls, record, request: ["cat", str(stream)])
    )
    NodeJobSender._fetch_out("20261002-1200-abc", quiet_when_absent=False)
    received = NodeJobSender.received_dir("20261002-1200-abc")
    assert (received / "ckpt" / "model.bin").read_bytes() == b"weights"
    assert (
        not (received.parent.parent / "escape.txt").exists()
        and not (tmp_path / "escape.txt").exists()
    )


def test_only_paths_the_node_allows_are_bound_and_only_read_only(job_node, tmp_path, monkeypatch):
    from dreamference.node import NodeJob

    data = tmp_path / "datasets" / "imagenet"
    data.mkdir(parents=True)
    with pytest.raises(ValueError, match="allows nothing"):
        NodeJob.validate(job_request(binds=[str(data)]))
    config = tmp_path / "home-config.toml"
    config.write_text(f'[node]\nbindable = ["{tmp_path / "datasets"}"]\n')
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda path: (
            str(config) if path.endswith("config.toml") else path.replace("~", os.environ["HOME"])
        ),
    )
    assert NodeJob.validate(job_request(binds=[str(data)]))["binds"] == [str(data)]
    for path, reason in (
        (str(tmp_path), "does not allow"),
        (f"{tmp_path}/datasets/../../etc", "does not allow"),
        (str(tmp_path / "datasets" / "missing"), "does not exist"),
        ("relative/path", "absolute"),
    ):
        with pytest.raises(ValueError, match=reason):
            NodeJob.validate(job_request(binds=[path]))


def test_finished_jobs_are_pruned_a_day_after_the_fetch_or_after_fourteen_days(job_node, capsys):
    from datetime import datetime, timedelta

    from dreamference.node import NodeJob, NodeServe

    fetched = NodeJob.submit(
        job_request(
            id="20261002-1400-aaa",
            commit=job_node["commit"],
            command=["bash", "-c", "echo x > new.txt"],
        ),
        "/x",
    )
    NodeJob.execute(fetched["id"])
    unfetched = NodeJob.submit(
        job_request(id="20261002-1401-bbb", commit=job_node["commit"], command=["true"]), "/x"
    )
    NodeJob.execute(unfetched["id"])
    assert NodeServe.serve(f"job-fetched {fetched['id']}") == 0
    now = datetime.now().astimezone()
    assert NodeJob.prune(now) == []  # nothing is due yet
    listed = [
        json.loads(line)
        for line in (capsys.readouterr(), NodeServe.serve("job-list"), capsys.readouterr().out)[
            2
        ].splitlines()
    ]
    assert all(job["prune_after"] for job in listed)
    assert NodeJob.prune(now + timedelta(days=2)) == ["20261002-1400-aaa"]
    assert not NodeJob.job_dir("20261002-1400-aaa").exists()
    branches = subprocess.run(
        ["git", "--git-dir", str(job_node["repo"]), "branch", "--list", "job/*"],
        capture_output=True,
        text=True,
    ).stdout
    assert "20261002-1400-aaa" not in branches
    assert NodeJob.prune(now + timedelta(days=15)) == ["20261002-1401-bbb"]
    # A running job is never pruned, and cannot be marked fetched.
    running = NodeJob.submit(job_request(id="20261002-1402-ccc", commit=job_node["commit"]), "/x")
    assert NodeJob.mark_fetched(running["id"]) is False
    assert NodeJob.prune(now + timedelta(days=100)) == []


def test_a_paired_node_serving_the_same_model_is_a_lane_and_every_other_is_named(monkeypatch):
    from dreamference.node import NodeLanes, NodePairing

    paired_record()  # spark-2, .106
    for number in (3, 4, 5):
        NodePairing._save(
            {
                "node": f"{number}{number}{number}{number}-x",
                "name": f"spark-{number}",
                "address": f"192.168.50.10{number + 4}",
                "user": "owner",
                "ssh_port": 22,
            }
        )
    # Every node is reached where the browse shows it, never at its recorded address.
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": record["name"],
                    "node": record["node"],
                    "address": record["address"],
                    "port": "8000",
                }
                for record in NodePairing.paired()
            ]
        ),
    )
    answers = {
        "192.168.50.106": {"model_port": 8000},
        "192.168.50.107": {"model_port": 8000},
        "192.168.50.108": {"runner": "a Night Shift run"},
    }
    asked = []

    def run(cls, record, request, capture=True, input_text=None):
        asked.append((record["name"], request))
        info = answers.get(record["address"])
        return (
            subprocess.CompletedProcess([], 0, json.dumps(info) + "\n", "")
            if info
            else subprocess.CompletedProcess([], 255, "", "No route to host")
        )

    monkeypatch.setattr(NodePairing, "run", classmethod(run))

    class Host:
        @classmethod
        def served_model(cls, host, timeout=3.0):
            return {
                "http://192.168.50.106:8000": ("m", 1),
                "http://192.168.50.107:8000": ("other", 1),
            }.get(host)

        @classmethod
        def metrics(cls, host, timeout=3.0):
            return {
                "running": 0.0,
                "served": 0.0,
                "kv_pool": 300000.0 if "106" in host else 100000.0,
            }

    budget = lambda pool: (int(pool // 100000), 50000)
    lanes, notes = NodeLanes.lanes("http://127.0.0.1:8000", ("m", 1), "paired", Host, budget)
    assert [(lane["name"], lane["host"], lane["parallel"]) for lane in lanes] == [
        ("this machine", "http://127.0.0.1:8000", 1),
        ("spark-2", "http://192.168.50.106:8000", 3),
    ]
    assert any("spark-3: serves other, not m" in note for note in notes)
    assert any("spark-4: a Night Shift run is in progress there" in note for note in notes)
    assert any("spark-5: did not answer" in note for note in notes)
    assert "Also using spark-2's model server" in NodeLanes.describe(lanes[1])
    lanes, notes = NodeLanes.lanes(
        "http://127.0.0.1:8000", ("m", 1), ["spark-3", "spark-9"], Host, budget
    )
    assert len(lanes) == 1 and any("spark-9: not a paired node" in note for note in notes)
    asked.clear()
    assert NodeLanes.lanes("http://127.0.0.1:8000", ("m", 1), "none", Host, budget) == (
        lanes[:1],
        [],
    )
    assert asked == []  # "none" asks nobody
    assert (
        NodeLanes.wanted(None) is None
        and NodeLanes.wanted(False) == []
        and NodeLanes.wanted("a, b") == ["a", "b"]
    )


def test_the_sender_always_sends_caps_and_reaches_the_node_through_the_pairing(
    tmp_path, monkeypatch
):
    from dreamference.node import NodeJobSender

    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.106",
                    "port": "8000",
                }
            ]
        ),
    )
    repo = tmp_path / "My Repo!"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    request = NodeJobSender.compose(str(repo), "a" * 40, ["python3", "x.py"], None, None, None)
    assert (request["memory"], request["time"]) == ("8G", "90m")  # never sent without them
    assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-f]{3}", request["id"])
    assert re.fullmatch(r"My-Repo-[0-9a-f]{10}", request["repo"])
    assert (
        NodeJobSender.compose(str(repo), "a" * 40, ["x"], "16G", "2h", "pytest -q")["test"]
        == "pytest -q"
    )
    record = paired_record()
    assert (
        NodeJobSender.git_url(record, "calc-0123456789")
        == "ssh://owner@192.168.50.106:22/jobs/calc-0123456789.git"
    )
    fixed = dict(
        record, address="fd00::6", ssh_port=2222, address_fixed=True
    )  # a direct link, given by hand
    assert NodeJobSender.git_url(fixed, "r") == "ssh://owner@[fd00::6]:2222/jobs/r.git"
    ssh = NodeJobSender.git_environment(record)["GIT_SSH_COMMAND"]
    assert (
        ssh.startswith("ssh -i ")
        and "HostKeyAlias=mightling-node-2222-bbbb" in ssh
        and "StrictHostKeyChecking=yes" in ssh
    )
    assert " -p " not in ssh  # the URL carries the port


def test_the_jobs_own_options_are_not_read_as_node_runs():
    from dreamference.node import NodeJobSender

    split = NodeJobSender.split_run_arguments
    options, command = split(
        [
            "--memory",
            "16G",
            "--time=2h",
            "--test",
            "pytest -q",
            "--",
            "python",
            "train.py",
            "--epochs",
            "3",
            "--memory",
            "x",
        ]
    )
    assert (options.memory, options.time, options.test, options.gpu) == (
        "16G",
        "2h",
        "pytest -q",
        False,
    )
    assert command == ["python", "train.py", "--epochs", "3", "--memory", "x"]
    options, command = split(["--gpu", "python", "train.py", "--time", "5"])  # no `--`
    assert (
        options.gpu is True
        and options.time is None
        and command == ["python", "train.py", "--time", "5"]
    )
    options, command = split(["--", "ls", "-la"])
    assert options.memory is None and command == ["ls", "-la"]
    options, command = split(
        ["--bind", "/data/a", "--out=ckpt", "--setup", "uv sync", "python", "--bind", "x"]
    )
    assert (options.bind, options.out, options.setup) == (["/data/a"], "ckpt", "uv sync")
    assert command == ["python", "--bind", "x"]
    assert split([])[1] == []


def test_a_job_is_not_sent_from_outside_a_repository_or_to_an_unpaired_node(
    tmp_path, monkeypatch, capsys
):
    from dreamference.node import NodeJobSender

    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(tmp_path)) == 1
    assert "not a paired node" in capsys.readouterr().out
    paired_record()
    assert NodeJobSender.run("spark-2", [], cwd=str(tmp_path)) == 1
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(tmp_path)) == 1
    assert "git repository at a commit" in capsys.readouterr().out
    assert NodeJobSender.logs("20261002-1200-abc") == 1 and NodeJobSender.fetch("../x") == 1


# -- ling-app's Ask (ASK §10, §18.6) -------------------------------------------------------------
# Until 2026-10-08 the app's Chat window was the Onyx web UI, reached on a client through a loopback
# forwarder to the node and signed in with the per-install Onyx password; until 2026-10-09 the
# menu's Ask was a window on `ling web` on the same machine. Ask is now a view of the app window,
# on the app's own app-server: an Ask thread runs where the app runs, against the node's model
# server, so nothing is forwarded, nothing is signed in to and no password is read.

ELECTRON_SRC = Path(__file__).resolve().parent.parent / "desktop" / "electron" / "src"


def test_ask_in_the_app_reaches_no_server_and_reads_no_password():
    for retired in (
        "chat.ts",
        "web.ts",
        "sign-in.ts",
        "forwarder.ts",
        "discover.ts",
        "node_locator.ts",
    ):
        assert not (ELECTRON_SRC / retired).exists(), retired
    for source in ELECTRON_SRC.glob("*.ts"):
        if source.name == "credentials.test.ts":  # the test that names what must be absent
            continue
        text = source.read_text()
        # The preview tab's comment mode (DESKTOP_PARITY Phase 3 item 4) runs its element picker in an
        # isolated world of a previewed page: a page of the user's, never a sign-in.
        if source.name == "preview.ts":
            text = text.replace(
                "executeJavaScriptInIsolatedWorld(PICK_WORLD, [{ code: PICK_SCRIPT }], true)", ""
            )
        assert "executeJavaScript" not in text and "--print-url" not in text, source.name
        assert "admin@dreamference.dev" not in text and "chat-admin.json" not in text, source.name


def test_node_id_prints_the_id_and_writes_it_once(monkeypatch, capsys):
    from dreamference.cli import main

    for _ in range(2):
        monkeypatch.setattr("sys.argv", ["ling-admin", "node", "id"])
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == 0
    first, second = capsys.readouterr().out.split()
    assert first == second == NodeIdentity.read()


def test_the_installer_makes_a_gb10_a_node_and_offers_it_to_the_network():
    # §9: a GB10 gets both halves, its node id, and `node enable`, with no question asked (the user
    # decided 2026-10-08: advertising starts by itself); --no-advertise is the opt-out. The runs
    # themselves are in test_release_install.py.
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
    node_id = script.index("admin node id > /dev/null")
    enable = script.index("if admin node enable --yes")
    assert script.index("if admin host setup --yes") < node_id < enable
    assert "--no-advertise) ADVERTISE=0" in script
    assert (
        subprocess.run(
            ["bash", "-n", str(Path(__file__).resolve().parent.parent / "install.sh")]
        ).returncode
        == 0
    )


def test_every_node_command_reaches_its_handler(monkeypatch, capsys):
    # The committed file once had the job commands after the catch-all usage exit; the working
    # tree, which the tests ran, did not. Each command is now reached through `main()`.
    from dreamference.cli import main
    from dreamference.node import (
        NodeJob,
        NodeJobSender,
        NodeModelSync,
        NodePairing,
        NodeRemote,
        NodeServe,
    )

    reached = []
    note = lambda name, code=0: lambda *args, **kwargs: reached.append(name) or code
    monkeypatch.setattr(
        NodeModelSync,
        "sync",
        classmethod(
            lambda cls, name, model, address=None: (
                reached.append(("sync-model", name, model, address)) or 0
            )
        ),
    )
    monkeypatch.setattr(
        NodeRemote, "list_lines", classmethod(lambda cls: reached.append("list") or ["x"])
    )
    monkeypatch.setattr(NodeRemote, "status", classmethod(note("status")))
    monkeypatch.setattr(NodeRemote, "set_model", classmethod(note("set")))
    monkeypatch.setattr(NodeRemote, "start", classmethod(note("start")))
    monkeypatch.setattr(NodeRemote, "stop", classmethod(note("stop")))
    monkeypatch.setattr(NodePairing, "add", classmethod(note("add", True)))
    monkeypatch.setattr(NodePairing, "remove", classmethod(note("remove", True)))
    monkeypatch.setattr(
        NodeJobSender,
        "run",
        classmethod(
            lambda cls, name, command, **kw: (
                reached.append(("run", command, kw["memory"], kw["setup"], kw["out"], kw["binds"]))
                or 0
            )
        ),
    )
    monkeypatch.setattr(NodeJobSender, "jobs", classmethod(note("jobs")))
    monkeypatch.setattr(NodeJobSender, "logs", classmethod(note("logs")))
    monkeypatch.setattr(NodeJobSender, "cancel", classmethod(note("cancel")))
    monkeypatch.setattr(NodeJobSender, "fetch", classmethod(note("fetch")))
    monkeypatch.setattr(NodeJob, "execute", classmethod(note("job-exec")))
    monkeypatch.setattr(
        NodeServe,
        "serve",
        classmethod(
            lambda cls, request, key_tag=None: reached.append(("serve-job", request, key_tag)) or 0
        ),
    )
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "info")
    job = "20261002-1200-abc"
    for argv in (
        ["list"],
        ["status", "spark-2"],
        ["set", "spark-2", "--model", "m"],
        ["start", "spark-2"],
        ["stop", "spark-2"],
        ["add", "spark-2"],
        ["remove", "spark-2"],
        [
            "run",
            "spark-2",
            "--memory",
            "4G",
            "--setup",
            "uv sync",
            "--out",
            "ckpt",
            "--bind",
            "/data/a",
            "--bind",
            "/data/b",
            "--",
            "python3",
            "x.py",
            "--epochs",
            "3",
        ],
        ["jobs"],
        ["logs", job],
        ["cancel", job],
        ["fetch", job],
        ["job-exec", job],
        ["serve-job", "--key", "abc"],
        ["sync-model", "spark-2", "m", "--address", "10.0.0.2"],
    ):
        monkeypatch.setattr("sys.argv", ["ling-admin", "node", *argv])
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == 0, argv
    assert reached == [
        "list",
        "status",
        "set",
        "start",
        "stop",
        "add",
        "remove",
        (
            "run",
            ["python3", "x.py", "--epochs", "3"],
            "4G",
            "uv sync",
            "ckpt",
            ["/data/a", "/data/b"],
        ),
        "jobs",
        "logs",
        "cancel",
        "fetch",
        "job-exec",
        ("serve-job", "info", "abc"),
        ("sync-model", "spark-2", "m", "10.0.0.2"),
    ]


# -- copying a model to another node (§12.2) -------------------------------------------------------

SYNC_KEY = "qwen3.8-27b-nvfp4-dflash2"


def model_cache(hub, repo, files, revision="rev1"):
    """A hub cache folder as huggingface_hub writes one: blobs by checksum, a snapshot of links."""
    import hashlib

    folder = hub / ("models--" + repo.replace("/", "--"))
    for name, data in files.items():
        digest = hashlib.sha256(data).hexdigest()
        (folder / "blobs").mkdir(parents=True, exist_ok=True)
        (folder / "blobs" / digest).write_bytes(data)
        link = folder / "snapshots" / revision / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(folder / "blobs" / digest, link.parent))
    (folder / "refs").mkdir(exist_ok=True)
    (folder / "refs" / "main").write_text(revision)
    return folder


def test_a_model_is_copied_to_a_paired_node_and_lands_whole(tmp_path, monkeypatch, capsys):
    import sys
    from pathlib import Path

    from dreamference.node import NodeModelSync, NodePairing

    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    here, there = tmp_path / "here", tmp_path / "there"
    monkeypatch.setenv("HF_HUB_CACHE", str(here))
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    repos = NodeModelSync.repos(SYNC_KEY)
    assert len(repos) == 2  # the checkpoint and its drafter
    pins = {repo: ModelMatrixRegistry.pinned_revision(repo) for repo in repos}
    assert all(pins.values())
    # Only another revision of the checkpoint here (the Hub's default branch): not the one served.
    model_cache(
        here,
        repos[0],
        {"model.safetensors": b"main" * 300, "config.json": b"{}"},
        revision="319f741",
    )
    for index, repo in enumerate(repos[1:], 1):
        model_cache(
            here,
            repo,
            {"model.safetensors": b"w" * (1000 + index), "config.json": b"{}"},
            revision=pins[repo],
        )
    paired_record()
    checkout = Path(__file__).resolve().parent.parent
    receiver = [
        sys.executable,
        "-c",
        "import sys; from dreamference.node.node_model_sync import NodeModelSync; "
        # the size the sender declared, the last word of `model-receive <key> <size>`
        f"sys.exit(NodeModelSync.receive({SYNC_KEY!r}, int(sys.argv[1])))",
    ]
    # The receiver checks the real disk; a CI runner has less free than the copy's margin.
    receiver[2] = PLENTY_OF_DISK + receiver[2]
    environment = dict(os.environ, HF_HUB_CACHE=str(there), PYTHONPATH=str(checkout))
    monkeypatch.setattr(
        NodePairing,
        "ssh_command",
        classmethod(
            lambda cls, record, request: [
                "env",
                *[f"{k}={v}" for k, v in environment.items()],
                *receiver,
                request.split()[-1],
            ]
        ),
    )
    assert NodeModelSync.sync("spark-2", SYNC_KEY) == 1
    assert f"at {pins[repos[0]][:12]}" in capsys.readouterr().out and not there.exists()
    model_cache(
        here,
        repos[0],
        {"model.safetensors": b"w" * 1000, "config.json": b"{}"},
        revision=pins[repos[0]],
    )
    assert NodeModelSync.sync("spark-2", SYNC_KEY) == 0
    for repo in repos:
        folder = there / ("models--" + repo.replace("/", "--"))
        assert (folder / "snapshots" / pins[repo] / "config.json").read_bytes() == b"{}"
        assert (folder / "snapshots" / pins[repo] / "model.safetensors").is_symlink()
        assert (
            (folder / "snapshots" / pins[repo] / "model.safetensors").read_bytes().startswith(b"w")
        )
    # The pinned commit and its blobs only: the other revision and its weights stay here.
    checkpoint = there / ("models--" + repos[0].replace("/", "--"))
    assert sorted(p.name for p in (checkpoint / "snapshots").iterdir()) == [pins[repos[0]]]
    assert len(list((checkpoint / "blobs").iterdir())) == 2
    assert not list(there.glob(".mightling-sync-*"))
    assert "Serve it there with: ling-admin node set spark-2" in capsys.readouterr().out
    # A model this machine does not have is not sent.
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    assert NodeModelSync.sync("spark-2", SYNC_KEY) == 1


# What a receiver sees in place of the real disk: 2 TiB free, whatever the machine running the suite has.
PLENTY_OF_DISK = "import shutil, types; shutil.disk_usage = lambda path: types.SimpleNamespace(total=4 << 40, used=2 << 40, free=2 << 40); "


def receive(monkeypatch, hub, members, size=None):
    import io
    import shutil
    import sys
    import tarfile
    import types

    from dreamference.node import NodeModelSync

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: types.SimpleNamespace(total=4 << 40, used=2 << 40, free=2 << 40),
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if isinstance(data, str):  # a link to `data`
                info.type, info.linkname, data = tarfile.SYMTYPE, data, b""
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(buffer.getvalue())))
    if size is None:  # what the sender declares: the bytes of its plain files
        size = sum(len(data) for _, data in members if isinstance(data, bytes))
    return NodeModelSync.receive(SYNC_KEY, size)


def test_the_receiving_node_decides_what_lands_in_its_cache(tmp_path, monkeypatch, capsys):
    import hashlib

    from dreamference.node import NodeModelSync

    folder = "models--" + NodeModelSync.repos(SYNC_KEY)[0].replace("/", "--")
    good = b"weights"
    digest = hashlib.sha256(good).hexdigest()
    hub = tmp_path / "hub"
    assert (
        receive(monkeypatch, hub, [(f"{folder}/blobs/{'0' * 64}", good)]) == 1
    )  # checksum mismatch
    assert "does not match its checksum" in capsys.readouterr().err and not (hub / folder).exists()
    assert (
        receive(monkeypatch, hub, [("models--someone--else/blobs/x", good)]) == 1
    )  # another model
    assert receive(monkeypatch, hub, [(f"{folder}/../../escape", good)]) == 1  # out of the folder
    assert not (tmp_path / "escape").exists() and not list(hub.glob(".mightling-sync-*"))
    assert (
        receive(monkeypatch, hub, [(f"{folder}/blobs/{digest}", good)], size=10**18) == 2
    )  # no room
    assert receive(monkeypatch, hub, [(f"{folder}/blobs/{digest}", good)]) == 0
    assert (hub / folder / "blobs" / digest).read_bytes() == good
    # A second copy keeps what is there.
    assert receive(monkeypatch, hub, [(f"{folder}/blobs/{digest}", good)]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["already_here"] == 1
    from dreamference.node import NodeServe

    assert NodeServe.serve("model-receive not-a-model 10") == 2
    assert NodeServe.serve(f"model-receive {SYNC_KEY} lots") == 2


def test_a_copy_cannot_reach_outside_its_model_folder_through_a_link(tmp_path, monkeypatch, capsys):
    import hashlib

    from dreamference.node import NodeModelSync

    # Each of these once landed in the hub beside the model's folder, where `_merge` kept it: a
    # client could plant the files of a model the owner had not downloaded yet.
    folder = "models--" + NodeModelSync.repos(SYNC_KEY)[0].replace("/", "--")
    hub = tmp_path / "hub"
    escapes = [
        [(f"{folder}/l", ".."), (f"{folder}/l/models--evil--m/config.json", b"{}")],
        [(f"{folder}/snapshots/l", "../.."), (f"{folder}/snapshots/l/stray", b"x")],
        [(f"{folder}/here", "."), (f"{folder}/here/up", ".."), (f"{folder}/here/up/stray", b"x")],
        [(f"..{folder}/stray", b"x")],
        [(f"./{folder}/blobs/x", "/etc/passwd")],
    ]
    for members in escapes:
        assert receive(monkeypatch, hub, members) == 1, members
        assert list(hub.iterdir()) == [], members
    assert "not a folder of this model" in capsys.readouterr().err
    # The cache's own links, from a snapshot to its blob, still arrive.
    good = b"weights"
    digest = hashlib.sha256(good).hexdigest()
    snapshot = f"{folder}/snapshots/{'a' * 40}"
    members = [
        (f"{folder}/blobs/{digest}", good),
        (f"{snapshot}/model.bin", f"../../blobs/{digest}"),
        (f"{snapshot}/sub/tokenizer.bin", f"../../../blobs/{digest}"),
    ]
    assert receive(monkeypatch, hub, members) == 0
    assert (hub / snapshot / "model.bin").read_bytes() == good
    assert (hub / snapshot / "sub" / "tokenizer.bin").read_bytes() == good


def test_a_copy_cannot_carry_more_than_it_declared(tmp_path, monkeypatch, capsys):
    import hashlib

    from dreamference.node import NodeModelSync

    # The free-space check is made for the declared size; `model-receive <key> 0` followed by an
    # endless stream once filled the node's disk.
    folder = "models--" + NodeModelSync.repos(SYNC_KEY)[0].replace("/", "--")
    hub = tmp_path / "hub"
    good = b"weights"
    members = [(f"{folder}/blobs/{hashlib.sha256(good).hexdigest()}", good)]
    assert receive(monkeypatch, hub, members, size=len(good) - 1) == 1
    assert "more than the 6 bytes it declared" in capsys.readouterr().err
    assert list(hub.iterdir()) == []
    assert receive(monkeypatch, hub, members, size=len(good)) == 0


def test_enable_without_avahi_installs_it_or_publishes_nothing(machine, monkeypatch, capsys):
    # A GB10 reinstalled as Ubuntu Server has no Avahi; DGX OS has it.
    monkeypatch.setattr(NodeAdvertiser, "avahi_installed", classmethod(lambda cls: False))
    asked = []
    monkeypatch.setattr(
        NodeAdvertiser,
        "run_privileged",
        classmethod(lambda cls, command, purpose, yes=False: asked.append(command) or False),
    )
    assert NodeAdvertiser.enable() is False
    assert asked == [["apt-get", "install", "-y", "avahi-daemon"]]
    assert "MIGHTLING_NODE" in capsys.readouterr().out
    assert not NodeServiceFile.service_path.exists()


# -- a paired node is reached where it is now, never at a remembered address -----------------------


def test_every_connection_resolves_the_node_on_the_network_first(monkeypatch):
    # 2026-10-10: node-b's lease had moved from .30 to .246 and every lane probe went to the
    # old address for a night. The record remembers an address; no connection trusts it.
    from dreamference.node import NodePairing

    record = paired_record()  # remembered at .106
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.200",
                    "port": "8000",
                }
            ]
        ),
    )
    assert NodePairing.ssh_command(record, "info")[-2] == "owner@192.168.50.200"
    assert NodePairing.paired()[0]["address"] == "192.168.50.200"  # remembered for next time
    # Other nodes answer and this one does not: it is off, and its old address may be another
    # machine's by now. Nothing is tried.
    NodePairing.forget_resolved()
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-3",
                    "node": "3333-cccc",
                    "address": "192.168.50.106",
                    "port": "8000",
                }
            ]
        ),
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("ssh must not run"))
    answer = NodePairing.run(record, "info")
    assert (
        answer.returncode == 255
        and "not on the network" in answer.stderr
        and "192.168.50.200" in answer.stderr
    )
    with pytest.raises(LookupError):
        NodePairing.ssh_command(record, "info")
    # A browse that returns nothing at all (multicast blocked, the access point rebooting) is
    # repeated until the budget runs out; then the node is refused. The remembered address is
    # never the fallback (the user's rule, 2026-10-10: resolve, retry for a minute, then refuse).
    NodePairing.forget_resolved()
    browses = []
    monkeypatch.setattr(
        NodeBrowser, "browse", classmethod(lambda cls, timeout=6: browses.append(1) or [])
    )
    monkeypatch.setattr(NodePairing, "resolve_wait_s", 0.05)
    with pytest.raises(LookupError):
        NodePairing.ssh_command(record, "info")
    assert len(browses) >= 2, "the browse was repeated before the node was given up"
    # ... and a node that appears during the wait is found.
    NodePairing.forget_resolved()
    answers = [
        [],
        [],
        [{"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.200", "port": "8000"}],
    ]
    monkeypatch.setattr(
        NodeBrowser, "browse", classmethod(lambda cls, timeout=6: answers.pop(0) if answers else [])
    )
    monkeypatch.setattr(NodePairing, "resolve_wait_s", 5.0)
    monkeypatch.setattr("dreamference.node.node_pairing.time.sleep", lambda seconds: None)
    assert NodePairing.ssh_command(record, "info")[-2] == "owner@192.168.50.200"
    assert not answers, "the node was found on the third browse"
    NodePairing.forget_resolved()
    # An address given by hand (`sync-model --address`, a direct link) is used as it is.
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.201",
                    "port": "8000",
                }
            ]
        ),
    )
    fixed = dict(record, address="10.10.0.2", address_fixed=True)
    assert NodePairing.ssh_command(fixed, "info")[-2] == "owner@10.10.0.2"
    assert (
        NodePairing.paired()[0]["address"] == "192.168.50.200"
    )  # a fixed address is not remembered


def test_a_lane_and_a_job_follow_the_node_to_its_new_address(monkeypatch):
    from dreamference.node import NodeJobSender, NodeLanes, NodePairing

    paired_record()  # remembered at .106
    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {
                    "name": "spark-2",
                    "node": "2222-bbbb",
                    "address": "192.168.50.200",
                    "port": "8000",
                },
                {
                    "name": "spark-7",
                    "node": "7777-gggg",
                    "address": "192.168.50.207",
                    "port": "8000",
                },
            ]
        ),
    )
    NodePairing._save(
        {
            "node": "5555-eeee",
            "name": "spark-5",
            "address": "192.168.50.109",
            "user": "owner",
            "ssh_port": 22,
        }
    )  # paired, not on the network
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: subprocess.CompletedProcess(
                [], 0, json.dumps({"model_port": 8000}) + "\n", ""
            )
        ),
    )

    class Host:
        @classmethod
        def served_model(cls, host, timeout=3.0):
            return ("m", 1) if host == "http://192.168.50.200:8000" else None

        @classmethod
        def metrics(cls, host, timeout=3.0):
            return {"kv_pool": 200000.0}

    lanes, notes = NodeLanes.lanes(
        "http://127.0.0.1:8000", ("m", 1), "paired", Host, lambda pool: (2, None)
    )
    assert [lane["host"] for lane in lanes[1:]] == ["http://192.168.50.200:8000"]
    assert any("spark-5: is not on the network" in note for note in notes)
    record = NodePairing.find("spark-2", browse=False)
    assert NodeJobSender.git_url(record, "repo-abc").startswith("ssh://owner@192.168.50.200:22/")
    with pytest.raises(LookupError):
        NodeJobSender.git_url(NodePairing.find("spark-5", browse=False), "repo-abc")


def test_job_sender_run_handles_dirty_tree_unreachable_node_push_failure_drop_and_success(
    tmp_path, monkeypatch, capsys
):
    from dreamference.config import DreamferenceConfig
    from dreamference.node import NodeJobSender, NodePairing
    from dreamference.runner.safe_git import SafeGit

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@x"], check=True)
    (repo / "a.txt").write_text("1\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    (repo / "dirty.txt").write_text("untracked\n")

    # compose falls back to airgapped="on" when DreamferenceConfig fails.
    monkeypatch.setattr(
        DreamferenceConfig,
        "__init__",
        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("bad config")),
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert NodeJobSender.compose(str(repo), head, ["true"], None, None, None)["airgapped"] == "on"

    record = paired_record()
    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: dict(record))
    )

    # 1. git_url LookupError returns 1.
    monkeypatch.setattr(
        NodeJobSender,
        "git_url",
        classmethod(lambda cls, rec, slug: (_ for _ in ()).throw(LookupError("not on LAN"))),
    )
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(repo)) == 1
    out = capsys.readouterr().out
    assert "Uncommitted changes are not sent" in out and "not on LAN" in out

    # 2. git push failure returns 1.
    bare = tmp_path / "remote.git"
    monkeypatch.setattr(NodeJobSender, "git_url", classmethod(lambda cls, rec, slug: str(bare)))
    monkeypatch.setattr(
        NodeJobSender, "git_environment", classmethod(lambda cls, rec: dict(os.environ))
    )
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(repo)) == 1
    assert "Could not push to the node" in capsys.readouterr().out

    # 3. Connection drop (exit 255) during job-submit returns 255 without fetching.
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, rec, req, capture=True, input_text=None: subprocess.CompletedProcess(
                [], 255, "", ""
            )
        ),
    )
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(repo)) == 255
    assert "connection to spark-2 ended" in capsys.readouterr().out

    # 4. Successful job-submit with --out fetches branch, fetches out files, and marks job-fetched.
    calls = []
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, rec, req, capture=True, input_text=None: (
                calls.append(req) or subprocess.CompletedProcess([], 0, "", "")
            )
        ),
    )
    monkeypatch.setattr(NodeJobSender, "_fetch_branch", classmethod(lambda cls, jid, **kw: True))
    monkeypatch.setattr(NodeJobSender, "_fetch_out", classmethod(lambda cls, jid, **kw: True))
    assert NodeJobSender.run("spark-2", ["true"], cwd=str(repo), out="ckpt") == 0
    assert any(req.startswith("job-submit ") for req in calls)
    assert any(req.startswith("job-fetched ") for req in calls)
    assert SafeGit is not None


def test_job_sender_jobs_logs_cancel_and_fetch_branches(tmp_path, monkeypatch, capsys):
    from dreamference.node import NodeJobSender, NodePairing

    # jobs() with no paired nodes and with an unknown node name.
    assert NodeJobSender.jobs() == 0
    assert "No node is paired" in capsys.readouterr().out
    assert NodeJobSender.jobs("unknown-node") == 1
    assert "not a paired node" in capsys.readouterr().out

    record_a = paired_record()
    record_b = {
        "node": "3333-cccc",
        "name": "spark-3",
        "address": "192.168.50.107",
        "user": "owner",
        "ssh_port": 22,
    }
    NodePairing._save(record_b)

    answers = [
        subprocess.CompletedProcess([], 255, "", "ssh timeout"),
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "id": "20261002-1200-abc",
                    "status": "done",
                    "command": ["pytest", "-q"],
                    "branch": "job/20261002-1200-abc",
                    "out_files": 2,
                    "prune_after": "2026-10-03T12:00:00+00:00",
                }
            )
            + "\n",
            "",
        ),
    ]
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(lambda cls, rec, req, capture=True, input_text=None: answers.pop(0)),
    )
    assert NodeJobSender.jobs() == 0
    assert NodeJobSender.jobs("spark-2") == 0
    out = capsys.readouterr().out
    assert "not answering (ssh timeout)" in out
    assert "no jobs" in out
    assert (
        "branch job/20261002-1200-abc  2 --out file(s)  (pruned after 2026-10-03 12:00)" in out
    )

    # cancel(), logs(), fetch(), _fetch_branch() and _fetch_out() edge cases.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@x"], check=True)
    (repo / "f.txt").write_text("v1\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "v1"], check=True)

    job_id = "20261002-1200-abc"
    NodeJobSender._remember(job_id, record_a, str(repo), "repo-slug")
    sent_reqs = []
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, rec, req, capture=True, input_text=None: (
                sent_reqs.append(req) or subprocess.CompletedProcess([], 0, "", "")
            )
        ),
    )
    assert NodeJobSender.cancel(job_id) == 0
    assert NodeJobSender.logs(job_id) == 0
    assert sent_reqs == [f"job-cancel {job_id}", f"job-logs {job_id}"]

    # _fetch_branch and _fetch_out when node cannot be resolved on network.
    monkeypatch.setattr(
        NodeJobSender,
        "git_url",
        classmethod(lambda cls, rec, slug: (_ for _ in ()).throw(LookupError("offline"))),
    )
    monkeypatch.setattr(
        NodePairing,
        "ssh_command",
        classmethod(lambda cls, rec, req: (_ for _ in ()).throw(LookupError("offline"))),
    )
    assert NodeJobSender.fetch(job_id) == 1
    assert "offline" in capsys.readouterr().out

    # _fetch_branch when remote has no branch yet, and _fetch_out when stream is empty.
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    monkeypatch.setattr(NodeJobSender, "git_url", classmethod(lambda cls, rec, slug: str(bare)))
    monkeypatch.setattr(
        NodeJobSender, "git_environment", classmethod(lambda cls, rec: dict(os.environ))
    )
    monkeypatch.setattr(
        NodePairing, "ssh_command", classmethod(lambda cls, rec, req: ["true"])
    )
    assert NodeJobSender.fetch(job_id) == 1
    assert "No result branch" in capsys.readouterr().out
    assert not NodeJobSender._fetch_out(job_id, quiet_when_absent=False)
    assert "No --out files" in capsys.readouterr().out

    # _fetch_branch when remote has the branch -> fetch() returns 0 and sends job-fetched.
    subprocess.run(
        ["git", "-C", str(repo), "push", "--quiet", str(bare), f"HEAD:refs/heads/job/{job_id}"],
        check=True,
    )
    assert NodeJobSender.fetch(job_id) == 0
    assert f"job-fetched {job_id}" in sent_reqs

