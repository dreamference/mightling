//! `ling monitor …`: the session monitor (specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md Part B).
//! The commands are the `ling-monitor` crate's (`monitor/src/cli.rs`); this module only tells it how
//! its timer starts it again: as this `ling` with `monitor` in front of the command.

/// `ling monitor …` from a shell or the timer. Returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "ling".to_string());
    ling_monitor::cli::run(args, &[exe, "monitor".to_string()])
}
