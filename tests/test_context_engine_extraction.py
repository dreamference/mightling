"""What the context engine extracts from a file: its symbols and its TF-IDF tokens and weights."""

from dataclasses import asdict
from pathlib import Path

from dreamference.context_engine.ast_symbol_extractor import ASTSymbolExtractor

STORE_SOURCE = '''class Store:
    """Keeps rows."""

    def put(self, key, value):
        """Stores one row."""
        return key


async def fetch(url, timeout):
    return url
'''


def test_each_definition_keeps_its_kind_lines_signature_docstring_and_relative_path() -> None:
    # Search results hand these to the agent: a wrong line range or an absolute path sends it to
    # the wrong place, and a missing async function or method is a symbol it cannot find.
    symbols = ASTSymbolExtractor.extract_python_ast(
        Path("/workspace/pkg/store.py"), "pkg/store.py", STORE_SOURCE
    )
    found = sorted((asdict(symbol) for symbol in symbols), key=lambda symbol: symbol["line_start"])
    assert found == [
        {
            "name": "Store",
            "symbol_type": "class",
            "file_path": "pkg/store.py",
            "line_start": 1,
            "line_end": 6,
            "signature": "class Store",
            "docstring": "Keeps rows.",
        },
        {
            "name": "put",
            "symbol_type": "function",
            "file_path": "pkg/store.py",
            "line_start": 4,
            "line_end": 6,
            "signature": "def put(self, key, value)",
            "docstring": "Stores one row.",
        },
        {
            "name": "fetch",
            "symbol_type": "function",
            "file_path": "pkg/store.py",
            "line_start": 9,
            "line_end": 10,
            "signature": "def fetch(url, timeout)",
            "docstring": None,
        },
    ]


def test_a_file_that_does_not_parse_has_no_symbols_and_does_not_raise() -> None:
    # A half-written file is normal in a workspace being edited; raising here dropped the whole
    # file from the index, its keywords included.
    assert (
        ASTSymbolExtractor.extract_python_ast(Path("broken.py"), "broken.py", "def broken(:\n")
        == []
    )
