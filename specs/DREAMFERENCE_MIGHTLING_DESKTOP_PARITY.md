# Mightling Desktop — parity with the Codex app, October 2026: every feature, the binary's architecture, and the look

> - **Status:** proposed 2026-10-14 (owner: "I want the same functionality", "the same architecture as the Codex binary", "make it look like the original Codex desktop"). Supersedes §2 and §6 of [DREAMFERENCE_MIGHTLING_DESKTOP.md](./DREAMFERENCE_MIGHTLING_DESKTOP.md) (2026-10-03) as the feature inventory; that document's §3 (what identical can mean), §8 (security) and §13 (risks) still hold, and [DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md](./DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md) and [DREAMFERENCE_MIGHTLING_ASK.md](./DREAMFERENCE_MIGHTLING_ASK.md) describe what is built.
> - **What "the Codex app" is now:** by 2026-10-03 the upstream vendor's package was already named after its chat product (DESKTOP §1), and its documentation now describes one **desktop app** (macOS, Windows, and a Linux preview) with three surfaces, Chat, Work and Codex; this document covers the Codex surface. Every feature below is read from the vendor's documentation as it stood on 2026-10-14, from the three videos the owner named (§8), and from the Codex source pinned in `codex/` (`rust-v0.158.0`). Nothing below was read from the vendor's app bundle beyond what §1 of the 2026-10-03 spec recorded.
> - **Trade dress (DESKTOP §13):** layout and interaction may follow the vendor's; name, icon, colours, sounds and copy must not. §3 stays inside that line.
> - **Dark mode** is not approved (2026-10-16): the app stays light only (DESKTOP §14 question 3, decided 2026-10-03). The look of §3 (branch `desktop/codex-look`) was merged on 2026-10-16 light only, without the dark block and the system-theme following it was built with; §3.6's tokens keep a dark theme one block away (§7 question 2).

---

## 0. Summary

The Codex app is a window over an open protocol served by an open binary. Mightling already has the same shape: `ling` is that binary, built from the same source with Mightling's patches, and the desktop app is a stock Electron window that spawns `ling app-server` and speaks the same JSON-RPC over stdio. What differs is **how much of the protocol the window uses** (43 of the 170 client request methods the pinned binary's generated schema lists, plus 11 server requests and 85 notifications, §4.1) and **how many of the window-side features exist** (tabs, terminal, review pane, worktrees, settings, shortcuts: none yet).

This document does three things:

1. **§1** writes down the architecture of the Codex binary as it is, so Mightling's desktop work follows it rather than inventing a parallel one: the crates, the app-server's internals, the protocol's domains, and the line between what the server owns and what the vendor's window owns.
2. **§2** inventories every feature of the Codex surface of the vendor's desktop app, feature by feature, with the protocol method or window mechanism behind it, Mightling's status today, and the phase that delivers it. Features that exist only as the vendor's hosted services are listed in §2.21 with the reason they are not offered.
3. **§3** specifies the look: fonts, libraries, shapes, spacing and motion, taken from how the vendor's product surface is built, with Mightling's own colours.

§4 is the gap list and phases, §5 the tests, §6 the risks, §7 the open questions, §8 the sources.

---

## 1. The architecture of the Codex binary, and the mandate to keep it

### 1.1 One binary, many surfaces

`codex` (`cli/src/main.rs`, pinned 0.158.0) is one statically linked binary with these subcommands: `exec`, `review`, `login`/`logout`, `mcp`, `plugin`, `app-server`, `remote-control`, `app`, `completion`, `update`, `doctor`, `sandbox`, `debug`, `execpolicy`, `apply`, `resume`, `queue`, `archive`/`unarchive`/`delete`, `fork`, `migrate-rollouts`, `cloud`, `responses-api-proxy`, `stdio-to-uds`, `exec-server`, `agents`, `tcp-tunnel`, `features`; with no subcommand it is the TUI. Every surface is a client of the same core:

| Surface | How it reaches the core |
|---|---|
| TUI (`codex`) | In-process: the `app-server-client` crate wraps `app-server`'s `in_process` host behind one async API, so the TUI is itself an app-server client. `codex --remote ws://…` drives a remote app-server instead |
| Desktop app (vendor) | Spawns `codex -c features.code_mode_host=true app-server` and speaks JSON-RPC on stdio (read from the package, DESKTOP §1) |
| VS Code extension | Same app-server, stdio |
| `codex exec` / SDK | `exec` drives the core directly for one turn; the SDK is a typed client over app-server |
| `codex app` | Opens `codex://threads/<id>` in the installed desktop app (macOS and Windows) |
| Mobile and web | The vendor's cloud; not a local client |

**Mightling:** `ling` is this binary (`CodexBrandedBuilder`, `ling-rs/` copied in as `codex-rs/ling`, the patch series). Its surfaces are the TUI, `ling exec`, `ling app-server` and `ling app` (the Electron window), plus `ling web` (the same UI served to browsers). The mandate: **every surface stays a client of the one core.** No feature is built twice, once for the TUI and once for the app; a feature the app needs and the server lacks is added to the server (in `ling-rs/` or as a small patch) and used by both.

### 1.2 The layers

Read from the workspace (`codex/codex-rs/Cargo.toml`, 120 crates), grouped by role:

```
clients:   tui · (vendor desktop) · VS Code ext · exec · SDK · app-server-test-client
             │                       │
transport: stdio JSONL (default) · ws:// · unix:// · off · in_process (embedders) · app-server-daemon (shared, serialized lifecycle)
             │
front:     app-server ─ message_processor → request_processors/{initialize,thread,turn,account,apps,catalog,
           command_exec,config,environment,fs,git,marketplace,mcp*,plugin,project,skills,review,diagnostics,
           feedback,daemon_*} · extensions (ext/* registry) · model_catalog · models_refresh_worker · fs_watch ·
           fuzzy_file_search · code_mode_host · config_manager · connection_rpc_gate · outgoing_message
             │
facade:    core-api  (thread management over codex-core)        protocol · app-server-protocol (v1, v2, schema)
             │
core:      core  (the agent loop: model requests, tools, approvals, compaction, sessions)
             ├─ tools · apply-patch · shell-command · shell-escalation · execpolicy (Starlark rules) · network-proxy
             ├─ sandboxing · bwrap · linux-sandbox · windows-sandbox-rs/-service · mxc-sandbox · process-hardening
             ├─ model-provider · model-provider-info · models-manager · lmstudio · ollama · codex-api · codex-client
             ├─ rollout (JSONL sessions) · state (SQLite metadata) · thread-store · attachment-store · message-history · history
             ├─ skills · plugin · core-plugins · core-plugin-common · hooks · connectors · codex-mcp · rmcp-client
             ├─ code-mode · code-mode-host · code-mode-protocol · code-mode-runtime · exec-server(-protocol)
             ├─ file-search · file-watcher · worktree · git-utils · mermaid · terminal-detection
             └─ ext/: agent · agent-message-board · connectors · extension-api · git-attribution · goal · guardian-reviewer ·
                      guardian-v2 · history-notes · image-generation · items · mcp · memories · queue · skills · web-search
vendor-only: login · keyring-store · secrets · cloud-tasks(-client) · cloud-config · chatgpt · backend-client ·
           analytics · otel · otel-trace-websocket · feedback · user-verification · attestation · realtime-webrtc ·
           voice-host · workload-identity · aws-auth · tcp-tunnel · responses-api-proxy
```

Three properties of this layering matter for Mightling:

- **The server owns the agent.** Threads, turns, items, approvals, configuration, skills, plugins, MCP, the file system view, process execution, fuzzy file search, git diff of a thread, compaction, memories, goals, queues, subagents: all are **methods**. The vendor's window never runs the agent loop itself.
- **The window owns the workstation.** Git operations beyond the diff (worktree creation, stage, commit, push, pull requests), the terminal emulator, tabs and layout, previews, notifications, settings pages, shortcuts, pets: these live in the vendor's Electron main process (DESKTOP §1 and §5: the protocol has no commit, push, worktree or PR method; `codex-worktree` is a crate the TUI links, not a server method).
- **Features are extensions.** `/goal`, the message queue, memories, guardian review, skills, MCP, subagents, web search and image generation are `ext/*` crates registered through `ext/extension-api`. Mightling's patch `0017` registers its own extension there (DESKTOP §8.2). Anything Mightling adds to the agent should be an extension before it is a patch.

### 1.3 The app-server

- **Transports** (`app-server/src/main.rs`, the vendor's app-server document): `--listen stdio://` (default, newline-delimited JSON), `ws://IP:PORT` (experimental; also serves `/readyz` and `/healthz`; auth by capability token or signed bearer token), `unix://` (WebSocket over Codex's control socket or a path), `off`. `--code-mode-host wss://…` points every thread at a remote Code Mode host. `app-server-daemon` keeps one managed server alive across CLI invocations and the updater; `daemon_thread_recovery` resumes persistent root threads after a daemon restart.
- **Lifecycle:** one `initialize` per connection (`clientInfo`, `capabilities.experimentalApi`, `optOutNotificationMethods`, `requestAttestation`, `mcpServerOpenaiFormElicitation`), then `initialized`; `thread/start|resume|fork`; `turn/start`; `turn/steer`; notifications until `turn/completed`; `turn/interrupt`.
- **Primitives:** thread → turns → items (user message, agent message, reasoning, command execution, file change, MCP tool call, plan, tool call, user-input request, permissions request, auto-approval review).
- **Dispatch:** `message_processor` routes each request to a domain processor (`request_processors/`), gates experimental methods (`connection_rpc_gate`), and fans notifications out per subscribed thread (`outgoing_message`). `config_manager` layers `config.toml`, profiles, project `.codex/config.toml`, managed requirements and `-c` overrides; `model_catalog` and `models_refresh_worker` serve `model/list`; `fs_watch` and `fuzzy_file_search` back the file tree and `@` mentions; `code_mode_host` starts the Code Mode host when `features.code_mode_host` is on.

