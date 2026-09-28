# Dreamference Agent Runtimes & Integration

> **Version:** 1.2.0
> **Subject:** Goose, Cline, Aider, Continue, OpenHands Integration & Session Startup

---

## Table of Contents

- [1. Agent Runtimes Overview](#1-agent-runtimes-overview)
- [2. Goose (Default)](#2-goose-default)
- [3. Cline (VS Code)](#3-cline-vs-code)
- [4. Aider (CLI)](#4-aider-cli)
- [5. Continue (IDE)](#5-continue-ide)
- [6. OpenHands (Docker UI)](#6-openhands-docker-ui)
- [7. Session Startup Flows](#7-session-startup-flows)
- [8. Goose-vLLM Integration](#8-goose-vllm-integration)
- [9. Failure Modes](#9-failure-modes)

---

## 1. Agent Runtimes Overview

CLI selects the runner via `--agent` / `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER` / config `agent_runner` (default `goose`).

**Choices**: `goose`, `cline`, `aider`, `continue`, `openhands`

All non-Goose runners reuse `GooseRunner.wait_for_vllm()` for shared vLLM auto-launch logic.

---

## 2. Goose (Default)

### 2.1. Overview

- **Package**: `dreamference/runner/goose_runner.py`, `goose_installer.py`
- **Runtime**: Goose AI Agent (`aaif-goose/goose` v1.45+)
- **Mode**: Native shell execution + MCP tool bridging

### 2.2. Auto-Installation

```bash
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes
```

**Fallback**: `pip install goose-ai` if the download script fails.

### 2.3. Executable Resolution

Goose binary is located in this order:
1. `PATH` environment variable
2. `~/.local/bin/goose`
3. `~/.goose/bin/goose`
4. `sys.prefix/bin/goose`
5. `sys.prefix/bin/goose-ai`

### 2.4. Configuration Synthesis

`ensure_goose_config()` writes `~/.config/goose/config.yaml` with:

```yaml
provider: openai
host: {vllm_host}
base_path: v1
api_key: gb10-local-token
model: {hf_repo}
extensions:
  developer:
    allow_shell: true
  jetbrains_mcp:
    cmd: dreamference
    args: [mcp]
```

### 2.5. Session Commands

- **Chat**: `goose session` (interactive multi-turn)
- **Run**: `goose run --text "<prompt>"` (non-interactive task)
- **Debug**: Optional `--debug` flag for verbose output

### 2.6. Sandbox Prefix (Goose Only)

```
--sandbox {none,apptainer,podman,docker}  (default: none)
```

**Apptainer** (recommended for unprivileged users):
```bash
apptainer exec --writable-tmpfs --bind {cwd}:/workspace docker://ubuntu:22.04 <goose_command>
```

**Podman** (rootless):
```bash
podman run --rm -it -v {cwd}:/workspace:Z -w /workspace ubuntu:22.04 <goose_command>
```

**Docker** (requires root or sudo):
```bash
docker run --rm -it -v {cwd}:/workspace -w /workspace ubuntu:22.04 <goose_command>
```

Missing runtime binary → warning and unsandboxed execution.

---

## 3. Cline (VS Code)

### 3.1. Overview

- **Package**: `dreamference/runner/cline_runner.py`, `cline_installer.py`
- **Runtime**: VS Code (`code`) or VSCodium (`codium`) + marketplace extension `saoudrizwan.claude-dev`
- **Mode**: IDE-driven pair programming with local vLLM endpoint

### 3.2. Provisioning

```bash
code --install-extension saoudrizwan.claude-dev
```

Missing extension → auto-install on launch.

### 3.3. Workspace Rules

- `.clinerules` file is rewritten on every launch with local/offline guidance.
- Cave Mode instructions are included if `--cave` is enabled.
- Ensures rules are never stale.

### 3.4. Session Behavior

1. Auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Require `code` or `codium` on `PATH`.
3. Install Cline extension if missing.
4. Ensure `.clinerules` exists.
5. Print connection details (API key `gb10-local-token`).
6. Launch VS Code on the workspace.

**Note**: Prompt text is printed only (not auto-submitted to Cline).

### 3.5. Manual Configuration

Model selection and endpoint configuration do not propagate automatically to the extension; they must be manually entered into the Cline UI.

---

## 4. Aider (CLI)

### 4.1. Overview

- **Package**: `dreamference/runner/aider_runner.py`, `aider_installer.py`
- **Runtime**: Aider CLI (`aider` from package `aider-chat`)
- **Mode**: CLI-driven multi-file pair programming

### 4.2. Provisioning

```bash
pip install aider-chat
```

**Fallback**: `pipx install aider-chat` if pip install fails.

### 4.3. Launch Flags

```bash
aider \
  --openai-api-base {vllm_host}/v1/ \
  --openai-api-key gb10-local-token \
  --model openai/{hf_repo} \
  [--no-auto-commits | --auto-commits] \
  [--message "<prompt>"] \
  [--verbose]
```

### 4.4. Sandbox Integration

- `sandbox != none` → `--no-auto-commits` (prevents auto-commits in sandboxed sessions)
- `sandbox == none` → `--auto-commits` (allows automatic commits in native sessions)
- **Note**: Aider does **not** wrap with `SandboxManager` prefixes; the runner handles sandbox logic differently.

### 4.5. Session Behavior

1. Auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Install `aider-chat` via pip (then pipx) if `aider` missing.
3. Launch `aider` with OpenAI-compatible base/key/model flags.
4. If `run` mode: use `--message <prompt>`.
5. If `--debug`: use `--verbose`.

---

## 5. Continue (IDE)

### 5.1. Overview

- **Package**: `dreamference/runner/continue_runner.py`, `continue_installer.py`
- **Runtime**: VS Code / VSCodium + marketplace extension `Continue.continue`
- **Mode**: IDE chat + tab autocomplete

### 5.2. Provisioning

```bash
code --install-extension Continue.continue
```

Missing extension → auto-install on launch.

### 5.3. Configuration

Writes/merges `~/.continue/config.json` with:

```json
{
  "models": [
    {
      "provider": "openai",
      "model": "{hf_repo}",
      "apiBase": "{vllm_host}/v1/",
      "apiKey": "gb10-local-token"
    }
  ],
  "tabAutocompleteModel": null
}
```

**Note**: Tab autocomplete (`tabAutocompleteModel`) is unset by default. A real autocomplete model requires a separate vLLM instance on another port, as speculative draft models are not served as separate endpoints.

### 5.4. Session Behavior

1. Auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Require VS Code / VSCodium.
3. Install `Continue.continue` if missing.
4. Write/merge Continue config.
5. Launch `code|codium $(pwd)`.

**Note**: Prompt argument is unused.

---

## 6. OpenHands (Docker UI)

### 6.1. Overview

- **Package**: `dreamference/runner/openhands_runner.py`, `openhands_installer.py`
- **Runtime**: Docker image `ghcr.io/all-hands-ai/openhands:main`
- **Mode**: Web UI for autonomous agent workflows
- **Port**: 3000 (web UI at `http://localhost:3000`)

### 6.2. Provisioning

Requires working Docker (`docker ps` must succeed).

```bash
docker pull ghcr.io/all-hands-ai/openhands:main
```

Missing image → auto-pull on launch.

### 6.3. Container Launch

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

### 6.4. Session Behavior

1. Auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Check Docker daemon availability.
3. Pull image if missing.
4. Remove stale container (`docker rm -f dreamference-openhands`).
5. Run OpenHands UI container.

**Note**: Prompt argument is unused (not passed into the container).

---

## 7. Session Startup Flows

### 7.1. Goose Path (`puffin-admin chat|run` with default agent)

```
puffin-admin chat | run [--agent goose]
       |
       v
[1] Resolve config (CLI > Env > config file > defaults)
       |
       v
[2] ensure_goose_config()  -->  ~/.config/goose/config.yaml
       |
       v
[3] validate_model()       -->  hard-fail if model exceeds memory budget
       |
       v
[4] GET {vllm_host}/v1/models  --healthy?--+
       |                                   |
       | no                                | yes
       v                                   |
[5] Auto-launch vLLM (background)          |
       |  a. download_model(primary/draft) |
       |  b. launch tier resolve + Popen   |
       |  c. log streamer                  |
       v                                   |
[6] Poll readiness (0.1s slices, [vLLM] logs)
       |                                   |
       +------------------+----------------+
                          |
                          v
[7] Pre-warm Goose system prompt + MCP tools (silent max_tokens=1 chat request)
       |
       v
[8] Provision Goose CLI if missing
       |
       v
[9] Optional sandbox prefix
       |
       v
[10] Exec goose session | goose run --text "<prompt>"
```

### 7.2. Cline Path (`--agent cline`)

1. Health-check / auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Require `code` or `codium` on `PATH`.
3. Install Cline extension if missing.
4. Ensure `.clinerules` exists.
5. Print connection details; launch VS Code on the workspace.

**Note**: Prompt text is printed only (not auto-submitted to Cline).

### 7.3. Aider Path (`--agent aider`)

1. Health-check / auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Install `aider-chat` via pip (then pipx) if `aider` missing.
3. Launch `aider` with OpenAI-compatible base/key/model flags.
4. Optional architect/editor split when `draft_model` is set.
5. `run` mode → `--message <prompt>`.
6. `--debug` → `--verbose`.

### 7.4. Continue Path (`--agent continue`)

1. Health-check / auto-launch vLLM.
2. Require VS Code / VSCodium.
3. Install `Continue.continue` if missing.
4. Write/merge `~/.continue/config.json`.
5. Launch `code|codium $(pwd)`.

### 7.5. OpenHands Path (`--agent openhands`)

1. Health-check / auto-launch vLLM.
2. Require Docker daemon.
3. Pull `ghcr.io/all-hands-ai/openhands:main` if missing.
4. `docker rm -f dreamference-openhands`.
5. Run container on port **3000** with workspace + docker.sock mounts and LLM env pointing at local vLLM.

### 7.6. `puffin-admin server start` Variant

Launches `VLLMServerManager.start_server(background=True)`, starts a `ModelLoadingMonitor` thread (live log piping + 10s Docker memory stats + stage detection + health polling), prints progress, and **exits once the model health check passes** (the vLLM server/container continues running).

Uses CLI `--model` (defaults to `qwen3.6-35b-a3b-nvfp4`) and other tuning flags from config. No agent runner is started.

---

## 8. Goose-vLLM Integration

### 8.1. Endpoint Wiring

`DreamferenceConfig.get_env_vars()` + `ensure_goose_config()` set:

```bash
GOOSE_PROVIDER=openai
OPENAI_BASE_URL={vllm_host}/v1
OPENAI_API_KEY=gb10-local-token
GOOSE_MODEL=<resolved HF repo>
```

### 8.2. Real Shell Execution

- `GOOSE_ALLOW_SHELL=1` and `GOOSE_ALLOW_READ=1` exported on every launch.
- Built-in `developer` extension registered with `"allow_shell": true` so Goose invokes the native `/bin/bash -c` instead of emitting simulated JSON `{"name":"shell"}` tool calls.

### 8.3. MCP Companion

Always registers the `jetbrains_mcp` stdio extension (`puffin-admin mcp`).

### 8.4. Deep-Merge Safety

`ensure_goose_config` performs a targeted deep-merge of the `extensions` dict so `developer` + `jetbrains_mcp` are never overwritten when the user already has an `extensions` section in `~/.config/goose/config.yaml`.

### 8.5. vLLM-Side Function Calling

`server start` passes `--enable-auto-tool-choice` plus the parser resolved for the target model, satisfying Goose function-calling requirements without extra flags.

**Parser Resolution Order**:
1. Explicit `--tool-call-parser` argument
2. Model's `launch_overrides` recipe
3. Family guess from model name (`mistral` → `mistral`, otherwise `hermes`)

**Important**: Parser choice is **not** a per-family constant. Qwen 2.5 emits Hermes-style `<tool_call>` blocks while Qwen 3.6 emits XML, so the default model resolves to `qwen3_xml` and Qwen 2.5 Coder still resolves to `hermes`.

### 8.6. System Prompt Injection

`DreamferenceConfig.build_instructions()` injects `HERMES_TOOL_CALL_PROMPT` into the Goose `instructions` block **only** when the resolved parser is `hermes`. Teaching a model to emit Hermes tags while the server runs an XML parser produces tool calls the server cannot parse. Cave Mode instructions are appended independently of parser choice.

**Result**: `puffin-admin chat` / `run` produce a fully-functional Goose session that can execute real shell commands and call tools against the GB10 vLLM instance out-of-the-box.

---

## 9. Failure Modes

| Failure | Signal | Recovery |
| :------ | :----- | :------- |
| vLLM launch failure | Timeout on `/v1/models` health check | Hint to run `puffin-admin server start --model <model>`; `chat`/`run` exit `1` |
| Process crash during wait | Log drain, process detection | Drain remaining logs; return failure |
| Ctrl+C during wait | Signal handler | Cancel without starting the agent |
| Goose install failure | `which goose` not found | Print manual curl install command; exit `1` |
| Cline / Continue without VS Code | `which code` / `which codium` fails | Exit `1` with PATH install hint |
| Aider install failure | `which aider` not found after pip/pipx | Exit `1` with `pip install aider-chat` hint |
| OpenHands without Docker | `docker ps` fails | Exit `1` with Docker daemon hint |
| OpenHands image pull failure | `docker pull` error | Exit `1` with pull command hint |

---

## See Also

- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — vLLM launch & configuration
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md)** — Docker architecture & images
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md)** — CLI commands & configuration
