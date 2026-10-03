# Puffin Desktop — the Codex desktop app's shape, on `puffin app-server`

**Status:** proposed on 2026-10-03. Nothing here is built. §1 was read from OpenAI's own Linux package (`chatgpt_arm64.deb` 26.930.31730, unpacked, not installed or run) and from the pinned Codex source (`rust-v0.158.0`); §3's CodexMonitor facts are from its repository, not from a build. Every claim about how either app *behaves* is marked as read, not run, and Phase 0 (§10) runs them.

**Goal:** make Puffin's desktop app work the way OpenAI's Codex desktop app does — a desktop client that drives the agent through Codex's **app-server** (JSON-RPC over stdio), with the same projects → threads → turns model, the same composer, approvals, diffs, worktrees, review and terminal — and look and feel as close to it as Puffin's constraints allow (§2). Today `puffin-app` is a window on the Onyx web chat (DREAMFERENCE_ONYX, `desktop/`); it has no coding agent in it at all.

**Builds on:**
- `puffin` itself: the launcher in `puffin-rs/` resolves the model server, writes the model catalog, the prompt blocks and `config.toml`, and every Codex hook Puffin has (cave mode's extension is already registered in the app-server, patch `0017`; MCP tools as plain functions, `0020`; the air-gap sandbox hook, `0019`) applies to app-server clients because they are Codex's;
- the Tauri shell in `desktop/` and its hard-won WebKitGTK settings (CLAUDE.md, "The webview's own environment lives in `src-tauri/src/main.rs`");
- `puffin app`, the launcher's entry to the window (`puffin-rs/src/app.rs`);
- PUFFIN_APPS (Gmail and Drive through `/apps`, on branch `spec/puffin-apps`), PUFFIN_NIGHT_SHIFT, PUFFIN_AIRGAPPED, PUFFIN_NODE.

**Needs no Codex patch for Phases 1–2,** with one exception that Phase 0 decides (§7.2, the air-gap check on `thread/start`).

---

## 1. What OpenAI's app is (read from the package, 2026-10-03)

| What | Found |
|---|---|
| Package | `chatgpt` 26.930.31730, arm64, maintainer OpenAI, homepage `developers.openai.com/codex/app`; 1.5 GB installed. Adds OpenAI's apt repository and an AppArmor profile |
| Shell | An Electron app on OpenAI's "owl" Chromium runtime (`owl-electron-app.json`: packaged from `codex/codex-apps/electron` in OpenAI's private monorepo). User data directory `Codex`. Main process in `app.asar` (`.vite/build/*.js`), UI a React bundle under `webview/` |
| The agent | **A bundled `codex` binary** (`resources/codex`, `codex-cli 0.160.0`, statically linked), plus `codex-code-mode-host`, `rg` and `tectonic` (LaTeX) beside it |
| How it runs the agent | Spawns **`codex -c features.code_mode_host=true app-server --analytics-default-enabled`** and speaks JSON-RPC over **stdio** (`kind: stdio`). Alternatives in the same code: a WebSocket URL (`CODEX_APP_SERVER_WS_URL` or `hostConfig.websocket_url`), a shared local daemon (`codex app-server daemon`, behind `CODEX_APP_SERVER_USE_LOCAL_DAEMON=1`), and host kinds `local`, `wsl`, `ssh` and `remote-control` |
| Which binary | **`CODEX_CLI_PATH` overrides the bundled one**, as does `hostConfig.codex_cli_command`. `CODEX_APP_SERVER_CHATGPT_BASE_URL` and `CODEX_APP_SERVER_OPENAI_BASE_URL` become `-c chatgpt_base_url=…` / `-c openai_base_url=…` |
| Handshake | `initialize` with `clientInfo: {name: "codex_desktop", title: "Codex Desktop"}` and `capabilities: {experimentalApi: true}`. The app-server treats a stdio client named `Codex Desktop` specially only for device user-verification (`initialize_processor.rs:107`), and on Windows for uninstall registration |
| Sign-in gate | The UI computes `requiresAuth = account.requiresOpenaiAuth ?? true`. The app-server answers `account/read` with `requiresOpenaiAuth = config.model_provider.requires_openai_auth` (`account_processor.rs:1051`), which is **false for a local provider**. So the gate is the provider's, not the UI's — read, not run |
| Bundled extras | Plugins `browser`, `chrome`, `code-review`, `codex-app-tools`, `deep-research`, `latex`, `unified-computer-use`, `visualize`; a computer-use runtime (`cua_node`, 203 MB); skills; notification sounds |
| Telemetry | The UI bundles reference Statsig 189 times, Sentry 130 times and `ab.chatgpt` 4 times; the agent is started with `--analytics-default-enabled` |

