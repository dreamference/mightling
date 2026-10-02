# Puffin Technical Specification

> - **Version:** 1.2.0 (`dreamference.__version__`). Release `v1.3.0` was published on 2026-10-02 from commit `5d2254f`; the release workflow stamps the version in its own checkout, so the source still says 1.2.0
> - **Target Hardware:** NVIDIA GB10 (Blackwell SM121, 128 GB unified memory)
> - **Deployment Model:** single node, with the model, the code and the sessions on the machine. Not air-gapped by default: web search, page fetch and Gmail use the internet unless `/airgapped on` ([PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)); a GB10 offered to the local network with `puffin-admin node enable` serves clients that find it over mDNS ([PUFFIN_NODE](./DREAMFERENCE_PUFFIN_NODE.md), Part 1 built in part)
> - **License:** AGPL-3.0-or-later
> - **Specs last reconciled with the code:** 2026-09-28; the descriptive specs were re-checked against the parser, the model registry and the source tree on 2026-10-01, and CLI, CODEBASE, ARCHITECTURE, CONTEXT and DOCKER again on 2026-10-02 (at `cebd6db`) for `swe-bench`, `audit egress`, `node` and the three packages behind them

This directory holds the specification, split into focused documents. This page is the index.

**Where the old monolith went:** earlier versions of this file also carried the whole pre-split specification, about 900 lines, below the index. That copy had drifted from the code on nearly every point (default agent, CLI commands, models, recipes), and it duplicated the modular documents. It was removed on 2026-09-28; it is in git history if needed.

---

## 📚 Documents

### System

| Document | What it covers |
|---|---|
| [DREAMFERENCE_PUFFIN_NODE.md](./DREAMFERENCE_PUFFIN_NODE.md) | *Implemented in part on one GB10 (2026-10-02, §18): discovery and `puffin node`, `puffin-admin node enable`, pairing and managing a second node over SSH, script jobs on another node.* Splitting Puffin into a client (`puffin`, `puffin-code`, `puffin-app`; Ubuntu, macOS, Windows) and `puffin-node` (the GB10), found over mDNS with nothing to configure; then several nodes with no stored roles, and jobs sent to another node over SSH |
| [DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md) | System overview: `puffin`, the Puffin web UI, `puffin-admin`, the twelve packages |
| [DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md) | Source layout, class inventory, import conventions, the default model's full launch command |
| [DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md) | Requirements, installation, helper scripts, troubleshooting |
| [DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md) | `puffin-admin` command reference, configuration tiers, environment variables |

### Models and inference

| Document | What it covers |
|---|---|
| [DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md) | The model matrix (eight entries), the default model (Qwen3.8-27B on SGLang), GB10 detection |
| [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md) | The launch engines (vLLM, and SGLang for the default model), recipes and precedence, speculative decoding, host safety |
| [DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md) | Images (project default and pinned DFlash images), caches, tensorization, containers |
| [DREAMFERENCE_PREFIX_CACHE.md](./DREAMFERENCE_PREFIX_CACHE.md) | Prefix caching on the hybrid GDN + DFlash stack: findings and runtime patches |

### Agents

