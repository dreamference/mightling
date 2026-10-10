//! `ling audit egress` on Windows (specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §14): one real
//! `ling exec` session in a throwaway repository and `CODEX_HOME`, traced over ETW, judged as the
//! node's `ling-admin audit egress` judges its strace (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3.2).
//!
//! A pass needs every destination the session's processes reached to be the model server or an
//! allow-listed loopback port, no name looked up, and no networked git helper started. Exit 0 is a
//! pass, 1 a failure, 2 a trace that showed nothing (never a pass). The trace is three providers
//! in one real-time session, which needs Administrator rights or the Performance Log Users group:
//! Microsoft-Windows-Kernel-Process (to follow the session's process tree), Kernel-Network
//! (TCP connects and sends, UDP sends) and DNS-Client (every lookup, which strace on Linux could
//! not name). Windows' own traffic is outside the tree and outside the verdict; the report says so.
//!
//! The verdict is plain Rust over recorded events, tested on every platform; only the recording
//! differs: ETW on Windows (`windows`), `strace` on Linux (`linux`, since 2026-10-10: a client has
//! no `ling-admin`, so the audit the node runs in Python is here too). The strace reading
//! (`strace`) is the Python `StraceParser`'s rules, in the same events: a UDP `connect` with
//! nothing sent is a route lookup, listed and not judged, until a payload follows on that socket;
//! a payload to port 53 names what was asked, or is a query whose name could not be read.

use std::collections::BTreeMap;
use std::collections::HashSet;
use std::net::IpAddr;
use std::path::Path;

/// Loopback services a session may reach besides the model server (MIGHTLING_EGRESS §4.3).
pub const GMAIL_PORT: u16 = 8767;
pub const SEARXNG_PORT: u16 = 8888;

/// Where the launcher points `chatgpt_base_url` (`OFFLINE_CHATGPT_BASE_URL`): a connect there is a
/// call to the upstream vendor's backend that no patch closed.
pub const BLACKHOLE_PORT: u16 = 9;

/// Names a resolver answers on the machine itself; a lookup of one leaves nothing.
const LOCAL_NAMES: &[&str] = &["localhost"];

/// Helpers git starts to reach a remote: their start is a networked git command.
const NETWORKED_GIT: &[&str] = &[
    "git-remote-https.exe", "git-remote-http.exe", "git-remote-ftp.exe", "git-remote-ftps.exe", "ssh.exe",
    "git-remote-https", "git-remote-http", "git-remote-ftp", "git-remote-ftps",
];

const PROMPT: &str = "Reply with exactly: pong";
const USAGE: &str = "Usage: ling audit egress [--host <model server URL>] [--prompt <text>] [--keep]";

/// The session's time limit, and the units that reach outside the machine by design when the user
/// turned them on (MIGHTLING_EGRESS §10.8): named in the report, since no traced session shows them.
const SESSION_TIMEOUT_S: u64 = 300;
const SIGNAL_UNIT: &str = "mightling-signal.service";
const CHAT_UNIT: &str = "mightling-chat.service";
const MATRIX_PROXY_UNIT: &str = "mightling-matrix-proxy.socket";

/// What the trace recorded, reduced to what the verdict reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    /// A process started; `image` is its file name.
    Start { pid: u32, parent: u32, image: String },
    /// A process sent to, or connected to, an address.
    Connect { pid: u32, address: IpAddr, port: u16, udp: bool },
    /// A process asked the DNS client for a name.
    Lookup { pid: u32, name: String },
    /// A process connected or sent to a resolver (port 53); the name asked, when it could be
    /// read from the payload, is a `Lookup` beside it.
    Query { pid: u32, server: String },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Pass,
    Fail,
    TraceFailed,
}

impl Status {
    pub fn exit_code(self) -> i32 {
        match self {
            Status::Pass => 0,
            Status::Fail => 1,
            Status::TraceFailed => 2,
        }
    }
}

/// The model server's address and port, from its base URL. `None` for the address when the host is
/// a name other than `localhost`: a session that has to look it up fails the audit on the lookup.
pub fn model_endpoint(host: &str) -> Option<(Option<IpAddr>, u16)> {
    let (scheme, rest) = host.split_once("://").unwrap_or(("http", host));
    let authority = rest.split('/').next()?;
    let (name, port) = match authority.strip_prefix('[') {
        Some(v6) => {
            let (name, rest) = v6.split_once(']')?;
            (name, rest.strip_prefix(':'))
        }
        None => match authority.rsplit_once(':') {
            Some((name, port)) => (name, Some(port)),
            None => (authority, None),
        },
    };
    let port = match port {
        Some(port) => port.parse().ok()?,
        None if scheme == "https" => 443,
        None => 80,
    };
    let address = if name.eq_ignore_ascii_case("localhost") { Some(IpAddr::from([127, 0, 0, 1])) } else { name.parse().ok() };
    Some((address, port))
}

fn is_loopback(address: &IpAddr) -> bool {
    match address {
        IpAddr::V4(v4) => v4.is_loopback(),
        IpAddr::V6(v6) => v6.is_loopback() || v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback()),
    }
}

fn display(address: &IpAddr, port: u16) -> String {
    match address {
        IpAddr::V4(v4) => format!("{v4}:{port}"),
        IpAddr::V6(v6) => format!("[{v6}]:{port}"),
    }
}

/// The session's processes: `root` and everything it started, however deep.
pub fn process_tree(events: &[Event], root: u32) -> HashSet<u32> {
    let mut tree = HashSet::from([root]);
    loop {
        let before = tree.len();
        for event in events {
            if let Event::Start { pid, parent, .. } = event
                && tree.contains(parent)
            {
                tree.insert(*pid);
            }
        }
        if tree.len() == before {
            return tree;
        }
    }
}

/// What the session did, from the events of its process tree.
#[derive(Debug, Default)]
pub struct Session {
    /// Destination to the number of events.
    pub destinations: BTreeMap<(IpAddr, u16), usize>,
    pub lookups: BTreeMap<String, usize>,
    /// Resolver to the number of calls that named it.
    pub dns_servers: BTreeMap<String, usize>,
    /// Image to the number of starts.
    pub processes: BTreeMap<String, usize>,
}

pub fn session(events: &[Event], root: u32) -> Session {
    session_in(events, &process_tree(events, root))
}

/// What every traced process did: for an `strace -f` trace, whose processes are all the
/// session's by construction, and where a thread's socket calls carry the thread's own id, which
/// never ran an `execve` and so is in no process tree.
pub fn session_all(events: &[Event]) -> Session {
    let all: HashSet<u32> = events
        .iter()
        .map(|event| match event {
            Event::Start { pid, .. } | Event::Connect { pid, .. } | Event::Lookup { pid, .. } | Event::Query { pid, .. } => *pid,
        })
        .collect();
    session_in(events, &all)
}

fn session_in(events: &[Event], tree: &HashSet<u32>) -> Session {
    let mut session = Session::default();
    for event in events {
        match event {
            Event::Start { pid, image, .. } if tree.contains(pid) => {
                *session.processes.entry(image.clone()).or_default() += 1;
            }
            Event::Connect { pid, address, port, .. } if tree.contains(pid) => {
                *session.destinations.entry((*address, *port)).or_default() += 1;
            }
            Event::Lookup { pid, name } if tree.contains(pid) => {
                *session.lookups.entry(name.trim_end_matches('.').to_lowercase()).or_default() += 1;
            }
            Event::Query { pid, server } if tree.contains(pid) => {
                *session.dns_servers.entry(server.clone()).or_default() += 1;
            }
            _ => {}
        }
    }
    session
}

