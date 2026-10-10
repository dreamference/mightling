//! Where the Mightling node is (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.1, §6.2).
//!
//! A machine is either a node (it has `~/.config/dreamference/node-id`, written the first time it
//! loads a model) or a client. A node's programs talk to its own services on loopback. A client's
//! programs read `$CODEX_HOME/node.json`, which the launcher writes after finding a node on the
//! network, so only the launcher ever browses and everything else works inside a sandbox.
//!
//! A client enrolled for remote access (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §5) also
//! remembers the node's overlay name, `remote`: a name, never an address. A program that finds the
//! node's LAN address silent uses that name instead, if NetBird's client reports its management
//! connected: with the overlay down the name is never handed to the system's resolver.
//!
//! Standard library only: this file is compiled into the launcher and the web commands.

use std::net::SocketAddr;
use std::net::TcpStream;
use std::net::ToSocketAddrs;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;

/// The DNS-SD service type a node is advertised under.
pub const SERVICE_TYPE: &str = "_mightling-node._tcp.local.";

/// The version of the advertised contract this code understands. A node advertising a higher
/// `proto` is refused.
pub const PROTO: u32 = 1;

/// The file under `$CODEX_HOME` that remembers the node.
pub const NODE_FILE: &str = "node.json";

/// The model server's port when an address is given without one.
pub const DEFAULT_MODEL_PORT: u16 = 8000;

/// The prefix of a node's peer name in the overlay: `mightling-<node id>`.
pub const OVERLAY_PREFIX: &str = "mightling-";

/// How long the LAN address may take to accept a connection before the overlay name is tried.
const LAN_PROBE: Duration = Duration::from_millis(300);

/// A node as a client remembers it.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct Node {
    /// The node's stable id. Empty for a node set by address that never answered a browse.
    pub node: String,
    pub name: String,
    /// An IP address, or a host name the user gave to `ling node use`.
    pub address: String,
    pub model_port: u16,
    pub web_port: Option<u16>,
    pub search_port: Option<u16>,
    pub version: String,
    pub last_seen: String,
    /// The node's overlay name (`mightling-<id>.<domain>`), learnt from its advert or at
    /// enrolment; empty without remote access.
    pub remote: String,
}

impl Node {
    /// The model server's base URL, without `/v1`.
    pub fn model_url(&self) -> String {
        format!("http://{}:{}", url_host(&self.address), self.model_port)
    }

    /// SearXNG's base URL, when the node shares it.
    pub fn search_url(&self) -> Option<String> {
        self.search_port.map(|port| format!("http://{}:{port}", url_host(&self.address)))
    }

    /// The web UI's base URL, when the node shares it.
    pub fn web_url(&self) -> Option<String> {
        self.web_port.map(|port| format!("http://{}:{port}", url_host(&self.address)))
    }

    /// The file's text: one flat JSON object, written whole.
    pub fn render(&self) -> String {
        let mut fields = vec![
            format!("  \"node\": {}", quote(&self.node)),
            format!("  \"name\": {}", quote(&self.name)),
            format!("  \"address\": {}", quote(&self.address)),
            format!("  \"model_port\": {}", self.model_port),
        ];
        if let Some(port) = self.web_port {
            fields.push(format!("  \"web_port\": {port}"));
        }
        if let Some(port) = self.search_port {
            fields.push(format!("  \"search_port\": {port}"));
        }
        fields.push(format!("  \"version\": {}", quote(&self.version)));
        fields.push(format!("  \"last_seen\": {}", quote(&self.last_seen)));
        if !self.remote.is_empty() {
            fields.push(format!("  \"remote\": {}", quote(&self.remote)));
        }
        format!("{{\n{}\n}}\n", fields.join(",\n"))
    }

    /// Reads the file's text. `None` unless it is an object with an address.
    pub fn parse(text: &str) -> Option<Node> {
        let fields = flat_object(text)?;
        let get = |key: &str| fields.iter().find(|(name, _)| name == key).map(|(_, value)| value.as_str());
        let port = |key: &str| get(key).and_then(|value| value.parse::<u16>().ok()).filter(|port| *port != 0);
        let address = get("address")?.trim().to_string();
        if address.is_empty() {
            return None;
        }
        Some(Node {
            node: get("node").unwrap_or_default().to_string(),
            name: get("name").unwrap_or_default().to_string(),
            address,
            model_port: port("model_port").unwrap_or(DEFAULT_MODEL_PORT),
            web_port: port("web_port"),
            search_port: port("search_port"),
            version: get("version").unwrap_or_default().to_string(),
            last_seen: get("last_seen").unwrap_or_default().to_string(),
            remote: get("remote").unwrap_or_default().to_string(),
        })
    }

