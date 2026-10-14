# Code Mode in night 6: where the time went

An offline analysis of SWE-bench night 6 (harness at `25d514e`, 44 tasks of `night2-no-mismatch.txt`, two at a time), from the run folders under `~/.local/share/dreamference/swe-bench/runs/` (`n6-default`, `n6-exec-tool-freeform`): the instance records, the `exec --json` logs and every session's rollout. Nothing was re-run. Written 2026-10-14.

- **n6-default**: Code Mode with `exec` offered to SGLang as a function (`dc7bd9d`, `ling-rs/src/exec_tool.rs`). 31/44 resolved, median 7 min 7 s, 6 timeouts.
- **n6-exec-tool-freeform**: `exec` stays a custom tool, which SGLang drops, so the model sees `exec_command`, the `code_*` tools and the orphan `wait` directly. 30/44, median 5 min 38 s, 1 timeout.

The resolved difference is not significant (McNemar p = 1.0); the question here is only the time.

## The answer in brief

1. **A request costs the same in both arms; the default arm makes more of them.** Model time per request is 5.03 s against 5.01 s (paired median ratio 0.98), with the same input (30.6k against 30.3k tokens per request), output (153 against 159) and prefix-cache hit rate (96.9% against 96.7%). The default arm made 6,353 requests against 5,738 (+11%; per instance, median 89.5 against 76.5, paired median ratio 1.44). Wall time is almost exactly requests times ~5 s in both arms, so every finding below is about why there are more requests or longer waits between them.
2. **The extra 4,612 s of agent time (35,810 s against 31,198 s) breaks down roughly as:**
   - **~1,600 s in failed `exec` cells.** 221 of 2,864 cells (7.7%) ended `Script failed` (228 counting the pragma error and the policy rejections), in 176 bursts of up to 8. Each is a full round trip that produced nothing.
   - **~500 to 1,500 s of per-cell overhead on the cells that worked.** 2,646 of 2,864 cells (92%) are exactly one `tools.exec_command` call wrapped in JavaScript; 11 cells called two tools. An `exec` request takes 2.80 s at the median (mean 4.68 s) against 2.43 s (4.04 s) for a direct `exec_command` in the same arm and 2.60 s (4.41 s) in the other arm. It decodes at 32 tokens/s against 35, which fits command text escaped twice, once in a JS string and again in the JSON argument, drafting less well. Speculative acceptance was not logged, so that is a likely reason, not a measured one.
   - **+1,190 s waiting on long test runs** (`write_stdin` polls with `yield_time_ms` of 240,000 to 300,000: 619 s on a hung sqlite suite in django-13807, 425 s in sympy-18698, 240 s on the full Django suite in django-11265). These went through the direct `exec_command`, not `exec`. Nothing in the data ties them to Code Mode; they are variance that costs time in either arm.
   - **+768 s in compactions**: 60 against 55, at about 85 s each (5,461 s against 4,693 s, about 15% of agent time in both arms). The 49,152-token task budget compacts at 43k to 48k tokens of input. `exec`'s description adds about 1,000 tokens to the fixed prefix (first request 13.0k against 12.0k tokens), which leaves about 3% less room between compactions.
   - What is left is variance between trajectories, and it runs both ways.
