"""
Remote access (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md): the node's CA, the control plane,
the box, the tunnel, the node's own peer, enrolment by code, and the commands that tie them.

Nothing here reaches a real box, Docker, systemd, sudo or the network: every class's seam (`_run`,
`_request`, `_download`, `_run_root`) is replaced. `openssl` runs for real where noted, inside the
test's own HOME, because the argv is the thing worth testing there.
"""

import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request

import pytest

from dreamference.node import NodeServiceFile
from dreamference.remote import (
    RemoteAccess,
    RemoteBox,
    RemoteCertificateAuthority,
    RemoteControlPlane,
    RemoteEnrolment,
    RemoteNodePeer,
    RemoteSettings,
    RemoteTunnel,
)
from dreamference.remote import remote_settings

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"
BOX = "relay.example.org"
HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBoxHostKey root@box"

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")


def _done(argv, out="", code=0, err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


# -- settings ---------------------------------------------------------------------------------------

def test_names_are_derived_from_the_node_id_and_the_box_name_never_an_address():
    assert RemoteSettings.node_peer_name(NODE_ID.upper()) == f"mightling-{NODE_ID}"
    assert RemoteSettings.overlay_name(NODE_ID) == f"mightling-{NODE_ID}.netbird.selfhosted"
    assert len(RemoteSettings.node_peer_name(NODE_ID)) <= 63, "one DNS label"
    assert RemoteSettings.management_url(BOX) == "https://relay.example.org:443"
    assert RemoteSettings.relay_address(BOX) == "rels://relay.example.org:33080"


def test_private_files_are_0600_in_a_0700_folder_and_remote_json_needs_a_box():
    path = RemoteSettings.write_private("admin-token", "nbp_secret\n")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(RemoteSettings.folder().stat().st_mode & 0o777) == "0o700"
    assert RemoteSettings.read_private("admin-token") == "nbp_secret"
    assert RemoteSettings.read() is None
    RemoteSettings.write({"dns_name": BOX, "overlay_name": "x"})
    assert RemoteSettings.read()["dns_name"] == BOX


def test_everything_from_netbird_is_pinned_by_digest():
    assert "@sha256:" in remote_settings.SERVER_IMAGE and remote_settings.NETBIRD_VERSION in remote_settings.SERVER_IMAGE
    for arch in ("amd64", "arm64"):
        assert "@sha256:" in remote_settings.RELAY_IMAGES[arch]
        assert len(remote_settings.RELAY_BINARY_SHA256[arch]) == 64
        assert len(remote_settings.CLIENT_ARCHIVES[arch]) == 64


# -- the CA -----------------------------------------------------------------------------------------

@needs_openssl
def test_the_ca_is_name_constrained_and_signs_only_what_it_may():
    assert RemoteCertificateAuthority.ensure(BOX)
    assert oct(RemoteCertificateAuthority.ca_key().stat().st_mode & 0o777) == "0o600"
    text = subprocess.run(["openssl", "x509", "-in", str(RemoteCertificateAuthority.ca_certificate()), "-noout", "-text"],
                          capture_output=True, text=True).stdout
    assert "Name Constraints: critical" in text and f"DNS:{BOX}" in text and "DNS:netbird.selfhosted" in text
    assert "pathlen:0" in text
    assert RemoteCertificateAuthority.permits(BOX)
    assert RemoteCertificateAuthority.permits("mgmt." + BOX), "a name under the box's is permitted"
    assert not RemoteCertificateAuthority.permits("other.example.org")
    issued = RemoteCertificateAuthority.issue("control-plane", [BOX])
    assert issued is not None
    certificate, key = issued
    verified = subprocess.run(["openssl", "verify", "-CAfile", str(RemoteCertificateAuthority.ca_certificate()), str(certificate)],
                              capture_output=True, text=True)
    assert verified.returncode == 0, verified.stderr
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    expiry = RemoteCertificateAuthority.expiry(certificate)
    assert expiry is not None and 800 < (expiry.timestamp() - time.time()) / 86400 < 830


@needs_openssl
def test_a_certificate_outside_the_constraints_does_not_verify():
    RemoteCertificateAuthority.ensure(BOX)
    certificate, _ = RemoteCertificateAuthority.issue("stray", ["www.bank.example"])
    verified = subprocess.run(["openssl", "verify", "-CAfile", str(RemoteCertificateAuthority.ca_certificate()), str(certificate)],
                              capture_output=True, text=True)
    assert verified.returncode != 0 and "permitted subtree violation" in verified.stdout + verified.stderr


def test_an_existing_ca_is_kept(monkeypatch):
    RemoteCertificateAuthority.ca_key().parent.mkdir(parents=True)
    RemoteCertificateAuthority.ca_key().write_text("key")
    RemoteCertificateAuthority.ca_certificate().write_text("cert")
    monkeypatch.setattr(RemoteCertificateAuthority, "_run", classmethod(lambda cls, argv, input_text=None: pytest.fail("openssl ran")))
    assert RemoteCertificateAuthority.ensure("another.example.org")


# -- the control plane ------------------------------------------------------------------------------

def test_the_server_config_serves_tls_and_names_the_box_for_relay_and_stun():
    config = RemoteControlPlane.render_config(BOX, "s3cret")
    assert 'exposedAddress: "https://relay.example.org:443"' in config
    assert 'certFile: "/nb/tls.crt"' in config and 'keyFile: "/nb/tls.key"' in config
    assert '"rels://relay.example.org:33080"' in config and '"stun:relay.example.org:3478"' in config
    assert config.count('"s3cret"') == 2
    assert "disableAnonymousMetrics: true" in config
    for digit_run in ("100.", "192.168", "10.0."):
        assert digit_run not in config, "no address literal"


def test_the_container_is_on_loopback_pinned_and_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(RemoteControlPlane, "_run", classmethod(lambda cls, argv, timeout=300: calls.append(argv) or _done(argv)))
    assert RemoteControlPlane.start()
    run = calls[-1]
    assert run[:3] == ["docker", "run", "-d"]
    assert "127.0.0.1:33443:443" in run and remote_settings.SERVER_IMAGE in run
    assert "NB_SETUP_PAT_ENABLED=true" in run and "--memory" in run
    assert run[-2:] == ["--config", "/nb/config.yaml"]


def test_bootstrap_makes_a_one_day_token_then_a_year_token(monkeypatch):
    requests = []

    def fake(cls, dns_name, method, path, body=None, token=None):
        requests.append((method, path, body, token))
        if path == "/api/setup":
            return 200, {"personal_access_token": "nbp_day", "user_id": "u1", "email": body["email"]}
        if path == "/api/users/u1/tokens":
            return 200, {"plain_token": "nbp_year"}
        return 404, {}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.bootstrap(BOX, f"mightling-{NODE_ID}") == "nbp_year"
    setup = requests[0][2]
    assert setup["create_pat"] is True and setup["pat_expire_in"] == 1 and setup["email"].endswith(".invalid")
    assert requests[1][2]["expires_in"] == 365 and requests[1][3] == "nbp_day"


def test_a_setup_key_is_one_off_one_use_ten_minutes(monkeypatch):
    seen = {}

    def fake(cls, dns_name, method, path, body=None, token=None):
        seen.update(body=body, token=token, path=path)
        return 200, {"key": "A1B2"}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.setup_key(BOX, "tok", "client laptop") == "A1B2"
    assert seen["path"] == "/api/setup-keys" and seen["token"] == "tok"
    assert seen["body"] | {} == {"name": "client laptop", "type": "one-off", "expires_in": 600,
                                 "auto_groups": [], "usage_limit": 1, "ephemeral": False}


# -- the box ----------------------------------------------------------------------------------------

def test_haproxy_passes_only_the_boxs_own_name_to_the_tunnel():
    config = RemoteBox.haproxy_config(BOX)
    assert "mode tcp" in config and "bind :443" in config
    assert "    user haproxy" in config and "    group haproxy" in config, "haproxy drops root after binding"
    assert "use_backend node if { req.ssl_sni -i relay.example.org }" in config
    assert "server tunnel 127.0.0.1:8443" in config
    assert "default_backend refuse" in config and "tcp-request content reject" in config
    assert "mode http" not in config and "ssl crt" not in config, "TLS is never terminated on the box"


@pytest.mark.parametrize("script", ["install", "remove"])
def test_the_box_scripts_are_valid_bash(script, tmp_path):
    text = RemoteBox.install_script("amd64", "apt") if script == "install" else RemoteBox.remove_script()
    path = tmp_path / "s.sh"
    path.write_text(text)
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def test_the_install_script_checks_the_binary_and_records_what_it_added():
    text = RemoteBox.install_script("arm64", "dnf")
    assert f"echo '{remote_settings.RELAY_BINARY_SHA256['arm64']}  '" in text and "sha256sum -c" in text
    assert text.index("sha256sum -c") < text.index("install -m 0755 \"$stage\"/netbird-relay")
    assert "dnf install -y -q haproxy" in text and "apt-get" not in text
    assert "haproxy=installed" in text and "firewall=ufw" in text
    assert "sshd -t" in text and "AllowTcpForwarding" not in text, "the drop-in is a staged file"
    assert text.index("global_before=") < text.index("sshd-tunnel.conf") < text.index("global_after=")
    assert "docker" not in text


def test_the_tunnel_account_may_only_listen_on_the_boxs_loopback():
    line = RemoteTunnel.authorized_line("ssh-ed25519 AAAA mightling-remote-tunnel")
    assert line.startswith('restrict,port-forwarding,permitlisten="127.0.0.1:8443",command="/usr/sbin/nologin" ')
    drop_in = RemoteBox.sshd_drop_in()
    assert "Match User mightling-tunnel" in drop_in and "AllowTcpForwarding remote" in drop_in
    assert "GatewayPorts no" in drop_in and "PermitTTY no" in drop_in


def test_probe_reads_the_boxs_facts(monkeypatch):
    out = f"arch=x86_64\nroot=yes\npackages=apt\nhost_key={HOST_KEY}\n"
    monkeypatch.setattr(RemoteBox, "_run", classmethod(lambda cls, argv, input_bytes=None, timeout=600: _done(argv, out)))
    facts = RemoteBox.probe(BOX)
    assert facts == {"arch": "amd64", "root": "yes", "packages": "apt", "host_key": HOST_KEY}


def test_a_relay_binary_with_the_wrong_digest_is_never_sent(monkeypatch):
    calls = []

    def fake(cls, argv, input_bytes=None, timeout=600):
        calls.append(argv)
        if argv[:2] == ["docker", "create"]:
            return _done(argv, "c0ffee\n")
        if argv[:2] == ["docker", "cp"]:
            open(argv[3], "wb").write(b"not the relay")
        return _done(argv)

    monkeypatch.setattr(RemoteBox, "_run", classmethod(fake))
    cert = RemoteSettings.write_private("certs/relay.crt", "c")
    assert not RemoteBox.deploy(BOX, "amd64", "apt", "s", cert, cert, "ssh-ed25519 AAAA t")
    assert not any(argv[0] == "ssh" for argv in calls)
    assert ["docker", "rm", "c0ffee"] in calls, "the scratch container is removed"


def test_deploy_copies_one_archive_then_runs_one_root_script(monkeypatch):
    calls = []
    binary = b"\x7fELF relay"
    monkeypatch.setitem(remote_settings.RELAY_BINARY_SHA256, "amd64", hashlib.sha256(binary).hexdigest())
    monkeypatch.setitem(__import__("dreamference.remote.remote_box", fromlist=["x"]).RELAY_BINARY_SHA256,
                        "amd64", hashlib.sha256(binary).hexdigest())

    def fake(cls, argv, input_bytes=None, timeout=600):
        calls.append((argv, input_bytes))
        if argv[:2] == ["docker", "create"]:
            return _done(argv, "c0ffee\n")
        if argv[:2] == ["docker", "cp"]:
            open(argv[3], "wb").write(binary)
        if argv[0] == "ssh" and input_bytes and input_bytes.startswith(b"set -eu"):
            return _done(argv, "mightling-box-ready\n")
        return _done(argv)

    monkeypatch.setattr(RemoteBox, "_run", classmethod(fake))
    cert = RemoteSettings.write_private("certs/relay.crt", "CERT")
    key = RemoteSettings.write_private("certs/relay.key", "KEY")
    assert RemoteBox.deploy(BOX, "amd64", "apt", "s3cret", cert, key, "ssh-ed25519 AAAA t")
    ssh_calls = [(argv, data) for argv, data in calls if argv[0] == "ssh"]
    assert len(ssh_calls) == 2
    copy, install = ssh_calls
    assert copy[0][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"] and copy[0][5] == BOX
    assert copy[0][6].endswith("cat > /tmp/mightling-remote-stage.tgz")
    with tarfile.open(fileobj=io.BytesIO(copy[1]), mode="r:gz") as archive:
        names = set(archive.getnames())
        assert archive.extractfile("relay.env").read() == b"NB_AUTH_SECRET=s3cret\n"
        assert archive.extractfile("netbird-relay").read() == binary
        assert all(member.mode == 0o600 for member in archive.getmembers())
    assert names == {"netbird-relay", "relay.crt", "relay.key", "relay.env", "haproxy.cfg",
                     "mightling-relay.service", "authorized_keys", "sshd-tunnel.conf"}
    assert "sudo -n bash -s" in install[0][6]


def test_the_relay_unit_runs_unprivileged_with_its_own_tls_and_stun():
    unit = RemoteBox.relay_unit(BOX)
    assert "User=mightling-relay" in unit and "NoNewPrivileges=yes" in unit
    assert "--exposed-address rels://relay.example.org:33080" in unit and "--enable-stun" in unit
    assert "--tls-cert-file" in unit and "letsencrypt" not in unit
    assert "EnvironmentFile=/opt/mightling-relay/relay.env" in unit and "NB_AUTH_SECRET" not in unit


# -- the tunnel -------------------------------------------------------------------------------------

def test_the_tunnel_unit_checks_the_box_and_fails_loudly():
    unit = RemoteTunnel.render_unit(BOX)
    assert "-R 127.0.0.1:8443:127.0.0.1:33443" in unit
    assert "StrictHostKeyChecking=yes" in unit and f"UserKnownHostsFile={RemoteTunnel.known_hosts()}" in unit
    assert "ExitOnForwardFailure=yes" in unit and "BatchMode=yes" in unit and "-F /dev/null" in unit
    assert f"mightling-tunnel@{BOX}" in unit and "Restart=always" in unit


def test_the_known_hosts_file_holds_the_box_and_nothing_else():
    RemoteTunnel.write_known_host(BOX, HOST_KEY)
    assert RemoteTunnel.known_hosts().read_text() == f"{BOX} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBoxHostKey\n"


def test_install_enables_and_restarts_the_user_unit(monkeypatch):
    calls = []
    monkeypatch.setattr(RemoteTunnel, "_run", classmethod(lambda cls, argv: calls.append(argv) or _done(argv)))
    assert RemoteTunnel.install(BOX)
    assert ["systemctl", "--user", "enable", "mightling-remote-tunnel.service"] in calls
    assert ["systemctl", "--user", "restart", "mightling-remote-tunnel.service"] in calls
    assert RemoteTunnel.unit_path().read_text() == RemoteTunnel.render_unit(BOX)


# -- the node's own peer ----------------------------------------------------------------------------

def _archive(binary=b"netbird binary"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in (("README.md", b"r"), ("netbird", binary)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_the_client_archive_must_match_its_pin(monkeypatch):
    data = _archive()
    monkeypatch.setattr(RemoteNodePeer, "arch", classmethod(lambda cls: "arm64"))
    monkeypatch.setattr(RemoteNodePeer, "_download", classmethod(lambda cls, url: data))
    assert RemoteNodePeer.client_binary() is None
    monkeypatch.setitem(__import__("dreamference.remote.remote_node_peer", fromlist=["x"]).CLIENT_ARCHIVES,
                        "arm64", hashlib.sha256(data).hexdigest())
    binary = RemoteNodePeer.client_binary()
    assert binary is not None and binary.read_bytes() == b"netbird binary"


def test_the_node_joins_under_its_own_name_with_the_ca_in_the_system_store(tmp_path):
    script = RemoteNodePeer.script(tmp_path / "netbird", tmp_path / "ca.crt", "https://relay.example.org:443", "KEY",
                                   f"mightling-{NODE_ID}")
    assert "install -m 0755" in script and "/usr/local/bin/netbird" in script
    assert "/usr/local/share/ca-certificates/mightling-node-ca.crt" in script and "update-ca-certificates" in script
    assert f"netbird up --management-url https://relay.example.org:443 --setup-key KEY --hostname mightling-{NODE_ID}" in script


def test_enrol_runs_one_root_script_and_deletes_it(monkeypatch):
    ran = []
    monkeypatch.setattr(RemoteNodePeer, "installed", classmethod(lambda cls: "/usr/local/bin/netbird"))
    monkeypatch.setattr(RemoteNodePeer, "_run_root",
                        classmethod(lambda cls, script, purpose, yes: ran.append(script.read_text()) or True))
    assert RemoteNodePeer.enrol(RemoteCertificateAuthority.ca_certificate(), "https://b:443", "K", NODE_ID, yes=True)
    assert len(ran) == 1 and "install -m 0755" not in ran[0], "an installed client is kept"
    assert not RemoteSettings.path("node-peer.sh").exists(), "the setup key does not stay on disk"


# -- enrolment --------------------------------------------------------------------------------------

def test_the_proofs_match_the_shared_vectors():
    # The same vectors are in the launcher's tests (ling-rs/src/remote_join.rs).
    nonce = "00112233445566778899aabbccddeeff"
    assert RemoteEnrolment.client_proof("12345678", nonce) == "5cc98f27431d7b9945a3a7d2215bb483ef347521c9a0106c73c40cc87a24129a"
    assert RemoteEnrolment.server_mac("12345678", nonce, '{"a": 1}') == "95aac94aa3d5918feea3606ca4501a53022e70dd0e1bd320ff66a2570228f9de"
    assert len(RemoteEnrolment.new_code()) == 8 and RemoteEnrolment.new_code().isdigit()


def test_a_wrong_proof_gets_nothing_and_mints_nothing():
    minted = []
    request = {"nonce": "0" * 32, "proof": RemoteEnrolment.client_proof("87654321", "0" * 32)}
    assert RemoteEnrolment.answer("12345678", request, lambda c: minted.append(c) or {}) is None
    assert RemoteEnrolment.answer("12345678", {"nonce": "zz" * 16, "proof": "x"}, lambda c: {}) is None
    assert minted == []


def test_a_right_proof_gets_a_bundle_the_client_can_check():
    nonce = "ab" * 16
    request = {"nonce": nonce, "proof": RemoteEnrolment.client_proof("12345678", nonce), "client": "laptop"}
    answer = RemoteEnrolment.answer("12345678", request, lambda client: {"setup_key": "K", "for": client})
    assert json.loads(answer["bundle"]) == {"setup_key": "K", "for": "laptop"}
    assert answer["mac"] == RemoteEnrolment.server_mac("12345678", nonce, answer["bundle"])


@pytest.mark.parametrize("address,allowed", [
    ("192.168.0.20", True), ("10.1.2.3", True), ("172.16.5.5", True), ("fe80::1%wlan0", True),
    ("127.0.0.1", True), ("::ffff:192.168.0.20", True),
    ("100.82.28.142", False), ("100.64.0.1", False), ("8.8.8.8", False), ("2001:4860::1", False), ("bogus", False),
])
def test_only_the_lan_may_enrol_never_the_overlay(address, allowed):
    assert RemoteEnrolment.lan_address(address) is allowed


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _post(port, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mightling/enrol", data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_listener_serves_one_client_then_stops():
    port = _free_port()
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(result=RemoteEnrolment.serve(
        "12345678", lambda client: {"setup_key": "K"}, port=port, minutes=1, host="127.0.0.1")))
    thread.start()
    time.sleep(0.3)
    status, body = _post(port, {"nonce": "1" * 32, "proof": "0" * 64})
    assert status == 401 and body["attempts_left"] == 9
    nonce = "2" * 32
    status, body = _post(port, {"nonce": nonce, "proof": RemoteEnrolment.client_proof("12345678", nonce), "client": "laptop"})
    assert status == 200 and body["mac"] == RemoteEnrolment.server_mac("12345678", nonce, body["bundle"])
    thread.join(5)
    assert outcome["result"] == "enrolled:laptop"


def test_ten_wrong_proofs_withdraw_the_code():
    port = _free_port()
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(result=RemoteEnrolment.serve(
        "12345678", lambda client: pytest.fail("minted"), port=port, minutes=1, host="127.0.0.1")))
    thread.start()
    time.sleep(0.3)
    for _ in range(10):
        _post(port, {"nonce": "1" * 32, "proof": "0" * 64})
    thread.join(5)
    assert outcome["result"] == "withdrawn"


# -- the commands -----------------------------------------------------------------------------------

@pytest.fixture
def seams(monkeypatch):
    """Every outside effect of `setup`, recorded."""
    log = []
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: NODE_ID))
    monkeypatch.setattr(RemoteAccess, "_confirm", classmethod(lambda cls, q: True))
    monkeypatch.setattr(RemoteAccess, "_advertise", classmethod(lambda cls, name: log.append(("advertise", name))))
    monkeypatch.setattr(RemoteBox, "probe", classmethod(lambda cls, d: {"arch": "amd64", "root": "yes", "packages": "apt", "host_key": HOST_KEY}))
    monkeypatch.setattr(RemoteBox, "deploy", classmethod(lambda cls, *a: log.append(("deploy", a[0], a[1])) or True))
    monkeypatch.setattr(RemoteCertificateAuthority, "ensure", classmethod(lambda cls, d: log.append(("ca", d)) or True))

    def issue(cls, label, names):
        crt = RemoteSettings.write_private(f"certs/{label}.crt", f"{label} cert")
        key = RemoteSettings.write_private(f"certs/{label}.key", f"{label} key")
        log.append(("issue", label, tuple(names)))
        return crt, key

    monkeypatch.setattr(RemoteCertificateAuthority, "issue", classmethod(issue))
    monkeypatch.setattr(RemoteControlPlane, "start", classmethod(lambda cls: log.append(("start",)) or True))
    monkeypatch.setattr(RemoteControlPlane, "wait_ready", classmethod(lambda cls, d, seconds=120: True))
    monkeypatch.setattr(RemoteControlPlane, "bootstrap", classmethod(lambda cls, d, n: "nbp_year"))
    monkeypatch.setattr(RemoteControlPlane, "setup_key", classmethod(lambda cls, d, t, n: log.append(("key", n)) or "KEY"))
    monkeypatch.setattr(RemoteTunnel, "public_key", classmethod(lambda cls: "ssh-ed25519 AAAA t"))
    monkeypatch.setattr(RemoteTunnel, "install", classmethod(lambda cls, d: log.append(("tunnel", d)) or True))
    monkeypatch.setattr(RemoteNodePeer, "enrol", classmethod(lambda cls, ca, url, key, node, yes: log.append(("join", url, key)) or True))
    monkeypatch.setattr(RemoteAccess, "node_label",
                        classmethod(lambda cls, d, t, name, seconds=30: f"{name}.netbird.selfhosted"))
    return log


def test_setup_does_everything_in_order_and_records_names_only(seams):
    assert RemoteAccess.setup("Relay.Example.org.", yes=True) == 0
    steps = [entry[0] for entry in seams]
    assert steps == ["ca", "issue", "issue", "start", "deploy", "tunnel", "key", "join", "advertise"]
    assert ("join", "https://relay.example.org:443", "KEY") in seams
    record = RemoteSettings.read()
    assert record["dns_name"] == BOX and record["overlay_name"] == f"mightling-{NODE_ID}.netbird.selfhosted"
    assert record["node_peer"] is True
    assert RemoteSettings.read_private("admin-token") == "nbp_year"
    assert len(RemoteSettings.read_private("relay-secret")) == 64
    assert RemoteTunnel.known_hosts().read_text().startswith(BOX + " ssh-ed25519 ")
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", RemoteSettings.path("remote.json").read_text()), "no address is written"


def test_setup_fails_loudly_when_management_renamed_the_node(seams, monkeypatch, capsys):
    monkeypatch.setattr(RemoteAccess, "node_label",
                        classmethod(lambda cls, d, t, name, seconds=30: f"{name}-1.netbird.selfhosted"))
    assert RemoteAccess.setup(BOX, yes=True) == 1
    assert "not mightling-" in capsys.readouterr().out
    assert not RemoteSettings.read().get("node_peer"), "a rerun checks again"
    assert ("advertise", RemoteSettings.overlay_name(NODE_ID)) not in seams


def test_node_label_reads_managements_name_for_the_node(monkeypatch):
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    assert RemoteAccess.node_label(BOX, "tok", f"mightling-{NODE_ID}") == f"mightling-{NODE_ID}.netbird.selfhosted"
    assert RemoteAccess.node_label(BOX, "tok", "nobody", seconds=0) is None


def test_a_renewal_leaves_one_node_token(monkeypatch):
    requests = []

    def fake(cls, dns_name, method, path, body=None, token=None):
        requests.append((method, path))
        if method == "POST":
            return 200, {"plain_token": "nbp_new", "personal_access_token": {"id": "t3"}}
        if method == "GET":
            return 200, [{"id": "t1", "name": "setup-token"}, {"id": "t2", "name": "mightling-node-20251010"},
                         {"id": "t3", "name": "mightling-node-20261010"}, {"id": "t9", "name": "someone else's"}]
        return 200, {}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.renew_token(BOX, "old", "u1") == "nbp_new"
    deleted = [path for method, path in requests if method == "DELETE"]
    assert deleted == ["/api/users/u1/tokens/t1", "/api/users/u1/tokens/t2"]


def test_setup_again_keeps_the_secret_and_does_not_rejoin(seams, monkeypatch):
    RemoteAccess.setup(BOX, yes=True)
    secret = RemoteSettings.read_private("relay-secret")
    seams.clear()
    monkeypatch.setattr(RemoteControlPlane, "wait_ready", classmethod(lambda cls, d, seconds=120: False))
    monkeypatch.setattr(RemoteControlPlane, "renew_token", classmethod(lambda cls, d, t, user_id=None: "nbp_new"))
    assert RemoteAccess.setup(BOX, yes=True) == 0
    assert RemoteSettings.read_private("relay-secret") == secret
    assert "join" not in [entry[0] for entry in seams]
    assert RemoteSettings.read_private("admin-token") == "nbp_new"


@pytest.mark.parametrize("name", ["", "relay", "http://relay.example.org", "relay.example.org:443", "user@relay.example.org"])
def test_setup_wants_a_dns_name(seams, name):
    assert RemoteAccess.setup(name, yes=True) == 2
    assert seams == []


def test_setup_refuses_on_a_machine_that_is_not_a_node(seams, monkeypatch):
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: None))
    assert RemoteAccess.setup(BOX, yes=True) == 1 and seams == []