    /// Whether `remote` is this node's own overlay name: `mightling-<its id>.` and a domain. The
    /// name carries the id, so a client that resolves it has found this node and no other.
    pub fn overlay_name_is_its_own(&self) -> bool {
        let remote = self.remote.to_ascii_lowercase();
        !self.node.is_empty()
            && remote.len() > OVERLAY_PREFIX.len() + self.node.len() + 1
            && remote.starts_with(&format!("{OVERLAY_PREFIX}{}.", self.node.to_ascii_lowercase()))
            && remote.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
    }

    /// This node at its overlay name instead of its LAN address.
    pub fn via_overlay(&self) -> Node {
        Node { address: self.remote.clone(), ..self.clone() }
    }
}

/// An address as it goes into a URL: an IPv6 address in brackets, anything else as it is.
pub fn url_host(address: &str) -> String {
    if address.contains(':') && !address.starts_with('[') {
        format!("[{address}]")
    } else {
        address.to_string()
    }
}

fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// The members of one flat JSON object whose values are strings, numbers, booleans or null, as
/// `(key, value text)`. Anything nested, or anything that is not such an object, is `None`: the
/// file is written by `Node::render` and nothing else is expected in it.
fn flat_object(text: &str) -> Option<Vec<(String, String)>> {
    let mut chars = text.trim().chars().peekable();
    if chars.next()? != '{' {
        return None;
    }
    let mut fields = Vec::new();
    loop {
        skip_space(&mut chars);
        match chars.peek()? {
            '}' => {
                chars.next();
                break;
            }
            ',' => {
                chars.next();
                continue;
            }
            '"' => {}
            _ => return None,
        }
        let key = string(&mut chars)?;
        skip_space(&mut chars);
        if chars.next()? != ':' {
            return None;
        }
        skip_space(&mut chars);
        let value = if chars.peek()? == &'"' {
            string(&mut chars)?
        } else {
            let mut bare = String::new();
            while let Some(c) = chars.peek() {
                if matches!(c, ',' | '}') || c.is_whitespace() {
                    break;
                }
                if matches!(c, '{' | '[') {
                    return None;
                }
                bare.push(*c);
                chars.next();
            }
            if bare == "null" { String::new() } else { bare }
        };
        fields.push((key, value));
    }
    skip_space(&mut chars);
    chars.next().is_none().then_some(fields)
}

fn skip_space(chars: &mut std::iter::Peekable<std::str::Chars<'_>>) {
    while chars.peek().is_some_and(|c| c.is_whitespace()) {
        chars.next();
    }
}

fn string(chars: &mut std::iter::Peekable<std::str::Chars<'_>>) -> Option<String> {
    if chars.next()? != '"' {
        return None;
    }
    let mut out = String::new();
    loop {
        match chars.next()? {
            '"' => return Some(out),
            '\\' => match chars.next()? {
                'n' => out.push('\n'),
                'r' => out.push('\r'),
                't' => out.push('\t'),
                'u' => {
                    let code: String = (0..4).map(|_| chars.next()).collect::<Option<String>>()?;
                    out.push(char::from_u32(u32::from_str_radix(&code, 16).ok()?)?);
                }
                other => out.push(other),
            },
            c => out.push(c),
        }
    }
}

/// The user's home folder: `HOME`, or `USERPROFILE` on Windows.
pub fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .filter(|home| !home.is_empty())
        .or_else(|| std::env::var_os("USERPROFILE").filter(|home| !home.is_empty()))
        .map(PathBuf::from)
}

/// `$CODEX_HOME`, or `~/.mightling` when a shell-environment policy stripped the variable.
pub fn codex_home() -> Option<PathBuf> {
    match std::env::var_os("CODEX_HOME") {
        Some(home) if !home.is_empty() => Some(PathBuf::from(home)),
        _ => Some(home_dir()?.join(".mightling")),
    }
}

/// `$CODEX_HOME/node.json`.
pub fn node_file() -> Option<PathBuf> {
    Some(codex_home()?.join(NODE_FILE))
}

/// The file that marks a machine as a node, and holds its id.
pub fn node_id_file() -> Option<PathBuf> {
    Some(home_dir()?.join(".config").join("dreamference").join("node-id"))
}

