"""NetBird management's REST API, as the node's own administrator calls it (REMOTE_ACCESS §14.2).

Every call goes to `https://localhost:33073`, the control plane's loopback proxy, and verifies the
server against the node's CA alone; nothing here ever leaves the machine. With no identity provider
and no dashboard, the administrator's token comes from `POST /api/setup`, which answers only while
no account exists, so `remote setup` calls it before the tunnel to the box is up. `_request` is the
seam the tests replace.
"""

import json
import ssl
import time
import urllib.error
import urllib.request

from dreamference.remote.remote_ca import OVERLAY_DOMAIN, RemoteCA
from dreamference.remote.remote_control_plane import LOCAL_URL

REQUEST_TIMEOUT_S = 30
WAIT_STEP_S = 1.0
HTTP_OK = 200
HTTP_NO_CONTENT = 204
TOKEN_DAYS = 365
# A setup key lives a day at the least (§14.2, item 5); the enrolment code withdraws it sooner.
SETUP_KEY_SECONDS = 86400

Json = dict[str, object]


class RemoteManagementApi:
    """Calls to the node's management service."""

    @classmethod
    def _request(
        cls, method: str, path: str, body: Json | None = None, token: str = ""
    ) -> tuple[int, object]:
        """One JSON request. A connection that fails is status 0.

        Args:
            method: The HTTP method.
            path: The path under `/api`, with its leading slash.
            body: The JSON body, if any.
            token: The administrator's token, if the call needs one.

        Returns:
            tuple[int, object]: The status and the decoded answer (an empty dict when there is none).
        """
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{LOCAL_URL}/api{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if token:
            request.add_header("Authorization", f"Token {token}")
        context = ssl.create_default_context(cafile=str(RemoteCA.cert_path()))
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_S, context=context
            ) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except ValueError:
                return error.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, {}

    @classmethod
    def answering(cls) -> bool:
        """Whether management answers at all (any HTTP status).

        Returns:
            bool: True once the server is up behind the proxy.
        """
        status, _ = cls._request("GET", "/users")
        return status != 0

    @classmethod
    def wait_until_answering(cls, seconds: float) -> bool:
        """Waits for management to answer.

        Args:
            seconds: How long to wait at most.

        Returns:
            bool: Whether it answered in time.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cls.answering():
                return True
            time.sleep(WAIT_STEP_S)
        return False

    @classmethod
    def create_owner(cls, email: str, name: str, password: str) -> Json | None:
        """Creates the owner account and its token, once (`POST /api/setup`).

        Args:
            email: The owner's address (never mailed; the embedded IdP needs one).
            name: Its display name.
            password: Its password.

        Returns:
            Json | None: `user_id` and `personal_access_token`, or None when the server refused
            (an account exists already) or gave no token.
        """
        body: Json = {"email": email, "name": name, "password": password, "create_pat": True,
                      "pat_expire_in": TOKEN_DAYS}  # fmt: skip
        status, answer = cls._request("POST", "/setup", body)
        if status != HTTP_OK or not isinstance(answer, dict):
            return None
        if not isinstance(answer.get("personal_access_token"), str) or not isinstance(
            answer.get("user_id"), str
        ):
            return None
        return answer

    @classmethod
    def rotate_token(cls, user_id: str, token: str, name: str) -> tuple[str, str] | None:
        """Makes a new token for the owner and deletes the others.

        Args:
            user_id: The owner's id.
            token: The current token.
            name: The new token's name.

        Returns:
            tuple[str, str] | None: The new token and its id, or None when it could not be made.
        """
        status, answer = cls._request(
            "POST", f"/users/{user_id}/tokens", {"name": name, "expires_in": TOKEN_DAYS}, token
        )
        if (
            status != HTTP_OK
            or not isinstance(answer, dict)
            or not isinstance(answer.get("plain_token"), str)
        ):
            return None
        record = answer.get("personal_access_token")
        new_id = record.get("id") if isinstance(record, dict) else None
        if not isinstance(new_id, str):
            return None
        new_token = str(answer["plain_token"])
        _, listed = cls._request("GET", f"/users/{user_id}/tokens", token=new_token)
        for old in listed if isinstance(listed, list) else []:
            if isinstance(old, dict) and old.get("id") not in (None, new_id):
                cls._request("DELETE", f"/users/{user_id}/tokens/{old['id']}", token=new_token)
        return new_token, new_id

    @classmethod
    def create_setup_key(cls, token: str, name: str) -> Json | None:
        """Mints a one-off, non-ephemeral setup key (an ephemeral one would rename the peer).

        Args:
            token: The administrator's token.
            name: What management lists it as.

        Returns:
            Json | None: Its `id` and `key`, or None.
        """
        body: Json = {"name": name, "type": "one-off", "expires_in": SETUP_KEY_SECONDS, "auto_groups": [],
                      "usage_limit": 1, "ephemeral": False}  # fmt: skip
        status, answer = cls._request("POST", "/setup-keys", body, token)
        if status != HTTP_OK or not isinstance(answer, dict):
            return None
        if not isinstance(answer.get("id"), str) or not isinstance(answer.get("key"), str):
            return None
        return answer

    @classmethod
    def delete_setup_key(cls, token: str, key_id: str) -> bool:
        """Withdraws a setup key.

        Args:
            token: The administrator's token.
            key_id: The key's id.

        Returns:
            bool: Whether management deleted it.
        """
        status, _ = cls._request("DELETE", f"/setup-keys/{key_id}", token=token)
        return status in (HTTP_OK, HTTP_NO_CONTENT)

    @classmethod
    def peers(cls, token: str) -> list[Json] | None:
        """Every peer of the overlay.

        Args:
            token: The administrator's token.

        Returns:
            list[Json] | None: The peers as management describes them, or None when it did not answer.
        """
        status, answer = cls._request("GET", "/peers", token=token)
        if status != HTTP_OK or not isinstance(answer, list):
            return None
        return [peer for peer in answer if isinstance(peer, dict)]

    @classmethod
    def delete_peer(cls, token: str, peer_id: str) -> bool:
        """Deletes a peer: its key is forgotten and its overlay address released at once.

        Args:
            token: The administrator's token.
            peer_id: The peer's id.

        Returns:
            bool: Whether management deleted it.
        """
        status, _ = cls._request("DELETE", f"/peers/{peer_id}", token=token)
        return status in (HTTP_OK, HTTP_NO_CONTENT)

    @classmethod
    def dns_domain(cls, token: str) -> str:
        """The overlay's DNS domain, as the account says.

        Args:
            token: The administrator's token.

        Returns:
            str: The account's `dns_domain`, or NetBird's default when it sets none.
        """
        status, answer = cls._request("GET", "/accounts", token=token)
        if (
            status == HTTP_OK
            and isinstance(answer, list)
            and answer
            and isinstance(answer[0], dict)
        ):
            settings = answer[0].get("settings")
            domain = settings.get("dns_domain") if isinstance(settings, dict) else None
            if isinstance(domain, str) and domain:
                return domain
        return OVERLAY_DOMAIN