@pytest.mark.parametrize("facts,reason", [
    ({"error": "Permission denied (publickey)"}, "did not work"),
    ({"arch": "amd64", "root": "no", "packages": "apt", "host_key": HOST_KEY}, "passwordless sudo"),
    ({"arch": "", "root": "yes", "packages": "apt", "host_key": HOST_KEY}, "amd64 nor arm64"),
    ({"arch": "amd64", "root": "yes", "packages": "", "host_key": HOST_KEY}, "neither apt nor dnf"),
    ({"arch": "amd64", "root": "yes", "packages": "apt", "host_key": ""}, "ed25519 host key"),
])
def test_setup_stops_before_changing_anything_when_the_box_will_not_do(seams, monkeypatch, capsys, facts, reason):
    monkeypatch.setattr(RemoteBox, "probe", classmethod(lambda cls, d: facts))
    assert RemoteAccess.setup(BOX, yes=True) == 1
    assert reason in capsys.readouterr().out and seams == []


def test_a_box_under_another_name_needs_a_new_ca(seams, monkeypatch, capsys):
    RemoteSettings.write({"dns_name": "old.example.org", "overlay_name": "x"})
    RemoteCertificateAuthority.ca_certificate().parent.mkdir(parents=True, exist_ok=True)
    RemoteCertificateAuthority.ca_certificate().write_text("ca")
    monkeypatch.setattr(RemoteCertificateAuthority, "permits", classmethod(lambda cls, d: False))
    assert RemoteAccess.setup(BOX, yes=True) == 1
    assert "remove --purge" in capsys.readouterr().out and seams == []


