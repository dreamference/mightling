# The local file index, `ling-docs`

Developer notes behind the local-file-index line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md`, whose §16 records what Phase 1 built and §17 how it ships.

The local file index is `ling-docs`, a Rust binary of its own (`ling-docs-rs/`, own lockfile).

- It indexes the user's own folders ("collections": `~/Documents` and `~/Downloads` by default, `ling docs add|remove|search|read|status`) into one SQLite file per collection under `$CODEX_HOME/docs/`.
- Search is FTS5 (plus a trigram table for Chinese and Japanese) and exact dense vectors from `snowflake-arctic-embed-m-v2.0` int8, fused by weighted RRF.
- It is offered to the model as `docs_search`/`docs_read` (MCP server `ling_docs`, registered by `ling-rs/src/docs_index.rs` when a collection exists or PDFs can be read).
- `pdf_search` (`src/pdf_search.rs`, `specs/DREAMFERENCE_PDF_SEARCH.md` §3) searches one PDF outside the collections: a URL (downloaded by `ling-fetch --download`), or a file under the session's working directory. The PDF becomes a one-document collection `pdf-<12 hex>` in `$CODEX_HOME/docs/pdf/`, built by `index::index_file` through the same extractor scope, admission and index worker; `docs_read` reads its `pdf-…:1` ids. `ling-docs extract` prints a file's pages, under `RLIMIT_AS` where no user systemd is reachable (inside the agent's sandbox).

## No network, bounded memory

- It opens no socket: PDFium and ONNX Runtime are loaded with dlopen and, with the model (~320 MB in all), installed pinned by `ling-admin docs setup` (run by `codex build` and by `install.sh` on a node); `ling-admin audit egress --docs` proves it.
- Indexing runs only outside the sandbox, in `mightling-index-docs-*` scopes of `mightling-index.slice` admitted against the code index's own ledger (copied `host.rs`), inside bwrap with no network.
- Extraction is capped at 1 GiB with `OOMPolicy=continue`, so an over-cap PDF reads as killed, not as a stop from outside.
- Linux only.

## Tests

Its tests run with `cargo test --locked` in `ling-docs-rs/` (PDF and model cases only when `MIGHTLING_DOCS_LIB_DIR`/`MIGHTLING_DOCS_MODEL_DIR` are set), and **never with the real `HOME`**: default collections would index the real `~/Downloads`. The `pdf_search` cases run `ling-docs mcp` with `MIGHTLING_DOCS_TEST_HOST=direct` (runs as plain children under `RLIMIT_AS`, no systemd), `MIGHTLING_DOCS_TEST_EMBEDDER=hash` and a stand-in `ling-fetch` (`MIGHTLING_FETCH_BIN`).

`ling-docs-rs/eval/` is the evaluation corpus, whose Wikipedia puffin articles are the bird: `scripts/rename_mightling.py` leaves that folder alone.
