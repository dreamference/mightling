"""Instance images built on this machine, for a dataset nobody publishes arm64 images for.

SWE-bench Multilingual's task repository (`swe-bench-multilingual-tasks`) holds one Dockerfile per
instance, every one pinned to `linux/amd64`. Their base images (`ubuntu:22.04`, `rust:*`, the
Temurin Maven images) are multi-architecture, so a native arm64 build needs only a few rewrites
(`arm64_dockerfile`), and one layer added at the end for the agent, which runs as the host's user
with no network where the grader runs as root with it: the toolchains' caches opened up, the
crates a Rust repository needs fetched, Go pointed at root's module cache. Nothing in that layer
changes what the tests do, and validation (the reference patch resolves, a no-op patch does not)
is still what admits an instance. `agent_check` adds what validation cannot see, as a note in the
build's record: whether the eval script's test command, run as the agent's user without the
network, shows a permission or download failure. It is a note and not a verdict, because some
suites print those words on purpose (coreutils tests "Permission denied"); a task list is drawn
from instances whose note is empty.

Builds run in a BuildKit builder container of their own (`BUILDER_NAME`) capped in memory and
cores: a Rust test build beside a resident model server must not be able to take the host down.
"""

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_images import SweBenchImages

# Each Multilingual repository's language (the dataset has no such column). C and C++ are kept
# apart: the owner works in both.
LANGUAGES: Final[dict[str, str]] = {
    "redis/redis": "c",
    "valkey-io/valkey": "c",
    "jqlang/jq": "c",
    "micropython/micropython": "c",
    "fmtlib/fmt": "cpp",
    "nlohmann/json": "cpp",
    "tokio-rs/tokio": "rust",
    "tokio-rs/axum": "rust",
    "sharkdp/bat": "rust",
    "astral-sh/ruff": "rust",
    "uutils/coreutils": "rust",
    "burntsushi/ripgrep": "rust",
    "nushell/nushell": "rust",
    "caddyserver/caddy": "go",
    "gin-gonic/gin": "go",
    "prometheus/prometheus": "go",
    "gohugoio/hugo": "go",
    "hashicorp/terraform": "go",
    "projectlombok/lombok": "java",
    "apache/lucene": "java",
    "google/gson": "java",
    "apache/druid": "java",
    "javaparser/javaparser": "java",
    "reactivex/rxjava": "java",
    "preactjs/preact": "js",
    "axios/axios": "js",
    "babel/babel": "js",
    "facebook/docusaurus": "js",
    "vuejs/core": "js",
    "mrdoob/three.js": "js",
    "immutable-js/immutable-js": "js",
    "rubocop/rubocop": "ruby",
    "fluent/fluentd": "ruby",
    "fastlane/fastlane": "ruby",
    "jekyll/jekyll": "ruby",
    "faker-ruby/faker": "ruby",
    "jordansissel/fpm": "ruby",
    "laravel/framework": "php",
    "briannesbitt/carbon": "php",
    "php-cs-fixer/php-cs-fixer": "php",
    "phpoffice/phpspreadsheet": "php",
}

# What `arm64_dockerfile` looks for in a task Dockerfile.
CHROME: Final[str] = "google-chrome"
PLATFORM_PIN: Final[str] = "--platform="
MVND: Final[str] = "maven-mvnd-"
GO_DOWNLOAD: Final[str] = "dl.google.com/go/"

# A Maven test command of an eval script: the tool and the module it tests.
MAVEN_TEST: Final[re.Pattern[str]] = re.compile(r"(mvnd|mvn|\./mvnw) test [^\n|&]*?-pl ([\w./-]+)")

# The warm-up runs Maven without mvnd's daemon, which would outlive the build step.
MAVEN_WITHOUT_DAEMON: Final[dict[str, str]] = {"mvnd": "mvn"}

# The upstream Dockerfiles fetch mvnd from a mirror that keeps only current releases (1.0.2 is
# gone from it); the archive keeps every one.
MVND_MIRROR: Final[str] = "https://downloads.apache.org/maven/mvnd/"
MVND_ARCHIVE: Final[str] = "https://archive.apache.org/dist/maven/mvnd/"

