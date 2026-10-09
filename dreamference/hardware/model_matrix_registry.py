"""
Model Matrix Registry for Dreamference.

This module provides the ModelMatrixRegistry class which acts as the single source of truth
for supported LLMs, speculative decoding draft models, and short alias resolution.
"""

from typing import Any, Dict, Optional, Final
from dreamference.hardware.model_spec import ModelSpec

# Quantization formats that a checkpoint declares in its own config.json. vLLM auto-detects these,
# and passing an explicit --quantization alongside them fights that detection.
#
# 'AUTOROUND-INT4' is spelled out rather than folded into a bare 'INT4' because the two mean
# different things in this table: several entries list 'INT4' to say "could be served in INT4",
# which declares nothing, while an AutoRound checkpoint ships
# quantization_config.quant_method='auto-round' in config.json and needs no flag.
SELF_DECLARING_PRECISIONS: Final[frozenset] = frozenset(
    {"NVFP4", "MXFP4", "AWQ", "GPTQ", "AUTOROUND-INT4"}
)

# Single source of truth for the model Dreamference serves when nothing else is specified. Imported by
# the config layer and the vLLM launcher so the two cannot drift apart.
#
# qwen3.8-27b-nvfp4-dflash2 since 2026-09-29, on SGLang: decode ties the 122B on prose and code
# (24.1 / 47.5 against 23.8 / 49.9 tok/s) and beats it on JSON (82.5 against 53.1), the live
# slash-command suite passes as it did, a real ling task finished in 10 s against 34, context is
# 262K against 32K, and ~29 GB more host memory stays free while serving. The 122B recipes on vLLM
# (hybrid-dflash, the default from 2026-08-23, and int4-dflash) and the 35B were removed on
# 2026-10-07: Mightling serves this one model.
DEFAULT_MODEL_ALIAS: Final[str] = "qwen3.8-27b-nvfp4-dflash2"

# Models Mightling served once and removed, keyed by every name an older configuration could hold for
# them (alias, HuggingFace repository, display name, all lowercase), each naming the release that
# removed it. Unknown keys are otherwise taken as raw HuggingFace repositories, so without this a
# 1.4.1 dreamference.toml naming a removed alias was accepted by `main-model set`, and `model
# download` and `server start` tried to fetch a repository of that name.
REMOVED_MODELS: Final[Dict[str, str]] = {
    name: "1.5.1"
    for name in (
        "qwen3.5-122b-a10b-hybrid-dflash",
        "qwen 3.5 122b-a10b (int4+fp8 hybrid + dflash + dense-bandwidth stack)",
        "qwen3.5-122b-a10b-int4-dflash",
        "qwen 3.5 122b-a10b (int4 autoround + dflash)",
        "intel/qwen3.5-122b-a10b-int4-autoround",
        "qwen3.5-122b-a10b-nvfp4",
        "qwen 3.5 122b-a10b (nvfp4)",
        "nvidia/qwen3.5-122b-a10b-nvfp4",
        "qwen3.6-35b-a3b-nvfp4",
        "qwen 3.6 35b-a3b (nvfp4)",
        "nvidia/qwen3.6-35b-a3b-nvfp4",
        "qwen3.5-122b-a10b-dflash-draft",
        "qwen 3.5 122b-a10b dflash drafter (draft model)",
        "z-lab/qwen3.5-122b-a10b-dflash",
    )
}

# The diffusion model served beside the main one. A separate default rather than a mode of the
# main model, because the two run in parallel: every configuration names both, and `ling-admin server
# start` launches both.
DEFAULT_DIFFUSION_MODEL_ALIAS: Final[str] = "tiny-a2d-coder-0.5b-diffusion"

# Whether Mightling uses a diffusion model at all. Off since 2026-10-03: the only one that fits beside
# the main model (Tiny-A2D 0.5B) was measured unusable in every role tried (FAST_TOOLS §1,
# COMPACTION §9.3), so it cost memory and a download for nothing. Off means it is never started
# or downloaded, a leftover container is removed, and nothing names it to the user: no
# `diffusion-model` command, no `server start` flags, no endpoint row, no entry in `model list`.
# The code stays, for a capable diffusion model later (FAST_TOOLS §3); setting this to True
# restores all of it.
DIFFUSION_ENABLED: Final[bool] = False

