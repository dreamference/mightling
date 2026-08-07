from dgxcoder.config import DGXCoderConfig
from dgxcoder.runner import GooseRunner

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
