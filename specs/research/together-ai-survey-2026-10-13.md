# Moonshot 2: what Together AI's research offers Mightling (2026-10-13)

**Status:** research survey, nothing built. Reading only: no GPU, no model server, no agent run. One number is new and measured: the best-of-K ceiling in §3.1, computed from the default arms of nights 2 to 4 already on disk.
**Question:** what in Together AI's published research could make ling-engine and the production server faster, improve or sharpen our benchmarks, or give Mightling new features, given one 27B hybrid model (Qwen3.8-27B NVFP4 with DFlash2 at 12 drafts) on one or two GB10s, air-gapped.
**Reads with:**
- ling-engine `reports/research-2026-10-09.md` (the arXiv survey of the year's inference papers) and `specs/drafts/MOONSHOT-repository-aware-decoding-2026-10-13.md` (the first moonshot);
- ling-engine `specs/DREAMFERENCE_LING_ENGINE_BACKLOG.md` §19 (items 8, 10, 19, 20, 21);
- [SELF_SPEEDING](../DREAMFERENCE_SELF_SPEEDING.md), [PDF_SEARCH](../DREAMFERENCE_PDF_SEARCH.md), [MIGHTLING_SWE_BENCH](../DREAMFERENCE_MIGHTLING_SWE_BENCH.md) and [agent-survey-2026-10-09.md](./agent-survey-2026-10-09.md).

Numbers from Together's papers and posts are the authors' own, on their hardware, read on the date above. Numbers measured on our machines say so. Everything else is an estimate and marked as one.

---

## Short answer

1. **No Together paper changes the engine plan; four sharpen items already in it.**
   - **ScaleSearch (MLSys 2026) gives a clean, exact speed item that is not in the plan:** re-quantize the DFlash2 drafter's NVFP4 scales. The drafter's own card puts its NVFP4 loss at 3.60 against 3.71 accepted per block of 8 in BF16. That bounds the gain at about **+3% tokens per step**, at no cost per step and with output unchanged.
   - **Aurora/ATLAS and the "customized speculator" post set realistic expectations for self-speeding.** Their training loss (KL on accepted tokens plus KL on the top-10 of each rejected position) is the one to use. Online training added little over a fine-tuned static speculator: 3.08 against 2.99 accepted per step. Data volume is the binding cost: +10% needed ~20M tokens and +20% ~50M tokens. One SWE-bench night arm here produces 0.4–0.6M output tokens.
   - **DAS (MLSys 2026)** adds two measured rules to moonshot P1's design:
     - per-request suffix trees over a sliding window of the 16–32 most recent sessions, rather than one global tree;
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
  - Quantization error falls about 25–27%. The format is unchanged: the result is ordinary NVFP4 with different scale bytes, so every NVFP4 kernel reads it. The paper says it "seamlessly integrates" but does not say so in those words.
  - Offline cost is 1.3–1.7× plain quantization on their microbenchmark.
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
  - **What reaching 20M needs:** reaching Together's 20M-token point needs the spec's `drafter generate` step (the target re-answering collected prompts).
  - **What that costs here:** at ~50 tok/s single stream that is ~4.3M tokens a day, and ~5.8M at two streams (2 × ~33.5 tok/s, production's and stage 4's measured rate). So 20M tokens is **3–5 dedicated nights** (estimate).
  - **The gate stays:** Phase 0 ("is there anything to adapt to") remains the first gate.
- **Expect less than Together's figures.** Their gains are against EAGLE-3 speculators at 2.5–3 accepted per step. DFlash2 already accepts ~5 at 12 drafts on our replay set, so headroom per step is smaller. The OVERVIEW stretch target (≥ 8 accepted) stays a stretch.

### 2.4 Moonshot P1: DAS's two rules (design input)

- **What DAS is** ([arXiv 2511.13841](https://arxiv.org/html/2511.13841), MLSys 2026; Together, Stanford, UCSD): a model-free, lossless proposer for RL rollouts.
- **What it found:**
  - **Per-request suffix trees** over a **sliding window of recent rollouts** beat one global tree. A window of 16 or 32 beat "all" (Figure 7). Updates take under a millisecond per 100 tokens (Ukkonen).
  - **A length-aware budget** gives long requests aggressive draft budgets, medium ones less, and short ones none. It is "up to 15%" better than an unlimited budget (Figure 12). Code RL rollouts were ~25% faster end to end on 8×H100.
- **What we take** (design input for the first moonshot's §3.1 proposer interface and P1, no new item):
  - the user-history source as a window of the last 16–32 sessions in the same repository, not the whole history;
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
  - With planning, 70B-class open models beat a frontier model reading the whole text at once, from 1K to 64K tokens.
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
| 1 | ScaleSearch on the DFlash2 drafter, acceptance A/B at 12 drafts on the replay set (§2.1) | ~½ day, ~1 GPU-hour on node-b | A free +0–3% tokens per step, exact |
| 2 | The selector alone on the 13 tasks where nights 2–4's default runs disagree (§3.1) | ~1 hour of model time, no agent runs | Whether best-of-K is worth building |
| 3 | The collateral column in `swe-bench report` (§3.2) | a few hours, no GPU | Sharper night comparisons |
| 4 | Aurora's rejected-token loss and the volume arithmetic written into SELF_SPEEDING §4–§5 (§2.3) | docs | Realistic expectations before training |
| 5 | DAS's window and budget rules written into the moonshot's P1 design (§2.4) | docs | P1's proposer |
| 6 | Acting-aware eviction in the checkpoint store (§2.5) | ~1 day in the engine, after the step savings | Multi-session latency |
| 7 | `docs_ask` (plan-divide-conquer) and a `research` skill (§4.1, §4.2) | a few days each | New product capability |
| 8 | Read DeepSWE v1.1's task format and Epoch's flaw list; check CoderForge's environments (§3.3) | hours, reading | Whether either becomes a list |

Steps 1 and 2 are the only ones that need the GPU, and both are short.

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
