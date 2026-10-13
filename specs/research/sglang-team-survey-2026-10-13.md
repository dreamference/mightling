# What the SGLang team's work offers Mightling (2026-10-13)

**Status:** research survey; nothing was built and no GPU was used. One analysis is new and computed from data already on disk: the two-stream prefill interference in §2.1, from M4 stage 4's two `ling-serve` request logs on second-puffin (`~/m4s4/runs/s4c2.serve.log`, `s4c2b.serve.log`).

**Question:** what in the published work of the SGLang team could do any of the following for one 27B hybrid model (Qwen3.8-27B NVFP4 with DFlash2 at 12 drafts) on one or two GB10s, air-gapped?
- make ling-engine or the production server faster or more correct;
- sharpen our benchmarks;
- give Mightling new features.

The work read comprises the LMSYS blog, RadixArk, the SGLang papers, the SGLang docs and code, and the GitHub RFCs and issues.

**Reads with:**
- ling-engine:
  - `specs/DREAMFERENCE_LING_ENGINE_MILESTONES.md` §14 ("Order") and `specs/DREAMFERENCE_LING_ENGINE_BACKLOG.md` §19;
  - `specs/drafts/M4-sessions-and-batches-2026-10-10.md` ("Stage 4 as built");
  - `reports/backlog-2026-10-09.md` (the B items);
  - `reports/github-survey-2026-10-09.md` and `reports/research-2026-10-09.md`.
- This repository: [together-ai-survey-2026-10-13.md](./together-ai-survey-2026-10-13.md), [agent-survey-2026-10-09.md](./agent-survey-2026-10-09.md), [SELF_SPEEDING](../DREAMFERENCE_SELF_SPEEDING.md), [MIGHTLING_SWE_BENCH_FAILURES](../DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md) §6.5.

**How numbers are labelled:**
- Numbers from SGLang's posts, papers and issues are the authors' own, on their hardware, read on the date above.
- Numbers measured on our machines say so.
- Anything else is an estimate and is marked as one.
- "Exact" means the output is byte-identical to the engine's own non-speculative decoding (the engine's rule). A lossy idea is at most an arm for a benchmark night.

---

## Short answer

