# A megakernel for ling-engine that stays debuggable, testable and manageable

Research note, 2026-10-14. Scope: how the published megakernels are built and verified, what one
would buy ling-engine's decode step on the GB10, and the design rules that keep such a kernel
testable. It extends [ling-engine RESEARCH §16.7](../../ling-engine/specs/DREAMFERENCE_LING_ENGINE_RESEARCH.md)
and backlog item B32 (`reports/backlog-2026-10-09.md`), and changes neither.

## 1. Verdict

1. **The gain is smaller than the literature's headlines suggest, because ling-engine already has
   what those kernels win against.** The published 1.5–2.5× results are against vLLM and SGLang
   launching 280–450 kernels per token with no graph and reaching 39–50% of the bus; ling-engine
   runs its 333 kernels inside one CUDA graph with programmatic dependent launch and streams weights
   at 233–244 GB/s of the box's ~273 GB/s (85–89%). Cohere's megakernel reaches 62% of its speed of
   light, Hazy Research's 78%, ForgeMegakernel's 51–86%. The engine's own estimate for a persistent
   step is **3–6%** (§16.7: 1–2% launch and ramp gaps, 2–4% overlap of the non-streaming work),
   and the bandwidth floor bounds everything: 16 GB of weights plus the attention's KV at 273 GB/s
   is about 65–70 ms against today's ~101 ms. A megakernel is the way to approach that floor, not a
   way past it, and the numbers in §3 say the honest expectation is a step of **90–95 ms**, not 70.
2. **Testability is a design choice, not a tool.** None of the four published systems describes a
   test method beyond end-to-end comparison; the one that mentions it says the hard part is that "a
   miscounted barrier can cause deadlock much later" and that "barrier accounting needs explicit
   invariants and careful testing" (Cohere). The two newest papers do add the right ideas: a *static
   validator* of the schedule that certifies deadlock- and race-freedom before launch
   (AutoMegaKernel: 7,160 adversarial schedules, 6,091 unsafe, zero false accepts) and a *mid-state
   oracle* that derives every intermediate tensor and checks it during generation (ForgeMegakernel).
   Both fit ling-engine's exactness policy (bit-identical, per module, never perplexity).
3. **Build it as "one kernel body, three launchers"** (§4): every instruction is an existing kernel
   body, so the eager path, the graph path and the megakernel compute the same bits and the graph
   path stays the reference, the fallback and the bisection tool. The schedule is data produced on
   the host and validated on the CPU; the kernel is an interpreter over it with a deadline on every
   wait, so a bug is a diagnostic record, never a hung GPU on a unified-memory host.
4. **Order:** B32's two-kernel toy first (one afternoon) settles whether the layer-boundary bubble is
   worth anything; then one layer, one DeltaNet repeat, the verify pass, the draft and the commit,
   each gated by the same exactness tests the graph path passes.

## 2. What has been built, and how each one is verified

