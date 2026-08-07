"""
Model Matrix Registry for DGXCoder.

This module provides the ModelMatrixRegistry class which acts as the single source of truth
for supported LLMs, speculative decoding draft models, and short alias resolution.
"""

from typing import Dict, Optional, Final
from dgxcoder.hardware.model_spec import ModelSpec

class ModelMatrixRegistry:
    """
    Registry holding qualified models for NVIDIA GB10 hardware and short alias resolution logic.
    """

    # Static registry of supported target models and speculative draft models
    MATRIX: Final[Dict[str, ModelSpec]] = {
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
        Resolves short model alias (e.g. 'qwen2.5-coder-32b') to official HuggingFace repository ID.

        Args:
            model_key (str): Short model alias or full repo string.

        Returns:
            str: Official HuggingFace repository identifier (or original string if unmapped).
        """
        if not model_key:
            return model_key
        spec = cls.MATRIX.get(model_key.lower())
        return spec.hf_repo_id if spec else model_key

    @classmethod
    def get_spec(cls, model_key: str) -> Optional[ModelSpec]:
        """
        Retrieves ModelSpec for given short alias.

        Args:
            model_key (str): Short model name key.

        Returns:
            Optional[ModelSpec]: ModelSpec object or None if key is unrecognized.
        """
        return cls.MATRIX.get(model_key.lower())