/// The verdict and its reasons. `model` is the model server's endpoint; `replied` whether the
/// session answered, without which nothing was shown.
pub fn judge(session: &Session, model: (Option<IpAddr>, u16), replied: bool) -> (Status, Vec<String>) {
    let (model_address, model_port) = model;
    let model_on_loopback = model_address.is_some_and(|address| is_loopback(&address));
    let is_model = |address: &IpAddr, port: u16| {
        port == model_port && (model_address == Some(*address) || (model_on_loopback && is_loopback(address)))
    };
    let mut problems = Vec::new();
    let mut model_events = 0;
    for ((address, port), count) in &session.destinations {
        let target = display(address, *port);
        if is_model(address, *port) {
            model_events += count;
        } else if !is_loopback(address) {
            problems.push(format!("connected to {target} ({count}x): not on this machine"));
        } else if *port == BLACKHOLE_PORT {
            problems.push(format!(
                "connected to {target} ({count}x): a call to the upstream vendor's backend that no patch closes (it failed here only because the launcher redirects that URL)"
            ));
        } else if *port != GMAIL_PORT && *port != SEARXNG_PORT {
            problems.push(format!("connected to {target} ({count}x): a local port that is not on the allowlist"));
        }
    }
    for (name, count) in &session.lookups {
        if !LOCAL_NAMES.contains(&name.as_str()) {
            problems.push(format!("asked a resolver for {name} ({count}x)"));
        }
    }
    if session.lookups.is_empty() && !session.dns_servers.is_empty() {
        let servers: Vec<String> = session.dns_servers.iter().map(|(server, count)| format!("{server} ({count}x)")).collect();
        problems.push(format!("sent a DNS query to {} whose name could not be read", servers.join(", ")));
    }
    for image in session.processes.keys() {
        if NETWORKED_GIT.contains(&image.to_lowercase().as_str()) {
            problems.push(format!("started {image}, which git runs to reach a remote"));
        }
    }
    if !problems.is_empty() {
        return (Status::Fail, problems);
    }
    if !replied {
        return (Status::TraceFailed, vec!["the session produced no reply, so the trace shows nothing".to_string()]);
    }
    if model_events == 0 {
        return (Status::TraceFailed, vec![
            "no connection to the model server was recorded, although the session replied: the trace did not see this session's network events".to_string(),
        ]);
    }
    (Status::Pass, Vec::new())
}

/// The report: the verdict and anything unexpected first, then everything seen, then what the
/// verdict does not cover.
pub fn render(session: &Session, model: (Option<IpAddr>, u16), status: Status, problems: &[String]) -> Vec<String> {
    let mark = match status {
        Status::Pass => "✅ Egress audit: pass",
        Status::Fail => "❌ Egress audit: fail",
        Status::TraceFailed => "⚠️ Egress audit: trace failed",
    };
    let mut lines = vec![mark.to_string()];
    lines.extend(problems.iter().map(|problem| format!("   - {problem}")));
    lines.push("Network destinations:".to_string());
    if session.destinations.is_empty() {
        lines.push("   none".to_string());
    }
    let (model_address, model_port) = model;
    for ((address, port), count) in &session.destinations {
        let label = if *port == model_port && (model_address == Some(*address) || (model_address.is_some_and(|a| is_loopback(&a)) && is_loopback(address))) {
            "model server"
        } else if !is_loopback(address) {
            "not on this machine"
        } else {
            match *port {
                GMAIL_PORT => "Gmail search service",
                SEARXNG_PORT => "SearXNG",
                _ => "not on the allowlist",
            }
        };
        lines.push(format!("   {:<24} {count:>4}x  {label}", display(address, *port)));
    }
    let names: Vec<String> = session.lookups.iter().map(|(name, count)| format!("{name} ({count}x)")).collect();
    lines.push(format!("DNS lookups: {}", if names.is_empty() { "none".to_string() } else { names.join(", ") }));
    if !session.dns_servers.is_empty() {
        let servers: Vec<String> = session.dns_servers.iter().map(|(server, count)| format!("{server} ({count}x)")).collect();
        lines.push(format!("Resolvers queried: {}", servers.join(", ")));
    }
    let programs: Vec<String> = session.processes.iter().map(|(image, count)| format!("{image} ({count})")).collect();
    lines.push(format!("Processes started: {}", if programs.is_empty() { "none".to_string() } else { programs.join(", ") }));
    lines.push(if cfg!(windows) {
        "Not judged: Windows' own traffic (Defender, Windows Update, telemetry) is outside this session's processes, so \"ling made no connection\" is not \"this computer made none\". Defender's automatic sample submission can upload an unknown program, ling's own included, to Microsoft."
            .to_string()
    } else {
        "Not judged: the code index's and the file index's indexers run detached in their own network-less sandbox and outlive the session, so they are outside this trace (`ling-admin audit egress --docs` on a node traces the file index)."
            .to_string()
    });
    lines
}

/// What the user turned on that reaches outside this machine by design, which no traced session
/// shows (MIGHTLING_EGRESS §10.8): one line per enabled messenger unit, none when all are off.
/// Reads `systemctl is-enabled` only; nothing where systemd is absent.
pub fn declared_exceptions() -> Vec<String> {
    if cfg!(windows) || !on_path("systemctl") {
        return Vec::new();
    }
    let enabled = |unit: &str, user: bool| {
        let mut command = std::process::Command::new("systemctl");
        if user {
            command.arg("--user");
        }
        command.args(["is-enabled", "--quiet", unit]).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        command.status().is_ok_and(|status| status.success())
    };
    let mut lines = Vec::new();
    if enabled(SIGNAL_UNIT, false) {
        lines.push(format!("Signal bridge enabled ({SIGNAL_UNIT}): signal-cli connects to Signal's servers, outside this trace. `ling signal remove` turns it off."));
    }
    if enabled(CHAT_UNIT, true) {
        lines.push(format!("Chat bridge enabled ({CHAT_UNIT}): with a Telegram bot set up, `ling chat serve` connects to Telegram's Bot API, outside this trace. `ling chat stop` turns it off."));
    }
    if enabled(MATRIX_PROXY_UNIT, true) {
        lines.push("Matrix homeserver enabled (`ling-admin matrix`): offered to your tailnet by `tailscale serve`. `ling-admin matrix stop` turns it off.".to_string());
    }
    lines
}

/// Whether a program is on PATH.
pub fn on_path(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).any(|dir| dir.join(name).is_file()))
        .unwrap_or(false)
}

