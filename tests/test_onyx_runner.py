import contextlib
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import pytest

from dreamference.chat import OnyxInstaller, OnyxRunner
from dreamference.chat.onyx_runner import (
    ONYX_PROVIDER_NAME,
    ONYX_PROVIDER_TYPE,
    ONYX_SEARCH_PROVIDER_NAME,
    SEARXNG_CONTAINER_URL,
    PUFFIN_ASSISTANT_NAME,
    PUFFIN_COMPANY_NAME,
    PUFFIN_EXCLUDED_TOOLS,
    DEFAULT_ONYX_EMAIL,
)


def test_loopback_vllm_host_is_rewritten_to_the_docker_gateway():
    # Onyx runs on the default bridge, where localhost is the container itself. Without this
    # rewrite the provider is created successfully and then simply never connects.
    with patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"):
        assert OnyxRunner.resolve_container_vllm_url("http://localhost:8000") == (
            "http://172.17.0.1:8000/v1"
        )
        assert OnyxRunner.resolve_container_vllm_url("http://127.0.0.1:9001") == (
            "http://172.17.0.1:9001/v1"
        )


def test_routable_vllm_host_is_left_alone():
    with patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"):
        assert OnyxRunner.resolve_container_vllm_url("http://10.0.0.5:8000") == (
            "http://10.0.0.5:8000/v1"
        )


def test_gateway_falls_back_when_docker_cannot_be_queried():
    with patch("subprocess.run", side_effect=OSError("no docker")):
        assert OnyxRunner.docker_bridge_gateway() == "172.17.0.1"


def test_provider_lookup_reads_the_providers_key():
    # The endpoint answers with a dict, not a bare list; iterating it directly walks the keys.
    listing = json.dumps(
        {"providers": [{"id": 7, "name": ONYX_PROVIDER_NAME}], "default_text": None}
    ).encode()

    class _Response:
        def read(self):
            return listing

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    runner = OnyxRunner()
    with patch("urllib.request.urlopen", return_value=_Response()):
        assert runner._find_provider("http://x/api", "cookie", ONYX_PROVIDER_NAME) == 7
        assert runner._find_provider("http://x/api", "cookie", "absent") is None


@pytest.fixture(autouse=True)
def _image_search_without_side_effects(monkeypatch):
    # configure() enables image search by default, and the real step starts containers and
    # rewrites the deployment's nginx template -- none of which belongs in a test run. The
    # *internals* are stubbed rather than the method, so configure tests still exercise the
    # registration flow against the patched _request, and the dedicated image search tests
    # override these with their own patches.
    monkeypatch.setattr(OnyxRunner, "_image_search_secret", classmethod(lambda cls: "test-secret"))
    monkeypatch.setattr(OnyxRunner, "_start_siglip", lambda self: True)
    monkeypatch.setattr(OnyxRunner, "_start_image_search_service", lambda self, secret: True)
    monkeypatch.setattr(OnyxRunner, "_inject_image_route", lambda self: True)


def test_configure_creates_then_updates_the_same_provider():
    # PUT /provider is not an upsert: is_creation must match reality in both directions, so a
    # second run has to update by id rather than create a duplicate.
    runner = OnyxRunner()
    calls = []

    def fake_request(url, payload, cookie, method="POST"):
        calls.append((url, payload, method))
        return {"id": 1}, None

    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None):
        assert runner.configure() == 0

    provider_call = calls[0]
    assert "is_creation=true" in provider_call[0]
    assert provider_call[2] == "PUT"
    assert provider_call[1]["provider"] == ONYX_PROVIDER_TYPE
    assert provider_call[1]["api_base"].startswith("http://172.17.0.1:")
    assert "id" not in provider_call[1]

    calls.clear()
    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=42):
        assert runner.configure() == 0

    assert "is_creation=false" in calls[0][0]
    assert calls[0][1]["id"] == 42


def test_configure_names_the_served_model_and_its_context_length():
    # Both generic provider types ship an empty known-model list, so the model has to be named
    # explicitly or Onyx offers nothing to chat with.
    runner = OnyxRunner()
    captured = {}

    def fake_request(url, payload, cookie, method="POST"):
        captured.setdefault("first", payload)
        return {"id": 1}, None

    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None):
        runner.configure()

    models = captured["first"]["model_configurations"]
    assert len(models) == 1
    assert models[0]["is_visible"] is True
    assert "/" in models[0]["name"], "should be the HF repo id vLLM serves, not the alias"
    # Whatever the *configured* recipe declares -- hardcoding a number here made the test
    # fail the first time the main model changed to an entry with a different context cap.
    from dreamference.config import DreamferenceConfig
    from dreamference.hardware import get_model_launch_overrides

    expected = get_model_launch_overrides(DreamferenceConfig().model)["max_model_len"]
    assert models[0]["max_input_tokens"] == expected


def test_default_admin_email_avoids_reserved_domains():
    # email-validator rejects .local, .localhost, .test and .invalid outright, so a default in
    # one of those makes the very first `puffin-admin puffin configure` fail with a 422.
    assert not DEFAULT_ONYX_EMAIL.endswith((".local", ".localhost", ".test", ".invalid"))


def test_lifecycle_commands_are_onyx_cli_passthroughs():
    runner = OnyxRunner()
    with patch.object(OnyxInstaller, "is_installed", return_value=True), \
         patch.object(OnyxInstaller, "get_onyx_executable", return_value="/bin/onyx-cli"), \
         patch("subprocess.call", return_value=0) as call:
        runner.stop()
        assert call.call_args[0][0] == ["/bin/onyx-cli", "deploy", "stop"]
        runner.status()
        assert call.call_args[0][0] == ["/bin/onyx-cli", "deploy", "status"]
        runner.logs(follow=True)
        assert call.call_args[0][0] == ["/bin/onyx-cli", "deploy", "logs", "--follow"]
        runner.uninstall()
        assert call.call_args[0][0] == ["/bin/onyx-cli", "deploy", "uninstall"]


def test_start_requests_lite_mode_non_interactively():
    # --lite is what selects the reduced stack, and --no-prompt is what keeps the guided
    # installer from blocking forever on a question nobody is there to answer.
    runner = OnyxRunner()
    with patch.object(OnyxInstaller, "is_installed", return_value=True), \
         patch.object(OnyxInstaller, "get_onyx_executable", return_value="/bin/onyx-cli"), \
         patch.object(runner.vllm_manager, "check_health", return_value=True), \
         patch("subprocess.call", return_value=0) as call:
        assert runner.start() == 0

    command = call.call_args[0][0]
    assert command[:4] == ["/bin/onyx-cli", "deploy", "install"] + ["--lite"]
    assert "--no-prompt" in command
    assert "--no-wait" not in command


def test_start_survives_vllm_being_down():
    # Unlike the terminal agents, Onyx is a service and may legitimately come up before the model.
    runner = OnyxRunner()
    with patch.object(OnyxInstaller, "is_installed", return_value=True), \
         patch.object(OnyxInstaller, "get_onyx_executable", return_value="/bin/onyx-cli"), \
         patch.object(runner.vllm_manager, "check_health", return_value=False), \
         patch("subprocess.call", return_value=0):
        assert runner.start() == 0


def test_web_search_registers_searxng_as_a_native_provider():
    # Onyx supports SearXNG as a first-class search provider, so web access is a provider
    # registration rather than an instruction appended to the system prompt. The provider path is
    # admin-configured and its client does no SSRF validation, which is what lets it reach a
    # private container address with Onyx's secure default left alone.
    runner = OnyxRunner()
    calls = []

    with patch.object(OnyxRunner, "_attach_searxng", return_value=True), \
         patch.object(OnyxRunner, "_get_json", return_value=[]), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, p)), ({}, None))[1]):
        assert runner.enable_web_search("http://x/api", "cookie") is True

    url, payload = calls[0]
    assert url.endswith("/admin/web-search/search-providers")
    assert payload["provider_type"] == "searxng"
    assert payload["config"]["searxng_base_url"] == SEARXNG_CONTAINER_URL
    assert payload["activate"] is True
    assert "id" not in payload


def test_web_search_updates_an_existing_provider_in_place():
    runner = OnyxRunner()
    calls = []
    listing = [{"id": 3, "name": ONYX_SEARCH_PROVIDER_NAME}]

    with patch.object(OnyxRunner, "_attach_searxng", return_value=True), \
         patch.object(OnyxRunner, "_get_json", return_value=listing), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, p)), ({}, None))[1]):
        assert runner.enable_web_search("http://x/api", "cookie") is True

    assert calls[0][1]["id"] == 3


def test_web_search_is_skipped_when_searxng_is_absent():
    # SearXNG not running must not fail the whole configure -- the model provider is still valid.
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_attach_searxng", return_value=False), \
         patch.object(OnyxRunner, "_request") as request:
        assert runner.enable_web_search("http://x/api", "cookie") is False
        request.assert_not_called()


