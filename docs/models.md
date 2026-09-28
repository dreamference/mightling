# Models

Every model Puffin can serve is listed in its model registry, together with the exact server
settings it runs with on a GB10. Choosing a model chooses all of its settings, so there is nothing
to tune by hand.

## The registry

| Alias | Model | Parameters | Precision | Memory needed | Images |
|---|---|---|---|---|---|
| **`qwen3.5-122b-a10b-hybrid-dflash`** (default) | Qwen 3.5 122B-A10B, INT4+FP8 hybrid, with DFlash speculative decoding | 122B (10B active) | INT4 + FP8 | 71.5 – 120 GB | yes |
| `qwen3.5-122b-a10b-int4-dflash` | Qwen 3.5 122B-A10B, INT4 AutoRound, with DFlash | 122B (10B active) | INT4 | 71.5 – 120 GB | yes |
| `qwen3.5-122b-a10b-nvfp4` | Qwen 3.5 122B-A10B | 122B (10B active) | NVFP4 | 78 – 120 GB | yes |
| `qwen3.6-35b-a3b-nvfp4` | Qwen 3.6 35B-A3B | 35B (3B active) | NVFP4 | 25 – 60 GB | no |
| `qwen3.5-122b-a10b-dflash-draft` | DFlash drafter for the 122B models (not served on its own) | 0.8B | BF16 | 1.5 – 2.5 GB | no |
| `tiny-a2d-coder-0.5b-diffusion` | Tiny-A2D, a diffusion conversion of Qwen 2.5 Coder 0.5B | 0.6B | BF16 | 1.5 – 3 GB | no |

List them, with their Hugging Face repositories, on your machine:

```bash
puffin-admin model list
```

## The default model

`qwen3.5-122b-a10b-hybrid-dflash` is Intel's INT4 AutoRound quantisation of Qwen 3.5 122B-A10B,
with its dense layers in FP8. A small drafter model (DFlash) proposes 12 tokens at a time and the
large model checks them in one pass. That is why structured output such as code and JSON comes out
faster than prose.

Measured on a GB10, single-stream, at a 32k context with eight slots:

| Output | Tokens per second |
|---|---|
| Prose | 23.8 |
| Code | 49.9 |
| JSON | 53.1 |

Server settings it runs with (from the registry):

| Setting | Value |
|---|---|
| Context length | 32,768 tokens |
| Concurrent sequences | 8 |
| GPU memory fraction | 0.7 |
| Speculative decoding | DFlash, `z-lab/Qwen3.5-122B-A10B-DFlash`, 12 tokens |
| Prefix caching | on |
| Attention backend | FlashAttention |
| Tool calls / reasoning parsers | `qwen3_xml` / `qwen3` |
| Thinking | off by default |
| Server image | a pinned vLLM build with the dense-layer optimisations |

`qwen3.5-122b-a10b-int4-dflash` stays in the registry as the tested fallback.

## The diffusion model

Beside the main model, `puffin-admin server start` also runs a small code-diffusion model,
`tiny-a2d-coder-0.5b-diffusion`, on port 8001 with its own OpenAI-compatible endpoint. The main
model server cannot serve diffusion models, so it runs in a separate container with a fixed 8 GB
memory cap. If it ever runs away, only that container is stopped. It starts before the main model,
so the main model's memory check already accounts for it.

## Changing models

```bash
puffin-admin main-model set qwen3.6-35b-a3b-nvfp4       # pick the main model
puffin-admin diffusion-model set <alias>                # pick the diffusion model
puffin-admin model download --model <alias>             # fetch weights ahead of time
puffin-admin server stop && puffin-admin server start   # restart with the new choice
```

`main-model set` also points a running web chat at the new model (`--no-onyx` skips that). The
terminal agent needs no change: it asks the server which model it serves each time it starts.

`puffin-admin main-model inspect` runs sample prompts against the running model and reports how it
was launched.
