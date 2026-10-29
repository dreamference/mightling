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
    # `ling-admin main-model set X` where X happens to equal today's DEFAULT_MODEL used to write no
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
    # every `ling-admin init` would freeze whatever model happened to be current that day.
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
    # Same intent-vs-coincidence contract as the main model: `ling-admin diffusion-model set X` where
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
    # `[night]` is read by Night Shift and `[swe_bench]` by SweBenchSettings, not by
    # DreamferenceConfig; a save used to drop them.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager
    path = tmp_path / "dreamference.toml"
    path.write_text(
        'agent_runner = "codex"\n\n[night]\nwindow = "02:00-05:00"\n\n[swe_bench]\nmax_parallel = 2\n'
    )
    ConfigFileStorageManager.save_config_dict(path, {"agent_runner": "cline"})
    saved = ConfigFileStorageManager.load_config_dict(path)
    assert saved == {
        "agent_runner": "cline", "night": {"window": "02:00-05:00"}, "swe_bench": {"max_parallel": 2},
    }


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


def test_a_config_file_saved_with_a_byte_order_mark_is_read_whole(tmp_path):
    # Windows editors put U+FEFF first. TOML then read the first key as "\ufeffmightling_airgapped",
    # so the level the file sets was ignored (the air gap silently off), and JSON refused the file.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    texts = {
        "marked.toml": 'mightling_airgapped = "on"\nmodel = "m"\n',
        "marked.json": '{"mightling_airgapped": "on", "model": "m"}',
        "marked.yaml": "mightling_airgapped: 'on'\nmodel: m\n",
    }
    for name, text in texts.items():
        path = tmp_path / name
        path.write_bytes(b"\xef\xbb\xbf" + text.encode())
        assert ConfigFileStorageManager.load_config_dict(path) == {"mightling_airgapped": "on", "model": "m"}, name
    assert DreamferenceConfig(config_file=str(tmp_path / "marked.toml")).mightling_airgapped == "on"


def test_a_config_file_that_is_not_a_table_of_settings_is_ignored(tmp_path):
    # A YAML file holding one word or a JSON list came back as a str or a list, and the first
    # `.get` on it raised AttributeError out of DreamferenceConfig().
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    for name, text in {"word.yaml": "just a word\n", "list.yml": "- a\n- b\n", "list.json": "[1, 2]"}.items():
        path = tmp_path / name
        path.write_text(text)
        assert ConfigFileStorageManager.load_config_dict(path) == {}, name
        assert DreamferenceConfig(config_file=str(path)).model


def test_toml_strings_with_escaped_quotes_are_read_as_written(tmp_path):
    # The `toml` package read `"\"\"no"` as `no` and a lone `"\""` as an empty string.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    path = tmp_path / "quotes.toml"
    path.write_text('a = "\\"\\"no::,off1,"\nb = "\\""\n')
    assert ConfigFileStorageManager.load_config_dict(path) == {"a": '""no::,off1,', "b": '"'}
    DreamferenceConfig(config_file=str(path), hf_token='""token').save_config()
    assert DreamferenceConfig(config_file=str(path)).hf_token == '""token'


def test_a_toml_file_the_launcher_reads_is_read_here_too(tmp_path):
    # The toml 0.10 parser (TOML 0.5) refused a byte order mark and a mixed array, both of which
    # the Rust launcher and tomllib read, and the whole file then counted as empty.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    path = tmp_path / "dreamference.toml"
    path.write_text('﻿model = "x"\n', encoding="utf-8")
    assert ConfigFileStorageManager.load_config_dict(path) == {"model": "x"}
    path.write_text('model = "x"\nports = [1, "a"]\n', encoding="utf-8")
    assert ConfigFileStorageManager.load_config_dict(path) == {"model": "x", "ports": [1, "a"]}


def test_saving_never_overwrites_a_file_it_could_not_read(tmp_path):
    # Reading a broken file gives {}, so saving into it kept none of its tables and keys.
    import pytest

    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    path = tmp_path / "dreamference.toml"
    text = 'model = "x"\n[night]\nwindow = \n'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"dreamference\.toml"):
        ConfigFileStorageManager.save_config_dict(path, {"model": "y"})
    assert path.read_text(encoding="utf-8") == text


def test_saving_keeps_the_keys_only_the_launcher_reads(tmp_path, monkeypatch):
    # `main-model set` rewrote the file from the keys DreamferenceConfig knows plus its tables, so a
    # `mightling_context_window` or `mightling_turn_end_compaction` the Rust launcher reads, and any
    # array of tables, was deleted. A key this class owns still goes when it is back at its default.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    for name in ("DREAMFERENCE_MODEL", "DREAMFERENCE_CONFIG_PATH"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "dreamference.toml"
    path.write_text('mightling_context_window = 65536\nmightling_turn_end_compaction = false\n'
                    'kv_cache_dtype = "fp8"\n[[repos]]\nname = "a"\n', encoding="utf-8")
    config = DreamferenceConfig(config_file=str(path))
    config.kv_cache_dtype = None
    config.model = "qwen3.8-27b-nvfp4-dflash2"
    config.save_config()
    assert ConfigFileStorageManager.load_config_dict(path) == {
        "mightling_context_window": 65536, "mightling_turn_end_compaction": False,
        "model": "qwen3.8-27b-nvfp4-dflash2", "repos": [{"name": "a"}]}


