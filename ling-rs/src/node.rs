//! Finding the Mightling node (specs/DREAMFERENCE_MIGHTLING_NODE.md §5.3, §6).
//!
//! Before the client/server split the launcher talked to `localhost:8000` unless told otherwise.
//! It now resolves where the model server is through tiers, the first that yields a host winning:
//!
//! 1. `DREAMFERENCE_VLLM_HOST`, then `vllm_host` in a configuration file: "I know where it is";
//! 2. `MIGHTLING_NODE=<name>`: one node, for one command;
//! 3. this machine is a node (it has `~/.config/dreamference/node-id`): loopback, and no browse;
//! 3a. the remembered node's overlay name (`mightling-<id>.<domain>`, REMOTE_ACCESS §5), when
//!    NetBird's client says it is connected to management and the name resolves: no browse, and
//!    it works off the LAN. The name carries the id, so it can only be that node;
//! 4. the node remembered in `$CODEX_HOME/node.json`, found again on the network **by its id**, so
//!    an address change costs nothing; its last address is tried only when a browse returns
//!    nothing at all;
//! 5. a browse of `_mightling-node._tcp`: one node is used and remembered, several are a question.
//!
//! A node other than the remembered one is never adopted silently: prompts and source code go to
//! whichever node is chosen. `ling node list|use|forget` is the same logic from a shell.
//!
//! Only this module browses. `ling-search` and the others read `node.json` through the
//! `ling-node-locator` crate, so they need no multicast and work inside the sandbox.

use std::io::IsTerminal;
use std::io::Write;
use std::net::IpAddr;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::Instant;

use anyhow::bail;
pub use ling_node_locator::Node;
use ling_node_locator as locator;

/// How long a browse may take when nothing answers.
const BROWSE_TIMEOUT: Duration = Duration::from_secs(2);

/// How long to keep listening after the first answer, for a second node or a better address.
const SETTLE: Duration = Duration::from_millis(400);

/// One command's choice of node, by name, address or id.
pub const PIN_ENV: &str = "MIGHTLING_NODE";

const USAGE: &str = "Usage: ling node [list] | ling node use <name|address> | ling node forget\n       \
                     ling node remote join --code <8 digits>   (remote access, on the node's LAN)";

/// What a node advertises: where it is, and the records that are not kept in `node.json`.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct Advert {
    pub node: Node,
    /// Every usable address the node answered on, best first; `node.address` is the first, or
    /// the remembered one when the node still answers there.
    pub addresses: Vec<String>,
    /// The advertised contract version; a higher one than [`locator::PROTO`] is refused.
    pub proto: u32,
    /// `stopped`, `loading` or `ready`; empty when the node does not say.
    pub state: String,
    /// Whether the node's model is one a coding client can use.
    pub main: bool,
}

/// Something that can browse the local network for nodes. The real one is [`MdnsBrowser`]; the
/// tests use a list.
pub trait Browser {
    /// Returns the nodes that answered. With `wanted` set (the remembered node) it may stop as
    /// soon as that node answers on its remembered address.
    fn browse(&self, wanted: Option<&Node>) -> Vec<Advert>;
}

/// What the tiers are decided from.
#[derive(Clone, Debug, Default)]
pub struct Inputs {
    /// `DREAMFERENCE_VLLM_HOST`, then `vllm_host` in a configuration file.
    pub configured: Option<String>,
    /// `MIGHTLING_NODE`.
    pub pinned: Option<String>,
    pub is_node: bool,
    pub remembered: Option<Node>,
    /// Whether the remembered node's overlay name answers: NetBird's client is connected and the
    /// name resolves. Checked only when the node has one.
    pub overlay: bool,
}

/// Where the tiers ended.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Resolution {
    /// A host given by configuration, or loopback on a node. Nothing was browsed.
    Host(String),
    /// A node that answered a browse.
    Found { advert: Advert, remember: bool, note: Option<String> },
    /// The remembered node, by its last address, because no browse reached it.
    LastAddress { node: Node, note: Option<String> },
    /// The remembered node at its overlay name (remote access). Nothing was browsed.
    Overlay { node: Node },
    /// Several nodes and no way to choose without asking; `gone` names a remembered node that
    /// did not answer while others did.
    Choose { adverts: Vec<Advert>, gone: Option<String> },
    /// No node answered and none is remembered.
    NoneFound,
    /// A node was named that cannot be used; the text says why.
    Refused(String),
}

/// Runs the tiers.
pub fn resolve(inputs: &Inputs, browser: &dyn Browser) -> Resolution {
    if let Some(host) = inputs.configured.as_deref().filter(|host| !host.trim().is_empty()) {
        return Resolution::Host(host.to_string());
    }
    if let Some(name) = inputs.pinned.as_deref().filter(|name| !name.trim().is_empty()) {
        let found = browser.browse(None);
        return match found.iter().find(|advert| matches(advert, name)) {
            Some(advert) => checked(advert.clone(), false, None),
            None => Resolution::Refused(format!(
                "{PIN_ENV}={name}: no such node on this network.{}",
                listing(&found, None)
            )),
        };
    }
    if inputs.is_node {
        return Resolution::Host(crate::DEFAULT_VLLM_HOST.to_string());
    }
    if let Some(remembered) = inputs.remembered.as_ref().filter(|node| inputs.overlay && node.overlay_name_is_its_own()) {
        return Resolution::Overlay { node: remembered.via_overlay() };
    }
    if let Some(remembered) = &inputs.remembered {
        // Set by address and never seen in a browse: there is no id to look for.
        if remembered.node.is_empty() {
            return Resolution::LastAddress { node: remembered.clone(), note: None };
        }
        let found = browser.browse(Some(remembered));
        // An id is only what an advert says, and anything on the network can say it. Two adverts
        // with the remembered id are therefore a question, unless one of them is at the address
        // the node was remembered at (security review 2026-10).
        let same_id: Vec<&Advert> = found.iter().filter(|advert| advert.node.node == remembered.node).collect();
        let at_remembered = same_id
            .iter()
            .find(|advert| advert.addresses.iter().any(|address| *address == remembered.address))
            .copied();
        if same_id.len() > 1 && at_remembered.is_none() {
            return Resolution::Choose { adverts: found, gone: Some(label(remembered).to_string()) };
        }
        if let Some(advert) = at_remembered.or_else(|| same_id.first().copied()) {
            // A node with several addresses (two interfaces, or seen from its own machine) keeps
            // the one that is remembered for as long as it answers there: the address changes,
            // and the line that says so appears, only when the node has really moved.
            let mut advert = advert.clone();
            if advert.addresses.iter().any(|address| *address == remembered.address) {
                advert.node.address = remembered.address.clone();
            }
            let note = (advert.node.address != remembered.address).then(|| {
                format!("Node {} is now at {}.", label(&advert.node), advert.node.address)
            });
            return checked(advert, true, note);
        }
        if found.is_empty() {
            return Resolution::LastAddress {
                node: remembered.clone(),
                note: Some(format!(
                    "Node {} did not answer a browse of this network; trying its last address, {}.",
                    label(remembered),
                    remembered.address
                )),
            };
        }
        return Resolution::Choose { adverts: found, gone: Some(label(remembered).to_string()) };
    }
    let found = browser.browse(None);
    let chosen = match found.as_slice() {
        [] => return Resolution::NoneFound,
        [only] => only,
        several => {
            // Two Sparks, one with a coding model and one with speech or embeddings, need no
            // answer from anyone; two that both serve a coding model need one, once.
            let mut main = several.iter().filter(|advert| advert.main);
            match (main.next(), main.next()) {
                (Some(only), None) => only,
                _ => return Resolution::Choose { adverts: found, gone: None },
            }
        }
    };
    let note = format!(
        "Using Mightling node {} at {} (remembered; `ling node list` shows every node).",
        label(&chosen.node),
        chosen.node.address
    );
    checked(chosen.clone(), true, Some(note))
}

