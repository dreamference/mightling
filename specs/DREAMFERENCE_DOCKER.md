# Mightling Docker & Model Caching

> **Version:** 1.5.1
> **Subject:** Docker vLLM Architecture, Model Downloads, Tensorization, Cache Management
> **Checked against the code:** 2026-10-09 (`hardware/model_downloader.py`, `vllm_server/vllm_server_manager.py`, `sglang_launch_builder.py`, `Dockerfile`, and every `docker run` in `chat/`, `node/`, `runner/` and `swe_bench/`)

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
   - `ling-admin init`;
   - `ling-admin server start`, for the main model, `--draft-model`, and the recipe's drafter;
   - `ling-admin model download [--model M | --all]`.

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

`ling-admin clear model-cache` runs `ModelDownloader.clear_cache()`, which removes exactly two directories:
- the hub, `~/.cache/huggingface/hub` (or `$HF_HOME/hub`), leaving the stored HF token beside it;
- the tensorizer cache, `~/.cache/dreamference/tensorizer`, leaving vLLM's compile cache, the `ling` build cache, fonts and logs.

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

- **CLI:** `ling-admin model download --tensorize|--no-tensorize`, `server start --tensorize|--no-tensorize`.
- **Config key:** `use_tensorizer = true|false`; environment variable `DREAMFERENCE_USE_TENSORIZER`.
- **Recipe:** `launch_overrides["use_tensorizer"]`.

### 3.3. Clearing

`ling-admin clear tensorize-cache` runs `ModelDownloader.clear_tensorizer_cache()`, which removes `~/.cache/dreamference/tensorizer` and nothing else, reporting root-owned leftovers as in §2.3. Until 2026-09-29 it removed all of `~/.cache/dreamference`. See `DREAMFERENCE_CLI.md` §4.18.

---

## 4. Docker vLLM Architecture

### 4.1. Requirements

- A working Docker daemon (`docker ps` must succeed), used as a member of the `docker` group.
- The NVIDIA container runtime, for `--gpus all`.

### 4.2. Container Lifecycle

**Before launch:**
1. `ensure_docker_image(image)` (§5.3).
2. Force-remove any stale `dreamference-vllm-<port>` (`docker rm -f`).

**Launch** (full flag list in `DREAMFERENCE_CODEBASE.md` §5). Both engines share the prefix up to the image (`_docker_run_prefix`), so the memory cap, CPU limit, OOM score, mounts and PSI watchdog do not depend on the engine. The main model is an `engine: sglang` recipe and runs the image's own entrypoint:
```bash
docker run --ipc=host --network host --restart unless-stopped \
  --name dreamference-vllm-<port> --gpus all \
  --cpus=<n> --memory=<N>g --memory-swap=<N>g --oom-score-adj=800 \
  -v <$HF_HOME or ~/.cache/huggingface>:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 [-e HF_TOKEN=<token>] [recipe env] \
  -e HF_HUB_OFFLINE=1 -e TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor \
  lmsysorg/sglang@sha256:… python3 -m sglang.launch_server --model-path <snapshot dir> … [recipe extra_args]
```

A vLLM recipe (none in the registry since 2026-10-07; covered by the tests) runs:
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

**Shutdown:** `server stop` runs `docker stop`, and `server remove` runs `docker rm -f`. Both also remove a leftover diffusion sidecar (diffusion is switched off since 2026-10-03).

### 4.3. Volume Mounts

| Mount | Purpose |
| :---- | :------ |
| `~/.cache/huggingface:/root/.cache/huggingface` | Model weights |
| `~/.cache/dreamference:/root/.cache/dreamference` | Tensorized weights; the persistent torch.compile caches (vLLM's `VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm`, SGLang's `sglang/inductor`); the patched chat templates (`chat-templates`) |

The container name stays `dreamference-vllm-<port>` whatever the engine, so `server stop`, the watchdog and diagnostics find it the same way.

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

A recipe can pin its own image in `launch_overrides["docker_image"]`, and name its engine in `launch_overrides["engine"]`. The main model does:

