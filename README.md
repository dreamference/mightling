<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/puffin-logo-dark-outlined.svg">
  <img src="images/puffin-logo-light-outlined.svg" alt="Puffin" width="320">
</picture>

## Private AI on your DGX Spark. One command.

**A fully local alternative to cloud coding agents.** A coding agent, a chat assistant and a desktop
app, running entirely on your own GB10. No cloud. No account. No API bill. No telemetry.

[**Install**](#install) · [**Try it**](#try-these-first) · [**Proof**](#-your-code-never-leaves-your-desk) · [**Docs**](docs/getting-started.md) · [**Releases**](https://github.com/dreamference/puffin/releases)

[![Latest release](https://img.shields.io/github/v/release/dreamference/puffin?label=release&color=FF6B35)](https://github.com/dreamference/puffin/releases/latest)
[![Telemetry: none](https://img.shields.io/badge/telemetry-none-242A32)](docs/privacy.md)
[![Runs on any GB10](https://img.shields.io/badge/runs%20on-any%20GB10%20%C2%B7%20128%20GB-555555)](#runs-on-every-gb10)
[![GitHub stars](https://img.shields.io/github/stars/dreamference/puffin?style=flat&color=FFB400)](https://github.com/dreamference/puffin/stargazers)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue)](LICENSE)

<img src="images/puffin-hero.svg" alt="Install Puffin, start the model, run the agent, then audit what left the machine: every connection on 127.0.0.1, no DNS queries" width="760">

| **0** | **87 tok/s** | **$0** | **262K** |
|:---:|:---:|:---:|:---:|
| phone-home connections, verified by `audit egress` | JSON on one GB10 | per token, forever | tokens of context |

</div>

## Install

```bash
curl -fsSL https://github.com/dreamference/puffin/releases/latest/download/install.sh | bash
```

Then start the model and point Puffin at a project:

```bash
puffin-admin server start      # downloads the model once (~20 GB), then serves it
cd ~/your-project && puffin    # that's it
```

Works on the **NVIDIA DGX Spark** and every GB10 machine from Acer, ASUS, Dell, Gigabyte, HP, Lenovo
and MSI. The script is short: it verifies every download against the release's checksums and prints
each `sudo` command before running it. [Read it first](install.sh) if you like.

Already using a coding agent? Paste this into it:

```text
Install Puffin on this GB10 from https://github.com/dreamference/puffin (one-line installer in the README), then run puffin-admin server start.
```

## Try these first

```bash
puffin "explain this repository to me like I'm new on the team"
puffin "the tests are failing: find out why and fix them"
puffin "add input validation to the signup form, with tests"
```

Or queue work for tonight, inside a session:

```text
/night add upgrade every dependency and fix whatever breaks
```

Puffin works through the queue while you sleep, each task on its own git branch. You review the
branches over coffee.

## Why switch

| | Cloud coding agents | Puffin |
|---|---|---|
| Where your code goes | Their servers | Nowhere |
| Account needed | Yes | No |
| Cost per token | Metered | Zero |
| Rate limits | Yes | No |
| Works with the network unplugged | No | Yes |
| You can verify what leaves | No | `puffin-admin audit egress` |

---

## Why Puffin

### 🔒 Your code never leaves your desk

Cloud coding agents send your source code to someone else's servers. Puffin sends it nowhere: the
model runs on your GB10, and nothing in Puffin phones home. No usage analytics, no metrics, no
sign-in, no "anonymous" crash reports. That makes it usable for code you're not allowed to upload:
client work, regulated data, unreleased products.

**And you can prove it.** One command runs a real Puffin session under `strace` and lists every
network connection it made:

```console
$ puffin-admin audit egress
✅ Egress audit: pass
Network destinations:
   127.0.0.1:8000             29x  model server
   127.0.0.1:8767              1x  Gmail search service
DNS queries: none
Networked git commands: none
```

Everything stayed on `127.0.0.1`. Not one DNS lookup.

Need a hard guarantee? Type `/airgapped on` and every command the agent runs gets an empty network
namespace from the Linux kernel. It isn't a polite request to the model: there is simply no network
to reach. [Privacy & security →](docs/privacy.md)

### ⚡ Fast, with no rate limits and no bill

| Single stream on a GB10 | |
|---|---|
| Code | **50 tokens/s** |
| JSON | **87 tokens/s** |
| Prose | **25 tokens/s** |
| Reading your code (prefill) | **~1,700 tokens/s** |
| Context window | **262K tokens** |

A small drafter proposes 16 tokens at a time and the model checks them in one pass, which is why
code comes out fastest. Run four agents at once and they all keep going: no quota, no "please wait",
nothing to pay per token, ever.

### ✨ Nothing to configure

- **No tuning.** The model, its context length, memory share, kernels and speculative decoding are
  chosen for the GB10 already.
- **It won't freeze your machine.** On a GB10 the GPU and the system share one pool of memory, and an
  oversized model load can lock up the whole box. Puffin checks the host first and watches memory
  pressure during the load, stopping it before the machine stalls.
- **Your other machines find it.** Run `puffin-admin node enable` on the GB10, install the same
  script on another arm64 Linux machine on your network, and `puffin` there finds the GB10 by itself.
- **Updates are one command:** `puffin update`.

## What you get

| | |
|---|---|
| 🖥️ **`puffin`, the terminal agent** | Reads your repository, runs commands and tests, edits code, and asks before anything risky. [More →](docs/puffin.md) |
| 🌙 **Night Shift** | `/night add <task>` before bed; each task done on its own git branch by morning. Nothing is merged or pushed without you. |
| 🔎 **Web search and fetch** | Through a private SearXNG on your own machine: no search account, no API key. |
| 🧭 **A code index** | Definitions, callers and impact across your repository, so the agent finds code instead of grepping for it. |
| 🧩 **Your existing skills** | Skills you already wrote for Claude Code, Gemini CLI, OpenClaw or Hermes work in Puffin unchanged. |
| 💬 **Web chat** | A browser assistant with web search, voice input and image understanding, on the same local model. [More →](docs/web-chat.md) |
| 🪟 **Desktop app** | The chat in a window of its own, plus a Work window (preview) for agent sessions with approvals, diffs and undo. [More →](docs/desktop.md) |
| 📬 **Gmail, Drive, Calendar** (preview) | Read-only, through a sign-in that stays on your machine. Switched off entirely at `/airgapped on`. |

## Runs on every GB10

NVIDIA DGX Spark · Acer Veriton GN100 · ASUS Ascent GX10 · Dell Pro Max with GB10 · Gigabyte AI TOP
ATOM · HP ZGX Nano · Lenovo ThinkStation PGX · MSI EdgeXpert

They share the chip, the 128 GB of unified memory and DGX OS 7, so one installer covers them all.
Puffin is developed and tested daily on the ASUS Ascent GX10; the others are covered by tests.

## FAQ

**Do I need a GB10?** Yes, to run the model. Another arm64 Linux machine on your network can use it
as a client: the same install command sets that up automatically. Clients for x86 Linux, macOS and
Windows on Arm (the RTX Spark laptops) are planned.

**Is it really free?** Yes. Puffin is open source under the AGPL, and the model's weights are free.
Your only running cost is the electricity.

**What do I need besides the machine?** The OS it ships with (DGX OS 7, or Ubuntu 24.04 where the
vendor offers it), Docker with the NVIDIA Container Toolkit, Python 3 and Git. On plain Ubuntu,
`puffin-admin host check` tells you what's missing.

**Does anything ever reach the internet?** Only what you or the agent asks for, such as a web search.
The full list is below, and `/airgapped on` turns all of it off.

**I found a bug, or something the egress audit flagged.** Please [open an issue](https://github.com/dreamference/puffin/issues).
Reports from the audit are the most valuable ones we get.

<details>
<summary><b>What does reach the network, and when</b></summary>

Local isn't the same as air-gapped, and Puffin doesn't pretend otherwise. These are the only things
that leave the machine, and each one happens because you or the agent asked for it:

| When | What is sent | To |
|---|---|---|
| Installing, first model start | Container images, packages, model weights | Docker registries, PyPI, Hugging Face, GitHub |
| The agent or chat searches the web | The search query | Search engines, through SearXNG on your machine |
| The agent fetches a page | A request for that URL | That website |
| You connect Gmail, Drive or Calendar | Read-only requests | Google |
| You run `puffin update` | A release check and download | GitHub |

A search query is written by the model and can contain fragments of your context. At
`/airgapped on` none of these happen: no search, no fetch, no mail, only the model on your machine.

</details>

<details>
<summary><b>The model</b></summary>

Puffin serves one model, chosen and tuned for the GB10: **Qwen3.8-27B** in NVIDIA's 4-bit NVFP4
format (`qwen3.8-27b-nvfp4-dflash2`), served by SGLang with the DFlash2 drafter, a 262K-token
context and about 20 GB of weights. While it serves, roughly 38 GB of the machine's memory stays
free. [More about the model →](docs/models.md)

</details>

<details>
<summary><b>Configuration</b></summary>

Every setting resolves the same way: command-line flag, then `DREAMFERENCE_*` environment variable,
then `dreamference.toml` (in the project, then `~/.config/dreamference/config.toml`), then the
built-in default.

```toml
# dreamference.toml
vllm_host = "http://localhost:8000"
model = "qwen3.8-27b-nvfp4-dflash2"
puffin_airgapped = "on"     # every session starts air-gapped
puffin_gmail = false        # never offer Gmail to the agent
```

The same model server also drives Cline, Continue and OpenHands:
`puffin-admin run --agent cline "add type hints to utils.py"`. The web chat and the desktop app are
set up in [Get started](docs/getting-started.md).

</details>

<details>
<summary><b>Build from source</b></summary>

```bash
git clone --recurse-submodules https://github.com/dreamference/puffin.git puffin
cd puffin
python3 -m venv .venv && .venv/bin/pip install -e .
export PATH="$PWD/.venv/bin:$PATH"   # the agent runs puffin-admin, so keep it on PATH

puffin-admin host setup              # swap, kernel settings, out-of-memory guard
puffin-admin server start            # checks the host, downloads and loads the model
puffin-admin codex build             # compiles puffin and links it into ~/.local/bin
cd ~/my-project && puffin
```

`puffin-admin` is the Python package that runs the model server, the web chat and the builds
([command reference](docs/admin.md)); `puffin` and its web and code-index tools are Rust. Tests need
no GPU or Docker: `.venv/bin/python -m pytest tests/`. Design specs are in [`specs/`](specs/README.md),
and [How it works](docs/architecture.md) explains the pieces.

</details>

## Star history

<a href="https://star-history.com/#dreamference/puffin&Date"><img src="https://api.star-history.com/svg?repos=dreamference/puffin&type=Date" alt="Star history of dreamference/puffin" width="600"></a>

## Spread the word

Know someone with a DGX Spark? Send them this page. If you believe code should stay on the desk
it was written on, **[star the repo](https://github.com/dreamference/puffin/stargazers)**: it's how
other GB10 owners find Puffin.
[Share on X](https://x.com/intent/post?text=Private%20AI%20coding%20agent%20that%20runs%20entirely%20on%20my%20DGX%20Spark.%20No%20cloud%2C%20no%20account%2C%20no%20telemetry.&url=https%3A%2F%2Fgithub.com%2Fdreamference%2Fpuffin)
· [Share on LinkedIn](https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fgithub.com%2Fdreamference%2Fpuffin)

Issues and pull requests are welcome, especially anything the egress audit turns up.

## Credits

Puffin began as a fork of the open-source Codex CLI (Apache 2.0) and is growing into the world's
leading confidential local AI software. It also stands on
[SGLang](https://github.com/sgl-project/sglang), [vLLM](https://github.com/vllm-project/vllm),
[Onyx](https://github.com/onyx-dot-app/onyx), [SearXNG](https://github.com/searxng/searxng) and the
[Qwen](https://github.com/QwenLM) models. Thank you to all of them.

## License

Copyright (C) 2026 Dreamference contributors.

Puffin is free software under the **GNU Affero General Public License v3** or later; the full text
is in [`LICENSE`](LICENSE). Section 13 is the clause that matters for a fork: if you run a modified
Puffin and let people use it **over a network**, you must offer them its source.

This covers Puffin's own code. The components it deploys keep their own licences: Codex (Apache
2.0), SGLang and vLLM (Apache 2.0), Onyx (its own terms, including an `ee/` directory that is not
free software and that Puffin leaves switched off), and the model under its own weights licence.

NVIDIA, GB10, DGX and DGX Spark are trademarks of NVIDIA Corporation. Qwen is a trademark of
Alibaba Cloud. Other names are trademarks of their owners. Puffin and Dreamference are not
affiliated with, sponsored by or endorsed by any of them; the names say only what Puffin runs on and
is built from.
