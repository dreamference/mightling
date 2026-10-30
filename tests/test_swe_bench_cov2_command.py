"""The swe-bench command line, from setup to clean.

Setup's refusals come in order, a smoke whose agent step falls short is not recorded, build-images
selects and validates, clean removes only what it is told to, and the dispatch has refusals of its own.
"""

import argparse
import subprocess

import pytest

from dreamference.swe_bench import SweBenchRunStore, swe_bench_settings
from dreamference.swe_bench.swe_bench_command import USAGE, SweBenchCommand
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_image_builder import SweBenchImageBuilder
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_instance_run import REFINE_VERSIONS
from dreamference.swe_bench.swe_bench_report import SweBenchReport
from dreamference.swe_bench.swe_bench_runner import SweBenchRunner
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime

SMOKE_ID = swe_bench_settings.SMOKE_INSTANCES[0]


def parse(*words):
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers())
    return parser.parse_args(["swe-bench", *words])


def seam(monkeypatch, owner, name, value, calls, label=None):
    def fake(cls, *args, **kwargs):
        calls.append((label or name, args, kwargs))
        return value(*args, **kwargs) if callable(value) else value

    monkeypatch.setattr(owner, name, classmethod(fake))


@pytest.fixture
def ready(monkeypatch):
    """A machine where every step of setup succeeds; tests break one step at a time."""
    calls = []
    docker = {"info": 0}

    def docker_run(args, timeout=None):
        calls.append(("docker", args, {}))
        return subprocess.CompletedProcess(args, docker.get(args[0], 0), "29.0\n", "")

    monkeypatch.setattr(
        SweBenchDocker, "run", classmethod(lambda cls, *a, **k: docker_run(*a, **k))
    )
    rows = [{"instance_id": "a__a-1"}, {"instance_id": "b__b-2"}, {"instance_id": "c__c-3"}]
    seam(monkeypatch, SweBenchHarness, "install", True, calls)
    seam(
        monkeypatch,
        SweBenchHarness,
        "download_dataset",
        {"dataset": "verified", "rows": 3, "revision": "r" * 40},
        calls,
    )
    seam(monkeypatch, SweBenchHarness, "rows", rows, calls)
    seam(monkeypatch, SweBenchHarness, "tool", "/opt/patchelf", calls)
    seam(monkeypatch, SweBenchImages, "tags", ["a-a-1", "b-b-2"], calls)
    seam(monkeypatch, SweBenchImages, "built_tags", [], calls)
    seam(
        monkeypatch,
        SweBenchImages,
        "image_for",
        lambda i, tags, built: i if i != "c__c-3" else None,
        calls,
    )
    seam(monkeypatch, SweBenchRuntime, "installed_mightling", "/opt/ling", calls)
    seam(monkeypatch, SweBenchRuntime, "ensure", "f" * 64, calls)
    seam(monkeypatch, SweBenchEvaluator, "disk_problem", None, calls)
    seam(
        monkeypatch,
        SweBenchEvaluator,
        "validate",
        lambda dataset, ids, settings, force=False: dict.fromkeys(ids),
        calls,
    )
    return {"calls": calls, "docker": docker, "monkeypatch": monkeypatch}


def names(calls):
    return [call[0] for call in calls]


def test_setup_stops_when_docker_does_not_answer_and_installs_nothing(ready, capsys):
    ready["docker"]["info"] = 1
    assert SweBenchCommand.setup() == 1
    assert "Docker is not answering" in capsys.readouterr().out
    assert names(ready["calls"]) == ["docker"]


@pytest.mark.parametrize(
    ("owner", "name", "value", "last"),
    [
        (SweBenchHarness, "install", False, "install"),
        (SweBenchHarness, "download_dataset", None, "download_dataset"),
        (SweBenchRuntime, "installed_mightling", None, "installed_mightling"),
        (SweBenchRuntime, "ensure", None, "ensure"),
    ],
)
def test_setup_stops_at_the_first_step_that_fails_and_goes_no_further(
    ready, owner, name, value, last
):
    seam(ready["monkeypatch"], owner, name, value, ready["calls"])
    assert SweBenchCommand.setup(validate=True) == 1
    called = names(ready["calls"])
    assert called[-1] == last
    assert "validate" not in called and "disk_problem" not in called


def test_setup_without_a_built_ling_says_how_to_build_it(ready, capsys):
    seam(ready["monkeypatch"], SweBenchRuntime, "installed_mightling", None, ready["calls"])
    assert SweBenchCommand.setup() == 1
    assert "ling is not built: run `ling-admin codex build` first" in capsys.readouterr().out


