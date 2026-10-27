//! `ling sync …` (specs/DREAMFERENCE_MIGHTLING_SYNC.md §11):
//!
//! - `enable [--code N] [--name NAME] [--port N]`: on the node, turns the hub on in `ling web` and
//!   starts this machine's own agent; on another device, pairs with the node's hub using a code
//!   from `ling web pair` there, and starts the agent. The agent runs as the user unit
//!   `mightling-sync.service` on Linux.
//! - `status [--json]`: the hub (on the node) and the agent: devices, last cycle, what is waiting.
//! - `disable`: stops the agent and, on the node, turns the hub off; keeps every file.
//! - `forget`: on the node, deletes the hub's store; on a device, its pairing and its state.
//! - `exclude <folder>`: threads that ran there never leave this device (`[sync] exclude`).
//! - `agent [--once]`: the agent in the foreground; what the unit runs.
//! - `handover`: Phase 3, not built.
//!
//! Nothing here runs, listens or writes before `enable`: a device that never ran it has no unit, no
//! `~/.mightling/sync/` and a hub whose routes answer 404.

use std::io::IsTerminal;
use std::io::Write;
use std::net::IpAddr;
use std::net::ToSocketAddrs;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;

use ling_web_server::auth;
use ling_web_server::sync_store;
use serde_json::Value;
use serde_json::json;

use crate::agent;
use crate::agent::Agent;
use crate::client;
use crate::client::HubClient;
use crate::config;
use crate::config::DeviceFile;
use crate::config::Settings;

pub const UNIT_NAME: &str = "mightling-sync.service";

const USAGE: &str = "Usage: ling sync enable [--code N] [--name NAME] [--port N] | status [--json] | disable | forget | exclude <folder> | agent [--once] | handover <thread>";

/// Finds the node's host (a name or an address) by the node tiers, without asking its model server.
pub type Resolver = Arc<dyn Fn() -> Result<String, String> + Send + Sync>;

/// What the launcher tells `ling sync`.
#[derive(Clone)]
pub struct Environment {
    /// `ling`'s home folder (`~/.mightling`).
    pub codex_home: PathBuf,
    /// The user's home folder: the user unit lives under it.
    pub home: PathBuf,
    /// The `ling` binary: the unit runs it, and the agent runs Codex's `delete` and `archive` with it.
    pub ling: PathBuf,
    /// Whether this machine is a node: its hub is its own `ling web`.
    pub is_node: bool,
    pub resolve_node: Resolver,
}

impl Environment {
    fn web_state(&self) -> PathBuf {
        auth::state_dir(&self.codex_home)
    }
}

fn flag_value<'a>(args: &'a [String], flag: &str) -> Option<&'a str> {
    args.iter().position(|arg| arg == flag).and_then(|index| args.get(index + 1)).map(String::as_str)
}

pub async fn run_cli(args: &[String], environment: Environment) -> i32 {
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.first().copied() {
        Some("enable") => enable(&args[1..], &environment).await,
        Some("status") => status(&environment, words.contains(&"--json")),
        Some("disable") => disable(&environment),
        Some("forget") => forget(&environment).await,
        Some("exclude") => match words.get(1) {
            Some(folder) => exclude(folder),
            None => {
                eprintln!("Usage: ling sync exclude <folder>");
                2
            }
        },
        Some("agent") => run_agent(&environment, words.contains(&"--once")).await,
        Some("handover") => {
            eprintln!("Continuing a thread on another device is not built yet (specs/DREAMFERENCE_MIGHTLING_SYNC.md §6.4, Phase 3).");
            2
        }
        Some("-h" | "--help" | "help") => {
            println!("{USAGE}");
            0
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

/// This computer's name, for the hub's list of devices.
fn host_name() -> String {
    let from_file = std::fs::read_to_string("/etc/hostname").ok().map(|name| name.trim().to_string()).filter(|name| !name.is_empty());
    from_file
        .or_else(|| {
            std::process::Command::new("hostname")
                .output()
                .ok()
                .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
                .filter(|name| !name.is_empty())
        })
        .unwrap_or_else(|| "this computer".to_string())
}

/// Whether an address is the user's own network: loopback, a private range, link-local, the
/// overlay's carrier-grade range (100.64/10) or an IPv6 unique-local one.
pub fn private_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            let [a, b, ..] = v4.octets();
            v4.is_loopback() || v4.is_private() || v4.is_link_local() || (a == 100 && (b & 0xc0) == 64)
        }
        IpAddr::V6(v6) => {
            let first = v6.segments()[0];
            v6.is_loopback() || (first & 0xfe00) == 0xfc00 || (first & 0xffc0) == 0xfe80 || v6.to_ipv4_mapped().is_some_and(|v4| private_ip(IpAddr::V4(v4)))
        }
    }
}

