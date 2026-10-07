# `mling-admin` reference

`mling-admin` runs everything around the agent: the model server, the web chat, the desktop app,
models, and the mail commands the agent calls (`gmail`). The agent itself is `mling`, and its web
commands are programs of their own, `mling-search` and `mling-fetch`; see [Terminal agent](mling.md).

!!! note "Generated from the CLI"
    This page is generated from `mling-admin`'s own argument parser by
    `scripts/gen_admin_reference.py`. Run `mling-admin <command> --help` for the same text locally.


## Global options

| Option | Description |
|---|---|
| `--config` | Path to custom Mightling config file (.toml, .yaml or .json) |
| `--agent` | Select primary AI agent runner (default: codex) One of: `codex`, `cline`, `continue`, `openhands`. |
| `--hf-token` | HuggingFace API access token (or set via HF_TOKEN env var) |

## Commands

### `mling-admin init`

Initialize .dreamference project workspace and agent configs.

| Option | Description |
|---|---|
| `--model` | Model name served on vLLM GB10 endpoint. |
| `--vllm-host` | vLLM server URL. |
| `--draft-model` | Speculative decoding draft model name. |
| `--agent` | Primary AI agent runner. One of: `codex`, `cline`, `continue`, `openhands`. |
| `--hf-token` | HuggingFace API access token. |

### `mling-admin run`

Run an autonomous coding task.

| Option | Description |
|---|---|
| `prompt` | Task prompt for AI agent. |
| `--model` | Model name served on vLLM GB10 endpoint. |
| `--draft-model` | Speculative decoding draft model name. |
| `--agent` | Primary AI agent runner. One of: `codex`, `cline`, `continue`, `openhands`. |
| `--hf-token` | HuggingFace API access token. |
| `--debug` | Enable verbose debug output. |
| `--cave` | Enable Cave Mode strict prompt (no explanations, only commands/code) |

### `mling-admin status`

Display local GB10 hardware & agent connection status.

### `mling-admin index`

Index codebase AST & TF-IDF vector context.

| Option | Description |
|---|---|
| `--dir` | Directory to index. |
| `--force` | Force reindexing. |

### `mling-admin mcp`

Run stdio MCP server for JetBrains & VS Code extensions.

### `mling-admin model`

Model operations.

#### `mling-admin model list`

List available model names and HuggingFace repos.

#### `mling-admin model download`

Pre-download LLM & draft model weights into local HuggingFace cache.

| Option | Description |
|---|---|
| `--model` | Specific model to pre-download. |
| `--all` | Pre-download all qualified GB10 models. |
| `--tensorize`, `--no-tensorize` | Auto-convert model to tensorize format after download (default: False) |

### `mling-admin main-model`

Main model operations.

#### `mling-admin main-model set`

Set the main model.

| Option | Description |
|---|---|
| `model_name` | Name of the model to set as main. |
| `--no-onyx` | Skip re-registering the model with a running Onyx deployment. |

#### `mling-admin main-model inspect`

Inspect the currently running main model by running sample prompts.

| Option | Description |
|---|---|
| `--deep` | Also report quantization map, KV geometry, sampling provenance, graph coverage and per-workload speculative acceptance (sends extra requests; slower) |

### `mling-admin clear-tensorize-cache`

Clear local tensorizer model cache only.

### `mling-admin clear`

Clear operations.

#### `mling-admin clear model-cache`

Clear local HuggingFace and tensorizer model caches.

#### `mling-admin clear tensorize-cache`

Clear local tensorizer model cache only.

### `mling-admin endpoints`

Print the model server's endpoints (the standard /v1 API) and credentials.

### `mling-admin server`

Manage the vLLM server container (start, stop, remove).

#### `mling-admin server start`

Launch local vLLM server optimized for GB10 unified memory.