/// Refuses a node that speaks a newer contract than this client knows.
fn checked(advert: Advert, remember: bool, note: Option<String>) -> Resolution {
    if advert.proto > locator::PROTO {
        return Resolution::Refused(format!(
            "Node {} speaks a newer protocol ({}) than this ling ({}). Update this machine: ling update",
            label(&advert.node),
            advert.proto,
            locator::PROTO
        ));
    }
    Resolution::Found { advert, remember, note }
}

/// Whether `name` names this node: its name, its address or its id (or a prefix of the id of at
/// least four characters), ignoring case.
pub fn matches(advert: &Advert, name: &str) -> bool {
    let name = name.trim().to_lowercase();
    let node = &advert.node;
    node.name.to_lowercase() == name
        || node.name.to_lowercase() == name.trim_end_matches(".local")
        || node.address.to_lowercase() == name
        || (name.len() >= 4 && !node.node.is_empty() && node.node.to_lowercase().starts_with(&name))
}

fn label(node: &Node) -> &str {
    if node.name.is_empty() { &node.address } else { &node.name }
}

/// The nodes as lines, for a message. Empty when there are none.
fn listing(adverts: &[Advert], in_use: Option<&Node>) -> String {
    let mut text = String::new();
    for advert in adverts {
        let node = &advert.node;
        let used = in_use.is_some_and(|used| !used.node.is_empty() && used.node == node.node);
        text.push_str(&format!(
            "\n  {:<16} {:<15} {}{}{}",
            label(node),
            node.address,
            if advert.state.is_empty() { "?" } else { &advert.state },
            if advert.main { "" } else { "  (not a coding model)" },
            if used { "  (in use)" } else { "" },
        ));
    }
    text
}

/// §6.4's message for a browse that found nothing.
pub fn none_found_message() -> String {
    "No Mightling node found on this network.\n\
     - On a GB10: ling-admin server start, then ling-admin node enable\n\
     - A node still running Puffin 1.4 is not found: run `puffin update` there once\n\
     - Or name one by its address: ling node use <address>"
        .to_string()
}

/// What a node's advertised state and its model port's answer mean together (§6.4). `Ok` carries
/// a line to print before waiting, if any; `Err` is a message and no wait.
pub fn state_verdict(node: &Node, state: &str, answers: bool) -> Result<Option<String>, String> {
    if answers {
        return Ok(None);
    }
    match state {
        "stopped" => Err(format!(
            "Node {} found, but its model server is not running. On the node: ling-admin server start",
            label(node)
        )),
        "loading" => Ok(Some(format!("Node {}: model loading", label(node)))),
        // `ready` and refusing: the server stopped without `server stop`, or it is loading again
        // after a restart of the machine, which the advert cannot know.
        "ready" => Err(format!(
            "Node {} is advertised as ready, but its model server at {} does not answer. \
             On the node: ling-admin status (it may still be loading after a restart)",
            label(node),
            node.model_url()
        )),
        // Not seen in a browse (a remembered address) or an advert without a state: wait, as
        // before the split.
        _ => Ok(None),
    }
}

/// Reads the tiers' inputs from the environment and the files.
pub fn inputs() -> Inputs {
    let configured = crate::configured_vllm_host();
    let is_node = locator::is_node();
    let remembered = locator::remembered();
    // Asked only when it can decide something: no configured host, not a node, an overlay name.
    let overlay = configured.is_none()
        && !is_node
        && remembered.as_ref().is_some_and(|node| node.overlay_name_is_its_own() && overlay_answers(&node.remote));
    Inputs { configured, pinned: std::env::var(PIN_ENV).ok(), is_node, remembered, overlay }
}

/// Whether NetBird's client is connected to its management and `name` resolves. NetBird is asked
/// first so that, with the overlay down, the name is never sent to the system's DNS servers.
fn overlay_answers(name: &str) -> bool {
    let output = std::process::Command::new("netbird")
        .args(["status", "--json"])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output();
    let connected = output
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| serde_json::from_slice::<serde_json::Value>(&output.stdout).ok())
        .is_some_and(|status| netbird_connected(&status));
    connected && std::net::ToSocketAddrs::to_socket_addrs(&(name, locator::DEFAULT_MODEL_PORT)).is_ok_and(|mut found| found.next().is_some())
}

/// Reads `netbird status --json`: whether management is connected.
pub fn netbird_connected(status: &serde_json::Value) -> bool {
    status.pointer("/management/connected").and_then(serde_json::Value::as_bool).unwrap_or(false)
}

/// Resolves the model server's base URL for a command that needs the model, printing what the
/// user should know and remembering the node. `interactive` says whether a question may be asked.
pub async fn resolve_host(interactive: bool) -> anyhow::Result<String> {
    let inputs = inputs();
    let (node, state, remember, note) = match resolve(&inputs, &MdnsBrowser) {
        Resolution::Host(host) => return Ok(host),
        Resolution::Refused(message) => bail!("{message}"),
        Resolution::NoneFound => {
            // A machine with a model server on loopback that has not written its node id yet
            // (installed before the split): keep working, as before.
            if answers(crate::DEFAULT_VLLM_HOST).await {
                return Ok(crate::DEFAULT_VLLM_HOST.to_string());
            }
            bail!("{}", none_found_message());
        }
        Resolution::Choose { adverts, gone } => {
            let chosen = choose(&adverts, gone.as_deref(), interactive)?;
            (chosen.node, chosen.state, true, None)
        }
        Resolution::Found { advert, remember, note } => (advert.node, advert.state, remember, note),
        Resolution::LastAddress { node, note } => (node, String::new(), false, note),
        Resolution::Overlay { node } => {
            let note = format!("Node {} over the overlay ({}).", label(&node), node.address);
            (node, String::new(), false, Some(note))
        }
    };
    if let Some(note) = note {
        eprintln!("{note}");
    }
    if remember {
        remember_node(&node);
    }
    version_notice(&node);
    let host = node.model_url();
    match state_verdict(&node, &state, answers(&host).await) {
        Ok(Some(line)) => eprintln!("{line}"),
        Ok(None) => {}
        Err(message) => bail!("{message}"),
    }
    Ok(host)
}