| Document | What it covers |
|---|---|
| [DREAMFERENCE_PUFFIN_CODEX.md](./DREAMFERENCE_PUFFIN_CODEX.md) | **`puffin`**: the Codex fork, patch series, Rust launcher, build, `update`, `app`, `/usage` |
| [DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md) | All agent runners: Codex (default), Cline, Continue, OpenHands |
| [DREAMFERENCE_PUFFIN_GMAIL.md](./DREAMFERENCE_PUFFIN_GMAIL.md) | Read-only Gmail for the terminal agent (`puffin-admin gmail`) |
| [DREAMFERENCE_CODEX.md](./DREAMFERENCE_CODEX.md) | Superseded Codex page; what `CodexRunner`/`CodexInstaller` still do |
| [DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md) | The context engine (`puffin-admin index`, MCP `workspace_search_code`), with known gaps |
| [DREAMFERENCE_PUFFIN_CODE_INDEX.md](./DREAMFERENCE_PUFFIN_CODE_INDEX.md) | *Implemented (§14):* code index for `puffin`, the separate `puffin-code` binary (codebase-memory-mcp + SCIP) |
| [DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md) | `/night`, an overnight task queue worked in git worktrees on the GB10 (implemented 2026-10-01; §11 records what was built) |
| [DREAMFERENCE_SELF_SPEEDING.md](./DREAMFERENCE_SELF_SPEEDING.md) | *Proposed:* retraining the speculative drafter on your own sessions (`puffin-admin drafter`) |
| [DREAMFERENCE_PUFFIN_EGRESS.md](./DREAMFERENCE_PUFFIN_EGRESS.md) | `puffin-admin audit egress` (implemented: `exec` sessions 2026-10-01, the interface with `--tui` and the audit after `codex build` 2026-10-02, §10); *proposed:* the airlock, now the `on` level of `/airgapped` |
| [DREAMFERENCE_PUFFIN_COMPACTION.md](./DREAMFERENCE_PUFFIN_COMPACTION.md) | *Partly implemented (2026-10-02):* the interactive limit follows the KV pool, the rule-built ledger hook (on by default), Night Shift's per-task limit (off: Phase 0 found it cost the task); proposed: when `puffin` compacts: a limit tied to the KV pool, a per-task limit for Night Shift, what a compaction keeps and drops, a rule-built ledger re-injected after it, and why the diffusion model is not used for it |
| [DREAMFERENCE_PUFFIN_PROMPT.md](./DREAMFERENCE_PUFFIN_PROMPT.md) | *Proposed:* `/prompt`, choosing `puffin`'s system prompt by name: `default` (today's) and `high-swe`, a 4,229-char method for repository tasks written from the first benchmark run's failures; a ten-run pilot, no score claim yet |
| [DREAMFERENCE_PUFFIN_SKILLS.md](./DREAMFERENCE_PUFFIN_SKILLS.md) | *Phases 1 and 2 implemented (2026-10-02, §15):* skills from OpenAI, Claude, Gemini, OpenClaw and Hermes in `puffin`: per-skill links to each agent's folder, a frontmatter preflight, the catalogue budget, `puffin skill list\|show\|add\|remove\|search\|enable\|disable\|source\|adopt`, and a tool glossary that measured as unnecessary and is off by default. Not built: ClawHub and Hermes as install sources, repository `.claude/skills` |
| [DREAMFERENCE_PUFFIN_AIRGAPPED.md](./DREAMFERENCE_PUFFIN_AIRGAPPED.md) | *Partly implemented (2026-10-01, §14):* `/airgapped`, three levels of internet access for a `puffin` session: everything, DuckDuckGo-only search, none |
| [DREAMFERENCE_PUFFIN_SWE_BENCH.md](./DREAMFERENCE_PUFFIN_SWE_BENCH.md) | `puffin-admin swe-bench`, running `puffin` over SWE-bench on the GB10 (arm64 images, no network for the agent) for A/B comparisons, not a leaderboard score (Phase 1 implemented 2026-10-01; §12 records what was built, §13 the code index as a second arm and its first with/without pair) |
| [DREAMFERENCE_PUFFIN_FAST_TOOLS.md](./DREAMFERENCE_PUFFIN_FAST_TOOLS.md) | *Proposed:* `puffin fast`, a diffusion model (DiffusionGemma 26B A4B) for long routine output |
| [DREAMFERENCE_PUFFIN_CAVE_MODE.md](./DREAMFERENCE_PUFFIN_CAVE_MODE.md) | *Implemented (Phase 1):* `/cavemode`, terse answers by default (`ultra`), measured on this machine |

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

### Accepted by design

