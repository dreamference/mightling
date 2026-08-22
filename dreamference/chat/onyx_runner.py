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

# The name Onyx's containers resolve SearXNG by once it joins their network. SearXNG publishes
# only on 127.0.0.1, so the bridge gateway that reaches vLLM does not reach it -- attaching the
# container to Onyx's network is what makes it addressable, and exposes no new host port.
SEARXNG_CONTAINER_NAME: Final[str] = "searxng"

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

# Gmail search, registered the same way web search is: a capability Onyx already knows how to call,
# rather than instructions bolted onto a prompt.
#
# It runs as a container on Onyx's network for the same reason SearXNG does -- addressable by name,
# no new port on the host. That it is on a *private* network is not a security boundary here:
# Onyx's custom-tool client calls whatever URL the tool names and performs no SSRF validation, so
# anything else on that network could reach a service holding a live mailbox credential. The shared
# secret header is what actually protects it.
GNOME_TOKEN_UNIT: Final[str] = "dreamference-goa"
GMAIL_CONTAINER_NAME: Final[str] = "dream-gmail"
GMAIL_CONTAINER_URL: Final[str] = f"http://{GMAIL_CONTAINER_NAME}:8000"
GMAIL_HOST_PORT: Final[int] = 8767
GMAIL_SERVICE_IMAGE: Final[str] = "python:3-slim"
GMAIL_TOOL_NAME: Final[str] = "Gmail"
GMAIL_TOOL_DESCRIPTION: Final[str] = "Search and read the user's Gmail mailbox."

# Kept in step with the service module, which enforces it.
GMAIL_AUTH_HEADER: Final[str] = "X-Puffin-Gmail-Token"
SEARXNG_CONTAINER_URL: Final[str] = "http://searxng:8080"

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
PUFFIN_COMPANY_NAME: Final[str] = "Puffin"
PUFFIN_COMPANY_DESCRIPTION: Final[str] = (
    "Local, air-gapped pair programming on NVIDIA GB10."
)
PUFFIN_ASSISTANT_NAME: Final[str] = "Puffin"
# Empty on purpose. Onyx prints the assistant's description under the composer on the new-chat
# screen, where a sentence of deployment trivia is noise rather than orientation -- the greeting
# above it already says what this is.
PUFFIN_ASSISTANT_DESCRIPTION: Final[str] = ""

# What the assistant is told about itself.
#
# Without this the model answers "what is your name?" from its own pretraining -- "I'm an AI
# assistant" -- because nothing in the request mentions Puffin. Onyx's persona name is a label in
# the UI; it is not sent to the model. This is, and it goes on the Puffin persona rather than
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
PUFFIN_ASSISTANT_INSTRUCTIONS: Final[str] = (
    "You are Puffin, an AI assistant served by a model running on this machine's own NVIDIA GB10 "
    "hardware. When you are asked your name, who you are, or what you are, say that you are "
    "Puffin. Do not describe yourself as a generic assistant and do not answer with the name of "
    "the model you are served from. You can reach the live web through your search tool: use it "
    "for anything current, and never tell the user you have no internet access."
)

# Onyx's agentic coding tool, left off the Puffin assistant deliberately: Dreamference's own
# terminal agents cover that ground with the same model, and enabling both invites the two to
# edit the same tree from different directions.
PUFFIN_EXCLUDED_TOOLS: Final[frozenset] = frozenset({"coding_agent"})

