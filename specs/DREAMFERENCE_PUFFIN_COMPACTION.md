# Puffin Compaction — at night, at idle, and what the diffusion model can do

**Status:** proposed. Nothing in this spec is implemented yet; §1 and §3 are measurements and a literature review made on 2026-10-01.
**Question asked:** can Puffin's compaction be improved by running it at night, and continuously with the diffusion model beside the main one?
**Short answer:** compaction is not slow or poor on this machine; it is **switched off in effect**. The useful changes are two configuration values and one Night Shift task, none needs a Codex patch, and the current diffusion sidecar has no part in any of them.
**Builds on:**
- Codex's own compaction (`codex-rs/core/src/compact.rs`), unmodified;
- the launcher's model catalog (`puffin-rs/src/lib.rs`, `auto_compact_token_limit`);
- Night Shift ([PUFFIN_NIGHT_SHIFT](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md)), whose parallelism is computed from a per-task context budget;
- the diffusion slot ([PUFFIN_FAST_TOOLS](./DREAMFERENCE_PUFFIN_FAST_TOOLS.md), which already measured the sidecar on summaries).

---

## 1. What was measured before proposing anything

| What | Result |
|---|---|
| Compactions in the rollouts under `~/.puffin/sessions` (111 files) and `~/.codex/sessions`, before the probe two rows down | **0.** No rollout held a `"type":"compacted"` item. (The probe has since added one rollout that does) |
| Peak prompt size per session (83 sessions with usage records) | median **14,153** tokens, p90 30,679, p99 54,390, max **71,421** |
| When Puffin compacts | The launcher writes `auto_compact_token_limit = max_model_len` into the catalog: **262,144** on the default model |
| What the server can hold | SGLang's KV pool is **156,907** tokens (`sglang:max_total_num_tokens`), shared by every stream, while it is launched with `--context-length 262144` and reports that as `max_model_len`. The pool is sized at launch from free memory: Night Shift §11.1 read 144,870 on an earlier launch. Both the window Puffin advertises and its compaction limit are above the pool |
| Lowering the limit without a patch | `puffin exec -c model_auto_compact_token_limit=6000 …` compacts: 7 `compacted` items in one small task. Measured under the default scope (`model_auto_compact_token_limit_scope = total`); the other scope, `body_after_prefix`, counts only growth after the prefix and was not tried |
| What too low a limit costs | A deliberately degenerate limit: Puffin's fixed prefix (instructions and tool schemas) is itself near 6,000 tokens, so it compacted after almost every command. This shows the failure mode, not the cost at 32–49K. The same task (three `cat` commands of ~2.4K tokens each) ran **13 commands instead of 3** and read 175,625 prompt tokens: after each compaction the model re-ran what the summary no longer held. The answer was still right |
| The diffusion sidecar as a tool-output compressor (Tiny-A2D 0.5B, chat completions, "keep every path, number, hash and error") | a 3,000-character `git log --stat`: **14 of 50** identifiers survived, one path came back misspelt (`puffinin-code`), 9.1 s. Fast Tools §1 found the same on 2026-09-30: its log "summary" copied the log |
| Prefix reuse today | `sglang:cached_tokens_total` / `prompt_tokens_total` = 78,976 / 118,663 after a restart: about two thirds of prompt tokens come from the cache |

Three conclusions:

1. **Interactive sessions never compact, and at today's sizes they do not need to.** The largest session ever recorded is 27% of the window.
2. **The limit is wrong, not merely unused.** A session that did grow would reach the KV pool (157K, less whatever other streams hold) long before 262K. What SGLang does with a prompt larger than its pool was not tested (no session is that large, and the test would evict every cached prefix); it cannot be to serve it. The same holds for the 262K context window the catalog advertises.
3. **Night Shift is where it matters.** Its parallelism is `floor(pool / task_context)` with `task_context = 49,152` (Night Shift §11.1), but the runner passes no compaction limit, so each task may grow to 262K. The budget the parallelism is computed from is not enforced.

---

## 2. The cost model here is not the papers'

Published work on agent context measures API cost per token. On this machine tokens are free. What costs is:

