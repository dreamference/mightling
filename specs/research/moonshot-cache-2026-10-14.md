# Moonshot: caches, from the GPU's L2 to the agent's prefix (research note, 2026-10-14)

Part of the ling-engine working notes. Question from the owner: how can GPU and CPU caches be used to make ling-engine faster? This note separates the three things "cache" means here, sizes each against the measured step, and ends with a ranked list and the cheap experiments that settle the open numbers. Facts about the hardware were read from the machine on 2026-10-14 (second-puffin, CUDA 13.0.88, `cudaDeviceGetAttribute`, `lscpu -C`); facts about the engine are from `reports/M1.md`, `reports/M3.md`, `reports/micro-2026-10-10.md`, `specs/DREAMFERENCE_LING_ENGINE_MODEL.md` §5, `specs/DREAMFERENCE_LING_ENGINE_RESEARCH.md` §16.8–16.10, `specs/DREAMFERENCE_LING_ENGINE_SESSIONS.md` §11 and `specs/drafts/M4-sessions-and-batches-2026-10-10.md`. Estimates are marked as such.

## 1. The hardware, as measured

| Level | Size | Latency / bandwidth | Note |
| --- | --- | --- | --- |
| GPU shared memory + L1 | 100 KB per SM, 99 KB per block opt-in; 48 SMs | — | `micro-2026-10-10` §3 |
| GPU L2 | 25,165,824 B (24 MiB) | 136 ns; the bus delivers ~103 KB in flight chip-wide | `cudaDevAttrL2CacheSize`; `micro` §1 |
| Persisting L2 set-aside | up to 18,874,368 B (18 MiB); a request for 8 MiB gives 9 MiB (granularity) | — | `cudaDevAttrMaxPersistingL2CacheSize`, `cudaLimitPersistingL2CacheSize`: **supported on sm_121** |
| Access-policy window | up to 134,217,728 B (128 MiB) per window | — | `cudaDevAttrMaxAccessPolicyWindowSize` |
| Unified LPDDR5X | 128 GB, 256-bit, 8,533 MHz | ~273 GB/s peak, 257 measured; 395–402 ns to DRAM | page switches cost more than on HBM |
| GPU page tables | the GPU can use the host's page tables for pageable memory | — | `PageableMemoryAccessUsesHostPageTables = 1`; `cudaMalloc` memory keeps its own 2 MiB mappings |
| CPU | 20 cores (Cortex-X925 and A725 clusters) | — | one socket, one NUMA node |
| CPU L1 | 64 KB data + 64 KB instruction per core | — | 64-byte lines everywhere |
| CPU L2 | 512 KB per core as `lscpu` reports it (the porting guide gives 2 MB on the X925 cores) | — | private |
| CPU L3 | 3 × 8 MB = 24 MB total | — | shared per cluster, not across the chip |
| Host huge pages | THP `madvise` only; 2 MiB `Hugepagesize`; none reserved | — | relevant to any system-allocated memory the GPU touches |

Two consequences frame everything below. The GPU's L2 is 24 MiB against 17.6 GB of weights read every step: weights can never be cached, only streamed well. And CPU and GPU share one bus, so the CPU's cache misses are the GPU's lost bandwidth.

## 2. Where a step's bytes go, and what could live in a cache

Per decode step at the workload's median 25.7k-token context, single stream (`MODEL` §5, FP8 projections kept):

| Item | GB per step | Fits in L2? | Today |
| --- | --- | --- | --- |
| Target weights | 17.6 | no (700×) | streamed at 233–244 GB/s (B16) |
| Drafter layers | 1.55 | no | streamed once per step (stage 4: once for both streams) |
| Drafter head (32k tokens, FP8) | 0.16 | no (7×) | streamed |
| KV read (FP8, 16 attention layers) | 0.82 | no (34×) | tiles prefetched two ahead into L2 (M3: −12% per attention layer) |
| DeltaNet state, one FP32 read and write | 0.30 | **per layer yes**: 3.1 MB per layer, 151 MB in all | read and written through L2 beside the weight stream |
| Conv window | 3 MB | yes | — |
| Activations between kernels | ≤ 5,120 × rows × 2 B ≈ 0.2–0.4 MB per hop | yes | written and re-read through L2 within a layer |
| Drafter feature cache (per session) | small | yes | resident in the session slot |
| **Total** | **20.4** | | 83 ms at 245 GB/s; the step is ~101 ms |

