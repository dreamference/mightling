"""
Onyx Lite Deployment Runner for Dreamference.

This module provides the OnyxRunner class which deploys and manages a self-hosted Onyx Lite
stack pointed at the local vLLM endpoint, giving a browser chat UI in front of the same model
the terminal agents use.

Onyx Lite is not a separate application -- it is the stock Onyx images with the vector database,
Redis, Celery workers, model servers and object storage switched off, leaving an API server, a
web server and PostgreSQL. `onyx-cli deploy install --lite` selects that configuration, so this
module drives the CLI rather than writing compose files of its own.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Final, List, Optional, Tuple

from dreamference.config import DreamferenceConfig
from dreamference.hardware import (
    model_key_for_served_id,
    resolve_model_hf_repo,
    get_model_launch_overrides,
    model_supports_vision,
)
from dreamference.chat.onyx_brand_assets import OnyxBrandAssets
from dreamference.chat.onyx_ui_fonts import OnyxUIFonts
from dreamference.chat.onyx_ui_labels import OnyxUILabels
from dreamference.chat.onyx_ui_overrides import OnyxUIOverrides
from dreamference.chat.onyx_ui_scripts import OnyxUIScripts
from dreamference.chat.onyx_installer import OnyxInstaller
from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME, SearxngSidecar
from dreamference.chat.sidecar_network import SidecarNetwork
from dreamference.vllm_server import VLLMServerManager

# Onyx's web UI, as published by the stock lite deployment.
DEFAULT_ONYX_WEB_URL: Final[str] = "http://localhost:3000"

# Onyx's provider type for a plain OpenAI-compatible endpoint addressed by base URL, which is
# exactly what vLLM serves. `litellm_proxy` would also connect, but it is the descriptor for a
# LiteLLM proxy sitting in front of other providers, and picking it would misdescribe the setup.
# Both are "generic" in Onyx's registry -- neither ships a known-model list -- so the served model
# has to be named explicitly in model_configurations either way.
ONYX_PROVIDER_TYPE: Final[str] = "openai_compatible"
ONYX_PROVIDER_NAME: Final[str] = "dreamference-vllm"

# vLLM does not check bearer tokens unless it was launched with --api-key, but Onyx requires the
# field to be non-empty for this provider type.
ONYX_PLACEHOLDER_API_KEY: Final[str] = "dreamference-local"

# The first account to sign up becomes the admin, so these are the credentials Dreamference
# registers with when no account exists yet. Weak on purpose and printed on use: this is a
# single-node air-gapped box, and a password the user cannot discover would be worse than a
# well-known one they can change in the UI.
#
# `.dev` rather than the more natural `.local` because Onyx validates the address with
# email-validator, which rejects special-use and reserved domains -- `.local`, `.localhost`,
# `.test` and `.invalid` all fail. No mail is ever sent to it.
DEFAULT_ONYX_EMAIL: Final[str] = "admin@dreamference.dev"
DEFAULT_ONYX_PASSWORD: Final[str] = "dreamference"

# Google sign-in, alongside the password form rather than instead of it.
#
# Onyx used to pick one login method with `AUTH_TYPE`; in 4.5 that single-provider mode was removed
# (setting `AUTH_TYPE=google_oauth` now only logs a warning and falls back to basic). What replaced
# it is additive: `OAUTH_ENABLED` is simply `bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)`, and
# when true the app mounts a Google router *beside* the password routes. `GET /auth/type` reports
# `oauth_enabled` and `password_auth_enabled` as separate flags, which is how the login page ends up
# offering both. So enabling Google is two environment variables and nothing else -- in particular
# AUTH_TYPE must be left alone.
#
# The variables go in the deployment's `.env`, which `onyx-cli` generates and every service reads
# through `env_file`. That is configuration, not composition: Dreamference still never writes Onyx's
# compose files, and it touches only these two keys, leaving the rest of the file as found.
ONYX_ENV_FILE: Final[str] = os.path.expanduser("~/.config/onyx/deployment/.env")
ONYX_OAUTH_ID_KEY: Final[str] = "OAUTH_CLIENT_ID"
ONYX_OAUTH_SECRET_KEY: Final[str] = "OAUTH_CLIENT_SECRET"

# Where Google must send the user back. `WEB_DOMAIN` defaults to the same loopback origin the UI is
# served from, and Onyx builds the callback as `{WEB_DOMAIN}/auth/oauth/callback` -- this string has
# to be registered on the Google client verbatim or the sign-in fails at the redirect.
GOOGLE_REDIRECT_URI: Final[str] = f"{DEFAULT_ONYX_WEB_URL}/auth/oauth/callback"

# Onyx's backend posts anonymous usage records -- version, sign-ups, usage, latency, failures -- to
# `https://telemetry.onyx.app/anonymous_telemetry`, and the switch defaults to *off*:
# `DISABLE_TELEMETRY = os.environ.get("DISABLE_TELEMETRY", "").lower() == "true"`. An outbound
# channel that a local deployment never asked for is worth closing, so `configure()` sets it.
#
# The backend is the only live channel. The web container already ships `NEXT_TELEMETRY_DISABLED=1`
# for Next.js's own reporting, and its PostHog and Sentry keys are present but empty, which leaves
# those SDKs inert -- so there is nothing to switch off there and no point pretending otherwise.
ONYX_PRIVACY_ENV: Final[dict] = {"DISABLE_TELEMETRY": "true"}

# Onyx's nginx publishes `${HOST_PORT_80:-80}:80` and `${HOST_PORT:-3000}:80`, which Docker binds
# on every interface -- so the web UI, and the admin account `configure()` creates with a published
# default password (DEFAULT_ONYX_PASSWORD), were reachable from anything on the local network, and
# that account can search the user's mail through the Gmail tool. Prefixing the host side with
# 127.0.0.1 keeps both ports on this machine; the browser and the desktop app use localhost:3000
# either way. Done through the `.env` Onyx already reads, so its compose files stay untouched.
ONYX_LOOPBACK_ENV: Final[dict] = {"HOST_PORT_80": "127.0.0.1:80", "HOST_PORT": "127.0.0.1:3000"}

# Gmail search, registered the same way web search is: a capability Onyx already knows how to call,
# rather than instructions bolted onto a prompt.
#
# It runs as a container on Onyx's network for the same reason SearXNG does -- addressable by name,
# no new port on the host. That it is on a *private* network is not a security boundary here:
# Onyx's custom-tool client calls whatever URL the tool names and performs no SSRF validation, so
# anything else on that network could reach a service holding a live mailbox credential. The shared
# secret header is what actually protects it.
GMAIL_CONTAINER_NAME: Final[str] = "dreamference-gmail"
GMAIL_CONTAINER_URL: Final[str] = f"http://{GMAIL_CONTAINER_NAME}:8000"
GMAIL_HOST_PORT: Final[int] = 8767
GMAIL_SERVICE_IMAGE: Final[str] = "python:3-slim"
GMAIL_TOOL_NAME: Final[str] = "Gmail"
GMAIL_TOOL_DESCRIPTION: Final[str] = "Search and read the user's Gmail mailbox."

# Kept in step with the service module, which enforces it.
GMAIL_AUTH_HEADER: Final[str] = "X-Mightling-Gmail-Token"
SEARXNG_CONTAINER_URL: Final[str] = f"http://{SEARXNG_CONTAINER_NAME}:8080"

# Onyx ships first-class SearXNG support as a web *search provider*, which is why this module
# configures one instead of appending web instructions to the system prompt the way the Codex
# runner does. That was the first approach here and it was the wrong shape: Onyx's own base prompt
# already tells the model to search and then open the results, so the instructions were redundant
# the moment a real `web_search` tool existed. The prompt route also needed SSRF protection turned
# off wholesale -- `open_url` is LLM-initiated, and Onyx only allows those onto private addresses
# at the `disabled` level -- whereas the provider path is admin-configured and reaches SearXNG
# with the secure default left in place.
ONYX_SEARCH_PROVIDER_NAME: Final[str] = "dreamference-searxng"

# How far the rebrand can go without a licence. Onyx's whitelabelling -- `application_name`,
# `hide_onyx_branding`, custom logo and greeting -- lives in `ee/`, behind the
# ENABLE_PAID_ENTERPRISE_EDITION_FEATURES flag, and is a paid Enterprise feature. Dreamference does
# not switch that on. What the community edition does allow is a company name, a custom assistant,
# and retiring the stock one, which is what this applies. The window title, favicon and the logo in
# the top-left stay Onyx's.
MIGHTLING_COMPANY_NAME: Final[str] = "Mightling"
MIGHTLING_COMPANY_DESCRIPTION: Final[str] = (
    "Local, air-gapped pair programming on NVIDIA GB10."
)
MIGHTLING_ASSISTANT_NAME: Final[str] = "Mightling"
# The same assistant's name before the product became Mightling. `configure` renames that persona
# (the PATCH below carries the new name) rather than creating a second assistant beside it.
LEGACY_ASSISTANT_NAME: Final[str] = "Puffin"
# Empty on purpose. Onyx prints the assistant's description under the composer on the new-chat
# screen, where a sentence of deployment trivia is noise rather than orientation -- the greeting
# above it already says what this is.
MIGHTLING_ASSISTANT_DESCRIPTION: Final[str] = ""

# What the assistant is told about itself.
#
# Without this the model answers "what is your name?" from its own pretraining -- "I'm an AI
# assistant" -- because nothing in the request mentions Mightling. Onyx's persona name is a label in
# the UI; it is not sent to the model. This is, and it goes on the Mightling persona rather than
# through Onyx's one global prompt hook (`user_preferences`, capped at 500 characters) so that
# assistants the user creates themselves keep their own identity.
#
# The last sentence is here because its absence caused a bug. An earlier version opened with
# "air-gapped", and the model believed it: asked for the weather it explained at length that it had
# no internet and suggested looking out of the window -- while holding a working web_search tool.
# The deployment is local, not disconnected, and the prompt now says which.
#
# `replace_base_system_prompt` stays false, so this is appended to Onyx's base prompt rather than
# replacing it -- the base prompt is what tells the model how to use the search and Python tools,
# and dropping it to introduce a name would be a poor trade.
MIGHTLING_ASSISTANT_INSTRUCTIONS: Final[str] = (
    "You are Mightling, an AI assistant served by a model running on this machine's own NVIDIA GB10 "
    "hardware. When you are asked your name, who you are, or what you are, say that you are "
    "Mightling. Do not describe yourself as a generic assistant and do not answer with the name of "
    "the model you are served from. You can reach the live web through your search tool: use it "
    "for anything current, and never tell the user you have no internet access. When the user "
    "asks to see a picture, photo, or image of something, call the image_search tool and place "
    "the Markdown image embeds it returns into your reply verbatim -- you CAN display images "
    "this way, so never answer that you are unable to embed or show them."
)

# Onyx's agentic coding tool, left off the Mightling assistant deliberately: Dreamference's own
# terminal agents cover that ground with the same model, and enabling both invites the two to
# edit the same tree from different directions.
MIGHTLING_EXCLUDED_TOOLS: Final[frozenset] = frozenset({"coding_agent"})

# Speech-to-text. Onyx has a complete voice subsystem and shows no microphone button until an STT
# provider is registered, so the button is a configuration question, not a missing feature.
#
# The model is transcribed locally by a small Whisper server -- `speaches`, which speaks OpenAI's
# /v1/audio/transcriptions -- rather than by vLLM: one vLLM instance serves one model, and the
# main one is busy. It runs on CPU because ctranslate2's CUDA support does not cover SM121, and
# because dictation-length audio transcribes in seconds on GB10's cores anyway.
STT_CONTAINER_NAME: Final[str] = "dreamference-stt"
STT_IMAGE: Final[str] = "ghcr.io/speaches-ai/speaches:latest-cpu"
STT_HOST_PORT: Final[int] = 8100
STT_CONTAINER_URL: Final[str] = f"http://{STT_CONTAINER_NAME}:8000/v1"
STT_MODEL: Final[str] = "Systran/faster-whisper-small"
ONYX_VOICE_PROVIDER_NAME: Final[str] = "dreamference-whisper"

# Image search, per specs/DREAMFERENCE_IMAGE_SEARCH.md: a sidecar that searches SearXNG's image
# category, filters and ranks the candidates (SigLIP pre-filter, pHash collapse, a vision pass
# by the served model), caches the winners locally, and returns Markdown embeds. It follows the
# Gmail sidecar's shape exactly -- a single staged stdlib file in a stock python image, a
# user-owned bind mount (a named volume would be root-owned and unwritable under --user, the
# torch.compile-cache trap again), and a shared-secret header on the tool route. The images are
# served to the browser through an nginx route injected into the deployment's own template.
IMAGE_SEARCH_CONTAINER_NAME: Final[str] = "dreamference-image-search"
IMAGE_SEARCH_CONTAINER_URL: Final[str] = f"http://{IMAGE_SEARCH_CONTAINER_NAME}:8768"
IMAGE_SEARCH_HOST_PORT: Final[int] = 8768
IMAGE_SEARCH_SERVICE_IMAGE: Final[str] = "python:3-slim"
IMAGE_SEARCH_DATA_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/image-search")
IMAGE_SEARCH_TOOL_NAME: Final[str] = "Image Search"
IMAGE_SEARCH_TOOL_DESCRIPTION: Final[str] = (
    "Search the web for images and display them inline. ALWAYS use this when the user asks "
    "for a picture, photo, or image of something; embed the returned Markdown verbatim."
)

# The SigLIP pre-filter runs in an Infinity embeddings server, CPU-only for the same reason
# Whisper does: no GPU contention with the served model, and thumbnails are small. Its first
# start downloads the model weights into a named volume (root-owned is fine here -- the
# container runs as root and nothing on the host reads the cache). One platform gap, found by
# running it: Infinity publishes amd64 images only, and GB10 is aarch64 -- there the start
# fails, the warning names it, and the funnel degrades to its first candidates by design. The
# vision re-rank (served by vLLM) still runs either way and is the stronger filter.
SIGLIP_CONTAINER_NAME: Final[str] = "dreamference-siglip"
SIGLIP_IMAGE: Final[str] = "michaelf34/infinity:latest-cpu"
SIGLIP_MODEL_ID: Final[str] = "google/siglip-base-patch16-224"
SIGLIP_PORT: Final[int] = 9100
SIGLIP_CONTAINER_URL: Final[str] = f"http://{SIGLIP_CONTAINER_NAME}:{SIGLIP_PORT}"

# The nginx route, injected into the host-side template the deployment's own entrypoint runs
# envsubst over ($mightling_img and $1 are not in its whitelist, so both survive templating). The
# deferred-resolution form is load-bearing: a literal proxy_pass hostname is resolved at config
# load, and if the sidecar is absent nginx refuses to start AT ALL -- the whole UI dies, not
# just images. With a resolver directive and a variable target, a missing sidecar is a 502 on
# /puffin-images/ and nothing else. The rewrite exists because a variable proxy_pass does not
# append the location remainder the way a literal one does.
NGINX_TEMPLATE_PATH: Final[str] = os.path.expanduser(
    "~/.config/onyx/data/nginx/app.conf.template")
NGINX_IMAGE_ROUTE_BEGIN: Final[str] = "# >>> puffin-image-search"
NGINX_IMAGE_ROUTE_END: Final[str] = "# <<< puffin-image-search"
NGINX_IMAGE_ROUTE_ANCHOR: Final[str] = "client_max_body_size"
NGINX_IMAGE_ROUTE: Final[str] = (
    f"    {NGINX_IMAGE_ROUTE_BEGIN}\n"
    "    location /puffin-images/ {\n"
    "        resolver 127.0.0.11 valid=10s;\n"
    f"        set $mightling_img {IMAGE_SEARCH_CONTAINER_URL};\n"
    "        rewrite ^/puffin-images/(.*)$ /images/$1 break;\n"
    "        proxy_pass $mightling_img;\n"
    "    }\n"
    f"    {NGINX_IMAGE_ROUTE_END}"
)

# Onyx validates a voice provider's address and hardcodes the private-network exemption to Azure
# alone -- `allow_private_network = provider_type.lower() == "azure"` -- without consulting the
# SSRF Protection setting an admin can change through the API. A local sidecar on the Docker
# network is therefore refused outright, however the settings are configured.
#
# Azure is not a way around it: that provider speaks Azure's Speech REST protocol
# (/speech/recognition/conversation/cognitiveservices/v1), not OpenAI's. So the exemption is
# widened by one word instead. The alternative is a shim presenting Azure's protocol in front of
# Whisper, which needs no patch and survives upgrades -- worth building if this becomes load
# bearing. Unlike the frontend patches this one is Python, so it needs an api_server restart.
ONYX_VOICE_SSRF_FILE: Final[str] = "/app/onyx/server/manage/voice/api.py"
ONYX_VOICE_SSRF_ANCHOR: Final[str] = 'allow_private_network = provider_type.lower() == "azure"'
ONYX_VOICE_SSRF_PATCH: Final[str] = (
    'allow_private_network = provider_type.lower() in ("azure", "openai")'
)




class OnyxRunner:
    """
    Runner class managing the lifecycle of an Onyx Lite deployment backed by local vLLM.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes OnyxRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to
                DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    @classmethod
    def resolve_container_vllm_url(cls, vllm_host: str) -> str:
        """
        Rewrites a host-side vLLM URL into one reachable from inside an Onyx container.

        This is the step that silently breaks an otherwise correct setup. Dreamference launches
        vLLM with `--network host`, so on the host it answers on localhost:8000 -- but Onyx's
        containers are on the default bridge, where `localhost` is the container itself and the
        provider merely fails to connect. The bridge gateway is the host as seen from those
        containers, so a loopback host is swapped for the gateway address. A non-loopback host is
        left alone: it is already routable, and rewriting it would be wrong.

        Args:
            vllm_host (str): The vLLM base URL as configured for host-side use.

        Returns:
            str: A base URL including the `/v1` suffix, reachable from an Onyx container.
        """
        from urllib.parse import urlparse

        parsed = urlparse(vllm_host if "//" in vllm_host else f"http://{vllm_host}")
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000

        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            host = cls.docker_bridge_gateway()
        return f"http://{host}:{port}/v1"

    @classmethod
    def docker_bridge_gateway(cls) -> str:
        """
        Reports the default bridge network's gateway address, which is the host from a container.

        Returns:
            str: The gateway IP, or Docker's conventional 172.17.0.1 if it cannot be read.
        """
        try:
            result = subprocess.run(
                [
                    "docker", "network", "inspect", "bridge",
                    "--format", "{{(index .IPAM.Config 0).Gateway}}",
                ],
                capture_output=True, text=True, timeout=15, check=False,
            )
            gateway = result.stdout.strip()
            if gateway:
                return gateway
        except (OSError, subprocess.SubprocessError):
            pass
        return "172.17.0.1"

    def _cli(self, *args: str) -> List[str]:
        """
        Builds an onyx-cli argument vector, provisioning the CLI if it is missing.

        Args:
            *args (str): Subcommand and flags to append.

        Returns:
            List[str]: The full command, or an empty list if the CLI is unavailable.
        """
        if not OnyxInstaller.is_installed():
            OnyxInstaller.install_if_missing()
        binary = OnyxInstaller.get_onyx_executable()
        if not binary:
            return []
        return [binary, *args]

    def start(self, wait: bool = True) -> int:
        """
        Deploys (or restarts) the Onyx Lite stack and reports the URL to open.

        The vLLM health check is a warning rather than a gate, unlike the terminal agents: Onyx is
        a long-lived service and is perfectly capable of coming up before the model does. Its
        provider will simply fail to answer until vLLM is serving.

        Args:
            wait (bool): Whether to block until every Onyx container reports healthy.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        command = self._cli("deploy", "install", "--lite", "--no-prompt")
        if not command:
            print("❌ Onyx CLI (`onyx-cli`) is not installed and could not be installed.")
            return 1
        if not wait:
            command.append("--no-wait")

        if not self.vllm_manager.check_health():
            print("⚠️  Local vLLM is not answering yet — Onyx will start, but its model provider")
            print("   will not respond until `ling-admin server start` has the model serving.")

        print("🚀 Deploying Onyx Lite (API server + web server + PostgreSQL)...")
        returncode = subprocess.call(command)
        if returncode != 0:
            print("❌ Onyx Lite deployment failed. See the output above, or run: ling-admin chat logs")
            return returncode

        print(f"✅ Onyx Lite is up — open {DEFAULT_ONYX_WEB_URL}")
        print("💡 The first account to sign up becomes the admin.")
        print("💡 Then run `ling-admin chat configure` to point Onyx at the local vLLM model.")
        return 0

    def stop(self) -> int:
        """
        Stops the Onyx containers, leaving the deployment and its data in place.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        command = self._cli("deploy", "stop")
        if not command:
            print("❌ Onyx CLI (`onyx-cli`) is not installed.")
            return 1
        return subprocess.call(command)

    def status(self) -> int:
        """
        Prints the deployment's version, containers and health.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        command = self._cli("deploy", "status")
        if not command:
            print("❌ Onyx CLI (`onyx-cli`) is not installed.")
            return 1
        return subprocess.call(command)

    def logs(self, follow: bool = False) -> int:
        """
        Shows the deployment's container logs.

        Args:
            follow (bool): Whether to stream new log lines as they arrive.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        command = self._cli("deploy", "logs")
        if not command:
            print("❌ Onyx CLI (`onyx-cli`) is not installed.")
            return 1
        if follow:
            command.append("--follow")
        return subprocess.call(command)

    def served_model_key(self) -> str:
        """
        Names the registry entry of the model the server is actually serving.

        Onyx is registered with the model's served id and asks for it by that id, so registering
        the *configured* model broke the web chat the first time the server ran another one:
        `server start --model` with a different model left Onyx asking for a name the server did
        not know, and every answer came back empty (2026-09-29, the SGLang switch).

        Returns:
            str: The registry key whose checkpoint the server reports in /v1/models; the
                configured model when the server does not answer or serves something unlisted.
        """
        try:
            with urllib.request.urlopen(f"{self.config.vllm_host.rstrip('/')}/v1/models", timeout=5) as response:
                served = json.load(response)["data"][0]["id"]
        except (OSError, ValueError, KeyError, IndexError):
            return self.config.model
        return model_key_for_served_id(served, preferred=self.config.model) or self.config.model

    def configure(
        self,
        email: str = DEFAULT_ONYX_EMAIL,
        password: str = DEFAULT_ONYX_PASSWORD,
        web_url: str = DEFAULT_ONYX_WEB_URL,
        enable_web: bool = True,
        brand: bool = True,
        enable_voice: bool = True,
        enable_gmail: bool = True,
        enable_image_search: bool = True,
    ) -> int:
        """
        Registers the local vLLM model with Onyx as its default LLM provider.

        Onyx has no environment variable for this -- providers live in the database and are
        normally created by clicking through the Admin panel -- so this drives the same admin API
        the panel does. It is idempotent: `PUT /admin/llm/provider` upserts by name, and the
        account is only registered when logging in first fails.

        Args:
            email (str): Admin account e-mail; registered if no account exists yet.
            password (str): Admin account password.
            web_url (str): Base URL of the Onyx deployment.
            enable_web (bool): Whether to also give the default assistant SearXNG web access.
            brand (bool): Whether to rebrand the deployment as Mightling.
            enable_voice (bool): Whether to run a local Whisper server and enable the microphone.
            enable_gmail (bool): Whether to run the Gmail service and register its search tool.
            enable_image_search (bool): Whether to run the image search sidecar and its tool.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        api = f"{web_url.rstrip('/')}/api"

        # Before anything else, because it recreates the API server: authenticating first would
        # leave the session cookie pointing at a container that is about to be replaced.
        self.disable_telemetry()
        self.bind_to_loopback()

        cookie = self._authenticate(api, email, password)
        if not cookie:
            return 1

        model_key = self.served_model_key()
        model_name = resolve_model_hf_repo(model_key)
        overrides = get_model_launch_overrides(model_key) or {}
        vision = model_supports_vision(model_key)
        api_base = self.resolve_container_vllm_url(self.config.vllm_host)

        payload = {
            "name": ONYX_PROVIDER_NAME,
            "provider": ONYX_PROVIDER_TYPE,
            "api_base": api_base,
            "api_key": ONYX_PLACEHOLDER_API_KEY,
            "is_public": True,
            "model_configurations": [
                {
                    "name": model_name,
                    "is_visible": True,
                    "max_input_tokens": overrides.get("max_model_len"),
                    # Onyx gates image uploads on this flag alone. vLLM will happily accept an
                    # image for a vision checkpoint regardless, but the UI refuses to send one
                    # with "The current model does not support image input" unless it is set.
                    "supports_image_input": vision,
                }
            ],
        }

        # PUT /provider is not an upsert despite the name: `is_creation` decides which of the two
        # it performs, and it rejects the mismatching case in both directions -- creating over an
        # existing provider is a duplicate error, updating a missing one is a 404. Looking the
        # provider up first is what makes re-running this command safe.
        existing = self._find_provider(api, cookie, ONYX_PROVIDER_NAME)
        if existing is not None:
            payload["id"] = existing

        print(f"🔗 Registering {model_name} with Onyx at {api_base} ...")
        url = f"{api}/admin/llm/provider?is_creation={'false' if existing is not None else 'true'}"

        # A model *change* needs a three-step dance, found the hard way on the first real one:
        # Onyx refuses an update that removes the model currently stored as the provider's
        # default ("Cannot remove the default model ... change the default model before
        # removing"), and the default can only move to a model the provider already lists. So:
        # union first (old default kept alongside the new model), move the default, then trim
        # to the new model alone. A same-model re-run skips straight to the plain update.
        if existing is not None:
            stored_default = self._provider_default_model(api, cookie, existing)
            if stored_default and stored_default != model_name:
                union = dict(payload)
                # Visible, not hidden: hiding the still-default model trips the same
                # validation as removing it. It only exists for the one request anyway.
                union["model_configurations"] = payload["model_configurations"] + [
                    {"name": stored_default, "is_visible": True,
                     "max_input_tokens": None, "supports_image_input": False}
                ]
                _, error = self._request(url, union, cookie, method="PUT")
                if error:
                    print(f"❌ Could not stage the model change: {error}")
                    return 1
                _, error = self._request(
                    f"{api}/admin/llm/default",
                    {"provider_id": existing, "model_name": model_name},
                    cookie,
                )
                if error:
                    print(f"❌ Could not move the default model: {error}")
                    return 1

        provider, error = self._request(url, payload, cookie, method="PUT")
        if error:
            print(f"❌ Could not create the LLM provider: {error}")
            return 1

        provider_id = (provider or {}).get("id")
        if provider_id is not None:
            _, error = self._request(
                f"{api}/admin/llm/default",
                {"provider_id": provider_id, "model_name": model_name},
                cookie,
            )
            if error:
                print(f"⚠️  Provider created, but it could not be made the default: {error}")
            # A separate setting from the default chat model: Onyx routes image-bearing turns to
            # whatever is registered here, and leaving it unset means uploads stay refused even
            # though the provider advertises the capability.
            if vision:
                _, error = self._request(
                    f"{api}/admin/llm/default-vision",
                    {"provider_id": provider_id, "model_name": model_name},
                    cookie,
                )
                if error:
                    print(f"⚠️  Could not set the default vision model: {error}")
                else:
                    print("🖼️  Image input enabled — the served checkpoint is vision-capable.")

        if enable_web:
            self.enable_web_search(api, cookie)

        # Registered here rather than only from `ling-admin chat gmail`, because the Connect button that
        # obtains the Google credentials lives in the UI this tool belongs to. Waiting for consent
        # would mean a fresh install has no Gmail tool until someone had already finished a flow
        # they can only start from a page the tool is listed on.
        if enable_gmail:
            self.enable_gmail_search(api, cookie)

        if enable_image_search:
            self.enable_image_search(api, cookie)

        if brand:
            self.apply_branding(api, cookie)

        if enable_voice:
            # After the branding: this one may restart the API server, and a restart mid-way
            # would strand the other steps.
            self.enable_voice(api, cookie)

        print(f"✅ Onyx is pointed at the local model — open {web_url}")
        print(f"💡 Sign in as {email} / {password}")
        return 0

    def enable_voice(self, api: str, cookie: str) -> bool:
        """
        Gives Onyx a microphone by running a local Whisper server and registering it for STT.

        Three steps: start the transcription sidecar and join it to Onyx's network, widen Onyx's
        private-address exemption so it will accept a local endpoint, and register the provider.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            bool: True if speech-to-text is enabled once this returns.
        """
        if not self._start_stt_server():
            print("⚠️  Could not start the local speech-to-text server — skipping microphone.")
            return False

        if not self._allow_local_voice_endpoint():
            print("⚠️  Onyx will not accept a local voice endpoint — skipping microphone.")
            return False

        payload = {
            "name": ONYX_VOICE_PROVIDER_NAME,
            "provider_type": "openai",
            "api_base": STT_CONTAINER_URL,
            "api_key": ONYX_PLACEHOLDER_API_KEY,
            "api_key_changed": True,
            "stt_model": STT_MODEL,
            "activate_stt": True,
        }
        for provider in self._get_json(f"{api}/admin/voice/providers", cookie) or []:
            if provider.get("name") == ONYX_VOICE_PROVIDER_NAME:
                payload["id"] = provider["id"]
                break

        _, error = self._request(f"{api}/admin/voice/providers", payload, cookie)
        if error:
            print(f"⚠️  Could not register the speech-to-text provider: {error}")
            return False
        print("🎤 Microphone enabled — speech is transcribed locally by Whisper.")
        return True

    def _start_stt_server(self) -> bool:
        """
        Starts the Whisper sidecar if it is not already running, on Onyx's network.

        The container restarts with the host and keeps its model in a named volume, so the
        ~500 MB download happens once rather than on every boot. It is *created* on Onyx's
        network, as the Gmail sidecar is, not joined to it afterwards: a container created on
        Docker's default bridge keeps a copy of the host's DNS servers taken at start, and after
        the reboot of 2026-10-01 that copy was empty (`sidecar_network.py`). One found on the
        default bridge is replaced; the named volume keeps its model.

        Returns:
            bool: True if the server is running and reachable from Onyx.
        """
        network = self._onyx_network()
        if network and SidecarNetwork.created_on_default_bridge(STT_CONTAINER_NAME):
            print("🔁 Recreating the speech-to-text server off Docker's default bridge...")
            subprocess.run(["docker", "rm", "-f", STT_CONTAINER_NAME],
                           capture_output=True, timeout=60, check=False)

        running = subprocess.run(
            ["docker", "ps", "--filter", f"name={STT_CONTAINER_NAME}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()

        if STT_CONTAINER_NAME not in running:
            print("🎙️  Starting the local speech-to-text server...")
            result = subprocess.run(
                ["docker", "run", "-d", "--name", STT_CONTAINER_NAME,
                 "--restart", "unless-stopped",
                 *(["--network", network] if network else []),
                 "-p", f"127.0.0.1:{STT_HOST_PORT}:8000",
                 "-v", f"{STT_CONTAINER_NAME}-cache:/home/ubuntu/.cache/huggingface",
                 STT_IMAGE],
                capture_output=True, text=True, timeout=600, check=False,
            )
            if result.returncode != 0 and "already in use" not in result.stderr:
                print(f"⚠️  {result.stderr.strip()[:200]}")
                return False
            subprocess.run(["docker", "start", STT_CONTAINER_NAME],
                           capture_output=True, timeout=60, check=False)

        if network:
            # A no-op for a container created there; it covers one made before Onyx was running.
            subprocess.run(["docker", "network", "connect", network, STT_CONTAINER_NAME],
                           capture_output=True, timeout=30, check=False)

        # The model downloads on demand; pulling it here keeps the first dictation from timing out.
        subprocess.run(
            ["docker", "exec", STT_CONTAINER_NAME, "curl", "-s", "-X", "POST",
             f"http://127.0.0.1:8000/v1/models/{STT_MODEL}"],
            capture_output=True, timeout=900, check=False,
        )
        return True

    def _allow_local_voice_endpoint(self) -> bool:
        """
        Widens Onyx's voice-endpoint address check to accept a local OpenAI-compatible server.

        Restarts the API server, because unlike the frontend bundles this is Python that was
        already imported.

        Returns:
            bool: True if the exemption is in place.
        """
        containers = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.service=api_server",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        target = containers[0] if containers else None
        if not target:
            return False

        script = (
            f"p={ONYX_VOICE_SSRF_FILE!r};s=open(p).read();"
            f"new={ONYX_VOICE_SSRF_PATCH!r};old={ONYX_VOICE_SSRF_ANCHOR!r};"
            "print('done' if new in s else ('patched' if old in s else 'missing'));"
            "open(p,'w').write(s.replace(old,new,1)) if (old in s and new not in s) else None"
        )
        result = subprocess.run(
            ["docker", "exec", target, "python3", "-c", script],
            capture_output=True, text=True, timeout=60, check=False,
        )
        state = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "missing"
        if state == "done":
            return True
        if state != "patched":
            return False

        print("🔄 Restarting Onyx's API server to pick up the voice endpoint change...")
        subprocess.run(["docker", "restart", target], capture_output=True, timeout=180, check=False)
        for _ in range(40):
            health = subprocess.run(
                ["docker", "inspect", target, "--format", "{{.State.Health.Status}}"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            if health == "healthy":
                return True
            time.sleep(5)
        return False

    def apply_branding(self, api: str, cookie: str) -> bool:
        """
        Rebrands the Onyx deployment as Mightling as far as the community edition permits.

        Three changes, all of them free-tier: the company name, a `Mightling` assistant carrying the
        tools this deployment actually has, and retiring Onyx's stock assistant so the Mightling one
        is what a user lands on.

        What is deliberately *not* done: Onyx's whitelabelling -- the application name in the
        window title, the logo, `hide_onyx_branding` -- is an Enterprise feature gated behind
        ENABLE_PAID_ENTERPRISE_EDITION_FEATURES. Setting that flag without a licence would be
        helping oneself to a paid feature, so the browser tab keeps saying Onyx.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            bool: True if the branding was applied.
        """
        persona_id = self._upsert_mightling_assistant(api, cookie)

        settings = self._get_json(f"{api}/settings", cookie)
        if settings is None:
            print("⚠️  Could not read Onyx's settings — skipping rebrand.")
            return False

        payload = dict(settings)
        payload["company_name"] = MIGHTLING_COMPANY_NAME
        payload["company_description"] = MIGHTLING_COMPANY_DESCRIPTION
        # Only retire the stock assistant once there is a Mightling one to land on instead.
        payload["disable_default_assistant"] = persona_id is not None
        _, error = self._request(f"{api}/admin/settings", payload, cookie, method="PUT")
        if error:
            print(f"⚠️  Could not apply Mightling branding: {error}")
            return False

        # The logos are static files the web server hands out, not an Enterprise setting, so they
        # can be replaced without a licence -- but they live inside the container, so an upgrade
        # or `deploy install --force` restores Onyx's originals until this runs again.
        logos = OnyxBrandAssets.install()

        # Typography is the other half of the look, and travels the same way: a rewrite of the
        # compiled stylesheets plus font files served out of Onyx's own public directory.
        fonts = OnyxUIFonts.install()

        # Dreamference's own CSS, appended rather than substituted, and the sidebar label
        # rewrites -- which no stylesheet can reach, being text in a JSX call.
        OnyxUIOverrides.install()
        OnyxUILabels.install()
        OnyxUIScripts.install()

        print(f"✨ Rebranded as {MIGHTLING_COMPANY_NAME}"
              + (" with a Mightling assistant" if persona_id is not None else "")
              + (" and Mightling logos." if logos else ".")
              + (" Telegram typography applied." if fonts else ""))
        if not logos:
            print("💡 Logos unchanged — Onyx's own are still in place.")
        if not fonts:
            print("💡 Fonts unchanged — Onyx's own typefaces are still in place.")
        return True

    def _upsert_mightling_assistant(self, api: str, cookie: str) -> Optional[int]:
        """
        Creates or updates the Mightling assistant and puts it in front of the user.

        Onyx's own assistant cannot be renamed -- it is a builtin persona and the API refuses to
        modify one -- so branding it means creating a second, non-builtin assistant and retiring
        the stock one. Tools come from `GET /tool` rather than a fixed list, because it reports
        what this deployment can actually serve; Lite has no vector database, and asking for a
        tool that needs one is rejected outright.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            Optional[int]: The assistant's persona id, or None if it could not be created.
        """
        tools = self._get_json(f"{api}/tool", cookie) or []
        tool_ids = [t["id"] for t in tools if t.get("name") not in MIGHTLING_EXCLUDED_TOOLS]

        payload = {
            "name": MIGHTLING_ASSISTANT_NAME,
            "description": MIGHTLING_ASSISTANT_DESCRIPTION,
            "document_set_ids": [],
            "tool_ids": tool_ids,
            "system_prompt": MIGHTLING_ASSISTANT_INSTRUCTIONS,
            "task_prompt": "",
            "datetime_aware": True,
            "is_public": True,
            "replace_base_system_prompt": False,
        }

        existing = None
        personas = [p for p in self._get_json(f"{api}/persona", cookie) or [] if not p.get("builtin_persona")]
        for name in (MIGHTLING_ASSISTANT_NAME, LEGACY_ASSISTANT_NAME):
            existing = next((p["id"] for p in personas if p.get("name") == name), None)
            if existing is not None:
                break

        if existing is None:
            created, error = self._request(f"{api}/persona", payload, cookie)
            if error:
                print(f"⚠️  Could not create the Mightling assistant: {error}")
                return None
            persona_id = (created or {}).get("id")
        else:
            persona_id = existing
            _, error = self._request(f"{api}/persona/{persona_id}", payload, cookie, method="PATCH")
            if error:
                print(f"⚠️  Could not update the Mightling assistant: {error}")
                return None

        if persona_id is not None:
            # Best-effort placement; neither is worth failing the rebrand over.
            self._request(f"{api}/admin/persona/{persona_id}/featured",
                          {"is_featured": True}, cookie, method="PATCH")
            self._request(f"{api}/admin/persona/{persona_id}/listed",
                          {"is_listed": True}, cookie, method="PATCH")
        return persona_id

    def enable_google_login(self, client_id: str, client_secret: str) -> bool:
        """
        Adds Google sign-in to the login page, keeping the password form.

        Writes the OAuth client credentials into the deployment's `.env` and recreates the API
        server so it reads them. A restart is deliberately not enough: environment is fixed when a
        container is created, so `docker restart` would bring the old values straight back.

        Recreating that container also reverts `_allow_local_voice_endpoint()`, which patches a file
        inside it, so the patch is re-applied afterwards -- it is idempotent and only restarts the
        container when it actually changed something.

        Args:
            client_id (str): Google OAuth client ID.
            client_secret (str): Google OAuth client secret.

        Returns:
            bool: True if Onyx reports Google sign-in as enabled afterwards.
        """
        if not client_id or not client_secret:
            print("❌ Both a client ID and a client secret are required.")
            return False

        if not self._write_env_values({
            ONYX_OAUTH_ID_KEY: client_id,
            ONYX_OAUTH_SECRET_KEY: client_secret,
        }):
            return False

        print("🔄 Recreating Onyx's API server so it picks up the credentials...")
        if not self._recreate_api_server():
            return False

        # The voice exemption lives in a file inside that container, so it went with it.
        self._allow_local_voice_endpoint()

        state = self._get_json(f"{DEFAULT_ONYX_WEB_URL.rstrip('/')}/api/auth/type", cookie=None) or {}
        if not state.get("oauth_enabled"):
            print("⚠️  Onyx still reports Google sign-in as disabled — check the credentials.")
            return False

        print("✅ Google sign-in is enabled"
              + (" alongside the password form." if state.get("password_auth_enabled")
                 else ", and the password form is off."))
        print(f"💡 The Google client must list this redirect URI exactly: {GOOGLE_REDIRECT_URI}")
        return True

    def disable_telemetry(self) -> bool:
        """
        Stops Onyx's backend from posting usage records to its own servers.

        Does nothing when the setting is already in place: applying it means recreating the API
        server, and `configure()` calls this on every run.

        Returns:
            bool: True if telemetry is off afterwards.
        """
        if not self._env_already_set(ONYX_PRIVACY_ENV):
            if not self._write_env_values(ONYX_PRIVACY_ENV):
                return False
            print("🔕 Disabling Onyx's outbound telemetry...")
            if not self._recreate_api_server():
                return False
            self._allow_local_voice_endpoint()
        return True

    def bind_to_loopback(self) -> bool:
        """
        Publishes the web UI on 127.0.0.1 only, instead of on every network interface, unless
        this node is advertised and shares it (`ling-admin node enable`), in which case port
        3000 is published to the local network and port 80 stays on loopback.

        Does nothing when already in place, since applying it recreates the nginx container and
        `configure()` calls this on every run.

        Returns:
            bool: True if the web UI is bound as configured afterwards.
        """
        values = self.web_bind_env()
        # The advert says whether the web UI is offered; it follows the bind (a no-op on a node
        # that is not advertised, and when it already says so).
        from dreamference.node.node_advertiser import NodeAdvertiser
        NodeAdvertiser.on_web_ui_bound()
        if self._env_already_set(values):
            return True
        if not self._write_env_values(values):
            return False
        if values == ONYX_LOOPBACK_ENV:
            print("🔒 Restricting the web UI to this machine (127.0.0.1)...")
        else:
            print("📡 Publishing the web UI to the local network (port 3000): this node is advertised "
                  "(`ling-admin node enable`).")
        return self._recreate_service("nginx", wait_healthy=False)

    @classmethod
    def web_bind_env(cls) -> dict:
        """
        The web UI's two published ports, as Onyx's `.env` takes them.

        Port 3000 is published on every interface only on a node that is advertised and shares
        its web UI (specs/DREAMFERENCE_MIGHTLING_NODE.md §4); port 80 never leaves loopback.

        Returns:
            dict: `HOST_PORT_80` and `HOST_PORT`.
        """
        from dreamference.node.node_settings import NodeSettings
        return {"HOST_PORT_80": ONYX_LOOPBACK_ENV["HOST_PORT_80"],
                "HOST_PORT": f"{NodeSettings.web_bind_address()}:3000"}

    @classmethod
    def _env_already_set(cls, values: dict) -> bool:
        """
        Reports whether Onyx's `.env` already sets every given key to the given value.

        Args:
            values (dict): Mapping of environment key to expected value.

        Returns:
            bool: True if each key is present, uncommented, and already set as asked.
        """
        try:
            with open(ONYX_ENV_FILE) as handle:
                lines = handle.read().splitlines()
        except OSError:
            return False

        for key, value in values.items():
            wanted = {f"{key}={value}", f'{key}="{value}"'}
            if not any(line.strip() in wanted for line in lines):
                return False
        return True

    @classmethod
    def _write_env_values(cls, values: dict) -> bool:
        """
        Sets keys in Onyx's `.env`, rewriting each in place and leaving every other line alone.

        `onyx-cli` ships the file with the keys present but commented out, so a commented form is
        treated as the line to replace rather than something to leave and duplicate below.

        Args:
            values (dict): Mapping of environment key to value.

        Returns:
            bool: True if the file was written.
        """
        try:
            with open(ONYX_ENV_FILE) as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            print(f"❌ Could not read Onyx's environment file at {ONYX_ENV_FILE}: {exc}")
            return False

        for key, value in values.items():
            entry = f'{key}="{value}"'
            for index, line in enumerate(lines):
                stripped = line.lstrip("# ").strip()
                if stripped.startswith(f"{key}="):
                    lines[index] = entry
                    break
            else:
                lines.append(entry)

        try:
            with open(ONYX_ENV_FILE, "w") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            print(f"❌ Could not write {ONYX_ENV_FILE}: {exc}")
            return False
        return True

    @classmethod
    def _recreate_api_server(cls) -> bool:
        """
        Recreates the API server container from the deployment's compose files.

        Returns:
            bool: True if the container came back healthy.
        """
        return cls._recreate_service("api_server", wait_healthy=True)

    @classmethod
    def _recreate_service(cls, service: str, wait_healthy: bool) -> bool:
        """
        Recreates one Onyx container from the deployment's compose files, leaving the rest running.

        Args:
            service (str): The compose service name, e.g. `api_server` or `nginx`.
            wait_healthy (bool): Wait for the container's healthcheck; otherwise only for it to run.

        Returns:
            bool: True if the container came back (healthy, when asked to wait for that).
        """
        directory = os.path.dirname(ONYX_ENV_FILE)
        files: List[str] = []
        for name in ("docker-compose.yml", "docker-compose.onyx-lite.yml"):
            path = os.path.join(directory, name)
            if os.path.exists(path):
                files += ["-f", path]
        if not files:
            print(f"❌ No Onyx compose files found in {directory}.")
            return False

        result = subprocess.run(
            ["docker", "compose", *files, "-p", "onyx", "--project-directory", directory,
             "up", "-d", "--force-recreate", "--no-deps", service],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            print(f"❌ Could not recreate {service}: {result.stderr.strip()[:200]}")
            return False

        containers = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.service={service}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        target = containers[0] if containers else None
        if not target:
            return False
        if not wait_healthy:
            return True
        for _ in range(40):
            health = subprocess.run(
                ["docker", "inspect", target, "--format", "{{.State.Health.Status}}"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            if health == "healthy":
                return True
            time.sleep(5)
        print(f"⚠️  {service} did not report healthy in time.")
        return False

    def connect_gmail(
        self,
        web_url: str = DEFAULT_ONYX_WEB_URL,
        email: str = DEFAULT_ONYX_EMAIL,
        password: str = DEFAULT_ONYX_PASSWORD,
    ) -> bool:
        """Registers the Gmail search tool in Onyx."""
        api = f"{web_url.rstrip('/')}/api"
        cookie = self._authenticate(api, email, password)
        if not cookie:
            return False
        if not self.enable_gmail_search(api, cookie):
            return False
        self._upsert_mightling_assistant(api, cookie)
        print("✅ Gmail search is available to the assistant.")
        return True


    def enable_gmail_search(self, api: str, cookie: str) -> bool:
        """
        Gives the assistant Gmail search, as a custom tool pointing at the local Gmail service.

        The tool is registered rather than described in a prompt, which is the same choice web
        search makes: Onyx's own base prompt already knows how to use a registered tool, and a
        prompt-level instruction would be capped at the 500 characters `user_preferences` allows.

        Two operations are registered from one OpenAPI document -- searching, and reading one
        message -- mirroring `web_search` and `open_url`. A search that returned whole bodies would
        spend the context window on threads the question was not about.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            bool: True if the tool is registered.
        """
        from dreamference.chat.gmail_search_service import openapi_definition

        # Registration deliberately does **not** wait for consent. The tool is a service URL and a
        # shared-secret header; the Google refresh token is read per request, not at registration.
        # Requiring a stored token here enforced an ordering that is not real, and it was the
        # reason the tool could not exist before someone had already finished the OAuth flow --
        # which is backwards, because the Connect button that starts that flow is reached from the
        # UI this tool lives in. An unconnected search answers with the way to connect.
        # Note what GNOME is holding while we are here, so the setup form can offer that route to
        # someone who has never run a Gmail command at all. Cheap, and the alternative is a page
        # that recommends the hardest of the three paths on a desktop where the easiest is ready.

        secret = self._gmail_secret()
        if not secret or not self._start_gmail_service(secret):
            return False

        payload = {
            "name": GMAIL_TOOL_NAME,
            "description": GMAIL_TOOL_DESCRIPTION,
            "definition": openapi_definition(GMAIL_CONTAINER_URL),
            "custom_headers": [{"key": GMAIL_AUTH_HEADER, "value": secret}],
            "passthrough_auth": False,
        }

        existing = next(
            (t for t in (self._get_json(f"{api}/tool", cookie) or [])
             if t.get("display_name") == GMAIL_TOOL_NAME or t.get("name") == GMAIL_TOOL_NAME),
            None,
        )
        if existing:
            _, error = self._request(
                f"{api}/admin/tool/custom/{existing['id']}", payload, cookie, method="PUT"
            )
        else:
            _, error = self._request(f"{api}/admin/tool/custom", payload, cookie)
        if error:
            print(f"⚠️  Could not register the Gmail tool: {error}")
            return False

        print("📬 Gmail search registered.")
        return True

    @classmethod
    def _gmail_secret(cls) -> Optional[str]:
        """
        Returns the shared secret the service and Onyx authenticate with, creating it once.

        Args:
            None.

        Returns:
            Optional[str]: The secret, or None if it could not be stored.
        """
        import secrets as secrets_module

        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR

        path = os.path.join(CREDENTIALS_DIR, "service-secret")
        try:
            if os.path.exists(path):
                with open(path) as handle:
                    return handle.read().strip() or None
            os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
            secret = secrets_module.token_urlsafe(32)
            with open(path, "w") as handle:
                handle.write(secret)
            os.chmod(path, 0o600)
            return secret
        except OSError as exc:
            print(f"⚠️  Could not store the Gmail service secret: {exc}")
            return None

    def _start_gmail_service(self, secret: str) -> bool:
        """
        Runs the Gmail search service as a container on Onyx's network.

        The service module is copied next to the credentials and the directory mounted as
        `/config`, rather than mounting the source tree: the container then has no dependency on
        where Dreamference is checked out, and the image stays a stock `python:3-slim` with nothing
        installed into it -- which is only possible because the service uses no third-party
        libraries.

        Args:
            secret (str): Shared secret the service will require in its auth header.

        Returns:
            bool: True if the container is running.
        """
        import shutil

        from dreamference.chat import gmail_search_service, google_workspace_reader
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR

        network = self._onyx_network()
        if not network:
            print("⚠️  Onyx's Docker network could not be found.")
            return False

        try:
            shutil.copyfile(
                gmail_search_service.__file__, os.path.join(CREDENTIALS_DIR, "service.py")
            )
            # Drive and Calendar for Mightling's apps; the service imports it from beside itself.
            shutil.copyfile(
                google_workspace_reader.__file__,
                os.path.join(CREDENTIALS_DIR, "google_workspace_reader.py"),
            )
        except OSError as exc:
            print(f"⚠️  Could not stage the Gmail service: {exc}")
            return False

        subprocess.run(["docker", "rm", "-f", GMAIL_CONTAINER_NAME],
                       capture_output=True, timeout=60, check=False)
        result = subprocess.run(
            ["docker", "run", "-d", "--name", GMAIL_CONTAINER_NAME,
             "--restart", "unless-stopped", "--network", network,
             # As the invoking user, not root. The service *writes* to the mounted directory now --
             # a connection made from the UI stores the mailbox credentials from inside the
             # container -- and a root container writing into a user-owned directory leaves files
             # their owner cannot read or replace. The same trap the torch.compile cache hit, and
             # the same fix. Nothing in here needs root: the port is above 1024 and the only path
             # written is `/config`.
             "--user", f"{os.getuid()}:{os.getgid()}",
             # Read-write: the service writes the refresh token itself when the user completes
             # consent through the button in the UI.
             "-v", f"{CREDENTIALS_DIR}:/config",
             # Also published on loopback, because two callers reach it from outside the Docker
             # network -- the browser asking whether Gmail is connected, and Google redirecting
             # back after consent.
             "-p", f"127.0.0.1:{GMAIL_HOST_PORT}:8000",
             "-e", f"MIGHTLING_GMAIL_SECRET={secret}",
             GMAIL_SERVICE_IMAGE, "python3", "/config/service.py"],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            print(f"⚠️  Could not start the Gmail service: {result.stderr.strip()[:200]}")
            return False
        return True

    def enable_image_search(self, api: str, cookie: str) -> bool:
        """
        Gives the assistant web image search, as a custom tool pointing at the image sidecar.

        Mirrors the Gmail registration: an OpenAPI document (Onyx's custom-tool API consumes a
        document, not a bare URL -- the spec's registration sketch predates that discovery), a
        shared-secret header, and lookup-then-update so a re-run refreshes rather than
        duplicates. SigLIP is started best-effort: the funnel degrades to its first candidates
        without it, and blocking image search on a model download would invert the priorities.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            bool: True if the tool is registered.
        """
        from dreamference.chat.image_search_service import AUTH_HEADER, openapi_definition

        secret = self._image_search_secret()
        if not secret:
            return False
        self._start_siglip()
        if not self._start_image_search_service(secret):
            return False
        if not self._inject_image_route():
            print("⚠️  Nginx route not injected — images will not render in the browser.")

        payload = {
            "name": IMAGE_SEARCH_TOOL_NAME,
            "description": IMAGE_SEARCH_TOOL_DESCRIPTION,
            "definition": openapi_definition(IMAGE_SEARCH_CONTAINER_URL),
            "custom_headers": [{"key": AUTH_HEADER, "value": secret}],
            "passthrough_auth": False,
        }
        existing = next(
            (t for t in (self._get_json(f"{api}/tool", cookie) or [])
             if t.get("display_name") == IMAGE_SEARCH_TOOL_NAME
             or t.get("name") == IMAGE_SEARCH_TOOL_NAME),
            None,
        )
        if existing:
            _, error = self._request(
                f"{api}/admin/tool/custom/{existing['id']}", payload, cookie, method="PUT"
            )
        else:
            _, error = self._request(f"{api}/admin/tool/custom", payload, cookie)
        if error:
            print(f"⚠️  Could not register the Image Search tool: {error}")
            return False

        print("🖼️  Image search registered — results cache locally under /puffin-images/.")
        return True

    @classmethod
    def _image_search_secret(cls) -> Optional[str]:
        """
        Reads (creating on first use) the shared secret for the image sidecar's tool route.

        Returns:
            Optional[str]: The secret, or None if the data directory is unusable.
        """
        import secrets as _secrets

        path = os.path.join(IMAGE_SEARCH_DATA_DIR, "secret")
        try:
            os.makedirs(IMAGE_SEARCH_DATA_DIR, exist_ok=True)
            if os.path.exists(path):
                with open(path) as fh:
                    value = fh.read().strip()
                if value:
                    return value
            value = _secrets.token_urlsafe(32)
            with open(path, "w") as fh:
                fh.write(value)
            os.chmod(path, 0o600)
            return value
        except OSError as exc:
            print(f"⚠️  Could not prepare the image search secret: {exc}")
            return None

    def _start_siglip(self) -> bool:
        """
        Runs the SigLIP embeddings sidecar, best-effort.

        A stopped container is started rather than recreated (its weights volume makes the
        second start fast), and failure is only a warning: the funnel runs without the
        pre-filter, just less selectively.

        Returns:
            bool: True if the container is running or was started.
        """
        network = self._onyx_network()
        if not network:
            return False
        running = subprocess.run(
            ["docker", "ps", "--filter", f"name={SIGLIP_CONTAINER_NAME}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        if SIGLIP_CONTAINER_NAME in running:
            return True
        started = subprocess.run(
            ["docker", "start", SIGLIP_CONTAINER_NAME],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if started.returncode == 0:
            return True
        result = subprocess.run(
            ["docker", "run", "-d", "--name", SIGLIP_CONTAINER_NAME,
             "--restart", "unless-stopped", "--network", network,
             "-v", f"{SIGLIP_CONTAINER_NAME}-cache:/app/.cache",
             "-p", f"127.0.0.1:{SIGLIP_PORT}:{SIGLIP_PORT}",
             SIGLIP_IMAGE,
             "v2", "--model-id", SIGLIP_MODEL_ID, "--port", str(SIGLIP_PORT)],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            reason = " ".join(result.stderr.split())[:120]
            print(f"⚠️  SigLIP sidecar not started ({reason}) — "
                  "image pre-filtering degrades gracefully.")
            return False
        return True

    def _start_image_search_service(self, secret: str) -> bool:
        """
        Runs the image search sidecar on Onyx's network.

        The service module is staged into the data directory and the directory mounted as
        `/config`, exactly as the Gmail sidecar does. The one departure: Pillow. The service
        needs it for decoding and hashing, the stock image lacks it, and the container runs
        non-root -- so it is pip-installed into /tmp at boot (skipped when already present,
        which a plain restart preserves). Offline, the install fails and the service runs
        degraded rather than not at all.

        Args:
            secret (str): Shared secret the sidecar will require on /search.

        Returns:
            bool: True if the container answers its health route.
        """
        import shutil
        import urllib.request as _request

        from dreamference.chat import image_search_service

        network = self._onyx_network()
        if not network:
            print("⚠️  Onyx's Docker network could not be found.")
            return False
        try:
            os.makedirs(os.path.join(IMAGE_SEARCH_DATA_DIR, "data"), exist_ok=True)
            shutil.copyfile(
                image_search_service.__file__,
                os.path.join(IMAGE_SEARCH_DATA_DIR, "service.py"),
            )
        except OSError as exc:
            print(f"⚠️  Could not stage the image search service: {exc}")
            return False

        subprocess.run(["docker", "rm", "-f", IMAGE_SEARCH_CONTAINER_NAME],
                       capture_output=True, timeout=60, check=False)
        boot = (
            "export PIP_TARGET=/tmp/pylib PYTHONPATH=/tmp/pylib;"
            "python3 -c 'import PIL' 2>/dev/null"
            " || pip install -q --no-cache-dir pillow || true;"
            "python3 /config/service.py"
        )
        result = subprocess.run(
            ["docker", "run", "-d", "--name", IMAGE_SEARCH_CONTAINER_NAME,
             "--restart", "unless-stopped", "--network", network,
             "--user", f"{os.getuid()}:{os.getgid()}",
             "-v", f"{IMAGE_SEARCH_DATA_DIR}:/config",
             "-p", f"127.0.0.1:{IMAGE_SEARCH_HOST_PORT}:8768",
             "-e", f"MIGHTLING_IMAGE_SECRET={secret}",
             "-e", f"MIGHTLING_SEARXNG_URL={SEARXNG_CONTAINER_URL}",
             "-e", f"MIGHTLING_SIGLIP_URL={SIGLIP_CONTAINER_URL}",
             "-e", "MIGHTLING_VISION_URL="
                   f"{self.resolve_container_vllm_url(self.config.vllm_host)}",
             "-e", f"MIGHTLING_VISION_MODEL={resolve_model_hf_repo(self.served_model_key())}",
             "-e", "MIGHTLING_DATA_DIR=/config/data",
             IMAGE_SEARCH_SERVICE_IMAGE, "sh", "-c", boot],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            print(f"⚠️  Could not start the image search service: "
                  f"{result.stderr.strip()[:200]}")
            return False
        for _ in range(30):
            try:
                with _request.urlopen(
                        f"http://127.0.0.1:{IMAGE_SEARCH_HOST_PORT}/health", timeout=2):
                    return True
            except OSError:
                time.sleep(1)
        print("⚠️  The image search service did not become healthy.")
        return False

    @classmethod
    def apply_image_route(cls, template_text: str) -> str:
        """
        Returns the nginx template with the /puffin-images/ route present exactly once.

        Marker-based, like the stylesheet overrides: any existing block between the markers is
        cut first, so a re-run rewrites rather than accumulates, and edits to the route are
        appliable. The block lands directly after the server block's `client_max_body_size`
        line -- a directive the template has carried across Onyx versions.

        Args:
            template_text (str): The current template.

        Returns:
            str: The template with the route installed. Unchanged (and unrouted) if the anchor
                line is missing, which a caller reports rather than guessing at nginx syntax.
        """
        lines = template_text.splitlines()
        kept, skipping = [], False
        for line in lines:
            if NGINX_IMAGE_ROUTE_BEGIN in line:
                skipping = True
                continue
            if NGINX_IMAGE_ROUTE_END in line:
                skipping = False
                continue
            if not skipping:
                kept.append(line)
        out = []
        inserted = False
        for line in kept:
            out.append(line)
            if not inserted and NGINX_IMAGE_ROUTE_ANCHOR in line:
                out.append(NGINX_IMAGE_ROUTE)
                inserted = True
        return "\n".join(out) + ("\n" if template_text.endswith("\n") else "")

    def _inject_image_route(self) -> bool:
        """
        Installs the /puffin-images/ route into the deployment's nginx template and restarts
        nginx to re-run its templating.

        The template is a *host-side* file the nginx entrypoint processes at boot, so the edit
        survives container recreates -- unlike every patch that writes into a container
        filesystem. The nginx container is found by its compose service label, never by name.

        Returns:
            bool: True if the template carries the route and nginx restarted.
        """
        try:
            with open(NGINX_TEMPLATE_PATH) as fh:
                template = fh.read()
        except OSError as exc:
            print(f"⚠️  Could not read the nginx template: {exc}")
            return False
        updated = self.apply_image_route(template)
        if NGINX_IMAGE_ROUTE_BEGIN not in updated:
            print("⚠️  The nginx template has no anchor for the image route.")
            return False
        if updated == template:
            # Already installed: no write, and critically no restart -- nginx carries the very
            # session this configure run is talking through, and restarting it mid-run resets
            # every later step's connection. Seen live: the tool registration and the whole
            # branding pass died with ECONNRESET before this guard existed.
            return True
        try:
            with open(NGINX_TEMPLATE_PATH, "w") as fh:
                fh.write(updated)
        except OSError as exc:
            print(f"⚠️  Could not write the nginx template: {exc}")
            return False
        containers = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.service=nginx",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        if not containers:
            return False
        restart = subprocess.run(
            ["docker", "restart", containers[0]],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if restart.returncode != 0:
            return False
        # And wait for it to answer again before returning, for the same reason: the steps
        # after this one speak to Onyx through this proxy.
        import urllib.request as _request

        for _ in range(30):
            try:
                with _request.urlopen("http://localhost:3000/api/health", timeout=2):
                    return True
            except OSError:
                time.sleep(1)
        return False

    def enable_web_search(self, api: str, cookie: str) -> bool:
        """
        Gives Onyx's default assistant web access through SearXNG.

        Two steps, both needed: SearXNG's container joins Onyx's network so `open_url` can resolve
        it, and the assistant's system prompt gains the instructions describing how to use it.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.

        Returns:
            bool: True if the assistant now carries the web instructions.
        """
        if not self._attach_searxng():
            print("⚠️  SearXNG is not running or could not join Onyx's network — skipping web setup.")
            print("💡 Start it, then re-run: ling-admin chat configure")
            return False

        payload = {
            "name": ONYX_SEARCH_PROVIDER_NAME,
            "provider_type": "searxng",
            "config": {"searxng_base_url": SEARXNG_CONTAINER_URL},
            "activate": True,
        }

        existing = self._get_json(f"{api}/admin/web-search/search-providers", cookie) or []
        for provider in existing:
            if provider.get("name") == ONYX_SEARCH_PROVIDER_NAME:
                payload["id"] = provider["id"]
                break

        _, error = self._request(
            f"{api}/admin/web-search/search-providers", payload, cookie
        )
        if error:
            print(f"⚠️  Could not register SearXNG as Onyx's search provider: {error}")
            return False
        print("🌐 Onyx web search enabled via SearXNG.")
        return True

    def _attach_searxng(self) -> bool:
        """
        Connects the SearXNG container to Onyx's Docker network.

        SearXNG publishes on 127.0.0.1 only, so unlike vLLM it is not reachable over the bridge
        gateway; joining Onyx's network makes it addressable by container name without opening
        any new port on the host.

        A container still on Docker's default bridge (started by hand from the old `docker run`
        hint) is first recreated on the sidecar network: joining Onyx's network does not change
        how a container resolves names, and on the default bridge that is a copy of the host's
        DNS servers which was empty after the reboot of 2026-10-01 (`sidecar_network.py`).

        Returns:
            bool: True if SearXNG is on Onyx's network once this returns.
        """
        network = self._onyx_network()
        if not network:
            return False
        if SidecarNetwork.created_on_default_bridge(SEARXNG_CONTAINER_NAME) and not SearxngSidecar.start():
            return False
        result = subprocess.run(
            ["docker", "network", "connect", network, SEARXNG_CONTAINER_NAME],
            capture_output=True, text=True, timeout=30, check=False,
        )
        # Already-connected is success, not failure -- this command is expected to re-run.
        return result.returncode == 0 or "already exists" in result.stderr

    def _onyx_network(self) -> Optional[str]:
        """
        Reports the Docker network Onyx's API server is attached to.

        Read from the running container rather than assumed to be `onyx_default`, because the
        compose project name is configurable and the network name follows it.

        Returns:
            Optional[str]: The network name, or None if the API server is not running.
        """
        try:
            containers = subprocess.run(
                ["docker", "ps", "--filter", "name=api_server", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout.split()
            for name in containers:
                networks = subprocess.run(
                    ["docker", "inspect", name, "--format",
                     "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
                    capture_output=True, text=True, timeout=15, check=False,
                ).stdout.split()
                if networks:
                    return networks[0]
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _get_json(self, url: str, cookie: str) -> Optional[dict]:
        """
        Fetches and decodes a JSON document from the Onyx API.

        Args:
            url (str): Absolute endpoint URL.
            cookie (str): Session cookie header value.

        Returns:
            Optional[dict]: The decoded body, or None if it could not be read.
        """
        request = urllib.request.Request(url, headers={"Cookie": cookie}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _provider_default_model(self, api: str, cookie: str,
                                provider_id: int) -> Optional[str]:
        """
        Returns the default model name stored on an existing provider.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.
            provider_id (int): The provider to inspect.

        Returns:
            Optional[str]: The stored default model name, or None if unreadable.
        """
        request = urllib.request.Request(
            f"{api}/admin/llm/provider", headers={"Cookie": cookie}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                listing = json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, ValueError):
            return None
        # The listing is not a bare provider array: the defaults ride alongside it, as
        # {providers: [...], default_text: {provider_id, model_name}, default_vision: {...}}.
        # The stored default this helper exists to find is `default_text` -- the same record
        # `fetch_default_llm_model` reads in the removal validation.
        default = (listing or {}).get("default_text") if isinstance(listing, dict) else None
        if default and default.get("provider_id") == provider_id:
            return default.get("model_name")
        return None

    def _find_provider(self, api: str, cookie: str, name: str) -> Optional[int]:
        """
        Returns the id of an existing Onyx LLM provider with the given name.

        Args:
            api (str): Onyx API base URL.
            cookie (str): Session cookie header value.
            name (str): Provider name to look for.

        Returns:
            Optional[int]: The provider's id, or None if no provider by that name exists.
        """
        request = urllib.request.Request(
            f"{api}/admin/llm/provider", headers={"Cookie": cookie}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                listing = json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, ValueError):
            return None
        # The endpoint answers with {"providers": [...], "default_text": ..., "default_vision": ...}
        # rather than a bare list, so iterating the response directly walks the dict's keys.
        providers = listing.get("providers") if isinstance(listing, dict) else listing
        for provider in providers or []:
            if provider.get("name") == name:
                return provider.get("id")
        return None

    def _authenticate(self, api: str, email: str, password: str) -> Optional[str]:
        """
        Logs in to Onyx, registering the account first if it does not exist yet.

        Args:
            api (str): Onyx API base URL.
            email (str): Account e-mail.
            password (str): Account password.

        Returns:
            Optional[str]: The session cookie header value, or None if authentication failed.
        """
        cookie = self._login(api, email, password)
        if cookie:
            return cookie

        print(f"👤 Registering {email} as the Onyx admin account...")
        _, error = self._request(
            f"{api}/auth/register", {"email": email, "password": password}, cookie=None
        )
        if error:
            print(f"❌ Could not register an Onyx account: {error}")
            print("💡 If an account already exists, pass its credentials:")
            print("   ling-admin chat configure --email you@example.com --password ...")
            return None

        cookie = self._login(api, email, password)
        if not cookie:
            print("❌ Registered the account but could not log in with it.")
        return cookie

    def _login(self, api: str, email: str, password: str) -> Optional[str]:
        """
        Performs a form-encoded login and returns the resulting session cookie.

        fastapi-users expects `username`/`password` as form fields rather than JSON, and hands
        back the session as a Set-Cookie header rather than a token in the body.

        Args:
            api (str): Onyx API base URL.
            email (str): Account e-mail, sent as the `username` field.
            password (str): Account password.

        Returns:
            Optional[str]: The cookie header value, or None if the credentials were rejected.
        """
        from urllib.parse import urlencode

        body = urlencode({"username": email, "password": password}).encode()
        request = urllib.request.Request(
            f"{api}/auth/login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                cookies = response.headers.get_all("Set-Cookie") or []
        except (urllib.error.URLError, OSError):
            return None
        for value in cookies:
            if value.startswith("fastapiusersauth"):
                return value.split(";", 1)[0]
        return None

    def _request(
        self,
        url: str,
        payload: dict,
        cookie: Optional[str],
        method: str = "POST",
    ) -> Tuple[Optional[dict], Optional[str]]:
        """
        Sends a JSON request to the Onyx API and decodes the response.

        Args:
            url (str): Absolute endpoint URL.
            payload (dict): JSON body to send.
            cookie (Optional[str]): Session cookie header value, if authenticated.
            method (str): HTTP method to use.

        Returns:
            Tuple[Optional[dict], Optional[str]]: The decoded body (None if empty) and an error
                string (None on success).
        """
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            return None, f"HTTP {exc.code}: {exc.read().decode()[:300]}"
        except (urllib.error.URLError, OSError) as exc:
            return None, str(exc)
        try:
            return json.loads(raw), None
        except ValueError:
            return None, None

    def uninstall(self) -> int:
        """
        Permanently deletes the Onyx deployment and all of its data.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        command = self._cli("deploy", "uninstall")
        if not command:
            print("❌ Onyx CLI (`onyx-cli`) is not installed.")
            return 1
        return subprocess.call(command)
