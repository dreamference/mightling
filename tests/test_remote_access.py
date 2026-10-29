"""Remote access (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md).

The node's CA, the control plane, the box, the tunnel, the node's own peer, enrolment by code, and
the commands that tie them.

Nothing here reaches a real box, Docker, systemd, sudo or the network: every class's seam (`_run`,
`_request`, `_download`, `_run_root`) is replaced. `openssl` runs for real where noted, inside the
test's own HOME, because the argv is the thing worth testing there.
"""

import base64
import contextlib
import hashlib
import io
import json
import pathlib
import re
import shutil
import socket
import ssl
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
    RemoteJoinScript,
    RemoteNodePeer,
    RemotePublicCertificate,
    RemoteSettings,
    RemoteTunnel,
    remote_settings,
)
from dreamference.remote.remote_settings import CLIENT_ARCHIVES

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"
BOX = "relay.example.org"
HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBoxHostKey root@box"

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)


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


def test_a_private_write_that_fails_leaves_the_old_file_whole():
    """remote.json was truncated before the new text was written: a failed write lost the record."""
    RemoteSettings.write({"dns_name": BOX, "overlay_name": "x"})
    before = RemoteSettings.path("remote.json").read_text()
    with pytest.raises(UnicodeEncodeError):
        RemoteSettings.write_private("remote.json", "\udc80")
    assert RemoteSettings.path("remote.json").read_text() == before
    assert RemoteSettings.read()["dns_name"] == BOX
    assert sorted(p.name for p in RemoteSettings.folder().iterdir()) == ["remote.json"], (
        "no temporary file is left behind"
    )


def test_everything_from_netbird_is_pinned_by_digest():
    assert (
        "@sha256:" in remote_settings.SERVER_IMAGE
        and remote_settings.NETBIRD_VERSION in remote_settings.SERVER_IMAGE
    )
    for arch in ("amd64", "arm64"):
        assert "@sha256:" in remote_settings.RELAY_IMAGES[arch]
        assert len(remote_settings.RELAY_BINARY_SHA256[arch]) == 64
        assert len(remote_settings.CLIENT_ARCHIVES[arch]) == 64


# -- the CA -----------------------------------------------------------------------------------------


@needs_openssl
def test_the_ca_is_name_constrained_and_signs_only_what_it_may():
    assert RemoteCertificateAuthority.ensure(BOX)
    assert oct(RemoteCertificateAuthority.ca_key().stat().st_mode & 0o777) == "0o600"
    text = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(RemoteCertificateAuthority.ca_certificate()),
            "-noout",
            "-text",
        ],
        capture_output=True,
        text=True,
    ).stdout
    assert (
        "Name Constraints: critical" in text
        and f"DNS:{BOX}" in text
        and "DNS:netbird.selfhosted" in text
    )
    assert "pathlen:0" in text
    assert RemoteCertificateAuthority.permits(BOX)
    assert RemoteCertificateAuthority.permits("mgmt." + BOX), "a name under the box's is permitted"
    assert not RemoteCertificateAuthority.permits("other.example.org")
    issued = RemoteCertificateAuthority.issue("control-plane", [BOX])
    assert issued is not None
    certificate, key = issued
    verified = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(RemoteCertificateAuthority.ca_certificate()),
            str(certificate),
        ],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    expiry = RemoteCertificateAuthority.expiry(certificate)
    assert expiry is not None and 800 < (expiry.timestamp() - time.time()) / 86400 < 830


@needs_openssl
def test_a_reissue_that_cannot_be_signed_keeps_the_old_key_and_certificate(monkeypatch):
    RemoteCertificateAuthority.ensure(BOX)
    certificate, key = RemoteCertificateAuthority.issue("control-plane", [BOX])
    before = (certificate.read_bytes(), key.read_bytes())
    real = RemoteCertificateAuthority._run.__func__

    def run(cls, argv, input_text=None):
        if argv[1] == "x509":
            return subprocess.CompletedProcess(argv, 1, "", "unable to load CA private key")
        return real(cls, argv, input_text)

    monkeypatch.setattr(RemoteCertificateAuthority, "_run", classmethod(run))
    assert RemoteCertificateAuthority.issue("control-plane", [BOX]) is None
    assert (certificate.read_bytes(), key.read_bytes()) == before, "the pair still matches"


@needs_openssl
def test_a_certificate_outside_the_constraints_does_not_verify():
    RemoteCertificateAuthority.ensure(BOX)
    certificate, _ = RemoteCertificateAuthority.issue("stray", ["www.bank.example"])
    verified = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(RemoteCertificateAuthority.ca_certificate()),
            str(certificate),
        ],
        capture_output=True,
        text=True,
    )
    assert (
        verified.returncode != 0
        and "permitted subtree violation" in verified.stdout + verified.stderr
    )


def test_an_existing_ca_is_kept(monkeypatch):
    RemoteCertificateAuthority.ca_key().parent.mkdir(parents=True)
    RemoteCertificateAuthority.ca_key().write_text("key")
    RemoteCertificateAuthority.ca_certificate().write_text("cert")
    monkeypatch.setattr(
        RemoteCertificateAuthority,
        "_run",
        classmethod(lambda cls, argv, input_text=None: pytest.fail("openssl ran")),
    )
    assert RemoteCertificateAuthority.ensure("another.example.org")


# -- the control plane ------------------------------------------------------------------------------


def test_the_server_config_serves_tls_and_names_the_box_for_relay_and_stun():
    config = RemoteControlPlane.render_config(BOX, "s3cret", "S0VZ")
    assert 'exposedAddress: "https://relay.example.org:443"' in config
    assert 'certFile: "/nb/tls.crt"' in config and 'keyFile: "/nb/tls.key"' in config
    assert (
        '"rels://relay.example.org:33080"' in config and '"stun:relay.example.org:3478"' in config
    )
    assert config.count('"s3cret"') == 2
    assert "disableAnonymousMetrics: true" in config
    # Fixed before the first start: a key made at each start loses the tokens at the next restart.
    assert 'encryptionKey: "S0VZ"' in config
    for digit_run in ("100.", "192.168", "10.0."):
        assert digit_run not in config, "no address literal"


def _geolocation_files():
    data = RemoteSettings.path("server", "data")
    data.mkdir(parents=True, exist_ok=True)
    (data / "GeoLite2-City_20260911.mmdb").write_bytes(b"mmdb")
    (data / "geonames_20260911.db").write_bytes(b"db")


def test_the_container_is_on_loopback_pinned_and_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(
        RemoteControlPlane,
        "_run",
        classmethod(lambda cls, argv, timeout=300: calls.append(argv) or _done(argv)),
    )
    _geolocation_files()
    assert RemoteControlPlane.start()
    assert sum(argv[:2] == ["docker", "run"] for argv in calls) == 1, (
        "no fetch when the files are there"
    )
    run = next(argv for argv in calls if argv[:2] == ["docker", "run"])
    assert run[:3] == ["docker", "run", "-d"] and remote_settings.SERVER_IMAGE in run
    # On an internal network, published nowhere: the loopback proxy is the only way in.
    assert run[run.index("--network") + 1] == "dreamference-remote" and "-p" not in run
    assert "NB_SETUP_PAT_ENABLED=true" not in run and "--memory" in run
    assert run[-2:] == ["--config", "/nb/config.yaml"]
    assert ["systemctl", "--user", "enable", "--now", "mightling-remote-control.socket"] in calls
    units = RemoteControlPlane.proxy_units()
    assert "ListenStream=127.0.0.1:33443" in units["mightling-remote-control.socket"]
    assert "systemd-socket-proxyd 172.31.232.10:443" in units["mightling-remote-control.service"]
    # Only the start that creates the owner may hand out a token.
    assert "NB_SETUP_PAT_ENABLED=true" in RemoteControlPlane.run_command(setup_pat=True)


def test_bootstrap_makes_a_one_day_token_then_a_year_token(monkeypatch):
    requests = []

    def fake(cls, dns_name, method, path, body=None, token=None):
        requests.append((method, path, body, token))
        if path == "/api/setup":
            return 200, {
                "personal_access_token": "nbp_day",
                "user_id": "u1",
                "email": body["email"],
            }
        if path == "/api/users/u1/tokens":
            return 200, {"plain_token": "nbp_year"}
        return 404, {}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.bootstrap(BOX, f"mightling-{NODE_ID}") == "nbp_year"
    setup = requests[0][2]
    assert (
        setup["create_pat"] is True
        and setup["pat_expire_in"] == 1
        and setup["email"].endswith(".invalid")
    )
    assert requests[1][2]["expires_in"] == 365 and requests[1][3] == "nbp_day"
    # The owner's password is the way back if the token is ever lost: kept, private.
    assert RemoteSettings.read_private("owner-password") == setup["password"]
    assert oct(RemoteSettings.path("owner-password").stat().st_mode & 0o777) == "0o600"


def test_the_ca_scope_is_the_parent_of_the_box_name_unless_set():
    assert RemoteSettings.default_ca_scope("remote.dreamference.ai") == "dreamference.ai"
    assert RemoteSettings.default_ca_scope("dreamference.ai") == "dreamference.ai"
    assert RemoteSettings.in_scope("remote.dreamference.ai", "dreamference.ai")
    assert not RemoteSettings.in_scope("remote.dreamference.ai.evil.org", "dreamference.ai")
    assert RemoteCertificateAuthority.constraints("dreamference.ai") == (
        "critical,permitted;DNS:dreamference.ai,permitted;DNS:netbird.selfhosted"
    )


def test_a_box_outside_the_given_scope_is_refused(seams, capsys):
    assert RemoteAccess.setup("remote.dreamference.ai", yes=True, ca_scope="example.org") == 2
    assert "not under the CA scope example.org" in capsys.readouterr().out
    assert seams == []


def test_a_set_up_control_plane_never_starts_with_the_setup_token_allowed(seams, monkeypatch):
    RemoteSettings.write_private("admin-token", "nbp_old\n")
    monkeypatch.setattr(
        RemoteControlPlane, "wait_ready", classmethod(lambda cls, d, seconds=120: False)
    )
    monkeypatch.setattr(
        RemoteControlPlane, "renew_token", classmethod(lambda cls, d, t, user_id=None: "nbp_new")
    )
    assert RemoteAccess.setup(BOX, yes=True) == 0
    assert [entry[1] for entry in seams if entry[0] == "start"] == [False]
    assert RemoteSettings.read_private("admin-token") == "nbp_new"


