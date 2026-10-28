"""The overlay's control plane on the node (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §3).

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
import logging
import secrets
import socket
import ssl
import subprocess
import time
from pathlib import Path
from typing import Any, Final

from dreamference.remote.remote_certificate_authority import RemoteCertificateAuthority
from dreamference.remote.remote_settings import (
    CONTROL_ADDRESS,
    CONTROL_LISTEN_PORT,
    CONTROL_NETWORK,
    CONTROL_PLANE_PORT,
    CONTROL_SUBNET,
    OVERLAY_DOMAIN,
    PROXY_SERVICE_UNIT,
    PROXY_SOCKET_UNIT,
    SERVER_CONTAINER,
    SERVER_IMAGE,
    SETUP_KEY_MINUTES,
    SOCKET_PROXYD,
    STUN_PORT,
    RemoteSettings,
)

# Failures go to standard error through this logger; the console-facing `RemoteAccess` prints
# the rest (specs/DREAMFERENCE_PYTHON_QUALITY.md §3.9).
LOG: Final[logging.Logger] = logging.getLogger(__name__)

MEMORY_LIMIT: Final[str] = "512m"
TOKEN_DAYS: Final[int] = 365
# NetBird's geolocation databases, as the server names them in its data folder.
GEO_PATTERNS: Final[tuple[str, str]] = ("GeoLite2-City_*.mmdb", "geonames_*.db")
GEO_WAIT_S: Final[float] = 300.0
GEO_POLL_S: Final[float] = 2.0

HTTP_OK: Final[int] = 200
RUNNING: Final[str] = "true"
NO_SUCH_CONTAINER: Final[str] = "No such container"
OWNER_ROLE: Final[str] = "owner"
# The name `/api/setup` gives the one-day token it hands out.
SETUP_TOKEN_NAME: Final[str] = "setup-token"


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
    def _run(cls, argv: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def _request(
        cls,
        dns_name: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, Any]:
        """One JSON request to management on loopback, the certificate checked against the CA."""
        # The system store for the public certificate (spec §15), the node's CA for its own.
        context = ssl.create_default_context()
        ca = RemoteCertificateAuthority.ca_certificate()
        if ca.is_file():
            context.load_verify_locations(cafile=str(ca))
        connection = cls._Connection(CONTROL_PLANE_PORT, dns_name, context, timeout=20)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        try:
            connection.request(
                method, path, body=json.dumps(body) if body is not None else None, headers=headers
            )
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
    def render_config(cls, dns_name: str, secret: str, store_key: str) -> str:
        """The combined server's configuration.

        Args:
            dns_name: The box's public DNS name.
            secret: The relay's shared secret.
            store_key: The store's encryption key (base64 of 32 bytes). Without it the server makes
                a new one at every start, and after a restart the token it issued no longer works
                (found on the node after the restart of 2026-10-14).

        Returns:
            str: The combined server's `config.yaml`.
        """
        exposed = RemoteSettings.management_url(dns_name)
        return "\n".join(
            [
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
                f'    encryptionKey: "{store_key}"',
                "",
            ]
        )

    @classmethod
    def write_files(
        cls, dns_name: str, secret: str, store_key: str, certificate: str, key: str
    ) -> None:
        """Writes the server folder the container mounts: the configuration, the certificate and key.

        Args:
            dns_name: The box's public DNS name.
            secret: The relay's shared secret.
            store_key: The store's encryption key.
            certificate: The control plane's certificate (PEM).
            key: Its key (PEM).
        """
        RemoteSettings.write_private(
            "server/config.yaml", cls.render_config(dns_name, secret, store_key)
        )
        RemoteSettings.write_private("server/tls.crt", certificate)
        RemoteSettings.write_private("server/tls.key", key)
        (RemoteSettings.path("server", "data")).mkdir(parents=True, exist_ok=True)

    @classmethod
    def running(cls) -> bool:
        """Whether the control plane's container runs.

        Returns:
            bool: Whether the container is running.
        """
        result = cls._run(
            ["docker", "inspect", "-f", "{{.State.Running}}", SERVER_CONTAINER], timeout=30
        )
        return result.returncode == 0 and result.stdout.strip() == RUNNING

    @classmethod
    def run_command(cls, setup_pat: bool) -> list[str]:
        """The container's `docker run`.

        Args:
            setup_pat: True only for the start that creates the owner account, which lets
                `/api/setup` hand out a token; every later start leaves it off, so a wiped store
                behind a live tunnel can never give the world an owner's token.

        Returns:
            list[str]: The argv.
        """
        argv = [
            "docker", "run", "-d", "--name", SERVER_CONTAINER, "--restart", "unless-stopped",
            "--network", CONTROL_NETWORK, "--ip", CONTROL_ADDRESS,
            "--memory", MEMORY_LIMIT, "--memory-swap", MEMORY_LIMIT,
            "--label", "dreamference.service=remote-access",
            "-v", f"{RemoteSettings.path('server')}:/nb",
        ]  # fmt: skip
        if setup_pat:
            argv += ["-e", "NB_SETUP_PAT_ENABLED=true"]
        return [*argv, SERVER_IMAGE, "--config", "/nb/config.yaml"]

    @classmethod
    def start(cls, setup_pat: bool = False) -> bool:
        """Starts the container on its internal network, and the loopback proxy in front of it.

        A stopped or differently configured container is replaced; the data folder, with the peers,
        keys and tokens, is kept.

        Args:
            setup_pat: See `run_command`.

        Returns:
            bool: True when both run.
        """
        if cls._run(["docker", "network", "inspect", CONTROL_NETWORK], timeout=30).returncode != 0:
            created = cls._run(
                [
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--subnet",
                    CONTROL_SUBNET,
                    CONTROL_NETWORK,
                ],
                timeout=60,
            )
            if created.returncode != 0:
                LOG.error(
                    f"❌ Could not create the internal network {CONTROL_NETWORK}: {created.stderr.strip()[:300]}"
                )
                return False
        cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        if not cls.geolocation_ready() and not cls.prime_geolocation():
            return False
        result = cls._run(cls.run_command(setup_pat))
        if result.returncode != 0:
            LOG.error(f"❌ Could not start {SERVER_CONTAINER}: {result.stderr.strip()[:400]}")
            return False
        if not cls.install_proxy():
            LOG.error(f"❌ Could not start the loopback proxy ({PROXY_SOCKET_UNIT}).")
            return False
        return True

    @classmethod
    def restart(cls) -> bool:
        """Restarts the running container as it is, so that it reads its certificate again.

        NetBird's combined server loads its certificate once, at start. The container keeps its
        configuration (no setup token, the internal network); the peers' WireGuard sessions are
        peer to peer and carry on, and their management and signal streams reconnect.

        Returns:
            bool: True when Docker restarted it.
        """
        result = cls._run(["docker", "restart", "-t", "10", SERVER_CONTAINER], timeout=120)
        if result.returncode != 0:
            LOG.error(f"❌ Could not restart {SERVER_CONTAINER}: {result.stderr.strip()[:300]}")
        return result.returncode == 0

    @classmethod
    def geolocation_ready(cls) -> bool:
        """Whether the data folder holds NetBird's two geolocation databases.

        The server cannot start without them: with `disableGeoliteUpdate` it uses the files it
        finds, and with none it fetches them, which a container with no route out cannot (found on
        the node, 2026-10-27: `could not initialize geolocation service`, a restart loop).

        Returns:
            bool: True when both are there.
        """
        data = RemoteSettings.path("server", "data")
        return all(any(data.glob(pattern)) for pattern in GEO_PATTERNS)

    @classmethod
    def prime_geolocation(cls, seconds: float | None = None) -> bool:
        """Lets the server fetch its geolocation databases once, then takes it away.

        The pinned image is started on Docker's default bridge, with no port published (nothing
        can reach it) and the setup token never allowed; NetBird downloads the two databases from
        its own package server and checks them against the SHA-256 published beside them. As soon
        as both are in the data folder the container is removed, and every start after this one
        runs on the internal network with no route out.

        Args:
            seconds: How long to wait for the files; `GEO_WAIT_S` by default.

        Returns:
            bool: True when both databases arrived.
        """
        LOG.warning(
            "⚠️  Fetching NetBird's geolocation databases once (the only start with a route out)…"
        )
        argv = cls.run_command(setup_pat=False)
        start = argv.index("--network")
        argv[start : start + 4] = ["--network", "bridge"]
        argv[argv.index("--restart") + 1] = "no"
        cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        if cls._run(argv).returncode != 0:
            LOG.error("❌ Could not start the control plane to fetch its geolocation databases.")
            return False
        deadline = time.monotonic() + (GEO_WAIT_S if seconds is None else seconds)
        while not cls.geolocation_ready() and time.monotonic() < deadline:
            time.sleep(GEO_POLL_S)
        cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        if not cls.geolocation_ready():
            LOG.error(
                "❌ NetBird's geolocation databases did not arrive; `docker logs` of a manual run says why."
            )
            return False
        return True

    @classmethod
    def proxy_units(cls) -> dict[str, str]:
        """The loopback proxy's two user units: 127.0.0.1:33443 to the container's 443.

        Returns:
            dict[str, str]: Unit file name → text.
        """
        socket_unit = (
            "# Written by `ling-admin remote setup`: the control plane on loopback (REMOTE_ACCESS §2).\n"
            "[Unit]\nDescription=Mightling remote access: the control plane, on loopback\n\n"
            f"[Socket]\nListenStream=127.0.0.1:{CONTROL_PLANE_PORT}\n\n"
            "[Install]\nWantedBy=sockets.target\n"
        )
        service_unit = (
            "# Written by `ling-admin remote setup`.\n"
            "[Unit]\nDescription=Mightling remote access: the control plane's loopback proxy\n"
            f"Requires={PROXY_SOCKET_UNIT}\nAfter={PROXY_SOCKET_UNIT}\n\n"
            f"[Service]\nExecStart={SOCKET_PROXYD} {CONTROL_ADDRESS}:{CONTROL_LISTEN_PORT}\n"
        )
        return {PROXY_SOCKET_UNIT: socket_unit, PROXY_SERVICE_UNIT: service_unit}

    @classmethod
    def unit_dir(cls) -> Path:
        """The user's systemd unit folder.

        Returns:
            Path: The user's systemd unit folder, computed at call time (tests move `HOME`).
        """
        return Path.home() / ".config" / "systemd" / "user"

    @classmethod
    def install_proxy(cls) -> bool:
        """Writes the proxy's units and (re)starts them.

        Returns:
            bool: True when every systemctl step succeeded.
        """
        folder = cls.unit_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in cls.proxy_units().items():
            (folder / name).write_text(text, encoding="utf-8")
        steps = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "stop", PROXY_SERVICE_UNIT],
            ["systemctl", "--user", "enable", "--now", PROXY_SOCKET_UNIT],
        ]
        return all(cls._run(step, timeout=60).returncode == 0 for step in steps)

    @classmethod
    def remove_proxy(cls) -> None:
        """Stops the proxy and deletes its units and the internal network (`remove --purge`)."""
        cls._run(["systemctl", "--user", "disable", "--now", PROXY_SOCKET_UNIT], timeout=60)
        cls._run(["systemctl", "--user", "stop", PROXY_SERVICE_UNIT], timeout=60)
        for name in (PROXY_SOCKET_UNIT, PROXY_SERVICE_UNIT):
            (cls.unit_dir() / name).unlink(missing_ok=True)
        cls._run(["systemctl", "--user", "daemon-reload"], timeout=60)
        cls._run(["docker", "network", "rm", CONTROL_NETWORK], timeout=60)

    @classmethod
    def stop(cls) -> bool:
        """Removes the container; the data folder stays.

        Returns:
            bool: True when no container is left.
        """
        result = cls._run(["docker", "rm", "-f", SERVER_CONTAINER], timeout=60)
        return result.returncode == 0 or NO_SUCH_CONTAINER in result.stderr

    @classmethod
    def wait_ready(cls, dns_name: str, seconds: int = 120) -> bool | None:
        """Waits for the API.

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
            if status == HTTP_OK and isinstance(body, dict):
                return bool(body.get("setup_required"))
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)

    # -- the management API -----------------------------------------------------------------------

    @classmethod
    def bootstrap(cls, dns_name: str, node_peer_name: str) -> str | None:
        """Makes the instance's owner and the node's own management token, once.

        `/api/setup` gives a one-day token, which makes a 365-day one. The owner's password is
        random, used by nobody, and kept beside the token (`owner-password`, 0600) as the way back
        into the API should the token ever be lost.

        Args:
            dns_name: The box's public DNS name.
            node_peer_name: The node's peer name, for the owner's address (`…@<name>.invalid`).

        Returns:
            Optional[str]: The 365-day token, or None.
        """
        password = secrets.token_urlsafe(24) + "aA1!"
        RemoteSettings.write_private("owner-password", password + "\n")
        status, body = cls._request(
            dns_name,
            "POST",
            "/api/setup",
            {
                "email": f"owner@{node_peer_name}.invalid",
                "password": password,
                "name": "Mightling node",
                "create_pat": True,
                "pat_expire_in": 1,
            },
        )
        if status != HTTP_OK or not body.get("personal_access_token"):
            LOG.error(f"❌ The control plane refused its first setup ({status}): {str(body)[:300]}")
            return None
        return cls.renew_token(dns_name, body["personal_access_token"], body["user_id"])

    @classmethod
    def renew_token(cls, dns_name: str, token: str, user_id: str | None = None) -> str | None:
        """Makes a new 365-day token with a valid one.

        Args:
            dns_name: The box's public DNS name.
            token: A valid token.
            user_id: The owner's id; looked up when not given.

        Returns:
            Optional[str]: The new token, or None.
        """
        if user_id is None:
            status, users = cls._request(dns_name, "GET", "/api/users", token=token)
            owners = (
                [
                    user
                    for user in users
                    if isinstance(user, dict) and user.get("role") == OWNER_ROLE
                ]
                if status == HTTP_OK
                else []
            )
            if not owners:
                return None
            user_id = owners[0]["id"]
        status, body = cls._request(
            dns_name,
            "POST",
            f"/api/users/{user_id}/tokens",
            {"name": f"mightling-node-{time.strftime('%Y%m%d')}", "expires_in": TOKEN_DAYS},
            token=token,
        )
        if status != HTTP_OK or not body.get("plain_token"):
            LOG.error(
                f"❌ The control plane refused a management token ({status}): {str(body)[:300]}"
            )
            return None
        cls.prune_tokens(
            dns_name,
            body["plain_token"],
            user_id,
            (body.get("personal_access_token") or {}).get("id"),
        )
        return body["plain_token"]

    @classmethod
    def prune_tokens(cls, dns_name: str, token: str, user_id: str, keep: str | None) -> None:
        """Deletes the node's older tokens (and the one-day setup token), so a renewal leaves one.

        Args:
            dns_name: The box's public DNS name.
            token: The new token, which does the deleting.
            user_id: The owner's id.
            keep: The new token's id; nothing is deleted without it.
        """
        if not keep:
            return
        status, tokens = cls._request(dns_name, "GET", f"/api/users/{user_id}/tokens", token=token)
        if status != HTTP_OK or not isinstance(tokens, list):
            return
        for entry in tokens:
            name = str(entry.get("name", ""))
            if entry.get("id") != keep and (
                name.startswith("mightling-node-") or name == SETUP_TOKEN_NAME
            ):
                cls._request(
                    dns_name, "DELETE", f"/api/users/{user_id}/tokens/{entry['id']}", token=token
                )

    @classmethod
    def setup_key(
        cls,
        dns_name: str,
        token: str,
        name: str,
        ephemeral: bool = False,
        minutes: int = SETUP_KEY_MINUTES,
    ) -> str | None:
        """Mints a one-time setup key: one use, ten minutes by default, no groups beyond the default.

        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.
            name: What the key is for, kept in management's activity log.
            ephemeral: Whether the peer it enrols is ephemeral: management deletes such a peer
                by itself once it has been offline for ten minutes (a test runner's peer).
            minutes: How long the key may wait; management refuses it after that, spent or not.

        Returns:
            Optional[str]: The key, or None.
        """
        status, body = cls._request(
            dns_name,
            "POST",
            "/api/setup-keys",
            {
                "name": name,
                "type": "one-off",
                "expires_in": minutes * 60,
                "auto_groups": [],
                "usage_limit": 1,
                "ephemeral": ephemeral,
            },
            token=token,
        )
        if status != HTTP_OK or not body.get("key"):
            LOG.error(f"❌ The control plane refused a setup key ({status}): {str(body)[:300]}")
            return None
        return body["key"]

    @classmethod
    def peers(cls, dns_name: str, token: str) -> list[dict[str, Any]] | None:
        """Every peer of the overlay.

        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.

        Returns:
            Optional[List[Dict[str, Any]]]: Every peer (`id`, `name`, `dns_label`, `connected`,
            `last_seen`, `os`), or None when management did not answer.
        """
        status, body = cls._request(dns_name, "GET", "/api/peers", token=token)
        return body if status == HTTP_OK and isinstance(body, list) else None

    @classmethod
    def delete_peer(cls, dns_name: str, token: str, peer_id: str) -> bool:
        """Deletes a peer: its WireGuard key is forgotten and its overlay address released.

        Args:
            dns_name: The box's public DNS name.
            token: The node's management token.
            peer_id: The peer's id.

        Returns:
            bool: True when management deleted it.
        """
        status, _ = cls._request(dns_name, "DELETE", f"/api/peers/{peer_id}", token=token)
        return status == HTTP_OK

    @classmethod
    def overlay_domain(cls) -> str:
        """The overlay's DNS domain.

        Returns:
            str: The overlay's DNS domain (NetBird's default).
        """
        return OVERLAY_DOMAIN
