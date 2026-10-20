"""
Instance images for this machine's architecture, and the list of instances that are known to
grade correctly in them (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §5.3, §12).

On arm64 the only source is the community repository `greynewell/swe-bench-arm64`, whose tags
are the instance ids with `__` written `-`. An image existing is not evidence that the instance
works: it is **validated** when the reference patch resolves it here and a no-op patch does not.
Only validated instances are run, and the rest are reported as excluded.

SWE-rebench has no arm64 images at all: each instance's is built from the SWE-rebench fork's
recipes (`SweBenchImageBuilder`), on this or another machine, and travels as an archive with the
record of its validation. Its validated list is a file of its own (`validated-arm64-rebench.json`),
so nothing that draws Verified tasks from `validated-arm64.json` ever sees a SWE-rebench task.
"""

import hashlib
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker

# The tag list is refetched after a day: the repository gains images rarely.
TAG_LIST_MAX_AGE_S: int = 24 * 3600


class SweBenchImages:
    """Resolves, pulls and removes instance images, and keeps the validated list."""

    @classmethod
    def _fetch_tags(cls) -> List[str]:
        """Lists every tag of the community repository through the registry's own API (Docker
        Hub's web API stops anonymous paging at 1,000 tags; the repository has 1,721)."""
        repo = swe_bench_settings.COMMUNITY_IMAGE_REPO
        with urllib.request.urlopen(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
                timeout=30) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repo}/tags/list?n=10000",
                                         headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return list(json.load(response)["tags"])

    # Seam: tests replace it so nothing in the suite reaches the network.
    fetch_tags: Callable[[], List[str]] = _fetch_tags

    @classmethod
    def tags_path(cls) -> Path:
        """
        Returns:
            Path: The cached tag list.
        """
        return swe_bench_settings.CACHE_DIR / "community-tags.json"

    @classmethod
    def tags(cls, refresh: bool = False) -> List[str]:
        """
        Returns the community repository's tags, from the cache when it is fresh.

        Args:
            refresh: Fetch the list again even if the cached one is fresh.

        Returns:
            List[str]: The tags; the stale cached list if the registry cannot be reached, and an
            empty list when there is none.
        """
        path = cls.tags_path()
        cached: List[str] = []
        try:
            record = json.loads(path.read_text())
            cached = list(record["tags"])
            if not refresh and time.time() - float(record["fetched_at"]) < TAG_LIST_MAX_AGE_S:
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            fetched = cls.fetch_tags()
        except (OSError, ValueError, KeyError) as error:
            if not cached:
                print(f"⚠️  Could not list {swe_bench_settings.COMMUNITY_IMAGE_REPO}: {error}")
            return cached
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": time.time(), "tags": sorted(fetched)}) + "\n")
        return fetched

    @classmethod
    def image_for(cls, instance_id: str, tags: Optional[List[str]] = None, dataset: Optional[str] = None,
                  row: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Names the image an instance runs in on this machine.

        Args:
            instance_id: The instance.
            tags: The community repository's tags; None reads them.
            dataset: The instance's dataset; None is Verified.
            row: Its dataset row, for SWE-rebench's published image on an x86-64 machine.

        Returns:
            Optional[str]: The image reference, or None when no image exists here: for SWE-rebench
            on arm64, when the built image is neither present nor archived (`missing_reason`).
        """
        if swe_bench_settings.SweBenchSettings.is_rebench(dataset):
            if swe_bench_settings.SweBenchSettings.architecture() == "amd64" and row and row.get("docker_image"):
                return str(row["docker_image"])
            image = cls.built_image(instance_id)
            present = cls.archive_path(image).exists() or SweBenchDocker.image_digest(image) is not None
            return image if present else None
        tag = instance_id.replace("__", "-")
        known = cls.tags() if tags is None else tags
        return f"{swe_bench_settings.COMMUNITY_IMAGE_REPO}:{tag}" if tag in known else None

    @classmethod
    def missing_reason(cls, dataset: Optional[str] = None) -> str:
        """
        Args:
            dataset: The dataset; None is Verified.

        Returns:
            str: Why an instance has no image here, as validation reports it.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        if swe_bench_settings.SweBenchSettings.is_rebench(dataset):
            return (f"no {architecture} image built yet (`ling-admin swe-bench build --dataset <name>` on any "
                    f"{architecture} machine, then `ling-admin swe-bench import <folder>` here)")
        return f"no {architecture} image"

    # -- SWE-rebench's built images ----------------------------------------------------------------

    @classmethod
    def built_image(cls, instance_id: str) -> str:
        """
        Args:
            instance_id: A SWE-rebench instance.

        Returns:
            str: The local name of its built image, `mightling-swe-rebench-<arch>:<id>` with `__`
            written `-`. It is never pushed or pulled.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return f"{swe_bench_settings.BUILT_IMAGE_PREFIX}-{architecture}:{instance_id.replace('__', '-')}"

    @classmethod
    def is_built(cls, image: Optional[str]) -> bool:
        """
        Args:
            image: An image reference.

        Returns:
            bool: True for an image built from SWE-rebench's recipes rather than pulled.
        """
        return bool(image) and str(image).startswith(f"{swe_bench_settings.BUILT_IMAGE_PREFIX}-") \
            and not str(image).startswith(f"{swe_bench_settings.BUILT_IMAGE_PREFIX}-base-")

    @classmethod
    def archive_path(cls, image: str, directory: Optional[Path] = None) -> Path:
        """
        Args:
            image: A built image.
            directory: The archive folder; None is the cache's.

        Returns:
            Path: Where its archive (`docker save`, gzip) is kept.
        """
        return (directory or swe_bench_settings.ARCHIVE_DIR) / f"{image.split(':', 1)[-1]}.tar.gz"

    @classmethod
    def ensure(cls, image: str) -> Optional[str]:
        """
        Makes an instance image present locally: a built image is loaded from its archive (the same
        image, by id, as the one validated), any other is pulled.

        Args:
            image: The image reference.

        Returns:
            Optional[str]: Its digest (a built image's id), or None when it is not available.
        """
        if not cls.is_built(image):
            return SweBenchDocker.ensure_image(image)
        digest = SweBenchDocker.image_digest(image)
        if digest:
            return digest
        archive = cls.archive_path(image)
        if not archive.exists():
            return None
        if SweBenchDocker.run(["load", "-i", str(archive)], timeout=3600).returncode != 0:
            return None
        return SweBenchDocker.image_digest(image)

    @classmethod
    def file_sha256(cls, path: Path) -> str:
        """
        Args:
            path: A file.

        Returns:
            str: Its SHA-256, hex.
        """
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    # -- the validated list --------------------------------------------------------------------

    @classmethod
    def validated_path(cls, dataset: Optional[str] = None) -> Path:
        """
        Args:
            dataset: The dataset; None is Verified.

        Returns:
            Path: `validated-<arch>.json` in the cache, `validated-<arch>-rebench.json` for SWE-rebench.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        pool = "-rebench" if swe_bench_settings.SweBenchSettings.is_rebench(dataset) else ""
        return swe_bench_settings.CACHE_DIR / f"validated-{architecture}{pool}.json"

    @classmethod
    def validated(cls, dataset: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Args:
            dataset: The dataset; None is Verified.

        Returns:
            Dict[str, Dict[str, Any]]: Validated instance id to `{image, digest, source, at}`.
        """
        return cls._read_validated(dataset).get("instances", {})

    @classmethod
    def rejected(cls, dataset: Optional[str] = None) -> Dict[str, str]:
        """
        Args:
            dataset: The dataset; None is Verified.

        Returns:
            Dict[str, str]: Instances that failed validation here, with the reason.
        """
        return cls._read_validated(dataset).get("rejected", {})

    @classmethod
    def record_validation(cls, instance_id: str, image: Optional[str], digest: Optional[str],
                          problem: Optional[str], dataset: Optional[str] = None,
                          extra: Optional[Dict[str, Any]] = None) -> None:
        """
        Records one instance's validation result, replacing an earlier one.

        Args:
            instance_id: The instance.
            image: Its image reference.
            digest: The digest of the image it was validated in.
            problem: Why it is not validated; None when it is.
            dataset: The dataset; None is Verified.
            extra: More fields for a validated instance (a built image's archive, where it was
                built and validated).
        """
        record = cls._read_validated(dataset)
        record.setdefault("instances", {}).pop(instance_id, None)
        record.setdefault("rejected", {}).pop(instance_id, None)
        if problem is None:
            record["instances"][instance_id] = {
                "image": image, "digest": digest, "source": "built" if cls.is_built(image) else "community",
                "harness": swe_bench_settings.HARNESS_VERSION,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                **(extra or {}),
            }
        else:
            record["rejected"][instance_id] = problem
        cls.write_validated(record, dataset)

    @classmethod
    def update_validated(cls, instance_id: str, fields: Dict[str, Any], dataset: Optional[str] = None) -> None:
        """
        Adds fields to a validated instance's record (a built image's archive once it is written).

        Args:
            instance_id: A validated instance.
            fields: The fields.
            dataset: The dataset; None is Verified.
        """
        record = cls._read_validated(dataset)
        if instance_id in record.get("instances", {}):
            record["instances"][instance_id].update(fields)
            cls.write_validated(record, dataset)

    @classmethod
    def write_validated(cls, record: Dict[str, Any], dataset: Optional[str] = None,
                        path: Optional[Path] = None) -> None:
        """
        Replaces a validated list in one step.

        Args:
            record: `{"instances": …, "rejected": …}`.
            dataset: The dataset; None is Verified.
            path: Another file to write (an archive folder's `validated.json`).
        """
        path = path or cls.validated_path(dataset)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(staging, path)

    @classmethod
    def _read_validated(cls, dataset: Optional[str] = None) -> Dict[str, Any]:
        try:
            record = json.loads(cls.validated_path(dataset).read_text())
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    @classmethod
    def import_archives(cls, folder: Path, dataset: str) -> Tuple[int, List[str]]:
        """
        Takes in what `swe-bench build` wrote on another machine: each validated instance whose
        archive is in `folder` with the SHA-256 its record names is copied into the cache's archive
        folder and recorded as validated here, with where it was built and validated; rejections
        are recorded too. Nothing is loaded into Docker until an instance needs its image.

        Args:
            folder: The builder's archive folder (with its `validated.json`).
            dataset: The dataset the instances belong to.

        Returns:
            Tuple[int, List[str]]: How many validated instances were taken in, and what was not.
        """
        try:
            incoming = json.loads((folder / "validated.json").read_text())
        except (OSError, ValueError) as error:
            return 0, [f"{folder / 'validated.json'}: {error}"]
        taken, problems = 0, []
        for instance_id, entry in sorted((incoming.get("instances") or {}).items()):
            image = entry.get("image") or ""
            archive = entry.get("archive") or {}
            source = folder / Path(str(archive.get("file") or "")).name
            if not cls.is_built(image) or not archive.get("sha256") or not source.is_file():
                problems.append(f"{instance_id}: no archive in {folder}")
                continue
            if cls.file_sha256(source) != archive["sha256"]:
                problems.append(f"{instance_id}: {source.name} does not match its SHA-256")
                continue
            target = cls.archive_path(image)
            if source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                shutil.copyfile(source, staging)
                os.replace(staging, target)
            # The harness version and time are the builder's validation's, and stay.
            fields = {key: value for key, value in entry.items() if key not in ("image", "digest", "source")}
            fields["imported_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            cls.record_validation(instance_id, image, entry.get("digest"), None, dataset, fields)
            taken += 1
        for instance_id, problem in sorted((incoming.get("rejected") or {}).items()):
            if instance_id not in cls.validated(dataset):
                cls.record_validation(instance_id, None, None, problem, dataset)
        return taken, problems

    # -- disk ----------------------------------------------------------------------------------

    @classmethod
    def local_images(cls) -> List[Dict[str, str]]:
        """
        Returns:
            List[Dict[str, str]]: The benchmark's images present locally, as `{image, size}`.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        images = []
        for repository in (swe_bench_settings.COMMUNITY_IMAGE_REPO,
                           f"{swe_bench_settings.BUILT_IMAGE_PREFIX}-{architecture}"):
            result = SweBenchDocker.run(["images", repository,
                                         "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"], timeout=60)
            for line in result.stdout.splitlines():
                name, _, size = line.partition("\t")
                if name:
                    images.append({"image": name, "size": size})
        return images

    @classmethod
    def remove(cls, images: List[str]) -> int:
        """
        Removes instance images. Layers shared with images that stay are kept by Docker.

        Args:
            images: Image references.

        Returns:
            int: How many were removed.
        """
        removed = 0
        for image in images:
            if SweBenchDocker.run(["rmi", image], timeout=300).returncode == 0:
                removed += 1
        return removed
