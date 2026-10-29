"""Installed LoRA adapters keep the model on SGLang while ling-serve is the default engine."""

import pytest

from dreamference.cli import adapter_command
from dreamference.cli.adapter_command import AdapterCommand
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli
from dreamference.hardware import ModelMatrixRegistry
from dreamference.hardware.model_matrix_registry import ENGINE_LING, ENGINE_SGLANG
from dreamference.vllm_server.adapter_serving import AdapterServing

MODEL = "qwen3.8-27b-nvfp4-dflash2"


@pytest.fixture
def ling_default():
    """The registry as shipped: ling-serve is the model's default engine since the 1.8.0 switch."""
    assert ModelMatrixRegistry.engines(MODEL) == [ENGINE_LING, ENGINE_SGLANG]


@pytest.fixture
def sglang_default(monkeypatch):
    """The rollback position (QWEN38_DEFAULT_ENGINE = ENGINE_SGLANG): SGLang first."""
    monkeypatch.setattr(
        ModelMatrixRegistry, "engines", classmethod(lambda cls, key: [ENGINE_SGLANG, ENGINE_LING])
    )


@pytest.fixture
def adapters(monkeypatch):
    monkeypatch.setattr(
        AdapterServing, "launch_adapters", classmethod(lambda cls, key: [{"name": "mine"}])
    )


@pytest.fixture
def no_adapters(monkeypatch):
    monkeypatch.setattr(AdapterServing, "launch_adapters", classmethod(lambda cls, key: []))


def test_ling_default_with_an_adapter_falls_back_to_sglang_and_says_so(
    ling_default, adapters, capsys
):
    assert Cli._engine_for_adapters(MODEL, None, from_flag=False) == ENGINE_SGLANG
    out = capsys.readouterr().out
    assert "Adapters need SGLang until 1.8.1" in out
    assert "mine" in out


def test_ling_from_the_config_with_an_adapter_also_falls_back(ling_default, adapters):
    assert Cli._engine_for_adapters(MODEL, ENGINE_LING, from_flag=False) == ENGINE_SGLANG


def test_explicit_engine_ling_with_an_adapter_is_refused(ling_default, adapters, capsys):
    with pytest.raises(SystemExit) as stop:
        Cli._engine_for_adapters(MODEL, ENGINE_LING, from_flag=True)
    assert stop.value.code == 2
    assert "--engine ling cannot serve the installed adapters" in capsys.readouterr().out


def test_ling_without_adapters_is_untouched(ling_default, no_adapters, capsys):
    assert Cli._engine_for_adapters(MODEL, None, from_flag=False) is None
    assert Cli._engine_for_adapters(MODEL, ENGINE_LING, from_flag=True) == ENGINE_LING
    assert capsys.readouterr().out == ""


def test_the_shipped_default_with_an_adapter_starts_sglang(adapters, capsys):
    # No monkeypatched registry: a plain `server start` after the 1.8.0 switch, adapter installed.
    assert Cli._engine_for_adapters(MODEL, None, from_flag=False) == ENGINE_SGLANG
    assert "mine" in capsys.readouterr().out


def test_explicit_sglang_with_an_adapter_is_untouched(adapters, capsys):
    assert Cli._engine_for_adapters(MODEL, ENGINE_SGLANG, from_flag=True) == ENGINE_SGLANG
    assert Cli._engine_for_adapters(MODEL, ENGINE_SGLANG, from_flag=False) == ENGINE_SGLANG
    assert capsys.readouterr().out == ""


def test_sglang_default_with_an_adapter_is_untouched(sglang_default, adapters, capsys):
    assert Cli._engine_for_adapters(MODEL, None, from_flag=False) is None
    assert Cli._engine_for_adapters(MODEL, ENGINE_SGLANG, from_flag=True) == ENGINE_SGLANG
    assert capsys.readouterr().out == ""


def test_adapter_install_warns_when_the_engine_is_ling(ling_default, capsys):
    AdapterCommand._warn_if_ling({"key": MODEL})
    assert "serves adapters from 1.8.1" in capsys.readouterr().out


def test_adapter_install_warns_on_the_shipped_default(capsys):
    AdapterCommand._warn_if_ling({"key": MODEL})
    assert "serves adapters from 1.8.1" in capsys.readouterr().out


def test_adapter_install_does_not_warn_when_the_config_names_sglang(monkeypatch, capsys):
    monkeypatch.setenv("DREAMFERENCE_SERVER_ENGINE", ENGINE_SGLANG)
    AdapterCommand._warn_if_ling({"key": MODEL})
    assert capsys.readouterr().out == ""


def test_adapter_install_does_not_warn_on_sglang(sglang_default, capsys):
    AdapterCommand._warn_if_ling({"key": MODEL})
    assert capsys.readouterr().out == ""
    assert adapter_command  # module imported
