"""The pinned digests of the community instance images (`COMMUNITY_IMAGE_REPO`).

The images come from a third-party repository by mutable tag: whoever controls the repository can
retag an instance's image, and the next pull would take the new one silently. `image_digests/<arch>.json`
(committed, made from the digests the validated list recorded) names the digest each image must have.
`SweBenchDocker.ensure_image` refuses a pulled image whose digest differs, and
`ling-admin swe-bench pin-images` is the deliberate way to change the list.
"""

import json
import os
import sys
from pathlib import Path
from typing import Final

from dreamference.swe_bench import swe_bench_settings

COMMUNITY: Final[str] = "community"
PINS_DIR: Final[Path] = Path(__file__).resolve().parent / "image_digests"


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


class SweBenchImagePins:
    """Reads, checks and rewrites the pinned digest list of this machine's architecture."""

    @classmethod
    def path(cls) -> Path:
        """Locate the pin file of this architecture.

        Returns:
            Path: `image_digests/<arch>.json`.
        """
        return PINS_DIR / f"{swe_bench_settings.SweBenchSettings.architecture()}.json"

    @classmethod
    def load(cls) -> dict[str, str]:
        """Read the pin list.

        Returns:
            dict[str, str]: Image reference to its pinned digest; empty if the file is absent or unreadable.
        """
        try:
            record = json.loads(cls.path().read_text())
        except (OSError, ValueError):
            return {}
        images = record.get("images") if isinstance(record, dict) else None
        return {str(k): str(v) for k, v in images.items()} if isinstance(images, dict) else {}

    @classmethod
    def problem(cls, image: str, digest: str | None) -> str | None:
        """Compares an image's digest with its pin.

        Args:
            image: The image reference.
            digest: The digest the image has locally.

        Returns:
            str | None: What is wrong, or None when the digest matches or the image has no pin
            (an image built here is never pinned, and a new community image has none until
            `pin-images` records it).
        """
        pinned = cls.load().get(image)
        if pinned is None or digest == pinned:
            return None
        return (
            f"{image} has digest {digest}, but {pinned} is pinned: the community repository changed the "
            f"image. Check why; `ling-admin swe-bench pin-images --accept {image}` pins the new one."
        )

    @classmethod
    def write(cls, images: dict[str, str]) -> Path:
        """Replaces the pin list.

        Args:
            images: Image reference to digest.

        Returns:
            Path: The file written.
        """
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps({"images": dict(sorted(images.items()))}, indent=1) + "\n")
        Path(staging).replace(path)
        return path

    @classmethod
    def review(cls, accept: list[str], new: bool) -> int:
        """Compares the local community images with the pins, and changes pins on request.

        Args:
            accept: Images whose local digest becomes their pin (the deliberate update).
            new: Also pin every local image that has no pin.

        Returns:
            int: 0 when every local image matches its pin (after any change), else 1.
        """
        from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
        from dreamference.swe_bench.swe_bench_images import SweBenchImages

        pins = cls.load()
        local = {
            item["image"]: SweBenchDocker.image_digest(item["image"])
            for item in SweBenchImages.local_images()
        }
        changed = False
        for image in accept:
            if not local.get(image):
                _say(f"❌ {image} is not present locally; pull it first.")
                return 1
            pins[image], changed = str(local[image]), True
        if new:
            for image, digest in local.items():
                if digest and image not in pins:
                    pins[image], changed = digest, True
        if changed:
            _say(f"✅ Pins written to {cls.write(pins)}; review the diff and commit it.")
        differing = [
            image for image, digest in local.items() if image in pins and pins[image] != digest
        ]
        unpinned = [image for image in local if image not in pins]
        for image in differing:
            _say(f"❌ {image}: local {local[image]}, pinned {pins[image]}")
        _say(
            f"{len(pins)} pinned; {len(local)} local, {len(differing)} differ, {len(unpinned)} unpinned"
            + (" (`pin-images --new` pins them)" if unpinned else "")
        )
        return 1 if differing else 0

    @classmethod
    def from_validated(cls) -> dict[str, str]:
        """Collect the community images of the validated list.

        Returns:
            dict[str, str]: The community images of the validated list with the digest recorded for each.
        """
        from dreamference.swe_bench.swe_bench_images import SweBenchImages

        return {
            record["image"]: record["digest"]
            for record in SweBenchImages.validated().values()
            if record.get("source") == COMMUNITY
            and record.get("image")
            and str(record.get("digest") or "").startswith("sha256:")
        }
