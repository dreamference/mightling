# Dreamference Architecture Overview

> - **Version:** 1.2.0 (`dreamference.__version__`, `setup.py`)
> - **Target Hardware:** NVIDIA GB10 (Blackwell SM121, 128 GB unified LPDDR5X)
> - **Deployment Model:** single-node, air-gapped
> - **License:** AGPL-3.0-or-later
> - **Checked against the code:** 2026-09-28

---

## 1. Executive Summary

**Dreamference** is a local, air-gapped agentic coding platform for a single NVIDIA GB10. It serves open models with vLLM in Docker, and puts three front ends on the same local OpenAI-compatible endpoint:

- **`puffin`:** the terminal coding agent, and the default. It is a Puffin-branded build of OpenAI's Codex CLI with a Rust launcher compiled in that points it at the local model. It is built from a pinned fork (`codex/` submodule) plus small patches (`codex-patches/`) and the launcher crate (`puffin-rs/`).
- **Puffin web UI:** Onyx Lite, deployed and patched by `puffin-admin puffin …`. It is a browser chat with web search, image search, voice and Gmail, and it is also shown as a desktop window by the Tauri shell `puffin-app`.
- **Other agents:** Goose, Cline, Aider, Continue and OpenHands, through `puffin-admin run --agent …`.

Everything is administered through **`puffin-admin`**, the Python CLI.

```text
+------------------------------------------------------------------------------------+
|  Front ends                                                                        |
|   puffin (Rust, Codex fork)   Puffin web UI (Onyx Lite) / puffin-app   IDEs (MCP)  |
|   puffin-admin run --agent goose|cline|aider|continue|openhands                    |
+-----------------------+--------------------------------+---------------------------+
                        | OpenAI-compatible HTTP          | stdio MCP (puffin-admin mcp)
+-----------------------v--------------------------------v---------------------------+
|  dreamference/ (Python)                                                            |
|   config  hardware  vllm_server  runner  chat  context_engine  mcp_server  cli     |
|   agent tools: puffin-admin search / fetch (SearXNG), gmail (read-only service)    |
+-----------------------+------------------------------------------------------------+
                        | docker run
+-----------------------v------------------------------------------------------------+
|  Containers on the GB10                                                            |
|   dreamference-vllm-8000 (main model)   dreamference-diffusion-8001 (sidecar)      |
|   puffin-* (Onyx: api, web, db, nginx, code-interpreter)                           |
|   dreamference-gmail, dreamference-image-search, dreamference-stt, dreamference-searxng |
+------------------------------------------------------------------------------------+
|  NVIDIA GB10 — Blackwell GPU + Arm CPU sharing 128 GB unified memory               |
+------------------------------------------------------------------------------------+
```

---

## 2. Models

The model matrix (`hardware/model_matrix_registry.py`, `MATRIX`) is the source of truth for launch flags. Per-model vLLM tuning lives in each entry's `launch_overrides`, and a model may pin its own Docker image.

| Alias | Weights | Role |
| --- | --- | --- |
| `qwen3.5-122b-a10b-hybrid-dflash` | `Intel/Qwen3.5-122B-A10B-int4-AutoRound` | **Default** main model (`DEFAULT_MODEL_ALIAS`); DFlash speculative decoding, vision |
| `qwen3.5-122b-a10b-int4-dflash` | same weights | Tested fallback recipe |
| `qwen3.5-122b-a10b-nvfp4` | `nvidia/Qwen3.5-122B-A10B-NVFP4` | NVFP4 alternative, vision |
| `qwen3.6-35b-a3b-nvfp4` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Smaller MoE |
| `qwen3.5-122b-a10b-dflash-draft` | `z-lab/Qwen3.5-122B-A10B-DFlash` | DFlash drafter, not served on its own |
| `tiny-a2d-coder-0.5b-diffusion` | `dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1` | **Default** diffusion model, served by the sidecar (vLLM cannot serve diffusion checkpoints) |

The current recipes run at a 32k context. See `DREAMFERENCE_MODELS.md` and `DREAMFERENCE_INFERENCE.md`.

---

## 3. Packages

Each package's `__init__.py` is a re-export facade with an explicit `__all__`. The same-named top-level modules (`dreamference/config.py`, `cli.py`, …) are dead shims, shadowed by the packages.

### 3.1. `config/`: 4-tier configuration

Every field in `DreamferenceConfig.__init__` resolves, highest priority first:
1. constructor argument (the CLI);
2. `DREAMFERENCE_*` environment variable;
3. config file (`--config`, `DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`/`.json`, `~/.config/dreamference/config.toml`);
4. module-level `DEFAULT_*` constant.

`save_config()` writes only values that differ from the defaults. The package also writes the Goose config and the Goose environment.

### 3.2. `hardware/`: models, downloads, telemetry

- `model_matrix_registry.py` / `model_spec.py`: the matrix, and `ModelSpec` (`supports_vision`, `is_diffusion`, `launch_overrides`).
- `model_downloader.py`: HF downloads and tensorizer caching.
- `hardware_manager.py`, `hardware_telemetry.py`, `memory_metrics.py`: GB10 detection and memory figures.

