"""What the context engine indexes, within which bounds, and how a rebuild replaces the old index.

Indexing runs beside a resident model server, so its memory is a safety property
(docs/dev/context-engine.md, "Memory is a safety property"): the bounds here are pinned by value,
not compared with the constants they would change together with.
"""

import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dreamference.context_engine import ContextEngine, context_engine, embedding_calculator

ONE_MIB = 1024 * 1024
SNIFF_BYTES = 8192
IGNORED_DIRECTORIES = (
    "target",
    ".cargo",
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    ".idea",
    ".vscode",
)


@pytest.fixture
def keyword_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_unavailable", True)


class OnesModel:
    """An embedding model that gives every text the same unit vector."""

    def encode(self, texts: list[str], **kwargs: object) -> np.ndarray:
        return np.full((len(texts), 2), 2**-0.5, dtype=np.float32)


def names(paths: list[Path]) -> list[str]:
    return sorted(path.name for path in paths)


@pytest.mark.usefixtures("keyword_only")
def test_the_walk_skips_every_build_cache_and_version_control_directory(tmp_path: Path) -> None:
    for directory in IGNORED_DIRECTORIES:
        (tmp_path / "pkg" / directory).mkdir(parents=True)
        (tmp_path / "pkg" / directory / "inside.py").write_text("x = 1\n")
    (tmp_path / "lib.py").write_text("x = 1\n")
    assert names(ContextEngine(workspace_root=str(tmp_path)).collect_files()) == ["lib.py"]


@pytest.mark.usefixtures("keyword_only")
def test_binary_and_archive_extensions_are_skipped_even_when_their_bytes_look_like_text(
    tmp_path: Path,
) -> None:
    for name in ("logo.png", "photo.jpg", "native.so", "bundle.zip", "cached.pyc"):
        (tmp_path / name).write_text("not really binary\n")
    (tmp_path / "lib.py").write_text("x = 1\n")
    assert names(ContextEngine(workspace_root=str(tmp_path)).collect_files()) == ["lib.py"]


@pytest.mark.usefixtures("keyword_only")
def test_a_file_of_exactly_one_mib_is_indexed_and_one_byte_more_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "edge.txt").write_text("a\n" * (ONE_MIB // 2))
    (tmp_path / "over.txt").write_text("a\n" * (ONE_MIB // 2) + "a")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {"edge.txt"}


@pytest.mark.usefixtures("keyword_only")
def test_a_nul_in_the_first_8_kib_marks_a_file_binary_and_a_later_one_does_not(
    tmp_path: Path,
) -> None:
    (tmp_path / "early.txt").write_bytes(b"a" * (SNIFF_BYTES - 1) + b"\0")
    (tmp_path / "late.txt").write_bytes(b"a" * SNIFF_BYTES + b"\0 text\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {"late.txt"}


@pytest.mark.usefixtures("keyword_only")
def test_a_file_that_is_not_utf8_is_indexed_without_its_undecodable_bytes(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(b"def caf\xe9_order():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    assert set(engine.indexed_files) == {"latin.py"}
    assert "caf_order" in engine.tf_idf_index


@pytest.mark.usefixtures("keyword_only")
def test_a_symlink_is_not_followed_out_of_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("not this workspace's\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (workspace / "lib.py").write_text("x = 1\n")
    assert names(ContextEngine(workspace_root=str(workspace)).collect_files()) == ["lib.py"]


@pytest.mark.usefixtures("keyword_only")
def test_in_a_git_workspace_build_output_git_does_not_ignore_is_still_skipped(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "build.rs").write_text("fn main() {}\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    assert names(engine.collect_files()) == ["app.py"]
    # The parser checks again, so a caller that lists files itself cannot index build output.
    assert engine._process_single_file(tmp_path / "target" / "build.rs") is None


@pytest.mark.usefixtures("keyword_only")
@pytest.mark.parametrize(
    ("file_count", "cpu_count", "workers"),
    [(3, 64, 1), (64, 64, 3), (1000, 64, 8), (1000, 2, 2), (1000, None, 4)],
)
def test_the_parse_pool_is_sized_to_the_work_and_capped_at_eight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_count: int,
    cpu_count: int | None,
    workers: int,
) -> None:
    # Each worker is a fork of a parent that has imported torch.
    sizes = []

    class RecordingPool:
        def __init__(self, max_workers: int) -> None:
            sizes.append(max_workers)

        def __enter__(self) -> "RecordingPool":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def map(self, function: object, items: list[Path], chunksize: int = 1) -> list[None]:
            return [None for _ in items]

    engine = ContextEngine(workspace_root=str(tmp_path))
    monkeypatch.setattr(context_engine, "ProcessPoolExecutor", RecordingPool)
    monkeypatch.setattr(context_engine.os, "cpu_count", lambda: cpu_count)
    paths = [tmp_path / f"f{i}.py" for i in range(file_count)]
    monkeypatch.setattr(engine, "collect_files", lambda: paths)
    engine.index_workspace(force_reindex=True)
    assert sizes == [workers]


@pytest.mark.usefixtures("keyword_only")
def test_a_cached_index_is_loaded_unless_a_rebuild_is_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    ContextEngine(workspace_root=str(tmp_path)).index_workspace(force_reindex=True)
    (tmp_path / "b.py").write_text("def beta():\n    pass\n")

    cached = ContextEngine(workspace_root=str(tmp_path))

    def no_walk() -> list[Path]:
        raise AssertionError("a cached index is not rebuilt")

    monkeypatch.setattr(cached, "collect_files", no_walk)
    assert cached.index_workspace()["total_indexed_files"] == 1

    forced = ContextEngine(workspace_root=str(tmp_path))
    assert forced.index_workspace(force_reindex=True)["total_indexed_files"] == 2


def test_a_forced_rebuild_forgets_a_deleted_file_in_memory_and_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding_calculator.EmbeddingCalculator, "_model", OnesModel())
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
    (tmp_path / "gone.py").write_text("def vanished():\n    pass\n")
    engine = ContextEngine(workspace_root=str(tmp_path))
    engine.index_workspace(force_reindex=True)
    (tmp_path / "gone.py").unlink()
    engine.index_workspace(force_reindex=True)

    assert set(engine.indexed_files) == {"a.py"}
    assert {symbol.file_path for symbol in engine.symbols} == {"a.py"}
    conn = sqlite3.connect(engine.sqlite_file)
    try:
        assert conn.execute("SELECT rel_path FROM files").fetchall() == [("a.py",)]
        assert conn.execute("SELECT DISTINCT file_path FROM symbols").fetchall() == [("a.py",)]
    finally:
        conn.close()
    storage = engine.sqlite_storage
    assert storage.search_fts(["vanished"], 5) == {}
    assert set(storage.search_fts(["alpha"], 5)) == {"a.py"}
    assert set(storage.load_embeddings()) == {"a.py"}
