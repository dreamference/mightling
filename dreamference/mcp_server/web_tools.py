"""
Web access tools for the Dreamference MCP server.

A deliberate departure from the project's air-gapped premise, and worth being explicit about: with
these registered, the agent can reach the public internet. They exist because Codex cannot get web
access any other way against a local model — its native `web_search` is a hosted Responses tool
executed by the provider, so a vLLM backend answers `unsupported call: web_search` no matter how it
is configured. Running the fetch locally through MCP is the only route, and it has the advantage
that every request goes through code in this repository rather than a third-party service.

Search goes through a self-hosted SearXNG instance rather than any single engine's endpoint. That
keeps the search side on the same footing as everything else here — a container this machine owns,
with no account, no API key, and no third party seeing the query. SearXNG aggregates the upstream
engines itself, so switching or adding engines is its configuration rather than this module's code.

Its JSON API has two prerequisites, both set in ~/.config/searxng/settings.yml: `search.formats`
must include `json` (SearXNG serves HTML only by default) and `server.limiter` must be off (the
limiter is bot protection, and a local tool calling its own instance trips it). If the instance is
down this tool reports that plainly instead of falling back to a public engine — a silent fallback
would send queries somewhere the operator did not choose.

Fetching needs no service at all: `requests` plus `bs4`, both already required. The whole capability
stays inspectable and removable — delete the two entries from MCPToolRegistry and the agent is
offline again.
"""

import os
import re
from typing import Any, Dict, Final, List
from urllib.parse import urlparse

import requests

# Sent on every request. Some sites serve a stub or a challenge page to unknown agents, and the
# point of a fetch tool is to return what a person would see.
USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
# Self-hosted SearXNG. Bound to loopback by the container's port mapping, so nothing outside this
# machine can query it. Overridable for an instance that lives elsewhere.
SEARXNG_URL: Final[str] = os.getenv("DREAMFERENCE_SEARXNG_URL", "http://127.0.0.1:8888")
# Printed when the instance cannot be reached, because the fix is a single command and the
# alternative is an agent that quietly believes the web does not exist.
SEARXNG_START_HINT: Final[str] = (
    "docker run -d --name dreamference-searxng --restart unless-stopped -p 127.0.0.1:8888:8080 "
    "-v ~/.config/searxng:/etc/searxng docker.io/searxng/searxng:latest"
)
REQUEST_TIMEOUT_S: Final[float] = 25.0
# Hard ceiling on what a single fetch will pull down, before any text extraction. A model cannot
# use more than this anyway, and without it one link to a large binary stalls the whole session.
MAX_DOWNLOAD_BYTES: Final[int] = 5 * 1024 * 1024
# Default cap on the text handed back. Callers can ask for more, but not for the whole 5 MB.
DEFAULT_MAX_CHARS: Final[int] = 20_000
MAX_CHARS_CEILING: Final[int] = 100_000
# Everything that carries no prose. Dropped before text extraction so the result reads like the
# page rather than like its source.
_NON_CONTENT_TAGS: Final[tuple] = (
    "script", "style", "noscript", "svg", "canvas", "template", "iframe", "form",
)


