"""Copying signed adapters to a paired node (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §6.4).

`ling-admin node sync-model <node> <model>` sends the model's cache folders and then, over the
same SSH forced command, the cache folder of every installed adapter that Dreamference signed and
that was made for that model. A third-party adapter is never copied: its install needs a person's
typed confirmation on each node. The receiving node decides everything, as it does for the model:
the folder must be the named repository's, every content-addressed file must match its name, and
the adapter must pass its own checks there (the signature against the node's own key list, every
file against the manifest) before anything is moved into its cache and recorded. A node that serves
another base keeps the files and lists the adapter as `base mismatch`. Nothing is ever removed on
the other side.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

from dreamference.hardware.adapter_catalog import COMMIT, REPO
from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.adapter_manifest import NAME
from dreamference.hardware.adapter_store import TRUST_DREAMFERENCE, AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier
from dreamference.node.node_model_sync import DISK_MARGIN, GIB, NodeModelSync
from dreamference.node.node_serve import RECEIVE_OPERATION

RECEIVE_ARGUMENTS: Final[int] = 4


class NodeAdapterSync:
    """Both ends of an adapter copy."""

    @classmethod
    def signed_for(cls, model_key: str) -> list[dict[str, object]]:
        """The installed adapters a sync of a model carries.

        Args:
            model_key: The model being synced.

        Returns:
            list[dict[str, object]]: Entries signed by Dreamference and made for that model.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

        repo = ModelMatrixRegistry.resolve_hf_repo(model_key)
        return [
            entry
            for _, entry in sorted(AdapterStore.entries().items())
            if entry.get("trust") == TRUST_DREAMFERENCE and entry.get("base_repo") == repo
        ]

    @classmethod
    def after(
        cls, model_code: int, name: str, model_key: str, address: str | None
    ) -> tuple[int, list[str]]:
        """Follows a model's copy with its adapters', when the model's copy succeeded.

        Args:
            model_code: `NodeModelSync.sync`'s exit code.
            name: The paired node.
            model_key: The model synced.
            address: The address `sync` was given, if any.

        Returns:
            tuple[int, list[str]]: The exit code of the whole sync and the lines to print.
        """
        if model_code != 0:
            return model_code, []
        return cls.send_to(name, model_key, address)

    @classmethod
    def send_to(
        cls, name: str, model_key: str, address: str | None = None
    ) -> tuple[int, list[str]]:
        """Sends a model's signed adapters to a paired node, after `NodeModelSync.sync` sent the model.

        Args:
            name: The paired node.
            model_key: The model just synced.
            address: The address `sync` was given, if any (the node's QSFP link's).

        Returns:
            tuple[int, list[str]]: As `send`.
        """
        from dreamference.node.node_pairing import NodePairing

        if not cls.signed_for(model_key):
            return 0, []
        record = NodePairing.find(name)
        if record is None:
            return 1, [f"❌ {name} is not a paired node."]
        if address:
            record = dict(record, address=address, address_fixed=True)
        return cls.send(record, model_key)

    @classmethod
    def send(cls, record: dict[str, Any], model_key: str) -> tuple[int, list[str]]:
        """Sends the signed adapters of a model to a paired node, one after the other.

        Args:
            record: The paired node's record (`NodePairing.find`).
            model_key: The model just synced.

        Returns:
            tuple[int, list[str]]: 0 when every adapter arrived (or there was none), 1 otherwise;
            and a line per adapter, for the command to print.
        """
        from dreamference.node.node_pairing import NodePairing

        lines: list[str] = []
        failed = 0
        hub = NodeModelSync.hub()
        for entry in cls.signed_for(model_key):
            name, repo, commit = str(entry["name"]), str(entry["repo"]), str(entry["commit"])
            folder = hub / NodeModelSync.folder_name(repo)
            size = NodeModelSync.size([folder])
            try:
                command = NodePairing.ssh_command(
                    record, f"{RECEIVE_OPERATION} {name} {repo} {commit} {size}"
                )
            except LookupError as error:
                return 1, [*lines, f"❌ {error}"]
            tar = subprocess.Popen(
                ["tar", "-C", str(hub), "-cf", "-", "--exclude=*.incomplete", folder.name],
                stdout=subprocess.PIPE,
            )
            ssh = subprocess.Popen(command, stdin=tar.stdout)
            if tar.stdout is not None:
                tar.stdout.close()
            code = ssh.wait()
            tar.wait()
            if code != 0 or tar.returncode not in (0, None):
                failed += 1
                lines.append(
                    f"❌ The adapter {name} did not reach {record['name']} (node exit {code})."
                )
            else:
                lines.append(
                    f"✅ {record['name']} has the adapter {name} ({size / GIB:.2f} GiB), checked there."
                )
        return (1 if failed else 0), lines

    @classmethod
    def receive(cls, arguments: list[str]) -> int:
        """Receives one adapter's cache folder on standard input and installs it if it checks out.

        Args:
            arguments: `<name> <repo> <commit> <size>`, as the sender's request named them.

        Returns:
            int: 0 when it is installed; 1 when the copy or a check failed; 2 when refused.
        """
        if len(arguments) != RECEIVE_ARGUMENTS or not arguments[3].isdigit():
            cls._say(f"❌ {RECEIVE_OPERATION} takes <name> <repo> <commit> <size>.")
            return 2
        name, repo, commit, size = arguments[0], arguments[1], arguments[2], int(arguments[3])
        if not (NAME.fullmatch(name) and REPO.fullmatch(repo) and COMMIT.fullmatch(commit)):
            cls._say("❌ That is not an adapter name, a repository and a commit.")
            return 2
        hub = NodeModelSync.hub()
        hub.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(hub).free < size + DISK_MARGIN:
            cls._say("❌ This node has too little free space for the adapter.")
            return 2
        folder_name = NodeModelSync.folder_name(repo)
        staging = hub / f".mightling-adapter-sync-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            problem = NodeModelSync._unpack(sys.stdin.buffer, staging, {folder_name}, size)
            problem = problem or NodeModelSync._verify(staging)
            problem = problem or cls._check(staging / folder_name / "snapshots" / commit, name)
            if problem:
                cls._say(f"❌ {problem}; nothing was added.")
                return 1
            NodeModelSync._merge(staging, hub)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return cls._record(name, repo, commit, hub / folder_name / "snapshots" / commit)

    @classmethod
    def _check(cls, snapshot: Path, name: str) -> str | None:
        """The adapter's own checks, on the staged copy, with this node's key list.

        Args:
            snapshot: The staged snapshot folder.
            name: The name the sender gave.

        Returns:
            str | None: The reason it is refused, or None.
        """
        try:
            manifest = AdapterVerifier.check_files(snapshot, signed=True)
        except ValueError as error:
            return str(error)
        if manifest.name != name:
            return f"the manifest names {manifest.name}, not {name}"
        return None

    @classmethod
    def _record(cls, name: str, repo: str, commit: str, snapshot: Path) -> int:
        """Records a received adapter and says whether it fits the base this node serves.

        Args:
            name: Its name.
            repo: Its repository.
            commit: Its commit.
            snapshot: Its snapshot folder in this node's cache.

        Returns:
            int: 0, or 1 when the merged copy no longer verifies.
        """
        from dreamference.config import DreamferenceConfig

        try:
            manifest = AdapterVerifier.check_files(snapshot, signed=True)
        except ValueError as error:
            cls._say(f"❌ {error}")
            return 1
        AdapterStore.put(
            AdapterInstaller.entry(manifest, repo, commit, snapshot, TRUST_DREAMFERENCE)
        )
        config = DreamferenceConfig()
        problem = AdapterVerifier.base_problem(
            manifest, AdapterVerifier.served_base(config.vllm_host, config.model)
        )
        # The sender reads this line; the SSH session's standard output is the protocol.
        sys.stdout.write(
            json.dumps({"adapter": name, "installed": True, "base": problem or "matches"}) + "\n"
        )
        return 0

    @classmethod
    def _say(cls, line: str) -> None:
        """Writes a refusal where the sender shows it: the SSH session's standard error.

        Args:
            line: The message.
        """
        sys.stderr.write(line + "\n")
