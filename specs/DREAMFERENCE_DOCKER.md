# Mightling Docker & Model Caching

> **Version:** 1.7.2
> **Subject:** Docker vLLM Architecture, Model Downloads, Tensorization, Cache Management
> **Checked against the code:** 2026-10-28 (`hardware/model_downloader.py`, `hardware/release_asset_downloader.py`, `vllm_server/vllm_server_manager.py`, `sglang_launch_builder.py`, `ling_serve_launch_builder.py`, `model_gate.py`, `Dockerfile`, and every `docker run` in `chat/`, `forge/`, `node/`, `remote/`, `runner/` and `swe_bench/`)

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

- For Hub checkpoints, it tries `huggingface_hub.snapshot_download` first, then the `huggingface-cli` binary. For models Dreamference hosts as GitHub release assets (`RELEASE_HOSTED_MODELS` in `model_matrix_registry.py`: the searched-scale DFlash2 drafter `dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch` and `ling-engine`'s sparse head `dreamference/qwen3.8-27b-sparse-head` since 1.7.2), `ReleaseAssetDownloader` (`hardware/release_asset_downloader.py`) fetches each file from the `models-2026-10-27` release of `dreamference/mightling`, checks its SHA-256, and writes it into the hub cache under `snapshots/<tag>/` as a Hub download would.
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
2. Force-remove any stale `dreamference-vllm-<port>` (`docker rm -f`), and start the model gate (`ModelGate.start`, `dreamference-gate-<port>`) on the public port (`0.0.0.0:<port>`) so the engine listens behind it on loopback (`127.0.0.1:<port + 10000>`, i.e. `18000` for port `8000`). If the gate cannot start, `ModelGate.remove` removes it and the engine serves the public port itself.

**Launch** (full flag list in `DREAMFERENCE_CODEBASE.md` §5). All three engines (`sglang`, `ling` and `vllm`) share the prefix up to the image (`_docker_run_prefix`), so the memory cap, CPU limit, OOM score, mounts and PSI watchdog do not depend on the engine. The main model's default recipe is `engine: sglang` (with `engine: ling`, `ling-serve`, opt-in via `server start --engine ling` or `server_engine = "ling"` since 1.7.2) and runs the image's own entrypoint, with `SGLangRuntimePatcher.mount_args(...)` mounting the recipe's runtime patches (and `AdapterServing` when LoRA adapters are installed):
```bash
docker run --ipc=host --network host --restart unless-stopped \
  --name dreamference-vllm-<port> --gpus all \
  --cpus=<n> --memory=<N>g --memory-swap=<N>g --oom-score-adj=800 \
  -v <$HF_HOME or ~/.cache/huggingface>:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 [-e HF_TOKEN=<token>] [recipe env] \
  -e HF_HUB_OFFLINE=1 -e TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor \
  [SGLangRuntimePatcher mounts] \
  lmsysorg/sglang@sha256:… python3 -m sglang.launch_server --model-path <snapshot dir> … [recipe extra_args] [AdapterServing args]
```

An `engine: ling` recipe (`LingServeLaunchBuilder`, `vllm_server/ling_serve_launch_builder.py`, opt-in for the default model since 1.7.2) passes no `HF_TOKEN` and runs `ling-serve`:
```bash
docker run --ipc=host --network host --restart unless-stopped \
  --name dreamference-vllm-<port> --gpus all \
  --cpus=<n> --memory=<N>g --memory-swap=<N>g --oom-score-adj=800 \
  -v <$HF_HOME or ~/.cache/huggingface>:/root/.cache/huggingface \
  -v ~/.cache/dreamference:/root/.cache/dreamference \
  -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 \
  ghcr.io/dreamference/ling-serve@sha256:… ling-serve --model <snapshot dir> --served-model-name <hf_repo> --host <host> --port <engine_port> …
```

A vLLM recipe (`qwen3.8-flash-next-nvfp4-mtp` in the registry since 2026-10-19; also covered by the test recipes) runs:
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

**Shutdown:** `server stop` runs `docker stop` on `dreamference-vllm-<port>` and `ModelGate.stop(port)` (`dreamference-gate-<port>`), and `server remove` runs `docker rm -f` on both (`ModelGate.remove(port)`). Both also remove a leftover diffusion sidecar (diffusion is switched off since 2026-10-03).

### 4.3. Volume Mounts

| Mount | Purpose |
| :---- | :------ |
| `~/.cache/huggingface:/root/.cache/huggingface` | Model weights |
| `~/.cache/dreamference:/root/.cache/dreamference` | Tensorized weights; the persistent torch.compile caches (vLLM's `VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm`, SGLang's `sglang/inductor`); the patched chat templates (`chat-templates`); SGLang's runtime patches (`sglang-patches`, also bind-mounted over the image's target file by `SGLangRuntimePatcher`) |

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
| `qwen3.8-27b-nvfp4-dflash2` (SGLang, default; also `qwen3.8-27b-minima-nvfp4-dflash2`) | `lmsysorg/sglang@sha256:b1259f3ea327…` (v0.5.21, pinned by digest; v0.5.19 through 1.6.3; v0.5.21 from 1.6.4) | Pulled from the registry, not built |
| `qwen3.8-27b-nvfp4-dflash2` (`ling-serve`, opt-in since 1.7.2 via `engine_recipes["ling"]`) | `ghcr.io/dreamference/ling-serve@sha256:7bff4e37e731…` (`8d112d0d2539`, pinned by digest) | Built by `scripts/build_ling_serve_image.sh` (`docker/ling-serve/Dockerfile`) and pulled from GHCR |
| `qwen3.8-flash-next-nvfp4-mtp` (vLLM trial candidate, since 2026-10-19) | `vllm/vllm-openai@sha256:1ff6716fe240…` (2026-10-19 nightly, pinned by digest) | Pulled from the registry, not built |

