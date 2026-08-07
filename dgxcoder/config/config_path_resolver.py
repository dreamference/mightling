import os
from pathlib import Path
from typing import Optional, Final

LOCAL_DGXCODER_CONFIG_PATH: Final[Path] = Path(".dgxcoder") / "config.yaml"
GLOBAL_DGXCODER_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "dgxcoder" / "config.yaml"

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
