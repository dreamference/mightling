"""`WebTools`, the MCP server's `web_fetch` and `web_search`.

`requests.get` is replaced in every test, so nothing here reaches the network.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from dreamference.mcp_server import web_tools
from dreamference.mcp_server.web_tools import AIRGAPPED_ON_MESSAGE, SEARXNG_START_HINT, WebTools


class FakePage:
    """A streamed response: `chunks` are what `iter_content` yields, counted as they are read."""

    def __init__(
        self,
        chunks,
        content_type="text/html; charset=utf-8",
        status=200,
        url="https://example.com/",
    ):
        self.chunks = list(chunks)
        self.read = 0
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.status_code = status
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def iter_content(self, size):
        for chunk in self.chunks:
            self.read += 1
            yield chunk


@pytest.fixture(autouse=True)
def online(monkeypatch):
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "off"))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/1",
        "ftp://example.com/file",
        "/etc/passwd",
        "example.com/page",
        "http:///no-host",
        "",
    ],
)
def test_fetch_refuses_anything_but_an_http_address_without_a_request(url):
    # The scheme check is what keeps `web_fetch` from reading local files.
    with patch.object(web_tools.requests, "get") as get:
        outcome = WebTools.fetch(url)

    assert get.call_count == 0
    assert outcome["url"] == url and "error" in outcome and "text" not in outcome


def test_fetch_sends_nothing_at_air_gap_on(monkeypatch):
    monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "on"))
    with patch.object(web_tools.requests, "get") as get:
        outcome = WebTools.fetch("https://example.com")

    assert get.call_count == 0 and outcome["error"] == AIRGAPPED_ON_MESSAGE


def test_fetch_reduces_html_to_its_title_and_readable_text():
    html = (
        b"<html><head><title> Release notes </title><style>p{color:red}</style>"
        b"<script>track()</script></head><body><nav>Home | About</nav>"
        b"<p>First   paragraph.</p>\n\n\n<p>Second paragraph.</p></body></html>"
    )
    page = FakePage([html], url="https://example.com/notes")
    with patch.object(web_tools.requests, "get", return_value=page) as get:
        outcome = WebTools.fetch("https://example.com/notes")

    assert get.call_args.kwargs["stream"] is True and get.call_args.kwargs["timeout"] > 0
    assert outcome["title"] == "Release notes"
    assert "First paragraph." in outcome["text"] and "Second paragraph." in outcome["text"]
    assert "track()" not in outcome["text"] and "color:red" not in outcome["text"]
    assert (outcome["status"], outcome["final_url"], outcome["truncated"]) == (
        200,
        "https://example.com/notes",
        False,
    )


def test_fetch_hands_back_text_that_is_not_markup_as_it_is():
    source = b"def main():\n    return '<p>not markup</p>'\n"
    with patch.object(web_tools.requests, "get", return_value=FakePage([source], "text/x-python")):
        outcome = WebTools.fetch("https://example.com/main.py")

    assert outcome["title"] == "" and outcome["text"] == source.decode()


@pytest.mark.parametrize(
    "max_chars, kept", [(5, 5), (0, 1), (-40, 1), (10**9, web_tools.MAX_CHARS_CEILING)]
)
def test_fetch_clamps_the_text_it_returns(max_chars, kept):
    body = b"x" * (web_tools.MAX_CHARS_CEILING + 10)
    with patch.object(web_tools.requests, "get", return_value=FakePage([body], "text/plain")):
        outcome = WebTools.fetch("https://example.com/big.txt", max_chars=max_chars)

    assert len(outcome["text"]) == kept and outcome["truncated"] is True


def test_fetch_stops_reading_at_the_download_ceiling(monkeypatch):
    # The body is streamed so an endless or huge page is cut off while it arrives.
    monkeypatch.setattr(web_tools, "MAX_DOWNLOAD_BYTES", 16)
    page = FakePage([b"abcdefgh", b"ijklmnop", b"qrstuvwx", b"never read"], "text/plain")
    with patch.object(web_tools.requests, "get", return_value=page):
        outcome = WebTools.fetch("https://example.com/endless", max_chars=1000)

    assert page.read == 2
    assert outcome["text"] == "abcdefghijklmnop"


def test_fetch_reports_a_failed_request_instead_of_raising():
    with patch.object(web_tools.requests, "get", return_value=FakePage([], status=404)):
        missing = WebTools.fetch("https://example.com/gone")
    with patch.object(web_tools.requests, "get", side_effect=requests.ConnectionError("refused")):
        refused = WebTools.fetch("https://example.com/")

    assert missing["error"].startswith("request failed: 404")
    assert refused["error"] == "request failed: refused"


def test_search_says_how_to_start_searxng_when_it_is_unreachable():
    with patch.object(web_tools.requests, "get", side_effect=requests.ConnectionError("refused")):
        outcome = WebTools.search("python asyncio")

    assert "unreachable" in outcome["error"] and SEARXNG_START_HINT in outcome["hint"]


def test_search_says_the_json_api_is_off_when_html_comes_back():
    response = MagicMock()
    response.json.side_effect = ValueError("Expecting value")
    with patch.object(web_tools.requests, "get", return_value=response):
        outcome = WebTools.search("python asyncio")

    assert "did not return JSON" in outcome["error"] and "search.formats" in outcome["hint"]


def test_search_sends_nothing_for_an_empty_query_or_at_air_gap_on(monkeypatch):
    with patch.object(web_tools.requests, "get") as get:
        assert WebTools.search("   ")["error"] == "empty query"
        monkeypatch.setattr(WebTools, "airgapped_level", classmethod(lambda cls: "on"))
        assert WebTools.search("python")["error"] == AIRGAPPED_ON_MESSAGE

    assert get.call_count == 0


def test_search_keeps_at_most_the_results_asked_for():
    response = MagicMock()
    response.json.return_value = {
        "results": [{"title": f"t{n}", "url": f"https://e/{n}"} for n in range(5)]
    }
    with patch.object(web_tools.requests, "get", return_value=response):
        two = WebTools.search("python", max_results=2)
        at_least_one = WebTools.search("python", max_results=0)

    assert [r["title"] for r in two["results"]] == ["t0", "t1"] and two["result_count"] == 2
    assert at_least_one["result_count"] == 1
    assert two["results"][0]["snippet"] == "" and two["results"][0]["engine"] == ""
