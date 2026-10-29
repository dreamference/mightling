"""lego, Cloudflare, the CAA record, the served certificate and the renewal timer.

Nothing here reaches Let's Encrypt, Cloudflare, Docker, systemd or a socket: `_run`, `_download`,
`_cloudflare`, `urllib.request.urlopen` and `socket.create_connection` are replaced, and the
node's folders are the test's own HOME.
"""

import datetime
import hashlib
import io
import json
import socket
import ssl
import stat
import subprocess
import tarfile
import urllib.error
import urllib.request

import pytest

from dreamference.remote import RemoteControlPlane, RemotePublicCertificate, RemoteSettings
from dreamference.remote import remote_public_certificate as module

BOX = "remote.example.org"
ACCOUNT = "https://acme-v02.api.letsencrypt.org/acme/acct/1"


def _done(argv, out="", code=0, err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


def _token(text="cf-secret-token\n"):
    from pathlib import Path

    path = Path(module.CLOUDFLARE_TOKEN_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commands(monkeypatch, results=None):
    """Replaces `_run`; `results` maps a word in the argv to a CompletedProcess."""
    calls = []

    def fake(cls, argv, env=None, timeout=0):
        calls.append(argv)
        for word, result in (results or {}).items():
            if word in argv:
                return result
        return _done(argv)

    monkeypatch.setattr(RemotePublicCertificate, "_run", classmethod(fake))
    return calls


def _archive(*members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# -- the seams


def test_a_missing_tool_or_a_timeout_is_a_failed_command_not_a_raise(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("openssl")

    monkeypatch.setattr(subprocess, "run", missing)
    assert RemotePublicCertificate._run(["openssl"]).returncode == 127

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert RemotePublicCertificate._run(["lego"]).returncode == 127


def test_a_failed_download_is_none_not_a_raise(monkeypatch):
    def refuse(url, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert RemotePublicCertificate._download("https://203.0.113.5/lego.tar.gz") is None


def test_cloudflare_without_a_token_makes_no_request(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("requested without a token")
    )
    status, body = RemotePublicCertificate._cloudflare("GET", "/zones")
    assert status == 0 and "no Cloudflare token" in body["errors"][0]["message"]


def test_cloudflare_gets_the_token_as_a_bearer_and_never_returns_it(monkeypatch):
    _token("cf-secret-token\n")
    seen = {}

    class Reply(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["data"] = request.data
        return Reply(b'{"result": []}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert RemotePublicCertificate._cloudflare("POST", "/x", {"a": 1}) == (200, {"result": []})
    assert seen["auth"] == "Bearer cf-secret-token" and json.loads(seen["data"]) == {"a": 1}


def test_a_cloudflare_error_page_that_is_not_json_is_an_empty_body(monkeypatch):
    _token()

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 502, "bad", {}, io.BytesIO(b"<html>"))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert RemotePublicCertificate._cloudflare("GET", "/zones") == (502, {})


def test_a_cloudflare_error_with_a_json_body_keeps_the_body(monkeypatch):
    _token()

    def refuse(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "no", {}, io.BytesIO(b'{"errors": [{"message": "denied"}]}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert RemotePublicCertificate._cloudflare("GET", "/zones") == (
        403,
        {"errors": [{"message": "denied"}]},
    )


def test_an_unreachable_or_garbled_cloudflare_is_status_zero_naming_only_the_error_type(
    monkeypatch,
):
    _token()

    class Garbled(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda r, timeout: Garbled(b"not json"))
    status, body = RemotePublicCertificate._cloudflare("GET", "/zones")
    assert status == 0 and body == {"errors": [{"message": "JSONDecodeError"}]}

    def down(request, timeout):
        raise urllib.error.URLError("cf-secret-token in a message")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    status, body = RemotePublicCertificate._cloudflare("GET", "/zones")
    assert status == 0 and "secret" not in json.dumps(body)


def test_a_cloudflare_reply_that_is_not_an_object_is_an_empty_body_not_a_crash(monkeypatch):
    _token()

    class Reply(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda r, timeout: Reply(b'["x"]'))
    assert RemotePublicCertificate._cloudflare("GET", "/zones") == (200, {})
    assert RemotePublicCertificate.zone_id(BOX) is None


def test_a_server_that_cannot_be_reached_has_no_served_certificate(monkeypatch):
    def refuse(address, timeout):
        raise ConnectionRefusedError()

    monkeypatch.setattr(socket, "create_connection", refuse)
    assert RemotePublicCertificate._served_der("127.0.0.1", 33443, BOX) is None


def test_the_token_is_none_when_missing_or_blank():
    assert RemotePublicCertificate.token() is None
    _token("  \n")
    assert RemotePublicCertificate.token() is None
    _token(" abc \n")
    assert RemotePublicCertificate.token() == "abc"


# -- reading certificates


def test_a_fingerprint_is_the_sha256_of_the_leafs_der_for_a_chain():
    leaf, other = b"leaf-der", b"intermediate-der"
    chain = ssl.DER_cert_to_PEM_cert(leaf) + ssl.DER_cert_to_PEM_cert(other)
    expected = hashlib.sha256(leaf).hexdigest()
    assert RemotePublicCertificate.fingerprint(chain) == expected
    assert RemotePublicCertificate.fingerprint(leaf) == expected
    path = RemoteSettings.path("chain.pem")
    RemoteSettings.write_private("chain.pem", chain)
    assert RemotePublicCertificate.fingerprint(path) == expected


def test_no_certificate_gives_no_fingerprint_and_garbage_is_not_a_raise():
    assert RemotePublicCertificate.fingerprint(None) is None
    assert RemotePublicCertificate.fingerprint(RemoteSettings.path("missing.pem")) is None
    assert RemotePublicCertificate.fingerprint("no certificate here") is None
    garbage = "-----BEGIN CERTIFICATE-----\n!!!not base64!!!\n-----END CERTIFICATE-----\n"
    assert RemotePublicCertificate.fingerprint(garbage) is None


def _enddate(years=1):
    when = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365 * years + 5)
    return when.strftime("notAfter=%b %d %H:%M:%S %Y GMT")


def test_a_certificates_issuer_and_days_left_are_read_from_openssl(monkeypatch):
    out = f"{_enddate()}\nissuer=\n    organizationName = Let's Encrypt\n    commonName = E7\n"
    _commands(monkeypatch, {"openssl": _done([], out)})
    facts = RemotePublicCertificate.describe(RemoteSettings.path("c.pem"))
    assert facts["issuer"] == "Let's Encrypt E7" and 360 <= facts["days"] <= 371


def test_an_issuer_with_no_name_is_called_unknown(monkeypatch):
    _commands(monkeypatch, {"openssl": _done([], _enddate() + "\n")})
    assert RemotePublicCertificate.describe(RemoteSettings.path("c.pem"))["issuer"] == "unknown"


@pytest.mark.parametrize(
    "out, code",
    [
        ("", 1),
        ("issuer=\n    commonName = X\n", 0),
        ("notAfter=next Tuesday\n", 0),
        ("garbage \x00 output", 0),
    ],
)
def test_an_openssl_reply_that_has_no_usable_end_date_is_none(monkeypatch, out, code):
    _commands(monkeypatch, {"openssl": _done([], out, code)})
    assert RemotePublicCertificate.describe(RemoteSettings.path("c.pem")) is None


# -- lego


def _pin(monkeypatch, data, arch="amd64"):
    monkeypatch.setattr(
        module.platform, "machine", lambda: "x86_64" if arch == "amd64" else "aarch64"
    )
    monkeypatch.setattr(RemotePublicCertificate, "_download", classmethod(lambda cls, url: data))
    monkeypatch.setitem(module.LEGO_ARCHIVES, arch, hashlib.sha256(data or b"").hexdigest())


def test_an_unsupported_architecture_downloads_no_lego(monkeypatch):
    monkeypatch.setattr(module.platform, "machine", lambda: "riscv64")
    monkeypatch.setattr(
        RemotePublicCertificate,
        "_download",
        classmethod(lambda cls, url: pytest.fail("downloaded")),
    )
    assert RemotePublicCertificate.lego() is None


def test_a_failed_lego_download_is_none(monkeypatch):
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(RemotePublicCertificate, "_download", classmethod(lambda cls, url: None))
    assert RemotePublicCertificate.lego() is None


@pytest.mark.parametrize(
    "members",
    [
        [("README", b"r")],
        [("lego", b"")],
        [("bin/lego", b"binary")],
    ],
)
def test_an_archive_without_a_usable_top_level_lego_installs_nothing(monkeypatch, members):
    _pin(monkeypatch, _archive(*members))
    assert RemotePublicCertificate.lego() is None
    assert not list(RemoteSettings.path("acme").rglob("lego-*"))


def test_lego_is_kept_private_and_executable_for_its_owner_only(monkeypatch):
    _pin(monkeypatch, _archive(("lego", b"#!/bin/sh\n")))
    binary = RemotePublicCertificate.lego()
    assert binary is not None and binary.read_bytes() == b"#!/bin/sh\n"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o700
    assert stat.S_IMODE(binary.parent.stat().st_mode) == 0o700
    monkeypatch.setattr(
        RemotePublicCertificate, "_download", classmethod(lambda cls, url: pytest.fail("again"))
    )
    assert RemotePublicCertificate.lego() == binary, "a binary already there is not fetched again"


# -- the ACME account


def _write_account(text, staging=False):
    host = "acme-staging-v02.api.letsencrypt.org" if staging else "acme-v02.api.letsencrypt.org"
    folder = RemotePublicCertificate.folder(staging) / "accounts" / host / module.ACCOUNT_ID
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "account.json").write_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"registration": "x"}',
        '{"registration": {"accountURL": 5}}',
        '{"registration": {"accountURL": "http://plain.example.org/acct"}}',
        '{"registration": {"accountURL": "file:///etc/passwd"}}',
    ],
)
def test_an_account_file_that_does_not_hold_an_https_url_has_no_account(text):
    _write_account(text)
    assert RemotePublicCertificate.account_url() is None


def test_an_existing_account_is_not_registered_again(monkeypatch):
    _write_account(json.dumps({"registration": {"accountURL": ACCOUNT}}))
    calls = _commands(monkeypatch)
    assert RemotePublicCertificate.register(RemoteSettings.path("lego"), False) == ACCOUNT
    assert calls == []


def test_a_failed_registration_is_none_and_its_log_hides_token_lines(monkeypatch, caplog):
    result = _done([], err="line one\nusing token cf-secret-token\nthe real error", code=1)
    _commands(monkeypatch, {"register": result})
    with caplog.at_level("ERROR"):
        assert RemotePublicCertificate.register(RemoteSettings.path("lego"), False) is None
    assert "the real error" in caplog.text and "cf-secret-token" not in caplog.text


def test_a_registration_that_leaves_no_account_file_is_none(monkeypatch):
    _commands(monkeypatch)
    assert RemotePublicCertificate.register(RemoteSettings.path("lego"), False) is None


def test_the_tail_of_a_failure_is_the_last_three_lines_cut_to_600_characters():
    result = _done([], err="\n".join(f"line {i}" for i in range(10)))
    assert RemotePublicCertificate._tail(result) == "line 7 | line 8 | line 9"
    assert len(RemotePublicCertificate._tail(_done([], out="x" * 2000))) == 600
    assert RemotePublicCertificate._tail(_done([])) == ""


# -- obtaining


def _obtain_ready(monkeypatch):
    _token()
    binary = RemoteSettings.path("lego")
    monkeypatch.setattr(RemotePublicCertificate, "lego", classmethod(lambda cls: binary))
    monkeypatch.setattr(
        RemotePublicCertificate, "register", classmethod(lambda cls, b, s, a=None: ACCOUNT)
    )
    monkeypatch.setattr(RemotePublicCertificate, "ensure_caa", classmethod(lambda cls, n, a: True))


def test_obtaining_stops_at_each_missing_step_and_runs_lego_only_at_the_end(monkeypatch):
    calls = _commands(monkeypatch)
    assert RemotePublicCertificate.obtain(BOX) is False, "no token"
    _obtain_ready(monkeypatch)
    monkeypatch.setattr(RemotePublicCertificate, "lego", classmethod(lambda cls: None))
    assert RemotePublicCertificate.obtain(BOX) is False, "no lego"
    _obtain_ready(monkeypatch)
    monkeypatch.setattr(
        RemotePublicCertificate, "register", classmethod(lambda cls, b, s, a=None: None)
    )
    assert RemotePublicCertificate.obtain(BOX) is False, "no account"
    _obtain_ready(monkeypatch)
    monkeypatch.setattr(RemotePublicCertificate, "ensure_caa", classmethod(lambda cls, n, a: False))
    assert RemotePublicCertificate.obtain(BOX) is False, "no CAA record"
    assert calls == [], "lego never ran before every check passed"


def test_a_lego_run_that_fails_or_leaves_no_certificate_is_false(monkeypatch):
    _obtain_ready(monkeypatch)
    _commands(monkeypatch, {"run": _done([], err="boom", code=1)})
    assert RemotePublicCertificate.obtain(BOX) is False
    _commands(monkeypatch)
    assert RemotePublicCertificate.obtain(BOX) is False, "exit 0 but no file"
    RemotePublicCertificate.files(BOX)[0].parent.mkdir(parents=True)
    RemotePublicCertificate.files(BOX)[0].write_text("crt")
    assert RemotePublicCertificate.obtain(BOX) is True


# -- CAA


def test_the_caa_records_allow_the_given_accounts_by_dns_01_and_forbid_wildcards():
    records = RemotePublicCertificate.caa_records([ACCOUNT + "2", ACCOUNT, ACCOUNT])
    assert [r["tag"] for r in records] == ["issue", "issue", "issuewild"]
    assert records[0]["value"].endswith(f"accounturi={ACCOUNT}")
    assert "validationmethods=dns-01" in records[0]["value"]
    assert records[-1]["value"] == ";"


def test_the_zone_is_looked_up_from_the_full_name_up_and_never_for_a_bare_tld(monkeypatch):
    asked = []

    def reply(cls, method, path, body=None):
        asked.append(path)
        if path.endswith("name=example.org"):
            return 200, {"result": [{"id": "z1"}]}
        return 200, {"result": []}

    monkeypatch.setattr(RemotePublicCertificate, "_cloudflare", classmethod(reply))
    assert RemotePublicCertificate.zone_id("A.Remote.Example.org.") == "z1"
    assert asked == [
        "/zones?name=a.remote.example.org",
        "/zones?name=remote.example.org",
        "/zones?name=example.org",
    ]
    asked.clear()
    assert RemotePublicCertificate.zone_id("host.invalid.test") is None
    assert "/zones?name=test" not in asked


def _cloudflare(monkeypatch, zone, existing, post_status=200):
    calls = []

    def fake(cls, method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/zones?"):
            return 200, {"result": [{"id": "z1"}] if zone else []}
        if method == "GET":
            return (200, {"result": existing}) if existing is not None else (500, {})
        if method == "POST":
            return post_status, {"errors": [{"message": "no"}, "junk"]}
        return 200, {}

    monkeypatch.setattr(RemotePublicCertificate, "_cloudflare", classmethod(fake))
    return calls


def test_no_zone_means_no_caa_record_is_written(monkeypatch):
    calls = _cloudflare(monkeypatch, zone=False, existing=[])
    assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is False
    assert all(method == "GET" for method, *_ in calls)


def test_a_caa_listing_that_fails_changes_nothing(monkeypatch):
    calls = _cloudflare(monkeypatch, zone=True, existing=None)
    assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is False
    assert [m for m, *_ in calls] == ["GET", "GET"]


def test_records_already_right_are_left_alone(monkeypatch):
    existing = [
        {"id": str(i), "data": data}
        for i, data in enumerate(RemotePublicCertificate.caa_records([ACCOUNT]))
    ]
    calls = _cloudflare(monkeypatch, zone=True, existing=existing)
    assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is True
    assert [m for m, *_ in calls] == ["GET", "GET"]


def test_wrong_records_are_deleted_and_the_right_ones_posted(monkeypatch):
    stale = [
        {"id": "old1", "data": {"flags": 0, "tag": "issue", "value": "evil-ca.example"}},
        "junk entry",
    ]
    calls = _cloudflare(monkeypatch, zone=True, existing=stale)
    assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is True
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in calls[2:]]
    assert methods[0] == ("DELETE", "old1") and [m for m, _ in methods[1:]] == ["POST", "POST"]


def test_a_refused_caa_record_is_false_and_the_log_names_the_messages(monkeypatch, caplog):
    _cloudflare(monkeypatch, zone=True, existing=[], post_status=403)
    with caplog.at_level("ERROR"):
        assert RemotePublicCertificate.ensure_caa(BOX, [ACCOUNT]) is False
    assert "refused a CAA record" in caplog.text and "['no']" in caplog.text


# -- the served certificate


def _serving(monkeypatch, running=True, restarted=True):
    monkeypatch.setattr(RemoteControlPlane, "running", classmethod(lambda cls: running))
    monkeypatch.setattr(RemoteControlPlane, "restart", classmethod(lambda cls: restarted))


def test_the_served_key_is_written_private_and_nothing_restarts_when_the_container_is_down(
    monkeypatch,
):
    _serving(monkeypatch, running=False)
    monkeypatch.setattr(
        RemoteControlPlane, "restart", classmethod(lambda cls: pytest.fail("restarted"))
    )
    assert RemotePublicCertificate.serve(BOX, "crt", "key") is True
    crt, key = RemotePublicCertificate.served_files()
    assert (crt.read_text(), key.read_text()) == ("crt", "key")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_a_restart_docker_refuses_is_a_failed_serve(monkeypatch):
    _serving(monkeypatch, restarted=False)
    assert RemotePublicCertificate.serve(BOX, "crt", "key") is False


def test_a_serve_succeeds_only_when_the_new_leaf_is_what_loopback_presents(monkeypatch):
    _serving(monkeypatch)
    pem = ssl.DER_cert_to_PEM_cert(b"new-leaf")
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    answers = iter([None, b"old-leaf", b"new-leaf"])
    monkeypatch.setattr(
        RemotePublicCertificate, "_served_der", classmethod(lambda cls, h, p, n: next(answers))
    )
    assert RemotePublicCertificate.serve(BOX, pem, "key") is True


def test_a_serve_that_never_shows_the_new_leaf_is_false_after_the_wait(monkeypatch, caplog):
    _serving(monkeypatch)
    clock = iter(range(0, 100000, 40))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        RemotePublicCertificate, "_served_der", classmethod(lambda cls, h, p, n: b"old-leaf")
    )
    with caplog.at_level("ERROR"):
        assert (
            RemotePublicCertificate.serve(BOX, ssl.DER_cert_to_PEM_cert(b"new-leaf"), "k") is False
        )
    assert "did not come back" in caplog.text


def test_the_served_certificate_is_due_when_it_differs_from_legos(monkeypatch):
    crt = RemotePublicCertificate.files(BOX)[0]
    crt.parent.mkdir(parents=True)
    crt.write_text(ssl.DER_cert_to_PEM_cert(b"one"))
    assert RemotePublicCertificate.due(BOX) is True, "nothing served yet"
    RemoteSettings.write_private("server/tls.crt", ssl.DER_cert_to_PEM_cert(b"one"))
    assert RemotePublicCertificate.due(BOX) is False
    RemoteSettings.write_private("server/tls.crt", ssl.DER_cert_to_PEM_cert(b"two"))
    assert RemotePublicCertificate.due(BOX) is True


# -- the renewal timer


def test_the_renewal_units_alert_the_owner_on_failure_and_run_in_the_batch_slice():
    units = RemotePublicCertificate.timer_units()
    service = units[module.CERTIFICATE_SERVICE_UNIT]
    assert f"OnFailure={module.CERTIFICATE_ALERT_UNIT}" in service
    assert "remote certificate renew" in service and "mightling-batch.slice" in service
    assert "Persistent=true" in units[module.CERTIFICATE_TIMER_UNIT]
    assert "owner-notify" in units[module.CERTIFICATE_ALERT_UNIT]


def test_the_timer_is_not_up_when_systemd_refuses_it(monkeypatch):
    calls = _commands(monkeypatch, {"enable": _done([], code=1)})
    assert RemotePublicCertificate.install_timer() is False
    assert len(calls) == 2
    for name in RemotePublicCertificate.timer_units():
        assert (RemoteControlPlane.unit_dir() / name).is_file()


def test_removing_the_timer_deletes_the_units_and_a_second_removal_is_harmless(monkeypatch):
    _commands(monkeypatch)
    RemotePublicCertificate.install_timer()
    calls = _commands(monkeypatch)
    RemotePublicCertificate.remove_timer()
    for name in RemotePublicCertificate.timer_units():
        assert not (RemoteControlPlane.unit_dir() / name).exists()
    assert calls[0][-1] == module.CERTIFICATE_TIMER_UNIT and calls[-1][-1] == "daemon-reload"
    RemotePublicCertificate.remove_timer()


def test_the_timers_state_is_systemds_word_or_unknown(monkeypatch):
    _commands(monkeypatch, {"is-active": _done([], "inactive\n", 3)})
    assert RemotePublicCertificate.timer_state() == "inactive"
    _commands(monkeypatch, {"is-active": _done([], "", 127)})
    assert RemotePublicCertificate.timer_state() == "unknown"