def test_configure_can_opt_out_of_web_search():
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", return_value=({"id": 1}, None)), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_web_search") as web:
        assert runner.configure(enable_web=False) == 0
        web.assert_not_called()


def test_configure_advertises_vision_for_a_vision_checkpoint():
    # Onyx gates image uploads on supports_image_input alone: vLLM accepts images for this
    # checkpoint either way, but without the flag the UI refuses with "The current model does not
    # support image input". The default-vision registration is a second, separate setting.
    runner = OnyxRunner()
    calls = []

    def fake_request(url, payload, cookie, method="POST"):
        calls.append((url, payload))
        return {"id": 1}, None

    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_web_search", return_value=True):
        assert runner.configure() == 0

    assert calls[0][1]["model_configurations"][0]["supports_image_input"] is True
    assert any(url.endswith("/admin/llm/default-vision") for url, _ in calls)


def test_configure_skips_vision_for_a_text_only_model():
    from dreamference.config import DreamferenceConfig

    config = DreamferenceConfig()
    config.model = "llama-3.3-70b"
    runner = OnyxRunner(config=config)
    calls = []

    def fake_request(url, payload, cookie, method="POST"):
        calls.append((url, payload))
        return {"id": 1}, None

    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_web_search", return_value=True):
        assert runner.configure() == 0

    assert calls[0][1]["model_configurations"][0]["supports_image_input"] is False
    assert not any(url.endswith("/admin/llm/default-vision") for url, _ in calls)


def test_branding_creates_a_dream_assistant_from_available_tools():
    # Tools come from GET /tool, not a fixed list: Lite has no vector database, and asking for a
    # tool it cannot serve is rejected outright.
    runner = OnyxRunner()
    calls = []
    tools = [{"id": 6, "name": "run_python"}, {"id": 11, "name": "coding_agent"},
             {"id": 7, "name": "open_url"}, {"id": 3, "name": "web_search"}]

    def fake_get(url, cookie):
        if url.endswith("/tool"):
            return tools
        if url.endswith("/persona"):
            return []
        return {"company_name": None}

    def fake_request(url, payload, cookie, method="POST"):
        calls.append((url, payload, method))
        return {"id": 5}, None

    with patch.object(OnyxRunner, "_get_json", side_effect=fake_get), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request):
        assert runner.apply_branding("http://x/api", "cookie") is True

    persona = next(p for u, p, m in calls if u.endswith("/persona"))
    assert persona["name"] == PUFFIN_ASSISTANT_NAME
    assert 11 not in persona["tool_ids"], "coding_agent is excluded on purpose"
    assert sorted(persona["tool_ids"]) == [3, 6, 7]

    settings = next(p for u, p, m in calls if u.endswith("/admin/settings"))
    assert settings["company_name"] == PUFFIN_COMPANY_NAME
    assert settings["disable_default_assistant"] is True


def test_branding_updates_the_existing_dream_assistant():
    # Re-running must not leave a second "Puffin" in the assistant list.
    runner = OnyxRunner()
    calls = []

    def fake_get(url, cookie):
        if url.endswith("/tool"):
            return [{"id": 7, "name": "open_url"}]
        if url.endswith("/persona"):
            return [{"id": 9, "name": PUFFIN_ASSISTANT_NAME, "builtin_persona": False}]
        return {}

    with patch.object(OnyxRunner, "_get_json", side_effect=fake_get), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, method)), ({"id": 9}, None))[1]):
        runner.apply_branding("http://x/api", "cookie")

    assert ("http://x/api/persona/9", "PATCH") in calls
    assert not any(u.endswith("/api/persona") and m == "POST" for u, m in calls)


def test_branding_keeps_the_stock_assistant_when_dream_could_not_be_created():
    # Retiring Onyx's assistant without a replacement would leave the user with nothing.
    runner = OnyxRunner()
    captured = {}

    def fake_request(url, payload, cookie, method="POST"):
        if url.endswith("/persona"):
            return None, "boom"
        captured[url] = payload
        return {}, None

    def fake_get(url, cookie):
        return [] if url.endswith(("/tool", "/persona")) else {}

    with patch.object(OnyxRunner, "_get_json", side_effect=fake_get), \
         patch.object(OnyxRunner, "_request", side_effect=fake_request):
        runner.apply_branding("http://x/api", "cookie")

    assert captured["http://x/api/admin/settings"]["disable_default_assistant"] is False


def test_configure_can_opt_out_of_branding():
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", return_value=({"id": 1}, None)), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_web_search", return_value=True), \
         patch.object(OnyxRunner, "apply_branding") as brand:
        assert runner.configure(brand=False) == 0
        brand.assert_not_called()


def test_brand_assets_match_the_dimensions_onyx_ships():
    # The frontend lays these out against fixed aspect ratios; a square wordmark reflows the
    # sidebar rather than merely looking different.
    import tempfile
    from PIL import Image
    from dreamference.chat import OnyxBrandAssets

    workdir = tempfile.mkdtemp()
    assets = OnyxBrandAssets.render(workdir)
    assert Image.open(assets["logo.png"]).size == (400, 400)
    assert Image.open(assets["logo-dark.png"]).size == (400, 400)
    assert Image.open(assets["logotype.png"]).size == (2640, 733)
    assert Image.open(assets["logotype-dark.png"]).size == (720, 320)
    assert set(assets) == {
        "logo.png", "logo-dark.png", "logotype.png",
        "logotype-dark.png", "logo.svg", "onyx.ico",
    }


def test_brand_asset_install_is_skipped_when_onyx_is_not_running(monkeypatch):
    from conftest import REAL_BRAND_INSTALL
    from dreamference.chat.onyx_brand_assets import OnyxBrandAssets as _Assets
    monkeypatch.setattr(_Assets, "install", REAL_BRAND_INSTALL)
    from dreamference.chat import OnyxBrandAssets

    with patch.object(OnyxBrandAssets, "find_web_container", return_value=None), \
         patch("subprocess.run") as run:
        assert OnyxBrandAssets.install() is False
        run.assert_not_called()


def test_wordmark_replacement_targets_every_onyx_letter():
    # The sidebar wordmark is four inline letter paths, not /logotype.png -- which the bundle
    # never references. Missing one letter leaves a fragment of "onyx" on screen.
    from dreamference.chat.onyx_brand_assets import (
        ONYX_WORDMARK_PREFIXES, PUFFIN_WORDMARK_PATH,
    )

    assert len(ONYX_WORDMARK_PREFIXES) == 4, "o, n, y and x each need a rule"
    assert list(ONYX_WORDMARK_PREFIXES.values()).count("PUFFIN") == 1
    assert list(ONYX_WORDMARK_PREFIXES.values()).count("HIDE") == 3
    assert PUFFIN_WORDMARK_PATH.startswith("M")
    assert '"' not in PUFFIN_WORDMARK_PATH, "must survive embedding in the patch script"


def test_logo_patch_leaves_unrelated_onyx_strings_alone():
    # "Onyx" also names the author of builtin skills and the fallback owner of shared agents.
    # Rewriting those would state something untrue rather than rebrand anything.
    from dreamference.chat.onyx_brand_assets import ONYX_APP_NAME_STRINGS

    for key in ONYX_APP_NAME_STRINGS:
        assert "application_name" in key or "return" in key


def test_voice_registers_the_local_whisper_endpoint():
    # Onyx shows no microphone until an STT provider exists, so the button is a registration.
    from dreamference.chat.onyx_runner import (
        ONYX_VOICE_PROVIDER_NAME, STT_CONTAINER_URL, STT_MODEL,
    )

    runner = OnyxRunner()
    calls = []

    with patch.object(OnyxRunner, "_start_stt_server", return_value=True), \
         patch.object(OnyxRunner, "_allow_local_voice_endpoint", return_value=True), \
         patch.object(OnyxRunner, "_get_json", return_value=[]), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, p)), ({}, None))[1]):
        assert runner.enable_voice("http://x/api", "cookie") is True

    url, payload = calls[0]
    assert url.endswith("/admin/voice/providers")
    assert payload["provider_type"] == "openai"
    assert payload["api_base"] == STT_CONTAINER_URL
    assert payload["stt_model"] == STT_MODEL
    assert payload["activate_stt"] is True
    assert payload["name"] == ONYX_VOICE_PROVIDER_NAME
    assert "id" not in payload


