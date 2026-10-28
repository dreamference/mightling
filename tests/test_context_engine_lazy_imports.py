"""Importing the context engine must not load the machine-learning libraries (they cost seconds)."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
HEAVY_MODULES: Final[tuple[str, ...]] = (
    "torch",
    "transformers",
    "sentence_transformers",
    "sklearn",
)
SUBPROCESS_TIMEOUT_SECONDS: Final[int] = 120

IMPORT_PROBE: Final[str] = """
import sys
import dreamference.context_engine
print(",".join(name for name in {heavy!r} if name in sys.modules))
"""

UNAVAILABLE_PROBE: Final[str] = """
import dreamference.context_engine.embedding_calculator as module
module.SentenceTransformer = None
module.EmbeddingCalculator._model = None
module.EmbeddingCalculator._unavailable = False
print(module.EmbeddingCalculator.embed_texts(["x"]))
"""


def _run(code: str) -> str:
    """Runs a probe in a fresh interpreter with the repository on the path and returns its output."""
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def test_importing_the_context_engine_loads_no_machine_learning_library():
    assert _run(IMPORT_PROBE.format(heavy=HEAVY_MODULES)) == ""


def test_embeddings_are_none_when_sentence_transformers_is_unavailable():
    assert _run(UNAVAILABLE_PROBE) == "None"
