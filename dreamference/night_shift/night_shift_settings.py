"""
The `[night]` table of `dreamference.toml` (specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §7).

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

DEFAULT_WINDOW: Final[str] = "01:00-07:00"
DEFAULT_MAX_PARALLEL: Final[int] = 3
DEFAULT_TASK_TIMEOUT: Final[str] = "90m"
DEFAULT_TEST_TIMEOUT: Final[str] = "20m"
DEFAULT_TASK_MEMORY: Final[str] = "8G"
DEFAULT_NUDGES: Final[int] = 2
DEFAULT_IDLE_MINUTES: Final[int] = 10
DEFAULT_INDEX_TIMEOUT: Final[str] = "20m"

# Tokens of KV cache budgeted per concurrent task. The spec's first formula divided the KV pool by
# the full context length, which on the default model (SGLang, 144,870 pool tokens, 262,144-token
# context) gives zero: one full context does not even fit. A Codex task's context grows with its
# turns but rarely nears the window, and SGLang shares the common prompt prefix between streams,
# so tasks are budgeted at this size instead (144,870 / 49,152 = 2 here). Measured on 2026-10-01.
DEFAULT_TASK_CONTEXT: Final[int] = 49_152
DEFAULT_COMPACT_AT: Final[int] = 0


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
        self.task_context: int = max(1, int(table.get("task_context", DEFAULT_TASK_CONTEXT)))
        # Where a task's session compacts, passed to every `puffin exec` of the task (compaction
        # spec §4.1). 0, the default, passes none, and the launcher's own limit (60% of the KV pool)
        # applies. Making it `task_context` would make the parallelism's budget true, but measured on
        # 2026-10-02 it cost the task: at 32K no run finished in an hour, at 49,152 the one run
        # finished the code but not its own tests, where the same task without a limit passed in
        # 14-18 minutes (compaction spec §11).
        self.compact_at: int = max(0, int(table.get("compact_at", DEFAULT_COMPACT_AT)))
        self.idle_minutes: float = float(table.get("idle_minutes", DEFAULT_IDLE_MINUTES))
        # Refresh each repository's code index before its tasks start (code-index spec §6.3).
        self.index: bool = bool(table.get("index", True))
        self.index_timeout_s: int = self.parse_duration(table.get("index_timeout", DEFAULT_INDEX_TIMEOUT))

    @classmethod
    def read_table(cls, path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Reads `[night]` from a TOML file.

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
        table = document.get("night", {})
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
