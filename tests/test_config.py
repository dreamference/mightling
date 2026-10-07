from pathlib import Path
from dreamference.config import DreamferenceConfig

def test_tool_call_parser_follows_the_model():
    assert DreamferenceConfig(model="qwen2.5-coder-32b").resolve_tool_call_parser() == "hermes"
    assert DreamferenceConfig(model="qwen3.8-27b-nvfp4-dflash2").resolve_tool_call_parser() == "qwen3_coder"

def test_load_custom_config_file(tmp_path):
    cfg_file = tmp_path / "custom_config.yaml"
    cfg_file.write_text("model: llama-3.3-70b\ndraft_model: qwen2.5-coder-1.5b\nnum_speculative_tokens: 8\n")
    
    # Load from file
    config = DreamferenceConfig(config_file=str(cfg_file))
    assert config.model == "llama-3.3-70b"
    assert config.draft_model == "qwen2.5-coder-1.5b"
    assert config.num_speculative_tokens == 8

    # CLI parameter overrides file
    config_override = DreamferenceConfig(config_file=str(cfg_file), model="starcoder2-15b")
    assert config_override.model == "starcoder2-15b"
    assert config_override.draft_model == "qwen2.5-coder-1.5b"

def test_hf_token_config(tmp_path):
    cfg_file = tmp_path / "hf_config.yaml"
    cfg_file.write_text("hf_token: hf_test_token_12345\n")
    config = DreamferenceConfig(config_file=str(cfg_file))
    assert config.hf_token == "hf_test_token_12345"

    config_cli = DreamferenceConfig(config_file=str(cfg_file), hf_token="hf_override_67890")
    assert config_cli.hf_token == "hf_override_67890"

def test_pinning_the_current_default_model_survives_a_default_change(tmp_path):
    # `mling-admin main-model set X` where X happens to equal today's DEFAULT_MODEL used to write no
    # `model` key at all, because save_config only recorded non-defaults. The pin then silently
    # followed the default to a different checkpoint the next time DEFAULT_MODEL moved -- which it
    # did on 2026-08-15. A chosen model and a defaulted one are different intents.
    import dreamference.config.dreamference_config as cfg_mod

    cfg_file = tmp_path / "pin.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.model = cfg_mod.DEFAULT_MODEL          # exactly what `main-model set` does
    config.save_config()

    assert "model" in cfg_file.read_text()

    original_default = cfg_mod.DEFAULT_MODEL
    try:
        cfg_mod.DEFAULT_MODEL = "some-future-default"
        reloaded = DreamferenceConfig(config_file=str(cfg_file))
        assert reloaded.model == original_default
    finally:
        cfg_mod.DEFAULT_MODEL = original_default

def test_unpinned_model_is_not_fossilised_into_the_config(tmp_path):
    # The other half of the contract: a config nobody pinned must keep tracking the default, or
    # every `mling-admin init` would freeze whatever model happened to be current that day.
    import dreamference.config.dreamference_config as cfg_mod

    cfg_file = tmp_path / "unpinned.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")

    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "model" not in cfg_file.read_text()

    original_default = cfg_mod.DEFAULT_MODEL
    try:
        cfg_mod.DEFAULT_MODEL = "some-future-default"
        assert DreamferenceConfig(config_file=str(cfg_file)).model == "some-future-default"
    finally:
        cfg_mod.DEFAULT_MODEL = original_default

def test_model_from_config_file_is_preserved_on_resave(tmp_path):
    # A model read from the file is a pin too, even if it later coincides with the default: the
    # user wrote it down. Re-saving for an unrelated reason must not drop it.
    cfg_file = tmp_path / "fromfile.yaml"
    cfg_file.write_text("model: starcoder2-15b\n")

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.agent_runner = "cline"
    config.save_config()
    assert "starcoder2-15b" in cfg_file.read_text()

def test_every_config_names_a_diffusion_model():
    # The diffusion model is a peer of the main one: nothing set anywhere still resolves to a
    # concrete registry alias, so `server start` always has something to launch beside vLLM.
    from dreamference.config.dreamference_config import DEFAULT_DIFFUSION_MODEL
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    config = DreamferenceConfig()
    assert config.diffusion_model == DEFAULT_DIFFUSION_MODEL
    assert ModelMatrixRegistry.get_spec(config.diffusion_model) is not None

