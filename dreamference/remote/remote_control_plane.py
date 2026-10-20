"""NetBird's control plane on the node: management and signal, in one container (REMOTE_ACCESS §2).

The combined `netbird-server` image, pinned by digest, runs as `dreamference-remote` on an
**internal** Docker network, so it has no route out of this machine (no metrics, no GeoLite
download, nothing, because nothing can be reached); both are also switched off in its
configuration. It serves its own TLS with a certificate from the node's CA. The host reaches it on
`127.0.0.1:33073` through a loopback proxy (two user units running `systemd-socket-proxyd`, as for
the Matrix homeserver); the SSH tunnel to the box forwards to that same port. Nothing listens
beyond loopback.

Its relay and STUN are switched off by naming the box's (`relays.addresses`, `stuns`); the store's
encryption key is fixed in the configuration before the first start (§14.2, item 8). Memory is
capped, since it runs beside a resident model server.
"""

import json
from pathlib import Path

from dreamference.remote.netbird_pins import NETBIRD_SERVER_IMAGE
from dreamference.remote.remote_executor import RemoteExecutor
from dreamference.remote.remote_state import RemoteState

CONTAINER = "dreamference-remote"
VOLUME = "dreamference-remote-data"
NETWORK = "dreamference-remote"
SUBNET = "172.31.232.0/24"
CONTAINER_ADDRESS = "172.31.232.10"
PORT = 33073
LOCAL_URL = f"https://localhost:{PORT}"
MEMORY_LIMIT = "512m"
PROXY_SOCKET_UNIT = "mightling-remote-control.socket"
PROXY_SERVICE_UNIT = "mightling-remote-control.service"
SOCKET_PROXYD = "/usr/lib/systemd/systemd-socket-proxyd"
CONFIG_IN_CONTAINER = "/etc/netbird"
DATA_IN_CONTAINER = "/var/lib/netbird/"
RELAY_PUBLIC_PORT = 443
STUN_PORT = 3478
CREDENTIALS_TTL = "12h"
STOP_TIMEOUT_S = 60
RUN_TIMEOUT_S = 600