3. **The timeouts are mostly tails.** The 6 instances that timed out in the default arm cost 16,209 s there and 10,087 s in the other arm: +6,122 s, more than the whole gap. The other 38 instances took *less* total time with Code Mode (19,601 s against 21,111 s), because the freeform arm had its own runaways (sympy-15875: 510 s against 2,513 s; sympy-24443: 359 s against 1,248 s). The median moved, though. On the 28 instances the freeform arm finished in under 700 s, the default arm took 72% longer (11,092 s against 6,450 s) and made 46% more requests (2,261 against 1,546), 111 of them failed cells. Night 5's default arm saw the same tools as the freeform arm (`exec` dropped by SGLang, a different `ling` build) and had **5** timeouts, median 5 min 5 s, 5,821 requests. With the same tools, 1 timeout one night and 5 another: the timeout count is mostly noise. The slower median is not; two runs without Code Mode had medians of 5:05 and 5:38, against 7:07 with it.
4. **Yields, cell time limits and output limits play no part.** No cell yielded: the longest ran 25 s, under the 30 s default yield, and no output says `Script running`. The model set `yield_time_ms` on `exec_command` about equally often in both arms. Output is 896 bytes per `exec` call against 1,003 per direct `exec_command` (964 in the freeform arm), with 3 truncations against 3 (6 in the freeform arm). The orphan `wait` was called 7 and 8 times and took no time. What does differ: an `exec` cell's output is only what the cell prints, and 2,348 cells print `r.output` while only 3 read the exit code. The model loses the `Process exited with code N` line that a direct call shows, and 9.2% of `exec` outputs come back empty against 4.6% for direct calls.
5. **The report's token line is wrong for timed-out tasks.** `Tokens` adds up `turn.completed` events (`dreamference/swe_bench/swe_bench_run_store.py`, around line 197), and a session killed by the timeout never emits one. Night 6's "111M against 160M input" leaves out the default arm's 6 timeouts (83.6M) and the freeform arm's one (14.4M). From the rollouts' `token_usage_record`s, the totals are **194.7M against 174.0M input** (+12% with Code Mode) and 973k against 911k output. Any earlier report built by the same code undercounts the same way when it had timeouts (not checked night by night).

## Failed cells

What the 221 `Script failed` outputs say (the top kinds; the rest are single cases):

| Error | Cells | What the model wrote |
|---|--:|---|
| `SyntaxError` (unexpected identifier, token or string, missing `)`, unexpected end) | ~115 | Python heredocs and quotes inside a JS template literal or string, Python statements (`import json`) as JS, a bare `r = await …` |
| `ReferenceError: exec_command is not defined` / `exec is not defined` / `r is not defined` | 36 | the call without the `tools.` prefix, or `text(r)` after a failed line |
| `TypeError: tools.exec / code_show / code_search / apply_patch / ling_search is not a function` | 28 | tools that are not nested in the isolate. The `code_*` tools are flat functions (patch 0020), `apply_patch` is not offered on SGLang, and `exec` cannot call itself |
| `exec_command failed: … rejected: rm -f style commands are not permitted` | 15 | the policy's refusal, shown as a script error |

Failures cluster after a compaction. Of the first 10 cells after each compaction, 12.0% fail, against 6.9% of all other cells. The summary drops the examples of working cells, so the model rebuilds the convention by trial. In sympy-18698, after a summary that was only a tool call, 16 cells in a row failed with `exec_command is not defined`. Direct `exec_command` calls interleaved with them did run.

Compaction summaries that were degenerate (only a `<tool_call>` block, or under 400 characters): 6 of 60 in the default arm (django-11477 twice, 11964, 12209, 13807, sympy-18698) and 4 of 55 in the freeform arm. It is not specific to Code Mode, but under Code Mode it lands on two of the six timeouts.

## The six timeouts: the last 10 minutes

| Instance | Freeform arm | Last 10 minutes in the default arm | Cause |
|---|---|---|---|
| django-11265 | timeout 2,701 s | 52 `exec` and 43 direct `exec_command` calls, 20 Python probes tracing the `FilteredRelation` subquery, no edit; one compaction at 2,287 s. Earlier, 240 s waiting on two full-suite runs. | The task: both arms timed out |
| django-11433 | done 2,670 s | Compaction at 2,442 s, fix applied, then 30 test runs, several with wrong labels (`admin` for `admin_views`) and re-runs. Resolved anyway. | Length: the other arm finished 30 s before the limit |
| django-11477 | done 822 s | Compaction at 2,421 s, then it reverted its own fix with `git checkout` and derived it again, 6 failed cells (`r is not defined` ×3, `tools.apply_patch`, heredoc quoting ×2), then began editing the `.po`/`.mo` locale files for a test. Over the session: 6 compactions (2 degenerate, at 362 s and 618 s), 15 failed cells. | Code Mode: failed cells, lost context |
| django-12663 | done 2,661 s | Two compactions (2,161 s and 2,558 s), fix in place, writing a regression test, manual repro failing. Resolved in both arms. | Length |
| django-13112 | done 649 s | 126 `exec` cells, 19 test runs, 13 Python probes reading migration state code; the fix was already in place (resolved in both). One compaction at 2,344 s. | Sprawl, unclear; not a mechanical fault |
| sympy-18698 | done 584 s | Degenerate compaction at 2,078 s, then 16 failed cells (`exec_command is not defined`, `tools.code_show is not a function`) between 40 direct calls, and 420 s polling one sympy test run (`write_stdin` 120 s then 300 s). 28 failed cells in the session. | Code Mode: failed cells after compaction, plus a long wait |