1. **Production has two live correctness exposures in this exact configuration, and the SGLang team's own issue tracker documents both.**
   - **The DFlash checkpoint-crossing bug.** When accepted draft tokens cross a GDN checkpoint boundary, SGLang v0.5.19 skips the state save. A later prefix-cache hit then pairs full-attention KV at the new length with a stale recurrent state ([#37817](https://github.com/sgl-project/sglang/issues/37817)).
     - The fix ([#37818](https://github.com/sgl-project/sglang/pull/37818)) took warm-against-cold next-token mismatches from 24/56 to 2/56, and boundary+1 cases from 8/8 to 0/8.
     - It is in v0.5.20 and v0.5.21, **not in the pinned v0.5.19** (checked against the tags).
     - Production runs DFlash2 with the radix cache and `extra_buffer`, which is the affected path, and agent sessions hit long prefixes on every turn.
   - **Concurrency corruption on GB10 with our checkpoint.** On a DGX Spark with `RadixArk/Qwen3.8-27B-NVFP4` and DFlash2, 111 of 304 greedy answers were wrong at concurrency 8, against 0 of 100 serial ([#35860 comment](https://github.com/sgl-project/sglang/issues/35860)).
     - The root-cause threads, [#36548](https://github.com/sgl-project/sglang/issues/36548) and [#35150](https://github.com/sgl-project/sglang/issues/35150), are both **still open**.
     - Their localisation: SGLang's GDN verify path is not bit-equal to its own decode. A width-one verify with every draft rejected diverges from plain decode on 19/32 cases.
     - The packed-FP4 LM head turns that drift into flipped argmaxes. The BF16-head export brings it to 1/304.
     - Upgrading production fixes the first, at about half a day of re-validation; the second has no upstream fix (§2.9).
   - **What it means for ling-engine.** It is the strongest external argument for exact speculation: ling-engine's `--spec-check` already requires tokens *and* recurrent state equal to plain decoding.
2. **Mixed chunk against the step savings for the two-stream gap: the savings block closes it; the mixed batch is the only change that also lowers the worst case. SGLang's own mixed chunk is not a design to copy.**
   - **The measurement (new, from stage 4's logs).**
     - 547 and 637 prefill chunk passes ran beside a decode, in 3,849 and 4,370 decode passes (pooled per-stream period 150.0 and 153.4 ms).
     - Each pass carries a fixed cost of about 100–124 ms that production pays once per prefill, not once per 256 tokens.
     - Production schedules prefill first, in chunks of up to 8,192 tokens. Most warm turns there are one pass; in ling-engine they are 2–4, split by the 256-token contended size and by checkpoint bounds.
   - **The options (estimates from the same logs):**

     | Option | Effect on the mean period | Notes |
     | --- | --- | --- |
     | The step-savings block already planned | −10 to −13 ms | about 137–146 ms |
     | Running a short turn's whole prefill (≤ 512 new tokens) in one pass | −1.5 to −2 ms | exact, a scheduler rule; must keep the checkpoint it crosses |
     | A contended chunk of 512 | −7 ms | raises the worst wait to about 380–400 ms |
     | A true mixed batch | −11 to −12 ms | cuts the worst wait to about 190–230 ms |

   - **Why a true mixed batch needs a new kernel.** It is exact only with a fused GEMM that serves the decode rows' FP16 numerics and the prefill rows' NVFP4/FP8 numerics from one weight read.
   - **Why SGLang's version is no model.** With speculation on, SGLang's mixed step degrades each running request to a one-token decode, so a stream gets 1 token from that pass instead of ~5 ([`mix_with_running`, v0.5.19](https://github.com/sgl-project/sglang/blob/v0.5.19/python/sglang/srt/managers/schedule_batch.py)). On hybrid GDN models it also corrupts radix checkpoints ([#39342](https://github.com/sgl-project/sglang/issues/39342), open).
   - Details in §2.1.
3. **Structured output (plan item 4) has a complete, production-tested design to follow, and one new exact speedup.**
   - **SGLang's design with DFLASH:**
     - the drafter stays unconstrained;
     - the host walks the draft chain against the grammar *after* launching the verify, filling one vocabulary mask per verify row;
     - the masks are applied to every row before acceptance;
     - grammar-invalid drafts simply fail verification.
   - **XGrammar** builds a mask in under 40 µs per token for JSON. Twelve rows per stream fit inside the ~110 ms verify many times over.
   - **XGrammar-2's TagDispatch** constrains only inside tool-call tags. Its worked example is the `<function=…>` shape our model emits, with ~10 ms compiles on dynamic tool sets.
   - **New and exact: grammar-forced drafts.** Where the token-level mask allows exactly one token, put that token in the draft block. The masked target must accept it.
   - SGLang's compressed-FSM "jump-forward" is *not* exact under our rule: it re-tokenizes the forced text (§2.5).
4. **The drafter: SpecForge ships a DFlash2 recipe for exactly Qwen3.8-27B, and the SGLang team names target-regenerated data as "the single largest lever on final acceptance".**
   - This sharpens SELF_SPEEDING. The recipe trained on 1.07M conversations regenerated by the target, mean 4.5K tokens.
   - The DFlash paper's long-context fine-tune (1.6K samples, 3 epochs) took acceptance on a 27B drafter from 3.61 to 6.05 at 16K tokens of context.
   - Our replay's median context is 23.7K. Acceptance by context bucket (B10) is therefore the measurement to take before any training (§2.4).
5. **A set of cheap correctness tests for ling-engine's checkpoint store, each copied from a bug SGLang shipped** (§2.6):
   - the checkpoint-crossing table of #37818;
   - warm against cold at boundary ±1;
   - "the same prompt, 32 copies at once, count distinct completions" ([#36291](https://github.com/sgl-project/sglang/issues/36291));
   - a greedy request re-run after sampled requests to catch state pollution.
6. **Benchmarks** (§3).
   - Our own nights put noise discordance at ~18% of tasks between identical arms. At that rate a 10-point difference needs about **155 paired tasks** for 80% power, a 5-point one about 626. No 44–50-task night can separate the arms we run.
   - **ling-engine changes this once it serves a night.** Its per-session seeded sampling gives both arms the same random stream ("common random numbers"). Production cannot: the deterministic flag is accepted and has no effect on this GB10 configuration ([#36291](https://github.com/sgl-project/sglang/issues/36291)).
   - The SGLang posts add a measurement discipline worth copying for the engine:
     - calibrate profiled against unprofiled runs;
     - use cold-weight microbenchmarks;
     - decide A/Bs on step time derived from mean TPOT × mean accept length, never on peak windows.
7. **Product** (§4).
   - A `/v1/decisions`-style scoring path: probabilities over typed answers from one prefill, validated on Qwen3.8-27B. It is the cheap selector that the Together survey's best-of-K and Night Shift's triage need.
   - Priority between the interactive session and Night Shift.
   - Session close and pin hints from the launcher.
   - A checkpoint store that survives an engine restart, keyed with an "environment stamp".
8. **Two nodes, for the end of the plan: pipeline parallelism is exact where tensor parallelism is not.**
   - An estimated +25–50% per stream at two streams, and nothing at one stream.
   - It belongs beside the TP note as the exact alternative (§2.10).
9. **Not worth building, and why:** §5.
   - Lossy: NVFP4 KV, which cost our model 1.6 SWE-bench Verified points in SGLang's own test.
   - At the wrong scale: PD multiplexing on green contexts, PD disaggregation, two-batch overlap.
   - Already superseded here: deterministic mode, overlap scheduling, ReplaySSM.

**What it changes in the plan** (proposals in §7; nothing is changed without the owner):
- Production: move to SGLang ≥ v0.5.20 now; probe the open concurrency bug at two streams; drop `--num-continuous-decode-steps 2`, which is dead code in v0.5.19.
- ling-engine:
  - add one exact scheduler rule (short turns in one pass) beside the savings block;
  - add the checkpoint tests to M4 stage 5;
  - carry the masking design and grammar-forced drafts into structured output;
  - keep the mixed batch outside the order, with its real price written down: a dual-numerics GEMM.

---

## 1. Method and scope

**What was catalogued:**
- **The LMSYS blog: all 113 posts from November 2023 (the first SGLang-era post) to October 2026**, from the site's source repository ([lm-sys/lm-sys.github.io, `blog/`](https://github.com/lm-sys/lm-sys.github.io/tree/main/blog)), because the rendered site is script-only.
- RadixArk's site and model cards.
- The SGLang docs and cookbook.
- The SGLang code at the tags v0.5.19 (production) and v0.5.21, and `main` on 2026-10-13.
- The SGLang 2026 Q4 roadmap ([#42473](https://github.com/sgl-project/sglang/issues/42473)) and the issues and PRs listed in Sources.

**Read in full (post or paper text):** 61 of the 113 posts, every one that touches serving, scheduling, caching, speculation, structured output, quantization, vision, local hardware, agents or benchmarks; two more in part. Sources marks each item as full or partial. One post read in full, about another vendor's model on DGX Spark, is left uncited because nothing in it applies.

**Skipped as outside the question:**
- Chatbot Arena leaderboard posts.
- Diffusion and video-generation serving (except one kernel note).
- TTS and audio.
- TPU, AMD and Intel deployments.
- Large-scale RL infrastructure (Miles), except what bears on drafter training.
- Model day-0 posts for MoE models with no hybrid or speculative content.

**Papers read:**
- SGLang (NeurIPS 2024, §3–6 and App. B), XGrammar, XGrammar-2 and DSpark (main text), in full.
- DFlash: method, training, long-context and ablation sections.
- PD-Multiplexing, in part.
- SpecForge (ICML 2026): abstract and results only.

**Code claims** quote the file at the tag named; each was checked against the source by grep, not from a summary.

**Already covered elsewhere, so not repeated:**
- DFlash2 tree verification (SGLang #31069), FlashInfer's GDN tree kernels and the sparse drafter head: the GitHub survey of 2026-10-09.
- Online drafter training (Aurora/ATLAS), suffix trees (DAS) and tool-wait eviction (ThunderAgent): the Together survey.

## 2. Engine and production speed

The engine's measured state is ling-engine M4 stage 4, merged 2026-10-13:
- **One stream:** ~103–104 ms per step at ~5 tokens per step. About 82 ms of it is the 17.6 GB weight read at ~215 GB/s.
- **Two streams:**
  - the batched step is ~130 ms: draft 13.3, verify and accept ~110, commit ~7;
  - the pooled per-stream period is 151.9 ms (33.4 tok/s), against production's 145 ms (33.9 tok/s);
  - 13–14% of periods wait about 267 ms behind the other stream's 256-token prefill chunk.

### 2.1 The two-stream gap: mixed chunk against the step savings

**How SGLang schedules a prefill beside running decodes** (v0.5.19, read in the code):
- **Production does not pass `--enable-mixed-chunk`.** `Scheduler.get_next_batch_to_run` runs a waiting prefill batch first ("Run prefill first if possible"), and a chunked request in flight is always continued.
  - So every chunk of a long prompt runs back to back as a prefill-only pass of up to `--chunked-prefill-size` tokens, 8,192 in production. The other stream's decode waits for the whole prefill.
  - An open PR states the policy outright: "Never interleave decode while an active chunked request must continue" ([#31395](https://github.com/sgl-project/sglang/pull/31395)).
  - The cookbook for this model says the same on faster cards: "8192-token chunks stall them ~600ms at a time" ([Qwen3.8-27B cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B)).
- **Mixed chunk is allowed with DFLASH in the code, despite the docs.**
  - `SpeculativeAlgorithm.supports_mixed_chunk()` returns true for EAGLE, EAGLE3, DFLASH and DSPARK ([`spec_info.py` at v0.5.19, line 132](https://github.com/sgl-project/sglang/blob/v0.5.19/python/sglang/srt/speculative/spec_info.py)), since [#36933](https://github.com/sgl-project/sglang/pull/36933) (in the v0.5.19 release notes).
  - The docs' method table still says DFLASH "disables overlap scheduler & mixed chunked prefill" ([speculative decoding docs](https://docs.sglang.io/docs/advanced_features/speculative_decoding)). That row is stale.
- **What a mixed step does to a speculating request.** In `mix_with_running`, every running request is set to `set_extend_range(prefix_len, prefix_len + 1)`: a one-token decode with no speculation in that pass. The validation hook's comment says: "Running requests degrade to a plain 1-token decode inside a mixed step."
- **On hybrid GDN models mixed chunk corrupts the radix cache.** The mixed batch skips the `extra_buffer` checkpoint write, but the unwritten slot is still donated to the tree ([#39342](https://github.com/sgl-project/sglang/issues/39342), open; fix [#39526](https://github.com/sgl-project/sglang/pull/39526) open).
  - Measured on v0.5.16 at temperature 0: exact match 0.84–0.86 with mixed chunk on, against 0.91 off.
  - A PR to split the GDN kernels for mixed batches ([#36065](https://github.com/sgl-project/sglang/pull/36065)) was closed unmerged.
- **The knobs that bound the stall instead:**
  - `--prefill-decode-interval N`, in v0.5.19 and default 0: N decode rounds after each prefill batch, between chunks of one prompt too.
  - `--max-consecutive-prefill-batches` ([#32775](https://github.com/sgl-project/sglang/pull/32775), draft). It answers a report of 2,863 prefills against 36–38 decodes in 15 minutes with speculation on ([#32549](https://github.com/sgl-project/sglang/issues/32549)).
  - Mixed-chunk decode interleaving ([#31395](https://github.com/sgl-project/sglang/pull/31395)): mean TPOT 217.62 → 52.35 ms in a burst on 8×H200.

**What our logs say (new; computed from stage 4's two replay runs at two streams).**
- Each `ling-serve` request line records its prefill's new tokens, its chunk count, how many chunks ran beside a decode, and the compute rate.
- **Where the chunks come from:**
  - Of the prefills that ran beside a decode, 74 and 77 took 1–2 chunks, 34 and 31 took 3–4, and a dozen new-session prefills took 13–89 chunks each.
  - Prefills of ≤ 1,024 new tokens (mean ~415) account for 236 and 235 of the 547 and 637 chunk passes; the long new-session prefills account for the rest.
- **What a chunk pass costs.** Two calibrations bracket it:
  - The stage-2 sweep at 34K (contended chunks of 128, 256 and 512 tokens, period minus the one-row step) gives about **100 ms + 0.35 ms per token**. Stage 4's period split (267 − ~104 ms ≈ 163 ms per 256-token pass) agrees with it.
  - A fit to the request lines' own compute times (82–256 tokens per chunk at ~23–34K) gives about 124 ms + 0.34 ms per token. Those times are host-side for every chunk but the last (only the last synchronizes), so this is the upper reading.
  - The estimates below use the fixed part as a 100–124 ms range. Most of it is what any pass costs: the weight read (~82 ms) and the per-layer work that does not scale with rows.
- **Why a short turn takes several passes.** `Engine::begin_prefill` ends a pass at every checkpoint bound (the ends of the first two messages, and every 2,048 positions up to the 32,768-token checkpoint limit), and `prefill_chunk` caps a contended pass at 256 tokens. A 228-token turn starting at position 30,624 therefore takes two passes, split at 30,720.
- **Why production's number is lower.** A prefill split into k chunks pays the fixed part k times. Production pays it once per ≤ 8,192 tokens.
  - That is the structural reason production's pooled period is lower even though its single stalls are far longer.
  - The pooled metric the M4 gate uses rewards few large stalls, while the owner's bound (2026-10-12: about twice the two-stream period) rewards many small ones. The two pull in opposite directions; only a mixed batch improves both.

**The options, priced against the logs.** All are estimates; the base is the pooled period of the two runs, 150.0 and 153.4 ms.

| Option | Pooled period (two runs) | Worst wait beside a prefill | Exact? | Cost | Where it is |
| --- | --- | --- | --- | --- | --- |
| Today (stage 4) | 150.0 / 153.4 | ~330 ms | — | — | — |
| **The step-savings block** (B16, B07, commit overlap, B31, sparse head, B26): −10 to −13 ms off every batched step, a little less off one-row steps | **~137–143 / ~140–146** | ~255–320 ms | yes | ~1 week (already estimated) | in the plan, item 1 |
| **Short turns in one pass:** if the whole prefill is ≤ 512 new tokens, run it as one pass, keeping the state at any checkpoint bound it crosses | 148.1–148.5 / 151.7–152.0 (**−1.5 to −2 ms**); a 768 cap would give −2.8 to −3.5 ms but breaks the bound | ~385–400 ms on those passes (104 + T(512)), inside the owner's ~440 ms bound; a 768 cap would reach ~470–490 | yes, if the crossed checkpoint's state is captured inside the pass (as the end-of-prompt snap already is, `keep_at`) or that one checkpoint is knowingly skipped; this is #37818's bug class, so §2.6 item 1's test applies | ~1–2 days + one replay pair | **new** |
| Contended chunk 512 for every prefill | ~143 / ~146 (**−7 ms**) | ~380–400 ms (stage 2 measured 376 mean at 34K) | yes | a flag + one replay pair | against the owner's 256 (2026-10-12) |
| **True mixed batch:** the chunk's rows and the partner's verify rows in one pass, one weight read | ~139 / ~142 (**−11 to −12 ms**): each of the 547/637 chunk passes stops costing the partner its own ~104 ms one-row step, keeping ~25 ms of drafter, attention, DeltaNet verify and commit | ~190–230 ms | **only with a dual-numerics GEMM**: the decode rows must keep the rows path's FP16 activations while the prefill rows use NVFP4/FP8 activations, from the same weight tiles | weeks: a new fused GEMM for every projection, plus graph and scheduler work | outside the order (MILESTONES §14) |
| SGLang's mixed chunk under speculation (decode rows become a 1-token decode) | worse: the partner gets 1 token per ~T+10 ms instead of ~5 per ~T+104 | lower | — | — | not a candidate |

What this means:
- **The savings block closes the mean gap by itself** if it lands near its estimate, as the M4 draft's own arithmetic already said (~7 ms needed).
- **Short turns in one pass is a cheap exact addition,** worth −1.5 to −2 ms, and it stays inside the bound only at a 512-token cap. Its one subtlety is the checkpoint bound a merged pass crosses.
- **The mixed batch is the only option that improves the mean and the worst case together,** and its price is now concrete: a dual-numerics GEMM, not a scheduler change.
  - **Recommendation:** keep it out of the order; re-decide at stage 5 if the gate run shows the chunk tax still dominant.
  - It weighs most where long new-session prefills meet a decoding stream, for example Night Shift starting a task beside an interactive session.
- A second CUDA stream for the prefill (the M4 draft's stage 6) is the cheaper exact relative. SGLang's PD-Multiplexing does it with green contexts (§5). It overlaps the prefill's compute with the decode's weight read, but both read all the weights, so it saves less than the mixed batch on a bandwidth-bound box.

### 2.2 Host run-ahead and overlap scheduling: maps to B08, worth ≤ 1 ms

**What the SGLang team did:**
- **Spec V2.** "Chasing the Batch-1 Floor" ([post](https://lmsys.org/blog/2026-08-21-ling3-flash-spec-decode-blackwell/)) and the DFlash/Spec V2 post ([post](https://lmsys.org/blog/2026-06-15-next-generation-speculative-decoding-dflash-v2/)) take every accept-dependent count off the host.
  - Step k's graph writes accepted tokens and lengths into device buffers indexed by request slot, and step k+1's graph reads them.
  - The host learns the accept count one step late through a side-stream copy.
  - One forgotten `needs_cpu_seq_lens` flag had pinned the host to the GPU for 485 µs per step. A blocking copy inside an attention "plan" call left 47% of a DSpark step idle.
  - Spec V2 with overlap: +33% (11.4 → 15.3K tok/s) on Qwen3-8B at concurrency 32 on one B200 ([#23000](https://github.com/sgl-project/sglang/pull/23000)), and ~2 ms per step at batch 1 (845.98 → 1,161.88 tok/s at accept 6.35, the same PR).
- **For us.** ling-engine is C++ with two host round trips per step (the selector walk after the draft, the accept after the verify), "a few tens of microseconds" each. M3 measured GPU idle inside the step at 1.1 ms (`reports/M3.md`).
  - The accept on the device with the host one step late is B08.
  - The ceiling is the 1.1 ms of idle, not SGLang's figures, which are Python scheduler costs.
  - **No new item.** B08 gets one design rule from the post: no blocking device-to-host read anywhere on the step's path.

### 2.3 Speculation: DSpark, adaptive verify length, draft depth

- **DSpark** ([post](https://lmsys.org/blog/2026-07-06-dspark-sglang/); [arXiv 2607.05147](https://arxiv.org/abs/2607.05147)) adds a sequential head and a calibrated confidence head to a DFlash-style drafter, then trims the verify to the length that maximises expected tokens per second.
  - It is exact as long as the trimming decision is causal; the production version uses confidences from two steps earlier.
  - The post itself: "At batch size 1 the target verify does not slow down much with more tokens, so trimming saves little and the two arms tie."
  - Our two streams verify 26 rows, inside the rows that are nearly free here. **No gain from trimming.**
  - The DSpark drafter for our target loses to DFlash2: acceptance 4.36 against 5.46 on GSM8K, 3.30 against 4.39 on HumanEval ([#35371](https://github.com/sgl-project/sglang/pull/35371)).
  - **What transfers is the cost table.** Choosing a proposal length by expected accepted tokens divided by the profiled step time T(rows) is the rule moonshot P1 needs once a 64–128-token copy plus the chain exceeds the free rows. It is design input for P1, not an item.
- **Draft depth.**
  - The batch-1 post's sweep gives a break-even rule, "Δaccept > 0.05 × accept", for one more draft position at their ~5 ms step.
  - M0 already swept 8–16 drafts on our replay and chose 12 (+7%).
  - The SGLang cookbook's DFlash2 recipe uses 8 (the drafter's trained block), which M0 measured here at −3%. **Nothing to change.**
- **`--speculative-adaptive`** (EMA of accepted length, one graph set per depth; [docs](https://docs.sglang.io/docs/advanced_features/speculative_decoding)): EAGLE only, and at batch 1–2 it would sit at its top tier. Not applicable.
- **LiLiCorr** (v0.5.21, `SGLANG_ENABLE_LILICORR_SAMPLING`) re-ranks the DFlash top-K lattice and "Verify stays exact".
  - The docs cite about +6% acceptance at temperature 1 on Qwen3-8B.
  - Read only in the docs; no drafter for our model is published.
  - Recorded to watch, beside the selector work of item 19 and M2.

### 2.4 The drafter: SpecForge's recipe for our model, and long context

**What exists** ([SpecForge v0.3 post](https://lmsys.org/blog/2026-08-04-specforge-v0-3/); recipe [`docs/recipes/qwen3.8-27b-dflash2-disaggregated.md`](https://github.com/sgl-project/SpecForge/blob/main/docs/recipes/qwen3.8-27b-dflash2-disaggregated.md) and [`configs/qwen3.8-27b-dflash2.json`](https://github.com/sgl-project/SpecForge/blob/main/configs/qwen3.8-27b-dflash2.json)):
- **A DFlash2 recipe for `Qwen/Qwen3.8-27B`.**
  - Five draft layers at width 5,120, block 8, a top-16 candidate selector of rank 256, reading target layers 5, 19, 33, 47 and 61.
  - Its layout "matches the public `incoai/Qwen3.8-27B-DFlash2` checkpoint".
- **The training data and losses.**
  - 1.07M conversations "whose assistant turns were regenerated by" the target, mean 4.5K tokens, 90th percentile at the 8,192-token cap.
  - Loss: cross-entropy decaying along the block, plus a selector cross-entropy over the target head's strict top-16.
- **Capture.** The hybrid target's hidden states come from a patched SGLang capture server; the trainer never runs the target.
- **How the team ranks the levers.** "regenerating dataset responses with the target model — greedily, in the reasoning mode that will be served — has been the single largest lever on final acceptance."
  - The SpecForge paper puts regeneration at +5.3% throughput on average and a broader data mix at a further 1.17× ([arXiv 2603.18567](https://arxiv.org/abs/2603.18567); abstract and results only).
- **The losses to add in any fine-tune:**
  - D-PACE: emitted length +8.5–12.8% on 4–8B targets ([arXiv 2605.18810](https://arxiv.org/abs/2605.18810), results only);
  - LK: "up to 8-10%" (abstract).
  - Neither has been tested on DFlash2, and they need not add.

**Long context** ([DFlash, arXiv 2602.06036](https://arxiv.org/html/2602.06036v2) §5.4, Table 4). A Qwen3.5-27B DFlash drafter fine-tuned on 1.6K LongAlign samples for 3 epochs. Acceptance, base drafter → fine-tuned:

| Context | hotpotqa | gov_report |
| --- | --- | --- |
| 1K | 4.91 → 4.99 | 4.53 → 4.53 |
| 8K | 4.46 → 5.76 | 3.32 → 4.04 |
| 16K | 3.61 → 6.05 | 2.67 → 3.81 |
| 32K | — | 2.09 → 3.56 |

The same paper: a drafter trained at a larger block generalises to smaller inference blocks, "However, the reverse does not hold."

**What it means for us:**
- **Measure before training.** Our replay's median context is 23.7K tokens, and ling-engine's ~5.2 accepted per step was measured at ~31K.
  - So our drafter has not collapsed the way the paper's base drafter did.
  - But nobody has split our acceptance by context bucket. B10 (the logging replay) does exactly that, at no GPU cost beyond a replay.
  - If the 16–35K buckets sit well below the 1–4K buckets, a long-context fine-tune is the largest drafter lever in this survey. An *estimate* of +10–30% decode at long context, conditional on that measurement.
- **SELF_SPEEDING (owner's spec) gets four inputs:**
  - the recipe and its config as the starting point, warm-started from the served drafter;
  - **target-regenerated data in the served reasoning mode** as the first data lever, ahead of collecting more sessions;
  - long agent contexts in the training mix;
  - the one-sample overfit gate ("served, the whole block must be accepted") as the integration test.
- **Feasibility on one GB10 (estimate):**
  - The full recipe (≈4.8B tokens) is ~23 days of target prefill per epoch at 2,100–2,600 tok/s. That is a rented-node job, about 13 hours per epoch on 8×H200 by the recipe's own rate.
  - A long-context fine-tune of 1.6K × 16K tokens is ~3 hours of capture per epoch on a GB10.
  - Offline features cost ~61 KB per token (~1.6 TB for that set), so capture should be online.
  - Unverified: SpecForge and its capture patch on aarch64/SM121.
- **Exactness.** Unchanged. The drafter only proposes; this is BACKLOG §19.20's agent-tuned drafter and the self-speeding plan, with a better recipe.
- **Quantization.** The served drafter's card shows that activation-scale calibration already recovered round-to-nearest's 13% acceptance loss (3.26 → 3.60 per block of 8, against 3.71 in BF16). That leaves ScaleSearch (§19.22) its ≤ 3% bound, as the Together survey said.

### 2.5 Structured output (plan item 4): the design to follow

**How SGLang does it with DFLASH** (v0.5.19, [`speculative/spec_utils.py`](https://github.com/sgl-project/sglang/blob/v0.5.19/python/sglang/srt/speculative/spec_utils.py) and `dflash_worker_v2.py`, read in the code):
1. Before the verify launch, the draft chain becomes a degenerate tree (`GrammarTree.from_linear_chain`).
2. **After** the verify is launched, so that all of this is host work hidden behind the forward, a barrier advances the previous step's grammar over its committed tokens.
3. A depth-first walk then runs over the draft. For each row it:
   - checks the draft token against the parent row's mask;
   - if allowed, calls `accept_token`, fills this row's mask, recurses, then `rollback(1)`;
   - stops at the first grammar-invalid draft.
4. The pinned masks go to the GPU without blocking and are applied to **every** verify row's logits before acceptance.
5. The drafter is never constrained. An invalid draft is simply a rejection.

**Why the host cost is invisible** ([XGrammar, arXiv 2411.15100](https://arxiv.org/html/2411.15100)):
- Mask generation takes "under 40 µs per token for JSON Schema and CFG (JSON), and under 200 µs for XML and Python DSL", on a desktop CPU.
- That comes from an adaptive token-mask cache (fewer than 1% of tokens are context-dependent on the JSON grammar; 160 MB → 0.46 MB) and a persistent stack with constant-time rollback.
- For us (estimate): 13 rows × ≤ 40 µs ≈ 0.5 ms per stream per step, hidden behind a ~110 ms verify.
- The masks are 31 KB per row for our 248,320-token vocabulary: ~0.8 MB per step at two streams.
- The GB10's CPU cores are unmeasured for this.

**Dynamic tool sets** ([XGrammar-2, arXiv 2601.04426](https://arxiv.org/html/2601.04426)):
- **TagDispatch** leaves text free until a registered tag opens (`<function=get_weather>{…}</function>` is the paper's own example, our model's shape), then applies that tool's sub-grammar.
- Compile is ~10 ms against >1,000 ms for a monolithic grammar.
- Substructures are reused across requests: 25–96% for 10–500 tools.
- Codex sends a different tool set per request, which is exactly the case it targets.
- **Use structural tags with `<tool_call>` and `<function=` as triggers, not one EBNF over the whole output.** That is how `tool_choice: "required"` and the custom-tool grammars of BACKLOG §19.13 can be enforced without constraining the model's prose.

**New and exact: grammar-forced drafts.**
- SGLang's compressed FSM ([post](https://lmsys.org/blog/2024-02-05-compressed-fsm/); paper §4) jump-forwards over strings the grammar forces. It is +1.6× throughput on their JSON benchmark.
- It is **not exact**: it re-tokenizes the forced text, and the paper itself notes the resulting "skewed probability distribution".
- The exact version fits our verify:
  - wherever the token-level mask at a position allows exactly one token, write that token into the draft block in place of the drafter's;
  - the masked target's argmax (and its sampled token) must be that token, so it is accepted;
  - the output equals constrained non-speculative decoding.
- The gain is confined to forced runs (keys, punctuation, enum values) and is unmeasured. DFlash2 already predicts much tool-call boilerplate, so expect little on tool calls and more on schema-heavy `response_format` requests.
- **Cost:** one branch in the host walk. Add it to item 4's scope as an option measured after the masks work.

**Pitfalls SGLang hit, to avoid:**
- the grammar must be synchronized before a mixed step under speculation ([#42401](https://github.com/sgl-project/sglang/issues/42401));
- a grammar request batched with others deadlocked under DSpark with TP ([#41449](https://github.com/sgl-project/sglang/issues/41449));
- relaxed acceptance flags (`--speculative-accept-threshold-*`) and SGLang's "relaxed" penalties under speculation are lossy.

### 2.6 Hybrid-state correctness: tests to copy into the engine

Each of these is a bug SGLang shipped on a hybrid GDN model. Each maps to a cheap test for ling-engine's checkpoint store and batch path. All are exact by definition, since they only check.

1. **Checkpoint crossing during a speculative commit** ([#37817](https://github.com/sgl-project/sglang/issues/37817)/[#37818](https://github.com/sgl-project/sglang/pull/37818)).
   - A snapshot taken at a boundary during a commit must be the state after exactly `boundary − pre` accepted tokens.
   - Port the PR's table as a unit test: 255 + 1 → save at step 0; 254 + 2 → step 1; 250 + 3 → none; 256 + 1 → none.
   - Add their integration check: the next token after a warm-cache prefix hit at boundary −1, 0 and +1 equals the cold-prefill result.
2. **A mixed or interleaved step that claims a checkpoint slot and never writes it** ([#39342](https://github.com/sgl-project/sglang/issues/39342)). ling-engine's rule "publish a checkpoint only after its copy has completed" already cites a related SGLang issue. A test belongs beside it: a checkpoint saved during a step that also ran another session's chunk resumes bit-exact.
3. **Verify against decode parity** ([#35150](https://github.com/sgl-project/sglang/issues/35150)). Width-one verify with every draft force-rejected must equal plain decode.
   - ling-engine's `--spec-check` is stronger: tokens and recurrent state.
   - One lesson to keep: align traces by *consumed token*, not by round. The issue's authors lost a day to the off-by-one between "decode round 1 consumes the prompt's last token" and "verify round 1 consumes the bonus token".
4. **Batch invariance under load** ([#36291](https://github.com/sgl-project/sglang/issues/36291)). 32 identical temperature-0 requests at once must produce one distinct completion.
   - On GB10 with our checkpoint and DFlash2, SGLang produced 20 distinct at concurrency 16 even with `--enable-deterministic-inference`.
   - A dense control on the same box gave 1.
   - For ling-engine at `--batch 2` this is a minute of GPU and the cleanest end-to-end statement of row invariance.
5. **State pollution** ("Chasing the Batch-1 Floor"). A greedy request re-run after interleaved temperature-sampled requests must match its first run.
6. **Prefix and chunking determinism** ([deterministic inference post](https://lmsys.org/blog/2025-09-22-sglang-deterministic/)). SGLang's least deterministic case was the prefix test: 18 distinct outputs in 50 trials at a 2,048-token prefix in normal mode.
   - The hybrid-specific version for us: the same history reached by decode (the recurrent update) and by a later prefill (the chunked scan) must give the same state.
   - So must the same prompt prefilled in 256-token and 2,048-token chunks.
   - ling-engine's `--prefix-check` and `--chunk-check` cover parts of this. The decode-against-re-prefill case is the one to confirm.

**Placement:** M4 stage 5's exactness checks. **Cost:** about a day, mostly host code.

### 2.7 Sessions and caching

- **Session-aware eviction** ([Unified Radix Cache post](https://lmsys.org/blog/2026-08-11-unified-radix-cache/); [#29173](https://github.com/sgl-project/sglang/pull/29173)).
  - Requests carry a `session_id`; entries of active sessions are evicted last, without being pinned; `/close_session` drops the reference.
  - On SWE-bench agent trajectories at batch 32–256 with TP8: TTFT −2.9% to −16.6%. The cache implementation changed at the same time, so this is not an isolated ablation.
  - ling-engine already keys sessions by `prompt_cache_key` with a 30 s protection, and §19.8 now ranks tool-waiting sessions first (Together survey).
  - What is new is the **explicit release**. The launcher knows when a `ling` session ends (and Night Shift when a task ends) and could say so through the `ling` extension (BACKLOG §19.15). That frees the slot at once instead of after LRU.
  - The SGLang Q4 roadmap goes further, with `PREFETCH`, `DEMOTE` and `PIN` hints for sessions, sub-agents and tool calls ([#43098](https://github.com/sgl-project/sglang/issues/43098)). **Small, exact; a proposal for §19.8.**
- **A checkpoint store that survives a restart.**
  - HiCache's L3 tier is a `get/exist/set` store keyed by page ([post](https://lmsys.org/blog/2025-09-10-sglang-hicache/)).
  - SGLang's weight-cache daemon refuses state whose "environment stamp" differs: model, revision, quantization hash, dtype, compute capability, library versions ([post](https://lmsys.org/blog/2026-08-21-sglang-fast-recovery/)).
  - Together these are the missing design for B43 (spill sessions to NVMe).
  - A 35K-token session is ~2.3 GB of BF16 KV plus ~75 MB of GDN state. That is under a second from NVMe (*estimate*, disk speed unmeasured) against ~16 s of re-prefill, after any engine restart or update.
  - Because byte-identity depends on the kernel build, the stamp must include the engine's build id.
  - HiCache's host tier itself gains nothing on unified memory, and its hybrid support crashes on SM120 ([#24121](https://github.com/sgl-project/sglang/issues/24121)). **Do not enable HiCache in production.**
- **Images** (plan item 5). SGLang replaces image placeholder tokens with a hash of the image before prefix matching ([NVILA post](https://lmsys.org/blog/2025-07-16-nvila/)) and keeps a vision-embedding cache keyed by that hash (4 GB by default; [EPD post](https://lmsys.org/blog/2026-01-12-epd/)).
  - BACKLOG §19.16 already requires prefix reuse keyed on the image content.
  - The embedding cache is the addition: a screenshot re-sent on later turns skips the vision tower.
  - The vision tower gains nothing from tensor parallelism ("ViT do NOT benefit from increased Tensor Parallelism"), so in two-node mode it stays on one node.
- **Token-in-token-out** ([post](https://lmsys.org/blog/2026-05-13-no-token-left-behind/)). Three things break "each turn's prompt extends the last prompt plus its response" on a Qwen-family template:
  - the model stops at `<|im_end|>` but the template writes `<|im_end|>\n`;
  - tool-call JSON is re-serialized with spaces;
  - earlier reasoning is cut at a user message.

  This is B19's miss audit. The post's fix list is the checklist to run it with, and `preserve_thinking` (on by default for Qwen3.8) should be confirmed on our path.

### 2.8 Small kernel items (evidence for items already planned)

| Source | What they measured | For us |
| --- | --- | --- |
| GLM-5.2 agentic post ([post](https://lmsys.org/blog/2026-07-13-glm52-optimization/)) | A warp-specialised, deeply pipelined small-M GEMM that "keeps many tiles' worth of data in flight": 1.05–1.13× over cuBLAS, "approximately 4%" end to end at batch 1 | Evidence for B16's direction; no new item |
| v0.5.19 release notes ([v0.5.19](https://github.com/sgl-project/sglang/releases/tag/v0.5.19); [#33722](https://github.com/sgl-project/sglang/pull/33722)) | A fused accept-and-state-advance for a linear-attention MTP verify cuts verify-and-commit time 45–63%, bit-identical | Our commit is ~7 ms at two streams. A fused accept-and-commit is a candidate beside the commit overlap. Different recurrence; *estimate* −1 to −3 ms, unmeasured |
| ReplaySSM ([Qwen3.8 day-0 post](https://lmsys.org/blog/2026-08-12-qwen3-8-day0-support/); [#42209](https://github.com/sgl-project/sglang/pull/42209)) | Record inputs during verify, replay the accepted prefix once | ling-engine already does this: its verify reads S₀ and writes nothing, and the commit replays. No gain |
| "Chasing the Batch-1 Floor" | Router gate and LM head moved from FP32 to BF16: "+10%", the largest single change after run-ahead | Not applicable (our head is NVFP4). It confirms that head bytes matter, which is item 19's premise |
| Vera Rubin post ([post](https://lmsys.org/blog/2026-10-09-vera-rubin/)) | Split-KV merge "issue all the loads first": 20% at batch 1, 128K; a hidden strided copy, 2.6 µs × 69 per step: 1.8% | The first is M3's pattern already. The second is an audit worth one nsys pass: implicit copies of non-contiguous slices on the step path |
| Advanced CUDA Graph post ([post](https://lmsys.org/blog/2026-08-17-advanced-cuda-graph/)) | Prefill graphs 1.70–1.93× faster than eager, "flat across a 32× range in prompt length, which is the signature of launch overhead" | ling-engine's prefill chunks run eagerly. A 256-token contended chunk is a fixed shape and could be captured. *Estimate* ≤ 1–3 ms per chunk pass, ≤ 0.4 ms on the pooled period. Not worth it before the scheduler rule of §2.1 |

### 2.9 The production server (SGLang v0.5.19)

Production uses the registry's `QWEN38_SGLANG_RECIPE`: the pinned v0.5.19 image, `RadixArk/Qwen3.8-27B-NVFP4` at `52d1adc`, DFlash2 at 12 drafts, `--chunked-prefill-size 8192`, `--num-continuous-decode-steps 2` and `--disable-flashinfer-autotune`.

**Correctness:**
- **The checkpoint-crossing bug, fixed in v0.5.20.**
  - Upgrading to v0.5.20 or v0.5.21 also brings the hybrid fixes listed in their notes. v0.5.21 deletes the old mamba radix cache classes ([#40313](https://github.com/sgl-project/sglang/pull/40313)), but only because they were unreachable: hybrid models already route to the unified cache ([#30468](https://github.com/sgl-project/sglang/pull/30468)), and v0.5.21 still validates `extra_buffer`. The launch line must still be re-validated on the new image.
  - Mightling's canary, chat-template patch and night-2 recipe all need a re-run on the new image.
  - Alternatively, back-port the one-function fix to a locally built image.
- **The open concurrency corruption** ([#36548](https://github.com/sgl-project/sglang/issues/36548), [#35150](https://github.com/sgl-project/sglang/issues/35150)).
  - Its reports are at concurrency 8 and on mixed loads. **Our exposure at one and two streams is unmeasured.**
  - The probe is the issue's own: a fixed greedy ordering prompt, 304 copies at concurrency 2 and 8, count wrong answers.
  - The mitigation measured upstream is the BF16-head export `RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead` (1/304 wrong).
    - Its cost (*estimate*): the head grows from ~0.72 GB to ~2.54 GB per read, +8.5 ms per pass at 215 GB/s, more if the drafter's selector also reads the BF16 head.
    - That is about **+8–16% per step**.
    - It would also be a different checkpoint from the one ling-engine is validated against.
  - **Recommendation:** run the probe at two streams first; take the BF16 head only if the probe fails.
- **Zombie requests after a client disconnect** ([#36333](https://github.com/sgl-project/sglang/issues/36333), open). A disconnected streaming client leaves a request decoding to `max_tokens`; two such requests "occupied both `--max-running-requests` slots" for 15 minutes.
  - A Codex interrupt (Esc) mid-stream is a disconnect.
  - Unreproduced here. The check is a minute with `curl` and `/metrics`.

**Configuration:**
- **`--num-continuous-decode-steps 2` does nothing.** No code in v0.5.19 reads it (checked: the scheduler has no reference), and [#37551](https://github.com/sgl-project/sglang/pull/37551) removes it as "dead since #2095".
  - Drop it, so that nobody tunes it.
  - The registry's description and INFERENCE spec should stop presenting it as a setting.
- **Chunk size and the stall.**
  - The cookbook recommends 2,048 on hybrid GDN models to keep inter-token latency smooth.
  - A GB10 report measured 4,096 as ~16% worse than 8,192 in aggregate at concurrency 8 ([#35860](https://github.com/sgl-project/sglang/issues/35860)).
  - `--prefill-decode-interval` bounds the stall without shrinking chunks.
  - At one or two streams, production's worst inter-token gap during a new session's 20–35K prefill is several seconds. That is the UX cost behind its better pooled number (§2.1).
  - **An ITL p99 measurement at two streams** with one long prefill is the honest comparison for the M4 gate, alongside the pooled period.
- **State pool.**
  - The cookbook sizes `--max-mamba-cache-size` as concurrency × S (S = 5 slots for `extra_buffer`) and warns that "folding D into the pin would over-provision the pool". With 8 running requests that is 40, against production's 96.
  - *Estimate:* ~4.4 GB of state slots at 78.4 MB each.
  - Memory only, not speed. Check the boot log's `max_running_requests` line before changing it.
  - `extra_buffer_lazy` saves one slot per request.
- **Determinism.**
  - `--enable-deterministic-inference` is a no-op on this configuration ([#36291](https://github.com/sgl-project/sglang/issues/36291)), and the team has "no plan" for deterministic speculative decoding ([#10278](https://github.com/sgl-project/sglang/issues/10278)).
  - Its cost where it works is 34.35% on average (Qwen3-8B, H200).
  - At concurrency above 1, production's run-to-run divergence is expected and is not a bug by itself. Corruption is judged by task-level answers, as in the probe above.

### 2.10 Two nodes: pipeline parallelism is the exact option (recorded for the end of the plan)

Per the owner's decision of 2026-10-13, two-node work waits (BACKLOG §19.21). One input for then, from the pipeline-parallel post ([post](https://lmsys.org/blog/2026-01-15-chunked-pipeline/)) and the PP2-with-speculation protocol in the DeepSeek-V4-Pro post ([post](https://lmsys.org/blog/2026-08-19-deepseek-v4-pro-engine-optimization-h20/); [#32281](https://github.com/sgl-project/sglang/pull/32281)):
- **Exactness.** Pipeline parallelism runs each layer's arithmetic unchanged on one node, so it is byte-identical to one machine. Tensor parallelism is not, and the TP note's exact forms cost up to ~1.4× in the worst case.
- **Traffic.** One hop per pass, carrying the activations: 13 rows × 5,120 × 2 B ≈ 133 KB. TP needs 128 all-reduces per step.
- **Speed (estimate):**
  - **One stream:** no gain; each node reads half the weights, but in turn.
  - **Two streams:** the pipeline fills itself. Stream A runs on node 2 while stream B runs on node 1. Each node reads its half of the weights once per stream per period, so the per-stream period approaches 2 × (~41 ms + half the non-GEMM work) ≈ 90–110 ms, against today's 130–152.
  - That is roughly **+25–50% per stream at two streams**, against today's 130–152 ms. It must be re-estimated after the savings block and the moonshot land, since both shorten the one-node step it is compared with. TP's 1.5–1.8× applies to one stream too.
- **The drafter** reads target layers 5, 19, 33, 47 and 61, so it sits on the last stage and receives the first stage's features. This is the coordination SGLang's protocol implements for DSpark under PP2.
- **For the TP note:** PP2 is the exact alternative for the two-stream case; TP stays the one that speeds up a single stream.

## 3. Benchmarks

### 3.1 How many tasks a night needs

**The noise is measured here.**
- The default arm ran three times on the same 44 tasks (nights 2–4, the Together survey §3.1). Any two runs disagree on 8–10 tasks: n3 against n4 resolve 29 and 31 with 26 in common, so 3 + 5 = 8.
- That is a noise discordance of about 18–23% between identical arms. FAILURES §6.5 found ~15% on 24 tasks.

**The power arithmetic** (*estimate*; paired design, two-sided α = 0.05, 80% power):

| True difference | Discordance 0.20 | Discordance 0.30 |
| --- | --- | --- |
| 5 points | 626 tasks | 940 tasks |
| 10 points | 155 tasks | 233 tasks |
| 15 points | 67 tasks | 102 tasks |

- Exact McNemar on 44–50 tasks needs about 10 against 2 (p = 0.039) or 12 against 3 (p = 0.035).
- **No 44–50-task night can separate a 5–10-point effect.** FAILURES already says so, and judges arms by their behaviour measures first; the table puts a number on how far off a resolve-count claim is.

**What the SGLang posts add:**
1. **Repeats.** Agentic runs in these posts use 8 attempts per task (the Vera Rubin post's SWE-bench Verified RL: "8 tasks × 8 attempts").
   - For us, 3 attempts per task per arm turns a 44-task night into 132 paired trials and per-task solve rates, analysed by a bootstrap over tasks.
   - That is 3× the agent time. It is the same budget as the Together survey's best-of-3, and the same runs serve both.
2. **Common random numbers, once ling-engine serves the nights.** ling-engine reseeds sampling per session from the request's seed and position (M4 draft §1), and its output is exact and row-invariant.
   - Two arms that differ only in the harness then share the random stream until their first real difference.
   - An A/A night would show zero discordance from sampling, leaving only the agent side's own nondeterminism: tool timing, timestamps.
   - Production cannot do this (§2.9).
   - That makes ling-engine as the benchmark server a statistical gain, not only a speed one. It needs a fixed per-task seed in the harness. *Estimate:* noise discordance falls from ~20% to the harness's own rate, unmeasured.
3. **Pick tasks that separate, and say so beforehand** ([Arena-Hard](https://lmsys.org/blog/2024-04-19-arena-hard/)).
   - Arena-Hard chose prompts by a score correlated with separability and reports separability itself: the fraction of model pairs whose 95% intervals do not overlap (87.4% against MT-Bench's 22.6%).
   - Our analogue: from nights 2–5, keep tasks resolved by some runs and not others, and drop those always or never solved, which carry no information.
   - The rule is fixed before any arm runs. The SGLang agent-development post calls changing the benchmark set after seeing results reward hacking ([post](https://lmsys.org/blog/2026-07-02-agent-assisted-sglang-development/)).
4. **An anchor arm and pooled nights.**
   - Every night runs production's configuration as one arm.
   - Nights are pooled in one logistic model with task fixed effects (Bradley–Terry, as Arena-Hard ranks against an anchor).
   - Covariates are added only if they are fixed before the arm acts. The style-control post's own caveat: a covariate the arm changes (tokens used) is a mediator, not a confounder ([post](https://lmsys.org/blog/2024-08-28-style-control/)).

### 3.2 Engine measurement discipline

From "Chasing the Batch-1 Floor", stated there as the rules every change had to pass:
- **Calibrate the profiler.** A profiled step measured 5.2 ms against 4.9 ms unprofiled, back-computed from TPOT × accept length.
  - ling-engine's `LING_PROFILE` synchronises per phase.
  - The step-savings block should state each gain from unprofiled runs.
- **Cold-weight microbenchmarks.** A kernel looped alone keeps its weights in L2: "Hot 7 µs, cold 11 µs: enough to reverse a ranking." B16's sweep should flush L2 between calls, or run inside the real step.
- **Decide on derived step time, never on peaks.** Peak throughput "carries roughly a ±5% phase band"; their A/B criterion was mean TPOT × mean accept length.
- **A correctness gate per change:**
  - a byte-exact 256-token greedy generation;
  - accept length within 0.05;
  - a greedy re-run after interleaved sampled requests (§2.6 item 5).

Two more:
- From the infer-forge post ([post](https://lmsys.org/blog/2026-08-28-infer-forge-loop-engineering/)): SGLang's benchmark once left streamed reasoning out of TTFT and token counts. Our replay's tok/s should be checked to count reasoning tokens.
- From the agent-development post: "Buckets close to the Roofline should allow no-go or fallback decisions". B16 is ~215 of ~235 GB/s, so state its stop rule before starting.

### 3.3 Workloads and reference numbers

- **Agentic trace sets for P1's offline replay:**
  - [`novita/agentic_code_dataset_22`](https://huggingface.co/datasets/novita/agentic_code_dataset_22): 22 sessions of a commercial coding agent, 17,487 turns, "39.3% output pattern repetition";
  - with suffix decoding on top of MTP, TPOT fell 21.9% on 8×H200 ([post](https://lmsys.org/blog/2026-01-21-novita-glm4/)).
  - Fetched once and kept locally, it is a second corpus beside our own sessions for P1's copy statistics. It comes from another harness and model, so it is a cross-check, not a target.
- **Trace replay.** `bench_serving --dataset-name mooncake --use-trace-timestamps` replays multi-turn traces with their timing ([docs](https://docs.sglang.io/cookbook/base/benchmarks/autoregressive_model_benchmark.md)). Reporting ITL percentiles there is how the prefill stall shows up (§2.9).
- **GB10 reference points:**
  - The closest published comparator is a 28B dense NVFP4 decoder with DFlash on a DGX Spark ([Muse Glimmer post](https://lmsys.org/blog/2026-08-10-meta-muse-glimmer/)): 12.1 tok/s per user without speculation, 36.4 with DFlash at batch 1, and 301 tok/s aggregate at batch 8.
    - Its 12.1 tok/s is ~83 ms per token, the same weight-read floor as ours.
    - Our ~48.5 tok/s single stream is ~33% above its DFlash figure, on a different model.
  - The cookbook's 80 GB10 configurations for our model were checked at concurrency 1 only: "No throughput or acceptance-length numbers were re-taken". The SGLang team publishes no tok/s for our model on Spark.
  - The community cell for our exact flags on a stock Spark ([#35860](https://github.com/sgl-project/sglang/issues/35860), DFlash2 at 8 drafts, unquantized drafter) reports single-stream 39.4 tok/s on code, 43.5 on reasoning and 20.2 on prose, a greedy median of 50.0, and 135–148 tok/s aggregate at concurrency 8. That is the cross-check for our ~48.5 single stream; the GitHub survey's community numbers predate DFlash2.
- **A second agentic trace set:** the NVFP4-KV post's AgentX benchmark replays traces of a commercial coding agent with 1,200 s windows and a cache flush per point ([post](https://lmsys.org/blog/2026-09-16-nvfp4-kv-cache/)). Its dataset and driver are third-party; worth one look for the M4 gate's traffic, not adopted.

## 4. Product features

### 4.1 Scoring without generating (`/v1/decisions`)

- **What it is** ([decision models post](https://lmsys.org/blog/2026-09-25-sglang-decision-models/); [cookbook §4](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B)).
  - A typed question (choice, yes/no, score) about an input returns a probability for every option from one prefill, with thinking forced off.
  - It also returns `label_mass`, the probability the model puts outside the offered answers, which serves as an abstain signal.
  - Qwen3.8-27B is the first model validated for it.
  - The post's own caution: "None of these values is a calibrated probability".
- **For Mightling:**
  - **The selector the Together survey's best-of-K needs.** Score K candidate patches against the task (yes/no "does this patch fix the issue"), or compare them pairwise.
  - **Night Shift triage.** "Did this run finish?", "is this diff risky?" in the morning report.
  - **In-harness gates.** "Is this tool output an error?"
- **Cost (estimate):**
  - with prefix reuse, a 30K-token context and four 1K-token candidates cost ~4K tokens of prefill, about 2 s;
  - without reuse, about 50 s.
- **Where it lives:**
  - ling-engine needs a prefill-only request path and a gather over the label tokens' logits, which the verify already computes. That is a small M5 API item, after structured output.
  - On production it needs a newer SGLang (nightly when the cookbook was written).
- **Exactness:** it reads the model's own next-token distribution.

### 4.2 The interactive session ahead of Night Shift

- SGLang's priority scheduling (`--enable-priority-scheduling`, a preemption threshold; [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)) is the multi-tenant form.
- For us it is one rule in ling-serve's two-request scheduler: when an interactive request and a Night Shift request compete, the interactive one's prefill and steps go first, and Night Shift's long prefills take the contended chunk size.
- The launcher already distinguishes the two callers, through `DREAMFERENCE_VLLM_HOST` and unattended flags, so it can mark them in the `ling` extension.
- **Exact:** scheduling only. **Cost:** small. It pairs with §2.7's session release.

### 4.3 Restart without losing sessions

§2.7's persistent checkpoint store with an environment stamp, plus the weight-cache daemon's lesson: a resident holder of the loaded weights makes an engine restart take a second.
- Mightling restarts the model server on updates and on `night pause/resume`.
- Today each restart costs the weight load plus a full re-prefill of every live session (~16 s per 35K session).
- B43 is the item. SGLang's design gives it the key and the refusal rule.

### 4.4 Tool calls constrained only where they are

XGrammar-2's structural tags (§2.5) make "the model may write prose, but a tool call must parse" a server guarantee.
- That is the remedy BACKLOG §19.13 names for apply_patch's freeform grammar.
- It is also the product guarantee for every client: Codex, Cline, Continue, OpenHands.

### 4.5 Reproducible runs

- A per-request `sampling_seed` (SGLang's seeded sampling, in the deterministic-inference post) lets a user or Night Shift re-run a session and get the same tokens.
- ling-engine has the mechanism already (per-session seeded RNG). The feature is to expose it end to end:
  - a seed in the launcher's request;
  - recorded in the rollout;
  - a "re-run with the same seed" in Night Shift's report.
- Production cannot offer it at concurrency above 1 (§2.9).

### 4.6 Smaller

- **`/v1/messages`.** SGLang also serves the other major agent API family's messages endpoint and documents several agent harnesses against our model.
  - Mightling's two-surfaces decision (BACKLOG §19.15) stands; this only confirms that a translation shim, if ever wanted, lives outside the engine.
  - One upstream bug to know: prior-turn reasoning spliced into content breaks the Qwen3.8 template ([#40959](https://github.com/sgl-project/sglang/issues/40959)).
- **GB10 host notes from the cookbook,** already handled by Mightling's host safety:
  - memory fraction 0.85 meets earlyoom's threshold, "15 of the 48 cells were killed";
  - `nvidia-smi` reports no memory, so gate on `MemAvailable`;
  - Docker GPU access is CDI-only.

## 5. Not worth pursuing here

| Work | Why not |
| --- | --- |
| NVFP4 KV cache ([post](https://lmsys.org/blog/2026-09-16-nvfp4-kv-cache/)) | Lossy, and measured on our model: SWE-bench Verified 389/500 (77.8%) with FP8 KV against 381/500 (76.2%) with NVFP4. At one or two streams with 16 attention layers it saves ~2.3 ms per stream at 35K against FP8 (*estimate*). HiCache with FP4 KV "can silently corrupt output" ([#29913](https://github.com/sgl-project/sglang/issues/29913)). B41 (FP8 KV, the safe way) stays the only KV item, under the exactness policy |
| PD-Multiplexing on green contexts ([post](https://lmsys.org/blog/2025-09-28-pdmux/); [arXiv 2504.14489](https://arxiv.org/html/2504.14489v3)) | Partitions SMs, "not memory bandwidth"; our decode is bandwidth-bound, and a concurrent prefill reads all the weights again. Measured at MaaS scale on H200/A100; speculation support is listed as future work, and in v0.5.19 `--enable-pdmux` requires chunked prefill off. The M4 draft's stage-6 second stream is the form to measure if the mixed batch is ever needed |
| PD disaggregation, two-batch overlap, large-scale EP ([post](https://lmsys.org/blog/2025-05-05-large-scale-ep/)) | Multi-node MoE serving. TBO helps "only at large batch sizes (e.g., 64 or 128)" ([Ant Group post](https://lmsys.org/blog/2025-09-26-sglang-ant-group/)). Disaggregation across our two nodes is the Together survey's §6 item 3, and §2.10's PP2 covers more |
| Deterministic mode ([post](https://lmsys.org/blog/2025-09-22-sglang-deterministic/)) | Costs 34.35% on average where it works and does nothing on our production configuration. ling-engine is row-invariant by construction, which is stronger. Only its tests are taken (§2.6) |
| Overlap scheduler, Spec V2, ReplaySSM, mini-SGLang | They hide Python scheduling or replace per-position state writes. ling-engine is C++ with 1.1 ms of in-step idle (B08 covers it) and already replays the accepted path |
| DSpark trimming, `--speculative-adaptive` | At 26 verify rows the extra rows are nearly free (the DSpark post itself: "the two arms tie" at batch 1). The DSpark drafter loses to DFlash2 on our target |
| Compressed-FSM jump-forward | Not exact (re-tokenization). Its exact form is §2.5's grammar-forced drafts |
| Lookahead decoding ([post](https://lmsys.org/blog/2023-11-21-lookahead-decoding/)) | Dominated by DFlash2. Its block attention mask for a second proposal branch in the same verify is what M2 and P1 already plan |
| Dynamic chunking ([post](https://lmsys.org/blog/2026-01-15-chunked-pipeline/)) | Pipeline-parallel only (`pp_size > 1`), and it models attention cost growing with the prefix; 48 of our 64 layers are linear |
| HiCache host tier, HiSparse, SSD Expert Pack, EPD encoder servers, CPU vision encoders | Unified memory makes "host" the same LPDDR5X; no sparse attention; dense resident weights ("adds an unnecessary data path" when experts fit); no encoder GPU; no AMX on GB10's Arm cores |
| `--enable-int8-mamba-checkpoint`, a BF16 SSM state against FP32 | Lossy state compression. The MiniMax M2 post's warning that "Linear attention is currently far more sensitive to numerical precision" ([post](https://lmsys.org/blog/2025-11-04-miminmax-m2/)) argues for keeping the state's precision |
| Breakable CUDA graphs for prefill | Fixes launch overhead in a Python engine. For ling-engine's eager 256-token chunks, ≤ 0.4 ms on the pooled period (§2.8) |
| BF16 LM head in production as a speed or quality move | Only as the measured mitigation for #36548, and only if the probe at two streams fails (§2.9) |

## 6. What SGLang does that ling-engine's plan still lacks

| Capability in SGLang | Status in ling-engine's plan | Verdict |
| --- | --- | --- |
| Mixed prefill and decode in one pass | Outside the order (MILESTONES §14) | Keep out. Its exact price is a dual-numerics GEMM; re-decide at stage 5 (§2.1) |
| Bounding the stall: `--prefill-decode-interval`, decode interleaving | The 256-token contended chunk and the owner's bound | Add the short-turn one-pass rule (§2.1) |
| A radix tree shared by many sessions, paged KV | Two resident sessions, a flat KV per slot, a prefix-keyed checkpoint store; paging is stage 6 | Enough for one user; nothing to add |
| Session release and pin hints | Protection, LRU, tool-wait-first (§19.8) | Add an explicit release and pin through the `ling` extension (§2.7) |
| Persistent prefix cache across restarts (HiCache L3, environment stamp) | B43, decision pending | Design input for B43 (§4.3) |
| Priority scheduling | None | A one-rule version for interactive over Night Shift (§4.2) |
| Grammar backends with speculation | Item 4 | Follow SGLang's verify-row masking and XGrammar-2's structural tags; add grammar-forced drafts (§2.5) |
| Scoring endpoints (`/v1/score`, `/v1/decisions`) | None | M5 API item after structured output (§4.1) |
| Thinking budget | §19.12 | Unchanged |
| Vision-embedding cache keyed by image hash | §19.16 requires the hash in the prefix key | Add the embedding cache to §19.16 |
| Deterministic mode | Row-invariant and exact by construction | Stronger already; take the tests only (§2.6) |
| Drafter training stack (SpecForge) | Self-speeding (product side), §19.20 | Use the Qwen3.8-27B recipe and regenerated data (§2.4) |
| Speculative pipeline parallelism across devices | Two-node TP, last | Add PP2 to the TP note as the exact option (§2.10) |

## 7. Proposed next steps (none started; each needs the owner's approval)

| # | Step | Cost | Decides |
| --- | --- | --- | --- |
| 1 | **Production: SGLang ≥ v0.5.20** (or the #37818 back-port), with the canary, template patch and launch line re-validated; drop `--num-continuous-decode-steps` (§2.9) | ~½ day on this machine, one model-server restart | Removes a known wrong-state bug from every prefix hit |
| 2 | **Production probe for #36548 at two and eight streams** (304 copies of a fixed greedy prompt), plus the 32-identical-requests distinct count (§2.9, §2.6) | ~1 GPU-hour on production, no agent runs | Whether the BF16-head mitigation (+8–16% step) is needed |
| 3 | **ling-engine: the six checkpoint and batch tests of §2.6** into stage 5's exactness checks | ~1 day, mostly host code; minutes of GPU | Guards the checkpoint store against the bugs SGLang shipped |
| 4 | **ling-engine: short turns in one pass** (whole prefill ≤ 512 new tokens in one pass, capturing the state at any checkpoint bound it crosses), measured on the stage-4 replay pair beside the savings block (§2.1) | ~1–2 days + one replay pair (~1 GPU-hour) | −1.5 to −2 ms pooled, exact, inside the ~440 ms bound |
| 5 | **B10's acceptance by context bucket** (1–4K, 8–16K, 16–35K) on the replay, before any drafter training (§2.4) | One replay with logging, ~1 GPU-hour | Whether a long-context DFlash2 fine-tune is the top drafter lever |
| 6 | **Structured output's design note** written from §2.5: verify-row masks built after the verify launch, XGrammar-2 structural tags for `<tool_call>`/`<function=`, grammar-forced drafts as a measured option | docs, ~½ day | Plan item 4's scope |
| 7 | **SELF_SPEEDING updated** with the SpecForge recipe, regenerated data in the served reasoning mode, long contexts in the mix and the overfit gate (§2.4) | docs | The drafter plan's recipe |
| 8 | **Benchmark design:** a pre-registered separable task list from nights 2–5, 3 attempts per task per arm, production as the anchor arm; per-task fixed seeds once ling-engine serves a night (§3.1) | docs + harness changes, ~1 day | Whether a night can detect 10 points |
| 9 | **Session release and pin hints; interactive-before-Night-Shift priority** in ling-serve and the launcher (§2.7, §4.2) | ~1–2 days, after stage 5 | Multi-session latency |
| 10 | **`/v1/decisions`-style scoring** as an M5 API item, first used as the best-of-K selector on the 13 disputed tasks of nights 2–4 (§4.1) | ~2 days in the engine; the selector test itself ~1 hour of model time | Night Shift attempts and triage |
| 11 | **PP2 written into the two-node note** as the exact alternative (§2.10) | docs | Item 8's choice, later |

Steps 1–2 are production-side and need the model server; step 3 needs minutes of second-puffin's GPU; steps 4–5 need about an hour each. The rest is writing.

## Sources

All read 2026-10-13. "Full" means the whole text was read; "part" means only the sections named.

**LMSYS blog posts** (all at `https://lmsys.org/blog/<slug>/`):
- **Read in full: speculation and drafters.**
  - [The next generation of speculative decoding: DFlash and Spec V2](https://lmsys.org/blog/2026-06-15-next-generation-speculative-decoding-dflash-v2/)
  - [Chasing the Batch-1 Floor](https://lmsys.org/blog/2026-08-21-ling3-flash-spec-decode-blackwell/)
  - [DSpark in SGLang](https://lmsys.org/blog/2026-07-06-dspark-sglang/)
  - [SpecForge v0.3.0](https://lmsys.org/blog/2026-08-04-specforge-v0-3/)
  - [SpecForge](https://lmsys.org/blog/2025-07-25-spec-forge/)
  - [SpecBundle & SpecForge v0.2](https://lmsys.org/blog/2025-12-23-spec-bundle-phase-1/)
  - [EAGLE-3 on Vertex](https://lmsys.org/blog/2025-12-01-eagle3-vertex/)
  - [MTP](https://lmsys.org/blog/2025-07-17-mtp/)
  - [Lookahead decoding](https://lmsys.org/blog/2023-11-21-lookahead-decoding/)
- **Read in full: serving, scheduling and caching.**
  - [RadixAttention and SGLang](https://lmsys.org/blog/2024-01-17-sglang/)
  - [SGLang v0.3](https://lmsys.org/blog/2024-09-04-sglang-v0-3/)
  - [SGLang v0.4](https://lmsys.org/blog/2024-12-04-sglang-v0-4/)
  - [Llama 3 serving](https://lmsys.org/blog/2024-07-25-sglang-llama3/)
  - [Deterministic inference](https://lmsys.org/blog/2025-09-22-sglang-deterministic/)
  - [PD-Multiplexing](https://lmsys.org/blog/2025-09-28-pdmux/)
  - [Unified Radix Cache](https://lmsys.org/blog/2026-08-11-unified-radix-cache/)
  - [Advanced CUDA Graph Techniques](https://lmsys.org/blog/2026-08-17-advanced-cuda-graph/)
  - [HiCache](https://lmsys.org/blog/2025-09-10-sglang-hicache/)
  - [Fast Engine Recovery](https://lmsys.org/blog/2026-08-21-sglang-fast-recovery/)
  - [R-Fork](https://lmsys.org/blog/2025-12-10-rfork/)
  - [Pipeline parallelism](https://lmsys.org/blog/2026-01-15-chunked-pipeline/)
  - [Mini-SGLang](https://lmsys.org/blog/2025-12-17-minisgl/)
- **Read in full: kernels and quantization.**
  - [Qwen3 latency on MI300X](https://lmsys.org/blog/2026-02-11-Qwen-latency/)
  - [GLM-5.2 NVFP4 agentic](https://lmsys.org/blog/2026-07-13-glm52-optimization/)
  - [ModelOpt integration](https://lmsys.org/blog/2025-12-02-modelopt-quantization/)
  - [Cleaner quantization stack](https://lmsys.org/blog/2026-07-31-cleaner-quantization-stack/)
  - [NVFP4 KV cache](https://lmsys.org/blog/2026-09-16-nvfp4-kv-cache/)
  - [HPC-Ops](https://lmsys.org/blog/2026-08-07-hpc-ops-sglang/)
  - [DeepSeek-V4-Pro on H20](https://lmsys.org/blog/2026-08-19-deepseek-v4-pro-engine-optimization-h20/)
  - [DeepSeek-R1 on H20](https://lmsys.org/blog/2025-09-26-sglang-ant-group/)
  - [GLM4-MoE at Novita](https://lmsys.org/blog/2026-01-21-novita-glm4/)
  - [GB200 part I](https://lmsys.org/blog/2025-06-16-gb200-part-1/)
  - [MiniMax-H3](https://lmsys.org/blog/2026-08-27-minimax-h3-h200/)
  - [MiniMax M2](https://lmsys.org/blog/2025-11-04-miminmax-m2/)
  - [HiSparse](https://lmsys.org/blog/2026-04-10-sglang-hisparse/)
  - [Vera Rubin](https://lmsys.org/blog/2026-10-09-vera-rubin/)
- **Read in full: structured output.**
  - [Compressed FSM](https://lmsys.org/blog/2024-02-05-compressed-fsm/)
- **Read in full: our model, local hardware and vision.**
  - [Qwen3.8 day-0](https://lmsys.org/blog/2026-08-12-qwen3-8-day0-support/)
  - [Qwen3.8-Flash-Next](https://lmsys.org/blog/2026-08-26-qwen-flash-next/)
  - [DGX Spark review](https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/) (and its benchmark sheet's LLM tab)
  - [Muse Glimmer](https://lmsys.org/blog/2026-08-10-meta-muse-glimmer/)
  - [SSD Expert Pack](https://lmsys.org/blog/2026-08-29-sglang-ssd-expert-pack/)
  - [EPD](https://lmsys.org/blog/2026-01-12-epd/)
  - [Heterogeneous EPD](https://lmsys.org/blog/2026-06-01-hetero-epd/)
  - [NVILA](https://lmsys.org/blog/2025-07-16-nvila/)
- **Read in full: agents, training and benchmarks.**
  - [No Token Left Behind](https://lmsys.org/blog/2026-05-13-no-token-left-behind/)
  - [OPD in Miles](https://lmsys.org/blog/2026-07-18-opd-support-in-miles/)
  - [Miles](https://lmsys.org/blog/2025-11-19-miles/)
  - [Miles v0.1](https://lmsys.org/blog/2026-08-18-miles-v0-1/)
  - [Agent-assisted SGLang development](https://lmsys.org/blog/2026-07-02-agent-assisted-sglang-development/)
  - [Infer-forge](https://lmsys.org/blog/2026-08-28-infer-forge-loop-engineering/)
  - [Arena-Hard](https://lmsys.org/blog/2024-04-19-arena-hard/)
  - [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/)
  - [Style control](https://lmsys.org/blog/2024-08-28-style-control/)
  - [GLM-4.5](https://lmsys.org/blog/2025-07-31-glm4-5/)
  - [LongCat-Flash](https://lmsys.org/blog/2025-09-01-sglang-longcat-flash/)
  - [Nemotron 3 Ultra](https://lmsys.org/blog/2026-06-04-nvidia-run-nemotron-3-ultra/)
  - [GTC 2026](https://lmsys.org/blog/2026-03-25-gtc2026/)
  - [InferenceMAX](https://lmsys.org/blog/2025-10-14-sa-inference-max/)
- **Read in part:**
  - [Large-scale EP on 96 H100](https://lmsys.org/blog/2025-05-05-large-scale-ep/) (parallelism, PD, TBO, evaluation and limitations)
  - [Decision models](https://lmsys.org/blog/2026-09-25-sglang-decision-models/) (method, benchmarks, Score API)
- **Catalogued, not read:** the other 50 posts (Arena, diffusion and video, TTS, TPU, AMD and Intel deployments, RL infrastructure, MoE day-0 posts).

**Papers:**
- SGLang [arXiv 2312.07104](https://arxiv.org/html/2312.07104) (§3–6, App. B: part)
- XGrammar [arXiv 2411.15100](https://arxiv.org/html/2411.15100) (full)
- XGrammar-2 [arXiv 2601.04426](https://arxiv.org/html/2601.04426) (full)
- DFlash [arXiv 2602.06036](https://arxiv.org/html/2602.06036v2) (§4–5: part)
- DSpark [arXiv 2607.05147](https://arxiv.org/abs/2607.05147) (main text; the appendix not reached)
- PD-Multiplexing [arXiv 2504.14489](https://arxiv.org/html/2504.14489v3) (part)
- SpecForge [arXiv 2603.18567](https://arxiv.org/abs/2603.18567) (abstract and results only)
- D-PACE [arXiv 2605.18810](https://arxiv.org/abs/2605.18810) (results only)

**SGLang docs and code:**
- [Qwen3.8-27B cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B) (full, from `docs/cookbook/autoregressive/Qwen/Qwen3.8-27B.mdx`)
- [Qwen3.8 cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8) (full)
- [speculative decoding](https://docs.sglang.io/docs/advanced_features/speculative_decoding) (part)
- [deterministic inference](https://docs.sglang.io/docs/advanced_features/deterministic_inference) (full)
- [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments) (the rows cited)
- [bench_serving](https://docs.sglang.io/cookbook/base/benchmarks/autoregressive_model_benchmark.md) (full)
- Code at [v0.5.19](https://github.com/sgl-project/sglang/tree/v0.5.19), the functions cited only:
  - `speculative/spec_info.py`, `arg_groups/speculative_hook.py`
  - `managers/scheduler.py`, `managers/schedule_batch.py`
  - `speculative/spec_utils.py`, `speculative/dflash_worker_v2.py`
  - `server_args.py`
- Release notes for [v0.5.19](https://github.com/sgl-project/sglang/releases/tag/v0.5.19), v0.5.20 and v0.5.21.

**SGLang issues and PRs (read with comments where relevant):**
- Speculation and hybrid state:
  - [#35371](https://github.com/sgl-project/sglang/pull/35371) (DFlash2)
  - [#23000](https://github.com/sgl-project/sglang/pull/23000) (Spec V2 + DFlash)
  - [#37817](https://github.com/sgl-project/sglang/issues/37817) / [#37818](https://github.com/sgl-project/sglang/pull/37818)
  - [#35150](https://github.com/sgl-project/sglang/issues/35150)
  - [#36548](https://github.com/sgl-project/sglang/issues/36548)
  - [#35860](https://github.com/sgl-project/sglang/issues/35860) (the GB10 cell and its comments)
  - [#36291](https://github.com/sgl-project/sglang/issues/36291)
  - [#42209](https://github.com/sgl-project/sglang/pull/42209)
  - [#33722](https://github.com/sgl-project/sglang/pull/33722)
- Scheduling and mixed chunk:
  - [#36933](https://github.com/sgl-project/sglang/pull/36933)
  - [#39342](https://github.com/sgl-project/sglang/issues/39342) / [#39526](https://github.com/sgl-project/sglang/pull/39526)
  - [#36065](https://github.com/sgl-project/sglang/pull/36065)
  - [#31395](https://github.com/sgl-project/sglang/pull/31395)
  - [#32775](https://github.com/sgl-project/sglang/pull/32775)
  - [#32549](https://github.com/sgl-project/sglang/issues/32549)
  - [#37551](https://github.com/sgl-project/sglang/pull/37551)
- Caching:
  - [#29173](https://github.com/sgl-project/sglang/pull/29173)
  - [#43098](https://github.com/sgl-project/sglang/issues/43098)
  - [#24121](https://github.com/sgl-project/sglang/issues/24121)
  - [#29913](https://github.com/sgl-project/sglang/issues/29913)
- Server behaviour and grammar:
  - [#36333](https://github.com/sgl-project/sglang/issues/36333)
  - [#10278](https://github.com/sgl-project/sglang/issues/10278)
  - [#40959](https://github.com/sgl-project/sglang/issues/40959)
  - [#42401](https://github.com/sgl-project/sglang/issues/42401)
  - [#41449](https://github.com/sgl-project/sglang/issues/41449)
  - [#32281](https://github.com/sgl-project/sglang/pull/32281)
- Roadmap [#42473](https://github.com/sgl-project/sglang/issues/42473) (full).

**RadixArk and models:**
- [radixark.ai](https://radixark.ai) (full; nothing on local or edge hardware)
- the [`RadixArk/Qwen3.8-27B-NVFP4` card](https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4) and its `hf_quant_config.json`: ModelOpt, `MIXED_PRECISION`, NVFP4 MLPs and LM head, FP8 attention and GDN projections, "max" calibration on 1,024 news samples of 512 tokens
- SpecForge's [Qwen3.8-27B DFlash2 recipe](https://github.com/sgl-project/SpecForge/blob/main/docs/recipes/qwen3.8-27b-dflash2-disaggregated.md) and [config](https://github.com/sgl-project/SpecForge/blob/main/configs/qwen3.8-27b-dflash2.json)
- [`novita/agentic_code_dataset_22`](https://huggingface.co/datasets/novita/agentic_code_dataset_22) (card only)

**Measured or computed here:**
- §2.1: the request lines of `~/m4s4/runs/s4c2.serve.log` and `s4c2b.serve.log` on second-puffin (M4 stage 4's two-stream replay, 146 requests each), parsed for new tokens, chunks, chunks beside a decode and compute rate.
- §3.1: the nights 2–4 default arms (the Together survey §3.1) and the standard paired power formula.
