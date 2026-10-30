"""The Cline and Continue runners once the server answers: the user's files and the launch.

What protects the user here: a `.clinerules` the user wrote and the other keys of their
Continue config survive, and the exit code of VS Code is the session's. Nothing here starts
VS Code or reaches a server.
"""

import json
import subprocess

import pytest

from dreamference.config import DreamferenceConfig
from dreamference.runner import ClineInstaller, ClineRunner, ContinueInstaller, ContinueRunner
from dreamference.runner.vllm_readiness_waiter import VLLMReadinessWaiter
from dreamference.vllm_server import VLLMServerManager


@pytest.fixture
def ready(monkeypatch, tmp_path):
    """A healthy server, VS Code on PATH, the workspace and home in tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(VLLMServerManager, "check_health", lambda self: True)
    monkeypatch.setattr(
        VLLMReadinessWaiter, "served_model", staticmethod(lambda manager, model: "served/model")
    )
    installs = []
    for installer, method in (
        (ClineInstaller, "install_cline_if_missing"),
        (ContinueInstaller, "install_continue_if_missing"),
    ):
        monkeypatch.setattr(installer, "is_vscode_installed", classmethod(lambda cls: True))
        monkeypatch.setattr(installer, "get_vscode_executable", classmethod(lambda cls: "code"))
        monkeypatch.setattr(
            installer, method, classmethod(lambda cls, _m=method: installs.append(_m) or True)
        )
    return installs


RUNNERS = [ClineRunner, ContinueRunner]


@pytest.mark.parametrize("runner", RUNNERS, ids=["cline", "continue"])
@pytest.mark.parametrize("code", [0, 3])
def test_the_session_exits_with_vs_codes_exit_code(ready, monkeypatch, tmp_path, runner, code):
    launched = []
    monkeypatch.setattr(subprocess, "call", lambda cmd: launched.append(cmd) or code)
    assert runner(config=DreamferenceConfig()).run_session(prompt="fix the build") == code
    assert launched == [["code", str(tmp_path)]]
    assert len(ready) == 1


@pytest.mark.parametrize("runner", RUNNERS, ids=["cline", "continue"])
def test_a_launch_that_fails_exits_1(ready, monkeypatch, capsys, runner):
    def missing(cmd):
        raise FileNotFoundError("code")

    monkeypatch.setattr(subprocess, "call", missing)
    assert runner(config=DreamferenceConfig()).run_session() == 1
    assert "Failed to launch VS Code" in capsys.readouterr().out


def test_cline_leaves_rules_the_user_wrote(ready, monkeypatch, tmp_path):
    rules = tmp_path / ".clinerules"
    rules.write_text("# my own rules\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "call", lambda cmd: 0)
    assert ClineRunner(config=DreamferenceConfig()).run_session() == 0
    assert rules.read_text(encoding="utf-8") == "# my own rules\n"


def test_continue_keeps_the_users_other_settings_and_replaces_only_the_models(ready, tmp_path):
    # A config it can read is merged in place: no backup, the user's keys stay, and the two
    # model entries point at this server.
    folder = tmp_path / ".continue"
    folder.mkdir()
    old = {
        "allowAnonymousTelemetry": False,
        "models": [{"title": "cloud", "provider": "anthropic"}],
        "tabAutocompleteModel": {"title": "old"},
    }
    (folder / "config.json").write_text(json.dumps(old), encoding="utf-8")

    path = ContinueRunner(config=DreamferenceConfig()).ensure_continue_config()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["allowAnonymousTelemetry"] is False
    assert [m["model"] for m in written["models"]] == ["served/model"]
    assert written["tabAutocompleteModel"]["apiBase"].endswith("/v1/")
    assert not (folder / "config.json.bak").exists()


def test_continue_backs_up_a_config_that_is_json_but_not_an_object(ready, tmp_path):
    folder = tmp_path / ".continue"
    folder.mkdir()
    (folder / "config.json").write_text("[1, 2]", encoding="utf-8")
    ContinueRunner(config=DreamferenceConfig()).ensure_continue_config()
    assert (folder / "config.json.bak").read_text(encoding="utf-8") == "[1, 2]"
