"""
Copying a model's files to another node (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.2):
`ling-admin node sync-model <node> <model>`.

A second Spark that is to serve the model this one already has need not download tens of gigabytes
again: the Hugging Face cache folders of the model and of its drafter are sent over the pairing as
one tar stream, each at the commit the model registry pins (its snapshot and the blobs that snapshot
links to; another revision in the sender's cache stays behind), which over the QSFP link
(`--address`) runs at the link's speed. The receiving node decides everything that lands on its
disk: only the cache folders of a key of its own model matrix are accepted, nothing may point
outside them, every content-addressed weight file is checked against its name, and files it already
has are kept. It writes into a staging folder and moves files into place only once all of them
checked out, so an interrupted copy leaves nothing half-written in the cache.
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
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Final, List, Optional

GIB: Final[int] = 1024 ** 3

# Free space a copy leaves on the receiving disk.
DISK_MARGIN: Final[int] = 10 * GIB

# A large file in the hub cache is stored under the SHA-256 of its content.
LFS_BLOB: Final[re.Pattern] = re.compile(r"[0-9a-f]{64}")

# The parts of a hub cache folder a copy may carry links in, and the parent a link may climb to.
BLOBS: Final[str] = "blobs"
SNAPSHOTS: Final[str] = "snapshots"
PARENT: Final[str] = ".."


class NodeModelSync:
    """Both ends of a model copy."""

    @classmethod
    def repos(cls, model_key: str) -> List[str]:
        """
        Args:
            model_key: A key of the model matrix.

        Returns:
            List[str]: The Hugging Face repositories the model is served from: the checkpoint,
            the drafter its recipe names, if any, and the assets its engine reads (get_engine_assets).
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        repos = [ModelMatrixRegistry.resolve_hf_repo(model_key), ModelMatrixRegistry.get_speculative_draft_repo(model_key)]
        repos += [asset["repo"] for asset in ModelMatrixRegistry.get_engine_assets(model_key)]
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
    def members(cls, hub: Path, repo: str) -> list[Path] | None:
        """The paths of the hub cache that carry a repository at the commit the registry pins.

        Args:
            hub: The hub cache.
            repo: A Hugging Face repository id.

        Returns:
            list[Path] | None: For a pinned repository, its snapshot of that commit and the
            blobs the snapshot links to; for one no recipe pins, its whole cache folder. None when
            what would be sent is not here (for a pinned one, a cache holding only another
            revision, which is not the checkpoint the recipe was measured on, or a snapshot
            linking to a blob that is missing, as a download that broke off leaves it).
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        folder = hub / cls.folder_name(repo)
        pin = ModelMatrixRegistry.pinned_revision(repo)
        if not pin:
            return [folder] if (folder / "snapshots").is_dir() else None
        snapshot = folder / "snapshots" / pin
        if not snapshot.is_dir():
            return None
        blobs_dir = (folder / "blobs").resolve()
        blobs = set()
        for link in snapshot.rglob("*"):
            if link.is_symlink():
                target = link.resolve()
                if not target.exists():
                    return None
                if target.parent == blobs_dir:
                    blobs.add(folder / "blobs" / target.name)
        return [snapshot, *sorted(blobs)]

    @classmethod
    def size(cls, paths: list[Path]) -> int:
        """
        Args:
            paths: Cache folders, or files and folders in them.

        Returns:
            int: Bytes of the regular files in them that would be sent (links are free, and an
            unfinished download is not sent).
        """
        total = 0
        for root in paths:
            for path in [root, *root.rglob("*")] if root.is_dir() else [root]:
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
            # An address given by hand is used as it is; otherwise every connection resolves the
            # node on the network first (NodePairing.resolve).
            record = dict(record, address=address, address_fixed=True)
        hub = cls.hub()
        repos = cls.repos(model_key)
        found = {repo: cls.members(hub, repo) for repo in repos}
        missing = [cls._described(repo) for repo, paths in found.items() if paths is None]
        if missing:
            print(f"❌ This machine does not have {', '.join(missing)}: `ling-admin model download {model_key}` "
                  f"here first, or on {record['name']}.")
            return 1
        paths = [path for repo in repos for path in found[repo] or []]
        size = cls.size(paths)
        try:
            command = NodePairing.ssh_command(record, f"model-receive {model_key} {size}")
        except LookupError as error:
            print(f"❌ {error}")
            return 1
        print(f"📤 Copying {model_key} ({size / GIB:.1f} GiB, {len(repos)} folder(s)) to {record['name']} "
              f"at {command[-2].partition('@')[2]}...", flush=True)
        started = time.time()
        tar = subprocess.Popen(["tar", "-C", str(hub), "-cf", "-", "--exclude=*.incomplete",
                                *[str(path.relative_to(hub)) for path in paths]], stdout=subprocess.PIPE)
        ssh = subprocess.Popen(command, stdin=tar.stdout)
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

    @classmethod
    def _described(cls, repo: str) -> str:
        """Names a repository's cache folder, at its pinned commit if it has one."""
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        pin = ModelMatrixRegistry.pinned_revision(repo)
        return f"{cls.folder_name(repo)} at {pin[:12]}" if pin else cls.folder_name(repo)

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
            problem = cls._unpack(sys.stdin.buffer, staging, allowed, size)
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
    def _unpack(cls, stream: Any, staging: Path, allowed: set, size: int) -> str | None:
        """Extracts the stream into `staging`; returns what was wrong with it, if anything.

        `size` is what the sender declared, counted as `size()` counts it: the free-space check was
        made for that much, so a copy that carries more is refused before it can fill the disk.
        """
        root = Path(staging).resolve()
        carried = 0
        try:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                for member in archive:
                    carried += member.size if member.isreg() else 0
                    refusal = (
                        f"the copy carried more than the {size} bytes it declared"
                        if carried > size
                        else None
                        if cls._stays_in_folder(member, root, allowed)
                        else f"the copy carried {member.name}, which is not a folder of this model"
                    )
                    if refusal:
                        return refusal
                    # `data` also refuses devices and odd modes.
                    archive.extract(member, staging, filter="data")
        except (tarfile.TarError, OSError) as error:
            return f"the copy was cut short or malformed ({error})"
        return None

    @classmethod
    def _stays_in_folder(cls, member: tarfile.TarInfo, root: Path, allowed: set) -> bool:
        """Whether a member of a copy lands inside one of the model's folders.

        The `data` filter alone keeps a member inside the staging folder, not inside the model's
        own folder: a link to `..` there, then a file through it, once put a folder of the
        client's choosing beside the model's, and `_merge` moved it into the cache.

        Args:
            member: The member about to be extracted.
            root: The staging folder, resolved.
            allowed: The names of the model's folders.

        Returns:
            bool: True for a folder, file or link of the cache's own shape: the top is one of the
            model's folders, no `..`, nothing below a link, and links only from a snapshot to a
            blob of the same folder (`snapshots/<revision>/…/<file> -> ../../blobs/<file>`).
        """
        parts = PurePosixPath(member.name).parts
        if not parts or parts[0] not in allowed or PARENT in parts or member.islnk():
            return False
        top, *below = parts
        if not below:
            return member.isdir()
        kind, *rest = below
        if kind == BLOBS and not (member.isreg() and len(rest) == 1 if rest else member.isdir()):
            return False
        if member.issym():
            target = PurePosixPath(member.linkname).parts
            if (
                kind != SNAPSHOTS
                or not rest[1:]
                or target[:-1] != (PARENT,) * len(rest) + (BLOBS,)
                or target[-1] in (".", PARENT)
            ):
                return False
        place = root.joinpath(*parts)
        return place.parent.resolve().is_relative_to(root / top) and not place.is_symlink()

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
