"""
Model Specification Data Structure for Neurr.

This module defines the ModelSpec dataclass, which encapsulates metadata, memory
requirements, quantization support, and HuggingFace repository mapping for LLM models
qualified to run on NVIDIA GB10 hardware.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class ModelSpec:
    """
    Data model representing a Large Language Model's hardware requirements and attributes.

    Attributes:
        name (str): Human-readable display name of the model (e.g., 'Qwen 2.5 Coder 32B').
        params_b (float): Total parameter count in billions (e.g., 32.0).
        supported_precisions (List[str]): Supported floating-point or quantized formats (e.g., ['BF16', 'FP8']).
        min_memory_gb (float): Minimum estimated VRAM/RAM required in GB for single batch inference.
        max_memory_gb (float): Maximum estimated VRAM/RAM required in GB for high concurrency/context length.
        compatible_gb10 (bool): Flag indicating if model fits within NVIDIA GB10 128GB unified memory.
        notes (str): Operational or performance guidance notes for deployment.
        hf_repo_id (str): Official HuggingFace repository identifier (e.g., 'Qwen/Qwen2.5-Coder-32B-Instruct').
        launch_overrides (Dict[str, Any]): Per-model vLLM launch recipe. Not every model runs well on the
            same flags: NVFP4 checkpoints on GB10 (SM121) need a specific GEMM/MoE backend pair and their
            own tool-call parser, and picking the wrong one yields silent numerical corruption rather than
            an error. Keys mirror `build_launch_command` parameter names, plus 'env' (a Dict[str, str] of
            environment variables exported into the vLLM container). Explicit caller arguments always win
            over these values; they only fill in what the caller left unset.
    """
    name: str
    params_b: float
    supported_precisions: List[str]
    min_memory_gb: float
    max_memory_gb: float
    compatible_gb10: bool
    notes: str
    hf_repo_id: str
    launch_overrides: Dict[str, Any] = field(default_factory=dict)
