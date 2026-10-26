"""The model gate (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §18): its rules, the proxy itself in
front of a stand-in model server on scratch loopback ports, the host's side (container, pause,
resume) and `server start` putting the engine behind it.

Nothing here touches the model server or Docker of the machine running the suite: the stand-in
server and the gate bind 127.0.0.1 on ports the kernel picks, and every `docker` call is faked.
"""

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import REAL_GATE_PROBE

from dreamference.hardware import get_model_launch_overrides
from dreamference.vllm_server import model_gate, model_gate_service
from dreamference.vllm_server.model_gate import ModelGate
from dreamference.vllm_server.model_gate_guard import ModelGateGuard as GateGuard
from dreamference.vllm_server.model_gate_service import (
    PAUSE_FILE,
    RUN_FILE,
    STALE_S,
    ModelGateService,
)
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

SERVICE = Path(model_gate.__file__).with_name("model_gate_service.py")


def write(directory: Path, name: str, record) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(record if isinstance(record, str) else json.dumps(record))


def run_record(**overrides):
    record = {
        "run": "n1-default",
        "id": "abc",
        "label": "night 1",
        "networks": ["10.200.0.0/16"],
        "done": 37,
        "total": 100,
        "eta_s": 9 * 3600,
        "heartbeat": time.time(),
    }
    record.update(overrides)
    return record


# -- the rules ------------------------------------------------------------------------------------


def test_the_gate_is_open_without_a_live_run(tmp_path):
    assert ModelGateService.state(str(tmp_path))["state"] == "open"
    write(tmp_path, RUN_FILE, "{not json")
    assert ModelGateService.state(str(tmp_path))["state"] == "open"
    # A run that died without cleaning up stops refreshing its heartbeat: the gate opens by itself.
    write(tmp_path, RUN_FILE, run_record(heartbeat=time.time() - STALE_S - 1))
    state = ModelGateService.state(str(tmp_path))
    assert state["state"] == "open" and state["stale"] == "n1-default"
    # A record that names no network could not tell the run's requests apart: never closed.
    write(tmp_path, RUN_FILE, run_record(networks=[]))
    assert ModelGateService.state(str(tmp_path))["state"] == "open"


def test_a_live_run_closes_it_and_a_pause_opens_it(tmp_path):
    write(tmp_path, RUN_FILE, run_record())
    state = ModelGateService.state(str(tmp_path))
    assert state["state"] == "closed" and state["run"] == "n1-default" and state["id"] == "abc"
    assert state["message"] == (
        "The model is running a benchmark (night 1, 37/100 done, about 9 h left). "
        "Try later or run `ling-admin night pause`."
    )
    write(tmp_path, PAUSE_FILE, {"since": time.time() - 10, "until": time.time() + 600})
    assert ModelGateService.state(str(tmp_path))["state"] == "paused"
    write(tmp_path, PAUSE_FILE, {"since": time.time() - 10, "until": time.time() - 1})
    assert ModelGateService.state(str(tmp_path))["state"] == "closed"


@pytest.mark.parametrize(
    "seconds, text",
    [
        (None, None),
        (30, "about 1 min left"),
        (40 * 60, "about 40 min left"),
        (85 * 60, "about 1 h 25 min left"),
        (9 * 3600 + 600, "about 9 h left"),
        (2 * 3600 + 2 * 60, "about 2 h left"),
    ],
)
def test_the_eta_reads_like_a_person_would_say_it(seconds, text):
    assert ModelGateService.describe_eta(seconds) == text


def test_the_message_names_the_run_when_there_is_no_label_or_eta_yet():
    message = ModelGateService.message({"run": "acc-25", "done": 0, "total": 24, "eta_s": None})
    assert message == (
        "The model is running a benchmark (SWE-bench run acc-25, 0/24 done). "
        "Try later or run `ling-admin night pause`."
    )


def test_the_message_keeps_the_shape_the_signal_bridge_reads():
    # The Signal bridge recognises a refused question by this text inside Codex's error and relays
    # what is in the parentheses (ling-rs/signal/src/bridge.rs, `benchmark_holds_model`; SIGNAL
    # §17.3). Rewording it here means changing the bridge too, or it falls back to the raw error.
    message = ModelGateService.message(
        {"label": "night 4", "done": 37, "total": 100, "eta_s": 9 * 3600}
    )
    assert message.startswith("The model is running a benchmark (")
    details = message[len("The model is running a benchmark (") : message.index(").")]
    assert details == "night 4, 37/100 done, about 9 h left"
    assert ModelGateService.refusal({"message": message})["error"]["message"] == message


