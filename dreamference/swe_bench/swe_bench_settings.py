"""
Where `ling-admin swe-bench` keeps its files, what it pins, and the `[swe_bench]` table of
`dreamference.toml` (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §4, §12).

The directories are module-level constants on purpose: the test suite re-points every such
attribute into a scratch home, and a path computed inside a function would escape that and let
a test write the user's real cache.
"""

import os
import platform
import tomllib
from pathlib import Path
from typing import Any, Dict, Final, Optional

from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.night_shift.night_shift_settings import NightShiftSettings

# Rebuildable: the harness, the dataset snapshot, the relocated `ling`, the validated list.
CACHE_DIR: Final[Path] = Path(os.path.expanduser("~/.cache/dreamference/swe-bench"))

# Results: one directory per run.
RESULTS_DIR: Final[Path] = Path(os.path.expanduser("~/.local/share/dreamference/swe-bench"))

# The upstream harness, installed in a virtualenv of its own. A smoke pass is tied to this
# version: upgrading it means proving the pipeline again.
HARNESS_VERSION: Final[str] = "5.0.2"

# Dataset names this command accepts, and the HuggingFace ids behind them. `lite` is not an
# alias of the harness's own command line, which is why the mapping lives here.
DATASETS: Final[Dict[str, str]] = {
    "verified": "SWE-bench/SWE-bench_Verified",
    "lite": "SWE-bench/SWE-bench_Lite",
    "full": "SWE-bench/SWE-bench",
}

# The only source of arm64 instance images: the task repository's own Dockerfiles are pinned to
# linux/amd64 and an x86-64 Miniconda, so a local build produces images this machine cannot run.
# Third-party and unaudited; every result names it.
COMMUNITY_IMAGE_REPO: Final[str] = "greynewell/swe-bench-arm64"

# The internal Docker network of the agent's containers: it reaches the model server at the
# network's gateway and nothing else.
NETWORK_NAME: Final[str] = "mightling-swe-bench"

# Five instances from five repositories, each validated on this machine on 2026-10-01 (the gold
# patch resolves it, a no-op patch does not). `smoke` grades all five both ways and runs the
# agent on the first.
SMOKE_INSTANCES: Final[tuple] = (
    "sympy__sympy-13480",
    "django__django-14089",
    "sphinx-doc__sphinx-9230",
    "pytest-dev__pytest-7982",
    "astropy__astropy-14309",
)

DEFAULT_MAX_PARALLEL: Final[int] = 3
DEFAULT_TASK_TIMEOUT: Final[str] = "45m"
DEFAULT_TASK_MEMORY: Final[str] = "8G"
DEFAULT_TASK_CPUS: Final[int] = 4
DEFAULT_NUDGES: Final[int] = 2
DEFAULT_IDLE_MINUTES: Final[int] = 10
DEFAULT_TASK_CONTEXT: Final[int] = 49_152
DEFAULT_EVAL_WORKERS: Final[int] = 4
# The cap put on each grading container (the harness sets none). The ten containers measured on
# 2026-10-01 peaked at 160 MiB; 4G leaves room for suites that were not measured, and four
# workers of it plus the 8 GiB reserve fit beside the resident model server (about 28 GiB free).
DEFAULT_EVAL_MEMORY: Final[str] = "4G"
DEFAULT_EVAL_TIMEOUT: Final[str] = "30m"
DEFAULT_DISK_RESERVE: Final[str] = "100G"


class SweBenchSettings:
    """Resolved `[swe_bench]` settings, defaults filled in."""

    def __init__(self, table: Optional[Dict[str, Any]] = None) -> None:
        """
        Args:
            table: The `[swe_bench]` table; None reads it from the config file.
        """
        if table is None:
            table = self.read_table()
        duration = NightShiftSettings.parse_duration
        self.max_parallel: int = max(1, int(table.get("max_parallel", DEFAULT_MAX_PARALLEL)))
        self.task_timeout_s: int = duration(table.get("task_timeout", DEFAULT_TASK_TIMEOUT))
        self.task_memory: str = str(table.get("task_memory", DEFAULT_TASK_MEMORY))
        self.task_cpus: int = max(1, int(table.get("task_cpus", DEFAULT_TASK_CPUS)))
        self.nudges: int = max(0, int(table.get("nudges", DEFAULT_NUDGES)))
        self.idle_minutes: float = float(table.get("idle_minutes", DEFAULT_IDLE_MINUTES))
        self.task_context: int = max(1, int(table.get("task_context", DEFAULT_TASK_CONTEXT)))
        self.eval_workers: int = max(1, int(table.get("eval_workers", DEFAULT_EVAL_WORKERS)))
        self.eval_memory: str = str(table.get("eval_memory", DEFAULT_EVAL_MEMORY))
        self.eval_timeout_s: int = duration(table.get("eval_timeout", DEFAULT_EVAL_TIMEOUT))
        self.disk_reserve: str = str(table.get("disk_reserve", DEFAULT_DISK_RESERVE))
        # Paired nodes serving the same model add lanes (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3).
        self.nodes: Any = table.get("nodes", "paired")

    @classmethod
    def read_table(cls, path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Reads `[swe_bench]` from a TOML file.

        Args:
            path: The file; None resolves the config file.

        Returns:
            Dict[str, Any]: The table, or an empty one when absent or unreadable.
        """
        path = path or ConfigPathResolver.resolve_path()
        try:
            with open(path, "rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        table = document.get("swe_bench", {})
        return table if isinstance(table, dict) else {}

    @classmethod
    def architecture(cls) -> str:
        """
        Returns:
            str: The machine's architecture as Docker names it (`arm64`, `amd64`).
        """
        machine = platform.machine().lower()
        return {"aarch64": "arm64", "x86_64": "amd64"}.get(machine, machine)

    @classmethod
    def dataset_id(cls, name: str) -> str:
        """
        Resolves a dataset name.

        Args:
            name: `verified`, `lite`, `full`, or a HuggingFace id.

        Returns:
            str: The HuggingFace dataset id.
        """
        return DATASETS.get(name.strip().lower(), name)
