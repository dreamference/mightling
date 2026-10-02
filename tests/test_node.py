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


def test_the_advert_follows_searxng_and_the_web_ui_started_after_enable(monkeypatch):
    path = NodeServiceFile.service_path
    path.write_text(NodeServiceFile.render(8000, NODE_ID, "1.3.0", "ready"))
    monkeypatch.setattr(NodeAdvertiser, "search_port", classmethod(lambda cls: 8888))
    NodeAdvertiser.on_searxng_started()               # not advertised: the file is not touched
    assert "search" not in NodeServiceFile.read()
    NodeSettings.save(advertise=True)
    NodeAdvertiser.on_searxng_started()
    assert NodeServiceFile.read()["search"] == "8888"
    NodeAdvertiser.on_web_ui_bound()                  # conftest's scratch web UI counts as installed
    assert NodeServiceFile.read()["web"] == "3000"
    NodeSettings.save(advertise=True, web=False)
    NodeAdvertiser.on_web_ui_bound()
    assert "web" not in NodeServiceFile.read() and NodeServiceFile.read()["search"] == "8888"


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


def test_enable_without_root_publishes_nothing_and_prints_the_file(machine, capsys):
    assert NodeAdvertiser.enable() is False           # sudo cannot prompt here: nothing is installed
    out = capsys.readouterr().out
    assert "<type>_puffin-node._tcp</type>" in out and "save the following as" in out
    assert "Nothing was published" in out
    # A web UI on the LAN that nobody can find would be the worst of both states: no bind moved.
    assert NodeSettings.load() == {"advertise": False, "web": False}
    assert machine == []
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
    out = capsys.readouterr().out
    assert "one account" in out and "Gmail" in out    # what sharing the web UI means, said at enable
    assert "searxng" in machine and "nginx" in machine
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


# -- the locator crate ------------------------------------------------------------------------------

def test_the_web_crates_locator_is_a_byte_identical_copy():
    # puffin-web-rs is built on its own, outside the Codex workspace, so it holds a copy; a
    # launcher and a `puffin-search` that read node.json differently would talk to two machines.
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    leaf = repo / "puffin-rs" / "node-locator" / "src" / "lib.rs"
    assert (repo / "puffin-web-rs" / "src" / "node_locator.rs").read_bytes() == leaf.read_bytes()
    # puffin-app is a third build of its own (Tauri), with a third copy.
    assert (repo / "desktop" / "src-tauri" / "src" / "node_locator.rs").read_bytes() == leaf.read_bytes()
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


# -- callers that run `puffin` name the model server, so the launcher never browses for a node -----

def test_a_night_task_names_its_model_server_to_puffin(tmp_path, monkeypatch):
    import time
    from dreamference.night_shift import NightShiftSettings, NightShiftTaskRun
    monkeypatch.setattr(NightShiftTaskRun, "USE_SCOPE", False)
    monkeypatch.delenv("DREAMFERENCE_VLLM_HOST", raising=False)
    task = {"id": "20261002-0100-abc", "repo": str(tmp_path), "base": "0" * 40, "task": "x"}
    output = tmp_path / "out.txt"
    run = NightShiftTaskRun(tmp_path / "night", task, NightShiftSettings({}), "puffin",
                            deadline=time.time() + 30, model_host="http://localhost:8000")
    assert run._run_capped(["bash", "-c", "echo host=$DREAMFERENCE_VLLM_HOST"], tmp_path, output, timeout=20) == 0
    assert output.read_text().strip() == "host=http://localhost:8000"


def test_the_other_unattended_callers_name_it_too():
    # The egress audit counts a DNS query as a failure, and a browse is one on the wire; a
    # SWE-bench container reaches nothing but the model server. Both must stay on tier 1.
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    for path in ("dreamference/audit/egress_audit.py", "dreamference/swe_bench/swe_bench_instance_run.py",
                 "dreamference/runner/codex_runner.py", "dreamference/runner/codex_test_runner.py"):
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
    monkeypatch.setattr("sys.argv", ["puffin-admin", "server", "start", "--no-diffusion"])
    with pytest.raises(SystemExit):
        controller.main()
    assert NodeServiceFile.read()["state"] == "stopped"


# -- Part 2: pairing, and managing a node from another (§12.4, §13.2, §15.1) ------------------------

PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBodyForTestsOnly0000000000000000000 owner@laptop"