def test_only_the_runs_network_and_read_only_probes_pass_a_closed_gate():
    closed = {"state": "closed", "networks": ["172.30.0.0/16"]}
    assert ModelGateService.allows(closed, "172.30.0.5", "POST", "/v1/responses")
    assert ModelGateService.allows(closed, "::ffff:172.30.0.5", "POST", "/v1/chat/completions")
    for peer in ("127.0.0.1", "172.17.0.2", "192.168.50.106", "172.31.0.5"):
        assert not ModelGateService.allows(closed, peer, "POST", "/v1/responses")
    for path in (
        "/v1/models",
        "/v1/models/RadixArk/Qwen",
        "/metrics",
        "/health",
        "/get_model_info",
    ):
        assert ModelGateService.allows(closed, "127.0.0.1", "GET", path)
    # Reads that run the model, and anything that is not a read, are refused.
    assert not ModelGateService.allows(closed, "127.0.0.1", "GET", "/health_generate")
    assert not ModelGateService.allows(closed, "127.0.0.1", "POST", "/v1/models")
    assert not ModelGateService.allows(closed, "127.0.0.1", "POST", "/generate")
    for state in ({"state": "open"}, {"state": "paused", "networks": ["172.30.0.0/16"]}):
        assert ModelGateService.allows(state, "127.0.0.1", "POST", "/v1/responses")


def test_a_request_head_is_rewritten_to_one_request_per_connection():
    head = (
        b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\nKeep-Alive: 5\r\n"
        b"Content-Length: 2\r\n\r\n"
    )
    assert ModelGateService.rewrite_head(head) == (
        b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nConnection: close\r\n\r\n"
    )
    method, path, headers = ModelGateService.parse_head(
        b"GET http://h:8000/v1/models?x=1 HTTP/1.1\r\nA: b\r\n\r\n"
    )
    assert (method, path, headers) == ("GET", "/v1/models", [(b"a", b"b")])
    assert ModelGateService.parse_head(b"garbage\r\n\r\n") is None


# -- the proxy, live on loopback ------------------------------------------------------------------


