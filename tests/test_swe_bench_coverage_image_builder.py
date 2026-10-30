"""Image builder refusals: it builds only from the pinned task repository, or reports why not."""

import subprocess

from dreamference.swe_bench import swe_bench_image_builder as module
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_image_builder import SweBenchImageBuilder

DATASET = "multilingual"


def done(code=0, err=""):
    return subprocess.CompletedProcess([], code, "", err)


def fake_git(monkeypatch, fail_on=(), calls=None):
    calls = [] if calls is None else calls

    def execute(command, **kwargs):
        calls.append(list(command))
        verb = command[1] if command[1] != "-C" else command[3]
        if verb == "clone":
            (SweBenchImageBuilder.task_repo_dir(DATASET) / ".git").mkdir(
                parents=True, exist_ok=True
            )
        return done(1, "git said no") if verb in fail_on else done()

    monkeypatch.setattr(SweBenchImageBuilder, "execute", staticmethod(execute))
    return calls


def test_a_dataset_without_a_task_repository_has_nothing_to_build_from(capsys):
    assert SweBenchImageBuilder.ensure_task_repo("verified") is None
    assert "has no task repository" in capsys.readouterr().out


def test_a_failed_clone_gives_no_task_repository_and_no_checkout(monkeypatch, capsys):
    calls = fake_git(monkeypatch, fail_on=("clone",))
    assert SweBenchImageBuilder.ensure_task_repo(DATASET) is None
    assert "git clone" in capsys.readouterr().out
    assert [c[1] for c in calls] == ["clone"]


def test_a_commit_that_cannot_be_checked_out_gives_no_task_repository(monkeypatch, capsys):
    calls = fake_git(monkeypatch, fail_on=("checkout",))
    assert SweBenchImageBuilder.ensure_task_repo(DATASET) is None
    assert "Could not check out" in capsys.readouterr().out
    assert calls[-1][3] == "checkout"


def test_a_missing_pinned_commit_is_fetched_before_the_checkout(monkeypatch):
    calls = fake_git(monkeypatch, fail_on=("cat-file",))
    assert SweBenchImageBuilder.ensure_task_repo(DATASET) == SweBenchImageBuilder.task_repo_dir(
        DATASET
    )
    verbs = [c[3] if c[1] == "-C" else c[1] for c in calls]
    assert verbs == ["clone", "cat-file", "fetch", "checkout"]


def test_a_builder_that_cannot_be_created_is_reported(monkeypatch, capsys):
    def docker(args, timeout=None, input_text=None):
        return (
            done(1, "buildkit missing") if args[1] == "inspect" or args[1] == "create" else done()
        )

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: docker(*a, **k)))
    settings = swe_bench_settings.SweBenchSettings()
    assert SweBenchImageBuilder.ensure_builder(settings) is False
    assert "Could not create the builder" in capsys.readouterr().out


def test_without_a_task_repository_or_builder_every_instance_is_reported_unbuilt(monkeypatch):
    monkeypatch.setattr(SweBenchHarness, "rows", classmethod(lambda cls, dataset: []))
    monkeypatch.setattr(
        SweBenchImageBuilder, "ensure_task_repo", classmethod(lambda cls, dataset: None)
    )
    settings = swe_bench_settings.SweBenchSettings()
    outcome = SweBenchImageBuilder.build(DATASET, ["a__a-1", "b__b-2"], settings)
    assert set(outcome) == {"a__a-1", "b__b-2"}
    assert all("missing" in problem for problem in outcome.values())


def test_an_instance_without_a_dockerfile_in_the_pinned_repository_is_not_built(
    monkeypatch, tmp_path
):
    repo = tmp_path / "tasks-repo"
    repo.mkdir()
    monkeypatch.setattr(SweBenchHarness, "rows", classmethod(lambda cls, dataset: []))
    monkeypatch.setattr(
        SweBenchImageBuilder, "ensure_task_repo", classmethod(lambda cls, dataset: repo)
    )
    monkeypatch.setattr(
        SweBenchImageBuilder, "ensure_builder", classmethod(lambda cls, settings: True)
    )
    monkeypatch.setattr(module, "BUILD_HEADROOM_BYTES", 0)
    docker_calls = []
    monkeypatch.setattr(
        SweBenchDocker,
        "run",
        classmethod(lambda cls, args, **k: docker_calls.append(args) or done()),
    )
    outcome = SweBenchImageBuilder.build(DATASET, ["a__a-1"], swe_bench_settings.SweBenchSettings())
    assert "no Dockerfile in the task repository" in outcome["a__a-1"]
    assert not any(call[0] == "buildx" and "build" in call for call in docker_calls)