/// Runs `ling audit …` and returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let mut host = None;
    let mut keep = false;
    let mut prompt = PROMPT.to_string();
    let mut words = args.iter().map(String::as_str);
    if words.next() != Some("egress") {
        println!("{USAGE}");
        return 2;
    }
    while let Some(word) = words.next() {
        match word {
            "--host" => host = words.next().map(str::to_string),
            "--prompt" => prompt = words.next().unwrap_or(PROMPT).to_string(),
            "--keep" => keep = true,
            _ => {
                println!("{USAGE}");
                return 2;
            }
        }
    }
    if cfg!(target_os = "macos") {
        println!("⚠️ Egress audit: trace failed");
        println!("   - the audit traces with strace on Linux and ETW on Windows; there is no recorder for macOS yet.");
        return 2;
    }
    let Some(host) = host.or_else(known_host) else {
        println!("⚠️ Egress audit: trace failed");
        println!("   - no model server is known here: pass --host <URL>, or choose a node first (ling node use <address>)");
        return 2;
    };
    let Some(model) = model_endpoint(&host) else {
        println!("{host} is not a model server URL. {USAGE}");
        return 2;
    };
    // A server that is not there would have the session wait out its whole timeout (five minutes,
    // in install.ps1's elevated window) for a trace that can only fail. Asked before tracing, so
    // this connection is not part of what is judged.
    if let (Some(address), port) = model
        && std::net::TcpStream::connect_timeout(&std::net::SocketAddr::new(address, port), std::time::Duration::from_secs(5)).is_err()
    {
        println!("⚠️ Egress audit: trace failed");
        println!("   - the model server at {host} does not answer: start it, or pass --host <URL>");
        return 2;
    }
    println!("🚀 Tracing one `ling exec` session against {host} (throwaway repository and CODEX_HOME)...");
    let recorded = if cfg!(windows) {
        windows::audit(&host, keep).map(|(events, root, replied)| (events, root, replied, Vec::new()))
    } else {
        linux::audit(&host, keep, &prompt)
    };
    match recorded {
        Ok((events, root, replied, extra_lines)) => {
            let session = if cfg!(windows) { session(&events, root) } else { session_all(&events) };
            let (status, problems) = judge(&session, model, replied);
            for line in render(&session, model, status, &problems) {
                println!("{line}");
            }
            for line in extra_lines {
                println!("{line}");
            }
            for exception in declared_exceptions() {
                println!("ℹ️  Declared exception: {exception}");
            }
            if status == Status::TraceFailed {
                println!("💡 The session needs the model server at {host}: start the node, or pass --host <URL>.");
            }
            status.exit_code()
        }
        Err(reason) => {
            println!("⚠️ Egress audit: trace failed");
            println!("   - {reason}");
            2
        }
    }
}

/// The model server this machine uses, found without a browse: a browse is a multicast DNS query,
/// and the audit is about to count those.
fn known_host() -> Option<String> {
    let inputs = crate::node::inputs();
    match crate::node::resolve(&inputs, &NoBrowse) {
        crate::node::Resolution::Host(host) => Some(host),
        crate::node::Resolution::LastAddress { node, .. } => Some(node.model_url()),
        _ => None,
    }
}

struct NoBrowse;

impl crate::node::Browser for NoBrowse {
    fn browse(&self, _wanted: Option<&crate::node::Node>) -> Vec<crate::node::Advert> {
        Vec::new()
    }
}

/// The throwaway repository, `CODEX_HOME` and configuration a session runs with, so nothing of
/// the user's influences the result and nothing of theirs is touched. The code index and the file
/// index are off: their indexers run detached and outlive the session, so they are not part of
/// what this can show.
fn prepare(work: &Path, host: &str) -> std::io::Result<()> {
    let repo = work.join("repo");
    std::fs::create_dir_all(&repo)?;
    std::fs::create_dir_all(work.join("home"))?;
    std::fs::write(repo.join("README.md"), "A throwaway repository for `ling audit egress`.\n")?;
    let git = ["-c", "user.name=audit", "-c", "user.email=audit@localhost", "-c", "commit.gpgsign=false"];
    for step in [&["init", "-q"][..], &["add", "-A"], &["commit", "-qm", "audit"]] {
        let _ = std::process::Command::new("git").args(git).args(step).current_dir(&repo).output();
    }
    let host = toml::Value::String(host.to_string());
    std::fs::write(work.join("config.toml"), format!("vllm_host = {host}\ncode_index_enabled = false\nmightling_docs = false\n"))
}

#[cfg(not(windows))]
mod windows {
    pub fn audit(_host: &str, _keep: bool) -> Result<(Vec<super::Event>, u32, bool), String> {
        Err("the ETW trace exists only on Windows".to_string())
    }
}

#[cfg(windows)]
mod linux {
    pub fn audit(_host: &str, _keep: bool, _prompt: &str) -> Result<(Vec<super::Event>, u32, bool, Vec<String>), String> {
        Err("the strace recorder exists only on Linux".to_string())
    }
}

/// Reading an `strace` of a session into [`Event`]s (the Python `StraceParser`'s rules,
/// MIGHTLING_EGRESS §3.1), platform-independent and tested on the recorded traces:
///
///     strace -f -qq -yy -e trace=connect,sendto,sendmsg,sendmmsg,execve,write,writev -s 256 -o <file> ling …
///
/// `sendmmsg` is how glibc sends a lookup's A and AAAA queries: without it a DNS query leaves no
/// name, only a connect to the resolver. `-yy` labels every descriptor with its socket kind and
/// inode (`23<UDPv6:[10868489]>`), which tells a route lookup from a connection: `connect()` on
/// a UDP socket sends nothing, it only asks the kernel for a route (Chromium's IPv6 reachability
/// check does exactly that before resolving any host). Such a connect is a route lookup, listed
/// and not judged, until a payload (`send*`, or `write`/`writev` on the connected socket) follows,
/// which makes its destination one like any other.
pub mod strace {
    use std::collections::BTreeMap;
    use std::collections::HashMap;
    use std::net::IpAddr;

    use super::Event;

    pub const TRACED_SYSCALLS: &str = "connect,sendto,sendmsg,sendmmsg,execve,write,writev";
    const DNS_PORT: u16 = 53;
    const UDP_KINDS: &[&str] = &["UDP", "UDPv6", "UDPLITE", "UDPLITEv6"];

    /// What the trace shows beside the events: listed in the report, never judged.
    #[derive(Debug, Default, PartialEq, Eq)]
    pub struct Extras {
        /// UDP connects nothing was sent on, `ip:port` to the count.
        pub route_lookups: BTreeMap<String, usize>,
        /// Unix socket paths connected or sent to (`@` for an abstract one).
        pub unix_sockets: BTreeMap<String, usize>,
        pub lines: usize,
    }

    /// Parses a whole trace. The root is the first process id seen (the traced `ling`); every
    /// process in an `strace -f` trace is in its tree, so each program started is recorded as
    /// started by it.
    pub fn parse(text: &str) -> (Vec<Event>, Option<u32>, Extras) {
        let mut events = Vec::new();
        let mut extras = Extras::default();
        let mut root: Option<u32> = None;
        // (pid, fd) of sockets connected to a resolver, whose later payloads are DNS queries.
        let mut resolver_sockets: HashMap<(u32, String), String> = HashMap::new();
        // Inode of a UDP socket only connected so far, to the destination it named.
        let mut udp_routes: HashMap<String, (IpAddr, u16)> = HashMap::new();
        // An execve whose result is on a later line (`<unfinished ...>`), by pid.
        let mut pending_execve: HashMap<u32, String> = HashMap::new();
        for raw in text.lines() {
            let line = raw.trim();
            let (pid, rest) = split_pid(line);
            if let Some(result) = resumed_execve(rest) {
                extras.lines += 1;
                let root_pid = *root.get_or_insert(pid);
                if let Some(started) = pending_execve.remove(&pid)
                    && result == 0
                {
                    read_execve(pid, root_pid, &started, &mut events);
                }
                continue;
            }
            let Some((syscall, arguments)) = split_call(rest) else { continue };
            extras.lines += 1;
            let root_pid = *root.get_or_insert(pid);
            match syscall {
                "execve" if arguments.trim_end().ends_with("<unfinished ...>") => {
                    pending_execve.insert(pid, arguments.to_string());
                }
                "execve" => read_execve(pid, root_pid, arguments, &mut events),
                "connect" | "sendto" | "sendmsg" | "sendmmsg" | "write" | "writev" => {
                    read_socket_call(pid, syscall, arguments, &mut events, &mut extras, &mut resolver_sockets, &mut udp_routes)
                }
                _ => {}
            }
        }
        (events, root, extras)
    }

