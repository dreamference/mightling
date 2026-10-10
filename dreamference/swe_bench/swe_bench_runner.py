"""
`ling-admin swe-bench run`: the agent phase (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §5, §12).

One `ling exec` per instance, in the instance's own container, several at once when the model
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
from dreamference.swe_bench.swe_bench_code_index import ARMS, SweBenchCodeIndex
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_gate_hold import SweBenchGateHold
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_hooks import HOOK_SETS, SweBenchHooks
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_instance_run import SCRATCH_MOUNT, TASK_RULES, SweBenchInstanceRun
from dreamference.swe_bench.swe_bench_name_stripper import SweBenchNameStripper
from dreamference.swe_bench.swe_bench_relay import SweBenchRelay
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime
from dreamference.vllm_server.model_gate import ModelGate

POLL_S: Final[float] = 5.0

# What the lock file says while a benchmark run holds Night Shift's runner lock.
LOCK_HOLDER: Final[str] = "a SWE-bench run"

# The prompts compiled into `ling` (ling-rs/src/prompt.rs); any other name is a file in
# `$CODEX_HOME/system-prompts/`. A run's manifest without a prompt ran the default.
BUILT_IN_PROMPTS: Final[tuple] = ("default", "offline", "high-swe", "ask")
# The apply_patch arm (spec §22): the launcher's override variable (ling-rs/src/lib.rs,
# `APPLY_PATCH_ENV`), and the manifest value for a run that left the launcher's choice alone.
APPLY_PATCH_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_APPLY_PATCH"
AUTO_APPLY_PATCH: Final[str] = "auto"
DEFAULT_RUN_PROMPT: Final[str] = "default"
PROMPT_DIR: Final[str] = "system-prompts"

# What a new run's name may hold. Grading passes `<name>-<n>` to the harness as its run id, which
# names Docker containers (`sweb.eval.<instance>.<run id>`), so a `+` or a `,` would let the agent
# phase run all night and then fail at grading. An arm with several task rules joins their names
# with `-`, the record arm's first: `n3-tests-v2-issue-v1`.
RUN_NAME: Final[re.Pattern] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class SweBenchRunner:
    """One agent-phase run."""

    # Seams the tests replace: the clock's sleep, the host probes, Night Shift's checks, and the
    # model gate (the run's hold on it, and the probe of a replica's).
    sleep = staticmethod(time.sleep)
    host = NightShiftHost
    admission = NightShiftRunner
    gate_hold = SweBenchGateHold
    gate = ModelGate

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
            str: `mightling-<codex tag>-<patch series hash, 8 hex>/<served model id>`.
        """
        from dreamference.runner.codex_branded_builder import CODEX_RELEASE_TAG, CodexBrandedBuilder
        digest = hashlib.sha256()
        for patch in CodexBrandedBuilder.patches():
            with open(patch, "rb") as handle:
                digest.update(handle.read())
        tag = CODEX_RELEASE_TAG.removeprefix("rust-v")
        return f"mightling-{tag}-{digest.hexdigest()[:8]}/{served_model}"

    @classmethod
    def build_manifest(cls, name: str, dataset: str, selected: List[str], excluded: Dict[str, str],
                       settings: "swe_bench_settings.SweBenchSettings", served: tuple,
                       runtime_hash: str, mightling_bin: str, parallel: int,
                       code_index: str = "off", prompt: Optional[str] = None,
                       mask: str = "off", strip_names: bool = False, refine: bool = False,
                       task_rules: Optional[List[str]] = None, review_turn: bool = False,
                       refine_version: str = "v1",
                       hooks: Optional[List[str]] = None, apply_patch: Optional[str] = None) -> Dict[str, Any]:
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
        stripped: Dict[str, Dict[str, Any]] = {}
        if strip_names:
            # The text each agent will see, fixed when the run starts: a resumed run, a later
            # change to the stripping, or another machine's word list cannot change it.
            rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
            for instance_id in selected:
                if instance_id in excluded or instance_id not in rows:
                    continue
                text, replaced = SweBenchNameStripper.strip(rows[instance_id]["problem_statement"],
                                                            rows[instance_id].get("patch", ""))
                stripped[instance_id] = {"text": text, "replaced": replaced}
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
            "puffin_version": cls._output([mightling_bin, "--version"]),
            "codex_tag": CODEX_RELEASE_TAG,
            "runtime_hash": runtime_hash,
            "cave_mode": config.mightling_cave_mode,
            "prompt": prompt or config.mightling_prompt,
            "prompt_sha256": cls.prompt_digest(prompt or config.mightling_prompt),
            "airgapped": "off (the container has no network; see the spec's §12)",
            "code_index": code_index,
            "masking": mask,
            "issue_text": "names stripped" if strip_names else "verbatim",
            **({"stripped_issues": stripped} if strip_names else {}),
            "refine": refine,
            "refine_version": refine_version,
            "task_rules": sorted(set(task_rules or [])),
            "review_turn": review_turn,
            "hooks": sorted(set(hooks or [])),
            "apply_patch": apply_patch or AUTO_APPLY_PATCH,
            **({"hooks_gate_sha256": SweBenchHooks.gate_digest()} if hooks else {}),
            "task_context": settings.task_context,
            "task_timeout_s": settings.task_timeout_s,
            "task_memory": settings.task_memory,
            "nudges": settings.nudges,
            "parallelism": parallel,
            "repository_commit": cls._output(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"]),
        }

    @classmethod
    def prompt_file(cls, name: str) -> Optional[Path]:
        """
        The file a custom prompt is read from: `$CODEX_HOME/system-prompts/<name>.md`. The built-in
        prompts are in the binary and have none.

        Args:
            name: The prompt's name.

        Returns:
            Optional[Path]: The file, or None for a built-in prompt.
        """
        if name in BUILT_IN_PROMPTS:
            return None
        from dreamference.runner.codex_installer import CodexInstaller
        return Path(CodexInstaller.home_dir()) / PROMPT_DIR / f"{name}.md"

    @classmethod
    def prompt_digest(cls, name: str) -> Optional[str]:
        """
        The SHA-256 of a custom prompt's file, so the manifest names the text a run measured; None
        for a built-in prompt, which `runtime_hash` already covers.

        Args:
            name: The prompt's name.

        Returns:
            Optional[str]: The hex digest, or None.
        """
        path = cls.prompt_file(name)
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        except OSError:
            return None

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
            code_index: str = "off", prompt: Optional[str] = None, mask: str = "off",
            strip_names: bool = False,
            refine: bool = False,
            task_rules: Optional[List[str]] = None,
            settings: Optional["swe_bench_settings.SweBenchSettings"] = None,
            label: Optional[str] = None,
            review_turn: bool = False,
            refine_version: str = "v1",
            hooks: Optional[List[str]] = None,
            apply_patch: Optional[str] = None) -> int:
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
            ignore_sessions: Do not wait for open `ling` sessions (for testing beside one).
            keep_images: With `evaluate`, False works one repository at a time and removes its
                images once it is graded, to make room for the next repository's.
            require_smoke: Refuse to run unless a smoke has passed on this machine.
            code_index: `off`, `universal` to index each instance's repository on the host
                and give the agent `ling-code`, or `exact` for the same with the SCIP stores
                alone (a new run only; a resumed run keeps its arm).
            prompt: The system prompt the agent starts with (prompt spec §6.2); None takes the
                configured one. A new run only, like `code_index`.
            mask: `on` masks old tool outputs in the agent's requests (context budget spec
                §4.1), `off` does not. A new run only, like `code_index`.
            strip_names: Take the names the gold patch touches out of each issue's text
                (`SweBenchNameStripper`). A new run only, like `code_index`.
            refine: Run each instance in two steps: a session that studies the issue and writes
                a refined description without changing the repository, then a fresh session
                that fixes it with the issue and the description. A new run only, like
                `code_index`.
            task_rules: Names of the rules added to the task prompt (`TASK_RULES`: `tests`, the
                failure analysis's test discipline, `tests-v2`, which lets the issue decide
                whether a failing old test or the change is wrong, and `issue-v1`, which has the
                agent work out what the issue asks for and follow the sibling code's pattern).
                They stack, in `TASK_RULES`' order. A new run only, like `code_index`.
            settings: Benchmark settings; defaults to the config file's.
            label: What the model gate's refusal calls this run (e.g. `night 1`); defaults to
                `SWE-bench run <name>`.
            review_turn: Resume each agent's session once more after it stops with a changed
                tree, to review and test its diff before the patch is collected (spec §19).
                A new run only, like `code_index`.
            refine_version: Which refine texts `refine` uses: `v1`, the measured ones, or `v2`
                (refine spec §10). A new run only, like `code_index`.
            apply_patch: How the agent is offered Codex's `apply_patch` tool, set for every session
                through `DREAMFERENCE_MIGHTLING_APPLY_PATCH`: `function`, `freeform` or `off`; None
                leaves the launcher's choice (spec §22). A new run only, like `code_index`.
            hooks: Hook sets registered in each instance's session (`HOOK_SETS`: `issue-v1`
                holds the first edit once until the files and functions the issue names have
                been read, and the first stop once until its example has been run; spec §20).
                Independent of `task_rules`. A new run only, like `code_index`.

        Returns:
            int: 0 when the run did what it could (whatever its instances did), 1 when it could
            not run.
        """
        settings = settings or swe_bench_settings.SweBenchSettings()
        if code_index not in ARMS:
            print(f"❌ --code-index is one of: {', '.join(ARMS)}.")
            return 1
        if prompt is not None:
            from dreamference.config import DreamferenceConfig
            if DreamferenceConfig.parse_prompt_name(prompt) is None:
                print(f"❌ --prompt {prompt!r} is not a prompt's name (lowercase letters, digits, hyphens).")
                return 1
            custom = cls.prompt_file(prompt)
            if custom is not None and not custom.is_file():
                print(f"❌ No prompt named {prompt}: it is not built in ({', '.join(BUILT_IN_PROMPTS)}) "
                      f"and {custom} does not exist.")
                return 1
        unknown_rules = sorted(set(task_rules or []) - set(TASK_RULES))
        if unknown_rules:
            print(f"❌ --task-rules takes: {', '.join(TASK_RULES)} (not {', '.join(unknown_rules)}).")
            return 1
        unknown_hooks = sorted(set(hooks or []) - set(HOOK_SETS))
        if unknown_hooks:
            print(f"❌ --hooks takes: {', '.join(HOOK_SETS)} (not {', '.join(unknown_hooks)}).")
            return 1
        if name is not None and not RUN_NAME.fullmatch(name) and SweBenchRunStore(name).manifest() is None:
            print(f"❌ --name {name!r}: a run's name is letters, digits, '.', '_' and '-', because the grader "
                  "names Docker containers after it (join task rules with '-': n3-tests-v2-issue-v1).")
            return 1
        if require_smoke and not cls.smoke_passed():
            print("❌ No smoke has passed on this machine with this harness version: "
                  "run `ling-admin swe-bench smoke` first.")
            return 1
        if not SweBenchHarness.rows(dataset):
            print("❌ The dataset is not downloaded: run `ling-admin swe-bench setup` first.")
            return 1
        mightling_bin = SweBenchRuntime.installed_mightling()
        if not mightling_bin:
            print("❌ ling is not built: run `ling-admin codex build` first.")
            return 1
        from dreamference.config import DreamferenceConfig
        vllm_host = DreamferenceConfig().vllm_host

        store = SweBenchRunStore(name or cls.default_name())
        end = cls.until_time(until)
        with NightShiftQueue.runner_lock(NightShiftQueue.night_dir(), holder=LOCK_HOLDER) as held:
            if not held:
                print(f"⚠️  {NightShiftQueue.runner_holder() or 'Another run'} holds the runner lock.")
                return 1
            gateway = SweBenchDocker.ensure_network(swe_bench_settings.NETWORK_NAME)
            if gateway is None:
                print(f"❌ Could not create the internal Docker network {swe_bench_settings.NETWORK_NAME}.")
                return 1
            # The model gate refuses every request but this run's while the block lasts (§18).
            with cls.gate_hold(store, vllm_host, SweBenchDocker.subnet(swe_bench_settings.NETWORK_NAME),
                               label) as hold:
                idle = settings.idle_minutes if idle_minutes is None else idle_minutes
                if hold.priority():
                    # Nobody else can reach the model now: an open session cannot start a turn, so
                    # the run only waits for requests already in flight to finish.
                    print("🚦 The model gate now refuses every request but this run's, until it ends "
                          "(`ling-admin night pause` lets them through for a while).", flush=True)
                    idle = 0
                elif hold.subnet and not hold.enforced:
                    print("💡 No model gate answers in front of the model server (it comes with the next "
                          "`ling-admin server start`): this run waits for other requests, as before.", flush=True)
                cls.admission.ignore_sessions = ignore_sessions or hold.priority()
                reason = cls.admission.admit(vllm_host, mightling_bin, idle, end or datetime.now().astimezone() + timedelta(days=365))
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
                runtime_hash = SweBenchRuntime.ensure(mightling_bin, SweBenchHarness.tool("patchelf"))
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
                                                  served, runtime_hash, mightling_bin, parallel, code_index,
                                                  prompt, mask, strip_names, refine, task_rules,
                                                  review_turn, refine_version, hooks,
                                                  apply_patch=apply_patch)
                    store.write_manifest(manifest)
                elif manifest.get("runtime_hash") != runtime_hash or manifest.get("served_model") != served[0]:
                    print(f"❌ Run {store.name} was started with another ling build or model "
                          f"({manifest.get('served_model')}); a run measures one configuration. Use a new --name.")
                    return 1
                hold.update(manifest["instances"], parallel)

                model_url = f"http://{gateway}:{urlparse(vllm_host).port or 8000}"
                run_prompt = str(manifest.get("prompt") or DEFAULT_RUN_PROMPT)
                lanes, lane_notes = cls.lanes(vllm_host, served, settings, parallel)
                lanes[0]["model_url"] = model_url
                relays = cls.open_relays(gateway, lanes[1:])
                for note in lane_notes:
                    print(f"   {note}")
                extra_env = {"DREAMFERENCE_MIGHTLING_CAVE_MODE": str(manifest.get("cave_mode") or "ultra"),
                             "DREAMFERENCE_MIGHTLING_AIRGAPPED": "off",
                             "DREAMFERENCE_MIGHTLING_PROMPT": run_prompt,
                             # A run made before masking existed has no key: it ran unmasked.
                             "DREAMFERENCE_MIGHTLING_MASK": str(manifest.get("masking") or "off"),
                             # The runner orchestrates `--refine` itself; the launcher's own refine mode
                             # would turn each step into two sessions, whatever the setting says.
                             "DREAMFERENCE_MIGHTLING_REFINE": "off"}
                # The apply_patch arm (spec §22): the launcher's override, only when the run asks.
                run_apply_patch = str(manifest.get("apply_patch") or AUTO_APPLY_PATCH)
                if run_apply_patch != AUTO_APPLY_PATCH:
                    extra_env[APPLY_PATCH_ENV] = run_apply_patch
                # A custom prompt reaches the container's CODEX_HOME read-only: the agent cannot edit
                # the text a later session of the same instance would start from.
                custom_prompt = cls.prompt_file(run_prompt)
                extra_mounts = [f"{custom_prompt}:{SCRATCH_MOUNT}/codex-home/{PROMPT_DIR}/{run_prompt}.md:ro"] \
                    if custom_prompt is not None else []

                finished = set(store.finished())
                pending = [i for i in manifest["instances"] if i not in finished]
                rows = {row["instance_id"]: row for row in SweBenchHarness.rows(manifest["dataset"])}
                indexes: Dict[str, Dict[str, Any]] = {}
                if manifest.get("code_index", "off") != "off" and pending:
                    # Every index is built before the first agent starts: an index run beside the
                    # agents would compete with them, and its time is not the agent's.
                    code_hash = SweBenchCodeIndex.ensure_runtime(SweBenchHarness.tool("patchelf"))
                    if code_hash is None:
                        return 1
                    # Which `ling-code` answered, beside the manifest, which is never edited: a
                    # resumed run may use another build, so each start appends its own line.
                    with open(store.directory / "ling-code.sha256", "a") as record:
                        record.write(f"{code_hash}  {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
                    arm = manifest["code_index"]
                    layer = "SCIP stores only" if arm == "exact" else "universal layer"
                    print(f"🗂️  Indexing {len(pending)} repositories on the host ({layer})...", flush=True)
                    # With `--eval --remove-images` the images are cycled one repository at a time; an
                    # image this pass pulled only to index goes again at once, and the instance pulls
                    # it back when it starts. Otherwise every image of the run would be on disk before
                    # the first agent starts (about 2.3 GB each), past the disk reserve on a 50-task run.
                    cycling = evaluate and not keep_images
                    for instance_id in pending:
                        image = manifest["images"][instance_id]["image"]
                        pulled = cycling and SweBenchDocker.image_digest(image) is None
                        record = SweBenchCodeIndex.ensure(rows[instance_id], image, arm) \
                            if SweBenchDocker.ensure_image(image) else None
                        if pulled:
                            SweBenchImages.remove([image])
                        if record is None:
                            print(f"❌ {instance_id} has no index, and a run measures one arm: not started.")
                            return 1
                        indexes[instance_id] = dict(
                            SweBenchCodeIndex.container_arguments(rows[instance_id], record, arm), record=record)
                        extra = (f", {len(record.get('stores', []))} store(s), peak {record.get('peak_mb', 0)} MiB"
                                 + (f", not finished: {', '.join(sorted(record['failed']))}" if record.get("failed") else "")) \
                            if arm == "exact" else ""
                        print(f"   {instance_id}: index {'cached' if record['cached'] else 'built'} "
                              f"({record['seconds']:.0f} s{extra})", flush=True)
                print(f"🏁 SWE-bench run {store.name}: {len(pending)} instance(s) to run, "
                      f"{len(finished)} done, {len(manifest.get('excluded', {}))} excluded; "
                      f"up to {parallel} at once on {served[0]}.")
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
                                               model_url, vllm_host, mightling_bin, parallel, end, extra_env,
                                               indexes, extra_mounts, lanes=lanes, hold=hold)
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
                    for relay in relays:
                        relay.close()
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
    def lanes(cls, vllm_host: str, served: Any, settings: "swe_bench_settings.SweBenchSettings",
              parallel: int) -> Any:
        """
        This machine's model server and every paired node serving the same model
        (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3). The containers all run here; a replica only
        answers some of their model requests, each lane holding as many instances as its own KV
        pool allows.

        Args:
            vllm_host: This machine's model server.
            served: What it serves.
            settings: The benchmark's settings (`max_parallel`, `task_context`, `nodes`).
            parallel: This machine's lane's size, already computed.

        Returns:
            Any: `(lanes, notes)`, this machine's lane first.
        """
        from dreamference.node.node_lanes import NodeLanes
        from dreamference.node.node_pairing import NodePairing
        local = {"name": "this machine", "node": None, "host": vllm_host, "parallel": parallel, "budget": None}
        if NodeLanes.wanted(settings.nodes) == [] or not NodePairing.paired():
            return [local], []
        budget = lambda pool: (cls.host.parallelism(settings.max_parallel, pool, settings.task_context), None)
        lanes, skipped = NodeLanes.lanes(vllm_host, served, settings.nodes, cls.host, budget)
        lanes[0]["parallel"] = parallel
        notes = [f"Also using {lane['name']}'s model server ({lane['host']}): up to {lane['parallel']} instance(s) "
                 f"there; the containers run on this machine." for lane in lanes[1:]]
        return lanes, notes + skipped

    @classmethod
    def open_relays(cls, gateway: str, lanes: List[Dict[str, Any]]) -> List[SweBenchRelay]:
        """
        Gives each replica lane an address its containers can reach: a relay on the gateway.

        Args:
            gateway: The benchmark network's gateway.
            lanes: The replica lanes; each gets `model_url`.

        Returns:
            List[SweBenchRelay]: The relays, to close when the run ends.
        """
        relays = []
        for lane in lanes:
            target = urlparse(lane["host"])
            relay = SweBenchRelay(gateway, (target.hostname, target.port or 8000))
            lane["model_url"] = f"http://{gateway}:{relay.start()}"
            relays.append(relay)
        return relays

    @classmethod
    def schedule(cls, store: SweBenchRunStore, pending: List[str], rows: Dict[str, Dict[str, Any]],
                 manifest: Dict[str, Any], settings: "swe_bench_settings.SweBenchSettings",
                 runtime_hash: str, model_url: str, vllm_host: str, mightling_bin: str, parallel: int,
                 end: Optional[datetime], extra_env: Dict[str, str],
                 indexes: Optional[Dict[str, Dict[str, Any]]] = None,
                 extra_mounts: Optional[List[str]] = None,
                 lanes: Optional[List[Dict[str, Any]]] = None,
                 hold: Optional[SweBenchGateHold] = None) -> Optional[str]:
        """
        Starts instances while the machine is quiet, memory and disk admit one more and `--until`
        has not passed; waits for the running ones. With `lanes`, an instance goes to the first
        model server with room and nothing in its way.

        With `hold` in force (§18) this machine's server refuses everyone else, so open sessions
        and other requests no longer hold a start back; during a pause they do again, as before
        the gate. A replica whose own gate a run of its own holds is skipped.

        Returns:
            Optional[str]: Why the run stopped before finishing `pending`; None when it finished.
        """
        lanes = lanes or [{"name": "this machine", "node": None, "host": vllm_host, "parallel": parallel,
                           "model_url": model_url}]
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
                lane_of = NightShiftRunner.lane_of
                free = [lane for lane in lanes
                        if sum(1 for _, run in active if lane_of(run, vllm_host) == lane["host"]) < lane["parallel"]]
                if queue and free:
                    reason, lane = None, None
                    priority = hold is not None and hold.priority()
                    for candidate in free:
                        if candidate.get("node") is None:
                            blocked = cls.admission.start_blocker(candidate["host"], mightling_bin, active, settings,
                                                                  **({"priority": True} if priority else {}))
                        else:
                            remote_gate = cls.gate.probe(candidate["host"]) or {}
                            blocked = "its model gate is closed for a benchmark run of its own" \
                                if remote_gate.get("state") == "closed" else \
                                cls.admission.start_blocker(candidate["host"], mightling_bin, active, settings, local=False)
                        if blocked is None:
                            reason, lane = None, candidate
                            break
                        reason = reason or (blocked if candidate.get("node") is None
                                            else f"{candidate['name']}: {blocked}")
                    if lane is not None:
                        instance_id = queue.pop(0)
                        run = SweBenchInstanceRun(
                            store, rows[instance_id], manifest["images"][instance_id]["image"],
                            manifest["model_name_or_path"], settings, SweBenchRuntime.directory(),
                            lane.get("model_url") or model_url, time.time() + settings.task_timeout_s, extra_env,
                            (indexes or {}).get(instance_id), extra_mounts,
                            issue=(manifest.get("stripped_issues") or {}).get(instance_id),
                            refine=bool(manifest.get("refine", False)),
                            task_rules=manifest.get("task_rules") or [],
                            review_turn=bool(manifest.get("review_turn", False)),
                            # A run made before refine-v2 existed has no key: it ran v1.
                            refine_version=str(manifest.get("refine_version") or "v1"),
                            hooks=manifest.get("hooks") or [],
                            # A run made before the option existed left the launcher's choice alone.
                            apply_patch=str(manifest.get("apply_patch") or AUTO_APPLY_PATCH))
                        run.lane_host = lane["host"]
                        if lane.get("node"):
                            run.notes.append(f"model server: {lane['name']} (a replica of this machine's model)")
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