| System | Hardware, model | Structure | Sync | Verification described | Result |
| --- | --- | --- | --- | --- | --- |
| Hazy Research "No bubbles" (2025-05) | H100/B200, Llama-3.2-1B BF16, batch 1 | on-device interpreter, 7 instruction types on one load/compute/store/finish template, per-SM instruction streams built ahead in Python, paged shared memory (13 × 16 KiB) | counter array in global memory, `__threadfence` + `atomicAdd`, consumers spin with `__nanosleep(20)`; MLP hidden split in 4 chunks with 4 counters | none in the post; repo has `scripts/diff_test.py`, `controller/timings_store.cuh`, a Python VM that executes the same instruction stream on the CPU | 78% of H100 bandwidth, <680 µs/pass on B200; 2.5× vLLM, 1.5× SGLang |
| Hazy Research TP-Llama (2025-09) | 8 GPUs, 70B | same interpreter; large instructions (tens of µs) to amortise dispatch; cross-SM work fetched dynamically ("SMs fetch new work when they're done") | as above, plus direct remote loads/stores | none | +22% over SGLang end to end |
| Cohere (2026-09) | H100, 30B MoE 3.3B active, BF16 | "a single CUDA file", one block per SM, 16 opcodes, 32-int task descriptors dealt round-robin into per-SM lists; attention and MoE from shared work queues claimed atomically; 3 warp groups (producer, consumers, storer, controller) | "plain integers in global memory, zeroed before each step"; fine-grained (O-proj per KV group) | end to end on benchmarks; standalone GEMM and attention "match cuBLAS and FlashAttention-3"; explicit warning about barrier accounting | 292 vs 185 tok/s (62% vs 39% of speed of light), 1.58× at batch 1 |
| MPK (OSDI '26) | A100/H100/B200, five models | compiler partitions operators into SM-sized tasks (352-byte descriptors); in-kernel runtime: 4 SMs run scheduler warps, the rest are workers with FIFO queues; events with trigger counts | semaphores in device memory; JIT dispatch needs two handoffs, AOT one | none described | 1.0–1.7× vs SGLang/vLLM; Qwen3-8B 14.5 → 12.5 ms against a ~10 ms floor |
| ForgeMegakernel (2026-09) | 8 model families 0.6–13B | agents generate a per-model kernel along "ten progressive milestones"; per-SM instruction stream, dependency counters, shared-memory buffer pool | counters | **mid-state test oracle**: derives the intermediate states and checks error, precision and speed during generation | 50–86% MBU; 1.21× over SGLang 0.5.18, 1.54× over a megakernel compiler |
| AutoMegaKernel (2026-06) | L4/L40S/A10G/RTX 5090 (sm_80/90/120), ≤1.1B | agent proposes a schedule in an IR; a **frozen validator "statically certifies deadlock-freedom and race-freedom via static graph checks"** before launch | counters | validator tested on 7,160 adversarial schedules, zero false accepts; output "reproduces HuggingFace greedy decode token-for-token" | 1.08–1.33× over graphed cuBLAS on inference-class GPUs; **slower than cuBLAS on A100/H100**, where it "localizes the cross-SM-sync bottleneck" |
| MonoMoE (2026-09) | quantized MoE decode in vLLM | weight-major persistent kernel, readiness flags, warp specialisation | flags | not described | integrated in vLLM across TP configs |

Three facts matter for ling-engine:

- **Every system pays for synchronisation.** Hazy's B200 breakdown of a 600 µs pass has 250 µs in
  "storing, consistency waits, and loading activations" and 40 µs of warp-level synchronisation;
  asynchronous barriers cost ~60 ns each even when passed. AutoMegaKernel *loses* to graphed cuBLAS
  on the high-bandwidth GPUs because cross-SM sync, not bandwidth, is the bottleneck there. The GB10
  is a 48-SM, 273 GB/s part: closer to the inference-class GPUs where megakernels win, which is the
  one encouraging sign.
- **The schedule is where bugs live, not the arithmetic.** Cohere's warning, Hazy's note that
  "missing fences cause silent corruption" that "looks like a bad quantization bug", and
  AutoMegaKernel's 6,091 unsafe schedules all point at the same place. Arithmetic bodies are tested
  the ordinary way; the dependency graph needs its own tests.
- **Static schedules suit a fixed-shape engine.** ling-compile knows every shape; the data-dependent
  parts of a step are the attention's key ranges (the graph path already launches the full
  `max_context` grid and lets empty ranges exit) and the accepted count (one commit graph per
  count). Hazy and Cohere keep the schedule static and give only attention and MoE a shared queue.

## 3. What it would buy ling-engine's step

The step breakdown at ~35K context with graphs on (`reports/M3.md` §5, GPU time per one-row step):
NVFP4 and FP8 GEMMs 89.1 ms, attention 11.2, DeltaNet recurrence, conv and gating 3.3, the
drafter's GEMMs and attention ~7–10, projections, norms, conversions and top-k ~4, idle ≤ 1 (the
B08 gate, "as production has"). The step-savings items since (B16, commit overlap) took it to ~101 ms
for 1 stream and ~146 ms per batched step at 2 streams.

What a megakernel can and cannot recover:

