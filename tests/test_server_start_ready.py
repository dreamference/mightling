"""`ling-admin server start` once the model answers, and the `server` commands around it.

The failure exits are tested in test_cli_entry_points.py; these cover what a start that succeeds,
or is interrupted, leaves behind: the node's advertised state, the NVFP4 canary's verdict and
the warm-up warning. Nothing here reaches Docker, a model server or the network.
"""

import subprocess

import pytest

from dreamference.chat.google_service import GoogleService
from dreamference.chat.image_search_sidecar import ImageSearchSidecar
from dreamference.chat.speech_sidecar import SpeechSidecar
from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.node import NodeAdvertiser
from dreamference.vllm_server import DiffusionServerManager, VLLMServerManager


class Monitor:
    """A loading monitor that reports ready after `polls` status reads."""

    def __init__(self, polls=0):
        self.polls = polls
        self.fatal = None
        self.stopped = False

    @property
    def server_ready(self):
        return self.polls <= 0

    def get_status(self):
        self.polls -= 1
        return {"stages_reached": ["weights"], "elapsed_seconds": 12.0}

    def print_progress(self):
        pass

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class Process:
    def __init__(self, wait_times_out=False):
        self.wait_times_out = wait_times_out
        self.terminated = self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.wait_times_out:
            raise subprocess.TimeoutExpired("docker", timeout)
        return 0

    def kill(self):
        self.killed = True


class Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self._text = text

    def json(self):
        return {"choices": [{"text": self._text}]}


@pytest.fixture
def start(monkeypatch):
    """Stubs every outside effect of `server start`; returns what the start did."""
    seen = {"node": [], "fallback": [], "posts": [], "monitor": Monitor(), "warm_up": 1.5}
    for sidecar in (GoogleService, SpeechSidecar, ImageSearchSidecar):
        monkeypatch.setattr(sidecar, "ensure_on_node", classmethod(lambda cls, *a, **k: None))
    for event in ("on_server_starting", "on_server_ready", "on_server_stopped"):
        monkeypatch.setattr(
            NodeAdvertiser,
            event,
            classmethod(lambda cls, *a, _e=event, **k: seen["node"].append(_e)),
        )
    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: seen["monitor"])
    monkeypatch.setattr(VLLMServerManager, "start_server", lambda self, **_: None)
    monkeypatch.setattr(VLLMServerManager, "warm_up", lambda self: seen["warm_up"])
    monkeypatch.setattr(
        DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None)
    )
    monkeypatch.setattr(
        DreamferenceCLIController, "_run_tool_call_canary", classmethod(lambda cls, *a, **k: True)
    )
    monkeypatch.setattr(
        DreamferenceCLIController,
        "_suggest_engine_fallback",
        classmethod(lambda cls, model, engine, what: seen["fallback"].append(what)),
    )
    monkeypatch.setattr("dreamference.hardware.resolve_model_hf_repo", lambda alias: "served/model")

    def post(url, json=None, timeout=None):
        seen["posts"].append(url)
        reply = seen.get("reply", Response(200, "Hello there"))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    return seen


NVFP4 = ["server", "start", "--no-diffusion", "--model", "qwen3.8-27b-nvfp4-dflash2"]


def test_a_ready_server_is_advertised_ready_and_never_stopped(start, monkeypatch, capsys):
    # The status loop runs until the monitor sees the server answer; the advertiser then says
    # `ready`, and the finally block must not take that back with `stopped`.
    def running(self, **_):
        self.process = Process()

    start["monitor"] = Monitor(polls=2)
    monkeypatch.setattr(VLLMServerManager, "start_server", running)
    DreamferenceCLIController.run_cli(NVFP4)
    assert start["node"] == ["on_server_starting", "on_server_ready"]
    assert start["monitor"].stopped
    out = capsys.readouterr().out
    assert "Server Ready!" in out and "Warm-up done in 1.5s" in out
    assert start["fallback"] == []


def test_a_failed_warm_up_is_a_warning_not_a_failure(start, capsys):
    start["warm_up"] = None
    DreamferenceCLIController.run_cli(NVFP4)
    out = capsys.readouterr().out
    assert "Warm-up request failed" in out and "Server Ready!" in out
    assert start["node"][-1] == "on_server_ready"


