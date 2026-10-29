"""
Configuration File Serialization & Storage Manager.

This module provides the ConfigFileStorageManager class handling loading and saving
Dreamference configuration dictionaries to YAML or JSON formats.
"""

import json
import os
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict

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
            return cls.parse_config_file(path)
        except Exception as e:
            print(f"⚠️ Error reading Mightling config file ({path}): {e}")
            return {}

    @staticmethod
    def _load_toml(text: str) -> dict[str, Any]:
        """Reads TOML text: with tomllib, and with the older `toml` package only for text it refuses.

        tomllib reads every valid TOML file, as the Rust readers do; the older `toml` package refuses
        some (a mixed-type array anywhere) and misreads others (an escaped quoted key, a string that
        starts with escaped quotes), so it is only the fallback.
        """
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            if toml is None:
                raise
            return toml.loads(text)

    @classmethod
    def parse_config_file(cls, path: Path) -> dict[str, Any]:
        """Parses a configuration file, raising when it cannot.

        TOML goes through `tomllib` (TOML 1.0, what the Rust launcher's parser and the other Python
        readers of the file accept), and a leading byte order mark is skipped: the `toml` package
        (TOML 0.5) refused both a mark and a mixed array, and the whole file then read as empty.

        Args:
            path (Path): File path to parse.

        Returns:
            Dict[str, Any]: The file's contents.
        """
        # `utf-8-sig`: an editor that saves a byte order mark (Notepad does) would otherwise make
        # TOML read the first key as "\ufeffkey", so that setting, often the air-gap level, was
        # silently ignored, and make JSON refuse the whole file.
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".toml":
            loaded = cls._load_toml(text)
        elif yaml and path.suffix in (".yaml", ".yml"):
            loaded = yaml.safe_load(text) or {}
        else:
            loaded = json.loads(text) or {}
        if not isinstance(loaded, dict):
            # A file that is only a list or a word has no settings; every caller reads a mapping.
            raise ValueError("not a table of settings; ignoring it")
        return loaded

    @classmethod
    def save_config_dict(cls, path: Path, data: Dict[str, Any], owned: Iterable[str] = ()) -> Path:
        """
        Saves configuration key-value dictionary to YAML, JSON, or TOML file on disk.

        Args:
            path (Path): Target file path.
            data (Dict[str, Any]): Dictionary of configuration options to serialize.
            owned (Iterable[str]): Keys the caller writes whenever they matter, so one missing
                from `data` is removed; every other key already in the file is kept.

        Returns:
            Path: Path object pointing to written file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # Tables in the file (`[night]`) and the keys only the Rust launcher reads
        # (`mightling_context_window`, …) belong to other readers, not to the flat keys being
        # saved: until 2026-10-01 `main-model set` and `init` silently removed Night Shift's
        # settings by rewriting the file from the flat keys alone, and until 2026-10-29 the
        # launcher's keys and any array of tables.
        if path.is_file():
            # A file that does not parse would read as {} and lose everything below.
            try:
                existing = cls.parse_config_file(path)
            except Exception as e:
                raise ValueError(f"{path} could not be read, so it was not overwritten: {e}") from e
            owned = set(owned)
            kept = {key: value for key, value in existing.items() if key not in data and key not in owned}
            data = {**data, **kept}
        # Written beside the file and renamed over it, as the Rust launcher's write_atomically
        # does: a reader never sees a truncated file, and a failed write leaves the old one. A
        # link (a dotfiles folder) is followed, so it stays a link, and the file keeps its mode.
        target = path.resolve()
        staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        mode = target.stat().st_mode & 0o7777 if target.is_file() else None
        try:
            with open(os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666), "w", encoding="utf-8") as f:
                if mode is not None:
                    os.fchmod(f.fileno(), mode)
                if toml and path.suffix == ".toml":
                    toml.dump(data, f)
                elif yaml and path.suffix in (".yaml", ".yml"):
                    yaml.dump(data, f, default_flow_style=False)
                else:
                    json.dump(data, f, indent=2)
            staging.replace(target)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        return path
