"""The stand-in web the agent meets in the jail (spec §5.3, §9.2).

Two servers on the jail's loopback, both driven by one scenario's `web` block and both logging
every request they see to a JSONL file, which the checks read:

- **SearXNG** (`ling-search` reaches it through `DREAMFERENCE_SEARXNG_URL`): `GET /search?q=…`
  answers SearXNG's JSON (`results`, `answers`, `infoboxes`, `unresponsive_engines`) from the
  scenario's `search` entries, or with the scenario's fault.
- **The proxy** (`HTTP_PROXY`, `http_proxy`, `HTTPS_PROXY` point at it): a plain request for one
  of the scenario's `pages` gets that page; any other plain request, and every `CONNECT` (an
  `https://` URL), gets the scenario's fault for it.

A scenario's `web` block:

```json
{
  "search": {"fault": null, "delay_s": 0, "results": [{"title": "...", "url": "http://...", "content": "..."}]},
  "pages": {"http://shop.example.test/item": {"status": 200, "body": "<html>...</html>"}},
  "other": {"fault": "captcha"},
  "connect": {"fault": "forbidden"}
}
```

Faults: `captcha` (200 with a CAPTCHA page; for search, every engine unresponsive with
"CAPTCHA"), `rate_limited` (429 with `Retry-After`; for search, "too many requests"), `forbidden`
(403), `reset` (the connection closed with no answer), `stall` (the answer after `delay_s`,
default 30), `empty` (search: no results and no error).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Optional
from urllib.parse import parse_qs, urlsplit

CAPTCHA_PAGE: Final[str] = (
    "<html><head><title>Are you a robot?</title></head><body><h1>One more step</h1>"
    "<p>Please complete the CAPTCHA below to confirm you are human.</p></body></html>"
)
UNRESPONSIVE: Final[dict] = {
    "captcha": [
        ["brave", "CAPTCHA"],
        ["duckduckgo", "CAPTCHA"],
        ["startpage", "Suspended: CAPTCHA"],
    ],
    "rate_limited": [
        ["brave", "too many requests"],
        ["google", "too many requests"],
        ["mwmbl", "Suspended: too many requests"],
    ],
    "forbidden": [["brave", "access denied"], ["google", "access denied"]],
}
DEFAULT_STALL_S: Final[float] = 30.0


class _Log:
    """An append-only JSONL log shared by both servers' threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, entry: dict) -> None:
        entry = {"at": round(time.time(), 3), **entry}
        with self.lock, self.path.open("a") as out:
            out.write(json.dumps(entry) + "\n")


def _send(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: str,
    content_type: str,
    headers: dict | None = None,
) -> None:
    data = body.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


def _reset(handler: BaseHTTPRequestHandler) -> None:
    """Closes the connection with no answer at all."""
    handler.close_connection = True
    try:
        handler.connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def search_answer(query: str, search: dict) -> tuple[int, dict]:
    """SearXNG's JSON for one query under the scenario's `search` block (status, body)."""
    fault = search.get("fault")
    body: dict[str, Any] = {
        "query": query,
        "number_of_results": 0,
        "results": [],
        "answers": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": [],
    }
    if fault in UNRESPONSIVE:
        body["unresponsive_engines"] = UNRESPONSIVE[fault]
        return 200, body
    if fault == "empty":
        return 200, body
    results = search.get("results", [])
    by_query = search.get("by_query", {})
    for words, chosen in by_query.items():
        if all(word.lower() in query.lower() for word in words.split()):
            results = chosen
            break
    body["results"] = [
        {"engine": "wikipedia", "engines": ["wikipedia"], "score": 1.0, **r} for r in results
    ]
    body["number_of_results"] = len(body["results"])
    return 200, body


