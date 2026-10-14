# Optimizing a megakernel once it exists: the levers, their evidence and their order

Research note, 2026-10-14, the sequel to
[megakernel-debuggable-2026-10-14.md](./megakernel-debuggable-2026-10-14.md). That note says how to
build a persistent step for ling-engine that stays testable, and what the first build buys (a step
in the low 90s of milliseconds from ~101). This one asks what comes after: every lever the published
megakernels and the engine's own measurements point at, what each is worth on the GB10, whether it
is exact, and the order in which to pull them. Numbers are estimates from measured pieces unless
marked measured; the measurement that settles each one is named.

## 1. The shape of the remaining time

A one-row speculative step at ~35K context, with graphs and PDL (`reports/M3.md` §5, plus B16 and
the commit overlap): ~101 ms. Where it goes, and which kind of time it is:

| Component | ms | Kind | What a megakernel's first build does to it |
| --- | --- | --- | --- |
| NVFP4 and FP8 weight GEMMs, 16 GB | ~86 | bus-bound (233–244 of 273 GB/s) | removes ramps and tails; 90–93% of bus is the realistic ceiling |
| Attention over the KV cache | ~11 | bus-bound (~2.2 GB of BF16 KV at ~205 GB/s) | nothing: it needs the same bytes per second the weights need |
| DeltaNet recurrence, conv, gating | ~3 | latency-bound, bus idle | overlaps with the weight stream on spare SMs |
| Norms, β/α projections, conversions, top-k | ~4 | latency-bound | overlaps |
| Drafter (5 layers, 1.55 GB, its attention) | ~7–10 | serial: needs the commit before, the verify after | bubbles removed inside it; nothing else |
| Host: lattice walk, accept, graph launches | <1 | serial | unchanged while draft, verify and commit stay three kernels |

Two facts fix the floor. The bus delivers 257–259 GB/s with the production server resident and
262–264 bare (`reports/micro-2026-10-10.md`, measured); and a read stream from **8 SMs already
reaches 257 GB/s** (measured), so the other 40 SMs are free for anything that is not bytes. The
exact floor of the step is therefore the bytes it must move, 16 GB of weights plus 2.2 GB of KV plus
the drafter's 1.55 GB, about **75 ms at 262 GB/s**, plus the serial parts no overlap removes. Every
exact lever below moves the step from the low 90s toward the low 80s; only the non-exact levers
(fewer bytes) move the floor itself.

## 2. The levers

Each entry: mechanism, evidence, expected effect here, exactness class under the engine's policy
(exact = bit-identical to the graph path; non-exact = behind a switch, promotable only through the
§13 gates), and the measurement that decides it.

### 2.1 Schedule search over a validated IR (exact)

Once the schedule is data with a validator, its order, tile sizes and SM assignment are a search
space, and the timings store measures every candidate from inside the kernel.

- **Evidence.** Cohere's wave-order ablations: the tuned order beat alternatives by 1–4% and
  attention-first by 7–19%, and dependency-affinity placement was 1–2% *slower* and dropped.
  AutoMegaKernel's search loop improved over its own first schedule by 1.25–1.72×. Ada-MK's offline
  DAG search with a shared-memory constraint model cut peak shared memory 50% and "completely
  eliminat[ed] runtime branching"; its premise, that under a fixed deployment the optimal path is
  determined at compile time, is ling-compile's premise.
- **Here.** The first schedule is hand-ordered; a search over (instruction order per SM, which SMs
  stream weights versus do latency work, tile size per matrix, split-K factor for the three
  5,120-output projections that give only 40 tiles of 128 for 48 SMs, RESEARCH §16.8) should find
  2–4% that hand ordering leaves on the table. Cheap to run: each candidate is a validated
  descriptor stream and one seeded replay window.
- **Decides.** Per-SM idle time from the timings store; the search's objective is the step time on a
  fixed 60-step window at three context buckets, with bit-identity as a hard constraint.

### 2.2 Fine-grained dependencies (exact)

Replace whole-tensor waits with chunked counters so a consumer starts on the part that is ready.

- **Evidence.** Hazy splits the MLP hidden state into four chunks with four counters so the down
  projection waits only for its chunk; Cohere lets the O-projection of one KV group start when that
  group's attention is done. The failure mode of the opposite choice is measured by the worklog
  kernel that used 476 coarse `grid.sync()` per token and ended **1.65× slower than CUDA graphs**:
  "the entire GPU is held back by the slowest SM" at every coarse barrier (FlashFormer says the
  same).
