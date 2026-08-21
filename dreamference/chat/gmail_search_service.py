"""
Gmail Search Service for Onyx.

This module provides the GmailSearchService class, an HTTP service that turns the Gmail REST API
into the two operations an assistant needs, and the OpenAPI description Onyx registers it under.

It mirrors how web search already works rather than inventing a new shape. Web search gives the
model `web_search` to find things and `open_url` to read one; this gives it `gmail_search` to find
messages and `gmail_message` to read one. The split matters for the same reason it does there: a
mailbox search that returned full bodies would fill the context with ten threads to answer a
question about one.

The module is deliberately **standard library only and self-contained**. It runs inside a stock
`python:3-slim` container with nothing installed into it, and it is copied next to the credentials
rather than mounted from the source tree, so the container has no idea where Dreamference lives.

Two things are load-bearing:

* **The shared secret.** The service sits on Onyx's Docker network holding a credential for a real
  mailbox, and Onyx's custom-tool client performs no SSRF validation -- it calls whatever URL the
  tool names. The header check is what stops anything else on that network reading the user's mail.
* **Read-only.** The token carries `gmail.readonly`, and the service exposes no route that could
  write even if it had a broader one.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional, Tuple

# Inside the container the credentials directory is mounted here.
CONFIG_DIR: Final[str] = os.environ.get("PUFFIN_GMAIL_CONFIG", "/config")
CREDENTIALS_NAME: Final[str] = "credentials.json"

# The header Onyx is registered to send. Anything on the Docker network can reach this service; the
# secret is what distinguishes Onyx from everything else.
AUTH_HEADER: Final[str] = "X-Puffin-Gmail-Token"

GOOGLE_TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"
GMAIL_API: Final[str] = "https://gmail.googleapis.com/gmail/v1/users/me"

# Gmail counts a "page" in messages, and each one costs a second request for its metadata. Twenty
# is enough for the model to choose from and still answers in about a second.
DEFAULT_RESULT_LIMIT: Final[int] = 10
MAX_RESULT_LIMIT: Final[int] = 20

# How much of a message body to return. Long threads are quoted repeatedly, and the tail is almost
# always older quoted copies of what is already above it.
MAX_BODY_CHARACTERS: Final[int] = 20_000

SERVICE_PORT: Final[int] = 8000

# The service is also published on this loopback port, because two callers reach it from outside
# the Docker network: the browser, asking whether Gmail is connected so it knows whether to show a
# Connect button, and Google, redirecting the user back after consent.
HOST_PORT: Final[int] = 8767
HOST_ORIGIN: Final[str] = f"http://localhost:{HOST_PORT}"

# The one redirect URI the Google client has to list. Consent is served here rather than from a
# throwaway listener in the CLI so that there is a single URI to register, and so the button in the
# UI can start the flow without the page needing to know the client id.
REDIRECT_PATH: Final[str] = "/oauth/callback"
REDIRECT_URI: Final[str] = f"{HOST_ORIGIN}{REDIRECT_PATH}"

GOOGLE_AUTH_URL: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_SCOPE: Final[str] = "https://www.googleapis.com/auth/gmail.readonly"

# The page origin allowed to read the status endpoint. The browser calls it cross-origin, from the
# Onyx UI to this service.
ONYX_ORIGIN: Final[str] = "http://localhost:3000"


class GmailSearchService:
    """
    Serves Gmail search and message retrieval over HTTP for Onyx's custom tool.
    """

    @classmethod
    def credentials(cls) -> Optional[Dict[str, str]]:
        """
        Reads the stored Google credentials from the mounted config directory.

        Returns:
            Optional[Dict[str, str]]: Client id, secret and refresh token, or None.
        """
        try:
            with open(os.path.join(CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    @classmethod
    def access_token(cls) -> Optional[str]:
        """
        Exchanges the stored refresh token for a short-lived access token.

        Fetched per request rather than cached: Google's access tokens last an hour, this service
        answers a handful of requests a day, and a cache would be one more thing to invalidate when
        the user revokes access.

        Returns:
            Optional[str]: A bearer token, or None if the refresh failed.
        """
        stored = cls.credentials()
        if not stored:
            return None
        payload = urllib.parse.urlencode({
            "client_id": stored["client_id"],
            "client_secret": stored["client_secret"],
            "refresh_token": stored["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        try:
            with urllib.request.urlopen(GOOGLE_TOKEN_URL, data=payload, timeout=30) as response:
                return json.loads(response.read()).get("access_token")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    @classmethod
    def _call(cls, path: str, token: str, **params: Any) -> Optional[Dict[str, Any]]:
        """
        Makes one authenticated Gmail API call.

        Args:
            path (str): Path below the user's mailbox root.
            token (str): Bearer access token.
            **params: Query parameters.

        Returns:
            Optional[Dict[str, Any]]: The decoded response, or None on failure.
        """
        url = f"{GMAIL_API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None

    @classmethod
    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        """
        Finds messages matching a Gmail search query.

        Args:
            query (str): A Gmail query, in the same syntax the Gmail search box takes.
            limit (int): Maximum number of messages to describe.

        Returns:
            Dict[str, Any]: `{"messages": [...]}`, each entry carrying the id, sender, subject,
                date and snippet -- enough to choose one to open, and no more.
        """
        token = cls.access_token()
        if not token:
            return {"error": "Gmail is not connected. Run: dream onyx gmail --client-id … "
                             "--client-secret …"}

        listing = cls._call("/messages", token, q=query, maxResults=max(1, min(limit, MAX_RESULT_LIMIT)))
        if listing is None:
            return {"error": "Gmail search failed."}

        results: List[Dict[str, str]] = []
        for entry in listing.get("messages", []):
            detail = cls._call(
                f"/messages/{entry['id']}", token,
                format="metadata", metadataHeaders=["From", "Subject", "Date"],
            )
            if not detail:
                continue
            headers = {
                h["name"].lower(): h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            results.append({
                "id": entry["id"],
                "from": headers.get("from", ""),
                "subject": headers.get("subject", "(no subject)"),
                "date": headers.get("date", ""),
                "snippet": detail.get("snippet", ""),
            })
        return {"messages": results}

    @classmethod
    def message(cls, message_id: str) -> Dict[str, Any]:
        """
        Reads one message in full.

        Args:
            message_id (str): The id from a search result.

        Returns:
            Dict[str, Any]: The headers and the plain-text body.
        """
        token = cls.access_token()
        if not token:
            return {"error": "Gmail is not connected."}

        detail = cls._call(f"/messages/{message_id}", token, format="full")
        if detail is None:
            return {"error": "Message not found."}

        headers = {
            h["name"].lower(): h["value"]
            for h in detail.get("payload", {}).get("headers", [])
        }
        body = cls._extract_text(detail.get("payload", {}))
        return {
            "id": message_id,
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "body": body[:MAX_BODY_CHARACTERS],
        }

    @classmethod
    def _extract_text(cls, payload: Dict[str, Any]) -> str:
        """
        Pulls the plain-text body out of a Gmail payload tree.

        Gmail nests parts arbitrarily deep and a message may carry both a text and an HTML copy.
        The text part is preferred and HTML is only stripped as a fallback, because a naive tag
        strip of a marketing email produces far more noise than its text alternative.

        Args:
            payload (Dict[str, Any]): The `payload` object from a full message.

        Returns:
            str: The body, or an empty string.
        """
        import base64

        def decode(data: str) -> str:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
            except (ValueError, TypeError):
                return ""

        plain, html = "", ""

        def walk(part: Dict[str, Any]) -> None:
            nonlocal plain, html
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data")
            if data:
                if mime == "text/plain" and not plain:
                    plain = decode(data)
                elif mime == "text/html" and not html:
                    html = decode(data)
            for child in part.get("parts", []) or []:
                walk(child)

        walk(payload)
        if plain:
            return plain
        return re.sub(r"<[^>]+>", " ", html) if html else ""

    @classmethod
    def status(cls) -> Dict[str, Any]:
        """
        Reports how far Gmail has been set up.

        Returns:
            Dict[str, Any]: `configured` -- a Google client is stored; `connected` -- someone has
                consented and there is a refresh token. The Connect button exists for the state
                where the first is true and the second is not.
        """
        stored = cls.credentials() or {}
        return {
            "configured": bool(stored.get("client_id") and stored.get("client_secret")),
            "connected": bool(stored.get("refresh_token")),
        }

    @classmethod
    def consent_url(cls) -> Optional[str]:
        """
        Builds the Google consent URL for the stored client.

        Returns:
            Optional[str]: The URL, or None if no client is configured.
        """
        stored = cls.credentials() or {}
        if not stored.get("client_id"):
            return None
        return f"{GOOGLE_AUTH_URL}?" + urllib.parse.urlencode({
            "client_id": stored["client_id"],
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": GMAIL_SCOPE,
            # Both are required for Google to return a refresh token rather than only an access
            # token; without them the service would stop working an hour later.
            "access_type": "offline",
            "prompt": "consent",
        })

    @classmethod
    def complete_consent(cls, code: str) -> bool:
        """
        Exchanges an authorisation code for a refresh token and stores it.

        Args:
            code (str): The code Google redirected back with.

        Returns:
            bool: True if a refresh token was stored.
        """
        stored = cls.credentials()
        if not stored:
            return False
        payload = urllib.parse.urlencode({
            "code": code,
            "client_id": stored["client_id"],
            "client_secret": stored["client_secret"],
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode()
        try:
            with urllib.request.urlopen(GOOGLE_TOKEN_URL, data=payload, timeout=30) as response:
                token = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return False

        refresh = token.get("refresh_token")
        if not refresh:
            return False
        stored["refresh_token"] = refresh
        try:
            path = os.path.join(CONFIG_DIR, CREDENTIALS_NAME)
            with open(path, "w") as handle:
                json.dump(stored, handle, indent=2)
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True

    @classmethod
    def serve(cls, port: int = SERVICE_PORT, secret: Optional[str] = None) -> None:
        """
        Runs the HTTP service until killed.

        Args:
            port (int): Port to listen on.
            secret (Optional[str]): Required value of the shared-secret header. Taken from the
                environment when not given.
        """
        expected = secret or os.environ.get("PUFFIN_GMAIL_SECRET", "")

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status: int, body: Dict[str, Any]) -> None:
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", ONYX_ORIGIN)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _redirect(self, location: str) -> None:
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()

            def _page(self, message: str) -> None:
                body = f"<h2>Puffin</h2><p>{message}</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                parsed = urllib.parse.urlparse(self.path)

                # Read by the browser, cross-origin from the Onyx page, to decide whether to offer
                # the Connect button. It exposes no mail and needs no secret.
                if parsed.path in ("/health", "/status"):
                    self._reply(200, cls.status())
                    return
                if parsed.path == "/oauth/start":
                    url = cls.consent_url()
                    if not url:
                        self._page("No Google client is configured. Run: "
                                   "<code>dream onyx gmail --client-id … --client-secret …</code>")
                        return
                    self._redirect(url)
                    return
                if parsed.path == REDIRECT_PATH:
                    query = urllib.parse.parse_qs(parsed.query)
                    code = (query.get("code") or [""])[0]
                    if code and cls.complete_consent(code):
                        self._page("Gmail connected. You can close this tab.")
                    else:
                        self._page("Could not complete the connection. Check the service logs.")
                    return
                if expected and self.headers.get(AUTH_HEADER) != expected:
                    self._reply(401, {"error": "unauthorised"})
                    return

                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path == "/search":
                    terms = (query.get("query") or [""])[0]
                    if not terms:
                        self._reply(400, {"error": "query is required"})
                        return
                    limit = int((query.get("limit") or [DEFAULT_RESULT_LIMIT])[0])
                    self._reply(200, cls.search(terms, limit))
                    return
                if parsed.path.startswith("/message/"):
                    self._reply(200, cls.message(parsed.path.rsplit("/", 1)[-1]))
                    return
                self._reply(404, {"error": "not found"})

            def log_message(self, *args: object) -> None:
                """Silenced: request logs would record the user's search terms."""

        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def openapi_definition(base_url: str) -> Dict[str, Any]:
    """
    Builds the OpenAPI document Onyx registers the tool from.

    Onyx derives one tool per operation and uses `operationId` as the tool name and `summary` as
    what the model reads when deciding to call it, so both are written for the model rather than
    for a developer browsing a schema.

    Args:
        base_url (str): URL the service is reachable at from Onyx's container.

    Returns:
        Dict[str, Any]: An OpenAPI 3 document.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "Gmail", "version": "1.0.0",
                 "description": "Search and read the user's Gmail mailbox."},
        "servers": [{"url": base_url}],
        "paths": {
            "/search": {
                "get": {
                    "operationId": "gmail_search",
                    "summary": (
                        "Search the user's Gmail. Use this for anything about their email: what "
                        "someone sent, when something arrived, receipts, bookings, threads on a "
                        "topic. Accepts Gmail search syntax such as 'from:alice invoice', "
                        "'subject:renewal', 'has:attachment', 'newer_than:7d'. Returns message "
                        "summaries; call gmail_message to read one in full."
                    ),
                    "parameters": [
                        {"name": "query", "in": "query", "required": True,
                         "schema": {"type": "string"},
                         "description": "Gmail search query."},
                        {"name": "limit", "in": "query", "required": False,
                         "schema": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                         "description": f"How many messages to return (max {MAX_RESULT_LIMIT})."},
                    ],
                    "responses": {"200": {"description": "Matching messages."}},
                }
            },
            "/message/{message_id}": {
                "get": {
                    "operationId": "gmail_message",
                    "summary": (
                        "Read one Gmail message in full, using an id from gmail_search. Returns "
                        "the sender, recipients, subject, date and the plain-text body."
                    ),
                    "parameters": [
                        {"name": "message_id", "in": "path", "required": True,
                         "schema": {"type": "string"},
                         "description": "Message id from a gmail_search result."},
                    ],
                    "responses": {"200": {"description": "The message."}},
                }
            },
        },
    }


if __name__ == "__main__":
    GmailSearchService.serve()
