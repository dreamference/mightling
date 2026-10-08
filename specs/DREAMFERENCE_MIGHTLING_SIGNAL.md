# Mightling over Signal: `ling signal`

**Status:** proposed (2026-10-08). The bridge's core is built and unit-tested on branch `features/signal` (§16); nothing has run against a real Signal account. The user asked for this on 2026-10-08 ("can I chat with ling using signal? … write a spec for this").
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
4. **One owner, bound by a code, not by a phone number typed in.** `ling signal setup` shows a one-time code. The first account that sends it from Signal within ten minutes becomes the owner, and its account id (ACI) and identity key are recorded.
   - **Strangers:** messages from anyone else are dropped without a reply, so the number does not reveal that a bot answers it.
   - **A changed identity key:** messages from the owner stop being accepted until the owner re-trusts on the node (§5.3). This is what a SIM swap or a re-registered phone looks like.
5. **A dedicated number is the recommended mode; linking to the owner's own account is the second.**
   - **Dedicated (Phase 1):** a second Signal account (prepaid SIM or landline) that the owner talks to like a contact.
   - **Linked (Phase 2):** the bridge becomes a linked device of the owner's account, and the owner talks to it in **Note to Self**. It costs nothing, but the bridge then receives every message of the owner's account. That is why it waits for Phase 0's measurements and comes with a warning (§4.2).
6. **Signal needs the internet, so the bridge runs only at `/airgapped off`.**
   - **Never at `on`:** at a user-level `on` the bridge refuses to start. A running bridge stops answering, and says why once, when the level changes.
   - **One external exception:** the bridge is the one Mightling component that talks to an outside service on its own. It talks only to Signal's servers, and only when the user turned it on. `ling-admin audit egress` names it as an enabled exception instead of passing silently (§10).
7. **Off by default.** No installer step turns it on. `ling signal setup` is the only way in, and it says what it changes before any sudo.
8. **signal-cli first, a native client later.**
   - **Phase 1:** the bridge drives **signal-cli** (GPL-3.0, Java), the maintained command-line Signal client, over JSON-RPC on stdio.
   - **Later:** a native Rust client (e.g. presage on libsignal, both AGPL-3.0) could remove the JVM, after an evaluation (§14).
9. **Text first.** Phase 1:
   - carries text both ways;
   - takes images and files *in* through `/api/upload`;
   - relays approvals as yes/no questions.

   Voice notes (through the speech sidecar), files *out*, Work threads on repositories and the linked mode are Phase 2.

---

## 1. What the owner sees

```
Owner (phone)                          Mightling (dedicated number)
───────────────────────────────────    ─────────────────────────────────────────────
What changed in the SGLang release
notes this week?                       ✓✓ (read)   … typing …
                                       Three things matter for us: … [1][2]
                                       Sources: [1] github.com/… [2] …
/new
                                       New thread. (The last one stays in your history.)
Here's a photo of the error  [image]   ✓✓   … typing …
                                       That traceback is from …
                                       ⚠️ Mightling wants to run:
                                         pip download torch==2.9 --no-deps -d .
                                       in this question's folder. Reply YES or NO.
YES                                    … typing …   Done: …
/stop                                  Stopped.
```

- **Each message from the owner is one turn.** While a turn runs, the bridge keeps the typing indicator alive.
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
| Java runtime | `openjdk-25-jre-headless` (apt) | signal-cli 0.14 needs Java 25 or later. Ubuntu 24.04 has it (25.0.4 on arm64, checked 2026-10-08) |
| signal-cli | `/opt/mightling/signal-cli-<version>/` | The release tarball, checked against the `.asc` signature from signal-cli's release and a SHA-256 pinned in Mightling's source |
| libsignal JNI for arm64 | `/opt/mightling/signal-cli-<version>/lib/libsignal_jni.so`, put inside the `libsignal-client-<v>.jar` | signal-cli bundles it only for x86_64 Linux, Windows and macOS (its wiki, "Provide native lib for libsignal") |
| The bridge | `/usr/local/lib/mightling/ling-signal` (root, 0755) | Users' homes are 0750 on Ubuntu 24.04, so the system account cannot run a binary from the user's install |
| The unit | `/etc/systemd/system/mightling-signal.service` | A system unit: no lingering needed, survives logout |
| State | `/var/lib/mightling-signal/` (0700, `StateDirectory=`) | signal-cli's data, the bridge's state, the device cookie |

