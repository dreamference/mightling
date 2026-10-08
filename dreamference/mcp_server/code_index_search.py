"""
Code search through `ling-code`, the code index router.

This module provides the CodeIndexSearch class, which answers the MCP server's
`workspace_search_code` from the router's index (specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md §8)
instead of building the `dreamference` context engine. The router reads two SQLite files and
exits in milliseconds; the context engine walks the workspace and loads an embedding model on
first use. The engine remains the fallback where `ling-code` is not installed or the workspace
has no index yet.
"""

import json
import os
import subprocess
from typing import Any, Dict, Final, List, Optional

from dreamference.runner.codex_branded_builder import INSTALL_DIR

# `ling-code` exits with this when the workspace has no index (and prints what to run).
NO_INDEX_EXIT: Final[int] = 3

SEARCH_TIMEOUT_S: Final[int] = 20


class CodeIndexSearch:
    """Runs `ling-code search` and shapes its rows for MCP clients."""

    @classmethod
    def executable(cls) -> Optional[str]:
        """
        Locates the installed `ling-code`.

        Only Mightling's own install directory counts, as for `ling`: a `ling-code` found on
        PATH could be another build, whose answers this module would pass on unchecked.

        Returns:
            Optional[str]: Absolute path to the binary, or None if it has not been built.
        """
        path = os.path.join(INSTALL_DIR, "bin", "ling-code")
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None

    @classmethod
    def search(cls, query: str, top_k: int = 5, cwd: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Searches the workspace's code index for definitions matching the words.

        Args:
            query (str): Words to match against names, qualified names and bodies.
            top_k (int): Number of results to return.
            cwd (Optional[str]): The workspace; defaults to the current directory.

        Returns:
            Optional[List[Dict[str, Any]]]: One record per definition (`rel_path`, `line`, `kind`,
            `symbol`, `source`), best first; None when the router cannot answer here (not
            installed, no index for this workspace, or a failed call), so the caller falls back.
        """
        binary = cls.executable()
        if binary is None or not query.strip():
            return None
        try:
            result = subprocess.run(
                [binary, "search", *query.split(), "--json"],
                cwd=cwd or os.getcwd(), capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=SEARCH_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            # NO_INDEX_EXIT, or an error: either way the engine answers instead.
            return None
        try:
            answer = json.loads(result.stdout)
        except ValueError:
            return None
        records: List[Dict[str, Any]] = []
        for row in answer.get("rows", [])[:max(0, top_k)]:
            kind, _, symbol = str(row.get("detail", "")).partition(" ")
            records.append({
                "rel_path": row.get("path"),
                "line": row.get("line"),
                "kind": kind,
                "symbol": symbol,
                "source": "ling-code",
            })
        return records
