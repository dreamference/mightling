# Puffin System Requirements & Setup

> **Version:** 1.2.0
> **Subject:** Installation, Hardware Detection, Quickstart, Helper Scripts
> **Checked against the code:** 2026-09-29 (`setup.py`, `scripts/`, `dreamference/`)

---

## Table of Contents

- [1. System Requirements](#1-system-requirements)
- [2. Hardware Identification](#2-hardware-identification)
- [3. Installation](#3-installation)
- [4. Helper Scripts](#4-helper-scripts)
- [5. Troubleshooting](#5-troubleshooting)
- [6. Post-Installation Checks](#6-post-installation-checks)

---

## 1. System Requirements

### 1.1. Hardware

- **NVIDIA GB10** (Blackwell SM121, 128 GB unified LPDDR5X, Arm `aarch64` CPU). Any host with ≥ 100 GB RAM also qualifies by the detection heuristic (§2), but the recipes and images target SM121.
- NVMe storage: the model caches plus the DFlash vLLM images (~41 GB each) take hundreds of GB.

### 1.2. Operating System and Drivers

- Linux ARM64 (Ubuntu; this machine runs a 6.17 NVIDIA kernel).
- The NVIDIA driver and the NVIDIA Container Toolkit, for `docker run --gpus all`.
- **Memory-safety prerequisites**, which `check_host_safety()` checks before a model load and aborts without: swap, and `earlyoom` or `systemd-oomd` configured. See `DREAMFERENCE_INFERENCE.md` §7.

### 1.3. Software

- **Python:** 3.10 or newer; development and tests run on 3.12. `setup.py` declares no `python_requires`.
- **Python dependencies** (`setup.py` `install_requires`): `pyyaml`, `toml`, `rich`, `requests`, `sentence-transformers`, `sqlite-vec`, `tensorizer`, `einops`, `fonttools[woff]`, `Pillow`, `beautifulsoup4`.
- **Docker**, which is required: the model server runs only in Docker.
- **For `puffin`:**
  - `git` with submodules (`codex/`), and a Rust toolchain via rustup (`puffin-admin codex build` installs rustup if missing);
  - `perl` and a C compiler, because OpenSSL is built from source;
  - no `libssl-dev` or `libcap-dev` is needed.
- **For the web UI:** `onyx-cli`, installed via pip by `OnyxInstaller` when missing.
- **For the desktop window:** GTK/WebKit 4.1 development headers, Rust and the Tauri CLI. `puffin-admin desktop install` fetches all three; the headers need `sudo apt-get`.
- **Optional agents:**
  - VS Code / VSCodium, for Cline and Continue;
  - `aider-chat`, for Aider;
  - Apptainer or Podman, for Goose sandboxing.

### 1.4. Privileges

- Docker through `docker` group membership, without `sudo`.
- Write access to `~/.cache/`, `~/.config/`, `~/.local/`.
- `sudo` only for the desktop's system packages (prompted, and visible before it runs).

---

## 2. Hardware Identification

`HardwareManager` reads `/proc/meminfo` and `nvidia-smi --query-gpu=name,driver_version,memory.total`. The machine qualifies as GB10 when:
- the GPU name contains `GB10` or `BLACKWELL`; **or**
- total memory ≥ 100 GB. If no GPU name is available, it is reported as "NVIDIA GB10 (Simulated / Unified Memory Node)".

There is no override variable. The `DREAMFERENCE_GB10_OVERRIDE` this document used to describe does not exist.

```bash
nvidia-smi --query-gpu=name,memory.total --format=csv
free -h
puffin-admin status        # "System Target" row
```

---

## 3. Installation

### 3.1. Recommended manual install

```bash
git clone --recurse-submodules https://github.com/dgxcoder/dgxcoder.git
cd dgxcoder                      # the codex/ submodule is shallow; add --depth 1 on update if preferred

python3 -m venv .venv
.venv/bin/pip install -e .       # installs the `puffin-admin` console script into .venv/bin

.venv/bin/puffin-admin init                  # default model qwen3.5-122b-a10b-hybrid-dflash: downloads weights,
                                             # writes dreamference.toml and the Goose config, indexes the workspace
.venv/bin/puffin-admin codex build           # builds puffin from codex/ + codex-patches/ + puffin-rs/,
                                             # links ~/.local/bin/puffin (first build: long; later: incremental)
.venv/bin/puffin-admin server start          # vLLM + diffusion sidecar; exits when healthy
puffin                                       # the terminal agent
```

**Extras:**
- **Web UI:** `puffin-admin puffin start`, then `puffin-admin puffin configure`.
- **Desktop window:** `puffin-admin desktop install`, then `puffin-admin desktop run`, or `puffin app`.

**Model images.** The default model pins `dreamference-vllm-dflash:0.23.0-aeon-dense5`, a locally built image. It is not pulled and not built automatically: build it from `Dockerfile.dflash` → `Dockerfile.dense` before the first `server start` (`DREAMFERENCE_DOCKER.md` §5.2–5.3).

### 3.2. Prebuilt `puffin`

A published GitHub release carries `puffin` and `codex-code-mode-host` for linux-arm64. Once a release exists, `puffin update` installs or refreshes them, and a source checkout is then only needed for `puffin-admin`. As of 2026-09-28 no release has been published.

### 3.3. `scripts/install_gb10.sh`

```bash
./scripts/install_gb10.sh [MODEL]
```

§3.1 in one script, every step done by `puffin-admin`:
1. `git submodule update --init codex`.
2. `python3 -m pip install -e .` into whatever environment is active.
3. `puffin-admin init`, with `--model MODEL` if one is given; otherwise the registry's default model.
4. `puffin-admin codex build`, so `puffin` exists when it finishes.
5. Tells you to run `puffin-admin server start`, then `puffin`.

Until 2026-09-29 it defaulted to `qwen3.6-35b-a3b-nvfp4`, installed Goose, and never built `puffin`.

---

## 4. Helper Scripts

All in `scripts/` at the repository root.

### 4.1. `scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT] [TOKENS]`

A thin wrapper over `puffin-admin server start`: each argument given becomes `--model`, `--port`, `--draft-model` or `--num-speculative-tokens`, and anything omitted takes the configured value. The launch therefore gets what the CLI gives it — the alias resolved to its HF repo, the registry recipe and pinned image, the host-safety checks and the PSI watchdog.

Until 2026-09-29 it ran `python3 -m vllm.entrypoints.openai.api_server` directly, outside Docker, with the alias unresolved and the removed `--speculative-model` flags.

### 4.2. `scripts/run_goose.sh`

```bash
./scripts/run_goose.sh "task prompt"
```

Runs `puffin-admin run --agent goose "$@"`, which checks the server, writes Goose's config for the served model id and provisions Goose if it is missing (`DREAMFERENCE_AGENTS.md`). Until 2026-09-29 it exported `GOOSE_MODEL` as the alias, which vLLM does not serve, and called `goose` directly.

---

## 5. Troubleshooting

| Problem | Fix |
| --- | --- |
| `docker ps` fails | `sudo apt install docker.io`; `sudo usermod -aG docker $USER`; log in again |
| `--gpus all` fails | Install the NVIDIA Container Toolkit and restart Docker |
| `server start` aborts in the host-safety pre-flight | Read the printed reason. Usually swap is missing, or `earlyoom`/`systemd-oomd` isn't configured, or the model doesn't fit current free memory |
| `puffin-admin: command not found` | It lives in `.venv/bin/`: use the full path, or add `.venv/bin` to `PATH` |
| `puffin: command not found` | `puffin-admin codex build`, then make sure `~/.local/bin` is on `PATH` |
| `puffin-admin codex build` says the submodule is not checked out | `git submodule update --init codex` |
| `puffin` waits forever "for local vLLM server" | `puffin-admin server start`; check `DREAMFERENCE_VLLM_HOST` / `vllm_host` |
| First `puffin-admin index` fails offline | The nomic embedding model is downloaded on first use; fetch it while online (`DREAMFERENCE_CONTEXT.md` §5) |

---

## 6. Post-Installation Checks

```bash
puffin-admin status                 # GB10 qualified; vLLM health; agent CLIs present; context index
puffin-admin model list             # the model matrix
puffin --version                    # "puffin 0.158.0"
puffin exec "say hello"             # a one-shot answer from the local model (needs the server)
puffin-admin index --force          # (re)builds .dreamference/ in the current directory
.venv/bin/python -m pytest tests/ -q   # 364 passed, 63 skipped without a model server (2026-09-30)
```

---

## See Also

- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI reference
- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** building `puffin`
- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agents
