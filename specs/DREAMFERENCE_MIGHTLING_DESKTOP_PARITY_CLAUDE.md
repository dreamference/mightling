# Mightling Desktop — what the Claude desktop app does better, October 2026: a gap analysis for Ask and Work

> - **Status:** proposed 2026-10-20 (owner: study the Claude desktop app, "make Mightling's desktop app as close to it as useful"), branch `desktop/parity-claude`. A companion to [DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md) (DESKTOP_PARITY), whose direction, parity with the Codex app, stands: this document adds what the Claude desktop app does that a local, private assistant should also do, ranks it, and says which items need the owner. §4 records what this branch built.
> - **Clean room.** Every claim about the Claude desktop app below is read from the vendor's public pages (help center, documentation, release notes, blog, the public Claude Code changelog), each row with its URL (§7). Nothing was downloaded from the vendor, unpacked, decompiled or read from the app's bundle, and no icon, image, font or string of the app is reused. Where a public page does not state something, the row says **not documented** rather than guessing.
> - **What could be observed:** nothing of the running app. It has a Linux build now (a beta since the week of 2026-06-29, `.deb` for x86_64 and arm64, Ubuntu 22.04+ and Debian 12+, [LNX]), so this GB10 and the MSI desktop qualify on paper, but (1) installing it needs root and registers the vendor's apt repository, which was not done on the owner's machines without the owner, the MSI's `sudo` asks for a password, and this machine runs the SWE-bench nights; (2) every surface is behind the vendor's sign-in ([LNX] "Launch and sign in"), and no account is used here, so a running copy would show only its sign-in screen. The Mac mini runs macOS 12.3 (`sw_vers`, 2026-10-20) and the app requires macOS 13 ([INSTALL]). §6 question 1 asks whether to install it on the MSI desktop, sign-in screen only.
> - **Where it stands (2026-10-20):** gap 1 (files and artifacts in Ask) merged to `main` as f77a6fc6 and gap 3 (Quick Entry) as 06c1fab5, both pushed with e13b6c83, after the full desktop gate (UI 139 and main-process 91 unit tests, the e2e suite 42 passed, the Python suite 1640 passed). On 2026-10-21 the owner answered §6: links in answers, incognito questions and earlier questions for the agent were built (§4.3 to §4.5), and this document was merged to `main`.
> - **Trade dress:** as DESKTOP_PARITY: layout and interaction may follow the compared app; its name, icon, colours, sounds and copy must not.

---

## 0. Summary

The Claude desktop app is one window with three tabs, **Chat**, **Cowork** and **Code** ([D]); since 2026-09-16 Chat and Cowork are being merged into one surface ([UNIFIED]). Mightling's app has two views over one local `ling app-server`: **Ask** (questions with no project, the agent in a scratch folder) and **Work** (the coding agent in a project). Work has been brought to the Codex app's level item by item (DESKTOP_PARITY §4.2, §4.3), and the Claude app's Code tab adds little that Work does not already have (§2.5, §2.6). **Ask is where the gap is**: the Claude app's chat surface shows what the assistant made in a panel beside the answer, gathers chats and their knowledge in projects, takes a question from anywhere through a global shortcut, keeps a chat out of history on request, and lets an earlier message be edited. Ask does none of these.

Ten gaps ranked by what they would change for a person using Mightling as a private assistant (§3), of which two need no decision and are built on this branch (§4): **files and artifacts beside an Ask answer**, and **Quick Entry**, a question from anywhere through a global shortcut, off until turned on. The rest need the owner (§6) or are shared with the Codex-parity work and left to whichever lands first.

---

## 1. The Claude desktop app, as its public pages describe it (2026-10-20)

- **Platforms:** macOS 13+, Windows 10+ (x64 and a separate arm64 build), Linux beta on Ubuntu 22.04+ / Debian 12+, x86_64 and arm64, installed from the vendor's apt repository and not self-updating ([INSTALL], [DOWNLOAD], [LNX]). On Linux there is no computer use and no dictation, and Quick Entry's global hotkey works on X11 but needs the desktop's GlobalShortcuts portal on Wayland ([LNX] "What's not in the Linux beta yet").
- **Surfaces:** Chat (conversations, projects, artifacts, connectors), Cowork (longer agentic tasks; on Linux in a QEMU/KVM virtual machine the app hosts, [LNX]), Code (Claude Code sessions with diff review, terminal, editor and live preview, [D]). Since 2026-09-16 "Cowork comes to every conversation": no Chat/Cowork switch, chats and tasks share Recents, a permission setting (Auto or Manual) in the message box, research through `/deep-research` ([UNIFIED]).
- **Everything is tied to the vendor's account:** sign-in, the model, memory, projects, connectors, sharing ([LNX], [PROJECTS], [MEMORY]). Rows that exist only because of that are in §2.17 and are not proposed.

---

## 2. Gap analysis, screen by screen

Columns: what the Claude desktop app does (with its source), what Mightling does today (with the file or spec), the gap, and the **impact** for a user of a local private assistant: **H** (changes how Ask or Work is used every day), **M** (noticeable), **L** (polish). **Shared** marks a gap DESKTOP_PARITY already lists for the Codex app, or that the Codex-parity study of the same day put in its Phase 4 (DESKTOP_PARITY §4.5 on `desktop/parity-study`, 1c69f09, items 13 to 40); those are left to whichever lands first. That study's first builds (items 14, 15 and 20 on `desktop/parity-elicitation`, `desktop/parity-hook-trust`, `desktop/parity-fixes`) touch neither Ask's files nor Quick Entry.

