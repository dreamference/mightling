# The context engine

Developer notes behind the context-engine line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_CONTEXT.md`.

The context engine (`dreamference/context_engine/`, used by `ling-admin index` and the MCP server `ling-admin mcp`) writes two artifacts into a gitignored `.dreamference/` at the workspace root:

- `context_index.json`: AST symbols + TF-IDF;
- `context.db`: SQLite, FTS5 plus an `embeddings(rel_path, vector BLOB)` table.

Both are rebuilt by `ling-admin index --force`.

## Dense search, fixed on 2026-09-29

Until 2026-09-29 dense search never worked: the embedding step re-read a one-shot `executor.map` iterator (so nothing was embedded), wrote to a sqlite-vec `vec0` table the unloaded extension could not create (every failure swallowed), keyed rows by `hash()` (randomised per process), and `load_index` never read them. Vectors are now plain float32 blobs scored in Python, reloaded by `load_index`, embedded with nomic's `search_document:`/`search_query:` prefixes, and an unavailable model means keyword-only search, not zero vectors.

## Memory is a safety property

**Indexing runs beside a resident vLLM, whose headroom is ~4 GB before earlyoom's 5% line, so its memory is a safety property, not a performance one.** Until 2026-09-29 it walked everything outside a short ignore list (11,565 files, 2.3 GB here, mostly a Tauri `target/` and the codex submodule) and earlyoom killed vLLM mid-index; `ling-admin mcp` indexes on first query, so any Rust workspace could do it.

Now:

- `git ls-files` discovery (not entering submodules);
- `target/` ignored;
- a 1 MiB cap and a NUL sniff;
- a pool sized to the work;
- the embedding model **pinned to the CPU** at 1,024 tokens (on CUDA it fails with out-of-memory while vLLM holds the GPU, and a failed attempt still leaves a CUDA context).

Measured with vLLM serving: 223 files, ~2 GB peak, earlyoom untouched.

Night Shift's `index` lock also applies: `index` refuses while a night run holds its lock ([night-shift.md](night-shift.md)).
