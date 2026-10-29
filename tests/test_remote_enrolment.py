"""Enrolment by code: the listener's refusals and the answer's checks, on loopback ports only.

The listener binds an ephemeral loopback port of the test's own; nothing else is reached.
"""

import json
import socket
import threading
import time

import pytest

from dreamference.remote import RemoteEnrolment
from dreamference.remote import remote_enrolment as module

CODE = "12345678"
NONCE = "a" * 32


def _good(client="laptop", nonce=NONCE):
    return {"nonce": nonce, "proof": RemoteEnrolment.client_proof(CODE, nonce), "client": client}


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Listener:
    def __init__(self, make_bundle=lambda client: {"setup_key": "K"}):
        self.port = _free_port()
        self.minted = []
        self.result = {}

        def mint(client):
            self.minted.append(client)
            return make_bundle(client)

        self.thread = threading.Thread(
            target=lambda: self.result.update(
                outcome=RemoteEnrolment.serve(
                    CODE, mint, port=self.port, minutes=1, host="127.0.0.1"
                )
            )
        )
        self.thread.start()
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    def raw(self, head, body=b"", path="/mightling/enrol", timeout=5):
        """One request with headers exactly as given; returns (status, parsed JSON)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(f"POST {path} HTTP/1.1\r\nHost: x\r\n{head}\r\n".encode() + body)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        status = int(data.split(b" ", 2)[1])
        return status, json.loads(data.split(b"\r\n\r\n", 1)[1] or b"{}")

    def post(self, request, **kwargs):
        body = json.dumps(request).encode()
        return self.raw(f"Content-Length: {len(body)}\r\n", body, **kwargs)

    def finish(self):
        """Ends the listener by spending the code (or the attempts)."""
        if self.thread.is_alive():
            for _ in range(10):
                if not self.thread.is_alive():
                    break
                try:
                    self.post({"nonce": "1" * 32, "proof": "0" * 64})
                except OSError:
                    break
        self.thread.join(10)
        assert not self.thread.is_alive()
        return self.result["outcome"]


# -- the answer


@pytest.mark.parametrize(
    "request_",
    [
        {},
        {"nonce": 5, "proof": "x"},
        {"nonce": NONCE, "proof": ["x"]},
        {"nonce": "a" * 31, "proof": "x"},
        {"nonce": "a" * 33, "proof": "x"},
        {"nonce": "g" * 32, "proof": "x"},
        {"nonce": "A" * 32, "proof": "x"},
    ],
)
def test_a_malformed_enrolment_request_is_refused_not_raised(request_):
    assert RemoteEnrolment.answer(CODE, request_, lambda c: pytest.fail("minted")) is None


def test_a_proof_in_capitals_is_accepted_and_a_long_client_name_is_cut():
    request = _good("x" * 100)
    request["proof"] = request["proof"].upper()
    seen = []
    answer = RemoteEnrolment.answer(CODE, request, lambda c: seen.append(c) or {"k": 1})
    assert answer is not None and seen == ["x" * 63]


def test_a_missing_or_odd_client_name_becomes_client():
    seen = []
    for name in (None, "", 7):
        request = _good()
        request["client"] = name
        RemoteEnrolment.answer(CODE, request, lambda c: seen.append(c) or {"k": 1})
    assert seen == ["client", "client", "7"]


def test_a_node_that_cannot_mint_a_key_answers_with_an_error_and_no_bundle():
    answer = RemoteEnrolment.answer(CODE, _good(), lambda c: None)
    assert answer == {"error": "the node could not mint a setup key"}


def test_a_failed_mint_is_a_500_that_is_not_an_enrolment():
    state = {"failures": 0, "outcome": None}
    status, body = RemoteEnrolment.respond(CODE, _good(), lambda c: None, state)
    assert status == 500 and "bundle" not in body
    assert state == {"failures": 0, "outcome": None}, "the code is still good for a retry"


def test_wrong_proofs_count_down_and_the_tenth_withdraws_the_code():
    state = {"failures": 0, "outcome": None}
    bad = {"nonce": NONCE, "proof": "0" * 64}
    lefts = []
    for _ in range(10):
        status, body = RemoteEnrolment.respond(CODE, bad, lambda c: pytest.fail("minted"), state)
        assert status == 401
        lefts.append(body["attempts_left"])
    assert lefts == list(range(9, -1, -1)) and state["outcome"] == "withdrawn"


def test_a_proof_with_letters_outside_ascii_is_one_wrong_attempt_not_a_crash():
    state = {"failures": 0, "outcome": None}
    bad = {"nonce": NONCE, "proof": "é" * 64}
    status, body = RemoteEnrolment.respond(CODE, bad, lambda c: pytest.fail("minted"), state)
    assert status == 401 and body["attempts_left"] == 9


# -- the listener


def test_a_request_for_another_path_is_a_404_and_does_not_count_against_the_code():
    listener = _Listener()
    assert listener.post(_good(), path="/other")[0] == 404
    assert listener.minted == []
    assert listener.post({"nonce": "1" * 32, "proof": "0" * 64})[1]["attempts_left"] == 9
    assert listener.post(_good())[0] == 200
    assert listener.finish() == "enrolled:laptop"


def test_a_request_from_outside_the_lan_is_a_403_and_mints_nothing(monkeypatch):
    monkeypatch.setattr(RemoteEnrolment, "lan_address", classmethod(lambda cls, address: False))
    listener = _Listener()
    status, body = listener.post(_good())
    assert status == 403 and "local network" in body["error"] and listener.minted == []
    monkeypatch.setattr(RemoteEnrolment, "lan_address", classmethod(lambda cls, address: True))
    assert listener.post(_good())[0] == 200
    assert listener.finish() == "enrolled:laptop"


@pytest.mark.parametrize(
    "head, body",
    [
        ("Content-Length: 9\r\n", b"not json!!"),
        ("Content-Length: 5\r\n", b'["x"]'),
        ("Content-Length: banana\r\n", b"{}"),
        ("", b""),
    ],
)
def test_a_body_that_is_not_a_json_object_is_one_wrong_attempt_not_a_crash(head, body):
    listener = _Listener()
    status, answer = listener.raw(head, body)
    assert status == 401 and answer["attempts_left"] == 9
    assert listener.post(_good())[0] == 200, "the listener is still serving"
    assert listener.finish() == "enrolled:laptop"


def test_a_negative_content_length_does_not_lift_the_body_limit():
    listener = _Listener()
    body = json.dumps(_good()).encode().rjust(module.MAX_BODY + 500, b" ")
    status, _ = listener.raw("Content-Length: -1\r\n", body)
    assert status == 401 and listener.minted == [], "a body past the cap is not read"
    assert listener.post(_good())[0] == 200
    assert listener.finish() == "enrolled:laptop"


def test_a_body_past_the_limit_is_cut_not_read_whole():
    listener = _Listener()
    body = json.dumps(_good()).encode().rjust(module.MAX_BODY + 500, b" ")
    status, _ = listener.raw(f"Content-Length: {len(body)}\r\n", body)
    assert status == 401 and listener.minted == []
    assert listener.post(_good())[0] == 200
    assert listener.finish() == "enrolled:laptop"


def test_a_client_that_connects_and_says_nothing_cannot_hold_the_listener(monkeypatch):
    monkeypatch.setattr(module, "REQUEST_TIMEOUT_S", 0.5)
    listener = _Listener()
    idle = socket.create_connection(("127.0.0.1", listener.port), timeout=5)
    try:
        status, _ = listener.post(_good(), timeout=5)
        assert status == 200
    finally:
        idle.close()
    assert listener.finish() == "enrolled:laptop"


def test_an_unused_code_expires(monkeypatch):
    clock = iter([0.0, 0.0, 61.0, 61.0, 61.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    assert (
        RemoteEnrolment.serve(CODE, lambda c: {}, port=_free_port(), minutes=1, host="127.0.0.1")
        == "expired"
    )
