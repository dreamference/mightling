"""An instance whose container cannot be made ready is recorded as an error, with an empty patch."""

import pytest

from tests.test_swe_bench import bench, run  # noqa: F401

from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench import swe_bench_instance_run as module

INSTANCE = "acme__widget-1"


def failed_preparation(bench, monkeypatch, which):
    monkeypatch.setattr(module, which, "echo 'tree: broken' >&2; exit 5")
    run(bench, instances=[INSTANCE])
    store = SweBenchRunStore("r1")
    return store.state(INSTANCE), store.predictions()


@pytest.mark.parametrize(
    "script, words",
    [("SCRUB_SCRIPT", "scrubbing /testbed failed (5)"), ("PREPARE_SCRIPT", "preparing /testbed failed (5)")],
)
def test_a_testbed_that_cannot_be_scrubbed_or_prepared_ends_in_error_with_no_patch(
    bench, monkeypatch, script, words
):
    state, predictions = failed_preparation(bench, monkeypatch, script)
    assert state["status"] == "error"
    assert any(words in note and "tree: broken" in note for note in state["notes"])
    assert [p["model_patch"] for p in predictions] == [""]