def test_the_authorised_line_forces_one_command_and_forbids_the_rest():
    from dreamference.node import NodePairing
    line = NodePairing.authorized_line(PUBLIC_KEY, "/home/u/.local/bin/puffin-admin node serve-job --key abc")
    assert line.startswith('command="/home/u/.local/bin/puffin-admin node serve-job --key abc",')
    for restriction in ("no-pty", "no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding", "no-user-rc"):
        assert restriction in line.split(" ssh-ed25519 ")[0]
    assert line.endswith(" puffin-node")                       # the sender's own comment is not kept
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
    assert NodeServe.authorize(PUBLIC_KEY) is True             # pairing twice does not add twice
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and lines[0] == "ssh-rsa AAAAB3own the-users-own-key"
    tag = NodeServe.key_tag(PUBLIC_KEY)
    assert f"node serve-job --key {tag}\"" in lines[1] and "no-pty" in lines[1]
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert NodeIdentity.read() is not None                     # a machine that accepts pairing is a node
    assert NodeServe.authorize("garbage") is False
    assert NodeServe.unauthorize("0" * 16) is False
    assert NodeServe.unauthorize("not-a-tag") is False
    assert NodeServe.unauthorize(tag) is True
    assert path.read_text() == "ssh-rsa AAAAB3own the-users-own-key\n"


def test_serve_job_refuses_everything_that_is_not_an_operation(monkeypatch, capsys):
    from dreamference.node import NodeServe
    ran = []
    monkeypatch.setattr(NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0))
    for request in (None, "", "bash", "bash -i", "python train.py", "git push", "scp -t /etc",
                    "status; rm -rf ~", "status extra", "set-model", "set-model a b", "start now",
                    "info --all", "'unterminated"):
        assert NodeServe.serve(request) == 2, request
    assert ran == []
    assert "may only ask for Puffin node operations" in capsys.readouterr().err


def test_serve_job_runs_this_nodes_own_puffin_admin(monkeypatch, capsys):
    from dreamference.node import NodeServe
    ran = []
    monkeypatch.setattr(NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0))
    monkeypatch.setattr(NodeServe, "linger", classmethod(lambda cls: False))
    assert NodeServe.serve("status") == 0 and NodeServe.serve("start") == 0 and NodeServe.serve("stop") == 0
    assert ran == [["status"], ["server", "start"], ["server", "stop"]]
    node_id = NodeIdentity.ensure()
    capsys.readouterr()
    assert NodeServe.serve("info") == 0
    info = json.loads(capsys.readouterr().out)
    assert info["node"] == node_id and info["linger"] is False and info["version"]


def test_only_a_key_of_the_nodes_own_matrix_is_loaded(monkeypatch, capsys):
    from dreamference.node import NodeServe
    ran = []
    monkeypatch.setattr(NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 0))
    for bad in ("RadixArk/Qwen3.8-27B-NVFP4", "../etc/passwd", "--model", "no-such-model", "QWEN", "a b"):
        assert NodeServe.serve(f"set-model {bad}") == 2, bad
    assert NodeServe.serve("set-model tiny-a2d-coder-0.5b-diffusion") == 2      # not a main model
    assert ran == []
    assert NodeServe.serve("set-model qwen3.8-27b-nvfp4-dflash2") == 0
    # The node's own commands, in order: its config keeps the assignment, then its own start,
    # with its own host-safety checks.
    assert ran == [["main-model", "set", "qwen3.8-27b-nvfp4-dflash2"], ["server", "stop"], ["server", "start"]]
    ran.clear()
    monkeypatch.setattr(NodeServe, "run_admin", classmethod(lambda cls, arguments: ran.append(arguments) or 3))
    assert NodeServe.serve("set-model qwen3.8-27b-nvfp4-dflash2") == 3          # a refusal comes back as it is
    assert len(ran) == 1


def paired_record(node_id="2222-bbbb", address="192.168.50.106"):
    from dreamference.node import NodePairing
    record = {"node": node_id, "name": "spark-2", "address": address, "user": "owner", "ssh_port": 22}
    NodePairing._save(record)
    return record


def test_every_connection_uses_the_pairing_key_and_the_pinned_host_key(monkeypatch):
    from dreamference.node import NodePairing
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    record = paired_record()
    command = NodePairing.ssh_command(record, "status")
    assert command[0] == "ssh" and command[-2:] == ["owner@192.168.50.106", "status"]
    options = " ".join(command)
    assert f"-i {NodePairing.key_path()}" in options and "IdentitiesOnly=yes" in options
    assert "BatchMode=yes" in options                           # never a password prompt mid-command
    assert "StrictHostKeyChecking=yes" in options and "HostKeyAlias=puffin-node-2222-bbbb" in options
    assert f"UserKnownHostsFile={NodePairing.known_hosts()}" in options
    assert "accept-new" in " ".join(NodePairing.ssh_options(record, accept_new=True))
    for name in ("spark-2", "SPARK-2", "192.168.50.106", "2222-bbbb", "2222"):
        assert NodePairing.find(name)["node"] == "2222-bbbb", name
    assert NodePairing.find("spark-9") is None and NodePairing.find("222") is None


