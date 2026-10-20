"""Remote access, the node's half (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3, §4, §13 Phase 1).

Offline by construction (§9, item 5): every command goes through `RemoteExecutor.run`, which a
fake replaces, and every call to management through `RemoteManagementApi._request`. The CA tests
run the real `openssl` on files under the test's own home folder, which reaches nothing. The
enrolment listener binds loopback on a port the kernel picks.
"""

import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from dreamference.cli.remote_command import RemoteCommand
from dreamference.remote import (
    NetBirdPins,
    RemoteAccess,
    RemoteCA,
    RemoteControlPlane,
    RemoteEnrolment,
    RemoteEnrolmentServer,
    RemoteExecutor,
    RemoteManagementApi,
    RemoteNodePeer,
    RemotePeers,
    RemoteState,
    RemoteStep,
    RemoteTunnel,
)
from dreamference.remote import remote_access as remote_access_module
from dreamference.remote.netbird_pins import NETBIRD_CLIENT_SHA256, NETBIRD_SERVER_IMAGE
from dreamference.vllm_server.model_gate import ModelGate
from dreamference.vllm_server.model_gate_service import NODE_ID_FILE, ModelGateService

NODE_ID = "4b2f8a1e-9c3d-4e5f-8a7b-1c2d3e4f5a6b"
SHA256_HEX = 64


class FakeExecutor:
    """Records every command and answers from a table of (argv prefix → result)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.answers: list[tuple[tuple[str, ...], subprocess.CompletedProcess]] = []

    def answer(self, prefix: tuple[str, ...], code: int = 0, stdout: str = "") -> None:
        self.answers.insert(
            0, (prefix, subprocess.CompletedProcess(list(prefix), code, stdout, ""))
        )

    def run(self, argv, stdin=None, timeout=120):
        self.calls.append(list(argv))
        self.inputs.append(stdin)
        for prefix, result in self.answers:
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return subprocess.CompletedProcess(argv, 0, "", "")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [call for call in self.calls if tuple(call[: len(prefix)]) == prefix]


@pytest.fixture
def executor(monkeypatch):
    fake = FakeExecutor()
    fake.answer(("uname", "-s"), stdout="Linux\n")
    fake.answer(("uname", "-m"), stdout="aarch64\n")
    fake.answer(("/usr/local/bin/netbird", "version"), code=127)
    fake.answer(("systemctl", "show", "-p", "LoadState"), stdout="not-found\n")
    monkeypatch.setattr(RemoteExecutor, "run", classmethod(lambda cls, *a, **k: fake.run(*a, **k)))
    return fake


@pytest.fixture
def node(monkeypatch):
    path = Path.home() / ".config" / "dreamference" / "node-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NODE_ID + "\n")
    monkeypatch.setattr(RemoteAccess, "hostname", classmethod(lambda cls: "gx10-9428"))
    return NODE_ID


class FakeManagement:
    """Management's REST API as a dict of answers, recording every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, object, str]] = []
        self.peers = [
            {"id": "p-node", "name": "gx10-9428", "hostname": "gx10-9428", "dns_label": "gx10-9428",
             "connected": True, "os": "Ubuntu 24.04"},
            {"id": "p-laptop", "name": "lg-gram", "hostname": "lg-gram", "dns_label": "lg-gram",
             "connected": False, "last_seen": "2026-10-20T08:00:00Z", "os": "Ubuntu 26.04"},
        ]  # fmt: skip
        self.owner_exists = False

    def request(self, method, path, body=None, token=""):
        self.requests.append((method, path, body, token))
        route = (method, path.split("/")[1] if "/" in path else path)
        answers = {
            ("GET", "users"): (401, {}),
            ("GET", "peers"): (200, self.peers),
            ("GET", "accounts"): (200, [{"settings": {"dns_domain": ""}}]),
            ("POST", "setup-keys"): (200, {"id": "k1", "key": "SETUP-KEY-1"}),
            ("DELETE", "setup-keys"): (200, {}),
            ("DELETE", "peers"): (200, {}),
        }
        if route == ("POST", "setup"):
            if self.owner_exists:
                return 412, {"message": "setup already completed"}
            self.owner_exists = True
            return 200, {
                "user_id": "u1",
                "email": body["email"],
                "personal_access_token": "nbp_first",
            }
        return answers.get(route, (404, {}))

    def calls(self, method: str, path: str) -> list:
        return [r for r in self.requests if r[0] == method and r[1] == path]