class RemoteControlPlane:
    """The `dreamference-remote` container, its configuration and its loopback proxy."""

    @classmethod
    def config_path(cls) -> Path:
        """The server's configuration on the host.

        Returns:
            Path: `remote/server/config.yaml`.
        """
        return RemoteState.server_dir() / "config.yaml"

    @classmethod
    def tls_dir(cls) -> Path:
        """Where the control plane's certificate and key are.

        Returns:
            Path: `remote/server/tls`.
        """
        return RemoteState.server_dir() / "tls"

    @classmethod
    def render_config(
        cls, dns_name: str, relay_name: str, relay_secret: str, store_key: str
    ) -> str:
        """The server's YAML configuration. Every value is a JSON string, which YAML reads as is.

        Args:
            dns_name: The box's name, the clients' management URL.
            relay_name: The relay's name on the box.
            relay_secret: The secret the relay shares with this server.
            store_key: The store's encryption key (base64 of 32 bytes).

        Returns:
            str: The file's text.
        """
        q = json.dumps
        lines = [
            "# Written by `ling-admin remote setup` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md).",
            "server:",
            f"  listenAddress: {q(f':{PORT}')}",
            f"  exposedAddress: {q(f'https://{dns_name}:443')}",
            "  tls:",
            f"    certFile: {q(f'{CONFIG_IN_CONTAINER}/tls/management.crt')}",
            f"    keyFile: {q(f'{CONFIG_IN_CONTAINER}/tls/management.key')}",
            f"  dataDir: {q(DATA_IN_CONTAINER)}",
            "  stuns:",
            f"    - uri: {q(f'stun:{relay_name}:{STUN_PORT}')}",
            "  relays:",
            f"    addresses: [{q(f'rels://{relay_name}:{RELAY_PUBLIC_PORT}')}]",
            f"    secret: {q(relay_secret)}",
            f"    credentialsTTL: {q(CREDENTIALS_TTL)}",
            "  disableAnonymousMetrics: true",
            "  disableGeoliteUpdate: true",
            "  auth:",
            f"    issuer: {q(f'https://{dns_name}:443/oauth2')}",
            "  store:",
            f"    engine: {q('sqlite')}",
            f"    encryptionKey: {q(store_key)}",
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def write_config(cls, text: str) -> None:
        """Writes the configuration (0600; the container reads it as root).

        Args:
            text: The YAML.
        """
        RemoteState.write_private(cls.config_path(), text)

    @classmethod
    def run_command(cls, setup_pat: bool) -> list[str]:
        """The container's `docker run`.

        Args:
            setup_pat: True only for the start that creates the owner account: it lets
                `POST /api/setup` return a token (§14.2, item 4). Every later start leaves it off.

        Returns:
            list[str]: The argv.
        """
        argv = [
            "docker", "run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
            "--network", NETWORK, "--ip", CONTAINER_ADDRESS,
            f"--memory={MEMORY_LIMIT}", f"--memory-swap={MEMORY_LIMIT}",
            "--label", "ling.remote-access=1",
            "-v", f"{VOLUME}:{DATA_IN_CONTAINER}",
            "-v", f"{RemoteState.server_dir()}:{CONFIG_IN_CONTAINER}:ro",
        ]  # fmt: skip
        if setup_pat:
            argv += ["-e", "NB_SETUP_PAT_ENABLED=true"]
        return [*argv, NETBIRD_SERVER_IMAGE]

    @classmethod
    def ensure_network(cls) -> bool:
        """Creates the internal network once.

        Returns:
            bool: Whether it exists afterwards.
        """
        if RemoteExecutor.ok(["docker", "network", "inspect", NETWORK]):
            return True
        return RemoteExecutor.ok(
            ["docker", "network", "create", "--internal", "--subnet", SUBNET, NETWORK]
        )

    @classmethod
    def running(cls) -> bool:
        """Whether the container runs.

        Returns:
            bool: True when Docker says it is running.
        """
        result = RemoteExecutor.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
        return result.returncode == 0 and result.stdout.strip() == str(True).lower()

    @classmethod
    def start(cls, setup_pat: bool) -> tuple[bool, str]:
        """Replaces the container with one on the current configuration; the volume keeps the state.

        Args:
            setup_pat: See `run_command`.

        Returns:
            tuple[bool, str]: Whether it started, and Docker's error when it did not.
        """
        if not cls.ensure_network():
            return False, f"could not create the internal Docker network {NETWORK}"
        RemoteExecutor.run(["docker", "rm", "-f", CONTAINER], timeout=STOP_TIMEOUT_S)
        result = RemoteExecutor.run(cls.run_command(setup_pat), timeout=RUN_TIMEOUT_S)
        if result.returncode != 0:
            return False, result.stderr.strip()[:500]
        return cls.install_proxy(), ""

    @classmethod
    def proxy_units(cls) -> dict[str, str]:
        """The loopback proxy's two user units.

        Returns:
            dict[str, str]: Unit file name → text.
        """
        socket = (
            "# Written by `ling-admin remote setup`: NetBird's control plane on loopback (REMOTE_ACCESS §2).\n"
            "[Unit]\nDescription=Mightling remote access: control plane, on loopback\n\n"
            f"[Socket]\nListenStream=127.0.0.1:{PORT}\n\n"
            "[Install]\nWantedBy=sockets.target\n"
        )
        service = (
            "# Written by `ling-admin remote setup`.\n"
            "[Unit]\nDescription=Mightling remote access: control plane proxy\n"
            f"Requires={PROXY_SOCKET_UNIT}\nAfter={PROXY_SOCKET_UNIT}\n\n"
            f"[Service]\nExecStart={SOCKET_PROXYD} {CONTAINER_ADDRESS}:{PORT}\n"
        )
        return {PROXY_SOCKET_UNIT: socket, PROXY_SERVICE_UNIT: service}

    @classmethod
    def unit_dir(cls) -> Path:
        """The user's systemd unit folder.

        Returns:
            Path: `~/.config/systemd/user`.
        """
        return Path.home() / ".config" / "systemd" / "user"

    @classmethod
    def install_proxy(cls) -> bool:
        """Writes the proxy's units, then (re)starts them.

        Returns:
            bool: Whether every systemctl step succeeded.
        """
        directory = cls.unit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in cls.proxy_units().items():
            (directory / name).write_text(text, encoding="utf-8")
        steps = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "stop", PROXY_SERVICE_UNIT],
            ["systemctl", "--user", "enable", "--now", PROXY_SOCKET_UNIT],
        ]
        return all(RemoteExecutor.ok(step) for step in steps)

    @classmethod
    def stop(cls) -> None:
        """Stops the proxy and the container; the configuration and the volume stay."""
        RemoteExecutor.run(["systemctl", "--user", "disable", "--now", PROXY_SOCKET_UNIT])
        RemoteExecutor.run(["systemctl", "--user", "stop", PROXY_SERVICE_UNIT])
        RemoteExecutor.run(["docker", "stop", CONTAINER], timeout=STOP_TIMEOUT_S)

    @classmethod
    def purge(cls) -> None:
        """Deletes the container, its volume (every peer and key), its network and the proxy units."""
        cls.stop()
        RemoteExecutor.run(["docker", "rm", "-f", CONTAINER], timeout=STOP_TIMEOUT_S)
        RemoteExecutor.run(["docker", "volume", "rm", VOLUME])
        RemoteExecutor.run(["docker", "network", "rm", NETWORK])
        for name in (PROXY_SOCKET_UNIT, PROXY_SERVICE_UNIT):
            (cls.unit_dir() / name).unlink(missing_ok=True)
        RemoteExecutor.run(["systemctl", "--user", "daemon-reload"])
