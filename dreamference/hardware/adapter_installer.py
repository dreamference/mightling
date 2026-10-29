"""Installing and removing adapters (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §6.2, §6.3).

`install` is the only adapter step that reaches the network, and only when a person runs it. In
order, each step stopping the install: resolve the name in the release's catalogue (or take
`--repo <org>/<repo>@<commit>`, never a branch); fetch `manifest.json` and its signature alone;
check the manifest's pin, the manifest itself, the signature (unless third-party, which a person
confirms by typing the name) and the base it was made for; then fetch exactly the files the
manifest lists (`allow_patterns`, so a file added to the repository later, or a `.py`, never
reaches the disk) and check every one of them; only then record it. The files stay where the
model's are, as a snapshot of the Hugging Face hub cache at the pinned commit.
"""

import datetime
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Final

from dreamference.hardware.adapter_catalog import AdapterCatalog
from dreamference.hardware.adapter_manifest import (
    MANIFEST_FILE,
    NAME,
    SIGNATURE_FILE,
    AdapterManifest,
)
from dreamference.hardware.adapter_store import TRUST_DREAMFERENCE, TRUST_THIRD_PARTY, AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier
from dreamference.hardware.model_downloader import ModelDownloader

GIB: Final[int] = 1024**3
DISK_MARGIN: Final[int] = 2 * GIB
VERSION_SEPARATOR: Final[str] = "@"