/// The hub's base URL on a host that is the user's own (§9): every address the host resolves to
/// must be private, so sync never reaches anything but the node, at any air-gap level.
pub fn hub_base(host: &str, port: u16) -> Result<String, String> {
    let bare = host.trim().trim_start_matches('[').trim_end_matches(']');
    let addresses: Vec<IpAddr> = (bare, port)
        .to_socket_addrs()
        .map_err(|err| format!("could not resolve {bare}: {err}"))?
        .map(|address| address.ip())
        .collect();
    if addresses.is_empty() {
        return Err(format!("{bare} resolves to nothing"));
    }
    if let Some(public) = addresses.iter().find(|ip| !private_ip(**ip)) {
        return Err(format!(
            "{bare} is at {public}, which is not on your own network: sync reaches only your node, on the LAN or the overlay"
        ));
    }
    let host = if bare.contains(':') { format!("[{bare}]") } else { bare.to_string() };
    Ok(format!("http://{host}:{port}"))
}

/// The host part of a URL the node tiers gave (`http://host:8000/v1`), or the host itself.
pub fn host_of(url: &str) -> String {
    let rest = url.split("://").nth(1).unwrap_or(url);
    let authority = rest.split('/').next().unwrap_or(rest);
    if let Some(stripped) = authority.strip_prefix('[') {
        return stripped.split(']').next().unwrap_or(stripped).to_string();
    }
    authority.rsplit_once(':').map_or(authority, |(host, _)| host).to_string()
}

async fn resolve_base(device: &DeviceFile, environment: &Environment) -> Result<String, String> {
    if device.local {
        return Ok(format!("http://127.0.0.1:{}", device.port));
    }
    let resolve = environment.resolve_node.clone();
    let port = device.port;
    tokio::task::spawn_blocking(move || resolve().and_then(|host| hub_base(&host_of(&host), port)))
        .await
        .map_err(|err| err.to_string())?
}

fn unit_path(home: &Path) -> PathBuf {
    home.join(".config/systemd/user").join(UNIT_NAME)
}

/// The user unit: the agent in the foreground, bounded in memory because it runs beside a resident
/// model server on the node (AGENTS.md, host safety).
pub fn unit_text(ling: &Path) -> String {
    format!(
        "# Written by `ling sync enable`; `ling sync disable` stops and removes it.\n\
         [Unit]\n\
         Description=Mightling sync agent (ling sync)\n\
         \n\
         [Service]\n\
         ExecStart={} sync agent\n\
         Restart=on-failure\n\
         RestartSec=5\n\
         MemoryMax=256M\n\
         Nice=10\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        ling.display()
    )
}

fn systemctl(args: &[&str]) -> Result<(), String> {
    let status = std::process::Command::new("systemctl")
        .arg("--user")
        .args(args)
        .status()
        .map_err(|err| format!("could not run systemctl: {err}"))?;
    if status.success() { Ok(()) } else { Err(format!("systemctl --user {} failed ({status})", args.join(" "))) }
}

fn start_unit(environment: &Environment) -> Result<(), String> {
    if !cfg!(target_os = "linux") {
        println!("Run the agent with `ling sync agent` (it is not installed as a service on this system yet).");
        return Ok(());
    }
    let unit = unit_path(&environment.home);
    unit.parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| std::fs::write(&unit, unit_text(&environment.ling)))
        .map_err(|err| format!("could not write {}: {err}", unit.display()))?;
    systemctl(&["daemon-reload"])?;
    systemctl(&["enable", "--now", UNIT_NAME])?;
    systemctl(&["restart", UNIT_NAME])
}

