"""The deadline of one web tool call: which sockets it shuts, and when it lets go of the thread."""

import socket

import pytest
import urllib3

from dreamference.mcp_server import request_deadline
from dreamference.mcp_server.request_deadline import RequestDeadline, deadline_pools


@pytest.fixture
def pair():
    """A connected pair of sockets: the call's end, and the server's end."""
    ours, theirs = socket.socketpair()
    ours.settimeout(5)
    with ours, theirs:
        yield ours, theirs


def opened_by_a_connection(sock: socket.socket) -> socket.socket:
    """Has a connection class wrapped as `deadline_pools` wraps them open `sock`."""

    class Connection:
        def _new_conn(self) -> socket.socket:
            return sock

    return request_deadline._registering(Connection)()._new_conn()  # type: ignore[arg-type]


def test_a_socket_opened_after_the_deadline_passed_is_shut_at_once(pair):
    # Shut, a read ends at once with nothing; open, it would wait out the socket's timeout.
    ours, _ = pair
    with RequestDeadline(0.01) as deadline:
        assert deadline.expired.wait(5)
        opened_by_a_connection(ours)
        assert ours.recv(4) == b""


def test_a_connection_opened_after_an_expired_call_is_not_that_calls_to_shut(pair):
    ours, theirs = pair
    with RequestDeadline(0.01) as deadline:
        assert deadline.expired.wait(5)
    opened_by_a_connection(ours)
    theirs.sendall(b"next")
    assert ours.recv(4) == b"next"


def test_a_deadline_left_before_it_passes_never_expires(pair):
    ours, theirs = pair
    with RequestDeadline(0.2) as deadline:
        opened_by_a_connection(ours)
    assert not deadline.expired.wait(0.6)
    theirs.sendall(b"open")
    assert ours.recv(4) == b"open"


def test_each_pool_class_is_wrapped_once_however_many_managers_ask():
    pools = urllib3.PoolManager().pool_classes_by_scheme
    first, second = deadline_pools(pools), deadline_pools(dict(pools))
    assert first.keys() == pools.keys()
    assert all(first[scheme] is second[scheme] for scheme in pools)
    assert all(issubclass(first[scheme], pools[scheme]) for scheme in pools)