class AdapterInstaller:
    """`ling-admin adapter install`, `remove` and `verify`."""

    @classmethod
    def install(
        cls,
        spec: str | None,
        repo: str | None,
        base: dict[str, str] | None,
        confirm: Callable[[AdapterManifest], bool] | None = None,
        token: str | None = None,
    ) -> dict[str, object]:
        """Installs an adapter.

        Args:
            spec: `<name>[@<version>]` from the catalogue, or None with `repo`.
            repo: `<org>/<repo>@<commit>`, or None with `spec`.
            base: The served base (`AdapterVerifier.served_base()`).
            confirm: For a third-party adapter, asks the person and says whether they typed its
                name; None for an adapter that must be signed by Dreamference.
            token: A Hugging Face token, for a private repository.

        Returns:
            dict[str, object]: The entry recorded in the installed set.

        Raises:
            ValueError: With the reason nothing was installed.
        """
        source, commit, pin = cls._resolve(spec, repo, base)
        folder = cls.fetch(source, commit, [MANIFEST_FILE, SIGNATURE_FILE], token)
        third_party = confirm is not None
        manifest = AdapterVerifier.check_manifest(
            folder, signed=not third_party, manifest_sha256=pin
        )
        problem = AdapterVerifier.base_problem(manifest, base)
        if problem:
            raise ValueError(problem)
        cls._check_name(manifest, source)
        if confirm is not None and not confirm(manifest):
            raise ValueError("not confirmed; nothing was installed")
        folder = cls.fetch(
            source, commit, [MANIFEST_FILE, SIGNATURE_FILE, *manifest.all_files()], token
        )
        manifest = AdapterVerifier.check_files(folder, signed=not third_party, manifest_sha256=pin)
        entry = cls.entry(
            manifest,
            source,
            commit,
            folder,
            TRUST_THIRD_PARTY if third_party else TRUST_DREAMFERENCE,
        )
        AdapterStore.put(entry)
        return entry

    @classmethod
    def entry(
        cls, manifest: AdapterManifest, repo: str, commit: str, folder: Path, trust: str
    ) -> dict[str, object]:
        """The installed set's record of a verified adapter.

        Args:
            manifest: Its verified manifest.
            repo: Its repository.
            commit: The commit installed.
            folder: Its snapshot folder.
            trust: `dreamference` or `third-party`.

        Returns:
            dict[str, object]: The entry.
        """
        hub = ModelDownloader.get_hf_cache_dir()
        snapshot = folder.relative_to(hub).as_posix() if folder.is_relative_to(hub) else str(folder)
        return {
            "name": manifest.name,
            "version": manifest.version,
            "display_name": manifest.display_name,
            "description": manifest.description,
            "repo": repo,
            "commit": commit,
            "snapshot": snapshot,
            "manifest_sha256": AdapterVerifier.file_digest(folder / MANIFEST_FILE),
            "trust": trust,
            "base_repo": manifest.base_repo,
            "base_revision": manifest.base_revision,
            "base_digest": manifest.base_digest,
            "rank": manifest.rank,
            "target_modules": list(manifest.target_modules),
            "files": manifest.all_files(),
            "signer": manifest.signer,
            "installed": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @classmethod
    def folder_of(cls, entry: dict[str, object]) -> Path:
        """An installed adapter's snapshot folder on this machine.

        Args:
            entry: Its entry in the installed set.

        Returns:
            Path: The folder.
        """
        snapshot = Path(str(entry.get("snapshot") or ""))
        return snapshot if snapshot.is_absolute() else ModelDownloader.get_hf_cache_dir() / snapshot

    @classmethod
    def verify(cls, entry: dict[str, object], base: dict[str, str] | None) -> str | None:
        """Checks an installed adapter again: files, signature, configuration and base.

        Args:
            entry: Its entry in the installed set.
            base: The served base.

        Returns:
            str | None: None when it may be served; otherwise the reason.
        """
        try:
            manifest = AdapterVerifier.check_files(
                cls.folder_of(entry),
                signed=entry.get("trust") != TRUST_THIRD_PARTY,
                manifest_sha256=str(entry.get("manifest_sha256") or "") or None,
            )
        except ValueError as error:
            return str(error)
        if manifest.name != entry.get("name"):
            return f"its manifest names {manifest.name}, not {entry.get('name')}"
        return AdapterVerifier.base_problem(manifest, base)

    @classmethod
    def remove(cls, name: str) -> bool:
        """Removes an adapter: its entry, then its snapshot through the hub cache's own API.

        Args:
            name: The installed name.

        Returns:
            bool: Whether it was installed.
        """
        entry = AdapterStore.get(name)
        if entry is None:
            return False
        AdapterStore.drop(name)
        cls.delete_snapshot(str(entry.get("repo") or ""), str(entry.get("commit") or ""))
        return True

    @classmethod
    def fetch(cls, repo: str, commit: str, files: list[str], token: str | None) -> Path:
        """Downloads named files of a repository at a commit into the hub cache.

        Args:
            repo: The repository.
            commit: The commit.
            files: Exactly the files to fetch (`allow_patterns`).
            token: A Hugging Face token, or None.

        Returns:
            Path: The snapshot folder.

        Raises:
            ValueError: When the download failed.
        """
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise ValueError(
                "huggingface_hub is not installed: `pip install huggingface_hub`"
            ) from error
        try:
            folder = snapshot_download(
                repo_id=repo, revision=commit, allow_patterns=files, token=token
            )
        except (OSError, ValueError) as error:
            raise ValueError(f"the download from {repo}@{commit[:12]} failed ({error})") from error
        return Path(folder)

    @classmethod
    def delete_snapshot(cls, repo: str, commit: str) -> None:
        """Deletes one snapshot through `huggingface_hub`'s cache API, never a parent folder.

        Args:
            repo: The repository.
            commit: The commit whose snapshot goes.
        """
        try:
            from huggingface_hub import scan_cache_dir

            cache = scan_cache_dir(ModelDownloader.get_hf_cache_dir())
        except (ImportError, OSError, ValueError):
            return
        revisions = [
            revision.commit_hash
            for cached in cache.repos
            if cached.repo_id == repo
            for revision in cached.revisions
            if revision.commit_hash == commit
        ]
        if revisions:
            cache.delete_revisions(*revisions).execute()

    @classmethod
    def free_space_problem(cls, size: int) -> str | None:
        """Checks the disk before a download.

        Args:
            size: The bytes about to be fetched.

        Returns:
            str | None: The reason the disk is too full, or None.
        """
        hub = ModelDownloader.get_hf_cache_dir()
        existing = hub if hub.exists() else hub.parent if hub.parent.exists() else Path.home()
        free = shutil.disk_usage(existing).free
        if free < size + DISK_MARGIN:
            return f"the disk has {free / GIB:.1f} GiB free; the adapter needs {(size + DISK_MARGIN) / GIB:.1f}"
        return None

    @classmethod
    def _resolve(
        cls, spec: str | None, repo: str | None, base: dict[str, str] | None
    ) -> tuple[str, str, str | None]:
        """Turns the command's arguments into a repository, a commit and the manifest's pin.

        Args:
            spec: `<name>[@<version>]`, or None.
            repo: `<org>/<repo>@<commit>`, or None.
            base: The served base.

        Returns:
            tuple[str, str, str | None]: Repository, commit, and the catalogue's manifest sha256
            (None for `--repo`).

        Raises:
            ValueError: When the name is unknown, the base is another, or the disk is full.
        """
        if repo is not None:
            source, commit = AdapterCatalog.parse_repo(repo)
            # No catalogue entry gives its size before the manifest is fetched: keep the margin.
            problem = cls.free_space_problem(0)
            if problem:
                raise ValueError(problem)
            return source, commit, None
        name, _, version = (spec or "").partition(VERSION_SEPARATOR)
        entry = AdapterCatalog.find(name, version or None)
        if entry is None:
            raise ValueError(
                f"{spec} is not in this release's catalogue (`ling-admin adapter list --available`); "
                "an adapter published since installs with --repo <org>/<repo>@<commit>"
            )
        if base is not None and entry.get("base_alias") != base["key"]:
            raise ValueError(
                f"base mismatch: {name} is made for {entry.get('base_alias')}; "
                f"this machine serves {base['key']}"
            )
        size = entry.get("size")
        problem = cls.free_space_problem(size if isinstance(size, int) else 0)
        if problem:
            raise ValueError(problem)
        return str(entry["repo"]), str(entry["commit"]), str(entry["manifest_sha256"])

    @classmethod
    def _check_name(cls, manifest: AdapterManifest, repo: str) -> None:
        """Refuses a name that another repository's adapter already holds here.

        Args:
            manifest: The adapter's manifest.
            repo: Where it comes from.

        Raises:
            ValueError: When the name is taken.
        """
        if not NAME.fullmatch(manifest.name):
            raise ValueError(f"{manifest.name!r} is not a name an adapter may have")
        existing = AdapterStore.get(manifest.name)
        if existing is not None and existing.get("repo") != repo:
            raise ValueError(
                f"an adapter named {manifest.name} is installed from {existing.get('repo')}: "
                f"`ling-admin adapter remove {manifest.name}` first"
            )
