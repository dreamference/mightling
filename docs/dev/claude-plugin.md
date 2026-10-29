# The Claude Code delegation plugin and session monitor

Specification: `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md`.

Two leaf crates under `ling-rs/` back the two halves of the design:

- **Part A (`ling-rs/delegate/`, `ling delegate …`)**: the Claude Code delegation plugin. Phase P1 builds the model-free classes `W1` (`watch`, turned on by the owner on 2026-10-27) and `L1` (`lookup`, and from 2026-10-28 the exact tools `run_test`, `git_log` and `diff_stat`), and, by the owner's decision of 2026-10-28 to move more work to the local model, `task` for T1 to T5, on probation. `ask` and `night` are not built.
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
  agents/diff-reviewer.md
  skills/delegate/SKILL.md
```

Prerequisite: `ling-delegate` on `PATH` (`~/.local/bin/ling-delegate`, linked by `ling-admin codex build` and `install.sh`; `.mcp.json` runs `ling-delegate mcp` over stdio, so the plugin does not depend on the installed `ling`).

Persistent install (the marketplace path), from the checkout's root:

```bash
claude plugin marketplace add /path/to/checkout     # or, inside Claude Code: /plugin marketplace add /path/to/checkout
claude plugin install mightling@mightling           # or: /plugin install mightling@mightling
```

Claude Code asks for the plugin's settings (`node`, `max_parallel`, `allow_classes`) at enable time; the defaults are fine (`allow_classes` is `W1,L1,task`; drop `task` to switch the model-backed tool off, or name single classes such as `T1,T2`). Restart Claude Code or run `/reload-plugins`, then check `/plugin` and `/mcp` (server `mightling`, tools `lookup`, `run_test`, `git_log`, `diff_stat`, `watch`, `task` and `task_verdict`). To update after a pull: `claude plugin marketplace update mightling`, then `claude plugin update mightling@mightling`. To remove: `claude plugin uninstall mightling@mightling` and `claude plugin marketplace remove mightling`.

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

## The exact tools: `run_test`, `git_log`, `diff_stat`

Same discipline as `lookup` (`ling-rs/delegate/src/tools.rs`): a closed list, every argument checked before a process starts, a timeout, bounded output, and a read-back where one exists. All process work goes through the `Runner` trait (`exec.rs`), so the unit tests script `git`, the runners, `ruff`, `systemctl` and `ling exec` and need no network, toolchain or model.

```bash
ling-delegate run_test pytest tests/test_x.py::test_y --worktree ../wt          # runner: pytest | cargo | npm
ling-delegate run_test cargo lookup::tests --worktree ../wt --cwd ling-rs/delegate --package ling-delegate
ling-delegate git_log ling-rs/delegate/src/lookup.rs --start 190 --end 198 --limit 5
ling-delegate diff_stat --repo ../wt
```

- `run_test`: one test, a **linked worktree only** (a `.git` file; never the main checkout), a target of one token with no leading `-`, `cwd` relative inside the worktree. The test runs inside `ling sandbox -P :workspace --sandbox-state-disable-network -C <worktree>` (the worktree and the temp folder writable, the rest read-only, no network; cargo offline): a worktree's tests, `conftest.py` and build scripts may be the model's code. A sandbox that does not start (no `ling`, or bwrap without the AppArmor profile from `ling-admin host setup`) refuses the call; nothing falls back to the host. Python is the main checkout's `.venv`, else `python3` on `PATH`, never the worktree's own `.venv`. Timeout 600 s (at most 1,800), tail 40 lines (at most 120, 6,000 characters), `nice 10` in `mightling-batch.slice` when `systemd-run --user` works. Result `{passed, exit_code, timed_out, seconds, summary, tail}`.
- `git_log`: `git log -- path`, or `-L start,end:path -s` for a function's lines (take them from `lookup`); the range is checked against the file's current length, malformed rows are dropped and counted, at most 30 commits.
- `diff_stat`: `git status` joined with `git diff --numstat HEAD`; untracked files by name, at most 100 files.

`run_test`, `git_log`, `diff_stat` follow `L1` in `DREAMFERENCE_DELEGATE_ALLOW_CLASSES` (or are named singly). In the MCP server `watch`, `task` and `run_test` calls run on their own threads, so a long wait or task does not block `lookup`.

## `task`: plan, execute, test (classes T1 to T5, on probation)

`ling-rs/delegate/src/task.rs`. The caller gives `class`, a **clean linked worktree**, `allowed_files` (paths, or folders ending in `/`), `steps` and ONE `acceptance` `{runner, target, cwd?, package?}`. The server then:

1. refuses (and logs the refusal, which does not count as a delegation) while a `ling-swe-*` unit is active (user or system), when the tree is dirty, when the sandbox does not start, when `max_parallel` tasks are running (pid files under `$CODEX_HOME/claude-plugin/running/`), or on a bad spec; and when `allowed_files` touches a protected path or names a lockfile (a folder is checked by every file git tracks under it). The protected paths (spec §6.1) are a built-in list in `ling-rs/delegate/src/exclude.rs` (sandboxing and the Codex patches, `/airgapped`, the egress allowlists and audit, keys, credentials, pairing and signing, the installers, permission and approval logic, the delegation policy itself and `.github/workflows`) plus `[claude_plugin] exclude` (a list of globs) in the repository's `dreamference.toml` and in `~/.config/dreamference/config.toml`; the config only adds (an entry starting with `!` is ignored), and a config that cannot be read refuses the task;
2. runs `ling exec -C <worktree> -s workspace-write --skip-git-repo-check -o <last message> <prompt>` in `mightling-batch.slice` at `nice 10`, with the plugin's `node` passed as `MIGHTLING_NODE` when set (unset, `ling` finds the model server through the launcher's tiers) (override the binary with `DREAMFERENCE_DELEGATE_LING`); 1,500 s by default, at most 3,600 s;
3. looks at the tree itself: a moved `HEAD` is undone (`git reset --mixed`) and fails the task (`committed`); a change to a protected path, whatever the scope said, rejects the whole diff (`excluded`), and so does a change to a lockfile or requirements file or to the dependency sections of a `Cargo.toml`, `package.json`, `pyproject.toml` or `setup.py`, compared parsed against `HEAD` (`dependencies`): every change is reverted and the note names the rule; a change outside `allowed_files`, or in a test file outside T2, is reverted and fails it (`scope`, `test_files`); T2 may not remove more assertions than it adds or add a skip, xfail or ignore (`weakened_tests`, a heuristic); the size limit (400 changed lines for T1 and T3, 150 for the others, `too_large`); no change at all is `empty`; a new file hidden by a `.gitignore` rule (so absent from the status and the diff), other than build output (`__pycache__`, `target`, `node_modules`, caches), is removed and fails it (`scope`);
4. runs the acceptance test (`acceptance`) and, for touched Python, `ruff format --check` and `ruff check` (`gate`), all inside the sandbox of `run_test`;
5. returns `{id, pass, reason, files, reverted, changed_lines, diff (20,000 characters at most: a larger diff fails the task as `too_large`, is not returned and is saved as `diff.patch` in the task's folder under `$CODEX_HOME/claude-plugin/tasks/`), acceptance, gates, agent, notes}`. It **never commits** and never applies the diff; new files are made visible with `git add -N`.

Probation ledger: `$CODEX_HOME/claude-plugin/ledger.jsonl` (default `~/.mightling`), one `task` record per task (class, hashed repository, status, reason, file and line counts, seconds; no brief, code or answer) and one `verdict` record per `task_verdict` call (`accept`, `reject`, `reverted`, `shadow_miss`; only for a task that passed the server's checks, the latest verdict counts). `ling-delegate ledger [--json]` prints per class the tasks, accepted, pending, rejected by verification and by review, reverts, the acceptance rate over decided tasks and `gate`: met at 20 decided tasks, at least 95% accepted, no revert and no shadow miss. The gate is reported, not enforced: promotion is the owner's command (spec 6.3). `ling-delegate task --json spec.json` runs a task from a shell.

The Rust tests run in a private export of the crate folder (`cargo test`), never in `codex/`.
