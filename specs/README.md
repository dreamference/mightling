# Puffin Technical Specification

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
| [DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md) | *Proposed:* `/night`, an overnight task queue worked in git worktrees on the GB10 |
| [DREAMFERENCE_SELF_SPEEDING.md](./DREAMFERENCE_SELF_SPEEDING.md) | *Proposed:* retraining the speculative drafter on your own sessions (`puffin-admin drafter`) |
| [DREAMFERENCE_PUFFIN_EGRESS.md](./DREAMFERENCE_PUFFIN_EGRESS.md) | *Proposed:* `puffin-admin audit egress` and `puffin --airlock` |
| [DREAMFERENCE_PUFFIN_FAST_TOOLS.md](./DREAMFERENCE_PUFFIN_FAST_TOOLS.md) | *Proposed:* `puffin fast`, a diffusion model (DiffusionGemma 26B A4B) for long routine output |

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

The reconciliation on 2026-09-28 found these places where the **code** was wrong or unsafe. Most were fixed on 2026-09-29, and the individual specs now describe the fixed behaviour, each with a one-line note of what it replaced.

### Still open

| Where | Defect | Spec |
|---|---|---|
| vLLM engine (default recipe) | Died once with `CUBLAS_STATUS_INTERNAL_ERROR` in a bf16 GEMM with two requests running, KV cache at 92% and ~21 GB of host memory free (2026-09-29 11:49); Docker restarted it. Likely cuBLAS failing to get workspace under memory pressure; not reproduced | INFERENCE |
| `HF_HOME` and the model container | The host downloads to `$HF_HOME/hub` when `HF_HOME` is set, but the container always mounts `~/.cache/huggingface`; with pinned checkpoints now served from their snapshot directories, a host with a custom `HF_HOME` would launch against a path the container cannot see. Pre-existing for vLLM, sharper for SGLang | INFERENCE |
| `dreamference-searxng` | After 18 h up, the container's Docker-embedded DNS (`127.0.0.11`, as it is joined to `onyx_default`) answered SERVFAIL for every name, so every engine failed and both the agents' `web_search` and Onyx's web search came back empty. Fresh containers on the same two networks resolved fine and `docker restart` cleared it; the cause is not known. The empty result is now reported as an error naming each failed engine, with the restart as the hint | ONYX |

### Accepted by design

| Where | Behaviour | Why it stays | Spec |
|---|---|---|---|
| Model server on `0.0.0.0:8000` | Listens on every interface with no API key, so any machine on the LAN can send it prompts | Puffin assumes the local network is trusted (decided 2026-09-30). The server must answer on the Docker bridge for Onyx and OpenHands, and binding the bridge address alone would break every `localhost:8000` client. On an untrusted network the user blocks the port with a firewall rule (`docs/privacy.md`) | INFERENCE |

### Fixed on 2026-09-29