def test_a_ready_setup_validates_only_the_instances_with_an_image_up_to_the_limit(ready, capsys):
    seam(
        ready["monkeypatch"],
        SweBenchEvaluator,
        "validate",
        lambda dataset, ids, settings, force=False: dict.fromkeys(
            ids, "the reference patch does not resolve it"
        ),
        ready["calls"],
    )
    assert SweBenchCommand.setup(validate=True, limit=1, force=True) == 0
    [(_, args, kwargs)] = [call for call in ready["calls"] if call[0] == "validate"]
    assert args[:2] == ("verified", ["a__a-1"]) and kwargs == {"force": True}
    out = capsys.readouterr().out
    assert "2 of 3 instances" in out
    assert "a__a-1: the reference patch does not resolve it" in out and "0 of 1 validated" in out


def test_a_ready_setup_without_validate_judges_nothing_and_warns_about_the_disk(ready, capsys):
    seam(
        ready["monkeypatch"], SweBenchEvaluator, "disk_problem", "only 10 GiB free", ready["calls"]
    )
    assert SweBenchCommand.setup() == 0
    assert "validate" not in names(ready["calls"])
    assert "only 10 GiB free" in capsys.readouterr().out


def test_setup_validates_the_named_instances_rather_than_every_one_with_an_image(ready):
    assert (
        SweBenchCommand.dispatch(parse("setup", "--validate", "--instances", "c__c-3, b__b-2")) == 0
    )
    [(_, args, _)] = [call for call in ready["calls"] if call[0] == "validate"]
    assert args[1] == ["c__c-3", "b__b-2"]


@pytest.fixture
def smoke(monkeypatch):
    """A smoke whose first step passes; `agent` says what the agent step leaves behind."""
    agent = {"code": 0, "prediction": False, "log": "", "state": None, "graded": False}
    seam(monkeypatch, SweBenchHarness, "rows", [{"instance_id": SMOKE_ID}], [])
    seam(monkeypatch, SweBenchHarness, "installed_version", swe_bench_settings.HARNESS_VERSION, [])
    seam(
        monkeypatch,
        SweBenchEvaluator,
        "validate",
        lambda dataset, ids, settings, force=False: dict.fromkeys(ids),
        [],
    )

    def run(**kwargs):
        store = SweBenchRunStore(kwargs["name"])
        store.write_manifest({"name": store.name, "instances": [SMOKE_ID]})
        if agent["prediction"]:
            store.append_prediction(SMOKE_ID, "model", "diff --git a/x b/x\n")
        if agent["log"]:
            store.log_path(SMOKE_ID).parent.mkdir(parents=True, exist_ok=True)
            store.log_path(SMOKE_ID).write_text(agent["log"])
        if agent["state"]:
            store.write_state(SMOKE_ID, agent["state"])
        return agent["code"]

    monkeypatch.setattr(SweBenchRunner, "run", classmethod(lambda cls, **kwargs: run(**kwargs)))
    monkeypatch.setattr(
        SweBenchEvaluator,
        "results",
        classmethod(
            lambda cls, store, variant=None: (
                (1, {}, {SMOKE_ID: {"resolved": True}}) if agent["graded"] else (None, {}, {})
            )
        ),
    )
    return agent


def test_a_smoke_whose_agent_run_did_not_start_is_not_recorded(smoke, capsys):
    smoke["code"] = 1
    assert SweBenchCommand.smoke() == 1
    out = capsys.readouterr().out
    for problem in (
        "the run did not start",
        "no prediction was written",
        "no agent log was written",
        "the prediction was not graded",
    ):
        assert problem in out
    assert not SweBenchRunner.smoke_path().exists()


def test_a_smoke_whose_agent_failed_is_not_recorded_even_with_a_graded_prediction(smoke, capsys):
    smoke.update(
        prediction=True,
        log="{}\n",
        graded=True,
        state={"status": "error", "notes": ["container died"]},
    )
    assert SweBenchCommand.smoke() == 1
    out = capsys.readouterr().out
    assert "Smoke failed: the agent run failed: container died." in out
    assert not SweBenchRunner.smoke_path().exists()


def test_a_smoke_with_an_empty_agent_log_is_not_recorded(smoke, capsys):
    smoke.update(prediction=True, log="", graded=True, state={"status": "done"})
    assert SweBenchCommand.smoke() == 1
    assert "Smoke failed: no agent log was written." in capsys.readouterr().out
    assert not SweBenchRunner.smoke_path().exists()


def test_a_smoke_whose_agent_step_left_everything_is_recorded(smoke):
    smoke.update(prediction=True, log="{}\n", graded=True, state={"status": "done", "wall_s": 12})
    SweBenchRunner.smoke_path().parent.mkdir(parents=True, exist_ok=True)
    assert SweBenchCommand.smoke() == 0
    assert SweBenchRunner.smoke_path().is_file()


