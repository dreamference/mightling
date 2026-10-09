//! `ling web`: the Mightling UI in a browser (specs/DREAMFERENCE_MIGHTLING_ASK.md §4).
//!
//! The server owns no agent of its own. Each tab opens a connection to the user's `ling
//! app-server` over its Unix socket, and everything the page sends passes the bridge policy
//! (`policy.json`, shared with the desktop app and held to the cases in `vectors/`). Every request
//! needs a credential, loopback included, and nothing a sandboxed command can read is one; the LAN
//! is served only on an advertised node. It calls
//! nothing on any network: its one outbound connection is the app-server's socket, and the only
//! processes it starts are `ling app-server` and `ling prompt show --composed`.

pub mod app_server;
pub mod ask;
pub mod ask_client;
pub mod assets;
pub mod auth;
pub mod cli;
pub mod devices;
pub mod policy;
pub mod prompts;
pub mod qr;
pub mod relay;
pub mod server;

pub use cli::Environment;
pub use cli::run_cli;
