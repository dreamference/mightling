"""
Sending a job to another node (specs/DREAMFERENCE_PUFFIN_NODE.md §13.3):
`puffin-admin node run|jobs|logs|cancel|fetch`.

The sender pushes `HEAD` to the node over the pairing, asks for the job, and shows its output;
when it ends, the branch the node committed is fetched back. Nothing lands in this checkout until
the user merges it, and uncommitted changes are not sent.
"""

import base64
import hashlib
import json
import os
import random
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_job import DEFAULT_MEMORY, DEFAULT_TIME, JOB_ID
from dreamference.node.node_pairing import NodePairing


class NodeJobSender:
    """The sending side of a remote job."""

    @classmethod
    def sent_dir(cls) -> Path:
        """
        Returns:
            Path: `~/.config/dreamference/nodes/jobs`: which node each job went to, and from
            which repository.
        """
        return NodePairing.nodes_dir() / "jobs"

    @classmethod
    def repo_slug(cls, repo: str) -> str:
        """
        Args:
            repo: A repository's root on this machine.

        Returns:
            str: Its name on the node: the folder name and a digest of this machine and the
            path, so two checkouts of the same name do not share a job repository.
        """
        digest = hashlib.sha1(f"{NodeIdentity.read() or ''}:{os.path.realpath(repo)}".encode()).hexdigest()[:10]
        name = "".join(c if c.isalnum() or c in "._-" else "-" for c in os.path.basename(os.path.realpath(repo)))
        return f"{name.strip('.-') or 'repo'}-{digest}"[:80]

    @classmethod
    def new_id(cls) -> str:
        """
        Returns:
            str: A job id, `<date>-<time>-<3 hex>`, the shape of a Night Shift task id.
        """
        return f"{datetime.now():%Y%m%d-%H%M}-{random.randrange(0x1000):03x}"

    @classmethod
    def git_url(cls, record: Dict[str, Any], slug: str) -> str:
        """
        Args:
            record: The paired node's record.
            slug: The repository's name on the node.

        Returns:
            str: The URL git pushes to and fetches from; the node's `serve-job` maps it to its
            job repository.
        """
        address = record["address"]
        host = f"[{address}]" if ":" in address else address
        return f"ssh://{record['user']}@{host}:{record.get('ssh_port') or 22}/jobs/{slug}.git"

    @classmethod
    def git_environment(cls, record: Dict[str, Any]) -> Dict[str, str]:
        """
        Args:
            record: The paired node's record.

        Returns:
            Dict[str, str]: The environment in which git reaches the node through the pairing
            key and its pinned host key, and never asks anything.
        """
        import shlex
        options = NodePairing.ssh_options(record)
        # The port is in the URL, and git passes it to ssh itself.
        at = options.index("-p")
        options = options[:at] + options[at + 2:]
        return dict(os.environ, GIT_SSH_COMMAND=shlex.join(["ssh", *options]), GIT_TERMINAL_PROMPT="0")

    @classmethod
    def compose(cls, repo: str, commit: str, command: List[str], memory: Optional[str], time_limit: Optional[str],
                test: Optional[str], gpu: bool = False) -> Dict[str, Any]:
        """
        Builds the request sent to the node. The memory cap and the time limit are always in it:
        Night Shift's defaults when the user gave none.

        Args:
            repo: The repository's root.
            commit: The commit the job runs at.
            command: The command.
            memory: `--memory`, or None for the default.
            time_limit: `--time`, or None for the default.
            test: `--test`, the command that decides pass or fail.
            gpu: `--gpu`; sent so that the node refuses it by its own rule.

        Returns:
            Dict[str, Any]: The request.
        """
        from dreamference.config import DreamferenceConfig
        try:
            level = DreamferenceConfig().puffin_airgapped
        except Exception:
            level = "on"
        return {
            "id": cls.new_id(), "repo": cls.repo_slug(repo), "commit": commit, "command": list(command),
            "memory": memory or DEFAULT_MEMORY, "time": time_limit or DEFAULT_TIME, "test": test,
            "gpu": bool(gpu), "airgapped": level, "sender": NodeIdentity.read() or "",
            "author": {"name": cls._git(repo, "config", "user.name").stdout.strip(),
                       "email": cls._git(repo, "config", "user.email").stdout.strip()},
        }

    @classmethod
    def run(cls, name: str, command: List[str], memory: Optional[str] = None, time_limit: Optional[str] = None,
            test: Optional[str] = None, gpu: bool = False, cwd: Optional[str] = None) -> int:
        """
        Runs a command on another node, in the current repository at HEAD.

        Args:
            name: The paired node.
            command: The command and its arguments.
            memory: The job's memory cap (default 8G).
            time_limit: The job's time limit (default 90m).
            test: A command that decides pass or fail, run after the job's own.
            gpu: Ask for the GPU (refused by the node for now).
            cwd: Where to run from; the working directory by default.

        Returns:
            int: The job's exit code; 1 when it could not be sent.
        """
        cwd = cwd or os.getcwd()
        if not command:
            print("❌ Nothing to run: puffin-admin node run <node> -- <command>")
            return 1
        record = NodePairing.find(name)
        if record is None:
            print(f"❌ {name} is not a paired node. Pair once with: puffin-admin node add {name}")
            return 1
        top = cls._git(cwd, "rev-parse", "--show-toplevel")
        head = cls._git(cwd, "rev-parse", "HEAD")
        if top.returncode != 0 or head.returncode != 0:
            print("❌ A job runs in a git repository at a commit, and this directory has none.")
            return 1
        repo, commit = top.stdout.strip(), head.stdout.strip()
        if cls._git(repo, "status", "--porcelain").stdout.strip():
            print("⚠️  Uncommitted changes are not sent: the job runs at HEAD.")
        request = cls.compose(repo, commit, command, memory, time_limit, test, gpu)
        url = cls.git_url(record, request["repo"])
        print(f"📤 Sending {commit[:10]} to {record['name']} as job {request['id']} "
              f"(memory {request['memory']}, time {request['time']})...")
        pushed = subprocess.run(["git", "-C", repo, "push", "--quiet", url, f"{commit}:refs/jobs/{request['id']}"],
                                env=cls.git_environment(record), capture_output=True, text=True, check=False)
        if pushed.returncode != 0:
            print(f"❌ Could not push to the node: {pushed.stderr.strip()[-300:]}")
            return 1
        cls._remember(request["id"], record, repo, request["repo"])
        payload = base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
        code = NodePairing.run(record, f"job-submit {payload}", capture=False).returncode
        cls._fetch_branch(request["id"], quiet_when_absent=True)
        return code

    @classmethod
    def split_run_arguments(cls, words: List[str]) -> Any:
        """
        Separates `node run`'s own options from the job's command in what follows the node's
        name: `--memory 16G --test "pytest -q" -- python train.py --epochs 3`.

        Args:
            words: Everything after the node's name.

        Returns:
            Any: `(options, command)`: the options (`memory`, `time`, `test`, `gpu`) and the
            command's words. With `--`, what follows it is the command verbatim, so the
            command's own options are never read as this one's; without it, the command starts
            at the first word that is not one of these options.
        """
        import argparse
        parser = argparse.ArgumentParser(add_help=False, prog="puffin-admin node run")
        parser.add_argument("--memory", default=None)
        parser.add_argument("--time", default=None)
        parser.add_argument("--test", default=None)
        parser.add_argument("--gpu", action="store_true")
        if "--" in words:
            at = words.index("--")
            options, extra = parser.parse_known_args(words[:at])
            return options, extra + words[at + 1:]
        options, command = parser.parse_known_args([])
        index = 0
        own = {"--memory": 2, "--time": 2, "--test": 2, "--gpu": 1}
        while index < len(words):
            word = words[index].split("=", 1)[0]
            if word not in own:
                break
            index += 1 if "=" in words[index] else own[word]
        options, _ = parser.parse_known_args(words[:index])
        return options, words[index:]

    @classmethod
    def jobs(cls, name: Optional[str] = None) -> int:
        """
        Lists jobs on one paired node, or on all of them.

        Args:
            name: A paired node, or None for every one.

        Returns:
            int: 0, or 1 when the node is not paired.
        """
        records = [NodePairing.find(name)] if name else NodePairing.paired()
        if name and records[0] is None:
            print(f"❌ {name} is not a paired node.")
            return 1
        if not records:
            print("No node is paired (`puffin-admin node add <node>`).")
            return 0
        for record in records:
            answer = NodePairing.run(record, "job-list")
            if answer.returncode != 0:
                print(f"{record['name']}: not answering ({answer.stderr.strip()[-100:]})")
                continue
            listed = [json.loads(line) for line in answer.stdout.splitlines() if line.startswith("{")]
            if not listed:
                print(f"{record['name']}: no jobs")
            for job in listed:
                branch = f"  branch {job['branch']}" if job.get("branch") else ""
                print(f"{record['name']}  {job['id']}  {job['status']:<9}  {' '.join(job['command'])[:60]}{branch}")
        return 0

    @classmethod
    def logs(cls, job_id: str) -> int:
        """Shows a job's output again, or continues it if the job is still running."""
        return cls._ask(job_id, f"job-logs {job_id}", capture=False)

    @classmethod
    def cancel(cls, job_id: str) -> int:
        """Stops a running job."""
        return cls._ask(job_id, f"job-cancel {job_id}", capture=False)

    @classmethod
    def fetch(cls, job_id: str) -> int:
        """Brings a job's result branch into the repository it was sent from."""
        return 0 if cls._fetch_branch(job_id, quiet_when_absent=False) else 1

    # -- pieces ----------------------------------------------------------------------------------

    @classmethod
    def _ask(cls, job_id: str, request: str, capture: bool) -> int:
        sent = cls._sent(job_id)
        record = NodePairing.find(sent["node"]) if sent else None
        if record is None:
            print(f"❌ No job {job_id} was sent from this machine to a node that is still paired.")
            return 1
        return NodePairing.run(record, request, capture=capture).returncode

    @classmethod
    def _fetch_branch(cls, job_id: str, quiet_when_absent: bool) -> bool:
        sent = cls._sent(job_id)
        record = NodePairing.find(sent["node"], browse=False) if sent else None
        if record is None:
            if not quiet_when_absent:
                print(f"❌ No job {job_id} was sent from this machine to a node that is still paired.")
            return False
        branch = f"job/{job_id}"
        fetched = subprocess.run(["git", "-C", sent["repo"], "fetch", "--quiet", cls.git_url(record, sent["slug"]),
                                  f"refs/heads/{branch}:refs/heads/{branch}"],
                                 env=cls.git_environment(record), capture_output=True, text=True, check=False)
        if fetched.returncode != 0:
            if not quiet_when_absent:
                print(f"No result branch for {job_id}: the job changed no file, or has not finished.")
            return False
        print(f"📥 The job's changes are on branch {branch} (review: git diff HEAD...{branch}).")
        return True

    @classmethod
    def _remember(cls, job_id: str, record: Dict[str, Any], repo: str, slug: str) -> None:
        cls.sent_dir().mkdir(parents=True, exist_ok=True)
        (cls.sent_dir() / f"{job_id}.json").write_text(
            json.dumps({"id": job_id, "node": record["node"], "repo": repo, "slug": slug}, indent=2) + "\n")

    @classmethod
    def _sent(cls, job_id: str) -> Optional[Dict[str, Any]]:
        if not JOB_ID.fullmatch(job_id or ""):
            return None
        try:
            return json.loads((cls.sent_dir() / f"{job_id}.json").read_text())
        except (OSError, ValueError):
            return None

    @classmethod
    def _git(cls, cwd: str, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", cwd, *arguments], capture_output=True, text=True, check=False)
