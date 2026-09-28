# `puffin-admin` reference

`puffin-admin` runs everything around the agent: the model server, the web chat, the desktop app,
models, and the helper commands the agent calls (`search`, `fetch`, `gmail`). The agent itself is
`puffin`; see [Terminal agent](puffin.md).

!!! note "Generated from the CLI"
    This page is generated from `puffin-admin`'s own argument parser by
    `scripts/gen_admin_reference.py`. Run `puffin-admin <command> --help` for the same text locally.


## Global options

| Option | Description |
|---|---|
| `--config` | Path to custom Dreamference config file (.toml, .yaml or .json) |
| `--sandbox` | Rootless container sandbox isolation engine. One of: `none`, `apptainer`, `podman`, `docker`. |
| `--agent` | Select primary AI agent runner (default: codex) One of: `goose`, `cline`, `aider`, `continue`, `openhands`, `codex`. |
| `--hf-token` | HuggingFace API access token (or set via HF_TOKEN env var) |

## Commands

### `puffin-admin init`

Initialize .dreamference project workspace and agent configs.

| Option | Description |
|---|---|
| `--model` | Model name served on vLLM GB10 endpoint. |
| `--vllm-host` | vLLM server URL. |
| `--draft-model` | Speculative decoding draft model name. |
| `--sandbox` | Rootless container sandbox engine. One of: `none`, `apptainer`, `podman`, `docker`. |
| `--agent` | Primary AI agent runner. One of: `goose`, `cline`, `aider`, `continue`, `openhands`, `codex`. |
| `--hf-token` | HuggingFace API access token. |

### `puffin-admin run`

Run an autonomous coding task.

| Option | Description |
|---|---|
| `prompt` | Task prompt for AI agent. |
| `--model` | Model name served on vLLM GB10 endpoint. |
| `--draft-model` | Speculative decoding draft model name. |
| `--sandbox` | Rootless container sandbox engine. One of: `none`, `apptainer`, `podman`, `docker`. |
| `--agent` | Primary AI agent runner. One of: `goose`, `cline`, `aider`, `continue`, `openhands`, `codex`. |
| `--hf-token` | HuggingFace API access token. |
| `--debug` | Enable verbose debug output. |
| `--cave` | Enable Cave Mode strict prompt (no explanations, only commands/code) |

### `puffin-admin status`

Display local GB10 hardware & agent connection status.

### `puffin-admin index`

Index codebase AST & TF-IDF vector context.

| Option | Description |
|---|---|
| `--dir` | Directory to index. |
| `--force` | Force reindexing. |

### `puffin-admin mcp`

Run stdio MCP server for JetBrains & VS Code extensions.

### `puffin-admin model`

Model operations.

#### `puffin-admin model list`

List available model names and HuggingFace repos.

#### `puffin-admin model download`

Pre-download LLM & draft model weights into local HuggingFace cache.

| Option | Description |
|---|---|
| `--model` | Specific model to pre-download. |
| `--all` | Pre-download all qualified GB10 models. |
| `--tensorize`, `--no-tensorize` | Auto-convert model to tensorize format after download (default: False) |

### `puffin-admin main-model`

Main model operations.

#### `puffin-admin main-model set`

Set the main model.

| Option | Description |
|---|---|
| `model_name` | Name of the model to set as main. |
| `--no-onyx` | Skip re-registering the model with a running Onyx deployment. |

#### `puffin-admin main-model inspect`

Inspect the currently running main model by running sample prompts.

| Option | Description |
|---|---|
| `--deep` | Also report quantization map, KV geometry, sampling provenance, graph coverage and per-workload speculative acceptance (sends extra requests; slower) |

### `puffin-admin diffusion-model`

Diffusion model operations.

#### `puffin-admin diffusion-model set`

Set the diffusion model served beside the main one.

| Option | Description |
|---|---|
| `model_name` | Name of the diffusion model to set. |

### `puffin-admin clear-tensorize-cache`

Clear local tensorizer model cache only.

### `puffin-admin clear`

Clear operations.