@pytest.fixture
def management(monkeypatch):
    fake = FakeManagement()
    monkeypatch.setattr(
        RemoteManagementApi, "_request", classmethod(lambda cls, *a, **k: fake.request(*a, **k))
    )
    monkeypatch.setattr(remote_access_module, "START_WAIT_S", 0.01)
    monkeypatch.setattr(RemoteManagementApi, "answering", classmethod(lambda cls: True))
    return fake


def said() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


# -- pins ----------------------------------------------------------------------------------------


def test_every_pin_is_a_sha256_and_names_the_release():
    for system, machine in NETBIRD_CLIENT_SHA256:
        url, sha = NetBirdPins.client_archive(system.capitalize(), machine)
        assert url.startswith(
            "https://github.com/netbirdio/netbird/releases/download/v0.81.3/netbird_0.81.3_"
        )
        assert len(sha) == SHA256_HEX and int(sha, 16) >= 0
    assert NetBirdPins.client_archive("Linux", "aarch64")[0].endswith(
        "netbird_0.81.3_linux_arm64.tar.gz"
    )
    assert NetBirdPins.client_archive("Darwin", "arm64")[0].endswith(
        "netbird_0.81.3_darwin_arm64.tar.gz"
    )
    assert NetBirdPins.client_archive("Linux", "riscv64") is None
    assert NETBIRD_SERVER_IMAGE.startswith("netbirdio/netbird-server:0.81.3@sha256:")
    assert NetBirdPins.relay_image("arm64").startswith("netbirdio/relay@sha256:f5f7bba0")
    assert NetBirdPins.relay_image("s390x") is None
    url, sha = NetBirdPins.gost_archive("amd64")
    assert url.endswith("/v3.3.0/gost_3.3.0_linux_amd64.tar.gz") and len(sha) == SHA256_HEX


def test_a_step_shows_sudo_and_never_its_secret_input():
    step = RemoteStep.sudo("Join", "netbird", "up", "--hostname", "a b", stdin="KEY", secret=True)
    assert step.command() == ["sudo", "netbird", "up", "--hostname", "a b"]
    assert step.display() == "sudo netbird up --hostname 'a b'   (input withheld)"
    assert "KEY" not in step.display() and "KEY" not in repr(step)
    assert RemoteStep.user("Look", "ls").command() == ["ls"]


# -- the CA, with the real openssl ---------------------------------------------------------------

needs_openssl = pytest.mark.skipif(subprocess.run(["which", "openssl"], capture_output=True).returncode != 0,
                                   reason="openssl is not installed")  # fmt: skip


def test_the_scope_is_the_box_domain_and_the_overlay():
    assert RemoteCA.default_scope("nb.example.org") == "example.org"
    assert RemoteCA.default_scope("example.org") == "example.org"
    assert RemoteCA.in_scope("relay.nb.example.org", "example.org")
    assert RemoteCA.in_scope("gx10-9428.netbird.selfhosted", "example.org")
    assert RemoteCA.in_scope("localhost", "example.org")
    assert not RemoteCA.in_scope("bank.com", "example.org")
    assert not RemoteCA.in_scope("notexample.org", "example.org")