    /// `1234 connect(…` (with `-o`), `[pid 1234] connect(…` (on a terminal) or `connect(…` (no -f).
    fn split_pid(line: &str) -> (u32, &str) {
        if let Some(rest) = line.strip_prefix("[pid")
            && let Some((digits, after)) = rest.trim_start().split_once(']')
            && let Ok(pid) = digits.trim().parse()
        {
            return (pid, after.trim_start());
        }
        let digits: String = line.chars().take_while(char::is_ascii_digit).collect();
        if !digits.is_empty()
            && let Some(after) = line[digits.len()..].strip_prefix(char::is_whitespace)
            && let Ok(pid) = digits.parse()
        {
            return (pid, after.trim_start());
        }
        (0, line)
    }

    /// `name(arguments…`, or None for a line that is not a call (a signal, an exit).
    fn split_call(text: &str) -> Option<(&str, &str)> {
        let open = text.find('(')?;
        let name = &text[..open];
        if name.is_empty() || !name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_') {
            return None;
        }
        Some((name, &text[open + 1..]))
    }

    /// `<... execve resumed>) = 0`: the result of a call another process's line interrupted.
    fn resumed_execve(text: &str) -> Option<i64> {
        if !text.starts_with("<... execve resumed>") {
            return None;
        }
        text.rsplit_once(')')?.1.trim().strip_prefix('=')?.trim().parse().ok()
    }

    fn read_execve(pid: u32, root: u32, arguments: &str, events: &mut Vec<Event>) {
        // A failed execve (`= -1 ENOENT`) is the shell walking PATH, not a program that ran.
        if failed(arguments) {
            return;
        }
        let strings = strings_in(arguments);
        let Some(program) = strings.first() else { return };
        let program = String::from_utf8_lossy(program);
        let image = program.rsplit('/').next().unwrap_or(&program).to_string();
        events.push(Event::Start { pid, parent: root, image });
    }

    /// `… ) = -1 ENOENT (No such file or directory)`: the result is before the errno's text.
    fn failed(arguments: &str) -> bool {
        arguments.contains(") = -1")
    }

    #[allow(clippy::too_many_arguments)]
    fn read_socket_call(
        pid: u32,
        syscall: &str,
        arguments: &str,
        events: &mut Vec<Event>,
        extras: &mut Extras,
        resolver_sockets: &mut HashMap<(u32, String), String>,
        udp_routes: &mut HashMap<String, (IpAddr, u16)>,
    ) {
        let (fd, kind, inner) = fd_label(arguments.split(',').next().unwrap_or("").trim());
        let udp = UDP_KINDS.contains(&kind);
        if syscall == "write" || syscall == "writev" {
            // Only a UDP socket's payload matters here: a stream's connect was already counted.
            if udp {
                udp_payload(pid, &inner, events, udp_routes);
            }
            return;
        }
        let destination = destination_in(arguments);
        let key = (pid, fd.clone());
        match destination {
            Some((address, port)) if port == DNS_PORT => {
                let server = super::display(&address, port);
                events.push(Event::Query { pid, server: server.clone() });
                if syscall == "connect" {
                    resolver_sockets.insert(key.clone(), server);
                }
            }
            Some((address, port))
                if syscall == "connect" && udp && !inner.is_empty() && inner.chars().all(|c| c.is_ascii_digit()) =>
            {
                // A route lookup: nothing is sent until a payload follows on this socket.
                *extras.route_lookups.entry(super::display(&address, port)).or_default() += 1;
                udp_routes.insert(inner.clone(), (address, port));
                resolver_sockets.remove(&key);
            }
            Some((address, port)) => {
                events.push(Event::Connect { pid, address, port, udp });
                resolver_sockets.remove(&key);
            }
            None if syscall == "connect" => {
                // A unix socket, netlink or anything else: this fd is not a resolver's any more.
                resolver_sockets.remove(&key);
            }
            None if udp => udp_payload(pid, &inner, events, udp_routes),
            None => {}
        }
        if let Some(path) = unix_socket_in(arguments) {
            *extras.unix_sockets.entry(path).or_default() += 1;
        }
        if syscall == "connect" {
            return;
        }
        // A payload sent to a resolver, named in the call or connected earlier, is a DNS query.
        let to_resolver = matches!(destination, Some((_, port)) if port == DNS_PORT) || resolver_sockets.contains_key(&key);
        if to_resolver {
            for payload in strings_in(arguments) {
                if let Some(name) = dns_query_name(&payload) {
                    events.push(Event::Lookup { pid, name });
                }
            }
        }
    }

    /// A payload sent on a UDP socket: its destination counts as reached, whatever the connect was.
    fn udp_payload(pid: u32, inner: &str, events: &mut Vec<Event>, udp_routes: &mut HashMap<String, (IpAddr, u16)>) {
        let target = match udp_routes.get(inner) {
            Some(target) => *target,
            None => match label_peer(inner) {
                // A resolver's payload is read as a query by the caller's DNS rule.
                Some((_, port)) if port == DNS_PORT => return,
                Some(target) => target,
                None => return,
            },
        };
        events.push(Event::Connect { pid, address: target.0, port: target.1, udp: true });
    }

    /// Splits a descriptor as strace printed it: `23`, or with `-yy` `23<UDPv6:[10868489]>`, into
    /// the number, the socket kind (empty without a label) and what the brackets hold.
    pub fn fd_label(text: &str) -> (String, &str, String) {
        let digits: String = text.chars().take_while(char::is_ascii_digit).collect();
        let rest = &text[digits.len()..];
        if let Some(label) = rest.strip_prefix('<')
            && let Some(label) = label.strip_suffix('>')
            && let Some((kind, bracketed)) = label.split_once(":[")
            && let Some(inner) = bracketed.strip_suffix(']')
        {
            return (digits, kind, inner.to_string());
        }
        (digits, "", String::new())
    }

    /// The remote end of a connected socket's label: `->127.0.0.53:53` or `->[::1]:53`.
    fn label_peer(inner: &str) -> Option<(IpAddr, u16)> {
        let peer = inner.rsplit("->").next()?;
        let (address, port) = peer.rsplit_once(':')?;
        let address = address.trim_start_matches('[').trim_end_matches(']');
        Some((address.parse().ok()?, port.parse().ok()?))
    }

    /// The IP destination named in a call's arguments, or None (a connected socket, a unix
    /// socket, netlink).
    pub fn destination_in(text: &str) -> Option<(IpAddr, u16)> {
        if let Some(at) = text.find("sa_family=AF_INET,") {
            let rest = &text[at..];
            let port = between(rest, "sin_port=htons(", ")")?;
            let address = between(rest, "sin_addr=inet_addr(\"", "\"")?;
            return Some((address.parse().ok()?, port.parse().ok()?));
        }
        if let Some(at) = text.find("sa_family=AF_INET6,") {
            let rest = &text[at..];
            let port = between(rest, "sin6_port=htons(", ")")?;
            let address = between(rest, "inet_pton(AF_INET6, \"", "\"")?;
            return Some((address.parse().ok()?, port.parse().ok()?));
        }
        None
    }

    fn between<'a>(text: &'a str, start: &str, end: &str) -> Option<&'a str> {
        let from = text.find(start)? + start.len();
        let to = text[from..].find(end)? + from;
        Some(&text[from..to])
    }

    fn unix_socket_in(text: &str) -> Option<String> {
        let marker = "sa_family=AF_UNIX, sun_path=";
        let rest = &text[text.find(marker)? + marker.len()..];
        let (abstract_socket, rest) = match rest.strip_prefix('@') {
            Some(rest) => (true, rest),
            None => (false, rest),
        };
        let literal = strings_in(rest).into_iter().next()?;
        let path = String::from_utf8_lossy(&literal).into_owned();
        Some(if abstract_socket { format!("@{path}") } else { path })
    }

    /// Every C string literal in a line of strace output, unescaped, in order.
    pub fn strings_in(text: &str) -> Vec<Vec<u8>> {
        let chars: Vec<char> = text.chars().collect();
        let mut strings = Vec::new();
        let mut index = 0;
        while index < chars.len() {
            if chars[index] != '"' {
                index += 1;
                continue;
            }
            let mut end = index + 1;
            while end < chars.len() && chars[end] != '"' {
                end += if chars[end] == '\\' { 2 } else { 1 };
            }
            let literal: String = chars[index + 1..end.min(chars.len())].iter().collect();
            strings.push(unescape(&literal));
            index = end + 1;
        }
        strings
    }

    /// strace's C-style escapes: `\n`, octal `\236` and `\0`, hex `\x1f`.
    pub fn unescape(literal: &str) -> Vec<u8> {
        let chars: Vec<char> = literal.chars().collect();
        let mut out = Vec::new();
        let mut index = 0;
        while index < chars.len() {
            let c = chars[index];
            if c != '\\' || index + 1 >= chars.len() {
                let mut buffer = [0u8; 4];
                out.extend_from_slice(c.encode_utf8(&mut buffer).as_bytes());
                index += 1;
                continue;
            }
            let following = chars[index + 1];
            if following.is_digit(8) {
                let mut end = index + 1;
                while end < chars.len() && end < index + 4 && chars[end].is_digit(8) {
                    end += 1;
                }
                let digits: String = chars[index + 1..end].iter().collect();
                out.push((u32::from_str_radix(&digits, 8).unwrap_or(0) & 0xFF) as u8);
                index = end;
            } else if following == 'x' {
                let digits: String = chars[index + 2..].iter().take(2).take_while(|c| c.is_ascii_hexdigit()).collect();
                if digits.is_empty() {
                    out.push(b'x');
                    index += 2;
                } else {
                    out.push(u8::from_str_radix(&digits, 16).unwrap_or(0));
                    index += 2 + digits.len();
                }
            } else {
                out.push(match following {
                    'n' => 10,
                    't' => 9,
                    'r' => 13,
                    'v' => 11,
                    'f' => 12,
                    'a' => 7,
                    'b' => 8,
                    'e' => 27,
                    other => (other as u32 & 0xFF) as u8,
                });
                index += 2;
            }
        }
        out
    }

    /// The name a DNS query asks for (strace may have cut the payload at 256 bytes; the name
    /// comes first, at offset 12), or None when the payload is not a query.
    pub fn dns_query_name(payload: &[u8]) -> Option<String> {
        // Header: id(2) flags(2) qdcount(2) ancount(2) nscount(2) arcount(2); QR is the top bit.
        if payload.len() < 14 || payload[2] & 0x80 != 0 || payload[4..6] != [0, 1] {
            return None;
        }
        let mut labels = Vec::new();
        let mut index = 12;
        let mut ended = false;
        while index < payload.len() {
            let length = payload[index] as usize;
            if length == 0 {
                ended = true;
                break;
            }
            if length > 63 || index + 1 + length > payload.len() {
                return None;
            }
            let label = &payload[index + 1..index + 1 + length];
            if !label.iter().all(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-') {
                return None;
            }
            labels.push(String::from_utf8_lossy(label).to_lowercase());
            index += 1 + length;
        }
        if !ended || labels.is_empty() {
            return None;
        }
        Some(labels.join("."))
    }

    /// The report lines for what the parser listed beside the events.
    pub fn extra_lines(extras: &Extras) -> Vec<String> {
        let lookups: Vec<String> = extras.route_lookups.iter().map(|(target, count)| format!("{target} ({count}x)")).collect();
        let sockets: Vec<String> = extras.unix_sockets.keys().cloned().collect();
        vec![
            format!("Route lookups (UDP connect, nothing sent): {}", if lookups.is_empty() { "none".to_string() } else { lookups.join(", ") }),
            format!("Unix sockets: {}", if sockets.is_empty() { "none".to_string() } else { sockets.join(", ") }),
        ]
    }
}

