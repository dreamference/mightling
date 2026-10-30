"""Exit codes of the swe-bench command line when it must refuse before touching anything."""

import argparse

from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench.swe_bench_command import SweBenchCommand
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness


def parse(*words):
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers())
    return parser.parse_args(["swe-bench", *words])


def test_smoke_refuses_until_setup_has_downloaded_the_dataset_and_installed_the_harness(capsys):
    assert SweBenchCommand.smoke() == 1
    assert "swe-bench setup" in capsys.readouterr().out


def test_eval_refuses_a_negative_image_chunk_before_looking_at_the_run(capsys):
    assert SweBenchCommand.evaluate(None, image_chunk=-1) == 1
    assert "--image-chunk -1" in capsys.readouterr().out


def test_eval_of_a_run_that_does_not_exist_is_refused(capsys):
    assert SweBenchCommand.evaluate("no-such-run") == 1
    assert "No such run: no-such-run" in capsys.readouterr().out


def test_eval_without_enough_free_memory_grades_nothing(monkeypatch, capsys):
    store = SweBenchRunStore("2026-10-04-a")
    store.write_manifest({"name": store.name})
    monkeypatch.setattr(NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 1 * GIB))

    def never(*args, **kwargs):
        raise AssertionError("must not grade")

    monkeypatch.setattr(SweBenchEvaluator, "grade", classmethod(never))
    assert SweBenchCommand.evaluate(store.name) == 1
    out = capsys.readouterr().out
    assert "Not graded" in out and "1 GiB of memory is available" in out


def test_build_images_stops_when_the_harness_cannot_be_installed(monkeypatch):
    monkeypatch.setattr(SweBenchHarness, "install", classmethod(lambda cls: False))
    assert SweBenchCommand.dispatch(parse("build-images", "--dataset", "multilingual")) == 1


def test_build_images_refuses_an_instance_that_is_not_in_the_dataset(monkeypatch, capsys):
    monkeypatch.setattr(SweBenchHarness, "rows", classmethod(lambda cls, dataset: [{"instance_id": "a__a-1"}]))
    args = argparse.Namespace(instances="a__a-1,b__b-2,c__c-3", subset=None, languages=None)
    assert SweBenchCommand.build_selection("multilingual", args) is None
    assert "Not in multilingual: b__b-2, c__c-3" in capsys.readouterr().out


def test_the_drop_test_hunks_flag_chooses_the_filtered_grading_series(monkeypatch):
    seen = []
    monkeypatch.setattr(SweBenchCommand, "evaluate", classmethod(lambda cls, *a: seen.append(("eval", *a)) or 0))
    monkeypatch.setattr(SweBenchCommand, "report", classmethod(lambda cls, *a: seen.append(("report", *a)) or 0))
    assert SweBenchCommand.dispatch(parse("eval", "r1", "--drop-test-hunks")) == 0
    assert SweBenchCommand.dispatch(parse("eval", "r1")) == 0
    assert SweBenchCommand.dispatch(parse("report", "r1", "--drop-test-hunks")) == 0
    assert SweBenchCommand.dispatch(parse("report", "r1")) == 0
    assert seen[0][:3] == ("eval", "r1", "drop-test-hunks")
    assert seen[1][:3] == ("eval", "r1", None)
    assert seen[2] == ("report", "r1", None, "drop-test-hunks")
    assert seen[3] == ("report", "r1", None, None)


def test_report_of_a_run_that_does_not_exist_is_refused(capsys):
    assert SweBenchCommand.report("no-such-run", None) == 1
    assert "No such run" in capsys.readouterr().out


def test_shard_refuses_a_count_outside_the_allowed_range_and_an_unreadable_list(tmp_path, capsys):
    source = tmp_path / "tasks.txt"
    source.write_text("a__a-1\n")
    assert SweBenchCommand.shard(source, 0) == 1
    assert "--shards 0" in capsys.readouterr().out
    assert SweBenchCommand.shard(tmp_path / "absent.txt", 2) == 1
    assert "absent.txt" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [source]
