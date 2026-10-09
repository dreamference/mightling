# Mightling over Signal: `ling signal`

**Status:** implemented, off by default (2026-10-08). The bridge is built and tested (§16) end to end against a scripted signal-cli and a stand-in `ling web`, and merged into `main` with the other messengers (§17): `ling signal …` is a launcher subcommand, the `ling-signal` daemon binary ships in the release, and signal-cli and its Java runtime are fetched pinned by setup. Nothing has run against a real Signal account yet. The user asked for this on 2026-10-08 ("can I chat with ling using signal? … write a spec for this").
**Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`, `ling web` on port 3100).
**Builds on:**
- [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md). The bridge is one more client of `ling web`, signed in like a paired phone (§4.3 there). It uses **Ask threads** (§3 there), the bridge policy (`ling-rs/web/policy.json`) and `/api/upload`.
- [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md): the air-gap resolver (`ling-rs/airgapped`).
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md): what `ling-admin audit egress` promises, and what this changes.
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md): the node is where the bridge runs.
- [SECURITY_REVIEW_2026-10](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md): the rule that a sandboxed command can read the user's home folder.

---

## 0. Decisions, stated first

1. **Signal is one more way into the same agent, not a second chatbot.** A message from the owner's phone becomes a turn in an **Ask thread** on the node. The same thread shows up in the Mightling app and in `ling web`, and can be continued there. Nothing is answered by anything but `ling`.
2. **The bridge is a client of `ling web`, never of the app-server directly.**
   - **What it gives:** the bridge policy, the Ask folders, upload caps and Night Shift's busy marker all apply to Signal with no second copy.
   - **How it signs in:** the bridge pairs once, like a phone's browser (`ling web pair`), and holds a device cookie.
3. **The bridge runs as its own system account, `mightling-signal`, not as the user.**
   - **Why:** Codex's sandbox limits writes, not reads, so a command the agent runs can read anything the user can (security review, 2026-10). Signal's identity keys are not something a prompt-injected `cat` may read. With them, an attacker could read every message sent to the bridge and send messages that look like Mightling's.
   - **How:** a separate uid with a 0700 state folder puts them out of the agent's reach. A systemd system unit with `ProtectHome=yes` also keeps the bridge out of the user's files.
4. **One owner.** Linked (the default), the owner is the account itself (§4.2). With a dedicated number, the owner is bound by a code, never by a phone number typed in: `ling signal setup` shows a one-time code. The first account that sends it from Signal within ten minutes becomes the owner, and its account id (ACI) and identity key are recorded.
   - **Strangers:** messages from anyone else are dropped without a reply, so the number does not reveal that a bot answers it.
   - **A changed identity key:** messages from the owner stop being accepted until the owner re-trusts on the node (§5.3). This is what a SIM swap or a re-registered phone looks like.
5. **Linked to the owner's own account is the mode (the user's decision, 2026-10-08: no second number).**
   - **Linked (default):** the bridge becomes a linked device of the owner's account, paired by QR code. The owner talks to it in **Note to Self**, and it answers there. It costs nothing, but the bridge then holds keys to the owner's whole account and receives every message of it. §4.2 is the risk analysis, and setup prints the warning before anything happens.
   - **Dedicated (`--number`):** a second Signal account (prepaid SIM or landline) that the owner talks to like a contact. Built and kept for anyone who prefers it.
6. **Signal needs the internet, so the bridge runs only at `/airgapped off`.**
   - **Never at `on`:** at a user-level `on` the bridge acts on nothing and sends nothing, not even a read receipt. It learns the level from `ling web` (§11), and until the first answer arrives it holds incoming messages rather than act on them. When the level drops back to `off`, it says once what was missed.
   - **One external exception:** the bridge is the one Mightling component that talks to an outside service on its own. It talks only to Signal's servers, and only when the user turned it on. `ling-admin audit egress` names it as an enabled exception instead of passing silently (§10).
7. **Off by default.** No installer step turns it on. `ling signal setup` is the only way in, and it says what it changes before any sudo.
8. **signal-cli first, a native client later.**
   - **Phase 1:** the bridge drives **signal-cli** (GPL-3.0, Java), the maintained command-line Signal client, over JSON-RPC on stdio.
   - **Later:** a native Rust client (e.g. presage on libsignal, both AGPL-3.0) could remove the JVM, after an evaluation (§14).
9. **Text first.** Phase 1:
   - carries text both ways;
   - takes images and files *in* through `/api/upload`;
   - relays approvals as yes/no questions.

   Voice notes (through the speech sidecar), files *out* and Work threads on repositories are Phase 2.
10. **Where it runs:** gx10-1a2b, the node with `ling web` and the model (the user's decision). Approvals are answered from the phone with YES or NO, as designed (§8).

---

## 1. What the owner sees

Linked to the owner's account, in **Note to Self** (the default). Every line is "sent by me"; the bridge's start with 🐦.

```
What changed in the SGLang release notes this week?
🐦 Three things matter for us: … [1][2]
   Sources: [1] github.com/… [2] …
/new
🐦 New thread. (The last one stays in your history.)
Here's a photo of the error  [image]
🐦 That traceback is from …
🐦 ⚠️ Mightling wants to run:
   pip download torch==2.9 --no-deps -d .
   in this question's folder. Reply YES or NO.
YES
🐦 Done: …
/stop
🐦 Stopped.
```

With a dedicated number, the same exchange happens in a conversation with that contact, without the 🐦, and the owner also sees read receipts and the typing indicator.

- **Each message from the owner is one turn.** While a turn runs, a dedicated-number bridge keeps the typing indicator alive; Note to Self has none.
- **A long turn gets one progress note** after a minute, then at most one every five minutes. The note says what it is doing, e.g. "running `pytest -q`, 2m10s". The final answer arrives as one or more messages.
- **A message sent while a turn runs is queued.** The bridge says so ("Queued; I'll take it next. /stop to interrupt.") and starts it when the turn ends. At most five are queued.
- **The thread is the conversation.** Everything since the last `/new` is one Ask thread with its own scratch folder, so "and what about the second one?" works.

---

## 2. Architecture

```
 Owner's Signal app ──E2E──► Signal servers ◄──E2E── signal-cli (JVM, jsonRpc on stdio)
                                                         ▲  stdio JSON-RPC
                                                         │
                       system user mightling-signal      │
                       ┌─────────────────────────────────┴───────────────────────┐
                       │ ling-signal (bridge daemon, Rust)                       │
                       │  owner gate · commands · queue · formatter · approvals  │
                       └───────────────┬─────────────────────────────────────────┘
                                       │ HTTP + WebSocket, loopback, device cookie
                                       ▼
                       user (owner of the node)
                       ling web :3100 ──policy.json──► ling app-server ──► model server :8000
                                        └── /api/upload ──► ~/.mightling/ask/<thread>/
