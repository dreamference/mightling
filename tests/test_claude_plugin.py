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
    assert manifest["userConfig"]["allow_classes"]["default"] == "W1,L1,task"


def test_the_mcp_server_is_ling_delegate_mcp() -> None:
    """`.mcp.json` declares one stdio server, `mightling`, running `ling-delegate mcp`, not the `ling` on PATH."""
    servers = _json(PLUGIN / ".mcp.json")["mcpServers"]
    assert list(servers) == ["mightling"]
    assert servers["mightling"]["command"] == "ling-delegate"
    assert servers["mightling"]["args"] == ["mcp"]


def test_the_subagent_reaches_only_the_plugin_tools_and_read() -> None:
    """`agents/delegate.md` lists the plugin's own tools and `Read`: no shell, no editor, no `task_verdict`."""
    text = (PLUGIN / "agents" / "delegate.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    tools = [line.strip()[2:] for line in front.splitlines() if line.strip().startswith("- ")]
    prefix = "mcp__plugin_mightling_mightling__"
    assert tools == [
        prefix + t for t in ("watch", "task", "lookup", "run_test", "git_log", "diff_stat")
    ] + ["Read"]
    assert "Bash" not in tools and "Edit" not in tools and "Write" not in tools


def test_the_skill_hands_bounded_edits_to_task_with_its_rules() -> None:
    """The skill names `task`, its classes, the review step and the verdict that feeds the ledger."""
    text = (PLUGIN / "skills" / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "mcp__plugin_mightling_mightling__<tool>" in text
    for tool in ("run_test", "git_log", "diff_stat", "task_verdict"):
        assert f"`{tool}`" in text
    for cls in ("T1", "T2", "T3", "T4", "T5"):
        assert f"| `{cls}` |" in text
    for rule in ("never commits", "do the task yourself at once", "clean linked worktree", "95%"):
        assert rule in text


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


def test_the_diff_reviewer_reads_and_nothing_else() -> None:
    """`agents/diff-reviewer.md` can only `Read` and answers `ACCEPT` or `REJECT: <one line>`."""
    text = (PLUGIN / "agents" / "diff-reviewer.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    tools = [line.strip()[2:] for line in front.splitlines() if line.strip().startswith("- ")]
    assert tools == ["Read"]
    assert "`ACCEPT`" in text and "`REJECT: <one line" in text


def test_the_user_config_reaches_the_server_as_its_environment() -> None:
    """The three settings reach `ling-delegate` as `DREAMFERENCE_DELEGATE_*`, defaulting to "" and 2."""
    env = _json(PLUGIN / ".mcp.json")["mcpServers"]["mightling"]["env"]
    assert env == {
        "DREAMFERENCE_DELEGATE_NODE": "${user_config.node}",
        "DREAMFERENCE_DELEGATE_MAX_PARALLEL": "${user_config.max_parallel}",
        "DREAMFERENCE_DELEGATE_ALLOW_CLASSES": "${user_config.allow_classes}",
    }
    config = _json(PLUGIN / ".claude-plugin" / "plugin.json")["userConfig"]
    assert config["node"]["default"] == ""
    assert config["max_parallel"]["default"] == 2


def test_the_skill_never_retries_and_reads_zero_hits_and_notes_as_hints() -> None:
    """A failed `task` is done by Claude without a retry; `agent.note` and zero hits are not facts."""
    text = (PLUGIN / "skills" / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "no retry, no second delegation of the same task" in text
    assert "Treat `agent.note` as a hint, never a fact." in text
    assert "Zero hits is a fact about the index, not about the concept" in text
