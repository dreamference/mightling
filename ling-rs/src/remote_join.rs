//! `ling node remote join --code <8 digits>`: enrolling this machine in its node's overlay, on the
//! node's LAN (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §4.1).
//!
//! The node prints a code with `ling-admin remote code` and waits. This command finds the node by a
//! browse (off the LAN nothing answers, and it stops there), proves it knows the code without
//! sending it, checks the node's proof over the bundle it gets back, and then, saying each step
//! before it runs:
//!
//! 1. installs the node's CA in the system trust store (NetBird's client trusts nothing else; the CA
//!    is name-constrained to the box and the overlay's domain, so it vouches for nothing more, and
//!    `constrained_ca` refuses one that is not before anything is installed);
//! 2. installs NetBird's client from its release archive, pinned by SHA-256, when it is missing,
//!    and starts its service;
//! 3. runs `netbird up` with the management URL and the one-time setup key from the bundle;
//! 4. remembers the node's overlay name in `node.json`, never an address.
//!
//! The exchange (the node's half is `dreamference/remote/remote_enrolment.py`):
//!
//! ```text
//! POST /mightling/enrol  {"nonce": n, "proof": HMAC(code, "mightling-enrol-client|" + n), "client": host}
//! ← {"bundle": text, "mac": HMAC(code, "mightling-enrol-server|" + n + "|" + text)}
//! ```
//!
//! HMAC-SHA256 keyed with the code's eight ASCII digits, lowercase hex.

use std::io::Read;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use base64::Engine;
use sha2::Digest;
use sha2::Sha256;

use crate::node::Advert;
use crate::node::Browser;
use crate::node::MdnsBrowser;
use crate::node::Node;

/// Where the node listens while a code is valid.
pub const ENROL_PORT: u16 = 3190;
pub const ENROL_PATH: &str = "/mightling/enrol";
const CLIENT_LABEL: &str = "mightling-enrol-client|";
const SERVER_LABEL: &str = "mightling-enrol-server|";

/// NetBird's client release and the SHA-256 of each archive (netbird_0.80.0_checksums.txt).
pub const NETBIRD_VERSION: &str = "0.80.0";
const CLIENT_ARCHIVES: [(&str, &str, &str); 6] = [
    ("linux", "amd64", "47ffaba4fc3929f31795bd6c5232d6c29744d3169e2c93e6d9c84624f0ef6405"),
    ("linux", "arm64", "8cbd99fa068a7b0f3968b2d31dc341acc17dd1e97c3053e61ed5905dbdae7341"),
    ("darwin", "amd64", "ce35e797c6e9259da7d9489e06a13ef70fcf62f47c0d53ed900cc399d96c7a6b"),
    ("darwin", "arm64", "a6aba5c5f3b028b11c19cad3c17f547bcd190089e9ace094ed6e30853e520613"),
    ("windows", "amd64", "5f3c66102e9950cd8a41f54621a515fbaf45bdd2a1cd52fe7a90158dc99251fa"),
    ("windows", "arm64", "3c7af395289b3027168727d32e0d585f65477ba77cd5a6051bef9b50569f202c"),
];

const USAGE: &str = "Usage: ling node remote join --code <8 digits> [--node <name>]\n\
    On the node, on the same LAN: ling-admin remote code";

/// What the node sends: everything a client needs to join, and nothing that is an address.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Bundle {
    pub node: String,
    pub name: String,
    pub ca: String,
    pub management_url: String,
    pub relay: String,
    pub overlay_domain: String,
    pub overlay_name: String,
    pub setup_key: String,
}

// -- the proofs -------------------------------------------------------------------------------------

/// HMAC-SHA256 (RFC 2104), on the `sha2` the launcher already links.
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    let mut block = [0u8; 64];
    if key.len() > 64 {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    inner.update(block.map(|byte| byte ^ 0x36));
    inner.update(message);
    let mut outer = Sha256::new();
    outer.update(block.map(|byte| byte ^ 0x5c));
    outer.update(inner.finalize());
    outer.finalize().into()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// The client's proof that it knows the code.
pub fn client_proof(code: &str, nonce: &str) -> String {
    hex(&hmac_sha256(code.as_bytes(), format!("{CLIENT_LABEL}{nonce}").as_bytes()))
}

/// The node's proof over the bundle's exact text.
pub fn server_mac(code: &str, nonce: &str, bundle: &str) -> String {
    hex(&hmac_sha256(code.as_bytes(), format!("{SERVER_LABEL}{nonce}|{bundle}").as_bytes()))
}

/// Compares two strings in time that does not depend on where they differ.
fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// The code as typed (`1234 5678` or `12345678`), or `None` when it is not eight digits.
pub fn parse_code(text: &str) -> Option<String> {
    let digits: String = text.chars().filter(|c| !c.is_whitespace() && *c != '-').collect();
    (digits.len() == 8 && digits.chars().all(|c| c.is_ascii_digit())).then_some(digits)
}

// -- the CA's constraints ---------------------------------------------------------------------------

/// `basicConstraints` (2.5.29.19) and `nameConstraints` (2.5.29.30), the extension OIDs' contents.
const BASIC_CONSTRAINTS: &[u8] = &[0x55, 0x1d, 0x13];
const NAME_CONSTRAINTS: &[u8] = &[0x55, 0x1d, 0x1e];
/// `SEQUENCE { cA TRUE, pathLenConstraint 0 }`, the only basic constraints a node's CA has.
const CA_PATHLEN_ZERO: &[u8] = &[0x30, 0x06, 0x01, 0x01, 0xff, 0x02, 0x01, 0x00];

/// One DER element of `bytes`: its tag, its contents and what follows it. Strict DER only: no
/// indefinite or overlong length, no multi-byte tag, nothing past the end.
fn der(bytes: &[u8]) -> Result<(u8, &[u8], &[u8]), String> {
    let malformed = || "the CA in the bundle is not a well-formed certificate".to_string();
    let tag = *bytes.first().ok_or_else(malformed)?;
    let first = *bytes.get(1).ok_or_else(malformed)?;
    if tag & 0x1f == 0x1f {
        return Err(malformed());
    }
    let (length, start) = if first < 0x80 {
        (usize::from(first), 2usize)
    } else {
        let count = usize::from(first & 0x7f);
        if count == 0 || count > 4 {
            return Err(malformed());
        }
        let digits = bytes.get(2..2 + count).ok_or_else(malformed)?;
        if digits.first() == Some(&0) {
            return Err(malformed());
        }
        let length = digits.iter().fold(0usize, |acc, digit| (acc << 8) | usize::from(*digit));
        if length < 0x80 {
            return Err(malformed());
        }
        (length, 2 + count)
    };
    let end = start.checked_add(length).ok_or_else(malformed)?;
    let contents = bytes.get(start..end).ok_or_else(malformed)?;
    Ok((tag, contents, bytes.get(end..).unwrap_or_default()))
}

/// Every element of `bytes`, in order.
fn der_all(mut bytes: &[u8]) -> Result<Vec<(u8, &[u8])>, String> {
    let mut elements = Vec::new();
    while !bytes.is_empty() {
        let (tag, contents, rest) = der(bytes)?;
        elements.push((tag, contents));
        bytes = rest;
    }
    Ok(elements)
}

/// The one element of `bytes`, which must have `tag`.
fn der_only(bytes: &[u8], tag: u8) -> Result<&[u8], String> {
    match der_all(bytes)?.as_slice() {
        [(found, contents)] if *found == tag => Ok(contents),
        _ => Err("the CA in the bundle is not a well-formed certificate".to_string()),
    }
}

/// Whether a `nameConstraints` DNS entry (lowercased, without its leading dot) is a domain a
/// node's CA may be held to: not empty (that permits every name) and not a bare top-level domain.
fn constraint_entry_is_a_domain(entry: &str) -> bool {
    entry.contains('.')
        && !entry.starts_with('.')
        && !entry.ends_with('.')
        && entry.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '-' | '.'))
}

