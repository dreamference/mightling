"""The Google service's state-changing POSTs refuse pages from other origins (security review 2026-10)."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from dreamference.chat.gmail_search_service import GmailSearchService, HOST_ORIGIN, ONYX_ORIGIN


def test_only_the_web_chat_and_the_service_itself_may_post():
    assert GmailSearchService.post_refusal(None) is None
    assert GmailSearchService.post_refusal(ONYX_ORIGIN) is None
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
    thread = threading.Thread(target=GmailSearchService.serve, kwargs={"port": port, "secret": "s3cret"},
                              daemon=True)
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
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", headers=headers,
                                     data=json.dumps({"email": "nobody@example.com"}).encode())
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_a_foreign_page_cannot_disconnect_or_start_a_connection(service_port):
    assert _post(service_port, "/disconnect", origin="https://evil.example") == 403
    assert _post(service_port, "/api/google/oauth/start?app=gmail", origin="https://evil.example") == 403
    assert _post(service_port, "/api/google/oauth/complete", origin="http://evil.example:8767") == 403


def test_the_web_chat_and_programs_still_reach_disconnect(service_port):
    # No such account: the request is accepted and answers that nothing was deleted.
    assert _post(service_port, "/disconnect", origin=ONYX_ORIGIN) in (200, 400)
    assert _post(service_port, "/disconnect") in (200, 400)
