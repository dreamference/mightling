"""
Python AST Symbol Extractor for Context Engine.

This module provides the ASTSymbolExtractor class which uses Python's native `ast` module
to parse code files and extract class and function definitions.
"""

import ast
from pathlib import Path
from typing import List
from neurr.context_engine.code_symbol import CodeSymbol

class ASTSymbolExtractor:
    """
    Parser class extracting Python classes, functions, async functions, signatures, and docstrings.
    """

    @classmethod
    def extract_python_ast(cls, file_path: Path, rel_path: str, content: str) -> List[CodeSymbol]:
        """
        Parses Python source content using `ast.parse` and extracts top-level/nested definitions.

        Args:
            file_path (Path): Path to source file.
            rel_path (str): Relative workspace path.
            content (str): Complete file content string.

        Returns:
            List[CodeSymbol]: Extracted CodeSymbol objects.
        """
        symbols: List[CodeSymbol] = []
        try:
            tree = ast.parse(content, filename=str(file_path))
            for node in ast.walk(tree):
                # Extract Class Definitions
                if isinstance(node, ast.ClassDef):
                    doc = ast.get_docstring(node)
                    symbols.append(CodeSymbol(
                        name=node.name,
                        symbol_type="class",
                        file_path=rel_path,
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                        signature=f"class {node.name}",
                        docstring=doc
                    ))
                # Extract Function & Async Function Definitions
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node)
                    args = [a.arg for a in node.args.args]
                    sig = f"def {node.name}({', '.join(args)})"
                    symbols.append(CodeSymbol(
                        name=node.name,
                        symbol_type="function",
                        file_path=rel_path,
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                        signature=sig,
                        docstring=doc
                    ))
        except Exception:
            # Silently pass syntax errors for partial/malformed code files
            pass
        return symbols
