"""`SweBenchReport`'s arithmetic: the split of the unresolved verdicts, the lines that report it,
and what the model gate did. The run store and the logs it reads are stand-ins, so nothing here
needs a run on disk.
"""

from datetime import datetime, timezone

import pytest

from dreamference.swe_bench import SweBenchEvaluator, SweBenchReport
from dreamference.swe_bench.swe_bench_gate_hold import SweBenchGateHold


class Store:
    def __init__(self, predictions=()):
        self._predictions = list(predictions)

    def predictions(self):
        return self._predictions


@pytest.fixture
def logged(monkeypatch):
    """The test counts each instance's grading log shows; records the model each was looked up by."""
    counts, models = {}, {}

    def read(cls, store, number, variant, result, instance_id, model):
        models[instance_id] = model
        return counts.get(instance_id)

    monkeypatch.setattr(SweBenchEvaluator, "logged_test_counts", classmethod(read))
    return counts, models


def test_each_unresolved_instance_gets_the_first_cause_that_applies(logged):
    counts, models = logged
    results = {
        "ok": {"resolved": True},
        "empty": {"empty": True, "applied": False},
        "rejected": {"applied": False, "test_patch_failed": True},
        "tests": {"applied": True, "test_patch_failed": True},
        "broke-both": {"applied": True},
        "broke": {"applied": True},
        "unfixed": {"applied": True},
        "no-log": {"applied": True},
        "nothing-failed": {"applied": True},
    }
    counts.update({
        "broke-both": {"p2p_failed": 3, "f2p_failed": 1},
        "broke": {"p2p_failed": 1, "f2p_failed": 0},
        "unfixed": {"p2p_failed": 0, "f2p_failed": 2},
        "nothing-failed": {"p2p_failed": 0, "f2p_failed": 0},
    })
    store = Store([{"instance_id": "broke", "model_name_or_path": "ling-b"}])

    causes = SweBenchReport.unresolved_causes(store, {"model_name_or_path": "ling-a"}, 2, None,
                                              results, list(results))

    assert causes == {
        "empty": ["empty"],
        "not_applied": ["rejected"],
        "test_patch": ["tests"],
        "broke": ["broke", "broke-both"],
        "not_fixed": ["unfixed"],
        # A verdict of unresolved with no failing test on disk is not guessed at.
        "unknown": ["no-log", "nothing-failed"],
        "broke_unfixed": ["broke-both"],
        "p2p_failed": {"broke": 1, "broke-both": 3},
    }
    # The log is looked up under the prediction's model, else the run's.
    assert models["broke"] == "ling-b" and models["unfixed"] == "ling-a"
    assert "ok" not in models and "empty" not in models


def test_before_grading_no_instance_has_a_cause(logged):
    causes = SweBenchReport.unresolved_causes(Store(), {}, None, None, {"a": {"applied": True}}, ["a"])

    assert all(value in ([], {}) for value in causes.values())


def test_the_causes_lines_count_against_the_graded_instances():
    causes = {"broke": ["a", "b"], "broke_unfixed": ["b"], "p2p_failed": {"a": 1, "b": 4},
              "not_fixed": ["c", "d", "e"], "empty": ["f"], "not_applied": [], "unknown": ["g"]}

    lines = SweBenchReport.causes_lines(causes, 16)

    assert lines[0] == ("Broke a passing test 2 of 16 graded (12.5%), 1 of them not fixing the issue "
                        "either: a (1 PASS_TO_PASS), b (4 PASS_TO_PASS)")
    assert lines[1].startswith("Did not fix          3:")
    assert lines[2] == "Graded empty         1: no patch to grade (f)"
    assert lines[3].startswith("No test results      1,") and lines[3].endswith(": g")
    assert len(lines) == 4  # no line for a cause nobody has
    assert SweBenchReport.causes_lines(causes, 0) == []

    clean = dict(causes, broke=[], broke_unfixed=[], p2p_failed={}, empty=[], unknown=[], not_applied=["h"])
    assert SweBenchReport.causes_lines(clean, 3) == [
        "Broke a passing test 0 of 3 graded (0.0%)",
        "Did not fix          3: a FAIL_TO_PASS test still fails, every PASS_TO_PASS test passes",
        "Patch did not apply  1: h",
    ]


@pytest.mark.parametrize("seconds, said", [
    (0, "0 s"), (59.9, "59 s"), (60, "1 min 0 s"), (400, "6 min 40 s"),
    (3599, "59 min 59 s"), (3600, "1 h 0 min"), (61800, "17 h 10 min"), (90061, "25 h 1 min"),
])
def test_durations_read_in_the_largest_unit_that_fits(seconds, said):
    assert SweBenchReport.duration(seconds) == said


def held(monkeypatch, sessions, pauses=()):
    monkeypatch.setattr(SweBenchGateHold, "read", classmethod(
        lambda cls, store: {"sessions": list(sessions), "pauses": list(pauses)}))


def started_at(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def test_a_run_from_before_the_gate_says_nothing_about_it(monkeypatch):
    held(monkeypatch, [], [{"start": 0, "end": 10}])

    assert SweBenchReport.gate_lines(Store(), {}, []) == []


def test_the_gate_line_tells_a_whole_run_from_part_of_one(monkeypatch):
    held(monkeypatch, [{"gate": "in force"}, {"gate": "in force"}])
    assert "refused" in SweBenchReport.gate_lines(Store(), {}, [])[0]

    held(monkeypatch, [{"gate": "in force"}, {"gate": "none"}])
    assert "for part of the run only" in SweBenchReport.gate_lines(Store(), {}, [])[0]

    held(monkeypatch, [{"gate": "none"}, {}])
    assert "none in front of the model server" in SweBenchReport.gate_lines(Store(), {}, [])[0]


def test_the_instances_that_ran_during_a_pause_are_named(monkeypatch):
    base = 1_790_000_000
    held(monkeypatch, [{"gate": "in force"}],
         [{"start": base + 1000, "end": base + 1600}, {"start": base + 5000, "end": base + 4000}])
    states = {
        "before": {"started": started_at(base), "wall_s": 1000},  # ends as the pause starts
        "across": {"started": started_at(base + 900), "wall_s": 200},
        "inside": {"started": started_at(base + 1100), "wall_s": 60},
        "after": {"started": started_at(base + 1600), "wall_s": 500},
        "unparsable": {"started": "yesterday", "wall_s": 10},
        "unfinished": {"started": started_at(base + 1100)},
    }

    gate, paused = SweBenchReport.gate_lines(Store(), states, list(states) + ["never-started"])

    # A pause that ends before it starts counts as no time at all.
    assert paused.startswith("Gate paused         2 time(s), 10 min 0 s in all (")
    assert paused.endswith("so their times are not comparable: across, inside")
