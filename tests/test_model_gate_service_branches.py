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
