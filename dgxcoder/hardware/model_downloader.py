import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional
from dgxcoder.hardware.model_matrix_registry import ModelMatrixRegistry

class ModelDownloader:
    """Handles HuggingFace Hub pre-downloads into local cache."""

    @classmethod
    def is_model_downloaded(cls, model_key: str) -> bool:
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if "/" in repo_id:
            folder_name = "models--" + repo_id.replace("/", "--")
            cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / folder_name / "snapshots"
            if cache_dir.exists() and any(cache_dir.iterdir()):
                return True
        return False

    @classmethod
    def download_model(cls, model_key: str, hf_token: Optional[str] = None) -> bool:
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if cls.is_model_downloaded(model_key):
            print(f"✅ Model '{model_key}' ({repo_id}) is already pre-downloaded in local cache.")
            return True

        print(f"📥 Pre-downloading HuggingFace weights for '{model_key}' ({repo_id})...")
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo_id, token=token_val)
            print(f"✅ Successfully pre-downloaded {repo_id}")
            return True
        except ImportError:
            pass
        except Exception as e:
            print(f"⚠️ HuggingFace snapshot_download note: {e}")

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
        results: Dict[str, bool] = {}
        print("🚀 Pre-downloading all qualified NVIDIA GB10 LLM & Draft models...")
        for key, spec in ModelMatrixRegistry.MATRIX.items():
            if spec.compatible_gb10:
                results[key] = cls.download_model(key, hf_token=hf_token)
        return results
