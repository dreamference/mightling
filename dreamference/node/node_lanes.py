"""
The model servers one run can spread its tasks over (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3).

A *lane* is one model server and the share of its KV pool a run may use. This machine's server is
always the first lane; a paired node serving the **same model** is a replica and adds a lane of its
own, sized from that node's own KV pool. The tasks still run here (their worktrees, scopes and
containers are this machine's); only their model requests go to the other node. A session stays on
the lane it started on, because the prefix cache is per server.

A paired node is left out, with the reason in the run's notes, when it does not answer over the
pairing, when its own night or benchmark run holds its runner lock, when its model server does not
answer, or when it serves another model: a run measures one configuration.
"""

import json
from typing import Any, Dict, Final, List, Optional, Tuple

from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_pairing import NodePairing

# What `[night] nodes` / `[swe_bench] nodes` may say besides a list of names.
NODES_PAIRED: Final[str] = "paired"
NODES_NONE: Final[tuple] = ("none", "off", "false", "")

DEFAULT_MODEL_PORT: Final[int] = 8000


class NodeLanes:
    """Builds the lanes of a night or benchmark run."""

    @classmethod
    def wanted(cls, setting: Any) -> Optional[List[str]]:
        """
        Reads the `nodes` setting.

        Args:
            setting: `"paired"` (the default: every paired node), `"none"`, `false`, or a list of
                paired nodes' names.

        Returns:
            Optional[List[str]]: The names asked for, `[]` for none, or None for every paired node.
        """
        if setting is None or setting is True:
            return None
        if isinstance(setting, (list, tuple)):
            return [str(name).strip() for name in setting if str(name).strip()]
        text = str(setting).strip().lower()
        if text == NODES_PAIRED:
            return None
        if text in NODES_NONE:
            return []
        return [name.strip() for name in str(setting).split(",") if name.strip()]

    @classmethod
    def info(cls, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Asks a paired node about itself over the pairing.

        Args:
            record: The paired node's record.

        Returns:
            Optional[Dict[str, Any]]: Its `info` answer, or None when it does not answer.
        """
        answer = NodePairing.run(record, "info")
        if answer.returncode != 0:
            return None
        for line in answer.stdout.splitlines():
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    return None
                return parsed if isinstance(parsed, dict) else None
        return None

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
    def lanes(cls, local_host: str, served: Tuple[str, int], setting: Any, host: Any,
              budget: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        This machine's lane and every usable replica's.

        Args:
            local_host: This machine's model server, as configured.
            served: What it serves: `(model id, context)`.
            setting: The run's `nodes` setting (see `wanted`).
            host: The probes (`NightShiftHost` or a test's stand-in): `served_model`, `metrics`.
            budget: `(kv_pool) -> (parallel, context limit or None)` for one server.

        Returns:
            Tuple[List[Dict[str, Any]], List[str]]: The lanes, this machine's first, each with
            `name`, `node`, `host`, `kv_pool`, `parallel` and `budget`; and a note per paired
            node that was left out, saying why.
        """
        metrics = host.metrics(local_host) or {}
        kv_pool = metrics.get("kv_pool", 0.0)
        parallel, limit = budget(kv_pool)
        lanes = [{"name": "this machine", "node": None, "host": local_host, "kv_pool": kv_pool,
                  "parallel": parallel, "budget": limit}]
        wanted = cls.wanted(setting)
        if wanted == []:
            return lanes, []
        notes: List[str] = []
        paired = NodePairing.paired()
        if wanted is not None:
            names = {record.get("name", "").lower() for record in paired}
            for name in wanted:
                if name.lower() not in names:
                    notes.append(f"{name}: not a paired node (`ling-admin node add {name}`), so not used.")
            paired = [record for record in paired if record.get("name", "").lower() in {n.lower() for n in wanted}]
        mine = NodeIdentity.read()
        for record in paired:
            if mine and record.get("node") == mine:
                continue
            name = record.get("name") or record["node"]
            # Where the node is now, never where it was: its lease may have moved (NodePairing.resolve).
            address = NodePairing.resolve(record)
            if address is None:
                notes.append(f"{name}: is not on the network (no node answering a browse carries its id), "
                             f"so its model server was not used.")
                continue
            info = cls.info(record)
            if info is None:
                notes.append(f"{name}: did not answer over the pairing, so its model server was not used.")
                continue
            if info.get("runner"):
                notes.append(f"{name}: {info['runner']} is in progress there, so its model server was not used.")
                continue
            port = info.get("model_port") or DEFAULT_MODEL_PORT
            url = f"http://{cls.url_host(address)}:{port}"
            theirs = host.served_model(url)
            if theirs is None:
                notes.append(f"{name}: its model server at {url} is not answering, so it was not used.")
                continue
            if theirs[0] != served[0]:
                notes.append(f"{name}: serves {theirs[0]}, not {served[0]}; a run measures one model, "
                             f"so it was not used.")
                continue
            their_metrics = host.metrics(url) or {}
            their_pool = their_metrics.get("kv_pool", 0.0)
            their_parallel, their_limit = budget(their_pool)
            lanes.append({"name": name, "node": record["node"], "host": url, "kv_pool": their_pool,
                          "parallel": their_parallel, "budget": their_limit})
        return lanes, notes

    @classmethod
    def describe(cls, lane: Dict[str, Any]) -> str:
        """
        Args:
            lane: A lane.

        Returns:
            str: One line for a run's notes about a replica lane.
        """
        pool = f"KV pool {int(lane['kv_pool'])} tokens" if lane.get("kv_pool") else "KV pool unknown"
        limit = f", each compacting at {lane['budget']} tokens" if lane.get("budget") else ""
        return (f"Also using {lane['name']}'s model server ({lane['host']}, {pool}): up to "
                f"{lane['parallel']} task(s) there{limit}. The tasks run on this machine.")
