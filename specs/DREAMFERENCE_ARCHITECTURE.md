# Mightling Architecture Overview

> - **Version:** 1.2.0 (`dreamference.__version__`, `setup.py`)
> - **Target Hardware:** NVIDIA GB10 (Blackwell SM121, 128 GB unified LPDDR5X)
> - **Deployment Model:** single node; the model, the code and the sessions stay on the machine. Web search, page fetch and Gmail reach the internet at the default `/airgapped off`; only `/airgapped on` allows none of them
> - **License:** AGPL-3.0-or-later
> - **Checked against the code:** 2026-10-03 (packages and modules against the tracked source tree; the diffusion switch and the roadmap on 2026-10-03; model matrix and containers on 2026-10-01)

---

## 1. Executive Summary

**Mightling** (by Dreamference) is a local agentic coding platform for a single NVIDIA GB10: inference runs on the machine and no code or prompt goes to a cloud model. It is not air-gapped by default, because the agent's web search, page fetch and Gmail tools use the internet; `/airgapped on` switches those off and takes the network away from the agent's sandboxed commands, with the holes `DREAMFERENCE_MIGHTLING_AIRGAPPED.md` lists (see also `DREAMFERENCE_MIGHTLING_EGRESS.md`). It serves open models in Docker (SGLang for the default model, vLLM for the others), and puts three front ends on the same local standard /v1 endpoint:

- **`mling`:** the terminal coding agent, and the default. It is a Mightling-branded build of Codex CLI with a Rust launcher compiled in that points it at the local model. It is built from a pinned fork (`codex/` submodule) plus small patches (`codex-patches/`) and the launcher crate (`mling-rs/`).
- **Mightling web UI:** Onyx Lite, deployed and patched by `mling-admin chat …`. It is a browser chat with web search, image search, voice and Gmail, and it is also shown as a desktop window by the Tauri shell `mling-app`.
- **Other agents:** Cline, Continue and OpenHands, through `mling-admin run --agent …`.

Everything is administered through **`mling-admin`**, the Python CLI.

Since 2026-10-02 the GB10 can also be offered to the local network as a **node**: `mling-admin node enable` advertises it over mDNS, and `mling`, `mling-search` and `mling-app` on another machine find it with no address typed. The split is built in part, and with one GB10 here nothing has run between two machines; `DREAMFERENCE_MIGHTLING_NODE.md` §18 says what was built and measured.

```text
+------------------------------------------------------------------------------------+
|  Front ends                                                                        |
|   mling (Rust, Codex fork)   Mightling web UI (Onyx Lite) / mling-app   IDEs (MCP)  |
|   mling-admin run --agent cline|continue|openhands                                |
+-----------------------+--------------------------------+---------------------------+
                        | Standard /v1 HTTP          | stdio MCP (mling-admin mcp)
+-----------------------v--------------------------------v---------------------------+
|  dreamference/ (Python)                                                            |
|   config  hardware  vllm_server  runner  chat  context_engine  mcp_server          |
|   night_shift  swe_bench  audit  node  cli                                         |
|   agent tools: gmail (read-only); mling-search, mling-fetch, mling-code: Rust   |
+-----------------------+------------------------------------------------------------+
                        | docker run
+-----------------------v------------------------------------------------------------+
|  Containers on the GB10                                                            |
|   dreamference-vllm-8000 (main model)   (diffusion sidecar: switched off)          |
|   mightling-* (Onyx: api, web, db, nginx, code-interpreter)                           |
|   dreamference-gmail, dreamference-image-search, dreamference-stt, dreamference-searxng |
+------------------------------------------------------------------------------------+
|  NVIDIA GB10 — Blackwell GPU + Arm CPU sharing 128 GB unified memory               |
+------------------------------------------------------------------------------------+
```

---

## 2. Models

The model matrix (`hardware/model_matrix_registry.py`, `MATRIX`) is the source of truth for launch flags. Per-model tuning lives in each entry's `launch_overrides`; a model may pin its own Docker image and name its engine (`engine: sglang`).

