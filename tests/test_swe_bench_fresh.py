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


def _draw(module, tmp_path, count, exclude=None, out="fresh.txt", keep_mismatch=False, reuse=None):
    path = tmp_path / out
    code = module.draw(count, 7, path, tmp_path / "experiments", "a test", exclude, keep_mismatch, reuse)
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


def test_the_stratification_target_is_the_sample_alone_not_the_excluded_lists(fresh, tmp_path, monkeypatch):
    # The two sample tasks are easy; the mismatch list and an earlier fresh list are all hard. The
    # target mix must stay the sample's (all easy), whatever the excluded lists hold.
    rates = {"acme__s-1": 1.0, "acme__s-2": 1.0} | {f"acme__t-{n}": 0.0 for n in range(1, 9)} | {"acme__m-1": 0.0}
    rates |= {"acme__t-5": 1.0, "acme__t-6": 1.0}
    monkeypatch.setattr(fresh, "strong_rates", lambda experiments: rates)
    (tmp_path / "earlier.txt").write_text("acme__t-1\nacme__t-2\n")
    code, out = _draw(fresh, tmp_path, 2, exclude=[tmp_path / "earlier.txt"])
    assert code == 0
    assert set(fresh.read_ids(out)) == {"acme__t-5", "acme__t-6"}
    header = out.read_text()
    assert "easy/medium/hard 2/0/0 of 2" in header and "wanted 2/0/0, drawn 2/0/0." in header


def test_the_command_line_accepts_exclude_repeatedly(fresh, tmp_path):
    (tmp_path / "a.txt").write_text("acme__t-1\nacme__t-2\n")
    (tmp_path / "b.txt").write_text("acme__t-3\n")
    out = tmp_path / "c.txt"
    code = fresh.main(["draw", "--count", "5", "--seed", "1", "--out", str(out), "--experiments", str(tmp_path / "x"),
                       "--exclude", str(tmp_path / "a.txt"), "--exclude", str(tmp_path / "b.txt"), "--keep-mismatch"])
    assert code == 0
    assert set(fresh.read_ids(out)) == {f"acme__t-{n}" for n in range(4, 9)}
    assert any("outside sample-100.txt and a.txt (2), b.txt (1)" in line for line in out.read_text().splitlines())


# -- the rotation rule (2026-10-14): tasks of lists used three or more nights earlier may recur ----

def test_a_reused_list_stays_in_the_pool_and_the_header_counts_what_came_from_it(fresh, tmp_path, capsys):
    old = tmp_path / "fresh-50.txt"
    old.write_text("# night 1\nacme__t-1\nacme__t-2\nacme__t-8\n")   # t-8 is also a mismatch: still out
    older = tmp_path / "fresh-50b.txt"
    older.write_text("# nights 3-5\nacme__t-3\n")
    last = tmp_path / "fresh-50e.txt"
    last.write_text("# night 9\nacme__t-4\n")
    code, path = _draw(fresh, tmp_path, 5, exclude=[last], reuse=[old, older])
    assert code == 0
    chosen = set(fresh.read_ids(path))
    assert len(chosen) == 5 and chosen <= {"acme__t-1", "acme__t-2", "acme__t-3", "acme__t-5", "acme__t-6", "acme__t-7"}
    assert "acme__t-4" not in chosen and "acme__t-8" not in chosen
    header = path.read_text().splitlines()
    assert header[0].startswith("# 5 SWE-bench Verified tasks for a test") and "fresh" not in header[0]
    assert any("outside sample-100.txt and verified-mismatch-exclude.txt (2), fresh-50e.txt (1)" in line for line in header)
    assert any(line.startswith("# Reuse (the rotation rule of 2026-10-14, SWE_BENCH spec §16): the tasks of fresh-50.txt (3), "
                               "fresh-50b.txt (1) stay in the pool; the excluded lists win") for line in header)
    from_old = len(chosen & {"acme__t-1", "acme__t-2"})
    from_older = len(chosen & {"acme__t-3"})
    assert any(line == f"# Drawn: {from_old} from fresh-50.txt, {from_older} from fresh-50b.txt, "
                       f"{5 - from_old - from_older} in none of them." for line in header)
    assert f"({from_old} from fresh-50.txt, {from_older} from fresh-50b.txt" in capsys.readouterr().out


def test_without_reuse_the_draw_and_its_header_are_as_before(fresh, tmp_path):
    code, path = _draw(fresh, tmp_path, 3)
    assert code == 0
    header = path.read_text().splitlines()
    assert header[0].startswith("# 3 fresh SWE-bench Verified tasks for a test")
    assert not any("Reuse" in line or line.startswith("# Drawn:") for line in header)


def test_a_list_both_excluded_and_reused_is_refused(fresh, tmp_path, capsys):
    old = tmp_path / "fresh-50.txt"
    old.write_text("acme__t-1\n")
    code, path = _draw(fresh, tmp_path, 3, exclude=[old], reuse=[old])
    assert code == 1 and not path.exists()
    assert "the same list is excluded" in capsys.readouterr().out


def test_a_missing_or_empty_reuse_list_is_an_error(fresh, tmp_path, capsys):
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    for path in (tmp_path / "missing.txt", empty):
        code, out = _draw(fresh, tmp_path, 3, reuse=[path])
        assert code == 1 and not out.exists()
        assert f"--reuse-from {path}: no task ids found" in capsys.readouterr().out


def test_the_command_line_accepts_reuse_from_repeatedly(fresh, tmp_path):
    (tmp_path / "a.txt").write_text("acme__t-1\n")
    (tmp_path / "b.txt").write_text("acme__t-2\n")
    (tmp_path / "last.txt").write_text("acme__t-3\n")
    out = tmp_path / "c.txt"
    code = fresh.main(["draw", "--count", "4", "--seed", "1", "--out", str(out), "--experiments", str(tmp_path / "x"),
                       "--exclude", str(tmp_path / "last.txt"), "--reuse-from", str(tmp_path / "a.txt"),
                       "--reuse-from", str(tmp_path / "b.txt"), "--keep-mismatch"])
    assert code == 0
    assert "acme__t-3" not in fresh.read_ids(out)
    assert any("the tasks of a.txt (1), b.txt (1) stay in the pool" in line for line in out.read_text().splitlines())
