"""What the diffusion sidecar manager decides from Docker and the sidecar's health endpoint.

A `docker run` that fails is a failed start, with Docker's reason shown; a sidecar is healthy
only when /health answers 200; its load error is reported as the sidecar wrote it; and the stop
and remove commands reach only the container on the port asked for.
"""

import subprocess

import pytest
import requests

from dreamference.hardware.model_matrix_registry import (
    DEFAULT_DIFFUSION_MODEL_ALIAS,
    DEFAULT_MODEL_ALIAS,
)
from dreamference.vllm_server import DiffusionServerManager
from dreamference.vllm_server import diffusion_server_manager as dsm


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def _docker(monkeypatch, run_rc=0, stderr=""):
    runs = []

    def run(cmd, **kwargs):
        runs.append(cmd)
        rc = run_rc if cmd[:2] == ["docker", "run"] else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=stderr)

    monkeypatch.setattr(dsm.subprocess, "run", run)
    return runs


def _health(monkeypatch, answer):
    asked = []

    def get(url, timeout):
        asked.append(url)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", get)
    return asked


def test_a_docker_run_that_fails_is_a_failed_start_with_dockers_reason_shown(monkeypatch, capsys):
    runs = _docker(monkeypatch, run_rc=125, stderr="  port is already allocated\n")
    mgr = DiffusionServerManager()
    assert mgr.start_server(DEFAULT_DIFFUSION_MODEL_ALIAS, DEFAULT_MODEL_ALIAS) is False
    assert runs[0] == ["docker", "rm", "-f", "dreamference-diffusion-8001"]
    out = capsys.readouterr().out
    assert "failed to start: port is already allocated" in out and "started" not in out


def test_a_docker_run_that_succeeds_is_a_start(monkeypatch, capsys):
    _docker(monkeypatch)
    mgr = DiffusionServerManager()
    assert mgr.start_server(DEFAULT_DIFFUSION_MODEL_ALIAS, DEFAULT_MODEL_ALIAS) is True
    assert "'dreamference-diffusion-8001' started" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("answer", "healthy"),
    [
        (_Response(200), True),
        (_Response(503), False),
        (requests.ConnectionError("refused"), False),
    ],
)
def test_the_sidecar_is_healthy_only_when_its_health_answers_200(monkeypatch, answer, healthy):
    asked = _health(monkeypatch, answer)
    assert DiffusionServerManager("http://localhost:8001/").check_health() is healthy
    assert asked == ["http://localhost:8001/health"]


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        ({"status": "ok"}, "ok"),
        ({"status": "loading"}, "loading"),
        ({"status": "error", "error": "CUDA out of memory"}, "CUDA out of memory"),
        ({"status": "error"}, "error"),
        ({"status": "error", "error": ""}, "error"),
        ({}, None),
        (ValueError("not json"), None),
    ],
)
def test_the_load_state_is_the_sidecars_own_words(monkeypatch, payload, state):
    _health(monkeypatch, _Response(503, payload))
    assert DiffusionServerManager().get_load_state() == state


def test_an_unreachable_sidecar_has_no_load_state(monkeypatch):
    _health(monkeypatch, requests.Timeout("slow"))
    assert DiffusionServerManager().get_load_state() is None


def test_stop_and_remove_reach_only_the_container_on_the_port_asked_for(monkeypatch):
    runs = _docker(monkeypatch)
    mgr = DiffusionServerManager()
    mgr.stop_server(port=8011)
    mgr.remove_server(port=8012)
    DiffusionServerManager.remove_leftover(port=8013)
    assert runs == [
        ["docker", "stop", "dreamference-diffusion-8011"],
        ["docker", "rm", "-f", "dreamference-diffusion-8012"],
        ["docker", "rm", "-f", "dreamference-diffusion-8013"],
    ]
