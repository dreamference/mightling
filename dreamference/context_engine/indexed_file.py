"""
Indexed Source File Model for Context Engine.

This module defines the IndexedFile dataclass holding file paths, size, extracted AST symbols,
and normalized tokens.
"""

from dataclasses import dataclass
from typing import List
from dreamference.context_engine.code_symbol import CodeSymbol

@dataclass
class IndexedFile:
    """
    Data model representing an indexed workspace source file.

    Attributes:
        rel_path (str): Relative path within workspace root.
        abs_path (str): Absolute filesystem path.
        size_bytes (int): Total file size in bytes.
        symbols (List[CodeSymbol]): List of AST symbols parsed from file.
        tokens (List[str]): List of normalized code identifiers and sub-tokens.
    """
    rel_path: str
    abs_path: str
    size_bytes: int
    symbols: List[CodeSymbol]
    tokens: List[str]
