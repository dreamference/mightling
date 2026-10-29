"""The SWE-bench docker wrapper off the happy path: an instance only ever gets an internal network,
and what docker prints is read without trusting it. Every docker command is mocked.
"""

import json
import pathlib
import subprocess

import pytest

from dreamference.swe_bench.swe_bench_docker import SweBenchDocker

NETWORK = "mightling-swe"


def docker(monkeypatch, answers):
    """Answers each `docker <args>` with the first (prefix, rc, stdout) whose prefix it starts with."""
    calls = []

    def run(args, timeout=None, input_text=None):
        calls.append(args)
        for prefix, returncode, stdout in answers:
            if args[: len(prefix)] == prefix:
                return subprocess.CompletedProcess(["docker", *args], returncode, stdout, "")
        pytest.fail(f"unexpected docker {args}")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: run(*a, **k)))
    return calls


def described(internal, gateway="172.30.0.1"):
    return json.dumps([{"Internal": internal, "IPAM": {"Config": [{"Subnet": "172.30.0.0/16", "Gateway": gateway}]}}])


def test_a_network_that_is_not_internal_is_never_used(monkeypatch):
    docker(monkeypatch, [(["network", "inspect"], 0, described(False))])
    assert SweBenchDocker.ensure_network(NETWORK) is None


def test_a_missing_network_is_created_internal(monkeypatch):
    calls = docker(
        monkeypatch,
        [(["network", "inspect"], 1, ""), (["network", "create"], 0, "id\n")],
    )
    assert SweBenchDocker.ensure_network(NETWORK) is None, "still absent after the create"
    assert ["network", "create", "--internal", NETWORK] in calls


def test_a_network_that_cannot_be_created_gives_no_gateway(monkeypatch):
    docker(monkeypatch, [(["network", "inspect"], 1, ""), (["network", "create"], 1, "")])
    assert SweBenchDocker.ensure_network(NETWORK) is None


def test_an_internal_network_without_a_gateway_gives_none(monkeypatch):
    docker(monkeypatch, [(["network", "inspect"], 0, described(True, gateway=""))])
    assert SweBenchDocker.ensure_network(NETWORK) is None
    assert SweBenchDocker.subnet(NETWORK) == "172.30.0.0/16"


def test_an_internal_network_gives_its_gateway(monkeypatch):
    docker(monkeypatch, [(["network", "inspect"], 0, described(True))])
    assert SweBenchDocker.ensure_network(NETWORK) == "172.30.0.1"


@pytest.mark.parametrize("stdout", ["not json", "[]"])
def test_a_network_description_that_cannot_be_read_is_none(stdout, monkeypatch):
    docker(monkeypatch, [(["network", "inspect"], 0, stdout)])
    assert SweBenchDocker.network(NETWORK) is None
    assert SweBenchDocker.subnet(NETWORK) is None


def test_an_image_without_repository_digests_is_known_by_its_id(monkeypatch):
    docker(monkeypatch, [(["image", "inspect"], 0, "[] sha256:abc\n")])
    assert SweBenchDocker.image_digest("img") == "sha256:abc"


def test_an_image_whose_digests_cannot_be_read_is_known_by_its_id(monkeypatch):
    docker(monkeypatch, [(["image", "inspect"], 0, "garbled sha256:abc\n")])
    assert SweBenchDocker.image_digest("img") == "sha256:abc"


def test_an_image_that_cannot_be_pulled_has_no_digest(monkeypatch):
    calls = docker(monkeypatch, [(["image", "inspect"], 1, ""), (["pull"], 1, "")])
    assert SweBenchDocker.ensure_image("img") is None
    assert ["pull", "-q", "img"] in calls


@pytest.mark.parametrize(
    "returncode, stdout",
    [(1, ""), (0, "not json"), (0, "null"), (0, json.dumps(["HOME=/root", "LANG=C"]))],
)
def test_an_image_without_a_readable_path_has_none(returncode, stdout, monkeypatch):
    docker(monkeypatch, [(["image", "inspect"], returncode, stdout)])
    assert SweBenchDocker.image_path("img") is None


def test_an_image_path_is_read_from_its_environment(monkeypatch):
    docker(monkeypatch, [(["image", "inspect"], 0, json.dumps(["HOME=/root", "PATH=/opt/conda/bin:/usr/bin"]))])
    assert SweBenchDocker.image_path("img") == "/opt/conda/bin:/usr/bin"


def test_a_docker_command_that_times_out_or_cannot_start_reads_as_a_failure(monkeypatch):
    def timed_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="partial")

    monkeypatch.setattr(subprocess, "run", timed_out)
    result = SweBenchDocker.run(["ps"], timeout=1)
    assert (result.returncode, result.stdout, result.stderr) == (124, "partial", "timed out")

    def missing(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing)
    assert SweBenchDocker.run(["ps"]).returncode == 127


def test_a_docker_command_without_input_reads_nothing_from_the_terminal(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    SweBenchDocker.run(["ps"])
    assert seen["argv"] == ["docker", "ps"] and seen["stdin"] is subprocess.DEVNULL
    SweBenchDocker.run(["exec", "-i", "c", "sh"], input_text="echo")
    assert seen["stdin"] is None and seen["input"] == "echo"


def test_the_agents_docker_exec_starts_in_its_own_session(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: seen.update(kwargs, argv=argv))
    with pathlib.Path(tmp_path / "out").open("w") as out:
        SweBenchDocker.popen(["exec", "c", "true"], out)
    assert seen["argv"] == ["docker", "exec", "c", "true"]
    assert seen["start_new_session"] and seen["stdin"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.STDOUT
