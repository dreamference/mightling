"""
What a node needs for its model, computed here from the matrix (specs/DREAMFERENCE_PUFFIN_FLEET.md
§7.4).

- **Weights**: the checkpoint and the drafter its recipe names, and the context engine's embedding
  model when this machine has it, as hub-cache folders copied with `rsync -aH --partial`. The
  diffusion model is left out while it is switched off (`ModelMatrixRegistry.diffusion_enabled`).
- **Images built here** (a bare local tag, such as the fallback's `dreamference-vllm-dflash:…`)
  exist nowhere else and are copied: `docker save | zstd | ssh … 'zstd -d | docker load'`.
- **Images from a registry** (a reference with a `/`, digest-pinned or not) are pulled by the node
  itself: a loaded image may lose its repository digest under overlay2, and `server start` would
  then pull it again anyway. SearXNG's image is one of these.
"""

from pathlib import Path
from typing import Any, Dict, Final, List

GIB: Final[int] = 1024**3

# Free space a node keeps after everything is copied.
DISK_MARGIN_GB: Final[int] = 20


class FleetModelPlan:
    """The folders and images one matrix entry needs on a node."""

    @classmethod
    def plan(cls, model_key: str) -> Dict[str, Any]:
        """
        Args:
            model_key: A key of the model matrix.

        Returns:
            Dict[str, Any]: `folders` (hub-cache folder names), `copy_images` (local tags to
            save and load), `pull_images` (registry references the node pulls), `image` (the
            model server's image).
        """
        from dreamference.chat.searxng_sidecar import SEARXNG_IMAGE
        from dreamference.hardware import get_model_launch_overrides
        from dreamference.node.node_model_sync import NodeModelSync
        from dreamference.vllm_server.vllm_server_manager import DEFAULT_VLLM_IMAGE

        folders = [NodeModelSync.folder_name(repo) for repo in NodeModelSync.repos(model_key)]
        embedding = cls._embedding_folder()
        if embedding and embedding not in folders:
            folders.append(embedding)
        image = get_model_launch_overrides(model_key).get("docker_image", DEFAULT_VLLM_IMAGE)
        images = [image, SEARXNG_IMAGE]
        return {
            "folders": folders,
            "image": image,
            "copy_images": [ref for ref in images if not cls.from_registry(ref)],
            "pull_images": [ref for ref in images if cls.from_registry(ref)],
        }

    @classmethod
    def from_registry(cls, reference: str) -> bool:
        """
        Args:
            reference: An image reference.

        Returns:
            bool: True if it names a registry repository (has a `/`), which the node pulls;
            False for a bare tag built on this machine, which is copied.
        """
        return "/" in reference.split("@", 1)[0].split(":", 1)[0] or "@sha256:" in reference

    @classmethod
    def local_folders(cls, folders: List[str]) -> List[Path]:
        """
        Args:
            folders: Hub-cache folder names.

        Returns:
            List[Path]: Those present in this machine's cache, in order.
        """
        from dreamference.node.node_model_sync import NodeModelSync

        hub = NodeModelSync.hub()
        return [hub / folder for folder in folders if (hub / folder).is_dir()]

    @classmethod
    def bytes_needed(cls, folders: List[str]) -> int:
        """
        Args:
            folders: The folders a node lacks.

        Returns:
            int: What copying them would write there.
        """
        from dreamference.node.node_model_sync import NodeModelSync

        return NodeModelSync.size(cls.local_folders(folders))

    @classmethod
    def fits(cls, free_gb: float, needed_bytes: int) -> bool:
        """
        Args:
            free_gb: The node's free disk, in GiB.
            needed_bytes: What is to be copied.

        Returns:
            bool: True if the copy leaves the margin free.
        """
        return free_gb * GIB - needed_bytes >= DISK_MARGIN_GB * GIB

    @classmethod
    def _embedding_folder(cls) -> str:
        from dreamference.context_engine.embedding_calculator import EMBEDDING_MODEL
        from dreamference.node.node_model_sync import NodeModelSync

        folder = NodeModelSync.folder_name(EMBEDDING_MODEL)
        return folder if (NodeModelSync.hub() / folder).is_dir() else ""
