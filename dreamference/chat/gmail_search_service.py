"""
Gmail Search Service for Onyx.

This module provides the GmailSearchService class, an HTTP service that turns a Gmail mailbox into
the two operations an assistant needs, and the OpenAPI description Onyx registers it under.

It mirrors how web search already works rather than inventing a new shape. Web search gives the
model `web_search` to find things and `open_url` to read one; this gives it `gmail_search` to find
messages and `gmail_message` to read one. The split matters for the same reason it does there: a
mailbox search that returned full bodies would fill the context with ten threads to answer a
question about one.

**The transport is IMAP with XOAUTH2, authorised through GNOME's Google OAuth client.** That client
is verified by Google for `https://mail.google.com/`, so the consent screen is the normal one, with no
unverified-app warning and no Cloud project of the user's own. GNOME itself is not involved at run
time: the service performs the OAuth flow (PKCE, offline access) itself, with the client id and
secret compiled in as defaults and overridable through `GOA_GOOGLE_CLIENT_ID` /
`GOA_GOOGLE_CLIENT_SECRET`. Two earlier routes were built and removed -- an app password (unavailable
under Advanced Protection or to many Workspace users) and a Google client of the user's own (a Cloud
project, a consent screen, a publishing decision) -- as was an intermediate design in which GNOME
Online Accounts on the host held the refresh token.

What shapes the rest:

* **One sealed credential per Google address.** The refresh token is stored sealed with a key kept
  beside it (obfuscation with a stated threat model, not secret management), with the current access
  token, refreshed when under a minute remains. Several accounts can be connected; a search reports
  failures per account instead of dropping them.
* **Connecting is a small POST surface.** `POST /api/google/oauth/start` returns the consent URL,
  Google redirects back to this service, or the user pastes the final URL into
  `POST /api/google/oauth/complete`; `POST /disconnect` removes an account.
* **Read-only, and Gmail's search syntax is preserved exactly.** Messages are read with `BODY.PEEK`
  so nothing is marked read, and `X-GM-RAW` hands the query to the same engine the web UI uses --
  `from:alice invoice`, `newer_than:7d`, `has:attachment`.

The module is deliberately **standard library only and self-contained**. It runs inside a stock
`python:3-slim` container with nothing installed into it, and it is copied next to the credentials
rather than mounted from the source tree, so the container has no idea where Dreamference lives.

**The shared secret is the security boundary.** The service sits on Onyx's Docker network holding a
credential for a real mailbox, and Onyx's custom-tool client performs no SSRF validation -- it
calls whatever URL the tool names. The header check is what stops anything else on that network
reading the user's mail.
"""

import base64
import email
import email.header
import email.utils
import hashlib
import hmac
import html
import imaplib
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional, Tuple

# Drive and Calendar for Mightling's apps. In the container this file runs as /config/service.py with
# the reader staged beside it, so the plain import is the one that works there.
try:
    from dreamference.chat.google_workspace_reader import CALENDAR_SCOPE, DRIVE_SCOPE, GoogleWorkspaceReader
except ImportError:  # pragma: no cover - the container's layout
    from google_workspace_reader import CALENDAR_SCOPE, DRIVE_SCOPE, GoogleWorkspaceReader  # type: ignore

# Inside the container the credentials directory is mounted here.
CONFIG_DIR: Final[str] = os.environ.get("MIGHTLING_GMAIL_CONFIG", "/config")
CREDENTIALS_NAME: Final[str] = "credentials.json"

# The key the stored token is sealed with. See `_seal`: this is obfuscation with a clear threat
# model, not a secret-management system.
KEY_NAME: Final[str] = "credentials.key"

GOOGLE_OAUTH_CLIENT_ID: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_ID", "44438659992-7kgjeitenc16ssihbtdjbgguch7ju55s.apps.googleusercontent.com")
GOOGLE_OAUTH_CLIENT_SECRET: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_SECRET", "-gMLuQyDiI0XrQS_vx_mhuYF")
GMAIL_SCOPE: Final[str] = "https://mail.google.com/"
EMAIL_SCOPE: Final[str] = "https://www.googleapis.com/auth/userinfo.email"
GOOGLE_OAUTH_SCOPES: Final[str] = EMAIL_SCOPE + " " + GMAIL_SCOPE

# What each of Mightling's apps asks Google for (specs/DREAMFERENCE_MIGHTLING_APPS.md §5.2). The full Drive
# and Calendar scopes: GNOME's client is refused the read-only ones ("This app is blocked", measured
# 2026-10-03), so read-only is enforced here, where no write request exists.
APP_SCOPES: Final[dict[str, str]] = {
    "gmail": GMAIL_SCOPE,
    "drive": DRIVE_SCOPE,
    "calendar": CALENDAR_SCOPE,
}
APP_NAMES: Final[dict[str, str]] = {
    "gmail": "Gmail",
    "drive": "Google Drive",
    "calendar": "Google Calendar",
}

# Google's consent screen lets each permission be unticked. A grant without Gmail access still
# signs in and still names the address, but IMAP refuses it as "Invalid credentials" -- so such a
# grant used to be saved, listed as connected, and fail on every search.
MISSING_GMAIL_SCOPE: Final[str] = (
    "Google did not grant Gmail access for {email}. Connect it again and leave the box "
    "\"Read, compose, send and permanently delete all your email from Gmail\" ticked."
)
# What Google grants an access token for when its reply does not say.
TOKEN_LIFETIME_SECONDS: Final[int] = 3599
MISSING_APP_SCOPE: Final[str] = (
    "Google did not grant {app} access for {email}. Connect it again and leave the {app} box ticked."
)
OAUTH_STATES: Dict[str, Dict[str, Any]] = {}


# Written by the host so the setup page can say what GNOME is holding. The service cannot look for
# itself: GOA lives on the session bus, and this runs in a container that has neither a session bus
# nor `gdbus` to ask with. One line the host knows and the container does not.
GNOME_HINT_NAME: Final[str] = "gnome-accounts.json"

