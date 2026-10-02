# Puffin System Requirements & Setup

> **Version:** 1.2.0
> **Subject:** Installation, Hardware Detection, Quickstart, Helper Scripts
> **Checked against the code:** 2026-10-01 (`setup.py`, `scripts/`, `dreamference/`)

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
- NVMe storage: the model caches plus the model-server images (the fallback's DFlash vLLM images are ~41 GB each) take hundreds of GB.

### 1.2. Operating System and Drivers

- Linux ARM64 (Ubuntu; this machine runs a 6.17 NVIDIA kernel).
- The NVIDIA driver and the NVIDIA Container Toolkit, for `docker run --gpus all`.
- **Memory-safety prerequisites**, which `check_host_safety()` checks before a model load and aborts without: sysstat, 64 GB of swap, two raised sysctls, and `earlyoom` or `systemd-oomd` armed. `puffin-admin host setup` applies them (§3.3); `puffin-admin host check` only reports. See `DREAMFERENCE_INFERENCE.md` §7.

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
- **For the code index:** `puffin-admin code setup` installs the pinned tools `puffin-code` runs; Go, a JDK 17+ and a .NET SDK 8+ are optional and only enable their languages' exact indexers.
- **For Night Shift:** a systemd user session (`puffin-admin night enable` installs a user timer; lingering must be on for it to fire while logged out).
- **Optional agents:**
  - VS Code / VSCodium, for Cline and Continue;
  - Docker, for OpenHands.

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

.venv/bin/puffin-admin init                  # default model qwen3.8-27b-nvfp4-dflash2: downloads weights,
                                             # writes dreamference.toml, indexes the workspace
.venv/bin/puffin-admin codex build           # builds puffin from codex/ + codex-patches/ + puffin-rs/,
                                             # also puffin-search, puffin-fetch and puffin-code; links them into
                                             # ~/.local/bin (first build: long; later: incremental)
.venv/bin/puffin-admin server start          # model server (SGLang for the default) + diffusion sidecar; exits when healthy
puffin                                       # the terminal agent
```

**Extras:**
- **Web UI:** `puffin-admin puffin start`, then `puffin-admin puffin configure`.
- **Desktop window:** `puffin-admin desktop install`, then `puffin-admin desktop run`, or `puffin app`.
- **Web search for `puffin`:** `puffin-admin searxng start` (the web UI's `configure` also sets it up).
- **Code index:** `puffin-admin code setup`.
- **Night Shift:** `puffin-admin night enable`; tasks are queued from `puffin` with `/night add`.

**Model images.** The default model pins `lmsysorg/sglang` by digest; being registry-qualified, it is pulled by `server start` when missing. The fallback `qwen3.5-122b-a10b-hybrid-dflash` pins `dreamference-vllm-dflash:0.23.0-aeon-dense5`, a locally built image. That one is not pulled and not built automatically: build it from `Dockerfile.dflash` → `Dockerfile.dense` before the first `server start --model qwen3.5-122b-a10b-hybrid-dflash` (`DREAMFERENCE_DOCKER.md` §5.2–5.3).

### 3.2. Install from a release: `install.sh`

No checkout and nothing compiled. `install.sh` (repository root; attached to every release from the one after v1.3.0) installs from a published release:

```bash
export GH_TOKEN=...          # while the repository is private; a logged-in `gh` also works
gh release download -R dgxcoder/dgxcoder -p install.sh && bash install.sh [--role client|node] [--version X.Y.Z]
```

- **The machine decides the role.** A GB10 (arm64 Linux whose `nvidia-smi --query-gpu=name` says `GB10`) gets the **node**; everything else gets the **client**. `--role` overrides it. The device tree has no model string on this hardware and the DMI product name is the vendor's (`GX10` on the machine this was written on), so neither is used.
- **Client:** `puffin`, `codex-code-mode-host`, and, when the release carries them, `puffin-search`, `puffin-fetch` and `puffin-code`. They are the same assets `puffin update` downloads, by the same names, every archive checked against `puffin-<target>.sha256sums` before any is placed. They go to `~/.local/share/dreamference/puffin/bin`, linked into `~/.local/bin`; a real file of the same name there is left alone.
- **Node:** the client, then the release's wheel into a virtualenv of its own (`~/.local/share/dreamference/venv`), `puffin-admin` linked into `~/.local/bin`, then `puffin-admin host setup` (§3.3). It does not download a model or start anything: `puffin-admin server start` does that.
- **Token.** `GH_TOKEN`, `GITHUB_TOKEN` or `gh auth token`, sent to the API and to the asset downloads. Without one, on a private repository, the script stops and says so. `PUFFIN_RELEASE_REPO` names a fork.
- **Targets.** Releases carry linux-arm64 only. On another machine the script stops with the targets the release does have.

**What a release install cannot do**, and says so instead of failing in the middle: `puffin-admin codex build` (there is no source; `puffin update` installs a newer release), `puffin-admin desktop build|install` (the release's `.deb` or AppImage is the app; `desktop run` opens it when installed), and building the plain `Dockerfile` image for a recipe that does not pull its own. `CodexBrandedBuilder.has_source()` is the test: with no `codex-patches/` and `puffin-rs/` beside the package, installed binaries count as current. Until 2026-10-02 they counted as stale, and `puffin-admin run` installed rustup and then died on the missing `puffin-web-rs/` directory.

**Verified on 2026-10-02**, on this GB10, in a scratch home with no checkout on any path:
- **The gap, measured first.** The v1.3.0 wheel in a fresh virtualenv: installing it took 113 s and 5.8 GB (PyTorch is most of it). `--help`, `status` and `night status` worked. `codex build` and `run "…"` installed a 1.4 GB Rust toolchain and then raised `FileNotFoundError` on `site-packages/puffin-web-rs`. The wheel is missing no files: the package has no data files, so there is no `MANIFEST.in` to add.
- **Client, against the real v1.3.0 release:** 23 s, about 155 MB downloaded, checksums passed. `puffin --version`, a `puffin-search` query and one `puffin exec` turn against the model server all worked from that home.
- **Node, with no `--role`:** the script chose `node` here. Against a stand-in release holding v1.3.0's real binaries and a wheel built from this tree: 124 s; binaries, virtualenv and `puffin-admin` in place, and the host step reported nothing to do and ran no sudo. From that home, `puffin-admin codex build` reported the release's binaries and built nothing (and, with `puffin` removed, said how to install it); `desktop build` and `desktop install` pointed at the release's `.deb`; `puffin-admin run` went straight to `puffin` with no Rust toolchain installed.

**Not verified:** a second machine or a second user account; a fresh GB10 where `host setup` has real work (its commands run only in tests, with sudo mocked); `install.sh` downloaded from a release (the next release is the first to carry it); a non-GB10 client (no release has x86 or macOS binaries); and `server start` after a release install, which was not run because a model server is already resident here.

### 3.3. Host settings: `puffin-admin host check|setup`

`check_host_safety()` refuses a model load without them and, until 2026-10-02, only printed the commands. `HostSafetySetup` (`vllm_server/host_safety_setup.py`) takes every reading from the check's own helpers, so the two cannot disagree, and applies:

| Check | Fix |
|---|---|
| `sar` missing | install sysstat, enable collection |
| no OOM handler | install earlyoom, write `EARLYOOM_ARGS="-m 5,2 -s 100 -r 60"`, enable, restart |
| earlyoom running with arguments that cannot fire here | rewrite the arguments, restart |
| swap under 64 GB | resize `/swap.img` (or create it, with its fstab line) |
| `vm.min_free_kbytes`, `vm.watermark_scale_factor` too low | `sysctl -w`, and `/etc/sysctl.d/99-dreamference.conf` |

- Each command is printed, then run through `sudo`, which asks on the terminal. With no terminal nothing runs and the commands are printed.
- **Swap is resized only when it is the single `/swap.img` Ubuntu sets up.** A partition, zram, or several areas are reported and left alone. So is a root filesystem without 64 GB and 20 GB to spare, and so is swap holding more than fits back into memory (`swapoff` would have to move it there).
- A failed command stops its step; the host is read again at the end and what remains is listed.
- On this machine both commands report nothing to do and run no sudo.

### 3.4. `scripts/install_gb10.sh`

```bash
./scripts/install_gb10.sh [MODEL]
```

§3.1 in one script, every step done by `puffin-admin`:
1. `git submodule update --init codex`.
2. `python3 -m pip install -e .` into whatever environment is active.
3. `puffin-admin init`, with `--model MODEL` if one is given; otherwise the registry's default model.
4. `puffin-admin host setup` (§3.3).
5. `puffin-admin codex build`, so `puffin` exists when it finishes.
6. Tells you to run `puffin-admin server start`, then `puffin`.

Until 2026-09-29 it defaulted to `qwen3.6-35b-a3b-nvfp4` and never built `puffin`.

---

## 4. Helper Scripts

All in `scripts/` at the repository root.

### 4.1. `scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT] [TOKENS]`

A thin wrapper over `puffin-admin server start`: each argument given becomes `--model`, `--port`, `--draft-model` or `--num-speculative-tokens`, and anything omitted takes the configured value. The launch therefore gets what the CLI gives it — the alias resolved to its HF repo, the registry recipe and pinned image, the host-safety checks and the PSI watchdog.

Until 2026-09-29 it ran `python3 -m vllm.entrypoints.openai.api_server` directly, outside Docker, with the alias unresolved and the removed `--speculative-model` flags.

---

## 5. Troubleshooting

| Problem | Fix |
| --- | --- |
| `docker ps` fails | `sudo apt install docker.io`; `sudo usermod -aG docker $USER`; log in again |
| `--gpus all` fails | Install the NVIDIA Container Toolkit and restart Docker |
| `server start` aborts in the host-safety pre-flight | `puffin-admin host setup` applies the fixes it lists. If it still aborts, the model doesn't fit current free memory |
| `puffin-admin: command not found` | It lives in `.venv/bin/`: use the full path, or add `.venv/bin` to `PATH` |
| `puffin: command not found` | `puffin-admin codex build` in a checkout, or `install.sh` without one; then make sure `~/.local/bin` is on `PATH` |
| `puffin-admin codex build` says the submodule is not checked out | `git submodule update --init codex` |
| `puffin` waits forever "for local vLLM server" | `puffin-admin server start`; check `DREAMFERENCE_VLLM_HOST` / `vllm_host` |
| `puffin-search` says every engine failed with a connection error after a reboot | The SearXNG container was created on Docker's default bridge and started before the host had DNS: `puffin-admin searxng start` recreates it on the sidecar network (`DREAMFERENCE_DOCKER.md` §6) |
| First `puffin-admin index` fails offline | The nomic embedding model is downloaded on first use; fetch it while online (`DREAMFERENCE_CONTEXT.md` §5) |

---

## 6. Post-Installation Checks

```bash
puffin-admin status                 # GB10 qualified; vLLM health; agent CLIs present; context index
puffin-admin model list             # the model matrix
puffin --version                    # "puffin 0.158.0"
puffin exec "say hello"             # a one-shot answer from the local model (needs the server)
puffin-admin index --force          # (re)builds .dreamference/ in the current directory
.venv/bin/python -m pytest tests/ -q   # 521 tests on 2026-10-01; 63 of them need a running model server and are
                                       # skipped without one (the full run then takes ~13 minutes instead of ~25 s)
```

---

## See Also

- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI reference
- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** building `puffin`
- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agents
