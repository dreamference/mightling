# Mightling Architecture Overview

> - **Version:** 1.5.1 (`dreamference.__version__`, `setup.py`; release `v1.5.1`, 2026-10-08)
> - **Target Hardware:** NVIDIA GB10 (Blackwell SM121, 128 GB unified LPDDR5X)
> - **Deployment Model:** single node; the model, the code and the sessions stay on the machine. Web search, page fetch and Gmail reach the internet at the default `/airgapped off`; only `/airgapped on` allows none of them
> - **License:** AGPL-3.0-or-later
> - **Checked against the code:** 2026-10-09 (packages and modules against the tracked source tree, the model matrix, the containers, the launcher's subcommands, the release assets of `v1.5.1`)

---

## 1. Executive Summary

**Mightling** (by Dreamference) is a local agentic coding platform for a single NVIDIA GB10: inference runs on the machine and no code or prompt goes to a cloud model. It is not air-gapped by default, because the agent's web search, page fetch and Gmail tools use the internet; `/airgapped on` switches those off and takes the network away from the agent's sandboxed commands, with the holes `DREAMFERENCE_MIGHTLING_AIRGAPPED.md` lists (see also `DREAMFERENCE_MIGHTLING_EGRESS.md`). It serves one open model in Docker, Qwen3.8-27B on SGLang (the vLLM launcher is kept for recipes to come), and puts these front ends on the same local standard /v1 endpoint:

- **`ling`:** the terminal coding agent, and the default. It is a Mightling-branded build of Codex CLI with a Rust launcher compiled in that points it at the local model. It is built from a pinned fork (`codex/` submodule) plus small patches (`codex-patches/`) and the launcher crate (`ling-rs/`).
- **`ling web`:** Mightling's own web UI (`ling-rs/web/`, the page in `desktop/ui/`), served on port 3100. **Ask** threads are `ling` sessions under a bridge policy (the `ask` prompt, a scratch folder, uploads); **Work** drives `ling app-server`. A credential is checked on every request, and phones and other devices are paired. See `DREAMFERENCE_MIGHTLING_ASK.md`.
- **The desktop app `ling-app`:** an Electron app (`desktop/electron/`) with `ling` bundled inside: its Chat entry opens Ask on `ling web`, its Work window drives `ling app-server` over stdio. A `.deb` for Linux; a Mac `.dmg` as an unsigned preview. See `DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md`.
- **Onyx Lite**, the earlier web chat, deployed and patched by `ling-admin chat …`, with web search, image search, voice and Gmail. It stays until `ling web` matches it, then is retired (MIGHTLING_ASK §10, Phase C).
- **Messengers, off by default:** Signal (`ling signal`, linked to the owner's account, answering in Note to Self) and Matrix or Telegram (`ling chat`, with the homeserver from `ling-admin matrix`). Each is a paired client of `ling web`, so every message becomes an Ask thread. See `DREAMFERENCE_MIGHTLING_SIGNAL.md` and `DREAMFERENCE_MIGHTLING_CHAT.md`.
- **Other agents:** Cline, Continue and OpenHands, through `ling-admin run --agent …`.

The agent's tools beyond Codex's own are commands installed beside `ling`: `ling-search` and `ling-fetch` (the web, through a local SearXNG), `ling-code` (the code index, over MCP), `ling-docs` (the user's own documents, over MCP) and `ling-admin gmail`; `/apps` lists read-only Gmail, Google Drive and Google Calendar.

Everything is administered through **`ling-admin`**, the Python CLI.

Since 2026-10-02 the GB10 can also be offered to the local network as a **node**: `ling-admin node enable` advertises it over mDNS (`_mightling-node._tcp`), and `ling` and `ling-search` on another machine find it with no address typed. Clients are released for Linux (arm64 and x86-64), macOS and, as an unsigned preview, Windows (`install.ps1`). A second GB10 joined on 2026-10-08: the two were paired over SSH and both upgraded to 1.5.1 (`DREAMFERENCE_MIGHTLING_NODE.md` §18.10). `ling-admin node provision` sets up more GB10s from a node (`DREAMFERENCE_MIGHTLING_FLEET.md`).

```text
+------------------------------------------------------------------------------------+
|  Front ends                                                                        |
|   ling (Rust, Codex fork)   ling web :3100 (Ask, Work) / ling-app (Electron)       |
|   Onyx Lite (until retired)   IDEs (MCP)   ling signal / ling chat (off by default)|
|   ling-admin run --agent cline|continue|openhands                                  |
+-----------------------+--------------------------------+---------------------------+
                        | Standard /v1 HTTP              | stdio MCP (ling-admin mcp)
+-----------------------v--------------------------------v---------------------------+
|  dreamference/ (Python, ling-admin)                                                |
|   config  hardware  vllm_server  runner  chat  context_engine  mcp_server          |
|   night_shift  swe_bench  audit  node  cli                                         |
|  Rust beside ling: ling-search, ling-fetch, ling-code, ling-docs, ling-signal      |
+-----------------------+------------------------------------------------------------+
                        | docker run
+-----------------------v------------------------------------------------------------+
|  Containers on the GB10                                                            |
|   dreamference-vllm-8000 (the model: SGLang)   (diffusion sidecar: switched off)   |
|   Onyx (api_server, web_server, relational_db, nginx, code-interpreter)            |
|   dreamference-gmail, -searxng, -image-search, -stt; dreamference-matrix (off)     |
+------------------------------------------------------------------------------------+
|  NVIDIA GB10 — Blackwell GPU + Arm CPU sharing 128 GB unified memory               |
+------------------------------------------------------------------------------------+
```

---

## 2. Models

The model matrix (`hardware/model_matrix_registry.py`, `MATRIX`) is the source of truth for launch flags. Per-model tuning lives in each entry's `launch_overrides`; a model may pin its own Docker image and name its engine (`engine: sglang`).

| Alias | Weights | Role |
| --- | --- | --- |
| `qwen3.8-27b-nvfp4-dflash2` | `RadixArk/Qwen3.8-27B-NVFP4` | **The** main model (`DEFAULT_MODEL_ALIAS`, since 2026-09-29); served by SGLang with the DFlash2 drafter, 262K context, vision |
| `qwen3.8-27b-dflash2-draft` | `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` | Its DFlash2 drafter, not served on its own |
| `tiny-a2d-coder-0.5b-diffusion` | `dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1` | Default diffusion model, served by the sidecar (vLLM cannot serve diffusion checkpoints). **Not offered since 2026-10-03:** diffusion is switched off (`DIFFUSION_ENABLED = False`), so it is never started, downloaded or listed |

The Qwen 3.5 122B and Qwen 3.6 35B entries, their drafter and their vLLM images were removed on 2026-10-07; a removed alias is answered with the release that removed it and the command that switches to the main model. See `DREAMFERENCE_MODELS.md` and `DREAMFERENCE_INFERENCE.md`.

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

Four runner/installer pairs (Codex, Cline, Continue, OpenHands; Goose and Aider were removed on 2026-09-30) plus `vllm_readiness_waiter.py` and `docs_index_setup.py` (`ling-admin docs setup`, the models `ling-docs` loads). For Codex, the default, `codex_branded_builder.py` builds `ling` from the submodule, patches and launcher, and `codex_test_runner.py` runs Codex's own tests on that tree (`ling-admin codex test`). See `DREAMFERENCE_AGENTS.md` and `DREAMFERENCE_MIGHTLING_CODEX.md`.

### 3.5. `chat/`: the web chat, its services and the desktop app

- `onyx_runner.py` / `onyx_installer.py`: the Onyx Lite lifecycle and `configure`: LLM provider, branding, web search, voice, Gmail, image search; `chat_admin_credentials.py`: its per-install administrator password.
- `onyx_ui_overrides.py`, `onyx_ui_fonts.py`, `onyx_ui_labels.py`, `onyx_ui_scripts.py`, `onyx_brand_assets.py`: patches applied to Onyx's served CSS, JS and assets.
- `gmail_search_service.py`, `google_workspace_reader.py`, `gmail_credentials.py`, `gmail_client.py`: the Google service (read-only Gmail, Drive and Calendar) and the Gmail client; `google_service.py` starts that service without Onyx (`ling-admin google start|stop|status`).
- `image_search_service.py`: the image search sidecar.
- `searxng_sidecar.py`, `sidecar_network.py`: the SearXNG container (`ling-admin searxng start`) and the user-defined network the sidecars are created on.
- `matrix_homeserver.py`: the private Matrix homeserver behind `ling chat` (`ling-admin matrix`), off by default.
- `desktop_runner.py` / `desktop_installer.py` / `desktop_protocol_types.py`: building and running the Electron app `ling-app` (project in `desktop/electron/`, UI in `desktop/ui/`) and the app-server's TypeScript types for Work.

See `DREAMFERENCE_ONYX.md`, `DREAMFERENCE_MIGHTLING_GMAIL.md`, `DREAMFERENCE_MIGHTLING_APPS.md`, `DREAMFERENCE_IMAGE_SEARCH.md` and `DREAMFERENCE_MIGHTLING_CHAT.md`.

### 3.6. `context_engine/`: workspace index

Python `ast` symbols (`ast_symbol_extractor.py`), TF-IDF, SQLite FTS5 and `nomic-embed-text-v1.5` embeddings stored as plain float32 blobs in SQLite (`sqlite_context_storage.py`, `embedding_calculator.py`; sqlite-vec is not used), all written to `.dreamference/`. It serves the web canvas, and `workspace_search_code` over MCP where the workspace has no `ling-code` index (§3.7); `ling` does not use it. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`: IDE companion

A stdio MCP server (`ling-admin mcp`) with `ide_*` tools over an in-process `IDEState`, plus `web_search` / `web_fetch` (`web_tools.py`) and `workspace_search_code`, which `code_index_search.py` answers from `ling-code` when the workspace is indexed and the context engine answers otherwise.

### 3.8. `cli/`: `ling-admin`

`dreamference_cli_controller.py` (`build_parser`, `run_cli`), `model_deep_inspector.py` (`main-model inspect --deep`), `sonnet_dataset.py` (for `benchmark_server`), `code_index_setup.py` (`code setup`) and `legacy_name_migration.py` (the one-time Puffin → Mightling move, from an installed release only). See `DREAMFERENCE_CLI.md`.

### 3.9. `night_shift/`: the overnight queue

`ling-admin night {enable,disable,status,run}`: the queue the launcher writes (`night_shift_queue.py`), settings (`night_shift_settings.py`), host probes (`night_shift_host.py`), one task in its own git worktree (`night_shift_task_run.py`), admission and scheduling (`night_shift_runner.py`), the code-index refresh before a repository's tasks (`night_shift_index.py`), the morning report (`night_shift_report.py`), the systemd user timer (`night_shift_scheduler.py`), tasks handed to another node (`night_shift_remote.py`) and refine mode's texts (`refine_prompt.py`). See `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`.

### 3.10. `swe_bench/`: SWE-bench on this machine

`ling-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. The agent phase (`swe_bench_runner.py`, `swe_bench_instance_run.py`) runs one `ling exec` per instance inside that instance's own container, on an internal Docker network that reaches only the model server, with a relocated copy of `ling` (`swe_bench_runtime.py`); the upstream harness, in a virtualenv of its own (`swe_bench_harness.py`), validates instances and grades patches (`swe_bench_evaluator.py`). Images are third-party arm64 builds (`swe_bench_images.py`); `--code-index universal` adds `ling-code` as an arm (`swe_bench_code_index.py`), `--strip-names` takes the fix's names out of the issue (`swe_bench_name_stripper.py`), `eval --drop-test-hunks` regrades without the agent's test edits (`swe_bench_patch_filter.py`), and a relay reaches another node's model server (`swe_bench_relay.py`). It shares Night Shift's admission and runner lock. See `DREAMFERENCE_MIGHTLING_SWE_BENCH.md`.

### 3.11. `audit/`: what a session does on the network

`ling-admin audit egress` (`egress_audit.py`) runs one real `ling exec` under `strace` in a throwaway repository and home, parses the trace (`strace_parser.py`, `egress_trace.py`) and gives a verdict (`egress_verdict.py`); `--tui` drives the full-screen interface on a pseudo-terminal (`tui_session.py`) and `--docs` runs the document index's scenario (`docs_egress_audit.py`). See `DREAMFERENCE_MIGHTLING_EGRESS.md`.

### 3.12. `node/`: the GB10 as a node

`ling-admin node {enable,disable,status,id,list,add,remove,set,start,stop,sync-model,run,jobs,logs,cancel,fetch,prepare,provision}`. Advertising is `node_advertiser.py`: the Avahi service file that advertises `_mightling-node._tcp` (`node_service_file.py`), the node's stable id (`node_identity.py`), the two switches for what is published beyond loopback (`node_settings.py`) and a browse of the network as clients see it (`node_browser.py`). Managing another node is `node_remote.py`, over an SSH pairing (`node_pairing.py`) whose key the other node restricts to one forced command (`node_serve.py`). Jobs on another node are `node_job.py` and `node_job_sender.py`, `node sync-model` is `node_model_sync.py`, and `node_lanes.py` spreads a run's tasks over several model servers. `node prepare` (`node_prepare.py`, the root half of an install) and `node provision` (`node_provisioner.py` with the `fleet_*.py` helpers) set up more GB10s from this one. The client side is in the Rust crates (§3.13). See `DREAMFERENCE_MIGHTLING_NODE.md`.

### 3.13. Outside the Python package

| Path | What |
| --- | --- |
| `codex/` | Submodule: the `dgxcoder/codex` fork, pinned to `rust-v0.158.0`, never edited |
| `codex-patches/` | Patch series applied to an exported copy at build time (23 patches, `0001`–`0025`, 42,741 bytes against a 43 KB cap) |
| `ling-rs/` | The launcher crate compiled into `ling`: the model-server resolution, the catalog and prompts, `update`, `/usage`, `/cavemode`, `/night`, `/airgapped`, `/node`, `ling skill`, `ling prompt`, `ling docs`, refine, the compaction ledger, and the subcommands `ling web`, `ling chat` and `ling signal`. Member crates: `airgapped/` and `node-locator/` (standard library only), `masking/`, `skills/`, `apps/`, `tools/`, `web/` (`ling web`), `signal/` (also the `ling-signal` daemon) and `chat/` |
| `ling-web-rs/` | `ling-search` and `ling-fetch`, the agent's web commands: a standalone crate installed beside `ling`. It carries byte-identical copies of the two leaf crates' sources |
| `ling-code-rs/` | `ling-code`, the code index: a standalone crate installed beside `ling` |
| `ling-docs-rs/` | `ling-docs`, the index of the user's own documents (text, Markdown, PDF): a standalone crate, released for Linux beside `ling` from 1.6.0; `ling-admin docs setup` fetches what it loads (PDFium, ONNX Runtime, the embedding model), pinned |
| `desktop/electron/`, `desktop/ui/` | The Electron app `ling-app` and the Mightling UI it shares with `ling web` |
| `ling-engine/` | Submodule: the future model server for Qwen3.8-27B on DGX Spark (C++20 and CUDA), its own repository; nothing here builds it |
| `dreamference/web_canvas.py` | `ling-admin web` status page |
| `.github/workflows/` | `release.yml` (manually triggered: Python dist, `ling` and its companions for Linux, macOS and Windows, the desktop `.deb`; `SHA256SUMS` signed with Ed25519 in the `release` environment), `build-clients.yml`, `windows.yml`, `mac-preview.yml`, `docs.yml`, `cla.yml` |

---

## 4. Roadmap & Implementation Verification

- [x] GB10 model matrix, unified-memory targeting and host-safety guards
- [x] Docker vLLM lifecycle, weight pre-download, tensorizer, benchmark, deep inspection
- [x] Diffusion sidecar beside the main model (switched off on 2026-10-03; the code is kept)
- [x] Agent runners (Cline, Continue, OpenHands) and the stdio MCP server
- [x] Context engine (AST, FTS5, TF-IDF, embeddings) and web canvas
- [x] `ling`: branded Codex from a pinned fork, Rust launcher, `update`, `app`, `/usage`
- [x] Mightling web UI (Onyx Lite) with branding, web and image search, voice, Gmail; desktop window
- [x] Code index for `ling` (`ling-code`: codebase-memory-mcp + SCIP), implemented 2026-10-01; its §14 lists the parts not built (`DREAMFERENCE_MIGHTLING_CODE_INDEX.md`)
- [x] Cave mode (`/cavemode`) and Night Shift (`/night`, `ling-admin night`), 2026-10-01
- [x] `/airgapped` (Phase 1, in part), the egress audit for `exec` sessions (`ling-admin audit egress`) and SWE-bench (`ling-admin swe-bench`, Phase 1), 2026-10-01
- [x] Client/server split (`ling-admin node`, `ling node`, SSH pairing between nodes): Part 1 built in part on 2026-10-02, Parts 2 and 3 (other nodes as extra model servers, `node sync-model`, `node run --setup/--out/--bind`, `/night add --on`) on 2026-10-03; two GB10s paired on 2026-10-08 (`DREAMFERENCE_MIGHTLING_NODE.md` §18.10); sending from a client is not built
- [x] `/airgapped` at two levels (`duckduckgo` removed), `on` refused with Full Access, the start-up line; the sandbox prerequisite (`ling-admin host setup`, the AppArmor profile for `bwrap`), 2026-10-03
- [x] Named system prompts (`/prompt` Phase 1: `ling prompt`, `default` and `high-swe`) and skills Phase 3 (Hermes, ClawHub, repository skills), 2026-10-03
- [x] Observation masking (patch `0021`), `/apps` without a vendor sign-in (`0022`), the desktop Work window on `ling app-server`, 2026-10-06 (release 1.4.0)
- [x] Puffin renamed Mightling (`ling`, `ling-admin`, `~/.mightling`) with a one-time migration; signed releases (Ed25519 over `SHA256SUMS`); the desktop app rebuilt on Electron; refine mode (off by default); clients for x86-64 Linux, macOS and Windows (preview), 2026-10-07 and 2026-10-08 (release 1.5.1)
- [x] `node provision` / `node prepare` (FLEET Phases 1–3, tested offline; not yet run against a second machine) and the unattended node install (`install.sh`: one sudo prompt at most, host setup, `node enable`, model download and server start), 1.5.1
- [x] `ling web` with Ask threads, the bridge policy and the browser view; `ling-docs` Phase 1; Signal and Matrix/Telegram bridges, off by default (on `main` for 1.6.0)
- [ ] Onyx retired (MIGHTLING_ASK Phase C), messengers tried against real services, the Windows client run on Windows hardware
- [ ] Proposed specs are marked *Proposed* in `specs/README.md`

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** model matrix and default rationale
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM launch recipes and flags
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agent runtimes
- **[DREAMFERENCE_MIGHTLING_CODEX.md](./DREAMFERENCE_MIGHTLING_CODEX.md):** the `ling` binary
- **[DREAMFERENCE_ONYX.md](./DREAMFERENCE_ONYX.md):** the Mightling web UI
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md):** context engine
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** Docker and caches
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md):** CLI reference
- **[DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md):** installation
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md):** codebase layout
