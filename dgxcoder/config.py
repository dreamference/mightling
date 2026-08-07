import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Final
from dgxcoder.hardware import MODEL_MATRIX, check_model_compatibility

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"
DEFAULT_MODEL: Final[str] = "qwen2.5-coder-32b"
DEFAULT_SPECULATIVE_TOKENS: Final[int] = 5
DEFAULT_SANDBOX: Final[str] = "none"
DEFAULT_PREFIX_CACHING: Final[bool] = True
DEFAULT_CHUNKED_PREFILL: Final[bool] = True
DEFAULT_SCHEDULER_STEPS: Final[int] = 8
DEFAULT_ATTENTION_BACKEND: Final[str] = "auto"
DEFAULT_KV_CACHE_DTYPE: Final[str] = "auto"

GOOSE_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "goose" / "config.yaml"
GLOBAL_DGXCODER_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "dgxcoder" / "config.yaml"
LOCAL_DGXCODER_CONFIG_PATH: Final[Path] = Path(".dgxcoder") / "config.yaml"

class ConfigPathResolver:
    """Resolves active configuration file path based on precedence rules."""

    @classmethod
    def resolve_path(cls, custom_path: Optional[str] = None) -> Path:
        if custom_path:
            return Path(custom_path).resolve()
        
        env_path = os.getenv("DGXCODER_CONFIG_PATH")
        if env_path:
            return Path(env_path).resolve()

        if LOCAL_DGXCODER_CONFIG_PATH.exists():
            return LOCAL_DGXCODER_CONFIG_PATH.resolve()

        local_json = Path(".dgxcoder") / "config.json"
        if local_json.exists():
            return local_json.resolve()

        if GLOBAL_DGXCODER_CONFIG_PATH.exists():
            return GLOBAL_DGXCODER_CONFIG_PATH.resolve()

        return LOCAL_DGXCODER_CONFIG_PATH.resolve()

