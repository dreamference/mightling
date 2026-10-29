"""The digest that pins an adapter to the exact base checkpoint (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §5.2).

`sha256` over a canonical listing of the base's weight and configuration files: one line per file,
`<sha256>  <path>`, sorted by path, covering `*.safetensors`, `config.json`, `tokenizer*.json` and
the chat template, at the pinned revision. A weight file in the hub cache is a link to a blob named
by the sha256 of its content (Hugging Face's LFS object id, which `NodeModelSync` already relies
on), so the 21 GB of weights are never read: their hashes are their link targets. The small files
are hashed. The result is cached by (repository, revision, the files present), so it is computed
once per revision and again only when a download adds a file.
"""

import hashlib
import json
import os
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Final

COVERED: Final[tuple[str, ...]] = (
    "*.safetensors",
    "config.json",
    "tokenizer*.json",
    "chat_template*",
    "*.jinja",
)
LFS_BLOB: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
CACHE_FILE: Final[str] = "base-digests.json"
CHUNK: Final[int] = 1 << 20


class AdapterBaseDigest:
    """Computes and caches a base checkpoint's digest."""

    @classmethod
    def cache_path(cls) -> Path:
        """Where computed digests are kept.

        Returns:
            Path: `~/.cache/dreamference/adapters/base-digests.json`, under the current home.
        """
        return Path("~/.cache/dreamference/adapters").expanduser() / CACHE_FILE

    @classmethod
    def listing(cls, snapshot: Path, hashed: bool = True) -> list[str]:
        """The canonical listing the digest is taken over.

        Args:
            snapshot: The base's snapshot folder.
            hashed: False puts `-` for every hash, which reads no file.

        Returns:
            list[str]: `<sha256>  <path>` lines, sorted by path.
        """
        lines = []
        for path in sorted(snapshot.rglob("*"), key=lambda p: p.relative_to(snapshot).as_posix()):
            relative = path.relative_to(snapshot).as_posix()
            if path.is_dir() or not any(fnmatch(path.name, pattern) for pattern in COVERED):
                continue
            lines.append(f"{cls.file_hash(path) if hashed else '-'}  {relative}")
        return lines

    @classmethod
    def file_hash(cls, path: Path) -> str:
        """One file's sha256: its blob's name when the cache names it so, its content otherwise.

        Args:
            path: A file of the snapshot (usually a link into `blobs/`).

        Returns:
            str: The hex digest.
        """
        if path.is_symlink():
            blob = path.readlink().name
            if LFS_BLOB.fullmatch(blob):
                return blob
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def compute(cls, snapshot: Path) -> str:
        """The digest of a snapshot, computed now.

        Args:
            snapshot: The base's snapshot folder.

        Returns:
            str: `sha256:<hex>`.
        """
        text = "\n".join(cls.listing(snapshot)) + "\n"
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def of(cls, repo: str, revision: str, snapshot: Path) -> str:
        """The digest of a base, from the cache or computed once and cached.

        Args:
            repo: The base's repository.
            revision: The pinned commit.
            snapshot: Its snapshot folder.

        Returns:
            str: `sha256:<hex>`.
        """
        # The files present are part of the key: a snapshot still being downloaded lacks some,
        # and its digest must not stand for the complete one.
        names = "\n".join(line.split("  ", 1)[1] for line in cls.listing(snapshot, hashed=False))
        key = f"{repo}@{revision}#{hashlib.sha256(names.encode('utf-8')).hexdigest()[:16]}"
        cache = cls._read_cache()
        cached = cache.get(key)
        if isinstance(cached, str):
            return cached
        digest = cls.compute(snapshot)
        cache[key] = digest
        cls._write_cache(cache)
        return digest

    @classmethod
    def _read_cache(cls) -> dict[str, object]:
        """Reads the cache.

        Returns:
            dict[str, object]: Key to digest; empty when missing or unreadable.
        """
        try:
            record = json.loads(cls.cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    @classmethod
    def _write_cache(cls, cache: dict[str, object]) -> None:
        """Replaces the cache, atomically; a cache that cannot be written is only recomputed.

        Args:
            cache: Key to digest.
        """
        path = cls.cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            staging.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            Path(staging).replace(path)
        except OSError:
            return