### 2.1 Chat list and projects

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Sidebar with the chat history (Recents / "Chats and tasks"), Projects, an Artifacts tab, Customize, Scheduled; a toggle at its top right ([APPEAR], [CUSTOMIZE], [UNIFIED]) | Ask's sidebar lists its questions, pinned first, with search; Work's lists threads by project; Ctrl+B toggles it (DESKTOP_PARITY §2.1) | No list of what the assistant made across questions (the Artifacts tab) | L |
| Rename, delete (with confirmation), multi-select delete ("⋮" › "Select", "Select all") ([DELETE]) | Rename and archive; Settings › Archived threads unarchives (DESKTOP_PARITY §2.15) | No permanent delete, no multi-select | L |
| **Projects**: a name, **instructions** ("Set project instructions"), **knowledge** files on a panel at the right of the project page, chats inside the project share them and nothing else; star a project; archive, delete; when knowledge nears the context limit it is searched instead (RAG) ([PROJECTS], [RAG]) | Ask has no projects: every question starts alone in its own scratch folder (ASK §3.1). Work's projects are folders, with `AGENTS.md` as their instructions | **Ask has nothing that gathers questions on one subject with standing instructions and reference files.** The local equivalent is close at hand: a project's knowledge as a `ling-docs` collection (LOCAL_INDEX), its instructions composed into the `ask` prompt by the policy layer | **H** |
| Move a chat into a project ("Add to project", the "Move chat" modal) ([PROJECTS]) | ASK §3.3's "Continue as Work" (fork into a project folder) is specified, not built: the policy layer pins an Ask thread's fork to its folder (`desktop/electron/src/ask.ts`) | Continue an Ask question in a project | M |
| Project memory, kept apart from the account's with "Use account memory" ([MEMORY]) | Memories are per user (Settings › Memories, DESKTOP_PARITY §2.14) | Per-project memory | L |

### 2.2 Composer, attachments, files and screenshots

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| "+" menu: Add files or photos, Connectors (per-chat toggles), Web search, Research, Memory (off for this chat), Output templates ([UPLOAD], [CONNECTORS], [WEBSEARCH], [RESEARCH], [ARTIFACTS]) | Ask: the paperclip, paste and drop attach files and images (`desktop/ui/src/ask.ts`); search follows `/airgapped` (on at `off`); `$` skills and `/` commands in both views | No per-question switch for web search or for memory; no research mode with a longer budget | M |
| Attachments: PDF, DOCX, CSV, TXT, HTML, ODT, RTF, EPUB, JSON, XLSX; JPEG, PNG, GIF, WebP; 500 MB a file, 20 a chat, PDFs to 1000 pages ([UPLOAD]) | Any file, read by the agent from its folder (`ling-docs extract`, ASK §3.3); images 20 MB, other files 100 MB (`desktop/electron/src/ask.ts` `IMAGE_CAP`, `FILE_CAP`); images reach the model only when it has vision | Equivalent; the served Qwen3.8 is text-only, so an image is read as a file | — |
| Screen capture: in Quick Entry only, "Drag to take a screenshot" or click a window ([QUICK]); not documented in the main composer | Paste a screenshot | A capture is worth little while the served model is text-only; it comes with a vision model | L |
| Model menu beside Send: model, **Effort** (Low … Max), a **Thinking** toggle ([MODEL]) | The model and effort chip (Ctrl+Shift+M), one served model (DESKTOP_PARITY §2.3) | Same | — |
| Edit a draft in place (release notes 2026-06-12, [RELNOTES]); editing an **earlier** message creates "a different version of the chat, with its own artifacts" ([ARTIFACTS]); retry and version arrows are not documented | Esc Esc / **Edit** on the **last** turn only (`thread/revert`, DESKTOP_PARITY §2.3) | **Edit an earlier message**, keeping the version it replaces | M |
| Voice mode: a spoken conversation, hands-free or push-to-talk, transcripts saved ([VOICE]); not on Linux, where dictation is also missing ([LNX]) | Dictation into the composer through the local speech-to-text sidecar, at `/airgapped on` too (ASK §7, Ctrl+Shift+V) | Spoken answers need a local text-to-speech sidecar; **Mightling is ahead on Linux** (dictation works) | L |

### 2.3 Artifacts and preview panes

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| **What the assistant makes opens in a panel beside the chat** and is kept in the sidebar's Artifacts tab; Docs, Slides, Design, Dashboards; edit in place or select text and **Edit with Claude**; Export to Word, PDF, Markdown…; "Try fixing" on an error ([ARTIFACTS]); keyboard: Return opens, Escape steps out then closes the panel ([A11Y]) | Work: the artifacts viewer (Markdown, images, PDF, HTML) in a tab beside the thread, opened by itself when a turn creates one (Settings › General, `openArtifacts`, on by default; DESKTOP_PARITY §2.8). **Ask: none of it.** The tab area is Work's only (`tabsShown` requires `view === "work"`, `desktop/ui/src/App.tsx`), a file an Ask answer writes shows only as a diff, and ASK §3.1's "the thread's view lists them, with Reveal in folder" was never built | **Ask has no panel for what the agent made, no list of the question's files and no way to open its folder** | **H — built, §4.1** |
| Inline visuals: HTML charts and diagrams in the answer, full screen, Copy as image, Download, Save as artifact ([VISUALS]); legacy artifacts included Mermaid ([FILES]) | Markdown through `marked`; Mermaid not bundled (DESKTOP_PARITY §2.4, Partial) | Diagrams in answers | M, **Shared** (DESKTOP_PARITY §2.4) |
| Select text in an artifact and ask for a change ([ARTIFACTS]) | Not offered | Annotations on a preview | M, **Shared** (DESKTOP_PARITY §2.8, "Later") |
| Code tab: a Browser pane for the dev server, Auto-verify, element selection (Cmd+Shift+S), `.claude/launch.json`, links opened in the pane or the default browser (asked the first time) ([D] "Preview your app") | Work's preview tab with comment mode and per-site rules (DESKTOP_PARITY §2.9); no auto-verify (a text-only model cannot look) | Auto-verify waits for a vision model | L |
| File creation (.docx, .xlsx, .pptx, .pdf) in a sandbox with an egress switch ([FILES]) | The agent writes any file into the Ask folder or the project; office formats only through tools installed on the machine | Equivalent in principle; nothing bundled | L |

