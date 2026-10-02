"""`/airgapped` (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md): the Python side of the setting, and what
Python, the launcher and the web commands must agree on -- the default level, and one resolver."""

import re
from pathlib import Path

import dreamference.config.dreamference_config as cfg_mod
from dreamference.config import DreamferenceConfig

REPO = Path(__file__).resolve().parent.parent
LEAF_RS = REPO / "puffin-rs" / "airgapped" / "src" / "lib.rs"
WEB_COPY_RS = REPO / "puffin-web-rs" / "src" / "airgapped.rs"
LAUNCHER_RS = REPO / "puffin-rs" / "src" / "airgapped.rs"


def test_the_level_resolves_through_the_tiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "tiers.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "off"

    cfg_file.write_text("puffin_airgapped: duckduckgo\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "duckduckgo"
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "ON")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"
    assert DreamferenceConfig(config_file=str(cfg_file), puffin_airgapped="ddg").puffin_airgapped == "duckduckgo"


def test_an_invalid_value_is_skipped_not_adopted(tmp_path, monkeypatch):
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text("puffin_airgapped: duckduckgo\n")
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "sealed")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "duckduckgo"
    assert DreamferenceConfig(config_file=str(cfg_file), puffin_airgapped="max").puffin_airgapped == "duckduckgo"


def test_a_yaml_boolean_is_the_level_it_spells(tmp_path, monkeypatch):
    # YAML reads a bare `on` as true; taking that for "invalid" would silently leave the network on.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "bool.yaml"
    cfg_file.write_text("puffin_airgapped: on\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"


def test_save_config_writes_only_a_non_default_level(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "save.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "puffin_airgapped" not in cfg_file.read_text()

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.puffin_airgapped = "on"
    config.save_config()
    assert DreamferenceConfig(config_file=str(cfg_file)).puffin_airgapped == "on"


def test_the_default_and_the_level_names_match_the_rust_side():
    text = LEAF_RS.read_text()
    match = re.search(r'pub const DEFAULT_PUFFIN_AIRGAPPED: &str = "(\w+)";', text)
    assert match, "DEFAULT_PUFFIN_AIRGAPPED not found in puffin-rs/airgapped/src/lib.rs"
    assert match.group(1) == cfg_mod.DEFAULT_PUFFIN_AIRGAPPED
    for name in cfg_mod.PUFFIN_AIRGAPPED_LEVELS:
        assert f'=> "{name}"' in text, name


def test_the_web_commands_carry_the_same_resolver():
    # puffin-web-rs is built on its own, outside the Codex workspace, so it holds a copy; a level
    # the sandbox and the web commands resolved differently would be a restriction with a hole.
    assert WEB_COPY_RS.read_bytes() == LEAF_RS.read_bytes()


def test_only_on_is_described_as_having_no_network():
    # PUFFIN_EGRESS: nothing may call `off` or `duckduckgo` air-gapped.
    text = LAUNCHER_RS.read_text()
    duckduckgo = re.search(r'pub const DUCKDUCKGO_TEXT: &str = "([^"]+)";', text).group(1)
    assert "no network" not in duckduckgo and "air-gapped" not in duckduckgo
    assert "a preference, not a barrier" in text


# -- the resolver for whatever acts on the level in Python (spec §7) -------------------------------

def _two_files(tmp_path, monkeypatch, repo_text, user_text):
    """A working directory and a home, each with (or without) a configuration file."""
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    monkeypatch.delenv("DREAMFERENCE_CONFIG_PATH", raising=False)
    home, cwd = tmp_path / "home", tmp_path / "repo"
    (home / ".config" / "dreamference").mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    if repo_text is not None:
        (cwd / "dreamference.toml").write_text(repo_text)
    if user_text is not None:
        (home / ".config" / "dreamference" / "config.toml").write_text(user_text)
    return cwd


def test_between_the_two_files_the_stricter_wins_as_on_the_rust_side(tmp_path, monkeypatch):
    # The cases of `between_the_two_files_the_strictest_wins` in puffin-rs/airgapped/src/lib.rs.
    resolve = DreamferenceConfig.resolve_airgapped_level
    # A repository file the agent can write does not loosen the user's level.
    cwd = _two_files(tmp_path / "a", monkeypatch, 'puffin_airgapped = "off"\n', 'puffin_airgapped = "on"\n')
    assert resolve(cwd) == "on"
    # It may tighten it.
    cwd = _two_files(tmp_path / "b", monkeypatch, 'puffin_airgapped = "on"\n', 'puffin_airgapped = "ddg"\n')
    assert resolve(cwd) == "on"
    # A file without the key does not count, and neither does a missing file.
    cwd = _two_files(tmp_path / "c", monkeypatch, 'model = "x"\n', 'puffin_airgapped = "duckduckgo"\n')
    assert resolve(cwd) == "duckduckgo"
    cwd = _two_files(tmp_path / "d", monkeypatch, None, None)
    assert resolve(cwd) == "off"


def test_the_environment_decides_before_the_files_and_an_invalid_value_falls_through(tmp_path, monkeypatch):
    cwd = _two_files(tmp_path, monkeypatch, 'puffin_airgapped = "on"\n', 'puffin_airgapped = "on"\n')
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "off")
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "off"
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "sealed")
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "on"