def test_voice_updates_an_existing_provider_rather_than_duplicating():
    from dreamference.chat.onyx_runner import ONYX_VOICE_PROVIDER_NAME

    runner = OnyxRunner()
    calls = []
    with patch.object(OnyxRunner, "_start_stt_server", return_value=True), \
         patch.object(OnyxRunner, "_allow_local_voice_endpoint", return_value=True), \
         patch.object(OnyxRunner, "_get_json",
                      return_value=[{"id": 4, "name": ONYX_VOICE_PROVIDER_NAME}]), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, p)), ({}, None))[1]):
        runner.enable_voice("http://x/api", "cookie")

    assert calls[0][1]["id"] == 4


def test_voice_is_skipped_when_the_stt_server_will_not_start():
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_start_stt_server", return_value=False), \
         patch.object(OnyxRunner, "_request") as request:
        assert runner.enable_voice("http://x/api", "cookie") is False
        request.assert_not_called()


def test_voice_is_skipped_when_onyx_will_not_accept_a_local_endpoint():
    # Registering against an endpoint Onyx rejects would leave a broken provider behind.
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_start_stt_server", return_value=True), \
         patch.object(OnyxRunner, "_allow_local_voice_endpoint", return_value=False), \
         patch.object(OnyxRunner, "_request") as request:
        assert runner.enable_voice("http://x/api", "cookie") is False
        request.assert_not_called()


def test_configure_can_opt_out_of_voice():
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", return_value=({"id": 1}, None)), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_web_search", return_value=True), \
         patch.object(OnyxRunner, "apply_branding", return_value=True), \
         patch.object(OnyxRunner, "enable_voice") as voice:
        assert runner.configure(enable_voice=False) == 0
        voice.assert_not_called()


def test_voice_patch_targets_the_azure_only_exemption():
    # The anchor is Onyx's hardcoded exemption; if a future version rewrites that line the patch
    # must fail visibly rather than silently register a provider Onyx will refuse.
    from dreamference.chat.onyx_runner import (
        ONYX_VOICE_SSRF_ANCHOR, ONYX_VOICE_SSRF_PATCH,
    )

    assert '== "azure"' in ONYX_VOICE_SSRF_ANCHOR
    assert '"azure", "openai"' in ONYX_VOICE_SSRF_PATCH


def test_every_onyx_face_is_substituted_by_a_font_the_installer_fetches():
    # A family named here but missing from FONT_SOURCES rewrites Onyx's CSS to point at a file
    # that is never copied in, which serves a 404 and falls back to the browser's default.
    from dreamference.chat.onyx_ui_fonts import FACE_SUBSTITUTIONS, FONT_SOURCES

    assert set(FACE_SUBSTITUTIONS) == {"Hanken Grotesk", "KH Teka", "DM Mono"}
    for filename, _ in FACE_SUBSTITUTIONS.values():
        assert filename in FONT_SOURCES


def test_face_rewrite_keeps_onyxs_own_family_names():
    # The whole approach rests on this: the family name stays, only the file behind it changes,
    # so the forty-odd `font-family` declarations elsewhere in the CSS need no edit at all.
    from dreamference.chat.onyx_ui_fonts import OnyxUIFonts

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "3"
        assert OnyxUIFonts.patch_css("web") == 3

    script = run.call_args[0][0][-1]
    assert "font-family:Hanken Grotesk" in script
    assert "/fonts/Roboto.woff2" in script
    assert "/fonts/RobotoMono.woff2" in script


def test_a_newly_copied_face_restarts_the_web_server(monkeypatch):
    from conftest import REAL_FONTS_INSTALL
    from dreamference.chat.onyx_ui_fonts import OnyxUIFonts as _Fonts
    monkeypatch.setattr(_Fonts, "install", REAL_FONTS_INSTALL)
    # Next.js builds its static routes from `public/` at boot, so a face copied into a running
    # container has no URL until the server starts again.
    from dreamference.chat.onyx_ui_fonts import OnyxUIFonts

    with patch.object(OnyxUIFonts, "ensure_fonts", return_value={"Roboto.woff2": "/tmp/r"}), \
         patch.object(OnyxUIFonts, "installed_faces", return_value=set()), \
         patch.object(OnyxUIFonts, "patch_css", return_value=2), \
         patch.object(OnyxUIFonts, "restart_web_server", return_value=True) as restart, \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert OnyxUIFonts.install("web") is True
        restart.assert_called_once()


def test_faces_already_in_the_container_are_not_recopied_or_restarted(monkeypatch):
    from conftest import REAL_FONTS_INSTALL
    from dreamference.chat.onyx_ui_fonts import OnyxUIFonts as _Fonts
    monkeypatch.setattr(_Fonts, "install", REAL_FONTS_INSTALL)
    from dreamference.chat.onyx_ui_fonts import OnyxUIFonts

    with patch.object(OnyxUIFonts, "ensure_fonts", return_value={"Roboto.woff2": "/tmp/r"}), \
         patch.object(OnyxUIFonts, "installed_faces", return_value={"Roboto.woff2"}), \
         patch.object(OnyxUIFonts, "patch_css", return_value=2), \
         patch.object(OnyxUIFonts, "restart_web_server") as restart, \
         patch("subprocess.run") as run:
        assert OnyxUIFonts.install("web") is True
        run.assert_not_called()
        restart.assert_not_called()


def test_hover_rule_hides_the_actions_but_not_the_toolbar_they_sit_in():
    # The toolbar also carries the citation list and the message switcher. Hiding the toolbar
    # itself would take those with it, which is content disappearing rather than chrome.
    from dreamference.chat.onyx_ui_overrides import HOVER_TOOLBAR_CSS

    assert ':not([data-testid="AgentMessage/toolbar"])' in HOVER_TOOLBAR_CSS
    assert '[data-testid="onyx-ai-message"]:hover' in HOVER_TOOLBAR_CSS
    # No hover on a keyboard, so the controls have to come back on focus too.
    assert '[data-testid="onyx-ai-message"]:focus-within' in HOVER_TOOLBAR_CSS
    # Transparent buttons still take clicks without this.
    assert "pointer-events:none" in HOVER_TOOLBAR_CSS


def test_overrides_replace_their_own_previous_block():
    # The marker opens the block and everything after it belongs to Dreamference, so a re-run cuts
    # there and rewrites. Appending unconditionally would stack copies; appending only when the
    # marker is absent would make every later edit to the CSS unappliable.
    from dreamference.chat.onyx_ui_overrides import (
        OVERRIDE_MARKER, UI_OVERRIDES, OnyxUIOverrides,
    )

    assert UI_OVERRIDES.startswith(OVERRIDE_MARKER)
    assert UI_OVERRIDES.count(OVERRIDE_MARKER) == 1, "the cut point has to be unambiguous"
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "38"
        assert OnyxUIOverrides.append_overrides("web") == 38

    script = run.call_args[0][0][-1]
    assert "indexOf(MARK)" in script
    assert "base+CSS" in script


def test_selected_sidebar_row_is_specific_enough_to_beat_onyxs_own_rule():
    # Onyx styles the row with `.interactive[variant][state]`, a class and two attributes. An
    # override that drops the leading class loses the tie and the row stays grey -- which is
    # exactly what happened the first time.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_CSS, TIFFANY_BLUE

    assert SIDEBAR_CSS.count('.interactive[data-interactive-variant^="sidebar"]') == 3
    assert f"background-color:{TIFFANY_BLUE}" in SIDEBAR_CSS
    # Onyx's hover rule carries a pseudo-class, so the plain pair would lose under the pointer.
    assert ":hover:not([data-disabled])" in SIDEBAR_CSS
    assert "[data-interaction=hover]:not([data-disabled])" in SIDEBAR_CSS
    # Label and icons are read from these, not from `color`.
    assert "--interactive-foreground:#fff" in SIDEBAR_CSS
    assert "--interactive-foreground-icon:#fff" in SIDEBAR_CSS


def test_white_sidebar_is_scoped_to_light_mode():
    # `--background-tint-02` is what makes the panel dark in dark mode; forcing white unconditionally
    # would paint a white column into a dark UI.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_CSS

    assert (
        "html:not(.dark) .opal-sidebar-root__column"
        "{background-color:var(--background-tint-00)}"
    ) in SIDEBAR_CSS


def test_user_bubble_is_tinted_and_keeps_an_edge():
    # The tint is faint, so the shadow is still doing most of the work of separating the bubble from
    # the white canvas.
    from dreamference.chat.onyx_ui_overrides import MESSAGE_BUBBLE_CSS

    # The id is Onyx's own and carries the specificity to beat the Tailwind utility unaided.
    assert MESSAGE_BUBBLE_CSS.startswith("html:not(.dark) #onyx-human-message ")
    from dreamference.chat.onyx_ui_overrides import TIFFANY_TINT

    assert f"background-color:{TIFFANY_TINT}" in MESSAGE_BUBBLE_CSS
    assert "box-shadow:" in MESSAGE_BUBBLE_CSS