So about 98.5% of the step's bytes are a stream that no cache can shorten, and about 1.5% (the state round trip and the activations) is traffic that L2 could absorb if the stream did not evict it. That bounds lever A below.

## 3. Lever A: the GPU's L2, used as a cache for state instead of as a buffer for the stream

**What goes wrong today (hypothesis, to measure).** The weight stream passes through L2 with the default allocate-on-miss policy. Each GEMM's 100–400 MB of weights sweep the 24 MiB L2 several times over, so the layer's DeltaNet state slot (3.1 MB), the conv window and the activations written a few microseconds earlier are evicted before the next kernel reads them, and come back from DRAM at 400 ns and full bus cost.

**Two mechanisms, both available on sm_121:**

1. **Cache hints on the stream** (RESEARCH §16.8, "Keep L2 for state", planned, not yet built). Loads of weight tiles carry `L2::evict_first` (or a `createpolicy` fractional evict-first policy on `cp.async.bulk`/TMA), so a streamed line is the first candidate for eviction and the state, the activations and the prefetched KV tiles stay. Exact by construction: hints move data, never values.
2. **A persisting set-aside with an access-policy window** on the current layer's state slot and activation scratch. Measured today: a set-aside of up to 18 MiB exists, and a window may cover 128 MiB. In a CUDA graph the window is a kernel-node attribute (`cudaKernelNodeAttributeAccessPolicyWindow`), so each layer's kernels can pin that layer's 3.1 MB slot; the remaining L2 keeps serving the stream, which does not need it.

**Expected gain: 1–2% of the step (estimate).** The ceiling is the 0.3 GB state round trip plus the activation re-reads, about 1.5% of the bytes, and the DRAM latency the state kernels now pay on their first touch. The attention's L2 prefetch already captured the biggest L2 win in M3 (−12% per attention layer); the remaining state traffic is small by design (RESEARCH §16.11: 0.3 GB, under 2%). The gain grows with batch size (stage 4's batched step moves two states) and with the sparse head (item 5), whose top-K codebooks want to be L2-resident.

**Cost and risk.** Hints are a few lines per load in `stream_gemm_*` and the TMA descriptors; the set-aside is one host call and a node attribute per layer. The set-aside interacts with graph capture (attributes must be set on the captured nodes) and shrinks the L2 available to the attention prefetch, so measure the attention layers with and without it.

## 4. Lever B: shared memory, L1 and clusters

These are not caches the engine can "use more"; the rows path already keeps the whole activation of a ≤ 16-row step in shared memory and reads each weight byte once. Two cache-adjacent levers remain, both from RESEARCH §16.8:

- **Clusters with distributed shared memory for the split-K reductions.** The three 5,120-output projections give only 40 tiles for 48 SMs; splitting K and reducing the partial sums through DSMEM (ClusterFusion, 1.61× end to end on H100 for the whole method) keeps the partials on chip instead of in L2 or global atomics. `micro` §3 confirmed clusters of up to 8 blocks launch on sm_121. The gain is in the under-filled waves, not in cache traffic as such; B16 already recovered the 17–32-row loss, so what remains is the 40-of-48 under-fill on three matrices per layer.
- **Scales beside their values** so one TMA request fetches both and the DRAM opens fewer pages: fewer LPDDR5X page switches, which cost more than on HBM. B26 (6-bit scale codes) is the current form of this item.

Expected together: low single-digit percent (estimate), already on the step-savings list; nothing new from the cache angle.

