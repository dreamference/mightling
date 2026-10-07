# Mightling Ask, the Mightling web server, and retiring Onyx

**Status:** proposed (2026-10-07), nothing built. The user decided the same day: **drop Onyx, keep a web UI.** This spec replaces everything Onyx does for the product with Mightling's own pieces, keeps a browser UI, and adds what Onyx Lite never did here: search over the user's own files.
**Names:** written with the post-rename names ([RENAME_MIGHTLING](./DREAMFERENCE_RENAME_MIGHTLING.md), branch `rename/mightling`): `ling`, `ling-admin`, `ling-search`, `ling-fetch`, `ling-code`, `ling-app`, `~/.mightling`, `mightling_*` settings, `_mightling-node._tcp`. Where `main` still says Puffin, read `puffin` for `ling`.
**Builds on:**
- [PUFFIN_DESKTOP](./DREAMFERENCE_PUFFIN_DESKTOP.md): the Work window on `ling app-server`, the bridge's allow-list (§4.3), the air-gap rule in the server (§8.2, patch `0023`), Night Shift's busy marker (§8.3); and its Electron rebuild (branch `desktop/electron`), which copies the upstream vendor's desktop app;
- [PUFFIN_APPS](./DREAMFERENCE_PUFFIN_APPS.md): Gmail, Drive and Calendar through `/apps`, served by the Google service on port 8767;
- [ONYX](./DREAMFERENCE_ONYX.md): what is being replaced, feature by feature (§1 below);
- [IMAGE_SEARCH](./DREAMFERENCE_IMAGE_SEARCH.md), [PUFFIN_GMAIL](./DREAMFERENCE_PUFFIN_GMAIL.md), [GOA](./DREAMFERENCE_GOA.md): the sidecars that stay;
- [CONTEXT](./DREAMFERENCE_CONTEXT.md) and [PUFFIN_CODE_INDEX](./DREAMFERENCE_PUFFIN_CODE_INDEX.md): the indexing machinery the document index reuses;
- [PUFFIN_NODE](./DREAMFERENCE_PUFFIN_NODE.md): what an advertised node publishes; [PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md); [PUFFIN_EGRESS](./DREAMFERENCE_PUFFIN_EGRESS.md); the security review of 2026-10 (branch `security/review-1`).

---

## 0. Decisions, stated first

