# The `ling` launcher and its home folder

Developer notes behind the launcher summary in `AGENTS.md`. Specs: `specs/DREAMFERENCE_MIGHTLING_CODEX.md`, `specs/DREAMFERENCE_MIGHTLING_EGRESS.md`. How the binary is built: [codex-build.md](codex-build.md).

## What the launcher does

The launcher (`ling-rs/src/lib.rs`) is what the Python runner used to do, and `CodexRunner` now only builds and execs `ling`. Before Codex parses its arguments it skips everything for `--version`/`--help`/offline subcommands; otherwise it:

- resolves the vLLM URL (`DREAMFERENCE_VLLM_HOST`, then `vllm_host` in `DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml` or `~/.config/dreamference/config.toml`; on a client, the node tiers in [node.md](node.md));
- waits for the server with the old dotted progress;
- reads the served model's id **and `max_model_len` from `/v1/models`**, so it needs no copy of the model registry;
- writes `$CODEX_HOME/model_catalog.json` (Codex's own prompt plus `WEB_ACCESS_INSTRUCTIONS`; see [prompt.md](prompt.md) for named prompts);
- edits `config.toml` with `toml_edit` (so top-level keys can never be reparented into a table; `check_for_update_on_startup = false` because the update prompt would offer to replace Mightling with upstream Codex);
- prepends `--oss --local-provider openai-custom --model <id>` unless the user gave their own.

Two settings guard the connection to the local server:

- `stream_idle_timeout_ms = 900000` in the provider entry, only when absent. Upstream's 300 s idle timer is the only timeout before the first token, and it covers the server's queue as well as the prefill: one full KV pool (~157K tokens) is about 3 minutes, but three SWE-bench sessions queued behind each other can take 9 (`specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md` §1.10).
- `NO_PROXY` and `no_proxy`, extended first thing in `main()` with loopback and the model server's host, so a proxy left in the shell never sees local traffic (`ling-rs/src/proxy.rs`; `specs/DREAMFERENCE_MIGHTLING_EGRESS.md` §11). `ling-admin` does the same for itself and its children (`ProxyBypass`).

The web-access instructions are appended to the system prompt by the launcher (`WEB_ACCESS_INSTRUCTIONS` in `ling-rs/src/lib.rs`), so they apply in every workspace, not only this one.

## `CODEX_HOME` is `~/.mightling`, not `~/.codex`

`ling-rs/src/home.rs`, called first thing in Codex's `main()` by patch 0014, before `arg0` reads `.env` from the home folder. Upstream Codex keeps the vendor's sign-in in `~/.codex/auth.json`, and sharing the folder handed Mightling that login, and with it usage analytics, token refresh and the other channels to the vendor's backend. On first run the launcher copies an allow-list across (sessions, history, config, rules, skills, the session databases) and never `auth.json`, `installation_id`, `packages/` or the logs; the keyring is keyed by a hash of the home path, so it cannot leak the old login either. An explicit `CODEX_HOME` is respected. `CodexInstaller.home_dir()` mirrors the resolution for `ling-admin logs mcp`.

## Channels to the vendor, closed

- Patch 0013 disables the usage-analytics client where it is constructed, so no config or login can re-enable it.
- A traced session (strace on `connect`/`execve` plus reqwest debug logging) later showed more channels that need **no** login: OTEL metrics to Statsig at `ab.chatgpt.com` (Codex's default exporter in release builds, with an API key compiled in), `chatgpt.com/backend-api/plugins/featured`, a startup `git ls-remote https://github.com/openai/plugins.git`, and (TUI only) the upstream vendor's announcement tip from `raw.githubusercontent.com`. Patch 0015 closes each at its call site, and the launcher forces `chatgpt_base_url` to `http://127.0.0.1:9/backend-api/` so an undiscovered call to the vendor's backend fails locally.
- Patch 0016 switches off `ling doctor`'s sign-in, update and reachability checks (GitHub releases, the upstream vendor's desktop feed, a hard-coded `chatgpt.com` URL the redirect does not cover, `api.openai.com`) behind `MIGHTLING_DOCTOR_OFFLINE`; the code stays, and setting the constant to `false` restores them.

That trace is now `ling-admin audit egress` ([egress-audit.md](egress-audit.md)), and `ling-admin codex build` runs it (and its `--tui` form) after installing a new binary (`--no-audit` skips it; without a model server it says so and does not wait): after a Codex bump, read its verdict.

## Tests

The launcher's unit tests run with `cargo test --release -p ling-launcher` in the *export* directory under `~/.cache/dreamference/puffin-codex/`, never in `codex/`.