/// Whether this machine is a node: it then uses its own services on loopback and never browses.
pub fn is_node() -> bool {
    node_id_file().is_some_and(|path| is_node_at(&path))
}

/// [`is_node`] for a given id file: it must hold something, not merely exist.
pub fn is_node_at(path: &Path) -> bool {
    std::fs::read_to_string(path).is_ok_and(|text| !text.trim().is_empty())
}

/// The remembered node, if this client has one.
pub fn remembered() -> Option<Node> {
    remembered_at(&node_file()?)
}

/// [`remembered`] for a given file.
pub fn remembered_at(path: &Path) -> Option<Node> {
    Node::parse(&std::fs::read_to_string(path).ok()?)
}

/// What a program that is not the launcher should use for a service that defaults to loopback:
/// `None` on a node, or on a client that remembers no node (loopback, as before the split), and
/// the remembered node otherwise: at its LAN address, or at its overlay name when the LAN
/// address does not accept a connection, the node has one and the overlay is up.
pub fn remote_node() -> Option<Node> {
    remote_node_from(is_node(), remembered()).map(|node| reachable(node, lan_answers, overlay_up))
}

/// [`remote_node`]'s choice of address, with the LAN probe and NetBird's state given.
pub fn reachable(node: Node, lan_answers: fn(&Node) -> bool, overlay_up: fn() -> bool) -> Node {
    if !node.overlay_name_is_its_own() || lan_answers(&node) || !overlay_up() {
        node
    } else {
        node.via_overlay()
    }
}

