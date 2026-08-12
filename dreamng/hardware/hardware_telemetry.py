"""
Hardware Telemetry Data Structure for Dreamng.

This module defines the HardwareTelemetry dataclass which consolidates GPU identification,
unified memory totals, driver info, and system architecture details.
"""

from dataclasses import dataclass

@dataclass
class HardwareTelemetry:
    """
    Data model encapsulating hardware detection results and NVIDIA GB10 target qualification.

    Attributes:
        is_gb10 (bool): True if host system qualifies as NVIDIA GB10 (>=100GB unified memory or Blackwell GPU).
        gpu_name (str): Full model name of detected GPU via nvidia-smi (e.g., 'NVIDIA GB10 Blackwell').
        driver_version (str): Active NVIDIA graphics driver version string.
        total_unified_memory_gb (float): Total unified LPDDR5X memory size in GB.
        available_memory_gb (float): Currently available unreserved memory in GB.
        used_memory_gb (float): Currently used memory in GB.
        vram_gb (float): Dedicated GPU VRAM in GB reported by nvidia-smi.
        arch (str): Machine CPU architecture (e.g., 'aarch64', 'x86_64').
    """
    is_gb10: bool
    gpu_name: str
    driver_version: str
    total_unified_memory_gb: float
    available_memory_gb: float
    used_memory_gb: float
    vram_gb: float
    arch: str
