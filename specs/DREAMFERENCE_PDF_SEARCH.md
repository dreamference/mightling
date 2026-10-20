# Mightling PDF Reading and Search — Technical Specification

**Status:** Phase 1 (D, §3) and `ling-docs extract` (§4) **built and merged to main on 2026-10-13 after its live check** (§10); A and B proposed (revised 2026-10-03, reworked 2026-10-12 around `ling-docs`' PDF reader, and again on 2026-10-12 around the owner's decision of §2: `ling` gets a tool that searches inside a PDF it has not indexed; draft v1 of 2026-09-28 is in git history). PDFs already in a collection are covered since 2026-10-08 by `ling-docs` ([MIGHTLING_LOCAL_INDEX](./DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) Phase 1, PDFium with a reading-order pass); this document is about PDFs the agent meets during a task: a link on the web, a file in Drive, a file in the workspace or an Ask thread's attachment. §1 is read from the code on 2026-10-12; every other section is design, and claims not checked on this machine are marked *(unverified)*.
**Target:** `ling` (the terminal agent, and the Ask threads that run it). The Onyx web chat gets no PDF tool (§2, §5.3); it is retired in 1.7.0 ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) §19).
**Builds on:** `ling-docs` (`ling-docs-rs/`: its extractor, sandbox and memory admission, chunker, embedder, keyword tables and fusion: LOCAL_INDEX §7, §15.2, §16.3), its MCP server (`ling-docs mcp`, registered by `ling-rs/src/docs_index.rs`), `ling-fetch` (`ling-web-rs/`), the Drive app ([MIGHTLING_APPS](./DREAMFERENCE_MIGHTLING_APPS.md) §6, §9; [GOA](./DREAMFERENCE_GOA.md)), the tool-output budget of [MIGHTLING_CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md), and `/airgapped` ([MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)).

---

## 1. What changed since draft v1, and what is true today

Draft v1 proposed one thing: an Onyx sidecar (`dreamference-pdf-search`, port 8769) that searches SearXNG for `filetype:pdf`, downloads up to five PDFs, extracts them with PyMuPDF, embeds their chunks and returns the top excerpts to the web chat. The 2026-10-03 revision replaced it with "one new extractor (`pdf-extract`/`lopdf` in Rust, `pypdf` in Python), three surfaces". Since then a PDF reader and a search stack have been built and shipped, which moves the design again:

1. **Mightling already has a PDF reader, and a good one.** `ling-docs` reads PDFs through PDFium (`pdfium-render`, the pdfium-binaries build loaded at run time from `MIGHTLING_DOCS_LIB_DIR` or `../lib/ling-docs`), with the reading-order pass and the bidi step of LOCAL_INDEX §15.2, which matched the PyMuPDF reference everywhere and beat it on reading order. Its output is already page-located (`Unit { loc: "p.7", page: 7, … }`), and a page with no text layer is counted as needing OCR. It runs in the extractor sandbox of LOCAL_INDEX §16.3: bwrap with no network, a `systemd-run --user` scope capped at 1 GiB with `OOMPolicy=continue`, a per-file time limit, and the hostile set (a flate bomb, two million text operators, an encrypted and a truncated file) each ending in a one-line `failed: …`. A second extractor would duplicate all of that, with a weaker parser. **So nothing new extracts PDFs: every surface below hands the file to `ling-docs`.**
2. **Mightling already has the search, too.** The same crate chunks (512 tokens, 15 % overlap), embeds (`snowflake-arctic-embed-m-v2.0` int8 on the CPU, 8–13 chunks/s on four cores, LOCAL_INDEX §15.3), keeps FTS5 tables (`porter unicode61` plus a trigram table for Chinese and Japanese) and fuses keyword and dense rankings by weighted RRF. A database of one document is a collection like any other to that code.
3. **`ling` can find PDFs on the web but not read them.** `ling-search` returns PDF links like any other. `ling-fetch` (`ling-web-rs/src/fetch.rs`, read 2026-10-12) decodes any non-HTML body by charset, up to `MAX_DOWNLOAD_BYTES` (5 MiB) and `DEFAULT_MAX_CHARS` (8,000) characters, so the model receives up to 8,000 characters of compressed-stream noise; `ling-search --read` fetches through the same code and has the same defect.
4. **The Drive app is built** (APPS §14.4, a preview in 1.4.0): `drive_search` and `drive_read` are served by `ling apps serve drive` (Rust, in the launcher) over the Google service (`dreamference-gmail`, Python, `chat/google_workspace_reader.py`). `drive_read` refuses binary files with their type, PDFs included (APPS §9: "PDF_SEARCH is where text extraction belongs").
5. **The web UI is moving to Ask threads on `ling`** ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md)): a question in the new UI is answered by the same agent, with the same tools, and an attachment is written into the thread's own folder, which is the session's working directory (ASK §3.3). Onyx stays until the new UI matches it (decided 2026-10-07); Phase C retires it in 1.7.0 (ASK §10, §19).

