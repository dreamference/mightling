"""
SWE-rebench's instance images for this machine's architecture, built from the SWE-rebench fork's
recipes (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §28).

SWE-rebench publishes `swerebench/sweb.eval.x86_64.*` images only. The fork of the harness that
built them (`SWE-rebench/SWE-bench-fork`, `dockerfiles/python.py`, `test_spec/python.py`) takes
the platform as a parameter and only its `make_test_spec` pins x86-64, so the same three layers
are made here for `linux/arm64`: a base (Ubuntu 22.04, the build tools, Miniconda
`py311_23.11.0-2` for aarch64), the instance's conda environment `testbed` from its
`install_config`, and the repository at `/testbed` at the base commit after the instance's
`pre_install` and `install`, committed so `git diff` shows only what is changed later.

Two differences from the fork, neither of which changes what the image holds:

- **No `docker build`.** Each layer is a container started with a memory and CPU cap (BuildKit
  ignores `docker build --memory`) and committed: a build is capped wherever it runs.
- **The repository is cloned before the environment is made**, and the requirements or
  `environment.yml` are read from the clone at the environment commit (`git show`) instead of
  from raw.githubusercontent.com, through the fork's own processing of the files.

An image is built anywhere (`ling-admin swe-bench build`), validated where it was built, and kept
as an archive (`docker save`, gzip) with the record of its validation, so a machine that runs the
benchmark loads the very image that was validated (`ling-admin swe-bench import`). After each
instance the image and its container go, so Docker holds one instance image at a time beside the
base; the archives accumulate, and each is a whole image (`docker save` writes every layer, the
base's too): a few GB per instance, so a month of 110 is several hundred GB of archives.
"""

import gzip
import json
import os
import platform
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_rebench_rows import ENV_NAME, REPO_DIRECTORY, SweBenchRebenchRows

# The fork's DEFAULT_DOCKER_SPECS for Python.
UBUNTU_VERSION: Final[str] = "22.04"
CONDA_VERSION: Final[str] = "py311_23.11.0-2"

# The fork's `_DOCKERFILE_BASE_PY`, as the commands of one container.
BASE_SCRIPT: Final[str] = """set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
apt update && apt install -y \\
wget \\
git \\
build-essential \\
libffi-dev \\
libtiff-dev \\
python3 \\
python3-pip \\
python-is-python3 \\
jq \\
curl \\
locales \\
locales-all \\
tzdata \\
&& rm -rf /var/lib/apt/lists/*
wget 'https://repo.anaconda.com/miniconda/Miniconda3-{conda_version}-Linux-{conda_arch}.sh' -O miniconda.sh
bash miniconda.sh -b -p /opt/miniconda3
rm -f miniconda.sh
export PATH=/opt/miniconda3/bin:$PATH
conda init --all
conda config --append channels conda-forge
adduser --disabled-password --gecos 'dog' nonroot
"""

# The base image's environment, which `docker commit` keeps (the fork's `ENV` lines).
BASE_ENV: Final[tuple] = (
    "ENV PATH=/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "ENV TZ=Etc/UTC",
)

# The fork's `get_requirements_by_commit` and `get_environment_yml_by_commit`, reading the clone
# instead of raw.githubusercontent.com. Runs with the base image's python3.
REQUIREMENTS_HELPER: Final[str] = r'''
import subprocess, sys
from collections import OrderedDict

def read(commit, path):
    shown = subprocess.run(["git", "-C", "/testbed", "show", f"{commit}:{path}"], capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else None

mode, commit, paths = sys.argv[1], sys.argv[2], sys.argv[3:]
if mode == "yml":
    for path in paths:
        text = read(commit, path)
        if text is not None:
            print("\n".join("name: testbed" if line.startswith("name:") else line for line in text.split("\n")))
            sys.exit(0)
    sys.exit(f"no environment.yml at {paths}")
lines = []
for path in paths:
    text = read(commit, path)
    if text is not None:
        lines += text.split("\n")
if not lines:
    sys.exit(0)
excluded = lambda line: any(line.strip().startswith(x) for x in ["-e .", "#", ".[test", "-c"]) or line.strip() == "."
directory = "/".join(paths[-1].split("/")[:-1])
original, additional = [], []
for line in lines:
    if line.strip().startswith("-r"):
        name = line[len("-r"):].strip()
        text = read(commit, f"{directory}/{name}" if directory else name)
        if text is not None:
            additional += [extra for extra in text.split("\n") if not excluded(extra)]
    elif not excluded(line):
        original.append(line)
package = lambda line: line.split("==")[0].split(">=")[0].split("<=")[0].strip()
deduped = OrderedDict()
for line in original + additional:
    if package(line) and package(line) not in deduped:
        deduped[package(line)] = line
print("\n".join(deduped.values()))
'''

