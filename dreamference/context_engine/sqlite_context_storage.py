"""
SQLite + FTS5 Context Database Storage Engine.

This module provides the SQLiteContextStorage class for creating and querying an embedded
SQLite database with FTS5 full-text search virtual tables stored at `.dreamference/context.db`.

On GB10 (128 GB unified LPDDR5X), every connection applies PRAGMA mmap_size=2GB so the
DB is memory-mapped directly into the shared SoC RAM, eliminating disk I/O during indexing
and search.
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np

class SQLiteContextStorage:
    """
    Storage layer managing SQLite tables for files, symbols, and FTS5 full-text search index.
    """

    def __init__(self, db_path: Path):
        """
        Initializes SQLite storage layer with database path.

        Args:
            db_path (Path): Path to .dreamference/context.db file.
        """
        self.db_path = db_path

    def init_db(self) -> sqlite3.Connection:
        """
        Creates SQLite schema tables (`files`, `symbols`, and `fts_context` virtual table).

        Applies PRAGMA mmap_size = 2147483648 (2 GB) to map the DB directly into
        the GB10's 128 GB unified LPDDR5X memory for faster access during indexing.

        Returns:
            sqlite3.Connection: Open SQLite database connection.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA mmap_size = 2147483648;")
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    rel_path TEXT PRIMARY KEY,
                    abs_path TEXT,
                    size_bytes INTEGER
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    name TEXT,
                    symbol_type TEXT,
                    file_path TEXT,
                    line_start INTEGER,
                    line_end INTEGER,
                    signature TEXT,
                    docstring TEXT
                );
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts_context USING fts5(
                        rel_path UNINDEXED,
                        content
                    );
                """)
            except Exception:
                pass
            # Plain rows, not a sqlite-vec `vec0` table: that extension was never loaded into these
            # connections, so the table creation and every insert failed silently and no vector
            # was ever stored. Search scores the vectors in Python, so nothing needs the extension.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    rel_path TEXT PRIMARY KEY,
                    vector BLOB NOT NULL
                );
            """)
        return conn

    def search_fts(self, query_tokens: List[str], top_k: int) -> Dict[str, float]:
        """
        Queries FTS5 full-text search table for matching code tokens and returns BM25-ranked scores.

        Args:
            query_tokens (List[str]): Extracted search query tokens.
            top_k (int): Maximum number of search results to retrieve.

        Returns:
            Dict[str, float]: Map of relative file paths to FTS search scores.
        """
        file_scores: Dict[str, float] = {}
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("PRAGMA mmap_size = 2147483648;")
                clean_query = " OR ".join([f'"{t}"' for t in query_tokens if t.isalnum()])
                if clean_query:
                    cursor = conn.cursor()
                    cursor.execute("SELECT rel_path, rank FROM fts_context WHERE fts_context MATCH ? ORDER BY rank LIMIT ?", (clean_query, top_k * 2))
                    for row in cursor.fetchall():
                        r_path, rank = row[0], abs(row[1])
                        # Invert rank score so higher values indicate better relevance
                        file_scores[r_path] = file_scores.get(r_path, 0.0) + (1.0 / (rank + 1.0)) * 2.0
                conn.close()
            except Exception:
                pass
        return file_scores

    def store_embeddings(self, file_to_embedding: Dict[str, np.ndarray]) -> None:
        """
        Replaces the stored document embeddings with `file_to_embedding`.

        Keyed by path. (The old vec0 rows were keyed by `hash(path)`, which Python randomises per
        process, so even a working table could not have been read back after a restart.)

        Args:
            file_to_embedding (Dict[str, np.ndarray]): File path to float32 embedding.
        """
        conn = self.init_db()
        with conn:
            conn.execute("DELETE FROM embeddings;")
            conn.executemany(
                "INSERT INTO embeddings (rel_path, vector) VALUES (?, ?);",
                [(path, np.asarray(vector, dtype=np.float32).tobytes()) for path, vector in file_to_embedding.items()],
            )
        conn.close()

    def load_embeddings(self) -> Dict[str, np.ndarray]:
        """
        Reads back the stored document embeddings.

        Returns:
            Dict[str, np.ndarray]: File path to float32 embedding; empty if none are stored.
        """
        if not self.db_path.exists():
            return {}
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT rel_path, vector FROM embeddings;").fetchall()
        except sqlite3.OperationalError:
            rows = []  # an index built before this table existed
        finally:
            conn.close()
        return {path: np.frombuffer(blob, dtype=np.float32) for path, blob in rows}