**Removed 2026-10-07:** the DFlash vLLM images for the 122B recipes (`dreamference-vllm-dflash:0.23.0-aeon-dense5` and `-dense9`, ~41 GB each, built by hand from `Dockerfile.dflash` and `Dockerfile.dense` on top of the AEON sm121 vLLM) went with their recipes, and so did both Dockerfiles. Images already on a machine are not deleted by Mightling; `docker image rm` frees them.

The diffusion sidecar, when diffusion is switched on, runs in the **main model's** resolved image, not in `DEFAULT_VLLM_IMAGE`.

### 5.3. How images are acquired (`ensure_docker_image`)

- **Present locally:** used as is.
- **Registry-qualified** (the name contains `/`): `docker pull`.
- **`DEFAULT_VLLM_IMAGE`:** `docker build -t <tag> -f Dockerfile .`, using the **main** `Dockerfile`, the only image it produces.
- **Any other bare tag:** refused with an explanation, since the project `Dockerfile` builds only `DEFAULT_VLLM_IMAGE` and a pinned local tag has to be built by hand under that name. (Until 2026-09-29 such tags were "built" from the main `Dockerfile`, which put the NGC engine under the removed DFlash tag and failed its recipe.)

`probe_image()` never acquires an image. It reports on one already present, because it is called from `build_launch_command`, where a missing image must not start a multi-gigabyte download.

### 5.4. Container naming

- vLLM / SGLang / `ling-serve`: `dreamference-vllm-<port>`;
- model gate: `dreamference-gate-<port>` (on the public `<port>`, forwarding to the engine on `127.0.0.1:<port + 10000>`);
- diffusion: `dreamference-diffusion-<port>`, default 8001 (not created while diffusion is switched off).

---

## 6. Other Containers