| Alias | Weights | Role |
| --- | --- | --- |
| `qwen3.8-27b-nvfp4-dflash2` | `RadixArk/Qwen3.8-27B-NVFP4` | **Default** main model (`DEFAULT_MODEL_ALIAS`, since 2026-09-29); served by SGLang with the DFlash2 drafter, 262k context, vision |
| `qwen3.5-122b-a10b-hybrid-dflash` | `Intel/Qwen3.5-122B-A10B-int4-AutoRound` | Fallback (the default until 2026-09-29); vLLM, DFlash speculative decoding, vision |
| `qwen3.5-122b-a10b-int4-dflash` | same weights | Tested fallback recipe |
| `qwen3.5-122b-a10b-nvfp4` | `nvidia/Qwen3.5-122B-A10B-NVFP4` | NVFP4 alternative, vision |
| `qwen3.6-35b-a3b-nvfp4` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Smaller MoE |
| `qwen3.8-27b-dflash2-draft` | `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` | DFlash2 drafter of the default, not served on its own |
| `qwen3.5-122b-a10b-dflash-draft` | `z-lab/Qwen3.5-122B-A10B-DFlash` | DFlash drafter, not served on its own |
| `tiny-a2d-coder-0.5b-diffusion` | `dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1` | Default diffusion model, served by the sidecar (vLLM cannot serve diffusion checkpoints). **Not offered since 2026-10-03:** diffusion is switched off (`DIFFUSION_ENABLED = False`), so it is never started, downloaded or listed |

The default runs at a 262k context; the vLLM recipes run at 32k. See `DREAMFERENCE_MODELS.md` and `DREAMFERENCE_INFERENCE.md`.

---

## 3. Packages

Each package's `__init__.py` is a re-export facade with an explicit `__all__`. The same-named top-level modules (`dreamference/config.py`, `cli.py`, …) are dead shims, shadowed by the packages.

### 3.1. `config/`: 4-tier configuration

Every field in `DreamferenceConfig.__init__` resolves, highest priority first:
1. constructor argument (the CLI);
2. `DREAMFERENCE_*` environment variable;
3. config file (`--config`, `DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`/`.json`, `~/.config/dreamference/config.toml`);
4. module-level `DEFAULT_*` constant.

`save_config()` writes only values that differ from the defaults, and carries over tables already in the file. Those tables belong to other readers: `[night]` (`NightShiftSettings`) and `[swe_bench]` (`SweBenchSettings`) are read from the same file by their own classes, not by `DreamferenceConfig`.

### 3.2. `hardware/`: models, downloads, telemetry

- `model_matrix_registry.py` / `model_spec.py`: the matrix, and `ModelSpec` (`supports_vision`, `is_diffusion`, `launch_overrides`).
- `model_downloader.py`: HF downloads and tensorizer caching.
- `hardware_manager.py`, `hardware_telemetry.py`, `memory_metrics.py`: GB10 detection and memory figures.

### 3.3. `vllm_server/`: model serving and host safety

- `vllm_server_manager.py`: builds the `docker run …` command from recipe plus config, starts, stops, removes, tails logs, and resets a stale torch.compile cache.
- `sglang_launch_builder.py`: what follows the image for a model whose recipe names `engine: sglang`; the `docker run` prefix, and so the host-safety layer, is shared with vLLM.
- `chat_template_patcher.py`: patches a checkpoint's chat template on a copy at launch, from the registry entry's `chat_template_patches`; a non-matching anchor stops the start.
- `vllm_launch_options.py`: flag merging.
- `model_loading_monitor.py`, `vllm_startup_monitor.py`, `vllm_log_streamer.py`, `vllm_server_status.py`, `diagnostics.py`: load progress and status.
- **Host safety:** unified memory means a bad load can freeze the whole host. Two layers guard against it:
  - `check_host_safety()` runs *before* the load (swap, sysctl, earlyoom/systemd-oomd);
  - `psi_watchdog.MemoryPressureWatchdog` runs *during* it, sampling `/proc/pressure/memory` and killing the container on sustained pressure.
- `diffusion_server_manager.py` / `diffusion_openai_service.py`: the diffusion sidecar. It runs in the main model's image, starts *before* vLLM, and is capped at `--memory=8g`. **Switched off since 2026-10-03** (`DIFFUSION_ENABLED = False` in `hardware/model_matrix_registry.py`): `server start` starts no sidecar and removes one an older Mightling left behind; the code and its tests are kept.

### 3.4. `runner/`: agents