class StandIn(BaseHTTPRequestHandler):
    """A stand-in model server: /v1/models, and a chat endpoint that streams SSE."""

    protocol_version = "HTTP/1.1"
    seen = []
    second_chunk = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        StandIn.seen.append(("GET", self.path, self.headers.get("Connection")))
        body = json.dumps({"data": [{"id": "stand-in", "max_model_len": 4096}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = b""
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(size)
                self.rfile.readline()
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        StandIn.seen.append(("POST", self.path, self.headers.get("Connection"), body))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b'data: {"delta": "one"}\n\n')
        self.wfile.flush()
        # The second chunk is sent only once the client has seen the first: a gate that buffered
        # the stream would deadlock here, and the test would time out.
        StandIn.second_chunk.wait(10)
        self.wfile.write(b'data: {"delta": "two"}\n\ndata: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


@pytest.fixture
def live(tmp_path):
    """A stand-in model server and the gate in front of it, both on scratch loopback ports."""
    StandIn.seen = []
    StandIn.second_chunk = threading.Event()
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    state_dir = tmp_path / "gate"
    state_dir.mkdir()
    loop = asyncio.new_event_loop()
    service = ModelGateService(
        ("127.0.0.1", 0), ("127.0.0.1", upstream.server_address[1]), str(state_dir)
    )
    port = loop.run_until_complete(service.start())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    yield {"port": port, "state": state_dir, "upstream": upstream}
    loop.call_soon_threadsafe(service.server.close)
    loop.call_soon_threadsafe(loop.stop)
    upstream.shutdown()


def post(
    port, path="/v1/chat/completions", body=b'{"model": "stand-in", "stream": true}', headers=None
):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    return urllib.request.urlopen(request, timeout=10)


def test_an_open_gate_streams_through_without_buffering(live):
    with post(live["port"]) as response:
        assert response.status == 200
        first = response.readline() + response.readline()
        assert first == b'data: {"delta": "one"}\n\n'
        StandIn.second_chunk.set()
        assert response.read() == b'data: {"delta": "two"}\n\ndata: [DONE]\n\n'
    method, path, connection, body = StandIn.seen[-1]
    assert (method, path, body) == (
        "POST",
        "/v1/chat/completions",
        b'{"model": "stand-in", "stream": true}',
    )
    assert connection == "close"  # one request per connection, whatever the client asked


def responses_body(**fields):
    return json.dumps({"model": "stand-in", "stream": True, "input": "hi", **fields}).encode()


def test_a_responses_body_that_asks_nothing_reaches_the_engine_byte_for_byte(live):
    # Codex's own metadata, as every request carries it, and nothing of the loop guard's: what
    # SWE-bench and Night Shift send by default.
    body = responses_body(client_metadata={"thread_id": "t1", "turn_id": "u1"})
    StandIn.second_chunk.set()
    with post(live["port"], path="/v1/responses", body=body) as response:
        assert b"[DONE]" in response.read()
    assert StandIn.seen[-1][3] == body


def test_the_loop_guards_cap_and_sampling_arm_reach_the_engine_as_fields(live):
    metadata = {
        "ling_surface": "phone",
        "ling_max_output_tokens": "4096",
        "ling_sampling": "nonthinking",
    }
    with post(
        live["port"], path="/v1/responses", body=responses_body(client_metadata=metadata)
    ) as response:
        # Still streamed: the first chunk arrives before the engine sends the second.
        assert response.readline() + response.readline() == b'data: {"delta": "one"}\n\n'
        StandIn.second_chunk.set()
        assert b"[DONE]" in response.read()
    sent = json.loads(StandIn.seen[-1][3])
    assert sent["max_output_tokens"] == 4096
    assert {
        key: sent[key] for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty")
    } == {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
    assert sent["client_metadata"] == metadata and sent["input"] == "hi"


def test_without_adapters_a_responses_body_the_guard_cannot_read_whole_streams_through(
    live, monkeypatch
):
    # Above the gate's read limit: passed on untouched, as before the guard, never refused.
    monkeypatch.setattr(model_gate_service, "BODY_LIMIT", 16)
    body = responses_body(client_metadata={"ling_max_output_tokens": "4096"})
    StandIn.second_chunk.set()
    with post(live["port"], path="/v1/responses", body=body) as response:
        assert response.status == 200 and b"[DONE]" in response.read()
    assert StandIn.seen[-1][3] == body
    # A chunked body, which Codex never sends, streams through too.
    with socket.create_connection(("127.0.0.1", live["port"]), timeout=10) as client:
        client.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        )
        while client.recv(65536):
            pass
    assert StandIn.seen[-1][3] == b"hello"


def test_a_value_the_client_set_wins_over_the_loop_guard():
    body = responses_body(
        max_output_tokens=100,
        temperature=0.0,
        client_metadata={"ling_max_output_tokens": "4096", "ling_sampling": "nonthinking"},
    )
    sent, note = GateGuard.rewrite(body)
    record = json.loads(sent)
    assert (
        record["max_output_tokens"] == 100
        and record["temperature"] == 0.0
        and record["top_p"] == 0.8
    )
    assert (
        note
        == "loop guard (?): sampling=nonthinking (top_p, top_k, min_p, presence_penalty, repetition_penalty)"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        responses_body(),
        responses_body(client_metadata="x"),
        responses_body(client_metadata={"ling_max_output_tokens": "lots"}),
        responses_body(client_metadata={"ling_max_output_tokens": "8"}),
        responses_body(client_metadata={"ling_sampling": "greedy"}),
    ],
)
def test_a_body_the_loop_guard_cannot_read_or_that_asks_nothing_is_left_alone(body):
    assert GateGuard.rewrite(body) == (body, None)


def test_only_responses_bodies_are_read_for_the_loop_guard():
    assert GateGuard.applies("POST", "/v1/responses")
    assert GateGuard.applies("POST", "/v1/responses?x=1")
    assert not GateGuard.applies("GET", "/v1/responses")
    assert not GateGuard.applies("POST", "/v1/chat/completions")


def test_a_closed_gate_refuses_others_with_the_runs_name_and_eta(live):
    write(live["state"], RUN_FILE, run_record())
    with pytest.raises(urllib.error.HTTPError) as refused:
        post(live["port"], path="/v1/responses", body=b"x" * 200_000)
    error = refused.value
    assert error.code == 503
    assert (
        error.headers["Retry-After"] == "0" and error.headers["Content-Type"] == "application/json"
    )
    assert json.loads(error.read()) == {
        "error": {
            "message": "The model is running a benchmark (night 1, 37/100 done, about 9 h left). "
            "Try later or run `ling-admin night pause`.",
            "type": "service_unavailable",
            "code": "benchmark_running",
            "param": None,
        }
    }
    assert not [entry for entry in StandIn.seen if entry[0] == "POST"], "the engine never saw it"
    # Read-only probes still pass: the launcher can start and say what the model is.
    with urllib.request.urlopen(
        f"http://127.0.0.1:{live['port']}/v1/models", timeout=10
    ) as response:
        assert json.load(response)["data"][0]["id"] == "stand-in"


def test_the_runs_own_requests_and_a_pause_pass_a_closed_gate(live):
    # The suite's client is on loopback; naming loopback as the run's network plays the run.
    write(live["state"], RUN_FILE, run_record(networks=["127.0.0.0/8"]))
    StandIn.second_chunk.set()
    with post(live["port"]) as response:
        assert response.status == 200 and b"[DONE]" in response.read()
    write(live["state"], RUN_FILE, run_record())
    write(live["state"], PAUSE_FILE, {"since": time.time(), "until": time.time() + 60})
    with post(live["port"]) as response:
        assert response.status == 200 and b"[DONE]" in response.read()


def test_a_chunked_request_body_reaches_the_engine_whole(live):
    StandIn.second_chunk.set()
    with socket.create_connection(("127.0.0.1", live["port"]), timeout=10) as client:
        client.sendall(
            b"POST /v1/completions HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        )
        answer = b""
        while chunk := client.recv(65536):
            answer += chunk
    assert answer.startswith(b"HTTP/1.0 200") or answer.startswith(b"HTTP/1.1 200")
    assert StandIn.seen[-1][3] == b"hello world"


def test_a_kept_alive_connection_cannot_carry_a_second_request_past_the_gate(live):
    StandIn.second_chunk.set()
    with socket.create_connection(("127.0.0.1", live["port"]), timeout=10) as client:
        client.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n")
        time.sleep(0.3)
        # The gate closes meanwhile; the client tries the same connection again.
        write(live["state"], RUN_FILE, run_record())
        try:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
        except OSError:
            pass
        answer = b""
        try:
            while chunk := client.recv(65536):
                answer += chunk
        except OSError:
            pass
    assert answer.count(b"HTTP/1.") == 1, "one answer, to the first request only"
    assert not [entry for entry in StandIn.seen if entry[0] == "POST"]


def test_the_client_is_told_to_close_whatever_the_engine_says(tmp_path):
    """An engine that ignored `Connection: close` must still not get a pooled connection."""
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def keep_alive():
        connection, _ = upstream.accept()
        connection.recv(65536)
        connection.sendall(
            b"HTTP/1.1 100 Continue\r\n\r\n"
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\nKeep-Alive: timeout=5\r\n\r\nok"
        )
        time.sleep(2)  # and keeps the connection open
        connection.close()

    threading.Thread(target=keep_alive, daemon=True).start()
    loop = asyncio.new_event_loop()
    service = ModelGateService(
        ("127.0.0.1", 0), ("127.0.0.1", upstream.getsockname()[1]), str(tmp_path)
    )
    port = loop.run_until_complete(service.start())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
            answer = b""
            while not answer.endswith(b"ok"):
                answer += client.recv(65536)
    finally:
        loop.call_soon_threadsafe(service.server.close)
        loop.call_soon_threadsafe(loop.stop)
        upstream.close()
    interim, final = answer.split(b"\r\n\r\n", 1)[0], answer.split(b"\r\n\r\n", 1)[1]
    assert interim == b"HTTP/1.1 100 Continue"
    assert final == b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"


def test_a_tool_waiting_for_the_server_is_told_why_instead_of_waiting_out_the_run(
    monkeypatch, capsys
):
    from dreamference.config import DreamferenceConfig
    from dreamference.runner.vllm_readiness_waiter import VLLMReadinessWaiter

    waiter = VLLMReadinessWaiter(config=DreamferenceConfig())
    monkeypatch.setattr(waiter.vllm_manager, "check_health", lambda timeout=0.5: False)
    monkeypatch.setattr(
        ModelGate,
        "probe",
        classmethod(
            lambda cls, host, timeout=2.0: {
                "gate": "mightling",
                "state": "closed",
                "message": "The model is running a benchmark (night 1).",
            }
        ),
    )
    assert waiter.wait_for_vllm(poll_interval=0.01, max_wait=5) is False
    assert "⛔ The model is running a benchmark (night 1)." in capsys.readouterr().out


def test_a_gate_whose_engine_is_down_says_so(live):
    live["upstream"].shutdown()
    live["upstream"].server_close()
    with pytest.raises(urllib.error.HTTPError) as failed:
        post(live["port"])
    assert failed.value.code == 502
    assert "not answering behind its gate" in json.loads(failed.value.read())["error"]["message"]


def test_the_probe_answers_from_the_gate_itself(live, monkeypatch):
    monkeypatch.setattr(ModelGate, "probe", REAL_GATE_PROBE)
    write(live["state"], RUN_FILE, run_record())
    state = ModelGate.probe(f"http://127.0.0.1:{live['port']}")
    assert state["state"] == "closed" and state["run"] == "n1-default"
    assert not [entry for entry in StandIn.seen if entry[1] == "/mightling-gate"]
    # An engine without a gate answers that path 404: no gate.
    assert ModelGate.probe(f"http://127.0.0.1:{live['upstream'].server_address[1]}") is None


def test_the_service_runs_on_its_own_as_the_container_runs_it(tmp_path):
    """The file is copied into the engine's image and run there, without the package."""
    copy = tmp_path / "model_gate_service.py"
    copy.write_text(SERVICE.read_text())
    # The adapter rules travel beside it, as ModelGate.start copies them.
    (tmp_path / "model_gate_adapters.py").write_text(
        SERVICE.with_name("model_gate_adapters.py").read_text()
    )
    (tmp_path / "model_gate_guard.py").write_text(
        SERVICE.with_name("model_gate_guard.py").read_text()
    )
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    state = tmp_path / "state"
    write(state, RUN_FILE, run_record())
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(copy),
            "--listen",
            f"127.0.0.1:{port}",
            "--upstream",
            "127.0.0.1:9",
            "--state",
            str(state),
        ],
        cwd=str(tmp_path),
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/mightling-gate", timeout=1
                ) as response:
                    assert json.load(response)["state"] == "closed"
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)
        else:
            pytest.fail("the gate never answered")
        with pytest.raises(urllib.error.HTTPError) as refused:
            post(port, path="/v1/responses")
        assert refused.value.code == 503
    finally:
        process.terminate()
        _, errors = process.communicate(timeout=10)
    assert "refused POST /v1/responses" in errors
    for word in ("Uvicorn running", "started server", "ready", "listening on"):
        assert word not in errors, (
            "the model loading monitor would take the gate's log for the engine's"
        )