def test_a_named_config_file_replaces_the_working_directorys(tmp_path, monkeypatch):
    cwd = _two_files(tmp_path, monkeypatch, 'puffin_airgapped = "on"\n', None)
    named = tmp_path / "named.toml"
    named.write_text('puffin_airgapped = "duckduckgo"\n')
    monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", str(named))
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "duckduckgo"


# -- the MCP server's web tools follow the level ---------------------------------------------------

def test_at_on_the_mcp_web_tools_send_nothing(monkeypatch):
    from unittest.mock import patch
    from dreamference.mcp_server.web_tools import AIRGAPPED_ON_MESSAGE, WebTools

    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "on")
    with patch("dreamference.mcp_server.web_tools.requests.get") as get:
        found = WebTools.search("python asyncio")
        page = WebTools.fetch("https://example.com")
    get.assert_not_called()
    assert found == {"query": "python asyncio", "airgapped": "on", "error": AIRGAPPED_ON_MESSAGE}
    assert page == {"url": "https://example.com", "airgapped": "on", "error": AIRGAPPED_ON_MESSAGE}
    # An IDE has no slash command: the message names the setting, not `/airgapped`.
    assert "/airgapped" not in AIRGAPPED_ON_MESSAGE and "puffin_airgapped = on" in AIRGAPPED_ON_MESSAGE


def test_at_duckduckgo_the_mcp_search_names_that_engine_and_no_category(monkeypatch):
    from unittest.mock import MagicMock, patch
    from dreamference.mcp_server.web_tools import WebTools

    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "duckduckgo")
    response = MagicMock()
    response.json.return_value = {"results": [{"title": "t", "url": "u", "content": "c", "engine": "duckduckgo"}]}
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=response) as get:
        assert WebTools.search("python asyncio")["result_count"] == 1
    params = get.call_args.kwargs["params"]
    # With `categories` beside `engines`, SearXNG asked all five general engines (measured).
    assert params["engines"] == "duckduckgo" and "categories" not in params

    # DuckDuckGo not answering is the level working as chosen: no hint to restart a container.
    response.json.return_value = {"results": [], "answers": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=response):
        outcome = WebTools.search("python asyncio")
    assert outcome["error"].startswith("DuckDuckGo did not answer (duckduckgo: CAPTCHA).")
    assert "hint" not in outcome and outcome["airgapped"] == "duckduckgo"
    # Fetching is not restricted at this level.
    page = MagicMock(headers={"Content-Type": "text/plain"}, encoding="utf-8", url="https://example.com", status_code=200)
    page.iter_content.return_value = [b"hello"]
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=page):
        assert WebTools.fetch("https://example.com")["text"] == "hello"


def test_at_off_the_mcp_search_asks_the_category_as_before(monkeypatch):
    from unittest.mock import MagicMock, patch
    from dreamference.mcp_server.web_tools import WebTools

    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "off")
    response = MagicMock()
    response.json.return_value = {"results": []}
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=response) as get:
        WebTools.search("python asyncio")
    params = get.call_args.kwargs["params"]
    assert params["categories"] == "general" and "engines" not in params
