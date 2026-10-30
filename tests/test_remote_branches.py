"""Remote access, the refusals and rollbacks (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md).

Each test reaches one decision branch of the `remote` package that the other `test_remote_*.py`
files leave out: a command with no setup record, a step that fails and stops the ones after it,
a rollback that puts the box back. Nothing here reaches a real box, Docker, systemd, sudo or the
network: every class's seam is replaced, as in the other remote tests.
"""

import pytest

from dreamference.remote import (
    RemoteAccess,
    RemoteBox,
    RemoteCertificateAuthority,
    RemoteControlPlane,
    RemotePublicCertificate,
    RemoteRelayCertificate,
    RemoteSettings,
    RemoteTunnel,
)

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"
BOX = "relay.example.org"
RELAY = "turn.example.org"
NOT_SET_UP = "Remote access is not set up on this node"


def _set_up(**extra):
    RemoteSettings.write(
        {
            "dns_name": BOX,
            "overlay_name": f"mightling-{NODE_ID}.netbird.selfhosted",
            "node_peer": True,
            **extra,
        }
    )
    RemoteSettings.write_private("admin-token", "tok")


def _never(name):
    def fail(cls, *args, **kwargs):
        pytest.fail(f"{name} was called")

    return classmethod(fail)


# -- the certificate commands with nothing to act on ------------------------------------------------


@pytest.mark.parametrize("record", [None, {"dns_name": BOX, "box_only": True}])
def test_a_public_certificate_is_refused_before_the_node_is_set_up(monkeypatch, capsys, record):
    if record:
        RemoteSettings.write(record)
    monkeypatch.setattr(RemotePublicCertificate, "obtain", _never("obtain"))
    assert RemoteAccess.certificate_issue() == 3
    assert NOT_SET_UP in capsys.readouterr().out


@pytest.mark.parametrize("record", [None, {"dns_name": BOX, "box_only": True}])
def test_a_relay_certificate_is_refused_before_the_node_is_set_up(monkeypatch, capsys, record):
    if record:
        RemoteSettings.write(record)
    monkeypatch.setattr(RemoteRelayCertificate, "obtain", _never("obtain"))
    assert RemoteAccess.certificate_relay() == 3
    assert NOT_SET_UP in capsys.readouterr().out


def test_turning_the_relay_certificate_off_without_one_changes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(RemoteRelayCertificate, "off", _never("off"))
    assert RemoteAccess.certificate_relay_off() == 0
    _set_up()
    assert RemoteAccess.certificate_relay_off() == 0
    assert capsys.readouterr().out.count("The relay has no certificate of its own.") == 2


def test_a_relay_that_cannot_be_put_back_is_an_error(monkeypatch, capsys):
    _set_up(relay_name=RELAY)
    monkeypatch.setattr(RemoteRelayCertificate, "off", classmethod(lambda cls, record: False))
    assert RemoteAccess.certificate_relay_off() == 1
    assert "Could not put the relay back" in capsys.readouterr().out


def test_turning_the_public_certificate_off_without_a_setup_record_exits_3(monkeypatch, capsys):
    monkeypatch.setattr(RemotePublicCertificate, "serve", _never("serve"))
    assert RemoteAccess.certificate_off() == 3
    assert "not set up" in capsys.readouterr().out


def test_turning_the_public_certificate_off_needs_the_node_cas_files(monkeypatch, capsys):
    _set_up(public_certificate=True)
    monkeypatch.setattr(RemotePublicCertificate, "serve", _never("serve"))
    monkeypatch.setattr(RemotePublicCertificate, "remove_timer", _never("remove_timer"))
    assert RemoteAccess.certificate_off() == 1
    assert "certificate is missing" in capsys.readouterr().out
    assert RemoteSettings.read()["public_certificate"] is True, "the record is left as it was"


def test_a_control_plane_that_will_not_serve_the_node_cas_certificate_is_an_error(monkeypatch):
    _set_up(public_certificate=True)
    RemoteSettings.write_private("certs/control-plane.crt", "crt")
    RemoteSettings.write_private("certs/control-plane.key", "key")
    served = []
    monkeypatch.setattr(
        RemotePublicCertificate,
        "serve",
        classmethod(lambda cls, name, crt, key: served.append((name, crt, key)) or False),
    )
    monkeypatch.setattr(RemotePublicCertificate, "remove_timer", classmethod(lambda cls: None))
    assert RemoteAccess.certificate_off() == 1
    assert served == [(BOX, "crt", "key")]