So two timeouts (11477, 18698) come from Code Mode faults, one (13112) is unclear, and three (11265, 11433, 12663) would have happened, or nearly, without it.

## Proposals, ranked

Effects are estimates from this night's numbers, one sample per arm; each wants its own arm.

1. **Tell the model when to use `exec`, and how, in `exec`'s own description.** `ling-rs/tools/src/custom.rs` `as_function` already appends the grammar to the description it sends SGLang and vLLM. One more paragraph there reaches only the servers where the function form is used, with no Codex patch and no change on ling-engine:
   - one shell command: call `exec_command` directly;
   - `exec` only to combine several tool calls or to process output in JavaScript;
   - inside a cell the only nested tools are `tools.exec_command` and `tools.write_stdin` (the `code_*` tools are called directly);
   - always `text(r.output)` and the exit code;
   - write a Python edit with a quoted heredoc through `exec_command`, never inside a template literal.

   The Lark grammar text could go at the same time: a function call has no use for it, and dropping it saves about 100 prefix tokens. Expected effect: most of the 2,646 wrapper cells become direct calls (0.5 to 1.5 ks), and most of the ~150 syntax and name failures go (about 1 ks), so **2 to 2.5 ks of the 4.6 ks**, bringing the median back toward 5:40. The same text could go in the prompt (`ling-rs/src/prompt.rs`, `prompt::compose`) instead, but it would then reach ling-engine too.
2. **Restate the convention after a compaction, and catch degenerate summaries.** The ledger (`ling-rs/src/ledger.rs`, run by the `SessionStart`/`compact` hook the launcher registers) already appends to the conversation after each summary, from the rollout, with no model. It could:
   - add one line with a working cell from the session when it used `exec`;
   - re-append the previous summary when the new one is only a tool call or under ~400 characters.

   Expected effect: the post-compaction failure rate falls from 12% toward 7%, and the context losses that sank django-11477 and sympy-18698 are avoided: **1 to 2 fewer timeouts per night (about 2 ks each)**. This applies to both arms.
3. **Say what went wrong when a cell fails.** In `custom.rs`, `replay`/`output_text`, which turn outputs into what the model sees, append a fixed hint to a `Script failed` output that matches a known pattern. Examples:
   - `exec_command is not defined`: "nested tools are on `tools`: `tools.exec_command({cmd})`";
   - `tools.code_show is not a function`: "call `code_show` directly, not from `exec`";
   - a `SyntaxError` with `<<` in the input: "put the script in a file or call `exec_command` directly".

   The hint is deterministic, so the cached prefix holds. Expected effect: bursts of 1.3 failed cells on average come down toward 1, **about 0.3 to 0.5 ks**, more on the runaway instances.
