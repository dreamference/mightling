# Dreamference CLI Reference

> **Version:** 1.2.0
> **Subject:** Command Suite, Subcommands, Configuration, Environment Variables

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

**Entry Point**: `dream` (CLI controller: `DreamferenceCLIController` in `dreamference/cli/`)

**Framework**: Argparse + Rich terminal UI

**Subcommands**: 19 commands across 6 categories

- **Initialization**: `init`, `main-model {set,inspect}`
- **Agent Sessions**: `chat`, `run`
- **Server Management**: `server {start,stop,remove}`, `logs request`, `benchmark_server`
- **Indexing**: `index`
- **Utilities**: `status`, `endpoints`, `web`, `model {list,download}`, `clear {model-cache,tensorize-cache}`, `mcp`

---

## 2. Global Options

These flags apply to all subcommands:

| Flag | Alias | Type | Description |
| :--- | :--- | :--- | :--- |
| `--config PATH` | `-c` | Path | Custom Dreamference config (`.yaml` / `.json`) |
| `--sandbox {none,apptainer,podman,docker}` | | Choice | Rootless sandbox prefix (**Goose only**) |
| `--agent {goose,cline,aider,continue,openhands}` | `-a` | Choice | Primary agent runner (default: `goose`) |
| `--hf-token TOKEN` | | String | HuggingFace token (else `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN`) |
| `--debug` | | Flag | Verbose output for troubleshooting |
| `--cave` | | Flag | Enable Cave Mode (terse, command-only output) |

---

## 3. Subcommand Summary

| Subcommand | Description | Key Args |
| :--- | :--- | :--- |
| **`init`** | Pre-download models, generate config, force-index workspace | `[--model MODEL] [--draft-model DRAFT]` |
| **`chat`** | Interactive session for selected agent | `[--model MODEL] [--draft-model DRAFT]` |
| **`run`** | Non-interactive task execution | `"PROMPT" [--model MODEL]` |
| **`status`** | Hardware, vLLM, agent, and context readiness panels | (none) |
| **`server start`** | Launch local vLLM server optimized for GB10 | `[--model MODEL] [--port PORT] [--quantization QUANT]` |
| **`server stop`** | Stop running vLLM Docker container | `[--port PORT]` |
| **`server remove`** | Force-remove vLLM Docker container | `[--port PORT]` |
| **`logs request`** | Tail vLLM container logs | `[--port PORT]` |
| **`benchmark_server`** | Run vLLM benchmark using Sonnet dataset | `[--port PORT]` |
| **`index`** | AST + FTS5 + TF-IDF workspace index | `[--dir PATH] [--force]` |
| **`mcp`** | Stdio MCP server for IDE companion tools | (none) |
| **`endpoints`** | List all vLLM OpenAI-compatible REST endpoints | (none) |
| **`web`** | Web Canvas UI on port 8501 (default) | `[--port PORT]` |
| **`model list`** | List available models in matrix | (none) |
| **`model download`** | Pre-download model weights to HF cache | `[--model MODEL] [--all] [--tensorize/--no-tensorize]` |
| **`main-model set`** | Dynamically configure active primary model | `<model_name>` |
| **`main-model inspect`** | Inspect active model's capabilities via prompt probes | (none) |
| **`clear model-cache`** | Clear both HF and tensorizer model caches | (none) |
| **`clear tensorize-cache`** | Clear only tensorizer model cache | (none) |

---

## 4. Detailed Command Reference

### 4.1. `dream init`

```bash
dream init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST] [--sandbox …] [--agent …] [--hf-token …]
```

**Behavior**:
1. Downloads primary/draft weights → HF cache
2. Generates minimal sparse `dreamference.toml` via `config_generator`
3. Writes Goose config (`~/.config/goose/config.yaml`)
4. Forces full workspace re-index (`ContextEngine.index_workspace(force_reindex=True)`)

**Example**:
```bash
dream init --model qwen3.6-35b-a3b-nvfp4 --agent goose
```

---

### 4.2. `dream main-model`

#### Subcommand: `set`
```bash
dream main-model set <model_name>
```

**Behavior**: Modifies `dreamference.toml` to lock in a new primary model alias/repo.

#### Subcommand: `inspect`
```bash
dream main-model inspect
```

**Behavior**: Runs an automated suite of prompt probes against the running model to detect:
- Tool calling capability
- JSON mode support
- Reasoning tags
- ChatML format
- TTFT streaming
- MoE architecture
- Prompt latency

Outputs a detailed markdown table.

**Example**:
```bash
dream main-model inspect
```

---

### 4.3. `dream chat`

```bash
dream chat [--model MODEL] [--draft-model DRAFT_MODEL] [--agent goose|cline|aider|continue|openhands] [--sandbox …] [--hf-token …] [--debug] [--cave]
```

**Behavior**: Instantiates agent runner (`GooseRunner` / `ClineRunner` / `AiderRunner` / `ContinueRunner` / `OpenHandsRunner`), then `run_session(debug=…)`.