def test_chat_canvas_whitens_both_layers_that_paint_the_grey():
    # `body` carries the tint and so does the app shell stacked on it, via Tailwind's
    # `.bg-background` alias for the same token. Whitening only `body` repaints a surface nothing
    # can see and leaves the visible canvas grey -- which is exactly what happened. `.min-h-screen`
    # narrows it to the canvas: the alias alone also blanks the citation chips, which use it as
    # their fill.
    from dreamference.chat.onyx_ui_overrides import CHAT_SURFACE_CSS

    assert "html:not(.dark) body{background-color:var(--background-tint-00)}" in CHAT_SURFACE_CSS
    assert (
        "html:not(.dark) .bg-background.min-h-screen"
        "{background-color:var(--background-tint-00)}"
    ) in CHAT_SURFACE_CSS


def test_hidden_avatar_keeps_the_timeline_rail_in_layout():
    # The rail is what the message body's indent is measured against. `display:none` would collapse
    # it and slide the "Thought for Ns" header off the text it labels.
    from dreamference.chat.onyx_ui_overrides import AGENT_AVATAR_CSS

    assert "visibility:hidden" in AGENT_AVATAR_CSS
    assert "display:none" not in AGENT_AVATAR_CSS
    # Matched on the CSS variable the arbitrary-value class is built from, not the escaped class.
    assert AGENT_AVATAR_CSS.startswith('[class*="--timeline-rail-width"]')
    # Deliberately not scoped to the completed-message test id: Onyx sets that only once a message
    # finishes, so scoping there left the avatar on screen for the whole time an answer streamed.
    assert "onyx-ai-message" not in AGENT_AVATAR_CSS


def test_black_message_text_is_done_with_tokens_and_stays_out_of_dark_mode():
    # The tokens are black-at-alpha in light mode and *white*-at-alpha in dark. Forcing them black
    # unscoped would render every message invisible on a dark theme.
    from dreamference.chat.onyx_ui_overrides import MESSAGE_TEXT_CSS

    assert MESSAGE_TEXT_CSS.count("html:not(.dark) ") == 2
    assert "--text-04:#000" in MESSAGE_TEXT_CSS
    assert "--text-05:#000" in MESSAGE_TEXT_CSS
    # Redefining the tokens, not painting descendants: a blanket colour would flatten the syntax
    # highlighting in code blocks and the link colour along with it.
    assert "*{color:" not in MESSAGE_TEXT_CSS
    # --text-03 is the "Thought for Ns" meta label, chrome rather than message text.
    assert "--text-03" not in MESSAGE_TEXT_CSS


def test_assistant_is_told_its_own_name():
    # Onyx's persona name is a UI label and is never sent to the model, so without this the model
    # answers "what is your name?" from pretraining -- "I'm an AI assistant".
    from dreamference.chat.onyx_runner import (
        PUFFIN_ASSISTANT_INSTRUCTIONS, PUFFIN_ASSISTANT_NAME,
    )

    assert PUFFIN_ASSISTANT_NAME in PUFFIN_ASSISTANT_INSTRUCTIONS

    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_get_json", side_effect=[[{"id": 1, "name": "web_search"}], []]), \
         patch.object(OnyxRunner, "_request", return_value=({"id": 7}, None)) as request:
        assert runner._upsert_puffin_assistant("http://x/api", "cookie") == 7

    payload = request.call_args_list[0][0][1]
    assert payload["system_prompt"] == PUFFIN_ASSISTANT_INSTRUCTIONS
    # Appended to Onyx's base prompt, not in place of it -- the base prompt is what tells the model
    # how to drive the search and Python tools.
    assert payload["replace_base_system_prompt"] is False


def test_sidebar_mark_is_selected_by_the_artwork_geometry():
    # The two logo SVGs carry no class or id. The 64x64 viewBox is the same grid
    # `ONYX_LOGO_PATHS` keys its path substitutions to, so the two break together rather than one
    # of them silently missing.
    from dreamference.chat.onyx_brand_assets import ONYX_LOGO_PATHS
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_LOGO_CSS

    assert SIDEBAR_LOGO_CSS == 'svg[viewBox="0 0 64 64"]{display:none}'
    assert len(ONYX_LOGO_PATHS) == 4, "the 64x64 mark is still four paths"


def test_add_model_button_takes_its_divider_with_it():
    # A divider left behind would put a rule between the model name and nothing at all.
    from dreamference.chat.onyx_ui_overrides import MODEL_SELECTOR_CSS

    assert '[data-testid="model-selector"]>button:first-child' in MODEL_SELECTOR_CSS
    assert '[data-testid="model-selector"]>.opal-divider-vertical' in MODEL_SELECTOR_CSS


def test_sidebar_labels_match_on_the_jsx_prop_not_the_bare_word():
    # "New Session" and "Search Chats" also appear in analytics names and aria labels; matching the
    # bare string would rename things that are not this sidebar.
    from dreamference.chat.onyx_ui_labels import LABEL_SUBSTITUTIONS

    assert LABEL_SUBSTITUTIONS['children:"New Session"'] == 'children:"New"'
    assert LABEL_SUBSTITUTIONS['children:"Search Chats"'] == 'children:"Search"'
    # Every entry anchors on a quoted JS string literal, never a bare word -- a bare `New
    # Session` would also hit analytics event names and aria labels. Some keys carry syntax
    # around the literal (a route object, a call expression), so the invariant is the presence
    # of a complete quoted literal, not the key's final character.
    import re

    for key, value in LABEL_SUBSTITUTIONS.items():
        assert re.search(r'"[^"]+"', key), key
        assert re.search(r'"[^"]+"', value), value


def test_agents_section_is_hidden_by_what_it_contains():
    # The wrapper is a bare `flex flex-col` with no handle of its own, so the rule selects it via
    # the one child that carries a test id. Hidden, not removed -- dropping this constant from
    # UI_OVERRIDES brings the section back on the next configure.
    from dreamference.chat.onyx_ui_overrides import AGENTS_SECTION_CSS, UI_OVERRIDES

    assert ':has(>[data-testid="AppSidebar/more-agents"])' in AGENTS_SECTION_CSS
    assert AGENTS_SECTION_CSS.endswith("{display:none}")
    assert AGENTS_SECTION_CSS in UI_OVERRIDES


def test_projects_are_hidden_in_both_places_they_appear():
    # The sidebar section and the search palette's group. Hidden, not removed, in both.
    from dreamference.chat.onyx_ui_overrides import (
        PROJECTS_SECTION_CSS, SEARCH_PROJECTS_CSS, UI_OVERRIDES,
    )

    assert PROJECTS_SECTION_CSS in UI_OVERRIDES and SEARCH_PROJECTS_CSS in UI_OVERRIDES
    # The sidebar section is the one that is not the agents section -- it has no handle of its own.
    assert ':not(:has([data-testid="AppSidebar/more-agents"]))' in PROJECTS_SECTION_CSS
    # The palette is a flat list, so the heading is hidden separately from the entries.
    assert '*:has(+[data-command-item="new-project"])' in SEARCH_PROJECTS_CSS
    assert '[data-command-item^="project-"]' in SEARCH_PROJECTS_CSS


def test_sidebar_labels_are_black_without_erasing_dark_mode():
    # Onyx dims an unselected row to --text-03 (55% black) through the same variable the selected
    # row recolours. In dark mode that variable is white at partial alpha.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_TEXT_CSS

    assert SIDEBAR_TEXT_CSS.count("html:not(.dark) ") == 2
    assert "--interactive-foreground:#000" in SIDEBAR_TEXT_CSS
    # The icons beside "New" and "Search" stay a step back from the label.
    assert "--interactive-foreground-icon" not in SIDEBAR_TEXT_CSS


def test_footer_rule_cannot_take_the_composer_with_it():
    # Some layouts render the composer into the footer slot. Hiding the <footer> outright would
    # remove the input box, so the rule targets the version line's own span.
    from dreamference.chat.onyx_ui_overrides import FOOTER_CSS

    assert FOOTER_CSS == ".opal-root-layout__footer span:has(a){display:none}"


def test_badge_is_recoloured_through_the_variable_its_inline_style_reads():
    # The badge's colour is an inline style, which no stylesheet outranks without !important --
    # but the inline value is var(--action-link-05), so redefining the token is enough.
    from dreamference.chat.onyx_brand_assets import TIFFANY_BLUE
    from dreamference.chat.onyx_ui_overrides import NOTIFICATION_BADGE_CSS

    assert f"--action-link-05:{TIFFANY_BLUE}" in NOTIFICATION_BADGE_CSS
    assert "!important" not in NOTIFICATION_BADGE_CSS
    # Scoped to the sidebar: the same token is the app's link colour everywhere else.
    assert NOTIFICATION_BADGE_CSS.startswith(".opal-sidebar-root__column{")


