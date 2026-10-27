//! `ling sync`: one set of threads on every device, served by the user's own node
//! (specs/DREAMFERENCE_MIGHTLING_SYNC.md).
//!
//! The hub is a module of `ling web` on the node (`ling_web_server::sync_hub`); this crate is the
//! other half: the sync agent every enabled device runs, the node included, and the commands that
//! turn both on and off. The agent publishes the device's own interactive threads to the hub, by
//! appending what each rollout gained, and applies what other devices decided about them: a
//! deletion (`ling delete`) and an archive state (`ling archive`, `ling unarchive`), through Codex's
//! own commands. Without `[sync] replicate` (Phase 4) it writes nothing of another device's into
//! `~/.mightling`: other devices' threads are read from the hub.
//!
//! It is also a host (Phase 2, §18): attached to an app-server that listens on a socket here (the
//! TUI's control socket, `ling web`'s), it passes turns to the hub as they run and carries out what
//! other devices ask, through this machine's policy (`live.rs`).
//!
//! Off until `ling sync enable`, per device: before that no unit exists, no file is written and the
//! hub's routes answer 404. It talks to the node's `ling web` port and nothing else, on a private
//! or overlay address only, so `/airgapped on` changes nothing about it (§9).

pub mod agent;
pub mod cli;
pub mod client;
pub mod config;
pub mod live;
pub mod scan;

pub use cli::Environment;
pub use cli::run_cli;
