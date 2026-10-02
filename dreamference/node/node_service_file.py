"""
The Avahi service file that advertises the node (specs/DREAMFERENCE_PUFFIN_NODE.md §5.1, §5.2).

A static file under `/etc/avahi/services/` needs no running publisher, so the node is advertised
after a reboot with nobody logged in. Creating it needs root once (`NodeAdvertiser.enable`); it
is then owned by the node's user, and `server start`, `server stop` and an update rewrite it in
place. Every method that writes is a no-op when the file does not exist, so a node that was never
enabled is never advertised by a side effect.
"""

import re
from html import escape, unescape
from pathlib import Path
from typing import Any, Dict, Final, Optional

SERVICE_TYPE: Final[str] = "_puffin-node._tcp"

# Version of the advertised contract. A client refuses a node whose `proto` is higher than its own.
PROTO: Final[int] = 1

STATES: Final[tuple] = ("stopped", "loading", "ready")

# "Leave this record as it is", for the two records whose value can also be "absent" (None).
KEEP: Final[object] = object()


class NodeServiceFile:
    """Renders, reads and rewrites `puffin-node.service`."""

    # Where Avahi looks. A class attribute so tests point it at a scratch folder.
    service_path: Path = Path("/etc/avahi/services/puffin-node.service")

    @classmethod
    def render(cls, port: int, node_id: str, version: str, state: str = "stopped",
               web_port: Optional[int] = None, search_port: Optional[int] = None,
               main: bool = False) -> str:
        """
        Renders the service file.

        Args:
            port: The model server's port (the SRV port).
            node_id: The node's stable id.
            version: Puffin's version on the node.
            state: `stopped`, `loading` or `ready`.
            web_port: The web UI's port; None when it is not shared.
            search_port: SearXNG's port; None when it is not shared.
            main: Whether the model assigned to the node is one a coding client can use.

        Returns:
            str: The file's exact text. The instance name is the host name (`%h`).
        """
        if state not in STATES:
            raise ValueError(f"not a node state: {state!r}")
        records = [f"proto={PROTO}", f"node={node_id}", f"version={version}"]
        if web_port is not None:
            records.append(f"web={web_port}")
        if search_port is not None:
            records.append(f"search={search_port}")
        records.append(f"state={state}")
        if main:
            records.append("main=1")
        lines = [
            "<?xml version=\"1.0\" standalone='no'?>",
            "<!DOCTYPE service-group SYSTEM \"avahi-service.dtd\">",
            "<!-- Written by `puffin-admin node enable`; rewritten by `puffin-admin server start|stop`. -->",
            "<service-group>",
            "  <name replace-wildcards=\"yes\">%h</name>",
            "  <service>",
            f"    <type>{SERVICE_TYPE}</type>",
            f"    <port>{int(port)}</port>",
        ]
        lines += [f"    <txt-record>{escape(record)}</txt-record>" for record in records]
        lines += ["  </service>", "</service-group>", ""]
        return "\n".join(lines)

    @classmethod
    def parse(cls, text: str) -> Optional[Dict[str, str]]:
        """
        Reads a service file back.

        Args:
            text: The file's text.

        Returns:
            Optional[Dict[str, str]]: `port` and every TXT record by key, or None if the text is
            not a `_puffin-node._tcp` service.
        """
        if f"<type>{SERVICE_TYPE}</type>" not in text:
            return None
        port = re.search(r"<port>(\d+)</port>", text)
        if not port:
            return None
        records: Dict[str, str] = {"port": port.group(1)}
        for record in re.findall(r"<txt-record>(.*?)</txt-record>", text):
            key, _, value = unescape(record).partition("=")
            records[key] = value
        return records

    @classmethod
    def read(cls) -> Optional[Dict[str, str]]:
        """
        Returns:
            Optional[Dict[str, str]]: The installed file's records, or None when the node is not
            advertised.
        """
        try:
            return cls.parse(cls.service_path.read_text())
        except OSError:
            return None

    @classmethod
    def write(cls, text: str) -> bool:
        """
        Rewrites the installed file in place. In place, not write-and-rename: the folder belongs
        to root and only the file is the user's.

        Args:
            text: The new content.

        Returns:
            bool: False when the file does not exist or this user cannot write it (`node enable`
            must then be run again).
        """
        if not cls.service_path.is_file():
            return False
        try:
            cls.service_path.write_text(text)
        except OSError:
            return False
        return True

    @classmethod
    def update(cls, state: Optional[str] = None, port: Optional[int] = None,
               main: Optional[bool] = None, version: Optional[str] = None,
               web_port: Any = KEEP, search_port: Any = KEEP) -> Optional[bool]:
        """
        Changes records of the installed file and leaves the others as they are.

        Args:
            state: The new `state`, if it changes.
            port: The model server's port, if it changes.
            main: Whether the node now serves a model a coding client can use, if known.
            version: Puffin's version, if it changes.
            web_port: The web UI's port, or None to stop advertising it; `KEEP` leaves it.
            search_port: SearXNG's port, or None to stop advertising it; `KEEP` leaves it.

        Returns:
            Optional[bool]: None when the node is not advertised (nothing to do), True when the
            file was rewritten or already said so, False when it could not be written.
        """
        current = cls.read()
        if current is None or "node" not in current:
            return None
        text = cls.render(
            port=int(port if port is not None else current["port"]),
            node_id=current["node"],
            version=version or current.get("version", ""),
            state=state or current.get("state", "stopped"),
            web_port=(int(current["web"]) if current.get("web", "").isdigit() else None) if web_port is KEEP else web_port,
            search_port=(int(current["search"]) if current.get("search", "").isdigit() else None) if search_port is KEEP else search_port,
            main=("main" in current) if main is None else main,
        )
        try:
            if cls.service_path.read_text() == text:
                return True
        except OSError:
            pass
        return cls.write(text)