@pytest.fixture
def builder(monkeypatch):
    calls = []
    rows = [{"instance_id": i} for i in ("a__a-1", "b__b-2", "c__c-3")]
    seam(monkeypatch, SweBenchHarness, "install", True, calls)
    seam(monkeypatch, SweBenchHarness, "download_dataset", {}, calls)
    seam(monkeypatch, SweBenchHarness, "rows", rows, calls)
    outcome = {"a__a-1": None, "b__b-2": "build failed", "c__c-3": None}
    seam(
        monkeypatch,
        SweBenchImageBuilder,
        "build",
        lambda dataset, ids, settings, force=False: {i: outcome[i] for i in ids},
        calls,
    )
    seam(
        monkeypatch,
        SweBenchEvaluator,
        "validate",
        lambda dataset, ids, settings, force=False: dict.fromkeys(ids, "no-op resolves"),
        calls,
    )
    return calls


def test_build_images_of_an_unknown_instance_exits_2_and_builds_nothing(builder):
    assert SweBenchCommand.dispatch(parse("build-images", "--instances", "z__z-9")) == 2
    assert "build" not in names(builder)


def test_build_images_of_a_dataset_without_a_task_repository_exits_2_before_installing(
    builder, capsys
):
    assert SweBenchCommand.dispatch(parse("build-images", "--dataset", "verified")) == 2
    assert "has no task repository" in capsys.readouterr().out
    assert builder == []


def test_build_images_reads_the_subset_file_skipping_comments_and_validates_only_what_built(
    builder, tmp_path, capsys
):
    subset = tmp_path / "subset.txt"
    subset.write_text("# two of three\na__a-1\n\nb__b-2\n")
    assert (
        SweBenchCommand.dispatch(parse("build-images", "--subset", str(subset), "--validate")) == 1
    )
    [(_, build_args, _)] = [call for call in builder if call[0] == "build"]
    assert build_args[1] == ["a__a-1", "b__b-2"]
    [(_, validate_args, _)] = [call for call in builder if call[0] == "validate"]
    assert validate_args[1] == ["a__a-1"]
    out = capsys.readouterr().out
    assert "1 of 2 built" in out and "a__a-1: no-op resolves" in out and "0 of 1 validated" in out


def test_build_images_where_everything_built_exits_0(builder):
    assert SweBenchCommand.dispatch(parse("build-images", "--instances", "a__a-1,c__c-3")) == 0
    assert "validate" not in names(builder)


@pytest.fixture
def docker_calls(monkeypatch):
    calls = []
    listed = {"stdout": "c1\nc2\n"}

    def run(args, timeout=None):
        calls.append(args)
        stdout = listed["stdout"] if args[0] == "ps" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: run(*a, **k)))
    return calls, listed


def test_clean_of_one_run_removes_only_its_containers_and_scratch(docker_calls, monkeypatch):
    calls, _ = docker_calls
    for name in ("r1", "r2"):
        store = SweBenchRunStore(name)
        store.write_manifest({"name": name})
        (store.directory / "scratch" / "x").mkdir(parents=True)
    monkeypatch.setattr(
        SweBenchImages, "remove", classmethod(lambda cls, images: pytest.fail("no --images"))
    )
    assert SweBenchCommand.dispatch(parse("clean", "r1")) == 0
    assert calls == [
        ["ps", "-aq", "--filter", "label=ling.swe-bench.run=r1"],
        ["rm", "-f", "c1", "c2"],
    ]
    assert not (SweBenchRunStore("r1").directory / "scratch").exists()
    assert (SweBenchRunStore("r2").directory / "scratch" / "x").is_dir()


def test_clean_of_every_run_with_no_container_removes_none_and_with_images_removes_the_present_ones(
    docker_calls, monkeypatch, capsys
):
    calls, listed = docker_calls
    listed["stdout"] = ""
    for name in ("r1", "r2"):
        store = SweBenchRunStore(name)
        store.write_manifest({"name": name})
        (store.directory / "scratch").mkdir(parents=True)
    monkeypatch.setattr(
        SweBenchImages, "local_images", classmethod(lambda cls: [{"image": "i1"}, {"image": "i2"}])
    )
    removed = []
    monkeypatch.setattr(
        SweBenchImages, "remove", classmethod(lambda cls, images: removed.extend(images) or 1)
    )
    assert SweBenchCommand.dispatch(parse("clean", "--images")) == 0
    assert calls == [["ps", "-aq", "--filter", "label=ling.swe-bench.run"]]
    assert not any((SweBenchRunStore(n).directory / "scratch").exists() for n in ("r1", "r2"))
    assert removed == ["i1", "i2"]
    assert "Removed 1 of 2 instance image(s)" in capsys.readouterr().out
