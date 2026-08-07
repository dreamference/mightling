import ast
from pathlib import Path
from typing import List
from dgxcoder.context_engine.code_symbol import CodeSymbol

class ASTSymbolExtractor:
    """Parses code AST tree structures to extract symbol definitions."""

    @classmethod
    def extract_python_ast(cls, file_path: Path, rel_path: str, content: str) -> List[CodeSymbol]:
        symbols: List[CodeSymbol] = []
        try:
            tree = ast.parse(content, filename=str(file_path))
            for node in ast.walk(tree):
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
            pass
        return symbols