```

**Processes:**
- **`signal-cli`:** a child of the bridge, started as `signal-cli --config /var/lib/mightling-signal/signal-cli -a <account> --trust-new-identities on-first-use jsonRpc --receive-mode on-start` with `--ignore-stories`. It speaks JSON-RPC 2.0, one object per line. Messages arrive as `receive` notifications. The bridge calls `send`, `sendTyping`, `sendReceipt`, `listIdentities` and `trust`.
- **`ling-signal`:** the bridge daemon, a small Rust binary (crate `ling-rs/signal`).
  - **What it holds:** the signal-cli child, one `ling web` connection, and the state file (§7).
  - **How it is written:** like `ling-web-server` it is a crate of its own, testable without the Codex workspace. The launcher routes `ling signal …` to the same crate's user-facing commands (§6).
- **`ling web`:** the user's, unchanged. The bridge is a paired device (`ling web devices` lists it as `signal-bridge`, and `ling web revoke` ends it).

**Why not inside `ling web`:**
- `ling web` runs as the user, and the Signal keys must not.
- `ling web` calls nothing on any network, which is the promise of its module docs (`ling-rs/web/src/lib.rs`). A Signal client inside it would end that promise.

---

## 3. Install and the files it puts down

`ling signal setup` runs as the user and asks for sudo once, after listing what it will do:

| What | Where | Why |
|---|---|---|
| System account `mightling-signal` (no login shell, no home) | `/etc/passwd` | Keys out of the agent's reach (§0.3) |
| Java runtime | `/opt/mightling/jdk-25.0.4.1+1-jre/` | signal-cli 0.14 needs Java 25 or later. Eclipse Temurin's JRE, fetched by setup and pinned by URL and SHA-256 for arm64 and x86_64 (§17); no apt package, whose pool URL would vanish with the next security update |
| signal-cli | `/opt/mightling/signal-cli-<version>/` | The release tarball, checked against a SHA-256 pinned in Mightling's source and against the maintainer's detached OpenPGP signature (`.asc`) with a key whose fingerprint is pinned there too; both must pass before it is unpacked (§17.1) |
| libsignal JNI for arm64 | `/opt/mightling/signal-cli-<version>/lib/libsignal_jni.so`, put inside the `libsignal-client-<v>.jar` | signal-cli bundles it only for x86_64 Linux, Windows and macOS (its wiki, "Provide native lib for libsignal") |
| The bridge | `/usr/local/lib/mightling/ling-signal` (root, 0755) | Users' homes are 0750 on Ubuntu 24.04, so the system account cannot run a binary from the user's install |
| The unit | `/etc/systemd/system/mightling-signal.service` | A system unit: no lingering needed, survives logout |
| State | `/var/lib/mightling-signal/` (0700, `StateDirectory=`) | signal-cli's data, the bridge's state, the device cookie |

**The arm64 libsignal library:**
- **Phase 1:** use `exquo/signal-libs-build`'s `libsignal_jni.so-v<ver>-aarch64-unknown-linux-gnu.tar.gz` for the exact version the signal-cli release bundles. The version is read from the `libsignal-client-<ver>.jar` name in its `lib/`. The file is pinned by SHA-256 in `ling-rs/signal/src/setup.rs` (`LIBSIGNAL_AARCH64_SHA256`, beside the signal-cli and JRE pins).
- **Later:** Mightling's release workflow builds it from Signal's source (`java/build_jni.sh desktop` in signalapp/libsignal) and attaches it to each release, signed with the release key, so no third-party binary is trusted.

**The unit:**

```ini
[Unit]
Description=Mightling over Signal (ling signal)
After=network-online.target
Wants=network-online.target

[Service]
User=mightling-signal
StateDirectory=mightling-signal
StateDirectoryMode=0700
ExecStart=/usr/local/lib/mightling/ling-signal serve --state /var/lib/mightling-signal
Environment=JAVA_TOOL_OPTIONS=-Xmx256m -XX:+UseSerialGC
MemoryMax=768M
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=yes
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**On these settings:**
- **Memory:** `MemoryMax=768M` with a 256 MB heap keeps the JVM inside a fixed budget. Unified memory makes a runaway JVM a host problem (`docs/dev/host-safety.md`). The real footprint is measured in Phase 0 and the cap set from it.
- **No access to home folders:** `ProtectHome=yes` means the bridge can never open a file of the user's. Attachments reach the Ask folder through `ling web`'s `/api/upload`, which owns that check.

**Updates:**
- **The bridge binary:** `ling update` refreshes the user's binaries, not `/usr/local/lib/mightling/ling-signal`. When the two versions differ, `ling signal status` and the bridge's own `/status` say so, and `ling signal setup --refresh` copies the new one (sudo).
- **signal-cli:** updates are a pin change in Mightling's source, picked up by the same `--refresh`. Signal retires old clients, so an outdated signal-cli eventually stops working. `ling signal status` warns when signal-cli reports that it is deprecated.

---

## 4. The two account modes

### 4.1 Dedicated number (`--number`)

1. **Register.** `ling signal setup` asks for the number (E.164) and runs `register`. Signal usually asks for a CAPTCHA:
   - the command prints `https://signalcaptchas.org/registration/generate.html`;
   - the owner solves it in a browser and copies the `signalcaptcha://…` link it ends on;
   - they paste it, and the command runs `register --captcha …`.
   A landline gets `--voice`.
2. **Verify.** The owner types the SMS or voice code, and the command runs `verify <code>`.
3. **Lock the account.** Setup then:
   - sets a registration-lock PIN (`setPin`), random, stored only in the bridge's state, so the number cannot be re-registered by someone else who gets the SIM;
   - sets the profile name to "Mightling" (`updateProfile --given-name Mightling`);
   - turns off discovery by phone number (`updateAccount --discoverable-by-number false`), so strangers cannot find the account by guessing numbers. (Option names to be confirmed in Phase 0.)
4. **Bind the owner.** Setup prints a six-digit code: "send it from your phone to +1 555…". The first message that is exactly the code, within ten minutes, binds the owner:
   - the bridge records its `sourceUuid` (ACI) and the identity key's fingerprint from `listIdentities`;
   - it marks that identity verified (`trust --verified-safety-number`);
   - it answers "Paired. Ask me anything.".

   Other senders during that window are ignored, as always. If no code arrives in time, setup says so and leaves the bridge stopped.
5. **Disappearing messages.** Setup sets the conversation's timer to the owner's choice (`[signal] disappearing`, default one week; `updateContact --expiration`). Code, mail snippets and file contents shown on the phone then do not stay there forever.

Numbers that work: a prepaid SIM, a landline (voice code) or an eSIM data plan with a number. VoIP numbers are often refused by Signal. Setup says so before registering.

### 4.2 Linked to the owner's account (the default)

`ling-signal setup`, with no `--number`, does the following:

1. **Install:** it runs the install of §3, plus `qrencode` to draw the QR code.
2. **Link:**
   - it runs `signal-cli link -n "Mightling (<host>)"` as `mightling-signal`;
   - it reads the `sgnl://linkdevice?…` link from its output and draws it as a QR code in the terminal. signal-cli draws one itself only when it owns a terminal, which it does not under `sudo -u` with its output read.
3. **Scan:** the owner opens Signal → Settings → Linked devices → Link new device and scans the code. When the phone asks, they choose "Don't transfer" for message history.
4. **Learn the account:**
   - the number, from signal-cli's `Associated with: +…` line;
   - the account's id (ACI), from `listAccounts -o json`;
   - this device's number, from the `(this device)` line of `listDevices`, whose JSON leaves that mark out.
