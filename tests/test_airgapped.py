"""`/airgapped` (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md): the Python side of the setting, and what
Python, the launcher and the web commands must agree on -- the default level, and one resolver."""

import json
import os
import re
from pathlib import Path

import pytest
from conftest import REAL_AIRGAPPED_BINARY

import dreamference.config.airgapped_resolver as resolver_mod
import dreamference.config.dreamference_config as cfg_mod
from dreamference.config import AirgappedResolver, DreamferenceConfig
from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

REPO = Path(__file__).resolve().parent.parent
LEAF_RS = REPO / "ling-rs" / "airgapped" / "src" / "lib.rs"
CASES = json.loads((REPO / "ling-rs" / "airgapped" / "cases.json").read_text())
LAUNCHER_RS = REPO / "ling-rs" / "src" / "airgapped.rs"
# The crates outside the Codex workspace that act on the level: each links the one reader.
LINKING_CRATES = ("ling-web-rs", "ling-docs-rs")


def test_the_level_resolves_through_the_tiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "tiers.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "off"

    cfg_file.write_text('mightling_airgapped: "on"\n')
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "on"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "OFF")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "off"
    assert (
        DreamferenceConfig(config_file=str(cfg_file), mightling_airgapped="On").mightling_airgapped
        == "on"
    )


def test_an_invalid_value_is_skipped_not_adopted(tmp_path, monkeypatch):
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text('mightling_airgapped: "on"\n')
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "sealed")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "on"
    assert (
        DreamferenceConfig(config_file=str(cfg_file), mightling_airgapped="max").mightling_airgapped
        == "on"
    )
    # `duckduckgo` was a level until 2026-10-03; a value left behind is invalid like any other.
    assert (
        DreamferenceConfig(
            config_file=str(cfg_file), mightling_airgapped="duckduckgo"
        ).mightling_airgapped
        == "on"
    )
    assert DreamferenceConfig.parse_airgapped_level("ddg") is None


def test_a_yaml_boolean_is_the_level_it_spells(tmp_path, monkeypatch):
    # YAML reads a bare `on` as true; taking that for "invalid" would silently leave the network on.
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "bool.yaml"
    cfg_file.write_text("mightling_airgapped: on\n")
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "on"