/// Asks which of several nodes to use, or refuses with the list where no question can be asked.
fn choose(adverts: &[Advert], gone: Option<&str>, interactive: bool) -> anyhow::Result<Advert> {
    let heading = match gone {
        Some(name) => format!("The remembered node, {name}, is not on this network, and another is:"),
        None => "Several Mightling nodes are on this network:".to_string(),
    };
    if !interactive || !std::io::stdin().is_terminal() {
        bail!(
            "{heading}{}\nChoose one with: ling node use <name>   (or {PIN_ENV}=<name> for one command)",
            listing(adverts, None)
        );
    }
    eprintln!("{heading}");
    for (index, advert) in adverts.iter().enumerate() {
        eprintln!("  {}. {} at {}", index + 1, label(&advert.node), advert.node.address);
    }
    loop {
        eprint!("Use which? [1-{}] ", adverts.len());
        let _ = std::io::stderr().flush();
        let mut line = String::new();
        if std::io::stdin().read_line(&mut line).unwrap_or(0) == 0 {
            bail!("no node chosen");
        }
        if let Some(advert) = line.trim().parse::<usize>().ok().and_then(|n| adverts.get(n.wrapping_sub(1))) {
            return match checked(advert.clone(), true, None) {
                Resolution::Refused(message) => Err(anyhow::anyhow!(message)),
                _ => Ok(advert.clone()),
            };
        }
    }
}

async fn answers(host: &str) -> bool {
    let Ok(client) = crate::proxy::direct_client(Duration::from_secs(2)) else {
        return false;
    };
    crate::served_model(&client, host).await.is_some()
}

/// Writes `node.json`, unless it already says the same apart from when the node was last seen
/// today: the file is rewritten when something changed, not on every start.
fn remember_node(node: &Node) {
    let Some(path) = locator::node_file() else { return };
    let now = chrono::Local::now();
    let mut node = node.clone();
    node.last_seen = now.to_rfc3339_opts(chrono::SecondsFormat::Secs, false);
    if let Some(existing) = locator::remembered_at(&path) {
        let same_day = existing.last_seen.get(..10) == node.last_seen.get(..10);
        if same_day && (Node { last_seen: String::new(), ..existing }) == (Node { last_seen: String::new(), ..node.clone() }) {
            return;
        }
    }
    if let Err(error) = write_node_file(&path, &node) {
        eprintln!("Could not remember the node in {}: {error}", path.display());
    }
}

/// Remembers a node after `ling node remote join`, with its overlay name.
pub fn remember_enrolled(node: &Node) -> Result<(), String> {
    let path = locator::node_file().ok_or("could not resolve CODEX_HOME")?;
    write_node_file(&path, node).map_err(|error| format!("could not write {}: {error}", path.display()))
}

fn write_node_file(path: &Path, node: &Node) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let staging = path.with_file_name(format!(".{}.{}.tmp", locator::NODE_FILE, std::process::id()));
    std::fs::write(&staging, node.render())?;
    std::fs::rename(&staging, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&staging);
    })
}

/// One line, at most once a day, when the node runs another Mightling version. Nothing is blocked:
/// the model API is the contract, and it does not move with Mightling's version.
fn version_notice(node: &Node) {
    let Some(mine) = crate::update::MIGHTLING_VERSION else { return };
    let Some(home) = locator::codex_home() else { return };
    let today = chrono::Local::now().format("%Y-%m-%d").to_string();
    let marker = home.join("node-version-notice");
    if let Some(line) = version_notice_line(mine, node, std::fs::read_to_string(&marker).ok().as_deref(), &today) {
        eprintln!("{line}");
        let _ = std::fs::write(&marker, &today);
    }
}

fn version_notice_line(mine: &str, node: &Node, last_notice: Option<&str>, today: &str) -> Option<String> {
    if node.version.is_empty() || node.version == mine || last_notice.map(str::trim) == Some(today) {
        return None;
    }
    Some(format!(
        "Note: node {} runs Mightling {}, this machine {mine}. `ling update` here, or update the node.",
        label(node),
        node.version
    ))
}

// -- `ling node` ----------------------------------------------------------------------------------

/// What a `ling node …` command line asks for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    List,
    Use(String),
    Forget,
    /// `ling node remote …` (remote_join.rs).
    Remote(Vec<String>),
    Usage,
}

impl Request {
    pub fn from_args(args: &[String]) -> Request {
        match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
            [] | ["list"] => Request::List,
            ["use", name] => Request::Use(name.to_string()),
            ["forget"] => Request::Forget,
            ["remote", ..] => Request::Remote(args[1..].to_vec()),
            _ => Request::Usage,
        }
    }
}

/// Runs `ling node …` and returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(path) = locator::node_file() else {
        eprintln!("ling node: could not resolve CODEX_HOME");
        return 1;
    };
    match Request::from_args(args) {
        Request::Remote(rest) => crate::remote_join::run_cli(&rest).await,
        Request::Usage => {
            println!("{USAGE}");
            2
        }
        Request::Forget => {
            for line in forget(&path) {
                println!("{line}");
            }
            0
        }
        Request::List => {
            let adverts = MdnsBrowser.browse(None);
            let mut models = Vec::new();
            for advert in &adverts {
                models.push(served(&advert.node.model_url()).await);
            }
            for line in list_lines(&inputs(), &adverts, &models) {
                println!("{line}");
            }
            0
        }
        Request::Use(name) => {
            let adverts = MdnsBrowser.browse(None);
            let node = match pick(&adverts, &name) {
                Ok(node) => node,
                Err(message) => {
                    eprintln!("{message}");
                    return 1;
                }
            };
            let reachable = answers(&node.model_url()).await;
            let mut remembered = node.clone();
            remembered.last_seen = chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false);
            if let Err(error) = write_node_file(&path, &remembered) {
                eprintln!("Could not write {}: {error}", path.display());
                return 1;
            }
            println!("Using node {} at {}.", label(&node), node.model_url());
            if !reachable {
                println!("Its model server does not answer right now; ling will wait for it.");
            }
            if locator::is_node() {
                println!("Note: this machine is a node, so ling uses its own model server; set {PIN_ENV} to use another for one command.");
            }
            0
        }
    }
}

async fn served(host: &str) -> Option<crate::ServedModel> {
    let client = crate::proxy::direct_client(Duration::from_secs(2)).ok()?;
    crate::served_model(&client, host).await
}