5. **Timer:** it asks about the disappearing-message timer (below), then pairs with `ling web`, writes `bridge.json` with `mode: linked` and starts the unit.
6. **Talk:** the owner writes `/help` in Note to Self.

**What the bridge reads.**
- **Only Note to Self:** a Note to Self message reaches the bridge as a sync message, `syncMessage.sentMessage`, whose destination (`destinationUuid` or `destinationNumber`, as signal-cli's `JsonSyncDataMessage` writes them) is the account itself, sent from another of the owner's devices.
- **Never anything else, never counted:** messages from others (data messages), the owner's messages to anyone else or to a group (sync messages with another destination or a group), receipts, typing, calls and stories. Their timestamps are not even remembered, and `status.json` has no count of them (`strangersIgnored: null`).
- **Nothing on the owner's behalf:** no read receipt (it would mark the owner's own note read on every device) and no typing indicator (Note to Self shows none).

**Answers.**
- **Where:** answers go to Note to Self (`send` with `noteToSelf: true`, no recipient).
- **The marker:** every message the bridge writes starts with `🐦 ` (`LINKED_MARKER`), because in Note to Self the owner's notes and the bridge's replies are both "sent by me". The style ranges move by the marker's three UTF-16 units.
- **No loops:** a marked message, or one from the bridge's own device, is never a question. Signal does not echo a message to the device that sent it, but a second bridge linked to the same account would see this one's replies, and without the marker the two would answer each other forever.

**The risks of linking, and what is done about each.**

1. **The bridge holds keys to the owner's whole account.** A linked device gets the account's identity key pair and its own credentials at provisioning. With them, signal-cli can:
   - read every message the account receives from the moment of linking, in every conversation;
   - send as the owner to anyone;
   - read the contact and group lists that the phone syncs to every linked device, and change the profile.

   This is not a narrower key than the phone's; it is the same account. What is done:
   - **What signal-cli is told to do:** it runs with `--ignore-attachments --ignore-avatars --ignore-stickers --ignore-stories`, so nothing of other conversations is downloaded.
   - **What the bridge does:** it acts on Note to Self alone. Everything else signal-cli decrypts is dropped in memory by the gate, and the bridge never writes message text to disk or to the journal.
   - **What is stored anyway:** signal-cli's own store under `/var/lib/mightling-signal/signal-cli` still holds the account's keys and the synced contact and group lists. It is protected as the keys are (2 below).
2. **The agent must never reach those keys.** Unchanged from §0.3, and in linked mode it matters more.
   - **The keys live where the agent cannot go:** in `/var/lib/mightling-signal`, mode 0700, owned by the system account `mightling-signal`. The agent runs as the user, so no command it runs, sandboxed or not, can read them.
   - **The bridge cannot touch the user's files:** `ProtectHome=yes` keeps it out of them, and attachments reach the Ask folder only through `ling web`'s `/api/upload`.
   - **The bridge holds no route back to the keys:** the only thing it shares with the user's side is a `ling web` device cookie. That cookie gives `ling web`'s routes, which are the bridge policy, and nothing of Signal.
   - **The one exposure left is root:** anyone with root (sudo) on the node can read the keys, as they can read anything. The agent never gets root: `/airgapped`, Full Access and the sandbox are unchanged by this feature.
3. **Message history sync.** When a device is linked, current Signal phones can offer to transfer recent message history, and they sync contacts, groups and settings to every linked device.
   - **What the owner is told:** setup tells the owner to choose "Don't transfer".
   - **Whether signal-cli 0.14.9 would take an archive at all:** not established (§12).
   - **If history did arrive:** the bridge would not act on it. Old messages are older than the gate's 24-hour limit, and history arriving as anything other than live Note to Self sync messages is not a question.
   - **What sync still brings:** the contact and group lists still arrive, and live in signal-cli's store.
4. **Unlinking.** The owner can unlink the device from the phone at any time (Settings → Linked devices → Mightling → Unlink). Re-registering the account, on a new phone or by someone else, unlinks every linked device.
   - **What signal-cli sees:** the server then refuses the device's credentials, and signal-cli logs an `AuthorizationFailedException`.
   - **What the bridge does:** it treats that line as "unlinked". It writes `status.json` with what to do, stops signal-cli and exits with status 78, which the unit's `RestartPreventExitStatus=78` keeps from restarting in a loop. Nothing is deleted automatically: a mistaken match must not destroy a working link.
   - **What the keys can still do:** the device credentials are dead once unlinked. The account's identity key in the state folder is not: Signal does not rotate it on unlink, and it stays until the owner's account is registered again. So `ling-signal remove` deletes the state.
   - **If the state was ever exposed:** the only way to retire that identity key is to register the account again, which changes the safety number for every contact.
   - **The other way round:** `ling-signal remove` deletes the keys but cannot unlink the device from the phone's list, so it asks the owner to do that.
   - **Never `unregister`:** in linked mode `remove` never runs `unregister`, which would act on the owner's own registration. A test holds this, and an unknown mode is treated as linked.
5. **Thirty days offline.** Signal removes a linked device that has not connected for about 30 days. A node switched off that long must be linked again with `ling-signal setup`.
6. **The account's device limit.** Signal allows a small number of linked devices per account, five at the time of writing. The bridge takes one.

**Disappearing messages in Note to Self** (one week, the user's default). Signal keeps one disappearing-message timer per conversation, and a message carries its sender's timer, so the timer cannot be set for the bridge's replies alone. In Note to Self, a one-week timer therefore also applies to the owner's own notes written there from then on, on every device. Notes already there are not affected. Setup says this and asks, with yes as the default. It sets the timer with `updateContact <own number> --expiration 604800`, and if signal-cli refuses that on the account itself, it tells the owner where to set it on the phone (Note to Self → the name at the top → Disappearing messages). That this command works on one's own account is not established (§12).

**Attachments:** the owner's attachments in Note to Self are fetched one at a time with `getAttachment {id, recipient: <own number>}`, which returns the file as base64. They then go to the Ask folder through `/api/upload`. Nothing else is ever downloaded.

## 5. The owner, and everyone else

### 5.1 The gate

Every `receive` envelope passes these checks, in order. The first that fails drops the message:

1. **A direct message:**
   - dedicated mode: a `dataMessage`, not a group, story, call, typing, receipt or reaction;
   - linked mode: a `syncMessage.sentMessage` to the owner's own account.
2. **From the owner:** `sourceUuid` equals the recorded ACI. A phone number is never trusted on its own, since numbers move between people.
3. **From a trusted identity:** the owner's current identity key, from signal-cli's trust state, equals the recorded fingerprint. If it does not, §5.3.
4. **Not stale:** the message timestamp is within 24 hours. An old message delivered late after downtime is answered with "This arrived N hours late; send it again if you still want it", not acted on.
5. **Not seen before:** the timestamp is not already in the last 1,000 handled. Signal retries can deliver a message twice.

**Dropped messages are counted, never stored or answered.** `ling signal status` shows "12 messages from strangers ignored since …". Their content is not logged.

### 5.2 Commands

A message that is exactly one of these is a command. Anything else is a question.

| Command | What it does |
|---|---|
| `/new` | Starts a new Ask thread for the next message; the last one stays in the history (app, browser) |
| `/stop` | Interrupts the running turn (`turn/interrupt`) and empties the queue |
| `/status` | What is running and for how long, the queue, the model server's state, the air-gap level, the bridge's and signal-cli's versions |
| `/threads` | The last eight threads started from Signal, numbered, with their first line |
| `/use N` | Continues thread N from `/threads` (`thread/resume`; an Ask thread keeps its folder, ASK §16) |
| `/help` | This table |
| `YES` / `NO` | Only while an approval is pending (§8); otherwise an ordinary message |

**No command reaches the node's administration:** none starts or stops the model server, changes the air-gap level, pairs devices or changes the bridge. Those stay with a shell on the node.

### 5.3 A changed identity key

When the owner's identity key changes (a new phone, a reinstall, or someone else's SIM):
- the bridge stops accepting messages from that account;
- it sends nothing to the new key. A reply would go to whoever now holds the account, so the bridge says nothing. That is the point.
- it writes a line to the journal and to `ling signal status` ("the owner's safety number changed on …; run `ling signal trust` on the node").

