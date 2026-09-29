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


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_no_statsig_metrics_or_openai_plugin_sync(tmp_path):
    # A traced `puffin exec` on 2026-09-29, with no ChatGPT login anywhere, still contacted
    # ab.chatgpt.com (OTEL metrics to Statsig, on by default in release builds),
    # chatgpt.com/backend-api/plugins/featured, and github.com (`git ls-remote openai/plugins`).
    # Patch 0015 closes all three at the call sites.
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    rs = tmp_path / "src" / "codex-rs"
    otel = (rs / "otel" / "src" / "config.rs").read_text()
    assert "never export metrics to OpenAI's Statsig" in otel and "if cfg!(debug_assertions) {" not in otel
    manager = (rs / "core-plugins" / "src" / "manager.rs").read_text()
    assert "no startup sync of OpenAI's curated plugins" in manager
    featured = (rs / "core-plugins" / "src" / "remote_legacy.rs").read_text()
    assert 'no "featured plugins" request to chatgpt.com' in featured
    # The TUI fetched OpenAI's announcement tip from raw.githubusercontent.com on every start.
    tips = (rs / "tui" / "src" / "tooltips.rs").read_text()
    assert "no announcement fetch from raw.githubusercontent.com" in tips
