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
//! The verdict is plain Rust over recorded events, tested on every platform; only the recording is
//! Windows code. On Linux the command points at `ling-admin audit egress`.

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
const NETWORKED_GIT: &[&str] = &["git-remote-https.exe", "git-remote-http.exe", "git-remote-ftp.exe", "git-remote-ftps.exe", "ssh.exe"];

#[cfg_attr(not(windows), allow(dead_code))]
const PROMPT: &str = "Reply with exactly: pong";
const USAGE: &str = "Usage: ling audit egress [--host <model server URL>] [--keep]";

/// What the trace recorded, reduced to what the verdict reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    /// A process started; `image` is its file name.
    Start { pid: u32, parent: u32, image: String },
    /// A process sent to, or connected to, an address.
    Connect { pid: u32, address: IpAddr, port: u16, udp: bool },
    /// A process asked the DNS client for a name.
    Lookup { pid: u32, name: String },
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
    /// Image to the number of starts.
    pub processes: BTreeMap<String, usize>,
}

pub fn session(events: &[Event], root: u32) -> Session {
    let tree = process_tree(events, root);
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
    let programs: Vec<String> = session.processes.iter().map(|(image, count)| format!("{image} ({count})")).collect();
    lines.push(format!("Processes started: {}", if programs.is_empty() { "none".to_string() } else { programs.join(", ") }));
    lines.push(
        "Not judged: Windows' own traffic (Defender, Windows Update, telemetry) is outside this session's processes, so \"ling made no connection\" is not \"this computer made none\". Defender's automatic sample submission can upload an unknown program, ling's own included, to Microsoft."
            .to_string(),
    );
    lines
}

/// Runs `ling audit …` and returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let mut host = None;
    let mut keep = false;
    let mut words = args.iter().map(String::as_str);
    if words.next() != Some("egress") {
        println!("{USAGE}");
        return 2;
    }
    while let Some(word) = words.next() {
        match word {
            "--host" => host = words.next().map(str::to_string),
            "--keep" => keep = true,
            _ => {
                println!("{USAGE}");
                return 2;
            }
        }
    }
    if !cfg!(windows) {
        println!("On a node the egress audit is `ling-admin audit egress` (strace); this one is for Windows.");
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
    match windows::audit(&host, keep) {
        Ok((events, root, replied)) => {
            let session = session(&events, root);
            let (status, problems) = judge(&session, model, replied);
            for line in render(&session, model, status, &problems) {
                println!("{line}");
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
        crate::node::Resolution::Overlay { node } => Some(node.model_url()),
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
/// the user's influences the result and nothing of theirs is touched. The code index is off: its
/// indexers run detached and outlive the session, so they are not part of what this can show.
#[cfg_attr(not(windows), allow(dead_code))]
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
    std::fs::write(work.join("config.toml"), format!("vllm_host = {host}\ncode_index_enabled = false\n"))
}

#[cfg(not(windows))]
mod windows {
    pub fn audit(_host: &str, _keep: bool) -> Result<(Vec<super::Event>, u32, bool), String> {
        Err("the ETW trace exists only on Windows".to_string())
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
        assert!(lines.last().unwrap().starts_with("Not judged: Windows' own traffic"));
    }
}