/// The Linux recorder: one `ling exec` under strace in the throwaway repository, in its own
/// process group, stopped with everything it started when it does not answer within the limit.
#[cfg(not(windows))]
mod linux {
    use std::path::Path;
    use std::process::Command;
    use std::process::Stdio;
    use std::time::Duration;
    use std::time::Instant;

    use super::Event;
    use super::strace;

    pub fn audit(host: &str, keep: bool, prompt: &str) -> Result<(Vec<Event>, u32, bool, Vec<String>), String> {
        if !super::on_path("strace") {
            return Err("strace is not installed: sudo apt-get install strace".to_string());
        }
        let exe = std::env::current_exe().map_err(|error| format!("cannot find this executable: {error}"))?;
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let work = std::env::temp_dir().join(format!("mightling-audit-{}-{stamp}", std::process::id()));
        std::fs::create_dir_all(&work).map_err(|error| format!("cannot create {}: {error}", work.display()))?;
        let result = trace(&work, &exe, host, prompt);
        if keep {
            println!("ℹ️  Kept {}: trace.txt is the raw strace.", work.display());
        } else {
            let _ = std::fs::remove_dir_all(&work);
        }
        result
    }

    fn trace(work: &Path, exe: &Path, host: &str, prompt: &str) -> Result<(Vec<Event>, u32, bool, Vec<String>), String> {
        super::prepare(work, host).map_err(|error| format!("cannot prepare the throwaway repository: {error}"))?;
        let trace_path = work.join("trace.txt");
        let reply_path = work.join("reply.txt");
        let mut command = Command::new("strace");
        command
            .args(["-f", "-qq", "-yy", "-e", &format!("trace={}", strace::TRACED_SYSCALLS), "-s", "256", "-o"])
            .arg(&trace_path)
            .arg(exe)
            .args(["exec", "--skip-git-repo-check", "-o"])
            .arg(&reply_path)
            .arg(prompt)
            .current_dir(work.join("repo"))
            .env("CODEX_HOME", work.join("home"))
            .env("DREAMFERENCE_CONFIG_PATH", work.join("config.toml"))
            .env("DREAMFERENCE_VLLM_HOST", host)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command.spawn().map_err(|error| format!("cannot start strace: {error}"))?;
        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if started.elapsed() < Duration::from_secs(super::SESSION_TIMEOUT_S) => {
                    std::thread::sleep(Duration::from_millis(200));
                }
                Ok(None) => {
                    stop_group(child.id());
                    let _ = child.wait();
                    break;
                }
                Err(error) => return Err(format!("strace failed: {error}")),
            }
        }
        let replied = std::fs::read_to_string(&reply_path).is_ok_and(|text| !text.trim().is_empty());
        let text = std::fs::read_to_string(&trace_path).unwrap_or_default();
        let (events, root, extras) = strace::parse(&text);
        let Some(root) = root else {
            return Err("strace recorded no process: it could not attach".to_string());
        };
        Ok((events, root, replied, strace::extra_lines(&extras)))
    }

    /// Ends the session's whole process group: strace alone, killed, would leave `ling` waiting.
    fn stop_group(pid: u32) {
        #[cfg(unix)]
        {
            let group = pid as libc::pid_t;
            for (signal, grace) in [(libc::SIGTERM, 10), (libc::SIGKILL, 5)] {
                // SAFETY: a plain signal to a process group this process started.
                if unsafe { libc::killpg(group, signal) } != 0 {
                    return;
                }
                let until = Instant::now() + Duration::from_secs(grace);
                while Instant::now() < until {
                    // SAFETY: signal 0 only asks whether the group still exists.
                    if unsafe { libc::killpg(group, 0) } != 0 {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(200));
                }
            }
        }
        #[cfg(not(unix))]
        let _ = pid;
    }
}

