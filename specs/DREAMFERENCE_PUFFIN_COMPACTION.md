# Puffin Compaction — at night, at idle, and what the diffusion model can do

**Status:** partly implemented on 2026-10-02, after the Phase 0 measurement (§11): the interactive limit follows the KV pool (§4.2, on), the ledger hook is built and registered by the launcher (§10.1, on by default since 2026-10-02), and Night Shift's per-task limit is a setting (§4.1, off by default, because the measurement said so). §4.3, §4.4, §10.2 and §10.3 are not built. Before that: §1 and §3 are measurements and a literature review made on 2026-10-01. §7–§10 were added the same day for a second question, the compaction **prompt and algorithm**: §7 is read from the pinned Codex source, §9 is measured, §8 is literature, §10 is proposed.
**Question asked:** can Puffin's compaction be improved by running it at night, and continuously with the diffusion model beside the main one?
**Short answer:** compaction is not slow or poor on this machine; it is **switched off in effect**. The useful changes are two configuration values and one Night Shift task, none needs a Codex patch, and the current diffusion sidecar has no part in any of them.
**Short answer on the prompt and algorithm (§7–§10):** Codex's stock prompt already writes a good summary with this model, except for the trail of files; a structured prompt fixed the trail and lost the code, a trade and not a gain. What compaction loses is not prose but the **tool history**: every tool call and output is dropped, and the summary alone decides which paths survive. The fix that measured best is not a model at all: a rule-built ledger (files touched, failed commands, last test result) re-injected by a hook after each compaction. At night the useful job is an audit of the day's compactions; the diffusion sidecar failed the three new roles it was tried in.
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

*Built as a setting, off by default: Phase 0 (§11) found no limit at or below `task_context` that is no slower than none.*


`NightShiftTaskRun._exec` adds `-c model_auto_compact_token_limit=<n>` to every `puffin exec`, with `n` from a new `[night] compact_at` (default: `task_context`, 49,152).

- No patch: the key exists (`core/src/config/mod.rs`, `model_auto_compact_token_limit`) and §1 shows `-c` reaches `puffin exec`.
- It makes `task_context` true, so `floor(pool / task_context)` parallel tasks really fit the pool.
- **Accept only on measurement** (§5, Phase 0): the §1 probe shows a limit can cost more than it saves.

### 4.2 Do: make the interactive limit follow the KV pool, not the window

*Built (§11.1), with the 0.6 kept: no recorded session, and no Phase 0 run, reached 94K, so the share is a ceiling, not a measured optimum.*


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
- **A custom compaction prompt** now. `compact_prompt` is a config key (no patch), so it is cheap later, but nothing measured says the default is the problem. (§9.1 has since measured it on one session: still not the problem; §10.2 keeps a candidate on file.)

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

- **Patch budget:** 567 bytes remain after `0018` (27,500 − 26,933). Nothing here uses any; §7's one finding that would need a patch (the summary prefix) does not fit in it.
- **Compaction summaries are normal prose at every cave level** (Cave Mode §4, verified there).
- **The prefix cache is the thing being protected:** every proposal here appends or rewrites rarely.

---

## 7. How Codex compacts today (read from `rust-v0.158.0`, checked against a live run)

Puffin's provider has no remote compaction (a provider gets `RemoteCompactionSupport::V2` only if it is the upstream vendor's own provider, is an Azure Responses endpoint, or is Bedrock; the launcher's is named `openai-custom`), and the `token_budget` feature is off, so every compaction takes the **local** path in `core/src/compact.rs`. The `compacted` items in this machine's rollouts confirm it: each begins with the local path's summary prefix.

