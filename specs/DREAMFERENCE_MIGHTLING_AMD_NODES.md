# Mightling on AMD Strix Halo: the test box, a Windows node, a Linux node

**Status:** research and proposal (2026-10-21). **Nothing is built and nothing has been run on AMD hardware.** Every speed in this document is a third-party figure read on the web (source named) or an estimate computed from those figures (marked *estimate*). Statements about Mightling are read from this repository at `2eb7cb0d` (file named).

**Why this exists.** The owner asked (2026-10-21) whether AMD "Strix Halo" boxes (Ryzen AI Max+ 395, Radeon 8060S, `gfx1151`, 128 GB LPDDR5X-8000 on a 256-bit bus) can be Mightling **nodes**, machines that run the model, and not only clients. Later the same day the owner **bought one**: an ACEMAGIC AI Mini PC (Ryzen AI Max+ 395, 128 GB, 2 TB NVMe, dual LAN, Wi-Fi 7, Windows 11 Pro preinstalled), arriving **Friday 2026-10-23**, to be **kept on Windows for testing**. So the question changed from "buy one?" to "what do we do with this one": (A) a Windows client test box from day 1, (B) a node on Windows, (C) a node on Linux by dual boot or WSL2.

**Builds on:** [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) (the node role, discovery, pairing, jobs), [MIGHTLING_WINDOWS_ARM](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) (the Windows client as built, and the Windows node pieces designed but not built: engine supervisor §8.3, host safety §9, Night Shift §13, the ETW egress audit §14), [MODELS](./DREAMFERENCE_MODELS.md), [INFERENCE](./DREAMFERENCE_INFERENCE.md), the Flash-Next feasibility note on branch `models/flash-next` (`docs/dev/flash-next-feasibility-2026-10-19.md`) and the research note [windows-node-2026-10-14](./research/windows-node-2026-10-14.md).

## Short answer

