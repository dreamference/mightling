# Work streams and their status

Since 2026-10-10 the work runs as parallel streams (three, then four), each in its own working tree, so that no two
writers share a checkout. This file is their shared status board: **each stream appends one dated line
when it hands work off or finishes a piece, and reads the file when it starts.** It is the only place
where the streams can see each other; nothing else is shared but the git remotes. Lines are appended
with `session-note "<what changed>"` (a helper outside the repository that commits to this file through
a small clone of its own), or by hand; keep the newest at the bottom, one line each, and never rewrite
another stream's lines.

| Stream | Working tree | Owns |
| --- | --- | --- |
| **operations** | the main checkout of mightling | the SWE-bench nights and their watcher, the model server and the model gate, the installed `ling` and the install directory, node-b as a machine (containers, pairing, lane), releases and their pre-release checks, audits |
| **product** | `.claude/worktrees/product` (branch `work/product`) | mightling's code, tests, specs and docs; pushes through origin after a rebase; treats the model server, the gate, the install directory and node-b as read-only |
| **engine** | `~/ling-engine` | ling-engine's C++/CUDA, specs and reports; builds, tests and measures on node-b only; moves the submodule pointer in mightling through a note here, not by editing a mightling checkout |
| **crew** | `~/mighty-crew` (repository `dreamference/mighty-crew`) | the Mighty Crew product: the spec and the `crew` binary that runs a crew of coding-agent sessions on one codebase (what this board and the launcher do by hand); touches no other checkout |

A request that crosses a boundary (the engine stream needs the GPU without the reference container; the
product stream needs a new `ling` build installed) is a line here addressed to the stream that owns it.

## Status

- 2026-10-10 15:00 operations: night 1 running since 09:49 (default arm 34/50); night 2 (44 tasks) chains when it writes `done`; 1.6.0 prepared on `release/1.6.0`, cut Sunday evening after night 2; the model gate stays closed until then.
- 2026-10-10 15:00 engine: §16.1 microbenchmarks measured and committed (ling-engine 2384ddb, 1edc433; submodule at 1edc433); `--draft-block` defaults to 12; next: Codex Responses completeness, custom tools first (BACKLOG §19.13, §19.15).
- 2026-10-10 15:00 product: stream started on `work/product` at 5a0adb2; nothing handed off yet.
- 2026-10-10 15:03 operations: status board docs/dev/sessions.md and the session-note helper are in place; read the board at start, append a line at each hand-off
- 2026-10-10 15:25 operations: stream `crew` added (session 4): the Mighty Crew MVP spec is being written and the repository dreamference/mighty-crew created; decisions: public, Claude Code only in the MVP, Rust from the start, all roles bypass prompts.