def test_a_setup_key_is_one_off_one_use_ten_minutes(monkeypatch):
    seen = {}

    def fake(cls, dns_name, method, path, body=None, token=None):
        seen.update(body=body, token=token, path=path)
        return 200, {"key": "A1B2"}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.setup_key(BOX, "tok", "client laptop") == "A1B2"
    assert seen["path"] == "/api/setup-keys" and seen["token"] == "tok"
    assert seen["body"] | {} == {
        "name": "client laptop",
        "type": "one-off",
        "expires_in": 600,
        "auto_groups": [],
        "usage_limit": 1,
        "ephemeral": False,
    }
    RemoteControlPlane.setup_key(BOX, "tok", "bundle file", ephemeral=True)
    assert seen["body"]["ephemeral"] is True and seen["body"]["usage_limit"] == 1


# -- the box ----------------------------------------------------------------------------------------


def test_haproxy_passes_only_the_boxs_own_name_to_the_tunnel():
    config = RemoteBox.haproxy_config(BOX)
    assert "mode tcp" in config and "bind :443" in config
    assert "    user haproxy" in config and "    group haproxy" in config, (
        "haproxy drops root after binding"
    )
    assert "use_backend node if { req.ssl_sni -i relay.example.org }" in config
    assert "server tunnel 127.0.0.1:8443" in config
    assert "default_backend refuse" in config and "tcp-request content reject" in config
    assert "mode http" not in config and "ssl crt" not in config, (
        "TLS is never terminated on the box"
    )


@pytest.mark.parametrize("script", ["install", "remove"])
def test_the_box_scripts_are_valid_bash(script, tmp_path):
    text = (
        RemoteBox.install_script("amd64", "apt")
        if script == "install"
        else RemoteBox.remove_script()
    )
    path = tmp_path / "s.sh"
    path.write_text(text)
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def test_the_install_script_checks_the_binary_and_records_what_it_added():
    text = RemoteBox.install_script("arm64", "dnf")
    assert (
        f"echo '{remote_settings.RELAY_BINARY_SHA256['arm64']}  '" in text
        and "sha256sum -c" in text
    )
    assert text.index("sha256sum -c") < text.index('install -m 0755 "$stage"/netbird-relay')
    assert "dnf install -y -q haproxy" in text and "apt-get" not in text
    assert "haproxy=installed" in text and "firewall=ufw" in text
    assert "sshd -t" in text and "AllowTcpForwarding" not in text, "the drop-in is a staged file"
    assert (
        text.index("global_before=") < text.index("sshd-tunnel.conf") < text.index("global_after=")
    )
    assert "docker" not in text


def test_a_rerun_keeps_haproxy_recorded_as_installed_by_us(tmp_path):
    """Found on the real box (2026-10-13).

    `setup` after `--box-only` rewrote the manifest as `haproxy=present`, so `remove` would have
    left the package it installed.
    """
    text = RemoteBox.install_script("amd64", "apt")
    start = text.index("manifest=")
    block = text[start : text.index("fi\n", start) + 3].replace(
        remote_settings.BOX_RELAY_DIR, str(tmp_path)
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"apt-get": "exit 0", "haproxy": "exit 0"}.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    manifest = tmp_path / "MANIFEST"

    def run(with_haproxy: bool) -> str:
        # The stub apt-get comes first on PATH either way: the real one never runs.
        script = block if with_haproxy else block.replace("command -v haproxy", "false")
        subprocess.run(
            ["bash", "-euc", script], env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, check=True
        )
        return manifest.read_text()

    assert run(with_haproxy=False) == "haproxy=installed\n"
    assert run(with_haproxy=True) == "haproxy=installed\n", "the second run keeps it ours"
    manifest.write_text("")
    assert run(with_haproxy=True) == "haproxy=present\n", (
        "a haproxy found on a fresh box is the owner's"
    )


def test_the_tunnel_account_may_only_listen_on_the_boxs_loopback():
    line = RemoteTunnel.authorized_line("ssh-ed25519 AAAA mightling-remote-tunnel")
    assert line.startswith(
        'restrict,port-forwarding,permitlisten="127.0.0.1:8443",command="/usr/sbin/nologin" '
    )
    drop_in = RemoteBox.sshd_drop_in()
    assert "Match User mightling-tunnel" in drop_in and "AllowTcpForwarding remote" in drop_in
    assert "GatewayPorts no" in drop_in and "PermitTTY no" in drop_in


def test_probe_reads_the_boxs_facts(monkeypatch):
    out = f"arch=x86_64\nroot=yes\npackages=apt\nhost_key={HOST_KEY}\n"
    monkeypatch.setattr(
        RemoteBox,
        "_run",
        classmethod(lambda cls, argv, input_bytes=None, timeout=600: _done(argv, out)),
    )
    facts = RemoteBox.probe(BOX)
    assert facts == {"arch": "amd64", "root": "yes", "packages": "apt", "host_key": HOST_KEY}


def test_a_relay_binary_with_the_wrong_digest_is_never_sent(monkeypatch):
    calls = []

    def fake(cls, argv, input_bytes=None, timeout=600):
        calls.append(argv)
        if argv[:2] == ["docker", "create"]:
            return _done(argv, "c0ffee\n")
        if argv[:2] == ["docker", "cp"]:
            pathlib.Path(argv[3]).write_bytes(b"not the relay")
        return _done(argv)

    monkeypatch.setattr(RemoteBox, "_run", classmethod(fake))
    cert = RemoteSettings.write_private("certs/relay.crt", "c")
    assert not RemoteBox.deploy(BOX, "amd64", "apt", "s", cert, cert, "ssh-ed25519 AAAA t")
    assert not any(argv[0] == "ssh" for argv in calls)
    assert ["docker", "rm", "c0ffee"] in calls, "the scratch container is removed"


def test_deploy_copies_one_archive_then_runs_one_root_script(monkeypatch):
    calls = []
    binary = b"\x7fELF relay"
    monkeypatch.setitem(
        remote_settings.RELAY_BINARY_SHA256, "amd64", hashlib.sha256(binary).hexdigest()
    )
    monkeypatch.setitem(
        __import__("dreamference.remote.remote_box", fromlist=["x"]).RELAY_BINARY_SHA256,
        "amd64",
        hashlib.sha256(binary).hexdigest(),
    )

    def fake(cls, argv, input_bytes=None, timeout=600):
        calls.append((argv, input_bytes))
        if argv[:2] == ["docker", "create"]:
            return _done(argv, "c0ffee\n")
        if argv[:2] == ["docker", "cp"]:
            pathlib.Path(argv[3]).write_bytes(binary)
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
    assert (
        copy[0][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
        and copy[0][5] == BOX
    )
    assert copy[0][6].endswith("cat > /tmp/mightling-remote-stage.tgz")
    with tarfile.open(fileobj=io.BytesIO(copy[1]), mode="r:gz") as archive:
        names = set(archive.getnames())
        assert archive.extractfile("relay.env").read() == b"NB_AUTH_SECRET=s3cret\n"
        assert archive.extractfile("netbird-relay").read() == binary
        assert all(member.mode == 0o600 for member in archive.getmembers())
    assert names == {
        "netbird-relay",
        "relay.crt",
        "relay.key",
        "relay.env",
        "haproxy.cfg",
        "mightling-relay.service",
        "authorized_keys",
        "sshd-tunnel.conf",
    }
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
    assert (
        "StrictHostKeyChecking=yes" in unit
        and f"UserKnownHostsFile={RemoteTunnel.known_hosts()}" in unit
    )
    assert "ExitOnForwardFailure=yes" in unit and "BatchMode=yes" in unit and "-F /dev/null" in unit
    assert f"mightling-tunnel@{BOX}" in unit and "Restart=always" in unit


def test_the_known_hosts_file_holds_the_box_and_nothing_else():
    RemoteTunnel.write_known_host(BOX, HOST_KEY)
    assert (
        RemoteTunnel.known_hosts().read_text()
        == f"{BOX} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBoxHostKey\n"
    )


def test_install_enables_and_restarts_the_user_unit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        RemoteTunnel, "_run", classmethod(lambda cls, argv: calls.append(argv) or _done(argv))
    )
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
    monkeypatch.setitem(
        __import__("dreamference.remote.remote_node_peer", fromlist=["x"]).CLIENT_ARCHIVES,
        "arm64",
        hashlib.sha256(data).hexdigest(),
    )
    binary = RemoteNodePeer.client_binary()
    assert binary is not None and binary.read_bytes() == b"netbird binary"


def test_the_node_joins_under_its_own_name_with_the_ca_in_the_system_store(tmp_path):
    script = RemoteNodePeer.script(
        tmp_path / "netbird",
        tmp_path / "ca.crt",
        "https://relay.example.org:443",
        tmp_path / "node-setup-key",
        f"mightling-{NODE_ID}",
    )
    assert "install -m 0755" in script and "/usr/local/bin/netbird" in script
    assert (
        "/usr/local/share/ca-certificates/mightling-node-ca.crt" in script
        and "update-ca-certificates" in script
    )
    assert (
        "netbird up --management-url https://relay.example.org:443"
        f" --setup-key-file {tmp_path / 'node-setup-key'} --hostname mightling-{NODE_ID}" in script
    )
    assert "--setup-key " not in script


def test_enrol_runs_one_root_script_and_deletes_it(monkeypatch):
    ran = []
    monkeypatch.setattr(
        RemoteNodePeer, "installed", classmethod(lambda cls: "/usr/local/bin/netbird")
    )
    monkeypatch.setattr(
        RemoteNodePeer,
        "_run_root",
        classmethod(lambda cls, script, purpose, yes: ran.append(script.read_text()) or True),
    )
    assert RemoteNodePeer.enrol(
        RemoteCertificateAuthority.ca_certificate(), "https://b:443", "K", NODE_ID, yes=True
    )
    assert len(ran) == 1 and "install -m 0755" not in ran[0], "an installed client is kept"
    assert not RemoteSettings.path("node-peer.sh").exists(), "the setup key does not stay on disk"


