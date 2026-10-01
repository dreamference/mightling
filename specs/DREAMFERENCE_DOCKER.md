# Puffin Docker & Model Caching

> **Version:** 1.2.0
> **Subject:** Docker vLLM Architecture, Model Downloads, Tensorization, Cache Management
> **Checked against the code:** 2026-10-01 (`hardware/model_downloader.py`, `vllm_server/vllm_server_manager.py`, `Dockerfile*`; the launch line against `build_launch_command()` output)

---

## Table of Contents

- [1. Model Download & Caching](#1-model-download--caching)
- [2. HuggingFace Cache Management](#2-huggingface-cache-management)
- [3. Tensorization](#3-tensorization)
- [4. Docker vLLM Architecture](#4-docker-vllm-architecture)
- [5. vLLM Runtime Images](#5-vllm-runtime-images)
- [6. Other Containers](#6-other-containers)

---

## 1. Model Download & Caching

### 1.1. Download Flow

1. **Triggers:**
   - `puffin-admin init`;
   - `puffin-admin server start`, for the main model, `--draft-model`, and the recipe's drafter;
   - `puffin-admin model download [--model M | --all]`.

   `model list` only lists; it downloads nothing.
2. **Resolution:** alias → HF repo via `ModelMatrixRegistry.resolve_hf_repo()` (also exposed as `dreamference.hardware.resolve_model_hf_repo`).
3. **Download:** `ModelDownloader.download_model()`, or `download_all_models()` for every `compatible_gb10` entry.
4. **Cache:** `~/.cache/huggingface/hub/`, or `$HF_HOME/hub`.

### 1.2. Best-Effort Policy

- It tries `huggingface_hub.snapshot_download` first, then the `huggingface-cli` binary.
- Network or permission failures print a note and do **not** stop `server start`. vLLM then fetches the missing weights itself, from inside the container during the load, which this host handles badly. Pre-download before an offline session.

---

## 2. HuggingFace Cache Management

### 2.1. Cache Structure

```
~/.cache/huggingface/hub/
└── models--<org>--<model>/
    ├── refs/main
    ├── snapshots/<commit>/     (config.json, *.safetensors → symlinks into blobs/)
    └── blobs/
```

### 2.2. Detection

- `is_model_downloaded(alias)`: the snapshot directory exists and is non-empty.
- `get_model_snapshot_dir(alias)`: its path.

### 2.3. Clearing

`puffin-admin clear model-cache` runs `ModelDownloader.clear_cache()`, which removes exactly two directories:
- the hub, `~/.cache/huggingface/hub` (or `$HF_HOME/hub`), leaving the stored HF token beside it;
- the tensorizer cache, `~/.cache/dreamference/tensorizer`, leaving vLLM's compile cache, the `puffin` build cache, fonts and logs.

Anything a container wrote there as root survives the user's `rmtree`; the command then names the directory, prints the `sudo rm -rf` for it and exits 1. Until 2026-09-29 it removed both parents. Deleting individual `models--…` directories is the targeted alternative.

---

## 3. Tensorization

### 3.1. Overview

Tensorizer serializes weights into one `model.tensors` file for faster loading. It is **off by default**.

- **Cache:** `~/.cache/dreamference/tensorizer/<model dir>/model.tensors`. The paths come from `get_tensorized_dir()` / `get_tensorized_path()`.
- **Detection:** `is_model_tensorized(alias)` checks for a non-empty `model.tensors`.
- **Creation:** `tensorize_model(alias)`, run after download when tensorizing is on.
- **Loading:** when tensorized weights exist and tensorizing is on, `build_launch_command` sets `--load-format tensorizer`. That requires an image with vLLM's tensorizer extra, which the project `Dockerfile` adds.

### 3.2. Configuration

- **CLI:** `puffin-admin model download --tensorize|--no-tensorize`, `server start --tensorize|--no-tensorize`.
- **Config key:** `use_tensorizer = true|false`; environment variable `DREAMFERENCE_USE_TENSORIZER`.
- **Recipe:** `launch_overrides["use_tensorizer"]`.

### 3.3. Clearing

`puffin-admin clear tensorize-cache` runs `ModelDownloader.clear_tensorizer_cache()`, which removes `~/.cache/dreamference/tensorizer` and nothing else, reporting root-owned leftovers as in §2.3. Until 2026-09-29 it removed all of `~/.cache/dreamference`. See `DREAMFERENCE_CLI.md` §4.18.

---

## 4. Docker vLLM Architecture

### 4.1. Requirements

- A working Docker daemon (`docker ps` must succeed), used as a member of the `docker` group.
- The NVIDIA container runtime, for `--gpus all`.

### 4.2. Container Lifecycle

**Before launch:**
1. `ensure_docker_image(image)` (§5.3).
2. Force-remove any stale `dreamference-vllm-<port>` (`docker rm -f`).

**Launch** (full flag list in `DREAMFERENCE_CODEBASE.md` §5):
```bash
docker run --ipc=host --network host --restart unless-stopped \
  --name dreamference-vllm-<port> --gpus all \
  --cpus=<n> --memory=<N>g --memory-swap=<N>g --oom-score-adj=800 \
  -v <$HF_HOME or ~/.cache/huggingface>:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 [-e HF_TOKEN=<token>] [recipe env] \
  -e VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm -e CUTE_DSL_ARCH=sm_121a -e VLLM_LOGGING_LEVEL=DEBUG \
  -e VLLM_DEBUG_LOG_API_SERVER_RESPONSE=1 -e VLLM_DEBUG_LOG_API_SERVER_REQUEST=1 \
  --entrypoint vllm <image> serve <hf_repo> [vllm flags]
```

- `--restart unless-stopped` means the server comes back after a reboot, or a daemon restart, until it is stopped explicitly.
- `--memory-swap` equals `--memory`, so the container cannot swap.

**After launch:**
- `ModelLoadingMonitor` streams the logs;
- it prints Docker memory every 10 s;
- it tracks load stages;
- it polls `/v1/models` until healthy.

**Shutdown:** `server stop` runs `docker stop`, and `server remove` runs `docker rm -f`. Both also cover the diffusion sidecar.

### 4.3. Volume Mounts

| Mount | Purpose |
| :---- | :------ |
| `~/.cache/huggingface:/root/.cache/huggingface` | Model weights |
| `~/.cache/dreamference:/root/.cache/dreamference` | Tensorized weights, plus the persistent torch.compile cache (`VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm`) |

---

## 5. vLLM Runtime Images

### 5.1. Project default: `dreamference-vllm-tensorizer:26.07-py3`

`DEFAULT_VLLM_IMAGE` is a **bare tag**, built locally from the repository's `Dockerfile`:

```dockerfile
FROM nvcr.io/nvidia/vllm:26.07-py3
RUN pip install "vllm[tensorizer]"
RUN pip install ray
RUN python -m pip install --no-cache-dir --no-deps --force-reinstall "xgrammar==0.2.4"
ENTRYPOINT ["vllm", "serve"]
```

It is NVIDIA's NGC vLLM image, which includes the May 2026 SM12x FlashInfer backends, plus the tensorizer extra, Ray and a pinned `xgrammar`.

### 5.2. Per-model images

A recipe can pin its own image in `launch_overrides["docker_image"]`, and name its engine in `launch_overrides["engine"]`. The default model and the two 122B DFlash entries do:

| Model | Image | Built from |
| --- | --- | --- |
| `qwen3.8-27b-nvfp4-dflash2` (default, SGLang) | `lmsysorg/sglang@sha256:d6e7288627be…` (pinned by digest) | Pulled from the registry, not built |
| `qwen3.5-122b-a10b-hybrid-dflash` (fallback) | `dreamference-vllm-dflash:0.23.0-aeon-dense5` | `Dockerfile.dense`, on top of `dreamference-vllm-dflash:0.23.0-aeon-kvfix2` |
| `qwen3.5-122b-a10b-int4-dflash` | `dreamference-vllm-dflash:0.23.0-aeon-dense9` | same lineage |

The DFlash vLLM chain begins at `Dockerfile.dflash`, `FROM ghcr.io/aeon-7/aeon-vllm-ultimate:2026-06-18-v0.23.0-dflashfix`, which is the AEON sm121 vLLM the DGX Spark DFlash recipe is built on. The kvfix layers bake in KV page-size unification, mamba prefix alignment and block-table fixes. The dense layer adds the Entrpi dense-bandwidth patches. These images are ~41 GB each and are built by hand with `docker build -f Dockerfile.dense …`; nothing in the CLI builds them.

The diffusion sidecar runs in the **main model's** resolved image, not in `DEFAULT_VLLM_IMAGE`.

### 5.3. How images are acquired (`ensure_docker_image`)

- **Present locally:** used as is.
- **Registry-qualified** (the name contains `/`): `docker pull`.
- **`DEFAULT_VLLM_IMAGE`:** `docker build -t <tag> -f Dockerfile .`, using the **main** `Dockerfile`, the only image it produces.
- **Any other bare tag** (the pinned `dreamference-vllm-dflash:…` images): refused with an explanation. These are built by hand from `Dockerfile.dflash` and then `Dockerfile.dense` (§5.2), before `server start`. Until 2026-09-29 they too were "built" from the main `Dockerfile`, which put the NGC engine under the DFlash tag and failed the DFlash recipe on it.

`probe_image()` never acquires an image. It reports on one already present, because it is called from `build_launch_command`, where a missing image must not start a multi-gigabyte download.

### 5.4. Container naming

- vLLM: `dreamference-vllm-<port>`;
- diffusion: `dreamference-diffusion-<port>`, default 8001.

---

## 6. Other Containers

| Container | Started by | Notes |
| --- | --- | --- |
| `dreamference-diffusion-8001` | `server start`, before vLLM | `--memory=8g`, swap equal; `diffusion_openai_service.py` |
| `puffin-api_server-1`, `puffin-web_server-1`, `puffin-relational_db-1`, `puffin-nginx-1`, `puffin-code-interpreter-1` | `puffin-admin puffin start` (Onyx Lite via `onyx-cli`) | Container names pinned to `puffin-*` in the lite overlay |
| `dreamference-gmail`, `dreamference-image-search`, `dreamference-siglip`, `dreamference-stt` | `puffin-admin puffin configure` | Sidecars **created on** Onyx's network (not joined afterwards, see below); published on loopback only (gmail 8767, image search 8768, stt 8100) |
| `dreamference-searxng` | `puffin-admin searxng start` (`SearxngSidecar`) | `127.0.0.1:8888`; created on the project's own network `dreamference-sidecars`; `configure` joins it to Onyx's network, and first recreates one still on the default bridge |
| `dreamference-openhands` | `puffin-admin run --agent openhands` | `ghcr.io/all-hands-ai/openhands:main`, pulled on demand, `--rm`, UI on **`127.0.0.1:3001`** (`OPENHANDS_HOST_PORT`): not 3000, which is Onyx's, and loopback only because the container mounts the Docker socket (`DREAMFERENCE_AGENTS.md` §5) |

**No sidecar is created on Docker's default bridge.** A container's DNS setup is fixed by the network it is *created* on, and joining another network later does not change it. On the default bridge it is a copy of the host's upstream DNS servers (`/run/systemd/resolve/resolv.conf`) taken at container start; on a user-defined network, lookups go through Docker's resolver to the host's stub resolver (`127.0.0.53`) at lookup time.

- **What failed (2026-10-01):** after a reboot, Docker restarted `dreamference-searxng` and `dreamference-stt` at 18:14:10, and the Wi-Fi received its DNS server at 18:14:15. Both containers copied an empty list (`# NO EXTERNAL NAMESERVERS DEFINED` in their `/etc/resolv.conf`), so every lookup failed and each search engine reported "HTTP connection error" until a restart by hand. Both were also on Onyx's network, as a second network, which did not help. `dreamference-gmail`, created on Onyx's network, came through the same boot unharmed.
- **The fix:** `SidecarNetwork` (`chat/sidecar_network.py`) creates `dreamference-sidecars` when needed and tells whether a container was created on the default bridge (`HostConfig.NetworkMode` `bridge` or `default`). SearXNG is created on that network, because it must work where the web UI is not installed; the speech-to-text server is created on Onyx's network, like the other `configure` sidecars. A container found on the default bridge is removed and created again: SearXNG by `puffin-admin searxng start` or `configure`, speech-to-text by `configure`. Neither holds state outside its mount or named volume.
- **No resolver is hard-coded.** A fixed `--dns` (8.8.8.8, say) would send every lookup past the machine's own resolver, and Docker's `--dns` replaces the host's list instead of adding to it.
- **Checked on this machine:** both recreated containers show `# ExtServers: [host(127.0.0.53)]` and resolve names; `puffin-search` returns results; Onyx's API server reaches `dreamference-searxng:8080` and `dreamference-stt:8000` by name. The boot race itself was not reproduced, since that needs a reboot with late DNS.

**OpenHands launch:**

```bash
docker run --rm -it --name dreamference-openhands \
  -e LLM_MODEL=openai/{hf_repo} -e LLM_BASE_URL={OnyxRunner.resolve_container_vllm_url(vllm_host)} -e LLM_API_KEY=gb10-local-token \
  -e WORKSPACE_BASE={cwd} \
  -v /var/run/docker.sock:/var/run/docker.sock -v {cwd}:/opt/workspace_base \
  -p 127.0.0.1:3001:3000 ghcr.io/all-hands-ai/openhands:main
```

`LLM_BASE_URL` has loopback rewritten to the Docker bridge gateway, as for Onyx: vLLM runs with `--network host`, and `localhost` inside the container is the container. Until 2026-09-29 the launch passed the host's `localhost` URL and published `3000:3000` on every interface.

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** launch configuration and recipes
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runners
