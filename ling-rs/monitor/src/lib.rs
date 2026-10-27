//! The session monitor (specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md Part B, phase M0).
//!
//! Every five minutes a user timer runs `ling monitor check`: it reads what the agents' transcripts
//! gained since the last check (Claude Code's session and subagent files), keeps one row per agent,
//! runs the deterministic checks C1-C7 of §7.3, rewrites the summary the coordinator reads, and sends
//! the owner a few short alerts in fixed templates of facts. Nothing here calls a model, opens a
//! port or writes under an input path.

pub mod agents;
pub mod alerts;
pub mod checks;
pub mod cli;
pub mod config;
pub mod limits;
pub mod summary;
pub mod transcript;
pub mod units;