# -- enrolment --------------------------------------------------------------------------------------


def test_the_proofs_match_the_shared_vectors():
    # The same vectors are in the launcher's tests (ling-rs/src/remote_join.rs).
    nonce = "00112233445566778899aabbccddeeff"
    assert (
        RemoteEnrolment.client_proof("12345678", nonce)
        == "5cc98f27431d7b9945a3a7d2215bb483ef347521c9a0106c73c40cc87a24129a"
    )
    assert (
        RemoteEnrolment.server_mac("12345678", nonce, '{"a": 1}')
        == "95aac94aa3d5918feea3606ca4501a53022e70dd0e1bd320ff66a2570228f9de"
    )
    assert len(RemoteEnrolment.new_code()) == 8 and RemoteEnrolment.new_code().isdigit()


def test_a_wrong_proof_gets_nothing_and_mints_nothing():
    minted = []
    request = {"nonce": "0" * 32, "proof": RemoteEnrolment.client_proof("87654321", "0" * 32)}
    assert RemoteEnrolment.answer("12345678", request, lambda c: minted.append(c) or {}) is None
    assert (
        RemoteEnrolment.answer("12345678", {"nonce": "zz" * 16, "proof": "x"}, lambda c: {}) is None
    )
    assert minted == []


def test_a_right_proof_gets_a_bundle_the_client_can_check():
    nonce = "ab" * 16
    request = {
        "nonce": nonce,
        "proof": RemoteEnrolment.client_proof("12345678", nonce),
        "client": "laptop",
    }
    answer = RemoteEnrolment.answer(
        "12345678", request, lambda client: {"setup_key": "K", "for": client}
    )
    assert json.loads(answer["bundle"]) == {"setup_key": "K", "for": "laptop"}
    assert answer["mac"] == RemoteEnrolment.server_mac("12345678", nonce, answer["bundle"])


@pytest.mark.parametrize(
    "address,allowed",
    [
        ("192.168.50.20", True),
        ("10.1.2.3", True),
        ("172.16.5.5", True),
        ("fe80::1%wlan0", True),
        ("127.0.0.1", True),
        ("::ffff:192.168.50.20", True),
        ("100.101.102.103", False),
        ("100.64.0.1", False),
        ("8.8.8.8", False),
        ("2001:4860::1", False),
        ("bogus", False),
    ],
)
def test_only_the_lan_may_enrol_never_the_overlay(address, allowed):
    assert RemoteEnrolment.lan_address(address) is allowed


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _post(port, body):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mightling/enrol", data=json.dumps(body).encode(), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_listener_serves_one_client_then_stops():
    port = _free_port()
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(
            result=RemoteEnrolment.serve(
                "12345678",
                lambda client: {"setup_key": "K"},
                port=port,
                minutes=1,
                host="127.0.0.1",
            )
        )
    )
    thread.start()
    time.sleep(0.3)
    status, body = _post(port, {"nonce": "1" * 32, "proof": "0" * 64})
    assert status == 401 and body["attempts_left"] == 9
    nonce = "2" * 32
    status, body = _post(
        port,
        {
            "nonce": nonce,
            "proof": RemoteEnrolment.client_proof("12345678", nonce),
            "client": "laptop",
        },
    )
    assert status == 200 and body["mac"] == RemoteEnrolment.server_mac(
        "12345678", nonce, body["bundle"]
    )
    thread.join(5)
    assert outcome["result"] == "enrolled:laptop"


def test_ten_wrong_proofs_withdraw_the_code():
    port = _free_port()
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(
            result=RemoteEnrolment.serve(
                "12345678",
                lambda client: pytest.fail("minted"),
                port=port,
                minutes=1,
                host="127.0.0.1",
            )
        )
    )
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
    monkeypatch.setattr(
        RemoteAccess, "_advertise", classmethod(lambda cls, name: log.append(("advertise", name)))
    )
    monkeypatch.setattr(
        RemoteBox,
        "probe",
        classmethod(
            lambda cls, d, ssh_to=None: (
                (log.append(("probe", d, ssh_to)) and None)
                or {"arch": "amd64", "root": "yes", "packages": "apt", "host_key": HOST_KEY}
            )
        ),
    )
    monkeypatch.setattr(
        RemoteBox,
        "deploy",
        classmethod(
            lambda cls, *a, ssh_to=None: log.append(("deploy", a[0], a[1], ssh_to)) or True
        ),
    )
    monkeypatch.setattr(
        RemoteCertificateAuthority,
        "ensure",
        classmethod(lambda cls, d: log.append(("ca", d)) or True),
    )

    def issue(cls, label, names):
        crt = RemoteSettings.write_private(f"certs/{label}.crt", f"{label} cert")
        key = RemoteSettings.write_private(f"certs/{label}.key", f"{label} key")
        log.append(("issue", label, tuple(names)))
        return crt, key

    monkeypatch.setattr(RemoteCertificateAuthority, "issue", classmethod(issue))
    monkeypatch.setattr(
        RemoteControlPlane,
        "start",
        classmethod(lambda cls, setup_pat=False: log.append(("start", setup_pat)) or True),
    )
    monkeypatch.setattr(
        RemoteControlPlane, "wait_ready", classmethod(lambda cls, d, seconds=120: True)
    )
    monkeypatch.setattr(RemoteControlPlane, "bootstrap", classmethod(lambda cls, d, n: "nbp_year"))
    monkeypatch.setattr(
        RemoteControlPlane,
        "setup_key",
        classmethod(lambda cls, d, t, n: log.append(("key", n)) or "KEY"),
    )
    monkeypatch.setattr(RemoteTunnel, "public_key", classmethod(lambda cls: "ssh-ed25519 AAAA t"))
    monkeypatch.setattr(
        RemoteTunnel, "install", classmethod(lambda cls, d: log.append(("tunnel", d)) or True)
    )
    monkeypatch.setattr(
        RemoteNodePeer,
        "enrol",
        classmethod(lambda cls, ca, url, key, node, yes: log.append(("join", url, key)) or True),
    )
    monkeypatch.setattr(
        RemoteAccess,
        "node_label",
        classmethod(lambda cls, d, t, name, seconds=30: f"{name}.netbird.selfhosted"),
    )
    return log


def test_setup_does_everything_in_order_and_records_names_only(seams):
    assert RemoteAccess.setup("Relay.Example.org.", yes=True) == 0
    steps = [entry[0] for entry in seams]
    assert steps == [
        "probe",
        "ca",
        "issue",
        "issue",
        "start",
        "start",
        "start",
        "deploy",
        "tunnel",
        "key",
        "join",
        "advertise",
    ]
    assert ("join", "https://relay.example.org:443", "KEY") in seams
    # Started plainly, once more allowing `/api/setup`'s token, then plainly again.
    assert [entry[1] for entry in seams if entry[0] == "start"] == [False, True, False]
    assert ("ca", "example.org") in seams
    assert len(RemoteSettings.read_private("store-key")) == 44
    record = RemoteSettings.read()
    assert record["ca_scope"] == "example.org"
    assert (
        record["dns_name"] == BOX
        and record["overlay_name"] == f"mightling-{NODE_ID}.netbird.selfhosted"
    )
    assert record["node_peer"] is True
    assert RemoteSettings.read_private("admin-token") == "nbp_year"
    assert len(RemoteSettings.read_private("relay-secret")) == 64
    assert RemoteTunnel.known_hosts().read_text().startswith(BOX + " ssh-ed25519 ")
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", RemoteSettings.path("remote.json").read_text()), (
        "no address is written"
    )


def test_setup_fails_loudly_when_management_renamed_the_node(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        RemoteAccess,
        "node_label",
        classmethod(lambda cls, d, t, name, seconds=30: f"{name}-1.netbird.selfhosted"),
    )
    assert RemoteAccess.setup(BOX, yes=True) == 1
    assert "not mightling-" in capsys.readouterr().out
    assert not RemoteSettings.read().get("node_peer"), "a rerun checks again"
    assert ("advertise", RemoteSettings.overlay_name(NODE_ID)) not in seams


def test_node_label_reads_managements_name_for_the_node(monkeypatch):
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    assert (
        RemoteAccess.node_label(BOX, "tok", f"mightling-{NODE_ID}")
        == f"mightling-{NODE_ID}.netbird.selfhosted"
    )
    assert RemoteAccess.node_label(BOX, "tok", "nobody", seconds=0) is None


def test_a_renewal_leaves_one_node_token(monkeypatch):
    requests = []

    def fake(cls, dns_name, method, path, body=None, token=None):
        requests.append((method, path))
        if method == "POST":
            return 200, {"plain_token": "nbp_new", "personal_access_token": {"id": "t3"}}
        if method == "GET":
            return 200, [
                {"id": "t1", "name": "setup-token"},
                {"id": "t2", "name": "mightling-node-20251010"},
                {"id": "t3", "name": "mightling-node-20261010"},
                {"id": "t9", "name": "someone else's"},
            ]
        return 200, {}

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    assert RemoteControlPlane.renew_token(BOX, "old", "u1") == "nbp_new"
    deleted = [path for method, path in requests if method == "DELETE"]
    assert deleted == ["/api/users/u1/tokens/t1", "/api/users/u1/tokens/t2"]


def test_setup_again_keeps_the_secret_and_does_not_rejoin(seams, monkeypatch):
    RemoteAccess.setup(BOX, yes=True)
    secret = RemoteSettings.read_private("relay-secret")
    seams.clear()
    monkeypatch.setattr(
        RemoteControlPlane, "wait_ready", classmethod(lambda cls, d, seconds=120: False)
    )
    monkeypatch.setattr(
        RemoteControlPlane, "renew_token", classmethod(lambda cls, d, t, user_id=None: "nbp_new")
    )
    assert RemoteAccess.setup(BOX, yes=True) == 0
    assert RemoteSettings.read_private("relay-secret") == secret
    assert "join" not in [entry[0] for entry in seams]
    assert RemoteSettings.read_private("admin-token") == "nbp_new"


