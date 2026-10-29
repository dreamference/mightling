//! `ling web …` (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.1).
//!
//! - `serve [--lan] [--port N]`: the server in the foreground; what the user unit runs.
//! - `start [--lan] [--port N]`, `stop`: the user unit `mightling-web.service` (on macOS the launchd
//!   agent `ai.dreamference.mightling-web` in `~/Library/LaunchAgents`). `--port` is for a
//!   second Linux user on the node, whose server cannot also take 3100 (a Signal bridge of their
//!   own answers through it, specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §18).
//! - `status`: whether it answers, where, and how many devices are paired.
//! - `open [--no-browser | --print-url]`: signs this machine's browser in with a one-time link;
//!   `--print-url` prints the link alone, for the desktop app's Ask window.
//! - `pair`: an eight-digit code for another device, for ten minutes, and in a terminal its QR
//!   code (the Devices page, `/devices`, shows the same).
//! - `devices`, `revoke <device>`: paired devices (browsers and sync agents), and ending one;
//!   `devices --scope <device> watch|control` narrows a device to watching other devices' threads,
//!   or gives it control again (specs/DREAMFERENCE_MIGHTLING_SYNC.md §5.1).
//! - `ask [--port N] <question>`: one Ask thread through the running server, for scripts and the
//!   egress audit (ask_client.rs).
//!
//! `--lan` is refused unless this machine is an advertised node (`ling-admin node enable`), the
//! one case in which Mightling offers anything to the network.

use std::io::IsTerminal;
use std::io::Read;
use std::io::Write;
use std::net::SocketAddr;
use std::net::TcpStream;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;
use serde_json::json;

use crate::app_server;
use crate::app_server::Launch;
use crate::auth;
use crate::qr;
use crate::server;
use crate::server::Config;
use crate::server::DEFAULT_PORT;
use crate::server::Server;

pub const UNIT_NAME: &str = "mightling-web.service";

const USAGE: &str = "Usage: ling web start [--lan] [--port N] | stop | status | open [--no-browser | --print-url] | pair | devices [--scope <device> watch|control] | revoke <device> | serve [--lan] [--port N] | ask [--port N] <question>";

/// What the launcher tells `ling web`.
#[derive(Clone, Debug)]
pub struct Environment {
    /// `ling`'s home folder (`~/.mightling`).
    pub codex_home: PathBuf,
    /// The user's home folder: the node's settings and the user unit live under it.
    pub home: PathBuf,
    /// The `ling` binary.
    pub ling: PathBuf,
    /// Environment for the app-server it starts: the model server, so the child never browses.
    pub child_env: Vec<(String, String)>,
}

impl Environment {
    pub fn state_dir(&self) -> PathBuf {
        auth::state_dir(&self.codex_home)
    }
}

/// Whether this machine is an advertised node that shares its web UI
/// (`~/.config/dreamference/node-advertise.json`, written by `ling-admin node enable`).
pub fn advertised_web(home: &Path) -> bool {
    let path = home.join(".config/dreamference/node-advertise.json");
    let Ok(text) = std::fs::read_to_string(path) else { return false };
    let Ok(value) = serde_json::from_str::<Value>(&text) else { return false };
    value.get("advertise") == Some(&Value::Bool(true)) && value.get("web") != Some(&Value::Bool(false))
}

/// This node's name in the remote-access overlay (`~/.config/dreamference/remote/remote.json`,
/// written by `ling-admin remote setup`), once the node is a peer: an enrolled client off the LAN
/// opens `http://<that name>:3100/` (REMOTE_ACCESS §4.3). Only a name of the node's own shape
/// (`mightling-<id>.<domain>`, letters, digits, dots and hyphens) is taken.
pub fn overlay_name(home: &Path) -> Option<String> {
    let text = std::fs::read_to_string(home.join(".config/dreamference/remote/remote.json")).ok()?;
    let value = serde_json::from_str::<Value>(&text).ok()?;
    if value.get("node_peer") != Some(&Value::Bool(true)) {
        return None;
    }
    let name = value.get("overlay_name")?.as_str()?.trim().to_ascii_lowercase();
    let plain = name.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-');
    (plain && name.starts_with("mightling-") && name.contains('.')).then_some(name)
}