| Model | Image | Built from |
| --- | --- | --- |
| `qwen3.8-27b-nvfp4-dflash2` (SGLang) | `lmsysorg/sglang@sha256:d6e7288627be…` (v0.5.19, pinned by digest) | Pulled from the registry, not built |

**Removed 2026-10-07:** the DFlash vLLM images for the 122B recipes (`dreamference-vllm-dflash:0.23.0-aeon-dense5` and `-dense9`, ~41 GB each, built by hand from `Dockerfile.dflash` and `Dockerfile.dense` on top of the AEON sm121 vLLM) went with their recipes, and so did both Dockerfiles. Images already on a machine are not deleted by Mightling; `docker image rm` frees them.

The diffusion sidecar, when diffusion is switched on, runs in the **main model's** resolved image, not in `DEFAULT_VLLM_IMAGE`.

### 5.3. How images are acquired (`ensure_docker_image`)

- **Present locally:** used as is.
- **Registry-qualified** (the name contains `/`): `docker pull`.
- **`DEFAULT_VLLM_IMAGE`:** `docker build -t <tag> -f Dockerfile .`, using the **main** `Dockerfile`, the only image it produces.
- **Any other bare tag:** refused with an explanation, since the project `Dockerfile` builds only `DEFAULT_VLLM_IMAGE` and a pinned local tag has to be built by hand under that name. (Until 2026-09-29 such tags were "built" from the main `Dockerfile`, which put the NGC engine under the removed DFlash tag and failed its recipe.)

`probe_image()` never acquires an image. It reports on one already present, because it is called from `build_launch_command`, where a missing image must not start a multi-gigabyte download.

### 5.4. Container naming

- vLLM: `dreamference-vllm-<port>`;
- diffusion: `dreamference-diffusion-<port>`, default 8001 (not created while diffusion is switched off).

---

## 6. Other Containers

