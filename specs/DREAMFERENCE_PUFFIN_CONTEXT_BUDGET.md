# Puffin Context Budget — what fills the context, and how to keep it small

**Status:** proposed on 2026-10-03, revised the same day (v2). Phase 1's build is done (§8): masking, the per-output cap and `puffin-code`'s output; its live checks and the A/B are not. §1 is measured from the rollouts of the SWE-bench pair `idx14b-on` / `idx14b-off` (14 instances each, same `puffin` build `runtime_hash bd978d3ede04`, Qwen3.8-27B, compaction limit 44,000 tokens, three instances at once), plus a probe of the model server's prefix cache (§1.7). §3 is read from the pinned Codex source (`rust-v0.158.0`). §2 and the sources are published work. The effect figures in §4 come from replaying the recorded trajectories (§1.6), not from new runs.
**What v2 changed, and why:**
- **Compaction costs about 104 s here, not 24 s** (§1.8). The v1 time table used COMPACTION §9.2's single-stream 14K figure. With the measured cost, masking is a clear time win at 44K and 49K, not "about even".
- **A masking move re-prefills from the first masked item,** not from the oldest newly masked one (§1.7). Every move costs about the whole tail, so moves should be rarer and larger: the defaults are now 0.85/0.50 of the limit with a 16,000-token step (§4.1).
- **v1's trigger could never fire** (§1.9). It measured the view with Codex's byte estimator over the history items, which exclude the base instructions and tool schemas (7–9K tokens) and under-count code. At a 44K limit its "36K" high mark was really about 48K, above the limit. The trigger now uses the quantity auto-compaction itself uses, and the boundary is stored per history segment instead of replayed (§4.1).
- **The placeholder points to a saved copy** of the output instead of telling the model to run the command again, which is wrong for anything that edits files or takes minutes (§4.1, after Manus and Cursor in §2).
**Question asked:** in the index arm of the benchmark, sympy-18211 made 216 calls with 247K characters of tool output against 86 and 66K without the index, compacted three times against none, and the summaries lost what had been found. Does the code index put too much noise into the context, could something like `rtk` make the output brief, and what fixes it?
**Short answer:**
- **The index's own output is not the main noise.** `code_*` tools are 23% of the index arm's tool output. Shell **file reads** (`sed -n`, `cat`, `head`) are the largest share in both arms: 41% with the index, 48% without. The index arm read *more* with the shell than the plain arm (1.07M against 0.85M characters), so the index added to reading instead of replacing it. It did replace some searching (grep and find fell from 346K to 252K).
- **The lever that matches the problem is observation masking:** keep every command and every message, and replace the output of old tool calls with a short placeholder that names a saved copy, once the context is large. In replay it cuts compactions at the benchmark's limit by about half (21 → 12 with the index, 15 → 7 without) and to zero at the interactive limit, and saves a quarter to a third of the time spent compacting and re-prefilling (36 → 26 minutes over 14 instances in the index arm, 26 → 16.5 in the plain arm). The literature finds it matches LLM summarisation at half the cost. It needs one Codex hook.
- **Second, `puffin-code`'s own format:** a 100-line window on `code_show` saves 16% of its output (2.7% of the total), and the nine tool schemas cost 2.1K tokens on every request.
- **`rtk`-style filtering of shell output** is possible through Codex's `PreToolUse` hook (it can rewrite a command, not its output), but the filterable kinds (git, grep, tests) are only 20% of the index arm's output and 40% of the plain arm's. It is Phase 3, behind the other two.
- **This is a problem of unattended work at a small budget,** the benchmark's 44K and Night Shift's 49K. At the interactive limit of 94K the same trajectories would compact 5 times in 4 instances, and not at all with masking.

**Builds on:**
- [PUFFIN_COMPACTION](./DREAMFERENCE_PUFFIN_COMPACTION.md): the limits (§4.1, §4.2) and the rule-built ledger re-injected after a compaction (§10.1). Masking reduces how often compaction runs; the ledger improves what survives when it does. The idea of keeping the last reproduction in the ledger belongs there, not here.
- [PUFFIN_CODE_INDEX](./DREAMFERENCE_PUFFIN_CODE_INDEX.md): `puffin-code`'s MCP tools and their output format.
- [PUFFIN_SWE_BENCH](./DREAMFERENCE_PUFFIN_SWE_BENCH.md) §13.6: the pair measured here.
- The patch series in `codex-patches/`: one more small hook, within the 37,500-byte ceiling the user approved on 2026-10-03 (§4.1).

---

## 1. What was measured

All figures are from the session rollouts (`~/.local/share/dreamference/swe-bench/runs/idx14b-{on,off}/scratch/<instance>/codex-home/sessions/**/rollout-*.jsonl`): every `function_call` paired with its `function_call_output`, characters counted on the output as the model received it.

### 1.1 Where the tool output goes, across 14 instances

| Kind | Index arm: calls | chars | share | Plain arm: calls | chars | share |
|---|---|---|---|---|---|---|
| Shell file reads (`sed -n`, `cat`, `head`, `tail`) | 534 | 1,070,781 | **40.7%** | 483 | 849,347 | **48.0%** |
| `code_show` | 207 | 455,768 | 17.3% | – | – | – |
| Python snippets (`python -c`, heredocs) | 442 | 357,318 | 13.6% | 337 | 162,225 | 9.2% |
| Search and listing (`grep`, `find`, `ls`) | 329 | 252,461 | 9.6% | 428 | 346,149 | 19.6% |
| Test runs | 225 | 142,951 | 5.4% | 314 | 183,103 | 10.3% |
| `git` | 114 | 126,631 | 4.8% | 101 | 187,367 | 10.6% |
| `code_search` | 54 | 114,943 | 4.4% | – | – | – |
| `code_impact`, `code_callers`, `code_refs`, `code_def` | 34 | 41,259 | 1.6% | – | – | – |
| Other | 90 | 71,335 | 2.7% | 126 | 41,382 | 2.3% |
| **All** | **2,029** | **2,633,447** | | **1,789** | **1,769,573** | |

All `code_*` tools together: 612K characters, **23%** of the index arm. Shell: 77%.

A shell read averaged **2,005 characters** (about 50 lines) with the index and 1,758 without: the reads are already window-sized. A smaller window is not the lever; the number of reads is.

Compactions: **27** in the index arm, **15** in the plain arm. Each arm compacted in 8 of its 14 instances.

### 1.2 sympy-18211, the instance that started this

