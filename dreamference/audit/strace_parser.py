"""
Reading an `strace` of a `ling` session (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3.1).

This module provides the StraceParser class. It reads the output of

    strace -f -qq -yy -e trace=connect,sendto,sendmsg,sendmmsg,execve,write,writev -s 256 -o <file> ling …

and extracts where the session's processes connected, which names they asked a resolver for,
which unix sockets they opened and which programs they started. `sendmmsg` is in the list because
it is how glibc sends a lookup's A and AAAA queries: without it a DNS query leaves no name in the
trace, only a connect to the resolver.

`-yy` labels every descriptor with its socket type and inode (`23<UDPv6:[10868489]>`), which is
what tells a route lookup from a connection: `connect()` on a UDP socket sends no packet, it only
asks the kernel for a route and fixes the peer. Chromium does exactly that before resolving any
host (its IPv6 reachability check: a UDP connect to `[2001:4860:4860::8888]:443`, then reading
the local address it was given), and no switch turns it off. Such a connect is recorded under
`route_lookups`, not `destinations`; the first payload sent on that socket (`send*`, or `write`/
`writev`, which a connected UDP socket also accepts) moves its destination back into
`destinations`, where the verdict judges it like any other. A trace without `-yy` labels keeps
the old reading, every connect a destination.
"""

import ipaddress
import os
import re
from typing import Dict, Final, List, Optional, Tuple

from dreamference.audit.egress_trace import EgressTrace

DNS_PORT: Final[int] = 53

# git subcommands that reach a network (`push` beyond the spec's list: it is one too).
NETWORKED_GIT: Final[frozenset] = frozenset({"ls-remote", "fetch", "clone", "pull", "push", "remote-https", "remote-http"})

# git's own options that take their value as the next word.
GIT_OPTIONS_WITH_VALUE: Final[frozenset] = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"})

# `1234 connect(…` (with `-o`), `[pid 1234] connect(…` (on a terminal) or `connect(…` (no -f).
LINE: Final[re.Pattern] = re.compile(r"^(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?([a-z_0-9]+)\((.*)$")

INET: Final[re.Pattern] = re.compile(r'sa_family=AF_INET, sin_port=htons\((\d+)\), sin_addr=inet_addr\("([^"]+)"\)')
INET6: Final[re.Pattern] = re.compile(r'sa_family=AF_INET6, sin6_port=htons\((\d+)\),.*?inet_pton\(AF_INET6, "([^"]+)"')
UNIX: Final[re.Pattern] = re.compile(r'sa_family=AF_UNIX, sun_path=(@?)"((?:[^"\\]|\\.)*)"')

# A descriptor as `-yy` prints it: `23<UDPv6:[10868489]>`, or once connected
# `23<UDP:[127.0.0.1:40000->127.0.0.53:53]>`.
FD_LABEL: Final[re.Pattern] = re.compile(r"^(\d+)(?:<([A-Za-z0-9]+):\[(.*)\]>)?$")

# The remote end of a connected socket's label: `->127.0.0.53:53` or `->[::1]:53`.
LABEL_PEER: Final[re.Pattern] = re.compile(r"->\[?([0-9A-Fa-f:.]+?)\]?:(\d+)$")

UDP_KINDS: Final[frozenset] = frozenset({"UDP", "UDPv6", "UDPLITE", "UDPLITEv6"})

# `1234 <... execve resumed>) = 0`: the result of a call another process's line interrupted.
RESUMED_EXECVE: Final[re.Pattern] = re.compile(r"^(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?<\.\.\. execve resumed>.*\)\s*=\s*(-?\d+)")

SIMPLE_ESCAPES: Final[Dict[str, int]] = {
    "n": 10, "t": 9, "r": 13, "v": 11, "f": 12, "a": 7, "b": 8, "e": 27, "\\": 92, '"': 34, "'": 39,
}