4. **Bound the waits on test runs.** Add a line in the SWE-bench task prompt (`dreamference/swe_bench/swe_bench_instance_run.py`) or the prompt to wrap test runs in `timeout 300` and poll with `yield_time_ms` of 60,000 or less. Up to 1.2 ks in the default arm, 0.1 ks in the other. This is arm-independent and lowers the variance more than the median.
5. **Resolve the `apply_patch` contradiction.** The base instructions say "Use `apply_patch` for manual code edits", but on SGLang the catalog carries no `apply_patch_tool_type` (`ling-rs/src/lib.rs`, `model_catalog`), so no such tool exists. The model calls `tools.apply_patch` (4 times) or `apply_patch` (4 times in the freeform arm), writes "Let me use apply_patch properly", then edits with Python. Either drop those lines from the composed prompt when the tool is not offered, or offer it. Night 5's function arm ended in agent error on all 44 tasks, so there is no measurement yet. The effect is small but present in both arms.
6. **Offering `exec` only for some tasks: the data gives nothing to choose by.** 11 of 2,864 cells composed two tools; no SWE-bench task used what Code Mode adds. While Code Mode stays on, the lever is proposal 1. For unattended benchmark runs, `DREAMFERENCE_MIGHTLING_EXEC_TOOL=freeform` remains the faster measured setting.
7. **Output limits and yields: leave them.** `TOOL_OUTPUT_TOKEN_LIMIT` (8,000), `exec`'s defaults (30 s yield, 10,000 tokens) and the truncation policy did not bind (see point 4 of the brief).
8. **Fix the report's `Tokens` line** (`swe_bench_run_store.py`) to sum the rollout's `token_usage_record`s, or the last `token_count`, so that timed-out tasks count. This is accounting, not speed, but without it every comparison of an arm that times out more understates its cost.

## How the numbers were taken

- **Requests**: the `token_usage_record` entries in each session's rollout (`scratch/<id>/codex-home/sessions/…`); a resumed or nudged session adds its own file.
- **Model time** of a request runs from the previous tool output (or the start) to its `token_usage_record`.
- **Tool time** runs from the call item to its output item.
- **Compaction time** is the request that ends in a `compacted` record.
- **Exec cells** are `custom_tool_call` items named `exec` (the translated function call is recorded in the custom form). Direct calls are `function_call` items.
- **Commands inside cells** were read from the `cmd:` arguments.
- **Caveats**:
   - Each arm is one run.
   - The arms ran hours apart (the freeform arm first, from 18:37 UTC on 2026-10-13; the default arm from 23:30). Per-request latency matches, so server load did not differ materially.
   - Timed-out instances are cut at 2,700 s, which understates the default arm's real cost.

## Per instance

