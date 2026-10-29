"""
Local Semantic Embedding Calculator using sentence-transformers.

Provides EmbeddingCalculator for generating dense vectors from code/text
and computing cosine similarity. Designed for GB10 unified memory (loads
~100-300 MB model alongside vLLM).
"""

import importlib
from typing import TYPE_CHECKING, Any, Dict, Final, List, Optional
import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer as SentenceTransformerType

# sentence-transformers pulls in torch and transformers (about 2.5 s), so it is imported on first
# use. The attribute keeps its old meaning once resolved: the class, or None when the package is
# missing. Until then it is the _NOT_LOADED sentinel; tests may set it to a stand-in or to None.
_NOT_LOADED: Final = object()
SentenceTransformer: Any = _NOT_LOADED

EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"

# nomic-embed-text was trained with task prefixes and retrieves noticeably worse without them:
# documents are embedded as "search_document: ...", queries as "search_query: ...".
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# The model runs on the CPU. On GB10 the GPU is vLLM's: with it resident CUDA refuses even this
# model (out of memory, with gigabytes of host memory free), and a failed attempt still leaves a
# CUDA context behind in unified memory.
EMBEDDING_DEVICE: Final[str] = "cpu"

# Attention memory grows with the square of the sequence: at nomic's full 8,192 tokens and a batch
# of 32 it runs to gigabytes and pushed the host towards earlyoom's limit beside a resident vLLM.
# A file's first 1,024 tokens carry its imports, docstring and leading definitions.
MAX_SEQUENCE_TOKENS: Final[int] = 1024
ENCODE_BATCH_SIZE: Final[int] = 8


def _import_sentence_transformer() -> "type[SentenceTransformerType] | None":
    """Imports the SentenceTransformer class: None when sentence-transformers is not installed."""
    try:
        return importlib.import_module("sentence_transformers").SentenceTransformer
    except ImportError:
        return None


class EmbeddingCalculator:
    """
    Generates and scores dense semantic embeddings for hybrid search.
    """

    _model = None  # lazy singleton
    _unavailable = False  # set once loading has failed, so it is not retried per call

    @classmethod
    def _get_model(cls) -> Optional["SentenceTransformerType"]:
        """
        Loads the embedding model once, or reports that it cannot be loaded.

        Loading fetches the model from the HuggingFace cache, and from the network if it is not
        cached. On a machine without either, indexing used to fail outright; it now continues with
        keyword search only and says so once.

        Returns:
            Optional[SentenceTransformer]: The model, or None when it is unavailable.
        """
        global SentenceTransformer
        if cls._model is None and not cls._unavailable:
            # The import is inside the broad `except` too: a broken torch or transformers install
            # raises other errors than ImportError on import (OSError for a missing library,
            # RuntimeError), and they escaped into indexing and the MCP tool.
            try:
                if SentenceTransformer is _NOT_LOADED:
                    SentenceTransformer = _import_sentence_transformer()
                if SentenceTransformer is not None:
                    cls._model = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=True, device=EMBEDDING_DEVICE)
                    cls._model.max_seq_length = MAX_SEQUENCE_TOKENS
            except Exception as error:
                cls._unavailable = True
                print(f"⚠️  Embedding model {EMBEDDING_MODEL} is unavailable ({type(error).__name__}); "
                      "searching by keyword only.")
        return cls._model

    @classmethod
    def embed_texts(cls, texts: List[str], prefix: str = DOCUMENT_PREFIX) -> Optional[np.ndarray]:
        """
        Encodes texts into normalised embeddings.

        Args:
            texts (List[str]): The texts to embed.
            prefix (str): The task prefix, DOCUMENT_PREFIX or QUERY_PREFIX.

        Returns:
            Optional[np.ndarray]: Shape (n_texts, dim), or None when no model is available. Not
                zeros: a zero vector scores every document equally and hides that search is
                keyword-only.
        """
        model = cls._get_model()
        if model is None:
            return None
        embeddings = model.encode(
            [prefix + text for text in texts], batch_size=ENCODE_BATCH_SIZE,
            normalize_embeddings=True, convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    @classmethod
    def compute_embeddings(cls, doc_contents: Dict[str, str]) -> Dict[str, np.ndarray]:
        """
        Computes document embeddings for all files.

        Args:
            doc_contents (Dict[str, str]): File path to file content.

        Returns:
            Dict[str, np.ndarray]: File path to embedding; empty when no model is available.
        """
        if not doc_contents:
            return {}
        rel_paths = list(doc_contents.keys())
        embeddings = cls.embed_texts([doc_contents[p] for p in rel_paths], DOCUMENT_PREFIX)
        if embeddings is None:
            return {}
        return {p: embeddings[i] for i, p in enumerate(rel_paths)}
