"""An instance whose container cannot be made ready is recorded as an error, with an empty patch."""

import pytest

from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench import swe_bench_instance_run as module
from tests.test_swe_bench import bench as swe_bench_machine  # noqa: F401
from tests.test_swe_bench import run

INSTANCE = "acme__widget-1"


@pytest.fixture
def bench(request):
    return request.getfixturevalue("swe_bench_machine")


def failed_preparation(bench, monkeypatch, which):
    monkeypatch.setattr(module, which, "echo 'tree: broken' >&2; exit 5")
    run(bench, instances=[INSTANCE])
    store = SweBenchRunStore("r1")
    return store.state(INSTANCE), store.predictions()


@pytest.mark.parametrize(
    "script, words",
    [
        ("SCRUB_SCRIPT", "scrubbing /testbed failed (5)"),
        ("PREPARE_SCRIPT", "preparing /testbed failed (5)"),
    ],
)
def test_a_testbed_that_cannot_be_scrubbed_or_prepared_ends_in_error_with_no_patch(
    bench, monkeypatch, script, words
):
    state, predictions = failed_preparation(bench, monkeypatch, script)
    assert state["status"] == "error"
    assert any(words in note and "tree: broken" in note for note in state["notes"])
    assert [p["model_patch"] for p in predictions] == [""]


def test_a_runner_bug_ends_the_instance_in_error_and_still_removes_its_container(
    bench, monkeypatch
):
    def broken(self, state):
        raise RuntimeError("bug in the runner")

    monkeypatch.setattr(module.SweBenchInstanceRun, "_run", broken)
    run(bench, instances=[INSTANCE])
    store = SweBenchRunStore("r1")
    state = store.state(INSTANCE)
    assert state["status"] == "error"
    assert "runner error: bug in the runner" in state["notes"]
    assert [p["model_patch"] for p in store.predictions()] == [""]
    assert any(call[:2] == ["rm", "-f"] for call in bench["docker"].calls)


def test_an_interrupted_instance_writes_no_prediction_so_it_runs_again(bench, monkeypatch):
    def interrupted(self, state):
        self.stop_event.set()
        return "ok", "diff --git a/widget.py b/widget.py\n"

    monkeypatch.setattr(module.SweBenchInstanceRun, "_run", interrupted)
    run(bench, instances=[INSTANCE])
    store = SweBenchRunStore("r1")
    assert store.state(INSTANCE)["status"] == "interrupted"
    assert store.predictions() == []
    assert any(call[:2] == ["rm", "-f"] for call in bench["docker"].calls)


def test_a_container_that_does_not_start_ends_in_error_before_any_script_runs(bench, monkeypatch):
    monkeypatch.setattr(
        module.SweBenchInstanceRun, "_start_container", lambda self: "docker run failed: no space"
    )
    run(bench, instances=[INSTANCE])
    store = SweBenchRunStore("r1")
    state = store.state(INSTANCE)
    assert state["status"] == "error"
    assert "docker run failed: no space" in state["notes"]
    assert [p["model_patch"] for p in store.predictions()] == [""]
    assert not any(call[0] == "exec" for call in bench["docker"].calls)
