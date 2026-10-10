# Mightling 1.6.1 — release notes

**Status:** draft, 2026-10-10, written by the product stream from what landed on `main` after
`release/1.6.0` was branched (`git log origin/release/1.6.0..origin/main`, 13 commits that are not
board lines) and the decisions of 2026-10-10. Not published: the version is not set and there is
no tag. The user decided that 1.6.1 is cut **after night 5's result** (the `apply_patch` arm), so
the two arm sections below carry a `{{TBD}}` each; the rest is fixed. The text between the two
rules is the GitHub release's description.

**Version.** To be set to 1.6.1 in `setup.py`, `dreamference/__init__.py`, the MCP server's
`serverInfo` and `desktop/electron/package.json` when the release is cut. The release workflow
stamps it into the binaries.

**What 1.6.1 carries beyond 1.6.0** (all on `main`; the arms' branches merge when their nights
run: `bench/restart-after-compactions` on Monday 2026-10-13, `bench/apply-patch-function` when
night 5 is named):

| Commit | What |
|---|---|
| 859a158 | the built-in system prompt `offline` |
| b1b9296 | `apply_patch` offered freeform on ling-engine only, with an override for a benchmark arm |
| e7b6851 | the harness knows the launcher's built-in prompts; the guard tests use their own listener |
| 4168956 | tests offline by construction; the suite's real time in the guide |
| 6bef1d1 | `draw --exclude`, and the PR-issue mismatch list out of every draw by default |
| 98e32a5 | replay: compactions per task against the grading |
| 039dfd1, a9a8896, fc1e1ab | the night script: `REF` pins the harness, `ARMS` names the arms, nodes = none |
| 5630dff | Matrix homeserver tests stub the sidecar network |
| a22b776 | `docs/dev/sessions.md`, the work streams' board |
| 324a6b6 | ling-engine submodule at 1edc433 |

**Checklist before publishing:**
- **`ling prompt list`** on the release build lists `offline` between `default` and `high-swe`,
  and `DREAMFERENCE_MIGHTLING_PROMPT=offline ling exec "…"` runs with a prompt that names no
  `ling-search` or `ling-fetch` (`ling prompt show offline --composed`).
- **The catalog:** against this machine's SGLang, `~/.mightling/model_catalog.json` has no
  `apply_patch_tool_type`; against ling-engine (`owned_by: ling-engine` in `/v1/models`) it says
  `"freeform"`; `DREAMFERENCE_MIGHTLING_APPLY_PATCH=function ling exec …` writes `"function"`.
- **The suite:** `.venv/bin/python -m pytest tests/ -q` passes offline, about two minutes; nothing
  in it spawns the installed `ling` or reaches port 8000 (the guard in `tests/conftest.py`).
- **Night 4 and night 5** are reported (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §21, §22) and the
  two `{{TBD}}` paragraphs below are filled or removed.
- **The egress audit** (`ling-admin audit egress`, and `ling audit egress` if the Rust port has
  landed by then) passes on the release build.

---

**A prompt for a machine with no network.** `ling prompt use offline` (or
`DREAMFERENCE_MIGHTLING_PROMPT=offline` for one run) starts sessions with the general prompt
minus its web and email instructions, so the model is not told to run commands that cannot work
where there is no network: a container, an air-gapped machine, a benchmark. `default` is
unchanged.

**`apply_patch` where the server can run it.** Mightling now tells the agent about Codex's
`apply_patch` editing tool when the model server is ling-engine, which runs that kind of tool;
on the SGLang server a GB10 runs today the tool would be dropped by the server without a word,
so nothing changes there and the agent edits through the shell as before.
{{TBD: night 5's result. If the function form of `apply_patch` won on SGLang, it is now the
default there, and this paragraph says so with the measured counts; if not, say that the arm was
measured and the default stays.}}

**Benchmark nights, reproducible.** The SWE-bench rounds that decide what ships now run from one
script with the arms named (`ARMS`) and the harness commit pinned (`REF`), on this machine alone;
the task lists are committed under `docs/dev/swe-bench/`, a later fresh list leaves out every
earlier one (`draw --exclude`), and the 68 tasks whose pull request does not match its issue
(PAIChecker's human-labelled list) are out of every list from night 2 on. A replay count
(`scripts/context_budget_replay.py compactions`) reads compactions per task from a finished run.
{{TBD: the restart arm (night 4): what it did and whether it stays an arm.}}

**The test suite is offline by construction.** A test that would start the installed `ling` or
connect to the model server now fails with the path or address in its message; the one module
that tests the built binary opts in by name. The suite takes about two minutes, which the project
guide now says instead of the eight seconds it used to claim.

---

## Upgrading from 1.6.0

`ling update` on a client or a node; nothing else changes. The new prompt and the catalog field
take effect at the next session. A node's SWE-bench harness is updated with the wheel.
