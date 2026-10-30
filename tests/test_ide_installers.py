"""The VS Code extension installers of Cline and Continue and the OpenHands image pull.

subprocess.run is replaced in every test: nothing reaches VS Code or Docker.
"""

import shutil
import subprocess

import pytest

from dreamference.runner.cline_installer import CLINE_EXTENSION_ID, ClineInstaller
from dreamference.runner.continue_installer import CONTINUE_EXTENSION_ID, ContinueInstaller
from dreamference.runner.openhands_installer import OPENHANDS_IMAGE, OpenHandsInstaller

EXTENSIONS = [
    (
        ClineInstaller,
        CLINE_EXTENSION_ID,
        "install_cline_if_missing",
        "is_cline_extension_installed",
    ),
    (
        ContinueInstaller,
        CONTINUE_EXTENSION_ID,
        "install_continue_if_missing",
        "is_continue_extension_installed",
    ),
]
IDS = ["cline", "continue"]


def on_path(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


@pytest.mark.parametrize("installer, extension, install, _", EXTENSIONS, ids=IDS)
def test_an_installed_extension_is_never_installed_again(
    monkeypatch, installer, extension, install, _
):
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, f"ms-python.python\n{extension.upper()}\n", "")

    monkeypatch.setattr(shutil, "which", on_path("code"))
    monkeypatch.setattr(subprocess, "run", run)
    assert getattr(installer, install)() is True
    assert calls == [["code", "--list-extensions"]]


@pytest.mark.parametrize("installer, extension, install, _", EXTENSIONS, ids=IDS)
def test_vscodium_serves_when_vs_code_is_absent(monkeypatch, installer, extension, install, _):
    monkeypatch.setattr(shutil, "which", on_path("codium"))
    assert installer.get_vscode_executable() == "codium"
    assert installer.is_vscode_installed()


@pytest.mark.parametrize("installer, extension, install, listed", EXTENSIONS, ids=IDS)
def test_a_hanging_extension_list_counts_as_not_installed(
    monkeypatch, installer, extension, install, listed
):
    def hang(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(shutil, "which", on_path("code"))
    monkeypatch.setattr(subprocess, "run", hang)
    assert getattr(installer, listed)() is False


@pytest.fixture
def docker(monkeypatch):
    """A Docker CLI whose answers the test sets per subcommand; records every call."""
    answers = {"ps": 0, "images": "", "pull": 0}
    calls = []

    def run(cmd, **k):
        calls.append(cmd[1])
        answer = answers[cmd[1]]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, str):
            return subprocess.CompletedProcess(cmd, 0, answer, "")
        return subprocess.CompletedProcess(cmd, answer, "", "")

    monkeypatch.setattr(shutil, "which", on_path("docker"))
    monkeypatch.setattr(subprocess, "run", run)
    return answers, calls


def test_an_image_already_pulled_is_not_pulled_again(docker):
    answers, calls = docker
    answers["images"] = "abc123\n"
    assert OpenHandsInstaller.pull_image_if_missing() is True
    assert "pull" not in calls


@pytest.mark.parametrize("code, available", [(0, True), (1, False)])
def test_the_pull_decides_whether_the_image_is_available(docker, code, available):
    answers, calls = docker
    answers["pull"] = code
    assert OpenHandsInstaller.pull_image_if_missing() is available
    assert calls[-1] == "pull"


def test_a_pull_that_cannot_run_is_a_failure_not_a_crash(docker, capsys):
    answers, _ = docker
    answers["pull"] = OSError("docker vanished")
    assert OpenHandsInstaller.pull_image_if_missing() is False
    assert "Failed to pull OpenHands image" in capsys.readouterr().out


def test_a_docker_that_does_not_answer_is_unavailable(docker):
    answers, calls = docker
    answers["ps"] = subprocess.TimeoutExpired("docker", 3)
    assert OpenHandsInstaller.is_docker_available() is False
    assert OpenHandsInstaller.pull_image_if_missing() is False
    assert "pull" not in calls


def test_an_image_list_that_fails_means_not_downloaded(docker):
    answers, _ = docker
    answers["images"] = OSError("socket closed")
    assert OpenHandsInstaller.is_image_downloaded() is False


def test_the_image_is_looked_up_by_its_pinned_name(docker, monkeypatch):
    seen = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    assert OpenHandsInstaller.is_image_downloaded() is False
    assert seen[-1] == ["docker", "images", "-q", OPENHANDS_IMAGE]
