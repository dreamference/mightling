"""The Google service's refusals: the grants it does not store and the tokens it does not hand out.

A message read names its account; one that does not is read from the first account holding it.
"""

import html
import imaplib
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from dreamference.chat import gmail_search_service as svc_mod
from dreamference.chat.gmail_search_service import (
    CREDENTIALS_NAME,
    DRIVE_SCOPE,
    OAUTH_STATES,
    GmailSearchService,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def service_port():
    port = _free_port()
    threading.Thread(
        target=GmailSearchService.serve, kwargs={"port": port, "secret": "s3cret"}, daemon=True
    ).start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return port
        except OSError:
            time.sleep(0.05)
    pytest.fail("the service did not start")


@pytest.fixture
def saved(monkeypatch):
    """Records every token the service would store, and stores none."""
    calls: list = []
    monkeypatch.setattr(
        GmailSearchService, "save_token", classmethod(lambda cls, *a, **k: calls.append(a) or True)
    )
    return calls


# Built before any test patches urllib.request.urlopen, so the test's own requests stay real.
_CLIENT = urllib.request.build_opener()


def _call(port: int, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _CLIENT.open(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _google(monkeypatch, token: dict, user: dict) -> None:
    def urlopen(request, timeout=10):
        if "oauth2.googleapis.com/token" in request.full_url:
            return _Response(json.dumps(token).encode())
        if "userinfo" in request.full_url:
            return _Response(json.dumps(user).encode())
        raise AssertionError(f"unexpected request to {request.full_url}")

    monkeypatch.setattr(svc_mod.urllib.request, "urlopen", urlopen)


def _state(name: str, app: str) -> str:
    OAUTH_STATES[name] = {"code_verifier": "v", "time": time.time(), "app": app}
    return f"/?code=c1&state={name}"


# -- completing a consent from a pasted URL ----------------------------------------------------


def test_a_pasted_grant_without_the_apps_scope_is_refused_and_not_stored(
    service_port, saved, monkeypatch
):
    # Drive was granted where Gmail was asked for: the account must not be stored as connected.
    _google(monkeypatch, {"access_token": "tok", "scope": DRIVE_SCOPE}, {"email": "u@x.com"})
    status, body = _call(
        service_port,
        "/api/google/oauth/complete",
        {"url": "http://localhost:8767" + _state("st-scope", "gmail")},
    )
    assert status == 400
    assert json.loads(body)["error"] == GmailSearchService.missing_scope_message("gmail", "u@x.com")
    assert saved == []


def test_a_pasted_grant_with_no_account_address_is_refused_and_not_stored(
    service_port, saved, monkeypatch
):
    _google(monkeypatch, {"access_token": "tok", "scope": svc_mod.GMAIL_SCOPE}, {})
    status, body = _call(
        service_port,
        "/api/google/oauth/complete",
        {"url": "http://localhost:8767" + _state("st-noemail", "gmail")},
    )
    assert status == 400 and json.loads(body)["error"] == "Invalid URL or exchange failed"
    assert saved == []


def test_a_pasted_url_without_a_code_or_a_known_state_exchanges_nothing(
    service_port, saved, monkeypatch
):
    _google(monkeypatch, {}, {})  # any request to Google fails the test
    for url in ("http://localhost:8767/?state=x", "http://localhost:8767/?code=c&state=unknown"):
        status, body = _call(service_port, "/api/google/oauth/complete", {"url": url})
        assert status == 400 and json.loads(body)["error"] == "Invalid URL or exchange failed"
    assert saved == []


def test_a_failed_exchange_or_a_broken_body_answers_400_with_the_reason(
    service_port, saved, monkeypatch
):
    def urlopen(request, timeout=10):
        raise OSError("token exchange down")

    monkeypatch.setattr(svc_mod.urllib.request, "urlopen", urlopen)
    status, body = _call(
        service_port,
        "/api/google/oauth/complete",
        {"url": "http://localhost:8767" + _state("st-down2", "gmail")},
    )
    assert status == 400 and "token exchange down" in json.loads(body)["error"]

    request = urllib.request.Request(
        f"http://127.0.0.1:{service_port}/api/google/oauth/complete",
        method="POST",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        _CLIENT.open(request, timeout=5)
    assert refused.value.code == 400
    assert saved == []


# -- the browser's own callback ----------------------------------------------------------------


def test_the_callback_shows_a_scope_refusal_and_stores_nothing(service_port, saved, monkeypatch):
    _google(monkeypatch, {"access_token": "tok", "scope": DRIVE_SCOPE}, {"email": "u@x.com"})
    status, page = _call(service_port, _state("st-cb-scope", "calendar"))
    assert status == 200
    refusal = GmailSearchService.missing_scope_message("calendar", "u@x.com")
    assert "access was not granted" in page and html.escape(refusal) in page
    assert saved == []


def test_the_callback_without_an_account_address_shows_an_error_and_stores_nothing(
    service_port, saved, monkeypatch
):
    _google(monkeypatch, {"access_token": "tok", "scope": svc_mod.GMAIL_SCOPE}, {"email": ""})
    status, page = _call(service_port, _state("st-cb-noemail", "gmail"))
    assert status == 200 and "Invalid state or token exchange failed" in page
    assert "connected" not in page
    assert saved == []


# -- the tokens handed to the tools ------------------------------------------------------------


def _store(directory, accounts: dict) -> None:
    (directory / CREDENTIALS_NAME).write_text(json.dumps({"accounts": accounts}))


def test_a_refresh_that_returns_no_access_token_drops_the_account(tmp_path, monkeypatch):
    directory = str(tmp_path)
    expired = {
        "email": "a@x.com",
        "access_token": GmailSearchService._seal("old", directory),
        "expires_at": 0,
        "refresh_token": GmailSearchService._seal("ref", directory),
    }
    _store(tmp_path, {"a@x.com": expired})
    before = (tmp_path / CREDENTIALS_NAME).read_text()
    monkeypatch.setattr(
        svc_mod.urllib.request,
        "urlopen",
        lambda req, timeout=10: _Response(json.dumps({"error": "invalid_grant"}).encode()),
    )
    assert GmailSearchService.credentials(directory) == []
    # The expired token is neither handed out nor overwritten.
    assert (tmp_path / CREDENTIALS_NAME).read_text() == before


def test_a_current_token_that_does_not_unseal_is_not_handed_out(tmp_path, monkeypatch):
    directory = str(tmp_path)
    good = GmailSearchService._seal("tok-good", directory)
    _store(
        tmp_path,
        {
            "bad@x.com": {"email": "bad@x.com", "access_token": "!!!", "expires_at": 9999999999},
            "good@x.com": {"email": "good@x.com", "access_token": good, "expires_at": 9999999999},
        },
    )
    monkeypatch.setattr(
        svc_mod.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("a current token needs no refresh"),
    )
    creds = GmailSearchService.credentials(directory)
    assert [(c["email"], c["access_token"]) for c in creds] == [("good@x.com", "tok-good")]


# -- reading one message -----------------------------------------------------------------------


class _Mailbox:
    def __init__(self, uid: bytes = b"", failure: Exception | None = None):
        self.uid_found, self.failure, self.searched = uid, failure, 0

    def uid(self, *args):
        self.searched += 1
        if self.failure:
            raise self.failure
        return "OK", [self.uid_found]


def test_a_message_of_a_named_account_is_read_only_from_that_account(monkeypatch):
    named = _Mailbox(failure=imaplib.IMAP4.error("connection reset"))
    other = _Mailbox(uid=b"7")
    monkeypatch.setattr(
        GmailSearchService, "_fetch_body", classmethod(lambda cls, conn, uid: {"body": "other"})
    )
    found = GmailSearchService._find_message({"a@x.com": named, "b@x.com": other}, "a@x.com", "42")
    assert found == {"error": "connection reset"}
    assert other.searched == 0


def test_a_message_without_an_account_is_read_from_the_first_account_that_holds_it(monkeypatch):
    failing = _Mailbox(failure=OSError("timed out"))
    unreadable = _Mailbox(uid=b"5")
    holding = _Mailbox(uid=b"9")
    bodies = {id(unreadable): {"error": "Message could not be read."}, id(holding): {"body": "hi"}}
    monkeypatch.setattr(
        GmailSearchService, "_fetch_body", classmethod(lambda cls, conn, uid: bodies[id(conn)])
    )
    connections = {"a@x.com": failing, "b@x.com": unreadable, "c@x.com": holding}
    assert GmailSearchService._find_message(connections, None, "42") == {"body": "hi"}
    assert [m.searched for m in (failing, unreadable, holding)] == [1, 1, 1]


def test_a_message_no_account_can_read_is_not_found(monkeypatch):
    monkeypatch.setattr(
        GmailSearchService,
        "_fetch_body",
        classmethod(lambda cls, conn, uid: {"error": "Message could not be read."}),
    )
    connections = {"a@x.com": _Mailbox(failure=OSError("down")), "b@x.com": _Mailbox(uid=b"5")}
    assert GmailSearchService._find_message(connections, None, "42") == {
        "error": "Message not found"
    }
