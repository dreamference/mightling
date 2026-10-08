# Mightling Desktop — the Codex desktop app's shape, on `ling app-server`, grown from today's `ling-app`

**Status:** proposed on 2026-10-03, revised the same day after review. **Phase 1's code is written** (branch `desktop/work-window`, §15) but not yet built into `ling-app` or watched live; the rest is proposed. §1 was read from the upstream vendor's own Linux package (`chatgpt_arm64.deb` 26.930.31730, unpacked, not installed or run) and from the pinned Codex source (`rust-v0.158.0`). §2 lists the Codex app's features from the launch announcement (read from the Wayback Machine's copy; the page itself answers 403 and its videos could not be analysed, only their captions) and the upstream vendor's documentation and third-party write-ups. **§5 records what was measured on this GB10 on 2026-10-03** by driving the installed `ling app-server` over stdio from a script; everything else about behaviour is marked as read, not run.

**Goal:** make Mightling's desktop app work the way Codex desktop app does — a desktop client that drives the agent through Codex's **app-server** (JSON-RPC over stdio), with the same projects → threads → turns model, the same composer, approvals, diffs, review, worktrees and terminal — and **get there by growing today's `ling-app` in place, never breaking it**. Today `ling-app` (`desktop/`) is a window on the Onyx web chat with no coding agent in it. That window stays exactly as it is and becomes the app's **Chat** window; the coding agent arrives beside it as the **Work** window.

