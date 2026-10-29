"""The addresses the forge listens on (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §0.3, §2.3).

Only where the node advertises itself on the LAN, plus loopback for `ling-admin`'s own API calls:
never a Docker bridge, a container's veth, a VPN or the remote-access overlay (NetBird's `wt0`,
Tailscale). Avahi's own `allow-interfaces` and `deny-interfaces` narrow it further when the
owner set them. The addresses are resolved each time the forge starts, never stored, so a DHCP
change is picked up by a restart.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Final

LOOPBACK: Final[str] = "127.0.0.1"
AVAHI_CONF: Final[Path] = Path("/etc/avahi/avahi-daemon.conf")
# Interfaces that are never the LAN: container networking, virtual machines, VPNs and overlays.
NOT_LAN: Final[re.Pattern] = re.compile(
    r"^(lo|docker\d*|br-.*|veth.*|virbr.*|vnet.*|cni.*|flannel.*|lxc.*|lxd.*|podman.*|tailscale.*|ts\d*|"
    r"wt\d*|nb-.*|netbird.*|zt.*|tun\d*|tap\d*|wg\d*|ppp\d*)$"
)
ALLOW_INTERFACES: Final[str] = "allow-interfaces"
DENY_INTERFACES: Final[str] = "deny-interfaces"
# `ip -j`'s family of an IPv4 address.
IPV4_FAMILY: Final[str] = "inet"


class ForgeBind:
    """Finds the advertised interfaces' addresses."""

    @classmethod
    def interfaces(cls) -> list[dict[str, Any]]:
        """List the interfaces that are up, with their IPv4 addresses.

        Returns:
            list[dict[str, Any]]: `ip -j -4 addr show up`, or an empty list when it fails.
        """
        try:
            result = subprocess.run(
                ["ip", "-j", "-4", "addr", "show", "up"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            data = json.loads(result.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        return data if isinstance(data, list) else []

    @classmethod
    def avahi_rules(cls, text: str | None = None) -> tuple[list[str], list[str]]:
        """Read Avahi's `allow-interfaces` and `deny-interfaces` lists.

        Args:
            text: avahi-daemon.conf's text; read from /etc when None.

        Returns:
            tuple[list[str], list[str]]: The `allow-interfaces` and `deny-interfaces` lists.
        """
        if text is None:
            try:
                text = AVAHI_CONF.read_text()
            except OSError:
                text = ""
        allow: list[str] = []
        deny: list[str] = []
        for line in text.splitlines():
            key, _, value = line.strip().partition("=")
            names = [name.strip() for name in value.split(",") if name.strip()]
            if key.strip() == ALLOW_INTERFACES:
                allow = names
            elif key.strip() == DENY_INTERFACES:
                deny = names
        return allow, deny

    @classmethod
    def lan_addresses(
        cls, interfaces: list[dict[str, Any]] | None = None, avahi_text: str | None = None
    ) -> list[str]:
        """Return the IPv4 address of each advertised interface, in `ip`'s order.

        Args:
            interfaces: `ip -j` output; read now when None.
            avahi_text: avahi-daemon.conf's text; read from /etc when None.

        Returns:
            list[str]: The IPv4 address of each advertised interface, in `ip`'s order; empty when
            the node is on no LAN.
        """
        allow, deny = cls.avahi_rules(avahi_text)
        found: list[str] = []
        for interface in cls.interfaces() if interfaces is None else interfaces:
            name = str(interface.get("ifname", ""))
            if not name or NOT_LAN.match(name) or name in deny or (allow and name not in allow):
                continue
            for info in interface.get("addr_info", []) or []:
                address = info.get("local")
                family = info.get("family", IPV4_FAMILY)
                if family == IPV4_FAMILY and address and address not in found:
                    found.append(address)
        return found

    @classmethod
    def advertised_addresses(cls) -> list[str]:
        """Return the LAN addresses the forge listens on now: none while the node is not advertised.

        Returns:
            list[str]: `lan_addresses()` once `node enable` has run and `node disable` has not,
            else an empty list (loopback only).
        """
        from dreamference.node.node_settings import NodeSettings

        return cls.lan_addresses() if NodeSettings.advertised() else []

    @classmethod
    def publish_arguments(
        cls, addresses: list[str], host_port: int, container_port: int
    ) -> list[str]:
        """Build one `-p` per address, never a bare port Docker would publish on every interface.

        Args:
            addresses: The LAN addresses; loopback is always added first.
            host_port: The port on the host.
            container_port: The port inside the container.

        Returns:
            list[str]: The `docker run` arguments.
        """
        arguments: list[str] = []
        for address in [LOOPBACK, *[a for a in addresses if a != LOOPBACK]]:
            arguments += ["-p", f"{address}:{host_port}:{container_port}"]
        return arguments
