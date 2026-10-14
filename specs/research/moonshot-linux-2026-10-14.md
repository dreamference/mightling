# Moonshot: what Linux can still give ling-engine

*Research note, 2026-10-14. What the operating system, its tuning and its kernel modules can and cannot do for the model server on a GB10. Measured facts are marked with their source; everything else is an estimate to be measured before anything is changed on a production machine.*

## 1. The question and the short answer

ling-engine's decode step is a 16–17 GB weight stream over a 273 GB/s bus with about 35 ms of other work beside it (reports/M3.md §5; specs/drafts/M4-sessions-and-batches-2026-10-10.md). The engine already does what a user-space server can do about the host: one CUDA graph per step with programmatic dependent launch, one engine thread, microseconds of host work per step, `cudaMalloc` for weights, KV and state, no per-step allocations (RESEARCH §16.9, M4 draft "Scheduler"). So the question is whether the layer *below* it, Linux and the NVIDIA driver, holds anything.

The short answer: **no lever worth more than 1–3% of step time, and most of the real value is in not losing speed, not in gaining it.** The GB10's memory bandwidth is fixed (the memory clock is not exposed, let alone adjustable: `nvidia-smi -q -d CLOCK` reports `Memory: N/A`), the GPU is not power- or thermally-limited at decode (44–62 W, 2,405–2,476 MHz, throttle mask 0x0 over ten minutes: reports/micro-2026-10-10.md §4; second-puffin 2026-10-14), and the engine's own CPU thread barely touches memory. What remains is: (a) bus contention from everything *else* on the box, which Linux can bound; (b) wake-up latency on the engine thread between graph replays, which is a few hundred microseconds at most; (c) model load time, which is a one-off; and (d) one kernel-parameter change NVIDIA recommends for coherent systems that DGX OS already applies.

## 2. What the two machines run today (read 2026-10-14, second-puffin; gx10-9428 differs where noted)

| Setting | Value | Note |
| --- | --- | --- |
| Kernel | 7.0.0-1019-nvidia (second-puffin), 6.17.0-1029-nvidia (gx10-9428) | the two GB10s run different kernels |
| Page size | 4 KB | NVIDIA's Grace guide recommends 64 KB; DGX OS ships 4 KB |
| THP | `madvise`, defrag `madvise`, no hugetlbfs pages | the CUDA driver's `cudaMalloc` does not go through THP |
| CPU governor | `performance`, cppc_cpufreq, 2,808 MHz pinned | already optimal |
| CPU idle | acpi_idle, `menu`, LPI-0..3 with exit latencies 0 / 42 / 231 / 433 µs | the engine thread can sleep into a 433 µs state between steps |
| Kernel config | HZ=1000, NO_HZ_FULL built, PREEMPT_DYNAMIC | `nohz_full`/`isolcpus` not on the command line |
| Command line | `init_on_alloc=0 iommu.passthrough=0` | init_on_alloc=0 is the one Grace-guide item that matters, already set |
| IOMMU | SMMUv3, translated, lazy TLB invalidation, 21 groups | passthrough off |
| NUMA | 1 node | nothing to balance; `numa_balancing` moot |
| Swap | 63 GB, 0 used, zswap off, swappiness 60 | host safety's concern, not speed |
| irqbalance | inactive | all IRQs on CPU 0 by default |
| NVIDIA driver | 580.178.04, open kernel modules, persistence mode on | `nvidia_uvm` loaded with prefetch and thrashing mitigation on, HMM disabled |
| Power/clocks | SM 2,457 MHz now; power limit N/A on GB10 | 140 W shared CPU+GPU budget (RESEARCH §16.9) |

Memory bandwidth facts the note relies on (reports/micro-2026-10-10.md §1–2): a read stream reaches 258 GB/s (94.6% of 273) with the production server resident, 263 bare; `cudaMallocManaged` memory streams at 161 GB/s and CPU-first-touch `malloc` memory at 168 GB/s, i.e. 37% slower than `cudaMalloc`; 4 SMs saturate the bus; load-to-use latency 400 ns to DRAM.

## 3. The levers, ranked by expected effect on single-stream decode

### 3.1 Bus contention from other processes: bound it (0–5%, mostly defensive)

Everything on the box shares the 273 GB/s. The resident SGLang server costs 2% of the read stream *while idle* (micro §2). A CPU memory stream beside a decode step is the M0 measurement RESEARCH §16.9 asked for. The things that run beside production in practice: the code indexer and `ling-docs` (bounded by the host-wide memory budget, not by bandwidth), Night Shift's tests, SWE-bench containers, `tracker-miner-fs` (active on gx10-9428, 0 processes on second-puffin), `clamd` on the desktop class of machine, journald, docker's own copies.

