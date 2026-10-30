"""Failure paths of the scratch accessors the host uses on a directory an image has written."""

import os
from pathlib import Path

import pytest

from dreamference.swe_bench.swe_bench_scratch import SweBenchScratch


def test_a_failed_write_raises_and_leaves_neither_a_staging_file_nor_the_target(
    tmp_path, monkeypatch
):
    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        SweBenchScratch.write_text(tmp_path, "out.txt", "text\n")
    assert list(tmp_path.iterdir()) == []


def test_a_failed_write_keeps_the_previous_file(tmp_path, monkeypatch):
    (tmp_path / "out.txt").write_text("old\n")

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        SweBenchScratch.write_text(tmp_path, "out.txt", "new\n")
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]
    assert (tmp_path / "out.txt").read_text() == "old\n"


def test_a_listing_that_fails_midway_returns_what_it_has_and_closes_the_directory(
    tmp_path, monkeypatch
):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.log").write_text("x")
    before = len(list(Path("/proc/self/fd").iterdir()))

    def broken(*args, **kwargs):
        raise OSError("unreadable")
        yield  # pragma: no cover

    monkeypatch.setattr(os, "fwalk", broken)
    assert SweBenchScratch.files(tmp_path, "sub", "*.log") == []
    assert len(list(Path("/proc/self/fd").iterdir())) == before