/// Checks the bundle's CA before it goes in the system trust store, where it could vouch for any
/// site: it must be one CA that signs no other CA (`basicConstraints` critical, `CA:TRUE,
/// pathlen:0`), with critical `nameConstraints` that permit only DNS domains, none excluded, one of
/// them the management host or a domain above it. The certificate's DER is read here, with no
/// `openssl`, so the check runs everywhere `join` does; the node makes its CA this way in
/// `dreamference/remote/remote_certificate_authority.py`.
pub fn constrained_ca(pem: &str, management_host: &str) -> Result<(), String> {
    let malformed = || "the CA in the bundle is not a well-formed certificate".to_string();
    let body: String = pem.lines().filter(|line| !line.starts_with("-----")).map(str::trim).collect();
    let bytes = base64::engine::general_purpose::STANDARD.decode(body).map_err(|_| malformed())?;
    let certificate = der_all(der_only(&bytes, 0x30)?)?;
    let tbs = certificate.first().filter(|(tag, _)| *tag == 0x30).ok_or_else(malformed)?.1;
    let mut extension_lists = der_all(tbs)?.into_iter().filter(|(tag, _)| *tag == 0xa3);
    let (Some((_, list)), None) = (extension_lists.next(), extension_lists.next()) else {
        return Err("the CA in the bundle has no extensions, so no name constraints".to_string());
    };
    let mut basic = None;
    let mut names = None;
    let mut seen = Vec::new();
    for (tag, extension) in der_all(der_only(list, 0x30)?)? {
        let fields = der_all(extension)?;
        let (oid, critical, value) = match (tag, fields.as_slice()) {
            (0x30, [(0x06, oid), (0x04, value)]) => (*oid, false, *value),
            (0x30, [(0x06, oid), (0x01, flag), (0x04, value)]) => (*oid, *flag == [0xff], *value),
            _ => return Err(malformed()),
        };
        if seen.contains(&oid) {
            return Err("the CA in the bundle repeats an extension".to_string());
        }
        seen.push(oid);
        if oid == BASIC_CONSTRAINTS {
            basic = Some((critical, value));
        } else if oid == NAME_CONSTRAINTS {
            names = Some((critical, value));
        }
    }
    if basic != Some((true, CA_PATHLEN_ZERO)) {
        return Err("the CA in the bundle may sign other CAs (it needs critical CA:TRUE, pathlen:0)".to_string());
    }
    let Some((true, constraints)) = names else {
        return Err("the CA in the bundle is not name-constrained (it needs critical nameConstraints)".to_string());
    };
    let permitted = der_only(der_only(constraints, 0x30)?, 0xa0)
        .map_err(|_| "the CA's name constraints must permit DNS domains only and exclude none".to_string())?;
    let mut entries = Vec::new();
    for (tag, subtree) in der_all(permitted)? {
        let entry = match (tag, der_all(subtree)?.as_slice()) {
            (0x30, [(0x82, name)]) => String::from_utf8_lossy(name).to_ascii_lowercase(),
            _ => return Err("the CA's name constraints must permit DNS domains only and exclude none".to_string()),
        };
        let entry = entry.trim_start_matches('.').to_string();
        if !constraint_entry_is_a_domain(&entry) {
            return Err(format!("the CA's name constraint {entry:?} is not a domain"));
        }
        entries.push(entry);
    }
    let host = management_host.to_ascii_lowercase();
    let host = host.trim_end_matches('.');
    if !entries.iter().any(|entry| host == entry || host.ends_with(&format!(".{entry}"))) {
        return Err(format!("the CA in the bundle is not constrained to {host} (it permits {})", entries.join(", ")));
    }
    Ok(())
}

/// Checks the node's answer and reads the bundle.
///
/// Refused: a wrong proof (someone who does not know the code answered), a bundle for another
/// node than the one browsed, an overlay name that is not that node's own, a management URL that
/// is not HTTPS, anything that is not a certificate where the CA goes, a setup key with odd
/// characters, a CA that is not name-constrained to the management host (`constrained_ca`).
pub fn verify(code: &str, nonce: &str, answer: &serde_json::Value, node_id: &str) -> Result<Bundle, String> {
    let text = answer.get("bundle").and_then(|value| value.as_str()).ok_or("the node sent no bundle")?;
    let mac = answer.get("mac").and_then(|value| value.as_str()).ok_or("the node sent no proof")?;
    if !same(&mac.to_ascii_lowercase(), &server_mac(code, nonce, text)) {
        return Err("the answer does not prove the code: something other than your node answered".to_string());
    }
    let value: serde_json::Value = serde_json::from_str(text).map_err(|error| format!("the bundle is not JSON: {error}"))?;
    let field = |key: &str| value.get(key).and_then(|v| v.as_str()).unwrap_or_default().trim().to_string();
    let bundle = Bundle {
        node: field("node"),
        name: field("name"),
        ca: field("ca"),
        management_url: field("management_url"),
        relay: field("relay"),
        overlay_domain: field("overlay_domain"),
        overlay_name: field("overlay_name").to_ascii_lowercase(),
        setup_key: field("setup_key"),
    };
    if !node_id.is_empty() && bundle.node != node_id {
        return Err(format!("the bundle is for node {}, not the one browsed ({node_id})", bundle.node));
    }
    let own = Node { node: bundle.node.clone(), remote: bundle.overlay_name.clone(), ..Node::default() };
    if !own.overlay_name_is_its_own() || !bundle.overlay_name.ends_with(&format!(".{}", bundle.overlay_domain)) {
        return Err(format!("{} is not the node's own overlay name", bundle.overlay_name));
    }
    let host = bundle.management_url.strip_prefix("https://").unwrap_or_default();
    if host.is_empty() || !host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | ':')) {
        return Err(format!("{} is not an HTTPS URL", bundle.management_url));
    }
    if bundle.ca.matches("-----BEGIN ").count() != 1
        || !bundle.ca.starts_with("-----BEGIN CERTIFICATE-----")
        || !bundle.ca.trim_end().ends_with("-----END CERTIFICATE-----")
    {
        return Err("the CA in the bundle is not one certificate".to_string());
    }
    constrained_ca(&bundle.ca, host.split(':').next().unwrap_or_default())?;
    if bundle.setup_key.is_empty() || !bundle.setup_key.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("the setup key in the bundle is malformed".to_string());
    }
    Ok(bundle)
}

// -- what runs on this machine ----------------------------------------------------------------------

/// The operating system as NetBird's archives name it.
pub fn os_name() -> &'static str {
    match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    }
}

/// The architecture as NetBird's archives name it.
pub fn arch_name() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "amd64",
        "aarch64" => "arm64",
        other => other,
    }
}