| Container | Started by | Notes |
| --- | --- | --- |
| `dreamference-gate-<port>` | `server start`, before the engine (`ModelGate`, `vllm_server/model_gate.py`) | Runs `model_gate_service.py` in the engine's image (`--entrypoint python3`), `--network host`, `--memory=256m`, swap equal, `--cpus=2`, as the invoking user, `~/.local/state/dreamference/model-gate` mounted read-only at `/mightling-gate` (plus `AdapterServing.gate_mounts()`); listens on `0.0.0.0:<port>` and forwards to `127.0.0.1:<port + 10000>` |
| `dreamference-diffusion-8001` | `server start`, before vLLM, **only with `DIFFUSION_ENABLED` on** (off since 2026-10-03; a leftover is removed) | `--memory=8g`, swap equal; `diffusion_openai_service.py` |
| Onyx Lite: services `api_server`, `web_server`, `relational_db`, `nginx`, `code-interpreter` | Nothing since Onyx's retirement (MIGHTLING_ASK §10, Phase C); an older install still has them | Compose project `onyx`. `ling-admin chat remove` (`RetiredWebChat`, `chat/retired_web_chat.py`) runs `docker compose -p onyx … down` without `-v`, and only on a second yes deletes the project's volumes, `onyxdotapp/*` images and networks |
| `dreamference-gmail` (the Google service) | `ling-admin google start`, and `server start` on a node when it is absent (`GoogleService`, `chat/google_service.py`) | `python:3-slim` running the staged `service.py`, as the invoking user, `127.0.0.1:8767`; created on `dreamference-sidecars`; one an older install created on Onyx's network is adopted as it is |
| `dreamference-image-search`, `dreamference-siglip` | `ling-admin images start` (`ImageSearchSidecar`, `chat/image_search_sidecar.py`); image search alone (no SigLIP, no health wait) by `server start` on a node when absent | Created on `dreamference-sidecars`, replacing one found on another network (Onyx's, on an older install); image search as the invoking user with `~/.config/dreamference/image-search` as `/config`, `127.0.0.1:8768`; SigLIP best-effort, `127.0.0.1:9100`, weights in the volume `dreamference-siglip-cache` |
| `dreamference-stt` | `ling-admin voice start` (`SpeechSidecar`, `chat/speech_sidecar.py`), and `server start` on a node when absent (the model download detached) | speaches on the CPU, created on `dreamference-sidecars` (one on another network is replaced), `127.0.0.1:8100`, model in the volume `dreamference-stt-cache` |
| `dreamference-matrix` | `ling-admin matrix …` (`MatrixHomeserver`, `chat/matrix_homeserver.py`); off by default | tuwunel 1.9.3 pinned by digest; `--memory=1g`, swap equal; volume `dreamference-matrix-data`; on the **internal** network `dreamference-matrix` (subnet `172.31.231.0/24`, no route out), federation off, registration by token; reached through a loopback proxy on port 6167 and Tailscale (MIGHTLING_CHAT §15) |
| `dreamference-searxng` | `ling-admin searxng start` (`SearxngSidecar`) | `127.0.0.1:8888`; created on the project's own network `dreamference-sidecars`; one still on the default bridge is recreated, rejoining any other user-defined network it was on |
| `mightling-forge` | `ling-admin forge on\|run` (`ForgeContainer`, `forge/forge_container.py`; user unit `mightling-forge.service`, on by default on a node) | `codeberg.org/forgejo/forgejo:15.0.11-rootless@sha256:3cd6bc2b74c8…`, `--rm` under the user unit, `--memory=1g`, swap equal, `--cpu-shares=256`; on the user-defined network `mightling-forge`; HTTP port 3200 (also proxied at `/forge/` on `ling web` port 3100) and SSH port 2222 published on loopback and the advertised LAN addresses (`ForgeBind`) |
| `dreamference-remote` | `ling-admin remote setup\|start` (`RemoteControlPlane`, `remote/remote_control_plane.py`); off by default | `netbirdio/netbird-server:0.80.0@sha256:05b3d8d6d056…`; on the **internal** network `dreamference-remote` (subnet `172.31.232.0/24`, IP `172.31.232.10`, no route out); reached through a loopback `systemd-socket-proxyd` on `127.0.0.1:33443` and the reverse SSH tunnel to the relay box (`RemoteBox` also runs a short-lived `docker create`/`cp`/`rm` on `netbirdio/relay@sha256:…` to extract `/go/bin/netbird-relay`) |
| `dreamference-openhands` | `ling-admin run --agent openhands` | `ghcr.io/all-hands-ai/openhands:main`, pulled on demand, `--rm`, UI on **`127.0.0.1:3001`** (`OPENHANDS_HOST_PORT`): not 3000, which was Onyx's, and loopback only because the container mounts the Docker socket (`DREAMFERENCE_AGENTS.md` §5) |
| `mightling-swe-<run>-<instance>` (one per SWE-bench instance, labelled `ling.swe-bench.run=<run>`) | `ling-admin swe-bench run` and `smoke` (`SweBenchInstanceRun`) | The instance's own image (third-party arm64 builds, `greynewell/swe-bench-arm64`); on the **internal** network `mightling-swe-bench` (`docker network create --internal`), which reaches the model server at the network's gateway and nothing else; `--memory` and `--memory-swap` at `[swe_bench] task_memory` (8G), `--cpus` 4, `--pids-limit 4096`; the relocated `ling` mounted read-only at `/opt/ling`. Grading containers are the upstream harness's own, capped afterwards at `eval_memory` (4G) with `docker update` |

**What `ling-admin node enable` changes** (`node/node_settings.py`; `DREAMFERENCE_MIGHTLING_NODE.md` §4). On a node that is advertised, SearXNG is published on every interface instead of `127.0.0.1:8888`, and so is the web UI's port 3100 (`ling web`; port 3000 until Onyx's retirement in 1.7.0) unless the node was enabled with `--no-web`; `searxng start` recreates a container that is published on the other address (as `chat configure` did for Onyx before 1.7.0). The Gmail, image-search and speech-to-text sidecars never leave loopback. `node disable` puts both back on loopback.