# -- the host's side ------------------------------------------------------------------------------


def test_the_gate_container_serves_the_public_port_in_front_of_loopback():
    command = ModelGate.run_command(8000, "lmsysorg/sglang@sha256:abc")
    assert command[:3] == ["docker", "run", "-d"]
    assert command[command.index("--name") + 1] == "dreamference-gate-8000"
    assert command[command.index("--network") + 1] == "host"
    assert command[command.index("--restart") + 1] == "unless-stopped"
    assert f"{model_gate.GATE_DIR}:/mightling-gate:ro" in command
    assert command[command.index("--entrypoint") + 1] == "python3"
    assert command[command.index("--listen") + 1] == "0.0.0.0:8000"
    assert command[command.index("--upstream") + 1] == "127.0.0.1:18000"
    assert ModelGate.engine_port(60000) == 50000


def test_starting_the_gate_copies_the_service_beside_its_state(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    asked = []
    monkeypatch.setattr(
        ModelGate,
        "probe",
        classmethod(
            lambda cls, host, timeout=2.0: (
                asked.append(host) or {"gate": "mightling", "state": "open"}
            )
        ),
    )
    assert ModelGate.start(8000, "image")
    assert (model_gate.GATE_DIR / "model_gate_service.py").read_text() == SERVICE.read_text()
    for rules in ("model_gate_adapters.py", "model_gate_guard.py"):
        assert (model_gate.GATE_DIR / rules).read_text() == SERVICE.with_name(rules).read_text()
    assert calls[0] == ["docker", "rm", "-f", "dreamference-gate-8000"]
    assert calls[1][:3] == ["docker", "run", "-d"]
    assert asked == ["http://127.0.0.1:8000"]  # started is not enough: it must answer


def test_a_gate_that_never_answers_is_removed_so_the_engine_keeps_the_port(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, "OSError: [Errno 98] Address in use", "")
        ),
    )
    monkeypatch.setattr(model_gate, "START_WAIT_S", 0.05)
    monkeypatch.setattr(ModelGate, "sleep", staticmethod(lambda seconds: time.sleep(0.01)))
    assert not ModelGate.start(8000, "image")
    assert calls[-1] == ["docker", "rm", "-f", "dreamference-gate-8000"]
    assert (
        "did not answer on port 8000: OSError: [Errno 98] Address in use" in capsys.readouterr().out
    )