# Onyx sends this with every tool call. The service is on a private Docker network, but so is
# everything else Onyx runs, and a private network is not an authorisation boundary -- the shared
# secret is what distinguishes Onyx from everything else.
AUTH_HEADER: Final[str] = "X-Mightling-Gmail-Token"

IMAP_HOST: Final[str] = "imap.gmail.com"
IMAP_PORT: Final[int] = 993

# Gmail allows fifteen simultaneous IMAP connections per account. The service opens one per request
# and closes it, which keeps it far below that without any pooling; the timeout is what stops a
# stalled connection holding an Onyx tool call open indefinitely.
IMAP_TIMEOUT_SECONDS: Final[int] = 30

# Gmail's own name for the folder holding every message exactly once. Selecting it rather than
# INBOX means a search covers archived mail, and selecting it rather than each label in turn means
# a message with three labels is found once. The literal name is localised, so it is discovered
# through the `\All` special-use attribute and this constant is only the fallback.
ALL_MAIL_ATTRIBUTE: Final[str] = "\\All"
ALL_MAIL_FALLBACK: Final[str] = "[Gmail]/All Mail"

# Gmail counts a "page" in messages, and each one costs a header fetch. Twenty is enough for the
# model to choose from and still answers in about a second.
DEFAULT_RESULT_LIMIT: Final[int] = 10
MAX_RESULT_LIMIT: Final[int] = 20

# How much of a message body to return. Long threads are quoted repeatedly, and the tail is almost
# always older quoted copies of what is already above it.
MAX_BODY_CHARACTERS: Final[int] = 20_000

SERVICE_PORT: Final[int] = 8000

# The service is also published on this loopback port, because the browser reaches it from outside
# the Docker network: the Onyx page asks whether Gmail is connected so it knows whether to show the
# Connect button, and the setup page is served from here.
HOST_PORT: Final[int] = 8767
HOST_ORIGIN: Final[str] = f"http://localhost:{HOST_PORT}"

# The page origin allowed to read the status endpoint. The browser calls it cross-origin, from the
# Onyx UI to this service.
ONYX_ORIGIN: Final[str] = "http://localhost:3000"

# The setup page. It only ever explains -- there is nothing to submit.
CONNECT_PATH: Final[str] = "/connect"

# What the button pointed at under earlier designs. Kept as a redirect because Onyx serves its
# bundles `immutable`, so a browser that has not hard-refreshed still holds a script aiming here.
LEGACY_START_PATH: Final[str] = "/oauth/start"

# Where the user signs into Google. GNOME's Settings panel, opened by URI so the page can link
# straight at it rather than describing a path through a menu.
GNOME_SETTINGS_URI: Final[str] = "gnome-control-center://online-accounts"

# What an unconnected search answers with. It names the place the user can act rather than a
# command they would have to leave the app to run -- the model reads this and relays it.
NOT_CONNECTED_MESSAGE: Final[str] = (
    "Gmail is not connected. Open Settings -> Gmail Accounts in Mightling and choose Connect to Google."
)

# Enough styling that the setup page reads as part of Mightling rather than as a server error. It is
# the only page this project serves directly, and the user arrives at it from a polished UI.
PAGE_STYLE: Final[str] = (
    # The same neutral idiom as the patched Onyx UI (the share sheet, the connector card):
    # near-black text, grey secondary, hairline borders, a black pill for the one primary action.
    "body{margin:0;padding:48px 24px;background:#fff;color:#111827;"
    "font-family:Roboto,system-ui,sans-serif;font-size:14px;line-height:1.55}"
    "main{max-width:520px;margin:0 auto}"
    "h2{margin:0 0 6px;font-size:22px;font-weight:700;letter-spacing:-.01em}"
    "p{margin:0 0 14px}"
    "code{background:#f3f4f6;border-radius:4px;padding:1px 5px;font-family:'Roboto Mono',monospace;"
    "font-size:12.5px}"
    "a{color:#374151;text-decoration:underline}"
    "a:hover{color:#111827}"
    ".muted{color:#6b7280;font-size:13px}"
    "ol{counter-reset:step;list-style:none;padding-left:0;margin:0 0 18px}"
    "ol li{margin-bottom:16px;padding-left:30px;position:relative}"
    "ol li::before{counter-increment:step;content:counter(step);position:absolute;left:0;top:1px;"
    "width:20px;height:20px;border-radius:50%;background:#f3f4f6;color:#374151;"
    "font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center}"
    ".note{margin:20px 0;padding:14px 16px;border-radius:12px;background:#f9fafb;"
    "border:1px solid #e5e7eb;color:#6b7280;font-size:13px}"
    ".ready{margin:0 0 18px;padding:14px 16px;border-radius:12px;background:#f9fafb;"
    "border:1px solid #e5e7eb;color:#374151}"
    ".btn-primary{display:inline-block;padding:10px 18px;border:none;border-radius:12px;"
    "background:#111827;color:#fff;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer}"
    ".btn-primary:hover{background:#1f2937}"
    ".btn-secondary{display:inline-block;padding:8px 14px;border:1px solid #e5e7eb;border-radius:10px;"
    "background:#fff;color:#374151;font-size:13px;font-weight:500;font-family:inherit;cursor:pointer}"
    ".btn-secondary:hover{background:#f9fafb}"
    "input[type=text]{width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:10px;"
    "border:1px solid #e5e7eb;border-radius:10px;font-size:14px;font-family:inherit;color:#111827}"
    "input[type=text]:focus{outline:2px solid #d1d5db;outline-offset:0;border-color:#d1d5db}"
    ".divider{margin-top:28px;border-top:1px solid #f3f4f6;padding-top:20px}"
)


