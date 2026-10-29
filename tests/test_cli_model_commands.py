"""`ling-admin main-model set` and `model download`: which names they refuse and what they save or fetch."""

import tomllib

import pytest

from dreamference.chat.image_search_sidecar import ImageSearchSidecar
from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware.model_matrix_registry import (
    DEFAULT_DIFFUSION_MODEL_ALIAS,
    DEFAULT_MODEL_ALIAS,
    ModelMatrixRegistry,
)


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    for name in (
        "HF_TOKEN",
        "DREAMFERENCE_HF_TOKEN",
        "DREAMFERENCE_MODEL",
        "DREAMFERENCE_DRAFT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ImageSearchSidecar, "state", classmethod(lambda cls: "stopped"))
    return tmp_path / "dreamference.toml"


def _run(config_path, *argv):
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["--config", str(config_path), *argv])
    return exit_info.value.code


def test_main_model_set_saves_the_model_and_not_the_environment_token(
    config_path, monkeypatch, capsys
):
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")
    assert _run(config_path, "main-model", "set", DEFAULT_MODEL_ALIAS) == 0
    saved = tomllib.loads(config_path.read_text())
    assert saved == {"model": DEFAULT_MODEL_ALIAS}
    out = capsys.readouterr().out
    assert f"Main model set to '{DEFAULT_MODEL_ALIAS}'" in out
    assert "Image search runs with the previous model" not in out


def test_main_model_set_says_a_running_image_search_keeps_the_old_model(
    config_path, monkeypatch, capsys
):
    monkeypatch.setattr(ImageSearchSidecar, "state", classmethod(lambda cls: "running"))
    assert _run(config_path, "main-model", "set", DEFAULT_MODEL_ALIAS) == 0
    # Rich wraps the line to the terminal width.
    out = " ".join(capsys.readouterr().out.split())
    assert "`ling-admin images start` once the new one is served" in out


def test_main_model_set_refuses_a_model_that_is_not_offered(config_path, capsys):
    # Diffusion is switched off, so its checkpoint is not offered under any command.
    assert _run(config_path, "main-model", "set", DEFAULT_DIFFUSION_MODEL_ALIAS) == 1
    assert "is not a model Mightling offers" in capsys.readouterr().out
    assert not config_path.exists()


def test_main_model_set_refuses_a_diffusion_model_even_when_it_is_offered(
    config_path, monkeypatch, capsys
):
    monkeypatch.setattr(ModelMatrixRegistry, "is_offered", classmethod(lambda cls, name: True))
    assert _run(config_path, "main-model", "set", DEFAULT_DIFFUSION_MODEL_ALIAS) == 1
    assert "is a diffusion model and cannot be served" in capsys.readouterr().out
    assert not config_path.exists()


@pytest.fixture
def downloads(monkeypatch):
    calls = []
    for name in ("download_model", "download_model_with_drafter", "download_all_models"):
        monkeypatch.setattr(
            controller, name, lambda *a, _name=name, **k: calls.append((_name, a, k))
        )
    return calls


def test_model_download_all_fetches_every_model_with_the_configured_token(config_path, downloads):
    assert _run(config_path, "--hf-token", "hf_named", "model", "download", "--all") == 0
    assert downloads == [
        ("download_all_models", (), {"hf_token": "hf_named", "auto_tensorize": False})
    ]


def test_model_download_fetches_a_configured_draft_model_too(config_path, monkeypatch, downloads):
    monkeypatch.setenv("DREAMFERENCE_DRAFT_MODEL", "example/drafter")
    assert _run(config_path, "model", "download", "--model", DEFAULT_MODEL_ALIAS) == 0
    assert [(name, args) for name, args, _ in downloads] == [
        ("download_model_with_drafter", (DEFAULT_MODEL_ALIAS,)),
        ("download_model", ("example/drafter",)),
    ]


def test_model_download_refuses_a_model_that_is_not_offered(config_path, downloads, capsys):
    assert _run(config_path, "model", "download", "--model", DEFAULT_DIFFUSION_MODEL_ALIAS) == 1
    assert "is not a model Mightling offers" in capsys.readouterr().out
    assert downloads == []
