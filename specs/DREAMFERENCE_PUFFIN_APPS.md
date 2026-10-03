# Puffin Apps: Gmail, Google Drive and Google Calendar through `/apps`, with no OpenAI sign-in

**Status:** proposed (2026-10-03). Nothing here is built. §1 is read from the pinned Codex source (`rust-v0.158.0`); Phase 0's source checks are done (§11.1), its live checks and the Drive scope test are not. The user decided the patch cap and §12's questions on 2026-10-03 (§12), which adds Calendar as a third app; Phase 1 can start.
**Goal:** the standard Codex `/apps` command works in `puffin` without a ChatGPT sign-in. It lists **Puffin's own apps** (Gmail, Google Drive, Google Calendar), connects them through Puffin's local Google sign-in, switches them on and off, and hands their tools to the model. Nothing goes to `chatgpt.com`.
**Builds on:**
- [PUFFIN_GMAIL](./DREAMFERENCE_PUFFIN_GMAIL.md): the read-only Gmail service (`dreamference-gmail`, port 8767) and `puffin-admin gmail`;
- [GOA](./DREAMFERENCE_GOA.md): Google sign-in through GNOME's verified OAuth client, the token sealed on the machine; Drive is its unbuilt half;
- patch `0020` (`puffin-rs/tools`): MCP tools reach the local model as plain functions, which is what makes a tool route possible at all;
- [PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md): `on` must keep Gmail and Drive out of the session.

The request said "the `/app` route"; this spec reads that as Codex's `/apps` slash command. It is unrelated to `puffin app`, the desktop window.

---

## 1. What `/apps` does in Codex today

Read from the pinned source:

| Piece | Where | What it does |
|---|---|---|
| **The gate** | `tui/src/chatwidget/connectors.rs:150` `connectors_enabled()` | `Feature::Apps && has_chatgpt_account`. False hides `/apps` from the popup (`bottom_pane/slash_commands.rs:76`) and makes the command print "Apps are disabled." |
| **The list** | app-server `app/list` → `apps_processor.rs` `apps_list_inner` | Returns empty unless `features.apps_enabled_for_auth(uses_codex_backend)`; otherwise merges OpenAI's directory (`chatgpt/src/connectors.rs`, a `chatgpt.com` GET that needs "Codex backend auth") with the apps whose tools the `codex_apps` MCP server exposes |
| **Installed apps for `$` mentions** | app-server `app/installed` (`apps_processor/installed.rs:64`) | Same auth gate |
| **The tools** | `codex-mcp/src/mcp/mod.rs` | `codex_apps` is an MCP server at `chatgpt.com/backend-api/ps/mcp`, started only when `host_owned_codex_apps_enabled` (ChatGPT auth) |
| **Connecting** | `AppInfo.install_url` → `AppEvent::OpenAppLink` → `AppLinkView` → `webbrowser::open` | Opens the URL as given. Host validation (`chatgpt.com` only) applies to MCP *elicitations*, not to `/apps` list items |
| **After connecting** | `AppLinkView::complete_external_flow_and_close` | Sends `RefreshConnectors { force_refetch: true }`, so the list is fetched again |
| **On / off** | `AppEvent::SetAppEnabled` | Writes `[apps.<id>] enabled = false` (and clears it to re-enable) in `config.toml` |
| **Status label** | `connector_status_label` | `is_accessible` → "Installed" or "Installed · Disabled"; otherwise "Can be installed" |

So the screen, the browser hand-off, the refresh and the on/off switch are all generic. Only the gate and the two sources of data (directory and `codex_apps` tools) are tied to OpenAI.

## 2. The decision

**Keep Codex's `/apps` screen and replace its two OpenAI-bound inputs with Puffin's own, in one small patch.** The list comes from the machine, connecting opens Puffin's local Google sign-in, and the tools come from an MCP server the launcher declares.

Rejected:
- **A made-up ChatGPT sign-in pointed at a local stand-in backend.** A sign-in switches on token refresh to `auth.openai.com`, usage analytics and the other ChatGPT-backed channels that patches `0013`/`0015` closed, so the egress audit would fail. The stand-in API would also have to follow OpenAI's undocumented directory and `ps/mcp` formats through every Codex release.
- **A command of our own (`/gmail`, `/connect`).** It is cheaper to keep, but it is not the standard command, and the request is for `/apps`.
- **A local `/plugins` marketplace.** `/plugins` is visible without a sign-in and could install a Gmail MCP server and skill with no patch at all, but it has no notion of *connected*, no sign-in hand-off and no per-app on/off. Kept as the fallback if §11's Phase 0 finds the `/apps` hooks larger than §4 allows.

