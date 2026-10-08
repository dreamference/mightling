# Mightling 1.6.0 — release notes

**Status:** draft, 2026-10-08, written when the local file index (`ling-docs`, Phase 1 of
specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) was merged into `main` from `docs-index/1.6.0`. Not
published: the version is not set and there is no tag; both are decided when 1.6.0 is cut. Update
this file with whatever else merges before then.
The text between the two rules is the GitHub release's description.

**Version.** To be set to 1.6.0 in `setup.py`, `dreamference/__init__.py`, the MCP server's
`serverInfo` and `desktop/electron/package.json` when the release is cut. The release workflow
stamps it into the binaries.

**Checklist before publishing:**
- **The new asset:** the release carries `ling-docs-aarch64-unknown-linux-gnu.gz`, listed in
  `ling-aarch64-unknown-linux-gnu.sha256sums`, beside every asset 1.5.1 carried (the `puffin-*`
  transition copies included).
- **Signed:** `SHA256SUMS` and `SHA256SUMS.sig` are attached, and `ssh-keygen -Y verify` passes
  against `ling-rs/release-signing.pub` (specs/DREAMFERENCE_RELEASE_SIGNING.md §6).
- **Update from 1.5.1** in a scratch HOME: `ling update` installs 1.6.0 and `ling-docs`, and links
  `~/.local/bin/ling-docs`.
- **A node install** (`install.sh --role node`, scratch HOME): the summary shows the local file
  index step done, and `~/.local/share/dreamference/mightling/lib/ling-docs/` and
  `…/models/snowflake-arctic-embed-m-v2.0-int8/` hold the pinned files.
- **`ling-admin audit egress --docs`** passes on the release build (no destination, no DNS query).
- **`ling docs status`** on a node with `~/Documents` lists `documents`, indexed, and a session's
  prompt names it.

---

**Search your own documents.** Mightling now keeps a private index of your files and the agent
can search it. `~/Documents` and `~/Downloads` are indexed by default; add other folders with
`ling docs add <folder> --name <name>`, and remove any of them, the defaults included, with
`ling docs remove <name>`. In a session the agent searches with `docs_search`, reads a passage with
`docs_read`, and cites the file and the page or lines it used. From a shell: `ling docs search
"notice period acme"`, `ling docs read <id> --page 7`, `ling docs status`.

- **What it reads in this release:** PDF (text layer), Markdown, reStructuredText, Org, LaTeX and
  plain text. Scanned PDFs are recorded as needing OCR and counted in `status`; Word, HTML, mail and
  OCR come in a later release.
- **Every language.** The embedding model is multilingual (`snowflake-arctic-embed-m-v2.0`), keyword
  search covers Chinese and Japanese, and right-to-left PDFs are read in order.
- **Nothing leaves the machine.** The index opens no network connection at any step, and
  `ling-admin audit egress --docs` proves it. `/airgapped on` does not turn it off.
- **Safe to point at a Downloads folder.** Installers, archives, disk images and videos are never
  opened; secret-looking files and folders are skipped; each PDF is read in a sandbox with no
  network and a 1 GB memory cap, so a malformed or hostile file fails alone. Indexing shares the
  code index's memory budget and yields to the model server.
- **What it found on this machine's test set:** the right passage in the first 10 results for every
  one of 56 questions about 96 documents, in under 20 ms a query; 50,000 passages still answer in
  under 100 ms.
- **On a GB10 node** the installer downloads what the index needs (PDFium, ONNX Runtime and the
  embedding model, about 320 MB, each checked against a pinned checksum). On a node installed
  earlier, run `ling-admin docs setup` once after updating. Linux clients receive `ling-docs` but
  not these files yet, so for now the file index is a node feature; macOS and Windows clients do
  not have it.
- **To switch it off:** `mightling_docs = false` in `dreamference.toml`, or
  `DREAMFERENCE_MIGHTLING_DOCS=0`.

**Fixed: the code index's memory budget saw no running index.** Since the rename the code index
looked for its running scopes under a folder that does not exist, so a new index run was admitted
without counting the ones already running. Index runs again count each other, and the file
index's, against one budget.

---
