# The local file index, `ling-docs`

Developer notes behind the local-file-index line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md`, whose §16 records what Phase 1 built and §17 how it ships.

The local file index is `ling-docs`, a Rust binary of its own (`ling-docs-rs/`, own lockfile).

- It indexes the user's own folders ("collections": `~/Documents` and `~/Downloads` by default, `ling docs add|remove|search|read|status`) into one SQLite file per collection under `$CODEX_HOME/docs/`.
- Search is FTS5 (plus a trigram table for Chinese and Japanese) and exact dense vectors from `snowflake-arctic-embed-m-v2.0` int8, fused by weighted RRF.
- It is offered to the model as `docs_search`/`docs_read` (MCP server `ling_docs`, registered by `ling-rs/src/docs_index.rs` when a collection exists).

## No network, bounded memory

- It opens no socket: PDFium and ONNX Runtime are loaded with dlopen and, with the model (~320 MB in all), installed pinned by `ling-admin docs setup` (run by `codex build` and by `install.sh` on a node); `ling-admin audit egress --docs` proves it.
- Indexing runs only outside the sandbox, in `mightling-index-docs-*` scopes of `mightling-index.slice` admitted against the code index's own ledger (copied `host.rs`), inside bwrap with no network.
- Extraction is capped at 1 GiB with `OOMPolicy=continue`, so an over-cap PDF reads as killed, not as a stop from outside.
- Linux only.

## Tests

Its tests run with `cargo test --locked` in `ling-docs-rs/` (PDF and model cases only when `MIGHTLING_DOCS_LIB_DIR`/`MIGHTLING_DOCS_MODEL_DIR` are set), and **never with the real `HOME`**: default collections would index the real `~/Downloads`.

`ling-docs-rs/eval/` is the evaluation corpus, whose Wikipedia puffin articles are the bird: `scripts/rename_mightling.py` leaves that folder alone.
