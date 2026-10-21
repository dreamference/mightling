# Mightling Chat: talking to Mightling from a phone messenger

**Status:** Phase 1 implemented and merged into `main` on 2026-10-08, off by default (§14 what was built, §15 the merge: `ling chat …`, `ling-admin matrix …`, stop disables the units). Tested against stand-ins only: nothing has run against a real Telegram bot, a real Matrix client, Element X or Tailscale. Planned for 1.6.0. Proposed 2026-10-08.

**Spec owner:** the user. **Depends on:** MIGHTLING_ASK (Ask threads, `ling web`, the bridge policy), MIGHTLING_NODE (advertised nodes), MIGHTLING_AIRGAPPED, DOCKER §6 (sidecars).

---

## 0. Decisions, stated first

- **Two messengers, two levels of privacy (the user, 2026-10-08):**
  - **Matrix, private.** A Matrix homeserver runs on the node, and the phone reaches it over a Tailscale VPN. Messages and answers never leave the user's machines.
  - **Telegram, less private.** It is for users who accept that their messages and answers pass through Telegram's servers, where bot chats are not end-to-end encrypted. It is off until the user turns it on, and turning it on says exactly that and asks for a yes.
- **One bridge, two adapters.** `ling chat` is one process with a Matrix adapter and a Telegram adapter. Everything that is not messenger-specific is shared and tested once: threads, approvals, commands, rendering, splitting and access control.
- **A chat is an Ask thread** (MIGHTLING_ASK §3): no repository, a scratch folder, the `ask` prompt, and the same sandbox as an Ask thread in the browser. Threads started from a phone appear in the web UI and the desktop app, because they live in the same `ling app-server`.
- **The bridge is a client of `ling web`, never of the app-server directly.** Every message it sends passes the same bridge policy as a browser tab: allow-lists, dropped fields, named prompts and Ask folders. The policy stays in one place.
- **The bridge is a separate process, not part of `ling web`.** `ling web` promises it "calls nothing on any network". The Telegram adapter must reach `api.telegram.org`, so it lives elsewhere, and a crash in a messenger adapter cannot take the web UI down.
- **No open port for Telegram.** The adapter long-polls `getUpdates`, so no webhook and nothing reachable from the internet.
- **The private homeserver cannot reach the internet at all.** It runs on an internal Docker network (§5.2). This is enforced, not configured: no federation, no URL previews, no push gateway, because no route exists.

## 1. What the user gets

From the phone, in Element X (Matrix) or Telegram, a private chat with **Mightling**:

- **Questions and answers in a conversation.** Ask a question and get the answer as it is written. Telegram streams it live; Matrix shows "Mightling is typing…" and then the answer. The agent can search the web (at `/airgapped off`), read the user's files (`docs_*`), mail, Drive and Calendar (`/apps`), exactly as an Ask thread can.
- **Commands,** the same in both messengers:

| Command | What it does |
|---|---|
| `/new` | The next message starts a new Ask thread. Without it, messages continue the current one. |
| `/stop` | Interrupts the running turn (`turn/interrupt`). Telegram's stop button on a streaming answer does the same. |
| `/threads` | The last 10 threads started from this chat, numbered, with their names. |
| `/use <n>` | Continues thread `n` from that list. |
| `/status` | Model server, air-gap level, the current thread, and whether a turn is running. |
| `/cavemode [level]` | How terse this thread's answers are, as on Signal (CAVE_MODE §11.7): the level in force, a level for this thread, or `default <level|built-in>` for new ones. |
| `/help` | This table. |
| `/pair <code>` | Telegram only: pairs this Telegram account (§4.1). |

- **Approvals on the phone.** When the agent asks for something (a command, a file change, more permissions, or an answer to a question), the chat shows the request with **Approve** and **Decline**: buttons in Telegram, ✅/❌ reactions or replying `yes`/`no` in Matrix. Nothing is approved by silence: after 10 minutes the bridge declines and says so.
- **A message during a running turn** steers it (`turn/steer` with `expectedTurnId` set to the running turn), as typing into a running session does. If the server refuses the steer, for example because the turn just ended, the message starts the next turn instead.

**Not in Phase 1** (§13): photos, files and voice messages; Work threads (a repository); queuing Night Shift tasks; group chats; end-to-end encryption inside Matrix (§5.4 says why it is not needed for privacy).

## 2. Architecture

```
 Element X ── Tailscale (WireGuard) ── tailscale serve (HTTPS, tailnet name)
                                              │
                                              ▼
                         tuwunel homeserver  (container, internal network: no route out)
                                              ▲  Client-Server API, as the bot account
                                              │
 Telegram app ── Telegram servers ◄── HTTPS long poll ── ling chat serve ──► ling web (127.0.0.1:3100, /ws)
                                                          (user unit)            │  bridge policy, Ask folders
                                                                                 ▼
                                                                          ling app-server ─► model server
```

