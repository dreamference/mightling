"""Runner decisions: what stops a run before it starts, and what an unpullable image costs."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from tests.test_swe_bench import bench, run  # noqa: F401

from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_runner import SweBenchRunner
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime


def test_a_run_without_a_usable_runtime_starts_no_instance(bench, monkeypatch):
    monkeypatch.setattr(SweBenchRuntime, "ensure", classmethod(lambda cls, *a: None))
    assert run(bench, instances=["acme__widget-1"]) == 1
    assert SweBenchRunStore("r1").manifest() is None
    assert not any(call[0] == "run" for call in bench["docker"].calls)


def test_a_run_naming_an_instance_the_dataset_lacks_is_refused_before_any_container(bench, capsys):
    assert run(bench, instances=["nobody__nothing-9"]) == 1
    assert "nobody__nothing-9" in capsys.readouterr().out
    assert SweBenchRunStore("r1").manifest() is None
    assert not any(call[0] == "run" for call in bench["docker"].calls)


def test_an_image_that_cannot_be_pulled_is_recorded_as_an_error_with_an_empty_prediction(monkeypatch, capsys):
    store = SweBenchRunStore("2026-10-05-a")
    store.write_manifest({"name": store.name})
    monkeypatch.setattr(SweBenchDocker, "ensure_image", classmethod(lambda cls, image: None))
    started = []
    instance = SimpleNamespace(
        image="registry/none:latest", store=store, instance_id="a__a-1", model_name="m",
        run=lambda: started.append(1),
    )
    SweBenchRunner._run_one(instance)
    assert started == []
    assert store.state("a__a-1")["status"] == "error"
    assert [(p["instance_id"], p["model_patch"]) for p in store.predictions()] == [("a__a-1", "")]
    assert "the image could not be pulled" in capsys.readouterr().out


def test_the_default_name_takes_the_next_free_letter():
    today = datetime.now().strftime("%Y-%m-%d")
    assert SweBenchRunner.default_name() == f"{today}-a"
    SweBenchRunStore(f"{today}-a").write_manifest({"name": f"{today}-a"})
    assert SweBenchRunner.default_name() == f"{today}-b"


def test_until_time_is_the_next_such_time_never_one_already_past():
    assert SweBenchRunner.until_time(None) is None
    now = datetime.now().astimezone()
    past = (now - timedelta(minutes=5)).strftime("%H:%M")
    got = SweBenchRunner.until_time(past)
    assert got > now and got - now <= timedelta(days=1)
