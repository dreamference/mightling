# Mightling 2.0: the postponed work

**Status:** Proposed (2026-10-27)
**Scope:** Every item the owner decided between 2026-10-22 and 2026-10-27 to wait for Mightling 2.0, the `1.8.0` step that precedes 2.0 for the model server, and the other on-hold items recorded across `specs/`.

---

## 0. Overview

Between 2026-10-22 and 2026-10-27 the owner decided that six tracks wait for Mightling 2.0, so the `1.x` releases can ship the current feature set first. Three of these decisions are already recorded in a repository:

- the AMD / Windows port: ling-engine commit `7a5e8df` and `specs/DREAMFERENCE_LING_ENGINE_AMD.md` on ling-engine branch `specs/amd-port`;
- several drafters: ling-engine `specs/DREAMFERENCE_LING_ENGINE_MULTI_DRAFTER.md`, merged as `1a3e3fb` on ling-engine `origin/main`;
- the local search index: commit `2a419ec3` on branch `agy/local-search-spec`.

The rest are recorded here for the first time: the model server's `1.8.0` step and its 2.0 default, the Discord bridge, web search's paid providers, reselling and `google cse`, and Matrix on the overlay. The pinned `ling-engine/` submodule does not yet contain the ling-engine documents named above. Mightling's own `specs/` carries no other `2.0` label; four further items are on hold, switched off or left to later phases, and §7 collects them so nothing is lost.

| # | Item | Target | Primary spec(s) |
|---|---|---|---|
| 1 | `ling-engine` (`ling-serve`) becomes the default model server (`1.8.0` is the step before) | `1.8.0` → `2.0` | [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md), [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md), [DREAMFERENCE_RELEASE_1.7.2.md](./DREAMFERENCE_RELEASE_1.7.2.md), [DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md), `ling-engine/` submodule |
| 2 | Several drafters and own-session tuning (a larger drafter: no-go) | `2.0` | ling-engine `DREAMFERENCE_LING_ENGINE_MULTI_DRAFTER.md` and `DREAMFERENCE_LING_ENGINE_BIG_DRAFTER.md` (ling-engine `origin/main`), [DREAMFERENCE_SELF_SPEEDING.md](./DREAMFERENCE_SELF_SPEEDING.md), [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md), [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md), [DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md), [together-ai-survey-2026-10-13.md](./research/together-ai-survey-2026-10-13.md), [sglang-team-survey-2026-10-13.md](./research/sglang-team-survey-2026-10-13.md) |
| 3 | The Windows port of `ling-engine` for AMD Strix Halo (Ryzen AI Max+ 395, 128 GB) | `2.0` | ling-engine `DREAMFERENCE_LING_ENGINE_AMD.md` (branch `specs/amd-port`), [DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md), [DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md), [windows-node-2026-10-14.md](./research/windows-node-2026-10-14.md) |
| 4 | Discord bridge (DM only in Phase 1) | `2.0` | [DREAMFERENCE_MIGHTLING_DISCORD.md](./DREAMFERENCE_MIGHTLING_DISCORD.md), [DREAMFERENCE_MIGHTLING_CHAT.md](./DREAMFERENCE_MIGHTLING_CHAT.md), [DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md), [DREAMFERENCE_MIGHTLING_CHAT_REPOS.md](./DREAMFERENCE_MIGHTLING_CHAT_REPOS.md) |
| 5 | Web search: paid search API providers, reselling them, SearXNG's `google cse` engine, and the local search index | `2.0` | [DREAMFERENCE_MIGHTLING_AIRGAPPED.md](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md), [DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md), [DREAMFERENCE_MIGHTLING_ASK.md](./DREAMFERENCE_MIGHTLING_ASK.md), [DREAMFERENCE_RELEASE_1.7.1.md](./DREAMFERENCE_RELEASE_1.7.1.md), [DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md](./DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md); `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` (pending draft on branch `agy/local-search-spec`, not on `main`); no spec yet for paid search API providers or reselling |
| 6 | Matrix on the remote-access overlay | `2.0` | [DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) (§14.5), [DREAMFERENCE_MIGHTLING_CHAT.md](./DREAMFERENCE_MIGHTLING_CHAT.md) |
| 7 | Other on-hold and later-phase work in `specs/` (advisor node, diffusion `ling fast`, cross-device sync Phases 3–4, Forgejo over remote access, vision OCR queue and Drive PDFs) | `2.0` / on hold | [DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md](./DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md), [DREAMFERENCE_MIGHTLING_FAST_TOOLS.md](./DREAMFERENCE_MIGHTLING_FAST_TOOLS.md), [DREAMFERENCE_MIGHTLING_SYNC.md](./DREAMFERENCE_MIGHTLING_SYNC.md), [DREAMFERENCE_MIGHTLING_FORGEJO.md](./DREAMFERENCE_MIGHTLING_FORGEJO.md), [DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md](./DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md), [DREAMFERENCE_PDF_SEARCH.md](./DREAMFERENCE_PDF_SEARCH.md) |

---

## 1. `ling-engine` becomes the default model server in 2.0 (`1.8.0` is the step before)

### What it is

`ling-engine` (`ling-serve`) is Mightling's own C++/CUDA model server for `RadixArk/Qwen3.8-27B-NVFP4` with the DFlash2 drafter on the NVIDIA GB10. It is opt-in beside SGLang on `main` for `1.7.2`. Two decisions set when it becomes the default:

| Decided | Gate | Where recorded |
|---|---|---|
| 2026-10-26 (the owner) | The default once it reads images, serves adapters and runs more than two requests at once | `docs/dev/models-and-engines.md` ("ling-serve beside SGLang"), [DREAMFERENCE_RELEASE_1.7.2.md](./DREAMFERENCE_RELEASE_1.7.2.md), the comment above `QWEN38_LING_RECIPE` |
| 2026-10-27 (the owner) | `1.8.0` is the step before 2.0: `ling-serve` becomes the default once it handles 4 or more concurrent conversations and a benchmark night shows no loss against SGLang, cut only with the owner's OK. 2.0 builds on that engine | here, and `docs/dev/release-1.8.0.md` (with the owner's 2026-10-28 bar: 4-session pooled decode ≥ 34.6 tok/s, greedy-identical paged vs flat output, and a night with no loss) |

### Spec(s)

- `docs/dev/models-and-engines.md` ("ling-serve beside SGLang, opt-in (1.7.2)": the recipe, the image, the launch and the measurements) and `docs/dev/release-1.8.0.md` (the `1.8.0` cut checklist and bar)
- [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md) (§1, the default model and its drafter)
- [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) (§5.3, the SGLang recipe `ling-serve` would replace, and custom tools)
- [DREAMFERENCE_RELEASE_1.7.2.md](./DREAMFERENCE_RELEASE_1.7.2.md) (the opt-in engine and its measured numbers; still a draft)
- [DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) (§9, Phase 2: LoRA in `ling-engine`)
- The `ling-engine/` submodule (`DREAMFERENCE_LING_ENGINE_INTEGRATION.md`, `DREAMFERENCE_LING_ENGINE_BACKLOG.md`, `DREAMFERENCE_LING_ENGINE_RESEARCH.md`) and `docs/dev/host-safety.md`

### Dependencies

