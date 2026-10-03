# Puffin PDF Reading and Search — Technical Specification

**Status:** proposed, **nothing built** (revised 2026-10-03; draft v1 of 2026-09-28 is in git history). Nothing in `dreamference/`, `puffin-web-rs/` or the Gmail service reads a PDF today. §1 is read from the code on 2026-10-03; every other section is design, and claims not checked on this machine are marked *(unverified)*.
**Target:** `puffin` (the terminal agent) first, the Puffin web UI (Onyx) second.
**Builds on:** `puffin-fetch` / `puffin-search` (`puffin-web-rs/`), the MCP `web_fetch` (`mcp_server/web_tools.py`), the planned Drive app ([PUFFIN_APPS](./DREAMFERENCE_PUFFIN_APPS.md), [GOA](./DREAMFERENCE_GOA.md)), the tool-output budget of [PUFFIN_CONTEXT_BUDGET](./DREAMFERENCE_PUFFIN_CONTEXT_BUDGET.md), `/airgapped` ([PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)), and the Onyx custom-tool pattern of the image-search and Gmail sidecars ([ONYX](./DREAMFERENCE_ONYX.md)).

---

## 1. What changed since draft v1, and what is true today

Draft v1 proposed one thing: an Onyx sidecar (`dreamference-pdf-search`, port 8769) that searches SearXNG for `filetype:pdf`, downloads up to five PDFs, embeds their chunks and returns the top excerpts to the web chat. Four facts move the design:

1. **`puffin` is now the main surface, and it can already find PDFs but not read them.** `puffin-search` returns PDF links like any other. `puffin-fetch` (`puffin-web-rs/src/fetch.rs`, read 2026-10-03) treats any non-HTML body as text: a PDF's bytes are decoded by charset and handed back, up to `DEFAULT_MAX_CHARS` (8,000) characters, so the model receives up to 8,000 characters of compressed-stream noise. That is the cheapest defect to fix and the one the agent actually hits.
2. **MCP tools now reach the local model** (patch `0020`). A PDF tool can be a tool of `puffin` directly; it no longer has to be an Onyx tool to be callable.
3. **PDFs in Drive will be reachable read-only** through the Drive app (PUFFIN_APPS §9). GNOME's OAuth client grants only the **full** `…/auth/drive` scope; the read-only scope is refused (scope test, 2026-10-03, PUFFIN_APPS §11.1). As planned, `drive_read` refuses binary files, which includes every PDF in Drive.
4. **There is no embedding sidecar that runs here.** Infinity has no arm64 image (IMAGE_SEARCH §7), and the diffusion sidecar is switched off (`DIFFUSION_ENABLED = False`). The one embedding model that runs on this machine is the context engine's `nomic-embed-text-v1.5`, pinned to the CPU (CLAUDE.md, "Context engine").

On this host `pdftotext` (poppler-utils 24.02) and PyMuPDF (in `.venv`) are installed; neither can be assumed on a release install or a client machine.

---

## 2. Decision: one extractor, three surfaces, ranking only where there is no paging

| Surface | Where | What it does | Ranking |
|---|---|---|---|
| **A. `puffin-fetch` / `web_fetch`** | `puffin-web-rs` (Rust, in-process) and its Python twin | A PDF URL returns extracted text with page markers, paged like any long page | None: the model pages (`--page`, `--pages a-b`), as it does with `sed -n` ranges |
| **B. Drive `drive_read`** | the Google service (`dreamference-gmail`, Python) | A PDF in Drive returns extracted text, paged the same way, instead of being refused | None |
| **C. Web chat `pdf_search`** | an Onyx custom tool | Draft v1's search-download-rank, for the web UI, which has no paging tool | Embeddings (nomic, CPU) |

