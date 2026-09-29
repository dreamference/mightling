"""
Configuration File Serialization & Storage Manager.

This module provides the ConfigFileStorageManager class handling loading and saving
Dreamference configuration dictionaries to YAML or JSON formats.
"""

import json
from pathlib import Path
from typing import Dict, Any

try:
    import yaml
except ImportError:
    yaml = None

try:
    import toml
except ImportError:
    toml = None

class ConfigFileStorageManager:
    """
    Storage manager handling YAML/JSON/TOML parsing, serialization, and disk writing.
    """

    @classmethod
    def load_config_dict(cls, path: Path) -> Dict[str, Any]:
        """
        Loads configuration key-value dictionary from YAML, JSON, or TOML file.

        Args:
            path (Path): File path to load.

        Returns:
            Dict[str, Any]: Configuration dictionary loaded from file (or empty dict on error/missing).
        """
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                if toml and path.suffix == ".toml":
                    return toml.load(f)
                elif yaml and path.suffix in (".yaml", ".yml"):
                    return yaml.safe_load(f) or {}
                else:
                    return json.load(f) or {}
        except Exception as e:
            print(f"⚠️ Error reading Puffin config file ({path}): {e}")
            return {}

    @classmethod
    def save_config_dict(cls, path: Path, data: Dict[str, Any]) -> Path:
        """
        Saves configuration key-value dictionary to YAML, JSON, or TOML file on disk.

        Args:
            path (Path): Target file path.
            data (Dict[str, Any]): Dictionary of configuration options to serialize.

        Returns:
            Path: Path object pointing to written file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if toml and path.suffix == ".toml":
            with open(path, "w", encoding="utf-8") as f:
                toml.dump(data, f)
        elif yaml and path.suffix in (".yaml", ".yml"):
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False)
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        return path