/// The client archive's URL and SHA-256 for a system, if it is pinned.
pub fn client_archive(os: &str, arch: &str) -> Option<(String, &'static str)> {
    CLIENT_ARCHIVES.iter().find(|(o, a, _)| *o == os && *a == arch).map(|(o, a, sha)| {
        (
            format!("https://github.com/netbirdio/netbird/releases/download/v{NETBIRD_VERSION}/netbird_{NETBIRD_VERSION}_{o}_{a}.tar.gz"),
            *sha,
        )
    })
}

/// One file out of a gzipped tar, by base name. `None` when it is not there.
pub fn file_from_tar_gz(data: &[u8], wanted: &str) -> Option<Vec<u8>> {
    let mut tar = Vec::new();
    flate2::read::GzDecoder::new(data).read_to_end(&mut tar).ok()?;
    let mut offset = 0;
    while offset + 512 <= tar.len() {
        let header = &tar[offset..offset + 512];
        if header.iter().all(|byte| *byte == 0) {
            return None;
        }
        let text = |range: std::ops::Range<usize>| {
            String::from_utf8_lossy(&header[range]).trim_end_matches('\0').trim().to_string()
        };
        let name = text(0..100);
        let prefix = text(345..500);
        let size = usize::from_str_radix(text(124..136).trim_matches(char::from(0)), 8).ok()?;
        let kind = header[156];
        let full = if prefix.is_empty() { name } else { format!("{prefix}/{name}") };
        let start = offset + 512;
        if (kind == b'0' || kind == 0) && Path::new(&full).file_name().is_some_and(|base| base == wanted) {
            return tar.get(start..start + size).map(<[u8]>::to_vec);
        }
        offset = start + size.div_ceil(512) * 512;
    }
    None
}

/// The commands that put the node's CA in this system's trust store, the only store NetBird's
/// client reads. `id` names the file, so two nodes' CAs never overwrite each other.
pub fn trust_commands(os: &str, ca: &Path, id: &str, linux_tool: Option<&str>) -> Result<Vec<Vec<String>>, String> {
    let ca = ca.display().to_string();
    let short: String = id.chars().take(8).collect();
    let run = |argv: &[&str]| argv.iter().map(|arg| arg.to_string()).collect::<Vec<String>>();
    match os {
        "linux" => match linux_tool {
            Some("update-ca-certificates") => {
                let target = format!("/usr/local/share/ca-certificates/mightling-node-{short}.crt");
                Ok(vec![run(&["sudo", "install", "-m", "0644", &ca, &target]), run(&["sudo", "update-ca-certificates"])])
            }
            Some("update-ca-trust") => {
                let target = format!("/etc/pki/ca-trust/source/anchors/mightling-node-{short}.crt");
                Ok(vec![run(&["sudo", "install", "-m", "0644", &ca, &target]), run(&["sudo", "update-ca-trust", "extract"])])
            }
            _ => Err("this Linux has neither update-ca-certificates nor update-ca-trust".to_string()),
        },
        "darwin" => Ok(vec![run(&[
            "sudo", "security", "add-trusted-cert", "-d", "-r", "trustRoot", "-k", "/Library/Keychains/System.keychain", &ca,
        ])]),
        "windows" => Ok(vec![run(&["certutil", "-addstore", "-f", "Root", &ca])]),
        other => Err(format!("remote access is not supported on {other}")),
    }
}

/// The commands that install NetBird's client from a checked binary and start its service.
pub fn install_commands(os: &str, staged: &Path) -> Vec<Vec<String>> {
    let staged = staged.display().to_string();
    let run = |argv: &[&str]| argv.iter().map(|arg| arg.to_string()).collect::<Vec<String>>();
    if os == "windows" {
        let target = windows_client_path();
        let folder = Path::new(&target).parent().map(|p| p.display().to_string()).unwrap_or_default();
        return vec![
            run(&["cmd", "/C", "mkdir", &folder]),
            run(&["cmd", "/C", "copy", "/Y", &staged, &target]),
            run(&[&target, "service", "install"]),
            run(&[&target, "service", "start"]),
        ];
    }
    vec![
        run(&["sudo", "install", "-m", "0755", &staged, "/usr/local/bin/netbird"]),
        run(&["sudo", "/usr/local/bin/netbird", "service", "install"]),
        run(&["sudo", "/usr/local/bin/netbird", "service", "start"]),
    ]
}

fn windows_client_path() -> String {
    let base = std::env::var("ProgramFiles").unwrap_or_else(|_| "C:\\Program Files".to_string());
    format!("{base}\\Mightling\\NetBird\\netbird.exe")
}

/// The `netbird up` that joins the overlay. No sudo: the daemon's socket is open to local users.
/// The one-time key is read from a file only this user can read, never given on the command line,
/// where any process listing would show it.
pub fn up_command(netbird: &str, bundle: &Bundle, key_file: &Path) -> Vec<String> {
    vec![
        netbird.to_string(),
        "up".to_string(),
        "--management-url".to_string(),
        bundle.management_url.clone(),
        "--setup-key-file".to_string(),
        key_file.display().to_string(),
    ]
}

/// Writes the one-time key where only this user can read it (0600 on Unix).
pub fn write_key_file(folder: &Path, key: &str) -> Result<PathBuf, String> {
    std::fs::create_dir_all(folder).map_err(|error| error.to_string())?;
    let path = folder.join("remote-setup-key");
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&path).map_err(|error| error.to_string())?;
    std::io::Write::write_all(&mut file, format!("{key}\n").as_bytes()).map_err(|error| error.to_string())?;
    Ok(path)
}

/// The installed client, if any: on PATH, or where this command installs it.
fn installed_client() -> Option<String> {
    let name = if cfg!(windows) { "netbird.exe" } else { "netbird" };
    let on_path = std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path).map(|dir| dir.join(name)).find(|candidate| candidate.is_file())
    });
    let fixed = if cfg!(windows) { PathBuf::from(windows_client_path()) } else { PathBuf::from("/usr/local/bin/netbird") };
    on_path.or_else(|| fixed.is_file().then_some(fixed)).map(|path| path.display().to_string())
}

fn linux_tool() -> Option<&'static str> {
    ["update-ca-certificates", "update-ca-trust"].into_iter().find(|tool| {
        ["/usr/sbin", "/usr/bin", "/sbin", "/bin"].iter().any(|dir| Path::new(dir).join(tool).is_file())
    })
}

/// Runs each command with the terminal (sudo asks there), printing it first.
fn run_steps(purpose: &str, steps: &[Vec<String>]) -> Result<(), String> {
    println!("{purpose}:");
    for step in steps {
        println!("   {}", step.join(" "));
    }
    for step in steps {
        let status = Command::new(&step[0]).args(&step[1..]).status().map_err(|error| format!("{}: {error}", step[0]))?;
        if !status.success() {
            return Err(format!("`{}` failed ({status})", step.join(" ")));
        }
    }
    Ok(())
}

// -- the command --------------------------------------------------------------------------------------

/// What `ling node remote …` asks for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    Join { code: String, node: Option<String> },
    Usage,
}