/// This machine's addresses and names on the network: what a device on the LAN may put in `Host`.
pub fn lan_names() -> Vec<String> {
    let mut names = Vec::new();
    #[cfg(unix)]
    unsafe {
        let mut list: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut list) == 0 {
            let mut entry = list;
            while !entry.is_null() {
                let address = (*entry).ifa_addr;
                if !address.is_null() && i32::from((*address).sa_family) == libc::AF_INET {
                    let ipv4 = &*(address as *const libc::sockaddr_in);
                    let ip = std::net::Ipv4Addr::from(u32::from_be(ipv4.sin_addr.s_addr));
                    if !ip.is_loopback() && !ip.is_unspecified() {
                        names.push(ip.to_string());
                    }
                }
                entry = (*entry).ifa_next;
            }
            libc::freeifaddrs(list);
        }
    }
    if let Ok(host) = std::fs::read_to_string("/etc/hostname") {
        let host = host.trim().to_ascii_lowercase();
        if !host.is_empty() {
            names.push(format!("{host}.local"));
            names.push(host);
        }
    }
    names.sort();
    names.dedup();
    names
}

fn server_file(state: &Path) -> PathBuf {
    state.join("server.json")
}

/// Removes `server.json` at exit, when this process (`pid`) wrote it.
fn remove_server_file_if_ours(state: &Path, pid: u32) {
    let writer = std::fs::read_to_string(server_file(state))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("pid")?.as_u64());
    if writer == Some(u64::from(pid)) {
        let _ = std::fs::remove_file(server_file(state));
    }
}

/// The port of the running server, from what it wrote at start; the default otherwise.
pub fn running_port(state: &Path) -> u16 {
    std::fs::read_to_string(server_file(state))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("port")?.as_u64())
        .and_then(|port| u16::try_from(port).ok())
        .unwrap_or(DEFAULT_PORT)
}

/// Asks `/healthz` on loopback with the owner token. Returns its JSON when the server answers.
pub fn probe(state: &Path, port: u16) -> Option<Value> {
    let token = std::fs::read_to_string(state.join("token")).ok()?;
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    let request = format!(
        "GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
        token.trim()
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") {
        return None;
    }
    serde_json::from_str(response.split("\r\n\r\n").nth(1)?).ok()
}

/// The user unit's text, on the default port.
pub fn unit_text(ling: &Path, lan: bool) -> String {
    unit_text_on(ling, lan, DEFAULT_PORT)
}

/// The user unit's text; `--port` only when it is not the default, so the default unit is as it was.
pub fn unit_text_on(ling: &Path, lan: bool, port: u16) -> String {
    let lan = if lan { " --lan" } else { "" };
    let port = if port == DEFAULT_PORT { String::new() } else { format!(" --port {port}") };
    format!(
        "# Written by `ling web start`; `ling web stop` stops it.\n\
         [Unit]\n\
         Description=Mightling web server (ling web)\n\
         \n\
         [Service]\n\
         ExecStart={} web serve{lan}{port}\n\
         Restart=on-failure\n\
         RestartSec=2\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        exec_path(ling)
    )
}

/// A path as one ExecStart word: `%` and `$` doubled so systemd does not expand them, and quoted
/// when it holds a space, a quote or a backslash, which systemd would otherwise split or read.
fn exec_path(path: &Path) -> String {
    let path = path.display().to_string().replace('%', "%%").replace('$', "$$");
    if path.chars().any(|c| c.is_whitespace() || c == '"' || c == '\'' || c == '\\') {
        format!("\"{}\"", path.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        path
    }
}

fn systemctl(args: &[&str]) -> Result<(), String> {
    let status = std::process::Command::new("systemctl")
        .arg("--user")
        .args(args)
        .status()
        .map_err(|err| format!("could not run systemctl: {err}"))?;
    if status.success() { Ok(()) } else { Err(format!("systemctl --user {} failed ({status})", args.join(" "))) }
}

/// The launchd label of the per-user agent that stands in for the user unit on macOS.
pub const LAUNCHD_LABEL: &str = "ai.dreamference.mightling-web";

/// The launchd agent's property list: the same command as the user unit, restarted on failure.
pub fn launchd_plist(ling: &Path, lan: bool, port: u16, log: &Path) -> String {
    let lan = if lan { "\n        <string>--lan</string>" } else { "" };
    let port = if port == DEFAULT_PORT { String::new() } else { format!("\n        <string>--port</string>\n        <string>{port}</string>") };
    let escape = |text: &str| text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <!-- Written by `ling web start`; `ling web stop` stops it. -->\n\
         <plist version=\"1.0\">\n\
         <dict>\n    <key>Label</key>\n    <string>{LAUNCHD_LABEL}</string>\n\
         \x20   <key>ProgramArguments</key>\n    <array>\n        <string>{}</string>\n        <string>web</string>\n        <string>serve</string>{lan}{port}\n    </array>\n\
         \x20   <key>RunAtLoad</key>\n    <true/>\n\
         \x20   <key>KeepAlive</key>\n    <dict>\n        <key>SuccessfulExit</key>\n        <false/>\n    </dict>\n\
         \x20   <key>ThrottleInterval</key>\n    <integer>2</integer>\n\
         \x20   <key>StandardOutPath</key>\n    <string>{log}</string>\n    <key>StandardErrorPath</key>\n    <string>{log}</string>\n\
         </dict>\n</plist>\n",
        escape(&ling.display().to_string()),
        log = escape(&log.display().to_string())
    )
}

