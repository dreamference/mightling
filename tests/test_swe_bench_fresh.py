"""`scripts/swe_bench_fresh.py draw`: a later fresh list leaves out the earlier ones (FAILURES §9.4)."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("swe_bench_fresh", os.path.join(ROOT, "scripts", "swe_bench_fresh.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sample = tmp_path / "sample-100.txt"
    sample.write_text("# the sample\nacme__s-1\nacme__s-2\n")
    monkeypatch.setattr(module, "SAMPLE_100", sample)
    validated = {f"acme__t-{n}": {} for n in range(1, 9)} | {"acme__s-1": {}, "acme__s-2": {}}
    monkeypatch.setattr(module.SweBenchImages, "validated", classmethod(lambda cls: dict(validated)))
    monkeypatch.setattr(module, "strong_rates", lambda experiments: None)
    mismatch = tmp_path / "verified-mismatch-exclude.txt"
    mismatch.write_text("# PR-issue mismatches\nacme__t-8\nacme__m-1\n")
    monkeypatch.setattr(module, "MISMATCH_LIST", mismatch)
    return module


def _draw(module, tmp_path, count, exclude=None, out="fresh.txt", keep_mismatch=False):
    path = tmp_path / out
    code = module.draw(count, 7, path, tmp_path / "experiments", "a test", exclude, keep_mismatch)
    return code, path


def test_the_mismatch_list_is_out_by_default_and_named_in_the_header(fresh, tmp_path):
    code, out = _draw(fresh, tmp_path, 7)
    assert code == 0
    assert set(fresh.read_ids(out)) == {f"acme__t-{n}" for n in range(1, 8)}  # t-8 is a mismatch
    assert any("outside sample-100.txt and verified-mismatch-exclude.txt (2)" in line for line in out.read_text().splitlines())
    code, out = _draw(fresh, tmp_path, 8, keep_mismatch=True, out="all.txt")
    assert code == 0 and "acme__t-8" in fresh.read_ids(out)
    assert not any("mismatch" in line for line in out.read_text().splitlines() if line.startswith("#"))


def test_a_missing_mismatch_list_is_an_error_unless_kept(fresh, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fresh, "MISMATCH_LIST", tmp_path / "gone.txt")
    code, out = _draw(fresh, tmp_path, 4)
    assert code == 1 and not out.exists() and "gone.txt" in capsys.readouterr().out
    code, out = _draw(fresh, tmp_path, 4, keep_mismatch=True)
    assert code == 0


def test_a_later_list_leaves_out_the_earlier_fresh_lists_and_names_them(fresh, tmp_path):
    code, first = _draw(fresh, tmp_path, 4, out="fresh-4.txt")
    assert code == 0
    first_ids = set(fresh.read_ids(first))
    assert len(first_ids) == 4 and not first_ids & {"acme__s-1", "acme__s-2"}
    code, second = _draw(fresh, tmp_path, 3, exclude=[first], out="fresh-3.txt")
    assert code == 0
    second_ids = set(fresh.read_ids(second))
    assert len(second_ids) == 3 and not second_ids & first_ids and not second_ids & {"acme__s-1", "acme__s-2", "acme__t-8"}
    header = second.read_text().splitlines()[1]
    assert "outside sample-100.txt and verified-mismatch-exclude.txt (2), fresh-4.txt (4)" in header and "seed 7" in header


def test_too_few_tasks_outside_the_excluded_lists_is_refused(fresh, tmp_path, capsys):
    code, first = _draw(fresh, tmp_path, 5, out="fresh-5.txt")
    assert code == 0
    code, second = _draw(fresh, tmp_path, 4, exclude=[first], out="fresh-4.txt")
    assert code == 1 and not second.exists()
    assert ("Only 2 validated tasks lie outside sample-100.txt and verified-mismatch-exclude.txt (2), fresh-5.txt (5); "
            "4 are wanted") in capsys.readouterr().out


def test_an_empty_or_missing_exclude_list_is_an_error_not_a_silent_repeat(fresh, tmp_path, capsys):
    code, out = _draw(fresh, tmp_path, 4, exclude=[tmp_path / "missing.txt"])
    assert code == 1 and not out.exists()
    assert "--exclude" in capsys.readouterr().out
    (tmp_path / "empty.txt").write_text("# nothing but a comment\n")
    code, out = _draw(fresh, tmp_path, 4, exclude=[tmp_path / "empty.txt"])
    assert code == 1 and not out.exists()


def test_the_command_line_accepts_exclude_repeatedly(fresh, tmp_path):
    (tmp_path / "a.txt").write_text("acme__t-1\nacme__t-2\n")
    (tmp_path / "b.txt").write_text("acme__t-3\n")
    out = tmp_path / "c.txt"
    code = fresh.main(["draw", "--count", "5", "--seed", "1", "--out", str(out), "--experiments", str(tmp_path / "x"),
                       "--exclude", str(tmp_path / "a.txt"), "--exclude", str(tmp_path / "b.txt"), "--keep-mismatch"])
    assert code == 0
    assert set(fresh.read_ids(out)) == {f"acme__t-{n}" for n in range(4, 9)}
    assert any("outside sample-100.txt and a.txt (2), b.txt (1)" in line for line in out.read_text().splitlines())
