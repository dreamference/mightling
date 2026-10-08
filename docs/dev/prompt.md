# Choosing a session's system prompt

Developer notes behind the system-prompt line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_PROMPT.md`, whose §13 records what was built.

A new session's system prompt is chosen by name (`ling-rs/src/prompt.rs`):

- `default` is Codex's template plus the web, email and code blocks, byte for byte what the launcher sent before (a test composes both);
- `high-swe` (`ling-rs/prompts/high-swe.md`) is a 4.4 KB method for repository tasks with the code block only;
- a file `$CODEX_HOME/system-prompts/<name>.md` is a custom one.

The choice is `DREAMFERENCE_MIGHTLING_PROMPT`, then `mightling_prompt`, then `default` (`ling prompt list|show|use`, `[night] prompt`, `swe-bench run --prompt`).

Codex fixes the prompt when a session starts and a resumed session keeps the one it recorded, so the launcher never overrides it: `model_catalog.json` always carries `default`, and another prompt gets `model_catalog.<name>.json`, named with `-c model_catalog_json=` for that process only (`model_instructions_file` would also replace the prompt of every session the launch resumes).

Switching the prompt of a running session (`/prompt` in the TUI) is Phase 2 and needs a patch; it is not built.