fn read_code() -> Option<String> {
    if !std::io::stdin().is_terminal() {
        return None;
    }
    print!("Pairing code (from `ling web pair` on the node): ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    std::io::stdin().read_line(&mut line).ok()?;
    Some(line.trim().replace(' ', "")).filter(|code| !code.is_empty())
}

async fn enable(args: &[String], environment: &Environment) -> i32 {
    let no_unit = args.iter().any(|arg| arg == "--no-unit");
    let web_state = environment.web_state();
    let port = match flag_value(args, "--port").map(str::parse::<u16>) {
        None => ling_web_server::cli::running_port(&web_state),
        Some(Ok(port)) => port,
        Some(Err(_)) => {
            eprintln!("--port takes a port number");
            return 2;
        }
    };
    if Settings::load().replicate {
        println!("[sync] replicate is not built yet (Phase 4): other devices' threads are read from the node, not copied here.");
    }
    let codex_home = &environment.codex_home;
    if environment.is_node {
        if let Err(err) = sync_store::set_enabled(codex_home, true) {
            eprintln!("Could not turn the hub on: {err}");
            return 1;
        }
        if DeviceFile::load(codex_home).is_none() {
            let name = format!("{} (node)", host_name());
            let paired = auth::add_device_of(&web_state, &name, auth::DeviceKind::Sync).and_then(|(device, secret)| {
                DeviceFile { device, name: auth::clean_device_name(&name), secret, port, local: true }.save(codex_home)
            });
            if let Err(err) = paired {
                eprintln!("Could not pair this machine's own agent: {err}");
                return 1;
            }
        }
        println!("The sync hub is on in this node's `ling web`.");
        match ling_web_server::cli::probe(&web_state, port) {
            Some(health) if health.get("lan").and_then(Value::as_bool) == Some(true) => {
                println!("Pair another computer: run `ling web pair` here, then `ling sync enable` there and type the code.");
            }
            Some(_) => println!("`ling web` answers on loopback only; for other devices, `ling web start --lan` (an advertised node)."),
            None => println!("`ling web` is not running: start it with `ling web start --lan`."),
        }
    } else {
        match DeviceFile::load(codex_home) {
            Some(device) => println!("This device is already paired with the hub as {} ({}).", device.name, device.device),
            None => {
                let host = match tokio::task::spawn_blocking({
                    let resolve = environment.resolve_node.clone();
                    move || resolve()
                })
                .await
                .map_err(|err| err.to_string())
                .and_then(|found| found)
                {
                    Ok(host) => host_of(&host),
                    Err(err) => {
                        eprintln!("Could not find your node: {err}");
                        return 1;
                    }
                };
                let base = match hub_base(&host, port) {
                    Ok(base) => base,
                    Err(err) => {
                        eprintln!("{err}");
                        return 1;
                    }
                };
                let Some(code) = flag_value(args, "--code").map(|code| code.replace(' ', "")).or_else(read_code) else {
                    eprintln!("Give the pairing code from `ling web pair` on the node: ling sync enable --code <8 digits>");
                    return 2;
                };
                let name = flag_value(args, "--name").map(str::to_string).unwrap_or_else(host_name);
                let (id, secret) = match client::pair(&base, &code, &name).await {
                    Ok(paired) => paired,
                    Err(err) => {
                        eprintln!("Could not pair with the hub: {err}");
                        return 1;
                    }
                };
                let device = DeviceFile { device: id, name: auth::clean_device_name(&name), secret, port, local: false };
                if let Err(err) = device.save(codex_home) {
                    eprintln!("Could not keep the pairing: {err}");
                    return 1;
                }
                println!("Paired with the hub on {base} as {} ({}).", device.name, device.device);
            }
        }
    }
    if no_unit {
        println!("Start the agent with `ling sync agent`.");
        return 0;
    }
    match start_unit(environment) {
        Ok(()) => {
            println!("The sync agent is running ({UNIT_NAME}); `ling sync status` shows what it does.");
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

fn ago(seconds: u64) -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    ling_web_server::devices::ago(seconds, now)
}

fn status(environment: &Environment, as_json: bool) -> i32 {
    let codex_home = &environment.codex_home;
    let hub = sync_store::hub_dir(codex_home).join("hub.json").exists().then(|| {
        // Read, never opened: opening repairs files, which only the running `ling web` may do.
        let (threads, bytes) = sync_store::summary(codex_home);
        let agents = auth::load_devices(&environment.web_state()).iter().filter(|device| device.kind == auth::DeviceKind::Sync).count();
        json!({ "enabled": sync_store::enabled(codex_home), "threads": threads, "bytes": bytes, "devices": agents })
    });
    let device = DeviceFile::load(codex_home);
    let last: Option<Value> = std::fs::read_to_string(agent::status_path(codex_home)).ok().and_then(|text| serde_json::from_str(&text).ok());
    let unit = cfg!(target_os = "linux")
        && std::process::Command::new("systemctl")
            .args(["--user", "is-active", "--quiet", UNIT_NAME])
            .status()
            .is_ok_and(|status| status.success());
    if as_json {
        let summary = json!({
            "hub": hub,
            "device": device.as_ref().map(|d| json!({ "device": d.device, "name": d.name, "local": d.local, "port": d.port })),
            "agent": { "running": unit, "last": last },
        });
        println!("{summary}");
        return 0;
    }
    if hub.is_none() && device.is_none() {
        println!("Sync is off on this device. `ling sync enable` turns it on (on the node first).");
        return 3;
    }
    if let Some(hub) = &hub {
        if hub["enabled"] == Value::Bool(true) {
            println!(
                "Hub: on, {} thread(s), {} bytes, {} sync agent(s) paired.",
                hub["threads"].as_u64().unwrap_or(0),
                hub["bytes"].as_u64().unwrap_or(0),
                hub["devices"].as_u64().unwrap_or(0)
            );
        } else {
            println!("Hub: off (its store is kept; `ling sync enable` turns it on, `ling sync forget` deletes it).");
        }
    }
    if let Some(device) = &device {
        println!("Agent: this device is {} ({}), {}.", device.name, device.device, if unit { "running" } else { "not running" });
        if let Some(last) = &last {
            println!(
                "Last cycle {}: {} thread(s) published, {} rollout(s) waiting, hub {}.",
                ago(last["at"].as_u64().unwrap_or(0)),
                last["published"].as_u64().unwrap_or(0),
                last["pending"].as_u64().unwrap_or(0),
                last["hub"].as_str().unwrap_or("unknown")
            );
            for error in last["errors"].as_array().cloned().unwrap_or_default() {
                println!("  {}", error.as_str().unwrap_or_default());
            }
        }
    }
    0
}

fn disable(environment: &Environment) -> i32 {
    let mut code = 0;
    let unit = unit_path(&environment.home);
    if unit.exists() {
        if let Err(err) = systemctl(&["disable", "--now", UNIT_NAME]) {
            eprintln!("{err}");
            code = 1;
        }
        let _ = std::fs::remove_file(&unit);
        let _ = systemctl(&["daemon-reload"]);
    }
    if sync_store::enabled(&environment.codex_home) {
        if let Err(err) = sync_store::set_enabled(&environment.codex_home, false) {
            eprintln!("Could not turn the hub off: {err}");
            code = 1;
        } else {
            println!("The sync hub is off; its store is kept (`ling sync forget` deletes it).");
        }
    }
    println!("The sync agent is stopped; this device's pairing and files are kept.");
    code
}

async fn forget(environment: &Environment) -> i32 {
    let code = disable(environment);
    let codex_home = &environment.codex_home;
    if sync_store::hub_dir(codex_home).exists() {
        match sync_store::forget(codex_home) {
            Ok(()) => println!("Deleted the hub's store."),
            Err(err) => {
                eprintln!("Could not delete the hub's store: {err}");
                return 1;
            }
        }
    }
    if let Some(device) = DeviceFile::load(codex_home) {
        if device.local {
            let _ = auth::revoke_device(&environment.web_state(), &device.device);
        } else if let Ok(base) = resolve_base(&device, environment).await
            && let Ok(hub) = HubClient::new(&base, &device.secret)
            && hub.forget().await.is_err()
        {
            println!("The hub did not answer; revoke this device there with `ling web revoke {}`.", device.device);
        }
    }
    let dir = config::sync_dir(codex_home);
    for name in ["device.toml", "agent.json", "agent-status.json"] {
        let _ = std::fs::remove_file(dir.join(name));
    }
    let _ = std::fs::remove_dir(&dir);
    println!("This device's sync pairing and state are gone.");
    code
}

fn exclude(folder: &str) -> i32 {
    let path = PathBuf::from(folder);
    let path = if path.is_absolute() { path } else { std::env::current_dir().map(|cwd| cwd.join(&path)).unwrap_or(path) };
    let path = path.canonicalize().unwrap_or(path);
    let Some(config) = ling_airgapped::user_config_file() else {
        eprintln!("HOME is not set.");
        return 1;
    };
    match config::add_exclude(&config, &path) {
        Ok(true) => {
            println!("Threads that run in {} stay on this device; any already on the hub are taken off it.", path.display());
            0
        }
        Ok(false) => {
            println!("{} is already excluded.", path.display());
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

/// The agent's loop: a cycle a second until stopped, finding the hub again after five failures in
/// a row (the node moved, or `ling web` restarted on another address).
async fn run_agent(environment: &Environment, once: bool) -> i32 {
    let Some(mut agent) = Agent::load(&environment.codex_home, &environment.ling, Settings::load()) else {
        eprintln!("Sync is not enabled on this device: `ling sync enable`.");
        return 3;
    };
    let mut hub: Option<HubClient> = None;
    // The agent as a host (live.rs), one per hub address: started when the hub is found, and
    // started again when the hub is found somewhere else.
    let mut host: Option<(String, tokio::task::JoinHandle<()>)> = None;
    let mut failures = 0u32;
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).ok();
    loop {
        agent.settings = Settings::load();
        if hub.is_none() {
            match resolve_base(&agent.device, environment).await.and_then(|base| HubClient::new(&base, &agent.device.secret)) {
                Ok(client) => hub = Some(client),
                Err(err) => {
                    let report = agent::Report { errors: vec![err.clone()], ..agent::Report::default() };
                    agent.write_status("", &report, 0);
                    if once {
                        eprintln!("{err}");
                        return 1;
                    }
                }
            }
        }
        if let Some(client) = &hub
            && !once
            && host.as_ref().is_none_or(|(base, _)| base != client.base())
        {
            if let Some((_, task)) = host.take() {
                task.abort();
            }
            if let Ok(live_hub) = HubClient::new(client.base(), &agent.device.secret) {
                let task = tokio::spawn(crate::live::run(environment.codex_home.clone(), Arc::new(live_hub)));
                host = Some((client.base().to_string(), task));
            }
        }
        if let Some(client) = &hub {
            let report = agent.cycle(client).await;
            let pending = agent.pending(&crate::scan::scan(&environment.codex_home));
            agent.write_status(client.base(), &report, pending);
            if report.errors.iter().any(|error| error.contains("could not reach the hub")) {
                failures += 1;
                if failures >= 5 {
                    hub = None;
                    failures = 0;
                }
            } else {
                failures = 0;
            }
            if once {
                for error in &report.errors {
                    eprintln!("{error}");
                }
                return i32::from(!report.errors.is_empty());
            }
        }
        let pause = if hub.is_some() { agent::INTERVAL } else { std::time::Duration::from_secs(30) };
        tokio::select! {
            _ = tokio::time::sleep(pause) => {}
            _ = tokio::signal::ctrl_c() => return 0,
            _ = async { match terminate.as_mut() { Some(signal) => { signal.recv().await; } None => std::future::pending::<()>().await } } => return 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_users_own_network_is_a_hub_address() {
        for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.50.105", "169.254.1.1", "100.64.0.1", "100.101.102.103", "::1", "fd12::1", "fe80::1"] {
            assert!(private_ip(ip.parse().unwrap()), "{ip}");
        }
        for ip in ["8.8.8.8", "100.128.0.1", "172.32.0.1", "2001:4860::8888", "1.1.1.1"] {
            assert!(!private_ip(ip.parse().unwrap()), "{ip}");
        }
        assert_eq!(hub_base("192.168.50.105", 3100).unwrap(), "http://192.168.50.105:3100");
        assert_eq!(hub_base("[::1]", 3100).unwrap(), "http://[::1]:3100");
        assert!(hub_base("8.8.8.8", 3100).unwrap_err().contains("not on your own network"));
    }

    #[test]
    fn the_node_tiers_give_a_url_and_sync_takes_its_host() {
        assert_eq!(host_of("http://192.168.50.105:8000/v1"), "192.168.50.105");
        assert_eq!(host_of("http://gx10-1a2b.local:8000"), "gx10-1a2b.local");
        assert_eq!(host_of("http://[fd00::5]:8000/v1"), "fd00::5");
        assert_eq!(host_of("gx10.local"), "gx10.local");
    }

    #[test]
    fn the_unit_runs_the_agent_bounded() {
        let text = unit_text(Path::new("/home/u/.local/bin/ling"));
        assert!(text.contains("ExecStart=/home/u/.local/bin/ling sync agent\n"));
        assert!(text.contains("MemoryMax=256M\n"));
    }
}
