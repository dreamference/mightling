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

def test_load_custom_config_file(tmp_path):
    cfg_file = tmp_path / "custom_config.yaml"
    cfg_file.write_text("model: llama-3.3-70b\ndraft_model: qwen2.5-coder-1.5b\nnum_speculative_tokens: 8\n")
    
    # Load from file
    config = DGXCoderConfig(config_file=str(cfg_file))
    assert config.model == "llama-3.3-70b"
    assert config.draft_model == "qwen2.5-coder-1.5b"
    assert config.num_speculative_tokens == 8

    # CLI parameter overrides file
    config_override = DGXCoderConfig(config_file=str(cfg_file), model="starcoder2-15b")
    assert config_override.model == "starcoder2-15b"
    assert config_override.draft_model == "qwen2.5-coder-1.5b"

def test_sandbox_config(tmp_path):
    cfg_file = tmp_path / "sandbox_config.yaml"
    cfg_file.write_text("sandbox: apptainer\n")
    config = DGXCoderConfig(config_file=str(cfg_file))
    assert config.sandbox == "apptainer"

    config_cli = DGXCoderConfig(config_file=str(cfg_file), sandbox="podman")
    assert config_cli.sandbox == "podman"

def test_hf_token_config(tmp_path):
    cfg_file = tmp_path / "hf_config.yaml"
    cfg_file.write_text("hf_token: hf_test_token_12345\n")
    config = DGXCoderConfig(config_file=str(cfg_file))
    assert config.hf_token == "hf_test_token_12345"
    env = config.get_env_vars()
    assert env["HF_TOKEN"] == "hf_test_token_12345"

    config_cli = DGXCoderConfig(config_file=str(cfg_file), hf_token="hf_override_67890")
    assert config_cli.hf_token == "hf_override_67890"
