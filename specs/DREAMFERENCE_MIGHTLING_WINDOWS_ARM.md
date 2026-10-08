# Mightling on Windows on Arm — RTX Spark laptops and desktops

**Status:** proposed (2026-10-06). Nothing is built, and nothing has been run on Windows: there is no Windows machine here. Every statement about Windows behaviour is either read from source (the pinned Codex submodule at `rust-v0.158.0`, this repository at `9d4812c`) or read on the web, and each says which. §2 lists what was checked, and the phases (§19) open with what must be measured before anything is chosen.
**Target:** Windows 11 on Arm (`aarch64-pc-windows-msvc`). The first-class machines are NVIDIA's **RTX Spark** systems (the N1X chip: the GB10 die with Windows drivers), shipping from October 2026. Any other Windows 11 Arm64 PC (Snapdragon X) is a client-only target, and the cheapest place to test that half.
**Builds on:**
- the client/node split and its three-system client ([MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) §8, §9, §15.1);
- the launcher, patch series and build ([MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md));
- `/airgapped` and the egress audit ([MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md), [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md));
- the code index ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md));
- the desktop app ([MIGHTLING_DESKTOP](./DREAMFERENCE_MIGHTLING_DESKTOP.md));
- the model server and its host-safety layer ([INFERENCE](./DREAMFERENCE_INFERENCE.md)).

**Decided by the user, 2026-10-07** (these override anything below that reads otherwise; §21 has
the questions as asked):

- **No WSL.** The local engine is **W1 only**, a native `llama-server` (llama.cpp) that Mightling
  builds and manages. W2 (SGLang in a WSL2 distribution) is **not built**; every W2 passage below is
  kept as the record of the option considered. It is revisited only if W1 measures clearly too slow
  on the test machine. vLLM and SGLang have no Windows build in any case, and on Windows on Arm
  there is no CUDA PyTorch for them to run on.
- **Model: Qwen3.8-27B everywhere**, the node's model, as GGUF `Q4_K_M` for W1. It runs locally on
  **every machine where it fits at all** (about 24 GB of GPU budget and up), with the context sized
  to what is left; below that the machine is a client of a node.
- **A lone laptop (no node on the LAN) gets, natively:** web search (SearXNG has no Windows build,
  so a native search backend, §10), **Night Shift** in Rust (§13), and the **Chat window** of
  `ling-app` without Onyx's Linux containers (§11). `/apps` (Gmail, Drive, Calendar) stays
  node-only on Windows.
- **Unsigned for now.** Windows releases ship unsigned and are marked **preview**; the release notes
  say that Smart App Control must be off to run them (§16.3). Signing is revisited before Windows
  leaves preview.
- **x86-64 Windows from the start:** `x86_64-pc-windows-msvc` is built in Phase 1 beside Arm64.
- **Patch budget up to 39,500 bytes** for `0024` (§7.4); the cap in `test_the_patches_stay_small`
  is still raised only when the patch lands, by its size as written.
- **Test machine: an ASUS ProArt P16 (H7607, NVIDIA RTX Spark) with 128 GB** for Phases 3 and 4.
  Not the H7606, which is an AMD + RTX 5090 laptop.
- **Phases 0 and 1 start now.**

**Decisions proposed here, stated first because each could be read the other way:**

1. **Native Windows, not WSL, for everything a person types.** This keeps MIGHTLING_NODE §15.1 (2026-10-02): `ling`, `ling-code`, `ling-search`, `ling-fetch` and `ling-app` are Windows executables. WSL appears in this spec only as optional plumbing *inside* the local-engine profile (§8.3), the way Docker Desktop uses it. Whether even that is acceptable is question 1 in §21.
2. **Arm64 first.** `aarch64-pc-windows-msvc` is the RTX Spark target. `x86_64-pc-windows-msvc` (in MIGHTLING_NODE §8.1) is the same code and the same workflow job with one more matrix row; it is added when it costs nothing, not before.
3. **Three install profiles, decided by the machine and overridable** (§6): **client** (any Windows Arm PC; the model is on a DGX Spark or another node on the LAN), **local** (an RTX Spark with enough memory serves its own model), and **both**.
4. **`/airgapped on` is enforced on Windows,** not cooperative as MIGHTLING_NODE §8.2 assumed. Codex's own *elevated* Windows sandbox already runs offline commands as a separate local account whose traffic the Windows Firewall and WFP block in the kernel (§7.3). Mightling needs one hook (patch `0024`, an estimated 1.3–1.8 KB) that forces that identity for a sealed session, the same shape as `0019`'s Linux hook. At `on`, the unelevated sandbox and no sandbox are refused, exactly as Full Access is.
5. **The local engine is chosen by measurement, not here.** Two candidates are carried to an RTX Spark (§8): **W1**, a native `llama-server` that Mightling builds and manages; and **W2**, today's SGLang recipe (NVFP4 + DFlash2) in a Mightling-owned WSL2 distribution. W1 is simpler and native; W2 keeps the GB10 recipe and its speed. Until a machine is measured, Mightling runs against any local Responses-API server the user already has (Ollama, LM Studio, `llama-server`): Phase 3, §8.2.
6. **Signing is reopened for Windows.** MIGHTLING_NODE §15.1 decided not to sign. On Windows 11, Smart App Control *blocks* unsigned unknown executables outright rather than warning, and new laptops ship with it in evaluation or on mode. RTX Spark buyers are exactly new laptops. §16.3 recommends Azure Artifact Signing (about $9.99 a month, open to organisations in the EU); question 2 in §21.
7. **Python stays on the node, as MIGHTLING_NODE decided.** No part of a Windows install needs Python. Where `ling-admin` does node work today (managing the model server, Night Shift, the egress audit), the Windows equivalent is either Rust in the launcher or runs inside the WSL engine (§8.3, §13, §14).

---

## 1. The machines

### 1.1 What NVIDIA and the vendors publish (read on the web, 2026-10-06)

