import os
from dreamference.config import DreamferenceConfig
from dreamference.runner import (
    ClineRunner, ClineInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller
)

def test_cline_runner_clinerules_creation(tmp_path):
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        config = DreamferenceConfig(agent_runner="cline", model="qwen3.8-27b-nvfp4-dflash2")
        cline_runner = ClineRunner(config=config)
        rules_file = cline_runner.ensure_clinerules()
        assert rules_file.exists()
        content = rules_file.read_text(encoding="utf-8")
        assert "OpenAI Compatible" in content
        assert "RadixArk/Qwen3.8-27B-NVFP4" in content
    finally:
        os.chdir(orig_cwd)

def test_continue_runner_config_creation():
    config = DreamferenceConfig(agent_runner="continue", model="qwen3.8-27b-nvfp4-dflash2")
    continue_runner = ContinueRunner(config=config)
    cfg_file = continue_runner.ensure_continue_config()
    assert cfg_file.exists()
    content = cfg_file.read_text(encoding="utf-8")
    assert "RadixArk/Qwen3.8-27B-NVFP4" in content

def test_agent_runner_choices():
    for agent in ["codex", "cline", "continue", "openhands"]:
        cfg = DreamferenceConfig(agent_runner=agent)
        assert cfg.agent_runner == agent

def test_codex_runner_hands_arguments_and_host_to_mightling(tmp_path, monkeypatch):
    # Session setup lives in the Rust launcher now (ling-rs/, with its own tests for the TOML
    # scoping rule and the local-model options); the Python side only builds and hands over.
    # Arguments go through verbatim, the prompt is Codex's positional PROMPT, and the vLLM host
    # travels in the environment variable the launcher reads first.
    from dreamference.config import DreamferenceConfig
    from dreamference.runner import CodexRunner, CodexInstaller

    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml"), vllm_host="http://gb10:9000"))
    monkeypatch.setattr(CodexInstaller, "install_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/opt/ling"))
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env=None, **k: calls.append((cmd, env)) or 0)

    runner.run_session(agent_args=["exec", "--json", "do it"])
    runner.run_session(prompt="fix the tests")

    (forwarded, env), (prompted, _) = calls
    assert forwarded == ["/opt/ling", "exec", "--json", "do it"]
    assert prompted == ["/opt/ling", "fix the tests"]
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://gb10:9000"



def test_codex_runner_passes_the_compatibility_mode_in_the_launchers_variable(tmp_path, monkeypatch):
    """`ling-admin run --compat claude` sets DREAMFERENCE_MIGHTLING_COMPAT for that session only."""
    from dreamference.runner import CodexInstaller, CodexRunner

    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml")))
    monkeypatch.setattr(CodexInstaller, "install_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/opt/ling"))
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_COMPAT", raising=False)
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env=None, **k: calls.append(env) or 0)

    assert runner.run_session(prompt="fix it", compat="claude") == 0
    assert runner.run_session(prompt="fix it") == 0
    assert calls[0]["DREAMFERENCE_MIGHTLING_COMPAT"] == "claude"
    assert "DREAMFERENCE_MIGHTLING_COMPAT" not in calls[1]
    assert len(calls) == 2


def test_run_compat_is_refused_for_the_other_runners(tmp_path, monkeypatch, capsys):
    import pytest

    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    monkeypatch.setattr("subprocess.call", lambda *a, **k: pytest.fail("no runner may start"))
    with pytest.raises(SystemExit) as exited:
        DreamferenceCLIController.run_cli(["run", "x", "--agent", "cline", "--compat", "claude"])
    assert exited.value.code == 2
    assert "a mode of ling" in capsys.readouterr().out


