# The Claude Code delegation plugin and session monitor

Specification: `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md`.

Two leaf crates under `ling-rs/` back the two halves of the design:

- **Part A (`ling-rs/delegate/`, `ling delegate …`)**: the Claude Code delegation plugin. Phase P1 builds the two model-free classes: `W1` (`watch`, turned on by the owner on 2026-10-27) and `L1` (`lookup`). Model-backed classes (`ask`, `task`, `night`) remain disabled.
- **Part B (`ling-rs/monitor/`, `ling monitor …`)**: the deterministic session monitor over local transcripts (Phase M0).

Both crates depend only on `chrono`, `serde`, `serde_json`, `toml` (and `regex-lite` in `ling-delegate`), so their unit tests run standalone in a copy of the crate folder (`cargo test`) without compiling the Codex workspace.

## The `ling-delegate` binary

On Linux, `ling-admin codex build` builds `ling-delegate` in the same Cargo run as `ling` (`-p ling-delegate --bin ling-delegate`) and installs it beside `ling` in `~/.local/share/dreamference/mightling/bin/`. It **is** linked into `~/.local/bin` (`link_onto_path`, `install.sh`), because the plugin starts `ling-delegate mcp` and must not depend on the `ling` version: the benchmark harness replaces the installed `ling` nightly with a pinned older build that has no `delegate` command. A build is not current without it (`is_current`). The release carries `ling-delegate-<target>.gz` (Linux only, optional in `install.sh`, which also installs and links it; listed in `scripts/package_mightling.sh`, `release-build.yml` and the fleet bundle). A release install has no source and no build: it gets the asset.

## Installing the plugin into Claude Code from a checkout

Nothing installs or enables the plugin for you; the owner of the Claude Code does it. The plugin lives in `claude-plugin/`, with a repository-root marketplace manifest `.claude-plugin/marketplace.json` (marketplace `mightling`, plugin `mightling`, source `./claude-plugin`):

```
.claude-plugin/marketplace.json
claude-plugin/
  .claude-plugin/plugin.json
  .mcp.json
  agents/delegate.md
  skills/delegate/SKILL.md
```

Prerequisite: `ling-delegate` on `PATH` (`~/.local/bin/ling-delegate`, linked by `ling-admin codex build` and `install.sh`; `.mcp.json` runs `ling-delegate mcp` over stdio, so the plugin does not depend on the installed `ling`).

Persistent install (the marketplace path), from the checkout's root:

```bash
claude plugin marketplace add /path/to/checkout     # or, inside Claude Code: /plugin marketplace add /path/to/checkout
claude plugin install mightling@mightling           # or: /plugin install mightling@mightling
```

Claude Code asks for the plugin's settings (`node`, `max_parallel`, `allow_classes`) at enable time; the defaults are fine (`allow_classes` is `W1,L1`). Restart Claude Code or run `/reload-plugins`, then check `/plugin` and `/mcp` (server `mightling`, tools `lookup` and `watch`). To update after a pull: `claude plugin marketplace update mightling`, then `claude plugin update mightling@mightling`. To remove: `claude plugin uninstall mightling@mightling` and `claude plugin marketplace remove mightling`.

One session only, nothing persisted: `claude --plugin-dir /path/to/checkout/claude-plugin`.

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

## Using `lookup` (Class L1)

`lookup` answers one symbol question from `ling-code`'s index with **no model** (`ling-rs/delegate/src/lookup.rs`):

```bash
ling-delegate lookup def format_table
ling-delegate lookup refs SystemProbe --scope ling-rs/delegate
ling-delegate lookup outline ling-rs/delegate/src/classes.rs --limit 10
```

The closed list of operations is `def`, `refs`, `callers`, `impl`, `outline` (`name` is a file for `outline`); `scope` is a relative path prefix or glob, `repo` the repository (default: the current directory), `limit` 20 by default and 50 at most. The server runs `ling-code <op> --json` (a read of its own stores, 20 s limit, no socket), then reads every row back from the file inside the repository: a row whose file or line cannot be read, or that leaves the repository, is dropped and counted in `dropped`. The result `{op, query, scope, hits: [{path, start, end, quote, detail}], dropped, truncated, notes}` is bounded to about 6,000 characters; a quote is the definition's first three lines at most. A name or scope that starts with `-`, is absolute or climbs with `..` is refused before the index is asked. Zero hits is stated as a fact about the index only. The unit tests inject the index and files (`IndexBackend`), so they need no network and no `ling-code`.
