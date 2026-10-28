"""SWE-bench Multilingual on arm64: the pinned dataset, images built here, the agent's PATH."""

import json
import subprocess

import pytest

from dreamference.swe_bench import (
    SweBenchCommand,
    SweBenchDocker,
    SweBenchEvaluator,
    SweBenchHarness,
    SweBenchImageBuilder,
    SweBenchImages,
    SweBenchInstanceRun,
    SweBenchRunner,
    SweBenchSettings,
    swe_bench_image_builder,
    swe_bench_settings,
)
from dreamference.swe_bench.swe_bench_instance_run import CONTAINER_PATH

MULTILINGUAL = "SWE-bench/SWE-bench_Multilingual"
BUILT = swe_bench_settings.BUILT_IMAGE_REPO
COMMUNITY = swe_bench_settings.COMMUNITY_IMAGE_REPO

DOCKERFILE_C = """FROM --platform=linux/amd64 ubuntu:22.04

RUN apt update && apt install -y build-essential
RUN <<EOF_1
git clone https://github.com/redis/redis /testbed
cd /testbed
make
EOF_1

WORKDIR /testbed
"""

DOCKERFILE_JAVA = """FROM --platform=linux/amd64 maven:3.9.12-eclipse-temurin-11-noble

RUN curl -fsSL -o mvnd.zip https://downloads.apache.org/maven/mvnd/1.0.2/maven-mvnd-1.0.2-linux-amd64.zip && \\
    unzip mvnd.zip -d /tmp && \\
    mv /tmp/maven-mvnd-1.0.2-linux-amd64 /usr/local/mvnd && \\
    rm mvnd.zip && \\
    rm -rf /tmp/maven-mvnd-1.0.2-linux-amd64
"""

EVAL_SCRIPT = """#!/bin/bash
set -uxo pipefail
cd /testbed
git checkout abc123 tests/unit/list.tcl
git apply -v - <<'EOF_114329324912'
diff --git a/tests/unit/list.tcl b/tests/unit/list.tcl
EOF_114329324912
make distclean
make
(TERM=dumb ./runtest --single unit/list) | cat
git checkout abc123 tests/unit/list.tcl
"""


def row(instance_id, repo):
    return {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": "abc123",
        "eval_script": EVAL_SCRIPT,
        "problem_statement": "broken",
        "patch": "",
        "test_patch": "",
    }


ROWS = [
    row("redis__redis-1", "redis/redis"),
    row("tokio-rs__tokio-2", "tokio-rs/tokio"),
    row("preactjs__preact-3", "preactjs/preact"),
]


class Docker:
    """Records docker calls; images are a set; `buildx build` adds its tag."""

    def __init__(self):
        self.calls = []
        self.present = set()
        self.builder = False
        self.check_output = "ok\n"
        self.build_fails = set()

    def run(self, args, timeout=None, input_text=None):
        self.calls.append(list(args))
        verb = " ".join(args[:2]) if args[0] in ("buildx", "image") else args[0]
        code, out = getattr(self, "_" + verb.replace(" ", "_"), lambda a: (0, ""))(args)
        return subprocess.CompletedProcess(args, code, out, "")

    def _buildx_inspect(self, args):
        return (0 if self.builder else 1), ""

    def _buildx_create(self, args):
        self.builder = True
        return 0, ""

    def _buildx_build(self, args):
        tag = args[args.index("-t") + 1]
        if tag in self.build_fails:
            return 1, "error"
        self.present.add(tag)
        return 0, "built"

    def _images(self, args):
        prefix = args[1] + ":"
        return 0, "".join(
            f"{i.split(':')[1]}\n" for i in sorted(self.present) if i.startswith(prefix)
        )

    def _image_inspect(self, args):
        if args[2] not in self.present:
            return 1, ""
        if "{{.Size}}" in args:
            return 0, "2500000000\n"
        if "{{json .Config.Env}}" in args:
            return (
                0,
                '["PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin","RUSTUP_HOME=/x"]\n',
            )
        return 0, f"[] sha256:{abs(hash(args[2])) % 10**8:08d}"

    def _exec(self, args):
        return 0, (self.check_output if "bash" in args else "")

    def _rmi(self, args):
        self.present.discard(args[-1])
        return 0, ""


