# The Claude Code delegation plugin and session monitor

Specification: `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md`.

Two leaf crates under `ling-rs/` back the two halves of the design:

- **Part A (`ling-rs/delegate/`, `ling delegate …`)**: the Claude Code delegation plugin. Phase P1 builds class `W1` (`watch`) only, the class the owner turned on on 2026-10-27. Model-backed classes (`ask`, `lookup`, `task`, `night`) remain disabled.
- **Part B (`ling-rs/monitor/`, `ling monitor …`)**: the deterministic session monitor over local transcripts (Phase M0).

Both crates depend only on `chrono`, `serde`, `serde_json`, `toml` (and `regex-lite` in `ling-delegate`), so their unit tests run standalone in a copy of the crate folder (`cargo test`) without compiling the Codex workspace.

## Installing the plugin locally for Claude Code

The plugin lives in `claude-plugin/` with a repository-root marketplace manifest at `.claude-plugin/marketplace.json`:

```
.claude-plugin/marketplace.json
claude-plugin/
  .claude-plugin/plugin.json
  .mcp.json
  agents/delegate.md
  skills/delegate/SKILL.md
```

To load or install the plugin locally in Claude Code:

```bash
# Option 1: load directly for a session from a local checkout
claude --plugin-dir ./claude-plugin

# Option 2: install from the local checkout's marketplace manifest inside Claude Code
/plugin marketplace add ./
/plugin install mightling@mightling
```

`ling` must be on `PATH` (`~/.local/bin/ling`); `.mcp.json` starts `ling delegate mcp` over stdio.

## Using `watch` (Class W1)

`watch` blocks on one condition from a closed list with **no model** and returns `{met, observed, at}`:

```bash
# CLI usage
ling delegate watch --kind gh_run --run 123456789 --repo dreamference/mightling
ling delegate watch --kind file_line --path /tmp/train.log --pattern "Uvicorn running"
ling delegate watch --kind pid_exit --pid 4242 --timeout 600
ling delegate watch --kind systemd_unit --unit mightling-night.service --state active
ling delegate watch --kind http_status --url http://127.0.0.1:8000/health --status 200

# Inspect class statuses
ling delegate classes
```

Closed condition kinds: `file_line`, `pid_exit`, `http_status`, `gh_run`, `systemd_unit`, `command`, `night_task`, `git_ref`, `timeout`. Default timeout is 1,800 s (30 min), hard maximum is 14,400 s (4 h), and subprocess polls (`gh_run`, `systemd_unit`, `command`, `git_ref`) enforce a 15 s minimum interval.