## 5. Lever C: the CPU's caches, and the bus it shares

The engine's host work per step is microseconds by rule (§16.9, M0 §1): one engine thread, no per-step allocation, pinned. So the CPU's own caches are not on the engine's critical path. They matter in three indirect ways, and the third is the one with real numbers.

1. **The matcher and the suffix store.** The lookup matcher is a 4-byte-per-token array, 100 KB at 25.7k tokens: it fits one core's L2 and costs nothing. A cross-session suffix tree (SuffixDecoding, §16.12; up to 5.3× on SWE-bench agent traces against a slow baseline) would be tens of MB. Size it to stay inside one cluster's 8 MB L3, or its walks become DRAM traffic taken from the GPU's 273 GB/s during the very step they are meant to speed up. A capped, per-repository store that is rebuilt from the session history is the right shape; this is the cache half of the moonshot proposer (P1) and the only cache item in this note with a path to double-digit gains, through acceptance rather than bytes.
2. **Page tables and TLB reach.** The GPU streams 17.6 GB per step: at 2 MiB pages that is 8,800 translations per step, at 4 KiB pages 4.5 million, which no TLB survives. `cudaMalloc` memory on GB10 uses 2 MiB mappings by default, so the weights, the KV pool and the state pool are fine as the §16.9 allocation rule places them. The attribute `PageableMemoryAccessUsesHostPageTables = 1` means any system-allocated memory the GPU touches is translated through the host's tables, where THP is `madvise`-only on this host: such buffers must be `madvise(MADV_HUGEPAGE)` or explicit huge pages, or they are 4 KiB pages. Audit: list every allocation the GPU reads during a step and confirm none is system-allocated; the weights' load-time `mmap` is fine because it is read once.
3. **CPU traffic steals bus bandwidth.** Decode is bound by the 273 GB/s bus, and every byte the CPU misses in its caches is read from the same bus. On an Apple unified-memory system, placement aware of the shared memory alone gave 1.29× (§16.9). The numbers that matter are Mightling's, not the engine's: the code index and the local file index build beside a resident server, Night Shift runs beside it, and `pytest` in a SWE-bench container streams gigabytes. The measurement is M0's "decode step with a CPU memory stream beside it", which the reports do not show as done. If a 10 GB/s CPU stream costs the step 4%, bandwidth-aware admission (count bandwidth, not only memory: §16.9) is worth building; if it costs 0.5%, it is not.

Expected: item 3 is unknown and cheap to measure; items 1 and 2 are design rules, not gains.

## 6. Lever D: the software caches, where the time actually is

Measured in seconds, not milliseconds, and all exact:

| Cache | Measured loss today | Fix | Status |
| --- | --- | --- | --- |
| Shared-prefix checkpoint store (engine) | production recomputed a median 14.7k tokens in 143 of 168 sessions: 40% of all prefill, 9–13 s before the first answer (SESSIONS §11) | a DeltaNet checkpoint at the shared prefix's end plus the KV pages, so a new session costs one pass plus its tokens | built, M4 stage 1 (`--prefix-check` passes) |
| The prefix cache under compaction (Codex side) | every compaction request missed the server's prefix cache: 62–91K tokens re-read at ~1,350 tok/s, 47–75 s each, on turn-end and mid-turn compactions alike (bench 2026-10-13) | Codex patch 0027: the compaction request carries the tools so the prompt extends the cached one | built, benched tonight (v2) |
| Resident sessions (engine) | two interleaved agents evict each other's history and re-prefill from the last checkpoint (BACKLOG item 8) | a 30 GB session pool holds ~30 sessions at 1 GB each, LRU, eviction is freeing | M4 stage 5 |
| KV precision (engine) | 0.82 GB per step at 25.7k context in FP8; ~3.3 GB at 100k | NVFP4 KV (BitDecoding, §16.10): halves the KV read; 2% of the step at 25.7k, 8–10% at 100k | M5, behind the quality gate |
| Cross-session suffix store (engine + Mightling) | 46% of visible tokens continue a match in the agent's own context; copied spans accept 8–11 of 64 against ~5 (moonshot P0) | SuffixDecoding-style store feeding the gated proposer | P1, next GPU slot |