Four runner/installer pairs (Codex, Cline, Continue, OpenHands; Goose and Aider were removed on 2026-09-30) plus `vllm_readiness_waiter.py`. For Codex, the default, `codex_branded_builder.py` builds `mling` from the submodule, patches and launcher, and `codex_test_runner.py` runs Codex's own tests on that tree (`mling-admin codex test`). See `DREAMFERENCE_AGENTS.md` and `DREAMFERENCE_MIGHTLING_CODEX.md`.

### 3.5. `chat/`: the Mightling web UI and desktop

- `onyx_runner.py` / `onyx_installer.py`: the Onyx Lite lifecycle and `configure`: LLM provider, branding, web search, voice, Gmail, image search.
- `onyx_ui_overrides.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_scripts.py`, `onyx_brand_assets.py`: patches applied to Onyx's served CSS, JS and assets.
- `gmail_search_service.py`, `gmail_credentials.py`, `gmail_client.py`: the read-only Gmail service and its client.
- `image_search_service.py`: the image search sidecar.
- `searxng_sidecar.py`, `sidecar_network.py`: the SearXNG container (`mling-admin searxng start`) and the user-defined network the sidecars are created on.
- `desktop_runner.py` / `desktop_installer.py`: the Tauri window `mling-app` (project in `desktop/`).

See `DREAMFERENCE_ONYX.md`, `DREAMFERENCE_MIGHTLING_GMAIL.md` and `DREAMFERENCE_IMAGE_SEARCH.md`.

### 3.6. `context_engine/`: workspace index

Python `ast` symbols (`ast_symbol_extractor.py`), TF-IDF, SQLite FTS5 and `nomic-embed-text-v1.5` embeddings stored as plain float32 blobs in SQLite (`sqlite_context_storage.py`, `embedding_calculator.py`; sqlite-vec is not used), all written to `.dreamference/`. It serves the web canvas, and `workspace_search_code` over MCP where the workspace has no `mling-code` index (§3.7); `mling` does not use it. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`: IDE companion

A stdio MCP server (`mling-admin mcp`) with `ide_*` tools over an in-process `IDEState`, plus `web_search` / `web_fetch` (`web_tools.py`) and `workspace_search_code`, which `code_index_search.py` answers from `mling-code` when the workspace is indexed and the context engine answers otherwise.

### 3.8. `cli/`: `mling-admin`

`dreamference_cli_controller.py` (`build_parser`, `run_cli`), `model_deep_inspector.py` (`main-model inspect --deep`), `sonnet_dataset.py` (for `benchmark_server`) and `code_index_setup.py` (`code setup`). See `DREAMFERENCE_CLI.md`.

### 3.9. `night_shift/`: the overnight queue

`mling-admin night {enable,disable,status,run}`: the queue the launcher writes (`night_shift_queue.py`), settings (`night_shift_settings.py`), host probes (`night_shift_host.py`), one task in its own git worktree (`night_shift_task_run.py`), admission and scheduling (`night_shift_runner.py`), the code-index refresh before a repository's tasks (`night_shift_index.py`), the morning report (`night_shift_report.py`) and the systemd user timer (`night_shift_scheduler.py`). See `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`.

### 3.10. `swe_bench/`: SWE-bench on this machine

`mling-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. The agent phase (`swe_bench_runner.py`, `swe_bench_instance_run.py`) runs one `mling exec` per instance inside that instance's own container, on an internal Docker network that reaches only the model server, with a relocated copy of `mling` (`swe_bench_runtime.py`); the upstream harness, in a virtualenv of its own (`swe_bench_harness.py`), validates instances and grades patches (`swe_bench_evaluator.py`). Images are third-party arm64 builds (`swe_bench_images.py`); `--code-index universal` adds `mling-code` as an arm (`swe_bench_code_index.py`). It shares Night Shift's admission and runner lock. See `DREAMFERENCE_MIGHTLING_SWE_BENCH.md`.

### 3.11. `audit/`: what a session does on the network

`mling-admin audit egress` (`egress_audit.py`) runs one real `mling exec` under `strace` in a throwaway repository and home, parses the trace (`strace_parser.py`, `egress_trace.py`) and gives a verdict (`egress_verdict.py`). See `DREAMFERENCE_MIGHTLING_EGRESS.md`.

### 3.12. `node/`: the GB10 as a node

