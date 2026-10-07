# Models

Every model Mightling can serve is listed in its model registry, together with the exact server
settings it runs with on a GB10. Choosing a model chooses all of its settings, so there is nothing
to tune by hand.

## The registry

| Alias | Model | Parameters | Precision | Memory needed | Images |
|---|---|---|---|---|---|
| **`qwen3.8-27b-nvfp4-dflash2`** (default) | Qwen3.8-27B, NVFP4, with DFlash2 speculative decoding, served by SGLang | 27B | NVFP4 | 20 – 70 GB | yes |
| `qwen3.8-27b-dflash2-draft` | DFlash2 drafter for Qwen3.8-27B (not served on its own) | ~1B | NVFP4 | 1 – 2 GB | no |

List them, with their Hugging Face repositories, on your machine:

```bash
mling-admin model list
```

## The default model

`qwen3.8-27b-nvfp4-dflash2` is RadixArk's NVFP4 quantisation of Qwen3.8-27B, served by **SGLang**
rather than vLLM, because its speed comes from a drafter only SGLang runs: DFlash2, which proposes
16 tokens at a time for the model to check in one pass. The recipe follows
[hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38), measured on a GB10.

Measured on a GB10, single-stream:

| Output | Tokens per second |
|---|---|
| Prose | 25.5 |
| Code | 50.3 |
| JSON | 87.0 |
| Reading a prompt | ~1,700 (a 13K-token prompt in 8 s; ~1,000 at 116K tokens) |

It reads images, holds a 262K-token context (a fact planted in the middle of a 116K-token prompt was
found), and while serving leaves about 38 GB of memory free, so several agents can work at once:
four `mling` tasks in parallel finished in 23 seconds.

Server settings it runs with (from the registry):

| Setting | Value |
|---|---|
| Server | SGLang v0.5.19, pinned by digest |
| Checkpoint | `RadixArk/Qwen3.8-27B-NVFP4`, pinned commit |
| Context length | 262,144 tokens |
| Concurrent requests | 8 |
| Memory fraction | 0.50 |
| Speculative decoding | DFlash2, `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`, 16 tokens |
| Tool calls / reasoning parsers | `qwen3_coder` / `qwen3` |
| Thinking | `mling` asks for none; chat thinks at medium effort unless told otherwise |

## Changing models

```bash
mling-admin main-model set <alias>                     # pick the main model
mling-admin model download --model <alias>             # fetch weights ahead of time
mling-admin server stop && mling-admin server start   # restart with the new choice
```

`main-model set` also points a running web chat at the new model (`--no-onyx` skips that). The
terminal agent needs no change: it asks the server which model it serves each time it starts.

`mling-admin main-model inspect` runs sample prompts against the running model and reports how it
was launched.
