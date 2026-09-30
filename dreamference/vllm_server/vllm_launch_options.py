"""
vLLM Launch Options Data Structure for Dreamference.

This module provides the VLLMLaunchOptions dataclass which encapsulates parameters for
launching vLLM OpenAI API server processes on NVIDIA GB10 hardware.
"""

from dataclasses import dataclass
from typing import Optional
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS

@dataclass
class VLLMLaunchOptions:
    """
    Data model defining options for starting a local vLLM OpenAI-compatible API server.

    Attributes:
        model (str): Primary model name or HuggingFace repo ID.
        port (int): Port number for HTTP OpenAI endpoint (default 8000).
        quantization (Optional[str]): Quantization method ('fp8', 'int8', 'awq', etc.).
        max_model_len (int): Maximum model context length (tokens).
        gpu_memory_utilization (float): Fraction of GPU memory allocated to vLLM KV cache (0.90 = 90%).
        draft_model (Optional[str]): Optional speculative decoding draft model.
        num_speculative_tokens (int): Number of draft tokens proposed per speculative step.
        hf_token (Optional[str]): HuggingFace API authorization token.
        enable_prefix_caching (bool): Enable automatic prompt prefix KV-cache reuse.
        enable_chunked_prefill (bool): Enable chunked prefill execution for low generation latency.
        num_scheduler_steps (int): Multi-step scheduling iteration count.
        attention_backend (str): Attention implementation backend ('FLASHINFER', 'FLASH_ATTN', 'auto').
        kv_cache_dtype (str): Precision datatype for KV cache ('auto', 'fp8').
    enable_auto_tool_choice (bool): Enable automatic tool choice for function calling.
    tool_call_parser (str): Tool-call parser name (e.g. 'hermes').
    max_num_batched_tokens (int): Max tokens per batch when chunked prefill is enabled (GB10 optimization).
    guided_decoding_backend (Optional[str]): Structured-outputs backend for deterministic JSON/tool-call
        output ('auto', 'xgrammar', 'guidance'). None leaves vLLM's own default in place.
    """
    model: str = DEFAULT_MODEL_ALIAS
    port: int = 8000
    quantization: Optional[str] = None
    max_model_len: int = 16384
    gpu_memory_utilization: float = 0.50
    draft_model: Optional[str] = None
    num_speculative_tokens: int = 8
    hf_token: Optional[str] = None
    enable_prefix_caching: bool = True
    enable_chunked_prefill: bool = True
    num_scheduler_steps: int = 8
    attention_backend: str = "auto"
    enable_auto_tool_choice: bool = True
    tool_call_parser: str = "hermes"
    max_num_batched_tokens: int = 8192
    guided_decoding_backend: Optional[str] = None