1. **The engine question is settled by the community, not by the big runtimes.** SGLang and vLLM exist for `gfx1151` but are not usable for our recipe (patched community builds, Triton attention, prefill around 100–130 tok/s on a 4-bit 27B). What works is **llama.cpp** (upstream, Windows and Linux release binaries for Vulkan and ROCm 10.0, MTP and DFlash2 drafting, a `/v1/responses` endpoint) and two engines written for this one chip: **gufo** (MIT, open source, C++/HIP) and **halogen** (a free but closed binary under an EULA). gufo is the candidate; llama.cpp is the fallback; halogen is the ceiling to measure against.
2. **On this box the 27B is an Ask-grade model and Flash-Next is the Work-grade model.** Qwen3.8-27B (dense) decodes at about 20–27 tok/s with DFlash2 but prefills at only 340–650 tok/s, so an agent turn deep in a session takes **about 15–20 s** (*estimate*) against about 5–8 s on the GB10. Qwen3.8-Flash-Next (125B MoE, 6B active) decodes at about 40 tok/s with MTP and prefills at about 1,600 tok/s on gufo, so a turn takes **about 5–6 s** (*estimate*; halogen measures 4.2 s), close to today's GB10 production. The catch: Flash-Next is not yet validated as Mightling's model on any machine (its A/B on the GB10 has not run).
3. **Windows costs a little speed and a fixed memory carve-out.** The few Windows figures (AMD's 27B at 24.5 tok/s; Flash-Next at 30–38 tok/s in community reports and 34–37 tok/s on gufo's Windows port) sit 0–15% under the Linux ones; Windows prefill is unmeasured. Flash-Next needs a 96 GB "Variable Graphics Memory" carve-out there, which leaves Windows 32 GB. Nothing of `ling-admin`'s node exists on Windows (no Docker recipe, no PSI, no systemd, no Avahi, no Python by decision), so a Windows node is **4–5 weeks** of Rust in the launcher for a minimum (model server, advertisement, watchdog), against **2–3 weeks** for a minimum Linux node, which reuses nearly everything.
4. **Recommendation:** Friday is a **Windows client day** (Part A), plus a first engine measurement on Windows with no Mightling code (llama.cpp Vulkan and gufo's Windows port). The node work goes to **Linux by dual boot** on the same 2 TB disk or a second NVMe (the box has three M.2 slots), after the Windows client testing the owner wants is done. WSL2 now works for ROCm on Strix Halo, but it is not recommended for the node (§8).

---

## 1. The box and the GB10

### 1.1 What arrived (to confirm on Friday)

The order's description (Ryzen AI Max+ 395, 128 GB LPDDR5X, 2 TB NVMe, Radeon 8060S, dual LAN, Wi-Fi 7, Windows 11 Pro) matches ACEMAGIC's **M1A PRO+** product page (acemagic.com/products/m1a-395, read 2026-10-21): Wi-Fi 7 and Bluetooth 5.4, a 2 TB PCIe 4.0 ×4 NVMe in one of **three M.2 2280 slots**, "three flexible modes providing up to 140 W", a tool-free side panel. ACEMAGIC also sells an F9A with the same chip. **The model, the BIOS version, the LAN chips and the default memory carve-out are read from the box on Friday**, not assumed.

### 1.2 Against the GB10

| | AMD Strix Halo (this box) | NVIDIA GB10 (gx10-1a2b, today's node) |
|---|---|---|
| CPU | 16 Zen 5 cores / 32 threads, **x86-64** | 20 Arm cores (10 × X925, 10 × A725), **aarch64** |
| GPU | Radeon 8060S, 40 CUs, RDNA 3.5, `gfx1151`, **wave32**, 64 KB LDS; PCI id `1002:1586` (Framework community, Level1Techs N5 MAX notes) | GB10, SM121, NVFP4 tensor cores |
| Low-precision math | no FP4/FP8 matrix units usable by today's engines; 4-bit weights are dequantised (GGUF Q4_K, AWQ) | NVFP4 W4A4 in hardware |
| Memory | 128 GB LPDDR5X-8000, 256-bit: 256 GB/s theoretical, **~215–218 GB/s measured** (sleepingrobots.com; the owner's request) | 128 GB LPDDR5X, 273 GB/s; 257–262 GB/s measured (ling-engine reports) |
| How the GPU gets memory | **Linux:** GTT, the driver's window on system RAM (default half of RAM; `amdgpu.gttsize`/`ttm.pages_limit` raise it to ~124 GiB). **Windows:** a firmware carve-out ("Variable Graphics Memory", up to 96 GB) plus shared memory | one unified pool, CUDA allocations |
| Compute stack | ROCm 7.x–10.x (TheRock), HIP, Vulkan (RADV / AMDVLK on Linux, Adrenalin on Windows) | CUDA 13, DGX OS |
| Power | up to 140 W package in "performance" mode (ACEMAGIC); halogen's numbers are at ~85 W sustained | ~140 W |
| Price | the owner's order; the class sells for about €2,000–4,300 (the owner's request) | ASUS Ascent GX10: €3,499 at launch from ASUS Germany (NVIDIA developer forum, 2025-10-20); €3,779 observed July 2026 (rubentorney.com) |

Decode is bandwidth-bound on both. At equal bytes per token Strix Halo is about **80%** of the GB10 (215 against 260 GB/s measured). The larger gap is **prefill**, which is compute-bound: the GB10 has NVFP4 tensor cores, and RDNA 3.5 dequantises 4-bit weights in shaders. That is why the dense 27B suffers here (§4) and the 6B-active MoE does not.

---

## 2. Backends on `gfx1151` (October 2026)

### 2.1 Verdict

| Backend | Runs on gfx1151 | Windows | Drafting | `/v1/responses` for `ling` | Prefix cache, hybrid (GDN) models | Long context (64K+) | Qwen3.8 tool calls | Verdict |
|---|---|---|---|---|---|---|---|---|
| **SGLang** | official nightly image `rocm/sgl-dev:*-gfx1151*` (e.g. `v0.5.20-rocm724-gfx1151-20260924`), Triton attention, AITER off (docs.sglang.io, AMD GPUs page) | no | no evidence for DFlash2 or MTP on gfx1151 | yes (SGLang's own) | RadixAttention | untested here | `qwen3_coder` parser | **Not a candidate.** int4 dense layers need community patches (Marlin is CUDA-only); Qwen3.6-27B BF16 measured at **1.7 tok/s**; the 35B-A3B MoE at 39.6 tok/s only after nine patches (github.com/JeremiahM37/strix-halo-sglang) |
| **vLLM** | not in upstream's supported list; Lemonade ships `vllm:rocm` as **experimental, Linux only**, "validated on gfx1151" (lemonade-server.ai vLLM page); community builds with 18 patches | no | DFlash in a patched build | yes, but the non-streaming Responses path misroutes tool calls with thinking off (hec-ovi/vllm-awq4-qwen) | yes | 256K in the patched build | `qwen3_coder` works on chat completions | **Not a candidate.** Qwen3.6-27B AWQ + DFlash: 24.8 tok/s decode, **105–134 tok/s prefill** even with a custom HIP kernel (hec-ovi/vllm-awq4-qwen); "gfx1151 lacks an MXFP4 kernel for vLLM" (zenn.dev, 2026-06-17) |
| **llama.cpp** (upstream) | yes: release assets `ubuntu-rocm-10.0-x64`, `ubuntu-vulkan-x64`, **`win-rocm-10.0-x64`, `win-vulkan-x64`** (release `b11791`, 2026-10-21) | **yes** | MTP (`--spec-type draft-mtp`) for Qwen3.8-27B; DFlash2 (z-lab GGUF drafter); Flash-Next MTP via PRs and forks (llama.cpp discussion #27950) | yes, translated to chat completions; **#30773**: a reply cut by `max_output_tokens` is reported `completed`, and a cut tool call arrives as a `function_call` with invalid JSON (dev.to, 2026-10-21, b11753) | yes, with `--ctx-checkpoints` (mandatory for GDN layers; slot save/restore does not keep them) | yes | `--jinja` with a patched template | **Fallback.** Prefill at depth is the weakness (27B: 239 tok/s at 32K; Flash-Next: 278 at 32K) |
| **gufo** (github.com/gufo-org/gufo) | yes, the only target; MIT; C++20/HIP, ROCm 7.2.3; container `ghcr.io/gufo-org/toolboxes/gufo-runtime` | an **unmerged fork** (github.com/pixmaate/gufo, TheRock ROCm 10.0 for Windows, "No WSL") | DFlash2 for the 27B, adaptive MTP for Flash-Next | yes: "Chat Completions and Responses support text, images, function tools, structured output and streaming" | conversation caching is "first-class" | native context by default | function tools; it moves mid-conversation developer messages to the leading system turn itself, the problem our `chat_template_patches` solve | **Candidate.** The fastest open engine on this chip by a wide margin at depth (§4) |
| **halogen** (github.com/peonist-ai/halogen-flash-server; 27B: halogen-server) | yes, the only target; a compiled binary in a container image | **no** ("WSL2 … is not a supported host"; Linux only) | its own draft head plus prompt lookup | yes, written for Codex: streaming, `function_call` round trip, strict tools, schema-constrained output; **no response store** | yes | 1M documented | yes | **Ceiling, not a dependency.** EULA: free for any use including commercial, unmodified redistribution allowed, no source; at most 4 speculating conversations. "With `HALOGEN_DOWNLOAD` unset, the container makes no outbound network connections at all" (its README) |
| **Lemonade** (AMD) | llama.cpp (Vulkan, ROCm) and FastFlowLM (NPU), vLLM on Linux | **yes** | through llama.cpp | yes (#2674: "System message must be at the beginning" with tools: the same template trap production patches) | through llama.cpp | yes | through llama.cpp | Bring-your-own server on Windows; ships an OpenTelemetry export subsystem (its telemetry guide), so the egress audit must cover it before it is trusted |
| **LM Studio** | llama.cpp runtimes (Vulkan, ROCm); headless daemon `llmster` | **yes** | MTP (AMD's day-0 blog tells users to set 4 draft tokens) | yes ("Codex is supported because LM Studio implements … `POST /v1/responses`", lmstudio.ai docs) | yes | yes | yes | Closed source; useful only as a known-good reference on Windows |
| MLC, ONNX/NPU | not pursued: no Qwen3.8 hybrid support found, and the NPU (50 TOPS) is for small models (halogen's NPU page runs "small models") | | | | | | | — |

### 2.2 Pitfalls the first boot must avoid (from the sources above)

- **llama.cpp on HIP must be built with hipCUB** (`hipcub-devel`, `rocprim-devel`, `-DGGML_CUDA_USE_CUB=ON`), or Flash-Next's sparse-attention indexer falls back to the CPU past 1,024 tokens of context and decode collapses from about 20 to 5–6 tok/s (sleepingrobots.com, llama.cpp #26592, #27856). The Vulkan backend does not have this cliff.
- **Flash-Next needs `-fit off`, and must not get `--no-mmap`:** the n-gram table (26.8–38 GB) has to page from the SSD by mmap (sleepingrobots.com). Dense models want the opposite.
- **The GGUF's embedded chat template is Qwen's stock one** and has the two bugs production patches (`xhigh` default; `raise_exception` on `minimal`/`high` and on a second system message). Use a patched template (`--chat-template-file`, the same copy `ChatTemplatePatcher` writes, or froggeric's fixed templates per sleepingrobots.com).
- **Set `--min-p 0`** explicitly in llama.cpp (its default 0.05 is not the model's).
- **`--ctx-checkpoints` is mandatory** for prefix reuse on GDN models in llama.cpp; `-np 3 --ctx-checkpoints 8` took return-to-session time from 21–251 s to 0.3–0.5 s for one user (llama.cpp #27950).
- **Read the output, not the counter:** an early MTP port produced plausible tok/s while emitting noise above about 1,000 prompt tokens (#27950), and multi-segment prompts degraded to `//////` on gfx1151 in one build (#27797). The tool-call canary and a greedy long-prompt probe belong in the first measurement, as on the GB10.
- **#30773:** until it is fixed, a `ling` turn cut by an output limit on llama-server looks complete.
- **Linux kernel floor:** AMD's two KFD fixes for gfx1151 (Linux 6.18.4, Ubuntu 24.04 HWE `6.17.0-19.19~24.04.2` or OEM `6.14.0-1018`, per AMD's Strix Halo page as quoted by halogen's troubleshooting guide) and `CONFIG_HSA_AMD_SVM`.
- **A GPU process killed with work in flight can leave 35–45 GiB of GTT allocated with no process alive**, cleared only by a reboot (halogen troubleshooting, issues #34 and #79, four hosts). Our watchdog's kill is exactly such a kill (§7.3).

---

## 3. Models

### 3.1 Qwen3.8-27B (production's model) as GGUF

- **Checkpoints:** unsloth `Qwen3.8-27B-GGUF` UD-Q4_K_XL and UD-Q8_K_XL at revision `4ca72078…`, with the z-lab `Qwen3.8-27B-DFlash2-GGUF` Q4_K_M drafter at `2d9571f8…` (gufo's README). The GGUFs keep Qwen's MTP layers (`blk.*.nextn.*`), which llama.cpp uses with `--spec-type draft-mtp` (github.com/sudoingX/qwen38-mtp). halogen ships its own 6.3-bpw `.hgn` checkpoint (35.9 GB).
- **Memory:** gufo peaks at **40.2 GiB** (Q4, 262,144-token capacity) and 50.3 GiB with a 16K prefix and 4K prompt; llama.cpp 36.2–36.6 GiB (gufo's 27B benchmarks). KV at 64K is a small part of that: the model has 16 attention layers out of 64.
- **Speed (gufo, Q4, greedy, thinking off, 2026-10-19):** autoregressive decode 12.4 tok/s; **DFlash2 decode on mixed text 26.9 at depth 0, 19.8 at 32K, 18.6 at 64K**; prefill **648 at 0, 413 at 32K, 339 at 64K**; llama.cpp prefill 348 / 239 / 191. AMD's own day-0 figure: **"up to 24.5 tokens per second"** on Windows with llama.cpp Vulkan and MTP = 4, VGM set to 64 GB (amd.com blog, August 2026). Launch-day community reports without MTP: 10.5–14 tok/s (kingy.ai; a Facebook group).
- **halogen-server** (closed): DFlash2 mean 31.7 tok/s over ten prompt shapes; prefill 566 tok/s at 32K; a 32K prompt with a 256-token answer in 66 s (its README).

### 3.2 Qwen3.8-Flash-Next (MoE, ~125B total, ~6B active)

- **Checkpoints:** unsloth `Qwen3.8-Flash-Next-GGUF` UD-Q4_K_XL (111.3 GB) or UD-IQ4_XS (93.7 GB) at revision `38bb39ee…`, with the shared-Q8_0 MTP sidecar; AtomicChat's 4.27-bpw build (92.9 GB, of which 54.5 GB needs fast memory and the n-gram table pages from the SSD; KLD 0.084 against BF16, sleepingrobots.com). No AWQ build for AMD is in use; the NVFP4 exports of the Flash-Next note are useless here (no FP4 units).
- **Fit in 128 GB:** gufo peaks at **88.8 GiB** of HIP memory at a 133K capacity (90.5 with a 16K prefix); one Windows report fits 262K context in 74 GB of a 96 GB carve-out with the table in host mmap (llama.cpp #27950). That leaves roughly 20–35 GiB for everything else: enough for the node, tight for parallel Night Shift tasks (8 GiB each) or SWE-bench containers.
- **Speed (gufo, UD-Q4_K_XL, adaptive MTP, 2026-10-18):** decode on mixed text **35.8 at depth 0, 40.0 at 32K, 39.2 at 64K, 39.9 at 128K**; prefill **1,735 at 0, 1,619 at 32K, 1,556 at 64K, 1,481 at 128K**. llama.cpp on the same box: decode 31.7 / 25.4 / 19.7, prefill 469 / 278 / 208. 8 concurrent requests: **127 tok/s aggregate on mixed text** (gufo). halogen 0.18: 73 tok/s writing code, 54 on prose, prefill 1,821 / 1,740 / 1,675 at 8K / 32K / 128K, **an agent turn at 30K–96K of context in 4.2 s median (first token 1.6 s)**, a cold 34K first turn in 21 s (its README; 115 turns of a coding agent).
- **Quality against the 27B (Qwen's card, via the Flash-Next note):** SWE-bench Pro 62.5 against 61.7 (noise), DeepSWE 1.1 58.7 against 42.2, SWE-bench Multilingual 81.0 against 73.8, all in third-party harnesses at 256K and temperature 1.0. **Not measured by us on any machine:** the Flash-Next A/B night on the GB10 is prepared on branch `models/flash-next` and has not run.

### 3.3 Smaller MoEs

Measured with llama.cpp on this chip (github.com/visorcraft/strix-halo-llm-perf, Vulkan unless stated): Qwen3 30B-A3B Q4_K_M 86 tok/s; Qwen3-Coder-Next 80B-A3B Q4_K_M 42.7 tok/s (pp512 531); GPT-OSS 120B 53.4 tok/s; Nemotron-3 Nano 30B-A3B 61.5 tok/s. "Active parameters predict tg well": 3B active ≈ 61–86, 5B ≈ 53, 10B ≈ 29–33, 22B ≈ 17 tok/s. None is a Mightling model, and none beats the Qwen3.8 pair on the coding numbers we use; they matter only as a fallback if Flash-Next disappoints in quality.

---

## 4. Expected experience per surface (*estimates* from the figures above)

**Method.** A steady-state agent turn costs `tail ÷ prefill(depth) + answer ÷ decode(depth)`, with the prefix cached (gufo, halogen and llama-server all cache it). The tail is the tool result plus the model's last call, taken as 2,000 tokens (halogen's traces: 800–3,000), the answer as 200 tokens. GB10 rows use production's SGLang figures (prose 25.5, code 50.3 tok/s, prefill ~1,700 tok/s, MODELS §2) and ling-engine's (about 64 tok/s on agent turns and 4K tok/s prefill, as stated in the owner's request of 2026-10-21). All AMD rows are third-party Linux figures; for Windows see §6.4.

| Surface | GB10, SGLang (production) | GB10, ling-engine | Strix Halo, 27B, gufo | Strix Halo, 27B, llama.cpp | Strix Halo, Flash-Next, gufo | Strix Halo, Flash-Next, llama.cpp |
|---|---|---|---|---|---|---|
| **Ask / phone chat**, short context, decode | 25.5 prose, 50.3 code | ~64 | ~27 (mixed, DFlash2) | ~23 | ~36–38 (mixed, MTP); halogen 54 prose | ~32 |
| **Work: one agent turn at 32K** | ~5–6 s | ~4 s | **~15 s** | ~19 s | **~6 s** | ~15 s |
| **Work: one agent turn at 57–64K** | ~6–7 s | ~4 s | **~17 s** | ~21 s | **~6.5 s** | ~20 s |
| **Work: cold first turn, 34K prompt** | ~20 s | ~9 s | ~80 s | ~140 s | **~21 s** (halogen measured 21 s) | ~100 s |
| **Night Shift**, aggregate decode at 8 streams | not measured as an aggregate | — | 91 tok/s mixed | 72 | 127 mixed (246 repetitive) | 62 |
| **Night Shift**, a 40-turn task at 30–60K *(estimate)* | ~4 min | ~3 min | ~10–12 min | ~13–14 min | ~4 min | ~10–13 min |

What it means for a person: **Ask feels the same on the 27B** (prose speed equals the GB10's) and better on Flash-Next. **Work on the 27B is about three times slower per turn than the GB10**, which is the difference between watching the agent and leaving it. **Work on Flash-Next with gufo is close to today's GB10**; with llama.cpp it is not. Night Shift on one box runs one or two tasks at a time with Flash-Next resident (memory, §3.2).

---

## 5. Part A: day 1, a Windows client test box

The box takes the role the Windows laptop would have had: the first real Windows PC to run the x64 client against a GB10 node. It needs no Mightling code.

### 5.1 What exists for Windows today (read from the repository and release `v1.7.0`)

- **Release assets** for `x86_64-pc-windows-msvc` and `aarch64-pc-windows-msvc`: `ling`, `ling-search`, `ling-fetch`, `ling-code`, `codex-code-mode-host`, `codex-command-runner`, `codex-windows-sandbox-setup`, the `.sha256sums`, and the unsigned NSIS installers **`Mightling-Setup-1.7.0-x64.exe`** and `-arm64.exe` (`gh release view v1.7.0`).
- **`install.ps1`**: `irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex` installs into `%LOCALAPPDATA%\Programs\Mightling\bin` with no administrator rights, then asks Windows once for elevation to set up Codex's elevated sandbox (which `/airgapped on` needs), the firewall rule that lets `ling` find nodes on Private networks, and the egress audit. `-NoSandbox` skips that step. An already-elevated shell takes the elevated branch directly (`install.ps1`, the `IsInRole(Administrator)` check).
- **`ling audit egress`** on Windows: an ETW trace of a real session (Kernel-Network, DNS-Client, Kernel-Process), the same verdict as Linux, needs elevation (WINDOWS_ARM §14, built in Phase 2). Windows' own traffic (Defender, Update, telemetry) is outside the verdict, and Defender's sample submission can upload an unknown unsigned executable.
- **Not on Windows:** `ling web` (Unix sockets; the desktop app is the Ask surface), a Night Shift runner (`/night add --on <node>` is the way), SearXNG.
- **CI (`.github/workflows/windows.yml`)** on `windows-latest` (x64) and `windows-11-arm`: the crates' tests; `ling --version` and `ling exec` against a stub model server; `install.ps1` under Windows PowerShell 5.1; the air-gap check (the non-admin sandbox refused at `on`, a sandboxed `curl.exe` reaches the network at `off` and not at `on`); `ling audit egress` against the stub; the launcher tests; the desktop app built, packaged by NSIS, silently installed, started (it opens on Ask and starts its own `ling app-server`), quit and uninstalled.

### 5.2 What CI cannot show, and the box can

A real LAN and **mDNS discovery** of a real GB10 node (CI has a stub on loopback); a **real model** behind `ling exec`, Ask and Work; **Smart App Control and SmartScreen** on unsigned files downloaded by a browser; Defender's behaviour on the unsigned binaries; the **interactive UAC prompt** of `install.ps1` as a person sees it; a standard (non-admin) user with over-the-shoulder elevation; **`ling update`** from an older release; sleep, resume and Wi-Fi changes under a running `ling`; a long session; non-ASCII user names; and the GPU.

### 5.3 The owner's setup checklist (Friday, about 30 minutes)

1. **Network:** cable LAN 1 to the same network as gx10-1a2b. In Windows, set the connection's profile to **Private** (Settings › Network & internet › Ethernet › Network profile type). The installer's discovery rule and the SSH rule below apply to Private only.
2. **Name:** rename the computer to **`amd-box`** (Settings › System › About › Rename this PC) and restart. Windows answers `amd-box.local` over mDNS on a Private network (*to verify from gx10-1a2b with `avahi-resolve -n amd-box.local`*).
3. **Account:** keep the local administrator account the box was set up with; note its user name for the agents.
4. **BitLocker / device encryption:** if it is on, save the recovery key (Settings › Privacy & security › Device encryption) **before any BIOS change** in §8.
5. **Drivers:** run Windows Update, then install AMD Software: Adrenalin Edition (26.2.2 or newer, which also enables ROCm for WSL, §8.2). Note the version.
6. **OpenSSH server**, in an administrator PowerShell:

   ```powershell
   Get-WindowsCapability -Online -Name OpenSSH.Server* | Add-WindowsCapability -Online
   Set-Service sshd -StartupType Automatic; Start-Service sshd
   # An administrator's keys go here, not in ~\.ssh\authorized_keys:
   $keys = "$env:ProgramData\ssh\administrators_authorized_keys"
   Add-Content -Path $keys -Value 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICBPV5KiOAqxD540lHgqVyQclzZ0J8gKFT/1QymuShtm dgxcoder@gx10-1a2b amd-box'
   icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
   Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Profile Private
   ```

   The key is the dedicated one generated on gx10-1a2b on 2026-10-21: `~/.ssh/amd-box_ed25519` (fingerprint `SHA256:wr/35cbsw/wO0NUeLfEz/YYs5yYiXSu/XtEchXqFHXg`). Only the public half leaves gx10-1a2b.
7. **Smart App Control:** the preview builds are unsigned, and Smart App Control blocks them outright (`install.ps1`'s header). Turning it off is the owner's decision (§12, D2): Microsoft has documented that it cannot be turned back on without reinstalling Windows.
8. **Power mode:** set the box's performance mode (up to 140 W) for any measurement, and say which mode was used.
9. Tell the agents: done, and the user name.

### 5.4 What the agents do

1. Add to `~/.ssh/config` on gx10-1a2b, shaped like the `mac` and `desktop` entries (resolved by mDNS at use time, never by address):

   ```
   Host amd-box amd-box.local
       HostName amd-box.local
       HostKeyAlias amd-box
       User <the owner's Windows user name>
       IdentityFile ~/.ssh/amd-box_ed25519
       IdentitiesOnly yes
       CheckHostIP no
   ```

2. `ssh amd-box` and record: Windows build, Adrenalin version, the carve-out (VGM) and shared GPU memory Task Manager reports, the BIOS version, the LAN chips, `Get-NetConnectionProfile`, free disk.
3. Run the release test (§5.5) for v1.7.0 against gx10-1a2b, and write the results to a dated file.

### 5.5 A repeatable release test (every release, about 45 minutes; to be scripted as `scripts/windows_release_check.ps1`, not yet written)

| # | Step | Who | Pass |
|---|---|---|---|
| 1 | Install the **previous** release with `install.ps1 -Version <n-1>` in a fresh user profile | agent over SSH | `ling --version` prints n-1 |
| 2 | `ling update` | agent | the new version; the signature check passes |
| 3 | `ling node list` | agent | gx10-1a2b found by mDNS, its model and context read from `/v1/models` |
| 4 | `ling exec "list the files here and say which is largest"` in a scratch repository | agent | a tool call runs, an answer arrives |
| 5 | `/airgapped on`: the sandboxed `curl.exe` probe of `windows.yml` | agent | reaches the network at `off`, not at `on`; the non-admin sandbox refused at `on` |
| 6 | `ling audit egress` (elevated) | agent | exit 0; every connection to gx10-1a2b or loopback |
| 7 | `ling-search "…"`, `ling-fetch <url>` | agent | answers |
| 8 | `ling-code` index and a `def` query on a small repository | agent | answers |
| 9 | Download `Mightling-Setup-<n>-x64.exe` **in a browser**, run it | owner | what SmartScreen and Defender say is recorded; installs per user; Start menu entry |
| 10 | The app: Ask a question, then a Work task in a scratch folder | owner | answers from the node; the task's tool calls run |
| 11 | Sleep the box 5 minutes, wake, continue the Work task | owner | the session continues or says why not |
| 12 | Uninstall the app; `ling` stays | owner | the folder, Start entry and Apps entry are gone |
| 13 | Install as a **standard user** with `install.ps1` (UAC asks for an administrator) | owner | the sandbox is set up for that user |

---

## 6. Part B: the box as a node on Windows

### 6.1 What can serve the model natively on Windows 11 with the 8060S

| Engine on Windows | How | State |
|---|---|---|
| **llama.cpp, Vulkan** | upstream release `llama-<b>-bin-win-vulkan-x64.zip` | released; AMD's day-0 27B figure (24.5 tok/s, MTP = 4) was measured on it |
| **llama.cpp, ROCm/HIP** | upstream `llama-<b>-bin-win-rocm-10.0-x64.zip`; for Flash-Next a build with hipCUB, or a fork (olliehm's "rdna-boosts" build against TheRock 10.1, 38 tok/s through Lemonade with a 96 GB carve-out, llama.cpp #27950) | released; Flash-Next's fast path is fork-dependent |
| **gufo, Windows port** | github.com/pixmaate/gufo, TheRock ROCm 10.0 for Windows (`therock-dist-windows-gfx1151-10.0.0.tar.gz`, SHA-256 pinned in its WINDOWS.md) | **unmerged fork**; the one Windows figure in its docs: Flash-Next sampled decode of 34.5 / 37.2 / 48.3 tok/s on prose / code / reasoning (its Windows-only decode switches on; 7.6–9.5% slower with them off). The fork's README speed table appears to be upstream's Linux table at fork time, so it is not used here |
| **Lemonade, LM Studio** | installers; both speak the Responses API | bring-your-own server (WINDOWS_ARM §8.2's Phase 3); the launcher already reads llama.cpp's window from `/props` (`ling-rs/src/lib.rs`, `llama_cpp_window`) |
| **vLLM, SGLang** | none on Windows | Lemonade's vLLM is Linux only; nothing else found |
| **halogen** | — | Linux only |

### 6.2 The Windows memory model

- The GPU sees a **firmware carve-out** (VGM, set in AMD Software › Performance › Tuning or the BIOS, up to 96 GB) plus shared system memory. gufo's port measured that `hipMalloc` succeeds up to about 110 GiB, but memory **beyond the carve-out** is backed page by page at about 0.63 GiB/s (against 28 GiB/s inside), and decode kernels reading weights there run **6–22% slower**; `hipMemGetInfo` double-counts it, so gufo sizes itself from the DXGI budget (`QueryVideoMemoryInfo`) instead (pixmaate/gufo `docs/WINDOWS.md`). That is the same budget WINDOWS_ARM §9 chose for the watchdog, arrived at independently.
- **Flash-Next needs the 96 GB carve-out** (load in ~30 s; with 64 GB it loads in ~2 minutes and decodes slower), which leaves Windows **32 GB**. The 27B is comfortable at 64 GB (AMD's configuration).
- **A carve-out overflow freezes the whole box** under WDDM (olliehm's report in #27950); his fix is a preflight proxy that measures footprints and evicts before load. That is the Windows form of the GB10's lock-up and the reason the watchdog is not optional.
- The carve-out is a firmware setting: changing it means a reboot, and it applies to Linux too if the box later dual-boots (§8.1).

### 6.3 `ling-admin`'s node pieces on Windows

| Node piece (Linux, today) | On Windows | Exists? |
|---|---|---|
| `ling-admin server start`: Docker, the SGLang image, `--gpus all` (`vllm_server_manager.py`) | an engine supervisor in the launcher running `llama-server.exe` (or gufo) as a child in a Job Object, with a pinned binary and a pinned GGUF (WINDOWS_ARM §8.3, W1) | designed, not built |
| The model registry, Python (`ModelMatrixRegistry.MATRIX`) | the launcher needs the recipe without Python: a registry export (JSON) shipped in the release, read by Rust | no |
| Host safety: `check_host_safety()`, the PSI watchdog, earlyoom | `GlobalMemoryStatusEx`, the DXGI budget, `CreateMemoryResourceNotification`, `TerminateJobObject`; the §9.4 test (allocate in a second process until the watchdog trips; the desktop must stay responsive) | designed (WINDOWS_ARM §9), not built |
| The model gate (`model_gate_service.py`, Python, run from the engine's image) | a Rust port in the launcher, or nothing: it only gives SWE-bench priority, and SWE-bench does not run on a Windows node | no |
| Tool-call canary, NVFP4 canary (Python) | the tool-call canary in Rust; the NVFP4 canary does not apply | no |
| Advertisement: Avahi service file (`NodeServiceFile`) | `mdns-sd` registration from the launcher (the crate, 0.21.4, is already a launcher dependency for browsing, `ling-rs/Cargo.toml`) or Windows' `DnsServiceRegister`; plus an inbound firewall rule for port 8000 on Private | no |
| Pairing and jobs over SSH with one forced command | Windows OpenSSH (`ForceCommand` / `command=` *to verify*), jobs in a Job Object | no |
| Night Shift (Python, systemd timer) | a Rust `ling night run` under Task Scheduler (WINDOWS_ARM §13) | designed, not built |
| Sandbox for agent commands: bwrap and the AppArmor profile | Codex's elevated Windows sandbox, `/airgapped on` through patch `0024` | **built** |
| Egress audit (strace, Python) | `ling audit egress` over ETW; it must grow to cover the engine's process, which runs outside `ling`'s tree | built for `ling`; the engine not covered |
| SearXNG, the Google service, voice, image search (Linux containers) | none; a node on Windows offers inference only | no |
| `ling web` (Ask in a browser, port 3100) | Unix-only; the desktop app on the box itself is the Ask surface | no |
| SWE-bench (Docker, Linux containers) | Docker Desktop with the WSL2 backend could run the x86-64 images; not a node feature | no |

### 6.4 The minimum viable Windows node, and its numbers

**Minimum:** the engine supervisor (pinned `llama-server.exe` Vulkan or ROCm, pinned GGUF, patched template, the context sized to the DXGI budget), the watchdog with the §9.4 test passed, the advertisement and firewall rule, and the tool-call canary. A client then finds `amd-box` as it finds a GB10 (MIGHTLING_NODE §12.3), and Work and Ask run against it. **Stays Linux-only:** the gate, Night Shift lanes on the node, jobs, SWE-bench, the sidecars, `ling web`.

**Effort (*estimate*, one stream of work):** supervisor and registry export 1.5–2 weeks; watchdog and its test 1.5 weeks; advertisement and firewall 3–4 days; canary and audit coverage of the engine 1 week. **About 4–5 weeks** for the minimum; Night Shift in Rust adds 2 weeks; the gate 1 week if wanted.

**Expected numbers on Windows (*estimate*; thin evidence):** decode about **0–15%** under §4's Linux rows: the 27B at 24.5 tok/s with llama.cpp Vulkan and MTP (AMD) against 23–27 on Linux; Flash-Next at 34–37 tok/s sampled on gufo's port, 38 tok/s through Lemonade on a patched llama.cpp build and 30 tok/s in another report (medium.com/@GenerationAI, 2026-09-03), against 36–40 greedy on Linux. **Prefill on Windows is unmeasured** and is what decides Work; Phase 1 (§9) measures it. Provisionally: 27B Ask ~22–25 tok/s and a 32K Work turn ~16–20 s; Flash-Next (gufo port) Ask ~33–36 tok/s and a Work turn ~6–8 s, on a 96 GB carve-out.

---

## 7. Part B': the work for a Linux AMD node (if the box dual-boots, or for a second box)

Most of `ling-admin` is Linux code that assumes NVIDIA only in a few places.

| Area | Change | Size (*estimate*) |
|---|---|---|
| **Platform concept** | `HardwareManager` qualifies only the GB10 (`is_gb10`, PCI `0x10de:0x2e12`); add Strix Halo by PCI `1002:1586` and `gfx_target_version 110501` from `/sys/class/kfd/kfd/topology/nodes/*/properties`. `ModelSpec.compatible_gb10` becomes a per-platform field. The installer's node detection (`install.sh`, `uname -m` = `aarch64` + GB10) learns x86-64 + `1002:1586` | 3–4 days |
| **Registry entries** | e.g. `qwen3.8-flash-next-gguf-q4-mtp` and `qwen3.8-27b-gguf-q4-dflash2`, with `engine: "gufo"` or `"llama.cpp"`, an image pinned by digest, the GGUF revision, the drafter, the context and slot count, the patched template; never by renaming existing entries | 2–3 days |
| **Launch builder** | `_docker_run_prefix` hard-codes `--gpus all`; an AMD prefix is `--device /dev/kfd --device /dev/dri --group-add video --group-add render --security-opt seccomp=unconfined --ipc=host --ulimit memlock=-1`. A `GufoLaunchBuilder` / `LlamaCppLaunchBuilder` beside `SGLangLaunchBuilder`. The memory gates use `gpu_memory_utilization`, which neither engine has: they size from a fixed context and slot count instead | 1–1.5 weeks |
| **Model gate** | it runs `python3` from "the engine's image" (`model_gate.py`); gufo's and llama.cpp's images may have no Python, so the gate gets its own pinned image | 2–3 days |
| **Host checks** (`check_host_safety`, `HostSafetySetup`) | kernel ≥ 6.18.4 (or the Ubuntu HWE/OEM floors), `CONFIG_HSA_AMD_SVM` (capability bit `0x08000000`), the `cwsr_size` check halogen documents, `amdgpu.gttsize` and `ttm.pages_limit` sized to RAM (126,976 MiB and 32,505,856 pages for 128 GB), BIOS carve-out at its **minimum** on Linux (the opposite of Windows), `render`/`video` group membership; swap, `vm.min_free_kbytes` and earlyoom as today. `amd_iommu=off` is worth 13–16% of prefill (halogen) but turns off DMA translation machine-wide: an owner's decision, not a default | 1 week |
| **Host safety during load** | PSI is system-wide, and GTT pages are system RAM, so the watchdog should see the pressure (*to measure*). GTT is **not charged to the container's memory cgroup** (ROCm/legacy-rocm-build #6370, "closed as completed" on 2026-10-19 without a visible fix; a TTM memcg series was posted to dri-devel in July 2026), the same blind spot as the GB10 (MIGHTLING_NODE §13.4). New: read `mem_info_gtt_used` before every start and refuse when tens of GiB are held by no process (halogen's pre-flight), because **a watchdog kill can leave GTT stuck until a reboot**; and an engine silent *inside the driver* must not be mistaken for a hung one | 1 week, including the measurement |
| **Advertisement and locator** | unchanged (Avahi on Ubuntu); the TXT records gain the platform | 1 day |
| **Egress audit** | unchanged; the engine container is checked like the SGLang one | 1–2 days |
| **Night Shift** | unchanged; the memory caps of its scopes do not bound GTT, so parallelism comes from the model's resident size (1–2 tasks beside Flash-Next) | 2 days |
| **SWE-bench** | x86-64 **runs the upstream `swebench/sweb.eval.x86_64.*` images natively**: all 500 Verified instances, not greynewell's 400 arm64 images (`docs/dev/swe-bench.md`), so the image rewrite becomes architecture-aware and a cross-machine comparison is restricted to the shared 400. The patchelf'd `ling` uses the x86-64 Linux build. Validation reruns per image | 1 week |
| **Provisioning** | `node provision` (MIGHTLING_FLEET) for Ubuntu 26.04 on x86-64: the kernel command line, the groups, Docker, the profile | 2–3 days |
| **Canary** | the tool-call canary as is; the NVFP4 canary skipped (no `nvfp4` alias) | — |

**Total: about 5–7 weeks** for the whole table; **2–3 weeks** for a minimum Linux node (platform, one registry entry, the launch prefix and builder, the GTT pre-flight, the gate image, advertisement), which serves Work and Ask to clients. ling-engine (CUDA only) is out of scope: gufo shows that a model-specific engine for this chip is possible, and porting ours to HIP is a separate project.

---

## 8. Part C: Linux on the box, by dual boot or WSL2

### 8.1 Dual boot

- **Disk:** the 2 TB drive can be shrunk (Windows plus its tools fit in ~400 GB; Linux and the models in the rest: Flash-Next ~94–111 GB, the 27B ~18–36 GB), or a second NVMe goes in one of the two free M.2 slots (the owner's purchase, if wanted), which keeps Windows' disk untouched.
- **What it buys:** the minimum Linux node of §7 in 2–3 weeks; gufo's and halogen's Linux engines (the fastest numbers in §4); `ling-admin` unchanged; Night Shift and SWE-bench on x86-64 images.
- **Costs:** a reboot to switch roles, so the box is **either** a Windows test client **or** a node at any moment; BitLocker's recovery key must be at hand; the **carve-out conflict**: Windows wants 64–96 GB carved out, Linux wants the minimum, and the setting is firmware-wide, so each switch may mean a BIOS change too (*to verify whether the box's BIOS exposes it, and whether AMD Software's setting persists across a Linux boot*).

### 8.2 WSL2

- **ROCm in WSL2 is now official on Strix Halo:** "This release also marks the first time AMD officially supports Ryzen Strix and Strix Halo SKUs" (Adrenalin 26.2.2 + ROCm 7.2.1, through the open-source `librocdxg` over `/dev/dxg`; rocm.docs.amd.com, WSL how-to). Ubuntu 24.04 and 22.04 are the supported distributions.
- The early bug that capped ROCm's pool at the `.wslconfig` `memory` setting and ignored the dedicated carve-out (ROCm/legacy-rocm-build #6022) was **fixed in `librocdxg`** (merged, closed 2026-04-24; one reporter confirmed 95.79 GB visible on commit `7772ad6`, another still saw the cap).
- **Against it for the node:** halogen refuses WSL2 outright (its registration needs KFD's SVM interface, which `/dev/dxg` does not provide); gufo's Windows port says "No WSL"; host safety becomes two layers (the VM's PSI does not see allocations the Windows driver makes for it, so the watchdog must live on the Windows side and kill the whole VM, WINDOWS_ARM §9.2's W2 column); and the owner decided **"No WSL"** on 2026-10-07 for the Windows product (WINDOWS_ARM, decisions). It does make one thing possible without a reboot: Docker in WSL2 running the x86-64 SWE-bench images against a Windows-side engine. That is a research curiosity, not a plan.

### 8.3 The trade-off

Keeping Windows for testing and running the node on Linux are both possible on one box, but not at the same time. The recommendation is to **finish the Windows client and Windows-engine measurements first** (one to two weeks), then add Linux as a second boot for the node work, and to revisit only if the owner wants the box permanently as a node (then Linux only, Windows testing moves to another machine).

---

## 9. Recommendation and phased plan

| Phase | What | Who | Gate to the next |
|---|---|---|---|
| **0, Friday** | Part A: the setup checklist, the release test of v1.7.0, the CI-gap list checked off | owner (§5.3), agents (§5.4–5.5) | the client works against gx10-1a2b; findings filed |
| **1, days 2–5** | Windows engine measurements with no Mightling code: llama.cpp Vulkan and ROCm release zips and gufo's Windows port, Qwen3.8-27B Q4_K_XL (+ DFlash2 drafter, + MTP) at VGM 64 GB and Flash-Next UD-IQ4_XS/Q4_K_XL (+ MTP) at VGM 96 GB; decode on production's prose/code/JSON prompts, prefill at 8K/32K/64K, the tool-call canary through `/v1/responses` from `ling exec`, a greedy long-prompt probe, the memory high-water mark | agents over SSH; the owner changes VGM in AMD Software and reboots | §4's estimates confirmed or corrected |
| **2, week 2** | Decide: Windows node (4–5 weeks, §6.4) or Linux node by dual boot (2–3 weeks, §7), or neither | owner | a decision |
| **3** | The chosen minimum node; the box advertised; `ling` from the GB10 and the laptop uses it | agents | a Work session from a client against the box |
| **4** | Flash-Next quality: the SWE-bench A/B on the GB10 (prepared on `models/flash-next`), then the same instance list on the box (x86-64 images, Linux) | agents | whether Flash-Next can be the AMD node's Work model |

**Model pairing:** Flash-Next (UD-Q4_K_XL or IQ4_XS + MTP) for Work on this box, if its quality holds; Qwen3.8-27B (Q4_K_XL + DFlash2) for Ask and as the safe default until then. One model is served at a time, as on the GB10.

---

## 10. Friday, step by step

| Time | Owner | Agents |
|---|---|---|
| on arrival | unpack; Ethernet on LAN 1; first boot; finish Windows setup with a local administrator | — |
| +15 min | §5.3 steps 1–6 (Private network, rename to `amd-box`, BitLocker key, Windows Update, Adrenalin, OpenSSH with the key); say "done" and the user name | prepare the `~/.ssh/config` entry; check `avahi-resolve -n amd-box.local` |
| +30 min | §5.3 step 7 (Smart App Control: decision D2) and step 8 (performance mode) | `ssh amd-box`; record the machine (§5.4 step 2) |
| +45 min | — | release test steps 1–8 over SSH |
| +1 h 15 | release test steps 9–13 at the box (browser download, the app's Ask and Work, sleep/wake, uninstall, standard user) | watch the node's logs on gx10-1a2b; collect the results |
| +2 h | set VGM to 64 GB in AMD Software, reboot (only if D3 is yes) | download the pinned llama.cpp zips and the 27B GGUF; first measurements (Phase 1) |
| end of day | — | the results file: what passed, what failed, the first numbers, and the issues to open |

---

## 11. Risks

- **Every AMD number is third-party.** The gufo and halogen figures come from their own benchmark harnesses (gufo states its peaks include repetitive output; this document uses its mixed-text rows). halogen's licence asks that published figures name the version and prompt set; any of ours will.
- **gufo is two months old** (created 2026-08-11) and its Windows port is an unmerged fork; a node built on it inherits its pace. llama.cpp is the fallback and is far slower at depth.
- **Flash-Next's quality as Mightling's Work model is unmeasured**, and the published gains are on DeepSWE and Multilingual, not on SWE-bench Pro.
- **Driver-level failures:** the GTT leak after a kill, a GPU page fault on one kernel (halogen #83), the carve-out overflow freeze on Windows. The box has no power button experience with us yet; the first loads run with the watchdog design's test in mind, with nothing else important open.
- **Unsigned Windows builds:** Smart App Control and Defender behaviour is part of what Friday measures.
- **The RAM price spike** ("before the prices went crazy", a Strix Halo owner in #27950) makes more boxes of this class more expensive than the €2,000–4,300 range in the owner's request.

---

## 12. Decisions for the owner

- **D1.** The box's name (proposed: `amd-box`) and that the agents use the local administrator account over SSH with the dedicated key.
- **D2.** Turn Smart App Control off on the box (needed for the unsigned preview; documented as not reversible without reinstalling Windows), or wait for signing (WINDOWS_ARM §16.3).
- **D3.** Allow Phase 1 on Friday or soon after: engine measurements on Windows with third-party binaries (llama.cpp release zips, gufo's Windows fork, TheRock ROCm), and a VGM change to 64 GB then 96 GB.
- **D4.** Engine to build a node on: gufo first (MIT), llama.cpp as the fallback; halogen only as a ceiling measurement on Linux, never shipped (closed EULA).
- **D5.** Where the node work goes after Phase 1: Windows node (4–5 weeks, inference only, Windows kept), Linux node by dual boot (2–3 weeks, the full node, reboots to switch), or no node (the box stays a Windows test client).
- **D6.** For dual boot: shrink the 2 TB drive, or a second NVMe in a free slot (a purchase).
- **D7.** Model pairing: Flash-Next for Work once its A/B passes, the 27B until then; or the 27B only, for parity with the GB10 at about three times the turn time.
- **D8.** Whether "a node on anything but a GB10" (a non-goal in MIGHTLING_NODE §1, open question 10 in §15.2) becomes a product goal, or stays an experiment on this one box.

---

## Sources

Read on the web, 2026-10-21, unless dated otherwise. "Third-party" means not measured by us.

- AMD, day-0 Qwen3.8-27B blog: amd.com/en/blogs/2026/run-qwen-3-8-27b-on-amd-ryzen-ai-max-and-radeon-graphics-cards-day-0.html (24.5 tok/s, Windows, Vulkan, MTP = 4, VGM 64 GB, GMKtec EVO-X2).
- gufo: github.com/gufo-org/gufo (README, MIT per the GitHub API), `docs/models/qwen3.8-27b/BENCHMARKS.md` (2026-10-19), `docs/models/qwen3.8-flash-next/BENCHMARKS.md` (2026-10-18); Windows port github.com/pixmaate/gufo (`README.md`, `docs/WINDOWS.md`).
- halogen: github.com/peonist-ai/halogen-flash-server (README, `LICENSE.md` EULA 0.1, `docs/codex.md`, `docs/limits.md`, `docs/troubleshooting.md`, version 0.18.13); github.com/peonist-ai/halogen-server (the 27B, 0.1.4).
- llama.cpp: discussion #27950 (Flash-Next on Strix Halo, MTP, hipCUB, `--ctx-checkpoints`, the Windows report); PRs/issues #26592, #27466, #27797, #27836, #27856, #28118, #30773; releases `b11791` and `v0.7.0` (GitHub API); dev.to/homelabpm on #30773.
- sleepingrobots.com/dreams/qwen38-flash-next-strix-halo (2026-08-29: quants, the hipCUB cliff, template and flag pitfalls, ~218 GB/s).
- github.com/visorcraft/strix-halo-llm-perf (llama.cpp single-host results, Vulkan against ROCm).
- SGLang AMD GPU docs: docs.sglang.io/docs/hardware-platforms/amd_gpu (gfx1151 image); github.com/JeremiahM37/strix-halo-sglang.
- vLLM: lemonade-server.ai/docs/guide/configuration/vllm/; github.com/hec-ovi/vllm-awq4-qwen; zenn.dev/shuzan/articles/72852bb9621b40.
- Lemonade issue #2674; lmstudio.ai developer docs, the compatibility-endpoints page.
- ROCm on WSL: rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/howto_wsl.html; ROCm/legacy-rocm-build #6022 and #6370 (GitHub API, state and comments).
- PCI id `1002:1586`: community.frame.work "Linux + ROCm: January 2026 Stable Configurations Update"; forum.level1techs.com "NAS Review Notes: Minisforum N5 MAX".
- ACEMAGIC M1A PRO+: acemagic.com/products/m1a-395.
- GX10 prices: forums.developer.nvidia.com/t/nvidia-marketplace-asus-ascent-gx10-reservations/348408; rubentorney.com (July 2026).
- Not re-read: the Level1Techs benchmark thread named in the owner's request (forum.level1techs.com/t/…/233796) renders by script and returned no text; its figures (a 24B dense Q4 at ~13.6 tok/s, ~317 tok/s pp512) are consistent with visorcraft's Qwen2.5-32B row (11.3 tok/s, 242 pp512) but are **unverified** here.

Read from this repository: `dreamference/hardware/hardware_manager.py`, `model_spec.py`; `dreamference/vllm_server/vllm_server_manager.py` (`_docker_run_prefix`, `HOST_MEMORY_RESERVE_GB`, `LOAD_TRANSIENT_FRACTION`), `model_gate.py`; `ling-rs/src/lib.rs` (`llama_cpp_window`), `ling-rs/Cargo.toml`; `ling-code-rs/src/index/probe.rs` (`GAUGES`: SGLang and vLLM only, so llama.cpp's and gufo's busy gauges must be added); `install.ps1`; `.github/workflows/windows.yml`; `docs/dev/models-and-engines.md`, `docs/dev/swe-bench.md`; the Flash-Next note on `models/flash-next`.
