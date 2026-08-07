"""
HuggingFace Model Pre-downloader for DGXCoder.

This module handles pre-flight downloads of model weights from HuggingFace Hub into local cache
prior to launching vLLM or Goose sessions. It prioritizes local cache to minimize HF API calls.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional
from dgxcoder.hardware.model_matrix_registry import ModelMatrixRegistry

class ModelDownloader:
    """
    Utility class managing pre-downloading and local cache verification for LLM weights.
    Prioritizes local cache to minimize HuggingFace Hub API calls.
    """

    # Standard HuggingFace cache location
    HF_CACHE_HOME = Path.home() / ".cache" / "huggingface" / "hub"

    @classmethod
    def get_hf_cache_dir(cls) -> Path:
        """
        Gets the HuggingFace cache directory, respecting HF_HOME environment variable.

        Returns:
            Path: HuggingFace hub cache directory path.
        """
        hf_home = os.getenv("HF_HOME")
        if hf_home:
            return Path(hf_home) / "hub"
        return cls.HF_CACHE_HOME

    @classmethod
    def is_model_downloaded(cls, model_key: str) -> bool:
        """
        Checks if model snapshot files already exist in local HuggingFace cache.

        Args:
            model_key (str): Short alias or HuggingFace repo ID.

        Returns:
            bool: True if snapshot directory exists and is non-empty.
        """
        if not model_key:
            return False
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if not repo_id or "/" not in repo_id:
            return False
        folder_name = "models--" + repo_id.replace("/", "--")
        cache_dir = cls.get_hf_cache_dir() / folder_name / "snapshots"
        if cache_dir.exists() and any(cache_dir.iterdir()):
            return True
        return False

    @classmethod
    def download_model(cls, model_key: str, hf_token: Optional[str] = None) -> bool:
        """
        Pre-downloads HuggingFace model weights into local cache before vLLM initialization.

        First checks if model is already in cache. If cached, returns immediately.
        Otherwise attempts downloading via python `huggingface_hub.snapshot_download`.
        Falls back to `huggingface-cli download` if needed.

        Args:
            model_key (str): Short alias or HuggingFace repo ID.
            hf_token (Optional[str]): Optional HuggingFace access token.

        Returns:
            bool: True if weights are present or successfully downloaded.
        """
        if not model_key:
            raise ValueError("model_key cannot be None or empty. Please specify a model to download.")
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if not repo_id:
            raise ValueError(f"Unable to resolve model '{model_key}' to a valid HuggingFace repository ID.")
        
        # Check cache FIRST before any downloads
        if cls.is_model_downloaded(model_key):
            cache_size = cls._get_cache_size(model_key)
            print(f"✅ Model '{model_key}' ({repo_id}) found in local cache ({cache_size}).")
            return True

        print(f"📥 Pre-downloading HuggingFace weights for '{model_key}' ({repo_id})...")
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

        # 1. Attempt python huggingface_hub snapshot_download
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo_id, token=token_val)
            cache_size = cls._get_cache_size(model_key)
            print(f"✅ Successfully pre-downloaded {repo_id} ({cache_size})")
            return True
        except ImportError:
            pass
        except Exception as e:
            print(f"⚠️ HuggingFace snapshot_download note: {e}")

        # 2. Attempt CLI fallback
        if shutil.which("huggingface-cli"):
            cmd = ["huggingface-cli", "download", repo_id]
            if token_val:
                cmd.extend(["--token", token_val])
            res = subprocess.run(cmd)
            if res.returncode == 0:
                cache_size = cls._get_cache_size(model_key)
                print(f"✅ Successfully pre-downloaded {repo_id} via huggingface-cli ({cache_size})")
                return True

        print(f"💡 vLLM will attempt to fetch '{repo_id}' during initialization (cache-first mode).")
        return False

    @classmethod
    def _get_cache_size(cls, model_key: str) -> str:
        """
        Gets human-readable size of cached model.

        Args:
            model_key (str): Model key to check.

        Returns:
            str: Human-readable size (e.g., "12.5 GB").
        """
        try:
            repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
            if not repo_id:
                return "unknown size"
            folder_name = "models--" + repo_id.replace("/", "--")
            cache_dir = cls.get_hf_cache_dir() / folder_name
            if not cache_dir.exists():
                return "0 B"
            
            total_size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
            for unit in ["B", "KB", "MB", "GB"]:
                if total_size < 1024:
                    return f"{total_size:.1f} {unit}"
                total_size /= 1024
            return f"{total_size:.1f} TB"
        except Exception:
            return "unknown size"

    @classmethod
    def download_all_models(cls, hf_token: Optional[str] = None) -> Dict[str, bool]:
        """
        Pre-downloads all qualified GB10 models in the matrix.

        Args:
            hf_token (Optional[str]): Optional HuggingFace access token.

        Returns:
            Dict[str, bool]: Dictionary mapping model keys to download success flags.
        """
        results: Dict[str, bool] = {}
        print("🚀 Pre-downloading all qualified NVIDIA GB10 LLM & Draft models...")
        for key, spec in ModelMatrixRegistry.MATRIX.items():
            if spec.compatible_gb10:
                results[key] = cls.download_model(key, hf_token=hf_token)
        return results
