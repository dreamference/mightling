"""The gate's limits on what it reads, and how it is started.

A body larger than the gate reads is refused with a 413 before anything reaches the engine; a
refused client that never finishes its body still gets the 503 once the drain gives up; a
Content-Length the gate cannot read frames no body; and `main` turns the addresses it is given
into the ones it serves on.
"""

import asyncio
import socket
import time

from test_model_gate import live, run_record, write  # noqa: F401 - fixture

from dreamference.vllm_server import model_gate_service
from dreamference.vllm_server.model_gate_service import RUN_FILE, ModelGateService, _address, main


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def test_a_body_larger_than_the_gate_reads_is_refused_with_413(monkeypatch, tmp_path):
    monkeypatch.setattr(model_gate_service, "BODY_LIMIT", 16)
    service = ModelGateService(("127.0.0.1", 0), ("127.0.0.1", 1), str(tmp_path))

    async def run():
        body = b"20\r\n" + b"x" * 32 + b"\r\n0\r\n\r\n"
        return await service.read_and_rewrite(
            _reader(body), ("chunked", 0), {"data": []}, guarded=True
        )

    refusal, body, swap = asyncio.run(run())
    assert refusal is not None
    status, payload = refusal
    assert status == 413
    assert payload["error"]["code"] == "request_too_large"
    assert body == b"" and swap is None


def test_a_body_within_the_limit_without_adapters_is_passed_on_unchanged(tmp_path):
    service = ModelGateService(("127.0.0.1", 0), ("127.0.0.1", 1), str(tmp_path))

    async def run():
        return await service.read_and_rewrite(
            _reader(b'{"a": 1}'), ("length", 8), None, guarded=False
        )

    assert asyncio.run(run()) == (None, b'{"a": 1}', None)


def test_a_content_length_the_gate_cannot_read_frames_no_body():
    assert ModelGateService.framing([(b"content-length", b"twelve")]) == ("none", 0)
    assert ModelGateService.framing([(b"content-length", b"")]) == ("none", 0)
    assert ModelGateService.framing([(b"content-length", b"12")]) == ("length", 12)
    assert ModelGateService.framing(
        [(b"transfer-encoding", b"gzip, Chunked"), (b"content-length", b"12")]
    ) == ("chunked", 0)


def test_a_refused_client_that_never_finishes_its_body_still_gets_the_503(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(model_gate_service, "DRAIN_TIMEOUT_S", 0.3)
    write(live["state"], RUN_FILE, run_record())
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", live["port"]), timeout=10) as client:
        # Promises a thousand bytes, sends ten and keeps the connection open.
        client.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\n" + b"x" * 10
        )
        answer = b""
        while chunk := client.recv(65536):
            answer += chunk
    assert answer.startswith(b"HTTP/1.1 503 ")
    assert time.monotonic() - started < 5


def test_a_client_that_closes_before_its_head_ends_gets_nothing(live):  # noqa: F811
    with socket.create_connection(("127.0.0.1", live["port"]), timeout=10) as client:
        client.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\n")
        client.shutdown(socket.SHUT_WR)
        assert client.recv(65536) == b""


def test_an_address_without_a_host_listens_everywhere_and_brackets_are_dropped():
    assert _address(":8000") == ("0.0.0.0", 8000)
    assert _address("[::1]:18000") == ("::1", 18000)
    assert _address("127.0.0.1:18000") == ("127.0.0.1", 18000)


def test_main_serves_on_the_addresses_it_is_given_and_an_interrupt_ends_it_cleanly(
    monkeypatch, tmp_path
):
    built = []

    class Service:
        def __init__(self, listen, upstream, state):
            built.append((listen, upstream, state))

        async def serve(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(model_gate_service, "ModelGateService", Service)
    code = main(["--listen", ":8000", "--upstream", "127.0.0.1:18000", "--state", str(tmp_path)])
    assert code == 0
    assert built == [(("0.0.0.0", 8000), ("127.0.0.1", 18000), str(tmp_path))]
