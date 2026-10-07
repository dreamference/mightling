"""
Copying a model's files to another node (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.2):
`ling-admin node sync-model <node> <model>`.

A second Spark that is to serve the model this one already has need not download tens of
gigabytes again: the Hugging Face cache folders of the model and of its drafter are sent over the
pairing as one tar stream, which over the QSFP link (`--address`) runs at the link's speed. The
receiving node decides everything that lands on its disk: only the cache folders of a key of its
own model matrix are accepted, nothing may point outside them, every content-addressed weight file
is checked against its name, and files it already has are kept. It writes into a staging folder
and moves files into place only once all of them checked out, so an interrupted copy leaves
nothing half-written in the cache.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

GIB: Final[int] = 1024 ** 3

# Free space a copy leaves on the receiving disk.
DISK_MARGIN: Final[int] = 10 * GIB

# A large file in the hub cache is stored under the SHA-256 of its content.
LFS_BLOB: Final[re.Pattern] = re.compile(r"[0-9a-f]{64}")


class NodeModelSync:
    """Both ends of a model copy."""

    @classmethod
    def repos(cls, model_key: str) -> List[str]:
        """
        Args:
            model_key: A key of the model matrix.

        Returns:
            List[str]: The Hugging Face repositories the model is served from: the checkpoint,
            and the drafter its recipe names, if any.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        repos = [ModelMatrixRegistry.resolve_hf_repo(model_key), ModelMatrixRegistry.get_speculative_draft_repo(model_key)]
        return [repo for repo in repos if repo and "/" in repo]

    @classmethod
    def folder_name(cls, repo: str) -> str:
        """
        Args:
            repo: A Hugging Face repository id.

        Returns:
            str: Its folder in the hub cache, `models--<owner>--<name>`.
        """
        return "models--" + repo.replace("/", "--")

    @classmethod
    def hub(cls) -> Path:
        """
        Returns:
            Path: This machine's Hugging Face hub cache.
        """
        from dreamference.hardware.model_downloader import ModelDownloader
        return ModelDownloader.get_hf_cache_dir()

    @classmethod
    def size(cls, folders: List[Path]) -> int:
        """
        Args:
            folders: Cache folders.

        Returns:
            int: Bytes of the regular files in them that would be sent (links are free, and an
            unfinished download is not sent).
        """
        total = 0
        for folder in folders:
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink() and not path.name.endswith(".incomplete"):
                    total += path.stat().st_size
        return total

    # -- the sender ------------------------------------------------------------------------------

    @classmethod
    def sync(cls, name: str, model_key: str, address: Optional[str] = None) -> int:
        """
        Sends a model's cache folders to a paired node.

        Args:
            name: The paired node.
            model_key: A key of the model matrix.
            address: Reach the node at this address instead of the advertised one (its QSFP
                link's). The host key is pinned to the node, so any address of it is checked.

        Returns:
            int: 0 when the node has the files, 1 otherwise.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        from dreamference.node.node_pairing import NodePairing
        if model_key not in ModelMatrixRegistry.MATRIX:
            print(f"❌ {model_key} is not a key of the model matrix (`ling-admin model list`).")
            return 1
        record: Optional[Dict[str, Any]] = NodePairing.find(name)
        if record is None:
            print(f"❌ {name} is not a paired node. Pair once with: ling-admin node add {name}")
            return 1
        if address:
            record = dict(record, address=address)
        hub = cls.hub()
        folders = [hub / cls.folder_name(repo) for repo in cls.repos(model_key)]
        missing = [folder.name for folder in folders if not (folder / "snapshots").is_dir()]
        if missing:
            print(f"❌ This machine does not have {', '.join(missing)}: `ling-admin model download {model_key}` "
                  f"here first, or on {record['name']}.")
            return 1
        size = cls.size(folders)
        print(f"📤 Copying {model_key} ({size / GIB:.1f} GiB, {len(folders)} folder(s)) to {record['name']} "
              f"at {record['address']}...", flush=True)
        started = time.time()
        tar = subprocess.Popen(["tar", "-C", str(hub), "-cf", "-", "--exclude=*.incomplete",
                                *[folder.name for folder in folders]], stdout=subprocess.PIPE)
        ssh = subprocess.Popen(NodePairing.ssh_command(record, f"model-receive {model_key} {size}"),
                               stdin=tar.stdout)
        tar.stdout.close()
        code = ssh.wait()
        tar.wait()
        if code != 0 or tar.returncode not in (0, None):
            print(f"❌ The copy did not complete (node exit {code}, tar exit {tar.returncode}); "
                  f"{record['name']}'s cache is as it was.")
            return 1
        seconds = max(1.0, time.time() - started)
        print(f"✅ {record['name']} has {model_key} ({size / GIB / seconds * 8:.1f} Gbit/s over {seconds:.0f} s). "
              f"Serve it there with: ling-admin node set {record['name']} --model {model_key}")
        return 0

    # -- the node --------------------------------------------------------------------------------

    @classmethod
    def receive(cls, model_key: str, size: int) -> int:
        """
        Receives a model's cache folders on standard input, as a tar stream from `sync`.

        Args:
            model_key: The model being sent: a key of this node's own matrix, which decides the
                folders that may arrive.
            size: The bytes the sender will send, for the disk check.

        Returns:
            int: 0 when every file is in place; 1 when the copy failed; 2 when it was refused.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        from dreamference.node.node_serve import MODEL_KEY
        if not MODEL_KEY.fullmatch(model_key) or model_key not in ModelMatrixRegistry.MATRIX:
            print(f"❌ {model_key!r} is not a model this node knows.", file=sys.stderr)
            return 2
        hub = cls.hub()
        hub.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(hub).free
        if free < size + DISK_MARGIN:
            print(f"❌ This node has {free / GIB:.0f} GiB free; the copy needs {(size + DISK_MARGIN) / GIB:.0f} "
                  f"with the margin.", file=sys.stderr)
            return 2
        allowed = {cls.folder_name(repo) for repo in cls.repos(model_key)}
        staging = hub / f".mightling-sync-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            problem = cls._unpack(sys.stdin.buffer, staging, allowed)
            problem = problem or cls._verify(staging)
            if problem:
                print(f"❌ {problem}; nothing was added to the cache.", file=sys.stderr)
                return 1
            added, kept = cls._merge(staging, hub)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        print(json.dumps({"model": model_key, "added": added, "already_here": kept}))
        return 0

    @classmethod
    def _unpack(cls, stream: Any, staging: Path, allowed: set) -> Optional[str]:
        """Extracts the stream into `staging`; returns what was wrong with it, if anything."""
        try:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                for member in archive:
                    top = member.name.lstrip("./").split("/", 1)[0]
                    if top not in allowed:
                        return f"the copy carried {member.name}, which is not a folder of this model"
                    # `data` refuses absolute names, `..`, devices, and links that leave the folder.
                    archive.extract(member, staging, filter="data")
        except (tarfile.TarError, OSError) as error:
            return f"the copy was cut short or malformed ({error})"
        return None

    @classmethod
    def _verify(cls, staging: Path) -> Optional[str]:
        """Checks every content-addressed weight file against its name."""
        for blob in staging.glob("*/blobs/*"):
            if blob.is_file() and not blob.is_symlink() and LFS_BLOB.fullmatch(blob.name):
                digest = hashlib.sha256()
                with open(blob, "rb") as handle:
                    for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != blob.name:
                    return f"{blob.relative_to(staging)} does not match its checksum"
        return None

    @classmethod
    def _merge(cls, staging: Path, hub: Path) -> tuple:
        """
        Moves every staged file and link into the cache where nothing is yet.

        Returns:
            tuple: (files added, files already there).
        """
        added = kept = 0
        for path in sorted(staging.rglob("*"), key=lambda p: (len(p.parts), str(p))):
            if path.is_dir() and not path.is_symlink():
                continue
            target = hub / path.relative_to(staging)
            if target.exists() or target.is_symlink():
                kept += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            added += 1
        return added, kept
