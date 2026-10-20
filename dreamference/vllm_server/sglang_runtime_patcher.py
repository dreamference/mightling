"""Patching one source file of the pinned SGLang image at launch (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §8.4).

The same discipline as `ChatTemplatePatcher`: the recipe names the file, the sha256 of the image's
own copy, and (anchor, replacement) pairs; the pristine file is copied out of the image (`docker
create` and `docker cp`, so a node that has only pulled the image needs nothing running), checked
against its pin, patched where each anchor occurs exactly once, written under the dreamference
cache, and bind-mounted read-only over the original in the engine's container. The image itself is
never changed, and a file that is not the one the patch was written for is never patched: the
launch then serves no adapters rather than a wrong file.

The one patch so far: SGLang v0.5.21's `/v1/responses` builds its `GenerateReqInput` without the
adapter, so it accepted `<base>:<adapter>` and ran the base model (the probe of 2026-10-19). Both
places that build one pass `lora_path` as `/v1/chat/completions` does.
"""

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Final

from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher

DOCKER_TIMEOUT_S: Final[float] = 120.0


class SGLangRuntimePatcher:
    """Writes patched copies of image files and names their mounts."""

    @classmethod
    def host_dir(cls) -> Path:
        """Where patched files are written, under the current home.

        Returns:
            Path: `~/.cache/dreamference/sglang-patches`.
        """
        return Path("~/.cache/dreamference/sglang-patches").expanduser()

    @classmethod
    def file_name(cls, patch: dict[str, object]) -> str:
        """Names a patched file after the original's pin and the substitutions, deterministically.

        Args:
            patch: A recipe entry: `path`, `sha256`, `substitutions`.

        Returns:
            str: The file name, so the launch can name the mount before the file exists and the
            recipe drift check rebuilds the same command.
        """
        substitutions = patch.get("substitutions") or []
        digest = hashlib.sha256(repr(substitutions).encode("utf-8")).hexdigest()[:12]
        return f"{Path(str(patch['path'])).name}-{str(patch['sha256'])[:12]}-{digest}"

    @classmethod
    def mount_args(cls, patches: list[dict[str, object]]) -> list[str]:
        """The `docker run` volume options that put the patched files in place.

        Args:
            patches: The recipe's runtime patches.

        Returns:
            list[str]: `-v <host>:<container path>:ro` per patch.
        """
        args: list[str] = []
        for patch in patches:
            args.extend(["-v", f"{cls.host_dir() / cls.file_name(patch)}:{patch['path']}:ro"])
        return args

    @classmethod
    def prepare(cls, image: str, patches: list[dict[str, object]]) -> str | None:
        """Writes every patched file the launch will mount.

        Args:
            image: The engine's image, as the recipe pins it.
            patches: The recipe's runtime patches.

        Returns:
            str | None: None when every file is in place; otherwise what went wrong.
        """
        for patch in patches:
            target = cls.host_dir() / cls.file_name(patch)
            if target.is_file():
                continue
            original = cls.extract(image, str(patch["path"]))
            if original is None:
                return f"could not copy {patch['path']} out of {image}"
            if hashlib.sha256(original).hexdigest() != patch["sha256"]:
                return f"{Path(str(patch['path'])).name} in {image} is not the file the patch was written for"
            pairs = [tuple(pair) for pair in patch.get("substitutions") or []]  # type: ignore[attr-defined]
            patched, failed = ChatTemplatePatcher.apply(original.decode("utf-8"), pairs)
            if failed:
                return f"{len(failed)} anchor(s) of the patch to {Path(str(patch['path'])).name} no longer match"
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.with_name(f".{target.name}.tmp")
                staging.write_text(patched, encoding="utf-8")
                staging.replace(target)
            except OSError as error:
                return f"could not write {target} ({error})"
        return None

    @classmethod
    def extract(cls, image: str, path: str) -> bytes | None:
        """Copies one file out of an image without running it.

        Args:
            image: The image.
            path: The file's path inside it.

        Returns:
            bytes | None: Its content, or None when docker could not provide it.
        """
        created = cls._docker(["docker", "create", image])
        if created is None or created.returncode != 0:
            return None
        container = created.stdout.strip()
        try:
            with tempfile.TemporaryDirectory(prefix="mightling-sglang-patch-") as work:
                copied = cls._docker(["docker", "cp", f"{container}:{path}", f"{work}/original"])
                if copied is None or copied.returncode != 0:
                    return None
                return (Path(work) / "original").read_bytes()
        except OSError:
            return None
        finally:
            cls._docker(["docker", "rm", "-f", container])

    @classmethod
    def _docker(cls, command: list[str]) -> "subprocess.CompletedProcess[str] | None":
        """Runs one docker command.

        Args:
            command: The command.

        Returns:
            subprocess.CompletedProcess[str] | None: Its result, or None when docker could not run.
        """
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