- **`ling chat serve`** runs as the user unit `mightling-chat.service` (`ling chat start` and `stop`, as `ling web` does). It holds one bridge connection to `ling web` and runs each enabled adapter as a task. One adapter failing, for example Telegram unreachable, logs, backs off (exponential, capped at 5 minutes) and never stops the other.
- **Reaching `ling web`.** The bridge signs in the way `ling web ask` does: it writes a one-time login code into `ling web`'s state folder and trades it for a session cookie (MIGHTLING_ASK §16, `ask_client.rs`). The bridge runs as the user, outside any sandbox, so it can write that folder; a sandboxed command cannot. If `ling web` is not running, the bridge starts it (`ling web start`) and waits up to two minutes.
- **One bridge connection for every chat.** The connection carries every thread's notifications. The bridge routes them by `threadId` to the chat that owns the thread.
- **Reconnecting means signing in again.** Sessions made from one-time codes live in `ling web`'s memory (MIGHTLING_ASK §16), so a restarted `ling web` no longer knows the bridge's cookie. On a closed socket, or a refused upgrade, the bridge:
  1. writes a fresh login code and signs in again;
  2. reopens the bridge and resubscribes (`thread/resume`) to every thread with a turn in flight;
  3. reads back with `thread/turns/list` any turn that ended while it was away, so its answer is still delivered.
- **Night Shift gives way** to a chat turn as it does to a browser tab, because `ling web` keeps the busy marker per connection (MIGHTLING_DESKTOP §8.3).

## 3. The conversation model (shared by both adapters)

