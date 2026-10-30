"""Tests of `SweBenchShardMerge`: which shards are refused, and what a merged run keeps of them."""

import json

from dreamference.swe_bench.swe_bench_evaluator import VARIANTS, SweBenchEvaluator
from dreamference.swe_bench.swe_bench_gate_hold import GATE_RECORD
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_shard_merge import SweBenchShardMerge

COUNTS = {"f2p": 2, "f2p_failed": 0, "p2p": 5, "p2p_failed": 1}


def shard(name, instances, index=None, of=2, sha="abc", finished=None, **extra):
    store = SweBenchRunStore(name)
    manifest = {"name": name, "instances": instances, "model": "m1", **extra}
    if index is not None:
        manifest["shard"] = {"index": index, "of": of, "list_sha256": sha}
    store.write_manifest(manifest)
    for instance_id in instances if finished is None else finished:
        store.append_prediction(instance_id, "m1", "diff")
    return store


def grade(store, grader, results, variant=None):
    store.write_grading(1, {"grader": grader, "results": results}, variant)


def refused(capsys, name, runs, text):
    assert SweBenchShardMerge.merge(name, runs) == 1
    assert text in capsys.readouterr().out
    assert SweBenchRunStore(name).manifest() is None


def test_a_name_that_is_not_a_run_name_is_refused_and_writes_nothing(capsys):
    shard("a", ["i1"])
    shard("b", ["i2"])
    refused(capsys, "bad name!", ["a", "b"], "is not a run's name")


def test_a_name_of_a_run_that_exists_is_refused_and_writes_nothing(capsys):
    shard("a", ["i1"])
    shard("b", ["i2"])
    shard("taken", ["i3"])
    assert SweBenchShardMerge.merge("taken", ["a", "b"]) == 1
    assert "a run named taken exists already" in capsys.readouterr().out
    assert SweBenchRunStore("taken").manifest()["instances"] == ["i3"]


def test_a_name_that_is_one_of_the_shards_is_refused(capsys):
    shard("a", ["i1"])
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("a", ["a", "b"]) == 1
    assert "a run named a exists already" in capsys.readouterr().out


def test_a_run_that_is_a_merge_already_is_refused_and_writes_nothing(capsys):
    shard("a", ["i1"])
    shard("b", ["i2"])
    shard("c", ["i3"])
    assert SweBenchShardMerge.merge("ab", ["a", "b"]) == 0
    refused(capsys, "abc", ["ab", "c"], "ab is a merge already")


def test_shards_of_different_counts_are_refused_and_write_nothing(capsys):
    shard("a", ["i1"], index=1, of=2)
    shard("b", ["i2"], index=2, of=3)
    refused(capsys, "m", ["a", "b"], "different lists or of different counts")


def test_shards_of_different_lists_are_refused_and_write_nothing(capsys):
    shard("a", ["i1"], index=1, sha="one")
    shard("b", ["i2"], index=2, sha="two")
    refused(capsys, "m", ["a", "b"], "different lists or of different counts")


def test_plain_gradings_made_by_different_graders_are_not_merged(capsys):
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": {"resolved": True}})
    grade(shard("b", ["i2"]), {"harness": "2"}, {"i2": {"resolved": False}})
    refused(capsys, "m", ["a", "b"], "Not merged: b's plain grading was made with another grader")


def test_variant_gradings_made_by_different_graders_are_not_merged(capsys):
    variant = VARIANTS[0]
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": {}}, variant)
    grade(shard("b", ["i2"]), {"harness": "2"}, {"i2": {}}, variant)
    refused(capsys, "m", ["a", "b"], f"b's {variant} grading was made with another grader")


def test_gradings_by_one_grader_are_joined_into_the_merged_runs_grading_one():
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": {"resolved": True, **COUNTS}})
    grade(shard("b", ["i2"]), {"harness": "1"}, {"i2": {"resolved": False, **COUNTS}})
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    record = SweBenchRunStore("m").grading(1)
    assert record["grader"] == {"harness": "1"}
    assert sorted(record["results"]) == ["i1", "i2"]