The app-server it drives is the one in our submodule. Its client surface (`app-server-protocol/schema/typescript/ClientRequest.ts`, 637 generated v2 types) covers everything the app shows: `thread/start|resume|fork|archive|delete|list|read|name/set|revert|compact/start|shellCommand`, `turn/start|steer|interrupt`, `review/start`, `model/list`, `skills/list`, `plugin/*`, `marketplace/*`, `app/*`, `fs/*` (read, write, watch), `command/exec` (with stdin, resize and terminate: a terminal), `config/read`, `config/value/write`, `mcpServerStatus/list`, `permissionProfile/list`, `account/*`, `feedback/upload`. Approvals and streamed items come back as server requests and notifications. `codex app-server generate-ts` writes these types for whatever binary runs it.

**What follows from this:** the Codex desktop app is a client of a published, open protocol, served by an open binary. Everything that makes it a *coding agent* is on our side of that line already. What is OpenAI's own is the window: its layout, its interaction design and its proprietary extras.

---

## 2. What "identical" can and cannot mean here

| Constraint | Consequence |
|---|---|
| Puffin talks to no OpenAI service (patches `0013`, `0015`, `0016`; the egress audit) | No ChatGPT sign-in, no `codex_apps`, no cloud tasks, no rate-limit or credits UI, no Statsig, no Sentry |
| OpenAI's app is proprietary and not ours to redistribute | Puffin cannot ship it, its webview bundle, its icons, sounds or strings |
| Puffin is AGPL-3.0 | Anything we fork must be licence-compatible (MIT and Apache-2.0 are) |
| The model is local and smaller | Features that need OpenAI's hosted models (image generation, deep research, OpenAI's computer-use model) have nothing to call |
| The webview is WebKitGTK, not Chromium | The in-app browser and Chromium-specific behaviour cannot be reproduced one to one |

So "as close as possible" means: **the same architecture** (an app-server client over stdio), **the same information architecture and interaction model** (projects, threads, a composer, item-by-item streaming, inline approvals, a diff and review pane, worktrees per thread, terminal tabs, settings that write `config.toml`), **the same keyboard shortcuts and slash commands where they apply**, and **sessions shared with the TUI** as OpenAI's app shares them with its CLI. Not the same pixels, and not OpenAI's trade dress: Puffin's own name, icon (`OnyxBrandAssets.render_app_icon()`), colours and copy.

---

## 3. Three ways to get there

### 3.1 Run OpenAI's app on `puffin` — rejected

`CODEX_CLI_PATH=~/.local/bin/puffin chatgpt` would, as read, start `puffin app-server` under OpenAI's window, and the local provider would switch the sign-in gate off. It is the most faithful option and must not be the product:
- the window itself is OpenAI's: its own Statsig and Sentry clients, its own updater and apt repository, and a ChatGPT-first UI. The egress audit would fail on the window even if the agent never left the machine;
- it passes `--analytics-default-enabled` to the agent;
- it cannot be shipped, so every user would install OpenAI's package and configure an environment variable.

It is still useful once, as the **reference** (§10, Phase 0): run it against `puffin app-server` on a scratch home with the network namespace cut, and record each screen it reaches and each request it sends. That is how "as close as possible" gets measured instead of guessed. Nothing from it goes into Puffin.

### 3.2 Fork CodexMonitor — chosen as the starting point

