"""
Editor Selection Dataclass for MCP Server.

This module defines the EditorSelection data structure holding active open file path,
line, column, and selected text in JetBrains or VS Code editors.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class EditorSelection:
    """
    Data model representing active IDE cursor selection state.

    Attributes:
        file_path (Optional[str]): Active file path in editor tab.
        line (int): 1-indexed cursor line position.
        column (int): 1-indexed cursor column position.
        selection_text (str): Currently highlighted text selection string.
    """
    file_path: Optional[str] = None
    line: int = 1
    column: int = 1
    selection_text: str = ""
