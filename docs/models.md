# Models

Every model Mightling can serve is listed in its model registry, together with the exact server
settings it runs with on a GB10. Choosing a model chooses all of its settings, so there is nothing
to tune by hand.

How well the default model solves real coding tasks, release by release, is on the
[Benchmarks](benchmarks.md) page.

## The registry

| Alias | Model | Parameters | Precision | Memory needed | Images |
|---|---|---|---|---|---|
| **`qwen3.8-27b-nvfp4-dflash2`** (default) | Qwen3.8-27B, NVFP4, with DFlash2 speculative decoding, served by ling-serve (SGLang with `--engine sglang`) | 27B | NVFP4 | 20 – 70 GB | yes (ling-serve from 1.8.1; SGLang too) |
| `qwen3.8-27b-dflash2-draft` | DFlash2 drafter for Qwen3.8-27B (not served on its own) | ~1B | NVFP4 | 1 – 2 GB | no |
| `qwen3.8-27b-minima-nvfp4-dflash2` (candidate, under evaluation) | Qwen3.8-27B with every layer in NVFP4 ("Minima"), same server settings and drafter as the default | 27B | NVFP4 | 19 – 70 GB | no |
| `qwen3.8-flash-next-nvfp4-mtp` (trial candidate) | Qwen3.8-Flash-Next (125B with 6B active, 51B n-gram table, 4B MTP head), QAD NVFP4 with MTP speculative decoding, served by vLLM | 180B | NVFP4 | 100 – 115 GB | yes |

List them, with their Hugging Face repositories, on your machine:

```bash
ling-admin model list
```

## The default model

`qwen3.8-27b-nvfp4-dflash2` is RadixArk's NVFP4 quantisation of Qwen3.8-27B, with DFlash2
speculative decoding: a small drafter proposes 12 tokens at a time and the model checks them in one
pass. Its output is exactly what the model alone would write; the drafter changes the speed, never
the answer. The recipe follows
[hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38), measured on a GB10.

### ling-serve, the default engine (1.8.0)