@pytest.fixture
def machine(monkeypatch):
    """A machine with the Multilingual snapshot and a task repository checked out."""
    docker = Docker()
    monkeypatch.setattr(
        SweBenchDocker, "run", classmethod(lambda cls, *a, **k: docker.run(*a, **k))
    )
    monkeypatch.setattr(SweBenchImages, "fetch_tags", classmethod(lambda cls: ["django-django-1"]))
    # The disk check reads the real disk (a CI runner has 8 GiB free); these tests are about the builds.
    monkeypatch.setattr(swe_bench_image_builder, "BUILD_HEADROOM_BYTES", 0)
    git_calls = []

    def fake_git(command, **kwargs):
        git_calls.append(list(command))
        if command[1] == "clone":
            repo = SweBenchImageBuilder.task_repo_dir("multilingual")
            for item, dockerfile in (
                ("redis__redis-1", DOCKERFILE_C),
                ("tokio-rs__tokio-2", "FROM --platform=linux/amd64 rust:1.85\nWORKDIR /testbed\n"),
                ("preactjs__preact-3", DOCKERFILE_C + "RUN apt install -y google-chrome-stable\n"),
            ):
                (repo / "tasks" / item).mkdir(parents=True)
                (repo / "tasks" / item / "Dockerfile").write_text(dockerfile)
            (repo / ".git").mkdir()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(SweBenchImageBuilder, "execute", staticmethod(fake_git))
    snapshot = SweBenchHarness.snapshot_path("multilingual")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    snapshot.with_suffix(".meta.json").write_text(
        json.dumps({"revision": swe_bench_settings.DATASET_REVISIONS[MULTILINGUAL], "rows": 3})
    )
    return docker, git_calls


def test_multilingual_is_a_dataset_pinned_to_a_revision_with_a_task_repository():
    assert SweBenchSettings.dataset_id("multilingual") == MULTILINGUAL
    assert SweBenchSettings.revision("multilingual") == "846e647b9f33c0b51b739d005d13d85493c9af09"
    assert SweBenchSettings.task_repo("multilingual")["commit"].startswith("6e08cbcd")
    assert SweBenchSettings.revision("verified") is None, "Verified keeps the snapshot it has"
    assert SweBenchSettings.task_repo("verified") is None
    assert "built here" in SweBenchRunner.image_source("multilingual")
    assert SweBenchRunner.image_source("verified") == f"community ({COMMUNITY})"


def test_the_download_asks_for_the_pinned_revision_and_replaces_a_snapshot_of_another(monkeypatch):
    calls = []

    def fake(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"revision": command[-1], "rows": 1}) + '\n{"instance_id": "a__b-1"}\n',
            "",
        )

    monkeypatch.setattr(SweBenchHarness, "execute", staticmethod(fake))
    path = SweBenchHarness.snapshot_path("multilingual")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"instance_id": "old"}\n')
    path.with_suffix(".meta.json").write_text(json.dumps({"revision": "older", "rows": 1}))
    meta = SweBenchHarness.download_dataset("multilingual")
    assert calls and calls[0][-2:] == [
        MULTILINGUAL,
        swe_bench_settings.DATASET_REVISIONS[MULTILINGUAL],
    ]
    assert meta["revision"] == swe_bench_settings.DATASET_REVISIONS[MULTILINGUAL]
    assert SweBenchHarness.download_dataset("multilingual") == meta and len(calls) == 1, (
        "the pinned snapshot is kept"
    )


def test_the_dockerfile_loses_its_amd64_pin_and_gains_the_agent_layer():
    out, problem = SweBenchImageBuilder.arm64_dockerfile(DOCKERFILE_C)
    assert problem is None
    assert "--platform" not in out and out.startswith("FROM ubuntu:22.04")
    assert "chmod -R a+rwX" in out and "cargo fetch" not in out and "GOPATH" not in out
    rust, _ = SweBenchImageBuilder.arm64_dockerfile("FROM --platform=linux/amd64 rust:1.85\n")
    assert "RUN cd /testbed && cargo fetch" in rust
    assert rust.index("cargo fetch") < rust.index("chmod -R a+rwX"), (
        "caches are opened after they are filled"
    )
    go, _ = SweBenchImageBuilder.arm64_dockerfile(
        "FROM --platform=linux/amd64 ubuntu:jammy\nRUN url='https://dl.google.com/go/go1.23.8.linux-arm64.tar.gz'\n"
    )
    assert "ENV GOPATH=/root/go GOCACHE=/root/.cache/go-build GOTOOLCHAIN=local" in go


def test_maven_images_take_mvnd_from_the_archive_with_its_jvm_client():
    out, problem = SweBenchImageBuilder.arm64_dockerfile(DOCKERFILE_JAVA)
    assert problem is None
    assert (
        "https://archive.apache.org/dist/maven/mvnd/1.0.2/" in out
        and "downloads.apache.org" not in out
    )
    assert "ln -sf mvnd.sh /usr/local/mvnd/bin/mvnd && \\\n    rm mvnd.zip" in out
    assert "MAVEN_OPTS=-Dmaven.repo.local=/root/.m2/repository" in out


def test_a_dockerfile_that_installs_chrome_is_refused():
    out, problem = SweBenchImageBuilder.arm64_dockerfile(
        DOCKERFILE_C + "RUN apt install google-chrome-stable\n"
    )
    assert out is None and "Chrome" in problem