def test_save_config_writes_only_a_non_default_level(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    cfg_file = tmp_path / "save.yaml"
    cfg_file.write_text("vllm_host: http://localhost:8000\n")
    DreamferenceConfig(config_file=str(cfg_file)).save_config()
    assert "mightling_airgapped" not in cfg_file.read_text()

    config = DreamferenceConfig(config_file=str(cfg_file))
    config.mightling_airgapped = "on"
    config.save_config()
    assert DreamferenceConfig(config_file=str(cfg_file)).mightling_airgapped == "on"


def test_the_default_and_the_level_names_match_the_rust_side():
    text = LEAF_RS.read_text()
    match = re.search(r'pub const DEFAULT_MIGHTLING_AIRGAPPED: &str = "(\w+)";', text)
    assert match, "DEFAULT_MIGHTLING_AIRGAPPED not found in ling-rs/airgapped/src/lib.rs"
    assert match.group(1) == cfg_mod.DEFAULT_MIGHTLING_AIRGAPPED
    for name in cfg_mod.MIGHTLING_AIRGAPPED_LEVELS:
        assert f'=> "{name}"' in text, name


def test_the_web_and_docs_commands_link_the_one_reader():
    # ling-web-rs and ling-docs-rs are built on their own, outside the Codex workspace. They held
    # copies of the resolver (one byte-identical, one rewritten) until 2026-10-29; a level the
    # sandbox and a command resolved differently would be a restriction with a hole, so each now
    # links the crate itself and keeps no reader of its own.
    for crate in LINKING_CRATES:
        manifest = (REPO / crate / "Cargo.toml").read_text()
        assert re.search(
            r'^ling-airgapped = \{ path = "\.\./ling-rs/airgapped" \}', manifest, re.M
        ), crate
        assert not (REPO / crate / "src" / "airgapped.rs").exists(), crate
        assert "ling-airgapped" in (REPO / crate / "Cargo.lock").read_text(), crate
    for crate in LINKING_CRATES:
        sources = "\n".join(path.read_text() for path in (REPO / crate / "src").rglob("*.rs"))
        for reader in (
            "fn toml_value",
            "fn configured_on",
            "fn config_texts",
            "fn sealed_in",
            "fn seal_dir",
        ):
            assert reader not in sources, f"{crate} reads the setting itself ({reader})"


def test_only_on_is_described_as_having_no_network():
    # MIGHTLING_EGRESS: nothing may call `off` air-gapped.
    text = LAUNCHER_RS.read_text()
    off = re.search(r'pub const OFF_TEXT: &str = "([^"]+)";', text).group(1)
    assert "no network" not in off and "air-gapped" not in off
    assert "no network" in re.search(r'pub const ON_TEXT: &str = "([^"]+)";', text).group(1)


# -- the one reader as Python asks it (spec §6.5) --------------------------------------------------


def _answering(tmp_path, case) -> str:
    """A `ling` that answers as the case says: its output and exit status, never, or not at all."""
    if case.get("timeout"):
        text = "#!/bin/sh\nexec sleep 30\n"
    elif case["stdout"] is None:
        return str(tmp_path / "missing" / "ling")
    else:
        (tmp_path / "stdout").write_text(case["stdout"])
        text = f"#!/bin/sh\ncat '{tmp_path / 'stdout'}'\nexit {case['status']}\n"
    binary = tmp_path / "ling"
    binary.write_text(text)
    binary.chmod(0o755)
    return str(binary)


@pytest.mark.parametrize("case", CASES["answer"], ids=[case["name"] for case in CASES["answer"]])
def test_an_answer_is_off_only_when_it_is_well_formed_and_says_off(tmp_path, monkeypatch, case):
    # The shared cases: desktop/electron/src/airgapped.test.ts runs the same ones.
    monkeypatch.setattr(resolver_mod, "RESOLVE_TIMEOUT_SECONDS", 0.5)
    assert AirgappedResolver.level(binary=_answering(tmp_path, case)) == case["expect"]


def test_the_answer_cases_cover_every_way_of_failing():
    names = {case["name"] for case in CASES["answer"]}
    assert {
        "no binary is on",
        "a timeout is on",
        "a failed command is on",
        "an empty answer is on",
    } <= names
    assert {case["expect"] for case in CASES["answer"]} == {"off", "on"}


def test_the_one_asked_is_the_built_ling_never_one_on_path():
    assert (
        REAL_AIRGAPPED_BINARY.__func__(AirgappedResolver) == CodexBrandedBuilder.executable_path()
    )


def test_the_question_names_the_folder_the_session_and_the_parent(tmp_path):
    ask = AirgappedResolver.command
    assert ask("ling") == ["ling", "airgapped", "--json"]
    assert ask("ling", cwd=tmp_path, thread="abc-1", sealed_by=42) == [
        "ling",
        "airgapped",
        "--json",
        "--thread",
        "abc-1",
        "--cwd",
        str(tmp_path),
        "--sealed-by",
        "42",
    ]


def test_the_configured_level_is_the_one_reader_s_for_the_folder(tmp_path, monkeypatch):
    log = tmp_path / "asked"
    monkeypatch.setenv("FAKE_LING_AIRGAPPED_LOG", str(log))
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    assert DreamferenceConfig.resolve_airgapped_level(tmp_path) == "off"
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "sealed")
    assert DreamferenceConfig.resolve_airgapped_level() == "on"
    assert log.read_text().splitlines() == [
        f"airgapped --json --cwd {tmp_path}",
        "airgapped --json",
    ]


def test_without_an_answer_the_configured_level_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(
        AirgappedResolver, "binary", classmethod(lambda cls: str(tmp_path / "no-ling"))
    )
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "off")
    assert DreamferenceConfig.resolve_airgapped_level() == "on"


def test_python_reads_no_air_gap_file_itself():
    # The tiers are read in one place; a Python copy of them drifted from the crate twice.
    for path in (REPO / "dreamference").rglob("*.py"):
        text = path.read_text()
        assert "toml_airgapped_value" not in text and "_toml_top_level" not in text, path
        assert "SEAL_DIR" not in text and '"ling-airgapped"' not in text, path


def _two_files(tmp_path, monkeypatch, repo_text, user_text):
    """A working directory and a home, each with (or without) a configuration file."""
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
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


def test_a_named_config_file_replaces_the_working_directorys(tmp_path, monkeypatch):
    cwd = _two_files(tmp_path, monkeypatch, 'mightling_airgapped = "on"\n', None)
    named = tmp_path / "named.toml"
    named.write_text('mightling_airgapped = "off"\n')
    monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", str(named))
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "off"