def test_declining_changes_nothing(seams, monkeypatch):
    monkeypatch.setattr(RemoteAccess, "_confirm", classmethod(lambda cls, q: False))
    assert RemoteAccess.setup(BOX) == 1 and seams == []


def _set_up():
    RemoteSettings.write({"dns_name": BOX, "overlay_name": f"mightling-{NODE_ID}.netbird.selfhosted", "node_peer": True})
    RemoteSettings.write_private("admin-token", "tok")


PEERS = [
    {"id": "p0", "name": f"mightling-{NODE_ID}", "dns_label": f"mightling-{NODE_ID}.netbird.selfhosted", "connected": True},
    {"id": "p1", "name": "laptop", "dns_label": "laptop.netbird.selfhosted", "connected": True, "os": "Ubuntu"},
    {"id": "p2", "name": "laptop-2", "dns_label": "laptop-2.netbird.selfhosted", "connected": False, "last_seen": "t"},
]


def test_revoke_deletes_exactly_one_peer(monkeypatch):
    _set_up()
    deleted = []
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    monkeypatch.setattr(RemoteControlPlane, "delete_peer", classmethod(lambda cls, d, t, p: deleted.append(p) or True))
    assert RemoteAccess.revoke("laptop") == 0 and deleted == ["p1"], "an exact name wins over a prefix"
    assert RemoteAccess.revoke("lap") == 1 and deleted == ["p1"], "an ambiguous prefix deletes nothing"
    assert RemoteAccess.revoke("mightling-") == 1 and deleted == ["p1"], "the node itself is not revoked"


