"""
Configuration Path Resolver for Dreamference.

This module provides the ConfigPathResolver class which enforces the resolution order for
locating `dreamference.toml`, `~/.config/dreamference/config.toml` or a custom configuration path.
"""

import os
from pathlib import Path
from typing import Optional, Final

LOCAL_DREAMFERENCE_CONFIG_PATH: Final[Path] = Path("dreamference.toml")
GLOBAL_DREAMFERENCE_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "dreamference" / "config.toml"

class ConfigPathResolver:
    """
    Resolver utility determining the active configuration file path according to 4-tier precedence:
    1. Explicit `--config <path>` CLI parameter
    2. `DREAMFERENCE_CONFIG_PATH` environment variable
    3. Project local `dreamference.toml` or `dreamference.json`
    4. Global user home `~/.config/dreamference/config.toml`
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

        local_json = Path("dreamference.json")
        if local_json.exists():
            return local_json.resolve()

        if GLOBAL_DREAMFERENCE_CONFIG_PATH.exists():
            return GLOBAL_DREAMFERENCE_CONFIG_PATH.resolve()

        return LOCAL_DREAMFERENCE_CONFIG_PATH.resolve()

    @classmethod
    def user_path(cls) -> Path:
        """The user's own configuration file, `~/.config/dreamference/config.toml`."""
        return GLOBAL_DREAMFERENCE_CONFIG_PATH

    @classmethod
    def is_repository_file(cls, path: Path, custom_path: str | None = None) -> bool:
        """Whether `resolve_path` chose the working folder's own file.

        That is a repository's, which a clone brings and the agent can write, rather than one the
        user named or the user's own.

        Args:
            path (Path): The path `resolve_path(custom_path)` returned.
            custom_path (str | None): The explicit path given to it, if any.

        Returns:
            bool: True for `./dreamference.toml` or `./dreamference.json`.
        """
        if custom_path or os.getenv("DREAMFERENCE_CONFIG_PATH"):
            return False
        return path.resolve() != GLOBAL_DREAMFERENCE_CONFIG_PATH.resolve()
