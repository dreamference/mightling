from pathlib import Path
from dgxcoder.config import DGXCoderConfig

def test_config_env_vars():
    config = DGXCoderConfig(vllm_host="http://localhost:8000", model="qwen2.5-coder-32b")
    env = config.get_env_vars()
    assert env["GOOSE_PROVIDER"] == "openai"
    assert env["OPENAI_BASE_URL"] == "http://localhost:8000/v1"
    assert env["GOOSE_MODEL"] == "Qwen/Qwen2.5-Coder-32B-Instruct"

def test_ensure_goose_config(tmp_path):
    config = DGXCoderConfig()
    config.config_path = tmp_path / "goose" / "config.yaml"
    config.ensure_goose_config()
    assert config.config_path.exists()
    content = config.config_path.read_text()
    assert "jetbrains_mcp" in content

def test_instructions_follow_the_model_tool_call_parser():
    # Hermes-parser models need the <tool_call> format taught; XML-parser models must not be
    # told to emit it, or their tool calls stop parsing server-side.
    hermes = DGXCoderConfig(model="qwen2.5-coder-32b")
    assert hermes.resolve_tool_call_parser() == "hermes"
    assert "<tool_call>" in hermes.build_instructions()

    xml = DGXCoderConfig(model="qwen3.6-35b-a3b-nvfp4")
    assert xml.resolve_tool_call_parser() == "qwen3_xml"
    assert "<tool_call>" not in xml.build_instructions()

def test_cave_mode_survives_parser_selection():
    cave = DGXCoderConfig(model="qwen3.6-35b-a3b-nvfp4", cave_mode=True)
    instructions = cave.build_instructions()
    assert "Cave Mode" in instructions
    assert "<tool_call>" not in instructions

def test_ensure_goose_config_rewrites_stale_instructions(tmp_path):
    config = DGXCoderConfig(model="qwen3.6-35b-a3b-nvfp4")
    config.config_path = tmp_path / "goose" / "config.yaml"
    config.config_path.parent.mkdir(parents=True)
    config.config_path.write_text(
        "instructions: use <tool_call> tags\nextensions:\n  custom:\n    enabled: true\n"
    )
    config.ensure_goose_config()
    content = config.config_path.read_text()
    assert "<tool_call>" not in content
    assert "custom" in content  # unrelated user extensions are still preserved

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
