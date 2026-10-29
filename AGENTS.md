# AGENTS.md

The project guide for people and coding agents working in this repository: what every task needs. The long architecture notes and the lessons behind them are in `docs/dev/` (listed at the end); each section below names its file.

Keep this file under ~24 KB: `ling` reads only the first 32 KB of `AGENTS.md` (`project_doc_max_bytes`). New detail goes into a `docs/dev/` file, with at most a line here.

## You have web access

```bash
ling-search "your query here"              # search; -n N for more results (default 5)
ling-fetch "https://example.com"           # fetch a page as readable text
```

Prefer them to `curl` or `wget` for reading the web; sandboxed commands have the network unless `/airgapped on`. There is no web search *tool*; search is a shell command. The full instructions are appended to the system prompt by the `ling` launcher (`WEB_ACCESS_INSTRUCTIONS` in `ling-rs/src/lib.rs`), so they apply in every workspace; this section is a pointer, not a copy.

## Project

**Mightling** (by Dreamference, the company; the Python package keeps the name `dreamference`) is a local, air-gapped agentic pair programmer targeting single-node **NVIDIA GB10 (Blackwell, SM121)** systems with 128 GB unified LPDDR5X memory. Its agent is `ling`, a Mightling-branded build of Codex, served by a locally-launched model server running in Docker; Cline, Continue and OpenHands can use the same server. Around it: `ling-admin` (Python, this package), a web UI (`ling web`), a desktop app (Electron), a code index, a local file index, Night Shift and a SWE-bench harness.

**The product was called Puffin until 1.5** (renamed 2026-10-07; spec `specs/DREAMFERENCE_RENAME_MIGHTLING.md`). The commands are `ling` and `ling-admin` and the agent's home is `~/.mightling`; the old names are **not** kept as aliases. What still carries the old name on purpose (the `puffin-*` release assets, the one-time migration, a few internal names such as the build cache `~/.cache/dreamference/puffin-codex`): [docs/dev/install-modes.md](docs/dev/install-modes.md#old-release-names).

## Commands

The venv is at `.venv/`. Use `.venv/bin/python` explicitly; there is no activation step assumed.

```bash
# Install (editable)
.venv/bin/pip install -e .

# Full test suite (~2 min, no GPU, Docker or model server: every hardware, subprocess and server call is mocked)
.venv/bin/python -m pytest tests/ -q

# Single file / single test
.venv/bin/python -m pytest tests/test_vllm_server.py -q
.venv/bin/python -m pytest tests/test_vllm_server.py::test_nvfp4_model_applies_registry_launch_recipe -q

# Build and install ling, ling-search/ling-fetch, ling-code and ling-docs (stamped; --force rebuilds)
ling-admin codex build

# Docs (mkdocs-material; CI publishes to GitHub Pages on push to main)
mkdocs serve
```

Rust and TypeScript tests, each in its own place:

