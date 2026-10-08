"""
What a browse of the local network returns, from the node's side
(specs/DREAMFERENCE_MIGHTLING_NODE.md §5): `ling-admin node status` shows it so the owner sees what
clients see. Clients themselves browse with the launcher's own code, not with this.
"""

import re
import shutil
import subprocess
from typing import Dict, Final, List, Optional

from dreamference.node.node_service_file import SERVICE_TYPE

# What NVIDIA's first-boot wizard publishes on every finished unit (FLEET §3, §9.1).
SSH_SERVICE_TYPE: Final[str] = "_ssh._tcp"

# Host names a GB10 is shipped with: DGX Spark, ASUS Ascent GX10, HP ZGX.
GB10_NAMES: Final[re.Pattern] = re.compile(r"^(spark|gx10|zgx)-", re.IGNORECASE)


class NodeBrowser:
    """Browses `_mightling-node._tcp` through Avahi's own tool."""

    @classmethod
    def parse(cls, output: str, service_type: str = SERVICE_TYPE) -> List[Dict[str, str]]:
        """
        Reads `avahi-browse -rtp` output.

        Args:
            output: The tool's parseable output.
            service_type: The service to keep (`_mightling-node._tcp` unless told otherwise).

        Returns:
            List[Dict[str, str]]: One entry per node and address family seen on a real network
            interface: `name`, `host`, `address`, `port`, `interface` and the TXT records by key.
            Docker's bridges and container interfaces are left out: Avahi answers on them too,
            and no client is there.
        """
        nodes: List[Dict[str, str]] = []
        seen = set()
        for line in output.splitlines():
            fields = line.split(";")
            if len(fields) < 10 or fields[0] != "=" or fields[4] != service_type:
                continue
            interface = fields[1]
            if interface == "lo" or interface.startswith(("docker", "br-", "veth")):
                continue
            entry = {"interface": interface, "name": fields[3], "host": fields[6],
                     "address": fields[7], "port": fields[8]}
            for record in fields[9].split('" "'):
                key, _, value = record.strip('"').partition("=")
                if key:
                    entry[key] = value
            identity = (entry.get("node", entry["name"]), entry["address"])
            if identity not in seen:
                seen.add(identity)
                nodes.append(entry)
        return nodes

    @classmethod
    def unprovisioned(cls, match: Optional[str] = None, timeout: int = 6) -> List[Dict[str, str]]:
        """
        Finds machines `node provision` could set up: SSH hosts on the network that are not
        already advertised as Mightling nodes and whose name looks like a GB10's (FLEET §9.1).
        Nothing is decided from the name alone: provisioning checks `/etc/dgx-release` first.

        Args:
            match: A regular expression for names, instead of the GB10 patterns.
            timeout: Seconds for each browse.

        Returns:
            List[Dict[str, str]]: One entry per host (`name`, `host`, `address`, `port`).
        """
        pattern = re.compile(match, re.IGNORECASE) if match else GB10_NAMES
        advertised = cls.browse(timeout=timeout)
        nodes = {entry.get("host", "").lower() for entry in advertised}
        nodes |= {entry.get("name", "").lower() for entry in advertised}
        found: List[Dict[str, str]] = []
        seen = set()
        for entry in cls.browse_service(SSH_SERVICE_TYPE, timeout=timeout):
            host = entry["host"].lower()
            short = host.split(".")[0]
            if host in nodes or short in nodes or entry["name"].lower() in nodes:
                continue
            if not (pattern.search(short) or pattern.search(entry["name"])) or short in seen:
                continue
            seen.add(short)
            found.append(entry)
        return found

    @classmethod
    def browse_service(cls, service_type: str, timeout: int = 6) -> List[Dict[str, str]]:
        """
        Args:
            service_type: The DNS-SD service to browse.
            timeout: Seconds to allow the tool.

        Returns:
            List[Dict[str, str]]: See `parse`; empty when `avahi-browse` is missing or times out.
        """
        if not shutil.which("avahi-browse"):
            return []
        try:
            result = subprocess.run(["avahi-browse", "-rtp", service_type], capture_output=True,
                                    text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        return cls.parse(result.stdout, service_type)

    @classmethod
    def browse(cls, timeout: int = 6) -> List[Dict[str, str]]:
        """
        Browses the network once.

        Args:
            timeout: Seconds to allow the tool.

        Returns:
            List[Dict[str, str]]: See `parse`; empty when `avahi-browse` is missing or times out.
        """
        if not shutil.which("avahi-browse"):
            return []
        try:
            result = subprocess.run(["avahi-browse", "-rtp", SERVICE_TYPE], capture_output=True,
                                    text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        return cls.parse(result.stdout)