**Function Calling (Goose)**:
- Enabled by default via `--enable-auto-tool-choice`
- Tool-call parser automatically resolved per-model (e.g., `qwen3_xml` for default model, `hermes` for Qwen 2.5 Coder)
- No extra flags needed for `dream chat`

**Cave Mode (`--cave`)**:
- Injects strict Cave Mode system prompt into Goose `instructions` or `.clinerules` for Cline
- Forces terse, command-only output

**Example**:
```bash
dream chat --agent aider --debug
```

---

### 4.4. `dream run`

```bash
dream run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--agent …] [--sandbox …] [--hf-token …] [--debug] [--cave]
```

**Behavior**:
- Goose: `goose run --text "<prompt>"`
- Aider: `aider … --message "<prompt>"`
- Cline: prints prompt and opens VS Code
- Continue / OpenHands: launch UI; prompt unused

**Example**:
```bash
dream run "Refactor database connection pool to use async pg" --agent goose
```

---

### 4.5. `dream status`

```bash
dream status
```

**Output Panels**:

1. **Hardware**:
   - GB10 qualification
   - GPU name, driver version
   - Total/used/available unified memory
   - VRAM usage, architecture

2. **vLLM & Agent**:
   - Endpoint health
   - Served models list
   - Active agent runner
   - Configured/draft model
   - Sandbox mode, HF token presence
   - Prefix/chunked prefill status
   - Scheduler steps, KV cache dtype
   - Agent binary/extension readiness

3. **Context**:
   - Indexed file count
   - AST symbol count
   - JSON + SQLite paths (if loaded)

---

### 4.6. `dream server start`

```bash
dream server start [--model MODEL] [--port PORT] [--quantization QUANT] [--draft-model DRAFT] [--hf-token …] [--api-key KEY] [--enable-auto-tool-choice] [--tool-call-parser PARSER] [--max-num-batched-tokens N] [--tensorize/--no-tensorize]
```

**Behavior**:
1. Starts vLLM in background + ModelLoadingMonitor
2. Streams live logs + memory every 10s
3. Exits cleanly once health check passes (server keeps running)

**Optional Features**:
- `--api-key KEY`: Enable OpenAI-compatible auth
- `--enable-auto-tool-choice`: Enable function calling (default on)
- `--max-num-batched-tokens 8192`: Auto-passed with chunked prefill (default)

**Docker Command**:
```bash
docker rm -f dreamference-vllm-<port>
docker run --ipc=host --network host --name dreamference-vllm-<port> --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e HF_TOKEN=<token> \
  -e CUTE_DSL_ARCH=sm_121a \
  -e VLLM_LOGGING_LEVEL=DEBUG \
  [recipe-specific environment variables] \
  --entrypoint vllm <image> serve <model_id> [vllm-flags]
```

**Example**:
```bash
dream server start --model qwen3.6-35b-a3b-nvfp4 --port 8000
```

---

### 4.7. `dream server stop`

```bash
dream server stop [--port PORT]
```

**Behavior**: Stops Docker container `dreamference-vllm-<port>` (safe no-op if not running).

**Docker Command**: `docker stop dreamference-vllm-<port>`

**Example**:
```bash
dream server stop --port 8000
```

---

### 4.8. `dream server remove`

```bash
dream server remove [--port PORT]
```

**Behavior**: Forces removal of Docker container.

**Docker Command**: `docker rm -f dreamference-vllm-<port>`

**Example**:
```bash
dream server remove --port 8000
```

---

### 4.9. `dream logs request`

```bash
dream logs request [--port PORT]
```

**Behavior**: Tails the logs of the running vLLM container.

**Docker Command**: `docker logs -f dreamference-vllm-<port>`

**Example**:
```bash
dream logs request --port 8000
```

---

### 4.10. `dream benchmark_server`

```bash
dream benchmark_server [--port PORT]
```

**Behavior**: Run vLLM serve benchmark using Sonnet dataset on the running server container.

**Example**:
```bash
dream benchmark_server --port 8000
```

---

### 4.11. `dream index`

```bash
dream index [--dir PATH] [--force]
```

**Behavior**: Indexes workspace (Python AST + FTS5 + TF-IDF + semantic embeddings); persists:
- `.dreamference/context_index.json` (symbols + metadata)
- `.dreamference/context.db` (SQLite with FTS5 + vec0 embeddings)

**Example**:
```bash
dream index --force
```

---

### 4.12. `dream mcp`

```bash
dream mcp
```

**Behavior**: Stdio JSON-RPC MCP server.

**Tools Exposed**:
- `ide_get_active_editor`
- `ide_get_diagnostics`
- `ide_get_open_files`
- `ide_open_file`
- `ide_apply_diff`
- `workspace_search_code`

IDE fields live in in-process `IDEState` (empty unless populated by companion); workspace search uses `ContextEngine.search_code`.

---

### 4.13. `dream endpoints`

```bash
dream endpoints
```

**Behavior**: Prints two Rich tables:

1. **OpenAI-Compatible Endpoints**:
   - `/v1/models` (GET)
   - `/v1/chat/completions` (POST)
   - `/v1/completions` (POST)
   - etc.

