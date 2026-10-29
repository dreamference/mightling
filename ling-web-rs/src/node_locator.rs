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
//!
//! It also reads whether this machine's Wi-Fi saves power ([`wifi_power_save_hint`]), which loses
//! the multicast a browse depends on. Only the launcher calls that, after a browse found nothing;
//! the rest of this file runs nothing.

use std::net::SocketAddr;
use std::net::TcpStream;
use std::net::ToSocketAddrs;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

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
        self.search_port
            .map(|port| format!("http://{}:{port}", url_host(&self.address)))
    }

    /// The web UI's base URL, when the node shares it.
    pub fn web_url(&self) -> Option<String> {
        self.web_port
            .map(|port| format!("http://{}:{port}", url_host(&self.address)))
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
        let get = |key: &str| {
            fields
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value.as_str())
        };
        let port = |key: &str| {
            get(key)
                .and_then(|value| value.parse::<u16>().ok())
                .filter(|port| *port != 0)
        };
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
            && remote.starts_with(&format!(
                "{OVERLAY_PREFIX}{}.",
                self.node.to_ascii_lowercase()
            ))
            && remote
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
    }

    /// This node at its overlay name instead of its LAN address.
    pub fn via_overlay(&self) -> Node {
        Node {
            address: self.remote.clone(),
            ..self.clone()
        }
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
                'b' => out.push('\u{8}'),
                'f' => out.push('\u{c}'),
                'u' => {
                    // A character outside the BMP is a pair of escaped UTF-16 halves.
                    let high = hex4(chars)?;
                    let code = if (0xd800..0xdc00).contains(&high) {
                        if chars.next()? != '\\' || chars.next()? != 'u' {
                            return None;
                        }
                        let low = hex4(chars)?;
                        if !(0xdc00..0xe000).contains(&low) {
                            return None;
                        }
                        0x10000 + ((high - 0xd800) << 10) + (low - 0xdc00)
                    } else {
                        high
                    };
                    out.push(char::from_u32(code)?);
                }
                other => out.push(other),
            },
            c => out.push(c),
        }
    }
}

/// The four hex digits of a `\u` escape.
fn hex4(chars: &mut std::iter::Peekable<std::str::Chars<'_>>) -> Option<u32> {
    let code: String = (0..4).map(|_| chars.next()).collect::<Option<String>>()?;
    if !code.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    u32::from_str_radix(&code, 16).ok()
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
// One line on purpose: a test checks the path's source text.
#[rustfmt::skip]
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
    let pin = std::env::var(PIN_ENV).ok();
    remote_node_pinned(pin.as_deref(), is_node(), remembered())
        .map(|node| reachable(node, lan_answers, overlay_up))
}

/// One command's choice of node, by name, address or id (the launcher reads it too).
pub const PIN_ENV: &str = "MIGHTLING_NODE";

/// [`remote_node_from`] with `MIGHTLING_NODE` given. A pin beats loopback on a node and the
/// remembered node, as in the launcher. It names the remembered node (by name, address or id) or
/// else a host: a bare name is looked up as `<name>.local`, since this crate does not browse.
pub fn remote_node_pinned(
    pin: Option<&str>,
    is_node: bool,
    remembered: Option<Node>,
) -> Option<Node> {
    let Some(pin) = pin.map(str::trim).filter(|pin| !pin.is_empty()) else {
        return remote_node_from(is_node, remembered);
    };
    let wanted = pin.to_lowercase();
    if let Some(node) = remembered.filter(|node| {
        node.name.to_lowercase() == wanted
            || node.name.to_lowercase() == wanted.trim_end_matches(".local")
            || node.address.to_lowercase() == wanted
            || (wanted.len() >= 4
                && !node.node.is_empty()
                && node.node.to_lowercase().starts_with(&wanted))
    }) {
        return Some(node);
    }
    let bare = !pin.contains('.') && !pin.contains(':');
    let address = if bare {
        format!("{pin}.local")
    } else {
        pin.to_string()
    };
    Some(Node {
        name: pin.to_string(),
        address,
        model_port: DEFAULT_MODEL_PORT,
        search_port: Some(8888),
        ..Node::default()
    })
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
    // The key is the `"management"` a colon follows, and its value must be the object itself:
    // `"management": null` followed by another object must not lend that object's `connected`.
    let Some(rest) = status
        .match_indices("\"management\"")
        .find_map(|(start, key)| status[start + key.len()..].trim_start().strip_prefix(':'))
        .map(str::trim_start)
        .filter(|rest| rest.starts_with('{'))
    else {
        return false;
    };
    let Some(close) = rest.find('}') else {
        return false;
    };
    let object = &rest[..close];
    let Some(key) = object.find("\"connected\"") else {
        return false;
    };
    object[key + "\"connected\"".len()..]
        .trim_start()
        .trim_start_matches(':')
        .trim_start()
        .starts_with("true")
}