1. **The request.** The session's base instructions, then the **whole history as it stands**, then the compaction prompt as one more user message. The prefix is the one the last turn used, so the request is cache-hot: its cost is the summary's output, not a re-read. (This corrects the emphasis of §2: the re-read after a compaction is small, because the new history is short.)
2. **The prompt** (`prompts/templates/compact/prompt.md`, 426 bytes): "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task", then four bullets (progress and decisions; context, constraints and preferences; what remains; critical data and references) and "Be concise, structured". It names no sections, never says "verbatim", and never mentions files, commands or errors.
3. **What replaces the history** (`build_compacted_history`, and a recorded `replacement_history` shows the same order): the initial context (developer instructions, the environment block), then the **user's messages verbatim**, in their original order, then one user-role item holding a fixed prefix and the summary. The user messages are capped at 20,000 tokens (`COMPACT_USER_MESSAGE_MAX_TOKENS`), filled from the newest backwards, so it is the oldest that are cut.
4. **What is dropped: everything else.** Every assistant message, every tool call and every tool output. There is no "keep the last K turns" tail and no placeholder for old output: after a compaction the model's only record of what it ran and read is the summary's prose.
5. **Compaction is implicitly incremental.** The previous summary is a user-role item in the history, so the next compaction reads it and the model folds it into the new one. Nothing tells it to preserve what the old summary held.
6. **The summary prefix says something false here** (`summary_prefix.md`): "You also have access to the state of the tools that were used by that language model." On the local path the tool history is gone. It is a compiled-in constant; changing it is a ~1 KB patch (the old and new line), more than the 567 bytes left under the cap. Recorded as a finding, not proposed.
7. **If the request overflows**, the oldest history item is removed and the request retried; other errors retry with backoff.

**What can change it without a patch:**

| Lever | What it does |
|---|---|
| `compact_prompt` (config or `-c`), `experimental_compact_prompt_file` | Replaces the prompt of step 2 |
| `model_auto_compact_token_limit`, `…_scope`, `model_post_turn_compact_threshold_percent` | When (§4) |
| `PreCompact` / `PostCompact` hooks | Run a command around each compaction; they receive the session's `transcript_path`; they can stop the turn but inject nothing |
| `SessionStart` hook with matcher `compact` | Queued by every compaction (`session/mod.rs`, `SessionStartSource::Compact`); its `additionalContext` is recorded as a developer message **after** the summary, before the next model request. This is the one place a program can add to the compacted history (§9.2) |
| `features.token_budget` (under development, off) | A different algorithm: no summary at all, a fresh context window with the world state, and a `new_context` tool the model calls itself. Not tried |

Hooks run only when trusted: a hook's hash must be recorded as trusted in Codex's config layers (where it is written was not traced), or the run must pass `--dangerously-bypass-hook-trust`. An untrusted hook is skipped with nothing in `exec`'s output (§9.2).

---

## 8. What is published on prompts and algorithms

Read on 2026-10-01 from abstracts, vendor posts and one pull request; figures are theirs, not re-run here.

| Source | Finding | Bearing here |
|---|---|---|
| Factory, *Evaluating Context Compression* | Scores summaries with probe questions asked after compaction (recall, artifact, continuation, decision). Its own "anchored iterative" summary (fixed sections: session intent, file modifications, decisions, next steps; only the newly dropped span is summarised and merged) scores 3.70/5 against 3.44 (Anthropic's SDK) and 3.35 (the upstream vendor's compact endpoint). **Every method is worst at the artifact trail: 2.19–2.45/5** | Which files were touched is the thing model-written summaries lose, whatever the prompt. That is a job for a list, not for prose |
| *Lost in Compaction* (arXiv 2608.11242) | Compactors keep on average **17%** of the standing instructions a user gave earlier in a session; a separate extractor running beside the compactor restores over 90% without changing it | Codex already does the cheap half: user messages are kept verbatim (§7.3). The pattern, a rule or extractor beside the summariser, is §10.1 |
| Claude Code's compaction prompt (as published by third parties) | Nine required sections (request and intent, concepts, files and code sections, errors and fixes, problem solving, all user messages, pending tasks, current work, next step), an analysis pass before the summary, "file names, full code snippets, function signatures", and verbatim quotes of the latest request "to ensure there's no drift" | The model for a structured prompt. Its "all user messages" section is redundant in Codex (§7.3) |
| hermes-agent PR #2323 | Four changes shipped together: a section template; iterative update ("PRESERVE existing info, ADD new progress"); replacing old tool output with a placeholder **before** summarising, "30%+" saved with no model call; a ~20K-token verbatim tail instead of a fixed number of turns | The tail and the pre-pass are algorithm changes Codex's local path cannot express without a patch |
| TRACE (arXiv 2608.06503) | Judges one compaction at a time by continuing the task from both sides of it ("boundary-local evaluation"), not by end-to-end score | The right shape for a night-time audit (§10.3): each recorded compaction is a test case |
| *Detect, Remask, Repair* (arXiv 2606.12807) | A masked diffusion LM updates a summary by masking only the spans new context made false and regenerating those | The one published diffusion role that fits compaction: repairing an incremental summary instead of rewriting it. It needs a capable masked LM |
| Template infilling for diffusion LMs (arXiv 2510.13870) | Fix the output's structure as a template and let the diffusion model fill the gaps: +9.4% over prefix prompting | Tried here as form filling (§9.3): the current sidecar fails it |
| Sleep-time work (Letta; Auto-Dreamer, arXiv 2605.20616) | Consolidation is distinct from compaction: it runs offline over many sessions and rewrites a store, where compaction must produce one text under a deadline | §4.4 already proposes the consolidation job. Night is not for compacting transcripts |

