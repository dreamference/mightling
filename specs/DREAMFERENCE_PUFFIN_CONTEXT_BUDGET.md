# Puffin Context Budget — what fills the context, and how to keep it small

**Status:** proposed on 2026-10-03. Nothing here is built. §1 is measured from the rollouts of the SWE-bench pair `idx14b-on` / `idx14b-off` (14 instances each, same `puffin` build `runtime_hash bd978d3ede04`, Qwen3.8-27B, compaction limit 44,000 tokens). §3 is read from the pinned Codex source (`rust-v0.158.0`). §2 and the sources are published work. The effect figures in §4 come from replaying the recorded trajectories (§1.6), not from new runs.
**Question asked:** in the index arm of the benchmark, sympy-18211 made 216 calls with 247K characters of tool output against 86 and 66K without the index, compacted three times against none, and the summaries lost what had been found. Does the code index put too much noise into the context, could something like `rtk` make the output brief, and what fixes it?
**Short answer:**
- **The index's own output is not the main noise.** `code_*` tools are 23% of the index arm's tool output. Shell **file reads** (`sed -n`, `cat`, `head`) are the largest share in both arms: 41% with the index, 48% without. The index arm read *more* with the shell than the plain arm (1.07M against 0.85M characters), so the index added to reading instead of replacing it. It did replace some searching (grep and find fell from 346K to 252K).
- **The lever that matches the problem is observation masking:** keep every command and every message, and replace the output of old tool calls with a one-line placeholder once the context is large. In replay it cuts compactions at the benchmark's limit by about half (21 → 10 with the index, 15 → 6 without) and to zero at the interactive limit. The literature finds it matches LLM summarisation at half the cost. It needs one Codex hook.
- **Second, `puffin-code`'s own format:** a 100-line window on `code_show` saves 16% of its output (2.7% of the total), and the nine tool schemas cost 2.1K tokens on every request.
- **`rtk`-style filtering of shell output** is possible through Codex's `PreToolUse` hook (it can rewrite a command, not its output), but the filterable kinds (git, grep, tests) are only 20% of the index arm's output and 40% of the plain arm's. It is Phase 3, behind the other two.
- **This is a problem of unattended work at a small budget,** the benchmark's 44K and Night Shift's 49K. At the interactive limit of 94K the same trajectories would compact 5 times in 4 instances, and not at all with masking.

**Builds on:**
- [PUFFIN_COMPACTION](./DREAMFERENCE_PUFFIN_COMPACTION.md): the limits (§4.1, §4.2) and the rule-built ledger re-injected after a compaction (§10.1). Masking reduces how often compaction runs; the ledger improves what survives when it does. The idea of keeping the last reproduction in the ledger belongs there, not here.
- [PUFFIN_CODE_INDEX](./DREAMFERENCE_PUFFIN_CODE_INDEX.md): `puffin-code`'s MCP tools and their output format.
- [PUFFIN_SWE_BENCH](./DREAMFERENCE_PUFFIN_SWE_BENCH.md) §13.6: the pair measured here.
- The patch series in `codex-patches/`: one more one-line hook (§4.1).

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

---

## 2. What is published