# The layer added at the end of every image. The agent runs as the host's user with HOME in its
# scratch directory: root's caches must be readable (and the tools' lock files writable) by it, and
# Go and Maven must look in root's caches, not in an empty HOME. For root, the grader's user, each
# variable names what is already the default.
AGENT_LAYER: Final[str] = (
    "\n# Mightling: the agent runs as the host's user, with no network\n"
    "RUN chmod 755 /root && for d in /usr/local/cargo /usr/local/rustup /root/go /root/.cache "
    '/root/.m2 /root/.gradle /root/.ivy2; do if [ -e "$d" ]; then chmod -R a+rwX "$d"; fi; done\n'
)
GO_LAYER: Final[str] = "ENV GOPATH=/root/go GOCACHE=/root/.cache/go-build GOTOOLCHAIN=local\n"
# Crates fetched while the network is there: tokio's and axum's Dockerfiles build nothing.
RUST_LAYER: Final[str] = "RUN cd /testbed && cargo fetch\n"
MAVEN_LAYER: Final[str] = (
    "ENV MAVEN_OPTS=-Dmaven.repo.local=/root/.m2/repository GRADLE_USER_HOME=/root/.gradle\n"
)

# What `agent_check` reads as the agent being unable to work, in the test command's output.
AGENT_FAILURES: Final[tuple[str, ...]] = (
    "Permission denied",
    "Read-only file system",
    "Could not resolve host",
    "Temporary failure in name resolution",
    "failed to download",
    "dial tcp",
    "Network is unreachable",
    "Could not transfer artifact",
)

# Seconds a build and an agent check may take.
BUILD_TIMEOUT_S: Final[int] = 2 * 3600
AGENT_CHECK_TIMEOUT_S: Final[int] = 30 * 60
# Free disk a build needs beyond the disk reserve (the largest image measured, ruff's, plus room).
BUILD_HEADROOM_BYTES: Final[int] = 20 * 1024**3


