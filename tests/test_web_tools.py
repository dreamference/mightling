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
    """The air gap is off and `requests.get` is a mock."""
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "off"))
    get = MagicMock()
    monkeypatch.setattr(web_tools.requests, "get", get)
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
def test_fetch_decodes_a_page_whose_server_names_no_charset_by_its_content(online, body, content_type):
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
