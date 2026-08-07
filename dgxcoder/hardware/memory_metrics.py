"""
System Memory Metrics Dataclass for DGXCoder.

This module provides the MemoryMetrics data structure used to report total,
available, and consumed system host memory parsed from /proc/meminfo.
"""

from dataclasses import dataclass

@dataclass
class MemoryMetrics:
    """
    Data model holding snapshot memory statistics for host RAM/Unified Memory.

    Attributes:
        total_gb (float): Total installed physical RAM in Gigabytes.
        available_gb (float): Immediately available unallocated RAM in Gigabytes.
        used_gb (float): Currently allocated/consumed RAM in Gigabytes.
    """
    total_gb: float
    available_gb: float
    used_gb: float
