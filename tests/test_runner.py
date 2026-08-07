import os
from dgxcoder.config import DGXCoderConfig
from dgxcoder.runner import (
    GooseRunner, SandboxManager, ClineRunner, ClineInstaller,
    AiderRunner, AiderInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller
)

def test_runner_sandbox_prefix_none():
    config = DGXCoderConfig(sandbox="none")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert prefix == []

def test_runner_sandbox_prefix_docker():
    config = DGXCoderConfig(sandbox="docker")
    runner = GooseRunner(config=config)
    prefix = runner.get_sandbox_command_prefix()
    assert isinstance(prefix, list)

def test_cline_runner_clinerules_creation(tmp_path):
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        config = DGXCoderConfig(agent_runner="cline", model="qwen2.5-coder-32b")
        cline_runner = ClineRunner(config=config)
        rules_file = cline_runner.ensure_clinerules()
        assert rules_file.exists()
        content = rules_file.read_text(encoding="utf-8")
        assert "OpenAI Compatible" in content
        assert "Qwen/Qwen2.5-Coder-32B-Instruct" in content
    finally:
        os.chdir(orig_cwd)

def test_continue_runner_config_creation():
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
