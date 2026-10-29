"""The instance image cache of a night machine (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §30).

With `--eval --remove-images` a run removes each chunk's images once the chunk is graded, and the next
night on the same list pulls them again (about 2.3 GB each, minutes per image). The cache keeps the
images of the task lists named in `[swe_bench] image_cache_lists` instead, as long as the disk keeps
`image_cache_floor` free: below it, the cached images least recently used by a run go first, and only
then the images of the chunk just graded. Pulls already skip an image that is present
(`SweBenchDocker.ensure_image`), so a cached image costs the next run nothing.

When each image was last used is kept in `IMAGE_CACHE_RECORD`; an image missing from it counts as the
oldest.
"""

import json
import shutil
import time
from pathlib import Path

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_shards import SweBenchShards


class SweBenchImageCache:
    """Decides which instance images stay on disk after a run used them."""

    @classmethod
    def image_name(cls, instance_id: str) -> str:
        """The community image an instance runs in (`SweBenchImages.image_for` without the tag check).

        Args:
            instance_id: The instance.

        Returns:
            str: Its image reference.
        """
        return f"{swe_bench_settings.COMMUNITY_IMAGE_REPO}:{instance_id.replace('__', '-')}"

    @classmethod
    def cached_images(cls, settings: "swe_bench_settings.SweBenchSettings") -> set[str]:
        """The images of the instances in the cached task lists.

        Args:
            settings: Benchmark settings (`image_cache_lists`).

        Returns:
            set[str]: Their image references; empty when no list is named or none can be read.
        """
        images: set[str] = set()
        for name in settings.image_cache_lists:
            path = Path(name).expanduser()
            try:
                text = path.read_text()
            except OSError:
                print(f"⚠️  Image cache: cannot read the task list {path}; its images are not kept.")  # noqa: T201 — CLI output
                continue
            images |= {cls.image_name(instance_id) for instance_id in SweBenchShards.ids(text)}
        return images

    @classmethod
    def floor_bytes(cls, settings: "swe_bench_settings.SweBenchSettings") -> int:
        """The free disk the cache keeps.

        Args:
            settings: Benchmark settings (`image_cache_floor`, `disk_reserve`).

        Returns:
            int: Bytes; the disk reserve plus `IMAGE_CACHE_HEADROOM` when no floor is set.
        """
        from dreamference.night_shift.night_shift_host import NightShiftHost

        if settings.image_cache_floor.strip():
            return NightShiftHost.parse_size(settings.image_cache_floor)
        return NightShiftHost.parse_size(settings.disk_reserve) + NightShiftHost.parse_size(
            swe_bench_settings.IMAGE_CACHE_HEADROOM
        )

    @classmethod
    def free_bytes(cls) -> int:
        """Free bytes on the disk the images and the benchmark's cache share."""
        swe_bench_settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(swe_bench_settings.CACHE_DIR).free

    @classmethod
    def last_used(cls) -> dict[str, float]:
        """When each cached image was last used by a run.

        Returns:
            dict[str, float]: Image reference to Unix time; empty when there is no record. An
            entry that is not a time is left out.
        """
        try:
            record = json.loads(swe_bench_settings.IMAGE_CACHE_RECORD.read_text())
        except (OSError, ValueError):
            return {}
        times: dict[str, float] = {}
        for image, when in record.items() if isinstance(record, dict) else []:
            if isinstance(when, (int, float)) and not isinstance(when, bool):
                times[str(image)] = float(when)
        return times

    @classmethod
    def touch(cls, images: list[str]) -> None:
        """Records that a run has just used these images.

        Args:
            images: Image references.
        """
        record = cls.last_used()
        now = time.time()
        record.update(dict.fromkeys(images, now))
        swe_bench_settings.IMAGE_CACHE_RECORD.parent.mkdir(parents=True, exist_ok=True)
        swe_bench_settings.IMAGE_CACHE_RECORD.write_text(
            json.dumps(record, indent=1, sort_keys=True) + "\n"
        )

    @classmethod
    def release(
        cls, images: list[str], settings: "swe_bench_settings.SweBenchSettings"
    ) -> list[str]:
        """Lets go of images a run has finished with: keeps the cached ones, removes the rest.

        A kept image stays only while the disk has the floor free: below it, cached images least
        recently used go first (the ones just released last of all), then, if the floor is still not
        reached, the kept images are removed after all.

        Args:
            images: The images just finished with (a graded chunk, or images pulled only to index).
            settings: Benchmark settings.

        Returns:
            list[str]: The images removed.
        """
        cached = cls.cached_images(settings)
        keep = [image for image in images if image in cached]
        removed = [image for image in images if image not in cached]
        SweBenchImages.remove(removed)
        if keep:
            cls.touch(keep)
        removed += cls.evict(settings)
        return removed

    @classmethod
    def evict(
        cls,
        settings: "swe_bench_settings.SweBenchSettings",
        protect: frozenset[str] = frozenset(),
        floor: int | None = None,
    ) -> list[str]:
        """Removes instance images, least recently used first, until the floor is free.

        Args:
            settings: Benchmark settings.
            protect: Images never removed here (a run's images still to be graded).
            floor: Free bytes to reach; None for the cache's floor (a night's start asks for its own).

        Returns:
            list[str]: The images removed.
        """
        floor = cls.floor_bytes(settings) if floor is None else floor
        if cls.free_bytes() >= floor:
            return []
        used = cls.last_used()
        present = [entry["image"] for entry in SweBenchImages.local_images()]
        candidates = sorted(
            (image for image in present if image not in protect),
            key=lambda image: used.get(image, 0.0),
        )
        removed = []
        for image in candidates:
            if cls.free_bytes() >= floor:
                break
            if SweBenchImages.remove([image]):
                removed.append(image)
        if removed:
            print(  # noqa: T201 — CLI output
                f"🧹 Image cache: {len(removed)} image(s) removed to keep {floor / 1024**3:.0f} GiB free."
            )
        return removed
