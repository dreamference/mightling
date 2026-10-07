"""
Whether this node is advertised, and what it shares (specs/DREAMFERENCE_MIGHTLING_NODE.md §4).

Kept in `~/.config/dreamference/node-advertise.json`, not in `dreamference.toml`: that file is
resolved from the working directory first, and the address a container publishes on must not
depend on the folder `mling-admin chat configure` happened to be run from.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Final

LOOPBACK: Final[str] = "127.0.0.1"
EVERY_INTERFACE: Final[str] = "0.0.0.0"


class NodeSettings:
    """The two switches `mling-admin node enable|disable` set."""

    @classmethod
    def path(cls) -> Path:
        """
        Returns:
            Path: `~/.config/dreamference/node-advertise.json`, resolved at call time.
        """
        return Path(os.path.expanduser("~/.config/dreamference/node-advertise.json"))

    @classmethod
    def load(cls) -> Dict[str, bool]:
        """
        Returns:
            Dict[str, bool]: `advertise` (the node is offered to the network) and `web` (the web
            UI is offered with it). Both False when the file is missing or unreadable: a node
            that was never enabled keeps every bind on loopback.
        """
        try:
            data: Any = json.loads(cls.path().read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        advertise = data.get("advertise") is True
        return {"advertise": advertise, "web": advertise and data.get("web") is not False}

    @classmethod
    def save(cls, advertise: bool, web: bool = True) -> None:
        """
        Args:
            advertise: Whether the node is advertised.
            web: Whether the web UI is shared with the network when it is.
        """
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps({"advertise": advertise, "web": web}, indent=2) + "\n")
        os.replace(staging, path)

    @classmethod
    def advertised(cls) -> bool:
        """
        Returns:
            bool: True once `node enable` has run and `node disable` has not.
        """
        return cls.load()["advertise"]

    @classmethod
    def search_bind_address(cls) -> str:
        """
        Returns:
            str: The host address SearXNG publishes on: every interface on an advertised node,
            loopback otherwise.
        """
        return EVERY_INTERFACE if cls.advertised() else LOOPBACK

    @classmethod
    def web_bind_address(cls) -> str:
        """
        Returns:
            str: The host address the web UI's port 3000 publishes on: every interface on an
            advertised node that shares its web UI, loopback otherwise.
        """
        return EVERY_INTERFACE if cls.load()["web"] else LOOPBACK
