# Mightling Code Indexing & Context Engine

> **Version:** 1.2.0
> **Subject:** AST Extraction, Hybrid Search, SQLite/FTS5 Indexing, Testing
> **Checked against the code:** 2026-09-29 (`dreamference/context_engine/`); §6's test table against `tests/` on 2026-10-02 (all 28 test files are listed)

---

## Table of Contents

- [1. Code Indexing Pipeline](#1-code-indexing-pipeline)
- [2. AST Symbol Extraction](#2-ast-symbol-extraction)
- [3. Hybrid Search](#3-hybrid-search)
- [4. Cache Behavior](#4-cache-behavior)
- [5. Known Gaps](#5-known-gaps)
- [6. Tests Architecture](#6-tests-architecture)

**Who uses this index:**
- the MCP tool `workspace_search_code` (`ling-admin mcp`, for JetBrains and VS Code);
- the web canvas (`ling-admin web`);
- `status`, which shows the counts.

The `ling` agent does **not** use it: it searches with `rg`/`ast-grep` through its shell. A code index for `ling` is proposed separately in `DREAMFERENCE_MIGHTLING_CODE_INDEX.md`.

---

## 1. Code Indexing Pipeline

### 1.1. Entry Points

- **`ling-admin init`:** always forces a full re-index (`ContextEngine().index_workspace(force_reindex=True)`) of the current directory.
- **`ling-admin index [--dir PATH] [--force]`:** indexes `--dir`, or the current directory. Without `--force`, an existing index is reused (§4).

### 1.2. Steps (`index_workspace`)

1. **Discover files** (`collect_files`):
   - **In a git repository:** `git ls-files -z --cached --others --exclude-standard`, i.e. tracked files plus untracked files git does not ignore. Every `.gitignore` applies, nested ones included.
   - **Submodules are not entered.** They are someone else's code (`codex/` alone is ~8,700 files), and git lists each one as a single directory entry, which the file check drops.
   - **Outside a repository, or without git:** `os.walk` from the workspace root.
   - **Both paths skip `IGNORE_DIRS`:** `.git`, `.svn`, `.hg`, `__pycache__`, `.venv`, `venv`, `node_modules`, `.idea`, `.vscode`, `build`, `dist`, `.dreamference`, `target`, `.cargo`.
   - **Both paths skip `IGNORE_EXTENSIONS`:** `.pyc .pyo .so .o .a .exe .dll .dylib .png .jpg .jpeg .gif .ico .pdf .zip .tar .gz`.
   - **Symlinks are skipped.**
   - **A file is skipped if it is larger than `MAX_FILE_BYTES` (1 MiB), or has a NUL byte in its first 8 KiB (binary).** Everything else is decoded as UTF-8, with errors ignored.
   - **Why:** until 2026-09-29 discovery was the walk alone, with no size cap. On this repository it collected 11,565 files and 2.3 GB, 2.2 GB of it a Tauri `target/`, against 223 files and 2.4 MB now. Holding that beside a resident vLLM pushed the host under earlyoom's 5% line, and earlyoom killed vLLM's EngineCore. `ling-admin mcp` indexes on its first query, so any workspace with a Rust build tree was exposed.
2. **Reset the database:** it clears `files`, `symbols` and `fts_context`.
3. **Parse in parallel:** `ProcessPoolExecutor(max_workers=max(1, min(8, cpu_count, files // 32 + 1)))`. The pool is sized to the work, because each worker is a fork of a parent that has imported torch. Results are consumed as they arrive, inside the pool's `with`. Each worker reads the file, tokenizes it (`TFIDFCalculator.tokenize`), and extracts symbols from **`.py` files only** (`ASTSymbolExtractor`).
4. **Persist to `.dreamference/context.db`:**
   - `files(rel_path PRIMARY KEY, abs_path, size_bytes)`;
   - `symbols(name, symbol_type, file_path, line_start, line_end, signature, docstring)`;
   - `fts_context`, an FTS5 table with `rel_path UNINDEXED, content` (the full file text);
   - every connection sets `PRAGMA mmap_size = 2147483648` (2 GB).
5. **TF-IDF:** `TFIDFCalculator.compute_matrix` builds an IDF table and a per-token TF-IDF map. It uses one `Counter` per document; until 2026-09-29 it scanned every document's token list once per token, which is quadratic.
6. **Embeddings:** `EmbeddingCalculator.compute_embeddings` encodes each file with sentence-transformers `nomic-ai/nomic-embed-text-v1.5`, one vector per file.
   - The model is loaded with `trust_remote_code=True`; vectors are normalised and 768-dim.
   - **Input:** each text is prefixed `search_document: ` and truncated to its first `MAX_SEQUENCE_TOKENS` (1,024) tokens. Texts are encoded in batches of 8.
   - **Device:** the model runs on the **CPU** (`EMBEDDING_DEVICE`). With vLLM resident, CUDA refuses even this model (out of memory, although the host has gigabytes free), and a failed attempt leaves a CUDA context in unified memory.
   - **Memory:** at nomic's full 8,192 tokens and a batch of 32, attention memory alone runs to gigabytes. The vectors are kept in memory and written to `embeddings(rel_path PRIMARY KEY, vector BLOB)` as float32 bytes, replacing the previous run's rows. A plain table, not sqlite-vec: search scores the vectors in Python, so nothing needs the extension. If the model cannot be loaded, this step stores nothing and indexing continues (§5).
7. **Save** `.dreamference/context_index.json`, holding the symbols, the file metadata and the TF-IDF tables (`idf_table`, `tf_idf_index`).

**Memory budget.** Beside a resident vLLM the host has about 10–13 GB available, and earlyoom sends SIGTERM at 5% (about 6 GB). An index run therefore has roughly 4 GB to work with. Measured on this repository with vLLM serving:
- 223 files in 1 min 47 s;
- available memory never below 10.8 GB;
- peak RSS 2.1 GB;
- no earlyoom action.

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
3. **Dense:** the query is embedded with the `search_query: ` prefix, and every file vector adds `3.0 × max(0, cosine)`. Skipped when there are no file vectors or no model.
4. The top `top_k` files are returned, sorted by the combined score.

**Result:** a list of `{"rel_path", "score", "symbols": [all symbols in the file], "size_bytes"}`. There are no snippets or matched line ranges: the caller reads the file.

Nomic was trained with those task prefixes and retrieves noticeably worse without them. Until 2026-09-29 neither was applied, and no embedding was computed at all: step 6 iterated a one-shot `executor.map` result a second time and got nothing, and the sqlite-vec extension was never loaded, so every `vec_context` write failed silently.

---

## 4. Cache Behavior

- **Without `--force`:** if `context_index.json` exists, `load_index()` restores the symbols, the file metadata and the TF-IDF tables, reads the file vectors back from `embeddings`, and returns the summary immediately. No files are re-read.
- **A JSON without `tf_idf_index`** was written before the tables were saved, and is treated as absent: it is rebuilt once.
- **With `--force`**, or when there is no JSON: a full re-index.
- **Locations:**
  - `.dreamference/context_index.json`;
  - `.dreamference/context.db`;
  - the nomic model in the HF cache (`~/.cache/huggingface/hub/models--nomic-ai--nomic-embed-text-v1.5`).
- **Loading the model:** it is loaded lazily, on the first embed. At indexing time that is the first index run; at search time it is the first query.

---

## 5. Known Gaps

These are recorded here because the spec used to promise otherwise.

- **Dense search sees only a file's head.** Embeddings cover a file's first 1,024 tokens, so a definition deep in a long file is found by FTS5 and TF-IDF but not by meaning. Chunking files into several vectors would close this.
- **Not air-gapped by default.** Nothing pre-downloads the nomic model. `init` downloads only the LLM weights, so the first embedding triggers a Hugging Face download. On an offline machine without the model cached, loading fails once, `⚠️ Embedding model … is unavailable` is printed, and indexing and search carry on by keyword only (FTS5 and TF-IDF) for the rest of the process. The same happens silently when `sentence-transformers` is not installed. Pre-fetch the model while online to get the dense signal. (Until 2026-09-29 a failed load aborted indexing, and a missing library produced zero vectors that scored every file equally.)
- **Symbols are Python only.** Rust, TypeScript and the rest get full-text and embedding coverage, but no symbols.

---

## 6. Tests Architecture

- **Runner:** `.venv/bin/python -m pytest tests/ -q`. No GPU, Docker or model server is needed; hardware, subprocess and Docker calls are mocked. On 2026-10-02 pytest collects 655 tests (at `cebd6db`). On 2026-10-01, at 521, 63 were skipped without a model server and the rest took about 25 s; neither figure was re-measured for 655. The skips are the live slash-command tests, which need a running model server; with one, the full run takes about 13 minutes.
- **Style:** `tmp_path` isolation, and no external services except in tests that detect them and skip.

| Test File | Scope |
| :-------- | :---- |
| `test_config.py` | `DreamferenceConfig`: four-tier resolution, model pinning, tool-call parser |
| `test_context_engine.py` | Indexing and search |
| `test_hardware.py` | GB10 detection, model matrix, downloads |
| `test_vllm_server.py` | Launch command and recipe layering, host safety, compile-cache reset |
| `test_diffusion_server.py` | Diffusion sidecar |
| `test_runner.py` | Agent runners, the Codex hand-off |
| `test_codex_branded_builder.py` | `ling` build: patches apply, submodule untouched, build key, size limit |
| `test_mightling_slash_commands.py` | Every `ling` slash command and subcommand on a pseudo-terminal (live cases skip without vLLM) |
| `test_cli_entry_points.py` | `ling-admin` entry point, no Python `ling`, strict parsing |
| `test_mcp_server.py` | MCP tools and IDE state |
| `test_onyx_runner.py` | Onyx deployment, configure, branding, UI patches, Gmail service |
| `test_onyx_ui_scripts.py` | Injected UI JavaScript, run under node against a stub DOM |
| `test_gmail_client.py` | `ling-admin gmail` client |
| `test_image_search_service.py` | Image search sidecar |
| `test_desktop.py` | Desktop window (`ling-app`) install and launch |
| `test_codex_test_runner.py` | `ling-admin codex test`: Codex's own tests on the patched export, the skip list |
| `test_mightling_privacy.py` | No usage analytics or the upstream vendor channels from `ling` |
| `test_web_commands.py` | `ling-search` and `ling-fetch` are Rust binaries, not console scripts |
| `test_cave_mode.py` | Cave mode's Python side: the setting, and the level texts matching the benchmark's |
| `test_code_index.py` | `ling-admin code setup`, and `server start` stopping index runs before a load |
| `test_night_shift.py` | Night Shift's runner, with a scripted stand-in for `ling` |
| `test_swe_bench.py` | `ling-admin swe-bench`, with a stand-in for `docker` and a scripted `ling exec` |
| `test_egress_audit.py` | `ling-admin audit egress`: the strace parser and the verdict, on a recorded trace |
| `test_airgapped.py` | `/airgapped`: the Python side of the setting, and the default level and the resolver that Python, the launcher and the web commands share |
| `test_node.py` | The node half of the client/server split: the service file, the node id, the published addresses; nothing writes `/etc` or runs `sudo` |
| `test_sidecar_network.py` | Sidecars are created on a user-defined network, never Docker's default bridge |
| `test_cache_clearing.py` | `ling-admin clear` removes weights only |
| `test_admin_reference.py` | `docs/admin.md` is generated from the CLI and in step with it |

The Rust launcher has its own unit tests, run in the build export with `cargo test --release -p ling-launcher` (`DREAMFERENCE_MIGHTLING_CODEX.md`); `ling-web-rs/` and `ling-code-rs/` are tested with `cargo test --locked` in their own directories.

---

## See Also

- **[DREAMFERENCE_MIGHTLING_CODE_INDEX.md](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md):** the code index for `ling` (`ling-code`, implemented 2026-10-01)
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent integration
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md):** source layout
