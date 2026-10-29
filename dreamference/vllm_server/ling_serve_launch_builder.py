"""ling-serve's command line for a registry recipe marked `engine: ling`.

ling-serve is ling-engine's HTTP server, written for the default model alone (ling-engine
specs/DREAMFERENCE_LING_ENGINE_INTEGRATION.md §12, its command line in
DREAMFERENCE_LING_ENGINE_CLI.md §1). Everything around the engine stays what `VLLMServerManager`
builds for every engine -- the container name, the cgroup memory cap, the CPU limit, the OOM
score, the mounts and the PSI watchdog -- so this class produces only what follows the image name.
"""

from typing import Any, Final

from dreamference.hardware.model_matrix_registry import ENGINE_LING, ModelMatrixRegistry
from dreamference.vllm_server.sglang_launch_builder import SGLangLaunchBuilder

# The program in the image (docker/ling-serve/Dockerfile), named after the image as SGLang's launch
# names `python3 -m sglang.launch_server`.
LING_SERVE_PROGRAM: Final[str] = "ling-serve"

# The image label that carries the ling-engine commit the binary was built from.
LING_ENGINE_COMMIT_LABEL: Final[str] = "ai.dreamference.ling-engine.commit"

# ling-serve's flag for captured CUDA graphs per speculative step.
STEP_GRAPHS_FLAG: Final[str] = "--graphs"

# The engine feature (a recipe's `engine_features`) of the KV page pool, and ling-serve's flag for it.
KV_PAGES_FEATURE: Final[str] = "kv-pages"
KV_PAGES_FLAG: Final[str] = "--kv-pages"

# ling-serve's switch for image input (`--vision on|off`, on by default), parsed since ling-engine
# 689690c, an ancestor of LING_ENGINE_KV_PAGES_SINCE: an engine with the "kv-pages" feature takes it,
# an older one exits 2 on it. A recipe with `vision: False` turns the tower off (1.8.0), so
# /model_info reports no image understanding and the launcher declares text input only.
VISION_FLAG: Final[str] = "--vision"
VISION_OFF: Final[str] = "off"

# What `owned_by` says in ling-serve's /v1/models.
LING_SERVE_OWNER: Final[str] = "ling-engine"


class LingServeLaunchBuilder:
    """Builds ling-serve's command line from a model's registry recipe."""

    @classmethod
    def is_ling(cls, recipe: dict[str, Any]) -> bool:
        """Tells whether a recipe asks for ling-engine.

        Args:
            recipe: The model's launch recipe.

        Returns:
            bool: True for `engine: ling`.
        """
        return ModelMatrixRegistry.engine_of(recipe) == ENGINE_LING

    @classmethod
    def server_args(
        cls,
        hf_model: str,
        recipe: dict[str, Any],
        port: int,
        max_model_len: int | None,
        host: str = "0.0.0.0",
    ) -> list[str]:
        """Builds everything after the image name.

        The checkpoint and the drafter go in as snapshot directories in the container's view of
        the HuggingFace hub, at the recipe's pinned revisions, as for SGLang. The chat template is
        compiled into the engine (the checkpoint's with Mightling's patches), so no template file
        is passed; ling-serve has no flag for one, nor for an API key or a memory fraction.

        Args:
            hf_model: HuggingFace repository ID of the target model; also the served model id,
                which `model_key_for_served_id` maps back to the registry entry.
            recipe: The model's launch recipe (`engine: ling`).
            port: Port to serve on.
            max_model_len: Context length (`--max-context`); None leaves ling-serve's default.
            host: Address to serve on: every interface, or loopback behind the model gate.

        Returns:
            list[str]: The command, starting with `ling-serve`.
        """
        args: list[str] = [
            LING_SERVE_PROGRAM,
            "--model",
            SGLangLaunchBuilder.snapshot_path(hf_model, recipe.get("revision")),
            "--served-model-name",
            hf_model,
            "--host",
            host,
            "--port",
            str(port),
        ]
        if max_model_len:
            args.extend(["--max-context", str(max_model_len)])
        speculative = recipe.get("speculative_config") or {}
        if speculative.get("model"):
            args.extend(
                [
                    "--draft",
                    SGLangLaunchBuilder.snapshot_path(
                        str(speculative["model"]), speculative.get("revision")
                    ),
                ]
            )
            if speculative.get("num_speculative_tokens"):
                args.extend(["--draft-block", str(speculative["num_speculative_tokens"])])
        if KV_PAGES_FEATURE in (recipe.get("engine_features") or []):
            # An engine with the pool parses these flags. The sessions and the batch are the
            # recipe's, paged or flat; paging is the recipe's choice (`kv_pages`, off at 1.8.0,
            # where the recipe's context is the one four flat slots fit at). Given both the
            # sessions and the context, the engine sizes nothing and refuses a start it cannot hold.
            if recipe.get("sessions"):
                args.extend(["--sessions", str(recipe["sessions"])])
            if recipe.get("batch"):
                args.extend(["--batch", str(recipe["batch"])])
            if recipe.get("kv_pages"):
                args.append(KV_PAGES_FLAG)
            if recipe.get("vision") is False:
                args.extend([VISION_FLAG, VISION_OFF])
        args.extend(str(a) for a in recipe.get("extra_args") or [])
        return args

    @classmethod
    def server_args_of(cls, argv: list[str]) -> list[str] | None:
        """Finds ling-serve's own arguments in a container's command line.

        Args:
            argv: A `docker run` command or a container's argv.

        Returns:
            list[str] | None: What follows `ling-serve`, or None when it is not a ling-serve launch.
        """
        if LING_SERVE_PROGRAM not in argv:
            return None
        return argv[argv.index(LING_SERVE_PROGRAM) + 1 :]

    @classmethod
    def facts(cls, image: str, labels: dict[str, str], args: list[str]) -> dict[str, str]:
        """What `server status` reports about a running ling-serve.

        Args:
            image: The container's image.
            labels: The image's labels (the engine commit is one).
            args: ling-serve's own arguments (`server_args_of`).

        Returns:
            dict[str, str]: Label to value, formatted for display.
        """

        def value(name: str) -> str | None:
            return args[args.index(name) + 1] if name in args[:-1] else None

        commit = str(labels.get(LING_ENGINE_COMMIT_LABEL) or "unknown")
        drafter = value("--draft")
        drafter_name = drafter.rsplit("/snapshots/", 1)[0].rsplit("/", 1)[-1] if drafter else ""
        return {
            "Image": image or "Unknown",
            "Engine": f"ling-serve (ling-engine {commit[:12]})",
            "Max Model Len": value("--max-context") or "ling-serve's default",
            "Speculative": (
                f"DFlash2, block {value('--draft-block') or 'default'}, via {drafter_name}"
                if drafter
                else "Disabled"
            ),
            "Step graphs": "On" if STEP_GRAPHS_FLAG in args else "Off",
            **(
                {"Sessions": f"{value('--sessions')} (batch {value('--batch') or 'default'})"}
                if value("--sessions")
                else {}
            ),
            **({"KV cache": "Paged"} if KV_PAGES_FLAG in args else {}),
            **(
                {"Image input": "Off (--engine sglang reads images)"}
                if value(VISION_FLAG) == VISION_OFF
                else {}
            ),
        }