| | RTX Spark (N1X) | DGX Spark (GB10), for comparison |
|---|---|---|
| Silicon | The same GB10 die (Wikipedia, citing NVIDIA) | GB10 |
| CPU | 20 Arm v9.2 cores in two clusters, each 5 × Cortex-X925 and 5 × Cortex-A725 (NVIDIA's porting guide) | the same |
| GPU | Blackwell iGPU; two N1X variants named by the first Windows-on-Arm driver: 6,144 and 5,120 CUDA cores (VideoCardz; NemoClaw v0.0.130 matches both names) | Blackwell, 6,144 cores, SM 12.1 |
| Memory | "Up to 128 GB" LPDDR5X, 256-bit, 300 GB/s, fully coherent (porting guide). Smaller configurations are reported only from leaks (16–128 GB for N1X); one report says every system is 128 GB. **Unconfirmed until vendors list models.** | 128 GB, 273 GB/s |
| OS | **Windows 11 on Arm only.** Linux is "unannounced and unavailable": NVIDIA has an internal Ubuntu 24.04 image ("N1x FastOS", open driver 615, CUDA 13.4), but the public open driver 615.71.09 lists GB10 and not the N1X device IDs (computingforgeeks, 2026-10-03) | DGX OS (Ubuntu 24.04) |
| Driver, CUDA | Developer Preview driver 616.00 (Arm64), 2026-07-16; CUDA Toolkit 13.4 with native Windows Arm64 (NVCC, runtime, cuBLAS, cuFFT, Nsight), released 2026-09-09, 13.4.1 replacing the July preview | Linux driver, CUDA 13 |
| NPU | Yes, at Copilot+'s 40 TOPS line (not used by Mightling) | none |
| Machines | ASUS ProArt P16 and P14 and a ProArt mini PC; MSI Prestige N16 Flip AI+ and EdgeMesa N AI+; Microsoft Surface Laptop Ultra and the **Surface RTX Spark Dev Box** (128 GB, US, Microsoft.com only, "later this year"); HP OmniBook; Lenovo Yoga; Dell. Acer and Gigabyte later | eight OEM boxes |
| Availability | "As early as October 2026", no exact date; ASUS and MSI first batches pre-sold to distributors; one report puts the launch on 7 October | shipping |

### 1.2 How much of the memory the GPU gets

This is the number the local profile lives or dies by, and it differs from Linux. NVIDIA's porting guide (Unified Memory Architecture) splits physical memory into three regions: a **dedicated carveout** that Windows reports as dedicated GPU memory, **shared system memory** that both CPU and GPU use, and **CPU-only** memory the GPU cannot allocate from. Its rule, quoted:

> "The nominal shared system memory size is the post-carveout capacity minus 16 GB. That value is clamped to a minimum of 50% and a maximum of 80% of the post-carveout capacity."

The GPU budget is the carveout plus the shared size. Computed from that rule (the 128 GB rows match vramcalculator.com's table; the others are this spec's arithmetic):

| Installed | Carveout | Shared | **GPU budget** | CPU-only |
|---|---|---|---|---|
| 128 GB | 0 | 102.4 | **102.4 GB** | 25.6 |
| 128 GB | 16 | 89.6 | **105.6 GB** | 22.4 |
| 128 GB | 32 | 76.8 | **108.8 GB** | 19.2 |
| 128 GB | 48–96 | 64.0–25.6 | **112.0 GB** | 16.0 |
| 64 GB (if it exists) | 0 | 48.0 | **48.0 GB** | 16.0 |
| 32 GB (if it exists) | 0 | 16.0 | **16.0 GB** | 16.0 |

Who sets the carveout (firmware, a vendor tool, or Windows' new unified-memory setting, which VideoCardz reported but which could not be read) is not documented in what could be read. **Measure it on the first machine.**

What the guide says matters for an inference engine:
- **`cudaMalloc` lands in the dedicated segment first** and "spills to the shared segment" when it is full. Shared allocations use smaller pages and can be slower.
- **Avoid `cudaMallocManaged`**: "technically supported" but on "a compatibility path that can lead to performance degradation".
- **`cudaMemGetInfo` reports dedicated plus shared**, while NVML (`nvidia-smi`) reports **dedicated only**. This is why NemoClaw's qualification read "31232 MiB total" on an N1X: tools that size a model from `nvidia-smi` will undersize it.
- And, in NVIDIA's own words: **"Allocating the full GPU budget can leave too little host memory and can make the system unresponsive."** That is the GB10 freeze this repository's host-safety layer exists for (`docs/dev/host-safety.md`), now documented by NVIDIA for Windows. §9 carries the layer across.

### 1.3 What the default model needs

The default model, `qwen3.8-27b-nvfp4-dflash2`, is served by SGLang with `--mem-fraction-static 0.50` plus a 24 GB container headroom (`hardware/model_matrix_registry.py`). On the GB10 that is about 60 GiB plus headroom, and leaves about 38.7 GB of host memory free while serving. Its weights are about 20 GB. On a 128 GB RTX Spark, a GPU budget of 102–112 GB holds it with the same context. A 64 GB machine would hold the weights and a shorter context. A 32 GB machine is a client (§6).

---

## 2. What was checked, and what was not

**Read from source here (2026-10-06):**

| Question | Result |
|---|---|
| Does upstream Codex build for Windows Arm64? | Yes. `codex/.github/workflows/rust-release-windows.yml` builds `aarch64-pc-windows-msvc` in three bundles: `codex`, `codex-code-mode-host` and `codex-responses-api-proxy`; the sandbox helpers `codex-windows-sandbox-setup`, `codex-windows-sandbox-service` and `codex-command-runner`; and `codex-app-server`. It runs on the upstream vendor's own larger runners, linking with LLVM through `.github/actions/setup-msvc-env`. |
| Is there a prebuilt V8 for it? | Yes. `third_party/v8/rusty_v8_150_4_0_release_manifests.sha256` pins `rusty_v8_ptrcomp_sandbox_release_aarch64-pc-windows-msvc`. On Windows the archive is `rusty_v8_<profile>_<target>.lib.gz` with no `lib` prefix (`.github/actions/setup-rusty-v8/action.yml` line 26), not the `librusty_v8_…a.gz` the builder asks for. |
| What does Codex's Windows sandbox do? | Three levels (`protocol/src/config_types.rs`: `Disabled`, `RestrictedToken`, `Elevated`; config `[windows] sandbox = "unelevated" \| "elevated" \| "mxc"`). **The default is `Disabled`**, and the TUI asks once to set one up. *Elevated* creates two local accounts, `CodexSandboxOffline` and `CodexSandboxOnline` (`windows-sandbox-rs/src/setup.rs:53`), and installs firewall rules and WFP filters that block the offline account's non-loopback traffic, plus its loopback traffic except proxy ports (`setup_provisioning/firewall.rs`, `wfp.rs`). *Unelevated* uses a restricted token, and its "no network" is only environment variables pointing proxies at `127.0.0.1:9` (`env.rs`, `apply_no_network_to_env`). *MXC* is Microsoft Execution Containers (AppContainer), pinned from `github.com/microsoft/mxc`. |
| How does Codex run hooks on Windows? | Through `cmd.exe` (`COMSPEC`); `hooks/src/engine/command_runner_tests.rs` tests a quoted hook command path there. |
| Does Codex use the Responses API only? | Yes: `wire_api = "chat"` gives an explanatory error in 0.158 (`model-provider-info` tests). Any local server Mightling uses on Windows must serve `/v1/responses`. |
| Do the code index tools exist for Windows Arm64? | **codebase-memory-mcp v0.11.0 ships `windows-arm64.zip`** (checked with `gh release view` today; MIGHTLING_NODE §2 said a Windows build was unconfirmed, which this corrects). The scip CLI v0.10.0 and scip-go v0.2.7 ship **no Windows build**. |
| Which parts of this repository assume Linux? | §15, file by file. |

**Read on the web, not tested:** everything in §1; CUDA working inside WSL2 on an N1X (NemoClaw issue #9000, 2026-08-13: `docker run --gpus all … vectoradd` passed through Docker Desktop's WSL backend, GPU through `/dev/dxg`); NemoClaw serving Qwen 3.6 35B-A3B on N1X through Ollama on the Windows host and an "experimental managed llama.cpp recipe" in WSL (release notes v0.0.119–v0.0.130, September 2026); Ollama's Windows Arm64 CUDA support (v0.32.3, July 2026) and its `/v1/responses` (from 0.13.3); `llama-server`'s `/v1/responses` (translated to chat completions); GitHub's free `windows-11-arm` runners for public repositories; WSL's defaults (VM memory 50% of RAM, swap 25%, `networkingMode = mirrored`); Smart App Control's blocking; Azure Artifact Signing's price and eligibility.

**Not checked by anyone here, and so the first work of each phase (§19):** any build of any Mightling binary for Windows; anything on RTX Spark hardware; whether SGLang or vLLM run in WSL2 on an N1X; the carveout setting; `mdns-sd` beside Windows' own mDNS responder; whether the sandbox accounts can read `%USERPROFILE%\.mightling`; WebView2's handling of `target="_blank"` in `ling-app`.

---

## 3. Goals and non-goals

**Goals**
- A person with an RTX Spark laptop runs `irm …/install.ps1 | iex`, and then `ling` works: against a DGX Spark on their LAN, or against the laptop's own GPU.
- The privacy story holds on Windows: no upstream vendor channel, `/airgapped on` enforced by the kernel, and an egress audit that proves it (§14).
- The same agent, prompts, skills, code index answers and `/apps` as on Linux.
- Nothing new for Linux users: every change is gated by `cfg(windows)` or is a portable fix that Linux tests cover.

**Non-goals (this spec)**
- Windows on x86-64 as a first target (decision 2).
- A Windows *node* for other machines on the LAN. The local profile serves the laptop itself; publishing it (`node enable` on Windows) is Phase 6 at the earliest.
- Running Mightling's Python (`ling-admin`) on Windows.
- Linux on RTX Spark laptops. When NVIDIA ships it, they are GB10s and today's Linux node applies (the GB10-machines branch, `gb10/all-machines`, covers detection).
- The NPU.

---

## 4. What exists on Windows already

| Piece | State | Source |
|---|---|---|
| Codex itself, its TUI, `exec`, `app-server` | Built upstream for `aarch64-pc-windows-msvc` | §2 |
| Codex's Windows sandbox (three levels) | Upstream, compiled in on Windows | §2 |
| `mdns-sd` | Pure Rust, documented for Windows | MIGHTLING_NODE §2 |
| `node-locator` (and its two byte-identical copies) | Already reads `USERPROFILE` when `HOME` is absent (`ling-rs/node-locator/src/lib.rs:205`) | read here |
| Night Shift's task lock in the launcher | `std::fs::File::lock`, which is cross-platform (`ling-rs/src/night.rs:504`) | read here |
| Tauri 2 | Builds `aarch64-pc-windows-msvc` with WebView2, which ships with Windows 11 | Tauri docs |
| codebase-memory-mcp | `windows-arm64.zip` release asset | §2 |
| rust-analyzer, Node.js, Git for Windows | Native Arm64 Windows builds published upstream | (not re-checked today) |
| CI | `windows-11-arm` hosted runners: free, public repositories only, 4 vCPU. This repository became public on 2026-10-06 | GitHub changelog |

---

## 5. Components, one by one

What each part of Mightling needs on Windows, with the phase that delivers it (§19).

| Component | Linux today | Windows | Phase |
|---|---|---|---|
| `ling` (Codex + `ling-rs/` + patches) | built by `CodexBrandedBuilder` | same builder on a `windows-11-arm` runner, with the fixes of §16.1; ships `ling.exe` plus the three sandbox helpers named as upstream names them | 1 |
| `codex-code-mode-host` | beside `ling` | `codex-code-mode-host.exe` beside `ling.exe` (Codex resolves it next to its own executable) | 1 |
| `ling-search`, `ling-fetch` | `ling-web-rs/` | same crate; `airgapped.rs` copy gets the path fixes of §15 | 1 |
| `ling-code` (queries) | SQLite reads | portable once its paths read `USERPROFILE` | 1 |
| `ling-code` (indexing) | bwrap + `systemd-run` + cgroup slice + `flock` (`src/index/host.rs`, `index/mod.rs`) | a `WindowsHost` behind the existing `Host` trait: a Job Object with a memory cap and below-normal priority, no executing indexers (§12) | 5 |
| Sandbox | bubblewrap (Codex's Linux helper) | Codex's elevated Windows sandbox, set up once with one UAC prompt (§7) | 2 |
| `/airgapped on` enforcement | patch `0019` in `linux-sandbox` | patch `0024` in `windows-sandbox-rs` (§7.3) | 2 |
| Egress audit | `ling-admin audit egress` (Python, strace) | `ling audit egress` in Rust over ETW, elevated (§14) | 2 |
| Model server | Docker + SGLang/vLLM via `ling-admin server start` | Phase 1: a node on the LAN. Phase 3: any local Responses-API server. Phase 4: Mightling-managed W1 or W2 (§8) | 1, 3, 4 |
| Host safety | `check_host_safety` + PSI watchdog | a Rust watchdog over Windows' memory notifications and DXGI budgets, plus engine-side caps (§9) | 4 |
| Web search backend | SearXNG container on the node | the node's SearXNG; on a lone laptop, SearXNG inside the W2 distribution, or no search with a clear message (§10) | 1, 4 |
| `ling-app` Chat (Onyx) | Onyx containers on the node | the node's Onyx through the forwarder; on a lone laptop, only with W2 (§11) | 5 |
| `ling-app` Work | `ling app-server` | works natively; WebView2 instead of WebKitGTK (§11) | 5 |
| `/apps` (Gmail, Drive, Calendar) | the Google service container (port 8767) on the node | from a client: as MIGHTLING_NODE §15.2 question 7 (out of scope); on a lone laptop, only with W2 | 6 |
| Night Shift | Python runner + systemd timer and scopes | a Rust runner (`ling night run`) under Task Scheduler and Job Objects, or none (§13) | 6 |
| Skills from other agents | symlinks under `~/.mightling/skills/from-*` | directory junctions, which need no privilege (§15) | 1 |
| `ling update` | Linux only (`update.rs:133`) | Windows asset names, rename-aside replacement of a running `.exe` (§15) | 1 |
| Installer | `install.sh` | `install.ps1` (§16) | 1 |

---

## 6. Install profiles

| Profile | Machine | What installs | How it is chosen |
|---|---|---|---|
| **client** | any Windows 11 Arm64 PC; an RTX Spark whose GPU budget is under the threshold below | the Rust client, the sandbox set up, `ling node` finds a node on the LAN | the default |
| **local** | an RTX Spark with a GPU budget of at least **48 GB** (Phase 4 measures and may raise it) | client plus the local engine (§8) and the watchdog (§9) | `install.ps1` reads the GPU name and `cudaMemGetInfo`'s total through a small probe in `ling.exe` (`ling doctor gpu`); NVML alone would undersize it (§1.2) |
| **both** | local, plus a node elsewhere for when the laptop is busy or asleep | the launcher's resolution tiers (MIGHTLING_NODE §6.1) pick the loopback engine first, and a remembered node when loopback is down | automatic |

`install.ps1 -Role client|local` overrides the detection, as `install.sh --role` does.

`install.ps1` must run under **Windows PowerShell 5.1**, the only PowerShell a standard Windows 11
machine has (an RTX Spark laptop such as the ASUS ProArt P16), not only under PowerShell 7: no `??`,
`?.`, ternaries, `&&`/`||` between commands or `-Parallel`, and `Invoke-WebRequest -UseBasicParsing`.
The Surface RTX Spark Dev Box ships PowerShell 7 as its default shell, with Developer Mode on, so a
check run only there would hide both a 5.1 incompatibility and anything that needs Developer Mode
(symbolic links, which is why skills use junctions, §15). Phase 1's checks run the installer under
5.1 on a machine with Developer Mode off (added 2026-10-07).

The resolution tiers need one change: today "a node → loopback" means a machine with `~/.config/dreamference/node-id`. A Windows local install writes no node id (it is not advertised), so the tiers gain a *local engine* entry: `%USERPROFILE%\.config\dreamference\engine.json`, naming the loopback URL of the engine Mightling manages. It is tried before the remembered node and is never advertised.

---

## 7. The sandbox and `/airgapped`

### 7.1 Codex on Windows runs commands unsandboxed until someone sets a sandbox up

`WindowsSandboxLevel::Disabled` is the default (§2). The TUI's first permission prompt offers "Set up default sandbox (requires Administrator permissions)", "Use non-admin sandbox (higher risk if prompt injected)" or "Quit" (`tui/src/chatwidget/windows_sandbox_prompts.rs`). `ling exec`, Night Shift and SWE-bench-style runs have no TUI to ask.

**Mightling sets the sandbox up at install, not at first use:**
- `install.ps1` runs `codex-windows-sandbox-setup.exe` behind one UAC prompt. That one elevation also adds the firewall rule for mDNS (§10.2), so the user sees one prompt, at a moment they expect it.
- The launcher then writes `[windows] sandbox = "elevated"` into `$CODEX_HOME\config.toml`, only when absent, as it does for `network_access` (`lib.rs:790`).
- When the setup failed or was declined, the launcher prints at every start that commands run without a sandbox, and `/airgapped on` is refused (§7.4).

### 7.2 What the elevated sandbox gives, read from source

- Commands run as `CodexSandboxOnline` or `CodexSandboxOffline`, local accounts that setup creates. **They are shared with any upstream Codex on the same machine**: `service_identity.rs` keeps "each packaged channel's service and pipe separate while accounts remain shared". A Mightling install and an Codex install coexist, and neither sees the other's `CODEX_HOME`.
- **Filesystem:** writes are allowed only to roots that setup grants to the sandbox accounts through ACLs (`workspace_acl.rs`, `acl.rs`); reads are granted per policy (`core/src/windows_sandbox_read_grants.rs`).
- **Network:** the offline account is blocked by Windows Firewall rules scoped to its SID and by WFP filters on `ALE_USER_ID` (`setup_provisioning/firewall.rs`, `wfp.rs`). Both are kernel-enforced and persistent. Rule names say "Codex Sandbox Offline …" in the firewall's interface. Renaming them would be a patch for no protection, so this spec leaves them.

**To verify on the first machine:** whether `CodexSandboxOnline` can read `%USERPROFILE%\.mightling\node.json` (needed by `ling-search`) and execute `ling-search.exe` from the install directory; whether `%TEMP%` is a writable root.

### 7.3 Enforcing `on`: patch `0024`

On Linux, `0019` makes the sandbox helper force `NetworkSandboxPolicy::Restricted` when the command's session is sealed, reading the session from the command's own environment (`CODEX_THREAD_ID`, then `CODEX_SESSION_ID`). The Windows equivalent:

- **Where:** the two *elevated* entry points, each of which resolves the profile itself with the command's `env_map` in hand: `windows-sandbox-rs/src/unified_exec/backends/elevated.rs:178` (`spawn_windows_sandbox_session_elevated_for_permission_profile`, interactive and unified exec) and `elevated_impl.rs:120` (`run_windows_sandbox_capture_for_permission_profile`, captured commands). There is no single function with both the profile and the command's environment: `ResolvedWindowsSandboxPermissions::try_from_permission_profile`, which every path calls, sees only the parent's environment, where `CODEX_THREAD_ID` is not set. The legacy (unelevated) path, `spawn_prep.rs`'s `prepare_spawn_context_common`, is not hooked, because §7.4 refuses it at `on`.
- **What:** one helper added to `resolved_permissions.rs`. It takes the profile and the `env_map`, and returns the profile unchanged, or a clone with `network = Restricted` when `ling_airgapped::resolve(&[thread, session]).level == On` for the ids in `env_map` (`CODEX_THREAD_ID`, then `CODEX_SESSION_ID`, as on Linux). Each of the two call sites passes the helper's result instead of `permission_profile`. The elevated setup then selects the offline identity (`SandboxNetworkIdentity::from_permissions`, `setup.rs:804`). Before writing it, confirm that Codex puts `CODEX_THREAD_ID` into the Windows command's environment: it does in `core/src/unified_exec/process_manager.rs:1453`, but the capture path was not traced.
- **Size:** a dependency line in `windows-sandbox-rs/Cargo.toml`, the helper (about six lines) and two changed call lines: an estimated 1.3–1.8 KB. The series is 37,288 bytes under a 37,500 cap, with 38,500 approved (2026-10-05), so this passes the approved ceiling. That is the user's call (§21, question 3).
- **Seals:** the resolver needs a place for seals that a sandboxed command cannot write. On Windows that is `%LOCALAPPDATA%\Mightling\airgapped-seals`: the sandbox accounts are other users, and setup grants them nothing there. `runtime_dir()` in `ling-rs/airgapped/src/lib.rs` gains a `cfg(windows)` branch, and the web crate's byte-identical copy follows (the test that compares them enforces it). Seal pruning needs process liveness without `/proc` (`airgapped.rs:79`): `OpenProcess` plus `GetExitCodeProcess`.

### 7.4 What is refused at `on`

| Sandbox level | At `on` |
|---|---|
| elevated | enforced (offline account) |
| unelevated (restricted token) | **refused**, like Full Access: "airgapped is on, and the non-admin sandbox cannot take a command's network away" |
| disabled (no sandbox) | **refused** |
| `mxc` | refused for now: MXC's repository says no profile "should be treated as security boundaries currently" |
| Full Access | refused, as today (`0019`, `0023`) |

Full Access stays refused by `permission_refusal` (`0023`'s validator), which binds every `app-server` client. The sandbox-level refusals need the configured `[windows] sandbox`, which the validator does not see. They run in the launcher (at start, from `config.toml` and `-c`) and in `/airgapped on` (patch `0019`'s TUI hook calls into `ling-rs/src/airgapped.rs`). An `app-server` started by the launcher inherits the start-time check. One started some other way with an unelevated sandbox would not be refused, and its commands would only get proxy variables. Closing that gap means extending `0023`'s validator to the sandbox mode: about 0.3 KB more, listed with question 3.

### 7.5 Wording

The sandbox prompt and setup errors say "Codex" (`windows_sandbox_prompts.rs`). `0001` already renames what is visible on Linux. On Windows these lines are visible too: about 0.5 KB of branding, or a launcher-side setup that pre-empts the prompt so it never shows (§7.1 does that). **Proposed: pre-empt, no branding patch.** If the prompt can still appear (setup failed), it says Codex, and the start-up line explains.

### 7.6 What Phase 2 built (2026-10-07)

- **`0024-airgapped-windows`, 2,042 bytes** (series 39,330 on its own, cap 39,500; 42,741 with `0025-node-slash-command` once both were merged, cap 43,000). As §7.3 says, with one difference in the resolver: `ling_airgapped::sealed_for_env` takes the command's environment map and reads the level variable from it too, falling back to this process's, because the sandbox resolves a command's permissions in its own process before the command starts. That `CODEX_THREAD_ID` reaches the capture path was confirmed: `core/src/exec_env.rs` `create_env` puts it in every command's environment. `spawn_windows_sandbox_session_for_level` (also what `puffin sandbox` uses) dispatches to the hooked elevated function, and the legacy path's `prepare_spawn_context_common` is the only other resolver of a profile for a command, so there is no third site at 0.158.
- **The refusals** (`puffin-rs/src/airgapped.rs`): the sandbox is read as Codex reads it (`[windows] sandbox`, else the legacy features, `-c` last), and whether it is set up by Codex's own `sandbox_setup_is_complete`. At `on`, only the elevated sandbox, set up, starts; the unelevated, MXC and no sandbox are refused at start and by `/airgapped on` (and `puffin airgapped default on`), each with the remedy. `/airgapped on` inside a session reads the configuration files only: a `-c windows.sandbox=…` given at start is not visible there, which errs towards refusing. `0023`'s validator was **not** extended (the 0.3 KB option): an app server started without the launcher, with the unelevated sandbox, is still not refused.
- **`[windows] sandbox = "elevated"`** is written only once the setup is complete, not whenever absent: selected and not set up, the first command would run the setup itself behind its own prompt, which says Codex. `ling sandbox setup` writes it too. Without a set-up sandbox every start says that commands run without one.
- **`install.ps1`**: one elevated PowerShell (no prompt when already elevated) runs `ling sandbox setup --elevated --user <DOMAIN\user> --codex-home <home>`, named by the unelevated script, because with an administrator's credentials typed over a standard user's shoulder the elevated process is the administrator; then the inbound UDP 5353 rules `Mightling-mDNS-ling` and `Mightling-mDNS-ling-app` (Private, Domain), replaced on every install; then `ling audit egress`. `-NoSandbox` skips it; a declined prompt leaves the client working unsandboxed.
- **`ling audit egress`** (`ling-rs/src/audit.rs`, `ferrisetw` 1.2): one ETW session with Kernel-Process (ProcessStart), Kernel-Network (TCP send and connect, UDP send; the port is in network byte order) and DNS-Client (3006). The verdict is the Linux one (the model server, loopback 8767 and 8888; port 9, any lookup but `localhost`, or a `git-remote-http(s)`/`ssh` start fails), and a session that replied with no connection to the model server recorded is a failed trace, not a pass, so a misread event cannot pass. The model server is taken from configuration or the remembered node, never from a browse. On Linux the command points at `ling-admin audit egress`.
- **Assets:** `codex-windows-sandbox-service` (§16.2) is not shipped: the elevated path without the packaged service registration does not look for it (`helper_materialization.rs` names the setup helper and the command runner only).

- **Open: TLS inside the elevated sandbox.** On GitHub's Windows runners (run 37639756174), Schannel fails
  with `SEC_E_NO_CREDENTIALS` inside the elevated sandbox, so at `/airgapped off` a command the agent runs
  that uses Windows' own TLS (`curl.exe`, probably PowerShell's `Invoke-WebRequest`) cannot make an https
  request: it connects, then fails. It comes from the upstream Windows sandbox (the offline/online sandbox
  accounts have no credentials Schannel can acquire), not from Phase 2; the CI probe at `off` uses plain
  HTTP for that reason. `ling-search` and `ling-fetch` use rustls and are probably unaffected, which is not
  checked yet. Decided 2026-10-07: investigate on the real laptop (§18).

---

## 8. The model on the laptop

### 8.1 What the engine must provide

Read from the launcher and `ling-code`:

| Need | Why | SGLang/vLLM | `llama-server` | Ollama |
|---|---|---|---|---|
| `POST /v1/responses` | Codex 0.158 speaks nothing else | yes | yes (translated to chat completions) | yes, from 0.13.3 |
| `GET /v1/models` with `max_model_len` | the catalogue's context (`lib.rs:574`; falls back to 32,768) | yes | **no**: the context is in `/props` (`n_ctx`), to be read as a fallback | **no**: `/api/show` has `context_length`, to be read as a fallback |
| `/metrics` busy gauges | `ling-code` waits for an idle model (`index/probe.rs:40`); the compaction limit reads the KV pool (`compaction.rs:72`) | yes | with `--metrics`; gauge names (`llamacpp:requests_processing`) to be added to `GAUGES` | none: idle detection degrades to "unknown", which must not block indexing forever |
| Qwen3 tool calls (`qwen3_coder`) and reasoning split | the model's tool calls and `/think` text | parsers configured per recipe | Jinja template plus llama.cpp's tool-call parsing: **to measure** with `ling exec` | to measure |
| Chat-template patches (`chat_template_patches`) | Codex's `high`/`minimal` efforts were answered with HTTP 400 | patched copy at launch | `--chat-template-file` with the same patched copy | Modelfile `TEMPLATE`, a different template language: **not portable** |

### 8.2 Phase 3: bring your own server

The cheapest useful step, and testable on any Windows Arm PC with enough memory: the launcher already accepts any URL through `DREAMFERENCE_VLLM_HOST` or `vllm_host`. Phase 3 adds:
- the two context fallbacks above, and llama.cpp's gauges;
- detection at start of Ollama (11434), LM Studio (1234) and `llama-server` (8080) on loopback, offered once ("found Ollama at 127.0.0.1:11434 serving qwen3.8:27b; use it? `ling node use local:ollama`"), never adopted silently (MIGHTLING_NODE §6.1's rule);
- a start-up line naming what is unmeasured: tool calls on a template Mightling did not patch.

### 8.3 Phase 4: a Mightling-managed engine, W1 (W2 not built: no WSL, decided 2026-10-07)

| | **W1: native `llama-server`** | **W2: SGLang in a Mightling WSL2 distribution** |
|---|---|---|
| What runs | `llama-server.exe` built by Mightling's release for `aarch64-pc-windows-msvc` with CUDA 13.4 (`sm_121` to be confirmed on N1X), as a child of a Mightling engine supervisor | a WSL2 distribution `Mightling` (Ubuntu 24.04 arm64, imported from a tarball the release ships), Docker Engine inside it, and today's node: `ling-admin server start` with the pinned `lmsysorg/sglang` image and the NVFP4 + DFlash2 recipe |
| Model | GGUF: Qwen3.8-27B `Q4_K_M` (about 18 GB; Ollama and llama.cpp support the architecture). **Decided 2026-10-07: Qwen3.8-27B is the default on Windows too**, the same model as the node (§21, question 6) | the registry's default entry, unchanged |
| Speed (expected, not measured) | dense 27B: bandwidth-bound, at most about 300 GB/s ÷ 18 GB ≈ 17 tok/s before speculation; an MoE with ~3 B active is several times faster | the GB10 numbers (prose 25.5, code 50.3, JSON 87.0 tok/s), if GPU paravirtualisation costs little; **unknown** |
| GPU access | native WDDM; `cudaMalloc` as NVIDIA recommends (§1.2) | `/dev/dxg` paravirtualisation; CUDA on WSL lists limited UVM and "pinned system memory … availability is limited" (CUDA on WSL guide §5.1). NemoClaw's notes say its WSL path on N1X "still breaks" in QA |
| Memory accounting | one Windows process: Job Object and DXGI budgets apply directly (§9) | two layers: the VM's RAM cap (`.wslconfig` `memory`, default 50%), and GPU allocations made through the host driver outside it. **To measure** |
| Dependencies | none beyond the NVIDIA driver | WSL2 (built into Windows 11), a 3–5 GB distribution, about 15 GB of images. **No Docker Desktop**: Docker Engine inside the distribution avoids Docker Desktop's subscription requirement for larger companies. NemoClaw proved the GPU path only through Docker Desktop, so Docker Engine plus NVIDIA's container toolkit in a distribution is the first thing W2 measures |
| What else it brings | nothing | SearXNG, Onyx, the Google service and Night Shift's Python runner, all unchanged in the distribution: the whole node, with the client on Windows |
| Networking | loopback | WSL `localhost` forwarding (default), or `networkingMode = mirrored` (Windows 11 22H2+) |
| Air gap | the engine is a host process; `/airgapped` does not govern it (as on Linux, where the model server is outside the sandbox too) | the same, plus the distribution's own egress, which the audit must cover (§14) |
| Updates | one binary in the release | an image digest and a distribution tarball |

**How to choose (Phase 4's gate):** on one 128 GB RTX Spark, for each engine:
1. `server start` to first token, cold and warm;
2. the SWE-bench-free benchmark the registry used (prose, code, JSON; single stream; temperature 0);
3. `ling exec` on the slash-command suite (tool calls, efforts);
4. four concurrent `ling` tasks;
5. the memory test of §9.4.

**Decided 2026-10-07:** W1 for every local install; W2 is not built. (Proposed before the decision: W1 for every local install, because it has no WSL, no images and no second memory layer; W2 as an opt-in "full node on this laptop" (`-Role local -Engine wsl`) for people who want the GB10 recipe, Chat and Night Shift. If W2 turns out within 10% of the GB10's speed and passes §9.4, the default is revisited: speed is the product.)

### 8.4 Which model on which machine

The registry gains Windows entries, never by renaming existing ones: `qwen3.8-27b-gguf-q4km` for W1 (min memory 24 GB), and the existing default for W2. `main-model set` on Windows is a launcher command (`ling model set`), because there is no `ling-admin`. `model_supports_vision` carries over: Qwen3.8 is multimodal in both formats, but llama.cpp needs its `mmproj` file, which W1 downloads beside the GGUF.

---

## 9. Host safety on Windows

### 9.1 What does not exist

There is no PSI, no earlyoom or systemd-oomd, no `sysctl`, no cgroup and no `swap.img` (`check_host_safety`, `psi_watchdog.py` and `host_safety_setup.py` are all Linux). Windows' own behaviour under memory exhaustion is paging to `pagefile.sys` and trimming working sets, which is the slow death the GB10 watchdog exists to cut short. NVIDIA's warning (§1.2) says the freeze is real on this hardware too.

### 9.2 The layers, carried across

| GB10 (Linux) | RTX Spark (W1) | RTX Spark (W2) |
|---|---|---|
| pre-flight: swap, sysctl, OOM daemon present | pre-flight: free physical memory (`GlobalMemoryStatusEx`), the GPU budget (`cudaMemGetInfo` through the probe), the pagefile size, and other GPU-heavy processes (DXGI `QueryVideoMemoryInfo` per adapter) | the same on the host, plus the VM's `.wslconfig` `memory` against the model's need |
| engine sized from the matrix (`gpu_memory_utilization`, headroom) | `llama-server` with a fixed context (`-c`) and KV size, so its footprint is known before load; **never** sized to the whole GPU budget (NVIDIA: avoid allocating it all) | `gpu_memory_utilization` recomputed against the Windows GPU budget, not 128 GB |
| PSI watchdog kills the container (SIGKILL, dockerd socket, CLI) | a watchdog thread in the engine supervisor: `CreateMemoryResourceNotification(LowMemoryResourceNotification)` plus a 1 s poll of available memory and of the process's DXGI usage against its budget; trips terminate the engine's **Job Object** (`TerminateJobObject`, which needs no fork and no shell) | the same host-side watchdog; the kill is `wsl --terminate Mightling`, which takes the whole VM down: blunt, but certain |
| container memory cap | Job Object `JOB_OBJECT_LIMIT_JOB_MEMORY` on the engine. It caps committed CPU memory only: Microsoft says job limits are not a limit on GPU memory, hence the DXGI check | `.wslconfig` `memory` |

### 9.3 Trip thresholds

- **Start:** available physical memory under 8 GB, or the engine's DXGI usage above 90% of its budget, for 3 s.
- **Then measure and adjust**, as `psi_watchdog.py`'s thresholds were. Laptops also have Windows itself (several GB at idle), Defender and a browser beside the engine.

### 9.4 The test that must pass before `local` is offered

On a 128 GB RTX Spark: start the engine, then allocate host memory in a second process in 1 GB steps until the watchdog trips. Pass: the engine is killed, the desktop stays responsive (input latency measured), and nothing else is killed. Then repeat with the engine allocating KV past its budget (`-c` far above the planned size). This is the Windows version of the load that froze the GB10.

---

## 10. Search, discovery and the network

### 10.1 Web search

`ling-search` queries a SearXNG instance: on a client, the node's (MIGHTLING_NODE §4). A lone laptop with W1 has none. Options:
1. no search, with the error naming why;
2. SearXNG in the W2 distribution only;
3. SearXNG as a native Windows process, which upstream does not support.

**Proposed:** (1) for W1 and (2) for W2. The prompt's web block is then left out when no search backend answers, exactly as the Gmail block is left out when no account is connected (`lib.rs`, `gmail_access_instructions`), so the model is never told about a command that cannot work.

### 10.2 Discovery and the firewall

`mdns-sd` binds UDP 5353 and receives multicast answers. Windows Defender Firewall asks the first time an unknown program listens. On a network marked Public the default answer blocks it, and discovery then fails silently. The install's one elevation (§7.1) adds an inbound rule for `ling.exe` and `ling-app.exe` on UDP 5353, Private and Domain profiles only. On a Public network, `ling node use <address>` remains (MIGHTLING_NODE §6.3). Whether `mdns-sd` coexists with Windows' own mDNS responder (the DNS Client service also listens on 5353) is unverified (MIGHTLING_NODE §2) and is Phase 1's first test.

---

## 11. `ling-app` on Windows

- **Builds** with `tauri build --target aarch64-pc-windows-msvc`. The bundle is NSIS (the installer itself runs emulated under Prism; the app is native) or MSI; NSIS, because it installs per user without elevation.
- **The webview is WebView2 (Chromium).** Everything `main.rs` sets for WebKitGTK (`WEBKIT_DISABLE_DMABUF_RENDERER`, `GTK_THEME`) is inert there.
- **Theme:** Chat's overrides are `html:not(.dark)`, so the window needs the light scheme. On Windows that comes from the window's `theme: "Light"` in `tauri.conf.json`, which Tauri passes to WebView2's preferred colour scheme. To verify.
- **The injected scrollbar** (`onyx_ui_scripts.py`) refuses to run on Blink and leaves the native bar, which is correct for WebView2.
- **`target="_blank"`:** wry handles new-window requests differently on WebView2. Whether a link opens a window, the browser, or nothing is to be checked in Phase 5, and the engine marker extended with `webview2`.
- **Chat** needs Onyx: the node's (through the forwarder, unchanged), or W2's. With W1 alone, `ling app` opens Work only and says why there is no Chat.
- **Work** drives `ling app-server` over stdio, which is native, and `0023` already binds its permission choices.
- **Finding the app:** `app.rs` reads a `.desktop` entry (`find_executable`). On Windows it looks beside `ling.exe`, then under `%LOCALAPPDATA%\Programs\Mightling`, then the NSIS uninstall key in `HKCU`.
- **Webview data:** `%LOCALAPPDATA%\dev.dreamference.mightling\EBWebView`.

---

## 12. The code index on Windows

- **Queries** read SQLite and work once `paths::home()` reads `USERPROFILE` (`ling-code-rs/src/paths.rs:118` falls back to `/`).
- **The universal layer:** codebase-memory-mcp `windows-arm64.zip` is pinned in `code-index.sha256` beside the Linux line, with its own checksum, and installed by `ling code setup` (Rust, MIGHTLING_NODE §8.3).
- **The exact layer:** the scip CLI has no Windows build. It is Go; Mightling's release can build it (`GOOS=windows GOARCH=arm64`) from the pinned v0.10.0 tag and publish it as a release asset with a checksum. **Proposed**, because without `scip` there is no exact layer at all, and the exact layer is the part of the index the SWE-bench runs used.
- **Indexers that read source** (scip-python and scip-typescript under Node Arm64, codebase-memory) run in a Job Object with a memory cap (a quarter of RAM on a client, as MIGHTLING_NODE §8.3 says) at `BELOW_NORMAL_PRIORITY_CLASS`.
- **Indexers that execute the project's build** (rust-analyzer, scip-java, scip-dotnet) **do not run**, as on macOS. Windows can contain them: the Codex sandbox's offline account, or an AppContainer, is a real filesystem and network boundary, and running them under the offline account is Phase 6.
- **Code changes:** `src/index/host.rs` is behind a `Host` trait with a test fake; a `WindowsHost` implements it. `index/mod.rs` (`libc::setsid`, `libc::kill`) and `host.rs`'s `flock` move behind `cfg(unix)`, with `CREATE_NEW_PROCESS_GROUP`, `OpenProcess` and `LockFileEx` (or `std::fs::File::lock`) on Windows.

---

## 13. Night Shift on Windows

The runner is Python (`dreamference/night_shift/`) under a systemd timer, with each task in a memory-capped scope, `ling sandbox` for its test run, and `flock`. With W2, the distribution runs it unchanged against repositories *inside* the distribution. Repositories on `C:\` reached through `/mnt/c` are slow, and `ling` there would be the Linux binary.

**Proposed, Phase 6:** a Rust runner, `ling night run`, in the launcher. It reuses `night.rs`'s task format and lock, and runs each task's `ling exec` in a Job Object, with the elevated sandbox for the test run. Task Scheduler (`schtasks /create … /sc daily`, per user, no elevation) replaces the timer. Until then, `/night add` on Windows queues the task and says no runner exists on this machine; `/night add --on <node>` is the way (sending from a client is MIGHTLING_NODE §15.2 question 9).

---

## 14. The egress audit on Windows

`ling-admin audit egress` is strace and Python. The Windows version is `ling audit egress`, in Rust, because there is no Python on the client.

- **Tracing:** an ETW real-time session (the `ferrisetw` crate) with Microsoft-Windows-Kernel-Network (TCP/UDP connect, send and accept, with process ids), Microsoft-Windows-DNS-Client (every name lookup, which strace could not see from `sendmmsg`) and Microsoft-Windows-Kernel-Process (process starts, to follow the session's tree, including commands spawned through the sandbox accounts).
- **Verdict:** the same as Linux. Every destination is an allow-listed loopback port or the node; no DNS query; no networked git; exit 0, 1 or 2.
- **Needs elevation:** a kernel ETW session needs Administrator or the Performance Log Users group. The audit runs elevated, once, after `ling update` (as `codex build` runs it on Linux), and prints the verdict.
- **Windows' own traffic** (Defender, Windows Update, telemetry) is outside the process tree and so outside the verdict. The report says so, because "Mightling made no connection" is not "this laptop made no connection". **Defender's automatic sample submission can upload an unknown executable to Microsoft**, Mightling's own binaries included. Signing (§16.3) reduces that; the README says it.
- **W2:** the distribution's traffic leaves through the WSL virtual NIC, attributed to the WSL host process in Windows' events. The audit adds a strace run inside the distribution (the existing Python audit) and merges the two verdicts.

---

## 15. Launcher and crate changes, file by file

Each is a `cfg(windows)` branch or a portable fix with a Linux test. None needs a Codex patch except §7.3.

| File | Today | Change |
|---|---|---|
| `ling-rs/src/update.rs:78` `target()` | `{arch}-unknown-linux-gnu` | `{arch}-pc-windows-msvc` on Windows; asset names carry `.exe` inside the `.gz` |
| `update.rs:133` | returns unless Linux | Windows allowed |
| `update.rs` `replace()` | `rename` over the target and `chmod 0o755` | Windows cannot replace a running `.exe` by rename. Rename `ling.exe` aside to `ling.exe.old`, move the new one in, and delete `*.old` at the next start |
| `update.rs` `link_onto_path()` | symlink in `~/.local/bin` | no links: the install directory is on the user's `PATH` (`HKCU\Environment`, set by `install.ps1`) |
| `ling-rs/src/app.rs:145` `find_executable()` | `.desktop` entry under `XDG_DATA_HOME` | §11 |
| `app.rs:127` | `process_group(0)` under `cfg(unix)` | `CREATE_NEW_PROCESS_GROUP \| DETACHED_PROCESS` |
| `ling-rs/src/home.rs:85,133` | `DirBuilderExt` modes and symlink copying | skip both under Windows (ACLs of `%USERPROFILE%` already restrict); copy the allow-list without links |
| `ling-rs/airgapped/src/lib.rs:156,173` | `HOME` only | `HOME`, then `USERPROFILE`, as `node-locator` already does. **This is a correctness bug on Windows:** a user-level `mightling_airgapped = "on"` would be silently unread when `HOME` is unset (the usual case) |
| `airgapped/src/lib.rs:194` `writable_roots()` | `/tmp`, `$TMPDIR` | `%TEMP%`, `%TMP%` on Windows |
| `airgapped/src/lib.rs:229` `runtime_dir()` | `XDG_RUNTIME_DIR`, `/run/user/<uid>` | `%LOCALAPPDATA%\Mightling` (§7.3) |
| `ling-rs/src/airgapped.rs:79` | prunes seals through `/proc` | `OpenProcess` liveness |
| `airgapped.rs:208` status line | "bwrap --unshare-net" | "the Windows sandbox's offline account" on Windows |
| `ling-rs/src/compaction.rs:181` `hook_command_for()` | POSIX single quotes when the path is not plain | Codex runs hooks through `cmd.exe` on Windows, which does not understand single quotes, and a Windows path is never "plain" (`\`, `:`). Quote with double quotes on Windows. **Without this, the ledger and start-up-line hooks fail on every Windows session** |
| `ling-rs/src/lib.rs:554` | `~/.config/dreamference/config.toml` from `HOME` | the same relative path under `USERPROFILE` |
| `lib.rs` `WEB_ACCESS_INSTRUCTIONS` | shell commands with double-quoted arguments | valid in PowerShell as written; the Gmail block names `ling-admin gmail`, which does not exist on Windows, and stays out (it is only added when the service answers) |
| `lib.rs` `updated_config()` | Linux settings | also `[windows] sandbox = "elevated"` when absent (§7.1) |
| `ling-rs/skills/` | `symlink_dir` (needs Developer Mode on Windows: MIGHTLING_SKILLS §15) | directory **junctions**, which need no privilege. `std::fs::canonicalize` returns `\\?\C:\…` verbatim paths on Windows: the `[[skills.config]]` entries must use the form Codex's own loader produces, which a test compares |
| `ling-rs/src/code_index.rs:188` | `process_group` | as for `app.rs` |
| `ling-web-rs/src/airgapped.rs` | the byte-identical copy | follows the crate (the comparing test keeps them identical) |
| `ling-code-rs/src/paths.rs:118` | `HOME` or `/` | `USERPROFILE` |
| `ling-code-rs/src/index/*` | Linux host | §12 |

One helper, `home_dir()`, reading `HOME` then `USERPROFILE`, is shared by the std-only crates as `node-locator` does today, so the four copies cannot disagree.

---

## 16. Building, installing, signing

### 16.1 The builder on a Windows runner

`CodexBrandedBuilder` (Python) runs in CI only; Windows users never need Python. It needs these fixes:
- **`host_target()`** (`codex_branded_builder.py:338`) returns `{platform.machine()}-unknown-linux-gnu`. `platform.machine()` is `ARM64` on Windows. Map it to `aarch64-pc-windows-msvc`.
- **V8 asset names:** `rusty_v8_<profile>_<target>.lib.gz` on Windows, `librusty_v8_…a.gz` elsewhere (as upstream's action does).
- **Linker:** upstream configures LLVM's linker for Windows (`setup-msvc-env`); the workflow needs the MSVC Arm64 build tools on the runner image.
- **Install only what the build needs:** the workflow runs `pip install -e .`, which pulls `sentence-transformers` (PyTorch) and `tensorizer`. Their Windows Arm64 wheels are uncertain (CPython on Windows Arm64 is still tier 3; many wheels are missing). Use `pip install -e . --no-deps` plus the builder's few imports, or a `build` extra.
- **Line endings:** there is no `.gitattributes`. A Windows checkout with `core.autocrlf=true` turns `codex-patches/*.patch` to CRLF and `git apply` fails. Add `*.patch -text` and `*.sh text eol=lf`.
- **Symlinks and paths:** `PATH_LINK` and the other `~/.local/bin` links (`codex_branded_builder.py:67–95`) are skipped on Windows.
- **Time:** upstream says Windows release builds "can exceed an hour" on its large runners; `windows-11-arm` has 4 vCPUs. The job limit is 6 hours. If the build does not fit, a self-hosted Windows Arm runner (a Surface Dev Box) is the fallback.

### 16.2 Release assets

Per binary, as today: `ling-aarch64-pc-windows-msvc.gz` (holding `ling.exe`), `codex-code-mode-host-…`, `ling-search-…`, `ling-fetch-…`, `ling-code-…`, and the three sandbox helpers `codex-windows-sandbox-setup-…`, `codex-windows-sandbox-service-…` and `codex-command-runner-…`, under the names Codex looks for. One `ling-aarch64-pc-windows-msvc.sha256sums` covers them, so `install.ps1` and `ling update` share one mechanism, as `install.sh` and `update.rs` do. `ling-app` ships as an NSIS `.exe`. Codex's LICENSE and NOTICE travel with them.

### 16.3 Signing

- **Why reopen it:** MIGHTLING_NODE §15.1 decided to document the warning rather than sign. On Windows 11, Smart App Control "outright blocks any unknown, unsigned … apps"; GitHub issues show installers that cannot be run at all, with no "Run anyway". It is on or in evaluation on clean installs, which describes every new RTX Spark laptop. Without signing, a share of the target audience cannot run `ling.exe`.
- **What it costs:** Azure Artifact Signing is about $9.99 a month (5,000 signatures), with no hardware token, open to organisations in the EU and UK (individuals only in the US and Canada). A signed binary still starts without SmartScreen reputation, but Smart App Control evaluates the signature, and reputation accrues to the certificate across releases.
- **Decided 2026-10-07: unsigned for now.** Windows releases are marked preview, and their notes say Smart App Control must be off to run them; signing is revisited before Windows leaves preview. (Proposed before the decision: Dreamference applies for Artifact Signing as an organisation, and the release workflow signs every Windows `.exe` with `signtool` through the Azure action. Until then, the Windows release is marked preview, and the README says how to switch Smart App Control off, which Windows 11 now allows without a reinstall.)

### 16.4 `install.ps1`

`irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex`:
1. Checks Windows 11 on Arm64 (`$env:PROCESSOR_ARCHITECTURE`, the OS build); otherwise names the client-only or unsupported case.
2. Downloads the assets of §16.2, verifies them against the checksum file, and unpacks them into `%LOCALAPPDATA%\Programs\Mightling\bin`. No elevation.
3. Adds that directory to the user's `PATH` (`HKCU\Environment`, broadcast `WM_SETTINGCHANGE`).
4. **One UAC prompt:** sandbox setup (§7.1) plus the mDNS firewall rule (§10.2). Declining leaves a working client without a sandbox; `/airgapped on` is refused, and the launcher says so at start.
5. Decides the profile (§6), and for `local` installs the chosen engine (§8.3).
6. Runs `ling audit egress` once with that elevation (§14), and prints the verdict.

Uninstall is `ling uninstall`. It removes the install directory, the PATH entry, the firewall rule (elevated), and the WSL distribution if one was created. It leaves `%USERPROFILE%\.mightling` unless `--purge` is given. The sandbox accounts stay, because upstream Codex may use them.

---

## 17. Security and privacy differences

| Topic | Linux node | Windows |
|---|---|---|
| Sandbox | bubblewrap namespaces | separate local accounts, ACLs, firewall and WFP; set up with Administrator rights once |
| `on` | no network namespace | offline account (§7.3); unelevated and no sandbox refused (§7.4) |
| The upstream vendor channels | closed by `0013`, `0015`, `0016`; audited | the same patches; **verify by audit** that nothing Windows-only (the sandbox setup's telemetry counters in `windows_sandbox_prompts.rs`, Windows-only update paths) reaches the network |
| OS telemetry | DGX OS's own services, outside the audit | Windows telemetry and Defender sample submission, outside the audit, stated in the report (§14) |
| Local accounts | none | `CodexSandboxOffline` and `CodexSandboxOnline` exist machine-wide after setup, shared with upstream Codex |
| Model weights | in the HF cache | the same under `%USERPROFILE%\.cache\huggingface`, or in the WSL VHD for W2 |

---

## 18. Measurements and tests

**Offline, on any CI runner:**
- the path functions of §15, with `HOME` unset and `USERPROFILE` set;
- `hook_command_for` for `C:\Users\Jane Doe\…` producing a `cmd.exe`-valid line;
- the junction-based skill links;
- the seal directory under `%LOCALAPPDATA%`;
- `update.rs` asset names and rename-aside;
- the airgapped crate and its copy staying byte-identical;
- the refusal table of §7.4.

**On `windows-11-arm` (CI):**
- the whole launcher crate's tests (`cargo test --release -p ling-launcher` in the export directory);
- `ling-web-rs` tests;
- `ling-code` query tests;
- `ling --version` and `ling exec` against a stub Responses server.

**On a Windows Arm PC, no RTX Spark needed (Phase 1–2 gates):**
- install, sandbox setup and uninstall;
- discovery of a DGX Spark node, and `ling node use`;
- `ling-search` through the node from a sandboxed command;
- `/airgapped on` (a sandboxed `curl` fails) and `off` (it succeeds);
- the refusals at `on`;
- `ling audit egress` passing;
- `ling update` replacing a running `ling.exe`.

**On an RTX Spark (Phase 3–4 gates):**
- the carveout and the GPU budget as `cudaMemGetInfo` and NVML report them;
- `sm_121` confirmed (`nvidia-smi --query-gpu=compute_cap`, in WSL as well);
- Phase 3 against Ollama and `llama-server`;
- W1 and W2 through §8.3's five measurements;
- the freeze test of §9.4.

---

## 18a. Linux on RTX Spark laptops (watch, not build)

Reimaging an RTX Spark laptop with Linux is not possible yet (checked 2026-10-07):
- NVIDIA has not announced Linux for RTX Spark and would not comment.
- The public open GPU driver (615.71.09) lists GB10 but not the N1X (`10de:2e03`).
- No laptop maker has said how the firmware describes the hardware (ACPI or a device tree, which
  Snapdragon laptops needed per model), and no distribution publishes an image.

NVIDIA's NemoClaw tracker shows an internal Ubuntu 24.04 image, "N1x FastOS" (kernel 7.0, driver
615, CUDA 13.4), so a release is likely. Its issues also show a vLLM load running out of memory and
leaving the host unresponsive: the freeze Mightling's host-safety layer exists for.

When NVIDIA ships Linux for N1X, Mightling on such a laptop is the node stack almost unchanged:
- GB10 detection learns the N1X's PCI id (`hardware_manager.py`, `install.sh`);
- the default model is sized to the machine's memory, as on Windows;
- battery, sleep and thermals are added to the host checks.

Customers stay on Windows; this is a faster path for people who choose Linux, and for the test
laptop.

---

## 19. Phases

| Phase | Delivers | Needs | Gate |
|---|---|---|---|
| 0 | the fixes of §15 and §16.1 that are portable, with their Linux tests; `.gitattributes`; `cargo check` of the std-only crates for the Windows target | nothing new | Linux suite green |
| 1 | the Windows client: release jobs on `windows-11-arm` and x86-64 Windows (unsigned, preview), `install.ps1`, `update`, junction skills, client against a LAN node | a Windows Arm PC (Snapdragon is enough) | §18's client checks |
| 2 | sandbox at install, patch `0024`, the refusals, `ling audit egress` over ETW | the same PC | the audit passes; `on` blocks |
| 3 | bring-your-own local server (§8.2) | an RTX Spark, or any Arm PC with a small model for function only | tool calls and efforts pass the slash-command suite on llama-server and Ollama |
| 4 | the managed engine (W1), the watchdog, the `local` profile | the ASUS ProArt P16 (H7607) with 128 GB | §8.3's measurements and §9.4 |
| 5 | `ling-app` on Windows, the code index's static layer, scip built for Windows | Phase 1 | Work and Chat (node) open; `code_def` answers exactly |
| 6 | Night Shift in Rust, executing indexers under the offline account, the lone laptop's native web search and Chat window | Phase 4 | per feature |

Phases 1 and 2 need no RTX Spark and can be finished before the hardware arrives. Phases 3 and 4 need one; the Surface RTX Spark Dev Box (Microsoft.com, US) or an ASUS ProArt P16 with 128 GB are the candidates.

---

## 20. Risks

1. **WSL GPU paravirtualisation on N1X is young.** NemoClaw's own notes show it breaking in QA. W2 may simply not work in 2026, which is why W1 exists.
2. **The GPU budget may be configured small by default** (NemoClaw saw about 30 GB through NVML on one unit; NVML counts only the carveout, so this proves nothing about the budget). Phase 4 measures before any promise.
3. **Smart App Control** blocks unsigned builds (§16.3).
4. **The `windows-11-arm` runner may be too slow** for Codex's release build (§16.1).
5. **Upstream churn:** Codex's Windows sandbox is changing quickly (MXC, packaged service identities). `0024`'s two call sites in the elevated backends may move at the next Codex bump, and a third elevated entry point could appear without failing the patch. So the egress audit's `/airgapped on` case (§14, §18) must run after every bump; a moved call site already fails `git apply --check` loudly, as every patch here does.
6. **Speed.** W1's dense 27B would be about a third of the GB10's code speed. Mightling's pitch on the DGX Spark is speed; the user chose the same model everywhere over a faster MoE (§21, question 6), so the Windows engine's speed work (speculative decoding in W1, or W2's SGLang recipe) carries the weight.

---

## 21. Questions for the user

1. **WSL as plumbing:** **decided 2026-10-07: no WSL.** W1 only; W2 is not built, and is revisited only if W1 measures clearly too slow.
2. **Signing:** **decided 2026-10-07: unsigned for now,** marked preview; revisited before Windows leaves preview.
3. **Patch cap:** **decided 2026-10-07: up to 39,500 bytes** for `0024`.
4. **Hardware:** **decided 2026-10-07: an ASUS ProArt P16 (H7607, RTX Spark) with 128 GB.**
5. **x86-64 Windows:** **decided 2026-10-07: from the start,** in Phase 1.
6. **Default local model on Windows:** **decided 2026-10-07: Qwen3.8-27B**, the same model as the node. Phase 4 measures how fast it runs on each engine; it no longer chooses the model.

---

## 22. Changes to other specs when this is built

- **MIGHTLING_NODE §2, §8.1–§8.3, §15.1:** codebase-memory has a Windows Arm64 build; Windows `on` is enforced, not cooperative; the Windows target is Arm64 first; the signing decision as revised.
- **MIGHTLING_AIRGAPPED §14:** the Windows enforcement and refusal table.
- **MIGHTLING_EGRESS §10:** the ETW audit.
- **MIGHTLING_CODEX:** `0024`, the Windows assets, the builder changes.
- **MIGHTLING_CODE_INDEX §14:** the Windows host and the built scip CLI.
- **MIGHTLING_SKILLS §15:** junctions.
- **MIGHTLING_DESKTOP §10:** the Windows bundle.
- **SETUP:** `install.ps1`.
- **README:** the Windows row and the Smart App Control note.

---

## Sources

- NVIDIA, Windows on Arm Porting Guide: [System Overview](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/overview.html), [Unified Memory Architecture](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/index.html), [Usage with CUDA APIs](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/umacuda.html), [Porting and Emulation](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/portingandemulation/index.html)
- [RTX Spark Developer Preview](https://forums.developer.nvidia.com/t/rtx-spark-developer-preview/377106) (NVIDIA forums, 2026-07-16); [Port Windows Apps to ARM: RTX Spark Guide](https://developer.nvidia.com/topics/ai/local-ai/port-apps)
- [CUDA on WSL User Guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html) (13.4), Known Limitations
- [CUDA 13.4 released, ready for RTX Spark](https://videocardz.com/newz/cuda-13-4-released-ready-for-rtx-spark); [NVIDIA's first GeForce driver for Windows on Arm confirms RTX Spark N1X with 6,144 or 5,120 CUDA cores](https://videocardz.com/newz/nvidias-first-geforce-driver-for-windows-on-arm-confirms-rtx-spark-n1x-with-6144-or-5120-cuda-cores)
- [NVIDIA RTX Spark Laptops: N1X Specs, CUDA and Linux](https://computingforgeeks.com/nvidia-rtx-spark-linux/) (2026-10-03)
- [Nvidia RTX Spark](https://en.wikipedia.org/wiki/Nvidia_RTX_Spark) (Wikipedia); [RTX Spark vs Ryzen AI Max: How Much Memory the GPU Gets](https://vramcalculator.com/rtx-spark-local-llm/)
- NVIDIA NemoClaw: [issue #9000](https://github.com/NVIDIA/NemoClaw/issues/9000) (CUDA in WSL2 on N1X), [issue #12219](https://github.com/NVIDIA/NemoClaw/issues/12219) (Ollama on the Windows host), [release notes](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/release-notes)
- [Advanced settings configuration in WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl-config) (Microsoft Learn); [WSL on Windows on Arm](https://onarm.net/guides/wsl-on-windows-arm)
- Microsoft Execution Containers: [platform research](https://microsoft.github.io/entrabot/platform-learnings/mxc-windows-sandbox/), [InfoWorld](https://www.infoworld.com/article/4215416/running-ai-agents-in-sandboxes-with-microsoft-execution-containers.html)
- [GitHub Actions: Windows Arm64 hosted runners for public repositories](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/); [Windows Developer Blog](https://blogs.windows.com/windowsdeveloper/2025/04/14/github-actions-now-supports-windows-on-arm-runners-for-all-public-repos/)
- [Tauri Windows Installer](https://v2.tauri.app/distribute/windows-installer/)
- [llama.cpp: Responses API in llama-server](https://www.simplified.guide/llama-cpp/server-call-responses-api); [Codex with Ollama: Responses API from 0.13.3](https://www.simplified.guide/codex/ollama-model-use); [Deprecating chat/completions in Codex](https://github.com/openai/codex/discussions/7782)
- [ggml-org/Qwen3.8-27B-GGUF](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF); [Run Qwen3.8-27B locally with Ollama](https://www.yottalabs.ai/post/how-to-run-qwen-3-8-27b-locally-ollama-gguf-single-gpu-2026)
- Smart App Control: [Notebookcheck](https://www.notebookcheck.net/Windows-11-Smart-App-Control-blocks-unknown-executables-before-launch.1024308.0.html), [askwell issue #929](https://github.com/Rumeasiyan/askwell/issues/929), [Windows Latest](https://www.windowslatest.com/2025/12/16/microsoft-confirms-you-can-soon-disable-smart-app-control-without-reinstalling-windows-11/)
- [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options); [Azure Artifact Signing at $9.99/month](https://discuss.4d.com/t/windows-application-signing-with-ms-azure-artifact-signing-9-99-month/38154); [Melatonin: Azure Artifact Signing](https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/)
- [Job objects and memory notifications](https://devblogs.microsoft.com/oldnewthing/20251229-00/?p=111927) (The Old New Thing); [CreateMemoryResourceNotification](https://learn.microsoft.com/en-us/windows/desktop/api/memoryapi/nf-memoryapi-creatememoryresourcenotification)
- [ferrisetw](https://docs.rs/ferrisetw/latest/ferrisetw/); [Kernel network ETW events](https://learn.microsoft.com/hu-hu/windows/win32/etw/tcpip)
- [How to use uv on Windows ARM64](https://pydevtools.com/handbook/how-to/how-to-use-uv-on-windows-arm64/) (Python wheel availability)
- RTX Spark availability: [Windows Central](https://www.windowscentral.com/hardware/nvidia/nvidia-confirms-rtx-spark-configurations-and-availability-first-devices-expected-to-begin-shipping-as-soon-as-next-month-with-two-n1x-configs-on-offer), [TechPowerUp](https://www.techpowerup.com/353314/nvidia-rtx-spark-arrives-on-october-7-new-teaser-confirms), [Tech Insider](https://tech-insider.org/asus-msi-rtx-spark-laptops-sellout-2026/), [Surface RTX Spark Dev Box](https://blogs.windows.com/devices/2026/06/02/building-the-next-generation-of-devices-for-developers-surface-rtx-spark-dev-box/)
