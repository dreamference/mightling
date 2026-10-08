"""
A TCP relay from the benchmark network's gateway to another node's model server
(specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3, specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md).

An instance's container is on an internal Docker network whose only reachable address is the
gateway, where this machine's model server answers. A paired node serving the same model is not
reachable from there, and must not be made reachable by opening the network: the relay listens on
the gateway, on a port of its own, and forwards each connection to that one node's model port and
nowhere else. It lives as long as the run.
"""

import socket
import threading
from typing import Final, List, Optional, Tuple

BUFFER: Final[int] = 65536


class SweBenchRelay:
    """One listening socket on the gateway, forwarding to one model server."""

    def __init__(self, listen_address: str, target: Tuple[str, int]) -> None:
        """
        Args:
            listen_address: The gateway's address.
            target: The other node's model server, `(address, port)`.
        """
        self.listen_address = listen_address
        self.target = target
        self.port: Optional[int] = None
        self._server: Optional[socket.socket] = None
        self._threads: List[threading.Thread] = []
        self._closed = threading.Event()

    def start(self) -> int:
        """
        Listens on a free port of the gateway and starts forwarding.

        Returns:
            int: The port.
        """
        family = socket.AF_INET6 if ":" in self.listen_address else socket.AF_INET
        server = socket.socket(family, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.listen_address, 0))
        server.listen(64)
        self._server = server
        self.port = server.getsockname()[1]
        thread = threading.Thread(target=self._accept, name=f"relay-{self.port}", daemon=True)
        thread.start()
        self._threads.append(thread)
        return self.port

    def close(self) -> None:
        """Stops accepting; connections in flight end with their peers."""
        self._closed.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.target, timeout=10)
                upstream.settimeout(None)
            except OSError:
                client.close()
                continue
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()

    @classmethod
    def _pipe(cls, source: socket.socket, sink: socket.socket) -> None:
        try:
            while True:
                data = source.recv(BUFFER)
                if not data:
                    break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for end in (sink, source):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # Each direction closes only its source once both ends are shut.
            try:
                source.close()
            except OSError:
                pass
