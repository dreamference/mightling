"""
Code Symbol AST Representation for Context Engine.

This module defines the CodeSymbol dataclass which represents extracted AST symbols
(classes, functions, methods) with line ranges, signatures, and docstrings.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class CodeSymbol:
    """
    Data model for an extracted Abstract Syntax Tree (AST) code symbol.

    Attributes:
        name (str): Symbol identifier name (e.g. 'DGXCoderConfig').
        symbol_type (str): Type of symbol ('class', 'function', 'method').
        file_path (str): Workspace relative path to source file.
        line_start (int): 1-indexed starting line number.
        line_end (int): 1-indexed ending line number.
        signature (str): Extracted definition signature line (e.g. 'def run_session(self, prompt)').
        docstring (Optional[str]): Associated docstring text if present.
    """
    name: str
    symbol_type: str
    file_path: str
    line_start: int
    line_end: int
    signature: str
    docstring: Optional[str] = None
