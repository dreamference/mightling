"""
Google OAuth Credentials for Gmail Search.

This module provides the GmailCredentials class, which runs Google's installed-application consent
flow once and stores the resulting refresh token where the search service can find it.

Three decisions worth knowing about:

* **No Google client library.** `google-auth-oauthlib` and `google-api-python-client` would pull a
  dependency tree that is not in the Ubuntu archive, and Dreamference's packaging depends on the
  everyday paths needing only what apt can satisfy. The OAuth exchange is two form posts and the
  Gmail API is REST, so the standard library covers both.
* **The consent flow runs on the host, not in the container.** It needs a browser and a loopback
  redirect; the service that later *uses* the token runs headless on Onyx's network and only ever
  refreshes it.
* **The scope is read-only.** `gmail.readonly` cannot send, delete or modify. A search tool has no
  business holding a credential that could do any of those, and Google shows the user the narrower
  consent screen.
"""

import http.server
import json
import os
import secrets
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Dict, Final, Optional

# Where the refresh token lives. The directory is bind-mounted into the search service's container,
# so this path is also `/config` as far as that container is concerned.
CREDENTIALS_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/gmail")
CREDENTIALS_FILE: Final[str] = os.path.join(CREDENTIALS_DIR, "credentials.json")

# Read-only, deliberately. See the module docstring.
GMAIL_SCOPE: Final[str] = "https://www.googleapis.com/auth/gmail.readonly"

GOOGLE_AUTH_URL: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"

# The loopback port the consent redirect comes back to. It has to be registered on the Google
# client as an authorised redirect URI, so it is fixed rather than chosen at random.
REDIRECT_PORT: Final[int] = 8766
REDIRECT_URI: Final[str] = f"http://localhost:{REDIRECT_PORT}/"

# How long to wait for the user to finish consenting in the browser.
CONSENT_TIMEOUT_SECONDS: Final[int] = 300


