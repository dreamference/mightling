# The Onyx web chat: deployment and configuration

Developer notes behind the web-chat line in `AGENTS.md`. Specs: `specs/DREAMFERENCE_ONYX.md`, `specs/DREAMFERENCE_DOCKER.md`. The patches applied to Onyx's web UI are in [onyx-ui-patches.md](onyx-ui-patches.md).

## Onyx Lite is a service, not an agent

`chat/onyx_runner.py`, which is why it has a package of its own rather than sitting under `runner/`, whose modules are all one-process-per-invocation agent wrappers. It is a browser chat UI in front of the same vLLM endpoint the terminal agents use, so it lives under `ling-admin chat {start,configure,…}` rather than in the `--agent` switch: every entry in that switch is a CLI that Dreamference execs and waits on, and Onyx is a set of long-lived containers. (`ling-admin chat` was `puffin-admin puffin`; its alias is `onyx`.)

Dreamference never writes Onyx's compose files; `onyx-cli deploy install --lite --no-prompt` selects the reduced stack (no Vespa, Redis, Celery, model servers or object storage: API server, web server and PostgreSQL only, ~900 MB resident).

Two things are worth knowing before touching `configure()`:

- Onyx has **no environment variable for the LLM provider**: providers live in its database and are normally created by clicking through the Admin panel, so `configure` drives the same admin API, registering the first account (which becomes admin) if login fails.
- `PUT /admin/llm/provider` **is not an upsert** despite the name: an `is_creation` query flag picks create or update and the endpoint rejects the mismatching case in both directions, which is why the provider is looked up by name first.

The vLLM base URL is rewritten from loopback to the Docker bridge gateway, because vLLM runs with `--network host` while Onyx is on the default bridge, where `localhost` is the Onyx container itself.

## The Mightling rebrand stops at the licence boundary

`apply_branding()` sets `company_name`, creates a public **Mightling** assistant carrying whatever `GET /tool` reports (minus `coding_agent`, which overlaps the terminal agents), and sets `disable_default_assistant` so users land on it: all community-edition settings. Onyx's actual whitelabelling (`application_name`, `hide_onyx_branding`, custom logo/greeting) lives in `ee/` behind `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES` and is a **paid Enterprise feature**; Dreamference does not set that flag, so the browser tab, favicon and top-left logo stay Onyx's.

The assistant is also handed `MIGHTLING_ASSISTANT_INSTRUCTIONS` as its `system_prompt`, because **Onyx's persona name is a UI label and is never sent to the model**: without it the model answers "what is your name?" from pretraining. It goes on the Mightling persona rather than through Onyx's one global hook (`user_preferences`, capped at 500 characters) so assistants the user creates keep their own identity, and `replace_base_system_prompt` stays false so it is appended to the base prompt that teaches the search and Python tools rather than replacing it. The stock assistant is only retired once the Mightling one exists: a test covers that ordering, because reversing it leaves a user with no assistant at all. `--no-brand` skips the whole step.

## Logos are a separate matter from the settings

`/logo.png`, `/logo-dark.png`, `/logotype*.png`, `/logo.svg` and `/onyx.ico` are ordinary static files the Next.js server hands out, so `chat/onyx_brand_assets.py` renders Mightling versions with Pillow (from a Tiffany Blue gradient: `TIFFANY_BLUE` is defined there and imported by `onyx_ui_overrides.py` for the selected sidebar row, so the favicon and the UI cannot drift apart) and `docker cp`s them into the web-server container: a file swap, not the licensed `use_custom_logo` path. They match the originals' dimensions exactly, since the frontend lays them out against those aspect ratios. Being container-filesystem writes, they do **not** survive an image upgrade or `deploy install --force`; re-running `ling-admin chat configure` restores them, and nothing binary is checked into the repo.

## Telemetry is on until told otherwise

Onyx's backend posts anonymous records (version, sign-ups, usage, latency, failures) to `https://telemetry.onyx.app/anonymous_telemetry`, and the switch defaults off: `DISABLE_TELEMETRY = os.environ.get("DISABLE_TELEMETRY", "").lower() == "true"`. `configure()` sets it, first thing and before authenticating, because applying it recreates the API server and a session cookie taken beforehand would point at a container about to be replaced. It is a no-op once set, checked by reading the `.env`, since `configure()` runs often and recreating a container is not free.

