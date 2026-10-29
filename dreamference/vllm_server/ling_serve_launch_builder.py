"""ling-serve's command line for a registry recipe marked `engine: ling`.

ling-serve is ling-engine's HTTP server, written for the default model alone (ling-engine
specs/DREAMFERENCE_LING_ENGINE_INTEGRATION.md §12, its command line in
DREAMFERENCE_LING_ENGINE_CLI.md §1). Everything around the engine stays what `VLLMServerManager`
builds for every engine -- the container name, the cgroup memory cap, the CPU limit, the OOM
score, the mounts and the PSI watchdog -- so this class produces only what follows the image name.
"""

import os
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

# 1.8.1: paging and fair pairing are per-launch switches. A recipe says `kv_pages` / `fair_pairing`
# (booleans; off in the ling recipe until the 10-31 night decides), and a night arm overrides either
# with the environment (`DREAMFERENCE_LING_PAGING=0|1`, `DREAMFERENCE_LING_FAIR_PAIRING=0|1`) so one
# image serves every arm. The flags go in explicitly, the default value too, so `docker inspect` names
# the arm. `--no-kv-pages` exists on an engine with the "paging-switch" feature (the page pool became
# ling-serve's default there, off by owner decision, so the flag is the only way to say flat);
# `--fair-pairing 1|0` on one with "fair-pairing".
PAGING_SWITCH_FEATURE: Final[str] = "paging-switch"
FAIR_PAIRING_FEATURE: Final[str] = "fair-pairing"
SWITCH_ON: Final[str] = "1"
SWITCH_OFF: Final[str] = "0"
NO_KV_PAGES_FLAG: Final[str] = "--no-kv-pages"
FAIR_PAIRING_FLAG: Final[str] = "--fair-pairing"
PAGING_ENV: Final[str] = "DREAMFERENCE_LING_PAGING"
FAIR_PAIRING_ENV: Final[str] = "DREAMFERENCE_LING_FAIR_PAIRING"

# ling-serve's switch for image input (`--vision on|off`, on by default), parsed since ling-engine
# 689690c, an ancestor of LING_ENGINE_KV_PAGES_SINCE: an engine with the "kv-pages" feature takes it,
# an older one exits 2 on it. A recipe with `vision: True` says `--vision on` (1.8.1), so the start
# never depends on the engine's default, /model_info reports image understanding and the launcher
# declares image input; `vision: False` turns the tower off (1.8.0), so the launcher declares text
# input only. Either way the plan-only check is asked with the same flag, so the tower's scratch and
# its embedding cache are in the plan exactly when the launch has them.
VISION_FLAG: Final[str] = "--vision"
VISION_ON: Final[str] = "on"
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
    def switch(cls, recipe: dict[str, Any], key: str, env_name: str, default: bool) -> bool:
        """Reads one per-launch switch: the environment, then the recipe, then the default.

        Args:
            recipe: The model's launch recipe.
            key: The recipe's key (`kv_pages`, `fair_pairing`).
            env_name: The environment variable a night arm sets (`0`/`1`; anything else is ignored).
            default: The value when neither names it.

        Returns:
            bool: Whether the switch is on for this launch.
        """
        env = os.environ.get(env_name, "").strip()
        if env in (SWITCH_OFF, SWITCH_ON):
            return env == SWITCH_ON
        value = recipe.get(key)
        return value if isinstance(value, bool) else default

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
            args.extend(cls._paged_engine_args(recipe))
        args.extend(str(a) for a in recipe.get("extra_args") or [])
        return args

    @classmethod
    def _paged_engine_args(cls, recipe: dict[str, Any]) -> list[str]:
        """Builds what only an engine with the page pool is given: sessions, KV layout, vision, pairing.

        The sessions and the batch are the recipe's, paged or flat; given both and the context, the
        engine sizes nothing and refuses a start it cannot hold. Paging is a per-launch switch.

        Args:
            recipe: The model's launch recipe, its `engine_features` naming "kv-pages".

        Returns:
            list[str]: The arguments, in launch order.
        """
        features = recipe.get("engine_features") or []
        args: list[str] = []
        if recipe.get("sessions"):
            args.extend(["--sessions", str(recipe["sessions"])])
        if recipe.get("batch"):
            args.extend(["--batch", str(recipe["batch"])])
        if cls.switch(recipe, "kv_pages", PAGING_ENV, True):
            args.append(KV_PAGES_FLAG)
        elif PAGING_SWITCH_FEATURE in features:
            args.append(NO_KV_PAGES_FLAG)
        if FAIR_PAIRING_FEATURE in features:
            fair = cls.switch(recipe, "fair_pairing", FAIR_PAIRING_ENV, False)
            args.extend([FAIR_PAIRING_FLAG, SWITCH_ON if fair else SWITCH_OFF])
        if isinstance(recipe.get("vision"), bool):
            args.extend([VISION_FLAG, VISION_ON if recipe["vision"] else VISION_OFF])
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
            **({"KV cache": "Flat"} if NO_KV_PAGES_FLAG in args else {}),
            **(
                {"Fair pairing": "On" if value(FAIR_PAIRING_FLAG) == SWITCH_ON else "Off"}
                if value(FAIR_PAIRING_FLAG)
                else {}
            ),
            **({"Image input": "On"} if value(VISION_FLAG) == VISION_ON else {}),
            **(
                {"Image input": "Off (--engine sglang reads images)"}
                if value(VISION_FLAG) == VISION_OFF
                else {}
            ),
        }
