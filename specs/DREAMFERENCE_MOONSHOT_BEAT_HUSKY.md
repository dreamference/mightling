# Moonshot: beating Husky with model-specific inference for agents

**Status:** Proposed (2026-10-28), a moonshot for the 2.0 engine track. Nothing is built, and nothing here changes 1.x. Numbers measured on our machines say so. Numbers from papers are the authors' own and are unverified.

**Depends on:** [2_0](./DREAMFERENCE_2_0.md) (the 2.0 list and order), ling-engine's specs on `dreamference/ling-engine` main: [BATCHING](https://github.com/dreamference/ling-engine/blob/main/specs/DREAMFERENCE_LING_ENGINE_BATCHING.md), [MULTI_DRAFTER](https://github.com/dreamference/ling-engine/blob/main/specs/DREAMFERENCE_LING_ENGINE_MULTI_DRAFTER.md), [LOWBIT](https://github.com/dreamference/ling-engine/blob/main/specs/DREAMFERENCE_LING_ENGINE_LOWBIT.md), [BACKLOG](https://github.com/dreamference/ling-engine/blob/main/specs/DREAMFERENCE_LING_ENGINE_BACKLOG.md), and the copying moonshot [repository-aware decoding](https://github.com/dreamference/ling-engine/blob/main/specs/drafts/MOONSHOT-repository-aware-decoding-2026-10-13.md) (2026-10-13), which this spec builds on rather than repeats.

