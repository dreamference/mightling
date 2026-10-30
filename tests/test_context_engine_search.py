"""How the context engine ranks files: full-text, TF-IDF and vector scores, and what survives a restart."""

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dreamference.context_engine import ContextEngine, embedding_calculator
from dreamference.context_engine.indexed_file import IndexedFile
from dreamference.context_engine.sqlite_context_storage import SQLiteContextStorage


def fts_storage(tmp_path: Path, rows: dict[str, str]) -> SQLiteContextStorage:
    storage = SQLiteContextStorage(tmp_path / "context.db")
    conn = storage.init_db()
    with conn:
        conn.executemany("INSERT INTO fts_context VALUES (?, ?);", rows.items())
    conn.close()
    return storage


def test_a_files_score_is_full_text_plus_tf_idf_plus_three_times_its_positive_cosine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.indexed_files = {
        name: IndexedFile(rel_path=name, abs_path=name, size_bytes=1, symbols=[], tokens=[])
        for name in ("a.py", "b.py", "c.py", "d.py", "e.py")
    }
    monkeypatch.setattr(engine, "tokenize", lambda text: ["alpha"])
    monkeypatch.setattr(
        engine.sqlite_storage,
        "search_fts",
        lambda tokens, top_k: {"a.py": 0.5, "c.py": 0.375, "d.py": 0.25},
    )
    engine.tf_idf_index = {"alpha": {"a.py": 0.25, "b.py": 0.125}}
    # Not unit vectors, so the cosine has to divide by both norms.
    engine.file_embeddings = {
        "a.py": np.array([3.0, 0.0], dtype=np.float32),
        "b.py": np.array([0.0, 3.0], dtype=np.float32),
        "c.py": np.array([-4.0, 0.0], dtype=np.float32),  # opposite: adds nothing, takes nothing
        "e.py": np.array([0.0, 1.0], dtype=np.float32),  # scores 0, ranked fifth and cut
    }
    monkeypatch.setattr(
        embedding_calculator.EmbeddingCalculator,
        "embed_texts",
        lambda texts, prefix: np.array([[2.0, 0.0]], dtype=np.float32),
    )
    results = engine.search_code("alpha", top_k=4)
    assert [(r["rel_path"], r["score"]) for r in results] == [
        ("a.py", 3.75),
        ("c.py", 0.375),
        ("d.py", 0.25),
        ("b.py", 0.125),
    ]


def test_full_text_search_matches_any_query_word(tmp_path: Path) -> None:
    storage = fts_storage(tmp_path, {"a.py": "alpha only", "b.py": "beta only", "c.py": "gamma"})
    assert set(storage.search_fts(["alpha", "beta"], 5)) == {"a.py", "b.py"}


def test_full_text_search_keeps_twice_top_k_candidates_for_the_merge(tmp_path: Path) -> None:
    # The candidates are merged with the TF-IDF and vector scores before the cut to top_k.
    storage = fts_storage(tmp_path, {f"{i}.py": "alpha" for i in range(3)})
    assert len(storage.search_fts(["alpha"], 1)) == 2


def test_a_token_that_is_not_a_plain_word_cannot_break_the_full_text_query(tmp_path: Path) -> None:
    storage = fts_storage(tmp_path, {"a.py": "alpha"})
    assert set(storage.search_fts(['alpha"', "alpha"], 5)) == {"a.py"}


def test_stored_embeddings_read_back_empty_from_an_index_older_than_their_table(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "context.db")
    conn.execute(
        "CREATE TABLE files (rel_path TEXT PRIMARY KEY, abs_path TEXT, size_bytes INTEGER)"
    )
    conn.close()
    assert SQLiteContextStorage(tmp_path / "context.db").load_embeddings() == {}


def test_reading_embeddings_from_a_missing_index_creates_no_database(tmp_path: Path) -> None:
    assert SQLiteContextStorage(tmp_path / "context.db").load_embeddings() == {}
    assert not (tmp_path / "context.db").exists()
