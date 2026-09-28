# Dreamference Agent Runtimes & Integration

> **Version:** 1.2.0
> **Subject:** the agent runners: Codex (`puffin`, default), Goose, Cline, Aider, Continue, OpenHands.
> **Checked against the code:** 2026-09-28 (`dreamference/runner/`, `dreamference/config/dreamference_config.py`)

---

## Table of Contents

- [1. Agent Runtimes Overview](#1-agent-runtimes-overview)
- [2. Codex / `puffin` (Default)](#2-codex--puffin-default)
- [3. Goose](#3-goose)
- [4. Cline (VS Code)](#4-cline-vs-code)
- [5. Aider (CLI)](#5-aider-cli)
- [6. Continue (IDE)](#6-continue-ide)
- [7. OpenHands (Docker UI)](#7-openhands-docker-ui)
- [8. Waiting for vLLM](#8-waiting-for-vllm)
- [9. Goose-vLLM Integration](#9-goose-vllm-integration)
- [10. Failure Modes](#10-failure-modes)

---

## 1. Agent Runtimes Overview

The runner is chosen by `--agent`, then `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER`, then `agent_runner` in the config file, then the default **`codex`**.

**Choices:** `codex`, `goose`, `cline`, `aider`, `continue`, `openhands`.

**How each is reached:**
- **Codex:** the interactive agent is the `puffin` binary itself, run directly.
- **All six:** reachable through `puffin-admin run "PROMPT" [--agent …]`. `puffin-admin chat` was removed on 2026-09-28.

**Dispatch:** a strategy switch on `config.agent_runner` in `DreamferenceCLIController.run_cli`. Each agent has a `<agent>_installer.py` / `<agent>_runner.py` pair under `dreamference/runner/`.

**Waiting for vLLM** (§8): the five non-Codex runners call `GooseRunner.wait_for_vllm()`. Codex doesn't: the `puffin` launcher waits for the server itself.

---

## 2. Codex / `puffin` (Default)

- **Package:** `dreamference/runner/codex_runner.py`, `codex_installer.py`, `codex_branded_builder.py`.
- **Runtime:** the Puffin-branded Codex build (`~/.local/share/dreamference/puffin/bin/puffin`, linked at `~/.local/bin/puffin`), built from the `codex/` submodule plus `codex-patches/`. It never uses an upstream `codex` from PATH.
- **Session:** `CodexRunner.run_session()` builds `puffin` if needed and runs `puffin [args…] ["PROMPT"]`, passing `DREAMFERENCE_VLLM_HOST` along. With `--debug` it sets `RUST_LOG`.
- **Everything else happens in the launcher `puffin-rs/`:**
  - waiting for vLLM;
  - the model catalog and `~/.codex/config.toml`;
  - the system prompt, with web access and optionally Gmail;
  - the `--oss --local-provider openai-custom --model <id>` options.

See `DREAMFERENCE_PUFFIN_CODEX.md` for the full description.

---

## 3. Goose

### 3.1. Overview

- **Package:** `dreamference/runner/goose_runner.py`, `goose_installer.py`
- **Runtime:** Goose AI agent (`aaif-goose/goose` 1.45+)
- **Mode:** native shell execution plus MCP tool bridging

### 3.2. Auto-Installation

```bash
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes || true
```

There is no pip fallback: a failed install prints the manual command.

### 3.3. Executable Resolution

`GooseInstaller` looks, in order:
1. `goose` on `PATH`;
2. `~/.local/bin/goose`;
3. `~/.goose/bin/goose`;
4. `sys.prefix/bin/goose`;
5. `sys.prefix/bin/goose-ai`.

### 3.4. Configuration Synthesis

`DreamferenceConfig.ensure_goose_config()` writes `~/.config/goose/config.yaml`:

```yaml
provider: openai
openai:
  base_url: {vllm_host}/v1
  api_key: gb10-local-token
  model: {resolved HF repo}
extensions:
  developer:
    enabled: true
    type: builtin
    allow_shell: true
  jetbrains_mcp:
    enabled: true
    type: stdio
    cmd: dreamference
    args: [mcp]
instructions: {build_instructions(): Hermes tool-call prompt if the parser is hermes, plus Cave Mode text}
```

If the file exists:
- `extensions` is merged: the two entries above are added or replaced, and the user's other extensions are kept;
- `developer` is then forced back to `allow_shell: true`;
- `provider`, `openai` and `instructions` overwrite the user's values;
- any other top-level key the user added is kept.

`instructions` is always rewritten, so a stale block for a previous model's tool-call format cannot survive.

> ⚠️ **Known defect:** `jetbrains_mcp.cmd` is `dreamference`, a command that no longer exists. The console script has been `puffin-admin` since the rename, so Goose cannot start this MCP extension. It should be `cmd: puffin-admin`, `args: [mcp]`.

### 3.5. Session Commands

- **Run:** `goose run --text "<prompt>"`
- **Without a prompt:** `goose session`. `puffin-admin chat` is gone, so this path is reachable only programmatically.
- **Debug:** `--debug` is appended.

Before launching, `run_session` calls `DreamferenceConfig.validate_model()`, which hard-fails if the main and draft models exceed GB10's memory budget.

### 3.6. Sandbox Prefix (`SandboxManager`)

```
--sandbox {none,apptainer,podman,docker}  (default: none)
```

| Mode | Prefix |
| --- | --- |
| `apptainer` | `apptainer exec --writable-tmpfs --bind {cwd}:/workspace docker://ubuntu:22.04` |
| `podman` | `podman run --rm -it -v {cwd}:/workspace:Z -w /workspace ubuntu:22.04` |
| `docker` | `docker run --rm -it -v {cwd}:/workspace -w /workspace ubuntu:22.04` |

A missing runtime binary prints a warning and runs unsandboxed. Only Goose uses these prefixes. Aider maps `sandbox` to its commit policy (§5.4), and Codex has its own sandbox (`-s/--sandbox` policy).

---

## 4. Cline (VS Code)

### 4.1. Overview

- **Package:** `dreamference/runner/cline_runner.py`, `cline_installer.py`
- **Runtime:** VS Code (`code`) or VSCodium (`codium`) plus the marketplace extension `saoudrizwan.claude-dev`

### 4.2. Session Behaviour

1. Wait for vLLM (§8).
2. Require `code` or `codium` on `PATH`; exit 1 with a hint otherwise.
3. Install the extension if missing (`code --install-extension saoudrizwan.claude-dev`).
4. Rewrite `.clinerules` in the workspace on every launch, with local/offline guidance plus Cave Mode text when `--cave` is set.
5. Print the connection details (OpenAI-compatible provider, base URL, API key `gb10-local-token`, model).
6. Open VS Code on the workspace.

The prompt is printed, not submitted to Cline. The model and endpoint must be entered in Cline's UI by hand; they do not propagate to the extension.

---

## 5. Aider (CLI)

### 5.1. Overview

- **Package:** `dreamference/runner/aider_runner.py`, `aider_installer.py`
- **Runtime:** `aider` from the `aider-chat` package

### 5.2. Provisioning

It tries `python -m pip install aider-chat` in the active environment, then `pipx install aider-chat`.

### 5.3. Launch Flags

```bash
aider \
  --openai-api-base {vllm_host}/v1/ \
  --openai-api-key gb10-local-token \
  --model openai/{hf_repo} \
  --auto-commits | --no-auto-commits \
  [--editor-model openai/{hf_repo} --architect --model openai/{draft_hf_repo}]   # when draft_model is set
  [--message "<prompt>"] \
  [--verbose]
```

**Architect mode.** When a `draft_model` is configured, the runner appends `--architect`, the main model as `--editor-model`, and a second `--model` naming the *draft* model. The later `--model` wins, so the small draft model plans and the main model edits.

### 5.4. Sandbox Mapping

- `sandbox != none` → `--no-auto-commits`
- `sandbox == none` → `--auto-commits`

No `SandboxManager` prefix is applied.

---

## 6. Continue (IDE)

### 6.1. Overview

- **Package:** `dreamference/runner/continue_runner.py`, `continue_installer.py`
- **Runtime:** VS Code / VSCodium plus the extension `Continue.continue`

### 6.2. Configuration

It writes `~/.continue/config.json`. If the file exists, it replaces only `models` and `tabAutocompleteModel` and keeps the rest.

```json
{
  "models": [
    {
      "title": "Dreamference local ({model alias})",
      "provider": "openai",
      "model": "{hf_repo}",
      "apiBase": "{vllm_host}/v1/",
      "apiKey": "gb10-local-token"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Dreamference Tab Autocomplete",
    "provider": "openai",
    "model": "{draft hf_repo, or Qwen/Qwen2.5-Coder-1.5B-Instruct}",
    "apiBase": "{vllm_host}/v1/",
    "apiKey": "gb10-local-token"
  }
}
```

**Tab autocomplete** points at the *same* vLLM endpoint, but names a model that endpoint does not serve. That is the draft model if one is configured, otherwise `Qwen/Qwen2.5-Coder-1.5B-Instruct`, and vLLM serves exactly one model. Autocomplete requests therefore fail unless a second vLLM serving that model is started, and the URL edited to point at it.

### 6.3. Session Behaviour

1. Wait for vLLM (§8).
2. Require VS Code / VSCodium.
3. Install `Continue.continue` if missing.
4. Write or merge the config.
5. Launch `code|codium $(pwd)`.

The prompt is unused.

---

## 7. OpenHands (Docker UI)

### 7.1. Overview

- **Package:** `dreamference/runner/openhands_runner.py`, `openhands_installer.py`
- **Runtime:** Docker image `ghcr.io/all-hands-ai/openhands:main`, web UI at `http://localhost:3000`

> ⚠️ **Port conflict:** port 3000 is also where the Puffin web UI (Onyx) listens (`puffin-admin puffin start`). OpenHands cannot start while Onyx is running.

### 7.2. Container Launch

```bash
docker rm -f dreamference-openhands
docker run --rm -it --name dreamference-openhands \
  -e LLM_MODEL=openai/{hf_repo} \
  -e LLM_BASE_URL={vllm_host}/v1 \
  -e LLM_API_KEY=gb10-local-token \
  -e WORKSPACE_BASE={cwd} \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v {cwd}:/opt/workspace_base \
  -p 3000:3000 \
  ghcr.io/all-hands-ai/openhands:main
```

### 7.3. Session Behaviour

1. Wait for vLLM (§8).
2. Require a working Docker daemon (`docker ps`).
3. Pull the image if missing.
4. Remove any stale container, then run.

The prompt is unused.

---

## 8. Waiting for vLLM

`GooseRunner.wait_for_vllm(poll_interval=1.0, max_wait=600.0)`, used by Goose, Cline, Aider, Continue and OpenHands:

1. It prints `⏳ Waiting for local vLLM server at {vllm_host} to become available...`.
2. Every second it checks `GET /v1/models` (1 s timeout) and prints a dot.
3. Once healthy, it **pre-warms**: a silent `max_tokens=1` chat completion with a short Goose-style system message and one placeholder tool schema (`dreamference_mcp`). Its real value is proving that the engine can generate, not just list models. It returns success only when the pre-warm succeeds. The prompt it sends is a stand-in, not the agent's actual system prompt, so it doesn't meaningfully prime the prefix cache for the real session.
4. After 600 s it prints `❌ Timed out …` and `💡 Start vLLM in another terminal via: puffin-admin server start`, and fails.
5. Ctrl+C cancels without starting the agent.

**It does not launch vLLM.** The method has an `auto_launch` parameter, but it is unused: nothing downloads or starts a server from here. Start the server with `puffin-admin server start`, which is documented in `DREAMFERENCE_INFERENCE.md`.

---

## 9. Goose-vLLM Integration

### 9.1. Endpoint Wiring

`DreamferenceConfig.get_env_vars()` sets, for Goose processes:

```bash
GOOSE_PROVIDER=openai
OPENAI_BASE_URL={vllm_host}/v1
OPENAI_API_KEY=gb10-local-token
GOOSE_MODEL=<resolved HF repo>
GOOSE_ALLOW_SHELL=1
GOOSE_ALLOW_READ=1
GOOSE_TELEMETRY_OFF=1
```

### 9.2. Real Shell Execution

The built-in `developer` extension is registered with `allow_shell: true`, and `GOOSE_ALLOW_SHELL=1` / `GOOSE_ALLOW_READ=1` are exported. Goose therefore runs `/bin/bash -c` for real instead of emitting simulated `{"name":"shell"}` tool calls.

### 9.3. vLLM-Side Function Calling

`server start` passes `--enable-auto-tool-choice` plus the parser `VLLMServerManager.resolve_tool_call_parser()` resolves:
1. an explicit `--tool-call-parser` (`auto` counts as unset);
2. the model's `launch_overrides["tool_call_parser"]`;
3. a guess from the name: `mistral` → `mistral`, otherwise `hermes`.

The parser is not a per-family constant. Qwen 2.5 emits Hermes-style `<tool_call>` blocks, while Qwen 3.5/3.6 emit XML. The default model `qwen3.5-122b-a10b-hybrid-dflash` resolves to `qwen3_xml` (reasoning parser `qwen3`), and Qwen 2.5 Coder resolves to `hermes`.

### 9.4. System Prompt Injection

`DreamferenceConfig.build_instructions()` puts `HERMES_TOOL_CALL_PROMPT` into Goose's `instructions` **only** when the resolved parser is `hermes`. Teaching an XML-parser model to emit Hermes tags produces tool calls the server cannot parse. Cave Mode text is appended regardless of the parser.

---

## 10. Failure Modes

| Failure | Signal | Recovery |
| :------ | :----- | :------- |
| vLLM not running | No healthy `/v1/models` within 600 s | Hint `puffin-admin server start`; exit 1 |
| vLLM up but not generating | Pre-warm request fails | Keeps waiting until the timeout |
| Ctrl+C during wait | `KeyboardInterrupt` | Cancel without starting the agent |
| Model over memory budget (Goose) | `validate_model()` false | Exit 1 with the reason |
| Goose install failure | `goose` still not found | Print the manual curl command; exit 1 |
| Cline / Continue without VS Code | Neither `code` nor `codium` on PATH | Exit 1 with a PATH hint |
| Aider install failure | `aider` still not found after pip/pipx | Exit 1 with a `pip install aider-chat` hint |
| OpenHands without Docker | `docker ps` fails | Exit 1 with a Docker daemon hint |
| OpenHands image pull failure | `docker pull` error | Exit 1 with the pull command |
| OpenHands with Onyx running | Port 3000 already bound | Stop Onyx (`puffin-admin puffin stop`) first |
| `puffin` not built (Codex) | Build fails or is missing | `puffin-admin codex build` |

---

## See Also

- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** the default agent, `puffin`
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM launch and configuration
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** Docker architecture and images
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI commands and configuration
