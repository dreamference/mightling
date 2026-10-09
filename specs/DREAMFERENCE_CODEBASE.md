# Mightling Codebase Architecture & Reference

> **Version:** 1.5.1
> **Subject:** Source Code Layout, Module Organization, Package Structure
> **Checked against the code:** 2026-10-09 (module tree against the tracked source tree, by script: every Python module under `dreamference/` is named below, and every Rust crate and the desktop projects by folder; §5 against the registry, its command last recorded from `build_launch_command()` on 2026-10-01)

---

## Table of Contents

- [1. Package Overview](#1-package-overview)
- [2. Module Structure](#2-module-structure)
- [3. Subsystem Packages](#3-subsystem-packages)
- [4. Import Conventions](#4-import-conventions)
- [5. Launch Command for the Main Model](#5-launch-command-for-the-main-model)

---

## 1. Package Overview

**Version:** `dreamference.__version__ == "1.5.1"`.

**Architecture:** twelve subsystem packages under `dreamference/`. Each `__init__.py` is a re-export facade with an explicit `__all__`. Alongside them sit the Rust launcher `ling-rs/` (with its member crates: the web UI server, Signal and chat bridges, apps, skills and the leaf crates), the web commands `ling-web-rs/`, the code index `ling-code-rs/`, the document index `ling-docs-rs/`, the Codex fork `codex/` (submodule) with its patches `codex-patches/`, the desktop app `desktop/` (Electron shell `desktop/electron/` and the shared UI `desktop/ui/`), and the `ling-engine/` submodule (the future model server's own repository; nothing here builds it).

**Dead shims:** the top-level `dreamference/<name>.py` modules (`cli.py`, `config.py`, `hardware.py`, …) contain `from dreamference.<name>.__init__ import *`. They **never execute**: Python resolves the same-named package directory first. Editing them has no effect.

**Conventions:**
- one class per file, named after the class;
- stateless helpers as `@classmethod`s;
- `typing.Final` module constants;
- Google-style docstrings.

### 1.1. Subsystem Packages

| Package | Role |
| :--- | :--- |
| `dreamference/config/` | 4-tier config resolution, config generation |
| `dreamference/hardware/` | GB10 detection and telemetry, model matrix, HF downloads and tensorization |
| `dreamference/vllm_server/` | Docker model-server lifecycle (vLLM and SGLang), launch arguments, chat-template patching, host-safety guards, diffusion sidecar |
| `dreamference/runner/` | Four agent installer/runner pairs, the readiness waiter, the `ling` builder, the Codex test runner, `ling-docs`'s model setup |
| `dreamference/chat/` | Onyx Lite (the web chat until its retirement) lifecycle and patches, the Google service (Gmail, Drive, Calendar), image search, the SearXNG sidecar and the sidecar network, the Matrix homeserver, the desktop app's runner |
| `dreamference/context_engine/` | AST symbols, TF-IDF, FTS5 and dense retrieval |
| `dreamference/mcp_server/` | stdio MCP server for JetBrains / VS Code, web tools, and code search through `ling-code` |
| `dreamference/cli/` | `ling-admin`, deep model inspection, benchmark dataset, code-index tool setup, the Puffin → Mightling migration |
| `dreamference/night_shift/` | Night Shift: the overnight run of the `/night` queue, its timer and report |
| `dreamference/swe_bench/` | `ling-admin swe-bench`: `ling` over SWE-bench instances, graded by the upstream harness |
| `dreamference/audit/` | `ling-admin audit egress`: a traced `ling` session and a verdict on where it connected |
| `dreamference/node/` | The node half of the client/server split: the advertised service, the node id, what is published to the LAN, managing other nodes over an SSH pairing, jobs, model sync, lanes, and provisioning more GB10s (`node prepare`, `node provision`) |

---

## 2. Module Structure

```
dreamference/
├── __init__.py                       # __version__ = "1.5.1" only (no re-exports)
├── web_canvas.py                     # CanvasHandler + start_web_canvas_server (ling-admin web)
├── {cli,config,context_engine,hardware,mcp_server,runner,vllm_server}.py   # dead shims (see §1)
│
├── cli/
│   ├── dreamference_cli_controller.py    # DreamferenceCLIController, main()
│   ├── model_deep_inspector.py           # ModelDeepInspector (main-model inspect --deep)
│   ├── sonnet_dataset.py                 # embedded Sonnet corpus for benchmark_server
│   ├── code_index_setup.py               # CodeIndexSetup, PinnedTool (ling-admin code setup)
│   └── legacy_name_migration.py          # LegacyNameMigration (ling-admin's half of the Puffin → Mightling move)
├── config/
│   ├── dreamference_config.py            # DreamferenceConfig: 4-tier resolution
│   ├── config_path_resolver.py           # ConfigPathResolver
│   ├── config_file_storage_manager.py    # ConfigFileStorageManager
│   └── config_generator.py               # generate_default_init_config (functions, no class)
├── hardware/
│   ├── model_matrix_registry.py          # ModelMatrixRegistry (MATRIX), DEFAULT_MODEL_ALIAS
│   ├── model_spec.py                     # ModelSpec
│   ├── model_downloader.py               # ModelDownloader
│   ├── hardware_manager.py               # HardwareManager
│   ├── hardware_telemetry.py             # HardwareTelemetry
│   └── memory_metrics.py                 # MemoryMetrics
├── vllm_server/
│   ├── vllm_server_manager.py            # VLLMServerManager
│   ├── vllm_launch_options.py            # VLLMLaunchOptions
│   ├── sglang_launch_builder.py          # SGLangLaunchBuilder (engine: sglang, the main model)
│   ├── chat_template_patcher.py          # ChatTemplatePatcher (chat_template_patches, on a copy)
│   ├── vllm_log_streamer.py              # VLLMLogStreamer
│   ├── vllm_server_status.py             # VLLMServerStatus
│   ├── vllm_startup_monitor.py           # VLLMStartupMonitor
│   ├── model_loading_monitor.py          # ModelLoadingMonitor
│   ├── psi_watchdog.py                   # MemoryPressureWatchdog
│   ├── diagnostics.py                    # ContainerDiagnostics
│   ├── host_safety_setup.py              # HostSafetySetup (ling-admin host check|setup)
│   ├── sandbox_prerequisite.py           # SandboxPrerequisite (bubblewrap's AppArmor profile, checked every run)
│   ├── diffusion_server_manager.py       # DiffusionServerManager
│   └── diffusion_openai_service.py       # DiffusionModelRunner (runs inside the sidecar container)
├── runner/
│   ├── codex_runner.py / codex_installer.py    # CodexRunner / CodexInstaller (default agent)
│   ├── codex_branded_builder.py                 # CodexBrandedBuilder (builds ling, the web commands, ling-code)
│   ├── codex_test_runner.py                     # CodexTestRunner (ling-admin codex test)
│   ├── cline_runner.py / cline_installer.py
│   ├── continue_runner.py / continue_installer.py
│   ├── openhands_runner.py / openhands_installer.py
│   ├── docs_index_setup.py                      # DocsIndexSetup (ling-admin docs setup: what ling-docs loads)
│   └── vllm_readiness_waiter.py                 # VLLMReadinessWaiter (wait_for_vllm)
├── chat/
│   ├── onyx_runner.py / onyx_installer.py       # OnyxRunner / OnyxInstaller
│   ├── onyx_ui_overrides.py                     # OnyxUIOverrides (appended CSS)
│   ├── onyx_ui_fonts.py                         # OnyxUIFonts (@font-face swap)
│   ├── onyx_ui_labels.py                        # OnyxUILabels (string rewrites in JS)
│   ├── onyx_ui_scripts.py                       # OnyxUIScripts (injected behaviour)
│   ├── onyx_brand_assets.py                     # OnyxBrandAssets (logos, favicon, app icon)
│   ├── chat_admin_credentials.py                # ChatAdminCredentials (the web chat's per-install admin password)
│   ├── gmail_search_service.py                  # GmailSearchService (the Google service inside its container)
│   ├── google_workspace_reader.py               # GoogleWorkspaceReader (read-only Drive and Calendar, staged beside it)
│   ├── google_service.py                        # GoogleService (ling-admin google start|stop|status, without the web UI)
│   ├── gmail_credentials.py                     # GmailCredentials
│   ├── gmail_client.py                          # GmailClient (ling-admin gmail)
│   ├── matrix_homeserver.py                     # MatrixHomeserver (ling-admin matrix: tuwunel on an internal network)
│   ├── image_search_service.py                  # ImageSearchService, HardenedFetcher, ImageStore, SearxngClient, SiglipClient, VisionRanker, FetchRejected
│   ├── searxng_sidecar.py                       # SearxngSidecar (ling-admin searxng start)
│   ├── sidecar_network.py                       # SidecarNetwork (the user-defined network sidecars are created on)
│   ├── desktop_protocol_types.py                # DesktopProtocolTypes (the app-server's TypeScript types for Work)
│   └── desktop_runner.py / desktop_installer.py # DesktopRunner / DesktopInstaller (ling-app)
├── context_engine/
│   ├── context_engine.py                 # ContextEngine
│   ├── ast_symbol_extractor.py           # ASTSymbolExtractor (Python ast)
│   ├── tfidf_calculator.py               # TFIDFCalculator
│   ├── sqlite_context_storage.py         # SQLiteContextStorage
│   ├── embedding_calculator.py           # EmbeddingCalculator (nomic-embed-text-v1.5)
│   ├── code_symbol.py                    # CodeSymbol
│   └── indexed_file.py                   # IndexedFile
├── mcp_server/
│   ├── mcp_server.py                     # MCPServer
│   ├── mcp_tool_registry.py              # MCPToolRegistry
│   ├── web_tools.py                      # WebTools (the MCP web_search / web_fetch tools)
│   ├── code_index_search.py              # CodeIndexSearch (workspace_search_code through ling-code)
│   ├── ide_state.py                      # IDEState
│   └── editor_selection.py               # EditorSelection
├── night_shift/
│   ├── night_shift_runner.py             # NightShiftRunner (ling-admin night run: admission, scheduling)
│   ├── night_shift_task_run.py           # NightShiftTaskRun (one task: worktree, ling exec, tests, commit)
│   ├── night_shift_queue.py              # NightShiftQueue (the files under $CODEX_HOME/night, the runner lock)
│   ├── night_shift_host.py               # NightShiftHost (read-only probes of the server and the host)
│   ├── night_shift_index.py              # NightShiftIndex (refreshes a repository's code index before its tasks)
│   ├── night_shift_remote.py             # NightShiftRemote (/night add --on <node>: tasks worked by another node)
│   ├── refine_prompt.py                  # RefinePrompt (refine mode's texts: study, then solve)
│   ├── night_shift_settings.py           # NightShiftSettings (the [night] table)
│   ├── night_shift_report.py             # NightShiftReport (the morning report)
│   └── night_shift_scheduler.py          # NightShiftScheduler (the systemd user timer)
├── swe_bench/
│   ├── swe_bench_command.py              # SweBenchCommand (ling-admin swe-bench: parser and dispatch)
│   ├── swe_bench_settings.py             # SweBenchSettings (the [swe_bench] table, paths, pins)
│   ├── swe_bench_harness.py              # SweBenchHarness (the upstream harness in its own virtualenv)
│   ├── swe_bench_images.py               # SweBenchImages (arm64 instance images, the validated list)
│   ├── swe_bench_runtime.py              # SweBenchRuntime (the relocated ling that starts in an instance image)
│   ├── swe_bench_docker.py               # SweBenchDocker (the one place the benchmark runs docker)
│   ├── swe_bench_runner.py               # SweBenchRunner (swe-bench run: admission, scheduling, resume)
│   ├── swe_bench_instance_run.py         # SweBenchInstanceRun (one instance: container, ling exec, prediction)
│   ├── swe_bench_code_index.py           # SweBenchCodeIndex (--code-index universal: index on the host, mount read-only)
│   ├── swe_bench_name_stripper.py        # SweBenchNameStripper (swe-bench run --strip-names)
│   ├── swe_bench_patch_filter.py         # SweBenchPatchFilter (eval --drop-test-hunks: test files out of a patch)
│   ├── swe_bench_relay.py                # SweBenchRelay (the gateway relay to another node's model server)
│   ├── swe_bench_evaluator.py            # SweBenchEvaluator (validation and grading through the harness)
│   ├── swe_bench_run_store.py            # SweBenchRunStore (one run's files)
│   └── swe_bench_report.py               # SweBenchReport (a run's report, two runs compared)
├── audit/
│   ├── egress_audit.py                   # EgressAudit (ling-admin audit egress: the traced session)
│   ├── tui_session.py                    # TuiSession (--tui: a session driven on a pseudo-terminal)
│   ├── docs_egress_audit.py              # DocsEgressAudit (--docs: the local file index's scenario)
│   ├── strace_parser.py                  # StraceParser (reads the strace output)
│   ├── egress_trace.py                   # EgressTrace (destinations, DNS names, processes)
│   └── egress_verdict.py                 # EgressVerdict (pass, fail or trace failed, with reasons)
└── node/
    ├── node_advertiser.py                # NodeAdvertiser (ling-admin node enable|disable|status)
    ├── node_service_file.py              # NodeServiceFile (the Avahi service file, _mightling-node._tcp)
    ├── node_identity.py                  # NodeIdentity (the node's stable id)
    ├── node_settings.py                  # NodeSettings (~/.config/dreamference/node-advertise.json)
    ├── node_browser.py                   # NodeBrowser (what a browse of the network returns)
    ├── node_remote.py                    # NodeRemote (node list|set|start|stop: managing other nodes from this one)
    ├── node_pairing.py                   # NodePairing (node add|remove: a key restricted to one forced command)
    ├── node_serve.py                     # NodeServe (node serve-job: the operations a paired key may ask for)
    ├── node_job.py                       # NodeJob (a job sent to this node, from record to result branch)
    ├── node_job_sender.py                # NodeJobSender (node run|jobs|logs|cancel|fetch)
    ├── node_model_sync.py                # NodeModelSync (node sync-model)
    ├── node_lanes.py                     # NodeLanes (the model servers one run spreads its tasks over)
    ├── node_prepare.py                   # NodePrepare (sudo ling-admin node prepare: the root steps of a node install)
    ├── node_provisioner.py               # NodeProvisioner (ling-admin node provision)
    ├── fleet_probe.py                    # FleetProbe (a machine's state before provisioning changes it)
    ├── fleet_session.py                  # FleetSession (the provisioning SSH session to one machine)
    ├── fleet_askpass.py                  # FleetAskpass (answers ssh's password prompt during provisioning)
    ├── fleet_bundle.py                   # FleetBundle (the bundle install.sh --from installs)
    └── fleet_model_plan.py               # FleetModelPlan (what a node needs for its model)

scripts/                                  # at the repository root, not inside the package
├── install_gb10.sh                       # full installation from a checkout
├── run_vllm_gb10.sh                      # thin wrapper over ling-admin server start
├── package_mightling.sh                  # the release packaging of the binaries
├── gen_admin_reference.py                # regenerates docs/admin.md from build_parser()
├── rename_mightling.py                   # the Puffin → Mightling rename check
├── context_budget_cache_probe.py, context_budget_replay.py   # CONTEXT_BUDGET measurements
├── swe_bench_compare.py                  # the published-results comparison (SWE_BENCH_COMPARISON)
├── swe_bench_fresh.py, swe_bench_night1.sh   # fresh validated tasks and the nightly quality A/B
└── cave_mode_bench/                      # the cave-mode benchmark and its level texts

ling-rs/src/*.rs                              # the launcher compiled into ling: lib, help, home, app, update, usage,
                                              # cave, night, code_index, docs_index, airgapped, node, node_command,
                                              # apps, audit, compaction, ledger, mask, notice, prompt, refine,
                                              # release_signature, rename, skills, signal, chat, web
ling-rs/airgapped/                            # crate ling-airgapped: the levels and their resolution (std only)
ling-rs/node-locator/                         # crate ling-node-locator: where the node is (std only)
ling-rs/masking/                              # crate ling-masking: observation masking (patch 0021)
ling-rs/skills/                               # crate ling-skills: links, preflight and budget for other agents' skills
ling-rs/apps/                                 # crate ling-apps: /apps over MCP (Gmail, Drive, Calendar)
ling-rs/tools/                                # crate ling-tools
ling-rs/web/                                  # crate ling-web-server: ling web (UI server, Ask threads, bridge policy)
ling-rs/signal/                               # crate ling-signal: the Signal bridge (library and daemon binary)
ling-rs/chat/                                 # crate ling-chat: ling chat (Matrix and Telegram)
ling-rs/cave/, ling-rs/prompts/               # data: cave-mode level texts; the named system prompts (ask, high-swe, refine)
ling-web-rs/src/{lib,search,fetch,read,html_text,airgapped,node_locator}.rs, src/bin/   # ling-search, ling-fetch
ling-code-rs/src/                             # ling-code, the code index (router, SCIP stores, submodules, session, MCP)
ling-docs-rs/src/                             # ling-docs, the local document index (collections, extraction, embeddings, MCP)
codex-patches/00NN-*.patch                    # patch series for the codex/ submodule (23 patches, 0001–0025; 0003–0004 retired)
desktop/electron/src/                         # the Electron desktop app (main process, bridge, egress, Work)
desktop/ui/src/                               # the Mightling UI (Ask and Work), served by ling web and app://
```

---

## 3. Subsystem Packages

### 3.1. `config/`

`DreamferenceConfig.__init__` resolves every field: constructor argument, then `DREAMFERENCE_*` env var, then config file, then `DEFAULT_*`. It also validates the memory budget (`validate_model`), and resolves the tool-call parser. `ConfigPathResolver` finds the file: `--config`, `DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml|.json`, `~/.config/dreamference/config.toml`. `save_config()` writes only non-default values.

### 3.2. `hardware/`

`ModelMatrixRegistry.MATRIX` is the source of truth for models and their `launch_overrides`, which is the per-model recipe, including an optional pinned `docker_image` and an optional `engine` (`sglang` for the default model; vLLM otherwise). `ModelSpec` also records `supports_vision` and `is_diffusion`. `ModelDownloader` manages the HF cache (`$HF_HOME/hub`) and the tensorizer cache (`~/.cache/dreamference/tensorizer`).

### 3.3. `vllm_server/`

- **`VLLMServerManager`:** builds the `docker run …` command (§5; for an `engine: sglang` recipe `SGLangLaunchBuilder` supplies what follows the image, and `ChatTemplatePatcher` the patched template), runs the host-safety pre-flight (`check_host_safety`), starts under `MemoryPressureWatchdog`, stops, removes, tails logs, and resets a stale torch.compile cache (`_reset_stale_compile_cache`).
- **`DiffusionServerManager`:** runs the diffusion sidecar (`diffusion_openai_service.py`) in the main model's image before vLLM starts, capped at 8 GB. Switched off since 2026-10-03 (`ModelMatrixRegistry.diffusion_enabled()` is false): `server start` only calls `remove_leftover()` on a sidecar an older Mightling left behind.

### 3.4. `runner/`

Four pairs: Codex (default), Cline, Continue and OpenHands, plus `VLLMReadinessWaiter` (the non-Codex runners' wait for the server), `CodexBrandedBuilder`, `CodexTestRunner` and `DocsIndexSetup`. See `DREAMFERENCE_AGENTS.md`.

### 3.5. `chat/`

The web chat and its companions: Onyx deployment and configuration (retired once `ling web` matches it, MIGHTLING_ASK §10), the four kinds of UI patch (CSS, fonts, labels, scripts) plus brand assets and the per-install admin password, the Google service (Gmail, Drive and Calendar, also startable without Onyx by `ling-admin google`) and the Gmail client, the image-search sidecar, the SearXNG sidecar with the user-defined network the sidecars are created on, the Matrix homeserver behind `ling chat`, and the runner of the Electron desktop app. See `DREAMFERENCE_ONYX.md`, `docs/dev/onyx.md` and `docs/dev/onyx-ui-patches.md`.

### 3.6. `context_engine/`

AST symbol extraction, TF-IDF, SQLite FTS5 and embeddings stored as plain float32 blobs (sqlite-vec is not used), written to `.dreamference/`. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`

`MCPServer` (stdio JSON-RPC) with the tools from `MCPToolRegistry`:
- `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`;
- `web_search`, `web_fetch`;
- `workspace_search_code`, answered by `CodeIndexSearch` from `ling-code` when the workspace is indexed, and by the context engine otherwise.

`WebTools` is the MCP server's copy of what `ling-search` and `ling-fetch` do, and like them it follows the `/airgapped` level; those two are Rust (`ling-web-rs/`, `DREAMFERENCE_MIGHTLING_CODEX.md` §4.1), and the two implementations are kept in step by hand.

### 3.8. `cli/`

`DreamferenceCLIController.build_parser()` / `run_cli()`, `ModelDeepInspector`, the Sonnet dataset, `CodeIndexSetup` and `LegacyNameMigration` (which runs only from an installed copy with no source beside it, and never when `MIGHTLING_LEGACY_MIGRATION=0`). See `DREAMFERENCE_CLI.md`.

### 3.9. `night_shift/`

`NightShiftRunner.run()` is `ling-admin night run`: admission, then a scheduling loop that starts one `NightShiftTaskRun` per queued task, each in its own git worktree under a memory-capped systemd scope. `NightShiftQueue` reads and writes the task files the launcher (`ling-rs/src/night.rs`) creates, under the same per-task locks, and holds the runner lock, which records who holds it and which `ling-admin swe-bench` shares. `NightShiftIndex` refreshes each repository's code index before its tasks start, `NightShiftRemote` hands a task to another node's runner, and `RefinePrompt` holds refine mode's texts. See `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`.

### 3.10. `swe_bench/`

`SweBenchCommand.dispatch()` is `ling-admin swe-bench`. `SweBenchRunner` runs the agent phase with Night Shift's admission and runner lock: one `SweBenchInstanceRun` per instance, each a `ling exec` inside that instance's container on an internal Docker network that reaches only the model server, using the relocated `ling` that `SweBenchRuntime` builds. `SweBenchEvaluator` validates instances and grades predictions through the upstream harness (`SweBenchHarness`); `SweBenchReport` prints a run and compares two. Every docker command goes through `SweBenchDocker`. `SweBenchNameStripper` (`--strip-names`), `SweBenchPatchFilter` (`eval --drop-test-hunks`) and `SweBenchRelay` (a lane on another node) serve particular arms. See `DREAMFERENCE_MIGHTLING_SWE_BENCH.md`.

### 3.11. `audit/`

`EgressAudit.run()` is `ling-admin audit egress`: one real `ling exec` under `strace`, in a throwaway repository and `CODEX_HOME` (`--tui` drives a full-screen session through `TuiSession`; `--docs` runs `DocsEgressAudit`, the local file index's scenario). `StraceParser` turns the trace into an `EgressTrace`, and `EgressVerdict` passes it only if the session reached nothing but the model server and the other allowlisted loopback services. See `DREAMFERENCE_MIGHTLING_EGRESS.md`.

### 3.12. `node/`

`NodeAdvertiser` is `ling-admin node enable|disable|status`, and `NodeRemote`, `NodePairing` and `NodeServe` are `node list|add|remove|set|start|stop` (other nodes are listed from their open model port and changed only over an SSH pairing). `NodeAdvertiser` installs the Avahi service file `NodeServiceFile` renders, publishes the web UI and SearXNG beyond loopback, and records both switches in `NodeSettings`. `NodeIdentity` is the id clients remember a node by. `NodeJob`, `NodeJobSender`, `NodeModelSync` and `NodeLanes` are jobs, `node sync-model` and lanes (NODE §12–§13); `NodePrepare`, `NodeProvisioner` and the `Fleet*` classes are `node prepare` and `node provision` (MIGHTLING_FLEET). The client side is Rust: `ling-rs/src/node.rs` and the `ling-node-locator` crate, with a byte-identical copy of the locator in `ling-web-rs/` (a test compares them, as one does for the `ling-airgapped` copy in `ling-web-rs/`). The Tauri shell, which held a third copy, was replaced by the Electron app, which locates nothing itself: it runs the bundled `ling` (`ling app-server` for Work, `ling web` on port 3100 for Ask), whose launcher resolves the model server. See `DREAMFERENCE_MIGHTLING_NODE.md`.

---

## 4. Import Conventions

Import from the **subpackage** facade. The root package re-exports nothing, so `from dreamference import DreamferenceConfig` raises `ImportError`.

```python
from dreamference.config import DreamferenceConfig
from dreamference.context_engine import ContextEngine
from dreamference.hardware import HardwareManager, resolve_model_hf_repo
from dreamference.runner import CodexRunner, ClineRunner
from dreamference.vllm_server import VLLMServerManager
```

Direct module imports (`from dreamference.config.dreamference_config import DreamferenceConfig`) also work, and are what modules inside the package use for sibling classes, to avoid import cycles.

Each package `__init__.py` follows this pattern:

```python
# dreamference/config/__init__.py
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.config.config_path_resolver import ConfigPathResolver
# ...

__all__ = ["DreamferenceConfig", "ConfigPathResolver", ...]
```

---

## 5. Launch Command for the Main Model

### 5.1. `qwen3.8-27b-nvfp4-dflash2` (SGLang)

The output of `VLLMServerManager().build_launch_command("qwen3.8-27b-nvfp4-dflash2")` on this machine on 2026-10-01, with default config:

| Parameter / Flag | Value | Source |
| :--- | :--- | :--- |
| Docker image | `lmsysorg/sglang@sha256:d6e7288627be…` | `launch_overrides["docker_image"]`, pinned by digest |
| Container limits | `--cpus=14.0 --memory=85g --memory-swap=85g --oom-score-adj=800`, `--restart unless-stopped` | Derived from the host, `gpu_memory_utilization` 0.5 and the recipe's `container_headroom_gb` 24 |
| Container env | `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`, `HF_HUB_OFFLINE=1`, `TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor` | The launcher |
| Entry | `python3 -m sglang.launch_server` | `SGLangLaunchBuilder` |
| `--model-path` | the checkpoint's **snapshot directory** under the mounted HF cache, at the pinned revision | `hf_repo_id` and `revision` |
| `--served-model-name` | `RadixArk/Qwen3.8-27B-NVFP4` | `hf_repo_id` |
| `--context-length` | `262144` | Recipe `max_model_len` |
| `--mem-fraction-static` | `0.5` | Recipe `gpu_memory_utilization` |
| `--tool-call-parser` / `--reasoning-parser` | `qwen3_coder` / `qwen3` | Recipe |
| `--speculative-algorithm`, `--speculative-draft-model-path`, `--speculative-num-draft-tokens`, `--speculative-draft-model-quantization` | `DFLASH`, the drafter's snapshot directory, `16`, `modelopt_fp4` | Recipe `speculative_config` |
| `--chat-template` | a patched copy under `~/.cache/dreamference/chat-templates/` | `ChatTemplatePatcher`, from the recipe's `chat_template_patches` |
| `--attention-backend` / `--sampling-backend` | `flashinfer` / `pytorch` | Recipe `extra_args` |
| `--chunked-prefill-size`, `--max-running-requests`, `--cuda-graph-max-bs`, `--torch-compile-max-bs`, `--num-continuous-decode-steps` | `8192`, `8`, `8`, `4`, `2` | Recipe `extra_args` |
| `--mamba-radix-cache-strategy`, `--mamba-ssm-dtype`, `--max-mamba-cache-size` | `extra_buffer`, `bfloat16`, `96` | Recipe `extra_args` |
| `--trust-remote-code`, `--tp-size 1`, `--disable-prefill-cuda-graph`, `--disable-flashinfer-autotune`, `--enable-torch-compile`, `--sleep-on-idle`, `--enable-metrics` | flags | Builder and recipe `extra_args` |

### 5.2. The removed vLLM fallback

Until 2026-10-07 this section also listed the vLLM command of `qwen3.5-122b-a10b-hybrid-dflash` (image `dreamference-vllm-dflash:0.23.0-aeon-dense5`, 32K context, DFlash with 12 tokens). It was removed with the model (`DREAMFERENCE_MODELS.md` §1, `DREAMFERENCE_INFERENCE.md` §5.2); the flags vLLM always gets are in INFERENCE §4 and §6.

The recipe is data in `hardware/model_matrix_registry.py`. Change it there, not in the launch builder. Tests assert the layering.

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md):** system overview
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md):** context engine internals
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM and SGLang configuration and launch
