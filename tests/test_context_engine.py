import os
from pathlib import Path

import pytest

from dreamference.context_engine import ContextEngine

def test_context_engine_indexing(tmp_path, monkeypatch):
    # Index and search without the dense model: loading nomic-embed-text downloads it and runs its
    # remote code, which depends on the installed transformers and on network access. The engine
    # falls back to zero vectors when no model is available, and the lexical ranking still has to
    # find the symbol.
    from dreamference.context_engine import embedding_calculator
    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
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


def test_embeddings_survive_a_restart_and_use_the_nomic_prefixes(tmp_path, monkeypatch):
    # Vectors used to be written to a sqlite-vec table that was never created (the extension was
    # not loaded), keyed by hash(path), which is randomised per process -- so a new process always
    # searched by keyword only. They now live in a plain table and load_index reads them back.
    import numpy as np
    from dreamference.context_engine import embedding_calculator

    seen = []

    class FakeModel:
        # One dimension per concept, so meaning can match with no shared keyword.
        def encode(self, texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True):
            seen.extend(texts)
            rows = [[1.0 if any(w in t for w in ("login", "password", "authenticate")) else 0.0, 1.0] for t in texts]
            vectors = np.array(rows, dtype=np.float32)
            return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", FakeModel())
    (tmp_path / "auth.py").write_text("def authenticate(user, password):\n    return True\n")
    (tmp_path / "math_utils.py").write_text("def add(a, b):\n    return a + b\n")
    ContextEngine(workspace_root=str(tmp_path)).index_workspace(force_reindex=True)
    assert any(t.startswith("search_document: ") for t in seen)

    restarted = ContextEngine(workspace_root=str(tmp_path))
    assert restarted.load_index()
    assert set(restarted.file_embeddings) == {"auth.py", "math_utils.py"}
    results = restarted.search_code("login")  # no file contains the word "login"
    assert results and results[0]["rel_path"] == "auth.py"
    assert any(t.startswith("search_query: ") for t in seen)


def test_indexing_without_an_embedding_model_is_keyword_only_not_zero_vectors(tmp_path, monkeypatch):
    from dreamference.context_engine import embedding_calculator

    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert engine.file_embeddings == {}
    assert engine.search_code("alpha")[0]["rel_path"] == "a.py"


def test_tf_idf_survives_a_restart(tmp_path, monkeypatch):
    # Token lists are not persisted, so a cached load used to come back with empty TF-IDF tables
    # and scored by FTS and embeddings alone. An index written before the tables were saved is
    # treated as stale and rebuilt.
    import json
    from dreamference.context_engine import embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    (tmp_path / "auth.py").write_text("def authenticate(user, password):\n    return True\n")
    (tmp_path / "math_utils.py").write_text("def add(a, b):\n    return a + b\n")
    built = ContextEngine(workspace_root=str(tmp_path))
    built.index_workspace(force_reindex=True)

    restarted = ContextEngine(workspace_root=str(tmp_path))
    assert restarted.load_index()
    assert restarted.tf_idf_index == built.tf_idf_index
    assert "auth.py" in restarted.tf_idf_index["authenticate"]

    data = json.loads(restarted.index_file.read_text())
    del data["tf_idf_index"]
    restarted.index_file.write_text(json.dumps(data))
    assert not ContextEngine(workspace_root=str(tmp_path)).load_index()


def test_a_git_workspace_indexes_what_git_tracks_and_skips_large_and_binary_files(tmp_path, monkeypatch):
    # Indexing walked everything not in IGNORE_DIRS: on this repo, a Tauri `target/` (2.2 GB) and
    # the codex submodule -- 11,565 files. Holding that in memory beside a resident vLLM put the
    # host under earlyoom's limit, and earlyoom killed vLLM.
    import subprocess
    from dreamference.context_engine import context_engine, embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("generated/\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "bundle.js").write_text("var x = 1;\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "build.rs").write_text("fn main() {}\n")
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "untracked_but_not_ignored.py").write_text("x = 1\n")
    (tmp_path / "huge.txt").write_text("a" * (context_engine.MAX_FILE_BYTES + 1))
    (tmp_path / "blob.bin").write_bytes(b"ELF\0\0\0binary")
    subprocess.run(["git", "add", "app.py", ".gitignore"], cwd=tmp_path, check=True)

    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {".gitignore", "app.py", "untracked_but_not_ignored.py"}


def test_outside_git_the_walk_skips_build_output(tmp_path, monkeypatch):
    from dreamference.context_engine import embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    (tmp_path / "target" / "release").mkdir(parents=True)
    (tmp_path / "target" / "release" / "out.rs").write_text("fn main() {}\n")
    (tmp_path / "lib.py").write_text("x = 1\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    assert [p.name for p in engine.collect_files()] == ["lib.py"]


def test_the_embedding_model_runs_on_the_cpu_with_bounded_inputs(monkeypatch):
    # On the GPU it failed with CUDA out of memory beside a resident vLLM (search went
    # keyword-only), and at full length it pushed the host towards earlyoom's limit.
    from dreamference.context_engine import embedding_calculator

    made = []

    class FakeSentenceTransformer:
        def __init__(self, name, trust_remote_code=True, device=None):
            made.append(device)
            self.max_seq_length = 8192

    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", FakeSentenceTransformer)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", False)
    model = embedding_calculator.EmbeddingCalculator._get_model()
    assert made == ["cpu"]
    assert model.max_seq_length == embedding_calculator.MAX_SEQUENCE_TOKENS