impl Request {
    pub fn from_args(args: &[String]) -> Request {
        let mut code = None;
        let mut node = None;
        let mut rest = args.iter();
        if rest.next().map(String::as_str) != Some("join") {
            return Request::Usage;
        }
        while let Some(arg) = rest.next() {
            match arg.as_str() {
                "--code" => code = rest.next().and_then(|value| parse_code(value)),
                "--node" => node = rest.next().cloned(),
                other => match other.strip_prefix("--code=") {
                    Some(value) => code = parse_code(value),
                    None => return Request::Usage,
                },
            }
        }
        match code {
            Some(code) => Request::Join { code, node },
            None => Request::Usage,
        }
    }
}

/// Picks the node to enrol with among those that answered a browse.
pub fn choose(adverts: &[Advert], named: Option<&str>, remembered: Option<&Node>) -> Result<Advert, String> {
    if adverts.is_empty() {
        return Err("No Mightling node answered on this network. Enrolment is done on the node's LAN: \
                    connect to it and run this again."
            .to_string());
    }
    if let Some(name) = named {
        return adverts
            .iter()
            .find(|advert| crate::node::matches(advert, name))
            .cloned()
            .ok_or_else(|| format!("No node named {name} answered on this network."));
    }
    if let Some(remembered) = remembered.filter(|node| !node.node.is_empty())
        && let Some(advert) = adverts.iter().find(|advert| advert.node.node == remembered.node)
    {
        return Ok(advert.clone());
    }
    if let [only] = adverts {
        return Ok(only.clone());
    }
    let main: Vec<&Advert> = adverts.iter().filter(|advert| advert.main).collect();
    match main.as_slice() {
        [only] => Ok((*only).clone()),
        _ => Err("Several nodes answered; name one: ling node remote join --code <digits> --node <name>".to_string()),
    }
}

/// Runs `ling node remote …` and returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let (code, named) = match Request::from_args(args) {
        Request::Join { code, node } => (code, node),
        Request::Usage => {
            println!("{USAGE}");
            return 2;
        }
    };
    if ling_node_locator::is_node() {
        eprintln!("This machine is a node: it joined the overlay at `ling-admin remote setup`.");
        return 1;
    }
    match join(&code, named.as_deref()).await {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            0
        }
        Err(message) => {
            eprintln!("❌ {message}");
            1
        }
    }
}

async fn join(code: &str, named: Option<&str>) -> Result<Vec<String>, String> {
    let adverts = MdnsBrowser.browse(None);
    let advert = choose(&adverts, named, ling_node_locator::remembered().as_ref())?;
    let address = advert
        .addresses
        .iter()
        .find(|address| address.parse::<std::net::Ipv4Addr>().is_ok())
        .or(advert.addresses.first())
        .cloned()
        .ok_or("the node answered without an address")?;
    let nonce = hex(&rand::random::<[u8; 16]>());
    let client_name = hostname();
    let client = crate::proxy::direct_client(Duration::from_secs(20)).map_err(|error| error.to_string())?;
    let url = format!("http://{}:{ENROL_PORT}{ENROL_PATH}", ling_node_locator::url_host(&address));
    let response = client
        .post(&url)
        .json(&serde_json::json!({"nonce": nonce, "proof": client_proof(code, &nonce), "client": client_name}))
        .send()
        .await
        .map_err(|_| format!("Node {} is not waiting for a code: run `ling-admin remote code` on it first.", advert.node.name))?;
    let status = response.status();
    let answer: serde_json::Value = response.json().await.unwrap_or_default();
    if status.as_u16() == 401 {
        let left = answer.get("attempts_left").and_then(|value| value.as_u64()).unwrap_or(0);
        return Err(format!("Wrong code ({left} attempts left before it is withdrawn)."));
    }
    if !status.is_success() {
        return Err(format!(
            "The node refused ({status}): {}",
            answer.get("error").and_then(|value| value.as_str()).unwrap_or("no reason given")
        ));
    }
    let bundle = verify(code, &nonce, &answer, &advert.node.node)?;
    println!("✅ Node {} sent its bundle; its overlay name is {}.", advert.node.name, bundle.overlay_name);

    let home = ling_node_locator::codex_home().ok_or("could not resolve CODEX_HOME")?;
    let folder = home.join("remote");
    std::fs::create_dir_all(&folder).map_err(|error| error.to_string())?;
    let ca_path = folder.join(format!("node-ca-{}.crt", bundle.node.chars().take(8).collect::<String>()));
    std::fs::write(&ca_path, format!("{}\n", bundle.ca.trim_end())).map_err(|error| error.to_string())?;
    let os = os_name();
    run_steps(
        "🔐 The node's CA goes into this system's trust store (name-constrained to the box and the overlay)",
        &trust_commands(os, &ca_path, &bundle.node, linux_tool())?,
    )?;

    let netbird = match installed_client() {
        Some(path) => path,
        None => {
            let (url, sha) = client_archive(os, arch_name()).ok_or(format!("NetBird's client is not pinned for {os}/{}", arch_name()))?;
            println!("⬇️  NetBird {NETBIRD_VERSION} from {url}");
            let data = client
                .get(&url)
                .timeout(Duration::from_secs(300))
                .send()
                .await
                .and_then(|response| response.error_for_status())
                .map_err(|error| format!("could not download NetBird's client: {error}"))?
                .bytes()
                .await
                .map_err(|error| error.to_string())?;
            let digest = hex(&Sha256::digest(&data));
            if digest != sha {
                return Err(format!("{url} has SHA-256 {digest}, not the pinned {sha}; nothing was installed"));
            }
            let name = if os == "windows" { "netbird.exe" } else { "netbird" };
            let binary = file_from_tar_gz(&data, name).ok_or(format!("{url} holds no {name}"))?;
            let staged = folder.join(name);
            std::fs::write(&staged, binary).map_err(|error| error.to_string())?;
            run_steps("📦 NetBird's client is installed and its service started", &install_commands(os, &staged))?;
            let _ = std::fs::remove_file(&staged);
            installed_client().ok_or("NetBird's client is not where it was installed")?
        }
    };
    let home = ling_node_locator::codex_home().ok_or("could not resolve CODEX_HOME")?;
    let key_file = write_key_file(&home, &bundle.setup_key)?;
    let joined = run_steps("🌐 This machine joins the overlay with the one-time key", &[up_command(&netbird, &bundle, &key_file)])
        .map_err(|error| error.replace(&bundle.setup_key, "<setup key>"));
    let _ = std::fs::remove_file(&key_file);
    joined?;

    let mut node = advert.node.clone();
    node.remote = bundle.overlay_name.clone();
    node.last_seen = chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false);
    crate::node::remember_enrolled(&node)?;
    Ok(vec![
        format!("✅ Enrolled. Off this LAN, ling reaches {} as {} while NetBird is connected.", advert.node.name, bundle.overlay_name),
        "   `netbird status` shows the overlay; on the node, `ling-admin remote peers` lists this machine.".to_string(),
    ])
}

