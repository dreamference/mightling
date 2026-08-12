"""
Parallel Codebase Context Indexing Engine for Dreamng.

This module provides the ContextEngine class which coordinates multi-process AST parsing,
SQLite/FTS5 persistence, and TF-IDF vector search for zero-egress local code retrieval.
"""

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple, Final
try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from dreamng.context_engine.code_symbol import CodeSymbol
from dreamng.context_engine.indexed_file import IndexedFile
from dreamng.context_engine.ast_symbol_extractor import ASTSymbolExtractor
from dreamng.context_engine.tfidf_calculator import TFIDFCalculator
from dreamng.context_engine.sqlite_context_storage import SQLiteContextStorage
from dreamng.context_engine.embedding_calculator import EmbeddingCalculator

# Directories ignored during indexing
IGNORE_DIRS: Final[Set[str]] = {
    ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
    "node_modules", ".idea", ".vscode", "build", "dist", ".dreamng"
}

# Binary and non-source extensions ignored during indexing
IGNORE_EXTENSIONS: Final[Set[str]] = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".exe", ".dll", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz"
}

class ContextEngine:
    """
    Context indexing engine providing zero-egress local semantic code search.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        """
        Initializes ContextEngine for specified workspace root directory.

        Args:
            workspace_root (Optional[str]): Root directory path of target workspace. Defaults to current working directory.
        """
        self.workspace_root: Path = Path(workspace_root or os.getcwd()).resolve()
        self.dreamng_dir: Path = self.workspace_root / ".dreamng"
        self.dreamng_dir.mkdir(parents=True, exist_ok=True)
        self.index_file: Path = self.dreamng_dir / "context_index.json"
        self.sqlite_file: Path = self.dreamng_dir / "context.db"
        self.sqlite_storage: SQLiteContextStorage = SQLiteContextStorage(self.sqlite_file)
        
        self.indexed_files: Dict[str, IndexedFile] = {}
        self.symbols: List[CodeSymbol] = []
        self.tf_idf_index: Dict[str, Dict[str, float]] = {}
        self.idf_table: Dict[str, float] = {}
        self.file_embeddings: Dict[str, np.ndarray] = {}

    def is_ignored(self, path: Path) -> bool:
        """
        Checks whether file path matches ignored directory names or extensions.

        Args:
            path (Path): Path to evaluate.

        Returns:
            bool: True if path should be skipped during indexing.
        """
        for part in path.parts:
            if part in IGNORE_DIRS:
                return True
        if path.suffix in IGNORE_EXTENSIONS:
            return True
        return False

    def tokenize(self, text: str) -> List[str]:
        """Delegates tokenization to TFIDFCalculator."""
        return TFIDFCalculator.tokenize(text)

    def extract_python_ast(self, file_path: Path, rel_path: str, content: str) -> List[CodeSymbol]:
        """Delegates AST parsing to ASTSymbolExtractor."""
        return ASTSymbolExtractor.extract_python_ast(file_path, rel_path, content)

    def _process_single_file(self, abs_path: Path) -> Optional[Tuple[IndexedFile, str]]:
        """
        Worker thread routine that reads file contents, tokenizes terms, and extracts AST symbols.

        Args:
            abs_path (Path): Absolute filesystem path of target file.

        Returns:
            Optional[Tuple[IndexedFile, str]]: Tuple of (IndexedFile, content_string) or None if ignored/failed.
        """
        if self.is_ignored(abs_path):
            return None
        rel_path = str(abs_path.relative_to(self.workspace_root))
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tokens = self.tokenize(content)
            file_symbols: List[CodeSymbol] = []
            if abs_path.suffix == ".py":
                file_symbols = self.extract_python_ast(abs_path, rel_path, content)

            idx_file = IndexedFile(
                rel_path=rel_path,
                abs_path=str(abs_path),
                size_bytes=len(content.encode("utf-8")),
                symbols=file_symbols,
                tokens=tokens
            )
            return idx_file, content
        except Exception:
            return None

    def index_workspace(self, force_reindex: bool = False) -> Dict[str, Any]:
        """
        Indexes all source files in workspace in parallel using ProcessPoolExecutor and persists
        results to `.dreamng/context_index.json` and `.dreamng/context.db`.

        Args:
            force_reindex (bool): If True, reindexes workspace regardless of existing cached files.

        Returns:
            Dict[str, Any]: Summary dictionary containing indexing metrics.
        """
        if not force_reindex and self.load_index():
            return self.get_summary()

        self.indexed_files.clear()
        self.symbols.clear()
        self.tf_idf_index.clear()
        self.idf_table.clear()

        # Step 1: Collect workspace files
        files_to_index: List[Path] = []
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                p = Path(root) / file
                if not self.is_ignored(p):
                    files_to_index.append(p)

        doc_tokens: Dict[str, List[str]] = {}
        all_tokens_set: Set[str] = set()

        # Step 2: Initialize SQLite schema
        conn = self.sqlite_storage.init_db()
        with conn:
            conn.execute("DELETE FROM files;")
            conn.execute("DELETE FROM symbols;")
            try:
                conn.execute("DELETE FROM fts_context;")
            except Exception:
                pass

        # Step 3: Parallel multi-process file parsing across CPU cores (bypasses GIL)
        max_workers = min(32, (os.cpu_count() or 4) * 2)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(self._process_single_file, files_to_index)

        # Step 4: Populate database records and in-memory indexes
        with conn:
            for item in results:
                if item is None:
                    continue
                idx_file, content = item
                rel_path = idx_file.rel_path
                self.indexed_files[rel_path] = idx_file
                self.symbols.extend(idx_file.symbols)
                doc_tokens[rel_path] = idx_file.tokens
                all_tokens_set.update(idx_file.tokens)

                conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?);", (rel_path, idx_file.abs_path, idx_file.size_bytes))
                for sym in idx_file.symbols:
                    conn.execute("INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?);", (sym.name, sym.symbol_type, sym.file_path, sym.line_start, sym.line_end, sym.signature, sym.docstring))
                try:
                    conn.execute("INSERT INTO fts_context (rel_path, content) VALUES (?, ?);", (rel_path, content))
                except Exception:
                    pass

        conn.close()

        # Step 5: Calculate TF-IDF matrix (kept for backward compat) + semantic embeddings
        self.idf_table, self.tf_idf_index = TFIDFCalculator.compute_matrix(doc_tokens, all_tokens_set)

        # Compute and store dense embeddings + vec table
        doc_contents = {p: c for p, (f, c) in zip([item[0].rel_path for item in results if item], [(item[0], item[1]) for item in results if item]) if p}
        # Re-collect contents for embedding
        file_to_content = {}
        for item in results:
            if item:
                idx_file, content = item
                file_to_content[idx_file.rel_path] = content
        file_to_emb, emb_matrix = EmbeddingCalculator.compute_embeddings(file_to_content)
        self.file_embeddings = file_to_emb
        if emb_matrix.size > 0:
            self.sqlite_storage.insert_vectors(list(file_to_emb.keys()), emb_matrix)

        self.save_index()
        return self.get_summary()

    def search_code(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Performs local hybrid semantic search combining SQLite FTS5 rank, TF-IDF, and dense vector similarity.

        Args:
            query (str): Natural language or code search query string.
            top_k (int): Number of top matching files to return.

        Returns:
            List[Dict[str, Any]]: List of matching file records sorted by score descending.
        """
        query_tokens = self.tokenize(query)
        # Combine FTS5 full-text score with TF-IDF vector score
        file_scores: Dict[str, float] = self.sqlite_storage.search_fts(query_tokens, top_k)

        for token in query_tokens:
            if token in self.tf_idf_index:
                for rel_path, tfidf in self.tf_idf_index[token].items():
                    file_scores[rel_path] = file_scores.get(rel_path, 0.0) + tfidf

        # Semantic vector component (query embedding vs stored doc embeddings)
        q_emb = EmbeddingCalculator.embed_texts([query])
        if q_emb.size > 0 and self.file_embeddings:
            vec_scores = {}
            for rel_path, emb in self.file_embeddings.items():
                sim = float(np.dot(q_emb[0], emb) / (np.linalg.norm(q_emb[0]) * np.linalg.norm(emb) + 1e-8))
                vec_scores[rel_path] = max(0.0, sim)
            for rel_path, sim in vec_scores.items():
                file_scores[rel_path] = file_scores.get(rel_path, 0.0) + sim * 3.0  # weight semantic higher

        sorted_files = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        results: List[Dict[str, Any]] = []
        for rel_path, score in sorted_files:
            idx_file = self.indexed_files.get(rel_path)
            if idx_file:
                results.append({
                    "rel_path": rel_path,
                    "score": round(score, 4),
                    "symbols": [asdict(s) for s in idx_file.symbols],
                    "size_bytes": idx_file.size_bytes
                })
        return results

    def save_index(self) -> None:
        """Saves cached index JSON to `.dreamng/context_index.json`."""
        data: Dict[str, Any] = {
            "workspace_root": str(self.workspace_root),
            "symbol_count": len(self.symbols),
            "file_count": len(self.indexed_files),
            "symbols": [asdict(s) for s in self.symbols],
            "files": {
                r: {
                    "rel_path": f.rel_path,
                    "size_bytes": f.size_bytes,
                    "symbols": [asdict(s) for s in f.symbols]
                }
                for r, f in self.indexed_files.items()
            }
        }
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_index(self) -> bool:
        """Loads cached index from disk if available."""
        if not self.index_file.exists():
            return False
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.symbols = [CodeSymbol(**s) for s in data.get("symbols", [])]
            self.indexed_files = {}
            for rel_path, file_data in data.get("files", {}).items():
                syms = [CodeSymbol(**s) for s in file_data.get("symbols", [])]
                abs_p = str(self.workspace_root / rel_path)
                self.indexed_files[rel_path] = IndexedFile(
                    rel_path=rel_path,
                    abs_path=abs_p,
                    size_bytes=file_data.get("size_bytes", 0),
                    symbols=syms,
                    tokens=[]
                )
            return True
        except Exception:
            return False

    def get_summary(self) -> Dict[str, Any]:
        """Returns workspace indexing stats."""
        return {
            "workspace_root": str(self.workspace_root),
            "total_indexed_files": len(self.indexed_files),
            "total_ast_symbols": len(self.symbols),
            "index_file": str(self.index_file),
            "sqlite_file": str(self.sqlite_file) if self.sqlite_file.exists() else None,
        }
