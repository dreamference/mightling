# Dreamference Codex Integration

> **Version:** 1.2.0
> **Subject:** Codex CLI runner, provisioning, and vLLM integration.

---

## 📍 Overview

Codex is a lightweight pair-programming AI agent supported by Dreamference. It functions as an interactive coding assistant that targets the local GB10 vLLM inference endpoint to provide real-time code synthesis and refactoring, operating entirely as an AI pair-programmer.

> **Note**: Codex is strictly an AI-powered coding agent. It is not a comic book archive server or any other type of media service.

## 1. Codex Runner (`CodexRunner`)

Located in `dreamference/runner/codex_runner.py`.

The `CodexRunner` orchestrates the session lifecycle:
1.  **VLLM Health**: Ensures the local vLLM instance is reachable.
2.  **Provisioning**: Uses `CodexInstaller` to ensure `codex` is in the PATH.
3.  **Launch**: Spawns the `codex` CLI process with the required environment variables pointing to the local OpenAI-compatible vLLM endpoint.

### 1.1. Environment Configuration

Codex is configured to operate entirely offline on the GB10 system via:

- `OPENAI_API_BASE`: Set to `http://<vllm_host>/v1`.
- `OPENAI_API_KEY`: Set to `gb10-local-token`.
- `--model`: Set to `openai/<hf_repo_alias>` (resolved via `dreamference.hardware.resolve_model_hf_repo`).

## 2. Codex Installer (`CodexInstaller`)

Located in `dreamference/runner/codex_installer.py`.

Handles automatic provisioning of the Codex CLI:

- **Detection**: Uses `shutil.which("codex")` to check availability.
- **Auto-Installation**: Attempts to install via:
  1. `pip install codex` (system/venv)
  2. `pipx install codex` (if available)

## 3. Session Flow

### 3.1. Session Parameters

- **prompt**: Optional task prompt for non-interactive execution.
- **debug**: Boolean flag to enable verbose output (`--debug` flag passed to Codex).

```python
# Launching a session (interactive)
CodexRunner().run_session()

# Launching a task (non-interactive, debug mode)
CodexRunner().run_session(prompt="Refactor the context engine", debug=True)
```

The runner directly invokes the `codex` binary via `subprocess.call` after injecting the environment variables.

---

## 4. Troubleshooting

### 4.1. EngineDeadError During Shutdown
If you observe `vllm.v1.engine.exceptions.EngineDeadError` in logs upon closing a Codex session (or stopping the vLLM server), this is a known, benign artifact of the vLLM engine being forcibly killed while it may still have pending cleanup tasks or unfinished requests. It does not indicate a functional defect in Codex, the vLLM server, or your workspace.

---

## 🔗 Quick Links

- **Codex Repository**: https://github.com/openai/codex