fn launchctl(args: &[&str]) -> Result<(), String> {
    let status = std::process::Command::new("launchctl")
        .args(args)
        .status()
        .map_err(|err| format!("could not run launchctl: {err}"))?;
    if status.success() { Ok(()) } else { Err(format!("launchctl {} failed ({status})", args.join(" "))) }
}

/// Where the macOS agent's property list lives.
fn launchd_plist_path(home: &Path) -> PathBuf {
    home.join("Library/LaunchAgents").join(format!("{LAUNCHD_LABEL}.plist"))
}

/// The launchd domain of this user's login session, `gui/<uid>`.
fn launchd_domain() -> String {
    // SAFETY: getuid has no preconditions and cannot fail.
    format!("gui/{}", unsafe { libc::getuid() })
}

/// Stops the web server: the user unit on Linux, the launchd agent on macOS.
fn stop() -> i32 {
    let result = if cfg!(target_os = "macos") {
        let target = format!("{}/{LAUNCHD_LABEL}", launchd_domain());
        launchctl(&["bootout", &target])
    } else {
        systemctl(&["stop", UNIT_NAME])
    };
    match result {
        Ok(()) => {
            println!("Stopped the Mightling web server.");
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

/// Writes the service definition and (re)starts it: a user unit on Linux, a launchd agent on macOS.
fn install_and_restart(environment: &Environment, lan: bool, port: u16) -> Result<(), String> {
    let macos = cfg!(target_os = "macos");
    let (path, text) = if macos {
        let log = environment.home.join("Library/Logs/mightling-web.log");
        (launchd_plist_path(&environment.home), launchd_plist(&environment.ling, lan, port, &log))
    } else {
        (environment.home.join(".config/systemd/user").join(UNIT_NAME), unit_text_on(&environment.ling, lan, port))
    };
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| if macos { std::fs::create_dir_all(environment.home.join("Library/Logs")) } else { Ok(()) })
        .and_then(|()| std::fs::write(&path, text));
    if let Err(err) = written {
        return Err(format!("Could not write {}: {err}", path.display()));
    }
    if macos {
        let domain = launchd_domain();
        // A loaded agent is replaced: bootout fails when none is loaded, which is fine.
        let _ = launchctl(&["bootout", &format!("{domain}/{LAUNCHD_LABEL}")]);
        launchctl(&["bootstrap", &domain, &path.display().to_string()])
    } else {
        systemctl(&["daemon-reload"]).and_then(|()| systemctl(&["restart", UNIT_NAME]))
    }
}

/// Opens a URL in this machine's browser, without waiting for it.
fn open_browser(url: &str) -> bool {
    let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    std::process::Command::new(opener)
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .is_ok()
}

/// The word after `flag`; `Some("")` when the flag ends the line, so that a bare flag is refused.
fn flag_value<'a>(args: &'a [String], flag: &str) -> Option<&'a str> {
    let index = args.iter().position(|arg| arg == flag)?;
    Some(args.get(index + 1).map_or("", String::as_str))
}

