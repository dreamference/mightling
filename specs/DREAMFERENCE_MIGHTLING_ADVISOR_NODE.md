# Mightling Advisor Node — a second GB10 that reviews the coder's work

**Status:** on hold since 2026-10-03, until a suitable advisor model is found; nothing below is to be built before then. Proposed on 2026-10-02. Nothing here is implemented. The spec was written on the GB10 itself (`gx10-9428`), so the machine's memory, swap, earlyoom and sidecar figures in §2 were read there. One load-bearing mechanism was run live: a blocking `Stop` hook continuing a `ling exec` turn against the 27B (§2). The advisor model was **not** downloaded, loaded or run. There is one Spark here, its model server was serving other tasks, and the chosen checkpoint is a 133 GB download that needs the whole machine. Every figure about the advisor model is therefore published, not measured here, and §12 marks each one. Phase 0 (§10) is the measurement.
**Target:** a second DGX Spark (GB10, SM121, 128 GB unified LPDDR5X, about 273 GB/s), bought to raise the quality of the code that `ling` on the first one writes.
**Builds on:**
- the client and `mightling-node` split, mDNS discovery, the TXT records, `node.json`, the SSH pairing and `ling-admin node set` ([MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md), §5, §6, §12, §15.1, §18.6);
- the launcher and its trusted-hook registration, which the compaction ledger already uses ([MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md) §4, [MIGHTLING_COMPACTION](./DREAMFERENCE_MIGHTLING_COMPACTION.md) §11);
- the model matrix, the SGLang engine and the host-safety layer ([MODELS](./DREAMFERENCE_MODELS.md), [INFERENCE](./DREAMFERENCE_INFERENCE.md) §5.3, §7, [PREFIX_CACHE](./DREAMFERENCE_PREFIX_CACHE.md));
- the benchmark runner and the prompt spec's list of its defects ([MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §12–§13, [MIGHTLING_PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md) §1.4, §6);
- Night Shift ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)).

**Decisions made here, stated first because each could be read the other way:**

1. **The advisor model is Qwen3.8-Flash-Next in NVIDIA's NVFP4 build, with thinking on.** It is the strongest model that one Spark can serve at 4 bits or better with a usable context (§3). It is **not a step change** over the 27B on the headline number: SWE-bench Pro is 62.5 against 61.7, a tie. The gain is in the repository-level and agentic scores: DeepSWE 58.7 against 42.2, SWE-bench Multilingual 81.0 against 73.8, NL2Repo 48.1 against 42.3, and the independent Artificial Analysis index 40 against 34. On top of that, the coder runs with reasoning set to `none`, so the advisor adds thinking as well as weights.
2. **No DeepSeek model fits.** The user suggested DeepSeek, and the arithmetic is in §3.4. V3.2 is 671B, about 377 GB at 4 bits. V4-Flash is 284B and 167 GB as released; on one Spark it fits only at 2–3 bits, and even its full-precision SWE-bench Pro (52.6) is below the 27B's 61.7. V4.1-Flash is 552B plus 196B of lookup tables and does not fit two Sparks at FP4.
3. **The advisor reviews; it does not code.** It reads the session and answers in text. It runs no commands, edits nothing, and its advice reaches the coder as a message the coder must check against the code and may reject (§4).
4. **It is reached through Codex's own hooks and one launcher subcommand, with no Codex patch.** The automatic review "before done" is a `Stop` hook. It is **gated by rule**, so a question-and-answer turn is never reviewed, and it is consulted once per turn. The coder or the user can also ask explicitly with `ling advise "<question>"`. The "stuck" and "before the first edit" triggers are Phase 2 arms, not defaults.
5. **It fails open.** No advisor node, a node that is loading, a busy queue, a timeout or an answer that cannot be parsed all mean: no block, one line in the session's advisor log, and the coder carries on as it does today.
6. **Nodes still have no roles** ([MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) decision 7). "Advisor mode" is a property of the model assigned to the node. The matrix entry carries `role = "advisor"`, so the node advertises `advisor=1` and **not** `main=1`, and nobody sets or stores a role.
7. **"Maxes memory" means a large resident model plus the page cache, not a larger arena.** About 73 GiB of the model stays resident, its 47.7 GiB n-gram table is memory-mapped from NVMe, and what earlyoom's line and the host reserve leave is given to the page cache for that table, never to the arena (§6). earlyoom stays on. A published recipe that reaches 94% of memory by disabling earlyoom is excluded for that reason.
8. **The measurement comes before any claim.** The gain to expect is small: Anthropic measured +2.7 points for its own advisor pairing. This benchmark's noise floor is 4 instances in 24. So the first evidence is an offline replay of the 24 recorded benchmark trajectories through the advisor (§8.2), and the primary A/B reading is per failure class, not the resolved rate.

---

## 1. Goals and non-goals

**Goals**
- A user with two Sparks types `ling` on the first and gets the same agent as today, whose work is reviewed by a stronger model on the second before it declares a task done, with nothing to configure beyond assigning the model.
- Night Shift tasks and benchmark runs get the same review, recorded in their reports.
- If the advisor is not there, nothing changes.
- A measurement on this hardware that says whether the review helps, and by how much, per failure class.

**Non-goals**
- A second coder, best-of-N sampling, or the advisor writing patches.
- Splitting one model across two Sparks (topology (b) of MIGHTLING_NODE §12.2). §3.8 says what it would buy.
- An advisor in the cloud. Mightling's model traffic stays on the local network.
- An advisor on anything but a GB10.

---

## 2. What was checked, and what was not

**Read on this GB10 on 2026-10-02:**

| Question | Result |
|---|---|
| Memory the OS sees | `MemTotal` 127,535,152 kB = **121.6 GiB**. With the 27B serving and every sidecar up: 93 GiB used, 33 GiB buffers and cache, 28 GiB available |
| Swap and kernel settings | 64 GiB swap; `vm.min_free_kbytes` 1,048,576; `vm.watermark_scale_factor` 200 (the values `check_host_safety()` requires) |
| earlyoom | active, `-m 5,2 -s 100 -r 60`: it sends SIGTERM below 5% available memory (6.1 GiB here) and SIGKILL below 2% (2.4 GiB); with `-s 100` the swap condition always holds, so memory alone decides |
| Disk | 292 GB free on the one filesystem that holds the model caches |
| Sidecars' host memory (`docker stats`) | speech-to-text 44 MiB; SearXNG 135 MiB; diffusion 295 MiB charged (8 GiB cap); image search 6 MiB; Gmail 16 MiB; web UI stack about 560 MiB (nginx 20, web 116, API 332, Postgres 66, code interpreter 25). GPU memory is **not** charged to a container's cgroup (MIGHTLING_NODE §13.8), so these are lower bounds for the two GPU sidecars |
| Codex's `Stop` hook (`codex-rs/hooks/src/events/stop.rs`, pinned `rust-v0.158.0`) | Receives `session_id`, `turn_id`, `cwd`, `transcript_path`, `model`, `stop_hook_active` and `last_assistant_message`. `{"decision":"block","reason":…}` makes the reason a **continuation prompt**: the turn goes on with it as input |
| **A blocking `Stop` hook in `ling exec`, run live** against the 27B (scratch `CODEX_HOME`, a stub hook, `--dangerously-bypass-hook-trust`) | **The turn continues.** The reason reaches the model as a **user** message wrapped in `<hook_prompt hook_run_id="stop:…">`. The second stop arrives with `stop_hook_active: true` and the same `turn_id`, and the stub's `{}` ends the turn. Two runs: (1) the reason asked for unrelated work (create `reviewed.txt`). The model **refused**: "`reviewed.txt` … isn't in your request, and stop-hook prompts can't add new work." (2) The reason was a finding about the task itself, framed as §4.5 frames it (`mean([])` raises `ZeroDivisionError`; check it; fix it or say why not). The model answered "Holds", fixed it, ran a check and finished, in 747 tokens. So the mechanism works in `exec`, and **the coder weighs hook text against the user's request on its own**, which is the behaviour the design wants, provided findings stay inside the task (§4.5) |
| Hook timeouts (`hooks/src/engine/discovery.rs`) | A command hook's `timeout_sec` defaults to **600 s** and is clamped only for `SessionEnd` and `Interrupt`. The ledger hook sets 10 s |
| What the TUI shows while a hook runs | A hook may carry a `status_message`, which `tui/src/status_indicator_widget.rs` shows in the status line (read, not run) |
| `PreToolUse` and `PostToolUse` | Both receive `transcript_path` and the tool's input. `PreToolUse` can deny the call with a reason; both can add `additionalContext` for the model (read from the event code and its tests) |
| What a shell command knows about its session | Codex exports `CODEX_THREAD_ID` and `CODEX_SESSION_ID` to every command (`core/src/exec_env.rs`), so a command can find its own rollout |
| Codex's `/review` and `review_model` | `spawn_review_thread` (`core/src/session/review.rs`) takes `review_model` but **the parent's provider**, so it cannot reach another server without a patch (§9) |
| Patch budget | 31,175 of 31,500 bytes (MIGHTLING_CODEX §3). A `/advisor` slash command does not fit without raising the cap |

**Read on the web, not tested here** (sources with dates at the end; the model facts were checked on the model cards on 2026-10-02):
- the candidate models' sizes, architectures, scores and licences (§3);
- single-Spark and two-Spark speeds from NVIDIA's forums, vLLM's blog and community repositories (§3.5, §3.8);
- the state of NVFP4, FP8 and MLA kernels on SM121 (§3.9);
- the advisor pattern as others built and measured it (§4.1).

