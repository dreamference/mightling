"""How the context engine ranks files: full-text, TF-IDF and vector scores, and what survives a restart."""

from pathlib import Path

import numpy as np
import pytest

from dreamference.context_engine import ContextEngine, embedding_calculator
from dreamference.context_engine.indexed_file import IndexedFile


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