class StraceParser:
    """Turns trace text into an EgressTrace."""

    @classmethod
    def parse(cls, text: str) -> EgressTrace:
        """
        Parses a whole trace.

        Args:
            text (str): The trace, one syscall per line.

        Returns:
            EgressTrace: What the traced processes did.
        """
        trace = EgressTrace()
        # (pid, fd) of sockets connected to a resolver, whose later payloads are DNS queries.
        resolver_sockets: Dict[Tuple[str, str], str] = {}
        # Inode of a UDP socket that was only connected so far, to the destination it names.
        udp_routes: Dict[str, str] = {}
        # An execve whose result is on a later line (`<unfinished ...>`), by pid.
        pending_execve: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            resumed = RESUMED_EXECVE.match(line)
            if resumed:
                trace.lines += 1
                started = pending_execve.pop(resumed.group(1) or resumed.group(2) or "", None)
                if started is not None and int(resumed.group(3)) == 0:
                    cls._read_execve(started, trace)
                continue
            match = LINE.match(line)
            if not match:
                continue
            trace.lines += 1
            pid = match.group(1) or match.group(2) or ""
            syscall, rest = match.group(3), match.group(4)
            if syscall == "execve" and rest.rstrip().endswith("<unfinished ...>"):
                pending_execve[pid] = rest
            elif syscall == "execve":
                cls._read_execve(rest, trace)
            elif syscall in ("connect", "sendto", "sendmsg", "sendmmsg", "write", "writev"):
                cls._read_socket_call(pid, syscall, rest, trace, resolver_sockets, udp_routes)
        return trace

    @classmethod
    def _read_socket_call(cls, pid: str, syscall: str, rest: str, trace: EgressTrace,
                          resolver_sockets: Dict[Tuple[str, str], str],
                          udp_routes: Optional[Dict[str, str]] = None) -> None:
        udp_routes = {} if udp_routes is None else udp_routes
        fd, kind, inner = cls.fd_label(rest.split(",", 1)[0].strip())
        udp = kind in UDP_KINDS
        if syscall in ("write", "writev"):
            # Only a UDP socket's payload matters here: a stream's connect was already counted.
            if udp:
                cls._udp_payload(inner, trace, udp_routes)
            return
        destination = cls.destination_in(rest)
        if destination is not None:
            address, port = destination
            target = cls.format_destination(address, port)
            if port == DNS_PORT:
                trace.dns_servers[target] = trace.dns_servers.get(target, 0) + 1
                if syscall == "connect":
                    resolver_sockets[(pid, fd)] = target
            elif syscall == "connect" and udp and inner.isdigit():
                # A route lookup: nothing is sent until a payload follows on this socket.
                trace.route_lookups[target] = trace.route_lookups.get(target, 0) + 1
                udp_routes[inner] = target
                resolver_sockets.pop((pid, fd), None)
            else:
                trace.destinations[target] = trace.destinations.get(target, 0) + 1
                resolver_sockets.pop((pid, fd), None)
        elif syscall == "connect":
            # A unix socket, netlink or anything else: this fd is not a resolver's any more.
            resolver_sockets.pop((pid, fd), None)
        elif udp:
            # A payload on a connected UDP socket, which names no destination of its own.
            cls._udp_payload(inner, trace, udp_routes)
        unix = UNIX.search(rest)
        if unix:
            path = ("@" if unix.group(1) else "") + cls.unescape(unix.group(2)).decode("utf-8", "replace")
            trace.unix_sockets[path] = trace.unix_sockets.get(path, 0) + 1
        if syscall == "connect":
            return
        # A payload sent to a resolver, named in the call or connected earlier, is a DNS query.
        to_resolver = (destination is not None and destination[1] == DNS_PORT) or (pid, fd) in resolver_sockets
        if to_resolver:
            for payload in cls.strings_in(rest):
                name = cls.dns_query_name(payload)
                if name:
                    trace.dns_names[name] = trace.dns_names.get(name, 0) + 1

    @classmethod
    def _udp_payload(cls, inner: str, trace: EgressTrace, udp_routes: Dict[str, str]) -> None:
        """A payload sent on a UDP socket: its destination counts as reached, whatever the connect was."""
        target = udp_routes.get(inner)
        if target is None:
            peer = LABEL_PEER.search(inner)
            if peer is None:
                return
            address, port = peer.group(1), int(peer.group(2))
            if port == DNS_PORT:
                return  # A resolver's payload is read as a query by the caller's DNS rule.
            target = cls.format_destination(address, port)
        trace.destinations[target] = trace.destinations.get(target, 0) + 1

    @classmethod
    def fd_label(cls, text: str) -> Tuple[str, str, str]:
        """
        Splits a descriptor as strace printed it.

        Args:
            text (str): `23`, or with `-yy` `23<UDPv6:[10868489]>`.

        Returns:
            Tuple[str, str, str]: The descriptor number, the socket kind (`""` without a label)
            and what the brackets hold (an inode, or `local->remote` once connected).
        """
        match = FD_LABEL.match(text)
        if match is None:
            return text, "", ""
        return match.group(1), match.group(2) or "", match.group(3) or ""

    @classmethod
    def _read_execve(cls, rest: str, trace: EgressTrace) -> None:
        # A failed execve (`= -1 ENOENT`) is the shell walking PATH, not a program that ran.
        if re.search(r"\)\s*=\s*-1\b", rest):
            return
        strings = [value.decode("utf-8", "replace") for value in cls.strings_in(rest)]
        if not strings:
            return
        program, argv = strings[0], strings[1:]
        name = os.path.basename(program)
        trace.processes[name] = trace.processes.get(name, 0) + 1
        if name == "git" or name.startswith("git-"):
            subcommand = name[len("git-"):] if name.startswith("git-") else cls.git_subcommand(argv[1:])
            # `git ls-remote --get-url` only prints a URL after rewriting it; it contacts nothing.
            if subcommand == "ls-remote" and "--get-url" in argv:
                return
            if subcommand in NETWORKED_GIT:
                trace.networked_git.append(" ".join([name] + argv[1:])[:300])

    @classmethod
    def git_subcommand(cls, args: List[str]) -> Optional[str]:
        """
        Finds the subcommand of a git command line: the first word that is neither one of git's
        own options nor an option's value.

        Args:
            args (List[str]): The arguments after `git`.

        Returns:
            Optional[str]: `fetch`, `rev-parse`, …, or None for a bare `git` or `git --version`.
        """
        skip = False
        for arg in args:
            if skip:
                skip = False
            elif arg in GIT_OPTIONS_WITH_VALUE:
                skip = True
            elif not arg.startswith("-"):
                return arg
        return None

    @classmethod
    def destination_in(cls, text: str) -> Optional[Tuple[str, int]]:
        """
        Finds the IP destination named in a syscall's arguments.

        Args:
            text (str): The arguments as strace printed them.

        Returns:
            Optional[Tuple[str, int]]: Address and port, or None when the call names none (a
            connected socket, a unix socket, netlink).
        """
        match = INET.search(text)
        if match:
            return match.group(2), int(match.group(1))
        match = INET6.search(text)
        if match:
            return match.group(2), int(match.group(1))
        return None

    @classmethod
    def format_destination(cls, address: str, port: int) -> str:
        """
        Formats a destination as `ip:port`, with IPv6 in brackets.

        Args:
            address (str): The IP address.
            port (int): The port.

        Returns:
            str: `127.0.0.1:8000` or `[::1]:8000`.
        """
        return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"

    @classmethod
    def is_loopback(cls, address: str) -> bool:
        """
        Tells whether an address never leaves the machine.

        Args:
            address (str): An IPv4 or IPv6 address.

        Returns:
            bool: True for 127.0.0.0/8, ::1, their IPv4-mapped forms, and the unspecified
            address, which Linux connects to the local host.
        """
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        return ip.is_loopback or ip.is_unspecified

    @classmethod
    def strings_in(cls, text: str) -> List[bytes]:
        """
        Extracts every C string literal from a line of strace output, unescaped.

        Args:
            text (str): Part of a trace line.

        Returns:
            List[bytes]: The strings' bytes, in order.
        """
        strings: List[bytes] = []
        index = 0
        while index < len(text):
            if text[index] != '"':
                index += 1
                continue
            end = index + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            strings.append(cls.unescape(text[index + 1:end]))
            index = end + 1
        return strings

    @classmethod
    def unescape(cls, literal: str) -> bytes:
        """
        Decodes strace's C-style escapes: `\\n`, octal `\\236` and `\\0`, hex `\\x1f`.

        Args:
            literal (str): The text between the quotes.

        Returns:
            bytes: The bytes it stands for.
        """
        out = bytearray()
        index = 0
        while index < len(literal):
            char = literal[index]
            if char != "\\" or index + 1 >= len(literal):
                out.extend(char.encode("utf-8"))
                index += 1
                continue
            following = literal[index + 1]
            if following in "01234567":
                end = index + 1
                while end < len(literal) and end < index + 4 and literal[end] in "01234567":
                    end += 1
                out.append(int(literal[index + 1:end], 8) & 0xFF)
                index = end
            elif following == "x" and re.match(r"[0-9a-fA-F]{1,2}", literal[index + 2:index + 4]):
                digits = re.match(r"[0-9a-fA-F]{1,2}", literal[index + 2:index + 4]).group(0)
                out.append(int(digits, 16))
                index += 2 + len(digits)
            else:
                out.append(SIMPLE_ESCAPES.get(following, ord(following) & 0xFF))
                index += 2
        return bytes(out)

    @classmethod
    def dns_query_name(cls, payload: bytes) -> Optional[str]:
        """
        Reads the name a DNS query asks for.

        Args:
            payload (bytes): A UDP payload (strace may have cut it at 256 bytes; the name comes
                first, at offset 12).

        Returns:
            Optional[str]: The name, or None when the payload is not a DNS query.
        """
        # Header: id(2) flags(2) qdcount(2) ancount(2) nscount(2) arcount(2); QR is the top bit.
        if len(payload) < 14 or payload[2] & 0x80 or payload[4:6] != b"\x00\x01":
            return None
        labels: List[str] = []
        index = 12
        while index < len(payload):
            length = payload[index]
            if length == 0:
                break
            if length > 63 or index + 1 + length > len(payload):
                return None
            label = payload[index + 1:index + 1 + length]
            if not re.fullmatch(rb"[A-Za-z0-9_\-]+", label):
                return None
            labels.append(label.decode("ascii"))
            index += 1 + length
        else:
            return None
        return ".".join(labels).lower() if labels else None