**Not checked, and so Phase 0 (§10):** everything about running Qwen3.8-Flash-Next on this hardware: whether SGLang's n-gram-table offload works on SM121; resident memory; page-cache behaviour; PSI during load and during consults; prefill and decode speed with thinking on; prefix-cache hits on repeated consults; and whether a 2–5 minute hook disturbs the TUI.

---

## 3. The model

### 3.1 What "better than the 27B" has to mean

- **SWE-bench Verified is no longer the yardstick.** the upstream vendor stopped reporting it in early 2026 for contamination (MIGHTLING_SWE_BENCH §8). Qwen's 2026 cards report SWE-bench Pro, DeepSWE, NL2Repo, Multilingual, LiveCodeBench and Terminal-Bench instead, and the 27B's card has **no Verified number**. The comparison below uses what both cards report, plus the Artificial Analysis Intelligence Index as the one independent figure.
- **The coder on node 1 runs below its card.** `ling` sends reasoning effort `none`, so every turn of the 27B is answered without thinking (MODELS §1, INFERENCE §5.3), while the card's scores are with thinking. An advisor that thinks therefore brings two things: a stronger model, and the thinking the coder does without. §8 separates them with a control arm in which the advisor is the 27B itself at `xhigh`.
- **A review can only hand over what the reviewer has.** Anthropic's guidance says the same: "the advisor can only hand over capability the executor lacks". A model of equal strength is still useful as a second look, but the case for buying hardware rests on the gap.

### 3.2 The budget on one Spark

| Item | GiB | Source |
|---|---|---|
| Memory the OS sees | 121.6 | `MemTotal`, §2 |
| `HOST_MEMORY_RESERVE_GB` kept outside the arena by the pre-flight | 11.2 (12 GB) | INFERENCE §7 |
| earlyoom's SIGTERM line (5% available) | 6.1 | §2 |
| OS, Docker and the engine's own process | about 5.5 | dev.to measurement of a Spark (undated, assumed) |
| **Largest arena (weights + KV + state)** | **about 105, in practice 85–95** | 121.6 − 11.2 − 5.5; the default recipes stop well below (0.5 and 0.7 of GPU memory) because a desktop shares the host and 0.80 froze it once (MODELS §2) |

So: **weights at or under about 80 GiB leave a usable KV pool and a page cache; 100 GiB of weights is the end of the line.** A checkpoint of 100 GB or more on disk does not fit as a resident model.

### 3.3 Candidates

Scores are the vendor's with thinking at its highest published setting, unless marked. "AA" is the Artificial Analysis Intelligence Index (fetched 2026-10-02). KV is per 1,000 tokens of context at a 16-bit cache, from each `config.json` (layers with full attention × 2 × KV heads × head size × 2 bytes; linear-attention and Mamba layers hold a fixed state per sequence instead).

