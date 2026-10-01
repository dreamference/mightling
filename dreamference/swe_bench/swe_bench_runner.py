"""
`puffin-admin swe-bench run`: the agent phase (specs/DREAMFERENCE_PUFFIN_SWE_BENCH.md §5, §12).

One `puffin exec` per instance, in the instance's own container, several at once when the model
server's KV pool and the host's memory admit it. The run measures whatever model is being served
and never starts, stops or loads it. Admission, the runner lock and the start checks are Night
Shift's, imported, not copied: a benchmark run and a night run exclude each other.
"""

import hashlib
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Final, List, Optional
from urllib.parse import urlparse

from dreamference.night_shift.night_shift_host import NightShiftHost
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_runner import NightShiftRunner
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_instance_run import SweBenchInstanceRun
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime

POLL_S: Final[float] = 5.0

# What the lock file says while a benchmark run holds Night Shift's runner lock.
LOCK_HOLDER: Final[str] = "a SWE-bench run"


class SweBenchRunner:
    """One agent-phase run."""

    # Seams the tests replace: the clock's sleep, the host probes, and Night Shift's checks.
    sleep = staticmethod(time.sleep)
    host = NightShiftHost
    admission = NightShiftRunner

    @classmethod
    def smoke_path(cls) -> Path:
        """
        Returns:
            Path: The record of the last passed smoke on this machine.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return swe_bench_settings.CACHE_DIR / f"smoke-{architecture}.json"

    @classmethod
    def smoke_passed(cls) -> bool:
        """
        Returns:
            bool: True when a smoke passed here with the harness version now pinned.
        """
        import json
        try:
            record = json.loads(cls.smoke_path().read_text())
        except (OSError, ValueError):
            return False
        return record.get("harness") == swe_bench_settings.HARNESS_VERSION and bool(record.get("passed"))

    # -- selection -----------------------------------------------------------------------------

    @classmethod
    def select(cls, dataset: str, instances: Optional[List[str]], limit: Optional[int],
               subset: Optional[str]) -> List[str]:
        """
        Chooses the instances a new run covers, in a fixed order (sorted by instance id), so two
        runs with the same arguments cover the same instances.

        Args:
            dataset: The dataset.
            instances: Explicit instance ids, if given.
            limit: Keep only the first N.
            subset: A file of instance ids, one per line, if given.

        Returns:
            List[str]: The selected instance ids.

        Raises:
            ValueError: When an id is not in the dataset.
        """
        known = [row["instance_id"] for row in SweBenchHarness.rows(dataset)]
        wanted: Optional[List[str]] = None
        if subset:
            wanted = [line.strip() for line in Path(subset).read_text().splitlines()
                      if line.strip() and not line.startswith("#")]
        if instances:
            wanted = (wanted or []) + list(instances)
        if wanted is None:
            selected = list(known)
        else:
            unknown = sorted(set(wanted) - set(known))
            if unknown:
                raise ValueError(f"not in {dataset}: {', '.join(unknown[:5])}")
            selected = sorted(set(wanted))
        return selected[:limit] if limit else selected

    # -- the manifest --------------------------------------------------------------------------

    @classmethod
    def model_name(cls, served_model: str) -> str:
        """
        Builds the predictions' `model_name_or_path`, so a result stays attributable to the build
        and the model that produced it.

        Args:
            served_model: The served model's id.

        Returns:
            str: `puffin-<codex tag>-<patch series hash, 8 hex>/<served model id>`.
        """
        from dreamference.runner.codex_branded_builder import CODEX_RELEASE_TAG, CodexBrandedBuilder
        digest = hashlib.sha256()
        for patch in CodexBrandedBuilder.patches():
            with open(patch, "rb") as handle:
                digest.update(handle.read())
        tag = CODEX_RELEASE_TAG.removeprefix("rust-v")
        return f"puffin-{tag}-{digest.hexdigest()[:8]}/{served_model}"

    @classmethod
    def build_manifest(cls, name: str, dataset: str, selected: List[str], excluded: Dict[str, str],
                       settings: "swe_bench_settings.SweBenchSettings", served: tuple,
                       runtime_hash: str, puffin_bin: str, parallel: int) -> Dict[str, Any]:
        """
        Collects what a run measured (§6.4). Written once, when the run starts.

        Returns:
            Dict[str, Any]: The manifest.
        """
        from dreamference.config import DreamferenceConfig
        from dreamference.hardware import model_key_for_served_id
        from dreamference.runner.codex_branded_builder import CODEX_RELEASE_TAG, REPO_ROOT
        config = DreamferenceConfig()
        validated = SweBenchImages.validated()
        meta = SweBenchHarness.dataset_meta(dataset)
        served_id, context = served
        try:
            alias = model_key_for_served_id(served_id, config.model)
        except Exception:
            alias = None
        return {
            "name": name,
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dataset": swe_bench_settings.SweBenchSettings.dataset_id(dataset),
            "dataset_revision": meta.get("revision"),
            "dataset_rows": meta.get("rows"),
            "harness": SweBenchHarness.installed_version(),
            "architecture": swe_bench_settings.SweBenchSettings.architecture(),
            "image_source": f"community ({swe_bench_settings.COMMUNITY_IMAGE_REPO})",
            "images": {i: {"image": validated[i]["image"], "digest": validated[i].get("digest")}
                       for i in selected if i in validated},
            "instances": [i for i in selected if i not in excluded],
            "excluded": excluded,
            "model_name_or_path": cls.model_name(served_id),
            "served_model": served_id,
            "served_context": context,
            "model_alias": alias,
            "puffin_version": cls._output([puffin_bin, "--version"]),
            "codex_tag": CODEX_RELEASE_TAG,
            "runtime_hash": runtime_hash,
            "cave_mode": config.puffin_cave_mode,
            "airgapped": "off (the container has no network; see the spec's §12)",
            "task_context": settings.task_context,
            "task_timeout_s": settings.task_timeout_s,
            "task_memory": settings.task_memory,
            "nudges": settings.nudges,
            "parallelism": parallel,
            "repository_commit": cls._output(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"]),
        }

    @classmethod
    def _output(cls, command: List[str]) -> Optional[str]:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                    stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    # -- the run -------------------------------------------------------------------------------

    @classmethod
    def run(cls, dataset: str = "verified", instances: Optional[List[str]] = None,
            limit: Optional[int] = None, subset: Optional[str] = None, name: Optional[str] = None,
            evaluate: bool = False, until: Optional[str] = None, idle_minutes: Optional[float] = None,
            ignore_sessions: bool = False, keep_images: bool = True, require_smoke: bool = True,
            settings: Optional["swe_bench_settings.SweBenchSettings"] = None) -> int:
        """
        Runs the agent over a run's instances, resuming a run of the same name.

        Args:
            dataset: The dataset of a new run.
            instances: Explicit instance ids of a new run.
            limit: Keep the first N selected instances of a new run.
            subset: A file of instance ids of a new run.
            name: The run's name; a new run without one is named after the date.
            evaluate: Grade the predictions when the agent phase ends.
            until: `HH:MM` after which no new instance starts; running ones finish.
            idle_minutes: Minutes the model must have been idle first; defaults to the setting.
            ignore_sessions: Do not wait for open `puffin` sessions (for testing beside one).
            keep_images: With `evaluate`, False works one repository at a time and removes its
                images once it is graded, to make room for the next repository's.
            require_smoke: Refuse to run unless a smoke has passed on this machine.
            settings: Benchmark settings; defaults to the config file's.

        Returns:
            int: 0 when the run did what it could (whatever its instances did), 1 when it could
            not run.
        """
        settings = settings or swe_bench_settings.SweBenchSettings()
        if require_smoke and not cls.smoke_passed():
            print("❌ No smoke has passed on this machine with this harness version: "
                  "run `puffin-admin swe-bench smoke` first.")
            return 1
        if not SweBenchHarness.rows(dataset):
            print("❌ The dataset is not downloaded: run `puffin-admin swe-bench setup` first.")
            return 1
        puffin_bin = SweBenchRuntime.installed_puffin()
        if not puffin_bin:
            print("❌ puffin is not built: run `puffin-admin codex build` first.")
            return 1
        from dreamference.config import DreamferenceConfig
        vllm_host = DreamferenceConfig().vllm_host

        store = SweBenchRunStore(name or cls.default_name())
        end = cls.until_time(until)
        with NightShiftQueue.runner_lock(NightShiftQueue.night_dir(), holder=LOCK_HOLDER) as held:
            if not held:
                print(f"⚠️  {NightShiftQueue.runner_holder() or 'Another run'} holds the runner lock.")
                return 1
            cls.admission.ignore_sessions = ignore_sessions
            idle = settings.idle_minutes if idle_minutes is None else idle_minutes
            reason = cls.admission.admit(vllm_host, puffin_bin, idle, end or datetime.now().astimezone() + timedelta(days=365))
            reason = reason or SweBenchEvaluator.disk_problem(settings)
            if reason:
                print(f"⚠️  Not run: {reason}.")
                return 1
            served = cls.host.served_model(vllm_host)
            if served is None:
                print(f"⚠️  Not run: the model server at {vllm_host} is not answering.")
                return 1
            metrics = cls.host.metrics(vllm_host) or {}
            parallel = cls.host.parallelism(settings.max_parallel, metrics.get("kv_pool", 0.0), settings.task_context)
            runtime_hash = SweBenchRuntime.ensure(puffin_bin, SweBenchHarness.tool("patchelf"))
            if runtime_hash is None:
                return 1

            manifest = store.manifest()
            if manifest is None:
                try:
                    selected = cls.select(dataset, instances, limit, subset)
                except (ValueError, OSError) as error:
                    print(f"❌ {error}")
                    return 1
                print(f"🔎 Checking that {len(selected)} instance(s) grade correctly here "
                      "(reference patch resolves, no-op patch does not)...")
                problems = SweBenchEvaluator.validate(dataset, selected, settings)
                excluded = {i: problem for i, problem in problems.items() if problem}
                manifest = cls.build_manifest(store.name, dataset, selected, excluded, settings,
                                              served, runtime_hash, puffin_bin, parallel)
                store.write_manifest(manifest)
            elif manifest.get("runtime_hash") != runtime_hash or manifest.get("served_model") != served[0]:
                print(f"❌ Run {store.name} was started with another puffin build or model "
                      f"({manifest.get('served_model')}); a run measures one configuration. Use a new --name.")
                return 1

            gateway = SweBenchDocker.ensure_network(swe_bench_settings.NETWORK_NAME)
            if gateway is None:
                print(f"❌ Could not create the internal Docker network {swe_bench_settings.NETWORK_NAME}.")
                return 1
            model_url = f"http://{gateway}:{urlparse(vllm_host).port or 8000}"
            extra_env = {"DREAMFERENCE_PUFFIN_CAVE_MODE": str(manifest.get("cave_mode") or "ultra"),
                         "DREAMFERENCE_PUFFIN_AIRGAPPED": "off"}

            finished = set(store.finished())
            pending = [i for i in manifest["instances"] if i not in finished]
            print(f"🏁 SWE-bench run {store.name}: {len(pending)} instance(s) to run, "
                  f"{len(finished)} done, {len(manifest.get('excluded', {}))} excluded; "
                  f"up to {parallel} at once on {served[0]}.")
            rows = {row["instance_id"]: row for row in SweBenchHarness.rows(manifest["dataset"])}
            interrupted = False
            previous = signal.getsignal(signal.SIGTERM)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, cls._raise_interrupt)
            try:
                # One repository at a time only when its images are to be removed after grading;
                # otherwise everything is one group, so small repositories do not run alone.
                cycling = evaluate and not keep_images
                groups = cls.by_repository(pending, rows) if cycling else {None: pending}
                for repo, group in groups.items():
                    stopped = cls.schedule(store, group, rows, manifest, settings, runtime_hash,
                                           model_url, vllm_host, puffin_bin, parallel, end, extra_env)
                    if evaluate:
                        graded_now = [i for i in manifest["instances"] if repo is None or rows[i]["repo"] == repo]
                        SweBenchEvaluator.grade(store, settings, only=graded_now)
                        if cycling and not stopped:
                            SweBenchImages.remove([manifest["images"][i]["image"] for i in graded_now
                                                   if i in manifest["images"]])
                    if stopped:
                        print(f"⏸️  Stopped: {stopped}. Run the same command again to resume.")
                        break
            except KeyboardInterrupt:
                interrupted = True
                print("\n⏸️  Interrupted; the instances that were running will run again on resume.")
            finally:
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, previous)
            done = len(store.finished())
            print(f"✅ {done} of {len(manifest['instances'])} instance(s) have a prediction: "
                  f"{store.predictions_path}")
            return 130 if interrupted else 0

    @classmethod
    def _raise_interrupt(cls, *_: Any) -> None:
        raise KeyboardInterrupt

    @classmethod
    def schedule(cls, store: SweBenchRunStore, pending: List[str], rows: Dict[str, Dict[str, Any]],
                 manifest: Dict[str, Any], settings: "swe_bench_settings.SweBenchSettings",
                 runtime_hash: str, model_url: str, vllm_host: str, puffin_bin: str, parallel: int,
                 end: Optional[datetime], extra_env: Dict[str, str]) -> Optional[str]:
        """
        Starts instances while the machine is quiet, memory and disk admit one more and `--until`
        has not passed; waits for the running ones.

        Returns:
            Optional[str]: Why the run stopped before finishing `pending`; None when it finished.
        """
        queue = list(pending)
        active: List[tuple] = []
        waiting_for: Optional[str] = None
        stopped: Optional[str] = None
        try:
            while queue or active:
                active = [(thread, run) for thread, run in active if thread.is_alive()]
                if queue and stopped is None:
                    if end is not None and datetime.now().astimezone() >= end:
                        stopped = f"it is past {end:%H:%M} (--until)"
                    else:
                        stopped = SweBenchEvaluator.disk_problem(settings)
                    if stopped:
                        queue.clear()
                        continue
                if queue and len(active) < parallel:
                    reason = cls.admission.start_blocker(vllm_host, puffin_bin, active, settings)
                    if reason is None:
                        instance_id = queue.pop(0)
                        run = SweBenchInstanceRun(
                            store, rows[instance_id], manifest["images"][instance_id]["image"],
                            manifest["model_name_or_path"], settings, SweBenchRuntime.directory(),
                            model_url, time.time() + settings.task_timeout_s, extra_env)
                        thread = threading.Thread(target=cls._run_one, args=(run,),
                                                  name=f"swe-{instance_id}", daemon=True)
                        thread.start()
                        active.append((thread, run))
                        waiting_for = None
                        continue
                    # Said once per kind of reason: the free-memory figure in it moves every poll.
                    kind = re.sub(r"[\d.]+", "#", reason)
                    if kind != waiting_for:
                        print(f"⏳ {datetime.now():%H:%M} waiting to start the next instance: "
                              f"{reason.replace('the night run', 'this run')}.", flush=True)
                        waiting_for = kind
                cls.sleep(POLL_S)
        except KeyboardInterrupt:
            for _, run in active:
                run.stop_event.set()
            for thread, _ in active:
                thread.join()
            raise
        return stopped

    @classmethod
    def _run_one(cls, run: SweBenchInstanceRun) -> None:
        if SweBenchDocker.ensure_image(run.image) is None:
            run.store.write_state(run.instance_id, {"instance_id": run.instance_id, "status": "error",
                                                    "notes": ["the image could not be pulled"]})
            run.store.append_prediction(run.instance_id, run.model_name, "")
            print(f"   {run.instance_id}: error (the image could not be pulled)")
            return
        # The clock starts once the image is here: a pull is not the agent's time.
        run.started = time.time()
        run.deadline = run.started + run.settings.task_timeout_s
        status = run.run()
        print(f"   {run.instance_id}: {status} ({int(time.time() - run.started)} s)", flush=True)

    @classmethod
    def by_repository(cls, instance_ids: List[str], rows: Dict[str, Dict[str, Any]]) -> Dict[Any, List[str]]:
        """
        Groups instances by repository, keeping their order: with `--eval --remove-images` a
        repository's instances are run and graded together, so its images can go before the next.

        Returns:
            Dict[str, List[str]]: Repository to its instances.
        """
        groups: Dict[str, List[str]] = {}
        for instance_id in instance_ids:
            groups.setdefault(rows[instance_id]["repo"], []).append(instance_id)
        return groups

    @classmethod
    def default_name(cls) -> str:
        """
        Returns:
            str: `<date>-a`, or the next free letter.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        existing = set(SweBenchRunStore.runs())
        for letter in "abcdefghijklmnopqrstuvwxyz":
            if f"{today}-{letter}" not in existing:
                return f"{today}-{letter}"
        return f"{today}-{int(time.time())}"

    @classmethod
    def until_time(cls, until: Optional[str]) -> Optional[datetime]:
        """
        Args:
            until: `HH:MM`, or None.

        Returns:
            Optional[datetime]: The next such time, or None.
        """
        if not until:
            return None
        hour, minute = (int(part) for part in until.split(":"))
        now = datetime.now().astimezone()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)
