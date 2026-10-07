# Mightling Onyx Integration & Branding (the web UI)

> **Version:** 1.2.0
> **Subject:** Onyx Lite deployment; provider registration; Mightling branding; the four kinds of UI patch; voice, web search, image search and Gmail; telemetry
> **Checked against the code:** 2026-09-29 (`dreamference/chat/`)

---

## Table of Contents

- [1. Overview & Lifecycle](#1-overview--lifecycle)
- [2. `configure`: API Provisioning](#2-configure-api-provisioning)
- [3. Branding (settings, persona, assets)](#3-branding-settings-persona-assets)
- [4. The Four Kinds of UI Patch](#4-the-four-kinds-of-ui-patch)
- [5. Voice (local Whisper + SSRF patch)](#5-voice-local-whisper--ssrf-patch)
- [6. Web Search (SearXNG)](#6-web-search-searxng)
- [7. Image Search](#7-image-search)
- [8. Gmail](#8-gmail)
- [9. Google Sign-In & Telemetry](#9-google-sign-in--telemetry)

---

## 1. Overview & Lifecycle

**Onyx Lite** is the stock Onyx stack with Vespa, Redis, Celery, the model servers and object storage switched off. It is a browser chat UI in front of the same vLLM model the terminal agents use. Its containers are pinned to `mightling-*` names in the lite overlay:
- `ling-web_server-1` (Next.js);
- `mightling-api_server-1` (FastAPI);
- `mightling-relational_db-1` (PostgreSQL);
- `mightling-nginx-1`;
- `ling-code-interpreter-1`.

The UI is served at `http://localhost:3000`, and the desktop window `ling-app` shows the same server. Both of nginx's ports (80 and 3000) are published on **127.0.0.1 only** once `configure` has run (§2 step 1): Docker's default is every interface, which put the UI — and the admin account `configure` creates with a published default password, which can search the user's mail — on the local network. Until 2026-09-29 they were.

Onyx is a service, not an agent, so it lives in `chat/`, not `runner/`:

| Module | Role |
| --- | --- |
| `chat/onyx_runner.py` (`OnyxRunner`) | Lifecycle, `configure`, branding, voice, web search, image search, Gmail, Google sign-in, telemetry |
| `chat/onyx_installer.py` (`OnyxInstaller`) | Finds `onyx-cli`: the venv's `bin/` first, then `PATH`, then `~/.local/bin`. If missing, installs it with pip |
| `chat/onyx_brand_assets.py` | Logo, wordmark and favicon files, and the in-bundle logo paths |
| `chat/onyx_ui_fonts.py`, `onyx_ui_overrides.py`, `onyx_ui_labels.py`, `onyx_ui_scripts.py` | The UI patches (§4) |
| `chat/gmail_*`, `chat/image_search_service.py` | Sidecar services (§7, §8) |

Mightling never writes Onyx's compose files. Everything goes through `onyx-cli`:

| Command (`ling-admin chat …`, alias `onyx`) | Does |
|---|---|
| `start [--no-wait]` | `onyx-cli deploy install --lite --no-prompt`, and waits until healthy unless `--no-wait` |
| `configure [--email] [--password] [--no-web] [--no-brand] [--no-voice] [--no-gmail] [--no-image-search]` | §2 |
| `google-auth [--client-id] [--client-secret]` | §9 |
| `gmail` | (Re-)registers the Gmail tool and refreshes the Mightling assistant's tool list (`connect_gmail()`). It does **not** sign an account in; that happens in the UI (§8) |
| `status` / `logs [-f]` / `stop` | `onyx-cli deploy status` / `logs` / `stop`. `stop` keeps the data |
| `uninstall` | Removes the deployment **and its data** |

---

## 2. `configure`: API Provisioning

Onyx has **no environment variable for the LLM provider**. Providers live in its database, so `configure` drives the admin API that the Admin panel uses. In order:

1. **Telemetry off** (§9), then **loopback only** (`bind_to_loopback()`), except on a node advertised with `ling-admin node enable`, where port 3000 is published on every interface for clients' `ling-app` and port 80 stays on loopback (`web_bind_env()`; [MIGHTLING_NODE §4](./DREAMFERENCE_MIGHTLING_NODE.md)): `HOST_PORT_80=127.0.0.1:80` and `HOST_PORT=127.0.0.1:3000` in the deployment `.env`, which Onyx's compose files already read, and nginx is recreated. Both come first, because applying them recreates containers, and a session cookie taken earlier would point at the replaced API server. Each is a no-op once set.
2. **Authenticate as the admin account** (`ChatAdminCredentials`, `dreamference/chat/chat_admin_credentials.py`):
   - **The password is generated per install** (40 random URL-safe characters plus one each of upper case, lower case, digit and symbol, so any password rule Onyx can enforce passes) and kept in `~/.config/dreamference/chat-admin.json`, mode 0600 in a 0700 folder. The e-mail is `admin@dreamference.dev`, which is not a secret.
   - **A fresh deployment** registers that account (the first account becomes admin) and stores the password.
   - **An install made before 2026-10-07** still has the old published password; `configure` signs in with it once, changes it through Onyx's own `POST /api/password/change-password` (`{old_password, new_password}`), confirms the new one by signing in, and stores it. The new password is stored before the change is requested, and removed again if Onyx refuses, so no outcome leaves an account whose password is nowhere.
   - **`--email` and `--password`** use the user's own account, register it if none exists, and store it.
   - **A password changed in the web UI** is never overwritten: signing in fails, nothing changes, and the message says to pass it once with `--email`/`--password`.
   - `ling-admin chat password` prints the stored account. The desktop app reads the same file at run time to sign its Chat window in; on a client machine there is no file (the account is the node's), so the window shows Onyx's login page.
   - Before 2026-10-07 every install used `admin@dreamference.dev` / `dreamference`, both published in this repository: anyone who could reach port 3000 could sign in as admin (on an advertised node, anyone on the LAN). The old password survives only as `LEGACY_ONYX_PASSWORD`, read by the migration alone (a test holds that).
3. **Register the model** as provider `dreamference-vllm`, type `openai_compatible`:
   - `api_base` is the vLLM URL with loopback rewritten to the Docker **bridge gateway** (`docker network inspect bridge` → e.g. `http://172.17.0.1:8000/v1`), because vLLM uses `--network host` and `localhost` inside Onyx is the container itself;
   - `max_input_tokens` is the recipe's `max_model_len`;
   - `supports_image_input` is the model's `supports_vision`.
4. **Upsert correctly.** `PUT /admin/llm/provider` is **not** an upsert: `?is_creation=true|false` picks the operation, and each value rejects the other case. So the provider is looked up by name first.
   - **Model change:** Onyx refuses to drop the stored default model. The runner first sends the union of the old and new models, then moves the default (`POST /admin/llm/default`), then trims to the new model.
5. **Make it the default** (`POST /admin/llm/default`). For a vision model it also calls `POST /admin/llm/default-vision`. Both settings are needed, or uploads stay refused with "The current model does not support image input".
6. **Web search** (§6), unless `--no-web`.
7. **Gmail tool** (§8), unless `--no-gmail`.
8. **Image search** (§7), unless `--no-image-search`.
9. **Branding** (§3–4), unless `--no-brand`.
10. **Voice** (§5), unless `--no-voice`. It comes last, because it may restart the API server.

---

## 3. Branding (settings, persona, assets)

Onyx's real white-labelling (`application_name`, `hide_onyx_branding`, custom logo and greeting) lives in `ee/`, behind `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES`. That is a **paid** feature, and Mightling does not set it. The rebrand uses community settings plus file patches.

**Settings** (`PUT /admin/settings`):
- `company_name = "Mightling"`;
- `company_description = "Local, air-gapped pair programming on NVIDIA GB10."`;
- `disable_default_assistant = true`, but only once the Mightling assistant exists. Reversing that order would leave a user with no assistant, and a test covers the ordering.

**The Mightling assistant** (a public persona):
- **Description:** empty on purpose. Onyx prints it under the composer, where it would be noise.
- **Tools:** everything `GET /tool` reports, minus `coding_agent`, which overlaps the terminal agents.
- **`system_prompt`:** `MIGHTLING_ASSISTANT_INSTRUCTIONS`. Onyx's persona *name* is only a UI label and is never sent to the model. The instructions say it is Mightling, running on this machine's GB10, and that it can reach the web through its search tool, so it must never claim to have no internet. They also say that when asked for a picture it calls `image_search` and embeds the returned Markdown images.
- **`replace_base_system_prompt = false`:** the instructions are appended, so Onyx's base prompt, which teaches the search and Python tools, is kept.

**Asset files** (`OnyxBrandAssets`): rendered with Pillow from a Tiffany Blue gradient (`TIFFANY_BLUE = #0ABAB5`), then copied with `docker cp` into `/app/public`:
- `logo.png` and `logo-dark.png` (400×400);
- `logotype.png` (2640×733) and `logotype-dark.png` (720×320);
- `logo.svg` (56×56 viewBox, like Onyx's own);
- `onyx.ico`.

Each matches the original's size. These are container file writes, so an image upgrade or `deploy install --force` reverts them, and re-running `configure` restores them. Nothing binary is checked in.

**In-bundle logo and name** (§4.2): the sidebar mark is four shapes on a **64×64** grid inside the JS bundle, not a static file. `ONYX_LOGO_PATHS` maps each Onyx path to Mightling's. `ONYX_APP_NAME_STRINGS` replaces `application_name?.trim()||"Onyx"` and `.trim()}return"Onyx"` with `"Mightling"`.

---

## 4. The Four Kinds of UI Patch

They are applied in this order by `apply_branding()`:
1. `OnyxBrandAssets.install()`;
2. `OnyxUIFonts.install()`;
3. `OnyxUIOverrides.install()`;
4. `OnyxUILabels.install()`;
5. `OnyxUIScripts.install()`.

### 4.1. Fonts: substituted at `@font-face` (`onyx_ui_fonts.py`)

Onyx names its interface face in around forty declarations, mostly through `--font-hanken-grotesk`. Rather than rewriting those, the four `@font-face` blocks are rewritten, so **the CSS still says `Hanken Grotesk` and renders Roboto**:

| Face | Becomes | Weights |
| --- | --- | --- |
| `Hanken Grotesk` | `Roboto.woff2` | 100 900 |
| `KH Teka` | `Roboto.woff2` | 100 900 |
| `DM Mono` | `RobotoMono.woff2` | 100 700 |

The variable TTFs come once from `google/fonts`, are converted to WOFF2 with fontTools, and are cached in `~/.cache/dreamference/fonts`. Onyx then serves them from `/app/public/fonts` (`/fonts/…`), with no font CDN at page load. Next.js registers `public/` routes **at boot**, so `install()` restarts the web server, but only when it actually copied a new file.

### 4.2. Strings: rewritten in the compiled JS (`onyx_ui_labels.py`, plus the brand strings above)

`LABEL_SUBSTITUTIONS` rewrites *quoted* literals, and carries the prop name where the wording is common:
- `children:"New Session"` → `"New"`;
- `children:"Search Chats"` → `"Search"`;
- `"How can I help you today?"` → `"Message"` (both the placeholder and `aria-placeholder`);
- `"Search chat sessions, projects..."` → `"Search chat sessions"`;
- the settings tab holding the Google/Gmail connection → "Gmail Accounts".

A CSS `content` swap would leave the original in the accessibility tree and in find-in-page.

### 4.3. Styles: appended (`onyx_ui_overrides.py`)

`UI_OVERRIDES` concatenates 35 named `Final[str]` rules. Examples:
- `SIDEBAR_CSS`, `MESSAGE_BUBBLE_CSS`, `CHAT_SURFACE_CSS`, `MESSAGE_TEXT_CSS`, `HOVER_TOOLBAR_CSS`;
- `AGENT_AVATAR_CSS`, `MODEL_CHIP_CSS`, `SHARE_BUTTON_CSS`, `FOOTER_CSS`, `HELP_LINK_CSS`;
- `AGENTS_SECTION_CSS`, `PROJECTS_SECTION_CSS`, `SETTINGS_SECTIONS_CSS`;
- `GALLERY_CSS`, `CUSTOM_SCROLLBAR_CSS`, `STREAMING_CURSOR_CSS`.

The block is appended to **every** stylesheet under `/app/.next`, because Next.js splits CSS per route. It opens with `/*dreamference-ui-overrides*/`, and a re-run cuts at the marker and rewrites, so later edits apply.

**Rules of the craft** (details in `CLAUDE.md`):
- **Match Onyx's specificity exactly.** The selected sidebar row is `.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"]` → `#0ABAB5`, and on hover `#09A19C`.
- **Redefine tokens instead of repainting.** Message text is black because `--text-04`/`--text-05` are redefined.
- **Anchor to things a build cannot renumber:** `data-testid`, `data-*`, `aria-label`, `opal-…` BEM classes and SVG viewBoxes. Never Tailwind utilities.
- **Use `:has()`** for unlabelled sections.
- **Scope every white and black rule `html:not(.dark)`**, because those tokens are translucent white in dark mode.
- **The per-message avatar column is hidden with `visibility`,** not `display`, because the timeline grid aligns against it. The selector is `[class*="--timeline-rail-width"]`.
- **Hover-revealed toolbar:** the assistant message controls (`[data-testid^="AgentMessage/"]` except the toolbar) sit at `opacity:0`, and appear on `:hover`/`:focus-within`.
- **Hidden, not removed:** dropping a constant restores the feature on the next `configure`.

### 4.4. Behaviour: injected scripts (`onyx_ui_scripts.py`)

`UI_SCRIPTS`, marked `/*dreamference-ui-scripts*/`, is appended **only to the JS chunks that mention `opal-sidebar-footer`**, the chunk that renders its anchor. It is wrapped in a guard and a `try`, because a throwing top-level statement in a chunk takes the app down. It adds:
- the "Connect Google" button and Gmail Accounts section;
- a custom sidebar scrollbar (`#mightling-scrollbar`), because WebKitGTK's native one can't be styled;
- the settings modal and its synthetic tabs;
- image-tool step handling;
- a gallery with a lightbox.

Two traps are handled:
- Turbopack ends each chunk with `//# debugId=…` and **no trailing newline**, so a block appended directly would sit inside the comment and never run. Newlines are trimmed, and exactly one is written.
- The webview caches chunks, so verify with a fresh cache.

The tests execute the real script under node against a stub DOM (`tests/test_onyx_ui_scripts.py`).

---

## 5. Voice (local Whisper + SSRF patch)

Onyx shows no microphone button until a speech-to-text provider exists, and vLLM has no `/v1/audio/transcriptions`.

- **`dreamference-stt` sidecar:**
  - image `ghcr.io/speaches-ai/speaches:latest-cpu`, running on **CPU** (ctranslate2's CUDA doesn't cover SM121, and dictation-length audio takes about 5 s);
  - model `Systran/faster-whisper-small`, cached in the volume `dreamference-stt-cache`;
  - published on `127.0.0.1:8100` and created on Onyx's network, where it is `http://dreamference-stt:8000/v1`. One created on the default bridge (before 2026-10-01) is replaced by the next `configure`; the volume keeps its model ([DOCKER §6](./DREAMFERENCE_DOCKER.md)).
- **SSRF patch:** Onyx exempts only Azure voice endpoints from its private-address block (`allow_private_network = provider_type.lower() == "azure"`), whatever the SSRF setting. `_allow_local_voice_endpoint()` rewrites that line in `/app/onyx/server/manage/voice/api.py` to `in ("azure", "openai")`, then **restarts the API server**: this is imported Python, unlike the frontend patches.
- **Registration:** the voice provider `dreamference-whisper`, type `openai`, pointing at the sidecar.

`--no-voice` skips all of this. A shim that speaks Azure's protocol would need no patch, and would be the better answer if voice becomes load-bearing.

---

## 6. Web Search (SearXNG)

Onyx has first-class SearXNG support (`WebSearchProviderType.SEARXNG`, no API key). `configure` registers the provider `dreamference-searxng` with `searxng_base_url = http://dreamference-searxng:8080`. Onyx's own base prompt already teaches search-then-open.

- **Container:**
  - `configure` does **not** start SearXNG. `ling-admin searxng start` does (`SearxngSidecar`), and the error messages of `ling-search` and `web_tools.py` name that command. It creates the container on the network `dreamference-sidecars`, never Docker's default bridge, whose DNS is a copy taken at container start ([DOCKER §6](./DREAMFERENCE_DOCKER.md)).
  - It publishes only on loopback, **port 8888** on the host.
  - `_attach_searxng()` joins it to Onyx's network, because the bridge gateway that reaches vLLM doesn't reach it. A container still on the default bridge is recreated on `dreamference-sidecars` first.
- **Why a provider, not a prompt:**
  - The only global prompt hook, `user_preferences`, is capped at 500 characters.
  - Reaching SearXNG through the LLM-driven `open_url` tool would require SSRF protection set to `disabled`. The admin-configured provider's client does no SSRF validation, so the secure `validate_all` default stays untouched.
- **Other users:** `ling-search` and the MCP `web_search` tool use the same container, via `127.0.0.1:8888`.

---

## 7. Image Search

`enable_image_search()` provisions two containers, both joined to Onyx's network:
- the `dreamference-image-search` sidecar (`chat/image_search_service.py` on `python:3-slim`), published on `127.0.0.1:8768` with data in `~/.config/dreamference/image-search`;
- a SigLIP embedding sidecar, `dreamference-siglip` (image `michaelf34/infinity:latest-cpu`, model `google/siglip-base-patch16-224`). It is started best-effort.

It registers an **Image Search** custom tool, and injects an nginx route (`# >>> puffin-image-search`) so that the fetched images are served to the browser under `/puffin-images/`. See `DREAMFERENCE_IMAGE_SEARCH.md`. Infinity publishes amd64 images only, so on GB10 (aarch64) the SigLIP sidecar does not start. The search then skips the SigLIP pre-filter and relies on the vision model's rank-and-filter pass (`DREAMFERENCE_IMAGE_SEARCH.md` §7).

---

## 8. Gmail

- **Service:** `dreamference-gmail` (`chat/gmail_search_service.py` on `python:3-slim`) reads the connected accounts' credentials, over **read-only IMAP**. It is published on `127.0.0.1:8767` and authenticated by the `X-Mightling-Gmail-Token` header.
- **Tool:** `enable_gmail_search()` registers a **Gmail** custom tool, "Search and read the user's Gmail mailbox." It is registered even before an account is connected, because the Connect button lives in this UI.
- **Connecting accounts:** in the web UI, through the injected "Connect Google" button and Settings → Gmail Accounts (§4.4). The OAuth flow runs in the Gmail service (`DREAMFERENCE_GOA.md` §0). `ling-admin chat gmail` only (re-)registers the tool.
- **The terminal agent:** `ling` reaches the same service through `ling-admin gmail` (`DREAMFERENCE_MIGHTLING_GMAIL.md`).

---

## 9. Google Sign-In & Telemetry

- **`google-auth`:** writes `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` to `~/.config/onyx/deployment/.env`, and recreates the API server. The login page then offers Google alongside username and password. The redirect URI is `http://localhost:3000/auth/oauth/callback`.
- **Telemetry:** Onyx's backend posts anonymous records to `telemetry.onyx.app` unless `DISABLE_TELEMETRY=true`. `disable_telemetry()` writes that to the deployment `.env`, and it is a no-op once set (checked by reading the file, to avoid needless recreation). The web container already ships `NEXT_TELEMETRY_DISABLED=1`, and its PostHog/Sentry keys are empty.