def test_pause_extends_from_its_start_and_resume_records_the_end():
    assert ModelGate.paused_until() is None
    first = ModelGate.pause(3600, now=1000.0)
    assert first == {"since": 1000.0, "until": 4600.0}
    extended = ModelGate.pause(7200, now=2000.0)
    assert extended == {"since": 1000.0, "until": 9200.0}  # one interval, not two
    assert ModelGate.resume(now=3000.0)
    assert ModelGate.pause_record() == {"since": 1000.0, "until": 3000.0}
    assert not ModelGate.resume(now=3500.0)
    assert ModelGate.pause(60, now=4000.0)["since"] == 4000.0  # a new pause after the old ended


def test_a_hold_removes_only_its_own_record():
    ModelGate.write_run(run_record(id="ours"))
    ModelGate.clear_run("someone-else")
    assert ModelGate.run_record()["id"] == "ours"
    ModelGate.clear_run("ours")
    assert ModelGate.run_record() is None
    assert ModelGate.state()["state"] == "open"


def test_status_says_when_no_gate_answers():
    lines = ModelGate.describe("http://localhost:8000")
    assert lines[0].startswith("Model gate: open")
    assert "No gate answers" in lines[1]


# -- `server start` puts the engine behind it -----------------------------------------------------


def test_behind_the_gate_the_engine_listens_on_loopback_at_the_internal_port():
    mgr = VLLMServerManager()
    gated = mgr.build_launch_command(model="qwen3.8-27b-nvfp4-dflash2", gate=True)
    plain = mgr.build_launch_command(model="qwen3.8-27b-nvfp4-dflash2")
    flag = lambda cmd, name: cmd[len(cmd) - 1 - cmd[::-1].index(name) + 1]
    assert (flag(gated, "--host"), flag(gated, "--port")) == ("127.0.0.1", "18000")
    assert (flag(plain, "--host"), flag(plain, "--port")) == ("0.0.0.0", "8000")
    # Every client and the watchdog address the container by the public port's name.
    assert flag(gated, "--name") == "dreamference-vllm-8000"


