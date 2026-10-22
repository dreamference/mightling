//! `ling sync …`: one set of threads on every device, served by the user's own node
//! (specs/DREAMFERENCE_MIGHTLING_SYNC.md). The agent and the commands are the `ling-sync` crate
//! (`sync/`), the hub a module of `ling web` (`web/src/sync_hub.rs`); this module only tells them
//! where things are, and how to find the node: by the node tiers, without asking its model server,
//! so a sync cycle connects to the node's `ling web` port and nothing else.

use std::path::PathBuf;
use std::sync::Arc;

use crate::node;
use crate::node::Resolution;

/// The node's host by the node tiers: a configured host, loopback on a node, the remembered node
/// (browsed for by its id, never kept as an address), or its overlay name.
fn resolve_node() -> Result<String, String> {
    match node::resolve(&node::inputs(), &node::MdnsBrowser) {
        Resolution::Host(host) => Ok(host),
        Resolution::Found { advert, .. } => Ok(advert.node.address),
        Resolution::LastAddress { node, .. } | Resolution::Overlay { node } => Ok(node.address),
        Resolution::Choose { .. } => Err("several nodes answer on this network: choose one with `ling node use <name>`".to_string()),
        Resolution::NoneFound => Err(node::none_found_message()),
        Resolution::Refused(message) => Err(message),
    }
}

/// `ling sync …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("Could not resolve CODEX_HOME.");
        return 1;
    };
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        eprintln!("HOME is not set.");
        return 1;
    };
    let ling = match std::env::current_exe() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("Could not find this program's path: {err}");
            return 1;
        }
    };
    let environment = ling_sync::Environment {
        codex_home,
        home,
        ling,
        is_node: node::inputs().is_node,
        resolve_node: Arc::new(resolve_node),
    };
    ling_sync::run_cli(args, environment).await
}