- **Prefill time:** ~1,700 tok/s, ~1,000 tok/s at 116K. Re-reading a 48K context after its cache is lost is about 30 s.
- **The KV pool:** 157K tokens for all streams together.
- **Output time:** a ~1.5K-token summary at ~25 tok/s of prose is about a minute.
- **Rework:** what the model does again because the summary dropped it (§1: 4× the commands at an extreme limit).

So anything that rewrites early history **every turn** (per-turn observation masking, a small model pruning each tool result in place after the fact) is the worst fit: each rewrite invalidates the radix cache from that position and costs a re-prefill of everything after it. The right shape is **compact rarely, and touch nothing in between**.

---

## 3. What the literature says, read against §2

| Source | Finding | Bearing here |
|---|---|---|
| *The Complexity Trap* (JetBrains, arXiv 2508.21433) | On SWE-bench Verified, replacing old tool outputs with a placeholder halves cost and matches or slightly beats LLM summarisation; a hybrid saves a further 7–11% | A compressor model is not required for good results. Summaries also hide the signal that a task is going nowhere, so runs get longer |
| *CliffCompaction* (arXiv 2609.26779) | Rule-based: at a threshold, keep the system prompt, the task, tool-call signatures, short results and the last K turns verbatim; drop long results. History is untouched between compactions, so the KV cache stays valid. 32–45K is the reported sweet spot; 8K is too tight; 16K costs ~2 points on SWE-bench Verified | The closest match to §2. Its threshold range agrees with Night Shift's 49,152 budget, and its "8K is too tight" with the 6,000-token probe in §1 |
| *Paritok-4B* (arXiv 2608.24188) and *AGORA* (arXiv 2605.26596) | A small model can compress agent observations only when conditioned on the task and working on whole segments; token-level extractive pruning (LLMLingua-2 style) removes identifiers, brackets and verbs, the tokens an agent acts on | A 0.5B model pruning tokens is the failing case, which §1 reproduced. A ~4B compressor is a different model from the one in the slot |
| *DiffuMask* (arXiv 2604.06627) | A diffusion LM prunes prompts by parallel mask prediction, up to 80% shorter with accuracy kept, on few-shot prompts | The one published use of a diffusion model for compression. It prunes demonstrations, not agent trajectories, and needs a model trained for it |
| *Sleep-time compute* (Letta, 2025) | An agent that rewrites its memory while idle needs ~5× less compute at question time, when later questions are predictable from the context | The night analogue is not compacting a transcript; it is distilling yesterday's sessions into notes the next session starts with |
| Claude Code's Auto Dream | A background agent merges, dedups and prunes memory files between sessions, after 24 h and 5+ sessions | The same idea in a shipping coding agent; its failure mode was memory that filled with duplicates and stale facts until consolidated |

Figures in this table are from abstracts and summaries read on 2026-10-01, not from re-running the papers; check the source before quoting one.

---

## 4. Proposals

### 4.1 Do: give Night Shift tasks the compaction limit their budget assumes

`NightShiftTaskRun._exec` adds `-c model_auto_compact_token_limit=<n>` to every `puffin exec`, with `n` from a new `[night] compact_at` (default: `task_context`, 49,152).

- No patch: the key exists (`core/src/config/mod.rs`, `model_auto_compact_token_limit`) and §1 shows `-c` reaches `puffin exec`.
- It makes `task_context` true, so `floor(pool / task_context)` parallel tasks really fit the pool.
- **Accept only on measurement** (§5, Phase 0): the §1 probe shows a limit can cost more than it saves.

### 4.2 Do: make the interactive limit follow the KV pool, not the window

The launcher already reads `/v1/models`; it would also read the pool (`sglang:max_total_num_tokens`, or `num_gpu_blocks × block_size` on vLLM, as `NightShiftHost` does) and write `auto_compact_token_limit = min(max_model_len, pool × 0.6)`: about 94K today. The pool is read at each start because it changes between launches (§1). The 0.6 is a placeholder, leaving room for a second stream and the summary request; Phase 0 sets it. No session recorded so far would have compacted; one that grows now compacts instead of exhausting the pool. `max_context_window` should be capped at the pool the same way once §1's untested case is tested. The interactive limit and Night Shift's do not compete: a night run does not start while a `puffin` session is open.

