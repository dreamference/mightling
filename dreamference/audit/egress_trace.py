"""
What one traced `ling` session did on the network (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3.1).

This module provides the EgressTrace dataclass: the destinations, DNS names, unix sockets and
processes that StraceParser reads out of a trace, counted.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class EgressTrace:
    """Everything the audit's verdict is decided from."""

    # `ip:port` of every `connect`, `sendto`, `sendmsg` and `sendmmsg` to an IP address, with how
    # often it was seen. A connect that returned EINPROGRESS or failed still counts: the attempt
    # is what an audit is for. Port 53 is listed under `dns_servers` instead.
    destinations: Dict[str, int] = field(default_factory=dict)
    # Resolvers that were sent a query (`127.0.0.53:53` is systemd-resolved's stub).
    dns_servers: Dict[str, int] = field(default_factory=dict)
    # Every name asked of a resolver.
    dns_names: Dict[str, int] = field(default_factory=dict)
    # Every unix socket path a process connected or sent to.
    unix_sockets: Dict[str, int] = field(default_factory=dict)
    # Every program started, by its file name.
    processes: Dict[str, int] = field(default_factory=dict)
    # git commands that reach a network: `ls-remote`, `fetch`, `clone`, `pull`, `remote-https`.
    networked_git: List[str] = field(default_factory=list)
    # Lines of the trace that were read.
    lines: int = 0
