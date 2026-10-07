"""
What a browse of the local network returns, from the node's side
(specs/DREAMFERENCE_MIGHTLING_NODE.md §5): `mling-admin node status` shows it so the owner sees what
clients see. Clients themselves browse with the launcher's own code, not with this.
"""

import shutil
import subprocess
from typing import Dict, List

from dreamference.node.node_service_file import SERVICE_TYPE


class NodeBrowser:
    """Browses `_mightling-node._tcp` through Avahi's own tool."""

    @classmethod
    def parse(cls, output: str) -> List[Dict[str, str]]:
        """
        Reads `avahi-browse -rtp` output.

        Args:
            output: The tool's parseable output.

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
            if len(fields) < 10 or fields[0] != "=" or fields[4] != SERVICE_TYPE:
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