# The production recipe: the SGLang launch of qwen3.8-27b-nvfp4-dflash2. A module constant so an
# entry measured against it (night 2's all-NVFP4 checkpoint, specs/DREAMFERENCE_MODELS.md §2.2)
# can take it whole and change only the checkpoint, which is what makes that A/B fair.
QWEN38_SGLANG_RECIPE: Final[Dict[str, Any]] = {
    "engine": "sglang",
    "docker_image": (
        "lmsysorg/sglang@sha256:"
        "d6e7288627be8b02be88e4bba38e73f6d50e2826869f753c13a4c4385ab3eda9"
    ),
    "revision": "52d1adc5f38aa5ebf099c29ed7025ba34cfbb854",
    "max_model_len": 262144,
    # SGLang's --mem-fraction-static: weights plus KV pool. What lies outside it (CUDA
    # graphs, torch.compile, activations) is why the container needs more headroom
    # than a vLLM arena of the same fraction.
    "gpu_memory_utilization": 0.50,
    "container_headroom_gb": 24.0,
    "tool_call_parser": "qwen3_coder",
    "reasoning_parser": "qwen3",
    "speculative_config": {
        "method": "DFLASH",
        "model": "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal",
        "revision": "bd7a934213c47a9e7ef69eef36bb3325f47fd1f1",
        "num_speculative_tokens": 12,
        "quantization": "modelopt_fp4",
    },
    # The checkpoint's own chat template refused two things our clients send, and
    # reasoned at its most expensive level by default. Applied to a copy at launch
    # (ChatTemplatePatcher); each anchor must match exactly once or the start stops.
    "chat_template_patches": [
        # Codex offers `minimal`/`high` (and `max` exists elsewhere); the template knew
        # only xhigh/medium/low and answered HTTP 400. The default drops from xhigh to
        # medium: hasso5703 measured xhigh at 3.19x medium's thinking tokens and a
        # lower HumanEval (93.9% against 98.2%), five failures being budget
        # truncations. ling itself sends `none` and is unaffected.
        [
            "    {%- set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}\n"
            "    {%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}",
            "    {%- set resolved_reasoning_effort = reasoning_effort|default('medium') %}\n"
            "    {%- if resolved_reasoning_effort in ('max', 'high') %}\n"
            "        {%- set resolved_reasoning_effort = 'xhigh' %}\n"
            "    {%- elif resolved_reasoning_effort == 'minimal' %}\n"
            "        {%- set resolved_reasoning_effort = 'low' %}\n"
            "    {%- endif %}\n"
            "    {%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}",
        ],
        # A system message after the first one raised "System message must be at the
        # beginning" on the chat-completions path; it becomes a reminder in the turn.
        [
            "        {%- if not loop.first %}\n"
            "            {{- raise_exception('System message must be at the beginning.') }}\n"
            "        {%- endif %}",
            "        {%- if not loop.first %}\n"
            "            {{- '<|im_start|>user\\n<system-reminder>\\n' + content + "
            "'\\n</system-reminder><|im_end|>\\n' }}\n"
            "        {%- endif %}",
        ],
    ],
    "extra_args": [
        "--attention-backend", "flashinfer",
        # FlashInfer's plain sampling kernel, used only when a request asks for no
        # truncation at all (top_p 1 and no top_k: what the completions endpoint does
        # by default), returned token 0 ('!') for 16 of 16 sampled requests on this
        # GB10; any top_p < 1 or any top_k was clean. PyTorch's sampler: 0 of 16, and
        # no measurable speed cost (greedy 25.5 / 50.3 / 87.0 tok/s prose/code/JSON).
        "--sampling-backend", "pytorch",
        "--chunked-prefill-size", "8192",
        "--disable-prefill-cuda-graph",
        "--cuda-graph-max-bs", "8",
        "--disable-flashinfer-autotune",
        "--mamba-radix-cache-strategy", "extra_buffer",
        "--mamba-ssm-dtype", "bfloat16",
        "--max-mamba-cache-size", "96",
        "--max-running-requests", "8",
        "--enable-torch-compile",
        "--torch-compile-max-bs", "4",
        "--num-continuous-decode-steps", "2",
        # Stops the scheduler busy-polling a CPU core while no request is running,
        # which on a box that is always on is most of the time.
        "--sleep-on-idle",
        "--enable-metrics",
    ],
}

