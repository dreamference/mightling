---
hide:
  - navigation
---

<div class="puffin-hero" markdown>
<img src="assets/puffin.svg" alt="Puffin logo">
<div markdown>
# Puffin

Your own AI coding agent. On your desk. Nothing leaves. Puffin turns an NVIDIA DGX Spark, or any
GB10 machine, into a private AI that codes, searches and answers for you, with no cloud, no
account, no API bill and no telemetry.
</div>
</div>

Puffin serves a strong open model on one GB10 machine (128 GB of unified memory) and puts three
things in front of it:

<div class="puffin-cards" markdown>
<div markdown>
### Terminal agent

`puffin` reads your code, runs commands and tests, and makes changes in your repository, asking
before anything risky. Its model is the one on your machine.

[Terminal agent →](puffin.md)
</div>
<div markdown>
### Web chat

A browser chat assistant with web search, voice input and image understanding, all answered by the
same local model.

[Web chat →](web-chat.md)
</div>
<div markdown>
### Desktop app

`puffin-app` puts the web chat in a window of its own, with a Work window (preview) that drives
agent sessions with approvals, diffs and undo.

[Desktop app →](desktop.md)
</div>
</div>

## Why Puffin

- **Confidential, and you can prove it.** Inference runs on your GB10, nothing in Puffin phones
  home, and `puffin-admin audit egress` traces a real session and lists every connection it made.
  `/airgapped on` takes the network away from every command the agent runs. See
  [Privacy & security](privacy.md) for exactly what can leave the machine, and when.
- **Fast, with no rate limits.** The default model, Qwen3.8-27B with speculative decoding, measures
  50 tokens/s on code, 87 on JSON and 25 on prose on a GB10, single-stream, with a 262K-token
  context. Run several agents at once: there is no quota and no bill. See [Models](models.md).
- **Easy.** One installer, no tuning, and the model load is guarded so it cannot freeze the
  machine: on the GB10 the GPU and the system share one pool of memory, so Puffin checks the host
  before loading and watches memory pressure while it loads. See [Architecture](architecture.md).

## Quick start

With Puffin installed (see [Get started](getting-started.md)):

```bash
puffin-admin server start     # load the model (several minutes the first time)
cd ~/my-project && puffin     # start the terminal agent in a repository
```
