"""
NVIDIA GB10 Hardware Manager & Compatibility Verification.

This module provides the HardwareManager class responsible for inspecting host physical
RAM via /proc/meminfo, detecting Blackwell GPU specs via nvidia-smi, and evaluating
model memory budgets.
"""

import os
import shutil
import subprocess
from typing import Dict, Final, Tuple
from dreamference.hardware.memory_metrics import MemoryMetrics
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

# The GB10's GPU as the PCI bus lists it: the same chip in every GB10 machine (NVIDIA's DGX Spark
# and the seven partner boxes), so it identifies one before a driver is installed, when there is
# no `nvidia-smi` to ask. Read on an ASUS Ascent GX10 (`000f:01:00.0`, class 0x030000).
GB10_PCI_VENDOR: Final[str] = "0x10de"
GB10_PCI_DEVICE: Final[str] = "0x2e12"
PCI_DEVICES_DIR: Final[str] = "/sys/bus/pci/devices"
DMI_DIR: Final[str] = "/sys/class/dmi/id"
# Present on DGX OS only; the partner boxes ship DGX OS too (HP and Lenovo also document plain
# Ubuntu 24.04), and on this ASUS it says `DGX_PLATFORM="GX10"` beside the DGX Spark's name.
DGX_RELEASE: Final[str] = "/etc/dgx-release"
OS_RELEASE: Final[str] = "/etc/os-release"

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

        # Qualification logic: GB10 name check, the GB10's PCI id (a box whose driver is not
        # installed yet has no nvidia-smi), or Unified Memory >= 100GB
        if "GB10" in gpu_name.upper() or "BLACKWELL" in gpu_name.upper():
            is_gb10 = True
        elif cls.gb10_on_pci():
            is_gb10 = True
            if gpu_name == "N/A":
                gpu_name = "NVIDIA GB10 (no driver answering: nvidia-smi is missing or failed)"
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
            arch=os.uname().machine,
            machine=cls.machine_name(),
            os_name=cls.os_name(),
        )

    @classmethod
    def gb10_on_pci(cls) -> bool:
        """
        Looks for the GB10's GPU on the PCI bus, without a driver.

        Returns:
            bool: True if a device with the GB10's vendor and device id is present.
        """
        try:
            entries = os.listdir(PCI_DEVICES_DIR)
        except OSError:
            return False
        for entry in entries:
            device = os.path.join(PCI_DEVICES_DIR, entry)
            if (cls._read_line(os.path.join(device, "vendor")) == GB10_PCI_VENDOR
                    and cls._read_line(os.path.join(device, "device")) == GB10_PCI_DEVICE):
                return True
        return False

    @classmethod
    def machine_name(cls) -> str:
        """
        The vendor's name for this machine, from the firmware's DMI tables.

        Every GB10 machine reports the same GPU, so this is the only reading that says whose box
        Puffin is running on (an ASUS says `ASUSTeK COMPUTER INC.` and `GX10`; NVIDIA's own is
        reported as `NVIDIA` and `NVIDIA_DGX_Spark`). It is shown, never matched on.

        Returns:
            str: Vendor and product joined, or whichever of the two exists; empty if neither.
        """
        vendor = cls._read_line(os.path.join(DMI_DIR, "sys_vendor"))
        product = cls._read_line(os.path.join(DMI_DIR, "product_name"))
        # "Dell Inc." and "Dell Pro Max ...": the product already names its maker.
        if product and vendor and product.lower().startswith(vendor.split()[0].lower()):
            return product
        return " ".join(part for part in (vendor, product) if part)

    @classmethod
    def os_name(cls) -> str:
        """
        The operating system, naming the DGX OS release where there is one.

        Returns:
            str: e.g. `DGX OS 7.5.0 (Ubuntu 24.04.4 LTS)` or `Ubuntu 24.04.4 LTS`; empty if
            neither file can be read.
        """
        ubuntu = cls._key_values(OS_RELEASE).get("PRETTY_NAME", "")
        dgx = cls._key_values(DGX_RELEASE)
        # The over-the-air version is the installed one; the build version is what the image
        # shipped with and stays put after updates.
        version = dgx.get("DGX_OTA_VERSION") or dgx.get("DGX_SWBUILD_VERSION")
        if version:
            return f"DGX OS {version} ({ubuntu})" if ubuntu else f"DGX OS {version}"
        return ubuntu

    @staticmethod
    def _read_line(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.readline().strip()
        except OSError:
            return ""

    @staticmethod
    def _key_values(path: str) -> Dict[str, str]:
        """Reads a shell-style `KEY="value"` file such as /etc/os-release; empty if unreadable."""
        values: Dict[str, str] = {}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    key, sep, value = line.strip().partition("=")
                    if sep and key and not key.startswith("#"):
                        values[key] = value.strip().strip('"').strip("'")
        except OSError:
            pass
        return values

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
