# Mightling 1.7.0 — release notes

**Status:** prepared 2026-10-19 on the branch `release/1.7.0` (rebased onto origin/main at `b4c353e`,
plus the version set to 1.7.0 and these notes; rebased again right before the cut if `main` moves); not merged and not tagged. The owner cuts 1.7.0 after their
try-out. Built from `git log --first-parent v1.6.4..release/1.7.0`. The text between the two rules
is the GitHub release's description: the release workflow publishes it as it stands
(`scripts/release_notes.py`), and refuses to while a `{{TBD` is left in it.

**Version.** Set to 1.7.0 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`,
`desktop/electron/package.json` and its `package-lock.json`. The release workflow stamps it into the
binaries, the desktop builds and the installers' names (`Mightling-1.7.0-<arch>-preview.dmg`,
`mightling_1.7.0_<arch>.deb`, `Mightling-Setup-1.7.0-<arch>.exe`).

**What 1.7.0 carries beyond 1.6.4** (first-parent merges on `main`):

| Area | Merges |
|---|---|
| Desktop app, Work at parity with the Codex app (DESKTOP_PARITY Phases 2 and 3) | 1087bda (the look), eca4a6a, 7973c5a, 9c5f31d, 4ca1962, 44c9b8b, 116f690, 1064dc9, b639f9b, f0477c6, 84d52b3, b10e1f0, a46aed7, 1d9eb48, 93426fd, e6eccb3, 5a217b2, a435355, 9b227b1, 0096587, fa07a02, f48e550, d4d199a, a94401f |
| Desktop fixes from the Mac, Linux and Windows tests | c2bcc7a, cb9914b, 07ab263, 7f47485, 089c15c, a5caa86, 1670d0e (the server-exited notice taken back by Start again), 230bdd9, 4affe5d, e1c1824, 7c69114, 31e93f4, b4c353e (Review after a switch, Revert), c8efb96 (e2e) |
| Desktop app on Linux: the sandbox profile for a zip or an unpacked folder | 66e2237 |
| Desktop app on Windows | 54a4578, 0f41011, 0711318, ee2cb06 |
| The Onyx web chat retired (Phase C) | dd2fb78 |
| Night Shift best-of-N | 510c273, aef148e |
| Night Shift's open-session probe (`ling apps serve` and the other daemons are not sessions) | 842819a |
| Several Signal bridges per machine | b385ab7 |
| Nodes: choosing between two, and finding one on Wi-Fi | 76527a2 (another node's answer no longer cuts the browse short), e2a73a3 (a node whose model server is stopped is no choice), 3131fb1 (the browse waits 4.5 s for the remembered or named node) |
| `ling`: `/import` hidden | 7d10096 |
| Specs reconciled with the code | 71d55d8, a5c0ad7 |

**Checklist before publishing:**
- **The suite:** `.venv/bin/python -m pytest tests/ -q` passes offline on this branch.
- **The desktop gate:** `npm test` in `desktop/electron` and `desktop/ui`, and the full `npm run e2e`.
- **The launcher's Rust tests** pass in the build's export (`cargo test --release -p ling-launcher`,
  never in `codex/`), and `windows.yml` passes for both targets (it did on `main` at `0711318`,
  run 39273226009, the Windows installer included).
- **The egress audit** (`ling-admin audit egress`, with `--app` and `--docs`) passes on the release
  build against this machine's model server.
- **The Mac preview** of the last `desktop/mac-1.7` build was tried on the Mac mini (macOS 12.3).

---

**The desktop app's Work view does what the Codex app's does.** The composer (`@` files, `$` skills,
`/` commands, a model and effort picker, Plan mode, follow-ups), the review pane (the uncommitted,
staged, commit and branch scopes; stage, unstage and revert by file or hunk; comments for the agent;
a generated commit message; commit, push and a pull-request page), worktrees with hand-off to the
local checkout and local environments, terminal tabs, threads with pins, sections and search, a
goal row, `/review`, skills, MCP servers, plugins and hooks, subagents and memories, the Scheduled
view of Night Shift, and notifications. Beside the thread: a file tree and file tabs, search files
(Ctrl+P, `name:line`), an artifacts viewer (Markdown, images, PDF, HTML without scripts), a preview
tab for pages on this machine or the local network (a site on the internet asks first, and
`/airgapped on` keeps it to this machine), and an Activity view across threads. Revert under a turn asks first, undoes the turn's file changes in
its repository and then removes the turn from the thread; if the files cannot be undone, nothing
changes. Off until you turn
them on in Settings: a pet, suggested follow-up prompts, a pull-request tab through your own `gh`,
and a read-only board of what subagents posted for each other. Another agent's configuration can be
imported (its plugins are not). On a Mac, the shortcuts use the Command key.

**The desktop app on Windows.** A preview for Windows 11 on Arm and x86-64, now with an installer
(`Mightling-Setup-1.7.0-<arch>.exe`, the note above): it installs for you alone, with no
administrator, adds a Start menu entry, and Settings → Apps removes it. It is a client of a
Mightling node on your network, with `ling.exe` inside. `ling update` and `install.ps1` update the
command-line client only; run the next release's installer to update the app.

**The Mac preview is steadier.** It no longer runs the Mac's stub `git` (whose dialog offered the
Command Line Tools in any folder; install them for the review pane and worktrees), never waits on a
keychain prompt at start, and stays responsive while macOS asks for access to a folder under
Documents, Desktop or Downloads; it quits on SIGTERM. Still signed ad hoc and not notarized: the note above
says how to open it the first time.

**The Onyx web chat is retired.** `ling web` and the desktop app have taken over its jobs, image
search and voice included. On a machine that still has its containers, `ling-admin` offers once to
remove them, and `ling-admin chat remove` does it at any time (Onyx's images are about 4.9 GB); the
deployment folder is left for you to delete.

**Night Shift can try a task more than once.** `/night add --best-of N` runs the task N times in
separate worktrees and keeps the attempt its chooser picks; one attempt stays the default.

**Several Signal bridges on one machine.** `ling signal setup --name work` links a second account,
each bridge its own system account and service; the first is `default`. The egress audit names
every bridge that is on.

**Two nodes on one network.** A client that remembers a node no longer gives it up because another
node answered first, a node whose model server is stopped is not chosen, and on Wi-Fi with power
saving the remembered node is found by browsing rather than at an address it no longer has.

**An unpacked Linux build starts.** The release's `.deb` writes the AppArmor profile Chromium's
sandbox needs; a zip or a copied `Mightling-linux-<arch>/` folder had none and stopped with
`No usable sandbox!`. Such a build now carries `install-sandbox-profile.sh` beside the binary: run it
once before the first start (docs/desktop.md). `ling-admin desktop install` writes the profile for a
checkout's build before its first run.

**`/import` is hidden in `ling`.** Its plugin step could add a marketplace hosted on GitHub, which
the egress audit does not cover.

---

## Upgrading from 1.6.4

`ling update` on a client or a node. The desktop app: on Linux the release's `.deb`, on a Mac the new
dmg, on Windows the new installer. A node that still runs the Onyx web chat is offered its removal
by `ling-admin`; nothing else changes on the node.
