"""The gate's service where a client sends too little, too much or no HTTP at all."""

import asyncio
import json

import pytest

from dreamference.vllm_server.model_gate_guard import SAMPLING_ARMS, SAMPLING_KEY, ModelGateGuard
from dreamference.vllm_server.model_gate_service import ModelGateService


class Sink:
    """A stand-in for a connection's writer: it keeps what it is sent."""

    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def get_extra_info(self, name):
        return ("127.0.0.1", 50000) if name == "peername" else None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def _run(data, step):
    """Runs `step(reader)` over a client that sent `data` and then closed."""

    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return await step(reader)

    return asyncio.run(go())


# -- handle --------------------------------------------------------------------------------------------


def test_a_head_that_is_not_http_is_refused_without_reaching_the_engine(monkeypatch, tmp_path):
    async def no_engine(*args, **kwargs):
        pytest.fail("the gate connected to the engine")

    monkeypatch.setattr(asyncio, "open_connection", no_engine)
    service = ModelGateService(("127.0.0.1", 0), ("127.0.0.1", 1), str(tmp_path))
    sink = Sink()

    _run(b"\x16\x03\x01 not http\r\n\r\n", lambda reader: service.handle(reader, sink))

    assert sink.data.startswith(b"HTTP/1.1 400 Bad Request\r\n")
    assert b"Not an HTTP/1.x request." in sink.data
    assert sink.closed


# -- watch_client --------------------------------------------------------------------------------------


def test_what_a_client_sends_after_its_request_is_read_and_dropped():
    async def watch(reader):
        await ModelGateService.watch_client(reader)
        return await reader.read()

    assert _run(b"GET /v1/models HTTP/1.1\r\n\r\n", watch) == b""


# -- copy_body -----------------------------------------------------------------------------------------


def test_a_body_the_client_cuts_short_is_moved_as_far_as_it_came():
    sink = Sink()

    _run(b"0123", lambda reader: ModelGateService.copy_body(reader, ("length", 10), sink))

    assert sink.data == b"0123"


def test_a_discarded_chunked_body_is_read_no_further_than_the_limit():
    async def discard(reader):
        await ModelGateService.copy_body(reader, ("chunked", 0), None, limit=4)
        return await reader.read()

    assert _run(b"4\r\nabcd\r\n4\r\nefgh\r\n0\r\n\r\n", discard) == b"4\r\nefgh\r\n0\r\n\r\n"


def test_a_chunked_bodys_trailers_go_to_the_engine_with_it():
    sink = Sink()
    body = b"2\r\nab\r\n0\r\nX-Trailer: 1\r\n\r\n"

    _run(
        body + b"GET /next", lambda reader: ModelGateService.copy_body(reader, ("chunked", 0), sink)
    )

    assert sink.data == body
