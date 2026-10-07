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
    # Session setup lives in the Rust launcher now (mling-rs/, with its own tests for the TOML
    # scoping rule and the local-model options); the Python side only builds and hands over.
    # Arguments go through verbatim, the prompt is Codex's positional PROMPT, and the vLLM host
    # travels in the environment variable the launcher reads first.
    from dreamference.config import DreamferenceConfig
    from dreamference.runner import CodexRunner, CodexInstaller

    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml"), vllm_host="http://gb10:9000"))
    monkeypatch.setattr(CodexInstaller, "install_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/opt/mling"))
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env=None, **k: calls.append((cmd, env)) or 0)

    runner.run_session(agent_args=["exec", "--json", "do it"])
    runner.run_session(prompt="fix the tests")

    (forwarded, env), (prompted, _) = calls
    assert forwarded == ["/opt/mling", "exec", "--json", "do it"]
    assert prompted == ["/opt/mling", "fix the tests"]
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://gb10:9000"


def test_openhands_stays_on_this_machine_and_off_onyx_port(monkeypatch):
    # It mounts the Docker socket, so its UI must not be published on the network; 3000 belongs
    # to Onyx; and from inside the container a loopback vLLM URL would point at the container.
    from dreamference.config import DreamferenceConfig
    from dreamference.runner.openhands_runner import OpenHandsRunner, OPENHANDS_HOST_PORT
    from dreamference.runner.openhands_installer import OpenHandsInstaller
    from dreamference.chat.onyx_runner import OnyxRunner

    runner = OpenHandsRunner(config=DreamferenceConfig(vllm_host="http://localhost:8000"))
    monkeypatch.setattr(runner.vllm_manager, "check_health", lambda: True)
    monkeypatch.setattr(OpenHandsInstaller, "is_docker_available", classmethod(lambda cls: True))
    monkeypatch.setattr(OpenHandsInstaller, "pull_image_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(OnyxRunner, "docker_bridge_gateway", classmethod(lambda cls: "172.17.0.1"))
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
