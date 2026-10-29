# Mightling 1.7.2 — release notes

**Status:** published 2026-10-28 as `v1.7.2`, the current release (tag on `f94f712`; this line updated 2026-10-29). Prepared 2026-10-28 on the branch `release/1.7.2`: origin/main at `989e3e98`, plus the
version set to 1.7.2 and the rest of these notes, cut through the release workflow from that branch
(nothing merged into main after `989e3e98` is in it). Started 2026-10-26 on the branch
`models/ling-engine-server` with the model server's section. Built from the merges in
`v1.7.1..release/1.7.2`. The text between the two rules is the GitHub release's description: the
release workflow publishes it as it stands (`scripts/release_notes.py`).

**Version.** Set to 1.7.2 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`,
`desktop/electron/package.json` and its `package-lock.json`, and, new since 1.7.1, the delegation
plugin's manifest and marketplace entry and `ling delegate mcp`'s `serverInfo`
(`ling-rs/delegate/src/mcp.rs`).

**Checked before the cut (2026-10-28):** the release dry run 40651273143 passed on `989e3e98`
(every build and test the release runs, the clients and the Mac dmgs included); `windows.yml` green
on that tree; no messenger blocking marker; the two ling-serve images that carried a home path
answer 404 on the registry, and the pinned `7bff4e37` answers 200.

**The engine.** ling-engine 8d112d0d25393bca95e3e5c9227d0135eba6f2eb (branch
`release/1.7.2-engine`, 2026-10-27): bdda3a2 plus, alone, main's tokenizer fix (15e82b6: the BPE
cache under a shared mutex; concurrent requests could crash ling-serve with SIGSEGV in
`Tokenizer::bpe`; token ids unchanged, `ling-tokenizer-threads-tests`). bdda3a2 is b8e9451, the
commit SWE-bench nights 12 and 15 tested, plus the parse-only tool-call fix of branch
`fix/toolcall-text` (the owner's rule of 2026-10-20: a later commit only if the engine's checks show
it bit-exact); its binary (sha256 598e5520…9ef41f1) is greedy-identical to night 15's on agentlong
(16 of 16 turns, every streamed event) and passes `--spec-check`. The image's binary, sha256
8e517c880d6139e2cc7f9de8075e7f6a2b85638334d2be947904767b7d81b2b3, is 8d112d0 rebuilt under
neutral paths (the first build, 7c9098f1…, carried the build machine's home path, as bdda3a2's
image did); its device code is the first build's, and on node-b on 2026-10-27 it was
greedy-identical to the first build on agentlong (16 of 16 turns identical, the same 5,271 tokens in the same 781 verify passes) and passed `tests/server` and a
smoke (a tool call, 2 and 4 concurrent requests), as the first build had. Night 17 (2026-10-26 to 27) compares b8e9451 with SGLang carrying the
phase-merge fix.

**Before publishing:**
- **The image** `ghcr.io/dreamference/ling-serve:8d112d0d2539` (`scripts/build_ling_serve_image.sh`,
  whose anonymity check found nothing of the build machine in it), pushed on 2026-10-27 and pinned
  in `QWEN38_LING_IMAGE` as `sha256:7bff4e37e731893a6248b0632897a2740e1842e0b19b28a68f0eee22d670caf8` (an anonymous pull of the digest answers 200). It
  replaces the first 8d112d0 image (`sha256:9428996a…`) and bdda3a2's (`sha256:87807c50…`), both
  deleted from the registry because their binaries carried a home path.
- **Long context** (the nights ran 65,536), done on node-b on 2026-10-26 with the bdda3a2 image:
  started at 262,144 in 40 s (host memory +60 GB, 46 GB left available), both needles of a
  204,857-token prompt answered exactly, prefill ~850-900 tokens/s at that length, decode 39.6
  tokens/s there (74.9 at short context), `ling-run --spec-check` on the long prompt passed (tokens
  and state identical).
- **The drafter:** ScaleSearch (merge ccff3b6), `dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch`,
  hosted as assets of the `models-2026-10-27` pre-release and pinned by SHA-256, on SGLang and
  ling-serve alike.
- **The live test on the node:** switch to ling-serve (`server stop`, `server start --engine ling`),
  `/v1/models`, a `ling exec` task, a phone bridge, the desktop app and the egress audit, then back
  to SGLang (not recorded here before the cut).
- **The suite**, the desktop gate, the Rust tests and `ling-admin codex build` as for 1.7.1.

---

**Answers stream again with SGLang.** Since SGLang 0.5.21, an answer to a request that offered
tools (every `ling` turn, the desktop app, `ling web` and the phone bridges) arrived whole at the end
instead of word by word. A second patch applied at every SGLang launch lets the text stream as it is
written; the model's output does not change. A node applies it at its next `ling-admin server start`.

**A new drafter for the default model.** Qwen3.8-27B's DFlash2 drafter is now Dreamference's NVFP4
quantization with searched block scales (ScaleSearch), on SGLang and on ling-serve alike. It is
downloaded from Mightling's own GitHub release assets and checked by SHA-256; a node fetches it at
its next `ling-admin server start`. Every model download now takes the revision the model registry
pins, for the model and its drafter.

**A second engine for the model, opt-in: ling-serve.** The default model, Qwen3.8-27B with the
DFlash2 drafter, can now also be served by `ling-serve`, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
SGLang stays the default. To try it:

```bash
ling-admin server stop && ling-admin server start --engine ling
```

or `server_engine = "ling"` in `~/.config/dreamference/config.toml` to keep it; remove that line (or
`server start --engine sglang`) to go back. If ling-serve fails one of `server start`'s checks,
`server start` says so and prints the way back; it never switches by itself.

It serves the same checkpoint, drafter and 262,144-token context from its own image (under 1 GB,
against SGLang's 34 GB), inside the same container limits and memory-safety checks. Its speculative
decoding is exact: the drafter changes the speed, never the output. Measured on a GB10: ready in
40 s; about 60 GB of memory in use while serving; a code answer at 74.9 tokens per second; a
204,857-token prompt with two facts hidden in it answered both exactly, with decode at 39.6 tokens
per second at that length.

Its limits, which are why it is not the default yet:

- **No images**: an image reaches the model as the text `[image]` (`ling` and the phone bridges
  know, and send text).
- **No LoRA adapters**, and no API key.
- **Two requests at once**; a third waits.
- **Changing the end of a long prompt reads it again in full**: near 200K tokens that is nearly four
  minutes (~850-900 tokens per second).

It becomes the default once it reads images, serves adapters and runs more than two requests at
once.

ling-engine (opt-in): fixed a crash under concurrent requests (tokenizer cache). Its image is
`ghcr.io/dreamference/ling-serve@sha256:7bff4e37e731893a6248b0632897a2740e1842e0b19b28a68f0eee22d670caf8`;
the two earlier images, whose binaries carried a path of the machine that built them, are deleted
from the registry.

**Telegram's Mini App works on your repositories.** A phone thread can switch to a repository on
the node's forge and work in it as Work mode does: a repository picker, a checkout of its own for
each thread, the diff, a git bar where every write takes a fresh one-time confirmation, a Waiting
for you strip with once-only approval choices, and a read-only view of a command's output with
search and copy. A new phone is paired from the bot's own button when it first opens the Mini App,
and a link into a thread opens only after that check. It ships with no hidden switch: it is there
whenever the Telegram bridge is set up and `[chat] telegram_mini_app_url` is configured.

**Phones.** On Telegram with topics on, a message outside any topic continues the topic used last,
and `/new` opens a new one. Signal answers stream in by edits, with a status line at once in Note to
Self. The phone takes the most likely reading of a question and says it in one line instead of
asking back, asking first only before something destructive or costly; phone threads ask for no
approvals, and dated facts go to `ling-search` first.

**A forge on every node.** `ling-admin node enable` sets up Forgejo on the node (`--no-forge` skips
it): sign-in required, new repositories private, the first node set up holding the forge of record
and the others mirroring it, daily backups, and `ling-admin forge move` to hand the record to another node. In
`ling web`, Code beside Ask and Work opens it, already signed in, for paired control devices.

**The desktop app** gains a side chat (Ctrl+Alt+S), `/init` writing a short `AGENTS.md`, edited
messages that keep every version sent, two-line summaries of idle chats, an Ask question continued
in a project as a Work thread, threads deleted for good (after a confirmation naming them) and
several chosen at once for Archive, Move and Delete, terminals as tabs, CSV and HTML tabs, office
files previewed as PDFs through an installed LibreOffice, Mermaid diagrams drawn in answers, tool
calls shown in full, the agent's plan as a checklist while it works, its questions answered in full,
`/review`'s findings on their lines, pull requests with their comments and reviews, a command's
prefix or a host always allowed from its approval, a warning when another thread works in the same
checkout, Settings › Memories, dictation (Ctrl+Shift+V) with a word list, a first-run page, a
readable interface font (Atkinson Hyperlegible), and File and Help menus with a tray that counts
the threads that wait. MCP servers: added with their environment, folder and headers (a token only
by its variable's name), recommended open-source servers (none offered at `/airgapped on`), `.mcpb`
bundles installed from a file, and each tool allowed, asked or off. MCP servers' own instructions
now reach the model.

**On Windows** the desktop app is sharp at every display scaling, its notifications show labelled
Mightling and its window groups with its taskbar pin, and the title bar leaves the window controls
their room. The installer and uninstaller close a running app and replace its folder whole, never
leaving a mix of two versions. The app's data moves once to
`%LOCALAPPDATA%\dev.dreamference.mightling`. `ling`'s sandbox works over SSH and on a PC with
several users, worktree sessions work, `ling doctor` runs, and `install.ps1` keeps one mDNS firewall
rule per program.

**Night Shift** repeats a task on chosen weekdays, every N nights or on a day of the month
(`ling-admin night every --days`, `--every`, `--monthly`). Each finished run stays unread in the
desktop app's Scheduled inbox until opened, `ling-admin night archive` moves old runs away, and a
task queued from a Work thread shows its morning result in that thread, with Apply.

**Install, update, uninstall.** The one-line installers install the desktop app too (`--no-app`,
`-NoApp` to skip it), and `ling update` updates the app they installed under the same signed checks.
`ling uninstall` removes a release install of the client on Linux, macOS and Windows, keeping the
desktop app while it is there.

**Sync, phase 2:** live turns followed and answered from any paired device, off until
`ling sync enable`.

**Also:** a repository thread's or a phone bridge's turn can no longer choose a permissions profile
or add folders past its sandbox; the desktop app's `!` command runs in your shell at `/airgapped off`
and is refused at `on`; the local file index no longer embeds everything again after an upgrade;
`ling-admin node add` takes the user from your SSH config and names the user it tried when a login
is refused.

## Known issues

**Windows: https from inside the sandbox (a known limit).** On Windows, `curl.exe` and PowerShell's
`Invoke-WebRequest` cannot reach an https address from inside `ling`'s sandbox: Windows' own TLS
(Schannel) refuses the sandbox's restricted token, whatever the sandbox grants it, and nothing in
Mightling can change that. Use `ling-fetch` for pages and files; `ling` tells the model so, and
Python, Node and git work too, since they bring their own TLS (sessions set git to OpenSSL, so
`git clone` and `git fetch` over https work in the sandbox). Full Access sessions run outside the
sandbox and are not affected.

**Windows: compatibility mode is not available yet (a known limit).** Compatibility mode
(`ling --compat claude`) is not available on Windows yet; it refuses to start rather than run
without its permission rules, and says what turned it on and how to turn it off (`ling compat use off`
for the setting in your config file).

---

## Upgrading from 1.7.1

`ling update` on a client or a node (it updates the desktop app too when the one-line installer put
it there). On a node, `ling-admin server stop && ling-admin server start` then restarts SGLang with
the streaming fix and fetches the new drafter; then `ling web start` (it restarts the server) and,
if a phone bridge is on, `ling chat start` (or `ling signal setup --refresh` for Signal). The
desktop app otherwise: on Linux the release's `.deb`, on a Mac the new dmg, on Windows the new
installer. ling-serve stays opt-in: the node pulls its image (under 1 GB) at the first
`ling-admin server start --engine ling`.