| Component | Today | Megakernel lever | Bound |
| --- | --- | --- | --- |
| Weight GEMMs | 89 ms at 233–244 GB/s | next layer's weight stream starts during this layer's reduction and norm; no ramp per kernel | 16 GB / 273 GB/s = 59 ms is the floor; realistic 80–85 ms (90–93% of bus, Hazy's 78% was on HBM with a 1B model) |
| Attention | 11 ms, ~205 GB/s, 10–20% short of FlashInfer | runs on a subset of SMs while others stream FFN weights (needs the rest to saturate the bus alone, §16.7's condition 16.1(2)) | hidden only if the weight stream saturates with fewer SMs; otherwise B07's split is the lever, not the megakernel |
| DeltaNet, norms, conversions, top-k | ~7 ms, bandwidth-idle | overlap with the weight stream on spare SMs | up to ~5 ms recovered |
| Drafter | ~7–10 ms | **not overlappable**: the draft of step n+1 needs the commit of step n, the verify needs the draft | none (own instruction stream, same bubbles removed) |
| Launch gaps, ramps, tails | ≤ 1 ms idle measured, plus per-kernel ramp inside the kernels | gone | 1–2 ms |

Sum: a step of roughly **90–95 ms from ~101**, i.e. 5–10%, with the drafter and the serial
dependency between draft, verify and commit untouched. That agrees with §16.7's 3–6% plus whatever
the attention overlap yields. It does **not** support a 70–75 ms step: that figure would need the
weight stream at 100% of the bus for the whole step and every non-GEMM millisecond hidden, which no
published megakernel achieves. The parent conversation's estimate should be revised to this range.

Two things decide between 5% and 10% and are cheap to measure before any kernel is written:

1. **The layer-boundary bubble** in an Nsight Systems trace of 60 steps (`bench/profile/step_breakdown.py`,
   the B08 measurement): the sum of gaps and ramps at the 333 boundaries is the launch-side ceiling.
2. **Whether 36 of 48 SMs saturate the bus** on `stream_gemm_nvfp4` (§16.7's 16.1(2), B16's SM
   sweep gives most of this): if they do, the other 12 can hide attention, DeltaNet and norms; if
   not, overlap buys nothing and the megakernel is only the bubble removal.

B32's toy (FFN up then down at M = 16 as one persistent kernel with a per-tile counter, against the
PDL graph of the same two kernels) measures both the bubble and the counter cost on this exact
hardware in an afternoon, and is the right first step.

## 4. The design that stays testable

### 4.1 One kernel body, three launchers

Every instruction type wraps an **existing** kernel body from `src/core/kernels.cuh`,
`stream_gemm.cu` and `draft_kernels.cu`: the body is a `__device__` function of (tile coordinates,
pointers, shape); the eager launcher, the graph node and the megakernel's interpreter call the same
function with the same tile decomposition and the same reduction order. Consequences:

- The rows path's **row invariance and bit-identity carry over by construction**, so the exactness
  gate is "megakernel output == graph output, `memcmp`", exactly as `--graph-check` compares graphs
  with eager today. No tolerance anywhere.
- The per-kernel tests in `tests/kernel_tests.cu` and `tests/stream_tests.cu` (host reference,
  every row count, `memcmp`) remain the unit tests of each instruction's arithmetic. New tests cover
  only the interpreter and the schedule.
- Fusions that change arithmetic order (split-K across SMs, a different reduction tree, norm folded
  into a GEMM epilogue with a different accumulation) are **non-exact modes** under the engine's
  exactness policy: behind a switch, off by default, and only promotable through §13's gates. The
  first megakernel fuses nothing new; it schedules the existing bodies.

### 4.2 The schedule is data, built on the host, validated on the CPU

The host (ling-compile, where every shape is known) emits per-SM instruction streams: a fixed-size
descriptor per instruction (opcode, tile coordinates, pointer indices into a table, the counters it
waits on with their target values, the counter it increments). Like Hazy and Cohere, the stream is
static for a given (context length bucket, batch, accepted count); like MPK's AOT mode, no in-kernel
scheduler. Attention gets the one dynamic element: a shared work queue over key ranges, claimed
atomically, because its range count varies with the context.

A **static validator** runs on the host before any launch and in CI without a GPU (AutoMegaKernel's
idea, trivial to write for a static DAG):

- the dependency graph is acyclic and every wait names a counter that some earlier instruction, on
  some SM, increments to at least the target (deadlock-freedom);
- no two instructions write the same bytes without an ordering edge, and every read of a tile is
  ordered after its last write (race-freedom), computed from the tile footprints the descriptor
  declares;
- the shared-memory page budget and the register budget hold for every SM's stream;
- the counter targets equal the number of producers, exactly (Cohere's "barrier accounting"), and
  each counter is used in one step only (zeroed per step).

The validator is tested the way AutoMegaKernel tested theirs: a generator mutates valid schedules
(drop an edge, miscount a target, swap two tiles) and the validator must reject every mutant.

### 4.3 A CPU interpreter as the mid-state oracle

The same descriptor stream runs on the CPU against the host reference bodies that
`kernel_tests.cu` already has (Hazy's `python_vm.py` is this). Its outputs after every instruction
are the **mid-states** (ForgeMegakernel). A debug build of the megakernel dumps the same tiles into
a side buffer at instruction boundaries; the test compares them in schedule order and names the
**first divergent instruction**. For the rows path this is an exact compare. This turns the worst
megakernel bug, silent garbage from a missing fence, into "instruction 1,742 on SM 17 read tile X
before counter Y reached 3", because the dumped tile differs from the oracle's while every input
tile agrees.

### 4.4 In-kernel instrumentation, always compiled, mostly off

- **Deadline on every wait.** A spin on a counter has a bound in `globaltimer` ticks (configurable,
  default 50 ms). On expiry the waiting block writes a diagnostic record {SM, instruction index,
  counter, observed, expected, timestamp} to a fixed slot and **exits the kernel**; the host sees the
  record and reports it. A persistent kernel that hangs on a unified-memory GB10 is a host-safety
  incident (docs/dev/host-safety.md), so this is mandatory, not a debug option.
- **Timings store** (Hazy's `timings_store.cuh`): per instruction, start and end `globaltimer` and
  the SM id, in a per-SM ring buffer, enabled by a kernel argument. `bench/profile` renders it as a
  per-SM timeline next to the Nsight trace; the layer-boundary bubble and the attention/weight
  overlap are then measured from inside, per instruction, not inferred from kernel boundaries.
- **Barrier-check mode**: each instruction re-reads its wait counters after its loads with a fence
  and records a violation if any is below target; cheap enough to leave on in the exactness suite.
- **Serial mode**: the interpreter runs the whole stream on one SM, in schedule order, no spinning.
  If serial output equals the graph path and parallel output does not, the bug is in
  synchronisation; if serial already differs, it is in a body or a tile mapping. This halves every
  investigation at the start.
- **Hybrid bisection**: run the megakernel for the first K instructions, hand the activations to the
  graph path for the rest (both paths share buffers), compare the step's output; binary search on K
  finds the first bad instruction without the oracle when the oracle is too slow for a full step.

### 4.5 Tooling that already exists

- `compute-sanitizer --tool racecheck` and `synccheck` on the B32 toy and on one-layer streams (the
  full step is too slow under racecheck; the toy and one layer are where the sync patterns are
  introduced). `memcheck` on the full step in the debug build, PDL off, as M3 §6 did.
- `cuda-gdb` on serial mode for a single instruction, with the deadline disabled.
- Nsight Compute on the persistent kernel gives one number per step (`dram__bytes_read`); the
  timings store gives the per-instruction view Nsight cannot.

### 4.6 Manageability rules

- **One file per instruction type** (Hazy's `demos/low-latency-llama/*.cu`), one header for the
  interpreter and the ABI (warp roles, page allocator, counters), one host module for the schedule
  generator and the validator. Never Cohere's single file: it works for a one-model demo and defeats
  review.
- **The worst instruction sets the kernel's registers and occupancy.** A CI check reads the register
  count per instruction body (`cuobjdump --dump-resource-usage` on the debug object, or
  `__launch_bounds__` with a fixed budget) and fails when an instruction exceeds the budget the NVFP4
  GEMM body sets. Big-tile dequant GEMMs and the attention are the usual offenders.
- **Descriptors are versioned and dumpable** (a `--mk-dump-schedule` flag writes the stream as text),
  so a schedule is diffable in review and a regression in step time can be bisected to a schedule
  change versus a body change.
- **The graph path is never removed.** It is the reference for every exactness test, the fallback
  when the validator rejects a schedule (e.g. a context bucket the generator did not anticipate),
  and the path the debug build uses by default. The build rules in §16.7 already say so.
- **Fixed shapes, fixed streams.** One stream per (context bucket, batch size ∈ {1, 2}, accepted
  count for the commit), generated at startup like the commit graphs today; no JIT in the kernel.

## 5. Risks specific to ling-engine

- **Serial dependency of the speculative step.** Draft → verify → accept (host) → commit → draft is a
  chain; a megakernel removes bubbles inside each part and can overlap the commit's DeltaNet replay
  with the next draft (the commit-overlap item already does this on a second stream), but the
  drafter's ~7–10 ms stays on the critical path. Any plan that counts it as hidden is wrong.
- **Two host decisions per step** (the lattice walk and the accept rule) end each GPU part today; a
  single persistent kernel for the whole step would need them on the device. Keep three persistent
  kernels (draft, verify, commit), one per graph today, and move the decisions later if ever.
- **Exactness versus overlap.** Overlapping the attention with FFN weight streaming changes nothing
  arithmetically; fusing the gated norm into the GEMM prologue does not either if the norm's
  reduction stays per row in the same order. Anything that changes a reduction order is a non-exact
  mode. The validator cannot see this; review and the exactness suite must.
- **The 48-SM wave tail.** A GEMM with 5120 × 10,240 output tiles divides unevenly over 48 SMs; the
  graph path pays this per kernel, the megakernel can fill the tail with the next instruction, which
  is most of the 1–2% launch-side gain. Tile sizes are chosen per instruction, not per kernel, so the
  schedule generator should expose them.
- **Unified memory.** A runaway persistent kernel cannot be killed by the PSI watchdog the way a
  container can; the in-kernel deadline (4.4) and a host-side `cudaEvent` timeout that resets the
  context are the two layers, mirroring `check_host_safety` plus the watchdog around a model load.

## 6. Recommendation

Adopt the staging below, each stage gated by "bit-identical to the graph path on `--spec-check`,
`--graph-check` and `--batch-check`, and no slower at 1 and 2 streams on seeded replays", the
engine's standing rule. Stop at the first stage whose measured gain is below its maintenance cost.

| Stage | Scope | Settles | Effort |
| --- | --- | --- | --- |
| 0 | B08 measurement: boundary bubbles and SM-subset bandwidth from the trace | the ceiling (§3) | hours |
| 1 | B32 toy: FFN up+down at M = 16 as one persistent kernel with per-tile counters, with the validator, the deadline and the timings store from day one | counter cost and bubble gain on the GB10 | 1 day |
| 2 | one full layer (GDN or attention block) in the verify pass, oracle and serial mode working | the ABI, the page allocator, the test harness | 1 week |
| 3 | one repeat (3 GDN + 1 attention) with attention on a work queue overlapping FFN weights | whether overlap pays (the 2–4%) | 1 week |
| 4 | the whole verify pass as one kernel; draft and commit stay graphs | most of the gain | 1–2 weeks |
| 5 | draft and commit as persistent kernels; batch of two | the rest | 1 week |

The expected result of the full programme is a step in the low 90s of milliseconds from ~101, worth
about 5–10% in tokens per second; it compounds with the acceptance work (the better-quantised
drafter, the copy proposer), which remains the larger lever. The programme is also where the owner's
C++ fluency matters most: the interpreter, the ABI and the validator are the design; the instruction
bodies already exist.

## Sources

- Hazy Research, "Look Ma, No Bubbles! Designing a Low-Latency Megakernel for Llama-1B" (2025-05): https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles ; repository https://github.com/HazyResearch/Megakernels (`include/controller/timings_store.cuh`, `megakernels/python_vm.py`, `megakernels/scripts/diff_test.py`)
- Hazy Research, "How Many Llamas Can Dance in the Span of a Kernel?" (2025-09): https://hazyresearch.stanford.edu/blog/2025-09-28-tp-llama-intro
- Cohere, "Megakernels" (2026-09): https://cohere.com/blog/megakernels
- MPK: A Compiler and Runtime for Mega-Kernelizing Tensor Programs (OSDI '26): https://arxiv.org/abs/2512.22219
- ForgeMegakernel: A General Framework for Efficient Auto-Regressive Model Decode Megakernels: https://arxiv.org/abs/2609.12379
- AutoMegaKernel: A Statically-Checked Agent Harness for Self-Retargeting Megakernel Synthesis: https://arxiv.org/abs/2606.09682
- MonoMoE: An Efficient Fused Mega-kernel for Quantized MoE Decoding: https://arxiv.org/abs/2609.04244
- NVIDIA Compute Sanitizer (memcheck, racecheck, initcheck, synccheck): https://docs.nvidia.com/cuda/compute-sanitizer/
- ling-engine: `specs/DREAMFERENCE_LING_ENGINE_RESEARCH.md` §16.7, `reports/backlog-2026-10-09.md` B08/B32/L2, `reports/M3.md` §5, `specs/DREAMFERENCE_LING_ENGINE_MILESTONES.md` exactness policy, `src/core/speculate.cpp` header comment.
