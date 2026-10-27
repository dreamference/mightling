# Mightling on Discord: a third adapter for `ling chat`

**Status:** Proposed (2026-10-21); decided by the owner on 2026-10-22, every proposal of §0.1 accepted; nothing built, and the build waits for Telegram's Phase 0 and the shared Phase 2 (D9). The owner asked that day for a Discord integration "at least as private as our Telegram implementation". Discord's platform was read from its own pages on 2026-10-21 (sources in §14); anything a primary source did not confirm is marked **unverified**, and what can only be learnt with a real Discord application is Phase 0 (§10), which the owner runs. No Discord application or account was created for this spec.

**Spec owner:** the owner. **Depends on:** [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md) (the bridge, its hub and §16's fixes), [MIGHTLING_TELEGRAM_MOONSHOT](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md) (approvals with nonces, the partial kept, `blocks.rs`, the Mini App of §15), [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md) §19 (images, the gap rule, bounds, the phone prompt, quick follow-ups), [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) (Ask threads, `ling web`, the bridge policy), [MIGHTLING_REMOTE_ACCESS](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) (the HTTPS front for the browser link), [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md).

---

## 0. Decisions, stated first

The owner accepted every proposal on 2026-10-22; the decisions are listed again in §0.1.

- **Discord is the least private of the four messengers, and the bridge says so.** Like Telegram, every message and answer is stored on Discord's servers where Discord can read it; Discord does not encrypt text end to end and said on 2026-05-18 it has "no current plans to extend E2EE to text messages". Unlike Telegram, Discord's privacy policy states that it proactively scans attachments and other content, and a Discord bot can read back its whole DM history, so a stolen token exposes every past message, not only the next ones (§2). The bridge cannot change either; it matches Telegram on everything it controls and adds two compensations (no streamed partials by default, and the bot's answers deleted after a set time if the owner wants).
- **"At least as private as Telegram", measured.** On everything the bridge decides, the Discord adapter puts no more on Discord's servers than the Telegram adapter puts on Telegram's: what the owner sends, the final answers and the approval texts. No inbound port, no webhook, DM with a paired user only, off until a typed consent, paused at `/airgapped on`, link previews off. Where the platform itself is worse (scanning, history readable by the token), the consent text says it in plain words.
- **One adapter in the existing bridge.** Discord is a third adapter of `ling chat` (`ling-rs/chat/src/discord.rs`), beside Matrix and Telegram. The hub, `limits.rs`, approvals with random nonces, the partial kept on stop and time-out, image OCR through `ling web`, the `chat` prompt and `ask/chat-<hex>` folders, quick follow-ups and the gap rule are shared and not re-specified here (CHAT §3, §16; MOONSHOT §14).
- **The Gateway only; no inbound port.** The adapter is an outbound WebSocket client of Discord's Gateway. Slash commands and button presses arrive as `INTERACTION_CREATE` on that connection, which Discord does only while the application has **no Interactions Endpoint URL** (the two are mutually exclusive). Setup verifies that none is set, and that webhook events are off (§3.2).
- **DM-only, with pairing.** The bot answers in its own DM with a paired Discord user id and nowhere else. Its commands are registered for the bot's DM alone (`contexts: [BOT_DM]`), so they never appear in servers or in the owner's DMs with other people. It leaves every server it is added to, except, later and only if the owner chooses it, one private server holding only the owner and the bot (§3.12, Phase 3).
- **No privileged intent.** Message content in DMs sent to the app is exempt from the Message Content intent, so the bot asks only for `GUILDS` and `DIRECT_MESSAGES`.
- **The answer arrives whole by default.** Discord has no draft message. Streaming would mean editing a stored message every few seconds, putting each partial answer on Discord's servers as a message version. The default is Discord's typing indicator and a small status line with a Stop button, then the final answer (§3.7). Streamed edits are an opt-in.
- **An Activity cannot do what the Telegram Mini App does.** Every network request of a production Activity goes through Discord's proxy (`<application id>.discordsays.com`, run on Cloudflare Workers), which forwards to the URL mappings the developer configures. The only documented way around the proxy is a developer test setting, not a product. So `ling web` is not offered as an Activity. The private front-end is a **link button** that opens `ling web`'s own HTTPS front over the owner's tailnet in the phone's browser (§3.11).
- **Not built:** server channels with other people, voice channels and Activities in them, user-installed commands in other people's DMs and servers, the HTTP interactions endpoint, webhook events, a self-bot, and any Discord AI feature (§6).
- **Phase 0 runs on a throwaway application** the owner creates, never one that will carry the owner's real conversations (§10).

### 0.1 Decided (the owner, 2026-10-22)

Every proposal was accepted as written.

| # | Decision | Decided |
|---|---|---|
| D1 | **Streaming**: typing indicator, a status line with Stop, then the whole answer (default); `[chat] discord_stream = true` turns on edited partials, with a consent line of its own | Whole answers by default |
| D2 | **Where the bot lives**: a DM only; a one-person private server with a forum channel (each post an Ask thread) as a later option (§3.12) | DM only in Phase 1; the server in Phase 3 if wanted |
| D3 | **How the owner adds the app**: user install (no server at all) if Phase 0 shows the bot then receives plain DMs; otherwise a private server whose only purpose is to share one with the bot | Whatever Phase 0 shows works with the fewest servers |
| D4 | **Deleting the bot's answers**: `[chat] discord_keep_hours` (0 keeps them). The owner's own messages cannot be deleted by a bot | No automatic deletion: `discord_keep_hours` stays 0 |
| D5 | **The front-end**: a link button to `ling web` over the tailnet (`/app` and the status line), never an Activity | Yes |
| D6 | **The token's home**: `~/.mightling/chat/discord.json` beside Telegram's token (readable by the agent's commands, CHAT §6), or a system account as Signal's keys are | Same as Telegram for Phase 1; revisit with Telegram together |
| D7 | **The app's terms and privacy policy**: Discord's Developer Terms ask every application for a privacy policy and a way to report problems; for an app whose developer is its only user, a one-paragraph policy (nothing kept beyond the node, nothing shared) linked in the app's settings | Yes, a fixed text in the docs that setup points to |
| D8 | **Voice messages**: transcribed on the node through the shared path of MOONSHOT §3.4 when it lands; until then dropped with one line | Yes, with a consent line about the voice |
| D9 | **Discord before or after the shared Phase 2 items** (voice, Night Shift, files back) | After Telegram's Phase 0 and the shared Phase 2, so the adapter starts with them |

## 1. What Discord offers (verified 2026-10-21)

The developer change log's latest entry is of **2026-10-14** (OAuth2 responses carry `iss`). The documentation moved to `docs.discord.com/developers`; every page there is also served as Markdown (`<page>.md`), which is how they were read.

### 1.1 The bot platform, as it matters here

| Area | What the docs say | Use here |
|---|---|---|
| **Gateway** | A WebSocket (`wss://gateway.discord.gg/?v=10&encoding=json` is the example; the URL comes from `GET /gateway/bot`). Hello carries `heartbeat_interval` (example 45,000 ms); Identify; Ready carries a **`resume_gateway_url`** to use on a resume; Resume (op 6), Reconnect (op 7), Invalid Session (op 9). Optional `zlib-stream` or `zstd-stream` compression | The adapter's only inbound path |
| **Identify budget** | "Clients are limited to 1000 `IDENTIFY` calls to the websocket in a 24-hour period … Upon hitting this limit, all active sessions for the app will be terminated, **the bot token will be reset**" | The bridge resumes whenever it may and counts its identifies (§3.2) |
| **Interactions: two ways** | Over the Gateway (`INTERACTION_CREATE`) or by an outgoing webhook to an Interactions Endpoint URL; "These two methods are **mutually exclusive**" | Gateway only; setup checks the URL is unset |
| **Interaction timing** | An initial response "within 3 seconds" or the token is invalidated; tokens valid for 15 minutes for follow-ups | A button press is acknowledged at once by editing its message (§3.6) |
| **Intents** | `DIRECT_MESSAGES` (1 << 12): `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `CHANNEL_PINS_UPDATE`; `GUILDS` (1 << 0) for server joins; `MESSAGE_CONTENT` (1 << 15) is privileged | `GUILDS` and `DIRECT_MESSAGES` only |
| **Message content exemption** | The Message Content intent "is **not** needed … in these situations: Messages your app sends; **Direct Messages sent to your app**; Messages that @mention your app; Replies to your app's messages" | No privileged intent |
| **Privileged intent review** (2026-06-10) | Review is needed from 10,000 users; under that, a toggle | Irrelevant: one user |
| **Installation contexts** | `GUILD_INSTALL` (0) and `USER_INSTALL` (1); a user-installed app is "visible across all of an authorizing user's servers, DMs, and GDMs", limited to commands; "Once it's installed you can open a DM with it" (tutorial) | D3; whether a user-installed bot receives plain DM messages is Phase 0 item 1 |
| **Command contexts** | Per command, `integration_types` and `contexts`: `GUILD` (0), `BOT_DM` (1), `PRIVATE_CHANNEL` (2, other DMs and group DMs; needs `USER_INSTALL`) | Every command `contexts: [1]` |
| **Application settings by API** | `GET /applications/@me` returns `bot_public` ("When `false`, only the app owner can add the app to guilds"), `bot_require_code_grant`, `owner`, `team`, `interactions_endpoint_url`, `integration_types_config`; `PATCH` can set `interactions_endpoint_url`, `event_webhooks_status` (1 disabled, 2 enabled) and others, but not `bot_public` | Setup's checks (§3.2) |
| **Message size** | `content` up to **2,000 characters**; Components V2 (`IS_COMPONENTS_V2`, 1 << 15) replaces `content` with Text Display components, up to 40 components per message (no per-message character limit found for Text Display: **unverified**) | Split at 2,000; V2 not used in Phase 1 |
| **Buttons** | Styles Primary 1, Secondary 2, **Success 3**, **Danger 4**, Link 5 (a `url`, max 512 characters), Premium 6; `custom_id` 1–100 characters; `disabled` | Approvals (`a:<nonce>:<choice>` fits), Stop, the link button |
| **Message flags** | `SUPPRESS_EMBEDS` (1 << 2), `SUPPRESS_NOTIFICATIONS` (1 << 12), `IS_VOICE_MESSAGE` (1 << 13), `IS_COMPONENTS_V2` (1 << 15) can be set when sending | Every bot message `SUPPRESS_EMBEDS` |
| **Edits** | "The fields `content`, `embeds`, `flags` and `components` can be edited by the original message author" | The status line; opt-in streaming |
| **Deletion** | Deleting another user's message needs `MANAGE_MESSAGES`, "if operating on a guild channel"; a DM has no such permission | The bot can delete only its own messages (D4) |
| **History** | `GET /channels/{id}/messages` reads a channel's messages; in a DM the bot sees content (the exemption above) | The stolen-token risk (§2, §5) |
| **Threads and forums** | Public and private threads and `GUILD_FORUM` channels exist **only in servers**; a DM has none | DM-only keeps `/threads` and `/use` (§3.5); threads only in the private server (§3.12) |
| **Typing** | `POST /channels/{id}/typing`, "expires after 10 seconds" | Renewed every 8 s while a turn runs |
| **Attachments** | Upload limit per file **20 MiB** by default since 2026-09-03 (up from 10 MiB; more with Nitro). Attachment URLs on `cdn.discordapp.com` are **signed and expire** (`ex`, `is`, `hm` parameters) | Downloaded as they arrive, capped at 20 MB (§3.8) |
| **Voice messages** | `IS_VOICE_MESSAGE`: one audio attachment with `duration_secs` and `waveform`, no text, cannot be edited | D8 |
| **Rate limits** | Per route and global; "**rate limits should not be hard coded**", parse `X-RateLimit-*` headers and `retry_after`; global 50 requests a second; 10,000 invalid requests (401, 403, 429) in 10 minutes ban the IP for a while | Headers honoured; edit cadence measured in Phase 0 |
| **Creating a DM** | `POST /users/@me/channels` with `recipient_id`; "DMs should generally be initiated by a user action" | The bot never opens a DM first; the owner writes to it |
| **Channel obfuscation** (2026-08 to 2026-11-16) | Bots will no longer see full metadata of server channels they cannot view | Irrelevant to a DM |

### 1.2 Activities: the embedded app, and why it is not Telegram's Mini App

What the docs say:
- **What an Activity is.** "Web applications that run in an iframe within Discord on desktop, mobile and web", talking to the client through `postMessage` (the Embedded App SDK wraps it). They "can be launched in channels, DMs, or from the App Launcher"; with User Install they reach "servers, DMs, and Group DMs". An Activity opens from the app's Entry Point command or as a `LAUNCH_ACTIVITY` (type 12) answer to an interaction.
- **Everything goes through Discord's proxy.** "All network traffic is routed through the Discord Proxy for various security reasons. Under the hood we utilize Cloudflare Workers." The page is served from `https://<application id>.discordsays.com/`, and each path prefix is forwarded to a target the developer sets in the portal (**URL mappings**, `/` → the page's host). The "sandbox" is enforced by a Content Security Policy: a request to anything unmapped fails with `blocked:csp`, except Discord's own API and CDN. The proxy exists "to hide the users' IP addresses, your application's IP addresses, and to block URLs from known malicious endpoints". Cookies must be set for the `discordsays.com` name.
- **The proxy must reach the target.** A mapping's target is a public host name (the local development guide tunnels a laptop out with `cloudflared` for this). A tailnet name (`*.ts.net`, a 100.x address) is not reachable from Cloudflare's network.
- **The one path around the proxy.** A developer can "load your application via a localhost port or other unique URL" with an **Application URL Override**, and then "your application's network traffic will not pass through Discord's proxy" (HTTPS needed on web and desktop, not on mobile). It is reached through **Developer Mode** in the client and the **Developer Activity Shelf**, which the guide opens from a voice channel, shows only the applications of the developer and their teams, and the production instructions say the override "should not be enabled".

The verdict:
- **A production Activity puts everything through Discord.** To serve `ling web` as an Activity, the node would have to be reachable from the internet (Tailscale Funnel, or the remote-access box), Discord's proxy would terminate TLS for `<id>.discordsays.com`, and every page, keystroke, answer and approval would pass through Discord's Cloudflare Workers in plain text. That is **more** than the chat itself sends to Discord (the Ask threads, the Work view, diffs), the opposite of the Mini App, which *reduces* what Telegram sees (MOONSHOT §3.6).
- **The override is a developer's test switch, not a product.** For a one-owner app the owner is its developer, so in principle the override could point at the tailnet front and load directly. But it needs Developer Mode, it is documented as a launch path from a voice channel's shelf, it is not meant to stay on, nothing documents it as a stable or mobile-supported way to run an app day to day, and `ling web` would have to allow Discord's pages to frame it (`frame-ancestors`), the clickjacking surface the Mini App work refused for web.telegram.org (MOONSHOT §15). How the override behaves on a phone, and what CSP the client puts around the iframe, can only be seen with a real application: **unverified**, and not worth building on.
- **The private front-end is a link** (§3.11): the phone's browser loads `ling web` from the tailnet, Discord carries only the link.

### 1.3 What Discord stores and reads

From the **Privacy Policy** (effective 2025-09-29, last updated 2025-08-29), the **DAVE** posts of 2024-09-17 and 2026-05-18, and the **Terms of Service** (effective 2025-09-29):
- **Messages are stored and shown "as you direct"**: "we collect and store the messages you send and display them as you direct". Content you create includes messages, drafts, voice messages and files.
- **Encryption: in transit and at rest, keys Discord's.** "All information sent within our services is encrypted both in transit and at rest … TLS to encrypt text and images in transit." That is server-side encryption: Discord reads the content.
- **No end-to-end encryption for text.** Voice and video in DMs, group DMs, voice channels and Go Live are end to end encrypted with DAVE (MLS based), required for every call since early March 2026. The 2024 announcement: "messages on Discord will continue to follow our content moderation approach and are not end-to-end encrypted." The 2026 post: "We have no current plans to extend E2EE to text messages." Bots' text in DMs is ordinary text.
- **Proactive scanning.** For safety Discord processes content including "proactively scanning attachments and other content for illegal or harmful activity", generates "transcriptions of content as part of our investigation", and uses reported, violating and widely available content "to create systems and models" for detection. Processing "can include use of automated technologies such as machine learning systems".
- **Retention.** "We retain personal information until we determine it is no longer needed"; a deleted message: "Content may be retained by Discord for use as described elsewhere in this policy and in our data retention policy" (that policy's support page refused the fetch: **unverified** beyond these sentences).
- **Apps.** Discord says it does not control third-party apps and requires them to have a privacy policy.
- **Discord's own AI.** The chatbot Clyde was shut down on 2023-12-01 (secondary sources; Discord's own notice not re-read: **unverified**). The design uses no Discord AI feature whatever exists: the model is the GB10's.

### 1.4 Terms that bind the bot

- **A bot account, never a self-bot.** Discord's support article "Automated User Accounts (Self-Bots)" and the Terms ("using any unauthorized software designed to modify the services") rule out automating the owner's own account. Linking to the owner's account, as Signal does, is therefore not possible here; the bot is its own account the owner talks to.
- **Developer Policy** (effective 2024-07-08, read through the Internet Archive: Discord's developer help centre refuses scripted fetches, so a later revision may exist: **unverified** for 2026):
  - #3: do not bypass Discord's privacy, safety or security features; respect blocks and removals. Met: the bot leaves where it is not wanted.
  - #5: no unsolicited DMs. Met: the bot writes only in a DM the owner opened.
  - #15: use API Data only for the stated function. Met: everything stays on the node for the owner's own chat.
  - **#16: do not "transmit data to Discord … that includes protected health information, financial information, or other sensitive information under applicable law".** An Ask thread can read the owner's mail and files and quote them. That is the owner's choice of what to ask, but the consent text and the docs say it: an answer quoting a bank statement is the kind of message this rule names.
  - #21: no training on message content. Met: Mightling trains nothing on chat data.
- **Developer Terms of Service** (effective 2024-07-08, same caveat): tokens kept "encrypted in any files or other materials accessible by third parties"; API Data protected with "encryption of the data at rest"; a privacy policy for the application; deletion of API Data when the application stops. For a bot whose developer is its only user these are met by the node's own protections (full-disk encryption is the owner's; the agent's commands are not a third party) and by D7's one-paragraph policy. Violations allow Discord to suspend the application; the owner's own account is not otherwise at stake, since nothing here automates it.

## 2. The privacy model, compared

Who can read what, with each bridge as built (Signal in linked mode, Matrix on the node over Tailscale, Telegram as of MOONSHOT §14–§15) and Discord as proposed:

| | Matrix (CHAT §5) | Signal (SIGNAL §10) | Telegram (CHAT §4) | Discord (this spec) |
|---|---|---|---|---|
| **Question and answer text** | Only the node and the phone | Only the node and the phone (end to end) | Telegram's servers, readable by Telegram | Discord's servers, readable by Discord |
| **End-to-end encryption** | Not needed: the server is the node (§5.4) | Yes | No (cloud chat, no secret chats for bots) | No, and no plans for text |
| **Attachments the owner sends** | Node only | Node only (others' deleted at once, §19.1) | Telegram's servers | Discord's servers, and scanned (§1.3) |
| **Server-side scanning, stated by the operator** | None | None possible | Not stated in the sources read for these specs | Yes: "proactively scanning attachments and other content" |
| **Metadata the operator sees** | None outside the node; the tailnet name in CT logs | That two accounts talk, sizes, times (less with sealed sender) | Everything about the chat | Everything about the chat, plus the client's own analytics |
| **Partial answers while writing** | None ("typing…") | None | Drafts, a 30-second preview (whether Telegram keeps drafts is not documented) | None by default (typing, a status line); edited messages if D1's opt-in is on |
| **Link previews of URLs in answers** | None (no route out) | Off | Off (`link_preview_options`) | Off (`SUPPRESS_EMBEDS`, links in `<…>`, §3.6) |
| **What a stolen bridge credential gives** | Use inside the tailnet only | Keys live under a system account the agent cannot read | The next updates before the bridge reads them, and sending as the bot; **no history** (the Bot API has none) | **The whole DM history** (`GET /channels/{id}/messages`), live messages, and sending as the bot |
| **Who can start a turn** | Allow-listed accounts on the node | The owner only | A paired Telegram user id | A paired Discord user id |
| **An in-app front-end** | — | — | The Mini App, loaded directly over the tailnet: Telegram sees that it opened | **None that bypasses Discord** (§1.2); a link to the browser instead, Discord sees the link |
| **Inbound port** | Tailnet only (`tailscale serve`) | None | None (long poll) | None (Gateway) |
| **At `/airgapped on`** | Keeps working | Stops | Pauses | Pauses |
| **Account at risk** | None | The owner's own (linked) | A throwaway bot, revocable | A bot application, revocable; the owner's account only if Discord acts on the developer |

**The verdict.** On content, Discord is where Telegram is: everything sent through the chat is the operator's to read. On three points it is worse, and the bridge can only say so: stated proactive scanning, history readable with the token, and no in-app front-end that keeps content off its servers. On everything the bridge chooses, it puts no more on Discord than the Telegram adapter puts on Telegram, and by default less (no streamed partials). For a private chat, the answer stays Signal or Matrix.

## 3. The design

### 3.1 Architecture

```
 Discord app ── Discord's servers ◄── WSS Gateway (outbound) ── ling chat serve ──► ling web (127.0.0.1:3100, /ws)
                       ▲                REST over HTTPS (outbound)   (user unit)        │  bridge policy, Ask folders
                       │                                                                 ▼
                       └── cdn.discordapp.com (attachments, signed URLs)          ling app-server ─► model server

 Phone browser ── tailnet (WireGuard) ── HTTPS front (tailscale serve or the remote overlay) ──► ling web
                 (the link button of §3.11; nothing of it passes through Discord)
```

- **One more adapter task** in `mightling-chat.service`, started when `discord.json` has a token, exactly as Telegram's: one adapter failing (Discord unreachable, a refused token) backs off and never stops the others.
- **The hub key** is `discord:<DM channel id>`; `hub::adapter_of` routes `Outbound` by the prefix. In the private server of §3.12 it would be `discord:<forum id>/<thread id>`, the `topic_of` form Telegram's topics use.
- **New code:** `ling-rs/chat/src/discord.rs` (the REST client, the poller's equivalent, the renderer) and `ling-rs/chat/src/discord_gateway.rs` (the WebSocket session: heartbeats, identify, resume). `tokio-tungstenite`, already a dependency for `ling web`'s socket, gains its rustls feature for `wss://`; no new crate. The CLI gains `ling chat discord …` (§4).

### 3.2 The Gateway, outbound only

- **Connecting.** `GET /gateway/bot` gives the URL; the adapter connects with `?v=10&encoding=json`, no compression in Phase 1 (fewer moving parts; a phone chat's traffic is small).
- **Identify** with `intents = GUILDS | DIRECT_MESSAGES` (1 + 4096 = 4097), no presence, `large_threshold` unset. The token is sent only inside the Identify payload and the REST `Authorization` header, never in a URL, a log line or an error (as `without_url` keeps Telegram's).
- **Heartbeats** at `heartbeat_interval` with the first jittered, as the docs require; a missed heartbeat acknowledgement closes the socket and resumes.
- **Resume first, always.** Every disconnect that allows it reconnects to `resume_gateway_url` with Resume (op 6) and the last sequence number. A new Identify happens only after Invalid Session with `d: false` or a close code that forbids resuming.
- **The identify budget.** The adapter keeps its identifies of the last 24 hours in `discord-state.json`. Past **100** it stops and says so in the journal and in `ling chat status` ("Discord: stopped after 100 sign-ins in a day; see the journal"), far short of the 1,000 that resets the token. Backoff on failures is exponential, capped at 5 minutes, as every adapter's. The unit's restart policy cannot crash-loop into identifies: a restarted bridge resumes from the saved session id and sequence when the session is still valid.
- **Setup's checks of the application** (`GET /applications/@me`, at setup and at every start):
  - `interactions_endpoint_url` must be empty, or interactions would never reach the Gateway: setup refuses, naming the portal page to clear it;
  - `event_webhooks_status` must not be enabled (2): setup refuses the same way;
  - `bot_public` should be `false` ("only the app owner can add the app to guilds"): setup warns and asks the owner to switch it off in the portal (it cannot be set by API);
  - the application's `owner` (or `team`) is shown, so the owner sees whose application it is.

  At start, a failed check stops the adapter with one line; nothing is relayed until it passes.

### 3.3 DM-only, pairing, and what the bot accepts

- **Pairing,** as Telegram's (CHAT §4.1): `ling chat discord pair` prints an 8-digit code, valid 10 minutes and once, stored as its SHA-256; ten wrong codes withdraw every pending code. The owner sends `/pair <code>` to the bot in its DM; the bridge records the **user id** (a snowflake that never changes) and that DM's channel id.
- **Accepted:** `MESSAGE_CREATE` in a DM channel (no `guild_id`) whose author is a paired user and not a bot; `INTERACTION_CREATE` whose `context` is `BOT_DM` (1) and whose user is paired.
- **Ignored:** `MESSAGE_UPDATE` and `MESSAGE_DELETE` (an edited question is not re-asked), messages from bots (the bot's own included: the loop guard on this surface is that the bridge answers people only), system messages, anything with a `guild_id` outside §3.12.
- **Strangers:** anyone else writing to the bot gets one reply a day, "This is a private Mightling. Ask its owner to pair you.", the same text as Telegram's, and is otherwise ignored. An interaction from a stranger is answered with that line as an ephemeral response (Discord requires an answer within 3 s).
- **Servers:** on `GUILD_CREATE` for any server (other than §3.12's), the bot leaves at once (`DELETE /users/@me/guilds/{id}`) and logs the server id, never its name.
- **The DM is opened by the owner.** The bot never calls `POST /users/@me/channels`: Discord's docs ask that DMs be "initiated by a user action", and a bot that writes first is what Developer Policy #5 forbids.

### 3.4 Commands

- **Registered as global application commands with `contexts: [1]` (`BOT_DM`)** and the integration types the app supports, so Discord offers them only in the bot's DM: `/new`, `/stop`, `/threads`, `/use <n>`, `/status`, `/cavemode [level]`, `/help`, `/pair <code>`, and `/app` when a front is set (§3.11). The same table as CHAT §1.
- **Typed text works too.** A message starting with `/` that Discord did not turn into an interaction (an older client, a typo) is handled as the hub handles text commands today.
- **An interaction is acknowledged at once.** The adapter answers within the 3 seconds with a deferred response (type 5) and sends the hub's reply as the follow-up, or, for a button, with `UPDATE_MESSAGE` (type 7). The hub never waits on a 3-second clock.

### 3.5 The conversation, from the shared core

Nothing here is new; it is the hub as it is (CHAT §3, §16; SIGNAL §19; MOONSHOT §14):
- threads open with `prompt: "chat"` in `ask/chat-<hex>`, so the model gate gives them the phone surface's cap (4,096 tokens per response, `docs/dev/loop-guard.md`) and the loop detector;
- the bounds of `limits.rs` (`answer_seconds` 180, `tool_calls` 25, `command_seconds` 60), "answer now", the interrupt 30 s later and the final turn without tools;
- quick follow-ups steered into the running turn or merged into one question;
- the gap rule (`new_thread_after_hours`, default 4);
- `/threads` and `/use`, since a DM has no threads (§1.1); Signal works the same way;
- the partial answer kept on `/stop`, the Stop button and a time-out, sent as a message ending "(stopped)" or "(cut off: …)";
- approvals with random nonces (§3.6).

### 3.6 Rendering, approvals and links

- **Markdown, parsed by the bridge.** The model's Markdown goes through the parser of `blocks.rs` (MOONSHOT §3.2) and is written out as Discord's Markdown: bold, italic, strikethrough, inline code, fenced code with a safe language name, headings, lists, quotes. Tables, which Discord does not render, become a fenced block. Everything else is escaped, so model text cannot produce Discord's own syntax: user, role and channel mentions (`<@…>`, `<#…>`), `@everyone`, timestamps, custom emoji, spoilers or subtext.
- **No mention ever pings:** every message carries `allowed_mentions: {"parse": []}`.
- **No masked links from model text.** A Markdown link becomes `text (<https://…>)`: the real address is shown, and the angle brackets keep Discord from building a preview. Only `http`, `https` and `mailto` become links. A disguised "Approve" link cannot be made, because buttons are made only by the bridge (MOONSHOT §3.2's rule).
- **No previews at all:** every bot message is sent with `SUPPRESS_EMBEDS`, the equivalent of Telegram's `link_preview_options.is_disabled`. Whether Discord's servers still fetch a URL in a message that suppresses embeds is **not documented**; the brackets and the flag together are the most the bridge can do, and Phase 0 item 6 looks at what the client shows.
- **Splitting** at 1,900 characters (the limit is 2,000), on a paragraph, then a line, then a word, a code block closed and reopened, as for Telegram.
- **Approvals.** A request becomes a message with its text (cut to 1,000 characters) and buttons: the first option Success (green), "Decline" Danger (red). `custom_id` is `a:<nonce>:<choice>`, the 16-hex-digit nonce Telegram's adapter already uses; a press naming a nonce the hub never issued is ignored, one it closed gets "That request is no longer open." The press is acknowledged with `UPDATE_MESSAGE`, which replaces the buttons with one disabled button showing the outcome ("Approved", "Declined", "Declined (timed out)", "Answered elsewhere"). Only the paired user's press counts. Secret questions are refused as on every bridge.

### 3.7 While the answer is written (D1)

- **The default: nothing of the answer until it is done.**
  - At the start of a turn the bridge posts one **status line**, "Working on it…", with a **Stop** button (Danger), and renews the typing indicator every 8 s (it expires after 10).
  - The status line is edited at most every 15 s with what the hub knows that is not content: elapsed time and the number of tool calls ("Working on it… 1:20, 6 steps"). No text of the answer, no tool names, no command lines.
  - When the answer is ready it is sent as new messages, and the status line is deleted.
  - Stop (the button or `/stop`) interrupts the turn; the partial is then sent as a message ending "(stopped)", as on Telegram.
- **The opt-in: `[chat] discord_stream = true`.** The status line becomes the draft: its content is the answer so far, edited at most every 2 s (to be tuned in Phase 0 against the `X-RateLimit-*` headers; the docs give no number and say not to hard-code one), cut to the last 1,900 characters; when the answer is done, the draft is deleted and the answer sent as new messages, so a notification arrives for the finished answer. Turning it on prints one line: "Streaming stores each partial answer on Discord's servers as a message version, not a passing preview as Telegram's drafts are."
- **Why not stream by default.** Telegram's `sendMessageDraft` is documented as "a temporary 30-second preview"; a Discord message edit is a change to a stored message. Whether Discord keeps earlier versions is not documented, so the spec assumes it may. Every partial answer stored is a piece of the turn that Telegram's adapter never stores as a message.

### 3.8 Images, files and voice

- **Images and files** the owner sends: each attachment's signed `url` is downloaded at once (the URLs expire), from `cdn.discordapp.com` or `media.discordapp.net` only, up to 20 MB (Telegram's `DOWNLOAD_LIMIT`, and Discord's default upload limit), then uploaded into the thread's Ask folder through `/api/upload?read=1` and composed by the shared `attachments.rs`: an image as `localImage` when the model takes images, its text as `[Screenshot text: …]` from Tesseract on the node, a file named; with nothing usable, "I can't read this image; describe it or paste the text." and no turn.
- **Voice messages** (`IS_VOICE_MESSAGE`, one `audio/ogg` attachment): until MOONSHOT §3.4's shared voice path lands, dropped with one line ("I can't take a voice message here yet, so it was dropped"); after it, transcribed on the node with the "Heard: …" echo line, and setup's consent gains the voice line (D8).
- **Stickers, embeds, polls, forwarded messages and anything else** are named as dropped in one line.
- **Nothing is sent back** as a file in Phase 1; MOONSHOT §3.7's "Send file" button is shared work and comes to Discord with it.

### 3.9 The air gap

Exactly Telegram's (CHAT §3): at `/airgapped on` the adapter relays nothing and answers one line saying why; the Gateway session stays connected (a disconnect would only cost identifies), and messages that arrive meanwhile are not read. When the level drops back, one line: "Mightling was air-gapped from <time> to <time>; messages from then were not read. Send again what you need."

### 3.10 What the bot never does

- write first, or in any channel but a paired user's DM (and §3.12's server, if built);
- accept a command from a server or another person's DM (`contexts: [1]`);
- ask for a privileged intent, presence, or the member list;
- read a channel's history (it keeps what it needs in `threads.json`); `GET /channels/{id}/messages` is never called;
- put a button, link-login or Activity launcher from model text into a message;
- launch an Activity.

### 3.11 The private front-end: a link, not an Activity (D5)

- **The setting:** `[chat] discord_web_url`, in the user's own configuration file only, with the rules of `telegram_mini_app_url` (`ling_web_server::telegram_app::mini_app_url`: a plain `https://` address, no user name, password or fragment). The address is `ling web`'s HTTPS front on the owner's network: `tailscale serve`, or the remote-access overlay. `ling web` serves that name as it serves the Mini App's: it reads `[chat] discord_web_url` beside `telegram_mini_app_url` (today `telegram_app::mini_app_url` reads only the latter), checks it with the same rules and adds its host to `Config::public_names`, with the device credential as the only way in.
- **What the bot sends:** `/app` answers with a **Link button** (style 5) "Open Mightling" to that address, and the status line of §3.7 carries the same button. The button is made by the bridge; no model text ever becomes one.
- **What happens on the phone:** Discord shows its own "you are leaving Discord" prompt and opens the address in the browser (or in Discord's in-app browser, depending on the client: Phase 0 item 5). The page loads from the node over the tailnet; the browser pairs with `ling web pair` like any other, once.
- **What Discord sees:** the address in a message of the bot's (the tailnet name, already public in certificate transparency logs, CHAT §5.3), and, possibly, that the button was pressed (Discord's client analytics: not documented, so assumed). Nothing typed into the page and none of its answers.
- **No Discord launch check.** The Mini App checks Telegram's signed `initData` on top of the device credential (MOONSHOT §15). A link carries nothing signed, and none is needed: the device credential is what lets a page in, everywhere.
- **Not built:** an Activity (§1.2), and loosening `ling web`'s `frame-ancestors 'none'` for Discord's origins.
- **Repo work (2026-10-27):** on Discord it happens only behind this link; the bot sends fixed-template notices with no file content (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §5.3, G11).

### 3.12 Later, if wanted: a one-person private server for threads (D2, Phase 3)

A DM has no threads, so one Ask thread per Discord thread needs a server. If the owner wants it:
- the owner creates a server with no one else in it and adds the bot with `bot_public` off (only the owner can add it); `ling chat discord server <id>` records that one server;
- a **forum channel** "Mightling": each post is an Ask thread (the hub key `discord:<forum>/<thread>`), the post's title the thread's name, `/new` creating a post; the bot needs View Channel, Send Messages, Send Messages in Threads, Create Public Threads, Read Message History (to see the thread it is in), and nothing else;
- **the guard:** the bot reads the server's `member_count` (from `GUILD_CREATE`, no member intent) and leaves the moment it is more than two, saying so once in the DM; it also leaves a server it was not told to stay in;
- **why it is later:** a server is more Discord state (names, a member list, a channel structure), more permissions to keep minimal, and the forum's metadata (post titles = thread names) is new on Discord's servers, as Telegram's topic names are (MOONSHOT §3.1).

## 4. Turning it on

```
ling chat discord setup | pair | users | remove <id> | off
```

1. **`ling chat discord setup`** (a terminal is required; without one, setup refuses: consent is typed by a person):
   - **The warning,** printed whole, and the owner types `yes`:

     ```
     Discord is the least private way to reach Mightling:
     - everything you send the bot, and every answer, is stored on Discord's servers, readable by Discord; Discord does not encrypt text end to end;
     - Discord scans messages and attachments, and may keep what you delete;
     - answers can include what the agent reads for you: mail, files, code;
     - anyone holding the bot's token can read the whole conversation, past messages included.
     For a private chat, use Signal (`ling signal setup`) or Matrix (`ling-admin matrix start`) instead.
     ```

   - **The application,** step by step, all in the owner's own Discord account: create an application in the Developer Portal, name it, open Bot, reset and copy the token, switch **Public Bot** off, leave the Interactions Endpoint URL empty, set the installation contexts (D3, as Phase 0 decides), and paste the token, read with echo off and never taken as an argument.
   - **The checks:** `GET /users/@me` (the token works; the bot's name), `GET /applications/@me` (§3.2's checks). It stores the token and the consent date in `~/.mightling/chat/discord.json` (0600, folder 0700), registers the commands (§3.4), and prints the install link Discord's portal gives, for the owner to open.
2. **`ling chat discord pair`:** the code of §3.3.
3. **`ling chat discord users`** lists the paired user ids; **`remove <id>`** ends one; **`off`** deletes the token, the pairings and the bot's commands (`PUT` an empty command list), and says to delete the application in the portal for good.
4. **`ling chat start`** starts the adapter with the others (CHAT §7); `ling chat status` shows Discord's line: on or off, connected, identifies today, paired users.

## 5. Credentials and their threat model

`~/.mightling/chat/` gains:

| File | Contents |
|---|---|
| `discord.json` | the bot token, the consent date, the application id, the paired user ids and their DM channel ids, `discord_stream`'s consent if given |
| `discord-state.json` | the Gateway session id, the last sequence number, the resume URL, identifies of the last 24 hours |

As CHAT §6 says of Telegram's token: **these files are readable by any command the agent runs** (Codex's sandbox limits writes, not reads; D6). What each loss means:
- **A stolen Discord token is worse than a stolen Telegram token.** With it someone can connect to the Gateway and receive the owner's messages, send as the bot, and **read the DM's whole history** through the REST API. The bot cannot delete the owner's messages, only its own (D4's `discord_keep_hours` limits what history of answers there is). Rotating: reset the token in the portal (which ends every session) and run `ling chat discord setup` again.
- **It does not let anyone command the agent.** The bridge acts only on events Discord delivers on its own Gateway session from a paired user id; another session with the same token sees the same events but cannot inject one into the bridge's.
- **Pairing cannot be done from a sandbox,** as for Telegram: only `ling chat discord pair`, run by the owner, writes a code, the sandbox cannot write `pairing/`, and codes are stored as hashes.
- **What the bridge logs:** events ("turn started", "stranger ignored", "left server <id>"), never message text, user names or server names.

## 6. What is not built, and why

- **Server channels with other people.** Other people's messages would reach the node (none of them agreed to Mightling), and the model's answers, from an Ask thread that can read the owner's mail and files, would appear before them. The Message Content intent would also be needed. The same reasoning as Telegram's guest and business modes (MOONSHOT §3.9, §3.10).
- **Voice channels**, and Activities in them. Voice is end to end encrypted with DAVE, but a bot in a call is a member of its MLS group and sees everything; a spoken back-and-forth needs real-time speech in both directions; and a voice channel is a server's, with its people. Voice *messages* in the DM are D8.
- **Activities** as a front-end: everything through Discord's proxy (§1.2).
- **User-installed commands in other DMs and servers** (`PRIVATE_CHANNEL`, `GUILD` contexts): the bot would answer in conversations with other people, as the owner's tool. Not with an Ask thread that knows the owner's mail.
- **The HTTP interactions endpoint and webhook events:** both need a public HTTPS endpoint on the node or a relay; the Gateway does the same with no open port.
- **A self-bot** on the owner's account: against Discord's terms (§1.4).
- **Discord's AI, the Social SDK, monetization, the App Directory, Linked Roles, Rich Presence:** no use to a one-owner assistant; the app is never listed.
- **Components V2 layouts:** not needed for text answers; a later rendering choice once Phase 0 shows how they look on a phone.

## 7. Egress

- **`ling chat`'s destinations with Discord on:** `127.0.0.1:<ling web>`; `discord.com:443` (REST, `/api/v10`); the Gateway, `gateway.discord.gg:443` and the host of `resume_gateway_url` (Discord's documented example is under `discord.gg`; the exact host names are recorded in Phase 0); `cdn.discordapp.com:443` and `media.discordapp.net:443` for attachments. The adapter refuses to download an attachment from any other host.
- **`ling-admin audit egress`** names the adapter as a declared exception when the chat unit is enabled and `discord.json` has a token, one line beside Telegram's (`EgressAudit.declared_exceptions`): "Discord bridge enabled (`mightling-chat.service`): `ling chat serve` connects to Discord's Gateway and API, outside this trace. `ling chat discord off` or `ling chat stop` turns it off." With Discord off it adds nothing, and the report is what it was.
- **The link front adds no destination from the node:** the phone reaches `ling web` through the front the Matrix and Mini App lines already name.
- **The agent's own egress is unchanged:** a Discord turn is an Ask turn.

## 8. Configuration

`[chat]` in the user's config file (`~/.config/dreamference/config.toml`), never a repository's, beside CHAT §10's keys:

| Key | Default | Meaning |
|---|---|---|
| `discord_stream` | `false` | edited partial answers (§3.7, D1); `true` also needs the consent line, given once at a terminal (`ling chat discord setup --stream`) |
| `discord_status_interval_s` | 15 | how often the status line's time and step count are updated |
| `discord_keep_hours` | 0 | delete the bot's own messages this long after they were sent; 0 keeps them (D4) |
| `discord_web_url` | unset | the HTTPS front for `/app` and the status line's button (§3.11) |
| `discord_identify_limit` | 100 | identifies in 24 hours before the adapter stops (§3.2) |

The shared keys (`approval_timeout_s`, `answer_seconds`, `tool_calls`, `command_seconds`, `new_thread_after_hours`, `max_threads_listed`) apply unchanged.

## 9. Coexisting with Telegram, Signal and Matrix

| Feature | Where it lives | Discord |
|---|---|---|
| Threads, commands, bounds, follow-ups, gap rule, partial kept | Hub (shared) | As every bridge; `/threads` and `/use` (no threads in a DM) |
| Approvals with nonces, the used state | Hub; buttons per adapter | Success and Danger buttons, one disabled outcome button |
| Markdown → messenger format | `blocks.rs` parser, a writer per adapter | A Discord Markdown writer that escapes Discord's own syntax |
| Images, OCR, vision | `attachments.rs`, `ling web` | Signed CDN URLs downloaded at once |
| Voice (MOONSHOT §3.4) | Shared, when built | Voice messages (D8) |
| Night Shift and files back (MOONSHOT §3.5, §3.7) | Shared, when built | Come with them; Night Shift reports to Discord only if `night_report_to` names it |
| In-app front-end | `ling web` | A link button; no Activity |
| The air gap | Hub | Pauses, as Telegram |

## 10. Phase 0, on a throwaway application

The owner creates a **second** Discord application for Phase 0 (never the one that will carry real conversations), and a second, empty Discord account is better still for the stranger tests. The bridge runs with that token in a scratch home. On the phone and on Discord's desktop app:

1. **User install alone:** install the app to the owner's account only (no shared server), DM the bot: does `MESSAGE_CREATE` arrive over the Gateway with its content? Do the `BOT_DM` commands appear? (D3. If not, the fallback is an empty private server shared with the bot, and the bot leaves every other.)
2. **Interactions over the Gateway:** with the endpoint URL empty, a slash command and a button press arrive as `INTERACTION_CREATE`; a deferred response and `UPDATE_MESSAGE` within 3 s.
3. **The Gateway's hosts:** the URL from `GET /gateway/bot` and the `resume_gateway_url` of several sessions, recorded for §7 and the egress audit's list.
4. **Edits:** the rate-limit headers on editing one message every 1, 2 and 5 s for three minutes; whether the client marks a bot's edited message "(edited)"; the cadence for D1's opt-in.
5. **The link button:** where it opens on iOS and Android (system browser or Discord's in-app browser), the "leaving Discord" prompt, and `ling web` loading over the tailnet and pairing.
6. **Previews:** an answer with a bare URL, a bracketed one and a Markdown link, all with `SUPPRESS_EMBEDS`: does the client show any preview? (Whether Discord's servers fetch the URL cannot be seen from the phone; a URL on a host the owner controls, watched in its log, would show it, and is optional.)
7. **Attachments:** a screenshot, a 15 MB file and a voice message; the signed URLs' `ex` lifetime.
8. **Strangers and servers:** the second account DMs the bot (one reply a day); the bot added to a server leaves it.
9. **The identify budget:** the bridge killed and restarted ten times resumes rather than re-identifying.

**Not in Phase 0:** the Activity URL Override (§1.2). It is not part of the design, and testing it means turning on Developer Mode and configuring an override pointed at the node.

## 11. Phases

- **Phase 0** (§10): on a throwaway application.
- **Phase 1:** the adapter (§3.1–§3.10), DM-only, whole answers, pairing, setup with its checks, the egress line, the tests (§12). Effort M (3–6 days), most of it the Gateway session and the stand-in.
- **Phase 2:** the link front (§3.11), the opt-in streaming (D1), `discord_keep_hours` (D4); voice, Night Shift reports and files back as the shared work lands. Effort S.
- **Phase 3:** the private server with a forum channel (§3.12), if D2 says so. Effort M.
- **Not built:** §6.

## 12. Tests

All against stand-ins; none reaches Discord.

- **A stand-in Gateway** (axum with its WebSocket feature, in `tests/discord.rs`) speaking op 10 Hello, op 1/11 heartbeats, op 2 Identify, Ready with `session_id` and `resume_gateway_url` pointing at a second listener, op 0 dispatches with sequence numbers, op 6 Resume and `RESUMED`, op 7 Reconnect, op 9 Invalid Session (`d` true and false), and close codes. With it:
  - heartbeats at the interval, the first jittered; a missed ack closes and resumes;
  - Reconnect and resumable closes resume at `resume_gateway_url` with the last sequence and never identify;
  - Invalid Session `false` identifies once; the identify counter persists across restarts and stops the adapter at its limit;
  - the Identify carries intents 4097 and no privileged bit; the token appears in no log, error or URL.
- **A stand-in REST API** (axum): `/gateway/bot`, `/users/@me`, `/applications/@me` (each failing check of §3.2 refused or warned), message create, edit and delete, typing, interaction callbacks, command registration with `contexts: [1]`, `DELETE /users/@me/guilds/{id}`; 429 with `retry_after` and the bucket headers honoured.
- **The adapter:**
  - pairing: one use, expiry, ten wrong codes; a stranger's one reply a day; a stranger's interaction answered ephemerally;
  - `GUILD_CREATE` leaves the server; a message with a `guild_id` is ignored; bot authors and `MESSAGE_UPDATE` ignored;
  - commands by interaction and by typed text reach the hub alike; every interaction acknowledged within the stand-in's 3 s;
  - approvals: nonce `custom_id`s, Success and Danger, `UPDATE_MESSAGE` with the disabled outcome, a forged nonce ignored, a closed one answered;
  - whole answers: typing renewed every 8 s, the status line edited no more often than set and never with answer text, deleted at the end; Stop interrupts and the partial arrives "(stopped)";
  - `discord_stream`: edits paced, cut to 1,900 characters, the final answer sent new;
  - rendering: mentions, `@everyone`, timestamps, spoilers, custom emoji and masked links in model text arrive escaped or as `text (<url>)`; `allowed_mentions` empty and `SUPPRESS_EMBEDS` on every message; splitting at 1,900 with code blocks reopened; a table as a code block;
  - attachments downloaded only from the two CDN hosts, under 20 MB, then through the shared compose; a voice message dropped with its line;
  - `/airgapped on` relays nothing and keeps the session; the line when it drops back;
  - `discord_keep_hours` deletes only the bot's messages, after their time.
- **The hub:** the `discord:` prefix routes output to the Discord adapter; the existing hub tests run with a Discord key beside Telegram's.
- **Python:** `EgressAudit.declared_exceptions` names Discord when the unit is enabled and `discord.json` has a token, and not otherwise (`tests/test_egress_audit.py`).
- **`limits.rs`** is untouched; `tests/test_chat_limits.py` still holds the two copies equal.

## 13. What stays local

As MOONSHOT §12: the model and its inference, the agent's tool calls and their outputs, the Ask folders and uploaded files after OCR (and transcription, when it lands), mail, Drive and Calendar contents, the local file index, code and diffs, and the model's reasoning never leave the node through Discord, except what an answer quotes. With the default of §3.7, neither do partial answers. Everything done through the link front (§3.11) stays between the phone and the node.

## 14. Sources (read 2026-10-21)

Discord's developer documentation, `https://docs.discord.com/developers/…` (each page as `….md`):
- `change-log` (entries to 2026-10-14: default upload limit 20 MiB of 2026-09-03; privileged intent access of 2026-06-10; channel obfuscation; attachment editing of 2026-08-05).
- `events/gateway` (connection lifecycle, `resume_gateway_url`, intents, the message content intent, the 1,000-identify limit, sharding note on DMs); `gateway/you-might-not-need-a-privileged-intent` (the exemptions).
- `interactions/receiving-and-responding` (Gateway or webhook, mutually exclusive; 3 s and 15 min); `interactions/application-commands` (`integration_types`, `contexts`); `tutorials/developing-a-user-installable-app` (`BOT_DM` vs `PRIVATE_CHANNEL`, opening a DM after a user install).
- `resources/application` (installation contexts; `bot_public`, `owner`, `interactions_endpoint_url`, `event_webhooks_status`; Get and Edit Current Application); `resources/message` (2,000 characters, flags, voice messages, edit and delete rules); `resources/channel` (typing, 10 s); `resources/user` (Create DM and its warning); `topics/threads` (threads and forums in servers only); `components/reference` (button styles, `custom_id`, `disabled`, Components V2); `reference` (signed attachment CDN URLs, the 20 MiB upload limit); `topics/rate-limits` (headers, no hard-coding, global 50/s, invalid-request bans).
- Activities: `activities/overview`, `activities/how-activities-work` (iframe, `postMessage`, Entry Point, `LAUNCH_ACTIVITY`), `activities/development-guides/networking` (all traffic through the proxy on Cloudflare Workers, `<client id>.discordsays.com`, `patchUrlMappings`, cookies), `activities/development-guides/local-development` (URL mappings, the CSP and its exceptions, the Application URL Override and Developer Activity Shelf, `cloudflared`), `activities/development-guides/mobile`, `activities/development-guides/production-readiness`, `activities/building-an-activity` (DMs and group DMs with User Install), `platform/activities`, `developer-tools/embedded-app-sdk` (`openExternalLink`).

Discord's policies and posts:
- Privacy Policy, `https://discord.com/privacy` (effective 2025-09-29).
- Terms of Service, `https://discord.com/terms` (effective 2025-09-29).
- "Meet DAVE: Discord's New End-to-End Encryption for Audio & Video", `https://discord.com/blog/meet-dave-e2ee-for-audio-video` (2024-09-17).
- "Every Voice and Video Call on Discord Is Now End-to-End Encrypted", `https://discord.com/blog/every-voice-and-video-call-on-discord-is-now-end-to-end-encrypted` (2026-05-18).
- Developer Policy and Developer Terms of Service (both effective 2024-07-08), `https://support-dev.discord.com/hc/en-us/articles/8563934450327` and `…/8562894815383`, read through `web.archive.org` captures of 2026 because the help centre answers scripted fetches with 403.
- "Automated User Accounts (Self-Bots)", `https://support.discord.com/hc/en-us/articles/115002192352` (the help centre refused the fetch with 403 on 2026-10-21; its title and summary are from search results).
- Not read, refused with 403: "How long Discord keeps your information" (`https://support.discord.com/hc/en-us/articles/5431812448791`). Clyde's shutdown on 2023-12-01 is from secondary sources only.
