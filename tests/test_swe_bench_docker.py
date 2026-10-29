"""`SweBenchDocker`, the one place the benchmark runs `docker`, and `SweBenchRuntime`, the copy of
`ling` mounted into every instance container.

`subprocess.run` is replaced in each test (or `SweBenchDocker.run`, for the methods built on it),
so nothing here runs `docker`, `ldd` or `patchelf`.
"""

import subprocess

import pytest

from dreamference.swe_bench import SweBenchDocker, SweBenchRuntime, swe_bench_docker


def fake_run(monkeypatch, outcome):
    """Makes `subprocess.run` in the docker module raise or return `outcome`; returns its calls."""
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(swe_bench_docker.subprocess, "run", run)
    return calls


def test_a_timed_out_docker_command_returns_its_partial_output_as_text(monkeypatch):
    # On POSIX `TimeoutExpired.stdout` is bytes even in text mode, so a timed-out `docker exec`
    # that had printed something handed bytes to callers that join it with text: a scrub script
    # that timed out became "runner error: can't concat str to bytes" instead of its 124.
    fake_run(monkeypatch, subprocess.TimeoutExpired(["docker"], 5, output=b"note: partial\n"))

    result = SweBenchDocker.run(["exec", "c", "bash", "-c", "true"], timeout=5)

    assert result.returncode == 124
    assert result.stdout == "note: partial\n"
    assert result.stderr == "timed out"
    assert "tree: equal" not in result.stdout  # what the instance run asks of it, without a TypeError


def test_a_timed_out_docker_command_with_no_output_returns_empty_text(monkeypatch):
    fake_run(monkeypatch, subprocess.TimeoutExpired(["docker"], 5))

    result = SweBenchDocker.run(["ps"], timeout=5)

    assert (result.returncode, result.stdout) == (124, "")


def test_a_missing_docker_is_return_code_127(monkeypatch):
    fake_run(monkeypatch, FileNotFoundError("docker"))

    result = SweBenchDocker.run(["ps"])

    assert result.returncode == 127 and "docker" in result.stderr


def test_standard_input_is_closed_unless_text_is_given(monkeypatch):
    calls = fake_run(monkeypatch, subprocess.CompletedProcess(["docker"], 0, "", ""))

    SweBenchDocker.run(["ps"])
    SweBenchDocker.run(["load"], input_text="data")

    (first, quiet), (_, fed) = calls
    assert first == ["docker", "ps"]
    assert quiet["stdin"] is subprocess.DEVNULL and quiet["input"] is None
    assert fed["stdin"] is None and fed["input"] == "data"
