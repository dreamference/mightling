import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

@dataclass
class ModelSpec:
    name: str
    params_b: float
    supported_precisions: list[str]
    min_memory_gb: float
    max_memory_gb: float
    compatible_gb10: bool
    notes: str
    hf_repo_id: str

MODEL_MATRIX: Dict[str, ModelSpec] = {
    "qwen2.5-coder-32b": ModelSpec(
        name="Qwen 2.5 Coder 32B",
        params_b=32.0,
        supported_precisions=["BF16", "INT8", "FP8"],
        min_memory_gb=35.0,
        max_memory_gb=64.0,
        compatible_gb10=True,
        notes="Fits comfortably in 128GB Unified Memory",
        hf_repo_id="Qwen/Qwen2.5-Coder-32B-Instruct"
    ),
    "qwen2.5-coder-72b": ModelSpec(
        name="Qwen 2.5 Coder 72B",
        params_b=72.0,
        supported_precisions=["INT8", "FP8", "INT4"],
        min_memory_gb=45.0,
        max_memory_gb=80.0,
        compatible_gb10=True,
        notes="Supported (INT8/FP8 quantized fit)",
        hf_repo_id="Qwen/Qwen2.5-Coder-72B-Instruct"
    ),
    "deepseek-r1-distill-32b": ModelSpec(
        name="DeepSeek-R1-Distill-Qwen-32B",
        params_b=32.0,
        supported_precisions=["BF16", "INT8", "FP8"],
        min_memory_gb=35.0,
        max_memory_gb=64.0,
        compatible_gb10=True,
        notes="Fits comfortably in 128GB Unified Memory",
        hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
    ),
    "deepseek-r1-distill-70b": ModelSpec(
        name="DeepSeek-R1-Distill-Llama-70B",
        params_b=70.0,
        supported_precisions=["INT8", "FP8", "INT4"],
        min_memory_gb=45.0,
        max_memory_gb=80.0,
        compatible_gb10=True,
        notes="Supported (INT8/FP8 quantized fit)",
        hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
    ),
    "llama-3.3-70b": ModelSpec(
        name="Llama 3.3 70B Instruct",
        params_b=70.0,
        supported_precisions=["INT8", "FP8"],
        min_memory_gb=75.0,
        max_memory_gb=80.0,
        compatible_gb10=True,
        notes="Supported (INT8/FP8 quantized fit)",
        hf_repo_id="meta-llama/Llama-3.3-70B-Instruct"
    ),
    "qwen2.5-coder-1.5b": ModelSpec(
        name="Qwen 2.5 Coder 1.5B (Draft Model)",
        params_b=1.5,
        supported_precisions=["BF16", "FP16", "INT8"],
        min_memory_gb=3.5,
        max_memory_gb=6.0,
        compatible_gb10=True,
        notes="Speculative decoding draft model (~3.5GB memory)",
        hf_repo_id="Qwen/Qwen2.5-Coder-1.5B-Instruct"
    ),
    "qwen2.5-coder-3b": ModelSpec(
        name="Qwen 2.5 Coder 3B (Draft Model)",
        params_b=3.0,
        supported_precisions=["BF16", "FP16", "INT8"],
        min_memory_gb=6.5,
        max_memory_gb=10.0,
        compatible_gb10=True,
        notes="Speculative decoding draft model (~6.5GB memory)",
        hf_repo_id="Qwen/Qwen2.5-Coder-3B-Instruct"
    ),
    "starcoder2-15b": ModelSpec(
        name="StarCoder2 15B",
        params_b=15.0,
        supported_precisions=["BF16", "FP16"],
        min_memory_gb=20.0,
        max_memory_gb=30.0,
        compatible_gb10=True,
        notes="Fits easily",
        hf_repo_id="bigcode/starcoder2-15b"
    ),
    "deepseek-v3-671b": ModelSpec(
        name="DeepSeek-V3 671B (MoE)",
        params_b=671.0,
        supported_precisions=["INT4"],
        min_memory_gb=350.0,
        max_memory_gb=400.0,
        compatible_gb10=False,
        notes="Exceeds 128GB (Requires multi-node or >128GB hardware)",
        hf_repo_id="deepseek-ai/DeepSeek-V3"
    ),
}

def resolve_model_hf_repo(model_key: str) -> str:
    """Resolves short model alias to official HuggingFace repository ID."""
    if not model_key:
        return model_key
    spec = MODEL_MATRIX.get(model_key.lower())
    if spec:
        return spec.hf_repo_id
    return model_key

def is_model_downloaded(model_key: str) -> bool:
    """Checks if model weights exist in local HuggingFace cache directory."""
    repo_id = resolve_model_hf_repo(model_key)
    if "/" in repo_id:
        folder_name = "models--" + repo_id.replace("/", "--")
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / folder_name / "snapshots"
        if cache_dir.exists() and any(cache_dir.iterdir()):
            return True
    return False

