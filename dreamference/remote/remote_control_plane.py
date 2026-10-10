"""
The overlay's control plane on the node (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §3).

NetBird's combined server (management and signal on one port) runs as the container
`dreamference-remote`, published on loopback only. It serves TLS itself, with a certificate from the
node's CA for the box's DNS name, so the box in front of it passes bytes and terminates nothing: the
reverse tunnel carries the box's port 443 to this loopback port. Its own relay and STUN are switched
off by naming the box's (`relays`, `stuns`), and its identity provider is never used: peers enrol
with one-time setup keys, which the node mints through the management API with a token it made for
itself at the first start (`/api/setup` with `NB_SETUP_PAT_ENABLED`, then a 365-day token).

Verified on 2026-10-10 against 0.80.0 in a local rehearsal: TLS from a private CA served by the
container behind a plain TCP pass-through and behind haproxy routing by server name, setup-key
enrolment, peers' names under the overlay domain, a relayed connection, and revocation by deleting
the peer.

Docker goes through `_run`, HTTPS through `_request`; the tests replace those two.
"""

import http.client
import json
import secrets
import socket
import ssl
import subprocess
import time
from typing import Any, Dict, Final, List, Optional, Tuple

from dreamference.remote.remote_certificate_authority import RemoteCertificateAuthority
from dreamference.remote.remote_settings import (
    CONTROL_PLANE_PORT,
    OVERLAY_DOMAIN,
    SERVER_CONTAINER,
    SERVER_IMAGE,
    SETUP_KEY_MINUTES,
    STUN_PORT,
    RemoteSettings,
)

MEMORY_LIMIT: Final[str] = "512m"
TOKEN_DAYS: Final[int] = 365


