# Mightling Codebase Architecture & Reference

> **Version:** 1.2.0
> **Subject:** Source Code Layout, Module Organization, Package Structure
> **Checked against the code:** 2026-10-02 (module tree against the tracked source tree, by script: every Python module under `dreamference/` and every Rust source file of the launcher, the web commands and the desktop shell is named below; §5 against `build_launch_command()` output on 2026-10-01)

---

## Table of Contents

- [1. Package Overview](#1-package-overview)
- [2. Module Structure](#2-module-structure)
- [3. Subsystem Packages](#3-subsystem-packages)
- [4. Import Conventions](#4-import-conventions)
- [5. Launch Commands for the Default and Fallback Models](#5-launch-commands-for-the-default-and-fallback-models)

---

## 1. Package Overview

**Version:** `dreamference.__version__ == "1.2.0"`.

**Architecture:** twelve subsystem packages under `dreamference/`. Each `__init__.py` is a re-export facade with an explicit `__all__`. Alongside them sit the Rust launcher `mling-rs/`, the web commands `mling-web-rs/`, the code index `mling-code-rs/`, the Codex fork `codex/` with its patches `codex-patches/`, and the Tauri project `desktop/`.

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
| `dreamference/runner/` | Four agent installer/runner pairs, the readiness waiter, the `mling` builder, the Codex test runner |
| `dreamference/chat/` | Onyx Lite (Mightling web UI) lifecycle and patches, Gmail, image search, the SearXNG sidecar and the sidecar network, desktop window |
| `dreamference/context_engine/` | AST symbols, TF-IDF, FTS5 and dense retrieval |
| `dreamference/mcp_server/` | stdio MCP server for JetBrains / VS Code, web tools, and code search through `mling-code` |
| `dreamference/cli/` | `mling-admin`, deep model inspection, benchmark dataset, code-index tool setup |
| `dreamference/night_shift/` | Night Shift: the overnight run of the `/night` queue, its timer and report |
| `dreamference/swe_bench/` | `mling-admin swe-bench`: `mling` over SWE-bench instances, graded by the upstream harness |
| `dreamference/audit/` | `mling-admin audit egress`: a traced `mling` session and a verdict on where it connected |
| `dreamference/node/` | The node half of the client/server split: the advertised service, the node id, what is published to the LAN, and managing other nodes over an SSH pairing |

---

## 2. Module Structure

```
dreamference/
├── __init__.py                       # __version__ = "1.2.0" only (no re-exports)
├── web_canvas.py                     # CanvasHandler + start_web_canvas_server (mling-admin web)
├── {cli,config,context_engine,hardware,mcp_server,runner,vllm_server}.py   # dead shims (see §1)
│
├── cli/
│   ├── dreamference_cli_controller.py    # DreamferenceCLIController, main()
│   ├── model_deep_inspector.py           # ModelDeepInspector (main-model inspect --deep)
│   ├── sonnet_dataset.py                 # embedded Sonnet corpus for benchmark_server
│   └── code_index_setup.py               # CodeIndexSetup, PinnedTool (mling-admin code setup)
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
│   ├── sglang_launch_builder.py          # SGLangLaunchBuilder (engine: sglang, the default model)
│   ├── chat_template_patcher.py          # ChatTemplatePatcher (chat_template_patches, on a copy)
│   ├── vllm_log_streamer.py              # VLLMLogStreamer
│   ├── vllm_server_status.py             # VLLMServerStatus
│   ├── vllm_startup_monitor.py           # VLLMStartupMonitor
│   ├── model_loading_monitor.py          # ModelLoadingMonitor
│   ├── psi_watchdog.py                   # MemoryPressureWatchdog
│   ├── diagnostics.py                    # ContainerDiagnostics
│   ├── diffusion_server_manager.py       # DiffusionServerManager
│   └── diffusion_openai_service.py       # DiffusionModelRunner (runs inside the sidecar container)
├── runner/
│   ├── codex_runner.py / codex_installer.py    # CodexRunner / CodexInstaller (default agent)
│   ├── codex_branded_builder.py                 # CodexBrandedBuilder (builds mling, the web commands, mling-code)
│   ├── codex_test_runner.py                     # CodexTestRunner (mling-admin codex test)
│   ├── cline_runner.py / cline_installer.py
│   ├── continue_runner.py / continue_installer.py
│   ├── openhands_runner.py / openhands_installer.py
│   └── vllm_readiness_waiter.py                 # VLLMReadinessWaiter (wait_for_vllm)
├── chat/
│   ├── onyx_runner.py / onyx_installer.py       # OnyxRunner / OnyxInstaller
│   ├── onyx_ui_overrides.py                     # OnyxUIOverrides (appended CSS)
│   ├── onyx_ui_fonts.py                         # OnyxUIFonts (@font-face swap)
│   ├── onyx_ui_labels.py                        # OnyxUILabels (string rewrites in JS)
│   ├── onyx_ui_scripts.py                       # OnyxUIScripts (injected behaviour)
│   ├── onyx_brand_assets.py                     # OnyxBrandAssets (logos, favicon, app icon)
│   ├── gmail_search_service.py                  # GmailSearchService (container service)
│   ├── gmail_credentials.py                     # GmailCredentials
│   ├── gmail_client.py                          # GmailClient (mling-admin gmail)
│   ├── image_search_service.py                  # ImageSearchService, HardenedFetcher, ImageStore, SearxngClient, SiglipClient, VisionRanker, FetchRejected
│   ├── searxng_sidecar.py                       # SearxngSidecar (mling-admin searxng start)
│   ├── sidecar_network.py                       # SidecarNetwork (the user-defined network sidecars are created on)
│   └── desktop_runner.py / desktop_installer.py # DesktopRunner / DesktopInstaller (mling-app)
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
│   ├── code_index_search.py              # CodeIndexSearch (workspace_search_code through mling-code)
│   ├── ide_state.py                      # IDEState
│   └── editor_selection.py               # EditorSelection
├── night_shift/
│   ├── night_shift_runner.py             # NightShiftRunner (mling-admin night run: admission, scheduling)
│   ├── night_shift_task_run.py           # NightShiftTaskRun (one task: worktree, mling exec, tests, commit)
│   ├── night_shift_queue.py              # NightShiftQueue (the files under $CODEX_HOME/night, the runner lock)
│   ├── night_shift_host.py               # NightShiftHost (read-only probes of the server and the host)
│   ├── night_shift_index.py              # NightShiftIndex (refreshes a repository's code index before its tasks)
│   ├── night_shift_settings.py           # NightShiftSettings (the [night] table)
│   ├── night_shift_report.py             # NightShiftReport (the morning report)
│   └── night_shift_scheduler.py          # NightShiftScheduler (the systemd user timer)
├── swe_bench/
│   ├── swe_bench_command.py              # SweBenchCommand (mling-admin swe-bench: parser and dispatch)
│   ├── swe_bench_settings.py             # SweBenchSettings (the [swe_bench] table, paths, pins)
│   ├── swe_bench_harness.py              # SweBenchHarness (the upstream harness in its own virtualenv)
│   ├── swe_bench_images.py               # SweBenchImages (arm64 instance images, the validated list)
│   ├── swe_bench_runtime.py              # SweBenchRuntime (the relocated mling that starts in an instance image)
│   ├── swe_bench_docker.py               # SweBenchDocker (the one place the benchmark runs docker)
│   ├── swe_bench_runner.py               # SweBenchRunner (swe-bench run: admission, scheduling, resume)
│   ├── swe_bench_instance_run.py         # SweBenchInstanceRun (one instance: container, mling exec, prediction)
│   ├── swe_bench_code_index.py           # SweBenchCodeIndex (--code-index universal: index on the host, mount read-only)
│   ├── swe_bench_evaluator.py            # SweBenchEvaluator (validation and grading through the harness)
│   ├── swe_bench_run_store.py            # SweBenchRunStore (one run's files)
│   └── swe_bench_report.py               # SweBenchReport (a run's report, two runs compared)
├── audit/
│   ├── egress_audit.py                   # EgressAudit (mling-admin audit egress: the traced session)
│   ├── strace_parser.py                  # StraceParser (reads the strace output)
│   ├── egress_trace.py                   # EgressTrace (destinations, DNS names, processes)
│   └── egress_verdict.py                 # EgressVerdict (pass, fail or trace failed, with reasons)
└── node/
    ├── node_advertiser.py                # NodeAdvertiser (mling-admin node enable|disable|status)
    ├── node_service_file.py              # NodeServiceFile (the Avahi service file, _mightling-node._tcp)
    ├── node_identity.py                  # NodeIdentity (the node's stable id)
    ├── node_settings.py                  # NodeSettings (~/.config/dreamference/node-advertise.json)
    ├── node_browser.py                   # NodeBrowser (what a browse of the network returns)
    ├── node_remote.py                    # NodeRemote (node list|set|start|stop: managing other nodes from this one)
    ├── node_pairing.py                   # NodePairing (node add|remove: a key restricted to one forced command)
    └── node_serve.py                     # NodeServe (node serve-job: the operations a paired key may ask for)

scripts/                                  # at the repository root, not inside the package
├── install_gb10.sh                       # full installation
├── run_vllm_gb10.sh                      # thin wrapper over mling-admin server start
├── gen_admin_reference.py                # regenerates docs/admin.md from build_parser()
└── cave_mode_bench/                      # the cave-mode benchmark and its level texts

mling-rs/src/{lib,help,home,app,update,usage,cave,night,code_index,airgapped,node}.rs   # launcher compiled into mling
mling-rs/airgapped/src/lib.rs                  # crate mling-airgapped: the three levels and their resolution (std only)
mling-rs/node-locator/src/lib.rs               # crate mightling-node-locator: where the node is (std only)
mling-web-rs/src/{lib,search,fetch,html_text,airgapped,node_locator}.rs, src/bin/   # mling-search, mling-fetch
mling-code-rs/src/                             # mling-code, the code index (router, SCIP stores, submodules, session, MCP)
codex-patches/00NN-*.patch                      # patch series for the codex/ submodule (17 patches, 0001–0019)
desktop/src-tauri/src/{main,discover,forwarder,node_locator}.rs   # Tauri shell (binary mling-app)
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

Four pairs: Codex (default), Cline, Continue and OpenHands, plus `VLLMReadinessWaiter` (the non-Codex runners' wait for the server), `CodexBrandedBuilder` and `CodexTestRunner`. See `DREAMFERENCE_AGENTS.md`.

### 3.5. `chat/`

The Mightling web UI and its companions: Onyx deployment and configuration, the four kinds of UI patch (CSS, fonts, labels, scripts) plus brand assets, the Gmail service and client, the image-search sidecar, the SearXNG sidecar with the user-defined network the sidecars are created on, and the Tauri desktop window. See `DREAMFERENCE_ONYX.md` and `CLAUDE.md`.

### 3.6. `context_engine/`

AST symbol extraction, TF-IDF, SQLite FTS5 and embeddings stored as plain float32 blobs (sqlite-vec is not used), written to `.dreamference/`. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`

`MCPServer` (stdio JSON-RPC) with the tools from `MCPToolRegistry`:
- `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`;
- `web_search`, `web_fetch`;
- `workspace_search_code`, answered by `CodeIndexSearch` from `mling-code` when the workspace is indexed, and by the context engine otherwise.

`WebTools` is the MCP server's copy of what `mling-search` and `mling-fetch` do, and like them it follows the `/airgapped` level; those two are Rust (`mling-web-rs/`, `DREAMFERENCE_MIGHTLING_CODEX.md` §4.1), and the two implementations are kept in step by hand.

### 3.8. `cli/`

`DreamferenceCLIController.build_parser()` / `run_cli()`, `ModelDeepInspector`, the Sonnet dataset and `CodeIndexSetup`. See `DREAMFERENCE_CLI.md`.

### 3.9. `night_shift/`

`NightShiftRunner.run()` is `mling-admin night run`: admission, then a scheduling loop that starts one `NightShiftTaskRun` per queued task, each in its own git worktree under a memory-capped systemd scope. `NightShiftQueue` reads and writes the task files the launcher (`mling-rs/src/night.rs`) creates, under the same per-task locks, and holds the runner lock, which records who holds it and which `mling-admin swe-bench` shares. `NightShiftIndex` refreshes each repository's code index before its tasks start. See `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`.

### 3.10. `swe_bench/`

`SweBenchCommand.dispatch()` is `mling-admin swe-bench`. `SweBenchRunner` runs the agent phase with Night Shift's admission and runner lock: one `SweBenchInstanceRun` per instance, each a `mling exec` inside that instance's container on an internal Docker network that reaches only the model server, using the relocated `mling` that `SweBenchRuntime` builds. `SweBenchEvaluator` validates instances and grades predictions through the upstream harness (`SweBenchHarness`); `SweBenchReport` prints a run and compares two. Every docker command goes through `SweBenchDocker`. See `DREAMFERENCE_MIGHTLING_SWE_BENCH.md`.

### 3.11. `audit/`

`EgressAudit.run()` is `mling-admin audit egress`: one real `mling exec` under `strace`, in a throwaway repository and `CODEX_HOME`. `StraceParser` turns the trace into an `EgressTrace`, and `EgressVerdict` passes it only if the session reached nothing but the model server and the other allowlisted loopback services. See `DREAMFERENCE_MIGHTLING_EGRESS.md`.

### 3.12. `node/`

`NodeAdvertiser` is `mling-admin node enable|disable|status`, and `NodeRemote`, `NodePairing` and `NodeServe` are `node list|add|remove|set|start|stop` (other nodes are listed from their open model port and changed only over an SSH pairing). `NodeAdvertiser` installs the Avahi service file `NodeServiceFile` renders, publishes the web UI and SearXNG beyond loopback, and records both switches in `NodeSettings`. `NodeIdentity` is the id clients remember a node by. The client side is Rust: `mling-rs/src/node.rs` and the `mightling-node-locator` crate, with byte-identical copies of the locator in `mling-web-rs/` and `desktop/src-tauri/` (a test compares them, as one does for the `mling-airgapped` copy in `mling-web-rs/`). See `DREAMFERENCE_MIGHTLING_NODE.md`.

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

## 5. Launch Commands for the Default and Fallback Models

### 5.1. The default: `qwen3.8-27b-nvfp4-dflash2` (SGLang)

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

### 5.2. The fallback: `qwen3.5-122b-a10b-hybrid-dflash` (vLLM)

The output of `VLLMServerManager().build_launch_command("qwen3.5-122b-a10b-hybrid-dflash")` on this machine, with default config (first recorded 2026-09-28, re-run 2026-10-01):

| Parameter / Flag | Value | Source |
| :--- | :--- | :--- |
| Docker image | `dreamference-vllm-dflash:0.23.0-aeon-dense5` | `launch_overrides["docker_image"]`; the project default `DEFAULT_VLLM_IMAGE` is `dreamference-vllm-tensorizer:26.07-py3` |
| Container limits | `--cpus=14.0 --memory=93g --memory-swap=93g --oom-score-adj=800`, `--restart unless-stopped` | Derived from the host and `gpu_memory_utilization` |
| Container env | `VLLM_MARLIN_USE_ATOMIC_ADD=1`, `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0` (recipe); `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`, `VLLM_CACHE_ROOT`, `CUTE_DSL_ARCH=sm_121a`, `VLLM_LOGGING_LEVEL=DEBUG`, request/response debug logging (always) | Recipe `env` plus the launcher |
| Model | `Intel/Qwen3.5-122B-A10B-int4-AutoRound` | `hf_repo_id` |
| `--max-model-len` | `32768` | Recipe |
| `--gpu-memory-utilization` | `0.7` | Recipe |
| `--max-num-batched-tokens` | `9048` | Recipe |
| `--attention-backend` | `flash_attn` | Recipe |
| `--kv-cache-dtype` | `auto` | Recipe; the config leaves it unset |
| `--tool-call-parser` / `--reasoning-parser` | `qwen3_xml` / `qwen3` | Recipe |
| `--speculative-config` | `{"method": "dflash", "model": "z-lab/Qwen3.5-122B-A10B-DFlash", "num_speculative_tokens": 12, "attention_backend": "FLASH_ATTN"}` | Recipe |
| `--structured-outputs-config.backend` | `xgrammar` | `DEFAULT_GUIDED_DECODING_BACKEND` |
| `--override-generation-config` | `{"temperature": 0.0, "top_p": 1.0, "top_k": 0}` | `DEFAULT_GENERATION_OVERRIDES` in the launcher; a recipe may override or disable it via `generation_overrides` |
| `--default-chat-template-kwargs` | `{"enable_thinking": false}` | Recipe `extra_args` |
| `--max-num-seqs` / `--tensor-parallel-size` / `--dtype` | `8` / `1` / `auto` | Recipe `extra_args` |
| `--enable-prefix-caching`, `--enable-chunked-prefill` | flags | Config defaults |
| `--async-scheduling`, `--trust-remote-code`, `--enable-log-requests`, `--enable-log-outputs`, `--max-log-len 2048`, `--enable-auto-tool-choice` | flags | Launcher |

The recipe is data in `hardware/model_matrix_registry.py`. Change it there, not in the launch builder. Tests assert the layering.

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md):** system overview
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md):** context engine internals
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM and SGLang configuration and launch
