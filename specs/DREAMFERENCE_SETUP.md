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

- **NVIDIA GB10** (Blackwell SM121, 128 GB unified LPDDR5X, Arm `aarch64` CPU), in any of the eight machines built on it (§3.5): NVIDIA's DGX Spark and the Acer, ASUS, Dell, Gigabyte, HP, Lenovo and MSI boxes.
- NVMe storage: the default model (~20 GB of weights, the SGLang image) fits a 1 TB drive, the smallest any GB10 machine ships with; the fallback's DFlash vLLM images are ~41 GB each, and SWE-bench keeps 100 GB free.

### 1.2. Operating System and Drivers

- Linux ARM64: DGX OS 7 (Ubuntu 24.04 underneath), which every GB10 machine ships with, or Ubuntu 24.04 with NVIDIA's packages where the vendor documents it (HP, Lenovo). This machine runs DGX OS 7.5.0 with the 6.17 NVIDIA kernel and driver 580.
- The NVIDIA driver and the NVIDIA Container Toolkit, for `docker run --gpus all`. DGX OS ships both, Docker, Avahi and bubblewrap; on plain Ubuntu `puffin-admin host check` names what is missing (§3.3).
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
- the GPU name contains `GB10`; **or**
- the PCI bus has the GB10's GPU, vendor `0x10de` device `0x2e12` (added 2026-10-06: a machine whose driver is not installed yet has no `nvidia-smi` to ask; `install.sh`'s `is_gb10` has the same fallback).

Nothing else qualifies. Until 2026-10-06 a GPU name containing `BLACKWELL` or total memory ≥ 100 GB did too, which counted an RTX PRO 6000 Blackwell workstation or any large x86 server as a GB10: `server start` then went on to SM121 recipes, and `node enable` advertised it.

The vendor is never matched on: every GB10 machine names itself differently in DMI (`ASUSTeK COMPUTER INC.` / `GX10` here; the DGX Spark reports `NVIDIA` / `NVIDIA_DGX_Spark`). `puffin-admin status` shows it as **Machine** (DMI vendor and product) and **Operating System** (`DGX OS <version> (<Ubuntu>)` from `/etc/dgx-release`, or `/etc/os-release` alone), so a report says whose box it came from.

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
git clone --recurse-submodules https://github.com/dreamference/puffin.git
cd dgxcoder                      # the codex/ submodule is shallow; add --depth 1 on update if preferred

python3 -m venv .venv
.venv/bin/pip install -e .       # installs the `puffin-admin` console script into .venv/bin

.venv/bin/puffin-admin init                  # default model qwen3.8-27b-nvfp4-dflash2: downloads weights,
                                             # writes dreamference.toml, indexes the workspace
.venv/bin/puffin-admin codex build           # builds puffin from codex/ + codex-patches/ + puffin-rs/,
                                             # also puffin-search, puffin-fetch and puffin-code; links them into
                                             # ~/.local/bin (first build: long; later: incremental)
.venv/bin/puffin-admin server start          # model server (SGLang for the default); exits when healthy
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
gh release download -R dreamference/puffin -p install.sh && bash install.sh [--role client|node] [--version X.Y.Z]
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
- **Node, with no `--role`:** the script chose `node` here. Against a stand-in release holding v1.3.0's real binaries and a wheel built from this tree: 124 s; binaries, virtualenv and `puffin-admin` in place, and the host step reported nothing to do and ran no sudo (that was before the sandbox check of §3.3 existed). From that home, `puffin-admin codex build` reported the release's binaries and built nothing (and, with `puffin` removed, said how to install it); `desktop build` and `desktop install` pointed at the release's `.deb`; `puffin-admin run` went straight to `puffin` with no Rust toolchain installed.