@pytest.mark.parametrize(
    "name",
    ["", "relay", "http://relay.example.org", "relay.example.org:443", "user@relay.example.org",
     "a.example.org\tb", "*.example.org", "a,b.example.org", "rélay.example.org", "-a.example.org"],
)  # fmt: skip
def test_setup_wants_a_dns_name(seams, name):
    assert RemoteAccess.setup(name, yes=True) == 2
    assert seams == []


def test_setup_refuses_on_a_machine_that_is_not_a_node(seams, monkeypatch):
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: None))
    assert RemoteAccess.setup(BOX, yes=True) == 1 and seams == []


@pytest.mark.parametrize(
    "facts,reason",
    [
        ({"error": "Permission denied (publickey)"}, "did not work"),
        (
            {"arch": "amd64", "root": "no", "packages": "apt", "host_key": HOST_KEY},
            "passwordless sudo",
        ),
        ({"arch": "", "root": "yes", "packages": "apt", "host_key": HOST_KEY}, "amd64 nor arm64"),
        (
            {"arch": "amd64", "root": "yes", "packages": "", "host_key": HOST_KEY},
            "neither apt nor dnf",
        ),
        ({"arch": "amd64", "root": "yes", "packages": "apt", "host_key": ""}, "ed25519 host key"),
    ],
)
def test_setup_stops_before_changing_anything_when_the_box_will_not_do(
    seams, monkeypatch, capsys, facts, reason
):
    monkeypatch.setattr(RemoteBox, "probe", classmethod(lambda cls, d, ssh_to=None: facts))
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


def test_box_only_deploys_the_box_and_runs_nothing_on_the_node(seams, capsys):
    assert RemoteAccess.setup(BOX, yes=True, ssh_to="my-box", box_only=True) == 0
    steps = [entry[0] for entry in seams]
    assert steps == ["probe", "ca", "issue", "issue", "deploy"], (
        "no container, no tunnel, no key minted, no join, no advert"
    )
    assert ("probe", BOX, "my-box") in seams and ("deploy", BOX, "amd64", "my-box") in seams
    record = RemoteSettings.read()
    assert record["box_only"] is True and record["ssh"] == "my-box" and "node_peer" not in record
    assert RemoteTunnel.known_hosts().read_text().startswith(BOX + " ssh-ed25519 "), (
        "the tunnel pins the box by its DNS name"
    )
    assert RemoteSettings.read_private("admin-token") is None
    assert f"ling-admin remote setup {BOX} --ssh my-box" in capsys.readouterr().out


def test_setup_after_box_only_finishes_with_the_remembered_alias(seams):
    assert RemoteAccess.setup(BOX, yes=True, ssh_to="my-box", box_only=True) == 0
    seams.clear()
    assert RemoteAccess.setup(BOX, yes=True) == 0
    assert (
        ("probe", BOX, "my-box") in seams and ("tunnel", BOX) in seams and ("start", False) in seams
    )
    record = RemoteSettings.read()
    assert record["node_peer"] is True and record["ssh"] == "my-box" and "box_only" not in record


def test_box_only_on_a_finished_setup_keeps_its_record(seams):
    assert RemoteAccess.setup(BOX, yes=True) == 0
    seams.clear()
    assert RemoteAccess.setup(BOX, yes=True, box_only=True) == 0
    assert [entry[0] for entry in seams] == ["probe", "ca", "issue", "issue", "deploy"]
    record = RemoteSettings.read()
    assert record["node_peer"] is True and "box_only" not in record


def test_box_only_under_another_name_records_the_new_box(seams, monkeypatch, capsys):
    """The finished record was kept whole, so it went on naming the old box."""
    assert RemoteAccess.setup(BOX, yes=True) == 0
    monkeypatch.setattr(RemoteCertificateAuthority, "permits", classmethod(lambda cls, d: True))
    other = "relay2.example.org"
    capsys.readouterr()
    assert RemoteAccess.setup(other, yes=True, box_only=True) == 0
    record = RemoteSettings.read()
    assert record["dns_name"] == other and record["box_only"] is True
    assert f"ling-admin remote setup {other}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "destination", ["192.0.2.7", "root@192.0.2.7", "[2001:db8::1]", "two words", "-oProxyCommand=x"]
)
def test_the_ssh_destination_is_never_an_address(seams, destination, capsys):
    assert RemoteAccess.setup(BOX, yes=True, ssh_to=destination) == 2
    assert seams == [] and RemoteSettings.read() is None


def test_an_alias_reaches_ssh_but_the_box_keeps_its_dns_name(monkeypatch):
    calls = []
    monkeypatch.setattr(
        RemoteBox,
        "_run",
        classmethod(
            lambda cls, argv, input_bytes=None, timeout=600: (
                calls.append(argv) or _done(argv, "active\nactive\n")
            )
        ),
    )
    assert RemoteBox.unit_states(BOX, ssh_to="my-box") == {"haproxy": "active", "relay": "active"}
    assert RemoteBox.remove(BOX, ssh_to="my-box") is False
    assert [argv[-2] for argv in calls] == ["my-box", "my-box"]
    assert RemoteBox.unit_states(BOX) is not None and calls[-1][-2] == BOX


@pytest.mark.parametrize(
    "own, other, healthy_words",
    [
        ("verified", "closed", "reaches this node's control plane"),
        ("closed", "closed", "the tunnel is not up"),
        ("foreign", "closed", "something other than this node"),
        ("unreachable", "unreachable", "does not take a connection"),
        ("verified", "answered", "ANOTHER NAME GOT A TLS ANSWER"),
    ],
)
def test_the_public_port_line_says_what_443_did(own, other, healthy_words):
    assert healthy_words in RemoteBox.public_port_line({"own": own, "other": other})


def test_the_public_port_is_checked_with_the_boxs_name_and_another(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        RemoteBox,
        "_handshake",
        classmethod(
            lambda cls, d, port, name, ca, timeout=10, expected=None: (
                seen.append((d, port, name, ca)) or ("verified" if ca else "closed")
            )
        ),
    )
    ca = tmp_path / "ca.crt"
    assert RemoteBox.check_public_port(BOX, ca) == {"own": "verified", "other": "closed"}
    assert seen == [(BOX, 443, BOX, ca), (BOX, 443, "not-this-box.invalid", None)]


