"""What the context engine extracts from a file: its symbols and its TF-IDF tokens and weights."""

import math
from dataclasses import asdict
from pathlib import Path

import pytest

from dreamference.context_engine.ast_symbol_extractor import ASTSymbolExtractor
from dreamference.context_engine.tfidf_calculator import TFIDFCalculator

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


def test_tokens_split_camel_case_keep_digits_and_the_whole_word_and_drop_single_letters() -> None:
    assert TFIDFCalculator.tokenize("parseHTTPResponse snake_case v2Config x") == [
        "parse",
        "httpresponse",
        "parsehttpresponse",
        "snake_case",
        "snake_case",
        "v2",
        "config",
        "v2config",
    ]


def test_tf_idf_weights_are_term_frequency_times_smoothed_inverse_document_frequency() -> None:
    docs = {"a.py": ["alpha", "alpha", "beta"], "b.py": ["beta"], "c.py": ["gamma"]}
    idf, index = TFIDFCalculator.compute_matrix(docs, {"alpha", "beta", "gamma"})
    rare = math.log(4 / 2) + 1  # in one document of three
    common = math.log(4 / 3) + 1  # in two of three
    assert idf == pytest.approx({"alpha": rare, "beta": common, "gamma": rare})
    assert index["alpha"] == pytest.approx({"a.py": 2 / 3 * rare})
    assert index["beta"] == pytest.approx({"a.py": 1 / 3 * common, "b.py": common})
    assert index["gamma"] == pytest.approx({"c.py": rare})


def test_a_workspace_of_one_file_is_still_weighted() -> None:
    assert TFIDFCalculator.compute_matrix({"only.py": ["x"]}, {"x"}) == (
        {"x": 1.0},
        {"x": {"only.py": 1.0}},
    )


def test_only_tokens_in_the_token_set_are_weighted() -> None:
    idf, index = TFIDFCalculator.compute_matrix({"a.py": ["alpha", "beta"]}, {"alpha"})
    assert (idf, index) == ({"alpha": 1.0}, {"alpha": {"a.py": 0.5}})
