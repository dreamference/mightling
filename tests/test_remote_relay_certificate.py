"""The relay's own public certificate (REMOTE_ACCESS §15.5): name, DNS, ACME account, the box, renewal."""

import io
import json
import pathlib
import re
import subprocess
import tarfile

import pytest

from dreamference.remote import (
    RemoteAccess,
    RemoteBox,
    RemoteControlPlane,
    RemotePublicCertificate,
    RemoteRelayCertificate,
    RemoteSettings,
    remote_settings,
)

BOX = "remote.example.org"
RELAY = "relay.example.org"
SECRET_TOKEN = "cf-secret-token"
RELAY_ACCOUNT = "https://acme-v02.api.letsencrypt.org/acme/acct/999"


def _done(argv, out="", code=0, err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


def _set_up(**extra):
    RemoteSettings.write({"dns_name": BOX, "ssh": "the-box", "node_peer": True, **extra})
    RemoteSettings.write_private("admin-token", "tok")


def _relay_files(certificate="relay-crt", key="relay-key"):
    crt, key_path = RemotePublicCertificate.files(RELAY)
    crt.parent.mkdir(parents=True, exist_ok=True)
    crt.write_text(certificate)
    key_path.write_text(key)
    return crt, key_path


def _fingerprint(monkeypatch, value="fp-1"):
    monkeypatch.setattr(RemotePublicCertificate, "fingerprint", classmethod(lambda cls, x: value))


# -- the name ---------------------------------------------------------------------------------------


def test_the_relay_gets_its_own_name_under_the_boxs_parent_domain():
    assert RemoteSettings.default_relay_name("remote.dreamference.ai") == "relay.dreamference.ai"
    _set_up()
    assert RemoteSettings.relay_address(BOX) == f"rels://{BOX}:33080", "no relay name yet"
    _set_up(relay_name=RELAY)
    assert RemoteSettings.relay_address(BOX) == f"rels://{RELAY}:33080"
    assert f"--exposed-address rels://{RELAY}:33080" in RemoteBox.relay_unit(BOX)
    config = RemoteControlPlane.render_config(BOX, "s", "k")
    assert f'- "rels://{RELAY}:33080"' in config
    assert f'exposedAddress: "https://{BOX}:443"' in config, "management stays on the box's name"


@pytest.mark.parametrize(
    ("name", "ok"),
    [(RELAY, True), (BOX, False), ("example.org", False), ("re lay.example.org", False)],
)
def test_the_relay_name_must_differ_from_the_control_planes(name, ok):
    assert RemoteRelayCertificate.valid_name(name, BOX) is ok


# -- DNS --------------------------------------------------------------------------------------------


def _cloudflare(monkeypatch, records):
    calls = []

    def call(cls, method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/zones?name="):
            return 200, {"result": [{"id": "z1"}]}
        if method == "GET":
            query = dict(p.split("=") for p in path.split("?")[1].split("&"))
            found = [
                r for r in records if r["name"] == query["name"] and r["type"] == query["type"]
            ]
            return 200, {"result": found}
        return 200, {"result": {}}

    monkeypatch.setattr(RemotePublicCertificate, "_cloudflare", classmethod(call))
    return calls


def test_the_relays_name_gets_the_boxs_addresses_unproxied(monkeypatch):
    calls = _cloudflare(
        monkeypatch,
        [
            {"id": "a", "name": BOX, "type": "A", "content": "192.0.2.7", "proxied": False},
            {"id": "b", "name": BOX, "type": "AAAA", "content": "2001:db8::7", "proxied": False},
        ],
    )
    assert RemoteRelayCertificate.ensure_addresses(RELAY, BOX)
    posts = [b for m, _, b in calls if m == "POST"]
    assert {(b["type"], b["name"], b["content"], b["proxied"]) for b in posts} == {
        ("A", RELAY, "192.0.2.7", False),
        ("AAAA", RELAY, "2001:db8::7", False),
    }


def test_addresses_already_right_are_left_alone(monkeypatch):
    calls = _cloudflare(
        monkeypatch,
        [
            {"id": "a", "name": BOX, "type": "A", "content": "192.0.2.7"},
            {"id": "c", "name": RELAY, "type": "A", "content": "192.0.2.7", "proxied": False},
        ],
    )
    assert RemoteRelayCertificate.ensure_addresses(RELAY, BOX)
    assert all(m == "GET" for m, _, _ in calls)


# -- the ACME account and the CAA record ------------------------------------------------------------


def test_the_relay_has_its_own_account_and_its_own_caa_record(monkeypatch):
    token = pathlib.Path(remote_settings.CLOUDFLARE_TOKEN_FILE).expanduser()
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(SECRET_TOKEN)
    token.chmod(0o600)
    binary = RemotePublicCertificate.folder() / "bin" / "lego"
    monkeypatch.setattr(RemotePublicCertificate, "lego", classmethod(lambda cls: binary))
    seen = []

    def run(cls, argv, env=None, timeout=600):
        seen.append(argv)
        assert argv[argv.index("--account-id") + 1] == "mightling-relay"
        assert SECRET_TOKEN not in json.dumps(env), "the token goes by file"
        if argv[1] == "accounts":
            path = (
                RemotePublicCertificate.folder()
                / "accounts"
                / "acme-v02.api.letsencrypt.org"
                / "mightling-relay"
            )
            path.mkdir(parents=True)
            (path / "account.json").write_text(
                json.dumps({"registration": {"accountURL": RELAY_ACCOUNT}})
            )
        else:
            crt, key = RemotePublicCertificate.files(RELAY)
            crt.parent.mkdir(parents=True, exist_ok=True)
            crt.write_text("c")
            key.write_text("k")
        return _done(argv)

    monkeypatch.setattr(RemotePublicCertificate, "_run", classmethod(run))
    caa = []
    monkeypatch.setattr(
        RemotePublicCertificate,
        "ensure_caa",
        classmethod(lambda cls, d, urls: caa.append((d, urls)) or True),
    )
    assert RemoteRelayCertificate.obtain(RELAY)
    assert caa == [(RELAY, [RELAY_ACCOUNT])], "only the relay's account may issue for its name"
    assert [a[1] for a in seen] == ["accounts", "run"]
    assert seen[1][seen[1].index("--domains") + 1] == RELAY


# -- the box ----------------------------------------------------------------------------------------


def test_the_box_gets_the_relays_certificate_key_and_unit_and_nothing_else(monkeypatch):
    _set_up(relay_name=RELAY)
    sent = []

    def fake(cls, argv, input_bytes=None, timeout=600):
        sent.append((argv, input_bytes))
        return _done(argv, out="mightling-box-ready\n")

    monkeypatch.setattr(RemoteBox, "_run", classmethod(fake))
    assert RemoteBox.update_relay(BOX, "CRT", "KEY", "the-box")
    (copy, archive), (root, script) = sent
    assert copy[-2] == "the-box" and root[-2] == "the-box", "the user's own ssh destination"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = sorted(tar.getnames())
        assert names == ["mightling-relay.service", "relay.crt", "relay.key"]
        assert tar.extractfile("relay.key").read() == b"KEY"
        assert f"rels://{RELAY}:33080".encode() in tar.extractfile("mightling-relay.service").read()
    text = script.decode()
    folder = re.fullmatch(r"umask 077; mkdir (\S+) && cat > \1/stage\.tgz", copy[-1]).group(1)
    assert (
        folder.startswith("/tmp/mightling-remote-stage-")
        and f"tar -xzf {folder}/stage.tgz " in text
    )
    assert "systemctl restart mightling-relay.service" in text
    for other in ("haproxy", "ufw", "firewall", "sshd", "authorized_keys", "apt"):
        assert other not in text, other


def test_a_failed_relay_restart_is_reported(monkeypatch):
    monkeypatch.setattr(
        RemoteBox,
        "_run",
        classmethod(lambda cls, argv, input_bytes=None, timeout=600: _done(argv, code=1)),
    )
    assert not RemoteBox.update_relay(BOX, "c", "k")


def test_setup_keeps_the_relays_public_certificate_on_the_box():
    node = (pathlib.Path("/n/relay.crt"), pathlib.Path("/n/relay.key"))
    _set_up()
    assert RemoteRelayCertificate.box_files(BOX, node) == node
    _set_up(relay_name=RELAY)
    assert RemoteRelayCertificate.box_files(BOX, node) == node, "no certificate files yet"
    files = _relay_files()
    assert RemoteRelayCertificate.box_files(BOX, node) == files


# -- the switch -------------------------------------------------------------------------------------


def _box(monkeypatch, *, updates=None, handshake="verified"):
    updates = [] if updates is None else updates
    monkeypatch.setattr(
        RemoteBox,
        "update_relay",
        classmethod(lambda cls, d, c, k, ssh=None: updates.append((d, c, k, ssh)) or True),
    )
    monkeypatch.setattr(RemoteBox, "_handshake", classmethod(lambda cls, *a, **k: handshake))
    _fingerprint(monkeypatch)
    return updates


def test_the_switch_pushes_the_relay_then_points_management_at_it(monkeypatch):
    _set_up()
    _relay_files()
    updates = _box(monkeypatch)
    steps = []
    monkeypatch.setattr(
        RemoteControlPlane,
        "rewrite_config",
        classmethod(lambda cls, d: steps.append(("config", RemoteSettings.relay_name(d))) or True),
    )
    RemoteSettings.write_private("server/tls.crt", "served-crt")
    RemoteSettings.write_private("server/tls.key", "served-key")
    monkeypatch.setattr(
        RemotePublicCertificate,
        "serve",
        classmethod(lambda cls, d, c, k: steps.append(("restart", c, k)) or True),
    )
    assert RemoteRelayCertificate.switch(RemoteSettings.read(), RELAY)
    assert updates == [(BOX, "relay-crt", "relay-key", "the-box")]
    assert steps == [("config", RELAY), ("restart", "served-crt", "served-key")]
    record = RemoteSettings.read()
    assert record["relay_name"] == RELAY and record["relay_pushed"] == "fp-1"


def test_a_relay_that_does_not_present_the_new_leaf_is_put_back(monkeypatch):
    _set_up()
    _relay_files()
    updates = _box(monkeypatch, handshake="foreign")
    monkeypatch.setattr(
        RemoteControlPlane, "rewrite_config", classmethod(lambda cls, d: pytest.fail("management"))
    )
    node_ca = (_node_ca("node-crt"), pathlib.Path(_node_ca("node-crt").with_suffix(".key")))
    monkeypatch.setattr(
        "dreamference.remote.remote_relay_certificate.RemoteCertificateAuthority.issue",
        classmethod(lambda cls, label, names: node_ca),
    )
    monkeypatch.setattr(RemotePublicCertificate, "serve", classmethod(lambda cls, *a: True))
    RemoteSettings.write_private("server/tls.crt", "x")
    RemoteSettings.write_private("server/tls.key", "y")
    monkeypatch.setattr(RemoteControlPlane, "rewrite_config", classmethod(lambda cls, d: True))
    assert not RemoteRelayCertificate.switch(RemoteSettings.read(), RELAY)
    assert updates[-1][1] == "node-crt", "the node CA's certificate went back to the box"
    assert "relay_name" not in RemoteSettings.read()


def _node_ca(text):
    path = RemoteSettings.write_private("certs/relay.crt", text)
    path.with_suffix(".key").write_text("node-key")
    return path


def test_off_restores_the_box_name_and_the_node_cas_certificate(monkeypatch):
    _set_up(relay_name=RELAY, relay_pushed="fp-1")
    crt = _node_ca("node-crt")
    monkeypatch.setattr(
        "dreamference.remote.remote_relay_certificate.RemoteCertificateAuthority.issue",
        classmethod(lambda cls, label, names: (crt, crt.with_suffix(".key"))),
    )
    sent = []
    monkeypatch.setattr(
        RemoteBox,
        "update_relay",
        classmethod(
            lambda cls, d, c, k, ssh=None: sent.append((c, RemoteSettings.relay_address(d))) or True
        ),
    )
    monkeypatch.setattr(RemoteControlPlane, "rewrite_config", classmethod(lambda cls, d: True))
    RemoteSettings.write_private("server/tls.crt", "x")
    RemoteSettings.write_private("server/tls.key", "y")
    monkeypatch.setattr(RemotePublicCertificate, "serve", classmethod(lambda cls, *a: True))
    assert RemoteAccess.certificate_relay_off() == 0
    assert sent == [("node-crt", f"rels://{BOX}:33080")]
    assert "relay_name" not in RemoteSettings.read()


# -- renewal and the alert --------------------------------------------------------------------------


def test_a_renewal_pushes_to_the_box_only_a_new_relay_certificate(monkeypatch):
    _set_up(relay_name=RELAY, relay_pushed="fp-1")
    _relay_files()
    _fingerprint(monkeypatch, "fp-1")
    monkeypatch.setattr(
        RemoteRelayCertificate, "obtain", classmethod(lambda cls, n, staging=False: True)
    )
    updates = _box(monkeypatch)
    _fingerprint(monkeypatch, "fp-1")
    assert RemoteRelayCertificate.renew(RemoteSettings.read())
    assert updates == [], "the box already has it: the relay is not restarted"
    _fingerprint(monkeypatch, "fp-2")
    assert RemoteRelayCertificate.renew(RemoteSettings.read())
    assert len(updates) == 1 and RemoteSettings.read()["relay_pushed"] == "fp-2"


def test_one_failing_renewal_does_not_stop_the_other_and_exits_non_zero(monkeypatch):
    _set_up(public_certificate=True, relay_name=RELAY)
    ran = []
    monkeypatch.setattr(
        RemoteAccess, "_renew_control_plane", classmethod(lambda cls, d: ran.append("cp") or False)
    )
    monkeypatch.setattr(
        RemoteRelayCertificate, "renew", classmethod(lambda cls, r: ran.append("relay") or True)
    )
    assert RemoteAccess.certificate_renew() == 1
    assert ran == ["cp", "relay"]


def test_a_failed_renewal_alerts_the_owner_through_the_notify_path():
    units = RemotePublicCertificate.timer_units()
    service = units["mightling-remote-certificate.service"]
    assert "OnFailure=mightling-remote-certificate-alert.service" in service
    alert = units["mightling-remote-certificate-alert.service"]
    assert "ExecStart=%h/.local/bin/owner-notify " in alert and "OnFailure" not in alert
    assert "Slice=mightling-batch.slice" in alert
    assert set(units) == {
        "mightling-remote-certificate.service",
        "mightling-remote-certificate-alert.service",
        "mightling-remote-certificate.timer",
    }


# -- the command ------------------------------------------------------------------------------------


def test_a_staging_relay_certificate_changes_nothing_on_the_box(monkeypatch):
    _set_up()
    _relay_files()
    monkeypatch.setattr(
        RemoteRelayCertificate, "ensure_addresses", classmethod(lambda cls, *a: True)
    )
    monkeypatch.setattr(
        RemoteRelayCertificate, "switch", classmethod(lambda cls, *a: pytest.fail("box"))
    )
    monkeypatch.setattr(
        RemoteRelayCertificate, "obtain", classmethod(lambda cls, n, staging=False: staging)
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "describe", classmethod(lambda cls, c: {"issuer": "S", "days": 1})
    )
    assert RemoteAccess.certificate_relay(staging=True, name=RELAY) == 0
    assert RemoteAccess.certificate_relay(name=BOX) == 2, "never the control plane's own name"
    assert "relay_name" not in RemoteSettings.read()


def test_a_setup_rerun_keeps_the_relay_and_certificate_records(monkeypatch):
    _set_up(public_certificate=True, relay_name=RELAY, relay_pushed="fp-1")
    overlay = "mightling-x.netbird.selfhosted"
    monkeypatch.setattr(RemoteAccess, "node_label", classmethod(lambda cls, *a, **k: overlay))
    monkeypatch.setattr(RemoteAccess, "_advertise", classmethod(lambda cls, name: None))
    run = {
        "dns_name": BOX, "overlay_name": overlay, "token": "t", "node_id": "x", "scope": "example.org",
        "ssh_to": "the-box", "existing": RemoteSettings.read(), "yes": True,
    }  # fmt: skip
    assert RemoteAccess._stage_node_peer(run)
    record = RemoteSettings.read()
    assert record["public_certificate"] is True
    assert (record["relay_name"], record["relay_pushed"]) == (RELAY, "fp-1")


def test_status_names_the_relay_certificate(monkeypatch, capsys):
    _set_up(relay_name=RELAY)
    _relay_files()
    monkeypatch.setattr(
        RemoteBox,
        "unit_states",
        classmethod(lambda cls, d, ssh_to=None: {"haproxy": "active", "relay": "active"}),
    )
    monkeypatch.setattr(
        RemoteBox,
        "check_public_port",
        classmethod(lambda cls, d, ca: {"own": "verified", "other": "closed"}),
    )
    monkeypatch.setattr(
        RemotePublicCertificate,
        "describe",
        classmethod(lambda cls, c: {"issuer": "LE", "days": 80}),
    )
    _fingerprint(monkeypatch)
    outcome = ["verified"]
    monkeypatch.setattr(RemoteBox, "_handshake", classmethod(lambda cls, *a, **k: outcome[0]))
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: []))
    from dreamference.remote import RemoteTunnel

    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "active"))
    RemoteAccess.status()
    out = capsys.readouterr().out
    assert f"Relay cert: LE, valid 80 more days, presented by the box ({RELAY})" in out
    outcome[0] = "foreign"
    assert RemoteAccess.status() == 1


def test_turning_the_control_planes_certificate_off_keeps_the_relays_renewal(monkeypatch):
    _set_up(public_certificate=True, relay_name=RELAY)
    RemoteSettings.write_private("certs/control-plane.crt", "node-crt")
    RemoteSettings.write_private("certs/control-plane.key", "node-key")
    monkeypatch.setattr(
        RemotePublicCertificate, "remove_timer", classmethod(lambda cls: pytest.fail("timer"))
    )
    monkeypatch.setattr(RemotePublicCertificate, "serve", classmethod(lambda cls, *a: True))
    assert RemoteAccess.certificate_off() == 0
    assert RemoteSettings.read()["public_certificate"] is False


@pytest.mark.parametrize(
    "name",
    ["-relay.example.org", "relay-.example.org", "rélay.example.org", "relay.exa_mple.org",
     "١٢٣.example.org", "a" * 64 + ".example.org", "re.lay..example.org"],
)  # fmt: skip
def test_a_relay_name_that_is_not_an_ascii_host_name_is_refused(name):
    assert RemoteRelayCertificate.valid_name(name, BOX) is False