def _one_connection_server(certificate=None, key=None):
    """A scratch loopback listener for one connection: TLS with the given certificate, or a bare close."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def handshake(connection):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certificate), str(key))
        with (
            contextlib.suppress(ssl.SSLError, OSError),
            context.wrap_socket(connection, server_side=True),
        ):
            pass

    def serve():
        with listener:
            connection, _ = listener.accept()
            with connection:
                if certificate is not None:
                    handshake(connection)

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1]


@needs_openssl
def test_a_handshake_tells_the_node_from_a_stranger_and_from_nothing(tmp_path):
    RemoteCertificateAuthority.ensure(BOX)
    ca = RemoteCertificateAuthority.ca_certificate()
    node_certificate, node_key = RemoteCertificateAuthority.issue("control-plane", [BOX])
    assert (
        RemoteBox._handshake(
            "127.0.0.1", _one_connection_server(node_certificate, node_key), BOX, ca
        )
        == "verified"
    )
    stranger_certificate, stranger_key = tmp_path / "s.crt", tmp_path / "s.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-subj",
            f"/CN={BOX}",
            "-addext",
            f"subjectAltName=DNS:{BOX}",
            "-days",
            "1",
            "-keyout",
            str(stranger_key),
            "-out",
            str(stranger_certificate),
        ],
        check=True,
        capture_output=True,
    )
    assert (
        RemoteBox._handshake(
            "127.0.0.1", _one_connection_server(stranger_certificate, stranger_key), BOX, ca
        )
        == "foreign"
    )
    assert (
        RemoteBox._handshake(
            "127.0.0.1",
            _one_connection_server(stranger_certificate, stranger_key),
            "not-this-box.invalid",
            None,
        )
        == "answered"
    )
    assert RemoteBox._handshake("127.0.0.1", _one_connection_server(), BOX, ca) == "closed"
    assert RemoteBox._handshake("127.0.0.1", _free_port(), BOX, ca, timeout=2) == "unreachable"


def test_status_of_a_box_only_setup_names_the_missing_half(monkeypatch, capsys):
    RemoteSettings.write(
        {
            "dns_name": BOX,
            "overlay_name": f"mightling-{NODE_ID}.netbird.selfhosted",
            "box_only": True,
            "ssh": "my-box",
        }
    )
    monkeypatch.setattr(
        RemoteBox,
        "unit_states",
        classmethod(lambda cls, d, ssh_to=None: {"haproxy": "active", "relay": "active"}),
    )
    monkeypatch.setattr(
        RemoteBox,
        "check_public_port",
        classmethod(lambda cls, d, ca: {"own": "closed", "other": "closed"}),
    )
    monkeypatch.setattr(
        RemoteTunnel, "state", classmethod(lambda cls: pytest.fail("the tunnel was asked about"))
    )
    assert RemoteAccess.status() == 1
    out = capsys.readouterr().out
    assert (
        "haproxy active, relay active" in out
        and "not set up yet (--box-only)" in out
        and "--ssh my-box" in out
    )


def test_status_is_green_only_when_443_reaches_the_node(monkeypatch, capsys):
    _set_up()
    monkeypatch.setattr(
        RemoteBox,
        "unit_states",
        classmethod(lambda cls, d, ssh_to=None: {"haproxy": "active", "relay": "active"}),
    )
    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "active"))
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    port = {"own": "verified", "other": "closed"}
    monkeypatch.setattr(RemoteBox, "check_public_port", classmethod(lambda cls, d, ca: dict(port)))
    assert RemoteAccess.status() == 0
    port["own"] = "closed"
    assert RemoteAccess.status() == 1


def _set_up():
    RemoteSettings.write(
        {
            "dns_name": BOX,
            "overlay_name": f"mightling-{NODE_ID}.netbird.selfhosted",
            "node_peer": True,
        }
    )
    RemoteSettings.write_private("admin-token", "tok")


PEERS = [
    {
        "id": "p0",
        "name": f"mightling-{NODE_ID}",
        "dns_label": f"mightling-{NODE_ID}.netbird.selfhosted",
        "connected": True,
    },
    {
        "id": "p1",
        "name": "laptop",
        "dns_label": "laptop.netbird.selfhosted",
        "connected": True,
        "os": "Ubuntu",
    },
    {
        "id": "p2",
        "name": "laptop-2",
        "dns_label": "laptop-2.netbird.selfhosted",
        "connected": False,
        "last_seen": "t",
    },
]


def test_revoke_deletes_exactly_one_peer(monkeypatch):
    _set_up()
    deleted = []
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    monkeypatch.setattr(
        RemoteControlPlane,
        "delete_peer",
        classmethod(lambda cls, d, t, p: deleted.append(p) or True),
    )
    assert RemoteAccess.revoke("laptop") == 0 and deleted == ["p1"], (
        "an exact name wins over a prefix"
    )
    assert RemoteAccess.revoke("lap") == 1 and deleted == ["p1"], (
        "an ambiguous prefix deletes nothing"
    )
    assert RemoteAccess.revoke("mightling-") == 1 and deleted == ["p1"], (
        "the node itself is not revoked"
    )


def test_remove_keeps_the_ca_and_purge_deletes_everything(monkeypatch):
    _set_up()
    calls = []
    monkeypatch.setattr(
        RemoteAccess, "_advertise", classmethod(lambda cls, name: calls.append(("advertise", name)))
    )
    monkeypatch.setattr(
        RemoteBox,
        "remove",
        classmethod(lambda cls, d, ssh_to=None: calls.append(("box", d)) or True),
    )
    monkeypatch.setattr(RemoteTunnel, "remove", classmethod(lambda cls: calls.append(("tunnel",))))
    monkeypatch.setattr(
        RemoteControlPlane, "stop", classmethod(lambda cls: calls.append(("stop",)) or True)
    )
    monkeypatch.setattr(
        RemoteNodePeer, "leave", classmethod(lambda cls, yes: calls.append(("leave",)) or True)
    )
    monkeypatch.setattr(
        RemoteControlPlane, "remove_proxy", classmethod(lambda cls: calls.append(("proxy",)))
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "remove_timer", classmethod(lambda cls: calls.append(("timer",)))
    )
    assert RemoteAccess.remove(yes=True) == 0
    assert calls == [("box", BOX), ("tunnel",)] and RemoteSettings.read() is not None
    calls.clear()
    assert RemoteAccess.remove(purge=True, yes=True) == 0
    assert calls == [
        ("box", BOX),
        ("tunnel",),
        ("stop",),
        ("proxy",),
        ("timer",),
        ("leave",),
        ("advertise", None),
    ]
    assert not RemoteSettings.folder().exists()


def test_a_bundle_carries_names_the_ca_and_a_fresh_key(monkeypatch):
    _set_up()
    RemoteCertificateAuthority.ca_certificate().parent.mkdir(parents=True, exist_ok=True)
    RemoteCertificateAuthority.ca_certificate().write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(
        RemoteControlPlane,
        "setup_key",
        classmethod(lambda cls, d, t, n, ephemeral=False: f"KEY for {n}"),
    )
    bundle = RemoteAccess.bundle(RemoteSettings.read(), "tok", NODE_ID, "laptop")
    assert bundle["setup_key"] == "KEY for client laptop" and bundle["node"] == NODE_ID
    assert bundle["management_url"] == "https://relay.example.org:443"
    assert bundle["relay"] == "rels://relay.example.org:33080"
    assert bundle["overlay_name"] == f"mightling-{NODE_ID}.netbird.selfhosted" and bundle[
        "ca"
    ].startswith("-----BEGIN")


def _bundle_file_ready(monkeypatch):
    _set_up()
    RemoteCertificateAuthority.ca_certificate().parent.mkdir(parents=True, exist_ok=True)
    RemoteCertificateAuthority.ca_certificate().write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: NODE_ID))
    minted = []

    def setup_key(cls, dns_name, token, name, ephemeral=False, minutes=10):
        minted.append((name, ephemeral) if minutes == 10 else (name, ephemeral, minutes))
        return "SECRET-KEY-1"

    monkeypatch.setattr(RemoteControlPlane, "setup_key", classmethod(setup_key))
    monkeypatch.setattr(
        RemoteEnrolment,
        "serve",
        classmethod(lambda cls, *a, **k: pytest.fail("a bundle file opens no listener")),
    )
    return minted


def test_a_bundle_file_holds_an_ephemeral_peers_bundle_in_a_private_file(
    monkeypatch, tmp_path, capsys
):
    minted = _bundle_file_ready(monkeypatch)
    target = tmp_path / "bundle.json"
    assert RemoteAccess.bundle_file(str(target)) == 0
    assert minted == [("client bundle file", True)]
    assert target.stat().st_mode & 0o777 == 0o600
    bundle = json.loads(target.read_text())
    assert bundle["setup_key"] == "SECRET-KEY-1" and bundle["node"] == NODE_ID
    assert bundle["overlay_name"] == f"mightling-{NODE_ID}.netbird.selfhosted"
    assert bundle["management_url"] == "https://relay.example.org:443"
    assert bundle["ca"].startswith("-----BEGIN")
    assert "SECRET-KEY-1" not in capsys.readouterr().out


def test_a_ttl_lifetime_is_a_number_with_a_unit_up_to_a_day():
    parse = RemoteAccess.parse_ttl
    assert [parse(t) for t in ("90m", "2h", "1d", "45")] == [90, 120, 1440, 45]
    assert [parse(t) for t in ("25h", "2d", "0m", "", "h", "2 hours", "-5m")] == [None] * 7


def test_a_ttl_bundle_is_an_ordinary_peers_key_that_waits(monkeypatch, tmp_path):
    minted = _bundle_file_ready(monkeypatch)
    target = tmp_path / "bundle.json"
    assert RemoteAccess.bundle_file(str(target), ttl="2h", name="house-1") == 0
    assert minted == [("client house-1", False, 120)]
    assert json.loads(target.read_text())["setup_key"] == "SECRET-KEY-1"


def test_a_ttl_over_a_day_or_a_bad_name_mints_nothing(monkeypatch, tmp_path):
    minted = _bundle_file_ready(monkeypatch)
    assert RemoteAccess.bundle_file(str(tmp_path / "a"), ttl="25h") == 1
    assert RemoteAccess.bundle_file(str(tmp_path / "b"), join=True, name="Bad Name") == 1
    assert minted == [] and not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_a_join_file_is_one_private_line_that_decodes_to_a_valid_script(
    monkeypatch, tmp_path, capsys
):
    minted = _bundle_file_ready(monkeypatch)
    target = tmp_path / "join.txt"
    assert RemoteAccess.bundle_file(str(target), join=True, name="house-1", ssh_user="owner") == 0
    assert minted == [("client house-1", False, 120)]  # two hours, one use, not ephemeral
    assert target.stat().st_mode & 0o777 == 0o600
    text = target.read_text()
    assert text.count("\n") == 1 and text.startswith(" printf %s '")
    # The script reaches sudo on a pipe: never an argument sudo logs or /proc/<pid>/cmdline shows.
    assert text.rstrip("\n").endswith("| base64 -d | sudo bash -s") and "bash -c" not in text
    assert "SECRET-KEY-1" not in text  # only inside the base64
    assert "SECRET-KEY-1" not in capsys.readouterr().out
    encoded = text.split("'")[1]
    script = base64.b64decode(encoded).decode()
    assert "SECRET-KEY-1" in script and "--setup-key-file" in script
    assert "--setup-key " not in script and "set -x" not in script
    assert "BEGIN CERTIFICATE" in script  # no public certificate recorded: the CA travels
    path = tmp_path / "join.sh"
    path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
    assert "mightling-" in script
    assert "gx10-remote" in script and "User" in script
    # One function called last, so nothing in the script can read the rest of it as its input.
    assert script.startswith("join_main() {\nset -eu\n") and script.endswith("\n}\njoin_main\n")


def test_the_join_script_leaves_out_the_ca_when_the_certificate_is_public():
    bundle = {
        "management_url": "https://remote.example.org:443",
        "setup_key": "K",
        "overlay_name": "mightling-ab.netbird.selfhosted",
        "ca": "",
    }
    script = RemoteJoinScript.script(bundle)
    assert "update-ca-certificates" not in script and "ca.crt" not in script
    for arch in ("amd64", "arm64"):  # the laptop (x86-64) and the GB10s (arm64) both have a pin
        assert CLIENT_ARCHIVES[arch] in script
    assert set(re.findall(r"@[A-Z_0-9]+@", script)) == {"@ARCH@"}  # no placeholder left unfilled
    assert "netbird_0.80.0_linux_@ARCH@.tar.gz" in script


def test_a_bundle_file_never_overwrites_and_mints_nothing_when_refused(monkeypatch, tmp_path):
    minted = _bundle_file_ready(monkeypatch)
    target = tmp_path / "bundle.json"
    target.write_text("keep")
    assert RemoteAccess.bundle_file(str(target)) == 1
    assert RemoteAccess.bundle_file(str(tmp_path / "missing" / "bundle.json")) == 1
    assert target.read_text() == "keep" and minted == []


def test_a_bundle_file_needs_remote_access_set_up(tmp_path, capsys):
    assert RemoteAccess.bundle_file(str(tmp_path / "bundle.json")) == 3
    assert not (tmp_path / "bundle.json").exists()
    assert "not set up" in capsys.readouterr().out


def test_status_without_setup_says_so(capsys):
    assert RemoteAccess.status() == 3 and "not set up" in capsys.readouterr().out


# -- the advertisement and the CLI ------------------------------------------------------------------


def test_the_advert_carries_the_overlay_name_only_when_set_up():
    plain = NodeServiceFile.render(port=8000, node_id=NODE_ID, version="1.6.1")
    assert "remote=" not in plain
    with_remote = NodeServiceFile.render(
        port=8000, node_id=NODE_ID, version="1.6.1", remote="mightling-x.netbird.selfhosted"
    )
    assert NodeServiceFile.parse(with_remote)["remote"] == "mightling-x.netbird.selfhosted"
    NodeServiceFile.service_path.write_text(with_remote)
    assert NodeServiceFile.update(state="ready") is True
    assert NodeServiceFile.read()["remote"] == "mightling-x.netbird.selfhosted", (
        "a state change keeps it"
    )
    assert NodeServiceFile.update(remote=None) is True and "remote" not in NodeServiceFile.read()


def test_the_cli_has_the_remote_group():
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    parser = DreamferenceCLIController.build_parser()
    args = parser.parse_args(["remote", "setup", BOX, "--yes"])
    assert args.remote_command == "setup" and args.dns_name == BOX and args.yes
    assert args.ssh_to is None and not args.box_only
    staged = parser.parse_args(["remote", "setup", BOX, "--ssh", "my-box", "--box-only"])
    assert staged.ssh_to == "my-box" and staged.box_only
    assert parser.parse_args(["remote", "remove", "--purge"]).purge
    assert parser.parse_args(["remote", "code"]).bundle_file is None
    assert (
        parser.parse_args(["remote", "code", "--bundle-file", "/x/b.json"]).bundle_file
        == "/x/b.json"
    )
    assert "remote" in parser.command_groups


def test_the_first_start_fetches_the_geolocation_databases_once_then_has_no_route_out(monkeypatch):
    # Found on the node (2026-10-27): with no route out and no databases, the server stops at
    # "could not initialize geolocation service" and restarts forever.
    calls = []

    def run(cls, argv, timeout=300):
        calls.append(argv)
        if argv[:2] == ["docker", "run"] and "bridge" in argv:
            _geolocation_files()
        return _done(argv)

    monkeypatch.setattr(RemoteControlPlane, "_run", classmethod(run))
    assert not RemoteControlPlane.geolocation_ready()
    assert RemoteControlPlane.start(setup_pat=True)
    runs = [argv for argv in calls if argv[:2] == ["docker", "run"]]
    fetch, real = runs
    assert fetch[fetch.index("--network") + 1] == "bridge" and "--ip" not in fetch
    assert fetch[fetch.index("--restart") + 1] == "no"
    assert "-p" not in fetch and "NB_SETUP_PAT_ENABLED=true" not in fetch
    assert calls[calls.index(fetch) + 1] == ["docker", "rm", "-f", "dreamference-remote"]
    assert (
        real[real.index("--network") + 1] == "dreamference-remote"
        and "NB_SETUP_PAT_ENABLED=true" in real
    )
    assert RemoteControlPlane.geolocation_ready()


def test_no_databases_after_the_wait_is_a_failed_start(monkeypatch):
    calls = []
    monkeypatch.setattr(
        RemoteControlPlane,
        "_run",
        classmethod(lambda cls, argv, timeout=300: calls.append(argv) or _done(argv)),
    )
    monkeypatch.setattr("dreamference.remote.remote_control_plane.GEO_WAIT_S", 0.0)
    assert not RemoteControlPlane.start()
    assert sum(argv[:2] == ["docker", "run"] for argv in calls) == 1, "the real start never happens"
    assert calls[-1] == ["docker", "rm", "-f", "dreamference-remote"]


def _run_node_peer_script(tmp_path, monkeypatch, store_text: str) -> list[str]:
    """Runs the node's root script against stand-ins for every command; returns their calls."""
    import subprocess

    from dreamference.remote import remote_node_peer

    bin_dir, calls = tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    for name in ("netbird", "update-ca-certificates"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
        stub.chmod(0o755)
    install = bin_dir / "install"
    install.write_text(f'#!/bin/sh\necho "install $*" >> {calls}\ncp "$3" "$4"\n')
    install.chmod(0o755)
    ca, store = tmp_path / "ca.crt", tmp_path / "store.crt"
    ca.write_text("new CA\n")
    store.write_text(store_text)
    monkeypatch.setattr(remote_node_peer, "TRUST_STORE_FILE", str(store))
    script = RemoteNodePeer.script(
        None, ca, "https://remote.example.org:443", tmp_path / "key", f"mightling-{NODE_ID}"
    )
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    subprocess.run(["bash", "-c", script], env=env, check=True)
    return calls.read_text().splitlines()


def test_a_changed_ca_restarts_the_running_client_before_it_logs_in(tmp_path, monkeypatch):
    # A daemon started before the CA changed keeps the old roots (Go loads them once per
    # process): without the restart its login to the new management fails (2026-10-27).
    calls = _run_node_peer_script(tmp_path, monkeypatch, "old CA\n")
    assert any(call.startswith("install -m 0644") for call in calls)
    stop, start = calls.index("netbird service stop"), calls.index("netbird service start")
    down = calls.index("netbird down")
    up = next(i for i, call in enumerate(calls) if call.startswith("netbird up"))
    assert stop < start < down < up


def test_an_unchanged_ca_keeps_the_client_running_but_resets_a_stale_profile(tmp_path, monkeypatch):
    # A profile from an earlier control plane made `netbird up` answer "Already connected".
    calls = _run_node_peer_script(tmp_path, monkeypatch, "new CA\n")
    assert not any(call.startswith("install") for call in calls)
    assert "netbird service stop" not in calls
    down = calls.index("netbird down")
    assert any(call.startswith("netbird up") for call in calls[down:])


# -- the public certificate (spec §15) --------------------------------------------------------------

ACCOUNT = "https://acme-v02.api.letsencrypt.org/acme/acct/123"


def _lego_archive(binary=b"#!/bin/sh\n"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("lego")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
    return buffer.getvalue()


def _token():
    path = pathlib.Path(remote_settings.CLOUDFLARE_TOKEN_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("cf-secret-token\n")
    return path


def _account(staging=False, url=ACCOUNT):
    host = "acme-staging-v02.api.letsencrypt.org" if staging else "acme-v02.api.letsencrypt.org"
    path = RemotePublicCertificate.folder(staging) / "accounts" / host / "mightling-node"
    path.mkdir(parents=True, exist_ok=True)
    (path / "account.json").write_text(json.dumps({"registration": {"accountURL": url}}))


def test_lego_is_pinned_and_a_wrong_digest_installs_nothing(monkeypatch):
    archive = _lego_archive()
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    monkeypatch.setattr(RemotePublicCertificate, "_download", classmethod(lambda cls, url: archive))
    assert RemotePublicCertificate.lego() is None, "the digest is not the pinned one"
    monkeypatch.setitem(remote_settings.LEGO_ARCHIVES, "arm64", hashlib.sha256(archive).hexdigest())
    binary = RemotePublicCertificate.lego()
    assert binary is not None and binary.name == f"lego-{remote_settings.LEGO_VERSION}"
    assert oct(binary.stat().st_mode & 0o777) == "0o700"
    assert oct(RemotePublicCertificate.folder().stat().st_mode & 0o777) == "0o700"
    assert "v5.5.2" in remote_settings.LEGO_URL.format(version="5.5.2", arch="arm64")


def test_lego_gets_the_token_by_file_never_in_its_environment(monkeypatch):
    monkeypatch.setenv("CF_DNS_API_TOKEN", "leaked")
    env = RemotePublicCertificate.lego_env()
    assert "CF_DNS_API_TOKEN" not in env
    assert env["CF_DNS_API_TOKEN_FILE"].endswith(".config/dreamference/cloudflare-token")
    argv = RemotePublicCertificate.lego_argv(pathlib.Path("/x/lego"), ["run"], staging=True)
    assert argv[:2] == ["/x/lego", "run"]
    assert argv[argv.index("--server") + 1] == remote_settings.ACME_STAGING
    assert argv[argv.index("--path") + 1].endswith("remote/acme-staging")
    assert "--accept-tos" in argv and "--email" not in argv


def test_caa_allows_only_this_account_by_dns01_and_no_wildcard():
    records = RemotePublicCertificate.caa_records([ACCOUNT])
    assert records == [
        {
            "flags": 0,
            "tag": "issue",
            "value": f"letsencrypt.org; validationmethods=dns-01; accounturi={ACCOUNT}",
        },
        {"flags": 0, "tag": "issuewild", "value": ";"},
    ]


def _fake_cloudflare(monkeypatch, existing):
    calls = []

    def call(cls, method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/zones?name=remote."):
            return 200, {"result": []}
        if path.startswith("/zones?name="):
            return 200, {"result": [{"id": "z1"}]}
        if method == "GET":
            return 200, {"result": existing}
        return 200, {"result": {}}

    monkeypatch.setattr(RemotePublicCertificate, "_cloudflare", classmethod(call))
    return calls


def test_caa_is_written_once_and_left_alone_when_right(monkeypatch):
    calls = _fake_cloudflare(
        monkeypatch, [{"id": "old", "data": {"flags": 0, "tag": "issue", "value": "x"}}]
    )
    assert RemotePublicCertificate.ensure_caa("remote.example.org", [ACCOUNT])
    writes = [(m, p) for m, p, _ in calls if m != "GET"]
    assert writes == [
        ("POST", "/zones/z1/dns_records"),
        ("POST", "/zones/z1/dns_records"),
        ("DELETE", "/zones/z1/dns_records/old"),
    ]
    assert all(
        b["name"] == "remote.example.org" and b["type"] == "CAA" for m, _, b in calls if m == "POST"
    )
    right = [
        {"id": f"r{i}", "data": d}
        for i, d in enumerate(RemotePublicCertificate.caa_records([ACCOUNT]))
    ]
    calls = _fake_cloudflare(monkeypatch, right)
    assert RemotePublicCertificate.ensure_caa("remote.example.org", [ACCOUNT])
    assert all(m == "GET" for m, _, _ in calls)


def test_obtain_without_a_token_runs_nothing(monkeypatch):
    monkeypatch.setattr(
        RemotePublicCertificate, "_run", classmethod(lambda cls, *a, **k: pytest.fail("ran lego"))
    )
    assert not RemotePublicCertificate.obtain(BOX)


def test_obtain_registers_sets_caa_then_runs_lego_with_the_renewal_window(monkeypatch):
    _token()
    binary = RemotePublicCertificate.folder() / "bin" / "lego"
    monkeypatch.setattr(RemotePublicCertificate, "lego", classmethod(lambda cls: binary))
    order = []

    def run(cls, argv, env=None, timeout=600):
        order.append(argv[1])
        assert env["CF_DNS_API_TOKEN_FILE"] and "cf-secret-token" not in json.dumps(env)
        if argv[1] == "accounts":
            _account()
        else:
            assert argv[argv.index("--domains") + 1] == BOX
            assert argv[argv.index("--renew-days") + 1] == "30"
            certificate, key = RemotePublicCertificate.files(BOX)
            certificate.parent.mkdir(parents=True, exist_ok=True)
            certificate.write_text("crt")
            key.write_text("key")
        return _done(argv)

    monkeypatch.setattr(RemotePublicCertificate, "_run", classmethod(run))
    caa = []
    monkeypatch.setattr(
        RemotePublicCertificate,
        "ensure_caa",
        classmethod(lambda cls, d, urls: caa.append((d, urls)) or True),
    )
    assert RemotePublicCertificate.obtain(BOX)
    assert order == ["accounts", "run"] and caa == [(BOX, [ACCOUNT])]
    assert RemotePublicCertificate.obtain(BOX) and order[2:] == ["run"], "registered once"


@needs_openssl
def test_serving_restarts_the_control_plane_and_waits_for_the_new_leaf(monkeypatch):
    RemoteCertificateAuthority.ensure(BOX)
    certificate, key = RemoteCertificateAuthority.issue("control-plane", [BOX])
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: True))
    restarts = []
    monkeypatch.setattr(
        RemoteControlPlane, "restart", classmethod(lambda cls: restarts.append(1) or True)
    )
    served = iter([None, b"old", der])
    monkeypatch.setattr(
        RemotePublicCertificate, "_served_der", classmethod(lambda cls, h, p, n: next(served))
    )
    monkeypatch.setattr("dreamference.remote.remote_public_certificate.RELOAD_POLL_S", 0)
    assert RemotePublicCertificate.serve(BOX, certificate.read_text(), key.read_text())
    assert restarts == [1]
    crt, tls_key = RemotePublicCertificate.served_files()
    assert (
        oct(tls_key.stat().st_mode & 0o777) == "0o600"
        and crt.read_text() == certificate.read_text()
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "_served_der", classmethod(lambda cls, h, p, n: b"old")
    )
    monkeypatch.setattr("dreamference.remote.remote_public_certificate.RELOAD_WAIT_S", 0.05)
    assert not RemotePublicCertificate.serve(BOX, certificate.read_text(), key.read_text())


def test_the_restart_keeps_the_container_as_it_is(monkeypatch):
    seen = []
    monkeypatch.setattr(
        RemoteControlPlane,
        "_run",
        classmethod(lambda cls, argv, timeout=300: seen.append(argv) or _done(argv)),
    )
    assert RemoteControlPlane.restart()
    assert seen == [["docker", "restart", "-t", "10", "dreamference-remote"]]


@needs_openssl
def test_fingerprint_and_description_read_the_leaf(monkeypatch):
    RemoteCertificateAuthority.ensure(BOX)
    certificate, _ = RemoteCertificateAuthority.issue("control-plane", [BOX])
    chain = certificate.read_text() + RemoteCertificateAuthority.ca_certificate().read_text()
    leaf = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
    assert RemotePublicCertificate.fingerprint(chain) == leaf
    assert RemotePublicCertificate.fingerprint(certificate) == leaf
    assert RemotePublicCertificate.fingerprint("nothing") is None
    facts = RemotePublicCertificate.describe(certificate)
    assert facts["issuer"] == "Mightling Mightling node CA" and 800 < facts["days"] <= 825


@needs_openssl
def test_with_the_public_certificate_443_must_serve_exactly_the_installed_leaf(monkeypatch):
    RemoteCertificateAuthority.ensure(BOX)
    ca = RemoteCertificateAuthority.ca_certificate()
    certificate, key = RemoteCertificateAuthority.issue("control-plane", [BOX])
    # The node's CA stands in for the system store here.
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    leaf = RemotePublicCertificate.fingerprint(certificate)
    port = _one_connection_server(certificate, key)
    assert RemoteBox._handshake("127.0.0.1", port, BOX, None, expected=leaf) == "verified"
    port = _one_connection_server(certificate, key)
    assert RemoteBox._handshake("127.0.0.1", port, BOX, None, expected="0" * 64) == "foreign"


def _public_set_up():
    _set_up()
    record = RemoteSettings.read()
    record["public_certificate"] = True
    RemoteSettings.write(record)


def test_a_staging_certificate_is_never_served(monkeypatch, capsys):
    _set_up()
    monkeypatch.setattr(
        RemotePublicCertificate, "obtain", classmethod(lambda cls, d, staging=False: staging)
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "describe",
        classmethod(lambda cls, c: {"issuer": "(STAGING)", "days": 63}),
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "serve", classmethod(lambda cls, *a: pytest.fail("served"))
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "install_timer", classmethod(lambda cls: pytest.fail("timer"))
    )
    assert RemoteAccess.certificate_issue(staging=True) == 0
    assert not RemoteSettings.public_certificate()


def test_issuing_serves_records_and_arms_the_timer(monkeypatch):
    _set_up()
    steps = []
    monkeypatch.setattr(
        RemotePublicCertificate, "obtain", classmethod(lambda cls, d, staging=False: True)
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "describe",
        classmethod(lambda cls, c: {"issuer": "Let's Encrypt E8", "days": 63}),
    )
    monkeypatch.setattr(RemotePublicCertificate, "due", classmethod(lambda cls, d: True))
    monkeypatch.setattr(
        RemotePublicCertificate,
        "install",
        classmethod(lambda cls, d: steps.append("install") or True),
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "install_timer",
        classmethod(lambda cls: steps.append("timer") or True),
    )
    assert RemoteAccess.certificate_issue() == 0
    assert steps == ["install", "timer"] and RemoteSettings.public_certificate()


def test_renewal_restarts_only_for_a_new_certificate(monkeypatch):
    monkeypatch.setattr(
        RemotePublicCertificate,
        "obtain",
        classmethod(lambda cls, d, staging=False: pytest.fail("lego")),
    )
    _set_up()
    assert RemoteAccess.certificate_renew() == 0, "no public certificate: nothing to renew"
    _public_set_up()
    installs = []
    due = [False]
    monkeypatch.setattr(
        RemotePublicCertificate, "obtain", classmethod(lambda cls, d, staging=False: True)
    )
    monkeypatch.setattr(RemotePublicCertificate, "due", classmethod(lambda cls, d: due[0]))
    monkeypatch.setattr(
        RemotePublicCertificate, "describe", classmethod(lambda cls, c: {"issuer": "x", "days": 50})
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "install", classmethod(lambda cls, d: installs.append(d) or True)
    )
    assert RemoteAccess.certificate_renew() == 0 and installs == []
    due[0] = True
    assert RemoteAccess.certificate_renew() == 0 and installs == [BOX]


def test_off_serves_the_node_ca_again_and_stops_renewing(monkeypatch):
    _public_set_up()
    RemoteSettings.write_private("certs/control-plane.crt", "node-crt")
    RemoteSettings.write_private("certs/control-plane.key", "node-key")
    seen = []
    monkeypatch.setattr(
        RemotePublicCertificate, "remove_timer", classmethod(lambda cls: seen.append("timer"))
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "serve",
        classmethod(lambda cls, d, c, k: seen.append((d, c, k)) or True),
    )
    assert RemoteAccess.certificate_off() == 0
    assert (
        seen == ["timer", (BOX, "node-crt", "node-key")] and not RemoteSettings.public_certificate()
    )


def test_a_setup_rerun_keeps_the_public_certificate_in_front(monkeypatch):
    _public_set_up()
    certificate, key = RemotePublicCertificate.files(BOX)
    certificate.parent.mkdir(parents=True, exist_ok=True)
    certificate.write_text("public-crt")
    key.write_text("public-key")
    node = (
        RemoteSettings.write_private("certs/c.crt", "node-crt"),
        RemoteSettings.write_private("certs/c.key", "node-key"),
    )
    written = {}
    monkeypatch.setattr(
        RemoteControlPlane,
        "write_files",
        classmethod(lambda cls, d, s, k, c, key: written.update(c=c, key=key)),
    )
    monkeypatch.setattr(RemoteAccess, "_control_plane_token", classmethod(lambda cls, d, n: "tok"))
    run = {"dns_name": BOX, "secret": "s", "node_id": NODE_ID, "control": node}
    assert RemoteAccess._stage_control_plane(run)
    assert written == {"c": "public-crt", "key": "public-key"}


def test_the_renewal_timer_is_daily_in_the_batch_slice():
    units = RemotePublicCertificate.timer_units()
    service = units["mightling-remote-certificate.service"]
    assert "ExecStart=%h/.local/bin/ling-admin remote certificate renew" in service
    assert "Slice=mightling-batch.slice" in service and "Type=oneshot" in service
    assert "OnCalendar=" in units["mightling-remote-certificate.timer"]


def test_status_names_the_public_certificate_and_its_timer(monkeypatch, capsys):
    _public_set_up()
    monkeypatch.setattr(
        RemoteBox,
        "unit_states",
        classmethod(lambda cls, d, ssh_to=None: {"haproxy": "active", "relay": "active"}),
    )
    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "active"))
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: PEERS))
    monkeypatch.setattr(
        RemoteBox,
        "check_public_port",
        classmethod(lambda cls, d, ca: {"own": "verified", "other": "closed"}),
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "describe",
        classmethod(lambda cls, c: {"issuer": "Let's Encrypt E8", "days": 63}),
    )
    timer = ["active"]
    monkeypatch.setattr(RemotePublicCertificate, "timer_state", classmethod(lambda cls: timer[0]))
    assert RemoteAccess.status() == 0
    out = capsys.readouterr().out
    assert (
        "Public cert: Let's Encrypt E8, valid 63 more days" in out and "Renewal:    active" in out
    )
    timer[0] = "inactive"
    assert RemoteAccess.status() == 1, "nothing renews it"


