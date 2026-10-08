"""Models a release removed: a 1.4.1 configuration that names one is refused with what to do.

Unknown keys are taken as raw HuggingFace repositories, so before REMOVED_MODELS a removed alias
was accepted by `main-model set`, and `model download` and `server start` tried to fetch a
repository of that name.
"""

import pytest

from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, ModelMatrixRegistry

REMOVED = "qwen3.5-122b-a10b-hybrid-dflash"


def test_every_name_of_a_removed_model_is_known_and_none_of_the_served_ones():
    for name in (REMOVED, "Qwen3.5-122B-A10B-Int4-DFlash", "nvidia/Qwen3.6-35B-A3B-NVFP4",
                 "Intel/Qwen3.5-122B-A10B-int4-AutoRound", "qwen3.5-122b-a10b-dflash-draft"):
        assert ModelMatrixRegistry.removed_in(name) == "1.5.0", name
        assert not ModelMatrixRegistry.is_offered(name), name
    for name in (DEFAULT_MODEL_ALIAS, "qwen3.8-27b-dflash2-draft", "someone/raw-repo", "", None):
        assert ModelMatrixRegistry.removed_in(name) is None, name
    assert ModelMatrixRegistry.is_offered("someone/raw-repo")


def test_the_message_says_what_happened_and_the_command_that_fixes_it():
    message = ModelMatrixRegistry.removed_message(REMOVED, configured=True)
    assert "removed in Mightling 1.5.0" in message and "your Mightling configuration" in message
    assert f"ling-admin main-model set {DEFAULT_MODEL_ALIAS}" in message


def test_main_model_set_refuses_a_removed_model_and_saves_nothing(capsys, monkeypatch):
    saved = []
    monkeypatch.setattr(controller.DreamferenceConfig, "save_config", lambda self: saved.append(1))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["main-model", "set", REMOVED])
    assert exit_info.value.code == 1 and not saved
    assert f"main-model set {DEFAULT_MODEL_ALIAS}" in capsys.readouterr().out


def test_model_download_refuses_a_removed_model_without_downloading(capsys, monkeypatch):
    downloads = []
    monkeypatch.setattr(controller, "download_model", lambda *a, **k: downloads.append(a))
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "download", "--model", REMOVED])
    assert exit_info.value.code == 1 and not downloads
    assert "removed in Mightling 1.5.0" in capsys.readouterr().out


def test_server_start_with_a_removed_model_in_dreamference_toml_stops_before_anything_starts(
        capsys, monkeypatch, tmp_path):
    # The case a 1.4.1 user upgrading meets: the model is in the project's dreamference.toml.
    (tmp_path / "dreamference.toml").write_text(f'model = "{REMOVED}"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DREAMFERENCE_MODEL", raising=False)
    monkeypatch.setattr(DreamferenceCLIController, "_refuse_during_night_run", classmethod(lambda cls, what: None))

    def must_not_start(*args, **kwargs):
        raise AssertionError("server start went past the model check")

    monkeypatch.setattr(controller, "VLLMServerManager", must_not_start)
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["server", "start"])
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "your Mightling configuration" in out and f"main-model set {DEFAULT_MODEL_ALIAS}" in out
