"""Installed LoRA adapters keep the model on SGLang while ling-serve is the default engine."""

import pytest

from dreamference.cli import adapter_command
from dreamference.cli.adapter_command import AdapterCommand
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController as Cli
from dreamference.config import DreamferenceConfig
from dreamference.hardware import ModelMatrixRegistry
from dreamference.hardware.model_matrix_registry import ENGINE_LING, ENGINE_SGLANG
from dreamference.vllm_server.adapter_serving import AdapterServing

MODEL = "qwen3.8-27b-nvfp4-dflash2"


@pytest.fixture
def ling_default(monkeypatch):
    """Makes ling-serve the model's default engine, as the 1.8.0 switch will."""
    monkeypatch.setattr(
        ModelMatrixRegistry, "engines", classmethod(lambda cls, key: [ENGINE_LING, ENGINE_SGLANG])
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
    assert "Adapters need SGLang until ling-engine supports them" in out
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


def test_sglang_default_with_an_adapter_is_untouched(adapters, capsys):
    assert Cli._engine_for_adapters(MODEL, None, from_flag=False) is None
    assert Cli._engine_for_adapters(MODEL, ENGINE_SGLANG, from_flag=True) == ENGINE_SGLANG
    assert capsys.readouterr().out == ""


def test_adapter_install_warns_when_the_engine_is_ling(ling_default, capsys):
    AdapterCommand._warn_if_ling({"key": MODEL}, DreamferenceConfig())
    assert "cannot serve adapters yet" in capsys.readouterr().out


def test_adapter_install_does_not_warn_on_sglang(capsys):
    AdapterCommand._warn_if_ling({"key": MODEL}, DreamferenceConfig())
    assert capsys.readouterr().out == ""
    assert adapter_command  # module imported


def test_adapter_install_reads_the_engine_from_the_config_it_was_given(monkeypatch, capsys):
    # The warning built a fresh default configuration, so a `--config` naming ling-serve was not read.
    from dreamference.node.node_identity import NodeIdentity

    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: object()))
    monkeypatch.setattr(
        adapter_command.AdapterVerifier,
        "served_base",
        classmethod(lambda cls, host, model: {"key": MODEL}),
    )
    entry = {"display_name": "Tuned", "version": "1", "name": "tuned", "trust": "dreamference"}
    monkeypatch.setattr(
        adapter_command.AdapterInstaller, "install", classmethod(lambda cls, *a, **k: entry)
    )

    class Config:
        vllm_host = "http://127.0.0.1:8000"
        model = MODEL
        server_engine = ENGINE_LING
        hf_token = None

    args = Cli.build_parser().parse_args(["adapter", "install", "tuned"])
    assert AdapterCommand.run(args, Config()) == 0
    assert "cannot serve adapters yet" in capsys.readouterr().out
