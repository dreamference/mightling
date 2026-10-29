"""`ling-admin`'s start-up: what importing the CLI and building its parser may not load.

The launcher, the desktop app and Night Shift run `ling-admin` for quick commands (`--help`,
`node …`), and every run imports the whole package through the facades. The libraries below are
imported where they are used instead, which took about a third off a `--help` (2026-10-29).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each is imported inside the functions that need it; the comment there points here.
DEFERRED = ("requests", "urllib3", "rich", "yaml", "toml")

PROBE = """
import json, sys
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
DreamferenceCLIController.build_parser()
print(json.dumps(sorted({name.split(".")[0] for name in sys.modules})))
"""


def loaded_at_start() -> set[str]:
    """Top-level modules loaded by importing the CLI and building its parser, in a fresh process."""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    )
    return set(json.loads(result.stdout))


def test_the_cli_starts_without_the_deferred_libraries() -> None:
    """Importing `dreamference.cli` and building the parser loads none of them."""
    loaded = loaded_at_start()
    assert "dreamference" in loaded
    assert sorted(loaded & set(DEFERRED)) == []


def test_the_deferred_libraries_are_still_there_when_used(tmp_path: Path) -> None:
    """The code that needs them still gets them: TOML and YAML configs round-trip, Rich draws."""
    from dreamference.cli.dreamference_cli_controller import rich_console
    from dreamference.config.config_file_storage_manager import (
        ConfigFileStorageManager,
        optional_module,
    )

    assert optional_module("toml") is not None
    assert optional_module("yaml") is not None
    assert optional_module("not_a_module_here") is None
    assert rich_console() is rich_console()
    assert type(rich_console()).__module__ == "rich.console"
    for name in ("config.toml", "config.yaml"):
        path = tmp_path / name
        ConfigFileStorageManager.save_config_dict(path, {"model": "m", "night": {"window": "1-5"}})
        assert ConfigFileStorageManager.load_config_dict(path) == {
            "model": "m",
            "night": {"window": "1-5"},
        }
    assert "model = " in (tmp_path / "config.toml").read_text(encoding="utf-8")
