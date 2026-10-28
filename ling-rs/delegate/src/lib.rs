//! `ling delegate`, the Mightling delegation plugin backend for Claude Code
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md`, Part A).
//!
//! Model-free: W1 ("Waiting and monitoring", `watch`) and L1 ("Lookups with quotes": `lookup`, and
//! the exact tools `run_test`, `git_log`, `diff_stat`). Model-backed: `task` for T1 to T5, on
//! probation with a ledger (`task.rs`); `ask` and `night` are not built.
//!
//! `watch` uses no model: it blocks on a single condition from a closed list
//! (`file_line`, `pid_exit`, `http_status`, `gh_run`, `systemd_unit`,
//! `command`, `night_task`, `git_ref`, `timeout`) with a hard maximum duration
//! and bounded resources, returning once with `{met, observed, at}`.

pub mod classes;
pub mod cli;
pub mod exec;
pub mod lookup;
pub mod mcp;
pub mod task;
pub mod tools;
pub mod watch;
