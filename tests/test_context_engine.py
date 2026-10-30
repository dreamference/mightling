import os
from pathlib import Path
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


def test_snake_case_and_camel_case_identifiers_are_split_into_their_words(tmp_path, monkeypatch):
    # Only camelCase was split: `get_user_name` stayed one token, and the full-text search drops a
    # token with an underscore as not alphanumeric, so a snake_case query never reached FTS5.
    from dreamference.context_engine import embedding_calculator
    from dreamference.context_engine.tfidf_calculator import TFIDFCalculator

    assert TFIDFCalculator.tokenize("getUserName") == ["get", "user", "name", "getusername"]
    assert TFIDFCalculator.tokenize("get_user_name") == ["get", "user", "name", "get_user_name"]
    assert TFIDFCalculator.tokenize("x = DreamferenceConfig") == ["dreamference", "config", "dreamferenceconfig"]

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)
    (tmp_path / "users.py").write_text("def get_user_name(user_id):\n    return lookup(user_id)\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert engine.sqlite_storage.search_fts(TFIDFCalculator.tokenize("get_user_name"), top_k=5)
    assert engine.search_code("user name")[0]["rel_path"] == "users.py"


def test_every_connection_to_the_context_database_maps_it_into_memory(tmp_path, monkeypatch):
    # Schema setup and full-text search set the pragma; reading the vectors back did not.
    import sqlite3

    import numpy as np

    from dreamference.context_engine import sqlite_context_storage
    from dreamference.context_engine.sqlite_context_storage import SQLiteContextStorage

    opened = []

    class Recording(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.statements = []
            opened.append(self)

        def execute(self, sql, *args):
            self.statements.append(sql)
            return super().execute(sql, *args)

    storage = SQLiteContextStorage(tmp_path / "context.db")
    storage.store_embeddings({"a.py": np.ones(4, dtype=np.float32)})
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite_context_storage.sqlite3, "connect",
                        lambda *args, **kwargs: real_connect(*args, factory=Recording, **kwargs))
    storage.init_db().close()
    storage.search_fts(["alpha"], top_k=5)
    storage.store_embeddings({"a.py": np.ones(4, dtype=np.float32)})
    assert set(storage.load_embeddings()) == {"a.py"}
    assert len(opened) == 4
    for conn in opened:
        assert conn.statements[0] == "PRAGMA mmap_size = 2147483648;"


def _keyword_only(monkeypatch):
    from dreamference.context_engine import embedding_calculator

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)


def test_python_symbols_are_classes_and_functions_with_bare_signatures(tmp_path, monkeypatch):
    # Spec §2: no bases, positional argument names only, methods are functions, names unqualified.
    _keyword_only(monkeypatch)
    (tmp_path / "shapes.py").write_text(
        "class Square(Shape, metaclass=Meta):\n"
        '    """A square."""\n'
        "    def area(self, scale: float = 1.0, *, exact=False) -> float:\n"
        "        return 1.0\n"
        "\n"
        "async def fetch(url, timeout=3):\n"
        '    """Fetches."""\n'
        "    return url\n"
    )
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    found = {s.name: s for s in engine.symbols}
    assert set(found) == {"Square", "area", "fetch"}
    assert (found["Square"].symbol_type, found["Square"].signature) == ("class", "class Square")
    assert (found["Square"].line_start, found["Square"].line_end) == (1, 4)
    assert found["Square"].docstring == "A square."
    assert (found["area"].symbol_type, found["area"].signature) == ("function", "def area(self, scale)")
    assert (found["fetch"].symbol_type, found["fetch"].signature) == ("function", "def fetch(url, timeout)")
    assert all(s.file_path == "shapes.py" for s in engine.symbols)


