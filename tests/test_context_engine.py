import os
from pathlib import Path
from dgxcoder.context_engine import ContextEngine

def test_context_engine_indexing(tmp_path):
    py_file = tmp_path / "sample.py"
    py_file.write_text("class TestModel:\n    def execute(self):\n        pass\n")

    engine = ContextEngine(workspace_root=str(tmp_path))
    summary = engine.index_workspace(force_reindex=True)
    assert summary["total_indexed_files"] == 1
    assert summary["total_ast_symbols"] == 2

    # Search code
    results = engine.search_code("TestModel execute")
    assert len(results) > 0
    assert results[0]["rel_path"] == "sample.py"
