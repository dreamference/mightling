# Dreamference Code Indexing & Context Engine

> **Version:** 1.2.0
> **Subject:** AST Extraction, Hybrid Search, SQLite/FTS5 Indexing, Testing
> **Checked against the code:** 2026-09-28 (`dreamference/context_engine/`)

---

## Table of Contents

- [1. Code Indexing Pipeline](#1-code-indexing-pipeline)
- [2. AST Symbol Extraction](#2-ast-symbol-extraction)
- [3. Hybrid Search](#3-hybrid-search)
- [4. Cache Behavior](#4-cache-behavior)
- [5. Known Gaps](#5-known-gaps)
- [6. Tests Architecture](#6-tests-architecture)

**Who uses this index:**
- the MCP tool `workspace_search_code` (`puffin-admin mcp`, for JetBrains and VS Code);
- the web canvas (`puffin-admin web`);
- `status`, which shows the counts.

The `puffin` agent does **not** use it: it searches with `rg`/`ast-grep` through its shell. A code index for `puffin` is proposed separately in `DREAMFERENCE_PUFFIN_CODE_INDEX.md`.

---

## 1. Code Indexing Pipeline

### 1.1. Entry Points

- **`puffin-admin init`:** always forces a full re-index (`ContextEngine().index_workspace(force_reindex=True)`) of the current directory.
- **`puffin-admin index [--dir PATH] [--force]`:** indexes `--dir`, or the current directory. Without `--force`, an existing index is reused (§4).

### 1.2. Steps (`index_workspace`)

1. **Discover files:** `os.walk` from the workspace root.
   - It skips `IGNORE_DIRS`: `.git`, `.svn`, `.hg`, `__pycache__`, `.venv`, `venv`, `node_modules`, `.idea`, `.vscode`, `build`, `dist`, `.dreamference`.
   - It skips `IGNORE_EXTENSIONS`: `.pyc .pyo .so .o .a .exe .dll .dylib .png .jpg .jpeg .gif .ico .pdf .zip .tar .gz`.
   - Every other file is read as UTF-8, with errors ignored. That includes other binaries and very large files, because there is no size cap.
2. **Reset the database:** it clears `files`, `symbols` and `fts_context`.
3. **Parse in parallel:** `ProcessPoolExecutor(max_workers=min(32, cpu_count*2))`. Each worker reads the file, tokenizes it (`TFIDFCalculator.tokenize`), and extracts symbols from **`.py` files only** (`ASTSymbolExtractor`).
4. **Persist to `.dreamference/context.db`:**
   - `files(rel_path PRIMARY KEY, abs_path, size_bytes)`;
   - `symbols(name, symbol_type, file_path, line_start, line_end, signature, docstring)`;
   - `fts_context`, an FTS5 table with `rel_path UNINDEXED, content` (the full file text);
   - every connection sets `PRAGMA mmap_size = 2147483648` (2 GB).
5. **TF-IDF:** `TFIDFCalculator.compute_matrix` builds an in-memory IDF table and per-token TF-IDF map.
6. **Embeddings:** `EmbeddingCalculator.compute_embeddings` encodes **each whole file** with sentence-transformers `nomic-ai/nomic-embed-text-v1.5` (`trust_remote_code=True`, normalized, 768-dim), one vector per file. The vectors are kept in memory and also written to `vec_context`, a sqlite-vec `vec0(embedding float[768])` table, with `rowid = abs(hash(rel_path)) % 2**63`. Python's `hash()` of a string is randomised per process, so these ids are not stable, and each re-index adds new rows rather than replacing the old ones. The table is not queried (§5), so this only costs disk.
7. **Save** `.dreamference/context_index.json`, holding the symbols and file metadata only.

---

## 2. AST Symbol Extraction

`ASTSymbolExtractor.extract_python_ast` walks the whole `ast` tree of each `.py` file:

| `symbol_type` | From | `signature` |
| :--- | :--- | :--- |
| `class` | `ast.ClassDef` | `class Name` (no bases) |
| `function` | `ast.FunctionDef`, `ast.AsyncFunctionDef`, including methods | `def name(arg1, arg2)`: positional argument names only, no annotations, defaults or return type |

Each symbol records `name` (unqualified), `file_path`, `line_start`, `line_end` and `docstring`. There is no parent class and no separate method type: methods are `function`s. Files that don't parse yield no symbols, silently. Other languages yield no symbols, but are still tokenized, full-text indexed and embedded.

**Tokenization** (`TFIDFCalculator.tokenize`): it takes `[A-Za-z0-9_]+` runs, splits camelCase (`getUserName` → `get`, `user`, `name`) and snake_case, and lowercases.

---

## 3. Hybrid Search

`search_code(query, top_k=5)`:

1. **FTS5:** the query tokens are joined with `OR` and matched against `fts_context`, taking up to `2 × top_k` rows. Each row adds `2 / (|rank| + 1)` to its file's score.
2. **TF-IDF:** for each query token, each file's TF-IDF weight is added.
3. **Dense:** the query is embedded, and every in-memory file vector adds `3.0 × max(0, cosine)`.
4. The top `top_k` files are returned, sorted by the combined score.

**Result:** a list of `{"rel_path", "score", "symbols": [all symbols in the file], "size_bytes"}`. There are no snippets or matched line ranges: the caller reads the file.

Nomic's `search_document:` / `search_query:` task prefixes are **not** applied at either stage, and nomic's retrieval quality is lower without them.

---

## 4. Cache Behavior

- **Without `--force`:** if `context_index.json` exists, `load_index()` restores the symbols and file metadata and returns the summary immediately. No files are re-read.
- **With `--force`**, or when there is no JSON: a full re-index.
- **Locations:**
  - `.dreamference/context_index.json`;
  - `.dreamference/context.db`;
  - the nomic model in the HF cache (`~/.cache/huggingface/hub/models--nomic-ai--nomic-embed-text-v1.5`).
- **Loading the model:** it is loaded lazily, on the first embed. At indexing time that is the first index run; at search time it is the first query.

---

## 5. Known Gaps

These are recorded here because the spec used to promise otherwise.

- **Search after a cached load is FTS5-only.** `load_index()` restores neither the TF-IDF tables nor the file embeddings, so in a fresh process (every `puffin-admin mcp` session) steps 2 and 3 of §3 contribute nothing. The `vec_context` table is written but never queried: dense search reads only the in-memory vectors from the current indexing run.
- **Not air-gapped by default.** Nothing pre-downloads the nomic model. `init` downloads only the LLM weights, so the first embedding triggers a Hugging Face download. On an offline machine without the model cached, `SentenceTransformer(…)` raises, and indexing (and the first search) fails. Only when `sentence-transformers` is not installed at all does `embed_texts` fall back to zero vectors, which silently disables the dense signal. Pre-fetch the model while online.
- **Symbols are Python only.** Rust, TypeScript and the rest get full-text and embedding coverage, but no symbols.

---

## 6. Tests Architecture

- **Runner:** `.venv/bin/python -m pytest tests/ -q`. No GPU, Docker or model server is needed; hardware, subprocess and Docker calls are mocked. On 2026-09-28: 317 passed and 63 skipped in about 30 s. The skips are the live slash-command tests, which need a running vLLM.
- **Style:** `tmp_path` isolation, and no external services except in tests that detect them and skip.

| Test File | Scope |
| :-------- | :---- |
| `test_config.py` | `DreamferenceConfig`, env vars, Goose config, cave mode |
| `test_context_engine.py` | Indexing and search |
| `test_hardware.py` | GB10 detection, model matrix, downloads |
| `test_vllm_server.py` | Launch command and recipe layering, host safety, compile-cache reset |
| `test_diffusion_server.py` | Diffusion sidecar |
| `test_runner.py` | Agent runners, sandbox logic, the Codex hand-off |
| `test_codex_branded_builder.py` | `puffin` build: patches apply, submodule untouched, build key, size limit |
| `test_puffin_slash_commands.py` | Every `puffin` slash command and subcommand on a pseudo-terminal (live cases skip without vLLM) |
| `test_cli_entry_points.py` | `puffin-admin` entry point, no Python `puffin`, strict parsing |
| `test_mcp_server.py` | MCP tools and IDE state |
| `test_onyx_runner.py` | Onyx deployment, configure, branding, UI patches, Gmail service |
| `test_onyx_ui_scripts.py` | Injected UI JavaScript, run under node against a stub DOM |
| `test_gmail_client.py` | `puffin-admin gmail` client |
| `test_image_search_service.py` | Image search sidecar |
| `test_desktop.py` | Desktop window (`puffin-app`) install and launch |

The Rust launcher has its own unit tests, run in the build export with `cargo test --release -p puffin-launcher` (`DREAMFERENCE_PUFFIN_CODEX.md`).

---

## See Also

- **[DREAMFERENCE_PUFFIN_CODE_INDEX.md](./DREAMFERENCE_PUFFIN_CODE_INDEX.md):** proposed code index for `puffin`
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent integration
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md):** source layout
