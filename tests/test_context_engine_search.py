"""How the context engine ranks files: full-text, TF-IDF and vector scores, and what survives a restart."""

import sqlite3
import sys
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


def test_symbols_sizes_and_the_idf_table_survive_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    (tmp_path / "auth.py").write_text(
        "class Login:\n    def check(self, password):\n        pass\n"
    )
    (tmp_path / "notes.txt").write_text("login notes\n")
    built = ContextEngine(workspace_root=str(tmp_path))
    built.index_workspace(force_reindex=True)

    restarted = ContextEngine(workspace_root=str(tmp_path))
    assert restarted.load_index()
    assert restarted.idf_table == built.idf_table
    assert {r: (f.size_bytes, f.symbols) for r, f in restarted.indexed_files.items()} == {
        r: (f.size_bytes, f.symbols) for r, f in built.indexed_files.items()
    }


class RecordingModel:
    """A stand-in for the sentence-transformers model that records how it is built and called."""

    def __init__(self, name: str, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.encoded: list[dict[str, object]] = []
        self.max_seq_length = 8192

    def encode(self, texts: list[str], **kwargs: object) -> np.ndarray:
        self.encoded.append({"texts": texts, **kwargs})
        return np.ones((len(texts), 2), dtype=np.float64)


@pytest.fixture
def recording_model(monkeypatch: pytest.MonkeyPatch) -> RecordingModel:
    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", RecordingModel)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", False)
    model = embedding_calculator.EmbeddingCalculator._get_model()
    assert isinstance(model, RecordingModel)
    return model


def test_the_model_reads_at_most_1024_tokens_of_a_file(recording_model: RecordingModel) -> None:
    # Attention memory grows with the square of the sequence; this is the memory bound, by value.
    assert recording_model.max_seq_length <= 1024


def test_the_model_runs_its_pinned_remote_code(recording_model: RecordingModel) -> None:
    # nomic-embed-text does not load without its modeling code; the code is pinned to a commit.
    assert recording_model.kwargs.get("trust_remote_code") is True


def test_texts_are_encoded_in_normalised_batches_of_at_most_eight_as_float32(
    recording_model: RecordingModel,
) -> None:
    vectors = embedding_calculator.EmbeddingCalculator.embed_texts(["a", "b"])
    assert vectors is not None
    assert vectors.dtype == np.float32
    (call,) = recording_model.encoded
    assert isinstance(call["batch_size"], int)
    assert call["batch_size"] <= 8
    assert call["normalize_embeddings"] is True


def test_no_files_means_no_call_to_the_model(recording_model: RecordingModel) -> None:
    assert embedding_calculator.EmbeddingCalculator.compute_embeddings({}) == {}
    assert recording_model.encoded == []


def test_a_model_that_fails_to_load_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each attempt can download the model or leave memory behind; one failure is final.
    attempts = []

    def failing_model(name: str, **kwargs: object) -> None:
        attempts.append(name)
        raise OSError("no network")

    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", failing_model)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", False)
    assert embedding_calculator.EmbeddingCalculator._get_model() is None
    assert embedding_calculator.EmbeddingCalculator._get_model() is None
    assert len(attempts) == 1


def test_without_sentence_transformers_installed_search_is_keyword_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        embedding_calculator, "SentenceTransformer", embedding_calculator._NOT_LOADED
    )
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    assert embedding_calculator.EmbeddingCalculator._get_model() is None
    assert embedding_calculator.SentenceTransformer is None
