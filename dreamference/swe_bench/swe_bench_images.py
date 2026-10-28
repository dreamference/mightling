"""
Instance images for this machine's architecture, and the list of instances that are known to
grade correctly in them (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §5.3, §12).

On arm64 Verified's images come from the community repository `greynewell/swe-bench-arm64`, whose
tags are the instance ids with `__` written `-`. A dataset nobody publishes arm64 images for
(Multilingual) has its images built on this machine (`SweBenchImageBuilder`) and tagged the same
way in `BUILT_IMAGE_REPO`; those are never pulled and never removed by a run. An image existing is
not evidence that the instance works: it is **validated** when the reference patch resolves it here
and a no-op patch does not. Only validated instances are run, and the rest are reported as excluded.
"""

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker

# The tag list is refetched after a day: the repository gains images rarely.
TAG_LIST_MAX_AGE_S: int = 24 * 3600

# How `docker images` shows an image without a tag.
UNTAGGED: str = "<none>"


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
    def image_for(
        cls, instance_id: str, tags: Optional[List[str]] = None, built: list[str] | None = None
    ) -> Optional[str]:
        """
        Names the image an instance runs in on this machine.

        The community image when there is one, else an image built here.

        Args:
            instance_id: The instance.
            tags: The community repository's tags; None reads them.
            built: The tags of the images built here; None lists them.

        Returns:
            Optional[str]: The image reference, or None when no arm64 image exists.
        """
        tag = instance_id.replace("__", "-")
        known = cls.tags() if tags is None else tags
        if tag in known:
            return f"{swe_bench_settings.COMMUNITY_IMAGE_REPO}:{tag}"
        here = cls.built_tags() if built is None else built
        return cls.built_name(instance_id) if tag in here else None

    @classmethod
    def built_name(cls, instance_id: str) -> str:
        """The reference an instance's image has when it is built here.

        Args:
            instance_id: The instance.

        Returns:
            str: The image reference.
        """
        return f"{swe_bench_settings.BUILT_IMAGE_REPO}:{instance_id.replace('__', '-')}"

    @classmethod
    def built_tags(cls) -> list[str]:
        """The tags of the instance images built on this machine.

        Returns:
            list[str]: The tags, untagged images left out.
        """
        result = SweBenchDocker.run(
            ["images", swe_bench_settings.BUILT_IMAGE_REPO, "--format", "{{.Tag}}"], timeout=60
        )
        return [tag for tag in result.stdout.split() if tag and tag != UNTAGGED]

    @classmethod
    def is_built(cls, image: str) -> bool:
        """Says whether an image was built here, which nothing pulls back once it is gone.

        Args:
            image: An image reference.

        Returns:
            bool: True for an image in `BUILT_IMAGE_REPO`.
        """
        return image.startswith(f"{swe_bench_settings.BUILT_IMAGE_REPO}:")

    # -- the validated list --------------------------------------------------------------------

    @classmethod
    def validated_path(cls) -> Path:
        """
        Returns:
            Path: `validated-<arch>.json` in the cache.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        return swe_bench_settings.CACHE_DIR / f"validated-{architecture}.json"

    @classmethod
    def validated(cls) -> Dict[str, Dict[str, Any]]:
        """
        Returns:
            Dict[str, Dict[str, Any]]: Validated instance id to `{image, digest, source, at}`.
        """
        return cls._read_validated().get("instances", {})

    @classmethod
    def rejected(cls) -> Dict[str, str]:
        """
        Returns:
            Dict[str, str]: Instances that failed validation here, with the reason.
        """
        return cls._read_validated().get("rejected", {})

    @classmethod
    def record_validation(cls, instance_id: str, image: Optional[str], digest: Optional[str],
                          problem: Optional[str]) -> None:
        """
        Records one instance's validation result, replacing an earlier one.

        Args:
            instance_id: The instance.
            image: Its image reference.
            digest: The digest of the image it was validated in.
            problem: Why it is not validated; None when it is.
        """
        record = cls._read_validated()
        record.setdefault("instances", {}).pop(instance_id, None)
        record.setdefault("rejected", {}).pop(instance_id, None)
        if problem is None:
            record["instances"][instance_id] = {
                "image": image, "digest": digest,
                "source": "built" if image and cls.is_built(image) else "community",
                "harness": swe_bench_settings.HARNESS_VERSION,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        else:
            record["rejected"][instance_id] = problem
        path = cls.validated_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(staging, path)

    @classmethod
    def _read_validated(cls) -> Dict[str, Any]:
        try:
            record = json.loads(cls.validated_path().read_text())
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}

    # -- disk ----------------------------------------------------------------------------------

    @classmethod
    def local_images(cls) -> List[Dict[str, str]]:
        """
        Returns:
            List[Dict[str, str]]: The benchmark's images present locally, as `{image, size}`.
        """
        result = SweBenchDocker.run(["images", swe_bench_settings.COMMUNITY_IMAGE_REPO,
                                     "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"], timeout=60)
        images = []
        for line in result.stdout.splitlines():
            name, _, size = line.partition("\t")
            if name:
                images.append({"image": name, "size": size})
        return images

    @classmethod
    def remove(cls, images: List[str]) -> int:
        """
        Removes instance images.

        Layers shared with images that stay are kept by Docker. Images built here are kept:
        nothing pulls them back, and building one again takes up to an hour (`docker rmi`
        removes one by hand).

        Args:
            images: Image references.

        Returns:
            int: How many were removed.
        """
        removed = 0
        for image in images:
            if cls.is_built(image):
                continue
            if SweBenchDocker.run(["rmi", image], timeout=300).returncode == 0:
                removed += 1
        return removed
