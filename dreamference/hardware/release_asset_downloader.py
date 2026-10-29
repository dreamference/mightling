"""Fetches a model Dreamference hosts as GitHub release assets into the Hugging Face hub cache.

The registry names such a model's files, each with its release asset's URL and SHA-256
(RELEASE_HOSTED_MODELS). They land as a download from the Hub would put them: the file in
`blobs/<sha256>`, linked from `snapshots/<revision>/<name>`, so the launch builders' snapshot
paths, the memory gates and `node sync-model`, which checks every blob against its name, need
nothing of their own. A file is streamed to `<sha256>.<id>.incomplete` (which node sync never sends),
hashed on the way, and moved into place only when the hash matches its pin.
"""

import hashlib
import http.client
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Final

from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

CHUNK_BYTES: Final[int] = 8 * 1024 * 1024
TIMEOUT_SECONDS: Final[int] = 60
USER_AGENT: Final[str] = "mightling-model-download"


class ReleaseAssetDownloader:
    """Downloads and checks the release-hosted models of the registry."""

    @classmethod
    def folder(cls, repo: str) -> Path:
        """The model's folder in the hub cache, `models--<owner>--<name>`.

        Args:
            repo (str): The model's repository-style name.

        Returns:
            Path: The folder.
        """
        return ModelDownloader.get_hf_cache_dir() / ("models--" + repo.replace("/", "--"))

    @classmethod
    def is_present(cls, model_key: str) -> bool:
        """Reports whether every file of the model's snapshot is on disk, linked to its pinned blob.

        Args:
            model_key (str): A release-hosted model's alias or name.

        Returns:
            bool: True when the snapshot is complete at its pins.
        """
        source = ModelMatrixRegistry.get_release_source(model_key)
        if source is None:
            return False
        folder = cls.folder(ModelMatrixRegistry.resolve_hf_repo(model_key))
        snapshot = folder / "snapshots" / source["revision"]
        for name, pin in source["files"].items():
            link = snapshot / name
            if not link.is_symlink() or link.readlink().name != pin["sha256"]:
                return False
            if not (folder / "blobs" / pin["sha256"]).is_file():
                return False
        return True

    @classmethod
    def ensure(cls, model_key: str) -> bool:
        """Makes the model's snapshot complete at its pins, fetching only what is missing.

        Args:
            model_key (str): A release-hosted model's alias or name.

        Returns:
            bool: True when the snapshot is complete at its pins afterwards.
        """
        if cls.is_present(model_key):
            cls._say(
                f"✅ Model '{model_key}' found in local cache (SHA-256 pinned release assets)."
            )
            return True
        return cls.download(model_key)

    @classmethod
    def download(cls, model_key: str) -> bool:
        """Fetches the model's files that are not on disk yet and links its snapshot.

        Args:
            model_key (str): A release-hosted model's alias or name.

        Returns:
            bool: True when the snapshot is complete at its pins afterwards.
        """
        source = ModelMatrixRegistry.get_release_source(model_key)
        if source is None:
            cls._say(f"❌ {model_key} is not a model Mightling hosts as release assets.")
            return False
        repo = ModelMatrixRegistry.resolve_hf_repo(model_key)
        folder = cls.folder(repo)
        blobs = folder / "blobs"
        snapshot = folder / "snapshots" / source["revision"]
        blobs.mkdir(parents=True, exist_ok=True)
        snapshot.mkdir(parents=True, exist_ok=True)
        for name, pin in source["files"].items():
            blob = blobs / pin["sha256"]
            if not blob.is_file() and not cls._fetch(pin, blob):
                return False
            link = snapshot / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(Path("..") / ".." / "blobs" / pin["sha256"])
        cls._say(
            f"✅ {repo} at {source['revision']} ({len(source['files'])} file(s), SHA-256 checked)"
        )
        return True

    @classmethod
    def _fetch(cls, pin: dict[str, Any], blob: Path) -> bool:
        """Streams one asset into its blob, keeping it only when its SHA-256 matches the pin.

        Args:
            pin (dict[str, Any]): The file's `url` and `sha256`.
            blob (Path): Where the checked file goes.

        Returns:
            bool: True when the blob is in place.
        """
        digest = hashlib.sha256()
        size = 0
        cls._say(f"📥 {pin['url']}")
        request = urllib.request.Request(pin["url"], headers={"User-Agent": USER_AGENT})
        # A name of this fetch's own: two downloads of one model at once each stream to theirs.
        try:
            handle, name = tempfile.mkstemp(
                dir=blob.parent, prefix=blob.name + ".", suffix=".incomplete"
            )
        except OSError as error:
            cls._say(f"❌ Could not download {pin['url']}: {error}")
            return False
        partial = Path(name)
        try:
            with (
                os.fdopen(handle, "wb") as out,
                urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response,
            ):
                for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
        # A body cut short raises IncompleteRead, an HTTPException rather than an OSError.
        except (OSError, http.client.HTTPException) as error:
            partial.unlink(missing_ok=True)
            cls._say(f"❌ Could not download {pin['url']}: {error}")
            return False
        if digest.hexdigest() != pin["sha256"]:
            partial.unlink(missing_ok=True)
            cls._say(
                f"❌ {pin['url']} does not match its pinned SHA-256 ({size} bytes, got {digest.hexdigest()})."
            )
            return False
        # mkstemp makes it readable by its owner alone; a blob the Hub writes is readable by all.
        partial.chmod(0o644)
        partial.replace(blob)
        return True

    @classmethod
    def _say(cls, message: str) -> None:
        """One line of the download's progress for the person running `model download` or `server start`."""
        print(message, flush=True)  # noqa: T201 - console-facing, called from the CLI's commands
