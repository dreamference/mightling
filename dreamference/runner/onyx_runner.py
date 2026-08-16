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
from dreamference.runner.onyx_brand_assets import OnyxBrandAssets
from dreamference.runner.onyx_installer import OnyxInstaller
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
DREAM_COMPANY_NAME: Final[str] = "Dream"
DREAM_COMPANY_DESCRIPTION: Final[str] = (
    "Local, air-gapped pair programming on NVIDIA GB10."
)
DREAM_ASSISTANT_NAME: Final[str] = "Dream"
DREAM_ASSISTANT_DESCRIPTION: Final[str] = (
    "Local pair programmer on GB10 — web search, Python, and file reading, "
    "served entirely from this machine."
)

# Onyx's agentic coding tool, left off the Dream assistant deliberately: Dreamference's own
# terminal agents cover that ground with the same model, and enabling both invites the two to
# edit the same tree from different directions.
DREAM_EXCLUDED_TOOLS: Final[frozenset] = frozenset({"coding_agent"})

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
            brand (bool): Whether to rebrand the deployment as Dream.
            enable_voice (bool): Whether to run a local Whisper server and enable the microphone.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        api = f"{web_url.rstrip('/')}/api"
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
        Rebrands the Onyx deployment as Dream as far as the community edition permits.

        Three changes, all of them free-tier: the company name, a `Dream` assistant carrying the
        tools this deployment actually has, and retiring Onyx's stock assistant so the Dream one
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
        persona_id = self._upsert_dream_assistant(api, cookie)

        settings = self._get_json(f"{api}/settings", cookie)
        if settings is None:
            print("⚠️  Could not read Onyx's settings — skipping rebrand.")
            return False

        payload = dict(settings)
        payload["company_name"] = DREAM_COMPANY_NAME
        payload["company_description"] = DREAM_COMPANY_DESCRIPTION
        # Only retire the stock assistant once there is a Dream one to land on instead.
        payload["disable_default_assistant"] = persona_id is not None
        _, error = self._request(f"{api}/admin/settings", payload, cookie, method="PUT")
        if error:
            print(f"⚠️  Could not apply Dream branding: {error}")
            return False

        # The logos are static files the web server hands out, not an Enterprise setting, so they
        # can be replaced without a licence -- but they live inside the container, so an upgrade
        # or `deploy install --force` restores Onyx's originals until this runs again.
        logos = OnyxBrandAssets.install()

        print(f"✨ Rebranded as {DREAM_COMPANY_NAME}"
              + (" with a Dream assistant" if persona_id is not None else "")
              + (" and Dream logos." if logos else "."))
        if not logos:
            print("💡 Logos unchanged — Onyx's own are still in place.")
        return True

    def _upsert_dream_assistant(self, api: str, cookie: str) -> Optional[int]:
        """
        Creates or updates the Dream assistant and puts it in front of the user.

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
        tool_ids = [t["id"] for t in tools if t.get("name") not in DREAM_EXCLUDED_TOOLS]

        payload = {
            "name": DREAM_ASSISTANT_NAME,
            "description": DREAM_ASSISTANT_DESCRIPTION,
            "document_set_ids": [],
            "tool_ids": tool_ids,
            "system_prompt": "",
            "task_prompt": "",
            "datetime_aware": True,
            "is_public": True,
            "replace_base_system_prompt": False,
        }

        existing = None
        for persona in self._get_json(f"{api}/persona", cookie) or []:
            if persona.get("name") == DREAM_ASSISTANT_NAME and not persona.get("builtin_persona"):
                existing = persona["id"]
                break

        if existing is None:
            created, error = self._request(f"{api}/persona", payload, cookie)
            if error:
                print(f"⚠️  Could not create the Dream assistant: {error}")
                return None
            persona_id = (created or {}).get("id")
        else:
            persona_id = existing
            _, error = self._request(f"{api}/persona/{persona_id}", payload, cookie, method="PATCH")
            if error:
                print(f"⚠️  Could not update the Dream assistant: {error}")
                return None

        if persona_id is not None:
            # Best-effort placement; neither is worth failing the rebrand over.
            self._request(f"{api}/admin/persona/{persona_id}/featured",
                          {"is_featured": True}, cookie, method="PATCH")
            self._request(f"{api}/admin/persona/{persona_id}/listed",
                          {"is_listed": True}, cookie, method="PATCH")
        return persona_id

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
