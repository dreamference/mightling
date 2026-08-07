from dataclasses import dataclass

@dataclass
class MemoryMetrics:
    total_gb: float
    available_gb: float
    used_gb: float
