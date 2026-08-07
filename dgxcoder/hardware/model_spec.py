from dataclasses import dataclass
from typing import List

@dataclass
class ModelSpec:
    name: str
    params_b: float
    supported_precisions: List[str]
    min_memory_gb: float
    max_memory_gb: float
    compatible_gb10: bool
    notes: str
    hf_repo_id: str
