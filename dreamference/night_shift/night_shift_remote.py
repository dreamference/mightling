"""
Night tasks worked by another node (specs/DREAMFERENCE_MIGHTLING_NODE.md §13.3, §13.5, §13.7).

`/night add --on <node>` records the node with the task. At night this machine's runner does not
run it: it pushes the task's base commit to that node's job repository over the pairing and asks
the node to queue the task in its own Night Shift queue. The node's own runner then works it with
its own `ling exec` against its own model server over loopback, inside a sandbox that hides the
node owner's home folder and gives the task a `CODEX_HOME` of its own. When the node has finished
it, the branch `night/<id>` is fetched back here and the morning report lists the task with the
node it ran on.

On this machine a handed-over task is `sent` until its result comes back; a `/night drop` of it is
forwarded to the node. Nothing is merged: the branch arrives as a branch.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.night_shift.night_shift_queue import FINAL_STATUSES, NightShiftQueue

# How often a waiting run asks the nodes about the tasks it sent.
POLL_S: Final[float] = 120.0

# The most a task's text may be, as sent to a node.
MAX_TASK_CHARS: Final[int] = 20_000

# How long a node keeps a finished task from another machine: a day after the sender fetched it,
# 14 days when nobody did (the jobs' rule, §13.7).
KEEP_AFTER_FETCH: Final[timedelta] = timedelta(days=1)
KEEP_UNFETCHED: Final[timedelta] = timedelta(days=14)

# Statuses on this machine of a task that another node holds.
SENT_STATUSES: Final[tuple] = ("sent", "cancel-requested")


class NightShiftRemote:
    """Both halves: the sender's hand-off and collection, and the node's `night-*` operations."""

    # Seam: the tests replace the clock's sleep.
    sleep = staticmethod(time.sleep)

    # -- the sender ------------------------------------------------------------------------------

    @classmethod
    def sent_tasks(cls, night_dir: Path) -> List[Dict[str, Any]]:
        """
        Args:
            night_dir: The queue directory.

        Returns:
            List[Dict[str, Any]]: Tasks handed to another node whose result has not come back.
        """
        return [task for task in NightShiftQueue.tasks(night_dir)
                if task.get("on") and task.get("status") in SENT_STATUSES]

    @classmethod
    def hand_off(cls, night_dir: Path, pending: List[Dict[str, Any]], notes: List[str]) -> List[Dict[str, Any]]:
        """
        Hands every pending task queued with `--on` to its node.

        Args:
            night_dir: The queue directory.
            pending: The night's runnable tasks.
            notes: The morning report's notes, appended to.

        Returns:
            List[Dict[str, Any]]: The tasks now held by another node.
        """
        from dreamference.node.node_job_sender import NodeJobSender
        from dreamference.node.node_pairing import NodePairing
        sent = []
        for task in [task for task in pending if task.get("on")]:
            record = NodePairing.find(task["on"])
            if record is None:
                NightShiftQueue.transition(night_dir, task["id"], "failed",
                                           note=f"{task['on']} is not a paired node",
                                           result={"last_message": f"{task['on']} is not a paired node: pair once "
                                                                   f"with `ling-admin node add {task['on']}`, then "
                                                                   f"queue the task again."})
                continue
            repo = task["repo"]
            slug = NodeJobSender.repo_slug(repo)
            pushed = subprocess.run(
                ["git", "-C", repo, "push", "--quiet", NodeJobSender.git_url(record, slug),
                 f"{task['base']}:refs/night/{task['id']}"],
                env=NodeJobSender.git_environment(record), capture_output=True, text=True, check=False)
            if pushed.returncode != 0:
                notes.append(f"{task['id']}: could not reach {record['name']} ({pushed.stderr.strip()[-200:]}); "
                             f"the task stays queued for it.")
                continue
            request = cls.request_for(night_dir, task, slug)
            payload = base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
            answer = NodePairing.run(record, f"night-submit {payload}")
            reply = cls.json_line(answer.stdout) or {}
            if answer.returncode == 255:
                notes.append(f"{task['id']}: {record['name']} did not answer; the task stays queued for it.")
                continue
            if answer.returncode != 0:
                reason = reply.get("error") or answer.stderr.strip()[-300:] or "refused"
                NightShiftQueue.transition(night_dir, task["id"], "failed", note=f"{record['name']} refused it",
                                           result={"last_message": f"{record['name']} refused the task: {reason}",
                                                   "node": record["name"]})
                continue
            fields = {"sent_to": {"node": record["node"], "name": record["name"], "slug": slug}}
            NightShiftQueue.transition(night_dir, task["id"], "sent", note=f"handed to {record['name']}", **fields)
            if reply.get("note"):
                notes.append(f"{task['id']} on {record['name']}: {reply['note']}")
            sent.append(dict(task, status="sent", **fields))
        return sent

    @classmethod
    def request_for(cls, night_dir: Path, task: Dict[str, Any], slug: str) -> Dict[str, Any]:
        """
        Builds what is sent to the node for one task.

        Args:
            night_dir: The queue directory.
            task: The task as queued here.
            slug: Its repository's name on the node.

        Returns:
            Dict[str, Any]: The request: the task, its base commit and test, the `/airgapped`
            level it runs at here (the node applies the stricter of that and its own), and the
            git identity the node commits the result as.
        """
        from dreamference.night_shift.night_shift_task_run import NightShiftTaskRun
        from dreamference.node.node_identity import NodeIdentity
        repo = Path(task["repo"])
        level = NightShiftTaskRun.airgapped_level(repo, None, night_dir.parent, repo)
        git = lambda key: subprocess.run(["git", "-C", str(repo), "config", key], capture_output=True,
                                         text=True, check=False).stdout.strip()
        return {"id": task["id"], "repo": slug, "base": task["base"], "task": task["task"], "test": task.get("test"),
                "airgapped": level, "author": {"name": git("user.name"), "email": git("user.email")},
                "sender": NodeIdentity.read() or "", "sender_name": socket.gethostname()}

    @classmethod
    def collect(cls, night_dir: Path, sent: List[Dict[str, Any]], end: datetime, notes: List[str],
                wait: bool) -> None:
        """
        Asks each node about the tasks sent to it, forwards a `/night drop`, and brings back
        every finished task's branch and result.

        Args:
            night_dir: The queue directory.
            sent: The tasks other nodes hold.
            end: When this run's window closes.
            notes: The morning report's notes, appended to.
            wait: Keep asking, every two minutes, until each has finished or the window closes.
        """
        from dreamference.node.node_pairing import NodePairing
        waiting = [task["id"] for task in sent]
        silent: set = set()
        while waiting:
            for task_id in list(waiting):
                task = NightShiftQueue.read(night_dir, task_id)
                if task is None or task.get("status") not in SENT_STATUSES:
                    waiting.remove(task_id)
                    continue
                target = task.get("sent_to") or {}
                record = NodePairing.find(target.get("node") or task.get("on", ""), browse=False)
                if record is None:
                    waiting.remove(task_id)
                    notes.append(f"{task_id}: {target.get('name') or task.get('on')} is no longer paired, so its "
                                 f"result cannot be fetched.")
                    continue
                if task.get("status") == "cancel-requested":
                    NodePairing.run(record, f"night-cancel {task_id}")
                answer = NodePairing.run(record, f"night-status {task_id}")
                status = cls.json_line(answer.stdout)
                if answer.returncode != 0 or status is None:
                    if record["name"] not in silent:
                        notes.append(f"{record['name']} did not answer about its tasks; they stay sent.")
                        silent.add(record["name"])
                    waiting.remove(task_id)
                    continue
                if status.get("status") in FINAL_STATUSES:
                    cls._bring_back(night_dir, task, record, status)
                    waiting.remove(task_id)
            if not wait or not waiting or datetime.now().astimezone() >= end:
                break
            cls.sleep(min(POLL_S, max(1.0, end.timestamp() - time.time())))

    @classmethod
    def _bring_back(cls, night_dir: Path, task: Dict[str, Any], record: Dict[str, Any],
                    status: Dict[str, Any]) -> None:
        """Fetches a finished task's branch and copies its result into this machine's record."""
        from dreamference.node.node_job_sender import NodeJobSender
        from dreamference.node.node_pairing import NodePairing
        result = dict(status.get("result") or {})
        result["node"] = record["name"]
        branch = result.get("branch")
        if branch:
            slug = (task.get("sent_to") or {}).get("slug") or NodeJobSender.repo_slug(task["repo"])
            fetched = subprocess.run(
                ["git", "-C", task["repo"], "fetch", "--quiet", NodeJobSender.git_url(record, slug),
                 f"+refs/heads/{branch}:refs/heads/{branch}"],
                env=NodeJobSender.git_environment(record), capture_output=True, text=True, check=False)
            if fetched.returncode != 0:
                result.pop("branch", None)
                result["last_message"] = (f"the task finished on {record['name']}, but its branch could not be "
                                          f"fetched: {fetched.stderr.strip()[-200:]}")
                NightShiftQueue.transition(night_dir, task["id"], "sent", note="fetch failed", result=result)
                return
            stat = subprocess.run(["git", "-C", task["repo"], "diff", "--stat", f"{task['base']}..{branch}"],
                                  capture_output=True, text=True, check=False).stdout.rstrip()
            if stat:
                result["diff_stat"] = stat
        NodePairing.run(record, f"night-fetched {task['id']}")
        NightShiftQueue.transition(night_dir, task["id"], status["status"], note=f"came back from {record['name']}",
                                   result=result)

    @classmethod
    def json_line(cls, text: str) -> Optional[Dict[str, Any]]:
        """
        Args:
            text: A node's answer.

        Returns:
            Optional[Dict[str, Any]]: Its last JSON object line, or None.
        """
        for line in reversed((text or "").splitlines()):
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    return None
                return parsed if isinstance(parsed, dict) else None
        return None

    # -- the node ----------------------------------------------------------------------------------

    @classmethod
    def serve(cls, operation: str, arguments: List[str]) -> int:
        """
        Carries out a night request from a paired node: submit, status, cancel, fetched.

        Args:
            operation: `night-submit`, `night-status`, `night-cancel` or `night-fetched`.
            arguments: Its arguments.

        Returns:
            int: 0, 1 when there is no such task, 2 for a refused request.
        """
        from dreamference.node.node_job import JOB_ID
        from dreamference.node.node_serve import REFUSAL
        if operation == "night-submit" and len(arguments) == 1:
            try:
                request = json.loads(base64.urlsafe_b64decode(arguments[0].encode()))
                reply = cls.accept(request)
            except (ValueError, TypeError) as error:
                print(json.dumps({"error": str(error)}))
                return 2
            print(json.dumps(reply))
            return 0
        if operation in ("night-status", "night-cancel", "night-fetched") and len(arguments) == 1 \
                and JOB_ID.fullmatch(arguments[0]):
            task = NightShiftQueue.read(NightShiftQueue.night_dir(), arguments[0])
            if task is None or not task.get("remote"):
                print(json.dumps({"error": "no such task from another node"}))
                return 1
            if operation == "night-status":
                print(json.dumps({"status": task.get("status"), "result": task.get("result"),
                                  "attempts": task.get("attempts")}))
                return 0
            if operation == "night-cancel":
                print(json.dumps({"status": cls.cancel(arguments[0])}))
                return 0
            NightShiftQueue.update(NightShiftQueue.night_dir(), arguments[0],
                                   lambda record: record.setdefault("fetched", NightShiftQueue.now()))
            print(json.dumps({"status": task.get("status")}))
            return 0
        print(REFUSAL, file=sys.stderr)
        return 2

    @classmethod
    def accept(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Queues a task from another machine in this node's own Night Shift queue. Everything here
        came from another machine.

        Args:
            request: What the sender built (`request_for`).

        Returns:
            Dict[str, Any]: `status`, and a `note` saying when the task will be worked.

        Raises:
            ValueError: With the reason, when the task cannot be accepted.
        """
        from dreamference.node.node_job import COMMIT, JOB_ID, NodeJob
        if not isinstance(request, dict):
            raise ValueError("a task is a JSON object")
        task_id = str(request.get("id") or "")
        if not JOB_ID.fullmatch(task_id):
            raise ValueError("not a task id")
        repo = NodeJob.repo_path(str(request.get("repo") or ""))
        base = str(request.get("base") or "")
        if not COMMIT.fullmatch(base):
            raise ValueError("a task names its base commit as a full hash")
        text = request.get("task")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TASK_CHARS:
            raise ValueError(f"a task is text of at most {MAX_TASK_CHARS} characters")
        test = request.get("test")
        if test is not None and not isinstance(test, str):
            raise ValueError("the test command is a string")
        known = subprocess.run(["git", "--git-dir", str(repo), "cat-file", "-e", f"{base}^{{commit}}"],
                               capture_output=True, text=True, check=False)
        if known.returncode != 0:
            raise ValueError("the base commit was not pushed to this node before the task was sent")
        # Refused now rather than failed at night: code from another machine never runs unsandboxed.
        blocked = NodeJob.sandbox_blocker()
        if blocked:
            raise ValueError(blocked)
        night_dir = NightShiftQueue.night_dir()
        tasks_dir = night_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        path = tasks_dir / f"{task_id}.json"
        if path.exists():
            raise ValueError(f"a task {task_id} already exists on this node; queue it again for a new id")
        author = request.get("author") if isinstance(request.get("author"), dict) else {}
        now = NightShiftQueue.now()
        record = {
            "id": task_id, "repo": str(repo), "base": base, "branch": f"night/{task_id}", "task": text,
            "test": test or None, "model_at_add": None, "status": "queued",
            "history": [{"at": now, "status": "queued", "note": f"from {str(request.get('sender_name') or '?')[:80]}"}],
            "attempts": 0, "nudges": 0, "session": None, "result": None,
            "airgapped": NodeJob.stricter(str(request.get("airgapped") or "off"), NodeJob.node_airgap_level()),
            "author": {"name": str(author.get("name") or "Mightling Night Shift")[:100],
                       "email": str(author.get("email") or "mightling-night@localhost")[:200]},
            "remote": {"sender": str(request.get("sender") or "")[:100],
                       "sender_name": str(request.get("sender_name") or "")[:100]},
        }
        staging = tasks_dir / f".{task_id}.{os.getpid()}.tmp"
        staging.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        try:
            os.link(staging, path)
        except FileExistsError:
            raise ValueError(f"a task {task_id} already exists on this node") from None
        finally:
            staging.unlink(missing_ok=True)
        cls.prune()
        return {"status": "queued", "note": cls.start_runner()}

    @classmethod
    def start_runner(cls) -> str:
        """
        Starts this node's night run now if its window is open and nothing holds its runner lock,
        so a task handed over during the sender's night is worked the same night.

        Returns:
            str: What will happen to the task, for the sender's report.
        """
        from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler, UNIT
        from dreamference.night_shift.night_shift_settings import NightShiftSettings
        if not (NightShiftScheduler.unit_dir() / f"{UNIT}.timer").exists():
            return ("Night Shift is not enabled on this node (`ling-admin night enable` there), so the task "
                    "waits until a night run is started there")
        if NightShiftQueue.runner_active():
            return f"{NightShiftQueue.runner_holder() or 'a run'} is in progress there; the task waits for the next night"
        try:
            start, end = NightShiftSettings.parse_window(NightShiftSettings().window)
        except ValueError:
            return "this node's Night Shift window could not be read; the task waits for its next night run"
        now = datetime.now().time()
        inside = (start <= now < end) if start <= end else (now >= start or now < end)
        if not inside:
            return "outside this node's window; the task is worked in its next night run"
        from dreamference.node.node_job import NodeJob
        # Reached through a forced command, which has no session: the user's bus is found as a
        # job's unit finds it.
        started = subprocess.run(["systemctl", "--user", "start", "--no-block", f"{UNIT}.service"],
                                 capture_output=True, text=True, env=NodeJob.user_bus_environment(), check=False)
        if started.returncode != 0:
            return f"could not start this node's night run ({started.stderr.strip()[-120:]}); the task waits"
        return "this node's night run was started for it"

    @classmethod
    def cancel(cls, task_id: str) -> str:
        """
        Args:
            task_id: A task from another machine.

        Returns:
            str: Its status after the request: `cancelled`, `cancel-requested`, or what it was.
        """
        def change(record: Dict[str, Any]) -> str:
            if record.get("status") in ("queued", "interrupted"):
                NightShiftQueue.set_status(record, "cancelled", "dropped by the sender")
            elif record.get("status") == "running":
                NightShiftQueue.set_status(record, "cancel-requested", "dropped by the sender")
            return record["status"]
        return NightShiftQueue.update(NightShiftQueue.night_dir(), task_id, change)

    @classmethod
    def task_home(cls, task_id: str) -> Path:
        """
        Args:
            task_id: A task from another machine.

        Returns:
            Path: Its own `CODEX_HOME` on this node, `~/.mightling/jobs/<id>/home`: the only part of
            the home folder its `ling exec` sees, kept across the nudges and nights of the task.
        """
        from dreamference.node.node_job import NodeJob
        return NodeJob.job_dir(task_id) / "home"

    @classmethod
    def prune(cls, now: Optional[datetime] = None) -> List[str]:
        """
        Removes finished tasks from other machines that are past their keep: the record, its
        logs, its `CODEX_HOME`, the branch and the pushed ref.

        Args:
            now: The time to compare with; the current time by default.

        Returns:
            List[str]: The ids removed.
        """
        import shutil
        from dreamference.node.node_job import NodeJob
        now = now or datetime.now().astimezone()
        night_dir = NightShiftQueue.night_dir()
        removed = []
        for task in NightShiftQueue.tasks(night_dir):
            if not task.get("remote") or task.get("status") not in FINAL_STATUSES:
                continue
            last = (task.get("history") or [{}])[-1].get("at")
            try:
                keep_from = datetime.fromisoformat(task.get("fetched") or last)
            except (TypeError, ValueError):
                continue
            if keep_from + (KEEP_AFTER_FETCH if task.get("fetched") else KEEP_UNFETCHED) > now:
                continue
            repo = task.get("repo") or ""
            if os.path.isdir(repo):
                for ref in (f"refs/heads/{task.get('branch')}", f"refs/night/{task['id']}"):
                    subprocess.run(["git", "--git-dir", repo, "update-ref", "-d", ref], capture_output=True, check=False)
            for path in (night_dir / "tasks" / f"{task['id']}.json", night_dir / "tasks" / f"{task['id']}.lock"):
                path.unlink(missing_ok=True)
            for log in (night_dir / "logs").glob(f"{task['id']}.*") if (night_dir / "logs").is_dir() else []:
                log.unlink(missing_ok=True)
            shutil.rmtree(NodeJob.job_dir(task["id"]), ignore_errors=True)
            removed.append(task["id"])
        return removed
