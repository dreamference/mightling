# Dreamference Code Indexing & Context Engine

> **Version:** 1.2.0
> **Subject:** AST Extraction, Hybrid Search, SQLite/FTS5 Indexing, Testing

---

## Table of Contents

- [1. Code Indexing Pipeline](#1-code-indexing-pipeline)
- [2. AST Symbol Extraction](#2-ast-symbol-extraction)
- [3. Hybrid Search Architecture](#3-hybrid-search-architecture)
- [4. Cache Behavior](#4-cache-behavior)
- [5. Performance Notes](#5-performance-notes)
- [6. Tests Architecture](#6-tests-architecture)

---

## 1. Code Indexing Pipeline

### 1.1. Entry Points

- **`puffin-admin init`**: Always forces a full re-index (`ContextEngine.index_workspace(force_reindex=True)`).
- **`puffin-admin index [--dir PATH] [--force]`**: Manual indexing of any directory (defaults to CWD).

### 1.2. Parallel Execution

`index_workspace` uses `ProcessPoolExecutor(max_workers = min(32, cpu_count*2))` to bypass the GIL on the ARM Cortex host (`context_engine.py:156-159`).

This enables efficient multi-file indexing on the unified memory architecture where I/O and symbol extraction can overlap.

### 1.3. Ingestion Pipeline

**Step 1: File Discovery**
- Walk workspace directory recursively
- Skip `IGNORE_DIRS`: `.git`, `.venv`, `node_modules`, `.idea`, `.dreamference`, and others
- Skip `IGNORE_EXTENSIONS`: binaries, images, archives

**Step 2: Symbol Extraction**
- For every accepted file: read content
- Tokenize with camelCase/snake_case awareness
- Extract Python AST symbols (class, function, signature, docstring, line ranges) via `ASTSymbolExtractor`

**Step 3: SQLite Persistence**
Persist to SQLite with:
- `files` table: file path, size, last modified
- `symbols` table: symbol name, type (class/function), signature, docstring, line start/end, parent class
- `fts_context` FTS5 virtual table: full original file content (for keyword search)
- `vec_context` vec0 virtual table: dense embeddings (768-dim, stored as BLOB)
- **PRAGMA mmap_size = 2147483648** (2 GB) for unified-memory access on GB10

**Step 4: TF-IDF Computation**
- Compute in-memory TF-IDF matrix (`TFIDFCalculator.compute_matrix`)
- Store term-document frequencies for ranking

**Step 5: Semantic Embeddings**
- Compute 768-dim embeddings via `EmbeddingCalculator` (sentence-transformers + nomic-ai/nomic-embed-text-v1.5)
- Store in `vec_context` (sqlite-vec virtual table)
- **Important**: To maintain the air-gap, the embedding model is pre-downloaded offline during `init`
- Requires `trust_remote_code=True`
- Correctly applies `search_document: ` prefix (for ingestion) and `search_query: ` prefix (for search)

**Step 6: Cache Artifacts**
- Write JSON cache `.dreamference/context_index.json` (symbols + metadata only)
- Close DB (reopen on demand for queries)

---

## 2. AST Symbol Extraction

### 2.1. Supported Symbols

Python AST extraction captures:

| Symbol Type | Attributes |
| :---------- | :---------- |
| **Class** | Name, docstring, inheritance list, line start/end, methods |
| **Function** | Name, arguments/parameters, docstring, line start/end, return type hint |
| **Method** | Signature, docstring, line range, parent class |

### 2.2. Tokenization

Tokens are split with awareness of:
- `camelCase` words (e.g., `getUserName` → `["get", "user", "name"]`)
- `snake_case` words
- Special characters (underscores, dots) as boundaries

This enables fuzzy matching on naming conventions common in different code styles.

### 2.3. Symbol Storage

Each symbol is stored with:
- Qualified name (e.g., `ClassName.method_name`)
- Type (class, function, method)
- Full signature (arguments & types if available)
- Docstring (if present)
- File path & line range
- Parent class (for methods)

---

## 3. Hybrid Search Architecture

### 3.1. Search Method

`search_code(query, top_k=10)` combines three ranking signals:

1. **FTS5 Rank**: Full-text search match quality (term frequency, inverse document frequency, phrase proximity)
2. **TF-IDF Score**: In-memory TF-IDF matrix ranking (term importance within document)
3. **Cosine Similarity**: Dense embeddings (768-dim nomic-embed-text-v1.5) → stored vec_context vectors

### 3.2. Ranking Formula

```
combined_score = fts5_rank(query, doc) 
                 + tfidf_score(query, doc) 
                 + 3.0 × cosine_similarity(query_embedding, doc_embedding)
```

The `3.0` multiplier elevates semantic similarity when syntactic signals are weak.

### 3.3. Return Format

`search_code` returns top-k files with:
- File path
- Symbols and their line ranges
- Match score
- Context snippets (if available)

### 3.4. Integration

Used by MCP `workspace_search_code` tool, accessible to all agents and IDE companions.

---

## 4. Cache Behavior

### 4.1. Warm vs. Cold Index

- **Without `--force`**: `load_index()` returns cached summary instantly (symbols + metadata from JSON).
- **With `--force`**: Full re-index of workspace.

### 4.2. Query Behavior

- FTS5 + vector queries **still work from SQLite** even if TF-IDF/embeddings are cold.
- Lazy loading: Embedding model is loaded only on first search if not already in memory.

### 4.3. Cache Locations

- `.dreamference/context_index.json` — Symbol metadata + statistics (for quick startup)
- `.dreamference/context.db` — SQLite with FTS5 + vec0 tables (persistent query store)
- Model cache — Sentence-transformer checkpoint (`~/.cache/huggingface/`) for embeddings

---

## 5. Performance Notes

### 5.1. Optimization Techniques

1. **ProcessPoolExecutor**: Bypass GIL for multi-file AST extraction
2. **SQLite mmap**: 2 GB memory-mapped region for zero-copy queries on unified memory
3. **Lazy Embedding Loading**: Embedding model only loaded when search is triggered
4. **In-Memory TF-IDF**: Fast term-weighting without repeated SQLite reads

### 5.2. Benchmark

On a typical GB10 system with a 10,000-file Python codebase (100+ MB):

- Initial index: ~10–30 seconds (parallel AST extraction)
- Warm search: <100 ms (FTS5 + cached embeddings)
- Full re-index: ~10–30 seconds

The embedding model (100–300 MB) runs alongside vLLM in shared unified memory.

---

## 6. Tests Architecture

### 6.1. Framework

- **Test Runner**: pytest (invoked via `.venv/bin/python -m pytest tests/ -q`)
- **Coverage**: All major subsystems tested in isolation
- **Mocking**: Hardware, subprocess, and Docker calls are mocked (no GPU, no Docker required)

### 6.2. Test Structure

One `test_*.py` per major subsystem:

| Test File | Scope |
| :-------- | :---- |
| `test_config.py` | `DreamferenceConfig`, env vars, temporary Goose config, cave mode |
| `test_context_engine.py` | Indexing, AST extraction, hybrid search (FTS5 + TF-IDF + embeddings) |
| `test_hardware.py` | GB10 detection, model matrix, download helpers |
| `test_mcp_server.py` | MCP tools and IDE state |
| `test_runner.py` | All agent runners (Goose, Cline, …) and sandbox logic |
| `test_vllm_server.py` | VLLMLaunchOptions, server manager, health checks |

### 6.3. Test Style

- **Lightweight unit tests**: Minimal dependencies, fast feedback
- **tmp_path fixtures**: Filesystem isolation per test
- **No external services**: All hardware/subprocess calls are mocked
- **Green after every change**: Regression tests ensure stability

### 6.4. Running Tests

```bash
# Full suite (~8 seconds, no GPU/Docker required)
.venv/bin/python -m pytest tests/ -q

# Single file
.venv/bin/python -m pytest tests/test_vllm_server.py -q

# Single test
.venv/bin/python -m pytest tests/test_vllm_server.py::test_nvfp4_model_applies_registry_launch_recipe -q
```

---

## See Also

- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — Model indexing & launch
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Agent integration with context
- **[DREAMFERENCE_CODEX.md](./DREAMFERENCE_CODEX.md)** — Codex integration
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md)** — Source code layout
