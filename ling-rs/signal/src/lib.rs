//! Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md).
//!
//! `ling-signal serve` is the bridge daemon the system unit runs as `mightling-signal`: it drives
//! signal-cli over JSON-RPC on stdio and is one more paired client of `ling web`, so a message from
//! the owner's phone becomes a turn in an Ask thread. The conversation's rules (`bridge`), the gate
//! (`gate`) and the formatting (`format`) are pure and tested without Signal or a network.

pub mod bridge;
pub mod command;
pub mod envelope;
pub mod format;
pub mod gate;