def test_a_paired_node_is_followed_to_its_new_address(monkeypatch):
    from dreamference.node import NodePairing
    paired_record()
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [
        {"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.200", "port": "8000"}]))
    assert NodePairing.find("spark-2")["address"] == "192.168.50.200"
    assert NodePairing.paired()[0]["address"] == "192.168.50.200"


def test_managing_a_node_needs_the_pairing_and_sends_one_operation(monkeypatch, capsys):
    from dreamference.node import NodePairing, NodeRemote
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert NodeRemote.status("spark-2") == 1
    assert "puffin-admin node add spark-2" in capsys.readouterr().out
    paired_record()
    sent = []

    def run(cls, record, request, capture=True, input_text=None):
        sent.append((record["name"], request, capture))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    assert NodeRemote.set_model("spark-2", "qwen3.5-122b-a10b-hybrid-dflash") == 0
    assert NodeRemote.stop("spark-2") == 0 and NodeRemote.start("spark-2") == 0 and NodeRemote.status("spark-2") == 0
    assert sent == [("spark-2", "set-model qwen3.5-122b-a10b-hybrid-dflash", False), ("spark-2", "stop", False),
                    ("spark-2", "start", False), ("spark-2", "status", False)]


def test_pairing_checks_that_the_machine_that_answered_is_the_advertised_node(monkeypatch, capsys):
    from dreamference.node import NodePairing
    advert = {"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.106", "port": "8000"}
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [advert]))
    sent = []
    monkeypatch.setattr(NodePairing, "authorize_on_node",
                        classmethod(lambda cls, record, public_key: sent.append(public_key) or True))
    answers = {"info": json.dumps({"node": "2222-bbbb", "linger": False})}
    monkeypatch.setattr(NodePairing, "run", classmethod(
        lambda cls, record, request, capture=True, input_text=None: subprocess.CompletedProcess([], 0, answers[request], "")))
    assert NodePairing.add("spark-2", user="owner") is True
    assert sent[0].startswith("ssh-ed25519 ") and NodePairing.key_path().is_file()
    assert oct(NodePairing.key_path().stat().st_mode & 0o777) == "0o600"
    assert NodePairing.paired()[0]["node"] == "2222-bbbb"
    assert "loginctl enable-linger" in capsys.readouterr().out     # a job would die with its sender
    # Another machine answering at that address: refused, and nothing is left paired.
    answers["info"] = json.dumps({"node": "9999-other"})
    assert NodePairing.add("spark-2", user="owner") is False
    assert NodePairing.paired() == []
    assert NodePairing.add("spark-9") is False                     # not on the network


def test_the_node_list_shows_what_each_node_serves_without_any_pairing(monkeypatch):
    from dreamference.night_shift import NightShiftHost
    from dreamference.node import NodeRemote
    mine = NodeIdentity.ensure()
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [
        {"name": "spark-1", "node": mine, "address": "192.168.50.105", "port": "8000", "main": "1", "version": "1.3.0", "state": "ready"},
        {"name": "spark-2", "node": "2222-bbbb", "address": "192.168.50.106", "port": "8000", "version": "1.3.0", "state": "stopped"}]))
    monkeypatch.setattr(NightShiftHost, "served_model", classmethod(
        lambda cls, host, timeout=3.0: ("RadixArk/Qwen3.8-27B-NVFP4", 262144) if "105" in host else None))
    monkeypatch.setattr(NightShiftHost, "metrics", classmethod(
        lambda cls, host, timeout=3.0: {"running": 2.0, "served": 1.0, "kv_pool": 156907.0}))
    lines = NodeRemote.list_lines()
    assert lines[0] == ("spark-1  http://192.168.50.105:8000/v1  RadixArk/Qwen3.8-27B-NVFP4 (262144 tokens), "
                        "2 request(s) running, KV pool 156907 tokens  Puffin 1.3.0  (this machine)")
    assert lines[1] == ("spark-2  http://192.168.50.106:8000/v1  model server stopped  Puffin 1.3.0  "
                        "(not paired: `puffin-admin node add spark-2` to manage it; not a coding model)")
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert "No Puffin node answers" in NodeRemote.list_lines()[0]
