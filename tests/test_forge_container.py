"""`ForgeContainer`: docker commands, the API on loopback, and the waits and listings built on it.

No docker command runs and nothing is sent over the network: `subprocess.run` and the URL opener
are stand-ins that record what they were given.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamference.forge import forge_container
from dreamference.forge.forge_container import LABEL, ForgeContainer


@pytest.fixture
def docker(monkeypatch):
    """Docker answering each command by its first words, recording every argv and timeout."""
    state = {"calls": [], "answers": {}}

    def run(argv, timeout=None, **_):
        state["calls"].append((argv[1:], timeout))
        for words, answer in state["answers"].items():
            if tuple(argv[1 : 1 + len(words)]) == words:
                return SimpleNamespace(returncode=answer[0], stdout=answer[1], stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(forge_container.subprocess, "run", run)
    return state


@pytest.mark.parametrize(
    "error", [FileNotFoundError("docker"), subprocess.TimeoutExpired(["docker"], 30)]
)
def test_docker_that_cannot_run_or_hangs_reads_as_exit_127_with_the_reason(monkeypatch, error):
    def broken(*_, **__):
        raise error

    monkeypatch.setattr(forge_container.subprocess, "run", broken)
    result = ForgeContainer.docker(["ps"])
    assert result.returncode == 127 and result.args == ["docker", "ps"]
    assert result.stderr == str(error)


def test_an_extra_mount_is_read_only_and_the_image_comes_last():
    argv = ForgeContainer.run_arguments(
        Path("/data"), [], extra_mounts=[("/host/patterns", "/etc/patterns")]
    )
    assert argv[argv.index("/host/patterns:/etc/patterns:ro") - 1] == "-v"
    assert argv[-1] == ForgeContainer.image


def test_the_forge_network_is_created_with_its_label_only_when_missing(docker):
    assert ForgeContainer.ensure_network()
    assert [argv[:2] for argv, _ in docker["calls"]] == [["network", "inspect"]]

    docker["calls"].clear()
    docker["answers"][("network", "inspect")] = (1, "")
    assert ForgeContainer.ensure_network()
    assert docker["calls"][1][0] == ["network", "create", "--label", LABEL, ForgeContainer.network]

    docker["answers"][("network", "create")] = (1, "")
    assert ForgeContainer.ensure_network() is False


def test_the_image_is_pulled_only_when_missing_and_a_failed_pull_fails(docker, capsys):
    assert ForgeContainer.ensure_image()
    assert not any(argv[0] == "pull" for argv, _ in docker["calls"])

    docker["answers"][("image", "inspect")] = (1, "")
    assert ForgeContainer.ensure_image()
    assert docker["calls"][-1] == (["pull", ForgeContainer.image], 1800)
    assert "Pulling the forge's image" in capsys.readouterr().out

    docker["answers"][("pull",)] = (1, "")
    assert ForgeContainer.ensure_image() is False


@pytest.mark.parametrize(
    ("answer", "running"), [((0, "true\n"), True), ((0, "false\n"), False), ((1, "true"), False)]
)
def test_the_container_runs_only_when_docker_says_true(docker, answer, running):
    docker["answers"][("inspect",)] = answer
    assert ForgeContainer.running() is running


def test_forgejo_runs_inside_the_named_container(docker):
    ForgeContainer.forgejo(["admin", "user", "list"], timeout=60)
    assert docker["calls"] == [
        (["exec", ForgeContainer.name, "forgejo", "admin", "user", "list"], 60)
    ]
