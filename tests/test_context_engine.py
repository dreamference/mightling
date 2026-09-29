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