fn hostname() -> String {
    std::env::var("HOSTNAME")
        .ok()
        .or_else(|| std::fs::read_to_string("/etc/hostname").ok())
        .or_else(|| std::env::var("COMPUTERNAME").ok())
        .map(|name| name.trim().chars().filter(|c| c.is_ascii_alphanumeric() || *c == '-').take(63).collect())
        .filter(|name: &String| !name.is_empty())
        .unwrap_or_else(|| "client".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    const NODE_ID: &str = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001";
    // Made the way the node makes its CA (remote_certificate_authority.py), constrained to
    // example.org and netbird.selfhosted, and the ways one could be made to vouch for more.
    const GOOD_CA: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBlDCCATqgAwIBAgIUQYiLH6Ase6KR+pYLH0M87QvBr50wCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o3oweDASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAz\n\
        BgNVHR4BAf8EKTAnoCUwDYILZXhhbXBsZS5vcmcwFIISbmV0YmlyZC5zZWxmaG9z\n\
        dGVkMB0GA1UdDgQWBBTq5bDiI7+cmIIoWBqH41ePLIuUVTAKBggqhkjOPQQDAgNI\n\
        ADBFAiEArqIdW7P3TiYK5kkM9Lxs/HHw7waDZWUZKbAAKJFdTQwCIDU15ieYWRCY\n\
        1sXiQhztvnGxbF0JMvaUGRRSUjmG43K6\n\
        -----END CERTIFICATE-----";
    const CA_WITHOUT_NAME_CONSTRAINTS: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBYDCCAQWgAwIBAgIUDh15IzVbQc/judCtvhaYx6F4z2cwCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o0UwQzASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAd\n\
        BgNVHQ4EFgQU6uWw4iO/nJiCKFgah+NXjyyLlFUwCgYIKoZIzj0EAwIDSQAwRgIh\n\
        ALHkhb9QKKwKpWS7Ddyrlk0748s9WjeMTLvktoQyqXG1AiEA6rjFV9zJm5YBVwxx\n\
        wGCnmGpAulv8pkMU7XFAEQPjPEk=\n\
        -----END CERTIFICATE-----";
    const CA_WITH_NON_CRITICAL_NAME_CONSTRAINTS: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBfDCCASGgAwIBAgIUSH2tSOZ2eWFctSOPB5C7eL8d1y4wCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o2EwXzASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAa\n\
        BgNVHR4EEzARoA8wDYILZXhhbXBsZS5vcmcwHQYDVR0OBBYEFOrlsOIjv5yYgihY\n\
        GofjV48si5RVMAoGCCqGSM49BAMCA0kAMEYCIQDJMOu3UuI1IGb01/bsBugsseDW\n\
        GQt/SnztOSGKSHlFygIhAL7EFtaP7Jou6srgW++N+2f8QCTs4IGHM8zzkn0b7ule\n\
        -----END CERTIFICATE-----";
    const CA_WITHOUT_PATHLEN: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBezCCASGgAwIBAgIUIJ8hQi5Flu73su479tMcf/8gkeUwCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o2EwXzAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAdBgNV\n\
        HR4BAf8EEzARoA8wDYILZXhhbXBsZS5vcmcwHQYDVR0OBBYEFOrlsOIjv5yYgihY\n\
        GofjV48si5RVMAoGCCqGSM49BAMCA0gAMEUCIQC/RoSomzNZeRy+PagS9l6mEP9L\n\
        Q0PXXIRUVQz1w86+eQIgdIgrvLaVM3nnNm66X0hIAezNSUlWARLGFqtkmhs7Uks=\n\
        -----END CERTIFICATE-----";
    const CA_PERMITTING_AN_IP_RANGE: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBijCCATCgAwIBAgIUJ9hmYCqxZO0HXquxDzrUjSvGdp0wCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o3AwbjASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAp\n\
        BgNVHR4BAf8EHzAdoBswDYILZXhhbXBsZS5vcmcwCocICgAAAP8AAAAwHQYDVR0O\n\
        BBYEFOrlsOIjv5yYgihYGofjV48si5RVMAoGCCqGSM49BAMCA0gAMEUCIQC4cnWy\n\
        nVbdkp8mcnYqHbYP4wGPxRIVSOTcx/dMuveybQIgNEqvJ79vHLBYVHCfBSDVnLUe\n\
        Vg+C85toyBpRifLfIOA=\n\
        -----END CERTIFICATE-----";
    const CA_PERMITTING_A_WHOLE_TLD: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBdjCCARygAwIBAgIULG9cYxzpuqNS3fLf75LOEvhfA6kwCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o1wwWjASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAV\n\
        BgNVHR4BAf8ECzAJoAcwBYIDb3JnMB0GA1UdDgQWBBTq5bDiI7+cmIIoWBqH41eP\n\
        LIuUVTAKBggqhkjOPQQDAgNIADBFAiEApq95qSgvcp8ioP60ErirZRxYA+bAaupy\n\
        8JtvZnYRr14CIAmssNYJgCeb/+dkJbPBoq6w1aKLnMc20jQwwXUm7web\n\
        -----END CERTIFICATE-----";
    const CA_WITH_AN_EXCLUDED_SUBTREE: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBkzCCATmgAwIBAgIUdOwEtYZBd0oFTI6O4cZaehKstPcwCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o3kwdzASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAy\n\
        BgNVHR4BAf8EKDAmoA8wDYILZXhhbXBsZS5vcmehEzARgg9iYWQuZXhhbXBsZS5v\n\
        cmcwHQYDVR0OBBYEFOrlsOIjv5yYgihYGofjV48si5RVMAoGCCqGSM49BAMCA0gA\n\
        MEUCIQClEEZTM9jaNCiIXuxbvX3zvbHaft5cGU2qK/LcMtQ/9QIgZ3QGpL1IEG7C\n\
        fLprtMe48ThiHe5MJjRHo8I1fg17Y7k=\n\
        -----END CERTIFICATE-----";
    const CA_WITH_A_COMMENT: &str = "-----BEGIN CERTIFICATE-----\n\
        MIIBkjCCATegAwIBAgIUN+Z5C1QwVPJuwyMJxIn9/NjvDwwwCgYIKoZIzj0EAwIw\n\
        DDEKMAgGA1UEAwwBdDAeFw0yNjEwMjkyMDM0NTBaFw0zNjEwMjYyMDM0NTBaMAwx\n\
        CjAIBgNVBAMMAXQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQpdHNEPm7q7lgS\n\
        TDXlc9rFvDOdWizQy37IUON2kjfteym4U2YYMsfuobYyZVmrX/S4MkPxFnZMETUz\n\
        JjzVa/z4o3cwdTASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAd\n\
        BgNVHR4BAf8EEzARoA8wDYILZXhhbXBsZS5vcmcwEQYJYIZIAYb4QgENBAQWAmhp\n\
        MB0GA1UdDgQWBBTq5bDiI7+cmIIoWBqH41ePLIuUVTAKBggqhkjOPQQDAgNJADBG\n\
        AiEAlnX3FcBS4oQL5em6KMFHYKrxdUhzuXE0XdfbRtOpUUkCIQD9JhEfkSw93SD9\n\
        2cULAb4Qixyr13eAGkTOe5dOZZCrrA==\n\
        -----END CERTIFICATE-----";
    const CA: &str = GOOD_CA;

    #[test]
    fn hmac_matches_rfc_4231_case_2() {
        let mac = hmac_sha256(b"Jefe", b"what do ya want for nothing?");
        assert_eq!(hex(&mac), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    }

    #[test]
    fn the_proofs_match_the_nodes_vectors() {
        // The same vectors are in tests/test_remote_access.py (the node's half).
        let nonce = "00112233445566778899aabbccddeeff";
        assert_eq!(client_proof("12345678", nonce), "5cc98f27431d7b9945a3a7d2215bb483ef347521c9a0106c73c40cc87a24129a");
        assert_eq!(server_mac("12345678", nonce, "{\"a\": 1}"), "95aac94aa3d5918feea3606ca4501a53022e70dd0e1bd320ff66a2570228f9de");
    }

    fn bundle_text() -> String {
        serde_json::json!({
            "version": 1, "node": NODE_ID, "name": "gx10-1a2b", "ca": CA,
            "management_url": "https://relay.example.org:443", "relay": "rels://relay.example.org:33080",
            "overlay_domain": "netbird.selfhosted",
            "overlay_name": format!("mightling-{NODE_ID}.netbird.selfhosted"),
            "netbird_version": "0.80.0", "setup_key": "A1B2C3D4-E5F6",
        })
        .to_string()
    }

    fn answer(code: &str, nonce: &str, text: &str) -> serde_json::Value {
        serde_json::json!({"bundle": text, "mac": server_mac(code, nonce, text)})
    }

    #[test]
    fn a_proven_bundle_for_the_browsed_node_is_accepted() {
        let nonce = "ab".repeat(16);
        let bundle = verify("12345678", &nonce, &answer("12345678", &nonce, &bundle_text()), NODE_ID).expect("accepted");
        assert_eq!(bundle.setup_key, "A1B2C3D4-E5F6");
        assert_eq!(bundle.management_url, "https://relay.example.org:443");
        assert_eq!(bundle.overlay_name, format!("mightling-{NODE_ID}.netbird.selfhosted"));
    }

    #[test]
    fn an_answer_without_the_code_or_for_another_node_is_refused() {
        let nonce = "ab".repeat(16);
        let text = bundle_text();
        let forged = answer("87654321", &nonce, &text);
        assert!(verify("12345678", &nonce, &forged, NODE_ID).unwrap_err().contains("does not prove the code"));
        let replayed = answer("12345678", &"cd".repeat(16), &text);
        assert!(verify("12345678", &nonce, &replayed, NODE_ID).is_err(), "another nonce's answer");
        let tampered = serde_json::json!({"bundle": text.replace("relay.example.org", "evil.example"), "mac": server_mac("12345678", &nonce, &text)});
        assert!(verify("12345678", &nonce, &tampered, NODE_ID).is_err());
        let other = verify("12345678", &nonce, &answer("12345678", &nonce, &text), "00000000-0000-0000-0000-000000000000");
        assert!(other.unwrap_err().contains("not the one browsed"));
    }

    #[test]
    fn a_proven_bundle_with_bad_contents_is_still_refused() {
        let nonce = "ab".repeat(16);
        for (key, value) in [
            ("overlay_name", "laptop.netbird.selfhosted".to_string()),
            ("management_url", "http://relay.example.org".to_string()),
            ("management_url", "https://relay.example.org/$(reboot)".to_string()),
            ("ca", "not a certificate".to_string()),
            ("setup_key", "key; rm -rf /".to_string()),
        ] {
            let mut value_json: serde_json::Value = serde_json::from_str(&bundle_text()).unwrap();
            value_json[key] = serde_json::Value::String(value.clone());
            let text = value_json.to_string();
            assert!(verify("12345678", &nonce, &answer("12345678", &nonce, &text), NODE_ID).is_err(), "{key}={value}");
        }
    }

    #[test]
    fn a_ca_that_could_vouch_for_other_sites_is_refused() {
        let nonce = "ab".repeat(16);
        let two = format!("{GOOD_CA}\n{GOOD_CA}");
        for (label, ca) in [
            ("no name constraints", CA_WITHOUT_NAME_CONSTRAINTS),
            ("non-critical name constraints", CA_WITH_NON_CRITICAL_NAME_CONSTRAINTS),
            ("no pathlen", CA_WITHOUT_PATHLEN),
            ("an IP range", CA_PERMITTING_AN_IP_RANGE),
            ("a whole top-level domain", CA_PERMITTING_A_WHOLE_TLD),
            ("an excluded subtree", CA_WITH_AN_EXCLUDED_SUBTREE),
            ("two certificates", two.as_str()),
        ] {
            let mut value: serde_json::Value = serde_json::from_str(&bundle_text()).unwrap();
            value["ca"] = serde_json::Value::String(ca.to_string());
            let text = value.to_string();
            assert!(verify("12345678", &nonce, &answer("12345678", &nonce, &text), NODE_ID).is_err(), "{label}");
        }
    }

    #[test]
    fn the_ca_must_be_constrained_to_the_management_host() {
        for host in ["relay.example.org", "example.org.", "Relay.Example.Org"] {
            assert_eq!(constrained_ca(GOOD_CA, host), Ok(()), "{host}");
        }
        // A comment is only text in the DER: it cannot pass for a constraint.
        assert_eq!(constrained_ca(CA_WITH_A_COMMENT, "relay.example.org"), Ok(()));
        for host in ["relay.example.net", "evilexample.org", "org", ""] {
            assert!(constrained_ca(GOOD_CA, host).is_err(), "{host}");
        }
    }

    #[test]
    fn an_empty_or_top_level_constraint_is_not_a_domain() {
        for entry in ["", "org", ".", "example.org.", ".example.org", "exa mple.org", "EXAMPLE.org"] {
            assert!(!constraint_entry_is_a_domain(entry), "{entry:?}");
        }
        assert!(constraint_entry_is_a_domain("example.org"));
        assert!(constraint_entry_is_a_domain("netbird.selfhosted"));
    }

    #[test]
    fn only_strict_der_is_read() {
        assert!(der(&[0x30, 0x80, 0x00, 0x00]).is_err(), "indefinite length");
        assert!(der(&[0x30, 0x81, 0x01, 0x00]).is_err(), "overlong length");
        assert!(der(&[0x30, 0x82, 0x00, 0x80]).is_err(), "length with a leading zero");
        assert!(der(&[0x30, 0x05, 0x00]).is_err(), "past the end");
        assert!(der(&[0x1f, 0x01, 0x00]).is_err(), "multi-byte tag");
        let body: String = GOOD_CA.lines().filter(|line| !line.starts_with("-----")).collect();
        let mut bytes = base64::engine::general_purpose::STANDARD.decode(body).unwrap();
        bytes.push(0);
        let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
        let trailing = format!("-----BEGIN CERTIFICATE-----\n{encoded}\n-----END CERTIFICATE-----");
        assert!(constrained_ca(&trailing, "relay.example.org").is_err(), "a byte past the certificate");
    }

    #[test]
    fn a_length_is_read_from_at_most_four_bytes_with_no_leading_zero() {
        let mut leading_zero = vec![0x04, 0x82, 0x00, 0x80];
        leading_zero.extend([0u8; 0x80]);
        assert!(der(&leading_zero).is_err(), "a leading zero even when the contents are there");
        let mut wrapping = vec![0x04, 0x89, 0x01, 0, 0, 0, 0, 0, 0, 0, 0x80];
        wrapping.extend([0u8; 0x80]);
        assert!(der(&wrapping).is_err(), "nine length bytes that wrap round to 0x80");
        assert_eq!(der_only(&[0x04, 0x00], 0x30), Err("the CA in the bundle is not a well-formed certificate".to_string()));
    }

    /// One DER element with `tag` around `contents`.
    fn tlv(tag: u8, contents: &[u8]) -> Vec<u8> {
        let mut out = vec![tag];
        match contents.len() {
            n if n < 0x80 => out.push(n as u8),
            n if n < 0x100 => out.extend([0x81, n as u8]),
            n => out.extend([0x82, (n >> 8) as u8, n as u8]),
        }
        out.extend(contents);
        out
    }

    fn extension(oid: &[u8], flag: Option<u8>, value: &[u8]) -> Vec<u8> {
        let mut fields = tlv(0x06, oid);
        if let Some(flag) = flag {
            fields.extend(tlv(0x01, &[flag]));
        }
        fields.extend(tlv(0x04, value));
        tlv(0x30, &fields)
    }

    /// Name constraints whose permitted subtrees are `subtrees`, under `tag` (0xa0 permitted).
    fn name_constraints(tag: u8, subtrees: &[Vec<u8>]) -> Vec<u8> {
        tlv(0x30, &tlv(tag, &subtrees.concat()))
    }

    fn dns(name: &str) -> Vec<u8> {
        tlv(0x30, &tlv(0x82, name.as_bytes()))
    }

    /// A certificate with only what `constrained_ca` reads: the extensions, no key or signature.
    fn certificate(tbs_tag: u8, lists: &[Vec<Vec<u8>>]) -> String {
        let tbs: Vec<u8> = lists.iter().map(|list| tlv(0xa3, &tlv(0x30, &list.concat()))).collect::<Vec<_>>().concat();
        let der = tlv(0x30, &tlv(tbs_tag, &[tlv(0x02, &[1]), tbs].concat()));
        let body = base64::engine::general_purpose::STANDARD.encode(der);
        format!("-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n")
    }

    fn constrained(subtrees: &[Vec<u8>]) -> Vec<Vec<u8>> {
        vec![
            extension(BASIC_CONSTRAINTS, Some(0xff), CA_PATHLEN_ZERO),
            extension(NAME_CONSTRAINTS, Some(0xff), &name_constraints(0xa0, subtrees)),
        ]
    }

    #[test]
    fn a_dns_constraint_may_have_a_leading_dot_and_any_case() {
        for name in ["example.org", ".example.org", "Example.ORG"] {
            assert_eq!(constrained_ca(&certificate(0x30, &[constrained(&[dns(name)])]), "relay.example.org"), Ok(()), "{name}");
        }
    }

    #[test]
    fn a_ca_read_by_hand_still_needs_each_check() {
        let host = "relay.example.org";
        let names = |subtrees: &[Vec<u8>]| extension(NAME_CONSTRAINTS, Some(0xff), &name_constraints(0xa0, subtrees));
        let basic = extension(BASIC_CONSTRAINTS, Some(0xff), CA_PATHLEN_ZERO);
        let good = constrained(&[dns("example.org")]);
        let mut subtree_with_a_minimum = tlv(0x82, b"example.org");
        subtree_with_a_minimum.extend(tlv(0x80, &[0]));
        for (label, ca) in [
            ("the to-be-signed part is not a SEQUENCE", certificate(0x31, std::slice::from_ref(&good))),
            ("two extension lists", certificate(0x30, &[good.clone(), good.clone()])),
            (
                "name constraints marked FALSE in the flag",
                certificate(0x30, &[vec![basic.clone(), extension(NAME_CONSTRAINTS, Some(0x00), &name_constraints(0xa0, &[dns("example.org")]))]]),
            ),
            ("name constraints twice", certificate(0x30, &[vec![basic.clone(), names(&[dns("example.org")]), names(&[dns("example.org")])]])),
            ("only an excluded subtree", certificate(0x30, &[vec![basic.clone(), extension(NAME_CONSTRAINTS, Some(0xff), &name_constraints(0xa1, &[dns("example.org")]))]])),
            ("an email domain", certificate(0x30, &[constrained(&[tlv(0x30, &tlv(0x81, b"example.org"))])])),
            ("a subtree that is a SET", certificate(0x30, &[constrained(&[tlv(0x31, &tlv(0x82, b"example.org"))])])),
            ("a subtree with a minimum", certificate(0x30, &[constrained(&[tlv(0x30, &subtree_with_a_minimum)])])),
        ] {
            assert!(constrained_ca(&ca, host).is_err(), "{label}");
        }
        assert_eq!(constrained_ca(&certificate(0x30, &[good]), host), Ok(()), "the same CA without the fault");
    }

    #[test]
    fn a_key_of_exactly_one_block_is_used_as_it_is() {
        assert_eq!(hex(&hmac_sha256(&[b'k'; 64], b"m")), "3b7a8d453e76edb519238a515105a57508d0f169f480ebeee120a9e7803289fa");
        assert_eq!(hex(&hmac_sha256(&[b'k'; 65], b"m")), "c2d7d4c2256ddb097409ec0c67e1d58fa1a007fc6932cac0d892a525d08af7cc");
    }

    #[test]
    fn a_code_is_eight_digits_exactly() {
        for bad in ["123456789", "1234567a", "1234567"] {
            assert_eq!(parse_code(bad), None, "{bad}");
        }
    }

    /// `verify` on the good bundle with `key` set to `value`.
    fn verify_with(key: &str, value: &str, node_id: &str) -> Result<Bundle, String> {
        let nonce = "ab".repeat(16);
        let mut json: serde_json::Value = serde_json::from_str(&bundle_text()).unwrap();
        json[key] = serde_json::Value::String(value.to_string());
        let text = json.to_string();
        verify("12345678", &nonce, &answer("12345678", &nonce, &text), node_id)
    }

    #[test]
    fn an_empty_or_short_proof_is_no_proof() {
        let nonce = "ab".repeat(16);
        let text = bundle_text();
        let mac = server_mac("12345678", &nonce, &text);
        for wrong in ["", &mac[..32], &mac[..63]] {
            let answer = serde_json::json!({"bundle": text, "mac": wrong});
            assert!(verify("12345678", &nonce, &answer, NODE_ID).unwrap_err().contains("does not prove the code"), "{wrong:?}");
        }
        let upper = serde_json::json!({"bundle": text, "mac": mac.to_ascii_uppercase()});
        assert!(verify("12345678", &nonce, &upper, NODE_ID).is_ok(), "a proof in capitals");
    }

    #[test]
    fn verify_accepts_what_only_differs_in_form() {
        assert!(verify_with("node", NODE_ID, "").is_ok(), "no node browsed: any node's bundle");
        let upper = format!("MIGHTLING-{}.NETBIRD.SELFHOSTED", NODE_ID.to_ascii_uppercase());
        assert_eq!(verify_with("overlay_name", &upper, NODE_ID).unwrap().overlay_name, format!("mightling-{NODE_ID}.netbird.selfhosted"));
    }

    #[test]
    fn verify_refuses_each_field_on_its_own() {
        let own = format!("mightling-{NODE_ID}");
        let cases = [
            ("overlay_domain", "evil.example".to_string(), "not the node's own overlay name"),
            ("overlay_domain", "d.selfhosted".to_string(), "not the node's own overlay name"),
            ("overlay_name", format!("{own}.evil.example"), "not the node's own overlay name"),
            ("management_url", "https://".to_string(), "is not an HTTPS URL"),
            ("management_url", "https://evil.example/.relay.example.org".to_string(), "is not an HTTPS URL"),
            ("ca", format!("-----END CERTIFICATE-----\n{GOOD_CA}"), "is not one certificate"),
            ("ca", format!("{GOOD_CA}\n-----"), "is not one certificate"),
            ("ca", GOOD_CA.replacen('\n', "\n-----BEGIN PRIVATE KEY-----\n", 1), "is not one certificate"),
            ("setup_key", String::new(), "setup key in the bundle is malformed"),
            ("setup_key", "A1B2_C3D4".to_string(), "setup key in the bundle is malformed"),
        ];
        for (key, value, error) in cases {
            let refused = verify_with(key, &value, NODE_ID).unwrap_err();
            assert!(refused.contains(error), "{key}={value:?}: {refused}");
        }
    }

    #[test]
    fn the_command_line_wants_join_and_eight_digits() {
        let args = |line: &str| line.split(' ').map(str::to_string).collect::<Vec<_>>();
        assert_eq!(Request::from_args(&args("join --code 12345678")), Request::Join { code: "12345678".into(), node: None });
        assert_eq!(
            Request::from_args(&args("join --code=1234-5678 --node gx10")),
            Request::Join { code: "12345678".into(), node: Some("gx10".into()) }
        );
        for bad in ["join", "join --code 1234567", "join --code abcdefgh", "leave --code 12345678", "join --code 12345678 --x"] {
            assert_eq!(Request::from_args(&args(bad)), Request::Usage, "{bad}");
        }
        assert_eq!(parse_code("1234 5678").as_deref(), Some("12345678"));
    }

    #[test]
    fn the_ca_goes_into_each_systems_own_store() {
        let ca = Path::new("/home/u/.mightling/remote/node-ca-7c1e0c7a.crt");
        let debian = trust_commands("linux", ca, NODE_ID, Some("update-ca-certificates")).unwrap();
        assert_eq!(debian[0].last().unwrap(), "/usr/local/share/ca-certificates/mightling-node-7c1e0c7a.crt");
        assert_eq!(debian[1], vec!["sudo", "update-ca-certificates"]);
        let fedora = trust_commands("linux", ca, NODE_ID, Some("update-ca-trust")).unwrap();
        assert!(fedora[0].last().unwrap().starts_with("/etc/pki/ca-trust/source/anchors/"));
        let mac = trust_commands("darwin", ca, NODE_ID, None).unwrap();
        assert_eq!(mac[0][..6], ["sudo", "security", "add-trusted-cert", "-d", "-r", "trustRoot"]);
        assert!(mac[0].contains(&"/Library/Keychains/System.keychain".to_string()));
        let windows = trust_commands("windows", ca, NODE_ID, None).unwrap();
        assert_eq!(windows[0][..4], ["certutil", "-addstore", "-f", "Root"]);
        assert!(trust_commands("linux", ca, NODE_ID, None).is_err());
    }

    #[test]
    fn every_supported_client_archive_is_pinned() {
        for os in ["linux", "darwin", "windows"] {
            for arch in ["amd64", "arm64"] {
                let (url, sha) = client_archive(os, arch).expect("pinned");
                assert!(url.ends_with(&format!("netbird_0.80.0_{os}_{arch}.tar.gz")), "{url}");
                assert_eq!(sha.len(), 64);
            }
        }
        assert_eq!(client_archive("freebsd", "amd64"), None);
    }

    #[test]
    fn the_binary_comes_out_of_the_archive_by_name() {
        let mut tar = Vec::new();
        for (name, data) in [("LICENSE", b"text".to_vec()), ("netbird", b"\x7fELF client".to_vec())] {
            let mut header = [0u8; 512];
            header[..name.len()].copy_from_slice(name.as_bytes());
            let size = format!("{:011o}\0", data.len());
            header[124..136].copy_from_slice(size.as_bytes());
            header[156] = b'0';
            tar.extend_from_slice(&header);
            tar.extend_from_slice(&data);
            tar.resize(tar.len().div_ceil(512) * 512, 0);
        }
        tar.extend_from_slice(&[0u8; 1024]);
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        std::io::Write::write_all(&mut gz, &tar).unwrap();
        let data = gz.finish().unwrap();
        assert_eq!(file_from_tar_gz(&data, "netbird"), Some(b"\x7fELF client".to_vec()));
        assert_eq!(file_from_tar_gz(&data, "netbird.exe"), None);
    }

    #[test]
    fn unix_installs_root_owned_and_windows_into_program_files() {
        let linux = install_commands("linux", Path::new("/tmp/x/netbird"));
        assert_eq!(linux[0], vec!["sudo", "install", "-m", "0755", "/tmp/x/netbird", "/usr/local/bin/netbird"]);
        assert_eq!(linux[1][1..], ["/usr/local/bin/netbird", "service", "install"]);
        let windows = install_commands("windows", Path::new("C:\\t\\netbird.exe"));
        assert!(windows[1].last().unwrap().ends_with("\\Mightling\\NetBird\\netbird.exe"));
        assert_eq!(windows[2][1..], ["service", "install"]);
    }

    #[test]
    fn up_uses_the_box_and_the_one_time_key_without_sudo() {
        let bundle = Bundle { management_url: "https://relay.example.org:443".into(), setup_key: "K".into(), ..Default::default() };
        let key_file = Path::new("/home/u/.mightling/remote-setup-key");
        assert_eq!(
            up_command("/usr/local/bin/netbird", &bundle, key_file),
            ["/usr/local/bin/netbird", "up", "--management-url", "https://relay.example.org:443", "--setup-key-file", "/home/u/.mightling/remote-setup-key"]
        );
    }

    #[test]
    fn the_key_file_is_private_and_holds_only_the_key() {
        let folder = std::env::temp_dir().join(format!("ling-remote-key-{}", std::process::id()));
        let path = write_key_file(&folder, "K-1").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "K-1\n");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        }
        let _ = std::fs::remove_dir_all(&folder);
    }

    fn advert(id: &str, name: &str, main: bool) -> Advert {
        Advert {
            node: Node { node: id.into(), name: name.into(), address: "192.168.50.5".into(), model_port: 8000, ..Node::default() },
            addresses: vec!["192.168.50.5".into()],
            proto: 1,
            state: "ready".into(),
            main,
        }
    }

    #[test]
    fn enrolment_needs_the_lan_and_a_clear_choice_of_node() {
        assert!(choose(&[], None, None).unwrap_err().contains("node's LAN"));
        let one = advert(NODE_ID, "gx10", true);
        assert_eq!(choose(std::slice::from_ref(&one), None, None).unwrap().node.name, "gx10");
        let two = [one.clone(), advert("aaaa1111", "spark", true)];
        assert!(choose(&two, None, None).is_err());
        assert_eq!(choose(&two, Some("spark"), None).unwrap().node.name, "spark");
        let remembered = Node { node: NODE_ID.into(), ..Node::default() };
        assert_eq!(choose(&two, None, Some(&remembered)).unwrap().node.name, "gx10");
        let speech = [one.clone(), advert("bbbb2222", "speech", false)];
        assert_eq!(choose(&speech, None, None).unwrap().node.name, "gx10", "one coding node among several");
    }
}