def test_every_key_save_config_writes_is_one_it_owns(tmp_path):
    # A key written but not in CONFIG_KEYS would never be removed again once back at its default.
    import dreamference.config.dreamference_config as cfg_mod
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    path = tmp_path / "all.json"
    # The token is saved only as an argument names it (an assigned one is not).
    config = DreamferenceConfig(config_file=str(path), hf_token="x")
    for key in sorted(set(cfg_mod.CONFIG_KEYS) - {"hf_token"}):
        value = getattr(config, key)
        changed = (not value) if isinstance(value, bool) else (value + 1) if isinstance(value, int) else "x"
        setattr(config, key, {"server_engine": "ling", "mightling_airgapped": "on",
                              "mightling_refine_version": "v2"}.get(key, changed))
    config.save_config()
    assert set(ConfigFileStorageManager.load_config_dict(path)) == set(cfg_mod.CONFIG_KEYS)


def test_saving_writes_no_token_and_no_level_that_came_from_the_environment(tmp_path, monkeypatch):
    # `main-model set` saved every resolved field, so a shell's HF_TOKEN went into the file (with
    # no file yet, `./dreamference.toml` in the working directory), and a session's
    # DREAMFERENCE_MIGHTLING_AIRGAPPED=off removed the file's `on`. The file keeps what it held.
    from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

    for name in ("DREAMFERENCE_CONFIG_PATH", "DREAMFERENCE_MODEL", "DREAMFERENCE_HF_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_shell")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "off")
    monkeypatch.chdir(tmp_path)
    config = DreamferenceConfig()
    assert config.hf_token == "hf_from_the_shell"
    config.model = "qwen3.8-27b-nvfp4-dflash2"
    saved = config.save_config()
    assert saved == tmp_path / "dreamference.toml"
    assert "hf_from_the_shell" not in saved.read_text(encoding="utf-8")

    saved.write_text('hf_token = "hf_in_the_file"\nmightling_airgapped = "on"\n', encoding="utf-8")
    config = DreamferenceConfig()
    assert (config.hf_token, config.mightling_airgapped) == ("hf_from_the_shell", "off")
    config.model = "qwen3.8-27b-nvfp4-dflash2"
    config.save_config()
    assert ConfigFileStorageManager.load_config_dict(saved) == {
        "hf_token": "hf_in_the_file", "mightling_airgapped": "on", "model": "qwen3.8-27b-nvfp4-dflash2"}
    # A token given on the command line is still the caller's choice to save.
    config = DreamferenceConfig(hf_token="hf_on_the_command_line")
    config.save_config()
    assert ConfigFileStorageManager.load_config_dict(saved)["hf_token"] == "hf_on_the_command_line"


def test_saving_replaces_the_file_whole_through_a_link_and_keeps_its_mode(tmp_path, monkeypatch):
    # The file was truncated and rewritten in place: a launcher reading it meanwhile, or a save
    # that failed half-way, saw an empty or partial file (the Rust side writes atomically). A
    # config kept as a link into a dotfiles folder must stay a link, and a 0600 file 0600.
    import pytest

    from dreamference.config import config_file_storage_manager as storage

    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text('model = "x"\n', encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    storage.ConfigFileStorageManager.save_config_dict(link, {"model": "y"})
    assert link.is_symlink() and storage.ConfigFileStorageManager.load_config_dict(real) == {"model": "y"}
    assert real.stat().st_mode & 0o777 == 0o600
    assert sorted(entry.name for entry in real.parent.iterdir()) == ["config.toml"]

    def broken(data, handle):
        handle.write("model = ")
        raise RuntimeError("disk full")
    monkeypatch.setattr(storage.optional_module("toml"), "dump", broken)
    with pytest.raises(RuntimeError):
        storage.ConfigFileStorageManager.save_config_dict(link, {"model": "z"})
    assert real.read_text(encoding="utf-8") == 'model = "y"\n'
    assert sorted(entry.name for entry in real.parent.iterdir()) == ["config.toml"]


def test_refine_from_the_environment_is_on_only_if_it_says_so(tmp_path, monkeypatch):
    # An argument outranks the variable, the variable the file; a blank variable counts as unset.
    path = tmp_path / "dreamference.toml"
    path.write_text("mightling_refine = true\n")
    for value, expected in (("yes", True), ("ON", True), ("off", False), ("maybe", False), ("  ", True)):
        monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", value)
        assert DreamferenceConfig(config_file=str(path)).mightling_refine is expected, value
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", "off")
    assert DreamferenceConfig(config_file=str(path), mightling_refine=True).mightling_refine is True


def test_without_the_older_toml_package_a_file_tomllib_refuses_reads_as_empty(tmp_path, monkeypatch, capsys):
    import tomllib

    import pytest

    import dreamference.config.config_file_storage_manager as storage

    monkeypatch.setattr(storage, "optional_module", lambda name: None)
    with pytest.raises(tomllib.TOMLDecodeError):
        storage.ConfigFileStorageManager._load_toml("model = \n")
    path = tmp_path / "config.toml"
    path.write_text("model = \n")
    assert storage.ConfigFileStorageManager.load_config_dict(path) == {}
    assert "Error reading" in capsys.readouterr().out


def test_a_draft_model_is_checked_together_with_the_main_model(tmp_path, monkeypatch):
    import dreamference.hardware as hardware

    asked = []

    def speculative(model, draft):
        asked.append((model, draft))
        return False, "does not fit"

    monkeypatch.setattr(hardware, "check_speculative_compatibility", speculative)
    config = DreamferenceConfig(config_file=str(tmp_path / "none.toml"), draft_model="small-draft")
    assert config.validate_model() == (False, "does not fit")
    assert asked == [(config.model, "small-draft")]
