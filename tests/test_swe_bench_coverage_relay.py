"""The relay's failure paths: an unreachable model server, a dead listener, a broken pipe."""

import socket

from dreamference.swe_bench import swe_bench_relay as module
from dreamference.swe_bench.swe_bench_relay import SweBenchRelay


class FakeSocket:
    def __init__(self, chunks=(), fail_send=False, fail_close=False, fail_shutdown=True):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False
        self.fail_send = fail_send
        self.fail_close = fail_close
        self.fail_shutdown = fail_shutdown

    def recv(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        if self.fail_send:
            raise OSError("peer gone")
        self.sent.append(data)

    def shutdown(self, how):
        if self.fail_shutdown:
            raise OSError("not connected")

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError("close failed")


class OneShotServer:
    """Accepts one client, then reports a closed listener."""

    def __init__(self, client):
        self.client = client

    def accept(self):
        if self.client is None:
            raise OSError("listener closed")
        client, self.client = self.client, None
        return client, ("peer", 1)


def test_unreachable_model_server_closes_the_client_and_keeps_listening(monkeypatch):
    client = FakeSocket()
    relay = SweBenchRelay("127.0.0.1", ("model", 1))
    relay._server = OneShotServer(client)

    def refuse(target, timeout):
        raise OSError("refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    started = []
    monkeypatch.setattr(module.threading, "Thread", lambda **kw: started.append(kw))
    relay._accept()  # returns once the listener raises
    assert client.closed is True
    assert started == []


def test_a_closed_relay_stops_accepting_without_touching_the_model_server(monkeypatch):
    relay = SweBenchRelay("127.0.0.1", ("model", 1))
    relay._server = OneShotServer(FakeSocket())
    relay._closed.set()

    def never(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(socket, "create_connection", never)
    relay._accept()


def test_close_without_a_listener_and_with_a_failing_listener_does_not_raise():
    relay = SweBenchRelay("127.0.0.1", ("model", 1))
    relay.close()
    assert relay._closed.is_set()
    relay = SweBenchRelay("127.0.0.1", ("model", 1))
    relay._server = FakeSocket(fail_close=True)
    relay.close()
    assert relay._closed.is_set()


def test_pipe_survives_a_dead_peer_and_a_failing_close():
    source = FakeSocket(chunks=[b"abc"], fail_close=True)
    sink = FakeSocket(fail_send=True)
    SweBenchRelay._pipe(source, sink)
    assert sink.sent == []
    assert source.closed is True


def test_pipe_forwards_until_the_source_ends():
    source = FakeSocket(chunks=[b"ab", b"cd"])
    sink = FakeSocket()
    SweBenchRelay._pipe(source, sink)
    assert sink.sent == [b"ab", b"cd"]
