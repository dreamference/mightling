"""The loop guard in the built `ling` (docs/dev/loop-guard.md), against a stand-in model server.

`ling exec` runs for real, with a throwaway HOME and CODEX_HOME, against an HTTP server on a free
loopback port that answers `/v1/models` and streams `/v1/responses` the way SGLang does. Each test
scripts what the "model" does: loop in its reasoning until the client hangs up, end a response as
incomplete with a tool call cut short, or answer. What is checked is what Codex itself did with the
guard's events: that it went on with the turn after a nudge (a second request carrying the nudge
and the reasoning cut before its loop), that the looping request was dropped (the server saw the
client go), that a cut tool call never ran and an incomplete response was not retried, and that a
second stop in a row ended the turn with the plain message.

The tests skip unless the binary reads the guard's variables: `MIGHTLING_BIN` points them at a
build that is not installed yet (as for test_mightling_slash_commands.py). No model server is used.
"""

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
from dreamference.swe_bench.swe_bench_runner import SweBenchRunner

MIGHTLING = os.environ.get("MIGHTLING_BIN") or CodexBrandedBuilder.executable_path()
MODEL = "stand-in/loop-model"
LOOP_NUDGE_START = "You were repeating yourself"
LENGTH_NUDGE_START = "Your last output reached its length limit"
ENDED_START = "I stopped this answer because I kept repeating myself"
LOOP_BLOCK = (
    "Wait, maybe the cache is stale. Let me check the cache again and see whether the entry was written "
    "before the request, because if it was not then the second lookup would miss as well. "
)


def binary_has_the_guard() -> bool:
    """Whether the binary names the guard's switch, as `swe-bench run` checks an arm's."""
    return SweBenchRunner.ling_reads(MIGHTLING, "DREAMFERENCE_MIGHTLING_LOOP_GUARD")


pytestmark = [
    pytest.mark.installed_binary,
    pytest.mark.skipif(not binary_has_the_guard(), reason=f"{MIGHTLING} has no loop guard"),
]


def event(kind: str, **fields) -> bytes:
    return f"data: {json.dumps({'type': kind, **fields})}\n\n".encode()


def completed(rid: str) -> bytes:
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    return event("response.completed", response={"id": rid, "status": "completed", "usage": usage})


class StandIn(BaseHTTPRequestHandler):
    """A model server that loops, cuts or answers, as `script` says for each request in turn."""

    script: ClassVar[list[str]] = []
    bodies: ClassVar[list[dict]] = []
    hung_up: ClassVar[list[int]] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps(
            {
                "object": "list",
                "data": [
                    {"id": MODEL, "object": "model", "owned_by": "sglang", "max_model_len": 65536}
                ],
            }
        ).encode()
        self.send_response(200 if self.path.startswith("/v1/models") else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        StandIn.bodies.append(body)
        kind = StandIn.script[min(len(StandIn.bodies), len(StandIn.script)) - 1]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        rid = f"resp_{len(StandIn.bodies)}"
        try:
            self.wfile.write(event("response.created", response={"id": rid}))
            getattr(self, kind)(rid)
        except (BrokenPipeError, ConnectionResetError):
            StandIn.hung_up.append(len(StandIn.bodies))

    def loop(self, rid):
        self.wfile.write(
            event(
                "response.output_item.added",
                output_index=0,
                item={
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [],
                    "content": [],
                    "status": "in_progress",
                },
            )
        )
        self.wfile.write(
            event(
                "response.reasoning_text.delta",
                item_id="rs_1",
                output_index=0,
                content_index=0,
                delta="The user wants a word. ",
            )
        )
        # Far more than the detector needs; it ends when the client hangs up.
        for _ in range(3000):
            for start in range(0, len(LOOP_BLOCK), 12):
                self.wfile.write(
                    event(
                        "response.reasoning_text.delta",
                        item_id="rs_1",
                        output_index=0,
                        content_index=0,
                        delta=LOOP_BLOCK[start : start + 12],
                    )
                )
            self.wfile.flush()
            time.sleep(0.001)
        self.wfile.write(completed(rid))

    def cut(self, rid):
        # What SGLang sends when max_output_tokens ends a tool call: the call closed with what it
        # had, then `response.incomplete`.
        item = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "touch RAN && echo " + "x" * 50})[:40],
            "status": "completed",
        }
        self.wfile.write(
            event("response.output_item.added", output_index=0, item=dict(item, arguments=""))
        )
        self.wfile.write(event("response.output_item.done", output_index=0, item=item))
        self.wfile.write(
            event(
                "response.incomplete",
                response={
                    "id": rid,
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            )
        )
        self.wfile.flush()

    def answer(self, rid):
        item = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        }
        self.wfile.write(event("response.output_item.added", output_index=0, item=item))
        self.wfile.write(
            event(
                "response.output_text.delta",
                item_id="msg_1",
                output_index=0,
                content_index=0,
                delta="pong",
            )
        )
        done = dict(
            item,
            status="completed",
            content=[{"type": "output_text", "text": "pong", "annotations": []}],
        )
        self.wfile.write(event("response.output_item.done", output_index=0, item=done))
        self.wfile.write(completed(rid))
        self.wfile.flush()


