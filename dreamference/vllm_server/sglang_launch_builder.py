"""
SGLang Server Argument Builder for Dreamference.

This module provides the SGLangLaunchBuilder class, which turns a registry recipe marked
`engine: sglang` into the arguments of `python3 -m sglang.launch_server`.

One model is served by SGLang rather than vLLM, and only because of its drafter: Qwen3.8-27B's
DFlash2 block-diffusion drafter runs in SGLang and, in vLLM, only through an unmerged pull
request. Everything around the engine stays what `VLLMServerManager` already builds for vLLM --
the container name, the cgroup memory cap, the CPU limit, the OOM score, the mounts and the PSI
watchdog -- so this class produces only what follows the image name.
"""

from typing import Any, Dict, Final, List, Optional

from dreamference.hardware.model_downloader import ModelDownloader

# Where SGLang's torch.compile output lands inside the container. Under the dreamference cache
# mount, like vLLM's, so a compiled graph survives the container (a cold compile is minutes).
CONTAINER_SGLANG_INDUCTOR_DIR: Final[str] = "/root/.cache/dreamference/sglang/inductor"

# The host's HuggingFace hub as the container sees it, wherever HF_HOME puts it on the host
# (the docker prefix mounts it here).
CONTAINER_HF_HUB_DIR: Final[str] = ModelDownloader.CONTAINER_HF_HUB_DIR


class SGLangLaunchBuilder:
    """
    Builds the SGLang server command line from a model's registry recipe.
    """

    @classmethod
    def is_sglang(cls, recipe: Dict[str, Any]) -> bool:
        """
        Tells whether a recipe asks for SGLang.

        Args:
            recipe (Dict[str, Any]): The model's launch_overrides.

        Returns:
            bool: True for `engine: sglang`.
        """
        return str(recipe.get("engine", "vllm")).lower() == "sglang"

    @classmethod
    def snapshot_path(cls, repo_id: str, revision: Optional[str]) -> str:
        """
        Names a checkpoint so SGLang loads exactly the pinned commit, offline.

        A pinned checkpoint is given as its snapshot directory rather than as a repository ID
        plus `--revision`: SGLang looks some of a checkpoint's files up without the revision it
        was given, and offline the cache resolves that lookup through `refs/main`, which a
        download by commit does not write. The first launch on 2026-09-29 failed exactly so, in
        a restart loop. A directory needs no resolution at all.

        Args:
            repo_id (str): HuggingFace repository ID.
            revision (Optional[str]): Pinned commit, or None for the repository ID as is.

        Returns:
            str: A container path to the snapshot, or the repository ID when nothing is pinned.
        """
        if not revision:
            return repo_id
        return f"{CONTAINER_HF_HUB_DIR}/models--{repo_id.replace('/', '--')}/snapshots/{revision}"

    @classmethod
    def container_env(cls) -> Dict[str, str]:
        """
        Environment the SGLang container needs.

        Returns:
            Dict[str, str]: Variable name to value.
        """
        return {
            "TORCHINDUCTOR_CACHE_DIR": CONTAINER_SGLANG_INDUCTOR_DIR,
            # Weights are fetched on the host before the launch, at the pinned revisions, so the
            # server never reaches the Hub itself -- the same guarantee the vLLM path gives by
            # pre-downloading.
            "HF_HUB_OFFLINE": "1",
        }

    @classmethod
    def server_args(
        cls,
        hf_model: str,
        recipe: Dict[str, Any],
        port: int,
        max_model_len: Optional[int],
        gpu_memory_utilization: float,
        tool_call_parser: Optional[str],
        reasoning_parser: Optional[str],
        api_key: Optional[str] = None,
    ) -> List[str]:
        """
        Builds everything after the image name.

        Args:
            hf_model (str): HuggingFace repository ID of the target model.
            recipe (Dict[str, Any]): The model's launch_overrides.
            port (int): Port to serve on.
            max_model_len (Optional[int]): Context length; None leaves the checkpoint's own.
            gpu_memory_utilization (float): Passed as --mem-fraction-static.
            tool_call_parser (Optional[str]): SGLang tool-call parser name.
            reasoning_parser (Optional[str]): SGLang reasoning parser name.
            api_key (Optional[str]): Optional API key.

        Returns:
            List[str]: The command, starting with `python3 -m sglang.launch_server`.
        """
        args: List[str] = [
            "python3", "-m", "sglang.launch_server",
            "--model-path", cls.snapshot_path(hf_model, recipe.get("revision")),
            "--trust-remote-code",
            # The id clients see in /v1/models, and the one ling, Onyx and the agents send back.
            # Kept to the repository ID, as vLLM reports it, so switching engines renames nothing.
            "--served-model-name", hf_model,
            # Every interface, as vLLM: Onyx and OpenHands reach the server from Docker's bridge.
            "--host", "0.0.0.0",
            "--port", str(port),
            "--tp-size", "1",
            "--mem-fraction-static", str(gpu_memory_utilization),
        ]
        if max_model_len:
            args.extend(["--context-length", str(max_model_len)])
        if tool_call_parser:
            args.extend(["--tool-call-parser", tool_call_parser])
        if reasoning_parser:
            args.extend(["--reasoning-parser", reasoning_parser])
        if api_key:
            args.extend(["--api-key", api_key])

        speculative = recipe.get("speculative_config") or {}
        if speculative.get("model"):
            args.extend([
                "--speculative-algorithm", str(speculative.get("method", "DFLASH")).upper(),
                "--speculative-draft-model-path",
                cls.snapshot_path(str(speculative["model"]), speculative.get("revision")),
            ])
            if speculative.get("num_speculative_tokens"):
                args.extend(["--speculative-num-draft-tokens", str(speculative["num_speculative_tokens"])])
            if speculative.get("quantization"):
                args.extend(["--speculative-draft-model-quantization", str(speculative["quantization"])])

        patches = recipe.get("chat_template_patches")
        if patches and recipe.get("revision"):
            from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher

            args.extend(["--chat-template",
                         ChatTemplatePatcher.container_path(hf_model, recipe["revision"], patches)])

        args.extend(str(a) for a in recipe.get("extra_args") or [])
        return args
