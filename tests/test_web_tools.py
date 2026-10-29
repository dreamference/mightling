"""Tests for the MCP server's web tools: every request is a stand-in, nothing reaches the network."""

from unittest.mock import MagicMock

import pytest
import requests

from dreamference.mcp_server import web_tools
from dreamference.mcp_server.web_tools import MAX_DOWNLOAD_BYTES, WebTools


class Page:
    """Stands in for a streamed `requests.Response`."""

    def __init__(
        self, body: bytes, content_type: str = "text/html", encoding: str | None = "utf-8"
    ) -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.encoding = encoding
        self.url = "https://example.org/final"
        self.status_code = 200
        self.read = 0

    def raise_for_status(self) -> None:
        """Answers like a 200."""

    def iter_content(self, size: int):
        """Yields the body in chunks of `size`, counting what was read."""
        for start in range(0, len(self.body), size):
            self.read += size
            yield self.body[start : start + size]


@pytest.fixture
def online(monkeypatch):
    """The air gap is off and every request's `get` is a mock."""
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "off"))
    get = MagicMock()
    monkeypatch.setattr(web_tools._Session, "get", get)
    return get


def test_fetch_at_air_gap_on_makes_no_request(monkeypatch, online):
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "on"))
    outcome = WebTools.fetch("https://example.org")
    assert outcome["airgapped"] == "on" and outcome["error"] == web_tools.AIRGAPPED_ON_MESSAGE
    online.assert_not_called()


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "gopher://example.org/", "example.org", "http://"]
)
def test_fetch_refuses_anything_but_an_http_url_with_a_host_before_any_request(online, url):
    assert "error" in WebTools.fetch(url)
    online.assert_not_called()


def test_fetch_returns_a_failed_request_as_an_error(online):
    online.side_effect = requests.ConnectionError("refused")
    assert WebTools.fetch("https://example.org")["error"] == "request failed: refused"


def test_fetch_stops_reading_a_body_at_the_download_ceiling(online):
    page = Page(b"a" * (MAX_DOWNLOAD_BYTES * 2), content_type="text/plain")
    online.return_value = page
    outcome = WebTools.fetch("https://example.org", max_chars=10)
    assert page.read == MAX_DOWNLOAD_BYTES
    assert outcome["text"] == "a" * 10 and outcome["truncated"] is True


def test_fetch_reduces_html_to_its_title_and_readable_text(online):
    online.return_value = Page(
        b"<html><head><title> The title </title><style>p {}</style></head>"
        b"<body><script>steal()</script><p>One   two</p>\n\n\n<p>Three</p></body></html>"
    )
    outcome = WebTools.fetch("https://example.org")
    assert outcome["title"] == "The title"
    assert "steal" not in outcome["text"] and "p {}" not in outcome["text"]
    assert outcome["text"].endswith("One two\n\nThree")
    assert outcome["final_url"] == "https://example.org/final" and outcome["truncated"] is False


def test_fetch_hands_back_text_that_is_not_markup_as_it_is(online):
    online.return_value = Page(b"<not> markup", content_type="application/json", encoding=None)
    assert WebTools.fetch("https://example.org")["text"] == "<not> markup"


@pytest.mark.parametrize("content_type", ["text/html", "text/plain"])
def test_fetch_reads_a_page_that_names_an_unknown_charset_as_utf8(online, content_type):
    # The charset comes from the page's server: an unknown one used to raise LookupError out of a
    # tool whose errors are returned, not raised.
    online.return_value = Page(
        "café".encode(), content_type=content_type, encoding="x-no-such-charset"
    )
    assert WebTools.fetch("https://example.org")["text"] == "café"


@pytest.mark.parametrize(
    "body, content_type",
    [
        ("<p>café</p>".encode(), "text/html"),
        ("<meta charset='iso-8859-1'><p>café</p>".encode("latin-1"), "text/html"),
        ("café".encode(), "text/plain"),
    ],
)
def test_fetch_decodes_a_page_whose_server_names_no_charset_by_its_content(
    online, body, content_type
):
    # requests answers ISO-8859-1 for any text/* reply without a charset (`response.encoding`),
    # which turned every such UTF-8 page into mojibake ("cafÃ©").
    online.return_value = Page(body, content_type=content_type, encoding="ISO-8859-1")
    assert WebTools.fetch("https://example.org")["text"] == "café"


def test_search_at_air_gap_on_or_with_an_empty_query_makes_no_request(monkeypatch, online):
    assert WebTools.search("   ")["error"] == "empty query"
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "on"))
    assert WebTools.search("python")["airgapped"] == "on"
    online.assert_not_called()