---

## 9. Measured on 2026-10-01

Scripts and outputs are in the session scratchpad (not kept). All against the default model (Qwen3.8-27B on SGLang) and the Tiny-A2D sidecar.

### 9.1 The stock prompt against a structured one, on one real coding session

**No recorded session was usable.** The largest rollouts under `~/.puffin/sessions` are web lookups and compaction probes; none is long coding work. So one was made: `puffin exec` in a scratch repository (a cut-down copy of this one) with a scratch `CODEX_HOME`, asked to add a dry-run mode to Night Shift with two tests. It ran 37 commands and peaked at 54,399 prompt tokens. The session was cut just after its first test run (31 commands in; one test passing, one failing), flattened into chat messages (tool output capped at 12,000 characters each, 33K prompt tokens) and sent to `/v1/chat/completions` twice, once ending with Codex's `prompt.md` and once with the candidate of §10.2. This is not byte-for-byte Codex's request; both prompts saw the same input.

| | Stock prompt | Structured candidate |
|---|---|---|
| Summary size | 4,587 chars | 4,692 chars |
| Output tokens (of which reasoning) | 2,235 (876) | 2,302 (912) |
| Wall time | 83 s | 59 s |
| The two files changed, named | 2/2 | 2/2 |
| The two tests added, named | 2/2 | 2/2 |
| Path strings the commands acted on (14 strings, about 12 files: some appear both bare and with their directory) | **4** | **13** |
| The failing assertion's text | no (explains the cause instead) | yes, quoted |
| The code that was added | quoted in full | described in one sentence |
| Symbols of the modules read (`round_robin`, `detect_test_command`, …) | explained, with line numbers | fewer |
| Commands that failed with a non-zero exit (2, both incidental) | 0 | 0 |
| Paths not in the input | 0 | 0 |
| Correct next step (fix the test's repository grouping, re-run) | yes | yes |

Reading: **the stock prompt is strong on understanding and code, and weak on the artifact trail.** With this model it already produces sections, quotes the code and states the next step, but it named 4 of the 14 paths. The structured prompt fixes the trail (13) and quotes the error, and pays for it with the code and the account of the modules read: a trade, neither a superset of the other. That is the case for §10.1, which gets 14 of 14 without giving up what the stock prompt does well. One session, one run each: the timing difference is noise, and nothing here says which summary the next turn works better from.

### 9.2 A rule-built ledger, and whether a hook can deliver it

**The ledger.** From the same cut session, by regular expressions over the tool calls and outputs, no model: the files the commands acted on, each command that exited non-zero with its code, and the last test-result line. 588 characters, built in milliseconds.

| | Paths acted on | Failed commands | Last test result |
|---|---|---|---|
| Stock summary | 4/14 | 0/2 | in prose |
| Stock summary + ledger | **14/14** | **2/2** | verbatim |

**Delivery.** A `SessionStart` hook with matcher `compact`, in a scratch `CODEX_HOME`, printing `additionalContext`; `puffin exec` with `model_auto_compact_token_limit=14000` on a three-command task:

- Without trust the hook did not run and nothing in `exec`'s output said so (the scratch home's logs were not checked). With `--dangerously-bypass-hook-trust`, `PreCompact` and `SessionStart` both ran and received `transcript_path`.
- The hook's text was recorded as a developer message 55 ms after the `compacted` item and before the next model request.
- **In that same turn the model did not use it:** told to end its answer with the ledger code "if you have been given one", it answered "No ledger code given". **On the next turn, asked directly, it quoted the message.** So the text is in context; one trial says it may be overlooked mid-turn. Wording and placement are open (§10.1).
- The compaction itself took 24 s at a 14K context.