class SweBenchImageBuilder:
    """Builds, records and checks the instance images of a dataset that has a task repository."""

    # Seam: tests replace it so nothing in the suite runs git or reaches the network.
    execute: Callable[..., subprocess.CompletedProcess] = staticmethod(
        lambda command, **kwargs: subprocess.run(command, capture_output=True, text=True, **kwargs)
    )

    @classmethod
    def language(cls, row: dict[str, Any]) -> str:
        """Names a row's language.

        Args:
            row: A dataset row.

        Returns:
            str: Its repository's language (`c`, `cpp`, `rust`, `go`, `java`, `js`, `ruby`, `php`),
            `other` for a repository not in `LANGUAGES`.
        """
        return LANGUAGES.get(str(row.get("repo", "")).lower(), "other")

    # -- the task repository -------------------------------------------------------------------

    @classmethod
    def task_repo_dir(cls, dataset: str) -> Path:
        """Where a dataset's task repository is checked out.

        Args:
            dataset: A dataset name or id.

        Returns:
            Path: The directory.
        """
        name = swe_bench_settings.SweBenchSettings.dataset_id(dataset).split("/")[-1]
        return swe_bench_settings.CACHE_DIR / "task-repos" / name

    @classmethod
    def ensure_task_repo(cls, dataset: str) -> Path | None:
        """Checks the dataset's task repository out at its pinned commit, cloning it the first time.

        Args:
            dataset: A dataset name or id.

        Returns:
            Optional[Path]: The checkout; None when the dataset has none or git failed.
        """
        pinned = swe_bench_settings.SweBenchSettings.task_repo(dataset)
        if pinned is None:
            print(  # noqa: T201 — CLI output
                f"❌ {dataset} has no task repository: its images are pulled, not built."
            )
            return None
        directory = cls.task_repo_dir(dataset)
        if not (directory / ".git").exists():
            directory.parent.mkdir(parents=True, exist_ok=True)
            cloned = cls.execute(
                ["git", "clone", "--quiet", pinned["url"], str(directory)], timeout=1800
            )
            if cloned.returncode != 0:
                print(  # noqa: T201 — CLI output
                    f"❌ git clone {pinned['url']} failed: {cloned.stderr.strip()[-300:]}"
                )
                return None
        has_commit = cls.execute(
            ["git", "-C", str(directory), "cat-file", "-e", f"{pinned['commit']}^{{commit}}"],
            timeout=60,
        )
        if has_commit.returncode != 0:
            cls.execute(["git", "-C", str(directory), "fetch", "--quiet", "origin"], timeout=1800)
        checked = cls.execute(
            ["git", "-C", str(directory), "checkout", "--quiet", "--detach", pinned["commit"]],
            timeout=300,
        )
        if checked.returncode != 0:
            print(  # noqa: T201 — CLI output
                f"❌ Could not check out {pinned['commit'][:12]}: {checked.stderr.strip()[-300:]}"
            )
            return None
        return directory

    # -- the Dockerfile ------------------------------------------------------------------------

    @classmethod
    def arm64_dockerfile(cls, text: str, eval_script: str = "") -> tuple[str | None, str | None]:
        """Rewrites one task Dockerfile for a native arm64 build and adds the agent's layer.

        Args:
            text: The task repository's Dockerfile.
            eval_script: The instance's eval script, for the Maven warm-up (`maven_warmup`).

        Returns:
            Tuple[Optional[str], Optional[str]]: The rewritten Dockerfile, or None and why it
            cannot be built for arm64.
        """
        if CHROME in text:
            return None, "installs Google Chrome, which has no arm64 Linux build"
        out = re.sub(r"^FROM --platform=linux/amd64 ", "FROM ", text, flags=re.M)
        if PLATFORM_PIN in out:
            return None, "pins a platform this rewrite does not know"
        if MVND in out:
            # mvnd ships its native client for amd64 only; the same zip has the JVM client.
            out = out.replace(MVND_MIRROR, MVND_ARCHIVE)
            out = out.replace(
                "    rm mvnd.zip && \\",
                "    ln -sf mvnd.sh /usr/local/mvnd/bin/mvnd && \\\n    rm mvnd.zip && \\",
                1,
            )
        # The caches are opened up last, after everything that fills them.
        layers = ""
        if GO_DOWNLOAD in out:
            layers += GO_LAYER
        if re.search(r"^FROM rust:", out, flags=re.M):
            layers += RUST_LAYER
        if re.search(r"^FROM maven:", out, flags=re.M):
            layers += MAVEN_LAYER + cls.maven_warmup(eval_script)
        return out.rstrip("\n") + "\n" + layers + AGENT_LAYER, None

    @classmethod
    def maven_warmup(cls, eval_script: str) -> str:
        """A layer that runs one existing test of the module the eval script tests, with the network.

        Maven fetches the test runner's provider (`surefire-junit4` and the like) only when a test
        runs, so without this the agent's offline `mvn test` fails before any test does. The test
        run is one the repository already has, never one of the benchmark's.

        Args:
            eval_script: The instance's eval script.

        Returns:
            str: The `RUN` line; empty when the script runs no Maven test with `-pl`.
        """
        found = MAVEN_TEST.search(cls.test_command(eval_script) or "")
        if not found:
            return ""
        tool, module = found.group(1), found.group(2)
        tool = MAVEN_WITHOUT_DAEMON.get(tool, tool)
        return (
            f"RUN cd /testbed && cls=$(find {module}/src/test -name '*Test.java' 2>/dev/null | sort | head -1 "
            "| xargs -r basename | sed 's/[.]java$//') && if [ -n \"$cls\" ]; then "
            f'{tool} test -B -q -pl {module} -Dtest="$cls" -Dsurefire.failIfNoSpecifiedTests=false '
            "-DfailIfNoTests=false || true; fi\n"
        )

    # -- the builder ---------------------------------------------------------------------------

    @classmethod
    def ensure_builder(cls, settings: "swe_bench_settings.SweBenchSettings") -> bool:
        """Makes the capped BuildKit builder exist.

        Args:
            settings: Benchmark settings (`build_memory`, `build_cpus`).

        Returns:
            bool: True when the builder is there.
        """
        name = swe_bench_settings.BUILDER_NAME
        if SweBenchDocker.run(["buildx", "inspect", name], timeout=60).returncode == 0:
            return True
        options = f"memory={settings.build_memory},memory-swap={settings.build_memory}"
        if settings.build_cpus:
            options += f",cpuset-cpus={settings.build_cpus}"
        created = SweBenchDocker.run(
            [
                "buildx",
                "create",
                "--name",
                name,
                "--driver",
                "docker-container",
                "--driver-opt",
                options,
            ],
            timeout=300,
        )
        if created.returncode != 0:
            print(  # noqa: T201 — CLI output
                f"❌ Could not create the builder {name}: {created.stderr.strip()[-300:]}"
            )
            return False
        return True

    @classmethod
    def build_dir(cls) -> Path:
        """Where each build's context and log are kept."""
        return swe_bench_settings.CACHE_DIR / "build"

    @classmethod
    def record_path(cls) -> Path:
        """`built-<arch>.json` in the cache: every build's result."""
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return swe_bench_settings.CACHE_DIR / f"built-{architecture}.json"

    @classmethod
    def records(cls) -> dict[str, dict[str, Any]]:
        """Instance id to its last build's record."""
        try:
            record = json.loads(cls.record_path().read_text())
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    @classmethod
    def _record(cls, instance_id: str, entry: dict[str, Any]) -> None:
        record = cls.records()
        record[instance_id] = entry
        path = cls.record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        Path(staging).replace(path)

    @classmethod
    def disk_problem(cls, settings: "swe_bench_settings.SweBenchSettings") -> str | None:
        """Says whether there is room for another build.

        Args:
            settings: Benchmark settings (`disk_reserve`).

        Returns:
            Optional[str]: Why there is no room for another build; None when there is.
        """
        from dreamference.night_shift.night_shift_host import NightShiftHost

        swe_bench_settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(swe_bench_settings.CACHE_DIR).free
        needed = NightShiftHost.parse_size(settings.disk_reserve) + BUILD_HEADROOM_BYTES
        if free < needed:
            return f"{free / 1024**3:.0f} GiB free, a build needs {needed / 1024**3:.0f} (the reserve and 20)"
        return None

    @classmethod
    def build(
        cls,
        dataset: str,
        instance_ids: list[str],
        settings: "swe_bench_settings.SweBenchSettings",
        force: bool = False,
    ) -> dict[str, str | None]:
        """Builds the instances' images one at a time and records each result.

        Each is tagged `BUILT_IMAGE_REPO:<tag>`; an image already built is kept unless `force`.

        Args:
            dataset: The dataset the instances belong to.
            instance_ids: The instances.
            settings: Benchmark settings.
            force: Build again an image that is already there.

        Returns:
            Dict[str, Optional[str]]: Instance id to why its image could not be built, None when it is there.
        """
        outcome: dict[str, str | None] = {}
        from dreamference.swe_bench.swe_bench_harness import SweBenchHarness

        rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
        repo = cls.ensure_task_repo(dataset)
        if repo is None or not cls.ensure_builder(settings):
            return dict.fromkeys(instance_ids, "the task repository or the builder is missing")
        built = set(SweBenchImages.built_tags())
        commit = (swe_bench_settings.SweBenchSettings.task_repo(dataset) or {}).get("commit", "")
        for instance_id in instance_ids:
            if not force and instance_id.replace("__", "-") in built:
                outcome[instance_id] = None
                continue
            source = repo / "tasks" / instance_id / "Dockerfile"
            try:
                text = source.read_text()
            except OSError:
                outcome[instance_id] = f"no Dockerfile in the task repository at {commit[:12]}"
                continue
            dockerfile, problem = cls.arm64_dockerfile(
                text, rows.get(instance_id, {}).get("eval_script", "")
            )
            if dockerfile is None:
                outcome[instance_id] = problem
                cls._record(
                    instance_id,
                    {
                        "image": None,
                        "problem": problem,
                        "task_commit": commit,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                )
                continue
            disk = cls.disk_problem(settings)
            if disk:
                print(  # noqa: T201 — CLI output
                    f"⚠️  Stopping: {disk}"
                )
                outcome.update(
                    {rest: f"not built: {disk}" for rest in instance_ids if rest not in outcome}
                )
                break
            outcome[instance_id] = cls._build_one(
                instance_id, dockerfile, rows.get(instance_id, {}), commit
            )
        return outcome

    @classmethod
    def _build_one(
        cls, instance_id: str, dockerfile: str, row: dict[str, Any], commit: str
    ) -> str | None:
        """Builds one image from its rewritten Dockerfile, checks it as the agent and records it.

        Args:
            instance_id: The instance.
            dockerfile: Its arm64 Dockerfile.
            row: Its dataset row (empty: no agent check).
            commit: The task repository's commit, for the record.

        Returns:
            Optional[str]: Why the build failed; None when the image is there.
        """
        from dreamference.swe_bench.swe_bench_instance_run import SweBenchInstanceRun

        image = SweBenchImages.built_name(instance_id)
        context = cls.build_dir() / instance_id
        context.mkdir(parents=True, exist_ok=True)
        (context / "Dockerfile").write_text(dockerfile)
        log = cls.build_dir() / "logs" / f"{instance_id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        print(  # noqa: T201 — CLI output
            f"🔨 {instance_id} ({cls.language(row)})...", flush=True
        )
        started = time.time()
        result = SweBenchDocker.run(
            [
                "buildx",
                "build",
                "--builder",
                swe_bench_settings.BUILDER_NAME,
                "--load",
                "--progress",
                "plain",
                "-t",
                image,
                str(context),
            ],
            timeout=BUILD_TIMEOUT_S,
        )
        seconds = round(time.time() - started)
        log.write_text((result.stdout or "") + (result.stderr or ""))
        problem = (
            None
            if result.returncode == 0
            else f"the build failed (exit {result.returncode}; {log})"
        )
        size = cls.image_size(image) if problem is None else None
        note = None
        if problem is None and row:
            path = SweBenchInstanceRun.container_path(SweBenchDocker.image_path(image))
            note = cls.agent_check(row, image, path)
        cls._record(
            instance_id,
            {
                "image": image if problem is None else None,
                "problem": problem,
                "seconds": seconds,
                "size": size,
                "agent_check": note,
                "task_commit": commit,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        )
        sized = f", {size / 1e9:.1f} GB" if size else ""
        print(  # noqa: T201 — CLI output
            f"   {'✅' if problem is None else '❌'} {seconds} s{sized}{'' if problem is None else ': ' + problem}"
        )
        if note:
            print(  # noqa: T201 — CLI output
                f"   ⚠️  {note}"
            )
        return problem

    @classmethod
    def image_size(cls, image: str) -> int | None:
        """An image's size.

        Args:
            image: An image reference.

        Returns:
            Optional[int]: Its size in bytes; None when it is not there.
        """
        result = SweBenchDocker.run(
            ["image", "inspect", image, "--format", "{{.Size}}"], timeout=60
        )
        try:
            return int(result.stdout.strip()) if result.returncode == 0 else None
        except ValueError:
            return None

    # -- the agent's side ----------------------------------------------------------------------

    @classmethod
    def test_command(cls, eval_script: str) -> str | None:
        """Picks the test command out of an eval script.

        That is what runs after the test patch is applied and before the test files are reset again.

        Args:
            eval_script: The dataset row's `eval_script`.

        Returns:
            Optional[str]: The commands, one per line; None when the script has no such part.
        """
        lines = eval_script.split("\n")
        ends = [i for i, line in enumerate(lines) if re.fullmatch(r"EOF_\w+", line.strip())]
        if not ends:
            return None
        body = []
        for line in lines[ends[-1] + 1 :]:
            if line.startswith(("git checkout ", "rm -f ")):
                break
            if line.strip():
                body.append(line)
        return "\n".join(body) or None

    @classmethod
    def agent_check(cls, row: dict[str, Any], image: str, image_path: str) -> str | None:
        """Runs the graded tests as the agent's user, with no network, on the reference fix.

        The image is started as a run starts it (its tree opened up as `SCRUB_SCRIPT` does), the
        test patch and the reference patch are applied, and the eval script's test command runs:
        what the agent would run to check a correct fix. A permission or download failure is
        noted; the container is thrown away, so nothing of either patch stays in the image.

        Args:
            row: The instance's dataset row.
            image: Its image.
            image_path: The PATH the agent's container gets.

        Returns:
            Optional[str]: What may stop the agent, with the output around it; None when nothing showed.
        """
        command = cls.test_command(row.get("eval_script", ""))
        if command is None:
            return "the eval script has no test command to try"
        name = f"mightling-agent-check-{os.getpid()}"
        SweBenchDocker.run(["rm", "-f", name], timeout=120)
        started = SweBenchDocker.run(
            [
                "run",
                "-d",
                "--init",
                "--name",
                name,
                "--network",
                "none",
                "--memory",
                "8g",
                "-e",
                f"PATH={image_path}",
                "-w",
                "/testbed",
                image,
                "sleep",
                "infinity",
            ],
            timeout=300,
        )
        if started.returncode != 0:
            return f"the container did not start: {started.stderr.strip()[-200:]}"
        try:
            SweBenchDocker.run(
                ["exec", "-u", "0", name, "chmod", "-R", "a+rwX", "/testbed"], timeout=1800
            )
            user = f"{os.getuid()}:{os.getgid()}"
            for patch in (row.get("test_patch", ""), row.get("patch", "")):
                if patch.strip():
                    SweBenchDocker.run(
                        ["exec", "-i", "-u", user, name, "git", "-C", "/testbed", "apply", "-"],
                        timeout=300,
                        input_text=patch,
                    )
            result = SweBenchDocker.run(
                [
                    "exec",
                    "-u",
                    user,
                    "-e",
                    "HOME=/tmp",
                    "-e",
                    f"PATH={image_path}",
                    name,
                    "bash",
                    "-c",
                    f"cd /testbed && {command}",
                ],
                timeout=AGENT_CHECK_TIMEOUT_S,
            )
        finally:
            SweBenchDocker.run(["rm", "-f", name], timeout=120)
        output = (result.stdout or "") + (result.stderr or "")
        for failure in AGENT_FAILURES:
            if failure in output:
                at = output.index(failure)
                return f"as the agent's user offline: …{output[max(0, at - 160) : at + 80].strip()}"
        return None
