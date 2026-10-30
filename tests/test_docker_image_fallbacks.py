"""Where `ensure_docker_image`, `probe_image` and `is_docker_available` fall back when docker does not oblige."""
import subprocess
from pathlib import Path

import pytest

from dreamference.vllm_server import VLLMServerManager, vllm_server_manager
from dreamference.vllm_server.vllm_server_manager import DEFAULT_VLLM_IMAGE

REAL_IS_FILE = Path.is_file


@pytest.fixture
def docker(monkeypatch):
    """`subprocess.run` records each docker command; `state["present"]` is what `docker image inspect` finds."""
    mgr = VLLMServerManager()
    state = {"present": False, "appears_after": None, "commands": []}

    def run(argv, **kwargs):
        state["commands"].append(argv)
        if argv[:2] == ["docker", state["appears_after"]]:
            state["present"] = True
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mgr, "is_image_present", lambda image: state["present"])
    monkeypatch.setattr(vllm_server_manager.subprocess, "run", run)
    return mgr, state


def _dockerfile(monkeypatch, present):
    monkeypatch.setattr(Path, "is_file",
                        lambda self: present if self.name == "Dockerfile" else REAL_IS_FILE(self))


# -- ensure_docker_image -------------------------------------------------------------------------------


def test_a_registry_image_that_the_pull_brings_is_ready(docker):
    mgr, state = docker
    state["appears_after"] = "pull"

    assert mgr.ensure_docker_image("registry.example/engine:1") is True
    assert state["commands"] == [["docker", "pull", "registry.example/engine:1"]]


def test_a_release_install_without_the_dockerfile_builds_nothing(docker, monkeypatch, capsys):
    mgr, state = docker
    _dockerfile(monkeypatch, present=False)

    assert mgr.ensure_docker_image(DEFAULT_VLLM_IMAGE) is False
    assert state["commands"] == []
    assert "which a release install does not have" in capsys.readouterr().out


def test_a_build_that_leaves_no_image_behind_is_a_failure(docker, monkeypatch, capsys):
    mgr, state = docker
    _dockerfile(monkeypatch, present=True)

    assert mgr.ensure_docker_image(DEFAULT_VLLM_IMAGE) is False
    assert [argv[:2] for argv in state["commands"]] == [["docker", "build"]]
    assert "is missing locally and could not be built" in capsys.readouterr().out


# -- probe_image ---------------------------------------------------------------------------------------


@pytest.fixture
def probe(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(VLLMServerManager, "_image_probe_cache", {})
    monkeypatch.setattr(mgr, "is_image_present", lambda image: True)
    runs = []

    def answer(code, stdout):
        def run(argv, **kwargs):
            runs.append(argv)
            return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")
        monkeypatch.setattr(vllm_server_manager.subprocess, "run", run)

    return mgr, runs, answer


def test_a_probe_container_that_fails_is_not_read_and_its_unknown_is_kept(probe):
    mgr, runs, answer = probe
    answer(1, "vllm=0.12.1\ntensorizer=True\n")

    assert mgr.probe_image("img:1") == {"vllm_version": None, "has_tensorizer": False}
    assert mgr.probe_image("img:1") == {"vllm_version": None, "has_tensorizer": False}
    assert len(runs) == 1


def test_lines_the_probe_did_not_write_are_passed_over(probe):
    mgr, runs, answer = probe
    answer(0, "WARNING: the requested image's platform\nvllm=0.11.0rc1+cu129\ntensorizer=True\n")

    assert mgr.probe_image("img:1") == {"vllm_version": (0, 11), "has_tensorizer": True}