One compaction's cache miss (47–75 s) equals 500–700 decode steps of the hardware-cache gain in §3. This is why the software caches rank first.

## 7. Ranking

| Lever | Gain | Exact? | Effort | When |
| --- | --- | --- | --- | --- |
| D: compaction request cache-hot (0027) | 47–75 s per compaction, several per long session | yes | done, benching | tonight's verdict |
| D: shared-prefix checkpoints, resident sessions | 9–13 s per new session; no re-prefill between interleaved agents | yes | built / stage 5 | M4 |
| D: cross-session suffix store for the proposer | acceptance on copied spans 5 → 8–11; +9–10% whole-session gated (P0 estimate) | yes (speculation) | weeks | P1 |
| C3: bandwidth-aware admission for sidecars | unknown; 0–5% of the step while an indexer runs (estimate) | yes | a day to measure, a week to build | measure first |
| A: evict-first hints + persisting set-aside for state | 1–2% of the step (estimate); more at batch 2 and with the sparse head | yes | days | after B26 |
| D: NVFP4 KV | 2% at 25.7k, 8–10% at 100k context | no, quality gate | weeks | M5 |
| B: cluster split-K reductions, scales beside values | low single digits, already listed | yes | on the step-savings list | B26 and after |
| C2: page-size audit | a rule, not a gain, unless a 4 KiB mapping is found | yes | hours | now |

## 8. Experiments, cheapest first

1. **E1, the L2 hit rate of the state (hours).** Nsight Compute on one layer of the rows path: `lts__t_sectors_lookup_hit/miss` on the DeltaNet state kernels and the first kernel after each GEMM. If the state's loads already hit, lever A is dead; if they miss, its ceiling is confirmed.
2. **E2, evict-first on the stream (a day).** Add the cache policy to `stream_gemm_*`'s loads and the TMA descriptors behind a switch (`LING_L2_EVICT_FIRST`); re-run E1 and the seeded replay (`--sampling-seed 1013`, outputs and passes identical). Keep if the step is faster at one and two streams.
3. **E3, the set-aside (a day).** `cudaLimitPersistingL2CacheSize` at 4 and 9 MiB, a per-layer window on the state slot set as a kernel-node attribute in the captured graph; measure with and without E2, and the attention layers separately (the prefetch loses L2 to the set-aside).
4. **E4, CPU stream beside decode (hours).** A pinned CPU thread streaming 5, 10 and 20 GB/s while the replay decodes; the step inflation per GB/s is the number Mightling's admission needs. Also run `ling-code`'s indexer and a `pytest` container as the realistic streams.
5. **E5, page-size audit (hours).** `cuMemGetAllocationGranularity` for the pools; `nvidia-smi -q` and `/proc/<pid>/smaps` for any system-allocated buffer the step touches; `madvise(MADV_HUGEPAGE)` where one is found.
6. **E6, the suffix store's size (part of P1).** Build the store from the recorded sessions, measure its size against the 8 MB L3 and its walk cost per step; cap it before it costs bus bandwidth.

## 9. Verdict

The hardware caches are a 1–3% lever on the step, worth E1–E3 because they are exact and cheap, but they are not where the time is: the step is 98.5% a weight stream that no cache shortens, and the engine already keeps its KV tiles and activations in L2 where it counts. The caches that decide the user's experience are the software ones: the prefix the agent shares with itself across compactions and sessions (tens of seconds each, exact, built or benching), and the cross-session store that lifts acceptance on copied text (the moonshot's P1). The one unknown worth a day is the CPU's bus traffic beside decode, because Mightling's indexers and test containers run there all night and the answer decides whether admission needs a bandwidth budget.