---

## 2. Decisions

**2026-10-12, the owner: `ling` needs PDF search.** Not Onyx: the agent itself gets a tool that searches inside a PDF it has **not** indexed in a collection (a PDF from a URL, a Drive file, a local path or an Ask attachment) and returns the most relevant passages with their page numbers for a query, so it can cite them in the shape `docs_search`/`docs_read` already use. Long PDFs (hundreds of pages: scientific papers, contracts, manuals) are the use case: read page by page through `web_fetch`/`ling-fetch`, a 300-page document is 100+ calls of 8,000 characters, and the context fills long before the answer is found. This reverses the 2026-10-12 morning revision's "no ranking" (its argument held for a document the model reads in a few pages; it fails for one it must search). **The Onyx PDF tool stays dropped** (§5.3).

| Surface | Where the bytes come from | Who calls the reader | Ranking |
|---|---|---|---|
| **D. `pdf_search` (the agent's tool, §3)** | a URL through `ling-fetch`; a Drive file through the Drive app; a local file in the session's workspace | `ling-docs mcp` | **Yes:** keyword and meaning, fused, over an ephemeral index of that one PDF |
| **A. `ling-fetch`, `ling-search --read`, `web_fetch`** | the web, through `ling-fetch`'s existing download (redirects, proxy rules, `/airgapped`) | `ling-fetch` runs `ling-docs extract` (§4) | None: the model pages (`--page`, `--pages a-b`) |
| **B. Drive `drive_read`** | the Google service, `files.get?alt=media` | `ling apps serve drive` runs `ling-docs extract` | None |
| **C. Onyx `pdf_search`** | — | — | Not planned (§5.3) |

**One reader, one search stack.** Everything about PDFs (parsing, reading order, the scan check, limits, isolation, memory admission) and everything about ranking (chunking, embedding, keyword tables, fusion, merging adjacent chunks, the untrusted wrapping, the output budget) stays in `ling-docs-rs/`, tested once. D adds a source resolver and a cache in front of that code, nothing beside it.

**Order.** D (local paths and URLs), with `ling-docs extract` (§4) built in the same step because D's extraction and the command share one entry point; then A (which first fixes the decoded-bytes defect with a one-line notice, §5.1); then Drive for D and B together, since both need the service's download (§3.3.2, §5.2).

---

## 3. D: `pdf_search`, the agent's PDF search

### 3.1 The tools

Two tools of the existing `ling-docs mcp` server, both read-only (`readOnlyHint`), so Codex runs them without asking and `exec` (approval `never`) allows them:

```text
pdf_search {
  source: string   # "https://…/paper.pdf" | "drive:<file id>" | "./specs/contract.pdf" | "/abs/path.pdf"
  query:  string   # what to find, in the document's language if it can be guessed
  k?:     integer  # passages, default 6, at most 12
}
docs_read {        # the existing tool, unchanged in shape
  doc_id:   string # "pdf-3f2a9c01d4e7:1", as pdf_search returned it
  locator?: string # "p.7"; without one, from the first page
}
```

- **Reading reuses `docs_read`.** `pdf_search` gives every passage an id of the form `pdf-<12 hex digits of the PDF's SHA-256>:1` and a locator `p.N` (or `p.N .. p.M` for merged chunks), exactly like a collection's hit. `docs_read(doc_id, "p.N")` returns that page from the cached index, a page at a time under the 6,000-token budget, with `More: docs_read(…, locator="p.N+1")` while the document goes on. No `pdf_read` tool: one reader of located text is enough, and the model already knows it. `docs_read` is therefore offered whenever `pdf_search` is, with or without a collection; `docs_search` still only when a collection exists.
- **Output** (one text block, under the launcher's 8,000-token cap per call by the same `render_search` budget as `docs_search`):
  - a header line: the source (URL after redirects, `drive:<id> (<name>)` or the path), the title from the document info or the file name, the page count, how many pages were indexed (§3.5), the pages with no text layer (§3.6), and a note while the embedding is still running (§3.4);
  - the passages, each in `<untrusted source="docs" id="pdf-…:1">` with `path: <source>` and `locator: p.N`, as `docs_search` renders them;
  - a footer: `Read a page: docs_read(doc_id="pdf-…:1", locator="p.N"). Cite as <source>, p.N.`
- **Citation shape** is `docs_search`'s: source plus page. The prompt block (§3.8) says so in one sentence.
- **Errors** are one line, as the reader's own: `failed: password-protected`, `not a PDF (no %PDF- header)`, `larger than 64 MiB`, `outside this session's workspace: …`, `Web access is off in this session (/airgapped on) …`, `failed: killed: over the extractor's memory cap (1 GiB)`.

### 3.2 Where it runs

**In `ling-docs mcp`, not in the command sandbox.** Codex starts MCP servers itself, as children of the `ling` process, outside the sandbox it puts around the model's shell commands (the same reason §5.2 gives for the Drive app). That decides three things:
- the extractor keeps its full wall: a `systemd-run --user` scope in `mightling-index.slice`, admitted against the host-wide budget shared with `ling-code` and the docs index, inside bwrap with no network. The question of §4's last point (whether bwrap and a scope nest inside Codex's sandbox) does not arise for D;
- what the sandbox would have enforced, the tool enforces itself: which local files it may open (§3.3.3) and whether the session may use the network (§3.7);
- the server lives exactly as long as the session, so "per session" has a natural owner (§3.4).

The launcher registers the server as it does today (`-c mcp_servers.ling_docs.command=…`, `args=["mcp"]`), but now when **either** a collection exists **or** PDF reading is available (`libpdfium.so` installed); the server lists `docs_search` only with a collection, and `pdf_search` and `docs_read` only with PDFium. With `mightling_docs = false`, or no `ling-docs` (macOS, Windows), nothing is offered. Codex's default MCP tool timeout (300 s, `DEFAULT_TOOL_TIMEOUT`, read 2026-10-12) is above everything §3.5 lets one call take.

The server forwards to its children what the scope, the ledger and the fetch need beyond Codex's short allow-list: `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` (the user's systemd and the ledger at `$XDG_RUNTIME_DIR/mightling-index/admission.lock`, without which a scope cannot be started and admission would use a ledger of its own), the proxy variables (for `ling-fetch`), `DREAMFERENCE_MIGHTLING_AIRGAPPED`, and the `DREAMFERENCE_MIGHTLING_DOCS_PDF_*` settings of §3.9.

