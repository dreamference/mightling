# Moonshot 3: compaction research after 2026-10-01, read against our cost model (2026-10-13)

**Status:** research survey, nothing built. Reading plus one new measurement from runs already on disk (§1): no GPU, no model server, no agent run.
**Question:** what newer work on agent context compaction could make Mightling's sessions faster or more successful, sharpen our benchmarks, or give users new features, for one 27B hybrid model on one GB10 where tokens are free and time is not.
**Reads with:** [MIGHTLING_COMPACTION](../DREAMFERENCE_MIGHTLING_COMPACTION.md), which already covers *The Complexity Trap*, *CliffCompaction*, Factory's probe evaluation, *Lost in Compaction*, TRACE, Paritok and the diffusion roles (§3, §8 there; not repeated here), and its cost model (§2 there: prefill ~1,700 tok/s, an edit to early history costs a re-prefill of everything after it, "compact rarely and touch nothing in between"); [agent-survey-2026-10-09.md](./agent-survey-2026-10-09.md) and [together-ai-survey-2026-10-13.md](./together-ai-survey-2026-10-13.md).

Figures from papers are the authors' own, read from abstracts and the full text where stated, on the date above. Figures measured here say so.

---

## Short answer

1. **On our own benchmark, compaction is a large, measured cost (§1, new).** Over nights 1–4 (7 runs, 320 instances), sessions that compacted three or more times resolved **38%** against **75–78%** for zero or one compaction, and **compaction itself took 11.9 of 69.8 agent-hours (17%)**, a median **98 s** per compaction with two sessions sharing the server. Part of the resolve gap is task difficulty, but within the same 18 tasks the low-compaction runs still resolved more (0.50 against 0.40).
2. **The best-fitting idea is "drop before you summarise", once per threshold crossing (§3.1).** *The Complexity Trap*'s hybrid and *CliffCompaction* both say: at the threshold, first drop old tool outputs and keep the calls, and summarise only if still over. On our cost model that replaces a ~98 s decode with a ~10–20 s re-prefill, and never touches history between crossings. It needs a Codex patch (the local compaction path has no hook for it); test it as a night arm.
3. **Delayed tail rewrites are the one per-turn method that fits a cache (§3.2).** AgentDiet rewrites only the step two steps back, so the cache from the start of the session to that step survives: the re-prefill is the last two steps, a few thousand tokens. On a hybrid model this is also the only kind of edit the engine can make cheap: recurrent state cannot drop a span, so every edit replays from the nearest checkpoint before it (§4).
4. **Compress at ingestion, not afterwards (§3.3).** SWE-Pruner and CoACT shrink a tool output before it enters the context, so nothing cached is invalidated. A non-destructive variant needs no model: save a long output to a file and keep a preview, as the gateway study recommends.
5. **The long-copy moonshot does not speed compaction (§1).** Only 8% of summary characters sit in verbatim copies of 64 characters or more from the history (23% for 32 or more); runs of 64 *tokens* are rarer still. Summaries paraphrase.
6. **Not worth building here:** implicit/soft-token compression (it fails on SWE-bench Verified in the one study that tried), approximate KV erasure (inexact, and impossible on recurrent layers), anything that needs reinforcement learning of the main model (MEM1, ReSum, FoldGRPO), per-turn rolling compression (AttnCompress) and agent-driven compression from a five-task study (Focus).

---

## 1. Measured from nights 1–4 (new)

Source: the rollouts and gradings of `n1-default`, `n1-tests-v2`, `n2-default`, `n2-tests-v2`, `n3-default`, `n3-issue-v1` and `n4-default` under `~/.local/share/dreamference/swe-bench/runs/`, 320 instances, all with the ledger hook on. A compaction is a `compacted` item in the rollout; its duration is the gap between it and the last earlier rollout line more than 2 s before it (the compaction request's prefill plus the summary's decode; an upper bound by a second or two).

| Compactions in the session | Instances | Resolved | Median wall | Hit the 45-min limit |
|---|---|---|---|---|
| 0 | 150 | 113 (75%) | 3.0 min | 0 |
| 1 | 74 | 58 (78%) | 10.1 min | 0 |
| 2 | 30 | 17 (57%) | 17.9 min | 0 |
| 3 or more | 66 | 25 (38%) | 36.6 min | 25 |