| Source | Finding | Bearing here |
|---|---|---|
| Lindenbauer et al., *The Complexity Trap* (JetBrains Research, 2025), SWE-agent on SWE-bench Verified, five model settings | **Observation masking** (keep the agent's actions and reasoning, replace older observations with a placeholder, keeping the latest **10** turns) halves cost against an unmanaged context and matches, sometimes slightly beats, LLM summarisation. With Qwen3-Coder 480B it raised the solve rate by 2.6% while being 52% cheaper. Summarisation made Gemini 2.5 Flash's trajectories 15% longer. A hybrid (mask first, summarise rarely) cut cost another 7–11%. Observations were about 84% of an average turn. | The same shape as here: tool output dominates, and summaries lose things. Codex already summarises; masking is the missing first layer. |
| OpenHands condensers | `ObservationMaskingCondenser` and `RecentEventsCondenser` beside the LLM summariser; masking is LLM-free and keeps the history intact. | An open-source agent ships it. |
| Anthropic, context editing (`clear_tool_uses_20250919`) | Server-side: when the context passes a threshold, the oldest tool results are replaced by a placeholder; `keep` (default 3 recent tool uses), `clear_at_least` (so clearing is worth the cache it breaks), `exclude_tools`, optional clearing of tool inputs. "One of the safest lightest touch forms of compaction." | The parameters map onto §4.1 one to one; `clear_at_least` is the published form of the hysteresis §4.1 needs. |
| Anthropic, *Writing tools for agents* | Tools should page, select ranges, filter and truncate with sensible defaults; Claude Code caps a tool response at 25,000 tokens; a `response_format` of concise against detailed cut a response from 206 to 72 tokens. | §4.2 and §4.3. |
| Yang et al., *SWE-agent* (NeurIPS 2024) | A file viewer of **100** lines scored 18.0% on SWE-bench Lite against 14.3% for 30 lines and 12.7% for whole files; search capped at 50 results. | §4.3's window size. |
| `rtk` (rtk-ai, Apache-2.0) | A CLI proxy that condenses common commands' output (git, grep, test runners, linters) by rewriting the agent's command through a hook; claims 60–90% on those commands. | §4.5. |

---

## 3. Where Codex lets Puffin act

Read from `rust-v0.158.0`:

- **Tool output stored in history** is cut to the catalog's `truncation_policy` (middle truncation, `codex-utils-output-truncation`), or to `tool_output_token_limit` from `config.toml` when set (`models-manager/src/model_info.rs:32`).
- **The request sent to the model** is built by `ContextManager::for_prompt()` → `for_prompt_annotated()` (`core/src/context_manager/history.rs:528–543`), after `normalize_history`. It is called for every sampling request (`core/src/session/turn.rs:516`) **and** for the compaction request (`core/src/compact.rs:272`). A hook there covers both; a hook in `turn.rs` alone would send the compaction request the unmasked history, which can be several times the limit.
- **`PreToolUse` hooks can rewrite a command.** `updatedInput` is applied through `with_updated_hook_input` to `exec_command` (`core/src/tools/registry.rs:628`). This is the mechanism `rtk` uses with Claude Code.
- **`PostToolUse` hooks cannot rewrite output.** `updatedMCPToolOutput` is parsed and rejected as unsupported (`hooks/src/engine/output_parser.rs:434`). A hook can only add context or block.
- **Auto-compaction triggers on the token count the server reports** for the last request. If the request is masked, the count is smaller and compaction fires later, which is the intended effect.
- **The rollout file keeps everything.** Masking at `for_prompt` changes what is sent, not what is recorded, so the ledger hook (which reads the rollout) and every audit keep the full outputs.

---

## 4. Proposals

### 4.1 Do: observation masking, as one hook in `for_prompt_annotated`

**The rule.**
- Only `function_call_output` items are masked. Commands, the model's messages and the user's messages are kept whole.
- Nothing is masked until the masked view of the history passes a **high mark**. Then the oldest maskable outputs are masked, oldest first, until the view is under a **low mark**, and nothing more is masked until the view has grown by a **minimum step** since. The boundary moves rarely, so the model server's prefix cache holds between moves.
- Never masked: the **10** most recent outputs (the published window), and any output under **600** characters. The exemption costs little, since 90% of the index arm's output characters are in outputs of 600 characters or more, and it keeps the short, dense outputs that carry results. The observation that decided sympy-18211, `ConditionSet(_gen, …)` from a `python -c`, was under 600 characters and would never have been masked.
- The placeholder keeps the output's first line (the exit code) and its last **4** lines, so `FAILED (failures=2)` and a traceback's last line survive:
  ```
  [puffin: output of call 87 (sed -n '996,1060p' sympy/solvers/inequalities.py) removed to save context, 2,302 chars; run it again if you need it]
  Exit code: 0
  …
  <last 4 lines>
  ```
- The view is measured with Codex's own token estimator (the one its truncation uses), because the hook has no server count. Defaults, relative to the compaction limit `L`: high mark **0.82·L**, low mark **0.55·L**, minimum step **8,000** tokens. At the benchmark's 44K these are 36K and 24K.

**Where.** One line at the end of `for_prompt_annotated`: `puffin_launcher::masking::apply(&mut items)`, with the rule in a module of the launcher crate (a leaf module with its own tests, like `airgapped`). The launcher sets the policy once at start from the compaction limit it already computes (`puffin-rs/src/compaction.rs`). The hook is stateless: whether an item is masked is decided from the history alone, so a resumed session and a subagent get the same view, and `prompt_debug` shows the masked view, which is what the model sees.

**What it costs in the patch budget.** The series is 33,686 bytes against a cap of 33,750 in `test_the_patches_stay_small`. A hook of 300–500 bytes needs the cap raised explicitly, as every hook before it did.

**Configuration.** `puffin_mask_tool_output = true` (default once Phase 1 passes), and the four numbers as keys under `[puffin_mask]` for measurement. Night Shift and SWE-bench take the same keys.

**Replayed effect, compactions over 14 instances:**

| Limit | Index arm, unmasked | masked | Plain arm, unmasked | masked |
|---|---|---|---|---|
| 44,000 (the benchmark) | 21 | **10** | 15 | **6** |
| 49,152 (Night Shift's `task_context`) | 17 | **7** | 11 | **5** |
| 65,536 | 11 | **3** | 6 | **2** |
| 94,144 (interactive: 60% of the KV pool) | 5, in 4 instances | **0** | 2, in 2 instances | **0** |

Masking old long commands too (heredocs over 600 characters outside the last 10, which hold whole scripts and file contents the model wrote) takes the 44K row to **6** and **3**. Its risk is that the model loses the exact text it wrote, which the file still has. It is Phase 2, optional.

**What it costs in time.** Each move of the boundary makes the model server prefill again from the oldest newly masked item to the end (prefill about 1,700 tokens per second). Compaction is taken at about 24 s each (COMPACTION §9.2), not counting the re-reading afterwards:

| 14 instances at 44K | Compactions | Mask moves | Re-prefill | Compaction time | Total |
|---|---|---|---|---|---|
| Index arm, unmasked | 21 | 0 | 0 | 8.4 min | 8.4 min |
| Index arm, masked | 10 | 49 | 6.2 min | 4.0 min | 10.2 min |
| Plain arm, unmasked | 15 | 0 | 0 | 6.0 min | 6.0 min |
| Plain arm, masked | 6 | 38 | 5.5 min | 2.4 min | 7.9 min |

On direct time masking is about even (1.8–1.9 minutes more over 14 instances, roughly 8 s per instance). Its gain is in what survives: half the compactions, and none of the summary's losses for the outputs it removes. The 354K characters of re-reading after compactions in the index arm (§1.4) are where any time is won back; the replay cannot count that, the A/B can. Without the minimum step the same rule moved the boundary 256 times and cost 25 minutes, which is why the step is part of the rule.

### 4.2 Do: put a cap on a single tool output

Set `truncation_policy.limit` in the catalog to **8,000** tokens (about 30,000 characters), not the whole context. Upstream uses 10,000, Claude Code 25,000. It changes nothing measured here (the largest output was about 2K tokens) and stops one `cat` of a large file from filling a 44K budget. One line in `puffin-rs/src/lib.rs`, and a test.

### 4.3 Do: `puffin-code`'s own output

`puffin-code` is ours, so its format is the cheapest lever, but a small one: all `code_*` output is 23% of the index arm.

- **`code_show` pages at 100 lines by default** (SWE-agent's measured optimum), with a last line naming the rest: `… lines 1962–2026 not shown; code_show name=solveset offset=100`. The `offset` and `limit` arguments already exist; only the default changes. 26 of 207 shows exceeded 100 lines; the cap saves 16% of `code_show`'s output, **2.7%** of the arm's. A 60-line default would save 30% (5%) and is a Phase 2 measurement, since a shorter window cost SWE-agent more than it saved.
- **Long docstrings fold** after 12 lines, with the count of lines folded. `solveset`'s docstring was 35 of its 165 lines.
- **The fixed overhead: 2.1K tokens on every request**, mostly the nine tool schemas (the prompt block differs by only about 500 characters per instance). Shorter tool descriptions and one shared argument schema are the first half. Whether nine tools should be fewer is a measurement, not a guess: the arm called `code_show` 207 times, `code_search` 54, `code_impact` 17, `code_callers` 9, `code_def` and `code_refs` 4 each, and `code_callees`, `code_impl` and `code_outline` never.
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
- **Raising the benchmark's limit by running fewer instances at once.** It would remove most compactions (§4.1's 65K row), but it changes the benchmark, not Puffin; the benchmark's 44K stands in for Night Shift's 49K, which is the budget this spec is for.

---

## 5. Scope

| Budget | Where it applies | Bearing |
|---|---|---|
| 44,000 tokens | the SWE-bench runs at three instances at once (KV pool 133K) | where the problem was found |
| 49,152 tokens | Night Shift's `task_context` | **the budget this spec is for** |
| 94,144 tokens | interactive sessions (60% of the KV pool, COMPACTION §4.2) | replay: 5 compactions in 4 of 14 instances without masking, none with it |

Interactive sessions are not the target, and nothing here should be read as a general `puffin` problem. Masking still applies there, and costs nothing until the high mark is reached.

---

## 6. Phases and tests

**Phase 0, replay (done, 2026-10-03).** §1 and the tables of §4.1. The replay scripts read the rollout files and need nothing running; they become a `puffin-admin swe-bench report --context` section in Phase 1, so every later run reports its per-kind output, compactions and re-reads.

**Phase 1, build.**
- §4.1: the `masking` module in `puffin-rs` with unit tests: nothing masked below the high mark; masked down to the low mark; no new move before the minimum step; the last 10 and outputs under 600 characters never masked; the placeholder's first and last lines; the result depends on the history alone (the same input gives the same output, so a resumed session gets the same view). The patch, with the cap raised. A live check in a real session: `prompt_debug` before and after the high mark, and the server's prefix-cache hits between moves.
- §4.2: the catalog's limit and a test.
- §4.3: the 100-line default and folding in `puffin-code` (`cargo test --locked`), shorter schemas, the overhead re-measured from the first request's input tokens.

**Phase 1 acceptance, the A/B.** On a rebuilt `puffin`, the same 14 instances at 44K, three arms: index with masking, index without, plain without; the plain arm repeated for noise. Report per arm: compactions, re-read characters across compactions, tool output per kind, agent time, resolved. Masking ships on by default if compactions fall by at least a third and neither resolved instances nor agent time get worse beyond the plain arm's repeat-to-repeat spread. The number of resolved instances is reported, not claimed: at 14 instances a difference of two is noise (SWE_BENCH §13.5).

**Phase 2.** Masking old long commands (§4.1); a 60-line `code_show`; `code_search` row caps; fewer tools if the call counts hold.

**Phase 3.** §4.5, after Phase 1 shows what remains.

---

## 7. Open questions

1. **Does masking change the agent's behaviour?** The published result says no worse than summarisation; with a 27B model and Codex's prompt, only the A/B shows it. A model that sees a placeholder may re-run commands more often.
2. **The prefix cache with SGLang's hybrid attention.** PREFIX_CACHE records how the hybrid GDN layers cache. Whether a masked prefix is re-used between moves as the time table assumes is checked live in Phase 1.
3. **Subagents and `/compact` typed by the user** go through the same `for_prompt`; they are covered by construction, but not yet looked at.
4. **The 2.1K-token overhead** is attributed to the tool schemas by elimination (the prompt block's difference is small). A request recorded with and without the tools confirms it.

---

## Sources

- Lindenbauer, Slinko, Felder, Bogomolov, Zharov, [*The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management*](https://arxiv.org/abs/2508.21433) (arXiv 2508.21433), and JetBrains Research, [*Cutting Through the Noise: Smarter Context Management for LLM-Powered Agents*](https://blog.jetbrains.com/research/2025/12/efficient-context-management/) (December 2025).
- OpenHands, [Context Condenser](https://docs.openhands.dev/sdk/guides/context-condenser) and [Condenser architecture](https://docs.openhands.dev/sdk/arch/condenser).
- Anthropic, [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing); [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents).
- Yang et al., [*SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*](https://arxiv.org/abs/2405.15793) (NeurIPS 2024).
- [rtk-ai/rtk](https://github.com/rtk-ai/rtk) (Apache-2.0) and its [site](https://www.rtk-ai.app/); `rtk 0.46.0` as installed on this machine (`rtk --help`, `rtk init --help`).
- Codex `rust-v0.158.0`: `core/src/context_manager/history.rs`, `core/src/session/turn.rs`, `core/src/compact.rs`, `core/src/tools/registry.rs`, `core/src/tools/context.rs`, `hooks/src/engine/output_parser.rs`, `models-manager/src/model_info.rs`, `models-manager/models.json`.