**No sidecar is created on Docker's default bridge.** A container's DNS setup is fixed by the network it is *created* on, and joining another network later does not change it. On the default bridge it is a copy of the host's upstream DNS servers (`/run/systemd/resolve/resolv.conf`) taken at container start; on a user-defined network, lookups go through Docker's resolver to the host's stub resolver (`127.0.0.53`) at lookup time.

- **What failed (2026-10-01):** after a reboot, Docker restarted `dreamference-searxng` and `dreamference-stt` at 18:14:10, and the Wi-Fi received its DNS server at 18:14:15. Both containers copied an empty list (`# NO EXTERNAL NAMESERVERS DEFINED` in their `/etc/resolv.conf`), so every lookup failed and each search engine reported "HTTP connection error" until a restart by hand. Both were also on Onyx's network, as a second network, which did not help. `dreamference-gmail`, created on Onyx's network, came through the same boot unharmed.
- **The fix:** `SidecarNetwork` (`chat/sidecar_network.py`) creates `dreamference-sidecars` when needed and tells whether a container was created on the default bridge (`HostConfig.NetworkMode` `bridge` or `default`). Since Onyx's retirement in 1.7.0, SearXNG, the Google service, image search/SigLIP and speech-to-text are all created on `dreamference-sidecars` (earlier, speech-to-text and the other `configure` sidecars were created on Onyx's network). A container found on the default bridge (or on Onyx's network, for image search and speech-to-text) is removed and created again. Neither holds state outside its mount or named volume.
- **No resolver is hard-coded.** A fixed `--dns` (8.8.8.8, say) would send every lookup past the machine's own resolver, and Docker's `--dns` replaces the host's list instead of adding to it.
- **Checked on this machine:** both recreated containers show `# ExtServers: [host(127.0.0.53)]` and resolve names; `ling-search` returns results; Onyx's API server reaches `dreamference-searxng:8080` and `dreamference-stt:8000` by name. The boot race itself was not reproduced, since that needs a reboot with late DNS.

**OpenHands launch:**

```bash
docker run --rm -it --name dreamference-openhands \
  -e LLM_MODEL=openai/{hf_repo} -e LLM_BASE_URL={DockerBridge.container_vllm_url(vllm_host)} -e LLM_API_KEY=gb10-local-token \
  -e WORKSPACE_BASE={cwd} \
  -v /var/run/docker.sock:/var/run/docker.sock -v {cwd}:/opt/workspace_base \
  -p 127.0.0.1:3001:3000 ghcr.io/all-hands-ai/openhands:main
```

`LLM_BASE_URL` has loopback rewritten to the Docker bridge gateway (`DockerBridge`, `chat/docker_bridge.py`; Onyx's provider had the same rewrite): vLLM runs with `--network host`, and `localhost` inside the container is the container. Until 2026-09-29 the launch passed the host's `localhost` URL and published `3000:3000` on every interface.

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** launch configuration and recipes
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runners