`ling signal trust` shows the new safety number and asks the owner to compare it with the one their phone shows (Signal → the conversation → View safety number), then records the new fingerprint.

---

## 6. Commands on the node

`ling signal …` runs as the user. Anything that touches the system account goes through one sudo prompt, with the commands printed first, as `ling-admin host setup` does.

| Command | What it does |
|---|---|
| `ling signal setup [--number +… [--voice]] [--dry-run]` | §3 and §4: install, then link to the owner's account by QR (the default) or register a dedicated number and bind its owner; pair with `ling web`, start the unit |
| `ling signal setup --refresh [--dry-run]` | Copies the current bridge binary, fetches the pinned signal-cli and Java if they changed, points the bridge at them and restarts it |
| `ling signal status` | Running or not, mode, the owner's last message time, versions, messages ignored, the last error |
| `ling signal trust` | §5.3 |
| `ling signal stop` / `start` | The unit: `stop` disables it as well, so it stays off across a reboot |
| `ling signal remove` | Stops and disables the unit, unregisters (dedicated) or unlinks (linked), revokes the `ling web` device, and deletes `/var/lib/mightling-signal` and the account. Asks for confirmation; the Ask threads stay |
| `ling-signal serve --state DIR` | The daemon itself: what the unit runs; nobody types it. `ling signal` refuses it and the bridge account's other commands |

**Pairing with `ling web`:** setup runs `ling web pair` as the user and hands the eight-digit code to the daemon. The daemon `POST`s it to `http://127.0.0.1:3100/pair` with the server's own `Origin`, and keeps the device cookie in its state. `ling web` must be running; setup starts it (`ling web start`) if it isn't.

---

## 7. State

`/var/lib/mightling-signal/` (0700, owned by `mightling-signal`):

| File | Contents |
|---|---|
| `signal-cli/` | signal-cli's own data: account, keys, its SQLite store |
| `bridge.json` | Mode, account, the owner's ACI and fingerprint, `ling web`'s port, the disappearing timer |
| `device-cookie` (0600) | The `ling web` device credential |
| `conversation.json` | The current thread id, the last eight Signal threads, the queue, the last 1,000 handled timestamps, the ignored-message counter |
| `pin` (0600) | The registration-lock PIN |

`/run/mightling-signal/status.json` (0644, the unit's `RuntimeDirectory`) is the one file meant for the user: running, mode, whether the owner is paired, whether `ling web` is connected, the air gap, the last owner message's time, the strangers counted, the identity refusals, the last error and the versions. It holds no message text and no account id. `ling-signal status` reads it.

Every file is replaced whole: written to a temporary name, then renamed, as Night Shift writes its tasks.

---

## 8. The agent side

The bridge holds one WebSocket to `ling web` (`/ws`) with the bridge envelope that `ling web ask` uses:
- `{call, message: {type: "work/start" | "work/send", …}}` out;
- `{answer, result | error}` and `{event: {channel: "work://message", payload}}` in.

**Turns:**
- **New thread:** `thread/start {prompt: "ask"}`. The policy makes it an Ask thread in a fresh scratch folder.
- **Continue:** `thread/resume {threadId}`. The policy keeps an Ask thread in its folder.
- **A message:** `turn/start {threadId, input: [{type: "text", text}, …]}`. Uploaded images are added as `{type: "localImage", path}`, only when the served model has vision.
- **The answer:** the text of `item/agentMessage/delta` for that thread, until `turn/completed`. Several agent messages in one turn are kept as paragraphs.
- **Interrupt:** `turn/interrupt {threadId, turnId}`.

**Approvals:**
- **Which requests:** `item/commandExecution/requestApproval` and `item/fileChange/requestApproval` from the app-server.
- **How they reach the owner:** one Signal message, "⚠️ Mightling wants to run: `<command>` in `<folder>`. Reply YES or NO."
- **Answers:** YES answers `{decision: "accept"}` and NO answers `{decision: "decline"}`, both through `ling web` (the policy only lets a pending request be answered).
- **Timeout:** no reply in ten minutes is `decline`, and the owner is told.
- **What is never offered:** "accept for session" and execpolicy amendments. A rule that outlives one question should be set at a keyboard.
- **Permission requests** (`item/permissions/requestApproval`) are always declined from Signal, with a note to use the app.

**Attachments in:**
- **What the bridge does:** each attachment the owner sends is read from signal-cli's attachment folder (inside the bridge's state), sent to `/api/upload?thread=<id>&kind=image|file&name=…` and deleted from the state.
- **What `ling web` enforces:** the upload caps (20 MB images, 100 MB files) and the Ask-folder check.
- **Order:** for a thread not created yet, the bridge first starts it, then uploads, then starts the turn.

**Connection loss:** if `ling web` is down or the socket closes, the bridge reconnects with backoff (1, 2, 5, 10, 30 s). After ten seconds down it tells the owner once: "Mightling's web server on <node> isn't answering; I'll keep trying."

**Model server down:** a turn that fails with a connection error to the model server is answered with "The model server on <node> isn't running (`ling-admin server start` on the node)."

---

## 9. Formatting answers for Signal

Signal shows plain text with style ranges: bold, italic, strikethrough, monospace and spoiler. signal-cli takes them as `start:length:STYLE`, in UTF-16 code units. The formatter turns the agent's Markdown into text plus ranges:

| Markdown | Signal |
|---|---|
| ```` ``` ```` fenced block | its lines, MONOSPACE, fences dropped; a language tag dropped |
| `` `code` `` | MONOSPACE |
| `**bold**`, `__bold__` | BOLD |
| `*italic*`, `_italic_` (between word boundaries) | ITALIC |
| `~~strike~~` | STRIKETHROUGH |
| `# Heading` … `######` | the heading text, BOLD |
| `[text](url)` | `text (url)`; a bare URL stays as it is (Signal links it) |
| `- item`, `* item` | `• item` |
| `1. item` | unchanged |
| tables | kept as text, MONOSPACE, so the columns line up |