### 1.4 The protocol, by domain

The methods, read from the generated schema `app-server-protocol/schema/typescript/{ClientRequest,ServerRequest,ServerNotification,ClientNotification}.ts` (0.158.0: 170 client requests, 11 server requests, 85 server notifications, 1 client notification, recounted 2026-10-16 from the committed types by `tests/test_desktop_parity.py`, which holds them as `desktop/ui/src/protocol-inventory.json` grouped by the domains below; the 2026-10-14 count of 107 and 10 left out v1 and test methods; the same files `desktop/ui/src/protocol/` carries), grouped here by domain. Names are what a client sends or receives; the v1 methods `initialize`, `fuzzyFileSearch`, `gitDiffToRemote`, `getAuthStatus` and `getConversationSummary` have no slash:

| Domain | Methods (client → server unless noted) |
|---|---|
| Session | `initialize`, `initialized`, `currentTime/read`, `server/diagnostics`, `serverRequest/resolved`, `experimentalFeature/list`, `experimentalFeature/enablement/set` |
| Account (vendor) | `account/read`, `account/updated`, `account/login/*`, `account/logout`, `account/rateLimits/*`, `account/usage/read`, `account/bedrock/*`, `account/gatewayOAuth/*`, `account/chatgptAuthTokens/refresh`, `account/workspaceMessages/read`, `account/sendAddCreditsNudgeEmail`, `account/rateLimitResetCredit/consume` |
| Models and modes | `model/list`, `model/rerouted`, `model/safetyBuffering/updated`, `model/verification`, `modelProvider/capabilities/read`, `modelProvider/authRecovery*`, `collaborationMode/list`, `permissionProfile/list`, `configRequirements/read` |
| Config | `config/read`, `config/value/write`, `config/batchWrite`, `config/mcpServer/reload` |
| Projects | `project/list|read|create|update|move|delete|import`, `project/changed` |
| Threads | `thread/start|resume|fork|read|list|loaded/list|unsubscribe|archive|unarchive|delete|search|searchOccurrences`, `thread/items/list`, `thread/turns/list`, `thread/timeline/list`, `thread/name/set`, `thread/metadata/update`, `thread/settings/update`, `thread/memoryMode/set`, `thread/compact/start`, `thread/revert`, `thread/shellCommand`, `thread/attachment/add|list|remove`, `thread/backgroundTerminals/list|terminate|clean`, `thread/approveGuardianDeniedAction`; notifications `thread/started|closed|archived|unarchived|deleted|compacted|reverted|status/changed|name/updated|project/updated|settings/updated|tokenUsage/updated|attachment/updated|environment/connected|disconnected` |
| Thread sections | `threadSection/create|list|update|delete`, `thread/section/move` |
| Goal | `thread/goal/get|set|clear`, `thread/goal/updated|cleared` |
| Queue | `thread/queue/add|list|update|reorder|delete|start`, `thread/queue/changed` |
| Turns | `turn/start`, `turn/steer`, `turn/interrupt`, `turn/settings/update`; notifications `turn/started|completed|plan/updated|diff/updated|moderationMetadata` |
| Items (server → client) | `item/started|completed`, `item/agentMessage/delta`, `item/reasoning/textDelta|summaryTextDelta|summaryPartAdded`, `item/commandExecution/outputDelta|requestApproval|terminalInteraction`, `item/fileChange/outputDelta|patchUpdated|requestApproval`, `item/mcpToolCall/progress`, `item/plan/delta`, `item/tool/call`, `item/tool/requestUserInput`, `item/permissions/requestApproval`, `item/autoApprovalReview/started|completed`, `autoApprovalReview/strictReviewRequired`, `rawResponse/completed`, `rawResponseItem/completed` |
| Review | `review/start` |
| Execution | `command/exec`, `command/exec/write|resize|terminate`, `command/exec/outputDelta`; `process/spawn|writeStdin|resizePty|kill`, `process/outputDelta|exited`; `rollout/compress` |
| Files | `fs/readFile|writeFile|readDirectory|createDirectory|copy|remove|getMetadata|watch|unwatch`, `fs/changed`; `fuzzyFileSearch/sessionStart|sessionUpdate|sessionStop`, `fuzzyFileSearch/sessionUpdated|sessionCompleted` |
| Skills, plugins, apps | `skills/list`, `skills/config/write`, `skills/extraRoots/set`, `skills/changed`; `plugin/list|read|search|install|uninstall|reconcile|installed`, `plugin/skill/read`, `plugin/share/*`; `marketplace/add|remove|upgrade`; `app/list|read|installed`, `app/list/updated`; `hooks/list`, `hook/started|completed` |
| MCP | `mcpServerStatus/list`, `mcpServer/startupStatus/updated`, `mcpServer/tool/call`, `mcpServer/resource/read`, `mcpServer/oauth/login`, `mcpServer/oauthLogin/completed`, `mcpServer/elicitation/request`, `mcpServer/event/stream/start|stop|notification` |
| Memory | `memory/status`, `memory/reset` |
| Environments | `environment/add|info|status` |
| Git | `gitDiffToRemote` (v1) — the only git method |
| External agents | `externalAgentConfig/detect|import|import/readHistories|recordHistory`, `externalAgentConfig/import/progress|completed` |
| Realtime (vendor) | `thread/realtime/*` (start, stop, append audio/text/speech, SDP, voices, transcript and audio deltas) |
| Remote control (vendor) | `remoteControl/enable|disable|status/read|pairing/*|client/*`, `remoteControl/status/changed` |
| Platform | `windowsSandbox/readiness|setupStart|setupCompleted`, `windows/worldWritableWarning`, `userVerification/*`, `attestation/generate`, `feedback/upload` |

A `tests/` drift test (§5) recounts the four generated schema files so a Codex bump that adds or renames a method is caught.

### 1.5 Mightling's binding

