"""
Local Semantic Embedding Calculator using sentence-transformers.

Provides EmbeddingCalculator for generating dense vectors from code/text
and computing cosine similarity. Designed for GB10 unified memory (loads
~100-300 MB model alongside vLLM).
"""

from typing import Dict, List, Tuple
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore


class EmbeddingCalculator:
    """
    Generates and scores dense semantic embeddings for hybrid search.
    """

    _model = None  # lazy singleton

    @classmethod
    def _get_model(cls) -> "SentenceTransformer":
        if cls._model is None and SentenceTransformer is not None:
            cls._model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
        return cls._model

    @classmethod
    def embed_texts(cls, texts: List[str]) -> np.ndarray:
        """
        Encode list of texts into normalized embeddings.
        Returns shape (n_texts, dim).
        """
        model = cls._get_model()
        if model is None:
            return np.zeros((len(texts), 768), dtype=np.float32)
        embeddings = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return embeddings.astype(np.float32)

    @classmethod
    def compute_embeddings(
        cls, doc_contents: Dict[str, str]
    ) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
        """
        Compute embeddings for all documents.

        Returns:
            (file_to_embedding, embedding_matrix) where matrix rows correspond to sorted file list.
        """
        if not doc_contents:
            return {}, np.zeros((0, 768), dtype=np.float32)

        rel_paths = list(doc_contents.keys())
        texts = [doc_contents[p] for p in rel_paths]
        embeddings = cls.embed_texts(texts)
        file_to_emb = {p: embeddings[i] for i, p in enumerate(rel_paths)}
        return file_to_emb, embeddings