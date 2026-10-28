"""`scripts/release_benchmark.py`: a release's published benchmark entry and the page rendered from it."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location(
        "release_benchmark", ROOT / "scripts" / "release_benchmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(**over):
    entry = {
        "version": "1.8.0",
        "benchmark": "SWE-bench Verified",
        "night": "21",
        "tasks": 500,
        "solved": 330,
        "previous_version": "1.7.2",
        "previous_solved": 322,
        "p_value": 0.31,
        "wall_hours": 11.5,
    }
    entry.update(over)
    return entry


def test_committed_entries_are_valid_and_the_page_is_in_sync(bench):
    items = bench.entries()
    assert (ROOT / bench.PAGE).read_text(encoding="utf-8") == bench.render(items)


def test_page_shows_numbers_and_the_caveat(bench):
    page = bench.render([bench.validate(_entry(), "1.8.0")])
    assert (
        "330 of 500" in page
        and "1.7.2: 322 of 500" in page
        and "+8" in page
        and "0.310" in page
        and "11.5 h" in page
    )
    assert bench.CAVEAT in page


def test_missing_p_value_is_said(bench):
    assert "not available" in bench.render([bench.validate(_entry(p_value=None), "1.8.0")])


@pytest.mark.parametrize(
    "over",
    [
        {"night": ""},
        {"night": None},
        {"solved": 501},
        {"p_value": 2},
        {"wall_hours": 0},
        {"night": "node/home"},
        {"benchmark": "run on /home/x"},
        {"benchmark": "me@host"},
        {"benchmark": "10.0.0.1"},
    ],
)
def test_bad_entries_are_refused(bench, over):
    with pytest.raises(bench.BenchmarkError):
        bench.validate(_entry(**over), "1.8.0")


def test_file_name_must_match_version(bench):
    with pytest.raises(bench.BenchmarkError):
        bench.validate(_entry(), "1.9.0")


def test_record_requires_the_night_and_writes_entry_and_page(bench, tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    common = [
        "record",
        "--version",
        "1.8.0",
        "--tasks",
        "500",
        "--solved",
        "330",
        "--previous-version",
        "1.7.2",
        "--previous-solved",
        "322",
        "--wall-hours",
        "11.5",
    ]
    with pytest.raises(SystemExit):
        bench.main(common)
    assert bench.main([*common, "--night", "21"]) == 0
    assert json.loads((tmp_path / "docs/releases/1.8.0.json").read_text())["night"] == "21"
    assert "330 of 500" in (tmp_path / "docs/benchmarks.md").read_text()
    assert bench.main(["check", "--version", "1.8.0"]) == 0
    assert bench.main(["check", "--version", "1.9.0"]) == 1