def test_search_names_an_unreachable_searxng_and_how_to_start_it(online):
    online.side_effect = requests.ConnectionError("refused")
    outcome = WebTools.search("python")
    assert "is unreachable: refused" in outcome["error"]
    assert web_tools.SEARXNG_START_HINT in outcome["hint"]


def test_search_names_a_searxng_that_answers_without_json(online):
    online.return_value.json.side_effect = ValueError("not json")
    outcome = WebTools.search("python")
    assert (
        "did not return JSON" in outcome["error"]
        and web_tools.SEARXNG_START_HINT in outcome["hint"]
    )


def test_search_keeps_at_most_max_results(online):
    online.return_value.json.return_value = {
        "results": [{"title": str(n), "url": f"u{n}"} for n in range(5)]
    }
    outcome = WebTools.search("python", max_results=2)
    assert [r["title"] for r in outcome["results"]] == ["0", "1"]
    assert outcome["results"][0] == {"title": "0", "url": "u0", "snippet": "", "engine": ""}


def drip(head: bytes) -> int:
    """
    Starts a stand-in on loopback that sends `head`, then a byte every 50 ms for at most 30 s.

    Each read returns well within REQUEST_TIMEOUT_S, so only the total deadline ends a request.

    Returns:
        int: The stand-in's port.
    """
    import socket
    import threading
    import time

    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        connection, _ = listener.accept()
        with connection, listener:
            connection.recv(65536)
            try:
                connection.sendall(head)
                for _ in range(600):
                    time.sleep(0.05)
                    connection.sendall(b"a")
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1]


@pytest.mark.parametrize(
    "scheme, head",
    [
        (
            "http",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5000000\r\n\r\n",
        ),
        ("http", b"HTTP/1.1 200 OK\r\nX-Slow: "),
        # A TLS handshake record of 16 KiB, its body still to come.
        ("https", b"\x16\x03\x03\x40\x00"),
    ],
    ids=["body", "headers", "tls-handshake"],
)
def test_a_page_sent_a_byte_at_a_time_ends_at_the_deadline(monkeypatch, loopback, scheme, head):
    import time

    port = drip(head)
    monkeypatch.setattr(web_tools, "REQUEST_DEADLINE_S", 2.0)
    started = time.monotonic()
    with pytest.raises(requests.Timeout):
        WebTools._download(f"{scheme}://127.0.0.1:{port}/")
    assert time.monotonic() - started < 10


@pytest.fixture
def loopback(monkeypatch):
    """The air gap is off and no proxy stands between a fetch and a stand-in on loopback."""
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "off"))
    for name in ("http", "https", "all"):
        monkeypatch.delenv(f"{name}_proxy", raising=False)
        monkeypatch.delenv(f"{name.upper()}_PROXY", raising=False)


def test_fetch_sends_no_netrc_credentials_to_a_page_or_where_it_redirects(loopback):
    # requests adds the user's ~/.netrc login for the request's host and again for every redirect
    # target, so a page could send the agent to a host the user keeps a password for and have it
    # read what that login sees. ling-fetch never reads .netrc.
    import http.server
    import os
    import threading
    from pathlib import Path

    netrc = Path(os.environ["HOME"]) / ".netrc"
    netrc.write_text("machine localhost login user password secret\n")
    netrc.chmod(0o600)
    seen: list = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}/final")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert WebTools.fetch(f"http://127.0.0.1:{port}/start")["text"] == "ok"
        assert WebTools.fetch(f"http://localhost:{port}/final")["text"] == "ok"
        # A login written in the URL is the caller's own, and still goes.
        assert WebTools.fetch(f"http://me:pw@localhost:{port}/final")["text"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [
        ("/start", None), ("/final", None), ("/final", None), ("/final", "Basic bWU6cHc=")
    ]


def test_a_page_sent_a_byte_at_a_time_through_a_proxy_ends_at_the_deadline(
    monkeypatch, loopback
):
    # Through a proxy requests opens its connections from another pool manager; the stand-in is
    # the proxy, so the page's own host is never looked up.
    import time

    port = drip(b"HTTP/1.1 200 OK\r\nX-Slow: ")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(web_tools, "REQUEST_DEADLINE_S", 2.0)
    started = time.monotonic()
    with pytest.raises(requests.Timeout):
        WebTools._download("http://page.invalid/")
    assert time.monotonic() - started < 10


def test_search_ends_at_the_deadline_when_searxng_answers_a_byte_at_a_time(
    monkeypatch, loopback
):
    import time

    port = drip(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 9000\r\n\r\n")
    monkeypatch.setattr(web_tools, "SEARXNG_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(web_tools, "REQUEST_DEADLINE_S", 2.0)
    started = time.monotonic()
    outcome = WebTools.search("python")
    assert "took longer than 2 s" in outcome["error"]
    assert time.monotonic() - started < 10
