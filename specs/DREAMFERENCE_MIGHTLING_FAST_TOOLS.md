# Fast Tools — a diffusion model for Mightling's mundane work

**Status:** proposed. Nothing in this spec is implemented yet. Since 2026-10-03 the diffusion slot it builds on is switched off (`DIFFUSION_ENABLED = False` in `hardware/model_matrix_registry.py`: no sidecar is started, downloaded or shown, the code is kept), so building this spec starts by switching it back on with a capable model in it.
**Goal:** let `ling` hand long, low-judgement outputs (file scaffolds, tests, docstrings, docs, commit and PR text, summaries of long tool output) to a fast diffusion model, so the main model spends its time on the decisions.
**Builds on:**
- the diffusion slot beside the main model (`DiffusionServerManager`, `diffusion-model set`, port 8001; [INFERENCE](./DREAMFERENCE_INFERENCE.md), AGENTS.md "Every configuration names a diffusion model");
- the launcher in `ling-rs/`, which already handles `ling app` and `ling update` before Codex parses argv, and would handle `ling fast` the same way ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md) chose the other route, a separate `ling-code` binary, because its router is large and changes often; §4.2 there gives the trade-off);
- the prompt block the launcher appends to the model catalog (`WEB_ACCESS_INSTRUCTIONS`), which is how the local model already learns `ling-search`;
- admission control from [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md) and host safety (`check_host_safety`).

**Needs no Codex patch.** The patch budget has 567 bytes left (26,933 of 27,500 on 2026-10-01, after `0018`), so everything here lives in the launcher, the registry and `ling-admin`.

---

## 1. The finding that shapes the design

Measured on this GB10 on 2026-09-30, before writing anything:

| What | Result |
|---|---|
| The current diffusion sidecar (Tiny-A2D, Qwen2.5-Coder 0.5B, bd3lm) on five mundane tasks | **Fast and unusable.** 136–173 tok/s, but: the commit message echoed the diff, docstrings were invented words and duplicated, unit tests did not parse, the lazy edit lost the file, the log "summary" copied the log. Its published HumanEval is 39.0 |
| Qwen3.8-27B (the main model) applying a lazy edit to a 118-line file | **126 tok/s**, byte-exact outside the edit, 1,177 tokens in 9.9 s. The DFlash2 drafter accepted 8.8–14.7 of 16 drafted tokens: copying is what a block-diffusion drafter is best at |
| Qwen3.8-27B on fresh output (greedy, single stream) | prose 25.5, code 50.3, JSON 87.0 tok/s |
| Qwen3.8-27B on the §2 task types themselves (same prompts a `ling fast` verb would take) | README-style docs 39.3, PR description 39.3, new module from a spec 52.4, unit-test scaffold 69.1, docstrings across a file 69.9 tok/s: formulaic text drafts better than the 25.5 of generic prose |
| Where `ling`'s own output goes (190 rollouts in `~/.mightling/sessions` and `~/.codex/sessions`) | **42.5%** of all model-emitted characters are whole files written through shell heredocs (`cat > f <<EOF`; median 3.9K chars, p90 14.6K); **24.6%** assistant prose (median 173 chars, p90 1.4K); 11.8% ordinary shell commands; no `apply_patch` calls at all. **Caveat:** most of these rollouts predate Qwen3.8 (Qwen3.5-era and `~/.codex` sessions), and the split between heredocs that *rewrite* existing files (copying, already fast) and heredocs that *create* files (fresh text, the real target) is not yet measured |

Three conclusions follow, and they are the spec's spine:

1. **The main model is already diffusion-accelerated where output is copied.** Rewriting or patching an existing file runs at ~126 tok/s through DFlash2. A separate "fast apply" model (the Morph/Relace/Mercury pattern) buys little here, and is **not** proposed.
2. **The gap is fresh text, and it is smaller than the generic numbers suggest.** On the task types themselves the main model does 39 tok/s on docs and PR prose, 52 on a new module, 69–70 on test scaffolds and docstrings. A capable diffusion model does ~140–160 tok/s on a Spark at full canvas and ~100 in mixed-length use (§3). Expected gain: **~2.5–4× on prose, ~2–3× on new modules, ~1.5–2× on tests and docstrings.** Prose is where offloading clearly pays; the code verbs need Phase 0 (§7) to show they clear 2×.
3. **The sidecar we ship cannot do it.** Any design must start by putting a capable model into the diffusion slot.

---

## 2. Which work to offload