@pytest.mark.parametrize(
    "reply, suggested",
    [
        (Response(200, "Hello there"), False),
        (Response(200, "! ! !!"), True),
        (Response(503), True),
        (OSError("connection refused"), True),
    ],
    ids=["healthy", "all-bangs", "http-error", "unreachable"],
)
def test_the_nvfp4_canary_suggests_another_engine_unless_the_output_is_healthy(
    start, reply, suggested
):
    # On SM12x the wrong kernel backend emits only '!' rather than failing; that output, an error
    # reply or no reply at all each send the user to the fallback engine.
    start["reply"] = reply
    DreamferenceCLIController.run_cli(NVFP4)
    assert start["posts"] == ["http://localhost:8000/v1/completions"]
    assert start["fallback"] == (["the NVFP4 canary"] if suggested else [])


def test_a_model_that_is_not_nvfp4_skips_the_canary(start, monkeypatch):
    monkeypatch.setattr(
        DreamferenceCLIController, "_server_engine", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(
        DreamferenceCLIController,
        "_engine_for_adapters",
        classmethod(lambda cls, model, engine, **k: engine),
    )
    monkeypatch.setattr(
        controller.ModelMatrixRegistry, "removed_in", classmethod(lambda cls, m: False)
    )
    monkeypatch.setattr("dreamference.hardware.get_model_launch_overrides", lambda *a, **k: {})
    DreamferenceCLIController.run_cli(
        ["server", "start", "--no-diffusion", "--model", "some/int4-model"]
    )
    assert start["posts"] == [] and start["fallback"] == []
    assert start["node"][-1] == "on_server_ready"


@pytest.mark.parametrize("wait_times_out", [False, True])
def test_ctrl_c_during_the_load_stops_the_container_and_the_advertised_loading(
    start, monkeypatch, capsys, wait_times_out
):
    # The container is terminated, killed when it does not go within five seconds, and the
    # node stops saying `loading` so clients do not wait for a model that is not coming.
    process = Process(wait_times_out)

    def interrupted(self, **_):
        self.process = process
        raise KeyboardInterrupt

    start["monitor"] = Monitor(polls=99)
    monkeypatch.setattr(VLLMServerManager, "start_server", interrupted)
    DreamferenceCLIController.run_cli(NVFP4)
    assert process.terminated and process.killed == wait_times_out
    assert start["node"] == ["on_server_starting", "on_server_stopped"]
    assert "Shutting down server" in capsys.readouterr().out


def test_ctrl_c_before_the_container_ran_still_clears_loading(start, monkeypatch):
    def interrupted(self, **_):
        raise KeyboardInterrupt

    start["monitor"] = Monitor(polls=99)
    monkeypatch.setattr(VLLMServerManager, "start_server", interrupted)
    DreamferenceCLIController.run_cli(NVFP4)
    assert start["node"] == ["on_server_starting", "on_server_stopped"]


def test_a_google_service_that_fails_on_a_node_never_blocks_the_model(start, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(GoogleService, "ensure_on_node", classmethod(lambda cls, *a, **k: False))
    monkeypatch.setattr(
        GoogleService, "problem", "The Google service did not start.", raising=False
    )
    monkeypatch.setattr(
        VLLMServerManager, "start_server", lambda self, **k: started.append(k["model"])
    )
    DreamferenceCLIController.run_cli(NVFP4)
    assert started == ["qwen3.8-27b-nvfp4-dflash2"]
    assert "The Google service did not start." in capsys.readouterr().out


def test_server_remove_removes_the_container_and_the_diffusion_leftover(monkeypatch):
    calls = []
    monkeypatch.setattr(
        VLLMServerManager, "remove_server", lambda self, port=8000: calls.append(("remove", port))
    )
    monkeypatch.setattr(
        NodeAdvertiser, "on_server_stopped", classmethod(lambda cls: calls.append(("node",)))
    )
    monkeypatch.setattr(
        DiffusionServerManager,
        "remove_leftover",
        classmethod(lambda cls, port=8001: calls.append(("leftover", port))),
    )
    monkeypatch.setattr(
        controller.ModelMatrixRegistry, "diffusion_enabled", classmethod(lambda cls: False)
    )
    DreamferenceCLIController.run_cli(
        ["server", "remove", "--port", "8100", "--diffusion-port", "8101"]
    )
    assert calls == [("remove", 8100), ("node",), ("leftover", 8101)]


@pytest.mark.parametrize("argv", [["server", "logs"], ["logs"]])
def test_ctrl_c_while_tailing_logs_ends_quietly(monkeypatch, capsys, argv):
    def tail(self, port=8000):
        raise KeyboardInterrupt

    monkeypatch.setattr(VLLMServerManager, "show_request_logs", tail)
    DreamferenceCLIController.run_cli(argv)
    assert "Stopped tailing logs." in capsys.readouterr().out