class WebTools:
    """
    Fetch and search primitives exposed to the agent over MCP.

    A classmethod namespace, like the other stateless helpers in this codebase: there is nothing
    worth keeping between calls, and a shared session would only hide which request went where.
    """

    @classmethod
    def _require_http_url(cls, url: str) -> str:
        """
        Validates that a URL is an ordinary web address.

        Args:
            url (str): Candidate URL.

        Returns:
            str: The URL, unchanged.

        Raises:
            ValueError: If the scheme is not http or https, which would let a `file://` or
                `gopher://` argument turn a web tool into a local-file reader.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Only http and https URLs are supported, got {parsed.scheme or 'no'} scheme")
        if not parsed.netloc:
            raise ValueError("URL has no host")
        return url

    @classmethod
    def _download(cls, url: str) -> requests.Response:
        """
        Retrieves a URL with a size ceiling and a timeout.

        Streamed rather than read whole, so the ceiling is enforced while the body arrives instead
        of after it has already been buffered.

        Args:
            url (str): URL to retrieve.

        Returns:
            requests.Response: The response, with `_body` holding at most MAX_DOWNLOAD_BYTES.
        """
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=REQUEST_TIMEOUT_S,
            stream=True,
        )
        response.raise_for_status()
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(8192):
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_DOWNLOAD_BYTES:
                break
        response._body = b"".join(chunks)  # type: ignore[attr-defined]
        return response

    @classmethod
    def _html_to_text(cls, html: str) -> Dict[str, str]:
        """
        Reduces an HTML document to its title and readable text.

        Args:
            html (str): Raw HTML.

        Returns:
            Dict[str, str]: {'title', 'text'}.
        """
        from bs4 import BeautifulSoup

        # html.parser rather than lxml: it is in the standard library, so this tool does not add a
        # dependency to a project that has to install cleanly offline.
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(_NON_CONTENT_TAGS):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""
        text = soup.get_text("\n")
        # Collapse the blank-line drifts that decomposing tags leaves behind, without joining
        # paragraphs that were genuinely separate.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*", "\n\n", text).strip()
        return {"title": title, "text": text}

    @classmethod
    def fetch(cls, url: str, max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
        """
        Retrieves a web page and returns its readable text.

        Args:
            url (str): Absolute http(s) URL.
            max_chars (int): Maximum characters of text to return; clamped to MAX_CHARS_CEILING.

        Returns:
            Dict[str, Any]: {'url', 'final_url', 'status', 'title', 'text', 'truncated'} on
                success, or {'error': ...} on failure. Errors are returned rather than raised so
                the agent sees what went wrong and can try a different source.
        """
        try:
            cls._require_http_url(url)
            response = cls._download(url)
        except ValueError as e:
            return {"url": url, "error": str(e)}
        except requests.RequestException as e:
            return {"url": url, "error": f"request failed: {e}"}

        limit = max(1, min(int(max_chars), MAX_CHARS_CEILING))
        content_type = response.headers.get("Content-Type", "")
        raw: bytes = getattr(response, "_body", b"")

        if "html" in content_type.lower():
            decoded = raw.decode(response.encoding or "utf-8", errors="replace")
            extracted = cls._html_to_text(decoded)
            title, text = extracted["title"], extracted["text"]
        else:
            # Plain text, JSON, source files: hand them back as-is rather than running a markup
            # parser over something that is not markup.
            title = ""
            text = raw.decode(response.encoding or "utf-8", errors="replace")

        return {
            "url": url,
            "final_url": response.url,
            "status": response.status_code,
            "content_type": content_type,
            "title": title,
            "text": text[:limit],
            "truncated": len(text) > limit,
        }

    @classmethod
    def search(
        cls,
        query: str,
        max_results: int = 8,
        categories: str = "general",
        language: str = "en",
    ) -> Dict[str, Any]:
        """
        Searches the web through the configured SearXNG instance.

        SearXNG queries the upstream engines on this machine's behalf and returns their merged,
        de-duplicated results as JSON, so no query leaves the host addressed to a search company
        and no API key is involved. Which engines it consults is SearXNG's configuration, not this
        module's — adding or removing one needs no change here.

        Args:
            query (str): Search terms.
            max_results (int): Maximum results to return.
            categories (str): SearXNG category, e.g. 'general', 'it', 'news', 'science'.
            language (str): Result language code.

        Returns:
            Dict[str, Any]: {'query', 'result_count', 'results': [{'title', 'url', 'snippet',
                'engine'}]}, or {'error': ...} naming the endpoint and how to start it.
        """
        if not query.strip():
            return {"query": query, "error": "empty query"}

        endpoint = f"{SEARXNG_URL.rstrip('/')}/search"
        try:
            response = requests.get(
                endpoint,
                params={
                    "q": query,
                    "format": "json",
                    "categories": categories,
                    "language": language,
                },
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as e:
            return {
                "query": query,
                "error": f"SearXNG at {SEARXNG_URL} is unreachable: {e}",
                "hint": f"Start it with: {SEARXNG_START_HINT}",
            }
        except ValueError:
            # HTML came back instead of JSON, which means `json` is missing from search.formats.
            return {
                "query": query,
                "error": f"SearXNG at {SEARXNG_URL} did not return JSON",
                "hint": "Add 'json' to search.formats in ~/.config/searxng/settings.yml and restart it",
            }

        limit = max(1, int(max_results))
        results: List[Dict[str, str]] = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "engine": item.get("engine", ""),
            }
            for item in payload.get("results", [])[:limit]
        ]

        answer = payload.get("answers") or []
        return {
            "query": query,
            "result_count": len(results),
            # SearXNG sometimes has a direct answer (calculators, definitions, unit conversions).
            # Surfacing it saves a fetch when it is the whole answer.
            "answers": answer[:3],
            "results": results,
        }
