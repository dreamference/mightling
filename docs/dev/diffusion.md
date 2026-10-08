# The diffusion sidecar (switched off)

Developer notes behind the diffusion line in `AGENTS.md`. Specs: `specs/DREAMFERENCE_INFERENCE.md`, `specs/DREAMFERENCE_MIGHTLING_FAST_TOOLS.md`.

## Switched off since 2026-10-03

`DIFFUSION_ENABLED = False` in `hardware/model_matrix_registry.py`: the only diffusion model that fits beside the main one, Tiny-A2D 0.5B, was measured unusable in every role tried (MIGHTLING_FAST_TOOLS §1, MIGHTLING_COMPACTION §9.3). While it is off:

- `server start` starts no sidecar and silently removes one an older Mightling left (it runs with `--restart unless-stopped`); `server stop`/`remove` do the same;
- `model list`, `model download --all` and `docs/` leave the model out;
- `model download` and `main-model set` refuse it;
- `endpoints` prints no diffusion URL, and `diffusion-model` is not a command;
- the `--diffusion-*` flags are still accepted, with their help suppressed.

The code below is kept for a capable diffusion model later (FAST_TOOLS §3), and setting the constant to `True` restores all of it. What follows describes it as it works when switched on.

## Every configuration names a diffusion model beside the main one

Default `tiny-a2d-coder-0.5b-diffusion`, the Tiny-A2D bd3lm conversion of Qwen2.5-Coder 0.5B. It resolves through the same 4-tier chain and the same pinning semantics as `model`, and `ling-admin server start` launches both: vLLM for the main model, and `DiffusionServerManager` (`vllm_server/diffusion_server_manager.py`) for the diffusion one, a `dreamference-diffusion-<port>` container (default 8001) running `diffusion_openai_service.py` (a stdlib server speaking the chat-completions API; torch/transformers imports stay inside methods because the host imports this module without torch).

Three choices are load-bearing:

- Diffusion checkpoints **cannot be served by vLLM** (`ModelSpec.is_diffusion` records it, and `main-model set`/`diffusion-model set` refuse each other's kind).
- The sidecar rides in the **main model's resolved docker image**, not `DEFAULT_VLLM_IMAGE`, because the default is a bare tag `ensure_docker_image` would *build* and has plausibly never been built on a machine whose main model pins its own image.
- The sidecar starts **before** the vLLM launch, because vLLM's pre-flight reads current free memory: a resident sidecar is accounted for, where the reverse order lets a marginal KV check pass and then lose the sidecar's memory mid-load.

No PSI watchdog: a fixed `--memory=8g` cap (swap equal) turns the worst case into a contained OOM kill.

## Sampling

**The sidecar samples by block diffusion itself** (`block_diffusion_sample`), not through `model.generate`, which is transformers' left-to-right decoder and never worked on this checkpoint: 32-token blocks of mask tokens, block-causal attention (the prompt is block 0; a block sees itself and earlier blocks), most-confident positions committed first.

Loading needs two shims, both in `load()`: imports under a `__main__` guard are dropped from transformers' text scan of the remote code (the Tiny-A2D file imports `dllm` there and was refused), and each decoder layer's `attention_type` is restored from `config.layer_types`. Until 2026-09-29 the sidecar had never produced a token. Use chat completions: raw completions on this chat-tuned 0.5B model are weak whatever the attention.
