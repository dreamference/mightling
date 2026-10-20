# Mightling on Telegram, the moonshot: Telegram's AI-bot platform for `ling chat`

**Status:** Proposed (2026-10-20), a moonshot: nothing built, nothing tried against Telegram. The Telegram features in §1 were read from Telegram's own pages on 2026-10-20 (sources in §13); each row gives the Bot API version and date. Anything not confirmed by a primary source is marked **unverified**.

**Spec owner:** the owner. **Depends on:** [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md) (the bridge, its Telegram adapter and §16's fixes), [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md) §19 (the same fixes for Signal), [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) (Ask threads, `ling web`, the bridge policy, `/api/transcribe`), [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md), [MIGHTLING_REMOTE_ACCESS](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) (for the Mini App's route).

**What prompted it.** The Telegram bridge went live for real on 2026-10-20 (the owner's own bot, on the owner's GB10). Meanwhile Telegram has spent the last year turning bots into an AI-assistant platform: streamed drafts, topics in private chats, rich messages, guest bots, chat automation. This spec asks which of those make Mightling-on-Telegram materially better, at what cost to privacy, and what to build first.

---

## 0. Decisions, stated first

These are proposals. The ones the owner must confirm are listed again in §0.1.

- **Telegram stays the less private messenger.** Nothing here changes what CHAT §0 says: every message and answer passes through Telegram's servers. Every feature below says what *new* data it puts there (§4).
- **The model never leaves the node.** No feature uses Telegram's own AI (its AI Editor, AI summaries or the Cocoon network). Telegram is a display and a keyboard; the answers come from the GB10.
- **Build what fits a one-owner assistant; skip what makes the bot talk to strangers.** Topics, rich messages, better approvals, voice and Night Shift serve the owner in their own chat. Guest mode and business "chat automation" bring other people's messages to the node and put the model's words in other chats. They rank last, and business mode is proposed as *not built* (§3.10).
- **First build (Phase 1): topics as Ask threads, rich messages for drafts and answers, and approval buttons with colour and a used state.** None of them puts new data on Telegram's servers. Together they turn the chat from one long scroll with `/threads` and `/use` into the shape Telegram now designs for AI bots.
- **Voice and Night Shift are shared-core work, not Telegram work.** Signal and Matrix get them in the same change (§6).
- **The bridge, not the model, builds every button and every link-carrying element.** Rich messages can carry buttons and Mini App launchers in their markup; model text is never passed through as markup (§3.2, §4).
- **Phase 0 runs on a throwaway bot.** Never on the owner's live bot or its token.

### 0.1 For the owner to confirm

