# Dreamference Onyx Integration & Branding Overrides

> **Version:** 1.2.0
> **Subject:** Onyx Lite Deployment, Puffin Rebrand, Telegram Typography, UI Stylesheet Patches, SSRF / Local Voice STT, SearXNG Web Search

---

## Table of Contents

- [1. Architectural Overview & Deployment Lifecycle](#1-architectural-overview--deployment-lifecycle)
- [2. Config-Driven API Provisioning](#2-config-driven-api-provisioning)
- [3. Brand Identity & Vector Asset Overrides (Puffin)](#3-brand-identity--vector-asset-overrides-puffin)
- [4. Next.js Bundle & Sidebar Label Rewriting](#4-nextjs-bundle--sidebar-label-rewriting)
- [5. Telegram-Style Typography Substitution](#5-telegram-style-typography-substitution)
- [6. Minimalist UI Layout & CSS Overrides](#6-minimalist-ui-layout--css-overrides)
- [7. Local Voice Transcription & SSRF Exemption Patch](#7-local-voice-transcription--ssrf-exemption-patch)
- [8. Secure Web Search Integration](#8-secure-web-search-integration)

--- 

## 1. Architectural Overview & Deployment Lifecycle

Dreamference integrates **Onyx Lite**—a self-hosted, browser-based chat UI—to provide a user-friendly conversational interface backed by the same local vLLM model serving terminal agents. 

Onyx Lite is not a separate application; rather, it is the stock Onyx stack configured to run with unnecessary components (the vector database, Redis, Celery workers, model servers, and object storage) switched off. This minimizes resource overhead on the NVIDIA GB10 hardware, running only:
- **web_server**: Next.js-based frontend
- **api_server**: FastAPI-based backend APIs
- **db**: PostgreSQL database

### 1.1. Package Structure

The Onyx integration and override engine is implemented across the following modules:
- `dreamference/runner/onyx_runner.py`: Orchestrates the stack lifecycle, API configurations, and rebrand pipelines.
- `dreamference/runner/onyx_installer.py`: Detects and manages installation of `onyx-cli`.
- `dreamference/runner/onyx_brand_assets.py`: Renders custom vector logos/icons and patches Next.js bundles to inject the brand assets.
- `dreamference/runner/onyx_ui_fonts.py`: Fetches, cache-converts, installs, and injects Roboto & Roboto Mono typography.
- `dreamference/runner/onyx_ui_labels.py`: Shortens long sidebar navigation labels within compiled JSX assets.
- `dreamference/runner/onyx_ui_overrides.py`: Appends CSS overrides for minimalist design, a white layout, and hover actions.

### 1.2. Lifecycle Operations

Dreamference controls the deployment strictly through the official `onyx-cli` tool. The main command flows map as follows:

| CLI Command | Internal Operation | Description |
|---|---|---|
| `dream onyx start` | `onyx-cli deploy install --lite --no-prompt` | Provisions and starts containers. Defaults to waiting until all containers are healthy unless `--no-wait` is passed. |
| `dream onyx configure` | (API & container injections) | Orchestrates provider registrations, branding swaps, and local sidecar hooks. |
| `dream onyx status` | `onyx-cli deploy status` | Reports deployment version, container state, and active health checks. |
| `dream onyx logs [--follow]` | `onyx-cli deploy logs` | Streams combined standard output/error from all container services. |
| `dream onyx stop` | `onyx-cli deploy stop` | Shuts down containers while preserving all data (PostgreSQL volumes remain). |
| `dream onyx uninstall` | `onyx-cli deploy uninstall` | Destroys containers and permanently deletes PostgreSQL databases/volumes. |

### 1.3. CLI Binary Resolution (`OnyxInstaller`)

To ensure standard setup on host systems, `OnyxInstaller` resolves the path to the `onyx-cli` executable by checking the following locations in order:
1. The active Python virtual environment path (`sys.prefix/bin/onyx-cli`) to prefer local `pip install onyx-cli`.
2. Environment `PATH` lookup via `shutil.which`.
3. User local binaries (`~/.local/bin/onyx-cli`).

If no executable is found, it automatically installs `onyx-cli` via `pip install --silent onyx-cli` before proceeding.

---

## 2. Config-Driven API Provisioning

Because Onyx does not configure its default model providers through environment variables, Dreamference automates database configuration by directly interacting with the Onyx API endpoints. 

Executing `dream onyx configure` triggers an idempotent, multi-stage administrative setup:

```
[dream onyx configure]
       │
       ▼
1. Authenticate / Register Admin (`admin@dreamference.dev`)
       │
       ▼
2. Resolve Docker Bridge Gateway (172.17.0.1)
       │
       ▼
3. PUT `/admin/llm/provider` (upsert OpenAI-compatible model link)
       │
       ├─► [Vision Supported?] ──► Set default-vision-model & supports_image_input=True
       ▼
4. Connect SearXNG (Web Search Container)
       │
       ▼
5. Apply Puffin Branding & Override Static Web Assets
       │
       ▼
6. Deploy Local Whisper Container (`speaches`) & Patch Voice API SSRF Check
```

### 2.1. Container Network Loopback Resolution

Dreamference launches vLLM with `--network host`, binding it to `localhost:8000` on the host machine. However, Onyx's containers run on Docker's default bridge network, where `localhost` resolves inside the container itself. 

To bridge this gap, `OnyxRunner` dynamically retrieves the Docker bridge network gateway (typically `172.17.0.1`) and rewrites loopback addresses:

```python
# dreamference/runner/onyx_runner.py
# Resolves localhost to the bridge gateway IP
gateway = subprocess.run(
    ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
    capture_output=True, text=True
).stdout.strip()
```

This transforms the host API URL `http://localhost:8000` into `http://172.17.0.1:8000/v1` for communication inside the container.

### 2.2. Model Integration & Vision Support

The local vLLM provider is registered under the identifier `dreamference-vllm` using the `openai_compatible` provider format. To ensure stability:
1. The model provider setup checks `GET /admin/llm/provider` to see if `dreamference-vllm` exists.
2. If it exists, the runner sends a `PUT` request with the provider's existing database ID to perform an update; otherwise, it triggers a creation request (`is_creation=true`).
3. Max input tokens are bound to `max_model_len` as declared in the model registry.
4. **Vision Gating**: If the active model supports vision, the runner overrides `supports_image_input` to `True`. Additionally, it calls `POST /admin/llm/default-vision` to route visual prompt structures. This removes the stock message: *"The current model does not support image input."*

---

## 3. Brand Identity & Vector Asset Overrides (Puffin)

Onyx reserves its full white-label capabilities (custom window titles, hiding copyright headers, replacing the default workspace branding) for its Paid Enterprise Edition (`ee/` module), gated by the environment variable `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES`. 

Because Dreamference strictly complies with open-source licenses, it does not force-enable the Enterprise flag. Instead, it applies rebranding via a two-tier approach:
- **Tier 1 (Community Settings API)**: Standard database settings and persona configs are updated.
- **Tier 2 (Static Container Modifications)**: Low-level modifications are made directly on Next.js compiled frontend bundles in the running container.

### 3.1. Rebranding Conversational Configs

Through the FastAPI settings endpoints, the database parameters are updated as follows:
- **Company Name**: `"Puffin"`
- **Company Description**: `"Local, air-gapped pair programming on NVIDIA GB10."`
- **Default Assistant Disabled**: True (the stock default assistant is disabled, forcing users to land on the custom Puffin persona).

### 3.2. Puffin Persona Initialization

An independent assistant persona called **Puffin** is created to avoid tampering with Onyx's non-editable built-in persona:
- **Name / Description**: `Puffin` / `Local pair programmer on GB10 — web search, Python, and file reading, served entirely from this machine.`
- **System Instructions**:
  ```
  You are Puffin, an AI assistant that runs entirely on this machine — a local, air-gapped 
  deployment on NVIDIA GB10 hardware. When you are asked your name, who you are, or what you 
  are, say that you are Puffin. Do not describe yourself as a generic assistant and do not 
  answer with the name of the model you are served from.
  ```
- **System Prompts Configuration**: `replace_base_system_prompt` is set to `False`. The prompt is appended to Onyx's base system instructions so that tool usage descriptions (search, Python execution) remain intact.
- **Tool Exclusions**: The coding agent tool (`coding_agent`) is excluded to prevent conflicts with Dreamference's specialized terminal-based agents running concurrently.

### 3.3. Vector Asset Injection (`OnyxBrandAssets`)

`OnyxBrandAssets` generates Puffin-themed SVGs and copies them over the web container's default assets:
- **Vector Favicon**: Renders an `.ico` vector favicon asset and places it at `/app/public/favicon.ico` (resolving as `/favicon.ico`).
- **Sidebar & Logo PNGs**: Renders brand logos and uploads them to `/app/public/logo.png` and `/app/public/images/logo.png`.

---

## 4. Next.js Bundle & Sidebar Label Rewriting

Because the web UI's sidebar logo and wordmark are written directly as inline SVG React components inside Next.js bundle JS chunks rather than references to static images, standard stylesheet manipulation cannot hide or replace them.

`OnyxBrandAssets` and `OnyxUILabels` solve this by recursively parsing the Next.js bundle files (`.js` and `.mjs` in `/app/.next` and `/app/public`) directly inside the container and replacing matching string definitions.

### 4.1. SVG Component Overwrite

The left-panel SVG mark is drawn by Onyx using coordinate-driven shapes. `OnyxBrandAssets` locates this component by matching its unique viewport definitions and substitutes the vector coordinates with Puffin's brand mark:

| Target Component | Matching Pattern | Substitute Path Data |
|---|---|---|
| **Onyx Logo Mark** | ViewBox `0 0 56 56` shapes | Puffin's Teal Shield and Crest coordinates |
| **Onyx Letter Wordmark** | SVG paths drawing letters "o-n-y-x" | Puffin's customized wordmark coordinates |

Additionally, references to `"Onyx"` within whitelabel fallback JS evaluations are surgically replaced:
- `application_name?.trim()||"Onyx"` ➔ `application_name?.trim()||"Puffin"`
- `.trim()}return"Onyx"` ➔ `.trim()}return"Puffin"`

### 4.2. Shortening Navigation Labels (`OnyxUILabels`)

To clean up the sidebar and give it a polished, compact look, `OnyxUILabels` searches through compiled chunks to shorten text inside JSX definitions:

```javascript
// Label substitution map used by the runner's node evaluator script
LABEL_SUBSTITUTIONS = {
    'children:"New Session"': 'children:"New"',
    'children:"Search Chats"': 'children:"Search"',
    '"How can I help you today?"': '"Message"' // Replaces both label & placeholder
}
```

This rewrite logic parses files in parallel inside the running container. It evaluates and replaces string segments, keeping count of modified files.

---

## 5. Telegram-Style Typography Substitution

Onyx's default fonts are Hanken Grotesk, KH Teka (headings), and DM Mono (code syntax). Replacing forty separate font-family definitions in CSS variables is fragile and breaks whenever Tailwind utilities are compiled.

`OnyxUIFonts` sidesteps this by intercepting and overwriting typography at the **`@font-face` source level**.

### 5.1. Target Mappings

The original fonts map to highly polished, readability-optimized alternatives used by Telegram:

| Original Font Name | Replacement Font | Target Style Range |
|---|---|---|
| `Hanken Grotesk` | **Roboto** (`Roboto.woff2`) | Weight range `100 900` |
| `KH Teka` | **Roboto** (`Roboto.woff2`) | Weight range `100 900` (replaces old header fonts) |
| `DM Mono` | **Roboto Mono** (`RobotoMono.woff2`) | Weight range `100 700` |

### 5.2. Compilation and Local Delivery

To preserve air-gapped security, fonts are never served from a public CDN at runtime:
1. On the host, raw variable TTF fonts are pulled once from the official Google Fonts repository.
2. They are cache-converted to highly optimized WOFF2 structures and stored under `~/.cache/dreamference/fonts`.
3. The `.woff2` files are copied directly into the web container's public directory (`/app/public/fonts`).
4. **CSS Patching**: Every compiled Next.js stylesheet under `/app/.next` is parsed. The `@font-face` blocks are surgically rewritten:

```css
/* BEFORE */
@font-face{font-family:'Hanken Grotesk';src:url(/_next/static/media/hanken-grotesk.woff2) ...}

/* AFTER */
@font-face{font-family:'Hanken Grotesk';src:url(/fonts/Roboto.woff2);font-weight:100 900}
```

Since Next.js indexes the `public/` folder only once at boot, `OnyxUIFonts` restarts the web container (`docker restart <web_container>`) immediately after copying the fonts to prevent font-loading 404s.

---

## 6. Minimalist UI Layout & CSS Overrides

`OnyxUIOverrides` appends Dreamference's custom CSS styles to every Next.js route stylesheet. To remain robust across software updates, selectors are pinned to stable React attributes (`data-testid`, BEM classes like `.opal-sidebar-root__column`, and custom element IDs) rather than compilation-unstable Tailwind utility names.

The overrides open with a unique marker `/*dreamference-ui-overrides*/`, allowing subsequent configuration sweeps to safely wipe and overwrite previous blocks without duplicate appends.

### 6.1. White Sidebar Theme (`SIDEBAR_CSS`)

Onyx tints the left sidebar grey by default. To match Telegram's light aesthetic, the panel is turned pure white, and selected chat sessions are highlighted in Tiffany Blue:

```css
html:not(.dark) .opal-sidebar-root__column {
  background-color: var(--background-tint-00); /* Adaptive white token */
}

/* Selected row background highlighting */
.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"] {
  background-color: #0EB1AB; /* Tiffany Blue brand color */
  --interactive-foreground: #fff;
  --interactive-foreground-icon: #fff;
}

/* Hover over selected rows */
.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"]:hover:not([data-disabled]) {
  background-color: #09A19C; /* Darker Tiffany hover tint */
}
```

### 6.2. Chat Surface & Message Bubbles

To create a clean interface, the chat canvas is changed from grey to white, and user messages are given distinct borders:

```css
/* Whiten canvas body and shell wrappers */
html:not(.dark) body, 
html:not(.dark) .bg-background.min-h-screen {
  background-color: var(--background-tint-00);
}

/* User message bubbles: plain white with a clean boundary shadow */
html:not(.dark) #onyx-human-message .bg-background-tint-02 {
  background-color: var(--background-tint-00);
  box-shadow: 0 1px 2px rgba(0,0,0,.08);
}
```

### 6.3. Action Toolbars and Avatar Hiding

- **Avatar Clutter**: Hides the octagonal user/agent avatar icons on the left of each message, streamlining the chat thread and reclaiming space:
  ```css
  [data-testid="onyx-ai-message"] .flex-shrink-0.w-8.h-8 {
    display: none !important;
  }
  ```
- **Reveal-on-Hover Message Controls**: Assistant controls (like, dislike, retry, copy, TTS) are hidden by default to keep the screen clean. Hovering or focusing a message transitions the opacity smoothly:
  ```css
  [data-testid="onyx-ai-message"] [data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"]) {
    opacity: 0;
    pointer-events: none;
    transition: opacity .12s ease-in-out;
  }

  [data-testid="onyx-ai-message"]:hover [data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"]),
  [data-testid="onyx-ai-message"]:focus-within [data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"]) {
    opacity: 1;
    pointer-events: auto;
  }
  ```

---

## 7. Local Voice Transcription & SSRF Exemption Patch

Onyx features full voice dictation support. However, it displays no microphone button until a valid Speech-to-Text (STT) provider is registered. Dreamference deploys a local STT service and integrates it into the stack.

### 7.1. Whisper STT Sidecar Deployment (`dream-stt`)

A Whisper transcription sidecar is launched as a background container:
- **Image**: `ghcr.io/speaches-ai/speaches:latest-cpu` (CPU processing is fast enough on Blackwell host cores and avoids SM121 CUDA conflicts).
- **Cached Model**: `Systran/faster-whisper-small` (cached in a persistent volume `dream-stt-cache`).
- **Network Routing**: The container is connected to Onyx's private bridge network, exposing its transcription endpoints internally at `http://dream-stt:8000/v1`.

### 7.2. SSRF Check Bypass Patch

Onyx implements Server-Side Request Forgery (SSRF) protections, preventing the API server from contacting loopback or private network endpoints. For voice STT, it hardcodes an exemption **only** for Microsoft Azure:

```python
# onyx/server/manage/voice/api.py (Original)
allow_private_network = provider_type.lower() == "azure"
```

Because of this, registering the local sidecar at `http://dream-stt:8000` is blocked and fails with a connection error. 

To resolve this, `OnyxRunner` executes a Python script inside the running API server container to patch the SSRF check:

```python
# onyx/server/manage/voice/api.py (Patched)
allow_private_network = provider_type.lower() in ("azure", "openai")
```

Once patched, the API server container is restarted and monitored until its status returns to healthy. The speech provider is then registered as an `openai` type pointing to `http://dream-stt:8000/v1` with the local Whisper model, enabling the microphone button in the web UI.

---

## 8. Secure Web Search Integration

Onyx's default assistant is granted web search access using the local **SearXNG** instance. 

To enable search without compromising system security:
1. Rather than writing instructions into the model system prompts, Dreamference registers SearXNG as an admin-configured search provider.
2. The SearXNG container (which publishes strictly on loopback `127.0.0.1:8080` to prevent external exposure) is joined to Onyx's private bridge network:
   ```bash
   docker network connect <onyx_network> searxng
   ```
3. A search provider named `dreamference-searxng` is created, pointing to `http://searxng:8080`.
4. This keeps the default secure SSRF protections active globally while allowing search traffic through the admin-configured network route.
