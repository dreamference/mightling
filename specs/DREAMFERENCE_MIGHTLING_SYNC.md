# Mightling Sync: one set of threads on every device, served by the user's own node

**Status:** proposed (2026-10-22), nothing built; **the owner's answers to §0.1 given the same day** and folded in (scope matches Codex's, §1.1; every paired device has full control by default). The facts about Codex's storage in §2 were read from the pinned source (`rust-v0.158.0`, `codex-rs/rollout/`, `codex-rs/state/`) and from a node's `~/.mightling` on 2026-10-22; anything not checked is marked **unverified** and is a Phase 0 measurement (§12).

**Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`, `ling web` on port 3100).

**Builds on:**
- [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md): `ling web`, its credentials and device pairing (§4.3, §18.2), Ask threads, and `policy.json` with its two enforcers;
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md): the client and node halves (§3) and what works from a remote client (§10);
- [MIGHTLING_REMOTE_ACCESS](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md): the overlay, the node's name-constrained CA, and the untrusted box that stores nothing;
- [MIGHTLING_DESKTOP](./DREAMFERENCE_MIGHTLING_DESKTOP.md) §6.9 (handoff and shared history) and the desktop parity specs;
- [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md), [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md) and [MIGHTLING_TELEGRAM_MOONSHOT](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md): the phone bridges and the Mini App;
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) and [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md).

**What prompted it.** A draft paper on the desktop app lists, among the frictions a Codex user meets in Mightling: "the commercial app provides automated real-time state synchronization between desktop, browser and mobile applications via vendor cloud accounts. Mightling confines session databases (`sqlite_home`) strictly to the local disk. Remote mobile interaction is possible only through self-hosted daemon bridges, which require explicit user configuration." The owner asked for a self-hosted server that closes this gap. Every Mightling user already runs one always-on machine of their own, the node with the model on it, so the server needs no new machine and no account anywhere.

---

## 0. Decisions, stated first

1. **The node is the sync hub, inside `ling web`.** The primary node's `ling web` gains a sync service under `/api/sync/`. `ling web` already runs there, already pairs devices with a one-time code and a device credential, already serves the phone's browser and the Telegram Mini App, and already enforces `policy.json`. A second server process would duplicate all of that. With several nodes, one is the hub (the primary, `ling node list`); the others are devices like a laptop.
2. **The scope is Codex's (owner, 2026-10-22).** Interactive threads, reachable live from every paired device, while files, credentials, settings, skills and memories stay on the machine where they live, as Codex does across its surfaces (§1.1). Anything Codex only offers through a vendor account or cloud (its cloud tasks, sign-in) has no counterpart here beyond what the node already runs.
3. **Rollouts are the source of truth; databases never travel.** A thread is its rollout file, Codex's append-only JSONL under `sessions/`. The SQLite files in `~/.mightling` (`state_5`, `thread_history_1`, `logs_2`, `goals_1`, `queue_1`, `memories_1`) are either projections that Codex rebuilds from rollouts or machine-local state; copying a live WAL database between machines corrupts it. Sync moves rollouts and a few small append-only files (§4) to the hub, which serves them to every paired device; copying them into another computer's own `~/.mightling` is an opt-in beyond Codex (`[sync] replicate`, §4).
4. **One writer per thread: the device that holds the code.** A turn runs where the thread's working tree is, because the agent's commands, sandbox and git repository are there (NODE §3). Every other device sees the thread live and, by default, may send a message, answer an approval, stop the turn, change the model or start a new thread on that device (§6.3). The hub forwards these to the owning device, whose own `policy.json` enforcement vets them as if typed there. Moving a thread to another device is an explicit **hand-over** (§6.4), never automatic.
5. **Off until the user turns it on, per device.** `ling sync enable` on the node and on each device; nothing changes for a user who never runs it: no port, no unit, no file, the egress audit's verdict as today. `ling sync disable` stops it and keeps the local data.
6. **Only the user's own machines.** Sync runs on the trusted LAN or over the remote-access overlay, to the node and nowhere else. The rented box of REMOTE_ACCESS relays ciphertext and stores nothing, as now. No vendor cloud, no account, no telemetry.
7. **Phone notifications go through what the user already set up.** A finished turn, a waiting approval or a Night Shift result reaches the phone through the bridge the user has on (Signal Note to Self, Telegram, Matrix), as a short notice with a link. Web Push is offered only as an explicit opt-in with a content-free payload (§7.4), because even an encrypted push passes the browser vendor's push service.
8. **The phone gets a real app surface without an app store.** `ling web`'s mobile layout becomes an installable web app (a PWA) over the overlay, where the node's CA gives it a secure context. On the bare LAN, without TLS, it stays the text-only page decided on 2026-10-07 (ASK §4.4). The Telegram Mini App stays as built.

### 0.1 The owner's answers (2026-10-22)

| # | Question | Answer |
|---|---|---|
| O1 | Sync inside `ling web` (decision 1), or a separate `ling sync serve` process on the node? | **Inside `ling web`**, as proposed |
| O2 | Which threads sync? | **Interactive threads (TUI, app, web, bridges), and otherwise what Codex does** (§1.1): every thread reachable live from every paired device; `ling exec` sessions are not Codex threads a user browses and stay out (`[sync] exec = true` turns them on); copying transcripts into other computers' own stores is opt-in (`[sync] replicate`) |
| O3 | Codex's memories, the user's skills and custom prompts, settings: sync them? | **Follows from O2: no.** Codex keeps them per machine (§1.1), so they stay on each device |
| O4 | Pairing scopes: may a paired phone control turns, or only watch? | **Every paired device, phones included, gets full control by default** (send, approve, stop, change the model, start a thread); changeable per device (`ling web devices --scope <device> watch`) |
| O5 | Hand-over with code that is not pushed: carry a git bundle through the node? | **Opt-in**, per hand-over, as proposed; refused at `/airgapped on` on either end |
| O6 | Web Push (§7.4)? | **Opt-in**, built and off by default, as proposed |

---

## 1. What the user expects (the vendor's app, from public material)

The upstream vendor's desktop app, its CLI and its web and mobile surfaces share a user's threads through the vendor's cloud account: start a task on the desktop, watch or continue it in the browser or on the phone, get a notification when it needs you. The paper describes this; the details of the vendor's mechanism are not needed here and were not studied (clean room: public behaviour only). What matters is the user-visible contract:

- **One list of threads** wherever the user looks, with titles, archive state and pins in agreement.
- **Live progress** on a second device while a turn runs, and the means to answer it.
- **Continue elsewhere** where that makes sense.
- **A phone surface** with notifications.

Mightling can meet all four without a cloud, because the user's node is always on and is already where the model runs.

### 1.1 What Codex shares, and the default scope that follows (read 2026-10-22, public material only)

| Surface or state | What Codex does | Mightling's default |
|---|---|---|
| **CLI, IDE extension and desktop app on one machine** | Share one home (`~/.codex`): `config.toml`, sessions, skills, rules and memories. The vendor's docs: "The CLI and IDE extension share the same config" ([Config basics](https://learn.chatgpt.com/docs/config-file/config-basic)) | Already the same: `ling`, `ling web` and the app share `~/.mightling` (DESKTOP §6.9) |
| **Local threads on a second computer** | Not shared. Threads and projects stay tied to the machine they ran on; cross-device sync of local chats is an open feature request ([openai/codex#21803](https://github.com/openai/codex/issues/21803), [#14067](https://github.com/openai/codex/discussions/14067)) | Reachable live from the other computer through the hub, not copied into its store; `[sync] replicate = true` copies them (beyond Codex) |
| **The phone (preview since 2026-05-14)** | Connects to the user's machines where Codex runs and "loads the live state from that environment": all threads, approvals, plugins and project context; review output, approve commands, change models, start new threads. "Your files, credentials, permissions, and local setup stay on the machine where Codex is operating", with updates in real time through a vendor relay that "keeps active session state and context synced anywhere you're signed in" ([Work with Codex from anywhere](https://openai.com/index/work-with-codex-from-anywhere/)) | The same contract through the user's own node: the hub is the relay, holds the threads' transcripts, and forwards control to the machine that owns each thread (§6). No vendor account |
| **Remote SSH environments** | The desktop app runs projects and threads inside remote hosts from the user's SSH configuration, reachable from the phone through the same relay (same page) | Out of scope here; nodes and jobs over SSH are NODE §13 |
| **Cloud tasks** | Run on the vendor's computers; started and continued from desktop, web and mobile ([Using Codex Cloud](https://help.openai.com/en/articles/20001545-using-codex-cloud)) | No cloud. The counterpart is Night Shift on the user's node, whose results the hub lists on every device (§4) |
| **Settings, skills, memories** | Per machine, in `~/.codex`; nothing found in the vendor's docs that syncs them between computers | Per device, never synced (O3) |
| **Sign-in** | A ChatGPT account on every surface | Device pairing with the node (ASK §4.3); no account |

The default scope is therefore: interactive threads, live and controllable from every paired device, with the transcripts on the hub so that a device can read them while their owner is off; settings, skills, memories and code stay where they are.

---

## 2. Where Mightling's state lives today (verified 2026-10-22)

| State | Where | Format | Synced today |
|---|---|---|---|
| Thread transcripts | `~/.mightling/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`, archived ones under `archived_sessions/` (`SESSIONS_SUBDIR`, `ARCHIVED_SESSIONS_SUBDIR` in `codex-rs/rollout/src/lib.rs`) | Append-only JSONL. A compression pass may replace a finished file by `<name>.jsonl.zst` (`rollout/src/compression.rs`) | No |
| Thread names | `~/.mightling/session_index.jsonl` (`rollout/src/session_index.rs`: `append_thread_name`, `remove_thread_name_entries`) | Append-only JSONL | No |
| Thread list, search and history projections | `state_5.sqlite`, `thread_history_1.sqlite` (tables `thread_items`, `thread_turns`, `thread_history_projection_state`) | SQLite in WAL mode, rebuilt from rollouts | No |
| One writer per thread | `~/.mightling/thread-writer-locks/` | Lock files | — |
| Logs, goals, the queue, memories | `logs_2`, `goals_1`, `queue_1`, `memories_1.sqlite` | SQLite | No |
| Ask threads' scratch folders | `~/.mightling/ask/<id>/` | Files | No |
| Phone chat state | `~/.mightling/chat/threads.json` on the node (CHAT §4) | JSON, replaced whole | Lives on the node already |
| Night Shift queue, results and report | The node (NIGHT_SHIFT) | Files | Lives on the node already |
| Desktop projects, pins, window state | The Electron app's user-data folder on each machine | JSON | No |
| The code | The user's working trees, on whichever machine | Git | Never by this spec, except hand-over (§6.4) |

**Size, measured on the development node on 2026-10-22:** 178 rollouts in 20 MB; the last seven days added 11 files and 2.8 MB. SWE-bench and Night Shift sessions run under their own homes and are not in this store. Sync volume is small; the design optimises for latency and correctness, not bandwidth.

**The gap, precisely.** A laptop or a desktop client runs its own `ling app-server` against the node's model (NODE §10, ASK §18.6), so its threads live in the client's `~/.mightling`. The node's own threads (its TUI, `ling web`, the phone bridges, Night Shift) live in the node's. Each surface sees only its own machine's threads, and the phone sees only the node's.

---

## 3. Architecture

```
 laptop                          node (hub)                          phone
 ┌───────────────────────┐       ┌────────────────────────────┐      ┌──────────────────┐
 │ ling app / ling TUI   │       │ ling web :3100             │      │ PWA (ling web)   │
 │ app-server            │       │  /api/sync/*  hub store    │◄────►│ Telegram Mini App│
 │ ~/.mightling          │       │  /ws live fan-out          │      │ Signal/Telegram  │
 │ sync agent ───────────┼──────►│  policy.json               │      │ notices          │
 └───────────────────────┘ LAN   │ the node's own sync agent  │      └──────────────────┘
                           or    │ ~/.mightling (node's own)  │
                           overlay└────────────────────────────┘
```

- **The hub** is a module of `ling web` (`ling-rs/web`): a store under `~/.mightling/sync/hub/` (0700), the HTTP routes of §5, and a WebSocket fan-out for live events (§6). It never runs a turn and never opens a working tree.
- **The sync agent** runs on every enabled device, the node included, because the node is also a device with its own threads. On a machine with the desktop app it runs inside the app's main process while the app is open, and as the user unit `mightling-sync.service` (`ling sync agent`) otherwise, so TUI-only machines and closed apps still sync. It watches `~/.mightling` (inotify, FSEvents, ReadDirectoryChangesW; a 30-second rescan as a fallback), uploads what its device owns and downloads what other devices own.
- **Code:** Rust, in `ling-rs` (the agent is a launcher subcommand, `ling sync …`) and `ling-rs/web` (the hub). It reads and writes files Codex owns, in Codex's own formats, so it needs **no Codex patch**.

---

## 4. What syncs, and what never does

| Item | Syncs | How |
|---|---|---|
| Rollouts of interactive threads (TUI, desktop Work and Ask, `ling web`, the phone bridges' threads) | Yes, to the hub, served to every paired device | Append-only by offset (§5.2) |
| The same rollouts copied into another computer's own `~/.mightling` | No by default: Codex does not (§1.1) | `[sync] replicate = true` on that computer (§5.2) |
| Archive state | Yes | A metadata event; each device moves the file between `sessions/` and `archived_sessions/` itself |
| Thread names | Yes | `session_index.jsonl` entries, append-only, with the device and a hybrid logical clock |
| Desktop pins, unread marks, the project list (path, display name, per device) | Yes, as the thread list's metadata | Small documents, last writer wins per field (§8) |
| Settings (`config.toml`), skills, rules, custom prompts, memories | **No**: per machine, as in Codex (O3, §1.1) | |
| Night Shift results and inbox | Already on the node; the hub indexes them so every device lists them | Read from the node's files |
| `ling exec` rollouts | No by default (O2): not threads a user browses | `[sync] exec = true` |
| Ask scratch folders (`ask/<id>/`), attachments over 20 MB | No | A synced thread shows "attachment kept on <device>" |
| The SQLite databases | **Never** | Each device projects its own from rollouts (§2) |
| `config.toml`, the model catalog, credentials, the web token, the Gmail and Telegram tokens, pairing keys | **Never** | Machine-local by design |
| The `ling-docs` index and `ling-code` indexes | **Never** | ASK §5.3's promise: the documents index never leaves its machine |
| Working trees | **Never**, except an explicit hand-over (§6.4, O5) | |

**Per project, per thread:** a project marked "this device only" in the app (or `[sync] exclude = ["~/work/secret-client"]`) never leaves the device, and its threads are listed on other devices by nothing at all, not even a title.

---

## 5. Data model and protocol

### 5.1 Identity

- **Devices.** Each device has an id (the node id for nodes; a random 128-bit id for others) and a display name. Pairing (ASK §4.3, the same 8-digit code and QR code) gives a non-browser agent a device secret of 256 bits, stored at `~/.mightling/sync/device.toml` (0600). `ling web devices` lists sync agents beside browsers; `ling web revoke <device>` ends both.
- **Scopes** (O4): `control` (read and follow threads; send, approve, stop, change the model, start a thread, hand over) is the default for **every** paired device, phones included. `watch` (read and follow only) is a per-device restriction: `ling web devices --scope <device> watch`, or Settings › Devices. Owner-device uploads need neither: a device may always publish its own threads.
- **Threads** keep Codex's thread id (the UUID in the rollout's name). The hub records the **owner device** (the device whose rollout it is) and a **repository identity** for the working tree: the root commit's hash and the hash of the `origin` URL, never the path or the URL itself.

### 5.2 Rollouts: append-only by offset

- **The hub's copy** of a rollout is its uncompressed bytes, kept in 1 MiB chunks, each with a SHA-256 and a chain hash over the chunks before it, so an offset plus its chain hash names a prefix exactly.
- **Upload** from the owner: `PUT /api/sync/rollouts/<thread>?offset=<n>&prefix=<chain hash>` with the new bytes. The hub accepts only if `offset` equals its length and the prefix hash matches; otherwise it answers with its length and hash, and the agent resumes from there.
- **Compression.** When Codex replaces a finished `.jsonl` by `.jsonl.zst`, the agent reads through the decompressed stream; the logical bytes, and so the offsets, are unchanged. A downloaded rollout is written as plain `.jsonl`, and the receiving Codex compresses it on its own schedule. (**Unverified:** that Codex's compression never changes content; Phase 0 checks it on real files.)
- **Reading** from other devices: `GET /api/sync/rollouts/<thread>?offset=<n>`. By default a device reads a thread it does not own from the hub on open, renders it, and writes nothing to its own `~/.mightling`, as Codex's phone reads the live state of the machine (§1.1). With `[sync] replicate = true` it also downloads: a new thread is written to `sessions/YYYY/MM/DD/<the owner's file name>` through a temporary file and a rename; growth is appended. The agent never writes a thread for which the local Codex holds a writer lock (`thread-writer-locks/`), and never a thread its own device owns.
- **The manifest.** `GET /api/sync/manifest?since=<cursor>` lists every change after a cursor: rollout lengths, names, archive state, metadata, tombstones. The cursor is the hub's own sequence number, never a device clock.

### 5.3 Making Codex see a downloaded thread

Codex projects rollouts into `state_5.sqlite` and `thread_history_1.sqlite`. **Unverified, and the first Phase 0 measurement:** whether a running `app-server` notices a rollout that appears or grows on disk (a reconciliation pass, a file watch, or only at start). Three outcomes, three designs:
1. **It notices:** nothing more is needed.
2. **It notices at `thread/list` or `thread/read`:** the app re-lists on a manifest change.
3. **Only at start:** the agent calls the app-server's own maintenance method if one exists (Phase 0 lists them), and otherwise the desktop app renders synced threads from the hub's copy until the next start. No patch is planned for this; if one turns out to be the only way, it must fit under the patch cap.

### 5.4 Deletion

Deleting a thread on any device with `control` writes a tombstone. Every device deletes its copy (both directories, its names entries via `remove_thread_name_entries`' format). The hub keeps the tombstone for 90 days, so a device that was off for a month still learns of it.

---

## 6. Live: watching and answering from another device

### 6.1 Publishing

- **The desktop app and `ling web`** are app-server clients already. Their sync agent republishes the notifications of the threads they own (item started and completed, deltas, approval requests, turn completed) to the hub's `/api/sync/live` WebSocket, already filtered by the same `policy.json` rules that govern the app's own IPC.
- **The TUI** has no app-server client to tap. Its live view on other devices follows the rollout as it grows: the agent uploads appended bytes within a second, and the hub fans them out. Deltas inside one message are not in the rollout, so a TUI turn is seen item by item, not token by token. **Unverified:** whether 0.158's TUI runs on an in-process app-server whose events could be reached without a patch; Phase 0 checks, and nothing is patched for it.

### 6.2 Following

A device opens a thread owned elsewhere and subscribes to it on the hub. It renders with the same Work and Ask components, under a banner: "Running on the laptop". The hub buffers the last 2,000 events of a running turn, so a phone that opens late catches up.

### 6.3 Answering

- **The commands:** a device with `control` (the default, O4) may send a message, answer an approval, stop the turn and change the thread's model, and may start a new thread on an online device, in one of that device's projects, as Codex's phone does (§1.1).
- **The route:** the hub forwards the command to the owner's agent, which calls its own app-server as if the user had acted there. The owner's enforcer applies `policy.json` to it, and a command it refuses is refused with the same message as locally.
- **Approvals:** an approval answered remotely is shown on the owner's screen as "approved from the phone".
- **When the owner is offline:** the banner says so, with when it was last seen.
  - A message is queued on the hub, at most 10 per thread for 24 hours. It is shown as pending and delivered in order when the owner reconnects.
  - An approval and a stop are never queued: an approval decided hours later, on a turn that may have moved on, is not what the user meant.

### 6.4 Hand-over

"Continue on this device" moves ownership of a thread to the asking device. It needs both devices online and the repository identity present on the receiving device, found by `ling sync` from the user's known project paths.
- **The handshake:** the old owner finishes or stops its turn, releases its writer lock, uploads its last bytes and marks the thread handed over. The new owner takes the lock and resumes the thread in its own working tree, as `ling resume <id>` does. The rollout keeps growing as one file; the owner field changes.
- **Uncommitted or unpushed work** (O5, Phase 3, opt-in per hand-over). The old owner makes a git bundle of the thread's branch and a patch of the uncommitted diff, the hub passes them through and keeps nothing after delivery, and the new owner applies them on a new branch, `handover/<thread>`, never onto the user's current branch. This is refused at `/airgapped on` on either end, and refused for a project marked "this device only".

---

## 7. The phone

### 7.1 A PWA over the overlay

`ling web` gains a web-app manifest, a service worker and a mobile layout of the thread list, a thread, the composer, approvals and the Night Shift inbox.
- **Where it installs:** a service worker needs a secure context, which the remote-access overlay provides under the node's name-constrained CA (REMOTE_ACCESS decision 3). So the PWA installs only over the overlay, once the phone has enrolled and trusted the CA.
- **On the plain LAN** the same pages work in the browser without installing, text-only as decided (ASK §4.4).
- **The service worker** caches the shell only, never thread contents.

### 7.2 The Telegram Mini App

It is unchanged (TELEGRAM_MOONSHOT §15). It reads `ling web` over the overlay, so it lists every synced thread once sync is on.

### 7.3 The bridges

A phone chat's thread is a node thread. With sync on, it is listed on the laptop and the desktop app like any other, and its replies from the phone appear there live.

### 7.4 Notifications

- **The default is the user's own bridge.** The hub sends one short notice, for example "Turn finished: Fix login redirect · open", or "Approval needed", through the bridge that is on. The link opens the PWA or the Mini App.
  - Content stays out of the notice unless the user turns on `[sync] notify_preview`; the bridge already carries the user's chat with the agent.
  - Rate limit: one notice per thread per 5 minutes.
- **Web Push is opt-in** (O6). Its payloads are encrypted end to end (RFC 8291), so the browser vendor's push service sees ciphertext. It still sees that a notice was sent, to which phone and when, so Mightling sends only a content-free payload ("something needs you"). The PWA fetches the details from the node itself. The push service's endpoint is the one and only outside destination this spec can add, so it is listed by `ling-admin audit egress --sync` as a declared exception (SIGNAL §10's pattern), and it is refused at `/airgapped on`.

---

## 8. Conflicts

- **Rollouts never conflict by construction.** One writer per thread (decision 3), and the hub rejects any upload that is not an extension of what it holds (§5.2).
- **Split brain.** For example, the laptop is offline and the user resumes the same thread on the desktop with `ling resume`. The hub sees two devices extend the same prefix. It keeps the first, and gives the second a new thread id titled "<title> (continued on the desktop)", with a link back. Two transcripts are never merged.
- **Small documents** (names, pins, archive state, projects): last writer wins per field, ordered by a hybrid logical clock (device clock plus counter plus device id), so a device with a wrong clock cannot win forever.

---

## 9. Security and privacy

| Threat | Answer |
|---|---|
| Someone on the LAN reads synced threads | LAN traffic to `ling web` is plain HTTP, on the trusted-LAN decision of 2026-09-30; over the overlay it is WireGuard plus TLS. A user who distrusts the LAN uses the overlay for sync too (`[sync] via = "overlay"`) |
| A stolen phone | `ling web revoke <device>`; its cookie and device secret stop working at once. The PWA caches no thread content (§7.1) |
| A compromised device | It sees what any paired device sees, and with `control` can send messages to other devices' threads. These reach them through their own `policy.json` and their own sandbox, so the attacker gains no more than typing at that device's composer would. Revoke it. |
| A sandboxed command reading the device secret | The sandbox can read the user's home (SECURITY_REVIEW_2026-10), the same exposure as `ling web`'s token today. It closes when that does. The secret lives outside every writable root, so a command cannot plant one. |
| A command injected into a thread controlling another device | Remote commands are user actions: an agent cannot issue them. The hub accepts `control` only from a device credential, never from the agent's tools, and `policy.json` has no method that sends to the hub. |
| The rented box | Unchanged: ciphertext only, stores nothing (REMOTE_ACCESS §1) |
| The hub's disk | The node already holds everything the hub holds, and more: the model, Night Shift, the phone chats. The hub store is 0700 and lives inside `~/.mightling`, so `ling-admin` backups and deletions treat it like the rest |

**`/airgapped`.**
- **At `on`:** sync keeps running to the node, which is the same machine the model server is on, and the only destination `on` allows (AIRGAPPED §5.4). It runs as its own process, outside the agent's session, and talks only to the node's `ling web` port. Web Push, hand-over bundles and any route other than the node are refused.
- **The audit:** `ling-admin audit egress --sync` traces the agent for one cycle. It passes only if every destination is the node's `ling web` port, on a private or overlay address, plus the declared Web Push endpoint if that is on.

---

## 10. Storage, retention and limits

- **Store:** `~/.mightling/sync/hub/` on the node. Its default cap is 20 GB, against 20 MB of real use today. Above the cap, rollouts of threads untouched for 180 days that are not pinned are dropped from the hub and marked "kept on <device>". The owner's own copy is never touched by the hub.
- **Devices with `replicate` on** keep what they downloaded until the thread is deleted. A small laptop can set `[sync] keep_days = 30`, after which older threads it doesn't own are dropped locally, listed, and fetched again on open.
- **Back-pressure:** at most 4 uploads and 4 downloads at once per agent.
- **Throttling:** live events are batched every 100 ms. A rollout line over 8 MB (a large tool output) is synced, but shown truncated on a phone.

---

## 11. Commands

```
ling sync enable                 # on the node: turns the hub on in ling web; on a device: pairs (code or QR) and starts the agent
ling sync status                 # hub or agent, devices, last cycle, pending uploads, queued messages
ling sync disable                # stops; keeps local files; on the node, keeps the hub store until `ling sync forget`
ling sync forget                 # node: deletes the hub store for real; device: forgets its secret
ling sync exclude <path>         # this project never leaves this device
ling sync handover <thread>      # continue a thread here (§6.4); --with-code for the bundle (O5)
```

The desktop app gets the same in Settings › Sync, and a "Running on the laptop" banner and "Continue here" button on threads owned elsewhere.

---

## 12. Phases

| Phase | What | Acceptance |
|---|---|---|
| **0. Measure** (no product code) | §5.3: does a running app-server see new and growing rollouts? Does compression keep the logical bytes? Can the TUI's events be reached without a patch? The app-server's maintenance methods; the cost of a 2,000-event buffer | A short report in §15 of this spec; the design branch chosen for §5.3 |
| **1. History everywhere** | Hub store and routes, the sync agent, rollouts, names, archive state, tombstones, pairing scopes, `ling sync` commands, `audit egress --sync` | A thread started in the laptop's app appears in the node's `ling web` and the phone's browser within 5 s of its first item; deleting it anywhere deletes it everywhere; the audit passes |
| **2. Live** | Live fan-out, following, remote send, approve and stop, the offline queue, the banner, notices through the bridges | From the phone, answer an approval raised by a desktop Work turn and see the turn continue; a TUI turn followed item by item |
| **3. Hand-over** | Ownership transfer; the optional bundle (O5) | A desktop thread continued on the node in the same repository, the transcript one file; with `--with-code`, an unpushed branch arrives as `handover/<thread>` |
| **4. Phone app and the rest** | The PWA over the overlay, Web Push (opt-in), pins, projects and unread marks; `[sync] replicate` | The PWA installed on an Android phone and an iPhone over the overlay; a push notice with no content in its payload, verified on the wire |

---

## 13. Tests

- **The hub:**
  - accepts an upload only at its length with a matching prefix;
  - chain hashes stay stable across compression;
  - manifest cursors never skip, after restarts too;
  - tombstones outlive a 30-day absence;
  - scopes: a new pairing gets `control`; a device restricted to `watch` has its control commands refused.
  - scope: settings, skills, memories and `ling exec` rollouts never reach the hub by default (asserted on the wire); without `replicate`, reading another device's thread writes nothing under `~/.mightling`.
- **The agent:**
  - never writes a thread with a local writer lock, and never one its device owns;
  - writes through a temporary file and a rename;
  - catches up after a pause with the 30-second rescan;
  - an excluded path's threads never reach the hub (asserted on the wire).
- **Conflicts:** two devices extend one prefix, and the second becomes a new thread; the hybrid clock orders a device whose clock is wrong.
- **Policy:** a remote command passes through the owner's `policy.json` enforcer; the conformance cases gain the forwarded methods, in both enforcers.
- **Egress:** `audit egress --sync` passes with sync on, and fails on a planted outside destination.
- **Off by default:** with sync never enabled, no route answers, no unit exists and no file is written. One test per surface.
- **No real machine:** every test uses its own `HOME` and stand-in app-servers (AGENTS.md, the hard rules).

---

## 14. Non-goals

- **No cloud relay that stores data.** The box stays a pass-through for ciphertext.
- **No sync between different users.** Mightling is a personal tool: one owner, paired devices (ASK §11).
- **No merging of transcripts,** and no running a turn anywhere but where the code is.
- **No native iOS or Android app.** The PWA, the Mini App and the bridges cover the phone without an app store account.
- **No sync of the indexes, the databases or credentials** (§4).

---

## 15. Changes to other specs when this is built

- **MIGHTLING_ASK:** §4.3 gains sync agents and scopes beside browser devices; §4.4 notes the PWA over the overlay.
- **MIGHTLING_NODE:** §10's table gains "threads from other devices: yes, with sync".
- **MIGHTLING_DESKTOP:** §6.9 "Handoff and shared history" becomes "yes, through the node".
- **MIGHTLING_REMOTE_ACCESS:** §8 lists sync among the overlay's users.
- **MIGHTLING_EGRESS:** the `--sync` audit and the Web Push exception.
- **The paper's paragraph,** once Phase 2 ships, should read: "Mightling synchronises threads between the user's desktop, browser and phone through a sync hub on the user's own node. History, titles and archive state everywhere; live turns followed and answered from any paired device; nothing passes a vendor cloud, and the session databases stay local because each device rebuilds them from the synced transcripts. It is off until the user enables it per device."