def test_the_join_script_runs_against_stand_in_commands_and_never_shows_the_key(tmp_path):
    """The script as root on a bare machine, with every command it calls replaced by a stub."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    home = tmp_path / "home"
    home.mkdir()
    stubs = {
        "curl": 'echo "curl $*" >> "$LOG"; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; : > "$out"',
        "sha256sum": 'cat >/dev/null; echo "sha256sum" >> "$LOG"',
        "tar": 'echo "tar $*" >> "$LOG"; touch "${@: -2:1}/netbird" 2>/dev/null; true',
        "install": 'echo "install $*" >> "$LOG"',
        "systemctl": 'echo "systemctl $*" >> "$LOG"',
        "apt-get": 'echo "apt-get $*" >> "$LOG"',
        "update-ca-certificates": 'echo "update-ca" >> "$LOG"',
        "chown": "true",
        "getent": f'echo "u:x:1:1::{home}:/bin/bash"',
        "sshd": "true",
        "netbird": 'echo "netbird $*" >> "$LOG"; case "$1" in version) echo 0.0.1;; '
        'status) echo "Management: Connected";; up) cat "${@: -3:1}" >> "$LOG.key";; esac',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/bash\n{body}\n")
        stub.chmod(0o755)
    bundle = {
        "management_url": "https://remote.example.org:443",
        "setup_key": "SECRET-KEY-2",
        "overlay_name": "mightling-ab.netbird.selfhosted",
        "ca": "",
    }
    script = tmp_path / "join.sh"
    script.write_text(RemoteJoinScript.script(bundle, peer="house-2", ssh_user="owner"))
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "LOG": str(log),
        "SUDO_USER": "owner",
    }
    result = subprocess.run(
        ["bash", "-c", f"id() {{ echo 0; }}; export -f id; bash {script}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "SECRET-KEY-2" not in calls + result.stdout + result.stderr
    assert "--management-url https://remote.example.org:443" in calls
    assert "--hostname house-2" in calls and "--setup-key-file" in calls
    assert "/netbird_0.80.0_linux_" in calls and "sha256sum" in calls  # the archive was checked
    config = (home / ".ssh" / "config").read_text()
    assert "Host gx10-remote" in config and "HostName mightling-ab.netbird.selfhosted" in config
    assert "User owner" in config


# -- refusals and failures of the commands ----------------------------------------------------------


def _plane(monkeypatch, peers=PEERS, deleted=None, delete_ok=True):
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: peers))
    monkeypatch.setattr(
        RemoteControlPlane,
        "delete_peer",
        classmethod(
            lambda cls, d, t, p: (deleted if deleted is not None else []).append(p) or delete_ok
        ),
    )


def test_revoke_and_peers_need_remote_access_set_up(monkeypatch, capsys):
    _plane(monkeypatch)
    assert RemoteAccess.revoke("laptop") == 3
    assert RemoteAccess.peers() == 3
    assert capsys.readouterr().out.count("not set up") == 2
    RemoteSettings.write({"dns_name": BOX})
    assert RemoteAccess.peers() == 3, "no admin token yet"


def test_revoke_and_peers_report_a_management_that_does_not_answer(monkeypatch, capsys):
    _set_up()
    _plane(monkeypatch, peers=None)
    assert RemoteAccess.revoke("laptop") == 1
    assert RemoteAccess.peers() == 1
    assert capsys.readouterr().out.count("Management did not answer") == 2


def test_revoke_with_no_match_deletes_nothing_and_lists_the_peers(monkeypatch, capsys):
    _set_up()
    deleted = []
    _plane(monkeypatch, deleted=deleted)
    assert RemoteAccess.revoke("nobody") == 1 and deleted == []
    out = capsys.readouterr().out
    assert "No peer" in out and "laptop-2" in out


def test_revoke_says_so_when_management_refuses_the_deletion(monkeypatch, capsys):
    _set_up()
    _plane(monkeypatch, delete_ok=False)
    assert RemoteAccess.revoke("laptop-2") == 1
    assert "refused to delete" in capsys.readouterr().out


def test_peers_prints_one_line_each_sorted_by_name(monkeypatch, capsys):
    _set_up()
    _plane(monkeypatch)
    assert RemoteAccess.peers() == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == ["laptop", "laptop-2", f"mightling-{NODE_ID}"]
    assert "last seen t" in lines[1] and "connected" in lines[0]


def test_a_peer_with_missing_fields_is_listed_not_a_raise():
    assert RemoteAccess.peer_lines([{"id": "p"}, {}]) != []


def test_remove_declined_changes_nothing(monkeypatch, capsys):
    _set_up()
    monkeypatch.setattr(RemoteAccess, "_confirm", classmethod(lambda cls, q: False))
    monkeypatch.setattr(
        RemoteBox, "remove", classmethod(lambda cls, *a, **k: pytest.fail("removed the box"))
    )
    assert RemoteAccess.remove() == 1
    assert RemoteSettings.read() is not None and "Nothing changed" in capsys.readouterr().out


def test_remove_answered_by_end_of_input_changes_nothing(monkeypatch):
    _set_up()

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    monkeypatch.setattr(
        RemoteBox, "remove", classmethod(lambda cls, *a, **k: pytest.fail("removed the box"))
    )
    assert RemoteAccess.remove() == 1


def test_remove_with_nothing_set_up_is_a_quiet_success(capsys):
    assert RemoteAccess.remove(yes=True) == 0
    assert "not set up" in capsys.readouterr().out


def test_remove_fails_when_the_box_cannot_be_cleaned_but_still_removes_the_tunnel(monkeypatch):
    _set_up()
    calls = []
    monkeypatch.setattr(RemoteBox, "remove", classmethod(lambda cls, d, ssh_to=None: False))
    monkeypatch.setattr(RemoteTunnel, "remove", classmethod(lambda cls: calls.append("tunnel")))
    assert RemoteAccess.remove(yes=True) == 1 and calls == ["tunnel"]


@pytest.mark.parametrize("failing", ["stop", "leave"])
def test_a_purge_whose_control_plane_or_overlay_stays_is_not_reported_removed(
    monkeypatch, capsys, failing
):
    _set_up()
    monkeypatch.setattr(RemoteAccess, "_advertise", classmethod(lambda cls, name: None))
    monkeypatch.setattr(RemoteBox, "remove", classmethod(lambda cls, d, ssh_to=None: True))
    monkeypatch.setattr(RemoteTunnel, "remove", classmethod(lambda cls: None))
    monkeypatch.setattr(RemoteControlPlane, "stop", classmethod(lambda cls: failing != "stop"))
    monkeypatch.setattr(RemoteNodePeer, "leave", classmethod(lambda cls, yes: failing != "leave"))
    monkeypatch.setattr(RemoteControlPlane, "remove_proxy", classmethod(lambda cls: None))
    monkeypatch.setattr(RemotePublicCertificate, "remove_timer", classmethod(lambda cls: None))
    assert RemoteAccess.remove(purge=True, yes=True) == 1
    assert "✅" not in capsys.readouterr().out


def test_a_summary_names_the_tunnel_only_when_set_up(monkeypatch):
    assert RemoteAccess.summary() == {"set_up": False}
    _set_up()
    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "active"))
    summary = RemoteAccess.summary()
    assert summary["set_up"] is True and summary["tunnel"] == "active"
    assert summary["dns_name"] == BOX and summary["box_only"] is False


def test_a_bundle_file_is_not_made_when_management_mints_no_key(monkeypatch, tmp_path, capsys):
    _bundle_file_ready(monkeypatch)
    monkeypatch.setattr(RemoteControlPlane, "setup_key", classmethod(lambda cls, *a, **k: None))
    target = tmp_path / "bundle.json"
    assert RemoteAccess.bundle_file(str(target)) == 1
    assert not target.exists()


def test_a_bundle_file_that_appears_while_minting_is_not_overwritten(monkeypatch, tmp_path, capsys):
    _bundle_file_ready(monkeypatch)
    target = tmp_path / "bundle.json"

    def racing_key(cls, *a, **k):
        target.write_text("someone else's")
        return "SECRET-KEY-1"

    monkeypatch.setattr(RemoteControlPlane, "setup_key", classmethod(racing_key))
    assert RemoteAccess.bundle_file(str(target)) == 1
    assert target.read_text() == "someone else's"
    assert "SECRET-KEY-1" not in capsys.readouterr().out


def test_a_code_needs_remote_access_set_up_and_listens_on_nothing(monkeypatch, capsys):
    monkeypatch.setattr(
        RemoteEnrolment, "serve", classmethod(lambda cls, *a, **k: pytest.fail("listened"))
    )
    assert RemoteAccess.code() == 3
    assert "not set up" in capsys.readouterr().out


def _code_ready(monkeypatch, outcome):
    _set_up()
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: NODE_ID))

    def serve(cls, code, mint):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(RemoteEnrolment, "serve", classmethod(serve))


@pytest.mark.parametrize(
    "outcome, status, words",
    [
        ("enrolled:laptop", 0, "laptop has its bundle"),
        ("withdrawn", 1, "ten wrong attempts"),
        ("expired", 1, "expired"),
        (KeyboardInterrupt(), 1, "withdrawn"),
        (OSError("address in use"), 1, "Could not listen"),
    ],
)
def test_a_code_ends_with_a_status_and_a_word_for_every_outcome(
    monkeypatch, capsys, outcome, status, words
):
    _code_ready(monkeypatch, outcome)
    assert RemoteAccess.code() == status
    assert words in capsys.readouterr().out


def test_a_value_that_holds_another_placeholder_stays_inside_its_quotes(tmp_path):
    bundle = {
        "management_url": "https://remote.example.org:443",
        "setup_key": "k@PEER@",
        "overlay_name": "mightling-ab.netbird.selfhosted",
        "ca": "",
    }
    script = RemoteJoinScript.script(bundle, peer="p@SSH_USER@", ssh_user="u; touch PWN #")
    assignments = [line for line in script.splitlines() if re.match(r"(KEY|PEER|SSH_USER)=", line)]
    probe = "\n".join([*assignments, 'printf "%s\\n" "$KEY" "$PEER" "$SSH_USER"'])

    done = subprocess.run(
        ["bash", "-c", probe], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert not (tmp_path / "PWN").exists()
    assert done.stdout.splitlines() == ["k@PEER@", "p@SSH_USER@", "u; touch PWN #"]