- **Per run:** 22 to 28 of 44–50 instances compact at least once; 36 to 74 compactions per run.
- **Where sessions compact:** at peaks of 49–62K prompt tokens (median 49,071), far below the window.
- **Duration:** 425 compactions, median **98 s**, p75 121 s, p90 145 s, max 321 s; **11.9 h of the 69.8 h** of agent wall time in these runs. Summaries are a median 6,559 characters (p90 9,877), about 1,600 tokens.
- **Same task, different compaction counts:** 18 tasks have both a run with at most one compaction and a run with three or more; mean resolve rate 0.50 against 0.40. Few tasks, so a direction, not a size.
- **Exact command repeats after a compaction:** 1% (281 of 24,222). The ledger seems to prevent the literal re-runs Phase 0 saw; re-reads with different arguments were not counted.
- **Verbatim copying in summaries** (`n3-default`, `n4-default`, 137 summaries, the compaction's own answer excluded from the history): a median 23% of summary characters lie in copies of ≥32 characters from the history, 8% in copies of ≥64 characters.

Two consequences:
- A third compaction is where tasks go wrong, and the restart arm (night 4) did not fix it. Making each compaction **cheaper and less lossy** matters more than avoiding the first one.
- At ~98 s each, compaction is the single largest non-model-work cost in a long session; the turn-end compaction bench running today measures the interactive case (one session, its own server), where this figure should be lower.

## 2. Read since 2026-10-01

| Source | What it does | Reported result | Fit to our cost model |
|---|---|---|---|
| **AgentDiet** (Xiao et al., FSE 2026, arXiv 2509.23586; full text read) | After each step, a cheap model rewrites the step two back (a=2) if it is over 500 tokens (θ), seeing only a window of b=1 steps, removing useless (file listings with `__pycache__`, make's directory lines), redundant (the new string echoed back by an edit tool) and expired (files opened while searching) content | Input tokens −40 to −60%, cost −21 to −36%; SWE-bench Verified 64.5→66.5% and 50.5→52.0% (200 instances); reflection overhead 5–15% of agent cost | **Good**: the fixed lag means only the tail is re-prefilled. Needs a Codex patch and a reflection model; the main model can do it at night-arm scale |
| **ACON** (Kang et al., ICML 2026, arXiv 2510.00615) | Optimises the compaction *guideline* in natural language from pairs where the full context succeeded and the compacted one failed; distils the compressor | Peak tokens −26 to −54% on AppWorld, OfficeBench, multi-objective QA; distilled compressors keep >95% | **Good, offline**: our nights produce exactly such pairs (§1). Ships as a prompt file (`experimental_compact_prompt_file`), no patch |
| **SWE-Pruner** (arXiv 2601.16746) | The agent states a goal; a 0.6B "skimmer" keeps the relevant *lines* of a file read before it enters the context | Tokens −23 to −54% on SWE-bench Verified with minimal loss; up to 26% fewer rounds | **Good shape** (ingestion, line-level, not token-level, which our §1 sidecar test showed fails). Needs a small model beside the main one |
| **CoACT** (arXiv 2607.02911) | Compresses each new observation; trained so the agent's *next action* is unchanged | Tokens −33% on SWE-bench Verified, near the uncompressed solve rate | Same shape as SWE-Pruner; its "next action preserved" test is a usable acceptance criterion for any compressor we try |
| **Cost attribution of compression gateways** (arXiv 2609.22114) | Splits a coding agent's bill into tool-schema filtering, content compression of reads and history summarisation | Schema filtering the only unambiguous win; compressed reads save ~quadratically over turns; **non-destructive compression with recall** keeps the downside bounded | Schema size is cached here, so it costs pool, not time. Recall-on-demand is the model-free version of §3.3 |
| **Context Window Lifecycle** (arXiv 2606.11213) | The agent tags work as typed, dependency-linked episodes; a deterministic policy evicts the most recoverable first (actions whose effects are on disk) | One session, 89 tasks, 80M tokens, no measured degradation | Good idea, no comparison against compaction; eviction must be batched to fit a cache |
| **TokenPilot** (arXiv 2606.17016) | Stabilises prefixes at ingestion; evicts expired segments on a batch-turn schedule | Cost −56 to −87% on PinchBench, Claw-Eval (abstract; a secondary source gives 74%) | Confirms "batch the edits"; benchmarks are not coding |
| **Context-Folding / FoldGRPO** (arXiv 2510.11967) | `branch` into a sub-context for a subtask, `return` a short message; trained with RL | Matches or beats ReAct with a 10× smaller active context on SWE and deep-research tasks | The *shape* (exploration in a sub-session that returns a summary) needs no training to try as a product feature; the reported gains rely on training |
| **Git Context Controller** (arXiv 2508.00031) | Memory as COMMIT/BRANCH/MERGE/CONTEXT over a `.GCC/` folder | Figures differ between versions; a secondary ablation credits retrieval of fine-grained logs, not branching | Retrieval of a log the agent wrote itself is what our ledger and recall-on-demand already approximate |
| **ACE** (ICLR 2026, arXiv 2510.04618) | A playbook updated by small deltas (generate, reflect, curate), never rewritten whole | +10.6% on agent tasks; names "context collapse" from repeated rewrites | Design rule for §4.4's nightly notes: append and curate, never regenerate |
| **AttnCompress** (ISSTA 2026, arXiv 2609.08318) | Proxy-model attention scores trajectory blocks; a rolling window keeps the top ones | 53.17% on SWE-bench Verified, tokens −21.6%, cost −33.6% | **Poor fit**: re-ranks history every turn, so the cache is invalidated every turn |
| **Implicit compression for SE agents** (arXiv 2605.11051) | ICAE-style soft tokens | Faster, better on single-shot tasks, **significantly worse on SWE-bench Verified** | Rules out soft-token compression for agent work |
| **KVEraser** (ICML 2026, arXiv 2606.17034) | Replaces an erased span's KV with learned states instead of recomputing the suffix | Near full-recomputation quality at +24% latency against 17.6× | Inexact, and our model's recurrent layers have no per-token state to erase |
| **Focus** (arXiv 2601.07190) | The agent itself decides when to fold history into a knowledge block | −22.7% tokens, same 3/5 solved on five SWE-bench Lite tasks | Too small a study to act on |
| MEM1 (arXiv 2506.15841), ReSum (arXiv 2509.13313) | Constant-size internal state or periodic summaries, learned with RL | Large gains on QA and web search | Need RL on the main model; we do not train it |

## 3. Proposals, ranked

### 3.1 Drop-first compaction (night arm, then default if it wins)

At the threshold, in one rewrite: keep the prefix, every user message, every tool **call**, short outputs and the last ~20K tokens verbatim; replace older long outputs with a one-line placeholder naming the call; re-inject the ledger as today. Summarise (Codex's path) only if the result is still over the limit, and never summarise a summary (CliffCompaction's rule).

- **Why:** both *The Complexity Trap* (masking matched summaries on SWE-bench Verified; summaries made runs longer) and *CliffCompaction* (truncate, never rephrase) point here, and on our hardware it trades a ~98 s decode (§1) for a ~10–20 s re-prefill of ~20–30K tokens at ~1,700 tok/s (estimate).
- **Cost:** a Codex patch in the compaction path, under the patch cap; the night harness already has arms. Rough size: two days.
- **Test:** a night arm against default on the 44-task list; record compactions, compaction seconds, re-reads, resolved. The acceptance rule: resolved not lower, compaction time down.

### 3.2 Delayed tail rewrite (after 3.1, only if 3.1 leaves third compactions common)

AgentDiet's rule with the main model as the reflector: after step *s*, rewrite step *s−2* if it is over ~500 tokens and the saving is over ~500, removing listing noise, echoed edits and expired reads. One rewrite per step, at the tail, so the re-prefill is the last two steps.

- **Why second:** it slows growth, so sessions reach the threshold later or never; but it costs a short model call per long step, which 3.1 does not.
- **Engine half (§4.1):** ling-engine can make this nearly free by keeping a state checkpoint at each tool-output boundary.

### 3.3 Offload-and-recall for long tool outputs (product, small)

A tool output over a size limit is written to a file in the session's scratch folder and the context gets its first and last lines, its size and the path; the agent reads it again with an ordinary command if it needs it. Non-destructive, no model, and it shrinks what later compactions must carry. Where it lives (the launcher's exec translation or a small patch) is to be checked against the pinned Codex's own output truncation.

### 3.4 Compaction guideline tuned on our own failures (night job, prompt only)

ACON's loop on SWE-bench data we already have: take task pairs where a run with few compactions resolved and a run with many did not (§1 lists 18 such tasks), have the model compare what the summaries dropped, revise the compaction prompt, and A/B the revised prompt in a night. Ships through `experimental_compact_prompt_file`, no patch. Watch for the trade §10.2 of the compaction spec found (a structured prompt kept the files and lost the code).

### 3.5 Benchmarks (cheap, do now)

- **Compaction columns in the SWE-bench report:** compactions per instance, compaction seconds and their share of wall time, resolved by compaction count. §1's script is the starting point.
- **Boundary probes at night:** Factory's four probe types (recall, artifact, continuation, decision) asked after each recorded compaction, scored against the pre-compaction history, next to the rule-based audit already running.
- **Next-action preservation (CoACT's test):** for any compressor, replay the step after a compaction from the full and the compacted context and compare the action.

### 3.6 Product features

- **A context meter with the next compaction's estimated time** in the TUI and the app, from the measured rate (§1).
- **`/compact` with a focus** ("keep everything about the parser"), passed to the summary prompt.
- **Exploration in a sub-session that returns a summary** (Context-Folding's shape): large searches and reads never enter the main context. Check what the pinned Codex offers for sub-agents before designing anything.
- **Nightly notes as a curated playbook** (ACE): when §4.4 of the compaction spec is built, update the notes by deltas, never regenerate them.

## 4. For ling-engine

1. **Edits are replays on a hybrid model.** The recurrent layers hold one state, not per-token entries, so removing a span means replaying from the nearest saved state before it. The checkpoint store (M2, M4) already provides this. A checkpoint at each tool-output boundary would make §3.2's tail rewrites cost only the replay of the last two steps, and §3.1's rewrite a replay from the first dropped output. This belongs with the checkpoint rules in BACKLOG item 8; output is unchanged.
2. **Compaction requests should hit the session's cache.** If Codex sends the compaction as the session's history plus the prompt, the engine's prefix match serves the whole history and only the summary is computed. To be confirmed with a request trace; if the request reorders or rewrites items, the 98 s includes a full re-prefill and the fix is on the client side.
3. **Compaction is decode-bound, not copy-bound.** With 8% of summary characters in ≥64-character copies (§1), the moonshot's long copies will not shorten compactions; ordinary speculation and the step savings will. A shorter summary (the ledger already carries the files) shortens it directly.
4. **Priority:** a turn-end compaction runs while the user reads; under two streams it should yield to a stream with a user waiting. This fits the scheduler's classes when they are revisited after M4.

## 5. Sources

- Lindenbauer et al., *The Complexity Trap*, NeurIPS 2025, [arXiv 2508.21433](https://arxiv.org/abs/2508.21433)
- Xiao et al., *Reducing Cost of LLM Agents with Trajectory Reduction* (AgentDiet), FSE 2026, [arXiv 2509.23586](https://arxiv.org/abs/2509.23586)
- Kang et al., *ACON*, ICML 2026, [arXiv 2510.00615](https://arxiv.org/abs/2510.00615)
- *SWE-Pruner*, [arXiv 2601.16746](https://arxiv.org/abs/2601.16746)
- Chen et al., *CoACT*, [arXiv 2607.02911](https://arxiv.org/abs/2607.02911)
- Chen and Shi, *An Empirical Cost Attribution of Context-Compression Gateways*, [arXiv 2609.22114](https://arxiv.org/abs/2609.22114)
- Semenov and Dorofeev, *Beyond Compaction: Structured Context Eviction*, [arXiv 2606.11213](https://arxiv.org/abs/2606.11213)
- *TokenPilot*, [arXiv 2606.17016](https://arxiv.org/abs/2606.17016)
- Sun et al., *Scaling Long-Horizon LLM Agent via Context-Folding*, [arXiv 2510.11967](https://arxiv.org/abs/2510.11967)
- *Git Context Controller*, [arXiv 2508.00031](https://arxiv.org/abs/2508.00031)
- Zhang et al., *Agentic Context Engineering*, ICLR 2026, [arXiv 2510.04618](https://arxiv.org/abs/2510.04618)
- Zeng et al., *AttnCompress*, ISSTA 2026, [arXiv 2609.08318](https://arxiv.org/abs/2609.08318)
- *On Problems of Implicit Context Compression for Software Engineering Agents*, [arXiv 2605.11051](https://arxiv.org/abs/2605.11051)
- Li et al., *KVEraser*, ICML 2026, [arXiv 2606.17034](https://arxiv.org/abs/2606.17034)
- Verma, *Active Context Compression* (Focus), [arXiv 2601.07190](https://arxiv.org/abs/2601.07190)
- Zhou et al., *MEM1*, [arXiv 2506.15841](https://arxiv.org/abs/2506.15841); *ReSum*, [arXiv 2509.13313](https://arxiv.org/abs/2509.13313)
- Nguyen et al., *CliffCompaction*, [arXiv 2609.26779](https://arxiv.org/abs/2609.26779); Min et al., *TRACE*, [arXiv 2608.06503](https://arxiv.org/abs/2608.06503)
- Factory, *Evaluating Context Compression for AI Agents* (2025-12-16), [factory.com/news/evaluating-compression](https://factory.com/news/evaluating-compression)
