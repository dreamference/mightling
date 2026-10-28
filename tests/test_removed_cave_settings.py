"""Cave mode was removed (2026-10-28). What it left behind in a user's files is ignored, never an error."""

from dreamference.config import DreamferenceConfig


def test_old_cave_keys_and_variable_are_ignored_and_dropped_on_save(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_CAVE_MODE", "ultra")
    path = tmp_path / "config.yaml"
    path.write_text(
        "vllm_host: http://example.invalid:9000\ncave_mode: true\nmightling_cave_mode: ultra\n"
    )
    config = DreamferenceConfig(config_file=str(path))
    assert config.vllm_host == "http://example.invalid:9000"
    assert not hasattr(config, "cave_mode") and not hasattr(config, "mightling_cave_mode")
    config.save_config()
    saved = path.read_text()
    assert "cave" not in saved and "example.invalid" in saved