@needs_openssl
def test_the_ca_signs_names_in_its_scope_and_openssl_refuses_others(tmp_path):
    assert RemoteCA.create("example.org", "gx10-9428")
    assert oct(RemoteCA.key_path().stat().st_mode & 0o777) == "0o600"
    assert oct(RemoteState.ca_dir().stat().st_mode & 0o777) == "0o700"
    out = tmp_path / "certs"
    assert RemoteCA.issue("management", ["nb.example.org", "localhost"], out)
    assert oct((out / "management.key").stat().st_mode & 0o777) == "0o600"
    assert not (out / "management.csr").exists() and not (out / "management.ext").exists()
    verify = ["openssl", "verify", "-CAfile", str(RemoteCA.cert_path())]
    assert (
        subprocess.run([*verify, str(out / "management.crt")], capture_output=True).returncode == 0
    )
    text = subprocess.run(["openssl", "x509", "-noout", "-text", "-in", str(out / "management.crt")],
                          capture_output=True, text=True).stdout  # fmt: skip
    assert (
        "DNS:nb.example.org" in text
        and "DNS:localhost" in text
        and "TLS Web Server Authentication" in text
    )
    # A name outside the scope: openssl itself refuses the chain, as Go's TLS would.
    assert RemoteCA.issue("evil", ["bank.com"], out)
    refused = subprocess.run([*verify, str(out / "evil.crt")], capture_output=True, text=True)
    assert (
        refused.returncode != 0 and "permitted subtree violation" in refused.stdout + refused.stderr
    )
    assert 395 <= RemoteCA.days_left(out / "management.crt") <= 397
    assert 3640 <= RemoteCA.days_left(RemoteCA.cert_path()) <= 3650
    assert not RemoteCA.needs_renewal(out / "management.crt")
    assert RemoteCA.needs_renewal(out / "missing.crt")
    assert RemoteCA.fingerprint().count(":") == 31
    # Made once: a second call keeps the same CA.
    pem = RemoteCA.pem()
    assert RemoteCA.create("other.org", "x") and RemoteCA.pem() == pem


# -- the control plane ---------------------------------------------------------------------------


def test_the_server_configuration_points_at_the_box_and_sends_nothing_home():
    text = RemoteControlPlane.render_config(
        "nb.example.org", "relay.nb.example.org", 'se"cret', "S0RLRVk="
    )
    server = yaml.safe_load(text)["server"]
    assert server["listenAddress"] == ":33073"
    assert server["exposedAddress"] == "https://nb.example.org:443"
    assert server["relays"] == {"addresses": ["rels://relay.nb.example.org:443"], "secret": 'se"cret',
                                "credentialsTTL": "12h"}  # fmt: skip
    assert server["stuns"] == [{"uri": "stun:relay.nb.example.org:3478"}]
    assert server["disableAnonymousMetrics"] is True and server["disableGeoliteUpdate"] is True
    assert server["store"] == {"engine": "sqlite", "encryptionKey": "S0RLRVk="}
    assert server["tls"]["certFile"] == "/etc/netbird/tls/management.crt"
    assert server["auth"]["issuer"] == "https://nb.example.org:443/oauth2"


def test_the_container_is_pinned_capped_internal_and_publishes_nothing():
    argv = RemoteControlPlane.run_command(setup_pat=False)
    assert argv[-1] == NETBIRD_SERVER_IMAGE
    assert argv[argv.index("--network") : argv.index("--network") + 2] == [
        "--network",
        "dreamference-remote",
    ]
    assert "--memory=512m" in argv and "--memory-swap=512m" in argv
    assert "-p" not in argv and "--publish" not in argv and "host" not in argv
    assert f"{RemoteState.server_dir()}:/etc/netbird:ro" in argv
    assert "NB_SETUP_PAT_ENABLED=true" not in argv
    assert "NB_SETUP_PAT_ENABLED=true" in RemoteControlPlane.run_command(setup_pat=True)
    units = RemoteControlPlane.proxy_units()
    assert "ListenStream=127.0.0.1:33073" in units["mightling-remote-control.socket"]
    assert "systemd-socket-proxyd 172.31.232.10:33073" in units["mightling-remote-control.service"]


def test_the_network_is_made_internal(executor):
    executor.answer(("docker", "network", "inspect"), code=1)
    assert RemoteControlPlane.ensure_network()
    assert executor.ran("docker", "network", "create") == [
        [
            "docker",
            "network",
            "create",
            "--internal",
            "--subnet",
            "172.31.232.0/24",
            "dreamference-remote",
        ]
    ]


# -- the tunnel ----------------------------------------------------------------------------------


