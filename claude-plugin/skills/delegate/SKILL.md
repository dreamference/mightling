---
name: delegate
description: Use Mightling's model-free tools before reading files or running suites (`lookup` for a symbol, `git_log` for why code is as it is, `diff_stat` for the size of a change, `run_test` for one named test), hand bounded edits that have one clear test to `task` (the local model), and use `watch` (`mcp__plugin_mightling_mightling__watch` or `ling delegate watch`) for every wait longer than a minute instead of `sleep` loops or repeated polling turns.
---

# Mightling tools: `lookup`, `git_log`, `diff_stat`, `run_test` (L1), `task` (T1 to T5), `watch` (W1)

All tool names are `mcp__plugin_mightling_mightling__<tool>`. Local work is free in Claude tokens: prefer it whenever a machine can check the result.

## Exact tools first, then files

| Instead of | Call | Returns |
| --- | --- | --- |
| grepping or opening files for one symbol | `lookup` (below) | definitions and references with verified quotes |
| `git log -p` or `git blame` to learn why code is as it is | `git_log` with `path`, and `start`/`end` (from `lookup`) for one function | `{hash, author, date, subject}` of up to 30 commits |
| `git diff` to see how big a change is | `diff_stat` with `repo` (a worktree root) | per-file `{status, added, deleted}`, totals, untracked names |
| running a suite and reading its log | `run_test` with `runner` (`pytest`, `cargo`, `npm`), `target` (one node id, test name or pattern), `worktree` | `{passed, exit_code, summary, tail}`: the last 40 lines, at most 120 |

`run_test` and `diff_stat` need a git worktree root; `run_test` accepts only a **linked worktree** (`git worktree add`), never the main checkout, and a target is one token that never starts with `-`. It runs at nice 10 with a timeout (default 600 s). These tools use **no model**; a refusal names the rule that was broken.

## `task`: hand a bounded edit with a clear test to the local model

Use `task` when the edit is bounded and a command can say it is done. Do not use it for design decisions, security-sensitive code, releases or anything without a test.

| Class | Use for | Size limit |
| --- | --- | --- |
| `T1` | lint, typing and format fixes | 400 changed lines |
| `T2` | test-only CI fixes (assertions may not be removed or weakened; no skip, xfail or ignore) | 150 |
| `T3` | mechanical refactors with existing tests (rename, move, signature); no test file touched | 400 |
| `T4` | docs updates checked against the code | 150 |
| `T5` | small UI items with an e2e test | 150 |