class GmailSearchService:
    """
    Serves Gmail search and message retrieval over HTTP for Onyx's custom tool.
    """

    # ------------------------------------------------------------------ storage

    @classmethod
    def _key(cls, directory: Optional[str] = None) -> bytes:
        """
        Returns the sealing key, creating it on first use.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bytes: A 32-byte key.
        """
        path = os.path.join(directory or CONFIG_DIR, KEY_NAME)
        try:
            with open(path, "rb") as handle:
                key = handle.read()
            if len(key) == 32:
                return key
        except OSError:
            pass
        key = secrets.token_bytes(32)
        with open(path, "wb") as handle:
            handle.write(key)
        os.chmod(path, 0o600)
        return key

    @classmethod
    def _keystream(cls, key: bytes, nonce: bytes, length: int) -> bytes:
        """
        Expands the key into `length` bytes, HMAC-SHA256 in counter mode.

        Args:
            key (bytes): The sealing key.
            nonce (bytes): Per-message nonce.
            length (int): How many bytes are needed.

        Returns:
            bytes: The keystream.
        """
        out = b""
        counter = 0
        while len(out) < length:
            out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
            counter += 1
        return out[:length]

    @classmethod
    def _seal(cls, plaintext: str, directory: Optional[str] = None) -> str:
        """
        Encrypts the access token for storage, encrypt-then-MAC.

        **This is obfuscation with a clear threat model, and it is worth being precise about what
        it buys.** The key sits in the same directory as the ciphertext, because the service is
        headless: anything it can read at startup, an attacker holding that directory can read too.
        What sealing does prevent is a live token being legible in a file that travels -- a backup,
        a `cat` over someone's shoulder, a grep through a copied config tree. It is not a defence
        against local root, and nothing here pretends otherwise. It matters less than it did when
        this file held an app password: the token inside expires within the hour.

        Args:
            plaintext (str): The access token.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            str: Base64 of nonce + tag + ciphertext.
        """
        key = cls._key(directory)
        nonce = secrets.token_bytes(16)
        data = plaintext.encode("utf-8")
        cipher = bytes(a ^ b for a, b in zip(data, cls._keystream(key, nonce, len(data))))
        tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
        return base64.b64encode(nonce + tag + cipher).decode("ascii")

    @classmethod
    def _unseal(cls, blob: str, directory: Optional[str] = None) -> Optional[str]:
        """
        Recovers a sealed token, or None if it does not authenticate.

        Args:
            blob (str): The value `_seal` produced.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Optional[str]: The token, or None.
        """
        try:
            raw = base64.b64decode(blob.encode("ascii"))
        except (ValueError, AttributeError):
            return None
        if len(raw) < 32:
            return None
        nonce, tag, cipher = raw[:16], raw[16:32], raw[32:]
        key = cls._key(directory)
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected):
            return None
        try:
            return bytes(
                a ^ b for a, b in zip(cipher, cls._keystream(key, nonce, len(cipher)))
            ).decode("utf-8")
        except UnicodeDecodeError:
            return None

    @classmethod
    def _raw(cls, directory: Optional[str] = None) -> Dict[str, Any]:
        """Reads credentials file. Migrates old single-account format if needed."""
        try:
            with open(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {"accounts": {}}
        if not isinstance(stored, dict):
            return {"accounts": {}}
        if "accounts" not in stored:
            # migrate legacy
            if "email" in stored and "access_token" in stored:
                return {"accounts": {stored["email"]: stored}}
            return {"accounts": {}}
        return stored

    @classmethod
    def credentials(cls, directory: Optional[str] = None) -> List[Dict[str, str]]:
        """Returns all valid valid accounts."""
        import json, time, urllib.request, urllib.parse
        stored = cls._raw(directory)
        valid = []
        for address, acc in stored.get("accounts", {}).items():
            sealed = acc.get("access_token")
            if not sealed: continue
            
            if float(acc.get("expires_at", 0)) - time.time() < 60:
                sealed_refresh = acc.get("refresh_token")
                if not sealed_refresh: continue
                refresh = cls._unseal(sealed_refresh, directory)
                if not refresh: continue
                
                req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token"
                }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as res:
                        data = json.load(res)
                        if "access_token" in data:
                            cls.save_token(address, data["access_token"], data.get("expires_in", 3599), directory, refresh_token=refresh)
                            valid.append({"email": address, "access_token": data["access_token"],
                                          "scopes": cls.account_scopes(acc)})
                except Exception:
                    pass
            else:
                token = cls._unseal(sealed, directory)
                if token:
                    valid.append({"email": address, "access_token": token, "scopes": cls.account_scopes(acc)})
        return valid


    @classmethod
    def delete_token(cls, address: str, directory: Optional[str] = None) -> bool:
        import os, json
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        stored = cls._raw(directory)
        accounts = stored.get("accounts", {})
        if address in accounts:
            del accounts[address]
            try:
                with open(path, "w") as handle:
                    json.dump({"accounts": accounts}, handle, indent=2)
                return True
            except OSError:
                return False
        return False
        
    @classmethod
    def save_token(
        cls,
        address: str,
        token: str,
        lifetime: int,
        directory: str | None = None,
        refresh_token: str | None = None,
        scopes: list[str] | None = None,
    ) -> bool:
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        stored = cls._raw(directory)
        accounts = stored.get("accounts", {})
        
        acc = accounts.get(address, {"email": address})
        # What the account held before: a stored token without recorded scopes was a Gmail grant.
        previous = cls.account_scopes(acc) if acc.get("access_token") else []
        acc["access_token"] = cls._seal(token, directory)
        acc["expires_at"] = time.time() + max(0, lifetime - 60)
        if refresh_token:
            acc["refresh_token"] = cls._seal(refresh_token, directory)
        if scopes:
            acc["scopes"] = sorted(set(previous) | set(scopes))
        accounts[address] = acc
        
        try:
            with open(path, "w") as handle:
                json.dump({"accounts": accounts}, handle, indent=2)
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True

    @classmethod
    def forget(cls, directory: Optional[str] = None) -> bool:
        """
        Removes the stored token, which is what disconnecting means here.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bool: True if nothing is stored afterwards.
        """
        try:
            os.remove(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME))
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    @classmethod
    def gnome_accounts(cls, directory: Optional[str] = None) -> List[str]:
        """
        Reads the Google addresses the host found in GNOME Online Accounts.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            List[str]: Addresses, empty if GNOME has none or the host never looked.
        """
        try:
            with open(os.path.join(directory or CONFIG_DIR, GNOME_HINT_NAME)) as handle:
                found = json.load(handle)
        except (OSError, ValueError):
            return []
        return [str(entry) for entry in found] if isinstance(found, list) else []

    @classmethod
    def save_gnome_accounts(cls, addresses: List[str], directory: Optional[str] = None) -> bool:
        """
        Records what the host saw in GNOME, for the setup page to report.

        Args:
            addresses (List[str]): Google addresses GOA is holding.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bool: True if the file was written.
        """
        try:
            with open(os.path.join(directory or CONFIG_DIR, GNOME_HINT_NAME), "w") as handle:
                json.dump(addresses, handle)
        except OSError:
            return False
        return True

    @classmethod
    def account_scopes(cls, account: dict[str, Any]) -> list[str]:
        """Tells which scopes a stored account was granted.

        Args:
            account (dict[str, Any]): One entry of the credentials file.

        Returns:
            list[str]: The recorded scopes; an account saved before scopes were recorded was a
                Gmail grant, the only kind there was.
        """
        return list(account.get("scopes") or [GMAIL_SCOPE])

    @classmethod
    def status(cls) -> dict[str, Any]:
        """Says which accounts are connected and what each was granted. Unauthenticated: addresses
        and scope names only, never mail or files.

        Returns:
            dict[str, Any]: `connected` and `email` (comma-separated, as the web UI reads them) and
                `accounts`, each with its `scopes`, which `/apps` reads.
        """
        creds = cls.credentials()
        connected = len(creds) > 0
        email = ", ".join(c["email"] for c in creds) if connected else None
        accounts = [{"email": c["email"], "scopes": c.get("scopes") or [GMAIL_SCOPE]} for c in creds]
        return {"configured": connected, "connected": connected, "email": email, "accounts": accounts}

    # ------------------------------------------------------------------ IMAP

    @classmethod
    def _xoauth2(cls, address: str, token: str):
        """
        Builds the responder imaplib's AUTHENTICATE calls for the XOAUTH2 exchange.

        Two details are the difference between working and hanging, and neither is guessable:

        * **Return raw bytes, not base64.** imaplib encodes whatever the responder returns; a
          pre-encoded string is encoded twice and Gmail rejects it with an error that reads like a
          bad password.
        * **Answer the second challenge with nothing.** On a token Google will not accept, the
          server does not fail the command -- it sends a continuation challenge carrying a
          base64 JSON error, and imaplib calls the responder again. The protocol's way to draw out
          the real `NO` response is an empty reply; returning the credential again instead leaves
          the exchange going nowhere until the socket timeout, so an expired token would surface
          as a stall rather than as an error.

        Args:
            address (str): The account the token authenticates as.
            token (str): A Google access token.

        Returns:
            A callable suitable for `IMAP4.authenticate`.
        """
        payload = f"user={address}\x01auth=Bearer {token}\x01\x01".encode()
        answered = []

        def respond(_challenge: bytes) -> bytes:
            if answered:
                return b""
            answered.append(True)
            return payload

        return respond

    @classmethod
    def _open_mailboxes(
        cls,
    ) -> Tuple[Dict[str, imaplib.IMAP4_SSL], Optional[str], List[Dict[str, str]]]:
        """
        Opens every connected account's All Mail folder read-only.

        An account that fails to open is reported rather than skipped: with several accounts, a
        silent skip makes "that account is broken" look exactly like "nothing matched there".

        Returns:
            The open connections by address, an error when none could be opened, and one
            `{"account", "error"}` entry per account that failed.
        """
        stored = cls.credentials()
        if not stored:
            return {}, NOT_CONNECTED_MESSAGE, []
        connections = {}
        failures: List[Dict[str, str]] = []
        for cred in stored:
            try:
                conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
                conn.authenticate("XOAUTH2", cls._xoauth2(cred["email"], cred["access_token"]))
                conn.select(f'"{cls._all_mail_folder(conn)}"', readonly=True)
                connections[cred["email"]] = conn
            except Exception as error:
                failures.append({"account": cred["email"], "error": cls._describe(error)})
        if not connections:
            return {}, "Could not connect to any Gmail accounts.", failures
        return connections, None, failures

    @classmethod
    def grants_scope(cls, token_response: dict[str, Any], scope: str) -> bool:
        """Tells whether a token response carries a scope.

        Args:
            token_response (dict[str, Any]): Google's token endpoint reply.
            scope (str): The scope the app needs.

        Returns:
            bool: True unless the reply lists its scopes and this one is not among them.
        """
        granted = token_response.get("scope")
        return granted is None or scope in str(granted).split()

    @classmethod
    def granted_scopes(cls, token_response: dict[str, Any]) -> list[str]:
        """Lists the scopes a token response names.

        Args:
            token_response (dict[str, Any]): Google's token endpoint reply.

        Returns:
            list[str]: Its scopes; Gmail's alone when the reply names none (the old behaviour).
        """
        return str(token_response.get("scope") or GMAIL_SCOPE).split()

    @classmethod
    def auth_url(cls, app: str, state: str, challenge: str) -> str:
        """Builds Google's consent URL for one app.

        Args:
            app (str): `gmail`, `drive` or `calendar`; anything else means Gmail.
            state (str): The OAuth state.
            challenge (str): The PKCE challenge.

        Returns:
            str: The URL. `include_granted_scopes` keeps an account's earlier grants, so connecting
                Drive to an account that has Gmail keeps Gmail.
        """
        scopes = EMAIL_SCOPE + " " + APP_SCOPES.get(app, GMAIL_SCOPE)
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": HOST_ORIGIN + "/",
            "response_type": "code", "scope": scopes, "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state,
        })

    @classmethod
    def missing_scope_message(cls, app: str, address: str) -> str:
        """Says which box to tick when Google returned a grant without the app's scope.

        Args:
            app (str): The app being connected.
            address (str): The account.

        Returns:
            str: The message; Gmail's keeps its own wording.
        """
        if app == "gmail" or app not in APP_SCOPES:
            return MISSING_GMAIL_SCOPE.format(email=address)
        return MISSING_APP_SCOPE.format(app=APP_NAMES[app], email=address)

    @classmethod
    def accept_grant(cls, app: str, token_response: dict[str, Any], address: str) -> str | None:
        """Checks a grant against the app it was asked for, and stores it if it fits.

        Args:
            app (str): `gmail`, `drive` or `calendar`.
            token_response (dict[str, Any]): Google's token endpoint reply.
            address (str): The account it is for.

        Returns:
            str | None: None when stored; otherwise the message naming the box to tick.
        """
        if not cls.grants_scope(token_response, APP_SCOPES.get(app, GMAIL_SCOPE)):
            return cls.missing_scope_message(app, address)
        cls.save_token(
            address,
            token_response["access_token"],
            token_response.get("expires_in", TOKEN_LIFETIME_SECONDS),
            refresh_token=token_response.get("refresh_token"),
            scopes=cls.granted_scopes(token_response),
        )
        return None

    @classmethod
    def grant_page(cls, app: str, refused: str | None) -> str:
        """Says, on the page Google redirects to, how connecting went.

        Args:
            app (str): The app connected.
            refused (str | None): Why the grant was refused, or None when it was stored.

        Returns:
            str: The page's HTML body.
        """
        name = html.escape(APP_NAMES.get(app, "Gmail"))
        if refused:
            return f"<h2>{name} access was not granted</h2><p>{html.escape(refused)}</p>"
        return (
            f"<h2>{name} connected</h2><p>You can close this tab. In <code>ling</code>, choose "
            "<b>I've connected it</b> in <code>/apps</code>; the tools arrive when you restart it "
            "or run <code>ling resume</code>.</p>"
        )

    @classmethod
    def grants_gmail(cls, token_response: Dict[str, Any]) -> bool:
        """
        Tells whether a token response carries Gmail access.

        Args:
            token_response (Dict[str, Any]): Google's token endpoint reply.

        Returns:
            bool: True unless the reply lists its scopes and Gmail's is not among them.
        """
        scope = token_response.get("scope")
        return scope is None or GMAIL_SCOPE in str(scope).split()

    @classmethod
    def _describe(cls, error: Exception) -> str:
        """
        Turns an IMAP failure into one readable line.

        imaplib raises with the server's response as bytes, which prints as `b'...'`.

        Args:
            error (Exception): The failure.

        Returns:
            str: A short description.
        """
        detail = error.args[0] if error.args else error
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        detail = str(detail) or type(error).__name__
        if "AUTHENTICATIONFAILED" in detail:
            # Most often a grant made with Gmail access unticked, or one revoked since.
            detail += " -- reconnect this account and allow Gmail access"
        return detail

    @classmethod
    def _all_mail_folder(cls, connection: imaplib.IMAP4_SSL) -> str:
        """
        Finds the folder holding every message once, by its special-use attribute.

        The English name `[Gmail]/All Mail` is what most examples hard-code, and it is wrong for
        every account whose interface language is not English. The `\\All` attribute in the LIST
        response is the same folder under any language.

        Args:
            connection (imaplib.IMAP4_SSL): A logged-in connection.

        Returns:
            str: The folder name.
        """
        try:
            status, rows = connection.list()
        except (imaplib.IMAP4.error, OSError):
            return ALL_MAIL_FALLBACK
        if status != "OK":
            return ALL_MAIL_FALLBACK
        for row in rows or []:
            line = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
            if ALL_MAIL_ATTRIBUTE not in line:
                continue
            # `(\HasNoChildren \All) "/" "[Gmail]/All Mail"` -- the name is the last quoted field.
            quoted = re.findall(r'"([^"]*)"', line)
            if quoted:
                return quoted[-1]
        return ALL_MAIL_FALLBACK

    @classmethod
    def _search_uids(cls, connection: imaplib.IMAP4_SSL, query: str) -> List[bytes]:
        """
        Runs a Gmail search and returns the matching UIDs, newest last.

        `X-GM-RAW` hands the string to the same engine the Gmail web UI uses, which is why the
        tool's documented syntax survived every change of transport unchanged.

        **The query is always sent as a literal with `CHARSET UTF-8`.** imaplib encodes ordinary
        arguments as ASCII, so a Cyrillic or accented search would raise before it ever reached
        Google -- and this mailbox is partly Russian. A literal also needs no quoting, which
        removes the other half of the problem: a query containing a quote or a backslash.

        Args:
            connection (imaplib.IMAP4_SSL): A connection with a folder selected.
            query (str): Gmail search syntax.

        Returns:
            List[bytes]: Matching UIDs.
        """
        try:
            connection.literal = query.encode("utf-8")
            status, data = connection.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        except (imaplib.IMAP4.error, OSError):
            status, data = "NO", None
        if status != "OK" and query.isascii():
            # Some servers reject CHARSET on a non-standard search key. An ASCII query can be sent
            # the ordinary quoted way, so it is worth one retry before giving up.
            try:
                escaped = query.replace("\\", "\\\\").replace('"', '\\"')
                status, data = connection.uid("SEARCH", "X-GM-RAW", f'"{escaped}"')
            except (imaplib.IMAP4.error, OSError):
                return []
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    @classmethod
    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        """
        Finds messages matching a Gmail search query.

        Returns:
            Dict[str, Any]: `messages`, newest first, plus `errors` naming each account that could
                not be searched -- present only when one failed, so a clean answer keeps the shape
                the web UI's tool has always read. `error` alone when no account could be opened.
        """
        connections, error, failures = cls._open_mailboxes()
        if not connections:
            return {"error": error, "errors": failures} if failures else {"error": error}
        
        limit = max(1, min(limit, MAX_RESULT_LIMIT))
        all_messages = []
        for address, connection in connections.items():
            try:
                uids = cls._search_uids(connection, query)
                if uids:
                    # Newest first: IMAP returns UIDs ascending, and recency is what a mailbox
                    # question almost always means.
                    messages = cls._fetch_headers(connection, uids[-limit:][::-1])
                    for m in messages:
                        m["id"] = f"{address}|{m['id']}"
                    all_messages.extend(messages)
            except Exception as failure:
                failures.append({"account": address, "error": cls._describe(failure)})
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
        
        answer: Dict[str, Any] = {"messages": all_messages[:limit]}
        if failures:
            answer["errors"] = failures
        return answer

    @classmethod
    def _fetch_headers(cls, connection: imaplib.IMAP4_SSL, uids: List[bytes]) -> List[Dict[str, str]]:
        """
        Fetches just the headers a result list needs.

        `BODY.PEEK` rather than `BODY` throughout: a search tool that silently marked twenty
        messages as read would be doing real damage to a mailbox.

        Args:
            connection (imaplib.IMAP4_SSL): A connection with a folder selected.
            uids (List[bytes]): UIDs to describe.

        Returns:
            List[Dict[str, str]]: One summary per message.
        """
        results: List[Dict[str, str]] = []
        for uid in uids:
            try:
                status, data = connection.uid(
                    "FETCH", uid.decode("ascii"),
                    "(X-GM-MSGID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
                )
            except (imaplib.IMAP4.error, OSError):
                continue
            if status != "OK" or not data:
                continue
            prefix, headers = b"", b""
            for item in data:
                if isinstance(item, tuple):
                    prefix, headers = item[0], item[1]
                    break
            message = email.message_from_bytes(headers)
            results.append({
                "id": cls._message_id(prefix),
                "from": cls._decode_header(message.get("From", "")),
                "subject": cls._decode_header(message.get("Subject", "")),
                "date": cls._decode_header(message.get("Date", "")),
            })
        return results

    @classmethod
    def _message_id(cls, prefix: bytes) -> str:
        """
        Pulls Gmail's stable message id out of a FETCH response prefix.

        `X-GM-MSGID` is the right id to hand the model rather than the UID: a UID is only
        meaningful inside one folder of one session, while the Gmail id is stable and searchable.

        Args:
            prefix (bytes): The untagged FETCH line, e.g. `1 (X-GM-MSGID 1234 BODY[...] {n}`.

        Returns:
            str: The id, or an empty string.
        """
        found = re.search(rb"X-GM-MSGID\s+(\d+)", prefix or b"")
        return found.group(1).decode("ascii") if found else ""

    @classmethod
    def _fetch_body(cls, connection: imaplib.IMAP4_SSL, uid: str) -> Dict[str, Any]:
        """
        Reads one message in full, without marking it read.

        Args:
            connection (imaplib.IMAP4_SSL): A connection with a folder selected.
            uid (str): The message's UID in that folder.

        Returns:
            Dict[str, Any]: The headers and the plain-text body, or an `error`.
        """
        try:
            status, fetched = connection.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
        except (imaplib.IMAP4.error, OSError):
            return {"error": "Message could not be read."}
        prefix, raw = b"", b""
        for item in fetched or []:
            if isinstance(item, tuple):
                prefix, raw = item[0], item[1]
                break
        if status != "OK" or not raw:
            return {"error": "Message could not be read."}
        parsed = email.message_from_bytes(raw)
        return {
            "id": cls._message_id(prefix),
            "from": cls._decode_header(parsed.get("From", "")),
            "to": cls._decode_header(parsed.get("To", "")),
            "subject": cls._decode_header(parsed.get("Subject", "")),
            "date": cls._decode_header(parsed.get("Date", "")),
            "body": cls._extract_text(parsed)[:MAX_BODY_CHARACTERS],
        }

    @classmethod
    def workspace(cls, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        """Answers the read-only Drive and Calendar endpoints (specs/DREAMFERENCE_MIGHTLING_APPS.md §9,
        §9a): `/drive/search`, `/drive/file/<id>`, `/calendar/events`, `/calendar/event/<calendar>/<id>`.

        Args:
            path (str): The request path.
            query (Dict[str, List[str]]): Its parsed query string.

        Returns:
            Dict[str, Any]: The reader's answer, or `error`.
        """
        def arg(name: str, default: str = "") -> str:
            return (query.get(name) or [default])[0]

        def number(name: str, default: int) -> int:
            try:
                return int(arg(name, str(default)))
            except ValueError:
                return default

        accounts = cls.credentials()
        if not accounts:
            return {"error": NOT_CONNECTED_MESSAGE}
        segments = [urllib.parse.unquote(part) for part in path.split("/")[2:]]
        if path == "/drive/search":
            terms = arg("q")
            return GoogleWorkspaceReader.drive_search(accounts, terms, number("limit", 10)) if terms else {"error": "q is required"}
        if path.startswith("/drive/file/") and len(segments) == 2:
            return GoogleWorkspaceReader.drive_read(accounts, segments[1])
        if path == "/calendar/events":
            now = time.time()
            start = arg("from") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            end = arg("to") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7 * 86400))
            return GoogleWorkspaceReader.calendar_events(
                accounts, start, end, arg("calendar"), arg("q"), number("limit", 25),
            )
        if path.startswith("/calendar/event/") and len(segments) == 3:
            return GoogleWorkspaceReader.calendar_event(accounts, segments[1], segments[2])
        return {"error": "not found"}

    @classmethod
    def message(cls, message_id: str) -> Dict[str, Any]:
        """Reads one message body."""
        if "|" in message_id:
            email, real_id = message_id.split("|", 1)
        else:
            email, real_id = None, message_id
            
        connections, error, _failures = cls._open_mailboxes()
        if not connections:
            return {"error": error}
            
        if email and email in connections:
            conn = connections[email]
            try:
                status, data = conn.uid("SEARCH", None, "X-GM-MSGID", real_id)
                if status == "OK" and data[0]:
                    uid = data[0].split()[0].decode("ascii")
                    return cls._fetch_body(conn, uid)
            except Exception as e:
                return {"error": str(e)}
            finally:
                for c in connections.values():
                    try:
                        c.logout()
                    except:
                        pass
            return {"error": "Message not found"}

        for conn in connections.values():
            try:
                status, data = conn.uid("SEARCH", None, "X-GM-MSGID", real_id)
                if status == "OK" and data[0]:
                    uid = data[0].split()[0].decode("ascii")
                    res = cls._fetch_body(conn, uid)
                    if res and "error" not in res:
                        return res
            except Exception:
                pass
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        return {"error": "Message not found"}
    @classmethod
    def _close(cls, connection: imaplib.IMAP4_SSL) -> None:
        """
        Ends a connection without letting its failure surface as the request's failure.

        Args:
            connection (imaplib.IMAP4_SSL): The connection to close.
        """
        for step in (connection.close, connection.logout):
            try:
                step()
            except (imaplib.IMAP4.error, OSError):
                pass

    @classmethod
    def _decode_header(cls, value: str) -> str:
        """
        Turns an RFC 2047 encoded header into plain text.

        Args:
            value (str): The raw header value.

        Returns:
            str: The decoded value.
        """
        if not value:
            return ""
        parts = []
        for chunk, charset in email.header.decode_header(value):
            if isinstance(chunk, bytes):
                parts.append(chunk.decode(charset or "utf-8", "replace"))
            else:
                parts.append(chunk)
        return "".join(parts).strip()

    @classmethod
    def _extract_text(cls, message: Message) -> str:
        """
        Returns the readable body: the plain-text part, or HTML stripped down to text.

        Args:
            message (Message): The parsed message.

        Returns:
            str: The body text, empty if there is none.
        """
        plain, markup = "", ""

        def decode(part: Message) -> str:
            payload = part.get_payload(decode=True)
            if payload is None:
                return ""
            charset = part.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, "replace")
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", "replace")

        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            # Attachments are skipped: a search tool answering from a PDF would need a parser this
            # container does not have, and the filename is already in the body's context.
            if (part.get("Content-Disposition") or "").lower().startswith("attachment"):
                continue
            if part.get_content_type() == "text/plain" and not plain:
                plain = decode(part)
            elif part.get_content_type() == "text/html" and not markup:
                markup = decode(part)

        if plain:
            return plain
        if not markup:
            return ""
        without_scripts = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_scripts)).strip()

    # ------------------------------------------------------------------ HTTP

    @classmethod
    def serve(cls, port: int = SERVICE_PORT, secret: Optional[str] = None) -> None:
        """
        Runs the HTTP service until killed.

        Args:
            port (int): Port to listen on.
            secret (Optional[str]): Required value of the shared-secret header. Taken from the
                environment when not given.
        """
        expected = secret or os.environ.get("MIGHTLING_GMAIL_SECRET", "")

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

            def _html(self, inner: str) -> None:
                # The link back matters more than it looks. In the browser this page opens in a new
                # tab and is disposable, but the desktop app has no new window to open, so it
                # navigates in place -- and without a way back the user is left staring at a bare
                # paragraph with no chrome to return from.
                body = (
                    '<!doctype html><html><head><meta charset="utf-8">'
                    '<title>Mightling</title><meta name="viewport" '
                    'content="width=device-width,initial-scale=1">'
                    f"<style>{PAGE_STYLE}</style></head><body><main>"
                    f"{inner}"
                    f'<p><a href="{ONYX_ORIGIN}/app">Back to Mightling</a></p>'
                    # Framed (in the connect modal) the link is surplus -- the modal has its own
                    # close, and navigating the iframe to the app would nest Mightling inside itself.
                    "<script>if(window.top!==window.self){var L=document.querySelectorAll('a');"
                    "for(var i=0;i<L.length;i++){if(L[i].textContent==='Back to Mightling')"
                    "L[i].style.display='none';}}</script>"
                    "</main></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _page(self, message: str) -> None:
                self._html(f"<h2>Mightling</h2><p>{message}</p>")

            def _setup_page(self, app: str = "gmail") -> None:
                """
                Serves the HTML for Mightling's own Google OAuth flow, for one app.

                Args:
                    app (str): `gmail`, `drive` or `calendar`: which scope the consent asks for.
                """
                name = APP_NAMES[app]
                full_access = "" if app == "gmail" else (
                    f'<p class="note">ⓘ Google will describe {name} access as full access ("see, edit, create and '
                    'delete"): GNOME\'s client may ask for nothing narrower. Mightling only reads; it has no way to '
                    'change or delete anything.</p>'
                )
                self._html(
                    f"""
                    <h2>Connect {name}</h2>
                    <p>Mightling authenticates directly with Google. What it reads stays on this machine.</p>
                    <p class="note">ⓘ The consent screen will say <b style="display:inline">GNOME</b> — Mightling authenticates through the GNOME desktop's Google integration. No Mightling credentials are sent to Google.</p>
                    {full_access}
                    <button id="start-btn" class="btn-primary">Authorize with Google</button>
                    <div class="divider">
                      <p class="muted">If Google's page ends at an address that does not load (a browser on
                      another machine), paste that address here:</p>
                      <input type="text" id="pasted" placeholder="http://localhost:8767/?state=…&amp;code=…">
                      <button id="paste-btn" class="btn-secondary">Finish connecting</button>
                      <p id="paste-result" class="muted"></p>
                    </div>
                    <p class="muted">Then return to <code>ling</code> and choose <b>I've connected it</b>.</p>
                    <script>
                        document.getElementById('start-btn').onclick = async () => {{
                            const res = await fetch('/api/google/oauth/start?app={app}', {{ method: 'POST' }});
                            const data = await res.json();
                            if (data.auth_url) window.open(data.auth_url, '_blank');
                        }};
                        document.getElementById('paste-btn').onclick = async () => {{
                            const url = document.getElementById('pasted').value;
                            const res = await fetch('/api/google/oauth/complete', {{
                                method: 'POST', body: JSON.stringify({{ url }}) }});
                            const data = await res.json();
                            document.getElementById('paste-result').textContent =
                                data.email ? 'Connected ' + data.email + '.' : (data.error || 'That did not work.');
                        }};
                    </script>
                    """
                )


            def do_OPTIONS(self) -> None:
                # The preflight must name the *page's* origin (Onyx), not this service's own;
                # answering with HOST_ORIGIN made the browser veto the POST before sending it.
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", ONYX_ORIGIN)
                self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/google/oauth/start":
                    app = (urllib.parse.parse_qs(parsed.query).get("app") or ["gmail"])[0]
                    app = app if app in APP_SCOPES else "gmail"
                    state = secrets.token_urlsafe(32)
                    verifier = secrets.token_urlsafe(32)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                    OAUTH_STATES[state] = {"code_verifier": verifier, "time": time.time(), "app": app}
                    self._reply(200, {"auth_url": cls.auth_url(app, state, challenge)})
                    return
                    

                if parsed.path == "/disconnect":
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        import json
                        data = json.loads(post_data)
                        email = data.get("email")
                        if email:
                            success = GmailSearchService.delete_token(email)
                            self._reply(200, {"status": "ok", "deleted": success})
                            return
                    except Exception as e:
                        self._reply(400, {"error": str(e)})
                        return
                    self._reply(400, {"error": "Invalid request"})
                    return
                if parsed.path == "/api/google/oauth/complete":
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        data = json.loads(post_data)
                        url = data.get("url", "")
                        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        if "code" in q and "state" in q:
                            code = q["code"][0]
                            state = q["state"][0]
                            if state in OAUTH_STATES:
                                verifier = OAUTH_STATES[state]["code_verifier"]
                                req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                                    "code": code,
                                    "code_verifier": verifier,
                                    "redirect_uri": HOST_ORIGIN + "/",
                                    "grant_type": "authorization_code"
                                }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                                with urllib.request.urlopen(req, timeout=10) as res:
                                    tdata = json.load(res)
                                
                                req2 = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {tdata['access_token']}"})
                                with urllib.request.urlopen(req2, timeout=10) as res2:
                                    user_data = json.load(res2)
                                    email_addr = user_data.get("email", "")
                                
                                app = OAUTH_STATES[state].get("app", "gmail")
                                if email_addr and "access_token" in tdata:
                                    refused = cls.accept_grant(app, tdata, email_addr)
                                    if refused:
                                        self._reply(400, {"error": refused})
                                    else:
                                        self._reply(200, {"status": "ok", "email": email_addr})
                                    return
                    except Exception as e:
                        self._reply(400, {"error": str(e)})
                        return
                    self._reply(400, {"error": "Invalid URL or exchange failed"})
                    return
                self._reply(404, {"error": "not found"})

            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                
                # OAuth redirect
                if parsed.path == "/" and "code" in query and "state" in query:
                    code = query["code"][0]
                    state = query["state"][0]
                    if state in OAUTH_STATES:
                        verifier = OAUTH_STATES[state]["code_verifier"]
                        import json
                        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                            "client_id": GOOGLE_OAUTH_CLIENT_ID,
                            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                            "code": code,
                            "code_verifier": verifier,
                            "redirect_uri": HOST_ORIGIN + "/",
                            "grant_type": "authorization_code"
                        }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                        try:
                            with urllib.request.urlopen(req, timeout=10) as res:
                                data = json.load(res)
                                
                            # identify user
                            req2 = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {data['access_token']}"})
                            with urllib.request.urlopen(req2, timeout=10) as res2:
                                user_data = json.load(res2)
                                email_addr = user_data.get("email", "")
                            
                            app = OAUTH_STATES[state].get("app", "gmail")
                            if email_addr and "access_token" in data:
                                self._html(cls.grant_page(app, cls.accept_grant(app, data, email_addr)))
                                return
                        except Exception as e:
                            self._html(f"<h2>Error</h2><p>{html.escape(str(e))}</p>")
                            return
                    self._html("<h2>Error</h2><p>Invalid state or token exchange failed.</p>")
                    return

                # Read by the browser, cross-origin from the Onyx page, to decide whether to offer
                # the Connect button. It exposes no mail and needs no secret.
                if parsed.path in ("/health", "/status"):
                    self._reply(200, cls.status())
                    return
                if parsed.path == LEGACY_START_PATH:
                    self._redirect(CONNECT_PATH)
                    return
                if parsed.path == CONNECT_PATH:
                    app = (urllib.parse.parse_qs(parsed.query).get("app") or ["gmail"])[0]
                    self._setup_page(app if app in APP_SCOPES else "gmail")
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
                    try:
                        limit = int((query.get("limit") or [DEFAULT_RESULT_LIMIT])[0])
                    except ValueError:
                        limit = DEFAULT_RESULT_LIMIT
                    self._reply(200, cls.search(terms, limit))
                    return
                if parsed.path.startswith(("/drive/", "/calendar/")):
                    self._reply(200, cls.workspace(parsed.path, query))
                    return
                if parsed.path.startswith("/message/"):
                    # Decoded: a client may percent-encode the `@` and `|` in "<account>|<id>".
                    message_id = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
                    self._reply(200, cls.message(message_id))
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