def test_a_workspace_inside_a_directory_named_like_build_output_is_indexed(tmp_path, monkeypatch):
    # The ignore list was matched against every part of the absolute path, so a checkout under a
    # `build/`, `dist/` or `target/` directory indexed nothing at all.
    from dreamference.context_engine import embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    workspace = tmp_path / "build" / "project"
    (workspace / "target").mkdir(parents=True)
    (workspace / "target" / "out.rs").write_text("fn main() {}\n")
    (workspace / "lib.py").write_text("def alpha():\n    pass\n")
    engine = ContextEngine(workspace_root=str(workspace))
    assert [p.name for p in engine.collect_files()] == ["lib.py"]
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {"lib.py"}


def test_the_full_text_score_is_highest_for_the_strongest_match(tmp_path):
    # FTS5's rank is bm25 negated: the best match is the most negative. Its magnitude was then
    # inverted a second time, so the strongest match scored lowest.
    from dreamference.context_engine.sqlite_context_storage import SQLiteContextStorage

    storage = SQLiteContextStorage(tmp_path / "context.db")
    conn = storage.init_db()
    with conn:
        conn.execute("INSERT INTO fts_context VALUES (?, ?);", ("strong.py", "alpha alpha alpha alpha alpha beta"))
        conn.execute("INSERT INTO fts_context VALUES (?, ?);",
                     ("weak.py", "alpha gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"))
        for i in range(8):
            conn.execute("INSERT INTO fts_context VALUES (?, ?);", (f"other{i}.py", "nothing here at all"))
    conn.close()
    scores = storage.search_fts(["alpha"], top_k=5)
    assert scores["strong.py"] > scores["weak.py"] > 0


def test_a_failed_index_save_leaves_the_previous_index_whole(tmp_path, monkeypatch):
    # `context_index.json` was rewritten in place: a reader (`ling-admin mcp`, the web canvas's
    # status) catching it half-written, or a kill mid-write, found unreadable JSON, and `load_index`
    # failing means a full re-index beside the model server.
    from dreamference.context_engine import context_engine, embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    before = engine.index_file.read_bytes()

    def dump_then_die(data, handle, **kwargs):
        handle.write('{"workspace_root": ')
        raise OSError("No space left on device")

    monkeypatch.setattr(context_engine.json, "dump", dump_then_die)
    with pytest.raises(OSError):
        engine.save_index()
    assert engine.index_file.read_bytes() == before
    assert sorted(p.name for p in engine.dreamference_dir.iterdir()) == ["context.db", "context_index.json"]


def test_each_worker_task_carries_a_path_not_the_whole_engine(tmp_path, monkeypatch):
    # The pool was mapped over a bound method, so every chunk pickled the engine with every file
    # indexed so far (tokens included), while the loop was still adding to it: the parent's memory
    # and the copying grew with the square of the workspace, beside a resident model server.
    import pickle

    from dreamference.context_engine import context_engine, embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    pickled = []

    class InlinePool:
        def __init__(self, max_workers=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, items, chunksize=1):
            # As the real pool does: a task is pickled when a worker is free for it, by then the
            # results before it have been consumed.
            for item in items:
                pickled.append(len(pickle.dumps(fn)))
                yield fn(item)

    monkeypatch.setattr(context_engine, "ProcessPoolExecutor", InlinePool)
    for i in range(3):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n" + "word " * 5_000)
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {"m0.py", "m1.py", "m2.py"}
    assert len(pickled) == 3 and max(pickled) < 4096


def test_snake_case_names_are_split_into_their_words():
    # Only camelCase was split, so `get_user_name` was one token and a search for `user` missed it
    # (spec §2 promises both).
    from dreamference.context_engine.tfidf_calculator import TFIDFCalculator

    tokens = TFIDFCalculator.tokenize("def get_user_name(): pass\nclass UserConfig_v2: pass")
    assert {"get", "user", "name", "get_user_name"} <= set(tokens)
    assert {"config", "v2", "userconfig_v2"} <= set(tokens)


def test_a_failed_full_text_search_closes_its_connection(tmp_path, monkeypatch):
    # `close()` sat inside the `try`, so a search that raised (a malformed MATCH, a locked or
    # damaged database) left its connection open for the life of the MCP server.
    from unittest.mock import MagicMock

    from dreamference.context_engine import sqlite_context_storage
    from dreamference.context_engine.sqlite_context_storage import SQLiteContextStorage

    (tmp_path / "context.db").write_bytes(b"")
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = sqlite_context_storage.sqlite3.OperationalError("locked")
    monkeypatch.setattr(sqlite_context_storage.sqlite3, "connect", lambda *a, **k: conn)
    assert SQLiteContextStorage(tmp_path / "context.db").search_fts(["alpha"], top_k=5) == {}
    conn.close.assert_called_once()


def test_a_search_returns_top_k_files_even_when_a_scored_path_is_no_longer_indexed(tmp_path):
    # The ranking was cut to `top_k` before paths missing from `indexed_files` were dropped, so a
    # stale path among the best scores left the caller with fewer results than it asked for.
    from dreamference.context_engine.indexed_file import IndexedFile

    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.indexed_files = {"a.py": IndexedFile(rel_path="a.py", abs_path=str(tmp_path / "a.py"),
                                                size_bytes=1, symbols=[], tokens=[])}
    engine.tf_idf_index = {"alpha": {"gone.py": 5.0, "a.py": 1.0}}
    assert [r["rel_path"] for r in engine.search_code("alpha", top_k=1)] == ["a.py"]
