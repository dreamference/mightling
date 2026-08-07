from dataclasses import dataclass
from typing import Optional

@dataclass
class VLLMLaunchOptions:
    model: str = "qwen2.5-coder-32b"
    port: int = 8000
    quantization: Optional[str] = None
    max_model_len: int = 16384
    gpu_memory_utilization: float = 0.90
    draft_model: Optional[str] = None
    num_speculative_tokens: int = 5
    hf_token: Optional[str] = None
    enable_prefix_caching: bool = True
    enable_chunked_prefill: bool = True
    num_scheduler_steps: int = 8
    attention_backend: str = "auto"
    kv_cache_dtype: str = "auto"