| Where | Was | Now |
|---|---|---|
| `clear tensorize-cache` / `clear model-cache` | Deleted the *parents*: all of `~/.cache/dreamference` (build and compile caches) and all of `~/.cache/huggingface` (including the login token) | Remove only `tensorizer/` and `hub/`, and report files a container left behind as root |
| `puffin-admin clear-tensorize-cache` | Parsed and did nothing | Same as `clear tensorize-cache` |
| `ensure_docker_image()` | Built the plain `Dockerfile` under a pinned DFlash tag | Builds only `DEFAULT_VLLM_IMAGE`; for other missing local tags, says how they are built |
| Goose config | MCP extension ran `dreamference mcp` | Runs `puffin-admin mcp` by its full venv path |
| OpenHands runner | Port 3000 (Onyx's), on every interface, with the Docker socket mounted; `LLM_BASE_URL` pointed at the container's own localhost | `127.0.0.1:3001`; vLLM reached through the bridge gateway |
| Onyx web UI | nginx published on every interface, so the default admin account was reachable from the LAN | `configure()` binds ports 80 and 3000 to 127.0.0.1 through Onyx's `.env` |
| Continue runner, Aider runner | Named the draft model (a speculative head, not a served model) for autocomplete / as the architect | Name the served model only |
| Context engine | Embeddings were never computed (a one-shot iterator was read twice), never stored (sqlite-vec not loaded, `hash()` row ids), never reloaded; no nomic prefixes | Stored in a plain table, reloaded by `load_index`, `search_document:`/`search_query:` prefixes, keyword-only when the model is unavailable |
| `puffin` launcher | Refused-command check read the first argument only | Finds the subcommand from Codex's own option definitions |
| `puffin` | Fresh `CODEX_HOME` opened on the ChatGPT sign-in screen; shared upstream's `~/.codex` and its ChatGPT login; Codex usage analytics on | `-c model_provider` on launch; own `~/.puffin`; analytics client disabled (patch 0013) |
| `scripts/*.sh` | Old default model; `run_vllm_gb10.sh` launched vLLM directly, bypassing host safety | Thin wrappers over `puffin-admin` |
| `server start --draft-model` | Emitted the removed `--speculative-model` / `--num-speculative-tokens` flags (vLLM 0.2x rejects them), `None` as the depth, and dropped the recipe's speculative settings | `resolve_speculative_config()` builds `--speculative-config` JSON, layered on an external-drafter recipe or replacing a self-speculation one; the compile-cache signature follows the launched depth |
| Gmail service | Module docstring described the retired GNOME-holds-the-token design; `GNOME_TOKEN_UNIT` unused | Docstring describes the OAuth flow as built; constant removed |
| `puffin` network traffic | A traced session with no ChatGPT login still reached `ab.chatgpt.com` (OTEL metrics to Statsig, default-on in release builds), `chatgpt.com/backend-api/plugins/featured`, `github.com` (`git ls-remote openai/plugins` at startup), and, in the TUI, OpenAI's announcement tip from `raw.githubusercontent.com` | Patch 0015 closes all four at the call sites; the launcher points `chatgpt_base_url` at a closed local port so any call not yet found fails locally |
| Tests | Wrote the real `~/.continue/config.json` and could reach the real Onyx `.env` and containers | `tests/conftest.py` gives every test its own home and a scratch Onyx `.env`, and fails any real container recreate |
| Context indexing | Walked everything outside a short ignore list with no size cap: 11,565 files and 2.3 GB here (a Tauri `target/` and the codex submodule), with 32 workers, full 8,192-token embeddings and a quadratic TF-IDF. Beside a resident vLLM it pushed the host under earlyoom's line and **earlyoom killed vLLM**. `puffin-admin mcp` indexes on first query, so any workspace with a Rust build tree was exposed | Asks `git ls-files` (honouring `.gitignore`, not entering submodules), skips `target/`, files over 1 MiB and binaries, sizes the pool to the work, runs the embedding model on the CPU at 1,024 tokens, and computes TF-IDF with one `Counter` per file: 223 files, ~2 GB peak, earlyoom untouched with vLLM serving |
| Context search after a restart | TF-IDF tables were not saved, so a new process scored by FTS5 and embeddings only; with vLLM resident the embedding model failed on CUDA (out of memory) and search went keyword-only | Tables saved with the index (an older index is rebuilt once); embedding model on the CPU |
| `puffin-admin mcp` | Answered the `notifications/initialized` notification with a `-32601` error; reported a failing tool as a parse error with no id (the client waited forever); no `ping`; `ide_apply_diff` claimed success while writing nothing | Notifications unanswered, `-32603` with the request's id, `ping` supported, `ide_apply_diff` says `not_applied` |
| `web_search` (MCP and `puffin-admin search`) | When every SearXNG engine failed, returned zero results, indistinguishable from a query with no coverage | Returns an error listing each engine and its failure, with a restart hint |
| Gmail connect | A Google grant with Gmail access unticked on the consent screen was saved and listed as connected, then failed every search with an opaque `[AUTHENTICATIONFAILED] Invalid credentials` (found live on one of three connected accounts; tokeninfo showed no `mail.google.com` scope) | The OAuth callback refuses such a grant and says which box to tick; an IMAP authentication failure now says to reconnect the account |
| Diffusion sidecar (`dreamference-diffusion-8001`) | **Had never produced a token.** transformers refused the Tiny-A2D remote code because it imports `dllm` (only under `if __name__ == "__main__"`); past that, its forward read `decoder_layer.attention_type`, which this transformers no longer sets; and the service called `model.generate`, a left-to-right decoder, which is the wrong algorithm for a block-diffusion model. `/health` reported the ImportError, but `server start` launched it anyway and `endpoints` advertised it | Main-guard imports are not load requirements; `attention_type` restored from `config.layer_types`; a block-diffusion sampler (32-token blocks, block-causal attention, most-confident-first commitment, the card's 128 steps) replaces `generate`. Chat completions return correct code in ~2 s on CUDA. Raw `/v1/completions` on this chat-tuned 0.5B model stays weak under every attention variant tried |
| Diffusion sidecar port | Bound `0.0.0.0:8001` under `--network host`, offering the model to the LAN | Binds `127.0.0.1`; nothing outside the host, or in a container, uses it |
| `puffin-admin puffin configure` / `main-model inspect` with a non-default model running | Registered, and compared against, the *configured* model: after `server start --model` served another, the web chat asked for a name the server did not know (empty answers), and `inspect` reported every SGLang flag as drift from a vLLM recipe | Both ask the server (`model_key_for_served_id`); the canary probes run with thinking off |
| Offline test suite and the live deployment | Every run of the `configure` tests ran real docker commands against the running stack: recreated the Gmail sidecar, joined SearXNG and speech-to-text to Onyx's network, copied test logos into the web server, rewrote its bundle and patched the API server through `docker exec`. After HOME isolation the Gmail sidecar came back with a pytest temp folder and test secret, and Gmail search answered "unauthorised" | conftest fails any real docker command that changes something and stubs the sidecar and UI-patcher methods; the live stack was restored with `configure` |
| Live slash-command suite | Took 1,101 s on Qwen3.8 against 807 s on the 122B. The harness waited up to 30 s to *see* the busy marker before each turn, and the faster model often finished a short turn between two screen reads, so a dozen tests each sat out the full 30 s. Its "the model answered" check could not fail either: the prompt it echoes already contains the word it looked for | The wait accepts an answer already on screen and gives up on the marker after 8 s (it is drawn when the turn begins); the check counts the word beyond the prompt's own |
| SGLang sampling on the completions endpoint | FlashInfer's untruncated-sampling kernel returned token 0 (`!`) for every sampled completions request (16 of 16); the NVFP4 canary caught it | `--sampling-backend pytorch` in the recipe: 0 of 16, no speed cost |
| Qwen3.8's chat template | Answered HTTP 400 to Codex's reasoning efforts `high`/`minimal` and refused a system message after the first | Patched on a copy at launch (`ChatTemplatePatcher`); a non-matching anchor stops the start |
| SGLang launch with pinned checkpoints | Restart loop: SGLang dropped `--revision` on offline lookups, and a download by commit writes no `refs/main` | Pinned checkpoints are served from their snapshot directories |
| vLLM usage statistics | Reported hardware, model and settings to stats.vllm.ai every ten minutes | `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` in every model container |
| `puffin` /pets and the TUI update check; Aider | /pets fetched art from OpenAI's CDN; a `true` in config.toml re-enabled the update check to api.github.com; Aider checked PyPI, offered PostHog and fetched litellm's price table from GitHub | /pets hidden (patch 0010), the launcher forces the update check off and drops `tui.pet`, Aider runs with its opt-outs |
| `puffin-admin model list` | `UnboundLocalError` on `Table`: a local import elsewhere in `run_cli` made the name local to the whole function | Local re-imports of module-level names removed |
| `puffin-admin server` / `clear` / `model` / `main-model` / `diffusion-model` without a subcommand | Printed nothing and exited 0 | Print the group's help and exit 1, like `desktop` and `puffin` |
