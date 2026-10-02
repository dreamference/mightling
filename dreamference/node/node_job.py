"""
A job sent to this node, from its record to its result branch
(specs/DREAMFERENCE_PUFFIN_NODE.md §13).

The job model is Night Shift's, pointed at another machine: the sender pushes a commit, the job
runs in a worktree of it here, and what `git status` shows afterwards is committed on `job/<id>`
for the sender to fetch. Three things differ from a night task on one's own machine:

* **every job has a memory cap and a time limit**; a record without them is not accepted;
* **every job runs inside bubblewrap**: the system read-only, the home folder an empty tmpfs,
  `/run` (and with it the Docker socket) hidden, only the job's worktree writable. Code sent from
  another machine must not see this node's model caches, mail secret or SSH keys;
* **the GPU is hidden**: a cgroup memory cap does not bound GPU memory on a GB10, where it is
  the same pool as host memory, so a job that could reach CUDA would be outside its cap. GPU jobs
  are not allowed for now (decided 2026-10-02).

The job is a systemd user unit, not a child of the sender's SSH connection, so closing the laptop
does not stop it (with lingering on; `puffin-admin node add` says when it is off).
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

JOB_ID: Final[re.Pattern] = re.compile(r"\d{8}-\d{4}-[0-9a-f]{3}")
REPO_SLUG: Final[re.Pattern] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
COMMIT: Final[re.Pattern] = re.compile(r"[0-9a-f]{40}")

DEFAULT_MEMORY: Final[str] = "8G"
DEFAULT_TIME: Final[str] = "90m"

# The most a sender may ask for; this node's ceilings, not the sender's.
MAX_MEMORY_BYTES: Final[int] = 32 * 1024 ** 3
MAX_TIME_S: Final[int] = 8 * 3600

# Free memory a job never eats into, as for a night task.
MEMORY_RESERVE: Final[int] = 8 * 1024 ** 3

AIRGAP_LEVELS: Final[tuple] = ("off", "duckduckgo", "on")

FINAL_STATUSES: Final[tuple] = ("done", "failed", "refused", "cancelled")


class NodeJob:
    """The node's side of one job."""

    # Seam: the tests never start a unit of the user's systemd.
    USE_UNIT: bool = True

    @classmethod
    def jobs_dir(cls) -> Path:
        """
        Returns:
            Path: `$CODEX_HOME/jobs` (`~/.puffin/jobs`): the job repositories and one folder per job.
        """
        codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.puffin")
        return Path(codex_home) / "jobs"

    @classmethod
    def repo_path(cls, slug: str) -> Path:
        """
        Args:
            slug: The repository's name on this node, as the sender derived it.

        Returns:
            Path: The bare repository jobs of that repository are pushed to.

        Raises:
            ValueError: If the slug is not a plain name (it comes from another machine).
        """
        if not REPO_SLUG.fullmatch(slug or "") or ".." in slug:
            raise ValueError(f"not a repository name: {slug!r}")
        return cls.jobs_dir() / "repos" / f"{slug}.git"

    @classmethod
    def job_dir(cls, job_id: str) -> Path:
        """
        Args:
            job_id: The job's id.

        Returns:
            Path: Its folder: `record.json`, `output.log` and, while it runs, `tree/`.

        Raises:
            ValueError: If the id is not a job id.
        """
        if not JOB_ID.fullmatch(job_id or ""):
            raise ValueError(f"not a job id: {job_id!r}")
        return cls.jobs_dir() / job_id

    @classmethod
    def unit_name(cls, job_id: str) -> str:
        """
        Args:
            job_id: The job's id.

        Returns:
            str: The systemd user unit the job runs as.
        """
        return f"puffin-job-{job_id}"

    # -- the record ------------------------------------------------------------------------------

    @classmethod
    def validate(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turns what a sender asked for into a job record, or refuses it. Everything here came
        from another machine.

        Args:
            request: `id`, `repo`, `commit`, `command` (a list), `memory`, `time`, and optionally
                `test`, `airgapped`, `gpu`, `author`.

        Returns:
            Dict[str, Any]: The record, status `queued`.

        Raises:
            ValueError: With the reason, when the job cannot be accepted.
        """
        from dreamference.night_shift.night_shift_host import NightShiftHost
        from dreamference.night_shift.night_shift_settings import NightShiftSettings
        if not isinstance(request, dict):
            raise ValueError("a job is a JSON object")
        job_id = request.get("id")
        cls.job_dir(job_id)
        cls.repo_path(request.get("repo"))
        if not COMMIT.fullmatch(str(request.get("commit") or "")):
            raise ValueError("a job names the commit it runs at, as a full hash")
        command = request.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(word, str) and word for word in command):
            raise ValueError("a job has a command")
        # No job without a memory cap and a time limit: there is no default a sender can omit
        # them into. The sender's own tool fills in Night Shift's defaults before sending.
        if not request.get("memory") or not request.get("time"):
            raise ValueError("a job needs a memory cap and a time limit")
        try:
            memory = NightShiftHost.parse_size(str(request["memory"]))
            seconds = NightShiftSettings.parse_duration(str(request["time"]))
        except ValueError as error:
            raise ValueError(str(error)) from None
        if memory <= 0 or seconds <= 0:
            raise ValueError("a job needs a memory cap and a time limit")
        if memory > MAX_MEMORY_BYTES:
            raise ValueError(f"this node allows a job at most {MAX_MEMORY_BYTES // 1024 ** 3}G of memory")
        if seconds > MAX_TIME_S:
            raise ValueError(f"this node allows a job at most {MAX_TIME_S // 3600}h")
        if request.get("gpu"):
            raise ValueError("GPU jobs are not allowed on a node for now: a memory cap does not bound GPU "
                             "memory on a GB10, where it is the same pool the model server uses")
        test = request.get("test")
        if test is not None and not isinstance(test, str):
            raise ValueError("the test command is a string")
        author = request.get("author") if isinstance(request.get("author"), dict) else {}
        return {
            "id": job_id, "repo": request["repo"], "commit": request["commit"], "command": command,
            "memory": str(request["memory"]), "memory_bytes": memory, "time": str(request["time"]),
            "time_s": seconds, "test": test or None,
            "airgapped": cls.stricter(str(request.get("airgapped") or "off"), cls.node_airgap_level()),
            "author": {"name": str(author.get("name") or "Puffin Job")[:100],
                       "email": str(author.get("email") or "puffin-job@localhost")[:200]},
            "sender": str(request.get("sender") or "")[:100],
            "status": "queued", "submitted": cls.now(), "branch": None, "exit_code": None, "note": None,
        }

    @classmethod
    def stricter(cls, first: str, second: str) -> str:
        """
        Args:
            first: An `/airgapped` level.
            second: Another.

        Returns:
            str: The stricter of the two; an unknown name counts as `on`.
        """
        rank = lambda level: AIRGAP_LEVELS.index(level) if level in AIRGAP_LEVELS else len(AIRGAP_LEVELS) - 1
        return AIRGAP_LEVELS[max(rank(first), rank(second))]

    @classmethod
    def node_airgap_level(cls) -> str:
        """
        Returns:
            str: This node's configured `/airgapped` level.
        """
        from dreamference.config import DreamferenceConfig
        try:
            return DreamferenceConfig().puffin_airgapped
        except Exception:
            return "on"

    @classmethod
    def read(cls, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Args:
            job_id: The job's id.

        Returns:
            Optional[Dict[str, Any]]: Its record, or None.
        """
        try:
            record = json.loads((cls.job_dir(job_id) / "record.json").read_text())
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    @classmethod
    def write(cls, record: Dict[str, Any]) -> None:
        """
        Args:
            record: The job's record, replaced whole.
        """
        directory = cls.job_dir(record["id"])
        directory.mkdir(parents=True, exist_ok=True)
        staging = directory / f".record.json.{os.getpid()}.tmp"
        staging.write_text(json.dumps(record, indent=2) + "\n")
        os.replace(staging, directory / "record.json")

    @classmethod
    def records(cls) -> List[Dict[str, Any]]:
        """
        Returns:
            List[Dict[str, Any]]: Every job on this node, oldest first.
        """
        found = []
        for path in sorted(cls.jobs_dir().glob("*/record.json")) if cls.jobs_dir().is_dir() else []:
            if JOB_ID.fullmatch(path.parent.name):
                record = cls.read(path.parent.name)
                if record:
                    found.append(record)
        return found

    @classmethod
    def now(cls) -> str:
        """
        Returns:
            str: The local time, RFC 3339.
        """
        return datetime.now().astimezone().isoformat(timespec="seconds")

    # -- starting it -----------------------------------------------------------------------------

    @classmethod
    def unit_command(cls, record: Dict[str, Any], admin: str) -> List[str]:
        """
        The command that starts the job as a unit of its own: capped, time-limited, and not a
        child of the connection that asked for it.

        Args:
            record: The job's record.
            admin: This node's `puffin-admin`, by absolute path.

        Returns:
            List[str]: The `systemd-run` argv.
        """
        return ["systemd-run", "--user", "--quiet", "--collect", f"--unit={cls.unit_name(record['id'])}",
                "-p", f"MemoryMax={record['memory_bytes']}", "-p", "MemorySwapMax=0",
                "-p", f"RuntimeMaxSec={record['time_s']}", "-p", "CPUQuota=800%",
                "--", admin, "node", "job-exec", record["id"]]

    @classmethod
    def submit(cls, request: Dict[str, Any], admin: str) -> Dict[str, Any]:
        """
        Accepts a job and starts it.

        Args:
            request: What the sender asked for.
            admin: This node's `puffin-admin`, by absolute path.

        Returns:
            Dict[str, Any]: The record as started.

        Raises:
            ValueError: When the job is refused; nothing was started.
        """
        record = cls.validate(request)
        if cls.read(record["id"]) is not None:
            raise ValueError(f"job {record['id']} already exists on this node")
        repo = cls.repo_path(record["repo"])
        known = subprocess.run(["git", "--git-dir", str(repo), "cat-file", "-e", f"{record['commit']}^{{commit}}"],
                               capture_output=True, text=True, check=False)
        if known.returncode != 0:
            raise ValueError("the commit was not pushed to this node before the job was sent")
        reason = cls.admission_blocker(record)
        if reason:
            raise ValueError(reason)
        cls.write(record)
        (cls.job_dir(record["id"]) / "output.log").touch()
        if cls.USE_UNIT:
            started = subprocess.run(cls.unit_command(record, admin), capture_output=True, text=True, check=False)
            if started.returncode != 0:
                record.update(status="failed", note=f"could not start the job's unit: {started.stderr.strip()[-200:]}")
                cls.write(record)
                raise ValueError(record["note"])
        return record

    @classmethod
    def admission_blocker(cls, record: Dict[str, Any]) -> Optional[str]:
        """
        Whether this node can take the job now. The node that would carry the load decides, with
        its own state; the sender's is irrelevant.

        Args:
            record: The job's record.

        Returns:
            Optional[str]: The reason it cannot, or None.
        """
        from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
        from dreamference.night_shift.night_shift_queue import NightShiftQueue
        if NightShiftQueue.runner_active():
            return "a night run or a benchmark run is in progress on this node"
        heavy = NightShiftHost.heavy_jobs()
        if heavy:
            return "; ".join(heavy)
        available = NightShiftHost.mem_available_bytes()
        needed = MEMORY_RESERVE + record["memory_bytes"]
        if available < needed:
            return (f"this node has {available / GIB:.1f} GiB of memory available; the job's "
                    f"{record['memory']} cap needs {needed / GIB:.0f} with the reserve")
        return None

    # -- running it (inside the unit) ------------------------------------------------------------

    @classmethod
    def sandbox_command(cls, tree: Path, command: List[str], network: bool, job_id: str) -> List[str]:
        """
        The bubblewrap command line a job's command runs under.

        Args:
            tree: The job's worktree, the only writable place.
            command: The job's command.
            network: False removes the network (the `/airgapped` level `on`).
            job_id: The job's id, given to the command as `PUFFIN_JOB`.

        Returns:
            List[str]: The argv. Every tmpfs comes before the one bind, because bubblewrap hides
            a bind made under a later tmpfs.
        """
        home = os.path.expanduser("~")
        argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        if not network:
            argv.append("--unshare-net")
        # The system read-only; a /dev without the GPU's device nodes; nothing of /run, where the
        # Docker socket and the user's session bus are.
        argv += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
        for hidden in ("/tmp", "/run", "/var/tmp", "/dev/shm", home):
            argv += ["--tmpfs", hidden]
        argv += ["--bind", str(tree), str(tree), "--clearenv"]
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": "dumb", "PUFFIN_JOB": job_id, "GIT_TERMINAL_PROMPT": "0",
            # CPU-only: a job that reached CUDA would be outside its memory cap.
            "CUDA_VISIBLE_DEVICES": "", "NVIDIA_VISIBLE_DEVICES": "none",
        }
        for key, value in environment.items():
            argv += ["--setenv", key, value]
        return argv + ["--chdir", str(tree), "--", *command]

    @classmethod
    def execute(cls, job_id: str) -> int:
        """
        Runs a job: worktree, command, test, commit. Called inside the job's unit by
        `puffin-admin node job-exec`.

        Args:
            job_id: The job's id.

        Returns:
            int: The command's exit code (the test's, if the command passed and it failed).
        """
        record = cls.read(job_id)
        if record is None or record.get("status") != "queued":
            print(f"❌ no queued job {job_id}", file=sys.stderr)
            return 1
        directory = cls.job_dir(job_id)
        tree = directory / "tree"
        repo = cls.repo_path(record["repo"])
        log = directory / "output.log"
        started = time.time()
        record.update(status="running", started=cls.now())
        cls.write(record)
        code = 1
        try:
            added = cls._git(["--git-dir", str(repo), "worktree", "add", "--detach", str(tree), record["commit"]])
            if added.returncode != 0:
                record["note"] = f"git worktree add failed: {added.stderr.strip()[-200:]}"
                return 1
            network = record["airgapped"] != "on"
            with open(log, "ab") as sink:
                code = cls._run(cls.sandbox_command(tree, record["command"], network, job_id), sink, tree)
                record["exit_code"] = code
                if code == 0 and record.get("test"):
                    sink.write(f"\n--- test: {record['test']}\n".encode())
                    sink.flush()
                    test_code = cls._run(cls.sandbox_command(tree, ["bash", "-c", record["test"]], network, job_id), sink, tree)
                    record["test_exit_code"] = test_code
                    code = test_code
            record["branch"] = cls._commit(record, tree, repo)
            return code
        except Exception as error:  # A runner bug must not leave the job `running` forever.
            record["note"] = f"job runner error: {error}"
            return 1
        finally:
            cls._git(["--git-dir", str(repo), "worktree", "remove", "--force", str(tree)])
            current = cls.read(job_id) or {}
            if current.get("status") == "cancelled":
                record["status"] = "cancelled"
            else:
                record["status"] = "done" if code == 0 else "failed"
            record.update(finished=cls.now(), wall_s=int(time.time() - started))
            cls.write(record)

    @classmethod
    def _commit(cls, record: Dict[str, Any], tree: Path, repo: Path) -> Optional[str]:
        """Commits what `git status` shows on `job/<id>`; None when the job changed nothing."""
        status = cls._git(["-C", str(tree), "status", "--porcelain"])
        if status.returncode != 0 or not status.stdout.strip():
            return None
        branch = f"job/{record['id']}"
        identity = ["-c", f"user.name={record['author']['name']}", "-c", f"user.email={record['author']['email']}"]
        first_line = shlex.join(record["command"])[:72]
        for step, arguments in (("checkout", ["checkout", "-q", "-b", branch]), ("add", ["add", "-A"]),
                                ("commit", [*identity, "commit", "-q", "-m", f"job: {first_line}"])):
            result = cls._git(["-C", str(tree), *arguments])
            if result.returncode != 0:
                record["note"] = f"git {step} failed: {result.stderr.strip()[-200:]}"
                return None
        return branch

    @classmethod
    def _run(cls, argv: List[str], sink: Any, tree: Path) -> int:
        try:
            return subprocess.run(argv, cwd=tree, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                                  check=False).returncode
        except OSError as error:
            sink.write(f"could not start: {error}\n".encode())
            return 127

    @classmethod
    def _git(cls, arguments: List[str]) -> subprocess.CompletedProcess:
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        return subprocess.run(["git", *arguments], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=environment, check=False)

    # -- watching and stopping it ----------------------------------------------------------------

    @classmethod
    def follow(cls, job_id: str, offset: int = 0, poll_s: float = 0.5) -> int:
        """
        Prints a job's output from `offset`, and keeps printing while it runs. The stream is a
        view: losing it does not stop the job.

        Args:
            job_id: The job's id.
            offset: Where in the output to start.
            poll_s: How often to look for more.

        Returns:
            int: The job's exit code once it has finished (1 if it failed without one).
        """
        log = cls.job_dir(job_id) / "output.log"
        position = max(0, offset)
        while True:
            record = cls.reconcile(job_id) or {}
            finished = record.get("status") in FINAL_STATUSES
            try:
                with open(log, "rb") as handle:
                    handle.seek(position)
                    chunk = handle.read()
            except OSError:
                chunk = b""
            if chunk:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                position += len(chunk)
            if finished and not chunk:
                break
            if not finished:
                time.sleep(poll_s)
        if record.get("note"):
            print(f"\n{record['note']}", file=sys.stderr)
        if record.get("status") == "done":
            return 0
        code = record.get("test_exit_code") if record.get("exit_code") == 0 else record.get("exit_code")
        return code if isinstance(code, int) and code != 0 else 1

    @classmethod
    def reconcile(cls, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Reads a job's record, first settling one whose unit is gone without a result: systemd
        stops a unit that passes its time limit or its memory cap, and the job's own code never
        gets to say so.

        Args:
            job_id: The job's id.

        Returns:
            Optional[Dict[str, Any]]: The record as it now stands.
        """
        record = cls.read(job_id)
        if record is None or record.get("status") not in ("queued", "running") or not cls.USE_UNIT:
            return record
        if cls.unit_active(job_id):
            return record
        # The unit may have finished between the read and the check: read once more.
        record = cls.read(job_id)
        if record and record.get("status") in ("queued", "running"):
            record.update(status="failed", finished=cls.now(),
                          note=f"the job was stopped without a result: it passed its time limit ({record['time']}), "
                               f"its memory cap ({record['memory']}), or the node was restarted")
            cls.write(record)
        return record

    @classmethod
    def unit_active(cls, job_id: str) -> bool:
        """
        Args:
            job_id: The job's id.

        Returns:
            bool: True while the job's unit is starting or running.
        """
        result = subprocess.run(["systemctl", "--user", "is-active", f"{cls.unit_name(job_id)}.service"],
                                capture_output=True, text=True, check=False)
        return result.stdout.strip() in ("active", "activating")

    @classmethod
    def cancel(cls, job_id: str) -> bool:
        """
        Stops a running job.

        Args:
            job_id: The job's id.

        Returns:
            bool: True if the job was running and is now marked cancelled.
        """
        record = cls.read(job_id)
        if record is None or record.get("status") in FINAL_STATUSES:
            return False
        record.update(status="cancelled", finished=cls.now())
        cls.write(record)
        if cls.USE_UNIT:
            subprocess.run(["systemctl", "--user", "stop", f"{cls.unit_name(job_id)}.service"],
                           capture_output=True, text=True, check=False)
        return True
