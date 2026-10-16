"""`scripts/swe_bench_best_of.py`: night 11's best-of-N against a single run, from runs and chooser v1's summary."""

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def best_of():
    spec = importlib.util.spec_from_file_location("swe_bench_best_of", os.path.join(ROOT, "scripts", "swe_bench_best_of.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def run(name, resolved):
    return {"name": name, "instances": sorted(resolved), "resolved": dict(resolved)}


def test_the_sign_flip_test_is_exact(best_of):
    assert best_of.sign_flip_p([]) == 1.0
    assert best_of.sign_flip_p([0, 0]) == 1.0
    assert best_of.sign_flip_p([1, -1]) == 1.0
    assert best_of.sign_flip_p([1, 1, 1]) == pytest.approx(2 / 8)       # the exact two-sided sign test
    assert best_of.sign_flip_p([1] * 6) == pytest.approx(2 / 64)
    assert best_of.sign_flip_p([0.5, 1, 0]) == pytest.approx(2 / 4)


def test_the_chooser_is_compared_with_the_first_run_on_the_same_tasks(best_of):
    runs = [run("n11-default-1", {"t1": True, "t2": False, "t3": False, "t4": True}),
            run("n11-default-2", {"t1": True, "t2": True, "t3": False, "t4": False}),
            run("n11-default-3", {"t1": True, "t2": True, "t3": False, "t4": False})]
    # t1 every run resolved, t3 none: no choice; t2 and t4 disputed, the chooser's credit from its summary.
    summary = {"runs": [r["name"] for r in runs], "stages": {"judge": 1, "regression filter": 1},
               "per_task": {"t2": {"chooser": 1.0}, "t4": {"chooser": 0.5}}}
    result = best_of.compare(runs, summary)
    assert (result["single"], result["chooser"], result["oracle"]) == (2.0, 2.5, 3.0)
    assert result["mean"] == 2.0                                        # 1 + 2/3 + 0 + 1/3
    assert (result["only_chooser"], result["only_single"]) == (1.0, 0.5)
    assert result["p"] == 1.0                                           # +1 and -0.5: every flip is as far
    text = best_of.render(result)
    assert "single run (n11-default-1):        2.0 (50.0%)" in text
    assert "best-of-3, chooser v1:     2.5 (62.5%)" in text
    assert "headroom over the single run captured: +0.50 of +1.00" in text
    assert "t2" in text and "t4" in text and "t1 " not in text.split("disagree")[1]


def test_a_summary_over_other_runs_is_refused(best_of, tmp_path, monkeypatch):
    chooser = best_of.chooser_module()
    monkeypatch.setattr(chooser, "load_run", lambda runs_dir, name: run(name, {"t1": True}))
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"runs": ["a", "b"], "per_task": {}}))
    with pytest.raises(SystemExit, match="was made over"):
        best_of.main(["--runs", "x,y", "--chooser", str(summary)])
    assert best_of.main(["--runs", "a,b", "--chooser", str(summary), "--json", str(tmp_path / "out.json")]) == 0
    assert json.loads((tmp_path / "out.json").read_text())["single"] == 1.0