Spec: `class`, `worktree` (a **clean linked worktree**: commit or stash first, so the diff is only the model's), `allowed_files` (exact relative paths, or folders ending in `/`), `steps` (concrete, in order), and ONE `acceptance` command `{runner, target, cwd?, package?}`. The server runs the local `ling exec` in that worktree, **reverts and fails** any change outside `allowed_files` (and any test file outside T2), undoes a commit, applies the size limit, runs the acceptance test and, for touched Python, `ruff format --check` and `ruff check`. It **never commits** and never applies anything. It refuses while a benchmark runs (`busy`, a dirty tree and a bad spec are refusals too).

1. Call `task` from a background subagent (`mightling:delegate`, `run_in_background: true`): it takes 5 to 30 minutes.
2. The result is `{id, pass, reason, files, changed_lines, diff, acceptance, gates, agent}`. On `pass: false`, or any refusal, **do the task yourself at once**: no retry, no second delegation of the same task. Treat `agent.note` as a hint, never a fact.
3. On `pass: true`, give a reviewer (`mightling:diff-reviewer`, which can only `Read`) only the brief and the diff, and ask for `ACCEPT` or `REJECT: <one line>`: did the diff do what the brief asked, and only that? Look for a missed second code path and for behaviour nobody asked for.
4. Apply an accepted diff yourself (it sits uncommitted in the worktree), then call `task_verdict` with the `id` and `accept` or `reject`. Later, if the change is undone, `reverted`; if your own result would have differed in effect, `shadow_miss`. The ledger (`ling-delegate ledger`) gates each class on 95% accepted over at least 20 decided tasks; a revert or a shadow miss blocks it.

## `lookup`: ask the index before you read files

## `lookup`: ask the index before you read files

Before opening or grepping files to find one symbol, call `mcp__plugin_mightling_mightling__lookup` (or `ling delegate lookup <op> <name>` from a shell). It uses **no model** and answers from `ling-code`'s index in about a second, with every quote read back from the file:

| `op` | Answers | `name` |
| --- | --- | --- |
| `def` | where the name is defined | the name (`Type.name`, or `path:line`, when it is ambiguous) |
| `refs` | every reference to it | the name |
| `callers` | the definitions that call it | the name |
| `impl` | implementations of a trait, interface or method | the name |
| `outline` | the definitions of one file, with their first lines | the file, relative to the repository |

Optional `scope` (a relative path prefix or glob), `repo` (default: the current directory) and `limit` (default 20, at most 50). The result is `{hits: [{path, start, end, quote, detail}], dropped, truncated, notes}`, about 1,500 tokens at most. Use the `path:start` it returns to `Read` only the lines you need.

- Use `lookup` for **one symbol or file**; a question that needs three or more searches is not a `lookup`.
- Zero hits is a fact about the index, not about the concept: the index can be stale or heuristic (read `notes`). For a literal string, use `rg`.
- A global instruction that names another code-navigation tool wins over this one.

## `watch` (Class W1, Waiting and Monitoring)

Use `mcp__plugin_mightling_mightling__watch` (or `ling delegate watch` from a shell) for **every wait longer than a minute** instead of `sleep` loops, `while` polling loops, or repeated status-check turns.

`watch` runs plain local code with **no model**, blocks until one condition from its closed list is met (or `timeout_s` expires), and returns once with `{met, observed, at}`.

## When to Use `watch`

- Waiting for a training run, benchmark, build, or long test suite log to print a completion or error line (`file_line`).
- Waiting for a background process PID to exit (`pid_exit`).
- Waiting for a local model server or service on loopback (`127.0.0.1`, `localhost`, `::1`) to return HTTP 200 (`http_status`).
- Waiting for a GitHub Actions workflow run to finish (`gh_run`, via `gh run view <id> --json status,conclusion`).
- Waiting for a systemd user unit to become `active` or `inactive` (`systemd_unit`, via `systemctl --user is-active <unit>`).
- Waiting for an allowlisted status command (`git`, `gh run view`, `ling night show`, `ling-admin night status`, `systemctl --user is-active`, `curl` to loopback) to reach an exit code or output pattern (`command`).
- Waiting for a Night Shift task (`$CODEX_HOME/night/tasks/<id>.json`) to reach a terminal status (`night_task`).
- Waiting for a git ref to move (`git_ref`).
- Waiting for a fixed timer duration (`timeout`).

To keep a long wait off your current turn while you continue other work, spawn the `mightling:delegate` subagent with `run_in_background: true`.

## Closed List of Condition Kinds

| `kind` | Required / Optional Fields | `observed` on Match |
| --- | --- | --- |
| `file_line` | `path` (or `target`), `pattern`, optional `regex: true` | `<path>:<line_number>: <matching line verbatim>` |
| `pid_exit` | `pid` (or `target`) | `pid <pid> exited` |
| `http_status` | `url` (or `target`, loopback `http://`), optional `status` (default `200`) | `HTTP <status> from <url>` |
| `gh_run` | `run_id` (or `target`), optional `repo` (`owner/name`) | `run <id> completed: <conclusion>` |
| `systemd_unit` | `unit` (or `target`), optional `state` (default `"active"`) | `unit <unit>: <state>` |
| `command` | `command` (or `target`), optional `exit_code`, optional `pattern`, optional `regex` | `exit <code>` or `exit <code>: <matching line>` |
| `night_task` | `id` (or `target`) | `night <id>: <status>` |
| `git_ref` | `ref` (or `target`), optional `from`, optional `repo` | `<ref> moved from <old> to <new>` |
| `timeout` | `timeout_s` (or `target` in seconds) | `timeout elapsed (<n>s)` |

Any condition kind outside this closed list is refused.

## Limits and Return Shape

- **Return object**: `{ "met": bool, "observed": string, "at": "<RFC3339 UTC>" }`.
- **Duration bounds**: default `timeout_s = 1800` (30 minutes), hard maximum `MAX_TIMEOUT_S = 14400` (4 hours). When `timeout_s` expires before the condition is met, `watch` returns `met: false` with the last observation.
- **Polling bounds**: files, PIDs, Night Shift task files, and loopback HTTP poll at `>= 1s`; `gh_run`, `systemd_unit`, `command`, and `git_ref` poll at `>= 15s`.
- **Resource bounds**: file reads are capped at 64 MiB per check and `observed` is capped at 6,000 characters (~1,500 tokens).
- **Scope**: `watch`, `lookup`, `run_test`, `git_log` and `diff_stat` use no model; `task` is on probation (T1 to T5); `ask` and `night` are not built.
