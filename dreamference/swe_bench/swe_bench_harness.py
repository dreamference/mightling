"""
The upstream SWE-bench harness, in a virtualenv of its own
(specs/DREAMFERENCE_PUFFIN_SWE_BENCH.md §6.1, §12).

Grading is always the harness's: this module installs it, hands it a dataset file and a
predictions file, and reads the per-instance reports it writes. Two things are done to make it
work on arm64, neither of which touches how a patch is graded:

- **The dataset is a local file** whose `image` column names the arm64 image of each instance.
  The published rows name `swebench/sweb.eval.x86_64.…` images this machine cannot run, and the
  harness accepts a local `.jsonl` wherever it accepts a dataset name.
- **A "no-op" patch stands in for the empty one** when validating: the harness never starts a
  container for an empty patch (it files it under "empty patches"), so "an empty patch must not
  resolve" would test nothing. The no-op adds one unrelated file.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Final, Iterable, List, Optional

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker

# A patch that applies to any repository and fixes nothing.
NOOP_PATCH: Final[str] = (
    "diff --git a/.puffin-swe-bench-noop b/.puffin-swe-bench-noop\n"
    "new file mode 100644\n--- /dev/null\n+++ b/.puffin-swe-bench-noop\n@@ -0,0 +1 @@\n+noop\n"
)

# Dataset columns that give the answer away; never shown to the agent (§5.4).
FORBIDDEN_FIELDS: Final[tuple] = ("hints_text", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS")

# The harness's own layout under the directory it is run in.
HARNESS_LOG_DIR: Final[str] = "logs/run_evaluation"

# Fetches a dataset's `test` split with the harness's own `datasets` package and prints its rows
# sorted by instance id, with the snapshot's revision on the first line.
DATASET_DUMP: Final[str] = """
import json, sys
from datasets import load_dataset
dataset = load_dataset(sys.argv[1], split="test")
try:
    from huggingface_hub import HfApi
    revision = HfApi().dataset_info(sys.argv[1]).sha or ""
except Exception:
    revision = ""
print(json.dumps({"revision": revision, "rows": len(dataset)}))
for row in sorted((dict(row) for row in dataset), key=lambda row: row["instance_id"]):
    print(json.dumps(row))