| Where | Behaviour | Why it stays | Spec |
|---|---|---|---|
| Model server on `0.0.0.0:8000` | Listens on every interface with no API key, so any machine on the LAN can send it prompts | Puffin assumes the local network is trusted (decided 2026-09-30). The server must answer on the Docker bridge for Onyx and OpenHands, and binding the bridge address alone would break every `localhost:8000` client. On an untrusted network the user blocks the port with a firewall rule (`docs/privacy.md`) | INFERENCE |
| Web UI on `0.0.0.0:3000`, **on an advertised node only** | After `puffin-admin node enable`, port 3000 is published on every interface: the web UI's one account, its chat history and its Gmail tool are open to the LAN | `puffin-app` and a browser on client machines need it; the user decided on 2026-10-02 that the web UI is shared by default. `node enable --no-web` keeps it on loopback, and a node that is not enabled keeps the loopback bind of 2026-09-29. Port 80 never leaves loopback | [PUFFIN_NODE §4, §15.1](./DREAMFERENCE_PUFFIN_NODE.md) |
| SearXNG on `0.0.0.0:8888`, **on an advertised node only** | After `node enable`, any machine on the LAN can send searches through the node | `puffin-search` on a client asks the node's SearXNG. Loopback-only otherwise, as fixed on 2026-09-29 | [PUFFIN_NODE §4](./DREAMFERENCE_PUFFIN_NODE.md) |

### Fixed on 2026-09-30

| Where | Was | Now |
|---|---|---|
| `HF_HOME` and the model containers | The host downloaded to `$HF_HOME/hub`, but the model server, the diffusion sidecar and the tensorizer container always mounted `~/.cache/huggingface`, so a host with a custom `HF_HOME` launched SGLang against snapshot paths the container could not see (and the tensorizer's container path also lacked `hub/`) | `ModelDownloader.container_volume_args()` mounts the resolved home (`HF_HOME`, `$XDG_CACHE_HOME/huggingface`, `~/.cache/huggingface`) at `/root/.cache/huggingface`, plus a separate hub mount when `HF_HUB_CACHE` moves it |

### Fixed on 2026-09-29

| Where | Was | Now |
|---|---|---|
| `clear tensorize-cache` / `clear model-cache` | Deleted the *parents*: all of `~/.cache/dreamference` (build and compile caches) and all of `~/.cache/huggingface` (including the login token) | Remove only `tensorizer/` and `hub/`, and report files a container left behind as root |
| `puffin-admin clear-tensorize-cache` | Parsed and did nothing | Same as `clear tensorize-cache` |
| `ensure_docker_image()` | Built the plain `Dockerfile` under a pinned DFlash tag | Builds only `DEFAULT_VLLM_IMAGE`; for other missing local tags, says how they are built |
| OpenHands runner | Port 3000 (Onyx's), on every interface, with the Docker socket mounted; `LLM_BASE_URL` pointed at the container's own localhost | `127.0.0.1:3001`; vLLM reached through the bridge gateway |
| Onyx web UI | nginx published on every interface, so the default admin account was reachable from the LAN | `configure()` binds ports 80 and 3000 to 127.0.0.1 through Onyx's `.env` |
| Continue runner | Named the draft model (a speculative head, not a served model) for autocomplete | Names the served model only |
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
| `web_search` (MCP and `puffin-search`) | When every SearXNG engine failed, returned zero results, indistinguishable from a query with no coverage | Returns an error listing each engine and its failure, with a restart hint |
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
| `puffin` /pets and the TUI update check | /pets fetched art from OpenAI's CDN; a `true` in config.toml re-enabled the update check to api.github.com | /pets hidden (patch 0010), the launcher forces the update check off and drops `tui.pet` |
| `puffin-admin model list` | `UnboundLocalError` on `Table`: a local import elsewhere in `run_cli` made the name local to the whole function | Local re-imports of module-level names removed |
| `puffin-admin server` / `clear` / `model` / `main-model` / `diffusion-model` without a subcommand | Printed nothing and exited 0 | Print the group's help and exit 1, like `desktop` and `puffin` |