def test_the_tunnel_forwards_one_port_with_its_own_key_and_a_pinned_host_key(executor):
    argv = RemoteTunnel.ssh_command("nb.example.org")
    assert argv[0] == "/usr/bin/ssh" and argv[-1] == "mightling-tunnel@nb.example.org"
    assert argv[argv.index("-R") + 1] == "127.0.0.1:10443:127.0.0.1:33073"
    assert argv[argv.index("-i") + 1] == str(RemoteState.tunnel_key())
    for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "ExitOnForwardFailure=yes",
                   f"UserKnownHostsFile={RemoteState.known_hosts()}", "IdentitiesOnly=yes"):  # fmt: skip
        assert option in argv
    unit = RemoteTunnel.unit_text("nb.example.org")
    assert "Restart=always" in unit and "WantedBy=default.target" in unit
    RemoteState.ensure_dir(RemoteState.base_dir())
    Path(f"{RemoteState.tunnel_key()}.pub").write_text(
        "ssh-ed25519 AAAAC3 mightling-remote-tunnel\n"
    )
    line = RemoteTunnel.authorized_line()
    assert line.startswith(
        'restrict,port-forwarding,permitlisten="127.0.0.1:10443",command="/bin/false" ssh-ed25519 '
    )
    assert RemoteTunnel.install("nb.example.org")
    assert RemoteTunnel.unit_path().read_text() == unit
    assert executor.ran("systemctl", "--user", "enable") == [
        ["systemctl", "--user", "enable", "mightling-remote-tunnel.service"]
    ]


# -- management's API ----------------------------------------------------------------------------


def test_setup_keys_are_one_off_not_ephemeral_and_the_owner_is_made_once(management):
    created = RemoteManagementApi.create_owner("owner@nb.example.org", "Mightling node", "pw")
    assert created["personal_access_token"] == "nbp_first"
    assert management.calls("POST", "/setup")[0][2]["pat_expire_in"] == 365
    assert RemoteManagementApi.create_owner("owner@nb.example.org", "Mightling node", "pw") is None
    key = RemoteManagementApi.create_setup_key("nbp_first", "laptop")
    assert key == {"id": "k1", "key": "SETUP-KEY-1"}
    body = management.calls("POST", "/setup-keys")[0][2]
    assert body["type"] == "one-off" and body["usage_limit"] == 1 and body["ephemeral"] is False
    assert body["expires_in"] == 86400
    assert management.calls("POST", "/setup-keys")[0][3] == "nbp_first"
    assert RemoteManagementApi.dns_domain("nbp_first") == "netbird.selfhosted"


def test_rotating_the_token_deletes_the_others(monkeypatch):
    seen = []

    def request(method, path, body=None, token=""):
        seen.append((method, path, token))
        if method == "POST":
            return 200, {"plain_token": "nbp_new", "personal_access_token": {"id": "t2"}}
        if method == "GET":
            return 200, [{"id": "t1"}, {"id": "t2"}]
        return 200, {}

    monkeypatch.setattr(
        RemoteManagementApi, "_request", classmethod(lambda cls, *a, **k: request(*a, **k))
    )
    assert RemoteManagementApi.rotate_token("u1", "nbp_old", "ling-admin") == ("nbp_new", "t2")
    assert ("DELETE", "/users/u1/tokens/t1", "nbp_new") in seen
    assert not any(path.endswith("/t2") for method, path, _ in seen if method == "DELETE")


# -- enrolment on the LAN ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "lan"),
    [("192.168.1.20", True), ("10.0.0.5", True), ("172.20.1.1", True), ("fe80::1%wlan0", True),
     ("fd12::5", True), ("::ffff:192.168.1.20", True), ("100.101.5.6", False), ("127.0.0.1", False),
     ("::1", False), ("8.8.8.8", False), ("not an address", False)],
)  # fmt: skip
def test_only_the_local_network_may_enrol(address, lan):
    assert RemoteEnrolment.lan_peer(address) is lan


def test_a_code_works_once_and_ten_wrong_ones_withdraw_it():
    window = RemoteEnrolment({"setup_key": "K"}, code="01234567", now=0.0)
    assert window.handle("100.70.0.2", b'{"code": "01234567"}', now=1.0)[0] == 403
    status, answer = window.handle("192.168.1.20", b'{"code": "01234568"}', now=1.0)
    assert status == 403 and answer["attempts_left"] == 9
    status, answer = window.handle("192.168.1.20", b'{"code": " 01234567 "}', now=2.0)
    assert status == 200 and answer == {"version": 1, "setup_key": "K"}
    assert window.handle("192.168.1.20", b'{"code": "01234567"}', now=3.0)[0] == 410
    other = RemoteEnrolment({}, code="11111111", now=0.0)
    for _ in range(9):
        assert other.handle("10.0.0.5", b'{"code": "00000000"}', now=1.0)[0] == 403
    assert other.handle("10.0.0.5", b'{"code": "00000000"}', now=1.0)[0] == 410
    assert other.withdrawn and other.handle("10.0.0.5", b'{"code": "11111111"}', now=1.0)[0] == 410
    late = RemoteEnrolment({}, code="22222222", now=0.0)
    assert late.handle("10.0.0.5", b'{"code": "22222222"}', now=601.0)[0] == 410
    assert len(RemoteEnrolment.new_code()) == 8 and RemoteEnrolment.new_code().isdigit()