#[cfg(windows)]
mod windows {
    use std::net::IpAddr;
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::time::Duration;
    use std::time::Instant;

    use ferrisetw::EventRecord;
    use ferrisetw::SchemaLocator;
    use ferrisetw::UserTrace;
    use ferrisetw::parser::Parser;
    use ferrisetw::provider::Provider;

    use super::Event;

    const KERNEL_PROCESS: &str = "22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716";
    const KERNEL_NETWORK: &str = "7dd42a49-5329-4832-8dfd-43d979153a88";
    const DNS_CLIENT: &str = "1c95126e-7eea-49a9-a3fe-a378b03ddb4d";
    /// WINEVENT_KEYWORD_PROCESS, and Kernel-Network's IPv4 and IPv6 keywords.
    const PROCESS_KEYWORD: u64 = 0x10;
    const NETWORK_KEYWORDS: u64 = 0x30;
    /// Kernel-Process: ProcessStart.
    const PROCESS_START: u16 = 1;
    /// Kernel-Network: TCP send and connect (IPv4, IPv6), UDP send (IPv4, IPv6).
    const TCP_OUT: [u16; 4] = [10, 12, 26, 28];
    const UDP_OUT: [u16; 2] = [42, 58];
    /// DNS-Client: a query started.
    const DNS_QUERY: u16 = 3006;

    const SESSION_TIMEOUT: Duration = Duration::from_secs(300);
    /// How long the trace runs before the session starts and after it ends, for ETW's buffers.
    const SETTLE: Duration = Duration::from_secs(3);

    type Sink = Arc<Mutex<Vec<Event>>>;

    fn push(sink: &Sink, event: Event) {
        sink.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(event);
    }

    fn process(sink: Sink) -> Provider {
        Provider::by_guid(KERNEL_PROCESS)
            .any(PROCESS_KEYWORD)
            .add_callback(move |record: &EventRecord, locator: &SchemaLocator| {
                if record.event_id() != PROCESS_START {
                    return;
                }
                let Ok(schema) = locator.event_schema(record) else { return };
                let parser = Parser::create(record, &schema);
                let (Ok(pid), Ok(parent)) = (parser.try_parse::<u32>("ProcessID"), parser.try_parse::<u32>("ParentProcessID")) else {
                    return;
                };
                let image: String = parser.try_parse("ImageName").unwrap_or_default();
                let image = image.rsplit(['\\', '/']).next().unwrap_or_default().to_string();
                push(&sink, Event::Start { pid, parent, image });
            })
            .build()
    }

    fn network(sink: Sink) -> Provider {
        Provider::by_guid(KERNEL_NETWORK)
            .any(NETWORK_KEYWORDS)
            .add_callback(move |record: &EventRecord, locator: &SchemaLocator| {
                let id = record.event_id();
                let udp = UDP_OUT.contains(&id);
                if !udp && !TCP_OUT.contains(&id) {
                    return;
                }
                let Ok(schema) = locator.event_schema(record) else { return };
                let parser = Parser::create(record, &schema);
                let (Ok(pid), Ok(address), Ok(port)) =
                    (parser.try_parse::<u32>("PID"), parser.try_parse::<IpAddr>("daddr"), parser.try_parse::<u16>("dport"))
                else {
                    return;
                };
                // The port is recorded in network byte order.
                push(&sink, Event::Connect { pid, address, port: u16::from_be(port), udp });
            })
            .build()
    }

    fn dns(sink: Sink) -> Provider {
        Provider::by_guid(DNS_CLIENT)
            .add_callback(move |record: &EventRecord, locator: &SchemaLocator| {
                if record.event_id() != DNS_QUERY {
                    return;
                }
                let Ok(schema) = locator.event_schema(record) else { return };
                let parser = Parser::create(record, &schema);
                if let Ok(name) = parser.try_parse::<String>("QueryName") {
                    push(&sink, Event::Lookup { pid: record.process_id(), name });
                }
            })
            .build()
    }