/// The node `name` names among those that answered; or, when none does and `name` is an address
/// (`host` or `host:port`), a node at that address with nothing else known about it, so that a
/// network without multicast still works.
pub fn pick(adverts: &[Advert], name: &str) -> Result<Node, String> {
    if let Some(advert) = adverts.iter().find(|advert| matches(advert, name)) {
        return match checked(advert.clone(), true, None) {
            Resolution::Refused(message) => Err(message),
            _ => Ok(advert.node.clone()),
        };
    }
    let name = name.trim().trim_start_matches("http://").trim_end_matches('/');
    let (address, port) = match name.rsplit_once(':') {
        // `host:port`, but not a bare IPv6 address, whose colons are not a port separator.
        Some((host, port)) if !host.contains(':') => match port.parse::<u16>() {
            Ok(port) => (host, port),
            Err(_) => return Err(format!("{name}: not a node name or an address{}", listing(adverts, None))),
        },
        _ => (name, locator::DEFAULT_MODEL_PORT),
    };
    let looks_like_address = address.parse::<IpAddr>().is_ok() || address.contains('.');
    if address.is_empty() || !looks_like_address {
        return Err(format!(
            "No node named {name} on this network.{}\nAn address works without discovery: ling node use <address>",
            listing(adverts, None)
        ));
    }
    Ok(Node { address: address.to_string(), model_port: port, ..Node::default() })
}

fn forget(path: &Path) -> Vec<String> {
    match std::fs::remove_file(path) {
        Ok(()) => vec!["Forgotten. The next start looks for a node on the network again.".to_string()],
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => vec!["No node was remembered.".to_string()],
        Err(error) => vec![format!("Could not remove {}: {error}", path.display())],
    }
}

/// The lines `ling node list` prints: every node that answered, with what it serves, which one
/// this machine uses, and the URL to paste into another tool.
pub fn list_lines(inputs: &Inputs, adverts: &[Advert], models: &[Option<crate::ServedModel>]) -> Vec<String> {
    let mut lines = Vec::new();
    if let Some(host) = &inputs.configured {
        lines.push(format!("This machine is configured to use {host} (DREAMFERENCE_VLLM_HOST or vllm_host)."));
    } else if inputs.is_node {
        lines.push(format!("This machine is a node: ling uses its own model server, {}.", crate::DEFAULT_VLLM_HOST));
    }
    let in_use = inputs.remembered.as_ref().filter(|_| inputs.configured.is_none() && !inputs.is_node);
    if adverts.is_empty() {
        lines.push("No Mightling node answered on this network.".to_string());
    }
    for (index, advert) in adverts.iter().enumerate() {
        let node = &advert.node;
        let serving = match models.get(index).and_then(Option::as_ref) {
            Some(model) => format!("{} ({} tokens)", model.id, model.max_model_len),
            None if advert.state.is_empty() => "not answering".to_string(),
            None => advert.state.clone(),
        };
        let used = in_use.is_some_and(|used| {
            (!used.node.is_empty() && used.node == node.node) || (used.node.is_empty() && used.address == node.address)
        });
        lines.push(format!(
            "{}{}  {}/v1  {}  Mightling {}{}",
            if used { "* " } else { "  " },
            label(node),
            node.model_url(),
            serving,
            if node.version.is_empty() { "?" } else { &node.version },
            if advert.main { "" } else { "  (not a coding model)" },
        ));
        if !node.remote.is_empty() {
            lines.push(format!("      off the LAN: {} (remote access)", node.remote));
        }
    }
    if let Some(used) = in_use {
        let seen = adverts.iter().any(|advert| {
            (!used.node.is_empty() && advert.node.node == used.node) || advert.node.address == used.address
        });
        if !seen && inputs.overlay && used.overlay_name_is_its_own() {
            lines.push(format!("* {}  {}/v1  over the overlay (remote access)", label(used), used.via_overlay().model_url()));
        } else if !seen {
            lines.push(format!(
                "* {}  {}/v1  remembered, did not answer this browse",
                label(used),
                used.model_url()
            ));
        }
    } else if inputs.configured.is_none() && !inputs.is_node && !adverts.is_empty() {
        lines.push("None is remembered yet: the next `ling` uses the one it finds, or asks.".to_string());
    }
    lines
}

// -- the browse -------------------------------------------------------------------------------------

/// Browses with the pure-Rust `mdns-sd` crate: one code path on Linux, macOS and Windows.
pub struct MdnsBrowser;