class _SearchHandler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        parts = urlsplit(self.path)
        query = (parse_qs(parts.query).get("q") or [""])[0]
        search = self.server.web.get("search", {})
        self.server.log.write(
            {"server": "search", "path": parts.path, "query": query, "fault": search.get("fault")}
        )
        if search.get("fault") == "reset":
            _reset(self)
            return
        delay = float(search.get("delay_s", 0) or 0)
        if search.get("fault") == "stall":
            delay = delay or DEFAULT_STALL_S
        time.sleep(delay)
        if search.get("fault") == "stall":
            status, body = search_answer(query, {**search, "fault": None})
        else:
            status, body = search_answer(query, search)
        _send(self, status, json.dumps(body), "application/json")


def fault_response(fault: str | None) -> tuple[int, str, dict] | None:
    """(status, body, headers) for a proxied request's fault; None for `reset` and `stall`."""
    if fault in (None, "captcha"):
        return 200, CAPTCHA_PAGE, {}
    if fault == "rate_limited":
        return 429, "<html><body>Too Many Requests</body></html>", {"Retry-After": "60"}
    if fault == "forbidden":
        return 403, "<html><body>Forbidden</body></html>", {}
    return None


class _ProxyHandler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _fault(self, block: dict) -> None:
        fault = block.get("fault", "captcha")
        if fault == "reset":
            _reset(self)
            return
        if fault == "stall":
            time.sleep(float(block.get("delay_s", DEFAULT_STALL_S)))
            fault = block.get("then", "forbidden")
        answer = fault_response(fault)
        if answer is None:
            _reset(self)
            return
        status, body, headers = answer
        _send(self, status, body, "text/html; charset=utf-8", headers)

    def do_CONNECT(self) -> None:  # noqa: N802
        host = self.path
        self.server.log.write({"server": "proxy", "method": "CONNECT", "url": host})
        block = self.server.web.get("connect", {"fault": "forbidden"})
        if block.get("fault", "forbidden") in (None, "captcha"):
            block = {**block, "fault": "forbidden"}  # a tunnel cannot carry a page without TLS
        self._fault(block)

    def do_GET(self) -> None:  # noqa: N802
        url = self.path
        pages = self.server.web.get("pages", {})
        page = pages.get(url) or pages.get(url.rstrip("/"))
        self.server.log.write(
            {"server": "proxy", "method": "GET", "url": url, "known": page is not None}
        )
        if page is None:
            self._fault(self.server.web.get("other", {"fault": "captcha"}))
            return
        if page.get("delay_s"):
            time.sleep(float(page["delay_s"]))
        if page.get("fault"):
            self._fault(page)
            return
        _send(
            self,
            int(page.get("status", 200)),
            page.get("body", ""),
            page.get("content_type", "text/html; charset=utf-8"),
        )

    do_HEAD = do_GET  # noqa: N815


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler: type, web: dict, log: _Log) -> None:
        super().__init__(("127.0.0.1", 0), handler)
        self.web = web
        self.log = log


class StandIns:
    """Both stand-ins for one scenario, on free loopback ports, for a `with` block."""

    def __init__(self, web: dict, log_path: Path) -> None:
        self.log = _Log(log_path)
        self.search = _Server(_SearchHandler, web, self.log)
        self.proxy = _Server(_ProxyHandler, web, self.log)
        self._threads = [
            threading.Thread(target=s.serve_forever, daemon=True) for s in (self.search, self.proxy)
        ]

    @property
    def searxng_url(self) -> str:
        return f"http://127.0.0.1:{self.search.server_address[1]}"

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.proxy.server_address[1]}"

    def environment(self) -> dict[str, str]:
        """What the agent's environment needs to reach the stand-ins and nothing else."""
        return {
            "DREAMFERENCE_SEARXNG_URL": self.searxng_url,
            "HTTP_PROXY": self.proxy_url,
            "http_proxy": self.proxy_url,
            "HTTPS_PROXY": self.proxy_url,
            "https_proxy": self.proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
            # Every scenario starts with an empty search cache (ling-web-rs/src/cache.rs).
            "LING_SEARCH_CACHE_DIR": "off",
        }

    def __enter__(self) -> StandIns:
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for server in (self.search, self.proxy):
            server.shutdown()
            server.server_close()


def read_log(path: Path) -> list[dict]:
    """The stand-ins' requests, in order."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