def test_a_result_without_test_counts_gets_them_from_the_harness_logs(monkeypatch):
    seen = []

    def counts(cls, store, number, variant, *rest):
        seen.append((store.name, number, variant, rest[1], rest[2]))
        return COUNTS

    monkeypatch.setattr(SweBenchEvaluator, "logged_test_counts", classmethod(counts))
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": {"resolved": True}})
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert SweBenchRunStore("m").grading(1)["results"]["i1"] == {"resolved": True, **COUNTS}
    assert seen == [("a", 1, None, "i1", "m1")]


def test_a_result_the_logs_have_no_counts_for_is_kept_as_it_is(monkeypatch):
    monkeypatch.setattr(
        SweBenchEvaluator, "logged_test_counts", classmethod(lambda cls, *args: None)
    )
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": {"resolved": True}})
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert SweBenchRunStore("m").grading(1)["results"]["i1"] == {"resolved": True}


def test_a_reused_plain_grading_points_at_the_merged_runs_plain_grading(monkeypatch):
    monkeypatch.setattr(
        SweBenchEvaluator, "logged_test_counts", classmethod(lambda cls, *args: None)
    )
    reused = {"resolved": True, "reused": "plain grading 4"}
    grade(shard("a", ["i1"]), {"harness": "1"}, {"i1": reused})
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert SweBenchRunStore("m").grading(1)["results"]["i1"]["reused"] == "plain grading 1"


def test_shards_without_gate_records_leave_the_merged_run_without_a_gate_file():
    shard("a", ["i1"])
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert not (SweBenchRunStore("m").directory / GATE_RECORD).exists()


def test_gate_sessions_and_pauses_are_joined_each_named_after_its_shard():
    a = shard("a", ["i1"])
    b = shard("b", ["i2"])
    session = {"start": 1, "end": 2, "gate": "in force"}
    pause = {"start": 3, "end": 4}
    (a.directory / GATE_RECORD).write_text(json.dumps({"sessions": [session]}))
    (b.directory / GATE_RECORD).write_text(json.dumps({"pauses": [pause]}))
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    gate = json.loads((SweBenchRunStore("m").directory / GATE_RECORD).read_text())
    assert gate == {"sessions": [{**session, "run": "a"}], "pauses": [{**pause, "run": "b"}]}


def test_a_scratch_that_is_a_file_is_not_linked_and_a_scratch_directory_is():
    a = shard("a", ["i1"])
    (a.directory / "scratch" / "i1").mkdir(parents=True)
    (a.directory / "scratch" / "note.txt").write_text("x")
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    scratch = SweBenchRunStore("m").directory / "scratch"
    assert sorted(path.name for path in scratch.iterdir()) == ["i1"]
    assert scratch.joinpath("i1").is_symlink()


def test_a_scratch_link_that_exists_already_is_not_replaced():
    a = shard("a", ["i1"])
    b = shard("b", ["i2"])
    (a.directory / "scratch" / "same").mkdir(parents=True)
    (b.directory / "scratch" / "same").mkdir(parents=True)
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    link = SweBenchRunStore("m").directory / "scratch" / "same"
    assert link.resolve() == (a.directory / "scratch" / "same").resolve()


def test_a_shard_with_instances_not_finished_is_noted_and_the_merge_still_succeeds(capsys):
    shard("a", ["i1", "i2"], finished=["i1"])
    shard("b", ["i3"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    out = capsys.readouterr().out
    assert "⚠️  a: 1 instance(s) not run yet." in out
    assert "b: " not in out.split("✅")[0]
    assert SweBenchRunStore("m").manifest()["instances"] == ["i1", "i2", "i3"]


def test_shards_without_shard_records_make_no_note_about_missing_shards(capsys):
    shard("a", ["i1"])
    shard("b", ["i2"])
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert "are not in the merge" not in capsys.readouterr().out


def test_a_merge_that_leaves_out_a_shard_of_the_list_says_so(capsys):
    shard("a", ["i1"], index=1, of=3)
    shard("b", ["i2"], index=2, of=3)
    assert SweBenchShardMerge.merge("m", ["a", "b"]) == 0
    assert "Shard(s) 3 of 3 are not in the merge" in capsys.readouterr().out
