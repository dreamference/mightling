//! puffin-code: fast, token-cheap and trustworthy answers about code, for the `puffin` agent.
//!
//! Two layers answer questions (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md):
//! - the **universal layer**, codebase-memory-mcp's graph, read directly from its SQLite file
//!   ([`graph`]), which covers every language approximately;
//! - the **exact layer**, SCIP indexes converted to SQLite by the scip CLI ([`scip_store`]).
//!
//! The [`router`] merges them, decides freshness for the whole repository ([`changed`]) and covers
//! every file changed since a snapshot with a whole-word text search ([`textscan`]), so an answer
//! is never silently incomplete. Indexing ([`index`]) runs only outside Codex's sandbox, admitted
//! against one host-wide memory budget and inside a network-less bwrap sandbox.

pub mod changed;
pub mod config;
pub mod graph;
pub mod index;
pub mod manifest;
pub mod mcp;
pub mod output;
pub mod paths;
pub mod prompt;
pub mod requests;
pub mod router;
pub mod scip_store;
pub mod scip_symbol;
pub mod session;
pub mod textscan;

/// The versions of the external tools this build of puffin-code knows the output of. The schema
/// fingerprints in [`graph`] and [`scip_store`] belong to these versions.
pub const PINNED_TOOLS: &[(&str, &str)] = &[
    ("codebase-memory-mcp", "0.11.0"),
    ("scip", "0.10.0"),
    ("scip-python", "0.6.6"),
    ("rust-analyzer", "1.95.0"),
];
