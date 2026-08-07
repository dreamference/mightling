from dgxcoder.config import DGXCoderConfig
from dgxcoder.runner import (
    GooseRunner, ClineRunner, ClineInstaller,
    AiderRunner, AiderInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller
)

def test_runner_sandbox_prefix_none():
    config = DGXCoderConfig(sandbox="none")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert prefix == []

def test_runner_sandbox_prefix_docker(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
    config = DGXCoderConfig(sandbox="docker")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert len(prefix) > 0
    assert prefix[0] == "docker"
    assert "ubuntu:22.04" in prefix

def test_cline_runner_clinerules_creation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = DGXCoderConfig(agent_runner="cline", model="qwen2.5-coder-32b")
    cline_runner = ClineRunner(config=config)
    rules_file = cline_runner.ensure_clinerules()
    assert rules_file.exists()
    content = rules_file.read_text(encoding="utf-8")
    assert "OpenAI Compatible" in content
    assert "Qwen/Qwen2.5-Coder-32B-Instruct" in content

def test_continue_runner_config_creation(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    config = DGXCoderConfig(agent_runner="continue", model="qwen2.5-coder-32b")
    continue_runner = ContinueRunner(config=config)
    cfg_file = continue_runner.ensure_continue_config()
    assert cfg_file.exists()
    content = cfg_file.read_text(encoding="utf-8")
    assert "Qwen/Qwen2.5-Coder-32B-Instruct" in content

def test_agent_runner_choices():
    for agent in ["goose", "cline", "aider", "continue", "openhands"]:
        cfg = DGXCoderConfig(agent_runner=agent)
        assert cfg.agent_runner == agent