# Where the build steps keep their helper inside the build container.
HELPER_PATH: Final[str] = "/root/mightling_requirements.py"

# The heredoc delimiter of the fork's environment scripts.
ENV_DELIMITER: Final[str] = "EOF_59812759871"

BUILD_CONTAINER_PREFIX: Final[str] = "mightling-rebench-build-"


class SweBenchImageBuilder:
    """Builds, validates, archives and cleans up SWE-rebench instance images."""

    @classmethod
    def platform(cls) -> str:
        """
        Returns:
            str: The Docker platform of this machine's images (`linux/arm64/v8`, `linux/amd64`).
        """
        return {"arm64": "linux/arm64/v8", "amd64": "linux/amd64"}.get(
            swe_bench_settings.SweBenchSettings.architecture(), "linux/arm64/v8")

    @classmethod
    def base_image(cls) -> str:
        """
        Returns:
            str: The local name of the base layer every instance image starts from.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return f"{swe_bench_settings.BUILT_IMAGE_PREFIX}-base-{architecture}:ubuntu{UBUNTU_VERSION}-conda-{CONDA_VERSION}"

    # -- the scripts ---------------------------------------------------------------------------

    @classmethod
    def base_script(cls) -> str:
        """
        Returns:
            str: The base layer's commands, for this machine's Miniconda.
        """
        conda_arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(
            swe_bench_settings.SweBenchSettings.architecture(), "aarch64")
        return BASE_SCRIPT.format(conda_version=CONDA_VERSION, conda_arch=conda_arch)

    @classmethod
    def clone_script(cls, row: Dict[str, Any]) -> str:
        """
        The first half of the fork's `make_repo_script_list_py`: the repository at `/testbed`,
        at the base commit, with no remote. The environment commit must be in its history.

        Args:
            row: A SWE-rebench row.

        Returns:
            str: The commands.
        """
        repo, base = row["repo"], row["base_commit"]
        environment = row.get("environment_setup_commit") or base
        return "\n".join([
            "set -euxo pipefail",
            f"git clone -o origin https://github.com/{repo} {REPO_DIRECTORY}",
            f"chmod -R 777 {REPO_DIRECTORY}",
            f"cd {REPO_DIRECTORY}",
            f"git reset --hard {base}",
            f"git cat-file -e {environment}^{{commit}}",
            "git remote remove origin",
            f"cat > {HELPER_PATH} <<'{ENV_DELIMITER}'\n{REQUIREMENTS_HELPER.strip()}\n{ENV_DELIMITER}",
        ]) + "\n"

    @classmethod
    def env_script(cls, row: Dict[str, Any]) -> str:
        """
        The fork's `make_env_script_list_py`: the conda environment `testbed`, then the fork's
        environment line for every later shell.

        Args:
            row: A SWE-rebench row.

        Returns:
            str: The commands.
        """
        config = SweBenchRebenchRows.install_config(row)
        python = config.get("python") or "3.9"
        packages = config.get("packages") or ""
        commit = row.get("environment_setup_commit") or row["base_commit"]
        commands = ["set -euxo pipefail", "source /opt/miniconda3/bin/activate"]
        if packages == "requirements.txt":
            paths = " ".join(shlex.quote(path) for path in config.get("reqs_path") or [])
            commands += [
                f"conda create -n {ENV_NAME} python={python} -y",
                f"python3 {HELPER_PATH} txt {commit} {paths} > $HOME/requirements.txt",
                f"conda activate {ENV_NAME} && python -m pip install -r $HOME/requirements.txt",
                "rm $HOME/requirements.txt",
            ]
        elif packages == "environment.yml":
            paths = " ".join(shlex.quote(path) for path in config.get("env_yml_path") or [])
            commands.append(f"python3 {HELPER_PATH} yml {commit} {paths} > environment.yml")
            if config.get("no_use_env"):
                commands += [f"conda create -c conda-forge -n {ENV_NAME} python={python} -y",
                             "conda env update -f environment.yml"]
            else:
                commands += ["conda env create --file environment.yml",
                             f"conda activate {ENV_NAME} && conda install python={python} -y"]
            commands.append("rm environment.yml")
        else:
            commands.append(f"conda create -n {ENV_NAME} python={python} {packages} -y")
        commands.append(f"conda activate {ENV_NAME}")
        if config.get("pip_packages"):
            commands.append(f"python -m pip install {' '.join(config['pip_packages'])}")
        commands.append(f'echo "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate {ENV_NAME}" > /root/.bashrc')
        return "\n".join(commands) + "\n"

    @classmethod
    def repo_script(cls, row: Dict[str, Any]) -> str:
        """
        The second half of the fork's `make_repo_script_list_py`: `pre_install` and `install` in the
        environment, then the commit that keeps the setup out of later diffs. A leading `sudo ` is
        dropped from a `pre_install` line (the build runs as root and the base has no sudo).

        Args:
            row: A SWE-rebench row.

        Returns:
            str: The commands.
        """
        config = SweBenchRebenchRows.install_config(row)
        commands = ["set -euxo pipefail", "source /opt/miniconda3/bin/activate", f"conda activate {ENV_NAME}",
                    'echo "Current environment: $CONDA_DEFAULT_ENV"', f"cd {REPO_DIRECTORY}"]
        commands += [re.sub(r"^sudo\s+", "", line) for line in config.get("pre_install") or []]
        if config.get("install"):
            commands.append(config["install"])
        commands += ["git config --global user.email setup@swebench.config",
                     "git config --global user.name SWE-bench",
                     "git commit --allow-empty -am SWE-bench"]
        return "\n".join(commands) + "\n"

    # -- building ------------------------------------------------------------------------------

    @classmethod
    def ensure_base(cls, settings: "swe_bench_settings.SweBenchSettings", log: Path) -> Optional[str]:
        """
        Builds the base layer once per machine.

        Args:
            settings: Benchmark settings (the build caps).
            log: The log the build's output is appended to.

        Returns:
            Optional[str]: Why there is no base image; None when it is present.
        """
        if SweBenchDocker.image_digest(cls.base_image()):
            return None
        return cls._layer(f"ubuntu:{UBUNTU_VERSION}", cls.base_image(), [("base", cls.base_script())],
                          settings, log, "base", BASE_ENV)

    @classmethod
    def build(cls, row: Dict[str, Any], settings: "swe_bench_settings.SweBenchSettings",
              log: Path) -> Optional[str]:
        """
        Builds one instance's image.

        Args:
            row: A SWE-rebench row.
            settings: Benchmark settings (the build caps and the step time limit).
            log: The log the build's output is written to.

        Returns:
            Optional[str]: Why it could not be built; None when the image is present.
        """
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("")
        problem = cls.ensure_base(settings, log)
        if problem:
            return problem
        steps = [("clone", cls.clone_script(row)), ("environment", cls.env_script(row)),
                 ("install", cls.repo_script(row))]
        return cls._layer(cls.base_image(), SweBenchImages.built_image(row["instance_id"]), steps,
                          settings, log, row["instance_id"], (f"WORKDIR {REPO_DIRECTORY}/", 'CMD ["/bin/bash"]'))

    @classmethod
    def _layer(cls, source: str, target: str, steps: List[Tuple[str, str]],
               settings: "swe_bench_settings.SweBenchSettings", log: Path, label: str,
               changes: tuple) -> Optional[str]:
        """
        Runs the steps in a capped container from `source` and commits it as `target`; the
        container goes whatever happens.

        Returns:
            Optional[str]: Why the layer could not be made; None when it is.
        """
        container = BUILD_CONTAINER_PREFIX + re.sub(r"[^A-Za-z0-9_.-]", "-", label)
        SweBenchDocker.run(["rm", "-f", container], timeout=120)
        started = SweBenchDocker.run(
            ["run", "-d", "--name", container, "--platform", cls.platform(),
             "--memory", settings.build_memory, "--memory-swap", settings.build_memory,
             "--cpus", str(settings.build_cpus), source, "sleep", "infinity"], timeout=1800)
        cls._append(log, f"$ docker run {source} ({label})", started)
        try:
            if started.returncode != 0:
                return f"the build container could not start: {cls._last_line(started)}"
            for step, script in steps:
                result = SweBenchDocker.run(["exec", "-i", container, "bash", "-s"],
                                            timeout=settings.build_step_timeout_s, input_text=script)
                cls._append(log, f"$ {step} step ({label})", result)
                if result.returncode == 124:
                    return f"the {step} step ran past {settings.build_step_timeout_s // 60} min"
                if result.returncode != 0:
                    return f"the {step} step failed: {cls._last_line(result)}"
            command = ["commit"]
            for change in changes:
                command += ["--change", change]
            committed = SweBenchDocker.run(command + [container, target], timeout=1800)
            cls._append(log, f"$ docker commit {target}", committed)
            if committed.returncode != 0:
                return f"the image could not be committed: {cls._last_line(committed)}"
            return None
        finally:
            SweBenchDocker.run(["rm", "-f", container], timeout=300)

    @classmethod
    def _append(cls, log: Path, title: str, result: Any) -> None:
        with open(log, "a") as handle:
            handle.write(f"{title}: exit {result.returncode}\n{result.stdout or ''}{result.stderr or ''}\n")

    @classmethod
    def _last_line(cls, result: Any) -> str:
        lines = [line for line in f"{result.stdout or ''}\n{result.stderr or ''}".splitlines() if line.strip()]
        return lines[-1].strip()[:300] if lines else f"exit {result.returncode}"

    # -- archives ------------------------------------------------------------------------------

    @classmethod
    def export(cls, image: str, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """
        Writes a built image to its archive: `docker save`, then gzip, which `docker load` reads.

        Args:
            image: A built image.
            directory: The archive folder; None is the cache's.

        Returns:
            Optional[Dict[str, Any]]: `{file, sha256, bytes}`, or None when it could not be saved.
        """
        target = SweBenchImages.archive_path(image, directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        tar = target.with_name(f".{target.name}.{os.getpid()}.tar")
        staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            if SweBenchDocker.run(["save", "-o", str(tar), image], timeout=3600).returncode != 0 or not tar.exists():
                return None
            with open(tar, "rb") as source, gzip.open(staging, "wb", compresslevel=3) as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
            os.replace(staging, target)
        finally:
            tar.unlink(missing_ok=True)
            staging.unlink(missing_ok=True)
        return {"file": target.name, "sha256": SweBenchImages.file_sha256(target), "bytes": target.stat().st_size}

    # -- the build command ---------------------------------------------------------------------

    @classmethod
    def log_path(cls, instance_id: str) -> Path:
        """
        Args:
            instance_id: An instance.

        Returns:
            Path: Its build log.
        """
        return swe_bench_settings.BUILD_DIR / instance_id / "build.log"

    @classmethod
    def build_all(cls, dataset: str, instance_ids: List[str], settings: "swe_bench_settings.SweBenchSettings",
                  force: bool = False, keep_images: bool = False) -> Dict[str, Optional[str]]:
        """
        Builds, validates and archives instances one at a time: each image is built in capped
        containers, graded with its reference patch and a no-op patch, written to its archive when
        it validates, and removed (unless `keep_images`), so the disk holds one instance image
        at a time beside the base. The archive folder's `validated.json` is rewritten after each
        instance, so the folder is what another machine imports.

        Args:
            dataset: A SWE-rebench dataset name.
            instance_ids: The instances, in order.
            settings: Benchmark settings.
            force: Build again instances that already have a result.
            keep_images: Leave each built image in Docker.

        Returns:
            Dict[str, Optional[str]]: Instance id to the reason it is not usable, None when it is.
        """
        rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
        outcome: Dict[str, Optional[str]] = {}
        host = platform.node()
        for position, instance_id in enumerate(instance_ids, 1):
            validated, rejected = SweBenchImages.validated(dataset), SweBenchImages.rejected(dataset)
            if instance_id not in rows:
                outcome[instance_id] = f"not in {dataset}"
                continue
            image = SweBenchImages.built_image(instance_id)
            if not force and instance_id in validated and SweBenchImages.archive_path(image).exists():
                outcome[instance_id] = None
                continue
            if not force and instance_id in rejected:
                outcome[instance_id] = rejected[instance_id]
                continue
            disk = SweBenchEvaluator.disk_problem(settings)
            if disk:
                print(f"⚠️  Stopped before {instance_id}: {disk}")
                break
            started = time.time()
            print(f"🔨 [{position}/{len(instance_ids)}] {instance_id}: building ({settings.build_memory}, "
                  f"{settings.build_cpus} CPUs)...", flush=True)
            problem = cls.build(rows[instance_id], settings, cls.log_path(instance_id))
            if problem:
                problem = f"the {swe_bench_settings.SweBenchSettings.architecture()} image could not be built: {problem}"
                SweBenchImages.record_validation(instance_id, None, None, problem, dataset)
            else:
                problem = SweBenchEvaluator.validate(dataset, [instance_id], settings, force=True).get(instance_id)
            if problem is None:
                archive = cls.export(image)
                if archive is None:
                    problem = "the image could not be archived"
                    SweBenchImages.record_validation(instance_id, None, None, problem, dataset)
                else:
                    SweBenchImages.update_validated(instance_id, {
                        "archive": archive, "built_on": host, "validated_on": host,
                        "build_seconds": int(time.time() - started),
                        "recipe": f"SWE-rebench fork, {cls.platform()}, ubuntu {UBUNTU_VERSION}, conda {CONDA_VERSION}",
                    }, dataset)
            if not keep_images and SweBenchDocker.image_digest(image):
                SweBenchImages.remove([image])
            outcome[instance_id] = problem
            print(f"   {instance_id}: {'✅ validated and archived' if problem is None else '❌ ' + problem} "
                  f"({int(time.time() - started)} s)", flush=True)
            cls.write_folder_record(dataset)
        return outcome

    @classmethod
    def write_folder_record(cls, dataset: str) -> None:
        """
        Writes the archive folder's `validated.json`: the validated instances that have an
        archive in it, and every rejection, which is what `import_archives` reads.

        Args:
            dataset: A SWE-rebench dataset name.
        """
        instances = {instance_id: entry for instance_id, entry in SweBenchImages.validated(dataset).items()
                     if entry.get("archive") and SweBenchImages.archive_path(entry["image"]).exists()}
        record = {"instances": instances, "rejected": SweBenchImages.rejected(dataset),
                  "dataset": swe_bench_settings.SweBenchSettings.dataset_id(dataset),
                  "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        SweBenchImages.write_validated(record, path=swe_bench_settings.ARCHIVE_DIR / "validated.json")

    @classmethod
    def describe(cls, row: Dict[str, Any]) -> str:
        """
        Args:
            row: A SWE-rebench row.

        Returns:
            str: Every command a build of it runs, in order, for `build --dry-run`.
        """
        parts = [f"# base {cls.base_image()} (built once per machine)", cls.base_script(),
                 f"# {row['instance_id']}: clone", cls.clone_script(row),
                 "# environment", cls.env_script(row), "# install", cls.repo_script(row)]
        return "\n".join(parts) + "\n# eval script (grading)\n" + str(row.get("eval_script") or "") + \
            "\n# install_config\n" + json.dumps(SweBenchRebenchRows.install_config(row), indent=2) + "\n"
