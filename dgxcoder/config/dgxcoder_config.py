"""
Main DGXCoder Configuration Class.

This module provides the DGXCoderConfig class which merges CLI parameters, environment
variables, `.dgxcoder/config.yaml`, and system defaults into a unified settings object.
It also manages Goose AI Agent configuration files (~/.config/goose/config.yaml).
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Final

from dgxcoder.hardware import MODEL_MATRIX, check_model_compatibility
from dgxcoder.config.config_path_resolver import ConfigPathResolver
from dgxcoder.config.config_file_storage_manager import ConfigFileStorageManager, yaml

# System defaults
DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"
DEFAULT_MODEL: Final[str] = "qwen2.5-coder-32b"
DEFAULT_SPECULATIVE_TOKENS: Final[int] = 5
DEFAULT_SANDBOX: Final[str] = "none"
DEFAULT_AGENT_RUNNER: Final[str] = "goose"
DEFAULT_PREFIX_CACHING: Final[bool] = True
DEFAULT_CHUNKED_PREFILL: Final[bool] = True
DEFAULT_SCHEDULER_STEPS: Final[int] = 8
DEFAULT_ATTENTION_BACKEND: Final[str] = "auto"
DEFAULT_KV_CACHE_DTYPE: Final[str] = "auto"

GOOSE_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "goose" / "config.yaml"

class DGXCoderConfig:
    """
    Unified configuration manager implementing the 4-tier precedence hierarchy:
    CLI Parameters > Environment Variables > `.dgxcoder/config.yaml` > System Defaults.
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        vllm_host: Optional[str] = None,
        model: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: Optional[int] = None,
        sandbox: Optional[str] = None,
        agent_runner: Optional[str] = None,
        hf_token: Optional[str] = None,
        enable_prefix_caching: Optional[bool] = None,
        enable_chunked_prefill: Optional[bool] = None,
        num_scheduler_steps: Optional[int] = None,
        attention_backend: Optional[str] = None,
        kv_cache_dtype: Optional[str] = None,
    ):
        """
        Initializes DGXCoderConfig by loading file defaults and overriding with environment variables and parameters.
        """
        self.config_file_path: Path = ConfigPathResolver.resolve_path(config_file)
        self.file_data: Dict[str, Any] = ConfigFileStorageManager.load_config_dict(self.config_file_path)

        # 1. Target vLLM server endpoint host URL
        self.vllm_host: str = (
            vllm_host
            if vllm_host is not None
            else os.getenv(
                "DGXCODER_VLLM_HOST",
                str(self.file_data.get("vllm_host", DEFAULT_VLLM_HOST))
            )
        )

        # 2. Main target LLM model name served on vLLM
        self.model: str = (
            model
            if model is not None
            else os.getenv(
                "DGXCODER_MODEL",
                str(self.file_data.get("model", DEFAULT_MODEL))
            )
        )

        # 3. Optional speculative decoding draft model name
        self.draft_model: Optional[str] = (
            draft_model
            if draft_model is not None
            else os.getenv(
                "DGXCODER_DRAFT_MODEL",
                self.file_data.get("draft_model", None)
            )
        )

        # 4. Number of speculative draft tokens to propose per step
        env_spec_tokens = os.getenv("DGXCODER_SPECULATIVE_TOKENS")
        if num_speculative_tokens is not None:
            self.num_speculative_tokens: int = num_speculative_tokens
        elif env_spec_tokens is not None:
            self.num_speculative_tokens = int(env_spec_tokens)
        else:
            self.num_speculative_tokens = int(
                self.file_data.get("num_speculative_tokens", DEFAULT_SPECULATIVE_TOKENS)
            )

        # 5. Container sandbox engine ('none', 'apptainer', 'podman', 'docker')
        self.sandbox: str = (
            sandbox
            if sandbox is not None
            else os.getenv(
                "DGXCODER_SANDBOX",
                str(self.file_data.get("sandbox", DEFAULT_SANDBOX))
            )
        ).lower()

        # 6. Primary AI agent runner ('goose' [default] or 'cline')
        self.agent_runner: str = (
            agent_runner
            if agent_runner is not None
            else os.getenv(
                "DGXCODER_AGENT",
                os.getenv(
                    "DGXCODER_RUNNER",
                    str(self.file_data.get("agent_runner", DEFAULT_AGENT_RUNNER))
                )
            )
        ).lower()

        # 7. HuggingFace access token
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

        # 8. Blackwell GB10 Performance Tuning Flags
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

        # Path to official Goose config file
        self.config_path: Path = GOOSE_CONFIG_PATH

    def save_config(self, target_path: Optional[Path] = None) -> Path:
        """
        Saves current active configuration parameters to YAML or JSON config file.

        Args:
            target_path (Optional[Path]): Target output file path. Defaults to self.config_file_path.

        Returns:
            Path: Written config file path.
        """
        out_path = target_path or self.config_file_path
        data: Dict[str, Any] = {
            "vllm_host": self.vllm_host,
            "model": self.model,
            "draft_model": self.draft_model,
            "num_speculative_tokens": self.num_speculative_tokens,
            "sandbox": self.sandbox,
            "agent_runner": self.agent_runner,
            "hf_token": self.hf_token,
            "enable_prefix_caching": self.enable_prefix_caching,
            "enable_chunked_prefill": self.enable_chunked_prefill,
            "num_scheduler_steps": self.num_scheduler_steps,
            "attention_backend": self.attention_backend,
            "kv_cache_dtype": self.kv_cache_dtype,
        }
        return ConfigFileStorageManager.save_config_dict(out_path, data)

    def validate_model(self) -> Tuple[bool, str]:
        """
        Validates selected main and draft models against GB10 hardware memory specs.

        Returns:
            Tuple[bool, str]: Tuple of (is_valid, validation_message).
        """
        if self.draft_model:
            from dgxcoder.hardware import check_speculative_compatibility
            return check_speculative_compatibility(self.model, self.draft_model)
        return check_model_compatibility(self.model)

    def get_env_vars(self) -> Dict[str, str]:
        """
        Generates environment variables required for Goose agent processes.

        Returns:
            Dict[str, str]: Environment variables dictionary (GOOSE_PROVIDER, OPENAI_BASE_URL, HF_TOKEN, etc.).
        """
        from dgxcoder.hardware import resolve_model_hf_repo
        resolved_model = resolve_model_hf_repo(self.model)
        base_url = self.vllm_host.rstrip("/") + "/v1"
        env: Dict[str, str] = {
            "GOOSE_PROVIDER": "openai",
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": "gb10-local-token",
            "GOOSE_MODEL": resolved_model,
        }
        if self.hf_token:
            env["HF_TOKEN"] = self.hf_token
            env["HUGGING_FACE_HUB_TOKEN"] = self.hf_token
        return env

    def ensure_goose_config(self, extra_mcp_servers: Optional[Dict[str, Any]] = None) -> None:
        """
        Ensures ~/.config/goose/config.yaml is updated with local vLLM OpenAI endpoint settings
        and stdio MCP extension configuration.

        Args:
            extra_mcp_servers (Optional[Dict[str, Any]]): Optional additional MCP extensions to merge.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        from dgxcoder.hardware import resolve_model_hf_repo
        resolved_model = resolve_model_hf_repo(self.model)
        base_url = self.vllm_host.rstrip("/") + "/v1"
        config_data: Dict[str, Any] = {
            "provider": "openai",
            "openai": {
                "base_url": base_url,
                "api_key": "gb10-local-token",
                "model": resolved_model
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