| | Index arm | Plain arm |
|---|---|---|
| Calls / tool output | 216 / 246,871 chars | 86 / 65,999 chars |
| `sed` | 47 calls, 108,227 chars (**44%**) | 21 calls, 33,259 chars (50%) |
| `code_show` | 18 calls, 48,534 chars (20%) | – |
| `grep` | 28 calls, 26,255 chars | 17 calls, 8,646 chars |
| Python snippets | 78 calls, 32,060 chars | 38 calls, 15,771 chars |
| Compactions, at call # | 3, at 66, 127 and 188 | 0 |
| Peak input tokens before each compaction | 44,026 / 43,805 / 43,772 | 42,149 (never compacted) |

**Corrections to what was reported earlier on 2026-10-03:**
- "48K of `code_show`" was right for this instance, but it was 18 calls, not 207. The 207 calls are the whole arm.
- `code_show` was not followed by a `sed` of the same lines. Of 156 `code_show` results that carry a line range, 7 (4%) were followed within three calls by an overlapping `sed` of the same file. Double reading after `code_show` is not a pattern.

### 1.3 The threshold is a cliff, and the index arm starts closer to it

| | Index arm | Plain arm |
|---|---|---|
| Input tokens of the first request (median) | **12,136** | **9,972** |
| Working room under a 44,000 limit | 31.9K | 34.0K |

The index arm carries about **2.1K tokens** of fixed overhead on every request: the nine `code_*` tool schemas and the prompt block. The plain arm's sympy-18211 peaked at 42,149 tokens, 1.9K under the limit. The overhead and the plain arm's margin are the same size. "Three compactions against none" on that instance is partly the cliff, not only a longer trajectory.

### 1.4 Compactions cascade into re-reading

