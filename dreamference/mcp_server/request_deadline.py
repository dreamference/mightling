"""The end of one web tool call's requests, however slowly the server sends them."""

import contextlib
import os
import socket
import threading
from types import TracebackType
from typing import Self, cast

import urllib3
from urllib3.connection import HTTPConnection

# The deadline of the call running on this thread, which its connections register with.
_CALL = threading.local()

# urllib3's pool classes (plain, TLS, SOCKS) and the same with connections that register.
_Pool = type[urllib3.HTTPConnectionPool]
_DEADLINE_POOLS: dict[_Pool, _Pool] = {}


class RequestDeadline:
    """The end of one call's requests, a fixed time after the call starts.

    The per-read timeout leaves a server sending a byte every few seconds, in the TLS handshake,
    the headers or the body, free to hold the call as long as it likes. While the deadline is
    entered, every socket a connection from `deadline_pools` opens on this thread is registered
    here (a duplicate of it, which stays valid when TLS takes the socket over); when it passes,
    each is shut down, which wakes a read blocked on it, and one opened later is shut down at
    once. Connecting is not covered: it ends at the connect timeout for each of the host's
    addresses.
    """

    def __init__(self, seconds: float) -> None:
        """Sets the deadline `seconds` after the block that enters it starts."""
        self.expired = threading.Event()
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def __enter__(self) -> Self:
        """Starts the clock and makes this the deadline of the calling thread's connections."""
        self._timer.start()
        _CALL.deadline = self
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Stops the clock and lets go of the duplicates; the sockets themselves stay open."""
        _CALL.deadline = None
        self._timer.cancel()
        with self._lock:
            for handle in self._sockets:
                handle.close()
            self._sockets.clear()

    def attach(self, sock: socket.socket) -> None:
        """Registers a socket the call opened, shutting it down if the deadline has passed."""
        with self._lock:
            handle = socket.socket(fileno=os.dup(sock.fileno()))
            self._sockets.append(handle)
            if self.expired.is_set():
                self._shut(handle)

    def _expire(self) -> None:
        with self._lock:
            self.expired.set()
            for handle in self._sockets:
                self._shut(handle)

    @staticmethod
    def _shut(handle: socket.socket) -> None:
        with contextlib.suppress(OSError):
            handle.shutdown(socket.SHUT_RDWR)


def deadline_pools(pools: dict[str, _Pool]) -> dict[str, _Pool]:
    """A pool manager's pool classes, each with connections that register with the deadline.

    Args:
        pools: The manager's `pool_classes_by_scheme`.

    Returns:
        dict[str, type[urllib3.HTTPConnectionPool]]: The same schemes, mapped to subclasses whose
            connections register each socket they open with the thread's RequestDeadline.
    """
    for pool in pools.values():
        if pool not in _DEADLINE_POOLS:
            # Typed as urllib3's protocol; every connection class it ships is an HTTPConnection.
            connection = cast(type[HTTPConnection], pool.ConnectionCls)
            _DEADLINE_POOLS[pool] = type(
                pool.__name__, (pool,), {"ConnectionCls": _registering(connection)}
            )
    return {scheme: _DEADLINE_POOLS[pool] for scheme, pool in pools.items()}


def _registering(connection: type[HTTPConnection]) -> type[HTTPConnection]:
    """`connection`'s subclass whose new sockets register with the thread's deadline."""

    def new_conn(self: HTTPConnection) -> socket.socket:
        sock = connection._new_conn(self)
        deadline = getattr(_CALL, "deadline", None)
        if deadline is not None:
            deadline.attach(sock)
        return sock

    return type(connection.__name__, (connection,), {"_new_conn": new_conn})
