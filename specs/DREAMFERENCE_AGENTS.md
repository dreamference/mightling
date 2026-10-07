# Puffin Agent Runtimes & Integration

> **Version:** 1.2.0
> **Subject:** the agent runners: Codex (`puffin`, default), Cline, Continue, OpenHands.
> **Checked against the code:** 2026-10-01 (`dreamference/runner/`, `dreamference/config/dreamference_config.py`)

---

## Table of Contents

- [1. Agent Runtimes Overview](#1-agent-runtimes-overview)
- [2. Codex / `puffin` (Default)](#2-codex--puffin-default)
- [3. Cline (VS Code)](#3-cline-vs-code)
- [4. Continue (IDE)](#4-continue-ide)
- [5. OpenHands (Docker UI)](#5-openhands-docker-ui)
- [6. Waiting for vLLM](#6-waiting-for-vllm)
- [7. vLLM-Side Function Calling](#7-vllm-side-function-calling)
- [8. Failure Modes](#8-failure-modes)

---

## 1. Agent Runtimes Overview

The runner is chosen by `--agent`, then `DREAMFERENCE_AGENT` / `DREAMFERENCE_RUNNER`, then `agent_runner` in the config file, then the default **`codex`**.

**Choices:** `codex`, `cline`, `continue`, `openhands`.

**How each is reached:**
- **Codex:** the interactive agent is the `puffin` binary itself, run directly.
- **All four:** reachable through `puffin-admin run "PROMPT" [--agent …]`. `puffin-admin chat` was removed on 2026-09-28.

**Dispatch:** a strategy switch on `config.agent_runner` in `DreamferenceCLIController.run_cli`. Each agent has a `<agent>_installer.py` / `<agent>_runner.py` pair under `dreamference/runner/`.

**Waiting for vLLM** (§6): the three non-Codex runners call `VLLMReadinessWaiter.wait_for_vllm()`. Codex doesn't: the `puffin` launcher waits for the server itself.

**No container sandbox of Dreamference's own.** `puffin-admin`'s `--sandbox` option and `SandboxManager` (apptainer/podman/docker prefixes) were removed on 2026-10-01: Goose applied the prefixes and Aider read the setting, and once both were gone nothing used them. Codex has its own sandbox (`puffin -s/--sandbox` policy), which is unrelated.

---

## 2. Codex / `puffin` (Default)

- **Package:** `dreamference/runner/codex_runner.py`, `codex_installer.py`, `codex_branded_builder.py`.
- **Runtime:** the Puffin-branded Codex build (`~/.local/share/dreamference/puffin/bin/puffin`, linked at `~/.local/bin/puffin`), built from the `codex/` submodule plus `codex-patches/`. It never uses an upstream `codex` from PATH.
- **Session:** `CodexRunner.run_session()` builds `puffin` if needed and runs `puffin [args…] ["PROMPT"]`, passing `DREAMFERENCE_VLLM_HOST` along. With `--debug` it sets `RUST_LOG`.
- **Everything else happens in the launcher `puffin-rs/`:**
  - waiting for vLLM;
  - the model catalog and `config.toml` in `$CODEX_HOME`, which `puffin` sets to `~/.puffin` (never upstream's `~/.codex`);
  - the system prompt, with web access and optionally Gmail;
  - the `--oss --local-provider openai-custom --model <id>` options.

See `DREAMFERENCE_PUFFIN_CODEX.md` for the full description.

---

## 3. Cline (VS Code)

### 3.1. Overview

- **Package:** `dreamference/runner/cline_runner.py`, `cline_installer.py`
- **Runtime:** VS Code (`code`) or VSCodium (`codium`) plus the marketplace extension `saoudrizwan.claude-dev`

### 3.2. Session Behaviour

1. Wait for vLLM (§6).
2. Require `code` or `codium` on `PATH`; exit 1 with a hint otherwise.
3. Install the extension if missing (`code --install-extension saoudrizwan.claude-dev`).
4. Rewrite `.clinerules` in the workspace on every launch, with local/offline guidance plus Cave Mode text when `--cave` is set.
5. Print the connection details (Standard /v1 provider, base URL, API key `gb10-local-token`, model).
6. Open VS Code on the workspace.

The prompt is printed, not submitted to Cline. The model and endpoint must be entered in Cline's UI by hand; they do not propagate to the extension.

---

## 4. Continue (IDE)

### 4.1. Overview

- **Package:** `dreamference/runner/continue_runner.py`, `continue_installer.py`
- **Runtime:** VS Code / VSCodium plus the extension `Continue.continue`

### 4.2. Configuration

It writes `~/.continue/config.json`. If the file exists, it replaces only `models` and `tabAutocompleteModel` and keeps the rest.

```json
{
  "models": [
    {
      "title": "Puffin local ({model alias})",
      "provider": "openai",
      "model": "{hf_repo}",
      "apiBase": "{vllm_host}/v1/",
      "apiKey": "gb10-local-token"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Puffin Tab Autocomplete",
    "provider": "openai",
    "model": "{hf_repo}",
    "apiBase": "{vllm_host}/v1/",
    "apiKey": "gb10-local-token"
  }
}
```

**Tab autocomplete** uses the served model, the only one the endpoint has. Until 2026-09-29 it named the draft model, or `Qwen/Qwen2.5-Coder-1.5B-Instruct`, neither of which vLLM serves, so every autocomplete request failed.

### 4.3. Session Behaviour

1. Wait for vLLM (§6).
2. Require VS Code / VSCodium.
3. Install `Continue.continue` if missing.
4. Write or merge the config.
5. Launch `code|codium $(pwd)`.

The prompt is unused.

---

## 5. OpenHands (Docker UI)

### 5.1. Overview

- **Package:** `dreamference/runner/openhands_runner.py`, `openhands_installer.py`
- **Runtime:** Docker image `ghcr.io/all-hands-ai/openhands:main`, web UI at `http://localhost:3001` (`OPENHANDS_HOST_PORT`)

Port 3001, not 3000, because the Puffin web UI (Onyx) owns 3000; published on `127.0.0.1` only, because the container mounts the Docker socket and a UI on the network would hand root on the host to anyone who can reach it.

### 5.2. Container Launch

```bash
docker rm -f dreamference-openhands
docker run --rm -it --name dreamference-openhands \
  -e LLM_MODEL=openai/{hf_repo} \
  -e LLM_BASE_URL={OnyxRunner.resolve_container_vllm_url(vllm_host)} \
  -e LLM_API_KEY=gb10-local-token \
  -e WORKSPACE_BASE={cwd} \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v {cwd}:/opt/workspace_base \
  -p 127.0.0.1:3001:3000 \
  ghcr.io/all-hands-ai/openhands:main
```

`LLM_BASE_URL` goes through the same rewrite as Onyx's provider: a loopback vLLM host becomes the Docker bridge gateway, because inside the container `localhost` is the container itself.

### 5.3. Session Behaviour

1. Wait for vLLM (§6).
2. Require a working Docker daemon (`docker ps`).
3. Pull the image if missing.
4. Remove any stale container, then run.

The prompt is unused.

---

## 6. Waiting for vLLM

`VLLMReadinessWaiter.wait_for_vllm(poll_interval=1.0, max_wait=600.0)` (`dreamference/runner/vllm_readiness_waiter.py`), used by Cline, Continue and OpenHands:

1. It prints `⏳ Waiting for local vLLM server at {vllm_host} to become available...`.
2. Every second it checks `GET /v1/models` (1 s timeout) and prints a dot.
3. Once healthy, it sends a silent `max_tokens=1` chat completion naming the served model id. This proves that the engine can generate, not just list models; it returns success only when that request succeeds.
4. After 600 s it prints `❌ Timed out …` and `💡 Start vLLM in another terminal via: puffin-admin server start`, and fails.
5. Ctrl+C cancels without starting the agent.

**It does not launch vLLM.** Nothing downloads or starts a server from here. Start the server with `puffin-admin server start`, which is documented in `DREAMFERENCE_INFERENCE.md`.

---

## 7. vLLM-Side Function Calling


`server start` passes `--enable-auto-tool-choice` plus the parser `VLLMServerManager.resolve_tool_call_parser()` resolves:
1. an explicit `--tool-call-parser` (`auto` counts as unset);
2. the model's `launch_overrides["tool_call_parser"]`;
3. a guess from the name: `mistral` → `mistral`, otherwise `hermes`.

The parser is not a per-family constant. Qwen 2.5 emits Hermes-style `<tool_call>` blocks, while Qwen 3.5/3.6 emit XML. The default model `qwen3.8-27b-nvfp4-dflash2` (served by SGLang) resolves to `qwen3_coder`, the 122B and 35B vLLM recipes to `qwen3_xml` (reasoning parser `qwen3` for all of them), and Qwen 2.5 Coder resolves to `hermes`.


## 8. Failure Modes

| Failure | Signal | Recovery |
| :------ | :----- | :------- |
| vLLM not running | No healthy `/v1/models` within 600 s | Hint `puffin-admin server start`; exit 1 |
| vLLM up but not generating | Pre-warm request fails | Keeps waiting until the timeout |
| Ctrl+C during wait | `KeyboardInterrupt` | Cancel without starting the agent |
| Cline / Continue without VS Code | Neither `code` nor `codium` on PATH | Exit 1 with a PATH hint |
| OpenHands without Docker | `docker ps` fails | Exit 1 with a Docker daemon hint |
| OpenHands image pull failure | `docker pull` error | Exit 1 with the pull command |
| `puffin` not built (Codex) | Build fails or is missing | `puffin-admin codex build` |

---

## See Also

- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** the default agent, `puffin`
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM launch and configuration
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** Docker architecture and images
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI commands and configuration