| Model (release) | Total / active | Build for SM121, size | KV per 1K tokens | Fits one Spark? | Coding scores | Licence | Engine on SM121 |
|---|---|---|---|---|---|---|---|
| **Qwen3.8-27B** (Aug 2026), *today's coder* | 27B dense; 16 of 64 layers full attention, 4 KV heads × 256 | RadixArk NVFP4 (FFN NVFP4, attention FP8) | 64 MiB | yes; pool 157K tokens measured beside the sidecars | Pro 61.7, TB 2.1 73.0, DeepSWE 42.2, NL2Repo 42.3, LCB v6 90.3; AA 34 | Apache 2.0 | SGLang, in use |
| **Qwen3.8-Flash-Next** (Aug 2026) | 125B main (512 experts, 10 + 1 active) + 51B n-gram table + 4B MTP; 6B active; 12 of 48 layers sparse full attention, 2 KV heads × 256 | NVIDIA NVFP4: routed experts NVFP4, attention and shared experts BF16, MTP and table FP8; 124 GiB on disk, of which the table is 47.7 GiB. RadixArk NVFP4 122 GiB. Intel int4 AutoRound (size not confirmed) | **24 MiB** | **yes, with the table memory-mapped from NVMe: about 73–75 GiB resident** | **Pro 62.5, Multilingual 81.0, DeepSWE 58.7, NL2Repo 48.1, LCB v6 91.9, Toolathlon 73.5; AA 40**. No Terminal-Bench published | Qwen Community 1.0; NVIDIA's build adds the NVIDIA Open Model License | vLLM 0.30 with 18 patches (measured on a GX10); SGLang `--ple-offload-backend file` (reported, not seen run on SM121) |
| DeepSeek V4-Flash (Apr 2026; 0731 refresh) | 284B / 13B; compressed sparse attention | official FP4 experts + FP8 = 167 GB; GGUF IQ2_XXS 90.9 GB, Q2_K_XL 96.8 GB, IQ3_XXS 104 GB, Q4_K_XL 155 GB | about 4–5 MiB (derived by others, not checked) | **only at 2–3 bits**: 2-bit experts at 81 GiB, peak 115 GiB | April: Verified 79.0, **Pro 52.6**, Multilingual 73.3, TB 2.0 56.9, LCB 91.6. 0731: DeepSWE 54.4, TB 2.1 82.7 | MIT | SGLang has an SM12x path for its attention; vLLM TP2 on two Sparks |
| DeepSeek V4.1-Flash (Sep 2026) | 552B + 196B "Engram" tables; 8B active in prefill, 16B in decode | 763B parameters as published; Unsloth Q2_K 246 GiB | 0.87 MiB (FP4 KV) | no | DeepSWE 74.2, TB 2.1 90.6, TB 4.0 31.2; AA 39 | MIT | not seen on a Spark except low-bit, streaming from SSD |
| DeepSeek V3.2 (2025) | 671B / 37B; MLA with sparse attention (DSA) | 4-bit ≈ 377 GB | 68.6 MiB (61 layers × 576 × 2 B) | no | superseded by V4 | MIT | sparse MLA has **no** SM121 backend (vLLM #45317; Triton fallback PR #54929 open) |
| GLM-5.3-Flash (Aug 2026) | 320B / 18B; 34 linear + 11 sparse-attention layers | BF16, FP8; community NVFP4 ≈ 180 GB (estimate); UD-Q2_K_XL 108.7 GB | about 11 MiB (config looks wrong; unverified) | only at 2 bits: 108.7 GB leaves no room; an 85 GB 2.05-bit build crashes after ~10K tokens | DeepSWE 63.4, TB 2.1 84.3; AA 42 | MIT | two Sparks, NVFP4 TP2 (reported) |
| MiniMax M2.7 (2026) | 230B / 10B | UD-IQ3_XXS 80 GB; REAP-pruned 172B NVFP4 98.9 GB (quality unvalidated) | not checked | only at 3 bits or pruned | Pro 56.2 (aggregator) | not checked | llama.cpp; vLLM TP2 on two Sparks |
| Qwen3.5-122B-A10B (Feb 2026), *today's fallback* | 122B / 10B; 12 of 48 full attention, 2 KV heads × 256 | Intel INT4 AutoRound, RedHat NVFP4 ≈ 75 GB | 24 MiB | yes (in the matrix) | Verified 72.0, TB 2 49.4, LCB 78.9; AA 16 | Apache 2.0 | vLLM, in use |
| Ling-3.0-flash (Aug 2026) | 124B / 5.1B; 35 linear + 7 MLA layers | official INT4 ≈ 77 GB | 7.9 MiB | yes | Pro 56.6 (OpenHands), Multilingual 72.4; AA 20 | MIT | vLLM fork with MTP |
| Nemotron 3 Super (Mar 2026) | 120B / 12B; Mamba-2 + 8 attention layers | NVFP4 + FP8 | 8 MiB | yes | Verified 60.5 (OpenHands) | NVIDIA Open Model License | vLLM, Marlin path |
| gpt-oss-120b (2025) | 117B / 5.1B; alternating full and 128-token sliding attention | MXFP4 ≈ 65 GB | 36 MiB (full layers) | yes | Verified 52.6 (medium effort); AA 12 | Apache 2.0 | vLLM, SGLang, llama.cpp |
| Devstral 2 (Dec 2025) | 123B dense | 4-bit ≈ 65–70 GB (estimate) | not checked | yes, but see §3.5 | Verified 72.2 | modified MIT | not measured on a Spark |

**Context that fits on one Spark**, for the rows that fit:

| Model | Context |
|---|---|
| the 27B | 262K advertised; its KV pool, measured beside the sidecars, is 157K tokens for all streams together |
| Flash-Next | 262K native per stream (1M with YaRN, validated to 500K by blazux); about 430K tokens of pool at §6.1's split, and 505–680K reported with the arena blazux used |
| Qwen3.5-122B | 32K as served here (its 131K launch was refused for KV) |
| gpt-oss-120b | 131K reported |
| Ling-3.0-flash | 131K verified by its recipe's author |
| DeepSeek V4-Flash, 2-bit | 262K measured, at 7.9 tok/s and a 115 GiB peak, above what host safety allows |

**Flash-Next's parameter count, reconciled.** The card's "125B" is read as the main model alone; the 51B table and the 4B MTP head come on top, about 180B in all, which blazux rounds to "~176B". The expert arithmetic supports that reading: 512 experts × 48 layers × 3 × 2560 × 640 ≈ 121B.

Too large for one or two Sparks even at 2 bits, and not tabulated: MiniMax M3 (428B), Kimi K2.6 and K3, Nemotron 3 Ultra (550B), Qwen3.5-397B, Qwen3.8's 2.4T model, DeepSeek V4-Pro, GLM-5.2/5.3 (744–753B), MiMo-V2.5/2.6-Pro.

### 3.4 The arithmetic for DeepSeek, the user's suggestion

| Model | Weights at 4 bits (4.5 bits with scales) | At 2 bits | Fits the 85–95 GiB arena? |
|---|---|---|---|
| DeepSeek V3.2, 671B | 671B × 0.5625 B ≈ 377 GB | ≈ 190 GB | no, at any usable precision |
| DeepSeek V4-Flash, 284B | 167 GB as released (FP4 experts, FP8 rest); 155 GB Q4_K_XL | 81–97 GB | only at 2 bits, and then the measured run peaked at **115 GiB** of 121.6 (Classmethod, 2026-05-13), above the ≈110 GiB that the 12 GB host reserve leaves. The 3.0-bit build with 40 of 256 experts pruned (99.5 GiB) needs earlyoom disabled |
| DeepSeek V4.1-Flash, 552B + 196B tables | ≈ 300 GB for the backbone alone | 246 GiB (Q2_K) | no; not even on two Sparks |

And quality would not follow the name: V4-Flash at full precision scores 52.6 on SWE-bench Pro against the 27B's 61.7. Its 0731 refresh is stronger on Terminal-Bench 2.1 (82.7 against 73.0) and DeepSWE (54.4 against 42.2), but still below Flash-Next's 58.7 on DeepSWE. A 2-bit copy is weaker again, and no coding evaluation of one against the full model was found. **DeepSeek is the right family only from four Sparks up (V4.1-Flash), which is not this product.**

### 3.5 Expected decode speed, from bandwidth

The ceiling is 273 GB/s divided by the bytes read per generated token: every active weight once, plus the KV read, which is small at these sizes. Measured speeds land at 50–70% of the ceiling, and speculative decoding (a drafter, or the model's own MTP head) multiplies them.

| Model | Bytes read per token (derived) | Ceiling | Measured on one Spark |
|---|---|---|---|
| Qwen3.8-27B NVFP4 | ≈ 16 GB (27B, NVFP4 FFN + FP8 attention) | ≈ 17 tok/s | 25.5 prose / 50.3 code with DFlash2 (here, 2026-09-29) |
| **Qwen3.8-Flash-Next NVFP4** | ≈ 1.5 GB of experts (11 × 48 × 3 × 2560 × 640 at 4.5 bits) + ≈ 7.2 GB of BF16 attention, linear-attention, shared-expert and output layers ≈ **8.7 GB** | **≈ 31 tok/s** | 34 with MTP and the side layers in FP8 (blazux, vLLM, 2026-09-28); 37 (MiaAI, 2026-09-05, quality complaints in replies); 43–47.5 on Intel int4 with MTP 3 (forum, 2026-09-09/30) |
| DeepSeek V4-Flash, 2-bit experts | ≈ 5 GB | ≈ 55 | 11–14 (llama.cpp, ds4): engine-bound |
| GLM-5.3-Flash, 2-bit | ≈ 6.3 GB | ≈ 43 | 17.7 (llama.cpp) |
| Devstral 2, dense 4-bit | ≈ 68 GB | ≈ **4** | none found; an answer with 3,000 tokens of thinking would take over 12 minutes |
| gpt-oss-120b | ≈ 2.7 GB | ≈ 100 | 58–62 |

Flash-Next is about as fast as today's coder, because its 6B active parameters cost less to read than the 27B's dense weights, even with its side layers in BF16.

### 3.6 The choice

**Chosen: Qwen3.8-Flash-Next, `nvidia/Qwen3.8-Flash-Next-NVFP4`, pinned to a revision, with thinking on.**
- It is the only model found that fits one Spark at 4 bits or better and scores above the 27B on the independent index (40 against 34) and on every coding row both cards share. The rows that matter for review are the repository-level ones (DeepSWE +16.5, NL2Repo +5.8, Multilingual +7.2).
- Its KV cost is 24 MiB per 1,000 tokens, so a full coder transcript fits with room for several at once (§4.4, §4.8).
- Its speed puts a thinking review in minutes, not tens of minutes (§4.7).
- **Where it is not better:** SWE-bench Pro is a tie (62.5 against 61.7), and there is no published Terminal-Bench, where the 27B scores 73.0. That is why §8's measurement comes before any statement that the advisor improves code.

**Runner-up: Qwen3.8-27B itself at `xhigh` thinking on the second Spark.** No other model that fits is stronger than the 27B. Ling-3.0-flash (AA 20, Pro 56.6), Qwen3.5-122B (AA 16), Nemotron 3 Super (Verified 60.5) and gpt-oss-120b (AA 12) are all weaker, and Devstral 2 is too slow. The 27B with thinking still gives the coder something it does not have, and it is the recipe the project already runs. It is the control arm of §8 and the fallback if Flash-Next's serving path does not come up in Phase 0.

**Considered and excluded for one Spark:** DeepSeek V4-Flash and GLM-5.3-Flash at 2–3 bits (§3.4); MiniMax M2.7 at 3 bits or pruned (unvalidated quality, licence not checked).

### 3.7 Then why not make it the coder?

If Flash-Next is stronger and as fast, the obvious question is whether node 1 should simply run it. Three reasons it is the advisor in this design, and one way the answer could change:
- **It needs the whole machine.** 73–75 GiB resident plus a page cache for a 47.7 GiB table leaves no room for the diffusion sidecar, speech-to-text, the web UI, indexing and benchmark containers, which a coder node runs (§6.4).
- **Its serving path is young.** The one measured single-Spark run is a vLLM 0.30 build carrying 18 patches. SGLang's offload has not been seen on SM121. The coder is the thing that must always be up; the advisor is allowed to be absent (decision 5).
- **The pairing gives thinking where latency allows it.** The coder answers each turn without thinking, which is what keeps an interactive session fast. The advisor thinks once per turn, where minutes are acceptable.
- **What would change it:** arm E of §8 runs Flash-Next as the coder with no advisor. If arm E beats the 27B with an advisor, the recommendation becomes to swap the coder, and the advisor role then wants a larger model than one Spark holds (§3.8). §13 asks that question.

### 3.8 Two Sparks linked by the QSFP port

MIGHTLING_NODE §12.2 records topology (b), one model split across two Sparks over the ConnectX-7 link, as "not planned". For an advisor it would mean **three** Sparks: one coder and two linked for the advisor.

| Model on two Sparks | Single-stream decode (reported) | Gain over Flash-Next on one |
|---|---|---|
| GLM-5.3-Flash NVFP4, TP2 with speculation | 30.8 tok/s, 262K | AA 42 against 40; DeepSWE 63.4 against 58.7; TB 2.1 84.3 |
| DeepSeek V4-Flash 0731, FP8/NVFP4, TP2 (+EP) | about 40 on mixed content, up to 82 with DSpark speculation; prefill about 1,700 | DeepSWE 54.4: **below** Flash-Next |
| Qwen3.8-Flash-Next NVFP4, TP2 with MTP | 47.5 | same model, faster, with the table resident |
| Qwen3.5-397B-A17B int4, TP2 | about 26–30 | older and weaker |

Link facts: 189.85 Gb/s across both rails in NVIDIA's own `ib_write_bw` example, about 196 Gb/s measured by a forum user; about 2 µs per RDMA write and about 40 µs per NCCL all-reduce, with no GPUDirect RDMA on the Spark. Latency, not bandwidth, limits tensor parallelism.

**Verdict:** a third Spark buys about two index points (GLM-5.3-Flash) in exchange for a Ray-based recipe, a joint pre-flight and a joint kill path that MIGHTLING_NODE §12.2 lists as missing. **Not worth it now.** The one model that would be a real step, DeepSeek V4.1-Flash (DeepSWE 74.2, TB 2.1 90.6), needs about four Sparks at FP4. That is revisited if a V4.1-class model appears at a two-Spark size (question 4 of §13).

### 3.9 The recipe (proposed matrix entry `qwen3.8-flash-next-advisor`)

| Key | Value | Why |
|---|---|---|
| checkpoint | `nvidia/Qwen3.8-Flash-Next-NVFP4` at a pinned revision, served as its snapshot directory | the SGLang revision trap of INFERENCE §5.3 |
| `engine` | `sglang`, image pinned by digest to a version with `--ple-offload-backend file` | the project's engine. The **fallback** is blazux's vLLM 0.30 build pinned by digest, as the 122B entries pin `aeon` images |
| n-gram table | memory-mapped from the snapshot on NVMe, not loaded into the arena | the only way it fits (§6) |
| context | 262,144 tokens (native) | the packet budget is 96K (§4.4); the window must hold one with room |
| memory | `--mem-fraction-static` chosen in Phase 0, starting at 0.72 (about 86 GiB: 73 of weights, 10 of KV, about 3 of linear-attention state) | §6.2 |
| concurrency | at most 4 running requests; linear-attention slots sized to match | §4.8 |
| speculation | the checkpoint's own MTP head | +10–50% reported; Phase 0 checks acceptance with thinking on |
| reasoning | parser `qwen3`; requests ask for thinking at the effort Phase 0 picks (`medium` against `xhigh`) | the 27B recipe's author measured `xhigh` at 3.19× the thinking tokens and a lower HumanEval |
| sampling | `--sampling-backend pytorch` | FlashInfer's untruncated sampler returned token 0 on this GPU (INFERENCE §5.3); the advisor samples with thinking, so the trap applies |
| chat template | `chat_template_patches` if its template refuses Codex's efforts as the 27B's did | the patcher already exists |
| `role` | `advisor` (new field) | §5 |
| `exclusive` | `true` (new field): no diffusion sidecar, and the node's other sidecars are stopped before the load | §6.4 |
| `mmap_weights_gb` | 47.7 (new field) | the pre-flight counts it against disk and the page cache, not the arena (§6.3) |
| tool-call parser | `qwen3_coder` | not needed by the advisor, but lets a user code with this model explicitly (`ling node use spark-2`) |

---

## 4. Advisor mode: how the coder consults it

### 4.1 The pattern

Others have built this, and their measurements set expectations:
- **Anthropic's advisor tool** (beta, 2026-03): the executor model calls a stronger advisor, which sees the full transcript, has no tools, and answers in 400–700 tokens. The suggested use is after orientation and before declaring done. Measured: Sonnet with an Opus advisor gained **+2.7 points** on SWE-bench Multilingual at 11.9% lower cost; Haiku on BrowseComp went from 19.7% to 41.2%.
- **Aider's architect/editor split** (2024): o1-preview as architect gained 79.7% → 85.0% on Aider's benchmark, and QwQ with Qwen2.5-Coder 32B reached 73.6% where either model alone scored 42.1% or 71.4%. These are 2024 models, but the result is the same shape: a strong reasoner paired with a capable writer.
- **Cline** has a 2026 proposal for a plan model that reviews each act attempt beside a Qwen3.6-27B coder, with no results. **No report of a second Spark used as a reviewer was found.**

What Mightling takes from these: the advisor sees the transcript, not a summary of it; it runs no tools; it is consulted at a few points, not every turn; and its answer is advice the executor weighs.

### 4.2 Three points of consultation, three mechanisms

| Point | Mechanism | Ships in |
|---|---|---|
| **Before declaring done** | `Stop` hook, gated by rule (§4.3). On "revise" it blocks with the advice as the continuation prompt | **Phase 1, on by default** |
| **On request** | `ling advise "<question>"`: the user types it in a shell, or the model runs it (the prompt names it, §4.6) | **Phase 1** |
| **When stuck** | `PostToolUse` hook: a rule over the rollout's tail (the same failing command 3 times since the last consult, or 6 non-zero exits in a row, or 25 tool calls since the last file change after work began) consults and returns the advice as `additionalContext` | Phase 2, as an arm |
| **Before committing to an approach** | `PreToolUse` hook on the turn's **first edit** (an `apply_patch` call, or a command that writes a tracked file): denies that one call with the advice as its reason, so the coder re-plans before anything lands | Phase 2, as an arm |

The model-initiated route is the weakest. In the benchmark's code-index arm the model was handed `ling-code` and called it **0 times in 24 instances** (MIGHTLING_SWE_BENCH §13.5). The default is therefore the automatic `Stop` review, and the request route is measured, not relied on.

All four are hooks or a launcher subcommand. **No Codex patch is needed.** The hooks are registered by the launcher in `config.toml` with their trust entries, exactly as `compaction.rs` registers the ledger hook (MIGHTLING_COMPACTION §11). Each runs the installed binary as `ling advise --hook <event>`.

### 4.3 The `Stop` gate

The hook runs at the end of every turn, so the rule decides cheaply (the ledger's rules take about 15 ms) whether this turn is worth minutes of review. A turn is reviewed when **all** of these hold:
1. `stop_hook_active` is false. When the coder stops again after acting on advice, it is not reviewed a second time in the same turn; `advisor.max_rounds = 1` by default.
2. **Files changed since the last consult in this session**: `git status --porcelain` differs from the snapshot recorded at that consult, or from the session's start. Outside a git repository, the `apply_patch` headers in the rollout are used, as the ledger does.
3. **Either** a test command ran in this turn (the ledger's test-summary patterns: pytest, `cargo test`, Jest, `go test`), **or** the last assistant message reads as completion ("done", "fixed", "implemented", "all tests pass", or the files-changed list Night Shift's preamble asks for).
4. The level allows it (§4.11).

A turn that only answered a question, only read code, or stopped half-way to ask the user something is not reviewed.

### 4.4 What is sent

**The transcript, rendered by rule, not a summary.** Recorded sessions here peak at a median of 14K prompt tokens, a p90 of 31K, a p99 of 54K and a maximum of 71K (MIGHTLING_COMPACTION §1). The advisor's window is 262K and its KV costs 24 MiB per 1,000 tokens, so the whole session fits in nearly every case. Summarising it with the coder would cost the coder's time and lose the tool history, which is what compaction was found to lose (MIGHTLING_COMPACTION §9).

The packet, in this order (fixed order and deterministic rendering, so a second consult in the same session reuses the first one's prefix in the advisor's cache):

1. The advisor's system prompt (about 1,500 characters, §4.5).
2. Every user message, verbatim.
3. The session's items in order: assistant messages verbatim; each command or `apply_patch` verbatim; each output clipped to its first 1,000 and last 1,000 characters; any compaction summary, marked as such.
4. The ledger (`ling ledger`): files changed, other files read, failed commands, the last test result.
5. **The current diff**: `git diff` against the session's starting commit, plus untracked files under 20 KB, capped at 40,000 characters, with the cut named.
6. The question: for the `Stop` review, a fixed request; for `ling advise`, the asker's text.

**Budget: 96K tokens.** It covers the recorded p99 whole, and its prefill takes under a minute (§4.7). **How it is counted without a tokenizer:** the launcher is standard-library Rust and ships no Qwen tokenizer, so the packet is cut at **300,000 characters**, about 3.1 characters per token. Code and command output tokenize at roughly 3–3.5 characters per token, so the estimate errs towards fewer tokens than the limit. Every response's `usage.prompt_tokens` is logged beside the packet's characters, and the ratio is recalibrated from those logs. If the engine still rejects a packet as too long, it is cut to two thirds and sent once more. The engine's own `/tokenize` endpoint was considered: it adds a round trip, and whether the pinned image exposes it is unchecked. Above the budget the cuts go, in order: oldest tool outputs to their first line, then oldest assistant messages to 500 characters. The first user message, the diff and the question are never cut. The packet records what was cut.

**What it contains is the session as the coder saw it**: source code, command output, and, where the Gmail tool was used, mail. It goes to a machine on the same trusted LAN as the model server already is (MIGHTLING_NODE §11), unencrypted, under the same decision.

### 4.5 What comes back, and how the coder receives it

**Format.** The request asks for JSON, constrained by a schema through the engine's structured output (applied after the reasoning block; that this works with SGLang's reasoning parser is assumed):

```json
{"verdict": "approve | revise | unsure",
 "findings": [{"severity": "high | medium | low", "where": "path:line or 'test run' or 'plan'",
               "problem": "…", "evidence": "what in the transcript or diff shows it",
               "suggestion": "…"}],
 "summary": "one or two sentences"}
```

**The system prompt asks for what the coder's recorded failures were** (MIGHTLING_PROMPT §1.4, 11 failures in 24 instances): a fix in the wrong place, which means checking that the changed code is the code that owns the behaviour; an incomplete fix, which means other call sites, a second file or a sibling code path; something broken that worked, which means the tests near the change and whether they ran after the last edit; invented behaviour the task did not ask for; and edited tests, fixtures or stray files in the diff. It forbids style comments and rewriting the solution, and says to answer `approve` when nothing of substance is wrong. Its text is fixed in the binary and versioned; the version is recorded with every consult.

**When it blocks.** Only on `revise` with at least one finding of `medium` or higher. On `approve`, `unsure`, or only `low` findings, nothing is injected; the verdict is logged and shown on the status line.

**What the coder reads** (the continuation prompt, capped at 6,000 characters so it stays under Codex's spill limit for hook text):

```text
Advisor review (Qwen3.8-Flash-Next on spark-2, 1 min 52 s). It read this session and the
current diff. It ran nothing and can be wrong. Check each point against the code before
acting. Fix what holds; for what does not, say in one line why. Then finish as before.

1. [high] src/forms/fields.py:1214 — … Evidence: … Suggestion: …
2. [medium] tests not run after the last edit — …
```

Telling the coder that it may reject a point, and asking it to say why, is the "weigh" part of the pattern. §8 measures how often points are accepted, rejected, and right.

**Findings must stay inside the user's request.** Codex delivers the text as a user message marked `<hook_prompt>`, and the coder judges it against what the user asked: in §2's live check it refused hook text that added unrelated work, and acted on a finding about the task itself. The advisor's system prompt therefore restricts findings to whether the change does what the user asked, and does it without breaking anything. "Also improve X" is out of scope. A suggestion the advisor thinks worth making beyond the task goes in `summary`, which is logged and shown to the user, not sent to the coder.

### 4.6 `ling advise`

A launcher subcommand, intercepted in `prepare_args` the way `night` and `ledger` are, so it costs no patch and lives in `ling-rs/src/advise.rs`:

| Form | Does |
|---|---|
| `ling advise "<question>"` | Finds the session from `CODEX_THREAD_ID` (set by Codex for every command) or `--session <id>`, builds the packet with the question at the end, waits for the answer and prints it. From the model's shell this is the "on request" route |
| `ling advise --hook stop\|post-tool\|pre-tool` | The hook entry points: JSON on stdin, JSON on stdout, always exit 0 |
| `ling advise status` | Which advisor node, its model and state, its queue, and this machine's level |
| `ling advise log [<session>]` | The session's consults: when, how long, what was sent (sizes and cuts), the verdict, the findings, whether they were injected |

When an advisor is resolved and the level is not `off`, the launcher appends a short block to the prompt:

```text
# Advisor
A stronger reviewer model runs on another machine. When you are unsure between approaches or stuck,
run `ling advise "<your question>"`: it reads this session and answers in one to three minutes.
It cannot run commands and can be wrong.
```

Whether to also tell the coder that its work will be reviewed is left to the measurement. It may change how the coder works, for better or worse, so arm B runs without that sentence.

### 4.7 Latency budget

A slow, high-quality answer is acceptable here, within limits:

| Step | Typical (30K-token packet) | Worst (96K) | Basis |
|---|---|---|---|
| Find the advisor, check it answers | under 1 s (remembered) | 2 s (a browse) | MIGHTLING_NODE §18.3: a browse takes 0.48–1.08 s |
| Build the packet | under 1 s | 2 s | the ledger takes about 15 ms; reading a large rollout dominates |
| Prefill | 10–13 s | 40–65 s | 2,400–3,000 tok/s at 8–32K reported for Flash-Next; long prompts are slower (the 27B fell from 1,700 to 1,000 tok/s at 116K) |
| Thinking | 50–200 s | same | 1,500–6,000 thinking tokens at about 30 tok/s; capped by `max_tokens` = 8,192 |
| The answer | 10–25 s | same | 300–800 tokens |
| **Total** | **about 1.5–4 min** | **about 5 min** | all assumed until Phase 0 |

- **The hook's timeout is 420 s.** At the timeout the hook gives up, nothing is injected, and the log says so. Codex's default would allow 600.
- **Against the work it reviews:** the benchmark's median instance took 5 min 49 s (MIGHTLING_SWE_BENCH §12.5), so one review adds roughly a third to an instance; a Night Shift task has 90 minutes; an interactive editing turn waits the full time.
- **What the user sees** while it runs is the hook's status message on the status line: `Advisor on spark-2 is reviewing this turn (about 2 min)`. Whether Esc or Ctrl-C ends the wait and leaves the turn intact is a Phase 0 check; the design needs it, and if Codex cancels the whole turn instead, the TUI default becomes `ask` (question 2).

### 4.8 Concurrency

- **Who shares one advisor:** interactive sessions on one or more clients, Night Shift (up to 3 tasks), and the benchmark (2–3 agents at once, §12.3 of its spec).
- **Memory is not the limit.** At 24 MiB per 1,000 tokens, four 96K packets need 9 GiB of KV, and the recipe's pool is about 10 GiB (§3.9). The limit is speed: decode is shared. Blazux reports 34 tok/s for one stream and 266 tok/s total across 48, so four at once still run at roughly 20–25 tok/s each (assumed).
- **The engine runs at most 4 requests at once and queues the rest.** Before sending, the hook reads the advisor's `/metrics` (running and queued requests) and estimates the wait as the queue length times the median consult time it has logged. If the wait plus its own expected time exceeds the timeout, it does not send: `advisor busy`, no block.
- **Interactive before unattended.** Requests carry a priority: interactive sessions high; Night Shift and the benchmark low. The engine is started with priority scheduling (`--enable-priority-scheduling` in SGLang's recent versions; assumed present in the pinned image, Phase 0). There is no per-client fairness beyond that; on a trusted LAN none is needed.
- **The benchmark arm is planned with the queue in mind:** 24 instances with one review each is about 50 minutes of advisor time, spread over two agents. The manifest records each consult's queue wait so that queueing is not read as the model's latency.

### 4.9 Failure behaviour

| Case | What happens | Time lost by the coder |
|---|---|---|
| No advisor configured or found | the hook exits at once; `ling advise` prints "No advisor node on this network" | about 0 (one cached lookup) |
| Advisor found, `state=loading` or `stopped` | no consult; status line `advisor not ready` | about 0 |
| Address does not answer | connection timeout of 2 s, then as above | 2 s |
| Queue too long | `advisor busy`, no consult (§4.8) | under 1 s |
| Timeout (420 s), engine error, answer not valid JSON after one retry with the schema | no block; the raw answer is kept in the log | up to 420 s |
| The advisor returns `revise` and the coder rejects every point | the turn ends with the coder's reasons; logged as "rejected" | the coder's extra turn |
| The advisor node dies mid-request | as a timeout | up to 420 s |

Every case is logged in `$CODEX_HOME/advisor/<thread-id>.jsonl`. The hook never exits non-zero and writes nothing to stderr: in `stop.rs`, an exit code of 2 turns stderr into a blocking reason, and a failure must never become a continuation.

### 4.10 Where it runs

- **Interactive `ling`:** as above, at the level the user sets.
- **`ling exec`:** a blocking `Stop` hook continues an `exec` turn, checked live (§2).
- **Night Shift:** each task's `ling exec` reviews itself before it returns. The morning report gains a column per task: the verdict, the findings, and whether the coder acted on each. The advisor's findings are thus also the reviewer's notes for the person reading the report, even where the coder rejected them.
- **SWE-bench:** the agent's container sits on an internal Docker network that reaches only the host's gateway (MIGHTLING_SWE_BENCH §9). For a run with `--advisor`, the runner starts a TCP forwarder on the host, bound to the internal network's gateway address, which relays to the advisor node. The container gets `DREAMFERENCE_ADVISOR_HOST=http://<gateway>:8010`. The agent can then reach the coder's model server and the advisor, and nothing else, so the isolation the benchmark depends on holds. `ling advise` is part of the `ling` binary, so the relocated runtime needs nothing new. The manifest records the arm, the advisor's model id and revision, the node id, the effort, the gate's version and the system prompt's version.
- **The egress audit** names its model server and never browses (MIGHTLING_NODE §11). It sets the advisor level to `off`, unless run with `--advisor`, in which case the advisor's address joins the allow-list.
- **`/airgapped`:** the advisor is a model server on the LAN, not the internet. The **hook** runs outside the command sandbox, as Codex's own model requests do, so it works at every level. The **shell form** `ling advise` is a command, so at level `on` patch `0019` takes its network away, and it says that the hook still reviews the turn.

### 4.11 Configuration

| Setting | Values | Default |
|---|---|---|
| `mightling_advisor` (TOML), `DREAMFERENCE_MIGHTLING_ADVISOR` | `auto` (the `Stop` review plus the command), `ask` (the command only), `off` | `auto` when an advisor is found. Whether the TUI should default to `ask` is question 2 |
| `DREAMFERENCE_ADVISOR_HOST` | a URL | unset; the first tier of §5.2 |
| `[advisor] max_rounds` | consults per turn | 1 |
| `[advisor] timeout` | seconds | 420 |
| `[advisor] packet_tokens` | packet budget | 96,000 |
| `[advisor] effort` | `medium`, `high`, `xhigh` | chosen in Phase 0 |
| `[advisor] triggers` | any of `stop`, `stuck`, `first-edit` | `stop` |

`[night] advisor` and `swe-bench run --advisor <level>` set the level for their runs and record it.

---

## 5. Discovery: the advisor in the node design

### 5.1 The node side

- **`role` is a field of the matrix entry**, with values `main` (default), `advisor`, `diffusion` or `draft`. `NodeAdvertiser.is_main_model()` becomes "role is `main`". The advisor entry therefore advertises no `main` record, and MIGHTLING_NODE §12.3's unprompted choice ("the one advertising `main=1`") still sees one coding node.
- **A new TXT record, `advisor=1`**, present when the assigned model's role is `advisor`. Like `main`, it is a property of the entry, so nobody sets it.
- **The rest of the advert is unchanged:** `state` tells a client whether the advisor is loading; the model and context are asked of `/v1/models`.
- **The advisor node is a node in every other respect.** It can be paired, listed, set and stopped with MIGHTLING_NODE's commands.

### 5.2 The client side

The advisor is resolved separately from the coder's model server, first match wins:
1. `DREAMFERENCE_ADVISOR_HOST`.
2. `advisor_host` in the configuration files the launcher already reads.
3. **The remembered advisor**: an `advisor` object in `$CODEX_HOME/node.json` (`node` id, name, address, port, `last_seen`), found again by its id.
4. **A browse** for `_mightling-node._tcp` records with `advisor=1`. One: it is used and remembered, with one line. Several: an interactive `ling` asks once; `exec` skips the advisor with a line naming `ling node use --advisor <name>`, and never refuses to run.

**Unlike the coder's server, the advisor is browsed for even on a node.** MIGHTLING_NODE §6.1 tier 3 ("a GB10 never browses for itself") is about the node's own model; finding a *different* machine is the point here. The browse runs at launch only when nothing is remembered or the remembered advisor does not answer, alongside the wait for the coder's server, so it adds no time to a normal start.

`ling node list` gains a role column. `ling node use --advisor <name|address>` and `ling node forget --advisor` manage the remembered advisor.

---

## 6. "Maxes memory" against host safety

### 6.1 Where the 121.6 GiB go (proposed starting point, to be set by Phase 0)

| Use | GiB | Kind |
|---|---|---|
| Resident weights: NVFP4 experts ≈ 63, BF16 side layers ≈ 6.5, MTP ≈ 3.7 | ≈ 73 (blazux reports ≈ 75) | arena |
| KV pool | ≈ 10 (≈ 430K tokens) | arena |
| Linear-attention state, 4 running plus cached prefixes | ≈ 3 | arena |
| **Arena (`--mem-fraction-static` ≈ 0.72)** | **≈ 86** | GPU (not charged to the cgroup) |
| Activations, CUDA graphs, workspace | ≈ 4–6 | outside the fraction (assumed) |
| Engine host process, OS, Docker, a desktop | ≈ 5.5–8 | host |
| **Page cache for the 47.7 GiB n-gram table** | **≈ 15–20** | reclaimable, counted as available |
| earlyoom's line | 6.1 | must stay free |

**This is what "maxes memory" means here.** The arena is sized to the model plus the KV the advisor's concurrency needs. Everything else that host safety leaves goes to the page cache of the table, where it buys speed (fewer NVMe reads) and is given back when the kernel needs it. A larger arena would buy KV nobody uses: 430K tokens already holds four full packets. A larger KV pool does not improve answers; a warmer table cache may make them faster.

### 6.2 What stays exactly as it is

- `check_host_safety()`: 64 GB swap, `vm.min_free_kbytes`, the watermark factor, earlyoom at 6% or less.
- The PSI watchdog during the load, with its thresholds unchanged.
- earlyoom on, and the model container's `--oom-score-adj=800`, so if anything has to die it is the advisor, whose absence the coder tolerates (§4.9).
- The 12 GB reserve outside the arena.

### 6.3 What the pre-flight must learn

- **Count the table as a file, not as weights.** Today `start_server()` checks that weights plus drafters fit the arena. With `mmap_weights_gb` (§3.9), the resident part is checked against the arena and the table against disk. The table must not be counted twice, and must not be left out of a "fits in free memory" check that would then pass a model that cannot run.
- **Stop the node's sidecars before measuring free memory** (§6.4), so the pre-flight sees the memory the advisor will actually have.
- **Bound the table's page cache.** Page cache from a memory-mapped file is charged to the cgroup that first touched it, while GPU allocations are not (MIGHTLING_NODE §13.8 measured 3.84 GiB charged against 53.8 GB held). So the container's `--memory` cap bounds the table's cache and the engine's host memory, not the arena. The recipe sets the cap explicitly to the host process plus a table-cache budget, about 24 GiB, instead of "fraction plus headroom", which on this engine bounds nothing. Whether the engine survives that cap under load is Phase 0's first memory question.

### 6.4 What must not run on an advisor node

| Component | On an advisor node | Why |
|---|---|---|
| Diffusion sidecar | **not started** (`exclusive`) | up to 8 GiB cap plus GPU memory; it serves the coder's fast tools, which run on the coder's node |
| Speech-to-text, image search, Gmail service | **stopped** before the load | they serve the web UI and the agent on the coder's node |
| Web UI stack (Onyx Lite, retired in 1.7.0; `ling web` since) | **stopped**; `node enable` defaults to `--no-web` on such a node | about 0.6 GiB of host memory plus Postgres, and nobody chats on the advisor node (question 5 offers it as a second model in node 1's web UI instead) |
| SearXNG | **stopped** | the advisor does no web search |
| `ling-code` index runs, Night Shift, benchmark containers, script jobs (MIGHTLING_NODE §13) | **refused** while the advisor model is assigned | each assumes a coder's memory budget; a script job sent to an advisor node is refused with that reason |
| Desktop session | allowed, not budgeted beyond the 5.5–8 GiB above | the machine is a Spark, and someone may log in |

`server start` with an `exclusive` model prints each sidecar it stops, and `server start` with a `main` model brings them back as today. Nothing is removed.

### 6.5 The table is the new risk

A 47.7 GiB file paged in from NVMe for every token is a new kind of load for this host:
- **Freezes from page-cache saturation are on record:** a forum thread (2026-06-16) traces Spark lock-ups during model loads to a page cache that grew without bound, and fixes it with a cron job that drops caches. The cgroup cap of §6.3 is this spec's answer; Phase 0 checks it.
- **The PSI watchdog measures exactly this pressure.** A thrashing table would show up as `full` stall time. Phase 0 runs a 100K-token session of consults and records `full avg10`, `avg60` and MemAvailable. If steady state comes near the watchdog's thresholds, the fraction or the cache budget is lowered; the thresholds are not loosened.
- **Loading:** reading 124 GiB of files fills the page cache as it goes. Phase 0 records whether the load trips the watchdog, and whether the load needs the table read at all, or only mapped.

---

## 7. Product and install

### 7.1 The user's path

The person has a working Spark (`spark-1`) and has bought a second (`spark-2`).

1. **On `spark-2`:** the ordinary installer (MIGHTLING_NODE §9). It detects a GB10 and installs node and client. `install.sh --model qwen3.8-flash-next-advisor` names the model so the default 27B is not downloaded first; without it, the 27B arrives and is replaced in step 3.
2. **On `spark-1`:** `ling-admin node add spark-2`, the existing pairing (MIGHTLING_NODE §13.2, §18.6). The node's password is typed once.
3. **On `spark-1`:** `ling-admin node set spark-2 --model qwen3.8-flash-next-advisor`. This is the existing command: the node runs its own `main-model set`, `server stop`, `server start`, with its own pre-flight and watchdog. Because the entry is `exclusive`, that `server start` stops the sidecars of §6.4 by itself; the user never names them.
   - **Shortcut on `spark-2` itself:** `ling-admin advisor enable` = `main-model set qwen3.8-flash-next-advisor`, `server start`, `node enable --no-web`. `advisor disable` restores the default model and the sidecars.
4. **First load:** the download is about 133 GB (NVIDIA's NVFP4 build; 140 GB of free disk recommended). That is about 22 minutes at 100 MB/s, 45 minutes at 50 MB/s, and two hours on a 150 Mbit/s line. A Spark that already holds the checkpoint can copy it over the QSFP link with MIGHTLING_NODE §12.2's `node sync-model` once that exists. Then the load and compile, reported at over 5 minutes before blazux's loading patches (Phase 0 measures it). The advert says `loading` throughout.
5. **On `spark-1`:** nothing. The next `ling` finds the advisor by browsing (§5.2).

### 7.2 What the first node shows

| Where | What |
|---|---|
| `ling` start | one line, once per session: `Advisor: spark-2 · Qwen3.8-Flash-Next · ready`, or `· loading`, or nothing when there is none |
| The status line during a review | `Advisor on spark-2 is reviewing this turn (about 2 min)` |
| After a review | the advice as the next input when it blocks; otherwise a one-line verdict |
| `ling advise status`, `ling advise log` | §4.6 |
| `ling node list`, `ling-admin node list` | a role column: `main`, `advisor` |
| Night Shift's morning report | the advisor column (§4.10) |
| `ling-admin swe-bench report` | the advisor arm and its consult statistics (§8) |

---

## 8. Proving that it improves code quality

### 8.1 First, the runner's defects

MIGHTLING_PROMPT §6.1 lists the defects that would be measured in place of any change to the agent. They come first, because an advisor comparison made before them measures how well each arm copes with the defects:
1. **`/testbed` sources are not writable by the agent** (`root:root 0644`): `chmod -R a+rwX /testbed` before the agent starts.
2. **`rg` is missing from the images**, though the prompt names it.
3. **Everything left behind is submitted**, test files and fixtures included. An advisor would flag these, which would credit it with fixing a runner defect.
4. **A fresh baseline on one build**, three times.

And the scale question MIGHTLING_PROMPT §6.3 already answers: validate about **100 instances** (28 are validated today, about 2.2 GB of images each), at which three repetitions can resolve a difference of about ten points.

### 8.2 The cheap first signal: replaying recorded failures (before any node work)

The 24 trajectories of `acc-25` are on disk: 13 resolved and 11 not, with the 11 failures already classified (MIGHTLING_PROMPT §1.4: wrong place 4, incomplete 3, broke something 2, invented behaviour 2).
- **Build each packet** at the point the agent stopped, exactly as the `Stop` hook would (§4.4), and send it to the advisor offline. On this one Spark that means a window with the 27B stopped and Flash-Next loaded alone, which Phase 0 needs anyway. The 27B at `xhigh` can be replayed against the live server today.
- **Score on the 11 failures:** did the advisor say `revise`, did a finding name the actual cause (the reference patch's file for "wrong place", the missing second file or call site for "incomplete", the newly failing test for "broke"), and was its suggestion pointing at the reference fix? Judged by rule where possible (files named against files in the reference patch) and by reading where not.
- **Score on the 13 successes:** the false-alarm rate, `revise` with a `medium` or `high` finding on a resolved patch. A high rate here predicts harm, because the coder may "fix" a correct patch.
- **Worth it** only if the advisor names the cause in a meaningful share of the 11 and rarely flags the 13. If it does neither, the A/B is not run and the spec says so.

This is an afternoon's work, needs no hooks, no second Spark and no benchmark run, and is the most informative per hour of anything here.

### 8.3 The A/B on the benchmark

All arms on the same validated instances, the same `ling` build, the same coder model, **three runs each**:

| Arm | Coder | Advisor | Triggers |
|---|---|---|---|
| A | 27B, `none` | none | |
| B | 27B, `none` | Flash-Next | `stop` |
| C | 27B, `none` | **27B at `xhigh`** on the second Spark | `stop`: the second-look-plus-thinking control |
| D | 27B, `none` | Flash-Next | `stop`, `stuck`, `first-edit`, prompt block (Phase 2) |
| E | **Flash-Next**, `none` | none | why not swap the coder (§3.7) |

**What is read, in order of how much it can show at this size:**
1. **Per failure class.** For each instance A got wrong, by class: did B or C get it right, and was a finding that named the cause what made the coder change course? The classes are what a reviewer is meant to catch, and changes there are less noisy than the total.
2. **Harm.** Instances resolved in A and not in B where the coder acted on a finding. Every one is read. A reviewer that breaks correct patches must not ship on by default, whatever the total.
3. **Behaviour:** consults per instance; verdict mix; findings accepted and rejected by the coder; of those, how many were right (checked against the reference); consult latency and queue wait; added wall time and tokens.
4. **The resolved rate, last,** with MIGHTLING_SWE_BENCH's paired interval and McNemar test, and "No measurable difference." when the interval contains zero. **The expected gain is small**: Anthropic's own advisor gain was +2.7 points, while two identical runs here differed on 4 of 24 instances. A three-point effect needs on the order of a thousand paired instances to show; 100 × 3 can show about ten. A null total is therefore the likely outcome at this size, and readings 1–3 carry the decision.

**The decision rule:**
- B ships as the default if it causes no more harm than C, and catches more failure classes than C.
- If C is as good as B, the gain is the second look and the thinking, not the model, and the cheaper recipe wins.
- If E beats B, swap the coder (question 3).

**Contamination cuts both ways.** Flash-Next (August 2026) has very likely seen SWE-bench Verified's repositories and fixes (MIGHTLING_SWE_BENCH §8), and an advisor that recalls a gold patch would look like a good reviewer. Every finding is checked for verbatim lines of the reference patch (an n-gram overlap test) and the count is reported. The uncontaminated check is Night Shift on the project's own repositories (§8.4).

### 8.4 Night Shift

A fixed set of about 20 tasks from this project's own backlog, each with a test command, written down before the first run. Each runs once per arm (A and B), on alternate nights, from the same base commits. Read: tests passing; the advisor's findings and what the coder did with them; and the owner's review of each branch, merged as is, merged after edits, or discarded. Twenty tasks is a qualitative check, not a rate. Its value is that these repositories are not in any model's training data and the tasks are the ones Mightling is used for.

---

## 9. Alternatives considered

- **Codex's own `/review` with `review_model`.** It runs a full review thread, with tools, under another model name. But `spawn_review_thread` keeps the parent's provider (§2), so the review model must be served by the coder's own server, unless a patch adds a provider override. It is also user-invoked, not automatic. Rejected for Phase 1. A one-line provider override is a candidate Phase 3 patch if an agentic advisor (next item) measures better.
- **An agentic advisor**: the advisor runs as `ling exec -s read-only` on the client, against the advisor node, and can open files and run `git` itself. It would review more accurately than a packet, at many requests and several times the latency. Codex's agent roles carry a `config_file` layer that could name the advisor's provider for a spawned subagent (read from `agent-roles/src`, not run). But the subagent route needs the model to choose to spawn one, and no recorded benchmark trajectory contains a subagent call (MIGHTLING_PROMPT §11.7). Kept as a Phase 2 arm, behind the packet.
- **A second coder with a judge** (best-of-N): doubles the code execution on the client and needs a selector; the advisor is cheaper and composes with it later.
- **A front-door proxy that routes some requests to the advisor:** rejected for the same reasons as MIGHTLING_NODE §5.4, and a router cannot know when a review is due.
- **A summary in place of the transcript:** the transcript fits (§4.4). Summaries lose the tool history (MIGHTLING_COMPACTION §9), and summarising costs the coder's time.
- **The diffusion slot as the advisor:** the 0.5B sidecar failed every reasoning role it was tried in (MIGHTLING_COMPACTION §9.3).
- **A cloud model as the advisor:** stronger, but Mightling's model traffic does not leave the LAN.
- **A `/advisor` slash command now:** the patch series has 325 bytes left of its cap. Phase 3, if the TUI needs a switch the configuration and `ling advise` do not give.

---

## 10. Phases

| Phase | Work | Done when |
|---|---|---|
| 0a, this Spark, no second one | In a window with the 27B stopped: Flash-Next on SGLang with `--ple-offload-backend file` on SM121, and the blazux vLLM build if SGLang fails; resident memory, page cache, cgroup charge and PSI during the load and over a 100K-token run of consults; prefill at 8K, 32K and 96K; decode with thinking at `medium` and `xhigh`; MTP acceptance; prefix-cache hits on a second consult of the same session (PREFIX_CACHE's hybrid-GDN findings apply); the NVFP4 canary; download and load time; whether Esc ends a long `Stop` hook without killing the turn; a trusted `Stop` registration in the TUI; the character-to-token ratio of real packets | each has a measured answer recorded here, and the recipe's fraction and cache cap are set |
| 0b | MIGHTLING_PROMPT §6.1's runner fixes and 100 validated instances; the offline replay of §8.2 with Flash-Next and with the 27B at `xhigh` | the replay's numbers are in §8.2; a go or no-go for the A/B |
| 1 | `role`, `exclusive` and `mmap_weights_gb` in `ModelSpec`; the advisor entry and recipe; the pre-flight changes; `server start`'s exclusive profile; the `advisor` TXT record and `main` suppression; `ling advise` with the `Stop` hook, the gate, the packet, the schema, fail-open and the log; the client resolution and `node.json`'s `advisor` entry; `ling-admin advisor enable\|disable`; Night Shift's report column; `swe-bench run --advisor` with the forwarder and manifest fields | arms A, B, C and E run on two Sparks, and §8.3 is filled in |
| 2 | The `stuck` and `first-edit` triggers and the prompt block as arm D; priority scheduling; the agentic advisor as an arm; the Night Shift comparison of §8.4 | arm D and the Night Shift set measured |
| 3 | Only if the measurements support it: a `/advisor` command (patch, cap raise); `/review` with a provider override; the advisor as a second model in node 1's web UI; a stacked advisor (§3.8) | |

---

## 11. Tests

Offline, with no network, no second machine and no model:
- **The gate:** each condition of §4.3 alone and together; `stop_hook_active` never consults; a question-only turn, a read-only turn and a turn that stops to ask are not reviewed; outside git, the `apply_patch` headers decide.
- **The packet:** deterministic for the same rollout, so the prefix is byte-identical between two consults of one session; the cut order of §4.4 at a small budget; the first user message, the diff and the question are never cut; outputs clipped to 1,000 + 1,000 characters; the cut list recorded.
- **The answer:** the schema accepted; a malformed answer retried once, then logged without blocking; blocking only on `revise` with a `medium` or higher finding; the continuation text capped at 6,000 characters.
- **Fail-open:** no advisor, `state=loading`, a refused port, a 2 s connect timeout, a queue too long, a 420 s timeout and an engine error. Each exits 0, writes nothing to stderr and blocks nothing, within its time bound, with a stand-in server.
- **Registration:** the `Stop` hook written with its trust hash next to the ledger's (the same hash function, `compaction.rs`), the user's hooks untouched, removed at `off`.
- **Discovery:** `advisor=1` advertised and `main` absent for the advisor entry; the choice rule unchanged with one `main` and one `advisor` node; the advisor browsed for on a node; several advisors skipped in `exec` with the hint; `ling node use --advisor`.
- **Node side:** `server start` with an `exclusive` model stops the listed sidecars and starts no diffusion sidecar (docker mocked, per the conftest rule); the pre-flight counts `mmap_weights_gb` against disk and not the arena; a script job and a Night Shift run refused while the advisor model is assigned.
- **Benchmark:** the forwarder relays to a stand-in advisor and only to it; the manifest fields; resume keeps the arm.
- **Night Shift:** the report column from a recorded consult.
- **Egress audit:** sets the advisor level to `off` unless `--advisor`.

Live, on two Sparks: the user's path of §7.1 end to end; a consult from a TUI session, from `exec`, from a Night Shift task and from a benchmark container; the advisor node stopped mid-consult; four consults at once with the queue rule; `ling-admin audit egress` still passing on the coder's node.

---

## 12. Checked here, and assumed

| | Status |
|---|---|
| This machine's memory, swap, sysctl values, earlyoom arguments, free disk and sidecar footprints (§2) | **read here** on 2026-10-02 |
| A blocking `Stop` hook continuing a `ling exec` turn; the `<hook_prompt>` user message; `stop_hook_active` on the second stop; the coder refusing out-of-scope hook text and acting on an in-scope finding (two runs, the live 27B, untrusted hook with the bypass flag) | **run here** on 2026-10-02 |
| The same in the TUI; a trusted registration of a `Stop` hook (assumed to work as the ledger's `SessionStart` registration does) | **not run** |
| Codex hook semantics: the 600 s default timeout, `status_message`, `PreToolUse` deny, `PostToolUse` context, `CODEX_THREAD_ID`; `/review`'s pinned provider | **read from the pinned source**, not run |
| Flash-Next's parameters, architecture, NVFP4 composition, licence and scores; the 27B's scores; DeepSeek V4-Flash's and V4.1-Flash's sizes and scores; AA indexes of 40 and 34 | **read on the model cards and AA pages**, 2026-10-02 |
| Flash-Next's 75 GiB resident set, 34 tok/s, 2,400–3,000 tok/s prefill and 505–680K KV pool on one Spark | **reported** by one repository (blazux, vLLM 0.30 with patches) |
| SGLang's `--ple-offload-backend file` working on SM121 | **reported**, not seen run |
| GLM-5.3-Flash's NVFP4 two-Spark speed, MiniMax M2.7's scores, Intel int4 sizes, DeepSeek V4-Flash's KV per token | **from aggregators or summaries**, not checked |
| The latency budget, concurrency speeds, cache budget, and the arena split of §6.1 | **derived or assumed**, Phase 0 |
| That structured output works after the reasoning block in the pinned SGLang; priority scheduling in it; Esc ending a hook | **assumed**, Phase 0 |
| That the advisor improves anything | **unknown**; §8 |

---

## 13. Open questions

1. **Licence.** Flash-Next is under the Qwen Community License 1.0, with NVIDIA's Open Model License on the NVFP4 build. Neither text was reviewed here. The 27B is Apache 2.0, and Mightling is AGPL. The recipe would download the weights on the user's machine, as today. Is that acceptable, or should the advisor default to the 27B at `xhigh` (the runner-up) until the terms are read?
2. **The TUI's default.** `auto` makes an interactive editing turn that ran tests or claims completion wait one to four minutes for review. `ask` leaves the review to `ling advise`. Which is the default in the TUI, given that unattended runs use `auto` either way?
3. **If arm E wins** (Flash-Next as the coder beats the 27B with an advisor): swap the coder, and look for an advisor larger than one Spark holds?
4. **A stacked advisor** on two linked Sparks, three in all: wanted at about two index points for a third machine (§3.8), or only when a DeepSeek V4.1-class model fits two?
5. **The advisor in the web UI.** (Written for Onyx, retired in 1.7.0; in `ling web` the question becomes a model choice for Ask threads.) Node 1's `ling-admin chat configure` could register the advisor's model as a second provider, so the chat UI can use the stronger model directly. Wanted?
6. **Morning review of night branches.** The advisor node is idle at night. Should Night Shift also send each finished branch's full diff for a second, slower review before the report is written, apart from the in-task review?
7. **Telling the coder it will be reviewed** (§4.6): measure it as a separate arm, or leave it out?

---

## 14. Changes to other specs when this is built

- [MODELS](./DREAMFERENCE_MODELS.md): the `role`, `exclusive` and `mmap_weights_gb` fields; the `qwen3.8-flash-next-advisor` entry and its rationale.
- [INFERENCE](./DREAMFERENCE_INFERENCE.md): the recipe (§5); the pre-flight counting a memory-mapped table against disk and the page cache (§7); the exclusive profile of `server start`; the explicit cgroup cap for this engine.
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md): the `advisor` TXT record and `main` derived from `role` (§5.1); the advisor's resolution and the `advisor` entry in `node.json` (§6.1–§6.2); `ling node use|forget --advisor`; `install.sh --model`; jobs refused on an advisor node (§13.4).
- [MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md): `advise.rs` among the launcher modules; the `Stop` hook registered beside the ledger's; the `# Advisor` prompt block.
- [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md): `[night] advisor` and the report column.
- [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md): `run --advisor`, the forwarder on the internal network, the manifest fields, and the advisor arm in `report --against`.
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md): the audit sets the advisor off unless `--advisor`, which adds its address to the allow-list.
- [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md): the hook allowed at every level; `ling advise` from a command cut at `on`.
- [MIGHTLING_PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md): the advisor arms sit beside its §6.2 arms on the same fixed runner.
- [README](./README.md): the index row, and the deployment line once a second node is supported.

---

## Sources

Model cards and indexes (read 2026-10-02):
- [Qwen/Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next): parameters, architecture, licence, the benchmark table against Qwen3.8-27B (Aug 2026)
- [nvidia/Qwen3.8-Flash-Next-NVFP4](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4): what is NVFP4, BF16 and FP8; licence (2026-08-31)
- [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B): scores and architecture (Aug 2026)
- [deepseek-ai/DeepSeek-V4-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash): sizes and scores (2026-04)
- [deepseek-ai/DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash): sizes, KV, scores (2026-09)
- [zai-org/GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash): parameters, scores
- [Artificial Analysis: Qwen3.8-Flash-Next](https://artificialanalysis.ai/models/qwen3-8-flash-next) (40) and [Qwen3.8-27B](https://artificialanalysis.ai/models/qwen3-8-27b) (34)
- [unsloth/DeepSeek-V4-Flash-0731-GGUF](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF): GGUF sizes (via research summary)

Serving on the Spark:
- [blazux/qwen3.8-Flash-DGX](https://github.com/blazux/qwen3.8-Flash-DGX): Flash-Next on one Spark, table memory-mapped, 75 GiB resident, 34 tok/s (2026-09-28)
- [NVIDIA forum: Qwen3.8-Flash-Next](https://forums.developer.nvidia.com/t/qwen3-8-flash-next/381228): int4 with MTP, 43–47.5 tok/s (2026-09-09, 09-30)
- [NVIDIA forum: MiaAI Flash-Next recipe](https://forums.developer.nvidia.com/t/miaai-lab-new-qwen3-8-flash-next-nvfp4-recipe-for-1x-dgx-spark-1m-context-vision-video-37-tok-s-c1/382446) (2026-09-05)
- [Classmethod: DeepSeek V4-Flash on one Spark](https://dev.classmethod.jp/en/articles/dgx-spark-dwarfstar4-deepseek-v4-flash-bench/): 2-bit, 81 GiB, peak 115 GiB (2026-05-13)
- [MiaAI: DeepSeek V4-Flash on one Spark](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-One-DGX-Spark): 3.0 bpw, pruned, earlyoom disabled (2026-08-21)
- [Classmethod: GLM-5.3-Flash first touch](https://dev.classmethod.jp/en/articles/dgx-spark-glm-5-3-flash-first-touch/) (2026-08-30) and [NVIDIA forum: 60 tok/s GLM-5.3-Flash](https://forums.developer.nvidia.com/t/60-tok-s-glm-5-3-flash-on-a-single-dgx-spark/382140) (2026-09-03)
- [NVIDIA forum: DeepSeek V4-Flash on 2× Spark](https://forums.developer.nvidia.com/t/guide-deepseek-v4-flash-on-2x-dgx-spark-gb10-reproducible-vllm-serving-recipe-up-to-1m-token-context/374742) (2026-06-27); [hazyumps/deepseek-v4-flash-gb10](https://github.com/hazyumps/deepseek-v4-flash-gb10) (deprecated 2026-07-31)
- [PixelML: Flash-Next NVFP4 on two Sparks](https://huggingface.co/PixelML/Qwen3.8-Flash-Next-NVFP4-Dual-DGX-Spark) (2026-08-26)
- [NVIDIA forum: page-cache saturation freezes](https://forums.developer.nvidia.com/t/fixed-dgx-spark-freezing-and-lockup-issue-unable-to-load-new-models-due-to-cache-saturation/373483) (2026-06-16)
- [NVIDIA DGX Spark known issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html) (updated 2026-09-10)
- [vLLM blog: vLLM on DGX Spark](https://vllm.ai/blog/2026-06-01-vllm-dgx-spark) (2026-06-01)
- [vLLM #50925](https://github.com/vllm-project/vllm/issues/50925) (NVFP4 MoE falls back to Marlin on SM121, 2026-08-03), [vLLM #45317](https://github.com/vllm-project/vllm/issues/45317) and [PR #54929](https://github.com/vllm-project/vllm/pull/54929) (sparse MLA on SM121), [FlashInfer #3170](https://github.com/flashinfer-ai/flashinfer/issues/3170) (SM121 support audit, 2026-04-24)
- [NVIDIA playbook: Connect Two Sparks, performance guide](https://raw.githubusercontent.com/NVIDIA/dgx-spark-playbooks/main/nvidia/connect-two-sparks/assets/performance_benchmarking_guide.md); [NVIDIA forum: two Sparks over ConnectX-7](https://forums.developer.nvidia.com/t/two-dgx-sparks-over-the-connectx-7-direct-link-setup-notes-throughput-numbers-and-two-questions-federated-vs-cluster-telemetry/376298) (2026-07-10); [NCCL latency on the Spark](https://contact.alessandrosangiorgi.net/posts/dgx-spark-nccl-collective-latency/) (2026-06-25)
- [dev.to: GB10 memory sizing](https://dev.to/conatusai/dgx-spark-gb10-memory-sizing-for-llm-serving-the-numbers-42b7) (undated)

The advisor pattern:
- [Anthropic: advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) and [The advisor strategy](https://claude.com/blog/the-advisor-strategy) (2026-04-09)
- [Anthropic: optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence) ("the advisor can only hand over capability the executor lacks")
- [Aider: architect/editor](https://aider.chat/2024/09/26/architect.html) (2024-09-26) and [QwQ as architect](https://aider.chat/2024/12/03/qwq.html) (2024-12-03)
- [Cline discussion #12959](https://github.com/cline/cline/discussions/12959) (2026-08/09), a plan model reviewing act attempts, no results