def test_collapsed_sidebar_keeps_its_expand_control_visible():
    # Onyx reveals the expand button on :hover of the column, which on a collapsed sidebar hides
    # the one control that gets you back.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_FOLDED_CSS

    assert "[data-folded=true] .opal-sidebar-header__logo-fold{display:flex}" in SIDEBAR_FOLDED_CSS
    assert "[data-folded=true] .opal-sidebar-header__logo-rest{display:none}" in SIDEBAR_FOLDED_CSS


def test_share_button_is_hidden_by_its_own_aria_label():
    from dreamference.chat.onyx_ui_overrides import SHARE_BUTTON_CSS, UI_OVERRIDES

    assert SHARE_BUTTON_CSS == '[aria-label="share-chat-button"]{display:none}'
    assert SHARE_BUTTON_CSS in UI_OVERRIDES


def test_model_chip_is_hidden_rather_than_relocated():
    # Three attempts to move it onto the composer toolbar row are documented in the module; each was
    # right on one screen and wrong on the other, because the chip is not a descendant of the
    # composer box and CSS cannot reparent. The wrapper goes, not the chip, so the row collapses
    # instead of leaving a gap above the composer.
    from dreamference.chat.onyx_ui_overrides import MODEL_CHIP_CSS, UI_OVERRIDES

    assert MODEL_CHIP_CSS == 'div:has(>[data-testid="model-selector"]){display:none}'
    assert MODEL_CHIP_CSS in UI_OVERRIDES


def test_brand_mark_is_exactly_the_tiffany_used_in_the_ui():
    # A gradient means only one scanline of the favicon is actually Tiffany, so it never quite
    # matched the sidebar row beside it. One colour, used everywhere.
    from dreamference.chat.onyx_brand_assets import BRAND_START, TIFFANY_BLUE

    assert "#%02x%02x%02x" % BRAND_START == TIFFANY_BLUE.lower()


def test_settings_sections_are_matched_without_the_selector_climbing():
    # `div:has(> .card)` matches a section and not its ancestors: the container's own children are
    # sections, not cards, so the child combinator inside :has() stops the match climbing.
    from dreamference.chat.onyx_ui_overrides import SETTINGS_SECTIONS_CSS

    assert "div:has(>.card)" in SETTINGS_SECTIONS_CSS
    # Chats goes whole; Memory keeps the "Personal Preferences" label that heads the whole group.
    assert "div:has(>.card):first-child{display:none}" in SETTINGS_SECTIONS_CSS
    assert "div:has(>.card):nth-child(2)>.card" in SETTINGS_SECTIONS_CSS
    assert "div:has(>.card):nth-child(2){display:none}" not in SETTINGS_SECTIONS_CSS


def test_selected_row_avatar_keeps_its_rest_look():
    # This rule once *inverted* the disc for legibility on the Tiffany pill. The footer row no
    # longer paints on select ("selected" there means the menu is open), so inversion became
    # white-on-white; the rule now holds the rest look -- grey disc, white initial -- through
    # the selected state instead.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_AVATAR_CSS

    assert SIDEBAR_AVATAR_CSS.endswith("{background-color:var(--text-02);color:#fff}")
    # The initials carry their own colour class, so the descendants are recoloured too.
    assert SIDEBAR_AVATAR_CSS.count(".bg-background-neutral-inverted-00") == 2


def test_help_link_is_hidden_by_where_it_points():
    # docs.onyx.app is off-brand and unreachable from an air-gapped machine. The menu entry has no
    # id, but a link to a specific external host is an unambiguous handle.
    from dreamference.chat.onyx_ui_overrides import HELP_LINK_CSS, UI_OVERRIDES

    assert HELP_LINK_CSS == '[href^="https://docs.onyx.app"]{display:none}'
    assert HELP_LINK_CSS in UI_OVERRIDES


def test_sidebar_header_matches_the_section_title_colour():
    # New, Search and the wordmark take `--text-02`, the token "Recents" is drawn in, so navigation
    # chrome recedes while the chat titles below stay black.
    from dreamference.chat.onyx_ui_overrides import (
        SIDEBAR_HEADER_TEXT_CSS, SIDEBAR_TEXT_CSS,
    )

    assert "--interactive-foreground:var(--text-02)" in SIDEBAR_HEADER_TEXT_CSS
    # The wordmark is an SVG whose paths carry a fill, so it needs a rule of its own.
    assert 'svg[viewBox="0 0 152 64"] path{fill:var(--text-02)}' in SIDEBAR_HEADER_TEXT_CSS
    # ...and the black-text rule is confined to the chat list, or it would fight this one.
    assert SIDEBAR_TEXT_CSS.count(".opal-sidebar-body__content") == 2


def test_avatar_shows_one_initial_without_recomputing_it():
    # The initials are computed in JavaScript, so CSS cannot shorten them -- but it can decline to
    # draw the rest: the span collapses to font-size 0 and ::first-letter is given the size back.
    # That works only because Onyx renders the span as a block; ::first-letter skips inline boxes.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_AVATAR_DISC_CSS

    # Onyx sizes the initials with an inline `font-size`, which only !important can outrank -- the
    # same trap as the unread badge, minus the escape, since this one is a literal not a variable.
    assert "span{font-size:0!important;width:100%;text-align:center;" in SIDEBAR_AVATAR_DISC_CSS
    assert "span::first-letter{font-size:var(--dream-avatar-initial-size)" in SIDEBAR_AVATAR_DISC_CSS
    # Collapsed to zero font size the span is a zero-width box, so the disc's flex centring has
    # nothing to centre and the surviving letter sits off to one side.
    assert "width:100%;text-align:center" in SIDEBAR_AVATAR_DISC_CSS
    # The disc takes the same token as the name beside it, rather than staying a black dot.
    assert "background-color:var(--text-02)" in SIDEBAR_AVATAR_DISC_CSS


def test_account_name_rule_cannot_reach_the_avatar_initials():
    # The initials live in a span too. Targeting the label's `truncate` keeps this off them.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_ACCOUNT_CSS

    assert "span.truncate{font-size:.75rem;font-weight:400}" in SIDEBAR_ACCOUNT_CSS
    # The section titles' size and weight, but black: the name is the one part worth reading.
    assert "--interactive-foreground:#000" in SIDEBAR_ACCOUNT_CSS


def test_assistant_is_not_told_it_is_disconnected():
    # An earlier prompt opened with "air-gapped" and the model believed it: asked for the weather it
    # explained it had no internet and suggested looking out of the window, while holding a working
    # web_search tool. Local is not the same as disconnected.
    from dreamference.chat.onyx_runner import PUFFIN_ASSISTANT_INSTRUCTIONS

    assert "air-gapped" not in PUFFIN_ASSISTANT_INSTRUCTIONS
    assert "no internet" in PUFFIN_ASSISTANT_INSTRUCTIONS  # only as the thing never to say
    assert "search tool" in PUFFIN_ASSISTANT_INSTRUCTIONS


def test_google_login_is_additive_and_leaves_auth_type_alone():
    # Onyx 4.5 removed AUTH_TYPE single-provider mode: setting AUTH_TYPE=google_oauth now only logs
    # a warning and falls back to basic. Google is enabled purely by the two credentials, and
    # `oauth_enabled`/`password_auth_enabled` are separate flags, so both appear on the login page.
    from dreamference.chat.onyx_runner import (
        GOOGLE_REDIRECT_URI, ONYX_OAUTH_ID_KEY, ONYX_OAUTH_SECRET_KEY, OnyxRunner,
    )

    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_write_env_values", return_value=True) as write, \
         patch.object(OnyxRunner, "_recreate_api_server", return_value=True), \
         patch.object(OnyxRunner, "_allow_local_voice_endpoint", return_value=True), \
         patch.object(OnyxRunner, "_get_json",
                      return_value={"oauth_enabled": True, "password_auth_enabled": True}):
        assert runner.enable_google_login("id.apps.googleusercontent.com", "secret") is True

    written = write.call_args[0][0]
    assert set(written) == {ONYX_OAUTH_ID_KEY, ONYX_OAUTH_SECRET_KEY}
    assert "AUTH_TYPE" not in written
    assert GOOGLE_REDIRECT_URI.endswith("/auth/oauth/callback")


def test_google_login_recreates_rather_than_restarts_the_api_server():
    # Environment is fixed when a container is created, so `docker restart` would bring the old
    # values straight back. Recreating also reverts the voice patch, which is re-applied after.
    from dreamference.chat.onyx_runner import OnyxRunner

    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_write_env_values", return_value=True), \
         patch.object(OnyxRunner, "_recreate_api_server", return_value=True) as recreate, \
         patch.object(OnyxRunner, "_allow_local_voice_endpoint", return_value=True) as voice, \
         patch.object(OnyxRunner, "_get_json", return_value={"oauth_enabled": True}):
        runner.enable_google_login("id", "secret")

    recreate.assert_called_once()
    voice.assert_called_once()