**A side finding on limits.** The first attempt used a limit of 9,000 and compacted **42 times** in a three-command task; at 14,000 it compacted once. With §1's probe at 6,000 this puts the floor for this prompt and tool set between 9K and 14K: below it, the fixed prefix leaves no room and every step compacts.

### 9.3 The diffusion sidecar in roles §1 did not cover

Input: `git log --stat` of this repository. A regular expression finds its 8 paths (in the first 700 characters) or 34 (in 3,000) in microseconds.

| Role | Result |
|---|---|
| Extraction ("list every file path, one per line, copied exactly"), 700 chars | Copied the input back with its `| 36+` columns: 0 clean path lines of 8, 2.6 s |
| The same, 1,500 and 3,000 chars | **Empty answer** both times. Cause not investigated: it may be the sidecar's handling of long prompts rather than the model |
| Verification ("does the exact path `X` appear in the text? yes or no"), 8 present and 8 altered paths | "yes" to all 16: 8/16, chance |
| Form filling (three labelled blanks to fill from the text) | Echoed the text; no blank filled |

**Verdict: no role.** It does not extract, verify or fill a form, and a regular expression does the extraction exactly and for free. This is a statement about Tiny-A2D 0.5B, not about diffusion: §8's two diffusion papers need a model that can follow an instruction.

---

## 10. Proposals for the prompt and the algorithm

### 10.1 Do, after one more measurement: a ledger re-injected after every compaction

*Built (§11.1) and measured (§11.2): at a 32K limit it halved compactions, commands and prompt tokens in the pair that finished. On by default since 2026-10-02, the user's decision after Phase 0; registering it writes a trusted hook into the user's `config.toml`, and `puffin_compaction_ledger = false` removes it at the next launch.*


