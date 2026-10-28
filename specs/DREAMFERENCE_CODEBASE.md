# Mightling Codebase Architecture & Reference

> **Version:** 1.7.2
> **Subject:** Source Code Layout, Module Organization, Package Structure
> **Checked against the code:** 2026-10-28 (module tree against the tracked source tree: every Python module under `dreamference/` is named below, and every Rust crate and the desktop projects by folder; §5 against `dreamference/hardware/model_matrix_registry.py`)

---

## Table of Contents

- [1. Package Overview](#1-package-overview)
- [2. Module Structure](#2-module-structure)
- [3. Subsystem Packages](#3-subsystem-packages)
- [4. Import Conventions](#4-import-conventions)
- [5. Launch Command for the Main Model](#5-launch-command-for-the-main-model)

---

## 1. Package Overview

**Version:** `dreamference.__version__ == "1.7.2"`.

**Architecture:** fourteen packages under `dreamference/` (thirteen subsystems and `cli/`). Each `__init__.py` is a re-export facade with an explicit `__all__`. Alongside them sit the Rust launcher `ling-rs/` (with its member crates: the web UI server, Signal and chat bridges, delegate and monitor MCP servers, sync, safegit, loop-guard, apps, skills and the leaf crates), the web commands `ling-web-rs/`, the code index `ling-code-rs/`, the document index `ling-docs-rs/`, the Codex fork `codex/` (submodule) with its patches `codex-patches/`, the desktop app `desktop/` (Electron shell `desktop/electron/` and the shared UI `desktop/ui/`), and the `ling-engine/` submodule (the Qwen3.8-27B model server's repository; `ling-serve` is served from a pinned container image).

**No shims:** the top-level `dreamference/<name>.py` modules (`cli.py`, `config.py`, `hardware.py`, …), each a `from dreamference.<name>.__init__ import *` that never executed because the same-named package resolves first, were deleted on 2026-10-20; `tests/test_code_quality.py` keeps them from coming back (PYTHON_QUALITY §11).

**Conventions:**
- one class per file, named after the class;
- stateless helpers as `@classmethod`s;
- `typing.Final` module constants;
- Google-style docstrings.

### 1.1. Subsystem Packages

| Package | Role |
| :--- | :--- |
| `dreamference/config/` | 4-tier config resolution, config generation, loopback proxy bypass |
| `dreamference/hardware/` | GB10 detection and telemetry, model matrix, HF and release-asset downloads, tensorization, LoRA adapter catalog/store/verification |
| `dreamference/vllm_server/` | Docker model-server lifecycle (SGLang, `ling-serve` and vLLM), launch arguments, chat-template and runtime patching, model gate, LoRA adapter serving, host-safety guards, diffusion sidecar |
| `dreamference/runner/` | Four agent installer/runner pairs, the readiness waiter, the `ling` builder, the Codex test runner, `ling-docs`'s model setup, `SafeGit` |
| `dreamference/chat/` | What is left of the retired Onyx web chat (`chat remove`/`status`), the Google service (Gmail, Drive, Calendar), image search, the SearXNG sidecar and the sidecar network, the Matrix homeserver, the desktop app's runner |
| `dreamference/context_engine/` | AST symbols, TF-IDF, FTS5 and dense retrieval |
| `dreamference/mcp_server/` | stdio MCP server for JetBrains / VS Code, web tools, and code search through `ling-code` |
| `dreamference/cli/` | `ling-admin`, adapter and SearXNG subcommands, deep model inspection, benchmark dataset, code-index tool setup, the Puffin → Mightling migration |
| `dreamference/night_shift/` | Night Shift: the overnight run of the `/night` queue, best-of-N and chooser, recurring tasks, autofill, archive, timer and report |
| `dreamference/swe_bench/` | `ling-admin swe-bench`: `ling` over SWE-bench instances, sharding/merging, image builder/cache, graded by the upstream harness |
| `dreamference/audit/` | `ling-admin audit egress`: a traced `ling` session (`exec`, `--tui`, `--app`, `--web`, `--compat`, `--docs`, `--sync`) and a verdict on where it connected |
| `dreamference/node/` | The node half of the client/server split: the advertised service, the node id, what is published to the LAN, managing other nodes over an SSH pairing, jobs, model and adapter sync, smart-plug power cycling, lanes, and provisioning more GB10s (`node prepare`, `node provision`) |
| `dreamference/forge/` | `ling-admin forge`: the node's Forgejo instance (forge of record, read-only mirrors, backups, GitHub mirror, push anonymity hook) |
| `dreamference/remote/` | `ling-admin remote`: off-LAN access over a NetBird overlay whose control plane and CA run on the node, with one relay box |

---

## 2. Module Structure

```
dreamference/
├── __init__.py                       # __version__ = "1.7.2" only (no re-exports)
├── web_canvas.py                     # CanvasHandler + start_web_canvas_server (ling-admin web)
│
├── cli/
│   ├── dreamference_cli_controller.py    # DreamferenceCLIController, main()
│   ├── adapter_command.py                # AdapterCommand (ling-admin adapter list|install|remove|verify)
│   ├── searxng_command.py                # SearxngCommand (ling-admin searxng start|status)
│   ├── model_deep_inspector.py           # ModelDeepInspector (main-model inspect --deep)
│   ├── sonnet_dataset.py                 # embedded Sonnet corpus for benchmark_server
│   ├── code_index_setup.py               # CodeIndexSetup, PinnedTool (ling-admin code setup)
│   └── legacy_name_migration.py          # LegacyNameMigration (ling-admin's half of the Puffin → Mightling move)
├── config/
│   ├── dreamference_config.py            # DreamferenceConfig: 4-tier resolution
│   ├── config_path_resolver.py           # ConfigPathResolver
│   ├── config_file_storage_manager.py    # ConfigFileStorageManager
│   ├── config_generator.py               # generate_default_init_config (functions, no class)
│   └── proxy_bypass.py                   # ProxyBypass (NO_PROXY / no_proxy for loopback and local hosts)
├── hardware/
│   ├── model_matrix_registry.py          # ModelMatrixRegistry (MATRIX), DEFAULT_MODEL_ALIAS
│   ├── model_spec.py                     # ModelSpec
│   ├── model_downloader.py               # ModelDownloader
│   ├── release_asset_downloader.py       # ReleaseAssetDownloader (release-hosted drafter and sparse head)
│   ├── adapter_catalog.py                # AdapterCatalog (catalogued LoRA adapters)
│   ├── adapter_installer.py              # AdapterInstaller (adapter install)
│   ├── adapter_manifest.py               # AdapterManifest (adapter.toml / manifest schema)
│   ├── adapter_signature.py              # AdapterSignature (Ed25519 signature check)
│   ├── adapter_store.py                  # AdapterStore (installed adapters on disk)
│   ├── adapter_verifier.py               # AdapterVerifier (adapter verify against the served base)
│   ├── adapter_base_digest.py            # AdapterBaseDigest (base checkpoint digest check)
│   ├── hardware_manager.py               # HardwareManager
│   ├── hardware_telemetry.py             # HardwareTelemetry
│   └── memory_metrics.py                 # MemoryMetrics
├── vllm_server/
│   ├── vllm_server_manager.py            # VLLMServerManager
│   ├── vllm_launch_options.py            # VLLMLaunchOptions
│   ├── sglang_launch_builder.py          # SGLangLaunchBuilder (engine: sglang, the main model)
│   ├── sglang_runtime_patcher.py         # SGLangRuntimePatcher (runtime_patches mounted into SGLang)
│   ├── ling_serve_launch_builder.py      # LingServeLaunchBuilder (engine: ling, ling-serve)
│   ├── chat_template_patcher.py          # ChatTemplatePatcher (chat_template_patches, on a copy)
│   ├── adapter_serving.py                # AdapterServing (LoRA flags and patches for SGLang)
│   ├── model_gate.py                     # ModelGate (loopback gate container in front of the engine)
│   ├── model_gate_service.py             # ModelGateService (runs inside the gate container)
│   ├── model_gate_guard.py               # ModelGateGuard (admission/pause state for SWE-bench)
│   ├── model_gate_adapters.py            # ModelGateAdapters (LRU adapter loading through the gate)
│   ├── tool_call_canary.py               # ToolCallCanary (/v1/responses streamed tool-call check at start)
│   ├── wifi_power_save.py                # WifiPowerSave (Wi-Fi power-save check in host check|setup)
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
│   ├── codex_branded_builder.py                 # CodexBrandedBuilder (builds ling, the web commands, ling-code, ling-docs)
│   ├── codex_test_runner.py                     # CodexTestRunner (ling-admin codex test)
│   ├── cline_runner.py / cline_installer.py
│   ├── continue_runner.py / continue_installer.py
│   ├── openhands_runner.py / openhands_installer.py
│   ├── docs_index_setup.py                      # DocsIndexSetup (ling-admin docs setup: what ling-docs loads)
│   ├── safe_git.py                              # SafeGit (host-side git with hooks, fsmonitor, proxy and ssh key command neutralised)
│   └── vllm_readiness_waiter.py                 # VLLMReadinessWaiter (wait_for_vllm)
├── chat/
│   ├── retired_web_chat.py                      # RetiredWebChat (ling-admin chat remove|status, the one-time offer)
│   ├── docker_bridge.py                         # DockerBridge (loopback → the Docker bridge, for containers)
│   ├── gmail_search_service.py                  # GmailSearchService (the Google service inside its container)
│   ├── google_workspace_reader.py               # GoogleWorkspaceReader (read-only Drive and Calendar, staged beside it)
│   ├── google_service.py                        # GoogleService (ling-admin google start|stop|status, without the web UI)
│   ├── gmail_credentials.py                     # GmailCredentials
│   ├── gmail_client.py                          # GmailClient (ling-admin gmail)
│   ├── matrix_homeserver.py                     # MatrixHomeserver (ling-admin matrix: tuwunel on an internal network)
│   ├── image_search_service.py                  # ImageSearchService, HardenedFetcher, ImageStore, SearxngClient, SiglipClient, VisionRanker, FetchRejected
│   ├── image_search_sidecar.py                  # ImageSearchSidecar (ling-admin images start|stop|status)
│   ├── image_search_mcp.py                      # ImageSearchMcp (ling-admin images mcp: the image_search tool)
│   ├── speech_sidecar.py                        # SpeechSidecar (ling-admin voice start|stop|status)
│   ├── searxng_sidecar.py                       # SearxngSidecar (ling-admin searxng start)
│   ├── searxng_settings.py                      # SearxngSettings (renders SearXNG's settings.yml)
│   ├── searxng_status.py                        # SearxngStatus (ling-admin searxng status)
│   ├── searxng_engine_event.py                  # SearxngEngineEvent (engine health events)
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
│   ├── night_shift_best_of_n.py          # NightShiftBestOfN (best-of-N attempts per queued task)
│   ├── night_shift_chooser.py            # NightShiftChooser (chooser v1/v2 over passing candidates)
│   ├── chooser_candidate.py              # ChooserCandidate (one attempt's diff and test metrics)
│   ├── night_shift_queue.py              # NightShiftQueue (the files under $CODEX_HOME/night, the runner lock)
│   ├── night_shift_recurring.py          # NightShiftRecurring (ling-admin night every: repeating tasks)
│   ├── night_shift_autofill.py           # NightShiftAutofill (ling-admin night autofill)
│   ├── autofill_candidate.py             # AutofillCandidate (one autofill candidate task)
│   ├── night_shift_archive.py            # NightShiftArchive (ling-admin night archive)
│   ├── archive_outcome.py                # ArchiveOutcome (summary returned by NightShiftArchive)
│   ├── night_shift_compaction_audit.py   # NightShiftCompactionAudit (compaction check on night runs)
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
│   ├── swe_bench_image_builder.py        # SweBenchImageBuilder (swe-bench build-images for datasets without arm64 images)
│   ├── swe_bench_image_cache.py          # SweBenchImageCache (chunked image pull/eviction)
│   ├── swe_bench_runtime.py              # SweBenchRuntime (the relocated ling that starts in an instance image)
│   ├── swe_bench_docker.py               # SweBenchDocker (the one place the benchmark runs docker)
│   ├── swe_bench_runner.py               # SweBenchRunner (swe-bench run: admission, scheduling, resume)
│   ├── swe_bench_instance_run.py         # SweBenchInstanceRun (one instance: container, ling exec, prediction)
│   ├── swe_bench_arm_smoke.py            # SweBenchArmSmoke (pre-run smoke check of an arm's flags)
│   ├── swe_bench_gate_hold.py            # SweBenchGateHold (holds the model gate during a benchmark run)
│   ├── swe_bench_hooks.py                # SweBenchHooks (hook sets for --hooks)
│   ├── swe_bench_issue_gate.py           # SweBenchIssueGate (issue target extraction and pre-check)
│   ├── swe_bench_issue_targets.py        # SweBenchIssueTargets (files/symbols named in the issue)
│   ├── swe_bench_difficulty.py           # SweBenchDifficulty (difficulty buckets for balanced sharding)
│   ├── swe_bench_shards.py               # SweBenchShards (swe-bench shard / run --shard)
│   ├── swe_bench_shard_merge.py          # SweBenchShardMerge (swe-bench merge across shard runs)
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
│   ├── compat_print_session.py           # CompatPrintSession (--compat claude: print-mode session)
│   ├── docs_egress_audit.py              # DocsEgressAudit (--docs: the local file index's scenario)
│   ├── sync_egress_audit.py              # SyncEgressAudit (--sync: one ling sync agent cycle)
│   ├── preview_page.py                   # PreviewPage (throwaway local page for --app / --web preview checks)
│   ├── strace_parser.py                  # StraceParser (reads the strace output)
│   ├── egress_trace.py                   # EgressTrace (destinations, DNS names, processes)
│   └── egress_verdict.py                 # EgressVerdict (pass, fail or trace failed, with reasons)
├── node/
│   ├── node_advertiser.py                # NodeAdvertiser (ling-admin node enable|disable|status)
│   ├── node_service_file.py              # NodeServiceFile (the Avahi service file, _mightling-node._tcp)
│   ├── node_identity.py                  # NodeIdentity (the node's stable id)
│   ├── node_settings.py                  # NodeSettings (~/.config/dreamference/node-advertise.json)
│   ├── node_browser.py                   # NodeBrowser (what a browse of the network returns)
│   ├── node_remote.py                    # NodeRemote (node list|set|start|stop: managing other nodes from this one)
│   ├── node_pairing.py                   # NodePairing (node add|remove: a key restricted to one forced command)
│   ├── node_serve.py                     # NodeServe (node serve-job: the operations a paired key may ask for)
│   ├── node_job.py                       # NodeJob (a job sent to this node, from record to result branch)
│   ├── node_job_sender.py                # NodeJobSender (node run|jobs|logs|cancel|fetch)
│   ├── node_model_sync.py                # NodeModelSync (node sync-model)
│   ├── node_adapter_sync.py              # NodeAdapterSync (syncs installed LoRA adapters to paired nodes)
│   ├── node_power_cycle.py               # NodePowerCycle (ling-admin node power-cycle)
│   ├── node_power_settings.py            # NodePowerSettings (ling-admin node power-config, node-power.json)
│   ├── node_shelly_plug.py               # NodeShellyPlug (Shelly gen 1/2 smart-plug HTTP client)
│   ├── node_lanes.py                     # NodeLanes (the model servers one run spreads its tasks over)
│   ├── node_prepare.py                   # NodePrepare (sudo ling-admin node prepare: the root steps of a node install)
│   ├── node_provisioner.py               # NodeProvisioner (ling-admin node provision)
│   ├── node_role_provisioner.py          # NodeRoleProvisioner (node provision --role night|engine)
│   ├── fleet_probe.py                    # FleetProbe (a machine's state before provisioning changes it)
│   ├── fleet_session.py                  # FleetSession (the provisioning SSH session to one machine)
│   ├── fleet_askpass.py                  # FleetAskpass (answers ssh's password prompt during provisioning)
│   ├── fleet_bundle.py                   # FleetBundle (the bundle install.sh --from installs)
│   ├── fleet_hub_fetch.py                # FleetHubFetch (node provision --model-source hf)
│   └── fleet_model_plan.py               # FleetModelPlan (what a node needs for its model)
├── forge/
│   ├── forge_command.py                  # ForgeCommand (ling-admin forge)
│   ├── forge_service.py                  # ForgeService (systemd user unit and timer for the node's Forgejo)
│   ├── forge_container.py                # ForgeContainer (the Forgejo Docker container)
│   ├── forge_settings.py                 # ForgeSettings (ports, paths, role: record or mirror)
│   ├── forge_app_ini.py                  # ForgeAppIni (renders Forgejo's app.ini)
│   ├── forge_accounts.py                 # ForgeAccounts (owner and agent accounts, SSH keys)
│   ├── forge_repositories.py             # ForgeRepositories (creating, importing and archiving repos)
│   ├── forge_anonymity.py                # ForgeAnonymity (pre-receive anonymity hook)
│   ├── forge_mirrors.py                  # ForgeMirrors (read-only mirrors on paired nodes)
│   ├── forge_github_mirror.py            # ForgeGithubMirror (forge github-mirror)
│   ├── forge_backup.py                   # ForgeBackup (forge backup|restore)
│   ├── forge_move.py                     # ForgeMove (forge move|promote)
│   ├── forge_bind.py                     # ForgeBind (LAN/loopback binding and Avahi advertisement)
│   ├── forge_phone.py                    # ForgePhone (phone access over Tailscale/remote)
│   ├── forge_serve.py                    # ForgeServe (node serve-job operations for the forge)
│   └── forge_storage.py                  # ForgeStorage (storage budget and checks)
└── remote/
    ├── remote_access.py                  # RemoteAccess (ling-admin remote setup|status|remove|code|peers|revoke|certificate)
    ├── remote_control_plane.py           # RemoteControlPlane (NetBird management/signal container on the node)
    ├── remote_box.py                     # RemoteBox (deploys the relay + haproxy box over SSH)
    ├── remote_tunnel.py                  # RemoteTunnel (the node's tunnel to the relay box)
    ├── remote_node_peer.py               # RemoteNodePeer (the node's own NetBird peer)
    ├── remote_enrolment.py               # RemoteEnrolment (LAN one-time code enrolment and peer bundles)
    ├── remote_bundle_options.py          # RemoteBundleOptions (options for remote code --bundle-file)
    ├── remote_join_script.py             # RemoteJoinScript (remote code --join one-liner)
    ├── remote_certificate_authority.py   # RemoteCertificateAuthority (the node's private CA)
    ├── remote_public_certificate.py      # RemotePublicCertificate (Let's Encrypt DNS-01 certificate for the control plane)
    ├── remote_relay_certificate.py       # RemoteRelayCertificate (Let's Encrypt certificate for the relay)
    └── remote_settings.py                # RemoteSettings (~/.config/dreamference/remote.json)

scripts/                                  # at the repository root, not inside the package
├── install_gb10.sh                       # full installation from a checkout
├── run_vllm_gb10.sh                      # thin wrapper over ling-admin server start
├── package_mightling.sh                  # the release packaging of the binaries
├── gen_admin_reference.py                # regenerates docs/admin.md from build_parser()
├── rename_mightling.py                   # the Puffin → Mightling rename check
├── context_budget_cache_probe.py, context_budget_replay.py   # CONTEXT_BUDGET measurements
├── swe_bench_compare.py                  # the published-results comparison (SWE_BENCH_COMPARISON)
├── swe_bench_fresh.py, swe_bench_night1.sh, swe_bench_night2.sh   # fresh validated tasks and the nightly quality A/Bs
├── build_ling_serve_image.sh, pin_ling_serve_image.py        # ling-serve container image build and digest pin
├── ask_evals/, industry_evals/, messenger_live/, qa/, macos/, adapters/, remote_acceptance/
└── (removed 2026-10-28: cave_mode_bench/ — the cave-mode benchmark and its level texts)

ling-rs/src/*.rs                              # the launcher compiled into ling: lib, help, home, app, update, usage,
                                              # adapter, night, code_index, docs_index, airgapped, node, node_command,
                                              # apps, audit, compaction, compat, compat_agents, compat_commands,
                                              # compat_hooks, compat_keys, compat_mcp, compat_memory,
                                              # compat_permissions, compat_print, compat_tui, delegate, exec_tool,
                                              # guard, images, ledger, loop_guard, mask, monitor, notice, plugins,
                                              # prompt, proxy, refine, release_signature, remote_join, rename,
                                              # sandbox_share, share_scope, skills, signal, chat, summary_cap, sync,
                                              # uninstall, userns, vision, web, windows_tls, winsta, worktree
                                              # (cave.rs removed 2026-10-28)
ling-rs/airgapped/                            # crate ling-airgapped: the levels and their resolution (std only)
ling-rs/node-locator/                         # crate ling-node-locator: where the node is (std only)
ling-rs/masking/                              # crate ling-masking: observation masking (patch 0021)
ling-rs/loop-guard/                           # crate ling-loop-guard: repetition loop detector
ling-rs/safegit/                              # crate ling-safegit: host-side git with repository hooks/config disabled
ling-rs/skills/                               # crate ling-skills: links, preflight and budget for other agents' skills
ling-rs/apps/                                 # crate ling-apps: /apps over MCP (Gmail, Drive, Calendar)
ling-rs/delegate/                             # crate ling-delegate: Claude plugin delegate MCP server and ledger
ling-rs/monitor/                              # crate ling-monitor: Claude plugin session monitor MCP server
ling-rs/sync/                                 # crate ling-sync: ling sync hub and agent
ling-rs/tools/                                # crate ling-tools
ling-rs/web/                                  # crate ling-web-server: ling web (UI server, Ask threads, bridge policy)
ling-rs/signal/                               # crate ling-signal: the Signal bridge (library and daemon binary)
ling-rs/chat/                                 # crate ling-chat: ling chat (Matrix and Telegram)
ling-rs/prompts/                              # data: the named system prompts (ask, high-swe, refine); ling-rs/cave/ removed 2026-10-28
ling-web-rs/src/{lib,search,fetch,read,html_text,airgapped,node_locator}.rs, src/bin/   # ling-search, ling-fetch
ling-code-rs/src/                             # ling-code, the code index (router, SCIP stores, submodules, session, MCP)
ling-docs-rs/src/                             # ling-docs, the local document index (collections, extraction, embeddings, MCP)
codex-patches/00NN-*.patch                    # patch series for the codex/ submodule (32 patches, 0001–0035; 0003–0004 and 0017 retired)
desktop/electron/src/                         # the Electron desktop app (main process, bridge, egress, Work)
desktop/ui/src/                               # the Mightling UI (Ask and Work), served by ling web and app://
```

---

## 3. Subsystem Packages

### 3.1. `config/`

`DreamferenceConfig.__init__` resolves every field: constructor argument, then `DREAMFERENCE_*` env var, then config file, then `DEFAULT_*`. It also validates the memory budget (`validate_model`), and resolves the tool-call parser. `ConfigPathResolver` finds the file: `--config`, `DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml|.json`, `~/.config/dreamference/config.toml`. `save_config()` writes only non-default values.

### 3.2. `hardware/`

`ModelMatrixRegistry.MATRIX` is the source of truth for models and their `launch_overrides` (and `engine_recipes`, naming opt-in alternative engines such as `ling` for `qwen3.8-27b-nvfp4-dflash2`), which is the per-model recipe, including an optional pinned `docker_image` and an optional `engine` (`sglang` for the default model, with `ling-serve` in `engine_recipes["ling"]`; vLLM otherwise). `ModelSpec` also records `supports_vision` and `is_diffusion`. `ModelDownloader` manages the HF cache (`$HF_HOME/hub`) and the tensorizer cache (`~/.cache/dreamference/tensorizer`), delegating release-hosted models (such as the searched-scale DFlash2 drafter and sparse head) to `ReleaseAssetDownloader`. `AdapterCatalog`, `AdapterInstaller`, `AdapterManifest`, `AdapterSignature`, `AdapterStore`, `AdapterVerifier` and `AdapterBaseDigest` manage LoRA adapters.

### 3.3. `vllm_server/`

- **`VLLMServerManager`:** builds the `docker run …` command (§5; for an `engine: sglang` recipe `SGLangLaunchBuilder` supplies what follows the image, `SGLangRuntimePatcher` mounts `runtime_patches`, `AdapterServing` adds LoRA flags and patches, and `ChatTemplatePatcher` writes the patched template; for `engine: ling`, `LingServeLaunchBuilder` builds the `ling-serve` launch), starts the loopback `ModelGate` (`ModelGateService`, `ModelGateGuard`, `ModelGateAdapters`) in front of the engine unless `--no-gate`, runs the host-safety pre-flight (`check_host_safety`), starts under `MemoryPressureWatchdog`, runs `ToolCallCanary`, stops, removes, tails logs, and resets a stale torch.compile cache (`_reset_stale_compile_cache`).
- **`DiffusionServerManager`:** runs the diffusion sidecar (`diffusion_openai_service.py`) in the main model's image before vLLM starts, capped at 8 GB. Switched off since 2026-10-03 (`ModelMatrixRegistry.diffusion_enabled()` is false): `server start` only calls `remove_leftover()` on a sidecar an older Mightling left behind.

### 3.4. `runner/`

Four pairs: Codex (default), Cline, Continue and OpenHands, plus `VLLMReadinessWaiter` (the non-Codex runners' wait for the server), `CodexBrandedBuilder`, `CodexTestRunner`, `DocsIndexSetup` and `SafeGit`. See `DREAMFERENCE_AGENTS.md`.

### 3.5. `chat/`

The sidecars and the desktop app: the Google service (Gmail, Drive and Calendar, `ling-admin google`) and the Gmail client, image search (`image_search_sidecar.py`, the service, and the `image_search` tool's MCP server `image_search_mcp.py`), speech-to-text (`speech_sidecar.py`), the SearXNG sidecar (`searxng_sidecar.py`, `searxng_settings.py`, `searxng_status.py`, `searxng_engine_event.py`) with the user-defined network the sidecars are created on, the Docker bridge rewrite (`docker_bridge.py`), the Matrix homeserver behind `ling chat`, the runner of the Electron desktop app, and `retired_web_chat.py`, which removes what the retired Onyx web chat left (MIGHTLING_ASK §10, §19; `DREAMFERENCE_ONYX.md` is its history).

### 3.6. `context_engine/`

AST symbol extraction, TF-IDF, SQLite FTS5 and embeddings stored as plain float32 blobs (sqlite-vec is not used), written to `.dreamference/`. See `DREAMFERENCE_CONTEXT.md`.

### 3.7. `mcp_server/`

`MCPServer` (stdio JSON-RPC) with the tools from `MCPToolRegistry`:
- `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`;
- `web_search`, `web_fetch`;
- `workspace_search_code`, answered by `CodeIndexSearch` from `ling-code` when the workspace is indexed, and by the context engine otherwise.

`WebTools` is the MCP server's copy of what `ling-search` and `ling-fetch` do, and like them it follows the `/airgapped` level; those two are Rust (`ling-web-rs/`, `DREAMFERENCE_MIGHTLING_CODEX.md` §4.1), and the two implementations are kept in step by hand.

### 3.8. `cli/`

`DreamferenceCLIController.build_parser()` / `run_cli()`, `AdapterCommand`, `SearxngCommand`, `ModelDeepInspector`, the Sonnet dataset, `CodeIndexSetup` and `LegacyNameMigration` (which runs only from an installed copy with no source beside it, and never when `MIGHTLING_LEGACY_MIGRATION=0`). See `DREAMFERENCE_CLI.md`.

### 3.9. `night_shift/`

`NightShiftRunner.run()` is `ling-admin night run`: admission, then a scheduling loop that starts one `NightShiftTaskRun` (or `NightShiftBestOfN` with `NightShiftChooser`) per queued task, each in its own git worktree under a memory-capped systemd scope. `NightShiftQueue` reads and writes the task files the launcher (`ling-rs/src/night.rs`) creates, under the same per-task locks, and holds the runner lock, which records who holds it and which `ling-admin swe-bench` shares. `NightShiftRecurring` manages `night every` repeating tasks, `NightShiftAutofill` queues bounded tasks from QA/CI/specs, `NightShiftArchive` moves old run records to `night/archive/`, `NightShiftIndex` refreshes each repository's code index before its tasks start, `NightShiftRemote` hands a task to another node's runner, and `RefinePrompt` holds refine mode's texts. See `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`.

### 3.10. `swe_bench/`

`SweBenchCommand.dispatch()` is `ling-admin swe-bench`. `SweBenchRunner` runs the agent phase with Night Shift's admission and runner lock (and `SweBenchGateHold` on the model gate): one `SweBenchInstanceRun` per instance, each a `ling exec` inside that instance's container on an internal Docker network that reaches only the model server, using the relocated `ling` that `SweBenchRuntime` builds. `SweBenchEvaluator` validates instances and grades predictions through the upstream harness (`SweBenchHarness`); `SweBenchReport` prints a run and compares two; `SweBenchShards` and `SweBenchShardMerge` split a list across machines and merge the shard runs. Every docker command goes through `SweBenchDocker`. `SweBenchNameStripper` (`--strip-names`), `SweBenchPatchFilter` (`eval --drop-test-hunks`), `SweBenchHooks` (`--hooks`), `SweBenchIssueGate` and `SweBenchRelay` (a lane on another node) serve particular arms. See `DREAMFERENCE_MIGHTLING_SWE_BENCH.md`.

### 3.11. `audit/`

`EgressAudit.run()` is `ling-admin audit egress`: one real `ling exec` under `strace`, in a throwaway repository and `CODEX_HOME` (`--tui` drives a full-screen session through `TuiSession`; `--compat claude` runs `CompatPrintSession`; `--docs` runs `DocsEgressAudit`, the local file index's scenario; `--sync` runs `SyncEgressAudit`). `StraceParser` turns the trace into an `EgressTrace`, and `EgressVerdict` passes it only if the session reached nothing but the model server and the other allowlisted loopback services. See `DREAMFERENCE_MIGHTLING_EGRESS.md`.

### 3.12. `node/`

`NodeAdvertiser` is `ling-admin node enable|disable|status`, and `NodeRemote`, `NodePairing` and `NodeServe` are `node list|add|remove|set|start|stop` (other nodes are listed from their open model port and changed only over an SSH pairing). `NodeAdvertiser` installs the Avahi service file `NodeServiceFile` renders, publishes the web UI and SearXNG beyond loopback, and records both switches in `NodeSettings`. `NodeIdentity` is the id clients remember a node by. `NodeJob`, `NodeJobSender`, `NodeModelSync`, `NodeAdapterSync` and `NodeLanes` are jobs, `node sync-model`, adapter sync and lanes (NODE §12–§13); `NodePowerCycle`, `NodePowerSettings` and `NodeShellyPlug` are `node power-cycle` and `node power-config`; `NodePrepare`, `NodeProvisioner`, `NodeRoleProvisioner` and the `Fleet*` classes are `node prepare` and `node provision` (MIGHTLING_FLEET). The client side is Rust: `ling-rs/src/node.rs` and the `ling-node-locator` crate, with a byte-identical copy of the locator in `ling-web-rs/` (a test compares them, as one does for the `ling-airgapped` copy in `ling-web-rs/`). The Tauri shell, which held a third copy, was replaced by the Electron app, which locates nothing itself: it runs the bundled `ling` (`ling app-server` for Work, `ling web` on port 3100 for Ask), whose launcher resolves the model server. See `DREAMFERENCE_MIGHTLING_NODE.md`.

### 3.13. `forge/`

`ForgeCommand` is `ling-admin forge`: a Forgejo container (`ForgeContainer`, `ForgeService`, `ForgeAppIni`, `ForgeSettings`) on every node, where the first node is the forge of record and paired nodes are read-only mirrors (`ForgeMirrors`, `ForgeMove`), with repository and account management (`ForgeRepositories`, `ForgeAccounts`), a pre-receive anonymity hook (`ForgeAnonymity`), backups (`ForgeBackup`) and optional GitHub mirroring (`ForgeGithubMirror`). See `DREAMFERENCE_MIGHTLING_FORGEJO.md`.

### 3.14. `remote/`

`RemoteAccess` is `ling-admin remote`: off-LAN access through a NetBird overlay whose control plane (`RemoteControlPlane`) and private CA (`RemoteCertificateAuthority`) run on the node, an optional Let's Encrypt DNS-01 certificate (`RemotePublicCertificate`, `RemoteRelayCertificate`), one relay box configured over SSH (`RemoteBox`, `RemoteTunnel`), and LAN one-time code or bundle enrolment (`RemoteEnrolment`, `RemoteBundleOptions`, `RemoteJoinScript`). See `DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md`.

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

The output of `VLLMServerManager().build_launch_command("qwen3.8-27b-nvfp4-dflash2")` on this machine on 2026-10-01, with default config (updated for the current recipe in `hardware/model_matrix_registry.py`; `ling-serve` is opt-in beside it via `server start --engine ling` and `LingServeLaunchBuilder`):

| Parameter / Flag | Value | Source |
| :--- | :--- | :--- |
| Docker image | `lmsysorg/sglang@sha256:b1259f3ea327…` (v0.5.21) | `launch_overrides["docker_image"]`, pinned by digest |
| Container limits | `--cpus=14.0 --memory=85g --memory-swap=85g --oom-score-adj=800`, `--restart unless-stopped` | Derived from the host, `gpu_memory_utilization` 0.5 and the recipe's `container_headroom_gb` 24 |
| Container env | `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`, `HF_HUB_OFFLINE=1`, `TORCHINDUCTOR_CACHE_DIR=/root/.cache/dreamference/sglang/inductor` | The launcher |
| Runtime patches | patched copy of `serving_responses.py` (`SGLANG_PHASE_MERGE_PATCH`, `SGLANG_STREAM_PHASE_PATCH`) mounted read-only | `SGLangRuntimePatcher`, from `launch_overrides["runtime_patches"]` |
| Entry | `python3 -m sglang.launch_server` | `SGLangLaunchBuilder` |
| `--model-path` | the checkpoint's **snapshot directory** under the mounted HF cache, at the pinned revision | `hf_repo_id` and `revision` |
| `--served-model-name` | `RadixArk/Qwen3.8-27B-NVFP4` | `hf_repo_id` |
| `--context-length` | `262144` | Recipe `max_model_len` |
| `--mem-fraction-static` | `0.5` | Recipe `gpu_memory_utilization` |
| `--tool-call-parser` / `--reasoning-parser` | `qwen3_coder` / `qwen3` | Recipe |
| `--speculative-algorithm`, `--speculative-draft-model-path`, `--speculative-num-draft-tokens`, `--speculative-draft-model-quantization` | `DFLASH`, the drafter's snapshot directory (`dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch` since 1.7.2), `12` (`16` on 2026-10-01; `12` since 2026-10-09), `modelopt_fp4` | Recipe `speculative_config` |
| `--chat-template` | a patched copy under `~/.cache/dreamference/chat-templates/` | `ChatTemplatePatcher`, from the recipe's `chat_template_patches` |
| `--attention-backend` / `--sampling-backend` | `flashinfer` / `pytorch` | Recipe `extra_args` |
| `--chunked-prefill-size`, `--max-running-requests`, `--cuda-graph-max-bs-decode`, `--torch-compile-max-bs`, `--num-continuous-decode-steps` | `8192`, `8`, `8`, `4`, `2` | Recipe `extra_args` |
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