class ModelMatrixRegistry:
    """
    Registry holding qualified models for NVIDIA GB10 hardware and short alias resolution logic.
    """

    # Static registry of supported target models and speculative draft models
    MATRIX: Final[Dict[str, ModelSpec]] = {
        "qwen3.8-27b-nvfp4-dflash2": ModelSpec(
            name="Qwen 3.8 27B (NVFP4 + DFlash2, SGLang)",
            params_b=27.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=20.0,
            max_memory_gb=70.0,
            compatible_gb10=True,
            notes=(
                "The one entry served by SGLang instead of vLLM (`engine: sglang`), because the speed "
                "is in the drafter and only SGLang runs it: DFlash2 is a block-diffusion drafter "
                "vLLM supports only through an unmerged pull request.\n\n"
                "Measured here (2026-09-29, single stream, temperature 0, thinking off, median of "
                "three after a warm-up, decode net of time to first token): prose 25.5, code 50.3, "
                "JSON 87.0 tok/s with the PyTorch sampler (24.1 / 47.5 / 82.5 before it); TTFT 0.22 s; "
                "prefill ~1,700 tok/s on a 13,333-token prompt the prefix cache could not help, "
                "~1,000 tok/s on 115,628 tokens (needle found). Image input works; four ling "
                "tasks at once finished in 23 s with >= 39.8 GB still available. First boot 7.5 min (torch.compile), with ~38.7 GB of "
                "host memory still available while serving, against ~10 GB beside the 122B. The "
                "same `ling exec` coding task took 10 s here and 34 s on the 122B.\n\n"
                "Published single-Spark numbers: vLLM with the checkpoint's own MTP managed 24.0 "
                "tok/s on a code prompt (26.0 with no speculation), SGLang with DFlash2 50.9 on "
                "code, 25.4 on long prose and 66.6 on short chat (MiaAI-Lab), 71.4 greedy median "
                "(hasso5703). Our 122B does 49.9 / 23.8 on code / prose, so decode is a tie there "
                "and structured output is faster; the larger gains are quality (Terminal-Bench 2.1 "
                "73.0), context (262K against 32K) and memory (~20 GB of weights against ~71).\n\n"
                "Recipe: github.com/hasso5703/dgx-spark-qwen38 (v1.18, measured 2026-09-17): the "
                "pinned lmsysorg/sglang v0.5.19 image, RadixArk's NVFP4 conversion and the "
                "RTN-calibrated NVFP4 DFlash2 drafter at the revisions pinned below, 12 draft "
                "tokens (the recipe says 16; 12 measured +7% on the replay set, 48.3 against "
                "45.1 tok/s single stream, speculation still exact, applied 2026-10-09), "
                "memory fraction 0.50, flashinfer attention, mamba radix cache in "
                "extra_buffer mode, torch.compile up to batch 4. Not copied: its API key and "
                "keepalive proxy (Mightling reaches the server the way it reaches vLLM) and its "
                "reasoning-effort default."
            ),
            hf_repo_id="RadixArk/Qwen3.8-27B-NVFP4",
            launch_overrides=QWEN38_SGLANG_RECIPE,
            supports_vision=True,
        ),
        # Night 2's candidate (specs/DREAMFERENCE_MODELS.md §2.2), not a default: production
        # switches to it only if a SWE-bench A/B on this machine holds quality and it is faster.
        "qwen3.8-27b-minima-nvfp4-dflash2": ModelSpec(
            name="Qwen 3.8 27B Minima (all-NVFP4 + DFlash2, SGLang)",
            params_b=27.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=19.0,
            max_memory_gb=70.0,
            compatible_gb10=True,
            notes=(
                "Minima (arXiv 2609.04098): Qwen3.8-27B with all 496 linear layers in NVFP4 W4A4, "
                "the Gated DeltaNet projections and gates included, by post-training quantization "
                "with llm-compressor (compressed-tensors `nvfp4-pack-quantized`), plus static FP8 "
                "KV-cache scales; embeddings, lm_head, conv1d and norms stay BF16. 18.8 GB on disk "
                "against the production checkpoint's FP8/NVFP4 mix. A text-only "
                "Qwen3_5ForCausalLM extraction: no vision tower, so no image input. Its own paper "
                "reports BF16 quality within seed noise and decode at 47 against 51 tok/s for the "
                "production recipe at concurrency 1 (RTX PRO 6000, vLLM 0.27.1).\n\n"
                "Served by the production recipe unchanged (QWEN38_SGLANG_RECIPE: the same image, "
                "flags, chat-template patches and DFlash2 drafter at 12 tokens), so a comparison "
                "differs in the target's weights alone. Two departures, both for that reason: "
                "the checkpoint's own revision, and `--kv-cache-dtype bfloat16`, because SGLang "
                "turns a compressed-tensors kv_cache_scheme into an FP8 KV pool under `auto`, "
                "while production's pool is BF16. The drafter fits: it carries no embeddings or "
                "lm_head of its own, and reads hidden states of width 5,120 at layers 5, 19, 33, "
                "47 and 61 of 64, which this checkpoint has; its acceptance is measured, not "
                "assumed, because it was trained against other hidden states.\n\n"
                "SGLang v0.5.19 serves the architecture (models/qwen3_5_text.py, with DFlash "
                "capture and the extra_buffer mamba cache) and the format "
                "(CompressedTensorsW4A4Fp4). That scheme takes the largest global scale of a fused "
                "group (qkv+z, b+a, q/k/v, gate/up) without rescaling, which the paper shows "
                "silently mis-scales the DeltaNet gates; this checkpoint ships its fused groups "
                "harmonized to one global scale each, checked on the download "
                "(specs/DREAMFERENCE_MODELS.md §2.2, scripts/check_nvfp4_fused_scales.py)."
            ),
            hf_repo_id="minima-ai/mnma_qwen3.8_27b_nvfp4",
            launch_overrides={
                **QWEN38_SGLANG_RECIPE,
                "revision": "16e768e7d0461b0b86e565ecedd08a24eca53e9a",
                "extra_args": [*QWEN38_SGLANG_RECIPE["extra_args"], "--kv-cache-dtype", "bfloat16"],
            },
            supports_vision=False,
        ),
        "qwen3.8-27b-dflash2-draft": ModelSpec(
            name="Qwen 3.8 27B DFlash2 Drafter (NVFP4, Draft Model)",
            params_b=1.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=1.0,
            max_memory_gb=2.0,
            compatible_gb10=True,
            notes=(
                "Drafter for qwen3.8-27b-nvfp4-dflash2, named by its speculative_config and listed "
                "so the memory gates have a size before the snapshot is on disk."
            ),
            hf_repo_id="maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal",
        ),
        "tiny-a2d-coder-0.5b-diffusion": ModelSpec(
            name="Tiny-A2D Qwen2.5-Coder 0.5B (bd3lm diffusion)",
            params_b=0.6,
            supported_precisions=["BF16"],
            # ~1.2 GiB of BF16 shards plus activations. Small enough that the diffusion sidecar
            # runs it under a fixed container memory cap instead of the PSI watchdog.
            min_memory_gb=1.5,
            max_memory_gb=3.0,
            compatible_gb10=True,
            notes=(
                "Default diffusion model. A Qwen2.5-Coder 0.5B converted to a block-diffusion "
                "(bd3lm) language model by the dLLM project's Tiny-A2D recipe: generation "
                "denoises 32-token blocks over up to 128 steps instead of decoding "
                "autoregressively. NOT vLLM-servable -- the checkpoint loads through "
                "transformers as AutoModelForMaskedLM with trust_remote_code and generates via "
                "its own remote-code sampler -- which is what is_diffusion records and why "
                "DiffusionServerManager serves it rather than the vLLM launcher. The model "
                "card's sampler settings (steps=128, block_size=32, temperature=0.0, "
                "cfg_scale=0.0, remasking='low_confidence') are applied by the sidecar's "
                "serving script, which falls back to bare max_new_tokens if the remote code's "
                "generate signature differs. Apache 2.0."
            ),
            hf_repo_id="dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1",
            is_diffusion=True,
        ),
    }

    @classmethod
    def key_for_served_id(cls, served_id: str, preferred: Optional[str] = None) -> Optional[str]:
        """
        Finds the registry entry for a model id a running server reports in /v1/models.

        The server names its model by checkpoint, and several entries can share one (recipes
        differing only in launch flags), so a preferred key wins when it matches.

        Args:
            served_id (str): The id from the server's /v1/models.
            preferred (Optional[str]): Key to return if it serves that checkpoint.

        Returns:
            Optional[str]: The matching key, or None when no entry serves that checkpoint.
        """
        if preferred and cls.resolve_hf_repo(preferred) == served_id:
            return preferred
        for key in cls.MATRIX:
            if cls.resolve_hf_repo(key) == served_id:
                return key
        return None

    @classmethod
    def resolve_hf_repo(cls, model_key: str) -> str:
        """
        Resolves model alias, repo string, or display name to official HuggingFace repository ID.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            str: Official HuggingFace repository identifier (or original string if unmapped).
        """
        if not model_key:
            return model_key
        spec = cls.get_spec(model_key)
        return spec.hf_repo_id if spec else model_key

    @classmethod
    def get_spec(cls, model_key: str) -> Optional[ModelSpec]:
        """
        Retrieves ModelSpec for given short alias, HuggingFace repo ID, or display name.

        Args:
            model_key (str): Short model name key, HF repo ID, or display name.

        Returns:
            Optional[ModelSpec]: ModelSpec object or None if key is unrecognized.
        """
        if not model_key:
            return None
        key = model_key.strip().lower()
        if key in cls.MATRIX:
            return cls.MATRIX[key]
        for spec in cls.MATRIX.values():
            if spec.hf_repo_id.lower() == key or spec.name.lower() == key:
                return spec
        norm_key = key.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
        for alias, spec in cls.MATRIX.items():
            norm_alias = alias.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_hf = spec.hf_repo_id.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_name = spec.name.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            if norm_key in (norm_alias, norm_hf, norm_name):
                return spec
        return None

    @classmethod
    def get_launch_overrides(cls, model_key: str) -> Dict[str, Any]:
        """
        Retrieves the per-model vLLM launch recipe for the given alias, repo ID, or display name.

        Args:
            model_key (str): Model alias, HF repo ID, or display name. Unrecognized keys
                yield an empty recipe, leaving the caller on global defaults.

        Returns:
            Dict[str, Any]: Copy of the model's launch_overrides, safe for the caller to mutate.
        """
        if not model_key:
            return {}
        spec = cls.get_spec(model_key)
        return dict(spec.launch_overrides) if spec else {}

    @classmethod
    def get_speculative_draft_repo(cls, model_key: str) -> Optional[str]:
        """
        Retrieves the separate draft checkpoint a model's recipe speculates against, if any.

        Two kinds of speculation live in `launch_overrides['speculative_config']` and only one of
        them names a second checkpoint. Self-speculation (MTP, Eagle heads) ships inside the target
        and has no 'model' key; a drafter like DFlash is its own repository that has to be fetched
        and counted against memory alongside the target. Callers that pre-download weights or size
        the pre-flight memory gates need to see the second kind, and it is invisible to them
        otherwise — the `draft_model` argument on the launcher only covers drafters named by the
        caller, not ones the recipe brings with it.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.

        Returns:
            Optional[str]: HuggingFace repo ID of the recipe's draft model, or None when the model
                is unknown, speculates against itself, or does not speculate at all.
        """
        spec_config = cls.get_launch_overrides(model_key).get("speculative_config") or {}
        return spec_config.get("model") or None

    @classmethod
    def supports_vision(cls, model_key: str) -> bool:
        """
        Reports whether the model accepts image input alongside text.

        vLLM needs no telling -- it reads the modality off the checkpoint -- but API clients do.
        Onyx, for one, refuses an upload with "The current model does not support image input"
        unless the model entry it holds advertises the capability, so the fact has to travel from
        this registry into that client's configuration.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the checkpoint is vision-capable.
        """
        spec = cls.get_spec(model_key) if model_key else None
        return bool(spec and spec.supports_vision)

    @classmethod
    def is_diffusion(cls, model_key: str) -> bool:
        """
        Reports whether the model is a diffusion language model.

        The distinction routes serving: an autoregressive checkpoint goes to the vLLM launcher, a
        diffusion one to the transformers-based sidecar, and pointing either at the other kind
        fails only at load time with an unhelpful error. Unknown keys report False, which sends
        them down the vLLM path -- the path with pre-flight gates that can explain a bad load.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the checkpoint generates by diffusion rather than autoregression.
        """
        spec = cls.get_spec(model_key) if model_key else None
        return bool(spec and spec.is_diffusion)

    @classmethod
    def diffusion_enabled(cls) -> bool:
        """
        Reports whether Mightling serves, downloads and shows diffusion models at all.

        Read through this method rather than the constant, at call time, so every caller sees
        the same switch and a test can turn it on in one place.

        Returns:
            bool: The value of DIFFUSION_ENABLED.
        """
        return DIFFUSION_ENABLED

    @classmethod
    def is_offered(cls, model_key: str) -> bool:
        """
        Reports whether a model is offered to the user: listed, downloadable, selectable.

        Every model is, except a diffusion model while diffusion is switched off. Unknown keys
        are offered, because a raw HuggingFace repository may still be downloaded by name.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: False for a removed model, and for a diffusion model with diffusion switched off.
        """
        if cls.removed_in(model_key):
            return False
        return cls.diffusion_enabled() or not cls.is_diffusion(model_key)

    @classmethod
    def removed_in(cls, model_key: Optional[str]) -> Optional[str]:
        """
        Names the release that removed a model, if `model_key` names one Mightling no longer serves.

        Args:
            model_key (Optional[str]): Short model alias, HF repo ID, or display name.

        Returns:
            Optional[str]: The release that removed it, or None for any other key.
        """
        if not model_key:
            return None
        return REMOVED_MODELS.get(model_key.strip().lower())

    @classmethod
    def removed_message(cls, model_key: str, configured: bool = False) -> str:
        """
        What to tell a user whose command or configuration names a removed model.

        Args:
            model_key (str): The removed model's name, as the user gave it.
            configured (bool): True when it came from the configuration rather than the command
                line, so the message says where it is set.

        Returns:
            str: Two lines: what happened, and the command that fixes it.
        """
        where = " (it is the model in your Mightling configuration)" if configured else ""
        return (f"'{model_key}' was removed in Mightling {cls.removed_in(model_key)}{where}: Mightling "
                f"serves one model, {DEFAULT_MODEL_ALIAS}.\n"
                f"   Switch to it: ling-admin main-model set {DEFAULT_MODEL_ALIAS}")

    @classmethod
    def declares_own_quantization(cls, model_key: str) -> bool:
        """
        Reports whether the checkpoint carries its quantization format in its own config.

        Callers use this to suppress an inferred `--quantization` flag: an NVFP4 or AWQ checkpoint
        already tells vLLM what it is, and overriding that with a guess derived from the model name
        misconfigures the load.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the model's precisions are all self-declaring.
        """
        spec = cls.get_spec(model_key) if model_key else None
        if not spec or not spec.supported_precisions:
            return False
        return all(p.upper() in SELF_DECLARING_PRECISIONS for p in spec.supported_precisions)