**Why no ranking for `puffin`.** The agent already reads by range and searches with `grep`-like tools; the context-budget measurements (PUFFIN_CONTEXT_BUDGET §1.1) show its cost is the *number* of reads, not their size, and that a fixed window (SWE-agent's 100 lines, §2 there) beats whole files. A page-addressed PDF is the same shape. Adding an embedding model to `puffin`'s path would put a 0.5–2 GB process *(estimate, unverified)* beside a resident model for something paging already does.

**Why ranking for Onyx.** Onyx's model cannot page a document it was never given; it calls a tool once and answers. Draft v1's design is right there.

**Order.** A, then B, then C. A is a few hundred lines in one crate and fixes a defect; B rides the Drive app; C is the only part needing a new container and an embedding model.

---

## 3. The extractor

- **`puffin-web-rs`:** a pure-Rust extractor (candidate: the `pdf-extract` crate, or `lopdf` with its text layer *(unverified: quality on real papers and manuals; Phase 0 decides)*), so a release install and a client machine need no poppler. Detection by `Content-Type: application/pdf` **or** the `%PDF-` magic in the first 1 KiB (servers send PDFs as `application/octet-stream`).
- **Python (Google service, Onyx tool, MCP `web_fetch`):** `pypdf` (BSD) rather than PyMuPDF. PyMuPDF is AGPL, which the project's licence would allow, but the service is a single stdlib file in a stock `python:3-slim` container that installs its one dependency at start (the image-search precedent), and `pypdf` is pure Python *(unverified: extraction quality against pdftotext on the Phase 0 set)*.
- **Output shape, shared:** `--- page N of M ---` markers; a header line with title (from the document info), page count, and which pages were returned; whitespace collapsed; ligatures normalised. A PDF with no text layer (a scan) says so in one line instead of returning nothing: `no text layer (scanned?); OCR is not offered`.
- **Paging:** default the first pages up to the existing 8,000-character default; `--page N` / `--pages a-b` select; `--max-chars` as today. Within PUFFIN_CONTEXT_BUDGET's per-output cap (8,000 tokens) by construction.
- **Limits:** the download ceiling rises from 5 MiB to **20 MiB for PDFs only** (a manual or a paper is often 5–15 MB); extraction stops after **10 s** or **500 pages** and says where it stopped; no JavaScript, no rendering, no embedded files, no font program execution (text extraction only).

---

## 4. Surfaces in detail

### 4.1 A: `puffin-fetch` and `web_fetch`

`puffin-fetch <url> [--page N | --pages a-b]`. The `--json` shape gains `pages`, `page_count` and `pdf: true`. The prompt's `WEB_ACCESS_INSTRUCTIONS` gains one sentence: PDFs are returned page by page; ask for the pages you need. The MCP `web_fetch` mirrors it, as `WebTools` mirrors `puffin-fetch` today.

At `/airgapped on` nothing changes: the fetch already refuses the network there.

### 4.2 B: Drive

`drive_read` (PUFFIN_APPS §9) accepts `application/pdf` and returns the extractor's output with the same paging parameters; other binaries stay refused. `drive_search` already matches PDFs through Drive's own `fullText contains`, which indexes PDF text on Google's side, so no local index is needed. Read-only by construction, as the rest of the Drive app; the token carries write permission (PUFFIN_APPS §10). Text from Drive is untrusted and is wrapped in the same boundary the Gmail tools use.

### 4.3 C: web chat `pdf_search`

Draft v1's sidecar, kept in its essentials and corrected:
- **Registration** as the image-search and Gmail tools: an OpenAPI document to `POST /admin/tool/custom`, lookup-then-`PUT`, a shared-secret header, `--no-pdf-search` on `puffin-admin puffin configure`.
- **Search:** SearXNG with `filetype:pdf`; candidates are results whose URL or `Content-Type` is a PDF; at most 5.
- **Download:** the image-search hardened downloader (DNS pinning, loopback and RFC 1918 rejection), 20 MiB, 10 s.
- **Rank:** chunks of ~500 words with 50 words of overlap, embedded with `nomic-embed-text-v1.5` on the CPU with its `search_document:` / `search_query:` prefixes (the context engine's settings), cosine similarity, top 8 chunks with URL and page.
- **Network:** created on `dreamference-sidecars` and joined to Onyx's network, never the default bridge (DOCKER §6).
- **Memory budget:** container capped at **2 GiB** (`--memory=2g`, swap equal), the model loaded once and kept; one request at a time. The cap is a safety property, not a tuning knob: the host keeps ~38.7 GB available while the default model serves (CLAUDE.md), and earlyoom acts at ~6 GB, so a 2 GiB tool cannot by itself cross that line. Peak to be measured in Phase 3 *(unverified: nomic on CPU in torch is ~0.55 GB of weights plus runtime; the context engine measured ~2 GB peak while indexing 223 files, which is the worst case, not a query)*.

---

## 5. Security

PDFs are attacker-controlled. Three rules apply on every surface:
1. **Text only.** No rendering, no JavaScript, no embedded files or attachments, no external references resolved.
2. **Bounded.** Size, time and page limits of §3; a parser exception returns an error line, never a partial stack trace.
3. **Untrusted text.** Output is marked as document content (the Gmail tools' boundary), so a PDF's "ignore your instructions" is data. `puffin` already sits behind its command sandbox; the web chat has no shell.

The SSRF guard of §4.3 applies to C only: A runs from the user's own shell context and already reaches what the user's machine reaches, as `puffin-fetch` does today.

---

## 6. Phases and acceptance

| Phase | Builds | Done when (measured) |
|---|---|---|
| **0** | Nothing. A fixed set of 20 public PDFs (papers, manuals, a scan, a 300-page spec, one `octet-stream` server) | For each candidate extractor (Rust candidate, `pypdf`, `pdftotext` as reference): characters per page, a word-level match against `pdftotext` ≥ 0.95 on the text-layer PDFs, time per page, peak RSS; the scan reported as scan. The Rust and Python choices are made from this table |
| **1 (A)** | PDF support in `puffin-fetch` and `web_fetch` | Unit tests on fixture PDFs (paging, markers, magic sniffing, limits, scan line); `puffin-fetch` of the 20 URLs returns no binary noise; a `puffin exec` task "find the default port in <vendor> manual PDF" answers from the right page, with tool output under 8,000 tokens per call |
| **2 (B)** | `drive_read` for PDFs | Rides PUFFIN_APPS Phase 2; a PDF in the test account's Drive (and one in a shared drive) is read by page; binary non-PDFs still refused |
| **3 (C)** | The Onyx `pdf_search` sidecar | Registered by `configure`; on 10 questions with known answers in public PDFs, the cited page contains the answer in ≥ 8; container peak under its 2 GiB cap with the default model serving; earlyoom silent |

## 7. Tests

Offline, as the rest of the suite: fixture PDFs checked into `tests/fixtures/pdf/` (generated, a few KB each), no network; conftest's rules apply (no real `docker`, no home writes). The Rust crate's tests run with `cargo test --locked` in `puffin-web-rs/`.

## 8. Open questions

- OCR for scans: out of scope here; the obvious candidate (Tesseract) is CPU-heavy and a second dependency.
- Whether Onyx Lite's own file upload already extracts PDFs for chat attachments *(unverified)*, which would make C matter only for web search.