def test_diffusion_model_resolves_through_the_same_four_tiers(tmp_path, monkeypatch):
    cfg_file = tmp_path / "tiers.yaml"
    cfg_file.write_text("diffusion_model: from-file\n")

    assert DreamferenceConfig(config_file=str(cfg_file)).diffusion_model == "from-file"
    monkeypatch.setenv("DREAMFERENCE_DIFFUSION_MODEL", "from-env")
    assert DreamferenceConfig(config_file=str(cfg_file)).diffusion_model == "from-env"
    assert DreamferenceConfig(
        config_file=str(cfg_file), diffusion_model="from-kwarg"
    ).diffusion_model == "from-kwarg"

def test_pinning_the_default_diffusion_model_survives_a_default_change(tmp_path):
    # Same intent-vs-coincidence contract as the main model: `mling-admin diffusion-model set X` where
    # X equals today's default is still a choice, and must be written down.
    import dreamference.config.dreamference_config as cfg_mod

    cfg_file = tmp_path / "dpin.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.diffusion_model = cfg_mod.DEFAULT_DIFFUSION_MODEL  # what `diffusion-model set` does
    config.save_config()
    assert "diffusion_model" in cfg_file.read_text()

    original_default = cfg_mod.DEFAULT_DIFFUSION_MODEL
    try:
        cfg_mod.DEFAULT_DIFFUSION_MODEL = "some-future-diffusion-default"
        assert DreamferenceConfig(config_file=str(cfg_file)).diffusion_model == original_default
    finally:
        cfg_mod.DEFAULT_DIFFUSION_MODEL = original_default

def test_unpinned_diffusion_model_is_not_fossilised_into_the_config(tmp_path):
    import dreamference.config.dreamference_config as cfg_mod

    cfg_file = tmp_path / "dunpinned.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")

    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "diffusion_model" not in cfg_file.read_text()

    original_default = cfg_mod.DEFAULT_DIFFUSION_MODEL
    try:
        cfg_mod.DEFAULT_DIFFUSION_MODEL = "some-future-diffusion-default"
        assert DreamferenceConfig(
            config_file=str(cfg_file)
        ).diffusion_model == "some-future-diffusion-default"
    finally:
        cfg_mod.DEFAULT_DIFFUSION_MODEL = original_default


def test_saving_keeps_tables_other_readers_own(tmp_path):
    # `[night]` is read by Night Shift, not by DreamferenceConfig; a save used to drop it.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager
    path = tmp_path / "dreamference.toml"
    path.write_text('agent_runner = "codex"\n\n[night]\nwindow = "02:00-05:00"\n')
    ConfigFileStorageManager.save_config_dict(path, {"agent_runner": "cline"})
    saved = ConfigFileStorageManager.load_config_dict(path)
    assert saved == {"agent_runner": "cline", "night": {"window": "02:00-05:00"}}


def test_a_default_host_and_agent_are_not_fossilised_into_the_config(tmp_path):
    # These two were written whatever their value until 2026-10-02, the one exception to "only
    # what differs from the defaults": a saved config stopped following DEFAULT_VLLM_HOST.
    import dreamference.config.dreamference_config as cfg_mod
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    for name in ("defaults.toml", "defaults.yaml", "defaults.json"):
        cfg_file = tmp_path / name
        DreamferenceConfig(config_file=str(cfg_file)).save_config()
        assert ConfigFileStorageManager.load_config_dict(cfg_file) == {}, name
        reloaded = DreamferenceConfig(config_file=str(cfg_file))
        assert reloaded.vllm_host == cfg_mod.DEFAULT_VLLM_HOST
        assert reloaded.agent_runner == cfg_mod.DEFAULT_AGENT_RUNNER

    cfg_file = tmp_path / "chosen.toml"
    config = DreamferenceConfig(config_file=str(cfg_file))
    config.vllm_host = "http://spark-2.local:8000"
    config.agent_runner = "cline"
    config.save_config()
    assert ConfigFileStorageManager.load_config_dict(cfg_file) == {
        "vllm_host": "http://spark-2.local:8000", "agent_runner": "cline"}