### 2.4 Links in answers

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Not documented for Chat. In Code, the first external link asks whether to use the Browser pane or the default browser; Cmd-click goes to the default browser ([D] "Browse external sites") | **A link in a message is not followable**: `renderMarkdown` writes it as a span with the address in its title (`desktop/ui/src/markdown.ts`), a Tauri-era limit ("no opener plugin yet", DESKTOP §9) kept since. Ask's answers cite sources as `[n]` with the list at the end (ASK §3.2), so a source cannot be opened from the answer | **Open a cited source** | **H — needs the owner, §6 question 2** |

### 2.5 Code view and diffs (the Code tab against Work)

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Diff pane (Cmd+Shift+D) file by file; click a line to comment, Cmd+Enter sends all comments; `/code-review` findings walked in the diff with **Fix this one**; Create PR through `gh`, Auto-fix and Merge when ready on the PR's menu ([D] "Review changes with diff view", "Review your code", "Monitor pull request status") | The review pane (Ctrl+Shift+G) with scopes, stage/revert per hunk, inline comments sent to the composer, `/review`, commit, push, the forge's PR page, the `gh` pull-request tab (DESKTOP_PARITY §2.5) | Review findings as inline comments on the diff (DESKTOP_PARITY §2.5 already says they show as Markdown only) | M, **Shared** |
| File pane: open a path from the chat, edit it in place with auto save, Override / Discard on a change on disk ([D] "Open and edit files") | File tabs, read only (DESKTOP_PARITY §2.8) | Editing in a file tab | M, **Shared** |
| Right-click any path (chat, diff, file pane): **Attach as context**, **Open in** (an editor), **Show in Finder/Explorer**, **Copy path** ([D] "Open files in other apps") | The open-file button on a changed file, the review pane's file names (editor), the header's Open (DESKTOP_PARITY §2.5, §2.6) | One context menu on every path | M, **Shared** |
| Panes dragged by their header, resized, popped out and docked back ([D] "Arrange your workspace") | Split / full / hidden tab area, the bottom panel, a thread popped out (DESKTOP_PARITY §2.1) | Free pane arrangement | L |
| Transcript view: Normal, Thinking, Verbose (Ctrl+O) ([D] "Switch view modes") | Settings › Project and terminal: command output open, folded or hidden | Same in effect | — |
| Task chips: work Claude finds out of scope offered as a chip that starts its own session in a worktree ([D] "Work across sessions") | Not offered | A suggested follow-up that starts a new thread | L |

### 2.6 Permission prompts

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Code tab: the mode under the prompt box (Manual, Accept edits, Plan, Auto, Bypass; Cmd+Shift+M), remembered per folder ([D] "Choose a permission mode"); in Manual mode each edit shows a diff with Accept / Reject ([QS]); the general tool prompt's exact labels are not documented, the CLI's are Yes / Yes, and don't ask again / No ([PERMS]) | The permissions picker in Work's composer; approvals in the stream with **Allow**, **Allow for this session**, **Decline** (`Approval` in `desktop/ui/src/App.tsx`), Enter / Esc (DESKTOP_PARITY §2.3) | Same in substance | — |
| Connectors: per tool or category **Always allow**, **Needs approval**, **Blocked** ([CONNECTORS]) | Skills and tools › MCP servers turns a whole server off or on (DESKTOP_PARITY §2.11) | **Per-tool rules for MCP tools** | M |
| Browser pane, external site: **Allow once**, **Always allow**, **Deny**, revocable in Settings ([D] "Approve Claude's actions on a site") | The preview tab: Allow once, Always allow, Block, kept in Settings › Browser (DESKTOP_PARITY §2.9) | Same | — |

### 2.7 Settings

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| General: Instructions for Claude, voice, and under "Desktop app" the **Quick access shortcut**, the voice shortcut, computer use ([PERSONAL], [UNIFIED], [QUICK]) | Settings › Personalization edits `~/.mightling/AGENTS.md`; no quick-entry shortcut | The quick-entry shortcut | **H — built, §4.2** |
| Appearance: Color mode (Light, Match System, Dark); **Chat font** (Default, Match System, **Dyslexic Friendly**) ([APPEAR]) | System / Light / Dark, accent and background colours, the UI and code font lists (DESKTOP_PARITY §2.15, item 11) | A ready-made readable-font choice | L |
| Memory: generate from chats, pause, reset, **Topics** listed and edited one by one, sensitive topics off by default ([MEMORY]) | Settings › Memories: on/off, tool-assisted chats, delete all, status (DESKTOP_PARITY §2.14) | Seeing and editing individual memories | M |
| Capabilities: code execution and file creation, network egress ([FILES]) | `/airgapped off|on` (AIRGAPPED) | Mightling's is stronger (an empty network namespace) | — |
| Privacy: **Export data** (an emailed link), Shared chats ([EXPORT], [SHARE]) | Sessions are files under `~/.mightling/sessions`; Copy the conversation's path (DESKTOP_PARITY §2.1) | Export a question as Markdown | L |
| Language: 11 interface languages ([LANG]) | English only | Localisation | L |
| Reflect (a monthly recap), Time and focus (break reminders, quiet hours) ([REFLECT], [FOCUS]) | Settings › Profile: local activity insights (DESKTOP_PARITY §2.15) | Not proposed (the activity page covers the useful part) | — |