[CodexMonitor](https://github.com/Dimillian/CodexMonitor) (Thomas Ricouard, **MIT**) is an open-source desktop client of the Codex app-server: Tauri with a Rust backend and a TypeScript/React/Vite frontend, which is Puffin's desktop stack already. It spawns `codex app-server` over stdio with a configurable binary path, and has workspaces and threads (resume, pin, rename, archive), per-thread drafts and interrupt, worktree and clone agents, git diff stats, branches and GitHub PR and issue views, a file tree, a prompt library, image attachments, autocomplete for skills, prompts and paths, a model picker, Whisper dictation, a terminal dock, and a remote daemon mode. It builds for Linux.

Forking it saves the protocol plumbing and most of the panels; what changes is the **layout**, reshaped to the Codex app's (§5), the brand, and everything §6 adds. Its differences from OpenAI's app (one app-server per *workspace*, a "monitor" sidebar) are UI decisions, not protocol ones.

### 3.3 Write it from scratch — the fallback

Tauri + React on types generated from our pinned binary (`puffin app-server generate-ts`). Cleanest code, every layout decision ours, and about three times the work. It is the fallback if Phase 0 finds CodexMonitor's structure fights the Codex app's layout more than it helps, or its protocol use is too far from our pinned 0.158.0.

**Either way, the protocol types are generated from the `puffin` binary the release ships,** never copied from CodexMonitor's or OpenAI's newer Codex: OpenAI's app bundles 0.160.0 and our pin is 0.158.0, and the protocol moves between releases. A test regenerates them at build time and fails on a diff.

---

## 4. Architecture

```
puffin-app (Tauri)
 ├─ Work  — bundled React UI ──Tauri IPC──► Rust bridge ──stdio JSON-RPC──► puffin app-server
 │                                                                            (the launcher, then Codex)
 └─ Chat  — webview on http://localhost:3000/app (the Onyx web UI, as today)
```

- **One `puffin app-server` per window,** as OpenAI's app does: threads carry their own `cwd`, so one server serves every project. (CodexMonitor runs one per workspace; Phase 0 measures what each costs. The app-server itself is small; the model is not in it.)
- **Started through the launcher, never as bare `codex`:** `puffin app-server`. The launcher is what makes it Puffin: the model server tier resolution (node, `node.json`, mDNS), the model catalog with Puffin's prompt blocks, `CODEX_HOME=~/.puffin`, `chatgpt_base_url` pointed at a closed port, skills links, the air-gap seal, `/prompt`'s choice. **Phase 0 must check** that `app-server` is not in `COMMANDS_WITHOUT_MODEL` for good reason (it needs the model), that the `--oss --local-provider … -c model_provider=… --model …` the launcher puts in front of the arguments is accepted with `app-server` after it (the `-c` certainly is; `--oss` and `--model` are TUI options and may be refused or ignored, in which case the launcher must pass `-c model=…` instead for this subcommand), and that **nothing the launcher prints reaches stdout** — the waiting dots, the start-up line at `on`, the night-run summary. Stdout is the JSON-RPC channel; one stray line breaks the handshake.
- **The wait for the model server** (up to 600 s on a cold load) happens inside the launcher before Codex starts. The bridge reads the launcher's stderr and shows it as the app's start-up screen ("Waiting for the model server on …"), as OpenAI's app shows its slow-start window.
- **`initialize`** with `clientInfo: {name: "puffin_desktop", title: "Puffin Desktop", version}` and `experimentalApi: true` (thread sections, attachments and `process/spawn` need it). Not `Codex Desktop`: that name unlocks device user-verification only, and claiming OpenAI's client identity is not ours to do.
- **Chat stays.** OpenAI's app has a Chat/Work toggle above the composer; Puffin's maps it to its two halves: Work is the app-server client, Chat is the Onyx web UI that `puffin-app` shows today, in a second webview. Everything Onyx's patches do keeps working, and the forwarder, `discover.rs` and the auto sign-in stay as they are for Chat.
- **On a client machine** the app-server runs locally (the client has `puffin`) and the launcher finds the node's model server as it does for the TUI; the Chat webview keeps using the forwarder. A Phase 3 option is OpenAI's `ssh` host kind: `ssh <node> puffin app-server` as the stdio transport, so the agent runs where the code is.

---

## 5. Surface by surface

| Codex app surface | App-server | Puffin |
|---|---|---|
| Project sidebar, threads per project, pin, rename, archive, sections | `thread/list`, `threadSection/*`, `thread/name/set`, `thread/archive`, `thread/metadata/update` | **Same.** Threads from `puffin` in a terminal appear here and back, because both read `~/.puffin/sessions` |
| Composer: text, `@` file mentions, images, attachments, model and effort picker | `turn/start`, `thread/attachment/*`, `fs/readDirectory`, `model/list` | **Same.** `model/list` shows the served model only; efforts are the ones Qwen3.8's patched template accepts |
| Streamed reply: reasoning, commands with output, file edits, plan updates | item notifications | **Same** |
| Steer while running, stop | `turn/steer`, `turn/interrupt` | **Same** |
| Inline approvals (command, patch, network) | server requests | **Same.** The air-gap and Full Access rules hold here too (§7) |
| Permissions picker (Read only / Default / Full Access) | `permissionProfile/list`, thread `sandbox` | **Same,** with Full Access disabled at `on` and the reason shown, as `/permissions` does in the TUI |
| Diff pane, revert | `thread/revert`, git | **Same** |
| Review (`/review`) | `review/start` | **Same,** local model |
| Worktree per thread | `worktree` crate, git | **Same** idea; Night Shift's worktree conventions (`night/<id>`) stay separate |
| Terminal tabs | `command/exec`, `command/exec/write|resize|terminate` | **Same,** run through Codex's sandbox when the thread's profile says so |
| Skills, plugins, marketplaces | `skills/list`, `plugin/*`, `marketplace/*` | **Same,** with Puffin's skill links (Claude, Gemini, OpenClaw, Hermes) listed as sources; OpenAI's featured catalogue stays closed (patch `0015`) |
| Apps (Gmail, Drive …) | `app/list|read|installed` | **Puffin's own,** from PUFFIN_APPS: local sign-in, read-only tools, nothing through `chatgpt.com` |
| Scheduled tasks / automations | the app's own scheduler | **Night Shift:** `/night add` from the composer, the queue as a panel, the morning report as a thread-like page. Driven by `puffin night …`; no new scheduler |
| Settings | `config/read`, `config/value/write` | **Same,** writing `~/.puffin/config.toml` (with `toml_edit` semantics the launcher already relies on) |
| Slash commands in the composer | client-side | The Codex ones the app has, plus Puffin's: `/airgapped`, `/cavemode`, `/night`, `/prompt`, `/apps`. Each is a call to the launcher's own command (`puffin airgapped`, `puffin night`, `puffin prompt` …), so the TUI and the app cannot drift |
| Voice dictation | the app's own | Puffin's speech-to-text sidecar (`dream-stt`, CPU Whisper), already running for the web UI |
| Notifications when a turn ends | the app's own | Desktop notification, no bundled sounds |
| Quick chat, Chat mode | ChatGPT | **The Onyx web UI** (§4) |
| Artifact viewer (documents, spreadsheets, images) | `fs/readFile` | Phase 3: Markdown, images and PDF only |
| ChatGPT sign-in, plan, credits, rate limits, workspace messages | `account/*` | **Not shown.** `account/read` answers `requiresOpenaiAuth: false`, so there is nothing to sign in to |
| Cloud tasks, remote control | OpenAI's services | **Not offered** (`cloud` is refused by the launcher) |
| Computer use, in-app browser, image generation, deep research | OpenAI's bundled plugins and models | **Not offered.** No local model or runtime for them; the browser is Chromium-only |
| Feedback upload | `feedback/upload` | **Not offered:** it uploads to OpenAI |

---

## 6. What Puffin adds, in the Codex app's idioms

- **Air-gap status** in the window's title bar, as the TUI's status line shows it: `off` or `on`, with the seal held or not, and the `NOT ENFORCED` reasons a click away.
- **The node:** which model server this window uses (this machine or a node found on the network), from the launcher's `node.json`, with `puffin node use` behind a picker on a client.
- **The code index:** its state per project (`puffin-code status`), and its `code_*` tools rendered like any other tool call.
- **The model server's state** (starting, serving, stopped) from `/v1/models`, with `puffin-admin server start` as the action when it is stopped, since without it nothing works.

---

## 7. Security and privacy

### 7.1 The window

- **No telemetry SDK** of any kind in the bundle, no remote fonts or scripts (the Content-Security-Policy allows `self` and the Tauri IPC only; Chat's webview keeps its own origin), no auto-updater: updates come from `puffin update`, as for the binaries.
- **The egress audit grows an `--app` mode:** trace `puffin-app` plus its `puffin app-server` through one scripted turn, with the same verdict rules. It must pass before a release ships the app.
- The bridge passes JSON-RPC only between the bundled UI and the app-server; the Chat webview has no access to it.

### 7.2 The air-gap rule must hold in the server, not the window

The Full Access refusal at `on` (PUFFIN_AIRGAPPED §14.7) is enforced in the launcher's argument check and in the TUI's two permission pickers. **An app-server client chooses the sandbox per thread** (`thread/start`'s `sandbox`, `turn/start` overrides), so a desktop UI that only greys out a menu item enforces nothing: any other app-server client, or a bug, gets Full Access at `on`. Phase 0 reads whether the launcher extension the app-server already registers (patch `0017`'s registry) can refuse a `thread/start` or `turn/start`; if it can, the rule moves there with no new patch; if not, it is one hook in the app-server's thread-start path, the same size as `0019`'s.

### 7.3 Sessions

Threads the app starts are ordinary Codex sessions in `~/.puffin/sessions`: Night Shift's "an open `puffin` session holds the runner back" must count an app-server with a live turn as such a session. Phase 0 checks how Night Shift detects one today.

---

## 8. The webview: what `desktop/` already learned

Every setting in `desktop/src-tauri/src/main.rs` carries over: `WEBKIT_DISABLE_DMABUF_RENDERER=1` (no window at all without it under the NVIDIA driver), `GTK_THEME=Adwaita:light` until the Work UI has a dark theme of its own (then it follows the system, as OpenAI's app does), the white `backgroundColor`, and **`target="_blank"` does nothing** in this window: links to the web go through `tauri-plugin-opener`. CodexMonitor was built on macOS first; its Linux build is untested here and Phase 0 builds it on the GB10.

---

## 9. Packaging

- The app stays `puffin-app` (`dev.dreamference.puffin`), built by `puffin-admin desktop build` and attached to releases as today; `install.sh` installs it on a client and a node alike.
- **`puffin app [folder]`** opens the window **on that project**, as `codex app <folder>` does upstream; today the launcher ignores the folder with a note (`app.rs`).
- Its frontend is built with the repository's pinned Node toolchain; the `node_modules` tree is not committed. CodexMonitor's code enters under `desktop/` with its MIT notice kept in `desktop/LICENSES/`.

---

## 10. Phases

**Phase 0 — check, before any UI work (about two days).**
1. `puffin app-server` from a shell: the arguments the launcher prepends are accepted, stdout carries JSON-RPC only, `initialize` → `account/read` (`requiresOpenaiAuth: false`) → `model/list` (the served model) → `thread/start` → `turn/start` works against the GB10's model, and the thread appears in `puffin resume`'s list.
2. The reference run (§3.1): OpenAI's app with `CODEX_CLI_PATH` pointing at `puffin`, on a scratch `HOME`, inside a network namespace that reaches only the model server. Record which screens open, which requests the app-server receives, and what fails. Uninstall afterwards.
3. CodexMonitor at a pinned commit: build on the GB10, point it at `puffin app-server`, run one thread. List what breaks against our 0.158.0 protocol.
4. §7.2: can the registered launcher extension refuse a `thread/start`?
5. §7.3: does Night Shift see an app-server session?
6. Memory: an idle and a busy app-server, CodexMonitor's per-workspace model against one per window.

*Done when* each has a recorded answer here, and the fork-or-scratch choice of §3 is confirmed.

**Phase 1 — Work mode.** The bridge, the project and thread sidebar, the composer, streaming items, approvals, the permissions picker with the air-gap rule, the diff pane, interrupt and steer, model and effort pickers, settings, the start-up screen, Chat as the second mode, `puffin app [folder]`, the egress audit's `--app` mode. *Done when* a task started in the app can be resumed in the TUI and the other way round, and the audit passes.

**Phase 2 — the rest of the Codex surface.** Worktree per thread, review, terminal tabs, skills, plugins and apps pages (PUFFIN_APPS), Night Shift as scheduled tasks, Puffin's slash commands, dictation, notifications, the air-gap and node indicators.

**Phase 3 — optional.** The `ssh` host kind to a node, the artifact viewer, macOS and Windows builds.

**Not proposed:** ChatGPT sign-in, cloud tasks, OpenAI's bundled plugins, computer use, the in-app browser, image generation, feedback upload, any telemetry.

---

## 11. Tests

- The bridge: JSON-RPC framing, a server that dies mid-turn, a launcher that is still waiting for the model, stray stdout from the launcher (must fail loudly in the test, not hang).
- The generated protocol types match `puffin app-server generate-ts` of the pinned binary (a diff fails the build).
- The UI against a scripted app-server stand-in (as Night Shift's tests script `puffin`): a full turn with a command, a patch, an approval and an interrupt; the Full Access item disabled at `on`.
- The egress audit's `--app` fixture, as `--tui` has.
- No test starts the real window, a real container or the model server, per `tests/conftest.py`.

---

## 12. Risks

- **The protocol moves.** Codex's app-server changes with every release; every Codex bump now also has a desktop UI to re-check. The generated types make the breakage a compile error rather than a silent one.
- **CodexMonitor moves too,** and follows newer Codex than our pin. The fork is pinned and owned; upstream fixes are taken by hand.
- **"Close to the Codex app" is a moving target** OpenAI ships weekly. The reference is the version recorded in Phase 0; parity with later versions is a decision each time, not a promise.
- **Trade dress.** Layout and interaction can follow OpenAI's; name, icon, colours, sounds and copy must not.
- **WebKitGTK** has repeatedly behaved differently from Chromium here (the scrollbar, `target=_blank`, DMABUF). A React app built and tested in Chromium will meet more of it.

---

## 13. Open questions

1. **Fork CodexMonitor or start from scratch?** Proposed: fork, confirmed by Phase 0 item 3.
2. **Does Chat stay inside the app** as the second mode, or does `puffin-app` become Work only, with the web UI left to the browser?
3. **Dark mode:** follow the system in Work (every Onyx override is `html:not(.dark)`-scoped, so Chat would stay light)?
4. **One app-server per window or per project?**
5. **Is installing OpenAI's package for the one reference run (§3.1) acceptable** on this machine? It adds OpenAI's apt repository unless removed.

---

## Sources

- OpenAI's package, unpacked: `~/Downloads/chatgpt_arm64.deb` (26.930.31730, SHA-256 `dd980085e9746fad8bd45b48354885d2ea3a9ad09889e0f0d6232f1d819b26b2`); `resources/app.asar` main-process files `application-network-startup-*.js`, `bootstrap-*.js`, `main-*.js`.
- Codex `rust-v0.158.0`: `codex-rs/app-server/README.md`, `app-server/src/request_processors/initialize_processor.rs`, `account_processor.rs`, `app-server-protocol/schema/typescript/`.
- [CodexMonitor](https://github.com/Dimillian/CodexMonitor) and its [LICENSE](https://github.com/Dimillian/CodexMonitor/blob/main/LICENSE) (MIT).
- [Codex app documentation](https://learn.chatgpt.com/docs/app) (OpenAI).
- [Inside the Codex App Workspace: PR Review Pane, Task Sidebar, and Artifact Viewer](https://codex.danielvaughan.com/2026/04/17/codex-app-workspace-pr-review-task-sidebar-artifact-viewer/) and [Mastering the Codex Desktop App: Automations, Triggers and the Review Queue](https://codex.danielvaughan.com/2026/04/08/codex-desktop-automations/) (third-party).
- [Best Codex GUI 2026: 4 Codex Desktop Apps Compared](https://dev.to/stravukarl/best-codex-gui-2026-4-codex-desktop-apps-compared-4c8c) (third-party).
