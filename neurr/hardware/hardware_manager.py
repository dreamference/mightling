"""
NVIDIA GB10 Hardware Manager & Compatibility Verification.

This module provides the HardwareManager class responsible for inspecting host physical
RAM via /proc/meminfo, detecting Blackwell GPU specs via nvidia-smi, and evaluating
model memory budgets.
"""

import os
import shutil
import subprocess
from typing import Tuple
from neurr.hardware.memory_metrics import MemoryMetrics
from neurr.hardware.hardware_telemetry import HardwareTelemetry
from neurr.hardware.model_matrix_registry import ModelMatrixRegistry

class HardwareManager:
    """
    Manager class for querying host system hardware specs and evaluating LLM memory compatibility.
    """

    @classmethod
    def get_system_memory(cls) -> MemoryMetrics:
        """
        Parses Linux /proc/meminfo to retrieve system RAM statistics in Gigabytes.

        Returns:
            MemoryMetrics: Dataclass containing total_gb, available_gb, and used_gb.
        """
        total_gb, avail_gb, used_gb = 0.0, 0.0, 0.0
        meminfo_path = "/proc/meminfo"
        if os.path.exists(meminfo_path):
            try:
                with open(meminfo_path, "r", encoding="utf-8") as f:
                    content = f.read()
                total_kb, avail_kb = 0, 0
                for line in content.splitlines():
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1])
                total_gb = round(total_kb / (1024 * 1024), 2)
                avail_gb = round(avail_kb / (1024 * 1024), 2)
                used_gb = round((total_kb - avail_kb) / (1024 * 1024), 2)
            except Exception:
                pass
        return MemoryMetrics(total_gb=total_gb, available_gb=avail_gb, used_gb=used_gb)

    @classmethod
    def detect_gb10_hardware(cls) -> HardwareTelemetry:
        """
        Detects GPU device info via `nvidia-smi` and checks system qualification for NVIDIA GB10 target specs.

        Returns:
            HardwareTelemetry: Dataclass containing GPU name, unified memory size, driver version, and architecture.
        """
        gpu_name = "N/A"
        is_gb10 = False
        driver_version = "N/A"
        vram_gb = 0.0

        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if res.returncode == 0 and res.stdout.strip():
                    lines = res.stdout.strip().splitlines()
                    first_line = lines[0].split(",")
                    gpu_name = first_line[0].strip()
                    if len(first_line) > 1:
                        driver_version = first_line[1].strip()
                    if len(first_line) > 2:
                        try:
                            vram_gb = round(float(first_line[2].strip()) / 1024.0, 2)
                        except ValueError:
                            pass
            except Exception:
                pass

        sys_mem = cls.get_system_memory()
        total_mem_gb = sys_mem.total_gb

        # Qualification logic: GB10 name check or Unified Memory >= 100GB
        if "GB10" in gpu_name.upper() or "BLACKWELL" in gpu_name.upper():
            is_gb10 = True
        elif total_mem_gb >= 100.0:
            is_gb10 = True
            if gpu_name == "N/A":
                gpu_name = "NVIDIA GB10 (Simulated / Unified Memory Node)"

        return HardwareTelemetry(
            is_gb10=is_gb10,
            gpu_name=gpu_name,
            driver_version=driver_version,
            total_unified_memory_gb=total_mem_gb,
            available_memory_gb=sys_mem.available_gb,
            used_memory_gb=sys_mem.used_gb,
            vram_gb=vram_gb,
            arch=os.uname().machine
        )

    @classmethod
    def check_model_compatibility(cls, model_key: str) -> Tuple[bool, str]:
        """
        Validates if requested model fits local hardware unified memory budget.

        Args:
            model_key (str): Short model alias or repo name.

        Returns:
            Tuple[bool, str]: Tuple of (is_compatible, status_message).
        """
        spec = ModelMatrixRegistry.get_spec(model_key)
        if not spec:
            return True, f"Unknown model '{model_key}'. Make sure host has enough memory."

        hw = cls.detect_gb10_hardware()
        avail_mem = hw.total_unified_memory_gb or 128.0

        if not spec.compatible_gb10:
            return False, f"Model '{spec.name}' exceeds GB10 128GB unified memory budget ({spec.min_memory_gb} GB required)."

        if avail_mem > 0 and spec.min_memory_gb > avail_mem * 0.95:
            return False, f"Model '{spec.name}' requires {spec.min_memory_gb} GB, but system only has {avail_mem} GB available."

        return True, f"✅ Compatible: {spec.name} ({spec.notes})"

    @classmethod
    def check_speculative_compatibility(cls, main_model_key: str, draft_model_key: str) -> Tuple[bool, str]:
        """
        Validates combined memory budget for dual-model speculative decoding.

        Args:
            main_model_key (str): Main target LLM model key.
            draft_model_key (str): Speculative decoding draft model key.

        Returns:
            Tuple[bool, str]: Tuple of (is_compatible, status_message).
        """
        valid_main, msg_main = cls.check_model_compatibility(main_model_key)
        if not valid_main:
            return False, f"Main model error: {msg_main}"

        valid_draft, msg_draft = cls.check_model_compatibility(draft_model_key)
        if not valid_draft:
            return False, f"Draft model error: {msg_draft}"

        main_spec = ModelMatrixRegistry.get_spec(main_model_key)
        draft_spec = ModelMatrixRegistry.get_spec(draft_model_key)

        if main_spec and draft_spec:
            total_req = main_spec.min_memory_gb + draft_spec.min_memory_gb
            hw = cls.detect_gb10_hardware()
            avail_mem = hw.total_unified_memory_gb or 128.0
            if total_req > avail_mem * 0.95:
                return False, f"Combined memory requirement ({total_req:.1f} GB) exceeds available unified memory ({avail_mem:.1f} GB)."
            return True, f"✅ Speculative Decoding Qualified: {main_spec.name} + {draft_spec.name} (~{total_req:.1f} GB total memory)"

        return True, f"✅ Speculative decoding enabled for {main_model_key} with draft model {draft_model_key}"