**The arm64 libsignal library:**
- **Phase 1:** use `exquo/signal-libs-build`'s `libsignal_jni.so-v<ver>-aarch64-unknown-linux-gnu.tar.gz` for the exact version the signal-cli release bundles. The version is read from the `libsignal-client-<ver>.jar` name in its `lib/`. The file is pinned by SHA-256 in `ling-rs/signal/pins.json`.
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
- **Memory:** `MemoryMax=768M` with a 256 MB heap keeps the JVM inside a fixed budget. Unified memory makes a runaway JVM a host problem (CLAUDE.md, host safety). The real footprint is measured in Phase 0 and the cap set from it.
- **No access to home folders:** `ProtectHome=yes` means the bridge can never open a file of the user's. Attachments reach the Ask folder through `ling web`'s `/api/upload`, which owns that check.

**Updates:**
- **The bridge binary:** `ling update` refreshes the user's binaries, not `/usr/local/lib/mightling/ling-signal`. When the two versions differ, `ling signal status` and the bridge's own `/status` say so, and `ling signal setup --refresh` copies the new one (sudo).
- **signal-cli:** updates are a pin change in Mightling's source, picked up by the same `--refresh`. Signal retires old clients, so an outdated signal-cli eventually stops working. `ling signal status` warns when signal-cli reports that it is deprecated.

---

## 4. The two account modes

### 4.1 Dedicated number (Phase 1, recommended)

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

### 4.2 Linked to the owner's account (Phase 2)

`ling signal setup --linked`:
- runs `link -n "Mightling"` and shows the `sgnl://linkdevice?…` link as a QR code in the terminal;
- the owner scans it in Signal → Settings → Linked devices;
- the owner then talks to Mightling in **Note to Self**, and the bridge answers there.

**What Phase 0 must establish before this ships:**
- that a message the owner sends to Note to Self from the phone reaches the linked device as a `syncMessage.sentMessage` whose destination is the owner's own account;
- that the bridge's own replies (sent from its device id) come back as sync messages and are ignored, so they never loop.

**The warning setup prints:** "A linked device receives *every* message of your Signal account. Mightling reads only Note to Self and discards the rest, and its keys are kept where the agent can't read them. Use a dedicated number if you'd rather it never see the rest." In this mode signal-cli runs with `--ignore-attachments`, so other conversations' attachments are never downloaded. The owner's own attachments are fetched one by one with `getAttachment`.

---

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
| `ling signal setup [--linked] [--number +…]` | §3 and §4: install, register or link, bind the owner, pair with `ling web`, start the unit |
| `ling signal setup --refresh` | Copies the current bridge binary and the pinned signal-cli again |
| `ling signal status` | Running or not, mode, the owner's last message time, versions, messages ignored, the last error |
| `ling signal trust` | §5.3 |
| `ling signal stop` / `start` | The unit |
| `ling signal remove` | Stops and disables the unit, unregisters (dedicated) or unlinks (linked), revokes the `ling web` device, and deletes `/var/lib/mightling-signal` and the account. Asks for confirmation; the Ask threads stay |
| `ling-signal serve --state DIR` | The daemon itself: what the unit runs; nobody types it |

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

- **When it is checked:** the bridge reads the user-level air-gap level through the resolver crate (`ling-rs/airgapped`), with the user's config paths given at setup, before every send and every turn.
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

## 15. Questions for the user

1. **Dedicated number or linked:** do you have a second number for Mightling (a prepaid SIM, a landline, an eSIM)? If not, Phase 2's linked mode is the only way, and it waits for Phase 0's check with your phone.
2. **Disappearing messages:** keep the default timer of one week?
3. **Approvals from the phone:** may an approval be answered from Signal at all, or should every turn from Signal run with approvals declined (read and answer only)?
4. **Which node:** run the bridge on gx10-1a2b, the production node, since node-b is for ling-engine only?

---

## 16. What was built (branch `features/signal`, 2026-10-08)

Filled in as the work lands; see the branch's last commit.
