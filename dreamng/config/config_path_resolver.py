"""
Configuration Path Resolver for Dreamng.

This module provides the ConfigPathResolver class which enforces the resolution order for
locating `.dreamng/config.yaml` or custom configuration paths.
"""

import os
from pathlib import Path
from typing import Optional, Final

LOCAL_DREAMNG_CONFIG_PATH: Final[Path] = Path(".dreamng") / "config.yaml"
GLOBAL_DREAMNG_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "dreamng" / "config.yaml"

class ConfigPathResolver:
    """
    Resolver utility determining the active configuration file path according to 4-tier precedence:
    1. Explicit `--config <path>` CLI parameter
    2. `DREAMNG_CONFIG_PATH` environment variable
    3. Project local `.dreamng/config.yaml` or `.dreamng/config.json`
    4. Global user home `~/.config/dreamng/config.yaml`
    """

    @classmethod
    def resolve_path(cls, custom_path: Optional[str] = None) -> Path:
        """
        Resolves the absolute path to the active Dreamng configuration file.

        Args:
            custom_path (Optional[str]): Explicit file path provided via CLI or API.

        Returns:
            Path: Resolved absolute Path object pointing to target config location.
        """
        if custom_path:
            return Path(custom_path).resolve()
        
        env_path = os.getenv("DREAMNG_CONFIG_PATH")
        if env_path:
            return Path(env_path).resolve()

        if LOCAL_DREAMNG_CONFIG_PATH.exists():
            return LOCAL_DREAMNG_CONFIG_PATH.resolve()

        local_json = Path(".dreamng") / "config.json"
        if local_json.exists():
            return local_json.resolve()

        if GLOBAL_DREAMNG_CONFIG_PATH.exists():
            return GLOBAL_DREAMNG_CONFIG_PATH.resolve()

        return LOCAL_DREAMNG_CONFIG_PATH.resolve()
