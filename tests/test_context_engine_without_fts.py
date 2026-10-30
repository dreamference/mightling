"""The context engine indexes and searches on an SQLite built without FTS5, and lists only files it indexed."""

import sqlite3
from pathlib import Path

import pytest

from dreamference.context_engine import ContextEngine, embedding_calculator
from dreamference.context_engine.sqlite_context_storage import SQLiteContextStorage

REAL_CONNECT = sqlite3.connect


class NoFts5:
    """A connection to an SQLite whose build has no FTS5 module."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._conn = REAL_CONNECT(*args, **kwargs)

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
        if "fts5" in sql:
            raise sqlite3.OperationalError("no such module: fts5")
        return self._conn.execute(sql, *args)

    def __enter__(self) -> "NoFts5":
        self._conn.__enter__()
        return self

    def __exit__(self, *exc: object) -> object:
        return self._conn.__exit__(*exc)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


@pytest.fixture
def keyword_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)


@pytest.mark.usefixtures("keyword_only")
def test_without_fts5_the_workspace_is_still_indexed_and_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "connect", NoFts5)
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    (tmp_path / "b.py").write_text("def beta():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    # A rebuild clears a full-text table it does not have.
    engine.index_workspace(force_reindex=True)

    assert set(engine.indexed_files) == {"a.py", "b.py"}
    conn = REAL_CONNECT(engine.sqlite_file)
    try:
        assert sorted(conn.execute("SELECT rel_path FROM files").fetchall()) == [
            ("a.py",),
            ("b.py",),
        ]
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name = 'fts_context'").fetchall()
            == []
        )
    finally:
        conn.close()
    assert engine.sqlite_storage.search_fts(["alpha"], 5) == {}
    assert [hit["rel_path"] for hit in engine.search_code("alpha")][:1] == ["a.py"]


def test_full_text_search_before_any_index_finds_nothing_and_creates_no_database(
    tmp_path: Path,
) -> None:
    storage = SQLiteContextStorage(tmp_path / "context.db")
    assert storage.search_fts(["alpha"], 5) == {}
    assert not (tmp_path / "context.db").exists()


@pytest.mark.usefixtures("keyword_only")
def test_a_full_text_hit_for_a_file_no_longer_indexed_is_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    monkeypatch.setattr(
        engine.sqlite_storage, "search_fts", lambda tokens, top_k: {"gone.py": 1.9, "a.py": 0.5}
    )
    assert [hit["rel_path"] for hit in engine.search_code("alpha")] == ["a.py"]
