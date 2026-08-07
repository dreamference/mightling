import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from dgxcoder.hardware import MODEL_MATRIX, check_model_compatibility

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_VLLM_HOST = "http://localhost:8000"
DEFAULT_MODEL = "qwen2.5-coder-32b"
GOOSE_CONFIG_PATH = Path.home() / ".config" / "goose" / "config.yaml"

class DGXCoderConfig:
    """Manages GB10 hardware setup and Goose agent configurations."""

    def __init__(self, vllm_host: str = DEFAULT_VLLM_HOST, model: str = DEFAULT_MODEL):
        self.vllm_host = os.getenv("DGXCODER_VLLM_HOST", vllm_host)
        self.model = os.getenv("DGXCODER_MODEL", model)
        self.config_path = GOOSE_CONFIG_PATH

    def validate_model(self) -> tuple[bool, str]:
        """Validates selected model against GB10 hardware specs."""
        return check_model_compatibility(self.model)

    def get_env_vars(self) -> Dict[str, str]:
        """Returns environment variables required to run Goose against local GB10 vLLM endpoint."""
        return {
            "GOOSE_PROVIDER": "openai",
            "OPENAI_HOST": self.vllm_host,
            "OPENAI_BASE_PATH": "v1",
            "OPENAI_API_KEY": "gb10-local-token",
            "GOOSE_MODEL": self.model,
        }

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

        # Write YAML or JSON format
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