def test_remove_keeps_the_ca_and_purge_deletes_everything(monkeypatch):
    _set_up()
    calls = []
    monkeypatch.setattr(RemoteAccess, "_advertise", classmethod(lambda cls, name: calls.append(("advertise", name))))
    monkeypatch.setattr(RemoteBox, "remove", classmethod(lambda cls, d: calls.append(("box", d)) or True))
    monkeypatch.setattr(RemoteTunnel, "remove", classmethod(lambda cls: calls.append(("tunnel",))))
    monkeypatch.setattr(RemoteControlPlane, "stop", classmethod(lambda cls: calls.append(("stop",)) or True))
    monkeypatch.setattr(RemoteNodePeer, "leave", classmethod(lambda cls, yes: calls.append(("leave",)) or True))
    assert RemoteAccess.remove(yes=True) == 0
    assert calls == [("box", BOX), ("tunnel",)] and RemoteSettings.read() is not None
    calls.clear()
    assert RemoteAccess.remove(purge=True, yes=True) == 0
    assert calls == [("box", BOX), ("tunnel",), ("stop",), ("leave",), ("advertise", None)]
    assert not RemoteSettings.folder().exists()


def test_a_bundle_carries_names_the_ca_and_a_fresh_key(monkeypatch):
    _set_up()
    RemoteCertificateAuthority.ca_certificate().parent.mkdir(parents=True, exist_ok=True)
    RemoteCertificateAuthority.ca_certificate().write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(RemoteControlPlane, "setup_key", classmethod(lambda cls, d, t, n: f"KEY for {n}"))
    bundle = RemoteAccess.bundle(RemoteSettings.read(), "tok", NODE_ID, "laptop")
    assert bundle["setup_key"] == "KEY for client laptop" and bundle["node"] == NODE_ID
    assert bundle["management_url"] == "https://relay.example.org:443"
    assert bundle["relay"] == "rels://relay.example.org:33080"
    assert bundle["overlay_name"] == f"mightling-{NODE_ID}.netbird.selfhosted" and bundle["ca"].startswith("-----BEGIN")


def test_status_without_setup_says_so(capsys):
    assert RemoteAccess.status() == 3 and "not set up" in capsys.readouterr().out


# -- the advertisement and the CLI ------------------------------------------------------------------

def test_the_advert_carries_the_overlay_name_only_when_set_up():
    plain = NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1.6.1")
    assert "remote=" not in plain
    with_remote = NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1.6.1", remote="mightling-x.netbird.selfhosted")
    assert NodeServiceFile.parse(with_remote)["remote"] == "mightling-x.netbird.selfhosted"
    NodeServiceFile.service_path.write_text(with_remote)
    assert NodeServiceFile.update(state="ready") is True
    assert NodeServiceFile.read()["remote"] == "mightling-x.netbird.selfhosted", "a state change keeps it"
    assert NodeServiceFile.update(remote=None) is True and "remote" not in NodeServiceFile.read()


def test_the_cli_has_the_remote_group():
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    parser = DreamferenceCLIController.build_parser()
    args = parser.parse_args(["remote", "setup", BOX, "--yes"])
    assert args.remote_command == "setup" and args.dns_name == BOX and args.yes
    assert parser.parse_args(["remote", "remove", "--purge"]).purge
    assert "remote" in parser.command_groups
