# Puffin CLI Reference

> **Version:** 1.2.0 (`setup.py`)
> **Subject:** Command Suite, Subcommands, Configuration, Environment Variables
> **Checked against the code:** 2026-10-02 (`dreamference/cli/dreamference_cli_controller.py`, `build_parser()`: a script walked the parser and found every subcommand and every visible `--option` in this document); the subcommand list walked again on 2026-10-03

---

## Table of Contents

- [1. CLI Overview](#1-cli-overview)
- [2. Global Options](#2-global-options)
- [3. Subcommand Summary](#3-subcommand-summary)
- [4. Detailed Command Reference](#4-detailed-command-reference)
- [5. Configuration Hierarchy](#5-configuration-hierarchy)
- [6. Environment Variables](#6-environment-variables)

---

## 1. CLI Overview

**Entry points** (`setup.py` `console_scripts`):

- `puffin-admin`: the administration CLI (`dreamference.cli:main`, controller `DreamferenceCLIController` in `dreamference/cli/`). Everything in this document.
- `puffin`: **not** a Python entry point. It is the Rust binary built by `puffin-admin codex build`: Codex with Puffin's branding and launcher compiled in, linked at `~/.local/bin/puffin`. It takes Codex's command line, plus five subcommands the launcher answers itself before Codex parses anything (`puffin app`, `puffin night …`, `puffin airgapped [default <level>]`, `puffin node list|use|forget`, `puffin prompt [list|show [<name>]|use <name>]`, which chooses the system prompt new sessions get: `default` or `high-swe`, `DREAMFERENCE_PUFFIN_PROMPT` or `puffin_prompt`, see `DREAMFERENCE_PUFFIN_PROMPT.md`) and `puffin update`, a subcommand patch `0008` adds to Codex's own parser. See `DREAMFERENCE_PUFFIN_CODEX.md`.

There is no `chat` subcommand any more (removed 2026-09-28). The interactive agent is `puffin`. The other agents (Cline, Continue, OpenHands) are reachable through `puffin-admin run "…" --agent …`.

**Framework:** argparse + Rich terminal UI. Parsing is strict: every command rejects unknown arguments.

**Commands:**

- **Setup:** `init`, `host {check,setup}`, `model {list,download}`, `main-model {set,inspect}`, `diffusion-model {set}` (absent while diffusion is switched off, §4.2), `clear {model-cache,tensorize-cache}`
- **Agents:** `run`, `codex {build,start,stop,test}`, `night {enable,disable,status,run}`
- **Measurement and checks:** `swe-bench {setup,smoke,run,eval,report,status,clean}`, `audit {egress}`
- **Model server:** `server {start,stop,remove,logs}`, `logs [server|mcp]`, `endpoints`, `benchmark_server`, `node {enable,disable,status,id,list,add,remove,set,start,stop,sync-model,run,jobs,logs,cancel,fetch}` (plus `authorize`, `serve-job` and `job-exec`, which a person does not type, §4.25)
- **Web UI and desktop:** `puffin {start,configure,google-auth,gmail,status,logs,stop,uninstall}` (alias `onyx`), `desktop {install,run,build,status}`
- **Agent tools:** `gmail {search,read,status}`, `searxng start`, `code setup`; search and fetch are commands of their own, `puffin-search` and `puffin-fetch` (§4.16), and so is the code index, `puffin-code` (§4.22)
- **Context and IDE:** `index`, `mcp`, `web`, `status`

---

## 2. Global Options

These options come before the subcommand (`puffin-admin --agent cline run "…"`). `init` and `run` also accept most of them after the subcommand.

| Flag | Type | Description |
| :--- | :--- | :--- |
| `--config PATH` | Path | Custom Puffin config file |
| `--agent {codex,cline,continue,openhands}` | Choice | Agent runner (default `codex`) |
| `--hf-token TOKEN` | String | HuggingFace token (else `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN`) |

`--debug` and `--cave` belong to `run`; they are not global.

---

## 3. Subcommand Summary

| Subcommand | Description | Key Args |
| :--- | :--- | :--- |
| **`init`** | Download weights, write the config, force a workspace re-index | `[--model] [--vllm-host] [--draft-model] [--agent] [--hf-token]` |
| **`run`** | Run one task with the selected agent | `PROMPT [--model] [--draft-model] [--agent] [--hf-token] [--debug] [--cave]` |
| **`status`** | Hardware, vLLM and agent, and context-index panels | — |
| **`index`** | AST + FTS5 + TF-IDF + embedding workspace index | `[--dir PATH] [--force]` |
| **`mcp`** | stdio MCP server for JetBrains / VS Code | — |
| **`web`** | Web Canvas status page | `[--port 8501]` |
| **`model list`** | List the model matrix | — |
| **`model download`** | Pre-download weights to the HF cache | `[--model] [--all] [--tensorize/--no-tensorize]` |
| **`main-model set`** | Pin the main model in the config | `MODEL [--no-onyx]` |
| **`main-model inspect`** | Probe the running main model | `[--deep]` |
| **`diffusion-model set`** | Pin the diffusion model served beside the main one | `MODEL` |
| **`clear model-cache`** | Delete the HF and `~/.cache/dreamference` model caches (see §4.17) | — |
| **`clear tensorize-cache`** | Delete the tensorizer cache under `~/.cache/dreamference` (see §4.18) | — |
| **`endpoints`** | Print endpoints and credentials | — |
| **`server start`** | Launch vLLM, plus the diffusion sidecar | many; see §4.6 |
| **`server stop` / `remove`** | Stop / remove the vLLM and diffusion containers | `[--port 8000] [--diffusion-port 8001]` |
| **`server logs`** / **`logs [server]`** | Tail the vLLM container | `[--port 8000]` |
| **`logs mcp`** | Codex MCP lifecycle lines from `~/.puffin/logs_2.sqlite` (or `$CODEX_HOME`) | — |
| **`benchmark_server`** | `vllm bench serve` on the Sonnet dataset | `[--port] [--model] [--dataset-path] [--num-prompts 8] [--max-concurrency 1]` |
| **`codex build`** | Build `puffin` from the `codex/` submodule and `codex-patches/` | `[--force]` |
| **`codex start` / `stop`** | Start / stop the Codex app-server daemon using `puffin` | — |
| **`codex test`** | Run Codex's own test suite on Puffin's patched tree, except the tests in `codex-tests/puffin-skips.toml` | `[-E FILTER] [--test-threads 8] [--jobs 6] [--memory-max 24G] [--accept-snapshots]` |
| **`code setup`** | Install the pinned tools the code index (`puffin-code`) runs | — |
| **`night …`** | Night Shift: the timer and the overnight run of the `/night` queue | `enable [--window]`, `disable`, `status`, `run [--until] [--minutes] [--idle-minutes] [--ignore-open-sessions]`; see §4.21 |
| **`swe-bench …`** | Run `puffin` over SWE-bench instances and have the upstream harness grade the patches | `setup`, `smoke`, `run`, `eval`, `report`, `status`, `clean`; see §4.23 |
| **`audit egress`** | Trace one real `puffin` session and list every network destination and process, with a verdict | `[--prompt P] [--json]`; see §4.24 |
| **`node …`** | Advertise this machine on the local network so clients find it with no address typed; list other nodes and, once paired over SSH, manage them, copy a model to them and run jobs on them | `enable [--no-web]`, `disable`, `status [NAME]`, `list`, `add NAME`, `remove NAME`, `set NAME --model M`, `start NAME`, `stop NAME`, `sync-model NAME MODEL`, `run NAME … -- CMD`, `jobs`, `logs`, `cancel`, `fetch`; see §4.25 |
| **`host …`** | The host settings a model load is refused without: report them, or apply them with sudo | `check`, `setup`; see §4.26 |
| **`puffin …`** (`onyx …`) | Onyx Lite web UI lifecycle | see §4.19 |
| **`desktop …`** | Tauri desktop window (`puffin-app`) | `install`, `run`, `build`, `status` |
| **`searxng start`** | Start the SearXNG container on `127.0.0.1:8888`, on the network `dreamference-sidecars`; replaces one made on Docker's default bridge ([DOCKER §6](./DREAMFERENCE_DOCKER.md)) | — |
| **`gmail …`** | Read-only Gmail search and read | `search QUERY [-n 10] [--json]`, `read ID [--max-chars 8000] [--json]`, `status [--json]` |

A top-level `clear-tensorize-cache` subcommand is the older spelling of `clear tensorize-cache` and does the same (until 2026-09-29 it parsed and did nothing).

---

## 4. Detailed Command Reference

### 4.1. `puffin-admin init`

```bash
puffin-admin init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST] [--agent …] [--hf-token …]
```

**Behaviour:**
1. Downloads the main (and draft) weights into the HF cache, tensorizing them if `use_tensorizer` is set.
2. Writes a minimal `dreamference.toml` via `config_generator.generate_default_init_config`, at the path `ConfigPathResolver` resolves.
3. Forces a full workspace re-index (`ContextEngine.index_workspace(force_reindex=True)`).

---

### 4.2. `puffin-admin main-model` / `diffusion-model`

> **The diffusion model is switched off since 2026-10-03** (`DIFFUSION_ENABLED` in `hardware/model_matrix_registry.py`). While it is off, `diffusion-model` is not a command, `server start` starts no sidecar and removes a leftover one without a word, `server stop`/`remove` remove a leftover too, `endpoints` prints no diffusion URL, and `model list`/`model download` leave the diffusion model out. The `--diffusion-*` flags are accepted with their help suppressed. Everything about the sidecar in this document describes it switched on.

```bash
puffin-admin main-model set MODEL [--no-onyx]
puffin-admin main-model inspect [--deep]
puffin-admin diffusion-model set MODEL
```

- **`main-model set`:** writes the model to `dreamference.toml`, which pins it. Unless `--no-onyx` is given, it also re-registers the model with a running Onyx deployment. It refuses a diffusion checkpoint.
- **`diffusion-model set`:** the counterpart for the diffusion sidecar. It refuses a model vLLM can serve.
- **`main-model inspect`:** probes the running model: tool calling, JSON mode, reasoning tags, streaming TTFT, latency. `--deep` also reports the quantization map, KV geometry, sampling provenance, graph coverage and per-workload speculative acceptance. That sends extra requests.

---

### 4.3. `puffin-admin run`

```bash
puffin-admin run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--agent …] [--hf-token …] [--debug] [--cave]
```

**Behaviour:** dispatches on `config.agent_runner` to one of `CodexRunner` (also for any unrecognised value), `ClineRunner`, `ContinueRunner` or `OpenHandsRunner`, then calls `run_session(prompt=…, debug=…)`.

- **Codex (default):**
  - builds `puffin` if it is missing or stale;
  - runs `puffin "PROMPT"`, passing the vLLM host as `DREAMFERENCE_VLLM_HOST`;
  - with `--debug`, sets `RUST_LOG=codex_mcp=trace,codex_core=debug,codex_app_server=debug,info`.
- **Cline:** prints the prompt and opens VS Code.
- **Continue / OpenHands:** launch their UI; the prompt is unused.
- **`--cave`:** injects the terse Cave Mode prompt into `.clinerules` for Cline.

---

### 4.4. `puffin-admin status`

**Panels:**

1. **NVIDIA GB10 Hardware Status:** GB10 qualification, GPU name, driver version, total / used / available unified memory, architecture.
2. **vLLM & Agent Status:**
   - vLLM health and served models;
   - active agent runner (default CODEX), configured model, tensorize status, draft model;
   - HF token presence;
   - prefix caching / chunked prefill, multi-step scheduling, KV cache dtype (`from model recipe` unless overridden), tool-call parser;
   - install state of each agent (Cline, Continue, OpenHands, Codex);
   - the Puffin config path.
3. **Context Engine Index Status:** indexed files, AST symbols, index path, SQLite store path. Shown only when an index exists.

---

### 4.5. `puffin-admin index`

```bash
puffin-admin index [--dir PATH] [--force]
```

Indexes the workspace, the current directory unless `--dir` is given, with Python `ast` symbols, FTS5, TF-IDF and `nomic-embed-text-v1.5` embeddings. It writes `.dreamference/context_index.json` and `.dreamference/context.db` (SQLite: FTS5, and the embeddings as plain float32 blobs; sqlite-vec is not used). It refuses to run while a Night Shift run holds its lock. See `DREAMFERENCE_CONTEXT.md`.

---

### 4.6. `puffin-admin server start`

```bash
puffin-admin server start [--model MODEL] [--port 8000] [--quantization Q] [--draft-model D] [--num-speculative-tokens N]
  [--hf-token T] [--num-scheduler-steps N] [--attention-backend B] [--kv-cache-dtype D] [--api-key K]
  [--enable-auto-tool-choice] [--tool-call-parser P] [--reasoning-parser P] [--moe-backend B]
  [--max-num-batched-tokens N] [--guided-decoding-backend B] [--tensorize/--no-tensorize]
  [--docker-image IMG] [--diffusion-model M] [--diffusion-port 8001] [--no-diffusion]
```

**Behaviour:**
1. Refuses while a Night Shift run holds its lock (§4.21), then runs the host-safety pre-flight (`check_host_safety`).
2. Starts the diffusion sidecar `dreamference-diffusion-<diffusion-port>` **first**, unless `--no-diffusion`, so that vLLM's free-memory check accounts for it.
3. Starts vLLM in Docker, under the PSI memory-pressure watchdog. It streams logs and memory until the health check passes, then exits, leaving the server running.

Per-model flags come from the model matrix's `launch_overrides`. Options given here override them, and anything unset falls back to the recipe. `--docker-image` overrides the model's pinned image.

**Speculation.** `--draft-model` and `--num-speculative-tokens` are turned into `--speculative-config` JSON by `VLLMServerManager.resolve_speculative_config()`, never into separate flags: vLLM 0.2x has no `--speculative-model` or `--num-speculative-tokens`. The depth passed is the config's resolved value (flag > `DREAMFERENCE_SPECULATIVE_TOKENS` > file > default), and it applies only together with `--draft-model`; without one, the recipe's speculative config is used exactly. See `DREAMFERENCE_INFERENCE.md` §3.

**Docker command** (`VLLMServerManager.start_server`):
```bash
docker run --ipc=host --network host --restart unless-stopped --name dreamference-vllm-<port> --gpus all \
  --cpus=<limit> --memory=<N>g --memory-swap=<N>g --oom-score-adj=800 \
  -v <$HF_HOME or ~/.cache/huggingface>:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 [-e HF_TOKEN=<token>] [recipe env vars] \
  -e VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm -e CUTE_DSL_ARCH=sm_121a -e VLLM_LOGGING_LEVEL=DEBUG \
  -e VLLM_DEBUG_LOG_API_SERVER_RESPONSE=1 -e VLLM_DEBUG_LOG_API_SERVER_REQUEST=1 \
  --entrypoint vllm <image> serve <hf_repo> [vllm flags]
```

The line above is the vLLM form. A model whose recipe names `engine: sglang` (the default model does) gets the same `docker run` prefix and mounts, and `SGLangLaunchBuilder` builds what follows the image (`DREAMFERENCE_INFERENCE.md`).

The container's memory limit is derived from the model's `gpu_memory_utilization` plus headroom, capped below total memory by a host reserve. `--memory-swap` equals `--memory`, so the container cannot swap.

---

### 4.7. `puffin-admin server stop` / `remove`

```bash
puffin-admin server stop   [--port 8000] [--diffusion-port 8001]
puffin-admin server remove [--port 8000] [--diffusion-port 8001]
```

- `stop` runs `docker stop` on both `dreamference-vllm-<port>` and `dreamference-diffusion-<diffusion-port>`.
- `remove` runs `docker rm -f` on both.

Both are safe no-ops when the containers don't exist.

---

### 4.8. `puffin-admin logs` / `server logs`

```bash
puffin-admin logs [server|mcp] [--port 8000]
puffin-admin server logs [--port 8000]
```

- **`logs`, `logs server` and `server logs`:** tail `dreamference-vllm-<port>` (`show_request_logs`).
- **`logs mcp`:** reads the Codex TUI's tracing database `~/.puffin/logs_2.sqlite` (`$CODEX_HOME` if set; not upstream Codex's `~/.codex`) read-only, and prints MCP server lifecycle lines. The TUI logs there, not to a file. Record them with `RUST_LOG=codex_mcp=trace puffin`.

---

### 4.9. `puffin-admin benchmark_server`

```bash
puffin-admin benchmark_server [--port 8000] [--model qwen3.8-27b-nvfp4-dflash2] [--dataset-path P] [--num-prompts 8] [--max-concurrency 1]
```

Runs vLLM's serving benchmark inside the running container, on the Sonnet dataset. The dataset is embedded in `sonnet_dataset.py` and staged at `/tmp/dreamference-sonnet.txt`. When `--dataset-path` is unset, the known in-image locations are probed first.

---

### 4.10. `puffin-admin mcp`

stdio JSON-RPC MCP server (`serverInfo.name` `dreamference-mcp-server`) for JetBrains and VS Code. Its tools (`mcp_tool_registry.py`):

- `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`: these read and write the in-process `IDEState`, which is empty unless an IDE companion populates it.
- `ide_apply_diff`: always answers `status: not_applied` and tells the agent to edit the file directly. Nothing in the server writes files. Until 2026-09-29 it answered `success`, so an agent carried on as though its edit had landed.
- `web_search`, `web_fetch`: `WebTools`, through the local SearXNG instance and direct HTTP.
- `workspace_search_code`: `ContextEngine.search_code`.

Protocol handling:
- methods: `initialize`, `ping`, `tools/list`, `tools/call`;
- notifications (no `id`) are never answered;
- malformed JSON gets `-32700` with a null id;
- an exception inside a method gets `-32603` carrying the request's id.

Until 2026-09-29 `notifications/initialized` drew a `-32601` reply, and a failing tool was reported as a parse error with no id, so the client never got its answer.

It is **not** registered with `puffin`; see `DREAMFERENCE_PUFFIN_CODEX.md` for why.

---

### 4.11. `puffin-admin endpoints`

It prints two tables:

1. **Available endpoints (Standard /v1):**
   - `/v1/models` (GET)
   - `/v1/chat/completions` (POST)
   - `/v1/completions` (POST)
   - `/v1/embeddings` (POST, if supported)
   - `/health` (GET)
2. **Credentials:**
   - the base URL on localhost and on the LAN IP;
   - the diffusion model URL (`http://localhost:8001/v1`), only while diffusion is switched on;
   - the API key, which is optional and set only by `server start --api-key`;
   - the `Authorization: Bearer <key>` header format.

Clients address the model by its full HF repo id (e.g. `RadixArk/Qwen3.8-27B-NVFP4`, the default model's).

---

### 4.12. `puffin-admin web`

```bash
puffin-admin web [--port 8501]
```

Serves the Web Canvas page (`web_canvas.py`) on `127.0.0.1:<port>` (until 2026-09-29, `0.0.0.0`) with `socketserver.TCPServer`: a status page with a live unified-memory gauge fed by `GET /api/status` (`{hardware, vllm, context}`).

---

### 4.13. `puffin-admin model list`

Lists `ModelMatrixRegistry.MATRIX`: names, HF repos, compatibility.

### 4.14. `puffin-admin model download`

```bash
puffin-admin model download [--model MODEL] [--all] [--tensorize/--no-tensorize]
```

- **Without `--all`:** downloads `--model` or the configured model, plus its draft model if any.
- **With `--all`:** every `compatible_gb10` matrix entry.
- **`--tensorize`:** converts to tensorizer format after the download. The default is off.

---

### 4.15. `puffin-admin codex`

```bash
puffin-admin codex build [--force]
puffin-admin codex start
puffin-admin codex stop
puffin-admin codex test [-E FILTER] [--test-threads 8] [--jobs 6] [--memory-max 24G] [--accept-snapshots]
```

- **`build`:** builds `puffin` and `codex-code-mode-host` with `CodexBrandedBuilder`, the web commands (`puffin-search`, `puffin-fetch`) and `puffin-code`, and links them into `~/.local/bin`. It refuses while a Night Shift run holds its lock. It skips the build when the recorded build key is current, unless `--force`. See `DREAMFERENCE_PUFFIN_CODEX.md`.
- **`start` / `stop`:** run `puffin app-server daemon start|stop`, building `puffin` first if it is missing. The parser's help text calls this the "Codex comic server"; that is a typo in the help, and the command drives the app-server daemon.
- **`test`:** runs Codex's own tests with `cargo-nextest` on the patched export, inside a memory-capped scope (`--memory-max`). `-E`/`--filter` takes a nextest filterset; `--accept-snapshots` rewrites the selected TUI snapshots and keeps those that differ from upstream's by the name alone. See `DREAMFERENCE_PUFFIN_CODEX.md`.

---

### 4.16. `puffin-search`, `puffin-fetch`, `gmail`

These are the commands the `puffin` agent is told to use in its prompt.

```bash
puffin-search "QUERY" [-n 5] [--json]                # local SearXNG
puffin-fetch URL [--max-chars 8000] [--json]         # readable text of one page
puffin-admin gmail search "GMAIL QUERY" [-n|--max-results 10] [--json]   # at most 20
puffin-admin gmail read MESSAGE_ID [--max-chars 8000] [--json]
puffin-admin gmail status [--json]
```

`gmail` talks to the `dreamference-gmail` service that the web UI already runs, and is read-only. See `DREAMFERENCE_PUFFIN_GMAIL.md`.

Search and fetch are programs of their own rather than `puffin-admin` subcommands: they are the agent's most frequent commands, and `puffin-admin` administers the machine. Both are Rust binaries from `puffin-web-rs/` (`DREAMFERENCE_PUFFIN_CODEX.md` §4.1), not console scripts, so they work from a shell with no virtualenv; `puffin-admin codex build` installs them beside `puffin` and links them into `~/.local/bin`. They were `puffin-admin search` and `puffin-admin fetch` until 2026-09-30; both subcommands are gone rather than aliased.

---

### 4.17. `puffin-admin clear model-cache`

`ModelDownloader.clear_cache()` removes exactly two directories: the HuggingFace hub (`~/.cache/huggingface/hub`, or `$HF_HOME/hub`) and the tensorizer cache (`~/.cache/dreamference/tensorizer`). Their parents are left alone: `~/.cache/huggingface` holds the HuggingFace login token, and `~/.cache/dreamference` holds vLLM's torch.compile cache, the `puffin` build cache (`puffin-codex/`), fonts and test logs. Until 2026-09-29 it deleted both parents.

Containers write parts of these caches as root, which `rmtree` cannot remove as the user. When a directory survives, the command says so, prints the `sudo rm -rf` that would finish the job, and exits 1 rather than reporting success.

### 4.18. `puffin-admin clear tensorize-cache`

`ModelDownloader.clear_tensorizer_cache()` removes `~/.cache/dreamference/tensorizer` only, with the same report-and-exit-1 behaviour for root-owned leftovers. Until 2026-09-29 it removed the parent, all of `~/.cache/dreamference`, including vLLM's compile cache (a cold recompile costs 8–12 minutes) and the `puffin` build cache.

---

### 4.19. `puffin-admin puffin` (alias `onyx`)

```bash
puffin-admin puffin start [--no-wait]
puffin-admin puffin configure [--email E] [--password P] [--no-web] [--no-brand] [--no-voice] [--no-gmail] [--no-image-search]
puffin-admin puffin google-auth [--client-id ID] [--client-secret S]
puffin-admin puffin gmail                 # (re-)register the Gmail tool; accounts are connected in the UI
puffin-admin puffin status | logs [-f|--follow] | stop | uninstall
```

This manages the Onyx Lite deployment (web chat UI) in front of the same vLLM model. `configure`:
- registers the vLLM provider;
- applies Puffin branding, fonts and UI patches;
- sets up SearXNG web search, Whisper voice, the Gmail tool and the image-search tool.

Each piece has its own opt-out. See `DREAMFERENCE_ONYX.md`.

### 4.20. `puffin-admin desktop`

`install`, `run`, `build`, `status` for the Tauri desktop window (binary `puffin-app`). `puffin app` opens the same window. See `CLAUDE.md` ("The desktop app is a window, not a second frontend").

### 4.21. `puffin-admin night`

```bash
puffin-admin night enable [--window HH:MM-HH:MM]     # default: [night] window, 01:00-07:00
puffin-admin night disable
puffin-admin night status
puffin-admin night run [--until HH:MM] [--minutes N] [--idle-minutes N] [--ignore-open-sessions]
```

Night Shift (`dreamference/night_shift/`, `DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md`). Tasks are queued from `puffin` with `/night add` (or `puffin night add …` from a shell); these commands run them.

- **`enable` / `disable`:** install or remove the systemd user timer `puffin-night.timer` that starts `night run` at the window's start.
- **`status`:** the timer, the window and the queue of every repository.
- **`run`:** works through the queue now, until the window ends (`--until`, or `--minutes` from now). It waits for the model server to have been idle for `--idle-minutes` (default 10). `--ignore-open-sessions` skips the wait for open `puffin` sessions; it is for testing.

While a night run holds its lock, `server start`, `codex build` and `index` refuse to run. A SWE-bench run takes the same lock (§4.23), so the two exclude each other and the refusal names whichever holds it.

### 4.22. `puffin-admin code setup`, `puffin-admin searxng start`

- **`code setup`:** installs the pinned, checksum-verified tools the code index runs, and records the toolchains (Go, JDK 17+, .NET SDK 8+, and Maven and Gradle when installed) the optional indexers need. The index itself is the Rust binary `puffin-code`, built and linked by `codex build`. See `DREAMFERENCE_PUFFIN_CODE_INDEX.md`.
- **`searxng start`:** creates the SearXNG container on the project network `dreamference-sidecars`, published on `127.0.0.1:8888` only. A container found on Docker's default bridge is replaced. See `DREAMFERENCE_DOCKER.md` §6. On a node that `node enable` advertised (§4.25) it is published beyond loopback instead.

### 4.23. `puffin-admin swe-bench`

```bash
puffin-admin swe-bench setup [--dataset verified|lite|full|<HF id>] [--validate [--instances IDS] [--limit N] [--force]]
puffin-admin swe-bench smoke [--idle-minutes N] [--ignore-open-sessions]
puffin-admin swe-bench run [--dataset D] [--instances IDS | --subset FILE] [--limit N] [--name NAME]
                           [--eval [--remove-images]] [--code-index off|universal] [--prompt <name>]
                           [--until HH:MM] [--idle-minutes N] [--ignore-open-sessions]
puffin-admin swe-bench eval [RUN]
puffin-admin swe-bench report [RUN] [--against RUN]
puffin-admin swe-bench status
puffin-admin swe-bench clean [RUN] [--images]
```

SWE-bench on this machine (`dreamference/swe_bench/`, `DREAMFERENCE_PUFFIN_SWE_BENCH.md`). `RUN` defaults to the latest run.

- **`setup`:** installs the upstream harness (`swebench` 5.0.2) in a virtualenv of its own, downloads the dataset and builds the relocated copy of `puffin` that starts inside the instance images. `--validate` also checks which instances grade correctly here (the reference patch resolves, a no-op patch does not), which pulls their images.
- **`smoke`:** proves the whole pipeline on five fixed instances. `run` refuses until a smoke has passed with the installed harness version.
- **`run`:** the agent phase. One `puffin exec` per instance, each inside that instance's own container on the internal Docker network `puffin-swe-bench`, which reaches the model server and nothing else. It writes `predictions.jsonl`. A run with an existing `--name` is resumed. `--eval` grades when the agent phase ends; `--remove-images` then works one repository at a time and removes its images once graded. `--code-index universal` indexes each instance's repository on the host and gives the agent `puffin-code` (default `off`). `--prompt <name>` starts the agent under that system prompt (`default`, `high-swe`, or a custom one from `$CODEX_HOME/system-prompts/`, mounted read-only); without it the configured one. The manifest records it, with the custom file's SHA-256, and `report --against` names it when two runs differ.
- **`eval`:** the grading phase: the upstream harness applies each patch and runs the tests. It needs no model.
- **`report`:** the resolved rate and what it was measured with; `--against` compares two runs instance by instance.
- **`status`:** runs, their progress, the images present and free disk.
- **`clean`:** removes a run's containers and scratch (every run's, with no `RUN`); `--images` also removes the instance images.

Files: the harness, dataset, runtime and validation results under `~/.cache/dreamference/swe-bench/`; one directory per run under `~/.local/share/dreamference/swe-bench/runs/`. Settings come from the `[swe_bench]` table (§5.1). `run` and `smoke` use Night Shift's admission (idle model, memory) and its runner lock.

### 4.24. `puffin-admin audit egress`

```bash
puffin-admin audit egress [--prompt PROMPT] [--json]
```

Runs one real `puffin exec` session under `strace`, in a throwaway repository with a throwaway `CODEX_HOME`, and prints every network destination, every name asked of a resolver and every process the session started, with a verdict (`dreamference/audit/`, `DREAMFERENCE_PUFFIN_EGRESS.md`). `--prompt` replaces the default prompt (a one-word reply); `--json` also writes the full result to `$CODEX_HOME/audit/<timestamp>.json`. Exit status: 0 on a pass, 1 on an unexpected destination, 2 when the trace itself failed.

### 4.25. `puffin-admin node`

```bash
puffin-admin node enable [--no-web]
puffin-admin node disable
puffin-admin node status [NAME]
puffin-admin node list
puffin-admin node add NAME [--user USER] [--ssh-port 22]
puffin-admin node remove NAME
puffin-admin node set NAME --model MODEL
puffin-admin node start NAME | stop NAME
puffin-admin node sync-model NAME MODEL [--address ADDR]
puffin-admin node run NAME [--memory 8G] [--time 90m] [--test CMD] [--setup CMD] [--out DIR] [--bind PATH]... -- COMMAND...
puffin-admin node jobs [NAME] | logs JOB | cancel JOB | fetch JOB
```

The node half of the client/server split (`dreamference/node/`, `DREAMFERENCE_PUFFIN_NODE.md`).

- **`enable`:** installs the Avahi service file that advertises this machine as `_puffin-node._tcp`, publishes the web UI and SearXNG beyond loopback, and records that it did, so a later `puffin configure` or `searxng start` keeps those addresses. `--no-web` keeps the web UI on this machine; clients then get `puffin` and web search only. Root is needed once, for the file under `/etc/avahi`: the command is printed and `sudo` prompts on the terminal. If the file cannot be installed, nothing is published. It prints that anyone on the local network can then use the node, with nothing encrypted or authenticated, and that the web UI's one account (and its Gmail tool) is shared.
- **`disable`:** stops advertising and puts the web UI and web search back on this machine only.
- **`status`:** the node id, what is advertised and published, and what a browse of the network returns; with a name, that paired node's status.
- **`list`:** every node on the local network, with its model, its load and whether it is paired. It needs no pairing: those figures come from each node's open model port.
- **`add`:** pairs with another node over SSH, once, so it can be managed from here. `NAME` is the node's name, address or id as `list` shows it. It makes a key used for nothing else, and the other node authorises it for one forced command only, with no terminal and no forwarding; the node's host key is pinned to its id.
- **`remove`:** unpairs: removes the key on both sides.
- **`set` / `start` / `stop`:** assign a model (a key of that node's model matrix) and start it there, or start or stop a paired node's model server. Each is carried out by that node's own `puffin-admin`, with its own host-safety checks.
- **`list`** also shows each node's free memory, for this machine and for paired nodes (asked over the pairing; memory is not on the open model port).
- **`sync-model`:** copies a model's Hugging Face cache folders (and its drafter's) to a paired node over the pairing, so it need not download them; `--address` uses another address of the same node, such as its QSFP link's. The node accepts only folders of a key of its own matrix, checks each weight file's checksum, and keeps files it already has.
- **`run`:** runs a command on a paired node, in this repository at `HEAD`, in a sandboxed, memory-capped and time-limited unit there; its changes come back as the branch `job/<id>`. `--setup` builds an environment once per lock-file content and binds it read-only into later jobs; `--out` names a folder that comes back as files to `~/.puffin/jobs/received/<id>/`, never committed; `--bind` binds a path on the node read-only, if the node's `[node] bindable` allows it. `jobs`, `logs`, `cancel` and `fetch` follow a job; a finished job is pruned on the node a day after it was fetched, or 14 days after it finished.

Three more subcommands are not typed by a person: `node job-exec <job>` (run inside a job's systemd unit to carry out that job), `node authorize` (run by `node add` on the other node; it reads a public key on standard input) and `node serve-job [--key TAG]` (the forced command sshd starts for a paired key; it refuses anything but info, status, start, stop, set-model, model-receive, unpair, the two git services for a job repository, and the job and night-task requests). There is no primary node: the machine `puffin-admin node …` is typed on is the one doing the managing. With one GB10 here, pairing was run against a scratch sshd on loopback, not against a second machine.

The switches are kept in `~/.config/dreamference/node-advertise.json`, not in `dreamference.toml`, because that file is resolved from the working directory first. On a client the counterpart is `puffin node list|use|forget`, in the launcher.

### 4.26. `puffin-admin host`

```bash
puffin-admin host check     # what `server start` would refuse over; changes nothing; exit 1 if anything is listed
puffin-admin host setup     # applies it: each command printed, then run through sudo
```

`HostSafetySetup` applies what `check_host_safety()` (§4.6) only prints: sysstat, an armed earlyoom, 64 GB of swap, and the two sysctls. It also covers the agent's prerequisite: on Ubuntu (`kernel.apparmor_restrict_unprivileged_userns = 1`) it installs `/etc/apparmor.d/puffin-bwrap`, a profile granting `userns` to `/usr/bin/bwrap` alone, so Codex's sandbox works from a systemd unit, the Night Shift timer or an SSH job, not only from a terminal (`sandbox_prerequisite.py`). Every other `puffin-admin` run checks that sandbox from a throwaway user unit (~25 ms) and, when it fails, offers to fix it, to turn Night Shift off, or to ask again later; `mcp`, `host` and `node serve-job` never ask. Loaded and verified on this machine on 2026-10-03. The cases it reports instead of acting on, and what was verified, are in [SETUP §3.3](./DREAMFERENCE_SETUP.md). `install.sh` and `scripts/install_gb10.sh` both run `host setup`.

**On a release install** (no checkout; [SETUP §3.2](./DREAMFERENCE_SETUP.md)) `codex build` builds nothing: it reports the installed release binaries and refreshes the links, or says how to install them; `desktop build` and `desktop install` point at the release's `.deb` and AppImage.

---

## 5. Configuration Hierarchy

Every field resolves in `DreamferenceConfig.__init__`, highest priority first:

1. **Constructor arguments**: the CLI passes `--model`, `--agent`, … through them.
2. **Environment variables**: `DREAMFERENCE_*`, `HF_TOKEN`.
3. **Config file**, resolved by `ConfigPathResolver`, first match wins:
   1. `--config`;
   2. `DREAMFERENCE_CONFIG_PATH`;
   3. `./dreamference.toml` or `./dreamference.json`;
   4. `~/.config/dreamference/config.toml`.
4. **Built-in defaults**: the module-level `DEFAULT_*` constants.

### 5.1. Config File Example

The file is TOML:

```toml
vllm_host = "http://localhost:8000"
model = "qwen3.8-27b-nvfp4-dflash2"
diffusion_model = "tiny-a2d-coder-0.5b-diffusion"
agent_runner = "codex"
num_speculative_tokens = 8
enable_prefix_caching = true
enable_chunked_prefill = true
num_scheduler_steps = 8
attention_backend = "auto"
# kv_cache_dtype unset = use the model recipe's value
puffin_gmail = true
puffin_cave_mode = "ultra"
puffin_airgapped = "off"     # off | on
puffin_compaction_ledger = true    # hand the agent a rule-built ledger after each compaction

[night]                      # Night Shift, read by `puffin-admin night` (NightShiftSettings)
window = "01:00-07:00"

[swe_bench]                  # read by `puffin-admin swe-bench` (SweBenchSettings)
max_parallel = 3
```

The tables take these keys, each with a built-in default:
- **`[night]`:** `window`, `max_parallel`, `task_timeout`, `test_timeout`, `task_memory`, `nudges`, `test`, `task_context`, `idle_minutes`, `index`, `index_timeout`;
- **`[swe_bench]`:** `max_parallel`, `task_timeout`, `task_memory`, `task_cpus`, `nudges`, `idle_minutes`, `task_context`, `eval_workers`, `eval_memory`, `eval_timeout`, `disk_reserve`.

A `sandbox = …` line left in an older file is ignored: the option was removed on 2026-10-01.

**Fixed 2026-10-01: `save_config()` used to drop the `[night]` table.** It rewrote the file from `DreamferenceConfig`'s flat fields, so `puffin-admin main-model set`, `diffusion-model set` and `init` removed Night Shift's settings. `ConfigFileStorageManager.save_config_dict()` now carries over every table already in the file that the saved keys do not name; `test_saving_keeps_tables_other_readers_own` covers it.

`save_config()` deliberately writes only values that differ from the defaults, so a round trip does not fossilise defaults into the TOML. Until 2026-10-02 `vllm_host` and `agent_runner` were the exception, written whatever their value; they now follow the rule (`test_a_default_host_and_agent_are_not_fossilised_into_the_config`). A file saved before that keeps its two lines until the next save.

---

## 6. Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DREAMFERENCE_CONFIG_PATH` | (resolver order, §5) | Config file path |
| `DREAMFERENCE_VLLM_HOST` | `http://localhost:8000` | vLLM endpoint. Also read by `puffin`'s launcher. |
| `DREAMFERENCE_MODEL` | `qwen3.8-27b-nvfp4-dflash2` | Main model alias |
| `DREAMFERENCE_DIFFUSION_MODEL` | `tiny-a2d-coder-0.5b-diffusion` | Diffusion sidecar model |
| `DREAMFERENCE_DRAFT_MODEL` | (unset) | Draft model alias |
| `DREAMFERENCE_SPECULATIVE_TOKENS` | `8` | Speculative token count |
| `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER` | `codex` | Agent runner (`codex`, `cline`, `continue`, `openhands`) |
| `DREAMFERENCE_USE_TENSORIZER` | `false` | Tensorize after download |
| `DREAMFERENCE_PUFFIN_GMAIL` | `true` | Add the Gmail section to `puffin`'s prompt when an account is connected |
| `DREAMFERENCE_PUFFIN_CAVE_MODE` | `ultra` | Cave-mode level for new `puffin` sessions (`off`, `lite`, `full`, `ultra`); also the config key `puffin_cave_mode` |
| `DREAMFERENCE_PUFFIN_PROMPT` | `default` | System prompt for new `puffin` sessions (`default`, `high-swe`, or a custom one in `$CODEX_HOME/system-prompts/<name>.md`); also the config key `puffin_prompt`, which `puffin prompt use` writes. An unknown name is skipped with a warning. A resumed session keeps the prompt it started with |
| `DREAMFERENCE_PUFFIN_AIRGAPPED` | `off` | How much of the internet a `puffin` session may use (`off`, `on`); also the config key `puffin_airgapped` |
| `DREAMFERENCE_PUFFIN_COMPACTION_LEDGER` | `true` | Register `puffin ledger` as the hook that runs after each compaction of a `puffin` session ([PUFFIN_COMPACTION §11](./DREAMFERENCE_PUFFIN_COMPACTION.md)); also the config key `puffin_compaction_ledger` |
| `DREAMFERENCE_SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG instance used by `puffin-search` and the MCP server's `web_search` |
| `CODEX_HOME` | `~/.puffin` | `puffin`'s home folder: sessions, config, skills (`skills/`), the Night Shift queue (`night/`), audit results (`audit/`) |
| `PUFFIN_NODE` | (unset) | On a client: the node one `puffin` command uses, by name, address or id, instead of the remembered one |
| `PUFFIN_RELEASE_REPO` | `dreamference/dgx-lunny` | Where `puffin update` looks for releases, e.g. a fork |
| `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN` | (unset) | HuggingFace token |
| `HF_HOME` | `~/.cache/huggingface` | HF cache root (the hub cache is `$HF_HOME/hub`) |

The table lists what a user sets. Left out on purpose are the variables one component sets for another (`PUFFIN_NIGHT_RUN`, `PUFFIN_UPSTREAM_TESTS`, the `PUFFIN_CODE_*` paths the launcher, the indexers and the SWE-bench runner pass along, the `PUFFIN_GMAIL_*`, `PUFFIN_IMAGE_*`, `PUFFIN_SIGLIP_URL` and `PUFFIN_VISION_*` settings of the sidecar containers, `DREAMFERENCE_DIFFUSION_PORT` and `DREAMFERENCE_DIFFUSION_MODEL_ID` inside the diffusion container), the build-time `PUFFIN_VERSION` the release workflow sets, and test seams (`PUFFIN_NIGHT_PUFFIN_BIN`, `PUFFIN_TEST_NODE_ID`). Each is described in the module that reads it.

**Tuning keys** (config file or CLI only, no environment variable):
- `enable_prefix_caching`;
- `enable_chunked_prefill`;
- `num_scheduler_steps`;
- `attention_backend`;
- `kv_cache_dtype` (default: unset, so the model recipe decides);
- `tool_call_parser` (default `hermes`, overridden by the recipe);
- `max_num_batched_tokens` (`8192`);
- `guided_decoding_backend` (`xgrammar`).

---

## See Also

- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runners
- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** the `puffin` binary
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM configuration
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** model caching and Docker management