def test_google_login_refuses_half_a_credential_pair():
    from dreamference.chat.onyx_runner import OnyxRunner

    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_write_env_values") as write:
        assert runner.enable_google_login("id-only", "") is False
        write.assert_not_called()


def test_env_writer_replaces_the_commented_placeholder_rather_than_duplicating_it(tmp_path):
    # onyx-cli ships the file with these keys present but commented out. Appending instead of
    # rewriting would leave the commented line above a second, live one.
    import dreamference.chat.onyx_runner as onyx

    env = tmp_path / ".env"
    env.write_text("# OAUTH_CLIENT_ID=\nUNRELATED=keep-me\n# OAUTH_CLIENT_SECRET=\n")
    with patch.object(onyx, "ONYX_ENV_FILE", str(env)):
        assert onyx.OnyxRunner._write_env_values({"OAUTH_CLIENT_ID": "abc"}) is True

    text = env.read_text()
    assert 'OAUTH_CLIENT_ID="abc"' in text
    assert "# OAUTH_CLIENT_ID=" not in text
    assert "UNRELATED=keep-me" in text


def test_telemetry_is_disabled_before_the_session_is_opened():
    # Applying it recreates the API server, so authenticating first would leave the session cookie
    # pointing at a container about to be replaced.
    from dreamference.chat.onyx_runner import OnyxRunner

    order = []
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "disable_telemetry",
                      side_effect=lambda: order.append("telemetry")), \
         patch.object(OnyxRunner, "bind_to_loopback",
                      side_effect=lambda: order.append("loopback")), \
         patch.object(OnyxRunner, "_authenticate",
                      side_effect=lambda *a, **k: order.append("auth") or None):
        assert runner.configure() == 1

    # Both recreate a container, so both come before the session cookie is taken.
    assert order == ["telemetry", "loopback", "auth"]


def test_the_web_ui_is_bound_to_this_machine_only(tmp_path):
    # Onyx's nginx publishes ${HOST_PORT_80:-80} and ${HOST_PORT:-3000} on every interface by
    # default, which put the admin account (with its published default password) on the network.
    from dreamference.chat import onyx_runner as onyx
    from dreamference.chat.onyx_runner import OnyxRunner

    env = tmp_path / ".env"
    env.write_text("# HOST_PORT_80=80\nHOST_PORT=3000\nOTHER=1\n")
    recreated = []
    with patch.object(onyx, "ONYX_ENV_FILE", str(env)), \
         patch.object(OnyxRunner, "_recreate_service",
                      side_effect=lambda service, wait_healthy: recreated.append(service) or True):
        assert OnyxRunner().bind_to_loopback() is True
        assert OnyxRunner().bind_to_loopback() is True  # already set: no second recreate

    lines = env.read_text().splitlines()
    assert 'HOST_PORT="127.0.0.1:3000"' in lines and 'HOST_PORT_80="127.0.0.1:80"' in lines
    assert "OTHER=1" in lines
    assert recreated == ["nginx"]


def test_telemetry_switch_is_a_no_op_once_it_is_set():
    # configure() calls this every run, and applying it means recreating a container.
    from dreamference.chat.onyx_runner import ONYX_PRIVACY_ENV, OnyxRunner

    assert ONYX_PRIVACY_ENV == {"DISABLE_TELEMETRY": "true"}
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_env_already_set", return_value=True), \
         patch.object(OnyxRunner, "_write_env_values") as write, \
         patch.object(OnyxRunner, "_recreate_api_server") as recreate:
        assert runner.disable_telemetry() is True
        write.assert_not_called()
        recreate.assert_not_called()


def test_env_matcher_ignores_a_commented_out_key(tmp_path):
    # The shipped file has these keys commented out; a commented line is not a setting.
    import dreamference.chat.onyx_runner as onyx

    env = tmp_path / ".env"
    env.write_text('# DISABLE_TELEMETRY=true\n')
    with patch.object(onyx, "ONYX_ENV_FILE", str(env)):
        assert onyx.OnyxRunner._env_already_set({"DISABLE_TELEMETRY": "true"}) is False
        env.write_text('DISABLE_TELEMETRY="true"\n')
        assert onyx.OnyxRunner._env_already_set({"DISABLE_TELEMETRY": "true"}) is True


def test_sidebar_is_not_auto_hidden():
    # Briefly auto-hidden on hover, then reverted: the sidebar stays open, and the control that
    # would close it is hidden instead.
    import dreamference.chat.onyx_ui_overrides as overrides

    assert not hasattr(overrides, "SIDEBAR_HOVER_CSS")
    # The hover-slide's signature specifically -- a blanket translateX ban started tripping on
    # the image search progress bar's animation, which slides its own thumb, not the sidebar.
    assert "translateX(-100%)" not in overrides.UI_OVERRIDES
    assert ".opal-sidebar-root__column{transform" not in overrides.UI_OVERRIDES


def test_closing_the_sidebar_is_removed_without_stranding_a_folded_one():
    # Hiding the way in would be a trap on its own: SIDEBAR_FOLDED_CSS keeps the expand control
    # visible for anyone whose sidebar is already collapsed, so there is still a way back.
    from dreamference.chat.onyx_ui_overrides import (
        SIDEBAR_CLOSE_CSS, SIDEBAR_FOLDED_CSS, UI_OVERRIDES,
    )

    assert SIDEBAR_CLOSE_CSS == '[aria-label="Close Sidebar"]{display:none}'
    assert SIDEBAR_CLOSE_CSS in UI_OVERRIDES
    assert "__logo-fold{display:flex}" in SIDEBAR_FOLDED_CSS


def test_avatar_initial_has_a_line_box_to_centre_in():
    # At font-size 0, `line-height:normal` resolves to zero, so the glyph has no line box and sits
    # high in the circle. The disc's own size supplies one.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_AVATAR_DISC_CSS

    assert "--dream-avatar-disc-size:18px" in SIDEBAR_AVATAR_DISC_CSS
    assert "line-height:var(--dream-avatar-disc-size)" in SIDEBAR_AVATAR_DISC_CSS


def test_vertical_divider_is_removed():
    from dreamference.chat.onyx_ui_overrides import DIVIDER_CSS, UI_OVERRIDES

    assert DIVIDER_CSS == ".opal-divider-line-vertical{display:none}"
    assert DIVIDER_CSS in UI_OVERRIDES


def test_gmail_tool_mirrors_the_web_search_shape():
    # `gmail_search` finds and `gmail_message` reads, the same split as web_search and open_url. A
    # search returning whole bodies would spend the context window on threads the question was not
    # about.
    from dreamference.chat.gmail_search_service import openapi_definition
    from dreamference.chat.onyx_runner import GMAIL_CONTAINER_URL

    document = openapi_definition(GMAIL_CONTAINER_URL)
    operations = [v["get"]["operationId"] for v in document["paths"].values()]
    assert operations == ["gmail_search", "gmail_message"]
    assert document["servers"][0]["url"] == GMAIL_CONTAINER_URL


def test_gmail_service_secret_is_shared_by_both_sides():
    # Onyx's custom-tool client performs no SSRF validation and the service sits on a network other
    # containers share, so this header is the only thing protecting a live mailbox credential.
    from dreamference.chat.gmail_search_service import AUTH_HEADER
    from dreamference.chat.onyx_runner import GMAIL_AUTH_HEADER

    assert GMAIL_AUTH_HEADER == AUTH_HEADER


def test_gmail_registration_does_not_wait_for_a_mailbox():
    # It used to refuse until credentials existed, on the reasoning that a tool pointing at an
    # unconnected service is an action that always fails. The ordering was backwards: the Connect
    # button lives on Settings -> Connectors, a page that lists the tool, so the tool had to exist
    # before anyone could connect. An unconnected search answers with the way to connect.
    from dreamference.chat.onyx_runner import OnyxRunner

    runner = OnyxRunner()
    with patch("dreamference.chat.gmail_credentials.GmailCredentials.load", return_value=None), \
         patch.object(OnyxRunner, "_gmail_secret", return_value="s3cret"), \
         patch.object(OnyxRunner, "_start_gmail_service", return_value=True), \
         patch.object(OnyxRunner, "_get_json", return_value=[]), \
         patch.object(OnyxRunner, "_request", return_value=({}, None)) as request:
        assert runner.enable_gmail_search("http://x/api", "cookie") is True
        assert request.call_args[0][0].endswith("/admin/tool/custom")


