# Mightling Ask, the Mightling web server, and retiring Onyx

**Status:** proposed (2026-10-07); Phase A partly built, see §16, §17 (2026-10-08) and §18 (2026-10-09); **Phase C built** (Onyx retired, image search and voice moved, branch `chat/onyx-phase-c`, 2026-10-09, merged after 1.6.0, ships in **1.7.0**, the user's decision of 2026-10-09), see §19. The user decided the same day: **drop Onyx, keep a web UI.** This spec replaces everything Onyx does for the product with Mightling's own pieces, keeps a browser UI, and adds what Onyx Lite never did here: search over the user's own files.
**Names:** written with the post-rename names ([RENAME_MIGHTLING](./DREAMFERENCE_RENAME_MIGHTLING.md), branch `rename/mightling`): `ling`, `ling-admin`, `ling-search`, `ling-fetch`, `ling-code`, `ling-app`, `~/.mightling`, `mightling_*` settings, `_mightling-node._tcp`. Where `main` still says Puffin, read `puffin` for `ling`.
**Builds on:**
- [MIGHTLING_DESKTOP](./DREAMFERENCE_MIGHTLING_DESKTOP.md): the Work window on `ling app-server`, the bridge's allow-list (§4.3), the air-gap rule in the server (§8.2, patch `0023`), Night Shift's busy marker (§8.3); and its Electron rebuild (branch `desktop/electron`), which copies the upstream vendor's desktop app;
- [MIGHTLING_APPS](./DREAMFERENCE_MIGHTLING_APPS.md): Gmail, Drive and Calendar through `/apps`, served by the Google service on port 8767;
- [ONYX](./DREAMFERENCE_ONYX.md): what is being replaced, feature by feature (§1 below);
- [IMAGE_SEARCH](./DREAMFERENCE_IMAGE_SEARCH.md), [MIGHTLING_GMAIL](./DREAMFERENCE_MIGHTLING_GMAIL.md), [GOA](./DREAMFERENCE_GOA.md): the sidecars that stay;
- [CONTEXT](./DREAMFERENCE_CONTEXT.md) and [MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md): the indexing machinery the document index reuses;
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md): what an advertised node publishes; [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md); [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md); the security review of 2026-10 (branch `security/review-1`).

---

## 0. Decisions, stated first

