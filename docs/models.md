# Models

Every model Mightling can serve is listed in its model registry, together with the exact server
settings it runs with on a GB10. Choosing a model chooses all of its settings, so there is nothing
to tune by hand.

## The registry

| Alias | Model | Parameters | Precision | Memory needed | Images |
|---|---|---|---|---|---|
| **`qwen3.8-27b-nvfp4-dflash2`** (default) | Qwen3.8-27B, NVFP4, with DFlash2 speculative decoding, served by ling-serve (SGLang as the fallback) | 27B | NVFP4 | 20 – 70 GB | on SGLang |
| `qwen3.8-27b-dflash2-draft` | DFlash2 drafter for Qwen3.8-27B (not served on its own) | ~1B | NVFP4 | 1 – 2 GB | no |
| `qwen3.8-27b-minima-nvfp4-dflash2` (candidate, under evaluation) | Qwen3.8-27B with every layer in NVFP4 ("Minima"), same server settings and drafter as the default | 27B | NVFP4 | 19 – 70 GB | no |

List them, with their Hugging Face repositories, on your machine:

```bash
ling-admin model list
```

## The default model

`qwen3.8-27b-nvfp4-dflash2` is RadixArk's NVFP4 quantisation of Qwen3.8-27B with DFlash2, a
drafter that proposes 12 tokens at a time for the model to check in one pass.

Since 1.7.2 it is served by **ling-serve**, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
Its speculation is exact (what the drafter proposes changes the speed, never the output), it
verifies the drafter's candidates as a tree, and it was tested against SGLang on SWE-bench nights
before it became the default. It runs in its own small image (`ghcr.io/dreamference/ling-serve`,
pinned by digest, its ling-engine commit in the image's labels), behind the same container limits
and host-safety checks as SGLang.

| Setting | ling-serve (default) | SGLang (fallback) |
|---|---|---|
| Image | `ghcr.io/dreamference/ling-serve`, pinned by digest | `lmsysorg/sglang` v0.5.21, pinned by digest |
| Checkpoint | `RadixArk/Qwen3.8-27B-NVFP4`, pinned commit | the same |
| Speculative decoding | DFlash2, `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`, 12 tokens, tree verification | the same drafter, 12 tokens, chain |
| Context length | 262,144 tokens | 262,144 tokens |
| Requests at once | 2 (two resident sessions); more wait | 8 |
| Container memory cap | the same as SGLang's (memory fraction 0.50 plus 24 GB) | memory fraction 0.50 |
| Tool calls / reasoning | `qwen3_coder` / `qwen3`, built in | `qwen3_coder` / `qwen3` parsers |
| Images | not read: an image arrives as the text `[image]` | read |
| Adapters (LoRA) | not served | served |
| API key (`--api-key`) | not supported | supported |

`ling` asks the server what it serves (the model, its context length, whether it reads images), so
nothing has to be configured on the clients when the engine changes.

### Serving with SGLang instead

SGLang serves the same checkpoint with the same drafter and was the engine until 1.7.2. Switch for
one start:

```bash
ling-admin server stop && ling-admin server start --engine sglang
```

or keep it, in `~/.config/dreamference/config.toml` (or `DREAMFERENCE_SERVER_ENGINE=sglang`):

```toml
server_engine = "sglang"
```

Use SGLang when you need image input, LoRA adapters, an API key or more than two requests running
at once. If ling-serve fails one of `server start`'s checks (it does not answer, the NVFP4 canary or
the tool-call check fails), `server start` says so and prints the command above; it never switches
engine by itself.

SGLang measured on a GB10, single-stream, 2026-10-09, with 12 draft tokens
(`scripts/decode_speed.py`): prose 32.0, code 68.6, JSON 84.1 tokens per second, reading a prompt at
~1,800 tokens per second (~1,000 at 116K tokens). The recipe follows
[hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38). It reads images, holds
a 262K-token context, and while serving leaves about 38 GB of memory free.

## Changing models

```bash
ling-admin main-model set <alias>                     # pick the main model
ling-admin model download --model <alias>             # fetch weights ahead of time
ling-admin server stop && ling-admin server start   # restart with the new choice
```

The terminal agent needs no change: it asks the server which model it serves each time it starts.
Image search names the served model when it starts, so `ling-admin images start` again after a
change (`main-model set` reminds you when it runs).

`ling-admin main-model inspect` runs sample prompts against the running model and reports how it
was launched.
