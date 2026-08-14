"""
Model Matrix Registry for Dreamference.

This module provides the ModelMatrixRegistry class which acts as the single source of truth
for supported LLMs, speculative decoding draft models, and short alias resolution.
"""

from typing import Any, Dict, Optional, Final
from dreamference.hardware.model_spec import ModelSpec

# Quantization formats that a checkpoint declares in its own config.json. vLLM auto-detects these,
# and passing an explicit --quantization alongside them fights that detection.
SELF_DECLARING_PRECISIONS: Final[frozenset] = frozenset({"NVFP4", "MXFP4", "AWQ", "GPTQ"})

# Single source of truth for the model Dreamference serves when nothing else is specified. Imported by
# the config layer and the vLLM launcher so the two cannot drift apart.
DEFAULT_MODEL_ALIAS: Final[str] = "qwen3.5-122b-a10b-nvfp4"

class ModelMatrixRegistry:
    """
    Registry holding qualified models for NVIDIA GB10 hardware and short alias resolution logic.
    """

    # Static registry of supported target models and speculative draft models
    MATRIX: Final[Dict[str, ModelSpec]] = {
        "qwen3.5-122b-a10b-nvfp4": ModelSpec(
            name="Qwen 3.5 122B-A10B (NVFP4)",
            params_b=122.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=78.0,
            max_memory_gb=120.0,
            compatible_gb10=True,
            notes=(
                "Default. At 78 GB this checkpoint is 64% of a GB10's unified memory, and the weights "
                "the driver pins are unreclaimable, so overshooting does not earn an OOM kill — the "
                "host livelocks in reclaim until the power button. That froze this machine six times "
                "on 2026-08-14 at every gpu_memory_utilization from 0.9 down to 0.3 — but not "
                "because the fraction is irrelevant. It has two bounds and those runs violated one "
                "or the other: below ~0.64 the arena is smaller than the weights, so the load "
                "overruns a budget it was never given, and above ~0.76 the arena plus the load's "
                "transient peak exceeds what is actually free. Both are now checked before launch "
                "rather than discovered by reset. The cgroup cap is the backstop, not the primary "
                "bound, and only became one once it was sized against the arena — at total-minus- "
                "reserve it sat above anything the container could reach and never fired. "
                "Tensorizer is deliberately not enabled here — NVFP4 checkpoints opt "
                "out of it, and it would not help regardless, since it does no O_DIRECT or fadvise "
                "and so never bypasses the page cache."
            ),
            hf_repo_id="nvidia/Qwen3.5-122B-A10B-NVFP4",
            launch_overrides={
                # 32k rather than the checkpoint's full 131072: the KV reservation is claimed during
                # the load, which is the only phase that has ever taken this machine down, and the
                # shorter context buys back roughly 15-20 GB of headroom exactly when it is scarcest.
                # Raise it once a load completes cleanly and the steady-state footprint is known.
                "max_model_len": 32768,
                # Must exceed 0.64 to hold the 77.8 GB of weights at all. The ceiling is not the
                # 12 GB host reserve, though — it is what is actually free at launch, minus the
                # peak the load passes through on its way to steady state (see
                # LOAD_TRANSIENT_FRACTION). 0.80 satisfied every static check and still froze the
                # machine on 2026-08-14: 97.3 GB of arena against 104.5 GB available left 7.2 GB
                # to absorb the page cache for a 77.8 GB checkpoint, and it did not.
                #
                # 0.72 puts the arena at 87.6 GB — 9.8 GB above the weights for KV and
                # activations, ample at 32k context with max-num-seqs 4 — and clears the gate by
                # ~5 GB with a browser and an IDE open. That margin is the point: the gate's own
                # answer here is 0.76, which passes by 0.4 GB and would fail again the moment
                # anything on the desktop grows. Erring low costs KV cache and fails loudly if
                # overdone; erring high costs the power button. Raise it only when loading
                # headless, where ~17 GB of desktop comes back.
                "gpu_memory_utilization": 0.72,
                "kv_cache_dtype": "fp8",
                "attention_backend": "flashinfer",
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                # Self-speculation off the MTP head shipped in this checkpoint. 785 mtp.* tensors
                # sit in the safetensors index and were being loaded and ignored, because
                # speculative_config was unset.
                #
                # Decode here is hard memory-bandwidth-bound: measured 11.0-11.2 tok/s on every
                # one of seven inspect prompts regardless of length, and aggregate throughput
                # doubled to 22 with a second concurrent stream — batching is nearly free, so the
                # per-token cost is dominated by reading weights, not by compute. That is exactly
                # the regime speculation pays in, since verifying k proposed tokens costs about
                # one weight read rather than k.
                #
                # num_speculative_tokens 1, not the 3 used by the qwen3.6-35b recipe below: this
                # checkpoint declares mtp_num_hidden_layers=1, so there is a single head and one
                # proposal per step is what it can actually predict. Asking for 3 drives the head
                # autoregressively and acceptance collapses.
                #
                # Lossless by construction — the full model verifies every proposal and keeps it
                # only if it matches what it would have produced, so a bad head costs speed, not
                # quality.
                "speculative_config": {"method": "mtp", "num_speculative_tokens": 1},
                "extra_args": [
                    "--max-num-seqs", "4",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                ],
            },
        ),
        "qwen3.6-35b-a3b-nvfp4": ModelSpec(
            name="Qwen 3.6 35B-A3B (NVFP4)",
            params_b=35.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=25.0,
            max_memory_gb=60.0,
            compatible_gb10=True,
            notes=(
                "Default. MoE with ~3B active params — decode speed tracks active params, not total, "
                "which is what the GB10's memory bandwidth rewards. Requires the FlashInfer b12x NVFP4 "
                "path (vLLM >= the May 2026 SM12x backends); the CUTLASS FP4 path is compiled for SM120 "
                "and silently emits garbage on SM121. Launch values follow NVIDIA's DGX Spark recipe."
            ),
            hf_repo_id="nvidia/Qwen3.6-35B-A3B-NVFP4",
            launch_overrides={
                "max_model_len": 131072,
                "gpu_memory_utilization": 0.3,
                "kv_cache_dtype": "fp8",
                "attention_backend": "flashinfer",
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                "max_num_batched_tokens": 8192,
                # MTP ships inside this checkpoint. Without it NVFP4 lands at the low end of the
                # published throughput range, so it is part of the recipe rather than a tuning extra.
                "speculative_config": {"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"},
                "extra_args": [
                    "--max-num-seqs", "4",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                ],
                "env": {
                    "VLLM_MARLIN_USE_ATOMIC_ADD": "1",
                },
            },
        ),
        "qwen2.5-coder-32b": ModelSpec(
            name="Qwen 2.5 Coder 32B",
            params_b=32.0,
            supported_precisions=["BF16", "INT8", "FP8"],
            min_memory_gb=35.0,
            max_memory_gb=64.0,
            compatible_gb10=True,
            notes="Fits comfortably in 128GB Unified Memory",
            hf_repo_id="Qwen/Qwen2.5-Coder-32B-Instruct"
        ),
        "qwen2.5-coder-72b": ModelSpec(
            name="Qwen 2.5 Coder 72B",
            params_b=72.0,
            supported_precisions=["INT8", "FP8", "INT4"],
            min_memory_gb=45.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="Qwen/Qwen2.5-Coder-72B-Instruct"
        ),
        "deepseek-r1-distill-32b": ModelSpec(
            name="DeepSeek-R1-Distill-Qwen-32B",
            params_b=32.0,
            supported_precisions=["BF16", "INT8", "FP8"],
            min_memory_gb=35.0,
            max_memory_gb=64.0,
            compatible_gb10=True,
            notes="Fits comfortably in 128GB Unified Memory",
            hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
        ),
        "deepseek-r1-distill-70b": ModelSpec(
            name="DeepSeek-R1-Distill-Llama-70B",
            params_b=70.0,
            supported_precisions=["INT8", "FP8", "INT4"],
            min_memory_gb=45.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
        ),
        "llama-3.3-70b": ModelSpec(
            name="Llama 3.3 70B Instruct",
            params_b=70.0,
            supported_precisions=["INT8", "FP8"],
            min_memory_gb=75.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="meta-llama/Llama-3.3-70B-Instruct"
        ),
        "qwen2.5-coder-1.5b": ModelSpec(
            name="Qwen 2.5 Coder 1.5B (Draft Model)",
            params_b=1.5,
            supported_precisions=["BF16", "FP16", "INT8"],
            min_memory_gb=3.5,
            max_memory_gb=6.0,
            compatible_gb10=True,
            notes="Speculative decoding draft model (~3.5GB memory)",
            hf_repo_id="Qwen/Qwen2.5-Coder-1.5B-Instruct"
        ),
        "qwen2.5-coder-3b": ModelSpec(
            name="Qwen 2.5 Coder 3B (Draft Model)",
            params_b=3.0,
            supported_precisions=["BF16", "FP16", "INT8"],
            min_memory_gb=6.5,
            max_memory_gb=10.0,
            compatible_gb10=True,
            notes="Speculative decoding draft model (~6.5GB memory)",
            hf_repo_id="Qwen/Qwen2.5-Coder-3B-Instruct"
        ),
        "starcoder2-15b": ModelSpec(
            name="StarCoder2 15B",
            params_b=15.0,
            supported_precisions=["BF16", "FP16"],
            min_memory_gb=20.0,
            max_memory_gb=30.0,
            compatible_gb10=True,
            notes="Fits easily",
            hf_repo_id="bigcode/starcoder2-15b"
        ),
        "deepseek-v3-671b": ModelSpec(
            name="DeepSeek-V3 671B (MoE)",
            params_b=671.0,
            supported_precisions=["INT4"],
            min_memory_gb=350.0,
            max_memory_gb=400.0,
            compatible_gb10=False,
            notes="Exceeds 128GB (Requires multi-node or >128GB hardware)",
            hf_repo_id="deepseek-ai/DeepSeek-V3"
        ),
    }

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