A small program, shipped with Puffin and registered as a `SessionStart` hook with matcher `compact`. It reads the rollout at `transcript_path`, and prints as `additionalContext`, by rule:
- files the session's commands wrote or read, changed files first;
- every command that exited non-zero, with its code, since the previous compaction (both failures in §9.2's session were incidental, so this list can be noise: whether to keep it is part of the test below);
- the last test-result line, verbatim;
- a first line saying what it is: "Ledger built from the tool history by rule; the tool history itself is no longer in context."

Why this and not a better prompt: §9.1 and Factory's probes agree that the artifact trail is what summaries lose, and §9.2 shows a rule keeps all of it for ~150 tokens. It is CliffCompaction's rule-based layer added beside Codex's model summary, and *Lost in Compaction*'s "extractor beside the compactor". It appends after the summary, so it costs no cached prefix. No patch.

Open before it ships:
1. **Does the model use it?** §9.2's one trial says not reliably in the same turn. Test wording and whether the ledger should instead be handed to the summariser (a `PreCompact` hook cannot inject, so that would mean writing the ledger into the worktree and naming the file in `compact_prompt`).
2. **Trust.** Night Shift can pass `--dangerously-bypass-hook-trust` for a hook Puffin itself installs. For interactive sessions the launcher would have to record the hook's hash as trusted, in the user's config: a decision for the user, since it makes Puffin run a program at every compaction.
3. **The functional test**, which decides: the §5 Phase 0 task at one limit, with and without the ledger, counting commands re-run after each compaction (§1 saw 13 commands where 3 were needed).

### 10.2 Keep on file, do not ship yet: a structured prompt

The candidate measured in §9.1: sections **Task, Files, Commands, Verified, Decisions, Next steps**, a first paragraph telling the summariser what the reader will and will not see ("the user's messages and this summary and nothing else: no tool calls, no tool output"), and one rule: "Copy paths, commands, identifiers, numbers and error text exactly as they appeared. Never paraphrase or shorten a path. Leave a section empty rather than guess." It would ship as a file named by `experimental_compact_prompt_file`, no patch.

Not shipped because §9.1 shows a trade, not a gain. Two changes worth making to it before a second measurement:
- keep the stock prompt's strength: add "quote the code you added or changed" to **Files**;
- drop the user's request from **Task** (Codex keeps user messages verbatim, §7.3) and say so, which frees the space.

If §10.1 ships, the prompt's **Files** and **Commands** sections become redundant and the stock prompt may be the better half of the pair. Measure the pair, not the prompt alone.

### 10.3 Night: audit the day's compactions (new), and replay candidates

Not compaction at night, which has nothing to work on (§4.5), but **measurement at night**, when the model is idle and tokens are free:

- **Audit.** For every `compacted` item in the day's rollouts, compare the identifiers in the items it replaced with the summary: paths acted on, failed commands, test results. Report the losses per session in the Night Shift morning report. No model call. It answers, on real sessions rather than on §9.1's one made-up task, whether summaries lose what matters.
- **Replay.** For the same compactions, re-run the summary with the candidate prompt (§10.2) and score both the same way, as §9.1 did by hand. One request per compaction, cache-cold, so it belongs in the window. Two weeks of this is the evidence §10.2 needs, following TRACE's point that a compaction is best judged at its own boundary.
- **Precondition:** there must be compactions to audit. Today there are none in real sessions (§1); this starts to produce data only after §4.1 or §4.2 lowers a limit.

### 10.4 Continuous: nothing that touches the context

"Continuously" has two meanings, and only one survives §2:
- **Continuously rewriting or pruning the history** (per-turn masking, a small model shortening each tool result in place): rejected in §4.5, and §9.3 removes the only candidate model.
- **Continuously maintaining state outside the context, used only at a compaction:** this is what §10.1 is. The rollout file is that state, written by Codex as it goes; the ledger is derived from it at the moment it is needed. A `PostToolUse` hook keeping a running ledger file would give the same result with more moving parts, so it is not proposed.
- **Compacting at the turn's end** (§4.3) remains the one "continuous" timing change worth trying, and §9.2's 24 s is its cost per compaction.

### 10.5 The diffusion model

No role for Tiny-A2D (§9.3, §1). If the slot gets a capable model, §4.6's two roles (summarising a long tool result before it enters history, then writing the compaction summary) come first, because they need only instruction-following. The two below need a *masked* model specifically and come after them, each gated by §5's identifier-survival test:
1. **Repairing the previous summary** instead of rewriting it (Detect, Remask, Repair): mask the spans the new turns made false, regenerate those. It fits Codex's implicitly incremental summaries (§7.5), but needs the summary to be written outside Codex's own compaction request, so it needs the Fast Tools router or a patch.
2. **Filling the ledger's free-text fields** (a one-line "why" per changed file) by template infilling. The paths and codes stay rule-built.

### 10.6 Not proposed

- **A patch to `summary_prefix.md`** (§7.6): true but unmeasured, and over budget.
- **A verbatim tail of recent turns** (hermes, CliffCompaction): the local path cannot keep assistant or tool items without a patch to `build_compacted_history`.
- **`features.token_budget`**: an unfinished upstream feature; revisit at the next Codex bump.
- **Model-written probes as the score** (Factory's method): the identifier counts of §9 are cheaper and not judged by the model under test; probes are worth adding only if the counts and task success disagree.

### 10.7 Where the ideas came from

Asked for ideas before the measurements, the advisor proposed: scoring the **stock prompt** with the identifier test rather than only compressors, and doing it against the chat endpoint from a rollout instead of resuming a real session; the **hook-delivered rule-built ledger** (§10.1); the **night-time audit** (§10.3); testing the sidecar as an **extractor and verifier** against a regular expression (§9.3); and recording the 20,000-token verbatim user messages and the false summary prefix (§7.3, §7.6). The structured candidate's sections follow its suggestion and Claude Code's published prompt. Found while measuring, not proposed by anyone: that no recorded session was usable, that an untrusted hook is skipped silently, that the model overlooked the injected ledger in the same turn, and the 9K–14K floor.

---

## 11. As built, and Phase 0 (2026-10-02)

### 11.1 What was built

| Piece | Where | Default |
|---|---|---|
| The interactive limit follows the KV pool (§4.2) | `puffin-rs/src/compaction.rs`: at every launch that reaches the model, the pool is read from `/metrics` (`sglang:max_total_num_tokens`, or vLLM's `num_gpu_blocks × block_size`) and `-c model_auto_compact_token_limit=<60% of it>` goes in front of the user's arguments, unless the command line or `config.toml` sets that key. With the default model that is **94,144** against the catalog's 262,144. An unreadable `/metrics` leaves the catalog's limit | on |
| The ledger (§10.1) | `puffin-rs/src/ledger.rs`, run as `puffin ledger` (the hook: JSON on stdin, JSON on stdout) or `puffin ledger show <rollout> [<cwd>]`. By rule, no model, in ~15 ms: `git status --porcelain` (or the `apply_patch` headers outside a repository); the other workspace files any command named, checked on disk and most recent first; the commands that exited non-zero since the previous compaction; the last test-summary line (pytest, `cargo test`, Jest, `go test`). Capped at 6,000 characters, under Codex's 2,500-token spill limit | — |
| The hook's registration | `compaction.rs`: one `[[hooks.SessionStart]]` group, matcher `compact`, command `<this binary> ledger`, timeout 10 s, and `[hooks.state."<config path>:session_start:<n>:0"] trusted_hash = "sha256:…"`. The hash is rebuilt from Codex's own definition (`hooks/src/engine/discovery.rs`, `hook_hash`: SHA-256 of the canonical JSON of the normalised hook); a test pins a value Codex accepted. The group is updated in place, the user's own hooks and their trust entries are untouched, switching off removes both, and with a `hooks.json` beside `config.toml` nothing is written | on (since 2026-10-02); off with `puffin_compaction_ledger = false` / `DREAMFERENCE_PUFFIN_COMPACTION_LEDGER=0` |
| Night Shift's per-task limit (§4.1) | `[night] compact_at`: `-c model_auto_compact_token_limit=<n>` on every `puffin exec` of a task; on the command line it beats the launcher's | `0`: none, so the launcher's 60% applies |

**Trust, checked live** (puffin 0.158.0, scratch `CODEX_HOME`, a three-command task at a 14,000 limit): the registration as written by `compaction.rs` made the hook run after the compaction with **no** `--dangerously-bypass-hook-trust`, and the ledger arrived as a developer message after the summary; the same file with one hex digit of the hash changed compacted and ran no hook. An untrusted hook is still skipped in silence (§7).

### 11.2 Phase 0

**The task.** §5 asked for "one long task … in this repository". A cut-down copy of this repository (its `dreamference/` package and the Night Shift tests, 3.1 MB, committed as one base) and a 2,500-character task: five changes to Night Shift (a dry run, a per-night task cap, a totals line in the report, a `--dry-run` flag, a JSON report), each with a test, and the whole test file passing. It reads every Night Shift module (the largest ~20 KB) and a 600-line test file. Graded afterwards by five **hidden** acceptance tests copied in after the agent stopped, and by the agent's own test file. `puffin exec -s workspace-write` from a pinned copy of the installed binary, one hour each, two runs at a time.

**Load.** The model server was shared with other tasks all day. Running and queued requests were sampled every minute; the first pair of limited runs saw on average 4.5 running and 6.5 queued, the second pair 2.2–2.4 running and none queued. The unlimited runs were not sampled (spot readings during `none-2`: 5–8 running). **Wall times are comparable only within a pair.**

| Run | Limit | Load (running / queued) | Wall | Finished | Hidden tests | Own tests | Commands | Compactions | Files re-read after a compaction | Prompt tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| none-1 | none | not sampled | 14 min 23 s | yes | **5/5** | 45 pass | 33 | 0 | — | 1.91 M | 8,440 |
| none-2 | none | not sampled (5–8 / ?) | 17 min 57 s | yes | **5/5** | 45 pass | 28 | 0 | — | 0.88 M | 6,273 |
| 32k-1 | 32,000 | 4.7 / 5.8 | 60 min | **no** | 0/5 | 39 pass | 40 | 5 | 33 | 0.89 M | 14,911 |
| 32k-L1 | 32,000 + ledger | 4.5 / 6.5 | 60 min | **no** | 0/5 | 39 pass | 34 | 2 | 11 | 0.76 M | 11,041 |
| 49k-1 | 49,152 | 4.5 / 6.5 | 60 min | **no** | **5/5** | 42 pass, 2 fail | 52 | 2 | 7 | 1.76 M | 15,402 |
| 32k-2 | 32,000 | 2.2 / 0 | 58 min | yes | **5/5** | 44 pass | 245 | **24** | 155 | 4.71 M | 74,485 |
| 32k-L2 | 32,000 + ledger | 2.4 / 0 | 49 min | yes | **5/5** | 44 pass | 114 | **11** | 72 | 2.25 M | 55,702 |

The unlimited runs peaked at 79,909 and 49,241 prompt tokens: the same task varies twofold in its prompt tokens without any limit. A first, three-change version of the task finished in 6 min 20 s with a 49K peak and was enlarged because it would barely have compacted at 49K.

**What it says.**

1. **Every limit at or below `task_context` cost the task.** No limited run was as fast as either unlimited one, and the two under light load took 49–58 minutes where none took 14–18. §5's rule (ship the limit that is no slower and no less successful than none) is met by no limit, so §4.1 does not ship as a default: `compact_at` exists, and is `0`.
2. **The mechanism is a thrash, not a bad summary.** Puffin's fixed prefix is ~11K tokens and the user's message is kept verbatim, so a 32K limit leaves ~15–20K for work; one 20 KB module and the test file fill it. After each compaction the model reads the files again (155 re-reads in 32k-2), fills the window, and compacts: 24 compactions in one task, some only minutes apart with a summary of the same length as the last. Summaries also grow, since each folds the previous one in (3.6K → 16.8K characters in 32k-1).
3. **The ledger helped, in both pairs.** Under light load, with the ledger: 11 compactions instead of 24, 114 commands instead of 245, 72 re-reads instead of 155, half the prompt tokens, 9 minutes less. Under heavy load neither finished, but the ledger run again compacted less (2 against 5) and re-read less (11 against 33). Two pairs, one task: a direction, not a measured size. It did not make 32K competitive with no limit.
4. **4 of the 44 summaries in these runs were a tool call, not a summary**: the compaction request's answer was a stray `<tool_call>…` in the model's text format, and Codex kept it as the summary. Over every compaction recorded on this machine it is 5 of 92 before these runs. The next turn then starts from the user's messages alone. Not investigated further; a candidate cause is that the request carries the session's tools.
5. **64K and a third repetition of each arm were not run.** The plan in §5 was four limits by three runs; with one hour per run and a shared server, the order was cut to the arms that decide: none, 32K (the floor CliffCompaction names), 49,152 (`task_context`), and 32K with the ledger. 64K would sit between 49K and the unlimited runs' 80K peak.

**Not measured:** the 94K interactive limit's cost. No recorded session and no Phase 0 run reached it, which is the point of it (§1): it guards the pool, and on today's sessions it never fires.

### 11.3 What is open

- **The ledger is on by default** (decided 2026-10-02, after Phase 0). It writes a trusted hook into the user's `config.toml` at every launch; `puffin_compaction_ledger = false` removes it at the next one. Its benefit rests on two pairs on one task (§11.2).
- **Night Shift's budget is not enforced.** With `compact_at = 0` a task may grow to the launcher's 94K; three tasks at once could ask for more than the pool. What SGLang does then (queue, retract, or fail) was not tested.
- **The tool-call summaries** (§11.2 item 4).
- **§4.3, §4.4, §10.2, §10.3**: not built.

