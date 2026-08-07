import ast
import json
import math
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
    "node_modules", ".idea", ".vscode", "build", "dist", ".dgxcoder"
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".exe", ".dll", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz"
}

@dataclass
class CodeSymbol:
    name: str
    symbol_type: str  # class, function, method, variable
    file_path: str
    line_start: int
    line_end: int
    signature: str
    docstring: Optional[str] = None

@dataclass
class IndexedFile:
    rel_path: str
    abs_path: str
    size_bytes: int
    symbols: List[CodeSymbol]
    tokens: List[str]

class ContextEngine:
    """Parallel AST + SQLite/FTS5 Vector Indexing Engine for DGXCoder zero-egress local code context."""

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.dgxcoder_dir = self.workspace_root / ".dgxcoder"
        self.dgxcoder_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.dgxcoder_dir / "context_index.json"
        self.sqlite_file = self.dgxcoder_dir / "context.db"
        
        self.indexed_files: Dict[str, IndexedFile] = {}
        self.symbols: List[CodeSymbol] = []
        self.tf_idf_index: Dict[str, Dict[str, float]] = {}  # token -> {rel_path: tfidf}
        self.idf_table: Dict[str, float] = {}

    def _init_db(self) -> sqlite3.Connection:
        """Initializes SQLite database with FTS5 full-text search table."""
        conn = sqlite3.connect(self.sqlite_file)
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

    def is_ignored(self, path: Path) -> bool:
        """Check if path should be ignored during indexing."""
        for part in path.parts:
            if part in IGNORE_DIRS:
                return True
        if path.suffix in IGNORE_EXTENSIONS:
            return True
        return False

    def tokenize(self, text: str) -> List[str]:
        """Splits code/comments into identifiers and normalized terms."""
        words = re.findall(r'[a-zA-Z0-9_]+', text)
        tokens = []
        for word in words:
            sub_words = re.sub('([a-z0-9])([A-Z])', r'\1 \2', word).lower().split()
            tokens.extend(sub_words)
            tokens.append(word.lower())
        return [t for t in tokens if len(t) > 1]

    def extract_python_ast(self, file_path: Path, rel_path: str, content: str) -> List[CodeSymbol]:
        """Parses Python code using native AST to extract classes and functions."""
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

    def _process_single_file(self, abs_path: Path) -> Optional[Tuple[IndexedFile, str]]:
        """Processes a single file in worker thread."""
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
        """Indexes all supported source files in the workspace using multi-threaded execution."""
        if not force_reindex and self.load_index():
            return self.get_summary()

        self.indexed_files.clear()
        self.symbols.clear()
        self.tf_idf_index.clear()
        self.idf_table.clear()

        files_to_index: List[Path] = []
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                p = Path(root) / file
                if not self.is_ignored(p):
                    files_to_index.append(p)

        doc_count = 0
        doc_tokens: Dict[str, List[str]] = {}
        all_tokens_set: Set[str] = set()

        conn = self._init_db()
        with conn:
            conn.execute("DELETE FROM files;")
            conn.execute("DELETE FROM symbols;")
            try:
                conn.execute("DELETE FROM fts_context;")
            except Exception:
                pass

        # Parallel multi-threaded file parsing across CPU cores
        max_workers = min(32, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(self._process_single_file, files_to_index)

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
                doc_count += 1

                conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?);", (rel_path, idx_file.abs_path, idx_file.size_bytes))
                for sym in idx_file.symbols:
                    conn.execute("INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?);", (sym.name, sym.symbol_type, sym.file_path, sym.line_start, sym.line_end, sym.signature, sym.docstring))
                try:
                    conn.execute("INSERT INTO fts_context (rel_path, content) VALUES (?, ?);", (rel_path, content))
                except Exception:
                    pass

        conn.close()

        # Compute TF-IDF Index
        if doc_count > 0:
            for token in all_tokens_set:
                df = sum(1 for tokens in doc_tokens.values() if token in tokens)
                idf = math.log((doc_count + 1) / (df + 1)) + 1
                self.idf_table[token] = idf
                
                self.tf_idf_index[token] = {}
                for rel_path, tokens in doc_tokens.items():
                    tf = tokens.count(token) / (len(tokens) or 1)
                    if tf > 0:
                        self.tf_idf_index[token][rel_path] = tf * idf

        self.save_index()
        return self.get_summary()

    def search_code(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Performs SQLite/FTS5 and TF-IDF local semantic search for relevant workspace code."""
        query_tokens = self.tokenize(query)
        file_scores: Dict[str, float] = {}

        # 1. Try SQLite FTS5 search if database exists
        if self.sqlite_file.exists():
            try:
                conn = sqlite3.connect(self.sqlite_file)
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

        # 2. Add TF-IDF vector score
        for token in query_tokens:
            if token in self.tf_idf_index:
                for rel_path, tfidf in self.tf_idf_index[token].items():
                    file_scores[rel_path] = file_scores.get(rel_path, 0.0) + tfidf

        sorted_files = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        results = []
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
        """Saves cached index into .dgxcoder/context_index.json."""
        data = {
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
        """Loads cached index if available."""
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