/// Whether NetBird's client reports its management connected (`netbird status --json`; the
/// daemon's socket is open to local users). False when NetBird is absent or says otherwise.
pub fn overlay_up() -> bool {
    Command::new("netbird")
        .args(["status", "--json"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|output| output.status.success())
        .is_some_and(|output| management_connected(&String::from_utf8_lossy(&output.stdout)))
}

/// Reads `"management": {..., "connected": true, ...}` out of `netbird status --json` without a
/// JSON library: the management object holds no nested object, and the peers' own `connected`
/// (a count) is outside it.
pub fn management_connected(status: &str) -> bool {
    let Some(start) = status.find("\"management\"") else { return false };
    let rest = &status[start..];
    let (Some(open), Some(close)) = (rest.find('{'), rest.find('}')) else { return false };
    if close < open {
        return false;
    }
    let object = &rest[open..close];
    let Some(key) = object.find("\"connected\"") else { return false };
    object[key + "\"connected\"".len()..].trim_start().trim_start_matches(':').trim_start().starts_with("true")
}

/// Whether the node's LAN address accepts a connection on the model port, within a moment.
fn lan_answers(node: &Node) -> bool {
    let addresses: Vec<SocketAddr> = match (node.address.as_str(), node.model_port).to_socket_addrs() {
        Ok(addresses) => addresses.collect(),
        Err(_) => return false,
    };
    addresses.iter().any(|address| TcpStream::connect_timeout(address, LAN_PROBE).is_ok())
}

/// [`remote_node`] with its two inputs given.
pub fn remote_node_from(is_node: bool, remembered: Option<Node>) -> Option<Node> {
    if is_node { None } else { remembered }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node() -> Node {
        Node {
            node: "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001".to_string(),
            name: "gx10-9428".to_string(),
            address: "192.168.0.105".to_string(),
            model_port: 8000,
            web_port: Some(3000),
            search_port: Some(8888),
            version: "1.3.0".to_string(),
            last_seen: "2026-10-02T09:10:04+01:00".to_string(),
            remote: String::new(),
        }
    }

    fn enrolled() -> Node {
        Node { remote: "mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001.netbird.selfhosted".to_string(), ..node() }
    }

    #[test]
    fn the_overlay_name_is_remembered_and_absent_without_remote_access() {
        assert!(!node().render().contains("remote"));
        let text = enrolled().render();
        assert!(text.contains("\"remote\": \"mightling-7c1e0c7a-"));
        assert_eq!(Node::parse(&text), Some(enrolled()));
    }

    #[test]
    fn only_the_nodes_own_overlay_name_is_trusted() {
        assert!(enrolled().overlay_name_is_its_own());
        assert!(!node().overlay_name_is_its_own());
        for remote in [
            "mightling-0000.netbird.selfhosted",
            "mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001",
            "evil.example/mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001.x",
            "laptop.netbird.selfhosted",
        ] {
            assert!(!Node { remote: remote.to_string(), ..node() }.overlay_name_is_its_own(), "{remote}");
        }
        assert!(!Node { node: String::new(), ..enrolled() }.overlay_name_is_its_own());
    }

    #[test]
    fn a_silent_lan_address_falls_back_to_the_overlay_name_only_while_the_overlay_is_up() {
        assert_eq!(reachable(enrolled(), |_| true, || true).address, "192.168.0.105");
        assert_eq!(
            reachable(enrolled(), |_| false, || true).model_url(),
            "http://mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001.netbird.selfhosted:8000"
        );
        assert_eq!(reachable(enrolled(), |_| false, || false).address, "192.168.0.105", "overlay down: the name is never resolved");
        assert_eq!(reachable(node(), |_| false, || true).address, "192.168.0.105", "no overlay name, no fallback");
    }

    #[test]
    fn management_connected_is_read_from_its_own_object() {
        let up = r#"{"peers": {"total": 2, "connected": 0}, "management": {"url": "https://b:443", "connected": true, "error": ""}}"#;
        assert!(management_connected(up));
        let down = r#"{"peers": {"connected": 2}, "management": {"url": "https://b:443", "connected": false}}"#;
        assert!(!management_connected(down));
        assert!(!management_connected(r#"{"daemonStatus": "NeedsLogin"}"#));
        assert!(!management_connected(""));
    }

    #[test]
    fn the_file_round_trips() {
        let text = node().render();
        assert!(text.starts_with("{\n  \"node\": \"7c1e0c7a-"));
        assert!(text.ends_with("\"last_seen\": \"2026-10-02T09:10:04+01:00\"\n}\n"));
        assert_eq!(Node::parse(&text), Some(node()));
    }

    #[test]
    fn what_a_node_does_not_share_is_absent_not_zero() {
        let unshared = Node { web_port: None, search_port: None, ..node() };
        let text = unshared.render();
        assert!(!text.contains("web_port") && !text.contains("search_port"));
        assert_eq!(Node::parse(&text), Some(unshared.clone()));
        assert_eq!(unshared.search_url(), None);
        assert_eq!(unshared.web_url(), None);
        assert_eq!(Node::parse("{\"address\": \"10.0.0.2\", \"search_port\": 0, \"web_port\": null}").and_then(|n| n.search_port), None);
    }

    #[test]
    fn urls_use_the_address_and_bracket_ipv6() {
        assert_eq!(node().model_url(), "http://192.168.0.105:8000");
        assert_eq!(node().search_url().as_deref(), Some("http://192.168.0.105:8888"));
        assert_eq!(node().web_url().as_deref(), Some("http://192.168.0.105:3000"));
        let v6 = Node { address: "fe80::1%wlan0".to_string(), ..node() };
        assert_eq!(v6.model_url(), "http://[fe80::1%wlan0]:8000");
        let named = Node { address: "spark.lan".to_string(), ..node() };
        assert_eq!(named.model_url(), "http://spark.lan:8000");
    }

    #[test]
    fn a_file_set_by_hand_needs_only_an_address() {
        let parsed = Node::parse("{ \"address\" : \"10.0.0.2\" }");
        assert_eq!(parsed.as_ref().map(|n| n.model_port), Some(DEFAULT_MODEL_PORT));
        assert_eq!(parsed.as_ref().map(|n| n.node.as_str()), Some(""));
    }

    #[test]
    fn anything_that_is_not_the_file_is_refused() {
        for text in ["", "[]", "{", "{\"address\": \"\"}", "{\"name\": \"x\"}", "{\"address\": {\"a\": 1}}", "{\"address\": \"a\"} x"] {
            assert_eq!(Node::parse(text), None, "{text}");
        }
    }

    #[test]
    fn strings_with_quotes_and_escapes_survive() {
        let odd = Node { name: "a \"b\" \\ c\n".to_string(), ..node() };
        assert_eq!(Node::parse(&odd.render()), Some(odd));
    }

    #[test]
    fn a_node_never_uses_a_remembered_node_and_a_client_without_one_stays_on_loopback() {
        assert_eq!(remote_node_from(true, Some(node())), None);
        assert_eq!(remote_node_from(false, None), None);
        assert_eq!(remote_node_from(false, Some(node())), Some(node()));
    }

    #[test]
    fn an_empty_id_file_does_not_make_a_node() {
        let dir = std::env::temp_dir().join(format!("ling-node-locator-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("node-id");
        assert!(!is_node_at(&path));
        let _ = std::fs::write(&path, "\n");
        assert!(!is_node_at(&path));
        let _ = std::fs::write(&path, "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001\n");
        assert!(is_node_at(&path));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
