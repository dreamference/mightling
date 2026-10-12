# Mightling PDF Reading and Search — Technical Specification

**Status:** proposed, **nothing built** (revised 2026-10-03, reworked 2026-10-12 around `ling-docs`' PDF reader; draft v1 of 2026-09-28 is in git history). PDFs already on the machine are covered since 2026-10-08 by `ling-docs` ([MIGHTLING_LOCAL_INDEX](./DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) Phase 1, PDFium with a reading-order pass); this document is about PDFs that arrive from the web and from Drive. §1 is read from the code on 2026-10-12; every other section is design, and claims not checked on this machine are marked *(unverified)*.
**Target:** `ling` (the terminal agent, and the Ask threads that run it) first; the Onyx web chat only if it outlives the Ask UI (§4.3).
**Builds on:** `ling-docs`' extractor (`ling-docs-rs/src/extract/`, its sandbox and memory admission: LOCAL_INDEX §7.1, §15.2, §16.3), `ling-fetch` / `ling-search --read` (`ling-web-rs/`), the MCP `web_fetch` (`mcp_server/web_tools.py`), the Drive app ([MIGHTLING_APPS](./DREAMFERENCE_MIGHTLING_APPS.md) §6, §9; [GOA](./DREAMFERENCE_GOA.md)), the tool-output budget of [MIGHTLING_CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md), and `/airgapped` ([MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)).

---

## 1. What changed since draft v1, and what is true today

Draft v1 proposed one thing: an Onyx sidecar (`dreamference-pdf-search`, port 8769) that searches SearXNG for `filetype:pdf`, downloads up to five PDFs, extracts them with PyMuPDF, embeds their chunks and returns the top excerpts to the web chat. The 2026-10-03 revision replaced it with "one new extractor (`pdf-extract`/`lopdf` in Rust, `pypdf` in Python), three surfaces". Since then a PDF reader has been built and shipped, which moves the design again:

1. **Mightling already has a PDF reader, and a good one.** `ling-docs` reads PDFs through PDFium (`pdfium-render`, the pdfium-binaries build loaded at run time from `MIGHTLING_DOCS_LIB_DIR` or `../lib/ling-docs`), with the reading-order pass and the bidi step of LOCAL_INDEX §15.2, which matched the PyMuPDF reference everywhere and beat it on reading order. Its output is already page-located (`Unit { loc: "p.7", page: 7, … }`), and a page with no text layer is counted as needing OCR. It runs in the extractor sandbox of LOCAL_INDEX §16.3: bwrap with no network, a `systemd-run --user` scope capped at 1 GiB with `OOMPolicy=continue`, a per-file time limit, and the hostile set (a flate bomb, two million text operators, an encrypted and a truncated file) each ending in a one-line `failed: …`. A second extractor would duplicate all of that, with a weaker parser. **So nothing new extracts PDFs: every surface below hands the file to `ling-docs`.**
2. **`ling` can find PDFs on the web but not read them.** `ling-search` returns PDF links like any other. `ling-fetch` (`ling-web-rs/src/fetch.rs`, read 2026-10-12) decodes any non-HTML body by charset, up to `MAX_DOWNLOAD_BYTES` (5 MiB) and `DEFAULT_MAX_CHARS` (8,000) characters, so the model receives up to 8,000 characters of compressed-stream noise; `ling-search --read` fetches through the same code and has the same defect. That is the defect the agent actually hits.
3. **The Drive app is built** (APPS §14.4, a preview in 1.4.0): `drive_search` and `drive_read` are served by `ling apps serve drive` (Rust, in the launcher) over the Google service (`dreamference-gmail`, Python, `chat/google_workspace_reader.py`). `drive_read` refuses binary files with their type, PDFs included (APPS §9: "PDF_SEARCH is where text extraction belongs").
4. **The web UI is moving to Ask threads on `ling`** ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md)): a question in the new UI is answered by the same agent, with the same tools. Onyx stays until the new UI matches it (decided 2026-10-07). An Onyx-only PDF tool would be built for a surface that is being retired.

---

## 2. Decision: one reader, reached from the web and from Drive

| Surface | Where the bytes come from | Who calls the reader | Ranking |
|---|---|---|---|
| **A. `ling-fetch`, `ling-search --read`, `web_fetch`** | the web, through `ling-fetch`'s existing download (redirects, proxy rules, `/airgapped`) | `ling-fetch` runs `ling-docs extract` on the downloaded file | None: the model pages (`--page`, `--pages a-b`) |
| **B. Drive `drive_read`** | the Google service, `files.get?alt=media` | `ling apps serve drive` runs `ling-docs extract` | None |
| **C. Onyx `pdf_search`** | — | — | Not planned (§4.3) |