**What prompted it.** Underdog published [Husky](https://husky.underdog.ai/) on 2026-09-20: a model-specific engine for their 4B model "Woof" on an Apple M5 Max, "up to 4.5× faster than MLX". Our comparison on 2026-10-28 (the coordinator's scratch report `husky-vs-ling-engine.md`) found ling-engine already does 9 of Husky's 17 ideas. Two more were built, measured and closed. Our 16-row verify costs the same as a one-token step (84.8 against 84.9 ms at 4K), where Husky's 8-row check costs 1.5×. So "beat Husky" can't mean copying Husky. It means going where its design doesn't, using the newest research, and proving it with receipts.

---

## 0. The claim we want to be able to make

Husky's own lead is narrow: one user, a 4B model, short prompts, writing and edits. Ours should be a different kind of claim, the one that matters for a coding agent:

> On one GB10, a 27B coding agent solves the same tasks as the best general engine, in **less than half the wall time**, with **bit-identical answers** at any concurrency, while **four agents** work at once, fully air-gapped.

### 0.1 Targets (moonshot, against today's measured baseline)

| Metric | Today (measured, node-b) | Moonshot target | Main levers |
|---|---|---|---|
| Single conversation, warm decode | 64 tok/s (6.7 tokens per ~105 ms step) | **≥ 120 tok/s** | M1, M2, M3, M9 |
| Edit-shaped spans (patches, rewrites) | about the same as the above | **≥ 250 tok/s** | M1 (long verified copies) |
| Time to first token, continued turn | 0.42 s median, 1 stream | **≤ 0.15 s** | M5, M7 |
| Cold prompt processing | ~2K tok/s (cold turns are prefill-bound) | **≥ 5K tok/s** | M4 |
| 4 conversations at once, slowest stream | 26–29 tok/s (pooled 34) | **≥ 50 tok/s each** | M6, M7 |
| SWE-bench round, 50 tasks (night 17) | 4 h 28 min, 33/50 solved | **≤ 2 h 15 min**, no fewer solved | all |
| Answers | identical at any concurrency | **unchanged**: identical | the exactness rule (§2) |

These are targets, not forecasts. Each lever in §3 states its own expected gain and its evidence.

## 1. Where we stand (measured)

- **The step:** about 105 ms for a speculative step at 64 tok/s warm. About 82 ms of a ~108 ms step is the weight read at ~215 GB/s (the repository-aware decoding draft §1). The 16-row verify costs the same as one row: rows are nearly free up to ~24, and 32 rows add ~12.9 ms (Husky comparison, BACKLOG 34).
- **Acceptance:** DFlash2/ScaleSearch lands ~6.7 tokens per step on our agent traffic, capped by its block (12–16). On 32-token-plus verbatim matches it accepts 9.89 against the 3-gram lookup's 6.87 (M1 §6).
- **Prefill:** cold agent turns are prefill-bound: ~290 s of a 378 s round is prompt processing at ~1.8–2.2K tok/s (BATCHING §2). The prefill GEMM measured 262 TFLOPS in isolation; the end-to-end rate is far below that.
- **Concurrency:** C1 (shortest prefill first plus side-share 16) took the warm follow-up's TTFT from 20.5 s to 1.4 s, and pooled decode at 4 streams to 34 tok/s. C2 (fused prefill and decode in one pass) was exact but slower: 243 against 189 ms per pass, because each projection waits for the other stream and the chunk's attention and DeltaNet kernels sit on the critical path (C2 note §10).
- **Memory:** flat KV slots cost ~17.3 GB per session at 262K. 4 × 262K is refused on node-b; P4 paged KV (64-token pages, a ~1.02M-position pool) is in progress (BATCHING §5.3).
- **End to end:** night 17, the engine solved 33/50 in 4 h 28 min, against 29/50 in 7 h 32 min for SGLang with the same model, harness and list.

## 2. Rules, unchanged

1. **Exact by default.** Greedy output stays bit-identical; sampling keeps the target distribution. A lever that changes outputs (M8, part of M5) is marked **not exact**, and ships only after a night shows no loss in solved rate, with the owner's OK (decision of 2026-10-28: such levers are 2.0).
2. **No approximate reuse of computed state** in the exact track. Position-independent KV reuse is out (copying draft §3.6).
3. **Nothing leaves the machine.** Every corpus, cache and drafter's training data is local.
4. **Drafter training waits for a freeze** of ling's prompt, tool names and formats, and Codex version, after the Ragon rename (owner, 2026-10-28).

## 3. The levers

Each lever lists its research basis, what we would build, whether it's exact, the expected gain and how we measure it.

### M1. Session-, workspace- and attempt-wide copying (extends the copying moonshot)

- **Research:**
  - [SuffixDecoding](https://arxiv.org/abs/2411.04975) (NeurIPS 2025 spotlight; in vLLM via [Arctic Inference](https://www.snowflake.com/en/engineering-blog/suffixdecoding-arctic-inference-vllm/)): suffix trees over prompts and *previous outputs*, with speculation length that grows with the match length, hybrid with a model drafter. The authors report up to 3.9× on SWE-bench-style agent loops, and 2.2× over EAGLE-2/3.
  - [AgSpec](https://arxiv.org/html/2610.01108v2), [EfficientEdit](https://arxiv.org/html/2506.02780v2) and [CopySpec](https://arxiv.org/html/2502.08923v1) are summarised in the copying draft §2.
- **What's new here beyond the copying draft:** two more corpora specific to us.
  - **(a) Across attempts.** Night Shift runs best-of-3 and benchmarks retry the same task; attempt 2 repeats much of attempt 1's tool calls and file reads.
  - **(b) Across agents in one node.** Four concurrent agents on one repository read the same files. Speculative copies of 64–128 tokens are nearly free here, because our verify rows are free up to ~24 and cheap to 64 (§1). That's the regime the copying draft identified as untried in the field.
- **Exact:** yes, with deterministic proposals.
- **Expected:** on edit-shaped spans, from ~7 to 20–40 tokens per step, which puts the 250 tok/s edit target within reach. Over whole sessions, less (+10–30%), because most tokens aren't copies. M1 in the copying draft measured +3% best case at a 15-token cap, so the gain hinges on long copies.
- **Measure:** first a CPU-only replay over recorded Work and Night Shift transcripts: accepted length per step by source, at caps of 16, 32, 64 and 128. Build only if edit spans gain ≥ 2×. (The owner put "lookup for edits" in 2.0; this is that item, widened.)

### M2. An agent-trained drafter, trained for acceptance directly

- **Research:**
  - [DLite](https://arxiv.org/abs/2610.23787) (Oct 2026): existing drafters lose up to 80% of their acceptance length on agentic workloads. A history-free drafter reaches ~2–6 and claims +70% over [DSpark](https://arxiv.org/html/2607.05147v1).
  - [Acceptance-Aware Draft Model Training](https://arxiv.org/abs/2609.24150) (Sep 2026): losses that optimise expected accepted length directly, including a GRPO-style variant, rather than cross-entropy or KL.
  - [DFlash](https://arxiv.org/abs/2602.06036) (ICML 2026), our drafter's family: drafters trained with larger blocks generalise to smaller ones, which allows dynamic block-size scheduling.
  - [When Parallel Drafter Meets Parallel SD](https://arxiv.org/pdf/2609.27396): the trade-off between acceptance and drafting latency for parallel drafters.
- **What:** MULTI_DRAFTER's `work` drafter, trained on the model's own replies to agent-shaped tasks. Public tasks first; the owner's own sessions are a 2.0 question still open. Use an acceptance-aware loss and a larger training block (32), so inference can schedule 12–32 by context.
- **Exact:** yes. The target model checks every proposal.
- **Expected:** from 6.7 to ~8 tokens per step (+19%, Husky comparison); more if DLite's agentic-loss finding holds for us. Training costs ~1 week on five GB10s at night, or 1–2 days rented (coordinator estimate, 2026-10-28).
- **Measure:** accepted length per step on held-out agent traces, then a night against today's drafter.
- **Precondition:** the prompt and tool freeze (§2.4).

### M3. Speculation that adapts to load and to confidence

- **Research:**
  - [SmartSpec](https://arxiv.org/html/2406.14066v1): speculation length chosen per request from estimated goodput, from none to many tokens. The authors report up to 3.2× lower latency.
  - [FlexDraft](https://huggingface.co/papers/2605.20022) (May 2026): switch between parallel and sequential draft and verify by batch size.
  - [PACER](https://arxiv.org/abs/2602.01274) (Feb 2026): learned per-step draft length.
  - [AdaEDL](https://arxiv.org/pdf/2410.18351): entropy-based early stop.
  - [BanditSpec](https://arxiv.org/pdf/2505.15141): bandit choice of speculation configuration.
- **What:**
  - Per stream and per step, choose the tree width and depth (and whether to draft at all) from a goodput model fed by the step-time table in §1 and the stream's recent acceptance. At 4 streams the verify rows are no longer free, so the right tree shrinks.
  - This generalises the two small exact items the owner approved for now: memory-sized sessions, and the drafter switched off when acceptance drops.
- **Exact:** yes. Only the proposals change, not the verification.
- **Expected:** single stream +3–8%. At 4 streams +15–30% pooled, by not wasting rows on low-confidence branches. That partly closes the slowest-stream gap.
- **Measure:** warm 1/2/4 agentlong with the probe, against main, at 16/16 greedy identity.

### M4. Prefill at tensor-core speed

- **Evidence:** cold turns are prefill-bound, and tool outputs make every turn partly cold. The prefill GEMM alone reached 262 TFLOPS, yet end-to-end prefill is ~2K tok/s. At ~54 GFLOP per token for a 27B model, the GEMMs alone would allow ~4.8K tok/s, so attention, DeltaNet chunking and launch structure lose more than half.
- **Research:**
  - [FlashInfer's chunked gated-delta-rule kernel](https://docs.flashinfer.ai/generated/flashinfer.gdn_prefill.chunk_gated_delta_rule.html), with initial and final states, for Blackwell.
  - [Erase-then-Delta Attention](https://arxiv.org/pdf/2606.26560): chunked delta-rule prefill costs, on 3:1 hybrids like ours.
  - [POD-Attention](https://arxiv.org/abs/2410.18038) (ASPLOS 2025): prefill attention kernels that co-run with decode.
- **What:** profile one cold 39K-token prefill end to end with Nsight on node-c, then attack the three largest gaps. Expected candidates:
  - DeltaNet chunk size and state passing;
  - full-attention prefill tiling for SM121;
  - fusing norm and residual into the prefill GEMM epilogues, which the decode path already does.
- **Exact:** yes; the same reduction order is required, as for C1 and C2.
- **Expected:** 2.5× prefill (to ≥ 5K tok/s), which roughly halves cold-turn time. This is the largest single lever for agent wall time, because agents spend most of their time reading.
- **Measure:** the BATCHING cold-turn probe, and the night wall time.

### M5. Turn-to-turn reuse when tool output moves things

- **Research:**
  - [Leyline](https://papers.cool/arxiv/2606.01065) (May 2026): agent prefix caches break when tool output shifts positions; a splice primitive replaces cached spans without re-prefilling what follows.
  - [CacheWise](https://www.emergentmind.com/papers/2606.16824) (Jun 2026): coding sessions have far more turns than chat, and tool-initiated input dominates.
  - [ReCache](https://arxiv.org/pdf/2608.19662) (Aug 2026): tool schemas recurring in different orders.
  - ["Don't Break the Cache"](https://arxiv.org/pdf/2601.06007v1) (Jan 2026).
- **What, exact part:** our resident sessions already reuse everything up to the new turn. Measure how often Codex's history rewriting (compaction, truncated tool outputs, reordered tool lists) breaks the prefix in real sessions. Where the client causes the break, fix it in our Codex patches so the prefix stays stable. That's the exact way to get Leyline's benefit.
- **What, not exact part:** splicing (position-shifted reuse) is not exact, so it's 2.0 and gated by a night like M8.
- **Expected:** continued-turn TTFT toward ≤ 0.15 s on turns that today re-prefill; the share of such turns is to be measured.
- **Measure:** the prefix hit rate per turn in recorded Work sessions, before and after.

### M6. Concurrency that overlaps instead of serialising (C2, done right)

- **Research:**
  - [NanoFlow](https://arxiv.org/abs/2408.12757) (OSDI 2025): nano-batches pipelined so compute-, memory- and network-bound operations overlap within one device, with execution-unit scheduling and an interference-aware auto-search. The authors report 1.91× throughput.
  - [POD-Attention](https://arxiv.org/abs/2410.18038): one attention kernel running prefill and decode concurrently on the same SMs. It reports up to 59% faster attention, and +22% in Sarathi-Serve.
  - [ExpertPlex](https://arxiv.org/pdf/2607.18002): static green-context SM partitions struggle with load variation.
- **What:** the C2 note §10 already names the fixes this research supports:
  - **run the prefill chunk one layer behind the decode,** so its state kernels overlap the next layer's projections (NanoFlow's pipelining);
  - **a POD-style attention kernel,** so the chunk's attention no longer slows down beside the GEMMs;
  - **dynamic SM shares** instead of a fixed 40-SM green context.
- **Exact:** yes, by construction, as C2 was.
- **Expected:** the slowest of 4 streams from 26–29 to ≥ 45 tok/s. C2's model predicted ~50 tok/s per decoding stream if the overlap were real.
- **Measure:** warm 4 and cold 2/4 with the 2-decoder probe, against W11.

### M7. Memory: paged or virtual, and how much

- **Research:**
  - [vAttention](https://arxiv.org/abs/2405.04437): contiguous virtual KV with physical pages mapped on demand through CUDA's virtual-memory APIs. Attention kernels stay unchanged; the authors report up to 1.99× decode throughput over vLLM's paged path.
  - [MagicDec](https://arxiv.org/abs/2408.11049) (ICLR 2025): at long context and batch, decode is KV-bound, and speculative decoding still pays when the draft reads a sparse or windowed KV.
- **What:**
  - P4 continues as planned.
  - Run a one-day vAttention comparison on GB10's unified memory: if VMM mapping works there, the paged-addressing changes in every KV site (P4a) become optional, which lowers both risk and kernel cost.
  - For the drafter at long contexts, try MagicDec's windowed KV read.
- **Exact:** yes for both.
- **Expected:** 4 × 262K sessions fit (the P4 goal); MagicDec's drafter gains grow with context.
- **Measure:** P4's own gates; and drafter step time at 128K and 262K, with and without the windowed KV.

### M8. A lower-bit KV cache (not exact; 2.0, owner decision)

- **Research:**
  - [UltraQuant](https://arxiv.org/pdf/2606.20474) (Jun 2026): FP4 KV for context-heavy agents, reporting 2.3× TTFT and 1.63× throughput over FP8.
  - [NVIDIA's NVFP4 KV](https://developer.nvidia.com/blog/optimizing-inference-for-long-context-and-large-batch-sizes-with-nvfp4-kv-cache/): < 1% loss reported on code and long-context benchmarks.
  - [vLLM's FP8 KV write-up](https://vllm.ai/blog/2026-04-22-fp8-kvcache): a long-context accumulation bug took a needle test from 91% to 13% until it was fixed.
- **What:** FP8 KV first (Husky comparison: ×1.06 at 41K, −19 ms per step at 128K), with the accumulation kept in FP32. NVFP4 only if FP8 passes.
- **Gate:** a night against the exact engine, no loss in solved rate, plus a long-context needle test.
- **Expected:** +6% at 41K, ~+15–20% at 128K, and half the KV memory.

### M9. The whole step overlaps its own weight loads

- **Research:**
  - [Mirage Persistent Kernel](https://arxiv.org/abs/2512.22219): compiler-generated megakernels, reporting up to 1.7× lower latency than kernel-per-operator serving at small batch.
  - [Ada-MK](https://www.emergentmind.com/papers/2605.11581) (May 2026): +23.6% single-batch over TensorRT-LLM.
  - Husky and our own BACKLOG 28 agree on when this pays: a megakernel only helps where it overlaps the next stage's weight loads with the current stage's tail.
- **What:** not another whole-step kernel (we built stages; they were exact and slower). Instead, a cross-layer prefetch: while layer *k*'s attention or DeltaNet runs, start streaming layer *k+1*'s first projection weights into L2. C2's L2-prefetch experiment already took the FP8 prefill GEMM from 98 to 160 GB/s.
- **Exact:** yes.
- **Expected:** +3–8% on decode, a bandwidth-efficiency gain on a step that is ~78% weight read.
- **Measure:** step time at 4K and 32K, 1 and 4 streams.

### M10. Hybrid-state snapshots

- **Research:**
  - [STree](https://arxiv.org/html/2505.14969v2) (NeurIPS 2025), [TreeWY](https://arxiv.org/pdf/2608.20961): tree verification for hybrids whose recurrent layers can't simply rewind.
  - [FlashInfer GDN](https://docs.flashinfer.ai/generated/flashinfer.gdn_prefill.chunk_gated_delta_rule.html): state in and state out.
- **What:** cheap snapshots of the DeltaNet states at turn and branch boundaries. That makes deeper trees (M1, M3) cheaper to reject, and lets parallel attempts (Night Shift best-of-3) fork from one prefilled state instead of prefilling three times.
- **Exact:** yes.
- **Expected:** best-of-3 nights read the shared prompt once, saving ~2× on their prefill. Deeper trees cost less to roll back.

## 4. How we prove it: the receipts

The 2.0 write-up (the owner's decision: published with the 2.0 engine-default release, as Dreamference and dgxcoder) needs a benchmark designed like Husky's, but agent-shaped.

1. **The agent suite:** SWE-bench Verified (and the weekly multilingual subset) on the same GB10, the same weights, our harness. Report solved, wall time per task and tokens per task, engine against SGLang and vLLM, with the arms alternated as in our nights.
2. **The step suite:** 16 agent-shaped prompt types, Husky's method applied to our work: patch a function, rewrite a file, add a test, read a 40K log then answer, call tools in a loop, summarise a diff, and so on. Three runs each in a quiet window, a canary first, repeats within 12%.
3. **Continued-turn TTFT** with resident sessions, against SGLang's prefix cache and against a cold re-prefill.
4. **Concurrency:** 1/2/4 agents, the slowest stream reported, not only the pooled rate.
5. **Exactness receipts:** greedy outputs compared token by token against single-stream decoding after every change. This is our edge: SGLang matched its own one-stream output on only 2–5 of 16 turns.

## 5. Order and effort

Ranked by agent wall-time gain for the effort. Every item is in 2.0; none before Ragon 1.9.0.

| # | Lever | Exact | Expected | Effort | Gate |
|---|---|---|---|---|---|
| 1 | M4 prefill | yes | cold turns ~2× faster | 2–3 weeks | profile first |
| 2 | M6 concurrency overlap | yes | slowest stream 26 → ≥ 45 tok/s | 2–3 weeks | after P4 |
| 3 | M1 long copies across session, attempts and agents | yes | edits ≥ 2× | CPU replay 2 days, then 2 weeks | replay ≥ 2× on edits |
| 4 | M2 agent drafter | yes | +19% or more | ~1 week of nights | prompt and tool freeze |
| 5 | M3 adaptive speculation | yes | +15–30% at 4 streams | 1 week | — |
| 6 | M10 state snapshots | yes | best-of-3 prefill ÷2 | 1 week | — |
| 7 | M7 vAttention trial, windowed-KV drafting | yes | risk and kernel-cost cut; long-context drafter speed | 1 day trial | — |
| 8 | M9 cross-layer prefetch | yes | +3–8% | 1 week | — |
| 9 | M5 stable prefixes (exact part) | yes | TTFT on broken-prefix turns | 1 week | measure share first |
| 10 | M8 FP8 KV | **no** | +6–20%, ½ KV memory | 1–2 days plus a night | night, no loss |

## 6. Non-goals

- The Metal and Mac port is a separate 2.0 item.
- No change to the model or its weights; that's out of scope here.
- No cloud offload, and no telemetry.
- No claim of beating Husky on its own turf (a 4B model on a laptop). The claim is about agents.

## 7. Open questions for the owner

1. **M1(b), copying across agents in one node:** is it acceptable that one agent's session text is used to *propose* tokens for another agent's session on the same node? Proposals never change the answer (the target model verifies them), but it is cross-session data flow inside the engine. Proposed default: same owner and same repository only.
2. **M5's not-exact splice and M8:** confirm both stay behind the "night shows no loss" gate in 2.0.
3. **The receipts benchmark (§4):** do we also run vLLM as a third arm? It costs one more night per comparison.

## 8. References

The authors' own claims are cited as reported and haven't been verified here.

- Husky: https://husky.underdog.ai/
- SuffixDecoding: https://arxiv.org/abs/2411.04975; Arctic Inference: https://www.snowflake.com/en/engineering-blog/suffixdecoding-arctic-inference-vllm/
- DLite: https://arxiv.org/abs/2610.23787; DSpark: https://arxiv.org/html/2607.05147v1
- Acceptance-aware draft training: https://arxiv.org/abs/2609.24150; parallel drafters: https://arxiv.org/pdf/2609.27396
- DFlash: https://arxiv.org/abs/2602.06036
- SmartSpec: https://arxiv.org/html/2406.14066v1; FlexDraft: https://huggingface.co/papers/2605.20022; PACER: https://arxiv.org/abs/2602.01274; AdaEDL: https://arxiv.org/pdf/2410.18351; BanditSpec: https://arxiv.org/pdf/2505.15141
- NanoFlow: https://arxiv.org/abs/2408.12757; POD-Attention: https://arxiv.org/abs/2410.18038; ExpertPlex: https://arxiv.org/pdf/2607.18002
- vAttention: https://arxiv.org/abs/2405.04437; MagicDec: https://arxiv.org/abs/2408.11049
- UltraQuant: https://arxiv.org/pdf/2606.20474; NVFP4 KV: https://developer.nvidia.com/blog/optimizing-inference-for-long-context-and-large-batch-sizes-with-nvfp4-kv-cache/; vLLM FP8 KV: https://vllm.ai/blog/2026-04-22-fp8-kvcache
- Mirage Persistent Kernel: https://arxiv.org/abs/2512.22219; Ada-MK: https://www.emergentmind.com/papers/2605.11581
- Leyline: https://papers.cool/arxiv/2606.01065; CacheWise: https://www.emergentmind.com/papers/2606.16824; ReCache: https://arxiv.org/pdf/2608.19662; Don't Break the Cache: https://arxiv.org/pdf/2601.06007v1
- STree: https://arxiv.org/html/2505.14969v2; TreeWY: https://arxiv.org/pdf/2608.20961; FlashInfer GDN: https://docs.flashinfer.ai/generated/flashinfer.gdn_prefill.chunk_gated_delta_rule.html; Erase-then-Delta: https://arxiv.org/pdf/2606.26560
- Copying moonshot (AgSpec, EfficientEdit, CopySpec and others): https://github.com/dreamference/ling-engine/blob/main/specs/drafts/MOONSHOT-repository-aware-decoding-2026-10-13.md
