"""
Configuration Path Resolver for Dreamference.

This module provides the ConfigPathResolver class which enforces the resolution order for
locating `.dreamference/config.yaml` or custom configuration paths.
"""

import os
from pathlib import Path
from typing import Optional, Final

LOCAL_DREAMFERENCE_CONFIG_PATH: Final[Path] = Path(".dreamference") / "config.yaml"
GLOBAL_DREAMFERENCE_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "dreamference" / "config.yaml"

class ConfigPathResolver:
    """
    Resolver utility determining the active configuration file path according to 4-tier precedence:
    1. Explicit `--config <path>` CLI parameter
    2. `DREAMFERENCE_CONFIG_PATH` environment variable
    3. Project local `.dreamference/config.yaml` or `.dreamference/config.json`
    4. Global user home `~/.config/dreamference/config.yaml`
    """

    @classmethod
    def resolve_path(cls, custom_path: Optional[str] = None) -> Path:
        """
        Resolves the absolute path to the active Dreamference configuration file.

        Args:
            custom_path (Optional[str]): Explicit file path provided via CLI or API.

        Returns:
            Path: Resolved absolute Path object pointing to target config location.
        """
        if custom_path:
            return Path(custom_path).resolve()
        
        env_path = os.getenv("DREAMFERENCE_CONFIG_PATH")
        if env_path:
            return Path(env_path).resolve()

        if LOCAL_DREAMFERENCE_CONFIG_PATH.exists():
            return LOCAL_DREAMFERENCE_CONFIG_PATH.resolve()

        local_json = Path(".dreamference") / "config.json"
        if local_json.exists():
            return local_json.resolve()

        if GLOBAL_DREAMFERENCE_CONFIG_PATH.exists():
            return GLOBAL_DREAMFERENCE_CONFIG_PATH.resolve()

        return LOCAL_DREAMFERENCE_CONFIG_PATH.resolve()