# -- issuing ----------------------------------------------------------------------------------------


@pytest.fixture
def issuing(monkeypatch):
    """A set-up node whose public certificate steps all succeed; the test breaks one."""
    _set_up()
    steps = {"obtain": True, "due": True, "install": True, "timer": True}
    monkeypatch.setattr(
        RemotePublicCertificate,
        "obtain",
        classmethod(lambda cls, name, staging=False: steps["obtain"]),
    )
    monkeypatch.setattr(RemotePublicCertificate, "due", classmethod(lambda cls, name: steps["due"]))
    monkeypatch.setattr(
        RemotePublicCertificate, "install", classmethod(lambda cls, name: steps["install"])
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "install_timer", classmethod(lambda cls: steps["timer"])
    )
    monkeypatch.setattr(RemotePublicCertificate, "describe", classmethod(lambda cls, path: None))
    return steps


def test_a_certificate_the_acme_flow_did_not_give_stops_the_issue(issuing, monkeypatch):
    issuing["obtain"] = False
    monkeypatch.setattr(RemotePublicCertificate, "install", _never("install"))
    assert RemoteAccess.certificate_issue() == 1
    assert "public_certificate" not in RemoteSettings.read()


def test_a_control_plane_that_will_not_restart_with_the_certificate_is_not_recorded(issuing):
    issuing["install"] = False
    assert RemoteAccess.certificate_issue() == 1
    assert "public_certificate" not in RemoteSettings.read()


def test_a_renewal_timer_that_does_not_start_fails_the_issue_with_a_warning(issuing, capsys):
    issuing["timer"] = False
    assert RemoteAccess.certificate_issue() == 1
    assert "renewal timer did not start" in capsys.readouterr().out
    assert RemoteSettings.read()["public_certificate"] is True


def test_a_certificate_already_served_is_not_installed_again(issuing, monkeypatch):
    issuing["due"] = False
    monkeypatch.setattr(RemotePublicCertificate, "install", _never("install"))
    assert RemoteAccess.certificate_issue() == 0


# -- renewing ---------------------------------------------------------------------------------------


def test_a_failed_relay_renewal_is_a_failed_renewal(monkeypatch):
    _set_up(relay_name=RELAY)
    monkeypatch.setattr(RemoteRelayCertificate, "renew", classmethod(lambda cls, record: False))
    assert RemoteAccess.certificate_renew() == 1


# -- the relay's certificate ------------------------------------------------------------------------


@pytest.fixture
def relay_issuing(monkeypatch):
    """A relay certificate issue whose steps succeed; the test breaks one."""
    _set_up()
    steps = {"addresses": True, "obtain": True, "switch": True, "timer": True}
    monkeypatch.setattr(
        RemoteRelayCertificate,
        "ensure_addresses",
        classmethod(lambda cls, relay, box: steps["addresses"]),
    )
    monkeypatch.setattr(
        RemoteRelayCertificate,
        "obtain",
        classmethod(lambda cls, relay, staging=False: steps["obtain"]),
    )
    monkeypatch.setattr(
        RemoteRelayCertificate, "switch", classmethod(lambda cls, record, relay: steps["switch"])
    )
    monkeypatch.setattr(
        RemotePublicCertificate, "install_timer", classmethod(lambda cls: steps["timer"])
    )
    monkeypatch.setattr(RemotePublicCertificate, "describe", classmethod(lambda cls, path: None))
    return steps


def test_a_relay_name_whose_addresses_cannot_be_set_stops_before_the_challenge(
    relay_issuing, monkeypatch
):
    relay_issuing["addresses"] = False
    monkeypatch.setattr(RemoteRelayCertificate, "obtain", _never("obtain"))
    assert RemoteAccess.certificate_relay(name=RELAY) == 1


def test_a_relay_certificate_the_acme_flow_did_not_give_leaves_the_box_alone(
    relay_issuing, monkeypatch
):
    relay_issuing["obtain"] = False
    monkeypatch.setattr(RemoteRelayCertificate, "switch", _never("switch"))
    assert RemoteAccess.certificate_relay(name=RELAY) == 1


def test_a_relay_that_fails_to_switch_is_reported_as_put_back(relay_issuing, monkeypatch, capsys):
    relay_issuing["switch"] = False
    monkeypatch.setattr(RemotePublicCertificate, "install_timer", _never("install_timer"))
    assert RemoteAccess.certificate_relay(name=RELAY) == 1
    assert "put back on the node CA's certificate" in capsys.readouterr().out