**The reader's one new entry point** is a command of `ling-docs` that reads one file and stores nothing (§3). Everything about PDFs (parsing, reading order, the scan check, limits, isolation, memory admission) stays in `ling-docs-rs/`, tested once.

**Why no ranking.** The agent already reads by range; the context-budget measurements (CONTEXT_BUDGET §1.1) show its cost is the *number* of reads, not their size, and that a fixed window beats whole files. A page-addressed PDF is the same shape. A PDF that belongs to the user, and should be searched by meaning, belongs in a `ling-docs` collection, where ranking already exists.

**Order.** A, then B. A fixes a defect in one crate plus one `ling-docs` command; B rides the Drive app and the same command.

---

## 3. The reader: `ling-docs extract`

`ling-docs extract <file> [--page N | --pages a-b] [--max-chars N] [--json]` (a command of the existing binary; no new crate, no new dependency):

- **The same extraction as indexing.** The file goes through `extract::pdf` in the same extractor worker, sandbox and scope as `ling-docs index` (Kind `Extract`: floor 256 MiB, ceiling 1 GiB, admitted against the host-wide budget shared with `ling-code`), with `docs_extract_timeout_s` as the time limit. Nothing is written to any collection or database; the worker's scratch is deleted on exit.
- **Output, shared by every surface:** a header line (file name, title from the document info when present, page count, which pages were returned), then `--- page N of M ---` before each page's text. A page with no text layer says `p.N: no text layer (scanned?); OCR is not offered here`; a document with none at all says so in one line. A failure is the worker's own line (`failed: password-protected`, `failed: not a PDF, or damaged`, `failed: killed: over the extractor's memory cap (1 GiB)`), never a partial trace.
- **Paging:** by default the first pages up to `--max-chars` (8,000, `ling-fetch`'s default); `--page N` / `--pages a-b` select. Within CONTEXT_BUDGET's per-output cap by construction. `--json` gives `pages`, `page_count`, `returned`, `title` and the units, for the callers below.
- **Run-time files:** PDFium is the one `ling-admin docs setup` already fetches, pinned by URL and SHA-256 (LOCAL_INDEX §17). Without it, `extract` exits with a line naming what is missing, which the callers pass on (§4.1).
- **Inside the agent's sandbox.** `ling-fetch` is a shell command the model runs, so `extract` is then started inside Codex's command sandbox, where a nested bwrap and a `systemd-run --user` scope may be refused *(unverified: the pinned helper's seccomp filter and nested user namespaces, to be tried in Phase 0)*. If they are, `extract` runs the worker as a child process with `RLIMIT_AS` at the extractor's 1 GiB cap and the same time limit, and the agent's sandbox (no network at `/airgapped on`, writes confined to the workspace) is the outer wall. The parser is the same either way; only the wall around it differs, and no extraction runs unbounded.

---

## 4. Surfaces in detail

### 4.1 A: `ling-fetch`, `ling-search --read`, `web_fetch`

- **Detection:** `Content-Type: application/pdf` **or** the `%PDF-` magic in the first 1 KiB (servers send PDFs as `application/octet-stream`).
- **Download ceiling:** 5 MiB rises to **20 MiB for PDFs only** (a manual or a paper is often 5–15 MB), decided after the first KiB; other bodies keep 5 MiB.
- **Hand-off:** the body is written to a private temporary file (mode 0600, removed afterwards) and `ling-docs extract` is run on it, found beside `ling-fetch` (both are linked into `~/.local/bin`). `ling-fetch <url> [--page N | --pages a-b]` passes the paging through; `--json` gains `pdf: true`, `pages`, `page_count`. `ling-search --read` reads a PDF result's first pages within its share of the output budget and names the page range it returned.
- **Without the reader** (macOS and Windows clients, which carry no `ling-docs`; a Linux client without its run-time files, LOCAL_INDEX §17), `ling-fetch` returns one line instead of the noise: `PDF, N bytes: reading PDFs needs ling-docs and PDFium (run \`ling-admin docs setup\` on a node)`. This alone fixes today's defect and ships first (§6, Phase 1a).
- **The prompt:** `WEB_ACCESS_INSTRUCTIONS` gains one sentence: PDFs come back page by page; ask for the pages you need.
- **MCP `web_fetch`** (`WebTools`) mirrors `ling-fetch` by running the same `ling-docs extract`, not a Python parser.
- At `/airgapped on` nothing changes: the fetch already refuses the network there.

### 4.2 B: Drive

- **The service** adds `files.get?alt=media` for `application/pdf` up to 20 MiB (APPS §9's transport), behind the same shared secret, returning the bytes to the caller. It parses nothing: the container stays a single stdlib file with no PDF library.
- **`ling apps serve drive`** writes the bytes to a private temporary file, runs `ling-docs extract` with the paging the call asked for, and answers within `drive_read`'s 20,000-character cap. It runs outside the agent's command sandbox (Codex starts MCP servers itself), so the reader has its full bwrap sandbox and scope there. `drive_read` gains optional `page` / `pages` arguments; other binaries stay refused with their type and size.
- **Search is Google's:** `drive_search` already matches PDFs through Drive's `fullText contains`, which indexes PDF text on Google's side, so no local index is needed.
- Read-only by construction, as the rest of the Drive app (the token carries write permission, APPS §10). The text is wrapped in the same `<untrusted source="drive" …>` boundary as every other Drive body.

### 4.3 C: the Onyx web chat

Not planned. The Ask threads that replace Onyx's chat run `ling`, so they get A and B with nothing added. Draft v1's sidecar (SearXNG `filetype:pdf`, a hardened downloader, chunks embedded on the CPU, a 2 GiB container) is kept in git history; it is worth reopening only if Onyx outlives the Ask UI, and then it would call the same reader rather than carry its own.

---

## 5. Security

PDFs are attacker-controlled. Three rules apply on every surface:
1. **Text only.** PDFium's text layer, nothing else: no rendering, no JavaScript, no embedded files or attachments, no external references resolved, no font program run for output (LOCAL_INDEX §7.1).
2. **Bounded and isolated.** The reader's memory cap, time limit and sandbox (§3); a size ceiling on every download; a parser failure is a one-line `failed: …`.
3. **Untrusted text.** Output is marked as document content (`<untrusted …>`, as `ling-search --read` and the apps already do), so a PDF's "ignore your instructions" is data. `ling` already sits behind its command sandbox.

A's downloads run with the user's own network reach, as `ling-fetch` does today; B's come only from Google's API.

---

## 6. Phases and acceptance

| Phase | Builds | Done when (measured) |
|---|---|---|
| **0** | Nothing. A fixed set of 20 public PDFs (papers, manuals, a scan, a 300-page spec, one `octet-stream` server), and one try of `ling-docs`' worker started from inside `ling`'s command sandbox | The worker's behaviour inside the agent's sandbox (nested bwrap and scope allowed or not) recorded here, which decides §3's last point; `ling-docs`' existing reader on the 20 PDFs (characters per page, time per page, peak memory), with the scan reported as one |
| **1a (A)** | `ling-fetch` detects PDFs and returns the one-line notice instead of decoded bytes | `ling-fetch` and `ling-search --read` of the 20 URLs return no binary noise, with or without `ling-docs` installed |
| **1 (A)** | `ling-docs extract`; `ling-fetch`, `ling-search --read` and `web_fetch` hand PDFs to it, with paging | Unit tests on fixture PDFs (paging, markers, magic sniffing, the 20 MiB ceiling, the scan line, a missing PDFium); a `ling exec` task "find the default port in <vendor> manual PDF" answers from the right page, with tool output under 8,000 tokens per call; the egress audit still passes |
| **2 (B)** | `drive_read` for PDFs, through the same command | A PDF in the test account's Drive (and one in a shared drive) is read by page; binary non-PDFs still refused; the service container gains no dependency |

## 7. Tests

Offline, as the rest of the suite: fixture PDFs (generated, a few KB each; `ling-docs-rs` already holds a hostile set), no network. `ling-docs extract` is tested in `ling-docs-rs/` with `cargo test --locked`, `ling-fetch`'s detection, ceiling, hand-off and notice in `ling-web-rs/` (the reader replaced by a stand-in), `web_fetch` and the service's PDF endpoint in the Python suite under conftest's rules (no real `docker`, no home writes).

## 8. Open questions

- Whether `ling-docs`' sandbox can be nested inside `ling`'s (§3, Phase 0), and if not, whether the `RLIMIT_AS` fallback is enough of a wall for the first PDFs or whether `ling-fetch` should instead ask a reader outside the sandbox (the `ling-docs session` process, through a request file as LOCAL_INDEX §6 does for indexing).
- OCR for scans: `ling-docs`' Phase 2 OCR (RapidOCR, LOCAL_INDEX §15.7) would reach these surfaces through the same command once built; nothing here adds it.
