# Models, engines and launch flags

Developer notes behind the "Models and the model server" summary in `AGENTS.md`. Specs: `specs/DREAMFERENCE_INFERENCE.md`, `specs/DREAMFERENCE_MODELS.md`.

## Config precedence

`config/dreamference_config.py`: every field resolves through the same 4-step chain, in `DreamferenceConfig.__init__`: constructor kwarg → `DREAMFERENCE_*` env var → `dreamference.toml` (local, then `~/.config/dreamference/config.toml`) → module-level `DEFAULT_*` constant. `save_config()` deliberately writes only values that differ from the defaults, so a round-trip does not fossilize defaults into the TOML.

## A model may name its engine

`launch_overrides['engine']` names the engine: `vllm` (or nothing), `sglang` or `ling`. Each builder emits only what follows the image (`SGLangLaunchBuilder`, `LingServeLaunchBuilder`); the container comes from the same `docker run` prefix for all three (`_docker_run_prefix`), so the host-safety layer (`check_host_safety()`, the cgroup cap, the PSI watchdog) is engine-independent.

A model may also carry recipes for other engines in `ModelSpec.engine_recipes` (complete recipes, never layered over the default). `get_launch_overrides(model, engine)` returns the default recipe for `None` or the default's own engine and the named alternative otherwise; `ModelMatrixRegistry.engines(model)` lists them, default first. The choice reaches `server start` as `--engine`, or persistently as `server_engine` in the config (`DREAMFERENCE_SERVER_ENGINE`); `build_launch_command`, `start_server`, `AdapterServing.prepare` and `resolve_tool_call_parser` take an `engine` argument, and the drift check derives it from the running container's command.

## ling-serve is the default model's engine (1.7.2)