def test_a_relay_whose_renewal_timer_does_not_start_fails_with_a_warning(relay_issuing, capsys):
    relay_issuing["timer"] = False
    assert RemoteAccess.certificate_relay(name=RELAY) == 1
    assert "renewal timer did not start" in capsys.readouterr().out


# -- setup stages -----------------------------------------------------------------------------------


def test_a_node_ca_that_cannot_be_made_stops_the_certificates(monkeypatch):
    monkeypatch.setattr(RemoteCertificateAuthority, "ensure", classmethod(lambda cls, scope: False))
    monkeypatch.setattr(RemoteCertificateAuthority, "issue", _never("issue"))
    run = {"scope": "example.org", "dns_name": BOX}
    assert RemoteAccess._stage_certificates(run) is False
    assert "control" not in run


@pytest.mark.parametrize("failing", ["control-plane", "relay"])
def test_a_certificate_that_cannot_be_issued_stops_setup_before_the_secret(monkeypatch, failing):
    monkeypatch.setattr(RemoteCertificateAuthority, "ensure", classmethod(lambda cls, scope: True))
    monkeypatch.setattr(
        RemoteCertificateAuthority,
        "issue",
        classmethod(lambda cls, label, names: None if label == failing else (label, label)),
    )
    run = {"scope": "example.org", "dns_name": BOX}
    assert RemoteAccess._stage_certificates(run) is False
    assert RemoteSettings.read_private("relay-secret") is None


@pytest.fixture
def owner_made(monkeypatch):
    """The two starts and waits of making the owner, recorded; the test breaks one."""
    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "absent"))
    log = []
    answers = {"start": [True, True], "ready": [{}, {}]}

    def start(cls, setup_pat=False):
        log.append(("start", setup_pat))
        return answers["start"].pop(0)

    def ready(cls, name, seconds=120):
        log.append(("ready",))
        return answers["ready"].pop(0)

    monkeypatch.setattr(RemoteControlPlane, "start", classmethod(start))
    monkeypatch.setattr(RemoteControlPlane, "wait_ready", classmethod(ready))
    monkeypatch.setattr(
        RemoteControlPlane,
        "bootstrap",
        classmethod(lambda cls, d, n: log.append(("bootstrap",)) or "nbp"),
    )
    return log, answers


@pytest.mark.parametrize(
    ("start", "ready"), [([False], [{}]), ([True], [None])], ids=["start", "ready"]
)
def test_an_owner_is_not_made_when_the_setup_start_fails(owner_made, start, ready):
    log, answers = owner_made
    answers["start"], answers["ready"] = list(start), list(ready)
    assert RemoteAccess._bootstrap_token(BOX, NODE_ID) is None
    assert ("bootstrap",) not in log and ("start", False) not in log


@pytest.mark.parametrize(
    ("start", "ready"), [([True, False], [{}]), ([True, True], [{}, None])], ids=["start", "ready"]
)
def test_a_token_is_not_given_when_the_closing_restart_fails(owner_made, start, ready):
    log, answers = owner_made
    answers["start"], answers["ready"] = list(start), list(ready)
    assert RemoteAccess._bootstrap_token(BOX, NODE_ID) is None
    assert ("bootstrap",) in log, "the owner was made on the first start"


# -- status -----------------------------------------------------------------------------------------


@pytest.fixture
def healthy(monkeypatch):
    """Everything `status` asks about is healthy; the test makes one thing not."""
    _set_up()
    box = {"units": {"haproxy": "active", "relay": "active"}}
    monkeypatch.setattr(
        RemoteBox, "unit_states", classmethod(lambda cls, d, ssh_to=None: box["units"])
    )
    monkeypatch.setattr(
        RemoteBox,
        "check_public_port",
        classmethod(lambda cls, d, ca: {"own": "verified", "other": "closed"}),
    )
    monkeypatch.setattr(RemoteTunnel, "state", classmethod(lambda cls: "active"))
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: []))
    return box


def test_status_without_a_setup_record_exits_3(capsys):
    assert RemoteAccess.status() == 3
    assert "not set up" in capsys.readouterr().out


def test_status_of_a_box_that_does_not_answer_over_ssh_is_unhealthy(healthy, capsys):
    healthy["units"] = None
    assert RemoteAccess.status() == 1
    assert "did not answer" in capsys.readouterr().out