### 3.3 Where the PDF comes from

The source decides the fetcher. **`ling-docs` itself opens no socket in any case**: the web is `ling-fetch`'s, Drive is the Drive app's, and a local file is opened by path. Whatever the source, the bytes land in a private temporary file (mode 0600, in a mode-700 directory under `$CODEX_HOME/docs/pdf/`), are checked for `%PDF-` in the first KiB and for the size cap, and are hashed; the hash names the cache entry (§3.4), and the temporary file is deleted once the index is built (the index keeps text only).

#### 3.3.1 A URL (`http://`, `https://`)

- **Air gap first** (§3.7): at `/airgapped on` the call is refused with `ling-fetch`'s own sentence and nothing is started.
- **The download is `ling-fetch`'s:** `ling-docs` runs `ling-fetch --download <file> --max-bytes <cap> <url>` (found beside `ling-docs`, as both are installed into the same `bin/`), a new mode of the existing binary: the same agent, user agent, cookies within the call, redirects, proxy rules and per-read timeout as a fetch, the body streamed through the cap into the file, nothing printed but one JSON line (`final_url`, `status`, `content_type`, `bytes`). It refuses a body without `%PDF-` in its first KiB (servers send PDFs as `application/octet-stream`, and an HTML error page as `200`), and a body over the cap, and deletes the partial file. `ling-fetch` resolves the level again itself (`refusal_now()`, unchanged), so a second check stands behind the first.
- **One download per URL per session:** the server remembers `url → hash` for its lifetime, so a second `pdf_search` on the same URL (another query, the usual case) goes straight to the cache.
- **Bounds:** 64 MiB (`docs_pdf_max_mb`), and a wall-clock limit of 120 s on the whole download (the per-read 25 s of `ling-fetch` alone would let a slow server drip for hours).

#### 3.3.2 A Drive file (`drive:<file id>`)

*Not in the first step.* It needs what Surface B needs (§5.2): the service's `files.get?alt=media` for `application/pdf` (behind the shared secret, returning the bytes, parsing nothing) and a command of the launcher that writes those bytes to a file for `ling-docs` (`ling apps drive download <id> --out <file> --max-bytes <cap>`), since the service is a socket and `ling-docs` opens none. Until then `pdf_search` answers a `drive:` source with one line: `Drive PDFs cannot be searched yet: drive_read refuses PDFs (PDF_SEARCH §3.3.2).` The Drive app's air gap rule applies unchanged (APPS: unavailable at `/airgapped on`).