def _post(port: int, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mightling-remote/join",
                                     data=json.dumps(body).encode(), method="POST")  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_listener_serves_one_bundle_then_closes(monkeypatch):
    monkeypatch.setattr(RemoteEnrolment, "lan_peer", classmethod(lambda cls, address: True))
    events: list[str] = []
    server = RemoteEnrolmentServer(("127.0.0.1", 0), RemoteEnrolment({"setup_key": "K"}, code="12345678"),
                                   events.append)  # fmt: skip
    port = server.server_address[1]
    done: list[bool] = []
    thread = threading.Thread(target=lambda: done.append(server.serve_window()))
    thread.start()
    assert _post(port, {"code": "00000000"})[0] == 403
    status, answer = _post(port, {"code": "12345678"})
    thread.join(timeout=10)
    assert status == 200 and answer["setup_key"] == "K"
    assert done == [True] and events[-1] == "127.0.0.1: bundle sent"


# -- setup, status and remove --------------------------------------------------------------------


def test_names_are_dns_names_never_addresses():
    assert "address" in RemoteAccess.name_problem("203.0.113.7", "relay.example.org")
    assert "address" in RemoteAccess.name_problem("nb.example.org", "2001:db8::1")
    assert "not a DNS name" in RemoteAccess.name_problem("localhost", "relay.localhost")
    assert "name of its own" in RemoteAccess.name_problem("nb.example.org", "nb.example.org")
    assert RemoteAccess.name_problem("nb.example.org", "relay.nb.example.org") == ""


def test_setup_prints_its_plan_and_changes_nothing_until_the_box_half_exists(executor, node):
    lines, say = said()
    assert RemoteAccess.setup("NB.example.org.", "", False, True, say, lambda q: True) == 0
    text = "\n".join(lines)
    assert "nb.example.org (control plane) and relay.nb.example.org (relay)" in text
    assert "sudo install -m 0755" in text and "sudo update-ca-certificates" in text
    assert "sudo /usr/local/bin/netbird service install" in text
    # The user-side download and check are steps, never shown as sudo.
    assert "sudo curl" not in text and "sudo sh -c" not in text
    lines.clear()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 1
    assert "not built yet" in lines[-1] and "nothing was changed" in lines[-1]
    assert not (RemoteState.base_dir() / "remote.json").exists()
    assert (
        not executor.ran("docker")
        and not executor.ran("sudo")
        and not executor.ran("systemctl", "--user")
    )


def test_setup_refuses_off_a_node_and_with_an_address(executor):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 1
    assert "run by a node" in lines[-1]
    assert RemoteAccess.setup("198.51.100.4", "", True, False, say, lambda q: True) == 2


@pytest.fixture
def built(monkeypatch, executor, management, node):
    """The box's half stood in for, and openssl's two certificate steps replaced."""
    monkeypatch.setattr(remote_access_module, "BOX_HALF_BUILT", True)
    issued: list[tuple[str, list[str]]] = []

    def issue(cls, name, sans, folder):
        RemoteState.ensure_dir(folder)
        (folder / f"{name}.crt").write_text("cert")
        issued.append((name, sans))
        return True

    monkeypatch.setattr(RemoteCA, "create", classmethod(lambda cls, scope, label: True))
    monkeypatch.setattr(RemoteCA, "exists", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteCA, "issue", classmethod(issue))
    monkeypatch.setattr(
        RemoteCA, "needs_renewal", classmethod(lambda cls, cert, now=None: not cert.exists())
    )
    monkeypatch.setattr(RemoteTunnel, "ensure_key", classmethod(lambda cls: True))
    return issued


