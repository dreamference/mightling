# Mightling 1.6.0 — release notes

**Status:** prepared 2026-10-10 on the branch `release/1.6.0` (origin/main plus the speed figures of
`figures/1.6.0-speed` and the version set to 1.6.0 in `setup.py`, `dreamference/__init__.py`, the MCP
server's `serverInfo` and `desktop/electron/package.json`). Built from `git log v1.5.1..origin/main` and
the specs and docs it touched: the local file index (`ling-docs`), the Mac preview, the Mightling web
UI, the phone messengers (Signal, Matrix, Telegram), 12 draft tokens and the model gate, and the
SWE-bench scripts. Not yet cut: the pre-release checks that need the model (below) run when the
model gate opens after the benchmark nights; then the branch is merged and tagged. Every placeholder
is filled; the refine section states the measured result (refine stays opt-in, MIGHTLING_REFINE §3).
The text between the two rules is the GitHub release's description.

**Version.** To be set to 1.6.0 in `setup.py`, `dreamference/__init__.py`, the MCP server's
`serverInfo` and `desktop/electron/package.json` when the release is cut. The release workflow
stamps it into the binaries.

**Checklist before publishing:**
- **The new assets:** the release carries `ling-docs-aarch64-unknown-linux-gnu.gz` and
  `ling-signal-<target>.gz` for each Linux target, listed in `ling-<target>.sha256sums`, beside every
  asset 1.5.1 carried (the `puffin-*` transition copies included). The release job's
  `cargo test --release -p ling-signal -p ling-chat` passed.
- **Signed:** `SHA256SUMS` and `SHA256SUMS.sig` are attached, and `ssh-keygen -Y verify` passes
  against `ling-rs/release-signing.pub` (specs/DREAMFERENCE_RELEASE_SIGNING.md §6).
- **Update from 1.5.1** in a scratch HOME: `ling update` installs 1.6.0, `ling-docs` and
  `ling-signal`; it links `~/.local/bin/ling-docs`, and `ling-signal` sits beside `ling` with no link
  and nothing running.
- **A node install** (`install.sh --role node`, scratch HOME): the summary shows the local file
  index step done, and `~/.local/share/dreamference/mightling/lib/ling-docs/` and
  `…/models/snowflake-arctic-embed-m-v2.0-int8/` hold the pinned files.
- **`ling-admin audit egress --docs`** passes on the release build (no destination, no DNS query).
- **`ling docs status`** on a node with `~/Documents` lists `documents`, indexed, and a session's
  prompt names it.
- **`ling web` from the release build:** `/` serves the Mightling UI, not the placeholder; Ask is the
  default view; a question with an image attached is answered. `ling-admin audit egress --web` passes.
- **The messengers stay off:** after the update, `ling signal status` and `ling chat status` show
  nothing set up, `systemctl list-unit-files 'mightling-*'` shows no new unit, and
  `ling-admin audit egress` names no declared exception.
- **The messengers against real services** — none has run yet (MIGHTLING_SIGNAL §17,
  MIGHTLING_CHAT §14–§15): Signal linked to a real account (setup, QR, a question in Note to Self,
  /stop, remove) is planned for the day of the cut, once the model is free; Telegram with a real bot
  and Matrix with tuwunel, Tailscale and Element X are not. Each still untested when 1.6.0 is cut
  keeps its "preview" label, and the notes above say so.
- **The Mac preview:** dispatched with `build_clients` and `build_mac_preview` on, the release carries
  `Mightling-1.6.0-arm64-preview.dmg` and `Mightling-1.6.0-x64-preview.dmg`, both listed in
  `SHA256SUMS`, and its description starts with the "Mac desktop app (preview, unsigned)" paragraph
  the workflow writes when a `-preview.dmg` is attached. The measured Mac run (37772687306) predates
  the change that made the app's Chat entry open Ask on `ling web`, so on a Mac, if one is at hand:
  the app opens after **Open Anyway** (or `xattr -dr com.apple.quarantine`), finds the node, and both
  Ask and Work answer (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §10.2).
- **`docs/desktop.md`** still describes the app's Chat as the Onyx web chat; it should say Ask before
  this text is published.
- **The website:** once released, show the Mac card again (the HTML comment in `website/index.html`'s
  clients block) and fill the refine row of the benchmark table.

---

**Search your own documents.** Mightling now keeps a private index of your files and the agent
can search it. `~/Documents` and `~/Downloads` are indexed by default; add other folders with
`ling docs add <folder> --name <name>`, and remove any of them, the defaults included, with
`ling docs remove <name>`. In a session the agent searches with `docs_search`, reads a passage with
`docs_read`, and cites the file and the page or lines it used. From a shell: `ling docs search
"notice period acme"`, `ling docs read <id> --page 7`, `ling docs status`.

- **What it reads in this release:** PDF (text layer), Markdown, reStructuredText, Org, LaTeX and
  plain text. Scanned PDFs are recorded as needing OCR and counted in `status`; Word, HTML, mail and
  OCR come in a later release.
- **Every language.** The embedding model is multilingual (`snowflake-arctic-embed-m-v2.0`), keyword
  search covers Chinese and Japanese, and right-to-left PDFs are read in order.
- **Nothing leaves the machine.** The index opens no network connection at any step, and
  `ling-admin audit egress --docs` proves it. `/airgapped on` does not turn it off.
- **Safe to point at a Downloads folder.** Installers, archives, disk images and videos are never
  opened; secret-looking files and folders are skipped; each PDF is read in a sandbox with no
  network and a 1 GB memory cap, so a malformed or hostile file fails alone. Indexing shares the
  code index's memory budget and yields to the model server.
- **What it found on our test set:** the right passage in the first 10 results for every one of 56
  questions about 96 documents, in under 20 ms a query; 50,000 passages still answer in under
  100 ms.
- **On a GB10 node** the installer downloads what the index needs (PDFium, ONNX Runtime and the
  embedding model, about 320 MB, each checked against a pinned checksum). On a node installed
  earlier, run `ling-admin docs setup` once after updating. Linux clients receive `ling-docs` but
  not these files yet, so for now the file index is a node feature; macOS and Windows clients do
  not have it.
- **To switch it off:** `mightling_docs = false` in `dreamference.toml`, or
  `DREAMFERENCE_MIGHTLING_DOCS=0`.

**A new web UI: `ling web`.** `ling web` now serves Mightling's own UI, built into every `ling`
(until now it served a placeholder page). It opens on **Ask**, for questions that are not about a
repository, with **Work**, the agent sessions, one click away.

- **Ask threads:** "New question" starts one; each thread has a folder of its own under
  `~/.mightling/ask/` and the agent's sandbox. Threads can be searched, renamed and archived, and
  every answer has a copy button.
- **Attachments:** the clip button, or paste a screenshot. Images go to the model (Qwen3.8 reads
  them); other files are placed in the thread's folder for the agent to open. Up to 20 MB an image
  and 100 MB a file.
- **On a phone:** below 720 px the thread list becomes a drawer, and the page fits without zooming
  or scrolling sideways. Sign a phone in with `ling web pair`, which prints an 8-digit code (one use,
  ten minutes).
- **Signing in:** `ling web open` opens a one-time link on this machine. Nothing a command run by the
  agent can read is a credential.
- **On a node shared with your network,** `ling-admin node enable` now starts `ling web` on the LAN
  (port 3100) and advertises it; `ling-admin node enable --no-web` keeps it on loopback.
- **The desktop app's Chat is now Ask.** On Linux and macOS, the app's Chat entry (menu label
  **Ask**) loads this UI from `ling web` on the same machine, starting `ling web serve` itself when
  none is running and stopping it on quit. On a client, Ask runs on the client against the node's
  model server. `ling app` opens it (`--ask`; `--chat` still works). On Windows, which has no
  `ling web` yet, Ask opens Work.
- **The previous web chat is still there.** It keeps running on port 3000 and is managed with
  `ling-admin chat …` as before. The image gallery, voice input and the Apps settings page are not
  in the new UI yet.

**Ask from your phone: Signal, Matrix and Telegram** *(preview, off by default)*. A message you send
becomes an Ask thread on the node, and the answer comes back in the same chat. Every messenger is
**off** until you run its setup on the node: installing or updating Mightling turns none of them on,
starts nothing and opens no port. All three need `ling web` running on the node (`ling web start`).

| Messenger | Privacy | Turn it on | Turn it off |
|---|---|---|---|
| **Signal** | End-to-end encrypted; your own account, in Note to Self | `ling signal setup` | `ling signal remove` |
| **Matrix** | Your own homeserver on the node, reached over your tailnet | `ling-admin matrix start`, then `ling chat start` | `ling chat stop`, `ling-admin matrix stop` |
| **Telegram** | Less private: messages pass through Telegram's servers unencrypted | `ling chat telegram setup`, then `ling chat start` | `ling chat telegram off` |

- **Signal** links the bridge to your own account as one more device, the way Signal Desktop is. You
  write in **Note to Self**; every other conversation is dropped unread. The bridge runs as a system
  account of its own (`mightling-signal`), so the agent can never read your Signal keys; signal-cli and
  its Java runtime are downloaded at setup, pinned by SHA-256. `ling signal setup --dry-run` prints
  every change first. A linked device holds keys to your whole account: read the Signal section of
  the messengers guide before linking.
- **Matrix** runs a private homeserver on the node, on a Docker network with no route out, and your
  phone reaches it through Tailscale (which you install and sign in to yourself). Use Element X.
- **Telegram** shows a warning and asks you to type `yes`: answers can carry what the agent read for
  you, such as mail, files and code, through Telegram's servers.
- **The air gap applies.** At `/airgapped on`, Signal sends nothing, not even a read receipt, and
  Telegram pauses; Matrix keeps working, because its homeserver never leaves the node.
- **The egress audit says so.** While a bridge is on, every `ling-admin audit egress` report names it
  as a declared exception.
- **Why preview:** the Matrix and Telegram bridge is tested against stand-ins of Telegram's Bot API
  and a Matrix homeserver, end to end through a real `ling web`; the Signal bridge is tested against
  a scripted signal-cli and a stand-in `ling web`; none has yet been exercised against a real Signal
  account, a real Telegram bot or a real homeserver with Element X, which is why each keeps its
  preview label. Full guide: the Phone messengers page of the documentation.

**Refine mode stays opt-in.** `ling --refine` (also `ling exec --refine`, `[night] refine = true`, or
`mightling_refine = true` in `dreamference.toml`) first studies the task in a session that changes
nothing, then does it in a fresh one. On a fixed 100-task sample of SWE-bench Verified, run on one
DGX Spark, it resolved 69/100 against 68/100 without it (the two arms each won tasks the other
lost: 10 against 9, McNemar p = 1.0), at about 2.2 times the time per task (a median of 13.9
minutes against 6.5), so it remains off by default. It can still help on tasks whose description is
thin or misleading.

**Measured: 68 of 100 SWE-bench Verified tasks.** With the default configuration, 1.5.1's agent and
Qwen3.8-27B resolved 68 of a fixed 100-task sample of SWE-bench Verified on one DGX Spark, offline,
one attempt per task. That corresponds to an estimated 69% on all 500 tasks (95% band 63.9–73.4%),
estimated from the per-task results of every public submission (`scripts/swe_bench_compare.py`).
It is our own measurement, not a leaderboard entry. The same sample with refine mode on resolved 69,
one more, at 2.2 times the time (above).

**The installer** installs the two new programs on Linux: `ling-docs`, linked onto your PATH, and
`ling-signal`, placed beside `ling` with no link and not started. On a GB10 node it also runs
`ling-admin docs setup` (skipped with `--no-model`). The unattended node install is otherwise as in
1.5.1.

**Mightling for macOS, as a preview.** The release now carries the desktop app for Macs:
`Mightling-1.6.0-arm64-preview.dmg` for Apple silicon and `Mightling-1.6.0-x64-preview.dmg` for
Intel, for macOS 12 or later. On a Mac the app is a client of your GB10: Ask and Work both run the
`ling` bundled in the app against the node's model. It finds the node on your network by itself, as
`ling` does (run `ling-admin node enable` on the GB10 first). The file index is not available on a
Mac.

**It is not signed with an Apple Developer ID and not notarized** (signed ad hoc only), so macOS
will not open it the first time. Check the dmg against the release's signed `SHA256SUMS`, drag
Mightling to Applications, then:
- try to open it once, and click **Open Anyway** in **System Settings → Privacy & Security**;
- on macOS 14 and earlier you can instead Control-click (or right-click) the app, choose **Open**,
  and confirm;
- or, in Terminal: `xattr -dr com.apple.quarantine /Applications/Mightling.app`.

Allow it when macOS asks whether Mightling may find devices on your local network; that is how it
reaches the GB10. The preview does not update itself, and `ling app` in a Mac terminal does not open
it yet. Full steps: the Desktop app page of the documentation.

**Faster decoding: 12 draft tokens.** The DFlash2 drafter now proposes 12 tokens per step instead
of 16. Measured on replayed agent sessions: +7% single-stream decode (48.3 against 45.1 tokens/s,
acceptance 5.23 against 4.99 per step) and neutral with two agents at once; the output is unchanged,
because speculative decoding preserves the model's distribution.

**A benchmark run now has priority over the model server.** `ling-admin server start` puts a small
gate container in front of the engine on the public port. While a SWE-bench run holds the gate, a
request that is not the run's is answered 503, naming the run and its time left, instead of slowing
it down; `ling-admin night pause [--for 2h]` lets you through meanwhile. With no run, nothing changes.

**Fixed:**
- **The code index's memory budget saw no running index.** Since the rename the code index looked
  for its running scopes under a folder that does not exist, so a new index run was admitted without
  counting the ones already running. Index runs again count each other, and the file index's,
  against one budget.
- **The Puffin-to-Mightling move** runs only for an installed Mightling, never from a source tree or
  a development build.

## Upgrading from 1.5.1

1. **Update:** `ling update` on every machine (or the one-line installer again). Update the desktop
   app on your other computers at the same time.
2. **On a GB10 node, once:** `ling-admin docs setup`, which downloads what the file index loads
   (about 320 MB). Until then, `ling docs status` names what is not installed and says to run it.
   The installer does this step for new node installs.
3. **`~/Documents` and `~/Downloads` are indexed by default** on a node from the first session on.
   To keep them out: `ling docs remove documents` / `ling docs remove downloads`, or switch the index
   off entirely with `mightling_docs = false` in `dreamference.toml`.
4. **To reach the new web UI from your phone or other computers,** run `ling-admin node enable` again
   on the node: it then starts `ling web` on the LAN (port 3100) and advertises it. On the node
   itself, `ling web open` is enough.
5. **The desktop app's Chat opens Ask** (the new UI) instead of the previous web chat. The previous
   web chat still runs at `http://localhost:3000` and is managed with `ling-admin chat …`.
6. **Nothing else turns on by itself.** The Signal, Matrix and Telegram bridges stay off until you run
   their setup; your settings, sessions and paired machines are unchanged.

---
