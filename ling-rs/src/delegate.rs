//! `ling delegate …`: the Claude Code delegation plugin server and CLI
//! (specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md Part A).
//! The implementation lives in the `ling-delegate` leaf crate (`delegate/src/cli.rs`).

/// `ling delegate …` from Claude Code's MCP stdio transport or a shell. Returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    ling_delegate::cli::run(args)
}
