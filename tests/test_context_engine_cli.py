"""`ling-admin init` and `ling-admin index`: which directory they index, and when they rebuild."""

from typing import ClassVar

import pytest

import dreamference.context_engine
from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.night_shift import NightShiftQueue


class _RecordingEngine:
    calls: ClassVar[list] = []

    def __init__(self, workspace_root=None):
        self.workspace_root = workspace_root

    def index_workspace(self, force_reindex=False):
        self.calls.append((self.workspace_root, force_reindex))
        return {"total_indexed_files": 7, "total_ast_symbols": 11}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for name in (
        "HF_TOKEN",
        "DREAMFERENCE_HF_TOKEN",
        "DREAMFERENCE_MODEL",
        "DREAMFERENCE_DRAFT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_RecordingEngine, "calls", [])
    monkeypatch.setattr(dreamference.context_engine, "ContextEngine", _RecordingEngine)
    monkeypatch.setattr(
        NightShiftQueue, "runner_holder", classmethod(lambda cls, night_dir=None: None)
    )
    for name in ("download_model", "download_model_with_drafter"):
        monkeypatch.setattr(controller, name, lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    return _RecordingEngine


def _run(tmp_path, *argv):
    try:
        DreamferenceCLIController.run_cli(["--config", str(tmp_path / "dreamference.toml"), *argv])
    except SystemExit as exit_info:
        return exit_info.code
    return 0


def test_init_always_rebuilds_the_index_of_the_current_directory(tmp_path, engine, capsys):
    assert _run(tmp_path, "init") in (0, None)
    assert engine.calls == [(None, True)]  # no root given: the engine takes the current directory
    assert "7 (11 AST symbols)" in " ".join(capsys.readouterr().out.split())


def test_index_reuses_a_saved_index_unless_forced(tmp_path, engine, capsys):
    assert _run(tmp_path, "index") in (0, None)
    assert _run(tmp_path, "index", "--dir", str(tmp_path / "other"), "--force") in (0, None)
    assert engine.calls == [(str(tmp_path), False), (str(tmp_path / "other"), True)]
    out = capsys.readouterr().out
    assert "Files Indexed: 7" in out and "AST Symbols:   11" in out
