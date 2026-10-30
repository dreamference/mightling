"""A run's report against numbers counted again from the raw files of a small synthetic run."""

import json
from pathlib import Path

import pytest

from dreamference.swe_bench import SweBenchRunStore, swe_bench_settings
from dreamference.swe_bench.swe_bench_report import SweBenchReport

MODEL = "model/x"
INSTANCES = [
    "acme__widget-1",
    "acme__widget-2",
    "acme__widget-3",
    "acme__widget-4",
    "acme__widget-5",
    "beta__gadget-6",
    "beta__gadget-7",
]
PATCH = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
# instance -> (status, patch, grading result); None: the instance was not graded.
RUN = {
    "acme__widget-1": ("done", PATCH, dict(resolved=True, applied=True, f2p=2, f2p_failed=0, p2p=5, p2p_failed=0)),
    "acme__widget-2": ("done", PATCH, dict(resolved=True, applied=True, f2p=1, f2p_failed=0, p2p=3, p2p_failed=0)),
    "acme__widget-3": ("done", PATCH, dict(resolved=False, applied=True, f2p=2, f2p_failed=1, p2p=4, p2p_failed=0)),
    "acme__widget-4": ("done", PATCH, dict(resolved=False, applied=True, f2p=1, f2p_failed=0, p2p=6, p2p_failed=2)),
    "acme__widget-5": ("done", PATCH, dict(resolved=False, applied=True, f2p=1, f2p_failed=1, p2p=6, p2p_failed=3)),
    "beta__gadget-6": ("timeout", "", None),
    "beta__gadget-7": ("empty", "", dict(resolved=False, applied=True, empty=True)),
}


def write_run(directory: Path) -> None:
    """Lays a run out on disk by hand, without the code that normally writes it."""
    directory.mkdir(parents=True)
    manifest = {"name": directory.name, "dataset": "d/d", "model_name_or_path": MODEL, "instances": INSTANCES}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "instances").mkdir()
    lines = []
    for instance_id, (status, patch, _) in RUN.items():
        (directory / "instances" / f"{instance_id}.json").write_text(
            json.dumps({"instance_id": instance_id, "status": status, "wall_s": 60})
        )
        lines.append(json.dumps({"instance_id": instance_id, "model_name_or_path": MODEL, "model_patch": patch}))
    (directory / "predictions.jsonl").write_text("\n".join(lines) + "\n")
    graded = {i: dict(result, test_patch_failed=False) for i, (_, _, result) in RUN.items() if result}
    (directory / "eval" / "1").mkdir(parents=True)
    (directory / "eval" / "1" / "grading.json").write_text(json.dumps({"grader": {}, "results": graded}))


def recount(directory: Path) -> dict:
    """Counts the run from its files alone: predictions, instance states and the grading."""
    manifest = json.loads((directory / "manifest.json").read_text())
    predictions = {}
    for line in (directory / "predictions.jsonl").read_text().splitlines():
        try:
            predictions[json.loads(line)["instance_id"]] = json.loads(line)
        except ValueError:
            continue
    results = json.loads((directory / "eval" / "1" / "grading.json").read_text())["results"]
    states = {
        i: json.loads((directory / "instances" / f"{i}.json").read_text())["status"]
        for i in manifest["instances"]
    }
    graded = [i for i in manifest["instances"] if i in results]
    broke = [i for i in graded if not results[i]["resolved"] and results[i].get("p2p_failed")]
    return {
        "resolved": sum(1 for i in graded if results[i]["resolved"]),
        "graded": len(graded),
        "finished": sum(1 for i in manifest["instances"] if i in predictions),
        "timeouts": sum(1 for i in manifest["instances"] if i in predictions and states[i] == "timeout"),
        "empty": sum(1 for i in manifest["instances"] if i in predictions and states[i] == "empty"),
        "empty_patches": sum(1 for p in predictions.values() if not p["model_patch"].strip()),
        "broke": broke,
        "broke_unfixed": [i for i in broke if results[i]["f2p_failed"]],
    }


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(swe_bench_settings, "RESULTS_DIR", tmp_path)
    return tmp_path / "runs"


def check_against_recount(directory: Path) -> str:
    expected = recount(directory)
    store = SweBenchRunStore(directory.name)
    summary = SweBenchReport.summary(store)
    text = SweBenchReport.render(store)
    assert summary["resolved"] == expected["resolved"]
    assert summary["graded"] == expected["graded"]
    assert summary["finished"] == expected["finished"]
    assert summary["statuses"].get("timeout", 0) == expected["timeouts"]
    assert summary["statuses"].get("empty", 0) == expected["empty"]
    assert summary["causes"]["broke"] == expected["broke"]
    assert summary["causes"]["broke_unfixed"] == expected["broke_unfixed"]
    total = len(INSTANCES)
    assert f"Resolved            {expected['resolved']} / {total} validated" in text
    assert f"Empty patch         {expected['empty']}" in text
    assert f"Timeout {expected['timeouts']}" in text
    broke = expected["broke"]
    if broke:
        assert (
            f"Broke a passing test {len(broke)} of {expected['graded']} graded "
            f"({100 * len(broke) / expected['graded']:.1f}%), {len(expected['broke_unfixed'])} of them"
        ) in text
    return text


def test_the_report_counts_match_a_recount_of_the_raw_files(results_dir):
    directory = results_dir / "r1"
    write_run(directory)
    text = check_against_recount(directory)
    assert "Resolved            2 / 7 validated" in text
    assert "Broke a passing test 2 of 6 graded (33.3%), 1 of them not fixing the issue either" in text
    assert "acme__widget-4 (2 PASS_TO_PASS), acme__widget-5 (3 PASS_TO_PASS)" in text
    assert "Did not fix          1:" in text
    assert "Timeout 1" in text
    assert recount(directory)["empty_patches"] == 2


def test_a_missing_prediction_and_a_torn_line_leave_the_report_consistent(results_dir):
    directory = results_dir / "r2"
    write_run(directory)
    lines = (directory / "predictions.jsonl").read_text().splitlines()
    kept = [line for line in lines if "acme__widget-4" not in line]
    (directory / "predictions.jsonl").write_text("\n".join(kept) + '\n{"instance_id": "acme__wid')
    text = check_against_recount(directory)
    summary = SweBenchReport.summary(SweBenchRunStore("r2"))
    assert summary["finished"] == 6
    # The grading of the instance without a prediction still counts: it is a recorded verdict.
    assert summary["graded"] == 6
    assert "INCOMPLETE          1 not run yet, 1 run but not graded yet" in text