@pytest.fixture
def server():
    StandIn.bodies, StandIn.hung_up = [], []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    yield httpd
    httpd.shutdown()


def run_ling(server, tmp_path, env_extra=None) -> tuple[subprocess.CompletedProcess, str]:
    home = tmp_path / "home"
    work = tmp_path / "work"
    for d in (home / ".mightling", work):
        d.mkdir(parents=True, exist_ok=True)
    last = tmp_path / "last.txt"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "CODEX_HOME": str(home / ".mightling"),
        "DREAMFERENCE_VLLM_HOST": f"http://127.0.0.1:{server.server_address[1]}",
        "DREAMFERENCE_CONFIG_PATH": str(tmp_path / "none.toml"),
        "DREAMFERENCE_MIGHTLING_GMAIL": "false",
        "DREAMFERENCE_MIGHTLING_LOOP_GUARD": "on",
        "DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET": "4096",
        "NO_COLOR": "1",
        **(env_extra or {}),
    }
    result = subprocess.run(
        [
            MIGHTLING,
            "exec",
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "--output-last-message",
            str(last),
            "Reply with one word: pong",
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
    )
    text = last.read_text() if last.exists() else ""
    return result, text


def texts_of(body: dict) -> str:
    return json.dumps(body.get("input"))


def test_a_looping_reasoning_is_cut_and_the_turn_goes_on_to_an_answer(server, tmp_path):
    StandIn.script = ["loop", "answer"]
    result, last = run_ling(server, tmp_path)
    assert result.returncode == 0, result.stderr[-3000:]
    assert last.strip() == "pong"
    assert len(StandIn.bodies) == 2
    assert 1 in StandIn.hung_up, "the looping request was dropped, so the server stopped generating"
    first, second = StandIn.bodies
    metadata = first.get("client_metadata") or {}
    assert (
        metadata.get("ling_surface") == "exec" and metadata.get("ling_max_output_tokens") == "4096"
    )
    sent = texts_of(second)
    assert LOOP_NUDGE_START in sent
    assert "The user wants a word." in sent and sent.count("Wait, maybe the cache is stale") == 1
    events = (
        (tmp_path / "home" / ".mightling" / "loop-guard" / "events.jsonl").read_text().splitlines()
    )
    assert [json.loads(line)["action"] for line in events] == ["nudged"]


def test_an_incomplete_response_is_not_retried_and_its_cut_tool_call_never_runs(server, tmp_path):
    StandIn.script = ["cut", "answer"]
    result, last = run_ling(server, tmp_path)
    assert result.returncode == 0, result.stderr[-3000:]
    assert last.strip() == "pong"
    assert len(StandIn.bodies) == 2, "Codex would have retried the incomplete response five times"
    assert LENGTH_NUDGE_START in texts_of(StandIn.bodies[1])
    assert "call_1" not in texts_of(StandIn.bodies[1])
    assert not (tmp_path / "work" / "RAN").exists()


def test_a_second_stop_in_a_row_ends_the_turn_with_the_plain_message(server, tmp_path):
    StandIn.script = ["loop", "loop", "answer"]
    result, last = run_ling(server, tmp_path)
    assert result.returncode == 0, result.stderr[-3000:]
    assert last.startswith(ENDED_START)
    assert len(StandIn.bodies) == 2


def test_with_nothing_switched_on_exec_sends_no_loop_guard_metadata(server, tmp_path):
    StandIn.script = ["answer"]
    result, last = run_ling(
        server,
        tmp_path,
        {"DREAMFERENCE_MIGHTLING_LOOP_GUARD": "", "DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET": ""},
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert last.strip() == "pong"
    metadata = StandIn.bodies[0].get("client_metadata") or {}
    assert not [key for key in metadata if key.startswith("ling_")]
    assert "max_output_tokens" not in StandIn.bodies[0]
