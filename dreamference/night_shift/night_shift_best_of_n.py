"""
Best-of-N for a Night Shift task (specs/DREAMFERENCE_BACKLOG.md item 6;
specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §11.9).

A task is attempted N times (`[night] best_of`, 3 by default; a task's own `best_of` outranks it),
one attempt after another inside the task's one slot, so the scheduler's memory and request
accounting stays one task, one scope. Each attempt is a whole `NightShiftTaskRun` in its own
worktree on `night/<id>-a<k>`, with its own session, test run and time limit (`task_timeout`, never
past the window). Chooser v1 (`NightShiftChooser`) then picks one: attempts whose test run failed
or that ran out of time are dropped unless every one did, and the survivors are judged in pairs by
the model server over the task's text and their diffs. The runner commits only the chosen attempt,
which becomes `night/<id>`; the others' worktrees and branches are removed and their diffs kept as
`logs/<id>-a<k>.diff` for the report. Nothing is merged, pushed or rebased.

Rules kept on purpose:
- A task resumed from an earlier night (a recorded session), a task from another machine
  (`remote`) and `best_of = 1` run as one attempt, exactly as before.
- Attempts are never resumed. When the window closes, the attempts that finished are chosen
  among and the report says how many ran; with none finished the task is `interrupted` and starts
  over the next night.
- With refine mode on, every attempt studies the task in its own session first.
- A judge call that fails counts as unreadable (a tie at the floor), so a judge fault never stops
  the night; a tie at the top goes to the attempt whose tests passed, then the earliest.
"""

import hashlib
import json
import os
import random
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dreamference.night_shift.chooser_candidate import ChooserCandidate
from dreamference.night_shift.night_shift_chooser import Compare, NightShiftChooser
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_settings import NightShiftSettings
from dreamference.night_shift.night_shift_task_run import NightShiftTaskRun

# Statuses of an attempt that ended without a candidate, worst last: what the task becomes when no
# attempt left a change (`failed` only when every attempt failed).
NO_CANDIDATE_ORDER = ("stalled", "no-change", "timed-out", "failed")


