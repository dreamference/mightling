"""The checks an adapter passes before it is recorded, served or copied (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §5).

One routine for `install`, `verify`, `server start` and a node receiving an adapter: the manifest's
sha256 against the catalogue's pin (when catalogued), the manifest parsed strictly, its signature
under `mightling-adapter` (unless the adapter was installed as third-party), every file against its
hash, the adapter's own configuration and tensors against what the served recipe can serve, and
the base it was made for against the base this machine serves. The base is asked of the model
server (`/v1/models`), never taken from the configuration; with no server answering, the
configured model stands in and the check is made again at `server start`.
"""

import hashlib
import json
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

from dreamference.hardware.adapter_base_digest import AdapterBaseDigest
from dreamference.hardware.adapter_manifest import (
    CONFIG_FILE,
    MANIFEST_FILE,
    SIGNATURE_FILE,
    WEIGHTS_FILE,
    AdapterManifest,
)
from dreamference.hardware.adapter_signature import AdapterSignature
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import ADAPTER_SEPARATOR, ModelMatrixRegistry

CHUNK: Final[int] = 1 << 20
HEADER_LIMIT: Final[int] = 64 << 20
LORA_DTYPE: Final[str] = "BF16"
SAFETENSORS_METADATA: Final[str] = "__metadata__"
LORA_SUFFIXES: Final[tuple[str, ...]] = (".lora_A.weight", ".lora_B.weight")
MODELS_TIMEOUT_S: Final[float] = 3.0


