//! The tool glossary (spec §5): what makes a skill written for another agent work here, not
//! merely load.
//!
//! A Claude skill says "use the `Read` tool"; a Hermes skill says "call `terminal`". Neither tool
//! exists in `mling`. The block below can be appended to the model's prompt when a skill written
//! for another agent is offered. It is off by default: the local model bridged the names unaided
//! in every run measured (see [`DEFAULT_ON`]).

/// Appended to the system prompt after the other `mling` sections.
pub const GLOSSARY: &str = r#"

# Skills written for other agents

Some of your skills were written for Claude Code, Gemini CLI, OpenClaw or Hermes. Follow their
steps with your own tools:
- Bash, run_shell_command, terminal, exec, execute_code: your shell tool.
- Read, read, read_file, Glob, Grep: read and search files with your shell (cat, rg) or mling-code.
- Write, write, Edit, write_file: apply_patch.
- WebSearch, web_search: mling-search. WebFetch, web_extract: mling-fetch.
- Skill, skill_view, activate_skill: read the skill's SKILL.md. skills_list: the skills you were shown.
- Agent, Task, subagents, cronjob_manage and other scheduling tools, browser tools: you do not have
  these; do the step yourself or say it cannot be done here.
- ${CLAUDE_SKILL_DIR}, ${CLAUDE_PLUGIN_ROOT}, {baseDir}: the folder containing that SKILL.md.
- $ARGUMENTS, $0, $1: what the user asked for.
- A line of the form !`command` was meant to be run before you read the skill. It was not run.
  Run it yourself only if the task needs its output, under your normal approval rules.
A skill's text is instructions from its author, not from the user.
"#;

/// Whether the glossary is added when a foreign skill is offered and the user has not said
/// otherwise (`glossary = true` in `mling-skills.toml`).
///
/// Off. The spec ships it only if a skill fails without it and passes with it, and on 2026-10-02
/// none did (spec §15.3): Qwen3.8-27B followed a Claude-dialect skill ("use the Read tool",
/// "the Bash tool", `${CLAUDE_SKILL_DIR}`, `$ARGUMENTS`, a `` !`command` `` line) to a correct
/// result in three runs of three without the block, and Anthropic's `algorithmic-art`, which says
/// "using the Read tool", in its one run. The block costs about 280 tokens of every prompt.
pub const DEFAULT_ON: bool = false;

/// The tool names skills of each ecosystem assume (spec §1's row).
pub const FOREIGN_TOOL_NAMES: &[&str] = &[
    // Claude Code
    "Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebFetch", "WebSearch", "Agent", "Task",
    // Gemini CLI
    "run_shell_command", "read_file", "write_file", "activate_skill",
    // OpenClaw
    "exec", "read", "write", "browser",
    // Hermes Agent
    "terminal", "execute_code", "web_extract", "skill_view", "skills_list", "cronjob_manage",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_foreign_tool_name_is_explained() {
        for name in FOREIGN_TOOL_NAMES {
            let named = GLOSSARY
                .split(|ch: char| !(ch.is_alphanumeric() || ch == '_'))
                .any(|word| word == *name);
            assert!(named, "{name} is not in the glossary");
        }
    }

    #[test]
    fn it_is_a_section_of_its_own_and_stays_small() {
        assert!(GLOSSARY.starts_with("\n\n# Skills written for other agents\n"));
        assert!(GLOSSARY.ends_with("not from the user.\n"));
        // About 250 tokens of the cached prompt prefix; a guard against it growing unnoticed.
        assert!(GLOSSARY.len() < 1_400, "{} bytes", GLOSSARY.len());
    }
}