What Linux offers:
- **cgroup v2 `memory.high`/`memory.max`** already bounds footprint. Bandwidth has no cgroup controller on arm64; the closest is **MPAM** (Arm's memory partitioning and monitoring), which the Cortex-X925/Grace class supports in hardware and Linux has been merging since 6.x as `resctrl` on arm64. Whether DGX OS's kernel exposes `/sys/fs/resctrl` on GB10 is a one-command check (`mount -t resctrl`); if it does, a bandwidth cap on the indexer/Night Shift cgroup is the only *direct* bandwidth lever Linux has. Expected value: nothing when the box is quiet, up to 5% when an indexer runs beside a user's session.
- **`SCHED_IDLE` and `nice 19` for indexers** limit CPU time, which indirectly limits the bytes they move. Already the cheap, available version of the above; Mightling's admission should also count bandwidth (RESEARCH §16.9), which is a product change, not a kernel one.
- **Stopping `tracker-miner-fs`** on nodes (it indexes the home directory and reads everything the agent writes). Zero risk, measurable only when it fires.

### 3.2 The engine thread's wake-up latency: pin and keep awake (≤1%)

Between two graph replays the engine thread waits on the GPU (event or stream sync), the scheduler puts the core into an idle state, and the wake-up costs up to the state's exit latency: 42, 231 or 433 µs here, against a 101 ms step — 0.4% at worst, per wake-up, if the thread sleeps deep once per step. Options, in increasing invasiveness:
1. **Spin-wait on the completion event** for the last ~1 ms of the expected step (`cudaEventQuery` in a loop), then block. User-space only, no kernel change, keeps the core in LPI-0. Costs one core at ~100% during decode, which the 20-core Grace can spare; power cost small against the 140 W budget but real (RESEARCH §16.9: CPU and GPU share it).
2. **`/dev/cpu_dma_latency`** held open with value 0 by the engine process: tells cpuidle to stay in the shallowest state system-wide while the server runs. One `open()`, no root needed on DGX OS? (it is root-only by default: 0600). Affects every core, so the whole CPU idles hotter.
3. **`isolcpus=` / `nohz_full=` + `SCHED_FIFO`** for the engine thread's core: removes timer ticks and migrations from that core. The kernel has `NO_HZ_FULL` built in but the command line does not use it; this needs a reboot and the owner's sudo. The gain over option 1 is noise-level for a 100 ms step; it matters for sub-millisecond steps, not ours.

Recommendation: option 1, inside the engine, measured with `--spec-check` exactness and the seeded replay (it is a scheduling change, so the outputs are bit-identical by construction). Do not pursue 2–3.

### 3.3 Interrupt placement (≤0.5%)

`irqbalance` is inactive, so the NVIDIA driver's and the NVMe's interrupts land on CPU 0 with everything else. If the engine thread is pinned (it should be, RESEARCH §16.9), pin it away from CPU 0 and leave the IRQs there. One line in the systemd unit (`CPUAffinity=`), no kernel change. Expected effect: removes rare multi-hundred-microsecond preemptions of the engine thread by IRQ handlers.

### 3.4 Allocation and page tables: already right (0%, but worth guarding)

- `cudaMalloc` for every hot buffer is the correct choice on this machine and the engine makes it. The 37% bandwidth penalty of managed or CPU-first-touch memory (micro §2) is the single biggest Linux-adjacent trap, and it is a *mistake to avoid*, not a gain to win: a tensorizer or a loader that leaves weights in `cudaMallocManaged` or in `mmap`ed file pages would cost a third of the bus. A test that asserts the allocation kind of the weight arenas belongs in `ling-stream-tests`.
- `init_on_alloc=0`, which NVIDIA's Grace tuning guide singles out because page zeroing on coherent systems "can cause heavy performance impacts to `cudaMalloc()`", is already on both machines' command lines (DGX OS default).
- **Page size.** The Grace guide recommends a 64 KB kernel page size (fewer faults, better TLB hit rate); DGX OS ships 4 KB and the GPU's own page tables are the driver's business, not the kernel's. For decode, where the GPU walks its own mappings of `cudaMalloc` memory, the host page size does not enter. It would matter for CPU-side mmap of large files (the "very slow mmap on DGX Spark" forum thread is about model loading through the page cache). Not worth a custom kernel.
- **THP.** `madvise` mode is right: the engine can `madvise(MADV_HUGEPAGE)` its few large host buffers (the tokenizer tables, the lookup matcher) if profiling ever shows TLB misses there; today's host work is microseconds, so it won't.

### 3.5 IOMMU passthrough (0–1% on DMA paths, 0% on decode)

`iommu.passthrough=0` means every DMA goes through SMMU translation. The Grace guide says passthrough "might have some performance benefits" for DMA-heavy applications. On GB10 the GPU reaches memory coherently over the on-package fabric, not through PCIe DMA, so decode does not touch the SMMU; NVMe reads (model load) and the network card do. Expect nothing for decode, a few percent on load. Needs a kernel command-line change and a reboot; low priority, and a security trade-off (a translated IOMMU is what keeps a misbehaving device from writing anywhere).

### 3.6 Model load time: the one place where tens of percent exist (one-off, not per step)

Loading 16–17 GB from NVMe into `cudaMalloc` memory is bounded by the drive (~3–7 GB/s for the Spark's NVMe) and by how the bytes get there. The CPU-staged path (read into page cache, copy into device memory) moves each byte twice over the same bus. Options: `O_DIRECT` reads into pinned staging buffers with `cudaMemcpyAsync` overlapped (user space, probably a 1.5–2× load speed-up over a naive loader); **GPUDirect Storage** (`cuFile`, the `nvidia-fs` kernel module) would move NVMe to GPU memory without the CPU copy, but it is a datacenter feature whose support on GB10/DGX OS is unverified (the DGX Spark user guide does not list it), and on a unified-memory machine the "copy" it removes is a memcpy within one DRAM, so the win is CPU time, not bandwidth. Measure the current `ling-serve` load time first; if it is under ~15 s it is not worth touching.

### 3.7 Kernel modules: nothing to write (0%)

The case for a custom kernel module would be: a scheduler hook to keep the engine core hot (3.2 does it from user space), a bandwidth governor (MPAM/resctrl does it if exposed, 3.1), or a faster path from NVMe (3.6, `nvidia-fs` exists). Nothing in the decode step crosses the kernel: graph launch is a user-space ioctl-free submission on modern drivers, and completion is polled or a driver interrupt we cannot move. There is also a strong reason not to: the NVIDIA open kernel modules and the DGX OS kernel are the supported pair; an out-of-tree module on a 128 GB unified-memory machine where a stuck DMA freezes the host (host-safety.md) is risk without a measured target.

### 3.8 The NVIDIA driver's knobs (0% for decode)

`nvidia_uvm` parameters (prefetch, thrashing mitigation, fault batch count) govern *managed* memory, which the engine does not use for hot data, so they are moot. `NVreg_EnableStreamMemOPs=0`: stream memory operations (`cuStreamWaitValue32`/`WriteValue`) are off; a persistent-kernel design that signals the host through memory would want them on, and that is a module parameter change with a reboot, worth noting for the megakernel work (specs/research/megakernel-debuggable-2026-10-14.md), not for today. Persistence mode is already on.

## 4. What to do, in order

1. **Measure, two hours, on second-puffin in a GPU slot:** (a) `ling-serve` decode step time with the engine thread pinned to a core ≠ 0 vs unpinned; (b) with a 1 ms spin-wait before the completion wait vs plain blocking; (c) with `stress-ng --vm` or the indexer running on four cores beside it, to put a number on contention (the M0 §1 measurement RESEARCH §16.9 wanted). Seeded replay, outputs must match. Expected: (a) and (b) ≤1% together; (c) is the number that decides whether 3.1 matters.
2. **If (c) shows >3%:** check `mount -t resctrl` on DGX OS; if MPAM is exposed, put indexers and Night Shift in a bandwidth-capped group through the existing memory-budget machinery (`host setup`). If not, `SCHED_IDLE` for indexers, which costs nothing.
3. **Guard rails, no measurement needed:** a `ling-stream-tests` assertion on the allocation kind of the weight arenas; `CPUAffinity=` and `Nice=-5` in the serve unit; stop `tracker-miner-fs` on nodes via `host setup` (it already handles Wi-Fi power saving the same way).
4. **Do not pursue:** 64 KB pages, hugetlbfs, `isolcpus`/`nohz_full`, IOMMU passthrough, custom modules, GPUDirect Storage. Each costs a reboot or a kernel and buys under 1% of a decode step.

## 5. What this means for the roadmap

Linux is not where the next 10% is. The order of levers for single-stream decode remains: acceptance (drafter re-quantisation, the copy proposer), then step time inside the engine (step savings, then a persistent step if B08's measurement supports it), then the bus-contention guard rails above so that what is won is not lost to a neighbouring process. The one genuinely new item this note adds to the backlog is the contention measurement and, conditional on it, MPAM bandwidth caps.

## Sources

- ling-engine: specs/DREAMFERENCE_LING_ENGINE_RESEARCH.md §16.1, §16.7, §16.9; reports/micro-2026-10-10.md §1–4; reports/M3.md §5; specs/drafts/M4-sessions-and-batches-2026-10-10.md (step breakdown, scheduler).
- NVIDIA, *Grace Performance Tuning Guide — Operating System Settings* (page size, THP, `init_on_alloc`, `iommu.passthrough`, governor): https://docs.nvidia.com/dccpu/grace-perf-tuning-guide/os-settings.html
- NVIDIA developer forums: "Very slow mmap on DGX Spark that affects model loading" (2025-11); "GB10 Hardware Baseline — First Direct Measurements" (2026-04); "NVML support for DGX Spark unified memory" (2026-01).
- vLLM, *vLLM on the DGX Spark* (2026-06): CUDA graphs by default, unified-memory headroom.
- Machine state read 2026-10-14 on second-puffin (kernel 7.0.0-1019-nvidia, driver 580.178.04) and gx10-9428 (kernel 6.17.0-1029-nvidia).
