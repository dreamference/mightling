//! Runs the two binaries against a local HTTP server standing in for SearXNG and for web pages.

use std::io::BufRead;
use std::io::BufReader;
use std::io::Write;
use std::net::TcpListener;
use std::process::Command;
use std::process::Output;
use std::sync::Arc;
use std::sync::Mutex;

/// A server that answers every connection with `respond(request line)` and records the lines.
struct Server {
    base: String,
    requests: Arc<Mutex<Vec<String>>>,
}

impl Server {
    fn start(respond: impl Fn(&str) -> Vec<u8> + Send + 'static) -> Server {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let seen = requests.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    continue;
                }
                let mut header = String::new();
                while reader.read_line(&mut header).is_ok_and(|n| n > 2) {
                    header.clear();
                }
                let line = line.trim_end().to_string();
                seen.lock().unwrap().push(line.clone());
                // A client that stops reading at its size cap makes this write fail; that is the
                // behaviour under test, not an error.
                let _ = stream.write_all(&respond(&line));
            }
        });
        Server { base, requests }
    }

    fn requests(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }
}

fn response(status: &str, content_type: &str, body: &[u8], extra: &str) -> Vec<u8> {
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\n{extra}Connection: close\r\n\r\n",
        body.len()
    );
    [head.as_bytes(), body].concat()
}

fn run(binary: &str, args: &[&str], env: &[(&str, &str)]) -> Output {
    let mut command = Command::new(binary);
    command.args(args);
    for name in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ] {
        command.env_remove(name);
    }
    // The air-gap level comes from the environment and from files under HOME: a test must not
    // inherit the level of the session or the machine that runs it.
    for name in [
        "DREAMFERENCE_PUFFIN_AIRGAPPED",
        "DREAMFERENCE_CONFIG_PATH",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
        "CODEX_HOME",
    ] {
        command.env_remove(name);
    }
    command.env("HOME", std::env::temp_dir().join("puffin-web-tests-no-home"));
    for (name, value) in env {
        command.env(name, value);
    }
    command.output().unwrap()
}

fn search(args: &[&str], env: &[(&str, &str)]) -> Output {
    run(env!("CARGO_BIN_EXE_puffin-search"), args, env)
}

fn fetch(args: &[&str], env: &[(&str, &str)]) -> Output {
    run(env!("CARGO_BIN_EXE_puffin-fetch"), args, env)
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

const SEARXNG_JSON: &str = r#"{"query": "q", "answers": [], "unresponsive_engines": [["brave", "CAPTCHA"]],
  "results": [{"title": "Lisbon - BBC Weather", "url": "https://www.bbc.com/weather/2267057",
               "content": "Light cloud", "engine": "bing"}]}"#;

#[test]
fn search_asks_searxng_for_json_and_prints_results() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let output = search(
        &["lisbon", "weather"],
        &[("DREAMFERENCE_SEARXNG_URL", &server.base)],
    );
    assert!(output.status.success());
    assert_eq!(
        stdout(&output),
        "1. Lisbon - BBC Weather\n   https://www.bbc.com/weather/2267057\n   Light cloud\n"
    );
    let request = &server.requests()[0];
    assert!(
        request.starts_with(
            "GET /search?q=lisbon+weather&format=json&categories=general&language=en "
        ),
        "{request}"
    );
}

#[test]
fn at_duckduckgo_search_names_the_engine_and_no_category() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let output = search(
        &["lisbon", "--json"],
        &[
            ("DREAMFERENCE_SEARXNG_URL", &server.base),
            ("DREAMFERENCE_PUFFIN_AIRGAPPED", "ddg"),
        ],
    );
    assert!(output.status.success());
    let request = &server.requests()[0];
    assert!(
        request.starts_with("GET /search?q=lisbon&format=json&engines=duckduckgo&language=en "),
        "{request}"
    );
    assert!(!request.contains("categories"));
    let printed: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(printed["airgapped"], "duckduckgo");
}

#[test]
fn at_duckduckgo_no_hint_says_to_restart_or_start_anything() {
    let body = r#"{"results": [], "answers": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}"#;
    let server =
        Server::start(move |_| response("200 OK", "application/json", body.as_bytes(), ""));
    let level = ("DREAMFERENCE_PUFFIN_AIRGAPPED", "duckduckgo");
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &server.base), level]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        stdout(&output),
        "❌ DuckDuckGo did not answer (duckduckgo: CAPTCHA). This session searches through DuckDuckGo only (/airgapped duckduckgo).\n"
    );
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", "http://127.0.0.1:9"), level]);
    assert!(!stdout(&output).contains("💡"), "{}", stdout(&output));
}

