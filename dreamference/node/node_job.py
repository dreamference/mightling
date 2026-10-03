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

A worktree on another machine has no virtualenv, so a job that needs one says how to build it
(`--setup`, or `[night] setup` in the repository's `dreamference.toml`); the result is kept per
repository and per content of its lock files, and bound read-only into later jobs (§13.6). Data
the job needs is bound read-only from paths this node's owner allows (`[node] bindable`), and
artifacts named with `--out` are kept beside the job, never committed (§13.3). Finished jobs are
pruned a day after the sender fetched them, and unfetched ones after 14 days (§13.7).
"""

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Sequence

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

# How long a finished job is kept: a day after the sender fetched it (so `node logs` still reads it
# that day), 14 days when nobody fetched it.
KEEP_AFTER_FETCH: Final[timedelta] = timedelta(days=1)
KEEP_UNFETCHED: Final[timedelta] = timedelta(days=14)

# The files whose content decides whether a job's environment can be reused: the same setup
# command and the same lock files give the same environment.
LOCK_FILES: Final[tuple] = (
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "poetry.lock", "uv.lock", "Pipfile.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum")

# What `--out` may name: a path inside the worktree.
OUT_PATH: Final[re.Pattern] = re.compile(r"[A-Za-z0-9._-][A-Za-z0-9._/-]{0,200}")


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
        setup = request.get("setup")
        if setup is not None and (not isinstance(setup, str) or not setup.strip()):
            raise ValueError("the setup command is a string")
        out = request.get("out")
        if out is not None and (not isinstance(out, str) or not OUT_PATH.fullmatch(out)
                                or ".." in out.split("/") or out.startswith("/")):
            raise ValueError("--out names a folder inside the repository, such as `checkpoints`")
        binds = cls.allowed_binds(request.get("binds") or [])
        author = request.get("author") if isinstance(request.get("author"), dict) else {}
        return {
            "id": job_id, "repo": request["repo"], "commit": request["commit"], "command": command,
            "memory": str(request["memory"]), "memory_bytes": memory, "time": str(request["time"]),
            "time_s": seconds, "test": test or None, "setup": setup or None,
            "out": out.strip("/") if out else None, "binds": binds,
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
    def bindable_roots(cls) -> List[str]:
        """
        Returns:
            List[str]: The folders under which a sender may ask for a read-only bind: `[node]
            bindable` in this node's user-level `config.toml`. None by default; it is the node
            owner's decision, never the sender's.
        """
        from dreamference.night_shift.night_shift_settings import NightShiftSettings
        path = Path(os.path.expanduser("~/.config/dreamference/config.toml"))
        table = NightShiftSettings.read_table(path, section="node")
        roots = table.get("bindable") or []
        if isinstance(roots, str):
            roots = [roots]
        return [os.path.realpath(os.path.expanduser(str(root))) for root in roots if str(root).strip()]

    @classmethod
    def allowed_binds(cls, binds: Any) -> List[str]:
        """
        Checks the read-only binds a sender asked for against what this node allows.

        Args:
            binds: The requested paths (a list of absolute paths on this node).

        Returns:
            List[str]: The paths, resolved.

        Raises:
            ValueError: If a path is malformed, missing, or outside every allowed root.
        """
        if not isinstance(binds, list) or not all(isinstance(path, str) and path.startswith("/") for path in binds):
            raise ValueError("--bind names absolute paths on the node")
        if len(binds) > 16:
            raise ValueError("a job may bind at most 16 paths")
        roots = cls.bindable_roots() if binds else []
        resolved = []
        for path in binds:
            real = os.path.realpath(path)
            if not any(real == root or real.startswith(root.rstrip("/") + "/") for root in roots):
                allowed = ", ".join(roots) or "nothing ([node] bindable in its config.toml is empty)"
                raise ValueError(f"this node does not allow binding {path}; it allows {allowed}")
            if not os.path.exists(real):
                raise ValueError(f"{path} does not exist on this node")
            resolved.append(real)
        return resolved

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
        cls.prune()
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
            started = subprocess.run(cls.unit_command(record, admin), capture_output=True, text=True,
                                     env=cls.user_bus_environment(), check=False)
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
        blocked = cls.sandbox_blocker()
        if blocked:
            return blocked
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

    @classmethod
    def sandbox_blocker(cls) -> Optional[str]:
        """
        Whether this node can sandbox a job at all. A job that cannot be sandboxed is refused,
        never run without: it is code from another machine.

        On Ubuntu 24.04 AppArmor lets only programs with a profile create a user namespace
        (`kernel.apparmor_restrict_unprivileged_userns`), and bubblewrap has none by default, so
        a process started by sshd or by the user's service manager cannot use it (measured on
        the GB10, 2026-10-02: "setting up uid map: Permission denied").

        Returns:
            Optional[str]: The reason, with what to do on the node, or None when bubblewrap works
            in the context a job's unit runs in.
        """
        probe = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "--dev", "/dev",
                 "--proc", "/proc", "true"]
        if cls.USE_UNIT:
            # Asked of a unit like the job's own: that is the context that has to be able to.
            probe = ["systemd-run", "--user", "--quiet", "--wait", "--collect", "--pipe", "--", *probe]
        try:
            result = subprocess.run(probe, capture_output=True, text=True, timeout=30,
                                    env=cls.user_bus_environment(), check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return f"this node cannot sandbox a job: {error}"
        if result.returncode == 0:
            return None
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["bubblewrap failed"]
        return ("this node cannot sandbox a job, so it will not run one (" + detail[0] + "). On Ubuntu this is "
                "AppArmor allowing user namespaces only to programs with a profile; on the node, as root, "
                "give /usr/bin/bwrap a profile with `userns,` (the apparmor-profiles package has "
                "bwrap-userns-restrict), then try again")

    @classmethod
    def user_bus_environment(cls) -> Dict[str, str]:
        """
        Returns:
            Dict[str, str]: The environment in which `systemd-run --user` and `systemctl --user`
            find the user's service manager. A forced command reached without a PAM session has
            no `XDG_RUNTIME_DIR`; the directory is the same for every session of the user.
        """
        environment = dict(os.environ)
        runtime = f"/run/user/{os.getuid()}"
        if not environment.get("XDG_RUNTIME_DIR") and os.path.isdir(runtime):
            environment["XDG_RUNTIME_DIR"] = runtime
        return environment

    # -- running it (inside the unit) ------------------------------------------------------------

    @classmethod
    def sandbox_command(cls, tree: Path, command: List[str], network: bool, job_id: str,
                        writable: Sequence[str] = (), readable: Sequence[str] = (),
                        environment: Optional[Dict[str, str]] = None) -> List[str]:
        """
        The bubblewrap command line a job's command runs under.

        Args:
            tree: The job's worktree, writable.
            command: The job's command.
            network: False removes the network (the `/airgapped` level `on`).
            job_id: The job's id, given to the command as `PUFFIN_JOB`.
            writable: Other folders bound read-write (the environment while setup builds it).
            readable: Folders bound read-only (a built environment, the node's allowed data).
            environment: Variables set on top of the fixed ones (`PUFFIN_ENV`, a longer `PATH`).

        Returns:
            List[str]: The argv. Every tmpfs comes before the binds, because bubblewrap hides
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
        argv += ["--bind", str(tree), str(tree)]
        for path in writable:
            argv += ["--bind", str(path), str(path)]
        for path in readable:
            argv += ["--ro-bind", str(path), str(path)]
        argv.append("--clearenv")
        variables = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": "dumb", "PUFFIN_JOB": job_id, "GIT_TERMINAL_PROMPT": "0",
            # CPU-only: a job that reached CUDA would be outside its memory cap.
            "CUDA_VISIBLE_DEVICES": "", "NVIDIA_VISIBLE_DEVICES": "none",
        }
        variables.update(environment or {})
        for key, value in variables.items():
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
                env_dir, problem = cls._environment(record, tree, network, sink)
                if problem:
                    record["note"] = problem
                    return 1
                readable = ([str(env_dir)] if env_dir else []) + list(record.get("binds") or [])
                variables = cls.environment_variables(env_dir)
                sandboxed = lambda command: cls.sandbox_command(tree, command, network, job_id,
                                                                readable=readable, environment=variables)
                code = cls._run(sandboxed(record["command"]), sink, tree)
                record["exit_code"] = code
                if code == 0 and record.get("test"):
                    sink.write(f"\n--- test: {record['test']}\n".encode())
                    sink.flush()
                    test_code = cls._run(sandboxed(["bash", "-c", record["test"]]), sink, tree)
                    record["test_exit_code"] = test_code
                    code = test_code
            if code != 0 and not env_dir and cls.missing_module(log):
                record["note"] = ("an environment failure, not a failing job: a Python module was missing, and with "
                                  "no setup command the job ran with the node's system interpreter "
                                  "(send it with --setup, or set [night] setup in dreamference.toml)")
            if record.get("out"):
                record["out_files"] = cls._keep_out(record, tree, directory)
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

    # -- the environment (§13.6) ------------------------------------------------------------------

    @classmethod
    def setup_command(cls, record: Dict[str, Any], tree: Path) -> Optional[str]:
        """
        Args:
            record: The job's record.
            tree: Its worktree, at the job's commit.

        Returns:
            Optional[str]: The command that builds the job's environment: `--setup`, else `[night]
            setup` in the repository's `dreamference.toml`; None when there is none.
        """
        if record.get("setup"):
            return record["setup"]
        from dreamference.night_shift.night_shift_settings import NightShiftSettings
        configured = NightShiftSettings.read_table(tree / "dreamference.toml").get("setup")
        return str(configured) if configured else None

    @classmethod
    def environment_key(cls, slug: str, setup: str, tree: Path) -> str:
        """
        Args:
            slug: The job repository's name.
            setup: The setup command.
            tree: The worktree, whose lock files are read.

        Returns:
            str: The name of the environment folder: the repository, then a digest of the setup
            command and the content of every lock file at the worktree's top.
        """
        digest = hashlib.sha256(setup.encode())
        for name in LOCK_FILES:
            path = tree / name
            if path.is_file():
                digest.update(f"\0{name}\0".encode())
                digest.update(path.read_bytes())
        return f"{slug}-{digest.hexdigest()[:16]}"

    @classmethod
    def environment_variables(cls, env_dir: Optional[Path]) -> Dict[str, str]:
        """
        Args:
            env_dir: The job's built environment, or None.

        Returns:
            Dict[str, str]: `PUFFIN_ENV`, and `PATH` with the environment's `bin` first.
        """
        if not env_dir:
            return {}
        return {"PUFFIN_ENV": str(env_dir), "VIRTUAL_ENV": str(env_dir),
                "PATH": f"{env_dir}/bin:/usr/local/bin:/usr/bin:/bin"}

    @classmethod
    def _environment(cls, record: Dict[str, Any], tree: Path, network: bool, sink: Any) -> Any:
        """
        Builds the job's environment, or finds it built.

        Returns:
            Any: `(folder or None, problem or None)`.
        """
        setup = cls.setup_command(record, tree)
        if not setup:
            record["environment"] = "none: the node's system interpreter"
            return None, None
        key = cls.environment_key(record["repo"], setup, tree)
        env_dir = cls.jobs_dir() / "envs" / key
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        with open(env_dir.parent / f"{key}.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if (env_dir / ".complete").is_file():
                    record["environment"] = f"{key} (reused)"
                    os.utime(env_dir / ".complete")
                    return env_dir, None
                shutil.rmtree(env_dir, ignore_errors=True)
                env_dir.mkdir()
                sink.write(f"--- setup: {setup}\n".encode())
                sink.flush()
                argv = cls.sandbox_command(tree, ["bash", "-c", setup], network, record["id"],
                                           writable=[str(env_dir)], environment=cls.environment_variables(env_dir))
                code = cls._run(argv, sink, tree)
                if code != 0:
                    shutil.rmtree(env_dir, ignore_errors=True)
                    return None, f"the environment's setup command failed (exit {code}); its output is in the log"
                (env_dir / ".complete").write_text(setup + "\n")
                sink.write(b"--- setup done\n")
                record["environment"] = f"{key} (built)"
                return env_dir, None
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @classmethod
    def missing_module(cls, log: Path) -> bool:
        """
        Args:
            log: The job's output.

        Returns:
            bool: True when its last lines show a Python module that could not be imported.
        """
        try:
            with open(log, "rb") as handle:
                handle.seek(max(0, log.stat().st_size - 8192))
                tail = handle.read().decode("utf-8", "replace")
        except OSError:
            return False
        return "ModuleNotFoundError" in tail or "No module named" in tail

    # -- artifacts (§13.3) -----------------------------------------------------------------------

    @classmethod
    def _keep_out(cls, record: Dict[str, Any], tree: Path, directory: Path) -> int:
        """
        Moves what the job wrote under `--out` out of the worktree, so it is kept as files beside
        the job and never committed.

        Returns:
            int: How many files were kept.
        """
        source = tree / record["out"]
        if not source.exists() or source.is_symlink():
            return 0
        kept = directory / "out"
        shutil.rmtree(kept, ignore_errors=True)
        kept.mkdir(parents=True)
        target = kept / Path(record["out"]).name
        shutil.move(str(source), str(target))
        return sum(1 for path in kept.rglob("*") if path.is_file())

    @classmethod
    def send_out(cls, job_id: str) -> int:
        """
        Writes a job's `--out` folder to standard output as a tar stream, for `node fetch`.

        Args:
            job_id: The job's id.

        Returns:
            int: 0, or 1 when the job kept nothing.
        """
        kept = cls.job_dir(job_id) / "out"
        if not kept.is_dir():
            print("this job kept no --out files", file=sys.stderr)
            return 1
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for child in sorted(kept.iterdir()):
                archive.add(str(child), arcname=child.name)
        sys.stdout.buffer.flush()
        return 0

    # -- pruning (§13.7) -------------------------------------------------------------------------

    @classmethod
    def prune_after(cls, record: Dict[str, Any]) -> Optional[str]:
        """
        Args:
            record: A job's record.

        Returns:
            Optional[str]: When the job may be pruned, RFC 3339; None while it is not finished.
        """
        if record.get("status") not in FINAL_STATUSES:
            return None
        try:
            if record.get("fetched"):
                return (datetime.fromisoformat(record["fetched"]) + KEEP_AFTER_FETCH).isoformat(timespec="seconds")
            finished = record.get("finished") or record.get("submitted")
            return (datetime.fromisoformat(finished) + KEEP_UNFETCHED).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None

    @classmethod
    def mark_fetched(cls, job_id: str) -> bool:
        """
        Records that the sender has the job's result, which starts its shorter keep.

        Args:
            job_id: The job's id.

        Returns:
            bool: True when the job is finished and is now marked.
        """
        record = cls.reconcile(job_id)
        if record is None or record.get("status") not in FINAL_STATUSES:
            return False
        record.setdefault("fetched", cls.now())
        cls.write(record)
        return True

    @classmethod
    def prune(cls, now: Optional[datetime] = None) -> List[str]:
        """
        Removes finished jobs that are past their keep: the folder, the worktree if one was left
        (a cancelled or killed job's), the result branch and the pushed ref.

        Args:
            now: The time to compare with; the current time by default.

        Returns:
            List[str]: The ids removed.
        """
        now = now or datetime.now().astimezone()
        removed = []
        for record in cls.records():
            after = cls.prune_after(record)
            if not after or datetime.fromisoformat(after) > now:
                continue
            directory = cls.job_dir(record["id"])
            try:
                repo = cls.repo_path(record["repo"])
            except ValueError:
                repo = None
            if repo is not None and repo.is_dir():
                if (directory / "tree").exists():
                    cls._git(["--git-dir", str(repo), "worktree", "remove", "--force", str(directory / "tree")])
                cls._git(["--git-dir", str(repo), "worktree", "prune"])
                cls._git(["--git-dir", str(repo), "update-ref", "-d", f"refs/jobs/{record['id']}"])
                cls._git(["--git-dir", str(repo), "branch", "-D", f"job/{record['id']}"])
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(record["id"])
        return removed

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
                                capture_output=True, text=True, env=cls.user_bus_environment(), check=False)
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
                           capture_output=True, text=True, env=cls.user_bus_environment(), check=False)
        return True
