"""
Configuration Path Resolver for Neurr.

This module provides the ConfigPathResolver class which enforces the resolution order for
locating `.neurr/config.yaml` or custom configuration paths.
"""

import os
from pathlib import Path
from typing import Optional, Final

LOCAL_NEURR_CONFIG_PATH: Final[Path] = Path(".neurr") / "config.yaml"
GLOBAL_NEURR_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "neurr" / "config.yaml"

class ConfigPathResolver:
    """
    Resolver utility determining the active configuration file path according to 4-tier precedence:
    1. Explicit `--config <path>` CLI parameter
    2. `NEURR_CONFIG_PATH` environment variable
    3. Project local `.neurr/config.yaml` or `.neurr/config.json`
    4. Global user home `~/.config/neurr/config.yaml`
    """

    @classmethod
    def resolve_path(cls, custom_path: Optional[str] = None) -> Path:
        """
        Resolves the absolute path to the active Neurr configuration file.

        Args:
            custom_path (Optional[str]): Explicit file path provided via CLI or API.

        Returns:
            Path: Resolved absolute Path object pointing to target config location.
        """
        if custom_path:
            return Path(custom_path).resolve()
        
        env_path = os.getenv("NEURR_CONFIG_PATH")
        if env_path:
            return Path(env_path).resolve()

        if LOCAL_NEURR_CONFIG_PATH.exists():
            return LOCAL_NEURR_CONFIG_PATH.resolve()

        local_json = Path(".neurr") / "config.json"
        if local_json.exists():
            return local_json.resolve()

        if GLOBAL_NEURR_CONFIG_PATH.exists():
            return GLOBAL_NEURR_CONFIG_PATH.resolve()

        return LOCAL_NEURR_CONFIG_PATH.resolve()
