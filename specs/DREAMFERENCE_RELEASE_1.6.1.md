# Mightling 1.6.1 — release notes

**Status:** published 2026-10-14 as `v1.6.1` (tag on `3ade766`; this line updated 2026-10-19). Prepared 2026-10-14 on the branch `release/1.6.1` (origin/main at `ad40db7`, the version
set to 1.6.1 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo` and
`desktop/electron/package.json`, and these notes). Cut after night 5, as decided
on 2026-10-10, with the results of nights 4, 5 and 6 below. Built from `git log v1.6.0..origin/main`
(the commits that are not board, chat or sessions lines; 5630dff, 0cf297a and 556b126 are left out
because 1.6.0 already carries them as 6d7030b, b645195 and aef45a2). The text between the two rules
is the GitHub release's description: the release workflow publishes it as it stands
(`scripts/release_notes.py`), and refuses to while a `{{TBD` is left in it.

**Version.** Set to 1.6.1 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`
and `desktop/electron/package.json`. The release workflow stamps it into the binaries.

**What 1.6.1 carries beyond 1.6.0** (all on `main`; the arms' branches `bench/restart-after-compactions`
and `bench/apply-patch-function` merged for nights 4 and 5):

| Commit | What |
|---|---|
| 859a158 | the built-in system prompt `offline` |
| b1b9296 | `apply_patch` offered freeform on ling-engine only, with an override for a benchmark arm |
| dc7bd9d | Code Mode on SGLang: `exec` offered as a function, its call mapped back to the custom tool call (`DREAMFERENCE_MIGHTLING_EXEC_TOOL`) |
| 340828b, fbbd7a1, 1f583b7, ad40db7 | PDF search: `pdf_search` and `docs_read` on its ids in `ling-docs mcp`, `ling-docs extract`, `ling-fetch --download`; the launcher offers `ling_docs` for it |
| 2c3ee03 | turn-end compaction as a launcher setting (off, and it stays off in 1.6.1), and Night Shift's compaction audit (on) |
| 9fb0b1e, 1ae9387, 80b2594, b86626e | Wi-Fi power saving: `host check` reports it, `host setup` and `node prepare` turn it off (prepare's one NetworkManager change), `node enable` warns, and a Linux client whose browse misses adds a hint when its own Wi-Fi saves power |
| 9ef8363 | `node provision --web` runs `ling-admin chat start` and `chat configure` |
| 9d96092, 12d4eaf | Signal: a question refused by the model gate during a benchmark run gets a clear reply (spec SIGNAL §17.3); a test pins the refusal text |
| 9e6f82e | Signal's first real-account test (2026-10-12) recorded; Signal drops the preview label, Matrix and Telegram keep it |
| e7b6851, 6bef1d1, 98e32a5, 039dfd1, a9a8896, fc1e1ab, d85f06b, 17eac9b, cd043ee | SWE-bench harness: the launcher's built-in prompts, `draw --exclude` and the mismatch list, the compactions replay, the night script (`REF`, `ARMS`, nodes = none), a run waits for an active index scope, fresh-50b |
| be64083, fa220c8, 0e95f5f, 06d0689, 25d514e | the arms of nights 4–6: `--restart-after-compactions`, `--apply-patch`, `--exec-tool` |
| e41d150, da5aca4, 2cceb7b, ad6af7a | SWE-bench reports: broken passing tests counted apart, compactions per run; `status` lighter; the chooser test script |
| 4168956 | tests offline by construction; the suite's real time in the guide |
| 29dd60b, 4976ede | releases publish themselves, with no approval step |
| 324a6b6, c93d3fe, 177542c | ling-engine submodule (its spec; never reaches a build) |
| 5a0adb2, a22b776, 8c453ba, 1c8e3b6, dbb7fd3, e17a49d, e5431db, afe8a70, 372df37, f048ec8, e6b0706, 5b9620e, bbdc22f, f2e5b19, 05e32b9, 9c8a249, 5a35d2d, e0ae9fd, 6012bae, 7979852, b612045 | docs, specs, research surveys, the paper, the logo's outline, the 1.6.0 status |

**Not in 1.6.1:** turn-end compaction stays off; the SGLang image pin is unchanged; the branches
`fix/code-mode-wait`, `signal/multi-account`, `chat/onyx-phase-c`, `compaction/drop-old-outputs`,
`remote/vpn-mvp-box` and `security/sandbox-home` are not merged.

**Checklist before publishing:**
- **`ling prompt list`** on the release build lists `offline` between `default` and `high-swe`,
  and `DREAMFERENCE_MIGHTLING_PROMPT=offline ling exec "…"` runs with a prompt that names no
  `ling-search` or `ling-fetch` (`ling prompt show offline --composed`).
- **The catalog:** against this machine's SGLang, `~/.mightling/model_catalog.json` has no
  `apply_patch_tool_type`; against ling-engine (`owned_by: ling-engine` in `/v1/models`) it says
  `"freeform"`. **Pending an owner decision:** `DREAMFERENCE_MIGHTLING_APPLY_PATCH=function` writes
  `"function"`, which Codex rust-v0.158.0 does not accept (its `ApplyPatchToolType` has only
  `freeform`), so `ling` exits at start with "unknown variant `function`, expected `freeform`"
  (night 5, below). The code is unchanged in 1.6.1 and the value is not advertised.
- **The suite:** `.venv/bin/python -m pytest tests/ -q` passes offline, about two minutes; nothing
  in it spawns the installed `ling` or reaches port 8000 (the guard in `tests/conftest.py`).
- **Nights 4, 5 and 6** are reported (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §21–§23; the reports
  in `~/.local/share/dreamference/swe-bench/night{4,5,6}/`) and the paragraphs below carry them.
- **The egress audit** (`ling-admin audit egress`, and `ling-admin audit egress --docs` because PDF
  search is new) passes on the release build.

---

**Search inside a long PDF.** The agent can now search a PDF it has not indexed with `pdf_search`:
a URL, or a PDF in the session's working directory or in one of your document collections. The PDF
is read by the local file index's reader in its sandbox and indexed on its own (keywords and
meaning, as `docs_search` does), and the best passages come back with their page numbers; the agent
reads on with `docs_read` and cites the page. A long paper or manual no longer has to be read page
by page through the web fetch. A URL is downloaded once (`ling-fetch --download`, PDFs only, within
a size cap) and a second question uses the copy, kept until it has gone unused for a day. At `/airgapped on`
a URL is refused and a local PDF is still searched. `ling-docs extract <file.pdf> --page N` prints
a PDF's text from a shell. Linux, where the local file index is installed (a node installs it).

**Code Mode now works on SGLang.** Codex offers Code Mode's `exec` tool (a program that calls
several tools in one turn) as a kind of tool the SGLang server drops without a word, so in 1.6.0
the model never saw it. `ling` now offers `exec` to SGLang and vLLM as an ordinary function and
turns the model's call back into the call Codex expects; against ling-engine nothing changes.
Measured on 44 SWE-bench Verified tasks (night 6): 31 resolved with it against 30 without
(p = 1.0, no measurable difference), the model used `exec` in all 44 tasks, and the median time
per task was 7 min 7 s against 5 min 38 s without it. It stays on;
`DREAMFERENCE_MIGHTLING_EXEC_TOOL=freeform` goes back to 1.6.0's behaviour.

**A prompt for a machine with no network.** `ling prompt use offline` (or
`DREAMFERENCE_MIGHTLING_PROMPT=offline` for one run) starts sessions with the general prompt
minus its web and email instructions, so the model is not told to run commands that cannot work
where there is no network: a container, an air-gapped machine, a benchmark. `default` is
unchanged.

**`apply_patch` where the server can run it.** Mightling now tells the agent about Codex's
`apply_patch` editing tool when the model server is ling-engine, which runs that kind of tool;
on the SGLang server a GB10 runs today the tool would be dropped by the server without a word,
so nothing changes there and the agent edits through the shell as before. Night 5 was to measure
an ordinary-function form of `apply_patch` on SGLang, and could not run: the Codex this release is
built on accepts only the freeform form, so all 44 of that arm's tasks stopped at start, while the
default resolved 29 of 44. The default is unchanged: no `apply_patch` tool on SGLang.

**Benchmark nights, reproducible.** The SWE-bench rounds that decide what ships now run from one
script with the arms named (`ARMS`) and the harness commit pinned (`REF`), on this machine alone;
the task lists are committed under `docs/dev/swe-bench/`, a later fresh list leaves out every
earlier one (`draw --exclude`), and the 68 tasks whose pull request does not match its issue
(PAIChecker's human-labelled list) are out of every list from night 2 on. A replay count
(`scripts/context_budget_replay.py compactions`) reads compactions per task from a finished run,
and the report now counts patches that broke a passing test apart from those that did not fix the
issue, and shows compactions per run with their share of the agent's time. Night 4 measured a
restart instead of a third compaction (`--restart-after-compactions 2`): 28 of 44 resolved against
the default's 31 (−6.8 points, p = 0.453, no measurable difference), so the arm is dropped and the
default stays: a session compacts as often as it needs.

**Night Shift's morning report checks compactions.** For the sessions of the day that compacted,
the report's new Compactions section says whether each summary still names the files changed, the
failed commands and the last test result. It reads the session files only, with no model call;
`compaction_audit = false` in the `[night]` table switches it off.

**The test suite is offline by construction.** A test that would start the installed `ling` or
connect to the model server now fails with the path or address in its message; the one module
that tests the built binary opts in by name. The suite takes about two minutes, which the project
guide now says instead of the eight seconds it used to claim.

**Signal is no longer a preview.** The Signal bridge (`ling signal setup`) has now been used against
a real Signal account: linked to the owner's own account by QR code, a question asked in Note to Self
and answered there, and `/stop`, with 1.6.0 as published. Nothing in the bridge changed for this.
Matrix and Telegram (`ling chat`) keep their preview label: neither has yet been tested against a
real homeserver or Telegram bot.

**Signal says when the model is busy with a benchmark.** While a benchmark run holds the model, a
question from Signal is not answered, and Mightling now replies in Note to Self with the run, its
progress and roughly how long is left, so you know when to ask again. Before, the reply was the
raw server error.

**Nodes on Wi-Fi are found again.** Wi-Fi power saving, which Ubuntu's NetworkManager turns on by
default, loses most of the multicast DNS your other computers find a GB10 by: on two GB10s on
Wi-Fi up to ~80% of the queries went unanswered, and laptops could not find their node.
`ling-admin host setup` and `sudo ling-admin node prepare` now turn it off for the Wi-Fi
connection in use (`nmcli connection modify <connection> 802-11-wireless.powersave 2`, and `iw dev
<interface> set power_save off` for now, with no reconnect, so an SSH session survives);
`ling-admin host check` and `node enable` say when it is on, with the commands. On a Linux laptop,
when `ling` finds no node and the laptop's own Wi-Fi saves power, it adds one line saying so, with
the commands to run; it changes nothing itself. On Ethernet nothing changes and nothing is said.

### Fixed

- **`ling-admin node provision --web`** ran `ling-admin ling start` and `ling configure`, a
  subcommand `ling-admin` never had (a leftover of the rename), so `--web` failed at that step
  since 1.5.1; it now runs `ling-admin chat start` and `chat configure` there, and the dry run
  names the web UI instead of `node enable --no-web`.

---

## Upgrading from 1.6.0

`ling update` on a client or a node; nothing else changes. The new prompt, PDF search and Code
Mode on SGLang take effect at the next session. A node's SWE-bench harness is updated with the
wheel.