class RemoteControlPlane:
    """The `dreamference-remote` container and the management API behind it."""

    class _Connection(http.client.HTTPSConnection):
        """HTTPS to loopback that checks the certificate against the box's DNS name."""

        def __init__(self, port: int, server_name: str, context: ssl.SSLContext, timeout: float):
            super().__init__("127.0.0.1", port, context=context, timeout=timeout)
            self._server_name = server_name
            self._tls = context

        def connect(self) -> None:
            raw = socket.create_connection((self.host, self.port), self.timeout)
            self.sock = self._tls.wrap_socket(raw, server_hostname=self._server_name)

    # -- seams ------------------------------------------------------------------------------------

    @classmethod
    def _run(cls, argv: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def _request(cls, dns_name: str, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 token: Optional[str] = None) -> Tuple[int, Any]:
        """One JSON request to management on loopback, the certificate checked against the CA."""
        context = ssl.create_default_context(cafile=str(RemoteCertificateAuthority.ca_certificate()))
        connection = cls._Connection(CONTROL_PLANE_PORT, dns_name, context, timeout=20)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            return 0, {"error": str(error)}
        finally:
            connection.close()
        try:
            return response.status, json.loads(raw) if raw else {}
        except ValueError:
            return response.status, {"error": raw.decode(errors="replace")[:300]}

    # -- the container ----------------------------------------------------------------------------

    @classmethod
    def render_config(cls, dns_name: str, secret: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.
            secret: The relay's shared secret.

        Returns:
            str: The combined server's `config.yaml`.
        """
        exposed = RemoteSettings.management_url(dns_name)
        return "\n".join([
            "# Written by `ling-admin remote setup` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2).",
            "server:",
            '  listenAddress: ":443"',
            f'  exposedAddress: "{exposed}"',
            '  healthcheckAddress: ":9000"',
            "  metricsPort: 9090",
            '  logLevel: "info"',
            '  logFile: "console"',
            "  tls:",
            '    certFile: "/nb/tls.crt"',
            '    keyFile: "/nb/tls.key"',
            f'  authSecret: "{secret}"',
            '  dataDir: "/nb/data"',
            "  disableAnonymousMetrics: true",
            "  disableGeoliteUpdate: true",
            "  # The box's STUN and relay; naming them switches the embedded ones off.",
            "  stuns:",
            f'    - uri: "stun:{dns_name}:{STUN_PORT}"',
            "  relays:",
            "    addresses:",
            f'      - "{RemoteSettings.relay_address(dns_name)}"',
            '    credentialsTTL: "12h"',
            f'    secret: "{secret}"',
            "  # Required by the server; no one signs in through it (peers enrol with setup keys).",
            "  auth:",
            f'    issuer: "{exposed}/oauth2"',
            "    localAuthDisabled: false",
            "  store:",
            '    engine: "sqlite"',
            "",
        ])

    @classmethod
    def write_files(cls, dns_name: str, secret: str, certificate: str, key: str) -> None:
        """
        Writes the server folder the container mounts: the configuration, the certificate and key.

        Args:
            dns_name: The box's public DNS name.
            secret: The relay's shared secret.
            certificate: The control plane's certificate (PEM).
            key: Its key (PEM).
        """
        RemoteSettings.write_private("server/config.yaml", cls.render_config(dns_name, secret))
        RemoteSettings.write_private("server/tls.crt", certificate)
        RemoteSettings.write_private("server/tls.key", key)
        (RemoteSettings.path("server", "data")).mkdir(parents=True, exist_ok=True)

    @classmethod
    def running(cls) -> bool:
        """
        Returns:
            bool: Whether the container is running.
        """
        result = cls._run(["docker", "inspect", "-f", "{{.State.Running}}", SERVER_CONTAINER], timeout=30)
        return result.returncode == 0 and result.stdout.strip() == "true"

    @classmethod
    def start(cls) -> bool:
        """
        Starts the container on loopback, replacing a stopped or differently configured one; the
        data folder (peers, keys, tokens) is kept.

        Returns:
            bool: True when it runs.
        """
        cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        result = cls._run([
            "docker", "run", "-d", "--name", SERVER_CONTAINER, "--restart", "unless-stopped",
            "--memory", MEMORY_LIMIT, "--label", "dreamference.service=remote-access",
            "-p", f"127.0.0.1:{CONTROL_PLANE_PORT}:443",
            "-v", f"{RemoteSettings.path('server')}:/nb",
            "-e", "NB_SETUP_PAT_ENABLED=true",
            SERVER_IMAGE, "--config", "/nb/config.yaml",
        ])
        if result.returncode != 0:
            print(f"❌ Could not start {SERVER_CONTAINER}: {result.stderr.strip()[:400]}")
            return False
        return True

    @classmethod
    def stop(cls) -> bool:
        """
        Removes the container; the data folder stays.

        Returns:
            bool: True when no container is left.
        """
        result = cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        return result.returncode == 0 or "No such container" in result.stderr

    @classmethod
    def wait_ready(cls, dns_name: str, seconds: int = 120) -> Optional[bool]:
        """
        Waits for the API.

        Args:
            dns_name: The box's public DNS name (the certificate's name).
            seconds: How long to wait.

        Returns:
            Optional[bool]: True when the instance still needs its first setup, False when it is
            set up, None when it never answered.
        """
        deadline = time.monotonic() + seconds
        while True:
            status, body = cls._request(dns_name, "GET", "/api/instance")
            if status == 200 and isinstance(body, dict):
                return bool(body.get("setup_required"))
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)

    # -- the management API -----------------------------------------------------------------------

    @classmethod
    def bootstrap(cls, dns_name: str, node_peer_name: str) -> Optional[str]:
        """
        Makes the instance's owner and the node's own management token, once: `/api/setup` gives a
        one-day token, which makes a 365-day one. The owner's password is random and discarded;
        nobody signs in.

        Args:
            dns_name: The box's public DNS name.
            node_peer_name: The node's peer name, for the owner's address (`…@<name>.invalid`).

        Returns:
            Optional[str]: The 365-day token, or None.
        """
        status, body = cls._request(dns_name, "POST", "/api/setup", {
            "email": f"owner@{node_peer_name}.invalid",
            "password": secrets.token_urlsafe(24) + "aA1!",
            "name": "Mightling node",
            "create_pat": True,
            "pat_expire_in": 1,
        })
        if status != 200 or not body.get("personal_access_token"):
            print(f"❌ The control plane refused its first setup ({status}): {str(body)[:300]}")
            return None
        return cls.renew_token(dns_name, body["personal_access_token"], body["user_id"])

    @classmethod
    def renew_token(cls, dns_name: str, token: str, user_id: Optional[str] = None) -> Optional[str]:
        """
        Makes a new 365-day token with a valid one.

        Args:
            dns_name: The box's public DNS name.
            token: A valid token.
            user_id: The owner's id; looked up when not given.

        Returns:
            Optional[str]: The new token, or None.
        """
        if user_id is None:
            status, users = cls._request(dns_name, "GET", "/api/users", token=token)
            owners = [user for user in users if isinstance(user, dict) and user.get("role") == "owner"] if status == 200 else []
            if not owners:
                return None
            user_id = owners[0]["id"]
        status, body = cls._request(dns_name, "POST", f"/api/users/{user_id}/tokens",
                                    {"name": f"mightling-node-{time.strftime('%Y%m%d')}", "expires_in": TOKEN_DAYS},
                                    token=token)
        if status != 200 or not body.get("plain_token"):
            print(f"❌ The control plane refused a management token ({status}): {str(body)[:300]}")
            return None
        cls.prune_tokens(dns_name, body["plain_token"], user_id, (body.get("personal_access_token") or {}).get("id"))
        return body["plain_token"]

    @classmethod
    def prune_tokens(cls, dns_name: str, token: str, user_id: str, keep: Optional[str]) -> None:
        """
        Deletes the node's older tokens (and the one-day setup token), so a renewal leaves one.

        Args:
            dns_name: The box's public DNS name.
            token: The new token, which does the deleting.
            user_id: The owner's id.
            keep: The new token's id; nothing is deleted without it.
        """
        if not keep:
            return
        status, tokens = cls._request(dns_name, "GET", f"/api/users/{user_id}/tokens", token=token)
        if status != 200 or not isinstance(tokens, list):
            return
        for entry in tokens:
            name = str(entry.get("name", ""))
            if entry.get("id") != keep and (name.startswith("mightling-node-") or name == "setup-token"):
                cls._request(dns_name, "DELETE", f"/api/users/{user_id}/tokens/{entry['id']}", token=token)

    @classmethod
    def setup_key(cls, dns_name: str, token: str, name: str) -> Optional[str]:
        """
        Mints a one-time setup key: one use, ten minutes, no groups beyond the default.

        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.
            name: What the key is for, kept in management's activity log.

        Returns:
            Optional[str]: The key, or None.
        """
        status, body = cls._request(dns_name, "POST", "/api/setup-keys", {
            "name": name, "type": "one-off", "expires_in": SETUP_KEY_MINUTES * 60,
            "auto_groups": [], "usage_limit": 1, "ephemeral": False,
        }, token=token)
        if status != 200 or not body.get("key"):
            print(f"❌ The control plane refused a setup key ({status}): {str(body)[:300]}")
            return None
        return body["key"]

    @classmethod
    def peers(cls, dns_name: str, token: str) -> Optional[List[Dict[str, Any]]]:
        """
        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.

        Returns:
            Optional[List[Dict[str, Any]]]: Every peer (`id`, `name`, `dns_label`, `connected`,
            `last_seen`, `os`), or None when management did not answer.
        """
        status, body = cls._request(dns_name, "GET", "/api/peers", token=token)
        return body if status == 200 and isinstance(body, list) else None

    @classmethod
    def delete_peer(cls, dns_name: str, token: str, peer_id: str) -> bool:
        """
        Deletes a peer: its WireGuard key is forgotten and its overlay address released.

        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.
            peer_id: The peer's id.

        Returns:
            bool: True when management deleted it.
        """
        status, _ = cls._request(dns_name, "DELETE", f"/api/peers/{peer_id}", token=token)
        return status == 200

    @classmethod
    def overlay_domain(cls) -> str:
        """
        Returns:
            str: The overlay's DNS domain (NetBird's default).
        """
        return OVERLAY_DOMAIN