def test_other_languages_and_unparsable_python_have_no_symbols_but_are_still_searched(tmp_path, monkeypatch):
    _keyword_only(monkeypatch)
    (tmp_path / "lib.rs").write_text("fn tokenizer_frobnicate() {}\n")
    (tmp_path / "broken.py").write_text("def quuxify(:\n")
    (tmp_path / "fine.py").write_text("def ok():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    summary = engine.index_workspace(force_reindex=True)
    assert summary["total_indexed_files"] == 3
    assert [s.name for s in engine.symbols] == ["ok"]
    assert engine.search_code("frobnicate")[0]["rel_path"] == "lib.rs"
    assert engine.search_code("quuxify")[0]["rel_path"] == "broken.py"


def test_symlinks_and_ignored_extensions_are_not_indexed(tmp_path, monkeypatch):
    _keyword_only(monkeypatch)
    (tmp_path / "real.py").write_text("x = 1\n")
    (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")
    (tmp_path / "logo.png").write_text("not really a picture\n")
    (tmp_path / "module.pyc").write_text("compiled\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    assert [p.name for p in engine.collect_files()] == ["real.py"]


def test_a_submodule_is_not_entered(tmp_path, monkeypatch):
    import subprocess

    _keyword_only(monkeypatch)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "protocol.file.allow=always"]
    vendor = tmp_path / "vendor_src"
    vendor.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=vendor, check=True)
    (vendor / "inside.py").write_text("x = 1\n")
    subprocess.run([*git, "add", "."], cwd=vendor, check=True)
    subprocess.run([*git, "commit", "-qm", "vendor"], cwd=vendor, check=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "app.py").write_text("x = 2\n")
    subprocess.run([*git, "submodule", "add", "-q", str(vendor), "vendor"], cwd=workspace, check=True,
                   capture_output=True)
    assert (workspace / "vendor" / "inside.py").is_file()
    engine = ContextEngine(workspace_root=str(workspace))
    assert sorted(p.name for p in engine.collect_files()) == [".gitmodules", "app.py"]


def test_a_full_reindex_replaces_every_table_of_the_previous_run(tmp_path, monkeypatch):
    import sqlite3

    import numpy as np

    from dreamference.context_engine import embedding_calculator

    class FakeModel:
        def encode(self, texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True):
            return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", FakeModel())
    (tmp_path / "keep.py").write_text("def keep():\n    pass\n")
    (tmp_path / "gone.py").write_text("def gone():\n    pass\n")
    ContextEngine(workspace_root=str(tmp_path)).index_workspace(force_reindex=True)
    (tmp_path / "gone.py").unlink()
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    conn = sqlite3.connect(engine.sqlite_file)
    tables = {
        "files": "SELECT rel_path FROM files",
        "symbols": "SELECT file_path FROM symbols",
        "fts_context": "SELECT rel_path FROM fts_context",
        "embeddings": "SELECT rel_path FROM embeddings",
    }
    for table, query in tables.items():
        assert {row[0] for row in conn.execute(query)} == {"keep.py"}, table
    conn.close()


def test_without_force_a_saved_index_is_reused_without_reading_the_files(tmp_path, monkeypatch):
    _keyword_only(monkeypatch)
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    ContextEngine(workspace_root=str(tmp_path)).index_workspace(force_reindex=True)
    engine = ContextEngine(workspace_root=str(tmp_path))
    monkeypatch.setattr(engine, "collect_files", lambda: (_ for _ in ()).throw(AssertionError("re-read")))
    summary = engine.index_workspace()
    assert (summary["total_indexed_files"], summary["total_ast_symbols"]) == (1, 1)
    assert engine.search_code("alpha")[0]["rel_path"] == "a.py"


def test_a_search_result_is_the_file_with_all_its_symbols_and_top_k_bounds_the_list(tmp_path, monkeypatch):
    _keyword_only(monkeypatch)
    for i in range(4):
        (tmp_path / f"m{i}.py").write_text(f"def shared_{i}():\n    pass\n\ndef other_{i}():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    results = engine.search_code("shared", top_k=2)
    assert len(results) == 2
    assert [r["score"] for r in results] == sorted((r["score"] for r in results), reverse=True)
    for result in results:
        assert set(result) == {"rel_path", "score", "symbols", "size_bytes"}
        assert len(result["symbols"]) == 2
    assert len(engine.search_code("shared")) == 4


def test_documents_are_encoded_in_batches_of_eight_and_normalised(tmp_path, monkeypatch):
    import numpy as np

    from dreamference.context_engine import embedding_calculator

    calls = []

    class FakeModel:
        def encode(self, texts, batch_size=32, normalize_embeddings=False, convert_to_numpy=False):
            calls.append((batch_size, normalize_embeddings))
            return np.ones((len(texts), 2), dtype=np.float32)

    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", FakeModel())
    vectors = embedding_calculator.EmbeddingCalculator.compute_embeddings({"a.py": "x", "b.py": "y"})
    assert calls == [(8, True)]
    assert set(vectors) == {"a.py", "b.py"} and vectors["a.py"].dtype == np.float32


def test_a_model_that_fails_to_load_is_reported_once_and_search_goes_keyword_only(tmp_path, monkeypatch, capsys):
    # An offline machine without the model cached: loading fails, indexing and search carry on.
    from dreamference.context_engine import embedding_calculator

    attempts = []

    class OfflineSentenceTransformer:
        def __init__(self, *args, **kwargs):
            attempts.append(args)
            raise OSError("no network")

    monkeypatch.setattr(embedding_calculator, "SentenceTransformer", OfflineSentenceTransformer)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", None)
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", False)
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    engine.index_workspace(force_reindex=True)
    assert engine.search_code("alpha")[0]["rel_path"] == "a.py"
    assert len(attempts) == 1
    assert engine.file_embeddings == {}
    out = capsys.readouterr().out
    assert out.count(f"Embedding model {embedding_calculator.EMBEDDING_MODEL} is unavailable") == 1


def test_the_parse_pool_is_sized_to_the_work(tmp_path, monkeypatch):
    # Each worker is a fork of a parent that has imported torch: one per 32 files, at most 8.
    from concurrent.futures import ThreadPoolExecutor

    from dreamference.context_engine import context_engine

    _keyword_only(monkeypatch)
    sizes = []

    def pool(max_workers):
        sizes.append(max_workers)
        return ThreadPoolExecutor(max_workers=max_workers)

    monkeypatch.setattr(context_engine, "ProcessPoolExecutor", pool)
    monkeypatch.setattr(context_engine.os, "cpu_count", lambda: 64)
    for count in (3, 70, 400):
        workspace = tmp_path / str(count)
        workspace.mkdir()
        for i in range(count):
            (workspace / f"f{i}.txt").write_text("x\n")
        ContextEngine(workspace_root=str(workspace)).index_workspace(force_reindex=True)
    assert sizes == [1, 3, 8]