class ConfigFileStorageManager:
    """Handles serialization and deserialization of DGXCoder YAML and JSON configurations."""

    @classmethod
    def load_config_dict(cls, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                if yaml and path.suffix in (".yaml", ".yml"):
                    return yaml.safe_load(f) or {}
                else:
                    return json.load(f) or {}
        except Exception as e:
            print(f"⚠️ Error reading DGXCoder config file ({path}): {e}")
            return {}

    @classmethod
    def save_config_dict(cls, path: Path, data: Dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if yaml and path.suffix in (".yaml", ".yml"):
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False)
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        return path

class DGXCoderConfig:
    """Manages GB10 hardware setup, workspace config files, performance tuning, and Goose agent configurations."""

    def __init__(
        self,
        config_file: Optional[str] = None,
        vllm_host: Optional[str] = None,
        model: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: Optional[int] = None,
        sandbox: Optional[str] = None,
        hf_token: Optional[str] = None,
        enable_prefix_caching: Optional[bool] = None,
        enable_chunked_prefill: Optional[bool] = None,
        num_scheduler_steps: Optional[int] = None,
        attention_backend: Optional[str] = None,
        kv_cache_dtype: Optional[str] = None,
    ):
        self.config_file_path: Path = ConfigPathResolver.resolve_path(config_file)
        self.file_data: Dict[str, Any] = ConfigFileStorageManager.load_config_dict(self.config_file_path)

        # Merge Precedence: CLI Param > Environment Var > Config File > System Default
        self.vllm_host: str = (
            vllm_host
            if vllm_host is not None
            else os.getenv(
                "DGXCODER_VLLM_HOST",
                str(self.file_data.get("vllm_host", DEFAULT_VLLM_HOST))
            )
        )

        self.model: str = (
            model
            if model is not None
            else os.getenv(
                "DGXCODER_MODEL",
                str(self.file_data.get("model", DEFAULT_MODEL))
            )
        )

        self.draft_model: Optional[str] = (
            draft_model
            if draft_model is not None
            else os.getenv(
                "DGXCODER_DRAFT_MODEL",
                self.file_data.get("draft_model", None)
            )
        )

        env_spec_tokens = os.getenv("DGXCODER_SPECULATIVE_TOKENS")
        if num_speculative_tokens is not None:
            self.num_speculative_tokens: int = num_speculative_tokens
        elif env_spec_tokens is not None:
            self.num_speculative_tokens = int(env_spec_tokens)
        else:
            self.num_speculative_tokens = int(
                self.file_data.get("num_speculative_tokens", DEFAULT_SPECULATIVE_TOKENS)
            )

        self.sandbox: str = (
            sandbox
            if sandbox is not None
            else os.getenv(
                "DGXCODER_SANDBOX",
                str(self.file_data.get("sandbox", DEFAULT_SANDBOX))
            )
        ).lower()

        self.hf_token: Optional[str] = (
            hf_token
            if hf_token is not None
            else os.getenv(
                "HF_TOKEN",
                os.getenv(
                    "DGXCODER_HF_TOKEN",
                    self.file_data.get("hf_token", None)
                )
            )
        )

        self.enable_prefix_caching: bool = bool(
            enable_prefix_caching
            if enable_prefix_caching is not None
            else self.file_data.get("enable_prefix_caching", DEFAULT_PREFIX_CACHING)
        )

        self.enable_chunked_prefill: bool = bool(
            enable_chunked_prefill
            if enable_chunked_prefill is not None
            else self.file_data.get("enable_chunked_prefill", DEFAULT_CHUNKED_PREFILL)
        )

        self.num_scheduler_steps: int = int(
            num_scheduler_steps
            if num_scheduler_steps is not None
            else self.file_data.get("num_scheduler_steps", DEFAULT_SCHEDULER_STEPS)
        )

        self.attention_backend: str = str(
            attention_backend
            if attention_backend is not None
            else self.file_data.get("attention_backend", DEFAULT_ATTENTION_BACKEND)
        )

        self.kv_cache_dtype: str = str(
            kv_cache_dtype
            if kv_cache_dtype is not None
            else self.file_data.get("kv_cache_dtype", DEFAULT_KV_CACHE_DTYPE)
        )

        self.config_path: Path = GOOSE_CONFIG_PATH

    def save_config(self, target_path: Optional[Path] = None) -> Path:
        """Saves current configuration parameters to YAML or JSON config file."""
        out_path = target_path or self.config_file_path
        data: Dict[str, Any] = {
            "vllm_host": self.vllm_host,
            "model": self.model,
            "draft_model": self.draft_model,
            "num_speculative_tokens": self.num_speculative_tokens,
            "sandbox": self.sandbox,
            "hf_token": self.hf_token,
            "enable_prefix_caching": self.enable_prefix_caching,
            "enable_chunked_prefill": self.enable_chunked_prefill,
            "num_scheduler_steps": self.num_scheduler_steps,
            "attention_backend": self.attention_backend,
            "kv_cache_dtype": self.kv_cache_dtype,
        }
        return ConfigFileStorageManager.save_config_dict(out_path, data)

    def validate_model(self) -> Tuple[bool, str]:
        """Validates selected main and draft models against GB10 hardware specs."""
        if self.draft_model:
            from dgxcoder.hardware import check_speculative_compatibility
            return check_speculative_compatibility(self.model, self.draft_model)
        return check_model_compatibility(self.model)

    def get_env_vars(self) -> Dict[str, str]:
        """Returns environment variables required to run Goose against local GB10 vLLM endpoint."""
        env: Dict[str, str] = {
            "GOOSE_PROVIDER": "openai",
            "OPENAI_HOST": self.vllm_host,
            "OPENAI_BASE_PATH": "v1",
            "OPENAI_API_KEY": "gb10-local-token",
            "GOOSE_MODEL": self.model,
        }
        if self.hf_token:
            env["HF_TOKEN"] = self.hf_token
            env["HUGGING_FACE_HUB_TOKEN"] = self.hf_token
        return env

    def ensure_goose_config(self, extra_mcp_servers: Optional[Dict[str, Any]] = None) -> None:
        """Writes or updates ~/.config/goose/config.yaml to integrate local vLLM and MCP servers."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        config_data: Dict[str, Any] = {
            "provider": "openai",
            "openai": {
                "host": self.vllm_host,
                "base_path": "v1",
                "api_key": "gb10-local-token",
                "model": self.model
            },
            "extensions": {
                "jetbrains_mcp": {
                    "enabled": True,
                    "type": "stdio",
                    "cmd": "dgxcoder",
                    "args": ["mcp"]
                }
            }
        }
        
        if extra_mcp_servers:
            config_data["extensions"].update(extra_mcp_servers)

        if yaml:
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        existing = yaml.safe_load(f) or {}
                    if isinstance(existing, dict):
                        existing.update(config_data)
                        config_data = existing
                except Exception:
                    pass

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False)
        else:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
