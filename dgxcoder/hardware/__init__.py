from typing import Dict, Any, Optional, Tuple

from dgxcoder.hardware.model_spec import ModelSpec
from dgxcoder.hardware.memory_metrics import MemoryMetrics
from dgxcoder.hardware.hardware_telemetry import HardwareTelemetry
from dgxcoder.hardware.model_matrix_registry import ModelMatrixRegistry
from dgxcoder.hardware.model_downloader import ModelDownloader
from dgxcoder.hardware.hardware_manager import HardwareManager

MODEL_MATRIX = ModelMatrixRegistry.MATRIX

def resolve_model_hf_repo(model_key: str) -> str:
    return ModelMatrixRegistry.resolve_hf_repo(model_key)

def is_model_downloaded(model_key: str) -> bool:
    return ModelDownloader.is_model_downloaded(model_key)

def download_model(model_key: str, hf_token: Optional[str] = None) -> bool:
    return ModelDownloader.download_model(model_key, hf_token=hf_token)

def download_all_models(hf_token: Optional[str] = None) -> Dict[str, bool]:
    return ModelDownloader.download_all_models(hf_token=hf_token)

def get_system_memory() -> Dict[str, float]:
    m = HardwareManager.get_system_memory()
    return {"total_gb": m.total_gb, "available_gb": m.available_gb, "used_gb": m.used_gb}

def detect_gb10_hardware() -> Dict[str, Any]:
    hw = HardwareManager.detect_gb10_hardware()
    return {
        "is_gb10": hw.is_gb10,
        "gpu_name": hw.gpu_name,
        "driver_version": hw.driver_version,
        "total_unified_memory_gb": hw.total_unified_memory_gb,
        "available_memory_gb": hw.available_memory_gb,
        "used_memory_gb": hw.used_memory_gb,
        "vram_gb": hw.vram_gb,
        "arch": hw.arch,
    }

def check_model_compatibility(model_key: str) -> Tuple[bool, str]:
    return HardwareManager.check_model_compatibility(model_key)

def check_speculative_compatibility(main_model_key: str, draft_model_key: str) -> Tuple[bool, str]:
    return HardwareManager.check_speculative_compatibility(main_model_key, draft_model_key)

__all__ = [
    "ModelSpec",
    "MemoryMetrics",
    "HardwareTelemetry",
    "ModelMatrixRegistry",
    "ModelDownloader",
    "HardwareManager",
    "MODEL_MATRIX",
    "resolve_model_hf_repo",
    "is_model_downloaded",
    "download_model",
    "download_all_models",
    "get_system_memory",
    "detect_gb10_hardware",
    "check_model_compatibility",
    "check_speculative_compatibility",
]
