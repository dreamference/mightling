"""
Chat Template Patcher for Puffin's model server.

This module provides the ChatTemplatePatcher class, which applies a recipe's string substitutions
to a checkpoint's own chat template and writes the result where the server container can read it.

A template is part of the checkpoint, and some reject requests our clients send. Qwen3.8's accepts
only the reasoning efforts `xhigh`, `medium` and `low`, so Codex's `high` or `minimal` came back as
HTTP 400 on every turn. Patching a copy at launch, rather than the cached checkpoint, keeps the
pinned snapshot byte-identical to what was downloaded.
"""

import hashlib
import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from dreamference.hardware.model_downloader import ModelDownloader

# Where patched templates are written: inside the dreamference cache the server container mounts
# at /root/.cache/dreamference, so the same file is visible on both sides. Not under `sglang/`:
# the container creates that directory as root for its compile cache, and the first launch
# failed there with PermissionError.
HOST_TEMPLATE_DIR = Path(os.path.expanduser("~/.cache/dreamference/chat-templates"))
CONTAINER_TEMPLATE_DIR = "/root/.cache/dreamference/chat-templates"


class ChatTemplatePatcher:
    """
    Writes a checkpoint's chat template with a recipe's substitutions applied.
    """

    @classmethod
    def file_name(cls, repo_id: str, revision: str, patches: Sequence[Sequence[str]]) -> str:
        """
        Names the patched template after the checkpoint and the patches.

        Deterministic, so the launch command can name the file before it exists and the recipe
        drift check rebuilds the same command.

        Args:
            repo_id (str): HuggingFace repository ID of the checkpoint.
            revision (str): The pinned commit.
            patches (Sequence[Sequence[str]]): (anchor, replacement) pairs.

        Returns:
            str: A file name unique to this checkpoint revision and patch set.
        """
        digest = hashlib.sha256(repr([tuple(p) for p in patches]).encode("utf-8")).hexdigest()[:12]
        return f"{repo_id.replace('/', '--')}-{revision[:12]}-{digest}.jinja"

    @classmethod
    def container_path(cls, repo_id: str, revision: str, patches: Sequence[Sequence[str]]) -> str:
        """
        Where the server container finds the patched template.

        Args:
            repo_id (str): HuggingFace repository ID of the checkpoint.
            revision (str): The pinned commit.
            patches (Sequence[Sequence[str]]): (anchor, replacement) pairs.

        Returns:
            str: Absolute path inside the container.
        """
        return f"{CONTAINER_TEMPLATE_DIR}/{cls.file_name(repo_id, revision, patches)}"

    @classmethod
    def apply(cls, template: str, patches: Sequence[Sequence[str]]) -> Tuple[str, List[str]]:
        """
        Applies substitutions whose anchors each occur exactly once.

        Args:
            template (str): The original template text.
            patches (Sequence[Sequence[str]]): (anchor, replacement) pairs.

        Returns:
            Tuple[str, List[str]]: The patched text, and the anchors that did not occur exactly
                once (empty on success).
        """
        failed: List[str] = []
        for anchor, replacement in patches:
            if template.count(anchor) != 1:
                failed.append(anchor)
                continue
            template = template.replace(anchor, replacement)
        return template, failed

    @classmethod
    def prepare(cls, repo_id: str, revision: str, patches: Sequence[Sequence[str]]) -> Optional[Path]:
        """
        Writes the patched template for a downloaded checkpoint.

        Args:
            repo_id (str): HuggingFace repository ID of the checkpoint.
            revision (str): The pinned commit, whose snapshot must be in the local cache.
            patches (Sequence[Sequence[str]]): (anchor, replacement) pairs.

        Returns:
            Optional[Path]: The written file, or None when the snapshot has no chat template or
                an anchor no longer matches (a different template than the recipe was written for;
                serving it unpatched would bring the refusals back, so the caller must stop).
        """
        snapshot = (ModelDownloader.get_hf_cache_dir() / f"models--{repo_id.replace('/', '--')}"
                    / "snapshots" / revision)
        source = snapshot / "chat_template.jinja"
        if not source.is_file():
            return None
        patched, failed = cls.apply(source.read_text(encoding="utf-8"), patches)
        if failed:
            return None
        target = HOST_TEMPLATE_DIR / cls.file_name(repo_id, revision, patches)
        try:
            HOST_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
            target.write_text(patched, encoding="utf-8")
        except OSError as error:
            print(f"⚠️  Could not write the patched chat template to {target}: {error}")
            return None
        return target