| instance | default: status, wall s | requests | compactions | exec cells (failed) | direct exec_command | freeform: status, wall s | requests | compactions |
|---|---|--:|--:|--:|--:|---|--:|--:|
| astropy__astropy-14369 | done 902 | 132 | 2 | 7 (3) | 112 | done 1180 | 226 | 3 |
| django__django-11265 | timeout 2702 | 387 | 5 | 162 (4) | 204 | timeout 2701 | 428 | 6 |
| django__django-11299 | done 430 | 90 | 0 | 2 (1) | 79 | done 359 | 59 | 0 |
| django__django-11433 | timeout 2702 | 434 | 5 | 228 (7) | 166 | done 2670 | 301 | 4 |
| django__django-11477 | timeout 2702 | 426 | 6 | 345 (15) | 29 | done 822 | 124 | 1 |
| django__django-11964 | done 1661 | 255 | 2 | 175 (7) | 70 | done 2107 | 440 | 5 |
| django__django-12209 | done 1330 | 262 | 3 | 112 (5) | 119 | done 1478 | 288 | 3 |
| django__django-12308 | done 424 | 73 | 0 | 66 (3) | 1 | done 186 | 25 | 0 |
| django__django-12663 | timeout 2701 | 452 | 6 | 329 (4) | 48 | done 2661 | 522 | 6 |
| django__django-13112 | timeout 2701 | 472 | 5 | 359 (6) | 50 | done 649 | 139 | 1 |
| django__django-13807 | done 1387 | 149 | 1 | 3 (3) | 121 | done 212 | 45 | 0 |
| django__django-13820 | done 188 | 48 | 0 | 2 (1) | 42 | done 727 | 155 | 1 |
| django__django-13821 | done 171 | 31 | 1 | 5 (2) | 22 | done 182 | 24 | 1 |
| django__django-14122 | empty 80 | 28 | 0 | 27 (1) | 0 | done 777 | 133 | 1 |
| django__django-14373 | done 35 | 16 | 0 | 1 (1) | 9 | done 212 | 52 | 0 |
| django__django-14580 | done 472 | 125 | 1 | 60 (4) | 46 | done 414 | 100 | 1 |
| django__django-14725 | done 1286 | 237 | 3 | 114 (9) | 105 | done 1806 | 247 | 3 |
| django__django-14752 | done 164 | 41 | 0 | 7 (5) | 32 | done 64 | 16 | 0 |
| django__django-14855 | done 357 | 67 | 0 | 19 (1) | 41 | done 406 | 82 | 0 |
| django__django-14915 | done 316 | 89 | 0 | 25 (3) | 55 | done 153 | 35 | 0 |
| django__django-15127 | done 166 | 50 | 0 | 9 (3) | 39 | done 97 | 32 | 0 |
| django__django-15278 | done 232 | 48 | 0 | 21 (3) | 11 | done 174 | 40 | 0 |
| django__django-15973 | done 1101 | 221 | 3 | 7 (4) | 183 | done 1424 | 227 | 3 |
| django__django-16136 | done 249 | 52 | 0 | 22 (1) | 17 | done 104 | 32 | 0 |
| django__django-16315 | done 1391 | 250 | 2 | 161 (17) | 61 | done 666 | 144 | 1 |
| django__django-16493 | done 256 | 53 | 0 | 30 (1) | 17 | done 86 | 23 | 0 |
| django__django-16667 | done 148 | 47 | 0 | 40 (1) | 0 | done 64 | 18 | 0 |
| django__django-16819 | done 1644 | 311 | 4 | 141 (7) | 117 | done 226 | 71 | 0 |
| django__django-17084 | done 500 | 102 | 1 | 20 (7) | 70 | done 303 | 82 | 0 |
| psf__requests-2931 | done 438 | 141 | 1 | 25 (3) | 94 | done 671 | 204 | 2 |
| pytest-dev__pytest-7982 | done 67 | 29 | 0 | 25 (2) | 0 | done 53 | 19 | 0 |
| sphinx-doc__sphinx-9673 | done 235 | 61 | 0 | 55 (16) | 1 | done 133 | 38 | 0 |
| sympy__sympy-13480 | done 143 | 39 | 0 | 4 (1) | 31 | done 81 | 27 | 0 |
| sympy__sympy-15345 | done 182 | 42 | 0 | 37 (4) | 0 | done 102 | 28 | 0 |
| sympy__sympy-15875 | done 510 | 107 | 0 | 75 (15) | 28 | done 2513 | 418 | 4 |
| sympy__sympy-16792 | done 568 | 119 | 1 | 37 (2) | 56 | done 318 | 65 | 1 |
| sympy__sympy-16886 | done 51 | 21 | 0 | 4 (3) | 11 | done 34 | 14 | 0 |
| sympy__sympy-18698 | timeout 2701 | 396 | 5 | 47 (28) | 299 | done 584 | 113 | 1 |
| sympy__sympy-19346 | done 131 | 39 | 0 | 6 (6) | 17 | done 618 | 142 | 1 |
| sympy__sympy-20428 | done 1092 | 174 | 2 | 21 (4) | 102 | done 1401 | 176 | 3 |
| sympy__sympy-20801 | done 194 | 50 | 0 | 11 (2) | 31 | done 126 | 34 | 0 |
| sympy__sympy-24213 | done 99 | 33 | 0 | 3 (1) | 21 | done 208 | 64 | 0 |
| sympy__sympy-24443 | done 359 | 61 | 0 | 2 (2) | 54 | done 1248 | 255 | 3 |
| sympy__sympy-24562 | done 642 | 93 | 1 | 13 (3) | 66 | done 198 | 31 | 0 |