def test_setup_builds_the_node_half_in_order_and_remembers_no_address(built, executor, management):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0, lines
    state = RemoteState.load()
    assert state["dns_name"] == "nb.example.org" and state["relay_name"] == "relay.nb.example.org"
    assert state["ca_scope"] == "example.org"
    assert (
        state["node_peer"]["dns_label"] == "gx10-9428"
        and state["node_peer"]["management"] == "nb.example.org"
    )
    assert RemoteAccess.overlay_name(state) == "gx10-9428.netbird.selfhosted"
    assert state["owner"]["token"] == "nbp_first" and len(state["relay_secret"]) > 30
    assert built == [
        ("management", ["nb.example.org", "localhost"]),
        ("relay", ["relay.nb.example.org"]),
    ]
    # The container ran first with the setup token allowed, then again without it.
    runs = executor.ran("docker", "run")
    assert (
        len(runs) == 2
        and "NB_SETUP_PAT_ENABLED=true" in runs[0]
        and "NB_SETUP_PAT_ENABLED=true" not in runs[1]
    )
    up = executor.ran("sudo", "/usr/local/bin/netbird", "up")
    assert up and "--setup-key-file" in up[0] and "SETUP-KEY-1" not in " ".join(up[0])
    assert not (RemoteState.base_dir() / "node-setup-key").exists()
    assert RemoteTunnel.unit_path().is_file()
    assert oct((RemoteState.base_dir() / "remote.json").stat().st_mode & 0o777) == "0o600"
    # No address anywhere in what is remembered.
    assert re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", json.dumps(state)) is None
    assert "✅" in lines[-3]


def test_a_second_setup_reconciles_and_touches_nothing_it_need_not(built, executor, management):
    _lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    built.clear()
    executor.calls.clear()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    assert built == []
    assert len(management.calls("POST", "/setup")) == 1
    assert not executor.ran("sudo", "/usr/local/bin/netbird", "up")
    assert (
        len(executor.ran("docker", "run")) == 1
        and "NB_SETUP_PAT_ENABLED=true" not in executor.ran("docker", "run")[0]
    )


def test_another_box_needs_remove_first_and_one_outside_the_scope_a_new_ca(built):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    assert RemoteAccess.setup("box2.example.org", "", True, False, say, lambda q: True) == 1
    assert "remote remove" in lines[-1]
    state = RemoteState.load()
    state.pop("dns_name")
    RemoteState.save(state)
    assert RemoteAccess.setup("nb.other.net", "", True, False, say, lambda q: True) == 1
    assert "under example.org only" in lines[-1] and "--purge" in lines[-1]


def test_a_token_near_expiry_is_rotated(built, management, monkeypatch):
    _lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    monkeypatch.setattr(
        RemoteManagementApi, "rotate_token", classmethod(lambda cls, u, t, n: ("nbp_2", "t2"))
    )
    state = RemoteState.load()
    state["owner"]["token_expires"] = time.time() + 5 * 86400
    assert RemoteAccess.stage_owner(state, say)
    assert (
        state["owner"]["token"] == "nbp_2"
        and state["owner"]["token_expires"] > time.time() + 300 * 86400
    )


def test_status_and_remove_keep_the_ca_and_the_peers(built, executor, management):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    executor.answer(("docker", "inspect", "-f"), stdout="true\n")
    lines.clear()
    assert RemoteAccess.status(say) == 0
    text = "\n".join(lines)
    assert "Box:            nb.example.org" in text and "gx10-9428.netbird.selfhosted" in text
    assert "2 enrolled, 1 connected now" in text and "expires in 36" in text
    lines.clear()
    assert RemoteAccess.remove(False, False, say, lambda q: False) == 1
    assert RemoteState.load()["dns_name"] == "nb.example.org"
    assert RemoteAccess.remove(False, True, say, lambda q: False) == 0
    state = RemoteState.load()
    assert (
        "dns_name" not in state
        and "relay_secret" not in state
        and state["node_peer"]["id"] == "p-node"
    )
    assert not RemoteTunnel.unit_path().exists() and not executor.ran(
        "sudo", "/usr/local/bin/netbird", "down"
    )


def test_purge_takes_the_node_off_the_overlay_and_deletes_the_folder(built, executor):
    _lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    assert RemoteAccess.remove(True, True, say, lambda q: False) == 0
    assert executor.ran("sudo", "/usr/local/bin/netbird", "down")
    assert executor.ran(
        "sudo", "rm", "-f", "/usr/local/share/ca-certificates/mightling-node-ca.crt"
    )
    assert executor.ran("docker", "volume", "rm", "dreamference-remote-data")
    assert not RemoteState.base_dir().exists()