- A shared paged KV and recurrent GDN state pool in `ling-engine` so 4+ concurrent conversations fit beside the host's reserves. At commit `bdda3a2` each resident session allocates its KV and state at start, about 19 GB at 262,144 tokens, the engine runs its tested default of 2 sessions, and it takes about 60 GB in all (`docs/dev/models-and-engines.md`, `docs/dev/host-safety.md`).
- Cross-request prefix KV reuse in `ling-engine` (at `bdda3a2`, a second prompt sharing a long prefix reused only 16,384 tokens).
- A SWE-bench night against the patched SGLang v0.5.21 recipe (`SGLANG_PHASE_MERGE_PATCH`) showing no loss. Night 17 (2026-10-26 to 27) compares `b8e9451` with that recipe.
- For the 2026-10-26 gate: images (an image part becomes the text `[image]` today, and `ling-serve` answers no `/model_info`) and unmerged LoRA serving ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) §9).
- The owner's OK before cutting `1.8.0`.

### What was already measured or built

- Opt-in support, on `main` for `1.7.2` (its release notes are still a draft): `QWEN38_LING_RECIPE` (`engine_recipes["ling"]` on `qwen3.8-27b-nvfp4-dflash2` in `dreamference/hardware/model_matrix_registry.py`), `LingServeLaunchBuilder` (`dreamference/vllm_server/ling_serve_launch_builder.py`), `ling-admin server start --engine ling`, `server_engine = "ling"` in `DreamferenceConfig`, and `DREAMFERENCE_SERVER_ENGINE=ling`.
- Mightling's side of the `1.8.0` switch, merged on 2026-10-28 (`docs/dev/release-1.8.0.md`, inert at the `1.7.2` pin): `QWEN38_DEFAULT_ENGINE = ENGINE_SGLANG`, `QWEN38_LING_SESSIONS = 4`, `QWEN38_LING_BATCH = 4` and `LING_ENGINE_KV_PAGES_SINCE = "f180b44a40369d7dd03f9ae97e7fa8be4ece1380"` (`dreamference/hardware/model_matrix_registry.py`); `LingServeLaunchBuilder.server_args` passing `--sessions 4 --batch 4 --kv-pages` when `"kv-pages"` is in `engine_features`; `VLLMServerManager.ling_serve_plan` running `--plan-only` for the pre-load memory check; `scripts/pin_ling_serve_image.py`; `ling-rs/src/share_scope.rs` sending `X-Ling-Share-Scope` (opaque owner + repository hash) on every model-server request for P4c prefix-cache scoping; `DreamferenceCLIController._engine_for_adapters` (`dreamference/cli/dreamference_cli_controller.py`) keeping launches with installed LoRA adapters on SGLang (falling back when `ling` comes from the default or config, and refusing `--engine ling` with exit 2); and the vision runtime libraries (`libpng16-16t64`, `libwebp7`, `libjpeg-turbo8`) in `docker/ling-serve/Dockerfile`.
- Container image `ghcr.io/dreamference/ling-serve:8d112d0d2539` (`sha256:7bff4e37e731893a6248b0632897a2740e1842e0b19b28a68f0eee22d670caf8`, ling-engine 8d112d0: bdda3a2 plus the tokenizer cache's mutex, rebuilt under neutral paths with binary SHA-256 `8e517c88…`; the earlier `8d112d0` build `sha256:9428996a…` and bdda3a2's `sha256:87807c50…` were deleted because their binaries carried a home path), built by `scripts/build_ling_serve_image.sh` on `nvidia/cuda:13.0.2-base-ubuntu24.04` with `python3` for the model gate: **681–682 MB** against SGLang's **33.7 GB**.
- Engine commit `bdda3a244a9cd24742346dce806561f7441405fd` (`b8e9451`, tested on SWE-bench nights 12 and 15, plus the parse-only tool-call fix `fix/toolcall-text` so tool calls with surrounding prose are emitted as structured calls), binary SHA-256 `598e5520…`, verified greedy-identical to night 15's binary on all 16 turns of `agentlong`.
- Freeform custom tools (`apply_patch` and Code Mode `exec`) are handled natively by `ling-serve` on `/v1/responses` without translation ([DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) §5.3).
- Live measurements on a GB10 node (2026-10-26) at `--max-context 262144` with DFlash2 draft block 12, CUDA graphs, PDL, tree verification, megakernel stages and 2 resident sessions:
  - Cold start to `/health` ready: **40 s** (against ~7.5 min cold `torch.compile` on SGLang).
  - Host memory delta: **+60 GB** (46 GB `MemAvailable` remaining on the 128 GB host).
  - The NVFP4 canary and the tool-call canary through `/v1/responses` passed (the tool call in **0.4–0.6 s**).
  - 600-token code answer: **74.9 tok/s** (7.05 accepted tokens/pass).
  - 204,857-token two-needle prompt (facts at 45% and 85% depth): both needles answered exactly; prefill **~850–900 tok/s** (228 s), decode **39.6 tok/s** at 205K context (152 ms/step, 6.09 tokens/pass).
  - Exactness check `ling-run --spec-check --max-context 262144` passed: speculative tokens and recurrent GDN state after rejections match plain single-token decode bit-for-bit.
  - `ling exec` with a workspace-write sandbox wrote and ran a file and its test through the model gate.

### Recorded ideas

- **Concurrency chosen during execution (the owner, 2026-10-30).** In `1.8.0` the number of concurrent sessions is fixed when the server starts: 4 by default, cut to 3, 2 or 1 with a warning when memory is short at start (the owner's decision of the same day). The 2.0 idea is to choose concurrency dynamically while the server runs: start with one session and grow towards four only when more conversations arrive (the owner), admit another session when memory and throughput allow it, hold new sessions back or park idle ones when memory tightens, and let clients that run several tasks (Night Shift, SWE-bench, `ling` with subagents) follow the number the engine can serve at that moment instead of a fixed `max_parallel`. It came from the 2026-10-30 bar night, where one GB10 used as a desktop had memory for 3 sessions but not 4. A paged KV and state pool is the likely precondition. Not specified yet.

- **Fork one long context into many short questions (the owner, 2026-10-30).** Read a long context once (a repository, a task, a long session), then fork it into many short questions with short answers, up to hundreds or a thousand, decoded together. The shared prefix is prefilled once and stored once; each fork adds only its own short suffix and, on the hybrid model, a copy of the linear-attention state at the fork point. Decode attention over the shared prefix should read it once per step for all forks (a shared-prefix attention kernel), which turns the step into compute-bound work instead of a thousand separate reads. Doing the same questions separately would prefill the long context once per question. Uses: grading Night Shift and SWE-bench attempts against one task, reviewing each file with the same repository context, subagents that share the parent's context. Likely needs paging with shared copy-on-write pages. Not specified yet.

- **A larger model across several GB10s, fed by fan-out (the owner, 2026-10-30).** Split a model too large for one GB10 across two or more nodes, layer by layer (pipeline parallel). One conversation gains nothing from this, because only one node works at a time. Many forks of one shared context keep every stage busy, so fan-out is what makes a multi-node large model pay. The links between nodes carry only activations, so ordinary Ethernet may do for pipeline parallel; tensor parallel would need the direct high-speed link. A likely split: the resident small model fans out wide to find candidates, and the large model confirms the few that matter (grading, review, the chooser). It runs beside the one-model-server-per-GB10 rule, which it would have to amend for the nodes it spans. Not specified yet.

- **Fan-out mode in ling-serve, guided by confidence (the owner, 2026-10-30).** Building on the fan-out ideas above:
  - **Scoring in one pass.** A request carries one context and many questions whose answers are a single token (yes or no, A or B). The engine evaluates every question's tokens in one pass over a tree that branches off the shared prefix, reusing the tree mask of speculative verification, and returns probabilities instead of sampled text. The context stays resident for a second round, such as confirming the first round's candidates.
  - **Diversity added per fork.** Each fork can carry a short addition (a strategy, an angle, a hypothesis such as "assume the problem is in function X", different worked examples). It is placed after the shared context, so the prefix stays shared. The additions can themselves come from a first fan-out ("list 30 ways to approach this"), and later nights can learn which additions win.
  - **Same question, many samples.** Many samples of one question at a temperature give a majority vote, a measure of agreement as confidence, and coverage where a check exists (tests). For single-token answers, sampling is replaced by reading the probability directly.
  - **Confidence as a signal (after DeepConf, 2025).** Each token's confidence is how concentrated the probability is on the top candidates. Averaged over a sliding window, the weakest stretch of a trace or patch is a better score than its mean. Uses: choosing among attempts (with a check for a bias towards short answers), weighting votes, and, online, stopping a fork early when a stretch falls below a bar set by the first forks, giving its compute to the others, and stopping sampling once the answers agree. Patches that differ in text but not in behaviour are grouped by behaviour (tests) before voting. A confidently wrong model defeats the signal, so it is combined with tests and judge votes, not used alone.
  - **API.** Our own speed extension beside the Responses API, per the API decision of 2026-10-12. Being measured first on SGLang (2026-10-30 and 2026-10-31) and on the best-of-3 chooser of nights 11 and 13. Not specified yet.

- **Reliability over long work (the owner, 2026-10-30).** Benchmarks of one issue put the open models close to the best closed ones, yet the closed ones are felt to work far better. The reason is that small errors per step compound over long work: at 100 steps, 99% right per step finishes about 37% of tasks and 97% about 5%. The 2.0 goal is not a cleverer model but a higher rate of right steps, and catching the wrong ones early. In order:
  1. **Measure where steps fail.** Classify every failed attempt by the first step that went wrong (misread issue, wrong file, malformed tool call, edit that does not apply, tests never run, loop, wrong fix), with its step count. The levers below are chosen from that table.
  2. **A check after every step.** After each edit the harness runs the relevant test, linter and type check and gives the result back, so a mistake is caught one step later, not fifty. Malformed tool calls are repaired, not left to derail the session.
  3. **Long work cut into short, checkable pieces.** A plan of small steps, each with its own done-check, inside one task, as Night Shift already does for whole tasks.
  4. **Branching only where the model is unsure.** Several continuations at the risky steps (the plan, the fix, the final patch), chosen by tests and confidence (the fan-out mode above); easy steps stay single.
  5. **Harness and model tuned to each other.** Prompts and tools measured on our own nights; later an adapter trained on the local model's own successful attempts (never on another vendor's outputs).
  6. **The right context.** Search, the code index and the installed source code, filtered by fan-out before it reaches the context.
  7. **Optional escalation** of the hardest planning and judging steps to a larger model (several GB10s, or a cloud model the user opts into).

  The measure is a benchmark of long tasks (100 steps and more) that reports the rate of right steps, beside SWE-bench. Levers 2 and 3 are expected to give the most (an estimate until the failure table exists).