class GmailCredentials:
    """
    Obtains and stores the Google refresh token that Gmail search runs on.
    """

    @classmethod
    def load(cls) -> Optional[Dict[str, str]]:
        """
        Reads the stored credentials.

        Returns:
            Optional[Dict[str, str]]: The client id, secret and refresh token, or None if Gmail has
                not been connected yet.
        """
        try:
            with open(CREDENTIALS_FILE) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return None
        if not all(stored.get(k) for k in ("client_id", "client_secret", "refresh_token")):
            return None
        return stored

    @classmethod
    def client(cls) -> Optional[Dict[str, str]]:
        """
        Reads the Google client pair, whether or not consent has been given yet.

        Split from `load()` because the two answer different questions. The service needs the
        client pair to *offer* a connect link; it needs the refresh token to actually read mail.
        Before anyone has consented the first is present and the second is not, and that is exactly
        the state the "Connect to Google" button exists for.

        Returns:
            Optional[Dict[str, str]]: The client id and secret, or None if not configured.
        """
        try:
            with open(CREDENTIALS_FILE) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return None
        if not all(stored.get(k) for k in ("client_id", "client_secret")):
            return None
        return {"client_id": stored["client_id"], "client_secret": stored["client_secret"]}

    @classmethod
    def save_client(cls, client_id: str, client_secret: str) -> bool:
        """
        Stores the Google client pair, leaving any existing refresh token in place.

        Args:
            client_id (str): Google OAuth client ID.
            client_secret (str): Google OAuth client secret.

        Returns:
            bool: True if the file was written.
        """
        existing = cls.load() or {}
        return cls.save(client_id, client_secret, existing.get("refresh_token", ""))

    @classmethod
    def save(cls, client_id: str, client_secret: str, refresh_token: str) -> bool:
        """
        Writes the credentials, readable only by this user.

        Args:
            client_id (str): Google OAuth client ID.
            client_secret (str): Google OAuth client secret.
            refresh_token (str): The long-lived refresh token.

        Returns:
            bool: True if the file was written.
        """
        try:
            os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
            with open(CREDENTIALS_FILE, "w") as handle:
                json.dump(
                    {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                    },
                    handle,
                    indent=2,
                )
            # `save_client` writes an empty refresh token deliberately, so that a configured but
            # unconsented client is a representable state rather than an absent file.
            # A refresh token is a bearer credential for the mailbox; the default umask is not
            # tight enough for that.
            os.chmod(CREDENTIALS_FILE, 0o600)
        except OSError as exc:
            print(f"❌ Could not store the Gmail credentials: {exc}")
            return False
        return True

    @classmethod
    def connect(cls, client_id: str, client_secret: str) -> bool:
        """
        Runs the consent flow in a browser and stores the refresh token it returns.

        Args:
            client_id (str): Google OAuth client ID.
            client_secret (str): Google OAuth client secret.

        Returns:
            bool: True if a refresh token was obtained and stored.
        """
        state = secrets.token_urlsafe(24)
        code = cls._await_authorisation_code(client_id, state)
        if not code:
            return False

        token = cls._exchange_code(client_id, client_secret, code)
        if not token:
            return False
        return cls.save(client_id, client_secret, token)

    @classmethod
    def _await_authorisation_code(cls, client_id: str, state: str) -> Optional[str]:
        """
        Opens the consent screen and catches the code Google redirects back with.

        Args:
            client_id (str): Google OAuth client ID.
            state (str): Random value echoed back by Google, checked to reject a redirect that did
                not originate from this request.

        Returns:
            Optional[str]: The authorisation code, or None.
        """
        received: Dict[str, str] = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                received.update({k: v[0] for k, v in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                ok = received.get("code") and received.get("state") == state
                self.wfile.write(
                    b"<h2>Puffin: Gmail connected.</h2><p>You can close this tab.</p>"
                    if ok else
                    b"<h2>Puffin: connection failed.</h2><p>Check the terminal.</p>"
                )
                done.set()

            def log_message(self, *args: object) -> None:
                """Silenced: the redirect catcher is an implementation detail, not a web server."""

        try:
            server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
        except OSError as exc:
            print(f"❌ Could not listen on {REDIRECT_URI}: {exc}")
            print("💡 Something else is using that port; the Google client requires this exact URI.")
            return None

        parameters = urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": GMAIL_SCOPE,
            # Without both of these Google returns only an access token on a repeat authorisation,
            # and the service would stop working an hour later with nothing to refresh from.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })
        url = f"{GOOGLE_AUTH_URL}?{parameters}"

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print("🔐 Opening Google's consent screen. Approve read-only Gmail access.")
        print(f"💡 If no browser opens, visit:\n   {url}")
        try:
            webbrowser.open(url)
        except (OSError, socket.error):
            pass

        finished = done.wait(CONSENT_TIMEOUT_SECONDS)
        server.shutdown()
        if not finished:
            print("❌ Timed out waiting for consent.")
            return None
        if received.get("state") != state:
            print("❌ The redirect did not carry the expected state value — ignoring it.")
            return None
        if not received.get("code"):
            print(f"❌ Google returned no code: {received.get('error', 'unknown error')}")
            return None
        return received["code"]

    @classmethod
    def _exchange_code(cls, client_id: str, client_secret: str, code: str) -> Optional[str]:
        """
        Trades the authorisation code for a refresh token.

        Args:
            client_id (str): Google OAuth client ID.
            client_secret (str): Google OAuth client secret.
            code (str): The authorisation code from the consent redirect.

        Returns:
            Optional[str]: The refresh token, or None.
        """
        payload = urllib.parse.urlencode({
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode()
        try:
            with urllib.request.urlopen(GOOGLE_TOKEN_URL, data=payload, timeout=30) as response:
                token = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"❌ Token exchange failed: {exc}")
            return None

        refresh = token.get("refresh_token")
        if not refresh:
            print("❌ Google returned no refresh token.")
            print("💡 Revoke Puffin at https://myaccount.google.com/permissions and try again — "
                  "Google only issues one on a fresh consent.")
            return None
        return refresh
