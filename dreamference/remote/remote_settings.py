"""Where remote access keeps its files, and what it pins (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §3).

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
from typing import Any, Final

NETBIRD_VERSION: Final[str] = "0.80.0"

# The combined server (management, signal and the embedded STUN/relay, the last two switched off by
# the external relay and STUN settings), the multi-architecture index pinned by digest.
SERVER_IMAGE: Final[str] = (
    "netbirdio/netbird-server:0.80.0"
    "@sha256:05b3d8d6d056e5062a2965d4d6e5d6d83f09f2bf9133947a8e5972e0e0e7a261"
)

# The relay for the box, by architecture: the per-platform manifest of `netbirdio/relay:0.80.0`
# (index sha256:1aff236d…) and the SHA-256 of `/go/bin/netbird-relay` inside it, a static binary.
RELAY_IMAGES: Final[dict[str, str]] = {
    "amd64": "netbirdio/relay@sha256:0ca78fc0d47d8534e3c23e679ba46d0d6dc95ac777a4ae3c3a56013799232fad",
    "arm64": "netbirdio/relay@sha256:fc86b094604812d8c97e082aa25c2bd959b25265582b75691f687b63eadb52ea",
}
RELAY_BINARY_SHA256: Final[dict[str, str]] = {
    "amd64": "8fbaf4085cb98db91e5538b443941c3b097b56441c7d150e8c92618bf3b62c92",
    "arm64": "394997a910bb256d4f8f86f215a9ff086927fae6e69f8cdbdcf43d9e4382a3f7",
}
RELAY_BINARY_IN_IMAGE: Final[str] = "/go/bin/netbird-relay"

# NetBird's client for the node's own peer, from netbird_0.80.0_checksums.txt.
CLIENT_ARCHIVES: Final[dict[str, str]] = {
    "amd64": "47ffaba4fc3929f31795bd6c5232d6c29744d3169e2c93e6d9c84624f0ef6405",
    "arm64": "8cbd99fa068a7b0f3968b2d31dc341acc17dd1e97c3053e61ed5905dbdae7341",
}
CLIENT_URL: Final[str] = (
    "https://github.com/netbirdio/netbird/releases/download/v{version}/netbird_{version}_linux_{arch}.tar.gz"
)

# The public certificate for the box's name (owner's decision, 2026-10-28, spec §15): lego, the ACME
# client, pinned by release and SHA-256 (lego_5.5.2_checksums.txt), run as the user with a DNS-01
# challenge in the owner's Cloudflare zone. The key and the certificate stay on the node.
LEGO_VERSION: Final[str] = "5.5.2"
LEGO_ARCHIVES: Final[dict[str, str]] = {
    "amd64": "2a35505089e7772c92e1e9ac144df91151ef2eca8568630db0ff91fca06d9bef",
    "arm64": "15b14ec2ab14fde69cc8396eb0204c5ce4327e31a486953225a6059b26db3e8c",
}
LEGO_URL: Final[str] = (
    "https://github.com/go-acme/lego/releases/download/v{version}/lego_v{version}_linux_{arch}.tar.gz"
)
ACME_PRODUCTION: Final[str] = "https://acme-v02.api.letsencrypt.org/directory"
ACME_STAGING: Final[str] = "https://acme-staging-v02.api.letsencrypt.org/directory"
# The Cloudflare API token with DNS edit on the zone (the owner's file, never printed).
CLOUDFLARE_TOKEN_FILE: Final[str] = "~/.config/dreamference/cloudflare-token"
CLOUDFLARE_API: Final[str] = "https://api.cloudflare.com/client/v4"
# Renewed when fewer days remain (or earlier when the CA's renewal information asks).
PUBLIC_RENEW_DAYS: Final[int] = 30
CERTIFICATE_SERVICE_UNIT: Final[str] = "mightling-remote-certificate.service"
CERTIFICATE_TIMER_UNIT: Final[str] = "mightling-remote-certificate.timer"

# The overlay's DNS domain: NetBird's default (the user's decision, 2026-10-10).
OVERLAY_DOMAIN: Final[str] = "netbird.selfhosted"

# On the node: the control plane listens on loopback only; the reverse tunnel is its one way in.
SERVER_CONTAINER: Final[str] = "dreamference-remote"
CONTROL_PLANE_PORT: Final[int] = 33443
# The container's network is internal, so the control plane has no route out of this machine: no
# metrics, no GeoLite download, nothing, because nothing can be reached (merged 2026-10-20). The host
# and the tunnel reach it through a loopback proxy on CONTROL_PLANE_PORT, as the Matrix homeserver.
CONTROL_NETWORK: Final[str] = "dreamference-remote"
CONTROL_SUBNET: Final[str] = "172.31.232.0/24"
CONTROL_ADDRESS: Final[str] = "172.31.232.10"
CONTROL_LISTEN_PORT: Final[int] = 443
PROXY_SOCKET_UNIT: Final[str] = "mightling-remote-control.socket"
PROXY_SERVICE_UNIT: Final[str] = "mightling-remote-control.service"
SOCKET_PROXYD: Final[str] = "/usr/lib/systemd/systemd-socket-proxyd"
# A box name of three labels or more is scoped to its parent (`remote.dreamference.ai` →
# `dreamference.ai`), so a later box under the same domain needs no new CA; a shorter one to itself.
SCOPE_PARENT_MIN_LABELS: Final[int] = 3
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
        """The remote-access folder.

        Returns:
            Path: `~/.config/dreamference/remote`, computed at call time (tests move `HOME`).
        """
        return Path("~/.config/dreamference/remote").expanduser()

    @classmethod
    def path(cls, *parts: str) -> Path:
        """A path under the remote-access folder.

        Args:
            *parts: A path under the folder.

        Returns:
            Path: That path.
        """
        return cls.folder().joinpath(*parts)

    @classmethod
    def read(cls) -> dict[str, Any] | None:
        """Reads `remote.json`.

        Returns:
            Optional[Dict[str, Any]]: `remote.json`, or None when remote access is not set up.
        """
        try:
            value = json.loads(cls.path("remote.json").read_text())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get("dns_name") else None

    @classmethod
    def write(cls, record: dict[str, Any]) -> None:
        """Writes `remote.json` whole, readable by this user only.

        Args:
            record: The record.
        """
        cls.write_private("remote.json", json.dumps(record, indent=2, sort_keys=True) + "\n")

    @classmethod
    def write_private(cls, name: str, text: str) -> Path:
        """Writes a file under the folder with mode 0600, the folder 0700.

        Args:
            name: The path under the folder.
            text: The content.

        Returns:
            Path: The file.
        """
        path = cls.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(cls.folder()).chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        Path(path).chmod(0o600)
        return path

    @classmethod
    def read_private(cls, name: str) -> str | None:
        """Reads a private file under the folder.

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
    def public_certificate(cls) -> bool:
        """Whether the control plane presents the public certificate.

        Returns:
            bool: True when `remote.json` records it (`remote certificate issue`).
        """
        record = cls.read()
        return bool(record and record.get("public_certificate"))

    @classmethod
    def node_peer_name(cls, node_id: str) -> str:
        """The node's peer name in the overlay.

        Args:
            node_id: The node's id (`~/.config/dreamference/node-id`).

        Returns:
            str: The node's host name in the overlay, one DNS label: `mightling-<id>`.
        """
        return f"{NODE_PEER_PREFIX}{node_id.strip().lower()}"

    @classmethod
    def overlay_name(cls, node_id: str) -> str:
        """The node's full overlay name.

        Args:
            node_id: The node's id.

        Returns:
            str: The node's full overlay name, `mightling-<id>.netbird.selfhosted`.
        """
        return f"{cls.node_peer_name(node_id)}.{OVERLAY_DOMAIN}"

    @classmethod
    def default_ca_scope(cls, dns_name: str) -> str:
        """The domain a new CA may sign under, for a box name.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: Its parent domain for a name of three labels or more, else the name itself.
        """
        labels = dns_name.lower().strip(".").split(".")
        return ".".join(labels[1:] if len(labels) >= SCOPE_PARENT_MIN_LABELS else labels)

    @classmethod
    def in_scope(cls, dns_name: str, scope: str) -> bool:
        """Whether a name lies in a CA scope.

        Args:
            dns_name: A DNS name.
            scope: A domain.

        Returns:
            bool: True for the domain itself or a name under it.
        """
        name, base = dns_name.lower().strip("."), scope.lower().strip(".")
        return name == base or name.endswith("." + base)

    @classmethod
    def management_url(cls, dns_name: str) -> str:
        """Where every peer reaches management and signal.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: Where every peer reaches management and signal: the box's name on 443.
        """
        return f"https://{dns_name}:{BOX_PUBLIC_PORT}"

    @classmethod
    def relay_address(cls, dns_name: str) -> str:
        """The relay's address, as NetBird writes it.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: The relay's address as NetBird writes it.
        """
        return f"rels://{dns_name}:{RELAY_PORT}"
