"""
The `[night]` table of `dreamference.toml` (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §7).

It is read directly through the same file resolution as every other setting
(`DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then the global file), because
`DreamferenceConfig` holds flat keys and a table would not round-trip through `save_config()`.
"""

import re
import tomllib
from datetime import time
from pathlib import Path
from typing import Any, Dict, Final, Optional, Tuple

from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.night_shift.refine_prompt import VERSIONS as REFINE_VERSIONS

DEFAULT_WINDOW: Final[str] = "01:00-07:00"
DEFAULT_MAX_PARALLEL: Final[int] = 3
DEFAULT_TASK_TIMEOUT: Final[str] = "90m"
DEFAULT_TEST_TIMEOUT: Final[str] = "20m"
DEFAULT_TASK_MEMORY: Final[str] = "8G"
DEFAULT_NUDGES: Final[int] = 2
DEFAULT_IDLE_MINUTES: Final[int] = 10
DEFAULT_INDEX_TIMEOUT: Final[str] = "20m"

# The smallest KV budget a concurrent task may be given; the run splits the pool evenly between as
# many tasks as can each get at least this much, and each task's session compacts at its share
# (`NightShiftHost.task_budget`). The spec's first formula divided the pool by the full context
# length, which on the default model gives zero. 49,152 was the first per-task figure; enforced as
# a compaction limit it cost the task (compaction spec §11: at 49,152 the one run did not finish its
# own tests in an hour, where the same task without a limit passed in 14-18 minutes and peaked at
# 49,241 and 79,909 tokens of context). 65,536 gives two tasks of 70,608 on today's 156,907-token
# pool, so the 80K run would compact about once. Raised on 2026-10-02.
DEFAULT_TASK_CONTEXT: Final[int] = 65_536


