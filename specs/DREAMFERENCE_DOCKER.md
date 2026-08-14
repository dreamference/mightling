# Dreamference Docker & Model Caching

> **Version:** 1.2.0
> **Subject:** Docker vLLM Architecture, Model Downloads, Tensorization, Cache Management

---

## Table of Contents

- [1. Model Download & Caching](#1-model-download--caching)
- [2. HuggingFace Cache Management](#2-huggingface-cache-management)
- [3. Tensorization](#3-tensorization)
- [4. Docker vLLM Architecture](#4-docker-vllm-architecture)
- [5. vLLM Runtime Images](#5-vllm-runtime-images)
- [6. Docker Agent Architecture](#6-docker-agent-architecture)

---

## 1. Model Download & Caching

### 1.1. Overview

Dreamference implements a best-effort download strategy for model weights. All operations degrade gracefully to on-demand fetch by vLLM if pre-download fails.

### 1.2. Download Flow

1. **Trigger Points**: `dream init`, `dream server start`, explicit `dream model list`, `dream model download` command
2. **Resolution**: Model alias → HuggingFace repo via `ModelMatrixRegistry.resolve_hf_repo()`
3. **Pre-download**: `ModelDownloader.download_model()` and `download_all_models()`
4. **Caching**: All weights land in `~/.cache/huggingface/hub/` (or `$HF_HOME/hub` if `HF_HOME` set)

### 1.3. Best-Effort Policy

- Pre-download uses `huggingface_hub.snapshot_download` (preferred) or `huggingface-cli download` fallback
- Network failures or permission errors do **not** halt startup
- vLLM continues and fetches weights on-demand if they're missing from cache
- Useful for air-gapped or bandwidth-limited deployments

---

## 2. HuggingFace Cache Management

### 2.1. Cache Structure

```
~/.cache/huggingface/hub/
├── models--<org>--<model>/           (one per model)
│   ├── refs/
│   │   └── main                      (current HEAD ref)
│   ├── snapshots/
│   │   └── <commit>/                 (commit snapshot)
│   │       ├── config.json
│   │       ├── model-*.safetensors   (weight shards)
│   │       └── ...
│   └── blobs/                        (content-addressed storage)
```

### 2.2. Detection

`is_model_downloaded(model_alias)` checks for non-empty snapshot directory.

### 2.3. Clearing Cache

```bash
dream clear model-cache
```

Removes both HF (`~/.cache/huggingface/`) and tensorizer (`~/.cache/dreamference/`) parent cache directories.

**Implementation**: `ModelDownloader.clear_cache()` → `shutil.rmtree()` with status messages (`🗑️ ℹ️ ✅`).

---

## 3. Tensorization

### 3.1. Overview

Tensorizer serializes PyTorch model weights to a compact binary format for faster loading on GB10.

- **Format**: `.tensors` file per model
- **Cache**: `~/.cache/dreamference/tensorizer/` (secondary cache)
- **Optional**: Disabled by default (`auto_tensorize=False`)

### 3.2. Tensorization Flow

After HF download (when `auto_tensorize=True`):

1. `tensorize_model(model_alias)` serializes weights to `<repo>--/model.tensors`
2. Stored in `~/.cache/dreamference/tensorizer/`
3. vLLM checks for `.tensors` file on startup
4. If present and valid, loads from `.tensors` instead of `.safetensors` (faster)

### 3.3. Detection

`is_model_tensorized(model_alias)` checks for non-empty `model.tensors` file.

### 3.4. Configuration

- CLI: `dream model download --tensorize` / `--no-tensorize`
- Config: `auto_tensorize: true/false` in `dreamference.toml`
- Default: Off (no automatic tensorization)

### 3.5. Clearing Tensorizer Cache

```bash
dream clear tensorize-cache
```

Removes only the tensorizer cache directory (`~/.cache/dreamference/tensorizer`).

**Implementation**: `ModelDownloader.clear_tensorizer_cache()` → `shutil.rmtree()` with status messages.

---

## 4. Docker vLLM Architecture

### 4.1. Overview

Dreamference uses Docker to run the primary vLLM inference engine. All Docker operations require a working Docker daemon (`docker ps` must succeed).

### 4.2. Daemon Requirements

- Docker service running and accessible
- No elevated privileges required by default (standard `docker` group membership)
- `docker ps` must succeed to proceed with any Docker operations

### 4.3. Container Lifecycle

**Pre-Launch**:
1. Pull vLLM image if missing (or use custom `dreamference-vllm-tensorizer:26.07-py3` if built locally)
2. Force-remove any stale container (`docker rm -f dreamference-vllm-<port>`)

**Launch**:
```bash
docker run --ipc=host --network host \
  --name dreamference-vllm-<port> \
  --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e HF_TOKEN=<token> \
  -e CUTE_DSL_ARCH=sm_121a \
  -e VLLM_LOGGING_LEVEL=DEBUG \
  [recipe-specific environment variables] \
  --entrypoint vllm <image> serve <model_id> [vllm-flags]
```

**Post-Launch**:
1. Stream logs to stdout (ModelLoadingMonitor)
2. Poll `/v1/models` every 0.1 seconds until healthy
3. Print progress: memory usage, stage detection, ETA

**Shutdown**:
- `docker stop dreamference-vllm-<port>` (graceful)
- `docker rm -f dreamference-vllm-<port>` (force removal)

### 4.4. Volume Mounts

| Mount | Purpose |
| :---- | :------ |
| `~/.cache/huggingface:/root/.cache/huggingface` | Model weight caching (inside container) |
| `~/.cache/dreamference:/root/.cache/dreamference` | Tensorizer cache (if enabled) |

### 4.5. Environment Exports

| Variable | Typical Value | Purpose |
| :-------- | :------------ | :------ |
| `HF_TOKEN` | (user's token) | Access gated models |
| `CUTE_DSL_ARCH` | `sm_121a` | Pin SM121 ISA for Blackwell |
| `VLLM_LOGGING_LEVEL` | `DEBUG` | Verbose logging for startup |
| Recipe-specific | (model-dependent) | NVFP4 backend selection, attention backend, MoE backend |

---

## 5. vLLM Runtime Images

### 5.1. Base Image

**Default Image**: `nvcr.io/nvidia/vllm:26.07-py3`

- **Source**: NVIDIA NGC repository (official vLLM container)
- **Pin Rationale**: Includes upstreamed `flashinfer-b12x` SM12x backends (merged May 2026)
- **Blackwell Support**: Full support for SM121 ISA, FP4 kernels, MoE backends

### 5.2. Custom Build (Optional)

Dockerfile dynamically builds if needed:

```dockerfile
FROM nvcr.io/nvidia/vllm:26.07-py3
RUN python -m pip install --no-cache-dir --no-deps "xgrammar==0.2.4"
RUN pip install "vllm[tensorizer]"
```

**Built As**: `dreamference-vllm-tensorizer:26.07-py3`

**Purpose**: Adds:
- `xgrammar` (grammar-constrained generation)
- `vllm[tensorizer]` (optional tensorizer support for fast model loading)

### 5.3. Version Compatibility

**Critical**: Using older vLLM containers (pre-May 2026) will fall back to slower or broken paths for NVFP4 inference on GB10.

If you use an older image:
- NVFP4 models may produce corrupted output (`!` only)
- Fallback to FP8 is triggered
- Performance is degraded

**Recommendation**: Always use `nvcr.io/nvidia/vllm:26.07-py3` or later.

### 5.4. Container Naming

- **Pattern**: `dreamference-vllm-<port>` (e.g., `dreamference-vllm-8000`)
- **Cleanup**: Old containers are force-removed before new launch

---

## 6. Docker Agent Architecture

### 6.1. OpenHands Container

Dreamference uses Docker for the optional OpenHands agent UI. All Docker operations require a working Docker daemon (`docker ps` must succeed).

### 6.2. Image

**Image**: `ghcr.io/all-hands-ai/openhands:main`

- **Source**: GitHub Container Registry (all-hands-ai organization)
- **Pulling**: On-demand via `OpenHandsInstaller.pull_image_if_missing()` when `--agent openhands` is selected
- **Local Build**: No local build; always pulled from GHCR

### 6.3. Container Lifecycle

**Pre-Launch**:
1. Pull image if missing (first run or after clearing)
2. Force-remove any stale container (`docker rm -f dreamference-openhands`)

**Launch**:
```bash
docker run --rm -it \
  --name dreamference-openhands \
  -e LLM_MODEL=openai/{hf_repo} \
  -e LLM_BASE_URL={vllm_host}/v1 \
  -e LLM_API_KEY=gb10-local-token \
  -e WORKSPACE_BASE={cwd} \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v {cwd}:/opt/workspace_base \
  -p 3000:3000 \
  ghcr.io/all-hands-ai/openhands:main
```

**Post-Launch**:
- Web UI accessible at `http://localhost:3000`
- Full access to workspace directory and Docker daemon (for autonomous agent tasks)

### 6.4. Container Naming & Cleanup

- **Name**: `dreamference-openhands` (single instance per system)
- **Cleanup**: Old instances removed before new launch
- **Isolation**: Each launch is stateless (no persistent container data)

### 6.5. Environment Variables

| Variable | Value | Purpose |
| :------- | :---- | :------ |
| `LLM_MODEL` | `openai/{hf_repo}` | Model identifier |
| `LLM_BASE_URL` | `{vllm_host}/v1` | vLLM API endpoint |
| `LLM_API_KEY` | `gb10-local-token` | Fixed authentication token |
| `WORKSPACE_BASE` | `{cwd}` | Workspace directory for agent tasks |

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md)** — Model matrix & defaults
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — Launch configuration & recipes
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Agent runners & OpenHands integration