| Codex | Mightling, today |
|---|---|
| `codex` binary | `ling` (CodexBrandedBuilder; patches close the vendor channels `0013`/`0015`/`0016`, register an extension `0017`, enforce `/airgapped` in the sandbox helper `0019` and in the app-server's config `0023`, add drop-first compaction `0026` and the cache-hot compaction request `0027`) |
| `codex app-server` spawned by the window with `features.code_mode_host=true` | `server.ts` spawns `<resources>/ling -c features.code_mode_host=true app-server` with `LOG_FORMAT=json`; the launcher adds the model, catalog and `CODEX_HOME=~/.mightling` |
| `clientInfo` `codex_desktop` / "Codex Desktop" | `mightling_desktop` / "Mightling Desktop", `experimentalApi: true` |
| The vendor's Electron (its own Chromium build "owl"), Vite, Forge | Stock Electron 42.x pinned, Vite, Forge 8, `maker-deb` |
| Window → main process IPC, unvetted | One IPC channel, every request vetted against `ling-rs/web/policy.json` (43 requests, 1 notification, 10 dropped thread fields), the same file `ling web` enforces; both run `ling-rs/web/vectors/` |
| Telemetry (Statsig, Sentry) | None; egress closed in the main process; `ling-admin audit egress --app` |
| Window-side git, terminal, previews | Not built (§4) |

**The mandate, stated once:** a feature in §2 is implemented at the layer the vendor implements it at. Server features use the server's method and nothing else (the policy allow-list grows by that method, with a vector); window features live in the main process (`desktop/electron/src/`) or the UI (`desktop/ui/`), never in a second agent process. Where the vendor's window calls `git`, Mightling's calls `git`. Where the vendor added a crate (`codex-worktree`) rather than a method, Mightling links the same crate from `ling-rs/` and exposes it to the window through the launcher (`ling worktree …`, as `ling airgapped --thread` already is) rather than through a new protocol method, provided the crate builds outside Codex's workspace (DESKTOP §11 Phase 0 item 9, open); the fallback is the window calling `git` as the vendor's does.

### 1.6 The window, module by module

The vendor's window is an Electron main process plus a bundled UI (DESKTOP §1). Its modules, inferred from what the protocol does not provide and what the documentation attributes to the app, against `desktop/electron/src/` today and what Phase 2 adds:

| Window module (vendor) | Mightling today (`desktop/electron/src/`) | Phase 2 |
|---|---|---|
| Spawn the agent (`codex … app-server`, `CODEX_CLI_PATH` override) | `server.ts` (spawns `<resources>/ling … app-server`) | unchanged |
| JSON-RPC router between UI and server; approval routing | `bridge.ts` + `policy.ts` (the allow-list, the dropped thread fields, the prompt field) | the allow-list grows per §2; one vector per new method |
| Egress and privacy switches | `egress.ts`, `app-protocol.ts` (privileged scheme, CSP) | unchanged; the preview tab adds its own rules (Phase 3) |
| Deep-link scheme (`codex://…`, §2.21) | `ling app --thread` argument only (`args.ts`) | `ling://` registered by the `.desktop` entry; `args.ts` parses the routes of §2.21 |
| Window state, frameless title bar, native controls | `window-state.ts`, `work.ts` | pop-out windows, always-on-top, tabs and layout state |
| Menu and accelerators | `shell.ts` | the keymap of §2.19 as data; the shortcuts settings page |
| Preferences (window settings) | none (window state only) | `prefs.ts`: a JSON file under the app's data folder for everything §2.15 marks as window-side |
| Git: worktrees, hand-off, stage, commit, push, PR | none | `git.ts` (child `git`; `ling worktree` when §1.5's condition holds) |
| Terminal PTY | none (the server's `command/exec` tty is the PTY) | xterm.js in the UI over `command/exec`/`process/*`; no PTY code of ours |
| Notifications, prevent sleep | none | `notify.ts` (`Notification`, `powerSaveBlocker`) |
| Air-gap level per thread | `airgapped.ts` (`ling airgapped --thread`) | unchanged |
| Night Shift busy marker | written by the server-side ledger; recognised by `NightShiftHost` | unchanged |
| Pets overlay, Appshots, computer-use runtime, browser plugin, telemetry | none | none (§2.21); the preview tab in Phase 3 |

---

## 2. Every feature of the Codex surface, and Mightling's answer

Status words: **Built** (the UI exists in the app today), **Allowed** (the method is in `policy.json` but no UI calls it yet), **Partial** (some of it), **Same** (to build, the Codex behaviour on our server), **Mightling's** (the same idea through a Mightling mechanism), **Later** (a named phase), **No** (not offered, with the reason). Shortcuts are the Linux bindings (§2.19).

### 2.1 Shell, window and navigation

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| One window; three surfaces: Chat, Work, Codex (Alt+1/2/3 on Windows and Linux) | window | **Mightling's:** two views, Ask and Work, **Built** (menu accelerators Ctrl+1 / Ctrl+2 today, `desktop/electron/src/shell.ts`); Phase 2 moves them to Alt+1 / Alt+2 so Ctrl+1–9 can mean "go to chat" as in the vendor's app | 2 |
| Sidebar: projects with pinned ones on top, Recents, New chat, Scheduled, Plugins (pinnable), Code Review, Search chats; toggle Ctrl+B | window; `project/list`, `thread/list`, `threadSection/*` | **Partial:** threads grouped by folder, rename, search, archive; no pins, sections, Recents, Scheduled or sidebar toggle | 2 |
| Pin a project or a chat | `thread/metadata/update` (pin flag), window prefs for projects | **Same** | 2 |
| Mark chat unread; unread indicators; next chat needing attention (Ctrl+Alt+A); clear all (Shift+Esc) | window state over `thread/status/changed`, `item/*/requestApproval` | **Same** | 2 |
| Command menu (Ctrl+K / Ctrl+Shift+P): commands, threads, projects, settings | window | **Same** | 2 |
| Navigate back / forward (Ctrl+[ / Ctrl+]) | window history | **Same** | 2 |
| Tabs per chat: terminal, browser, file, review; close / reopen / close others; next / previous tab; go to chat 1–9; recent chat 1–6 | window | **Same** for terminal, file and review tabs; browser tab §2.9 | 2 |
| Workspace layout: full view, split view (chat beside tabs), hidden tabs (Ctrl+Shift+B, Ctrl+Shift+F, Ctrl+Alt+B) | window | **Same** | 2 |
| Bottom panel (Ctrl+J) | window | **Built** (2026-10-16, item 7): environment setup and action runs, each a tab with its output and Stop, and the environment editor; terminal tabs join with item 8 | 2 |
| Font size: increase / decrease / reset (Ctrl+= / Ctrl+- / Ctrl+0) | window (`webContents.setZoomLevel` or a CSS variable) | **Same** | 2 |
| Undo / redo the last app action (archive, rename, pin…) | window | **Same** | 2 |
| Full screen (F11), quit (Ctrl+Q), close tab or window (Ctrl+W) | window | **Same**; Ctrl+W closes a tab, never the last window | 2 |
| Pop-out: a chat in its own window, **Always on top** | window | **Same** (a second `BrowserWindow` on the same app-server; still one app-server) | 2 |
| Quick chat (Ctrl+Alt+N): an ordinary chat of the vendor's chat surface beside Codex | the vendor's Chat surface | **Mightling's:** Ask is Quick chat; Ctrl+Alt+N opens a new Ask thread | 2 |
| Side chat (Ctrl+Alt+S): a status recap without interrupting the main chat | a second thread with the main thread's context | **Same** (a thread started with a reference to the main thread's id; the server has no side-chat method, so the window forks: `thread/fork` then a turn asking for a recap) | 2 |
| Standalone chat (Ctrl+Alt+O): a chat with no project | `thread/start` with `cwd` = home | **Same** | 2 |
| Search chats (unassigned shortcut); Find in chat (Ctrl+F, Ctrl+G, Shift+F3) | `thread/search`, `thread/searchOccurrences`; window find | **Partial:** the sidebar search is Built; find in chat to build | 2 |
| Copy chat deep link (Ctrl+Alt+L), copy conversation path, copy working directory | window; `ling://threads/<id>` (§2.20) | **Same** | 2 |
| Notifications when a turn completes or needs approval; prompt for permission | window (`Notification`) | **Same**, no bundled sounds | 2 |
| Prevent sleep while running | window (`powerSaveBlocker`) | **Same** | 2 |
| Appshots (screenshot of another app as context, Cmd+Cmd) | macOS/Windows runtime | **No:** Linux first; a screenshot attachment is §2.3's image input | — |
| Pets: a floating animated companion with chat controls (Option+Space / Win+Alt+P), status, custom pets | window overlay | **Later (3), with Mightling's own art**; the vendor's pets are its trade dress | 3 |

### 2.2 Projects

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Projects view: local projects (folders) and, in the vendor's chat surface, cloud projects | `project/list|read|create|update|move|delete|import` | **Partial:** `project/list|read|create|update` Allowed, the sidebar groups threads by folder; a Projects view to build | 2 |
| Open folder (Ctrl+O), project picker (Ctrl+Alt+Shift+O) | window dialog; `project/create` | **Same** | 2 |
| A project with several folders; **Make primary** (new chats start there; git, `AGENTS.md`, skills and `config.toml` discovery use it); secondary folders searchable and editable | `project/update`; `turn/start` `cwd` plus `runtimeWorkspaceRoots` | **Same**; the air-gap rule of DESKTOP §8.2 applies to every added root | 2 |
| Edit project, archive all its chats, delete | `project/update|delete`, `thread/archive` | **Same** | 2 |
| Permanent worktree as its own project (from the project's menu) | window git + `project/create` | **Same** (§2.6) | 2 |
| Sources section (uploaded files, connected context) | vendor's cloud projects | **No:** a local project's sources are its folders | — |
| Local / Cloud toggle, Work Cloud sync of conversations | vendor | **No** (no cloud) | — |

### 2.3 Composer and turns

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Text; `@` file mentions; `$` skill picker; `/` commands | `turn/start`, `fuzzyFileSearch`, `skills/list` | **Built** (2026-10-16): `@` searches the thread's folder through `fuzzyFileSearch` and writes the path; `$` lists `skills/list` and sends a `skill` input item beside the text; `/` opens the commands at the start of a message | 2 |
| Enter sends, or Ctrl+Enter for multiline or always (Settings › General) | window | **Built** (2026-10-16): Settings › General, kept in the window's preferences (`prefs.json`) | 2 |
| Follow-up behaviour: a message sent while running **steers** the run or **waits** for the next run (Settings › General) | `turn/steer` vs `thread/queue/add` | **Built** (2026-10-16): Settings › General; steer sends `turn/steer`, queue sends `thread/queue/add` and the server starts the next queued message when the turn ends; queued messages show above the composer with **steer now** and remove; the other behaviour is one click beside the send button | 2 |
| Restore previous composer prompt (↑ when empty) | window | **Built** (2026-10-16): ↑ walks back through this window's prompts and the thread's earlier messages, ↓ returns to the draft | 2 |
| Images: paste, attach, Shift+drag | `thread/attachment/*`, input items | **Built** (2026-10-16): Ask attaches pasted, picked and dropped files as before; in Work, Shift+drag attaches an image by its path on disk (a `localImage` item, nothing copied) and a plain drop writes the file's path as an `@` mention | 2 |
| Model picker (Ctrl+Shift+M), recent models (Alt+M), intelligence level / reasoning effort | `model/list`, `turn/start` `model` / `effort` | **Built** (2026-10-16), **Mightling's:** a chip in the composer opens the picker (Ctrl+Shift+M; Alt+M with recent models first) listing `model/list` (one served model) and the efforts its catalog entry allows; the effort goes with each `turn/start` | 2 |
| Plan mode (`/plan`), interview-then-goal | `turn/start` `collaborationMode` | **Allowed** (`collaborationMode/list`); the picker to build | 2 |
| Goal mode (`/goal`): the goal is prompt and completion criteria; a progress row above the composer with pause, resume, edit, clear | `thread/goal/set|get|clear`, `thread/goal/updated` | **Same** | 2 |
| Composer predictions (suggested completions while typing) | vendor | **Later:** needs a fast local completion path (the engine's or the sidecar's); COMPACTION §4.6's fast slot | 3 |
| Voice chat (Ctrl+Shift+V) | `thread/realtime/*`, vendor realtime | **Mightling's:** the speech-to-text sidecar (`dream-stt`) for dictation; `thread/realtime/*` never sent | 2 |
| Approve (Enter) / decline (Esc) an open approval | `item/*/requestApproval` answers | **Built** (2026-10-16): with nothing typed, Enter approves and Esc declines the first open approval of the shown thread | 2 |
| Steer while running; queued follow-ups; stop (Esc) | `turn/steer`, `thread/queue/*`, `turn/interrupt` | **Built** | — |
| Edit the previous message (Esc Esc) | `thread/revert` then `turn/start` | **Built** (2026-10-16): Esc Esc on an empty composer, `/edit` or the **Edit** link on the last turn reverts it (`thread/revert`) and puts its text back in the composer | 2 |
| Fork a thread | `thread/fork` | **Built** (2026-10-16): the fork button in the header and `/fork` | 2 |
| Context indicator and **Compress** | `thread/tokenUsage/updated`, `thread/compact/start` | **Built** (usage chip, its compress link, and `/compact`) | — |
| Slash commands of the TUI available in the app (`/model`, `/permissions`, `/review`, `/plan`, `/goal`, `/memories`, `/skills`, `/mcp`, `/plugins`, `/agents`, `/side`, `/btw`, `/compact`, `/recap`, `/diff`, `/status`, `/new`, `/resume`, `/fork`, `/worktree`, `/rename`, `/archive`, `/pet` …; the pinned TUI's list is in `tui/src/slash_command.rs`) | window parses `/`; each maps to a method or a window action | **Partial** (2026-10-16): `/new`, `/fork`, `/compact`, `/edit`, `/rename`, `/archive`, `/model`, `/permissions`, `/status`, `/skills`, `/mention`, `/mcp`, `/diff` (`gitDiffToRemote`), `/copy`, `/resume` and Mightling's `/airgapped` (status, and `default off|on` through `ling airgapped default`; a thread's own level is set inside a `ling` session, since the launcher has no `--thread` setter) are Built, each described in Mightling's words; `/review`, `/plan`, `/goal`, `/memories`, `/agents`, `/plugins`, `/side`, `/worktree` come with their items (5, 6, 7, 11, 13; side chat with 3); `/cavemode`, `/night`, `/prompt`, `/apps` with 10 and 12 | 2 |

### 2.4 The stream

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Items: user and agent messages, reasoning (collapsible), commands with live output, file changes with patches, plan updates, MCP and tool calls, user-input requests | `item/*` notifications | **Built** (messages, reasoning, commands, files, plan, diff); tool-call and user-input items to check | 2 |
| Markdown and Mermaid | window | **Partial:** Markdown built (`marked`); Mermaid to bundle | 2 |
| Inline approvals: command, patch, permissions; strict review required | `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `autoApprovalReview/strictReviewRequired` | **Built** for the first three; the air-gap rule holds in the server | — |
| Automatic approval reviews (a separate reviewer judges eligible requests) | `item/autoApprovalReview/*` | **Same**, rendered as an item; configured in `config.toml` | 2 |
| Guardian-denied action override | `thread/approveGuardianDeniedAction` | **Same** if a guardian is configured | 3 |
| Subagent threads: a panel above the composer with each subagent's status, stop all, open a subagent's thread | `thread/started` with a parent id (agent-graph-store), `thread/status/changed`, `turn/interrupt` | **Same**; one GB10 serves every subagent, so the KV headroom chip (DESKTOP §7) counts them | 2 |
| Memories per chat (`/memories`: use existing, contribute to future) | `thread/memoryMode/set`, `memory/status|reset` | **Same**, after Phase 0's check that memory generation calls no vendor service (it runs on the configured model: `memories.extract_model`, `memories.consolidation_model`) | 2 |
| Activity view (Ctrl+Alt+U): what the agent did, across threads | window over items | **Same** | 3 |

### 2.5 Review and git

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Review pane (Ctrl+Shift+G): the repository's state, not only the agent's edits; scopes **Unstaged** (default), **Staged**, **Commit**, **Branch** (against the base), **Last turn** | window `git`; `turn/diff/updated` for Last turn | **Built** (2026-10-16): a column beside the thread (Ctrl+Shift+G or the header's review button); Unstaged (untracked files included), Staged, Commit (HEAD or one of the last 30), Branch (against `origin/HEAD`, else main or master) from `git` in the main process (`git.ts`), Last turn from the server; in a browser on `ling web` only Last turn | 2 |
| Repository selector when a project has several repositories; **All repos** for Last turn | window `git` | **Built** (2026-10-16): the repository the thread's folder is in, or a selector over the repositories up to two levels below a folder that is not in one; Last turn is the server's diff of every repository the turn touched | 2 |
| Stage, unstage, revert: whole diff, per file, per hunk; split or unified | window `git` | **Built** (2026-10-16): Stage all / Revert all / Unstage all, per file, and per hunk (`git apply --cached`, `--reverse`, `--index --reverse` of a one-hunk patch); reverting asks first; unified or split, remembered in the window's preferences | 2 |
| Inline comments on diff lines (hover, **+**), sent to the composer as review guidance | window | **Built** (2026-10-16): **+** on a hovered line, comments listed under their lines, **Send to the composer** writes them as review guidance with the path, line and code | 2 |
| Mark a file as viewed | window state | **Built** (2026-10-16): a viewed file folds until its changes change | 2 |
| Click a file name: open in the chosen editor; Ctrl+click a line: open at the line; click the name's background: collapse | window; editor detection | **Built** (2026-10-16): editors found from `.desktop` entries, JetBrains Toolbox scripts and PATH (`editors.ts`: VS Code, VSCodium, Cursor, Zed, Sublime Text, the JetBrains IDEs, Kate, gedit, Text Editor), each opened at the line with its own arguments, or the system's choice; the choice is a window preference | 2 |
| `/review`: against a base branch, uncommitted changes, a commit, custom instructions; findings as inline comments; **Inline** in the chat or **Detached** (Settings › Git › Review delivery); `review_model` | `review/start`; `config/value/write` | **Partial**: built on the held branch `desktop/p2-policy` with `review/start` added to `policy.json`, which needs the Rust conformance run (cargo) before it merges; not on main yet | 2 |
| Commit (with a generated message), push, create pull request (the forge's new-PR page prefilled) | window `git`; Settings › Git: branch naming, force push, commit and PR prompts | **Built** (2026-10-16): commit what is staged or everything; **Write a message** asks the served model in an ephemeral read-only thread that never asks for approval (`thread/start` `ephemeral`); push (`--force-with-lease` when ticked, the upstream set the first time) and the forge's new pull request page (GitHub, GitLab, else the repository's page) in the system browser, both refused at `/airgapped on`; the Settings › Git prompts come with item 10 | 2 |
| Code Review plugin for pull requests: inbox (assigned to me / my team / authored by me), summary, changes, checks, **Fix** from a failing check, **Review with Codex**, review instructions, submit review (comment / approve / request changes), stacks | vendor plugin over GitHub/GitLab APIs | **Mightling's, later:** a local `gh`/`glab`-backed review tab, optional, off by default; the air gap refuses it at `on` | 3 |
| Automatic cloud reviews of pull requests | vendor cloud | **No** | — |
| Revert the thread's changes | `thread/revert` | **Built**: the per-turn **Revert**, and **Revert the turn** in the review pane's Last turn scope | 2 |

### 2.6 Worktrees, hand-off and environments

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| **Worktree** under the composer when starting a chat in a git project; choose the base branch (main, a feature branch, or the current branch with its uncommitted changes applied) | window `git`; the Codex Desktop worktree contract: `$CODEX_HOME/worktrees/<bucket>/<repo>`, `git worktree add --detach` | **Built** (2026-10-16): Local or Worktree under the composer of a new thread in a git project, the base (the current branch, with or without its uncommitted changes, or another local branch) and the environment whose setup runs; under `~/.mightling/worktrees` or Settings › Worktrees' root. Mechanism: the window runs `git` against the `codex-worktree` contract step for step (`desktop/electron/src/worktree.ts`: a 4-hex bucket, `worktree add --detach --no-checkout`, `core.worktree` in the worktree's own config, `reset --hard --no-recurse-submodules`, the owner in `codex-thread.json`), since `ling worktree` over the crate needs Rust; the TUI's `/worktree` and the app share the directories | 2 |
| Detached HEAD by default; **Create branch here** in the chat header | window `git` | **Built** (2026-10-16): the header's chip says worktree or local and the branch or detached; **Create branch here** names one | 2 |
| **Hand off** in the chat header, both directions (Local ↔ Worktree); the chat keeps its worktree; `.gitignore`d files move only via `.worktreeinclude`; `AGENTS.override.md` always copied | window `git` | **Built** (2026-10-16): **Hand off** to the local checkout or to the thread's worktree (a local thread gets a new one); the side the work leaves is snapshotted and cleaned, the side it reaches must be clean, a branch moves with the work; later turns run in the folder the work is in (`turn/start` `cwd`); `.worktreeinclude`'s ignored files and an ignored `AGENTS.override.md` are copied into a new worktree, never over a file, never a symlink | 2 |
| Codex-managed (disposable) vs permanent worktrees (a project of their own) | window | **Built** (2026-10-16): managed worktrees per thread; a permanent one from a project's button in the sidebar, listed as its own project and never cleaned up | 2 |
| Cleanup: keep the last 15 managed worktrees (Settings › Worktrees), never a pinned, running or permanent one; archive deletes; a snapshot before deletion and **Restore** on reopening | window | **Built** (2026-10-16): after a new worktree, the oldest beyond Settings › Worktrees' count (15) are removed unless running, shown or permanent (pinned threads join with item 3); archiving removes the thread's; each removal keeps a snapshot commit under `refs/mightling/snapshots/`, and reopening the thread offers **Restore** | 2 |
| Worktree root (Settings › Worktrees) | window prefs | **Built** (2026-10-16): root, the count and automatic deletion, written to `[desktop]` in `config.toml` through `config/value/write`, the keys the `codex-worktree` crate reads | 2 |
| Local environments (`.codex/` in the project, shareable): setup scripts per platform, run when a worktree is created; **actions** with icons in the top bar, run in the integrated terminal; **Run environment action 1** (Super+Shift+D) | window; `environment/*` | **Built** (2026-10-16) apart from the terminal: read from `.codex/` and `.mightling/`, written to `.mightling/environments/default.toml` only (§7 q3); the setup script runs when a worktree is made, the actions are buttons in the header and the bottom panel (Super+Shift+D runs the first), each run a tab of the bottom panel with its output in the user's shell (`tasks.ts`; the page names the environment and the action, the main process reads the script from the project and runs it only in the project or one of its worktrees), refused at `/airgapped on` since nothing sandboxes them; the vendor's file format is not documented, so `.codex/` is read in the shape its documentation describes. They move into terminal tabs with item 8 | 2 |
| Open in editor / terminal / file manager from the header | window | **Built** (2026-10-16): the header's Open: the chosen editor, the first terminal emulator found (Ptyxis, GNOME Terminal, Console, Konsole, …) or the file manager, in the folder the thread works in | 2 |
| Cloud environments, remote development environments | vendor | **No**; a node is reached through its own agent (NODE spec), not an environment | — |

### 2.7 Terminal

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| A terminal per chat, scoped to its project or worktree; **New tab › Terminal** or Ctrl+`; clear Ctrl+L | `command/exec` with `tty`, `write`, `resize`, `terminate` (or `process/*`); xterm.js in the window | **Same**, under the thread's sandbox profile so `/airgapped on` holds for what the user types too | 2 |
| The agent can read the terminal's output (a running dev server, a failed build) | the server ran it: `thread/backgroundTerminals/*`, `thread/shellCommand` | **Same** | 2 |
| Default terminal choice (Settings; on Windows PowerShell / cmd / Git Bash / WSL) | window prefs | **Same** (the user's `$SHELL` by default) | 2 |
| Where terminal tabs open by default; how much command output appears in chats (Settings › Project and terminal behaviour) | window prefs | **Same** | 2 |

### 2.8 Tabs, files and previews

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| File tree (Ctrl+Shift+E); search files (Ctrl+P); go to line (Ctrl+L) | `fs/readDirectory|readFile|watch`, `fuzzyFileSearch/*` | **Same** | 3 |
| File tabs: open a file from the tree, the review pane or a message; syntax highlighting | `fs/readFile`, window | **Same** | 3 |
| Artifacts viewer: previews of documents, presentations, spreadsheets and PDFs beside the chat; auto-open after a task; HTML preview with rendered / source toggle; standalone `.tex` edited beside a PDF compiled by the bundled compiler (`tectonic`) | window viewers; the vendor bundles `tectonic` | **Later:** Markdown, images, PDF and HTML in Phase 3; LaTeX if `tectonic` (Apache-2.0/MIT) is bundled; office formats through a local converter only if one is already installed | 3 |
| Annotations: select a region of a preview or page and ask for a focused change; annotation mode for websites and canvases (the vendor's extensibility API) | window | **Later** for previews Mightling renders; the website extensibility API is the vendor's | 3 |

### 2.9 The browser

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Built-in browser tab (Ctrl+T) with its own profile, history, downloads folder, address-bar search; split / full view | the vendor's Browser plugin on its Chromium | **Partial, later:** a **preview tab** (Electron `WebContentsView`) for local dev servers and file-backed pages, with the app's egress rules (loopback and the LAN only unless the user allows a site); no vendor plugin | 3 |
| Comment on the page (browse / comment mode, Ctrl+Shift+.) → comments to the composer | window | **Same** for the preview tab | 3 |
| Computer Use in the browser; the agent clicks, types, screenshots and verifies | vendor's computer-use runtime and model | **No** for now: Qwen3.8 is text-only here; a `browser_*` tool over the preview tab's DOM is a later idea once a vision-capable model is served | — |
| Agent permissions per site (browse / download / upload: require approval, always allow, block); developer mode (full CDP) | window | **Same** for the preview tab, as the `/airgapped` and egress rules already frame it | 3 |
| Browser extension for Chrome, Edge, Brave… | vendor | **No** | — |

### 2.10 Computer use, Appshots, Computer History

| Codex app | Mightling |
|---|---|
| Computer Use (macOS, Windows): see and operate desktop apps; always-allowed apps; locked use | **No:** needs a vision model and a desktop runtime the project does not have; Linux is not supported by the vendor either |
| Appshots: a screenshot of another app as context | **No** (macOS/Windows); paste a screenshot instead (§2.3) |
| Computer History: recent activity across apps as memories and a timeline | **No:** a telemetry-shaped feature; against Mightling's no-telemetry rule even locally until asked for |

### 2.11 Skills, plugins, MCP, apps, hooks

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Skills: list, enable / disable, create, `$` picker, where local skills load from | `skills/list`, `skills/config/write`, `skills/extraRoots/set`, `plugin/skill/read` | **Allowed** (`skills/list`); the UI and the `from-<agent>/` sources (SKILLS spec) to show | 2 |
| Plugins directory: tabs vendor / workspace / personal, **Installed** row, install (+), connect on first use, pin a plugin to the sidebar; `/plugins` | `plugin/list|search|install|uninstall|read|installed`, `marketplace/*` | **Same** for local and repository marketplaces; the vendor's catalogue stays closed (patch `0015`) | 2 |
| MCP: recommended servers, add your own, OAuth flow, status, reload, tool call, resource read; Settings › Integrations | `mcpServerStatus/list`, `config/mcpServer/reload`, `mcpServer/*` | **Allowed** (`mcpServerStatus/list`); the pages to build | 2 |
| Apps (Gmail, Drive…) | `app/list|read|installed` | **Mightling's:** MIGHTLING_APPS, local sign-in, read-only tools | 2 |
| Hooks (plugin hooks at lifecycle points) | `hooks/list`, `hook/started|completed` | **Same**, read-only list showing the ledger hook | 2 |
| Import another agent's configuration and histories | `externalAgentConfig/*` | **Same**, beside the SKILLS links | 3 |
| Sign in with the vendor's account at partner plugins | vendor | **No** | — |

### 2.12 Scheduled tasks (automations)

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| **Scheduled** in the sidebar: an inbox of runs with findings and an unread indicator; active / paused / completed tasks | the app's scheduler | **Mightling's: Night Shift.** The queue, the timer and the morning report already exist (`ling-admin night`); the Scheduled view shows them | 2 |
| Standalone task (a new chat per run) or a task inside an existing chat (returns to the same context) | app | **Same** for standalone; in-chat tasks map to `/night add` from the thread | 2 |
| Custom cadence, RFC 5545 RRULE | app | **Same** (Night Shift's timer takes a calendar expression; RRULE parsed into one) | 2 |
| Run in the local project or on a dedicated background worktree; the same task on several projects; model and effort per task; skills in the prompt; **Run now** | app | **Same:** Night Shift already runs each task in its own worktree; local mode and run-now to add | 2 |
| Worktree cleanup for scheduled tasks | app | **Same** (§2.6) | 2 |
| Event triggers (Gmail, Slack, GitHub), Team Tasks | vendor cloud | **No** | — |

### 2.13 Subagents and custom agents

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Spawn subagents on request or when `AGENTS.md` / a skill asks; agent threads visible; main thread collects results; `/agents` to inspect and switch | `ext/agent`, `agent-graph-store`; threads with a parent | **Same**; the server already has it (the TUI's `/agents`). Cost note: every subagent shares one GB10 | 2 |
| Custom agents with their own model and reasoning (`[agents]` in `config.toml`, custom agent files) | `config/*` | **Same**, one served model: custom agents differ by instructions and effort | 2 |
| Agent message board (shared discussions between agents) | `ext/agent-message-board` | **Later**, local backend only | 3 |

### 2.14 Memories

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Settings › Personalization: **Enable Codex memories**, **Allow memories from tool-assisted chats**, **Delete memories**; `/memories` per chat; files under `~/.codex/memories/`; `memories.*` keys | `thread/memoryMode/set`, `memory/status|reset`, `config/value/write` | **Same**, under `~/.mightling/memories/`; the rate-limit gate is moot here. Phase 0 check: the extraction and consolidation passes call only the served model | 2 |

### 2.15 Settings

The vendor's settings panel (Ctrl+,) has these pages. Mightling's reads and writes `~/.mightling/config.toml` through `config/read`, `config/value/write` and `config/batchWrite` for agent settings, and the window's own preferences file for window settings; the split is the vendor's too (agent settings are shared with the CLI, window settings are not).

| Page | Contents | Mightling |
|---|---|---|
| General | Enter behaviour; **Prevent sleep while running**; follow-up behaviour (steer / wait); Code review delivery | **Same** |
| Profile | Activity insights: lifetime and peak tokens, streaks, longest task; profile card | **Mightling's:** the same statistics from local session files; no sharing, no invitations |
| Keyboard shortcuts | Review, rebind, reset; search by command name or by keystroke | **Same** |
| Notifications | When turn-completion notifications appear; permission prompt | **Same** |
| Appearance | Base theme; accent, background and foreground colours; UI and code fonts; share a theme | **Same** in shape (§3.6 tokens); light base only until the dark-mode decision changes; theme sharing as a file |
| Pets | Built-in or custom pet, Mini, size | **Later (3)** with Mightling's own art |
| Browser | Install / enable, extension, permissions per site, history, downloads | **Same** for the preview tab (Phase 3) |
| Computer Use | Access review, always-allowed apps | **No** |
| Personalization | Edit personal `AGENTS.md` for this machine; repository instructions | **Same** (`~/.mightling/AGENTS.md`) |
| Suggested prompts | Context-aware follow-ups on start or return | **Later (3)**, generated by the served model between turns |
| Memories | Enable, tool-assisted chats, delete | **Same** |
| Archived chats | List with dates and project; **Unarchive** | **Same** (`thread/list` with archived filter, `thread/unarchive`) |
| Git | Branch naming; force push; commit-message and PR-description prompts; review delivery | **Same** |
| Project and terminal behaviour | Where files open; command output shown in chats; where terminal tabs open | **Same** |
| Worktrees | Worktree root; cleanup limit; automatic deletion on/off | **Same** |
| Integrations and MCP | Recommended servers, add your own, OAuth | **Same** |
| Developer mode | Full CDP access for the browser | **Same** for the preview tab (Phase 3), off by default |
| Agent | Native vs WSL (Windows) | **No** (Linux node; see WINDOWS_ARM for clients) |
| Code Review | Review instructions | **Same**, stored with `/review`'s instructions |

### 2.16 Shared history and hand-off

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Sessions shared with the CLI and the IDE extension | `~/.codex/sessions` | **Built:** `~/.mightling/sessions`; TUI threads appear in the app (measured, DESKTOP §5) | — |
| `/app` in the TUI opens the thread in the app; `codex://threads/<id>` | `codex app` | **Mightling's:** `ling app --thread <id>`; `/app` in the TUI needs one hook (DESKTOP §6.9) | 2 |
| IDE context toggle in the composer (files open in the editor) | IDE extension | **No:** no IDE extension (the MCP server for JetBrains and VS Code is a different thing) | — |

### 2.17 Platforms and packaging

| Codex app | Mightling |
|---|---|
| macOS, Windows (native PowerShell agent with the Windows sandbox, or WSL2), Linux preview (`.deb` x64/arm64, `.rpm`, Arch script; signed repositories; `--ozone-platform=wayland` flag) | Linux arm64 `.deb` for the node and clients (`ling-admin desktop install`, release assets); x86_64 Linux client build (CI); macOS and Windows client builds (RELEASE, WINDOWS_ARM); Wayland through Electron's defaults |
| Updates through the OS package manager | `ling update` and the release installer; no in-app updater |
| Run as administrator; execution policy notes | not applicable on the node |

### 2.18 Notifications of state

| Codex app | Mightling |
|---|---|
| Rate limits, credits, plan, workspace messages, sign-in | **No** (`account/*` is answered locally with nothing to show) |
| Model rerouted, safety buffering, moderation metadata | **No** (vendor models) |
| Windows sandbox readiness and setup, world-writable warnings | **Later**, with a Windows node (WINDOWS_ARM, windows-node research) |

### 2.19 Keyboard shortcuts (Linux)

The vendor's Linux bindings (read from its commands reference, 143 rows) equal its Windows column except Super for the Windows key. Mightling adopts them unchanged, so a user of both apps keeps one set of hands; the only Mightling-specific keys are the view switch. Rebindable in Settings (§2.15), stored with the window's preferences.

| Group | Action | Binding |
|---|---|---|
| General | Command menu | Ctrl+Shift+P or Ctrl+K |
| | Settings; keyboard shortcuts | Ctrl+, ; Ctrl+/ |
| | Open folder | Ctrl+O |
| | Back / forward | Ctrl+[ / Ctrl+] (and mouse back / forward) |
| | Font size + / − / reset | Ctrl+= / Ctrl+- / Ctrl+0 |
| | Toggle sidebar; bottom panel; terminal | Ctrl+B (or Ctrl+Shift+S); Ctrl+J; Ctrl+` |
| | Clear terminal (focused) | Ctrl+L |
| | Clear unread indicators | Shift+Esc |
| | Undo / redo app action | Ctrl+Z / Ctrl+Shift+Z |
| | Close tab; reopen; close others | Ctrl+W; Ctrl+Shift+T; Ctrl+Alt+W |
| | Full screen; quit | F11; Ctrl+Q |
| Chats | New chat; standalone chat; Quick chat (Ask) | Ctrl+N; Ctrl+Alt+O; Ctrl+Alt+N |
| | Archive; mark unread; pin; rename | Ctrl+Shift+A; Ctrl+Shift+U; Ctrl+Alt+P; Ctrl+Alt+R |
| | Side chat | Ctrl+Alt+S |
| | Find in chat; next; previous | Ctrl+F; Ctrl+G; Shift+F3 |
| | Previous / next chat or tab | Ctrl+Shift+Tab / Ctrl+Tab (or Ctrl+Page Up / Down) |
| | Next chat needing attention | Ctrl+Alt+A |
| | Recent chat 1–6; go to chat 1–9 | Ctrl+Alt+1–6; Ctrl+1–9 |
| Input | Model picker; recent models; project picker | Ctrl+Shift+M; Alt+M; Ctrl+Alt+Shift+O |
| | Restore previous prompt | ↑ (empty composer) |
| | Approve / decline | Enter / Esc |
| | Switch view: Ask / Work | Alt+1 / Alt+2 (the vendor's Chat / Work / Codex; Mightling's today are Ctrl+1 / Ctrl+2) |
| | Activity view | Ctrl+Alt+U |
| Workspace | Environment action 1 | Super+Shift+D |
| | Search files; file tree; review tab | Ctrl+P; Ctrl+Shift+E; Ctrl+Shift+G |
| | Chat ↔ tabs; cycle layout; full view | Ctrl+Alt+B; Ctrl+Shift+B; Ctrl+Shift+F |
| | Go to line / address bar | Ctrl+L |
| | Preview tab: open; back; forward; reload; copy URL; comment mode | Ctrl+T; Alt+←; Alt+→; Ctrl+R; Ctrl+Shift+C; Ctrl+Shift+. |
| | Copy conversation path; deep link; working directory | Ctrl+Alt+Shift+C; Ctrl+Alt+L; Ctrl+Shift+C |

A test holds this table as data and checks the window's keymap against it (§5).

### 2.20 Deep links and remote connections

The vendor's app registers a `codex://` scheme (its commands reference): `codex://threads/<id>`, `codex://threads/new`, `codex://new?prompt=…&path=…&originUrl=…` (a new chat with composer text, in a folder, or in the workspace whose git remote matches), `codex://settings`, `codex://settings/connections/{computer,devices,ssh}`, `codex://settings/connections/ssh/add?name=<ssh-config-host>`, `codex://skills`, `codex://automations` (Scheduled with the create flow open), `codex://plugins/install/<name>?marketplace=…`, `codex://plugins/<id>?hostId=local|<remote>`, `codex://pets/install?…`.

| Codex app | Mightling | Phase |
|---|---|---|
| `codex://threads/<id>`, `threads/new`, `new?prompt&path&originUrl` | **Same** as `ling://threads/<id>`, `ling://threads/new`, `ling://new?prompt&path&originUrl`, registered by the `.desktop` entry's `MimeType=x-scheme-handler/ling`; `ling app --thread <id>` stays the shell form | 2 |
| `codex://settings`, `codex://skills`, `codex://automations`, `codex://plugins/…` | **Same** as `ling://settings`, `ling://skills`, `ling://night` (the Scheduled view), `ling://plugins/…` | 2 |
| `codex://pets/install` | **Later (3)** with Mightling's pets | 3 |
| **Remote connections:** SSH hosts added from the SSH config become Codex hosts (`hostId`), so the app runs the agent on another machine; "control this Mac or PC from another device" | **Mightling's:** a node (NODE spec) is reached by its name, pairing and jobs, with the model server on the node and the agent local; the vendor's `ssh` host kind (DESKTOP §4.3, Phase 3) is the equivalent for running `ling app-server` on the node itself. Remote control of this machine from a phone is the vendor's service: **No** | 3 |

### 2.21 Not offered, and why

| Codex app | Why not |
|---|---|
| Sign-in, plans, credits, rate limits, profile sharing, invitations | No hosted account; `requiresOpenaiAuth: false` |
| Cloud tasks, Work Cloud sync, remote development environments, event-triggered tasks, Team Tasks, automatic cloud reviews | The vendor's cloud; `cloud` is refused by the launcher, remote control is `disabled` |
| Computer Use, Appshots, Computer History, the Browser plugin's agent runtime, image generation, deep research, Dots | Need the vendor's models or runtimes; Qwen3.8 is text-only; Linux unsupported by the vendor for most |
| The vendor's plugin directory, signing in to partners with the vendor's account | Patch `0015`; the vendor's identity |
| Feedback upload, analytics, attestation, user verification | Uploads to the vendor; no telemetry (product decision 2026-10-07) |
| IDE extension sync | No IDE extension |
| The vendor's pets, sounds, icons, strings | Trade dress |

---

## 3. The look: fonts, libraries, shapes

The vendor's product surface, read from its public design language rather than its bundle: a true-white canvas, near-black ink, one accent used sparingly, soft 8–12 px radii, pills for chips, hairline borders used rarely with whitespace as the divider, a sans-serif at weights 400/500/600 with generous line height. Its proprietary fonts (a commercial grotesque in its products, its own brand face in its marks) are not available to us and fall back to the system stack in its own CSS. Mightling takes the **structure** of that language and keeps its **own colours** (TIFFANY_BLUE `#0ABAB5` as the one accent, `onyx_brand_assets.py`) and its own mark.

### 3.1 Typography

| Role | Today (`desktop/ui/src/styles.css`) | Specified |
|---|---|---|
| UI font | `Roboto, "Hanken Grotesk", system-ui, sans-serif` (Roboto is rarely installed on Linux, so this is `system-ui` in practice) | `-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", Inter, Roboto, "Hanken Grotesk", sans-serif`: the system's own face on macOS and Windows, as the vendor's app uses, and **Inter** (OFL) everywhere else. `system-ui` is left out, since on Linux it is whatever the desktop configured (DejaVu Sans or Ubuntu here), so Inter is what Linux shows. Bundled as variable woff2 (Latin and Latin Extended) under `desktop/ui/src/fonts/` with its licence (the CSP already allows `font-src 'self'`; no remote font ever) |
| Code font | `"Roboto Mono", "DM Mono", ui-monospace, monospace` 12.5 px | `ui-monospace, "SF Mono", Menlo, Consolas, "JetBrains Mono", "Roboto Mono", monospace` 13 px; **JetBrains Mono** (OFL) bundled |
| Base size and leading | 14 px, browser default leading | 14 px UI, 15 px message text, line height 1.55–1.65 for prose and 1.4 for dense UI |
| Weights | 600 for titles | 400 body, 500 labels and navigation, 600 emphasis; never 700 |
| Headings in messages | browser defaults | 20 / 17 / 15 px at 600, tight leading, tracking normal |
| Labels | 12 px uppercase, 0.04 em (project headers) | 12 px at 500 in sentence case with a folder icon, as the vendor's project groups show; the threads beneath indented to the name |
| User-settable | — | Appearance settings (§2.15) override the UI and code font families and the base size (font-size shortcuts, §2.1) through the `:root` tokens of §3.6 |

### 3.2 Shape, surfaces and borders

- **Radii:** 8 px on controls and list rows, 12 px on inputs and cards, 16 px on the composer card and dialogs, 9999 px on chips. Today's 6 px become 8.
- **Borders:** hairline (`--line`), used for the sidebar edge, the composer card and tables; **turn separators go away** (today `.turn` has a bottom border): whitespace separates turns, 20 px.
- **Surfaces:** canvas white; sidebar a near-white panel (`--panel`); the composer card white with a hairline and a soft shadow only when focused; the user's message a soft tinted bubble (`--accent-soft`, 16 px radius, max-width 80%, right-aligned) and the agent's text plain on the canvas; code blocks and command output on `--panel` with 8 px radius.
- **Layout:** sidebar 260 px (today 280) collapsible to 0 (Ctrl+B); the thread column centred with a 760 px maximum width for text, full width for diffs and tables; the composer pinned at the bottom inside the column; tabs and the bottom panel as in §2.1.
- **Density:** 4 px base unit; 8 px gaps in controls, 12 px inside cards, 16 px between blocks, 24 px around the empty state.

### 3.3 Motion

150–220 ms for hover and selection, 280–360 ms for layout changes (sidebar, split view), `cubic-bezier(0.16, 1, 0.3, 1)`; fade and short translate only; `prefers-reduced-motion` turns transitions off.

### 3.4 Icons

Lucide (ISC), bundled as inline SVG, 16 px in rows and 20 px in the bar; Mightling's own mark for the app icon and the empty state. No emoji in controls (today's 📎 becomes the paperclip icon). Built as `desktop/ui/src/icons.tsx`: the path data of the icons used, copied from Lucide 0.460.0 with its ISC notice in `icons.LICENSE.txt`, rather than the `lucide-react` package (no new dependency in either `package.json`). The empty state's mark is the faceted ling of `images/mightling-logo-*.svg` without its wordmark, drawn from tokens; the app icon itself (`desktop/electron/icons/`) is unchanged (not replaced, the owner, 2026-10-16).

### 3.5 Libraries

All bundled at build time, licence-compatible with AGPL-3.0, no remote script, style or font (DESKTOP §8.1):

| Need | Library | Licence | Notes |
|---|---|---|---|
| UI | React 19, Vite 8, TypeScript | MIT | as today |
| Markdown | `marked` | MIT | as today; a sanitiser stays mandatory |
| Code highlighting | `shiki` (bundled grammars for the languages the agent edits) or `highlight.js` | MIT / BSD | `shiki` matches the vendor's rendering quality; its WASM needs `'wasm-unsafe-eval'`, already in the CSP |
| Diffs | own renderer over `git diff` output (unified and split), reusing the current `.diff` classes | — | `diff2html` (MIT) as the fallback if the own renderer costs more than a week |
| Mermaid | `mermaid` | MIT | rendered in a sandboxed iframe with `srcdoc`, since it evaluates text |
| Terminal | `@xterm/xterm` with `addon-fit`, `addon-web-links` (loopback links only) | MIT | over `command/exec` tty or `process/*` |
| Long lists | `@tanstack/react-virtual` | MIT | thread lists and long transcripts |
| Icons | Lucide (path data in `icons.tsx`; `lucide-react` if the set grows past a few dozen) | ISC | |
| Fonts | Inter, JetBrains Mono | OFL 1.1 | woff2, latin subsets |
| Command menu, shortcuts, tabs, find | own components | — | small, and the vendor's are not available |

Electron stays stock 42.x pinned (ELECTRON spec): no custom Chromium, no `--no-sandbox`, no GPU switches.

### 3.6 Theme tokens

`styles.css` keeps every colour, radius and font in `:root` tokens; the Appearance settings write overrides into a `<style>` the main process stores with the window's preferences. The token set (the first four lines are the minimum `styles.test.ts` checks; the rest came with the build of 2026-10-15):

```
--font-ui, --font-code, --size-base
--canvas, --panel, --text, --muted, --line, --accent, --accent-soft, --danger, --add, --del
--radius-sm 8px, --radius-md 12px, --radius-lg 16px
--motion-fast 180ms, --motion-slow 320ms
--size-prose 15px, --raised, --code-bg, --faint, --line-strong, --hover, --selected, --on-accent,
--focus-ring, --add-text, --del-text, --notice, --shadow, --shadow-focus, --scrim, --scrollbar,
--mark-* (the empty state's mark), --radius-pill, --ease, --column 760px, --sidebar 260px
```

Light only (dark not approved 2026-10-16): there is one colour set, on `:root`, with `color-scheme: light`, and nothing follows the system theme. `html[data-theme]` is set to `light` by the preload before the first paint and by `main.tsx`; the title bar overlay's symbols are fixed dark; no `nativeTheme` change crosses the bridge. `styles.test.ts` checks that no dark block and no `prefers-color-scheme` query is in the stylesheet. Should dark mode be approved, it is one more block, `:root[data-theme="dark"]`, redefining every colour token except `--accent` (built and shown on `desktop/codex-look` at 99bf80a, then removed before the merge), plus the theme's follower in the preload, `main.tsx` and the main process.

### 3.7 What stays Mightling's

The name and wordmark; TIFFANY_BLUE as the accent and the air-gap chip's colour; the empty-state copy; the ✅/⚠️ voice of CLI output stays in the CLI, not in the app; the model, node and index chips of DESKTOP §7.

---

## 4. Gaps and phases

### 4.1 Built today (1.6.1)

One window with Ask and Work; one `ling app-server` on stdio through the vetted IPC channel. **Allowed** (43 of the 170 client requests: initialize, account/read, config read/write, model/list, permission profiles, collaboration modes, project list/read/create/update, thread start/resume/fork/list/read/loaded/turns/items/search/name/metadata/archive/unarchive/unsubscribe/compact/revert, queue add/list/update/delete/reorder/start, turn start/steer/interrupt, fuzzyFileSearch, gitDiffToRemote, skills/list, mcpServerStatus/list, app/list). **Built** (a UI exists): threads grouped by folder with rename, search, archive, running markers and a per-turn revert link; streaming items (messages, reasoning, commands, file changes, plan, the turn's diff); inline approvals; the permissions picker with the air-gap courtesy; Steer and Stop; a compact link; attachments in Ask; the served-model, usage and air-gap chips; the devices link for `ling web`; the start-up screen; narrow-window drawer; `ling web` serving the same UI to browsers with the same policy; the egress audit's `--app` mode; Night Shift's busy marker. **Allowed without a UI:** project create/update, fork, the queue, `fuzzyFileSearch`, `collaborationMode/list`, `skills/list`, `mcpServerStatus/list`, `app/list`.

### 4.2 Phase 2 — the Codex surface (this spec's main body)

In the order the owner's use suggests, each item with its §2 row as acceptance:

1. **Look** (§3): tokens, fonts, radii, spacing, the composer card, the bubble, no turn separators, icons. One change, no behaviour. **Look: merged 2026-10-16** (light only; the app icon unchanged).
2. **Shell:** sidebar toggle, command menu, back/forward, font size, undo/redo, close/reopen tabs, the keymap table and its settings page (§2.1, §2.19).
3. **Threads:** pins, sections, Recents, unread and attention, mark unread, find in chat, deep links and `ling://`, pop-out with always-on-top, side chat, standalone chat (§2.1).
4. **Composer:** `@` mentions, `$` skills, `/` commands, Enter behaviour, follow-up behaviour, ↑ restore, Shift+drag images, approve/decline keys, edit previous, fork, Compress, the model and effort picker (§2.3). **Composer: merged 2026-10-16, eca4a6a** (the slash commands Partial: the ones whose methods come with later items wait for them).
5. **Plan and goal:** collaboration mode, the goal row (§2.3).
6. **Review pane and git:** scopes, repository selector, stage/unstage/revert at three levels, inline comments, viewed markers, open in editor, `/review` inline or detached, commit, push, PR page (§2.5). **Review pane and git: merged 2026-10-16, 7973c5a** (`/review` Partial: it needs `review/start` in `policy.json`, held on `desktop/p2-policy` for the Rust conformance run).
7. **Worktrees and hand-off**, local environments and actions, the bottom panel (§2.6). **Worktrees, hand-off, environments and the bottom panel: merged 2026-10-16, MERGE7** (window-side `git` on the `codex-worktree` contract until `ling worktree` exists; actions run as bottom-panel tasks until the terminal of item 8 lands).
8. **Terminal** tabs (§2.7).
9. **Tabs and layout:** split, full, hidden (§2.1).
10. **Settings pages** of §2.15 except Pets, Browser, Computer Use, Suggested prompts.
11. **Skills, plugins, MCP, apps, hooks** pages (§2.11).
12. **Scheduled** view over Night Shift (§2.12).
13. **Subagents** panel; **memories** controls (§2.13, §2.14).
14. **Notifications**, prevent sleep (§2.1).

### 4.3 Phase 3 — the workstation extras

File tree and file tabs; search files and go to line; the artifacts viewer (Markdown, images, PDF, HTML; LaTeX if `tectonic` is bundled); the preview tab with comments and per-site permissions; Activity view; pets with Mightling's art; suggested prompts; external agent import; the `gh`-backed pull-request tab; agent message board; dark mode and the Appearance page's light/dark/system choice, if dark is ever approved; macOS and Windows app builds (RELEASE, WINDOWS_ARM).

### 4.4 Not proposed

§2.21.

---

## 5. Tests and acceptance

- **Protocol inventory drift:** a test reads the method names of the four generated schema files (`app-server-protocol/schema/typescript/ClientRequest.ts`, `ServerRequest.ts`, `ServerNotification.ts`, `ClientNotification.ts`), compares them with the copy under `desktop/ui/src/protocol/` and with a checked-in list grouped as §1.4; a Codex bump that changes it fails until this spec and `policy.json` are reviewed. Built with item 4 (`tests/test_desktop_parity.py`; the submodule's schema is compared when the checkout has it).
- **Policy coverage:** every §2 row marked Built or Same with a method names a method present in `policy.json` or in a list of methods still to add; the test prints the methods a phase still lacks. Built with item 4 (`tests/test_desktop_parity.py`, with the inventory drift test): `POLICY_TO_ADD` names each missing method's item, and must equal what the parse of §2 finds missing.
- **Keymap:** §2.19 as JSON under `desktop/ui/src/keymap.json`; a test checks it against the UI's registered bindings and that no two actions share a binding in the same context. Built with item 4 (`keymap.test.ts`): each action carries `built` or `planned`, and a handler names its action with `keyAction("<id>")`, which the test finds in the sources.
- **Look:** a snapshot test of the rendered tokens (`getComputedStyle` of `:root` in the test renderer) against §3.6; a grep test that no stylesheet references a remote URL and that the only font sources are bundled files.
- **Per feature:** the scripted app-server stand-in (as today's `ask.test.ts` and `work.test.ts`) gains one scenario per §2 row as it ships: a worktree created and handed off against a scratch repository, a review-pane stage per hunk, a terminal tab echoing a command, a goal row paused and resumed, a queued follow-up started, a pinned thread sorted first.
- **Egress:** `ling-admin audit egress --app` passes with every Phase 2 feature on; the preview tab (Phase 3) adds its own allow-list cases.
- **E2E:** Playwright-Electron (`npm run e2e`) opens the one window, switches Ask/Work, opens a terminal tab and a review tab, and confirms one app-server process throughout.
- **Never on the real machine:** no test starts the real window against the real model server (`tests/conftest.py`).

---

## 6. Risks

- **The vendor's surface moved a lot in six months** (the 2026-10-03 inventory to this one: tabs, layout, Scheduled inbox, memories, subagents, pets, browser, Code Review plugin). Parity is a decision each time, taken against the dated inventory in §2; the drift test of §5 catches protocol changes, not window changes.
- **Window-side git is real engineering:** worktrees, hand-off, per-hunk staging and the snapshot/restore of deleted worktrees are where the vendor spent its own effort; the `codex-worktree` crate covers creation and the directory contract only.
- **One model server:** every parallel thread, subagent and side chat shares one GB10; the headroom chip must be honest and the composer should warn before a fourth parallel turn.
- **The experimental API:** projects, queues, sections, goals and search are experimental methods that change between Codex releases; generated types turn breakage into compile errors but not into feature loss.
- **Trade dress:** §3 stays on the structure side of the line; a review before the look ships compares screenshots with the vendor's for colour and copy.

---

## 7. Open questions

1. ~~**Bundle fonts or not?**~~ Bundled on `desktop/codex-look`: Inter and JetBrains Mono as variable woff2, Latin and Latin Extended, 185 KB together, with their OFL licences beside them.
2. ~~**Dark mode**~~ (DESKTOP §14 question 3): **dark mode not approved 2026-10-16, light only; the Appearance page's choice is Phase 3.** It was built on `desktop/codex-look` as the system theme's follower (screenshots in both themes, 2026-10-15); the owner kept the 2026-10-03 light-only decision, so the merge removed the dark block and the follower and kept the tokens (§3.6).
3. ~~**`.codex/` or `.mightling/` for local environments?**~~ **Decided 2026-10-16 (the owner): read both `.codex/` and `.mightling/` in a project, write only `.mightling/`** (built with item 7: `.mightling/environments/default.toml`, `name`, `[setup] script` with `linux`/`macos`/`windows` overrides, `[[actions]]` `name`, `icon`, `script`; `.codex/environments/*.toml` and `environment.toml` are read in that shape, since the vendor's generated file is not documented). Reading `.codex/` keeps shared repositories working for Codex users; Mightling's settings pages never put a vendor-named folder in the user's project.
4. **`tectonic` in the bundle** for LaTeX previews (Phase 3): about 30 MB. Recommendation: later, behind a setting.
5. **Pets:** worth Mightling's own art at all? Recommendation: ask the owner once Phase 2 ships.

---

## 8. Sources

- The vendor's documentation, read 2026-10-14 (Markdown twins of `learn.chatgpt.com/docs/…`): `app`, `reference/commands`, `reference/settings`, `projects`, `code-review`, `environments/git-worktrees`, `environments/local-environment`, `integrated-terminal`, `developer-settings`, `long-running-work`, `automations`, `agent-configuration/subagents`, `customization/memories`, `pets`, `browser`, `computer-use`, `artifacts-viewer`, `annotations-extensibility`, `plugins`, `extend/mcp`, `agent-approvals-security`, `sandboxing`, `permissions`, `image-inputs`, `quickstart`, `linux/linux-app`, `windows/windows-app`, `app-server`, `customization/computer-history`.
- The videos the owner named: "The Codex app is now on Windows" (`youtube.com/watch?v=8hNcRChDrNk`), "A first look at the Codex app" (`0e-Brv-gS9Q`), "The Complete Guide to OpenAI's Codex App" (`yp_sIdLVtIA`); also found: "How To Navigate OpenAI Codex Desktop GUI 2026" (`CvRcHNBEfdo`), "Complete Beginner's Guide to OpenAI's Codex App" (`nQFtsehu7h0`), "OpenAI Codex in 7 Minutes" (`2OnmwXm6N4U`). Their frames and narration could not be read from here; their titles and published descriptions were cross-checked against the documentation above, which is the authority for every row of §2.
- The Codex source pinned in `codex/` (`rust-v0.158.0`): `codex-rs/Cargo.toml` (workspace members), `cli/src/main.rs` (subcommands), `app-server/src/` (processors, transports, in-process host, daemon recovery), `app-server-protocol/src/protocol/common.rs` (method names), `tui/src/slash_command.rs`, the crate-level docs of `core-api`, `thread-store`, `rollout`, `state`, `file-watcher`, `ext/queue`, `ext/goal`, `app-server-daemon`, `app-server-client`.
- The vendor's app bundle as read on 2026-10-03 (DESKTOP §1): spawn line, `clientInfo`, bundled extras, telemetry.
- A public description of the vendor's design language (`nexu-io/open-design`, `design-systems/openai/DESIGN.md`): fonts, weights, radii, spacing, motion. Colours were read and not taken.
- Mightling: `docs/dev/desktop.md`, `ling-rs/web/policy.json`, `desktop/ui/src/styles.css`, `desktop/electron/src/`, DESKTOP, DESKTOP_ELECTRON and ASK specs.