- **State.** `~/.mightling/chat/threads.json` maps each chat (`telegram:<chat id>` or `matrix:<room id>`) to its current thread and its last 10 threads. It is replaced whole, written via a temporary file and a rename.
- **A new thread** is `thread/start` with `{"prompt": "ask"}`. The policy makes it an Ask thread with its own scratch folder. The bridge names it from the first message's first 60 characters (`thread/name/set`).
- **A turn** is `turn/start` with the message text. The answer is the concatenation of `item/agentMessage/delta` for the thread, until `turn/completed`. An `error` notification without `willRetry` ends the turn and is reported to the chat in one line.
- **Rendering.** The model writes Markdown. A small renderer turns it into each messenger's format:
  - **What it renders:** bold, italic, inline code, code blocks, links, and headings rendered as bold. Everything else is escaped.
  - **Telegram:** its HTML subset.
  - **Matrix:** `formatted_body` (`org.matrix.custom.html`), with the plain text as `body`.
  - **If the format is refused** (Telegram's "can't parse entities"), the bridge resends as plain text. A failed render never loses an answer.
- **Splitting.** Telegram allows 4,096 characters per message, and the bridge splits Matrix messages at 16,000. Splits fall on a paragraph, then a line, then a word. A code block is closed at the split and reopened in the next part.
- **Approvals.**
  - **What is relayed:** a server request on the chat's thread becomes a chat message showing what is asked (the command, the reason, the files), cut to 1,000 characters.
  - **How it is answered:** the answer is the JSON-RPC response to that request, sent back through `ling web`. `ling web` accepts an answer only to a request it has seen and not yet answered (the policy's pending set).
  - **Who can answer:** only the paired user, in that chat.
  - **No choice in 10 minutes:** the bridge sends the decline payload and says so in the chat.
  - **The payloads,** from the pinned protocol (`app-server-protocol/src/protocol/v2/item.rs` and `permissions.rs`):

| Request | Shown | Approve sends | Decline sends |
|---|---|---|---|
| `item/commandExecution/requestApproval` | `command`, `reason`, `cwd` | `{"decision": "accept"}` | `{"decision": "decline"}` |
| `item/fileChange/requestApproval` | `reason`, `grantRoot` | `{"decision": "accept"}` | `{"decision": "decline"}` |
| `item/permissions/requestApproval` | `reason`, `permissions` (network, file system) | `{"permissions": <the requested profile>, "scope": "turn"}` | `{"permissions": {}, "scope": "turn"}` |
| `item/tool/requestUserInput` | each question's `header`, `question` and `options` | `{"answers": {"<question id>": {"answers": ["<chosen label or typed text>"]}}}` | the same with empty `answers` |

  - **Approve means once.** "For this session" (`acceptForSession`) and policy amendments are not offered from a phone. A question marked `isSecret` is refused with a line telling the user to answer it at a computer, because a secret typed into a messenger stays in its history.
- **Air gap.** The bridge reads the user-level air-gap level (`ling-airgapped`, the user's config file, not a repository's) every minute and at every message:
  - **At `on`,** the Telegram adapter stops relaying: incoming messages are answered with one line saying why, and nothing is sent to the agent. Matrix keeps working, because the whole path stays on the machine.
  - **What the agent sees:** Ask threads follow the air gap as usual, so at `on` the agent has no web search, whichever messenger asked.

## 4. Telegram

### 4.1 Turning it on, and pairing

1. **`ling chat telegram setup`.**
   - **The warning.** It explains, in four lines, that everything sent to the bot and every answer passes through Telegram's servers unencrypted, that this may include mail, files and code the agent reads, and that Telegram keeps the chat history. The user must type `yes`.
   - **The token.** It asks for the bot token from @BotFather, read from the terminal with echo off and never taken as an argument, where it would be visible in the process list. It checks the token with `getMe`, stores it, and prints the bot's `@username`.
   - **Without a terminal,** setup refuses: consent must be typed by a person.
2. **`ling chat telegram pair`** prints an 8-digit code, valid for 10 minutes and for one use. The user sends `/pair <code>` to the bot from Telegram, and the bridge records that Telegram **user id** (a number that never changes, unlike a username).
   - **Codes are stored only as SHA-256 hashes,** as `ling web` stores them.
   - **Ten wrong codes** withdraw every pending code.
3. **`ling chat telegram users`** lists the paired users. **`ling chat telegram remove <id>`** ends one. **`ling chat telegram off`** deletes the token and the pairings.

### 4.2 What the bot accepts

- **Private chats with a paired user only.**
  - **Unpaired senders:** a message from anyone else gets one reply ("This is a private Mightling. Ask its owner to pair you.") at most once a day per sender, and is otherwise ignored.
  - **Groups and channels:** the bot leaves them at once (`leaveChat`).
- **Update types requested:** `message`, `callback_query` and `stopped_message_generation` (`allowed_updates`). Edited messages, inline queries and everything else are ignored.

### 4.3 Streaming

- **Live drafts.** While a turn runs, the bridge calls `sendMessageDraft` with the answer so far, at most once a second and at least every 20 seconds, since a draft lives 30 seconds. The draft carries `can_stop: true`, so Telegram shows a stop button. Pressing it arrives as `stopped_message_generation` and becomes `turn/interrupt`.
- **Before the first text,** an empty draft shows Telegram's own "Thinking…" placeholder.
- **The final answer** is `sendMessage`, which replaces the draft.
- **Drafts cut at 4,096 characters:** the draft shows the last 4,096 characters, and the final message is split (§3).
- **If `sendMessageDraft` is unavailable** (a Bot API error naming the method), the bridge falls back to `sendChatAction typing` every 4 seconds and the final message.
- **Commands** are registered with `setMyCommands` at start, so Telegram offers them on `/`.

### 4.4 Network

- **Destinations:** `https://api.telegram.org` only, with `getUpdates` long-polling (`timeout` 50 s). No webhook and no listening port.
- **Rate limits:** a `retry_after` answer is honoured.
- **Telegram unreachable:** the adapter backs off and retries. Matrix is unaffected.

## 5. Matrix

### 5.1 The homeserver

**tuwunel** (the maintained successor to conduwuit, Apache-2.0, `ghcr.io/matrix-construct/tuwunel`, arm64 and x86-64 images, release 1.9.3 of 2026-09-25):
- **Why tuwunel:** one Rust process with an embedded RocksDB, no PostgreSQL, a small memory footprint, and simplified sliding sync (`/sync` v5), which Element X needs.
- **Why not the others:** Synapse would need PostgreSQL and much more memory, and Dendrite is archived.

`ling-admin matrix start` (Python, beside the other sidecars, DOCKER §6):
- **The container:** `dreamference-matrix`, image pinned by digest, memory capped at `--memory=1g` (swap equal), data in the volume `dreamference-matrix-data`.
- **Its configuration** (environment):
  - `TUWUNEL_SERVER_NAME`: the server name (§5.3);
  - `TUWUNEL_ALLOW_FEDERATION=false`;
  - `TUWUNEL_ALLOW_REGISTRATION=true` with a random `TUWUNEL_REGISTRATION_TOKEN` (32 bytes). The token is needed to create any account, and the bridge uses it to create its own account and the user's;
  - `TUWUNEL_TRUSTED_SERVERS=[]`;
  - `TUWUNEL_URL_PREVIEW_DOMAIN_EXPLICIT_ALLOWLIST=[]`.
- **Its accounts:** creates the bot account `@mightling:<server name>` and stores its access token for the bridge.
- **`ling-admin matrix add-user <name>`** creates a user account with a generated password, printed once on the terminal and never stored, and adds the user to the bridge's allow-list.
- **`ling-admin matrix status`** reports the container, the address, the Tailscale state, the accounts and the allow-list. **`ling-admin matrix stop`** stops the container and keeps the volume.

### 5.2 No route out

- **An internal Docker network.** The container runs on `dreamference-matrix`, created `--internal` with a fixed subnet (`172.31.231.0/24`), and the container has a fixed address (`.10`).
- **No way out:**
  - an internal network has no route to anything but the host;
  - with federation off there is nothing to contact anyway.
- **The host reaches it through a loopback proxy.** Docker publishes no ports for a container on an internal network, and `tailscale serve` may refuse a backend that is not on loopback. So `ling-admin matrix start` also installs two user units:
  - `mightling-matrix-proxy.socket`, listening on `127.0.0.1:6167`;
  - `mightling-matrix-proxy.service`, running systemd's own `systemd-socket-proxyd 172.31.231.10:6167`.

  Nothing is added to install: the proxy ships with systemd. The bridge and `tailscale serve` both use `http://127.0.0.1:6167`. The host can reach the container's address across the internal network's bridge, the direction SWE-bench already relies on.
- **The consequence: no push notifications.**
  - **Element X's push gateway is unreachable.** The homeserver cannot reach it (sygnal at matrix.org, then Google or Apple), so the phone is notified only while Element X keeps its own connection.
  - **Android:** that works in the background.
  - **iOS:** it works only while the app is open, or briefly after.
  - **The opt-in:** `ling-admin matrix push on` moves the container to the sidecar network, so pushes go out. A push carries the event id only (Element X's `event_id_only` format), so no message content leaves the machine, but metadata does: that a message arrived, and when. It is off by default.

### 5.3 Reaching it from the phone

- **With Tailscale (the decided route).**
  - **The setup check:** `ling-admin matrix start` finds Tailscale running and signed in (`tailscale status --json`). It takes the node's tailnet name (`Self.DNSName` without its trailing dot, for example `gx10-1a2b.tail1234.ts.net`) as the **server name**, and runs `tailscale serve --bg --https=443 http://127.0.0.1:6167`.
  - **The server name is permanent.** Every account's id ends in it (`@owner:gx10-1a2b.tail1234.ts.net`), and Matrix has no way to rename a server. It is chosen once, stored in `matrix.json`, and passed to the container on every start. Renaming the machine, or moving it to another tailnet, changes its tailnet name: from then on `start` refuses, naming both names. The user either renames the machine back, or removes the homeserver (`ling-admin matrix remove`, which deletes the volume) and starts again with new accounts.
  - **Signing in:** the phone, with Tailscale installed and signed in to the same tailnet, signs in to `https://<tailnet name>` in Element X.
  - **The certificate:** a real one, issued by Let's Encrypt through Tailscale. This needs HTTPS turned on once in the tailnet's admin console, and the command says so when it is off.
  - **The privacy cost:** the tailnet name appears in public Certificate Transparency logs. The name is public; nothing about messages is.
- **Without Tailscale: not in Phase 1.** `start` refuses and prints how to install Tailscale. A LAN-only mode (the `.local` name over plain HTTP) waits for Phase 0 to show whether Element X accepts a homeserver without HTTPS at all.
- **Installing Tailscale is left to the user.** `ling-admin matrix start` does not install it: it needs root, and signing in to a tailnet is an interactive step with the user's account. The command prints the two install lines and stops. Headscale (self-hosted coordination) works the same way and is named as the fully self-hosted alternative.

### 5.4 Rooms and encryption

- **The bridge opens the room.** When the allow-list gains a user, the bridge creates a direct room (`createRoom`, `is_direct: true`, `preset: private_chat`, **no `m.room.encryption`**) and invites them. The user accepts the invite from "Mightling" in Element X.
- **Invites the bridge accepts:** only from allow-listed users, and only to rooms with two members.
- **Rooms the bridge leaves at once:**
  - an encrypted room (one with `m.room.encryption`), after one plain message explaining it;
  - a room that gains a third member.
- **Why no end-to-end encryption.**
  - **Everything is on the node:** the homeserver, the bridge and the agent are all on the node, and the phone's traffic is inside WireGuard and TLS.
  - **E2EE would protect only against the node itself,** which is also where the agent reads every message in plain text to answer it.
  - **The cost would be real:** a crypto store, device verification, and key backup for a bot.
  - **Revisit if** the homeserver ever runs somewhere other than the node.
- **Sync.**
  - **Request:** the bridge long-polls `/sync` (v3, `timeout` 30 s), keeping `next_batch` in its state file. The filter asks only for the event types it handles: `m.room.message` and `m.reaction` in the timeline, `m.room.member` and `m.room.encryption` in the state.
  - **What it ignores:** events older than its start and its own events.
  - **Typing:** "Mightling is typing…" is `PUT /typing` (30 s, renewed) while a turn runs.

## 6. Credentials and their threat model

`~/.mightling/chat/` is a folder (mode 0700) holding:

| File | Contents |
|---|---|
| `telegram.json` | the bot token, the consent date, and the paired user ids |
| `matrix.json` | the homeserver URL, the server name, the bot's user id and access token, and the allow-list (written by `ling-admin matrix`) |
| `matrix-admin.json` | the server name chosen once, the registration token, and whether push is on (`ling-admin matrix` only) |
| `matrix-state.json` | the bridge's `next_batch` and the room it opened for each user |
| `threads.json` | the chat → thread map |
| `pairing/` | pending code hashes |

The registration token in `matrix-admin.json` lets anyone who can reach the homeserver create an account. Only the node and the tailnet can reach it, and an account is useless to them: the bridge answers allow-listed users only.

**Stated plainly, as for the Google tokens (GOA §0): these files are readable by any command the agent runs.** Codex's sandbox limits writes, not reads. What that means:

- **A stolen Telegram token** lets someone read messages sent to the bot before the bridge does, and send messages as the bot. It does **not** let them command the agent: the bridge acts only on updates Telegram delivers from a paired user id, and Telegram cannot be made to deliver forged ones. `ling chat telegram setup` again, after `/revoke` in @BotFather, rotates the token.
- **A stolen Matrix bot token** is usable only from inside the tailnet or the node, since the homeserver is not on the internet.
- **Pairing cannot be done from a sandbox.** Pairing needs a code that only `ling chat telegram pair`, run by the user, creates. A sandboxed command cannot write the `pairing/` folder (the sandbox's writable roots exclude it), and codes are stored as hashes.

## 7. CLI

```
ling chat start | stop | status | serve          the bridge (user unit mightling-chat.service)
ling chat telegram setup | pair | users | remove <id> | off
ling-admin matrix start | stop | status | add-user <name> | push on|off | remove
```

- **`ling chat status`** reports:
  - each adapter: on or off, connected or not, and since when;
  - the paired users;
  - the threads with a running turn.
- **`ling chat start`** refuses when no adapter is configured, and says which command configures one.
- **The homeserver commands are `ling-admin`'s,** because containers are (DOCKER §6). The bridge reads `matrix.json`, which they write. They are `ling-admin matrix …`, not `ling-admin chat matrix …`: `ling-admin chat` is already the web UI's command (Onyx's alias; since Onyx's retirement in 1.7.0, the command that removes what it left).

## 8. Egress

- **`ling chat`'s destinations, and nothing else:**
  - `127.0.0.1:<ling web>`;
  - the homeserver, through the loopback proxy at `127.0.0.1:6167`;
  - `api.telegram.org:443`, only when Telegram is on.
- **The homeserver container:** none (§5.2), unless `push on`.
- **The agent's own egress is unchanged,** because a chat turn is an Ask turn. `ling-admin audit egress` covers sessions, not the bridge. The bridge's destinations are listed here and checked by its tests, which run against stand-in servers only.

## 9. Night Shift, nodes and clients

- **Night Shift:** a chat turn counts as a person at a terminal (§2).
- **Where it runs:** on a node. On a client (a laptop), `ling chat` works against that client's own `ling web`, but the homeserver belongs on the node, which is always on. `ling-admin matrix start` refuses on a machine without a node id.

## 10. Configuration

`[chat]` in the user's config file (`~/.config/dreamference/config.toml`), never the repository's:

| Key | Default | Meaning |
|---|---|---|
| `approval_timeout_s` | 600 | when an unanswered approval is declined |
| `telegram_draft_interval_ms` | 1000 | minimum gap between draft updates |
| `max_threads_listed` | 10 | `/threads` |
| `answer_seconds` | 180 | an answer's time, then "answer now" (§16.3) |
| `tool_calls` | 25 | an answer's tool calls, the same |
| `command_seconds` | 60 | one command's time, the same |
| `telegram_topics` | true | topics as threads when the bot has them (MOONSHOT §14) |
| `telegram_rich` | false | rich messages built from blocks, until Phase 0 (MOONSHOT §14) |
| `new_thread_after_hours` | 4 | a message after this long a silence starts a new thread; 0: never (§16.2; Signal reads it too, through `ling web`) |

## 11. Phase 0, measured before relying on it

1. **Element X:**
   - signing in to tuwunel through `tailscale serve`;
   - joining the bridge's unencrypted DM, and what it shows;
   - with plain HTTP on the LAN, whether it connects at all.
2. **The loopback proxy:** `systemd-socket-proxyd` reaching the container on the internal network from the host, and `tailscale serve` in front of it. If the host cannot reach an internal network's containers on some Docker version, the fallback is an ordinary sidecar network with the homeserver's outbound traffic blocked by tuwunel's own settings, a weaker guarantee that §0 would then have to state.
3. **Telegram on a real phone:**
   - `sendMessageDraft` streaming, the stop button, and `stopped_message_generation`;
   - a 10,000-character answer with code blocks, split.
4. **tuwunel:**
   - memory at rest and while syncing;
   - registration with the token through the client-server API;
   - that `/sync` v3 long-poll works for a bot.

## 12. Tests

All tests use stand-ins. None reaches Telegram, a homeserver, `ling web` or Docker.

- **Bridge core:**
  - routing notifications to the right chat;
  - `/new`, `/use` and `/threads`;
  - steering during a turn;
  - approvals: answered, declined, timed out, and an answer to a request that was never asked, which is refused;
  - air gap `on` stopping Telegram and not Matrix;
  - the renderer and the splitter (code blocks across splits, plain-text fallback);
  - reconnecting to `ling web` mid-turn and still delivering the answer.
- **Telegram adapter,** against a stand-in Bot API (an axum server):
  - pairing: one use, expiry, ten wrong codes;
  - an unpaired sender gets one reply a day;
  - a group is left;
  - drafts throttled, with the final message replacing them;
  - the stop button interrupts;
  - fallback without `sendMessageDraft`;
  - `retry_after` honoured;
  - the token never appears in logs or arguments.
- **Matrix adapter,** against a stand-in homeserver:
  - an invite from someone not on the allow-list is ignored;
  - an encrypted room is left;
  - a third member makes the bridge leave;
  - reactions and `yes`/`no` answer approvals;
  - typing is renewed;
  - `next_batch` persists across restarts.
- **`ling-admin matrix`,** with Docker and Tailscale mocked:
  - the internal network and fixed address, and the proxy units;
  - the server name stored once, and `start` refusing when Tailscale's name has changed;
  - federation off and the registration token set;
  - the `tailscale serve` command line;
  - refusing on a machine without a node id;
  - `push on` moves the network;
  - `add-user` prints the password once and stores none.

## 13. Phases

- **Phase 1 (this branch):**
  - the bridge, both adapters, and `ling chat` with its user unit;
  - `ling-admin matrix`;
  - the tests above.
- **Phase 2:**
  - photos and files, through `/api/upload` into the thread's folder;
  - voice messages, through the speech sidecar, once `/api/transcribe` exists (MIGHTLING_ASK §7);
  - `/night add <repo> <task>`;
  - push opt-in verified on iOS.
- **Phase 3:**
  - Work threads on a repository chosen from the phone, with approvals;
  - other messengers, if users ask (Signal through `signal-cli` is the obvious next, with the caveats its own project states).

## 14. What was built (Phase 1, branch `chat/messengers`, 2026-10-08; merged with §15)

**Built:**

| Piece | Where |
|---|---|
| The bridge: hub, link to `ling web`, renderer and splitter, state files, CLI | `ling-rs/chat/` (crate `ling-chat`), `src/{hub,agent,render,store,cli}.rs` |
| Telegram adapter | `ling-rs/chat/src/telegram.rs` |
| Matrix adapter | `ling-rs/chat/src/matrix.rs` |
| `ling chat …` in the launcher | `ling-rs/src/chat.rs`, one branch in `ling-rs/src/lib.rs`, one dependency line in `ling-rs/Cargo.toml` |
| `ling-admin matrix …` | `dreamference/chat/matrix_homeserver.py`, wired in `dreamference/cli/dreamference_cli_controller.py` |

**Where it departs from the design above:**

- **The command is `ling-admin matrix`,** not `ling-admin chat matrix`: `ling-admin chat` already names the web UI (Onyx's alias; its leftovers since 1.7.0).
- **The loopback proxy is decided:** two user units with `systemd-socket-proxyd` (§5.2), not a forwarder inside the bridge, so the homeserver stays reachable from the phone whether or not the bridge runs.
- **The image is pinned by digest:** tuwunel `v1.9.3@sha256:678b7f53…`, a multi-architecture index with arm64 and amd64.
- **A message that cannot steer a running turn** is queued and starts the next turn, with a one-line notice ("Noted: this goes in as soon as the current answer is done").
- **`requestUserInput` with several questions or a secret** is answered empty, and the chat says to answer it at a computer.
- **`ling chat status` does not list running turns,** which needs the bridge's own state. It names the messengers, the paired users and whether the unit runs.
- **The `[chat]` settings of §10 are read** from the user-level config file's `[chat]` table.
- **Telegram starts even when Telegram does not answer at boot.** The poller backs off and retries, and only a refused token (`Unauthorized`) is reported as needing `ling chat telegram setup`.
- **Approvals live in the bridge's memory.** One pending when the bridge restarts is forgotten by the bridge. The app-server's request stays open until another client (the web UI) answers it or the turn ends.

**Verified (2026-10-08, on stand-ins only):**

- **`cargo test`** in a copy of `ling-rs/chat` beside `web/` and `airgapped/`: **47 tests**, and `cargo clippy --all-targets` is clean.
  - 19 unit tests: renderer, splitter, state files, CLI helpers, the air-gap reading.
  - 11 hub tests against a scripted agent: threads, `/new`, `/use`, steering and its fallback, approvals with every payload of §3, timeouts, the air gap pausing Telegram only, reconnect recovery, stop, failures.
  - 11 Telegram tests against a stand-in Bot API (axum): pairing, strangers, groups, drafts with the stop button and the typing fallback, HTML with the plain-text fallback (approvals included), splitting, buttons, rate limits, starting while Telegram is unreachable, no token in errors.
  - 5 Matrix tests against a stand-in homeserver: invites, encrypted and crowded rooms, the first sync, formatting, typing, reactions, one room per user, `next_batch`.
  - **1 end-to-end test** through the real `ling-web-server` (its sign-in, `/ws` relay and bridge policy) to a stand-in app-server on a Unix socket:
    - the chat's `prompt: ask` arrives as the composed prompt in an Ask folder;
    - an approval's answer passes the policy's pending check;
    - after `ling web` restarts (new in-memory sessions), the bridge signs in again and resumes the thread.
- **Python:** 8 tests for `ling-admin matrix` with Docker, systemd, Tailscale and the homeserver replaced. The whole suite: 913 passed, with the same 13 failures `main` has on this machine, all tests that run the installed `ling` binary.

**Not verified:**

- **Nothing of Phase 0 (§11) has run.** No real Telegram, no real tuwunel, no Element X, no Tailscale (not installed on this machine), no phone.
- **The launcher was not compiled.** The Codex workspace was not built, so `ling chat` routing in `ling-rs/src/lib.rs` and `src/chat.rs` is untested. It mirrors `ling web`'s routing line for line.
- **The user units** (`mightling-chat.service` and the proxy units) were written only in tests and never started.
- **`ling chat telegram setup`'s terminal path** (consent, the token with echo off) was not run.
- **Privacy, still to decide:** a Telegram thread is an Ask thread with `/apps` and `docs_*`, so mail and file contents can reach Telegram's servers in an answer. The warning says so. A Telegram-specific prompt without those tools is a possible Phase 2 step if the user wants it.

## 15. Merged, off by default (branch `messengers/optional`, 2026-10-08)

The user decided that the messenger bridges ship with Mightling, are off by default and are turned on per machine by a command. This section records what merging into `main` changed, together with Signal (SIGNAL §17).

**The command surface stays as built.**
- `ling chat …` is a launcher subcommand, like `ling web`. The bridge is a library linked into `ling`, so there is no separate binary to ship.
- The homeserver is `ling-admin matrix …`, because containers are `ling-admin`'s, and `ling-admin chat` is the web UI's (the retired Onyx's leftovers since 1.7.0).
- There is no `ling chat matrix` alias: one command for one thing.

**Compiled at last.**
- The launcher workspace, with `ling-chat` linked in, built in a scratch export of the pinned Codex.
- `cargo test --release -p ling-chat` ran there: 47 passed. The launcher's 230 tests also passed.
- The end-to-end test through `ling-web-server` once missed its ten-second wait with the other suites running beside it on a busy machine. Its wait is now thirty seconds; alone, it takes about 6.5 s.
- The built `ling` routes `ling chat` (run with a scratch `HOME`): `status` answered, and `start` refused because nothing was set up.
- §14's "the launcher was not compiled" no longer holds.

**Off means off across a reboot.**
- `ling chat stop` now disables `mightling-chat.service` as well as stopping it.
- `ling-admin matrix stop` now:
  - turns `tailscale serve` off;
  - disables the proxy socket, not only stops it;
  - stops the container (whose `unless-stopped` policy keeps it stopped).
- `start` turns each of these on again.
- Nothing in the installer, Night Shift or `node enable` starts either one.

**The egress audit names each bridge that is on** (`EgressAudit.declared_exceptions`):
- the chat bridge's user unit, when it is enabled with a Telegram token (Telegram's Bot API, outside the trace);
- the Matrix proxy socket, when it is enabled (offered to the tailnet by `tailscale serve`; with push on, push notifications can leave).

With Matrix alone, the chat bridge talks only to loopback and is not an exception. With every bridge off, the audit names nothing, and the report is what it was before the bridges existed. Each case is tested.

**Release packaging.**
- Nothing new ships for Matrix and Telegram. `ling-chat` is inside `ling`, `ling-admin matrix` is in the wheel, and the tuwunel image is pinned by digest and pulled only by `ling-admin matrix start`.
- The release job runs the crate's tests in the build's export.

**Not verified:** everything §14 lists, apart from compiling the launcher. Also not run: the live egress audit with any bridge on, which needs the model server.

## 16. After the first mornings of real use (2026-10-20)

The owner's Signal thread of 2026-10-20 (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19) showed what a phone chat needs that a browser tab does not. `ling chat` gets the same fixes; each part is its own branch.

### 16.1 Images and other attachments (branch `chat/images`)

- **Telegram:** a photo (its largest size) or a document is downloaded with `getFile` (the Bot API's limit is 20 MB), its caption taken as the text. Voice and audio, video, video messages, animations, stickers, locations, contacts and polls are not taken, and the bridge says so in one line ("I can't take a voice message here, so it was dropped; …").
- **Matrix:** `m.image` and `m.file` are downloaded from the homeserver's authenticated media endpoint (`/_matrix/client/v1/media/download`, then the older `/_matrix/media/v3/download`), with the Matrix 1.10 caption (`body` beside a `filename`) as the text; `m.audio`, `m.video`, `m.location` and anything else are named as dropped.
- **The hub** (`attachments.rs`) uploads each file into the thread's Ask folder through the link (`AgentCommand::Upload`, `/api/upload?read=1` with the link's cookie, over HTTP beside the socket), then builds the turn as the Signal bridge does: an image as `localImage` when the served model takes images, its text as `[Screenshot text: …]`, a file named; with nothing usable, "I can't read this image; describe it or paste the text." and no turn. Queued inputs are merged into one turn (their texts joined, then their images).
- **`ling web`** reads an image's text with Tesseract (`ocr.rs`) and says whether the served model takes images (`vision`, from the catalog's `input_modalities`, which the launcher writes from the model server's `/model_info`). `ling chat start` says how to install Tesseract when it is missing.

### 16.2 A new thread after a long silence (branch `chat/fresh-thread`)

A message after `new_thread_after_hours` (default 4) without the conversation moving (the user's last message or the last answer, `last` per chat in `threads.json`) starts a new thread, with one line: "New thread (the last message was … ago); /use 2 to go back." Never while a turn runs or messages wait for one; `/use` counts as moving. The `[chat]` settings are read again every minute (`ling_web_server::chat_settings`, shared with `ling web`'s `/api/chat/settings`, which the Signal bridge asks).

### 16.3 An answer's bounds, the phone prompt, and the answer the phone gets (branch `chat/bounds`)

- **Bounds and the final turn:** as the Signal bridge (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19.3), the hub watches each turn (`limits.rs`, from `item/started` and `item/completed`) every 250 ms: past a bound it steers "answer now", 30 s later it interrupts and starts a final turn without tools, and a final turn past 90 s ends with what it wrote and one line.
- **The phone prompt:** threads start with `prompt: "chat"` (falling back to `ask` on a `ling web` that refuses it), in a scratch folder named `ask/chat-<hex>`.
- **The answer:** the turn's final message, or its last; a short last word after a longer earlier message ("The answer above stands.") brings that message with it, because the phone shows only the answer (2 of 56 Ask turns on 2026-10-20 ended so).

### 16.4 Quick follow-ups (branch `chat/merge-quick`)

The hub already steered a message into a running turn and merged the ones that could not steer into one turn when it ended (§3). Two changes: queued inputs keep their images (§16.1), and the final turn after a time-out (§16.3) is never steered into: a message then waits for the next turn.

### 16.5 Telegram's AI-bot platform, Phase 1 (branch `chat/telegram-phase1`)

Topics as threads (a conversation per `telegram:<chat>/<topic>`), rich messages built from blocks (off by default), approvals with colour, a used state and nonce ids, and the partial answer kept on stop and on a time-out in every bridge: specs/DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md §14. Approval ids are nonces for Matrix too.

### 16.6 Voice messages (branch `chat/voice`)

Telegram's `voice` and `audio` and Matrix's `m.audio` are downloaded like images and transcribed through the link (`AgentCommand::Transcribe`, `/api/transcribe` with the link's cookie) instead of being dropped; the hub says "Heard: …", adds the text to the question, and on a failure says so and asks nothing for a recording alone (SIGNAL §19.5 has the same rule). Video, video messages, stickers and the rest are still named as dropped.

