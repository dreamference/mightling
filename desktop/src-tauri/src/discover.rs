// Where the window's web UI is (specs/DREAMFERENCE_PUFFIN_NODE.md §6.1, §7).
//
// On a node the window loads the local web UI, as it always has. On any other machine the web UI
// is the node's, and `forwarder.rs` brings it to `localhost`. This decides which, with the same
// rules as the `puffin` launcher: the node remembered in `node.json` is looked for by its id, a
// single node found is used, and with several and none remembered the app does not guess: it
// says how to choose (`puffin node use <name>`). The app reads `node.json` and never writes it;
// the launcher is the one that remembers a node.

use std::net::IpAddr;
use std::time::Duration;
use std::time::Instant;

use crate::forwarder::Upstream;
use crate::node_locator as locator;
use crate::node_locator::Node;

const BROWSE_TIMEOUT: Duration = Duration::from_secs(2);
const SETTLE: Duration = Duration::from_millis(400);

pub const NONE_FOUND: &str = "No Puffin node found on this network.\n\
Start one on a GB10: puffin-admin server start, then puffin-admin node enable.\n\
Or name one by its address, in a terminal: puffin node use <address>";

/// What the forwarder should do, or `None` on a node, where there is nothing to forward.
pub fn upstream() -> Option<Upstream> {
    if locator::is_node() {
        return None;
    }
    let remembered = locator::remembered();
    let wanted = remembered.as_ref().map(|node| node.node.clone()).filter(|id| !id.is_empty());
    // A node set by address and never seen in a browse has no id to look for.
    let found = if remembered.is_some() && wanted.is_none() { Vec::new() } else { browse(wanted.as_deref()) };
    let decision = decide(remembered, found);
    // No node anywhere, but a web UI answers on this machine: a node that has not written its id
    // yet (installed before the split). Keep working as before, as the launcher does for the model.
    if decision == Upstream::Message(NONE_FOUND.to_string()) && local_web_ui_answers() {
        return None;
    }
    Some(decision)
}

fn local_web_ui_answers() -> bool {
    let address = std::net::SocketAddr::from((std::net::Ipv4Addr::LOCALHOST, crate::forwarder::PREFERRED_PORT));
    std::net::TcpStream::connect_timeout(&address, Duration::from_millis(500)).is_ok()
}

/// The decision, with the browse's answers given.
pub fn decide(remembered: Option<Node>, found: Vec<Node>) -> Upstream {
    if let Some(remembered) = remembered {
        // An id is only what an advert claims. With two claimants the one at the remembered
        // address is the node; if neither is there, the remembered address is used rather than
        // a guess (security review 2026-10, as the launcher does).
        let same: Vec<&Node> =
            found.iter().filter(|node| !remembered.node.is_empty() && node.node == remembered.node).collect();
        let seen = same
            .iter()
            .find(|node| node.address == remembered.address)
            .copied()
            .or_else(|| if same.len() == 1 { same.first().copied() } else { None });
        return match seen {
            Some(node) => web_ui(node),
            // Nothing answered: its last address. Others answered but not this one: still the
            // remembered node; a different one is never adopted silently.
            None => web_ui(&remembered),
        };
    }
    match found.as_slice() {
        [] => Upstream::Message(NONE_FOUND.to_string()),
        [only] => web_ui(only),
        several => {
            let names: Vec<String> = several.iter().map(|node| format!("{} ({})", label(node), node.address)).collect();
            Upstream::Message(format!(
                "Several Puffin nodes are on this network: {}.\nChoose one in a terminal: puffin node use <name>",
                names.join(", ")
            ))
        }
    }
}

fn web_ui(node: &Node) -> Upstream {
    match node.web_port {
        Some(port) => Upstream::Node(format!("{}:{port}", locator::url_host(&node.address))),
        None => Upstream::Message(format!(
            "The Puffin node {} does not share its web UI.\nOn the node: puffin-admin node enable (without --no-web)",
            label(node)
        )),
    }
}

fn label(node: &Node) -> &str {
    if node.name.is_empty() { &node.address } else { &node.name }
}