`qwen3.8-27b-nvfp4-dflash2` is served by ling-engine's `ling-serve` (`QWEN38_LING_RECIPE`, ling-engine `specs/DREAMFERENCE_LING_ENGINE_INTEGRATION.md` §12), with SGLang's recipe as `engine_recipes['sglang']` (the fallback, with the adapters' `lora` key). The owner's decision: 1.7.2 ships the engine at the commit SWE-bench nights 12 and 15 tested (79c38c7) or a later one whose changes the engine's checks show bit-exact.

- **The image** is `ghcr.io/dreamference/ling-serve`, pinned by digest in `QWEN38_LING_IMAGE`: `docker/ling-serve/Dockerfile` on `nvidia/cuda:13.0.2-base-ubuntu24.04` (pinned by digest; its libcudart 13.0.96 is the toolkit the binary is built with), the Release binary, glog from the engine's dependency build, Ubuntu 24.04's runtime libraries and `python3` for the model gate, which runs its stdlib-only proxy in the engine's image. `scripts/build_ling_serve_image.sh COMMIT [BINARY]` builds it on the engine's development machine (aarch64; no GPU): the binary with night 15's recipe (cmake Release with Ninja, `ninja ling-serve`), then the image, then checks that every library resolves and that the binary prints its usage in the container. 682 MB, against SGLang's 33.7 GB.
- **`engine_commit`** in the recipe (`QWEN38_LING_ENGINE_COMMIT`) is the full ling-engine commit the image's binary was built from, also in the image labels `org.opencontainers.image.revision` and `ai.dreamference.ling-engine.commit`. ling-serve does not report it itself (`/v1/models` says `owned_by: ling-engine`, `/health` says `ok`); `server status` reads the label. Anything that depends on the engine's version asks this field with an ancestry check (`git merge-base --is-ancestor`), never by comparing strings.
- **The launch** is the nights' (`scripts/engine_ab_remote.sh`): `ling-serve --model <snapshot> --served-model-name RadixArk/Qwen3.8-27B-NVFP4 --host <bind> --port <port> --max-context 65536 --draft <drafter snapshot> --draft-block 12 --graphs --pdl`, with the engine's defaults for the rest (tree verification, the megakernel stages, two sessions, a batch of two). The served id is the repository id, so `model_key_for_served_id` resolves it as for SGLang.
- **What ling-serve has no flag for**, and the integration spec assumed: a chat-template file (the patched template is compiled in, and the engine's `bench/template_test.py` checks it against `ChatTemplatePatcher`'s output; nothing is passed), a memory budget (its memory is what `--max-context` and its sessions allocate, about 57 GB; the container's cgroup cap is sized as SGLang's, so the envelope does not change), `--sleep-on-idle` (it idles without spinning) and an API key (`--api-key` is refused with ling-serve, naming the fallback). An unknown flag exits ling-serve with status 2, so the builder emits nothing else (`test_every_flag_the_builder_emits_is_one_ling_serve_parses`).
- **What it does not serve:** images (an image part becomes the text `[image]`; it answers no `/model_info`, so the launcher leaves Codex's modalities alone and the phone bridges send text), LoRA adapters (the recipe has no `lora` key, so `AdapterServing.prepare` serves none), structured output, and contexts beyond 65,536 tokens. The SGLang fallback serves all of them.
- **The drafter** is the registry's RTN-calibrated DFlash2 drafter, the one SGLang serves. The nights ran both arms with a ScaleSearch re-quantization of it (a local file with no download path); speculation is exact, so the drafter changes acceptance and speed, never the output (engine BACKLOG §19: tree verification x1.094 with ScaleSearch, x1.108 with RTN).
- **`server start`'s checks** are the same: the NVFP4 canary through `/v1/completions` and the tool call through `/v1/responses`. When ling-serve fails one of them, or stops before it answers, `server start` says so and prints the fallback (`ling-admin server start --engine sglang`, or `server_engine = "sglang"`); it never switches by itself.
- **`start_server` skips SGLang's preparations** for it: no chat-template copy, no runtime patches, no compile-cache reset (that one runs for vLLM only now).

Five SGLang traps, all handled:

- Pinned checkpoints go in as **snapshot directories**, because SGLang drops `--revision` on some offline lookups and a download by commit writes no `refs/main` (the first launch restart-looped).
- The checkpoint's chat template is **patched on a copy at launch** (`ChatTemplatePatcher`, anchors from the registry entry's `chat_template_patches`; a non-matching anchor stops the start) because Qwen3.8's own answered HTTP 400 to Codex's `high`/`minimal` efforts.
- The recipe samples with **PyTorch, not FlashInfer** (`--sampling-backend pytorch`), because FlashInfer's untruncated-sampling kernel (top_p 1, no top_k: the completions endpoint's default) returned token 0, `!`, for every sampled completions request, which greedy decoding and the chat path never hit, so only the NVFP4 canary in `server start` caught it.
- Anything that asks "which model is running" must ask the **server** (`model_key_for_served_id`), not the config: `configure` and `inspect` both used the configured model and broke when `server start --model` served another.
- **v0.5.21's `/v1/responses` split every announcement from its tool call** (found 2026-10-22). It tags text written before a tool call `phase: "commentary"`, Codex sends the item back, and the history rebuild (`_merge_consecutive_assistant_messages`, `serving_responses.py` line 1386) merges consecutive assistant items only when their phases are equal. A `function_call` carries no phase, so "Let me look at X." rendered as an assistant turn of its own, ended, followed by a turn holding only the call; after a few of those the model copies the pattern and ends its turn after an announcement. Found from SWE-bench: 57 early stops on nights 12 and 15, all plain text after earlier text-plus-call responses, the rate rising with how many such responses were in the context (0 with none, 1-2% with two or three, 4-12% with four or more), none on ling-engine, which merges regardless of phase. The image pin was the natural experiment: v0.5.19 (until 2026-10-18) tagged no phase, and nights 4-10 had no nudges and resolved 62-74%, against 40-52% on v0.5.21. Running SGLang's own conversion on night 12's django-15268 rollout gave three split announcements. The fix is the recipe's `runtime_patches` (`SGLANG_PHASE_MERGE_PATCH`): the merge ignores the phase, the image's file is patched on a copy (`SGLangRuntimePatcher`) and mounted read-only on every launch of the pinned image, together with the adapters' patch when they are served. A patch pinned to another image, a file whose sha256 differs, or an anchor that no longer matches stops `server start`, so an image bump has to re-check the patch. `tests/test_sglang_phase_merge.py` runs the pinned file's conversion (a verbatim excerpt) on that rollout: 3 splits unpatched, 0 patched.