**Splitting:**
- **Size:** a message is cut into parts of at most 2,000 UTF-16 units, at a paragraph break if there is one, else a line break, else a space.
- **Code blocks:** a cut inside a monospace range closes it in one part and reopens it in the next.
- **Very long answers:** an answer longer than eight parts is sent as its first part plus the whole answer as an attachment, `answer.md`, which the phone opens.

**Citations:** `[n]` markers and the source list from the `ask` prompt are left as text, so the phone shows the URLs and makes them tappable.

---

## 10. Security and privacy, together

- **What leaves the machine:**
  - **Signal's servers** see that the bridge's account and the owner's account exchange messages, their sizes and times (less where sealed sender applies), and nothing of their content (end-to-end encryption).
  - **The phone** holds every question and answer, until the disappearing timer clears them (§4.1).
  - **Nothing goes to Dreamference.**
- **The egress audit:**
  - `ling-admin audit egress` still traces a `ling` session and still requires no connection beyond loopback.
  - New: when `mightling-signal.service` is enabled, the verdict adds "Signal bridge enabled: signal-cli connects to Signal's servers". This is a declared exception, not a pass hidden behind one.
  - `audit egress --signal` traces the unit's processes for one minute. It passes only if every connection is a Signal service host (`chat.signal.org`, `storage.signal.org`, `cdn*.signal.org`, `sfu.voip.signal.org`; the list is checked in Phase 0) or loopback port 3100.
- **Prompt injection:**
  - **Who can start a turn:** only the owner. A web page, mail or file the agent reads can try to steer the agent, as in any session.
  - **Where answers go:** only to the owner's conversation, and the bridge never sends anywhere else. The agent has no tool that reaches Signal.
  - **What a hijacked agent can do:** the same as in the app. It runs in the Ask folder's sandbox, behind approvals the owner answers from the phone.
- **Keys:** they live only in `/var/lib/mightling-signal`, mode 0700, owned by a uid the agent does not run as. `ProtectHome=yes` cuts the other direction too.
- **Losing the phone:** the bridge stops when the identity key changes (§5.3). The registration lock keeps the dedicated number from being taken over. `ling signal stop` from any shell on the node stops it at once.
- **What the bridge logs:** to the journal, it writes events ("turn started", "approval declined", "stranger ignored"), never message text.

---

## 11. The air gap

- **When it is checked:** every five seconds the bridge asks `ling web` for the user-level level (`GET /api/airgapped`, behind the device cookie like every route). The bridge cannot read the user's configuration itself, because `ProtectHome=yes` and its own uid keep it out of the home folder. `ling web` resolves the level with the same crate the sandbox helper uses (`ling-rs/airgapped`): `DREAMFERENCE_MIGHTLING_AIRGAPPED`, then the strictest of the file `DREAMFERENCE_CONFIG_PATH` names and the user-level file. No session's level is consulted.
- **At `on`:** it starts no turn and sends nothing. It answers nothing, because answering would itself be a send.
- **When the level drops back to `off`:** it says once "Mightling was air-gapped from <time> to <time>; messages from then were not read. Send again what you need."
- **Session-level air gap:** a thread whose own level is `on` (set with `/airgapped on` in the app) is not used by the bridge. `/use` refuses it, saying why.

**Why the check runs in the bridge:** air-gap `on` promises that nothing of the session leaves the machine. Signal is a way out, so the bridge must ask the same question the sandbox helper asks.

---

## 12. Phase 0: measurements before building on them

Each item is answered on this machine (gx10-1a2b), not on node-b (that machine is for ling-engine alone):

1. **Does signal-cli run on arm64:** JRE 25 from apt, with the exquo `libsignal_jni.so` for the bundled libsignal version? Verified by `signal-cli --version` and by `signal-cli link` reaching the provisioning step (it prints a `sgnl://` link; nothing is linked).
2. **The JVM's memory:** resident memory idle and while receiving, which sets `MemoryMax`.
3. **Note to Self sync:** what the sync-message shape and loop behaviour are for the linked mode (§4.2). This needs the owner's phone.
4. **Option names:** the exact names for discovery, PIN and disappearing timers in the pinned signal-cli (`updateAccount`, `setPin`, `updateContact --expiration`).
5. **Long messages:** whether signal-cli sends a message over 2,000 characters as Signal's long-text attachment by itself. If it does, the splitter's limit can rise.
6. **Identity changes:** how a changed identity key appears under `--trust-new-identities on-first-use`. Does the message still decrypt, and what does `listIdentities` say?
7. **Signal's hosts:** what the daemon connects to in a minute of idle and a minute of traffic (strace), for the audit's allow-list.

---

### 12.1 Phase 0 results (2026-10-08, gx10-1a2b)