2. **Credentials**:
   - Base URL
   - API key (if enabled via `--api-key` on `start_server`)
   - `Authorization: Bearer <key>` format

**Note**: `--served-model-name` is never set, so external clients must use full HF repo paths.

**Example**:
```bash
dream endpoints
```

---

### 4.14. `dream web`

```bash
dream web [--port PORT]
```

**Behavior**: HTTP server on `0.0.0.0:{port}` (default `8501`).

**Content**:
- Glassmorphism dark SPA (CDN Mermaid)
- Static architecture Mermaid diagram & diff pane placeholders
- Live unified-memory gauge from `/api/status`
- Illustrative KV fill when vLLM is healthy

**REST Endpoint**:
- `GET /api/status` → `{ hardware, vllm, context }`

**Example**:
```bash
dream web --port 8501
```

---

### 4.15. `dream model list`

```bash
dream model list
```

**Behavior**: Lists all models in `ModelMatrixRegistry.MATRIX` with compatibility info.

---

### 4.16. `dream model download`

```bash
dream model download [--model MODEL] [--all] [--tensorize/--no-tensorize]
```

**Behavior**:
- Without `--all`: Downloads `args.model or config.model` + optional draft to `~/.cache/huggingface/hub/`
- With `--all`: Iterates sequentially over all `compatible_gb10` matrix entries
- `--tensorize`: Also converts model to tensorizer format

**Also invoked automatically** from `init` and `server start`.

**Example**:
```bash
dream model download --model qwen3.6-35b-a3b-nvfp4
```

---

### 4.17. `dream clear model-cache`

```bash
dream clear model-cache
```

**Behavior**: Clears both HuggingFace (`~/.cache/huggingface`) and tensorizer (`~/.cache/dreamference`) parent cache directories.

Invokes `ModelDownloader.clear_cache()` → `shutil.rmtree()`.

**Status Messages**: `🗑️ ℹ️ ✅`

**Example**:
```bash
dream clear model-cache
```

---

### 4.18. `dream clear tensorize-cache`

```bash
dream clear tensorize-cache
```

**Behavior**: Clears only tensorizer cache directory (`~/.cache/dreamference/tensorizer`).

Invokes `ModelDownloader.clear_tensorizer_cache()` → `shutil.rmtree()`.

**Status Messages**: `🗑️ ℹ️ ✅`

**Example**:
```bash
dream clear tensorize-cache
```

---

## 5. Configuration Hierarchy

**Resolution Order** (highest to lowest priority):

1. **CLI parameters** — `--config`, `--model`, `--agent`, `--sandbox`, …
2. **Environment variables** — `DREAMFERENCE_*`, `HF_TOKEN`, …
3. **Config file** — `dreamference.toml` (local, or `~/.config/dreamference/config.toml`)
4. **Built-in defaults** — Module-level `DEFAULT_*` constants

### 5.1. Config File Example

```yaml
vllm_host: http://localhost:8000
model: qwen3.6-35b-a3b-nvfp4
draft_model: null
num_speculative_tokens: 5
sandbox: none
agent_runner: goose
hf_token: null
enable_prefix_caching: true
enable_chunked_prefill: true
num_scheduler_steps: 8
attention_backend: auto
kv_cache_dtype: fp8
```

**Important**: `save_config()` deliberately writes only values that differ from the defaults, so a round-trip does not fossilize defaults into the TOML.

---

## 6. Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DREAMFERENCE_CONFIG_PATH` | Path | (resolver default) | Custom config file path |
| `DREAMFERENCE_VLLM_HOST` | URL | `http://localhost:8000` | vLLM endpoint URL |
| `DREAMFERENCE_MODEL` | String | `qwen3.6-35b-a3b-nvfp4` | Primary model alias |
| `DREAMFERENCE_DRAFT_MODEL` | String | (unset) | Draft model alias |
| `DREAMFERENCE_SPECULATIVE_TOKENS` | Int | `5` | Speculative token count |
| `DREAMFERENCE_SANDBOX` | Choice | `none` | Sandbox engine |
| `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER` | Choice | `goose` | Agent runner (`goose` \| `cline` \| `aider` \| `continue` \| `openhands`) |
| `HF_TOKEN` / `DREAMFERENCE_HF_TOKEN` | String | (unset) | HuggingFace token |
| `GOOSE_PROVIDER` | String | `openai` | Set for Goose processes |
| `OPENAI_BASE_URL` | URL | `{vllm_host}` | Set for Goose processes |
| `OPENAI_API_KEY` | String | `gb10-local-token` | Set for Goose processes |
| `GOOSE_MODEL` | String | `{model}` | Set for Goose processes |

**Tuning Keys** (config-file / CLI only, no dedicated env vars):
- `enable_prefix_caching`
- `enable_chunked_prefill`
- `num_scheduler_steps`
- `attention_backend`
- `kv_cache_dtype`

---

## See Also

- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Agent runners & session startup
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — vLLM configuration
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md)** — Model caching & Docker management