/// Whether the node's LAN address accepts a connection on the model port, within a moment.
fn lan_answers(node: &Node) -> bool {
    let addresses: Vec<SocketAddr> =
        match (node.address.as_str(), node.model_port).to_socket_addrs() {
            Ok(addresses) => addresses.collect(),
            Err(_) => return false,
        };
    addresses
        .iter()
        .any(|address| TcpStream::connect_timeout(address, LAN_PROBE).is_ok())
}

/// [`remote_node`] with its two inputs given.
pub fn remote_node_from(is_node: bool, remembered: Option<Node>) -> Option<Node> {
    if is_node { None } else { remembered }
}

// -- Wi-Fi power saving -----------------------------------------------------------------------------
//
// A Wi-Fi radio that saves power sleeps between beacons, and its access point holds multicast for
// it until the next DTIM beacon; on 2026-10-11/12 that lost up to ~80% of the mDNS queries between
// the machines here, so a client could not find its node. NetworkManager turns power saving on for
// every Wi-Fi connection by default. The node checks itself (`ling-admin host check`, `host setup`,
// `node enable`, `node prepare`: `dreamference/vllm_server/wifi_power_save.py`); a client that
// found no node by a browse reads the same two things here, the same way, and prints one hint with
// the commands. It never runs them: they need root.

/// How long one nmcli or iw reading may take. It runs only after a browse failed.
const WIFI_READ_TIMEOUT: Duration = Duration::from_secs(3);

/// NetworkManager's `802-11-wireless.powersave` values that mean off: the keyword `nmcli -g`
/// prints, or the number older versions print.
const POWERSAVE_OFF: [&str; 2] = ["disable", "2"];

/// One connected Wi-Fi interface, as nmcli and iw describe it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WifiInterface {
    pub device: String,
    pub connection: String,
    /// The connection's `802-11-wireless.powersave`, as nmcli prints it (`default`, `enable`, …).
    pub setting: String,
    /// Whether `iw` says the radio saves power now; `None` when iw is missing or does not say.
    pub radio: Option<bool>,
}

impl WifiInterface {
    /// Whether the connection's setting keeps power saving off.
    pub fn setting_off(&self) -> bool {
        let first = self
            .setting
            .split(' ')
            .next()
            .unwrap_or_default()
            .to_lowercase();
        POWERSAVE_OFF.contains(&first.as_str())
    }

    /// Whether power saving is on, or comes back at the next reconnect.
    pub fn saves_power(&self) -> bool {
        !self.setting_off() || self.radio == Some(true)
    }

    /// The commands that turn it off, to run as root: the connection's setting, kept across
    /// reconnects, and the radio, now and with no reconnect.
    pub fn fix_commands(&self) -> Vec<Vec<String>> {
        let mut commands = Vec::new();
        if !self.setting_off() {
            commands.push(
                [
                    "nmcli",
                    "connection",
                    "modify",
                    self.connection.as_str(),
                    "802-11-wireless.powersave",
                    "2",
                ]
                .map(String::from)
                .to_vec(),
            );
        }
        if self.radio == Some(true) {
            commands.push(
                [
                    "iw",
                    "dev",
                    self.device.as_str(),
                    "set",
                    "power_save",
                    "off",
                ]
                .map(String::from)
                .to_vec(),
            );
        }
        commands
    }
}

/// The connected Wi-Fi devices in `nmcli -t -f DEVICE,TYPE,STATE device status`.
pub fn connected_wifi_devices(listing: &str) -> Vec<String> {
    listing
        .lines()
        .filter_map(|line| {
            // Device names, types and states hold no colon, so the terse fields split cleanly.
            let fields: Vec<&str> = line.split(':').collect();
            (fields.len() >= 3
                && fields[1] == "wifi"
                && fields[2].starts_with("connected")
                && !fields[0].is_empty())
            .then(|| fields[0].to_string())
        })
        .collect()
}