- `ling-code-rs/`, `ling-docs-rs/`: `cargo test --locked` (own lockfiles).
- The messenger bridges: `cargo test --release -p ling-signal -p ling-chat` in the same export as the launcher.
- The launcher: `cargo test --release -p ling-launcher` in the build's *export* directory under `~/.cache/dreamference/puffin-codex/`, **never in `codex/`**. `ling-rs/skills/`: `cargo test` in a copy of the folder.
- Compile cache: `codex build` uses sccache when installed; by hand, see [codex-build.md](docs/dev/codex-build.md#the-compile-cache).
- `desktop/electron` and `desktop/ui`: `npm test`; `npm run e2e` in `desktop/electron` needs a display and `npm run package` first.
- Codex's own tests on the patched tree: `ling-admin codex test`.

ruff and mypy (pinned in setup.py's `dev` extra: `.venv/bin/pip install -e .[dev]`) run inside the suite as a ratchet, `tests/test_code_quality.py` and `tests/test_typing.py`: no count per file and rule may rise, and a new file must be formatted and clean (`.venv/bin/ruff format <file> && .venv/bin/ruff check --fix <file>` fixes most findings). Details: [docs/dev/testing.md](docs/dev/testing.md#the-code-standard-ratchet).

## Gotchas: the hard rules

- **Tests never touch the real machine.** `tests/conftest.py` gives every test its own `HOME`, re-points every `dreamference` module path computed at import under the real home into the test's home, fails any real `docker` command that changes something (reads like `docker info` stay allowed), fails any spawn of the installed `ling` (or anything under `~/.local/bin` and the install directory) and any connection to the model server's ports (8000, 18000), and stubs `GoogleService.start` and `RetiredWebChat.offer` (restore one from the `REAL_*` names in conftest to test it). A test needs a stand-in for the agent (`FAKE_MIGHTLING` in `test_night_shift.py`); only `test_mightling_slash_commands.py`, marked `installed_binary`, runs the built binary. A test needing a real path must opt in explicitly. Tests never write `/etc/avahi` or run sudo; `ling-docs` tests never use the real `HOME`. Details and history: [docs/dev/testing.md](docs/dev/testing.md).
- **`codex/` is never modified, and cargo never runs inside it**: even `cargo tree` rewrites the submodule's `Cargo.lock`. Changes to Codex are small numbered patches in `codex-patches/` under a size cap (`test_the_patches_stay_small`, raised only explicitly) or code in `ling-rs/`. No `--locked` for the Codex build. Details: [docs/dev/codex-build.md](docs/dev/codex-build.md).
- **`ling` is never upstream `codex`.** `CodexInstaller` resolves the built path only; a fallback to a `codex` on PATH would silently bring back the unbranded agent.
- **Host safety.** On unified memory a model load can freeze the whole host. Keep both layers, `check_host_safety()` before a load and the PSI watchdog during it, when touching `start_server()`; anything that runs beside a resident model server (indexing, sidecars) must bound its memory. Details: [docs/dev/host-safety.md](docs/dev/host-safety.md).
- **Two install modes.** A checkout has `codex/`, `codex-patches/` and the crates beside the package; a release install has only the wheel and prebuilt binaries. Anything new that reaches for a path under `REPO_ROOT` needs the `has_source()` guard. Details: [docs/dev/install-modes.md](docs/dev/install-modes.md).
- **Host control sockets** (Docker, D-Bus, systemd…) are masked from sandboxed commands only at `/airgapped on` (patch 0019, `host_sockets_at`); at `off` the sandbox is Codex's. Details: [docs/dev/airgapped.md](docs/dev/airgapped.md).
- **Sandbox results need the AppArmor profile.** `kernel.apparmor_restrict_unprivileged_userns` is 1, so `bwrap` (and Chromium's sandbox) need a profile to create a user namespace. Shells under PyCharm run in a permissive profile and pass anyway; units, the Night Shift timer and SSH jobs do not. Check `cat /proc/self/attr/current` before trusting a sandbox result; `ling-admin host setup` installs the profile. Details: [docs/dev/host-safety.md](docs/dev/host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one).
- **Unattended callers of `ling`** (Night Shift, the egress audit, SWE-bench, the Codex test runner) name the model server in `DREAMFERENCE_VLLM_HOST`: a node browse is a multicast DNS query, which the egress audit counts as a failure.
- **Launch flags belong in the model registry**, not in the launch builder ([docs/dev/models-and-engines.md](docs/dev/models-and-engines.md)). Ask the *server* which model is running (`model_key_for_served_id`), never the config.
- **The default model is `qwen3.8-27b-nvfp4-dflash2` on SGLang** (since 2026-09-29), with ling-engine's `ling-serve` beside it, opt-in since 1.7.2 (`engine_recipes['ling']`, `server start --engine ling`, `server_engine`). It is the only model served; the registry holds two candidates (`specs/DREAMFERENCE_MODELS.md`): `qwen3.8-27b-minima-nvfp4-dflash2` for night 2's A/B (§2.2) and the Flash-Next trial (§2.3).
- **The torch.compile cache is persistent and only partly self-invalidating**: vLLM's hash ignores `num_speculative_tokens`, so `_reset_stale_compile_cache()` keeps its own `model|method|n` signature ([docs/dev/models-and-engines.md](docs/dev/models-and-engines.md#the-torchcompile-cache-is-persistent-and-only-partly-self-invalidating)).
- **One air-gap reader**, `ling-rs/airgapped/`: Python and the app ask `ling airgapped --json`, failing closed. The node locator stays a **byte-identical copy** (`ling-rs/node-locator/`, `ling-web-rs/src/node_locator.rs`), compared by a test: change both or neither.
- **Never write the user's choices into the repository** where a clone or the agent could write them: submodule indexing choices live in `$CODEX_HOME/ling-code.toml`, a node's advertisement in `~/.config/dreamference/node-advertise.json`, and for `/airgapped` the strictest of the repository's and the user's config wins.
- The specification is `specs/`, one document per area, indexed by `specs/README.md` (which also lists the code defects found when the specs were reconciled with the code on 2026-09-28). There is no single `DREAMFERENCE_SPEC.md` any more; read the one document you need.
- `.gemini/`, `.antigravity/`, `.dreamference/` are gitignored leftovers of other tools.

## Where things live

Thirteen packages under `dreamference/` (twelve subsystems and `cli/`), each with an `__init__.py` that is a re-export facade with an explicit `__all__`:

| Package | Role |
| --- | --- |
| `config/` | 4-tier config resolution |
| `hardware/` | Model matrix registry, HF downloads/tensorization, GB10 telemetry |
| `vllm_server/` | Docker vLLM/SGLang lifecycle, launch-arg construction, host-safety guards |
| `runner/` | Per-agent installer + runner pairs, sandbox prefixes, the `ling` build (`codex_branded_builder.py`) and Codex's test runner |
| `chat/` | The sidecars (SearXNG, the Google service, image search and its MCP tool, speech-to-text), the Electron desktop app's build and launch, and the removal of the retired Onyx web chat's leftovers |
| `context_engine/` | AST symbol extraction + TF-IDF/dense retrieval |
| `mcp_server/` | stdio MCP server for JetBrains/VS Code |
| `night_shift/` | Night Shift: the overnight queue runner, its host probes, report and timer |
| `node/` | The node half of the client/server split: the advertised service file, the node id, what is published to the LAN, pairing, jobs and model sync over SSH |
| `swe_bench/` | `ling-admin swe-bench`: `ling` inside each SWE-bench instance's container, A/B runs and reports |
| `audit/` | `ling-admin audit egress`: the strace-based network audit of a real `ling` session |
| `forge/` | Forgejo on every node ([forge.md](docs/dev/forge.md)) |
| `remote/` | Remote access: the node's NetBird control plane, its CA, the relay box over SSH, LAN enrolment |
| `cli/` | `ling-admin`'s parser and dispatch (`dreamference_cli_controller.py`) |

Outside the package:

| Path | What |
| --- | --- |
| `codex/` | Submodule: the `dreamference/codex` fork, pinned to `CODEX_RELEASE_TAG` (`rust-v0.158.0`); never modified |
| `codex-patches/` | The numbered hooks applied to an export of `codex/` |
| `codex-tests/` | Skips, snapshot overlays and test-only patches for `ling-admin codex test` |
| `ling-rs/` | The launcher crate compiled into `ling`, plus leaf crates (`airgapped/`, `node-locator/`, `skills/`, `masking/`, `apps/`, `web/`, and the messenger bridges `chat/` and `signal/`) and `prompts/` |
| `ling-web-rs/` | `ling-search` and `ling-fetch` (own lockfile, own target directory) |
| `ling-code-rs/` | `ling-code`, the code index |
| `ling-docs-rs/` | `ling-docs`, the local file index |
| `ling-engine/` | Submodule: the spec of a Qwen3.8-27B-specific model server; never reaches a build |
| `desktop/electron/`, `desktop/ui/` | The desktop app and Work's React UI |
| `docs/` | The MkDocs user site; `docs/dev/` is developer notes, excluded from the site |
| `specs/` | One spec per area (`specs/README.md`) |
| `install.sh`, `install.ps1` | Release installers |
| `scripts/`, `paper/`, `website/`, `images/` | Helper scripts, the paper, the one-page site, logos |

`ling-admin` (`dreamference.cli:main`) has about thirty subcommand groups (`init`, `run`, `server`, `codex`, `node`, `night`, `desktop`, `audit`, …); every command rejects unknown arguments. The full list: [docs/dev/cli.md](docs/dev/cli.md).

`ling`, the terminal agent, is **not** Python: it is the Rust binary `CodexBrandedBuilder` builds, linked at `~/.local/bin/ling`, so every Codex flag and subcommand works natively. The old `chat` subcommand that started the agent was removed once the agent stopped being a Python wrapper; `ling-admin run "…"` remains for one-shot tasks with any configured agent. `ling-search` and `ling-fetch` are Rust binaries from `ling-web-rs/`, installed beside `ling`; `WebTools` in `mcp_server/web_tools.py` is only the MCP server's copy of the same behaviour.

## Architecture in brief

**Config precedence** (`config/dreamference_config.py`): constructor kwarg → `DREAMFERENCE_*` env var → `dreamference.toml` (local, then `~/.config/dreamference/config.toml`) → module-level `DEFAULT_*` constant, all in `DreamferenceConfig.__init__`. `save_config()` writes only values that differ from the defaults, so a round-trip does not fossilize defaults into the TOML.

**Runner dispatch is a strategy switch on `config.agent_runner`** in `cli/dreamference_cli_controller.py` (`run_cli`). Each agent has a matching `<agent>_installer.py` / `<agent>_runner.py` pair, and every runner checks vLLM health → provisions the agent CLI if missing → translates Dreamference config into the agent's own CLI flags → `subprocess.call`. Adding an agent means adding the pair, exporting both from `runner/__init__.py`, and extending the dispatch chain. Runners other than Codex wait for the server with `VLLMReadinessWaiter`; the `ling` launcher does its own waiting.

**Models and the model server.** `ModelMatrixRegistry.MATRIX` (`hardware/model_matrix_registry.py`) is the source of truth for launch flags; `launch_overrides` names the engine (SGLang for the default model, ling-serve in its `engine_recipes`) and pins a docker image. Speculation reaches vLLM only as `--speculative-config` JSON from `resolve_speculative_config()`. Details: [docs/dev/models-and-engines.md](docs/dev/models-and-engines.md). The diffusion sidecar is switched off (`DIFFUSION_ENABLED = False`) and kept for later: [docs/dev/diffusion.md](docs/dev/diffusion.md).

**Host safety.** `check_host_safety()` refuses an unsafe host before a load; `MemoryPressureWatchdog` kills the container on PSI pressure during one, through the fastest kill path the host can still run. `ling-admin host setup` (`HostSafetySetup`) applies the fixes. Details: [docs/dev/host-safety.md](docs/dev/host-safety.md).

**`ling` is Codex, built here.** `CodexBrandedBuilder` exports the pinned `codex-rs/`, copies `ling-rs/` in as `codex-rs/ling`, applies `codex-patches/`, builds `codex` (installed as `ling`), `codex-code-mode-host` and, on Linux, the Signal bridge's daemon `ling-signal` (beside `ling`, never on PATH) into `~/.local/share/dreamference/mightling/bin/`, and links `ling`, `ling-search`, `ling-fetch`, `ling-admin`, `ling-code` and `ling-docs` into `~/.local/bin`. V8 comes from the upstream vendor's `rusty-v8` release assets, checked against the submodule's manifests. Details: [docs/dev/codex-build.md](docs/dev/codex-build.md).

**The launcher** (`ling-rs/src/lib.rs`) resolves the model server, waits for it, reads the model id and `max_model_len` from `/v1/models`, writes `model_catalog.json` and `config.toml`, and prepends `--oss --local-provider openai-custom --model <id>`. `CODEX_HOME` is `~/.mightling`, never `~/.codex`, so the vendor's sign-in is never shared; patches 0013, 0015 and 0016 close the channels to the vendor. Details: [docs/dev/launcher.md](docs/dev/launcher.md); its loop guard: [docs/dev/loop-guard.md](docs/dev/loop-guard.md). System prompts are chosen by name (`default`, `high-swe`, custom): [docs/dev/prompt.md](docs/dev/prompt.md).

**The egress audit**, `ling-admin audit egress`, runs a real `ling exec` under strace and passes only if every connection is an allowlisted loopback port and no DNS query left. Re-run it after any Codex bump; `codex build` runs it. Details: [docs/dev/egress-audit.md](docs/dev/egress-audit.md).

**`/airgapped`** has two levels, `off` and `on`; `on` gives every sandboxed command an empty network namespace (patch `0019`), never goes with Full Access, and loads no plugin (allowed at `off`, but never the vendor's). Details: [docs/dev/airgapped.md](docs/dev/airgapped.md).

**Where edits land:** in place, as in Codex; `/worktree on` starts new TUI sessions in a worktree, `/worktree apply` brings changes back. Details: [docs/dev/launcher.md](docs/dev/launcher.md#where-edits-land-worktree).

**Skills from other agents** are linked under `~/.mightling/skills/from-<agent>/` at every start and budgeted, with no Codex patch. Details: [docs/dev/skills.md](docs/dev/skills.md).

**Night Shift** is two halves that share only files: `/night add` queues a task, `ling-admin night run` (a user timer) runs each in its own git worktree and memory-capped scope, three attempts by default (`[night] best_of`, chooser v1 picks one); the runner commits, and nothing is merged or pushed. Details: [docs/dev/night-shift.md](docs/dev/night-shift.md).

**SWE-bench** runs `ling` inside each instance's container (third-party arm64 images, validated instances only, a patchelf'd `ling`, the container as the sandbox). Its number compares configurations on this machine and is not a leaderboard score. While a run lasts, the model gate in front of the model server (its own container, started by `server start`; the engine sits on loopback behind it) refuses every request not from the run's network; `night pause` lets them through. Details: [docs/dev/swe-bench.md](docs/dev/swe-bench.md).

**Nodes.** A machine with `~/.config/dreamference/node-id` is a node and uses loopback; a client finds its node through tiers ending in an mDNS browse. A second node is managed over SSH through one forced command, never an open port; jobs run in a capped, sandboxed user unit. Details: [docs/dev/node.md](docs/dev/node.md).

**The web UI is `ling web`** (`ling-rs/web`, port 3100) and the desktop app the same page; the Onyx web chat is retired (spec `specs/DREAMFERENCE_MIGHTLING_ASK.md` §19). Image search and voice are sidecars behind both (`ling-admin images`, `ling-admin voice`); `server start` on a node starts them. Details: [docs/dev/cli.md](docs/dev/cli.md#sidecars-behind-ling-web-and-the-app).

**The desktop app** is stock Electron with `ling` inside: several windows (New Window; a thread, the terminal or the diff popped out), each Ask or Work, all on its own `ling app-server`, the app's only one (single-instance lock); one IPC channel routed per window, one composer per thread, every message vetted by the main process against `ling-rs/web/policy.json`, the file `ling web` enforces (both run its conformance cases); egress is closed in the main process and audited with `--app`.

**The code index**, `ling-code` (`ling-code-rs/`), answers `def`/`refs`/`callers`/… from codebase-memory's graph and scip stores; queries only read, indexing runs outside the sandbox under one host-wide memory budget, and a submodule is indexed only if it is yours or you ask. Details: [docs/dev/code-index.md](docs/dev/code-index.md).

**The local file index**, `ling-docs` (`ling-docs-rs/`), indexes the user's collections (`~/Documents`, `~/Downloads` by default) for `docs_search`/`docs_read`, opens no socket, and indexes in bwrap under the same memory budget. Details: [docs/dev/local-file-index.md](docs/dev/local-file-index.md).

**Phone messengers** ship with Mightling and are **off until a command turns one on**: `ling signal setup` (Signal, linked to the owner's account, Note to Self only), `ling chat start` after `ling chat telegram setup` or `ling-admin matrix start` (Matrix over Tailscale, Telegram). One surface: `ling signal …` and `ling chat …` are launcher subcommands; the small `ling-signal` binary is only the daemon a system account runs. `stop` disables, and the egress audit names every bridge that is on. Details: [docs/dev/messengers.md](docs/dev/messengers.md).

**The context engine** (`context_engine/`, `ling-admin index` and `mcp`) writes `.dreamference/context_index.json` and `context.db`; its indexing is bounded because it runs beside a resident model server. Details: [docs/dev/context-engine.md](docs/dev/context-engine.md).

## Conventions

- **One class per file**, file named after the class in snake_case. `__init__.py` does the re-exporting; module files hold no top-level logic beyond constants.
- **Class-level `@classmethod` over instances** for stateless helpers: the codebase has ~56 classmethods against 16 `__init__`s. `ModelMatrixRegistry` and the installers are pure classmethod namespaces.
- Module docstrings and full Google-style Args/Returns docstrings on public methods are the norm; match this density.
- `typing.Final` annotations on module constants.
- User-facing output goes through `rich` console or emoji-prefixed `print` (`✅ ⚠️ ❌ 🚀 💡`). Match the existing voice in CLI paths.
- The standard is [specs/DREAMFERENCE_PYTHON_QUALITY.md](specs/DREAMFERENCE_PYTHON_QUALITY.md), checked by the ratchet above (`.venv/bin/python -m pytest tests/test_code_quality.py tests/test_typing.py -q`). Where today's code and the rules differ, new code follows the rules: `str | None`, not `Optional[str]`; a docstring's summary on its first line.

## Searching this codebase

Use `rg -n "^class Foo"` for a definition and `ast-grep` for call sites by shape, always with constrained output (`--json=compact | jq`; a bare match prints the whole node). The command, measurements and verified patterns: [docs/dev/searching.md](docs/dev/searching.md).

## Detail files

- [testing.md](docs/dev/testing.md): how the tests are kept off the real machine, and why
- [models-and-engines.md](docs/dev/models-and-engines.md): config precedence, SGLang and its traps, pinned images, the model matrix, speculation, the compile cache
- [diffusion.md](docs/dev/diffusion.md): the diffusion sidecar (switched off)
- [host-safety.md](docs/dev/host-safety.md): pre-flight checks, the PSI watchdog, the AppArmor profile for `bwrap`
- [install-modes.md](docs/dev/install-modes.md): checkout vs release install, `install.sh`, `HostSafetySetup`
- [codex-build.md](docs/dev/codex-build.md): building `ling`, the patches and their size cap, V8, the submodules
- [launcher.md](docs/dev/launcher.md): the launcher, `~/.mightling`, the vendor channels closed
- [prompt.md](docs/dev/prompt.md): named system prompts
- [egress-audit.md](docs/dev/egress-audit.md): `ling-admin audit egress`
- [airgapped.md](docs/dev/airgapped.md): `/airgapped`
- [skills.md](docs/dev/skills.md): skills from other agents
- [night-shift.md](docs/dev/night-shift.md): Night Shift
- [swe-bench.md](docs/dev/swe-bench.md): the SWE-bench harness
- [node.md](docs/dev/node.md): clients, nodes, pairing and jobs
- [desktop.md](docs/dev/desktop.md): the Electron desktop app
- [code-index.md](docs/dev/code-index.md): `ling-code`
- [local-file-index.md](docs/dev/local-file-index.md): `ling-docs`
- [context-engine.md](docs/dev/context-engine.md): the Python context engine
- [remote-access.md](docs/dev/remote-access.md): remote access over a NetBird overlay, its DNS and its migration
- [adapters.md](docs/dev/adapters.md): LoRA adapters: `ling-admin adapter`, the signature, serving them
- [messengers.md](docs/dev/messengers.md): `ling signal`, `ling chat` and `ling-admin matrix`
- [sync.md](docs/dev/sync.md): `ling sync`
- [searching.md](docs/dev/searching.md): `ast-grep` and `rg` on this tree
- [cli.md](docs/dev/cli.md): every `ling-admin` subcommand, the sidecars behind `ling web` and the app
- [sessions.md](docs/dev/sessions.md): the work streams of 2026-10-10 (retired)