class AdapterVerifier:
    """Checks an adapter's files, signature, configuration and base."""

    @classmethod
    def check_files(
        cls, folder: Path, signed: bool, manifest_sha256: str | None = None
    ) -> AdapterManifest:
        """Checks everything that does not depend on the served base.

        Args:
            folder: The adapter's snapshot folder.
            signed: Whether the signature must verify (False only for a third-party adapter).
            manifest_sha256: The catalogue's pin of `manifest.json`, when catalogued.

        Returns:
            AdapterManifest: The verified manifest.

        Raises:
            ValueError: With the first reason the adapter is refused.
        """
        manifest = cls.check_manifest(folder, signed, manifest_sha256)
        for path, digest in manifest.all_files().items():
            if cls.file_digest(folder / path) != digest:
                raise ValueError(f"{path} does not match the manifest's sha256")
        cls.check_config(manifest, folder)
        return manifest

    @classmethod
    def check_manifest(
        cls, folder: Path, signed: bool, manifest_sha256: str | None = None
    ) -> AdapterManifest:
        """Checks the manifest and its signature, which is all `install` has before the weights.

        Args:
            folder: The adapter's snapshot folder.
            signed: Whether the signature must verify (False only for a third-party adapter).
            manifest_sha256: The catalogue's pin of `manifest.json`, when catalogued.

        Returns:
            AdapterManifest: The manifest.

        Raises:
            ValueError: With the reason.
        """
        data = cls._read(folder / MANIFEST_FILE)
        if manifest_sha256 is not None and hashlib.sha256(
            data
        ).hexdigest() != manifest_sha256.removeprefix("sha256:"):
            raise ValueError("manifest.json is not the one this release's catalogue pins")
        manifest = AdapterManifest.parse(data)
        if signed:
            try:
                signature = (folder / SIGNATURE_FILE).read_bytes()
            except OSError as error:
                raise ValueError(
                    f"{SIGNATURE_FILE} is missing: the adapter is not signed by Dreamference"
                ) from error
            problem = AdapterSignature.problem(data, signature)
            if problem:
                raise ValueError(problem)
        return manifest

    @classmethod
    def check_config(cls, manifest: AdapterManifest, folder: Path) -> None:
        """Checks the adapter against what the served recipe can serve (§8.2).

        The recipe's `lora` key names the modules the engine applies LoRA to (attention and Gated
        DeltaNet only: the probe of 2026-10-19 found LoRA on the NVFP4 MLP crashing under
        torch.compile) and the rank ceiling. The configuration's modules are SGLang's names (the
        GDN input projection as `in_proj_qkvz`); the tensors carry it split as `in_proj_q/k/v/z`.

        Args:
            manifest: The adapter's manifest.
            folder: Its snapshot folder.

        Raises:
            ValueError: With the reason.
        """
        lora = ModelMatrixRegistry.get_lora_recipe(manifest.base_repo)
        if lora is None:
            raise ValueError(
                f"the model {manifest.base_repo} is not served with adapters by this release"
            )
        config = cls._json(folder / CONFIG_FILE)
        modules = config.get("target_modules")
        if not isinstance(modules, list) or not modules:
            raise ValueError(f"{CONFIG_FILE} names no target_modules")
        allowed = set(lora["target_modules"])
        outside = sorted(str(module) for module in modules if module not in allowed)
        if outside:
            raise ValueError(
                f"the adapter changes {', '.join(outside)}, which the served recipe does not "
                f"apply adapters to (it does: {', '.join(sorted(allowed))})"
            )
        rank = config.get("r")
        if rank != manifest.rank:
            raise ValueError(f"{CONFIG_FILE} says rank {rank}, the manifest {manifest.rank}")
        if manifest.rank > int(lora["max_lora_rank"]):
            raise ValueError(
                f"rank {manifest.rank} is above the served recipe's ceiling of {lora['max_lora_rank']}"
            )
        cls.check_tensors(folder / WEIGHTS_FILE, set(lora["weight_modules"]))

    @classmethod
    def check_tensors(cls, weights: Path, allowed: set[str]) -> None:
        """Reads the safetensors header (no tensor data) and checks every tensor's name and type.

        Args:
            weights: `adapter_model.safetensors`.
            allowed: The module names a tensor may belong to.

        Raises:
            ValueError: With the first tensor refused.
        """
        try:
            with Path(weights).open("rb") as handle:
                (length,) = struct.unpack("<Q", handle.read(8))
                if length > HEADER_LIMIT:
                    raise ValueError(f"{WEIGHTS_FILE}'s header is implausibly large")
                header = json.loads(handle.read(length).decode("utf-8"))
        except (OSError, struct.error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{WEIGHTS_FILE} is not a safetensors file ({error})") from error
        if not isinstance(header, dict):
            raise ValueError(
                f"{WEIGHTS_FILE} is not a safetensors file (its header is not an object)"
            )
        tensors = {key: value for key, value in header.items() if key != SAFETENSORS_METADATA}
        if not tensors:
            raise ValueError(f"{WEIGHTS_FILE} holds no tensors")
        for key, value in tensors.items():
            module = next((key.removesuffix(s) for s in LORA_SUFFIXES if key.endswith(s)), None)
            if module is None or module.rsplit(".", 1)[-1] not in allowed:
                raise ValueError(
                    f"{WEIGHTS_FILE} holds {key}, which is not a LoRA tensor of a served module"
                )
            if not isinstance(value, dict) or value.get("dtype") != LORA_DTYPE:
                raise ValueError(f"{key} is not bfloat16")

    @classmethod
    def served_base(cls, host: str, configured: str) -> dict[str, str] | None:
        """The base this machine serves: asked of the server, else the configured model.

        Args:
            host: The local model server's URL.
            configured: The configured model key, used only when no server answers.

        Returns:
            dict[str, str] | None: `key`, `repo`, `revision`, `snapshot` (the local snapshot
            folder) and `source` (`server` or `configuration`); None when the model is not an
            entry of the matrix with a pinned revision.
        """
        served = cls.served_id(host)
        key = ModelMatrixRegistry.key_for_served_id(served, configured) if served else configured
        base = cls.base_for(key) if key else None
        if base is not None:
            base["source"] = "server" if served else "configuration"
        return base

    @classmethod
    def base_for(cls, key: str) -> dict[str, str] | None:
        """A model of the matrix as a base: its repository, pinned revision and local snapshot.

        Args:
            key: A model matrix key (the one being served, or about to be).

        Returns:
            dict[str, str] | None: `key`, `repo`, `revision`, `snapshot`; None when the model is
            not an entry with a pinned revision.
        """
        spec = ModelMatrixRegistry.get_spec(key)
        revision = str(ModelMatrixRegistry.get_launch_overrides(key).get("revision") or "")
        if spec is None or not revision:
            return None
        snapshot = (
            ModelDownloader.get_hf_cache_dir()
            / f"models--{spec.hf_repo_id.replace('/', '--')}"
            / "snapshots"
            / revision
        )
        return {
            "key": key,
            "repo": spec.hf_repo_id,
            "revision": revision,
            "snapshot": str(snapshot),
        }

    @classmethod
    def served_id(cls, host: str) -> str | None:
        """The base id a running server lists first in `/v1/models`, without an adapter suffix.

        Args:
            host: The model server's URL.

        Returns:
            str | None: The id, or None when nothing answers.
        """
        try:
            with urllib.request.urlopen(
                f"{host.rstrip('/')}/v1/models", timeout=MODELS_TIMEOUT_S
            ) as response:
                record = json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        data = record.get("data") if isinstance(record, dict) else None
        for entry in data if isinstance(data, list) else []:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and not entry.get("parent")
            ):
                return entry["id"].split(ADAPTER_SEPARATOR, 1)[0]
        return None

    @classmethod
    def base_problem(cls, manifest: AdapterManifest, served: dict[str, str] | None) -> str | None:
        """Says why an adapter does not fit the served base, if it does not.

        Args:
            manifest: The adapter's manifest.
            served: `served_base()`'s answer.

        Returns:
            str | None: None when the base matches; otherwise the reason (`base mismatch: …`).
        """
        if served is None:
            return (
                "base mismatch: this machine serves no model of the matrix with a pinned revision"
            )
        if (manifest.base_repo, manifest.base_revision) != (served["repo"], served["revision"]):
            return (
                f"base mismatch: made for {manifest.base_repo}@{manifest.base_revision[:12]}, "
                f"this machine serves {served['repo']}@{served['revision'][:12]} ({served['key']})"
            )
        snapshot = Path(served["snapshot"])
        if not snapshot.is_dir():
            key = served["key"]
            return (
                f"base mismatch: {key} is not downloaded here (`ling-admin model download {key}`)"
            )
        digest = AdapterBaseDigest.of(served["repo"], served["revision"], snapshot)
        if digest != manifest.base_digest:
            return (
                f"base mismatch: the base's files here digest to {digest[:19]}…, "
                f"the adapter's to {manifest.base_digest[:19]}…"
            )
        return None

    @classmethod
    def file_digest(cls, path: Path) -> str:
        """`sha256:<hex>` of a file's content, or an empty string when it cannot be read.

        Args:
            path: The file.

        Returns:
            str: The digest.
        """
        digest = hashlib.sha256()
        try:
            with Path(path).open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        return "sha256:" + digest.hexdigest()

    @classmethod
    def _read(cls, path: Path) -> bytes:
        """Reads a small file of the adapter.

        Args:
            path: The file.

        Returns:
            bytes: Its content.

        Raises:
            ValueError: When it is missing.
        """
        try:
            return path.read_bytes()
        except OSError as error:
            raise ValueError(f"{path.name} is missing ({error.strerror})") from error

    @classmethod
    def _json(cls, path: Path) -> dict[str, object]:
        """Reads a JSON object file of the adapter.

        Args:
            path: The file.

        Returns:
            dict[str, object]: Its object.

        Raises:
            ValueError: When it is missing or not an object.
        """
        try:
            record = json.loads(cls._read(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{path.name} is not JSON ({error})") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path.name} is not a JSON object")
        return record
