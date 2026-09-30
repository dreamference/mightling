# Puffin CLI Reference

> **Version:** 1.2.0 (`setup.py`)
> **Subject:** Command Suite, Subcommands, Configuration, Environment Variables
> **Checked against the code:** 2026-09-29 (`dreamference/cli/dreamference_cli_controller.py`, `build_parser()`)

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
- `puffin`: **not** a Python entry point. It is the Rust binary built by `puffin-admin codex build`: Codex with Puffin's branding and launcher compiled in, linked at `~/.local/bin/puffin`. It takes Codex's command line. See `DREAMFERENCE_PUFFIN_CODEX.md`.

There is no `chat` subcommand any more (removed 2026-09-28). The interactive agent is `puffin`. The other agents (Cline, Continue, OpenHands) are reachable through `puffin-admin run "…" --agent …`.

**Framework:** argparse + Rich terminal UI. Parsing is strict: every command rejects unknown arguments.

**Commands:**

- **Setup:** `init`, `model {list,download}`, `main-model {set,inspect}`, `diffusion-model {set}`, `clear {model-cache,tensorize-cache}`
- **Agents:** `run`, `codex {build,start,stop}`
- **Model server:** `server {start,stop,remove,logs}`, `logs [server|mcp]`, `endpoints`, `benchmark_server`
- **Web UI and desktop:** `puffin {start,configure,google-auth,gmail,status,logs,stop,uninstall}` (alias `onyx`), `desktop {install,run,build,status}`
- **Agent tools:** `gmail {search,read,status}`; search and fetch are commands of their own, `puffin-search` and `puffin-fetch` (§4.16)
- **Context and IDE:** `index`, `mcp`, `web`, `status`

---

## 2. Global Options

These options come before the subcommand (`puffin-admin --agent cline run "…"`). `init` and `run` also accept most of them after the subcommand.

| Flag | Type | Description |
| :--- | :--- | :--- |
| `--config PATH` | Path | Custom Puffin config file |
| `--sandbox {none,apptainer,podman,docker}` | Choice | Rootless container sandbox engine |
| `--agent {codex,cline,continue,openhands}` | Choice | Agent runner (default `codex`) |
| `--hf-token TOKEN` | String | HuggingFace token (else `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN`) |

`--debug` and `--cave` belong to `run`; they are not global.

---

## 3. Subcommand Summary

| Subcommand | Description | Key Args |
| :--- | :--- | :--- |
| **`init`** | Download weights, write the config, force a workspace re-index | `[--model] [--vllm-host] [--draft-model] [--sandbox] [--agent] [--hf-token]` |
| **`run`** | Run one task with the selected agent | `PROMPT [--model] [--draft-model] [--sandbox] [--agent] [--hf-token] [--debug] [--cave]` |
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
| **`puffin …`** (`onyx …`) | Onyx Lite web UI lifecycle | see §4.19 |
| **`desktop …`** | Tauri desktop window (`puffin-app`) | `install`, `run`, `build`, `status` |
| **`search`** | Web search through the local SearXNG | `QUERY… [-n 5] [--json]` |
| **`fetch`** | Fetch a URL as readable text | `URL [--max-chars 8000]` |
| **`gmail …`** | Read-only Gmail search and read | `search QUERY [-n 10] [--json]`, `read ID [--max-chars 8000] [--json]`, `status [--json]` |

A top-level `clear-tensorize-cache` subcommand is the older spelling of `clear tensorize-cache` and does the same (until 2026-09-29 it parsed and did nothing).

---

## 4. Detailed Command Reference

### 4.1. `puffin-admin init`

```bash
puffin-admin init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST] [--sandbox …] [--agent …] [--hf-token …]
```

**Behaviour:**
1. Downloads the main (and draft) weights into the HF cache, tensorizing them if `use_tensorizer` is set.
2. Writes a minimal `dreamference.toml` via `config_generator.generate_default_init_config`, at the path `ConfigPathResolver` resolves.
3. Forces a full workspace re-index (`ContextEngine.index_workspace(force_reindex=True)`).

---

### 4.2. `puffin-admin main-model` / `diffusion-model`

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
puffin-admin run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--agent …] [--sandbox …] [--hf-token …] [--debug] [--cave]
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
   - sandbox, HF token presence;
   - prefix caching / chunked prefill, multi-step scheduling, KV cache dtype (`from model recipe` unless overridden), tool-call parser;
   - install state of each agent (Cline, Continue, OpenHands, Codex);
   - the Puffin config path.
3. **Context Engine Index Status:** indexed files, AST symbols, index path, SQLite store path. Shown only when an index exists.

---

### 4.5. `puffin-admin index`

```bash
puffin-admin index [--dir PATH] [--force]
```

Indexes the workspace, the current directory unless `--dir` is given, with Python `ast` symbols, FTS5, TF-IDF and `nomic-embed-text-v1.5` embeddings. It writes `.dreamference/context_index.json` and `.dreamference/context.db` (SQLite with FTS5 and sqlite-vec). See `DREAMFERENCE_CONTEXT.md`.

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
1. Runs the host-safety pre-flight (`check_host_safety`).
2. Starts the diffusion sidecar `dreamference-diffusion-<diffusion-port>` **first**, unless `--no-diffusion`, so that vLLM's free-memory check accounts for it.
3. Starts vLLM in Docker, under the PSI memory-pressure watchdog. It streams logs and memory until the health check passes, then exits, leaving the server running.

Per-model flags come from the model matrix's `launch_overrides`. Options given here override them, and anything unset falls back to the recipe. `--docker-image` overrides the model's pinned image.