def test_status_of_a_management_that_does_not_answer_is_unhealthy(healthy, monkeypatch, capsys):
    monkeypatch.setattr(RemoteControlPlane, "peers", classmethod(lambda cls, d, t: None))
    assert RemoteAccess.status() == 1
    assert "management did not answer" in capsys.readouterr().out


def test_status_of_a_public_certificate_that_cannot_be_read_is_unhealthy(
    healthy, monkeypatch, capsys
):
    _set_up(public_certificate=True)
    monkeypatch.setattr(RemotePublicCertificate, "describe", classmethod(lambda cls, path: None))
    monkeypatch.setattr(RemotePublicCertificate, "timer_state", classmethod(lambda cls: "active"))
    assert RemoteAccess.status() == 1
    assert "certificate cannot be read" in capsys.readouterr().out


def test_status_of_a_relay_certificate_that_cannot_be_read_is_unhealthy(
    healthy, monkeypatch, capsys
):
    _set_up(relay_name=RELAY)
    monkeypatch.setattr(RemotePublicCertificate, "describe", classmethod(lambda cls, path: None))
    monkeypatch.setattr(RemoteBox, "_handshake", classmethod(lambda cls, *a, **k: "verified"))
    assert RemoteAccess.status() == 1
    assert f"certificate for {RELAY} cannot be read" in capsys.readouterr().out


# -- remove -----------------------------------------------------------------------------------------


def test_a_purge_whose_box_cannot_be_cleaned_is_not_reported_removed(monkeypatch, capsys):
    _set_up()
    monkeypatch.setattr(RemoteAccess, "_advertise", classmethod(lambda cls, name: None))
    monkeypatch.setattr(RemoteBox, "remove", classmethod(lambda cls, d, ssh_to=None: False))
    monkeypatch.setattr(RemoteTunnel, "remove", classmethod(lambda cls: None))
    monkeypatch.setattr(RemoteControlPlane, "stop", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "remove_proxy", classmethod(lambda cls: None))
    monkeypatch.setattr(RemotePublicCertificate, "remove_timer", classmethod(lambda cls: None))
    monkeypatch.setattr(
        "dreamference.remote.RemoteNodePeer.leave", classmethod(lambda cls, yes: True)
    )
    assert RemoteAccess.remove(purge=True, yes=True) == 1
    out = capsys.readouterr().out
    assert "✅" not in out and "Not removed" not in out
    assert RemoteSettings.read() is None, "the node's own records are gone all the same"


# -- the CAA records --------------------------------------------------------------------------------

ACCOUNT = "https://acme-v02.api.letsencrypt.org/acme/acct/1"


def _caa_cloudflare(monkeypatch, existing, delete_status=200):
    calls = []

    def fake(cls, method, path, body=None):
        calls.append((method, path.rsplit("/", 1)[-1], body))
        if path.startswith("/zones?"):
            return 200, {"result": [{"id": "z1"}]}
        if method == "GET":
            return 200, {"result": existing}
        if method == "DELETE":
            return delete_status, {}
        return 200, {}

    monkeypatch.setattr(RemotePublicCertificate, "_cloudflare", classmethod(fake))
    return calls


def test_a_caa_record_already_present_is_not_added_again_and_an_allowed_one_is_kept(monkeypatch):
    kept, issuewild = RemotePublicCertificate.caa_records([ACCOUNT])
    existing = [
        {"id": "keep", "data": kept},
        {"id": "old", "data": {"flags": 0, "tag": "issue", "value": "evil-ca.example"}},
    ]
    calls = _caa_cloudflare(monkeypatch, existing)
    assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is True
    changes = [(method, last, body and body["data"]) for method, last, body in calls[2:]]
    assert changes == [("POST", "dns_records", issuewild), ("DELETE", "old", None)]


def test_a_caa_record_that_cannot_be_deleted_fails_after_the_new_ones_went_in(monkeypatch, caplog):
    stale = {"id": "old", "data": {"flags": 0, "tag": "issue", "value": "evil-ca.example"}}
    calls = _caa_cloudflare(monkeypatch, [stale], delete_status=403)
    with caplog.at_level("ERROR"):
        assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is False
    assert [method for method, *_ in calls[2:]] == ["POST", "POST", "DELETE"]
    assert "kept an old CAA record" in caplog.text
