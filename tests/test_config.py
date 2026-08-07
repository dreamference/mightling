from pathlib import Path
from dgxcoder.config import DGXCoderConfig

def test_config_env_vars():
    config = DGXCoderConfig(vllm_host="http://localhost:8000", model="qwen2.5-coder-32b")
    env = config.get_env_vars()
    assert env["GOOSE_PROVIDER"] == "openai"
    assert env["OPENAI_HOST"] == "http://localhost:8000"
    assert env["GOOSE_MODEL"] == "qwen2.5-coder-32b"

def test_ensure_goose_config(tmp_path):
    config = DGXCoderConfig()
    config.config_path = tmp_path / "goose" / "config.yaml"
    config.ensure_goose_config()
    assert config.config_path.exists()
    content = config.config_path.read_text()
    assert "jetbrains_mcp" in content
