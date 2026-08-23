"""
Gmail Search Service for Onyx.

This module provides the GmailSearchService class, an HTTP service that turns a Gmail mailbox into
the two operations an assistant needs, and the OpenAPI description Onyx registers it under.

It mirrors how web search already works rather than inventing a new shape. Web search gives the
model `web_search` to find things and `open_url` to read one; this gives it `gmail_search` to find
messages and `gmail_message` to read one. The split matters for the same reason it does there: a
mailbox search that returned full bodies would fill the context with ten threads to answer a
question about one.

**The transport is IMAP and the credential comes from GNOME Online Accounts.** Two other routes
were built and removed, and the reasons are worth keeping: a Google **app password** is two minutes
of work but is unavailable to anyone in Google's Advanced Protection Program or under a Workspace
admin who has switched them off, and a **Google client of one's own** works for everybody but costs
a Cloud project, a consent screen, a publishing decision and an unverified-app warning. Both put
setup work on the user. GOA puts none: the user signs into Google once in GNOME Settings, and every
desktop application on the machine — Evolution for mail, Nautilus for files, and this — asks GOA
for a short-lived access token when it needs one.

Three consequences shape what is left:

* **This service holds no long-lived credential.** GNOME keeps the refresh token. What is stored
  here is about an hour's worth of access, replaced by a systemd user timer on the host.
* **There is nothing to submit.** With no password to type and no client to paste, the service
  serves no forms and accepts no POST at all; `/connect` is a page explaining where to sign in.
* **Gmail's search syntax is preserved exactly**, because `X-GM-RAW` hands the query to the same
  engine the web UI uses — `from:alice invoice`, `newer_than:7d`, `has:attachment`.

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
import urllib.parse, urllib.request, secrets, base64, hashlib
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional, Tuple

# Inside the container the credentials directory is mounted here.
CONFIG_DIR: Final[str] = os.environ.get("PUFFIN_GMAIL_CONFIG", "/config")
CREDENTIALS_NAME: Final[str] = "credentials.json"

# The key the stored token is sealed with. See `_seal`: this is obfuscation with a clear threat
# model, not a secret-management system.
KEY_NAME: Final[str] = "credentials.key"

GOOGLE_OAUTH_CLIENT_ID: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_ID", "44438659992-7kgjeitenc16ssihbtdjbgguch7ju55s.apps.googleusercontent.com")
GOOGLE_OAUTH_CLIENT_SECRET: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_SECRET", "-gMLuQyDiI0XrQS_vx_mhuYF")
GOOGLE_OAUTH_SCOPES: Final[str] = "openid email https://mail.google.com/ https://www.googleapis.com/auth/drive.readonly"
OAUTH_STATES: Dict[str, Dict[str, Any]] = {}


# Written by the host so the setup page can say what GNOME is holding. The service cannot look for
# itself: GOA lives on the session bus, and this runs in a container that has neither a session bus
# nor `gdbus` to ask with. One line the host knows and the container does not.
GNOME_HINT_NAME: Final[str] = "gnome-accounts.json"

# Onyx sends this with every tool call. The service is on a private Docker network, but so is
# everything else Onyx runs, and a private network is not an authorisation boundary -- the shared
# secret is what distinguishes Onyx from everything else.
AUTH_HEADER: Final[str] = "X-Puffin-Gmail-Token"

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
    "Gmail is not connected. Open Settings -> Connectors in Puffin and choose Connect to Google."
)

# Enough styling that the setup page reads as part of Puffin rather than as a server error. It is
# the only page this project serves directly, and the user arrives at it from a polished UI.
PAGE_STYLE: Final[str] = (
    "body{margin:0;padding:48px 24px;background:#fff;color:#111;"
    "font-family:Roboto,system-ui,sans-serif;font-size:14px;line-height:1.55}"
    "main{max-width:520px;margin:0 auto}"
    "h2{margin:0 0 4px;font-size:20px;font-weight:600}"
    "p{margin:0 0 14px}"
    "code{background:#f2f4f4;border-radius:4px;padding:1px 5px;font-family:'Roboto Mono',monospace;"
    "font-size:12.5px}"
    "a{color:#0ABAB5}"
    ".muted{color:#6b7280;font-size:12.5px}"
    "ol{counter-reset:step;list-style:none;padding-left:0;margin:0 0 18px}"
    "ol li{margin-bottom:16px;padding-left:30px;position:relative}"
    "ol li::before{counter-increment:step;content:counter(step);position:absolute;left:0;top:1px;"
    "width:20px;height:20px;border-radius:50%;background:#e6f7f7;color:#0a8f8b;"
    "font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center}"
    ".note{margin-top:22px;padding:12px 14px;border-radius:8px;background:#f6f8f8;"
    "color:#4b5563;font-size:12.5px}"
    ".ready{margin:0 0 18px;padding:12px 14px;border-radius:8px;background:#e6f7f7;color:#0a6f6c}"
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
        """
        Reads the credentials file as stored, without deciding whether it is usable.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Dict[str, Any]: The stored fields, empty if there are none.
        """
        try:
            with open(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {}
        return stored if isinstance(stored, dict) else {}

    @classmethod
    def credentials(cls, directory: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Reads the token the mailbox can actually be opened with.

        An expired token is not a failure to report, it is a stale file -- the timer that should
        have replaced it did not run. Reporting "not connected" is the honest state and brings the
        Connect button back.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Optional[Dict[str, str]]: `email` and `access_token`, or None.
        """
        stored = cls._raw(directory)
        address = stored.get("email")
        sealed = stored.get("access_token")
        if not address or not sealed:
            return None
        if float(stored.get("expires_at", 0)) - time.time() < 60:
            sealed_refresh = stored.get("refresh_token")
            if sealed_refresh:
                refresh = cls._unseal(sealed_refresh, directory)
                if refresh:
                    import json
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
                                cls.save_token(address, data["access_token"], data.get("expires_in", 3599), directory)
                                return {"email": address, "access_token": data["access_token"]}
                    except Exception as e:
                        pass
            return None
        token = cls._unseal(sealed, directory)
        return {"email": address, "access_token": token} if token else None

    @classmethod
    def save_token(
        cls, address: str, token: str, lifetime: int, directory: Optional[str] = None, refresh_token: Optional[str] = None
    ) -> bool:
        """
        Stores a GNOME Online Accounts access token for the container to use.

        Written by the host, because GOA lives on the session bus and the service does not. The
        expiry is recorded rather than the lifetime so that a stale file is recognisable as stale
        without knowing when it was written.

        Args:
            address (str): The Google address the token authenticates as.
            token (str): The access token.
            lifetime (int): Seconds until it expires, as GOA reported it.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bool: True if the file was written.
        """
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        try:
            with open(path, "w") as handle:
                json.dump({
                    "email": address,
                    "access_token": cls._seal(token, directory),
                    # A minute of slack, so a token about to expire is not handed to a connection
                    # that will take a moment to open.
                    "expires_at": time.time() + max(0, lifetime - 60),
                }, handle, indent=2)
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
    def status(cls) -> Dict[str, Any]:
        """
        Reports whether the mailbox is connected.

        Both flags mean the same thing -- a usable token exists -- and the pair is kept because the
        injected UI script reads it as one object.

        Returns:
            Dict[str, Any]: `configured` and `connected`.
        """
        connected = cls.credentials() is not None
        return {"configured": connected, "connected": connected}

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
    def _open_mailbox(cls) -> Tuple[Optional[imaplib.IMAP4_SSL], Optional[str]]:
        """
        Connects, authenticates and selects All Mail read-only.

        Returns:
            Tuple[Optional[imaplib.IMAP4_SSL], Optional[str]]: The connection, or an error message.
        """
        stored = cls.credentials()
        if not stored:
            return None, NOT_CONNECTED_MESSAGE
        try:
            connection = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
            connection.authenticate("XOAUTH2", cls._xoauth2(stored["email"], stored["access_token"]))
            # Read-only, so that nothing this service does can change the mailbox even by accident.
            connection.select(f'"{cls._all_mail_folder(connection)}"', readonly=True)
        except imaplib.IMAP4.error as exc:
            return None, f"Gmail refused the connection: {exc}"
        except OSError as exc:
            return None, f"Could not reach {IMAP_HOST}: {exc}"
        return connection, None

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

        Args:
            query (str): Gmail search syntax, e.g. `from:alice newer_than:7d`.
            limit (int): Maximum number of messages to return.

        Returns:
            Dict[str, Any]: `{"messages": [...]}`, each entry carrying the id, sender, subject and
                date -- enough to choose one to open, and no more.
        """
        connection, error = cls._open_mailbox()
        if not connection:
            return {"error": error}
        try:
            uids = cls._search_uids(connection, query)
            if not uids:
                return {"messages": []}
            # Newest first: IMAP returns UIDs ascending, and recency is what a mailbox question
            # almost always means.
            wanted = list(reversed(uids))[:max(1, min(limit, MAX_RESULT_LIMIT))]
            return {"messages": cls._summaries(connection, wanted)}
        finally:
            cls._close(connection)

    @classmethod
    def _summaries(cls, connection: imaplib.IMAP4_SSL, uids: List[bytes]) -> List[Dict[str, str]]:
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
    def message(cls, message_id: str) -> Dict[str, Any]:
        """
        Reads one message in full.

        Args:
            message_id (str): The `X-GM-MSGID` from a search result.

        Returns:
            Dict[str, Any]: The headers and the plain-text body.
        """
        if not message_id.isdigit():
            return {"error": "That is not a Gmail message id."}
        connection, error = cls._open_mailbox()
        if not connection:
            return {"error": error}
        try:
            try:
                status, data = connection.uid("SEARCH", None, "X-GM-MSGID", message_id)
            except (imaplib.IMAP4.error, OSError):
                return {"error": "Message not found."}
            if status != "OK" or not data or not data[0]:
                return {"error": "Message not found."}
            uid = data[0].split()[0].decode("ascii")
            try:
                status, fetched = connection.uid("FETCH", uid, "(BODY.PEEK[])")
            except (imaplib.IMAP4.error, OSError):
                return {"error": "Message could not be read."}
            raw = b""
            for item in fetched or []:
                if isinstance(item, tuple):
                    raw = item[1]
                    break
            if not raw:
                return {"error": "Message could not be read."}
            parsed = email.message_from_bytes(raw)
            return {
                "id": message_id,
                "from": cls._decode_header(parsed.get("From", "")),
                "to": cls._decode_header(parsed.get("To", "")),
                "subject": cls._decode_header(parsed.get("Subject", "")),
                "date": cls._decode_header(parsed.get("Date", "")),
                "body": cls._extract_text(parsed)[:MAX_BODY_CHARACTERS],
            }
        finally:
            cls._close(connection)

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

            def _html(self, inner: str) -> None:
                # The link back matters more than it looks. In the browser this page opens in a new
                # tab and is disposable, but the desktop app has no new window to open, so it
                # navigates in place -- and without a way back the user is left staring at a bare
                # paragraph with no chrome to return from.
                body = (
                    '<!doctype html><html><head><meta charset="utf-8">'
                    '<title>Puffin</title><meta name="viewport" '
                    'content="width=device-width,initial-scale=1">'
                    f"<style>{PAGE_STYLE}</style></head><body><main>"
                    f"{inner}"
                    f'<p><a href="{ONYX_ORIGIN}/app">Back to Puffin</a></p>'
                    "</main></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _page(self, message: str) -> None:
                self._html(f"<h2>Puffin</h2><p>{message}</p>")

            def _setup_page(self) -> None:
                """
                Explains where to sign in. There is nothing here to submit.

                The whole design is that the user has one place to manage their Google account --
                the desktop's own Settings panel -- and Puffin picks up what is there. So this page
                cannot be a form, and deliberately is not one: it says where to go, and reports
                what GNOME is currently holding so the user can tell whether the step is done.

                It also cannot be a *button*. GOA lives on the session bus and this page is served
                from a container that has neither a bus nor `gdbus`, so the host is what actually
                reads the token, on a timer. What the page can do is tell the user the truth about
                where things stand.
                """
                known = cls.gnome_accounts()
                if known:
                    listed = html.escape(", ".join(known))
                    self._html(
                        "<h2>Connect Gmail</h2>"
                        f'<p class="ready">✅ GNOME is signed into Google as <b '
                        f'style="display:inline">{listed}</b>. Puffin picks the account up '
                        "automatically — this page will stop appearing within a few minutes.</p>"
                        "<p>Puffin reads your mail directly from Google over IMAP, using the "
                        "account your desktop already holds. Nothing passes through Dreamference "
                        "and there is no password to create.</p>"
                        '<p class="note">ⓘ In a hurry? Run <code>dream onyx gmail</code> in a '
                        "terminal to connect now rather than waiting for the next check.</p>"
                    )
                    return
                self._html(
                    "<h2>Connect Gmail</h2>"
                    "<p>Puffin reads your mail directly from Google over IMAP, using the Google "
                    "account your desktop already holds. Nothing passes through Dreamference, and "
                    "there is no password or developer account to create.</p>"
                    "<ol>"
                    "<li>Open <b style=\"display:inline\">Settings → Online Accounts</b> on this "
                    "machine and sign into Google.</li>"
                    "<li>That is all. Puffin checks every few minutes and connects itself.</li>"
                    "</ol>"
                    '<p class="note">ⓘ The Google sign-in page will say <b '
                    'style="display:inline">GNOME</b> is asking for access. That is correct — '
                    "your desktop is what holds the account, and Puffin asks it for permission to "
                    "read your mail. No Puffin credentials are sent to Google.</p>"
                    '<p class="muted">Running Puffin on a machine with no desktop session? '
                    "GNOME Online Accounts is not available there, so Gmail search cannot be "
                    "connected on that host.</p>"
                )

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/google/oauth/start":
                    
                    state = secrets.token_urlsafe(32)
                    verifier = secrets.token_urlsafe(32)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                    OAUTH_STATES[state] = {"code_verifier": verifier, "time": time.time()}
                    
                    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_OAUTH_CLIENT_ID}&redirect_uri={urllib.parse.quote(HOST_ORIGIN + '/')}&response_type=code&scope={urllib.parse.quote(GOOGLE_OAUTH_SCOPES)}&access_type=offline&prompt=consent&code_challenge={challenge}&code_challenge_method=S256&state={state}"
                    self._reply(200, {"auth_url": auth_url})
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
                                
                                if email_addr and "access_token" in tdata:
                                    GmailSearchService.save_token(email_addr, tdata["access_token"], tdata.get("expires_in", 3599), refresh_token=tdata.get("refresh_token"))
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
                            
                            if email_addr and "access_token" in data:
                                GmailSearchService.save_token(email_addr, data["access_token"], data.get("expires_in", 3599), refresh_token=data.get("refresh_token"))
                                self._html("<h2>Connected Successfully</h2><p>You can close this tab.</p>")
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
                    if cls.status()["connected"]:
                        self._page("Gmail is already connected.")
                        return
                    self._setup_page()
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