A diffusion model's cost is **per canvas** (a block of 256 tokens denoised together), not per token: on a Spark, DiffusionGemma did 158.7 tok/s filling a full canvas but 16.5 tok/s on a cold 84-token reply. So the rule is **offload long, fresh, checkable output; keep short or judgement-heavy output on the main model.**

| Task | Typical output | Main model today | Offload? | Why |
|---|---|---|---|---|
| New file scaffold (module, CLI, config, fixture) from a spec | 500–4,000 tokens, fresh | 52 tok/s (measured) | **yes, if Phase 0 shows ≥ 2×** | Long fresh code; checkable by parse + tests |
| Unit-test scaffolding for a given file | 300–2,000 tokens | 69 tok/s (measured) | **only in batches** | Single-file gain ~1.5–2×; the clear win is several files in parallel (§5) |
| Docstrings across a file | whole-file rewrite, mostly copied | 70 tok/s (measured), ~126 when mostly copying | **only for many files at once** | Single-file speed is already there; the win is batching 10 files in parallel (§5) |
| README / docs section / changelog / PR description | 300–1,500 tokens, prose | 39 tok/s (measured) | **yes** | Prose is the main model's slowest output: ~2.5–4× |
| Commit message | 20–80 tokens | ~25 tok/s | **no** | Below one canvas: slower on diffusion |
| Summary of a long tool output (test log, build log, big diff) | input 5–50K, output 200–500 | prefill ~1,700 tok/s, then prose | **yes, for context** | Keeps 20K tokens of log out of the main model's context and KV; the time saving is secondary |
| Apply a lazy edit / rewrite a file | copied | ~126 tok/s | **no** | Already fast (§1) |
| Anything needing the repo's design judgement, debugging, API choice | any | — | **no** | The main model's job |

---

## 3. The model: DiffusionGemma 26B A4B, NVFP4