### 3.3. `vllm_server/`: model serving and host safety

- `vllm_server_manager.py`: builds the `docker run … vllm serve` command from recipe plus config, starts, stops, removes, tails logs, and resets a stale torch.compile cache.
- `vllm_launch_options.py`: flag merging.
- `model_loading_monitor.py`, `vllm_startup_monitor.py`, `vllm_log_streamer.py`, `vllm_server_status.py`, `diagnostics.py`: load progress and status.
- **Host safety:** unified memory means a bad load can freeze the whole host. Two layers guard against it:
  - `check_host_safety()` runs *before* the load (swap, sysctl, earlyoom/systemd-oomd);
  - `psi_watchdog.MemoryPressureWatchdog` runs *during* it, sampling `/proc/pressure/memory` and killing the container on sustained pressure.
- `diffusion_server_manager.py` / `diffusion_openai_service.py`: the diffusion sidecar. It runs in the main model's image, starts *before* vLLM, and is capped at `--memory=8g`.

### 3.4. `runner/`: agents

Six runner/installer pairs plus `sandbox_manager.py`. For Codex, the default, `codex_branded_builder.py` builds `puffin` from the submodule, patches and launcher. See `DREAMFERENCE_AGENTS.md` and `DREAMFERENCE_PUFFIN_CODEX.md`.

### 3.5. `chat/`: the Puffin web UI and desktop

- `onyx_runner.py` / `onyx_installer.py`: the Onyx Lite lifecycle and `configure`: LLM provider, branding, web search, voice, Gmail, image search.
- `onyx_ui_overrides.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_scripts.py`, `onyx_brand_assets.py`: patches applied to Onyx's served CSS, JS and assets.
- `gmail_search_service.py`, `gmail_credentials.py`, `gmail_client.py`: the read-only Gmail service and its client.
- `image_search_service.py`: the image search sidecar.
- `desktop_runner.py` / `desktop_installer.py`: the Tauri window `puffin-app` (project in `desktop/`).

See `DREAMFERENCE_ONYX.md`, `DREAMFERENCE_PUFFIN_GMAIL.md` and `DREAMFERENCE_IMAGE_SEARCH.md`.

### 3.6. `context_engine/`: workspace index

Python `ast` symbols (`ast_symbol_extractor.py`), TF-IDF, SQLite FTS5 and `nomic-embed-text-v1.5` embeddings in sqlite-vec (`sqlite_context_storage.py`, `embedding_calculator.py`), all written to `.dreamference/`. It serves `workspace_search_code` over MCP and the web canvas; `puffin` does not use it. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`: IDE companion

A stdio MCP server (`puffin-admin mcp`) with `ide_*` tools over an in-process `IDEState`, plus `web_search` / `web_fetch` (`web_tools.py`) and `workspace_search_code`.

### 3.8. `cli/`: `puffin-admin`

`dreamference_cli_controller.py` (`build_parser`, `run_cli`), `model_deep_inspector.py` (`main-model inspect --deep`) and `sonnet_dataset.py` (for `benchmark_server`). See `DREAMFERENCE_CLI.md`.

### 3.9. Outside the Python package

| Path | What |
| --- | --- |
| `codex/` | Submodule: the `dgxcoder/codex` fork, pinned to `rust-v0.158.0`, never edited |
| `codex-patches/` | Patch series applied to an exported copy at build time |
| `puffin-rs/` | The launcher crate compiled into `puffin` |
| `desktop/` | Tauri project for `puffin-app` |
| `dreamference/web_canvas.py` | `puffin-admin web` status page |
| `.github/workflows/release.yml` | Manually triggered release: Python dist, desktop bundles, `puffin` binaries |

---

## 4. Roadmap & Implementation Verification

- [x] GB10 model matrix, unified-memory targeting and host-safety guards
- [x] Docker vLLM lifecycle, weight pre-download, tensorizer, benchmark, deep inspection
- [x] Diffusion sidecar beside the main model
- [x] Agent runners (Goose, Cline, Aider, Continue, OpenHands) and the stdio MCP server
- [x] Context engine (AST, FTS5, TF-IDF, embeddings) and web canvas
- [x] `puffin`: branded Codex from a pinned fork, Rust launcher, `update`, `app`, `/usage`
- [x] Puffin web UI (Onyx Lite) with branding, web and image search, voice, Gmail; desktop window
- [ ] Code index for `puffin` (codebase-memory-mcp + SCIP): proposed, `DREAMFERENCE_PUFFIN_CODE_INDEX.md`

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix and default rationale
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM launch recipes and flags
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runtimes
- **[DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md):** the `puffin` binary
- **[DREAMFERENCE_ONYX.md](./DREAMFERENCE_ONYX.md):** the Puffin web UI
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md):** context engine
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** Docker and caches
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI reference
- **[DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md):** installation
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md):** codebase layout
