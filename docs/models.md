# Models

Every model Mightling can serve is listed in its model registry, together with the exact server
settings it runs with on a GB10. Choosing a model chooses all of its settings, so there is nothing
to tune by hand.

How well the default model solves real coding tasks, release by release, is on the
[Benchmarks](benchmarks.md) page.

## The registry

| Alias | Model | Parameters | Precision | Memory needed | Images |
|---|---|---|---|---|---|
| **`qwen3.8-27b-nvfp4-dflash2`** (default) | Qwen3.8-27B, NVFP4, with DFlash2 speculative decoding, served by SGLang | 27B | NVFP4 | 20 – 70 GB | yes |
| `qwen3.8-27b-dflash2-draft` | DFlash2 drafter for Qwen3.8-27B (not served on its own) | ~1B | NVFP4 | 1 – 2 GB | no |
| `qwen3.8-27b-minima-nvfp4-dflash2` (candidate, under evaluation) | Qwen3.8-27B with every layer in NVFP4 ("Minima"), same server settings and drafter as the default | 27B | NVFP4 | 19 – 70 GB | no |

List them, with their Hugging Face repositories, on your machine:

```bash
ling-admin model list
```

## The default model

`qwen3.8-27b-nvfp4-dflash2` is RadixArk's NVFP4 quantisation of Qwen3.8-27B, served by **SGLang**
rather than vLLM, because its speed comes from a drafter only SGLang runs: DFlash2, which proposes
12 tokens at a time for the model to check in one pass. The recipe follows
[hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38), measured on a GB10.

Measured on a GB10, single-stream, 2026-10-09, with 12 draft tokens (`scripts/decode_speed.py`):

| Output | Tokens per second |
|---|---|
| Prose | 32.0 |
| Code | 68.6 |
| JSON | 84.1 |
| Reading a prompt | ~1,800 (a 13K-token prompt in 7.3 s; ~1,000 at 116K tokens) |

It reads images, holds a 262K-token context (a fact planted in the middle of a 116K-token prompt was
found), and while serving leaves about 38 GB of memory free, so several agents can work at once:
four `ling` tasks in parallel finished in 23 seconds.

Server settings it runs with (from the registry):

| Setting | Value |
|---|---|
| Server | SGLang v0.5.19, pinned by digest |
| Checkpoint | `RadixArk/Qwen3.8-27B-NVFP4`, pinned commit |
| Context length | 262,144 tokens |
| Concurrent requests | 8 |
| Memory fraction | 0.50 |
| Speculative decoding | DFlash2, `dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch` (NVFP4 with searched scales, downloaded from Mightling's GitHub release assets and checked by SHA-256; the RTN-calibrated `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` until 1.7.2), 12 tokens |
| Tool calls / reasoning parsers | `qwen3_coder` / `qwen3` |
| Thinking | `ling` asks for none; chat thinks at medium effort unless told otherwise |

### ling-serve, opt-in

Since 1.7.2 the default model can also be served by **ling-serve**, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
SGLang stays the default; ling-serve becomes the default once it reads images, serves adapters and
runs more than two requests at once. Switch for one start:

```bash
ling-admin server stop && ling-admin server start --engine ling
```

or keep it, in `~/.config/dreamference/config.toml` (or `DREAMFERENCE_SERVER_ENGINE=ling`):

```toml
server_engine = "ling"
```

Back to SGLang: remove that line, or `ling-admin server start --engine sglang`. If ling-serve fails
one of `server start`'s checks (it does not answer, the NVFP4 canary or the tool-call check fails),
`server start` says so and prints the command to go back; it never switches engine by itself.

It serves the same checkpoint with the same drafter and the same 262,144-token context, from its
own small image (`ghcr.io/dreamference/ling-serve`, pinned by digest, the ling-engine commit in its
labels), inside the same container limits and host-safety checks. Its speculation is exact (the
drafter changes the speed, never the output) and it verifies the drafter's guesses as a tree. Its
limits:

- **No images.** An image arrives as the text `[image]`; `ling` and the phone bridges learn this
  from the server and send text.
- **No LoRA adapters**, and no API key (`--api-key` is refused).
- **Two requests at once**; a third waits until one finishes.
- **Changing the end of a long prompt reads it again**: a new prompt that shares a long beginning
  with an earlier one but differs inside the same message is read in full (at ~850-900 tokens per
  second near 200K tokens, nearly four minutes).

Measured on a GB10 on 2026-10-26 (ling-engine bdda3a2, 262,144-token context): ready in 40 s, about
60 GB of memory in use while serving; a 600-token code answer at 74.9 tokens per second; a
204,857-token prompt with two facts hidden at 45% and 85% of its length answered both exactly, with
decode at 39.6 tokens per second at that length.

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
