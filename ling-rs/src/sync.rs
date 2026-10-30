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
    node_host(node::resolve(&node::inputs(), &node::MdnsBrowser))
}

/// The host where the tiers ended. A node's address is kept bare, so an IPv6 one is bracketed
/// here, as in a URL: the sync agent reads the host out of what it is given, and a bare IPv6
/// address would lose its last group to the port separator.
fn node_host(resolution: Resolution) -> Result<String, String> {
    match resolution {
        Resolution::Host(host) => Ok(host),
        Resolution::Found { advert, .. } => Ok(ling_node_locator::url_host(&advert.node.address)),
        Resolution::LastAddress { node, .. } | Resolution::Overlay { node } => Ok(ling_node_locator::url_host(&node.address)),
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

#[cfg(test)]
mod tests {
    use super::*;
    use ling_sync::cli::host_of;

    fn at(address: &str) -> node::Node {
        node::Node { address: address.to_string(), model_port: 8000, ..Default::default() }
    }

    #[test]
    fn every_tier_gives_the_sync_agent_a_host_it_reads_back_whole() {
        for (address, host) in [("2001:db8::5", "2001:db8::5"), ("192.168.50.105", "192.168.50.105"), ("spark-1.local", "spark-1.local")] {
            let found = Resolution::Found {
                advert: node::Advert { node: at(address), addresses: vec![address.to_string()], ..Default::default() },
                remember: false,
                note: None,
            };
            let last = Resolution::LastAddress { node: at(address), note: None };
            let overlay = Resolution::Overlay { node: at(address) };
            for resolution in [found, last, overlay] {
                assert_eq!(host_of(&node_host(resolution.clone()).unwrap()), host, "{resolution:?}");
            }
        }
        assert_eq!(host_of(&node_host(Resolution::Host("http://[::1]:8000".to_string())).unwrap()), "::1");
        assert_eq!(host_of(&node_host(Resolution::Host("http://10.0.0.2:8000/".to_string())).unwrap()), "10.0.0.2");
    }

    #[test]
    fn no_single_node_is_an_error() {
        assert!(node_host(Resolution::NoneFound).is_err());
        assert!(node_host(Resolution::Choose { adverts: Vec::new(), gone: None }).is_err());
        assert_eq!(node_host(Resolution::Refused("no".to_string())), Err("no".to_string()));
    }
}