class NightShiftBestOfN:
    """Runs one task as N attempts and commits the one chooser v1 picks; `run()` returns its status."""

    # The judge factory, (server, issue, seed, log) -> Compare. Tests replace it: nothing in the
    # suite may reach a model server.
    JUDGE: Optional[Callable[..., Compare]] = None

    def __init__(self, night_dir: Path, task: Dict[str, Any], settings: NightShiftSettings,
                 mightling_bin: str, deadline: float, best_of: int, model_host: Optional[str] = None,
                 context_budget: Optional[int] = None, model_node: Optional[str] = None,
                 window_end: Optional[float] = None) -> None:
        """
        Args:
            night_dir: The queue directory.
            task: The task record as queued.
            settings: Night Shift settings.
            mightling_bin: The `ling` executable.
            deadline: `time.time()` by which the first attempt must stop.
            best_of: How many attempts.
            model_host: The model server the night run was admitted against; the attempts and the
                judge use it.
            context_budget: The task's share of the KV pool (each attempt's compaction limit).
            model_node: The paired node whose server `model_host` is, when it is not this machine's.
            window_end: `time.time()` at which the night's window closes; no attempt starts after
                it, and none runs past it.
        """
        self.night_dir = night_dir
        self.task = dict(task)
        self.task_id: str = task["id"]
        self.repo = Path(task["repo"])
        self.base: str = task["base"]
        self.branch: str = task.get("branch") or f"night/{self.task_id}"
        self.text: str = task["task"]
        self.settings = settings
        self.mightling_bin = mightling_bin
        self.deadline = deadline
        self.best_of = max(1, best_of)
        self.model_host = model_host
        self.context_budget = context_budget
        self.model_node = model_node
        self.window_end = window_end
        self.stop_event = threading.Event()
        # The model server the scheduler counts this task against (set by the runner).
        self.lane_host: Optional[str] = None
        self.current: Optional[NightShiftTaskRun] = None
        self.judging = False
        self.attempts: int = int(task.get("attempts") or 0)
        self.started = time.time()

    # -- what the scheduler reads ------------------------------------------------------------

    @property
    def in_model(self) -> bool:
        """Whether the task is waiting on the model: an attempt's `ling exec`, or the judge."""
        return self.judging or bool(self.current is not None and self.current.in_model)

    @property
    def current_unit(self) -> Optional[str]:
        """The running attempt's scope, whose memory the scheduler counts."""
        return self.current.current_unit if self.current is not None else None

    @classmethod
    def attempts_for(cls, task: Dict[str, Any], settings: NightShiftSettings,
                     night_dir: Optional[Path] = None) -> int:
        """
        How many attempts a task gets.

        Args:
            task: The task record.
            settings: Night Shift settings.
            night_dir: The queue directory, where a worktree kept from an earlier run would be.

        Returns:
            int: 1 for a resumed task, a task from another machine or a worktree kept from an
            earlier single run; else the task's `best_of`, then `[night] best_of`.
        """
        if task.get("session") or isinstance(task.get("remote"), dict):
            return 1
        if night_dir is not None and (night_dir / "worktrees" / str(task.get("id"))).is_dir():
            return 1
        try:
            wanted = int(task.get("best_of") or getattr(settings, "best_of", 1))
        except (TypeError, ValueError):
            wanted = int(getattr(settings, "best_of", 1))
        return max(1, wanted)

    @classmethod
    def task_run(cls, night_dir: Path, task: Dict[str, Any], settings: NightShiftSettings,
                 mightling_bin: str, deadline: float, **kwargs: Any) -> Any:
        """
        The run the scheduler starts for a task: a plain `NightShiftTaskRun` for one attempt, this
        class for more.

        Args:
            night_dir: The queue directory.
            task: The task record.
            settings: Night Shift settings.
            mightling_bin: The `ling` executable.
            deadline: When the (first) attempt must stop.
            **kwargs: `model_host`, `context_budget`, `model_node`, `window_end`.

        Returns:
            Any: An object with `run()`, `stop_event`, `in_model`, `current_unit` and `lane_host`.
        """
        n = cls.attempts_for(task, settings, night_dir)
        if n <= 1:
            return NightShiftTaskRun(night_dir, task, settings, mightling_bin, deadline, **kwargs)
        return cls(night_dir, task, settings, mightling_bin, deadline, best_of=n, **kwargs)

    # -- the run -----------------------------------------------------------------------------

    def run(self) -> str:
        """
        Runs the attempts, chooses, commits the chosen one.

        Returns:
            str: The status written.
        """
        if self._cancelled():
            return NightShiftQueue.transition(self.night_dir, self.task_id, "cancelled")
        self.attempts += 1
        NightShiftQueue.transition(self.night_dir, self.task_id, "running", attempts=self.attempts)
        outcomes: List[Dict[str, Any]] = []
        try:
            return self._run(outcomes)
        except Exception as error:  # A runner bug must not leave the task `running` forever.
            self._discard(outcomes)
            return self._finish("failed", note=f"night run error: {error}",
                                result={**self._timing(outcomes), "last_message": f"night run error: {error}"})

    def _window_over(self) -> bool:
        return self.stop_event.is_set() or (self.window_end is not None and time.time() >= self.window_end)

    def _attempt_deadline(self, number: int) -> float:
        if number == 1:
            return self.deadline
        limit = time.time() + self.settings.task_timeout_s
        return min(self.window_end, limit) if self.window_end is not None else limit

    def _run(self, outcomes: List[Dict[str, Any]]) -> str:
        for number in range(1, self.best_of + 1):
            if self._window_over():
                break
            if self._cancelled():
                self._discard(outcomes)
                return self._finish("cancelled", result=self._timing(outcomes))
            attempt = NightShiftTaskRun(self.night_dir, self.task, self.settings, self.mightling_bin,
                                        self._attempt_deadline(number), model_host=self.model_host,
                                        context_budget=self.context_budget, model_node=self.model_node,
                                        window_end=self.window_end, attempt=number)
            attempt.stop_event = self.stop_event
            attempt.lane_host = self.lane_host
            self.current = attempt
            try:
                status = attempt.run()
            finally:
                self.current = None
            outcome = attempt.outcome or {"attempt": number, "status": status, "result": {}}
            outcome["paths"] = (str(attempt.worktree), attempt.branch)
            outcomes.append(outcome)
            if status == "cancelled":
                self._discard(outcomes)
                return self._finish("cancelled", result=self._timing(outcomes))
            if status == "interrupted":
                break

        candidates = [o for o in outcomes if o["status"] == "candidate"]
        # What the window cut off is never resumed.
        self._discard([o for o in outcomes if o["status"] == "interrupted"])
        if not candidates:
            return self._no_candidate(outcomes)
        chosen, choice = self._choose(candidates)
        return self._commit(chosen, choice, outcomes)

    def _no_candidate(self, outcomes: List[Dict[str, Any]]) -> str:
        """No attempt left a change: the task ends as the attempts did, or `interrupted` by the window."""
        # An attempt that ran out of time or failed on its own may have left its worktree and branch.
        self._discard(outcomes)
        result = self._timing(outcomes)
        last = next((o["result"].get("last_message") for o in reversed(outcomes)
                     if o["result"].get("last_message")), None)
        if any(o["status"] == "interrupted" for o in outcomes) or not outcomes:
            if last:
                result["last_message"] = last
            result["note"] = "the window closed before any attempt finished; the task starts over next night"
            return self._finish("interrupted", result=result)
        statuses = {o["status"] for o in outcomes}
        status = "failed" if statuses == {"failed"} else next(
            (s for s in NO_CANDIDATE_ORDER if s in statuses), "failed")
        status = "no-change" if status == "timed-out" else status
        if last and status in ("stalled", "failed"):
            result["last_message"] = last
        return self._finish(status, result=result)

    def _choose(self, candidates: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Chooser v1 over the candidates.

        Returns:
            Tuple[Dict[str, Any], Dict[str, Any]]: The chosen attempt's outcome, and the choice
            (`stage`, `survivors`, `points`, `chosen`, `judge_calls`, `judge_errors`).
        """
        by_id: Dict[str, Dict[str, Any]] = {}
        pool: List[ChooserCandidate] = []
        for outcome in candidates:
            cid = f"a{outcome['attempt']}"
            by_id[cid] = outcome
            result = outcome["result"]
            try:
                patch = Path(result["diff"]).read_text(errors="replace")
            except (KeyError, OSError):
                patch = ""
            pool.append(ChooserCandidate(id=cid, patch=patch, runs=[cid], p2p_failed=self.regression(result)))
        log: List[Dict[str, Any]] = []
        seed = int(hashlib.sha256(self.task_id.encode()).hexdigest()[:8], 16)
        survivors = [c for c in pool if not c.breaks] or pool
        skipped: Optional[str] = None
        if len(pool) > 1 and len(survivors) > 1 and self._window_over():
            skipped = "judge skipped: the window had closed"
            compare: Compare = lambda a, b: None
        elif len(pool) > 1 and len(survivors) > 1 and not self.model_host and self.JUDGE is None:
            skipped = "judge skipped: no model server"
            compare = lambda a, b: None
        else:
            factory = self.JUDGE or NightShiftChooser.server_judge
            compare = factory(self.model_host or "", self.text, seed=seed, log=log)
        self.judging = True
        try:
            choice = NightShiftChooser.choose(pool, compare, random.Random(self.task_id))
        finally:
            self.judging = False
        if skipped:
            choice["stage"] = skipped
        if log:
            path = self.night_dir / "logs" / f"{self.task_id}.judge.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as handle:
                for record in log:
                    handle.write(json.dumps(record) + "\n")
            choice["judge_log"] = str(path)
        choice["judge_calls"] = len(log)
        choice["judge_errors"] = sum(1 for record in log if record.get("error") or record.get("ratings") is None)
        breaks = {c.id: c.breaks for c in pool}
        top = choice["chosen"] or [pool[0].id]
        chosen_id = min(top, key=lambda cid: (breaks[cid], int(cid[1:])))
        choice["chosen_attempt"] = int(chosen_id[1:])
        return by_id[chosen_id], choice

    @classmethod
    def regression(cls, result: Dict[str, Any]) -> Optional[int]:
        """
        The regression filter's input from an attempt's result: 0 when its tests passed, 1 when
        they failed or timed out or the attempt ran out of time, None when it was not tested.
        """
        if result.get("timed_out"):
            return 1
        verdict = str(result.get("test_result") or "untested")
        if verdict == "passed":
            return 0
        if verdict.startswith("failed") or verdict.startswith("timed out"):
            return 1
        return None

    def _commit(self, chosen: Dict[str, Any], choice: Dict[str, Any], outcomes: List[Dict[str, Any]]) -> str:
        """Commits the chosen attempt as `night/<id>`; removes every attempt's worktree and the others' branches."""
        result = dict(chosen["result"])
        worktree, branch = chosen["paths"]
        first_line = (self.text.strip().splitlines() or ["task"])[0][:72]
        code, output = self._git(Path(worktree), "commit", "-q", "-m", f"night: {first_line}")
        if code != 0:
            self._discard(outcomes)
            return self._finish("failed", note=f"git commit failed: {output.strip()[-300:]}",
                                result={**result, **self._timing(outcomes), "best_of": self._record(choice, outcomes)})
        self._discard([o for o in outcomes if o is not chosen])
        self._git(self.repo, "worktree", "remove", "--force", worktree)
        code, output = self._git(self.repo, "branch", "-M", branch, self.branch)
        final = self.branch if code == 0 else branch
        for key in ("worktree", "session", "diff", "timed_out"):
            result.pop(key, None)
        result.update(self._timing(outcomes))
        result["branch"] = final
        result["diff_stat"] = self._git(self.repo, "diff", "--stat", f"{self.base}..{final}")[1].rstrip()
        result["best_of"] = self._record(choice, outcomes)
        return self._finish("done", result=result)

    def _record(self, choice: Dict[str, Any], outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The result's `best_of` record: what ran, what was chosen and why, and every attempt."""
        attempts = []
        for outcome in outcomes:
            result = outcome["result"]
            entry: Dict[str, Any] = {"attempt": outcome["attempt"], "status": outcome["status"],
                                     "wall_s": result.get("wall_s")}
            for key in ("test_result", "diff", "timed_out"):
                if result.get(key) is not None:
                    entry[key] = result[key]
            if result.get("diff_stat"):
                entry["changes"] = result["diff_stat"].splitlines()[-1].strip()
            attempts.append(entry)
        return {"n": self.best_of, "ran": len(outcomes), "chosen": choice.get("chosen_attempt"),
                "stage": choice.get("stage"), "points": choice.get("points") or {},
                "survivors": choice.get("survivors") or [], "judge_calls": choice.get("judge_calls", 0),
                "judge_errors": choice.get("judge_errors", 0), "judge_log": choice.get("judge_log"),
                "attempts": attempts}

    # -- helpers -----------------------------------------------------------------------------

    def _discard(self, outcomes: List[Dict[str, Any]]) -> None:
        """Removes these attempts' worktrees and branches (the runner's own: nothing in them is the user's)."""
        for outcome in outcomes:
            paths = outcome.get("paths")
            if not paths:
                continue
            worktree, branch = paths
            if Path(worktree).is_dir():
                self._git(self.repo, "worktree", "remove", "--force", worktree)
            self._git(self.repo, "branch", "-D", branch)

    def _timing(self, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        timing: Dict[str, Any] = {"attempts": self.attempts,
                                  "nudges": sum(int(o["result"].get("nudges") or 0) for o in outcomes),
                                  "wall_s": int(time.time() - self.started)}
        if self.model_node:
            timing["model_node"] = self.model_node
        return timing

    def _finish(self, status: str, note: Optional[str] = None, result: Optional[Dict[str, Any]] = None) -> str:
        return NightShiftQueue.transition(self.night_dir, self.task_id, status, note=note,
                                          result=dict(result or {}), attempts=self.attempts)

    def _cancelled(self) -> bool:
        record = NightShiftQueue.read(self.night_dir, self.task_id) or {}
        return record.get("status") in ("cancel-requested", "cancelled")

    def _git(self, cwd: Path, *args: str) -> Tuple[int, str]:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, env=env)
        return result.returncode, result.stdout + result.stderr
