"""
`puffin-admin night run`: works through the Night Shift queue inside a window
(specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §5).

It never starts, stops or loads the model server, and gives way to anyone working interactively.
Admission (§5.2) decides whether the night runs at all; after that a scheduling loop starts tasks
while the window is open, the machine is quiet and memory admits one more, and stops them when
the window closes.
"""

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
from dreamference.night_shift.night_shift_queue import NightShiftQueue, RUNNABLE_STATUSES
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
    ignore_sessions: bool = False

    @classmethod
    def run(cls, until: Optional[str] = None, minutes: Optional[float] = None,
            idle_minutes: Optional[float] = None, night_dir: Optional[Path] = None,
            puffin_bin: Optional[str] = None, vllm_host: Optional[str] = None,
            settings: Optional[NightShiftSettings] = None, ignore_sessions: bool = False) -> int:
        """
        Runs the queue until the window closes or nothing is left.

        Args:
            until: `HH:MM` the window ends; defaults to the end of `[night] window`.
            minutes: Run for this many minutes instead of until a time of day.
            idle_minutes: How long the model server must have been idle before starting;
                defaults to `[night] idle_minutes` (10).
            night_dir: The queue directory; defaults to `$CODEX_HOME/night`.
            puffin_bin: The `puffin` executable; defaults to the installed branded build.
            vllm_host: The model server; defaults to the configured host.
            settings: Night Shift settings; defaults to the config file's.
            ignore_sessions: Do not wait for open `puffin` TUI sessions (for testing beside one;
                requests from them still block a start).

        Returns:
            int: 0 when the run completed (whatever its tasks did), 1 when it could not run.
        """
        settings = settings or NightShiftSettings()
        night_dir = night_dir or NightShiftQueue.night_dir()
        started = datetime.now().astimezone()
        end = cls.window_end(started, until, minutes, settings.window)
        if puffin_bin is None:
            from dreamference.runner.codex_installer import CodexInstaller
            puffin_bin = os.environ.get("PUFFIN_NIGHT_PUFFIN_BIN") or CodexInstaller.get_codex_executable()
        if not puffin_bin:
            print("❌ puffin is not built: run `puffin-admin codex build` first.")
            return 1
        if vllm_host is None:
            from dreamference.config import DreamferenceConfig
            vllm_host = DreamferenceConfig().vllm_host

        with NightShiftQueue.runner_lock(night_dir) as held:
            if not held:
                print("⚠️  Another night run holds the runner lock.")
                return 1
            pending = cls.pending_tasks(night_dir)
            if not pending:
                print("✅ Night Shift: nothing queued.")
                return 0
            print(f"🌙 Night Shift: {len(pending)} task(s) queued; window ends {end:%H:%M}.")
            notes: List[str] = []
            idle = settings.idle_minutes if idle_minutes is None else idle_minutes
            cls.ignore_sessions = ignore_sessions
            reason = cls.admit(vllm_host, puffin_bin, idle, end)
            if reason:
                notes.append(f"Not run: {reason}. The tasks stay queued for the next night.")
                print(f"⚠️  {reason}")
                cls._write_report(night_dir, started, pending, notes)
                return 0
            metrics = cls.host.metrics(vllm_host) or {}
            parallel = cls.host.parallelism(settings.max_parallel, metrics.get("kv_pool", 0.0),
                                            settings.task_context)
            notes.append(f"Up to {parallel} task(s) at once (max_parallel {settings.max_parallel}, "
                         f"KV pool {int(metrics.get('kv_pool', 0))} tokens, "
                         f"{settings.task_context} budgeted per task).")
            cls.schedule(night_dir, pending, settings, puffin_bin, vllm_host, end, parallel, notes)
            final = [NightShiftQueue.read(night_dir, task["id"]) or task for task in pending]
            path = cls._write_report(night_dir, started, final, notes)
            counts: Dict[str, int] = {}
            for task in final:
                counts[task.get("status", "?")] = counts.get(task.get("status", "?"), 0) + 1
            summary = ", ".join(f"{count} {status}" for status, count in counts.items())
            print(f"✅ Night Shift finished: {summary}. Report: {path}")
            return 0

    @classmethod
    def admit(cls, vllm_host: str, puffin_bin: str, idle_minutes: float, end: datetime) -> Optional[str]:
        """
        The admission checks of §5.2, in order; the first failure is the reason the night does
        not run.

        Returns:
            Optional[str]: The reason, or None when the run may start.
        """
        if cls.host.served_model(vllm_host) is None:
            return f"the model server at {vllm_host} is not answering (the night run never starts it)"
        if not cls.host.host_safety_ok():
            return "the host-safety checks failed (see `puffin-admin server start` for the details)"
        available = cls.host.mem_available_bytes()
        if available < MEMORY_RESERVE:
            return f"only {available / GIB:.1f} GiB of memory is available; a night run needs {MEMORY_RESERVE // GIB}"
        heavy = cls.host.heavy_jobs()
        if heavy:
            return "; ".join(heavy)
        return cls.wait_for_idle(vllm_host, puffin_bin, idle_minutes, end)

    @classmethod
    def wait_for_idle(cls, vllm_host: str, puffin_bin: str, idle_minutes: float,
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
            if not cls.ignore_sessions and cls.host.interactive_puffin_pids(puffin_bin):
                return "a puffin session is open (interactive use wins)"
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
    def schedule(cls, night_dir: Path, pending: List[Dict[str, Any]], settings: NightShiftSettings,
                 puffin_bin: str, vllm_host: str, end: datetime, parallel: int, notes: List[str]) -> None:
        """
        Starts tasks while the window is open, the machine is quiet and memory admits one more;
        at the window's end asks every running task to stop (it is then `interrupted`).
        """
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
            if queue and len(active) < parallel:
                reason = cls.start_blocker(vllm_host, puffin_bin, active, settings)
                if reason is None:
                    task = queue.pop(0)
                    deadline = min(end_ts, time.time() + settings.task_timeout_s)
                    run = NightShiftTaskRun(night_dir, task, settings, puffin_bin, deadline)
                    thread = threading.Thread(target=run.run, name=f"night-{task['id']}", daemon=True)
                    thread.start()
                    active.append((thread, run))
                    paused_for = None
                    continue
                if reason != paused_for:
                    notes.append(f"{datetime.now():%H:%M}: waiting to start the next task: {reason}.")
                    paused_for = reason
            cls.sleep(POLL_S)

    @classmethod
    def start_blocker(cls, vllm_host: str, puffin_bin: str, active: List[tuple],
                      settings: NightShiftSettings) -> Optional[str]:
        """
        Whether another task may start now (§5.5 and memory).

        An outside request is one beyond the night's own: the engine's running and queued
        requests exceed the number of night tasks currently waiting on the model.

        Returns:
            Optional[str]: What blocks a start, or None.
        """
        if not cls.ignore_sessions and cls.host.interactive_puffin_pids(puffin_bin):
            return "a puffin session is open"
        metrics = cls.host.metrics(vllm_host)
        if metrics is None:
            return "the model server's /metrics does not answer"
        ours = sum(1 for _, run in active if run.in_model)
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
