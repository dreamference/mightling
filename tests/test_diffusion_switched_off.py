"""Diffusion is switched off (DIFFUSION_ENABLED, 2026-10-03): never started, never downloaded,
never shown. The sidecar's code stays and is still tested in tests/test_diffusion_server.py; the
last test here turns the switch on and checks that everything comes back.
"""

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware import model_matrix_registry
from dreamference.hardware.model_matrix_registry import (
    DEFAULT_DIFFUSION_MODEL_ALIAS,
    DEFAULT_MODEL_ALIAS,
    ModelMatrixRegistry,
)
from dreamference.vllm_server import DiffusionServerManager, VLLMServerManager


def test_diffusion_models_are_not_offered_while_switched_off():
    assert ModelMatrixRegistry.diffusion_enabled() is False
    assert ModelMatrixRegistry.is_offered(DEFAULT_DIFFUSION_MODEL_ALIAS) is False
    assert ModelMatrixRegistry.is_offered(DEFAULT_MODEL_ALIAS) is True
    assert ModelMatrixRegistry.is_offered("someone/raw-hf-repo") is True  # downloadable by name


def test_the_cli_names_no_diffusion_anywhere(capsys):
    for argv in (["--help"], ["server", "start", "--help"], ["server", "stop", "--help"],
                 ["server", "remove", "--help"]):
        with pytest.raises(SystemExit):
            DreamferenceCLIController.run_cli(argv)
        assert "diffusion" not in capsys.readouterr().out.lower(), argv
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["diffusion-model", "set", DEFAULT_DIFFUSION_MODEL_ALIAS])
    assert "invalid choice: 'diffusion-model'" in capsys.readouterr().err


def test_model_list_leaves_out_the_diffusion_model(capsys):
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["model", "list"])
    out = capsys.readouterr().out
    assert DEFAULT_MODEL_ALIAS in out and "a2d" not in out


def test_the_diffusion_model_is_never_downloaded(monkeypatch, capsys):
    from dreamference.hardware.model_downloader import ModelDownloader
    fetched = []
    monkeypatch.setattr(ModelDownloader, "download_model",
                        classmethod(lambda cls, key, **_: fetched.append(key) or True))
    ModelDownloader.download_all_models()
    assert fetched and DEFAULT_DIFFUSION_MODEL_ALIAS not in fetched
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["model", "download", "--model", DEFAULT_DIFFUSION_MODEL_ALIAS])
    assert exit_info.value.code == 1 and fetched.count(DEFAULT_DIFFUSION_MODEL_ALIAS) == 0


def test_server_start_removes_a_leftover_sidecar_quietly_and_starts_none(monkeypatch, capsys):
    from dreamference.cli import dreamference_cli_controller as controller
    removed, started = [], []

    class Monitor:
        server_ready = False

        def start(self):
            pass

        def stop(self):
            pass

    def refused(self, **_):
        raise SystemExit(1)

    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: Monitor())
    monkeypatch.setattr(VLLMServerManager, "start_server", refused)
    monkeypatch.setattr(DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: removed.append(port)))
    monkeypatch.setattr(DiffusionServerManager, "start_server", lambda self, **k: started.append(k) or True)
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["server", "start"])
    assert removed == [8001] and started == []
    assert "diffusion" not in capsys.readouterr().out.lower()


def test_server_stop_says_nothing_about_diffusion(monkeypatch, capsys):
    removed = []
    monkeypatch.setattr(VLLMServerManager, "stop_server", lambda self, port=8000: None)
    monkeypatch.setattr(DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: removed.append(port)))
    DreamferenceCLIController.run_cli(["server", "stop"])
    assert removed == [8001]
    assert "diffusion" not in capsys.readouterr().out.lower()


def test_the_switch_brings_it_all_back(monkeypatch, capsys):
    monkeypatch.setattr(model_matrix_registry, "DIFFUSION_ENABLED", True)
    assert ModelMatrixRegistry.is_offered(DEFAULT_DIFFUSION_MODEL_ALIAS)
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["server", "start", "--help"])
    assert "--no-diffusion" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["diffusion-model"])
    assert exit_info.value.code == 1 and "usage: ling-admin diffusion-model" in capsys.readouterr().out