1. **One agent, no second chat backend.** Chat becomes an **Ask thread**: a conversation with the same `ling` agent, in a scratch folder instead of a repository. This is how the upstream vendor's desktop app does chat: it has no separate chat server either (MIGHTLING_DESKTOP §1).
2. **One UI, two hosts.** The Work UI in `desktop/ui/` becomes the **Mightling UI** (Work and Ask threads, settings, apps). The Electron app shows it over `app://`; a new **web server, `ling web`**, serves the same build to a browser. The UI talks through one message interface, so the same code runs in both (the upstream vendor's app re-dispatches its IPC as window `MessageEvent`s for exactly this reason).
3. **One bridge policy.** The allow-list, the dropped thread fields and the "answer only pending requests" rule (MIGHTLING_DESKTOP §4.3, `desktop/bridge/src/lib.rs`) move into one data file, `desktop/bridge/policy.json`, enforced by the Rust bridge crate (inside `ling web`) and by the Electron main process. Both run the same conformance vectors, so the two cannot drift.
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
| The "Mightling" assistant persona and its instructions (ONYX §3) | `MIGHTLING_ASSISTANT_INSTRUCTIONS` | The named prompt **`ask`** (`ling-rs/prompts/ask.md`), chosen per thread by the bridge (§3.2) | A |
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

**Other code that leans on Onyx, and must be moved before Phase C** (all moved, §19.1):
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

`ling-rs/prompts/ask.md`, a named prompt (MIGHTLING_PROMPT), composed with the launcher's `web`, `email` and `code` blocks. It says:
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

Browsers allow the microphone and the clipboard API only on HTTPS or `localhost`. Over plain HTTP from a phone (`http://192.168.0.105:3100`), voice and "copy" don't work. Options, in order:
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

- **Indexing** is done by `ling-admin docs index` on the node, admitted against the same host-wide memory budget as `ling-code` and run in a network-less bwrap sandbox inside `ling-index.slice` (MIGHTLING_CODE_INDEX's three rules). Extraction is Python (`pypdf` for PDFs, `python-docx`, the standard library's `email` and `html.parser`), decided in Phase 0 by quality on a sample of real files rather than by language preference.
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
| **B** | the one after (1.7); not shipped on its own: folded into 1.7.0 with Phase C (decided 2026-10-09) | `ling-admin chat export`; the app's Chat entry opens Ask; installers stop installing Onyx | **Opt-in:** `ling-admin chat start` installs and starts it; existing installs keep it running until the user runs `ling-admin chat retire` |
| **C** | **1.7.0** (the user's decision of 2026-10-09; first planned for 1.8); built 2026-10-09 (§19), merged after 1.6.0 | Onyx code removed | **Gone:** on upgrade, `ling-admin` offers to stop and remove the containers; the volumes are kept until the user confirms |

**For the 1.7.0 release notes** (Phase C ships in 1.7.0; no 1.7.0 notes file exists yet, so the line is kept here until one does):
- Saved Onyx chats aren't exported; `ling-admin chat remove` keeps them until you choose to delete them.

**The history export (Phase B):** `ling-admin chat export` reads every chat session through Onyx's own API (with the per-install admin password) and writes each as a Markdown file in `~/.mightling/ask/imported/<date>-<title>.md`, with the question, answer and citations. It adds that folder to `ling-docs`, so old chats are searchable from Ask. They aren't converted into live threads, because the app-server's thread format isn't a stable public schema.

**What Phase C deletes** (done, §19.3):
- `dreamference/chat/onyx_runner.py`, `onyx_installer.py`, `onyx_brand_assets.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_overrides.py`, `onyx_ui_scripts.py`, and their tests;
- the `chat` command group's Onyx subcommands and `server start --no-onyx` (the flag was `main-model set`'s);
- the Onyx notes (`docs/dev/onyx.md`, `docs/dev/onyx-ui-patches.md`) and the docs' web-chat page. [ONYX](./DREAMFERENCE_ONYX.md) is kept as history, marked retired.

**Rollback:** until Phase C ships, `ling-admin chat start` brings Onyx back with the user's data. After it, a release before Phase C does: its volumes are kept until the user deletes them (§19.4).

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

1. ~~Port~~ **Decided 2026-10-07:** 3100 for now (the user may revisit it; keep the port in one constant and the node advert, so a change is one edit).
2. ~~Voice from phones~~ **Decided 2026-10-07:** text only from other devices' browsers; voice through the desktop app (§4.4).
3. ~~Onyx history export~~ **Decided 2026-10-07:** not needed; a clean start. Phase B drops the export.
4. ~~Phase A's scope~~ **Decided 2026-10-07:** `ling-docs` ships on its own as soon as it is ready (agent and command line); Ask and `ling web` pick it up when they land.

---

## 15. Changes to other specs when this is built

- **MIGHTLING_DESKTOP:** Chat becomes Ask; `policy.json` replaces the hard-coded lists; the forwarder is retired.
- **ONYX:** marked retired in Phase C, kept as history (done, §19).
- **MIGHTLING_NODE:** the advertised `web` port becomes 3100; §5's "the web UI has one account" risk is replaced by pairing.
- **MIGHTLING_APPS:** Connect is linked from the UI's Settings → Apps rather than Onyx's injected button.
- **IMAGE_SEARCH:** an MCP tool instead of an Onyx custom tool; served by `ling web` (built, §19.1).
- **MIGHTLING_EGRESS:** the `--web` mode.
- **ARCHITECTURE, CLI, SETUP, AGENTS.md:** the web UI is `ling web`; the `chat` command group shrinks, then goes (shrunk to `remove` and `status`, §19.4).

---

## 16. What was built (Phase A, first part: branch `ask/phase-a`)

Built: the bridge policy as data, Ask threads, and `ling web` with its credentials. Not built yet: the UI's Ask view, voice, images, `ling-docs`, and the Onyx steps.

**The policy is data, and both hosts are held to the same cases.** `ling-rs/web/policy.json` holds the allow-lists, the dropped fields, the named prompts and the Ask rules. `ling-rs/web/vectors/outgoing.json` holds the conformance cases: 26 of them, each with fixed stubs (a prompt composes to `PROMPT:<name>`, folders are `SCRATCH/n`, threads `ask-*` are Ask threads). They live beside the crate, not in `desktop/bridge/`, because the build copies only `ling-rs/` into the Codex export. The desktop app should read the same file and run the same cases (built in §18). Five things differ from §2.3 and §3.1, all found while checking the pinned protocol (`app-server-protocol/src/protocol/v2/thread.rs`):

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
- **The embedded UI.** `build.rs` embeds the UI from `LING_WEB_UI_DIST`, but no build sets it yet, so `/` serves a placeholder until the desktop branch's `desktop/ui` is merged and the builder points at its `dist`. (Built in §17.)
- **Pairing extras.** No QR code, and no `ling-admin web enable`; `ling web start` writes and starts the user unit itself.
- **The node advert.** It still says `web=3000` (Onyx); it moves to 3100 when Onyx stops being the default (§10, Phase B). (Built in §17.)

**Verified on this machine (2026-10-07).**

- **Web crate:** `cargo test` in a copy of `ling-rs/web`: 22 unit tests and 11 server tests. The server tests cover the credential on every route, pairing (one use, expiry, withdrawal after ten wrong codes), `Host` and `Origin`, upload caps, and a tab through the policy to a stand-in app-server on a Unix socket. They also check that the owner token opens nothing but `/healthz`. They use no real network and no real `ling`.
- **Launcher:** `cargo test -p ling-launcher -p ling-web-server` in an export of the pinned Codex with the patches applied: 163 launcher tests, including the `ask` prompt.
- **Egress:** `ling-admin audit egress --web`, with a debug build from that export and the served Qwen3.8 model, **passes**. It connected only to the model server (3×) and the Gmail service (2×, the `ask` prompt's email block), sent no DNS query, and started `ling` three times: the server, `prompt show --composed`, and the app-server.
- **A warning for anyone testing a build of this branch:** run it with a scratch `HOME`. On a machine still laid out for Puffin, its first run of any kind, `--help` included, performs the rename migration (RENAME_MIGHTLING §4.2).

---

## 17. What was built (Phase A, second part: branch `web/ask-ui`, 2026-10-08)

Built: the UI embedded in every `ling` build, the Ask view, attachments, the phone layout, the desktop app's Ask window on `ling web`, and the node advert on 3100. These were the five gaps between `ling web` and the Onyx web UI. Onyx itself is untouched and still runs on 3000.

**The UI is in the binary.** `CodexBrandedBuilder` builds `desktop/ui` before cargo (`npm ci` once, then `npm run build`) and sets `LING_WEB_UI_DIST` for `ling-rs/web/build.rs`. A build without npm stops: a `ling` whose `ling web` serves the placeholder is the bug being fixed. The UI's sources are part of the build key, so a UI change rebuilds `ling`. `build.rs` embeds from an absolute, non-canonical path, which avoids Windows' `\\?\` prefix. The release, client and Windows `ling` jobs set up Node 22. The page's own CSP no longer allows frames from Onyx's ports. The page has a favicon (the app's mark, bundled under `assets/`).

**One page, two hosts, two views.** In `ling web`, the UI shows **Ask** by default and **Work** one click away (`#work`). In the desktop app's Work window (`app://`), it shows Work alone. That window's bridge (`desktop/electron/src/bridge.ts`) does not have the policy layer that turns `prompt: "ask"` into text and a folder, so it cannot run Ask. The page tells its host apart by `window.mightlingWindowType` (`html[data-host]`), and the frameless title bar's padding and drag region apply to the app alone. A browser keeps its own context menu, which a phone needs for copy and paste.

**Ask threads.** "New question" clears the view. The first message starts the thread with `{ model, prompt: "ask" }` and no `cwd`; the policy sets the folder, the sandbox and the composed prompt (§16). `work/start` now answers with `ask_root` (canonical). The UI lists the threads whose `cwd` is a `q-<hex>` folder under that root, and keeps them out of Work's projects. Search uses `thread/search` (with a snippet), rename uses `thread/name/set`, and archive uses `thread/archive`, which also handles the server's `thread/archived` notification.

**Attachments.** The clip button and pasted screenshots (the `paste` event's files) attach files to the next message. They are uploaded only after the thread exists, because `/api/upload` needs the `ask/<thread-id>` link. Images (PNG, JPEG, GIF, WebP) go as `localImage`. Other files are named in the text with their path in the thread's folder. A message may be an image alone. The model's vision is not checked; the served model has it.

**Copy.** Each answer has a copy button. Without `navigator.clipboard` (plain HTTP from another device, §4.4), it falls back to `document.execCommand("copy")` on a hidden textarea. A test runs it with `navigator.clipboard` undefined.

**Phone layout.** Below 720 px, the thread list becomes a drawer behind a ☰ button, with a backdrop that closes it. Inputs are 16 px, so mobile browsers do not zoom in. The chips that do not fit are hidden, and the composer clears the safe area. The page is text only from other devices; nothing asks for the microphone.

**The desktop app's Chat is Ask** (Phase B's "the app's Chat entry opens Ask"). The window (`chat.ts`, menu label "Ask") loads the Mightling UI from `ling web` on this machine, with no preload and no IPC. `web.ts` checks `ling web status`. When nothing answers, it starts `ling web serve` as the app's child and stops it on quit; a server the user runs as a unit is left alone. It then signs the window in with a link from `ling web open --print-url`, a new flag that prints the one-time link alone. A 401 after a server restart gets one fresh sign-in. The window never navigates off that server, and links open in the system browser. On Windows, which has no `ling web` yet, Ask opens Work. The Onyx window's password sign-in (`sign-in.ts`), the loopback forwarder to a node's port 3000 (`forwarder.ts`) and the discovery that fed it (`discover.ts`, `node_locator.ts`, `multicast-dns`) are removed. On a client, Ask runs on the client against the node's model server, as §8 says; the bundled `ling` finds the node. The app grants no permission except clipboard writes (no microphone). `ling app` opens Ask without checking for Onyx (`--ask`, with `--chat` kept as an alias), and so do `ling-admin desktop run` and `status`. `audit egress --app` allows `ling web`'s 3100 instead of 3000.

**The node advert names `ling web`.** `node enable` advertises `web=3100` when `ling` is installed, and runs `ling web start --lan`. `--no-web` and `disable` run `ling web start` again, back on loopback, when the unit was on the LAN. Onyx's own bind (3000 on every interface on a sharing node) is unchanged until Onyx is retired, and the enable notice says both.

**Verified on this machine (2026-10-08)**, with no live service touched:
- `desktop/ui`: 22 vitest cases (11 new: Ask threads, attachments, the copy fallback, archive and rename in the store), typecheck, and the production build.
- `ling-rs/web`: `cargo test` in a copy, with and without the UI embedded: 22 unit and 12 server tests. The new server test serves the embedded page with the bridge first and every asset it names.
- `desktop/electron`: 20 vitest cases, including `web.test.ts` (a running server used as it is; a missing one started, owned and stopped; only a one-time loopback link loaded), and the typecheck.
- Launcher: `cargo test --release -p ling-launcher -p ling-web-server` in a scratch export of the pinned Codex with the patches applied, the UI embedded and a scratch `HOME`: 229 launcher tests on the tree merged with main (with `ling app`'s Ask window), 22 + 12 web tests.
- `CodexBrandedBuilder.build_web_ui()` run for real (npm in `desktop/ui` only; nothing installed, no `ling` run). `ling-admin codex build` was not run here, because it installs into the live folder.
- The Python suite.
- A headless Chrome (playwright-core, a throwaway profile) against a scratch `ling web` built from this branch: the real server crate with the UI embedded, in front of a scripted app-server on a Unix socket, port 3199, a scratch home. All 23 checks passed:
  - sign-in by a one-time link, then Ask as the default view, with no console error and no CSP violation;
  - a question with an image: `thread/start` carried `baseInstructions` from the stand-in composer, the sandbox and an Ask folder, with no `prompt`; the image was written into that folder and sent as `localImage`;
  - the copy button with `navigator.clipboard` removed called `execCommand("copy")`;
  - rename, search with and without a match, and archive;
  - the Work view;
  - at 390 px: no horizontal scroll, the drawer closed, opened and closed again, and 16 px inputs;
  - a browser without a session got 401.

**Not built in this part:**
- **The desktop app's Work window still has no Ask.** Ask needs the policy in the Electron main process (§2.3: `bridge.ts` reading `policy.json` and running the vectors), or Work reaching `ling web`'s socket (§2.1). (Built in §18, the first way.)
- **Two app-servers on one `~/.mightling`.** With `ling web` (socket) and the app's Work (stdio) both running, Phase 0's question 1 is still unmeasured.
- **Images, apps and voice in the UI.** `/images/*`, `/api/apps` and `/api/transcribe` still answer 501. The gallery, Settings → Apps and the microphone are out of scope by the user's decision.
- **Pairing in the UI.** There is no Settings → Devices, and no QR code; `ling web pair` prints the code. (Built in §18.)
- **Retiring Onyx** (§10, Phase C): the `chat` command group, the Onyx sidecars' network, `google_service.py`'s secret, and Onyx's LAN bind on an advertised node.

---

## 18. What was built (Phase A, third part: branch `web/onyx-retire-2`, 2026-10-09)

Built: Ask in the desktop app's own window, and pairing a device by QR code. Two of the gaps listed in §17 before Onyx can go. Onyx is untouched; Phase C is not started.

### 18.1 Ask in the app window: the policy in the main process

**The choice.** §17 named two ways: the policy in the Electron main process (§2.3), or the app window reaching `ling web`'s socket (§2.1). §0.3, §2.3 and §16 all ask for the first, and the second is the open question of §2.1 and Phase 0 question 1 (one app-server or two), which is not decided here. So the main process enforces `policy.json` itself, and the app window keeps its own `ling app-server` over stdio, as Work had.

**One file, one set of cases.** `desktop/electron/src/policy.ts` imports `ling-rs/web/policy.json` at build time (Vite inlines it, as `include_str!` does in the crate), so a packaged app cannot be pointed at another file. It is a line-for-line port of `policy.rs`: the allow-lists, the dropped thread fields, a prompt only on `thread/start`, a named prompt turned into `baseInstructions`, `default` setting nothing, a scratch prompt confining the thread, and a resumed or forked Ask thread kept in its folder. `policy.test.ts` runs every case of `ling-rs/web/vectors/outgoing.json` with the shared stubs, so the two hosts are held to the same 26 cases. The hard-coded lists in `bridge.ts` are gone; the Python test that checks the allow-list against the pinned protocol now reads `policy.json`. One consequence: the app window may now send `app/list`, which `ling web` already allowed.

**Ask folders, as `ling web` makes them** (`desktop/electron/src/ask.ts`, a port of `ask.rs` and of `/api/upload`):
- **The prompt.** Before vetting, `server.ts` runs `ling prompt show <name> --composed` in `$CODEX_HOME/ask`, with the app-server's own `ling` and a 60-second limit, as `prompts.rs` does. The environment is the app-server's without `LOG_FORMAT` and `RUST_LOG`, which belong to the server. The launcher answers `prompt` before Codex parses anything, so no log line can reach stdout anyway. Vetting stays synchronous, so the policy and its cases are the same code.
- **The folder.** It is `ask/q-<16 hex>`, 0700 and canonical. When the server answers the `thread/start`, `ask/<thread-id>` becomes a relative link to it. On Windows it is a junction, which needs no privilege and which the Rust side's `canonicalize` also follows. `ling web` on Windows would write a plain file instead, but `ling web` does not run there.
- **`ask_root`.** `work/start` answers with it, so the page tells Ask threads from projects exactly as in a browser.
- **Attachments.** These go over the same IPC channel as `ask/upload`: the bytes in base64, cut into 1 MB pieces like any large message. The checks are `ling web`'s: the thread must have an Ask folder, images are capped at 20 MB and other files at 100 MB, the file name is cleaned the same way, and nothing is overwritten (`-1`, `-2`, … as on the server). `app://` has no `/api/upload`, so `bridge.upload` picks the channel by host.

**The page.** The page (`desktop/ui`) shows Ask and Work in both hosts. `#work` opens Work and anything else opens Ask, as in `ling web`.
- **Opening on Work.** The app window opens on Work when the menu's Work entry, `--work`, a folder, a thread or a `mightling://` link asked for it.
- **Switching an open window.** A new `view` message on the for-view channel switches a window that is already open.
- **The header's Ask button is gone.** It opened the separate Ask window; Ask is now in the sidebar.
- **The menu.** The menu's Ask entry still opens the Ask window on `ling web` (§17), which the Python tests and `audit egress --app` pin. On Windows, where that window never existed, it now opens the app window on Ask, so Windows has Ask for the first time. (Folded into the app window on every platform in §18.6.)

### 18.2 Pairing by QR code

**The Devices page.** `ling web` serves `/devices` (§4.3's Settings → Devices), linked from the sidebar of the browser host as "Pair a phone or another device". It is a page of the server's own, not a view of the React UI, because pairing exists only where `ling web` runs. It has no script; its forms post back to the server.
- **Who may use it.** Only this machine's session, from `ling web open`, may use it (`Server::owner_session`). That session's link is for loopback.
- **A paired device.** It gets a 403 page telling it to pair or revoke on the Mightling machine. So a stolen phone cookie cannot mint more devices, and a device cannot revoke the others.
- **Checks.** The page needs a credential like every route, and its POSTs pass the `Origin` check.
- **The page's parts.** It lists the paired devices (name, when paired, last use) with a Revoke button for each. **Show a pairing code** answers with the page itself, not a redirect, so the code never sits in a URL on this machine.
- **On loopback only,** it explains that no other device can reach the server and issues no code.

**What the QR code carries.** It is `http://<address>:<port>/pair?code=<8 digits>`: the pairing code of §4.3, valid ten minutes, one use, ten wrong tries withdrawing it. Nothing longer-lived goes in it: never the owner token, a session or a device cookie (a test reads the page for both).
- **Opening the link.** `GET /pair?code=` only fills in the form's field, and only with eight digits; anything else is not shown back. The person still presses Pair, so a link preview or prefetch cannot spend the code.
- **Which address comes first.** The address shown big is the one a phone most likely reaches: 192.168/16 and 10/8, then Tailscale's 100.64/10, then other addresses, then 172.16/12, where Docker's bridges are, then names. `.local` needs mDNS on the phone and does not cross Tailscale. The other addresses are folded underneath, each with its own code, labelled "(Tailscale)" where it applies.
- **Fixed at start.** The addresses are the ones `ling web serve --lan` read at start. An interface that comes up later is not offered until the server restarts.

**Drawn locally.** The QR codes are drawn by `qrcodegen` (Nayuki, MIT, no dependencies), a new dependency of `ling-web-server` (`src/qr.rs`), with ECC level M.
- **As SVG on the page.** The modules are drawn on white whatever the page's theme, and they are inline elements, which the existing CSP allows.
- **As half blocks in the terminal.** `ling web pair` now prints the same code, black on white set explicitly, when stdout is a terminal. It lists the most reachable address first.
- **No service.** No QR service and no script from elsewhere is used.

### 18.3 The two app-servers: what this part learned

This part did not decide §2.1. What it changes, and what was read on the way (the app's own second server is gone since §18.6; the risk below remains for a `ling web` the user runs beside the app):
- **Two app-servers can run on one `~/.mightling` today.** With the app open, both windows are in use: the app window on its own stdio app-server, and the Ask window on `ling web`'s app-server on the socket. Ask threads from either live under the one `~/.mightling/ask/` and are recognised by the same `ask/<thread-id>` links. Because the folder rules are the same, either host can resume the other's Ask thread in its folder.
- **The thread database is built for several connections.** Codex's state databases open read-write pools in WAL mode with a five-second busy timeout (`state/src/sqlite.rs`), which SQLite supports across processes. Read in the submodule's source, not measured.
- **The rollout files may not be.** Codex appends a thread's history to its JSONL rollout file. A search for lock calls (`flock`, `lock_exclusive`, `try_lock_exclusive`, `fs2`, `fd_lock`) in `rollout/` and `core/src` found none. This was read, not measured, in the submodule checkout, not confirmed at the pinned tag. So the risk is the same thread loaded in both servers at once: two writers on one rollout file, each with its own in-memory turn state. The app makes that possible, since one question can be open in the app window and in the Ask window. Neither server knows about the other's turn: the busy markers are per process, and Night Shift reads them all.
- **What Phase 0 question 1 still has to measure.** Run a turn on one thread in both servers, and check the rollout file and `thread/list` from each. Until then, the app window with its own server is what Work had already done since §17. If the answer is "one server", the app window moves to `ling web`'s socket, and this policy layer stays as the guard for the stdio fallback (§2.1).

### 18.4 Verified on this machine (2026-10-09)

No live service was touched: no model server, no installed `ling`, a scratch `HOME` for everything run.
- **`desktop/electron`.** 29 vitest cases, 8 of them new:
  - `policy.test.ts`: the policy is `ling web`'s file, all 26 conformance cases, `namedPrompt`, and a bad policy refused.
  - `ask.test.ts`: folders, links, a planted link out of the root, uploads within the caps and names, the prompt composed by a scripted `ling` in the Ask root, and an Ask thread through `AppServer` end to end. The end-to-end case checks the composed prompt, a fresh folder, the sandbox, the link, an attachment, a resume kept in its folder, a prompt on resume refused, Work left alone, and `feedback/upload` refused.
  - The typecheck and `npm run package` pass. The policy is in the main bundle, and the credentials test ran against the built bundles.
- **The real app.** `npm run e2e`: the packaged main process under Playwright-Electron, windows hidden, a scripted `ling`, a scratch `HOME`. Two runs:
  - Work opens on `app://-/index.html#work`.
  - Clicking Ask, attaching an image and sending sent `thread/start` with the composed prompt, the sandbox and a folder under the Ask root. `ask/thread-1` linked to that folder, and the image was in it and went out as `localImage`.
- **`desktop/ui`.** 26 vitest cases (4 new: the app host's upload over the channel, base64 of a large file, the `view` message, the host check), the typecheck and the production build.
- **`ling-rs/web`.** `cargo test` in a copy, with and without the UI embedded: 30 unit tests and 14 server tests.
  - **New unit tests:** the QR link, the address order, the SVG and the terminal drawing, the Devices page's escaping, its loopback case, the folded addresses, and the eight-digit check.
  - **New server tests:** `/devices` end to end, and a loopback server issuing no code. The end-to-end test covers the owner's page, a code and its QR codes, a foreign `Origin` refused, the prefilled pairing form, and an HTML-looking code not shown back. It then pairs from the code, checks that the paired device is refused the page, a new code and a revoke, and has the owner revoke it. The three new routes are in the credential test.
- **The QR codes decode.** A code drawn by `qr::svg` and by `qr::terminal` for `http://192.168.0.105:3100/pair?code=01234567`, rendered to pixels and read by OpenCV's `QRCodeDetector` in a scratch virtualenv, gave back exactly that link, both ways.
- **The Python suite.**
- **Not run:** the launcher's tests in a Codex export. `ling-rs/src/` did not change; the only change the export sees is the new `qrcodegen` dependency of `ling-web-server`, a leaf crate that Cargo adds to the workspace's lock at the next `codex build`, which does not use `--locked`. Its MIT licence is on the allow-list of the workspace's `deny.toml`, and that file bans nothing it brings.

### 18.5 Still not built before Phase C

- **The menu's Ask window.** It still uses `ling web` and its app-server, a second server on the same home (§18.3). Folding it into the app window is the §2.1 decision. (Built in §18.6.)
- **Images, apps and voice in the UI.** `/images/*`, `/api/apps` and `/api/transcribe` still answer 501; out of scope by the user's decision (§17). (Images and voice built in §19.2; `/api/apps` still answers 501.)
- **The Onyx steps** (all done in §19). Retiring Onyx (§10) still needs these:
  - the `chat` command group;
  - the Onyx sidecars' Docker network;
  - `google_service.py`'s secret, still taken from `OnyxRunner`;
  - Onyx's LAN bind on an advertised node;
  - `node_advertiser.py`'s Onyx `.env` check;
  - the docs' Onyx section.
- **Phase 0's measurements**, question 1 above all.

### 18.6 One window, one app-server in the app (branch `desktop/one-app-server`, 2026-10-09)

**The decision (the user's, 2026-10-09).** The menu's Ask no longer opens a window of its own on `ling web`: it brings the app window forward on Ask. While the app runs, the app itself starts exactly one app-server on `~/.mightling`, the app window's own over stdio (§18.1). This settles §2.1 for the desktop app the first way §17 named, the policy in the main process; `ling web` keeps its own app-server on its socket, for browsers and phones, unchanged.

**What changed in the app** (`desktop/electron/`):
- **One window.** `main.ts` keeps one `BrowserWindow`, the app window, and one `showView(view)`. The menu's Ask and Work (Ctrl+1, Ctrl+2), the tray's, `ling app` alone and a second instance with no target bring it forward on that view (restored if minimised), sending the page the `view` message of §18.1 when it is already open; `--work`, a folder, a thread or a `mightling://` link open it on Work. No path opens a second window.
- **Removed:** `chat.ts` (the Ask window), `web.ts` (finding, starting and stopping `ling web`, the one-time sign-in link) and `web.test.ts`; the `chat` block of `app.json`; the page-to-main `work/open-chat` message, which nothing sent since the header's Ask button went (§18.1); the Windows special case, since every platform now does what Windows did. The window's title is "Mightling" (it opens on Ask; the page's `<title>` was already that).
- **Unchanged:** the policy layer, the Ask folders and uploads (§18.1), `ling app`'s arguments (`ling-rs/src/app.rs`: comments, the help line and one error message only, which no longer name `ling web` or an Ask window), and `ling web`, including `open --print-url`, which no longer has a caller in the app.

**The audit's app mode** (`dreamference/audit/egress_audit.py`). The app is started with no argument, as `ling app` starts it (it was `--work`), in the same hidden session: the window on Ask, its app-server started, quit after 40 seconds. Port 3100 is no longer on the app session's allowlist, which is now the `exec` session's: a regression to a window on `ling web`, and so to a second app-server, shows as a connect to 3100 and fails the audit (a test plays one back). `--app`'s help text and `docs/admin.md` say "its window hidden". The macOS workflow's start check runs the same audit session and needed only its comment changed.

**What this does and does not settle.** The second server the app itself created is gone. A `ling web` the user runs (`ling web start`, or on an advertised node) beside the app is still a second app-server on the same home, so §18.3's risk, two writers on one rollout file when the same thread is open in both, remains for that case, and Phase 0 question 1 is still open for it.

**Verified on this machine (2026-10-09)**, a scratch `HOME` for everything run, no live service touched:
- `desktop/electron`: typecheck; 25 vitest cases (`web.test.ts`'s 5 gone with its module); the bundle check now anchors on the app-server's command line and checks that no `--print-url` is left in the main bundle. `npm run package` passes.
- `npm run e2e` (Playwright-Electron, scripted `ling`, hidden windows): 3 passed. The new case starts the app with no argument and checks that the one window is on `app://-/index.html` (Ask), that the File menu's Work and then Ask switch it (`#work`, then `#`) with `BrowserWindow.getAllWindows()` still 1, and that the stand-in `ling` was run with `app-server` exactly once and never with `web`.
- `desktop/ui`: 26 vitest cases and the typecheck (one comment changed).
- The Python suite (`-k "not codex_branded_builder"`): 931 passed, 89 skipped.
- Not run: the launcher's `cargo test` (`app.rs` changed in comments and two message strings only), and `ling-admin audit egress --app` against a real build (it needs the installed `ling` and a model server).

---

## 19. What was built (Phase C and the minimal §6 and §7: branch `chat/onyx-phase-c`, 2026-10-09)

Built: the "must be moved before Phase C" list of §1, the minimal image search and voice of §6 and §7 (the user's decision of 2026-10-09, after the first pass found them still on Onyx), then Phase C. Merged after the 1.6.0 release, not before, and shipped in 1.7.0 (the user's decision of 2026-10-09). Phase B (`chat export`, `chat retire`) was never built: §14.3 dropped the export, and Phase C follows Phase A directly.

### 19.1 Moved off Onyx first

- **The Google service's secret and header** are `GoogleService.secret()` and `GMAIL_AUTH_HEADER` in `chat/google_service.py`; `gmail_client.py` reads them there, and its hints name `ling-admin google start` and `/apps`.
- **The Docker bridge rewrite** OpenHands needs is `DockerBridge` (`chat/docker_bridge.py`).
- **The desktop entry's icon** is the app's own 256×256 PNG (`desktop/electron/icons`), no longer drawn by the Onyx brand assets.
- **The node advert** no longer reads Onyx's `.env` or rebinds its nginx; `apply_binds` starts `ling web` where the settings say, and `node status` has no Onyx line.
- **The Gmail service** accepts posts from its own origin only (no CORS for port 3000), its connect pages have no link back to Onyx, and its not-connected message names `/apps`.
- Already done before this branch: web search (`ling-search --read`), `/apps`, the desktop app (§18.6), the installers (no Onyx since §17).

### 19.2 Image search and voice, minimal

**Image search (§6).**
- `ling-admin images start [--no-siglip]|stop|status` (`ImageSearchSidecar`): the existing service on `dreamference-sidecars`, its store and secret in `~/.config/dreamference/image-search`, the vision re-rank pointed at the served model's id from `/v1/models`. SigLIP is best-effort and skipped with `--no-siglip` or when it does not start. A container found on another network (Onyx's) is replaced. The spec named only `images mcp`; `start|stop|status` are added, as the Google service has them.
- `ling-admin images mcp` (`chat/image_search_mcp.py`) serves `image_search(queries, count)` over stdio. It runs before the rename migration and the sandbox check, which could print. `initialize` and `tools/list` touch nothing. A call refuses at a configured `on` (`DreamferenceConfig.resolve_airgapped_level`) or when the parent session sealed itself at `on`, reading `ling-airgapped`'s seal folder as `ling-apps`' `sealed_by` does: a second reader of that format, a drift risk like the byte-identical Rust copies.
- The launcher declares it (`ling-rs/src/images.rs`, `-c mcp_servers.mightling_images.…`) on a node with a local model server, the configured air gap not `on`, the secret file present, `~/.local/bin/ling-admin` linked, and `DREAMFERENCE_MIGHTLING_IMAGES` not off. Nothing is probed over the network at start.
- The service now answers `![title](/images/<id>.jpg)`. `ling web` serves `/images/<16 hex>.jpg` read-only from the store (`ling-rs/web/src/sidecars.rs`; links refused, any other name 404), behind the credential like every route. The app's `app://` handler does the same (`imageFile` in `app-protocol.ts`). The page's Markdown shows those images and still no other (`markdown.ts`). The gallery and lightbox were not rebuilt: images are inline.

**Voice (§7).**
- `ling-admin voice start|stop|status` (`SpeechSidecar`): speaches on the CPU on `dreamference-sidecars`, `127.0.0.1:8100`, its model in the volume `dreamference-stt-cache` (kept from Onyx's), one on another network replaced.
- `ling web` answers `POST /api/transcribe` (audio only, 25 MB at most): a multipart request to the sidecar on loopback, `{text}` back, 503 naming `ling-admin voice start` when nothing answers. The app's main process takes `voice/transcribe` over the one IPC channel and does the same with `fetch` (`voice.ts`).
- The composer has a 🎤 button where the page may record (`canRecord`: a secure context with `getUserMedia` and `MediaRecorder`), so in the app and in `ling web` on this machine, never on a phone over plain HTTP (§14.2). It toggles (press, speak, press again) rather than push-to-talk; the text goes into the draft, never sent.
- The app grants the microphone to its own page only, audio only (`grantsRequest`/`grantsCheck` in `egress.ts`); every other permission is still refused.

### 19.3 What was deleted

`onyx_runner.py`, `onyx_installer.py`, `onyx_brand_assets.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_overrides.py`, `onyx_ui_scripts.py`, `chat_admin_credentials.py` and their tests (`test_onyx_runner.py`, `test_onyx_ui_scripts.py`, `test_chat_admin_credentials.py`): about 6,400 lines. Also deleted:
- `main-model set --no-onyx` and its re-registration step;
- the Gmail and image search services' OpenAPI documents (Onyx's custom tools were their only readers);
- the `fonttools` dependency (only the font patch used it);
- conftest's Onyx guards, replaced by one that stubs the offer below;
- `docs/dev/onyx.md` and `docs/dev/onyx-ui-patches.md`.

The docs' web-chat page became `docs/web.md` ("Web UI"): `ling web`, image search and voice, and how to remove Onyx. The ONYX spec is marked retired and kept as history.

### 19.4 What an existing install sees

- **`ling-admin chat …`** (and `onyx …`): every former subcommand prints where its job went and exits 2.
- **`chat status`** lists the containers, volumes, images, the sidecars still on Onyx's network and the deployment folder.
- **`chat remove`** (`RetiredWebChat`, `chat/retired_web_chat.py`) asks twice:
  1. Stop and remove the containers: `docker compose -p onyx … down --remove-orphans`, never `-v`. Without compose files, the containers go by name.
  2. Then, as a separate question, delete the compose project's volumes (saved chats and the Onyx accounts), Onyx's own images (`onyxdotapp/*`; PostgreSQL's and nginx's generic images stay), its networks, and the image search, SigLIP and speech containers still on its network.

  `--yes` answers the first question and `--delete-data` the second, for scripts. A no keeps everything, and `chat remove` asks again later.
- **On upgrade:** an interactive `ling-admin` run on a machine whose `~/.config/onyx/deployment` exists and whose Onyx containers are found makes the same offer once. A no is remembered in `~/.config/dreamference/web-chat-retired.json`. No terminal, no question and no Docker call.
- **Left alone:** the deployment folder (compose files, `.env` with the old admin password), and the `onyx-cli` package in the virtualenv.
- **Gone:** the `/puffin-images/` route goes with nginx (§6 kept it only until Phase C), so saved Onyx chats lose their pictures. The pictures stay in the store, and `ling web` serves them at `/images/`.
- **What moves:** the Gmail container Onyx's `configure` made is adopted by `ling-admin google start` as it is. `ling-admin images start` and `voice start` recreate the other two sidecars on the sidecar network.

### 19.5 Verified on this machine (2026-10-09)

No container, live service, model server or real home was touched.
- **The Python suite:** 897 passed, 91 skipped. One failure comes from the environment: `test_codex_branded_builder.py::…linked_onto_path…` fails because the shared virtualenv still carries the console script `puffin-admin`. It fails the same way without this branch.
- **New tests:**
  - the sidecars against a recording `docker`: networks, ports, mounts, the secret's mode, replacement off Onyx's network, the store kept on `stop`;
  - the MCP protocol: nothing before a call, arguments clamped, refusal at `on` and by the parent's seal, the CLI running it before anything that prints;
  - `chat remove`: no removes nothing; the first yes is `down` without `-v` and keeps volumes, images, networks and folder; the second deletes only Onyx's own; the flags; the offer once and only at a terminal.
- **`ling-rs/web`** (`cargo test` in a copy beside `../airgapped`): 34 unit and 16 server tests. New: images by name only (a link, `..`, an encoded `..`, upper case and another extension refused), and a recording through a stand-in speech service (the form, the text, 415, 400, 503).
- **The launcher** (`cargo test -p ling-launcher` in a scratch export of the pinned Codex with the patches, scratch `HOME`): 235 passed, the five new `images::` tests among them.
- **`desktop/electron`:** typecheck and 28 vitest cases, new: the permission rule, dictation, the image path. **`desktop/ui`:** typecheck, 29 vitest cases, new: stored images shown and no other, dictation over both hosts, `canRecord`; the production build.

**Not verified:** anything that needs a container or a real window.
- The sidecars actually starting on `dreamference-sidecars`;
- a real search through SearXNG and the model's vision;
- a real transcription by speaches;
- the microphone prompt in the packaged app and in a browser;
- `chat remove` against the real Onyx deployment on this machine, which still runs and was not touched;
- `npm run e2e` and `ling-admin audit egress --web` (its image search turn is §19.6).

### 19.6 The user's answers, built the same day

- **`server start` on a node starts image search and voice when they are missing,** as it starts the Google service (`ensure_on_node` on each):
  - Nothing happens on a client, or where the container exists, running or not.
  - Image search starts without SigLIP and without waiting for its health route: its first boot installs Pillow. Speech-to-text fetches its model detached (`docker exec -d`).
  - The vision re-rank is told the id the server will serve, the checkpoint's repository (`vision_model`), since the model is not answering yet.
  - A failure, or an exception from Docker, is a warning naming the command to retry (`ling-admin images start`, `ling-admin voice start`). It never blocks the model start; a test runs `server start` with both failing and checks that the load still runs.
  - conftest stubs both starts, as it stubs the Google service's (`REAL_IMAGE_SEARCH_START`, `REAL_SPEECH_START`).
- **`audit egress --web` has an image search turn** (§9):
  - Where image search is set up (its secret file exists and a `ling-admin` is found), the scratch home is laid out as a node, with a copy of the secret and a `ling-admin` link, so the traced session is offered the tool.
  - A second question asks for one picture, and its MCP server is traced with the app-server.
  - Port 8768 is on the allowlist of every session, and the report and the JSON say `Image search turn: answered with an image | answered, without an image | no answer | skipped: …`.
  - The turn is reported, not judged: the verdict stays the egress one.
  - Not run against the real sidecar here (§19.5).
- **The old docs URL:** `docs/web-chat.md` is a stub that sends the browser to `web/` (a meta refresh and a link, out of the nav and the search), because the docs workflow installs no redirect plugin. `mkdocs build --strict` with the workflow's pins passes.
- **The 1.7.0 release notes** get the line under §10's table.
- **Verified:** the Python suite, 907 passed, 91 skipped, with the same one failure from the environment. One run also failed `test_night_shift.py::…bundled_ling…`, a timing race on a loaded machine; it passed on three reruns and on the full rerun. No Rust or front-end code changed in this part.

