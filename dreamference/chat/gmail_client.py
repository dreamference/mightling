"""
Gmail Client for the Mightling terminal agent.

This module provides the GmailClient class, the HTTP client behind `ling-admin gmail`. It talks to
the Gmail search service the web UI already runs (`dreamference-gmail`, published on loopback), so
the terminal agent reads the same mailboxes with the same credentials and opens no IMAP connection
of its own. The service is the one component that unseals credentials and fans out across
accounts; a second IMAP path on the host would give two chances to disagree about the same sealed
file, and would put the mailbox token inside the agent's process.

Every method returns the service's JSON, or a dict with `error` and `hint` in the shape
`WebTools` uses, so the CLI formats both the same way.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Final, Optional

from dreamference.chat.gmail_credentials import CREDENTIALS_DIR
from dreamference.chat.gmail_search_service import IMAP_TIMEOUT_SECONDS, MAX_RESULT_LIMIT
from dreamference.chat.onyx_runner import GMAIL_AUTH_HEADER, GMAIL_HOST_PORT

GMAIL_SERVICE_URL: Final[str] = f"http://127.0.0.1:{GMAIL_HOST_PORT}"

# The file OnyxRunner._gmail_secret() creates and hands the container as MIGHTLING_GMAIL_SECRET. Only
# read here: a client that created one would hold a secret the running service does not know.
SERVICE_SECRET_PATH: Final[str] = os.path.join(CREDENTIALS_DIR, "service-secret")

NOT_RUNNING_ERROR: Final[Dict[str, str]] = {
    "error": "Gmail service is not running.",
    "hint": "Start it with: ling-admin chat start",
}
NOT_SET_UP_ERROR: Final[Dict[str, str]] = {
    "error": "Gmail has not been set up.",
    "hint": "Run: ling-admin chat start, then connect in Settings → Gmail Accounts",
}


class GmailClient:
    """
    Reads mail through the running Gmail search service.
    """

    @classmethod
    def status(cls) -> Dict[str, Any]:
        """
        Asks which accounts are connected. Needs no secret: the endpoint exposes no mail.

        Returns:
            Dict[str, Any]: `connected` and `email` (comma-separated addresses), or an error.
        """
        return cls._get("/status", authenticated=False)

    @classmethod
    def search(cls, query: str, limit: int = 10) -> Dict[str, Any]:
        """
        Searches every connected mailbox with Gmail's own query syntax.

        Args:
            query (str): A Gmail query, e.g. `from:alice invoice newer_than:7d`.
            limit (int): How many messages to return; clamped to the service's maximum.

        Returns:
            Dict[str, Any]: `messages`, newest first, and `errors` for accounts that failed.
        """
        limit = max(1, min(limit, MAX_RESULT_LIMIT))
        return cls._get("/search?" + urllib.parse.urlencode({"query": query, "limit": limit}))

    @classmethod
    def read(cls, message_id: str) -> Dict[str, Any]:
        """
        Reads one message, by the opaque id `search` printed.

        Args:
            message_id (str): The service's `"<account>|<X-GM-MSGID>"` id, as printed.

        Returns:
            Dict[str, Any]: Headers and `body`, or an error.
        """
        # `@` and `|` stay literal: the id is "<account>|<id>", and a service started before it
        # learned to decode its path would look up the encoded form and find nothing.
        return cls._get("/message/" + urllib.parse.quote(message_id, safe="@|"))

    @classmethod
    def _secret(cls) -> Optional[str]:
        try:
            with open(SERVICE_SECRET_PATH) as handle:
                return handle.read().strip() or None
        except OSError:
            return None

    @classmethod
    def _get(cls, path: str, authenticated: bool = True) -> Dict[str, Any]:
        headers = {}
        if authenticated:
            secret = cls._secret()
            if secret is None:
                return dict(NOT_SET_UP_ERROR)
            headers[GMAIL_AUTH_HEADER] = secret
        request = urllib.request.Request(GMAIL_SERVICE_URL + path, headers=headers)
        try:
            # IMAP is the slow part, and the service allows it this long per connection.
            with urllib.request.urlopen(request, timeout=IMAP_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                body = json.load(error)
            except ValueError:
                body = {}
            return {"error": body.get("error") or f"Gmail service answered HTTP {error.code}."}
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            return dict(NOT_RUNNING_ERROR)
        except ValueError:
            return {"error": "Gmail service returned something that is not JSON."}
