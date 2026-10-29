"""The Google service's state-changing POSTs refuse pages from other origins (security review 2026-10)."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from dreamference.chat.gmail_search_service import HOST_ORIGIN, GmailSearchService


def test_only_the_service_itself_and_programs_may_post():
    assert GmailSearchService.post_refusal(None) is None
    # The retired web chat's page (port 3000) is a foreign origin now.
    assert GmailSearchService.post_refusal("http://localhost:3000") is not None
    assert GmailSearchService.post_refusal(HOST_ORIGIN) is None
    assert GmailSearchService.post_refusal("http://127.0.0.1:8767") is None
    assert GmailSearchService.post_refusal("https://evil.example") is not None
    # A DNS-rebinding page reaches 127.0.0.1 but keeps its own origin.
    assert GmailSearchService.post_refusal("http://evil.example:8767") is not None
    assert GmailSearchService.post_refusal("null") is not None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def service_port():
    port = _free_port()
    thread = threading.Thread(
        target=GmailSearchService.serve, kwargs={"port": port, "secret": "s3cret"}, daemon=True
    )
    thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return port
        except OSError:
            time.sleep(0.05)
    pytest.fail("the service did not start")


def _post(port: int, path: str, origin=None) -> int:
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST",
        headers=headers,
        data=json.dumps({"email": "nobody@example.com"}).encode(),
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_a_foreign_page_cannot_disconnect_or_start_a_connection(service_port):
    assert _post(service_port, "/disconnect", origin="https://evil.example") == 403
    assert (
        _post(service_port, "/api/google/oauth/start?app=gmail", origin="https://evil.example")
        == 403
    )
    assert (
        _post(service_port, "/api/google/oauth/complete", origin="http://evil.example:8767") == 403
    )


def test_the_connect_page_and_programs_still_reach_disconnect(service_port):
    # No such account: the request is accepted and answers that nothing was deleted.
    assert _post(service_port, "/disconnect", origin=HOST_ORIGIN) in (200, 400)
    assert _post(service_port, "/disconnect") in (200, 400)


def _get(port: int, path: str, host=None) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_only_loopback_names_may_reach_the_service():
    assert GmailSearchService.host_refusal(None) is None
    for host in ("127.0.0.1:8767", "localhost:8767", "localhost", "[::1]:8767"):
        assert GmailSearchService.host_refusal(host) is None, host
    for host in ("evil.example:8767", "evil.example", "127.0.0.1.evil.example:8767", "[::1"):
        assert GmailSearchService.host_refusal(host) is not None, host


def test_a_rebinding_page_cannot_read_the_connected_accounts(service_port):
    # A page whose name resolves to 127.0.0.1 sends a same-origin GET with no Origin header; only
    # its Host gives it away. `/status` takes no secret and lists the connected accounts.
    assert _get(service_port, "/status") == 200
    assert _get(service_port, "/status", host="evil.example:8767") == 403
    assert _get(service_port, "/connect", host="evil.example:8767") == 403
    assert _post(service_port, "/disconnect") in (200, 400)


def test_a_service_started_without_a_secret_serves_nothing(monkeypatch):
    # A container made before the rename hands the secret over as PUFFIN_GMAIL_SECRET, which the
    # service no longer reads; restarted with today's code it must refuse, not serve mail openly.
    from dreamference.chat.gmail_search_service import AUTH_HEADER, NO_SECRET_ERROR

    monkeypatch.delenv("MIGHTLING_GMAIL_SECRET", raising=False)
    port = _free_port()
    threading.Thread(target=GmailSearchService.serve, kwargs={"port": port}, daemon=True).start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    for headers in ({}, {AUTH_HEADER: ""}):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/search?query=x", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 401
        assert json.load(refused.value)["error"] == NO_SECRET_ERROR


def _req_status(port: int, path: str, method: str = "GET", body=None, headers=None):
    hdrs = dict(headers or {})
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method, data=data, headers=hdrs
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")


def test_http_routes_cover_status_connect_and_oauth_flows(service_port, monkeypatch):
    import io
    from http import HTTPStatus

    from dreamference.chat import gmail_search_service as svc_mod
    from dreamference.chat.gmail_search_service import OAUTH_STATES

    monkeypatch.setattr(
        GmailSearchService,
        "status",
        classmethod(lambda cls: {"connected": False, "accounts": []}),
    )
    status_code, status_body = _req_status(service_port, "/status")
    assert status_code == HTTPStatus.OK and '"connected": false' in status_body

    conn_code, conn_html = _req_status(service_port, "/connect?app=drive")
    assert conn_code == HTTPStatus.OK and "Connect Google Drive" in conn_html

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as redir:
        opener.open(f"http://127.0.0.1:{service_port}/oauth/start", timeout=5)
    assert redir.value.code == HTTPStatus.FOUND

    start_code, start_body = _req_status(
        service_port, "/api/google/oauth/start?app=drive", method="POST"
    )
    assert start_code == HTTPStatus.OK and "auth_url" in json.loads(start_body)
    assert (
        _req_status(service_port, "/disconnect", method="POST", body="{bad-json")[0]
        == HTTPStatus.BAD_REQUEST
    )
    assert (
        _req_status(service_port, "/disconnect", method="POST", body="{}")[0]
        == HTTPStatus.BAD_REQUEST
    )
    assert (
        _req_status(service_port, "/unknown-post", method="POST", body="{}")[0]
        == HTTPStatus.NOT_FOUND
    )

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "oauth2.googleapis.com/token" in url:
            return FakeResp(
                json.dumps({"access_token": "tok", "scope": svc_mod.DRIVE_SCOPE}).encode()
            )
        if "userinfo" in url:
            return FakeResp(json.dumps({"email": "u@x.com"}).encode())
        return urllib.request.build_opener().open(req, timeout=timeout)

    OAUTH_STATES["st-ok"] = {"code_verifier": "v", "time": time.time(), "app": "drive"}
    monkeypatch.setattr(
        GmailSearchService, "accept_grant", classmethod(lambda cls, app, data, addr: None)
    )
    monkeypatch.setattr(svc_mod.urllib.request, "urlopen", fake_urlopen)

    # A pasted redirect URL completes the consent like the browser's own callback does.
    comp_ok, comp_body = _req_status(
        service_port,
        "/api/google/oauth/complete",
        method="POST",
        body=json.dumps({"url": "http://localhost:8767/?code=c1&state=st-ok"}),
    )
    assert comp_ok == HTTPStatus.OK and json.loads(comp_body)["email"] == "u@x.com"

    OAUTH_STATES["st-ok"] = {"code_verifier": "v", "time": time.time(), "app": "drive"}
    get_ok, html_ok = _req_status(service_port, "/?code=c1&state=st-ok")
    assert get_ok == HTTPStatus.OK and "Google Drive connected" in html_ok
    get_bad_st, html_bad = _req_status(service_port, "/?code=c1&state=missing")
    assert get_bad_st == HTTPStatus.OK and "Invalid state" in html_bad

    def broken_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "oauth2.googleapis.com/token" in url:
            raise OSError("token exchange down")
        return urllib.request.build_opener().open(req, timeout=timeout)

    monkeypatch.setattr(svc_mod.urllib.request, "urlopen", broken_urlopen)
    # A state is taken once: the second callback with st-ok is refused.
    get_again, html_again = _req_status(service_port, "/?code=c1&state=st-ok")
    assert get_again == HTTPStatus.OK and "Invalid state" in html_again
    OAUTH_STATES["st-down"] = {"code_verifier": "v", "time": time.time(), "app": "drive"}
    get_err, html_err = _req_status(service_port, "/?code=c1&state=st-down")
    assert get_err == HTTPStatus.OK and "token exchange down" in html_err


def test_http_routes_cover_authenticated_search_workspace_and_message(service_port, monkeypatch):
    from http import HTTPStatus

    from dreamference.chat import gmail_search_service as svc_mod
    from dreamference.chat.gmail_search_service import AUTH_HEADER

    assert (
        _req_status(service_port, "/search?query=hi", headers={AUTH_HEADER: "wrong"})[0]
        == HTTPStatus.UNAUTHORIZED
    )
    auth = {AUTH_HEADER: "s3cret"}
    assert _req_status(service_port, "/search", headers=auth)[0] == HTTPStatus.BAD_REQUEST

    monkeypatch.setattr(
        GmailSearchService, "search", classmethod(lambda cls, q, lim: {"messages": [], "lim": lim})
    )
    monkeypatch.setattr(GmailSearchService, "workspace", classmethod(lambda cls, p, q: {"ws": p}))
    monkeypatch.setattr(GmailSearchService, "message", classmethod(lambda cls, mid: {"mid": mid}))
    s_code, s_body = _req_status(service_port, "/search?query=hi&limit=bad", headers=auth)
    assert s_code == HTTPStatus.OK and json.loads(s_body)["lim"] == svc_mod.DEFAULT_RESULT_LIMIT
    w_code, w_body = _req_status(service_port, "/drive/search?q=doc", headers=auth)
    assert w_code == HTTPStatus.OK and json.loads(w_body)["ws"] == "/drive/search"
    m_code, m_body = _req_status(service_port, "/message/a%40x.com%7C42", headers=auth)
    assert m_code == HTTPStatus.OK and json.loads(m_body)["mid"] == "a@x.com|42"
    assert _req_status(service_port, "/unknown-get", headers=auth)[0] == HTTPStatus.NOT_FOUND


def test_the_shared_secret_is_compared_in_constant_time(service_port, monkeypatch):
    import hmac

    from dreamference.chat.gmail_search_service import AUTH_HEADER

    compared = []
    real = hmac.compare_digest

    def recording(a, b):
        compared.append(True)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", recording)
    request = urllib.request.Request(
        f"http://127.0.0.1:{service_port}/search?query=x", headers={AUTH_HEADER: "s3crex"}
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)
    assert refused.value.code == 401
    assert compared
