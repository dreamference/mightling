"""The refusals `ling-admin` makes before any work starts, reached through its own dispatch."""

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.cli.legacy_name_migration import LegacyNameMigration


@pytest.fixture(autouse=True)
def _no_migration(monkeypatch):
    monkeypatch.setattr(LegacyNameMigration, "run", classmethod(lambda cls: None))


@pytest.mark.parametrize(
    "argv",
    [["index"], ["index", "--force"], ["server", "start"], ["codex", "build"]],
)
def test_a_night_run_holding_the_runner_stops_loads_builds_and_index_runs(
    monkeypatch, capsys, argv
):
    from dreamference import context_engine
    from dreamference.night_shift import NightShiftQueue
    from dreamference.node import NodeIdentity
    from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

    monkeypatch.setattr(NightShiftQueue, "runner_holder", classmethod(lambda cls: "pid 99"))

    def refuse(*args, **kwargs):
        pytest.fail(f"`ling-admin {' '.join(argv)}` started work beside a night run")

    monkeypatch.setattr(context_engine, "ContextEngine", refuse)
    monkeypatch.setattr(NodeIdentity, "ensure", classmethod(refuse))
    monkeypatch.setattr(CodexBrandedBuilder, "build", classmethod(refuse))
    monkeypatch.setattr(DreamferenceCLIController, "display_header", classmethod(refuse))

    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(argv)
    assert exit_info.value.code == 1
    assert "waits: see `ling-admin night status`" in capsys.readouterr().out


def test_index_runs_once_no_night_run_holds_the_runner(monkeypatch, tmp_path):
    from dreamference import context_engine
    from dreamference.night_shift import NightShiftQueue

    monkeypatch.setattr(NightShiftQueue, "runner_holder", classmethod(lambda cls: None))
    monkeypatch.setattr(DreamferenceCLIController, "display_header", classmethod(lambda cls: None))
    indexed = []

    class Engine:
        def __init__(self, workspace_root):
            self.root = workspace_root

        def index_workspace(self, force_reindex):
            indexed.append((self.root, force_reindex))
            return {"total_indexed_files": 1, "total_ast_symbols": 2, "total_vector_chunks": 3}

    monkeypatch.setattr(context_engine, "ContextEngine", Engine)
    DreamferenceCLIController.run_cli(["index", "--dir", str(tmp_path), "--force"])
    assert indexed == [(str(tmp_path), True)]