- **Item 1, arm64: works.**
  - **What was run:** signal-cli 0.14.9 with Java 25.0.4 (Temurin, in a scratch folder; Ubuntu's `openjdk-25-jre-headless` 25.0.4 was the package setup installed then; since §17 setup fetches the pinned Temurin JRE instead) and exquo's `libsignal_jni.so` 0.103.0 for aarch64, the version the release bundles as `libsignal-client-0.103.0.jar`.
  - **How the library is found:** `-Djava.library.path=<dir>` through `JAVA_OPTS`, with no change to the jar.
  - **Result:** `signal-cli --version` answered, and `signal-cli link` reached Signal's provisioning service and printed a `sgnl://linkdevice?…` link. Nothing was linked; the process was stopped there.
- **Item 2, memory: about 190 MB** resident for the JVM at the link step, with `-Xmx256m -XX:+UseSerialGC`. Idle and receiving are still to be measured with an account. `MemoryMax=768M` stays until then.
- **Item 4, option names: confirmed** from signal-cli 0.14.9's man page:
  - `register [--voice] [--captcha …]` and `verify CODE [--pin PIN]`;
  - `setPin PIN`, a registration lock that, per the man page, "resets after 7 days of inactivity". An always-on bridge stays active.
  - `updateProfile --given-name`, `updateAccount --discoverable-by-number false` and `updateContact -e/--expiration SECONDS`;
  - `trust -v SAFETY_NUMBER`, and `listIdentities [-n RECIPIENT]`;
  - `jsonRpc --receive-mode on-start --ignore-attachments --ignore-stories`;
  - JSON-RPC parameter names "generally match the long CLI parameter names" in camelCase (`textStyle`, `targetTimestamp`).
- **Item 3, the Note to Self shape: read from signal-cli 0.14.9's source.** It is not observed yet. `JsonSyncDataMessage` writes `destinationNumber`, `destinationUuid` and the message's own fields beside them. `link` prints the URI, draws a QR only when it owns a terminal, and ends with `Associated with: <number>`. `listAccounts` gives `{number, aci}`, and `listDevices` marks `(this device)` in its text output only.
- **Items 5, 6 and 7 are open:** long messages, how a changed key appears in dedicated mode, and the hosts signal-cli connects to.
- **Linked mode adds four open items:**
  - whether signal-cli accepts a message-history transfer;
  - the exact log line when the device is unlinked;
  - whether `updateContact <own number> --expiration` sets Note to Self's timer;
  - the shape of `getAttachment`'s answer.

  All need the owner's phone.

## 13. Tests

All without Signal, without a network, without `ling web` running, and without sudo:

- **Gate (§5.1):**
  - a stranger is dropped;
  - a group message is dropped;
  - the owner's number with a different ACI is dropped;
  - a changed fingerprint is dropped and counted once;
  - a stale or duplicate timestamp is handled as specified;
  - the pairing code binds the first sender only, and only within its window.
- **Commands (§5.2):** every command, and YES/NO only while an approval is pending.
- **Formatter (§9):**
  - each Markdown form;
  - ranges in UTF-16 units, emoji and CJK included;
  - splitting at paragraph, line and space, with monospace reopened across parts;
  - the attachment fallback.
- **The conversation loop, with a fake Signal side and a fake agent side:**
  - a question becomes a thread and a turn;
  - a second question is queued;
  - `/stop` interrupts;
  - `/new` starts a thread;
  - an approval is relayed, answered and timed out;
  - air-gap `on` stops everything and `off` resumes with the notice.
- **signal-cli's JSON-RPC:** a scripted stand-in process (a shell script echoing recorded lines) checks the framing, the request ids and the `receive` notification parsing.
- **The unit file:** generated text against a fixed expectation: `ProtectHome`, `User`, `MemoryMax`, no `ExecStartPre` that runs as root.

---

## 14. Later

- **Voice notes:** Signal voice messages (AAC) go to `ling web`'s `/api/transcribe` (ASK §7), once it exists, and are answered as text. Answering in voice (text-to-speech) is not planned.
- **Files out:** a `ling web` route that serves a file from an Ask folder to a paired device, so the bridge can send what the agent wrote ("here is the report: report.pdf").
- **Work threads:** `/work <project>` starts a Work thread in a repository the owner listed in `[signal] projects`. It uses the default prompt and the workspace-write sandbox, with approvals as in §8. It never offers Full Access.
- **Night Shift from the phone:** `/night <task>` queues a Night Shift task (`ling night add`), and the morning report comes back as a message.
- **A native client:** evaluate presage (Rust, on Signal's libsignal; both AGPL-3.0, compatible with Mightling's licence) to replace the JVM. Ship it if it is maintained, passes the same tests and handles the same edge cases.
- **Other messengers:** Matrix, through the same bridge shape. WhatsApp and Telegram are out of scope: their bot APIs pass messages through the company's servers in readable form.

---

## 15. The user's answers (2026-10-08)

1. **Number:** no new number. The bridge is linked to the owner's own account and talks in Note to Self (§4.2).
2. **Approvals:** YES or NO from the phone, as designed (§8).
3. **Machine:** gx10-1a2b.
4. **Disappearing messages:** one week, the default. In Note to Self that timer also covers the owner's own notes, so setup asks first (§4.2).

---

## 16. What was built (branch `features/signal`, 2026-10-08)

The crate `ling-rs/signal` (`ling-signal`, library and binary). Like `ling-web-server`, its tests run in a copy of the folder (`cargo test`), with no Codex workspace, no network beyond loopback, no Signal and no sudo: 61 unit tests and one end-to-end test.

| Module | What it does |
|---|---|
| `envelope.rs` | Reads a `receive` notification: a direct message, Note to Self (linked mode), a group, or anything else |
| `gate.rs` | §5.1 and §5.3. The owner by ACI and fingerprint, never by number. Strangers counted, never answered. A changed key refused. Stale and duplicate messages. The pairing code binds the first sender with a key within ten minutes |
| `command.rs` | §5.2. YES and NO mean something only while an approval is pending |
| `format.rs` | §9. Markdown to text plus style ranges in UTF-16 units, emoji and CJK included. Splitting at paragraph, line or space without cutting a surrogate pair, with a range across a cut closed and reopened. More than eight parts becomes `answer.md` |
| `bridge.rs` | The conversation as a pure state machine. Questions open or resume an Ask thread; one runs at a time, five are queued. `/stop` interrupts (also before the turn id is known), and `/new`, `/threads`, `/use N`, `/status` and `/help` work as specified. Approvals are relayed one at a time and declined after ten minutes; permission requests are always declined. Typing is renewed every 10 s; a progress note comes at one minute, then every five. The model-server hint. Air gap `on` sends nothing (not even a receipt), and `off` says what was missed. A lost connection is reported once after 10 s, and a question that was opening its thread is asked again |
| `rpc.rs` | signal-cli's JSON-RPC on stdio: request ids, `receive` notifications, `send` with `textStyle` and attachments, typing, read receipts, and the trusted fingerprint from `listIdentities` |
| `agent.rs` | `ling web` as a paired device. `POST /pair` for the cookie, `/ws` with the same envelope as `ling web ask`, `/api/upload` and `/api/airgapped`. The translator from app-server messages to the conversation's events. Server requests other than approvals get a JSON-RPC error, so a turn never hangs on one |
| `state.rs` | `bridge.json`, `conversation.json`, `device-cookie` (each 0600, replaced whole) and the public `status.json` |
| `unit.rs` | The unit of §3, plus `RuntimeDirectory=` for the status file. A test checks that nothing in it runs as root |
| `serve.rs` | The daemon. It carries out the state machine's actions, reconnects to `ling web` with backoff, polls the air gap and sets the disappearing timer once the owner pairs |
| `setup.rs`, `main.rs` | `ling-signal setup --number +… [--voice] [--dry-run]` prints every step before any sudo: the system account, Java 25, signal-cli 0.14.9 and the arm64 libsignal 0.103.0, both checked against pinned SHA-256s before they are unpacked as root, the bridge in `/usr/local/lib/mightling`, the 0700 state folder and the unit. It then registers (CAPTCHA, SMS or voice code), sets the registration lock, the profile name and number discovery off, pairs with `ling web pair`, writes `bridge.json`, prints the owner code and waits for it. `status`, `start`, `stop` and `unit`. `init`, `pair` and `bind` are for the bridge's own account |

**`ling web`:** new route `GET /api/airgapped` (§11), behind the same credential as every route; its test is in `tests/server.rs`. The web crate's suite passes, 22 unit and 11 server tests.

**The end-to-end test** (`tests/daemon.rs`) runs the real daemon against two stand-ins:
- **signal-cli:** a shell script that logs every request and delivers a stranger's message, then the owner's.
- **`ling web`:** a WebSocket server in the test that plays one Ask thread.

It checks:
- the owner's question becomes `thread/start {prompt: "ask"}` and one `turn/start` with the owner's words;
- the answer reaches signal-cli as one `send` to the owner, with `textStyle ["14:1:BOLD"]`;
- the owner sees a read receipt and typing;
- nothing goes to the stranger, who is counted once;
- `conversation.json` keeps the thread, and `status.json` names neither the stranger nor the text.

**Linked mode** is built (2026-10-08), the default:
- `setup` links by QR code, prints the warning of §4.2 and asks about Note to Self's timer;
- the gate reads Note to Self alone and keeps nothing of other conversations;
- the marker and loop guard; sending with `noteToSelf`, without receipts or typing;
- attachments with `getAttachment`;
- unlink detection with exit 78;
- `remove` never runs `unregister`, and `trust` has nothing to do.

Its end-to-end test hands the daemon five messages:
- a friend's message;
- the owner's reply to the friend;
- a group message;
- a marked echo;
- one Note to Self question.

It checks that:
- exactly one `send` goes out, with `noteToSelf: true`, no recipient, the marker and the style moved to `17:1:BOLD`;
- no receipt, typing indicator, `listIdentities` or `getAttachment` call is made;
- one turn is started, with the note's words;
- `conversation.json` remembers only the note's timestamp;
- no text or id of the other conversations is in any file.

Tests: 69 unit tests and three end-to-end tests (dedicated, air gap on, linked).

**Not built** (as of this section; §17 adds the routing, the packaging and `setup --refresh`):
- **CI building libsignal's JNI library for arm64** (§3); exquo's build stays pinned.
- **Egress audit:** `audit egress --signal`, the trace of the unit's own connections. The note is built: when `mightling-signal.service` is enabled, every `ling-admin audit egress` report ends with "ℹ️ Declared exception: Signal bridge enabled …" (`EgressAudit.declared_exceptions`, tested).
- **Phase 2:** everything in §14.

A second end-to-end test runs the daemon with `ling web` answering `on`. The owner's message then gets no send, no receipt and no typing, and no thread or turn is requested.

**Not verified:**
- **Anything against Signal itself.** That needs a dedicated number, and the owner's phone for the code.
- **The JSON-RPC parameter shapes beyond the man page:**
  - `sendReceipt` with a single `recipient` string and `targetTimestamp` as an array;
  - `sendTyping` with a `recipient` array;
  - the field names of `listIdentities` (`uuid`, `fingerprint`, `safetyNumber`, `trustLevel` with `TRUSTED_VERIFIED`/`TRUSTED_UNVERIFIED`).

  The stand-in accepts any shape. A mismatch would show up only as a logged error, or, for `listIdentities`, as the owner's messages being refused.

**Verified without installing anything** (superseded by §17, which replaced the apt package with a pinned Temurin JRE): the Java home `setup` used then, `/usr/lib/jvm/java-25-openjdk-arm64`, is the directory Ubuntu 24.04's `openjdk-25-jre-headless` 25.0.4.1 (arm64) installs to. This was read from the package's contents with `apt-get download` and `dpkg -c`.

## 17. Merged, off by default (branch `messengers/optional`, 2026-10-08)

The user decided that the messenger bridges ship with Mightling, are off by default and are turned on per machine by a command. This section records what merging into `main` changed.

**One command surface: `ling signal …`.**
- The launcher links the crate under `cfg(target_os = "linux")` and routes `ling signal …` to `ling_signal::cli::run` (`ling-rs/src/signal.rs`), on a blocking thread so the commands' own runtimes never nest in the launcher's. Elsewhere it says the bridge runs on the Linux node only.
- The command line moved from `main.rs` to `cli.rs`, so both ways in run the same code. `main.rs` is now the small `ling-signal` binary, kept on purpose. The system account runs it from `/usr/local/lib/mightling` (users' homes are 0750). Running the 300 MB `ling` as a system account would also bring its home folder and its rename migration along. The binary also accepts the bridge account's own commands (`serve`, `init`, `retool`, `pair`, `bind`, `trust-owner`, `account`), which `ling signal` refuses.
- Setup takes the bridge to install from beside the resolved `ling`. Before this change it took its own executable, which from `ling` would have installed `ling` itself. When the bridge is not installed, setup says how to get it and changes nothing.
- User-facing text says `ling signal …` everywhere.

**Release packaging:**
- `ling-signal` is built in the same Cargo run as `ling` on Linux (`BUILDS_SIGNAL` and `SIGNAL_PACKAGE` in `codex_branded_builder.py`). It is a workspace member through the launcher's dependency, so it adds no second copy of any crate. `is_current` requires it on Linux.
- The release workflow gzips it as `ling-signal-<target>.gz` and lists it in `ling-<target>.sha256sums`. It also runs `cargo test --release -p ling-signal -p ling-chat` in the build's export.
- `scripts/package_mightling.sh`, `install.sh` (Linux) and `ling update` (`update::SIGNAL_COMMAND`, an optional command) install it beside `ling`. None of them links it onto PATH, and nothing runs it until `ling signal setup`.

**`setup --refresh`** (`setup::refresh_plan`):
- copies the bridge again;
- fetches whatever pinned runtime is not installed yet;
- rewrites the settings' signal-cli path, version and environment as the bridge's account (`retool`);
- rewrites the unit and runs `systemctl try-restart`.

The account, the keys and the pairing are left alone. `status` already pointed at this command whenever the two versions differ.

**Java is pinned, not installed with apt.**
- The JRE is Eclipse Temurin 25.0.4.1+1 (`OpenJDK25U-jre_<arch>_linux_hotspot_25.0.4.1_1.tar.gz`):
  - aarch64 SHA-256 `34828cbb93ed31c281c84ecb31ddab655d11a802f263c1fc019d42e9e0230fed`;
  - x86_64 SHA-256 `1731a34baadec5479258ea0202e4d5d865d2efeee60cb0c7d7eb056fe96ca219`.
- Both hashes were computed from the downloaded archives. They match the `.sha256.txt` files Adoptium publishes beside them.
- Setup downloads the JRE and checks it as the user, then unpacks it as root to `/opt/mightling/jdk-25.0.4.1+1-jre`, as it does for signal-cli. `JAVA_HOME` points there.
- `remove` deletes it with signal-cli.
- Nothing is bundled. The download follows the same pattern `ling-admin docs setup` uses for ling-docs's runtime.
- qrencode, which only draws the QR code in the terminal, is still an apt package. It is installed only when it is missing.

**Off by default:**
- No installer step, timer or other command creates the account, the unit or the state.
- `stop` now disables the unit as well, so a reboot does not bring the bridge back.
- The egress audit names the bridge only while its unit is enabled.

**Verified (2026-10-08, this machine, no Signal network):**
- **The launcher workspace compiled in a scratch export of the pinned Codex** (`prepare_source` + the pinned V8, `nice -n 10`, `-j 8`), with `codex` and `ling-signal` built together.
- **Tests, on the tree merged with `main`:**
  - `ling-launcher`: 230 passed (1 ignored), including the routing and `update` tests for `ling-signal`;
  - `ling-signal`: 73 unit tests and 3 end-to-end tests;
  - `ling-chat`: 47;
  - `ling-web-server`: 22 unit and 12 server tests;
  - the Python suite: 954 passed, with the one failure `main` already has in `test_codex_branded_builder.py`.
- **The built `ling`, run with a scratch `HOME`:**
  - `ling signal help`, `setup --dry-run` and `setup --refresh --dry-run` printed the plans above;
  - without `ling-signal` beside it, setup said how to get one and changed nothing;
  - `ling signal serve` was refused;
  - `ling chat status` answered, and `ling chat start` refused because nothing was set up.
- **signal-cli 0.14.9 on the pinned JRE:** it starts with the arm64 JNI library on its library path, inside an empty network namespace (`unshare -rn`). `--version` and `listAccounts` both answered.
- **The pins:** all three archives (signal-cli, libsignal for arm64, the JRE) matched their pins when downloaded.

**Not verified:**
- Anything against a real Signal account.
- `ling signal setup` and `--refresh` past `--dry-run`, which would need sudo and a phone.
- A release build of the new asset in CI.

### 17.1 The signature check on signal-cli (2026-10-09)

Setup now checks the signal-cli tarball twice before anything is unpacked as root: against the pinned SHA-256, as before, and against the maintainer's detached OpenPGP signature. Both must pass, and either failing stops setup with nothing unpacked.

**The steps** (`setup::runtime_steps`, so `setup` and `setup --refresh` both get them, and `--dry-run` prints them):
1. `curl` the tarball, then `sha256sum -c` against `SIGNAL_CLI_SHA256`.
2. `curl` `signal-cli-<version>.tar.gz.asc`, published beside the tarball on the release.
3. `<bridge> verify-signal-cli --file <tarball> --signature <tarball>.asc`, as the user. `<bridge>` is the `ling-signal` binary that setup is about to install. The key and its fingerprint are compiled into it, so no keyring or key file is written anywhere.
4. Only then `sudo tar -xzf`.

The plan's text says so: "Every download is checked before it is unpacked; signal-cli twice, by its pinned SHA-256 and by its maintainer's OpenPGP signature". The step itself is listed as "Check the signature with the maintainer's pinned key (FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570)".

**The pinned key:**
- `release_signature::SIGNAL_CLI_KEY_FINGERPRINT` is `FA10 826A 7490 7F9E C6BB  B7FC 2BA2 CD21 B5B0 9570`: AsamK `<asamk@gmx.de>`, RSA-2048, created 2016-06-18, sign and certify, no expiry. Its one subkey (`03D1 D926 31C7 91DC 3C72  9435 C25C DEA2 A548 BC9E`) is for encryption only.
- The armored key is `ling-rs/signal/keys/signal-cli-AsamK.asc`, embedded with `include_str!`. It is keys.openpgp.org's export: self-signatures only, 1.8 KB.
- The key file is data and the constant is the pin. A key file whose primary fingerprint is not the constant is refused before any signature is looked at.

**Where the fingerprint came from, and how it was cross-checked (2026-10-09):**
- **The `.asc` itself.** `signal-cli-0.14.9.tar.gz.asc` names its issuer as `FA10826A…B5B09570` (issuer-fingerprint subpacket; RSA, SHA-512, binary document, made 2026-10-07). This says which key to look for. It proves nothing on its own.
- **keys.openpgp.org**, looked up by that fingerprint. It serves the key with the user id `AsamK <asamk@gmx.de>`. keys.openpgp.org publishes a user id only after its address has confirmed it by email, so this source rests on the maintainer's mailbox, not on GitHub.
- **keyserver.ubuntu.com:** the same key, with the same user id and subkey.
- **`github.com/AsamK.gpg`**, the key on the GitHub account that publishes the releases: the same key. This is not independent of a compromise of that GitHub account.
- **The release tag.** GitHub reports the annotated tag `v0.14.9` (tag object `fabfcecb…`) as signed and verified. Its signature names the same issuer fingerprint.
- **gpgv.** With only this key in a temporary keyring, `gpgv` reported a good signature on the downloaded tarball (`VALIDSIG FA10826A…B5B09570`, primary key `FA10826A…B5B09570`). That tarball also matched the existing SHA-256 pin.
- signal-cli's README does not name a signing key; it was checked and says nothing about one. The two independent roots are therefore the keys.openpgp.org email confirmation and the GitHub account (its key list and its verified tag).

**The rules** (`ling-rs/signal/src/release_signature.rs`). Mightling sets these on top of the OpenPGP library's own checks, and anything else fails closed:
- The embedded key's primary fingerprint equals the pin, and its self-signatures (`verify_bindings`) hold.
- The `.asc` is one armored block with nothing after it, and the block holds exactly one signature. The parser would otherwise read the first block and ignore the rest, and "one of two signatures is good" is not accepted.
- The signature is of a binary document and is hashed with SHA-256, SHA-384, SHA-512, SHA3-256 or SHA3-512 (the real one uses SHA-512). A SHA-1 signature is refused even when it is otherwise good.
- The signature names its issuer by fingerprint, and that fingerprint is the pin. The library would otherwise accept a match on the 64-bit key id.
- It verifies against the **primary key only**. A signing subkey added to the key file cannot stand in for it.
- The tarball is streamed through the hash: 124 MB, about a second in a release build.

**Why rpgp, not gpgv or Sequoia:**
- **rpgp** (the `pgp` crate, 0.21, default features off, so no bzip2) is pure Rust and builds the same on aarch64 and x86_64 Linux. It needs no system tool at setup time.
- A clean release build of the crate alone took 22 s on this machine (`-j 8`, `nice`, with the SWE-bench run going). In the Codex workspace it adds 39 crates the workspace did not have: RSA, DSA, the older OpenPGP ciphers, `derive_builder` and `snafu`. It adds no second version of any crate the workspace already has (the workspace `Cargo.lock` before and after, compared).
- **gpgv** with a temporary keyring was the fallback. It is present wherever apt is, since apt depends on it, and it would add no build time. But it needs a dearmored binary keyring written at setup time and its `--status-fd` output parsed for `VALIDSIG`. It would also be one more external program whose version decides what is accepted. The crate's cost was small enough not to need it.
- **Sequoia** (`sequoia-openpgp`) is the heaviest of the three: a larger dependency tree, and a crypto backend to choose (Nettle by default, a C library). It was not tried.

**Tests:**
- `release_signature` has six tests:
  - a good signature by the pinned key passes, with the pin compared without spaces or case;
  - a bad signature fails: an altered payload, a corrupted signature, an empty file and text that is not a signature;
  - a signature by another key fails. The same goes for that key's file under the first key's pin, and for one block holding two signatures or two blocks;
  - a SHA-1 signature fails;
  - the embedded key has the pinned fingerprint, and the real 0.14.9 `.asc` is binary, SHA-512 and issued by the pin;
  - missing files fail.
- The fixtures are in `ling-rs/signal/testdata/`: two throwaway RSA-2048 keys made with gpg for the tests (`@example.invalid`), a 28-byte payload, their signatures, and the real 488-byte `.asc`. `cli` checks that `verify-signal-cli` exits 2 without its arguments and 1 on a missing or wrong file. `setup`'s plan tests check the order: download, SHA-256 check, `.asc` download, signature check, unpack, with the check run as the user.

**Verified (2026-10-09, this machine):**
- `ling-signal`: 80 unit tests and 3 end-to-end tests. They passed in a standalone copy of the crate and with `cargo test --release -p ling-signal` in a scratch export of the pinned Codex workspace with the patches applied.
- The built binary, run with a scratch `HOME`, on the real signal-cli 0.14.9 tarball:
  - `verify-signal-cli` passed;
  - on a truncated copy it failed with "the signature does not verify";
  - with a test key's `.asc` it failed with "the signature is by 9B87…, not by the pinned key".
- `setup --dry-run` printed the new steps in that order.

**When `SIGNAL_CLI_VERSION` moves**, the `.asc` comes from the new release. Setup still checks it against the same pinned key, so a release signed with another key fails closed until the pin is changed here, on the same evidence as above.
