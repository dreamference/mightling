# Moonshot 2: what Together AI's research offers Mightling (2026-10-13)

**Status:** research survey, nothing built. Reading only: no GPU, no model server, no agent run. One number is new and measured: the best-of-K ceiling in §3.1, computed from the default arms of nights 2 to 4 already on disk.
**Deepened the same afternoon (§8):** every Together blog post triaged (about 80, all 21 index pages), the papers behind the approved items read section by section, and their cited work followed where it touches our plan. Five earlier statements are corrected in place, each with a note pointing to §8. A second measurement from disk is added: resolved tasks by wall time across nights 1–4 (§8.5). **§8.1 lists what changes for work already in flight.**
**Question:** what in Together AI's published research could make ling-engine and the production server faster, improve or sharpen our benchmarks, or give Mightling new features, given one 27B hybrid model (Qwen3.8-27B NVFP4 with DFlash2 at 12 drafts) on one or two GB10s, air-gapped.
**Reads with:**
- ling-engine `reports/research-2026-10-09.md` (the arXiv survey of the year's inference papers) and `specs/drafts/MOONSHOT-repository-aware-decoding-2026-10-13.md` (the first moonshot);
- ling-engine `specs/DREAMFERENCE_LING_ENGINE_BACKLOG.md` §19 (items 8, 10, 19, 20, 21);
- [SELF_SPEEDING](../DREAMFERENCE_SELF_SPEEDING.md), [PDF_SEARCH](../DREAMFERENCE_PDF_SEARCH.md), [MIGHTLING_SWE_BENCH](../DREAMFERENCE_MIGHTLING_SWE_BENCH.md) and [agent-survey-2026-10-09.md](./agent-survey-2026-10-09.md).

Numbers from Together's papers and posts are the authors' own, on their hardware, read on the date above. Numbers measured on our machines say so. Everything else is an estimate and marked as one.

---

## Short answer

1. **No Together paper changes the engine plan; four sharpen items already in it.**
   - **ScaleSearch (MLSys 2026) gives a clean, exact speed item that is not in the plan:** re-quantize the DFlash2 drafter's NVFP4 scales. The drafter's own card puts its NVFP4 loss at 3.60 against 3.71 accepted per block of 8 in BF16. That bounds the gain at about **+3% tokens per step**, at no cost per step and with output unchanged. *(Corrected in §8.2: the same card calls that gap run-to-run noise (±0.2), so the item now starts by measuring the BF16 drafter against the served one at 12 drafts.)*
   - **Aurora/ATLAS and the "customized speculator" post set realistic expectations for self-speeding.** Their training loss (KL on accepted tokens plus KL on the top-10 of each rejected position) is the one to use. Online training added little over a fine-tuned static speculator: 3.08 against 2.99 accepted per step. Data volume is the binding cost: +10% needed ~20M tokens and +20% ~50M tokens. One SWE-bench night arm here produces 0.4–0.6M output tokens.
   - **DAS (MLSys 2026)** adds two measured rules to moonshot P1's design:
     - per-request suffix trees over a sliding window of the 16–32 most recent sessions, rather than one global tree *(corrected in §8.3: in DAS a larger window gives higher acceptance; 16–32 is a lookup-latency trade-off under RL policy drift, which a fixed served model does not have)*;
     - a speculation budget that grows with expected output length and is zero for short requests.
   - **ThunderAgent (ICML 2026)** gives a better eviction rule for M4's checkpoint store: evict sessions waiting on a tool call before sessions that are reasoning, weighted by how long the tool has run.
2. **The largest benchmark finding is ours, from data already on disk.**
   - **The ceiling:** across the three default arms of nights 2–4 (same 44 tasks), a single run resolves 29, 29 and 31. *Some* run resolves 34 of 44 for any two of them and **36 of 44 for all three** (measured, §3.1).
   - **What a selector captures:** DeepSWE's verifier-based selection captured about 58% of its comparable headroom (pass@1 42.2% → 59.0% against a pass@16 of 71.0%).
   - **Applied to us:** a best-of-3 with test-based selection would be worth an estimated **+3 to +4 tasks (+7–9 points)** on that list, at three times the agent time.
   - **Where it fits:** this is a Night Shift feature, where time is free, more than an interactive one.
3. **Three new benchmark sources are worth investigating, none adopting yet.**
   - DeepSWE v1.1 (Datacurve's multilingual long-horizon set, which Together reports on) — but Epoch AI flags 23 of its 113 tasks as flawed.
   - CoderForge-Preview's 51K Docker tasks from R2E-Gym, SWE-Smith and SWE-Rebench, as a pool for fresh validated lists.
   - A "collateral damage" column in our report: rollouts that break an already-passing test.
4. **Product features:**
   - **Plan-divide-conquer** reading for documents longer than the context: a planner writes the instructions, workers read chunks, a manager merges. It extends PDF search from "find the passage" to "answer over the whole book".
   - **A local deep-research skill** (plan → search → reflect → write with citations) on `ling-search`.
   - **A per-session "cost saved" line:** tokens processed locally against a cloud price list shipped with the release. No network, no telemetry.
   - **Mightling's model inside other harnesses**, as Together Link does. OpenAI-compatible harnesses already work. One that needs another API family would need a translation shim outside the engine, because the engine's decision is two API surfaces and no third.
5. **Two-node ideas are recorded for the end of the plan, per the owner's decision of 2026-10-13.**
   - **SSD/Saguaro (ICLR 2026):** the drafter runs on the second device and speculates on verification outcomes. It is the one two-node speedup that is **exact by construction**. Estimated +5–15%, far below tensor parallelism's 1.4–1.8×.
   - **Online drafter training on the second node:** Aurora's own setup.
   - **Prefill on one node and decode on the other:** cache-aware disaggregation. This removes the two-stream prefill stalls M4 measured.
6. **Not worth building, and why:** §5.
   - The lossy ones: activation sparsity (TEAL), lossy speculation (AutoJudge), 2-bit or FP4 KV (Kitty, ScaleSearchAttention).
   - Kernel work for B200 only: FlashAttention-4, ThunderKittens on Blackwell.
   - Architecture changes to a model we don't train: Ladder Residual, Mamba-3, LoLCATs.

---

## 1. Method and scope

- **What was catalogued:** Together's research page (29 papers and 8 projects); the ICML 2026 (9 papers) and MLSys 2026 (7 papers) pages; and all 21 pages of the research blog, about 80 posts from 2022 to September 2026.
- **What was read in full text (arXiv HTML or the blog post):**
  - Speculation and the speculator line: Aurora, DAS, Speculative Speculative Decoding, ATLAS, the customized-speculator post, MagicDec, the speculative part of Mamba in the Llama.
  - Quantization and sparsity: ScaleSearch, Kitty (abstract only), TEAL, AutoJudge.
  - Serving: ThunderAgent, cache-aware PD disaggregation, the ThunderKittens Blackwell post.
  - Agents and benchmarks: DeepSWE (the agent), the DeepSWE benchmark post, CoderForge-Preview, V1 (abstract), plan-divide-conquer, Open Deep Research, Together Link, the ColQwen2 document-RAG post, CDLM, ReasonIF, OSWorld-Human (search summary), Ladder Residual (search summary).
- **Skipped as outside the question:** Together's lessons on running agents for inference engineering, and the training-only papers (RedPajama, CocktailSGD, Untied Ulysses, the fine-tuning deep dives).
- **Where the earlier survey already covered a work:** FlashAttention-4's tricks are item 7 of ling-engine's 2026-10-09 survey; SuffixDecoding is in the first moonshot. This report adds only what is new.

## 2. Engine and production speed

The engine's measured state is ling-engine M4 stage 4 (merged 2026-10-13):
- **One stream:** ~102–104 ms per step, about 5 tokens per step. Of that, ~82 ms is the 17.6 GB weight read at ~215 GB/s.
- **Two streams:** 33.4 tok/s per stream at 151.9 ms against production's 33.9 at 145. The miss is the 13–14% of steps that wait behind the other stream's prefill chunk.

### 2.1 Re-quantize the drafter's NVFP4 scales (ScaleSearch): new, exact, small

- **What they did** (Gupta, Prairie, … Dao, Fu, De Sa; all list Together; MLSys 2026, [arXiv 2605.12464](https://arxiv.org/html/2605.12464)):
  - NVFP4 normally sets each 16-value block's FP8 scale from the block's maximum. ScaleSearch tries the neighbouring representable FP8 scales (offsets −2 to +6) and keeps the one with the lowest block error.
  - Quantization error falls about 25–27%. The format is unchanged: the result is ordinary NVFP4 with different scale bytes, so every NVFP4 kernel reads it. The paper says it "seamlessly integrates into existing PTQ and attention workflows". It publishes no code, and its only PTQ baseline is ModelOpt's NVFP4 path (no RTN, GPTQ or AWQ comparison).
  - Its cost figure is for *online* FP32→NVFP4 quantization of a 2048×2048 matrix: 1.27× at offsets [−1, 1] and 1.74× at [−2, 6]. There is no offline wall-clock figure; for a 2B drafter either is negligible.
  - *(Corrected in §8.2; the estimate and steps below are superseded there.)*
- **Why it matters here:**
  - The drafter `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` is an NVFP4 copy of a BF16 drafter. Its card reports 3.60 accepted per block of 8 against 3.71 in BF16 ([SELF_SPEEDING](../DREAMFERENCE_SELF_SPEEDING.md) §1).
  - A better-quantized drafter can recover part of that 3%. The drafter only proposes and the target verifies every token, so **the output is unchanged by construction**.
  - The drafter's bytes and kernels stay the same, so the step costs nothing more.
- **Estimate:** +0–3% tokens per step, on production SGLang and on the engine alike, since it is the same file.
- **Cost:** about half a day on the product side.
  1. Download the BF16 drafter.
  2. Quantize it with the drafter's calibration recipe plus the scale search.
  3. Measure acceptance on the replay set at 12 drafts (the method of the 2026-10-09 depth measurement: two runs each, one session).
  4. Pin it in the model matrix if it wins.
- **Where it slots:** a sibling of BACKLOG §19.20's tuned drafter. A tuned drafter must be quantized anyway, and this is the quantization step that drafter will need.

### 2.2 Re-quantize the target (ScaleSearch): quality, not speed; a model candidate

- **What it would do:** the same method on the target, Qwen3.8-27B. It changes outputs, so it is a **model candidate** (like night 2's `minima` candidate), not a speed item.
- **Their results (Qwen3-8B, ModelOpt NVFP4 path, Table 1, mean of 5 runs):** positive on three of four benchmarks.

  | | NVFP4 | ScaleSearch | BF16 |
  |---|---|---|---|
  | MATH-500 | 73.1 | 88.1 | 72.8 |
  | GPQA | 42.4 | 49.9 | 51.3 |
  | MMLU | 77.7 | 79.4 | 79.7 |
  | AIME-120 | 63.7 | 63.0 | 71.0 |

  - AIME did not recover.
  - The MATH-500 result above BF16 is surprising and should be treated as noise until reproduced.
- **What it needs:** the BF16 Qwen3.8-27B (~54 GB) and a ModelOpt quantization run on the GB10.
- **Two risks:**
  - The DFlash2 drafter reads the target's hidden states, so acceptance must be re-measured.
  - Our checkpoint (`RadixArk/Qwen3.8-27B-NVFP4`) may use a recipe we cannot reproduce exactly.
- **Decided by:** a SWE-bench night with the candidate as one arm.
- **Estimate:** unknown sign on SWE-bench. Worth one night only if the owner wants a quality lever before 1.7.

### 2.3 Self-speeding: what Aurora, ATLAS and the customized speculators measured

**What the papers report:**
- **Aurora** ([arXiv 2602.06932](https://arxiv.org/html/2602.06932), ICML 2026; shipped as ATLAS, NeurIPS 2025):
  - **Setup:** an EAGLE-3 speculator trained online on an SGLang server's traces.
  - **Loss:** KL on accepted tokens plus a KL term on rejected positions (top-10 filter, λ = 1). "The discarded tokens help most" on code (Figure 7).
  - **Cadence:** updates every 80–100 requests.
  - **Results:**
    - Day 0 from scratch, it reaches 2.46 accepted per step "within approximately 10,000 requests".
    - On a mixed stream it reaches 3.08 against 2.63 for a static speculator and **2.99 for a pretrained-then-fine-tuned one**.
    - Speedup falls with batch size: on Qwen3-Coder-Next it is 1.51× at batch 1, 1.23× at 8, 1.09× at 16 and 0.94× at 32.
- **Customized speculators** ([post](https://www.together.ai/blog/customized-speculative-decoding)): fine-tuning a base speculator on a customer's own traffic gave >1.10× with 20M tokens (about 10K prompt–response pairs of 2,048 tokens) and >1.20× with 50M.

**What this means for [SELF_SPEEDING](../DREAMFERENCE_SELF_SPEEDING.md):**
- **Nightly is enough.** The online-versus-fine-tuned gap (3.08 against 2.99, +3%) is small next to the static-versus-adapted gap (+17%). The spec's design of training at night with the server stopped keeps most of the value, and on one GB10 online training would compete with serving.
- **Use their loss.** Add the rejected-position term to the SpecForge DFlash2 recipe (§5 of the spec). Verify already produces the signal, as the 2026-10-09 survey noted for Draft-OPD.
- **Volume is the cost, and the spec should say so.**
  - **What we produce:** night 4's arms emitted 377,812 and 628,072 output tokens for 44 tasks (measured, `n4-*` reports). An interactive user produces less.
  - **What reaching 20M needs:** reaching Together's 20M-token point needs the spec's `drafter generate` step (the target re-answering collected prompts). *(Unit corrected in §8.6: Together's 20M is about 10K pairs at 2,048 sequence length, so it counts prompt and response tokens, not output alone.)*
  - **What that costs here:** at ~50 tok/s single stream that is ~4.3M tokens a day, and ~5.8M at two streams (2 × ~33.5 tok/s, production's and stage 4's measured rate). So 20M tokens is **3–5 dedicated nights** (estimate).
  - **The gate stays:** Phase 0 ("is there anything to adapt to") remains the first gate.
- **Expect less than Together's figures.** Their gains are against EAGLE-3 speculators at 2.5–3 accepted per step. DFlash2 already accepts ~5 at 12 drafts on our replay set, so headroom per step is smaller. The OVERVIEW stretch target (≥ 8 accepted) stays a stretch.

### 2.4 Moonshot P1: DAS's two rules (design input)

- **What DAS is** ([arXiv 2511.13841](https://arxiv.org/html/2511.13841), MLSys 2026; Together, Stanford, UCSD): a model-free, lossless proposer for RL rollouts.
- **What it found:**
  - **Per-request suffix trees** over a **sliding window of recent rollouts** beat one global tree. A window of 16 or 32 beat "all" (Figure 7). Updates take under a millisecond per 100 tokens (Ukkonen). *(Corrected in §8.3: Figure 7 shows larger windows, "all" included, give **higher** acceptance; 16 or 32 won only on lookup latency and staleness under RL policy drift.)*
  - **A length-aware budget** gives long requests aggressive draft budgets, medium ones less, and short ones none. It is "up to 15%" better than an unlimited budget (Figure 12). Code RL rollouts were ~25% faster end to end on 8×H100.
- **What we take** (design input for the first moonshot's §3.1 proposer interface and P1, no new item):
  - the user-history source as a window of the last 16–32 sessions in the same repository, not the whole history *(superseded by §8.3: partition by repository, size the window by lookup cost and memory only)*;
  - a proposal-length rule that is off for short requests (titles, autocomplete, one-line answers) and longest inside edit-shaped tool calls.

  Both are exact and both are cheap.

### 2.5 M4 sessions: program-aware eviction (ThunderAgent)

- **What ThunderAgent does** ([arXiv 2602.13692](https://arxiv.org/html/2602.13692), ICML 2026 spotlight): it schedules whole agent programs rather than requests.
  - When memory runs short, it pauses programs that are **acting** (waiting on a tool) before those that are **reasoning**, with a time decay on how long the tool has run, then the shortest context first.
  - It restores reasoning programs first.
  - It does not predict tool durations; it argues that TTL predictions are often wrong.
- **What they measured:** 1.48–3.58× over vLLM at up to 96 parallel programs (8×H100). Request-level engines inflate latency up to 7.14× by evicting a program's KV during its tool call.
- **What applies here:**
  - **Where:** ling-engine's checkpoint store (BACKLOG §19.8; the stage 3 rule just confirmed: least recently used gives way, a prefill never evicts its own prefix).
  - **What we can tell:** the engine knows when a response ended in a function call, and the next request with the same `prompt_cache_key` ends the tool phase. A session waiting on a long tool (a test suite, a build) can give way before one whose answer is being read.
  - **The refinement:** order eviction by (acting, time in tool) before recency. This sits next to the "own prefix" rule, which stays.
- **Estimate:** small at the one or two streams we run. It matters when the owner runs more sessions than stay resident, for example Night Shift plus an interactive session plus subagents. It is cheap: one field per session and a sort key. Exact: eviction changes only where a prefill resumes.

### 2.6 Pointers for items already planned

- **M2 tree speculation:** Sequoia ([post](https://www.together.ai/blog/sequoia)) and SpecExec ([post](https://www.together.ai/blog/specexec)) choose tree shape by dynamic programming over positional acceptance and size the tree to the hardware's verify cost.
  - On our box extra verify rows are nearly free up to about 64 (first moonshot §1), so the hardware-aware size is "large".
  - The DFlash-specific tree work already picked (CAST, Trees from Marginals) supersedes their shapes.
- **BACKLOG §19.10, speculation at long context:** MagicDec ([post](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference)) shows that once KV reads dominate, verifying more tokens per KV read pays even at batch 32: 1.84–2.00× at 32K–100K on 8×A100.
  - Our 16 attention layers make KV a smaller share than in a dense model, so the effect will be weaker.
  - Item 10's measurement at 128K–200K is where it would show.
- **Hybrid verify:** "Mamba in the Llama" verifies drafts through a recurrent layer by keeping one state and advancing it lazily by the accepted count. That is what the engine's verify-then-commit already does (M1). Nothing new.

## 3. Benchmarks

### 3.1 Best-of-K: the ceiling is measured, the selector is the question

**Measured here.** The default arm ran on the same 44-task list in nights 2, 3 and 4. Each night was pinned to its own harness commit, so the three runs differ slightly in harness as well as by chance.

| | n2-default | n3-default | n4-default | Any of the two | Any of the three | All three |
|---|---|---|---|---|---|---|
| Resolved of 44 | 29 | 29 | 31 | 34 (n3∪n4; also n2∪n3) | **36** | 23 |

- **Headroom:** a perfect selector over three runs would resolve 36 of 44 (81.8%), against a single run's mean of 29.7 (67.4%), about **+14 points**.
- **Spread:** only 23 tasks are solved every time, so about a quarter of a run's "resolved" set (6–8 of 29–31) is luck of the draw. The nights' p-values (0.45–0.73) already said so.

**What Together's work says about selection:**
- **DeepSWE-Preview** ([post](https://www.together.ai/blog/deepswe); Qwen3-32B, RL only): Pass@1 42.2%, Pass@16 71.0%. With "hybrid test-time scaling" it reaches 59.0% at K = 16 and 57.9% at K = 8.
  - The selector combines an execution-free verifier (a model trained to judge patches) with an execution-based one (a model writes tests and edge cases; the candidate passing most wins). Each alone gave "10+ points".
  - The selector captured about 58% of the Pass@16 − Pass@1 gap, and most of the gain was already there at K = 8. (The SWE-bench leaderboard comparison in [SWE_BENCH_COMPARISON](../DREAMFERENCE_MIGHTLING_SWE_BENCH_COMPARISON.md) already lists the K = 16 entry.)
- **V1** ([arXiv 2603.04304](https://arxiv.org/abs/2603.04304), ICML 2026): picking by **pairwise** comparison beats scoring each candidate alone by up to 10% Pass@1. The abstract does not separate SWE-bench numbers. Pointwise judges give nearly everything the top score.

**Estimate for us:** best-of-3 with test-based selection plus a pairwise judge, all from the same local model, captures half or more of the +6.3-task ceiling: **+3 to +4 tasks (+7–9 points)**. The cost is 3× agent time.
- **On the benchmark:** one arm would take about 20 hours on this list at today's pace (night 4 ran two arms in about 13.5 hours), so it does not fit a night. It fits as a Night Shift feature (§4.4), or a weekend run.
- **The selector's design, from the cited work:** §8.4 (regression tests first, patches without the agent's reasoning, a pairwise judge with V1's parameters).
- **A cheaper first test:** the selector alone. Run it on the patches the three default runs already produced (no agent time) and score how often it picks a resolving patch among the 13 tasks where the runs disagree. That is about an hour of model time and says whether the selector works before anything else is built.

### 3.2 A "collateral damage" column

- **What it is:** the DeepSWE benchmark post ([GLM-5.3 vs Flash](https://www.together.ai/blog/glm-5-3-vs-glm-5-3-flash-on-deepswe-cost-coding-and-routing)) reports the share of rollouts that **break an already-passing test**: 4.4% and 6.9% for its two models.
- **Why it's useful:** our "resolved" already requires PASS_TO_PASS, but an unresolved task can fail in two ways: it didn't fix the issue (FAIL_TO_PASS) or it broke something (PASS_TO_PASS). These are different failures with different fixes. `tests-v2`'s discipline rules aim at the second.
- **Cost:** a report column split from the grading logs already on disk, a few hours. It sharpens every night's comparison at no GPU cost.

### 3.3 New task sources (investigate, don't adopt)

- **DeepSWE v1.1** (Datacurve, [repo](https://github.com/datacurve-ai/deep-swe), [paper](https://arxiv.org/html/2607.07946v1)).
  - **What it is:** 113 long-horizon tasks in TypeScript, Go, Python, JavaScript and Rust, graded by hidden tests in a pristine container from the agent's commits. The dataset on Hugging Face is gated.
  - **Its flaws:** Epoch AI's review [flags at least 23 tasks (20%) as flawed](https://epoch.ai/benchmarks/deepswe/review), mostly hidden tests colliding with the agent's own tests.
  - **Why it's a candidate:** it fits the owner's 2026-10-09 rule ("keep SWE-bench Verified; investigate fresh and multilingual sets only").
  - **Open questions:** arm64 images; the time per task on a 27B at ~45 tok/s (long-horizon tasks may exceed our 45-minute ceiling); and whether the flawed list is fixed.
  - **First step:** read the task format and Epoch's list, no runs.
- **CoderForge-Preview** ([post](https://www.together.ai/blog/coderforge-preview)).
  - **What it is:** 258K trajectories by a 480B coder on 51,201 tasks in 1,655 repositories, from R2E-Gym, SWE-Smith and SWE-Rebench. Tasks overlapping SWE-bench Verified are excluded, and only permissively licensed repositories are kept.
  - **Two uses for us:**
    - **A pool for fresh validated lists.** SWE-Rebench's tasks are recent, which addresses the contamination noted in [MIGHTLING_SWE_BENCH](../DREAMFERENCE_MIGHTLING_SWE_BENCH.md).
    - **Prompts for the drafter's on-policy generation (§2.3).** The trajectories themselves were written by another model, so they are not drafter training targets.
  - **Open question:** whether the Docker environments are released, and for arm64. The post does not say.

### 3.4 Smaller findings

- **A cascade inside one model.** The DeepSWE benchmark post's cascade runs the cheap model first and escalates when a verifier rejects. It reached 80.9% at $1.70 per task, against 69.0% at $3.99 for the large model alone.
  - We serve one model, but its reasoning effort is a dial: low effort first, high effort when the tests fail.
  - It is an arm for a later night, tied to BACKLOG §19.12's thinking budget.
- **ReasonIF** ([post](https://www.together.ai/blog/large-reasoning-models-fail-to-follow-instructions-during-reasoning-a-benchmark-study)): open reasoning models follow formatting instructions *inside their reasoning* far less than in their answers. The strongest drop more than 50% between answer and reasoning, and the best is under 25% in reasoning.
  - This supports the agent survey's conclusion: enforce through the harness and hooks, don't ask the model to think in a particular way.
- **OSWorld-Human** ([arXiv 2506.16042](https://arxiv.org/html/2506.16042v1), MLSys 2026 oral): the best computer-use agents take 1.4–2.7× more steps than a human (2.7–4.3× in the MLSys version), and later steps cost about 3× early ones as context grows.
  - Our reports already count commands and wall time.
  - The late-step cost is the compaction work's subject (turn-end compaction bench, 2026-10-13).

## 4. Product features

### 4.1 Reading whole long documents: plan, divide, conquer

- **What they found** ([post](https://www.together.ai/blog/plan-divide-conquer); ICLR 2026, [arXiv 2506.16411](https://arxiv.org/abs/2506.16411)): a planner rewrites the task into instructions for workers, workers each read one chunk, and a manager merges.
  - With planning, 70B-class open models beat a frontier model reading the whole text at once. *(Corrected in §8.7: the paper's own figures are at 128K. For example, Llama-70B split into 16K chunks scores 0.57 on its Math task, where GPT-4o reading the whole text scores 0.33.)*
  - **The noise model:** model confusion grows superlinearly with length (splitting helps), cross-chunk dependence is lost by splitting (splitting hurts), and the merge adds its own error.
  - The best chunk size can be found from about 5 samples.
  - The planner's job is to ask workers for what the merge needs. Their example: for the second smallest number overall, each worker returns its two smallest.
- **For Mightling:** [PDF_SEARCH](../DREAMFERENCE_PDF_SEARCH.md) finds passages, and `docs_read` reads pages. Neither answers a question over a 400-page book, which no 94K-token session can hold.
  - **The feature:** a `docs_ask <document> "<question>"` path, as a skill or a `ling-docs` subcommand:
    1. Plan (one model call).
    2. Map over chunks of `docs_read` output (one call per chunk, at the one or two streams the node runs).
    3. Reduce (one call).
  - **Same reader, chunker and isolation;** nothing leaves the machine.
- **Estimate:** a few days on the product side. Its value is a capability we don't have, not speed: a 300-page PDF at ~8K-token chunks is ~40 map calls, minutes on a GB10.
- **Not for:** questions that need a clue in one chapter joined to a distant one. The paper's "high task noise" case.

### 4.2 Local deep research

- **What it is:** Together's Open Deep Research ([post](https://www.together.ai/blog/open-deep-research), [repo](https://github.com/togethercomputer/open_deep_research)) is a loop of planner → search → reflection (find gaps, search again) → source ranking → summarizer → writer, with a cited report as output.
  - They used several models; one local model can play every role.
  - It beat single-step retrieval on FRAMES, SimpleQA and HotPotQA (50 samples each).
- **For Mightling:** a `research` skill over `ling-search`/`ling-fetch` (SearXNG) that writes a cited Markdown report into the workspace. Ask threads already run `ling`, so the web chat gets it too.
  - The repository's license must be checked before reusing anything; the structure alone is enough to rebuild it.
- **Estimate:** a skill and a prompt, about two days. Most of the quality is in the reflection step and the source cap.

### 4.3 "Cost saved" per session

- **What Together does:** Together Link ([post](https://www.together.ai/blog/together-link-frontier-quality-open-models-in-the-harness-you-already-use)) shows each session's spend beside what the same session would have cost on a frontier closed model.
- **Mightling's version:** the inverse, with no network. Tokens in and out, from the rollout we already write, priced against a small table of public list prices shipped and dated with each release.
  - Shown at the end of a `ling` session and in the desktop app.
  - It is computed locally and never sent anywhere, so it is consistent with "no telemetry ever" (2026-10-07).
- **Estimate:** under a day. It is a marketing line as much as a feature, and the price table needs updating each release.

### 4.4 Several attempts at night (Night Shift)

- **What it is:** the product form of §3.1. A Night Shift task can ask for `attempts = 3`, each in its own worktree as today.
  - The morning report shows the chosen attempt, why it was chosen (tests written by the model, then a pairwise judge), and the alternatives' diffs.
  - Time at night is free, and the selector is the same one §3.1 tests first on old patches.
- **Estimate:** +7–9 points on SWE-bench-like tasks if the selector works (§3.1), at 3× the night's agent time.

### 4.5 Mightling's model in other harnesses

- **What Together does:** Together Link connects existing agent harnesses (it names five, including Codex and OpenCode) to its models with a one-line installer.
  - It routes **once per session**, "so prompt caching keeps working".
- **For Mightling:**
  - **What already works:** Cline, Continue and OpenHands use the node's OpenAI-compatible endpoint today.
  - **Harnesses that speak the same API:** they need only a documented configuration (unverified per harness).
  - **A harness that needs another API family:** this would need a translation shim. It cannot go in the engine, whose decision is two API surfaces and no third (BACKLOG §19.15). It could live beside the launcher as its own small process. That's the owner's call.
  - **Fleet routing:** route a session to one node for its whole life, never request by request, so the prefix cache keeps working.

### 4.6 Not now

- **Visual page retrieval for scanned PDFs** (ColQwen2, [post](https://www.together.ai/blog/multimodal-document-rag-with-llama-3-2-vision-and-colqwen2)): it indexes pages as images with thousands of vectors per page and needs a vision model.
  - PDF_SEARCH §3.6 already plans OCR, and the engine's vision encoder comes after M4.
  - Revisit when both exist.
- **Data-science agent** (DSGym, the open data-scientist agent): outside a coding pair programmer's scope.

## 5. Not worth pursuing here

| Work | Why not |
|---|---|
| TEAL (training-free activation sparsity, up to 1.8× at batch 1 and 50% sparsity); Deja Vu | It skips weight columns whose input activation is zero. Our verify runs 13 rows at once: at 50% sparsity per row, the union of non-zero inputs across 13 rows is ~100% unless rows are strongly correlated. NVFP4's 16-value blocks make skipping coarser still. It is also lossy. TEAL's own post says it "does not scale well to higher batch sizes". |
| AutoJudge (accept "unimportant" mismatches) | Lossy by design: −2.5 to −3% pass@1 on LiveCodeBench in their numbers. The engine's rule is exact output. |
| Kitty (2-bit KV), ScaleSearchAttention (NVFP4 KV) | Lossy. KV memory is not what limits us at one or two streams on 128 GB, and 16 attention layers keep KV reads a small share of the step. |
| Speculative Speculative Decoding on one GB10 | Its gain comes from drafting on a *separate device* while the target verifies (Llama-70B on 4×H100, drafter on its own H100). On one device the overlap disappears. Kept for two nodes (§6). |
| FlashAttention-4; ThunderKittens on Blackwell; ParallelKittens | Built on B200 features (tensor memory, CTA pairs, NVLink). FA4's portable ideas (conditional rescale, polynomial exp) are already item 7 of the 2026-10-09 survey. |
| Ladder Residual (+29% at TP 8) | Changes the architecture and needs retraining (3B tokens to convert part of an 8B). We serve a fixed model with exact output. |
| Mamba-3, Parcae, LoLCATs, Mamba in the Llama (distillation), BASED, Hyena | Architecture research for models we don't train. |
| CDLM (consistency diffusion LMs) | It speeds up diffusion LMs as *targets*. Its block-causal training might inform a future drafter recipe, but nothing in it is usable now. |
| Opportunistic Expert Activation | Mixture-of-experts only; our model is dense. |
| Mixture-of-Agents, MoAA, Minions | Several models, or a cloud half: we serve one model, air-gapped. |
| TTT-Discover | Test-time RL at about $500 per problem on a 120B; not a fit for a local 27B. |
| Medusa, Flash-Decoding, FlexGen, BitDelta | Superseded here (DFlash2, the engine's split-KV attention) or about multi-tenant serving. |

## 6. Two nodes: recorded for the end of the plan

Per the owner's decision of 2026-10-13 (ling-engine BACKLOG §19.21), two-node work waits until every one-node improvement is settled. The TP note's byte-identical choice stays open. These are alternatives or additions to TP to weigh then, not proposals now:

1. **The drafter on the second node, speculating ahead (SSD/Saguaro**, Kumar, Dao, May, [arXiv 2603.03251](https://arxiv.org/html/2603.03251), ICLR 2026).
   - **How it works:** while node A verifies, node B guesses the likely verification outcomes and pre-drafts for each. When the real outcome was guessed, the next draft is already there.
   - **Their result:** "on average 30% faster" than optimized speculative decoding at batch 1, up to 5× over autoregressive, with bonus-token prediction "up to 90%" accurate.
   - **For us:**
     - It is **exact by construction**, which TP is not at full speed.
     - The gain is bounded by the drafter's share of the step (~7–10 ms of ~105) plus the pre-speculation hit rate. **Estimate +5–15%**, against TP's 1.4–1.8×.
     - The unknown is the link: DFlash2 drafts from the target's hidden states, so each step ships those features to node B. Their size and the link's latency decide it.
2. **Online drafter training on the second node** (Aurora's own setup, §2.3): node B trains and hot-swaps the drafter while node A serves. This turns self-speeding's nightly training into a continuous one, without stopping the server.
3. **Prefill on one node, decode on the other** (cache-aware PD disaggregation, [post](https://www.together.ai/blog/cache-aware-disaggregated-inference): ~40% more sustainable throughput on B200 with RDMA).
   - It removes M4's two-stream stall (a decode waiting behind the other stream's prefill chunk) by moving prefill off the decoding node.
   - It needs the prompt's KV and recurrent state shipped over the link. The hybrid model's state is small per layer, but the total and the transfer time against recomputation are to be measured.

## 7. Proposed next steps (none started)

| # | Step | Cost | Decides |
|---|---|---|---|
| 1 | ScaleSearch on the DFlash2 drafter, acceptance A/B at 12 drafts on the replay set (§2.1) | ~½ day, ~1 GPU-hour on second-puffin | A free +0–3% tokens per step, exact |
| 2 | The selector alone on the 13 tasks where nights 2–4's default runs disagree (§3.1) | ~1 hour of model time, no agent runs | Whether best-of-K is worth building |
| 3 | The collateral column in `swe-bench report` (§3.2) | a few hours, no GPU | Sharper night comparisons |
| 4 | Aurora's rejected-token loss and the volume arithmetic written into SELF_SPEEDING §4–§5 (§2.3) | docs | Realistic expectations before training |
| 5 | DAS's window and budget rules written into the moonshot's P1 design (§2.4) | docs | P1's proposer |
| 6 | Acting-aware eviction in the checkpoint store (§2.5) | ~1 day in the engine, after the step savings | Multi-session latency |
| 7 | `docs_ask` (plan-divide-conquer) and a `research` skill (§4.1, §4.2) | a few days each | New product capability |
| 8 | Read DeepSWE v1.1's task format and Epoch's flaw list; check CoderForge's environments (§3.3) | hours, reading | Whether either becomes a list |

Steps 1 and 2 are the only ones that need the GPU, and both are short.

Added by the deeper pass (§8). These are proposals for the owner, not backlog items:

| # | Step | Cost | Decides |
|---|---|---|---|
| 9 | Step 1 restated (§8.2): the BF16 drafter against the served NVFP4 one, at 12 drafts on the replay, before any re-quantization | ~1 GPU-hour on second-puffin; a model-matrix change and a server restart, no code | Whether there is a gap above noise for ScaleSearch (or anything else) to recover |
| 10 | Step 2's selector built in R2E-Gym's order: regression tests first, then a pairwise judge over patches without the agent's reasoning (§8.4) | inside step 2's hour | Whether the cheap filter alone captures most of the headroom |
| 11 | YAQA on the *target*, as a model candidate for a night, only if the owner wants a quality lever (§8.6) | 1–30 GPU-hours plus a night; NVFP4 untested by its authors | Quality, not speed |
| 12 | Voice input biased toward the repository's identifiers from `ling-code`'s symbol index (§8.7) | ~1 day, once voice in `ling web` is the target | Dictating code names, the speech post's failure mode |

## 8. The deeper pass (2026-10-13, afternoon)

**What was done:**
- **The whole blog:** all 21 index pages of the research blog were listed (about 80 posts) and every post triaged against the question (§8.8). Twelve posts were read that the morning pass had not.
- **The papers behind the approved items:** ScaleSearch, DAS, Aurora, ThunderAgent, V1, DeepSWE and plan-divide-conquer were re-read section by section for the exact claim, table or formula, not the abstract.
- **Their cited work:** followed where it touches something in our plan: R2E-Gym (DeepSWE's verifiers), Continuum (ThunderAgent's baseline), OnlineSPEC and Online Speculative Decoding (Aurora's), SuffixDecoding, Infini-gram and AdaSpec (DAS's), and the drafter's own model card (ScaleSearch's target here).
- **One more measurement from disk** (§8.5).

Two-node work (§6) is unchanged, per the owner's ordering.

### 8.1 What changes for work in flight

| Work | Was | Now | Section |
|---|---|---|---|
| ling-engine BACKLOG §19.22, the drafter's NVFP4 scales | "+0–3% tokens per step" from re-quantizing with ScaleSearch | Measure the BF16 drafter against the served one at 12 drafts first. The model card calls its own 3.60/3.71 gap noise, GPTQ already gave nothing there, and half the drafter's error (W4A4 activations) is outside ScaleSearch's reach. Re-quantize only if the gap is above noise. | 8.2 |
| `moonshot/p1`, the proposer's user-history source (S4) | "a window of the last 16–32 sessions" (DAS) | Partition by repository, keep as much history as lookup cost and memory allow. DAS's larger windows had *higher* acceptance; its 16–32 rule answers RL policy drift, which a fixed served model does not have. | 8.3 |
| The chooser test script (`survey/backlog`) | A pairwise judge over three runs' patches | Run the repository's existing (PASS_TO_PASS-style) tests first and drop patches that break them, then judge pairwise over the diffs alone, without the agent's reasoning, using V1's parameters. | 8.4 |

### 8.2 The drafter's quantization: measure the ceiling first

What the served drafter's card (`maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`, the file pinned in the model matrix) says, beyond the 3.60/3.71 figure the morning pass used:
- **The gap is within noise.** "Run-to-run noise on the acceptance length is about ±0.2, so BF16 and this checkpoint are at parity." The 3.60/3.71 comes from 5 prompts × 600 tokens × 3 runs at temperature 0.7.
- **The agentic long-context readings go both ways:**

  | Context | BF16 | NVFP4 calibrated |
  |---|---|---|
  | 8K | 3.12 | 3.03 |
  | 32K | 3.18 | 3.32 |
  | 64K | 3.44 | 3.19 |
  | 85K | 3.18 | 3.21 |

- **Better rounding has been tried.** GPTQ, keeping q/k/v in BF16, and SmoothQuant folding were "tried without measurable gain".
- **It is W4A4.** Weights are round-to-nearest with scale amax/(6·448). Activations use static per-tensor scales calibrated on ~621K rows. ScaleSearch changes only the weights' block scales, so the activation half of the error is outside its reach. Uncalibrated RTN was 3.26, so the calibration was the large step.
- **The card's own claim is not ours.** The card calls block 8 "the DFlash 2 block size and cannot be raised". Our own sweep (SELF_SPEEDING §3.2) found 12 drafts best on SGLang, so at 12 the parity claim is untested in either direction.

**What this means:** the morning's "+0–3%" is an upper bound on a gap nobody has shown to exist.
- The BF16 drafter (3.53 GB) fits beside the target, so the first step is an A/B of BF16 against NVFP4 on our replay at 12 drafts, two runs each (§7 row 9).
- Re-quantization is worth doing only if BF16 wins by more than the run-to-run spread. In that case ScaleSearch, or a KL-to-target objective in the spirit of YAQA (§8.6), are the candidates.
- If BF16 does not win, item §19.22 closes at the cost of one GPU-hour.

### 8.3 DAS, read again: what the window rule is for

The paper's own wording, which the morning pass summarised the wrong way round:
- **Figure 7's caption:** "Larger windows (e.g., 16, 32, all) give higher acceptance because they offer more matching continuations".
- **Why "all" lost anyway:** "window_all shows the highest latency because querying and maintaining a full global history is more expensive and includes stale trajectories, so in practice moderate windows (16 or 32) strike a better balance".
- **The staleness is RL's:** "Because policy evolves over time, long-history generations become less predictive."
- **Per-problem trees beat a global one** because "patterns from one problem rarely transfer reliably to another" (Figure 6).

**For moonshot P1/P2:**
- **The staleness argument does not apply.** The served model does not change between sessions (until a promoted drafter, which does not change the target's outputs).
- **What does apply:**
  - **Lookup cost and memory.** DAS saw speculative-step time grow with the window; S4's budget is SuffixDecoding's ~6 GB for 572M tokens.
  - **Partitioning by the unit that transfers.** For DAS that is the problem; for us it is the repository, which the moonshot's S4 already partitions by.
- **The rule to take:** per repository, as much history as the lookup budget allows, oldest out first; not "16–32 sessions".
- **What DAS does not give:**
  - **Proposal parameters:** it reports no acceptance lengths, budget values or class thresholds. The budget's closed form (its Eq. 7) is in terms of fitted constants.
  - **A code-specific win over other model-free proposers:** its code-RL result is "roughly a 25% reduction in the rollout time" (the conclusion says "over 30%"), against no speculation.
- **From its references:**
  - **SuffixDecoding:** already the moonshot's anchor.
  - **Infini-gram:** suffix arrays with millisecond queries over trillions of tokens, no precomputed counts. It is the on-disk alternative if S4's in-memory tree outgrows its cap. Its per-token size was not checked here.
  - **AdaSpec:** adapts speculation to load and SLOs, "up to 66%" over prior systems. That is a multi-tenant concern, not ours at one or two streams.

### 8.4 The chooser: what the cited work says to build

- **R2E-Gym** (DeepSWE's source for hybrid verification, [arXiv 2504.07164](https://arxiv.org/html/2504.07164)), SWE-bench Verified, a 32B editing agent:
  - **The ceiling:** Pass@1 34.4% and Pass@26 64.4%. Its hybrid selection reaches 51.0% at Best@26 and 49.4% at Best@16.
  - **The cheap part:**
    - The repository's own regression tests alone, used to drop patches that break existing behaviour, take it "from 42.9% to 47.4%". Adding generated reproduction tests reaches 51.0%.
    - Generated tests discriminate poorly: "for the majority of problems, less than 20% of tests provide discriminative signal".
  - **A trap:** an execution-free judge "can be biased by agent thoughts rather than the patch itself", so the judge should see the issue and the diff, not the transcript.
- **V1** (read in full this time, [arXiv 2603.04304](https://arxiv.org/html/2603.04304)):
  - **The judge is the generator.** The same model judges what it generated; no separate verifier is trained.
  - **The format:** each comparison rates both solutions 1–10 in one call. A win, tie or loss scores 1, 0.5 or 0, weighted by the rating gap (max(|rᵢ−rⱼ|/9, τ), τ = 0.1).
  - **The pairing:** first random disjoint pairs until every candidate has been compared twice (d_min = 2), then Swiss rounds among neighbours in score within a window of 8. The parameters were not tuned per benchmark.
  - **The code prompt:** "You are an expert code reviewer. Compare two solutions to a programming problem and rate their correctness", preferring "the more robust and fault-tolerant one" when both look right.
  - **SWE-bench Lite** (300 tasks, N = 8 patches, the same model generating and judging): **pairwise 33.3%, pointwise 28.3%, a single run 26.3%.**
    - Pointwise scoring gave almost everything top marks, the failure the morning pass noted.
    - Compute-matched on code generation (64 calls, a 4B model), pointwise reached about 33% and pairwise 45%.
- **DeepSWE**, for the numbers already used:
  - **The verified figures:** Pass@1 42.2% (the mean of 16 runs), Pass@16 71.0%, hybrid Best@16 59.0% and Best@8 57.9%.
  - **Its two selectors:** its execution-based selector has a model write tests and edge cases; its execution-free one is a model "trained for 2 epochs over correct/incorrect patches".
  - **Its loop:** reward only when the agent calls submit; 64K context and 100 steps at evaluation.

**For the chooser script:**
1. Drop the candidates that break the instance's PASS_TO_PASS tests. These are the regression tests the repository already has; the SWE-bench dataset names them per instance.
   - **Use only the PASS_TO_PASS outcome.** Never use FAIL_TO_PASS or the `resolved` flag, or the selector-alone test grades itself.
   - **Read it from disk.** Each candidate's PASS_TO_PASS result is what the broken-test report column (in flight) extracts from the grading logs already on disk. Re-running the suites in Docker would be grading time, not step 2's hour of model time.
2. Judge the survivors pairwise with V1's procedure, giving the judge the issue text and the diff only.
3. Report which stage made the choice.

On three candidates, V1's tournament is just the three pairs.

### 8.5 Measured here: how much a longer per-task limit can buy

The owner moved the per-task limit from 45 to 60 minutes from night 7 (2026-10-13). From disk, over the eight arms of nights 1–4:
- **Data:** 364 sessions, 50 tasks in night 1 and 44 in nights 2–4.
- **Method:** each instance's `wall_s` and status from `runs/n{1..4}-*/instances/*.json`, and plain grading from `eval/1/grading.json`.

| Session ended | Sessions | Resolved | Rate |
|---|---|---|---|
| Finished in ≤ 15 min | 249 | 194 | 78% |
| 15–30 min | 60 | 27 | 45% |
| 30–40 min | 13 | 8 | 62% |
| 40–45 min, finished | 5 | 2 | 40% |
| Stopped at 45 min (timeout) | 32 | 10 | 31% |

(Five sessions ended empty or in error and are not in the table.)

- **What 15 more minutes can reach:** only the 22 unresolved timeouts in all eight arms, under 3 per arm, so at most about +6 points per arm. Ten more timeouts resolved anyway, because the patch left at the limit is graded.
- **What it is likely worth:** if the extra time resolves the 30–45-minute band's share (about half), +1 to +1.5 tasks per arm (+2–3 points), not significant at 44 tasks. *(Estimate.)*
- **What it costs:** at most 32 × 15 min of agent time over eight arms, about 1 hour per arm against ~10 hours today. That is under +10% agent time and about +5% night wall time at two at once, not the +20% assumed when the question was asked. *(Estimate.)*
- **Independent evidence:** CoderForge's failed trajectories ran 18–28% more steps than its successful ones on the same tasks. Long sessions are mostly lost sessions, here as there.
- **The decision itself is the owner's and stands.** These numbers size it: cheap, small, and it needs a new default baseline (already noted).

### 8.6 Smaller additions to §2 and §3

- **Aurora, precisely:**
  - **The loss:** KL(p_target ∥ p_draft) on accepted tokens + λ_discard · the same on discarded ones (λ = 1.0, discarded positions top-10 filtered). The appendix calls it reverse KL, while the formula reads forward. Copy the formula, not the label.
  - **The headline numbers are not ours:** the 3.08/2.99/2.63 come from Qwen3-8B on a shuffled five-domain stream (GSM8K, Spider, CodeSearchNet, Finance-Alpaca, chat). On a single-domain coding workload the static-versus-adapted gap may be smaller.
  - **Its coding table is speculation against none:** 1.92× at batch 1 on Qwen3-Coder-Next, not Aurora against a static drafter. Its update interval is "every 100 requests" in one place and 80 in another.
- **ATLAS-2:** Together's production version (AI Native Conf post) claims "a further 1.2×" on models that already had static speculators. That is the only production number for online adaptation; we have no measurement that maps to it.
- **Customized speculators, the unit:**
  - **What 20M counts:** "20M tokens is approximately 10K prompt-response pairs at 2048 sequence length". The 20M counts whole sequences, and whether the responses were the target's own is not stated.
  - **The volume estimate in §2.3, restated:** it stays at 3–5 nights if only response tokens count toward a 20M-equivalent. It falls to 1–3 nights if, like Together's, prompt tokens count too. SELF_SPEEDING's Phase 0 decides before any of this matters.
- **OnlineSPEC** (Aurora's cited peer, [arXiv 2603.12617](https://arxiv.org/abs/2603.12617)): online drafter updates from verification feedback, "up to 24% speedup over seven benchmarks and five foundation models". It confirms the direction without a number we can use.
- **ThunderAgent, the actual rule:**
  - **The pressure test:** the store is under pressure when capacity < Σ reasoning contexts + Σ acting contexts × 2^(−t), with t the tool's running time (Δt = 5 in their setting).
  - **The scores:**
    - pause score 1/c + [acting];
    - restore score 1/c + [reasoning].
  - **One ambiguity:** the text says it evicts "the shortest context lengths" first, which those scores make ambiguous. Our item should not copy the length term blindly. For us, a long acting session is also the one most expensive to re-prefill.
  - **The alternative:** Continuum ([arXiv 2511.02230](https://arxiv.org/abs/2511.02230)), its baseline, pins a session's cache for a TTL set from the reload cost and the queueing an eviction would cause.
  - **Which fits us:** at one or two streams, a TTL set by the session's re-prefill cost is the simpler rule to try first, and the decay rule second.
- **YAQA** (Together/Cornell, [arXiv 2505.22988](https://arxiv.org/abs/2505.22988), code [Cornell-RelaxML/yaqa](https://github.com/Cornell-RelaxML/yaqa)):
  - **What it changes:** rounding that minimises the whole model's KL to the original rather than each layer's error. It cut KL about a third against GPTQ/LDLQ (Llama 3.1 8B at 4 bits with QTIP: 0.019 → 0.013; with INT4: 0.038 → 0.028).
  - **What it costs:** 1–30 GPU-hours for a 10B model depending on the sketch.
  - **Its limits:** NVFP4 is not evaluated, and it needs the original BF16 model.
  - **For the target:** a quality candidate beside §2.2's ScaleSearch one, not a speed item.
  - **For the drafter:** the right objective is acceptance, which is KL to the *target*, not to the BF16 drafter. YAQA's machinery points at that objective, but nobody has published it.

### 8.7 Product and benchmark notes from the newly read posts

- **Plan-divide-conquer, the paper's numbers** (for §4.1's `docs_ask`):
  - **The tasks:** at 128K, InfiniteBench and LongBench-v2 tasks, with Qwen-72B as the planner.
  - **Llama-70B in 16K chunks against whole-context reading:**
    - Math, finding the second-smallest number: 0.57, against GPT-4o reading everything at 0.33.
    - Key-value retrieval: 1.00.
    - Open QA: 0.63, against 0.56 single-shot.
  - **The case it fails:** character inference across a whole novel stays at 0.04–0.18 for every model and chunk size, the "high task noise" case.
  - **The chunk size:** chosen from m = 5 sample documents per candidate size, matching an exhaustive search.
  - **Overlap:** a 1K-token chunk overlap gave mixed, mostly marginal results, so `docs_ask` need not overlap.
- **Speech models fail on names** ([post](https://www.together.ai/blog/how-speech-models-fail)):
  - **The size of the problem:** across 15 recognisers, street names were mis-transcribed 39% of the time on average. Whisper-Large's general word error rate is 14%, but 27% on names.
  - **Their fix:** under 1,000 synthetic samples gave Whisper-Base a 60% relative improvement.
  - **For Mightling:**
    - The voice sidecar runs `faster-whisper-small` (ONYX §5). Spoken code identifiers are the same kind of rare, context-poor token.
    - faster-whisper accepts an initial prompt and hotwords, so biasing toward the open repository's identifiers from `ling-code` is cheap (§7 row 12). That parameter is from the library, not from the post.
- **EinsteinArena:** agents improve verified results on open problems through a public API. The lessons transfer to Night Shift:
  - optimise against the real verifier, not a proxy;
  - require a minimum improvement before a new best counts;
  - keep notes of failed attempts so later runs do not repeat them.
  The ledger already does the last.
- **ParallelKernelBench:**
  - **How far models get:** the best model wrote correct multi-GPU kernels for 28 of 87 problems zero-shot, and only 22 beat PyTorch + NCCL.
  - **What an agent loop adds:** compile errors, tests and timings in a loop raised one model from 24 to 35 correct, then plateaued after about 20 refinement steps.
  - **Where they fail:** rank coordination and choosing the transfer mechanism (TMA and NVLS almost never used).
  - **Relevant to:** the two-node tensor-parallel work at the end of the plan, if agents write its kernels.
- **Together's own lessons from agents doing inference engineering** (skipped in the morning, read now): documentation quality "nearly perfect[ly]" predicted output quality; agents assumed success when no error appeared, until told to verify against a live endpoint; long jobs ran in persistent sessions, with the agent estimating completion and sleeping instead of polling. These match the rules this project already follows (watchers, explicit server checks).
- **The Open Source AI Stack** ([post](https://www.together.ai/blog/the-open-source-ai-stack)): it names PI, OpenCode and Amp as open harnesses besides Codex. It confirms §4.5, with no numbers.

### 8.8 Everything else, triaged

| Work | Verdict |
|---|---|
| Together Megakernel (whole model in one kernel; time to first 64 tokens 281 → 77 ms for a voice customer) | Not here. It removes gaps between kernels, and M4 stage 4 found one-launch kernels and graphs saved nothing measurable. Our step is weight reads. |
| together.compile, FlashAttention-3, ThunderKittens (original, Blackwell, Vera Rubin), the kernels-team post | Hopper/B200/Rubin kernels or cloud tooling; FA4's portable ideas already covered (§5). |
| Cache-aware PD disaggregation (35–40% more sustainable QPS on B200 with RDMA) | Two-node only, already in §6. |
| ATLAS (2025 post) | Superseded by Aurora (§2.3, §8.6). |
| Distribution-aware speculative decoding (blog) | Same as DAS; no numbers beyond the paper. |
| QTIP, BitDelta, Kitty | Weight formats without hardware support on GB10, multi-tenant fine-tune compression, lossy KV. |
| Minions, Mixture-of-Agents, MoAA | Need several models or a cloud half. |
| DeepCoder (14B RL coder), RARO (reasoning from demonstrations, no verifier), DPO / continued / long-context / multi-turn fine-tuning deep dives, TorchForge | Training recipes for models we don't train. |
| RAG fine-tuning for a code assistant (Morph, Mistral 7B) | A 7B fine-tuned per repository; Mightling's answer is retrieval at use time (`ling-code`). |
| Long-context retrieval with M2-BERT (80M, 32K context, LoCo) | 2024 retrieval models on a mostly synthetic benchmark; PDF_SEARCH's embedder choice postdates it. |
| Dragonfly (vision-language), ColQwen2 | Wait for the engine's vision encoder (§4.6). |
| Violin (video translation skill) | Cloud models end to end; nothing for a coding pair programmer. |
| DSGym, open data-scientist agent, AI for databases, FutureBench, "What do LLMs think", AGI point of view, EinsteinArena (§8.7) | Outside the question, or lessons only. |
| Mamba-3, Parcae, LoLCATs, BASED, Evo, StripedHyena, Mamba-3B-SlimPJ, Monarch Mixer, Hyena, H3/FlashConv, FlashFFTConv | Architecture research for models we don't train. |
| RedPajama (v1, v2, FAQ), CocktailSGD, decentralized training (four posts), HELM on the Together computer, Llama-2-32K posts, pricing update | Training data, training systems, or history. |
| Untied Ulysses, Opportunistic Expert Activation, FFT-based subspace selection, TTT-Discover | Multi-GPU context parallelism, MoE, optimizer research, test-time RL at scale. |
| Chipmunk, CDLM | Diffusion models as targets (§5). |
| Medusa, Flash-Decoding, FlexGen, Deja Vu, TEAL, AutoJudge | Already in §5. |

## Sources

Read 2026-10-13.

- **Together pages:** [Research](https://www.together.ai/research), [ICML 2026](https://www.together.ai/blog/icml-2026), [MLSys 2026](https://www.together.ai/mlsys-2026), [research blog](https://www.together.ai/research-blog) (21 pages).
- **Speculation:**
  - Aurora [arXiv 2602.06932](https://arxiv.org/html/2602.06932); ATLAS [post](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas); [customized speculators](https://www.together.ai/blog/customized-speculative-decoding).
  - DAS [arXiv 2511.13841](https://arxiv.org/html/2511.13841); SSD [arXiv 2603.03251](https://arxiv.org/html/2603.03251).
  - [MagicDec](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference); [Mamba in the Llama](https://www.together.ai/blog/the-mamba-in-the-llama-distilling-and-accelerating-hybrid-models); [Sequoia](https://www.together.ai/blog/sequoia); [SpecExec](https://www.together.ai/blog/specexec).
- **Quantization and sparsity:** ScaleSearch [arXiv 2605.12464](https://arxiv.org/html/2605.12464); Kitty [arXiv 2511.18643](https://arxiv.org/abs/2511.18643); [TEAL](https://www.together.ai/blog/teal-training-free-activation-sparsity-in-large-language-models); [AutoJudge](https://www.together.ai/blog/introducing-autojudge-streamlined-inference-acceleration-via-automated-dataset-curation).
- **Serving and kernels:** ThunderAgent [arXiv 2602.13692](https://arxiv.org/html/2602.13692); [cache-aware disaggregation](https://www.together.ai/blog/cache-aware-disaggregated-inference); [ThunderKittens on Blackwell](https://www.together.ai/blog/thunderkittens-nvidia-blackwell-gpus); Ladder Residual [arXiv 2501.06589](https://arxiv.org/html/2501.06589v5).
- **Agents and benchmarks:**
  - [DeepSWE](https://www.together.ai/blog/deepswe); [GLM-5.3 on DeepSWE](https://www.together.ai/blog/glm-5-3-vs-glm-5-3-flash-on-deepswe-cost-coding-and-routing); [DeepSWE repo](https://github.com/datacurve-ai/deep-swe); [Epoch review](https://epoch.ai/benchmarks/deepswe/review).
  - [CoderForge-Preview](https://www.together.ai/blog/coderforge-preview); V1 [arXiv 2603.04304](https://arxiv.org/abs/2603.04304); [ReasonIF](https://www.together.ai/blog/large-reasoning-models-fail-to-follow-instructions-during-reasoning-a-benchmark-study); OSWorld-Human [arXiv 2506.16042](https://arxiv.org/html/2506.16042v1).
- **Product:** [plan-divide-conquer](https://www.together.ai/blog/plan-divide-conquer); [Open Deep Research](https://www.together.ai/blog/open-deep-research); [Together Link](https://www.together.ai/blog/together-link-frontier-quality-open-models-in-the-harness-you-already-use); [ColQwen2 document RAG](https://www.together.ai/blog/multimodal-document-rag-with-llama-3-2-vision-and-colqwen2); [CDLM](https://www.together.ai/blog/consistency-diffusion-language-models).
- **Measured here:** `ling-admin swe-bench report n4-default --against n3-default` and `--against n2-default` (2026-10-13). Night 4's `restart-2-against-default.txt` for output token counts.
- **Added by the deeper pass (§8), read the afternoon of 2026-10-13:**
  - **Cited work:** R2E-Gym [arXiv 2504.07164](https://arxiv.org/html/2504.07164); Continuum [arXiv 2511.02230](https://arxiv.org/abs/2511.02230); OnlineSPEC [arXiv 2603.12617](https://arxiv.org/abs/2603.12617); Infini-gram [arXiv 2401.17377](https://arxiv.org/abs/2401.17377); AdaSpec [arXiv 2503.05096](https://arxiv.org/abs/2503.05096); plan-divide-conquer [arXiv 2506.16411](https://arxiv.org/html/2506.16411); YAQA [arXiv 2505.22988](https://arxiv.org/abs/2505.22988) and [post](https://www.together.ai/blog/yaqa).
  - **The drafter's card:** [maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal](https://huggingface.co/maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal).
  - **Posts:** [AI Native Conf announcements](https://www.together.ai/blog/ai-native-conf-research-and-product-announcements); [agents for inference engineering](https://www.together.ai/blog/ai-agents-to-automate-complex-engineering-tasks); [speech models fail](https://www.together.ai/blog/how-speech-models-fail); [the open source AI stack](https://www.together.ai/blog/the-open-source-ai-stack); [RAG fine-tuning](https://www.together.ai/blog/rag-fine-tuning); [Aurora](https://www.together.ai/blog/aurora); [DAS](https://www.together.ai/blog/distribution-aware-speculative-decoding); [M2-BERT retrieval](https://www.together.ai/blog/long-context-retrieval-models-with-monarch-mixer); [Violin](https://www.together.ai/blog/violin-open-source-translation-skill); [EinsteinArena](https://www.together.ai/blog/einsteinarena); [ParallelKernelBench](https://www.together.ai/blog/parallelkernelbench); RARO [arXiv 2511.21667](https://arxiv.org/abs/2511.21667).
  - **Measured here:** per-instance `wall_s` and `eval/1/grading.json` of `runs/n1-*` to `runs/n4-*` (§8.5).
