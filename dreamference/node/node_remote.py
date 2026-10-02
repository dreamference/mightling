"""
Managing other nodes from this one (specs/DREAMFERENCE_PUFFIN_NODE.md §12.3, §12.4, §15.1).

There is no primary: the machine a person types `puffin-admin node …` on is the one doing the
managing. Listing needs no pairing, because a node's model, load and KV pool are on its open
model port. Changing a node (its model, starting or stopping its server) goes over the SSH
pairing, and is carried out by that node's own `puffin-admin`.
"""

from typing import Any, Dict, List, Optional

from dreamference.node.node_browser import NodeBrowser
from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_pairing import NodePairing


class NodeRemote:
    """`puffin-admin node list|status|set|start|stop`."""

    @classmethod
    def list_lines(cls) -> List[str]:
        """
        Returns:
            List[str]: One line per node on the network: name, address, the model it serves and
            its context, its load, whether it is paired, and which one is this machine.
        """
        from dreamference.night_shift.night_shift_host import NightShiftHost
        nodes = NodeBrowser.browse()
        if not nodes:
            return ["No Puffin node answers on this network (`puffin-admin node enable` advertises this one)."]
        mine = NodeIdentity.read()
        paired = {record["node"] for record in NodePairing.paired()}
        lines = []
        for node in sorted(nodes, key=lambda entry: entry["name"]):
            host = f"http://{cls.url_host(node['address'])}:{node['port']}"
            served = NightShiftHost.served_model(host)
            metrics = NightShiftHost.metrics(host) if served else None
            if served:
                serving = f"{served[0]} ({served[1]} tokens)"
                load = f"{int(metrics['running'])} request(s) running" if metrics else "load unknown"
                pool = f", KV pool {int(metrics['kv_pool'])} tokens" if metrics and metrics.get("kv_pool") else ""
                detail = f"{serving}, {load}{pool}"
            else:
                detail = f"model server {node.get('state', 'not answering')}"
            tags = []
            if mine and node.get("node") == mine:
                tags.append("this machine")
            elif node.get("node") in paired:
                tags.append("paired")
            else:
                tags.append("not paired: `puffin-admin node add " + node["name"] + "` to manage it")
            if node.get("main") != "1":
                tags.append("not a coding model")
            lines.append(f"{node['name']}  {host}/v1  {detail}  Puffin {node.get('version', '?')}  ({'; '.join(tags)})")
        return lines

    @classmethod
    def url_host(cls, address: str) -> str:
        """
        Args:
            address: An IP address.

        Returns:
            str: The address as it goes in a URL (IPv6 in brackets).
        """
        return f"[{address}]" if ":" in address and not address.startswith("[") else address

    @classmethod
    def request(cls, name: str, request: str) -> int:
        """
        Sends one operation to a paired node, its output streaming to this terminal.

        Args:
            name: The node, by name, address or id.
            request: The operation.

        Returns:
            int: The node's exit code; 1 when the node is not paired.
        """
        record: Optional[Dict[str, Any]] = NodePairing.find(name)
        if record is None:
            print(f"❌ {name} is not a paired node. Pair once with: puffin-admin node add {name}")
            return 1
        return NodePairing.run(record, request, capture=False).returncode

    @classmethod
    def status(cls, name: str) -> int:
        """Shows that node's `puffin-admin status`."""
        return cls.request(name, "status")

    @classmethod
    def set_model(cls, name: str, model_key: str) -> int:
        """Assigns a model to that node and starts it there, by that node's own checks."""
        return cls.request(name, f"set-model {model_key}")

    @classmethod
    def start(cls, name: str) -> int:
        """Starts that node's model server with the model it has assigned."""
        return cls.request(name, "start")

    @classmethod
    def stop(cls, name: str) -> int:
        """Stops that node's model server."""
        return cls.request(name, "stop")
