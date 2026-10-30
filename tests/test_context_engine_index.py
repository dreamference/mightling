"""What the context engine indexes, within which bounds, and how a rebuild replaces the old index.

Indexing runs beside a resident model server, so its memory is a safety property
(docs/dev/context-engine.md, "Memory is a safety property"): the bounds here are pinned by value,
not compared with the constants they would change together with.
"""

import subprocess
from pathlib import Path

import pytest

from dreamference.context_engine import ContextEngine, embedding_calculator

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