**Speculation.** `--draft-model` and `--num-speculative-tokens` are turned into `--speculative-config` JSON by `VLLMServerManager.resolve_speculative_config()`, never into separate flags: vLLM 0.2x has no `--speculative-model` or `--num-speculative-tokens`. The depth passed is the config's resolved value (flag > `DREAMFERENCE_SPECULATIVE_TOKENS` > file > default), and it applies only together with `--draft-model`; without one, the recipe's speculative config is used exactly. See `DREAMFERENCE_INFERENCE.md` §3.

**Docker command** (`VLLMServerManager.start_server`):
```bash
docker run --ipc=host --network host --restart unless-stopped --name dreamference-vllm-<port> --gpus all \
  --cpus=<limit> --memory=<N>g --memory-swap=<N>g \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  [-e HF_TOKEN=<token>] [recipe env vars] \
  -e VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm -e CUTE_DSL_ARCH=sm_121a -e VLLM_LOGGING_LEVEL=DEBUG \
  -e VLLM_DEBUG_LOG_API_SERVER_RESPONSE=1 -e VLLM_DEBUG_LOG_API_SERVER_REQUEST=1 \
  --entrypoint vllm <image> serve <hf_repo> [vllm flags]
```

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
puffin-admin benchmark_server [--port 8000] [--model qwen3.5-122b-a10b-hybrid-dflash] [--dataset-path P] [--num-prompts 8] [--max-concurrency 1]
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

1. **Available endpoints (OpenAI-compatible):**
   - `/v1/models` (GET)
   - `/v1/chat/completions` (POST)
   - `/v1/completions` (POST)
   - `/v1/embeddings` (POST, if supported)
   - `/health` (GET)
2. **Credentials:**
   - the base URL on localhost and on the LAN IP;
   - the diffusion model URL (`http://localhost:8001/v1`);
   - the API key, which is optional and set only by `server start --api-key`;
   - the `Authorization: Bearer <key>` header format.

Clients address the model by its full HF repo id (e.g. `Intel/Qwen3.5-122B-A10B-int4-AutoRound`).

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
```

- **`build`:** builds `puffin` and `codex-code-mode-host` with `CodexBrandedBuilder` and links `~/.local/bin/puffin`. It skips the build when the recorded build key is current, unless `--force`. See `DREAMFERENCE_PUFFIN_CODEX.md`.
- **`start` / `stop`:** run `puffin app-server daemon start|stop`, building `puffin` first if it is missing. The parser's help text calls this the "Codex comic server"; that is a typo in the help, and the command drives the app-server daemon.

---

### 4.16. `puffin-search`, `puffin-fetch`, `gmail`

These are the commands the `puffin` agent is told to use in its prompt.

```bash
puffin-search "QUERY" [-n 5] [--json]                # local SearXNG
puffin-fetch URL [--max-chars 8000] [--json]         # readable text of one page
puffin-admin gmail search "GMAIL QUERY" [-n 10] [--json]
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
puffin-admin puffin status | logs [-f] | stop | uninstall
```

This manages the Onyx Lite deployment (web chat UI) in front of the same vLLM model. `configure`:
- registers the vLLM provider;
- applies Puffin branding, fonts and UI patches;
- sets up SearXNG web search, Whisper voice, the Gmail tool and the image-search tool.

Each piece has its own opt-out. See `DREAMFERENCE_ONYX.md`.

### 4.20. `puffin-admin desktop`

`install`, `run`, `build`, `status` for the Tauri desktop window (binary `puffin-app`). `puffin app` opens the same window. See `CLAUDE.md` ("The desktop app is a window, not a second frontend").

---

## 5. Configuration Hierarchy

Every field resolves in `DreamferenceConfig.__init__`, highest priority first:

1. **Constructor arguments**: the CLI passes `--model`, `--agent`, `--sandbox`, … through them.
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
model = "qwen3.5-122b-a10b-hybrid-dflash"
diffusion_model = "tiny-a2d-coder-0.5b-diffusion"
agent_runner = "codex"
sandbox = "none"
num_speculative_tokens = 8
enable_prefix_caching = true
enable_chunked_prefill = true
num_scheduler_steps = 8
attention_backend = "auto"
# kv_cache_dtype unset = use the model recipe's value
puffin_gmail = true
```

`save_config()` deliberately writes only values that differ from the defaults, so a round trip does not fossilise defaults into the TOML.

---

## 6. Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DREAMFERENCE_CONFIG_PATH` | (resolver order, §5) | Config file path |
| `DREAMFERENCE_VLLM_HOST` | `http://localhost:8000` | vLLM endpoint. Also read by `puffin`'s launcher. |
| `DREAMFERENCE_MODEL` | `qwen3.5-122b-a10b-hybrid-dflash` | Main model alias |
| `DREAMFERENCE_DIFFUSION_MODEL` | `tiny-a2d-coder-0.5b-diffusion` | Diffusion sidecar model |
| `DREAMFERENCE_DRAFT_MODEL` | (unset) | Draft model alias |
| `DREAMFERENCE_SPECULATIVE_TOKENS` | `8` | Speculative token count |
| `DREAMFERENCE_SANDBOX` | `none` | Sandbox engine |
| `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER` | `codex` | Agent runner (`codex`, `cline`, `continue`, `openhands`) |
| `DREAMFERENCE_USE_TENSORIZER` | `false` | Tensorize after download |
| `DREAMFERENCE_PUFFIN_GMAIL` | `true` | Add the Gmail section to `puffin`'s prompt when an account is connected |
| `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN` | (unset) | HuggingFace token |
| `HF_HOME` | `~/.cache/huggingface` | HF cache root (the hub cache is `$HF_HOME/hub`) |

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
