# Skills from other agents

Developer notes behind the skills line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_SKILLS.md`, whose §15 records what was built and measured.

Skills from other agents are links, a preflight and a budget, with no Codex patch. Codex, Claude Code, Gemini CLI, OpenClaw and Hermes all use the same `SKILL.md`, and Codex's loader, unmodified, already reads every dialect. What `ling` adds is in:

- `ling-rs/skills/`: a crate of its own with no Codex or network dependency, so its tests run alone (`cargo test` in a copy of the folder);
- `ling-rs/src/skills.rs`: downloads, the air-gap check, `ling skill …`.

At every start the launcher plans which skills are offered and:

1. rebuilds `~/.mightling/skills/from-<agent>/<name>` links to the skills in `~/.claude/skills`, `~/.gemini/skills`, `~/.openclaw/skills` and `~/.hermes/skills`;
2. writes `[[skills.config]]` entries in `config.toml` for skills Codex finds by itself that must not be offered.

## Found the hard way

- A path entry must name the **canonical path of `SKILL.md`**, not the folder.
- The launcher's entries are each under a comment line of their own, because `configure_codex_home` rewrites the same file with toml_edit, which appends new tables *after* them, and Codex refuses unknown keys.
- The same `SKILL.md` reached by two routes is one skill to Codex, so a "loser" entry for it would switch off the winner.
- A session can write `~/.mightling/skills` (so it can plant a folder under `from-*`, which is why those are rebuilt and anything foreign is quarantined) but cannot write through a link, **unless the target is under `/tmp` or the workspace**, which the sandbox makes writable anyway.
- Claude Code keeps claude.ai's skills one level deeper than documented, `synced/<account>/<name>`.

## Glossary and budget

The tool glossary (`Bash`, `Read`, `terminal` … mapped to `ling`'s tools) is implemented and **off**: Qwen3.8 followed Claude-dialect skills correctly without it in every measured run. On a machine with Claude Code installed and claude.ai skills synced, the plan offers 27 skills at 81% of the 5,242-token catalogue budget, 13 of them Claude Code's.

## Phase 3 (§15.6)

- Adds `hermes/<category>/<name>` (through Hermes's repository tarball, 79 MB) and `clawhub/<owner>/<slug>` as `add` sources. ClawHub slugs are unique per owner only, and its verdict decides: malicious never, anything short of a clean scan only with a person at a terminal (`--yes` refused).
- Links a repository's `.claude/skills` and `.gemini/skills` as `from-repo-claude`/`from-repo-gemini`, but only when the repository is trusted in `$CODEX_HOME/config.toml` (the code index's rule), never from anything inside the repository.
