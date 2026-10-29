"""Serving installed adapters on the model server (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §8).

The LoRA flags are launch flags, so they come from the registry (the recipe's `lora` key), and
they are passed only when an adapter is installed for the model being launched: until then the
launch line is exactly what it was, with nothing to recompile and nothing for the recipe drift
check to report. With adapters, `server start` checks every installed one again (signature, files,
configuration, base), gives the engine those that pass at its start (their modules fix the GPU
pool's module set, which a later load cannot widen), adds the adapters' runtime patch to the
file mounted over the image's `/v1/responses`, starts the engine with an API key only the gate
holds, and writes the verified list the gate loads from. Adapters need the gate: a launch without it serves none.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Any, ClassVar, Final

from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.adapter_store import AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
from dreamference.vllm_server.model_gate_adapters import KEY_FILE, LIST_FILE
from dreamference.vllm_server.sglang_runtime_patcher import SGLangRuntimePatcher

FILE_MODE: Final[int] = 0o600
GIB: Final[float] = float(1024**3)
DEFAULT_MAX_LOADED: Final[int] = 8


def say(message: str) -> None:
    """Prints one line with Rich's markup.

    Rich is imported here, not at the top: every `ling-admin` command imports this module
    (tests/test_cli_startup.py).
    """
    from rich.console import Console

    Console().print(message)


class AdapterServing:
    """Decides which adapters a launch serves, and prepares what serving them needs."""

    # Verification results within one process, keyed by what would change them: the launch
    # command is built more than once per start (and by the drift check), the files are hashed once.
    checked: ClassVar[dict[tuple[object, ...], str | None]] = {}

    @classmethod
    def gate_dir(cls) -> Path:
        """The gate's state folder, where the list and the engine's key are written.

        Returns:
            Path: `ModelGate`'s folder.
        """
        from dreamference.vllm_server.model_gate import GATE_DIR

        return GATE_DIR

    @classmethod
    def launch_adapters(cls, model_key: str) -> list[dict[str, object]]:
        """The installed adapters a launch of a model serves: those that pass every check.

        Args:
            model_key: The model being launched.

        Returns:
            list[dict[str, object]]: Their installed entries, by name; empty when the model's
            recipe serves no adapters or none is installed for it.
        """
        if ModelMatrixRegistry.get_lora_recipe(model_key) is None:
            return []
        base = AdapterVerifier.base_for(model_key)
        if base is None:
            return []
        chosen = []
        for name, entry in sorted(AdapterStore.entries().items()):
            if (entry.get("base_repo"), entry.get("base_revision")) != (
                base["repo"],
                base["revision"],
            ):
                continue
            problem = cls.problem(entry, base)
            if problem is None:
                chosen.append(entry)
            else:
                say(f"⚠️  The adapter {name} is not served: {problem}")
        return chosen

    @classmethod
    def problem(cls, entry: dict[str, object], base: dict[str, str]) -> str | None:
        """An installed adapter's check, remembered for files that have not changed.

        Args:
            entry: Its installed entry.
            base: The base being launched.

        Returns:
            str | None: Why it is not served, or None.
        """
        folder = AdapterInstaller.folder_of(entry)
        stamps = []
        for relative in sorted(entry.get("files") or {}):  # type: ignore[call-overload]
            try:
                stat = (folder / relative).stat()
                stamps.append((relative, stat.st_size, stat.st_mtime_ns))
            except OSError:
                stamps.append((relative, -1, -1))
        key = (entry.get("name"), entry.get("manifest_sha256"), base["revision"], tuple(stamps))
        if key not in cls.checked:
            cls.checked[key] = AdapterInstaller.verify(entry, base)
        return cls.checked[key]

    @classmethod
    def container_path(cls, entry: dict[str, object]) -> str:
        """Where the engine's container (and the gate's) finds an adapter's snapshot.

        Args:
            entry: Its installed entry.

        Returns:
            str: The snapshot under the container's hub mount.
        """
        return f"{ModelDownloader.CONTAINER_HF_HUB_DIR}/{entry['snapshot']}"

    @classmethod
    def server_args(cls, recipe: dict[str, Any], adapters: list[dict[str, object]]) -> list[str]:
        """The SGLang flags that serve the adapters, from the recipe's `lora` key.

        Args:
            recipe: The model's launch overrides.
            adapters: `launch_adapters()`'s answer.

        Returns:
            list[str]: Nothing without adapters; otherwise LoRA on, its pool, the adapters given at
            start (at most as many as the engine holds) and the engine's key.
        """
        lora = recipe.get("lora")
        if not adapters or not isinstance(lora, dict):
            return []
        loaded = int(lora.get("max_loaded_loras", DEFAULT_MAX_LOADED))
        at_start = [f"{entry['name']}={cls.container_path(entry)}" for entry in adapters[:loaded]]
        return [
            "--enable-lora",
            "--max-lora-rank",
            str(lora["max_lora_rank"]),
            "--max-loras-per-batch",
            str(lora["max_loras_per_batch"]),
            "--max-loaded-loras",
            str(loaded),
            # An adapter key the loader cannot place is an error, never an adapter silently dropped.
            "--lora-strict-loading",
            "--lora-paths",
            *at_start,
            "--api-key",
            cls.engine_key(),
        ]

    @classmethod
    def with_headroom(
        cls, recipe: dict[str, Any], adapters: list[dict[str, object]]
    ) -> dict[str, Any]:
        """The recipe with the container's headroom raised by the adapters the engine may hold.

        The GPU pool is inside the engine's memory fraction (it displaces KV cache); the loaded
        adapters sit in host memory, which on a GB10 is the same memory and the container's
        cgroup cap counts, so the cap grows by `max_loaded_loras` times the largest adapter.

        Args:
            recipe: The model's launch overrides.
            adapters: `launch_adapters()`'s answer.

        Returns:
            dict[str, Any]: A copy for the docker prefix; the recipe itself when nothing is served.
        """
        lora = recipe.get("lora")
        if not adapters or not isinstance(lora, dict):
            return recipe
        largest = max(cls.size(entry) for entry in adapters)
        extra = int(lora.get("max_loaded_loras", DEFAULT_MAX_LOADED)) * largest / GIB
        headroom = float(recipe.get("container_headroom_gb", 0.0)) + round(extra + 0.5)
        return {**recipe, "container_headroom_gb": headroom}

    @classmethod
    def size(cls, entry: dict[str, object]) -> int:
        """An adapter's bytes on disk.

        Args:
            entry: Its installed entry.

        Returns:
            int: The size of the files its manifest lists.
        """
        folder = AdapterInstaller.folder_of(entry)
        total = 0
        for relative in entry.get("files") or {}:  # type: ignore[attr-defined]
            try:
                total += (folder / str(relative)).stat().st_size
            except OSError:
                continue
        return total

    @classmethod
    def engine_key(cls) -> str:
        """The engine's API key, made once and kept 0600 in the gate's folder.

        Every launch line (and the drift check's rebuild of it) names the same one.

        Returns:
            str: The key.
        """
        path = cls.gate_dir() / KEY_FILE
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing
        key = secrets.token_hex(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        return key

    @classmethod
    def prepare(
        cls, model_key: str, docker_image: str, gated: bool, engine: str | None = None
    ) -> bool:
        """Before the engine starts: writes the runtime patches and the gate's list.

        Args:
            model_key: The model being launched.
            docker_image: The engine's image.
            gated: Whether the launch is behind the gate with no API key of the caller's; without
                that, nothing is served and the gate's list is removed.
            engine: The engine it is launched with; None is the model's default. Only a recipe
                with a `lora` key serves adapters (SGLang's; ling-serve serves none).

        Returns:
            bool: False when adapters were to be served and the patch could not be prepared (the
            launch must then serve none); True otherwise.
        """
        adapters = cls.launch_adapters(model_key) if gated else []
        recipe = ModelMatrixRegistry.get_launch_overrides(model_key, engine)
        lora = recipe.get("lora")
        if not adapters or not isinstance(lora, dict):
            cls.clear()
            return True
        # One file with the recipe's own patch and the adapters' (the launch mounts it).
        problem = SGLangRuntimePatcher.prepare(
            docker_image, SGLangRuntimePatcher.for_launch(recipe, lora=True)
        )
        if problem:
            say(f"⚠️  Adapters are not served at this start: {problem}.")
            cls.clear()
            return False
        cls.publish(ModelMatrixRegistry.resolve_hf_repo(model_key), adapters, lora)
        names = ", ".join(str(entry["name"]) for entry in adapters)
        say(f"🧩 Adapters served behind the gate: {names}")
        return True

    @classmethod
    def publish(cls, base_id: str, adapters: list[dict[str, object]], lora: dict[str, Any]) -> None:
        """Writes the verified list the gate loads from.

        Args:
            base_id: The served id of the base.
            adapters: The verified adapters.
            lora: The recipe's `lora` key.
        """
        record = {
            "base": base_id,
            "max_loaded": int(lora.get("max_loaded_loras", DEFAULT_MAX_LOADED)),
            "adapters": {
                str(entry["name"]): {
                    "path": cls.container_path(entry),
                    "files": entry.get("files"),
                    **{
                        key: entry.get(key)
                        for key in ("version", "display_name", "description", "trust")
                    },
                }
                for entry in adapters
            },
        }
        cls._write(record)

    @classmethod
    def withdraw(cls, name: str) -> None:
        """Takes a removed adapter off the gate's list, so its next request is refused.

        Args:
            name: The adapter's name.
        """
        try:
            record = json.loads((cls.gate_dir() / LIST_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        adapters = record.get("adapters") if isinstance(record, dict) else None
        if isinstance(adapters, dict) and adapters.pop(name, None) is not None:
            cls._write(record)

    @classmethod
    def _write(cls, record: dict[str, Any]) -> None:
        """Replaces the gate's list whole, 0600 from its first byte.

        Args:
            record: The list.
        """
        path = cls.gate_dir() / LIST_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
        staging.replace(path)

    @classmethod
    def clear(cls) -> None:
        """Removes the gate's list: the server serves no adapters."""
        try:
            (cls.gate_dir() / LIST_FILE).unlink()
        except OSError:
            return

    @classmethod
    def gate_mounts(cls) -> list[str]:
        """The gate's extra mount while adapters are served: the hub cache, read-only.

        The gate hashes an adapter's files there before it loads one.

        Returns:
            list[str]: Volume options, or nothing.
        """
        if not (cls.gate_dir() / LIST_FILE).is_file():
            return []
        return [
            "-v",
            f"{ModelDownloader.get_hf_cache_dir()}:{ModelDownloader.CONTAINER_HF_HUB_DIR}:ro",
        ]
