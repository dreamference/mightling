"""`ling-admin swe-bench run`: the agent phase (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §5, §12).

One `ling exec` per instance, in the instance's own container, several at once when the model
server's KV pool and the host's memory admit it. The run measures whatever model is being served
and never starts, stops or loads it. Admission, the runner lock and the start checks are Night
Shift's, imported, not copied: a benchmark run and a night run exclude each other.
"""

import hashlib
import re
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Final, Optional
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
from dreamference.swe_bench.swe_bench_image_cache import SweBenchImageCache
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_instance_run import (
    SCRATCH_MOUNT,
    TASK_RULES,
    SweBenchInstanceRun,
)
from dreamference.swe_bench.swe_bench_name_stripper import SweBenchNameStripper
from dreamference.swe_bench.swe_bench_relay import SweBenchRelay
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime
from dreamference.swe_bench.swe_bench_shards import SweBenchShards
from dreamference.vllm_server.model_gate import ModelGate

POLL_S: Final[float] = 5.0

# What the lock file says while a benchmark run holds Night Shift's runner lock.
LOCK_HOLDER: Final[str] = "a SWE-bench run"

# The prompts compiled into `ling` (ling-rs/src/prompt.rs); any other name is a file in
# `$CODEX_HOME/system-prompts/`. A run's manifest without a prompt ran the default.
BUILT_IN_PROMPTS: Final[tuple] = ("default", "offline", "high-swe", "ask", "chat", "chat-work")
# The apply_patch arm (spec §22): the launcher's override variable (ling-rs/src/lib.rs,
# `APPLY_PATCH_ENV`), and the manifest value for a run that left the launcher's choice alone.
APPLY_PATCH_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_APPLY_PATCH"
AUTO_APPLY_PATCH: Final[str] = "auto"
# The Code Mode arm (spec §23): the launcher's switch for how `exec` reaches the model
# (ling-rs/src/exec_tool.rs, `ENV`), and the manifest value for a run that left the launcher's choice alone.
EXEC_TOOL_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_EXEC_TOOL"
AUTO_EXEC_TOOL: Final[str] = "auto"
# The compatibility-mode arm (MIGHTLING_LING_CLAUDE §11): the launcher's switch
# (ling-rs/src/compat.rs), and the manifest value for a run that left it alone.
COMPAT_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_COMPAT"
AUTO_COMPAT: Final[str] = "auto"
# The drop-first compaction arm (compaction spec §13): the launcher's switch (ling-rs/src/compaction.rs,
# `DROP_ENV`), set to this value only when the run asks.
DROP_OLD_OUTPUTS_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS"
# The loop guard's arms (docs/dev/loop-guard.md; ling-rs/src/loop_guard.rs): the detector, the cap on
# each response and the sampling arm, each the launcher's variable. `ling exec` sets none of them by
# default, so a run without the flags sends what it always sent.
LOOP_GUARD_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_LOOP_GUARD"
OUTPUT_BUDGET_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET"
SAMPLING_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_SAMPLING"
DEFAULT_SAMPLING: Final[str] = "default"
SAMPLING_ARMS: Final[tuple[str, ...]] = (DEFAULT_SAMPLING, "nonthinking")
# The smallest cap the gate applies (`model_gate_guard.MIN_BUDGET`).
MIN_OUTPUT_BUDGET: Final[int] = 64
# The guard arms (spec §29): the launcher's switches for the test guard and the finish check
# (ling-rs/src/guard.rs), set to "1" only when the run asks.
TEST_GUARD_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_TEST_GUARD"
FINISH_CHECK_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_FINISH_CHECK"
DEFAULT_RUN_PROMPT: Final[str] = "default"
PROMPT_DIR: Final[str] = "system-prompts"
# The turn-end compaction arm (spec §16, night 10): Codex's own key, pinned on every `ling exec`
# command line of the run, since the launcher leaves `exec` alone whatever its switch says
# (ling-rs/src/compaction.rs, `with_turn_end`); the switch is set beside it for the record.
TURN_END_KEY: Final[str] = "model_post_turn_compact_threshold_percent"
TURN_END_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION"
AUTO_TURN_END: Final[str] = "auto"
# The launcher's rule for the percentage (`turn_end_percent` there): the share of the limit a turn
# must have passed, Codex's headroom off the window, and its cap on any limit.
TURN_END_SHARE_PERCENT: Final[int] = 70
EFFECTIVE_WINDOW_PERCENT: Final[int] = 95
MAX_LIMIT_PERCENT_OF_WINDOW: Final[int] = 90

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
    # A `ling-code` or `ling-docs` index run beside a session is a transient scope: a run waits
    # for it rather than refusing (night 1's second arm refused at 16:40 on 2026-10-10 for a
    # ling-docs index a session had started, and lost five minutes to a restart by hand).
    INDEX_WAIT_S = 30 * 60
    INDEX_POLL_S = 15
    gate_hold = SweBenchGateHold
    gate = ModelGate

    @classmethod
    def smoke_path(cls) -> Path:
        """Returns:
        Path: The record of the last passed smoke on this machine.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return swe_bench_settings.CACHE_DIR / f"smoke-{architecture}.json"

    @classmethod
    def smoke_passed(cls) -> bool:
        """Returns:
        bool: True when a smoke passed here with the harness version now pinned.
        """
        import json

        try:
            record = json.loads(cls.smoke_path().read_text())
        except (OSError, ValueError):
            return False
        return record.get("harness") == swe_bench_settings.HARNESS_VERSION and bool(
            record.get("passed")
        )

    # -- selection -----------------------------------------------------------------------------

    @classmethod
    def select(
        cls,
        dataset: str,
        instances: list[str] | None,
        limit: int | None,
        subset: str | None,
    ) -> list[str]:
        """Chooses the instances a new run covers, in a fixed order (sorted by instance id), so two
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
        wanted: list[str] | None = None
        if subset:
            wanted = [
                line.strip()
                for line in Path(subset).read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
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
        """Builds the predictions' `model_name_or_path`, so a result stays attributable to the build
        and the model that produced it.

        Args:
            served_model: The served model's id.

        Returns:
            str: `mightling-<codex tag>-<patch series hash, 8 hex>/<served model id>`.
        """
        from dreamference.runner.codex_branded_builder import CODEX_RELEASE_TAG, CodexBrandedBuilder

        digest = hashlib.sha256()
        for patch in CodexBrandedBuilder.patches():
            with Path(patch).open("rb") as handle:
                digest.update(handle.read())
        tag = CODEX_RELEASE_TAG.removeprefix("rust-v")
        return f"mightling-{tag}-{digest.hexdigest()[:8]}/{served_model}"

    @classmethod
    def turn_end_setting(cls, value: str | None) -> str | None:
        """Checks a `--turn-end-compaction` value.

        Args:
            value: `on`, `off`, a percentage from 1 to 99, or None (the key is not set).

        Returns:
            Optional[str]: The value as given, or None when it is not one of those.
        """
        if value is None or value in ("on", "off"):
            return value
        return value if value.isdigit() and 1 <= int(value) <= 99 else None

    @classmethod
    def turn_end_percent(cls, setting: str, window: int, limit: int) -> int | None:
        """The value `model_post_turn_compact_threshold_percent` gets for a run's setting.

        Args:
            setting: `on` (the launcher's own rule: a turn that ends past `TURN_END_SHARE_PERCENT`
                of the limit compacts, the share converted to Codex's base, the window less its
                headroom, as `turn_end_percent` in ling-rs/src/compaction.rs does), `off` (0, which
                Codex reads as off), a percentage (of the usable window, as Codex reads it), or
                `auto` (the key is not set: `exec` never compacts at a turn's end).
            window: The served model's context window (the manifest's `served_context`).
            limit: The run's `task_context`, the limit every session compacts at.

        Returns:
            Optional[int]: The percentage, 0 for `off`, None for `auto`.
        """
        if setting == AUTO_TURN_END:
            return None
        if setting == "off":
            return 0
        if setting.isdigit():
            return int(setting)
        usable = max(1, window * EFFECTIVE_WINDOW_PERCENT // 100)
        target = (
            min(limit, window * MAX_LIMIT_PERCENT_OF_WINDOW // 100) * TURN_END_SHARE_PERCENT // 100
        )
        return min(99, max(1, target * 100 // usable))

    @classmethod
    def wait_for_index_runs(cls, limit_s: int | None = None, poll_s: int | None = None) -> bool:
        """Waits for an active index run (a `mightling-index-*` scope: `ling-code` or `ling-docs`
        indexing started by a session) to end, for up to `limit_s`, polling every `poll_s`. Any
        other heavy job, or an index scope still active at the limit, is left to admission, which
        refuses as before. Prints one line when it starts waiting and one when it goes on.

        Args:
            limit_s: How long to wait at most; `INDEX_WAIT_S` by default.
            poll_s: Seconds between polls; `INDEX_POLL_S` by default.

        Returns:
            bool: True when no index run is active afterwards.
        """
        limit = cls.INDEX_WAIT_S if limit_s is None else limit_s
        poll = cls.INDEX_POLL_S if poll_s is None else poll_s
        started = time.monotonic()
        waited = False
        while True:
            jobs = cls.host.heavy_jobs()
            index_runs = [job for job in jobs if job.startswith("an index run is active")]
            if not index_runs or len(index_runs) != len(jobs):
                if waited:
                    print(
                        f"✅ The index run ended after {int(time.monotonic() - started)} s; going on.",
                        flush=True,
                    )
                return not index_runs
            if time.monotonic() - started >= limit:
                print(f"⚠️  Still active after {int(limit // 60)} min: {index_runs[0]}.", flush=True)
                return False
            if not waited:
                print(
                    f"⏳ Waiting for {index_runs[0]} to end (up to {int(limit // 60)} min, "
                    f"checked every {poll} s)...",
                    flush=True,
                )
                waited = True
            cls.sleep(poll)

    @classmethod
    def build_manifest(
        cls,
        name: str,
        dataset: str,
        selected: list[str],
        excluded: dict[str, str],
        settings: "swe_bench_settings.SweBenchSettings",
        served: tuple,
        runtime_hash: str,
        mightling_bin: str,
        parallel: int,
        code_index: str = "off",
        prompt: str | None = None,
        mask: str = "off",
        strip_names: bool = False,
        refine: bool = False,
        task_rules: list[str] | None = None,
        review_turn: bool = False,
        refine_version: str = "v1",
        turn_end_compaction: str | None = None,
        hooks: list[str] | None = None,
        restart_after_compactions: int = 0,
        apply_patch: str | None = None,
        exec_tool: str | None = None,
        drop_old_outputs: bool = False,
        loop_guard: bool = False,
        output_budget: int | None = None,
        sampling: str | None = None,
        compat: str | None = None,
        test_guard: bool = False,
        finish_check: bool = False,
    ) -> dict[str, Any]:
        """Collects what a run measured (§6.4). Written once, when the run starts.

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
        stripped: dict[str, dict[str, Any]] = {}
        if strip_names:
            # The text each agent will see, fixed when the run starts: a resumed run, a later
            # change to the stripping, or another machine's word list cannot change it.
            rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
            for instance_id in selected:
                if instance_id in excluded or instance_id not in rows:
                    continue
                text, replaced = SweBenchNameStripper.strip(
                    rows[instance_id]["problem_statement"], rows[instance_id].get("patch", "")
                )
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
            "images": {
                i: {"image": validated[i]["image"], "digest": validated[i].get("digest")}
                for i in selected
                if i in validated
            },
            "instances": [i for i in selected if i not in excluded],
            "excluded": excluded,
            "model_name_or_path": cls.model_name(served_id),
            "served_model": served_id,
            "served_context": context,
            "model_alias": alias,
            "puffin_version": cls._output([mightling_bin, "--version"]),
            "codex_tag": CODEX_RELEASE_TAG,
            "runtime_hash": runtime_hash,
            "cave_mode": swe_bench_settings.SWE_BENCH_CAVE_MODE,
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
            "restart_after_compactions": int(restart_after_compactions or 0),
            "hooks": sorted(set(hooks or [])),
            "turn_end_compaction": turn_end_compaction or AUTO_TURN_END,
            "apply_patch": apply_patch or AUTO_APPLY_PATCH,
            "exec_tool": exec_tool or AUTO_EXEC_TOOL,
            "compat": compat or AUTO_COMPAT,
            "drop_old_outputs": bool(drop_old_outputs),
            "loop_guard": bool(loop_guard),
            "output_budget": int(output_budget or 0),
            "sampling": sampling or DEFAULT_SAMPLING,
            "test_guard": bool(test_guard),
            "finish_check": bool(finish_check),
            **({"hooks_gate_sha256": SweBenchHooks.gate_digest()} if hooks else {}),
            "task_context": settings.task_context,
            "task_timeout_s": settings.task_timeout_s,
            "task_memory": settings.task_memory,
            "nudges": settings.nudges,
            "parallelism": parallel,
            "repository_commit": cls._output(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"]),
        }

    @classmethod
    def guard_env(cls, manifest: dict[str, Any]) -> dict[str, str]:
        """Gives the launcher's guard switches for a run's sessions (spec §29).

        Each is set only when the run asks, so a run without the arm measures what a user gets;
        a manifest from before the arms has neither key and ran without them.

        Args:
            manifest: The run's manifest.

        Returns:
            dict[str, str]: `DREAMFERENCE_MIGHTLING_TEST_GUARD` and
            `DREAMFERENCE_MIGHTLING_FINISH_CHECK`, each `1` when its arm is on.
        """
        switches = {TEST_GUARD_ENV: "test_guard", FINISH_CHECK_ENV: "finish_check"}
        return {env: "1" for env, key in switches.items() if manifest.get(key)}

    @classmethod
    def prompt_file(cls, name: str) -> Path | None:
        """The file a custom prompt is read from: `$CODEX_HOME/system-prompts/<name>.md`. The built-in
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
    def prompt_digest(cls, name: str) -> str | None:
        """The SHA-256 of a custom prompt's file, so the manifest names the text a run measured; None
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
    def _output(cls, command: list[str]) -> str | None:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    # -- the run -------------------------------------------------------------------------------

    @classmethod
    def run(
        cls,
        dataset: str = "verified",
        instances: list[str] | None = None,
        limit: int | None = None,
        subset: str | None = None,
        name: str | None = None,
        evaluate: bool = False,
        until: str | None = None,
        idle_minutes: float | None = None,
        ignore_sessions: bool = False,
        keep_images: bool = True,
        require_smoke: bool = True,
        code_index: str = "off",
        prompt: str | None = None,
        mask: str = "off",
        strip_names: bool = False,
        refine: bool = False,
        task_rules: list[str] | None = None,
        settings: Optional["swe_bench_settings.SweBenchSettings"] = None,
        label: str | None = None,
        review_turn: bool = False,
        refine_version: str = "v1",
        hooks: list[str] | None = None,
        turn_end_compaction: str | None = None,
        restart_after_compactions: int = 0,
        apply_patch: str | None = None,
        exec_tool: str | None = None,
        compat: str | None = None,
        drop_old_outputs: bool = False,
        loop_guard: bool = False,
        output_budget: int | None = None,
        sampling: str | None = None,
        shard: str | None = None,
        image_chunk: int | None = None,
        test_guard: bool = False,
        finish_check: bool = False,
    ) -> int:
        """Runs the agent over a run's instances, resuming a run of the same name.

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
            keep_images: With `evaluate`, False works one repository at a time, in chunks of at
                most `image_chunk` instances, and removes a chunk's images once it is graded, to
                make room for the next chunk's.
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
            restart_after_compactions: Stop the fix session once its N-th compaction is recorded
                and start one fresh session with the issue and the stopped session's diff as a
                file (spec §21); 0, the default, never restarts. A new run only, like `code_index`.
            apply_patch: How the agent is offered Codex's `apply_patch` tool, set for every session
                through `DREAMFERENCE_MIGHTLING_APPLY_PATCH`: `function`, `freeform` or `off`; None
                leaves the launcher's choice (spec §22). A new run only, like `code_index`.
            exec_tool: How the agent is offered Code Mode's `exec` tool, set for every session
                through `DREAMFERENCE_MIGHTLING_EXEC_TOOL`: `function` (translated) or `freeform`
                (Codex's custom tool, which SGLang drops); None leaves the launcher's choice,
                `function` on SGLang and vLLM (spec §23). A new run only, like `code_index`.
            compat: The compatibility mode for every session, through
                `DREAMFERENCE_MIGHTLING_COMPAT`: `claude` or `off`; None leaves the launcher's
                own setting, which a container's fresh home leaves off (MIGHTLING_LING_CLAUDE §11).
                A new run only, like `code_index`.
            drop_old_outputs: Drop-first compaction in every session, through
                `DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS`: when a session is about to compact, its
                older tool outputs are dropped first and the summary runs only if that is not
                enough (compaction spec §13). Off by default. A new run only, like `code_index`.
            loop_guard: The launcher's loop detector in every session (`DREAMFERENCE_MIGHTLING_LOOP_GUARD=on`):
                a response that repeats itself is cut where the loop began, and the model is told once to
                stop and answer. Off by default. A new run only, like `code_index`.
            output_budget: A cap on every response, in tokens (`DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET`), set
                as `max_output_tokens` by the model gate; None or 0 is no cap. A new run only.
            sampling: `nonthinking` has the gate fill in the model card's non-thinking sampling
                (`DREAMFERENCE_MIGHTLING_SAMPLING`); None or `default` leaves the server's. A new run only.
            test_guard: The launcher's test guard in every session, through
                `DREAMFERENCE_MIGHTLING_TEST_GUARD`: a change that weakens an existing test is
                pointed out, and the first stop while it stands is held once (spec §29). Off by
                default. A new run only, like `code_index`.
            finish_check: The launcher's finish check in every session, through
                `DREAMFERENCE_MIGHTLING_FINISH_CHECK`: the first stop after a source change that no
                test has run on is held once, as are a stop mid-work and a failure called
                pre-existing with no run on the unchanged code (spec §29). Off by default. A new
                run only, like `code_index`.
            hooks: Hook sets registered in each instance's session (`HOOK_SETS`: `issue-v1`
                holds the first edit once until the files and functions the issue names have
                been read, and the first stop once until its example has been run; spec §20).
                Independent of `task_rules`. A new run only, like `code_index`.
            turn_end_compaction: Compaction at the end of a turn in every session of the run (spec
                §16, night 10): `on` pins Codex's `model_post_turn_compact_threshold_percent` on
                each `ling exec` command line at the launcher's own rule for the run's limit
                (`turn_end_percent`), `off` pins it at 0, a number pins that percentage; None
                leaves the key alone, and `exec` never compacts at a turn's end. The launcher's
                switch is set to `1` or `0` beside it. A new run only, like `code_index`.
            shard: `I/K`: a new run covers only the I-th of K balanced shards of the selected
                instances (`SweBenchShards`), so K machines can share one list; recorded in the
                manifest, where `swe-bench merge` reads it. A resumed run keeps its shard, and
                naming another is refused.
            image_chunk: With `evaluate` and `keep_images` False, the most instances of one
                repository whose images are on disk at once; None takes `[swe_bench]
                image_chunk` (25), 0 the whole repository.

        Returns:
            int: 0 when the run did what it could (whatever its instances did), 1 when it could
            not run.
        """
        settings = settings or swe_bench_settings.SweBenchSettings()
        if code_index not in ARMS:
            print(f"❌ --code-index is one of: {', '.join(ARMS)}.")
            return 1
        if turn_end_compaction is not None and cls.turn_end_setting(turn_end_compaction) is None:
            print(
                f"❌ --turn-end-compaction {turn_end_compaction!r}: on, off or a percentage from 1 to 99."
            )
            return 1
        if prompt is not None:
            from dreamference.config import DreamferenceConfig

            if DreamferenceConfig.parse_prompt_name(prompt) is None:
                print(
                    f"❌ --prompt {prompt!r} is not a prompt's name (lowercase letters, digits, hyphens)."
                )
                return 1
            custom = cls.prompt_file(prompt)
            if custom is not None and not custom.is_file():
                print(
                    f"❌ No prompt named {prompt}: it is not built in ({', '.join(BUILT_IN_PROMPTS)}) "
                    f"and {custom} does not exist."
                )
                return 1
        unknown_rules = sorted(set(task_rules or []) - set(TASK_RULES))
        if unknown_rules:
            print(
                f"❌ --task-rules takes: {', '.join(TASK_RULES)} (not {', '.join(unknown_rules)})."
            )
            return 1
        unknown_hooks = sorted(set(hooks or []) - set(HOOK_SETS))
        if unknown_hooks:
            print(f"❌ --hooks takes: {', '.join(HOOK_SETS)} (not {', '.join(unknown_hooks)}).")
            return 1
        wanted_shard = SweBenchShards.parse(shard) if shard is not None else None
        if shard is not None and wanted_shard is None:
            print(f"❌ --shard {shard!r}: I/K, the I-th of K shards (1 <= I <= K).")  # noqa: T201 — CLI output
            return 1
        if image_chunk is not None and image_chunk < 0:
            print(  # noqa: T201 — CLI output
                f"❌ --image-chunk {image_chunk}: a number of instances, 0 for a whole repository."
            )
            return 1
        if (
            name is not None
            and not RUN_NAME.fullmatch(name)
            and SweBenchRunStore(name).manifest() is None
        ):
            print(
                f"❌ --name {name!r}: a run's name is letters, digits, '.', '_' and '-', because the grader "
                "names Docker containers after it (join task rules with '-': n3-tests-v2-issue-v1)."
            )
            return 1
        if require_smoke and not cls.smoke_passed():
            print(
                "❌ No smoke has passed on this machine with this harness version: "
                "run `ling-admin swe-bench smoke` first."
            )
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
        refusal = cls.resume_problem(store.manifest(), wanted_shard)
        if refusal:
            print(f"❌ Run {store.name}: {refusal}")  # noqa: T201 — CLI output
            return 1
        end = cls.until_time(until)
        with NightShiftQueue.runner_lock(NightShiftQueue.night_dir(), holder=LOCK_HOLDER) as held:
            if not held:
                print(
                    f"⚠️  {NightShiftQueue.runner_holder() or 'Another run'} holds the runner lock."
                )
                return 1
            gateway = SweBenchDocker.ensure_network(swe_bench_settings.NETWORK_NAME)
            if gateway is None:
                print(
                    f"❌ Could not create the internal Docker network {swe_bench_settings.NETWORK_NAME}."
                )
                return 1
            # The model gate refuses every request but this run's while the block lasts (§18). A server on
            # another machine (`vllm_host` naming it) has no gate of this machine's in front of it, so this
            # machine's gate is left open and the run waits for other requests there as it would without one.
            remote = cls.remote_server(vllm_host)
            subnet = None if remote else SweBenchDocker.subnet(swe_bench_settings.NETWORK_NAME)
            with cls.gate_hold(store, vllm_host, subnet, label) as hold:
                idle = settings.idle_minutes if idle_minutes is None else idle_minutes
                if hold.priority():
                    # Nobody else can reach the model now: an open session cannot start a turn, so
                    # the run only waits for requests already in flight to finish.
                    print(
                        "🚦 The model gate now refuses every request but this run's, until it ends "
                        "(`ling-admin night pause` lets them through for a while).",
                        flush=True,
                    )
                    idle = 0
                elif hold.subnet and not hold.enforced:
                    print(
                        "💡 No model gate answers in front of the model server (it comes with the next "
                        "`ling-admin server start`): this run waits for other requests, as before.",
                        flush=True,
                    )
                cls.admission.ignore_sessions = ignore_sessions or hold.priority()
                cls.wait_for_index_runs()
                reason = cls.admission.admit(
                    vllm_host,
                    mightling_bin,
                    idle,
                    end or datetime.now().astimezone() + timedelta(days=365),
                )
                reason = reason or SweBenchEvaluator.disk_problem(settings)
                # A resumed run keeps the arms it started with; a new one takes the flags.
                arms = store.manifest() or {
                    "loop_guard": loop_guard,
                    "output_budget": output_budget or 0,
                    "sampling": sampling or DEFAULT_SAMPLING,
                }
                reason = reason or cls.loop_guard_refusal(
                    arms, mightling_bin, cls.gate.probe(vllm_host)
                )
                if reason:
                    print(f"⚠️  Not run: {reason}.")
                    return 1
                served = cls.host.served_model(vllm_host)
                if served is None:
                    print(f"⚠️  Not run: the model server at {vllm_host} is not answering.")
                    return 1
                metrics = cls.host.metrics(vllm_host) or {}
                max_parallel = cls.max_parallel(settings, served)
                if max_parallel < settings.max_parallel:
                    print(  # noqa: T201 — CLI output
                        f"💡 At most {max_parallel} instance(s) at once: the cap of {served[0]}'s registry entry "
                        f"(max_parallel is {settings.max_parallel}).",
                        flush=True,
                    )
                parallel = cls.host.parallelism(
                    max_parallel, metrics.get("kv_pool", 0.0), settings.task_context
                )
                runtime_hash = SweBenchRuntime.ensure(
                    mightling_bin, SweBenchHarness.tool("patchelf")
                )
                if runtime_hash is None:
                    return 1

                manifest = store.manifest()
                if manifest is None:
                    try:
                        selected = cls.select(dataset, instances, limit, subset)
                    except (ValueError, OSError) as error:
                        print(f"❌ {error}")
                        return 1
                    whole = list(selected)
                    selected = cls.shard_of(dataset, whole, wanted_shard)
                    print(
                        f"🔎 Checking that {len(selected)} instance(s) grade correctly here "
                        "(reference patch resolves, no-op patch does not)..."
                    )
                    problems = SweBenchEvaluator.validate(dataset, selected, settings)
                    excluded = {i: problem for i, problem in problems.items() if problem}
                    manifest = cls.build_manifest(
                        store.name,
                        dataset,
                        selected,
                        excluded,
                        settings,
                        served,
                        runtime_hash,
                        mightling_bin,
                        parallel,
                        code_index,
                        prompt,
                        mask,
                        strip_names,
                        refine,
                        task_rules,
                        review_turn,
                        refine_version,
                        hooks=hooks,
                        turn_end_compaction=turn_end_compaction,
                        restart_after_compactions=restart_after_compactions,
                        apply_patch=apply_patch,
                        exec_tool=exec_tool,
                        compat=compat,
                        drop_old_outputs=drop_old_outputs,
                        loop_guard=loop_guard,
                        output_budget=output_budget,
                        sampling=sampling,
                        test_guard=test_guard,
                        finish_check=finish_check,
                    )
                    manifest.update(cls.shard_record(whole, wanted_shard))
                    store.write_manifest(manifest)
                elif (
                    manifest.get("runtime_hash") != runtime_hash
                    or manifest.get("served_model") != served[0]
                ):
                    print(
                        f"❌ Run {store.name} was started with another ling build or model "
                        f"({manifest.get('served_model')}); a run measures one configuration. Use a new --name."
                    )
                    return 1
                hold.update(manifest["instances"], parallel)

                model_url = f"http://{gateway}:{urlparse(vllm_host).port or 8000}"
                run_prompt = str(manifest.get("prompt") or DEFAULT_RUN_PROMPT)
                lanes, lane_notes = cls.lanes(vllm_host, served, settings, parallel)
                relays = cls.lane_relays(gateway, lanes, model_url, vllm_host if remote else None)
                for note in lane_notes:
                    print(f"   {note}")
                cave = manifest.get("cave_mode") or swe_bench_settings.SWE_BENCH_CAVE_MODE
                extra_env = {
                    "DREAMFERENCE_MIGHTLING_CAVE_MODE": str(cave),
                    "DREAMFERENCE_MIGHTLING_AIRGAPPED": "off",
                    "DREAMFERENCE_MIGHTLING_PROMPT": run_prompt,
                    # A run made before masking existed has no key: it ran unmasked.
                    "DREAMFERENCE_MIGHTLING_MASK": str(manifest.get("masking") or "off"),
                    # The runner orchestrates `--refine` itself; the launcher's own refine mode
                    # would turn each step into two sessions, whatever the setting says.
                    "DREAMFERENCE_MIGHTLING_REFINE": "off",
                }
                # The turn-end compaction arm (spec §16, night 10): Codex's key on every exec command
                # line (`SweBenchInstanceRun`), the launcher's switch beside it, only when the run asks.
                run_turn_end = str(manifest.get("turn_end_compaction") or AUTO_TURN_END)
                turn_end_percent = cls.turn_end_percent(
                    run_turn_end,
                    int(manifest.get("served_context") or 0),
                    int(manifest.get("task_context") or settings.task_context),
                )
                if turn_end_percent is not None:
                    extra_env[TURN_END_ENV] = "0" if turn_end_percent == 0 else "1"
                # The apply_patch arm (spec §22): the launcher's override, only when the run asks.
                run_apply_patch = str(manifest.get("apply_patch") or AUTO_APPLY_PATCH)
                if run_apply_patch != AUTO_APPLY_PATCH:
                    extra_env[APPLY_PATCH_ENV] = run_apply_patch
                # The Code Mode arm (spec §23): the launcher's switch, only when the run asks.
                run_exec_tool = str(manifest.get("exec_tool") or AUTO_EXEC_TOOL)
                if run_exec_tool != AUTO_EXEC_TOOL:
                    extra_env[EXEC_TOOL_ENV] = run_exec_tool
                # The compatibility-mode arm: the launcher's switch, only when the run asks.
                run_compat = str(manifest.get("compat") or AUTO_COMPAT)
                if run_compat != AUTO_COMPAT:
                    extra_env[COMPAT_ENV] = run_compat
                # The drop-first compaction arm (compaction spec §13): the launcher's switch, only
                # when the run asks. A run made before the option existed has no key and ran without.
                if manifest.get("drop_old_outputs"):
                    extra_env[DROP_OLD_OUTPUTS_ENV] = "1"
                # The loop guard's arms: the launcher's variables, only when the run asks. A run made
                # before the options existed has no keys and ran with none.
                extra_env.update(cls.loop_guard_env(manifest))
                extra_env.update(cls.guard_env(manifest))
                # A custom prompt reaches the container's CODEX_HOME read-only: the agent cannot edit
                # the text a later session of the same instance would start from.
                custom_prompt = cls.prompt_file(run_prompt)
                extra_mounts = (
                    [f"{custom_prompt}:{SCRATCH_MOUNT}/codex-home/{PROMPT_DIR}/{run_prompt}.md:ro"]
                    if custom_prompt is not None
                    else []
                )

                finished = set(store.finished())
                pending = [i for i in manifest["instances"] if i not in finished]
                rows = {
                    row["instance_id"]: row for row in SweBenchHarness.rows(manifest["dataset"])
                }
                indexes: dict[str, dict[str, Any]] = {}
                if manifest.get("code_index", "off") != "off" and pending:
                    # Every index is built before the first agent starts: an index run beside the
                    # agents would compete with them, and its time is not the agent's.
                    code_hash = SweBenchCodeIndex.ensure_runtime(SweBenchHarness.tool("patchelf"))
                    if code_hash is None:
                        return 1
                    # Which `ling-code` answered, beside the manifest, which is never edited: a
                    # resumed run may use another build, so each start appends its own line.
                    with Path(store.directory / "ling-code.sha256").open("a") as record:
                        record.write(f"{code_hash}  {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
                    arm = manifest["code_index"]
                    layer = "SCIP stores only" if arm == "exact" else "universal layer"
                    print(
                        f"🗂️  Indexing {len(pending)} repositories on the host ({layer})...",
                        flush=True,
                    )
                    # With `--eval --remove-images` the images are cycled one repository at a time; an
                    # image this pass pulled only to index goes again at once, and the instance pulls
                    # it back when it starts. Otherwise every image of the run would be on disk before
                    # the first agent starts (about 2.3 GB each), past the disk reserve on a 50-task run.
                    cycling = evaluate and not keep_images
                    for instance_id in pending:
                        image = manifest["images"][instance_id]["image"]
                        pulled = cycling and SweBenchDocker.image_digest(image) is None
                        record = (
                            SweBenchCodeIndex.ensure(rows[instance_id], image, arm)
                            if SweBenchDocker.ensure_image(image)
                            else None
                        )
                        if pulled:
                            SweBenchImageCache.release([image], settings)
                        if record is None:
                            print(
                                f"❌ {instance_id} has no index, and a run measures one arm: not started."
                            )
                            return 1
                        indexes[instance_id] = dict(
                            SweBenchCodeIndex.container_arguments(rows[instance_id], record, arm),
                            record=record,
                        )
                        extra = (
                            (
                                f", {len(record.get('stores', []))} store(s), peak {record.get('peak_mb', 0)} MiB"
                                + (
                                    f", not finished: {', '.join(sorted(record['failed']))}"
                                    if record.get("failed")
                                    else ""
                                )
                            )
                            if arm == "exact"
                            else ""
                        )
                        print(
                            f"   {instance_id}: index {'cached' if record['cached'] else 'built'} "
                            f"({record['seconds']:.0f} s{extra})",
                            flush=True,
                        )
                print(
                    f"🏁 SWE-bench run {store.name}: {len(pending)} instance(s) to run, "
                    f"{len(finished)} done, {len(manifest.get('excluded', {}))} excluded; "
                    f"up to {parallel} at once on {served[0]}."
                )
                interrupted = False
                previous = signal.getsignal(signal.SIGTERM)
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, cls._raise_interrupt)
                try:
                    # One repository at a time, in chunks of at most `image_chunk` instances, only when
                    # the images are to be removed after grading; otherwise everything is one group, so
                    # small repositories do not run alone. The chunks are cut from the whole run, so a
                    # resumed run has the same ones, and a chunk with nothing left to run is skipped.
                    cycling = evaluate and not keep_images
                    chunk = settings.image_chunk if image_chunk is None else image_chunk
                    waiting = set(pending)
                    groups = cls.run_groups(
                        manifest["instances"], waiting, rows, chunk if cycling else None
                    )
                    for group in groups:
                        todo = pending if group is None else [i for i in group if i in waiting]
                        stopped = cls.schedule(
                            store,
                            todo,
                            rows,
                            manifest,
                            settings,
                            runtime_hash,
                            model_url,
                            vllm_host,
                            mightling_bin,
                            parallel,
                            end,
                            extra_env,
                            indexes,
                            extra_mounts,
                            lanes=lanes,
                            hold=hold,
                        )
                        if evaluate:
                            graded_now = list(manifest["instances"]) if group is None else group
                            SweBenchEvaluator.grade(store, settings, only=graded_now)
                            if cycling and not stopped:
                                SweBenchImageCache.release(
                                    [
                                        manifest["images"][i]["image"]
                                        for i in graded_now
                                        if i in manifest["images"]
                                    ],
                                    settings,
                                )
                        if stopped:
                            print(f"⏸️  Stopped: {stopped}. Run the same command again to resume.")
                            break
                except KeyboardInterrupt:
                    interrupted = True
                    print(
                        "\n⏸️  Interrupted; the instances that were running will run again on resume."
                    )
                finally:
                    for relay in relays:
                        relay.close()
                    if threading.current_thread() is threading.main_thread():
                        signal.signal(signal.SIGTERM, previous)
                done = len(store.finished())
                print(
                    f"✅ {done} of {len(manifest['instances'])} instance(s) have a prediction: "
                    f"{store.predictions_path}"
                )
                return 130 if interrupted else 0

    @classmethod
    def _raise_interrupt(cls, *_: Any) -> None:
        raise KeyboardInterrupt

    @classmethod
    def max_parallel(
        cls, settings: "swe_bench_settings.SweBenchSettings", served: tuple | None
    ) -> int:
        """The configured `max_parallel`, lowered to the served model's own cap.

        A registry entry may carry `swe_bench_max_parallel`, for a model that leaves the host too
        little memory for the task containers.

        Args:
            settings: The benchmark's settings.
            served: What the model server serves, `(model id, context length)`.

        Returns:
            int: At least 1.
        """
        from dreamference.hardware import ModelMatrixRegistry

        cap = ModelMatrixRegistry.swe_bench_max_parallel(served[0] if served else None)
        return max(1, min(settings.max_parallel, cap) if cap else settings.max_parallel)

    @classmethod
    def lanes(
        cls,
        vllm_host: str,
        served: Any,
        settings: "swe_bench_settings.SweBenchSettings",
        parallel: int,
    ) -> Any:
        """This machine's model server and every paired node serving the same model
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

        local = {
            "name": "this machine",
            "node": None,
            "host": vllm_host,
            "parallel": parallel,
            "budget": None,
        }
        if NodeLanes.wanted(settings.nodes) == [] or not NodePairing.paired():
            return [local], []
        max_parallel = cls.max_parallel(settings, served)
        budget = lambda pool: (
            cls.host.parallelism(max_parallel, pool, settings.task_context),
            None,
        )
        lanes, skipped = NodeLanes.lanes(vllm_host, served, settings.nodes, cls.host, budget)
        lanes[0]["parallel"] = parallel
        notes = [
            f"Also using {lane['name']}'s model server ({lane['host']}): up to {lane['parallel']} instance(s) "
            f"there; the containers run on this machine."
            for lane in lanes[1:]
        ]
        return lanes, notes + skipped

    @classmethod
    def needs_gate_guard(cls, arms: dict[str, Any]) -> bool:
        """Whether a run's arms need the model gate's half of the loop guard (`model_gate_guard.py`).

        Args:
            arms: The manifest, or the new run's `loop_guard`, `output_budget` and `sampling`.

        Returns:
            bool: True with a cap or a sampling arm; the detector alone lives in `ling`.
        """
        return (
            int(arms.get("output_budget") or 0) > 0
            or str(arms.get("sampling") or DEFAULT_SAMPLING) != DEFAULT_SAMPLING
        )

    @classmethod
    def loop_guard_env(cls, manifest: dict[str, Any]) -> dict[str, str]:
        """The launcher's variables for a run's loop-guard arms; empty for a run without them.

        Args:
            manifest: The run's manifest.

        Returns:
            Dict[str, str]: Variables for every session's container.
        """
        env: dict[str, str] = {}
        if manifest.get("loop_guard"):
            env[LOOP_GUARD_ENV] = "on"
        if int(manifest.get("output_budget") or 0) > 0:
            env[OUTPUT_BUDGET_ENV] = str(int(manifest["output_budget"]))
        if str(manifest.get("sampling") or DEFAULT_SAMPLING) != DEFAULT_SAMPLING:
            env[SAMPLING_ENV] = str(manifest["sampling"])
        return env

    @classmethod
    def loop_guard_refusal(
        cls, arms: dict[str, Any], mightling_bin: str, gate: dict[str, Any] | None
    ) -> str | None:
        """Why a run's loop-guard arms cannot be measured here, if they cannot.

        An arm must never silently run as the default: a value the arms do not take, a `ling` that
        does not read the variables, or a gate that cannot apply them is a reason not to run.

        Args:
            arms: The manifest, or the new run's `loop_guard`, `output_budget` and `sampling`.
            mightling_bin: The `ling` the containers get.
            gate: This machine's gate probe (`ModelGate.probe`), None without a gate.

        Returns:
            Optional[str]: The reason, or None.
        """
        budget = int(arms.get("output_budget") or 0)
        if str(arms.get("sampling") or DEFAULT_SAMPLING) not in SAMPLING_ARMS:
            return f"--sampling is one of: {', '.join(SAMPLING_ARMS)}"
        if 0 < budget < MIN_OUTPUT_BUDGET or budget < 0:
            return f"--output-budget {budget}: 0 (no cap) or at least {MIN_OUTPUT_BUDGET} tokens"
        if not cls.loop_guard_env(arms):
            return None
        if not cls.ling_reads(mightling_bin, OUTPUT_BUDGET_ENV):
            return (
                f"{mightling_bin} does not read {OUTPUT_BUDGET_ENV}: rebuild it (`ling-admin codex build`) "
                "from a commit that has the loop guard"
            )
        if cls.needs_gate_guard(arms) and not (gate or {}).get("loop_guard"):
            return (
                "--output-budget and --sampling are applied by the model gate, and the gate in front of the "
                "model server predates them: they come with the next `ling-admin server start`"
            )
        return None

    @classmethod
    def ling_reads(cls, binary: str, name: str) -> bool:
        """Whether a `ling` binary names an environment variable, as the night scripts check an arm's switch.

        Args:
            binary: Its path.
            name: The variable.

        Returns:
            bool: True when the name is in the file.
        """
        needle = name.encode("ascii")
        try:
            with Path(binary).open("rb") as handle:
                return cls.stream_contains(handle, needle)
        except OSError:
            return False

    @classmethod
    def stream_contains(cls, handle: BinaryIO, needle: bytes) -> bool:
        """Whether a file holds a byte string, read 4 MiB at a time so a 300 MB binary is never held whole.

        Args:
            handle: The open file.
            needle: The bytes looked for.

        Returns:
            bool: True when found, across chunk boundaries too.
        """
        tail = b""
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle) :]
        return False

    @classmethod
    def remote_server(cls, vllm_host: str) -> bool:
        """Whether the configured model server is another machine's.

        An engine A/B serves the agent from the engine's development machine: its host is neither
        loopback nor one of this machine's names.

        Args:
            vllm_host: The configured model server's base URL.

        Returns:
            bool: True when the containers must reach it through a relay on the gateway.
        """
        host = (urlparse(vllm_host).hostname or "").lower().rstrip(".")
        if host in ("", "localhost", "::1", "0.0.0.0") or host.startswith("127."):
            return False
        names = {socket.gethostname().lower(), socket.getfqdn().lower()}
        names |= {name + ".local" for name in list(names)}
        return host not in names

    @classmethod
    def open_relays(cls, gateway: str, lanes: list[dict[str, Any]]) -> list[SweBenchRelay]:
        """Gives each replica lane an address its containers can reach: a relay on the gateway.

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
    def schedule(
        cls,
        store: SweBenchRunStore,
        pending: list[str],
        rows: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
        settings: "swe_bench_settings.SweBenchSettings",
        runtime_hash: str,
        model_url: str,
        vllm_host: str,
        mightling_bin: str,
        parallel: int,
        end: datetime | None,
        extra_env: dict[str, str],
        indexes: dict[str, dict[str, Any]] | None = None,
        extra_mounts: list[str] | None = None,
        lanes: list[dict[str, Any]] | None = None,
        hold: SweBenchGateHold | None = None,
    ) -> str | None:
        """Starts instances while the machine is quiet, memory and disk admit one more and `--until`
        has not passed; waits for the running ones. With `lanes`, an instance goes to the first
        model server with room and nothing in its way.

        With `hold` in force (§18) this machine's server refuses everyone else, so open sessions
        and other requests no longer hold a start back; during a pause they do again, as before
        the gate. A replica whose own gate a run of its own holds is skipped.

        Returns:
            Optional[str]: Why the run stopped before finishing `pending`; None when it finished.
        """
        lanes = lanes or [
            {
                "name": "this machine",
                "node": None,
                "host": vllm_host,
                "parallel": parallel,
                "model_url": model_url,
            }
        ]
        queue = list(pending)
        active: list[tuple] = []
        waiting_for: str | None = None
        stopped: str | None = None
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
                free = [
                    lane
                    for lane in lanes
                    if sum(1 for _, run in active if lane_of(run, vllm_host) == lane["host"])
                    < lane["parallel"]
                ]
                if queue and free:
                    reason, lane = None, None
                    priority = hold is not None and hold.priority()
                    for candidate in free:
                        if candidate.get("node") is None:
                            blocked = cls.admission.start_blocker(
                                candidate["host"],
                                mightling_bin,
                                active,
                                settings,
                                **({"priority": True} if priority else {}),
                            )
                        else:
                            remote_gate = cls.gate.probe(candidate["host"]) or {}
                            blocked = (
                                "its model gate is closed for a benchmark run of its own"
                                if remote_gate.get("state") == "closed"
                                else "its model gate cannot apply this run's loop-guard arm (it predates it)"
                                if cls.needs_gate_guard(manifest)
                                and not remote_gate.get("loop_guard")
                                else cls.admission.start_blocker(
                                    candidate["host"], mightling_bin, active, settings, local=False
                                )
                            )
                        if blocked is None:
                            reason, lane = None, candidate
                            break
                        reason = reason or (
                            blocked
                            if candidate.get("node") is None
                            else f"{candidate['name']}: {blocked}"
                        )
                    if lane is not None:
                        instance_id = queue.pop(0)
                        run = SweBenchInstanceRun(
                            store,
                            rows[instance_id],
                            manifest["images"][instance_id]["image"],
                            manifest["model_name_or_path"],
                            settings,
                            SweBenchRuntime.directory(),
                            lane.get("model_url") or model_url,
                            time.time() + settings.task_timeout_s,
                            extra_env,
                            (indexes or {}).get(instance_id),
                            extra_mounts,
                            issue=(manifest.get("stripped_issues") or {}).get(instance_id),
                            refine=bool(manifest.get("refine", False)),
                            task_rules=manifest.get("task_rules") or [],
                            review_turn=bool(manifest.get("review_turn", False)),
                            # A run made before refine-v2 existed has no key: it ran v1.
                            refine_version=str(manifest.get("refine_version") or "v1"),
                            hooks=manifest.get("hooks") or [],
                            # The turn-end compaction arm (spec §16): Codex's key on each exec command
                            # line; a run made before the option existed never set it.
                            turn_end_percent=cls.turn_end_percent(
                                str(manifest.get("turn_end_compaction") or AUTO_TURN_END),
                                int(manifest.get("served_context") or 0),
                                int(manifest.get("task_context") or settings.task_context),
                            ),
                            # A run made before the option existed never restarted.
                            restart_after_compactions=int(
                                manifest.get("restart_after_compactions") or 0
                            ),
                            # A run made before the option existed left the launcher's choice alone.
                            apply_patch=str(manifest.get("apply_patch") or AUTO_APPLY_PATCH),
                            exec_tool=str(manifest.get("exec_tool") or AUTO_EXEC_TOOL),
                        )
                        run.lane_host = lane["host"]
                        if lane.get("node"):
                            run.notes.append(
                                f"model server: {lane['name']} (a replica of this machine's model)"
                            )
                        thread = threading.Thread(
                            target=cls._run_one, args=(run,), name=f"swe-{instance_id}", daemon=True
                        )
                        thread.start()
                        active.append((thread, run))
                        waiting_for = None
                        continue
                    # Said once per kind of reason: the free-memory figure in it moves every poll.
                    kind = re.sub(r"[\d.]+", "#", reason)
                    if kind != waiting_for:
                        print(
                            f"⏳ {datetime.now():%H:%M} waiting to start the next instance: "
                            f"{reason.replace('the night run', 'this run')}.",
                            flush=True,
                        )
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
            run.store.write_state(
                run.instance_id,
                {
                    "instance_id": run.instance_id,
                    "status": "error",
                    "notes": ["the image could not be pulled"],
                },
            )
            run.store.append_prediction(run.instance_id, run.model_name, "")
            print(f"   {run.instance_id}: error (the image could not be pulled)")
            return
        # The clock starts once the image is here: a pull is not the agent's time.
        run.started = time.time()
        run.deadline = run.started + run.settings.task_timeout_s
        status = run.run()
        print(f"   {run.instance_id}: {status} ({int(time.time() - run.started)} s)", flush=True)

    @classmethod
    def by_repository(
        cls, instance_ids: list[str], rows: dict[str, dict[str, Any]]
    ) -> dict[Any, list[str]]:
        """Groups instances by repository, keeping their order: with `--eval --remove-images` a
        repository's instances are run and graded together, so its images can go before the next.

        Returns:
            Dict[str, List[str]]: Repository to its instances.
        """
        groups: dict[str, list[str]] = {}
        for instance_id in instance_ids:
            groups.setdefault(rows[instance_id]["repo"], []).append(instance_id)
        return groups

    @classmethod
    def shard_of(cls, dataset: str, whole: list[str], wanted: tuple[int, int] | None) -> list[str]:
        """The instances a new run covers: all of them, or its shard of them (`--shard`).

        Args:
            dataset: The dataset the instances are from, for their repositories.
            whole: The selected instances.
            wanted: The (I, K) shard asked for, or None.

        Returns:
            list[str]: The instances to run.
        """
        if wanted is None:
            return whole
        repos = {
            row["instance_id"]: str(row.get("repo", "")) for row in SweBenchHarness.rows(dataset)
        }
        selected = SweBenchShards.pick(whole, repos, *wanted)
        print(  # noqa: T201 — CLI output
            f"🧩 Shard {wanted[0]} of {wanted[1]}: {len(selected)} of the {len(whole)} selected "
            f"instance(s), {SweBenchShards.mix(selected, repos)}."
        )
        return selected

    @classmethod
    def shard_record(cls, whole: list[str], wanted: tuple[int, int] | None) -> dict[str, Any]:
        """The manifest's record of a new run's shard, where `swe-bench merge` reads it.

        Args:
            whole: The selected instances, before the shard was cut.
            wanted: The (I, K) shard, or None.

        Returns:
            dict[str, Any]: `{"shard": {...}}`, or nothing for a run made without `--shard`.
        """
        if wanted is None:
            return {}
        return {
            "shard": {
                "index": wanted[0],
                "of": wanted[1],
                "list_instances": len(whole),
                "list_sha256": SweBenchShards.digest(whole),
            }
        }

    @classmethod
    def lane_relays(
        cls,
        gateway: str,
        lanes: list[dict[str, Any]],
        model_url: str,
        remote_host: str | None,
    ) -> list[SweBenchRelay]:
        """Points each lane's containers at their model server, through a relay where they need one.

        The containers reach the run network's gateway only. This machine's server is there on its
        own port; a replica lane, or every lane when the server is another machine's (the engine
        A/B), gets a relay on the gateway.

        Args:
            gateway: The run network's gateway address.
            lanes: The run's lanes, this machine's first.
            model_url: This machine's server as the containers reach it.
            remote_host: The other machine's server when the run is served from there, else None.

        Returns:
            list[SweBenchRelay]: The relays opened.
        """
        if remote_host is not None:
            print(  # noqa: T201 — CLI output
                f"   The model server is another machine's ({remote_host}): reached through a relay "
                "on the run's network gateway.",
                flush=True,
            )
            return cls.open_relays(gateway, lanes)
        lanes[0]["model_url"] = model_url
        return cls.open_relays(gateway, lanes[1:])

    @classmethod
    def run_groups(
        cls,
        instances: list[str],
        waiting: set[str],
        rows: dict[str, dict[str, Any]],
        chunk: int | None,
    ) -> list[list[str] | None]:
        """The groups a run schedules in turn: one, or its image chunks with something left to run.

        Args:
            instances: The run's instances, in run order.
            waiting: Those not run yet.
            rows: Their dataset rows.
            chunk: The image chunk when images are removed after grading (0: a whole repository),
                or None for one group of everything.

        Returns:
            list[list[str] | None]: The chunks, or `[None]` for everything at once.
        """
        if chunk is None:
            return [None]
        groups: list[list[str] | None] = [
            list(group)
            for group in cls.image_chunks(instances, rows, chunk)
            if waiting.intersection(group)
        ]
        print(  # noqa: T201 — CLI output
            f"🧩 Images one repository at a time, at most {chunk or 'all'} of one repository "
            f"on disk at once: {len(groups)} chunk(s) to run.",
            flush=True,
        )
        return groups

    @classmethod
    def image_chunks(
        cls, instance_ids: list[str], rows: dict[str, dict[str, Any]], size: int
    ) -> list[list[str]]:
        """Cuts instances into the chunks whose images are on disk together (`--remove-images`).

        One repository at a time (`by_repository`), a repository of more than `size` instances
        into as few chunks of at most `size` as hold it, as equal as can be and in the instances'
        order (86 at 25: 22, 22, 21, 21).

        Args:
            instance_ids: The instances, in run order.
            rows: Their dataset rows.
            size: The most instances in a chunk; 0 for a whole repository.

        Returns:
            list[list[str]]: The chunks, repository by repository.
        """
        chunks: list[list[str]] = []
        for group in cls.by_repository(instance_ids, rows).values():
            parts = -(-len(group) // size) if size > 0 else 1
            low, extra = divmod(len(group), parts)
            start = 0
            for part in range(parts):
                end = start + low + (1 if part < extra else 0)
                chunks.append(group[start:end])
                start = end
        return chunks

    @classmethod
    def resume_problem(cls, manifest: dict[str, Any] | None, shard: tuple | None) -> str | None:
        """Why a run of this name cannot be run (again) as asked.

        Args:
            manifest: The existing run's manifest; None for a new run.
            shard: The (I, K) asked for, or None.

        Returns:
            str | None: The reason, or None.
        """
        if manifest is None:
            return None
        if manifest.get("merged_from"):
            return (
                f"it is the merge of {', '.join(manifest['merged_from'])} (`swe-bench merge`), which only "
                "reports: resume the shard that is not finished and merge again."
            )
        recorded = manifest.get("shard") or {}
        recorded_pair = (recorded.get("index"), recorded.get("of")) if recorded else None
        if shard is not None and tuple(shard) != recorded_pair:
            had = f"shard {recorded_pair[0]}/{recorded_pair[1]}" if recorded_pair else "no shard"
            return (
                f"it was started with {had}; a resumed run keeps its instances. Use a new --name."
            )
        return None

    @classmethod
    def default_name(cls) -> str:
        """Returns:
        str: `<date>-a`, or the next free letter.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        existing = set(SweBenchRunStore.runs())
        for letter in "abcdefghijklmnopqrstuvwxyz":
            if f"{today}-{letter}" not in existing:
                return f"{today}-{letter}"
        return f"{today}-{int(time.time())}"

    @classmethod
    def until_time(cls, until: str | None) -> datetime | None:
        """Args:
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
