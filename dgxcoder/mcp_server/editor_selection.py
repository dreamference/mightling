from dataclasses import dataclass
from typing import Optional

@dataclass
class EditorSelection:
    file_path: Optional[str] = None
    line: int = 1
    column: int = 1
    selection_text: str = ""