| Candidate | Evidence | Verdict |
|---|---|---|
| **`nvidia/diffusiongemma-26B-A4B-it-NVFP4`** (Google, Apache-2.0) | 25.2B total / 3.8B active MoE, 256K context, native tool calling; LiveCodeBench v6 69.1. **On a DGX Spark, vLLM, NVFP4: 158.7 tok/s prose, 143.1 code single-stream, 257 tok/s aggregate at 4 streams**; 18 GB on disk. First diffusion LLM supported natively in vLLM (0.24+; an aarch64 `vllm/vllm-openai:gemma-aarch64-cu130` image exists) | **Proposed.** Strong enough for scaffolding and prose, fast on this exact hardware, and in an engine Mightling already runs |
| Stable-DiffCoder 8B (ByteDance, 2026) | Beats its AR base on HumanEval/MBPP and editing | Runner-up: no optimised serving path found; transformers-only decode would forfeit the speed |
| Dream-Coder 7B, DiffuCoder 7B | Instruct HumanEval ~72–83 | Same serving problem |
| Tiny-A2D 0.5B (today's sidecar) | Measured unusable (§1) | Keep only as the fallback so `server start` stays cheap on small boxes |

It goes into the **existing diffusion slot**: a registry entry `diffusiongemma-26b-a4b-nvfp4` with `is_diffusion=True` and a new `engine: vllm-diffusion` served by the `DiffusionServerManager`, which gains a vLLM launch path (the main-model `_docker_run_prefix` already provides the memory cap, CPU limit and OOM score). Recipe from the Spark measurement: `--diffusion-config '{"canvas_length": 256, "max_denoising_steps": 48}'`, `--hf-overrides '{"diffusion_sampler":"entropy_bound","diffusion_entropy_bound":0.1}'`, `--max-num-seqs 2` (the diffusion state buffers scale with sequences × canvas × 262K vocabulary), `--tool-call-parser gemma4`, pinned by digest, `VLLM_NO_USAGE_STATS=1`, loopback only.

---

## 4. How `ling` uses it

### 4.1 Phase 1: `ling fast` — one-shot shell tools (no agent loop)

The local model already calls `ling-search` because its prompt tells it to; `ling fast` works the same way, and needs only chat completions from the diffusion server (§7 explains why not the Responses API yet). Implemented in the launcher (`ling-rs/src/fast.rs`), dispatched before Codex parses argv, like `ling app`.

| Command | Does | Writes files? |
|---|---|---|
| `ling fast scaffold <path> --spec <text or @file> [--like <file>...]` | Generates a new file from a spec, in the style of the example files | writes `<path>` only if it does not exist |
| `ling fast tests <file> [--framework pytest] [--out <path>]` | Generates unit tests for a file's public functions | writes the test file, then **runs it** and prints pass/fail |
| `ling fast docs <file>... [--style google]` | Adds docstrings only; parallel across files. Type hints change the AST (annotations are nodes), so they are not this verb's job; a separate `hints` verb, gated by a type checker, is future work | rewrites each file **only if the result parses and its AST with docstrings stripped is identical to the original's** |
| `ling fast write <path> --spec ... ` | Prose: README section, changelog, PR description | writes `<path>` (or stdout with `-`) |
| `ling fast digest <file or -> [--focus <text>]` | Summarises a long log or diff into what matters | stdout only |

Every command prints a compact result for the main model: what it wrote, the check it ran and the outcome (for example `tests/test_slug.py: 14 tests, 12 pass, 2 fail: <names>`), and the git diff stat. It never prints the generated content back unless asked (`--show`), so the main model's context gets the verdict, not the tokens it offloaded.

The launcher appends a `FAST_TOOLS_INSTRUCTIONS` block (≤ 120 tokens, same mechanism as `WEB_ACCESS_INSTRUCTIONS`) only when the diffusion server answers at startup:
> For long routine output (new files from a clear spec, unit-test scaffolds, docstrings across files, documentation, summaries of long logs) run `ling fast …` instead of writing it yourself, then review its report. Do the design, debugging and decisions yourself.

### 4.2 Verification gates (why a weaker model is acceptable)

The fast model is weaker on code than an autoregressive model of its class (DiffusionGemma's LiveCodeBench v6 69.1 is eight points under its own autoregressive twin, Gemma 4, at 77.1), so nothing it produces is trusted by construction:
- **Syntax:** Python output must `ast.parse`; other languages go through the project's formatter or compiler when one is configured, else are marked "unchecked".
- **Semantics preserved:** `docs` compares the AST with docstrings stripped; any difference rejects the rewrite.
- **Tests run:** `tests` executes the generated tests; failures are reported, not hidden.
- **Scope:** writes only the named paths; refuses paths outside the workspace; refuses to overwrite in `scaffold`.
- **Size and repetition:** output longer than 4× the requested size, or with a repeated 64-token window (a known diffusion failure), is rejected.
- **Corruption:** a run of 32+ `!` (token 0) or empty canvases rejects the output: tonight's sampler bug on SGLang produced exactly that.

### 4.3 Phase 2: a fast subagent

Codex already lets the main model spawn subagents and name their model (`spawn_agent`'s `model`, `[agents.<role>] config_file`, `default_subagent_model`). A role's config layer can set `model` but **not** `model_provider` (`core/src/agent/role.rs`), so the fast model must be reachable through the main provider's URL. Phase 2 adds a small router (loopback, stdlib, beside the Gmail sidecar pattern) that dispatches /v1 requests by `model` field: the served main model to SGLang, `mightling-fast` to the diffusion server; the launcher points the provider at the router and adds `mightling-fast` to the model catalog, and a role `[agents.fast_worker]` whose layer sets `model = "mightling-fast"`. The subagent then runs a whole mundane task with tools (DiffusionGemma's tool calling works through vLLM's `gemma4` parser). Gated on §7's Responses-API check.

### 4.4 Not proposed

- **A fast-apply model** (Morph/Relace/Mercury Apply-Edit): §1 shows the main model already copies at ~126 tok/s.
- **Routing `/compact` or commit messages** to it: compaction is inside Codex (a patch, and the budget is 567 bytes); commit messages are below one canvas.
- **Using it as the main model's drafter:** that is DFlash2's role already.

---

## 5. Parallel work

DiffusionGemma measured 257 tok/s aggregate at 4 streams on a Spark. `ling fast docs a.py b.py …` and `tests` over several files send up to `--max-num-seqs` requests at once. The main model is idle while its tool call runs (the agent loop is sequential), so a single agent's offload does not compete with its own decode.

---

## 6. Memory and safety

- **Budget:** Qwen3.8 on SGLang leaves ~38.7 GB available while serving. DiffusionGemma NVFP4 is 18 GB of weights plus the diffusion buffers. At `--gpu-memory-utilization 0.20` (~24 GB) and `--max-num-seqs 2`, availability would drop to ~14 GB. That is above earlyoom's 5% line (~6 GB), but only just: **measure before adopting (§7).**
- **Start order:** unchanged. The diffusion sidecar starts **before** the main model, so the main model's pre-flight sees it resident.
- **Contention:** decode on GB10 is bandwidth-bound, so the two models slow each other when both decode. One agent never overlaps (sequential tool calls); Night Shift and fan-out must count the fast model's streams in admission control.
- **Falling back:** `diffusion-model set tiny-a2d-coder-0.5b-diffusion` restores today's footprint, and `ling fast` then refuses (the startup probe finds a model under the capability floor and omits the prompt block).
- **Egress:** loopback only, `HF_HUB_OFFLINE=1`, usage stats off, pinned digest.

---

## 7. Measure first (Phase 0), with pass criteria

Run with the machine otherwise idle, main model serving:

1. **Fit:** start DiffusionGemma NVFP4 beside Qwen3.8 at 0.20 / 2 seqs. Pass: `MemAvailable` ≥ 12 GB at idle and ≥ 8 GB during a 4-stream burst, no earlyoom action, the main model's decode within 5% of its solo numbers when the fast model is idle.
2. **Speed on our tasks:** the §2 tasks at their typical lengths. Pass: ≥ 2× the main model's wall time on scaffold, tests, docs-prose; report per-canvas behaviour for 100–300-token outputs.
3. **Quality through the gates:** 20 real files from this repo. Pass: scaffold/tests parse ≥ 95%, generated tests run with ≥ 80% passing on correct code, `docs` AST-preservation 100% of accepted rewrites.
4. **End to end:** a `ling exec` task that needs a new module plus its tests, with and without `ling fast`. Pass: faster wall clock at equal or better test outcome.
5. **Responses API:** does vLLM serve `/v1/responses` with tool calls for DiffusionGemma? Decides whether Phase 2 needs a translating router.
6. **The sampler:** confirm untruncated sampling on this engine does not emit token 0 (the SGLang defect found on 2026-09-29).

If (1) fails, the fallback is a smaller diffusion model or running the fast model only when the main model is stopped, which defeats the purpose; the spec would then stop at Phase 0.

---

## 8. Tests

- Launcher unit tests for each `ling fast` verb against a stub /v1 server: request shape, gate enforcement (syntax, AST preservation, scope, repetition, token-0 run), report format.
- The prompt block is added only when the fast model answers and meets the capability floor.
- `DiffusionServerManager` builds the vLLM diffusion command from the registry recipe (flags, digest, loopback, telemetry off, memory cap).
- conftest keeps these tests off the running stack (the docker guard of 2026-09-29).
- A live test (skipped without the fast model) runs `ling fast tests` on a fixture file and requires the generated tests to parse and run.

---

## 9. Risks

- **Quality:** a weaker model writing code. Mitigated by the gates and by limiting the verbs to checkable outputs; the main model reviews every report.
- **Memory:** two resident models on unified memory (§6); Phase 0 decides.
- **Prompt adherence:** the local model may not reach for `ling fast`, or may over-use it. Measure call rates in rollouts after a week; adjust the prompt block.
- **Engine maturity:** diffusion support in vLLM is new (0.24+); pin the image and re-measure on upgrade, as with the main engines.

---

## Sources

- DiffusionGemma on a DGX Spark, vLLM, NVFP4 (158.7 / 143.1 tok/s, per-canvas cost): https://ai-muninn.com/en/blog/dgx-spark-diffusiongemma-nvfp4-vllm
- DiffusionGemma model card: https://huggingface.co/google/diffusiongemma-26B-A4B-it ; vLLM recipe: https://recipes.vllm.ai/Google/diffusiongemma-26B-A4B-it
- Tiny-A2D (HumanEval 39.0 for the 0.5B bd3lm): https://huggingface.co/collections/dllm-collection/tiny-a2d
- Fast apply as a pattern: Morph https://www.morphllm.com/fast-apply-model ; Mercury Coder Apply-Edit https://www.inceptionlabs.ai/blog/ultra-fast-apply-edit-with-mercury-coder
- Diffusion models for developer workflows (infilling, refactors; limits): https://blog.jetbrains.com/ai/2025/11/why-diffusion-models-could-change-developer-workflows-in-2026/
- Stable-DiffCoder: https://arxiv.org/abs/2601.15892 ; Dream-Coder 7B: https://arxiv.org/pdf/2509.01142 ; DiffuCoder: https://arxiv.org/html/2506.20639v1
- Small-model offload in Claude Code (background summaries/titles): https://code.claude.com/docs/en/model-config
- Model routing for mundane subtasks: https://dev.to/shaam_ai/llm-model-routing-in-2026-the-guide-every-team-should-read-4a8c