**Not verified:** a second machine or a second user account; `puffin` from a release install in a plain terminal (every run above was from the IDE's terminal, where the sandbox worked even before this machine had the AppArmor profile of §3.3, loaded on 2026-10-03); a fresh GB10 where `host setup` has real work (its commands run only in tests, with sudo mocked); `install.sh` downloaded from a release (the next release is the first to carry it); a non-GB10 client (no release has x86 or macOS binaries); and `server start` after a release install, which was not run because a model server is already resident here.

### 3.3. Host settings: `puffin-admin host check|setup`

`check_host_safety()` refuses a model load without them and, until 2026-10-02, only printed the commands. `HostSafetySetup` (`vllm_server/host_safety_setup.py`) takes every reading from the check's own helpers, so the two cannot disagree, and applies:

| Check | Fix |
|---|---|
| `sar` missing | install sysstat, enable collection |
| no OOM handler | install earlyoom, write `EARLYOOM_ARGS="-m 5,2 -s 100 -r 60"`, enable, restart |
| earlyoom running with arguments that cannot fire here | rewrite the arguments, restart |
| swap on disk under 64 GB (zram is not counted) | resize `/swap.img` (or create it, with its fstab line) |
| `vm.min_free_kbytes`, `vm.watermark_scale_factor` too low | `sysctl -w`, and `/etc/sysctl.d/99-dreamference.conf` |
| no `docker` | explained: Docker Engine and the NVIDIA Container Toolkit, with NVIDIA's and Docker's install pages |
| no NVIDIA Container Toolkit (`nvidia-ctk`, its hook or `nvidia-container-cli`) | explained: install from NVIDIA's repository, `nvidia-ctk runtime configure --runtime=docker`, restart Docker |
| the user cannot reach the Docker socket and is not in the `docker` group | `usermod -aG docker <user>` (effective at the next login) |
| no `bwrap` | `apt-get install -y bubblewrap`, and the AppArmor profile below in the same step where the restriction is on |
| `bwrap` refused a user namespace by AppArmor (from a transient user unit) | install `/etc/apparmor.d/puffin-bwrap`, `apparmor_parser -r` it |

- Each command is printed, then run through `sudo`, which asks on the terminal. With no terminal nothing runs and the commands are printed.
- **Swap is resized only when it is the single `/swap.img` Ubuntu sets up.** A partition or several areas are reported and left alone. zram is neither counted nor in the way (2026-10-06): its pages stay in the RAM a model load is short of, so `_swap_total_gb()` reads disk-backed areas from `/proc/swaps`, and a `/swap.img` beside zram is resized as if alone. So is a root filesystem without 64 GB and 20 GB to spare, and so is swap holding more than fits back into memory (`swapoff` would have to move it there).
- A failed command stops its step; the host is read again at the end and what remains is listed.
- On this machine the four model-load checks pass and no sudo runs.
- **One more check, and its fix: bubblewrap's sandbox.** Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, and with no AppArmor profile exempting `/usr/bin/bwrap`, `bwrap` is refused a user namespace. Measured here on 2026-10-02: it works from the PyCharm terminal, whose processes carry the snap's AppArmor label, and fails from a systemd user unit (so also a plain terminal, an SSH login and the Night Shift timer) with `setting up uid map: Permission denied`; `puffin sandbox` fails there too. Every sandbox Puffin starts uses that `bwrap` (Codex prefers the system's bubblewrap on PATH; the code indexers and node jobs call it by name). `host check` tries `bwrap` from a transient user unit; `host setup` now fixes it by installing `/etc/apparmor.d/puffin-bwrap`, a profile for `/usr/bin/bwrap` alone in the shape of Ubuntu's own for sandboxing programs (`chrome`, `linux-sandbox`: `flags=(unconfined)` and `userns,`), and loading it with `apparmor_parser -r`. The sysctl set to 0 would also work, for every program on the machine, and is not offered. Where the restriction is not the cause (the sysctl is not 1), the step stays a manual one.
- **The same check runs on every `puffin-admin` run** (`SandboxPrerequisite`, `vllm_server/sandbox_prerequisite.py`; added 2026-10-03). The probe is one transient unit, about 25 ms. When `bwrap` is refused and someone is at a terminal, it asks: **1** install the profile now (each command printed, sudo asks for the password), **2** turn off what needs it, **3** not now (the default, asked again next run). "Turn off" removes the Night Shift timer, refuses `night enable` and `night run`, and is remembered in `~/.config/dreamference/sandbox.json`, so the question is not asked again; once `bwrap` works (after `host setup`) the file is removed and the user is told to `night enable` again. With nobody at a terminal (the Night Shift timer, a script) it prints one line on stderr and refuses only `night run`/`night enable`, whose tasks would otherwise each fail inside a sandbox that cannot start. It never asks for `mcp`, `host` or `node serve-job` (protocols on stdio, or the command that reports it anyway), nor in a command `puffin` itself runs (`CODEX_THREAD_ID`/`CODEX_SANDBOX` set). **Verified on 2026-10-03:** the user ran `host setup` and the profile was loaded on this machine. From a throwaway systemd user unit, `bwrap --ro-bind / / --unshare-user --unshare-net true` and `puffin sandbox -- true` (Codex's sandbox, which sets up its loopback interface) both succeed where both failed before, and `host check` reports nothing to do. (Run from `!` in a Claude Code session, `host setup` has no terminal for sudo and prints the two commands instead; it has to be typed in a real terminal.)

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

### 3.5. Every GB10 machine

Puffin targets the chip, not a vendor's box. Eight machines carry it (researched 2026-10-06), all with 128 GB of unified LPDDR5x and the same GPU:

| Machine | Storage | OS shipped | Verified here |
|---|---|---|---|
| NVIDIA DGX Spark (Founders Edition) | 4 TB, self-encrypting | DGX OS | no |
| Acer Veriton GN100 (GN100-UD11) | up to 4 TB | DGX OS ("DGX Base OS") | no |
| ASUS Ascent GX10 | 1 TB, 2 TB (Gen4) or 4 TB (Gen5) | DGX OS, the only one ASUS supports | **yes**: this machine, 1 TB (916 GB root), DGX OS 7.5.0 (7.2.3 as shipped), Ubuntu 24.04.4, kernel 6.17.0-1029-nvidia, driver 580.173.02 |
| Dell Pro Max with GB10 (FCM1253) | 2 TB (QLC, Gen4) and up | DGX OS 7, lightly reskinned | no |
| Gigabyte AI TOP ATOM (ATAGB10-9000) | 4 TB | DGX OS | no |
| HP ZGX Nano G1n AI Station | 2 or 4 TB, self-encrypting | DGX OS 7, or Ubuntu 24.04 | no |
| Lenovo ThinkStation PGX | 1 or 4 TB, self-encrypting | DGX OS, or Ubuntu Pro with NVIDIA's packages | no |
| MSI EdgeXpert (MS-C931) | 1 or 4 TB | DGX OS | no |

Sources: [itechguides, all eight compared](https://www.itechguides.com/all-nvidia-dgx-spark-versions-so-far-1-generation-8-official-systems/), [Phoronix on the Dell](https://www.phoronix.com/review/dell-pro-max-gb10-preview), [Jeff Geerling on the Dell](https://www.jeffgeerling.com/blog/2025/dells-version-dgx-spark-fixes-pain-points/), [NVIDIA's DGX OS 7 guide](https://docs.nvidia.com/dgx/dgx-os-7-user-guide), [NVIDIA forum, GB10 = 10de:2e12](https://forums.developer.nvidia.com/t/please-add-gb10-10de-2e12-to-the-signed-nvgrace-gpu-vfio-pci-in-the-dgx-spark-kernel/383780), [a plain-Ubuntu GB10 guide](https://github.com/timothystewart6/ubuntu-gb10).

**What every machine shares, and so what Puffin relies on:**
- `nvidia-smi` names the GPU `NVIDIA GB10` and reports `memory.total` as `[N/A]` (unified memory), which `detect_gb10_hardware()` already tolerates; the PCI id is `10de:2e12`.
- DGX OS 7 is Ubuntu 24.04, so the apt package names (`sysstat`, `earlyoom`, `bubblewrap`, `avahi-daemon`, `libwebkit2gtk-4.1-dev`), the AppArmor user-namespace restriction and the systemd user session are the same on all of them.
- The driver is 580 or later on every shipped image, which the CUDA 13 images (the SGLang default and the NGC vLLM base) need. Nothing checks the version; none older exists for this chip.
- The default model, its pinned SGLang image (pulled, not built) and 64 GB of swap fit the smallest drive any of them ships, 1 TB.

**What differs, and how each is handled:**
- **DMI names.** Shown by `status`, never matched (§2). Only this ASUS's strings and the DGX Spark's published ones are known.
- **Swap.** Not documented by any vendor; forum reports show DGX Sparks with 2 GiB and 16 GiB. `host setup` brings a single `/swap.img` (or none) to 64 GB itself; a partition, LVM or several areas are left for the owner, with what to do (§3.3). This ASUS has a 64 GB `/swap.img`, made by hand on 2026-08-14.
- **OOM handler.** No vendor documents shipping earlyoom; systemd-oomd is installed on DGX OS but reported not enabled by default (inactive on this ASUS). Either way `host setup` installs and arms earlyoom (§3.3), and `check_host_safety()` accepts an active `systemd-oomd` instead.
- **Plain Ubuntu instead of DGX OS** (HP and Lenovo document it; anyone can reinstall). DGX OS brings Docker, the NVIDIA Container Toolkit, Avahi and (through GNOME) bubblewrap, and this ASUS's first user is in the `docker` group; a server install has none of that. `host check` names each (§3.3), `host setup` installs bubblewrap and joins the `docker` group itself, and `node enable` installs `avahi-daemon` through sudo or publishes nothing and says how to reach the node by address. Docker and the toolkit come from vendor repositories and are left to the owner, with the links.
- **The RTX Spark laptops** (GB10's Windows sibling, N1X, shipping from 2026-10) run Windows 11 on Arm and no Linux has been announced for them, so they cannot be a node: the model server needs Linux, Docker with the NVIDIA runtime, and the host-safety layer is Linux's. They are out of scope until NVIDIA ships Linux for them ([computingforgeeks](https://computingforgeeks.com/nvidia-rtx-spark-linux/)).

**Not verified on hardware, so made to tolerate rather than assumed:** every machine but this ASUS; plain Ubuntu on any of them (the Docker, toolkit, `docker` group, bubblewrap and Avahi steps are covered by tests only); a swap layout other than a single `/swap.img`; zram; another vendor's DMI strings; the DGX Spark's 4 TB self-encrypting drive (nothing in Puffin touches disk encryption). The first owner of another machine should run `puffin-admin host check` and `puffin-admin status` and report both.

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
