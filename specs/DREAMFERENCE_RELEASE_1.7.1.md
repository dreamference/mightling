# Mightling 1.7.1 — release notes

**Status:** published 2026-10-26 as `v1.7.1` (tag on `ea4eb73`; this line updated 2026-10-29). Prepared 2026-10-22 on the branch `release/1.7.1` (origin/main at `f93b53b`, plus the
version set to 1.7.1 and these notes), cut through the release workflow once the gate, the build on
the node and the device checks passed (the owner approved cutting without asking). Built from the
merges in `v1.7.0..release/1.7.1`. The text between the two rules is the GitHub release's
description: the release workflow publishes it as it stands (`scripts/release_notes.py`).

**Version.** Set to 1.7.1 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`,
`desktop/electron/package.json` and its `package-lock.json`.

**What 1.7.1 carries beyond 1.7.0** (merges into `main`):

| Area | Merges |
|---|---|
| Security: the sandbox and the host's control sockets | e0544b6 |
| The model server: SGLang's history keeps an announcement with its tool call | 046b126 |
| Phones: Telegram topics, voice, images and files in, files back, Night Shift, bounds, a fresh thread after a silence, quick follow-ups, the Mini App | 5764057, ef66653, 10d0de8, 2111639, 12dc2ed, 1da1f45, 00d0d66, e71cbe1, 4f02b20, 1305969 |
| Desktop app: dark mode, Settings › Node, Quick Entry, Ask's files, projects, Activity, export, the agent reading terminals and the preview, MCP questions, hook trust, keys, fixes | a1b6f18, 868c287, 06c1fab, f77a6fc, 868d665, 7921c8f, 642e8d7, d3678b4, 11cbfec, ec3900e, 2b61a54, e4a6f4d, 8e6ec30, ce831b4, ef8a167, 2900576, 80fc098, 10a4f1d, ac08ed2, 0fca917, 59e1e15, f349c44 |
| `ling`: the compatibility mode (`ling --compat claude`) | 549879c, 6f425be, 09000d2, 9a018a6, 60b5799, 4b4aeff, 5bcf9d1, 56bb9bf, d55f8e1 |
| `ling`: no vendor names, `/apps` listed, Approve for me off, `ling resume`, `ling app`'s log, apply_patch's form | bf89f49, 0d7aabf, e2f13b5, 3f3584d |
| Night Shift: three attempts by default, repeating tasks, the session probe | 05fb5c8, ea5b1aa, e27595f, e06f28a |
| The loop guard; cave mode lite by default | 1edb839, abf26e2 |
| LoRA adapters | ef860cd, 2058876, 1081c7b, 7877384, 9c6b2ea |
| The local file index: OCR, office documents, e-mail, spreadsheets, presentations | 6252d81, 7a96163, e6269e5 |
| Web search and fetch | 31f8636, 0257bf5, 59a53a6 |
| Gmail service after the rename; imports never fetch plugins | 439c60a, 36a42b0 |
| Remote access (groundwork, not usable yet) | b68d1f2, 021972e, 073cd25, c3b9118 |
| Tests, quality, specs | 293090b, e0b411a, e6219fd, 09a656c, 33d4227, 898ad2f, b36c223 |

**Checklist before publishing:**
- **The suite:** `.venv/bin/python -m pytest tests/ -q` on origin/main `f93b53b`: 1,764 passed.
- **The desktop gate:** `npm test` in `desktop/electron` (101 passed) and `desktop/ui` (181 passed).
- **The Rust tests** in the build's export: `ling-launcher` 403, `ling-web-server` and `ling-chat`
  and `ling-signal` all passed; Codex's TUI suite on the patched tree 5,515 of 5,515 (2026-10-21).
- **`ling-admin codex build`** on the node, with the egress audit it runs.
- **Device checks:** the desktop app and the `ling` client against the node, from the Mac mini and
  the MSI desktop.

---

**A sandboxed command can no longer reach the host's control sockets.** `ling`'s command sandbox
mounted the system read-only, which does not stop a program from talking to a socket: a command
could reach Docker's (and, for a user in the `docker` group, act as root on the host). Every
sandboxed command now finds Docker, containerd, Podman, the D-Bus buses, the systemd user manager,
snapd and Mightling's own runtime folder closed. Full Access, which has no sandbox, is unchanged.

**The agent no longer stops after saying what it will do.** Since the model server moved to
SGLang 0.5.21 (2026-10-18), its `/v1/responses` rebuilt a conversation's history with the agent's
commentary split from the tool calls that followed it, so the model learnt to announce its work and
end the turn. A patch applied at every SGLang launch keeps them together again. It cost about 15 to
20 points on our SWE-bench runs; an updated node applies it at its next `ling-admin server start`.

**Your phone does more.** On every bridge (Signal, Telegram, Matrix): voice messages are transcribed
on the node by the local speech-to-text, said back and answered; images and files are passed on,
their text read offline; a file the agent made and named is offered back and sent only when you say
so (never at `/airgapped on`); `/night add` queues a Night Shift task (confirmed first) and the
morning report comes to the phone; answers are bounded in time and tool calls; a message after a
long silence starts a new thread, with one line saying how to go back; a quick follow-up joins the
answer in hand. On Telegram, topics are Ask threads, and an optional Mini App opens over your own
network.

**Dark mode** in the desktop app and `ling web`: System, Light or Dark in Settings › Appearance.

**The desktop app** chooses its node by name in Settings › Node (the nodes on the network, the one
in use marked). New: Quick Entry (a question from any app through a shortcut, off by default),
the files an Ask answer made shown beside it, projects (several folders, standing instructions and
knowledge files), Activity opening on what needs you, a thread exported to Markdown or a web page
(paths, addresses and tokens hidden by default), the agent reading a thread's terminal tabs and the
preview's console when it needs to, an MCP server's question shown as a form, hooks' trust in
Settings, a hunk edited in place in the review pane, and more keyboard shortcuts. Revert says that
what shell commands changed is not undone; opening a thread no longer marks it active.

**`ling --compat claude`** runs `ling` the way Claude Code works: its `CLAUDE.md` files, subagents,
settings-file hooks, permission rules, MCP servers (each approved first), custom commands and
skills, its print mode (`-p`) and its keys, all on the local model. A plain `ling` is unchanged.

**`ling` speaks only Mightling.** No screen names ChatGPT or OpenAI any more, `/apps` is listed in
the command popup, the exit line says `ling resume <id>`, and "Approve for me", which reviewed with
models no Mightling node serves, is off unless you turn it on. `ling app` writes the app's output to
`~/.mightling/log/ling-app.log` and says why when the app stops as it starts.

**Night Shift tries each task three times** and keeps the attempt its chooser picks (31 of 50 tasks
against 22 of 50 with one attempt); `ling-admin night every` repeats a task each night.

**A loop guard** caps each answer's length by where it was asked and stops a model repeating itself;
**cave mode lite** (short answers in full sentences) is the default for Ask, the app, the phone and
Night Shift.

**LoRA adapters:** `ling-admin adapter install|list|remove|verify` for adapters signed for the
served model, and `ling --adapter <name>` to use one for a session.

**The local file index** reads Word, OpenDocument, HTML, e-mail, CSV, spreadsheets and
presentations, and scanned PDFs and images through offline OCR.

**Web search** keeps answering when engines refuse this machine, with more engines by category and
`ling-search -c/-t/-l`; `ling-fetch` resolves a Google share link from a phone.

## Known issues

- **On Windows, `ling-search` keeps no answer cache**, so a repeated search goes out again rather
  than being answered from the last hour's results. The cache needs to check who owns its folder,
  which this release does on Linux and macOS only.
- **`ling --compat claude` on Windows** hands Claude Code's hooks file paths that mix `\` and `/`
  (`C:\work\src/a.rs`). Windows accepts both; a hook that compares paths as text may not match.

---

## Upgrading from 1.7.0

`ling update` on a client or a node, then `ling web start` (it restarts the server) and, if a phone bridge is on, `ling chat
start` (or `ling signal setup --refresh` for Signal). The desktop app: on Linux the release's
`.deb`, on a Mac the new dmg, on Windows the new installer.
