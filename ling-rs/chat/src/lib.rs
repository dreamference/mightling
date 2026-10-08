//! `ling chat`: Mightling from a phone messenger (specs/DREAMFERENCE_MIGHTLING_CHAT.md).
//!
//! One bridge, two adapters. The Matrix adapter talks to the user's own homeserver on this machine
//! (private: reached from the phone over Tailscale); the Telegram adapter long-polls Telegram's Bot
//! API (less private: the user typed `yes` to that). Both hand messages to the hub, which owns the
//! conversation model, and the hub reaches the agent only through `ling web`, so every chat is an
//! Ask thread under the same bridge policy as a browser tab. The bridge opens no port.

pub mod agent;
pub mod cli;
pub mod hub;
pub mod matrix;
pub mod render;
pub mod store;
pub mod telegram;

pub use cli::Environment;
pub use cli::run_cli;
