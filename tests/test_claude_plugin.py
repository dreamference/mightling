"""The Claude Code plugin's packaging (specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md §4.1, §14).

The plugin is files only: these tests read them. They check the manifest and the marketplace
against the package version, that the MCP server is `ling delegate mcp`, that the subagent can
reach nothing but `watch`, and that nothing in the repository enables the plugin by itself.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Final

from dreamference import __version__

REPO: Final[Path] = Path(__file__).resolve().parent.parent
PLUGIN: Final[Path] = REPO / "claude-plugin"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_and_marketplace_name_mightling_at_the_package_version() -> None:
    """The plugin is `mightling`, from the marketplace `mightling`, at `dreamference.__version__`."""
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    marketplace = _json(REPO / ".claude-plugin" / "marketplace.json")
    assert manifest["name"] == "mightling"
    assert manifest["version"] == __version__
    assert marketplace["name"] == "mightling"
    assert [(p["name"], p["source"], p["version"]) for p in marketplace["plugins"]] == [
        ("mightling", "./claude-plugin", __version__)
    ]
    assert set(manifest["userConfig"]) == {"node", "max_parallel", "allow_classes"}
    assert manifest["userConfig"]["allow_classes"]["default"] == "W1,L1"


def test_the_mcp_server_is_ling_delegate_mcp() -> None:
    """`.mcp.json` declares one stdio server, `mightling`, running `ling-delegate mcp`, not the `ling` on PATH."""
    servers = _json(PLUGIN / ".mcp.json")["mcpServers"]
    assert list(servers) == ["mightling"]
    assert servers["mightling"]["command"] == "ling-delegate"
    assert servers["mightling"]["args"] == ["mcp"]


def test_the_subagent_reaches_only_watch_and_read() -> None:
    """`agents/delegate.md` lists the `watch` tool and `Read`, nothing else."""
    text = (PLUGIN / "agents" / "delegate.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    tools = [line.strip()[2:] for line in front.splitlines() if line.strip().startswith("- ")]
    assert tools == ["mcp__plugin_mightling_mightling__watch", "Read"]


def test_the_skill_sends_every_long_wait_to_watch() -> None:
    """The skill tells Claude to use `watch` for every wait longer than a minute."""
    text = (PLUGIN / "skills" / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "every wait longer than a minute" in text


def test_the_skill_sends_single_symbol_questions_to_lookup_first() -> None:
    """The skill names `lookup`, its closed op list and the model-free default classes."""
    text = (PLUGIN / "skills" / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "mcp__plugin_mightling_mightling__lookup" in text
    assert "before you read files" in text
    for op in ("def", "refs", "callers", "impl", "outline"):
        assert f"| `{op}` |" in text


def test_nothing_in_the_repository_enables_the_plugin() -> None:
    """No tracked settings file enables a plugin and no `.mcp.json` sits at the root.

    A root `.mcp.json` would start the server in every Claude Code session opened here, and
    `enabledPlugins` in a committed settings file would turn the plugin on for every clone.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    if not tracked:
        return
    assert ".mcp.json" not in tracked
    for name in tracked:
        if name.endswith(".json") and (name.startswith(".claude/") or "/.claude/" in name):
            assert "enabledPlugins" not in (REPO / name).read_text(encoding="utf-8"), name