**Builds on:**
- `ling` itself: the launcher in `ling-rs/` resolves the model server, writes the model catalog, the prompt blocks and `config.toml`; every Codex hook Mightling has (cave mode's extension, patch `0017`; MCP tools as plain functions, `0020`; the air-gap sandbox hook, `0019`) applies to app-server clients because they are Codex's;
- the Tauri shell in `desktop/`: `main.rs` (the WebKitGTK environment), `forwarder.rs` and `discover.rs` (a client reaching its node's web UI), `auto_sign_in.js`;
- `ling app`, the launcher's entry to the window (`ling-rs/src/app.rs`);
- MIGHTLING_APPS (Gmail and Drive through `/apps`), MIGHTLING_NIGHT_SHIFT, MIGHTLING_AIRGAPPED, MIGHTLING_NODE, MIGHTLING_CAVE_MODE, MIGHTLING_SKILLS.

**Codex patches:** none for Phase 1. Phase 2 needs at most two one-line hooks, each counted against the patch cap and decided in Phase 0: the air-gap check on the app-server's thread and turn paths (§8.2), and `/app` from the TUI on Linux (§6.9).

---

## 1. What the upstream vendor's app is (read from the package, 2026-10-03)

| What | Found |
|---|---|
| Package | `chatgpt` 26.930.31730, arm64, maintained by the upstream vendor, homepage `developers.openai.com/codex/app`; 1.5 GB installed. Adds the upstream vendor's apt repository and an AppArmor profile |
| Shell | An Electron app on the upstream vendor's "owl" Chromium runtime (`owl-electron-app.json`, packaged from `codex/codex-apps/electron` in the upstream vendor's private monorepo). User data directory `Codex`. Main process in `app.asar` (`.vite/build/*.js`), UI a React bundle under `webview/` |
| The agent | **A bundled `codex` binary** (`resources/codex`, `codex-cli 0.160.0`, statically linked), plus `codex-code-mode-host`, `rg` and `tectonic` beside it |
| How it runs the agent | Spawns **`codex -c features.code_mode_host=true app-server --analytics-default-enabled`** and speaks JSON-RPC over **stdio**. Alternatives in the same code: a WebSocket URL, a shared local daemon (`codex app-server daemon`), and host kinds `local`, `wsl`, `ssh` and `remote-control` |
| Which binary | **`CODEX_CLI_PATH` overrides the bundled one**, as does `hostConfig.codex_cli_command` |
| Handshake | `initialize` with `clientInfo: {name: "codex_desktop", title: "Codex Desktop"}` and `capabilities: {experimentalApi: true}`. The app-server treats a stdio client named `Codex Desktop` specially only for device user-verification, and on Windows for uninstall registration |
| Sign-in gate | The UI computes `requiresAuth = account.requiresOpenaiAuth ?? true`; the app-server answers from the model provider. **Measured against `ling app-server`: `requiresOpenaiAuth: false`** (§5) |
| Git, worktrees, terminal emulation | In the window's main process, not the app-server: the protocol has no commit, push, worktree or PR method (§5) |
| Bundled extras | Plugins `browser`, `chrome`, `code-review`, `codex-app-tools`, `deep-research`, `latex`, `unified-computer-use`, `visualize`; a computer-use runtime (`cua_node`, 203 MB); skills; notification sounds |
| Telemetry | The UI bundles reference Statsig 189 times, Sentry 130 times and `ab.chatgpt` 4 times; the agent is started with `--analytics-default-enabled` |

The app-server it drives is the one in our submodule. Its client requests (`app-server-protocol/schema/typescript/ClientRequest.ts`) are: `thread/start|resume|fork|archive|unarchive|delete|list|loaded/list|read|items/list|turns/list|name/set|metadata/update|revert|compact/start|shellCommand|inject_items|unsubscribe|approveGuardianDeniedAction`, `thread/goal/get|set|clear`, `thread/attachment/add|list|remove`, `threadSection/*`, `turn/start|steer|interrupt`, `review/start`, `model/list`, `modelProvider/capabilities/read`, `skills/list|config/write|extraRoots/set`, `plugin/*`, `marketplace/*`, `app/*`, `hooks/list`, `fs/*` (read, write, copy, remove, metadata, watch), `fuzzyFileSearch`, `gitDiffToRemote`, `command/exec|write|resize|terminate` (with `tty` and a PTY size: a terminal), `config/read|value/write|batchWrite|mcpServer/reload`, `configRequirements/read`, `mcpServerStatus/list`, `mcpServer/tool/call|resource/read|oauth/login`, `permissionProfile/list`, `experimentalFeature/list|enablement/set`, `externalAgentConfig/detect|import`, `account/*`, `feedback/upload`, `getConversationSummary`. Approvals and streamed items come back as server requests and notifications. `codex app-server generate-ts [--experimental]` writes the TypeScript types for whatever binary runs it.

**What follows:** the Codex desktop app is a client of a published, open protocol, served by an open binary. Everything that makes it a *coding agent* is on our side of that line. What is the upstream vendor's own is the window — its layout, its interaction design, its git and terminal plumbing, and its proprietary extras.

---

## 2. What the Codex app offers (the feature inventory)

**From the launch announcement** (2 February 2026, macOS; Windows on 4 March 2026): "a command center for agents".
- **Multiple agents in parallel:** "agents run in separate threads organized by projects, so you can seamlessly switch between tasks without losing context."
- **Review in the thread:** "review the agent's changes in the thread, comment on the diff, and even open it in your editor to make manual changes."
- **Worktrees:** "multiple agents can work on the same repo without conflicts … you can check out changes locally or let it continue making progress without touching your local git state."
- **Shared history:** "the app picks up your session history and configuration from the Codex CLI and IDE extension."
- **Skills:** "a dedicated interface to create and manage skills"; invoked explicitly or chosen automatically; a library (Figma, Linear, Cloudflare/Netlify/Render/Vercel deploys, GPT Image, the upstream vendor's docs, PDF/spreadsheet/docx); skills made in the app work in the CLI and IDE; checked into a repository for a team.
- **Automations:** "work in the background on an automatic schedule … instructions with optional skills … when an Automation finishes, the results land in a review queue."
- **Personality:** "a terse, pragmatic style and a more conversational, empathetic one," chosen with `/personality`.
- **Security:** the CLI's sandbox, cached web search by default, approval for elevated commands, project or team **rules** that let named commands run elevated.
- **Video captions** (the videos themselves were not analysable): "Updating a website using the Vercel and image generation skills"; "Creating a spreadsheet to generate shopping lists using the spreadsheet skill"; "Setting up an automation to periodically create new skills"; a 3D racing game built from one prompt over 7 million tokens, with Codex "playing the game" to test it.

**Added since, from the upstream vendor's documentation and changelog and third-party write-ups:** local / worktree / cloud execution modes and **hand-off** of a thread between Local and Worktree; a **review pane** with staged and unstaged diffs, stage / unstage / revert per file and per hunk, split or unified view, inline comments sent to the composer; **commit, push and "Create pull request"**; **"Open in"** an editor, terminal or file manager; **IDE extension sync** (the app follows the files open in the editor); an **integrated terminal** per thread (Cmd+J); a **command palette** (Cmd+K); **voice dictation**; **pop-out windows**; **queued follow-ups and steering** while a turn runs; editing the previous message (Esc twice); a **context-usage indicator** with a Compress action; **plan mode**; **thread fork**; **local environments** with setup scripts and **project actions**; **notifications**, prevent-sleep; an **artifact viewer** (documents, spreadsheets, images), **sites**; an **in-app browser** and **computer use**; image inputs and generation; web search; **MCP servers**, **plugins**, **apps** (connectors); settings pages and keyboard shortcuts; **Chat/Work** modes in the upstream desktop app; `/app` in the CLI to continue a session in the app.

§6 maps every one of these.

---

## 3. What "identical" can and cannot mean here

| Constraint | Consequence |
|---|---|
| Mightling talks to no upstream vendor service (patches `0013`, `0015`, `0016`; the egress audit) | No vendor sign-in, no `codex_apps`, no cloud tasks, no rate-limit or credits UI, no Statsig, no Sentry |
| The upstream vendor's app is proprietary and not ours to redistribute | Mightling cannot ship it, its webview bundle, its icons, sounds or strings |
| Mightling is AGPL-3.0 | Anything we take must be licence-compatible (MIT and Apache-2.0 are) |
| The model is local and smaller | Features that need the upstream vendor's hosted models (image generation, deep research, the upstream vendor's computer-use model) have nothing to call |
| The webview is WebKitGTK, not Chromium | The upstream vendor's agent-driven browser plugin cannot be reproduced. A **preview pane** showing a local dev server is just another webview and can be |
| Today's `ling-app` has users | Its window, its address, its sign-in and its client forwarding must keep working through every phase (§4.1) |

So "as close as possible" means: **the same architecture** (an app-server client over stdio), **the same information architecture and interaction model** (projects, threads, a composer, item-by-item streaming, inline approvals, a review pane, worktrees per thread, terminal tabs, settings that write `config.toml`), **the same keyboard shortcuts and slash commands where they apply** (§6.10), and **sessions shared with the TUI** as the upstream vendor's app shares them with its CLI. Not the same pixels, and not the upstream vendor's trade dress: Mightling's own name, icon (`OnyxBrandAssets.render_app_icon()`), colours and copy.

---

## 4. The approach: grow `desktop/` in place

**Chosen: evolve the existing Tauri project.** `desktop/` stays the project, `ling-app` (`dev.dreamference.mightling`) stays the binary, and nothing a user has today changes until Work passes its Phase 1 acceptance.

- **[CodexMonitor](https://github.com/Dimillian/CodexMonitor)** (Thomas Ricouard, **MIT**, Tauri + React, an app-server client with threads, worktrees, diffs, a terminal dock and dictation) is a **parts bin, not a fork**: the stdio bridge, the thread list, item rendering and the terminal dock are taken file by file where they fit, with its MIT notice kept in `desktop/LICENSES/`. Its layout (one app-server per workspace, a "monitor" sidebar) is not taken.
- **Writing the rest from scratch** on types generated from our pinned binary (§4.4) is the default for everything CodexMonitor does not already do well.
- **Running the upstream vendor's app on `ling` is rejected** as the product (§4.6).

### 4.1 What stays exactly as it is

- The window labelled **`ling`** on `http://localhost:3000/app` — Onyx, its patches, its fonts, its injected scripts — **is the Chat window**, unchanged.
- `main.rs`'s webview environment (`WEBKIT_DISABLE_DMABUF_RENDERER`, `GTK_THEME`), `forwarder.rs`, `discover.rs`, `node_locator.rs` and `auto_sign_in.js`, unchanged.
- `ling app` with no arguments keeps opening the Chat window **until Work's Phase 1 acceptance passes**; then it opens Work, and Chat is one click (and one shortcut) away. People who never open Work keep everything they have.
- A test loads the Chat window exactly as today (its URL, its sign-in script, the forwarder path on a client) and fails on any difference.

### 4.2 What changes in the current code

| Where | Today | Becomes |
|---|---|---|
| `tauri.conf.json` windows | one window, `ling`, external URL | two: `ling` (Chat, unchanged) and **`work`** (bundled UI, `WebviewUrl::App`), Work hidden until enabled |
| `tauri.conf.json` `frontendDist` | `../dist`, which does not exist | the Work UI's build output (`desktop/ui/dist`), built by `ling-admin desktop build` |
| `tauri.conf.json` `security.csp` | `null` | for Work: `default-src 'self'; connect-src ipc: http://ipc.localhost; img-src 'self' asset: data:` (no remote script, style, font or connect). Chat keeps no CSP of ours: it is Onyx's page |
| `capabilities/` | none | one capability, **Work window only**, granting the bridge's commands. Chat gets no capability: Tauri gives a remote-URL webview no IPC unless a capability names its URL (read from `tauri` 2.11.5, `ipc/authority.rs`), so Onyx's page cannot reach the agent |
| `src-tauri/src/` | `main.rs`, `discover.rs`, `forwarder.rs`, `node_locator.rs` | + `bridge.rs` (the app-server process and JSON-RPC routing), `git.rs` (worktrees, staging, commit, push), `pty.rs` only if `command/exec`'s terminal proves insufficient |
| `ling-rs/src/app.rs` | refuses to open on a node when Onyx is down; ignores a folder argument | refuses only when **the window being opened** needs a missing service (Chat needs Onyx; Work needs the model server, and shows its own waiting screen instead of refusing); `ling app <folder>` opens Work on that project; `ling app --thread <id>` opens that thread |
| `ling-rs/src/lib.rs` | `app-server` waits for the model and gets `--oss --model <id>`, which it ignores (§5) | `app-server` gets **`-c model="<id>"`**; `app-server generate-ts` and `generate-json-schema` are offline (`COMMANDS_WITHOUT_MODEL`) |
| `dreamference/night_shift/night_shift_host.py` | `app-server` and `app` are in `NON_INTERACTIVE`: an app-server never holds a night run back | a live **turn** in Work holds it back; an idle open window does not (§8.3) |

Two windows, not one window with two webviews: a child webview (`Window::add_child`) is behind Tauri's `unstable` feature (read from `tauri` 2.11.5), and a second top-level window is stable. The Chat/Work toggle (§6.1) therefore raises the other window, in the same position and size, rather than swapping a pane. If `unstable` multi-webview is ever stabilised, the toggle can become a pane without changing anything else.

### 4.3 The bridge

```
ling-app (Tauri)
 ├─ window "work" — Work UI (React, bundled) ──Tauri IPC──► bridge.rs ──stdio JSON-RPC──► ling app-server
 │                                                         git.rs ──► git (worktrees, stage, commit, push)
 └─ window "ling" — Chat: http://localhost:3000/app (Onyx, as today; forwarder on a client)
```

- **One `ling app-server` per app,** started when the Work window first opens, as the upstream vendor's app does: threads carry their own `cwd`, so one server serves every project. (Phase 0 measured an idle server; §5.)
- **Started through the launcher, never as bare `codex`:** `ling app-server`. The launcher brings the model server tiers (node, `node.json`, mDNS), the model catalog with Mightling's prompt blocks, `CODEX_HOME=~/.mightling`, `chatgpt_base_url` at a closed port, the skills links, the air-gap seal and `/prompt`'s choice.
- **The launcher's wait for the model server** (up to 600 s on a cold load) goes to **stderr**, which the bridge shows as Work's start-up screen. Measured: stdout carried JSON-RPC only (§5).
- **`initialize`** with `clientInfo: {name: "mightling_desktop", title: "Mightling Desktop", version}` and `experimentalApi: true`. Not `Codex Desktop`.
- **What the UI never sends:** `thread/start`'s `baseInstructions`, `developerInstructions`, `modelProvider` and `config` (they would replace Mightling's prompt, provider or policy); `feedback/upload`, `account/login/*` and `account/bedrock/*`; `remoteControl/*` (it pairs the machine with the upstream vendor's remote control); `thread/realtime/*` (the upstream vendor's realtime voice); `userVerification/*`. The bridge holds an allow-list of methods and drops everything else, so a UI bug cannot send them either.
- **Notifications the UI ignores:** `account/rateLimits/updated` (always empty here) and `remoteControl/status/changed` (`disabled`, measured).
- **On a client machine** the app-server runs locally and the launcher finds the node's model server as it does for the TUI. A Phase 3 option is the upstream vendor's `ssh` host kind: `ssh <node> ling app-server` as the stdio transport, so the agent runs where the code is.

### 4.4 Protocol types

Generated from the `ling` binary the release ships: `ling app-server generate-ts --experimental --out desktop/ui/src/protocol` (the subcommand exists in 0.158.0, `cli/src/main.rs`), never copied from CodexMonitor's or the upstream vendor's newer Codex (the upstream vendor's app bundles 0.160.0; our pin is 0.158.0; the protocol moves between releases). The generated files are committed; a test regenerates them from the submodule's `app-server-protocol` and fails on a diff, so a Codex bump that changes the protocol fails the build rather than the UI.

### 4.5 Work's frontend

React + Vite + TypeScript under `desktop/ui/`, built with the repository's pinned Node toolchain; `node_modules` not committed. Diff rendering, the terminal (xterm.js) and Markdown/Mermaid rendering use MIT or Apache-2.0 libraries bundled into the app, never loaded from a CDN. Every Onyx-side lesson in `docs/dev/onyx-ui-patches.md` still applies to Chat; for Work, §9 lists the WebKitGTK ones.

### 4.6 Running the upstream vendor's app on `ling` — rejected as the product

`CODEX_CLI_PATH=~/.local/bin/ling chatgpt` would, as read, start `ling app-server` under the upstream vendor's window, and the local provider switches its sign-in gate off (measured: `requiresOpenaiAuth: false`). It is the most faithful option and must not be the product: the window carries its own Statsig and Sentry clients, its updater and apt repository, and passes `--analytics-default-enabled`; it cannot be shipped. It remains useful as the **reference run** of Phase 0 item 6 and for later screen-by-screen comparisons: the user chose to install the package and keep it (§14, question 5).

---

## 5. Measured on 2026-10-03 (Phase 0, first part)

The installed `ling` (0.158.0, built 2 October) was driven over stdio by a script: `initialize` → `initialized` → `account/read` → `model/list` → `permissionProfile/list` → `config/read` → `thread/start` (ephemeral, read-only) → `turn/start` ("Reply with exactly the word OK"), with Qwen3.8-27B serving.

| Question | Answer |
|---|---|
| Does `ling app-server` start and complete a turn? | **Yes.** The model answered `OK`; the notifications were `thread/started`, `turn/started`, `item/started`, `item/agentMessage/delta`, `item/reasoning/*`, `item/completed`, `thread/tokenUsage/updated`, `turn/completed`, `mcpServer/startupStatus/updated` |
| Is stdout clean JSON-RPC through the launcher? | **Yes:** 0 non-JSON lines. The launcher's messages go to stderr |
| Sign-in | `account/read` → `{"account": null, "requiresOpenaiAuth": false}`: nothing to sign in to |
| **Does the launcher's model choice reach the app-server?** | **No — a bug in today's `ling`, independent of the desktop work.** The CLI applies only root `-c` overrides to `app-server` (`cli/src/main.rs`, the `AppServer` arm); `--oss --local-provider --model <id>` are the TUI's flags and are ignored there. The thread started with `model: ""` and the server warned "Model metadata for `` not found. Defaulting to fallback metadata" — **so Mightling's model catalog, and with it Mightling's prompt blocks, did not apply.** With `-c model="RadixArk/Qwen3.8-27B-NVFP4"` the warning is gone and the session's request carries Mightling's prompt ("You are Mightling, a coding agent…", the `ling-search` block): measured. Fix in §4.2 (the launcher) and, belt and braces, the bridge passes the served model in every `thread/start` |
| `model/list` | **Empty** even with the model set, although `model_catalog.json` holds the served model. **Cause found (2026-10-03):** the launcher's catalog entry said `"supported_in_api": false`, and Codex drops such a model from every picker unless the session has the vendor's sign-in (`ModelPreset::filter_by_auth`, `protocol/src/openai_models.rs`); Mightling never has one, so the TUI's `/model` list was empty too. Nothing else reads the field. Fixed on branch `fix/startup-lines-app-server` (`true`, with a test); not yet in an installed build, so not re-measured |
| `permissionProfile/list` | `:read-only`, `:workspace`, `:danger-full-access`, all `allowed: true` at level `off` — the picker's three rows |
| Remote control | `remoteControl/status/changed` → `disabled` |
| Rate limits | `account/rateLimits/updated` with every field null: ignore it |
| Do app-server threads appear in `ling resume`? | **Yes, measured:** a non-ephemeral thread started over the app-server (with `-c model=…`) was written to `~/.mightling/sessions` with `source: vscode` and `originator` set to the client's name, and `ling resume --last` in the TUI opened it with its turn on screen. (`vscode` is in `INTERACTIVE_SESSION_SOURCES`, `rollout/src/lib.rs`.) The probe's session file and its folder-trust entry were removed afterwards |
| Does an open app-server hold a night run back? | **No:** `NON_INTERACTIVE` in `night_shift_host.py` lists `app-server` and `app`. §8.3 decides what it should do |
| Terminal support | `command/exec` takes `tty` and an initial PTY size, with `write`, `resize` and `terminate`: terminal tabs need no PTY code of ours |
| Git | No worktree, stage, commit, push or PR method; `gitDiffToRemote` only. The TUI's `/worktree` uses the `codex-worktree` crate inside the TUI, not the server. Git is the window's job (`git.rs`), as in the upstream vendor's app |
| Plan mode | **Found and called:** `ling app-server generate-ts --experimental` (772 v2 types against 637) gives `turn/start` a `collaborationMode`; `collaborationMode/list` answered `Plan` (`mode: plan`, effort `medium`) and `Default` |
| Projects and the thread list | `project/list` answered `{"data": []}` (no projects yet, a well-formed list); `thread/list` returned the TUI's own sessions, so TUI threads appear in Work's list (measured) |
| What `--experimental` adds | `project/create|list|read|update|move|delete|import` (projects kept by the server), `thread/queue/add|list|update|reorder|delete|start` (queued follow-ups), `thread/search`, `thread/searchOccurrences`, `thread/timeline/list`, `thread/settings/update`, `turn/settings/update`, `process/spawn|writeStdin|resizePty|kill` and `thread/backgroundTerminals/*` (terminals), `memory/status|reset`, `thread/memoryMode/set`, `environment/*`, `plugin/search`, `server/diagnostics`; and, never to be sent from Mightling, `thread/realtime/*` (the upstream vendor's realtime voice), `remoteControl/*`, `userVerification/*`, `account/bedrock/*`. `turn/start` also gains `permissions`, `environments` and `runtimeWorkspaceRoots` |
| Worktree layout | The `codex-worktree` crate implements "the existing Codex Desktop contract": a checkout at `$CODEX_HOME/worktrees/<4-hex bucket>/<repository name>`, created with `git worktree add --detach` (no branch until one is made), the owning thread recorded in `codex-thread.json` in the worktree's git directory, and the settings `[desktop] git-worktree-root`, `worktree-auto-cleanup-enabled` and `worktree-keep-count` in `config.toml`. The TUI's `/worktree` uses it |
| `/app` in the TUI | Exists upstream: it opens `codex://threads/<id>`, and is compiled only on macOS and Windows (`slash_command.rs`) |
| Types | `app-server generate-ts` exists in 0.158.0. Today the launcher would make it wait for the model server (§4.2 fixes that) |

---

## 6. Feature by feature

Status: **Same** (the Codex app's behaviour, on our server), **Mightling's** (the same idea through a Mightling mechanism), **Later** (a named phase), **No** (not offered, with the reason).

### 6.1 Window and navigation

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Project sidebar, threads per project, pin, rename, archive, sections | `project/*` (experimental), `thread/list`, `threadSection/*`, `thread/name/set`, `thread/archive|unarchive`, `thread/metadata/update` | **Same.** Threads from `ling` in a terminal appear here and back (both read `~/.mightling/sessions`) | 1 |
| Switch threads without losing context; several threads running at once | `thread/loaded/list`, one server, per-thread subscriptions | **Same.** Parallel turns share one model server: Work shows the KV pool's headroom (§7) | 1 |
| Chat / Work toggle | Upstream desktop app | **Mightling's:** Work is the agent, Chat is the Onyx window (§4.2) | 1 |
| Command palette, thread search | client, `thread/search` (experimental) | **Same** (Ctrl+K): threads, projects, slash commands, settings | 2 |
| Pop-out windows | client | **Same:** a thread in its own Tauri window | 2 |
| Notifications when a turn ends or needs approval | client | **Same:** desktop notification, no bundled sounds | 2 |
| Prevent sleep while a turn runs | client | **Same:** `systemd-inhibit`-style idle inhibitor while any turn is running | 2 |
| Settings pages | `config/read`, `config/value/write`, `config/batchWrite` | **Same,** writing `~/.mightling/config.toml` | 1 |
| Dark mode | client | Later: light only for now (§14 question 3, decided) | 3 |

### 6.2 Composer and turns

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Text, `@` file mentions | `turn/start`, `fuzzyFileSearch` | **Same** | 1 |
| Images and attachments | `thread/attachment/*`, input items | **Same** for images (Qwen3.8 is text-only today: attachments are offered only when the served model's entry says `supports_vision`) | 2 |
| Model and effort picker | `model/list`, `turn/start` `model`/`effort` | **Mightling's:** `config/read`'s `model` (set by the launcher once Phase 0 item 2 ships; measured with `-c model`), with `$CODEX_HOME/model_catalog.json` as the fallback, while `model/list` is empty (§5); efforts the patched template accepts | 1 |
| Personality (`/personality`) | `personality` | **Mightling's: cave mode** (`/cavemode`, MIGHTLING_CAVE_MODE), not a second mechanism; `personality` is not sent | 2 |
| Plan mode | `turn/start` `collaborationMode`, `collaborationMode/list` (experimental, §5) | **Same** | 2 |
| Steer while running, queued follow-ups, stop | `turn/steer`, `thread/queue/*` (experimental), `turn/interrupt` | **Same** | 1 |
| Edit the previous message (Esc twice) | `thread/revert` + `turn/start` | **Same** | 2 |
| Context-usage indicator and Compress | `thread/tokenUsage/updated`, `thread/compact/start` | **Same**, against the launcher's KV-pool-based limit | 1 |
| Fork a thread | `thread/fork` | **Same** | 2 |
| Thread goal | `thread/goal/*` | **Same** | 2 |
| Slash commands | client | The Codex ones, plus Mightling's: `/airgapped`, `/cavemode`, `/night`, `/prompt`, `/apps`, each a call to the launcher's own command (`ling airgapped`, `ling night`, `ling prompt` …) so the TUI and the app cannot drift | 2 |
| Voice dictation | client; `thread/realtime/*` is the upstream vendor's realtime API | **Mightling's:** the speech-to-text sidecar (`dream-stt`, CPU Whisper) already running for Chat; `thread/realtime/*` is never sent | 2 |

### 6.3 The stream

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Reasoning, commands with output, file edits, plan updates, MCP and `code_*` tool calls | item notifications, `turn/plan/updated` | **Same** | 1 |
| Markdown and Mermaid rendering | client | **Same** | 1 |
| Inline approvals: command, patch, network | server requests | **Same**, with the air-gap rule (§8.2) | 1 |
| Guardian-denied action override | `thread/approveGuardianDeniedAction` | **Same** if the guardian reviewer is configured | 2 |

### 6.4 Review and git

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Diff of the thread's changes | items, `gitDiffToRemote`, git | **Same** | 1 |
| Review pane: staged and unstaged, stage / unstage / discard per file and per hunk, split or unified | git (`git.rs`) | **Same** | 2 |
| Inline comments on diff lines, sent to the composer | client | **Same** | 2 |
| `/review` | `review/start` | **Same,** local model | 2 |
| Commit, push | git | **Same** | 2 |
| Create pull request | prefilled hosting URL | **Same,** no API or token: the forge's new-PR page opens with branches prefilled | 2 |
| Open in editor, terminal, file manager | client | **Same:** detected from `.desktop` entries (VS Code, VSCodium, Cursor, Zed, JetBrains IDEs, terminals) | 2 |
| Revert the thread's changes | `thread/revert` | **Same** | 1 |

### 6.5 Worktrees and environments

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Worktree per thread; check out locally or let it continue | git, **the Codex Desktop contract** of §5 (shared with the TUI's `/worktree`): `git.rs` either depends on the `codex-worktree` crate by path, if it builds outside Codex's workspace (it inherits workspace dependencies), or implements the same contract with a test that a checkout made by one is listed and owned correctly by the other | **Same.** A thread handed between Work and the TUI keeps its worktree. Night Shift's `night/<id>` branches stay separate | 2 |
| Hand-off between Local and Worktree | git | **Same** | 2 |
| Delete a worktree, keeping the branch | git | **Same,** refusing while a turn runs there or with uncommitted changes | 2 |
| Local environment setup script, project actions | client config | **Same,** in a project file Mightling reads; each runs in a terminal tab | 2 |

### 6.6 Terminal

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| A terminal per thread, toggled with Ctrl+J, scoped to the thread's directory or worktree | `command/exec` with `tty`, `write`, `resize`, `terminate`; or `process/*` (experimental) | **Same.** Runs under the thread's sandbox profile, so `/airgapped on` holds there too | 2 |

### 6.7 Skills, plugins, apps, MCP

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Skills list, enable, create | `skills/list`, `skills/config/write`, `skills/extraRoots/set`, `plugin/skill/read` | **Same,** with Mightling's sources shown (Claude, Gemini, OpenClaw, Hermes, ClawHub with its verdict, MIGHTLING_SKILLS) | 2 |
| Plugins and marketplaces | `plugin/*`, `marketplace/*` | **Same** for local marketplaces; the upstream vendor's featured catalogue stays closed (patch `0015`) | 2 |
| Apps (Gmail, Drive …) | `app/list|read|installed` | **Mightling's:** MIGHTLING_APPS — local sign-in, read-only tools, nothing through `chatgpt.com` | 2 |
| MCP servers: status, reload, OAuth, call a tool, read a resource | `mcpServerStatus/list`, `config/mcpServer/reload`, `mcpServer/*` | **Same** | 2 |
| Hooks | `hooks/list` | **Same** (read-only list), showing the ledger hook | 2 |
| Import another agent's configuration | `externalAgentConfig/detect|import` | **Same,** beside MIGHTLING_SKILLS's links | 3 |
| Experimental features | `experimentalFeature/*` | **Same,** in Settings | 3 |

### 6.8 Automations

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Scheduled tasks with instructions and optional skills | the app's own scheduler | **Mightling's: Night Shift.** `/night add` from the composer, the queue as a panel, a schedule on the timer `night enable` installs. No new scheduler | 2 |
| Results land in a review queue | client | **Mightling's:** the morning report, each task's branch as a thread with its diff in the review pane | 2 |
| Memory across automation runs | `memory/*`, `thread/memoryMode/set` (experimental) | **Mightling's:** COMPACTION §4.4's nightly notes, if built; Codex's own memory if Phase 0 finds it needs no upstream vendor service | Later |
| Cloud triggers | The upstream vendor | **No:** no cloud | — |

### 6.9 Handoff and shared history

| Codex app | How | Mightling | Phase |
|---|---|---|---|
| Sessions shared with the CLI | `~/.codex` | **Same,** `~/.mightling` (§5) | 1 |
| `/app` in the TUI continues the session in the app | `codex://threads/<id>`, macOS and Windows only | **Mightling's:** `ling app --thread <id>` from a shell (no patch); `/app` in the TUI needs one hook (enable on Linux, scheme `ling://`), against the patch cap; `ling-app.desktop` registers `x-scheme-handler/ling` | 2 |
| IDE extension sync | the IDE extension | **No:** Mightling has no IDE extension. The MCP server for JetBrains and VS Code (`ling-admin mcp`) is a different thing | — |

### 6.10 Keyboard shortcuts (Linux bindings)

| Action | Codex app (macOS) | Mightling |
|---|---|---|
| Command palette | Cmd+K | Ctrl+K |
| Toggle terminal | Cmd+J | Ctrl+J |
| Toggle review pane | Cmd+Shift+G | Ctrl+Shift+G |
| New thread | Cmd+N | Ctrl+N |
| Edit previous message | Esc Esc | Esc Esc |
| Stop the turn | Esc | Esc |
| Chat / Work | — | Ctrl+Shift+C / Ctrl+Shift+W |

Rebindable in Settings, stored with the window's preferences, not in `config.toml`.

### 6.11 Not offered

| Codex app | Why |
|---|---|
| Vendor sign-in, plan, credits, rate limits, workspace messages | No hosted account; `requiresOpenaiAuth: false` (§5) |
| Cloud tasks, cloud environments, remote control | The upstream vendor's services; `cloud` is refused by the launcher, remote control is `disabled` |
| Computer use, the upstream vendor's browser plugin, image generation, deep research | No local model or runtime for them |
| Feedback upload | Uploads to the upstream vendor |
| Sites | Hosted by the upstream vendor |

**Offered later instead:** a **preview pane** (a WebKitGTK webview on a local dev server's URL, Phase 3) and an **artifact viewer** for Markdown, images and PDF (Phase 3).

---

## 7. What Mightling adds, in the Codex app's idioms

- **Air-gap status** in the Work window's title bar: `off` or `on`, the seal held or not, and the `NOT ENFORCED` reasons a click away.
- **The node:** which model server this app uses (this machine, or a node found on the network), from `node.json`, with `ling node use` behind a picker on a client.
- **The model server's state** (starting, serving, stopped) and its KV pool's headroom, from `/v1/models` and `/metrics`, with `ling-admin server start` as the action when it is stopped, since without it nothing works. Parallel threads share one pool; the headroom is what makes "run several agents at once" honest on one GB10.
- **The code index:** its state per project (`ling-code status`); `code_*` tools rendered like any tool call.
- **`/prompt`:** the system prompt in use per thread, from MIGHTLING_PROMPT.

---

## 8. Security and privacy

### 8.1 The window

- **No telemetry SDK** of any kind in Work's bundle, no remote fonts or scripts (§4.2's CSP), no auto-updater: updates come from `ling update`, as for the binaries.
- **The egress audit grows an `--app` mode:** trace `ling-app` plus its `ling app-server` through one scripted turn, with the same verdict rules. It must pass before a release ships Work.
- **Chat cannot reach the agent:** no capability names its origin (§4.2).

### 8.2 The air-gap rule must hold in the server, not the window

The Full Access refusal at `on` (MIGHTLING_AIRGAPPED §14.7) is enforced in the launcher's argument check and in the TUI's two permission pickers. **An app-server client chooses the sandbox per thread and per turn** (`thread/start`'s `sandbox` and `config`; `turn/start`'s `sandboxPolicy`, `permissions` and `runtimeWorkspaceRoots`), so greying out a row in Work enforces nothing: any other app-server client, or a bug, gets Full Access at `on`. Measured: `permissionProfile/list` reports `:danger-full-access` as `allowed` (at `off`, where it should be).

Where the check goes, decided in Phase 0 in this order:
1. **Codex's requirements system** (`allowed_permission_profiles` in `requirements.toml`) already removes profiles for every client, but its file is system-wide (`/etc/codex/requirements.toml`) and fixed, not per session: rejected unless a per-`CODEX_HOME` layer exists.
2. **The launcher's registered extension** (patch `0017`'s registry): if an extension can veto a thread or turn configuration, the rule moves there with no new patch.
3. **One hook** where the app-server resolves a thread's or turn's permissions (`thread_processor.rs`, beside `has_permission_override`, and the turn path in `turn_processor.rs`), refusing Full Access when `ling_airgapped::resolve` says `on`, with the same message as the launcher; and `permissionProfile/list` answering `allowed: false` with the reason at `on`, so every client's picker shows it.

**The seal is a second target.** The seal's protection rests on `$XDG_RUNTIME_DIR` being read-only inside the sandbox (MIGHTLING_AIRGAPPED §14.5). A client passing `runtimeWorkspaceRoots` (or a `permissions` profile, or `config`'s `sandbox_workspace_write.writable_roots`) that covers `$XDG_RUNTIME_DIR/ling-airgapped` would make it writable, and a command could then delete the seal. The same check therefore also refuses, at `on`, any writable root that contains the seal's directory or the directory itself.

Work also disables the Full Access row at `on`, but as a courtesy, not as the enforcement.

### 8.3 Sessions and Night Shift

Threads the app starts are ordinary Codex sessions in `~/.mightling/sessions`. Today Night Shift ignores an app-server entirely (§5). An always-open desktop app must not block every night, and process scanning cannot tell a busy server from an idle one. So: **the bridge writes a busy marker** `$CODEX_HOME/night/busy/<pid>`, where `<pid>` is **the `ling app-server` process's** (the bridge's child, so the night run can check it without knowing about windows), holding the ids of the threads with a live turn. It is written on `turn/started`, rewritten on `turn/completed`, and removed when the last turn ends or the server exits. Night Shift's admission treats a marker whose pid is alive (signal 0, the probe `_probe_direct_kill` uses) and whose `/proc/<pid>/exe` is the installed `ling` as an open session, and deletes any other marker: a window killed hard leaves a marker with a dead pid, which the next check prunes. The night run's own `ling exec` processes are unaffected.

---

## 9. The webview: what `desktop/` already learned

Every setting in `desktop/src-tauri/src/main.rs` applies to both windows: `WEBKIT_DISABLE_DMABUF_RENDERER=1` (no window at all without it under the NVIDIA driver), `GTK_THEME=Adwaita:light` (Chat's overrides are `html:not(.dark)`-scoped; Work follows it until it has a dark theme), the white `backgroundColor` (a repaint gap shows the window's own background). **`target="_blank"` does nothing**: links to the web go through `tauri-plugin-opener`. **WebKitGTK paints its own scrollbar** that CSS colours cannot reach (`docs/dev/onyx-ui-patches.md`, `onyx_ui_scripts.py`): Work's scroll areas use `scrollbar-width: thin` and are checked in an offscreen WebKitGTK view, not only in Chromium. CodexMonitor's code was built on macOS first; every part taken from it is checked on the GB10.

---

## 10. Packaging

- The app stays `ling-app` (`dev.dreamference.mightling`), built by `ling-admin desktop build` (which now also builds `desktop/ui/`) and attached to releases; `install.sh` installs it on a client and a node alike. The `.desktop` entry gains `MimeType=x-scheme-handler/ling;`.
- `ling app` opens Chat until Work passes Phase 1, then Work; `ling app --chat`, `ling app <folder>`, `ling app --thread <id>`.
- The installed debug build in `~/.local/share/applications` (28 September) is replaced by the release build.

---

## 11. Phases

**Phase 0 — check (about a day; part done, §5).**
1. ~~`ling app-server` handshake, sign-in gate, one turn, clean stdout~~ — done (§5).
2. **The launcher fix, a hard gate for everything after it:** `-c model="<id>"` for `app-server` (**built** on branch `fix/startup-lines-app-server`, 2026-10-03, with tests; a model the user names as `-c model=` or `--model` wins; not yet in an installed build); offline `generate-ts`. It is a bug in today's `ling` (any app-server client, including the upstream vendor's app pointed at `ling`, gets Codex's fallback prompt) and ships on its own, before any desktop work. Re-run §5's probe without the manual `-c` and see the metadata found and Mightling's prompt in the request.
3. ~~Why `model/list` is empty with a catalog entry present.~~ Found and fixed (§5): `supported_in_api` was `false`. Re-measure after the next build.
4. ~~`generate-ts --experimental`~~ — done (§5): plan mode, projects, queues, search, terminals.
5. §8.2: which of the three places holds the air-gap check — **decided: the third**, as a validator on the config's permission constraint (§15.3); written as patch `0023`, in the series since 2026-10-06. Still to measure: a `thread/start` with `sandbox: "danger-full-access"` at `on` is refused.
6. The reference run (§4.6), with the upstream vendor's package installed by the user (§14 question 5): The upstream vendor's app on `ling app-server`, scratch `HOME`, network namespace reaching only the model server; record each screen and request.
7. ~~A non-ephemeral thread appears in `ling resume`, and a TUI session in `thread/list`~~ — done (§5).
8. Memory of an idle and a busy app-server.
9. `git.rs` against the worktree contract: does `codex-worktree` build as a path dependency of `desktop/` (without running cargo inside `codex/`, which rewrites its lock file)?

*Done when* each has a recorded answer here and item 2 has shipped.

**Phase 1 — Work beside Chat.** The second window, the bridge, the busy marker, the project and thread sidebar, the composer, streaming items, approvals, the permission picker, interrupt and steer, the context indicator, the model picker, settings, the start-up screen, revert, the egress audit's `--app` mode, and the Chat window unchanged. *Done when* (with Phase 0 item 2 shipped, so both sides run Mightling's prompt) a task started in Work can be resumed in the TUI and the other way round, the audit passes, the Chat test passes unchanged — and only then does `ling app` open Work by default.

**Phase 2 — the rest of the Codex surface.** Review pane and git, worktrees and hand-off, terminal, command palette, pop-outs, notifications, prevent-sleep, fork, goal, edit-previous, images, skills, plugins, apps, MCP, hooks, Night Shift as automations, Mightling's slash commands, dictation, cave mode as personality, plan mode, `ling app --thread` and the `ling://` handler, the air-gap and node indicators, the KV headroom.

**Phase 3 — optional.** `/app` from the TUI on Linux (one hook), the `ssh` host kind to a node, the preview pane, the artifact viewer, external agent import, dark mode, macOS and Windows builds.

**Not proposed:** the vendor's sign-in, cloud tasks and triggers, the upstream vendor's bundled plugins, computer use, the upstream vendor's browser, image generation, sites, feedback upload, IDE extension sync, any telemetry.

---

## 12. Tests

- **The Chat window is unchanged:** its label, URL, sign-in script and the forwarder path on a client, compared with today's.
- The bridge: JSON-RPC framing, a server that dies mid-turn, a launcher still waiting for the model (stderr shown, no hang), stray stdout (fails loudly), the dropped `thread/start` fields (§4.3).
- The launcher: `app-server` gets `-c model=…`; `app-server generate-ts` does not wait for a model server.
- The generated protocol types match `generate-ts` of the pinned submodule.
- The UI against a scripted app-server stand-in (as Night Shift's tests script `ling`): a full turn with a command, a patch, an approval and an interrupt; Full Access disabled at `on`.
- The air-gap rule against the real app-server binary: `thread/start` and `turn/start` asking for Full Access at `on` are refused, and so is a `turn/start` whose `runtimeWorkspaceRoots` covers the seal's directory.
- Night Shift: a live busy marker holds the run back; a stale one is pruned; an idle Work window does not hold it back.
- `git.rs`: worktree create, hand-off, stage per hunk, commit, delete-keeping-branch, against scratch repositories.
- The egress audit's `--app` fixture, as `--tui` has.
- No test starts the real window, a real container or the model server, per `tests/conftest.py`.

---

## 13. Risks

- **The protocol moves.** Every Codex bump now has a desktop UI to re-check. Generated types make breakage a compile error.
- **Experimental methods carry Phase 1.** The project sidebar uses `project/*` and queued follow-ups use `thread/queue/*`, both experimental, which change between releases without the v2 surface's stability (the upstream vendor's own client also sends `experimentalApi: true`). They are re-checked at every Codex bump, and Phase 1 keeps fallbacks: the sidebar groups `thread/list` by `cwd` if `project/*` goes, and follow-ups queue in the window if `thread/queue/*` does, so a removed method degrades a feature instead of breaking the app.
- **CodexMonitor moves too,** and follows newer Codex. Parts taken from it are owned from then on.
- **"Close to the Codex app" is a moving target.** The reference is the version recorded in §1–§2; parity with later versions is a decision each time.
- **Trade dress.** Layout and interaction can follow the upstream vendor's; name, icon, colours, sounds and copy must not.
- **WebKitGTK** has repeatedly behaved differently from Chromium here (the scrollbar, `target=_blank`, DMABUF).
- **One model server for parallel threads.** The Codex app's "many agents at once" assumes a cloud. Here they share one GB10's KV pool; several busy threads slow each other and can push compaction (MIGHTLING_COMPACTION). §7's headroom indicator makes that visible rather than solving it.

---

## 14. Open questions

1. ~~Fork CodexMonitor or start from scratch?~~ **Settled by the user's direction:** grow `desktop/` in place, CodexMonitor as a parts bin (§4).
2. ~~Does Chat stay inside the app?~~ **Settled:** Chat is today's window, unchanged (§4.1).
3. ~~Dark mode?~~ **Decided by the user (2026-10-03): light only for now.** Work stays light like Chat (`GTK_THEME=Adwaita:light` applies to both, §9); dark mode stays in Phase 3, as one change for both windows.
4. ~~One app-server per app or per project?~~ **Decided (2026-10-03): one per app** (§4.3), as the upstream vendor's app does: one process, one place for the air-gap check of §8.2. Phase 0 item 8 still measures it, to record the cost, not to decide.
5. ~~Installing the upstream vendor's package for the reference run?~~ **Decided (2026-10-03): yes, installed and kept** for reference comparisons beyond the one run of §4.6. The user runs `sudo apt install ~/Downloads/chatgpt_arm64.deb` from a terminal. Kept means the upstream vendor's apt repository (`/etc/apt/sources.list.d/chatgpt.sources`) and its updater stay, and the app's own `~/.codex` is separate from Mightling's `~/.mightling`. Every reference run still uses a scratch `HOME` and a network namespace reaching only the model server (§4.6, Phase 0 item 6), because the window carries the upstream vendor's telemetry clients and must not reach the upstream vendor while pointed at `ling app-server`.

---

## 15. As built (2026-10-03, branch `desktop/work-window`)

Written while an overnight SWE-bench run held the machine: nothing below was built into `ling-app`, installed, or run against the model server. What was run is named in §15.5.

### 15.1 Where it is

| Part | Where |
|---|---|
| The bridge's pure half: the method allow-list, the thread fields never sent (`baseInstructions`, `developerInstructions`, `modelProvider`, `config`, `personality`), the framing of stdout, the busy marker, the served model from the catalog, finding `ling` | `desktop/bridge/` (`ling-desktop-bridge`, serde_json only) |
| The bridge: one `ling app-server` per app, started through the launcher when Work first asks; stdout to Work as `work://message`, stderr as `work://stderr` (the start-up screen), a non-protocol line as `work://protocol-error`; answers only to server requests still waiting; the busy marker kept and removed on exit | `desktop/src-tauri/src/bridge.rs` |
| Two windows: Chat exactly as configured; Work created only for `--work`, `--cwd <folder>` or `--thread <id>`, in which case Chat's entry is taken out of the configuration and opened later from Work's Chat button. The sign-in script and the forwarder's port rewrite now name Chat's label | `desktop/src-tauri/src/main.rs` |
| Work's capability (`core:default`, window `work` only); the CSP of §4.2 plus `style-src 'self' 'unsafe-inline'`; `frontendDist` `../ui/dist` | `desktop/src-tauri/capabilities/work.json`, `tauri.conf.json` |
| The UI: React 19 + Vite 8 + TypeScript; threads grouped by project folder, a new thread on a folder, streaming items (messages as markdown, reasoning, commands with output, file changes and the turn's diff), inline approvals (command, file change, permissions, the legacy pair, questions), steer while a turn runs, Stop (and Esc), revert of the last turn, the context indicator with Compress, the served model, the air-gap level, the permission picker | `desktop/ui/src/` |
| Protocol types | `desktop/ui/src/protocol/` (873 files), §15.2 |
| `ling app --work`, `ling app <folder>`, `ling app --thread <id>` (Work needs no Onyx; `ling app` alone still opens Chat) | `ling-rs/src/app.rs` |
| Night Shift holds back while a Work turn runs | `NightShiftHost.busy_app_server_pids` |
| The UI built before every `desktop build` and `desktop run` | `DesktopRunner.build_ui` |

### 15.2 Protocol types without running a binary

`codex app-server generate-ts --experimental` does not generate anything at run time: it writes the `typescript` map of `app-server-protocol/schema/precomputed/app-server-exports-experimental.json.zst`, which the binary carries through `include_bytes!`, trimming trailing spaces and tabs from each line. `DesktopProtocolTypes` reads the same file from the submodule (`zstd -dc`), so the committed types are byte for byte what the pinned binary writes, with no binary and no model server, and the launcher's `generate-ts` wait (§4.2) does not matter to the build. The drift test compares them and is skipped where there is no submodule (a release install, a worktree without it).

### 15.3 The air-gap rule in the server

The extension registry (§8.2 option 2) cannot veto: its turn admission sees no configuration and skips silently, and its config contributor only observes. Option 3 is therefore a hook, and the narrowest place found is not the app-server's request paths (thread start, resume, fork, each turn's `sandboxPolicy`, `permissions` and `runtimeWorkspaceRoots`, `thread/settings/update`) but the one thing they all set: the config's `Constrained<PermissionProfile>`. Codex already composes validators onto it for managed deny-read rules (`add_validator`), and the session's settings commits run every later change through it, so one validator covers every client and every path. It calls `ling_airgapped::permission_refusal`, new in the resolver crate (and its byte-identical web copy), which refuses Full Access at a configured `on`, and a writable root that holds the seals while the configured level is `on` or any session is sealed; Full Access is not judged by the seal rule.

- **Patch `0023-airgapped-app-server.patch`, 1,220 bytes:** a dependency line in `core/Cargo.toml` and the validator line in `core/src/config/mod.rs`. It was first kept outside the series for want of room under the cap; the user approved a ceiling of 38,500 on 2026-10-05, and it joined the series on 2026-10-06 (cap 37,500, series 37,288 bytes after 0001–0023). It applies cleanly after 0022.
- **What it does not see:** a thread's own `/airgapped on` (a session file or a seal for that thread), because the config is resolved before the thread has an id. Work's picker disables Full Access per thread from `work_airgapped`, a courtesy, not enforcement.
- **Not compiled:** no Codex build ran tonight.

### 15.4 Departures

- **No `git.rs`, settings pages, model picker or the egress audit's `--app` mode yet** (Phase 1 lists them): the served model is shown, not chosen; review and worktrees were Phase 2 anyway.
- **Chat and Work are separate processes** when started separately: `ling app` then `ling app --work` gives two `ling-app` processes. A single-instance plugin is a new dependency and was left for Phase 2.
- **Links in the agent's messages are not followable** (no opener plugin yet), and images show their alt text.
- **`thread/start` passes `permissions`** (a profile id), not `sandbox`, and each `turn/start` passes the picker's profile.

### 15.5 Checked

- `desktop/bridge`: `cargo test` 6 passed. The resolver crate: 11 passed (2 new). `ling app`'s argument parsing, compiled alone: 3 passed.
- `desktop/src-tauri`: `cargo check --tests` passed (no warnings). Its own tests were not run: that links the webview.
- `desktop/ui`: `tsc --noEmit` clean against the generated types; `vitest` 11 passed (the scripted session: a turn with a command, its approval, a patch, the answer; an interrupt; errors; the sidebar; the picker at `on`; markdown safety); `vite build` 287 KB of script.
- Python: `tests/test_desktop_work.py` (Chat unchanged, Chat without IPC, the CSP, the allow-list against the generated `ClientRequest`, the UI built before Tauri; the drift test run once against the main checkout's submodule: no drift), the Night Shift marker test.
- **Not checked:** the window itself, a turn through the real bridge, the busy marker under a real night run, the patch compiled or measured.

## Sources

- **The launch announcement**, [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/) (2 February 2026; updated 4 March 2026 for Windows). The page answered HTTP 403 to direct requests; it was read from the [Wayback Machine's copy](https://web.archive.org/web/2026/https://openai.com/index/introducing-the-codex-app/). Its videos could not be analysed; their captions are quoted in §2.
- The upstream vendor's package, unpacked: `~/Downloads/chatgpt_arm64.deb` (26.930.31730, SHA-256 `dd980085e9746fad8bd45b48354885d2ea3a9ad09889e0f0d6232f1d819b26b2`).
- Codex `rust-v0.158.0`: `cli/src/main.rs` (the `AppServer` arm, `AppServerSubcommand`), `app-server/src/request_processors/thread_processor.rs`, `turn_processor.rs`, `account_processor.rs`, `initialize_processor.rs`, `app-server-protocol/schema/typescript/`, `rollout/src/lib.rs`, `tui/src/slash_command.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `tui/src/app/history_ui.rs`, `config/src/loader/mod.rs`.
- `tauri` 2.11.5: `src/window/mod.rs` (`add_child` behind `unstable`), `src/ipc/authority.rs` (remote origins).
- [Codex app documentation](https://learn.chatgpt.com/docs/app) and [changelog](https://learn.chatgpt.com/docs/changelog) (the upstream vendor).
- [CodexMonitor](https://github.com/Dimillian/CodexMonitor) and its [LICENSE](https://github.com/Dimillian/CodexMonitor/blob/main/LICENSE) (MIT).
- Third-party: [The Neuron's deep dive](https://www.theneuron.ai/explainer-articles/openai-codex-app-deep-dive-how-it-works/); [ALM Corp's guide](https://almcorp.com/blog/openai-codex-app-macos-guide-features-pricing-security/); [Developer Toolkit's app tips](https://developertoolkit.ai/en/codex/tips-tricks/app-features/); [Daniel Vaughan on the review pane and automations](https://codex.danielvaughan.com/2026/04/17/codex-app-workspace-pr-review-task-sidebar-artifact-viewer/); [ZCode's Codex-parity audit](https://github.com/jptorres26/ZCode/pull/13) (review pane, worktree setup and delete, project actions, diff comments, PR link, Open in); [Best Codex GUI 2026](https://dev.to/stravukarl/best-codex-gui-2026-4-codex-desktop-apps-compared-4c8c).