    /// Traces one `ling exec` session. Returns the events, the session's process id and whether
    /// it replied.
    pub fn audit(host: &str, keep: bool) -> Result<(Vec<Event>, u32, bool), String> {
        let work = std::env::temp_dir().join(format!("mightling-audit-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&work);
        super::prepare(&work, host).map_err(|err| format!("could not prepare {}: {err}", work.display()))?;
        let result = traced_session(host, &work);
        if keep {
            println!("Kept: {}", work.display());
        } else {
            let _ = std::fs::remove_dir_all(&work);
        }
        result
    }

    fn traced_session(host: &str, work: &std::path::Path) -> Result<(Vec<Event>, u32, bool), String> {
        let exe = std::env::current_exe().map_err(|err| format!("could not find ling.exe: {err}"))?;
        let sink: Sink = Arc::default();
        let trace = UserTrace::new()
            .named(format!("MightlingEgressAudit{}", std::process::id()))
            .enable(process(sink.clone()))
            .enable(network(sink.clone()))
            .enable(dns(sink.clone()))
            .start_and_process()
            .map_err(|err| {
                format!("could not start an ETW trace ({err:?}): it needs Administrator rights or the Performance Log Users group")
            })?;
        std::thread::sleep(SETTLE);
        let reply = work.join("reply.txt");
        let spawned = std::process::Command::new(&exe)
            .args(["exec", "--skip-git-repo-check", "-o"])
            .arg(&reply)
            .arg(super::PROMPT)
            .current_dir(work.join("repo"))
            .env("CODEX_HOME", work.join("home"))
            .env("DREAMFERENCE_CONFIG_PATH", work.join("config.toml"))
            .env("DREAMFERENCE_VLLM_HOST", host)
            .env_remove("MIGHTLING_NODE")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        let root = match spawned {
            Ok(mut child) => {
                let root = child.id();
                let started = Instant::now();
                while matches!(child.try_wait(), Ok(None)) {
                    if started.elapsed() > SESSION_TIMEOUT {
                        let _ = child.kill();
                        let _ = child.wait();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(250));
                }
                root
            }
            Err(err) => {
                let _ = trace.stop();
                return Err(format!("could not start {}: {err}", exe.display()));
            }
        };
        std::thread::sleep(SETTLE);
        let _ = trace.stop();
        std::thread::sleep(Duration::from_secs(1));
        let events = std::mem::take(&mut *sink.lock().unwrap_or_else(std::sync::PoisonError::into_inner));
        let replied = std::fs::read_to_string(&reply).is_ok_and(|text| !text.trim().is_empty());
        Ok((events, root, replied))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: u32 = 100;
    const MODEL: (Option<IpAddr>, u16) = (Some(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST)), 8000);

    fn start(pid: u32, parent: u32, image: &str) -> Event {
        Event::Start { pid, parent, image: image.to_string() }
    }

    fn connect(pid: u32, address: &str, port: u16) -> Event {
        Event::Connect { pid, address: address.parse().unwrap(), port, udp: false }
    }

    #[test]
    fn the_model_servers_url_gives_its_address_and_port() {
        assert_eq!(model_endpoint("http://127.0.0.1:8000"), Some((Some([127, 0, 0, 1].into()), 8000)));
        assert_eq!(model_endpoint("http://localhost:30000/v1"), Some((Some([127, 0, 0, 1].into()), 30000)));
        assert_eq!(model_endpoint("http://192.168.1.20:8000/"), Some((Some([192, 168, 1, 20].into()), 8000)));
        assert_eq!(model_endpoint("http://[::1]:8000"), Some((Some("::1".parse().unwrap()), 8000)));
        assert_eq!(model_endpoint("https://spark.local"), Some((None, 443)));
        assert_eq!(model_endpoint("http://host:port"), None);
    }

    #[test]
    fn only_the_sessions_tree_is_judged() {
        let events = [
            start(ROOT, 1, "ling.exe"),
            start(101, ROOT, "codex-command-runner.exe"),
            start(102, 101, "cmd.exe"),
            start(200, 1, "MsMpEng.exe"),
            connect(ROOT, "127.0.0.1", 8000),
            connect(102, "127.0.0.1", 8888),
            // Defender, outside the tree.
            connect(200, "20.190.1.1", 443),
            Event::Lookup { pid: 200, name: "wdcp.microsoft.com".to_string() },
        ];
        let session = session(&events, ROOT);
        assert_eq!(process_tree(&events, ROOT), HashSet::from([ROOT, 101, 102]));
        assert_eq!(judge(&session, MODEL, true), (Status::Pass, Vec::new()));
    }

    #[test]
    fn the_verdict_names_each_problem() {
        let events = [
            start(ROOT, 1, "ling.exe"),
            start(101, ROOT, "git-remote-https.exe"),
            connect(ROOT, "127.0.0.1", 8000),
            connect(ROOT, "104.18.1.1", 443),
            connect(ROOT, "127.0.0.1", BLACKHOLE_PORT),
            connect(ROOT, "::1", 5000),
            Event::Lookup { pid: ROOT, name: "ab.chatgpt.com.".to_string() },
            Event::Lookup { pid: ROOT, name: "localhost".to_string() },
        ];
        let (status, problems) = judge(&session(&events, ROOT), MODEL, true);
        assert_eq!(status, Status::Fail);
        assert_eq!(problems.len(), 5, "{problems:#?}");
        assert!(problems[0].starts_with("connected to 104.18.1.1:443 (1x): not on this machine"));
        assert!(problems.iter().any(|problem| problem.contains("127.0.0.1:9") && problem.contains("upstream vendor")));
        assert!(problems.iter().any(|problem| problem.contains("[::1]:5000") && problem.contains("not on the allowlist")));
        assert!(problems.iter().any(|problem| problem == "asked a resolver for ab.chatgpt.com (1x)"));
        assert!(problems.iter().any(|problem| problem.contains("git-remote-https.exe")));
    }

    #[test]
    fn a_node_on_the_network_is_allowed_by_its_address_and_port() {
        let node = (Some(IpAddr::from([192, 168, 1, 20])), 8000);
        let events = [start(ROOT, 1, "ling.exe"), connect(ROOT, "192.168.1.20", 8000)];
        assert_eq!(judge(&session(&events, ROOT), node, true).0, Status::Pass);
        let events = [start(ROOT, 1, "ling.exe"), connect(ROOT, "192.168.1.20", 8000), connect(ROOT, "192.168.1.20", 22)];
        assert_eq!(judge(&session(&events, ROOT), node, true).0, Status::Fail);
        // A loopback model port is not allowed for a session whose model is elsewhere.
        let events = [start(ROOT, 1, "ling.exe"), connect(ROOT, "192.168.1.20", 8000), connect(ROOT, "127.0.0.1", 8000)];
        assert_eq!(judge(&session(&events, ROOT), node, true).0, Status::Fail);
    }

    #[test]
    fn a_trace_that_showed_nothing_is_never_a_pass() {
        let events = [start(ROOT, 1, "ling.exe"), connect(ROOT, "127.0.0.1", 8000)];
        assert_eq!(judge(&session(&events, ROOT), MODEL, false).0, Status::TraceFailed);
        // Replied, yet no connection to the model server seen: the network events were not read.
        let events = [start(ROOT, 1, "ling.exe")];
        assert_eq!(judge(&session(&events, ROOT), MODEL, true).0, Status::TraceFailed);
        assert_eq!(Status::TraceFailed.exit_code(), 2);
    }

    #[test]
    fn the_report_says_what_the_verdict_does_not_cover() {
        let events = [start(ROOT, 1, "ling.exe"), connect(ROOT, "127.0.0.1", 8000)];
        let session = session(&events, ROOT);
        let lines = render(&session, MODEL, Status::Pass, &[]);
        assert_eq!(lines[0], "✅ Egress audit: pass");
        assert!(lines.iter().any(|line| line.contains("127.0.0.1:8000") && line.ends_with("model server")));
        let expected = if cfg!(windows) { "Not judged: Windows' own traffic" } else { "Not judged: the code index's and the file index's indexers" };
        assert!(lines.last().unwrap().starts_with(expected));
    }

    // -- the strace reading (the Python StraceParser's recorded traces, audit-fixtures/) ----------

    const EXEC_PASS: &str = include_str!("../audit-fixtures/exec_pass.strace");
    const EXEC_LEAKS: &str = include_str!("../audit-fixtures/exec_leaks.strace");
    const CURL: &str = include_str!("../audit-fixtures/curl_example.strace");

    #[test]
    fn the_recorded_session_reaches_only_the_model_server_and_gmail() {
        let (events, root, extras) = strace::parse(EXEC_PASS);
        assert!(root.is_some());
        let session = session_all(&events);
        let destinations: Vec<(String, usize)> = session.destinations.iter().map(|((a, p), n)| (display(a, *p), *n)).collect();
        assert_eq!(destinations, vec![("127.0.0.1:8000".to_string(), 2), ("127.0.0.1:8767".to_string(), 1)]);
        assert!(session.lookups.is_empty() && session.dns_servers.is_empty());
        // nscd's socket is glibc asking for a name-service cache that is not there.
        assert!(extras.unix_sockets.contains_key("/var/run/nscd/socket"));
        // The launcher's own helpers and Codex's git probes, each counted once per successful exec.
        assert_eq!(session.processes["ling"], 1);
        assert_eq!(session.processes["ling-code"], 2);
        assert!(session.processes["git"] >= 5);
        assert!(extras.lines > 100);
        assert_eq!(judge(&session, MODEL, true), (Status::Pass, Vec::new()));
    }

    #[test]
    fn the_channels_the_patches_closed_fail_the_verdict_by_name() {
        let (events, _, _) = strace::parse(EXEC_LEAKS);
        let session = session_all(&events);
        let lookups: Vec<(&str, usize)> = session.lookups.iter().map(|(n, c)| (n.as_str(), *c)).collect();
        assert_eq!(lookups, vec![("ab.chatgpt.com", 2), ("chatgpt.com", 1), ("raw.githubusercontent.com", 1)]);
        assert_eq!(session.dns_servers.get("127.0.0.53:53"), Some(&3));
        for target in ["104.18.32.47:443", "[2606:4700:4400::6812:202f]:443", "140.82.121.3:443", "185.199.108.133:443"] {
            let count = session.destinations.iter().find(|((a, p), _)| display(a, *p) == target).map(|(_, n)| *n);
            assert_eq!(count, Some(1), "{target}");
        }
        assert_eq!(session.processes.get("git-remote-https"), Some(&1));
        let (status, problems) = judge(&session, MODEL, true);
        assert_eq!(status, Status::Fail);
        let report = problems.join("\n");
        for needle in [
            "ab.chatgpt.com",
            "chatgpt.com (1x)",
            "raw.githubusercontent.com",
            "git-remote-https",
            "104.18.32.47:443 (1x): not on this machine",
            "[2606:4700:4400::6812:202f]:443",
            "127.0.0.1:9 (1x): a call to the upstream vendor's backend that no patch closes",
        ] {
            assert!(report.contains(needle), "{needle}");
        }
        // A leak is a failure whether or not the session answered.
        assert_eq!(judge(&session, MODEL, false).0, Status::Fail);
    }

    #[test]
    fn a_real_lookup_and_connect_are_read_from_a_recording() {
        // Recorded, not written by hand: this is what glibc and the kernel actually print.
        let (events, _, _) = strace::parse(CURL);
        let session = session_all(&events);
        assert_eq!(session.lookups.get("example.com"), Some(&2)); // the A and the AAAA query
        assert_eq!(session.dns_servers.get("127.0.0.53:53"), Some(&1));
        assert!(session.destinations.keys().any(|(a, p)| !is_loopback(a) && *p == 443));
    }

    #[test]
    fn a_udp_connect_is_a_route_lookup_until_a_payload_follows() {
        let trace = concat!(
            "100 execve(\"/usr/bin/ling\", [\"ling\", \"exec\"], 0x7ffd /* 20 vars */) = 0\n",
            "100 connect(23<UDPv6:[10868489]>, {sa_family=AF_INET6, sin6_port=htons(443), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, \"2001:4860:4860::8888\", &sin6_addr), sin6_scope_id=0}, 28) = 0\n",
            "100 connect(24<UDP:[10868490]>, {sa_family=AF_INET, sin_port=htons(4433), sin_addr=inet_addr(\"1.2.3.4\")}, 16) = 0\n",
            "100 write(24<UDP:[127.0.0.1:40000->1.2.3.4:4433]>, \"hello\", 5) = 5\n",
            "100 connect(25<TCP:[10868491]>, {sa_family=AF_INET, sin_port=htons(8000), sin_addr=inet_addr(\"127.0.0.1\")}, 16) = 0\n",
        );
        let (events, root, extras) = strace::parse(trace);
        assert_eq!(root, Some(100));
        assert_eq!(extras.route_lookups.get("[2001:4860:4860::8888]:443"), Some(&1));
        let session = session_all(&events);
        let destinations: Vec<String> = session.destinations.keys().map(|(a, p)| display(a, *p)).collect();
        // The Chromium-style reachability check is not a destination; the UDP socket written to is.
        assert_eq!(destinations, vec!["1.2.3.4:4433", "127.0.0.1:8000"]);
        assert_eq!(session.processes.get("ling"), Some(&1));
    }

    #[test]
    fn a_query_whose_name_strace_cut_is_a_finding_and_a_failed_exec_is_not_a_process() {
        let trace = concat!(
            "100 execve(\"/usr/local/sbin/git\", [\"git\"], 0x1 /* 1 var */) = -1 ENOENT (No such file or directory)\n",
            "100 execve(\"/usr/bin/git\", [\"git\", \"status\"], 0x1 /* 1 var */) = 0\n",
            "100 connect(5<UDP:[1]>, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr(\"127.0.0.53\")}, 16) = 0\n",
            "100 sendmmsg(5<UDP:[127.0.0.1:1->127.0.0.53:53]>, [{msg_hdr={msg_iov=[{iov_base=\"\\0\\1\\1\\0\\0\\1\\0\\0\\0\\0\\0\\0\\3ab\", iov_len=16}]}}], 1, MSG_NOSIGNAL) = 1\n",
        );
        let (events, _, _) = strace::parse(trace);
        let session = session_all(&events);
        assert_eq!(session.processes.get("git"), Some(&1));
        assert!(session.lookups.is_empty());
        assert_eq!(session.dns_servers.get("127.0.0.53:53"), Some(&1), "{events:?}");
        let (status, problems) = judge(&session, MODEL, true);
        assert_eq!(status, Status::Fail);
        assert!(problems[0].starts_with("sent a DNS query to 127.0.0.53:53 (1x) whose name could not be read"));
        assert!(render(&session, MODEL, status, &problems).iter().any(|line| line.starts_with("Resolvers queried: 127.0.0.53:53 (1x)")));
    }

    #[test]
    fn strace_strings_escapes_and_dns_names_are_read_as_strace_prints_them() {
        assert_eq!(strace::unescape("a\\nb"), b"a\nb");
        assert_eq!(strace::unescape("\\0\\1\\236"), vec![0, 1, 0o236]);
        assert_eq!(strace::unescape("\\x1f\\x41"), vec![0x1f, 0x41]);
        assert_eq!(strace::strings_in("\"a\\\"b\", 3, \"c\""), vec![b"a\"b".to_vec(), b"c".to_vec()]);
        let mut query = vec![0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0];
        query.extend_from_slice(b"\x07example\x03com\x00");
        assert_eq!(strace::dns_query_name(&query), Some("example.com".to_string()));
        // A response (QR set), a short payload and a name cut off by strace's limit are not names.
        let mut response = query.clone();
        response[2] = 0x81;
        assert_eq!(strace::dns_query_name(&response), None);
        assert_eq!(strace::dns_query_name(&query[..10]), None);
        assert_eq!(strace::dns_query_name(&query[..query.len() - 1]), None);
        assert_eq!(strace::destination_in("{sa_family=AF_INET, sin_port=htons(8000), sin_addr=inet_addr(\"127.0.0.1\")}"), Some(("127.0.0.1".parse().unwrap(), 8000)));
        assert_eq!(strace::fd_label("23<UDPv6:[10868489]>"), ("23".to_string(), "UDPv6", "10868489".to_string()));
        assert_eq!(strace::fd_label("7"), ("7".to_string(), "", String::new()));
    }
}