#[test]
fn at_on_neither_command_sends_anything() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let level = ("DREAMFERENCE_PUFFIN_AIRGAPPED", "on");
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &server.base), level]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        stdout(&output),
        "❌ Web access is off in this session (/airgapped on). Only the user can change that, with /airgapped.\n"
    );
    let output = search(&["q", "--json"], &[("DREAMFERENCE_SEARXNG_URL", &server.base), level]);
    let printed: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(printed["airgapped"], "on");
    let output = fetch(&[&format!("{}/page", server.base)], &[level]);
    assert_eq!(output.status.code(), Some(1));
    assert!(stdout(&output).contains("/airgapped on"));
    assert!(server.requests().is_empty(), "{:?}", server.requests());
}

#[test]
fn the_session_file_decides_and_the_user_level_file_is_not_loosened_by_the_repository() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let root = std::env::temp_dir().join(format!("puffin-web-level-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let codex_home = root.join("puffin-home");
    std::fs::create_dir_all(codex_home.join("airgapped")).unwrap();
    std::fs::write(codex_home.join("airgapped/thread-1"), "on\n").unwrap();
    let home = codex_home.to_string_lossy().into_owned();
    // The session's file says `on`: nothing is sent.
    let output = search(
        &["q"],
        &[("DREAMFERENCE_SEARXNG_URL", &server.base), ("CODEX_HOME", &home), ("CODEX_THREAD_ID", "thread-1")],
    );
    assert!(stdout(&output).contains("/airgapped on"));
    // A subagent has no file of its own and takes the root session's.
    let output = search(
        &["q"],
        &[
            ("DREAMFERENCE_SEARXNG_URL", &server.base),
            ("CODEX_HOME", &home),
            ("CODEX_THREAD_ID", "thread-2"),
            ("CODEX_SESSION_ID", "thread-1"),
        ],
    );
    assert!(stdout(&output).contains("/airgapped on"));
    // Another session is not affected.
    let output = search(
        &["q"],
        &[("DREAMFERENCE_SEARXNG_URL", &server.base), ("CODEX_HOME", &home), ("CODEX_THREAD_ID", "thread-3")],
    );
    assert!(output.status.success());
    // A user-level `on` stands whatever the repository's file says.
    let user = root.join("user-home");
    std::fs::create_dir_all(user.join(".config/dreamference")).unwrap();
    std::fs::write(user.join(".config/dreamference/config.toml"), "puffin_airgapped = \"on\"\n").unwrap();
    let repository = root.join("dreamference.toml");
    std::fs::write(&repository, "puffin_airgapped = \"off\"\n").unwrap();
    let output = search(
        &["q"],
        &[
            ("DREAMFERENCE_SEARXNG_URL", &server.base),
            ("HOME", &user.to_string_lossy()),
            ("DREAMFERENCE_CONFIG_PATH", &repository.to_string_lossy()),
        ],
    );
    assert!(stdout(&output).contains("/airgapped on"));
    assert_eq!(server.requests().len(), 1);
}

#[test]
fn a_sandbox_without_network_is_named_as_the_sandbox_not_as_the_level() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let sandbox = ("CODEX_SANDBOX_NETWORK_DISABLED", "1");
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &server.base), sandbox]);
    assert_eq!(output.status.code(), Some(1));
    assert!(stdout(&output).contains("the sandbox it runs in does not allow it"));
    assert!(!stdout(&output).contains("airgapped") && !stdout(&output).contains("💡"));
    let output = fetch(&[&format!("{}/page", server.base)], &[sandbox]);
    assert!(stdout(&output).contains("the sandbox it runs in does not allow it"));
    assert!(server.requests().is_empty());
}

#[test]
fn search_json_has_the_documented_fields() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let output = search(
        &["q", "--json", "-n", "3"],
        &[("DREAMFERENCE_SEARXNG_URL", &server.base)],
    );
    let printed: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(printed["query"], "q");
    assert_eq!(printed["result_count"], 1);
    assert_eq!(printed["results"][0]["snippet"], "Light cloud");
}

#[test]
fn search_never_goes_through_a_proxy() {
    let server =
        Server::start(|_| response("200 OK", "application/json", SEARXNG_JSON.as_bytes(), ""));
    let dead = "http://127.0.0.1:9";
    let output = search(
        &["q"],
        &[
            ("DREAMFERENCE_SEARXNG_URL", &server.base),
            ("HTTP_PROXY", dead),
            ("ALL_PROXY", dead),
        ],
    );
    assert!(output.status.success(), "{}", stdout(&output));
}

