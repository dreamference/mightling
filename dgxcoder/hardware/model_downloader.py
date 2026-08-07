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
    TENSORIZER_CACHE_HOME = Path.home() / ".cache" / "dgxcoder" / "tensorizer"

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
    def get_tensorizer_cache_dir(cls) -> Path:
        """
        Gets the DGXCoder tensorizer cache directory.

        Returns:
            Path: Tensorizer model weights directory path.
        """
        return cls.TENSORIZER_CACHE_HOME

    @classmethod
    def get_model_snapshot_dir(cls, model_key: str) -> Optional[Path]:
        """
        Gets the local HuggingFace snapshot directory for the given model key.

        Args:
            model_key (str): Model key or repo ID.

        Returns:
            Optional[Path]: Path to snapshot directory if it exists, else None.
        """
        if not model_key:
            return None
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if not repo_id:
            return None
        folder_name = "models--" + repo_id.replace("/", "--")
        cache_dir = cls.get_hf_cache_dir() / folder_name / "snapshots"
        if cache_dir.exists():
            snapshots = [d for d in cache_dir.iterdir() if d.is_dir()]
            if snapshots:
                return sorted(snapshots, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        return None

    @classmethod
    def get_tensorized_dir(cls, model_key: str) -> Optional[Path]:
        """
        Gets the tensorized model output directory.

        Args:
            model_key (str): Model key or repo ID.

        Returns:
            Optional[Path]: Output directory path.
        """
        if not model_key:
            return None
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if not repo_id:
            return None
        folder_name = repo_id.replace("/", "--")
        return cls.TENSORIZER_CACHE_HOME / folder_name

    @classmethod
    def get_tensorized_path(cls, model_key: str) -> Optional[Path]:
        """
        Gets path to the .tensors serialized file if present and non-empty.

        Args:
            model_key (str): Model key or repo ID.

        Returns:
            Optional[Path]: Path to model.tensors if present, else None.
        """
        tdir = cls.get_tensorized_dir(model_key)
        if not tdir:
            return None
        tensor_file = tdir / "model.tensors"
        if tensor_file.exists() and tensor_file.stat().st_size > 0:
            return tensor_file
        return None

    @classmethod
    def is_model_tensorized(cls, model_key: str) -> bool:
        """
        Checks if model snapshot has been serialized into tensorize (.tensors) format.

        Args:
            model_key (str): Model key or repo ID.

        Returns:
            bool: True if tensorized file exists.
        """
        return cls.get_tensorized_path(model_key) is not None

    @classmethod
    def tensorize_model(
        cls,
        model_key: str,
        force: bool = False,
        hf_token: Optional[str] = None
    ) -> bool:
        """
        Converts local HuggingFace model weights into tensorize (.tensors) format
        and saves them alongside model config files for fast GPU loading in vLLM.

        Args:
            model_key (str): Short alias or HuggingFace repo ID.
            force (bool): Force re-conversion even if already tensorized.
            hf_token (Optional[str]): HuggingFace access token.

        Returns:
            bool: True if tensorized model is present or successfully created.
        """
        if not model_key:
            return False

        if not force and cls.is_model_tensorized(model_key):
            tpath = cls.get_tensorized_path(model_key)
            print(f"✅ Model '{model_key}' is already saved in tensorize format ({tpath}).")
            return True

        # Ensure HF snapshot exists
        if not cls.is_model_downloaded(model_key):
            print(f"📥 Pre-downloading HF weights for '{model_key}' prior to tensorization...")
            if not cls.download_model(model_key, hf_token=hf_token, auto_tensorize=False):
                print(f"❌ Failed to download HuggingFace model weights for '{model_key}'.")
                return False

        snapshot_dir = cls.get_model_snapshot_dir(model_key)
        if not snapshot_dir:
            print(f"❌ Model snapshot directory not found for '{model_key}'.")
            return False

        tdir = cls.get_tensorized_dir(model_key)
        if not tdir:
            return False
        tdir.mkdir(parents=True, exist_ok=True)
        tensors_file = tdir / "model.tensors"

        print(f"⚡ Converting '{model_key}' to tensorize format and saving to {tensors_file}...")

        serialization_success = False

        # Attempt 1: Try containerized vllm-tensorizer if Docker is available
        if shutil.which("docker"):
            try:
                hf_cache = os.path.expanduser("~/.cache/huggingface")
                dgx_cache = os.path.expanduser("~/.cache/dgxcoder")
                hf_base = cls.get_hf_cache_dir()
                dgx_base = cls.get_dgx_cache_dir()
                
                if snapshot_dir.is_relative_to(hf_base) and tdir.is_relative_to(dgx_base):
                    snap_rel = snapshot_dir.relative_to(hf_base)
                    tdir_rel = tdir.relative_to(dgx_base)
                    container_snap = f"/root/.cache/huggingface/{snap_rel}"
                    container_tfile = f"/root/.cache/dgxcoder/{tdir_rel}/model.tensors"

                    from dgxcoder.vllm_server.vllm_server_manager import DEFAULT_VLLM_IMAGE
                    print(f"   Using {DEFAULT_VLLM_IMAGE} Docker container for model weight serialization...")
                    cmd = [
                        "docker", "run", "--rm", "--gpus", "all",
                        "-v", f"{hf_cache}:/root/.cache/huggingface",
                        "-v", f"{dgx_cache}:/root/.cache/dgxcoder",
                        "--entrypoint", "python3",
                        DEFAULT_VLLM_IMAGE,
                        "-c",
                        f"from tensorizer import TensorSerializer; from transformers import AutoModelForCausalLM; "
                        f"model = AutoModelForCausalLM.from_pretrained('{container_snap}', trust_remote_code=True, torch_dtype='auto'); "
                        f"serializer = TensorSerializer('{container_tfile}'); "
                        f"serializer.write_module(model); serializer.close()"
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode == 0 and tensors_file.exists():
                        serialization_success = True
            except Exception as e:
                pass

        # Attempt 2: Try local tensorizer library (TensorSerializer)
        if not serialization_success:
            try:
                from tensorizer import TensorSerializer
                from transformers import AutoModelForCausalLM
                from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
                print("   Using local TensorSerializer (tensorizer Python library)...")
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TimeElapsedColumn(),
                ) as progress:
                    task = progress.add_task("Tensorizing model weights", total=None)
                    model = AutoModelForCausalLM.from_pretrained(
                        str(snapshot_dir),
                        trust_remote_code=True,
                        low_cpu_mem_usage=True,
                        torch_dtype="auto"
                    )
                    progress.update(task, description="Serializing to .tensors format")
                    serializer = TensorSerializer(str(tensors_file))
                    serializer.write_module(model)
                    serializer.close()
                    del model
                    progress.update(task, description="Tensorization complete", completed=True)
                serialization_success = True
            except ImportError:
                pass
            except Exception as e:
                print(f"⚠️ TensorSerializer note: {e}")

        # Attempt 3: Try vLLM tensorize script module if available
        if not serialization_success:
            try:
                import sys
                cmd = [
                    sys.executable, "-m", "examples.tensorize_vllm_model",
                    "--model", str(snapshot_dir),
                    "serialize",
                    "--serialized-directory", str(tdir),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and tensors_file.exists():
                    serialization_success = True
            except Exception:
                pass

        if not serialization_success:
            print(f"⚠️  'tensorizer' not installed — skipping tensorize step (normal download will be used).")
            return True

        # Copy metadata and tokenizer config files alongside model.tensors
        for fpath in snapshot_dir.iterdir():
            if fpath.is_file() and not fpath.name.endswith((".safetensors", ".bin", ".h5", ".ot", ".pt", ".onnx")):
                shutil.copy2(fpath, tdir / fpath.name)

        size_mb = tensors_file.stat().st_size / (1024 * 1024)
        print(f"✅ Successfully saved '{model_key}' in tensorize format ({size_mb:.1f} MB -> {tensors_file})")
        return True

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
    def download_model(
        cls,
        model_key: str,
        hf_token: Optional[str] = None,
        auto_tensorize: bool = True
    ) -> bool:
        """
        Pre-downloads HuggingFace model weights into local cache and optionally converts to tensorize format.

        Args:
            model_key (str): Short alias or HuggingFace repo ID.
            hf_token (Optional[str]): Optional HuggingFace access token.
            auto_tensorize (bool): If True, convert to tensorize format after download.

        Returns:
            bool: True if weights are present or successfully downloaded.
        """
        if not model_key:
            raise ValueError("model_key cannot be None or empty. Please specify a model to download.")
        repo_id = ModelMatrixRegistry.resolve_hf_repo(model_key)
        if not repo_id:
            raise ValueError(f"Unable to resolve model '{model_key}' to a valid HuggingFace repository ID.")
        
        success = False

        # Check cache FIRST before any downloads
        if cls.is_model_downloaded(model_key):
            cache_size = cls._get_cache_size(model_key)
            print(f"✅ Model '{model_key}' ({repo_id}) found in local cache ({cache_size}).")
            success = True
        else:
            print(f"📥 Pre-downloading HuggingFace weights for '{model_key}' ({repo_id})...")
            token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

            # 1. Attempt python huggingface_hub snapshot_download
            try:
                from huggingface_hub import snapshot_download
                snapshot_download(repo_id=repo_id, token=token_val)
                cache_size = cls._get_cache_size(model_key)
                print(f"✅ Successfully pre-downloaded {repo_id} ({cache_size})")
                success = True
            except ImportError:
                pass
            except Exception as e:
                print(f"⚠️ HuggingFace snapshot_download note: {e}")

            # 2. Attempt CLI fallback
            if not success and shutil.which("huggingface-cli"):
                cmd = ["huggingface-cli", "download", repo_id]
                if token_val:
                    cmd.extend(["--token", token_val])
                res = subprocess.run(cmd)
                if res.returncode == 0:
                    cache_size = cls._get_cache_size(model_key)
                    print(f"✅ Successfully pre-downloaded {repo_id} via huggingface-cli ({cache_size})")
                    success = True

        if success and auto_tensorize:
            cls.tensorize_model(model_key, hf_token=hf_token)  # best-effort; continues even if tensorizer missing

        if not success:
            print(f"💡 vLLM will attempt to fetch '{repo_id}' during initialization (cache-first mode).")
        return success

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
    def download_all_models(
        cls,
        hf_token: Optional[str] = None,
        auto_tensorize: bool = True
    ) -> Dict[str, bool]:
        """
        Pre-downloads all qualified GB10 models in the matrix.

        Args:
            hf_token (Optional[str]): Optional HuggingFace access token.
            auto_tensorize (bool): If True, convert models to tensorize format after download.

        Returns:
            Dict[str, bool]: Dictionary mapping model keys to download success flags.
        """
        results: Dict[str, bool] = {}
        print("🚀 Pre-downloading all qualified NVIDIA GB10 LLM & Draft models...")
        for key, spec in ModelMatrixRegistry.MATRIX.items():
            if spec.compatible_gb10:
                results[key] = cls.download_model(key, hf_token=hf_token, auto_tensorize=auto_tensorize)
        return results

    @classmethod
    def clear_cache(cls) -> None:
        """
        Clears the HuggingFace and tensorizer model caches.
        """
        for cache_dir in [cls.get_hf_cache_dir().parent, cls.get_tensorizer_cache_dir().parent]:
            if cache_dir.exists():
                print(f"🗑️  Clearing cache: {cache_dir}")
                shutil.rmtree(cache_dir, ignore_errors=True)
            else:
                print(f"ℹ️  Cache directory not found: {cache_dir}")
        print("✅ Model cache cleared.")

    @classmethod
    def clear_tensorizer_cache(cls) -> None:
        """
        Clears only the tensorizer model cache.
        """
        cache_dir = cls.get_tensorizer_cache_dir().parent
        if cache_dir.exists():
            print(f"🗑️  Clearing tensorizer cache: {cache_dir}")
            shutil.rmtree(cache_dir, ignore_errors=True)
        else:
            print(f"ℹ️  Tensorizer cache directory not found: {cache_dir}")
        print("✅ Tensorizer cache cleared.")

