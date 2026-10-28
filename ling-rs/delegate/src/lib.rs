//! `ling delegate`, the Mightling delegation plugin backend for Claude Code
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md`, Part A).
//!
//! Phase P1 enables the two model-free classes: W1 ("Waiting and monitoring", the `watch`
//! tool) and L1 ("Lookups with quotes", the `lookup` tool). Model-backed classes
//! (`ask`, `task`, `night`) remain disabled.
//!
//! `watch` uses no model: it blocks on a single condition from a closed list
//! (`file_line`, `pid_exit`, `http_status`, `gh_run`, `systemd_unit`,
//! `command`, `night_task`, `git_ref`, `timeout`) with a hard maximum duration
//! and bounded resources, returning once with `{met, observed, at}`.

pub mod classes;
pub mod cli;
pub mod lookup;
pub mod mcp;
pub mod watch;
