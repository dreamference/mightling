# Dreamference Technical Specification

> - **Version:** 1.2.0 (`dreamference.__version__`)
> - **Target Hardware:** NVIDIA GB10 (Blackwell SM121, 128 GB unified memory)
> - **Deployment Model:** single-node, air-gapped
> - **License:** AGPL-3.0-or-later
> - **Specs last reconciled with the code:** 2026-09-28

This directory holds the specification, split into focused documents. This page is the index.

**Where the old monolith went:** earlier versions of this file also carried the whole pre-split specification, about 900 lines, below the index. That copy had drifted from the code on nearly every point (default agent, CLI commands, models, recipes), and it duplicated the modular documents. It was removed on 2026-09-28; it is in git history if needed.

---

## 📚 Documents

### System

| Document | What it covers |
|---|---|
| [DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md) | System overview: `puffin`, the Puffin web UI, `puffin-admin`, the eight packages |
| [DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md) | Source layout, class inventory, import conventions, the default model's full launch command |
| [DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md) | Requirements, installation, helper scripts, troubleshooting |
| [DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md) | `puffin-admin` command reference, configuration tiers, environment variables |

### Models and inference

| Document | What it covers |
|---|---|
| [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md) | The model matrix (six entries), the default model, GB10 detection |
| [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) | vLLM launch engine, recipes and precedence, speculative decoding, host safety |
| [DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md) | Images (project default and pinned DFlash images), caches, tensorization, containers |
| [DREAMFERENCE_PREFIX_CACHE.md](./DREAMFERENCE_PREFIX_CACHE.md) | Prefix caching on the hybrid GDN + DFlash stack: findings and runtime patches |

### Agents

| Document | What it covers |
|---|---|
| [DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md) | **`puffin`**: the Codex fork, patch series, Rust launcher, build, `update`, `app`, `/usage` |
| [DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md) | All agent runners: Codex (default), Goose, Cline, Aider, Continue, OpenHands |
| [DREAMFERENCE_PUFFIN_GMAIL.md](./DREAMFERENCE_PUFFIN_GMAIL.md) | Read-only Gmail for the terminal agent (`puffin-admin gmail`) |
| [DREAMFERENCE_CODEX.md](./DREAMFERENCE_CODEX.md) | Superseded Codex page; what `CodexRunner`/`CodexInstaller` still do |
| [DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md) | The context engine (`puffin-admin index`, MCP `workspace_search_code`), with known gaps |
| [DREAMFERENCE_PUFFIN_CODE_INDEX.md](./DREAMFERENCE_PUFFIN_CODE_INDEX.md) | *Proposed:* code index for `puffin` (codebase-memory-mcp + SCIP) |

### Puffin web UI

| Document | What it covers |
|---|---|
| [DREAMFERENCE_ONYX.md](./DREAMFERENCE_ONYX.md) | Onyx Lite deployment, `configure`, branding, UI patches, voice, web search, telemetry |
| [DREAMFERENCE_IMAGE_SEARCH.md](./DREAMFERENCE_IMAGE_SEARCH.md) | The image search sidecar and tool |
| [DREAMFERENCE_GOA.md](./DREAMFERENCE_GOA.md) | Google auth through GNOME's OAuth client (Gmail implemented; Drive not) |
| [DREAMFERENCE_PDF_SEARCH.md](./DREAMFERENCE_PDF_SEARCH.md) | *Draft, not implemented:* PDF search tool |

---

## 🎯 Choose Your Path

- 🚀 **Install** → [SETUP](./DREAMFERENCE_SETUP.md)
- 🐦 **Use the terminal agent (`puffin`)** → [PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md)
- 💬 **Run the web chat UI** → [ONYX](./DREAMFERENCE_ONYX.md)
- 🛠️ **Administer (`puffin-admin`)** → [CLI](./DREAMFERENCE_CLI.md)
- 📦 **Pick or tune a model** → [MODELS](./DREAMFERENCE_MODELS.md), [INFERENCE](./DREAMFERENCE_INFERENCE.md)
- 🐳 **Manage images and caches** → [DOCKER](./DREAMFERENCE_DOCKER.md)
- 📐 **Read the code** → [CODEBASE](./DREAMFERENCE_CODEBASE.md), [ARCHITECTURE](./DREAMFERENCE_ARCHITECTURE.md)

---

## ⚠️ Known defects recorded in these specs

The reconciliation on 2026-09-28 found these places where the **code** is wrong or unsafe. Each spec documents the behaviour as it is; none of these is fixed yet.

| Where | Defect | Spec |
|---|---|---|
| `ModelDownloader.clear_tensorizer_cache()` | Deletes all of `~/.cache/dreamference` (compile cache, `puffin` build cache), not just `tensorizer/` | CLI §4.18, DOCKER §3.3 |
| `puffin-admin clear-tensorize-cache` | Registered subcommand with no handler: it does nothing | CLI §3 |
| `ensure_docker_image()` | A missing pinned bare-tag image (DFlash) is built from the main `Dockerfile` under the wrong name | DOCKER §5.3 |
| `ensure_goose_config()` | Goose MCP extension runs `dreamference mcp`; that command no longer exists | AGENTS §3.4 |
| OpenHands runner | Binds port 3000, which Onyx uses | AGENTS §7 |
| Continue runner | Tab autocomplete names a model the vLLM endpoint does not serve | AGENTS §6.2 |
| Context engine | After a cached load, search is FTS5-only; `vec_context` is never queried; nomic model not pre-fetched | CONTEXT §5 |
| `puffin` launcher | Refused-command check looks at the first argument only | PUFFIN_CODEX §4 |
| `puffin` | Fresh `CODEX_HOME` opens on the ChatGPT sign-in screen | PUFFIN_CODEX §5 |
| `scripts/*.sh` | Old default model; `run_vllm_gb10.sh` passes an unresolved alias to vLLM; `install_gb10.sh` never builds `puffin` | SETUP §3.3, §4 |
| `--draft-model` | Silently replaces the recipe's speculative config; legacy `--speculative-model` flag | INFERENCE §3.1 |
| Gmail service | Module docstring describes a retired design; `GNOME_TOKEN_UNIT` unused | GOA §0 |
| `server start --draft-model` | Without `--num-speculative-tokens`, emits `--num-speculative-tokens None`; the config's value (8) is never used here | INFERENCE §3.2 |
| Aider runner | With a draft model, `--architect` puts the *draft* (small) model in the architect seat and the main model as editor; this looks inverted | AGENTS §5.3 |