#### 3.3.3 A local file (a path, or an Ask attachment)

The server is outside the sandbox, so it bounds what it opens itself, by a rule no wider than what the session's sandbox reads:
- **Relative paths** are taken from the session's working directory. Codex tells an MCP server that declares the experimental capability `codex/sandbox-state-meta` the working directory of each call, in the call's `_meta` (`sandbox_cwd`, a `file:` URI; read 2026-10-12 in `core/src/mcp_tool_call.rs`); without it, the server's own working directory, which Codex starts it in.
- **Allowed:** a regular file, resolved through symlinks, under that working directory or under the root of one of the user's `ling-docs` collections. An Ask thread's attachment is in the thread's folder, which is its working directory, so attachments need nothing more.
- **Refused:** anything else, with the reason and the way out: `outside this session's workspace: copy it into the workspace first (cp in a shell command, which the sandbox allows or refuses)`. Paths inside the folders `ling-docs` never indexes (`~/.ssh`, `~/.gnupg`, `~/.mightling`, `~/.config`, …, LOCAL_INDEX §5.2) are refused even under the working directory.
- Not a PDF (`%PDF-` missing), empty, or over 64 MiB: refused with the reason. The file is copied to the private temporary file before extraction, so the extractor's sandbox sees one file read-only and never the workspace.

### 3.4 The ephemeral index

**A PDF becomes a collection of one document,** named `pdf-<12 hex digits of its SHA-256>`, in its own database `$CODEX_HOME/docs/pdf/pdf-<hash>.db` (the schema, journal mode and permissions of every collection database, LOCAL_INDEX §6). It is built by the indexing code itself: the `documents` row is written with the source as its path; the extractor runs as for a collection (§3.5); the index worker writes units and chunks (keyword search works from that moment) and then embeds the chunks one at a time; `Searcher` and `read` serve it like any collection. No second chunker, embedder, keyword table or fusion.