/// What `iw dev <device> get power_save` says: `Some(true)` for `Power save: on`.
pub fn radio_power_save(output: &str) -> Option<bool> {
    let text = output.trim().to_lowercase();
    if text.ends_with(": on") {
        Some(true)
    } else if text.ends_with(": off") {
        Some(false)
    } else {
        None
    }
}

/// Reads every connected Wi-Fi interface through `read`, which runs one command and returns its
/// output, or `None` when the program is missing or fails.
pub fn wifi_interfaces_with(read: &dyn Fn(&[&str]) -> Option<String>) -> Vec<WifiInterface> {
    let Some(listing) = read(&["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
    else {
        return Vec::new();
    };
    let mut found = Vec::new();
    for device in connected_wifi_devices(&listing) {
        let Some(connection) = read(&[
            "nmcli",
            "-g",
            "GENERAL.CONNECTION",
            "device",
            "show",
            device.as_str(),
        ]) else {
            continue;
        };
        let connection = connection.trim().to_string();
        if connection.is_empty() {
            continue;
        }
        let setting = read(&[
            "nmcli",
            "-g",
            "802-11-wireless.powersave",
            "connection",
            "show",
            connection.as_str(),
        ])
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
        .unwrap_or_else(|| "unknown".to_string());
        let radio = read(&["iw", "dev", device.as_str(), "get", "power_save"])
            .and_then(|text| radio_power_save(&text));
        found.push(WifiInterface {
            device,
            connection,
            setting,
            radio,
        });
    }
    found
}

/// The one hint line for interfaces that save power, with the commands that turn it off; `None`
/// when none does.
pub fn wifi_hint_from(interfaces: &[WifiInterface]) -> Option<String> {
    let saving: Vec<&WifiInterface> = interfaces
        .iter()
        .filter(|interface| interface.saves_power())
        .collect();
    if saving.is_empty() {
        return None;
    }
    let named: Vec<String> = saving
        .iter()
        .map(|interface| format!("{} ({})", interface.device, interface.connection))
        .collect();
    let commands: Vec<String> = saving
        .iter()
        .flat_map(|interface| interface.fix_commands())
        .map(|command| {
            let words: Vec<String> = command.iter().map(|word| shell_quote(word)).collect();
            format!("sudo {}", words.join(" "))
        })
        .collect();
    Some(format!(
        "Hint: Wi-Fi power saving is on for {}, and it loses most of the multicast a node is found by. Turn it off: {}",
        named.join(", "),
        commands.join(" && ")
    ))
}

/// The hint for this machine, read with nmcli and iw: Linux with NetworkManager and a connected
/// Wi-Fi interface that saves power, or `None` (another system, no NetworkManager, Ethernet only,
/// power saving already off). It never fails and changes nothing.
pub fn wifi_power_save_hint() -> Option<String> {
    if !cfg!(target_os = "linux") {
        return None;
    }
    wifi_hint_from(&wifi_interfaces_with(&read_command))
}

/// A word as a shell reads it back: as it is when that is safe, in single quotes otherwise.
fn shell_quote(word: &str) -> String {
    let safe = !word.is_empty()
        && word.chars().all(|c| {
            c.is_ascii_alphanumeric()
                || matches!(c, '-' | '_' | '.' | '/' | ':' | ',' | '+' | '=' | '@')
        });
    if safe {
        word.to_string()
    } else {
        format!("'{}'", word.replace('\'', "'\\''"))
    }
}

/// Runs one reading in the C locale, with a deadline. `None` when the program is missing, fails or
/// takes too long.
fn read_command(argv: &[&str]) -> Option<String> {
    let (name, args) = argv.split_first()?;
    let program = find_program(name)?;
    let mut child = Command::new(program)
        .args(args)
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + WIFI_READ_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let mut output = String::new();
    std::io::Read::read_to_string(&mut child.stdout.take()?, &mut output).ok()?;
    status.success().then_some(output)
}

/// A program on `PATH`, or in `/usr/sbin` and `/sbin`, which not every user's `PATH` has (iw).
fn find_program(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH").unwrap_or_default();
    std::env::split_paths(&path)
        .chain([PathBuf::from("/usr/sbin"), PathBuf::from("/sbin")])
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node() -> Node {
        Node {
            node: "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001".to_string(),
            name: "gx10-1a2b".to_string(),
            address: "192.168.50.105".to_string(),
            model_port: 8000,
            web_port: Some(3000),
            search_port: Some(8888),
            version: "1.3.0".to_string(),
            last_seen: "2026-10-02T09:10:04+01:00".to_string(),
            remote: String::new(),
        }
    }

    fn enrolled() -> Node {
        Node {
            remote: "mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001.netbird.selfhosted".to_string(),
            ..node()
        }
    }

    #[test]
    fn a_pinned_node_beats_loopback_and_the_remembered_node() {
        // Unset or blank: as before.
        assert_eq!(remote_node_pinned(None, true, Some(node())), None);
        assert_eq!(
            remote_node_pinned(Some(" "), false, Some(node())),
            Some(node())
        );
        assert_eq!(remote_node_pinned(None, false, None), None);
        // Naming the remembered node, by name or id, gives it back whole, even on a node.
        assert_eq!(
            remote_node_pinned(Some("GX10-1a2b"), true, Some(node())),
            Some(node())
        );
        assert_eq!(
            remote_node_pinned(Some("7c1e0c7a"), false, Some(node())),
            Some(node())
        );
        // Another host: an address as it is, a bare name through mDNS; the search port is the default.
        let other = remote_node_pinned(Some("10.0.0.7"), false, Some(node())).unwrap();
        assert_eq!(
            (other.search_url().as_deref(), other.model_url().as_str()),
            (Some("http://10.0.0.7:8888"), "http://10.0.0.7:8000")
        );
        let bare = remote_node_pinned(Some("spark-9"), false, None).unwrap();
        assert_eq!(
            bare.search_url().as_deref(),
            Some("http://spark-9.local:8888")
        );
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
            assert!(
                !Node {
                    remote: remote.to_string(),
                    ..node()
                }
                .overlay_name_is_its_own(),
                "{remote}"
            );
        }
        assert!(
            !Node {
                node: String::new(),
                ..enrolled()
            }
            .overlay_name_is_its_own()
        );
    }

    #[test]
    fn a_silent_lan_address_falls_back_to_the_overlay_name_only_while_the_overlay_is_up() {
        assert_eq!(
            reachable(enrolled(), |_| true, || true).address,
            "192.168.50.105"
        );
        assert_eq!(
            reachable(enrolled(), |_| false, || true).model_url(),
            "http://mightling-7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001.netbird.selfhosted:8000"
        );
        assert_eq!(
            reachable(enrolled(), |_| false, || false).address,
            "192.168.50.105",
            "overlay down: the name is never resolved"
        );
        assert_eq!(
            reachable(node(), |_| false, || true).address,
            "192.168.50.105",
            "no overlay name, no fallback"
        );
    }

    #[test]
    fn management_connected_is_read_from_its_own_object() {
        let up = r#"{"peers": {"total": 2, "connected": 0}, "management": {"url": "https://b:443", "connected": true, "error": ""}}"#;
        assert!(management_connected(up));
        let down = r#"{"peers": {"connected": 2}, "management": {"url": "https://b:443", "connected": false}}"#;
        assert!(!management_connected(down));
        assert!(!management_connected(r#"{"daemonStatus": "NeedsLogin"}"#));
        assert!(!management_connected(""));
        // Pretty-printed, as `netbird status --json` prints it.
        assert!(management_connected(
            "{\n  \"management\": {\n    \"url\": \"https://b:443\",\n    \"connected\" :  true\n  }\n}"
        ));
        // A management that is not an object does not borrow another object's `connected`.
        for status in [
            r#"{"management": null, "signal": {"connected": true}}"#,
            r#"{"management": "down", "signal": {"connected": true}}"#,
            r#"{"management": {}, "signal": {"connected": true}}"#,
            r#"{"management": {"connected": "true"}}"#,
        ] {
            assert!(!management_connected(status), "{status}");
        }
        // The word as a value before the key is not the key.
        assert!(management_connected(
            r#"{"kind": "management", "management": {"connected": true}}"#
        ));
    }

    #[test]
    fn a_pin_names_the_remembered_node_only_by_its_whole_name_address_or_a_long_id_prefix() {
        for pin in ["gx10-1a2b.local", "  GX10-1A2B  ", "192.168.50.105", "7C1E"] {
            assert_eq!(
                remote_node_pinned(Some(pin), false, Some(node())),
                Some(node()),
                "{pin}"
            );
        }
        // Three characters of the id, or a part of the name, are another host.
        for (pin, address) in [("7c1", "7c1.local"), ("gx10", "gx10.local")] {
            let other = remote_node_pinned(Some(pin), false, Some(node())).unwrap();
            assert_eq!((other.address.as_str(), other.node.as_str()), (address, ""));
        }
        // A node with no id is never named by a prefix of nothing.
        let unnamed = Node {
            node: String::new(),
            ..node()
        };
        assert_eq!(
            remote_node_pinned(Some("abcd"), false, Some(unnamed)).map(|n| n.address),
            Some("abcd.local".to_string())
        );
        // An IPv6 pin is an address, bracketed in its URLs.
        let v6 = remote_node_pinned(Some("fd00::7"), false, None).unwrap();
        assert_eq!(v6.model_url(), "http://[fd00::7]:8000");
        assert_eq!(url_host("[fd00::7]"), "[fd00::7]");
    }

    #[test]
    fn the_file_reader_takes_escapes_and_line_ends_and_refuses_what_render_never_writes() {
        let parsed = Node::parse(
            "{\r\n  \"address\": \"10.0.0.2\",\r\n  \"name\": \"caf\\u00e9\",\r\n  \"model_port\": 70000,\r\n}\r\n",
        )
        .unwrap();
        assert_eq!(parsed.name, "café");
        assert_eq!(
            parsed.model_port, DEFAULT_MODEL_PORT,
            "a port out of range is the default"
        );
        assert_eq!(
            Node::parse("{\"address\": \"  10.0.0.3 \"}").map(|n| n.address),
            Some("10.0.0.3".to_string())
        );
        for text in [
            "{\"address\": \"a\", \"tags\": [\"x\"]}",
            "{\"address\": \"a\\u00\"}",
            "{\"address\": \"a\"",
            "{address: \"a\"}",
        ] {
            assert_eq!(Node::parse(text), None, "{text}");
        }
    }

    #[test]
    fn a_character_outside_the_bmp_escaped_as_two_halves_is_read() {
        // A JSON writer that escapes non-ASCII (Python's json.dump does by default) writes 😀 as
        // two UTF-16 halves; a node file set by hand that way was refused whole.
        let parsed = Node::parse("{\"address\": \"a\", \"name\": \"\\uD83D\\ude00 lab\"}").unwrap();
        assert_eq!(parsed.name, "😀 lab");
        for half in ["\\ud83d", "\\ud83d x", "\\ud83d\\u0041", "\\ude00"] {
            let text = format!("{{\"address\": \"a\", \"name\": \"{half}\"}}");
            assert_eq!(Node::parse(&text), None, "{text}");
        }
    }

    #[test]
    fn backspace_and_form_feed_escapes_are_the_characters_not_the_letters() {
        let parsed = Node::parse("{\"address\": \"a\", \"name\": \"x\\by\\fz\"}").unwrap();
        assert_eq!(parsed.name, "x\u{8}y\u{c}z");
    }

    #[test]
    fn a_missing_or_unreadable_node_file_remembers_nothing() {
        let dir = std::env::temp_dir().join(format!("ling-node-file-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(NODE_FILE);
        assert_eq!(remembered_at(&path), None);
        std::fs::write(&path, "not json").unwrap();
        assert_eq!(remembered_at(&path), None);
        std::fs::write(&path, node().render()).unwrap();
        assert_eq!(remembered_at(&path), Some(node()));
        assert_eq!(remembered_at(&dir), None, "a folder is not the file");
        let _ = std::fs::remove_dir_all(&dir);
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
        let unshared = Node {
            web_port: None,
            search_port: None,
            ..node()
        };
        let text = unshared.render();
        assert!(!text.contains("web_port") && !text.contains("search_port"));
        assert_eq!(Node::parse(&text), Some(unshared.clone()));
        assert_eq!(unshared.search_url(), None);
        assert_eq!(unshared.web_url(), None);
        assert_eq!(
            Node::parse("{\"address\": \"10.0.0.2\", \"search_port\": 0, \"web_port\": null}")
                .and_then(|n| n.search_port),
            None
        );
    }

    #[test]
    fn urls_use_the_address_and_bracket_ipv6() {
        assert_eq!(node().model_url(), "http://192.168.50.105:8000");
        assert_eq!(
            node().search_url().as_deref(),
            Some("http://192.168.50.105:8888")
        );
        assert_eq!(
            node().web_url().as_deref(),
            Some("http://192.168.50.105:3000")
        );
        let v6 = Node {
            address: "fe80::1%wlan0".to_string(),
            ..node()
        };
        assert_eq!(v6.model_url(), "http://[fe80::1%wlan0]:8000");
        let named = Node {
            address: "spark.lan".to_string(),
            ..node()
        };
        assert_eq!(named.model_url(), "http://spark.lan:8000");
    }

    #[test]
    fn a_file_set_by_hand_needs_only_an_address() {
        let parsed = Node::parse("{ \"address\" : \"10.0.0.2\" }");
        assert_eq!(
            parsed.as_ref().map(|n| n.model_port),
            Some(DEFAULT_MODEL_PORT)
        );
        assert_eq!(parsed.as_ref().map(|n| n.node.as_str()), Some(""));
    }

    #[test]
    fn anything_that_is_not_the_file_is_refused() {
        for text in [
            "",
            "[]",
            "{",
            "{\"address\": \"\"}",
            "{\"name\": \"x\"}",
            "{\"address\": {\"a\": 1}}",
            "{\"address\": \"a\"} x",
        ] {
            assert_eq!(Node::parse(text), None, "{text}");
        }
    }

    #[test]
    fn strings_with_quotes_and_escapes_survive() {
        let odd = Node {
            name: "a \"b\" \\ c\n".to_string(),
            ..node()
        };
        assert_eq!(Node::parse(&odd.render()), Some(odd));
    }

    #[test]
    fn a_node_never_uses_a_remembered_node_and_a_client_without_one_stays_on_loopback() {
        assert_eq!(remote_node_from(true, Some(node())), None);
        assert_eq!(remote_node_from(false, None), None);
        assert_eq!(remote_node_from(false, Some(node())), Some(node()));
    }

    const WIFI_ONLY: &str = "wlP9s9:wifi:connected\np2p-dev-wlP9s9:wifi-p2p:disconnected\nenP7s7:ethernet:unavailable\nlo:loopback:connected (externally)\n";
    const ETHERNET_ONLY: &str =
        "enP7s7:ethernet:connected\nwlP9s9:wifi:disconnected\nlo:loopback:connected (externally)\n";

    /// A machine's nmcli and iw, answered from strings: (listing, connection, setting, radio).
    /// `None` for a program that is missing or fails.
    fn machine(
        listing: Option<&'static str>,
        connection: &'static str,
        setting: &'static str,
        radio: Option<&'static str>,
    ) -> impl Fn(&[&str]) -> Option<String> {
        move |argv: &[&str]| -> Option<String> {
            match argv {
                ["nmcli", "-t", ..] => listing.map(String::from),
                [
                    "nmcli",
                    "-g",
                    "GENERAL.CONNECTION",
                    "device",
                    "show",
                    "wlP9s9",
                ] => Some(format!("{connection}\n")),
                [
                    "nmcli",
                    "-g",
                    "802-11-wireless.powersave",
                    "connection",
                    "show",
                    name,
                ] if *name == connection => Some(format!("{setting}\n")),
                ["iw", "dev", "wlP9s9", "get", "power_save"] => {
                    radio.map(|state| format!("Power save: {state}\n"))
                }
                _ => panic!("unexpected reading {argv:?}"),
            }
        }
    }

    #[test]
    fn only_connected_wifi_devices_are_read() {
        assert_eq!(
            connected_wifi_devices(WIFI_ONLY),
            vec!["wlP9s9".to_string()]
        );
        assert!(connected_wifi_devices(ETHERNET_ONLY).is_empty());
        assert!(connected_wifi_devices("").is_empty());
        assert_eq!(
            connected_wifi_devices("wlan0:wifi:connected (externally)\n"),
            vec!["wlan0".to_string()]
        );
    }

    #[test]
    fn the_radio_says_on_off_or_nothing() {
        assert_eq!(radio_power_save("Power save: on\n"), Some(true));
        assert_eq!(radio_power_save("Power save: off"), Some(false));
        assert_eq!(
            radio_power_save("command failed: Operation not supported (-95)"),
            None
        );
        assert_eq!(radio_power_save(""), None);
    }

    #[test]
    fn wifi_saving_power_gets_one_hint_with_both_commands() {
        for setting in ["default", "enable", "3", "unknown"] {
            let found = wifi_interfaces_with(&machine(
                Some(WIFI_ONLY),
                "TP-Link_DA74",
                setting,
                Some("on"),
            ));
            assert_eq!(found.len(), 1, "{setting}");
            let hint = wifi_hint_from(&found).expect("a hint");
            assert!(!hint.contains('\n'), "one line: {hint}");
            assert!(
                hint.starts_with("Hint: Wi-Fi power saving is on for wlP9s9 (TP-Link_DA74)"),
                "{hint}"
            );
            assert!(hint.ends_with(
                "Turn it off: sudo nmcli connection modify TP-Link_DA74 802-11-wireless.powersave 2 \
                 && sudo iw dev wlP9s9 set power_save off"
            ), "{hint}");
        }
    }

    #[test]
    fn power_saving_off_for_good_and_now_gets_no_hint() {
        for setting in ["disable", "2", "2 (disable)"] {
            let found =
                wifi_interfaces_with(&machine(Some(WIFI_ONLY), "home", setting, Some("off")));
            assert_eq!(wifi_hint_from(&found), None, "{setting}");
        }
        // iw missing or silent: the setting decides.
        let found = wifi_interfaces_with(&machine(Some(WIFI_ONLY), "home", "disable", None));
        assert_eq!(found[0].radio, None);
        assert_eq!(wifi_hint_from(&found), None);
    }

    #[test]
    fn each_half_of_the_fix_is_named_only_when_it_is_needed() {
        // Set, not yet reconnected: the radio still sleeps.
        let set = wifi_hint_from(&wifi_interfaces_with(&machine(
            Some(WIFI_ONLY),
            "home",
            "disable",
            Some("on"),
        )))
        .unwrap();
        assert!(
            set.ends_with("Turn it off: sudo iw dev wlP9s9 set power_save off"),
            "{set}"
        );
        // Awake now, but it comes back at the next reconnect; iw cannot say.
        let unset = wifi_hint_from(&wifi_interfaces_with(&machine(
            Some(WIFI_ONLY),
            "home",
            "default",
            None,
        )))
        .unwrap();
        assert!(
            unset.ends_with(
                "Turn it off: sudo nmcli connection modify home 802-11-wireless.powersave 2"
            ),
            "{unset}"
        );
    }

    #[test]
    fn a_connection_name_is_quoted_for_the_shell() {
        let hint = wifi_hint_from(&wifi_interfaces_with(&machine(
            Some(WIFI_ONLY),
            "Bob's Wi-Fi",
            "default",
            Some("off"),
        )))
        .unwrap();
        assert!(hint.contains("on for wlP9s9 (Bob's Wi-Fi)"), "{hint}");
        assert!(
            hint.ends_with(
                "sudo nmcli connection modify 'Bob'\\''s Wi-Fi' 802-11-wireless.powersave 2"
            ),
            "{hint}"
        );
        assert_eq!(shell_quote("TP-Link_DA74"), "TP-Link_DA74");
        assert_eq!(shell_quote(""), "''");
        assert_eq!(shell_quote("a b"), "'a b'");
        assert_eq!(shell_quote("$(x)"), "'$(x)'");
    }

    #[test]
    fn no_networkmanager_or_no_wifi_says_nothing() {
        assert!(wifi_interfaces_with(&machine(None, "home", "default", Some("on"))).is_empty());
        assert!(
            wifi_interfaces_with(&machine(Some(ETHERNET_ONLY), "home", "default", Some("on")))
                .is_empty()
        );
        // A Wi-Fi device with no connection named is skipped, not guessed at.
        assert!(
            wifi_interfaces_with(&machine(Some(WIFI_ONLY), "", "default", Some("on"))).is_empty()
        );
        assert_eq!(wifi_hint_from(&[]), None);
    }

    #[test]
    fn a_missing_program_is_none_and_never_an_error() {
        assert_eq!(read_command(&["ling-no-such-program-for-a-test"]), None);
        assert_eq!(read_command(&[]), None);
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
