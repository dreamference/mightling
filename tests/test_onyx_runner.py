import json
from unittest.mock import patch

from dreamference.runner import OnyxInstaller, OnyxRunner
from dreamference.runner.onyx_runner import (
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
    assert models[0]["max_input_tokens"] == 131072


def test_default_admin_email_avoids_reserved_domains():
    # email-validator rejects .local, .localhost, .test and .invalid outright, so a default in
    # one of those makes the very first `dream onyx configure` fail with a 422.
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
    from dreamference.runner import OnyxBrandAssets

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


def test_brand_asset_install_is_skipped_when_onyx_is_not_running():
    from dreamference.runner import OnyxBrandAssets

    with patch.object(OnyxBrandAssets, "find_web_container", return_value=None), \
         patch("subprocess.run") as run:
        assert OnyxBrandAssets.install() is False
        run.assert_not_called()


def test_wordmark_replacement_targets_every_onyx_letter():
    # The sidebar wordmark is four inline letter paths, not /logotype.png -- which the bundle
    # never references. Missing one letter leaves a fragment of "onyx" on screen.
    from dreamference.runner.onyx_brand_assets import (
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
    from dreamference.runner.onyx_brand_assets import ONYX_APP_NAME_STRINGS

    for key in ONYX_APP_NAME_STRINGS:
        assert "application_name" in key or "return" in key


def test_voice_registers_the_local_whisper_endpoint():
    # Onyx shows no microphone until an STT provider exists, so the button is a registration.
    from dreamference.runner.onyx_runner import (
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
    from dreamference.runner.onyx_runner import ONYX_VOICE_PROVIDER_NAME

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
    from dreamference.runner.onyx_runner import (
        ONYX_VOICE_SSRF_ANCHOR, ONYX_VOICE_SSRF_PATCH,
    )

    assert '== "azure"' in ONYX_VOICE_SSRF_ANCHOR
    assert '"azure", "openai"' in ONYX_VOICE_SSRF_PATCH


def test_every_onyx_face_is_substituted_by_a_font_the_installer_fetches():
    # A family named here but missing from FONT_SOURCES rewrites Onyx's CSS to point at a file
    # that is never copied in, which serves a 404 and falls back to the browser's default.
    from dreamference.runner.onyx_ui_fonts import FACE_SUBSTITUTIONS, FONT_SOURCES

    assert set(FACE_SUBSTITUTIONS) == {"Hanken Grotesk", "KH Teka", "DM Mono"}
    for filename, _ in FACE_SUBSTITUTIONS.values():
        assert filename in FONT_SOURCES


def test_face_rewrite_keeps_onyxs_own_family_names():
    # The whole approach rests on this: the family name stays, only the file behind it changes,
    # so the forty-odd `font-family` declarations elsewhere in the CSS need no edit at all.
    from dreamference.runner.onyx_ui_fonts import OnyxUIFonts

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "3"
        assert OnyxUIFonts.patch_css("web") == 3

    script = run.call_args[0][0][-1]
    assert "font-family:Hanken Grotesk" in script
    assert "/fonts/Roboto.woff2" in script
    assert "/fonts/RobotoMono.woff2" in script


def test_a_newly_copied_face_restarts_the_web_server():
    # Next.js builds its static routes from `public/` at boot, so a face copied into a running
    # container has no URL until the server starts again.
    from dreamference.runner.onyx_ui_fonts import OnyxUIFonts

    with patch.object(OnyxUIFonts, "ensure_fonts", return_value={"Roboto.woff2": "/tmp/r"}), \
         patch.object(OnyxUIFonts, "installed_faces", return_value=set()), \
         patch.object(OnyxUIFonts, "patch_css", return_value=2), \
         patch.object(OnyxUIFonts, "restart_web_server", return_value=True) as restart, \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert OnyxUIFonts.install("web") is True
        restart.assert_called_once()


def test_faces_already_in_the_container_are_not_recopied_or_restarted():
    from dreamference.runner.onyx_ui_fonts import OnyxUIFonts

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
    from dreamference.runner.onyx_ui_overrides import HOVER_TOOLBAR_CSS

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
    from dreamference.runner.onyx_ui_overrides import (
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
    from dreamference.runner.onyx_ui_overrides import SIDEBAR_CSS, TIFFANY_BLUE

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
    from dreamference.runner.onyx_ui_overrides import SIDEBAR_CSS

    assert (
        "html:not(.dark) .opal-sidebar-root__column"
        "{background-color:var(--background-tint-00)}"
    ) in SIDEBAR_CSS


def test_user_bubble_is_white_and_keeps_an_edge():
    # A white bubble on Onyx's near-white chat background has no edge of its own, so the shadow is
    # load-bearing rather than decoration -- it is what Telegram uses in place of a fill.
    from dreamference.runner.onyx_ui_overrides import MESSAGE_BUBBLE_CSS

    # The id is Onyx's own and carries the specificity to beat the Tailwind utility unaided.
    assert MESSAGE_BUBBLE_CSS.startswith("html:not(.dark) #onyx-human-message ")
    assert "background-color:var(--background-tint-00)" in MESSAGE_BUBBLE_CSS
    assert "box-shadow:" in MESSAGE_BUBBLE_CSS


def test_chat_canvas_whitens_both_layers_that_paint_the_grey():
    # `body` carries the tint and so does the app shell stacked on it, via Tailwind's
    # `.bg-background` alias for the same token. Whitening only `body` repaints a surface nothing
    # can see and leaves the visible canvas grey -- which is exactly what happened. `.min-h-screen`
    # narrows it to the canvas: the alias alone also blanks the citation chips, which use it as
    # their fill.
    from dreamference.runner.onyx_ui_overrides import CHAT_SURFACE_CSS

    assert "html:not(.dark) body{background-color:var(--background-tint-00)}" in CHAT_SURFACE_CSS
    assert (
        "html:not(.dark) .bg-background.min-h-screen"
        "{background-color:var(--background-tint-00)}"
    ) in CHAT_SURFACE_CSS


def test_hidden_avatar_keeps_the_timeline_rail_in_layout():
    # The rail is what the message body's indent is measured against. `display:none` would collapse
    # it and slide the "Thought for Ns" header off the text it labels.
    from dreamference.runner.onyx_ui_overrides import AGENT_AVATAR_CSS

    assert "visibility:hidden" in AGENT_AVATAR_CSS
    assert "display:none" not in AGENT_AVATAR_CSS
    # Matched on the CSS variable the arbitrary-value class is built from, not the escaped class.
    assert '[class*="--timeline-rail-width"]' in AGENT_AVATAR_CSS
    assert AGENT_AVATAR_CSS.startswith('[data-testid="onyx-ai-message"] ')
