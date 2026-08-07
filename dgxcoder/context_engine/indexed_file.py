from dataclasses import dataclass
from typing import List
from dgxcoder.context_engine.code_symbol import CodeSymbol

@dataclass
class IndexedFile:
    rel_path: str
    abs_path: str
    size_bytes: int
    symbols: List[CodeSymbol]
    tokens: List[str]