| Container | Started by | Notes |
| --- | --- | --- |
| `dreamference-diffusion-8001` | `server start`, before vLLM, **only with `DIFFUSION_ENABLED` on** (off since 2026-10-03; a leftover is removed) | `--memory=8g`, swap equal; `diffusion_openai_service.py` |
| Onyx Lite: services `api_server`, `web_server`, `relational_db`, `nginx`, `code-interpreter` | Nothing since Onyx's retirement (MIGHTLING_ASK §10, Phase C); an older install still has them | Compose project `onyx`. `ling-admin chat remove` (`RetiredWebChat`, `chat/retired_web_chat.py`) runs `docker compose -p onyx … down` without `-v`, and only on a second yes deletes the project's volumes, `onyxdotapp/*` images and networks |
| `dreamference-gmail` (the Google service) | `ling-admin google start`, and `server start` on a node when it is absent (`GoogleService`, `chat/google_service.py`) | `python:3-slim` running the staged `service.py`, as the invoking user, `127.0.0.1:8767`; created on `dreamference-sidecars`; one an older install created on Onyx's network is adopted as it is |
| `dreamference-image-search`, `dreamference-siglip` | `ling-admin images start` (`ImageSearchSidecar`, `chat/image_search_sidecar.py`) | Created on `dreamference-sidecars`, replacing one found on another network (Onyx's, on an older install); image search as the invoking user with `~/.config/dreamference/image-search` as `/config`, `127.0.0.1:8768`; SigLIP best-effort, `127.0.0.1:9100`, weights in the volume `dreamference-siglip-cache` |
| `dreamference-stt` | `ling-admin voice start` (`SpeechSidecar`, `chat/speech_sidecar.py`) | speaches on the CPU, created on `dreamference-sidecars` (one on another network is replaced), `127.0.0.1:8100`, model in the volume `dreamference-stt-cache` |
| `dreamference-matrix` | `ling-admin matrix …` (`MatrixHomeserver`, `chat/matrix_homeserver.py`); off by default | tuwunel 1.9.3 pinned by digest; `--memory=1g`, swap equal; volume `dreamference-matrix-data`; on the **internal** network `dreamference-matrix` (subnet `172.31.231.0/24`, no route out), federation off, registration by token; reached through a loopback proxy on port 6167 and Tailscale (MIGHTLING_CHAT §15) |
| `dreamference-searxng` | `ling-admin searxng start` (`SearxngSidecar`) | `127.0.0.1:8888`; created on the project's own network `dreamference-sidecars`; one still on the default bridge is recreated, rejoining any other user-defined network it was on |
| `dreamference-openhands` | `ling-admin run --agent openhands` | `ghcr.io/all-hands-ai/openhands:main`, pulled on demand, `--rm`, UI on **`127.0.0.1:3001`** (`OPENHANDS_HOST_PORT`): not 3000, which was Onyx's, and loopback only because the container mounts the Docker socket (`DREAMFERENCE_AGENTS.md` §5) |
| `mightling-swe-<run>-<instance>` (one per SWE-bench instance, labelled `ling.swe-bench.run=<run>`) | `ling-admin swe-bench run` and `smoke` (`SweBenchInstanceRun`) | The instance's own image (third-party arm64 builds, `greynewell/swe-bench-arm64`); on the **internal** network `mightling-swe-bench` (`docker network create --internal`), which reaches the model server at the network's gateway and nothing else; `--memory` and `--memory-swap` at `[swe_bench] task_memory` (8G), `--cpus` 4, `--pids-limit 4096`; the relocated `ling` mounted read-only at `/opt/ling`. Grading containers are the upstream harness's own, capped afterwards at `eval_memory` (4G) with `docker update` |

**What `ling-admin node enable` changes** (`node/node_settings.py`; `DREAMFERENCE_MIGHTLING_NODE.md` §4). On a node that is advertised, SearXNG is published on every interface instead of `127.0.0.1:8888`, and so is the web UI's port 3000 unless the node was enabled with `--no-web`; `searxng start` and `ling configure` recreate a container that is published on the other address. Port 80 never leaves loopback, and neither do the Gmail, image-search and speech-to-text sidecars. `node disable` puts both back on loopback.

**No sidecar is created on Docker's default bridge.** A container's DNS setup is fixed by the network it is *created* on, and joining another network later does not change it. On the default bridge it is a copy of the host's upstream DNS servers (`/run/systemd/resolve/resolv.conf`) taken at container start; on a user-defined network, lookups go through Docker's resolver to the host's stub resolver (`127.0.0.53`) at lookup time.

- **What failed (2026-10-01):** after a reboot, Docker restarted `dreamference-searxng` and `dreamference-stt` at 18:14:10, and the Wi-Fi received its DNS server at 18:14:15. Both containers copied an empty list (`# NO EXTERNAL NAMESERVERS DEFINED` in their `/etc/resolv.conf`), so every lookup failed and each search engine reported "HTTP connection error" until a restart by hand. Both were also on Onyx's network, as a second network, which did not help. `dreamference-gmail`, created on Onyx's network, came through the same boot unharmed.
- **The fix:** `SidecarNetwork` (`chat/sidecar_network.py`) creates `dreamference-sidecars` when needed and tells whether a container was created on the default bridge (`HostConfig.NetworkMode` `bridge` or `default`). SearXNG is created on that network, because it must work where the web UI is not installed; the speech-to-text server is created on Onyx's network, like the other `configure` sidecars. A container found on the default bridge is removed and created again: SearXNG by `ling-admin searxng start` or `configure`, speech-to-text by `configure`. Neither holds state outside its mount or named volume.
- **No resolver is hard-coded.** A fixed `--dns` (8.8.8.8, say) would send every lookup past the machine's own resolver, and Docker's `--dns` replaces the host's list instead of adding to it.
- **Checked on this machine:** both recreated containers show `# ExtServers: [host(127.0.0.53)]` and resolve names; `ling-search` returns results; Onyx's API server reaches `dreamference-searxng:8080` and `dreamference-stt:8000` by name. The boot race itself was not reproduced, since that needs a reboot with late DNS.

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
