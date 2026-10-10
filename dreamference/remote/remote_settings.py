"""
Where remote access keeps its files, and what it pins (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §3).

Everything the node holds for the overlay lives under `~/.config/dreamference/remote/`: the node's
certificate authority, the control plane's configuration and data, the relay's shared secret, the
management token, the reverse tunnel's key and `remote.json`, the one file that says remote access is
set up (the box's DNS name, the node's overlay name, the overlay's domain). No address is written
anywhere: the box is reached by its name, the node by its overlay name.

NetBird is used as published, pinned by version and digest: the combined server image on the node,
the relay binary for the box (taken from the relay image's per-architecture manifest and checked by
SHA-256 on the node and again on the box), and the client's release archive for the node's own peer.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Final, Optional

NETBIRD_VERSION: Final[str] = "0.80.0"

# The combined server (management, signal and the embedded STUN/relay, the last two switched off by
# the external relay and STUN settings), the multi-architecture index pinned by digest.
SERVER_IMAGE: Final[str] = (
    "netbirdio/netbird-server:0.80.0"
    "@sha256:05b3d8d6d056e5062a2965d4d6e5d6d83f09f2bf9133947a8e5972e0e0e7a261"
)

# The relay for the box, by architecture: the per-platform manifest of `netbirdio/relay:0.80.0`
# (index sha256:1aff236d…) and the SHA-256 of `/go/bin/netbird-relay` inside it, a static binary.
RELAY_IMAGES: Final[Dict[str, str]] = {
    "amd64": "netbirdio/relay@sha256:0ca78fc0d47d8534e3c23e679ba46d0d6dc95ac777a4ae3c3a56013799232fad",
    "arm64": "netbirdio/relay@sha256:fc86b094604812d8c97e082aa25c2bd959b25265582b75691f687b63eadb52ea",
}
RELAY_BINARY_SHA256: Final[Dict[str, str]] = {
    "amd64": "8fbaf4085cb98db91e5538b443941c3b097b56441c7d150e8c92618bf3b62c92",
    "arm64": "394997a910bb256d4f8f86f215a9ff086927fae6e69f8cdbdcf43d9e4382a3f7",
}
RELAY_BINARY_IN_IMAGE: Final[str] = "/go/bin/netbird-relay"

# NetBird's client for the node's own peer, from netbird_0.80.0_checksums.txt.
CLIENT_ARCHIVES: Final[Dict[str, str]] = {
    "amd64": "47ffaba4fc3929f31795bd6c5232d6c29744d3169e2c93e6d9c84624f0ef6405",
    "arm64": "8cbd99fa068a7b0f3968b2d31dc341acc17dd1e97c3053e61ed5905dbdae7341",
}
CLIENT_URL: Final[str] = (
    "https://github.com/netbirdio/netbird/releases/download/v{version}/netbird_{version}_linux_{arch}.tar.gz"
)

# The overlay's DNS domain: NetBird's default (the user's decision, 2026-10-10).
OVERLAY_DOMAIN: Final[str] = "netbird.selfhosted"

# On the node: the control plane listens on loopback only; the reverse tunnel is its one way in.
SERVER_CONTAINER: Final[str] = "dreamference-remote"
CONTROL_PLANE_PORT: Final[int] = 33443
# On the box: where the tunnel lands (loopback), the public port haproxy listens on, the relay's
# port (TCP for WebSocket, UDP for QUIC) and STUN's.
BOX_TUNNEL_PORT: Final[int] = 8443
BOX_PUBLIC_PORT: Final[int] = 443
RELAY_PORT: Final[int] = 33080
STUN_PORT: Final[int] = 3478
# The unprivileged account on the box that may do nothing but hold the tunnel's listener.
BOX_TUNNEL_USER: Final[str] = "mightling-tunnel"
BOX_RELAY_DIR: Final[str] = "/opt/mightling-relay"

# On the node's LAN, while `ling-admin remote code` waits for one client.
ENROL_PORT: Final[int] = 3190
ENROL_PATH: Final[str] = "/mightling/enrol"
CODE_MINUTES: Final[int] = 10
CODE_ATTEMPTS: Final[int] = 10
SETUP_KEY_MINUTES: Final[int] = 10

TUNNEL_UNIT: Final[str] = "mightling-remote-tunnel.service"

# The node's own peer name in the overlay: its id, so the name is the node's and nobody else's.
NODE_PEER_PREFIX: Final[str] = "mightling-"


class RemoteSettings:
    """The folder, the pins and `remote.json`."""

    @classmethod
    def folder(cls) -> Path:
        """
        Returns:
            Path: `~/.config/dreamference/remote`, computed at call time (tests move `HOME`).
        """
        return Path(os.path.expanduser("~/.config/dreamference/remote"))

    @classmethod
    def path(cls, *parts: str) -> Path:
        """
        Args:
            *parts: A path under the folder.

        Returns:
            Path: That path.
        """
        return cls.folder().joinpath(*parts)

    @classmethod
    def read(cls) -> Optional[Dict[str, Any]]:
        """
        Returns:
            Optional[Dict[str, Any]]: `remote.json`, or None when remote access is not set up.
        """
        try:
            value = json.loads(cls.path("remote.json").read_text())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get("dns_name") else None

    @classmethod
    def write(cls, record: Dict[str, Any]) -> None:
        """
        Writes `remote.json` whole, readable by this user only.

        Args:
            record: The record.
        """
        cls.write_private("remote.json", json.dumps(record, indent=2, sort_keys=True) + "\n")

    @classmethod
    def write_private(cls, name: str, text: str) -> Path:
        """
        Writes a file under the folder with mode 0600, the folder 0700.

        Args:
            name: The path under the folder.
            text: The content.

        Returns:
            Path: The file.
        """
        path = cls.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cls.folder(), 0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o600)
        return path

    @classmethod
    def read_private(cls, name: str) -> Optional[str]:
        """
        Args:
            name: The path under the folder.

        Returns:
            Optional[str]: The file's text, stripped, or None when it is absent or empty.
        """
        try:
            text = cls.path(name).read_text().strip()
        except OSError:
            return None
        return text or None

    @classmethod
    def node_peer_name(cls, node_id: str) -> str:
        """
        Args:
            node_id: The node's id (`~/.config/dreamference/node-id`).

        Returns:
            str: The node's host name in the overlay, one DNS label: `mightling-<id>`.
        """
        return f"{NODE_PEER_PREFIX}{node_id.strip().lower()}"

    @classmethod
    def overlay_name(cls, node_id: str) -> str:
        """
        Args:
            node_id: The node's id.

        Returns:
            str: The node's full overlay name, `mightling-<id>.netbird.selfhosted`.
        """
        return f"{cls.node_peer_name(node_id)}.{OVERLAY_DOMAIN}"

    @classmethod
    def management_url(cls, dns_name: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: Where every peer reaches management and signal: the box's name on 443.
        """
        return f"https://{dns_name}:{BOX_PUBLIC_PORT}"

    @classmethod
    def relay_address(cls, dns_name: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: The relay's address as NetBird writes it.
        """
        return f"rels://{dns_name}:{RELAY_PORT}"