class NightShiftSettings:
    """Resolved Night Shift settings, defaults filled in."""

    def __init__(self, table: Optional[Dict[str, Any]] = None) -> None:
        """
        Args:
            table: The `[night]` table; None reads it from the config file.
        """
        if table is None:
            table = self.read_table()
        self.window: str = str(table.get("window", DEFAULT_WINDOW))
        self.max_parallel: int = max(1, int(table.get("max_parallel", DEFAULT_MAX_PARALLEL)))
        self.task_timeout_s: int = self.parse_duration(table.get("task_timeout", DEFAULT_TASK_TIMEOUT))
        self.test_timeout_s: int = self.parse_duration(table.get("test_timeout", DEFAULT_TEST_TIMEOUT))
        self.task_memory: str = str(table.get("task_memory", DEFAULT_TASK_MEMORY))
        self.nudges: int = max(0, int(table.get("nudges", DEFAULT_NUDGES)))
        self.test: Optional[str] = table.get("test")
        # The runner's test run executes agent-written code; `false` runs it with the user's rights
        # (for a test command that must reach Docker or write outside the worktree).
        self.test_sandbox: bool = bool(table.get("test_sandbox", True))
        # A stricter `/airgapped` level for night runs alone (airgapped spec §7), as written; the
        # runner takes the stricter of this and the configured level, so a looser one is ignored.
        self.airgapped: Any = table.get("airgapped")
        # The system prompt each task's `ling exec` starts with (prompt spec §7), passed as
        # DREAMFERENCE_MIGHTLING_PROMPT; absent, the configured one. A task resumed the next night keeps
        # the prompt it started with whatever this says, because a session keeps its prompt.
        prompt = table.get("prompt")
        self.prompt: Optional[str] = prompt.strip() if isinstance(prompt, str) and prompt.strip() else None
        # Refine mode for night tasks (specs/DREAMFERENCE_MIGHTLING_REFINE.md §5.3): `true` or `false`
        # here, absent for the configured `mightling_refine` (`refine_enabled`).
        refine = table.get("refine")
        self.refine: Optional[bool] = refine if isinstance(refine, bool) else None
        # Which texts it uses (spec §10): "v1" or "v2" here, absent (or not a version) for the
        # configured `mightling_refine_version` (`refine_version_in_force`).
        version = table.get("refine_version")
        version = version.strip().lower() if isinstance(version, str) else None
        self.refine_version: Optional[str] = version if version in REFINE_VERSIONS else None
        self.task_context: int = max(1, int(table.get("task_context", DEFAULT_TASK_CONTEXT)))
        # Where a task's session compacts, passed to every `ling exec` of the task (compaction
        # spec §4.1). Absent (None, the default): the task's share of the KV pool, so the tasks of
        # a night fit in the pool together. A number: that limit, and only as many tasks at once as
        # fit at it. 0: no limit, the launcher's own (60% of the pool) applies and the run's
        # parallelism is no longer backed by anything (the pre-2026-10-02 behaviour).
        compact_at = table.get("compact_at")
        self.compact_at: Optional[int] = None if compact_at is None else max(0, int(compact_at))
        self.idle_minutes: float = float(table.get("idle_minutes", DEFAULT_IDLE_MINUTES))
        # Refresh each repository's code index before its tasks start (code-index spec §6.3).
        self.index: bool = bool(table.get("index", True))
        self.index_timeout_s: int = self.parse_duration(table.get("index_timeout", DEFAULT_INDEX_TIMEOUT))
        # Other nodes' model servers a run may also use (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3):
        # "paired" (every paired node serving the same model), "none", or a list of names.
        self.nodes: Any = table.get("nodes", "paired")

    def refine_enabled(self) -> bool:
        """
        Whether night tasks are refined first: `[night] refine`, then `mightling_refine` through its
        own tiers (`DREAMFERENCE_MIGHTLING_REFINE`, the config file, the default).

        Returns:
            bool: True when each new task gets a study step before the one that does it.
        """
        if self.refine is not None:
            return self.refine
        from dreamference.config.dreamference_config import DreamferenceConfig
        return DreamferenceConfig().mightling_refine

    def refine_version_in_force(self) -> str:
        """
        Which refine texts night tasks get: `[night] refine_version`, then `mightling_refine_version`
        through its own tiers (`DREAMFERENCE_MIGHTLING_REFINE_VERSION`, the config file, the default).

        Returns:
            str: "v1" or "v2".
        """
        if self.refine_version is not None:
            return self.refine_version
        from dreamference.config.dreamference_config import DreamferenceConfig
        return DreamferenceConfig().mightling_refine_version

    @classmethod
    def read_table(cls, path: Optional[Path] = None, section: str = "night") -> Dict[str, Any]:
        """
        Reads `[night]` (or another table) from a TOML file.

        Args:
            path: The file; None resolves the config file.
            section: The table to read.

        Returns:
            Dict[str, Any]: The table, or an empty one when absent or unreadable.
        """
        path = path or ConfigPathResolver.resolve_path()
        try:
            with open(path, "rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        table = document.get(section, {})
        return table if isinstance(table, dict) else {}

    @classmethod
    def parse_duration(cls, value: Any) -> int:
        """
        Parses `90m`, `2h`, `45s` or a bare number of minutes.

        Args:
            value: The setting.

        Returns:
            int: Seconds.
        """
        if isinstance(value, (int, float)):
            return int(value * 60)
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(value))
        if not match:
            raise ValueError(f"not a duration: {value!r} (use e.g. 90m, 2h, 45s)")
        amount, unit = float(match.group(1)), match.group(2) or "m"
        return int(amount * {"s": 1, "m": 60, "h": 3600}[unit])

    @classmethod
    def parse_window(cls, window: str) -> Tuple[time, time]:
        """
        Parses `HH:MM-HH:MM`.

        Args:
            window: The window; its end may be earlier than its start (it then ends the next day).

        Returns:
            Tuple[time, time]: Start and end.
        """
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", window)
        if not match:
            raise ValueError(f"not a window: {window!r} (use HH:MM-HH:MM)")
        h1, m1, h2, m2 = (int(group) for group in match.groups())
        return time(h1, m1), time(h2, m2)
