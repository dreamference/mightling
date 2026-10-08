"""
`ling-admin night run`: works through the Night Shift queue inside a window
(specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5).

It never starts, stops or loads the model server, and gives way to anyone working interactively.
Admission (§5.2) decides whether the night runs at all; after that a scheduling loop starts tasks
while the window is open, the machine is quiet and memory admits one more, and stops them when
the window closes.

A paired node serving the same model is a second *lane* (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3):
the tasks still run here, but some of them send their model requests to that node, each lane
holding as many tasks as its own KV pool allows. A task queued with `--on <node>` is not run here
at all: it is handed to that node, whose own runner works it (`NightShiftRemote`).
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple

from dreamference.night_shift.night_shift_host import GIB, NIGHT_POOL_SHARE, NightShiftHost
from dreamference.night_shift.night_shift_index import NightShiftIndex
from dreamference.night_shift.night_shift_queue import NightShiftQueue, RUNNABLE_STATUSES
from dreamference.night_shift.night_shift_remote import NightShiftRemote
from dreamference.night_shift.night_shift_report import NightShiftReport
from dreamference.night_shift.night_shift_settings import NightShiftSettings
from dreamference.night_shift.night_shift_task_run import NightShiftTaskRun

# Free memory a night run never eats into: room above earlyoom's 5% line (~6.2 GiB here).
MEMORY_RESERVE: Final[int] = 8 * GIB

POLL_S: Final[float] = 5.0
IDLE_POLL_S: Final[float] = 30.0


class NightShiftRunner:
    """One night run."""

    # Seams the tests replace: the clock's sleep, and the probes of the host.
    sleep = staticmethod(time.sleep)
    host = NightShiftHost
    index = NightShiftIndex
    remote = NightShiftRemote
    ignore_sessions: bool = False

    @classmethod
    def run(cls, until: Optional[str] = None, minutes: Optional[float] = None,
            idle_minutes: Optional[float] = None, night_dir: Optional[Path] = None,
            mightling_bin: Optional[str] = None, vllm_host: Optional[str] = None,
            settings: Optional[NightShiftSettings] = None, ignore_sessions: bool = False) -> int:
        """
        Runs the queue until the window closes or nothing is left.

        Args:
            until: `HH:MM` the window ends; defaults to the end of `[night] window`.
            minutes: Run for this many minutes instead of until a time of day.
            idle_minutes: How long the model server must have been idle before starting;
                defaults to `[night] idle_minutes` (10).
            night_dir: The queue directory; defaults to `$CODEX_HOME/night`.
            mightling_bin: The `ling` executable; defaults to the installed branded build.
            vllm_host: The model server; defaults to the configured host.
            settings: Night Shift settings; defaults to the config file's.
            ignore_sessions: Do not wait for open `ling` TUI sessions (for testing beside one;
                requests from them still block a start).

        Returns:
            int: 0 when the run completed (whatever its tasks did), 1 when it could not run.
        """
        settings = settings or NightShiftSettings()
        night_dir = night_dir or NightShiftQueue.night_dir()
        started = datetime.now().astimezone()
        end = cls.window_end(started, until, minutes, settings.window)
        if mightling_bin is None:
            from dreamference.runner.codex_installer import CodexInstaller
            mightling_bin = os.environ.get("MIGHTLING_NIGHT_MIGHTLING_BIN") or CodexInstaller.get_codex_executable()
        if not mightling_bin:
            print("❌ ling is not built: run `ling-admin codex build` first.")
            return 1
        if vllm_host is None:
            from dreamference.config import DreamferenceConfig
            vllm_host = DreamferenceConfig().vllm_host

        with NightShiftQueue.runner_lock(night_dir) as held:
            if not held:
                print("⚠️  Another night run holds the runner lock.")
                return 1
            pending = cls.pending_tasks(night_dir)
            sent = cls.remote.sent_tasks(night_dir)
            if not pending and not sent:
                print("✅ Night Shift: nothing queued.")
                return 0
            print(f"🌙 Night Shift: {len(pending)} task(s) queued; window ends {end:%H:%M}.")
            notes: List[str] = []
            # A task queued for another node is handed over whatever this machine's own state: that
            # node's runner admits it by that node's checks.
            sent += cls.remote.hand_off(night_dir, pending, notes)
            local_tasks = [task for task in pending if not task.get("on")]
            looked_at = pending + [task for task in sent if task["id"] not in {t["id"] for t in pending}]
            if local_tasks:
                idle = settings.idle_minutes if idle_minutes is None else idle_minutes
                cls.ignore_sessions = ignore_sessions
                reason = cls.admit(vllm_host, mightling_bin, idle, end)
                if reason:
                    notes.append(f"Not run: {reason}. The tasks stay queued for the next night.")
                    print(f"⚠️  {reason}")
                    cls.remote.collect(night_dir, sent, end, notes, wait=False)
                    cls._write_report(night_dir, started, cls._reread(night_dir, looked_at), notes)
                    return 0
                lanes, lane_notes = cls.lanes(vllm_host, settings)
                local = lanes[0]
                notes.append(cls.budget_note(local["parallel"], local["budget"], local["kv_pool"], settings))
                notes.extend(lane_notes)
                cls.refresh_indexes(local_tasks, settings, end, notes)
                cls.schedule(night_dir, local_tasks, settings, mightling_bin, vllm_host, end, local["parallel"],
                             notes, context_budget=local["budget"], lanes=lanes)
            cls.remote.collect(night_dir, sent, end, notes, wait=False)
        # Waiting for other nodes uses nothing of this machine's, so it does not hold the runner
        # lock: `server start`, `codex build` and `index` refuse while that is held.
        cls.remote.collect(night_dir, sent, end, notes, wait=True)
        final = cls._reread(night_dir, looked_at)
        path = cls._write_report(night_dir, started, final, notes)
        counts: Dict[str, int] = {}
        for task in final:
            counts[task.get("status", "?")] = counts.get(task.get("status", "?"), 0) + 1
        summary = ", ".join(f"{count} {status}" for status, count in counts.items())
        print(f"✅ Night Shift finished: {summary}. Report: {path}")
        return 0

    @classmethod
    def _reread(cls, night_dir: Path, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [NightShiftQueue.read(night_dir, task["id"]) or task for task in tasks]

    @classmethod
    def lanes(cls, vllm_host: str, settings: NightShiftSettings) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        This machine's model server and every paired node serving the same model, each with the
        tasks it may hold and their compaction limit.

        Args:
            vllm_host: This machine's model server.
            settings: Night Shift settings (`max_parallel`, `task_context`, `compact_at`, `nodes`).

        Returns:
            Tuple[List[Dict[str, Any]], List[str]]: The lanes, this machine's first; and the
            report's lines on the other lanes and on each paired node left out.
        """
        from dreamference.node.node_lanes import NodeLanes
        from dreamference.node.node_pairing import NodePairing
        budget = lambda pool: cls.host.task_budget(settings.max_parallel, pool, settings.task_context,
                                                   settings.compact_at)
        if NodeLanes.wanted(settings.nodes) == [] or not NodePairing.paired():
            kv_pool = (cls.host.metrics(vllm_host) or {}).get("kv_pool", 0.0)
            parallel, limit = budget(kv_pool)
            return [{"name": "this machine", "node": None, "host": vllm_host, "kv_pool": kv_pool,
                     "parallel": parallel, "budget": limit}], []
        served = cls.host.served_model(vllm_host) or ("", 0)
        lanes, skipped = NodeLanes.lanes(vllm_host, served, settings.nodes, cls.host, budget)
        return lanes, [NodeLanes.describe(lane) for lane in lanes[1:]] + skipped

    @classmethod
    def admit(cls, vllm_host: str, mightling_bin: str, idle_minutes: float, end: datetime) -> Optional[str]:
        """
        The admission checks of §5.2, in order; the first failure is the reason the night does
        not run.

        Returns:
            Optional[str]: The reason, or None when the run may start.
        """
        if cls.host.served_model(vllm_host) is None:
            return f"the model server at {vllm_host} is not answering (the night run never starts it)"
        if not cls.host.host_safety_ok():
            return "the host-safety checks failed (see `ling-admin server start` for the details)"
        available = cls.host.mem_available_bytes()
        if available < MEMORY_RESERVE:
            return f"only {available / GIB:.1f} GiB of memory is available; a night run needs {MEMORY_RESERVE // GIB}"
        heavy = cls.host.heavy_jobs()
        if heavy:
            return "; ".join(heavy)
        return cls.wait_for_idle(vllm_host, mightling_bin, idle_minutes, end)

    @classmethod
    def wait_for_idle(cls, vllm_host: str, mightling_bin: str, idle_minutes: float,
                      end: datetime) -> Optional[str]:
        """
        Waits until nobody has used the model for `idle_minutes`: no TUI session, no running or
        queued request, and the server's prompt-token counter unchanged throughout.

        Returns:
            Optional[str]: Why the run cannot start (someone is working, or the window ran out
            while waiting); None once the model has been idle long enough.
        """
        quiet_since: Optional[float] = None
        last_served: Optional[float] = None
        while True:
            now = time.time()
            if not cls.ignore_sessions and cls.host.interactive_mightling_pids(mightling_bin):
                return "a Mightling session is open (interactive use wins)"
            metrics = cls.host.metrics(vllm_host)
            if metrics is None:
                return "the model server's /metrics does not answer"
            busy = metrics["running"] > 0 or (last_served is not None and metrics["served"] != last_served)
            last_served = metrics["served"]
            if busy or quiet_since is None:
                quiet_since = now
            if not busy and now - quiet_since >= idle_minutes * 60:
                return None
            if datetime.now().astimezone() >= end:
                return f"the model was in use until the window closed ({idle_minutes:g} idle minutes needed)"
            cls.sleep(min(IDLE_POLL_S, max(1.0, idle_minutes * 60)))

    @classmethod
    def refresh_indexes(cls, pending: List[Dict[str, Any]], settings: NightShiftSettings,
                        end: datetime, notes: List[str]) -> None:
        """
        Refreshes the code index of every repository with queued tasks, one at a time, before any
        task starts, so each begins with a fresh index (code-index spec §6.3). `ling-code` admits
        and sandboxes its own runs; a refresh that does not finish leaves the index as it was,
        which the router's answers already account for.

        Args:
            pending: The night's tasks.
            settings: Night Shift settings (`index`, `index_timeout`).
            end: When the window closes.
            notes: The morning report's notes, appended to.
        """
        if not settings.index:
            return
        binary = cls.index.executable()
        if binary is None:
            return
        repos: List[str] = []
        for task in pending:
            if task.get("remote"):
                # Another machine's repository, held here as a bare job repository: not indexed.
                continue
            if task.get("repo") and task["repo"] not in repos:
                repos.append(task["repo"])
        for repo in repos:
            # Never more than half of what is left of the window: the tasks are what it is for.
            budget = min(settings.index_timeout_s, (end.timestamp() - time.time()) / 2)
            if budget < 60:
                notes.append(f"Code index of {repo}: not refreshed, the window is nearly over.")
                continue
            if not Path(repo).is_dir():
                continue
            notes.append(f"Code index of {repo}: {cls.index.refresh(binary, Path(repo), budget)}.")

    @classmethod
    def budget_note(cls, parallel: int, budget: Optional[int], kv_pool: float,
                    settings: NightShiftSettings) -> str:
        """
        The report's line on how the KV pool was divided.

        Args:
            parallel: Tasks at once.
            budget: Each task's compaction limit, or None.
            kv_pool: Tokens of KV cache, 0 if unknown.
            settings: Night Shift settings.

        Returns:
            str: The note.
        """
        head = f"Up to {parallel} task(s) at once (max_parallel {settings.max_parallel}"
        if not kv_pool:
            tail = f"; the KV pool is unknown, so one at a time{f', compacting at {budget} tokens' if budget else ''})."
        elif budget is None:
            tail = (f", KV pool {int(kv_pool)} tokens). compact_at = 0: no per-task limit is passed, so "
                    f"the tasks together are not held to the pool.")
        else:
            source = "compact_at" if settings.compact_at else f"{int(NIGHT_POOL_SHARE * 100)}% of the pool, split {parallel} way(s)"
            tail = f", KV pool {int(kv_pool)} tokens); each task's session compacts at {budget} tokens ({source})."
        return head + tail

    @classmethod
    def schedule(cls, night_dir: Path, pending: List[Dict[str, Any]], settings: NightShiftSettings,
                 mightling_bin: str, vllm_host: str, end: datetime, parallel: int, notes: List[str],
                 context_budget: Optional[int] = None, lanes: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Starts tasks while the window is open, the machine is quiet and memory admits one more;
        at the window's end asks every running task to stop (it is then `interrupted`). Each task
        compacts at `context_budget`, its share of the KV pool (None: no limit is passed).

        With `lanes`, a task goes to the first lane that has room and nothing in its way: this
        machine's server first, then each replica, each holding its own `parallel` tasks and
        compacting them at its own `budget`.
        """
        lanes = lanes or [{"name": "this machine", "node": None, "host": vllm_host, "parallel": parallel,
                           "budget": context_budget}]
        queue = cls.round_robin(pending)
        active: List[tuple] = []
        end_ts = end.timestamp()
        paused_for: Optional[str] = None
        while queue or active:
            active = [(thread, run) for thread, run in active if thread.is_alive()]
            if time.time() >= end_ts:
                for _, run in active:
                    run.stop_event.set()
                for thread, _ in active:
                    thread.join()
                if queue:
                    notes.append(f"The window closed with {len(queue)} task(s) not started; they stay queued.")
                return
            free = [lane for lane in lanes
                    if sum(1 for _, run in active if cls.lane_of(run, vllm_host) == lane["host"]) < lane["parallel"]]
            if queue and free:
                reason, lane = None, None
                for candidate in free:
                    # This machine's lane is asked exactly as before lanes existed; a replica's
                    # says it is not this machine's, so a session open here does not hold it up.
                    blocked = cls.start_blocker(candidate["host"], mightling_bin, active, settings) \
                        if candidate.get("node") is None else \
                        cls.start_blocker(candidate["host"], mightling_bin, active, settings, local=False)
                    if blocked is None:
                        reason, lane = None, candidate
                        break
                    reason = reason or (blocked if candidate.get("node") is None else f"{candidate['name']}: {blocked}")
                if lane is not None:
                    task = queue.pop(0)
                    deadline = min(end_ts, time.time() + settings.task_timeout_s)
                    run = NightShiftTaskRun(night_dir, task, settings, mightling_bin, deadline, model_host=lane["host"],
                                            context_budget=lane["budget"],
                                            model_node=lane["name"] if lane.get("node") else None,
                                            window_end=end_ts)
                    run.lane_host = lane["host"]
                    thread = threading.Thread(target=run.run, name=f"night-{task['id']}", daemon=True)
                    thread.start()
                    active.append((thread, run))
                    paused_for = None
                    continue
                # Compared without its numbers: the memory reason carries a free-memory figure that
                # changes on every poll, and noting each one put the same wait in the report eight
                # times a minute (live run, 2026-10-02).
                if cls.reason_kind(reason) != paused_for:
                    notes.append(f"{datetime.now():%H:%M}: waiting to start the next task: {reason}.")
                    paused_for = cls.reason_kind(reason)
            cls.sleep(POLL_S)

    @classmethod
    def reason_kind(cls, reason: str) -> str:
        """
        A start blocker's reason with its figures removed, so one wait is noted once.

        Args:
            reason: What `start_blocker` returned.

        Returns:
            str: The reason with every number replaced by `#`.
        """
        return re.sub(r"\d+(?:\.\d+)?", "#", reason)

    @classmethod
    def lane_of(cls, run: Any, default: str) -> str:
        """
        Args:
            run: A running task (or benchmark instance).
            default: The run's own model server.

        Returns:
            str: The model server the task sends its requests to.
        """
        return getattr(run, "lane_host", None) or default

    @classmethod
    def start_blocker(cls, vllm_host: str, mightling_bin: str, active: List[tuple],
                      settings: NightShiftSettings, local: bool = True) -> Optional[str]:
        """
        Whether another task may start now (§5.5 and memory).

        An outside request is one beyond the night's own: the engine's running and queued
        requests exceed the number of night tasks currently waiting on the model.

        Args:
            vllm_host: The model server the task would use.
            mightling_bin: The `ling` executable.
            active: The running tasks.
            settings: Night Shift settings.
            local: Whether that server is this machine's. An open `ling` session here uses this
                machine's server, so it blocks this machine's lane and no other; a replica's own
                users show up as its outside requests.

        Returns:
            Optional[str]: What blocks a start, or None.
        """
        if local and not cls.ignore_sessions and cls.host.interactive_mightling_pids(mightling_bin):
            return "a Mightling session is open"
        metrics = cls.host.metrics(vllm_host)
        if metrics is None:
            return "the model server's /metrics does not answer"
        ours = sum(1 for _, run in active if run.in_model and cls.lane_of(run, vllm_host) == vllm_host)
        if metrics["running"] > ours:
            return "the model server is serving a request that is not the night run's"
        cap = cls.host.parse_size(settings.task_memory)
        headroom = 0
        for _, run in active:
            unit = run.current_unit
            used = cls.host.scope_memory_current(f"{unit}.scope") if unit else 0
            headroom += max(0, cap - used)
        available = cls.host.mem_available_bytes() - headroom
        if available < MEMORY_RESERVE + cap:
            return (f"{available / GIB:.1f} GiB is free after the running tasks' allowance; "
                    f"one more needs {(MEMORY_RESERVE + cap) / GIB:.0f}")
        return None

    @classmethod
    def pending_tasks(cls, night_dir: Path) -> List[Dict[str, Any]]:
        """
        Returns:
            List[Dict[str, Any]]: Queued and interrupted tasks of every repository, oldest first.
        """
        return [task for task in NightShiftQueue.tasks(night_dir)
                if task.get("status") in RUNNABLE_STATUSES]

    @classmethod
    def round_robin(cls, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Orders tasks first in, first out within a repository and alternates between repositories,
        so one long list cannot starve another (§4).

        Args:
            tasks: Tasks, oldest first.

        Returns:
            List[Dict[str, Any]]: The order to start them in.
        """
        by_repo: Dict[str, List[Dict[str, Any]]] = {}
        for task in tasks:
            by_repo.setdefault(task.get("repo", ""), []).append(task)
        order: List[Dict[str, Any]] = []
        lists = list(by_repo.values())
        while any(lists):
            for repo_tasks in lists:
                if repo_tasks:
                    order.append(repo_tasks.pop(0))
        return order

    @classmethod
    def window_end(cls, now: datetime, until: Optional[str], minutes: Optional[float],
                   window: str) -> datetime:
        """
        When this run must stop.

        Args:
            now: The start time, timezone-aware.
            until: `HH:MM`, or None for the window's end.
            minutes: A duration instead, if given.
            window: The configured `HH:MM-HH:MM` window.

        Returns:
            datetime: The next such time after `now`.
        """
        if minutes is not None:
            return now + timedelta(minutes=minutes)
        if until:
            hour, minute = (int(part) for part in until.split(":"))
        else:
            _, end = NightShiftSettings.parse_window(window)
            hour, minute = end.hour, end.minute
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    @classmethod
    def _write_report(cls, night_dir: Path, started: datetime, tasks: List[Dict[str, Any]],
                      notes: List[str]) -> Path:
        return NightShiftReport.write(night_dir, started, NightShiftReport.render(started, tasks, notes))