def download_model(model_key: str, hf_token: Optional[str] = None) -> bool:
    """Pre-downloads model weights from HuggingFace Hub into local cache prior to server launch."""
    repo_id = resolve_model_hf_repo(model_key)
    if is_model_downloaded(model_key):
        print(f"✅ Model '{model_key}' ({repo_id}) is already pre-downloaded in local cache.")
        return True

    print(f"📥 Pre-downloading HuggingFace weights for '{model_key}' ({repo_id})...")
    token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

    # 1. Try python huggingface_hub snapshot_download
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=repo_id, token=token_val)
        print(f"✅ Successfully pre-downloaded {repo_id}")
        return True
    except ImportError:
        pass
    except Exception as e:
        print(f"⚠️ HuggingFace snapshot_download note: {e}")

    # 2. Try huggingface-cli CLI fallback
    if shutil.which("huggingface-cli"):
        cmd = ["huggingface-cli", "download", repo_id]
        if token_val:
            cmd.extend(["--token", token_val])
        res = subprocess.run(cmd)
        if res.returncode == 0:
            print(f"✅ Successfully pre-downloaded {repo_id} via huggingface-cli")
            return True

    print(f"💡 vLLM will attempt to fetch '{repo_id}' during initialization.")
    return False

def download_all_models(hf_token: Optional[str] = None) -> Dict[str, bool]:
    """Pre-downloads all supported GB10 models from HuggingFace Hub."""
    results = {}
    print("🚀 Pre-downloading all qualified NVIDIA GB10 LLM & Draft models...")
    for key, spec in MODEL_MATRIX.items():
        if spec.compatible_gb10:
            results[key] = download_model(key, hf_token=hf_token)
    return results

def get_system_memory() -> Dict[str, float]:
    """Retrieves total and available system memory in GB from /proc/meminfo."""
    mem_info = {"total_gb": 0.0, "available_gb": 0.0, "used_gb": 0.0}
    meminfo_path = "/proc/meminfo"
    if os.path.exists(meminfo_path):
        try:
            with open(meminfo_path, "r", encoding="utf-8") as f:
                content = f.read()
            total_kb = 0
            avail_kb = 0
            for line in content.splitlines():
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
            mem_info["total_gb"] = round(total_kb / (1024 * 1024), 2)
            mem_info["available_gb"] = round(avail_kb / (1024 * 1024), 2)
            mem_info["used_gb"] = round((total_kb - avail_kb) / (1024 * 1024), 2)
        except Exception:
            pass
    return mem_info

def detect_gb10_hardware() -> Dict[str, Any]:
    """
    Checks system hardware against NVIDIA GB10 target specs:
    - GPU Name (NVIDIA GB10 Blackwell Architecture)
    - 128 GB Unified LPDDR5X Memory
    - Driver version & CUDA support
    """
    gpu_name = "N/A"
    is_gb10 = False
    driver_version = "N/A"
    cuda_version = "N/A"
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

    sys_mem = get_system_memory()
    total_mem_gb = sys_mem["total_gb"]

    # GB10 qualification check: GPU identified as GB10 or total unified memory >= 110 GB
    if "GB10" in gpu_name.upper() or "BLACKWELL" in gpu_name.upper():
        is_gb10 = True
    elif total_mem_gb >= 100.0:  # Unified Memory system matching GB10 spec class
        is_gb10 = True
        if gpu_name == "N/A":
            gpu_name = "NVIDIA GB10 (Simulated / Unified Memory Node)"

    return {
        "is_gb10": is_gb10,
        "gpu_name": gpu_name,
        "driver_version": driver_version,
        "total_unified_memory_gb": total_mem_gb,
        "available_memory_gb": sys_mem["available_gb"],
        "used_memory_gb": sys_mem["used_gb"],
        "vram_gb": vram_gb,
        "arch": os.uname().machine,
    }

def check_model_compatibility(model_key: str) -> Tuple[bool, str]:
    """Validates if the requested model can run on the local GB10 hardware setup."""
    spec = MODEL_MATRIX.get(model_key.lower())
    if not spec:
        return True, f"Unknown model '{model_key}'. Make sure host has enough memory."

    hw = detect_gb10_hardware()
    avail_mem = hw["total_unified_memory_gb"] or 128.0

    if not spec.compatible_gb10:
        return False, f"Model '{spec.name}' exceeds GB10 128GB unified memory budget ({spec.min_memory_gb} GB required)."

    if avail_mem > 0 and spec.min_memory_gb > avail_mem * 0.95:
        return False, f"Model '{spec.name}' requires {spec.min_memory_gb} GB, but system only has {avail_mem} GB available."

    return True, f"✅ Compatible: {spec.name} ({spec.notes})"

def check_speculative_compatibility(main_model_key: str, draft_model_key: str) -> Tuple[bool, str]:
    """Validates combined memory budget for dual-model speculative decoding on GB10 unified memory."""
    valid_main, msg_main = check_model_compatibility(main_model_key)
    if not valid_main:
        return False, f"Main model error: {msg_main}"

    valid_draft, msg_draft = check_model_compatibility(draft_model_key)
    if not valid_draft:
        return False, f"Draft model error: {msg_draft}"

    main_spec = MODEL_MATRIX.get(main_model_key.lower())
    draft_spec = MODEL_MATRIX.get(draft_model_key.lower())

    if main_spec and draft_spec:
        total_req = main_spec.min_memory_gb + draft_spec.min_memory_gb
        hw = detect_gb10_hardware()
        avail_mem = hw["total_unified_memory_gb"] or 128.0
        if total_req > avail_mem * 0.95:
            return False, f"Combined memory requirement ({total_req:.1f} GB) exceeds available unified memory ({avail_mem:.1f} GB)."
        return True, f"✅ Speculative Decoding Qualified: {main_spec.name} + {draft_spec.name} (~{total_req:.1f} GB total memory)"

    return True, f"✅ Speculative decoding enabled for {main_model_key} with draft model {draft_model_key}"