def test_the_agent_keeps_the_image_toolchain_on_its_path_and_verified_is_unchanged():
    verified = "/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert SweBenchInstanceRun.container_path(verified) == CONTAINER_PATH
    assert SweBenchInstanceRun.container_path(None) == CONTAINER_PATH
    rust = SweBenchInstanceRun.container_path("/usr/local/cargo/bin:/usr/local/bin:/usr/bin")
    assert rust == f"{CONTAINER_PATH}:/usr/local/cargo/bin"


def test_the_image_path_is_read_from_the_image_config(machine):
    docker, _ = machine
    docker.present.add("some:image")
    assert (
        SweBenchDocker.image_path("some:image")
        == "/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin"
    )
    assert SweBenchDocker.image_path("absent:image") is None


def test_a_community_image_comes_first_and_a_built_one_second(machine):
    docker, _ = machine
    docker.present.add(f"{BUILT}:redis-redis-1")
    assert SweBenchImages.image_for("django__django-1") == f"{COMMUNITY}:django-django-1"
    assert SweBenchImages.image_for("redis__redis-1") == f"{BUILT}:redis-redis-1"
    assert SweBenchImages.image_for("tokio-rs__tokio-2") is None


def test_a_built_image_is_never_removed_by_a_run(machine):
    docker, _ = machine
    built, pulled = f"{BUILT}:redis-redis-1", f"{COMMUNITY}:django-django-1"
    docker.present |= {built, pulled}
    assert SweBenchImages.remove([built, pulled]) == 1
    assert built in docker.present and pulled not in docker.present


def test_validation_records_a_built_image_as_built_and_leaves_an_unbuilt_one_unjudged(
    machine, monkeypatch
):
    docker, _ = machine
    docker.present.add(f"{BUILT}:redis-redis-1")
    graded = []

    def evaluate(cls, work, dataset_file, run_id, ids, predictions, *rest):
        graded.append([json.loads(line)["image"] for line in dataset_file.read_text().splitlines()])
        return True

    verdicts = {"gold": {"resolved": True}, "noop": {"resolved": False}}
    monkeypatch.setattr(SweBenchHarness, "evaluate", classmethod(evaluate))
    monkeypatch.setattr(
        SweBenchHarness,
        "instance_report",
        classmethod(
            lambda cls, work, run_id, model, i: verdicts["gold" if model == "gold" else "noop"]
        ),
    )
    outcome = SweBenchEvaluator.validate(
        "multilingual",
        ["redis__redis-1", "tokio-rs__tokio-2"],
        SweBenchSettings({"disk_reserve": "0"}),
    )
    assert outcome["redis__redis-1"] is None
    assert outcome["tokio-rs__tokio-2"].startswith("not validated yet")
    assert graded[0] == [f"{BUILT}:redis-redis-1"]
    assert SweBenchImages.validated()["redis__redis-1"]["source"] == "built"
    assert "tokio-rs__tokio-2" not in SweBenchImages.rejected(), "an unbuilt image is no verdict"


def test_the_test_command_is_what_runs_between_the_patch_and_the_reset():
    assert SweBenchImageBuilder.test_command(EVAL_SCRIPT) == (
        "make distclean\nmake\n(TERM=dumb ./runtest --single unit/list) | cat"
    )
    assert SweBenchImageBuilder.test_command("#!/bin/bash\nmake\n") is None


def test_build_makes_the_capped_builder_builds_each_image_and_records_it(machine):
    docker, git_calls = machine
    settings = SweBenchSettings({"disk_reserve": "0", "build_memory": "16g", "build_cpus": "0-3"})
    outcome = SweBenchImageBuilder.build(
        "multilingual", ["redis__redis-1", "tokio-rs__tokio-2", "preactjs__preact-3"], settings
    )
    assert outcome["redis__redis-1"] is None and outcome["tokio-rs__tokio-2"] is None
    assert "Chrome" in outcome["preactjs__preact-3"]
    assert git_calls[0][:2] == ["git", "clone"]
    assert any(
        c[:2] == ["git", "-C"] and "6e08cbcd763c869abd7aae4bcb9e1b3991180777" in c
        for c in git_calls
    )
    create = next(c for c in docker.calls if c[:2] == ["buildx", "create"])
    assert "memory=16g,memory-swap=16g,cpuset-cpus=0-3" in create
    builds = [c for c in docker.calls if c[:2] == ["buildx", "build"]]
    assert len(builds) == 2 and all(swe_bench_settings.BUILDER_NAME in c for c in builds)
    context = SweBenchImageBuilder.build_dir() / "redis__redis-1" / "Dockerfile"
    assert context.read_text().startswith("FROM ubuntu:22.04")
    record = SweBenchImageBuilder.records()
    assert record["redis__redis-1"]["image"] == f"{BUILT}:redis-redis-1"
    assert (
        record["redis__redis-1"]["size"] == 2500000000
        and record["redis__redis-1"]["agent_check"] is None
    )
    assert record["preactjs__preact-3"]["image"] is None
    again = len(docker.calls)
    SweBenchImageBuilder.build("multilingual", ["redis__redis-1"], settings)
    assert not [c for c in docker.calls[again:] if c[:2] == ["buildx", "build"]], (
        "a built image is kept"
    )


