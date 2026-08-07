from dataclasses import dataclass
from typing import Optional

@dataclass
class CodeSymbol:
    name: str
    symbol_type: str
    file_path: str
    line_start: int
    line_end: int
    signature: str
    docstring: Optional[str] = None
