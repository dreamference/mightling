"""
HuggingFace Model Pre-downloader for DGXCoder.

This module handles pre-flight downloads of model weights from HuggingFace Hub into local cache
prior to launching vLLM or Goose sessions.
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
    """

    @classmethod
    def is_model_downloaded(cls, model_key: str) -> bool:
        """
        Checks if model snapshot files already exist in local HuggingFace cache (~/.cache/huggingface/hub/).

        Args:
            model_key (str): Short alias or HuggingFace repo ID.

        Returns:
            bool: True if snapshot directory exists and is non-empty.
        """
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if "/" in repo_id:
            folder_name = "models--" + repo_id.replace("/", "--")
            cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / folder_name / "snapshots"
            if cache_dir.exists() and any(cache_dir.iterdir()):
                return True
        return False

    @classmethod
    def download_model(cls, model_key: str, hf_token: Optional[str] = None) -> bool:
        """
        Pre-downloads HuggingFace model weights into local cache before vLLM initialization.

        First attempts downloading via python `huggingface_hub.snapshot_download`.
        If missing, falls back to `huggingface-cli download`.

        Args:
            model_key (str): Short alias or HuggingFace repo ID.
            hf_token (Optional[str]): Optional HuggingFace access token.

        Returns:
            bool: True if weights are present or successfully downloaded.
        """
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if cls.is_model_downloaded(model_key):
            print(f"✅ Model '{model_key}' ({repo_id}) is already pre-downloaded in local cache.")
            return True

        print(f"📥 Pre-downloading HuggingFace weights for '{model_key}' ({repo_id})...")
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

        # 1. Attempt python huggingface_hub snapshot_download
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo_id, token=token_val)
            print(f"✅ Successfully pre-downloaded {repo_id}")
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
                print(f"✅ Successfully pre-downloaded {repo_id} via huggingface-cli")
                return True

        print(f"💡 vLLM will attempt to fetch '{repo_id}' during initialization.")
        return False

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