### 2.8 Keyboard shortcuts

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Code tab (Cmd → Ctrl on Windows and Linux): Cmd+/ shortcuts, Cmd+N, Ctrl+Tab, Cmd+W, Esc stop, Cmd+Shift+D diff, Cmd+Shift+B browser, Ctrl+\` terminal, Cmd+; side chat, Cmd+Shift+M mode, Cmd+Shift+I model, Cmd+Shift+E effort, Ctrl+O view mode ([D] "Keyboard shortcuts"); artifact panel keys ([A11Y]); a general chat list is not documented | 84 rebindable actions from the Codex app's Linux table (`desktop/ui/src/keymap.json`, DESKTOP_PARITY §2.19) | The Codex bindings stay, on purpose (one set of hands for both apps); Escape closing the artifact panel is added with §4.1 | L |

### 2.9 Quick Entry and the global hotkey

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| **Quick Entry:** a system-wide shortcut (double-tap Option by default, Option+Space or a custom one, Settings › General › Quick access shortcut) opens a floating box over any app; Enter sends it as a new chat; the five most recent chats are a click away; a region or window can be captured into it; dictation by Caps Lock; a first-launch prompt offers to turn the shortcut on; the app must be running, in the background is enough ([QUICK]). On Linux it works on X11, on Wayland through the GlobalShortcuts portal ([LNX]) | Inside the window only: Ctrl+Alt+N opens a new question, and the pet's quick question (off by default) does the same from its bubble (DESKTOP_PARITY §2.1); closing the window quits the app (`window-all-closed` in `desktop/electron/src/main.ts`); the tray has Ask, Work and Quit | **No way to ask from another app without switching to Mightling first** | **H — built, §4.2** |
| Deep link `claude://claude.ai/new?q=` prefills a new chat ([LINKS]) | `ling://new?prompt=&path=` starts a Work thread (`desktop/electron/src/args.ts`); no Ask equivalent | `ling://ask?prompt=` | L — built with §4.2 |