| # | Decision | Proposed |
|---|---|---|
| D1 | One Telegram **topic per Ask thread** (§3.1), `/new` creating a topic | Yes |
| D2 | How topics meet the "new thread after a gap" fix in flight (§2.2): inside a topic, the topic's thread continues whatever the gap; only a message outside any topic is subject to the gap rule | Yes |
| D3 | **Rich messages** as the default rendering on Telegram, built by the bridge as blocks (not the model's Markdown passed through), HTML kept as the fallback | Yes, after Phase 0 shows them on the owner's clients |
| D4 | **Voice messages** transcribed on the node, for all three bridges, with a "Heard: …" echo line so a mis-hearing is visible | Yes |
| D5 | **Night Shift from the phone**: `/night add` and the morning report pushed to the paired chat; the report reaches the bridges through `ling web` | Yes; which messenger(s) get the push is the owner's choice |
| D6 | A **Mini App** front-end (§3.6), served only over the tailnet | Later (Phase 3), only if the owner uses Tailscale on the phone |
| D7 | **Guest mode / inline mode** | Not built |
| D8 | **Business mode ("Chat Automation", answering others as the owner)** | Not built; revisit only with per-reply approval and a separate consent (§3.10) |
| D9 | The stop button keeps the partial answer (`keep_on_stop`), and the bridge then sends the partial as a real message marked as stopped | Yes |

## 1. What Telegram has shipped (verified 2026-10-20)

The Bot API changelog's latest entry today is **Bot API 10.3 of 2026-08-24**; the blog's latest post is of 2026-08-25. Nothing later is listed.

### 1.1 Bot API features that matter here

| Bot API | Date | Feature | Relevance |
|---|---|---|---|
| (apps) | 2025-10-10 | Blog: "Threads and Streaming Responses for AI Bots": threaded conversations and streamed replies for bots, announced in the apps | The announcement; the Bot API methods came with 9.3 |
| 9.3 | 2025-12-31 | **Topics in private chats**: `User.has_topics_enabled`, `message_thread_id` and `is_topic_message` in private chats, `message_thread_id` on every send method and `sendChatAction`, `editForumTopic`/`deleteForumTopic` in private chats. **`sendMessageDraft`**, streaming partial messages | §3.1 topics; today's streaming |
| 9.4 | 2026-02-09 | Bots can **create topics in private chats** (`createForumTopic`); BotFather setting to stop users creating or deleting topics (`User.allows_users_to_create_topics`). **Button `style`**: `danger` (red), `success` (green), `primary` (blue) on inline and reply keyboard buttons | §3.1, §3.3 |
| 9.5 | 2026-03-01 | `sendMessageDraft` **allowed for all bots**; `date_time` message entity | Today's streaming works for any bot from here |
| 9.6 | 2026-04-03 | **Managed bots**: a bot can create and manage other bots for their owners (`getManagedBotToken`, `t.me/newbot/...` links) | Rejected (§3.11) |
| 10.0 | 2026-05-08 | **Guest mode**: a bot mentioned in any chat it is not a member of gets a `guest_message` update and may send one reply with `answerGuestQuery`. **Bot-to-bot communication.** **Business bots no longer need Telegram Premium** ("Allowed Business Bots to manage user accounts without a Telegram Premium subscription"). Empty text in `sendMessageDraft` allowed (the "Thinking…" placeholder) | §3.9, §3.10, §3.12 |
| (apps) | 2026-05-07 | Blog "AI Bot revolution": guest AI bots in any chat; **Chat Automation**: "every Telegram user can connect a bot to their profile — and allow it to respond to messages on their behalf" (Settings › Chat Automation), choosing which chats it may access | §3.10 |
| 10.1 | 2026-06-11 | **Rich Messages**: `sendRichMessage`, **`sendRichMessageDraft`** ("stream partial rich messages"), `InputRichMessage` (exactly one of `markdown`, `html` or `blocks`), `editMessageText` with `rich_message`; headings, lists, tables, preformatted blocks, block quotes, collapsible details, formulas, footnotes, media blocks, a `RichBlockThinking` placeholder | §3.2 |
| 10.2 | 2026-07-14 | Explicit **blocks** for outgoing rich messages (`InputRichBlock*`, including `InputRichBlockThinking`) and `InputRichMessageMedia`; **ephemeral messages** (groups: visible to one user); communities; **Mini App origin hardening** (Mini App methods refused from other origins, on for all Mini Apps from 2026-07-20) | §3.2 (blocks), §3.6; ephemeral messages are group-only and irrelevant to a private chat |
| 10.3 | 2026-08-24 | **Buttons inside rich messages** (`RichMessageButton`, `RichBlockButtons`); **`can_stop` and `keep_on_stop`** on `sendMessageDraft` and `sendRichMessageDraft`, and the **`stopped_message_generation`** update (`MessageGenerationStopped`: chat, `message_thread_id`, `draft_id`); **`DisabledButton`** and `InlineKeyboardButton.disabled`; `force_reply` on keyboards; expandable block quotes; document blocks | Today's stop button; §3.2, §3.3 |
| 9.0 | 2025-04-11 | Business accounts: `BusinessBotRights` (`can_reply`: send and edit in private chats with incoming messages **in the last 24 hours**; `can_read_messages`; delete rights; profile, gifts, Stars, stories) | §3.10 |

**Limits, from the same pages:**
- **Rich messages:** up to 32,768 UTF-8 characters, 500 blocks, 16 levels of nesting. Rich Markdown "follows GitHub Flavored Markdown where possible" and may include supported HTML tags.
- **Plain messages and `sendMessageDraft`:** 4,096 characters. A draft is "a temporary 30-second preview"; the final answer must be sent as a message.
- **Rate:** "In a single chat, avoid sending more than one message per second", then 429 errors; 20 messages a minute in a group; about 30 a second across chats (bot FAQ).
- **Files:** `getFile` downloads up to 20 MB, uploads up to 50 MB; a self-hosted Local Bot API server lifts these to unlimited and 2,000 MB.
- **Mini Apps:** `WebAppInfo.url` must be an HTTPS URL; `initData` is signed with an HMAC derived from the bot token (and an Ed25519 signature for third parties) and must be validated before use; a `web_app` button is "available only in private chats between a user and the bot".

### 1.2 Telegram's own AI, for context only

- **Cocoon.** Telegram's "decentralized confidential compute network", live on 2025-11-30 (Durov's channel, post 462). It powers Telegram's AI summaries (blog, 2026-01-03), AI Editor (2026-03-31) and sticker search (2026-05-07). It is not a Bot API feature. Sending Mightling's prompts there would defeat the point of a local model: **not used**.
- **Grok / xAI. Unverified.** On 2025-05-28 Durov announced a one-year partnership to bring xAI's Grok into all Telegram apps "this summer" (post 422). No later primary source found today says whether it shipped; none of the 2025–2026 blog posts read for this spec mentions it. It does not affect this design either way.