1. **One agent, no second chat backend.** Chat becomes an **Ask thread**: a conversation with the same `ling` agent, in a scratch folder instead of a repository. This is how the upstream vendor's desktop app does chat: it has no separate chat server either (PUFFIN_DESKTOP §1).
2. **One UI, two hosts.** The Work UI in `desktop/ui/` becomes the **Mightling UI** (Work and Ask threads, settings, apps). The Electron app shows it over `app://`; a new **web server, `ling web`**, serves the same build to a browser. The UI talks through one message interface, so the same code runs in both (the upstream vendor's app re-dispatches its IPC as window `MessageEvent`s for exactly this reason).
3. **One bridge policy.** The allow-list, the dropped thread fields and the "answer only pending requests" rule (PUFFIN_DESKTOP §4.3, `desktop/bridge/src/lib.rs`) move into one data file, `desktop/bridge/policy.json`, enforced by the Rust bridge crate (inside `ling web`) and by the Electron main process. Both run the same conformance vectors, so the two cannot drift.
4. **The web server requires a credential on every request, loopback included.** Sandboxed commands can reach loopback whenever the network is on (`/airgapped off`), and the Onyx stack showed what a published default credential costs (security review, 2026-10). Nothing is reachable from the LAN unless the node is advertised and a device has been paired.
5. **Search over the user's files is new and local.** `ling-docs` indexes folders the user chooses, offline, inside the same memory budget and network-less sandbox as `ling-code`. Onyx Lite never did this here: the Lite stack leaves out Vespa, the workers and the embedding servers (ONYX §1).
6. **Onyx goes in three releases, never in one.** Preview beside Onyx, then Onyx opt-in with a history export, then removal (§10). Until the last step, `ling-admin chat start` brings Onyx back.

---

## 1. What Onyx does for Mightling today, and what replaces it

Read from `dreamference/chat/` and [ONYX](./DREAMFERENCE_ONYX.md) on 2026-10-07.

| Onyx today | Where | Replacement | Phase |
|---|---|---|---|
| Browser chat UI with history, search, rename | Onyx web and API servers, PostgreSQL | **Ask threads** in the Mightling UI: `thread/list`, `thread/search`, `thread/name/set`, `thread/archive` (all already in the allow-list) | A |
| The model as Onyx's LLM provider (`configure` §2) | `onyx_runner.py` `configure()` | Nothing to register: `ling app-server` reads the model server through the launcher's tiers | A |
| The "Puffin" assistant persona and its instructions (ONYX §3) | `PUFFIN_ASSISTANT_INSTRUCTIONS` | The named prompt **`ask`** (`ling-rs/prompts/ask.md`), chosen per thread by the bridge (§3.2) | A |
| Branding: logos, fonts, 35 CSS rules, label rewrites, injected scripts (ONYX §3–§4) | `onyx_brand_assets.py`, `onyx_ui_*.py` | Not needed: the UI is Mightling's own code. ~180 KB of patch modules and their tests are deleted in Phase C | C |
| Web search through SearXNG, then opening results (ONYX §6) | Onyx's SearXNG provider | **`ling-search --read`**: search, fetch the top pages, return extracts with numbered sources (branch `features/search-read-context`) | A |
| Gmail tool and the "Connect Google" button (ONYX §8, §4.4) | custom tool + injected script | **`/apps`**: Gmail, Drive, Calendar as the agent's MCP tools; connecting through the Google service's own `/connect` pages (port 8767), linked from the UI's Settings → Apps | A |
| Image search with a gallery (ONYX §7) | custom tool, nginx route `/puffin-images/`, gallery script | An **`image_search` MCP tool** calling the same sidecar (port 8768); images served by `ling web` and the app at `/images/` (§6) | A |
| Voice dictation (ONYX §5) | `dreamference-stt` (speaches, CPU) + an SSRF patch in Onyx's API server | The UI's microphone button posts audio to the **same sidecar** through `ling web` or the app's main process; no Onyx patch (§7) | A |
| Image upload and understanding | Onyx's vision default | The protocol's `localImage` input: uploads are written into the thread's scratch folder and sent as `localImage` | A |
| Python execution tool | `puffin-code-interpreter-1` | The agent runs code itself, in its sandbox, in the scratch folder | A |
| Google sign-in for the UI (ONYX §9) | `enable_google_login()` | Dropped: the web server pairs devices instead (§4.3) | C |
| Several user accounts, admin panel | Onyx | Dropped: Mightling is personal, one owner per machine (§11) | C |
| The desktop Chat window | `ling app` → `http://localhost:3000/app` | The Electron app's Ask view | B |
| The web UI on the LAN for clients (`node enable`, port 3000) | nginx on `0.0.0.0:3000` | `ling web` on the advertised port, devices paired (§8) | A |
| Telemetry switch (`DISABLE_TELEMETRY`) | Onyx's `.env` | Nothing to switch off: no telemetry exists in Mightling's code | C |

**Other code that leans on Onyx, and must be moved before Phase C:**
- `google_service.py` takes the Gmail service's shared secret from `OnyxRunner._gmail_secret()`; `gmail_client.py` and `gmail_credentials.py` document their files in Onyx terms. The secret and the token files move to a `GoogleService` of their own.
- The Gmail, image search and speech sidecars are created on, or joined to, **Onyx's Docker network**. They move to `dreamference-sidecars`, the network SearXNG already uses ([DOCKER §6](./DREAMFERENCE_DOCKER.md)).
- `node_advertiser.py` decides whether the web UI is shared by the presence of Onyx's `.env`, and calls `OnyxRunner().bind_to_loopback()`. Both become questions about `ling web`.
- `desktop_runner.py` and `ling-rs/src/app.rs` check that Onyx answers before opening the Chat window.
- `ling-admin chat …` (formerly `puffin-admin puffin`, alias `onyx`) and `server start --no-onyx`.
- `ModelSpec.supports_vision` exists because Onyx refuses uploads without it; Ask sends images only when it is true.

**Measured cost of Onyx here (2026-10-07, idle, model server running):** about **1.07 GB of RAM** (API server 780 MB, web server 131 MB, PostgreSQL 117 MB, nginx and the code interpreter 40 MB) and about **4.9 GB of images** (`onyx-backend` 2.58 GB, `python-executor-sci` 1.23 GB, `code-interpreter` 578 MB, web server, PostgreSQL, nginx).

---

## 2. Architecture

```
 Browser (any device, paired) ─┐                         ┌─ ling-search --read / ling-fetch ── SearXNG (8888)
 Electron app (Mightling) ─────┤  Mightling UI           │  /apps MCP: Gmail, Drive, Calendar ─ Google service (8767)
                               │  (desktop/ui, one build) │  image_search MCP ───────────────── image sidecar (8768)
                               ▼                          │  docs_* MCP ─────────────────────── ling-docs index
   app://  ── Electron main: policy.json ─┐               │  code_* MCP ─────────────────────── ling-code index
   https/ws ── ling web: policy.json ─────┼─► ling app-server ─► model server (8000)
                                          │   (one per user)
   microphone ── /api/transcribe ─────────┴────────────────────► speech sidecar (8100)
```

### 2.1 One app-server per user, two ways to reach it

The upstream vendor's app starts its own app-server over stdio, and also supports connecting to a **local daemon** over a socket. Mightling does the same:
- **`ling web` running** (always, on a node; on demand elsewhere): it owns the user's `ling app-server`, started as `ling app-server --listen unix://$XDG_RUNTIME_DIR/mightling/app-server.sock` (the pinned app-server accepts `stdio://`, `unix://PATH` and `ws://IP:PORT`). The Electron app connects to that socket instead of starting a second server, so a thread started in the browser streams in the app and vice versa.
- **`ling web` not running:** the Electron app starts `resources/ling app-server` over stdio, exactly as the upstream vendor's app starts its bundled agent.

Phase 0 measures whether two app-servers on one `~/.mightling` can coexist at all (their thread database and session files). If they can't, the Electron app always prefers the socket, and starts `ling web` in local-only mode rather than a second stdio server.

### 2.2 The transport interface

The UI never talks to a transport directly. It uses one object with the shape the Electron preload exposes (`sendMessageFromView(msg)`, and messages arriving as window `MessageEvent`s):
- **in Electron,** the preload implements it over IPC;
- **in a browser,** a 2 KB adapter implements it over one WebSocket to `ling web` (`/ws`), with the same chunking for large payloads.

Everything else (`rpc.ts`, `store.ts`, the views) is shared and tested once.

### 2.3 The bridge policy, once

`desktop/bridge/policy.json` holds:
- `allowedRequests`, `allowedNotifications` (today's `ALLOWED_REQUESTS`/`ALLOWED_NOTIFICATIONS`);
- `threadOpeners` and `droppedThreadFields` (`baseInstructions`, `developerInstructions`, `modelProvider`, `config`, `personality`);
- the pending-request rule (answers only to server requests not yet answered);
- **`namedPrompts`:** the prompt names the UI may ask for (`default`, `ask`, `high-swe`), which the policy layer, not the UI, turns into `baseInstructions` (§3.2).

`desktop/bridge/vectors/*.json` are conformance cases (message in → accepted or refused, fields out). The Rust crate's tests and the Electron main process's tests both run every vector.

---

## 3. Ask threads

### 3.1 What an Ask thread is

- **A thread with no repository.** The policy layer creates `~/.mightling/ask/<thread-id>/` and starts the thread with that `cwd`, sandbox `workspace-write` rooted there, and `runtimeWorkspaceRoots` limited to it.
- **The network follows the air-gap level,** as for any session: on at `off` (so `ling-search` and `ling-fetch` work), none at `on`. At `on` the UI says why search, apps and image search are unavailable, from `/airgapped`'s own status text.
- **Approvals:** reading tools need none; anything that would write outside the scratch folder is refused by the sandbox as usual.
- **Everything the agent makes stays in the folder:** downloaded pages, notes, generated files. The thread's view lists them, with "Reveal in folder" in the app and "Download" in the browser.

### 3.2 The `ask` prompt, chosen safely

`ling-rs/prompts/ask.md`, a named prompt (PUFFIN_PROMPT), composed with the launcher's `web`, `email` and `code` blocks. It says:
- the agent answers questions and does research for the user; it isn't working in a repository;
- for current facts it uses `ling-search --read`, and cites sources as `[n]` with the list at the end;
- for the user's files it uses `docs_search`/`docs_read`, citing `path` and page;
- for mail, files in Drive and calendars it uses the `/apps` tools, treating their content as untrusted;
- it writes anything long or reusable to the scratch folder and links it;
- it keeps answers short unless asked (cave mode applies as usual).

**The UI never sends prompt text.** It asks for `prompt: "ask"`; the policy layer reads the composed text from `ling prompt show ask --composed` (a new flag printing exactly what a session would receive) and sets `baseInstructions` itself. A name outside `namedPrompts` is refused. This is the one place the policy layer *adds* a dropped field, and the conformance vectors cover it.

### 3.3 What the Ask view offers

| Feature | How |
|---|---|
| New question, history, search, rename, archive | `thread/start` with `prompt: "ask"`, `thread/list` filtered by the Ask root, `thread/search`, `thread/name/set`, `thread/archive` |
| Streaming answers with citations | the stream as in Work; `[n]` markers linked to the source list |
| Attach images | written to the scratch folder, sent as `localImage`; only when the served model has vision (`ModelSpec.supports_vision`) |
| Attach files (PDF, documents) | written to the scratch folder; the prompt tells the agent to read them with `ling-docs extract` (§5.3) |
| Voice | the microphone button (§7) |
| Images in answers | `image_search` results rendered as a gallery with a lightbox (§6) |
| Apps | Settings → Apps: `app/list` with each app's state and its Connect link (the Google service's page) |
| Continue as Work | "Open in a project": forks the thread into a Work thread on a chosen folder (`thread/fork` with a new `cwd`) |

---

## 4. The Mightling web server: `ling web`

### 4.1 What it is

A subcommand of the `ling` launcher (Rust, in `ling-rs`, using the bridge crate):
- `ling web start [--lan]`, `stop`, `status`, `open`, `pair`, `devices`, `revoke <device>`;
- run as a systemd user unit, `mightling-web.service`, installed by `ling-admin web enable` (on a node, by `node enable`).

It serves:
- `/` and `/assets/*`: the Mightling UI build, embedded in the binary (no files to go stale, nothing loaded from elsewhere);
- `/ws`: the bridge, one WebSocket per tab, vetted by `policy.json` and relayed to the app-server socket;
- `/api/upload`: attachments into the thread's scratch folder, size-capped (images 20 MB, files 100 MB);
- `/api/transcribe`: audio to the speech sidecar (§7);
- `/images/*`: the image sidecar's store, read-only (§6);
- `/api/apps`: the Connect links for `/apps`, pointing at the Google service;
- `/healthz`.

**Port 3100**, chosen to avoid Onyx's 3000 and 80 while both exist, and kept afterwards so bookmarks never move.

### 4.2 Binding

- **Default: `127.0.0.1:3100`.**
- **An advertised node** (`ling-admin node enable`): also the LAN address, advertised as `web=3100` in the `_mightling-node._tcp` record. `node enable --no-web` keeps it on loopback, as today.

### 4.3 Credentials: required everywhere

- **This machine:** `ling web open` (and the Electron app, when it uses the web server's socket instead) reads `~/.mightling/web/token` (0600) and opens the browser on a one-time login URL. The URL is exchanged for a session cookie (HttpOnly, SameSite=Strict, Secure where TLS is on), and the one-time code dies with it.
- **Another device on the LAN:** `ling web pair` (or the UI's Settings → Devices) shows an 8-digit code and a QR code, valid for 10 minutes, for one device. The device enters the code, gets a long-lived device cookie, and is listed by `ling web devices` with its name and last use; `revoke` ends it.
- **Every request** carries a valid session. WebSocket upgrades and every POST also check `Origin` against the server's own origins, and `Host` against the addresses it serves (DNS rebinding). This is the fix the security review applied to the Google service, applied from the start.
- **Why loopback needs a credential too:** at `/airgapped off`, a command the agent runs, or a page a prompt injection convinced it to fetch, can reach `127.0.0.1:3100`. Without a credential, a command could start a thread with Full Access, or approve its own request through the bridge.
- **The Google service's `/connect` pages stay on loopback.** A paired device sees the apps' state, but connecting an account is done on the node itself, where the browser can complete Google's sign-in on `localhost`.

### 4.4 Secure context and the LAN

Browsers allow the microphone and the clipboard API only on HTTPS or `localhost`. Over plain HTTP from a phone (`http://192.168.50.105:3100`), voice and "copy" don't work. Options, in order:
1. **Phase A: accept it,** and say so in the UI. Text works from any device. The Electron app on a client keeps working, because it loads the UI over `app://` and reaches the node through its own connection.
2. **Phase D: TLS for an advertised node.** `ling-admin web tls` creates a per-node certificate authority, prints its fingerprint, and offers the CA file to paired devices, which install it once. This is a decision for the user (§14), because installing a CA on a phone is a real step.

**Decided 2026-10-07 (the user):** the web UI stays text-only from other devices; most people will use the desktop app, which needs no certificate. Phase D is not scheduled. If it is ever built, it should be a name-constrained authority (`.local` names and private addresses only), verified on iOS and Android before relying on the constraint, and the desktop app should pin the node's certificate at pairing instead of installing the authority system-wide.

Copying still works over plain HTTP: selecting and copying text, Ctrl+V and pasting a screenshot (the `paste` event's `clipboardData`) are not restricted. Only `navigator.clipboard` is, so every copy button must fall back to `document.execCommand('copy')` on a hidden textarea when `navigator.clipboard` is undefined; a test runs the button with `navigator.clipboard` removed.

### 4.5 What `ling web` never does

- No telemetry, no analytics, no remote fonts or scripts. The pages carry a strict CSP (`default-src 'none'`, scripts and styles `'self'`, `connect-src 'self'`).
- No route to the model server, SearXNG or the sidecars beyond the four listed in §4.1.
- No account system, no password: devices are paired, sessions are cookies.

---

## 5. Search over the user's files: `ling-docs`

### 5.1 Scope

- **The user chooses folders:** `ling docs add ~/Documents`, `ling docs list`, `ling docs forget <folder>` (removes its entries and chunks); also in the UI's Settings → Files.
- **Formats:** Markdown, plain text, HTML, PDF (its text layer), DOCX, ODT, EML and MBOX. Scanned PDFs (images without text) are listed as "no text"; OCR is a later phase.
- **Excluded by default:** `~/.mightling`, `~/.ssh`, `~/.gnupg`, keyrings and browser profiles, `.git` and dependency folders, and anything matching `.mightlingignore`. Code repositories are `ling-code`'s job.
- **Limits:** 100 MB per file, and a per-folder file count reported before indexing starts.

### 5.2 How it searches

- **Phase 1, keyword:** SQLite FTS5 with BM25 over chunks. A chunk is a heading section (Markdown, HTML, DOCX) or a page (PDF), split further at 2,000 characters. Each chunk keeps its path, page, heading and modification time.
- **Phase 2, meaning:** dense vectors from `nomic-embed-text-v1.5` on the CPU (the context engine's model, already cached here, pinned to the CPU because the GPU belongs to the model server; CONTEXT), stored as float32 blobs beside the FTS table, combined with BM25 by reciprocal-rank fusion. Built only if Phase 1's measurements (§12) show keyword search misses what users ask.

### 5.3 How it runs

- **Indexing** is done by `ling-admin docs index` on the node, admitted against the same host-wide memory budget as `ling-code` and run in a network-less bwrap sandbox inside `ling-index.slice` (PUFFIN_CODE_INDEX's three rules). Extraction is Python (`pypdf` for PDFs, `python-docx`, the standard library's `email` and `html.parser`), decided in Phase 0 by quality on a sample of real files rather than by language preference.
- **Freshness:** files are re-read when their size or modification time changes. While `ling web` or the app is running, a watch (inotify) queues changes; otherwise `ling docs index` (and Night Shift, before its tasks) catches up.
- **Queries only read:** a small read-only MCP server, `ling-docs mcp`, offers the agent three tools:
  - `docs_search(query, k)`: chunks with path, page and a snippet;
  - `docs_read(path, page|chunk)`: the text of one place;
  - `docs_list(folder)`.
  The launcher declares it like `ling-code`'s server, so every session, Ask or Work, has it.
- **`ling-docs extract <file>`** prints a file's text, for attachments in an Ask thread.
- **Air gap:** neither indexing nor querying touches the network, so the tools stay available at `/airgapped on`, unlike search and apps.
- **Privacy:** the index holds the documents' text. It lives in `~/.mightling/docs/index.db` (0600), is never copied to another machine (not by `sync-model`, not by pairing), and `forget` deletes it for real (`VACUUM`).

### 5.4 Relation to other specs

[PDF_SEARCH](./DREAMFERENCE_PDF_SEARCH.md) (proposed, not built) is about PDFs on the web; `ling-docs` is about files on the disk. They share the extraction code once both exist. The old context engine (`dreamference/context_engine/`) indexes code and is superseded by `ling-code`; its embedding and storage code is reused for Phase 2.

---

## 6. Images

- **The tool:** `image_search(queries, count)`, served over MCP by a thin `ling-admin images mcp` that calls the existing sidecar's `POST /search` with its token. It returns Markdown images pointing at `/images/<id>.jpg`.
- **Serving:** `ling web` and the Electron app's `app://` handler serve `/images/*` read-only from the sidecar's store (`~/.config/dreamference/image-search`). Onyx's nginx route `/puffin-images/` is kept until Phase C, because saved Onyx chats link to it.
- **Presentation:** the gallery and lightbox are rebuilt as a React component in the Mightling UI, replacing `GALLERY_SCRIPT`/`GALLERY_CSS`.
- **Network:** the sidecar moves to `dreamference-sidecars`. It still uses SearXNG and the served model's vision, as today (IMAGE_SEARCH §2). Off at `/airgapped on`.

---

## 7. Voice

- **The sidecar stays:** `dreamference-stt` (speaches, CPU, `faster-whisper-small`), moved to `dreamference-sidecars`, published on `127.0.0.1:8100` only.
- **The UI:** a microphone button in the composer records with `MediaRecorder`. On release the audio goes to `/api/transcribe` (web) or to the main process (Electron), which forwards it to the sidecar's `/v1/audio/transcriptions`, and the text is **inserted into the composer**, not sent. The user reads it first, as in Onyx.
- **No Onyx SSRF patch** is needed any more: the request never passes through Onyx.
- **At `/airgapped on`:** voice still works, because transcription is local.

---

## 8. Nodes and clients

- **On a node,** `ling web` runs as a service; the advertised record names `web=3100`.
- **A browser on another device** pairs once (§4.3), then uses Ask and Work on the node: the agent runs on the node, in the node's folders.
- **The Electron app on a client** runs its own `ling app-server` against the node's model server, as Work does today. Ask threads on a client run on the client, with search through the node's SearXNG (`ling-search` already reads `node.json`). `/apps` and `ling-docs` are local to wherever the agent runs: on a client they show "on the node only" until a later phase.
- **The client app's TCP forwarder** (`forwarder.rs`), which existed to keep Onyx's page a secure context, is retired with the Chat window. The app gains "Open the node in a browser" instead.

---

## 9. Security and privacy, together

- **One family of processes to audit:** `ling web`, `ling app-server`, the MCP servers, and the sidecars. The egress audit gains `--web`: it traces `ling web` and its app-server through a scripted Ask turn with search, image search and a docs query, and must pass with only loopback and the search engines' traffic from SearXNG (which the audit already accounts for).
- **No published credential anywhere.** The Onyx admin account goes away in Phase C; until then it uses the per-install password of branch `security/chat-password`.
- **The policy layer, the air gap in the server (patch `0023`), the sandbox and the untrusted-content wrapping** are unchanged and apply to Ask exactly as to Work.
- **The Electron app's fuses and CSP** (branch `desktop/electron`) apply to the Ask view, which is part of the same bundle.

---

## 10. Retiring Onyx, in three releases

| Phase | Release | What ships | Onyx |
|---|---|---|---|
| **A** | next minor (1.6) | Ask threads, `ling web` (loopback; LAN with pairing on advertised nodes), `ling-search --read`, `ling-docs` Phase 1, image search, voice and apps in the Mightling UI, `policy.json` with vectors | Unchanged, still the default. The app shows Ask beside Chat |
| **B** | the one after (1.7) | `ling-admin chat export`; the app's Chat entry opens Ask; installers stop installing Onyx | **Opt-in:** `ling-admin chat start` installs and starts it; existing installs keep it running until the user runs `ling-admin chat retire` |
| **C** | the one after that (1.8) | Onyx code removed | **Gone:** on upgrade, `ling-admin` offers to stop and remove the containers; the volumes are kept until the user confirms |

**The history export (Phase B):** `ling-admin chat export` reads every chat session through Onyx's own API (with the per-install admin password) and writes each as a Markdown file in `~/.mightling/ask/imported/<date>-<title>.md`, with the question, answer and citations. It adds that folder to `ling-docs`, so old chats are searchable from Ask. They aren't converted into live threads, because the app-server's thread format isn't a stable public schema.

**What Phase C deletes:**
- `dreamference/chat/onyx_runner.py`, `onyx_installer.py`, `onyx_brand_assets.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_overrides.py`, `onyx_ui_scripts.py`, and their tests;
- the `chat` command group's Onyx subcommands and `server start --no-onyx`;
- the Onyx sections of CLAUDE.md (about a third of it) and the docs' web-chat page. [ONYX](./DREAMFERENCE_ONYX.md) is kept as history, marked retired.

**Rollback:** until Phase C ships, `ling-admin chat start` brings Onyx back with the user's data.

---

## 11. What is lost, on purpose

- **Several user accounts and an admin panel.** Mightling is a personal tool: one owner per machine, with paired devices.
- **Onyx's assistants, projects and sharing.** Ask has threads and folders; sharing a thread means sharing its scratch folder.
- **Onyx's connectors** (Slack, Confluence and the rest). None was ever enabled here, and pulling company data from other services runs against the product's local-first promise. `/apps` covers the Google services the user connects; `ling-docs` covers files on the disk.
- **Google sign-in to the UI.** Devices are paired instead.

---

## 12. Phase 0: measurements before building

| # | Question | How | Decides |
|---|---|---|---|
| 1 | Can two `ling app-server` processes share `~/.mightling`? | Start two, run a turn in each, list threads from both, check the thread database and session files | §2.1: whether the app must always use the socket |
| 2 | Does `--listen unix://` behave like stdio for every method the UI uses? | Replay a recorded Work session over the socket | The web server's transport |
| 3 | WebSocket relay overhead | Time first-token and stream rate through `ling web` against stdio, same prompt | Whether the relay needs batching |
| 4 | Extraction quality | 20 real PDFs, 10 DOCX, 10 HTML pages: `pypdf` against `pdfminer.six` against `pdftotext` | §5.3's extractor |
| 5 | Keyword search quality | 30 questions over a real Documents folder: does the right chunk appear in the top 5? | Whether Phase 2 (dense) is needed |
| 6 | Indexing memory and time beside the model | Index 5,000 files with the model server serving; watch earlyoom's margin | The memory budget's share for `ling-docs` |
| 7 | Voice end to end | Record in the Electron app and a browser on localhost; check latency and text | §7 |
| 8 | `ling-search --read` against Onyx's web answers | The same 20 research questions in both; compare sources and answers | Whether Phase B can make Ask the default |

---

## 13. Tests

- **Policy:** `policy.json` vectors run by the Rust crate and by the Electron main process; one extra vector per named prompt.
- **`ling web`:** routes, the credential on every route (including `/healthz` from the LAN), pairing (expiry, one use), `Origin`/`Host` checks, upload size caps, and refusals at `/airgapped on` for the routes that need the network.
- **UI:** the existing Vite tests plus the Ask view, run once against the IPC adapter and once against the WebSocket adapter.
- **`ling-docs`:** extraction fixtures per format, chunking, BM25 ranking on a fixture corpus, `forget` leaving nothing behind, the sandbox having no network (as `ling-code`'s tests do).
- **Export:** `chat export` against a recorded Onyx API (no live Onyx in tests; conftest's rules).
- **Egress:** `audit egress --web` must pass before Phase A ships.

---

## 14. Questions for the user

1. **Port 3100** for the web server, kept forever?
2. ~~Voice from phones~~ **Decided 2026-10-07:** text only from other devices' browsers; voice through the desktop app (§4.4).
3. ~~Onyx history export~~ **Decided 2026-10-07:** not needed; a clean start. Phase B drops the export.
4. **Phase A's scope:** all of §10's row A in one release, or Ask and `ling web` first, with `ling-docs` in a release of its own?

---

## 15. Changes to other specs when this is built

- **PUFFIN_DESKTOP:** Chat becomes Ask; `policy.json` replaces the hard-coded lists; the forwarder is retired.
- **ONYX:** marked retired in Phase C, kept as history.
- **PUFFIN_NODE:** the advertised `web` port becomes 3100; §5's "the web UI has one account" risk is replaced by pairing.
- **PUFFIN_APPS:** Connect is linked from the UI's Settings → Apps rather than Onyx's injected button.
- **IMAGE_SEARCH:** an MCP tool instead of an Onyx custom tool; served by `ling web`.
- **PUFFIN_EGRESS:** the `--web` mode.
- **ARCHITECTURE, CLI, SETUP, CLAUDE.md:** the web UI is `ling web`; the `chat` command group shrinks, then goes.

---

## 16. What was built (Phase A, first part: branch `ask/phase-a`)

Built: the bridge policy as data, Ask threads, and `ling web` with its credentials. Not built yet: the UI's Ask view, voice, images, `ling-docs`, and the Onyx steps.

**The policy is data, and both hosts are held to the same cases.** `ling-rs/web/policy.json` holds the allow-lists, the dropped fields, the named prompts and the Ask rules. `ling-rs/web/vectors/outgoing.json` holds the conformance cases: 26 of them, each with fixed stubs (a prompt composes to `PROMPT:<name>`, folders are `SCRATCH/n`, threads `ask-*` are Ask threads). They live beside the crate, not in `desktop/bridge/`, because the build copies only `ling-rs/` into the Codex export. The desktop app should read the same file and run the same cases. Five things differ from §2.3 and §3.1, all found while checking the pinned protocol (`app-server-protocol/src/protocol/v2/thread.rs`):

- **More fields are dropped from every thread opener.** Besides the five the desktop dropped, the policy removes `dynamicTools`, `environments`, `selectedCapabilityRoots`, `history` and `path`. Each loads tools, environments, plugins, a forged history or a rollout file.
- **A prompt is chosen on `thread/start` only.** A resumed session keeps the prompt it recorded, so `prompt` on resume or fork is refused.
- **An Ask folder is named after its thread once the thread exists.** The thread id does not exist when `thread/start` is vetted, and a running session's `cwd` cannot be renamed. So the folder is created as `~/.mightling/ask/q-<random>/`, and the server's answer makes `ask/<thread-id>` a link to it. The link is also how an Ask thread is recognised afterwards: a resume or fork of one keeps its folder and sandbox whatever the UI asks, and uploads land there.
- **`runtimeWorkspaceRoots` is dropped, not set.** The field is experimental, so a client that has not opted in cannot send it. Without it the writable root is the `cwd`.
- **The prompt text comes from a subprocess.** It is `ling prompt show <name> --composed`, run in the Ask root. `--composed` prints the text alone, exactly. The launcher's own composition is shared through `prompt::current_parts`.

**`ling web`** is the crate `ling-rs/web` (`ling-web-server`); the launcher's `src/web.rs` only routes to it. Its pieces:

- **Credentials.** One-time login codes (two minutes), 8-digit pairing codes (ten minutes, one use; ten wrong codes withdraw every pending code), and device cookies. Sessions from `ling web open` live in memory, so a restart means `ling web open` again.
- **Nothing a sandboxed command can read is a credential.** Codex's sandbox limits writes, not reads, so a command the agent runs can read everything under `~/.mightling/web/`. Three consequences:
  - The owner token (`token`) opens `/healthz` alone, for `ling web status`.
  - Pending codes are stored under their SHA-256, so listing the folder reveals none.
  - Devices are stored as hashes.

  Signing in needs a code written by the user's own shell; the sandbox cannot write that folder. An earlier draft also accepted the token as `Bearer` on `/ws`, which would have let an injected command open the bridge.
- **Request checks.** `Host` and `Origin` checks, and the CSP of §4.5 widened to what the UI's own `<meta>` allows.
- **The relay.** One app-server connection per tab, on `$XDG_RUNTIME_DIR/mightling/app-server.sock`. `ling web` starts `ling -c features.code_mode_host=true app-server --listen unix://…` when nothing answers there, passing `DREAMFERENCE_VLLM_HOST` when this machine is a node or has the host configured.
- **The browser bridge.** `/bridge.js` defines `window.electronBridge` and `mightlingWindowType = "web"` over the WebSocket, so the desktop UI runs unchanged.
- **Uploads.** `/api/upload` writes into Ask folders only, within 20 MB for images and 100 MB for files.
- **Night Shift.** The busy marker is kept per tab, and Night Shift now treats `web` as non-interactive.
- **`ling web ask "<question>"`.** One Ask thread through the running server, for scripts and for `ling-admin audit egress --web`. It signs in like a browser: it writes a login code and trades it for a session cookie. That mode traces `ling web serve`, and everything it starts, while the untraced client asks.

**Not built in this part:**

- **Unimplemented routes.** `/api/transcribe`, `/images/*` and `/api/apps` answer 501.
- **The embedded UI.** `build.rs` embeds the UI from `LING_WEB_UI_DIST`, but no build sets it yet, so `/` serves a placeholder until the desktop branch's `desktop/ui` is merged and the builder points at its `dist`.
- **Pairing extras.** No QR code, and no `ling-admin web enable`; `ling web start` writes and starts the user unit itself.
- **The node advert.** It still says `web=3000` (Onyx); it moves to 3100 when Onyx stops being the default (§10, Phase B).

**Verified on this machine (2026-10-07).**

- **Web crate:** `cargo test` in a copy of `ling-rs/web`: 22 unit tests and 11 server tests. The server tests cover the credential on every route, pairing (one use, expiry, withdrawal after ten wrong codes), `Host` and `Origin`, upload caps, and a tab through the policy to a stand-in app-server on a Unix socket. They also check that the owner token opens nothing but `/healthz`. They use no real network and no real `ling`.
- **Launcher:** `cargo test -p ling-launcher -p ling-web-server` in an export of the pinned Codex with the patches applied: 163 launcher tests, including the `ask` prompt.
- **Egress:** `ling-admin audit egress --web`, with a debug build from that export and the served Qwen3.8 model, **passes**. It connected only to the model server (3×) and the Gmail service (2×, the `ask` prompt's email block), sent no DNS query, and started `ling` three times: the server, `prompt show --composed`, and the app-server.
- **A warning for anyone testing a build of this branch:** run it with a scratch `HOME`. On a machine still laid out for Puffin, its first run of any kind, `--help` included, performs the rename migration (RENAME_MIGHTLING §4.2).
