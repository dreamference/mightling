from typing import Dict, Any, List, Optional
from dgxcoder.mcp_server.editor_selection import EditorSelection

class IDEState:
    """Mock/Bridge state store for connected IDE editor sessions."""

    def __init__(self):
        self.selection: EditorSelection = EditorSelection()
        self.open_files: List[str] = []
        self.diagnostics: List[Dict[str, Any]] = []

    @property
    def active_file(self) -> Optional[str]:
        return self.selection.file_path

    @property
    def cursor_line(self) -> int:
        return self.selection.line

    @property
    def cursor_column(self) -> int:
        return self.selection.column

    @property
    def selection_text(self) -> str:
        return self.selection.selection_text

    def set_active_editor(self, file_path: str, line: int = 1, col: int = 1, selection: str = "") -> None:
        self.selection = EditorSelection(
            file_path=file_path,
            line=line,
            column=col,
            selection_text=selection
        )
        if file_path not in self.open_files:
            self.open_files.append(file_path)