# Speech-to-text. Onyx has a complete voice subsystem and shows no microphone button until an STT
# provider is registered, so the button is a configuration question, not a missing feature.
#
# The model is transcribed locally by a small Whisper server -- `speaches`, which speaks OpenAI's
# /v1/audio/transcriptions -- rather than by vLLM: one vLLM instance serves one model, and the
# main one is busy. It runs on CPU because ctranslate2's CUDA support does not cover SM121, and
# because dictation-length audio transcribes in seconds on GB10's cores anyway.
STT_CONTAINER_NAME: Final[str] = "dream-stt"
STT_IMAGE: Final[str] = "ghcr.io/speaches-ai/speaches:latest-cpu"
STT_HOST_PORT: Final[int] = 8100
STT_CONTAINER_URL: Final[str] = "http://dream-stt:8000/v1"
STT_MODEL: Final[str] = "Systran/faster-whisper-small"
ONYX_VOICE_PROVIDER_NAME: Final[str] = "dreamference-whisper"

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
            print("   will not respond until `dream server start` has the model serving.")

        print("🚀 Deploying Onyx Lite (API server + web server + PostgreSQL)...")
        returncode = subprocess.call(command)
        if returncode != 0:
            print("❌ Onyx Lite deployment failed. See the output above, or run: dream onyx logs")
            return returncode

        print(f"✅ Onyx Lite is up — open {DEFAULT_ONYX_WEB_URL}")
        print("💡 The first account to sign up becomes the admin.")
        print("💡 Then run `dream onyx configure` to point Onyx at the local vLLM model.")
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

    def configure(
        self,
        email: str = DEFAULT_ONYX_EMAIL,
        password: str = DEFAULT_ONYX_PASSWORD,
        web_url: str = DEFAULT_ONYX_WEB_URL,
        enable_web: bool = True,
        brand: bool = True,
        enable_voice: bool = True,
        enable_gmail: bool = True,
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
            brand (bool): Whether to rebrand the deployment as Puffin.
            enable_voice (bool): Whether to run a local Whisper server and enable the microphone.
            enable_gmail (bool): Whether to run the Gmail service and register its search tool.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        api = f"{web_url.rstrip('/')}/api"

        # Before anything else, because it recreates the API server: authenticating first would
        # leave the session cookie pointing at a container that is about to be replaced.
        self.disable_telemetry()

        cookie = self._authenticate(api, email, password)
        if not cookie:
            return 1

        model_name = resolve_model_hf_repo(self.config.model)
        overrides = get_model_launch_overrides(self.config.model) or {}
        vision = model_supports_vision(self.config.model)
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

        # Registered here rather than only from `dream onyx gmail`, because the Connect button that
        # obtains the Google credentials lives in the UI this tool belongs to. Waiting for consent
        # would mean a fresh install has no Gmail tool until someone had already finished a flow
        # they can only start from a page the tool is listed on.
        if enable_gmail:
            self.enable_gmail_search(api, cookie)

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
        Starts the Whisper sidecar if it is not already running and joins Onyx's network.

        The container restarts with the host and keeps its model in a named volume, so the
        ~500 MB download happens once rather than on every boot.

        Returns:
            bool: True if the server is running and reachable from Onyx.
        """
        running = subprocess.run(
            ["docker", "ps", "--filter", f"name={STT_CONTAINER_NAME}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()

        if STT_CONTAINER_NAME not in running:
            print("🎙️  Starting the local speech-to-text server...")
            result = subprocess.run(
                ["docker", "run", "-d", "--name", STT_CONTAINER_NAME,
                 "--restart", "unless-stopped",
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

        network = self._onyx_network()
        if network:
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
            ["docker", "ps", "--filter", "name=api_server", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        target = next((c for c in containers if "onyx" in c), None)
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
        Rebrands the Onyx deployment as Puffin as far as the community edition permits.

        Three changes, all of them free-tier: the company name, a `Puffin` assistant carrying the
        tools this deployment actually has, and retiring Onyx's stock assistant so the Puffin one
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
        persona_id = self._upsert_puffin_assistant(api, cookie)

        settings = self._get_json(f"{api}/settings", cookie)
        if settings is None:
            print("⚠️  Could not read Onyx's settings — skipping rebrand.")
            return False

        payload = dict(settings)
        payload["company_name"] = PUFFIN_COMPANY_NAME
        payload["company_description"] = PUFFIN_COMPANY_DESCRIPTION
        # Only retire the stock assistant once there is a Puffin one to land on instead.
        payload["disable_default_assistant"] = persona_id is not None
        _, error = self._request(f"{api}/admin/settings", payload, cookie, method="PUT")
        if error:
            print(f"⚠️  Could not apply Puffin branding: {error}")
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

        print(f"✨ Rebranded as {PUFFIN_COMPANY_NAME}"
              + (" with a Puffin assistant" if persona_id is not None else "")
              + (" and Puffin logos." if logos else ".")
              + (" Telegram typography applied." if fonts else ""))
        if not logos:
            print("💡 Logos unchanged — Onyx's own are still in place.")
        if not fonts:
            print("💡 Fonts unchanged — Onyx's own typefaces are still in place.")
        return True

    def _upsert_puffin_assistant(self, api: str, cookie: str) -> Optional[int]:
        """
        Creates or updates the Puffin assistant and puts it in front of the user.

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
        tool_ids = [t["id"] for t in tools if t.get("name") not in PUFFIN_EXCLUDED_TOOLS]

        payload = {
            "name": PUFFIN_ASSISTANT_NAME,
            "description": PUFFIN_ASSISTANT_DESCRIPTION,
            "document_set_ids": [],
            "tool_ids": tool_ids,
            "system_prompt": PUFFIN_ASSISTANT_INSTRUCTIONS,
            "task_prompt": "",
            "datetime_aware": True,
            "is_public": True,
            "replace_base_system_prompt": False,
        }

        existing = None
        for persona in self._get_json(f"{api}/persona", cookie) or []:
            if persona.get("name") == PUFFIN_ASSISTANT_NAME and not persona.get("builtin_persona"):
                existing = persona["id"]
                break

        if existing is None:
            created, error = self._request(f"{api}/persona", payload, cookie)
            if error:
                print(f"⚠️  Could not create the Puffin assistant: {error}")
                return None
            persona_id = (created or {}).get("id")
        else:
            persona_id = existing
            _, error = self._request(f"{api}/persona/{persona_id}", payload, cookie, method="PATCH")
            if error:
                print(f"⚠️  Could not update the Puffin assistant: {error}")
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
             "up", "-d", "--force-recreate", "--no-deps", "api_server"],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            print(f"❌ Could not recreate the API server: {result.stderr.strip()[:200]}")
            return False

        containers = subprocess.run(
            ["docker", "ps", "--filter", "name=api_server", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.split()
        target = next((c for c in containers if "onyx" in c), None)
        if not target:
            return False
        for _ in range(40):
            health = subprocess.run(
                ["docker", "inspect", target, "--format", "{{.State.Health.Status}}"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            if health == "healthy":
                return True
            time.sleep(5)
        print("⚠️  The API server did not report healthy in time.")
        return False

    def connect_gmail(
        self,
        web_url: str = DEFAULT_ONYX_WEB_URL,
        email: str = DEFAULT_ONYX_EMAIL,
        password: str = DEFAULT_ONYX_PASSWORD,
    ) -> bool:
        """
        Connects the mailbox through GNOME Online Accounts and registers the search tool.

        There is nothing for the user to create. Two other routes were built and removed -- a
        Google app password, and a Google client of the user's own -- because each put setup work
        on someone who has already signed into Google on their desktop. This reads the token GNOME
        is holding, hands it to the service and installs the timer that keeps doing so.

        Args:
            web_url (str): Base URL of the Onyx web UI.
            email (str): Onyx admin email.
            password (str): Onyx admin password.

        Returns:
            bool: True if the assistant can search Gmail afterwards.
        """
        from dreamference.chat.goa_accounts import GoaAccounts

        if not GoaAccounts.available():
            print("❌ GNOME Online Accounts is not answering on this session.")
            print("💡 It needs a GNOME desktop session; Gmail search is unavailable on a headless "
                  "host.")
            return False

        accounts = GoaAccounts.google_accounts()
        if not accounts:
            print("❌ No Google account has been added to GNOME.")
            print("💡 Open Settings → Online Accounts → Google and sign in, then run this again.")
            return False
        if len(accounts) > 1:
            print(f"ℹ️  {len(accounts)} Google accounts in GNOME; using the first, "
                  f"{accounts[0]['email']}.")

        if not self.refresh_gnome_token(announce=True):
            return False
        if not self.install_gnome_token_timer():
            print("⚠️  The token was stored but the refresh timer could not be installed.")
            print("💡 Gmail will stop answering in about an hour; re-run this command to renew.")

        api = f"{web_url.rstrip('/')}/api"
        cookie = self._authenticate(api, email, password)
        if not cookie:
            return False
        if not self.enable_gmail_search(api, cookie):
            return False
        self._upsert_puffin_assistant(api, cookie)
        print("✅ Gmail search is available to the assistant.")
        return True

    @classmethod
    def _record_gnome_accounts(cls) -> None:
        """
        Writes what GNOME Online Accounts is holding into the shared credentials directory.

        The container cannot look for itself -- GOA is on the session bus, and a stock
        `python:3-slim` has neither a bus nor `gdbus` -- so the host leaves it a note.
        """
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR
        from dreamference.chat.gmail_search_service import GmailSearchService
        from dreamference.chat.goa_accounts import GoaAccounts

        if not GoaAccounts.available():
            return
        try:
            os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
        except OSError:
            return
        GmailSearchService.save_gnome_accounts(
            [a["email"] for a in GoaAccounts.google_accounts() if a["email"]], CREDENTIALS_DIR
        )

    @classmethod
    def refresh_gnome_token(cls, announce: bool = False) -> bool:
        """
        Writes GOA's current access token where the Gmail service can read it.

        Run both by `dream onyx gmail --gnome` and, every half hour, by the systemd user timer that
        command installs. GOA refreshes the token itself when the one it holds has expired, so this
        never touches a refresh token and never stores one.

        Args:
            announce (bool): Whether to print what happened; the timer runs quietly.

        Returns:
            bool: True if a token was stored.
        """
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR
        from dreamference.chat.gmail_search_service import GmailSearchService
        from dreamference.chat.goa_accounts import GoaAccounts

        accounts = GoaAccounts.google_accounts()
        # Recorded whatever the answer, so the setup form learns when an account appears and stops
        # offering the route when one is removed.
        os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
        GmailSearchService.save_gnome_accounts(
            [a["email"] for a in accounts if a["email"]], CREDENTIALS_DIR
        )
        if not accounts:
            if announce:
                print("❌ No Google account has been added to GNOME.")
            return False

        account = accounts[0]
        issued = GoaAccounts.access_token(account["path"])
        if not issued:
            if announce:
                print("❌ GNOME would not issue an access token for that account.")
                print("💡 Open Settings → Online Accounts and check the account is not showing "
                      "an error; signing in again repairs a revoked grant.")
            return False

        token, lifetime = issued
        os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
        if not GmailSearchService.save_token(
            account["email"], token, lifetime, CREDENTIALS_DIR
        ):
            if announce:
                print("❌ Could not store the token.")
            return False
        if announce:
            print(f"🔐 {account['email']} connected through GNOME Online Accounts.")
        return True

    @classmethod
    def install_gnome_token_timer(cls) -> bool:
        """
        Installs the systemd user timer that keeps the stored token current.

        A **user** timer, not a system one: GOA lives on the session bus and only the session owner
        can ask it anything, so this belongs to the user and starts with their session. A timer
        rather than a daemon, because the work is one D-Bus call every half hour and a long-lived
        process would be one more thing to supervise.

        Returns:
            bool: True if the timer is installed and running.
        """
        import shutil

        from dreamference.chat.goa_accounts import REFRESH_INTERVAL_SECONDS, GoaAccounts

        if not shutil.which("systemctl"):
            return False
        executable = shutil.which("dream") or ""
        if not executable:
            return False

        directory = GoaAccounts.systemd_unit_directory()
        service = (
            "[Unit]\n"
            "Description=Refresh the Google access token Puffin reads Gmail with\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={executable} onyx gmail --refresh\n"
        )
        timer = (
            "[Unit]\n"
            "Description=Keep Puffin's Google access token current\n\n"
            "[Timer]\n"
            # `Persistent` so a machine that was asleep refreshes on waking rather than waiting out
            # the rest of the interval with a token that expired hours ago.
            f"OnBootSec={REFRESH_INTERVAL_SECONDS}\n"
            f"OnUnitActiveSec={REFRESH_INTERVAL_SECONDS}\n"
            "Persistent=true\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )
        try:
            with open(os.path.join(directory, GNOME_TOKEN_UNIT + ".service"), "w") as handle:
                handle.write(service)
            with open(os.path.join(directory, GNOME_TOKEN_UNIT + ".timer"), "w") as handle:
                handle.write(timer)
        except OSError as exc:
            print(f"⚠️  Could not write the systemd units: {exc}")
            return False

        for command in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", GNOME_TOKEN_UNIT + ".timer"],
        ):
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=60, check=False)
            if result.returncode != 0:
                print(f"⚠️  {' '.join(command)} failed: {result.stderr.strip()[:160]}")
                return False
        print(f"⏱️  Token refresh scheduled every {REFRESH_INTERVAL_SECONDS // 60} minutes.")
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
        self._record_gnome_accounts()

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

        from dreamference.chat import gmail_search_service
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR

        network = self._onyx_network()
        if not network:
            print("⚠️  Onyx's Docker network could not be found.")
            return False

        try:
            shutil.copyfile(
                gmail_search_service.__file__, os.path.join(CREDENTIALS_DIR, "service.py")
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
             "-e", f"PUFFIN_GMAIL_SECRET={secret}",
             GMAIL_SERVICE_IMAGE, "python3", "/config/service.py"],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            print(f"⚠️  Could not start the Gmail service: {result.stderr.strip()[:200]}")
            return False
        return True

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
            print("💡 Start it, then re-run: dream onyx configure")
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

        Returns:
            bool: True if SearXNG is on Onyx's network once this returns.
        """
        network = self._onyx_network()
        if not network:
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
            print("   dream onyx configure --email you@example.com --password ...")
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