### 1.3 Terms that bind a bot (Bot Platform Developer Terms, read 2026-10-20)

- **§4.x:** no data collection "aimed at creating large datasets, machine learning models and AI products"; data users send directly may be used with their "explicit, active and revocable consent". Mightling trains nothing on chat data; nothing changes.
- **§5.4 Telegram Business:** a business bot must state what it does and what it retains, never use or disclose business-chat content for anything else, never send it "to third parties (including third-party APIs)" without authorisation, never conceal its activity from the account owner, and announce significant changes. Violations "may lead to the immediate and permanent ban" of the bot and the account.
- **Loop prevention** (bot-to-bot): bots must deduplicate, rate-limit and cap interaction depth, or face "platform restrictions".

## 2. Where `ling chat` is today, and what is in flight

### 2.1 Already built (CHAT §4, Phase 1)

So the shortlist does not re-propose them:
- **Streaming** with `sendMessageDraft` (once a second at most, at least every 20 s), the empty-draft "Thinking…" placeholder, **`can_stop`** with `stopped_message_generation` → `turn/interrupt`, and the final answer by `sendMessage`.
- **Approvals** as an inline keyboard (Approve / Decline) answered by `callback_query`; a refused render is resent plain with its buttons.
- **Rendering** of the model's Markdown into Telegram's HTML subset, split at 4,096 characters on paragraph, line and word, code blocks closed and reopened.
- **Commands** registered with `setMyCommands`; `/new /stop /threads /use /status /help /pair`.
- **Pairing** by user id; strangers get one reply a day; groups are left.

### 2.2 In flight today (CHAT §16, SIGNAL §19)