#### `puffin-admin clear model-cache`

Clear local HuggingFace and tensorizer model caches.

#### `puffin-admin clear tensorize-cache`

Clear local tensorizer model cache only.

### `puffin-admin endpoints`

Print all available vLLM/OpenAI-compatible endpoints and credentials.

### `puffin-admin server`

Manage the vLLM server container (start, stop, remove).

#### `puffin-admin server start`

Launch local vLLM server optimized for GB10 unified memory.

| Option | Description |
|---|---|
| `--model` | Model name to serve (default: the configured main model; examples: qwen3.5-122b-a10b-hybrid-dflash, llama-3.3-70b) |
| `--port` | Port to expose OpenAI API endpoint. |
| `--quantization` | Quantization method (int8, fp8, awq) |
| `--draft-model` | Speculative decoding draft model (e.g. qwen2.5-coder-1.5b) |
| `--num-speculative-tokens` | Number of speculative tokens to propose. |
| `--hf-token` | HuggingFace API access token. |
| `--num-scheduler-steps` | Multi-step scheduling iterations per step. |
| `--attention-backend` | Attention backend (FLASHINFER, FLASH_ATTN, auto) |
| `--kv-cache-dtype` | KV cache precision (auto, fp8) |
| `--api-key` | OpenAI-compatible API key (optional; not set by default) |
| `--enable-auto-tool-choice` | Enable automatic tool choice for function calling (default: enabled) |
| `--tool-call-parser` | Tool call parser name (default: from the model's registry recipe, e.g. hermes, qwen3_xml) |
| `--reasoning-parser` | Reasoning-channel parser for models that emit separate thinking output (e.g. qwen3) |
| `--moe-backend` | Mixture-of-experts kernel backend (e.g. marlin, flashinfer-b12x); GB10 requires an SM121-safe choice. |
| `--max-num-batched-tokens` | Max tokens per batch for chunked prefill (GB10 optimization) |
| `--guided-decoding-backend` | Structured-outputs backend for deterministic JSON/tool calls (auto, xgrammar, guidance). Unset leaves vLLM's own default. |
| `--tensorize`, `--no-tensorize` | Save and load model in tensorize (.tensors) format (default: False) |
| `--docker-image` | Docker image for vLLM. Unset uses the model's own docker_image recipe entry, then the pinned default. |
| `--diffusion-model` | Diffusion model to serve beside the main one (default: the configured diffusion model, tiny-a2d-coder-0.5b-diffusion) |
| `--diffusion-port` | Port for the diffusion sidecar's OpenAI endpoint. |
| `--no-diffusion` | Skip starting the diffusion sidecar. |

#### `puffin-admin server stop`

Stop the running vLLM and diffusion Docker containers.

| Option | Description |
|---|---|
| `--port` | Port of the server to stop. |
| `--diffusion-port` | Port of the diffusion sidecar to stop. |

#### `puffin-admin server remove`

Remove the vLLM and diffusion Docker containers.

| Option | Description |
|---|---|
| `--port` | Port of the server to remove. |
| `--diffusion-port` | Port of the diffusion sidecar to remove. |

#### `puffin-admin server logs`

Tail the vLLM Docker container logs.

| Option | Description |
|---|---|
| `--port` | Port of the server to tail logs for. |

### `puffin-admin logs`

Tail the vLLM Docker container logs.

| Option | Description |
|---|---|
| `target` | server: vLLM container logs. mcp: Codex MCP server lifecycle, read from ~/.codex/logs_2.sqlite (the TUI logs there, not to a file) One of: `server`, `mcp`. |
| `--port` | Port of the server to tail logs for. |

### `puffin-admin codex`

Build puffin and manage its app-server daemon.

#### `puffin-admin codex build`

Build the Puffin-branded Codex from the codex submodule and codex-patches/.

| Option | Description |
|---|---|
| `--force` | Rebuild even if the installed build is current. |

#### `puffin-admin codex start`

Start puffin's app-server daemon in the background.

#### `puffin-admin codex stop`

Stop puffin's app-server daemon.

### `puffin-admin benchmark_server`

Run vLLM serve benchmark using Sonnet dataset.

| Option | Description |
|---|---|
| `--port` | Port of the server to benchmark. |
| `--model` | Model name to benchmark. |
| `--dataset-path` | Path to the sonnet dataset inside the container. Unset probes the known locations for the image the server is running. |
| `--num-prompts` | Number of prompts to benchmark. |
| `--max-concurrency` | Max concurrency for requests. |

### `puffin-admin puffin`

Manage the Puffin web chat UI (Onyx Lite) backed by local vLLM.

Alias: `onyx`.

#### `puffin-admin puffin start`

Deploy (or restart) Onyx Lite and wait until it is healthy.

| Option | Description |
|---|---|
| `--no-wait` | Return as soon as containers start. |

#### `puffin-admin puffin configure`

Point Onyx at the local vLLM model as its default provider.

| Option | Description |
|---|---|
| `--email` | Onyx admin e-mail (registered if no account exists) |
| `--password` | Onyx admin password. |
| `--no-web` | Skip registering SearXNG as Onyx's web search provider. |
| `--no-brand` | Skip rebranding the deployment as Puffin. |
| `--no-voice` | Skip the local Whisper server and the microphone button. |
| `--no-gmail` | Skip the Gmail service and its search tool. |
| `--no-image-search` | Skip the image search sidecar and its tool. |

#### `puffin-admin puffin google-auth`

Add Google sign-in to the login page, keeping username/password.

| Option | Description |
|---|---|
| `--client-id` | Google OAuth client ID. |
| `--client-secret` | Google OAuth client secret. |

#### `puffin-admin puffin gmail`

Connect Gmail and give the assistant a mailbox search tool.

| Option | Description |
|---|---|
| `--refresh` | ==SUPPRESS==. |

#### `puffin-admin puffin status`

Show Onyx version, containers and health.

#### `puffin-admin puffin logs`

Show Onyx container logs.

| Option | Description |
|---|---|
| `--follow`, `-f` | Stream new log lines. |

#### `puffin-admin puffin stop`

Stop the Onyx containers, keeping their data.

#### `puffin-admin puffin uninstall`

Permanently delete the Onyx deployment and all its data.

### `puffin-admin desktop`

Puffin desktop app (a native window onto the local deployment).

#### `puffin-admin desktop install`

Install the desktop build toolchain (system packages, Rust, Tauri CLI).

#### `puffin-admin desktop run`

Open the Puffin desktop window.

#### `puffin-admin desktop build`

Build a distributable desktop bundle.

#### `puffin-admin desktop status`

Report whether the desktop app can be built and launched.

### `puffin-admin search`

Search the web via the local SearXNG instance.

| Option | Description |
|---|---|
| `query` | Search terms. |
| `-n`, `--max-results` | Results to return. |
| `--json` | Emit raw JSON. |

### `puffin-admin fetch`

Fetch a URL and print its readable text.

| Option | Description |
|---|---|
| `url` | Absolute http(s) URL. |
| `--max-chars` | Characters to return. |

### `puffin-admin gmail`

Search and read connected Gmail accounts (read-only).

#### `puffin-admin gmail search`

Search with Gmail query syntax.

| Option | Description |
|---|---|
| `query` | Gmail query, e.g. from:alice newer_than:7d. |
| `-n`, `--max-results` | Messages to return (max 20) |
| `--json` | Emit raw JSON. |

#### `puffin-admin gmail read`

Read one message by the id search printed.

| Option | Description |
|---|---|
| `message_id` | Message id exactly as `gmail search` printed it. |
| `--max-chars` | Body characters to print. |
| `--json` | Emit raw JSON. |

#### `puffin-admin gmail status`

Show which accounts are connected.

| Option | Description |
|---|---|
| `--json` | Emit raw JSON. |

### `puffin-admin web`

Launch Web Canvas UI interactive pair-programming pane.

| Option | Description |
|---|---|
| `--port` | Port for Web Canvas UI. |