- **Here.** The layer boundary (norm → GEMM → residual → norm) and the GDN block (in-projection →
  conv/recurrence → gated norm → out-projection) are the two places where a whole-tensor wait
  serialises; chunking by output tile lets the next GEMM's tiles start as their input slices land.
  Worth 1–3 ms of the per-kernel tails and ramps that the graph path pays 333 times.
- **Decides.** The timings store's wait time per instruction, before and after.

### 2.3 Synchronisation cost (exact)

- **Evidence.** Hazy's B200 breakdown: of a 600 µs pass, ~40 µs is warp-level synchronisation and
  ~250 µs is "storing, consistency waits, and loading activations"; CUDA's asynchronous barriers
  cost ~60 ns each even when already passed. SwiftSpec's kernels carry the ready flag inside the
  data (an 8-byte store whose high word is the flag, NCCL's LL protocol) so a reader needs "no
  additional synchronization". FlashFormer uses "a per thread global memory fence coupled with an
  atomic-based global synchronization".
- **Here.** Three cheap steps once the counters exist: scoped release/acquire (`red.release.gpu`,
  `ld.acquire.gpu`) instead of `__threadfence()` + `atomicAdd` + a relaxed spin; `__nanosleep`
  tuned per wait class (a producer that is known to be ~50 µs away should not poll every 20 ns); and
  intra-SM producer/consumer handoff through `mbarrier` rather than global counters when both sides
  are on the same SM (the page allocator makes that the common case). LL-style flags-in-data suit
  the small activations (a 5,120-wide BF16 row is 10 KB) that cross SMs at every layer. Worth
  ~1 ms; worth more as a precondition for 2.2, whose chunked counters multiply the number of waits.
- **Decides.** The barrier-check mode's count of waits and the timings store's spin time; a
  microbenchmark of the counter primitives on the GB10 (the B32 toy measures exactly this).

### 2.4 Overlap of latency-bound work on the spare SMs (exact)

- **Evidence.** Measured here: 8 SMs saturate the bus. Hazy's TP kernel and MonoMoE dedicate warps
  or SMs to the weight stream and overlap the rest; the worklog kernel's biggest single win was L2
  prefetch of *this* layer's remaining weights from SMs idle during attention (−9.5%), while
  prefetching the *next* layer's weights gained 1.7% "because the lines got evicted before use".
- **Here.** DeltaNet recurrence, conv, gating, the norms, the β/α projections, the FP16 conversions
  and the top-k are ~7 ms of bus-idle time per step; running them on SMs that are not streaming
  weights hides most of it, ~3–5 ms. The attention is **not** in this set: its KV read is bytes, and
  interleaving it with the weight stream only shares the bus. What does help the attention is the
  B07 split (more warps per key range) and, later, FP8 KV (2.7).
- **Decides.** Nsight Compute `dram__bytes_read` per instruction class from the timings store's
  window: a class that moves few bytes but takes long is overlap material.

### 2.5 Weight-stream pipeline depth, prefetch distance and L2 policy (exact)

- **Evidence.** FlashFormer: two pipeline stages cannot overlap the GLU's in and gate loads; going
  from 2 to 3 stages gave ~5% (170 → 179 tok/s), 5 gave nothing over 4. The worklog kernel: prefetch
  distance of one layer is too far on an L2 that the stream flushes. Measured here: 400 ns
  load-to-use latency means ~103 KB in flight across the chip, ~2.1 KB per SM, to hold the bus
  (`micro-2026-10-10.md` §2); `stream_gemm` keeps `PF` chunks in flight per warp for this reason.
  RESEARCH §16.8 already plans evict-first or no-allocate hints on the weights so the 17 GB stream
  does not flush the 25 MB L2 that holds the per-layer DeltaNet state and the activations.
- **Here.** In a megakernel the prefetch distance becomes a schedule parameter: how many
  instructions ahead the weight loads for an SM's next tile are issued, bounded by the page budget.
  The right distance is the one that keeps ≥2.1 KB per SM in flight across the boundary where the
  graph path pays a ramp today; more than that evicts. Expect ~1–2 ms of ramp recovered, already
  counted in the first build's estimate; the lever here is not losing it to a bad distance.
- **Decides.** The timings store's load-issue to load-complete gap per instruction; L2 hit rate of
  the DeltaNet state and activations under Nsight Compute.

### 2.6 One partitioning for the whole layer, redundant small work (exact)

- **Evidence.** The worklog kernel's second-biggest win (3.90 → 3.49 ms, −10%) was letting every
  SM normalise its own copy of the activation in shared memory instead of two grid barriers per
  layer: "the flops were free, the `grid.sync()` plus global round-trip were not". Its remaining
  1.65× gap to graphs it attributes to a "partition mismatch": GEMVs partition by output row, norms
  and elementwise ops by hidden slice, and "switching views needs a global exchange that no local
  trick removes".
- **Here.** At M = 13–26 rows the norm of a 5,120-wide row is a few microseconds on one SM; every
  SM that consumes it can recompute it from the residual it already holds, with the same reduction
  order, so the result is bit-identical and a dependency edge disappears. The design rule for the
  schedule generator: partition every instruction in a layer by the same dimension where the
  arithmetic allows (output tiles of the GEMM define the slices the next norm needs), and recompute
  rather than exchange anything under ~10 KB. Worth ~1–2 ms across 64 layers.
- **Decides.** Count of cross-SM edges per layer in the validated schedule, before and after.

### 2.7 Fewer bytes (non-exact: quality-gated, behind switches)

The floor is bytes. These are the only levers that move it, and they are independent of the
megakernel; it makes them compound because, once the bubbles are gone, bytes are the whole step.

| Lever | Bytes saved per step at 35K | ms at 262 GB/s | Class | Status |
| --- | --- | --- | --- | --- |
| FP8 KV cache (64 → 32 KB per token) | ~1.1 GB | ~4 | non-exact (§13 quality gate; SGLang serves BF16 KV) | RESEARCH §16, not scheduled |
| NVFP4 for the FP8 projections (DeltaNet in/out, attention q/k/v/o: 7.2 GB → ~4.0) | ~3.2 GB | ~12 | non-exact; the ARCHITECTURE precision map allows it only where the quality gate passes, group by group | the re-quant item is the drafter first |
| B26 6-bit scale codes | small (scales are 1/16 of values) | <1 | exact decode of a re-encoded tensor | built, measuring |
| Drafter in BF16 (accepts more) | −1.55 GB (costs ~6 ms) | −6, repaid only by acceptance | the owner's decision: re-quantize NVFP4 better instead | in progress |

The second row is the single largest lever left anywhere in the step, and it is a quality
question, not a kernel question: the paper's §13 gate on GPQA, LiveCodeBench and IFBench subsets
decides it per projection group.

### 2.8 Cluster-level reductions on chip (exact if the order is designed for it)

- **Evidence.** ClusterFusion keeps split-K partial sums and attention's split-KV softmax
  statistics on chip with DSMEM collectives: SM-to-SM latency "190 cycles" against "over 470" for
  global memory, ClusterReduce 1.2× faster than the off-chip path at 32 KB and 2.4× at 256 KB,
  cluster size 4 best (8 and 16 degrade). Measured here: sm_121 launches clusters of up to **8**
  blocks with distributed shared memory (`micro-2026-10-10.md` §3).
- **Here.** The three 5,120-output projections give 40 tiles of 128 for 48 SMs (RESEARCH §16.8),
  so they run split-K or Stream-K to balance the stream, and their partial sums meet in global
  memory today. A cluster of 4 that reduces its partials over DSMEM removes that round trip and the
  wait on it. The reduction order inside the cluster is fixed and known, so the result is
  deterministic; it is **exact only if the generator reproduces today's split-K summation order**,
  otherwise it is a non-exact mode until promoted. The attention's split-KV combine
  (`attention_combine_kernel`) is the same pattern. Worth ~1–2 ms; worth measuring because it also
  removes global-memory traffic from the activations, which 2.3's LL flags otherwise carry.
- **Decides.** A DSMEM reduce microbenchmark at cluster 2, 4 and 8 on the GB10 (not yet measured;
  ClusterFusion's numbers are H100's), then the projections with and without.

### 2.9 Device-side decisions and a resident kernel (exact, low value)

- **Evidence.** FlashSpec runs the accept test on the GPU so the per-token decision does not stall
  on the CPU; SwiftSpec's kernels poll flags in global memory instead of waiting on the host; the
  Hopper utilization study puts raw dispatch at ~30% of a *kernel-per-op* decode iteration at a
  fixed 5 µs per launch, which the graph path has already removed here (idle ≤ 1 ms per step, the
  B08 gate).
- **Here.** The step's two host decisions (the lattice walk after the draft, the accept rule after
  the verify) each end a graph and start another: three graph launches per step, a few µs of host
  arithmetic, perhaps 0.1–0.3 ms in all. Moving the selector's 16 × 16 lattice walk and the accept
  rule onto the device fuses the three persistent kernels into one; a resident kernel that never
  exits and reads the next anchor from a mailbox removes the last launch. The gain is under 0.5%
  and the cost is the whole-step kernel the first note argues against (one deadline, one failure
  domain, the accept rule's sampling on the device). Do this last, if ever, and only after 2.1–2.6
  have made the remaining host time visible in the trace.
- **Decides.** Host gap per step in the Nsight Systems trace after the other levers.

### 2.10 Taking the drafter off the critical path (structural, not a kernel lever)

- **Evidence.** SwiftSpec's main result (1.43–1.50× from "parallel tree generation" alone) comes
  from drafting round n+1 while verifying round n−1, which needs a draft model that does not wait
  for the target's output. It notes that EAGLE-style drafters cannot do this because they depend
  on the target's verified features, and drafting in parallel lowered its compression ratio ~9%.
- **Here.** DFlash2 is such a drafter: its five layers read the target's hidden features of the
  accepted tokens, so the draft of step n+1 needs the commit of step n. The ~7–10 ms of drafter time
  per step is therefore serial by construction and no scheduling hides it. Three ways out, all
  outside the megakernel: a shorter drafter (fewer layers at the same acceptance, a training
  question), overlapping the drafter's own layers with the commit's DeltaNet replay (the commit
  overlap already runs the replay on a second stream; inside one kernel the two interleave on
  disjoint SMs), and the copy proposer (moonshot P1), which proposes from the context without the
  target's features and so *can* run ahead. Record here; decide in the engine's roadmap.

### 2.11 Two streams and prefill inside the schedule (exact)

- **Evidence.** Hazy's TP kernel mixes prefill and decode in one instruction stream with paged KV
  and hides batch scheduling with CPU-side pipelining. Measured here (stage 4): the batched step is
  ~130 ms but the per-stream period is 152 ms "with the prefill interference", so ~20 ms per step
  is lost to prefill and scheduling around the step, not inside it.
- **Here.** Once the verify is one persistent kernel over the concatenated rows of two sessions,
  the prefill's chunks become instructions too, scheduled into the SMs the weight stream does not
  need, and the per-stream period approaches the step. This is the largest exact lever for the
  two-stream gate (34.3 vs production's 33.9 tok/s per stream today) and worth ~10% there; it does
  nothing for the single stream.
- **Decides.** Per-stream period versus batched step on the seeded 2-stream replay.

### 2.12 What does not help, measured by others or here

- **Longer draft blocks:** B31 measured block 16 at ~1% (closed).
- **Tensor cores versus CUDA cores for the matvec part:** Hazy found tensor cores "help marginally
  on Blackwell"; the engine's verify at 13–26 rows already uses block-scaled `mma.sync`, and that is
  the right call at M ≥ 13.
- **Staging weights in shared memory** when each byte is used once (the worklog kernel: nothing to
  reuse at M = 1; at M = 13 the reuse is the 13 rows, already captured by the tile).
- **Dependency-affinity placement** of consumers next to producers: Cohere measured −1–2%.
- **Pipeline depth beyond 3–4 stages:** FlashFormer, no gain.
- **Flash-decoding's merge** at short contexts: a flat merge tax; the engine's agent contexts are
  long enough that the split-KV path is right, but the combine's cost should be in the timings.
- **Saving KV bytes by sharing across GQA heads when SM parallelism is the scarce resource:** the
  worklog kernel measured 0.88–0.95× and reverted. On 48 SMs parallelism is scarce; check before
  any such trade.

## 3. Expected cumulative effect

| Stage | Step, 1 stream | Accepted / step | tok/s | Class |
| --- | --- | --- | --- | --- |
| Today (graphs, PDL, B16, commit overlap) | ~101 ms | ~5 | ~49 | measured |
| First megakernel build (bubbles, ramps, tails) | ~92–95 | 5 | ~53–54 | exact |
| + 2.1 schedule search, 2.2 chunked deps, 2.3 cheap sync | ~88–91 | 5 | ~56 | exact |
| + 2.4 overlap of latency-bound work, 2.6 one partitioning | ~84–87 | 5 | ~59 | exact |
| + 2.8 cluster reductions | ~83–85 | 5 | ~60 | exact if order preserved |
| + 2.7 FP8 KV | ~79–81 | 5 | ~63 | non-exact, quality gate |
| + 2.7 NVFP4 projections where the gate passes | ~68–72 | 5 | ~71 | non-exact, quality gate |
| + acceptance work (re-quantized drafter, gated copy proposer) | same | ~6–6.5 | 85–95 | separate programme |

The exact programme is worth about 20% on the step (101 → ~84); the byte levers another 15–20%
behind quality gates; acceptance multiplies both. None of it reaches the 59 ms weight-only floor,
and none of it needs to: at ~70 ms and 6.5 accepted the single stream is at ~90 tok/s, which is the
figure the owner should hold in mind for "the end of the road" on this model and this box.

## 4. The order, and the loop that runs it

1. **Instrument before optimising.** The timings store, the barrier-check mode and the per-SM idle
   view from the first note are the optimiser's inputs; without them every lever above is a guess.
2. **2.3 then 2.2** (cheap sync, then chunked dependencies): the second multiplies the number of
   waits, so the first must come first.
3. **2.1 as a loop, not a task.** The schedule search runs unattended on node-b like the
   step-savings items do today: propose a validated schedule, replay a fixed seeded window at three
   context buckets, keep it if bit-identical and faster at 1 and 2 streams, log the per-SM timeline.
   ForgeMegakernel's "progressive milestones" and AutoMegaKernel's search loop are this pattern; the
   exactness gate is what makes it safe to leave running.
4. **2.4 and 2.6** are generator rules (which SMs stream, recompute under 10 KB) and belong in the
   search space of 3 once they exist.
5. **2.8** after a DSMEM microbenchmark on the GB10 says what a cluster-4 reduce costs here.
6. **2.7** runs in parallel with all of the above; it is a quality programme with its own gates.
7. **2.11** when the two-stream work reaches the kernel; **2.9** last or never.

## Sources

- Hazy Research, "Look Ma, No Bubbles!" (2025-05): https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles
- Hazy Research, "How Many Llamas Can Dance in the Span of a Kernel?" and "We Bought the Whole GPU, So We're Damn Well Going to Use the Whole GPU" (2025-09): https://hazyresearch.stanford.edu/blog/2025-09-28-tp-llama-intro , https://hazyresearch.stanford.edu/blog/2025-09-28-tp-llama-main
- Cohere, "Megakernels" (2026-09): https://cohere.com/blog/megakernels
- FlashFormer: Whole-Model Kernels for Efficient Low-Batch Inference: https://arxiv.org/abs/2505.22758
- ClusterFusion (NeurIPS 2025): https://arxiv.org/abs/2508.18850
- SwiftSpec: Ultra-Low Latency LLM Decoding by Scaling Asynchronous Speculative Decoding: https://arxiv.org/abs/2506.11309
- Ada-MK: Adaptive MegaKernel Optimization via Automated DAG-based Search: https://arxiv.org/abs/2605.11581
- Event Tensor: A Unified Abstraction for Compiling Dynamic Megakernel (MLSys 2026): https://arxiv.org/abs/2604.13327
- AutoMegaKernel: https://arxiv.org/abs/2606.09682 ; ForgeMegakernel: https://arxiv.org/abs/2609.12379 ; MPK (OSDI '26): https://arxiv.org/abs/2512.22219 ; MonoMoE: https://arxiv.org/abs/2609.04244
- Dissecting GPU Utilization for LLM Inference on Nvidia Hopper: https://arxiv.org/abs/2609.12923
- A decode megakernel worklog (28-layer decoder, the measured wins and the 1.65× gap to graphs): https://emre570.bearblog.dev/megakernel-decode/
- ling-engine: `reports/micro-2026-10-10.md` (bus, SM-subset saturation, sm_121 features incl. clusters of 8 with DSMEM), `reports/M3.md` §5, `specs/DREAMFERENCE_LING_ENGINE_RESEARCH.md` §16.7–16.8, `specs/DREAMFERENCE_LING_ENGINE_MODEL.md` (bytes per pass), `src/core/stream_gemm.cu` (`PF` chunks in flight).
