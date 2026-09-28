import os
from dreamference.config import DreamferenceConfig
from dreamference.runner import (
    GooseRunner, SandboxManager, ClineRunner, ClineInstaller,
    AiderRunner, AiderInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller
)

def test_runner_sandbox_prefix_none():
    config = DreamferenceConfig(sandbox="none")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert prefix == []

def test_runner_sandbox_prefix_docker():
    config = DreamferenceConfig(sandbox="docker")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert isinstance(prefix, list)

def test_cline_runner_clinerules_creation(tmp_path):
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        config = DreamferenceConfig(agent_runner="cline", model="qwen3.6-35b-a3b-nvfp4")
        cline_runner = ClineRunner(config=config)
        rules_file = cline_runner.ensure_clinerules()
        assert rules_file.exists()
        content = rules_file.read_text(encoding="utf-8")
        assert "OpenAI Compatible" in content
        assert "nvidia/Qwen3.6-35B-A3B-NVFP4" in content
    finally:
        os.chdir(orig_cwd)

def test_continue_runner_config_creation():
    config = DreamferenceConfig(agent_runner="continue", model="qwen3.6-35b-a3b-nvfp4")
    continue_runner = ContinueRunner(config=config)
    cfg_file = continue_runner.ensure_continue_config()
    assert cfg_file.exists()
    content = cfg_file.read_text(encoding="utf-8")
    assert "nvidia/Qwen3.6-35B-A3B-NVFP4" in content

def test_agent_runner_choices():
    for agent in ["goose", "cline", "aider", "continue", "openhands"]:
        cfg = DreamferenceConfig(agent_runner=agent)
        assert cfg.agent_runner == agent

def test_codex_runner_hands_arguments_and_host_to_puffin(tmp_path, monkeypatch):
    # Session setup lives in the Rust launcher now (puffin-rs/, with its own tests for the TOML
    # scoping rule and the local-model options); the Python side only builds and hands over.
    # Arguments go through verbatim, the prompt is Codex's positional PROMPT, and the vLLM host
    # travels in the environment variable the launcher reads first.
    from dreamference.config import DreamferenceConfig
    from dreamference.runner import CodexRunner, CodexInstaller

    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml"), vllm_host="http://gb10:9000"))
    monkeypatch.setattr(CodexInstaller, "install_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/opt/puffin"))
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env=None, **k: calls.append((cmd, env)) or 0)

    runner.run_session(agent_args=["exec", "--json", "do it"])
    runner.run_session(prompt="fix the tests")

    (forwarded, env), (prompted, _) = calls
    assert forwarded == ["/opt/puffin", "exec", "--json", "do it"]
    assert prompted == ["/opt/puffin", "fix the tests"]
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://gb10:9000"