def test_the_agent_check_notes_a_permission_failure_without_failing_the_build(machine):
    docker, _ = machine
    docker.check_output = (
        "error: failed to open: /usr/local/cargo/.package-cache: Permission denied\n"
    )
    settings = SweBenchSettings({"disk_reserve": "0"})
    outcome = SweBenchImageBuilder.build("multilingual", ["tokio-rs__tokio-2"], settings)
    assert outcome["tokio-rs__tokio-2"] is None
    note = SweBenchImageBuilder.records()["tokio-rs__tokio-2"]["agent_check"]
    assert "Permission denied" in note
    check = next(c for c in docker.calls if c[0] == "run" and "--network" in c)
    assert check[check.index("--network") + 1] == "none"
    applied = [c for c in docker.calls if c[0] == "exec" and "apply" in c]
    assert applied == [], "the test rows carry no patches"
    user_exec = next(c for c in docker.calls if c[0] == "exec" and "bash" in c)
    assert user_exec[user_exec.index("-u") + 1] != "0"


def test_build_stops_when_the_disk_reserve_would_be_crossed(machine):
    docker, _ = machine
    outcome = SweBenchImageBuilder.build(
        "multilingual",
        ["redis__redis-1", "tokio-rs__tokio-2"],
        SweBenchSettings({"disk_reserve": "1000000G"}),
    )
    assert all(problem.startswith("not built:") for problem in outcome.values())
    assert not [c for c in docker.calls if c[:2] == ["buildx", "build"]]


def test_build_images_refuses_a_dataset_whose_images_are_pulled(capsys):
    parser = __import__("argparse").ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers())
    args = parser.parse_args(["swe-bench", "build-images", "--dataset", "verified"])
    assert SweBenchCommand.dispatch(args) == 2
    assert "pulled" in capsys.readouterr().out


def test_build_images_selects_by_language(machine, monkeypatch):
    chosen = []
    monkeypatch.setattr(SweBenchHarness, "install", classmethod(lambda cls: True))
    monkeypatch.setattr(
        SweBenchImageBuilder,
        "build",
        classmethod(
            lambda cls, dataset, ids, settings, force=False: (
                chosen.extend(ids) or dict.fromkeys(ids)
            )
        ),
    )
    parser = __import__("argparse").ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers())
    args = parser.parse_args(["swe-bench", "build-images", "--languages", "c,rust"])
    assert SweBenchCommand.dispatch(args) == 0
    assert chosen == ["redis__redis-1", "tokio-rs__tokio-2"]
    assert SweBenchImageBuilder.language(ROWS[2]) == "js"


def test_the_agent_check_applies_the_test_patch_and_the_fix_before_the_tests(machine):
    docker, _ = machine
    docker.present.add(f"{BUILT}:redis-redis-1")
    checked = dict(ROWS[0], test_patch="diff --git a/t b/t\n", patch="diff --git a/s b/s\n")
    SweBenchImageBuilder.agent_check(checked, f"{BUILT}:redis-redis-1", "/usr/bin")
    execs = [c for c in docker.calls if c[0] == "exec"]
    applies = [i for i, c in enumerate(execs) if "apply" in c]
    tests = next(i for i, c in enumerate(execs) if "bash" in c)
    assert len(applies) == 2 and max(applies) < tests
    assert all(execs[i][execs[i].index("-u") + 1] != "0" for i in applies)


def test_a_maven_image_runs_one_existing_test_of_the_graded_module_at_build():
    script = (
        "git apply -v - <<'EOF_1'\ndiff\nEOF_1\n"
        "(mvnd test -B -T 1C -pl gson -Dtest=com.google.gson.FooTest#bar) | cat\n"
        "git checkout abc gson/src/test/x.java\n"
    )
    out, _ = SweBenchImageBuilder.arm64_dockerfile(DOCKERFILE_JAVA, script)
    warm = next(line for line in out.splitlines() if "failIfNoSpecifiedTests" in line)
    assert "find gson/src/test -name '*Test.java'" in warm
    assert 'mvn test -B -q -pl gson -Dtest="$cls"' in warm and "FooTest" not in warm
    assert out.index("failIfNoSpecifiedTests") < out.index("chmod -R a+rwX")
    plain, _ = SweBenchImageBuilder.arm64_dockerfile(DOCKERFILE_JAVA, "make test\n")
    assert "failIfNoSpecifiedTests" not in plain