# -- peers, codes and revocation -----------------------------------------------------------------


def test_peers_lists_and_revoke_refuses_the_node_itself(built, executor, management):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    executor.answer(("docker", "inspect", "-f"), stdout="true\n")
    lines.clear()
    assert RemotePeers.peers(say) == 0
    assert any("gx10-9428.netbird.selfhosted" in line and "(this node)" in line for line in lines)
    assert any("lg-gram" in line and "last seen 2026-10-20" in line for line in lines)
    assert RemotePeers.revoke("gx10-9428", say) == 2 and "this node" in lines[-1]
    assert RemotePeers.revoke("nobody", say) == 2
    assert RemotePeers.revoke("lg-gram.netbird.selfhosted", say) == 0
    assert management.calls("DELETE", "/peers/p-laptop")
    assert RemotePeers.match(management.peers, "p-l") == [] and RemotePeers.match(
        management.peers, "p-la"
    )


def test_an_unused_code_withdraws_its_key(built, executor, management, monkeypatch):
    lines, say = said()
    assert RemoteAccess.setup("nb.example.org", "", True, False, say, lambda q: True) == 0
    executor.answer(("docker", "inspect", "-f"), stdout="true\n")
    monkeypatch.setattr(RemoteEnrolmentServer, "serve_window", lambda self: False)
    lines.clear()
    assert RemotePeers.code(say, port=0) == 1
    assert lines[0].startswith("Enrolment code: ") and "ling node remote join --code" in lines[2]
    assert management.calls("DELETE", "/setup-keys/k1")
    bundle = RemotePeers.bundle(RemoteState.load(), "K")
    assert bundle["management_url"] == "https://nb.example.org:443"
    assert bundle["relay"] == "rels://relay.nb.example.org:443"
    assert bundle["remote_name"] == "gx10-9428.netbird.selfhosted" and bundle["node"] == NODE_ID


def test_peer_commands_need_a_set_up_node(executor):
    lines, say = said()
    assert RemotePeers.peers(say) == 1 and "not set up" in lines[-1]


# -- the gate's probe names the node -------------------------------------------------------------


def test_the_gate_probe_carries_the_node_id(tmp_path, node, monkeypatch):
    assert "node" not in ModelGateService.state(str(tmp_path))
    (tmp_path / NODE_ID_FILE).write_text("not a uuid\n")
    assert "node" not in ModelGateService.state(str(tmp_path))
    monkeypatch.setattr("dreamference.vllm_server.model_gate.GATE_DIR", tmp_path)
    ModelGate.write_node_id()
    assert ModelGateService.state(str(tmp_path))["node"] == NODE_ID


# -- the CLI and the guards ----------------------------------------------------------------------


def test_the_commands_parse_and_an_unknown_one_prints_usage(capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    parser = DreamferenceCLIController.build_parser()
    args = parser.parse_args(
        ["remote", "setup", "nb.example.org", "--relay-name", "r.example.org", "--dry-run"]
    )
    assert (args.dns_name, args.relay_name, args.dry_run, args.yes) == (
        "nb.example.org",
        "r.example.org",
        True,
        False,
    )
    assert parser.parse_args(["node", "remote", "revoke", "lg-gram"]).peer == "lg-gram"
    assert RemoteCommand.dispatch_remote(parser.parse_args(["remote"])) == 2
    assert RemoteCommand.dispatch_node_remote(parser.parse_args(["node", "remote"])) == 2
    assert "usage: ling-admin node remote" in capsys.readouterr().out


def test_the_real_netbird_never_runs_in_a_test(tmp_path):
    with pytest.raises(AssertionError, match="real netbird"):
        subprocess.run(["netbird", "status"], capture_output=True)
    stand_in = tmp_path / "netbird"
    stand_in.write_text("#!/bin/sh\necho 0.81.3\n")
    stand_in.chmod(0o755)
    assert (
        subprocess.run([str(stand_in), "version"], capture_output=True, text=True).stdout
        == "0.81.3\n"
    )
    assert RemoteNodePeer.trust_steps(Path("/x/ca.crt"))[0].root