- **A local model that answers searches with URLs (the owner, 2026-10-30).** Search engines block many of the automated requests behind `ling-search`. Octen-WebGR (arXiv 2610.35256, October 2026) maps a query straight to real URLs: the 4B model covers about 200 million pages in about 10 GB, decoding only identifiers that exist and mapping them back through a table, so it never invents a URL. On navigational queries it ranks the right page first 59.6% of the time, against 24.8% for dense retrieval and at most 37.5% for frontier models answering from memory; it is weak on rarely visited and new pages, and only navigational queries were measured. In 2.0 it becomes a backend `ling-search` tries first, off by default, falling back to the search engines when its confidence is low or the URL does not load. It runs as a sidecar with a hard memory bound beside the model server, and it also works with no network for the search itself. The released weights and identifier table are CC BY-NC 4.0 (not for commercial use; the inference code is Apache 2.0), so shipping it needs a commercial licence from Octen, or our own model trained the same way. **Decision (the owner, 2026-10-30): 2.0 builds our own, with codes, as a LoRA adapter on the local model.** Each URL in a developer-focused table (a few million pages to start: documentation, repositories, package indexes, issue trackers) gets a two-code identifier the way Octen builds them (an embedding of the URL and its queries, two rounds of k-means, a capacity-constrained assignment so no two URLs share a pair). The codes are written with tokens the model already has, mapped one to one, so the adapter needs no new vocabulary and the base model is untouched. The adapter is trained on query-to-identifier pairs we have the right to use, plus indexing pairs (the page's own text to its identifier), each seen many times, since repetition mattered more than model size in Octen's ablations. ling-serve decodes it with a beam over a prefix tree of the identifiers in the table, so every answer is a real URL, and applies the adapter only to these requests (they share no long prefix, so prefix caching is unaffected). New pages get identifiers by extending the table and a short further round of training. It is measured against SearXNG and against the plain local model on the same developer queries before it is switched on. Open: how well it does on developer queries (documentation, repositories, error messages) against SearXNG, and how the identifier table is kept fresh.

### Open questions

- How the two gates combine. The 2026-10-26 gate also asks for images and adapters, which the 2026-10-27 `1.8.0` gate does not name. As recorded on 2026-10-28 (`docs/dev/release-1.8.0.md`), `1.8.0` includes vision (`input_image`, `/model_info`), while unmerged LoRA serving in `ling-serve` ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) §9) waits for 2.0 and `DreamferenceCLIController._engine_for_adapters` keeps a node with installed adapters on SGLang. Structured output and `--api-key` are the other SGLang features `ling-serve` lacks.
- Whether SGLang stays in `ModelMatrixRegistry` as `--engine sglang` in 2.0 (for third-party models and fallback) or is removed from the default installation flow.
- How host-safety and container memory probes account for `cudaMalloc` unified memory allocations that sit outside the container's cgroup `memory.current` (the pre-load check now uses `VLLMServerManager.ling_serve_plan` with `--plan-only` to size the 4-session allocation before launch).

---

## 2. Several drafters and own-session tuning (a larger drafter: no-go)

### What it is

This work moves speculative decoding beyond the one DFlash2 drafter served today, in two ways:

- **Several drafters, one per kind of work.** ling-engine's MULTI_DRAFTER (the owner's decision of 2026-10-27) specifies it for 2.0: three drafters, `general` (today's), `work` for coding-agent turns and `ask` for prose, chosen per session from the workload the client declares. Each specialist is trained on about 20M tokens the target writes for public tasks, never the owner's sessions, and ships only if it beats `general` by at least 5% tokens/s on its own held-out set. Per-adapter drafter LoRAs ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) §7.2) and the sparse drafter head (`dreamference/qwen3.8-27b-sparse-head`) belong to the same track.
- **Tuning a drafter on the user's own sessions** (`ling-admin drafter`, [DREAMFERENCE_SELF_SPEEDING.md](./DREAMFERENCE_SELF_SPEEDING.md)), with SpecForge, overnight, with the model server stopped.

**A larger drafter is not pursued.** ling-engine's BIG_DRAFTER (2026-10-26/27) answers it: **no-go**. Doubling the drafter in NVFP4 adds 4.0–4.8 ms to the 104 ms step (measured), while the literature gives 3–10% more acceptance per doubling, so the expected gain is close to nothing. No bigger trained drafter exists for this target. Tuning today's size is the better lever, which is what the two items above do.

MULTI_DRAFTER and BIG_DRAFTER are on ling-engine `origin/main`; the pinned `ling-engine/` submodule does not have them yet.

### Spec(s)

