# Mightling Codex Integration

> **Superseded by [`DREAMFERENCE_MIGHTLING_CODEX.md`](DREAMFERENCE_MIGHTLING_CODEX.md).** The agent is `ling`: a Mightling-branded build of Codex, compiled from the `codex/` submodule plus `codex-patches/`, with the session setup in the Rust launcher `ling-rs/`. This page only records what the two Python classes that remain still do, and one troubleshooting note.
>
> **Checked against the code:** 2026-10-28.

---

## 1. `CodexRunner` (`dreamference/runner/codex_runner.py`)

Used by `ling-admin run "…"` when the agent is `codex`, which is the default. It no longer sets anything up; `ling` does that itself:

1. `CodexInstaller.install_if_missing()` builds `ling` if the installed build is missing or stale.
2. It runs `ling [agent args…] ["PROMPT"]` with `subprocess.call`. The prompt is Codex's positional `PROMPT`; there is no `--message` option.
3. It sets `DREAMFERENCE_VLLM_HOST` to the configured host, `DREAMFERENCE_MIGHTLING_GMAIL` (`"true"` or `"false"` from `config.mightling_gmail`), and, when `compat` is given (`--compat`), `DREAMFERENCE_MIGHTLING_COMPAT` (`COMPAT_ENV`), so non-default settings reach the launcher.
4. With `--debug` it sets `RUST_LOG=codex_mcp=trace,codex_core=debug,codex_app_server=debug,info`, unless `RUST_LOG` is already set. The TUI logs to `logs_2.sqlite` in `$CODEX_HOME` (`~/.mightling` unless set, resolved by `CodexInstaller.home_dir()`), not the terminal.

Waiting for vLLM, the model catalog, `~/.mightling/config.toml`, the system prompt and the `--oss --local-provider openai-custom --model …` options are all the launcher's job; see the superseding spec, §4.

## 2. `CodexInstaller` (`dreamference/runner/codex_installer.py`)

- `get_codex_executable()` returns `CodexBrandedBuilder.executable_path()` (`~/.local/share/dreamference/mightling/bin/ling`) if it exists, else `None`. It **never** falls back to a `codex` on PATH: that would silently bring back the unbranded agent.
- `home_dir()` returns `$CODEX_HOME` if set, else `~/.mightling`.
- `is_installed()` is `CodexBrandedBuilder.is_current() and CodexBrandedBuilder.web_tools_are_current()`: `ling` and the web commands (`ling-search`, `ling-fetch`) installed, *and* built from the current submodule, patches, launcher source and profile settings.
- `install_if_missing()` returns `True` when `is_installed()`, and otherwise calls `CodexBrandedBuilder.build()`.

There is no `pip`/`npm`/install-script path any more.

## 3. Troubleshooting

### 3.1. `EngineDeadError` during shutdown

`vllm.v1.engine.exceptions.EngineDeadError` in the logs when a session ends, or when the vLLM server is stopped, is a benign artifact of the engine being killed with cleanup or requests still pending. It does not indicate a defect in `ling`, the vLLM server or the workspace.

---

## Links

- Upstream: https://github.com/openai/codex
- Fork: https://github.com/dreamference/codex (submodule `codex/`, pinned to `rust-v0.158.0`)