Another stream of work is fixing what the first mornings of real use showed, for every bridge. This spec builds on it and does not repeat it:
- **Images and files** (§16.1, merged on 2026-10-20 as `10d0de8`): Telegram photos and documents downloaded with `getFile`, uploaded into the Ask folder through `/api/upload?read=1`, their text read by Tesseract on the node, the image passed to the model when the served model takes images. Voice, video and the rest are dropped with a one-line notice. **Quick messages queued during a turn are merged into one turn.**
- **A fresh thread after a gap** (branch `chat/fresh-thread`, not yet written up): a message long after the last one starts a new thread instead of continuing a days-old one.
- **A per-answer time limit**: the bridge bounds a turn that decodes for minutes without a word (SIGNAL §19's "fifteen minutes hanging").

How this spec meets each one:
- **Topics and the gap rule (D2).** A topic *is* the owner saying which thread a message belongs to, so inside a topic the gap rule does not apply. A message outside any topic (or in a client without topics) still follows it, and the new thread it starts gets its own topic.
- **The time limit and drafts.** A draft vanishes 30 s after its last update. When the bridge ends a turn on time, it sends the partial answer as a real message with one line saying it was cut off, rather than letting the draft disappear. The same applies to `/stop` and the stop button (D9).
- **Voice.** §16.1 drops voice with a notice; §3.4 replaces that notice with a transcription.

## 3. The candidates

Effort is the bridge's work after Phase 0, in working days for one person: **S** up to 2, **M** 3–6, **L** more. "Shared" means the change is in the hub or `ling web` and every bridge gets it.

### 3.1 Topics as Ask threads (Telegram only; core change shared). Effort M

**What the owner gets.** Each Ask thread is a topic in the bot's private chat, named after the thread. Switching tasks is tapping a topic, not `/threads` then `/use 3`. Two questions can stream at once in two topics. A topic the owner creates in the app starts a new thread.

**How.**
- BotFather turns on topics for the bot; setup reads `getMe` and checks `has_topics_enabled` (and `allows_users_to_create_topics`). Without topics, today's behaviour stays.
- **The core change:** the hub keys a conversation by chat *and* sub-thread (`telegram:<chat id>/<topic id>`), not by chat alone. `threads.json` gains the topic ids. Matrix's own threads could use the same key later; Signal has no threads and keeps `/threads` and `/use`.
- **`/new`** creates a topic (`createForumTopic`, 1–128 characters, a placeholder name) and the thread. When the bridge names the thread from the first message (CHAT §3), it renames the topic (`editForumTopic`).
- **Every send, draft and chat action** carries `message_thread_id`; `stopped_message_generation` names the topic, so the right turn is interrupted.
- **`/threads` and `/use`** stay for chats without topics; in a topic chat `/threads` lists the topics.
- **The per-chat rate limit is shared by every topic.** Two topics streaming at once share the chat's one message a second. The bridge throttles drafts per chat, not per topic: a running topic gets updates less often while another streams.

**Unknowns for Phase 0:** what happens to a message sent outside any topic in a topic-enabled private chat; what the bot sees when the owner deletes or closes a topic (then the thread is archived, never deleted); whether topics show on Telegram Desktop and the web client as on the phones.

**Privacy.** New on Telegram's servers: the topic names, which are the threads' names, cut from the first message's first 60 characters. That text was already there; the name makes it a label. Nothing else new.

### 3.2 Rich messages for drafts and answers (Telegram only). Effort M

**What the owner gets.** Answers with real tables, headings, lists, code blocks and formulas, in one message up to 32,768 characters instead of several 4,096-character pieces. The draft streams in the same format, so the answer does not change shape when it lands.

**How.**
- **Drafts** with `sendRichMessageDraft`, final answers with `sendRichMessage`; `can_stop` as today, `keep_on_stop` per D9.
- **The bridge builds `blocks`, not `markdown`.** Rich Markdown accepts HTML tags, including `<tg-button>` with `callback_data`, `url`, `login_url` and `web_app` types (`rich-messages.md`). Model text comes from web pages, mail and files the agent read; passed through, a prompt injection could put a button in an answer: a link dressed as "Approve", or a callback whose data mimics an approval. So the bridge parses the model's Markdown itself (a real Markdown parser, new: `render.rs` today handles only inline styles and code fences, not tables, nested lists or quotes) and emits `InputRichBlock*` blocks (paragraphs, headings, lists, preformatted with a language, tables, quotes). Raw HTML in the model's text is shown as text. **No button, link-login or Mini App element ever comes from model text.**
- **Approval callbacks carry a random nonce.** Today a button's data is `a:<n>:<choice>`, with `n` a counter the hub increments per approval (`hub.rs`, `next_approval`), checked against the hub's pending approvals and then `ling web`'s pending set (CHAT §3). A counter is guessable; that is harmless while answers cannot carry buttons, and stops being harmless the day they can. With rich messages each request gets an unguessable nonce, and a callback whose nonce the bridge never issued is ignored.
- **Above 32,768 characters or 500 blocks**, the splitter (CHAT §3) still splits, now at a block boundary.
- **Fallback:** if `sendRichMessage` is refused (an older Bot API, a client that cannot show it, a malformed block), the bridge sends today's HTML; a failed render never loses an answer.
- **The thinking placeholder:** `InputRichBlockThinking` before the first text, as the empty draft does today. It is a presentation element, not the model's reasoning, which is never sent.

**Unknowns for Phase 0:** what an older Telegram client shows for a rich message; whether `sendRichMessageDraft` has the same 30-second life; whether rich drafts count against the one-message-a-second limit the way plain drafts do.

**Privacy.** Nothing new: the same answer, better laid out.

### 3.3 Approvals with colour, a used state and the partial kept (Telegram only). Effort S

- **Approve** as a `success` (green) button, **Decline** as `danger` (red).
- **After a press**, the request stays readable with the choice shown as a `disabled` button (10.3), "Approved" or "Declined", in place of the keyboard. Today the bridge edits the message's text to append the outcome ("Answered elsewhere.", "The turn ended."), which should also remove the keyboard (an edit without `reply_markup`; checked in Phase 0); the disabled button makes the outcome scannable in a long topic.
- **A request whose 10 minutes ran out** is shown the same way, "Declined (timed out)".
- **The stop button** keeps the partial (`keep_on_stop`), and the bridge sends it as a message ending "(stopped)" (D9).

**Privacy.** Nothing new.

### 3.4 Voice messages, transcribed on the node (shared). Effort S

**What the owner gets.** Hold the microphone, speak a question, get an answer. This is the most phone-shaped input and today it is dropped.

**How.**
- **Telegram:** a `voice` (and `audio`) message is downloaded with `getFile` (20 MB) and sent to `ling web`'s `/api/transcribe`, which already exists for the web UI and the desktop app (speaches on `127.0.0.1:8100`, `sidecars.rs`). The transcript is the turn's text, merged with any caption.
- **The echo line:** before the answer, the bridge posts "Heard: <transcript>", so a mis-hearing is seen before the owner trusts the answer (D4).
- **Signal and Matrix in the same change:** Signal's voice-note attachments and Matrix's `m.audio` take the same path; this is the hub's `attachments.rs` growing one case.
- **Without the speech sidecar** (`ling-admin voice start` not run), the bridge says so in one line, as §16.1 does today, and names the command.

**Unknown for Phase 0:** whether speaches takes Telegram's OGG/Opus as it is or the bridge must convert it.

**Privacy.** New on Telegram's servers: **the owner's voice**. A recording is more identifying than text (a voiceprint, background sounds, other people speaking nearby) and Telegram keeps it with the chat. The transcription runs on the node; no speech service sees it. Setup's warning gains one line about voice.

### 3.5 Night Shift from the phone (shared). Effort M

**What the owner gets.** `/night add <repo> <task>` from the phone in the evening; the morning report as a message when the night ends. CHAT §13 Phase 2 and SIGNAL (the Phase 2 list, "`/night <task>` … and the morning report comes back as a message") already name this; neither is built.

**How.**
- **Adding** goes through `ling night add`, which the bridge runs as the user (it is not sandboxed), with the repository chosen from a short list the bridge reads from Night Shift's own configuration. A repository the owner has not set up for Night Shift is refused with the command to do it.
- **The push.** `ling-admin night run` is Python; the bridges are Rust, and the Signal bridge runs as its own system account and cannot read the owner's home folder. So the report reaches the bridges **through `ling web`**, which every bridge already signs in to: a small endpoint that returns reports newer than a given time, polled by each bridge every few minutes after 05:00, or a notification on the bridge's socket. One path for all three bridges (D5).
- **The message** is the report's summary: per task, the status, the branch name and the diffstat line, never the diff. On Telegram it is a rich message with a table, in its own "Night Shift" topic.
- **Which chat gets it** is the owner's setting (`[chat] night_report_to`, a list such as `["signal", "telegram"]`).

**Privacy.** New on Telegram's servers (when Telegram is chosen): repository names, task texts, branch names, file names and line counts from the diffstat, and test results. That is information about the owner's code, not the code; the owner chooses whether Telegram gets it.

### 3.6 A Mini App front-end to Ask and Work, over the tailnet (Telegram only). Effort L

**What the owner gets.** A menu button in the bot's chat opens Mightling's own UI (`ling web`: Ask, and Work with diffs and approvals) full-screen inside Telegram, without leaving the app.

**The privacy point that makes it interesting.** A Mini App is a web page the phone's Telegram app loads **directly** from its URL. If that URL is `ling web` behind `tailscale serve` (HTTPS, as Matrix already uses), the page and everything typed into it travel phone → tailnet → node, **not through Telegram's servers**. Telegram sees only that the owner opened the app, when, and the URL (the tailnet name, which is already public in Certificate Transparency logs, CHAT §5.3). It is the only feature here that *reduces* what Telegram sees.

**How.**
- **Prerequisite:** Tailscale on the phone, signed in to the tailnet, as for Matrix. Without it the page does not load; the button is not offered.
- **Credential:** `ling web`'s own device pairing stays the credential (MIGHTLING_ASK §16). `initData` is validated (the HMAC with the bot token) only to check that the Telegram user opening it is a paired one; it never replaces the device credential.
- **Policy:** the page is the same UI the desktop app and browsers get, under the same `policy.json`; a Telegram-specific shell (theme, back button, safe-area insets) is a thin layer, and the egress rules of `ling web` are unchanged.
- **Origin hardening** (10.2): the Mini App's methods work only from the configured domain, which is what we want.

**Risks.** A second web entry point to the node to keep secure; Telegram's webview differences across iOS, Android and desktop; effort that duplicates the desktop app for a smaller screen. Phase 3, D6.

### 3.7 Sending files and images back (shared). Effort S

When an answer names a file the agent wrote in the Ask folder, or an image search result (`/images/<id>.jpg`), the bridge may send it (`sendDocument`, `sendPhoto`, 50 MB) instead of only naming it. **New on Telegram's servers:** the file itself. Proposed opt-in per message: the answer offers a "Send file" button, the bridge sends only on a press.

### 3.8 Inline buttons the agent may offer (Telegram only). Effort S, later

Answers that end in a choice ("Run the tests?" "Open a Night Shift task for this?") could carry buttons. Only the bridge makes buttons, from a small fixed set of actions it knows (new topic, queue for Night Shift, send file); never from model text (§3.2). Listed so the block parser leaves room for it; not in Phase 1.

### 3.9 Guest mode and inline mode (Telegram only). Not proposed

**What it would be.** Mentioning the bot in any chat (`@bot question`) and getting one reply there (guest mode, 10.0), or composing a reply in any chat through the bot (inline mode).

**Why not.**
- It brings **other people's messages** to the node: the summoning message and the one it replies to, written by people who never agreed to Mightling.
- The answer appears **in their chat**, as the bot, with whatever the agent knows: an Ask thread can read the owner's mail and files.
- It conflicts with CHAT §4.2 ("private chats with a paired user only"). If ever built, a `guest_message` from anyone but a paired user is dropped, the answering thread has no `/apps` and no `docs_*`, and the reply waits for the owner's approval in the private chat, which defeats the point.
- One reply only, no streaming, and the inline result has to be ready in seconds; a local 27B model with tools is not that.

### 3.10 Business mode, "Chat Automation" (Telegram only). Not proposed; possibly never

**What it would be.** The owner connects the bot to their own Telegram account (Settings › Chat Automation; no Premium needed since 10.0). The bot receives the owner's incoming private messages (`business_message`) and, with `can_reply`, answers as the owner in chats with an incoming message in the last 24 hours.

**What is real in its favour.** Terms §5.4 (iv) forbids sending business-chat content to third-party APIs without authorisation; a local model is the one design that satisfies it naturally. That is a genuine Mightling advantage over hosted assistants.

**Why not, still.**
- **Other people's messages**, every one in the chats the owner selects, reach the bot over Telegram and the node; they never consented.
- **The model speaks as the owner.** A wrong or injected answer is the owner's word to a friend, a client or a bank.
- **The account at risk is the owner's own**, not a throwaway bot: §5.4's ban covers "your TPA and your account".
- **If ever built:** draft-only. Every reply waits for the owner's approval in the bot's private chat (shown with the incoming message and the proposed answer, Approve / Edit / Decline), nothing is sent by silence, `can_reply` is the only right asked for (no read, delete, profile, gift or Stars rights), and a separate consent at the terminal says that other people's messages will reach the node.

### 3.11 Rejected outright

- **Managed bots** (9.6): a manager bot creating the owner's bot from a link would need a Dreamference-run manager bot, a central service holding every user's bot token. Mightling has none and wants none. Setup stays "@BotFather, then paste the token".
- **Bot-to-bot communication** (10.0): no use for one owner's assistant; it adds Telegram's loop-prevention duties.
- **Stars, payments, subscriptions, paid broadcasts:** Mightling is not sold through Telegram.
- **Ephemeral messages** (10.2): for groups; the bot leaves groups.
- **Telegram's AI (Cocoon):** §1.2.

### 3.12 Listed, not recommended: a Local Bot API server

Telegram's open-source Bot API server, run on the node, lifts the file limits (unlimited download, 2,000 MB upload). It does **not** reduce what Telegram sees: it is a full MTProto client that talks to Telegram's data centres with an `api_id`/`api_hash` registered by the owner. It would add a long-running daemon and new destinations, each to be named by the egress audit. Worth it only if 20 MB proves too small for real voice notes or documents.

## 4. Privacy, feature by feature

Stated precisely: bot chats are Telegram **cloud chats**. They are encrypted between the phone and Telegram and between Telegram and the bridge (HTTPS to `api.telegram.org`), and **stored on Telegram's servers, where Telegram can read them**. They are not end-to-end encrypted; the Bot API has no secret chats. Telegram keeps the chat history until it is deleted.

| Feature | New on Telegram's servers | Whose data | Stays on the node |
|---|---|---|---|
| Topics (§3.1) | Topic names (= thread names) | Owner's | The threads, the Ask folders, everything the agent reads |
| Rich messages (§3.2) | Nothing new | — | Everything |
| Approvals (§3.3) | Nothing new (the request text was already sent) | Owner's | The command's execution |
| Voice (§3.4) | The owner's voice recordings | Owner's (and anyone audible nearby) | Transcription |
| Night Shift (§3.5) | Repo, task, branch and file names, line counts, test results | Owner's | Code, diffs, logs |
| Mini App (§3.6) | That it was opened, and the URL | Owner's | All content: it travels over the tailnet |
| Files back (§3.7) | The file sent | Owner's | Everything not sent |
| Guest / inline (§3.9) | Answers in others' chats | **Others'** and owner's | — (not built) |
| Business (§3.10) | Every message of the selected chats | **Others'** | — (not built) |

**The standing risk, unchanged:** an Ask thread can read the owner's mail, Drive, Calendar and files (`/apps`, `docs_*`), and whatever it quotes in an answer is then on Telegram's servers. Topics, rich messages and buttons do not change that; setup's warning already says it.

## 5. Platform risks

- **Account and bot bans.** The bot is the owner's own, one user, no spam, no data collection: the ordinary terms are met. The risk rises only with guest and business mode (§3.9, §3.10), and with business mode it is the owner's own account. The bot's token is revocable (`/revoke`), the account is not.
- **Rate limits.** One message a second per chat, shared by every topic (§3.1). Whether drafts count against it is measured in Phase 0; the bridge honours `retry_after` as today and degrades by sending drafts less often, never by dropping the final answer.
- **Draft lifetime.** 30 seconds; a turn that decodes for minutes without new text needs a draft refresh at least every 20 s (as today), and a cut-off turn needs a real message (§2.2).
- **API churn.** The rich-message and topic APIs are months old and changed between 10.1, 10.2 and 10.3 (`receiver_user_id` was replaced by `ephemeral_message_parameters` within six weeks). Every new call keeps a fallback to today's path, tested against the stand-in Bot API.
- **Telegram down or blocked.** Unchanged: the adapter backs off, Signal and Matrix keep working.
- **A new web entry point** (Mini App only): `ling web` reachable from the phone's Telegram webview over the tailnet; covered by `ling web`'s pairing and policy, which already face browsers.

## 6. Coexisting with Signal and Matrix

| Feature | Where it lives | Signal | Matrix |
|---|---|---|---|
| Sub-thread key in the hub (§3.1) | Shared core | Not used (no threads) | Possible later with Matrix threads; not researched here |
| Topics UI | Telegram adapter | `/threads`, `/use` | `/threads`, `/use` |
| Rich blocks (§3.2) | Telegram adapter, from the shared parser | Signal styles (today) | `formatted_body` (today) |
| Approval buttons (§3.3) | Telegram adapter | YES/NO (today) | Reactions, yes/no (today) |
| Partial kept on stop or time limit (§2.2) | Shared core | Yes | Yes |
| Voice (§3.4) | Shared core (`attachments.rs`, `/api/transcribe`) | Voice notes | `m.audio` |
| Night Shift (§3.5) | Shared core and `ling web` | Yes | Yes |
| Mini App (§3.6) | `ling web` plus a Telegram shell | — | — |
| Files back (§3.7) | Shared core | Attachments | `m.file` |

The rule stays CHAT §0's: anything not messenger-specific is in the hub and tested once.

## 7. The ranked shortlist

Ranked by benefit to the owner, then by what new data leaves the node, then by effort:

| Rank | Feature | Effort | Shared? | New data on Telegram | Phase |
|---|---|---|---|---|---|
| 1 | **Topics as Ask threads** (§3.1) | M | core key shared | Topic names | 1 |
| 2 | **Rich messages, drafts and answers** (§3.2) | M | Telegram | None | 1 |
| 3 | **Approvals: colour, used state, partial kept** (§3.3, D9) | S | partly | None | 1 |
| 4 | **Voice to text on the node** (§3.4) | S | yes | Voice recordings | 2 |
| 5 | **Night Shift add and morning report** (§3.5) | M | yes | Repo and task metadata | 2 |
| 6 | Files and images back, on a press (§3.7) | S | yes | The file | 2 |
| 7 | Mini App over the tailnet (§3.6) | L | `ling web` | Less than today for what goes through it | 3 |
| 8 | Bridge-made action buttons (§3.8) | S | Telegram | None | 3 |
| 9 | Guest / inline mode (§3.9) | M | Telegram | Others' messages | Not built |
| 10 | Business mode (§3.10) | L | Telegram | Others' messages | Not built |

**Build first:** ranks 1–3 together, on one branch after Phase 0. They are the features Telegram built for AI bots (its own words: topics "improving on established web-based AI chat UIs", streaming, rich results), they put nothing new on Telegram's servers, and they replace the part of today's adapter that is most awkward on a phone: one scroll, `/use <n>`, answers cut into 4,096-character pieces. Voice comes right after, because the sidecar and endpoint already exist and every bridge gains it.

## 8. Phases

- **Phase 0, measured on a throwaway bot (never the owner's).** A second bot from @BotFather with topics enabled, on the owner's phone and Telegram Desktop:
  1. topics: a bot-created topic, a user-created topic, a message outside topics, closing and deleting a topic, and what `getUpdates` shows for each;
  2. `sendRichMessageDraft` then `sendRichMessage`: a 20,000-character answer with tables and code; what an older client shows; `can_stop` and `keep_on_stop`;
  3. whether drafts count against one message a second, with two topics streaming;
  4. a voice note through `/api/transcribe`, as it comes;
  5. `editMessageReplyMarkup` with `style` and `disabled` on an approval, and whether today's `editMessageText` of a closed approval removes its keyboard.
- **Phase 1:** §3.1, §3.2, §3.3 and D9. The stand-in Bot API learns topics and rich messages.
- **Phase 2 (shared):** §3.4 voice, §3.5 Night Shift with the `ling web` report endpoint, §3.7 files on a press.
- **Phase 3:** §3.6 Mini App, if D6 says so; §3.8.
- **Not built:** §3.9, §3.10, §3.11.

## 9. Setup and configuration

- **BotFather toggles have no API.** Topics (and "users may create topics"), and anything else in BotFather's Mini App, are switched on by the owner. `ling chat telegram setup` says which to turn on, then verifies what it can with `getMe` (`has_topics_enabled`, `allows_users_to_create_topics`) and reports the rest as "not verifiable".
- **`allowed_updates`** grows only by what Phase 1–2 use: today's `message`, `callback_query`, `stopped_message_generation`; topic service messages arrive inside `message`. `guest_message`, `business_*`, `inline_query` stay unrequested.
- **The warning** at setup gains one line for voice and, when chosen, one for Night Shift reports.
- **`[chat]`** gains `telegram_topics` (default `true` when the bot has topics), `telegram_rich` (default `true` after Phase 0), and `night_report_to` (default empty).

## 10. Egress

- **Phases 1 and 2 add no destination.** The bridge still talks to `127.0.0.1:<ling web>` and `api.telegram.org:443`; `ling-admin audit egress` keeps naming the chat unit as the declared exception it is today.
- **The Mini App adds no destination from the node.** The phone reaches `ling web` through `tailscale serve`; the audit's Matrix line already covers that path and would name `ling web`'s tailnet port too.
- **A Local Bot API server** (§3.12), if ever, adds Telegram's data centres as destinations of a new daemon, named by the audit.

## 11. Tests

All against stand-ins, as today; none reaches Telegram.
- **Topics:** a topic per thread; `/new` creating one; a user-created topic starting a thread; drafts, sends and stop routed by `message_thread_id`; the gap rule skipped inside a topic and applied outside; per-chat draft throttling across two topics.
- **Rich messages:** Markdown → blocks for each element the parser renders; raw HTML and `<tg-button>` in model text arriving as text, never as markup; the split at 32,768 characters or 500 blocks on a block boundary; the fallback to HTML on refusal.
- **Approvals:** styles set; the keyboard disabled after a press and on time-out; a callback whose nonce the bridge never issued refused.
- **Partial answers:** on stop, on the time limit, the partial sent as a message.
- **Voice (shared):** a voice note per bridge to `/api/transcribe`, the echo line, the sidecar missing.
- **Night Shift (shared):** the report endpoint's "newer than" contract; one push per report per chat, never repeated after a restart.

## 12. What stays local

Whatever Telegram adds, these never leave the node through Telegram unless an answer quotes them: the model and its inference, the agent's tool calls and their outputs, the Ask folders and uploaded files after transcription and OCR, mail, Drive and Calendar contents, the local file index, code and diffs, Night Shift's logs, voice transcription, image OCR. The model's reasoning is never sent, in a draft or otherwise.

## 13. Sources (read 2026-10-20)

- Bot API changelog: `https://core.telegram.org/bots/api-changelog` (machine-readable `…/api-changelog.md`): versions 9.0–10.3 as dated in §1.1.
- Bot features: `https://core.telegram.org/bots/features` (Streaming Replies, Topics in Private Chats, AI Agents, Business Bots, Managed Bots, Bot-to-Bot Communication, Guest Bots, Rich Messages, Local Bot API).
- Method and type pages: `sendMessageDraft`, `sendRichMessageDraft`, `sendRichMessage`, `InputRichMessage`, `MessageGenerationStopped`, `createForumTopic`, `InlineKeyboardButton`, `BusinessBotRights`, `BusinessConnection`, `answerGuestQuery`, `WebAppInfo`, `Update` (under `https://core.telegram.org/bots/api/…`); rich formatting and its limits: `https://core.telegram.org/bots/api/rich-messages`.
- Mini Apps: `https://core.telegram.org/bots/webapps` (initData validation, HTTPS URLs).
- Bot FAQ: `https://core.telegram.org/bots/faq` (rate limits, file sizes).
- Telegram FAQ: `https://telegram.org/faq` (cloud chats, server-client encryption, "Are bots safe?").
- Bot Platform Developer Terms: `https://telegram.org/tos/bot-developers` (§4, §5.4, loop prevention).
- Telegram blog: `comments-in-video-chats-threads-for-bots` (2025-10-10), `new-design-ai-summaries` (2026-01-03), `ai-editor-mighty-polls-and-more` (2026-03-31), `ai-bot-revolution-11-new-features` (2026-05-07), `watch-apps-and-more` (2026-06-11), `communities-editor-invisible-messages` (2026-07-14), `welcome-messages-buttons-TG-13` (2026-08-25), `group-calls-made-easy` (2025-04-30, business bots in automated accounts), all under `https://telegram.org/blog/`.
- Durov's channel: `https://t.me/durov/422` (2025-05-28, Grok partnership, announced) and `https://t.me/durov/462` (2025-11-30, Cocoon live).