def test_configure_registers_gmail_so_a_fresh_install_has_the_tool():
    # Registering only from `puffin-admin puffin gmail --email …` meant a fresh install had no Gmail tool
    # until someone had finished a flow they can only start from the page that lists it.
    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_authenticate", return_value="cookie"), \
         patch.object(OnyxRunner, "_request", return_value=({"id": 1}, None)), \
         patch.object(OnyxRunner, "docker_bridge_gateway", return_value="172.17.0.1"), \
         patch.object(OnyxRunner, "_find_provider", return_value=None), \
         patch.object(OnyxRunner, "enable_gmail_search") as gmail:
        assert runner.configure() == 0
        gmail.assert_called_once()

        gmail.reset_mock()
        assert runner.configure(enable_gmail=False) == 0
        gmail.assert_not_called()


def test_an_unconnected_search_names_the_place_to_connect():
    # The string is read by the model and relayed to the user, so it points at the button rather
    # than at a command they would have to leave the app to run.
    from dreamference.chat.gmail_search_service import GmailSearchService

    with patch.object(GmailSearchService, "credentials", return_value=None):
        answer = GmailSearchService.search("anything", 5)

    assert "Settings -> Gmail Accounts" in answer["error"]
    assert "Connect to Google" in answer["error"]


def test_a_tampered_credentials_file_is_refused_rather_than_half_read(tmp_path):
    # Encrypt-then-MAC: the tag covers the ciphertext, so a flipped byte reads as "not connected"
    # rather than as a corrupted token that would fail against Google with a confusing message.
    from dreamference.chat.gmail_search_service import GmailSearchService

    GmailSearchService.save_token("me@gmail.com", "ya29.tok", 3600, str(tmp_path))
    stored = json.loads((tmp_path / "credentials.json").read_text())
    account = stored["accounts"]["me@gmail.com"]
    # A different first character every time: writing a fixed "A" left the file untouched whenever
    # the random nonce already began with one, and the release's test gate failed on that 1 in 64.
    first = account["access_token"][0]
    account["access_token"] = ("B" if first == "A" else "A") + account["access_token"][1:]
    (tmp_path / "credentials.json").write_text(json.dumps(stored))

    assert GmailSearchService.credentials(str(tmp_path)) == []


def test_all_mail_is_found_by_its_attribute_not_its_english_name():
    # `[Gmail]/All Mail` is what every example hard-codes and it is wrong for any account whose
    # interface language is not English. The `\All` attribute is the same folder in any language.
    from dreamference.chat.gmail_search_service import GmailSearchService

    class FakeConnection:
        def list(self):
            return "OK", [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\All \\HasNoChildren) "/" "[Gmail]/&BBIEQQR/BDAEHwQ+BEcEQgQw-"',
            ]

    assert GmailSearchService._all_mail_folder(FakeConnection()) \
        == "[Gmail]/&BBIEQQR/BDAEHwQ+BEcEQgQw-"


def test_a_mailbox_search_never_marks_anything_read():
    # The REST API could not mark a message read; IMAP can. A search tool that silently marked
    # twenty messages as seen would be doing real damage to a mailbox, so every fetch peeks and the
    # folder is selected read-only.
    import inspect

    from dreamference.chat.gmail_search_service import GmailSearchService

    for method in (GmailSearchService._fetch_headers, GmailSearchService._fetch_body):
        source = inspect.getsource(method)
        assert "BODY.PEEK" in source
        assert "BODY[" not in source
    assert "readonly=True" in inspect.getsource(GmailSearchService._open_mailboxes)


def test_a_non_ascii_query_is_sent_as_a_utf8_literal():
    # imaplib encodes ordinary arguments as ASCII, so a Cyrillic search would raise before it ever
    # reached Google -- and this mailbox is partly Russian. A literal also needs no quoting, which
    # removes the other half of the problem: a query containing a quote or a backslash.
    from dreamference.chat.gmail_search_service import GmailSearchService

    class FakeConnection:
        literal = None

        def __init__(self):
            self.calls = []

        def uid(self, *args):
            self.calls.append(args)
            return "OK", [b"7 9"]

    connection = FakeConnection()
    assert GmailSearchService._search_uids(connection, "от кого:аня") == [b"7", b"9"]
    assert connection.literal == "от кого:аня".encode("utf-8")
    assert connection.calls == [("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")]


def test_streaming_caret_is_pinned_on_the_class_combination():
    # The caret has no id, test id or BEM class, and `bg-theme-primary-05` alone is also buttons
    # and badges. The combination -- a pulsing 8x16 inline block in the primary colour -- is what
    # identifies it. If Onyx restyles the caret this stops matching, which shows up as a dark caret
    # coming back rather than as damage elsewhere.
    from dreamference.chat.onyx_ui_overrides import STREAMING_CURSOR_CSS

    assert ".animate-pulse.bg-theme-primary-05.inline-block.w-2.h-4" in STREAMING_CURSOR_CSS
    from dreamference.chat.onyx_ui_overrides import TIFFANY_CARET, TIFFANY_TINT

    assert f"background-color:{TIFFANY_CARET}" in STREAMING_CURSOR_CSS
    # Not the bubble tint: a wash sized for a whole message vanishes at 8x16 pixels.
    assert TIFFANY_CARET != TIFFANY_TINT


def test_scrollbar_defaults_to_visible_and_hides_only_in_the_browser():
    # Hiding depends on the engine marker the injected script writes onto <html>. If that has not
    # run -- or at all -- an "invisible until hover" default leaves a scroll container with no
    # visible affordance. Defaulting to visible makes the failure mode a scrollbar that is merely
    # always there. It is also what the desktop app wants, where hover proved unreliable.
    from dreamference.chat.onyx_ui_overrides import SIDEBAR_SCROLLBAR_CSS

    from dreamference.chat.onyx_ui_overrides import SIDEBAR_SCROLLBAR_THUMB

    assert SIDEBAR_SCROLLBAR_CSS.startswith(
        ".opal-sidebar-body__scroll{scrollbar-width:thin;"
        f"scrollbar-color:{SIDEBAR_SCROLLBAR_THUMB} var(--background-tint-00)}}"
    )
    # Only the browser hides it; the desktop app keeps it permanently.
    assert SIDEBAR_SCROLLBAR_CSS.count('html[data-puffin-engine="blink"]') == 2
    # WebKitGTK paints its scrollbar as engine chrome that no CSS colour reaches, so the app
    # suppresses the bar entirely; the browser keeps its hover-revealed one.
    assert 'html[data-puffin-engine="webkit"] .opal-sidebar-body__scroll{scrollbar-width:none}' in SIDEBAR_SCROLLBAR_CSS


def test_scrollbar_styles_both_engines_because_they_disagree():
    # Blink ignores `::-webkit-scrollbar` whenever `scrollbar-color` is set, so the standard
    # properties do the work there. WebKitGTK honours the pseudo-elements and draws a track
    # hairline the standard transparent track colour does not remove -- the grey line the desktop
    # app had and the browser never did. Both paths paint the track transparent.
    from dreamference.chat.onyx_ui_overrides import (
        SIDEBAR_SCROLLBAR_CSS, SIDEBAR_SCROLLBAR_THUMB,
    )

    assert "scrollbar-color:" in SIDEBAR_SCROLLBAR_CSS
    # The trough is painted, not left transparent: WebKitGTK draws its edge on the scrollbar
    # element, and a transparent trough left that edge showing as a rule beside the thumb.
    assert "::-webkit-scrollbar{width:8px;background-color:var(--background-tint-00);border:0" in SIDEBAR_SCROLLBAR_CSS
    assert "::-webkit-scrollbar-track{background-color:var(--background-tint-00);border:0" in SIDEBAR_SCROLLBAR_CSS
    # A literal white would draw a stripe down a dark sidebar; the token follows the theme.
    assert "#fff" not in SIDEBAR_SCROLLBAR_CSS.lower()
    # Separate selectors: sharing one applied display:none to the track and collapsed the
    # scrollbar into the very hairline this removes.
    assert "::-webkit-scrollbar-button{display:none}" in SIDEBAR_SCROLLBAR_CSS
    assert "scrollbar-track,.opal-sidebar-body__scroll::-webkit-scrollbar-button" not in SIDEBAR_SCROLLBAR_CSS
    # One definition of the thumb weight, used by both engines.
    assert SIDEBAR_SCROLLBAR_CSS.count(SIDEBAR_SCROLLBAR_THUMB) == 3
    assert "'" not in SIDEBAR_SCROLLBAR_CSS


def test_drawn_scrollbar_rests_hidden_until_the_script_places_it():
    # `display:none` is the stylesheet's resting state and the script flips it per update, so a
    # page where the script never ran shows nothing rather than a stray mispositioned bar.
    from dreamference.chat.onyx_ui_overrides import (
        CUSTOM_SCROLLBAR_CSS, SIDEBAR_SCROLLBAR_THUMB, UI_OVERRIDES,
    )

    assert CUSTOM_SCROLLBAR_CSS in UI_OVERRIDES
    assert "display:none" in CUSTOM_SCROLLBAR_CSS
    assert SIDEBAR_SCROLLBAR_THUMB in CUSTOM_SCROLLBAR_CSS