pub async fn run_cli(args: &[String], environment: Environment) -> i32 {
    let state = environment.state_dir();
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.first().copied() {
        Some("serve") => serve_command(&args[1..], &environment).await,
        Some("start") => start(&args[1..], &environment),
        Some("stop") => stop(),
        Some("status") => status(&state),
        Some("open") if words.contains(&"--print-url") => print_login_url(&state),
        Some("open") => open(&state, words.contains(&"--no-browser")),
        Some("pair") => pair(&state),
        Some("devices") if words.get(1) == Some(&"--scope") => match (words.get(2), words.get(3).copied().and_then(auth::Scope::parse)) {
            (Some(which), Some(scope)) => set_scope(&state, which, scope),
            _ => {
                eprintln!("Usage: ling web devices --scope <device id or name> watch|control");
                2
            }
        },
        Some("devices") => devices(&state),
        Some("ask") => ask_command(&args[1..], &state).await,
        Some("revoke") => match words.get(1) {
            Some(which) => revoke(&state, which),
            None => {
                eprintln!("Usage: ling web revoke <device id or name>");
                2
            }
        },
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

/// The listeners a server binds: loopback alone, or every interface on an advertised node.
pub async fn bind(port: u16, lan: bool) -> std::io::Result<Vec<tokio::net::TcpListener>> {
    if lan {
        return Ok(vec![tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], port))).await?]);
    }
    let mut listeners = vec![tokio::net::TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], port))).await?];
    // Browsers may resolve `localhost` to ::1 first; serve it too where the machine has IPv6.
    if let Ok(v6) = tokio::net::TcpListener::bind(SocketAddr::from((std::net::Ipv6Addr::LOCALHOST, port))).await {
        listeners.push(v6);
    }
    Ok(listeners)
}

