"""Grading refusals: an instance is left ungraded, never graded against a missing image."""

from tests.test_swe_bench import bench, run  # noqa: F401

from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator


def test_a_full_disk_leaves_the_instance_ungraded_and_never_calls_the_harness(bench, monkeypatch, capsys):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    calls = len(bench["harness"].calls)
    monkeypatch.setattr(SweBenchDocker, "image_digest", classmethod(lambda cls, image: None))
    monkeypatch.setattr(SweBenchEvaluator, "disk_problem", classmethod(lambda cls, settings: "disk is full"))
    assert SweBenchEvaluator.grade(store, bench["settings"]) == {}
    assert len(bench["harness"].calls) == calls
    assert "acme__widget-1 is not graded yet: disk is full" in capsys.readouterr().out


def test_an_image_that_cannot_be_built_leaves_the_instance_ungraded(bench, monkeypatch, capsys):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    calls = len(bench["harness"].calls)
    monkeypatch.setattr(SweBenchDocker, "ensure_image", classmethod(lambda cls, image: None))
    assert SweBenchEvaluator.grade(store, bench["settings"]) == {}
    assert len(bench["harness"].calls) == calls
    assert "acme__widget-1 is not graded yet: its image is not available" in capsys.readouterr().out


def test_a_reused_verdict_with_an_unreadable_grading_number_has_no_counts(tmp_path):
    store = SweBenchRunStore("r-reuse")
    result = {"resolved": True, "reused": "plain grading x"}
    assert SweBenchEvaluator.logged_test_counts(store, 2, "variant", result, "a__a-1", "m") is None