- ling-engine `specs/DREAMFERENCE_LING_ENGINE_MULTI_DRAFTER.md` and `specs/DREAMFERENCE_LING_ENGINE_BIG_DRAFTER.md` (ling-engine `origin/main`)
- [DREAMFERENCE_SELF_SPEEDING.md](./DREAMFERENCE_SELF_SPEEDING.md) (the `ling-admin drafter` own-session tuning pipeline)
- [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md) (§1: the drafter and `dreamference/qwen3.8-27b-sparse-head`); the constants `QWEN38_SCALESEARCH_DRAFTER` and `QWEN38_RTN_DRAFTER` are in `dreamference/hardware/model_matrix_registry.py`
- [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) (§3, speculative decoding configuration and compile-cache signature)
- [DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) (§4.2, §7.2, §8.4, §9: paired target + drafter LoRA adapters)
- [together-ai-survey-2026-10-13.md](./research/together-ai-survey-2026-10-13.md) (§2.1, ScaleSearch on the DFlash2 drafter; §2.3, Aurora and the customized speculators)
- [sglang-team-survey-2026-10-13.md](./research/sglang-team-survey-2026-10-13.md) (§2.4, SpecForge's recipe for this model and long context)

### Dependencies

- `ling-engine` as the default model server (§1), with `sparse-head` support (from ling-engine commit `ec9dce8`; the 1.7.2 recipe's engine, `bdda3a2`, predates it), per-session drafter switching (MULTI_DRAFTER) and per-request drafter LoRAs ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) §9).
- SpecForge (MIT, `sgl-project/SpecForge`) in a training environment. A tuned drafter starts from the BF16 base (3.53 GB) and is served as BF16 first, which costs about 2.2 GB of KV pool; re-quantizing it to NVFP4 is SELF_SPEEDING's Phase 3 (§5.4).
- Night Shift's `runner.lock` ([DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md) §4, §5.2) and SELF_SPEEDING's maintenance step (§2), because training needs about 32–36 GB plus activations (an estimate) and the model server must be stopped first (SELF_SPEEDING §5.3).
- On-policy generation (`drafter generate`, SELF_SPEEDING §4.2) to grow the training set; MULTI_DRAFTER sizes each specialist at about 20M tokens.

### What was already measured or built

- The draft depth, measured on 2026-10-09 (SELF_SPEEDING §1 and §3.2, INFERENCE §5.3): **12 draft tokens** beat 16 by **+7%** single-stream on the replay set (48.3 vs 45.1 tok/s, 5.23 vs 4.99 accepted per step); 8, 10 and 14 gained nothing. With `scripts/decode_speed.py` at 12 tokens (INFERENCE §5.3): prose **32.0 tok/s**, code **68.6 tok/s**, JSON **84.1 tok/s**.
- Two NVFP4 quantizations of `incoai/Qwen3.8-27B-DFlash2` (2B parameters, 3.53 GB in BF16) are pinned in the registry:
  - `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` (`QWEN38_RTN_DRAFTER`, a third-party RTN-calibrated quantization, 1.37 GB), ×1.108 with tree verification (`docs/dev/models-and-engines.md`).
  - `dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch` (`QWEN38_SCALESEARCH_DRAFTER`, Dreamference's re-quantization of it with searched block scales, hosted on the `models-2026-10-27` release through `RELEASE_HOSTED_MODELS`), ×1.094 with tree verification; served since 1.7.2.
- The sparse drafter head `dreamference/qwen3.8-27b-sparse-head` (the target LM head's rank-256 factors and 32K list) is hosted on the same release (`RELEASE_HOSTED_MODELS` and `QWEN38_SPARSE_HEAD_ASSET` in `dreamference/hardware/model_matrix_registry.py`). It is fetched only for a ling-engine recipe that lists `sparse-head` in `engine_features`; the 1.7.2 recipe lists none.
- The signed adapter manifest (`AdapterManifest` in `dreamference/hardware/adapter_manifest.py`) accepts optional `drafter_base` and `drafter_adapter` keys, and `AdapterVerifier` hashes the drafter adapter's files with the rest ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md) §4.2, §7.2). Nothing serves a drafter adapter yet: the pinned engine runs the drafter unadapted.
- The session corpus, measured on 2026-10-03 (SELF_SPEEDING §4.1): 162 session files (17 MB) held roughly **100K tokens** of model-generated text, far below the million-token scale of published domain adaptations. That is why `drafter generate` exists (about 180K tokens per hour per stream, an estimate, §4.2).
- Nothing of `ling-admin drafter {stats,collect,generate,tune,evaluate,promote,rollback,list}` has been built, and nothing of MULTI_DRAFTER has been trained or built.

### Open questions

- Whether `ling-serve` (or SGLang) exposes per-request accepted-token counts in response metadata so `drafter stats` can measure acceptance by task class (shell, patch, Python, Rust, prose) without polling server-wide counters.
- What trains the drafters: MULTI_DRAFTER uses public tasks only, never the owner's sessions, while SELF_SPEEDING tunes on the user's own sessions. Does `ling-admin drafter` stay as the local route beside the shipped specialists?
- Whether the sparse head pays on GB10 once a recipe lists it.
- How `ling-serve` loads and switches drafters or per-request drafter LoRAs without re-recording CUDA graphs. On SGLang the drafter runs unadapted ([DREAMFERENCE_MIGHTLING_ADAPTERS.md](./DREAMFERENCE_MIGHTLING_ADAPTERS.md), status note and §8.4).
- Whether SpecForge training on a single 128 GB GB10 should use offline target hidden-state capture (~51 GB scratch disk per 1M tokens) or an in-process target forward pass.

---

## 3. The Windows port of `ling-engine` for AMD Strix Halo (Ryzen AI Max+ 395, 128 GB)

### What it is

Porting `ling-engine` (`ling-serve`) to AMD Strix Halo (`gfx1151`, Ryzen AI Max+ 395 with Radeon 8060S 40-CU RDNA 3.5 integrated graphics and 128 GB LPDDR5X-8000 memory) on native Windows 11, together with an engine supervisor in the launcher, a Windows memory watchdog and mDNS node advertisement, so a Strix Halo machine runs as a Mightling node without WSL2.

The port is **Windows only**. The owner decided this on 2026-10-27, replacing the Linux-first plan of the day before; Linux is "later, if ever". The same day the first milestone passed at the box's 140 W setting, and the owner postponed the port to 2.0 so 1.x is finished first (ling-engine `specs/DREAMFERENCE_LING_ENGINE_AMD.md` §5 and §6.7, commit `7a5e8df`, branch `specs/amd-port`).

### Spec(s)

- ling-engine `specs/DREAMFERENCE_LING_ENGINE_AMD.md` on branch `specs/amd-port` (the hardware ceiling, the options, the Windows-only plan of §5, and the first milestone in §6.5–§6.7)
- [DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) (the box, the backends, the node pieces on Windows in §6.3, the owner's decisions in §12, and the measurements in §13)
- [DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) (§8.3, a Mightling-managed engine, W1; §9, host safety on Windows; §16.3, signing; §23, the test campaign of 2026-10-26)
- [windows-node-2026-10-14.md](./research/windows-node-2026-10-14.md) (Windows node survey)

### Dependencies

- HIP kernels for RDNA 3.5 (`gfx1151`, wave32, 64 KB LDS; WMMA for FP16, BF16, INT8 and INT4, and no FP4 or FP8 matrix unit) in `ling-engine`: the streaming GEMM, Gated DeltaNet, attention, prefill and DFlash2 tree verification. TheRock 10.0.0 compiles them; the driver's own HIP runtime runs them (AMD study §5, §6.4).
- An engine supervisor in the launcher: the engine as a child process in a Job Object, and a JSON export of the model registry so Windows needs no Python. Designed, not built ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §6.3; [DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §8.3 designs it around `llama-server`).
- Host safety on Windows ([DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §9: `GlobalMemoryStatusEx`, the DXGI budget from `QueryVideoMemoryInfo`, `CreateMemoryResourceNotification` and `TerminateJobObject`).
- Fixes for the Windows client bugs W1–W10 found on 2026-10-26 ([DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §23.3, defined in §23.4).

### What was already measured or built

- The owner's decisions:
  - no WSL2 (2026-10-07, [DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §8.3);
  - D1, D2, D3 and D8 (2026-10-21 and 2026-10-26, [DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §12): among them, nodes on non-NVIDIA hardware are a product goal (D8), and the carve-out goes to **96 GB**, leaving 31.6 GB of RAM to Windows. D4–D7 are open;
  - no interim llama.cpp engine (2026-10-26), and the Windows-only port (2026-10-27) (AMD study §5).
- The AMD test machine (ACEMAGIC Ryzen AI Max+ 395, 128 GB LPDDR5X-8000, 2 TB NVMe, Windows 11 Pro 25H2 build 26200.9711, AMD Adrenalin 26.10.1, driver `32.0.32015.7029`) was set up on 2026-10-26 ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §13.1, §13.2).
- A 60-line C client from AMD's ADLX sample (`IADLXVariableGraphicsMemory` in `amdadlx64.dll`, cross-compiled with `zig cc`) set the carve-out from about 8 GB as delivered to **96 GB** over SSH. After one reboot it read back as current, with no BIOS visit ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §13.1, §13.2).
- The Windows client was tested on the machine against a GB10 node: v1.7.0 ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §13.3) and an unpublished 1.7.1 candidate ([DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §23).
  - Passed: mDNS node discovery, `ling exec` in the unelevated sandbox, `ling audit egress` (ETW) and the desktop NSIS installer.
  - The elevated and unelevated sandboxes passed in a console session, `/airgapped on` included, but failed over SSH (bug W2, a session-0 issue).
  - Two `install.ps1` bugs found with v1.7.0 (the `PATH` array, and `USERDOMAIN=WORKGROUP` breaking `ling sandbox setup` over SSH) were fixed in 1.7.1.
- Baseline inference measurements on the machine with `llama.cpp` `b11907` (Vulkan and ROCm 10.0 with TheRock `10.0.0` for `gfx1151`) on `Qwen3.8-27B-UD-Q4_K_XL.gguf` (16.34 GiB, peak 19.1–19.9 GB VRAM, [DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §13.4):
  - Plain decode: **11.8–12.3 tok/s** at empty context, **10.5–10.9 tok/s** at 32K.
  - MTP speculative decode (4 draft tokens): **14.1–14.6 tok/s** on prose and **23.7–28.6 tok/s** on code.
  - Prefill: **301 tok/s** empty / **226 tok/s** at 32K on ROCm (**264 / 169 tok/s** on Vulkan); the GB10 on SGLang prefills at about 1,700 tok/s, 7.5 times this box's best at 32K. One Work turn at 32K costs about 24 s on ROCm and 27 s on Vulkan.
- The first milestone of the `ling-engine` port **passed at the box's 140 W setting** on 2026-10-27 (AMD study §6.5–§6.7; commit `0b362a3`: NVFP4 at 16 rows 229–230 GB/s steady over 90 s, WMMA 52.5 TFLOPS FP16 and 105 TOPS INT4). No engine code is ported yet; the probes stay in ling-engine's `bench/amd/`.

### Open questions

- What prefill and DFlash2 decode the port reaches at 32K–64K context against the llama.cpp baseline (226 tok/s prefill, 10.5–10.9 tok/s decode at 32K). The AMD study estimates 55–65 tok/s on agent output and about 6–7 s for a 32K agent turn.
- Whether dense `Qwen3.8-27B` with DFlash2 on `ling-engine` is enough for Work on Strix Halo, or whether `Qwen3.8-Flash-Next` (about 125B in all, about 6B active) should be the AMD model ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §12, D7).
- How software checks the power setting. The 140 W setting is the box's front knob, and Windows cannot read the firmware's mode ([DREAMFERENCE_MIGHTLING_AMD_NODES.md](./DREAMFERENCE_MIGHTLING_AMD_NODES.md) §13.2; AMD study §5).
- Whether WDDM / ROCm on Windows cleanly reclaims the 96 GB VGM carve-out after `TerminateJobObject` kills a runaway engine process, or whether a driver reset is required.
- Windows code signing ([DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md) §16.3) so Windows 11 Smart App Control does not block unsigned binaries.

---

## 4. Discord bridge (DM only in Phase 1)

### What it is

A third adapter in `ling chat` (`ling-rs/chat/src/discord.rs` and `discord_gateway.rs`, managed with `ling chat discord setup|pair|users|remove|off`) that lets the owner talk to `ling` from a Discord direct message. It connects outbound-only over Discord's WebSocket Gateway (`GUILDS | DIRECT_MESSAGES`, no privileged `MESSAGE_CONTENT` intent, no Interactions Endpoint URL, no inbound port), accepts messages only in a DM with a paired Discord user id (`contexts: [BOT_DM]`), posts whole answers by default behind a typing indicator and a status line with a Stop button (because every Discord message edit is stored as a visible revision), suppresses embeds and mentions on every send, and uses a Link button (`style: 5`) to `ling web` over the user's overlay (`[chat] discord_web_url`) instead of a Discord Activity (whose traffic goes through Discord's Cloudflare Workers proxy).

That the bridge waits for 2.0 is the owner's decision, recorded here. The Discord spec itself orders it only after Telegram's Phase 0 and the shared Phase 2 (D9).

### Spec(s)

- [DREAMFERENCE_MIGHTLING_DISCORD.md](./DREAMFERENCE_MIGHTLING_DISCORD.md) (proposed 2026-10-21, owner decisions D1–D9 approved 2026-10-22)
- [DREAMFERENCE_MIGHTLING_CHAT.md](./DREAMFERENCE_MIGHTLING_CHAT.md) (the `ling chat` hub and `ling web` client architecture)
- [DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md) (shared Phase 2 features: voice transcription, attachments, Night Shift, bounds and phone prompt)
- [DREAMFERENCE_MIGHTLING_CHAT_REPOS.md](./DREAMFERENCE_MIGHTLING_CHAT_REPOS.md) (§5.3, §10 G11: repository work from Discord only through the `ling web` link button, with zero-code notice templates in Discord chat)

### Dependencies

- Completion of Telegram Phase 0 live verification ([DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md) §14.1, per owner decision D9 in [DREAMFERENCE_MIGHTLING_DISCORD.md](./DREAMFERENCE_MIGHTLING_DISCORD.md) §0.1).
- Discord Phase 0 live verification on a throwaway Discord application and account ([DREAMFERENCE_MIGHTLING_DISCORD.md](./DREAMFERENCE_MIGHTLING_DISCORD.md) §10).
- An HTTPS front for `ling web` over Tailscale or the remote-access overlay (`[chat] discord_web_url`) when using the "Open in Mightling" link button or `/repo` work ([DREAMFERENCE_MIGHTLING_CHAT_REPOS.md](./DREAMFERENCE_MIGHTLING_CHAT_REPOS.md) §5.3).

### What was already measured or built

- Full specification written (2026-10-21) and all nine owner decisions D1–D9 approved (2026-10-22): whole answers by default (`[chat] discord_stream = false`), DM-only in Phase 1 (a one-person private server with a `GUILD_FORUM` channel deferred to Phase 3), no automatic deletion of old replies, and building after Telegram Phase 0 and the shared Phase 2.
- Discord's Bot API, Gateway rules (1,000 `IDENTIFY` calls per 24 hours before Discord resets the bot token), message flags (`SUPPRESS_EMBEDS = 1 << 2`), component V2 rules (`IS_COMPONENTS_V2 = 1 << 15`), and Activity proxy rules (`<application_id>.discordsays.com`) were verified against Discord's documentation on 2026-10-21.
- The shared `ling-chat` infrastructure and `ling web` routes that the Discord adapter uses are already built and merged (`1.7.1`): `ling-rs/chat/src/blocks.rs` (structured Markdown block parser), `limits.rs` (turn bounds), `attachments.rs` (image upload, offline Tesseract OCR, vision check), nonce-keyed approvals (`a:<nonce>:<choice>`), `/api/transcribe` voice transcription, `/api/ask/files` files-back approval, `/api/night/*` Night Shift queueing and morning reports, the `chat` prompt with `approval_policy = "never"`, and `tokio-tungstenite` in the workspace.
- Neither `ling-rs/chat/src/discord.rs` nor `discord_gateway.rs` has been written, and Phase 0 has not been run against Discord.

### Open questions

- Whether a user-installed application (`integration_types: [USER_INSTALL]`, `contexts: [BOT_DM]`) delivers plain DM `MESSAGE_CREATE` events over the Gateway with `DIRECT_MESSAGES` when the user and bot share no server, or whether setup must ask the owner to create a private one-person server (Phase 0 item 1, [DREAMFERENCE_MIGHTLING_DISCORD.md](./DREAMFERENCE_MIGHTLING_DISCORD.md) §10).
- Which exact `resume_gateway_url` and attachment CDN hostnames Discord uses in practice for `EgressAudit`'s `--chat` allow-list (the spec names `gateway.discord.gg`, `cdn.discordapp.com` and `media.discordapp.net`).
- Whether Discord's servers fetch `<url>` links to build previews when `SUPPRESS_EMBEDS` (`1 << 2`) is set on the message.
- Whether the Link button (`style: 5`) to `discord_web_url` opens in the system browser (which holds the paired device cookie and overlay certificate trust) or an in-app webview on iOS and Android.
- Whether Phase 3 (forum-channel threads in a one-person private server, D2) and moving the bot token to a system account, as Signal's keys are under `mightling-signal` (D6), are wanted after Phase 1.

---

## 5. Web search: paid search API providers, reselling them, SearXNG `google cse`, and the local search index

### What it is

Making web search reliable when public search engines rate-limit or CAPTCHA the node's egress address, across four tracks the owner decided to wait for 2.0 (recorded here; only the local search draft's own commit, `2a419ec3`, says "postponed to 2.0"): supporting optional paid search API providers configured with a user's own API key (Brave, Parallel, Perplexity and others), deciding whether Dreamference resells search API access so users do not need separate provider accounts, deciding whether SearXNG's `google cse` engine stays enabled in `SearxngSettings`, and building a local search index (`specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md`, a pending draft on branch `agy/local-search-spec`, not on `main`).

### Spec(s)

- **Paid search API providers and reselling:** no spec yet on `main`.
- **Local search index:** `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` (pending draft on branch `agy/local-search-spec`, not on `main`).
- **Existing search and SearXNG specs on `main`:** [DREAMFERENCE_MIGHTLING_AIRGAPPED.md](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md) (§1, §14.6, §14.10), [DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md) (§4.16, §4.22), [DREAMFERENCE_MIGHTLING_ASK.md](./DREAMFERENCE_MIGHTLING_ASK.md) (§1), [DREAMFERENCE_RELEASE_1.7.1.md](./DREAMFERENCE_RELEASE_1.7.1.md) (the 2026-10-20 SearXNG engine list overhaul), [DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md](./DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md) (§1, §2, §5.3–§5.4: search failure and CAPTCHA scenarios S6/S7).

### Dependencies

- A specification on `main` for paid search API providers covering provider selection, user key storage in the user's configuration directory (never inside a repository), air-gap enforcement (`/airgapped on` must block paid API calls exactly as it blocks SearXNG), and `ling-admin audit egress` rules.
- Reviewing and merging the pending draft `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` from branch `agy/local-search-spec`.
- Disk and memory budgets alongside a resident model server: the primary node's disk floor is 150–175 GB ([DREAMFERENCE_MIGHTLING_FORGEJO.md](./DREAMFERENCE_MIGHTLING_FORGEJO.md) §5.2), and any local search index or sidecar must obey `check_host_safety()` and the host-wide indexing memory budget.

### What was already measured or built

- `ling-search` (with `--read`, `--category`, `--time`, `--lang`, `--json`) and `ling-fetch` (`ling-web-rs/`), plus MCP `WebTools` (`dreamference/mcp_server/web_tools.py`), query the local `dreamference-searxng` container on the `dreamference-sidecars` Docker network (`dreamference/chat/searxng_sidecar.py`).
- On 2026-10-20, all four default general web engines in SearXNG refused the node's egress address at the same time (`dreamference/chat/searxng_settings.py`, [DREAMFERENCE_MIGHTLING_AIRGAPPED.md](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md) §14.10): Brave and Google (`google cse`) returned HTTP 429 (`Too Many Requests`), and DuckDuckGo and Startpage returned CAPTCHAs. Container logs showed Brave, DuckDuckGo and Startpage refusing the first query of every day since 2026-10-14, and `google cse`—the last general engine still answering—joined them on the morning of 2026-10-20.
- Every engine in SearXNG's catalogue was tested on 2026-10-20 and `SearxngSettings` (version 3, shipped in `1.7.1`) was updated:
  - Removed 18 engines operated by Russian or Chinese companies (`REMOVED_ENGINES`).
  - Disabled DuckDuckGo's HTML endpoint (`duckduckgo`, which suspended for 0 s on CAPTCHA and poisoned every query) in favour of `duckduckgo web`, and disabled Bing general search (which returned unrelated speed-test results for technical queries).
  - Raised SearXNG's suspension times so rate-limited engines back off: `search.suspended_times.SearxEngineAccessDenied` to 900 s (15 min) and `cf_SearxEngineCaptcha` / `recaptcha_SearxEngineCaptcha` to 86,400 s (24 h).
  - Enabled all working no-key engines in their categories (`GENERAL_ENGINES`: `brave`, `google cse`, `startpage`, `duckduckgo web`, `mwmbl`, `yep`, `seznam`, `gmx`, `searchch`, `wikipedia`, `wikidata`, plus `news`, `science`, `it`, `map`, `images`, `videos`, `music`, `files`, `social`, `books`, `movies`, `dictionaries`, `wikimedia`, `weather`).
- `UNAVAILABLE_HINT` in `ling-web-rs/src/search.rs` and `SEARCH_UNAVAILABLE_HINT` in `dreamference/mcp_server/web_tools.py` were updated so `ling` treats all-engines-refused as upstream rate limiting rather than a broken container and does not try to restart Docker; `ask-evals` scenarios S6 and S7 ([DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md](./DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md) §5.3–§5.4) test this path.
- `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` is drafted on branch `agy/local-search-spec`. Nothing for paid search API keys or reselling has been built.

### Open questions

- Which paid search API providers (Brave Search API, Parallel, Perplexity, Tavily, Exa, Kagi, Google Programmable Search JSON API) to support first, and whether `ling-search` calls them directly when a key is configured or configures them as SearXNG engines / fallback tiers when free engines return 429/CAPTCHA.
- Whether Dreamference resells search API access (and if so, how prepaid credits or a relay key work without introducing a mandatory Dreamference user account or logging queries), or whether users bring their own provider API keys only.
- Whether SearXNG's `google cse` stays enabled in `GENERAL_ENGINES` (it was the last major general engine answering before 2026-10-20, but sends queries to Google's Custom Search endpoint and hits 429 under regular use).
- How large the local search index in `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` is on disk, which corpus slices (offline documentation, Wikipedia, package docs, crawled pages) it ships or builds, and how it updates without crowding the primary node's disk reserve.

---

## 6. Matrix on the remote-access overlay

### What it is

Running the node's private `tuwunel` Matrix homeserver (`dreamference-matrix`, on an internal Docker network with no route to the internet and a loopback proxy on port 6167) over Mightling's self-hosted NetBird remote-access overlay (`mightling-<node id>.netbird.selfhosted`) instead of requiring Tailscale (`tailscale serve`). When remote access is set up on the node, the homeserver's Matrix server name is the node's overlay name, TLS is terminated on the node using a certificate signed by the node's name-constrained private CA (which already permits `netbird.selfhosted`), and NetBird becomes the default overlay for a new homeserver while Tailscale remains an option for users who already use it.

That it waits for 2.0 is the owner's decision, recorded here. REMOTE_ACCESS §14.5 calls it the overlay's last phase (the owner's decision of 2026-10-20).

### Spec(s)

- [DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) (§0 item 7, §4.2, §8, §13.2, §14.5)
- [DREAMFERENCE_MIGHTLING_CHAT.md](./DREAMFERENCE_MIGHTLING_CHAT.md) (§2, §5: `ling-admin matrix` and `ling chat` Matrix adapter)
- Developer notes: `docs/dev/remote-access.md` and `docs/dev/messengers.md`

### Dependencies

- Live phone and laptop client verification on the remote-access overlay ([DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) §9, §13.3), specifically confirming that the NetBird mobile app and Element X on iOS and Android accept TLS certificates issued by the node's user-installed, name-constrained private CA (`netbird.selfhosted`).
- The `ling web` Settings › Devices QR code enrolment page ([DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) §4.2) so a phone on the LAN can install the node's CA certificate and join the overlay without typing CLI commands.

### What was already measured or built

- The Matrix homeserver lifecycle `ling-admin matrix {start,stop,status,add-user,push,remove}` (`dreamference/chat/matrix_homeserver.py`, `tuwunel` v1.9.3 pinned by digest, `systemd-socket-proxyd` user units on loopback port 6167, no registration or federation) and the `ling chat` Matrix adapter (`ling-rs/chat/src/matrix.rs`) are built and merged, tested offline against stand-ins (CHAT's verification of 2026-10-08: 5 Matrix adapter tests in Rust and 8 Python tests for `ling-admin matrix`). Today `MatrixHomeserver.start()` requires Tailscale and runs `tailscale serve --bg --https=443 http://127.0.0.1:6167` in front of the loopback proxy.
- The self-hosted NetBird remote-access overlay (`dreamference/remote/`, `ling-admin remote {setup,status,remove,code,peers,revoke}`, `ling node remote join`, `netbirdio/netbird-server:0.80.0` on internal Docker network `dreamference-remote`, SSH reverse tunnel `mightling-remote-tunnel.service` to the relay box with haproxy SNI pass-through on port 443, and the EC P-256 CA constrained by `nameConstraints` to the configured CA scope and `netbird.selfhosted`) is built and merged to `main` (2026-10-20). Phase 0 ran live on the relay box (2026-10-12) and on the node (2026-10-13) (REMOTE_ACCESS §12.3, §12.4). The node's migration on 2026-10-27 found and fixed a first-start fault (`docs/dev/remote-access.md`). The live acceptance with a client (§9, §13.3) has not run.
- Not built yet: TLS termination in front of port 6167 using a leaf certificate from the node's CA for `mightling-<node id>.netbird.selfhosted`, the overlay selector in `ling-admin matrix start` (NetBird default when remote access is configured, Tailscale optional), and live Element X testing over the NetBird overlay.

### Open questions

- Whether Android's `x509.SystemCertPool()` in Go (used by the NetBird Android client) and Element X's Rust `matrix-sdk` TLS stack on Android and iOS trust the user-installed node CA certificate without requiring a system-store root or custom build ([DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md](./DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md) §4.2, §8, §11 item 3).
- Which lightweight TLS terminator on the node (a small Rust TLS proxy inside `ling-admin` / `ling web` or `caddy` / `haproxy` on the overlay interface) fronts loopback port 6167 under `mightling-<node id>.netbird.selfhosted` when Tailscale is not used, and on which port.
- Whether `ling-admin matrix` needs any helper for switching an existing Tailscale homeserver to the NetBird overlay beyond `ling-admin matrix remove` and `ling-admin matrix start` (Matrix embeds the server name in every user and room id, so changing the server name always creates a new homeserver, §14.5).

---

## 7. Other on-hold and later-phase items across `specs/`

No other document in Mightling's `specs/` is marked `"2.0"` or `"postponed"`. The items below are explicitly marked **on hold**, **switched off**, or **later phases** in their specifications and are tracked here so nothing is lost.

### 7.1 Advisor node on a second GB10

- **What it is:** Using a second paired GB10 node to serve `nvidia/Qwen3.8-Flash-Next-NVFP4` (a 125B hybrid MoE, 6B active, plus a 51B n-gram table, 47.7 GiB on disk, memory-mapped from NVMe; thinking on) as an advisor that reviews `ling`'s work at the end of a turn through a rule-gated `Stop` hook and `ling advise`, failing open if the advisor node is busy or unreachable.
- **Spec(s):** [DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md](./DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md) (*On hold (2026-10-03) until a suitable advisor model is found*).
- **Dependencies:** A model checkpoint that fits a single GB10 alongside host-safety reserves and demonstrates a positive resolved-rate gain over the 27B worker alone in a 24-task replay and A/B test; `Qwen3.8-Flash-Next` engine support ([DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md) §2.3).
- **What was already measured or built:** Nothing built. The memory budget against `check_host_safety()` (showing why no DeepSeek model and no 397B Qwen3.8 checkpoint fits, and why Flash-Next fits only with its n-gram table memory-mapped, about 73–75 GiB resident) and the hook design are specified in [DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md](./DREAMFERENCE_MIGHTLING_ADVISOR_NODE.md) §2–§6.
- **Open questions:** Whether `Qwen3.8-Flash-Next-NVFP4` (or a stronger reasoning checkpoint) beats best-of-N with the 27B model on the same second node ([DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md) §11.5, §11.9).

### 7.2 Diffusion sidecar and `ling fast`

- **What it is:** Running a capable block-diffusion model (`DiffusionGemma 26B A4B NVFP4`) in the diffusion slot as `ling fast` (`scaffold`, `tests`, `docs`, `write`, `digest`; FAST_TOOLS §2) for long, routine code, tests and prose where parallel block decoding is faster than autoregressive decode.
- **Spec(s):** [DREAMFERENCE_MIGHTLING_FAST_TOOLS.md](./DREAMFERENCE_MIGHTLING_FAST_TOOLS.md), [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) (§1), [DREAMFERENCE_MIGHTLING_COMPACTION.md](./DREAMFERENCE_MIGHTLING_COMPACTION.md) (§1, §4.5, §9.3), and `docs/dev/diffusion.md`.
- **Dependencies:** Availability of a 26B-class MoE block-diffusion checkpoint (`DiffusionGemma 26B A4B NVFP4`) that passes the identifier-survival test (≥95% of paths, symbols and numbers preserved) and fits in memory or swaps cleanly beside the main model server.
- **What was already measured or built:** `DiffusionServerManager` (`dreamference/vllm_server/diffusion_server_manager.py`) and the block-diffusion sampler for the 0.5B `tiny-a2d` sidecar are built. As a tool-output compressor, `tiny-a2d` (0.5B) kept only 14 of 50 identifiers of a 3,000-character `git log --stat`, with one path misspelt (COMPACTION §1). The diffusion slot is switched off since 2026-10-03 (`DIFFUSION_ENABLED = False` in `dreamference/hardware/model_matrix_registry.py`); the code is kept.
- **Open questions:** Whether any diffusion model small enough to coexist with the ~60 GB main server beats autoregressive decode with DFlash2 on these tasks (`ling-serve`: 74.9 tok/s on a 600-token code answer, §1).

### 7.3 Cross-device sync Phases 3–4 and Forgejo remote reach

- **What it is:** Completing the remaining phases of `ling sync`—explicit thread hand-over (`ling sync handover <thread>`, with `--with-code` for an optional git bundle of unpushed work), an installable PWA over the remote-access overlay with opt-in Web Push notifications, and background rollout replication (`[sync] replicate`)—plus exposing the node's Forgejo forge (`/forge/`) over the remote-access overlay.
- **Spec(s):** [DREAMFERENCE_MIGHTLING_SYNC.md](./DREAMFERENCE_MIGHTLING_SYNC.md) (§7, §9, §10, §12 Phases 3–4), [DREAMFERENCE_MIGHTLING_FORGEJO.md](./DREAMFERENCE_MIGHTLING_FORGEJO.md) (§9: reaching the forge outside the LAN is a later decision with remote access, O5), [DREAMFERENCE_MIGHTLING_CHAT_REPOS.md](./DREAMFERENCE_MIGHTLING_CHAT_REPOS.md).
- **Dependencies:** Live phone verification of the remote-access overlay (§6) and the node build of Forgejo ([DREAMFERENCE_MIGHTLING_FORGEJO.md](./DREAMFERENCE_MIGHTLING_FORGEJO.md) §8) and `/repo` ([DREAMFERENCE_MIGHTLING_CHAT_REPOS.md](./DREAMFERENCE_MIGHTLING_CHAT_REPOS.md) §13).
- **What was already measured or built:** `ling sync` Phases 0 and 1 (built 2026-10-22) and Phase 2 (built 2026-10-26, for `1.8.0`) are merged ([DREAMFERENCE_MIGHTLING_SYNC.md](./DREAMFERENCE_MIGHTLING_SYNC.md) §16–§18): append-only rollout sync by byte offset, history across paired devices, and live turn following and answering from any paired device (off until `ling sync enable`). Forgejo and `/repo` specs were decided by the owner on 2026-10-27.
- **Open questions:** Whether the git bundle of `ling sync handover --with-code` is still needed once every active repository is hosted on the node's own Forgejo instance ([DREAMFERENCE_MIGHTLING_FORGEJO.md](./DREAMFERENCE_MIGHTLING_FORGEJO.md)).

### 7.4 Local document index: the vision OCR queue, and Drive PDFs

- **What it is:** Queueing pages that OCR read with low confidence (below `docs_ocr_send_below`, 0.92) for the local model's vision in Night Shift's idle hours (part of LOCAL_INDEX's Phase 2, not built), and wiring Google Drive's PDF download (`files.get?alt=media`) into `drive_read` and `pdf_search` on `drive:<id>`.
- **Spec(s):** [DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md](./DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) (§13, §20.7), [DREAMFERENCE_PDF_SEARCH.md](./DREAMFERENCE_PDF_SEARCH.md) (§3.3.2, §5.2, §7 Phases 2–3).
- **Dependencies:** Vision support in the default model server (§1, when `ling-serve` is default) and the Drive service's binary PDF download endpoint.
- **What was already measured or built:** `ling-docs` Phase 1 (text, Markdown, PDF via PDFium) shipped in `1.6.0`. Phase 2 (DOCX, ODT, HTML, `.eml`/`.mbox` with attachments, CSV), Phase 3 (spreadsheets, presentations) and RapidOCR on the CPU for scanned PDFs and images were built on 2026-10-20 and are in `1.7.1`; `pdf_search` (Surface D for local files and URLs) and `ling-docs extract` are built and merged (`2026-10-13`).
- **Open questions:** Whether RapidOCR's CPU pass leaves enough low-confidence pages on real collections to justify running vision OCR at night once `ling-serve` supports `input_image`.

---

## 8. Proposed order and reasons

The postponed work falls into an inference spine (items 1 → 2 → 3) and two independent tracks (item 5 on web search; items 6 and 4 on overlays and phone bridges) that do not contend for the GB10 GPU or `ling-engine` kernel work.

1. **Step 1 (`1.8.0`, then the `2.0` foundation): `ling-engine` (`ling-serve`) as the default model server (§1).**
   - **Why first:** Every other inference and model item in 2.0 targets `ling-serve`, not SGLang. Moving from 2 pre-allocated sessions to a shared KV/state pool for 4+ concurrent conversations, adding prefix KV reuse, and a benchmark night with no loss against SGLang cut `1.8.0` (with the owner's OK); images and adapters complete the gate of 2026-10-26. That gives 2.0 one engine. Building multi-drafter or LoRA serving twice (once in SGLang and once in `ling-serve`) would waste effort right before SGLang stops being the default.
2. **Step 2 (in parallel with Step 1, no GPU contention): Web search (§5).**
   - **Why second / parallel:** Public search engines already rate-limit the node's IP (`2026-10-20` outage across Brave, `google cse`, DuckDuckGo and Startpage), so search reliability affects daily Ask and Work sessions now. Specifying and adding optional user API keys (Brave, Parallel, Perplexity), deciding on `google cse` and reselling, and merging `specs/DREAMFERENCE_MIGHTLING_LOCAL_SEARCH.md` from `agy/local-search-spec` live in `ling-web-rs/` and `dreamference/chat/` and do not wait on `ling-engine`.
3. **Step 3 (in parallel with Step 1, phone/network track): Matrix on the remote-access overlay (§6), then the Discord bridge (§4).**
   - **Why this order within the track:** Both depend on live phone verification of the HTTPS front (`ling web` and the node's private CA over the NetBird overlay). Finishing live phone acceptance on the NetBird overlay and wiring `ling-admin matrix start` to use `mightling-<node id>.netbird.selfhosted` (§6) proves the overlay TLS path on real phones first. Immediately after Telegram's Phase 0 check completes, Discord Phase 0 and Phase 1 (`discord.rs`, `discord_gateway.rs`, DM-only, §4) reuse the same `ling-chat` hub and the same overlay URL for `[chat] discord_web_url`.
4. **Step 4 (after Step 1): Several drafters and own-session tuning (§2) + LoRA in `ling-engine` (§1).**
   - **Why after Step 1:** Once `ling-serve` is the default server with a shared KV pool, MULTI_DRAFTER's per-session drafters, the sparse head (`dreamference/qwen3.8-27b-sparse-head`) and unmerged LoRA + drafter-LoRA support can be switched on and measured in one engine. `ling-admin drafter` (`stats`, `collect`, `generate`, `tune` with SpecForge, `evaluate`, `promote`) then tunes drafters for it. A larger drafter is not on the list (BIG_DRAFTER: no-go).
5. **Step 5 (after Step 1's shared pool and kernel interfaces settle): The Windows port of `ling-engine` for Strix Halo (§3).**
   - **Why fifth:** The first milestone at 140 W and the 96 GB carve-out on the test machine are proven. Running the AMD study's Windows-only plan (§5: W0 toolchain and bring-up, W1 decode, W2 prefill and serving) after `ling-engine`'s shared KV pool and scheduler settle on the GB10 avoids porting a moving engine layout twice.
6. **Step 6 (when prerequisites arrive): On-hold and later-phase items (§7).**
   - **Why last:** Cross-device sync Phases 3–4 and Forgejo remote reach (§7.3) follow once Forgejo and `/repo` Phase 1 and the overlay (§6) are in daily use; the vision OCR queue and Drive PDFs (§7.4) follow once `ling-serve` serves vision; the advisor node (§7.1) and diffusion `ling fast` (§7.2) stay on hold until checkpoints exist that beat the 27B worker + DFlash2 on this hardware.