### 2.10 Tray, menu and the app in the background

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Menu: Claude › Check for Updates…, Settings…; Help › Troubleshooting › Generate Diagnostic Report, Show Logs ([ENTERPRISE], [WINDEPLOY]); a tray icon, launch at login and close-to-tray are **not documented**; Quick Entry needs the app running in the background ([QUICK]) | File (Ask, Work, Quit), Edit, View, Window; a tray icon with Ask, Work, Quit (`desktop/electron/src/shell.ts`); closing the window quits | Staying in the tray when the window closes, which Quick Entry needs (§4.2 adds it as its own setting, off by default); "Show logs" (the app-server's stderr is shown only on its start-up screen) | M; the menu's and tray's other entries are **Shared** (DESKTOP_PARITY §4.5 item 36) |

### 2.11 Notifications

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Code tab: an OS notification when a session finishes and you are not viewing it ([D] "Get notified when a session finishes"); phone notifications for Dispatch and Remote Control; no setting for chat replies is documented; dock badges not documented | Each finished turn and each waiting approval or question, silent, Settings › Notifications (window not in front, always, never) (DESKTOP_PARITY §2.1, §2.15) | **Mightling is ahead** | — |

### 2.12 Onboarding

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Sign-in; at first launch a prompt offering to turn Quick Entry's shortcut on ("Turn on shortcut", Continue) ([QUICK]); nothing else documented | A start-up screen while the app-server starts or after it stops (`StartupScreen`); Ask's empty page explains what Ask is | A first-run page naming Ask, Work, the shortcut and `/airgapped` | L, **Shared** (DESKTOP_PARITY §4.5 item 29, First run, on `desktop/parity-study`) |

### 2.13 Theming

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| Light, Match System, Dark; three chat fonts ([APPEAR]) | System, Light, Dark with no flash at start, per-theme colours, font lists, a theme file (DESKTOP_PARITY §2.15) | **Mightling is ahead** except the readable-font preset (§2.7) | — |

### 2.14 Memory, search and privacy of a chat

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| **Search and reference past chats** as a tool the assistant calls, with citations back to the chats; off in Settings › Memory ([MEMORY]) | The user searches threads (`thread/search`, the sidebar); the agent cannot | **The agent recalling earlier questions** — a local tool over `~/.mightling/sessions`, off by default | M — needs the owner, §6 question 4 |
| **Incognito chat:** a ghost icon on a new chat; a black border and an "Incognito chat" label; not saved to history or memory; cannot be reopened once closed; not in projects ([INCOGNITO]) | Every Ask question is saved as a session and may feed memories | **A question that leaves nothing behind**: `thread/start` with `ephemeral` (already allowed: the commit-message helper uses it) plus removing its scratch folder when it closes | M — needs the owner, §6 question 3 |
| Share a chat as a public snapshot link ([SHARE]) | Not offered | Not proposed (a public link is a hosted service); export to a file covers the local need (§2.7) | — |

### 2.15 Connectors and extensions

| Claude desktop app | Mightling | Gap | Impact |
|---|---|---|---|
| **Desktop Extensions:** a local MCP server packed as one `.mcpb` file (formerly `.dxt`), installed from a directory or by double-click / drag onto Settings, with its runtime built in and secret fields in the system keychain ([LOCALMCP], [DXT]); `claude_desktop_config.json` for hand-written servers ([D] "MCP servers from the Claude desktop chat app") | Skills and tools › MCP servers adds a server by command or URL into `config.toml` (DESKTOP_PARITY §2.11) | One-file install of a local MCP server; it runs third-party code, so it needs the owner (§6 question 5) | M |
| Interactive connectors (MCP Apps): cards inline, sandboxed frames ([INTERACTIVE]) | Not offered | Waits for the protocol's support in `ling` | L |
| Customize: Skills, Connectors, Plugins, each with Discover ([CUSTOMIZE]) | Skills and tools: skills, MCP servers, apps, plugins (local and repository marketplaces), hooks (DESKTOP_PARITY §2.11) | Same, without a hosted catalogue (by design) | — |

### 2.16 Where Mightling is ahead

Dictation on Linux (the Claude app has none there, [LNX]); notifications for approvals and questions with their own settings; `/airgapped on` as an empty network namespace; Night Shift as the scheduled-task runner that also runs with the window closed (the Claude app's local scheduled tasks run only while the app is open and the computer awake, [ST]); worktree hand-off in both directions; per-hunk stage and revert; everything on the user's own machine and model.

### 2.17 Not proposed: the vendor's account and cloud

Sign-in, plans and usage limits; cloud sessions, Cowork in a virtual machine and Dispatch; Remote Control from a phone; shared projects and chats; Research on the vendor's models; computer use (macOS and Windows only); Claude in Chrome; Reflect. Each needs the vendor's service or a vision model; DESKTOP_PARITY §2.21's reasons apply.

---

## 3. The ten gaps that matter, ranked

| # | Gap | Impact | What it takes | Status |
|---|---|---|---|---|
| 1 | **Files and artifacts beside an Ask answer**, the question's folder listed and opened (§2.3) | H | Window only: the tab area in Ask for files inside the question's folder, the folder's tree, a card per file an answer made | **Built** (`desktop/ask-files`), §4.1 |
| 2 | **Projects for Ask:** instructions, knowledge files searched through `ling-docs`, questions gathered, a question moved in (§2.1) | H | The policy layer composes a project's instructions into the `ask` prompt and names its collection; the UI a project page | Decided 2026-10-21: Projects are the Codex-parity work's (DESKTOP_PARITY, branch `desktop/parity-projects`: one project for Ask and Work, instructions and knowledge files); Ask builds on them, no Ask-only projects |
| 3 | **Quick Entry:** a system-wide shortcut, a floating box, the app kept in the tray (§2.9, §2.10) | H | Window only: `globalShortcut`, a small window, two settings off by default | **Built** (`desktop/quick-entry`), §4.2; recent questions in the box not built |
| 4 | **Open a cited source** from an answer (§2.4) | H | The renderer keeps the address; a click opens it in the system browser after showing it; refused at `/airgapped on` like the forge's page | **Built** (decided 2026-10-21), §4.5 |
| 5 | **Edit an earlier message**, the replaced version kept (§2.2) | M | `thread/fork` at that turn, then `turn/start`; a switch between the versions | Proposed; the version switcher is UI the owner should see first |
| 6 | **Incognito question** (§2.14) | M | `ephemeral` threads, the folder removed on close (a main-process removal confined to the Ask root) | **Built** (decided 2026-10-21), §4.3 |
| 7 | **The agent recalls earlier questions** (§2.14) | M | A read-only MCP tool over the session files, off by default | **Built** (decided 2026-10-21) as a skill over kept copies rather than an MCP tool, §4.4 |
| 8 | **Per-tool rules for MCP tools** (§2.6) | M | The pinned `ling`'s per-tool approval keys, if any, written through `config/value/write` | Proposed; to check against the pinned Codex's config first |
| 9 | **Diagrams in answers** (Mermaid, inline charts) (§2.3) | M | Bundle Mermaid in the renderer, no script from the answer | **Shared** with DESKTOP_PARITY §2.4 |
| 10 | **One context menu on every path** (attach, open in editor, show in folder, copy path) (§2.5) | M | Window only | **Shared** with DESKTOP_PARITY §2.5, §2.8 |

Below the ten: one-file MCP extensions (§6 question 5), seeing and editing memories one by one, export a question as Markdown, permanent delete and multi-select, a readable-font preset, a first-run page, "Show logs", interface languages.

---

## 4. Built on `desktop/parity-claude`

### 4.1 Files and artifacts in Ask (gap 1)

Branch `desktop/ask-files`, window only (no method added to `policy.json`).

- **A file an answer makes is a card** in the answer: its name, its kind in words (Document, Web page, PDF, Image, File) and **Open**; the diff folds under it. Edits to existing files stay as diffs. (`MadeFile` in `desktop/ui/src/App.tsx`.)
- **It opens beside the answer**, in the same tab area and artifacts viewer as Work (Markdown, images, PDF, HTML), by itself when a turn creates one and Settings › General's "Open a document, PDF or image the agent creates…" is on (the default), else from the card.
- **The question's folder:** the header's **Files** button (and Ctrl+Shift+E in Ask) shows the folder's tree beside the answer; **Open the folder** opens it in the file manager. ASK §3.1's "the thread's view lists them, with Reveal in folder" is thereby built in the app.
- **Each view shows its own tabs** (`viewTabs` in `desktop/ui/src/ask.ts`): Ask only the files of the question in front, Work every tab but an Ask question's files; the list stays one, so a tab is where it was when the view comes back. Ctrl+W, Ctrl+Alt+W, Ctrl+P and Ctrl+L act on what the view shows.
- **Tests:** `ask.test.ts` (which tabs each view shows, the card's words); `e2e/extras.e2e.ts` (an Ask answer that writes `report.md`: the card, the tab beside the answer, close and Open again, the folder's tree, Work not showing it, another question not showing it, one app-server); the stand-in writes the file when a question says "with a report".

### 4.2 Quick Entry (gap 3)

Branch `desktop/quick-entry`, window only (no method added to `policy.json`; two message types on the app's own channel, as the pet has).

- **Settings › General › Quick Entry**, off by default: "Ask a quick question from any app with Ctrl+Alt+Space"; the shortcut is changed by pressing it in the field (Ctrl, Alt or Super with a letter, digit, function key or Space; Shift alone is refused, since it would take typing from other apps). The page says whether the shortcut was taken and, if not, why: taken by another app or the desktop, not a valid shortcut, or a Wayland session.
- **The box** (`desktop/electron/src/quick.ts`, `desktop/ui/src/QuickView.tsx`): a small frameless window above the others, in the upper third of the screen the pointer is on; Enter asks the text as a **new Ask question at once** and brings the app window forward on it, Shift+Enter starts a new line, Esc or a click elsewhere puts it away. It may ask the main process for `quick/ask` and `quick/hide` only (`handleQuick` in `main.ts`, beside the pet's `handlePet`). Unlike the compared app, it has no list of recent questions and no screen capture (the served model is text-only).
- **Other ways in:** the tray's **New question…**; `ling://quick` (so a Wayland desktop, where an app cannot take a system-wide key, can bind `xdg-open ling://quick` itself); `ling://ask?prompt=…` puts text in a new question's composer without sending it, as the compared app's `new?q=` does.
- **Keep Mightling in the tray when its window closes**, off by default (Settings › General): closing hides the window, so the shortcut and running turns go on; Quit (Ctrl+Q, the tray, the menu, a signal) still ends the app. With it off, closing quits as before; the egress audit's session never keeps it.
- **Tests:** `quick.test.ts` (which shortcuts may be system-wide and their Electron spelling, Wayland, the question trimmed and capped), `args.test.ts` (`ling://ask`, `ling://quick`), `ui/src/quick.test.ts` (the box's keys); `e2e/quick.e2e.ts`: off by default no shortcut is taken; on, the shortcut is registered, the box opened as `ling://quick` opens it asks "What is NVFP4?" as a new Ask question whose answer streams in, closing the window keeps it, a link brings it back, Settings turns the shortcut off and it is released, one app-server.

---

### 4.3 Incognito questions (gap 6, decided 2026-10-21)

Branch `desktop/incognito`, the desktop app only (`ling web` shows no switch: deleting a question's folder from a browser needs a route in `ling web`, a later step).

- **The switch:** **Incognito** in the header of a new Ask question (Settings need nothing). Before the first message, and for as long as the question is shown, a dashed frame round the thread and a note say what it means: not saved, out of the history, the memories and later searches, and **deleted for good**, with every file in its folder, when it is left.
- **Not saved:** the thread starts with `ephemeral: true`, so the server writes no session file and its memory pipeline skips it (Codex: `memories/write/src/start.rs` returns for an ephemeral session); the page lists no ephemeral thread, and an incognito question cannot be forked, popped out or side-chatted (each would make a saved copy).
- **Deleted when left:** another question, a new one, Work: the page stops its turn, sends `thread/unsubscribe` and asks the main process (`ask/forget`) to delete the folder, its files and its `ask/<thread-id>` link. The main process marks the folder when the question starts (`ask/incognito`, `.mightling-incognito`) and deletes only marked folders (`forgetIncognito`, `desktop/electron/src/ask.ts`); at the app's start and quit it sweeps any marked folder left over (`sweepIncognito`), so a crash or a quit with one open leaves nothing.
- **Tests:** `ask.test.ts` (a marked question deleted with its files and link, an unmarked one refused, the sweep), `e2e/incognito.e2e.ts` (a crashed app's leftover swept at the start; the switch and its note; started ephemeral, not listed, no fork; its file in the marked folder; leaving deletes folder and link and unsubscribes; a saved question untouched; one open at quit deleted).

### 4.4 Earlier questions for the agent (gap 7, decided 2026-10-21)

Branch `desktop/recall`, the desktop app only. Off by default: **Settings › Memories › Let the agent search your earlier Ask questions**.

- **What the agent gets:** a skill of the user's, `~/.mightling/skills/earlier-questions/SKILL.md`, loaded by every new session as the user's skills are: when the user refers to something asked before, search the copies with `rg` and read them, read only, and say which earlier question it used. Nothing new runs: the agent's own shell, in its own sandbox, on this machine. (An MCP server was the first idea, §3; the clients carry no Python and adding a tool to `ling` means a rebuild, while a skill over plain files needs neither.)
- **What is kept:** each Ask question of the user's as Markdown under `~/.mightling/ask-history/<thread-id>.md`: its title, when it was asked, its folder, then each question and answer (no commands, diffs or reasoning; 512 KB at most). Written by the page from `thread/list` and `thread/turns/list` (all when the app starts or the switch is turned on, one after each finished turn) through the main process (`recall/write`, refused while the switch is off; `desktop/electron/src/recall.ts`); never an incognito, subagent or Work thread; an archived question's copy removed (`recall/prune`).
- **Off** deletes the skill and every copy.
- **Tests:** `recall.test.ts` in both packages (nothing kept while off; the skill names the folder; copies by thread; long ones cut; pruning; off deletes all; what a copy holds and leaves out), `e2e/recall.e2e.ts` (off by default; on, the skill and the Ask question's copy, Work's threads not copied; a new question copied when its turn ends; off deletes both).

### 4.5 Links in answers (gap 4, decided 2026-10-21)

Branch `desktop/answer-links`, the app and `ling web`.

- A web address in a message, a review, a Night Shift report or a pull request's body (http or https, with a host, and no user name or password, which can hide the real host) is a link (`webLink` in `desktop/ui/src/markdown.ts`); every other link stays inert, its address on hover.
- A click or Enter opens a dialog first: the site on a line of its own, the full address, **Open in your browser**, **Copy the address**, **Cancel** (`LinkDialog.tsx`). The level is read when the link is clicked: at `/airgapped on` the dialog says so and offers the copy only.
- The app opens it with the system's browser through the main process (`open/url`), which checks the address again (`desktop/electron/src/links.ts`) and refuses at `/airgapped on` as it refuses the forge's page; `ling web` opens it in a new tab with no opener and no referrer.
- **Tests:** `work.test.ts` (which links act, which stay inert), `links.test.ts` in both packages, `e2e/links.e2e.ts` (the system's browser replaced by a recorder: Cancel and Esc open nothing, Open opens the address once; at `/airgapped on` no Open and the main process refuses; a `file:` address refused).

### 4.6 The composer (decided 2026-10-21)

The owner's four composer items. Two were DESKTOP_PARITY §4.5 item 22 (fork from a message, queue edits), handed to this work the same day.

- **Long pastes as attachments** (branch `desktop/composer-paste-predict`): a paste of 2,000 characters or 40 lines becomes a chip, "Pasted text · N lines", its start on hover (`isLongPaste`, `pasteLabel` in `desktop/ui/src/composer.ts`). In Ask it is uploaded into the question's folder as `pasted-<time>.txt`, the agent reading it as any attachment; in Work nothing is written into the project, and the text goes into the message after the words as a fenced block (`withPastes`). Settings › General turns it off.
- **One predicted next message** (same branch), replacing Phase 3 item 7's three chips: Settings › Suggested prompts (still off by default) has the served model predict the one message the user is most likely to send next (`predict` in `suggestions.ts`), shown in grey in the empty composer; Tab or → takes it in (`acceptsPrediction`), and it is never sent by itself, as the compared app's Code tab does ([D] "Accept a suggested prompt").
- **Fork from here, Edit in a new thread** (branch `desktop/fork-queue`): every finished turn offers both (on hover for earlier ones): the first is `thread/fork` through the turn (`lastTurnId`), the second `thread/fork` before it (`beforeTurnId`) with the turn's words in the new thread's composer. The thread itself is left as it is (the last turn's Edit still takes the message back in place), and an incognito question is never copied.
- **Queue edits** (same branch): a queued message is edited in place (`thread/queue/update`: its words replaced, images and skills kept; Enter keeps, Esc leaves it) and moved up or down (`thread/queue/reorder`).
- **Tests:** `paste.test.ts`, `suggestions.test.ts`, `queue.test.ts`; `e2e/composer.e2e.ts` (a 60-line paste: a file in Ask's folder, a fenced block in Work and no file in the project, a short paste stays text), the Phase 3 e2e's item 7 (the prediction in grey, Tab takes it, nothing sent), `e2e/forkqueue.e2e.ts` (a fork through the first of two turns, an edit in a new thread before the second with its words in the composer, two queued messages edited and reordered).

### 4.7 Projects in Ask: the project's page and its knowledge searched (decided 2026-10-21)

Branch `desktop/project-knowledge`, on the Projects of DESKTOP_PARITY §4.5 item 13 (one project for Ask and Work: folders, standing instructions, knowledge files), the compared app's project knowledge panel and its search of large knowledge ([PROJECTS], [RAG]).

- **The page:** a click on a project in Ask shows its page while a new question would start in it (`ProjectPage.tsx`): its standing instructions, its knowledge files with sizes and how the agent reaches them (searched, being indexed, or read whole), **Add files…**, **Edit project…**, and its questions, each a click away.
- **Search over the knowledge:** the project's knowledge folder becomes a `ling-docs` collection, `project-<id>`, made with its first file, reindexed as files come and go and removed with its last (`syncKnowledge`, `desktop/electron/src/docs.ts`). Once it is indexed, the project's context tells the agent to use `docs_search` with that collection for the passages a question needs (`projectContext`). `ling-docs` never indexes anything under `~/.mightling`; the owner allowed exactly this folder, `$CODEX_HOME/projects/<id>/knowledge`, on 2026-10-21 (LOCAL_INDEX §5), and a `ling-docs` built before it refuses the collection, in which case the files are read whole as before.
- **Tests:** `collections.rs` and `discover.rs` (the folder allowed; a sibling, the agent's sessions and a knowledge link into a secret folder refused; links inside skipped and, followed, never leading out), `knowledge.test.ts` in both packages (the collection's name, the context's hint only once indexed, the reach), `e2e/knowledge.e2e.ts` (a project made in Ask with instructions; its page; a file added becomes `ling docs add <folder> --name project-proj-1` and a reindex, the page saying the agent searches it; the first question's context naming the collection; the question listed on the page).

## 5. Tests and acceptance

Each item of §4 lands with its unit tests (`npm test` in `desktop/ui` and `desktop/electron`), an e2e case in `desktop/electron/e2e/` against the scripted stand-in, the full e2e run after `npm run package`, and the Python suite. Neither item adds a method to `ling-rs/web/policy.json`, so the Rust conformance run is not needed.

---

## 6. Questions for the owner, and the answers

Answered on 2026-10-21:

1. **Run the real app once?** Not now.
2. **Links in answers:** yes, after a confirmation showing the full address, never at `/airgapped on`: built, §4.5.
3. **Incognito questions:** yes, the thread and its folder deleted when left, the irreversibility made clear: built, §4.3.
4. **Recall of earlier questions:** yes, local and read only, off by default, a setting: built, §4.4.
5. **One-file MCP extensions (`.mcpb`):** not now.
6. **Projects for Ask:** the Codex-parity work owns Projects (instructions and knowledge files for Ask and Work); Ask builds on them, with no Ask-only projects.
7. **This document on `main`:** yes, merged.

Still open: composing a project's instructions into the `ask` prompt in the policy layer (today the window sends them as the first turn's context, DESKTOP_PARITY §4.5 item 13); `ling-docs` search over a project's knowledge was built on 2026-10-21, §4.7.

---

## 7. Sources

Read on 2026-10-20.

- [D] Use Claude Code Desktop: https://code.claude.com/docs/en/desktop
- [QS] Desktop quickstart: https://code.claude.com/docs/en/desktop-quickstart
- [ST] Desktop scheduled tasks: https://code.claude.com/docs/en/desktop-scheduled-tasks
- [LNX] Claude Desktop on Linux (beta): https://code.claude.com/docs/en/desktop-linux
- [PERMS] Permissions: https://code.claude.com/docs/en/permissions
- [INSTALL] Install Claude Desktop: https://support.claude.com/en/articles/10065433-install-claude-desktop
- [DOWNLOAD] https://claude.com/download
- [UNIFIED] Claude Cowork and chat in one Claude: https://support.claude.com/en/articles/16761823
- [QUICK] Use Quick Entry with Claude Desktop on Mac: https://support.claude.com/en/articles/12626668-use-quick-entry-with-claude-desktop-on-mac
- [LINKS] Open Claude Desktop with a link: https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link
- [APPEAR] Customizing your appearance settings: https://support.claude.com/en/articles/8887527-customizing-your-appearance-settings
- [CUSTOMIZE] Browse skills, connectors and plugins: https://support.claude.com/en/articles/14328846-browse-skills-connectors-and-plugins-in-one-directory
- [DELETE] Delete or rename a conversation: https://support.claude.com/en/articles/8230524-delete-or-rename-a-conversation
- [PROJECTS] How can I create and manage projects: https://support.claude.com/en/articles/9519177-how-can-i-create-and-manage-projects
- [RAG] RAG for projects: https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects
- [MEMORY] Memory and search of past chats: https://support.claude.com/en/articles/11817273
- [INCOGNITO] Using incognito chats: https://support.claude.com/en/articles/12260368-using-incognito-chats
- [SHARE] Share and unshare chats: https://support.claude.com/en/articles/10593882-share-and-unshare-chats
- [EXPORT] Export your data: https://support.claude.com/en/articles/9450526-export-your-claude-data
- [UPLOAD] Upload files: https://support.claude.com/en/articles/8241126-upload-files-to-claude
- [CONNECTORS] Connectors and custom connectors: https://support.claude.com/en/articles/11176164
- [INTERACTIVE] Interactive connectors: https://support.claude.com/en/articles/13454812-use-interactive-connectors-in-claude
- [WEBSEARCH] Web search: https://support.claude.com/en/articles/10684626-enable-and-use-web-search
- [RESEARCH] Research: https://support.claude.com/en/articles/11088861-use-research-on-claude
- [MODEL] Model, effort and thinking: https://support.claude.com/en/articles/8664678-change-the-model-effort-and-thinking-settings
- [ARTIFACTS] Artifacts (since 2026-09-16): https://support.claude.com/en/articles/17153992
- [A11Y] Artifacts with a keyboard or screen reader: https://support.claude.com/en/articles/17457950-use-artifacts-with-a-keyboard-or-screen-reader
- [VISUALS] Custom visuals in chat: https://support.claude.com/en/articles/13979539-custom-visuals-in-chat
- [FILES] Create and edit files: https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude
- [VOICE] Voice mode: https://support.claude.com/en/articles/11101966-use-voice-mode
- [PERSONAL] Personalization features: https://support.claude.com/en/articles/10185728-understanding-claude-s-personalization-features
- [LANG] Preferred language: https://support.claude.com/en/articles/10769299-how-to-use-claude-in-your-preferred-language
- [REFLECT] https://support.claude.com/en/articles/15672559 ; [FOCUS] https://support.claude.com/en/articles/15672868
- [LOCALMCP] Local MCP servers on Claude Desktop: https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- [DXT] Desktop Extensions: https://www.anthropic.com/engineering/desktop-extensions
- [ENTERPRISE] Enterprise configuration: https://support.claude.com/en/articles/12622667-enterprise-configuration-for-claude-desktop
- [WINDEPLOY] Deploy for Windows: https://support.claude.com/en/articles/12622703-deploy-claude-desktop-for-windows
- [RELNOTES] Release notes: https://support.claude.com/en/articles/12138966-release-notes
- Code tab redesign (2026-04-14): https://claude.com/blog/claude-code-desktop-redesign
