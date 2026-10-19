# Mightling 1.6.3 — release notes

**Status:** published 2026-10-15 as `v1.6.3` (tag on `18aa24a`; this line updated 2026-10-19). Prepared 2026-10-15 on the branch `release/1.6.3` (origin/main at `730a93e`, the version
set to 1.6.3 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo` and
`desktop/electron/package.json`, and these notes). Cut the night the turn-end
compaction bench v2 passed (owner's decision of 2026-10-14: a pass merges `compaction/cache-hit`
with the winning arm on by default and releases). There is no 1.6.2: the Code Mode fix merged after
1.6.1 (`c906339`) was never cut on its own and ships here. Built from `git log v1.6.1..origin/main`.
The text between the two rules is the GitHub release's description: the release workflow publishes
it as it stands (`scripts/release_notes.py`), and refuses to while a `{{TBD` is left in it.

**Version.** Set to 1.6.3 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`
and `desktop/electron/package.json`. The release workflow stamps it into the binaries.

**What 1.6.3 carries beyond 1.6.1** (all on `main`):

| Commit | What |
|---|---|
| 362def2 | the compaction request offers the turn's tools (Codex patch `0027`, 675 bytes; the series 44,672 bytes under the 45,000 cap), so it is served from the server's prefix cache |
| 9e5d8a7, 955a0f3 | the compaction summary bounded to 800 words through Codex's `compact_prompt`; on by default |
| 955a0f3 | turn-end compaction on by default at 20% of the usable window (launcher `TURN_END_DEFAULT`, `TURN_END_SHARE_PERCENT` 53) |
| 8de91ae, 1bd5eb3, baae689 | drop-first compaction (patch `0026`, 1,039 bytes): old tool outputs dropped before a summary; built as a setting, off, with a SWE-bench arm (`run --drop-old-outputs`) |
| d2b4b84 … c1d03c4 | the turn-end compaction bench (`scripts/turn_end_compaction_bench.py`) and its two reports, `docs/dev/turn-end-compaction/2026-10-13.md` (keep off, cache-cold) and `2026-10-14.md` (v2, switch on) |
| 25461c2 | Code Mode: no orphaned `wait` where the server drops the custom `exec` tool |
| 6028d6a | the night script smokes every arm on one real task before the unit starts; `TASK_TIMEOUT` |
| 34841ab, 148f82d | SWE-bench chooser v2 (`scripts/swe_bench_chooser_v2.py`): reproduction tests from the issue, the regression filter, behaviour groups, the judge last |
| 49a589f | the patch size cap raised to 45,000 bytes, once, by the owner |
| dd0c403, 0489800, f81a37c | backlog item 14 (per-output summaries off the critical path); chooser v3 research notes |
| 5840750, 0408be8 | research note: a Mightling node on Windows on Arm (gap analysis, no code) |
| b875b23, 2597053, b5d5f8a, 9e808fc, 4918dc6, eb89475, c0f0ade, d431d54, f4cb954, 56e8f4a, 675dd7e, a24d127 | ling-engine research notes and the paper (repository only) |

**Not in 1.6.3:** drop-first compaction stays off (its arm lost the bench: see below); the SGLang
image pin is unchanged; the branches `signal/multi-account`, `chat/onyx-phase-c`,
`remote/vpn-mvp-box`, `security/sandbox-home`, `bench/apply-patch-function-v2` and
`swe-bench/night10-harness` are not merged.

**Checklist before publishing:**
- **The suite:** `.venv/bin/python -m pytest tests/ -q` passes offline (1,271 passed on the merge
  commit, about 100 s); the launcher's Rust tests pass (`ling-launcher` 256, `ling-tools` 17,
  `ling-masking` 19 on the export).
- **The launcher's defaults on the release build:** a TUI session with a fresh `CODEX_HOME` carries
  `-c model_post_turn_compact_threshold_percent=20` and a `compact_prompt` ending in "Keep the
  summary under 800 words" (`ling-rs/src/compaction.rs`, `summary_cap.rs`); `ling exec` carries
  neither the percentage nor a turn-end compaction (COMPACTION spec §12.1).
- **The egress audit** (`ling-admin audit egress`, and `--docs`) passes on the release build: the
  compaction request now carries the tool list, nothing else about the requests changed.
- **The bench's report** is on `main` (`docs/dev/turn-end-compaction/2026-10-14.md`) and the spec's
  §14.3 records the decision.

---

**Compaction is faster, and it happens between your turns.** When a session's context reaches the
compaction limit, the agent summarises it and carries on from the summary. Until now every such
summary cost far more than it should: Mightling's model server keeps the session's prompt in its
cache, but the compaction request was built without the tool list, so its prompt differed from the
cached one within the first few tokens and the server re-read the whole context (60–90K tokens, 50–75
s) before writing a word. The compaction request now carries the turn's tools, so it is served from
the cache (a median 100% of the prompt cached in the measurement, against 0% before) and costs the
summary alone. The summary is also bounded to about 800 words, so it does not run long.

With compaction that cheap, Mightling now compacts **at the end of a turn**: once an answer is on
screen and the context has passed about 50,000 tokens (20% of the model's usable window), the
session compacts while you read, instead of stopping in the middle of your next request. If you type
during it, your message waits for the summary and then runs in the same turn (Enter), or starts the
next turn after it (Tab, the queue key); nothing is lost. Measured on two scripted multi-turn coding
sessions against a GB10 (12 sessions planned, 10 run): turn-end compactions took a median 53.5 s;
the sessions compacted mid-turn 2.0–2.5 times each without it and never with it; every typed message
was kept; and the sessions were 27–42% shorter in wall time. The report is
`docs/dev/turn-end-compaction/2026-10-14.md`. `ling exec` (Night Shift, SWE-bench) never compacts at
the end of a turn, since its process ends with the answer.

Switches, for anyone who wants the old behaviour: `mightling_turn_end_compaction = false` in the
Dreamference config file (or `DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION=0`) turns turn-end
compaction off; `DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS=0` removes the length bound (another
number moves it); `DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS=0` sends the compaction request without
tools as before.

**Dropping old tool outputs instead of summarising: built, measured, off.** A third variant drops
old tool outputs (keeping the calls and a one-line placeholder) when that alone brings the context
under the limit, and summarises only otherwise. It is in this release as a setting
(`mightling_drop_old_outputs`, `DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS=1`) and off: in the same
measurement it compacted less but left turns over the threshold with nothing on record, and one
session ran into the two-hour limit. It stays available for the benchmark nights.

**Code Mode's `wait` is gone where it could not work.** When Code Mode's `exec` is offered in the
form the SGLang server drops (`DREAMFERENCE_MIGHTLING_EXEC_TOOL=freeform`, the pre-1.6.1
behaviour), Codex still offered the companion `wait` tool, and the model's calls to it ended in
"exec cell not found", each costing minutes of waiting on a test run that had to be relaunched.
`wait` is now offered only with an `exec` that reaches the model.

**Benchmark nights: every arm smoked first.** The night script's `start` now runs one real task per
arm before the overnight unit starts and refuses to start on a failure, after a night whose
experimental arm failed every task in seconds on a setting the agent could not accept. A per-task
limit (`TASK_TIMEOUT`) is a parameter of the script. The best-of-N chooser has a second version
(`scripts/swe_bench_chooser_v2.py`) that writes reproduction tests from the issue, runs them in the
task's container and consults the judge last; it is a script, not yet part of Night Shift.

---

## Upgrading from 1.6.1

`ling update` on a client or a node; nothing else changes. Turn-end compaction, the cached
compaction request and the summary bound take effect at the next session. A node's SWE-bench harness
is updated with the wheel. To keep 1.6.1's behaviour, put `mightling_turn_end_compaction = false`
in `~/.config/dreamference/config.toml`.