impl Browser for MdnsBrowser {
    fn browse(&self, wanted: Option<&Node>) -> Vec<Advert> {
        let Ok(daemon) = mdns_sd::ServiceDaemon::new() else {
            return Vec::new();
        };
        let Ok(events) = daemon.browse(locator::SERVICE_TYPE) else {
            let _ = daemon.shutdown();
            return Vec::new();
        };
        let deadline = Instant::now() + BROWSE_TIMEOUT;
        let mut settle: Option<Instant> = None;
        let mut found: Vec<Advert> = Vec::new();
        loop {
            let limit = settle.map_or(deadline, |settle| settle.min(deadline));
            let now = Instant::now();
            if now >= limit {
                break;
            }
            match events.recv_timeout(limit - now) {
                Ok(mdns_sd::ServiceEvent::ServiceResolved(service)) => {
                    let addresses: Vec<IpAddr> = service.addresses.iter().map(mdns_sd::ScopedIp::to_ip_addr).collect();
                    let records: Vec<(String, String)> = service
                        .txt_properties
                        .iter()
                        .map(|property| (property.key().to_string(), property.val_str().to_string()))
                        .collect();
                    let Some(advert) = advert_from(&service.fullname, service.port, &addresses, &records) else {
                        continue;
                    };
                    let id = advert.node.node.clone();
                    merge(&mut found, advert);
                    // The remembered node, answering where it is remembered: nothing more to learn.
                    let settled = wanted.is_some_and(|wanted| {
                        wanted.node == id
                            && found.iter().any(|seen| seen.node.node == id && seen.addresses.contains(&wanted.address))
                    });
                    if settled {
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
        found.sort_by(|a, b| a.node.name.cmp(&b.node.name));
        found
    }
}

/// Builds an advert from what a browse resolved. `None` when no usable address came with it.
pub fn advert_from(fullname: &str, port: u16, addresses: &[IpAddr], records: &[(String, String)]) -> Option<Advert> {
    let record = |key: &str| records.iter().find(|(name, _)| name == key).map(|(_, value)| value.as_str());
    let port_record = |key: &str| record(key).and_then(|value| value.parse::<u16>().ok()).filter(|port| *port != 0);
    let name = fullname
        .strip_suffix(locator::SERVICE_TYPE)
        .map(|name| name.trim_end_matches('.'))
        .unwrap_or(fullname);
    let usable = usable_addresses(addresses);
    let mut node = Node {
        node: record("node").unwrap_or_default().to_string(),
        name: name.to_string(),
        address: usable.first()?.to_string(),
        model_port: port,
        web_port: port_record("web"),
        search_port: port_record("search"),
        version: record("version").unwrap_or_default().to_string(),
        last_seen: String::new(),
        remote: record("remote").unwrap_or_default().to_ascii_lowercase(),
    };
    // An overlay name that is not the node's own (`mightling-<its id>.`) is not kept.
    if !node.overlay_name_is_its_own() {
        node.remote.clear();
    }
    Some(Advert {
        addresses: usable.iter().map(IpAddr::to_string).collect(),
        node,
        // An advert without the record predates nothing: it is treated as the first contract.
        proto: record("proto").and_then(|value| value.parse().ok()).unwrap_or(1),
        state: record("state").unwrap_or_default().to_string(),
        main: record("main").is_some_and(|value| value != "0"),
    })
}

/// Connect to the resolved address, never the `.local` name. IPv4 first; then an IPv6 address
/// that needs no scope. Two kinds are dropped: a link-local IPv6 address, usable only with a
/// scope id that a URL cannot carry here; and loopback, which can never be the node a browsing
/// machine is looking for, because a node does not browse (the live browse of 2026-10-02, run on
/// the node itself, was answered on loopback and on each Docker bridge in turn).
pub fn best_address(addresses: &[IpAddr]) -> Option<IpAddr> {
    usable_addresses(addresses).first().copied()
}

/// The addresses a client can connect to, best first, without duplicates.
pub fn usable_addresses(addresses: &[IpAddr]) -> Vec<IpAddr> {
    let rank = |address: &IpAddr| match address {
        IpAddr::V4(v4) if v4.is_loopback() => None,
        IpAddr::V4(v4) if v4.is_link_local() => Some(1),
        IpAddr::V4(_) => Some(0),
        IpAddr::V6(v6) if v6.is_loopback() => None,
        IpAddr::V6(v6) if (v6.segments()[0] & 0xffc0) == 0xfe80 => None,
        IpAddr::V6(_) => Some(3),
    };
    let mut usable: Vec<(u8, IpAddr)> = addresses.iter().filter_map(|address| Some((rank(address)?, *address))).collect();
    usable.sort();
    usable.dedup();
    usable.into_iter().map(|(_, address)| address).collect()
}

/// Adds a node to those found, or adds the addresses of this answer to one already there: answers
/// arrive per interface and per address family, and one node must be one row, at its best address.
fn merge(found: &mut Vec<Advert>, advert: Advert) {
    let same = |other: &Advert| {
        if advert.node.node.is_empty() { other.node.name == advert.node.name } else { other.node.node == advert.node.node }
    };
    match found.iter_mut().find(|other| same(other)) {
        None => found.push(advert),
        Some(existing) => {
            let all: Vec<IpAddr> = existing
                .addresses
                .iter()
                .chain(advert.addresses.iter())
                .filter_map(|address| address.parse().ok())
                .collect();
            let usable = usable_addresses(&all);
            let address = usable.first().map(IpAddr::to_string).unwrap_or_else(|| existing.node.address.clone());
            *existing = Advert {
                addresses: usable.iter().map(IpAddr::to_string).collect(),
                node: Node { address, ..advert.node },
                ..advert
            };
        }
    }
}

/// `$CODEX_HOME/node.json`, for callers outside this module.
pub fn node_file() -> Option<PathBuf> {
    locator::node_file()
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use super::*;

    /// A browser that answers from a list and records what it was asked for.
    struct Fixed {
        adverts: Vec<Advert>,
        asked: RefCell<Vec<Option<String>>>,
    }

    impl Fixed {
        fn new(adverts: Vec<Advert>) -> Fixed {
            Fixed { adverts, asked: RefCell::new(Vec::new()) }
        }
    }

    impl Browser for Fixed {
        fn browse(&self, wanted: Option<&Node>) -> Vec<Advert> {
            self.asked.borrow_mut().push(wanted.map(|node| node.node.clone()));
            self.adverts.clone()
        }
    }

    fn advert(name: &str, id: &str, address: &str) -> Advert {
        Advert {
            addresses: vec![address.to_string()],
            node: Node {
                node: id.to_string(),
                name: name.to_string(),
                address: address.to_string(),
                model_port: 8000,
                web_port: Some(3000),
                search_port: Some(8888),
                version: "1.3.0".to_string(),
                last_seen: String::new(),
                remote: String::new(),
            },
            proto: 1,
            state: "ready".to_string(),
            main: true,
        }
    }

    fn spark1() -> Advert {
        advert("spark-1", "11111111-aaaa", "192.168.0.105")
    }

    fn spark2() -> Advert {
        advert("spark-2", "22222222-bbbb", "192.168.0.106")
    }

    #[test]
    fn a_configured_host_wins_and_nothing_is_browsed() {
        let browser = Fixed::new(vec![spark1()]);
        let inputs = Inputs { configured: Some("http://10.0.0.1:8000".into()), is_node: true, remembered: Some(spark2().node), pinned: Some("spark-1".into()), overlay: false };
        assert_eq!(resolve(&inputs, &browser), Resolution::Host("http://10.0.0.1:8000".into()));
        assert!(browser.asked.borrow().is_empty());
    }

    #[test]
    fn a_node_uses_loopback_and_never_browses() {
        let browser = Fixed::new(vec![spark2()]);
        let inputs = Inputs { is_node: true, remembered: Some(spark2().node), ..Inputs::default() };
        assert_eq!(resolve(&inputs, &browser), Resolution::Host(crate::DEFAULT_VLLM_HOST.into()));
        assert!(browser.asked.borrow().is_empty());
    }

    #[test]
    fn a_pinned_node_is_used_for_one_command_and_not_remembered() {
        let browser = Fixed::new(vec![spark1(), spark2()]);
        let inputs = Inputs { pinned: Some("SPARK-2".into()), is_node: true, ..Inputs::default() };
        assert_eq!(resolve(&inputs, &browser), Resolution::Found { advert: spark2(), remember: false, note: None });
        let missing = Inputs { pinned: Some("spark-9".into()), ..Inputs::default() };
        let Resolution::Refused(message) = resolve(&missing, &browser) else { panic!("not refused") };
        assert!(message.contains("MIGHTLING_NODE=spark-9: no such node") && message.contains("spark-1"), "{message}");
    }

    #[test]
    fn the_remembered_node_is_found_again_by_id_at_a_new_address() {
        let moved = advert("spark-1", "11111111-aaaa", "192.168.0.200");
        let browser = Fixed::new(vec![moved.clone()]);
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        assert_eq!(
            resolve(&inputs, &browser),
            Resolution::Found { advert: moved, remember: true, note: Some("Node spark-1 is now at 192.168.0.200.".into()) }
        );
        // The browse was told which id to stop at.
        assert_eq!(*browser.asked.borrow(), vec![Some("11111111-aaaa".to_string())]);
        // At the same address nothing is said.
        let same = Fixed::new(vec![spark1()]);
        assert_eq!(resolve(&inputs, &same), Resolution::Found { advert: spark1(), remember: true, note: None });
    }

    #[test]
    fn a_second_advert_claiming_the_remembered_id_is_never_followed_silently() {
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        let impostor = advert("spark-1", "11111111-aaaa", "192.168.0.66");
        // The real node still answers where it was remembered: that one is used, whatever order
        // the browse returned them in.
        assert_eq!(
            resolve(&inputs, &Fixed::new(vec![impostor.clone(), spark1()])),
            Resolution::Found { advert: spark1(), remember: true, note: None }
        );
        // Two claimants and neither at the remembered address: the user is asked.
        let other = advert("spark-1", "11111111-aaaa", "192.168.0.67");
        assert!(matches!(resolve(&inputs, &Fixed::new(vec![impostor, other])), Resolution::Choose { .. }));
    }

    #[test]
    fn a_node_with_several_addresses_keeps_the_remembered_one() {
        // Seen live on 2026-10-02: a node with a Wi-Fi address and Docker bridges answered a
        // different address on each start, and each start said it had moved.
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        let mut seen = advert("spark-1", "11111111-aaaa", "172.18.0.1");
        seen.addresses = vec!["172.18.0.1".into(), "192.168.0.105".into()];
        assert_eq!(
            resolve(&inputs, &Fixed::new(vec![seen.clone()])),
            Resolution::Found { advert: Advert { node: spark1().node, ..seen.clone() }, remember: true, note: None }
        );
        // It no longer answers there: it has moved, and the line says so.
        seen.addresses = vec!["172.18.0.1".into()];
        assert!(matches!(resolve(&inputs, &Fixed::new(vec![seen])), Resolution::Found { note: Some(note), .. } if note.contains("now at 172.18.0.1")));
    }

    #[test]
    fn the_last_address_is_tried_only_when_the_browse_returns_nothing_at_all() {
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        let silent = Fixed::new(Vec::new());
        let Resolution::LastAddress { node, note } = resolve(&inputs, &silent) else { panic!("not the last address") };
        assert_eq!(node, spark1().node);
        assert!(note.is_some_and(|note| note.contains("did not answer a browse") && note.contains("192.168.0.105")));
    }

    #[test]
    fn a_different_node_is_never_adopted_silently() {
        // The remembered node is gone and another answers: a question, not a switch. Prompts and
        // source code go to whichever node is chosen.
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        let browser = Fixed::new(vec![spark2()]);
        assert_eq!(
            resolve(&inputs, &browser),
            Resolution::Choose { adverts: vec![spark2()], gone: Some("spark-1".into()) }
        );
    }

    #[test]
    fn a_node_set_by_address_is_used_without_a_browse() {
        let by_hand = Node { address: "10.0.0.7".into(), model_port: 8000, ..Node::default() };
        let browser = Fixed::new(vec![spark1()]);
        let inputs = Inputs { remembered: Some(by_hand.clone()), ..Inputs::default() };
        assert_eq!(resolve(&inputs, &browser), Resolution::LastAddress { node: by_hand, note: None });
        assert!(browser.asked.borrow().is_empty());
    }

    #[test]
    fn one_node_found_is_used_and_remembered_with_one_line() {
        let browser = Fixed::new(vec![spark1()]);
        let Resolution::Found { advert, remember, note } = resolve(&Inputs::default(), &browser) else { panic!("not found") };
        assert_eq!((advert, remember), (spark1(), true));
        assert_eq!(note.as_deref(), Some("Using Mightling node spark-1 at 192.168.0.105 (remembered; `ling node list` shows every node)."));
    }

    #[test]
    fn none_found_and_none_remembered_is_its_own_case() {
        assert_eq!(resolve(&Inputs::default(), &Fixed::new(Vec::new())), Resolution::NoneFound);
        let message = none_found_message();
        assert!(message.starts_with("No Mightling node found on this network."));
        assert!(message.contains("ling-admin node enable") && message.contains("ling node use <address>"));
    }

    #[test]
    fn several_nodes_need_an_answer_unless_exactly_one_serves_a_coding_model() {
        let both = Fixed::new(vec![spark1(), spark2()]);
        assert_eq!(resolve(&Inputs::default(), &both), Resolution::Choose { adverts: vec![spark1(), spark2()], gone: None });
        let speech = Advert { main: false, ..spark2() };
        let one_main = Fixed::new(vec![speech.clone(), spark1()]);
        assert!(matches!(resolve(&Inputs::default(), &one_main), Resolution::Found { advert, remember: true, .. } if advert == spark1()));
        let none_main = Fixed::new(vec![speech.clone(), Advert { main: false, ..spark1() }]);
        assert!(matches!(resolve(&Inputs::default(), &none_main), Resolution::Choose { .. }));
    }

    #[test]
    fn where_no_question_can_be_asked_the_choice_is_refused_with_the_list() {
        let error = choose(&[spark1(), spark2()], None, false).unwrap_err().to_string();
        assert!(error.starts_with("Several Mightling nodes are on this network:"), "{error}");
        assert!(error.contains("spark-1") && error.contains("192.168.0.106") && error.contains("ling node use <name>"));
        let gone = choose(&[spark2()], Some("spark-1"), false).unwrap_err().to_string();
        assert!(gone.starts_with("The remembered node, spark-1, is not on this network, and another is:"), "{gone}");
    }

    #[test]
    fn a_node_with_a_newer_protocol_is_refused_and_names_the_update() {
        let newer = Advert { proto: locator::PROTO + 1, ..spark1() };
        let Resolution::Refused(message) = resolve(&Inputs::default(), &Fixed::new(vec![newer.clone()])) else { panic!("not refused") };
        assert!(message.contains("newer protocol (2)") && message.ends_with("ling update"), "{message}");
        assert!(pick(&[newer], "spark-1").is_err());
    }

    #[test]
    fn each_state_has_its_own_message() {
        let node = spark1().node;
        assert_eq!(state_verdict(&node, "stopped", true), Ok(None));
        assert_eq!(
            state_verdict(&node, "stopped", false),
            Err("Node spark-1 found, but its model server is not running. On the node: ling-admin server start".to_string())
        );
        assert_eq!(state_verdict(&node, "loading", false), Ok(Some("Node spark-1: model loading".to_string())));
        let stale = state_verdict(&node, "ready", false).unwrap_err();
        assert!(stale.contains("advertised as ready") && stale.contains("http://192.168.0.105:8000") && stale.contains("ling-admin status"));
        // A remembered address that no browse confirmed: wait, as before the split.
        assert_eq!(state_verdict(&node, "", false), Ok(None));
    }

    #[test]
    fn a_node_is_named_by_name_address_or_id_prefix() {
        let node = spark1();
        for name in ["spark-1", "Spark-1.local", "192.168.0.105", "1111", "11111111-AAAA"] {
            assert!(matches(&node, name), "{name}");
        }
        for name in ["spark", "111", "192.168.0.10", ""] {
            assert!(!matches(&node, name), "{name}");
        }
    }

    #[test]
    fn use_takes_a_name_or_an_address_that_no_browse_confirmed() {
        let adverts = [spark1(), spark2()];
        assert_eq!(pick(&adverts, "spark-2"), Ok(spark2().node));
        assert_eq!(
            pick(&[], "10.0.0.7"),
            Ok(Node { address: "10.0.0.7".into(), model_port: 8000, ..Node::default() })
        );
        assert_eq!(pick(&[], "http://spark.lan:9000/").map(|node| (node.address, node.model_port)), Ok(("spark.lan".to_string(), 9000)));
        assert_eq!(pick(&[], "fd00::7").map(|node| node.model_url()), Ok("http://[fd00::7]:8000".to_string()));
        let unknown = pick(&adverts, "spark-9").unwrap_err();
        assert!(unknown.starts_with("No node named spark-9") && unknown.contains("spark-1"), "{unknown}");
        assert!(pick(&[], "10.0.0.7:http").is_err());
    }

    #[test]
    fn the_command_line_is_parsed() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        assert_eq!(Request::from_args(&[]), Request::List);
        assert_eq!(Request::from_args(&args(&["list"])), Request::List);
        assert_eq!(Request::from_args(&args(&["use", "spark-2"])), Request::Use("spark-2".into()));
        assert_eq!(Request::from_args(&args(&["forget"])), Request::Forget);
        assert_eq!(Request::from_args(&args(&["use"])), Request::Usage);
        assert_eq!(Request::from_args(&args(&["enable"])), Request::Usage);
    }

    #[test]
    fn the_list_says_which_node_is_in_use_and_gives_the_url_to_paste() {
        let model = crate::ServedModel { id: "RadixArk/Qwen3.8-27B-NVFP4".into(), max_model_len: 262_144, owned_by: "sglang".into() };
        let inputs = Inputs { remembered: Some(spark1().node), ..Inputs::default() };
        let lines = list_lines(&inputs, &[spark1(), Advert { state: "stopped".into(), main: false, ..spark2() }], &[Some(model), None]);
        assert_eq!(lines[0], "* spark-1  http://192.168.0.105:8000/v1  RadixArk/Qwen3.8-27B-NVFP4 (262144 tokens)  Mightling 1.3.0");
        assert_eq!(lines[1], "  spark-2  http://192.168.0.106:8000/v1  stopped  Mightling 1.3.0  (not a coding model)");
        assert_eq!(lines.len(), 2);
        // The remembered node did not answer: it is still shown as the one in use.
        let away = list_lines(&inputs, &[spark2()], &[None]);
        assert!(away.last().is_some_and(|line| line.starts_with("* spark-1") && line.ends_with("did not answer this browse")));
        // On a node the list says so, and marks none as in use.
        let on_node = list_lines(&Inputs { is_node: true, ..Inputs::default() }, &[spark1()], &[None]);
        assert!(on_node[0].starts_with("This machine is a node") && on_node[1].starts_with("  spark-1"));
        assert_eq!(list_lines(&Inputs::default(), &[], &[]), vec!["No Mightling node answered on this network.".to_string()]);
    }

    #[test]
    fn an_advert_is_read_from_the_browse_answer() {
        let records: Vec<(String, String)> = [("proto", "1"), ("node", "7c1e"), ("version", "1.3.0"), ("web", "3000"), ("search", "8888"), ("state", "loading"), ("main", "1")]
            .iter()
            .map(|(key, value)| (key.to_string(), value.to_string()))
            .collect();
        let addresses: Vec<IpAddr> = ["fe80::1", "172.17.0.1", "192.168.0.105"].iter().filter_map(|a| a.parse().ok()).collect();
        let advert = advert_from("gx10-9428._mightling-node._tcp.local.", 8000, &addresses, &records);
        let advert = advert.unwrap_or_default();
        assert_eq!(advert.node.name, "gx10-9428");
        assert_eq!((advert.node.node.as_str(), advert.node.model_port, advert.node.web_port, advert.node.search_port), ("7c1e", 8000, Some(3000), Some(8888)));
        assert_eq!((advert.proto, advert.state.as_str(), advert.main), (1, "loading", true));
        // A node that shares nothing but the model, and says nothing else.
        let bare = advert_from("spark._mightling-node._tcp.local.", 8000, &addresses, &[]).unwrap_or_default();
        assert_eq!((bare.node.web_port, bare.node.search_port, bare.main, bare.proto), (None, None, false, 1));
        // Only a link-local IPv6 address: nothing to connect to.
        assert_eq!(advert_from("x._mightling-node._tcp.local.", 8000, &["fe80::1".parse::<IpAddr>().ok()].into_iter().flatten().collect::<Vec<_>>(), &[]), None);
    }

    #[test]
    fn ipv4_is_preferred_and_link_local_ipv6_is_dropped() {
        let parse = |addresses: &[&str]| addresses.iter().filter_map(|a| a.parse::<IpAddr>().ok()).collect::<Vec<_>>();
        assert_eq!(best_address(&parse(&["fe80::1", "fd00::2", "192.168.0.105"])), "192.168.0.105".parse().ok());
        assert_eq!(best_address(&parse(&["fe80::1", "fd00::2"])), "fd00::2".parse().ok());
        assert_eq!(best_address(&parse(&["127.0.0.1", "169.254.3.4"])), "169.254.3.4".parse().ok());
        assert_eq!(best_address(&parse(&["fe80::1"])), None);
        // Loopback is never the node a browsing machine wants: a node does not browse.
        assert_eq!(best_address(&parse(&["127.0.0.1", "::1"])), None);
    }

    #[test]
    fn one_node_seen_on_several_interfaces_is_one_row_with_its_best_address() {
        let mut found = Vec::new();
        merge(&mut found, advert("spark-1", "1111", "fd00::2"));
        merge(&mut found, advert("spark-1", "1111", "192.168.0.105"));
        merge(&mut found, advert("spark-1", "1111", "fd00::2"));
        merge(&mut found, advert("spark-2", "2222", "192.168.0.106"));
        assert_eq!(found.iter().map(|a| (a.node.name.as_str(), a.node.address.as_str())).collect::<Vec<_>>(),
                   vec![("spark-1", "192.168.0.105"), ("spark-2", "192.168.0.106")]);
        assert_eq!(found[0].addresses, vec!["192.168.0.105".to_string(), "fd00::2".to_string()]);
    }

    #[test]
    fn the_node_file_is_written_whole_and_forgotten() {
        let dir = std::env::temp_dir().join(format!("mightling-node-file-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("home").join(locator::NODE_FILE);
        let node = Node { last_seen: "2026-10-02T09:00:00+01:00".into(), ..spark1().node };
        assert!(write_node_file(&path, &node).is_ok());
        assert_eq!(locator::remembered_at(&path), Some(node));
        // No staging file is left beside it.
        let left: Vec<String> = std::fs::read_dir(dir.join("home")).into_iter().flatten().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        assert_eq!(left, vec![locator::NODE_FILE.to_string()]);
        assert_eq!(forget(&path), vec!["Forgotten. The next start looks for a node on the network again.".to_string()]);
        assert_eq!(forget(&path), vec!["No node was remembered.".to_string()]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The real browse, against a node advertised on this network. Not run by default: it needs
    /// an advert (`avahi-publish -s <name> _mightling-node._tcp 8000 proto=1 node=<id> …`, or a real
    /// node) and multicast. `MIGHTLING_TEST_NODE_ID=<id> cargo test … live_browse -- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn live_browse_finds_an_advertised_node() {
        let wanted = std::env::var("MIGHTLING_TEST_NODE_ID").unwrap_or_default();
        let started = Instant::now();
        let all = MdnsBrowser.browse(None);
        println!("browse(None): {:?} in {:?}", all, started.elapsed());
        let started = Instant::now();
        let one = MdnsBrowser.browse(Some(&Node { node: wanted.clone(), ..Node::default() }));
        println!("browse(Some({wanted})): {:?} in {:?}", one, started.elapsed());
        assert!(all.iter().any(|advert| advert.node.node == wanted), "the advertised node was not found");
        assert!(one.iter().any(|advert| advert.node.node == wanted));
        let remembered = Node { node: wanted.clone(), address: "192.0.2.1".into(), model_port: 8000, ..Node::default() };
        let resolution = resolve(&Inputs { remembered: Some(remembered), ..Inputs::default() }, &MdnsBrowser);
        println!("resolve(remembered at another address): {resolution:?}");
        assert!(matches!(resolution, Resolution::Found { remember: true, note: Some(_), .. }));
    }

    #[test]
    fn a_version_difference_is_said_once_a_day() {
        let node = spark1().node;
        assert_eq!(version_notice_line("1.3.0", &node, None, "2026-10-02"), None);
        let line = version_notice_line("1.4.0", &node, Some("2026-10-01\n"), "2026-10-02");
        assert_eq!(line.as_deref(), Some("Note: node spark-1 runs Mightling 1.3.0, this machine 1.4.0. `ling update` here, or update the node."));
        assert_eq!(version_notice_line("1.4.0", &node, Some("2026-10-02"), "2026-10-02"), None);
        assert_eq!(version_notice_line("1.4.0", &Node { version: String::new(), ..node }, None, "2026-10-02"), None);
    }

    // -- remote access (REMOTE_ACCESS §5) ------------------------------------------------------------

    fn enrolled() -> Node {
        Node { remote: "mightling-11111111-aaaa.netbird.selfhosted".to_string(), ..spark1().node }
    }

    #[test]
    fn the_overlay_name_comes_before_any_browse() {
        let browser = Fixed { adverts: vec![spark1()], asked: RefCell::new(Vec::new()) };
        let inputs = Inputs { remembered: Some(enrolled()), overlay: true, ..Inputs::default() };
        match resolve(&inputs, &browser) {
            Resolution::Overlay { node } => assert_eq!(node.model_url(), "http://mightling-11111111-aaaa.netbird.selfhosted:8000"),
            other => panic!("{other:?}"),
        }
        assert!(browser.asked.borrow().is_empty(), "no browse");
    }

    #[test]
    fn with_the_overlay_down_the_lan_tiers_run_as_before() {
        let browser = Fixed { adverts: vec![spark1()], asked: RefCell::new(Vec::new()) };
        let inputs = Inputs { remembered: Some(enrolled()), overlay: false, ..Inputs::default() };
        assert!(matches!(resolve(&inputs, &browser), Resolution::Found { .. }));
    }

    #[test]
    fn configuration_and_a_node_still_win_over_the_overlay() {
        let browser = Fixed { adverts: Vec::new(), asked: RefCell::new(Vec::new()) };
        let configured = Inputs { configured: Some("http://10.0.0.1:8000".into()), remembered: Some(enrolled()), overlay: true, ..Inputs::default() };
        assert_eq!(resolve(&configured, &browser), Resolution::Host("http://10.0.0.1:8000".into()));
        let node = Inputs { is_node: true, remembered: Some(enrolled()), overlay: true, ..Inputs::default() };
        assert!(matches!(resolve(&node, &browser), Resolution::Host(_)));
    }

    #[test]
    fn an_overlay_name_that_is_not_the_nodes_own_is_never_used() {
        let browser = Fixed { adverts: Vec::new(), asked: RefCell::new(Vec::new()) };
        let stranger = Node { remote: "laptop.netbird.selfhosted".to_string(), ..spark1().node };
        let inputs = Inputs { remembered: Some(stranger), overlay: true, ..Inputs::default() };
        assert!(matches!(resolve(&inputs, &browser), Resolution::LastAddress { .. }));
    }

    #[test]
    fn the_advert_carries_the_overlay_name_only_when_it_is_the_nodes_own() {
        let addresses = ["192.168.0.105".parse().unwrap()];
        let record = |remote: &str| {
            vec![("node".to_string(), "11111111-aaaa".to_string()), ("remote".to_string(), remote.to_string())]
        };
        let own = advert_from("spark-1._mightling-node._tcp.local.", 8000, &addresses, &record("Mightling-11111111-AAAA.netbird.selfhosted")).unwrap();
        assert_eq!(own.node.remote, "mightling-11111111-aaaa.netbird.selfhosted");
        let other = advert_from("spark-1._mightling-node._tcp.local.", 8000, &addresses, &record("mightling-2222.netbird.selfhosted")).unwrap();
        assert_eq!(other.node.remote, "");
    }

    #[test]
    fn netbirds_status_is_read_for_a_connected_management() {
        assert!(netbird_connected(&serde_json::json!({"management": {"url": "https://b:443", "connected": true}})));
        assert!(!netbird_connected(&serde_json::json!({"management": {"connected": false}})));
        assert!(!netbird_connected(&serde_json::json!({"daemonStatus": "NeedsLogin"})));
    }

    #[test]
    fn remote_subcommands_go_to_remote_join() {
        let args: Vec<String> = ["remote", "join", "--code", "12345678"].iter().map(|s| s.to_string()).collect();
        assert_eq!(Request::from_args(&args), Request::Remote(args[1..].to_vec()));
    }

    #[test]
    fn the_list_says_how_the_node_is_reached_off_the_lan() {
        let inputs = Inputs { remembered: Some(enrolled()), overlay: true, ..Inputs::default() };
        let lines = list_lines(&inputs, &[], &[]);
        assert!(lines.iter().any(|line| line.contains("over the overlay (remote access)")), "{lines:?}");
        let mut advert = spark1();
        advert.node.remote = enrolled().remote;
        let lines = list_lines(&Inputs::default(), &[advert], &[None]);
        assert!(lines.iter().any(|line| line.contains("off the LAN: mightling-11111111-aaaa.netbird.selfhosted")), "{lines:?}");
    }

}