/// Browses `_puffin-node._tcp`, as the launcher does (`puffin-rs/src/node.rs`).
fn browse(wanted: Option<&str>) -> Vec<Node> {
    let Ok(daemon) = mdns_sd::ServiceDaemon::new() else {
        return Vec::new();
    };
    let Ok(events) = daemon.browse(locator::SERVICE_TYPE) else {
        let _ = daemon.shutdown();
        return Vec::new();
    };
    let deadline = Instant::now() + BROWSE_TIMEOUT;
    let mut settle: Option<Instant> = None;
    let mut found: Vec<Node> = Vec::new();
    loop {
        let limit = settle.map_or(deadline, |settle| settle.min(deadline));
        let now = Instant::now();
        if now >= limit {
            break;
        }
        match events.recv_timeout(limit - now) {
            Ok(mdns_sd::ServiceEvent::ServiceResolved(service)) => {
                let addresses: Vec<IpAddr> = service.addresses.iter().map(mdns_sd::ScopedIp::to_ip_addr).collect();
                let Some(address) = best_address(&addresses) else { continue };
                let record = |key: &str| service.txt_properties.get_property_val_str(key).map(str::to_string);
                let port = |key: &str| record(key).and_then(|value| value.parse::<u16>().ok()).filter(|port| *port != 0);
                let node = Node {
                    node: record("node").unwrap_or_default(),
                    name: service
                        .fullname
                        .strip_suffix(locator::SERVICE_TYPE)
                        .unwrap_or(&service.fullname)
                        .trim_end_matches('.')
                        .to_string(),
                    address: address.to_string(),
                    model_port: service.port,
                    web_port: port("web"),
                    search_port: port("search"),
                    version: record("version").unwrap_or_default(),
                    last_seen: String::new(),
                };
                let is_wanted = wanted.is_some_and(|wanted| node.node == wanted);
                if !found.iter().any(|other| other.node == node.node && other.name == node.name) {
                    found.push(node);
                }
                if is_wanted {
                    break;
                }
                settle.get_or_insert(Instant::now() + SETTLE);
            }
            Ok(_) => {}
            Err(_) => break,
        }
    }
    let _ = daemon.stop_browse(locator::SERVICE_TYPE);
    let _ = daemon.shutdown();
    found
}

/// IPv4 first, then an IPv6 address that needs no scope; never loopback or link-local IPv6.
fn best_address(addresses: &[IpAddr]) -> Option<IpAddr> {
    let rank = |address: &IpAddr| match address {
        IpAddr::V4(v4) if v4.is_loopback() => None,
        IpAddr::V4(v4) if v4.is_link_local() => Some(1),
        IpAddr::V4(_) => Some(0),
        IpAddr::V6(v6) if v6.is_loopback() || (v6.segments()[0] & 0xffc0) == 0xfe80 => None,
        IpAddr::V6(_) => Some(3),
    };
    let mut usable: Vec<(u8, IpAddr)> = addresses.iter().filter_map(|address| Some((rank(address)?, *address))).collect();
    usable.sort();
    usable.first().map(|(_, address)| *address)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(name: &str, id: &str, address: &str) -> Node {
        Node {
            node: id.to_string(),
            name: name.to_string(),
            address: address.to_string(),
            model_port: 8000,
            web_port: Some(3000),
            ..Node::default()
        }
    }

    #[test]
    fn the_remembered_node_is_followed_to_its_new_address_and_never_swapped_for_another() {
        let remembered = node("spark-1", "1111", "192.168.50.105");
        let moved = node("spark-1", "1111", "192.168.50.200");
        assert_eq!(decide(Some(remembered.clone()), vec![moved]), Upstream::Node("192.168.50.200:3000".to_string()));
        assert_eq!(decide(Some(remembered.clone()), Vec::new()), Upstream::Node("192.168.50.105:3000".to_string()));
        let other = node("spark-2", "2222", "192.168.50.106");
        assert_eq!(decide(Some(remembered), vec![other]), Upstream::Node("192.168.50.105:3000".to_string()));
    }

    #[test]
    fn a_second_advert_claiming_the_remembered_id_is_not_followed() {
        let remembered = node("spark-1", "1111", "192.168.50.105");
        let impostor = node("spark-1", "1111", "192.168.50.66");
        let real = node("spark-1", "1111", "192.168.50.105");
        assert_eq!(decide(Some(remembered.clone()), vec![impostor.clone(), real]), Upstream::Node("192.168.50.105:3000".to_string()));
        let other = node("spark-1", "1111", "192.168.50.67");
        assert_eq!(decide(Some(remembered), vec![impostor, other]), Upstream::Node("192.168.50.105:3000".to_string()));
    }

    #[test]
    fn one_node_is_used_several_are_a_question_and_none_is_said() {
        let one = node("spark-1", "1111", "192.168.50.105");
        let two = node("spark-2", "2222", "fd00::6");
        assert_eq!(decide(None, vec![one.clone()]), Upstream::Node("192.168.50.105:3000".to_string()));
        assert_eq!(decide(None, vec![two.clone()]), Upstream::Node("[fd00::6]:3000".to_string()));
        let Upstream::Message(text) = decide(None, vec![one, two]) else { panic!("guessed a node") };
        assert!(text.contains("spark-1 (192.168.50.105), spark-2 (fd00::6)") && text.contains("puffin node use <name>"));
        assert_eq!(decide(None, Vec::new()), Upstream::Message(NONE_FOUND.to_string()));
    }

    #[test]
    fn a_node_that_keeps_its_web_ui_to_itself_is_said_not_forwarded_to() {
        let unshared = Node { web_port: None, ..node("spark-1", "1111", "192.168.50.105") };
        let Upstream::Message(text) = decide(None, vec![unshared]) else { panic!("forwarded to nothing") };
        assert!(text.contains("spark-1 does not share its web UI") && text.contains("puffin-admin node enable"));
    }
}