"""


class SweBenchHarness:
    """Installs and calls `swebench`."""

    # Seam: tests replace it so nothing in the suite runs the real harness or `pip`.
    execute: Callable[..., subprocess.CompletedProcess] = staticmethod(
        lambda command, **kwargs: subprocess.run(command, capture_output=True, text=True, **kwargs))

    @classmethod
    def venv_dir(cls) -> Path:
        """
        Returns:
            Path: The harness's virtualenv.
        """
        return swe_bench_settings.CACHE_DIR / "venv"

    @classmethod
    def tool(cls, name: str) -> str:
        """
        Args:
            name: An executable of the virtualenv (`swebench`, `python`, `patchelf`).

        Returns:
            str: Its absolute path.
        """
        return str(cls.venv_dir() / "bin" / name)

    @classmethod
    def installed_version(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The installed harness version, or None when there is no virtualenv.
        """
        if not os.path.exists(cls.tool("python")):
            return None
        result = cls.execute([cls.tool("python"), "-c",
                              "import importlib.metadata as m; print(m.version('swebench'))"])
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    @classmethod
    def install(cls) -> bool:
        """
        Creates the virtualenv and installs the pinned harness and `patchelf` into it. The
        project's own `.venv` is not touched.

        Returns:
            bool: True when the pinned version is installed.
        """
        wanted = swe_bench_settings.HARNESS_VERSION
        if cls.installed_version() == wanted and os.path.exists(cls.tool("patchelf")):
            return True
        cls.venv_dir().parent.mkdir(parents=True, exist_ok=True)
        if not os.path.exists(cls.tool("python")):
            created = cls.execute([sys.executable, "-m", "venv", str(cls.venv_dir())])
            if created.returncode != 0:
                print(f"❌ Could not create {cls.venv_dir()}: {created.stderr.strip()[-300:]}")
                return False
        installed = cls.execute([cls.tool("python"), "-m", "pip", "install", "--quiet",
                                 "--disable-pip-version-check", f"swebench=={wanted}", "patchelf"])
        if installed.returncode != 0:
            print(f"❌ pip install swebench=={wanted} failed: {installed.stderr.strip()[-400:]}")
            return False
        return cls.installed_version() == wanted

    # -- the dataset ---------------------------------------------------------------------------

    @classmethod
    def snapshot_path(cls, dataset: str) -> Path:
        """
        Args:
            dataset: A dataset name or id.

        Returns:
            Path: The local snapshot of its `test` split, rows sorted by instance id.
        """
        name = swe_bench_settings.SweBenchSettings.dataset_id(dataset).split("/")[-1]
        return swe_bench_settings.CACHE_DIR / "datasets" / f"{name}.jsonl"

    @classmethod
    def download_dataset(cls, dataset: str, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        Downloads a dataset's `test` split once and keeps it as a local file, so `run` and
        `eval` need no network and every run sees the same rows.

        Args:
            dataset: A dataset name or id.
            force: Download again even if a snapshot exists.

        Returns:
            Optional[Dict[str, Any]]: `{"revision", "rows"}` of the snapshot, or None on failure.
        """
        path = cls.snapshot_path(dataset)
        meta_path = path.with_suffix(".meta.json")
        if path.exists() and meta_path.exists() and not force:
            return json.loads(meta_path.read_text())
        path.parent.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, HF_HOME=str(path.parent / "hf"))
        result = cls.execute([cls.tool("python"), "-c", DATASET_DUMP,
                              swe_bench_settings.SweBenchSettings.dataset_id(dataset)], env=environment)
        lines = result.stdout.splitlines()
        if result.returncode != 0 or len(lines) < 2:
            print(f"❌ Could not download {dataset}: {result.stderr.strip()[-400:]}")
            return None
        meta = json.loads(lines[0])
        meta["dataset"] = swe_bench_settings.SweBenchSettings.dataset_id(dataset)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text("\n".join(lines[1:]) + "\n")
        os.replace(staging, path)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        return meta

    @classmethod
    def dataset_meta(cls, dataset: str) -> Dict[str, Any]:
        """
        Args:
            dataset: A dataset name or id.

        Returns:
            Dict[str, Any]: The snapshot's `{"revision", "rows", "dataset"}`; empty if absent.
        """
        try:
            return json.loads(cls.snapshot_path(dataset).with_suffix(".meta.json").read_text())
        except (OSError, ValueError):
            return {}

    @classmethod
    def rows(cls, dataset: str) -> List[Dict[str, Any]]:
        """
        Reads the local snapshot.

        Args:
            dataset: A dataset name or id.

        Returns:
            List[Dict[str, Any]]: Its rows, sorted by instance id; empty when not downloaded.
        """
        try:
            text = cls.snapshot_path(dataset).read_text()
        except OSError:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    @classmethod
    def write_dataset_file(cls, path: Path, rows: Iterable[Dict[str, Any]],
                           images: Dict[str, str]) -> List[str]:
        """
        Writes the dataset file one harness call grades against: the given rows, each with its
        `image` replaced by the image this machine runs.

        Args:
            path: Where to write it (`.jsonl`).
            rows: Dataset rows.
            images: Instance id to image reference; rows without an entry are left out.

        Returns:
            List[str]: The instance ids written.
        """
        written = []
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as handle:
            for row in rows:
                image = images.get(row["instance_id"])
                if not image:
                    continue
                handle.write(json.dumps(dict(row, image=image)) + "\n")
                written.append(row["instance_id"])
        return written

    # -- grading -------------------------------------------------------------------------------

    @classmethod
    def evaluate(cls, work_dir: Path, dataset_file: Path, run_id: str, instance_ids: List[str],
                 predictions: Optional[Path], workers: int, timeout_s: int,
                 memory: Optional[str] = None) -> bool:
        """
        Calls `swebench eval` in `work_dir`, where it leaves `logs/run_evaluation/<run_id>/`.

        Args:
            work_dir: The directory the harness runs in.
            dataset_file: The dataset file to grade against.
            run_id: The harness's run id; it caches results by this and the instance id.
            instance_ids: The instances to grade in this call.
            predictions: The predictions file, or None to grade the reference patches.
            workers: Instances graded at once.
            timeout_s: Seconds per instance.
            memory: A Docker memory limit put on each grading container as it appears (the
                harness sets none); None leaves them uncapped.

        Returns:
            bool: True when the harness ran to the end (whatever it found).
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        command = [cls.tool("swebench"), "eval", str(dataset_file), "--run-id", run_id,
                   "-j", str(workers), "-t", str(timeout_s), "--report-dir", str(work_dir)]
        command += ["--gold"] if predictions is None else ["-p", str(predictions)]
        for instance_id in instance_ids:
            command += ["-i", instance_id]
        stop = threading.Event()
        capper = None
        if memory:
            capper = threading.Thread(target=cls._cap_containers, args=(run_id, memory, stop), daemon=True)
            capper.start()
        try:
            result = cls.execute(command, cwd=str(work_dir),
                                 env=dict(os.environ, HF_HOME=str(swe_bench_settings.CACHE_DIR / "datasets" / "hf")))
        finally:
            stop.set()
            if capper:
                capper.join(timeout=10)
        (work_dir / f"harness-{run_id}.log").write_text((result.stdout or "") + (result.stderr or ""))
        if result.returncode != 0:
            print(f"⚠️  swebench eval exited {result.returncode}; see {work_dir / f'harness-{run_id}.log'}")
        return result.returncode == 0

    @classmethod
    def _cap_containers(cls, run_id: str, memory: str, stop: threading.Event) -> None:
        """
        Puts a memory limit on the harness's containers, which it creates with none. They belong
        to dockerd's cgroup, so a systemd scope around the harness would not reach them.
        """
        capped: set = set()
        while not stop.wait(1.0):
            listed = SweBenchDocker.run(["ps", "--filter", "name=sweb.eval.", "--format", "{{.Names}}"], timeout=20)
            for name in listed.stdout.split():
                if name.endswith(f".{run_id}") and name not in capped:
                    SweBenchDocker.run(["update", "--memory", memory, "--memory-swap", memory, name], timeout=20)
                    capped.add(name)

    @classmethod
    def instance_report(cls, work_dir: Path, run_id: str, model_name: str,
                        instance_id: str) -> Optional[Dict[str, Any]]:
        """
        Reads one instance's verdict from the harness's logs.

        Args:
            work_dir: The directory the harness ran in.
            run_id: The harness's run id.
            model_name: The predictions' `model_name_or_path` (`gold` for reference patches).
            instance_id: The instance.

        Returns:
            Optional[Dict[str, Any]]: The harness's report for the instance (`resolved`,
            `patch_successfully_applied`, ...), or None when it wrote none: the patch was empty,
            or the container failed before the tests ran.
        """
        path = (work_dir / HARNESS_LOG_DIR / run_id / model_name.replace("/", "__") / instance_id
                / "report.json")
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return report.get(instance_id) if isinstance(report, dict) else None

    @classmethod
    def test_patch_failed(cls, work_dir: Path, run_id: str, model_name: str, instance_id: str) -> bool:
        """
        Tells whether the instance's test patch failed to apply on top of the model's, the case
        §5.1 keeps visible instead of stripping test files from the model's patch.

        Returns:
            bool: True when the test output shows `git apply` rejecting the test patch.
        """
        path = (work_dir / HARNESS_LOG_DIR / run_id / model_name.replace("/", "__") / instance_id
                / "test_output.txt")
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return False
        return "error: patch failed" in text or "patch does not apply" in text
