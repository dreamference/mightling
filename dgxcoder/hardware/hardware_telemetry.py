from dataclasses import dataclass

@dataclass
class HardwareTelemetry:
    is_gb10: bool
    gpu_name: str
    driver_version: str
    total_unified_memory_gb: float
    available_memory_gb: float
    used_memory_gb: float
    vram_gb: float
    arch: str
