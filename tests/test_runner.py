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

def test_codex_config_keeps_top_level_keys_out_of_tables(tmp_path, monkeypatch):
    # TOML scopes a bare key to the most recent [section] above it, so appending a top-level key
    # to a file that already has tables silently reparents it. Codex writes its own
    # [tui.model_availability_nux] table -- whose values must be integers -- and appending
    # model_catalog_json after it produced "invalid type: string ... expected u32" and a CLI that
    # would not start.
    import tomllib
    from dreamference.config import DreamferenceConfig
    from dreamference.runner import CodexRunner

    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    # A config shaped like the one Codex leaves behind: tables only, no top-level keys.
    (codex_home / "config.toml").write_text(
        '[model_providers.openai-custom]\n'
        'name = "openai-custom"\n'
        'base_url = "http://localhost:8000/v1"\n'
        '\n'
        '[tui.model_availability_nux]\n'
        '"gpt-5.6-sol" = 3\n'
    )

    from dreamference.runner import CodexInstaller
    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml")))
    monkeypatch.setattr(runner.vllm_manager, "check_health", lambda: True)
    monkeypatch.setattr(CodexInstaller, "is_installed", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/bin/true"))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: 0)
    runner.run_session()

    parsed = tomllib.loads((codex_home / "config.toml").read_text())
    assert parsed["model_catalog_json"].endswith("model_catalog.json")
    # The pre-existing table must be untouched, and must not have adopted the key.
    assert parsed["tui"]["model_availability_nux"] == {"gpt-5.6-sol": 3}

def test_codex_config_stays_valid_toml_after_repeated_writes(tmp_path, monkeypatch):
    # Every top-level key the runner writes must land above the first [table]. TOML scopes a bare
    # key to the table above it, so a key appended anywhere else is silently reparented -- which
    # has now broken this config twice: once into [tui.model_availability_nux] (values must be
    # u32) and once into [mcp_servers.searxng.env] (values must be strings). Both surfaced only
    # when Codex refused to start.
    import tomllib
    from dreamference.config import DreamferenceConfig
    from dreamference.runner import CodexRunner, CodexInstaller

    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    # Seed with tables whose value types differ, so a reparented key fails to parse as that type.
    (codex_home / "config.toml").write_text(
        '[tui.model_availability_nux]\n"gpt-5.6-sol" = 3\n\n'
        '[mcp_servers.other.env]\nSOME_URL = "http://example"\n'
    )

    runner = CodexRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml")))
    monkeypatch.setattr(runner.vllm_manager, "check_health", lambda: True)
    monkeypatch.setattr(CodexInstaller, "is_installed", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/bin/true"))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: 0)

    # Twice: the second run must not duplicate or reparent anything.
    runner.run_session()
    runner.run_session()

    parsed = tomllib.loads((codex_home / "config.toml").read_text())
    assert isinstance(parsed.get("model_catalog_json"), str)
    assert parsed.get("suppress_unstable_features_warning") is True
    # The seeded tables keep their original value types -- nothing was reparented into them.
    assert parsed["tui"]["model_availability_nux"] == {"gpt-5.6-sol": 3}
    assert parsed["mcp_servers"]["other"]["env"] == {"SOME_URL": "http://example"}
    assert parsed["features"]["code_mode"] is True
