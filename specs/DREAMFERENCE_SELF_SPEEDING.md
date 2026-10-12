# Self-Speeding — the drafter learns your code

**Status:** proposed, **nothing built** (revised 2026-10-03 for the Qwen3.8 / SGLang stack, brought onto the renamed tree on 2026-10-12 with the draft depth the registry has run since 2026-10-09; the first version, written for the 122B on vLLM, is in git history). Published facts are cited in §11 with the date read; numbers measured on this machine say so; everything else is marked *(unverified)*.
**Scheduled (the owner's decision, 2026-10-12):** part of the plan for faster decode. Phase 0 (§3, read-only: acceptance per class from `/metrics`) is built first; training (§4–§5) needs the overnight window with the model server stopped, so it follows the benchmark nights. A promoted drafter speeds up production SGLang directly, and ling-engine's M5 trial then serves it under the engine's exact verify (ling-engine `specs/DREAMFERENCE_LING_ENGINE_BACKLOG.md` §19.20).
**Target:** the speculative drafter of the default model, `qwen3.8-27b-nvfp4-dflash2`: target `RadixArk/Qwen3.8-27B-NVFP4` on SGLang (`lmsysorg/sglang` v0.5.19, pinned by digest), drafter `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` (method `DFLASH`, **12** draft tokens, `quantization: modelopt_fp4`), all pinned by revision in the model matrix.
**Builds on:**
- the registry's `speculative_config` and `resolve_speculative_config()` ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- SGLang's `/metrics` (`--enable-metrics` is in the recipe);
- the session logs in `~/.mightling/sessions/`;
- the overnight window, runner lock and admission of [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md), and host safety ([INFERENCE](./DREAMFERENCE_INFERENCE.md), `check_host_safety`, the PSI watchdog).

---

## 1. Goal

Make `ling` faster on *this user's* work by fine-tuning the drafter on the user's own sessions, at night and on this machine. Nothing leaves the host, and the answers do not change.

**Why it is safe to try.** Speculative decoding is lossless: the target verifies every drafted token, so a better or worse drafter changes speed, not output. The risk is a slower machine, and §6 refuses to promote a drafter that is slower.

**Why it might be worth it, and why it might not.**
- **The drafter is where the speed is.** DFlash2 is why the default runs on SGLang at all: code 50.3 and JSON 87.0 tok/s here against 26.0 with no speculation (published single-Spark figure), and ~126 tok/s when copying ([MIGHTLING_FAST_TOOLS](./DREAMFERENCE_MIGHTLING_FAST_TOOLS.md) §1).
- **One live reading.** On 2026-10-03, during tonight's SWE-bench run (agent traffic, several streams), `sglang:spec_accept_length` read **4.65** accepted tokens per verify with **16** drafted (`spec_accept_rate` 0.243). One sample of a gauge whose window is *(unverified)*, taken at the depth then served.
- **The depth is settled.** On 2026-10-09, 12 draft tokens measured **+7%** over 16 on the replay set (48.3 against 45.1 tok/s single stream, two runs each in one session; 5.23 against 4.99 accepted per step, speculation still exact), and 8, 10 and 14 gained nothing ([INFERENCE](./DREAMFERENCE_INFERENCE.md), the registry's notes). The registry has run 12 since; everything below is measured and trained at 12 (§3.2).
- **Published:** the base drafter's card reports 4.39–5.46 accepted per verify on HumanEval, MBPP, GSM8K and MATH at its recommended block of **8** (7 draft tokens). The NVFP4 calibration recovered BF16's acceptance (3.60 against 3.71 per 8). Domain retraining of drafters is reported to help (first version of this spec, §1); none of those results is for DFlash2 on agent traffic.
- **What could make it pointless:** if the user's sessions accept as well as the fixed probes (§3), there is little to adapt to, and Phase 0 says so before anything is trained.

**Non-goals:** changing the target model; training while the target serves (§5.3); sharing drafters (§7).

---

## 2. Surface

Everything is in `ling-admin`; no slash command, because tuning stops the model server the asking session depends on.

| Command | Effect |
|---|---|
| `ling-admin drafter stats [--since 7d]` | Acceptance per workload class from the measurement log (§3), plus the live gauges |
| `ling-admin drafter collect` | Builds the training set (§4). The server keeps running |
| `ling-admin drafter generate [--budget 2h]` | Has the target re-answer collected prompts at temperature 0, for on-policy targets (§4.2). The server keeps running; admitted like a night task |
| `ling-admin drafter tune [--budget 3h]` | Trains a candidate (§5). Refuses unless the model server is stopped. Checkpoints, resumable |
| `ling-admin drafter evaluate <id>` | A/B against the current drafter (§6); restores the server as it found it |
| `ling-admin drafter promote <id>` / `rollback` / `list` | As in the first version: an explicit, reversible override of the registry's drafter |

**At night.** `[night] drafter = "off" | "collect" | "tune"` (default `off`):
- `collect` runs `collect` and `generate` inside the night's normal admission, with the server up, after the queue is empty.
- `tune` adds, in the window's tail, a **maintenance step**: stop the model server, `tune` and `evaluate` within the remaining budget, restart the server with the drafter that serves best. This is a deliberate exception to Night Shift's rule that the runner never starts or stops the model server (NIGHT_SHIFT §5.2), so it is opt-in, holds the runner lock throughout (SWE-bench and the queue are excluded, as today), refuses while any `ling` session is open, and always ends with a health check of the restarted server. If the restart fails, the registry's drafter is restored and the morning report says so first.
- Promotion is never automatic unless `[night] drafter_auto_promote = true`.

---

## 3. Phase 0 — measure before training

### 3.1 Acceptance per class

SGLang exposes, per server: `sglang:spec_accept_length` and `sglang:spec_accept_rate` (gauges), `sglang:spec_verify_calls_total` (counter), `sglang:spec_num_draft_tokens` (16 when read from this machine's `/metrics` on 2026-10-03; 12 since 2026-10-09). Per-request acceptance is not exposed *(unverified: whether a request-level field exists in the response's `meta_info`; if it does, attribution becomes exact and this section simplifies)*.

`drafter stats` therefore samples when the server has been idle for 10 minutes: read the counters, send one request at temperature 0, read again. Two probe sets:
- **fixed probes** (`WORKLOAD_PROBES`), comparable over time;
- **replayed prompts:** up to 50 prompts sampled from the user's recent sessions and classified (§4.1).

Each reading appends to `~/.cache/dreamference/drafters/acceptance.jsonl`:

```json
{"at": "…", "drafter": "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal@bd7a934", "depth": 12, "class": "code", "verify_calls": 212, "accept_length": 4.7, "tok_s": 49.8}
```

### 3.2 Depth: fixed at 12

The first version of this revision planned a sweep of 8, 12 and 16 draft tokens before any training (the recipe ran 16; the base drafter's card recommends a block of 8). That question was answered on its own on 2026-10-09: 12 beat 16 by 7% held-out tok/s on the replay set, with acceptance 5.23 against 4.99 per step, and 8, 10 and 14 gained nothing, so the registry's `num_speculative_tokens` is **12** (`hardware/model_matrix_registry.py`). It is not a variable here: the stock drafter's readings (§3.1), every candidate's training (§5) and every evaluation (§6) use 12, so a gain is the drafter's and not the depth's. A tuned drafter that accepts longer runs might move the best depth; re-sweeping is left to a later revision, after a promotion, and would be a registry change made on its own.

**Done when:** a week of readings exists, and `drafter stats` shows acceptance per class for the stock drafter at 12.

---

## 4. Training data

### 4.1 Source and what it holds today

Codex rollout files under `~/.mightling/sessions/YYYY/MM/DD/*.jsonl`: each turn's context and the target's response, including tool calls. **Only sessions served by the current target** are used (the drafter learns one target's distribution).

Measured on 2026-10-03: 162 session files (17 MB), all dated on or after 2026-09-29, i.e. all from Qwen3.8; they hold 290 assistant messages (166K characters) and 897 tool calls (226K characters of arguments): **roughly 100K tokens of model-generated text** at four characters per token *(estimate)*. Many are tests (the live slash-command suite, audits, probes), so the user's own work is a fraction of that. This is far below the ~2k-turn, million-token scale published domain adaptations used *(unverified for DFlash2)*, which is why §4.2 exists.

SWE-bench runs (`~/.local/share/dreamference/swe-bench/runs/*/scratch/*/codex-home/sessions`) are also on-policy Qwen3.8 output on real repositories. They may be used for training but **never** for evaluation, and are reported separately so a gain is not credited to benchmark repositories.

### 4.2 On-policy generation at night

A DFlash drafter is trained to predict the target's own continuations, and SpecForge captures the target's features from the conversations it is given; it does not regenerate responses itself *(SpecForge training docs, 2026-10-03)*. Responses written by this target are on-policy already; to grow the set without waiting weeks, `drafter generate` replays collected *prompts* (user turns plus their context up to that point) and lets the target answer them at temperature 0, with the server up, admitted like a night task. At the measured ~50 tok/s single-stream code decode, an hour yields ~180K response tokens per stream *(estimate; the night's admission decides the stream count)*.

### 4.3 Selection and exclusions

As in the first version: classes (`code`, `structured`, `tool-call`, `prose`, `repetitive`), balance toward the classes with the most headroom in §3, dedup by response hash, a 10% held-out split **by session**, and a minimum size (set from Phase 1, not fixed now). Dropped, with counts reported: turns whose context holds Gmail, Drive or Calendar tool output or `ling-admin gmail read` output; `.env`-like content and credential-pattern matches; sessions under `drafter.exclude_paths`; ephemeral sessions.

---

## 5. Training

### 5.1 Framework and starting weights

- **SpecForge** (MIT) has DFlash2 support (online training since August 2026): DFlash2 is selected by a draft config whose architecture is `DFlash2DraftModel`, with `training.strategy: dflash`; warm start from existing weights with `model.draft_checkpoint_path`; export to a Hugging Face directory with `specforge export --to hf`.
- **Start from the BF16 base drafter**, `incoai/Qwen3.8-27B-DFlash2` (Apache-2.0, 2B parameters, 3.53 GB): the served NVFP4 file is a post-training quantization of it and is not a training checkpoint. The licence question of the first version is answered: Apache-2.0 permits local derivatives.
- AdaFlash's code still has no licence; its paper may be used, its code may not be copied.

### 5.2 Two routes, both on one GB10

- **Online:** SpecForge drives a local SGLang server as the target-capture backend (`model.target_backend: sglang`). Its checked-in DFlash2 recipe owns **two GPUs**, one for capture and one for training; whether capture server and trainer can share GB10's single GPU and unified memory is *(unverified)* and is the first Phase 2 check.
- **Offline (the fallback):** capture features once (`data.hidden_states_path`), stop the capture server, then train the drafter alone. Capture is a prefill pass: at the measured ~1,700 tok/s prefill, 1M tokens is ~10 minutes. Disk is the cost: hidden size × captured layers × 2 bytes per token; at a hidden size of 5,120 and 5 captured layers (both *unverified* for this target and drafter) that is ~51 KB per token, ~51 GB per million tokens, under `~/.cache/dreamference/drafters/<id>/states/`, deleted with the candidate.

### 5.3 Memory, and why the server must stop

Fine-tuning 2B parameters with Adam in mixed precision holds bf16 weights (4 GB), fp32 master weights (8 GB), two fp32 moments (16 GB) and gradients (4–8 GB): **~32–36 GB plus activations** *(estimate)*. While the default model serves, the host has ~38.7 GB available and earlyoom acts at ~6 GB: training beside the server would leave no margin, and host RAM and GPU exhaustion are indistinguishable on GB10. So `tune` refuses while the server runs, is admitted against available memory minus a reserve and the last recorded peak ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md) §6.4's rule), runs in a memory-capped systemd scope, and checkpoints so `--budget` or the window's end stops it cleanly. With the server stopped ~100 GB are available, enough for the online route's target (~20 GB of weights) and the trainer together, if the single-GPU question resolves.

Compute is not the constraint *(estimate)*: ~6 × 2B FLOPs per trained token is ~12 PFLOP per million tokens per epoch, minutes to tens of minutes on GB10.

### 5.4 Output

`~/.cache/dreamference/drafters/<id>/`: the exported BF16 weights (loadable by SGLang as the speculative draft model), and `meta.json` (target and revision, base drafter and revision, data size, class mix, exclusions, steps, time, peak memory).

**Served as BF16 first.** The NVFP4 calibration of the current drafter (round-to-nearest plus activation calibration on 460 on-policy conversations, scales `amax / (6·448)`, ModelOpt layout) saved 2.2 GB of VRAM and recovered BF16's acceptance. A tuned BF16 drafter costs those 2.2 GB of KV pool (today's pool is ~157K tokens beside the sidecars; the size of the loss is *(unverified)*); re-quantizing a tuned drafter the same way is Phase 3, once a BF16 candidate has shown a gain.

---

## 6. Evaluation and promotion

### 6.1 Protocol

`drafter evaluate <id>` starts the server with the candidate and measures, one request at a time at temperature 0: the fixed probes and up to 200 held-out session prompts (never SWE-bench ones). It repeats with the current drafter. Per run: accept length, accept rate, verify calls, output tok/s, per class.

**Swapping the drafter.** A restart with the candidate named in the speculative config is the baseline route. SGLang merged disk/IPC weight updates for DFlash draft runners on 2026-09-24 (sgl-project/sglang#40777), with the note that DFlash weight updates have no end-to-end coverage; whether the pinned v0.5.19 image contains it is *(unverified)*. A hot swap is Phase 3, behind a live test.

### 6.2 Correctness check

Lossless in principle, but the target is NVFP4 and batching can perturb numerics *(unverified on this stack)*. So the check is relative: the current drafter is run **twice** on the same prompts first; the candidate's rate of differing outputs against the current drafter must not exceed the current drafter's rate against itself. A larger rate fails the evaluation and keeps the differing prompts for inspection.

### 6.3 Promotion

The candidate must improve held-out tok/s by at least `drafter.min_gain` (5%), regress no class by more than `drafter.max_regression` (3%), and pass §6.2. Promotion writes an override of the registry entry's `speculative_config.model` (a local path, plus `quantization` removed for a BF16 drafter); `rollback` removes it. The last three candidates are kept. A promoted drafter is tied to its target and revision in `meta.json`; if the configured main model differs, the override is ignored with a one-line warning.

---

## 7. Privacy and the air gap

- **Data:** sessions, the training set, captured states and candidates stay under `~/.cache/dreamference/drafters/`, mode `0700`. The drafter weights are derived from the user's data and are treated like it: no command exports them, and published numbers are aggregates.
- **Network:** training needs nothing beyond the cached base drafter and SpecForge's installation. Installing SpecForge (pip) is the one step that needs the internet; `drafter tune` refuses to install anything at `/airgapped on` and says what to install first.
- **Gmail, Drive and Calendar** tool output never enters the training set (§4.3).

---

## 8. Phases and acceptance

| Phase | Builds | Done when (measured) |
|---|---|---|
| **0** | `drafter stats` | A week of `acceptance.jsonl` readings; acceptance per class for the stock drafter at 12 draft tokens (the depth is settled, §3.2) |
| **1** | `collect`, `generate`, `[night] drafter = "collect"` | Two nights produce a training set with its exclusion report; its size in response tokens recorded here; no night task delayed by more than its admission allows |
| **2** | `tune` (online if one GPU suffices, else offline), `evaluate`, `promote`/`rollback`, `[night] drafter = "tune"` | `collect → tune → evaluate` completes inside one 6-hour window; no earlyoom action and no watchdog trip; the server ends healthy with the drafter it started with or a promoted one; the result (gain or none) recorded here |
| **3** | NVFP4 re-quantization of a winning candidate; hot swap if SGLang's draft-weight update works on the pinned image | The quantized candidate's acceptance within 3% of its BF16 form, and the KV pool restored to within 2% of today's; a live swap test passes §6.2 |

---

## 9. Tests

- **Unit:** session parsing and classification; exclusions (fixture sessions with Gmail and Drive tool output and a fake credential); held-out split by session; SWE-bench sessions kept out of evaluation; refusal of `tune` while the server runs and while a `ling` session is open; promotion thresholds; the relative correctness check with identical, self-divergent and candidate-divergent fixtures; rollback; target mismatch; the override reaching the SGLang launch command.
- **Fake server:** a scripted `/metrics` with SGLang's metric names and a chat endpoint.
- **Live smoke (manual):** `collect`, a 20-minute `tune --budget`, `evaluate`; it proves the pipeline, not a gain.

## 10. Open questions

- Does SGLang return per-request acceptance (§3.1)? It would replace idle sampling.
- Can SpecForge's capture server and trainer share GB10's one GPU (§5.2)?
- Hidden size and captured layers of this target/drafter pair, for the offline route's disk cost (§5.2).
- ~~Is a 16-token depth right for agent traffic?~~ Answered on 2026-10-09: 12, +7% over 16 (§3.2).

## 11. Sources (read 2026-10-03)

- [SpecForge](https://github.com/sgl-project/SpecForge) and its [training guide](https://github.com/sgl-project/SpecForge/blob/main/docs/sections/basic_usage/training.md): DFlash2 via `DFlash2DraftModel`, online and offline modes, `model.draft_checkpoint_path`, `specforge export`.
- [incoai/Qwen3.8-27B-DFlash2](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2): Apache-2.0, 2B BF16, block 8, acceptance per benchmark; no training recipe published.
- [maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal](https://huggingface.co/maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal): derived from incoai's drafter, RTN + activation calibration on 460 on-policy conversations, 3.53 → 1.37 GB, 3.60 vs 3.71 accepted per 8.
- [sgl-project/sglang#40777](https://github.com/sgl-project/sglang/pull/40777): disk/IPC weight updates for DFlash draft runners, merged 2026-09-24, no end-to-end coverage.
- [NVIDIA NeMo AutoModel: train a DFlash drafter](https://docs.nvidia.com/nemo/automodel/recipes-e2e-examples/dflash-speculative-decoding) and [NVIDIA/Model-Optimizer#2216](https://github.com/NVIDIA/Model-Optimizer/pull/2216): alternative training paths, not evaluated here.
