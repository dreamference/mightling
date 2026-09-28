---
hide:
  - navigation
---

<div class="puffin-hero" markdown>
<img src="assets/puffin.svg" alt="Puffin logo">
<div markdown>
# Puffin

A private AI assistant and coding agent that runs entirely on your NVIDIA GB10. The model, your
code and your conversations stay on your machine.
</div>
</div>

Puffin serves a 122-billion-parameter model on a single NVIDIA GB10 workstation (128 GB of
unified memory) and puts three things in front of it:

<div class="puffin-cards" markdown>
<div markdown>
### Terminal agent

`puffin` reads your code, runs commands and makes changes in your repository. It is built on
OpenAI's open-source Codex CLI, so its commands and flags will be familiar, but its model is the
one on your machine.

[Terminal agent →](puffin.md)
</div>
<div markdown>
### Web chat

A browser chat assistant with web search, voice input, image understanding and read-only access
to your Gmail, all answered by the same local model.

[Web chat →](web-chat.md)
</div>
<div markdown>
### Desktop app

`puffin-app` puts the web chat in a window of its own, with its own launcher entry and icon.

[Desktop app →](desktop.md)
</div>
</div>

## Why Puffin

- **Local by design.** Inference runs on your GB10. The coding agent sends its prompts to the model
  you serve, not to a cloud model, and needs no OpenAI account. The web chat's telemetry is switched
  off. See [Privacy & security](privacy.md) for exactly what does leave the machine, including one
  known gap.
- **A large model on one desk.** The default model is Qwen 3.5 122B-A10B with speculative decoding.
  On a GB10 it measures 23.8 tokens/s on prose, 49.9 on code and 53.1 on JSON, single-stream at a
  32k context with eight slots. See [Models](models.md).
- **Built to keep the machine up.** On the GB10, the GPU and the operating system share one pool of
  memory, so an oversized model load can freeze the whole machine. Puffin checks the host before
  loading and watches memory pressure while it loads. See [Architecture](architecture.md).
- **Familiar tools.** If you have used the Codex CLI, `puffin exec`, `puffin resume --last` and
  `-c key=value` work the same way.

## Quick start

With Puffin installed (see [Get started](getting-started.md)):

```bash
puffin-admin server start     # load the model (several minutes the first time)
cd ~/my-project && puffin     # start the terminal agent in a repository
```