| Option | Description |
|---|---|
| `--model` | Model name to serve (default: the configured main model; examples: qwen3.8-27b-nvfp4-dflash2, llama-3.3-70b) |
| `--port` | Port for the model server's /v1 API. |
| `--quantization` | Quantization method (int8, fp8, awq) |
| `--draft-model` | Speculative decoding draft model (e.g. qwen2.5-coder-1.5b) |
| `--num-speculative-tokens` | Number of speculative tokens to propose. |
| `--hf-token` | HuggingFace API access token. |
| `--num-scheduler-steps` | Multi-step scheduling iterations per step. |
| `--attention-backend` | Attention backend (FLASHINFER, FLASH_ATTN, auto) |
| `--kv-cache-dtype` | KV cache precision (auto, fp8) |
| `--api-key` | API key for the model server's /v1 API (optional; not set by default) |
| `--enable-auto-tool-choice` | Enable automatic tool choice for function calling (default: enabled) |
| `--tool-call-parser` | Tool call parser name (default: from the model's registry recipe, e.g. hermes, qwen3_xml) |
| `--reasoning-parser` | Reasoning-channel parser for models that emit separate thinking output (e.g. qwen3) |
| `--moe-backend` | Mixture-of-experts kernel backend (e.g. marlin, flashinfer-b12x); GB10 requires an SM121-safe choice. |
| `--max-num-batched-tokens` | Max tokens per batch for chunked prefill (GB10 optimization) |
| `--guided-decoding-backend` | Structured-outputs backend for deterministic JSON/tool calls (auto, xgrammar, guidance). Unset leaves vLLM's own default. |
| `--tensorize`, `--no-tensorize` | Save and load model in tensorize (.tensors) format (default: False) |
| `--docker-image` | Docker image for vLLM. Unset uses the model's own docker_image recipe entry, then the pinned default. |

#### `mling-admin server stop`

Stop the running model server.

| Option | Description |
|---|---|
| `--port` | Port of the server to stop. |

#### `mling-admin server remove`

Remove the model server's container.

| Option | Description |
|---|---|
| `--port` | Port of the server to remove. |

#### `mling-admin server logs`

Tail the vLLM Docker container logs.

| Option | Description |
|---|---|
| `--port` | Port of the server to tail logs for. |

### `mling-admin logs`

Tail the vLLM Docker container logs.

| Option | Description |
|---|---|
| `target` | server: vLLM container logs. mcp: the agent's MCP server lifecycle, read from ~/.mightling/logs_2.sqlite, or $CODEX_HOME (the TUI logs there, not to a file) One of: `server`, `mcp`. |
| `--port` | Port of the server to tail logs for. |

### `mling-admin codex`

Build mling and manage its app-server daemon.

#### `mling-admin codex build`

Build mling from the pinned upstream source in codex/ and the patches in codex-patches/.

| Option | Description |
|---|---|
| `--force` | Rebuild even if the installed build is current. |
| `--no-audit` | Do not trace the new build's network use afterwards (`mling-admin audit egress`) |

#### `mling-admin codex start`

Start mling's app-server daemon in the background.

#### `mling-admin codex stop`

Stop mling's app-server daemon.

#### `mling-admin codex test`

Run the upstream test suite on mling's patched tree, except the tests in codex-tests/mightling-skips.toml.

| Option | Description |
|---|---|
| `-E`, `--filter` | nextest filterset to narrow the run to. |
| `--test-threads` | Tests run at once (default 8) |
| `--jobs` | Parallel compile jobs (default 6) |
| `--memory-max` | Memory the run may use (default 24G) |
| `--accept-snapshots` | Rewrite the selected TUI snapshots and keep those that differ from upstream's by the name alone. |

### `mling-admin code`

Install the pinned tools of mling-code's code index.

#### `mling-admin code setup`

Install codebase-memory-mcp, the scip CLI and the language indexers, each checked against its pin.

### `mling-admin host`

Check or apply the host settings a model load and mling's sandbox need (swap, sysctls, earlyoom, sysstat, bubblewrap).

#### `mling-admin host check`

Show what `server start` would refuse over, changing nothing.

#### `mling-admin host setup`

Apply the settings; each command is printed first and sudo asks for your password.

### `mling-admin night`

Run the Night Shift queue overnight (tasks are queued with /night add).

#### `mling-admin night enable`

Install the systemd user timer that runs the queue every night.

| Option | Description |
|---|---|
| `--window` | HH:MM-HH:MM (default: [night] window, 01:00-07:00) |

#### `mling-admin night disable`

Remove the Night Shift timer.

#### `mling-admin night status`

Show the timer, the window and the queue of every repository.

#### `mling-admin night run`

Work through the queue now, until the window ends.

| Option | Description |
|---|---|
| `--until` | HH:MM to stop at (default: the end of the window) |
| `--minutes` | Run for this many minutes instead. |
| `--idle-minutes` | Minutes the model must have been idle first (default 10) |
| `--ignore-open-sessions` | Do not wait for open mling sessions to close (for testing; their requests still pause the run) |

### `mling-admin swe-bench`

Run mling over SWE-bench instances on this machine and grade the patches.

#### `mling-admin swe-bench setup`

Install the harness, download the dataset, build the mling runtime for the instance images.

| Option | Description |
|---|---|
| `--dataset` | verified (default), lite, full, or a HuggingFace id. |
| `--validate` | Also check which instances grade correctly here (pulls their images) |
| `--instances` | With --validate: comma-separated instance ids. |
| `--limit` | With --validate: only the first N instances, sorted by id. |
| `--force` | With --validate: check again instances that already have a result. |

#### `mling-admin swe-bench smoke`

Prove the whole pipeline on five instances; run refuses until this has passed.

| Option | Description |
|---|---|
| `--idle-minutes` | Minutes the model must have been idle first (default 10) |
| `--ignore-open-sessions` | Do not wait for open mling sessions to close (for testing) |

#### `mling-admin swe-bench run`

The agent phase: one mling exec per instance, producing predictions.jsonl.

| Option | Description |
|---|---|
| `--dataset` | verified (default), lite, full, or a HuggingFace id. |
| `--instances` | Comma-separated instance ids. |
| `--subset` | A file of instance ids, one per line. |
| `--limit` | Only the first N selected instances, sorted by id. |
| `--name` | The run's name; an existing run of that name is resumed. |
| `--eval` | Grade the predictions when the agent phase ends. |
| `--remove-images` | With --eval: work one repository at a time and remove its images once it is graded. |
| `--code-index` | universal: index each instance's repository on the host and give the agent mling-code (default off) One of: `off`, `universal`. |
| `--prompt` | The system prompt the agent starts with: default, high-swe, or a custom one in $CODEX_HOME/system-prompts (default: the configured one) |
| `--mask` | on: mask old tool outputs in the agent's requests (context budget spec §4.1; default off) One of: `off`, `on`. |
| `--until` | HH:MM after which no new instance starts. |
| `--idle-minutes` | Minutes the model must have been idle first (default 10) |
| `--ignore-open-sessions` | Do not wait for open mling sessions to close (for testing) |

#### `mling-admin swe-bench eval`

The grading phase: the upstream harness applies each patch and runs the tests.

| Option | Description |
|---|---|
| `run` | The run (default: the latest) |

#### `mling-admin swe-bench report`

Print the resolved rate and what it was measured with.

| Option | Description |
|---|---|
| `run` | The run (default: the latest) |
| `--against` | Compare with this run, instance by instance. |

#### `mling-admin swe-bench status`

Runs, their progress, images and disk.

#### `mling-admin swe-bench clean`

Remove a run's containers and scratch; with --images, the instance images.

| Option | Description |
|---|---|
| `run` | The run (default: every run's containers) |
| `--images` | Also remove the instance images. |

### `mling-admin audit`

Check what a Mightling session does on the network.

#### `mling-admin audit egress`

Trace one real mling session and list every network destination and process, with a verdict.

| Option | Description |
|---|---|
| `--tui` | Trace the full-screen interface on a pseudo-terminal instead of `mling exec` (needs pexpect and pyte) |
| `--prompt` | Prompt for the traced session (default: a one-word reply) |
| `--json` | Also write the full result to $CODEX_HOME/audit/<timestamp>.json. |

### `mling-admin node`

Advertise this machine on the local network so clients find it with no address typed.

#### `mling-admin node enable`

Advertise the node and publish the web UI and web search to the local network.

| Option | Description |
|---|---|
| `--no-web` | Keep the web UI on this machine; clients get mling and web search only. |

#### `mling-admin node disable`

Stop advertising and put the web UI and web search back on this machine only.

#### `mling-admin node status`

Show the node id, what is advertised and published, and what a browse of the network returns; with a name, that paired node's status.

| Option | Description |
|---|---|
| `name` | A paired node: show its `mling-admin status` instead. |

#### `mling-admin node id`

Print this node's id, writing it first if this machine has none yet.

#### `mling-admin node list`

List every node on the local network: its model, its load, and whether it is paired.

#### `mling-admin node add`

Pair with another node over SSH, once, so it can be managed from here.

| Option | Description |
|---|---|
| `name` | The node's name, address or id, as `node list` shows it. |
| `--user` | The account on that node (default: this user's name) |
| `--ssh-port` | That node's SSH port (default 22) |

#### `mling-admin node remove`

Unpair a node: remove the key on both sides.

| Option | Description |
|---|---|
| `name` | The paired node. |

#### `mling-admin node set`

Assign a model to a paired node and start it there.

| Option | Description |
|---|---|
| `name` | The paired node. |
| `--model` | A key of that node's model matrix. |

#### `mling-admin node start`

Start a paired node's model server.

| Option | Description |
|---|---|
| `name` | The paired node. |

#### `mling-admin node stop`

Stop a paired node's model server.

| Option | Description |
|---|---|
| `name` | The paired node. |

#### `mling-admin node run`

Run a command on a paired node, in this repository at HEAD; its changes come back as a branch.

| Option | Description |
|---|---|
| `name` | The paired node. |
| `--memory` | The job's memory cap (default 8G; the node sets the ceiling) |
| `--time` | The job's time limit (default 90m; the node sets the ceiling) |
| `--test` | A command that decides pass or fail, run after the job's own. |
| `--gpu` | Ask for the GPU (nodes refuse this for now) |
| `--setup` | The command that builds the job's environment, run once per lock-file content and kept on the node. |
| `--out` | A folder the job writes that comes back as files (to ~/.mightling/jobs/received/<id>), never as a commit. |
| `--bind` | A path on the node to bind read-only; the node's [node] bindable decides which are allowed (repeatable) |
| `job_command` | -- then the command and its arguments. |

#### `mling-admin node jobs`

List the jobs on a paired node, or on every paired node.

| Option | Description |
|---|---|
| `name` | A paired node (default: all) |

#### `mling-admin node logs`

Show a job's output again, or continue it.

| Option | Description |
|---|---|
| `job` | The job id. |

#### `mling-admin node cancel`

Stop a running job.

| Option | Description |
|---|---|
| `job` | The job id. |

#### `mling-admin node fetch`

Bring a job's result branch into the repository it was sent from, and its --out files.

| Option | Description |
|---|---|
| `job` | The job id. |

#### `mling-admin node sync-model`

Copy a model's files from this machine's cache to a paired node, so it need not download them.

| Option | Description |
|---|---|
| `name` | The paired node. |
| `model` | A key of the model matrix. |
| `--address` | Reach the node at this address instead, such as its QSFP link's. |

#### `mling-admin node job-exec`

(Run inside a job's systemd unit) carry out one job.

| Option | Description |
|---|---|
| `job` | The job id. |

#### `mling-admin node authorize`

(Run by `node add` on the other node) authorise a public key, read from standard input, for node operations only.

#### `mling-admin node serve-job`

(Run by sshd as a paired key's forced command) carry out one node operation.

| Option | Description |
|---|---|
| `--key` | The connecting key's tag. |

### `mling-admin benchmark_server`

Run vLLM serve benchmark using Sonnet dataset.

| Option | Description |
|---|---|
| `--port` | Port of the server to benchmark. |
| `--model` | Model name to benchmark. |
| `--dataset-path` | Path to the sonnet dataset inside the container. Unset probes the known locations for the image the server is running. |
| `--num-prompts` | Number of prompts to benchmark. |
| `--max-concurrency` | Max concurrency for requests. |

### `mling-admin chat`

Manage the Mightling web chat UI (Onyx Lite) backed by local vLLM.

Alias: `onyx`.

#### `mling-admin chat start`

Deploy (or restart) Onyx Lite and wait until it is healthy.

| Option | Description |
|---|---|
| `--no-wait` | Return as soon as containers start. |

#### `mling-admin chat configure`

Point Onyx at the local vLLM model as its default provider.

| Option | Description |
|---|---|
| `--email` | Onyx admin e-mail (registered if no account exists) |
| `--password` | Onyx admin password. |
| `--no-web` | Skip registering SearXNG as Onyx's web search provider. |
| `--no-brand` | Skip rebranding the deployment as Mightling. |
| `--no-voice` | Skip the local Whisper server and the microphone button. |
| `--no-gmail` | Skip the Gmail service and its search tool. |
| `--no-image-search` | Skip the image search sidecar and its tool. |

#### `mling-admin chat google-auth`

Add Google sign-in to the login page, keeping username/password.

| Option | Description |
|---|---|
| `--client-id` | Google OAuth client ID. |
| `--client-secret` | Google OAuth client secret. |

#### `mling-admin chat gmail`

Connect Gmail and give the assistant a mailbox search tool.

#### `mling-admin chat status`

Show Onyx version, containers and health.

#### `mling-admin chat logs`

Show Onyx container logs.

| Option | Description |
|---|---|
| `--follow`, `-f` | Stream new log lines. |

#### `mling-admin chat stop`

Stop the Onyx containers, keeping their data.

#### `mling-admin chat uninstall`

Permanently delete the Onyx deployment and all its data.

### `mling-admin desktop`

Mightling desktop app (a native window onto the local deployment).

#### `mling-admin desktop install`

Install the desktop build toolchain (system packages, Rust, Tauri CLI).

#### `mling-admin desktop run`

Open the Mightling desktop window.

#### `mling-admin desktop build`

Build a distributable desktop bundle.

#### `mling-admin desktop status`

Report whether the desktop app can be built and launched.

### `mling-admin gmail`

Search and read connected Gmail accounts (read-only).

#### `mling-admin gmail search`

Search with Gmail query syntax.

| Option | Description |
|---|---|
| `query` | Gmail query, e.g. from:alice newer_than:7d. |
| `-n`, `--max-results` | Messages to return (max 20) |
| `--json` | Emit raw JSON. |

#### `mling-admin gmail read`

Read one message by the id search printed.

| Option | Description |
|---|---|
| `message_id` | Message id exactly as `gmail search` printed it. |
| `--max-chars` | Body characters to print. |
| `--json` | Emit raw JSON. |

#### `mling-admin gmail status`

Show which accounts are connected.

| Option | Description |
|---|---|
| `--json` | Emit raw JSON. |

### `mling-admin searxng`

Manage the local SearXNG search container.

#### `mling-admin searxng start`

Start SearXNG on 127.0.0.1:8888 (recreates one made on Docker's default bridge).

### `mling-admin google`

Manage the local Google service (Gmail, Drive, Calendar).

#### `mling-admin google start`

Start the Google service on 127.0.0.1:8767 (adopts the web UI's if it exists).

#### `mling-admin google stop`

Remove the Google service container; connected accounts stay stored.

#### `mling-admin google status`

Show whether it runs and which accounts hold which apps.

### `mling-admin web`

Launch Web Canvas UI interactive pair-programming pane.

| Option | Description |
|---|---|
| `--port` | Port for Web Canvas UI. |