`mling-admin node {enable,disable,status,list,add,remove,set,start,stop}`. Advertising is `node_advertiser.py`: the Avahi service file that advertises `_mightling-node._tcp` (`node_service_file.py`), the node's stable id (`node_identity.py`), the two switches for what is published beyond loopback (`node_settings.py`) and a browse of the network as clients see it (`node_browser.py`). Managing another node is `node_remote.py`, over an SSH pairing (`node_pairing.py`) whose key the other node restricts to one forced command (`node_serve.py`). The client side is in the Rust crates (§3.13). See `DREAMFERENCE_MIGHTLING_NODE.md`.

### 3.13. Outside the Python package

| Path | What |
| --- | --- |
| `codex/` | Submodule: the `dgxcoder/codex` fork, pinned to `rust-v0.158.0`, never edited |
| `codex-patches/` | Patch series applied to an exported copy at build time (17 patches, `0001`–`0019`) |
| `mling-rs/` | The launcher crate compiled into `mling` (also `/usage`, `/cavemode`, `/night`, `/airgapped`, and `mling node`), with two leaf crates that use only the standard library: `airgapped/` (the three levels) and `node-locator/` (where the node is) |
| `mling-web-rs/` | `mling-search` and `mling-fetch`, the agent's web commands: a standalone crate installed beside `mling`. It carries byte-identical copies of the two leaf crates' sources |
| `mling-code-rs/` | `mling-code`, the code index: a standalone crate installed beside `mling` |
| `desktop/` | Tauri project for `mling-app`; on a machine that is not the node, a loopback forwarder brings the node's web UI to `localhost:3000` |
| `dreamference/web_canvas.py` | `mling-admin web` status page |
| `.github/workflows/release.yml` | Manually triggered release: Python dist, desktop bundles, `mling` binaries |

---

## 4. Roadmap & Implementation Verification

- [x] GB10 model matrix, unified-memory targeting and host-safety guards
- [x] Docker vLLM lifecycle, weight pre-download, tensorizer, benchmark, deep inspection
- [x] Diffusion sidecar beside the main model (switched off on 2026-10-03; the code is kept)
- [x] Agent runners (Cline, Continue, OpenHands) and the stdio MCP server
- [x] Context engine (AST, FTS5, TF-IDF, embeddings) and web canvas
- [x] `mling`: branded Codex from a pinned fork, Rust launcher, `update`, `app`, `/usage`
- [x] Mightling web UI (Onyx Lite) with branding, web and image search, voice, Gmail; desktop window
- [x] Code index for `mling` (`mling-code`: codebase-memory-mcp + SCIP), implemented 2026-10-01; its §14 lists the parts not built (`DREAMFERENCE_MIGHTLING_CODE_INDEX.md`)
- [x] Cave mode (`/cavemode`) and Night Shift (`/night`, `mling-admin night`), 2026-10-01
- [x] `/airgapped` (Phase 1, in part), the egress audit for `exec` sessions (`mling-admin audit egress`) and SWE-bench (`mling-admin swe-bench`, Phase 1), 2026-10-01
- [ ] Client/server split (`mling-admin node`, `mling node`, the `mling-app` forwarder, SSH pairing between nodes): Part 1 built in part on 2026-10-02, Parts 2 and 3 (other nodes as extra model servers, `node sync-model`, `node run --setup/--out/--bind`, `/night add --on`) on 2026-10-03; nothing run between two machines (`DREAMFERENCE_MIGHTLING_NODE.md`)
- [x] `/airgapped` at two levels (`duckduckgo` removed), `on` refused with Full Access, the start-up line; the sandbox prerequisite (`mling-admin host setup`, the AppArmor profile for `bwrap`), 2026-10-03
- [x] Named system prompts (`/prompt` Phase 1: `mling prompt`, `default` and `high-swe`) and skills Phase 3 (Hermes, ClawHub, repository skills), 2026-10-03
- [ ] Proposed specs are marked *Proposed* in `specs/README.md`

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix and default rationale
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM launch recipes and flags
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runtimes
- **[DREAMFERENCE_MIGHTLING_CODEX.md](./DREAMFERENCE_MIGHTLING_CODEX.md):** the `mling` binary
- **[DREAMFERENCE_ONYX.md](./DREAMFERENCE_ONYX.md):** the Mightling web UI
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md):** context engine
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** Docker and caches
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI reference
- **[DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md):** installation
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md):** codebase layout