def test_a_config_path_that_is_not_a_file_is_passed_over_without_a_word(
    tmp_path, capsys, monkeypatch
):
    # The MCP server asks here and speaks JSON-RPC on stdout, so a warning printed for a directory
    # would break its stream; a FIFO in that place would block the read until the timeout (`on`).
    # The crate reads a directory there as unreadable, so `on` (its `cases.rs`).
    cwd = _two_files(tmp_path, monkeypatch, None, 'mightling_airgapped = "on"\n')
    (cwd / "dreamference.toml").mkdir()
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "on"
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
def test_a_config_file_that_cannot_be_read_counts_as_absent_as_on_the_rust_side(tmp_path, monkeypatch):
    # ling-rs/airgapped skips a file `read_to_string` refuses; the user's level still holds.
    cwd = _two_files(tmp_path / "a", monkeypatch, 'mightling_airgapped = "off"\n', 'mightling_airgapped = "on"\n')
    (cwd / "dreamference.toml").chmod(0)
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "on"
    cwd = _two_files(tmp_path / "b", monkeypatch, 'mightling_airgapped = "on"\n', None)
    (cwd / "dreamference.toml").chmod(0)
    assert DreamferenceConfig.resolve_airgapped_level(cwd) == "off"


# -- the MCP server's web tools follow the level ---------------------------------------------------


def test_at_on_the_mcp_web_tools_send_nothing(monkeypatch):
    from unittest.mock import patch

    from dreamference.mcp_server.web_tools import AIRGAPPED_ON_MESSAGE, WebTools

    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "on")
    with patch("dreamference.mcp_server.web_session.WebSession.get") as get:
        found = WebTools.search("python asyncio")
        page = WebTools.fetch("https://example.com")
    get.assert_not_called()
    assert found == {"query": "python asyncio", "airgapped": "on", "error": AIRGAPPED_ON_MESSAGE}
    assert page == {"url": "https://example.com", "airgapped": "on", "error": AIRGAPPED_ON_MESSAGE}
    # An IDE has no slash command: the message names the setting, not `/airgapped`.
    assert (
        "/airgapped" not in AIRGAPPED_ON_MESSAGE
        and "mightling_airgapped = on" in AIRGAPPED_ON_MESSAGE
    )


def test_a_leftover_duckduckgo_level_counts_as_on(monkeypatch):
    from unittest.mock import patch

    from dreamference.mcp_server.web_tools import WebTools

    # `duckduckgo` was a level until 2026-10-03 (DuckDuckGo answered SearXNG with a CAPTCHA). A
    # value left behind was ignored until the one reader (§6.5): a value that is not a level now
    # counts as `on`, so nothing is sent.
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "duckduckgo")
    with patch("dreamference.mcp_server.web_session.WebSession.get") as get:
        assert WebTools.search("python asyncio")["airgapped"] == "on"
    get.assert_not_called()


def test_at_off_the_mcp_search_asks_the_category_as_before(monkeypatch):
    from unittest.mock import MagicMock, patch

    from dreamference.mcp_server.web_tools import WebTools

    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "off")
    response = MagicMock()
    response.json.return_value = {"results": []}
    with patch("dreamference.mcp_server.web_session.WebSession.get", return_value=response) as get:
        WebTools.search("python asyncio")
    params = get.call_args.kwargs["params"]
    assert params["categories"] == "general" and "engines" not in params


def test_the_mcp_web_tools_resolve_the_level_on_every_call(monkeypatch):
    from unittest.mock import MagicMock, patch

    from dreamference.mcp_server.web_tools import AIRGAPPED_ON_MESSAGE, WebTools

    # The server is long-lived: a level set after it started applies to its next call (§7).
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "off")
    response = MagicMock()
    response.json.return_value = {"results": []}
    with patch("dreamference.mcp_server.web_session.WebSession.get", return_value=response) as get:
        WebTools.search("python asyncio")
        monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "on")
        found = WebTools.search("python asyncio")
        page = WebTools.fetch("https://example.com")
    assert get.call_count == 1
    assert found["error"] == AIRGAPPED_ON_MESSAGE and page["error"] == AIRGAPPED_ON_MESSAGE


def test_the_slash_command_sits_after_permissions_with_its_description():
    # §6.4: both set what the agent may reach, so the popup lists them together.
    patch = (REPO / "codex-patches" / "0019-airgapped.patch").read_text()
    slash = patch.split("+++ b/codex-rs/tui/src/slash_command.rs\n", 1)[1].split(
        "\ndiff --git ", 1
    )[0]
    assert "     Permissions,\n+    Airgapped,\n" in slash
    assert (
        '+            SlashCommand::Airgapped => "set how much of the internet this session may use",'
        in slash
    )