#[test]
fn search_reports_every_failed_engine() {
    let body = r#"{"results": [], "answers": [], "unresponsive_engines": [["brave", "CAPTCHA"], ["startpage", "timeout"]]}"#;
    let server =
        Server::start(move |_| response("200 OK", "application/json", body.as_bytes(), ""));
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &server.base)]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        stdout(&output),
        "❌ SearXNG could not reach any search engine: brave: CAPTCHA; startpage: timeout\n\
         💡 If this machine is online, restart the container: docker restart dreamference-searxng\n"
    );
}

#[test]
fn search_explains_a_searxng_without_the_json_format() {
    let html = Server::start(|_| response("200 OK", "text/html", b"<html>results</html>", ""));
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &html.base)]);
    assert_eq!(output.status.code(), Some(1));
    assert!(stdout(&output).contains("did not return JSON"));
    assert!(stdout(&output).contains("search.formats"));

    let forbidden = Server::start(|_| response("403 Forbidden", "text/html", b"", ""));
    let output = search(&["q"], &[("DREAMFERENCE_SEARXNG_URL", &forbidden.base)]);
    assert_eq!(output.status.code(), Some(1));
    assert!(stdout(&output).contains("answered HTTP 403"));
    assert!(stdout(&output).contains("search.formats"));
}

#[test]
fn search_names_the_command_that_starts_an_unreachable_searxng() {
    let output = search(
        &["q"],
        &[("DREAMFERENCE_SEARXNG_URL", "http://127.0.0.1:9")],
    );
    assert_eq!(output.status.code(), Some(1));
    let printed = stdout(&output);
    assert!(
        printed.starts_with("❌ SearXNG at http://127.0.0.1:9 is unreachable"),
        "{printed}"
    );
    assert!(printed.contains("💡 Start it with: puffin-admin searxng start"));
}

#[test]
fn search_without_a_query_is_a_usage_error() {
    assert_eq!(search(&[], &[]).status.code(), Some(2));
}

#[test]
fn fetch_prints_title_and_text_and_follows_redirects() {
    let server = Server::start(|line| {
        if line.starts_with("GET /moved ") {
            response("302 Found", "text/html", b"", "Location: /page\r\n")
        } else {
            response(
                "200 OK",
                "text/html; charset=utf-8",
                "<html><head><title>Café</title><script>x()</script></head><body><p>Hello</p></body></html>"
                    .as_bytes(),
                "",
            )
        }
    });
    let url = format!("{}/moved", server.base);
    let output = fetch(&[&url], &[]);
    assert!(output.status.success());
    assert_eq!(stdout(&output), "# Café\n\nCafé\nHello\n");
    let printed: serde_json::Value =
        serde_json::from_slice(&fetch(&[&url, "--json"], &[]).stdout).unwrap();
    assert_eq!(printed["final_url"], format!("{}/page", server.base));
    assert_eq!(printed["status"], 200);
    assert_eq!(printed["truncated"], false);
}

#[test]
fn fetch_reports_http_errors() {
    let server = Server::start(|_| response("404 Not Found", "text/html", b"gone", ""));
    let output = fetch(&[&format!("{}/missing", server.base)], &[]);
    assert_eq!(output.status.code(), Some(1));
    assert!(
        stdout(&output).starts_with("❌ request failed: HTTP 404"),
        "{}",
        stdout(&output)
    );
}

#[test]
fn fetch_refuses_anything_but_http() {
    let output = fetch(&["file:///etc/passwd"], &[]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        stdout(&output),
        "❌ Only http and https URLs are supported, got file scheme\n"
    );
}

#[test]
fn fetch_stops_downloading_at_the_size_cap() {
    let body = vec![b'a'; 6 * 1024 * 1024];
    let server = Server::start(move |_| response("200 OK", "text/plain", &body, ""));
    let output = fetch(&[&server.base, "--max-chars", "100000000", "--json"], &[]);
    let printed: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(printed["text"].as_str().unwrap().len(), 100_000);
    assert_eq!(printed["truncated"], true);
    let output = fetch(&[&server.base, "--max-chars", "10"], &[]);
    assert!(stdout(&output).ends_with("aaaaaaaaaa\n\n[truncated at 10 chars]\n"));
}

#[test]
fn fetch_uses_the_environment_proxy_except_for_no_proxy_hosts() {
    let proxy = Server::start(|_| response("200 OK", "text/plain", b"via proxy", ""));
    let output = fetch(
        &["http://example.invalid/page"],
        &[("http_proxy", &proxy.base)],
    );
    assert_eq!(stdout(&output), "via proxy\n");
    assert_eq!(
        proxy.requests()[0],
        "GET http://example.invalid/page HTTP/1.1"
    );

    let output = fetch(
        &["http://example.invalid/page"],
        &[("http_proxy", &proxy.base), ("no_proxy", "invalid")],
    );
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(proxy.requests().len(), 1);
}
