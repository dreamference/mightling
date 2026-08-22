"""
Gmail Search Service for Onyx.

This module provides the GmailSearchService class, an HTTP service that turns a Gmail mailbox into
the two operations an assistant needs, and the OpenAPI description Onyx registers it under.

It mirrors how web search already works rather than inventing a new shape. Web search gives the
model `web_search` to find things and `open_url` to read one; this gives it `gmail_search` to find
messages and `gmail_message` to read one. The split matters for the same reason it does there: a
mailbox search that returned full bodies would fill the context with ten threads to answer a
question about one.

**The transport is IMAP, not the Gmail REST API.** The API route needed an OAuth client, which
means a Google Cloud project, a consent screen and a publishing decision before a single message
could be read -- three minutes of console work at best, and a branding-review dead end at worst.
IMAP needs a 16-character app password and nothing else. Two consequences are worth knowing:

* **Gmail's search syntax is preserved exactly**, because `X-GM-RAW` hands the query string to the
  same engine the web UI uses. `from:alice invoice`, `newer_than:7d`, `has:attachment` all still
  work, so the OpenAPI summaries the model reads did not have to change with the transport.
* **The credential is no longer read-only.** `gmail.readonly` was a real boundary; an app password
  is the whole account and IMAP has no scopes. What replaces it is narrower but weaker: the service
  issues `LOGIN`, `LIST`, `SEARCH` and `FETCH` and nothing else, and every fetch uses `BODY.PEEK`
  so that reading a message does not mark it read in the user's mailbox.

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
import urllib.parse
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional, Tuple

# Inside the container the credentials directory is mounted here.
CONFIG_DIR: Final[str] = os.environ.get("PUFFIN_GMAIL_CONFIG", "/config")
CREDENTIALS_NAME: Final[str] = "credentials.json"

# The key the stored app password is sealed with. See `_seal`: this is obfuscation with a clear
# threat model, not a secret-management system.
KEY_NAME: Final[str] = "credentials.key"

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
# Connect button, and the setup form is served from here.
HOST_PORT: Final[int] = 8767
HOST_ORIGIN: Final[str] = f"http://localhost:{HOST_PORT}"

# The page origin allowed to read the status endpoint. The browser calls it cross-origin, from the
# Onyx UI to this service.
ONYX_ORIGIN: Final[str] = "http://localhost:3000"

# The setup form: served on GET, submitted to on POST.
CONNECT_PATH: Final[str] = "/connect"

# What the button pointed at while the transport was OAuth. Kept as a redirect because Onyx serves
# its bundles `immutable`, so a browser that has not hard-refreshed still holds a script aiming
# here -- three lines of 302 is the difference between "works anyway" and "the button is broken".
LEGACY_START_PATH: Final[str] = "/oauth/start"

# The two Google pages the setup form links to. There is no third: an app password is the whole
# credential, and unlike an OAuth client it needs no project, no consent screen and no publishing.
GOOGLE_2SV_URL: Final[str] = "https://myaccount.google.com/signinoptions/two-step-verification"
GOOGLE_APP_PASSWORDS_URL: Final[str] = "https://myaccount.google.com/apppasswords"

# What an unconnected search answers with. It names the place the user can act rather than a
# command they would have to leave the app to run -- the model reads this and relays it.
NOT_CONNECTED_MESSAGE: Final[str] = (
    "Gmail is not connected. Open Settings -> Connectors in Puffin and choose Connect to Google."
)

# Enough styling that the setup pages read as part of Puffin rather than as a server error. They
# are the only pages this project serves directly, and the user arrives at them from a polished UI.
PAGE_STYLE: Final[str] = (
    "body{margin:0;padding:48px 24px;background:#fff;color:#111;"
    "font-family:Roboto,system-ui,sans-serif;font-size:14px;line-height:1.55}"
    "main{max-width:520px;margin:0 auto}"
    "h2{margin:0 0 4px;font-size:20px;font-weight:600}"
    "p{margin:0 0 14px}"
    "code{background:#f2f4f4;border-radius:4px;padding:1px 5px;font-family:'Roboto Mono',monospace;"
    "font-size:12.5px}"
    "label{display:block;margin-bottom:4px;font-weight:500}"
    "input{width:100%;box-sizing:border-box;margin-bottom:14px;padding:9px 10px;"
    "border:1px solid #d8dcdc;border-radius:8px;font-size:13.5px;font-family:inherit}"
    "button{padding:9px 16px;border:0;border-radius:8px;background:#0ABAB5;color:#fff;"
    "font-size:13.5px;font-weight:500;font-family:inherit;cursor:pointer}"
    "button:hover{background:#0aa8a3}"
    "a{color:#0ABAB5}"
    ".muted{color:#6b7280;font-size:12.5px}"
    "ol{counter-reset:step;list-style:none;padding-left:0;margin:0 0 18px}"
    "ol li{margin-bottom:16px;padding-left:30px;position:relative}"
    "ol li::before{counter-increment:step;content:counter(step);position:absolute;left:0;top:1px;"
    "width:20px;height:20px;border-radius:50%;background:#e6f7f7;color:#0a8f8b;"
    "font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center}"
    ".note{margin-top:22px;padding:12px 14px;border-radius:8px;background:#f6f8f8;"
    "color:#4b5563;font-size:12.5px}"
    ".error{color:#b91c1c}"
)


class GmailSearchService:
    """
    Serves Gmail search and message retrieval over HTTP for Onyx's custom tool.
    """

    # ------------------------------------------------------------------ credentials

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
        Encrypts the app password for storage, encrypt-then-MAC.

        **This is obfuscation with a clear threat model, and it is worth being precise about what
        it buys.** The key sits in the same directory as the ciphertext, because the service is
        headless: anything it can read at startup, an attacker holding that directory can read too.
        What sealing does prevent is the password being legible in a file that travels -- a backup,
        a `cat` over someone's shoulder, a grep through a copied config tree. It is not a defence
        against local root, and nothing here pretends otherwise.

        Args:
            plaintext (str): The app password.
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
        Recovers a sealed app password, or None if it does not authenticate.

        Args:
            blob (str): The value `_seal` produced.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Optional[str]: The app password, or None.
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
    def credentials(cls, directory: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Reads the stored mailbox credentials.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Optional[Dict[str, str]]: `email` and `app_password`, or None if not connected.
        """
        try:
            with open(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return None
        address = stored.get("email")
        sealed = stored.get("app_password")
        if not address or not sealed:
            return None
        password = cls._unseal(sealed, directory)
        if not password:
            return None
        return {"email": address, "app_password": password}

    @classmethod
    def save_credentials(
        cls, address: str, app_password: str, directory: Optional[str] = None
    ) -> bool:
        """
        Stores the mailbox credentials, sealed and readable only by this user.

        Args:
            address (str): The Gmail address.
            app_password (str): The 16-character app password.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bool: True if the file was written.
        """
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        try:
            with open(path, "w") as handle:
                json.dump(
                    {"email": address, "app_password": cls._seal(app_password, directory)},
                    handle,
                    indent=2,
                )
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True

    @classmethod
    def forget(cls, directory: Optional[str] = None) -> bool:
        """
        Removes the stored credentials, which is what disconnecting means here.

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
    def status(cls) -> Dict[str, Any]:
        """
        Reports whether the mailbox is connected.

        `configured` and `connected` report the same thing now that there is only one credential to
        hold -- the pair is kept because the injected UI script and Onyx's page both read it, and
        because "a client is stored" and "someone has consented" were genuinely different questions
        under the OAuth transport.

        Returns:
            Dict[str, Any]: `configured` and `connected`.
        """
        connected = cls.credentials() is not None
        return {"configured": connected, "connected": connected}

    # ------------------------------------------------------------------ IMAP

    @classmethod
    def verify(cls, address: str, app_password: str) -> Optional[str]:
        """
        Checks the credentials by logging in, so the form fails at the form.

        Args:
            address (str): The Gmail address.
            app_password (str): The app password.

        Returns:
            Optional[str]: None if the login worked, otherwise a message for the user.
        """
        try:
            connection = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
        except OSError as exc:
            return f"Could not reach {IMAP_HOST}: {exc}"
        try:
            connection.login(address, app_password)
        except imaplib.IMAP4.error:
            # Google's own text is unhelpful ("Invalid credentials (Failure)"), and the two things
            # that actually go wrong are a mistyped code and IMAP being switched off entirely.
            return ("Google refused that sign-in. Check the address, and that the app password was "
                    "copied whole — it is 16 characters with no spaces.")
        except OSError as exc:
            return f"Could not reach {IMAP_HOST}: {exc}"
        finally:
            try:
                connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        return None

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
            connection.login(stored["email"], stored["app_password"])
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
        every account whose interface language is not English -- `[Gmail]/Вся почта` on this one.
        The `\\All` attribute in the LIST response is the same folder under any language.

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
        tool's documented syntax survived the move off the REST API unchanged.

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
    def _summaries(
        cls, connection: imaplib.IMAP4_SSL, uids: List[bytes]
    ) -> List[Dict[str, str]]:
        """
        Fetches just the headers a result list needs.

        `BODY.PEEK` rather than `BODY` throughout: the REST API could not mark anything read, but
        IMAP can, and a search tool that silently marked twenty messages as read would be doing
        real damage to a mailbox.

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

            def _link_target(self) -> str:
                """
                Chooses how the Google links open, from the caller's engine.

                Tauri leaves wry's `new_window_req_handler` unset, so WebKitGTK never connects its
                `create` signal and a `target=_blank` click in the desktop app is silently inert --
                no window, no error. The injected script solves this by reading an engine marker,
                but this page is *served* rather than injected and has no marker to read. The
                User-Agent answers the same question: Blink's contains `Chrome/` and WebKitGTK's
                does not.
                """
                agent = self.headers.get("User-Agent", "")
                return "_blank" if "Chrome/" in agent else "_self"

            def _html(self, inner: str) -> None:
                # The link back matters more than it looks. In the browser the button opens a new
                # tab and this page is disposable, but the desktop app has no new window to open,
                # so it navigates in place -- and without a way back the user is left staring at a
                # bare paragraph with no chrome to return from.
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

            def _setup_form(self, error: str = "", address: str = "") -> None:
                """
                Asks for the mailbox and an app password, which is the whole setup.

                Two steps rather than the four the OAuth flow needed: no Cloud project, no consent
                screen, no publishing decision. The app password is a Google credential the user
                creates in their own account settings, and Google shows it in four groups of four
                -- which is why the field's value is stripped of whitespace before it is used.
                """
                target = self._link_target()
                note = f'<p class="error">{html.escape(error)}</p>' if error else ""
                self._html(
                    "<h2>Connect Gmail</h2>"
                    "<p>Puffin reads your mail directly from Google over IMAP. Nothing passes "
                    "through Dreamference.</p>"
                    "<ol>"
                    "<li>Turn on 2-Step Verification if you haven’t: "
                    f'<a href="{GOOGLE_2SV_URL}" target="{target}" rel="noopener">'
                    "myaccount.google.com/signinoptions/two-step-verification</a></li>"
                    "<li>Create an app password: "
                    f'<a href="{GOOGLE_APP_PASSWORDS_URL}" target="{target}" rel="noopener">'
                    "myaccount.google.com/apppasswords</a> → name it Puffin → Create "
                    "→ copy the 16-character code.</li>"
                    "</ol>"
                    f"{note}"
                    f'<form method="post" action="{CONNECT_PATH}">'
                    '<label for="addr">Gmail address</label>'
                    '<input id="addr" name="email" type="email" autocomplete="username" required '
                    f'value="{html.escape(address)}">'
                    '<label for="pw">App password</label>'
                    '<input id="pw" name="app_password" type="password" autocomplete="off" '
                    'required>'
                    '<button type="submit">Connect</button>'
                    "</form>"
                    '<p class="note">ⓘ Work/school account and the app-passwords page says '
                    "it’s unavailable? Your admin has disabled them — ask them to allow "
                    "app passwords for your account.</p>"
                )

            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                parsed = urllib.parse.urlparse(self.path)

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
                    self._setup_form()
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

            def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
                """
                Receives the setup form, checks the credentials and stores them.

                The login is attempted *before* anything is written, so a mistyped code fails on
                the form where it can be corrected rather than silently, later, inside a tool call
                the user never sees.
                """
                if urllib.parse.urlparse(self.path).path != CONNECT_PATH:
                    self._reply(404, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                address = (form.get("email") or [""])[0].strip()
                # Google displays the code as `xxxx xxxx xxxx xxxx`, and it is pasted that way far
                # more often than not. IMAP would simply reject it.
                app_password = re.sub(r"\s+", "", (form.get("app_password") or [""])[0])
                if not address or not app_password:
                    self._setup_form("Both the address and the app password are needed.", address)
                    return
                failure = cls.verify(address, app_password)
                if failure:
                    self._setup_form(failure, address)
                    return
                if not cls.save_credentials(address, app_password):
                    self._setup_form("Those credentials could not be stored — check the service "
                                     "logs.", address)
                    return
                self._page("Gmail is connected. You can close this tab.")

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