**The image pin moved from v0.5.19 to v0.5.21 on 2026-10-14** (1.6.2): v0.5.19 skipped the recurrent-state save when accepted drafts crossed a GDN checkpoint, so a later prefix-cache hit paired new KV with a stale state (upstream #37817/#37818, fixed in v0.5.20), and it is exposed to the open concurrency corruption #36548. The wrong-answer probe that decided it is [sglang-probe-2026-10-14.md](sglang-probe-2026-10-14.md): 0 wrong on both images at 1, 2 and 8 streams and on the two-turn checkpoint shape, v0.5.21 a few percent faster. One flag changed name with the image: v0.5.21 split `--cuda-graph-max-bs` into `--cuda-graph-max-bs-decode` and `--cuda-graph-max-bs-prefill` and rejects the old name as ambiguous (the server restart-loops at start), so the recipe says `-decode`; prefill graphs are off anyway. Every other production flag, `--num-continuous-decode-steps` included (dead code upstream, kept until the owner drops it), is accepted unchanged. A node that updates pulls the 33.7 GB image at its next `server start` (`ensure_docker_image` pulls registry-qualified images), before the old container is replaced.

Measured (single stream, greedy): prose 25.5, code 50.3, JSON 87.0 tok/s, prefill ~1,700 tok/s (~1,000 at 116K tokens), ~38.7 GB of host memory still free; four ling tasks at once finished in 23 s.

**Sampling is the checkpoint's.** SGLang's default `--sampling-defaults model` takes temperature 1.0, top_p 0.95 and top_k 20 from `generation_config.json` for any request that sets none, which every `ling` request is; presence_penalty stays 0. Those are Qwen3.8's thinking-mode values, while `ling` runs with thinking off, for which the card gives temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5. Kept as it is by the owner's decision (2026-10-20); the non-thinking values are a SWE-bench arm (`--sampling nonthinking`, applied by the model gate; [loop-guard.md](loop-guard.md)). `DEFAULT_GENERATION_OVERRIDES` (greedy) is vLLM's only and does not reach SGLang. `--preferred-sampling-params` could not change this on `/v1/responses`: the endpoint always fills temperature, top_p and presence_penalty itself before the preferred values are consulted.

After the NVFP4 canary, `server start` checks a **tool call through `/v1/responses`**, the path `ling` uses (`ToolCallCanary`): one streamed request with a trivial tool, 60 s at most. A failure is a warning naming the parser or template likely at fault (`--tool-call-parser`, `--reasoning-parser`, `chat_template_patches`) and does not stop the start. A chat-completions probe would not catch it: the Responses path converts tools and parses calls in code of its own (specs/DREAMFERENCE_MODELS.md §2.1).

It is the only model served, and was the only model in the registry from 2026-10-07 to 2026-10-09: the Qwen 3.5 122B fallbacks and Qwen 3.6 35B were removed on 2026-10-07, with their vLLM recipes, their images (`Dockerfile.dflash`, `Dockerfile.dense`) and the `runtime/` patches. The vLLM launcher stays, tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`).

## A candidate checkpoint: Minima (night 2)

`qwen3.8-27b-minima-nvfp4-dflash2` serves `minima-ai/mnma_qwen3.8_27b_nvfp4`, Qwen3.8-27B with all 496 linear layers in NVFP4 (compressed-tensors, text only, 18.8 GB). It exists for night 2's SWE-bench A/B against production and is never the default. Spec: `specs/DREAMFERENCE_MODELS.md` §2.2.

- **One recipe.** Production's launch overrides are the module constant `QWEN38_SGLANG_RECIPE`. The candidate takes the constant whole and changes the `revision` plus one flag, so the A/B differs in the weights alone. A change to production's recipe therefore changes the candidate's too, which is intended for as long as the two are compared.
- **`--kv-cache-dtype bfloat16` is load-bearing.** SGLang turns a compressed-tensors `kv_cache_scheme` into an FP8 KV pool under `auto`, and production's pool is BF16.
- **Fused NVFP4 groups must share one global scale.** SGLang's `CompressedTensorsW4A4Fp4` takes the largest scale of a fused group (`in_proj_qkv`+`in_proj_z`, `in_proj_b`+`in_proj_a`, `q/k/v`, `gate/up`) and does not rescale, which serves a per-module-calibrated checkpoint silently wrong. `scripts/check_nvfp4_fused_scales.py <snapshot>` checks a checkpoint: Minima's 352 groups are equal. Run it on any NVFP4 checkpoint before serving it.
- **No image input.** The candidate is a text-only extraction (`supports_vision=False`).
- **Speed** is measured by `scripts/decode_speed.py` (stdlib only), which checks every answer it times. The prompts behind production's 25.5 / 50.3 / 87.0 were not recorded, so a comparison measures both arms on the same night.

## A model may pin its own vLLM image

`launch_overrides['docker_image']` overrides `DEFAULT_VLLM_IMAGE` for that model alone, because the engine is part of a recipe just as much as the flags are (the removed 122B DFlash recipe ran on a third-party vLLM build because the project image's vLLM tripped a KV page-size assert on its drafter). Two consequences worth knowing before touching this:

- `ensure_docker_image()` *pulls* anything registry-qualified (a `/` in the name) and only *builds* the project's own bare-tag image.
- `probe_image()` deliberately does not acquire an image: it reports on one that is already present, because it is called from `build_launch_command`, where a missing image must not start a multi-gigabyte download.

## The model matrix is the source of truth for launch flags

`hardware/model_matrix_registry.py` holds `ModelMatrixRegistry.MATRIX: Dict[str, ModelSpec]`, and each spec carries `launch_overrides`. `VLLMServerManager.build_launch_command()` layers these over the generic defaults, so per-model vLLM tuning (context length, memory ratio, attention/MoE backend, tool-call and reasoning parsers, speculative config) belongs in the registry entry, **not** in the launch builder. Tests assert this layering directly.

Speculation reaches vLLM only as `--speculative-config` JSON, built by `resolve_speculative_config()`: vLLM 0.2x has no `--speculative-model`/`--num-speculative-tokens` flags. A `--draft-model` is layered onto a recipe that already names an external drafter (keeping its method and attention backend) and replaces a self-speculation (MTP) recipe outright; a depth override applies only together with `--draft-model`. The compile-cache signature goes through the same function, so it tracks the launched depth, not the recipe's.

## Image input is a client-side claim

`ModelSpec.supports_vision` records whether a checkpoint takes images (verified against each checkpoint's `config.json`, never inferred from the alias); the retired Onyx web chat refused uploads without it, and Ask relies on it when it sends an image as `localImage` (MIGHTLING_ASK §1).

## The torch.compile cache is persistent and only partly self-invalidating

Every launch sets `VLLM_CACHE_ROOT` to `/root/.cache/dreamference/vllm`, inside the existing cache mount, so a graph compiled once survives container restarts (a cold compile is 8–12 minutes). vLLM names the cache directory after a hash of the engine config, traced sources and compiler, but `SpeculativeConfig.compute_hash()` contributes only whether the method needs auxiliary hidden states, **not `num_speculative_tokens`**, so retuning `n` alone would reuse the old graph. `VLLMServerManager._reset_stale_compile_cache()` covers that gap with its own `model|method|n` signature. Removal runs inside the image because the cache is written by a root container into a user-owned directory, which makes a host-side `rmtree` fail on the first subdirectory.

## Optional tensorizer image

Only needed if the NGC tag lacks `tensorizer`:

```bash
docker build -t dreamference-vllm-tensorizer:26.07-py3 .
```
