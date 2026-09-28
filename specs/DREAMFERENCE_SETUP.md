# Dreamference System Requirements & Setup

> **Version:** 1.2.0
> **Subject:** Installation, Hardware Detection, Quickstart, Helper Scripts

---

## Table of Contents

- [1. System Requirements](#1-system-requirements)
- [2. Hardware Identification](#2-hardware-identification)
- [3. Quickstart Installation](#3-quickstart-installation)
- [4. Helper Scripts](#4-helper-scripts)

---

## 1. System Requirements

### 1.1. Hardware

- **System**: 1x NVIDIA GB10 (Blackwell, 128 GB Unified Memory) — or host with ≥100 GB RAM for detection fallback
- **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture)
- **Memory**: 128 GB Unified LPDDR5X (or ≥100 GB fallback)
- **CPU**: High-performance ARM Cortex CPU cores (`aarch64` architecture)
- **Storage**: NVMe PCIe SSD (recommended for workspace indexing & model caching)

### 1.2. Operating System

- **OS**: Linux ARM64 (Ubuntu 22.04 LTS or compatible)
- **Kernel**: Standard Linux kernel (5.15+ recommended)
- **Drivers**: NVIDIA Linux Driver 580+ / CUDA 13.x (typical GB10 stack)

### 1.3. Software Dependencies

**Core**:
- Python 3.10+
- PyYAML
- Rich (terminal UI)
- Requests (HTTP client)

**Optional**:
- **Docker**: For vLLM & OpenHands (strongly recommended)
- **VS Code / VSCodium**: For Cline & Continue runners
- **Aider**: `aider-chat` package for Aider runner
- **Sandbox Runtimes**: Apptainer, Podman, or Docker (for Goose sandbox prefixes)

### 1.4. Runtime Privileges

- **Docker Access**: Should not require `sudo` (standard `docker` group membership)
- **Filesystem**: Write access to `~/.cache/` and `~/.config/`

---

## 2. Hardware Identification

### 2.1. Automatic Detection

Dreamference automatically detects GB10 qualification via:

```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```

**Qualification Criteria**:
- GPU name contains `GB10` or `BLACKWELL`, OR
- Total memory ≥ ~100 GiB (fallback heuristic)

### 2.2. Manual Verification

```bash
# Check GPU name
nvidia-smi --query-gpu=name --format=csv

# Check total memory
free -h
```

**Expected Output for GB10**:
```
NVIDIA GB10 (Blackwell)
total: ~128G
```

### 2.3. Detection Override

For testing on unsupported hardware:
```bash
export DREAMFERENCE_GB10_OVERRIDE=1
puffin-admin status  # Will report GB10 mode
```

**Warning**: This is for testing only. Many GB10-specific optimizations may fail on incompatible hardware.

---

## 3. Quickstart Installation

### 3.1. Automated Install

```bash
# Clone repository
git clone https://github.com/dreamference/dreamference.git
cd dreamference

# Run install script (downloads Goose, installs package, initializes)
./scripts/install_gb10.sh [MODEL]

# Verify installation
puffin-admin status

# Start interactive session
puffin-admin chat
```

**MODEL Argument**: Optional (default `qwen3.6-35b-a3b-nvfp4`)

### 3.2. What the Install Script Does

`scripts/install_gb10.sh` performs:

1. **Install Goose CLI**:
   ```bash
   curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes
   ```

2. **Install Dreamference Package** (editable):
   ```bash
   pip install -e .
   ```

3. **Initialize Workspace**:
   ```bash
   puffin-admin init --model "${1:-qwen3.6-35b-a3b-nvfp4}"
   ```

This downloads model weights, generates `dreamference.toml`, writes Goose config, and force-indexes the workspace.

### 3.3. Manual Installation Steps

If you prefer to install manually:

```bash
# 1. Create & activate virtual environment (optional)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Dreamference in editable mode
pip install -e .

# 3. Install Goose (or skip; auto-install on first `puffin-admin chat`)
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes

# 4. Initialize workspace
puffin-admin init --model qwen3.6-35b-a3b-nvfp4

# 5. Verify installation
puffin-admin status
```

### 3.4. Verification

After installation:

```bash
# 1. Check hardware detection
puffin-admin status

# 2. Verify model matrix
puffin-admin model list

# 3. Try interactive chat
puffin-admin chat --agent goose

# 4. Try a task
puffin-admin run "Hello, Goose!"
```

---

## 4. Helper Scripts

Located in `scripts/` directory:

### 4.1. `scripts/install_gb10.sh`

```bash
./scripts/install_gb10.sh [MODEL]
```

**Role**: Full installation (Goose + package + initialization)

**Positional Args**:
- `[MODEL]`: Model alias (default `qwen3.6-35b-a3b-nvfp4`)

**What It Does**:
1. Installs Goose via `releases/latest/download/download_cli.sh`
2. Fallback to `pip install goose-ai` if download fails
3. `pip install -e .`
4. `puffin-admin init --model "${1:-qwen3.6-35b-a3b-nvfp4}"`

**Note**: Runtime Goose auto-install uses `releases/download/stable/…`; this script uses `releases/latest/…`.

### 4.2. `scripts/run_vllm_gb10.sh`

```bash
./scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT] [TOKENS]
```

**Role**: Thin foreground Python-module vLLM launch (minimal flags)

**Positional Args**:
- `[MODEL]`: Model alias (default `qwen3.6-35b-a3b-nvfp4`)
- `[PORT]`: vLLM port (default `8000`)
- `[DRAFT]`: Draft model for speculative decoding (optional)
- `[TOKENS]`: Speculative tokens (optional)

**Caveats**:
- No prefix-cache / chunked-prefill / kv-cache-dtype flags
- No Docker containerization
- No tensorizer support
- **Prefer `puffin-admin server start` for full GB10-tuned behavior**

### 4.3. `scripts/run_goose.sh`

```bash
./scripts/run_goose.sh
```

**Role**: Sets Goose OpenAI environment variables and runs `goose session`

**What It Does**:
1. Exports `GOOSE_PROVIDER=openai`
2. Exports `OPENAI_*` vars from config
3. Launches `goose session`

**Preference**: Use `puffin-admin chat` instead (handles more cases, better UX).

---

## 5. Installation Troubleshooting

### 5.1. Docker Not Found

**Error**: `docker ps` fails

**Resolution**:
```bash
# Install Docker
sudo apt install docker.io

# Add user to docker group (no sudo required)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker ps
```

### 5.2. Goose Not Found

**Error**: `which goose` returns nothing

**Resolution**:
```bash
# Manual Goose install
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes

# Verify
goose --version
```

### 5.3. NVIDIA Driver Not Found

**Error**: `nvidia-smi` fails

**Resolution**:
```bash
# Install NVIDIA drivers (550+ recommended for GB10)
sudo apt install -y nvidia-driver-550

# Reboot
sudo reboot

# Verify
nvidia-smi
```

### 5.4. Python 3.10+ Not Found

**Error**: `python --version` shows Python 3.9 or earlier

**Resolution**:
```bash
# Install Python 3.11 (or 3.10)
sudo apt install python3.11 python3.11-venv python3.11-dev

# Create venv with specific version
python3.11 -m venv .venv
source .venv/bin/activate

# Install Dreamference
pip install -e .
```

---

## 6. Post-Installation

### 6.1. Verify vLLM Health

```bash
puffin-admin server start --model qwen3.6-35b-a3b-nvfp4
```

Server should print logs and exit once healthy (vLLM stays running in background).

### 6.2. Verify Agent Runner

```bash
puffin-admin chat --agent goose
```

Should launch an interactive Goose session.

### 6.3. Verify Context Engine

```bash
puffin-admin index --force
```

Should index workspace and create `.dreamference/` artifacts.

### 6.4. Check Status

```bash
puffin-admin status
```

Should display:
- ✅ GB10 qualified
- ✅ vLLM endpoint health
- ✅ Agent readiness
- ✅ Context index loaded

---

## See Also

- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md)** — CLI reference
- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md)** — Model matrix & selection
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Agent setup & usage
