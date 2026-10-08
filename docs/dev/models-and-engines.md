# Models, engines and launch flags

Developer notes behind the "Models and the model server" summary in `AGENTS.md`. Specs: `specs/DREAMFERENCE_INFERENCE.md`, `specs/DREAMFERENCE_MODELS.md`.

## Config precedence

`config/dreamference_config.py`: every field resolves through the same 4-step chain, in `DreamferenceConfig.__init__`: constructor kwarg → `DREAMFERENCE_*` env var → `dreamference.toml` (local, then `~/.config/dreamference/config.toml`) → module-level `DEFAULT_*` constant. `save_config()` deliberately writes only values that differ from the defaults, so a round-trip does not fossilize defaults into the TOML.

## A model may name its engine

`launch_overrides['engine'] = 'sglang'` serves it with SGLang instead of vLLM; the default model, `qwen3.8-27b-nvfp4-dflash2` (Qwen3.8-27B NVFP4 with the DFlash2 drafter, since 2026-09-29), is the one that does, because DFlash2 runs only in SGLang. `SGLangLaunchBuilder` builds only what follows the image; the container comes from the same `docker run` prefix as vLLM (`_docker_run_prefix`), so the host-safety layer is engine-independent.

Four traps, all handled:

- Pinned checkpoints go in as **snapshot directories**, because SGLang drops `--revision` on some offline lookups and a download by commit writes no `refs/main` (the first launch restart-looped).
- The checkpoint's chat template is **patched on a copy at launch** (`ChatTemplatePatcher`, anchors from the registry entry's `chat_template_patches`; a non-matching anchor stops the start) because Qwen3.8's own answered HTTP 400 to Codex's `high`/`minimal` efforts.
- The recipe samples with **PyTorch, not FlashInfer** (`--sampling-backend pytorch`), because FlashInfer's untruncated-sampling kernel (top_p 1, no top_k: the completions endpoint's default) returned token 0, `!`, for every sampled completions request, which greedy decoding and the chat path never hit, so only the NVFP4 canary in `server start` caught it.
- Anything that asks "which model is running" must ask the **server** (`model_key_for_served_id`), not the config: `configure` and `inspect` both used the configured model and broke when `server start --model` served another.

Measured (single stream, greedy): prose 25.5, code 50.3, JSON 87.0 tok/s, prefill ~1,700 tok/s (~1,000 at 116K tokens), ~38.7 GB of host memory still free; four ling tasks at once finished in 23 s.

It is the only model served, and the only model in the registry since 2026-10-07: the Qwen 3.5 122B fallbacks and Qwen 3.6 35B were removed on that date, with their vLLM recipes, their images (`Dockerfile.dflash`, `Dockerfile.dense`) and the `runtime/` patches. The vLLM launcher stays, tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`).

## A model may pin its own vLLM image

`launch_overrides['docker_image']` overrides `DEFAULT_VLLM_IMAGE` for that model alone, because the engine is part of a recipe just as much as the flags are (the removed 122B DFlash recipe ran on a third-party vLLM build because the project image's vLLM tripped a KV page-size assert on its drafter). Two consequences worth knowing before touching this:

- `ensure_docker_image()` *pulls* anything registry-qualified (a `/` in the name) and only *builds* the project's own bare-tag image.
- `probe_image()` deliberately does not acquire an image: it reports on one that is already present, because it is called from `build_launch_command`, where a missing image must not start a multi-gigabyte download.

## The model matrix is the source of truth for launch flags

`hardware/model_matrix_registry.py` holds `ModelMatrixRegistry.MATRIX: Dict[str, ModelSpec]`, and each spec carries `launch_overrides`. `VLLMServerManager.build_launch_command()` layers these over the generic defaults, so per-model vLLM tuning (context length, memory ratio, attention/MoE backend, tool-call and reasoning parsers, speculative config) belongs in the registry entry, **not** in the launch builder. Tests assert this layering directly.

Speculation reaches vLLM only as `--speculative-config` JSON, built by `resolve_speculative_config()`: vLLM 0.2x has no `--speculative-model`/`--num-speculative-tokens` flags. A `--draft-model` is layered onto a recipe that already names an external drafter (keeping its method and attention backend) and replaces a self-speculation (MTP) recipe outright; a depth override applies only together with `--draft-model`. The compile-cache signature goes through the same function, so it tracks the launched depth, not the recipe's.

## Image input is a client-side claim

`ModelSpec.supports_vision` records whether a checkpoint takes images (verified against each checkpoint's `config.json`, never inferred from the alias); the web chat needs it, see [onyx.md](onyx.md#image-input-is-a-client-side-claim-not-a-server-capability).

## The torch.compile cache is persistent and only partly self-invalidating

Every launch sets `VLLM_CACHE_ROOT` to `/root/.cache/dreamference/vllm`, inside the existing cache mount, so a graph compiled once survives container restarts (a cold compile is 8–12 minutes). vLLM names the cache directory after a hash of the engine config, traced sources and compiler, but `SpeculativeConfig.compute_hash()` contributes only whether the method needs auxiliary hidden states, **not `num_speculative_tokens`**, so retuning `n` alone would reuse the old graph. `VLLMServerManager._reset_stale_compile_cache()` covers that gap with its own `model|method|n` signature. Removal runs inside the image because the cache is written by a root container into a user-owned directory, which makes a host-side `rmtree` fail on the first subdirectory.

## Optional tensorizer image

Only needed if the NGC tag lacks `tensorizer`:

```bash
docker build -t dreamference-vllm-tensorizer:26.07-py3 .
```