@contextlib.contextmanager
def _serve_gmail(tmp_path):
    """
    Runs the Gmail service on a free port against a temporary config directory.

    The `CONFIG_DIR` patch has to stay active for the life of the server, not just while it starts:
    the handler resolves the path per request, on the serving thread.
    """
    import socket
    import threading

    import dreamference.chat.gmail_search_service as service

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with patch.object(service, "CONFIG_DIR", str(tmp_path)):
        threading.Thread(
            target=service.GmailSearchService.serve, args=(port, "secret"), daemon=True
        ).start()
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1).read()
                break
            except OSError:
                time.sleep(0.05)
        yield port, service



def test_the_gmail_container_runs_as_the_invoking_user(monkeypatch):
    from conftest import REAL_START_GMAIL_SERVICE
    monkeypatch.setattr(OnyxRunner, "_start_gmail_service", REAL_START_GMAIL_SERVICE)
    # The service writes to the mounted directory now -- a connection made from the UI stores the
    # credentials from inside the container -- and a root container writing into a user-owned
    # directory leaves files their owner cannot read or replace. Same trap as the torch.compile
    # cache, same fix. Nothing in here needs root: the port is above 1024, only `/config` is
    # written, and stdlib IMAP over TLS was verified working as uid 1000.
    import os

    runner = OnyxRunner()
    with patch.object(OnyxRunner, "_onyx_network", return_value="onyx_default"), \
         patch("shutil.copyfile"), \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert runner._start_gmail_service("secret") is True

    command = run.call_args_list[-1][0][0]
    assert "--user" in command
    assert command[command.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_xoauth2_answers_the_second_challenge_with_nothing():
    # Two details, neither guessable. imaplib base64-encodes whatever the responder returns, so it
    # must be raw bytes -- pre-encoding is encoded twice and Gmail rejects it with an error that
    # reads like a bad password. And on a token Google will not accept, the server does not fail
    # the command: it sends a continuation challenge and imaplib calls the responder again. An
    # empty reply is the protocol's way to draw out the real `NO`; returning the credential again
    # leaves the exchange going nowhere until the socket timeout, so an expired token would surface
    # as a stall rather than as an error.
    from dreamference.chat.gmail_search_service import GmailSearchService

    respond = GmailSearchService._xoauth2("me@gmail.com", "ya29.token")

    assert respond(b"") == b"user=me@gmail.com\x01auth=Bearer ya29.token\x01\x01"
    assert respond(b'{"status":"400"}') == b""



def test_the_service_holds_no_long_lived_credential(tmp_path):
    # An account connected through GNOME hands over an hour's access at a time and keeps the
    # refresh token itself, so its credentials file is worth an hour of read access rather than a
    # mailbox. Nothing long-lived is written unless a refresh token is passed in explicitly.
    from dreamference.chat.gmail_search_service import GmailSearchService

    assert GmailSearchService.save_token("me@gmail.com", "ya29.tok", 3600, str(tmp_path))
    stored = json.loads((tmp_path / "credentials.json").read_text())
    account = stored["accounts"]["me@gmail.com"]

    assert "refresh_token" not in account and "app_password" not in account
    assert "ya29.tok" not in json.dumps(stored)
    assert account["expires_at"] > 0
    assert GmailSearchService.credentials(str(tmp_path)) == [
        {"email": "me@gmail.com", "access_token": "ya29.tok"},
    ]


def test_an_expired_token_reads_as_not_connected(tmp_path):
    # It is a stale file rather than a failure: the timer that should have replaced it did not
    # run. Reporting "not connected" brings the Connect button back, which is the honest state.
    from dreamference.chat.gmail_search_service import GmailSearchService

    GmailSearchService.save_token("me@gmail.com", "ya29.tok", 30, str(tmp_path))

    assert GmailSearchService.credentials(str(tmp_path)) == []






def test_image_search_registers_as_a_custom_tool_with_its_secret_header():
    # Mirrors the Gmail registration contract: an OpenAPI document (not the spec's bare-URL
    # sketch -- Onyx's custom-tool API consumes a document), a shared-secret header, and
    # create-on-absent so a re-run updates instead of duplicating.
    from dreamference.chat.image_search_service import AUTH_HEADER
    from dreamference.chat.onyx_runner import IMAGE_SEARCH_CONTAINER_URL, IMAGE_SEARCH_TOOL_NAME

    runner = OnyxRunner()
    calls = []

    with patch.object(OnyxRunner, "_image_search_secret", return_value="s3cret"), \
         patch.object(OnyxRunner, "_start_siglip", return_value=True), \
         patch.object(OnyxRunner, "_start_image_search_service", return_value=True), \
         patch.object(OnyxRunner, "_inject_image_route", return_value=True), \
         patch.object(OnyxRunner, "_get_json", return_value=[]), \
         patch.object(OnyxRunner, "_request",
                      side_effect=lambda u, p, c, method="POST": (calls.append((u, p, method)), ({}, None))[1]):
        assert runner.enable_image_search("http://x/api", "cookie") is True

    url, payload, method = calls[0]
    assert url.endswith("/admin/tool/custom") and method == "POST"
    assert payload["name"] == IMAGE_SEARCH_TOOL_NAME
    assert payload["custom_headers"] == [{"key": AUTH_HEADER, "value": "s3cret"}]
    definition = payload["definition"]
    assert definition["servers"] == [{"url": IMAGE_SEARCH_CONTAINER_URL}]
    assert "/search" in definition["paths"]


def test_the_nginx_image_route_is_deferred_resolution_and_idempotent():
    # A literal proxy_pass hostname is resolved at nginx config load; with the sidecar absent
    # nginx would refuse to start and take the whole UI down with it. The injected route must
    # therefore use the resolver-plus-variable form, and rewriting the template twice must not
    # accumulate blocks.
    template = "server {\n    client_max_body_size 5G;\n    location / {}\n}\n"
    once = OnyxRunner.apply_image_route(template)
    assert "resolver 127.0.0.11" in once
    assert "set $puffin_img" in once
    assert "proxy_pass $puffin_img;" in once
    assert "proxy_pass http" not in once
    assert OnyxRunner.apply_image_route(once) == once
    # A template without the anchor is left alone rather than guessed at.
    assert OnyxRunner.apply_image_route("server {}\n") == "server {}\n"
def test_a_search_returns_the_messages_it_finds():
    # search() swallows per-account failures so one broken mailbox does not hide the others, which
    # also hid a call to a helper that no longer existed: every search came back empty, silently.
    from dreamference.chat.gmail_search_service import GmailSearchService

    class FakeConnection:
        literal = None

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"7 9"]
            return "OK", [(
                b"9 (X-GM-MSGID 1234 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {40}",
                b"From: a@b.c\r\nSubject: Hi\r\nDate: Mon, 1 Jan 2026\r\n\r\n",
            ), b")"]

        def logout(self):
            pass

    with patch.object(GmailSearchService, "_open_mailboxes",
                      return_value=({"me@gmail.com": FakeConnection()}, None, [])):
        answer = GmailSearchService.search("hi", 1)

    assert answer == {"messages": [{
        "id": "me@gmail.com|1234", "from": "a@b.c", "subject": "Hi", "date": "Mon, 1 Jan 2026",
    }]}


def test_onyx_is_registered_with_the_model_actually_served(monkeypatch, tmp_path):
    # `server start --model X` with X not the configured model left Onyx asking for the old id,
    # and every web-chat answer came back empty (the SGLang switch, 2026-09-29).
    import io
    import json
    import urllib.request
    from dreamference.chat import onyx_runner
    from dreamference.chat.onyx_runner import OnyxRunner
    from dreamference.config import DreamferenceConfig

    from conftest import REAL_SERVED_MODEL_KEY
    monkeypatch.setattr(OnyxRunner, "served_model_key", REAL_SERVED_MODEL_KEY)
    runner = OnyxRunner(config=DreamferenceConfig(config_file=str(tmp_path / "d.toml"),
                                                  model="qwen3.5-122b-a10b-hybrid-dflash"))

    def serving(model_id):
        body = json.dumps({"data": [{"id": model_id}]}).encode()
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))

    serving("RadixArk/Qwen3.8-27B-NVFP4")
    assert runner.served_model_key() == "qwen3.8-27b-nvfp4-dflash2"
    serving("Intel/Qwen3.5-122B-A10B-int4-AutoRound")  # shared by two recipes: the configured wins
    assert runner.served_model_key() == "qwen3.5-122b-a10b-hybrid-dflash"

    def down(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert runner.served_model_key() == "qwen3.5-122b-a10b-hybrid-dflash"