def test_openhands_stays_on_this_machine_and_off_port_3000(monkeypatch):
    # It mounts the Docker socket, so its UI must not be published on the network; it stays on
    # 3001, as since Onyx had 3000; and from inside the container a loopback vLLM URL would point
    # at the container.
    from dreamference.config import DreamferenceConfig
    from dreamference.runner.openhands_runner import OpenHandsRunner, OPENHANDS_HOST_PORT
    from dreamference.runner.openhands_installer import OpenHandsInstaller
    from dreamference.chat.docker_bridge import DockerBridge

    runner = OpenHandsRunner(config=DreamferenceConfig(vllm_host="http://localhost:8000"))
    monkeypatch.setattr(runner.vllm_manager, "check_health", lambda: True)
    monkeypatch.setattr(OpenHandsInstaller, "is_docker_available", classmethod(lambda cls: True))
    monkeypatch.setattr(OpenHandsInstaller, "pull_image_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(DockerBridge, "gateway", classmethod(lambda cls: "172.17.0.1"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, **k: calls.append(cmd) or 0)

    assert runner.run_session() == 0
    cmd = calls[0]
    assert cmd[cmd.index("-p") + 1] == f"127.0.0.1:{OPENHANDS_HOST_PORT}:3000"
    assert OPENHANDS_HOST_PORT != 3000
    assert "LLM_BASE_URL=http://172.17.0.1:8000/v1" in cmd


def test_agents_only_name_the_model_the_server_serves(tmp_path):
    # The draft model is vLLM's internal speculative head, not a served model. Continue's
    # autocomplete used to send requests naming it.
    import json
    from dreamference.config import DreamferenceConfig
    from dreamference.hardware import resolve_model_hf_repo

    config = DreamferenceConfig(config_file=str(tmp_path / "d.toml"), model="qwen3.8-27b-nvfp4-dflash2",
                                draft_model="qwen3.8-27b-dflash2-draft")
    served = resolve_model_hf_repo(config.model)

    cont = json.loads(ContinueRunner(config=config).ensure_continue_config().read_text())
    assert cont["tabAutocompleteModel"]["model"] == served


def test_readiness_waiter_needs_health_and_a_completion(monkeypatch):
    # /health answers before the engine can generate, so the waiter also needs a completion.
    from dreamference.runner import VLLMReadinessWaiter

    waiter = VLLMReadinessWaiter(config=DreamferenceConfig())
    monkeypatch.setattr(waiter.vllm_manager, "check_health", lambda timeout=None: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    monkeypatch.setattr(waiter, "_pre_warm", lambda: True)
    assert waiter.wait_for_vllm(max_wait=5) is True

    monkeypatch.setattr(waiter, "_pre_warm", lambda: False)
    assert waiter.wait_for_vllm(max_wait=0) is False


def test_readiness_waiter_warms_the_model_the_server_runs_not_the_configured_one(monkeypatch):
    # The server may run another model than the config names (AGENTS: ask the server); a completion
    # for the configured one answers 404, and the waiter would wait out its whole timeout.
    import io
    import json

    from dreamference.runner import vllm_readiness_waiter as waiter_module

    waiter = waiter_module.VLLMReadinessWaiter(config=DreamferenceConfig())
    monkeypatch.setattr(waiter.vllm_manager, "get_models", lambda timeout=2.0: ["served/other-model"])
    asked = []

    class Response(io.BytesIO):
        status = 200

    def fake_urlopen(request, timeout=None):
        asked.append(json.loads(request.data)["model"])
        return Response(b"{}")

    monkeypatch.setattr(waiter_module.urllib.request, "urlopen", fake_urlopen)
    assert waiter._pre_warm() is True
    assert asked == ["served/other-model"]


def test_continue_keeps_a_config_it_cannot_read_before_writing_its_own(tmp_path, monkeypatch):
    # Continue reads JSON with comments; json.load cannot, and the user's file was overwritten.
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    original = '{\n  // my cloud model\n  "allowAnonymousTelemetry": false\n}\n'
    (tmp_path / ".continue").mkdir()
    (tmp_path / ".continue" / "config.json").write_text(original, encoding="utf-8")

    ContinueRunner(config=DreamferenceConfig()).ensure_continue_config()
    assert (tmp_path / ".continue" / "config.json.bak").read_text(encoding="utf-8") == original
