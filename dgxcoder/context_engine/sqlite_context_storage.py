import sqlite3
from pathlib import Path
from typing import Dict, List

class SQLiteContextStorage:
    """Encapsulates embedded SQLite + FTS5 full-text search persistence."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
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
        return conn

    def search_fts(self, query_tokens: List[str], top_k: int) -> Dict[str, float]:
        file_scores: Dict[str, float] = {}
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(self.db_path)
                clean_query = " OR ".join([f'"{t}"' for t in query_tokens if t.isalnum()])
                if clean_query:
                    cursor = conn.cursor()
                    cursor.execute("SELECT rel_path, rank FROM fts_context WHERE fts_context MATCH ? ORDER BY rank LIMIT ?", (clean_query, top_k * 2))
                    for row in cursor.fetchall():
                        r_path, rank = row[0], abs(row[1])
                        file_scores[r_path] = file_scores.get(r_path, 0.0) + (1.0 / (rank + 1.0)) * 2.0
                conn.close()
            except Exception:
                pass
        return file_scores