def _quiet_start(monkeypatch, mgr, gate_starts):
    from dreamference.hardware.hardware_telemetry import HardwareTelemetry
    from dreamference.vllm_server import psi_watchdog

    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda image: True)
    monkeypatch.setattr(mgr, "check_host_safety", lambda: None)
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(mgr, "_reset_stale_compile_cache", lambda *a, **k: None)
    monkeypatch.setattr("dreamference.hardware.download_model", lambda *a, **k: True)
    # The template is read from the checkpoint in the HuggingFace cache, which the test's home lacks.
    monkeypatch.setattr(
        "dreamference.vllm_server.chat_template_patcher.ChatTemplatePatcher.prepare",
        classmethod(lambda cls, *a: Path("/nonexistent/chat_template.jinja")),
    )
    events = []
    # The image's file comes out of docker, which a test may not run; the patch has its own tests.
    monkeypatch.setattr(
        "dreamference.vllm_server.sglang_runtime_patcher.SGLangRuntimePatcher.prepare",
        classmethod(lambda cls, image, patches: events.append(("patch", image, len(patches)))),
    )
    monkeypatch.setattr(
        "dreamference.hardware.hardware_manager.HardwareManager.detect_gb10_hardware",
        classmethod(
            lambda cls: HardwareTelemetry(
                is_gb10=True,
                gpu_name="NVIDIA GB10",
                driver_version="0",
                arch="aarch64",
                total_unified_memory_gb=128.0,
                available_memory_gb=128.0,
                used_memory_gb=0.0,
                vram_gb=0.0,
            )
        ),
    )
    built = []
    monkeypatch.setattr(
        mgr,
        "build_launch_command",
        lambda **kwargs: built.append(kwargs) or ["docker", "run", "image"],
    )
    monkeypatch.setattr(
        ModelGate,
        "start",
        classmethod(lambda cls, port, image: events.append(("gate", port)) or gate_starts),
    )
    monkeypatch.setattr(
        ModelGate, "remove", classmethod(lambda cls, port: events.append(("remove gate", port)))
    )
    monkeypatch.setattr(
        "subprocess.run", lambda command, **kwargs: events.append(tuple(command[:2]))
    )
    monkeypatch.setattr(psi_watchdog, "read_memory_pressure_full", lambda: None)
    return built, events


