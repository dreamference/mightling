"""`ling-admin matrix` (specs/DREAMFERENCE_MIGHTLING_CHAT.md §5) with Docker, systemd, Tailscale and the
homeserver replaced: `_run` and `_http` are the module's only ways out, and both are stand-ins here."""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import pytest

from dreamference.chat import matrix_homeserver as mh
from dreamference.chat.matrix_homeserver import MatrixHomeserver


class Machine:
    """A scripted Docker, systemd, Tailscale and tuwunel."""

    def __init__(self, tailnet_name: str = "node.tail1234.ts.net") -> None:
        self.tailnet_name = tailnet_name
        self.runs: List[List[str]] = []
        self.http: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
        self.network = False
        self.container = False
        self.registered: List[str] = []

    def run(self, argv: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        self.runs.append(argv)
        out, code = "", 0
        if argv[:3] == ["tailscale", "status", "--json"]:
            if self.tailnet_name:
                out = json.dumps({"BackendState": "Running", "Self": {"DNSName": self.tailnet_name + "."}})
            else:
                code = 1
        elif argv[:4] == ["docker", "network", "inspect", mh.MATRIX_NETWORK]:
            code = 0 if self.network else 1
        elif argv[:3] == ["docker", "network", "create"]:
            self.network = True
        elif argv[:2] == ["docker", "inspect"] and argv[-1] == mh.MATRIX_CONTAINER:
            if not self.container:
                code = 1
            elif "-f" in argv and "IPAddress" in argv[3]:
                out = mh.MATRIX_ADDRESS + " "
            elif "-f" in argv:
                out = "true"
        elif argv[:2] == ["docker", "run"]:
            self.container = True
        elif argv[:3] == ["docker", "rm", "-f"]:
            self.container = False
        return subprocess.CompletedProcess(argv, code, out, "")

    def request(self, method: str, url: str, body: Optional[Dict[str, Any]] = None,
                token: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        self.http.append((method, url, body))
        assert url.startswith(mh.PROXY_URL), url
        if url.endswith("/versions"):
            return 200, {"versions": ["v1.11"]}
        if url.endswith("/whoami"):
            return (200, {"user_id": f"@mightling:{self.tailnet_name}"}) if token == "bot-token" else (401, {})
        if url.endswith("/register"):
            auth = (body or {}).get("auth")
            if not auth:
                return 401, {"session": "s1", "flows": [{"stages": ["m.login.registration_token"]}], "completed": []}
            assert auth["type"] == "m.login.registration_token" and auth["session"] == "s1"
            if auth["token"] != json.load(open(MatrixHomeserver._path("matrix-admin.json")))["registration_token"]:
                return 401, {"errcode": "M_FORBIDDEN"}
            localpart = body["username"]
            self.registered.append(localpart)
            token = "bot-token" if localpart == mh.BOT_LOCALPART else f"{localpart}-token"
            return 200, {"user_id": f"@{localpart}:{self.tailnet_name}", "access_token": token}
        return 404, {}

    def argv(self, *prefix: str) -> List[List[str]]:
        return [argv for argv in self.runs if argv[: len(prefix)] == list(prefix)]


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    fake = Machine()
    monkeypatch.setattr(MatrixHomeserver, "_run", classmethod(lambda cls, argv, timeout=120: fake.run(argv, timeout)))
    monkeypatch.setattr(MatrixHomeserver, "_http",
                        classmethod(lambda cls, method, url, body=None, token=None: fake.request(method, url, body, token)))
    monkeypatch.setattr("dreamference.node.node_identity.NodeIdentity.read", classmethod(lambda cls: "node-1"))
    monkeypatch.setattr(mh.time, "sleep", lambda seconds: None)
    # The sidecar network is ensured with docker calls of its own (not `_run`): on a machine that has
    # the network it is a read, on CI it is `docker network create`, which conftest refuses (2026-10-10).
    monkeypatch.setattr("dreamference.chat.sidecar_network.SidecarNetwork.ensure", classmethod(lambda cls: True))
    return fake


def test_start_builds_a_homeserver_with_no_route_out_and_a_bot_account(machine, capsys):
    assert MatrixHomeserver.start() == 0
    created = machine.argv("docker", "network", "create")[0]
    assert "--internal" in created and mh.MATRIX_SUBNET in created
    run = machine.argv("docker", "run")[0]
    assert run[run.index("--network") + 1] == mh.MATRIX_NETWORK
    assert run[run.index("--ip") + 1] == mh.MATRIX_ADDRESS
    assert "--memory=1g" in run and "--memory-swap=1g" in run
    env = [run[i + 1] for i, arg in enumerate(run) if arg == "-e"]
    assert "TUWUNEL_SERVER_NAME=node.tail1234.ts.net" in env
    assert "TUWUNEL_ALLOW_FEDERATION=false" in env
    assert any(item.startswith("TUWUNEL_REGISTRATION_TOKEN=") and len(item) > 40 for item in env)
    assert run[-1] == mh.MATRIX_IMAGE and "@sha256:" in mh.MATRIX_IMAGE
    # The loopback proxy points at the container.
    service = open(os.path.join(MatrixHomeserver.unit_dir(), mh.PROXY_SERVICE_UNIT)).read()
    assert f"ExecStart={mh.SOCKET_PROXYD} {mh.MATRIX_ADDRESS}:{mh.MATRIX_PORT}" in service
    socket = open(os.path.join(MatrixHomeserver.unit_dir(), mh.PROXY_SOCKET_UNIT)).read()
    assert "ListenStream=127.0.0.1:6167" in socket
    # tailscale serve fronts the proxy, never the container's address.
    assert machine.argv("tailscale", "serve")[0] == ["tailscale", "serve", "--bg", "--https=443", mh.PROXY_URL]
    config = json.load(open(MatrixHomeserver._path("matrix.json")))
    assert config == {"user_id": "@mightling:node.tail1234.ts.net", "access_token": "bot-token",
                      "homeserver": mh.PROXY_URL, "server_name": "node.tail1234.ts.net", "allowed": []}
    assert os.stat(MatrixHomeserver._path("matrix.json")).st_mode & 0o777 == 0o600
    assert os.stat(MatrixHomeserver.chat_dir()).st_mode & 0o777 == 0o700
    assert "add-user" in capsys.readouterr().out


def test_a_second_start_reuses_the_container_and_the_bot(machine):
    assert MatrixHomeserver.start() == 0
    assert MatrixHomeserver.start() == 0
    assert len(machine.argv("docker", "run")) == 1
    assert machine.argv("docker", "start", mh.MATRIX_CONTAINER)
    assert machine.registered == [mh.BOT_LOCALPART]


def test_start_refuses_without_a_node_or_without_tailscale(machine, monkeypatch, capsys):
    machine.tailnet_name = ""
    assert MatrixHomeserver.start() == 1
    assert "tailscale up" in capsys.readouterr().out
    assert not machine.argv("docker", "run")
    monkeypatch.setattr("dreamference.node.node_identity.NodeIdentity.read", classmethod(lambda cls: None))
    machine.tailnet_name = "node.tail1234.ts.net"
    assert MatrixHomeserver.start() == 1
    assert "node" in capsys.readouterr().out
    assert not machine.argv("docker", "run")


def test_the_server_name_is_chosen_once_and_a_renamed_machine_is_refused(machine, capsys):
    assert MatrixHomeserver.start() == 0
    machine.tailnet_name = "renamed.tail1234.ts.net"
    machine.runs.clear()
    capsys.readouterr()
    assert MatrixHomeserver.start() == 1
    out = capsys.readouterr().out
    assert "node.tail1234.ts.net" in out and "renamed.tail1234.ts.net" in out
    assert not machine.argv("docker")


def test_add_user_prints_the_password_once_and_stores_none(machine, capsys):
    assert MatrixHomeserver.start() == 0
    capsys.readouterr()
    assert MatrixHomeserver.add_user("owner") == 0
    out = capsys.readouterr().out
    register = [body for method, url, body in machine.http if url.endswith("/register") and body and body.get("username") == "owner"]
    password = register[-1]["password"]
    assert password in out
    for name in os.listdir(MatrixHomeserver.chat_dir()):
        assert password not in open(MatrixHomeserver._path(name)).read(), name
    assert json.load(open(MatrixHomeserver._path("matrix.json")))["allowed"] == ["@owner:node.tail1234.ts.net"]
    assert MatrixHomeserver.add_user("Bad Name") == 2


def test_push_moves_the_container_to_the_sidecar_network(machine, capsys):
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.set_push(True) == 0
    assert machine.argv("docker", "rm", "-f", mh.MATRIX_CONTAINER)
    run = machine.argv("docker", "run")[0]
    assert run[run.index("--network") + 1] == "dreamference-sidecars"
    assert "--ip" not in run
    assert "push" in capsys.readouterr().out.lower()


def test_remove_needs_yes_and_then_deletes_everything(machine):
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.remove(False) == 1
    assert not machine.runs
    assert MatrixHomeserver.remove(True) == 0
    assert machine.argv("docker", "volume", "rm", mh.MATRIX_VOLUME)
    assert machine.argv("docker", "network", "rm", mh.MATRIX_NETWORK)
    assert not os.path.exists(MatrixHomeserver._path("matrix.json"))
    assert not os.path.exists(MatrixHomeserver._path("matrix-admin.json"))


def test_stop_turns_it_off_until_the_next_start(machine):
    # Off means off across a reboot: the socket is disabled, not only stopped, and the tailnet no
    # longer offers the name. The accounts stay.
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.stop() == 0
    assert machine.argv("tailscale", "serve", "--https=443", "off")
    assert machine.argv("systemctl", "--user", "disable", "--now", mh.PROXY_SOCKET_UNIT)
    assert machine.argv("docker", "stop", mh.MATRIX_CONTAINER)
    assert not any("volume" in argv for argv in machine.runs)
    assert os.path.exists(MatrixHomeserver._path("matrix-admin.json"))


def test_the_cli_routes_matrix_commands(machine, monkeypatch):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    called = []
    monkeypatch.setattr(MatrixHomeserver, "add_user", classmethod(lambda cls, name: called.append(name) or 0))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["matrix", "add-user", "owner"])
    assert exit_info.value.code == 0
    assert called == ["owner"]
