---
name: delegate
description: Use Mightling's `watch` tool (`mcp__plugin_mightling_mightling__watch` or `ling delegate watch`) for every wait longer than a minute instead of `sleep` loops or repeated polling turns.
---

# Mightling `watch` (Class W1 — Waiting and Monitoring)

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
- **Phase P1 scope**: only class `W1` (`watch`) is active. Model-backed tools (`ask`, `lookup`, `task`, `night`) remain disabled.