- **Keyed by content.** The same PDF from two URLs, or a URL and an attachment, is one entry; a changed PDF at the same URL is a new one.
- **Answering before the embedding ends.** Extraction and chunk writing take seconds even for hundreds of pages (78–131 pages/s, LOCAL_INDEX §15.2); embedding takes about a minute for a 300-page paper (≈400 chunks at 8–13 chunks/s) and minutes for a 1,000-page manual. A call waits for the embedding up to `docs_pdf_wait_s` (45 s); if it is still running, the call answers from what is there (keywords over every chunk, meaning over the chunks embedded so far: the fusion already handles a partly embedded database and says so in a note), and the embedding goes on in its scope. The next call finds more vectors.
- **Lifetime: per session in use, bounded on disk.** The server deletes nothing a running session needs: entries are touched on every use and evicted oldest-first when the cache passes `docs_pdf_cache_mb` (1,024 MiB), and any entry unused for `docs_pdf_cache_days` (1) is removed at the next server start. So a session that resumes the next morning, or a second question in another session about the same paper, finds the index; nothing outlives a day unused. The text of a PDF from the web or Drive is kept on disk for that day, as the text of a collection is: `$CODEX_HOME/docs` is mode 700. A run directory left by a killed server is removed at the next start.
- **Not a collection the user sees:** cache entries are not in `docs.toml`, not searched by `docs_search`, not in `ling docs status` (which gains one line: the cache's size and count), and the name pattern `pdf-` + 12 hex digits is refused for a user collection.

### 3.5 Bounds beside a resident model server

Every bound of indexing applies, because it is the same code:
- **Memory:** the extractor runs in a scope capped at 1 GiB (Kind `Extract`), the embedder in one capped at 2 GiB (Kind `Embed`, 0.9 GB measured), each admitted against the host-wide budget (`MemAvailable − (earlyoom's line + 4 GiB) − Σ promised`) under the shared ledger lock. When the budget cannot cover a run, the call answers `deferred: memory: …` at once rather than waiting. No user systemd (no scope can be capped): refused with that reason, as indexing defers.
- **Not deferred to Night Shift.** Collection indexing waits while Night Shift or a benchmark holds `runner.lock`; `pdf_search` is asked for by a running session (possibly Night Shift's own), so it runs, inside the same admission.
- **Time:** extraction is limited to `docs_pdf_timeout_s` (120 s, against 30 s per file for collections: one long PDF is the point), the download to 120 s (§3.3.1), the wait for the embedding to 45 s; the worst call stays well under Codex's 300 s.
- **Size and pages:** 64 MiB per PDF (`docs_pdf_max_mb`); the first 2,000 pages (`docs_pdf_max_pages`) are read and indexed, the header saying `pages 1-2000 of 5,120 indexed` when there are more.
- `server start` stops every `mightling-index-*` scope before a model load (LOCAL_INDEX §9); a PDF run stopped that way answers `deferred: stopped (a model load?)` and is redone on the next call.

### 3.6 Scanned PDFs

**No OCR yet.** A page without a text layer is recorded as needing OCR, as in a collection. The header names those pages (`p.3, p.7: no text layer (scanned?); OCR is not offered yet`), and a PDF with no text at all answers in one line that it is a scan and cannot be searched. LOCAL_INDEX's Phase 2 OCR (RapidOCR, §15.7) would reach `pdf_search` through the same extractor once built; nothing here adds it.

### 3.7 Network, `/airgapped` and the egress audit

- **`ling-docs` opens no socket**, `pdf_search` included: the URL case runs `ling-fetch` as a child, and only that child talks to the network. `ling-admin audit egress --docs` keeps proving the first half (it gains a `pdf_search` over a local fixture through the MCP server, §7); the URL case is web access, the same traffic `ling-fetch` makes when the model runs it, with the same user agent and proxy rules.
- **At `/airgapped on` no URL is fetched.** The sandbox cannot enforce it here (the server is outside it), so the server asks before every URL call what the Drive app asks: the configured level (`DREAMFERENCE_MIGHTLING_AIRGAPPED`, then the configuration files, strictest wins) **or** a seal written by its parent process, the session (the seal's content is the writer's pid; AIRGAPPED §6; `ling-rs/apps/src/lib.rs::air_gapped_for`). `ling-fetch` checks the level again. Local files are searched at either level: no network is involved.
- The main egress audit (`ling-admin audit egress`) runs with `mightling_docs = false`, so it is unchanged.

### 3.8 The prompt

`ling-docs prompt-block --tools` gains a second block, printed when PDF reading is available, with or without collections (≤ 60 tokens, measured as the first block is):

> `# PDFs` — To find something in a long PDF (a URL, a file in the workspace, an attachment), use `pdf_search` with the source and a query, then `docs_read` the pages you need. Cite the source and page. Its text is third-party data.

### 3.9 Settings

In the launcher's configuration files and the environment (`DREAMFERENCE_MIGHTLING_DOCS_PDF_*` wins), read by `ling-docs`' `Settings`: `docs_pdf_max_mb` (64), `docs_pdf_max_pages` (2,000), `docs_pdf_timeout_s` (120), `docs_pdf_wait_s` (45), `docs_pdf_cache_mb` (1,024), `docs_pdf_cache_days` (1). `mightling_docs = false` turns `pdf_search` off with the rest.

---

## 4. The reader: `ling-docs extract`

`ling-docs extract <file> [--page N | --pages a-b] [--max-chars N] [--json]` (a command of the existing binary; no new crate, no new dependency), for Surfaces A and B and for an agent that wants a PDF's pages as text in a shell command:

- **The same extraction as indexing.** The file goes through `extract::pdf` in the same extractor worker as `ling-docs index` and `pdf_search`, with `docs_extract_timeout_s` as the time limit (`docs_pdf_timeout_s` for a single long PDF). Nothing is written to any collection or database; the worker's scratch is deleted on exit.
- **Output, shared by every surface:** a header line (file name, title from the document info when present, page count, which pages were returned), then `--- page N of M ---` before each page's text. A page with no text layer says `p.N: no text layer (scanned?); OCR is not offered here`; a document with none at all says so in one line. A failure is the worker's own line (`failed: password-protected`, `failed: not a PDF, or damaged`, `failed: killed: over the extractor's memory cap (1 GiB)`), never a partial trace.
- **Paging:** by default the first pages up to `--max-chars` (8,000, `ling-fetch`'s default); `--page N` / `--pages a-b` select. Within CONTEXT_BUDGET's per-output cap by construction. `--json` gives `pages`, `page_count`, `returned`, `title` and the units, for the callers below.
- **Run-time files:** PDFium is the one `ling-admin docs setup` already fetches, pinned by URL and SHA-256 (LOCAL_INDEX §17). Without it, `extract` exits with a line naming what is missing, which the callers pass on (§5.1).
- **Inside the agent's sandbox.** `ling-fetch` and an agent's shell command run inside Codex's command sandbox, where the user's systemd cannot be reached (`Host::can_create_scopes` is false there) and a nested bwrap may be refused *(unverified: the pinned helper's seccomp filter and nested user namespaces, to be tried in Phase 0)*. There `extract` runs the worker as a plain child process with `RLIMIT_AS` at the extractor's 1 GiB cap and the same time limit, and the agent's sandbox (no network at `/airgapped on`, writes confined to the workspace) is the outer wall. The parser is the same either way; only the wall around it differs, and no extraction runs unbounded. Outside the sandbox (a terminal, `ling apps serve drive`, `pdf_search`), the scope and bwrap are used.

---

## 5. The paged surfaces

### 5.1 A: `ling-fetch`, `ling-search --read`, `web_fetch`

- **Detection:** `Content-Type: application/pdf` **or** the `%PDF-` magic in the first 1 KiB (servers send PDFs as `application/octet-stream`).
- **Download ceiling:** 5 MiB rises to **20 MiB for PDFs only** (a manual or a paper is often 5–15 MB), decided after the first KiB; other bodies keep 5 MiB.
- **Hand-off:** the body is written to a private temporary file (mode 0600, removed afterwards) and `ling-docs extract` is run on it, found beside `ling-fetch` (both are linked into `~/.local/bin`). `ling-fetch <url> [--page N | --pages a-b]` passes the paging through; `--json` gains `pdf: true`, `pages`, `page_count`. `ling-search --read` reads a PDF result's first pages within its share of the output budget and names the page range it returned. A PDF longer than a few pages also gets one line pointing at `pdf_search` when it is offered.
- **Without the reader** (macOS and Windows clients, which carry no `ling-docs`; a Linux client without its run-time files, LOCAL_INDEX §17), `ling-fetch` returns one line instead of the noise: `PDF, N bytes: reading PDFs needs ling-docs and PDFium (run \`ling-admin docs setup\` on a node)`. This alone fixes today's defect and ships first (§6, Phase 2a).
- **The prompt:** `WEB_ACCESS_INSTRUCTIONS` gains one sentence: PDFs come back page by page; ask for the pages you need, or search a long one with `pdf_search`.
- **MCP `web_fetch`** (`WebTools`) mirrors `ling-fetch` by running the same `ling-docs extract`, not a Python parser.
- At `/airgapped on` nothing changes: the fetch already refuses the network there.

### 5.2 B: Drive

- **The service** adds `files.get?alt=media` for `application/pdf` up to the PDF cap (APPS §9's transport), behind the same shared secret, returning the bytes to the caller. It parses nothing: the container stays a single stdlib file with no PDF library. The same endpoint serves D's `drive:` source (§3.3.2).
- **`ling apps serve drive`** writes the bytes to a private temporary file, runs `ling-docs extract` with the paging the call asked for, and answers within `drive_read`'s 20,000-character cap. It runs outside the agent's command sandbox (Codex starts MCP servers itself), so the reader has its full bwrap sandbox and scope there. `drive_read` gains optional `page` / `pages` arguments; other binaries stay refused with their type and size.
- **Search is Google's:** `drive_search` already matches PDFs through Drive's `fullText contains`, which indexes PDF text on Google's side; finding the passage inside a long one is `pdf_search` on `drive:<id>`.
- Read-only by construction, as the rest of the Drive app (the token carries write permission, APPS §10). The text is wrapped in the same `<untrusted source="drive" …>` boundary as every other Drive body.

### 5.3 C: the Onyx web chat

**Not planned; confirmed by the owner on 2026-10-12** when PDF search moved into `ling`. The Ask threads that replace Onyx's chat run `ling`, so they get D, A and B with nothing added. Draft v1's sidecar (SearXNG `filetype:pdf`, a hardened downloader, chunks embedded on the CPU, a 2 GiB container) is kept in git history; it was worth reopening only if Onyx outlived the Ask UI, which it does not: Onyx is retired in 1.7.0 (ASK §19), so C is closed.

---

## 6. Security

PDFs are attacker-controlled, and so are the URLs and paths a model may be talked into. Five rules apply on every surface:
1. **Text only.** PDFium's text layer, nothing else: no rendering, no JavaScript, no embedded files or attachments, no external references resolved, no font program run for output (LOCAL_INDEX §7.1).
2. **Bounded and isolated.** The reader's memory cap, time limit and sandbox (§3.5, §4); a size ceiling on every download and every file; a parser failure is a one-line `failed: …`.
3. **Untrusted text.** Output is marked as document content (`<untrusted …>`, as `docs_search`, `ling-search --read` and the apps already do, with the hardened tag escaping), so a PDF's "ignore your instructions" is data. `ling` already sits behind its command sandbox.
4. **No wider reach than the session.** `pdf_search` runs outside the sandbox, so it opens local files only under the session's working directory or a collection's root, never in the folders the index never reads (§3.3.3), and fetches only when the session may use the network (§3.7). A copy of the bytes, not the workspace, is what the extractor sees.
5. **Read-only.** Both tools only read; the cache is the only thing written, in `$CODEX_HOME/docs/pdf/` (mode 700), and evicted (§3.4).

A's and D's downloads run with the user's own network reach, as `ling-fetch` does today; B's and D's Drive reads come only from Google's API.

---

## 7. Phases and acceptance

| Phase | Builds | Done when (measured) |
|---|---|---|
| **0** | Nothing. A fixed set of 20 public PDFs (papers, manuals, a scan, a 300-page spec, a contract, one `octet-stream` server), and one try of `ling-docs`' worker started from inside `ling`'s command sandbox | The worker's behaviour inside the agent's sandbox (nested bwrap and scope allowed or not) recorded here, which decides §4's last point; `ling-docs`' reader on the 20 PDFs (characters per page, time per page, peak memory), with the scan reported as one; the time to a first `pdf_search` answer and to a fully embedded index for the 300-page spec |
| **1 (D)** | `pdf_search` for local files and URLs (`ling-fetch --download`), `docs_read` on its ids, the cache and its eviction, the prompt block, the launcher's registration; `ling-docs extract` | Rust tests on fixture PDFs (§8); a `ling exec` task "in <paper URL>, what learning rate did the authors use, and on which page?" answers with the right page, every tool output under 8,000 tokens, in fewer than five tool calls; a second question on the same URL downloads nothing; at `/airgapped on` the URL is refused and a local PDF is still searched; `audit egress --docs` passes with its `pdf_search` case |
| **2a (A)** | `ling-fetch` detects PDFs and returns the one-line notice instead of decoded bytes | `ling-fetch` and `ling-search --read` of the 20 URLs return no binary noise, with or without `ling-docs` installed |
| **2 (A)** | `ling-fetch`, `ling-search --read` and `web_fetch` hand PDFs to `ling-docs extract`, with paging | Unit tests on fixture PDFs (paging, markers, magic sniffing, the 20 MiB ceiling, the scan line, a missing PDFium); the egress audit still passes |
| **3 (B, D on Drive)** | The service's PDF download; `drive_read` for PDFs; `pdf_search` on `drive:<id>` | A PDF in the test account's Drive (and one in a shared drive) is read by page and searched; binary non-PDFs still refused; the service container gains no dependency |

## 8. Tests

Offline, as the rest of the suite: fixture PDFs (generated, a few KB each; `ling-docs-rs` already writes one-page PDFs in its tests and holds a hostile set), no network, no real systemd (the tests' host runs a scope's command as a plain child). In `ling-docs-rs/` with `cargo test --locked` (PDF cases when `MIGHTLING_DOCS_LIB_DIR` names PDFium): a multi-page PDF searched by a query that is on one page only (the right page first, its id read back with `docs_read`), a second call served from the cache without extraction, a PDF with an empty page (named as needing OCR), the page cap, a non-PDF and an oversized file refused, a path outside the working directory and one under `~/.ssh` refused, a relative path resolved against the `sandbox_cwd` of the call's `_meta`, a URL refused when the parent's seal says `on`, a URL fetched through a stand-in `ling-fetch` (a script that writes a fixture), eviction by size and by age, and the MCP server's tool list with and without collections and PDFium. `ling-fetch --download` in `ling-web-rs/` (magic check, cap, partial file removed; the HTTP side against a local listener). The launcher's registration in `ling-rs` (offered with PDFium and no collection; not offered without `ling-docs` or with `mightling_docs = false`; the forwarded variables). `web_fetch` and the service's PDF endpoint in the Python suite under conftest's rules when Phases 2–3 build them.

## 9. Open questions

- Whether `ling-docs`' sandbox can be nested inside `ling`'s (§4, Phase 0) for `extract` called from a shell command; `pdf_search` does not depend on it.
- Whether the cache should outlive the session at all (§3.4 keeps an unused entry one day so a resumed session finds it); `docs_pdf_cache_days = 0` would make it strictly per session, at the price of re-embedding on resume.
- Whether a model asked for a passage in a long PDF reliably prefers `pdf_search` over paging with `ling-fetch` once both exist; Phase 1's task measures it.
- OCR for scans: `ling-docs`' Phase 2 OCR (RapidOCR, LOCAL_INDEX §15.7) would reach these surfaces through the same extractor once built; nothing here adds it.

---

## 10. What the branch `pdf/search-tool` builds (2026-10-12)

Merged to main on 2026-10-13, after the live check of Phase 1 below.

**Live check (2026-10-13, node-b, production SGLang with the default model, `ling exec` built from the merge, throwaway `CODEX_HOME`):**
- "In https://arxiv.org/pdf/1512.03385, what learning rate did the authors use for training on ImageNet, and on which page?": one `pdf_search` call, unprompted; answer 0.1 (divided by 10 on plateaus), p.4, correct. Tool output 9,767 characters.
- Two questions on the same URL in one session (mini-batch 256, p.4; ensemble top-5 error 3.57 %, Table 5, p.6, both correct): two `pdf_search` calls, one `ling-fetch --download` (counted by a wrapper). Largest output 17,497 characters (`k` = 10), under 8,000 tokens.
- `DREAMFERENCE_MIGHTLING_AIRGAPPED=on`: the URL refused with the air-gap sentence and no fetch started; the local `resnet.pdf` searched (served from the content-keyed cache, so its header names the URL it was first indexed from).
- Not run: `ling-admin audit egress --docs` with its `pdf_search` case.

- **`ling-docs-rs/src/pdf_search.rs`:** the source resolver (URL, `drive:`, path, `file:` URI), the local-path rule of §3.3.3 (working directory from `_meta`, collections' roots, never the folders of LOCAL_INDEX §5.2, symlinks judged where they lead), the air-gap check (environment, configuration files strictest-wins, the parent's seal), the download through `ling-fetch --download` with a 120 s wall limit, the checked copy (0600, `%PDF-`, the size cap, SHA-256), the cache (`docs/pdf/pdf-<12 hex>.db`, touched on use, evicted by a one-day idle limit and the size cap, sparing an entry whose lock a session holds; dead run folders removed), and the answer (header, unmerged passages, the `docs_read` hint). A `drive:` source answers with the one line of §3.3.2.
- **`index::index_file`:** a PDF as the only document of its collection, through `extract_all` and the index worker (both now take a name and read-only roots rather than a `Collection`), a per-entry lock across sessions, a wait for the text and then up to `docs_pdf_wait_s` for the embedding, a `Background` worker handed back when it is still embedding, and an embed-only pass for an entry whose vectors a stopped worker left incomplete. Not deferred to Night Shift; admitted as every run is. The worker reads a page cap (`Job.max_pages`) and the document's title; the index worker keeps that title for a cache entry.
- **`ling-docs mcp`:** `pdf_search`, `docs_read` on `pdf-…:1` ids, the tool list by what exists, the `codex/sandbox-state-meta` capability, background embeddings stopped when stdin closes.
- **`ling-docs extract`** (§4) with `--page`, `--pages`, `--max-chars`, `--json`; inside the sandbox through `host::RlimitHost` (a plain child under `RLIMIT_AS`). PDFium read the fixtures under a 1 GiB `RLIMIT_AS` in the tests (not yet tried on the 20-PDF set of Phase 0).
- **`prompt-block --tools`** adds the `# PDFs` block (≤ 60 tokens by the conservative estimate); **`ling docs status`** names the cache's size; collection names of the form `pdf-` + 12 hex digits are refused.
- **`ling-fetch --download <file> [--max-bytes N]`** (`ling-web-rs`): the fetch's request, the body kept only when it is a PDF within the cap, written to a new 0600 file, one JSON line out; refused at `on` as a fetch is.
- **Launcher** (`ling-rs/src/docs_index.rs`): `ling_docs` registered when the block has `# Your documents` or `# PDFs`; `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, the air-gap variable, the proxy variables and the `DREAMFERENCE_MIGHTLING_DOCS_PDF_*` settings forwarded.
- **Tests:** `cargo test --locked` in `ling-docs-rs/` (59 unit, 6 integration; the PDF cases with `MIGHTLING_DOCS_LIB_DIR` set to the installed PDFium) and in `ling-web-rs/`; the launcher's unit tests in the build's export.

**Left:** the `audit egress --docs` case and the Phase 1 acceptance task on a real model (§7); Drive (§3.3.2, Phase 3); Surfaces A and B (Phase 2); Phase 0's measurements, including the first-answer and full-embedding times on a 300-page PDF.
