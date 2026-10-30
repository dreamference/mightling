"""Run-store reads that must not trust files a crashed or hostile run left behind."""

import json

from dreamference.swe_bench import SweBenchRunStore


def test_no_runs_directory_means_no_runs_and_no_latest():
    assert SweBenchRunStore.runs() == []
    assert SweBenchRunStore.latest() is None


def test_a_run_without_instances_has_no_states_and_a_missing_log_counts_zero():
    store = SweBenchRunStore("2026-10-03-a")
    store.write_manifest({"name": store.name})
    assert store.states() == {}
    stats = store.log_stats("django__django-1")
    assert stats["commands"] == 0 and stats["output_tokens"] == 0


def test_a_log_line_that_is_not_an_event_object_is_not_counted():
    store = SweBenchRunStore("2026-10-03-b")
    store.write_manifest({"name": store.name})
    path = store.log_path("django__django-2")
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}}
    path.write_text("header line\n[1, 2]\n42\n" + json.dumps(event) + "\n")
    assert store.log_stats("django__django-2")["commands"] == 1


def test_predictions_skip_torn_lines_and_records_without_an_instance():
    store = SweBenchRunStore("2026-10-03-c")
    store.write_manifest({"name": store.name})
    assert store.predictions() == []  # no file yet
    good = {"instance_id": "a__a-1", "model_name_or_path": "m", "model_patch": ""}
    store.predictions_path.write_text(
        json.dumps(good)
        + "\n"
        + '{"instance_id": "b__b-2", "mod\n'
        + "[1]\n"
        + json.dumps({"model_patch": "x"})
        + "\n"
        + json.dumps({"instance_id": ""})
        + "\n"
    )
    assert store.predictions() == [good]


def test_a_prediction_appended_after_a_torn_line_starts_on_its_own_line():
    store = SweBenchRunStore("2026-10-03-d")
    store.write_manifest({"name": store.name})
    store.predictions_path.write_text('{"instance_id": "x__x-1", "mod')  # killed mid-write
    store.append_prediction("y__y-2", "m", "diff")
    assert [p["instance_id"] for p in store.predictions()] == ["y__y-2"]


def test_compactions_count_an_untimed_record_and_skip_junk_lines():
    store = SweBenchRunStore("2026-10-03-e")
    store.write_manifest({"name": store.name})
    assert store.compactions("i-1") is None  # no rollout on disk
    sessions = store.directory / "scratch" / "i-1" / "codex-home" / "sessions"
    sessions.mkdir(parents=True)
    records = [
        "not json",
        "[1]",
        json.dumps({"type": "message", "timestamp": "2026-10-03T10:00:00Z"}),
        json.dumps({"type": "compacted", "timestamp": "2026-10-03T10:00:30Z"}),
        json.dumps({"type": "compacted"}),  # no timestamp: counted, not timed
    ]
    (sessions / "rollout-1.jsonl").write_text("\n".join(records) + "\n")
    assert store.compactions("i-1") == {"count": 2, "seconds": [30.0, None]}