`bind_to_loopback()` works the same way. Onyx's nginx publishes `${HOST_PORT_80:-80}:80` and `${HOST_PORT:-3000}:80` on **every interface** by default, which put the admin account (created until 2026-10-07 with a password published in this repository, now `LEGACY_ONYX_PASSWORD`, read only to migrate; the account's password is generated per install and kept in `~/.config/dreamference/chat-admin.json`, `ChatAdminCredentials`), able to search the user's mail, on the LAN. `configure()` sets `HOST_PORT_80=127.0.0.1:80` and `HOST_PORT=127.0.0.1:3000` in the `.env` and recreates nginx only. (An advertised node publishes port 3000 on every interface again; see [node.md](node.md).)

The backend is the only live channel: the web container already ships `NEXT_TELEMETRY_DISABLED=1` and its PostHog and Sentry keys are present but empty, leaving those SDKs inert.

## The microphone needs a second model server

Onyx's voice subsystem is complete but shows no mic button until an STT provider is registered, and vLLM cannot supply one: it serves a single model and has no `/v1/audio/transcriptions`. `enable_voice()` runs `speaches` (Whisper behind a `/v1/audio/transcriptions` API) as the `dream-stt` sidecar on **CPU** (ctranslate2's CUDA support does not cover SM121, and dictation-length audio transcribes in ~5 s on GB10's cores with no GPU contention), creates it on Onyx's network, and registers it as an `openai` voice provider.

One obstacle is not configurable: Onyx hardcodes the private-address exemption for voice endpoints to Azure alone (`allow_private_network = provider_type.lower() == "azure"`), ignoring the SSRF Protection setting, and its Azure provider speaks Azure Speech REST rather than the `/v1/audio/transcriptions` protocol. `_allow_local_voice_endpoint()` widens that one line and **restarts the API server** (unlike the frontend patches, this is already-imported Python). A shim presenting Azure's protocol in front of Whisper would need no patch and survive upgrades: the better answer if this becomes load-bearing. `--no-voice` skips it all.

## Image input is a client-side claim, not a server capability

`Qwen3_5MoeForConditionalGeneration` carries a `vision_config`, so vLLM accepted images for the (since removed) Qwen3.5-122B entries without being told, but Onyx refuses the upload with *"The current model does not support image input"* unless its own model entry says otherwise. `ModelSpec.supports_vision` records it (verified against each checkpoint's `config.json`, never inferred from the alias), `model_supports_vision()` exposes it, and `configure()` sends it as `supports_image_input` **and** registers `POST /admin/llm/default-vision`: two separate settings, and uploads stay refused if only the first is set.

## Onyx web search is a provider registration, not a prompt

Unlike the Codex runner, which appends `WEB_ACCESS_INSTRUCTIONS` to the system prompt because Codex has no search tool, Onyx ships **first-class SearXNG support** (`WebSearchProviderType.SEARXNG`, no API key), so `configure()` registers it at `POST /admin/web-search/search-providers` and Onyx's own base prompt already knows to search and then open results. `--no-web` skips it.

Two constraints make the alternatives worse and are worth not rediscovering:

- Appending to the prompt is capped at **500 characters** (`user_preferences` is the only global hook; the default assistant is a *builtin* persona and the API refuses to modify one).
- Reaching SearXNG through the LLM-driven `open_url` tool requires SSRF protection set all the way to `disabled`, since `outbound_allow_private_network()` is true for that level alone. The provider path is admin-configured and its client does no SSRF validation, so it reaches a private container address with the secure `validate_all` default untouched.

SearXNG publishes only on `127.0.0.1`, so `_attach_searxng()` joins its container to Onyx's network: the bridge gateway that reaches vLLM does not reach it.

## No sidecar is created on Docker's default bridge

`chat/sidecar_network.py`, `chat/searxng_sidecar.py`; `specs/DREAMFERENCE_DOCKER.md` §6. A container's DNS is decided by the network it is *created* on: the default bridge hands it a copy of the host's DNS servers taken at start, a user-defined network forwards each lookup to the host's resolver.

After the reboot of 2026-10-01 Docker restarted SearXNG and the speech-to-text server five seconds before the Wi-Fi had a DNS server; both kept an empty list and every search failed, although both were *also* on Onyx's network (joining later changes nothing). SearXNG is now started with `ling-admin searxng start` on the network `dreamference-sidecars` (it must work without the web UI), speech-to-text is created on Onyx's network, and `configure` replaces either one it finds on the default bridge. No resolver is hard-coded: `--dns` replaces the host's list and would send every lookup past the machine's own resolver.

## Names kept

The web chat's `/puffin-images/` route keeps its old name on purpose (`specs/DREAMFERENCE_RENAME_MIGHTLING.md`).