def test_server_start_starts_the_gate_before_the_engine(monkeypatch):
    mgr = VLLMServerManager()
    built, events = _quiet_start(monkeypatch, mgr, gate_starts=True)
    mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False)
    assert [kwargs["gate"] for kwargs in built] == [True]
    assert events.index(("gate", 8000)) < events.index(("docker", "run"))
    assert events.index(("docker", "rm")) < events.index(("gate", 8000)), (
        "the old engine lets go of the port first"
    )


def test_a_gate_that_cannot_start_leaves_the_engine_on_the_public_port(monkeypatch):
    mgr = VLLMServerManager()
    built, events = _quiet_start(monkeypatch, mgr, gate_starts=False)
    mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False)
    assert [kwargs["gate"] for kwargs in built] == [True, False]
    assert ("remove gate", 8000) in events


def test_server_start_prepares_the_recipes_runtime_patch_before_the_engine(monkeypatch):
    mgr = VLLMServerManager()
    _, events = _quiet_start(monkeypatch, mgr, gate_starts=True)
    mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False)
    image = get_model_launch_overrides("qwen3.8-27b-nvfp4-dflash2")["docker_image"]
    assert ("patch", image, 1) in events
    assert events.index(("patch", image, 1)) < events.index(("docker", "run"))


def test_a_runtime_patch_that_cannot_be_prepared_stops_the_start(monkeypatch):
    # Serving the image unpatched would bring back the split history (models-and-engines.md).
    mgr = VLLMServerManager()
    built, events = _quiet_start(monkeypatch, mgr, gate_starts=True)
    monkeypatch.setattr(
        "dreamference.vllm_server.sglang_runtime_patcher.SGLangRuntimePatcher.prepare",
        classmethod(lambda cls, image, patches: "1 anchor(s) of the patch to x.py no longer match"),
    )
    with pytest.raises(SystemExit) as stopped:
        mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False)
    assert built == [] and ("docker", "run") not in events
    assert "❌ The recipe's SGLang runtime patch could not be prepared" in str(stopped.value.code)
    assert "no longer match" in str(stopped.value.code)


def test_no_gate_removes_one_an_earlier_start_left(monkeypatch):
    mgr = VLLMServerManager()
    built, events = _quiet_start(monkeypatch, mgr, gate_starts=True)
    mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False, gate=False)
    assert [kwargs["gate"] for kwargs in built] == [False]
    assert ("gate", 8000) not in events and ("remove gate", 8000) in events


# -- `ling-admin night pause|resume` --------------------------------------------------------------


def test_night_pause_and_resume_from_the_command_line(capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    with pytest.raises(SystemExit) as paused:
        DreamferenceCLIController.run_cli(["night", "pause", "--for", "90m"])
    assert paused.value.code == 0
    until = ModelGate.paused_until()
    assert until is not None and 89 * 60 < until - time.time() <= 90 * 60
    assert "lets every request through until" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["night", "pause"])
    assert 59 * 60 < ModelGate.paused_until() - time.time() <= 60 * 60  # default 1 h, from now
    with pytest.raises(SystemExit) as resumed:
        DreamferenceCLIController.run_cli(["night", "resume"])
    assert resumed.value.code == 0 and ModelGate.paused_until() is None
    assert "Pause ended" in capsys.readouterr().out
    with pytest.raises(SystemExit) as bad:
        DreamferenceCLIController.run_cli(["night", "pause", "--for", "soon"])
    assert bad.value.code == 2


def test_ling_serve_starts_behind_the_gate_without_sglangs_runtime_patch(monkeypatch):
    mgr = VLLMServerManager()
    built, events = _quiet_start(monkeypatch, mgr, gate_starts=True)
    mgr.start_server(model="qwen3.8-27b-nvfp4-dflash2", background=False, engine="ling")
    assert not any(event[0] == "patch" for event in events)
    assert [kwargs["gate"] for kwargs in built] == [True]
    assert events.index(("gate", 8000)) < events.index(("docker", "run"))