From 1.8.0 the default model is served by **ling-serve**, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
SGLang, the engine of 1.0 to 1.7, is one flag away (see [Going back to SGLang](#going-back-to-sglang)).
ling-serve was opt-in from 1.7.2 (`--engine ling`); nothing about that command changes.

What changes for you:

- **Four conversations at once**, each with a 196,608-token context (the model's full 262,144 comes
  with paged memory in a later 1.8.x). Until 1.7 the
  engine ran two at a time and a third waited. A fourth task, a phone thread or the desktop app's
  second window no longer queues behind the others.
- **Memory sized at start** from what the machine has free (see [Memory](#memory)).
- **Conversations take fair turns**: a long prompt being read does not stall the answers of the
  others.
- **A small image**: ling-serve's Docker image is under 1 GB, against SGLang's 34 GB, so the first
  start after an upgrade pulls much less.
- Everything else stays: the same checkpoint, the same drafter, the same `/v1` API on the same
  port, the same container limits and the same memory-safety checks before a load. `ling`, `ling web`,
  the desktop app and the phone bridges need no change.

<!-- TODO(1.8.0): measured figures (combined rate of four conversations, start time, memory in use),
     from the bar night on the pinned engine commit; none are stated here until then. -->

What 1.8.0 leaves to 1.8.1, served by SGLang meanwhile:

- **No LoRA adapters on ling-serve.** While an adapter is installed, `server start` serves the model
  with SGLang and says so; an explicit `--engine ling` is refused.
- **No API key**: `server start --api-key` is refused unless `--engine sglang` is given.

`server start` checks the NVFP4 output and a tool call before it reports ready. If ling-serve fails
one of them, or stops before it answers, it says so and prints the command to go back to SGLang; it
never switches engine by itself.

#### Memory

ling-serve plans its whole footprint before it allocates anything: the weights, scratch space, a
fixed part for each of the four conversations, the drafter, and the pool of pages that holds their
contexts. The pool takes what is free at that moment, so the same release sizes itself differently on
a machine with the desktop session closed and on one with a browser and other services resident.
`ling-admin server start` runs the same plan first and refuses to start, naming the shortfall, when the
host would be left without room (the check described in [Troubleshooting](#troubleshooting)).

What this means in practice:

- Free memory before you start the server for the best result; closing the desktop session on the
  machine recovers the most.
- The context a conversation can reach is not reduced; what shrinks on a loaded machine is the number
  of long conversations that fit at once. An idle conversation's pages are reclaimed first.
- While serving, a request that arrives when the host is nearly out of memory is answered with HTTP
  503 and the reason, instead of risking the whole machine.

`ling-admin status` shows the running engine, its commit, the number of conversations
(sessions) and the batch, and whether the KV cache is paged.

<!-- TODO(1.8.0): host memory in use while serving at four sessions (measured), and the free-memory
     figure below which the start is refused. -->

#### Prefix sharing and its privacy scope

Agents begin every conversation with much of the same text: the system prompt, the tool list, the
repository's instructions. ling-serve keeps the computed form of such beginnings and reuses it, so a
new conversation that starts with text the server has already read does not read it again.

Sharing is limited on purpose. `ling` marks every request with a **scope**, an opaque hash of the
owner and the repository the conversation is working in, in the `X-Ling-Share-Scope` header, and the
server reuses a beginning only for requests with the same scope. Two repositories, or two owners on a
shared machine, never share, even when their texts are identical; a repository's own conversations
share with each other. The scope is a hash: the server never sees the repository's name or path, and
the header carries nothing readable.

- It saves reading time only. Whether a beginning was reused never changes the answer.
- A request that carries no scope shares only within its own conversation.
- Nothing leaves the machine: the shared store is memory of the model server on this node.

<!-- TODO(1.8.0): confirm against the pinned engine that a request
     without it shares nothing across conversations; confirm what `ling` sends from which release. -->

#### Going back to SGLang

For one start:

```bash
ling-admin server stop && ling-admin server start --engine sglang
```

or to keep it, in `~/.config/dreamference/config.toml` (or `DREAMFERENCE_SERVER_ENGINE=sglang`):

```toml
server_engine = "sglang"
```

Remove the line (or set `server_engine = "ling"`) to follow the default again. With
[`ling-admin adapter`](admin.md#ling-admin-adapter) adapters installed, `server start` uses SGLang by
itself until 1.8.1, when ling-serve serves them.
SGLang keeps its own limits and settings, below.

#### Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `server start` says "Not enough memory to start ling-serve" and prints a plan, the load peak and the missing amount | The plan for four conversations at 196,608 tokens, plus the load peak, does not fit in the memory free now | Free memory (close the desktop session, stop other large services), or `ling-admin server start --engine sglang` |
| `server start` reports a failed NVFP4 or tool-call check and prints the way back | The engine started but its output failed a check | Run the printed `--engine sglang` command, and report it with `ling-admin server logs` |
| A request is answered with HTTP 503 and a `host_memory_low` reason | The machine is nearly out of memory; the server refuses new work to protect the host | Free memory and retry; the work already running continues |
| A new conversation waits | Four are already running (or kept warm) | It starts when one finishes; recently used conversations are protected for 30 seconds |
| Which engine is running? | | `ling-admin status` |
| Logs | | `ling-admin server logs` |

<!-- TODO(1.8.0): check each row against the pinned engine's messages; confirm the 503 body shape. -->

### SGLang, the fallback (`--engine sglang`)

SGLang served the default model through 1.7. It reads images and serves LoRA
adapters, and its speed comes from the same DFlash2 drafter.

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

Server settings it runs with when you choose it (from the registry):

| Setting | Value |
|---|---|
| Server | SGLang v0.5.21, pinned by digest |
| Checkpoint | `RadixArk/Qwen3.8-27B-NVFP4`, pinned commit |
| Context length | 262,144 tokens |
| Concurrent requests | 8 |
| Memory fraction | 0.50 |
| Speculative decoding | DFlash2, `dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch` (NVFP4 with searched scales, downloaded from Mightling's GitHub release assets and checked by SHA-256; the RTN-calibrated `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` until 1.7.2), 12 tokens |
| Tool calls / reasoning parsers | `qwen3_coder` / `qwen3` |
| Thinking | `ling` asks for none; chat thinks at medium effort unless told otherwise |

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