A read is counted as a re-read when it overlaps the lines of an earlier read of the same file (`sed -n a,bp`, `cat`, `code_show`'s range).

| | Index arm | Plain arm |
|---|---|---|
| Characters of file reads | 1,305,762 | 708,951 |
| Re-reads, same context segment, no write to the file in between | 324,623 | 183,881 |
| Re-reads after a write to that file | 69,197 | 46,476 |
| **Re-reads across a compaction** | **353,694** | **118,843** |

Re-reading after a compaction, what the summaries lost, is three times larger in the index arm. Re-reads with the earlier output still in context are common in both arms, but they are mostly partial overlaps (a wider or shifted window): only 1.7% (index) and 1.4% (plain) of all tool output was a read fully contained in one of the last ten outputs with no write since (§4.6).

### 1.5 Two facts about the configuration

- **Per-output truncation is off.** Puffin's model catalog sets `truncation_policy` to `{"mode": "tokens", "limit": 262144}`, the whole context (`puffin-rs/src/lib.rs`). Upstream's catalog uses 10,000 tokens for every model. No output in this pair exceeded about 2K tokens (the largest was 7,381 characters), so it changed nothing here, but a single `cat` of a large file would go into the context whole.
- **Code Mode is on and unused.** The catalog says `tool_mode: code_mode` and the configuration `features.code_mode = true`, yet every one of the 3,818 calls is a direct `function_call`. Code Mode's economy (raw results stay in the JavaScript host and only what the script prints reaches the model) does not apply to this model as it behaves today.

### 1.6 Replaying the trajectories

The rollouts record every item and the server's token count before each request. Replaying them through a model of the context (each item's characters divided by the instance's own characters-per-token ratio, calibrated on the stretch before its first compaction: median 3.77 with the index, 3.50 without) reproduces the plain arm's compactions exactly (15 of 15) and the index arm's at 21 of 27. The difference is what the agent did after compacting, which a replay cannot know. The replay gives the mechanical effect of a policy on the same trajectories; how the agent's behaviour would change is for the A/B of §6.

### 1.7 What a masking move costs the prefix cache (probe, 2026-10-03)

Qwen3.8-27B is a hybrid model: most layers are linear attention (GDN), whose state cannot be rebuilt from a cached prefix of key/value pages. SGLang serves it with `--mamba-radix-cache-strategy extra_buffer` and `--max-mamba-cache-size 96`, so a prefix is reused only up to a position where a state was kept. Where those positions are decides what a masking move costs, so it was measured: a synthetic conversation sent to `/v1/responses` on the idle server (a 6.3K-token system block, then 20 turns of about 1K tokens, one request per turn), then three masking moves, each replacing the oldest turns with one-line placeholders, with turns appended between moves. `cached_tokens` is the server's own report. `scripts/context_budget_cache_probe.py` repeats it; its filler lines are shorter, so its counts differ from the table, and a second run gave the same pattern (move 1: 0 cached; moves 2 and 3: 5,568 and 5,632, the branch point, and nothing later).

| Request | Input tokens | Cached | Re-prefilled | Time |
|---|---|---|---|---|
| Turns 2–20, appending | 8–27K | all but the new turn | ~1–2K each | 0.6–1.5 s |
| Move 1: turns 0–7 masked | 19,069 | **0** | 19,069 | 13.0 s |
| Turns 21–26 on the masked view | 20–25K | all but the new turn | ~1.1K each | 0.7–0.8 s |
| Move 2: turns 0–13 masked | 19,198 | **6,336** | 12,862 | 8.6 s |
| Move 3: turns 0–19 masked | 16,063 | **6,400** | 9,663 | 6.5 s |

What it shows:
- **States are kept at the end of each request**, so appending is cheap and the cache works as Codex sessions need.
- **A move re-prefills from the first masked item, not from the oldest item it newly masks.** Moves 2 and 3 found a state at the branch point move 1 created (6,336 ≈ the end of the system block, where turn 0 began) and nothing later: every state on the masked view lies at a request end, and all of them are after the items a later move masks. So each move costs about the whole tail after the first masked item. v1's time table assumed "from the oldest newly masked item", which under-counts.
- **Move 1 hits only if a state is kept before the first maskable output.** In the probe the branch point preceded the first request's end and nothing was cached. In a Codex session the first output comes after the first request (the initial context) ends, so move 1 hits there if that state is still among the 96 kept. Under the benchmark's load it often is not: of 42 first requests after a compaction, which share that same initial context, 15 found it cached (≥10K) and 17 found less than 2K.
- **The prefill rate** here is about 1,450 tokens per second at 19K, the figure the replay now uses.

The probe was one stream on an idle server. Under three concurrent sessions the kept states compete for the 96 slots, so the cost of a move lies between "from the first masked item" and "from token 0". Phase 1 measures it live (§6).

### 1.8 What a compaction costs here

Measured from the pair's 42 compactions: for the 26 whose compaction request is identifiable in the rollout (it reports nothing cached), the time from the response before it to the `compacted` record; for all 42, the first request after.

| | Median | p25 | p75 |
|---|---|---|---|
| Compaction request (prefill of ~39.5K tokens, then the summary) | **96 s** | 70 s | 132 s |
| First request after it | 8 s | | |

- **The compaction request starts with nothing cached** (those 26), so it prefills the whole history, about 39.5K tokens. Its prefix differs from the session's from the first tokens: it is built with `..Default::default()` (`core/src/compact.rs`), so it carries no tools, and the chat template renders the tools at the head of the prompt.
- **The summary is long:** median 5,366 characters, up to 12,195, generated at decode speed.
- **The first request after** often re-prefills its whole 13K too (above).

So a compaction costs about **104 s** at three instances at once, against the 24 s COMPACTION §9.2 measured for one stream at a 14K context. Night Shift runs one task at a time, so its cost per compaction is lower, and these figures bound it from above.

### 1.9 What the hook can see, and what it cannot

- **Not in the history items:** the base instructions and the tool schemas go into each request separately (`Prompt.base_instructions`, `Prompt.tools`), not through `for_prompt`. They are the fixed overhead: the first request was 12,136 tokens (index arm, median) and 9,972 (plain), of which the history items before it (the initial context and the task) are about 10.8K bytes, about 3K tokens. So **about 9K tokens (index) and 7K (plain) are outside what the hook sees.**
- **Codex's estimator under-counts this model's tokens.** `approx_token_count` is bytes / 4 (`utils/string/src/truncate.rs`); the calibrated ratio here is 3.77 characters per token with the index and 3.50 without (§1.6), so the estimator reads 6–13% low.
- **Together:** v1's high mark, 0.82 × 44,000 = 36K "estimated tokens of history", corresponds to about 36K × 1.1 + 9K ≈ **48K real tokens**, above the 44K limit. Compaction would always fire first and masking never.
- **What the hook can see instead:** `for_prompt_annotated` consumes a clone of the session's `ContextManager`, which carries `token_info`, the server's count for the last request. `get_total_token_usage()` adds the estimate for the items recorded since, and that sum is exactly what auto-compaction compares with the limit (`core/src/session/context_window.rs`). On `puffin resume`, `token_info` is restored from the rollout (`core/src/session/mod.rs`, `last_token_info_from_rollout`). The trigger of §4.1 uses it.

---

## 2. What is published

| Source | Finding | Bearing here |
|---|---|---|
| Lindenbauer et al., *The Complexity Trap* (JetBrains Research, 2025), SWE-agent on SWE-bench Verified, five model settings | **Observation masking** (keep the agent's actions and reasoning, replace older observations with a placeholder, keeping the latest **10** turns) halves cost against an unmanaged context and matches, sometimes slightly beats, LLM summarisation. With Qwen3-Coder 480B: 54.8% solved at $0.61 per instance against 53.4% at $1.29 unmanaged and 53.8% at $0.64 summarised. Summarisation made Qwen3-Coder's trajectories 15% longer. A hybrid (mask first, summarise rarely) cut cost another 7–11%. Observations were about 84% of an average turn. Their masking is a **rolling window applied every turn** (an observation is masked once it is more than 10 turns old), costed at API prices. | The same shape as here: tool output dominates, and summaries lose things. Codex already summarises; masking is the missing first layer. A window that moves every turn changes the prefix every turn, which on this server means a re-prefill per request (§1.7); §4.1's hysteresis is a deliberate departure for that reason. |
| OpenHands condensers | `ObservationMaskingCondenser` and `RecentEventsCondenser` beside the LLM summariser; masking is LLM-free and keeps the history intact. | An open-source agent ships it. |
| OpenCode, `session/compaction.ts` (`prune`) | Walks back through the tool calls, protects the most recent **40,000** tokens of them (`PRUNE_PROTECT`), and erases the outputs of older calls, but only if that frees more than **20,000** tokens (`PRUNE_MINIMUM`); skill outputs are never pruned; a pruned output stays marked as compacted. | A second published hysteresis of the same shape as §4.1's: a protected recent window and a minimum saving per move, so the prefix changes rarely. |
| Anthropic, context editing (`clear_tool_uses_20250919`) | Server-side: when the context passes a threshold, the oldest tool results are replaced by a placeholder; `keep` (default 3 recent tool uses), `clear_at_least` (so clearing is worth the cache it breaks), `exclude_tools`, optional clearing of tool inputs. "One of the safest lightest touch forms of compaction." | The parameters map onto §4.1 one to one; `clear_at_least` is the published form of the hysteresis §4.1 needs. |
| Anthropic, *Writing tools for agents* | Tools should page, select ranges, filter and truncate with sensible defaults; Claude Code caps a tool response at 25,000 tokens; a `response_format` of concise against detailed cut a response from 206 to 72 tokens. | §4.2 and §4.3. |
| Yang et al., *SWE-agent* (NeurIPS 2024) | A file viewer of **100** lines scored 18.0% on SWE-bench Lite against 14.3% for 30 lines and 12.7% for whole files; search capped at 50 results. | §4.3's window size. |
| `rtk` (rtk-ai, Apache-2.0) | A CLI proxy that condenses common commands' output (git, grep, test runners, linters) by rewriting the agent's command through a hook; claims 60–90% on those commands. | §4.5. |
| Manus, *Context Engineering for AI Agents* (2025) | The KV-cache hit rate is the production agent's most important metric: keep the prefix stable and the context append-only. Compression must be **restorable**: content may leave the context only if the agent can get it back (a URL or a file path stays in place of a page or a document). | Masking breaks append-only by design, so it must be rare (§1.7, §4.1). The placeholder names a saved copy, so masking is restorable. |
| Cursor, *Dynamic context discovery* (January 2026) | Long tool outputs are written to files and the agent reads them with `tail` and ranged reads instead of truncating; after summarisation the agent gets a path to the full chat history; MCP tool descriptions are loaded on demand, which **cut total tokens by 46.9%** in runs that called an MCP tool. | The saved-copy placeholder of §4.1. The history-file idea belongs to COMPACTION §10. On-demand tool loading is not available to this model (§3), so §4.3 consolidates tools instead. |
| Liu, *What Does Context Compression Cost an Agent?* (arXiv 2608.16370) | Compression shrinks the context but adds interaction costs that task-completion metrics do not show: re-reads, re-executions and extra turns. | §1.4's re-reads across compactions are such a cost; the A/B of §6 reports re-reads and calls per arm, not only resolved instances. |
| *On Problems of Implicit Context Compression for SWE Agents* (arXiv 2605.11051) | A learned 4× compressor (ICAE) on Qwen3-8B resolved 7 SWE-bench Verified issues against 19 for the uncompressed base, with 40% longer trajectories: paths and URLs were reconstructed wrongly. | Lossy rewriting of what the agent saw is the risk; masking removes whole outputs and keeps the means to restore them, so it cannot misquote a path. |
| SWE-Pruner (arXiv 2601.16746), Squeez (arXiv 2604.04979) | Learned line-level pruners for coding agents: a 0.6B skimmer cut 23–54% of tokens on SWE-bench Verified without losing success; Squeez keeps the smallest verbatim block of a tool output the next step needs (0.86 recall, 92% of tokens removed). | Not pursued: each needs a second model resident beside the main one (host memory is the constraint here) and a call per tool output. Revisit if masking leaves too much. |
| ACON (arXiv 2510.00615), ACM (arXiv 2607.23809) | Compression guidelines optimised from failures (26–54% lower peak tokens), and an agent that decides itself when to compress and can query what it offloaded (SWE-bench Verified 0.530 against 0.489 for ReAct, after post-training a Qwen3.5-9B). | Both need training or a second model; rule-based masking is the first step either would be compared against. |

---

## 3. Where Codex lets Puffin act

Read from `rust-v0.158.0`:

- **Tool output stored in history** is cut to the catalog's `truncation_policy` (middle truncation, `codex-utils-output-truncation`), or to `tool_output_token_limit` from `config.toml` when set (`models-manager/src/model_info.rs:32`).
- **The request sent to the model** is built by `ContextManager::for_prompt()` → `for_prompt_annotated()` (`core/src/context_manager/history.rs:528–543`), after `normalize_history`. It is called for every sampling request (`core/src/session/turn.rs:516`) **and** for the compaction request (`core/src/compact.rs:272`). A hook there covers both; a hook in `turn.rs` alone would send the compaction request the unmasked history, which can be several times the limit.
- **`PreToolUse` hooks can rewrite a command.** `updatedInput` is applied through `with_updated_hook_input` to `exec_command` (`core/src/tools/registry.rs:628`). This is the mechanism `rtk` uses with Claude Code.
- **`PostToolUse` hooks cannot rewrite output.** `updatedMCPToolOutput` is parsed and rejected as unsupported (`hooks/src/engine/output_parser.rs:434`). A hook can only add context or block.
- **Auto-compaction triggers on the token count the server reports** for the last request, plus Codex's estimate for the items recorded since (`get_total_token_usage()`, compared with the limit in `core/src/session/context_window.rs`). If the request is masked, the count is smaller and compaction fires later, which is the intended effect.
- **The hook sees the history, not the whole request.** `for_prompt_annotated` returns the history items; the base instructions and the tools are added to the request elsewhere (`Prompt`, `core/src/client_common.rs`). But the `ContextManager` it consumes carries `token_info`, so the hook can read the same total auto-compaction reads (§1.9), and `token_info` is restored from the rollout on resume.
- **Codex's estimator is bytes / 4** (`approx_token_count`, `utils/string/src/truncate.rs`), described in the source as "a coarse lower bound". Here it reads 6–13% low (§1.9).
- **The compaction request carries no tools** (`Prompt { …, ..Default::default() }` in `core/src/compact.rs`), so its prefix differs from the session's and it is prefilled whole (§1.8).
- **MCP tools cannot be loaded on demand for this model.** Codex defers MCP tool schemas behind its `tool_search` tool only when the model catalog says `supports_search_tool` (`core/src/tools/spec_plan.rs`), which is false for the local model; patch `0020` sends every MCP tool as a plain function. Cursor's 46.9% (§2) is not available without a tool-search of our own.
- **The rollout file keeps everything.** Masking at `for_prompt` changes what is sent, not what is recorded, so the ledger hook (which reads the rollout) and every audit keep the full outputs.

---

## 4. Proposals

### 4.1 Do: observation masking, as one hook in `for_prompt_annotated`

**The rule.**
- Only `function_call_output` items are masked. Commands, the model's messages and the user's messages are kept whole.
- **When.** The size of the view is the quantity auto-compaction compares with the limit `L`: the server's count for the last request plus Codex's estimate for the items recorded since (`get_total_token_usage()`, §1.9, §3). The hook makes a **move** when that size passes a **high mark** and has grown by at least a **minimum step** since the last move. Between moves nothing changes, so the prefix cache holds.
- **A move.** Oldest first, skipping the **10** most recent outputs and every output under **600** characters, the hook masks outputs until the estimated saving (each output's bytes / 4, less its placeholder) covers the size minus a **low mark**. The estimator reads low (§1.9), so a move masks a little more than it must: the safe direction.
- **Defaults**, relative to `L`: high mark **0.85·L**, low mark **0.50·L**, minimum step **16,000** tokens. At the benchmark's 44K that is 37.4K and 22K; at Night Shift's 49,152, 41.8K and 24.6K. Each move re-prefills about everything after the first masked item, whatever it masks (§1.7), so few large moves beat many small ones: in replay this policy makes 29 moves where v1's 0.82/0.55/8K made 48, and is the fastest of seven policies swept at both 44K and 49K, in the expected and the worst case. It compacts twice more at 44K (12 against 10) and once more at 49K. v1's values stay as the comparison arm.
- **The low mark rarely binds.** Low marks of 0.45, 0.50 and 0.55 replay identically: the exemptions (the last 10, outputs under 600 characters) stop a move before it reaches any of them. The step and the high mark are what matter.
- **Never masked:** the 10 most recent outputs (the published window), and any output under 600 characters. The exemption costs little, since 90% of the index arm's output characters are in outputs of 600 characters or more, and it keeps the short, dense outputs that carry results. The observation that decided sympy-18211, `ConditionSet(_gen, …)` from a `python -c`, was under 600 characters and would never have been masked.

**The placeholder names a saved copy, and stays under 200 characters.** When a move first masks an output, the hook writes it to `$CODEX_HOME/masking/outputs/<call_id>.txt` (if the file is not already there). The placeholder is the path, the output's first line (the exit code) and its last line cut to 80 characters, so `FAILED (failures=2)` or a traceback's exception stays in view:
```
[output moved out of context, 2,302 chars: /home/u/.puffin/masking/outputs/call_ca1c38ae56a14f70ae27cdc6.txt]
Exit code: 0
… <last line, at most 80 characters>
```
- **The size matters.** At 44K a 400-character placeholder (v1's four last lines plus a path) gives back most of the gain: 14 compactions instead of 12 in the index arm, 29.9 minutes instead of 26.2. At 200 characters the tables below hold. The command is not repeated: it is in the call item just above.
- **How to use it is said once,** in one sentence the launcher adds to the instructions when masking is on ("an output moved out of context is in the file named; read it with `sed -n` or `tail`, do not re-run the command for it"), not in every placeholder. The instructions are at the head of the prompt and do not change within a session, so the sentence costs its few tokens once per request and nothing in the cache.
- **Why not "run it again"** (v1's wording): re-running is wrong for anything that edited a file, installed something or ran a long test suite, and its answer may differ from what the model saw. Reading the saved copy is exact, cheap and has no side effects. This is Manus's restorable compression and Cursor's output-as-file (§2).
- **Readable wherever commands run:** the read-only and workspace-write sandboxes leave the file system readable, and a SWE-bench container mounts its own `CODEX_HOME` (checked in Phase 1, §6).
- **Size:** the copies are a subset of what the rollout already holds (the index arm's whole tool output was 2.6 MB over 14 instances). They are pruned with the boundary files (below).
- `call_id`s are unique (`call_` and 96 random bits; 3,818 of 3,818 distinct in the pair), so one flat folder serves every session.

**The boundary is stored, not replayed.** v1 recomputed the boundary from the history on every request so as to store nothing. With the trigger on the server's count that cannot work: past counts are not in the history. So the hook keeps a small state per history segment, persisted at `$CODEX_HOME/masking/<key>.json`: the set of masked `call_id`s and the size after the last move. The key is the `call_id` of the segment's first `function_call`, which needs no thread id (the hook is not given one) and changes when a compaction rewrites the history.
- An item is shown as a placeholder if and only if its `call_id` is in the set, so the view is stable between moves by construction.
- `puffin resume` restores the same history and `token_info` (§3), so it reads the same file and sends the same view.
- After a compaction or a rollback the masked ids are gone from the history and are ignored; the new segment starts empty.
- A subagent has its own calls, so its own key. A forked thread starts with its parent's history, so it shares the parent's key and file until one of them compacts: a move made in either changes the other's next view (one extra re-prefill, not a wrong answer). Forks are rare in `puffin`; if they stop being rare, the key gains the thread id, which the patch would then have to pass in.
- Files older than 30 days are deleted at launch, with the outputs they name, as cave mode's session files are.
- A sandboxed command started from the home folder can write `$CODEX_HOME` (AIRGAPPED §14.5), so it could edit or delete these files. The worst it can do is unmask outputs (the context grows and compacts sooner) or mask more (restorably). Nothing here is a security boundary.

**Two output forms.** `exec_command` outputs are text that begins `Exit code: …`; MCP outputs (the `code_*` tools) are a list of content items whose first item is Codex's `Wall time: … Output:` header. The placeholder keeps the first line of the text in the first case and the tool's own first line (for `code_show`, the `show <symbol> (<path>:<a>-<b>)` header) in the second, followed by the last line either way; the saved copy of an MCP output is its text items joined.

**Where.** At the end of `for_prompt_annotated`, before the items are returned: the size is read from `self` (`get_total_token_usage`) and passed with the items to `puffin_masking::apply`. The rule lives in a **leaf crate**, `puffin-rs/masking/` (`puffin-masking`), with no Codex or launcher dependency and its own tests, which `codex-rs/core` depends on through one `Cargo.toml` line, as patch `0020` does for `puffin-rs/tools` and `0019` for `puffin-rs/airgapped`: Codex's core must not depend on the launcher. The launcher, in the same process, sets the policy and `CODEX_HOME` once at start through a setter the leaf crate exposes, with `L` from the compaction limit it already computes (`puffin-rs/src/compaction.rs`); with no policy set, `apply` does nothing. The same hook serves every sampling request and the compaction request (§3), so a compaction also prefills the smaller, masked history.

**What it costs in the patch budget.** The series is 33,686 bytes against a cap of 33,750 in `test_the_patches_stay_small`. On 2026-10-03 the user approved a ceiling of 37,500 for this hook (~0.9 KB as first drafted) and PUFFIN_APPS' hooks (~2.4 KB). Reading the size from `self` adds a line to the draft; the cap is raised by the patch's size as written, with a line saying so.

**Configuration.** `puffin_mask_tool_output = true` (default once Phase 1 passes), and the four numbers as keys under `[puffin_mask]` for measurement. Night Shift and SWE-bench take the same keys.

**Replayed effect, compactions over 14 instances** (`context_budget_replay.py mask`):

| Limit | Index arm: unmasked | 0.85/0.50/16K | 0.82/0.55/8K | Plain arm: unmasked | 0.85/0.50/16K | 0.82/0.55/8K |
|---|---|---|---|---|---|---|
| 44,000 (the benchmark) | 21 | **12** | 10 | 15 | **7** | 6 |
| 49,152 (Night Shift's `task_context`) | 17 | **9** | 8 | 11 | **6** | 5 |
| 65,536 | 11 | **3** | 3 | 6 | **2** | 2 |
| 94,144 (interactive: 60% of the KV pool) | 5, in 4 instances | **0** | 0 | 2, in 2 instances | **0** | 0 |

The rows above 44K are **upper bounds**: the trajectories were recorded at 44K and include the re-reading that followed each compaction there (354K characters in the index arm), which a run at a higher limit would not have made.

**What it costs in time.** A compaction is taken at its measured 104 s (§1.8), and a move at the prefill of everything from the masked item to the end at 1,450 tokens per second (§1.7). The re-prefill column gives two bounds: from the oldest item the move newly masks (v1's model) and from the first masked item (what the probe shows when the branch state is kept):

| 14 instances at 44K | Compactions | Moves | Re-prefill | Compacting | **Total** |
|---|---|---|---|---|---|
| Index arm, unmasked | 21 | 0 | 0 | 36.4 min | **36.4 min** |
| Index arm, 0.85/0.50/16K | 12 | 29 | 5.1–5.4 min | 20.8 min | **25.9–26.2 min** |
| Index arm, 0.82/0.55/8K | 10 | 48 | 7.1–9.8 min | 17.3 min | 24.4–27.1 min |
| Plain arm, unmasked | 15 | 0 | 0 | 26.0 min | **26.0 min** |
| Plain arm, 0.85/0.50/16K | 7 | 21 | 4.2–4.4 min | 12.1 min | **16.3–16.5 min** |
| Plain arm, 0.82/0.55/8K | 6 | 36 | 6.2–8.5 min | 10.4 min | 16.6–18.9 min |

At Night Shift's 49,152, with the default policy, the index arm goes from 29.5 to 20.2–21.1 minutes and the plain arm from 19.1 to 14.7–15.5 (v1's policy: 21.9–26.4 and 15.0–18.4).

- **Masking saves a quarter to a third of the time** spent compacting and re-prefilling, before counting the re-reading that compactions cause (354K characters in the index arm, §1.4), which the replay cannot count and the A/B can. v1 found it "about even" only because it took a compaction at 24 s.
- **The worst case still wins.** If under load no state survives before the first masked item, every move re-prefills from token 0, about 12K tokens (8 s) more per move: the index arm's total becomes 30.4 minutes against 36.4 (v1's policy: 33.9) and the plain arm's 19.0 against 26.0 (23.2). Fewer moves is what keeps the worst case cheap.
- **Without the minimum step** the same marks moved the boundary 256 times (v1's first replay) and cost 25 minutes of re-prefill: the step is what makes masking affordable on this server.

**Masking old long commands too** (heredocs over 600 characters outside the last 10, which hold whole scripts and file contents the model wrote) takes the default policy's 44K row to **10** compactions with the index and **6** without, and the totals to 21.8–22.6 and 13.9–14.6 minutes; at 49K, to 6 and 5 compactions, 14.9–16.4 and 12.4–13.5 minutes. The model loses the exact text it wrote, which the file still has, and with the saved copy the command's text is restorable too. It stays Phase 2, measured after Phase 1, because it changes what the model sees of its own actions, which the published results do not cover.

### 4.2 Do: put a cap on a single tool output

Set `truncation_policy.limit` in the catalog to **8,000** tokens (about 30,000 characters), not the whole context. Upstream uses 10,000, Claude Code 25,000. It changes nothing measured here (the largest output was about 2K tokens) and stops one `cat` of a large file from filling a 44K budget. One line in `puffin-rs/src/lib.rs`, and a test.

**Later, the same cap without the loss.** Codex's truncation cuts the middle of the output when it is recorded, so the cut part is gone from the history and the rollout. Once the hook of §4.1 exists it can do better, as Cursor does (§2): leave `truncation_policy` as a backstop well above 8,000 tokens, and have the hook show any output over 8,000 tokens as its first and last 40 lines plus the path of its saved copy, from the first request that carries it. That view never changes afterwards, so it costs the prefix cache nothing, and the full text stays one ranged read away. Phase 2, since no output measured here came near the cap.

### 4.3 Do: `puffin-code`'s own output

`puffin-code` is ours, so its format is the cheapest lever, but a small one: all `code_*` output is 23% of the index arm.

- **`code_show` pages at 100 lines by default** (SWE-agent's measured optimum), with a last line naming the rest: `… lines 1962–2026 not shown; code_show name=solveset offset=100`. The `offset` and `limit` arguments already exist; only the default changes. 26 of 207 shows exceeded 100 lines; the cap saves 16% of `code_show`'s output, **2.7%** of the arm's. A 60-line default would save 30% (5%) and is a Phase 2 measurement, since a shorter window cost SWE-agent more than it saved.
- **Long docstrings fold** after 12 lines, with the count of lines folded. `solveset`'s docstring was 35 of its 165 lines.
- **The fixed overhead: 2.1K tokens on every request**, mostly the nine tool schemas (the prompt block differs by only about 500 characters per instance). Shorter tool descriptions and one shared argument schema are the first half. Loading the schemas on demand, which cut Cursor's tokens by 46.9% (§2), is not available: Codex defers MCP tools only for a model with `supports_search_tool` (§3). The available lever is fewer tools, and the call counts point to three: `code_search`, `code_show`, and one navigation tool with a `kind` argument (`def`, `refs`, `callers`, `callees`, `impl`, `impact`), since the arm called `code_show` 207 times, `code_search` 54, `code_impact` 17, `code_callers` 9, `code_def` and `code_refs` 4 each, and `code_callees`, `code_impl` and `code_outline` never. Schemas sit at the head of the prompt, so any change to them is made once per build, never within a session (Manus: "mask, don't remove").
- **`code_search` row caps** are unmeasured (54 calls, 2,128 characters on average); Phase 2.
- **`code_outline` before reading a whole file.** `cat` of whole files was 203K characters in the index arm and `code_outline` was never called. One sentence in the prompt block (CODE_INDEX's "when" list) names it for a file not yet seen.

### 4.4 Measure, do not assume: why the index arm read more

The index arm made more calls (2,029 against 1,789) and read more with the shell, not less. This spec does not know why. The candidates are the wrong-layer start that SWE_BENCH §13.6 found in sympy-18211 (a search with the issue's words leading into the wrong module, addressed by the "reproduce first, follow the traceback" change proposed there), and compaction itself (re-reading after compaction is 354K characters in the index arm). The A/B of §6 separates them, since masking removes most compactions and leaves the start alone.

### 4.5 Later: `rtk`-style filtering of shell output

**What is reachable.** git, grep/find/ls and test runs are 20% of the index arm's tool output and 40% of the plain arm's. If they were halved, as `rtk` claims for such commands, the saving would be **8–12%** and about **20%** respectively. File reads, the largest share, must stay exact because the model edits from them, so they are not filterable. Compare the 50% fewer compactions of §4.1.

**How it would work in Codex.** A `PreToolUse` hook rewrites the command: `git diff` → a condensed diff, `grep -rn …` → grouped by file with a row cap, `python -m pytest` → failures only. Output cannot be filtered after the fact (`PostToolUse` cannot replace it, §3). Puffin would register the hook the way it registers the ledger hook.

**`rtk` itself, before adopting it:**
- **It has no Codex target.** `rtk init --agent` knows Claude Code, Cursor, Windsurf, Cline, Kilo Code, Antigravity, Kimi, Pi, Hermes, Droid and Vibe. Puffin would write the hook itself.
- **Telemetry is opt-in** and off by default ("anonymous usage metrics once per day" after consent). Inside a sandboxed command no consent prompt may ever appear, and the setting must be pinned off in its configuration, or only `rtk pipe` used. The egress audit would catch a slip.
- **Its usage database lives under the home folder.** Under the workspace-write sandbox, and in the benchmark's `/testbed` containers, that path may not be writable; `rtk proxy`/tracking must not fail the command.
- **Licence: Apache-2.0**, which can be bundled with AGPL-3.0 code; its TOML filters could be copied with attribution.
- **Its formats differ from what the model expects** from `git diff` or pytest. `rtk init` writes a prompt block for that reason; a 27B model's handling of condensed output has to be measured, not assumed.

The alternative is a small `puffin-filter` of our own with the three or four filters that matter here (git, grep, the test runners these repositories use). Decide after Phase 1 shows what is left.

### 4.6 Not proposed

- **A proxy between Codex and the model server that rewrites the history.** It would need no patch, but it is a second process on every request, has to parse a streaming protocol, and gains nothing over the hook of §4.1.
- **Remembering in `puffin-code` what it has already shown.** It cannot see compactions or masking, so it would refuse a re-read the model needs.
- **A hook that answers "already shown above" for a read the model has just seen.** Reads fully inside one of the last ten outputs with no write since were 1.7% of the index arm's output (45K characters in 42 calls) and 1.4% of the plain arm's. Not worth a hook.
- **Rewriting tool output after the fact.** Unsupported in this Codex (§3).
- **Changing the compaction summary.** That is COMPACTION §10.1–§10.2's subject, including keeping the last reproduction in the ledger.
- **The published per-turn rolling window** (§2: an output is masked once it is more than 10 turns old). On this server it would change the prefix on every request, and each change re-prefills everything after the first masked item (§1.7): 8–14 s on every request at 44K, which over a 200-call session costs more than all its compactions.
- **A learned pruner or compressor** (SWE-Pruner, Squeez, ACON; §2). Each needs a second model resident beside the main one, and host memory is this machine's constraint; implicit compression has also been shown to misquote paths (§2). The comparison point for any of them is rule-based masking, so it comes first.
- **Raising the benchmark's limit by running fewer instances at once.** It would remove most compactions (§4.1's 65K row), but it changes the benchmark, not Puffin; the benchmark's 44K stands in for Night Shift's 49K, which is the budget this spec is for.

---

## 5. Scope

| Budget | Where it applies | Bearing |
|---|---|---|
| 44,000 tokens | the SWE-bench runs at three instances at once (KV pool 133K) | where the problem was found |
| 49,152 tokens | Night Shift's `task_context` | **the budget this spec is for** |
| 94,144 tokens | interactive sessions (60% of the KV pool, COMPACTION §4.2) | replay: 5 compactions in 4 of 14 instances without masking, none with it |

Interactive sessions are not the target, and nothing here should be read as a general `puffin` problem. Masking still applies there and costs nothing until the high mark is reached. Past it, at 94K, the replay removes the index arm's 5 compactions (8.7 minutes at 104 s each) with 13 moves (3.9–6.8 minutes of re-prefill), and the plain arm's 2 (3.5 minutes) with 8 moves (2.9–4.7 minutes): no worse on time, and nothing summarised away. One stream at a time compacts faster than the 104 s measured at three (§1.8), so on time the interactive case is about even; the gain there is what survives. Whether masking is on by default for interactive sessions is decided after Phase 1, not assumed.

---

## 6. Phases and tests

**Phase 0, replay and probe (done, 2026-10-03).** §1 and the tables of §4.1, reproducible with `scripts/context_budget_replay.py {kinds,rereads,mask} <run dir>…` (reads the rollout files, needs nothing running; `mask --policy HIGH,LOW,STEP` replays any policy). The cache probe of §1.7 and the compaction timings of §1.8. In Phase 1 the same measurements become a `puffin-admin swe-bench report --context` section, so every later run reports its per-kind output, compactions, moves and re-reads.

**Phase 1, build.**
- §4.1, the leaf crate `puffin-rs/masking/` with unit tests:
  - nothing is masked below the high mark, or before the minimum step since the last move;
  - a move masks oldest first until the estimated saving covers the size minus the low mark;
  - the 10 most recent outputs and outputs under 600 characters are never masked;
  - the placeholder's path, first line and last line, for both output forms, and its length under 200 characters;
  - the saved copy is written once and holds the output exactly;
  - the state round-trips through its file, a new segment key starts empty, and ids no longer in the history are ignored;
  - with no policy set, `apply` changes nothing.
- The patch, with the cap raised by its size as written. The draft on branch `ctx/budget` (`1de046d`) keeps its hook site, patch shape, the launcher's resolution of `L` and the 8,000-token cap of §4.2; its trigger, its stateless replay and its "run it again" placeholder are replaced as above.
- **Live checks in a real session, before the A/B:**
  - the trigger fires before compaction: at each move, log the size the hook saw and the server's count for the request that follows, and check the move happens below `L`;
  - the cost of a move under load: `cached_tokens` on the request after each move, with three sessions running, against the bounds of §1.7;
  - `puffin resume` of a masked session sends the same view (its first request after resume hits the cache at the previous end);
  - a saved copy can be read from the workspace-write sandbox and from inside a SWE-bench container;
  - `prompt_debug` shows the masked view.
- §4.2: the catalog's limit and a test.
- §4.3: the 100-line default and folding in `puffin-code` (`cargo test --locked`), shorter schemas, the overhead re-measured from the first request's input tokens.

**Phase 1 acceptance, the A/B.** On a rebuilt `puffin`, the same 14 instances at 44K, four arms: index with masking (0.85/0.50/16K), index without, plain without, and the plain arm repeated for noise. If the machine's time allows, a fifth: index with v1's 0.82/0.55/8K. Report per arm: compactions, moves, `cached_tokens` after each move, re-read characters across compactions, reads of saved copies and re-runs of masked commands, tool output per kind, agent time, resolved. Masking ships on by default for Night Shift and the benchmark if compactions fall by at least a third and neither resolved instances nor agent time get worse beyond the plain arm's repeat-to-repeat spread. The number of resolved instances is reported, not claimed: at 14 instances a difference of two is noise (SWE_BENCH §13.5).

**Phase 2.** Masking old long commands (§4.1); the lossless cap of §4.2; a 60-line `code_show`; `code_search` row caps; three tools instead of nine if the call counts hold.

**Phase 3.** §4.5, after Phase 1 shows what remains.

---

## 7. Open questions

1. **Does masking change the agent's behaviour?** The published result says no worse than summarisation; with a 27B model and Codex's prompt, only the A/B shows it. A model that sees a placeholder may re-run commands instead of reading the saved copy; the A/B counts both.
2. **The cost of a move under load.** §1.7 measured one stream on an idle server: a move re-prefills from the first masked item when the branch state survives, from token 0 when it does not. With three sessions sharing 96 kept states, which happens is measured live in Phase 1. Both bounds keep masking ahead of compaction on time (§4.1).
3. **Subagents and `/compact` typed by the user** go through the same `for_prompt`; they are covered by construction, but not yet looked at.
4. **What the 2.1K-token overhead is.** It is attributed to the tool schemas by elimination. A quick check on 2026-10-03 was inconclusive: in a scratch directory with no index, `puffin exec` with `puffin_code_tools = false` sent *more* tokens (15,172) than the default (13,124), and `features.code_mode` on and off sent the same 13,124 (the catalog's `tool_mode` may override the flag). The method that answers it: in an indexed repository, record the first request with and without the tools, and with each prompt-block variant.
5. **The interactive default.** At 94K masking is about even on time and keeps more than a summary does (§5). Whether interactive sessions mask by default is decided after Phase 1.

---

## 8. As built (2026-10-03)

Branch `ctx/budget`, on `main` at the merge of this spec's v2.

- **§4.1, masking.** The leaf crate `puffin-rs/masking/` (`puffin-masking`, serde only) and patch `0021-observation-masking` (1,011 bytes: one dependency line in `codex-rs/core/Cargo.toml`, and four lines at the end of `for_prompt_annotated` that read `get_total_token_usage(false)` before normalising and pass it with the items to `puffin_masking::apply`; `false` is the session's default for `server_reasoning_included`, and this model sends no reasoning). The cap in `test_the_patches_stay_small` is raised to 34,750 (34,697 after). The rule is §4.1's: a move when the size passes the high mark and the size after the last move plus the step; oldest first, skipping the last 10 candidates and outputs under 600 characters, until the bytes/4 saving covers the size less the low mark; state at `$CODEX_HOME/masking/<first call_id>.json`, copies at `masking/outputs/<call_id>.txt`, written once; the placeholder within 200 characters (first line clipped to 60, last to 80, dropped before the limit is passed); segments older than 30 days pruned at launch with their copies. 11 unit tests, one per item of §6's list.
- **The launcher** (`puffin-rs/src/mask.rs`): off by default, `DREAMFERENCE_PUFFIN_MASK` then `puffin_mask_tool_output`; `[puffin_mask]` takes `high_percent`, `low_percent`, `min_step`, `keep_recent`, `min_chars` (v1's arm is `82`/`55`/`8000`); `L` is the last `-c model_auto_compact_token_limit` on the command line, then `config.toml`, then the window. When on, one paragraph joins the prompt (`prompt::Parts::masking`) saying to read the named file with `sed -n` or `tail` and not to re-run the command. Launcher tests: 143 passed, 1 ignored. `codex-core` checks with `0021` applied.
- **§4.2:** `truncation_policy.limit` is `min(8,000, window)` (`TOOL_OUTPUT_TOKEN_LIMIT`), with a test.
- **§4.3, `puffin-code`:** `show` prints 100 lines a page (`SHOW_LINES`) with a last line `… lines A-B not shown; next: offset N`, in the tool and in the shell (`puffin-code show <name> --offset N`); a Python docstring longer than 12 lines folds after them with a line naming the folded range; the nine tool descriptions went from 964 to 543 characters (the per-tool argument schemas are unchanged: MCP gives each tool its own, so "one shared schema" is not available); both prompt blocks name `outline` before reading a file not yet seen. `cargo test --locked`: 128 passed.
- **Not done:** the live checks of §6 (they need `codex build`, which no one ran on this branch) and the A/B.

## Sources

- Lindenbauer, Slinko, Felder, Bogomolov, Zharov, [*The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management*](https://arxiv.org/abs/2508.21433) (arXiv 2508.21433), and JetBrains Research, [*Cutting Through the Noise: Smarter Context Management for LLM-Powered Agents*](https://blog.jetbrains.com/research/2025/12/efficient-context-management/) (December 2025).
- OpenCode, [`packages/opencode/src/session/compaction.ts`](https://github.com/sst/opencode/blob/dev/packages/opencode/src/session/compaction.ts) (read 2026-10-03).
- OpenHands, [Context Condenser](https://docs.openhands.dev/sdk/guides/context-condenser) and [Condenser architecture](https://docs.openhands.dev/sdk/arch/condenser).
- Anthropic, [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing); [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents).
- Manus (Yichao Ji), [*Context Engineering for AI Agents: Lessons from Building Manus*](https://medium.com/@peakji/context-engineering-for-ai-agents-lessons-from-building-manus-71883f0a67f2) (July 2025).
- Cursor, [*Dynamic context discovery*](https://cursor.com/blog/dynamic-context-discovery) (January 2026).
- Liu, [*What Does Context Compression Cost an Agent? Interaction Costs Unrevealed by Task-Completion Metrics*](https://arxiv.org/abs/2608.16370) (arXiv 2608.16370).
- [*On Problems of Implicit Context Compression for Software Engineering Agents*](https://arxiv.org/abs/2605.11051) (arXiv 2605.11051).
- [*SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents*](https://arxiv.org/abs/2601.16746) (arXiv 2601.16746); [*Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents*](https://arxiv.org/abs/2604.04979) (arXiv 2604.04979).
- [*ACON: Optimizing Context Compression for Long-horizon LLM Agents*](https://arxiv.org/abs/2510.00615) (arXiv 2510.00615); [*ACM: Agentic Context Management for Long Horizon Tasks*](https://arxiv.org/abs/2607.23809) (arXiv 2607.23809).
- Yang et al., [*SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*](https://arxiv.org/abs/2405.15793) (NeurIPS 2024).
- [rtk-ai/rtk](https://github.com/rtk-ai/rtk) (Apache-2.0) and its [site](https://www.rtk-ai.app/); `rtk 0.46.0` as installed on this machine (`rtk --help`, `rtk init --help`).
- Codex `rust-v0.158.0`: `core/src/context_manager/history.rs`, `core/src/session/turn.rs`, `core/src/session/mod.rs`, `core/src/session/context_window.rs`, `core/src/compact.rs`, `core/src/client_common.rs`, `core/src/tools/registry.rs`, `core/src/tools/spec_plan.rs`, `core/src/tools/context.rs`, `hooks/src/engine/output_parser.rs`, `utils/string/src/truncate.rs`, `models-manager/src/model_info.rs`, `models-manager/models.json`.
- The model server's launch arguments (`--mamba-radix-cache-strategy extra_buffer`, `--max-mamba-cache-size 96`, `--chunked-prefill-size 8192`), read from the running container on 2026-10-03.
