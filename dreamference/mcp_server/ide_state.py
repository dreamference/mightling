"""
IDE State Store for MCP Server Bridge.

This module provides the IDEState class which maintains state for connected JetBrains and VS Code
editor tabs, active files, cursor positions, selections, and diagnostics.
"""

from typing import Dict, Any, List, Optional
from dreamference.mcp_server.editor_selection import EditorSelection

class IDEState:
    """
    Mock/Bridge state store for connected IDE editor sessions.
    """

    def __init__(self):
        """Initializes empty selection state, open files list, and diagnostics array."""
        self.selection: EditorSelection = EditorSelection()
        self.open_files: List[str] = []
        self.diagnostics: List[Dict[str, Any]] = []

    @property
    def active_file(self) -> Optional[str]:
        """Returns active editor file path."""
        return self.selection.file_path

    @property
    def cursor_line(self) -> int:
        """Returns active editor cursor line."""
        return self.selection.line

    @property
    def cursor_column(self) -> int:
        """Returns active editor cursor column."""
        return self.selection.column

    @property
    def selection_text(self) -> str:
        """Returns active editor selection text."""
        return self.selection.selection_text

    def set_active_editor(self, file_path: str, line: int = 1, col: int = 1, selection: str = "") -> None:
        """
        Updates active editor state and appends file path to open_files list.

        Args:
            file_path (str): Target file path.
            line (int): Cursor line position.
            col (int): Cursor column position.
            selection (str): Active selection text.
        """
        self.selection = EditorSelection(
            file_path=file_path,
            line=line,
            column=col,
            selection_text=selection
        )
        if file_path not in self.open_files:
            self.open_files.append(file_path)
