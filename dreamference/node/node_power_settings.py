"""Which plug powers which node (docs/dev/remote-access.md, "Power-cycling").

Kept in `~/.config/dreamference/node-power.json`, never in the repository: plug names and the
house's network are the owner's. Two tables:

- `plugs`: the plugs **this machine can reach** on its own network, by a name of the owner's choosing.
  `{"kind": "shelly", "host": "<name>", "channel": 0, "generation": 2}`. A node at another site keeps
  its plug here, on the machine at that site that acts for it, so nobody sending a request can
  name a host to talk to: a request names a plug that is already in this table.
- `nodes`: for each paired node, `{"plug": "<plug name>", "via": "<paired node>"}`; without `via`
  the plug is in this machine's own `plugs`.

A host is a name (`shellyplug-aabbcc.local`), never an address kept for later.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Final

from dreamference.node.node_shelly_plug import GENERATIONS, NodeShellyPlug

NAME: Final[re.Pattern] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
KINDS: Final[tuple[str, ...]] = ("shelly",)
MIN_CHANNEL: Final[int] = 0
COMMANDS: Final[tuple[str, ...]] = ("power-cycle", "power-config")
SUBCOMMANDS: Final[tuple[str, ...]] = ("plug", "node", "show")


class NodePowerSettings:
    """The plug table and the node table."""

    @classmethod
    def path(cls) -> Path:
        """The settings file.

        Returns:
            Path: `~/.config/dreamference/node-power.json`, resolved at call time.
        """
        return Path("~/.config/dreamference/node-power.json").expanduser()

    @classmethod
    def load(cls) -> dict[str, dict[str, Any]]:
        """Reads both tables.

        Returns:
            Dict[str, Dict[str, Any]]: `{"plugs": {...}, "nodes": {...}}`, both empty when the
            file is missing or unreadable; an entry that is not an object is dropped.
        """
        try:
            data: Any = json.loads(cls.path().read_text())
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {
            table: {
                key: value
                for key, value in (data.get(table) or {}).items()
                if isinstance(value, dict)
            }
            if isinstance(data.get(table), dict)
            else {}
            for table in ("plugs", "nodes")
        }

    @classmethod
    def _save(cls, data: dict[str, dict[str, Any]]) -> None:
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        Path(staging).replace(path)

    @classmethod
    def set_plug(
        cls, name: str, host: str, kind: str = "shelly", channel: int = 0, generation: int = 2
    ) -> str | None:
        """Records a plug this machine can reach.

        Args:
            name: The plug's name (the owner's).
            host: Its host name on this machine's network.
            kind: The brand (`shelly`).
            channel: The switch's id.
            generation: The plug's API generation.

        Returns:
            Optional[str]: None when saved, else why it was refused.
        """
        if not NAME.fullmatch(name):
            return f"{name!r} is not a plug name (letters, digits, . _ -)"
        if kind not in KINDS:
            return f"plug kind {kind!r} is not one of {', '.join(KINDS)}"
        if not NodeShellyPlug.valid_host(host):
            return f"{host!r} is not a plain host name (no scheme, port or path)"
        if channel < MIN_CHANNEL or generation not in GENERATIONS:
            return "channel is 0 or more, generation is 1 or 2"
        data = cls.load()
        data["plugs"][name] = {
            "kind": kind,
            "host": host,
            "channel": channel,
            "generation": generation,
        }
        cls._save(data)
        return None

    @classmethod
    def set_node(cls, node: str, plug: str, via: str | None = None) -> str | None:
        """Says which plug powers a node, and which paired node reaches it.

        Args:
            node: The paired node's name.
            plug: The plug's name (in this machine's table, or in `via`'s).
            via: The paired node at that site that acts for it; None for a plug this machine reaches.

        Returns:
            Optional[str]: None when saved, else why it was refused.
        """
        if not NAME.fullmatch(plug) or (via is not None and not NAME.fullmatch(via)):
            return "a plug or node name may hold letters, digits, . _ - only"
        if via is None and plug not in cls.load()["plugs"]:
            return f"no plug named {plug} here: `ling-admin node power-config plug {plug} --host …` first"
        data = cls.load()
        entry: dict[str, Any] = {"plug": plug}
        if via:
            entry["via"] = via
        data["nodes"][node] = entry
        cls._save(data)
        return None

    @classmethod
    def configure(cls, command: str | None, arguments: argparse.Namespace) -> int:
        """Carries out `ling-admin node power-config <plug|node|show>`.

        Args:
            command: `plug`, `node` or `show`.
            arguments: The parsed command line.

        Returns:
            int: The exit status.
        """
        if command not in SUBCOMMANDS:
            print("usage: ling-admin node power-config {plug,node,show}")
            return 2
        if command == SUBCOMMANDS[2]:
            print(json.dumps(cls.load(), indent=2, sort_keys=True))
            return 0
        refused = (
            cls.set_plug(
                arguments.plug,
                arguments.host,
                arguments.kind,
                arguments.channel,
                arguments.generation,
            )
            if command == SUBCOMMANDS[0]
            else cls.set_node(arguments.name, arguments.plug, arguments.via)
        )
        print(f"❌ {refused}" if refused else "✅ Saved.")
        return 1 if refused else 0

    @classmethod
    def dispatch(cls, arguments: argparse.Namespace) -> int:
        """Carries out `ling-admin node power-cycle` or `power-config`.

        Args:
            arguments: The parsed command line (`node_command` is one of `COMMANDS`).

        Returns:
            int: The exit status.
        """
        if arguments.node_command == COMMANDS[1]:
            return cls.configure(arguments.power_config_command, arguments)
        from dreamference.node.node_power_cycle import NodePowerCycle

        return NodePowerCycle.cycle(
            arguments.name,
            off_seconds=arguments.off_seconds,
            force=arguments.force,
            wait_minutes=arguments.wait_minutes,
        )