async fn serve_command(args: &[String], environment: &Environment) -> i32 {
    let lan = args.iter().any(|arg| arg == "--lan");
    if lan && !advertised_web(&environment.home) {
        eprintln!(
            "--lan serves the web UI to the network, which only an advertised node does: run `ling-admin node enable` first."
        );
        return 1;
    }
    let port = match flag_value(args, "--port").map(str::parse::<u16>) {
        None => DEFAULT_PORT,
        Some(Ok(port)) => port,
        Some(Err(_)) => {
            eprintln!("--port takes a port number");
            return 2;
        }
    };
    let state = environment.state_dir();
    let config = Config {
        codex_home: environment.codex_home.clone(),
        state_dir: state.clone(),
        port,
        lan_names: if lan { lan_names().into_iter().chain(overlay_name(&environment.home)).collect() } else { Vec::new() },
        public_names: ling_airgapped::user_config_file()
            .and_then(|path| std::fs::read_to_string(path).ok())
            .map(|text| crate::telegram_app::front_authorities(&text))
            .unwrap_or_default(),
        socket: app_server::default_socket(&state),
        launch: Launch { ling: environment.ling.clone(), env: environment.child_env.clone(), log: state.join("app-server.log") },
        images_dir: environment.home.join(crate::sidecars::IMAGE_STORE),
        speech_addr: crate::sidecars::SPEECH_ADDR.to_string(),
        user_config: ling_airgapped::user_config_file(),
        forge_url: None,
    };
    let server = match Server::new(config.clone()) {
        Ok(server) => server,
        Err(err) => {
            eprintln!("Could not prepare {}: {err}", state.display());
            return 1;
        }
    };
    let listeners = match bind(port, lan).await {
        Ok(listeners) => listeners,
        Err(err) => {
            eprintln!("Could not listen on port {port}: {err}");
            return 1;
        }
    };
    let urls: Vec<String> = std::iter::once(format!("http://127.0.0.1:{port}/"))
        .chain(config.lan_names.iter().map(|name| format!("http://{name}:{port}/")))
        .collect();
    let _ = auth::write_private(
        &server_file(&state),
        &json!({ "pid": std::process::id(), "port": port, "lan": lan, "urls": urls }).to_string(),
    );
    eprintln!("Mightling web server on {}", urls.join(", "));
    let shutdown = async {
        let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).ok();
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = async { match terminate.as_mut() { Some(signal) => { signal.recv().await; } None => std::future::pending::<()>().await } } => {}
        }
    };
    let result = server::serve(server, listeners, shutdown).await;
    remove_server_file_if_ours(&state, std::process::id());
    match result {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

fn start(args: &[String], environment: &Environment) -> i32 {
    let lan = args.iter().any(|arg| arg == "--lan");
    let port = match flag_value(args, "--port").map(str::parse::<u16>) {
        None => DEFAULT_PORT,
        Some(Ok(port)) => port,
        Some(Err(_)) => {
            eprintln!("--port takes a port number");
            return 2;
        }
    };
    if lan && !advertised_web(&environment.home) {
        eprintln!("--lan serves the web UI to the network, which only an advertised node does: run `ling-admin node enable` first.");
        return 1;
    }
    if let Err(err) = install_and_restart(environment, lan, port) {
        eprintln!("{err}");
        return 1;
    }
    println!(
        "Mightling web server started on http://127.0.0.1:{port}/{}. Sign this machine in with `ling web open`.",
        if lan { ", and on the LAN" } else { "" }
    );
    0
}

fn status(state: &Path) -> i32 {
    let port = running_port(state);
    let devices = auth::load_devices(state).len();
    match probe(state, port) {
        Some(health) => {
            let lan = health.get("lan").and_then(Value::as_bool).unwrap_or(false);
            println!(
                "Mightling web server: running on port {port} ({}), pid {}; {devices} paired device(s).",
                if lan { "loopback and the LAN" } else { "loopback only" },
                health.get("pid").and_then(Value::as_u64).unwrap_or(0)
            );
            0
        }
        None => {
            println!("Mightling web server: not running (start it with `ling web start`); {devices} paired device(s).");
            3
        }
    }
}

/// A one-time sign-in link to the running server, or the reason there is none.
fn login_url(state: &Path) -> Result<String, String> {
    let port = running_port(state);
    if probe(state, port).is_none() {
        return Err("The Mightling web server is not running: start it with `ling web start`.".to_string());
    }
    let code = auth::issue_login_code(state).map_err(|err| format!("Could not write a sign-in code: {err}"))?;
    Ok(format!("http://127.0.0.1:{port}/login?code={code}"))
}

/// `ling web open --print-url`: the link alone on stdout, for a program that loads it itself (the
/// desktop app's Ask window). The code is written by the caller's own process, as for `open`.
fn print_login_url(state: &Path) -> i32 {
    match login_url(state) {
        Ok(url) => {
            println!("{url}");
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

fn open(state: &Path, no_browser: bool) -> i32 {
    let url = match login_url(state) {
        Ok(url) => url,
        Err(err) => {
            eprintln!("{err}");
            return 1;
        }
    };
    if no_browser || !open_browser(&url) {
        println!("Open this link once, within two minutes: {url}");
    } else {
        println!("Opened Mightling in your browser.");
    }
    0
}

fn pair(state: &Path) -> i32 {
    let code = match auth::issue_pairing_code(state) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("Could not write a pairing code: {err}");
            return 1;
        }
    };
    let mut urls: Vec<String> = std::fs::read_to_string(server_file(state))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("urls").and_then(Value::as_array).cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(Value::as_str)
        .filter(|url| !url.contains("127.0.0.1"))
        .map(|url| format!("{url}pair"))
        .collect();
    // The address a phone most likely reaches first (qr.rs), and its code in the terminal.
    urls.sort_by_key(|url| qr::reachability(url_host(url).unwrap_or_default()));
    println!("Pairing code: {} {}", &code[..4], &code[4..]);
    println!("It works once, for ten minutes.");
    if urls.is_empty() {
        println!("This server is on loopback only, so no other device can reach it; on an advertised node, `ling web start --lan` serves the LAN.");
        return 0;
    }
    println!("On the other device, open {} and type the code.", urls.join(" or "));
    if crate::sync_store::enabled(state.parent().unwrap_or(state)) {
        println!("For another computer's sync agent, run `ling sync enable` there and type the code.");
    }
    if std::io::stdout().is_terminal()
        && let Some(drawn) = qr::terminal(&format!("{}?code={code}", urls[0]))
    {
        println!("Or scan this with it ({}):\n{drawn}", url_host(&urls[0]).unwrap_or_default());
    }
    0
}

/// The host of an `http://host:port/…` link, without brackets.
fn url_host(url: &str) -> Option<&str> {
    let rest = url.strip_prefix("http://").or_else(|| url.strip_prefix("https://"))?;
    let authority = rest.split('/').next()?;
    let host = authority.rsplit_once(':').map_or(authority, |(host, _)| host);
    Some(host.trim_start_matches('[').trim_end_matches(']'))
}

async fn ask_command(args: &[String], state: &Path) -> i32 {
    let mut port = running_port(state);
    let mut words = Vec::new();
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        if arg == "--port" {
            match rest.next().map(|value| value.parse::<u16>()) {
                Some(Ok(value)) => port = value,
                _ => {
                    eprintln!("--port takes a port number");
                    return 2;
                }
            }
        } else {
            words.push(arg.as_str());
        }
    }
    if words.is_empty() {
        eprintln!("Usage: ling web ask [--port N] <question>");
        return 2;
    }
    match crate::ask_client::ask(state, port, &words.join(" "), Duration::from_secs(15 * 60)).await {
        Ok(answer) if !answer.trim().is_empty() => 0,
        Ok(_) => {
            eprintln!("The answer was empty.");
            1
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

fn devices(state: &Path) -> i32 {
    let devices = auth::load_devices(state);
    if devices.is_empty() {
        println!("No paired devices. Pair one with `ling web pair`.");
        return 0;
    }
    for device in devices {
        let kind = if device.kind == auth::DeviceKind::Sync { "sync agent, " } else { "" };
        println!(
            "{}  {}  {kind}{}, paired {}, last used {}",
            device.id,
            device.name,
            device.scope.name(),
            ago(device.created),
            ago(device.last_used)
        );
    }
    0
}

fn set_scope(state: &Path, which: &str, scope: auth::Scope) -> i32 {
    match auth::set_scope(state, which, scope) {
        Ok(changed) if changed.is_empty() => {
            eprintln!("No paired device is called {which}; `ling web devices` lists them.");
            1
        }
        Ok(changed) => {
            for device in changed {
                let what = if scope == auth::Scope::Watch { "may now watch other devices' threads, not change them" } else { "may now control other devices' threads" };
                println!("{} ({}) {what}.", device.name, device.id);
            }
            0
        }
        Err(err) => {
            eprintln!("Could not update the devices: {err}");
            1
        }
    }
}

fn revoke(state: &Path, which: &str) -> i32 {
    match auth::revoke_device(state, which) {
        Ok(gone) if gone.is_empty() => {
            eprintln!("No paired device is called {which}; `ling web devices` lists them.");
            1
        }
        Ok(gone) => {
            for device in gone {
                println!("Revoked {} ({}). Its next request is refused.", device.name, device.id);
            }
            0
        }
        Err(err) => {
            eprintln!("Could not update the devices: {err}");
            1
        }
    }
}

fn ago(seconds: u64) -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    crate::devices::ago(seconds, now)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-web-cli-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_port_flag_without_a_number_is_refused_not_ignored() {
        let args = |words: &[&str]| words.iter().map(|w| w.to_string()).collect::<Vec<_>>();
        let port = |words: &[&str]| flag_value(&args(words), "--port").map(str::parse::<u16>);
        assert_eq!(port(&["--lan"]), None, "no flag: the default port");
        assert_eq!(port(&["--port", "8790"]), Some(Ok(8790)));
        assert!(matches!(port(&["--port"]), Some(Err(_))), "a bare --port is an error, not the default");
    }

    #[test]
    fn a_server_leaves_the_server_file_another_server_wrote() {
        let state = scratch("server-file");
        std::fs::write(server_file(&state), r#"{"pid": 4242, "port": 8788}"#).unwrap();
        remove_server_file_if_ours(&state, 4141);
        assert_eq!(running_port(&state), 8788, "a server that exits leaves the file of the one still running");
        remove_server_file_if_ours(&state, 4242);
        assert!(!server_file(&state).exists());
    }

    #[test]
    fn only_an_advertised_node_that_shares_its_web_ui_serves_the_lan() {
        let home = scratch("advertise");
        assert!(!advertised_web(&home));
        let file = home.join(".config/dreamference/node-advertise.json");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        std::fs::write(&file, r#"{"advertise": true, "web": true}"#).unwrap();
        assert!(advertised_web(&home));
        std::fs::write(&file, r#"{"advertise": true, "web": false}"#).unwrap();
        assert!(!advertised_web(&home));
        std::fs::write(&file, r#"{"advertise": false}"#).unwrap();
        assert!(!advertised_web(&home));
        std::fs::write(&file, "not json").unwrap();
        assert!(!advertised_web(&home));
    }

    #[test]
    fn a_node_in_the_overlay_answers_to_its_overlay_name() {
        let home = scratch("overlay");
        assert_eq!(overlay_name(&home), None);
        let file = home.join(".config/dreamference/remote/remote.json");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        let name = "mightling-00000000-0000-4000-8000-000000000000.netbird.selfhosted";
        std::fs::write(&file, format!(r#"{{"overlay_name": "{name}"}}"#)).unwrap();
        assert_eq!(overlay_name(&home), None, "not yet a peer");
        std::fs::write(&file, format!(r#"{{"overlay_name": "{name}", "node_peer": true}}"#)).unwrap();
        assert_eq!(overlay_name(&home).as_deref(), Some(name));
        std::fs::write(&file, r#"{"overlay_name": "evil.example.org", "node_peer": true}"#).unwrap();
        assert_eq!(overlay_name(&home), None, "only the node's own shape");
        std::fs::write(&file, r#"{"overlay_name": "mightling-x.y/z", "node_peer": true}"#).unwrap();
        assert_eq!(overlay_name(&home), None);
        std::fs::write(&file, "not json").unwrap();
        assert_eq!(overlay_name(&home), None);
    }

    #[test]
    fn the_launchd_agent_runs_the_server_in_the_foreground() {
        let log = Path::new("/home/u/Library/Logs/mightling-web.log");
        let text = launchd_plist(Path::new("/home/u/.local/bin/ling"), true, DEFAULT_PORT, log);
        assert!(text.contains("<string>ai.dreamference.mightling-web</string>"));
        assert!(text.contains("<string>/home/u/.local/bin/ling</string>\n        <string>web</string>\n        <string>serve</string>\n        <string>--lan</string>\n    </array>"));
        assert!(text.contains("<key>SuccessfulExit</key>\n        <false/>"));
        assert!(text.contains("<string>/home/u/Library/Logs/mightling-web.log</string>"));
        let plain = launchd_plist(Path::new("/x/ling"), false, DEFAULT_PORT, log);
        assert!(!plain.contains("--lan") && !plain.contains("--port"));
        assert!(launchd_plist(Path::new("/x/ling"), false, 3101, log).contains("<string>--port</string>\n        <string>3101</string>"));
        assert!(launchd_plist(Path::new("/x/a&b/ling"), false, DEFAULT_PORT, log).contains("/x/a&amp;b/ling"));
        assert_eq!(launchd_plist_path(Path::new("/home/u")), Path::new("/home/u/Library/LaunchAgents/ai.dreamference.mightling-web.plist"));
        assert!(launchd_domain().starts_with("gui/"));
    }

    #[test]
    fn the_unit_runs_the_server_in_the_foreground() {
        let text = unit_text(Path::new("/home/u/.local/bin/ling"), true);
        assert!(text.contains("ExecStart=/home/u/.local/bin/ling web serve --lan\n"));
        assert!(!unit_text(Path::new("/x/ling"), false).contains("--lan"));
        // The default port is not written, so the unit is as it always was; another one is.
        assert_eq!(unit_text_on(Path::new("/x/ling"), false, DEFAULT_PORT), unit_text(Path::new("/x/ling"), false));
        assert!(!unit_text(Path::new("/x/ling"), false).contains("--port"));
        assert!(unit_text_on(Path::new("/x/ling"), true, 3101).contains("ExecStart=/x/ling web serve --lan --port 3101\n"));
    }

    #[test]
    fn the_unit_quotes_a_binary_path_with_spaces() {
        // systemd splits ExecStart on spaces and expands `%` and `$`.
        let text = unit_text(Path::new("/home/u/My Tools/100%/ling"), false);
        assert!(text.contains("ExecStart=\"/home/u/My Tools/100%%/ling\" web serve\n"), "{text}");
        assert!(unit_text(Path::new("/x/$HOME/ling"), false).contains("ExecStart=/x/$$HOME/ling web serve\n"));
    }

    #[tokio::test]
    async fn serve_refuses_the_lan_on_a_machine_that_is_not_an_advertised_node() {
        let home = scratch("refuse");
        let environment = Environment {
            codex_home: home.join(".mightling"),
            home: home.clone(),
            ling: PathBuf::from("/nonexistent/ling"),
            child_env: Vec::new(),
        };
        assert_eq!(run_cli(&["serve".to_string(), "--lan".to_string()], environment).await, 1);
        assert!(!home.join(".mightling/web/server.json").exists());
    }
}
