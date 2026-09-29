"""Puffin never sends Codex's usage analytics to OpenAI.

Codex's analytics client is on unless config.toml says `[analytics] enabled = false`, and it sends
whenever a ChatGPT login is present in CODEX_HOME. Puffin shares `~/.codex` with any upstream Codex
install, so a leftover ChatGPT login was enough to send usage events to
`chatgpt.com/backend-api/codex/analytics-events/events` from local-model sessions. Patch 0013 turns
the client off where it is constructed, so neither the config nor the login can turn it back on.
"""

import os

import pytest

from dreamference.runner.codex_branded_builder import CODEX_SUBMODULE_DIR, CodexBrandedBuilder

SUBMODULE_PRESENT = os.path.isdir(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs"))


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_analytics_client_is_never_enabled(tmp_path):
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    client = (tmp_path / "src" / "codex-rs" / "analytics" / "src" / "client.rs").read_text()
    # The single place AnalyticsEventsClient builds its sending queue; both the TUI/exec session
    # and the app-server construct the client through it.
    assert "queue: (false && analytics_enabled != Some(false))" in client
    assert "queue: (analytics_enabled != Some(false))" not in client


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_puffin_uses_its_own_home_before_codex_reads_one(tmp_path):
    # Patch 0014: the first statement of Codex's main() switches CODEX_HOME to ~/.puffin, before
    # arg0 loads `.env` from the home folder, so upstream's ~/.codex -- and its ChatGPT login --
    # is never read. puffin-rs/src/home.rs carries session history over without auth.json.
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    main = (tmp_path / "src" / "codex-rs" / "cli" / "src" / "main.rs").read_text()
    start = main.index("fn main() -> anyhow::Result<()> {")
    body = main[start:start + 400]
    assert body.index("puffin_launcher::home::use_puffin_home();") < body.index("arg0_dispatch_or_else")


def test_the_python_side_reads_the_same_home(monkeypatch):
    from dreamference.runner.codex_installer import CodexInstaller

    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert CodexInstaller.home_dir() == os.path.expanduser("~/.puffin")
    monkeypatch.setenv("CODEX_HOME", "/elsewhere")
    assert CodexInstaller.home_dir() == "/elsewhere"
