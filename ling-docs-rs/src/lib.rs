//! ling-docs: the local file index (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md).
//!
//! The user's own folders ("collections") are extracted, chunked and indexed on this machine and
//! searched by keyword and by meaning, with every answer citing its file and page or lines. Two
//! halves, as in the code index:
//! - **queries** ([`search`], [`read`], [`mcp`], `status`) only read the databases, so they run
//!   inside the agent's sandbox and open nothing but local files;
//! - **indexing** ([`index`]) runs only outside it, started by [`session`]: discovery
//!   ([`discover`]) reads metadata and opens nothing it will not index, extraction ([`extract`])
//!   runs in a network-less bwrap sandbox, and every run is admitted against the host-wide memory
//!   budget the code index uses ([`host`]).
//!
//! No component opens a socket: the embedding model and the PDF engine are local libraries, and
//! the model is installed by `ling-admin docs setup`, never fetched from here.

pub mod chunk;
pub mod collections;
pub mod config;
pub mod discover;
pub mod embed;
pub mod extract;
pub mod host;
pub mod index;
pub mod mcp;
pub mod prompt;
pub mod read;
pub mod requests;
pub mod sandbox;
pub mod search;
pub mod session;
pub mod store;
pub mod untrusted;
