# Mightling Desktop — parity with the Codex app, October 2026: every feature, the binary's architecture, and the look

> - **Status:** proposed 2026-10-14 (owner: "I want the same functionality", "the same architecture as the Codex binary", "make it look like the original Codex desktop"). Supersedes §2 and §6 of [DREAMFERENCE_MIGHTLING_DESKTOP.md](./DREAMFERENCE_MIGHTLING_DESKTOP.md) (2026-10-03) as the feature inventory; that document's §3 (what identical can mean), §8 (security) and §13 (risks) still hold, and [DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md](./DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md) and [DREAMFERENCE_MIGHTLING_ASK.md](./DREAMFERENCE_MIGHTLING_ASK.md) describe what is built.
> - **What "the Codex app" is now:** the upstream vendor's documentation describes one **desktop app** (macOS, Windows, and a Linux preview) with three surfaces, Chat, Work and Codex; this document covers the Codex surface. Every feature below is read from the vendor's documentation as it stood on 2026-10-14, from the three videos the owner named (§8), and from the Codex source pinned in `codex/` (`rust-v0.158.0`). Nothing below was read from the vendor's app bundle (the 2026-10-03 reading of its package was removed from these specs on 2026-10-21, §9.5 item 11).
> - **Trade dress (DESKTOP §13):** layout and interaction may follow the vendor's; name, icon, colours, sounds and copy must not. §3 stays inside that line.
> - **Where it stands (2026-10-20):** Phase 2 (§4.2) merged 2026-10-16 to 2026-10-18, its held work as 1d9eb48 on 2026-10-18; Phase 3 (§4.3) items 1 to 10 and 12 merged on 2026-10-18 (item 12: the Windows app built in CI as an unsigned zip, 54a4578); item 11, dark mode, approved by the owner on 2026-10-20 and **built** the same day (branch `desktop/dark-mode`, for 1.7.1). Since 2026-10-19 a finished turn keeps what it streamed (c2bcc7a, `desktop/ui/src/store.ts`). Rows of §2 written before a merge are flipped below with the date and commit; the app's shell facts (Electron 42.11.11, Forge 8.0.1, one window, `ling-rs/web/policy.json`) are this document's, and DESKTOP and DESKTOP_ELECTRON point here for them.
> - **The study of 2026-10-20** (§9): the vendor's app as it is today, from public material only, read against the code; its gaps are Phase 4, items 13 to 40 (§4.5), and its open choices §9.5 (1, 2, 3, 6 and 7 in part decided 2026-10-21, the study merged to main the same day); items 14, 15, 19 and 20 merged on 2026-10-20 (ec3900e, 2b61a54, e4a6f4d, ac08ed2), the rest open.
> - **Dark mode** was approved on 2026-10-20 (it had been refused on 2026-10-16, keeping DESKTOP §14 question 3's light only of 2026-10-03, and the look of §3 merged without it): System (the default, following the OS), Light and Dark in Settings › Appearance, in the app and on `ling web` (§3.6, item 11).

---

## 0. Summary

> **Dated (2026-10-19):** this summary is the 2026-10-14 starting point. `policy.json` now allows 67 of the 170 client requests, and the window-side features it calls missing (tabs, terminal, review pane, worktrees, settings, shortcuts) were built in Phase 2 (§4.2). §4 is the current status.

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
| Desktop app (vendor) | A rich client of app-server, which the vendor documents as "the interface Codex uses to power rich clients" ([app-server](https://learn.chatgpt.com/docs/app-server)) |
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
- **The window owns the workstation.** Git operations beyond the diff (worktree creation, stage, commit, push, pull requests), the terminal emulator, tabs and layout, previews, notifications, settings pages, shortcuts, pets: these live in the vendor's Electron main process (DESKTOP §5: the protocol has no commit, push, worktree or PR method; `codex-worktree` is a crate the TUI links, not a server method).
- **Features are extensions.** `/goal`, the message queue, memories, guardian review, skills, MCP, subagents, web search and image generation are `ext/*` crates registered through `ext/extension-api`. Mightling's patch `0017` registers its own extension there (DESKTOP §8.2). Anything Mightling adds to the agent should be an extension before it is a patch.

### 1.3 The app-server

- **Transports** (`app-server/src/main.rs`, the vendor's app-server document): `--listen stdio://` (default, newline-delimited JSON), `ws://IP:PORT` (experimental; also serves `/readyz` and `/healthz`; auth by capability token or signed bearer token), `unix://` (WebSocket over Codex's control socket or a path), `off`. `--code-mode-host wss://…` points every thread at a remote Code Mode host. `app-server-daemon` keeps one managed server alive across CLI invocations and the updater; `daemon_thread_recovery` resumes persistent root threads after a daemon restart.
- **Lifecycle:** one `initialize` per connection (`clientInfo`, `capabilities.experimentalApi`, `optOutNotificationMethods`, `requestAttestation`, `mcpServerOpenaiFormElicitation`), then `initialized`; `thread/start|resume|fork`; `turn/start`; `turn/steer`; notifications until `turn/completed`; `turn/interrupt`.
- **Primitives:** thread → turns → items (user message, agent message, reasoning, command execution, file change, MCP tool call, plan, tool call, user-input request, permissions request, auto-approval review).
- **Dispatch:** `message_processor` routes each request to a domain processor (`request_processors/`), gates experimental methods (`connection_rpc_gate`), and fans notifications out per subscribed thread (`outgoing_message`). `config_manager` layers `config.toml`, profiles, project `.codex/config.toml`, managed requirements and `-c` overrides; `model_catalog` and `models_refresh_worker` serve `model/list`; `fs_watch` and `fuzzy_file_search` back the file tree and `@` mentions; `code_mode_host` starts the Code Mode host when `features.code_mode_host` is on.

### 1.4 The protocol, by domain

The methods, read from the generated schema `{ClientRequest,ServerRequest,ServerNotification,ClientNotification}.ts` of the experimental export (`generate-ts --experimental`; 0.158.0: 170 client requests, 11 server requests, 85 server notifications, 1 client notification, recounted 2026-10-16 from the committed types by `tests/test_desktop_parity.py`, which holds them as `desktop/ui/src/protocol-inventory.json` grouped by the domains below; the 2026-10-14 count of 107 and 10 left out v1 and test methods; the same files `desktop/ui/src/protocol/` carries), grouped here by domain. Names are what a client sends or receives; the v1 methods `initialize`, `fuzzyFileSearch`, `gitDiffToRemote`, `getAuthStatus` and `getConversationSummary` have no slash:

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
| A client names itself in `initialize`'s `clientInfo` | `mightling_desktop` / "Mightling Desktop", `experimentalApi: true` |
| The vendor's desktop shell | Stock Electron 42.11.11 pinned exactly (`desktop/electron/package.json`), Vite 8, Forge 8.0.1, `maker-deb` (Linux) and `maker-zip` (the Windows app, §2.17) |
| Window → main process IPC, unvetted | One IPC channel, every request vetted against `ling-rs/web/policy.json` (67 requests, 1 notification, 10 dropped thread fields on 2026-10-19; 43 requests on 2026-10-14), the same file `ling web` enforces; both run `ling-rs/web/vectors/` |
| Telemetry | None; egress closed in the main process; `ling-admin audit egress --app` |
| Window-side git, terminal, previews | Built: review pane and git 2026-10-16 (7973c5a), worktrees 2026-10-16 (9c5f31d), terminal tabs 2026-10-18 (1d9eb48), preview tab 2026-10-18 (a435355) (§4) |

**The mandate, stated once:** a feature in §2 is implemented at the layer the vendor implements it at. Server features use the server's method and nothing else (the policy allow-list grows by that method, with a vector); window features live in the main process (`desktop/electron/src/`) or the UI (`desktop/ui/`), never in a second agent process. Where the vendor's window calls `git`, Mightling's calls `git`. Where the vendor added a crate (`codex-worktree`) rather than a method, Mightling links the same crate from `ling-rs/` and exposes it to the window through the launcher (`ling worktree …`, as `ling airgapped --thread` already is) rather than through a new protocol method, provided the crate builds outside Codex's workspace (DESKTOP §11 Phase 0 item 9, open); the fallback is the window calling `git` as the vendor's does.

### 1.6 The window, module by module

> **Dated (2026-10-19):** the "Mightling today" column is 2026-10-14's. Phase 2 and 3 added the modules of the third column under other names in places (`notices.ts`, not `notify.ts`; `windows.ts`, `worktree.ts`, `tasks.ts`, `editors.ts`, `night.ts`, `insights.ts`, `preview.ts`, `prs.ts`, `board.ts`, `pet.ts`); `desktop/electron/src/` and [docs/dev/desktop.md](../docs/dev/desktop.md) are the current list.

The vendor's window is a desktop shell around its UI. Its modules, inferred from what the protocol does not provide and what the documentation attributes to the app, against `desktop/electron/src/` today and what Phase 2 adds:

| Window module (vendor) | Mightling today (`desktop/electron/src/`) | Phase 2 |
|---|---|---|
| Spawn the agent (an app-server) | `server.ts` (spawns `<resources>/ling … app-server`) | unchanged |
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
| One window; three surfaces: Chat, Work, Codex (Alt+1/2/3 on Windows and Linux) | window | **Mightling's:** two views, Ask and Work, **Built**; since item 2 (2026-10-16) Alt+1 / Alt+2 switch them, handled by the page (the menu shows the keys without registering them), so Ctrl+1–9 are free for threads | 2 |
| Sidebar: projects with pinned ones on top, Recents, New chat, Scheduled, Plugins (pinnable), Code Review, Search chats; toggle Ctrl+B | window; `project/list`, `thread/list`, `threadSection/*` | **Built** (2026-10-16, items 2 and 3; Scheduled, item 12, merged 2026-10-17, 84d52b3; Plugins, item 11, merged 2026-10-17, f0477c6, and 2026-10-18, 1d9eb48): pinned threads first, then Recent (folding), the user's sections, then projects with pinned projects on top; rename, search, archive; Ctrl+B. Sections are kept in the window's preferences until `threadSection/*` is in `policy.json` | 2 |
| Pin a project or a chat | `thread/metadata/update` (pin flag), window prefs for projects | **Built** (2026-10-16): in the window's preferences, since the protocol has no pin flag (`thread/metadata/update` carries none); Ctrl+Alt+P; Ctrl+Z unpins | 2 |
| Mark chat unread; unread indicators; next chat needing attention (Ctrl+Alt+A); clear all (Shift+Esc) | window state over `thread/status/changed`, `item/*/requestApproval` | **Built** (2026-10-16): a turn that ends while its thread is not in front (or the window is not focused) leaves it unread; a mark for a waiting approval or question; Ctrl+Shift+U, Ctrl+Alt+A, Shift+Esc | 2 |
| Command menu (Ctrl+K / Ctrl+Shift+P): commands, threads, projects, settings | window | **Built** (2026-10-16): commands (the keymap's built actions with their keys), threads, projects and settings pages, filtered as you type | 2 |
| Navigate back / forward (Ctrl+[ / Ctrl+]) | window history | **Built** (2026-10-16): the view and thread the window showed, Ctrl+[ / Ctrl+], the header's arrows and the mouse's side buttons | 2 |
| Tabs per chat: terminal, browser, file, review; close / reopen / close others; next / previous tab; go to chat 1–9; recent chat 1–6 | window | **Partial** (2026-10-16): the thread keys work (previous/next Ctrl+Shift+Tab / Ctrl+Tab in sidebar order, go to thread 1–9 Ctrl+1–9, recent thread 1–6 Ctrl+Alt+1–6); the review pane, the bottom panel's terminal tabs and close/reopen are built (items 2, 6, 7, 8); the tab layout itself is item 9 (merged 2026-10-17, 1064dc9); file tabs merged 2026-10-18 (Phase 3 item 1, 93426fd) | 2 |
| Workspace layout: full view, split view (chat beside tabs), hidden tabs (Ctrl+Shift+B, Ctrl+Shift+F, Ctrl+Alt+B) | window | **Built** (2026-10-17): the tab area beside the thread (split), in full (Ctrl+Shift+F), or hidden while its tabs stay open (Ctrl+Alt+B toggles thread and tabs, Ctrl+Shift+B cycles the three), with a layout switch on the tab strip; the layout is a window preference. The tab area holds the Review tab; terminal tabs open in the bottom panel (item 8), file tabs are Phase 3 (merged 2026-10-18, 93426fd) | 2 |
| Bottom panel (Ctrl+J) | window | **Built** (2026-10-16, item 7): environment setup and action runs, each a tab with its output and Stop, and the environment editor; terminal tabs join with item 8 | 2 |
| Font size: increase / decrease / reset (Ctrl+= / Ctrl+- / Ctrl+0) | window (`webContents.setZoomLevel` or a CSS variable) | **Built** (2026-10-16): CSS zoom on the page in steps, kept in the window's preferences, also a slider in Settings › General | 2 |
| Undo / redo the last app action (archive, rename, pin…) | window | **Built** (2026-10-16): archive (undone by `thread/unarchive`) and rename; Ctrl+Z belongs to a text field when one has focus; pin joins with item 3 | 2 |
| Full screen (F11), quit (Ctrl+Q), close tab or window (Ctrl+W) | window | **Built** (2026-10-16): F11 and Ctrl+Q from the page's keymap; Ctrl+W closes the frontmost surface (the review pane, then the bottom panel) and never the window, Ctrl+Shift+T reopens it, Ctrl+Alt+W closes both; tabs proper come with item 9 | 2 |
| Pop-out: a chat in its own window, **Always on top** | window | **Built** (2026-10-16): the header's pop-out and the row menu open the thread in a second window on the same app-server (the main process tags each window's request ids and answers a later window's `initialize` itself, `windows.ts`), with **Always on top** | 2 |
| Quick chat (Ctrl+Alt+N): an ordinary chat of the vendor's chat surface beside Codex | the vendor's Chat surface | **Built** (2026-10-16), **Mightling's:** Ask is Quick chat; Ctrl+Alt+N opens a new question | 2 |
| Side chat (Ctrl+Alt+S): a status recap without interrupting the main chat | a second thread with the main thread's context | **Built** (2026-10-16): `thread/fork`, then a turn asking for a short recap with nothing changed, opened in a pop-out (in a browser, selected) | 2 |
| Standalone chat (Ctrl+Alt+O): a chat with no project | `thread/start` with `cwd` = home | **Built** (2026-10-16): a new thread in the home folder | 2 |
| Search chats (unassigned shortcut); Find in chat (Ctrl+F, Ctrl+G, Shift+F3) | `thread/search`, `thread/searchOccurrences`; window find | **Built** (2026-10-16): the sidebar search, and find in the shown thread (Ctrl+F, Ctrl+G and Enter, Shift+F3 and Shift+Enter) marked with the CSS Custom Highlight API, so nothing in a streaming turn is changed; `thread/searchOccurrences` is not needed for it | 2 |
| Copy chat deep link (Ctrl+Alt+L), copy conversation path, copy working directory | window; `ling://threads/<id>` (§2.20) | **Built** (2026-10-16): `ling://threads/<id>`, the conversation's file (`thread.path`), the working directory | 2 |
| Notifications when a turn completes or needs approval; prompt for permission | window (`Notification`) | **Built** (2026-10-17): each thread's finished (or stopped) turn and each waiting approval or question, never a throwaway thread's or a subagent's turn (`notices.ts`), silent, as Settings › Notifications says (window not in front, always, never), a click shows the thread; a test notification there. Linux asks no permission for them | 2 |
| Prevent sleep while running | window (`powerSaveBlocker`) | **Built**: `powerSaveBlocker` while any turn runs, switchable in Settings › General (item 10) | 2 |
| Appshots (screenshot of another app as context, Cmd+Cmd) | macOS/Windows runtime | **No:** Linux first; a screenshot attachment is §2.3's image input | — |
| Pets: a floating animated companion with chat controls (Option+Space / Win+Alt+P), status, custom pets | window overlay | **Built** (2026-10-18, Phase 3 item 6, 0096587): Mightling's own ling, off by default; the vendor's pets are its trade dress | 3 |

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
| Plan mode (`/plan`), interview-then-goal | `turn/start` `collaborationMode` | **Built** (2026-10-17): the composer's **Plan** chip and `/plan` (with words: Plan mode on and the words in the composer) send `turn/start` with the `plan` collaboration mode on the served model and effort, offered when `collaborationMode/list` lists it; the interview that ends in a goal waits for the goal row | 2 |
| Goal mode (`/goal`): the goal is prompt and completion criteria; a progress row above the composer with pause, resume, edit, clear | `thread/goal/set|get|clear`, `thread/goal/updated` | **Built** (2026-10-17 on `desktop/p2-5b-goal`; merged 2026-10-18, 1d9eb48, with `thread/goal/get|set|clear` in `policy.json`): `/goal <objective>` sets it (no words: the row opens for one), the row above the composer shows the objective, how it stands, the tokens and time used, and Pause, Resume, Edit, Clear; `thread/goal/updated` and `cleared` keep it current | 2 |
| Composer predictions (suggested completions while typing) | vendor | **Later:** needs a fast local completion path (the engine's or the sidecar's); COMPACTION §4.6's fast slot | 3 |
| Voice chat (Ctrl+Shift+V) | `thread/realtime/*`, vendor realtime | **Mightling's:** the speech-to-text sidecar (`dream-stt`) for dictation; `thread/realtime/*` never sent | 2 |
| Approve (Enter) / decline (Esc) an open approval | `item/*/requestApproval` answers | **Built** (2026-10-16): with nothing typed, Enter approves and Esc declines the first open approval of the shown thread | 2 |
| Steer while running; queued follow-ups; stop (Esc) | `turn/steer`, `thread/queue/*`, `turn/interrupt` | **Built** | — |
| Edit the previous message (Esc Esc) | `thread/revert` then `turn/start` | **Built** (2026-10-16): Esc Esc on an empty composer, `/edit` or the **Edit** link on the last turn reverts it (`thread/revert`) and puts its text back in the composer | 2 |
| Fork a thread | `thread/fork` | **Built** (2026-10-16): the fork button in the header and `/fork` | 2 |
| Context indicator and **Compress** | `thread/tokenUsage/updated`, `thread/compact/start` | **Built** (usage chip, its compress link, and `/compact`) | — |
| Slash commands of the TUI available in the app (`/model`, `/permissions`, `/review`, `/plan`, `/goal`, `/memories`, `/skills`, `/mcp`, `/plugins`, `/agents`, `/side`, `/btw`, `/compact`, `/recap`, `/diff`, `/status`, `/new`, `/resume`, `/fork`, `/worktree`, `/rename`, `/archive`, `/pet` …; the pinned TUI's list is in `tui/src/slash_command.rs`) | window parses `/`; each maps to a method or a window action | **Partial** (2026-10-16): `/new`, `/fork`, `/compact`, `/edit`, `/rename`, `/archive`, `/model`, `/permissions`, `/status`, `/skills`, `/mention`, `/mcp`, `/diff` (`gitDiffToRemote`), `/copy`, `/resume` and Mightling's `/airgapped` (status, and `default off|on` through `ling airgapped default`; a thread's own level is set inside a `ling` session, since the launcher has no `--thread` setter) are Built, each described in Mightling's words; `/review`, `/plan`, `/goal`, `/memories`, `/agents`, `/plugins`, `/side`, `/worktree` come with their items (5, 6, 7, 11, 13; side chat with 3); `/cavemode`, `/night`, `/prompt`, `/apps` with 10 and 12. **Since then** (2026-10-17 to 2026-10-18) `/plan`, `/goal`, `/review`, `/memories`, `/agents`, `/night` and `/apps` are in the composer's list too (`desktop/ui/src/composer.ts`, 2026-10-19); `/plugins`, `/side`, `/worktree`, `/cavemode` and `/prompt` are not | 2 |

### 2.4 The stream

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Items: user and agent messages, reasoning (collapsible), commands with live output, file changes with patches, plan updates, MCP and tool calls, user-input requests | `item/*` notifications | **Built** (messages, reasoning, commands, files, plan, diff); tool-call and user-input items to check. Since 2026-10-19 (c2bcc7a) a finished turn keeps what it streamed: `ling`'s `turn/completed` lists only the answer, which had replaced the question, the commands and the diff until the thread was reopened | 2 |
| Markdown and Mermaid | window | **Partial:** Markdown built (`marked`); Mermaid to bundle | 2 |
| Inline approvals: command, patch, permissions; strict review required | `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `autoApprovalReview/strictReviewRequired` | **Built** for the first three; the air-gap rule holds in the server | — |
| Automatic approval reviews (a separate reviewer judges eligible requests) | `item/autoApprovalReview/*` | **Same**, rendered as an item; configured in `config.toml` | 2 |
| Guardian-denied action override | `thread/approveGuardianDeniedAction` | **Same** if a guardian is configured | 3 |
| Subagent threads: a panel above the composer with each subagent's status, stop all, open a subagent's thread | `thread/started` with a parent id (agent-graph-store), `thread/status/changed`, `turn/interrupt` | **Built** (2026-10-17): above the composer, the shown thread's subagents (threads with it as parent) by nickname and role, working or not (`thread/status/changed`, their turns), Open, Stop (`turn/interrupt`), Stop all, `/agents`; the header counts every working thread, subagents included, since they share the one model server | 2 |
| Memories per chat (`/memories`: use existing, contribute to future) | `thread/memoryMode/set`, `memory/status|reset` | **Built** (2026-10-17 and 2026-10-18): Settings › Memories turns them on; `/memories [on|off]` per thread (`thread/memoryMode/set`), the status and **Delete the memories** (`memory/status|reset`), built on `desktop/p2-13b-memories`, merged 2026-10-18 (1d9eb48) | 2 |
| Activity view (Ctrl+Alt+U): what the agent did, across threads | window over items | **Built** (2026-10-18, Phase 3 item 5, 9b227b1) | 3 |

### 2.5 Review and git

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Review pane (Ctrl+Shift+G): the repository's state, not only the agent's edits; scopes **Unstaged** (default), **Staged**, **Commit**, **Branch** (against the base), **Last turn** | window `git`; `turn/diff/updated` for Last turn | **Built** (2026-10-16): a column beside the thread (Ctrl+Shift+G or the header's review button); Unstaged (untracked files included), Staged, Commit (HEAD or one of the last 30), Branch (against `origin/HEAD`, else main or master) from `git` in the main process (`git.ts`), Last turn from the server; in a browser on `ling web` only Last turn | 2 |
| Repository selector when a project has several repositories; **All repos** for Last turn | window `git` | **Built** (2026-10-16): the repository the thread's folder is in, or a selector over the repositories up to two levels below a folder that is not in one; Last turn is the server's diff of every repository the turn touched | 2 |
| Stage, unstage, revert: whole diff, per file, per hunk; split or unified | window `git` | **Built** (2026-10-16): Stage all / Revert all / Unstage all, per file, and per hunk (`git apply --cached`, `--reverse`, `--index --reverse` of a one-hunk patch); reverting asks first; unified or split, remembered in the window's preferences | 2 |
| Inline comments on diff lines (hover, **+**), sent to the composer as review guidance | window | **Built** (2026-10-16): **+** on a hovered line, comments listed under their lines, **Send to the composer** writes them as review guidance with the path, line and code | 2 |
| Mark a file as viewed | window state | **Built** (2026-10-16): a viewed file folds until its changes change | 2 |
| Click a file name: open in the chosen editor; Ctrl+click a line: open at the line; click the name's background: collapse | window; editor detection | **Built** (2026-10-16): editors found from `.desktop` entries, JetBrains Toolbox scripts and PATH (`editors.ts`: VS Code, VSCodium, Cursor, Zed, Sublime Text, the JetBrains IDEs, Kate, gedit, Text Editor), each opened at the line with its own arguments, or the system's choice; the choice is a window preference | 2 |
| `/review`: against a base branch, uncommitted changes, a commit, custom instructions; findings as inline comments; **Inline** in the chat or **Detached** (Settings › Git › Review delivery); `review_model` | `review/start`; `config/value/write` | **Built** (2026-10-16 on `desktop/p2-6b-review`; merged 2026-10-18, 1d9eb48, with `review/start` in `policy.json`): `/review` with no words (uncommitted changes), `base <branch>`, `commit <sha>` or instructions of its own; Inline or Detached (Settings › Git; a detached review opens its thread); the review runs on the served model; its findings show as the review's Markdown, not yet as inline comments on the diff | 2 |
| Commit (with a generated message), push, create pull request (the forge's new-PR page prefilled) | window `git`; Settings › Git: branch naming, force push, commit and PR prompts | **Built** (2026-10-16): commit what is staged or everything; **Write a message** asks the served model in an ephemeral read-only thread that never asks for approval (`thread/start` `ephemeral`); push (`--force-with-lease` when ticked, the upstream set the first time) and the forge's new pull request page (GitHub, GitLab, else the repository's page) in the system browser, both refused at `/airgapped on`; the Settings › Git prompts come with item 10 | 2 |
| Code Review plugin for pull requests: inbox (assigned to me / my team / authored by me), summary, changes, checks, **Fix** from a failing check, **Review with Codex**, review instructions, submit review (comment / approve / request changes), stacks | vendor plugin over GitHub/GitLab APIs | **Mightling's, Built** (2026-10-18, Phase 3 item 9, d4d199a): a local `gh`-backed pull-request tab, optional, off by default; the air gap refuses it at `on` | 3 |
| Automatic cloud reviews of pull requests | vendor cloud | **No** | — |
| Revert the thread's changes | `thread/revert` | **Built**: the per-turn **Revert**, and **Revert the turn** in the review pane's Last turn scope; it asks first, undoes the turn's diff, then removes the turns (1.7.0), and says that what shell commands changed is not undone when the turns ran any (`revertQuestion`, 1.7.1) | 2 |

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
| A terminal per chat, scoped to its project or worktree; **New tab › Terminal** or Ctrl+`; clear Ctrl+L | `command/exec` with `tty`, `write`, `resize`, `terminate` (or `process/*`); xterm.js in the window | **Built** (2026-10-16 on `desktop/p2-8-terminal`; merged 2026-10-18, 1d9eb48, with `command/exec*` in `policy.json`): tabs in the bottom panel (Ctrl+`, **+**), per thread, in the folder the thread works in; xterm.js 6 with the fit add-on, bundled; `command/exec` with `tty`, a client-chosen `processId`, no timeout or output cap and the composer's permission profile, so `/airgapped on` holds for what is typed; keystrokes `command/exec/write`, size `command/exec/resize`, Close `command/exec/terminate`; Ctrl+L clears | 2 |
| The agent can read the terminal's output (a running dev server, a failed build) | the server ran it: `thread/backgroundTerminals/*`, `thread/shellCommand` | **Mightling's** (2026-10-16, same branch): **Send to the thread** puts the tab's recent output in the composer; `thread/shellCommand` is not allowed, since it runs unsandboxed with full access and would pass `/airgapped on` | 2 |
| Default terminal choice (Settings; on Windows PowerShell / cmd / Git Bash / WSL) | window prefs | **Built** (2026-10-16, same branch): Settings › Project and terminal: the shell, empty for the login shell `$SHELL` | 2 |
| Where terminal tabs open by default; how much command output appears in chats (Settings › Project and terminal behaviour) | window prefs | **Partial** (2026-10-16, same branch): how much command output the thread shows (open while running, folded, hidden) is a setting; tabs open in the bottom panel until item 9's layout gives them another place | 2 |

### 2.8 Tabs, files and previews

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| File tree (Ctrl+Shift+E); search files (Ctrl+P); go to line (Ctrl+L) | `fs/readDirectory|readFile|watch`, `fuzzyFileSearch/*` | **Built** (2026-10-18: the tree, Phase 3 item 1, 93426fd; search files and go to line, item 2, e6eccb3) | 3 |
| File tabs: open a file from the tree, the review pane or a message; syntax highlighting | `fs/readFile`, window | **Built** (2026-10-18, Phase 3 item 1, 93426fd), read only | 3 |
| Artifacts viewer: previews of documents, presentations, spreadsheets and PDFs beside the chat; auto-open after a task; HTML preview with rendered / source toggle; standalone `.tex` edited beside a PDF compiled by the bundled compiler (`tectonic`) | window viewers; the vendor bundles `tectonic` | **Built** (2026-10-18, Phase 3 item 3, 5a217b2): Markdown, images, PDF and HTML; no LaTeX (`tectonic`, Apache-2.0/MIT, not bundled, §7 question 4); office formats through a local converter only if one is already installed | 3 |
| Annotations: select a region of a preview or page and ask for a focused change; annotation mode for websites and canvases (the vendor's extensibility API) | window | **Later** for previews Mightling renders; the website extensibility API is the vendor's | 3 |

### 2.9 The browser

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Built-in browser tab (Ctrl+T) with its own profile, history, downloads folder, address-bar search; split / full view | the vendor's Browser plugin on its Chromium | **Partial; the preview tab Built** (2026-10-18, Phase 3 item 4, a435355, with its `--app` egress-audit case): a **preview tab** (Electron `WebContentsView`) for local dev servers and file-backed pages, with the app's egress rules (loopback and the LAN only unless the user allows a site); no vendor plugin | 3 |
| Comment on the page (browse / comment mode, Ctrl+Shift+.) → comments to the composer | window | **Built** for the preview tab (2026-10-18, a435355) | 3 |
| Computer Use in the browser; the agent clicks, types, screenshots and verifies | vendor's computer-use runtime and model | **No** for now: Qwen3.8 is text-only here; a `browser_*` tool over the preview tab's DOM is a later idea once a vision-capable model is served | — |
| Agent permissions per site (browse / download / upload: require approval, always allow, block); developer mode (full CDP) | window | Per-site permissions **Built** for the preview tab (2026-10-18, a435355); developer mode **Same**, as the `/airgapped` and egress rules already frame it | 3 |
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
| Skills: list, enable / disable, create, `$` picker, where local skills load from | `skills/list`, `skills/config/write`, `skills/extraRoots/set`, `plugin/skill/read` | **Partial** (2026-10-17): Skills and tools (the sidebar, `/skills`, `ling://skills`) lists `skills/list` with where each comes from (yours, the project's, another agent's linked in, built in) and whether it is on; `$` picks one (item 4). Turning one off (`skills/config/write`) merged 2026-10-18 (1d9eb48); extra roots wait for `policy.json` | 2 |
| Plugins directory: tabs vendor / workspace / personal, **Installed** row, install (+), connect on first use, pin a plugin to the sidebar; `/plugins` | `plugin/list|search|install|uninstall|read|installed`, `marketplace/*` | **Built** (2026-10-17 on `desktop/p2-11b-plugins`; merged 2026-10-18, 1d9eb48): Skills and tools › Plugins lists `plugin/list` by marketplace and installs (`plugin/install`, local and repository marketplaces only) or removes (`plugin/uninstall`); the vendor's catalogue stays closed (patch `0015`); pinning a plugin to the sidebar, search and the plugin page wait | 2 |
| MCP: recommended servers, add your own, OAuth flow, status, reload, tool call, resource read; Settings › Integrations | `mcpServerStatus/list`, `config/mcpServer/reload`, `mcpServer/*` | **Partial** (2026-10-17): Skills and tools › MCP servers lists `mcpServerStatus/list` (tools, state, origin), adds a server (a command with arguments, or a URL) and turns one off or on through `config/value/write` (`mcp_servers.<name>`), for new threads; reload (`config/mcpServer/reload`) merged 2026-10-18 (1d9eb48); OAuth waits for `policy.json` | 2 |
| Apps (Gmail, Drive…) | `app/list|read|installed` | **Built** (2026-10-17), **Mightling's:** Skills and tools › Apps lists `app/list` (signed in or not) and turns an app off or on (`apps.<id>.enabled` through `config/value/write`); `ling apps` sets one up | 2 |
| Hooks (plugin hooks at lifecycle points) | `hooks/list`, `hook/started|completed` | **Built** (2026-10-17 on `desktop/p2-11b-plugins`; merged 2026-10-18, 1d9eb48): Skills and tools › Hooks lists `hooks/list`, read only | 2 |
| Import another agent's configuration and histories | `externalAgentConfig/*` | **Built** (2026-10-18, Phase 3 item 8, f48e550: `externalAgentConfig/detect|import|import/readHistories`), beside the SKILLS links | 3 |
| Sign in with the vendor's account at partner plugins | vendor | **No** | — |

### 2.12 Scheduled tasks (automations)

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| **Scheduled** in the sidebar: an inbox of runs with findings and an unread indicator; active / paused / completed tasks | the app's scheduler | **Built** (2026-10-17), **Mightling's: Night Shift.** The sidebar's Scheduled (with the count of tasks finished since last looked at), `/night`, `ling://night`: the inbox of finished tasks, the queue, the latest morning report, read from `~/.mightling/night/` (`night.ts`) | 2 |
| Standalone task (a new chat per run) or a task inside an existing chat (returns to the same context) | app | **Partial** (2026-10-17): a task queued for a project from Scheduled or `/night add <task>` in a thread (through `ling night add`); a task returning to an existing thread is not offered | 2 |
| Custom cadence, RFC 5545 RRULE | app | **Same**, not yet: Night Shift runs on its nightly timer (`ling-admin night enable`); a cadence per task is not built | 2 |
| Run in the local project or on a dedicated background worktree; the same task on several projects; model and effort per task; skills in the prompt; **Run now** | app | **Partial** (2026-10-17): every task runs in its own worktree on `night/<id>` (Night Shift's rule); **Run now** starts the runner (`ling-admin night run`), which gives way to a busy machine; local mode, a task on several projects and model and effort per task are not built | 2 |
| Worktree cleanup for scheduled tasks | app | **Same** (§2.6) | 2 |
| Event triggers (Gmail, Slack, GitHub), Team Tasks | vendor cloud | **No** | — |

### 2.13 Subagents and custom agents

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Spawn subagents on request or when `AGENTS.md` / a skill asks; agent threads visible; main thread collects results; `/agents` to inspect and switch | `ext/agent`, `agent-graph-store`; threads with a parent | **Built** (2026-10-17): the server spawns them (`ext/agent`); the app shows them above the composer and in the stream (`collabAgentToolCall`, `subAgentActivity` items), `/agents` shows the panel | 2 |
| Custom agents with their own model and reasoning (`[agents]` in `config.toml`, custom agent files) | `config/*` | **Same**, not yet: custom agents are written in `config.toml` by hand for now; one served model, so they would differ by instructions and effort | 2 |
| Agent message board (shared discussions between agents) | `ext/agent-message-board` | **Built** (2026-10-18, Phase 3 item 10, a94401f), read only, local backend only | 3 |

### 2.14 Memories

| Codex app | Mechanism | Mightling | Phase |
|---|---|---|---|
| Settings › Personalization: **Enable Codex memories**, **Allow memories from tool-assisted chats**, **Delete memories**; `/memories` per chat; files under `~/.codex/memories/`; `memories.*` keys | `thread/memoryMode/set`, `memory/status|reset`, `config/value/write` | **Built** (2026-10-17 and 2026-10-18): Settings › Memories writes `features.memories`, `memories.use_memories`, `memories.generate_memories` and `memories.disable_on_external_context` through `config/value/write` (under `~/.mightling/memories/`); Delete, the status and `/memories` per thread merged 2026-10-18 (1d9eb48) with `memory/*` and `thread/memoryMode/set` in `policy.json` | 2 |

### 2.15 Settings

The vendor's settings panel (Ctrl+,) has these pages. Mightling's reads and writes `~/.mightling/config.toml` through `config/read`, `config/value/write` and `config/batchWrite` for agent settings, and the window's own preferences file for window settings; the split is the vendor's too (agent settings are shared with the CLI, window settings are not).

| Page | Contents | Mightling |
|---|---|---|
| General | Enter behaviour; **Prevent sleep while running**; follow-up behaviour (steer / wait); Code review delivery | **Built** (items 4, 10): Enter, follow-ups, prevent sleep (the main process asks before blocking sleep), the text size; review delivery lives with `/review` (Git page; merged 2026-10-18, 1d9eb48) |
| Profile | Activity insights: lifetime and peak tokens, streaks, longest task; profile card | **Built** (2026-10-17), **Mightling's:** sessions, lifetime and busiest-day tokens, the current and longest streak, the longest session, read from the end of each local session file (`insights.ts`); no card, no sharing |
| Keyboard shortcuts | Review, rebind, reset; search by command name or by keystroke | **Built** (2026-10-16, item 2): the §2.19 table with each action's keys, Change (a binding that clashes in its context is refused), Reset, Reset all, search by name or by keystroke; kept in the window's preferences |
| Notifications | When turn-completion notifications appear; permission prompt | **Built** (2026-10-17): when a finished turn notifies (window not in front, always, never) and whether a waiting approval or question does; silent |
| Appearance | Base theme; accent, background and foreground colours; UI and code fonts; share a theme | **Built** (2026-10-17): accent, background and text colours and the two font lists (only `#rgb`/`#rrggbb` and plain font names reach the stylesheet), a theme saved to and read from a JSON file. **Base theme built 2026-10-20** (item 11): System, Light, Dark; the colours are the theme shown's, each theme keeping its own |
| Pets | Built-in or custom pet, Mini, size | **Built** (2026-10-18, 0096587) with Mightling's own art, off by default |
| Browser | Install / enable, extension, permissions per site, history, downloads | **Same** for the preview tab (Phase 3) |
| Computer Use | Access review, always-allowed apps | **No** |
| Personalization | Edit personal `AGENTS.md` for this machine; repository instructions | **Built** (2026-10-17): `~/.mightling/AGENTS.md` edited in place; a project's own `AGENTS.md` stays the project's |
| Suggested prompts | Context-aware follow-ups on start or return | **Built** (2026-10-18, Phase 3 item 7, fa07a02), generated by the served model between turns, off by default |
| Memories | Enable, tool-assisted chats, delete | **Built** (item 13): the switches (2026-10-17, b10e1f0); Delete with `memory/reset` merged 2026-10-18 (1d9eb48) |
| Archived chats | List with dates and project; **Unarchive** | **Built** (2026-10-17): `thread/list` with `archived`, `thread/unarchive` |
| Git | Branch naming; force push; commit-message and PR-description prompts; review delivery | **Built** (2026-10-17) apart from two: the prefix Create branch here starts with, Force ticked by default, the commit-message instruction; the PR description is the forge page's own (no forge API), review delivery came with `/review` (2026-10-18, 1d9eb48) |
| Project and terminal behaviour | Where files open; command output shown in chats; where terminal tabs open | **Partial** (2026-10-17): where files open (the editor) on main; command output in threads and the terminal's shell merged with item 8 (2026-10-18, 1d9eb48) |
| Worktrees | Worktree root; cleanup limit; automatic deletion on/off | **Built** (item 7) |
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
| macOS, Windows (native PowerShell agent with the Windows sandbox, or WSL2), Linux preview (`.deb` x64/arm64, `.rpm`, Arch script; signed repositories; `--ozone-platform=wayland` flag) | Linux arm64 `.deb` for the node and clients (`ling-admin desktop install`, release assets); x86_64 Linux client build (CI); the macOS app as an unsigned preview `.dmg`, arm64 and x64, minimum macOS 12.0 (DESKTOP_ELECTRON §10, `mac-preview.yml`); the Windows app as an unsigned zip built in CI (`windows.yml`, Phase 3 item 12, 2026-10-18, 54a4578; no installer); Wayland through Electron's defaults |
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
| | Switch view: Ask / Work | Alt+1 / Alt+2 (the vendor's Chat / Work / Codex; Mightling's were Ctrl+1 / Ctrl+2 until item 2, 2026-10-16; `keymap.json` `viewAsk`/`viewWork`) |
| | Activity view | Ctrl+Alt+U |
| Workspace | Environment action 1 | Super+Shift+D |
| | Search files; file tree; review tab | Ctrl+P; Ctrl+Shift+E; Ctrl+Shift+G |
| | Chat ↔ tabs; cycle layout; full view | Ctrl+Alt+B; Ctrl+Shift+B; Ctrl+Shift+F |
| | Go to line / address bar | Ctrl+L |
| | Preview tab: open; back; forward; reload; copy URL; comment mode | Ctrl+T; Alt+←; Alt+→; Ctrl+R; Ctrl+Shift+C; Ctrl+Shift+. |
| | Copy conversation path; deep link; working directory | Ctrl+Alt+Shift+C; Ctrl+Alt+L; Ctrl+Shift+C |

A test holds this table as data and checks the window's keymap against it (§5). Since item 2 the page handles every binding itself from `keymap.json` (the Electron menu shows them with `registerAccelerator: false`), so a rebinding in Settings › Keyboard shortcuts applies everywhere.

### 2.20 Deep links and remote connections

The vendor's app registers a `codex://` scheme (its commands reference): `codex://threads/<id>`, `codex://threads/new`, `codex://new?prompt=…&path=…&originUrl=…` (a new chat with composer text, in a folder, or in the workspace whose git remote matches), `codex://settings`, `codex://settings/connections/{computer,devices,ssh}`, `codex://settings/connections/ssh/add?name=<ssh-config-host>`, `codex://skills`, `codex://automations` (Scheduled with the create flow open), `codex://plugins/install/<name>?marketplace=…`, `codex://plugins/<id>?hostId=local|<remote>`, `codex://pets/install?…`.

| Codex app | Mightling | Phase |
|---|---|---|
| `codex://threads/<id>`, `threads/new`, `new?prompt&path&originUrl` | **Built** (2026-10-16): the app registers `ling` (and `mightling`, for links made before) in its `.desktop` entry and the `.deb`; `ling://threads/<id>`, `ling://threads/new`, `ling://new?prompt&path`; `ling app --thread <id>` stays the shell form; a link reaching a running app is shown in its window | 2 |
| `codex://settings`, `codex://skills`, `codex://automations`, `codex://plugins/…` | **Partial** (2026-10-16): `ling://settings` and `ling://skills` open; `ling://night` and `ling://plugins/…` open Scheduled and the plugins page since items 12 and 11 merged (2026-10-17; `args.ts`) | 2 |
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

> **Dated (2026-10-19):** the "Today" column is 2026-10-14's; the "Specified" column is what item 1 merged on 2026-10-16 (`--font-ui` and `--font-code` in `desktop/ui/src/styles.css` read as specified).

| Role | Today, 2026-10-14 (`desktop/ui/src/styles.css`) | Specified |
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
--accent-text, --on-danger, --syn-* (file tabs' highlighting; dark mode, 2026-10-20),
--mark-* (the empty state's mark), --radius-pill, --ease, --column 760px, --sidebar 260px
```

Two themes (item 11, built 2026-10-20). Light is `:root` (`color-scheme: light`). Dark is one block, `:root[data-theme="dark"]`, redefining every colour token except `--accent` (`color-scheme: dark`), in the parity target's dark manner: neutral greys, the sidebar (`--panel` `#181818`) darker than the thread (`--canvas` `#212121`), raised cards and menus lighter (`--raised` `#2f2f2f`), near-white ink (`#ececec`), the user's bubble a dark tint of the accent, diffs on dark green and red with light green and red signs, GitHub-dark-like syntax colours; Tiffany Blue stays the accent. The same body is repeated under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`, so a browser shows the system's theme before any script runs (`styles.test.ts` holds the two bodies equal). The choice is the window preference `theme` (System, Light, Dark): in the desktop app it is `nativeTheme.themeSource`, each window opens with the theme's canvas as its background, its overlay's symbols in the theme's colour and the theme in an argument the preload sets as `html[data-theme]` before the first paint, and a change reaches every window as a `theme` message; on `ling web` `bridge.js` sets it before the first paint from `localStorage` or the system's. Text meets WCAG AA in both themes (`contrast.test.ts` over every text-on-surface pair), which added `--accent-text` (the accent darkened for text, `#00756f` in light) and `--on-danger`, made `--on-accent` dark ink (white on `#0abab5` is 2.4:1), and darkened the light theme's `--muted`, `--faint` and `--syn-comment`.

### 3.7 What stays Mightling's

The name and wordmark; TIFFANY_BLUE as the accent and the air-gap chip's colour; the empty-state copy; the ✅/⚠️ voice of CLI output stays in the CLI, not in the app; the model, node and index chips of DESKTOP §7.

---

## 4. Gaps and phases

### 4.1 Built today (1.6.1)

> **Dated (2026-10-19):** this is the 2026-10-14 baseline; `policy.json` has grown to 67 requests, and §4.2 and §4.3 are what was built since.

One window with Ask and Work; one `ling app-server` on stdio through the vetted IPC channel. **Allowed** (43 of the 170 client requests: initialize, account/read, config read/write, model/list, permission profiles, collaboration modes, project list/read/create/update, thread start/resume/fork/list/read/loaded/turns/items/search/name/metadata/archive/unarchive/unsubscribe/compact/revert, queue add/list/update/delete/reorder/start, turn start/steer/interrupt, fuzzyFileSearch, gitDiffToRemote, skills/list, mcpServerStatus/list, app/list). **Built** (a UI exists): threads grouped by folder with rename, search, archive, running markers and a per-turn revert link; streaming items (messages, reasoning, commands, file changes, plan, the turn's diff); inline approvals; the permissions picker with the air-gap courtesy; Steer and Stop; a compact link; attachments in Ask; the served-model, usage and air-gap chips; the devices link for `ling web`; the start-up screen; narrow-window drawer; `ling web` serving the same UI to browsers with the same policy; the egress audit's `--app` mode; Night Shift's busy marker. **Allowed without a UI:** project create/update, fork, the queue, `fuzzyFileSearch`, `collaborationMode/list`, `skills/list`, `mcpServerStatus/list`, `app/list`.

### 4.2 Phase 2 — the Codex surface (this spec's main body)

In the order the owner's use suggests, each item with its §2 row as acceptance:

1. **Look** (§3): tokens, fonts, radii, spacing, the composer card, the bubble, no turn separators, icons. One change, no behaviour. **Look: merged 2026-10-16** (light only then, dark added by Phase 3 item 11 on 2026-10-20; the app icon unchanged).
2. **Shell:** sidebar toggle, command menu, back/forward, font size, undo/redo, close/reopen tabs, the keymap table and its settings page (§2.1, §2.19). **Shell: merged 2026-10-16, 4ca1962** (close/reopen act on the review pane and the bottom panel until item 9's tabs; the settings dialog now has pages).
3. **Threads:** pins, sections, Recents, unread and attention, mark unread, find in chat, deep links and `ling://`, pop-out with always-on-top, side chat, standalone chat (§2.1). **Threads: merged 2026-10-16, 44c9b8b** (pins and sections window-side; find in page; `ling://night` and `ling://plugins` wait for items 12 and 11).
4. **Composer:** `@` mentions, `$` skills, `/` commands, Enter behaviour, follow-up behaviour, ↑ restore, Shift+drag images, approve/decline keys, edit previous, fork, Compress, the model and effort picker (§2.3). **Composer: merged 2026-10-16, eca4a6a** (the slash commands Partial: the ones whose methods come with later items wait for them).
5. **Plan and goal:** collaboration mode, the goal row (§2.3). **Plan mode: merged 2026-10-17, 116f690**; the goal row merged 2026-10-18 with Phase 2's held work, 1d9eb48.
6. **Review pane and git:** scopes, repository selector, stage/unstage/revert at three levels, inline comments, viewed markers, open in editor, `/review` inline or detached, commit, push, PR page (§2.5). **Review pane and git: merged 2026-10-16, 7973c5a** (`/review` merged 2026-10-18 with Phase 2's held work, 1d9eb48).
7. **Worktrees and hand-off**, local environments and actions, the bottom panel (§2.6). **Worktrees, hand-off, environments and the bottom panel: merged 2026-10-16, 9c5f31d** (window-side `git` on the `codex-worktree` contract until `ling worktree` exists; actions run as bottom-panel tasks until the terminal of item 8 lands).
8. **Terminal** tabs (§2.7). **Terminal tabs: merged 2026-10-18, 1d9eb48** (with Phase 2's held work, after the Rust conformance run: `cargo test --release -p ling-launcher -p ling-web-server` 261 + 34 + 16 passed).

**Held for the Rust conformance run (2026-10-17).** The methods Phase 2 adds to `ling-rs/web/policy.json` (`review/start`, `command/exec*`, `thread/goal/get|set|clear`, `skills/config/write`, `plugin/list|install|uninstall`, `config/mcpServer/reload`, `hooks/list`, `thread/memoryMode/set`, `memory/status|reset`, one conformance case each, and one keeping `thread/shellCommand` refused) are on `desktop/p2-policy`; the work that uses them is on `desktop/p2-6b-review` (`/review`), `desktop/p2-8-terminal` (item 8), `desktop/p2-5b-goal` (the goal row), `desktop/p2-11b-plugins` (plugins, hooks, skill switches, MCP reload) and `desktop/p2-13b-memories` (`/memories`, the memory status, Delete), all integrated on current main as `desktop/p2-held` with every check green. To land it, with no `ling-swe-*` unit active: `cargo test --release -p ling-launcher -p ling-web-server` in the build's export under `~/.cache/dreamference/puffin-codex/` with `desktop/p2-held`'s `ling-rs/`, then merge `desktop/p2-held` into main. **Landed 2026-10-18 as 1d9eb48**, after the conformance run passed in the export with its `ling-rs/` and `ling-admin audit egress --app` passed with every Phase 2 feature in the app.
9. **Tabs and layout:** split, full, hidden (§2.1). **Tabs and layout: merged 2026-10-17, 1064dc9** (the tab area holds the Review tab; terminal tabs stay in the bottom panel; file and preview tabs are Phase 3).
10. **Settings pages** of §2.15 except Pets, Browser, Computer Use, Suggested prompts. **Settings pages: merged 2026-10-17, b639f9b** (Integrations and MCP with item 11, Memories with item 13, the terminal's and `/review`'s settings with their held branches).
11. **Skills, plugins, MCP, apps, hooks** pages (§2.11). **Skills and tools: merged 2026-10-17, f0477c6** (skills, MCP servers and apps; plugins, hooks, skill switches and MCP reload merged 2026-10-18, 1d9eb48).
12. **Scheduled** view over Night Shift (§2.12). **Scheduled: merged 2026-10-17, 84d52b3** (inbox, queue, report, add, drop, Run now; cadence, local mode and per-task model are not built).
13. **Subagents** panel; **memories** controls (§2.13, §2.14). **Subagents and memory switches: merged 2026-10-17, b10e1f0** (per-thread memory mode, status and Delete merged 2026-10-18, 1d9eb48).
14. **Notifications**, prevent sleep (§2.1). **Notifications and sleep: merged 2026-10-17, a46aed7**.

### 4.3 Phase 3 — the workstation extras

In this order (the owner, 2026-10-18), each item with its §2 row as acceptance:

1. **File tree and file tabs** (§2.8): `fs/readDirectory|readFile|watch|unwatch`, read only. **File tree and file tabs: merged 2026-10-18, 93426fd.**
2. **Search files and go to line** (§2.8, §2.19: Ctrl+P, Ctrl+L). **Search files and go to line: merged 2026-10-18, e6eccb3.**
3. **Artifacts viewer** (§2.8): Markdown, images, PDF, HTML; LaTeX only if `tectonic` is bundled (§7 question 4). **Artifacts viewer (Markdown, images, PDF, HTML; no LaTeX: tectonic not bundled): merged 2026-10-18, 5a217b2.**
4. **Preview tab** with comments and per-site permissions (§2.9). **Preview tab, with its --app egress-audit case: merged 2026-10-18, a435355.**
5. **Activity view** (§2.4, Ctrl+Alt+U). **Activity view: merged 2026-10-18, 9b227b1.**
6. **Pets** with Mightling's art (§2.1, §2.15, §7 question 5). **Pets (Mightling's ling, off by default): merged 2026-10-18, 0096587.**
7. **Suggested prompts** (§2.15). **Suggested prompts (off by default): merged 2026-10-18, fa07a02.**
8. **External agent import** (§2.11): `externalAgentConfig/detect|import|import/readHistories`. **External agent import: merged 2026-10-18, f48e550.**
9. **The `gh`-backed pull-request tab** (§2.5). **Pull-request tab through gh (off by default): merged 2026-10-18, d4d199a.**
10. **Agent message board** (§2.13). **Agent message board (read only): merged 2026-10-18, a94401f.**
11. **Dark mode** and the Appearance page's light/dark/system choice: approved by the owner 2026-10-20 (not approved on 2026-10-16 and 2026-10-18), **built 2026-10-20** (§3.6): the dark token set, System following the OS through `prefers-color-scheme` and Electron's `nativeTheme`, the choice in Settings › Appearance kept per user in the window's preferences (`prefs.json` in the app's data folder, `localStorage` on `ling web`), no flash of the other theme at start (the window's background, the preload, `bridge.js`, the stylesheet's media query), every surface on tokens (the terminal's colours follow a change; HTML artifacts and PDF pages stay paper-white), AA contrast checked by `contrast.test.ts`, `theme.e2e.ts` with pictures of the key views in both themes. No new IPC message, so `policy.json` is unchanged.
12. **macOS and Windows app builds** (§2.17; RELEASE, WINDOWS_ARM). **Windows desktop app in CI (unsigned zip; macOS preview unchanged): merged 2026-10-18, 54a4578.**

The methods Phase 3 adds to `ling-rs/web/policy.json` (items 1 to 3 and 8, one conformance case each, plus two keeping `fs/writeFile` and `fs/remove` refused) were on `desktop/p3-policy`, on top of `desktop/p2-held`, so they shared its Rust conformance run. **In `policy.json` on main since 2026-10-18** (afe4749; `fs/getMetadata` taken out again by aced1d8, since nothing calls it): `fs/readDirectory|readFile|watch|unwatch` and `externalAgentConfig/detect|import|import/readHistories`.

### 4.4 Not proposed

§2.21; and the cloud and account rows of §9.3.

### 4.5 Phase 4 — the gaps of the 2026-10-20 study

The rows of §9.3, as items numbered on from Phase 3, in order of user impact. Each item names its acceptance; every item also passes the desktop gate (`npm test` in `desktop/ui` and `desktop/electron`, `npm run e2e` after `npm run package`, the conformance cases if `policy.json` changes, `.venv/bin/python -m pytest tests/ -q`). Items marked **decision** wait for §9.5.

13. **Projects.** (a) A project's menu in the sidebar with **Archive chats** (each of its threads archived, one undo restores them all) and **Remove from the sidebar** (a window preference; the project's threads stay, Ctrl+O or a new thread in the folder brings it back). (b) **Decided 2026-10-21 (§9.5.1): opt-in** server objects, with standing instructions and knowledge files that every thread of the project gets, in Ask and in Work: `project/list` at start, **New project** and Ctrl+O create one (`project/create`, the folder as its first root), **Edit project** adds and removes folders and **Make primary** reorders `roots` (`project/update`), a new thread in a project starts in the primary folder with `projectId` and the other roots as `runtimeWorkspaceRoots`, a thread row's **Move to project** and a multi-selection move several (`thread/metadata/update.projectId`); threads with no `projectId` keep today's folder grouping. Acceptance: an e2e against the stand-in creates a project with two folders, makes the second primary, starts a thread there (its `thread/start` carries `projectId`, `cwd` = the primary, the other root in `runtimeWorkspaceRoots`), moves an older thread in, archives the project's chats and undoes it; at `/airgapped on` the server refuses a root that holds the seal (patch 0023), and the app shows that refusal. **Merged 2026-10-21, 868d665d** (branch `desktop/parity-projects`; the sidebar's order made stable with it).
14. **MCP elicitations.** `mcpServer/elicitation/request` shows in the thread it belongs to (or as a dialog when it names none): a form request as its fields (string, number, boolean, enum, required marks, from `requestedSchema`) with Accept, Decline and Cancel; a URL request with the URL shown in full and **Open in the browser** (refused at `/airgapped on`, as push is) beside Accept and Decline; the answer is the request's response (`accept` with `content`, `decline`, `cancel`). Nothing is answered unseen any more. Acceptance: unit tests for the schema-to-fields mapping and the response; an e2e where the stand-in sends a form elicitation and receives the typed answer, and one where Decline is sent back. **MCP elicitations: merged 2026-10-20, ec3900e** (branch `desktop/parity-elicitation`; the vendor's own `openai/userVerification` mode is still declined, nothing here can answer it).
15. **Hook trust.** The Hooks tab shows each hook's trust state (`trustStatus`: trusted, untrusted, modified; managed hooks marked as such), its source file and its full command, and **Trust** (after a confirmation naming the command) writes `hooks.state.<key>.trusted_hash` = `currentHash` through `config/batchWrite` with `reloadUserConfig`, as the TUI does (`tui/src/hooks_rpc.rs`); **Turn off** writes the same entry's `enabled = false`. A banner on the tab and a count on Skills and tools say how many hooks wait for review. Acceptance: unit test of the batch edit; an e2e where the stand-in lists an untrusted hook, Trust sends the expected `config/batchWrite`, and the list read again shows it trusted. **Hook trust: merged 2026-10-20, 2b61a54** (branch `desktop/parity-hook-trust`).
16. **Inline diff editing.** **Decided 2026-10-21 (§9.5.2).** Acceptance: in the Unstaged scope, Edit on a hunk turns its new lines into a text area; Save applies the edit as a one-hunk patch through `git:apply` and reads the diff again; a patch git refuses (the file changed meanwhile) leaves the file as it was and says so. **Merged 2026-10-21** (branch `desktop/parity-diff-edit`).
17. **Activity: what needs you.** **Decided 2026-10-21 (§9.5.3): the list is the main tab, the timeline the second.** Acceptance: a list of threads that are unread, running, or waiting on an approval or a question, filtered by Pinned and Scheduled (Night Shift's unseen runs), each opening its thread; **Mark all as read** clears the unread marks and Night Shift's unseen count; the timeline stays reachable. **Merged 2026-10-21** (branch `desktop/parity-activity`).
18. **Scheduled runs that can be opened.** Each finished task in the inbox shows its result (`result`, the run's last message) and, when its session is known, **Open** resumes that thread in Work (`thread/resume`); its branch is shown with Open in the review pane. The Queue form takes a node (`--on`) and best-of (`--best-of`), and the view says whether Night Shift is enabled and when its window is. Acceptance: `night.test.ts` maps `result` and `session`; an e2e opens a finished run's thread. **Merged 2026-10-21** with decision 7 (branch `desktop/parity-night`); the node and best-of options in the Queue form are still open.
19. **The vendor's other bindings, and three misfires.** Ctrl+Y, Ctrl+F4, Ctrl+Shift+O, Ctrl+Shift+[ / ], Ctrl+K in a focused terminal, Ctrl+Shift+R (preview, without the cache), Ctrl++ (and the keypad's +); a shifted key no longer matches a binding without Shift (except Ctrl+=, typed with Shift on many layouts). Acceptance: `keymap.test.ts` holds the new bindings and checks that Ctrl+Shift+[ is the previous thread and not back. **Merged 2026-10-20, e4a6f4d** (branch `desktop/parity-keys`).
20. **Three bugs.** The review pane keeps each thread's comments, draft, viewed and folded files and commit message to that thread and folder; the worktree cleanup keeps a pinned thread's worktree; a notification is shown only when no window of the app (a pop-out included) has the focus. Acceptance: `worktrees.test.ts` (`protectedOwners`), the across-threads review e2e (a comment written on one thread is gone on the next and back on return). **Merged 2026-10-20, ac08ed2** (branch `desktop/parity-fixes`).
21. **The plan checklist.** `turn/plan/updated` shows above the composer and in the turn as the steps with their status (pending, in progress, completed) and the explanation; it updates in place and stays with the finished turn. Acceptance: a reducer test; the stand-in's turn sends two plan updates and the e2e sees the second.
22. **Composer commands, fork from a message, queue edits.** `/side`, `/pin`, `/pet`, `/project`, `/chat` (standalone), `/worktree` (hand off to a worktree), `/local` (hand off to the local checkout), `/reasoning` (the effort picker), `/plugins`, `/hooks`, and the enabled skills in the `/` list (inserting `$skill`); **Fork from here** on any turn (`thread/fork` with `lastTurnId`); a queued message can be edited (`thread/queue/update`) and moved up or down (`thread/queue/reorder`). Acceptance: `composer.test.ts` for the list; an e2e forks from the first of two turns and edits and reorders two queued messages.
23. **Questions answered in full.** `requestUserInput`: an option's description under it, a free-text field when `isOther`, a hidden field when `isSecret`. Acceptance: an e2e answers "other".
24. **Notifications that say which thread.** The title is the thread's name (or its first words), the body the turn's last message cut to one line, or what waits (the command, the question); Settings › Notifications has separate switches for approvals and for questions, both starting from today's one switch. Acceptance: `notices.test.ts`.
25. **Every thread reachable.** The sidebar reads `thread/list` page by page (its cursor) as it is scrolled or searched, so an older project does not vanish; a **Chronological** switch lists threads by date across projects; a `searchChats` action (no default key) focuses the search. Acceptance: a store test with 450 threads; `keymap.test.ts`.
26. **Tool calls in full.** MCP tool calls with their arguments, progress (`item/mcpToolCall/progress`) and result folded under them; dynamic tool calls, image views, hook prompts and the rest with a label and their payload instead of their type name; reasoning as Markdown.
27. **Review findings on their lines.** `/review`'s findings (`- title — /abs/path:start-end`) become comments on those lines in the review pane, foldable, each sendable to the composer.
28. **Two threads on one checkout.** Starting or steering a turn while another thread's turn runs in the same folder or worktree shows which, with Open and **Hand off to a worktree**; nothing is blocked.
29. **First run.** Shown once (a window preference): open a folder, start a standalone thread, import another agent's setup (the existing import panel), or ask a question in Ask.
30. **MCP servers and plugins.** The Add server form takes environment variables, a working folder, headers and a bearer-token variable; Remove; status updates from `mcpServer/startupStatus/updated`; Authenticate for OAuth servers (adds `mcpServer/oauth/login`). Plugins: details before install (adds `plugin/read`), search and an Installed row from `plugin/list`, on/off (`plugins.<id>.enabled`), marketplaces added and removed (adds `marketplace/add|remove`).
31. **Pastes and files in Work.** A pasted image and the paperclip attach in Work as in Ask; when the served model's `inputModalities` has no image, the chip says the model will not see it.
32. **Multi-repository review and pull requests.** The repository selector shows each repository's lines changed; Last turn offers All repos; the PR tab shows the pull request's comments and reviews (`gh pr view --json comments,reviews`) and creates one (`gh pr create`, refused at `/airgapped on`).
33. **Dictation.** A dictionary (names, paths, symbols) passed to the speech-to-text sidecar as its prompt; Ctrl+Shift+V starts and stops dictation.
34. **Pets and Night Shift status.** The pet keeps its place; its menu lists threads that need you; a Blocked state for a thread whose approval cannot be asked.
35. **Deep links.** `ling://threads/new?prompt&path`, `ling://new?originUrl` (the open workspace whose git remote matches), `ling://settings/<page>`, `ling://automations`, `ling://plugins/<id>` opening that plugin; a `path` that is not an absolute existing folder is ignored.
36. **Menu and tray.** File: New thread, Open folder, Settings; Window: the pop-outs; the tray shows how many threads run and wait.
37. **Subagent marks.** One colour and glyph per subagent, from its id, in the panel, the stream and the sidebar.
38. **Terminal tabs.** New tab › Terminal in the tab area; Settings says where terminal tabs open.
39. **Preview and files.** Comments on an area and several before sending; address-bar suggestions from the tab's own history (no web search); CSV as a table; **Open in the preview tab** on an HTML artifact.
40. **Settings.** Enter sends unless the prompt is multiline; a PR-description instruction in Settings › Git.

---

## 5. Tests and acceptance

- **Protocol inventory drift:** a test reads the method names of the four generated schema files (`ClientRequest.ts`, `ServerRequest.ts`, `ServerNotification.ts`, `ClientNotification.ts` of the experimental export `generate-ts --experimental` writes, `app-server-protocol/schema/precomputed/app-server-exports-experimental.json.zst`), compares them with the copy under `desktop/ui/src/protocol/` and with a checked-in list grouped as §1.4; a Codex bump that changes it fails until this spec and `policy.json` are reviewed. Built with item 4 (`tests/test_desktop_parity.py`; the submodule's export is compared when the checkout has it). The submodule's `schema/typescript/` is the stable surface (107 client requests, 10 server requests) and is not the reference: until 1.7.1 the test compared it, and failed in every checkout with the submodule.
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
2. ~~**Dark mode**~~ (DESKTOP §14 question 3): **approved 2026-10-20 and built** (item 11, §3.6). Before that it was not approved on 2026-10-16: it had been built on `desktop/codex-look` as the system theme's follower (screenshots in both themes, 2026-10-15), and the merge removed the dark block and the follower and kept the tokens.
3. ~~**`.codex/` or `.mightling/` for local environments?**~~ **Decided 2026-10-16 (the owner): read both `.codex/` and `.mightling/` in a project, write only `.mightling/`** (built with item 7: `.mightling/environments/default.toml`, `name`, `[setup] script` with `linux`/`macos`/`windows` overrides, `[[actions]]` `name`, `icon`, `script`; `.codex/environments/*.toml` and `environment.toml` are read in that shape, since the vendor's generated file is not documented). Reading `.codex/` keeps shared repositories working for Codex users; Mightling's settings pages never put a vendor-named folder in the user's project.
4. **`tectonic` in the bundle** for LaTeX previews (Phase 3): about 30 MB. Recommendation: later, behind a setting. **Not bundled** when the artifacts viewer merged (2026-10-18, 5a217b2); still open.
5. ~~**Pets:** worth Mightling's own art at all?~~ **Built 2026-10-18** (Phase 3 item 6, 0096587): Mightling's ling, off by default.

---

## 8. Sources

- The vendor's documentation, read 2026-10-14 (Markdown twins of `learn.chatgpt.com/docs/…`): `app`, `reference/commands`, `reference/settings`, `projects`, `code-review`, `environments/git-worktrees`, `environments/local-environment`, `integrated-terminal`, `developer-settings`, `long-running-work`, `automations`, `agent-configuration/subagents`, `customization/memories`, `pets`, `browser`, `computer-use`, `artifacts-viewer`, `annotations-extensibility`, `plugins`, `extend/mcp`, `agent-approvals-security`, `sandboxing`, `permissions`, `image-inputs`, `quickstart`, `linux/linux-app`, `windows/windows-app`, `app-server`, `customization/computer-history`.
- The videos the owner named: "The Codex app is now on Windows" (`youtube.com/watch?v=8hNcRChDrNk`), "A first look at the Codex app" (`0e-Brv-gS9Q`), "The Complete Guide to OpenAI's Codex App" (`yp_sIdLVtIA`); also found: "How To Navigate OpenAI Codex Desktop GUI 2026" (`CvRcHNBEfdo`), "Complete Beginner's Guide to OpenAI's Codex App" (`nQFtsehu7h0`), "OpenAI Codex in 7 Minutes" (`2OnmwXm6N4U`). Their frames and narration could not be read from here; their titles and published descriptions were cross-checked against the documentation above, which is the authority for every row of §2.
- The Codex source pinned in `codex/` (`rust-v0.158.0`): `codex-rs/Cargo.toml` (workspace members), `cli/src/main.rs` (subcommands), `app-server/src/` (processors, transports, in-process host, daemon recovery), `app-server-protocol/src/protocol/common.rs` (method names), `tui/src/slash_command.rs`, the crate-level docs of `core-api`, `thread-store`, `rollout`, `state`, `file-watcher`, `ext/queue`, `ext/goal`, `app-server-daemon`, `app-server-client`.
- For §9 (read 2026-10-20, the Markdown twins of `learn.chatgpt.com/docs/…`, nothing from the app itself): `whats-new` and its weekly pages, `changelog` (the desktop app's notes 26.928 to 26.1007), `app`, `linux/linux-app`, `windows/windows-app`, `reference/commands`, `reference/settings`, `reference/slash-commands`, `reference/troubleshooting`, `projects`, `notifications`, `use-chatgpt`, `quickstart`, `features`, `import`, `long-running-work`, `permission-modes`, `permissions`, `agent-approvals-security`, `sandboxing/auto-review`, `environments/modes`, `environments/git-worktrees`, `environments/local-environment`, `integrated-terminal`, `code-review`, `artifacts-viewer`, `browser`, `annotations-extensibility`, `image-inputs`, `visualizations`, `features/composer-predictions`, `features/voice`, `model-selection`, `agent-configuration/speed`, `agent-configuration/subagents`, `customization/memories`, `automations`, `plugins`, `skills-and-plugins`, `extend/mcp`, `hooks`, `pets`, `personalize`, `developer-settings`, `remote-connections`, `prompting`; the vendor's help centre on the macOS requirements (article 9395554).
- A public description of the vendor's design language (`nexu-io/open-design`, `design-systems/openai/DESIGN.md`): fonts, weights, radii, spacing, motion. Colours were read and not taken.
- Mightling: `docs/dev/desktop.md`, `ling-rs/web/policy.json`, `desktop/ui/src/styles.css`, `desktop/electron/src/`, DESKTOP, DESKTOP_ELECTRON and ASK specs.

---

## 9. The parity study of 2026-10-20

> **Status:** study of 2026-10-20 (owner: "study the Codex desktop app … and make Mightling's desktop app as close to it as possible"). §9.1 says what the vendor ships today, §9.2 how it was studied, §9.3 the gaps per screen ranked by user impact; the gaps to build are items 13 and on in §4.5, each with acceptance criteria.

### 9.1 What the vendor ships today

- **There is no standalone Codex desktop app any more.** The Codex app launched on macOS on 2026-02-02 and on Windows (native PowerShell and sandbox, or WSL) on 2026-03-04; on 2026-07-09 it was merged into the vendor's chat desktop app, where Codex is a view beside Chat and Work, with its own history (whats-new, "Use Codex in the ChatGPT desktop app", https://learn.chatgpt.com/docs/whats-new). Its deep links keep the `codex://` scheme "for compatibility" (https://learn.chatgpt.com/docs/reference/commands#deep-links).
- **Platforms:** macOS 14 or later, Apple silicon or Intel (the vendor's help centre, https://help.openai.com/en/articles/9395554); Windows x64 and ARM64 (https://learn.chatgpt.com/docs/windows/windows-app); Linux in preview since 2026-08-11: `.deb` and `.rpm` for x64 and ARM64, an Arch script, on Ubuntu 24.04 and 26.04, Debian 13, Fedora 43 and 44, installed from a signed vendor package repository, Computer Use not yet available, native Wayland experimental (https://learn.chatgpt.com/docs/linux/linux-app).
- **Versions:** the desktop app's release notes in the vendor's changelog (https://learn.chatgpt.com/docs/changelog) run 26.928 (2026-09-29), 26.930 (2026-10-01), 26.1002 (2026-10-07) and 26.1007 (2026-10-08), the newest on 2026-10-20; composer predictions shipped on 2026-10-09 as a beta for one paid plan. The CLI the app embeds is at 0.163.0 (2026-10-15); Mightling's `ling` is pinned at 0.158.0.
- **Since the inventory of 2026-10-14** (§2), the desktop app's own notes add: moving several selected chats to a project at once (26.930); **Remove from section** no longer pins the chat, and the Recent section stays folded when switching chats (26.1002); recovery guidance when a tool call needs approval but the chat's permissions forbid asking, with a link to those permissions (26.1007); queued messages no longer stuck on Sending and Steer restored in secondary windows (26.1007); earlier answers kept visible while new instructions are sent during a task (26.928).

### 9.2 Method, and what could be observed

**Clean room.** Only observable behaviour and public material were used: the vendor's documentation (the Markdown twins of `learn.chatgpt.com/docs/…` listed in §8 and below), its changelog and what's-new pages, its help centre, and the titles and published descriptions of its official videos. Nothing was downloaded and unpacked, decompiled or read from the vendor's app bundle, and none of its icons, images, fonts or strings were taken; every item below is re-implemented from that description. (The 2026-10-03 reading of the package that §8 and DESKTOP §1 recorded predated this rule and was removed on 2026-10-21, item 11 of §9.5.)

**Running the app was not possible within the rules.** The Mac mini runs macOS 12.3, below the app's macOS 14 minimum. The Linux preview would install on this GB10 (arm64) and on the MSI desktop (Ubuntu 26.04, x64), but the app asks for a sign-in with the vendor's account before any surface opens (https://learn.chatgpt.com/docs/linux/linux-app: "then sign in with your ChatGPT account to work with projects, local files, and Codex"), and its installer adds the vendor's package repository to the system; signing in is outside this study. So this study is documentation, release notes and official media only.

### 9.3 The gaps, per screen, ranked by user impact

Read against the code on main (7a96163) on 2026-10-20, not only against §2: where §2 says Built and the code does not, §9.4 says so. Impact: **H** a daily task fails or silently does nothing, **M** a common task takes a detour, **L** a convenience. "Decision" marks a gap whose fix changes behaviour users rely on or needs the owner's choice; those wait (§9.5). Item numbers are §4.5's. Vendor sources are `https://learn.chatgpt.com/docs/<page>`; `whats-new` is https://learn.chatgpt.com/docs/whats-new and `changelog` https://learn.chatgpt.com/docs/changelog.

| # | Screen | The vendor's app (its public source) | Mightling today | Gap | Impact |
|---|---|---|---|---|---|
| 13 | Sidebar, projects | The Projects view holds local projects as objects: **Edit project** adds folders and **Make primary** chooses the one new chats, git and `AGENTS.md`/skills/`config.toml` discovery use; secondary folders are searched and edited; **Archive chats** from a project's menu; **Remove** takes a project off the sidebar; several chats moved to a project at once (26.930) ([projects], [reference/troubleshooting#remove-a-project-from-the-sidebar], [changelog]) | Projects are derived by grouping loaded threads by folder (`store.ts` `projects`); a project header has Pin and Permanent worktree only; `project/*` and `runtimeWorkspaceRoots` are never sent, though the protocol has `Project.roots`, `thread/start.projectId` and `thread/metadata/update.projectId` and `policy.json` allows `project/list|read|create|update` | Multi-folder projects, edit, archive all, remove, move chats | H, decision (13b) |
| 14 | Approvals | Side-effecting MCP and app tool calls ask for approval, delivered as an MCP elicitation (form or URL) ([agent-approvals-security]) | Every `mcpServer/elicitation/request` is answered `decline` without showing anything (`App.tsx` `answerUnasked`) | MCP tools that ask can never run, and nobody is told | H |
| 15 | Skills and tools › Hooks | A hook that is not managed is skipped until its exact definition is reviewed and trusted, and again when it changes; each can be trusted or turned off ([hooks#review-and-trust-hooks]); import brings hooks over ([import]) | The Hooks tab lists event, command and on/off, read only; `trustStatus` is never shown | An untrusted or changed hook, every imported one included, never runs and the app says nothing | H |
| 16 | Review pane | Edit the diff in place (whats-new, 2026-07-09) | Diffs and file tabs are read only | Small fixes leave the app or go through the agent | H, decision |
| 17 | Activity | A bell in the sidebar lists chats that are unread, running or waiting for you, filtered by Work, Chat, Pinned, Scheduled, with **Mark all as read** ([notifications#follow-chats-in-activity-view]) | Activity (Ctrl+Alt+U) is a timeline of what the agent did across threads | The "what needs me" list | H, decision (beside or instead of the timeline) |
| 18 | Scheduled | Runs with findings land in the inbox and open as chats ([automations#manage-scheduled-tasks]) | Inbox rows show task, project, branch and status, cannot be opened, and leave out the run's result (`ScheduledView.tsx`; `night.ts` reads `result`, the view drops it, and the run's `session` is not mapped) | The inbox cannot be acted on | H |
| 19 | Keyboard | The Linux table's other bindings: Ctrl+Y redo, Ctrl+F4 close, Ctrl+Shift+O new chat, Ctrl+Shift+[ / ] previous / next, Ctrl+K clears a focused terminal, Ctrl+Shift+R reloads the preview without its cache, Ctrl++ and the keypad's + ([reference/commands#keyboard-shortcuts]) | First bindings only; and Ctrl+Shift+[ went **back**, Ctrl+Shift+0 reset the text size and Ctrl+Shift+1–9 went to a thread, since a shifted key matched its unshifted binding; Ctrl+K reached the shell | Bindings missing, three misfiring | M |
| 20 | Review pane, worktrees, notifications | A worktree a pinned chat is tied to is never cleaned up ([environments/git-worktrees#worktree-cleanup]); notifications only while the app is in the background ([notifications]) | The review pane's comments, draft, viewed marks and commit message followed it to the next thread shown (thread A's comments could be sent to thread B); cleanup protected running and shown threads only; a focused pop-out still got notified | Three bugs | M |
| 21 | Stream | The agent's plan is a checklist that updates as it works ([long-running-work]) | `turn/plan/updated` is handled nowhere; only Plan mode's proposal shows | The to-do list never appears | M |
| 22 | Composer | `/side`, `/pin`, `/pet`, `/project`, `/chat`, `/worktree`, `/local`, `/reasoning`, enabled skills in the `/` list ([reference/slash-commands]); fork from an earlier message (whats-new, March 16–20); queued messages edited and reordered ([prompting#steering-and-queuing]) | The actions exist as keys and buttons but not as commands; fork copies the whole thread (`ThreadForkParams.lastTurnId` unused); queued messages: steer now and remove | Typed vendor commands find nothing; no branching from the middle; a queued message cannot be fixed | M |
| 23 | Approvals | A question from the agent can take a free answer beside its options, hide a secret answer and describe each option ([prompting]) | `requestUserInput`'s `isOther`, `isSecret` and option descriptions are ignored | Plan mode's interview cannot be answered "other" | M |
| 24 | Notifications | Separate switches for permission and question notifications ([notifications#configure-desktop-notifications]) | One switch for both; every notice reads "A turn finished." with no thread name | Which of several threads finished is unknown | M |
| 25 | Sidebar | Search chats matches content and branch names and has a bindable action ([reference/commands#search-past-tasks-and-find-in-a-task]); every chat is reachable, by project or chronologically ([reference/troubleshooting#only-some-chats-appear-in-the-sidebar]) | One `thread/list` of 200, no "more": a project whose threads are all older disappears; no `searchChats` action to bind | Old projects vanish silently | M |
| 26 | Stream | Tool calls with their arguments, results and progress | `mcpToolCall` shows `server.tool` and an error only; `dynamicToolCall`, `imageView`, `hookPrompt`, `sleep` and others print their type name | `ling`'s own tools show as bare type names | M |
| 27 | Review pane | Review findings appear as inline comments in the review pane, and fold ([code-review], whats-new April 6–10) | `/review`'s findings show as Markdown in the thread | Findings are matched to lines by hand | M |
| 28 | Composer | Avoid two chats changing the same files ([long-running-work#run-goals-in-parallel]; "chat coordination", whats-new May 25–29) | A working count only | Two running threads on one checkout are not flagged | M |
| 29 | Onboarding | First run: choose where to work (a chat, a project, a folder) and import another agent's setup ([quickstart], [import]) | No first run; Import is under Skills and tools only | New users miss projects and import | M |
| 30 | Skills and tools › MCP, Plugins | Add server with environment, headers and token variables; Authenticate for OAuth; plugin details before install, search, an Installed row, on/off, marketplaces added and removed ([extend/mcp#configure-in-the-chatgpt-desktop-app], [plugins]) | Command-or-URL form; no OAuth; plugins as one row each, install or remove | Real servers need hand edits; plugins installed blind | M |
| 31 | Composer | Screenshots pasted and files attached in a coding chat ([image-inputs]) | In Work a pasted image is dropped silently and there is no paperclip; nothing warns that the served model is text-only (`Model.inputModalities` unused) | A paste does nothing, unexplained | M |
| 32 | Review pane, PRs | Every repository of a project with its lines changed, reviewed together; **All repos** in Last turn ([code-review], whats-new July 27–31); PR comments and activity beside the diff; create the PR in the app ([environments/local-environment]) | One repository at a time; the PR tab has no comments or activity; the PR is created on the forge's page | Multi-repo and PR work leave the app | M |
| 33 | Voice | Dictation cleanup and a custom dictionary for names, paths and symbols (whats-new May 4–8) | Raw transcription; Ctrl+Shift+V not bound | Code symbols come out wrong | M |
| 34 | Pets, Scheduled | The pet keeps its place, lists threads to open, shows Blocked ([pets]); a schedule's status is visible ([automations]) | The pet opens bottom right each time; Scheduled does not say whether Night Shift is on or when its window is; the Queue form lacks `--on <node>` and `--best-of` | Tasks queued that never run | M |
| 35 | Deep links | `codex://threads/new?<query>`, `new?…&originUrl=`, `settings/<page>`, `automations`, `plugins/<id>` ([reference/commands#deep-links]) | `threads/new` drops its query; no `originUrl`; `settings/<page>` opens General; no `automations`; `plugins/<id>` ignores the id | Links open the wrong place | L |
| 36 | Menu, tray | Settings from the app menu ([reference/settings]) | File: Ask, Work, Quit; the tray the same, with no count of running or waiting threads | | L |
| 37 | Subagents | One visual mark per background subagent everywhere (whats-new May 25–29) | Nickname and role as text | | L |
| 38 | Terminal | New tab › Terminal; where terminal tabs open ([integrated-terminal], [developer-settings#project-and-terminal-behavior]) | Bottom panel only | | L |
| 39 | Preview, files | Comments on an area and several before sending; address-bar history ([browser]); CSV and HTML previews one click away ([artifacts-viewer]) | One element at a time; plain address field; no CSV; the HTML note names the preview tab without a button | | L |
| 40 | Settings | Enter sends, or Ctrl+Enter only for a multiline prompt ([developer-settings#editor-settings-reference]); a PR-description prompt ([developer-settings#git]) | Two Enter modes; no PR prompt | | L |
| — | Approvals | **Approve for me**: eligible requests go to an automatic reviewer, with its status and risk on the item, and `/approve` retries a denial ([permission-modes], [sandboxing/auto-review]) | Not offered; `item/autoApprovalReview/*` is rendered nowhere (§2.4 said Same) | | M, decision |
| — | Terminal, browser | The agent reads the chat's terminal itself ([integrated-terminal#run-and-validate-your-project]); Developer mode gives it the preview's console and network ([browser#developer-mode]) | **Send to the thread** by hand; DevTools for the user only | | M, decision |
| — | Share | A read-only snapshot of a thread, secrets redacted, tool input and output left out ([use-chatgpt#share-a-read-only-snapshot-of-a-codex-thread]) | None | Local equivalent: export a thread to a file | M, decision |
| — | Scheduled | A task inside an existing chat, RRULE and minute cadences, local or worktree, several projects, model and effort per task, runs marked read and archived in bulk ([automations], whats-new June 15–19) | Night Shift: one-shot tasks in a nightly window, always a worktree | | M, decision |
| — | Composer | One predicted next message, Tab accepts it as a draft ([features/composer-predictions]); long pastes become attachments (whats-new Aug 3–7); completed-chat summaries (whats-new June 8–12); `/init` writes an `AGENTS.md` scaffold ([reference/slash-commands]) | Three suggestion chips after a turn (off by default); none of the rest | | L, decision |

Cloud or account features left out, as in §2.21: Work Cloud sync and the Local/Cloud toggle, cloud environments, Security Cloud, automatic cloud reviews, event triggers and Team Tasks, remote control from the phone, chat hand-off to another host (the Local ↔ Worktree hand-off covers this machine), partner sign-in, plans and usage, Ultrafast and Fast tiers, Dots, Space, Sites, Computer Use, Appshots, Computer History, Record & Replay, the Chrome extension, Codex Micro.

### 9.4 Rows of §2 that overstate what is built

- §2.4 **Activity view, Built:** what was built is a timeline of the agent's actions; the vendor's Activity view is the attention list of row 17.
- §2.4 **Automatic approval reviews, Same:** nothing renders `item/autoApprovalReview/*`.
- §2.4 **Plan, Built:** the proposal shows; the plan checklist (`turn/plan/updated`) does not (row 21).
- §2.3 **Images, Built:** in Ask only; a paste in Work is dropped (row 31).
- §2.3 **Voice:** dictation works from the button; its key (Ctrl+Shift+V) is planned in `keymap.json`, not bound.
- §2.2 **A project with several folders, Same:** nothing sends `project/*` or `runtimeWorkspaceRoots` (row 13).
- §2.1 **Search chats, Built:** no action to bind; a branch made later in the app is not matched (row 25).
- §2.8 **Artifacts, office formats through a local converter:** no such code.
- §2.9 **Developer mode, Same:** DevTools for the user; the agent's access is the decision row.
- §2.11 **Plugins, Built:** install and remove; no details, search, Installed row, on/off or marketplaces (row 30). **Import, Built:** one-off; automatic updates are a decision (§9.5).
- §2.12 **Scheduled, an inbox of runs with findings:** rows cannot be opened and carry no finding (row 18).
- §2.15 **Pets, Built:** Mini is the small size, not controls without a pet; the place is not kept (row 34).
- §2.19 **"Mightling adopts them unchanged":** the alternates were missing and three bindings misfired (row 19, fixed by item 19).
- §2.20 **Deep links, Built:** see row 35.

### 9.5 Decisions for the owner

1. ~~**Projects (item 13b).**~~ **Decided 2026-10-21 (the owner): server objects, opt-in.** Existing threads keep today's folder grouping; a project (several folders, Make primary, Edit, Archive chats, Remove, chats moved into it, standing instructions and knowledge files, for Ask and Work) exists only when the user creates one. Was: build projects as server objects. Proposed default: keep today's folder grouping for every existing thread, and create a project only when one is made explicitly (Ctrl+O, **New project**, Edit project). The alternative migrates every existing folder into a project at the first start.
2. ~~**Inline diff editing (row 16).**~~ **Decided 2026-10-21 (the owner): yes, as proposed** (through `git apply`, Unstaged only, `fs/writeFile` stays refused). Was: a write path in the window. The window has not written a user's file before, except by `git apply` of a hunk (Revert). Proposed: an edited hunk applied as a patch through the existing `git:apply` channel, Unstaged scope only, desktop app only; `fs/writeFile` stays refused in `policy.json`.
3. ~~**Activity (row 17).**~~ **Decided 2026-10-21 (the owner): the attention list (unread, running, waiting for an approval, Mark all as read) is Activity's main tab, today's timeline its second.** Was proposed: the attention list becomes Activity's first tab and the timeline its second; Ctrl+Alt+U and the sidebar bell open the list.
4. **Approve for me** (an automatic reviewer: a second agent on the one model server), with `/approve` (adds `thread/approveGuardianDeniedAction` to `policy.json`).
5. ~~**The agent reading the terminal or the preview's console**~~ **Decided 2026-10-21 (the owner): automatically, as tools the agent calls, bounded, secrets masked, local only; built and merged 2026-10-21** (branch `desktop/parity-agent-reads`: `windowTools` in `policy.json`). Was: (allow `dynamicTools` on `thread/start` and answer `item/tool/call` from the window, or a local MCP server from the main process): changes what reaches the model.
6. ~~**Thread export**~~ **Decided 2026-10-21 (the owner): a local file export to Markdown and HTML; paths, e-mail addresses and tokens redacted by default, with a preview before saving; nothing uploaded.** **Built and merged 2026-10-21** (branch `desktop/parity-export`).
7. **Night Shift's scheduling model,** **decided in part 2026-10-21 (the owner); that part built and merged 2026-10-21** (branch `desktop/parity-night`: `ling-admin night every`, Scheduled's run details and Repeat, tonight with a thread's context): recurring tasks (daily and weekly cadences), "do this tonight" from inside a chat with that thread's context, and a run's result, diff and transcript opened from the Scheduled inbox, fitting Night Shift's best-of-3 default and its report; still open: tasks inside a chat, cadences beyond nightly, local mode, several projects, model and effort per task, run archive (needs `ling night archive`).
8. **Composer predictions** as one inline Tab-accepted draft, replacing the chips; long pastes as attachments (needs `thread/attachment/*` or a written file); completed-chat summaries; `/init`'s prompt; side chat as a temporary free chat rather than today's saved recap (changes Ctrl+Alt+S).
9. **Smaller ones:** the worktree keep-count across all repositories rather than per repository (the vendor's count); office previews through an installed converter; `tectonic` (§7 question 4); Import's automatic updates (writes into `~/.mightling` unasked); a curated list of recommended MCP servers; mirroring the vendor's site annotation API (its namespace is the vendor's); saving "always allow this command prefix" and network amendments from the approval card.
10. **Observation.** Installing the Linux preview on the MSI desktop to look at its pre-sign-in screens only (it adds the vendor's package repository); this study did not.
11. ~~**The 2026-10-03 bundle reading**~~ **Decided 2026-10-21 (the owner): removed** from §1, §8, DESKTOP §1 and §4.6 and its sources, and DESKTOP_ELECTRON §2; a history rewrite later takes it out of the history.

[agent-approvals-security]: https://learn.chatgpt.com/docs/agent-approvals-security
[artifacts-viewer]: https://learn.chatgpt.com/docs/artifacts-viewer
[automations]: https://learn.chatgpt.com/docs/automations
[automations#manage-scheduled-tasks]: https://learn.chatgpt.com/docs/automations#manage-scheduled-tasks
[browser]: https://learn.chatgpt.com/docs/browser
[browser#developer-mode]: https://learn.chatgpt.com/docs/browser#developer-mode
[changelog]: https://learn.chatgpt.com/docs/changelog
[code-review]: https://learn.chatgpt.com/docs/code-review
[developer-settings#editor-settings-reference]: https://learn.chatgpt.com/docs/developer-settings#editor-settings-reference
[developer-settings#git]: https://learn.chatgpt.com/docs/developer-settings#git
[developer-settings#project-and-terminal-behavior]: https://learn.chatgpt.com/docs/developer-settings#project-and-terminal-behavior
[environments/git-worktrees#worktree-cleanup]: https://learn.chatgpt.com/docs/environments/git-worktrees#worktree-cleanup
[environments/local-environment]: https://learn.chatgpt.com/docs/environments/local-environment
[extend/mcp#configure-in-the-chatgpt-desktop-app]: https://learn.chatgpt.com/docs/extend/mcp#configure-in-the-chatgpt-desktop-app
[features/composer-predictions]: https://learn.chatgpt.com/docs/features/composer-predictions
[hooks#review-and-trust-hooks]: https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks
[image-inputs]: https://learn.chatgpt.com/docs/image-inputs
[import]: https://learn.chatgpt.com/docs/import
[integrated-terminal]: https://learn.chatgpt.com/docs/integrated-terminal
[integrated-terminal#run-and-validate-your-project]: https://learn.chatgpt.com/docs/integrated-terminal#run-and-validate-your-project
[long-running-work]: https://learn.chatgpt.com/docs/long-running-work
[long-running-work#run-goals-in-parallel]: https://learn.chatgpt.com/docs/long-running-work#run-goals-in-parallel
[notifications]: https://learn.chatgpt.com/docs/notifications
[notifications#configure-desktop-notifications]: https://learn.chatgpt.com/docs/notifications#configure-desktop-notifications
[notifications#follow-chats-in-activity-view]: https://learn.chatgpt.com/docs/notifications#follow-chats-in-activity-view
[permission-modes]: https://learn.chatgpt.com/docs/permission-modes
[pets]: https://learn.chatgpt.com/docs/pets
[plugins]: https://learn.chatgpt.com/docs/plugins
[projects]: https://learn.chatgpt.com/docs/projects
[prompting]: https://learn.chatgpt.com/docs/prompting
[prompting#steering-and-queuing]: https://learn.chatgpt.com/docs/prompting#steering-and-queuing
[quickstart]: https://learn.chatgpt.com/docs/quickstart
[reference/commands#deep-links]: https://learn.chatgpt.com/docs/reference/commands#deep-links
[reference/commands#keyboard-shortcuts]: https://learn.chatgpt.com/docs/reference/commands#keyboard-shortcuts
[reference/commands#search-past-tasks-and-find-in-a-task]: https://learn.chatgpt.com/docs/reference/commands#search-past-tasks-and-find-in-a-task
[reference/settings]: https://learn.chatgpt.com/docs/reference/settings
[reference/slash-commands]: https://learn.chatgpt.com/docs/reference/slash-commands
[reference/troubleshooting#only-some-chats-appear-in-the-sidebar]: https://learn.chatgpt.com/docs/reference/troubleshooting#only-some-chats-appear-in-the-sidebar
[reference/troubleshooting#remove-a-project-from-the-sidebar]: https://learn.chatgpt.com/docs/reference/troubleshooting#remove-a-project-from-the-sidebar
[sandboxing/auto-review]: https://learn.chatgpt.com/docs/sandboxing/auto-review
[use-chatgpt#share-a-read-only-snapshot-of-a-codex-thread]: https://learn.chatgpt.com/docs/use-chatgpt#share-a-read-only-snapshot-of-a-codex-thread