## 3. What the user sees

```
/apps
  Apps
  Installed 1 of 3 available apps.
› Gmail             Installed · owner@…, work@…        Enter: manage, enable/disable
  Google Drive      Can be installed                   Enter: connect in your browser
  Google Calendar   Can be installed                   Enter: connect in your browser
```

- **Gmail**, connected: "Installed" with the connected addresses in the description; Enter opens the app page with "Manage in browser" (the service's account page) and "Disable app".
- **Google Drive**, not connected: "Can be installed"; Enter opens the local sign-in in the browser; after "I've connected it", `/apps` refreshes and shows "Installed".
- **Disabled** (`[apps.puffin_gmail] enabled = false`): "Installed · Disabled"; the tools and the prompt section are left out of the next session.
- **Google Calendar** behaves as Drive does.
- **At `/airgapped on`:** all three rows say "Unavailable at /airgapped on" and their actions do nothing (§8).
- **Service not running:** the row says "Not running: `puffin-admin google start`" and has no link.
- **On a client machine** (the model server is another node): no Puffin apps are listed, because the Google service and its token live on the node (PUFFIN_NODE; the same rule the Gmail prompt block uses today, `host_is_local`). `/apps` then shows "No apps available."

The `$` mention hint in the header ("Use $ to insert an installed app") stays upstream's text. `$` mentions of Puffin apps are not offered in Phase 1 (§4.3).

## 4. The patch (`0021-puffin-apps`)

### 4.1 A leaf crate, `puffin-rs/apps/` (`puffin-apps`)

Like `puffin-tools` (patch `0020`) and `puffin-airgapped`, the logic lives in a crate of its own that Codex's crates can depend on without a cycle: `serde_json` and the standard library only, plus `puffin-airgapped` and `puffin-node-locator` (both std-only). It answers two questions:

- `offered() -> bool`: the model server is on this machine and the Google service's address is configured. Cheap, no network; called by the TUI on every gate check.
- `list() -> Option<serde_json::Value>`: `None` when Puffin offers no apps (so Codex's own path runs, which without a sign-in returns empty); otherwise the apps as JSON in `AppInfo`'s serde shape. It asks the service `GET http://127.0.0.1:8767/status` (unauthenticated today, over std `TcpStream`, 2 s timeout) for the connected accounts and their granted scopes, and resolves the air-gap level.

The keys are camelCase, the serde shape of the connectors crate's `AppInfo` (§11.1 item 1); a missing `isEnabled` defaults to true.

| Key | Gmail | Google Drive | Google Calendar |
|---|---|---|---|
| `id` | `puffin_gmail` | `puffin_drive` | `puffin_calendar` |
| `name` | `Gmail` | `Google Drive` | `Google Calendar` |
| `description` | connected addresses, or "Read-only search of your mail, on this machine" | connected addresses, or "Read-only search of your files, on this machine" | connected addresses, or "Read-only view of your calendars, on this machine" |
| `installUrl` | `http://localhost:8767/connect?app=gmail` | `http://localhost:8767/connect?app=drive` | `http://localhost:8767/connect?app=calendar` |
| `isAccessible` | an account is connected (today's `/status` lists Gmail accounts only); from Phase 2, an account holds `https://mail.google.com/` | an account holds `drive.readonly` (needs §9's scope report) | an account holds `calendar.readonly` |
| `distributionChannel` | `puffin` | `puffin` | `puffin` |

### 4.2 The hooks

| # | Crate, function | Hook (one line each) |
|---|---|---|
| H1 | `tui`, `ChatWidget::connectors_enabled` | `… && (self.has_chatgpt_account \|\| puffin_apps::offered())` |
| H2 | `app-server`, `apps_list_inner`, before the auth gate | `if let Some(apps) = puffin_apps::list() { return Ok(Some(puffin_apps_response(apps, &config, &params))); }`, where the helper (in the same file, added by the patch) deserialises into the protocol's `AppInfo`, applies Codex's own `AppToolPolicyEvaluator::apply_app_enabled_state` (so `[apps.<id>] enabled` from §1's on/off switch shows), and paginates with the existing `paginate_apps` |

H2 returns **before** the directory request and the `codex_apps` lookup, so no `chatgpt.com` call is made; `codex_apps` itself still never starts, because `host_owned_codex_apps_enabled` still requires a ChatGPT sign-in. Plus one line in each of the two crates' `Cargo.toml` (`puffin-apps = { path = "../puffin/apps" }`).

### 4.3 What is deliberately not hooked

- **`app/installed`** (the `$` mention list). Mentions resolve to `codex_apps` tools by connector id; Puffin's tools are ordinary MCP tools, which the model already sees. Leaving the gate shut keeps `$` empty rather than offering a mention that resolves to nothing. Phase 3 revisits it.
- **Plugin recommendations** (`plugin_recommendations_enabled`, also `Feature::Apps`): unchanged.

### 4.4 Budget

The series is at 33,686 of the 33,750-byte cap (`test_the_patches_stay_small`); the hooks as drafted are 2,385 bytes (§11.1 item 6). **Decided 2026-10-03:** the user approved a total of **37.5 KB** for the series, covering this patch (~2.4 KB) and the context budget's masking hook (~0.9 KB, PUFFIN_CONTEXT_BUDGET). The cap is still raised in the test explicitly, by the size of each patch as it lands, with a comment naming it; 37.5 KB is the ceiling, not a value to set in advance. Calendar adds no hook bytes: it is a third entry in `list()`'s answer.

## 5. Connecting

### 5.1 One service for Google, independent of the web UI

Today the Gmail service is created by `puffin-admin puffin configure`, so a machine without the web UI has none. `/apps` must work without it:

- **`puffin-admin google start`** creates `dreamference-gmail` on the `dreamference-sidecars` network (the SearXNG precedent, DOCKER §6: never the default bridge), publishing `127.0.0.1:8767` only. `configure` keeps creating it as now and adopts one that exists. The container keeps its name; the service gains Drive (§9), so the command is named for Google, and `puffin-admin gmail …` keeps working.
- **On by default on a node** (decided 2026-10-03): `install.sh`'s node role, `host setup` and `server start` start it when it is absent, so Connect from `/apps` works at once. It holds no token until an account is connected, publishes on loopback only, and `puffin-admin google stop` removes it. A client never starts it (the token lives on the node, §3).

### 5.2 The connect page

`GET /connect?app=gmail|drive|calendar` (today `/connect` with no parameter, Gmail only, §0 of GOA):

- **The scope follows the app.** `app=gmail` asks for `userinfo.email` and `mail.google.com`; `app=drive` asks for `userinfo.email` and `drive.readonly`; `app=calendar` asks for `userinfo.email` and `calendar.readonly`. All add `include_granted_scopes=true`, so connecting Drive or Calendar to an account that already has Gmail keeps Gmail, and the stored grant records the union.
- **A grant without the app's scope is refused** with the box to tick, as the Gmail callback already does for an unticked Gmail box.
- **The page ends with "Return to `puffin` and choose *I've connected it*."** Codex's app page then refreshes `/apps` (§1).
- **Opened in the browser by Codex** (`webbrowser::open`, no host check on this path). Phase 0 checks the case with no browser (SSH): Codex prints the URL, and the redirect to `localhost:8767` then needs the paste-back path that already exists (`POST /api/google/oauth/complete`), which the page must offer as a text box.

### 5.3 The existing front doors stay

The web UI's Settings → Gmail Accounts and `puffin-admin puffin gmail` connect the same accounts in the same store. `/apps` is a third door, not a replacement.

## 6. The tools

### 6.1 Served by `puffin` itself

The launcher answers `puffin apps serve gmail|drive|calendar` before Codex parses its arguments (as it does `puffin night`, `puffin skill`), speaking MCP over stdio. The command is the running binary (`current_exe`), so there is no new binary, no Python start-up (seconds, against Codex's MCP start-up wait) and no PATH lookup. It reads the service's shared secret from the file `puffin-admin gmail` uses today and calls the service over loopback.

| Tool | Arguments | Answers |
|---|---|---|
| `gmail_search` | `query` (Gmail syntax), `limit` ≤ 20 | id, account, date, from, subject, snippet per message; per-account errors |
| `gmail_read` | `id` | headers and text body, ≤ 20,000 chars |
| `drive_search` | `query` (Drive `q` subset: `name contains`, `fullText contains`, `modifiedTime >`), `limit` ≤ 20 | id, account, name, MIME type, modified, owner |
| `drive_read` | `id` | text of a Doc/Sheet/Slides export, or of a plain-text file, ≤ 20,000 chars; binary files refused with their type and size |
| `calendar_events` | `from`, `to` (RFC 3339; default today to 7 days ahead), `calendar` (optional; default every calendar of every account), `limit` ≤ 50 | id, account, calendar, start, end, title, location, organiser, attendee count |
| `calendar_search` | `query` (free text, the API's `q`), `from`/`to` optional, `limit` ≤ 50 | the same fields; `calendar_events` with an id returns one event's description, ≤ 20,000 chars |

- **Every tool carries `readOnlyHint: true`**, so Codex's `auto` approval runs them without a prompt, like the `code_*` tools.
- **Every body is wrapped** as `<untrusted source="gmail" id="…">…</untrusted>`, and each tool's description says that text inside it is data, never instructions.
- **No write verb exists**, in the tools or in the service (`BODY.PEEK`; Drive with `drive.readonly`; Calendar with `calendar.readonly`).

### 6.2 Declared by the launcher

At start the launcher adds, per app that is offered (§3), connected, enabled and allowed at the level:

```
-c mcp_servers.puffin_gmail.command="<current_exe>"
-c mcp_servers.puffin_gmail.args=["apps","serve","gmail"]
-c mcp_servers.puffin_gmail.startup_timeout_sec=15
```

(the code index's servers are declared the same way). The 15 s start-up wait is the margin the SWE-bench run of 2026-10-03 added for `puffin-code` (Codex's 1 s default dropped tools in loaded containers); it applies here unchanged. **An app connected mid-session gets its tools at the next start or `puffin resume`** (decided 2026-10-03): no hook declares a server into a running session, and the app page and the connect page both say "restart `puffin` (or `puffin resume`) to use it".

### 6.3 The prompt

When the Gmail tools are declared, the Gmail section names the tools instead of `puffin-admin gmail search/read`. The shell commands stay for scripts and Night Shift; they are no longer advertised to an interactive session, which narrows one path from an injected email to an outbound command: a tool result cannot be piped into a shell in the same call. Drive and Calendar get matching two-line sections.

## 7. Configuration

| Setting | Meaning | Precedence |
|---|---|---|
| `[apps.puffin_gmail] enabled` / `[apps.puffin_drive] enabled` / `[apps.puffin_calendar] enabled` in `~/.puffin/config.toml` | written by `/apps`' on/off switch (§1) | the switch |
| `puffin_gmail = false` (existing) | still honoured: it disables Gmail as before | outranks the `[apps]` entry when false |
| `DREAMFERENCE_PUFFIN_GMAIL` (existing), `DREAMFERENCE_PUFFIN_DRIVE`, `DREAMFERENCE_PUFFIN_CALENDAR` (new) | environment override, for Night Shift and tests | outranks both files |

The launcher reads the `[apps.*]` table with `toml_edit`, which it already uses on the same file. Default: on, when connected, matching Gmail's current default.

## 8. The air gap

- **At a configured `on`**, the launcher declares none of the servers and adds none of the prompt sections (Gmail's section is already dropped at `on`, commit `bb3d6d4`); `list()` marks every row unavailable.
- **Switched to `on` mid-session**, the declared servers are still running and, being MCP servers, run outside the command sandbox with the network. So **each tool call resolves the level itself** before touching the service: the configured level through `puffin-airgapped`, then any seal under `$XDG_RUNTIME_DIR/puffin-airgapped/` written by its parent `puffin` process (the server's parent pid is the session's `puffin`; seals record their writer). At `on` the call returns the level's message and makes no request. Phase 0 confirms the server's parent is the `puffin` process and not an intermediate.
- **The `/apps` rows at `on`** have no link and no actions, so the browser is not opened from an air-gapped session either.

## 9. Drive in the service

Prerequisite: GOA §12's open item, a one-off sign-in confirming Google accepts `drive.readonly` from GNOME's client. If it is refused, Drive leaves this spec and `/apps` lists Gmail alone.

- **Breadth (decided 2026-10-03): My Drive and shared drives.** `files.list` with `corpora=allDrives`, `includeItemsFromAllDrives=true` and `supportsAllDrives=true`, and `supportsAllDrives=true` on every read. Results name the shared drive a file is in. More files means more attacker-controlled text reaching the model; §6.1's wrapping and §10 apply unchanged.
- **Transport:** Drive REST v3 over HTTPS with the account's access token, which the service already refreshes. `files.list` (`fields` limited to id, name, mimeType, modifiedTime, owners, driveId), `files.export` to `text/plain` (Docs, Slides) and `text/csv` (Sheets), `files.get?alt=media` for `text/*` files under 1 MiB.
- **Caps:** 20 results, 20,000 characters per read, 10 MiB per export request; PDFs and other binaries are refused in Phase 2 (PDF_SEARCH is where text extraction belongs).
- **Endpoints:** `GET /drive/search?q=&limit=` and `GET /drive/file/<id>`, behind the same shared-secret header as `/search` and `/message/<id>`.
- **Status:** `/status` reports each account's granted scopes, which is what `list()` reads (§4.1). The status stays unauthenticated and carries addresses and scope names only.

## 9a. Calendar in the service

Added 2026-10-03 at the user's request. Prerequisite: the same kind of one-off sign-in as Drive's, confirming Google accepts `calendar.readonly` from GNOME's client (GOA lists the client as verified for Calendar); if refused, Calendar leaves this spec.

- **Transport:** Calendar API v3 over HTTPS with the same refreshed token: `calendarList.list` (the account's calendars), `events.list` with `singleEvents=true`, `orderBy=startTime`, `timeMin`/`timeMax`, and `q` for search; `fields` limited to the columns of §6.1.
- **Caps:** 50 events per call, a 366-day window at most, 20,000 characters for one event's description.
- **Endpoints:** `GET /calendar/events?from=&to=&calendar=&q=&limit=` and `GET /calendar/event/<calendar>/<id>`, behind the shared-secret header.
- **Read-only:** no insert, update, delete or RSVP, in the service or the tools. Event descriptions and invitations are attacker-controlled text (anyone can send an invitation); §6.1's wrapping applies.

## 10. Egress and security

- **No new destination for `puffin`.** The hooks return before any directory request; `codex_apps` does not start; the tools reach `127.0.0.1:8767` only. The service reaches `imap.gmail.com`, `oauth2.googleapis.com` and, new, `www.googleapis.com` (Drive and Calendar), as a container, not from `puffin`. The egress audit's allowlist already has 8767; its `--tui` run gains a scripted `/apps` open to show the screen makes no other connection.
- **Read-only by construction** at three layers: the OAuth scopes, the service's verbs, the tools' verbs.
- **Prompt injection:** a shared document is attacker-controlled text, like an email. §6.1's wrapping, §6.3's tool-only advertising and §8's air-gap check are the mitigations; none of them is a guarantee, and the spec does not claim one.
- **The token stays on the machine,** sealed as today (GOA §0); `/apps` never sees it.

## 11. Phases

**Phase 0, read and probe (no product code).**
1. H2's mapping: the app-server protocol's `AppInfo` (`app-server-protocol/src/protocol/v2/apps.rs:118`) against the connectors crate's; whether `paginate_apps` and `apply_app_enabled_state` can be reached from the hook's file without widening visibility (which would cost patch bytes).
2. `/apps` with a `http://localhost` install URL: opened by `webbrowser::open`; the app page's "I've connected it" refresh; the on/off switch writing `[apps.puffin_gmail]`.
3. No browser (SSH session): what Codex prints, and the paste-back path end to end.
4. The MCP server's parent pid is the session's `puffin` (§8).
5. The Drive and Calendar scope tests (§9, §9a): one sign-in asking for both.
6. Patch size of H1 + H2 as written.

**Phase 1, Gmail.** The crate and hooks (§4), `puffin apps serve gmail` (§6.1), the launcher's declarations and prompt (§6.2–6.3), `puffin-admin google start` and `/connect?app=gmail` (§5), the configuration (§7), the air gap (§8).

**Phase 2, Drive.** The service (§9), `/connect?app=drive`, `drive_search`/`drive_read`, the Drive row.

**Phase 3, Calendar.** The service (§9a), `/connect?app=calendar`, `calendar_events`/`calendar_search`, the Calendar row.

**Phase 4, optional.** `$` mentions (§4.3): hook `app/installed` and map a mention of a Puffin app to a one-line nudge naming its tools. Only if Phase 1 shows users reaching for `$gmail`.

### 11.1 Phase 0, read from the source (2026-10-03)

Read from the pinned submodule, nothing compiled; the hooks were drafted in a scratch copy of the four files they touch, not added to `codex-patches/`.

1. **H2's mapping holds, with one correction to §4.1.** The app-server's `AppInfo` in scope of `apps_processor.rs` is the connectors crate's (`connectors/src/app_info.rs:61`), the type `paginate_apps` takes and `app_info_to_api` converts; both it and the protocol's are `#[serde(rename_all = "camelCase")]`. So `list()` must emit **camelCase** keys (`installUrl`, `isAccessible`, `distributionChannel`, `isEnabled`), not the snake_case names of §4.1's table, and a missing `isEnabled` defaults to true. `paginate_apps`, `invalid_request` and `AppToolPolicyEvaluator` are all reachable from the hook's file without widening visibility; `app_info_to_api` passes `install_url` through unchanged. `Feature::Apps` (`apps`) is on by default and is **not** what the launcher's `enable_mcp_apps = false` switches off (that is `Feature::EnableMcpApps`, a different feature), so H1 needs no config change.
2. **A `http://localhost` install URL opens.** An `/apps` row sends `OpenAppLink`, the view sends `OpenUrlInBrowser`, and that calls `webbrowser::open` with no check (`tui/src/app/history_ui.rs:280`). The https-and-`chatgpt.com` check, `validate_external_url`, guards only the elicitation and tool-suggestion links (`app_link_view.rs:99,111`; `bottom_pane/mod.rs:1909`). The refresh and the on/off switch are not exercised without a build: **live check owed**.
3. **No browser:** `webbrowser::open` fails and the TUI prints `Failed to open browser for <url>: <err>`, so the URL is on screen to copy. On an SSH session `localhost:8767` is the remote machine's, so a browser elsewhere cannot reach it; this needs §5.2's paste-back path. **Live check owed.**
4. **Parent pid.** A stdio MCP server is spawned with `Command::new` in a new process group (`rmcp-client/src/stdio_server_launcher.rs:287`), not detached, so its parent is the `puffin` process, as §8 needs. Under the app server (the desktop app) the parent is the app-server process instead; §8's seal lookup must accept that case if the desktop app goes through the app server.
5. **The Drive scope test was not run:** it needs a person at Google's consent screen. Owed, and it gates Phase 2.
6. **Patch size as drafted: 2,385 bytes** (H1, H2, a 10-line helper, two manifest lines), above §4.4's 1.5–2 KB estimate. With it the series would be 36,071 bytes against today's 33,750 cap. Not raised: that is the user's decision (§4.4).

Also found: H1 also opens the `$` mention prefetch (`connector_mentions.rs:9`), which calls `app/installed`. Without a ChatGPT sign-in `runtime_enabled` is false there, so it never refreshes `codex_apps` and answers from the (empty) cached snapshot: no network call, and `$` stays empty as §4.3 intends. Confirm live with the first build.

### Tests

- **The crate:** `list()` against a stub service (connected, not connected, Drive scope missing, service down, timeout); level `on` marks rows unavailable; `offered()` false on a client.
- **The launcher:** declarations present only when offered, connected, enabled and allowed; `puffin_gmail = false` and the environment override win; the prompt names tools when declared and commands otherwise.
- **The MCP server:** tool list, `readOnlyHint`, wrapping, caps, refusal at a sealed `on`, a service error surfaced per account.
- **The service (Python, offline):** `/connect?app=` scope selection, `include_granted_scopes`, refusal of a grant missing the app's scope, Drive endpoints against recorded responses.
- **Live, after `codex build`:** the §3 screen in tmux, connect Gmail from `/apps`, the next session's `gmail_search` call, the egress audit with `--tui`.

## 12. Open questions

All four answered by the user on 2026-10-03:

1. **A connected app mid-session:** restart is acceptable. Tools arrive at the next start or `puffin resume`; no live hook (§6.2).
2. **The Google service:** on by default on a node (§5.1).
3. **Drive's breadth:** My Drive **and** shared drives (§9).
4. **Calendar:** yes, read-only, as a third app (§9a, Phase 3).

Also decided: the patch series may grow to 37.5 KB in all (§4.4).

## 13. Not proposed

- **Sending, drafting, labelling, writing to Drive, or creating, changing or answering calendar events.** Out of scope for every phase; a draft-only Gmail verb would be a separate spec with its own risk section.
- **OpenAI's connectors alongside Puffin's.** With a ChatGPT sign-in, H2 would still answer with Puffin's list, hiding OpenAI's; Puffin never holds that sign-in, so the case does not arise in a supported setup.
- **A web UI change.** The web UI's Gmail tool and Settings page are unchanged; Drive in the web UI is GOA's concern.