### 4.3 Try: compact at idle, with Codex's own turn-end compaction

Codex has `model_post_turn_compact_threshold_percent` (default 0, off): when a turn **ends** above that percentage of the window, and no input is queued, it compacts then, while the user reads the answer, instead of in the middle of the next turn. This is "continuous" compaction at the only moment it is free. The launcher would set it so the turn-end threshold sits below the §4.2 limit (for example 25% of 262K ≈ 65K). Unverified: how it behaves in `puffin exec`, and whether a user who types at once waits for it.

### 4.4 Try: a nightly consolidation task, not a nightly compaction

`puffin-admin night run` gains one built-in task per repository with sessions since the last run: the main model reads those rollouts' user messages and final answers and writes `$CODEX_HOME/night/notes/<repo>.md` (decisions made, commands that work, dead ends), merging with the previous file under a fixed size cap. The launcher appends that file to the catalog's instructions, as it does `WEB_ACCESS_INSTRUCTIONS`.

- It runs last in the window, after queued tasks, under the same admission rules.
- It writes its own file, never `AGENTS.md`: a wrong note there would steer every session and be committed.
- Appended instructions sit at the head of the prompt, so the file changes at most once a night, or every session would lose its cached prefix.
- Value is unmeasured. Letta's condition holds (same repository, so questions are predictable); Auto Dream's failure mode (stale and duplicate notes) is the risk the size cap and merge are for.

### 4.5 Not proposed

- **The current diffusion sidecar in any compaction role.** §1: 14 of 50 identifiers kept, and invented text. Summaries and prunings another model must act on are exactly what it cannot do.
- **Per-turn pruning or masking of old tool output**, by any model: §2.
- **Pre-compacting resumable sessions overnight.** It would save one ~30 s re-prefill per resume, the cache does not survive a server restart anyway, and no recorded session is large enough to need it.
- **A custom compaction prompt** now. `compact_prompt` is a config key (no patch), so it is cheap later, but nothing measured says the default is the problem.

### 4.6 When the diffusion slot holds a capable model

Fast Tools proposes a stronger model in the slot. Only then is a diffusion role in compaction worth testing, in this order:
1. **Summarise a long tool result before it enters history** (a `puffin fast` verb the main model calls instead of reading 10K tokens of log). It is append-only, so the cache is untouched; this is the Paritok use, conditioned on the task.
2. **Write the compaction summary.** Codex's compaction uses the session's model; routing it elsewhere needs the Fast Tools router or a patch. Worth it only if the summary's minute of prose output (§2) proves to matter.

Each needs the §5 identifier-survival test passed first.

---

## 5. Phases and tests

**Phase 0, measure (no code).** One long task (a multi-file change with tests in this repository, known to pass 60K tokens) run with `puffin exec` at limits of 32K, 49K, 64K and none, three runs each. Record: pass or fail, wall time, commands run, prompt tokens, compactions. §4.1 ships with the limit that is no slower and no less successful than "none"; if none qualifies, §4.1 is dropped and Night Shift's `task_context` is raised instead.

**Phase 1.** §4.1 and §4.2, with tests: the runner's command line carries the limit; the launcher's catalog carries `min(window, pool × 0.6)` and falls back to the window when `/metrics` does not answer.

**Phase 2.** §4.3 behind a config key, checked live in the TUI and in `exec`. §4.4 as a Night Shift built-in, judged after two weeks by whether its notes were right.

**The identifier-survival test** (for any model proposed as a compressor): 20 real tool outputs from rollouts; the compressed text must keep at least 95% of paths, test names, hashes and numbers, and contain no identifier absent from the input. The current sidecar scores 28% with invented identifiers.

---

## 6. Constraints

- **Patch budget:** 567 bytes remain after `0018` (27,500 − 26,933). Nothing here uses any.
- **Compaction summaries are normal prose at every cave level** (Cave Mode §4, verified there).
- **The prefix cache is the thing being protected:** every proposal here appends or rewrites rarely.
