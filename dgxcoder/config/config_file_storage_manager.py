import json
from pathlib import Path
from typing import Dict, Any

try:
    import yaml
except ImportError:
    yaml = None

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
