//! The sync hub's routes (`/api/sync/`) through the router in memory: off until enabled, who a caller
//! is, the pairing counters, what an upload and a read take from their query, and the long polls and
//! command routing of the live half. No real network, no real `ling`, nothing outside a scratch folder.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use axum::body::Body;
use axum::http::Request;
use axum::http::StatusCode;
use axum::http::header;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use ling_web_server::sync_store;
use serde_json::Value;
use serde_json::json;
use tower::ServiceExt;

const HOST: &str = "127.0.0.1:3100";
const ORIGIN: &str = "http://127.0.0.1:3100";
const THREAD: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";
const FILE: &str = "rollout-2026-09-30T10-38-40-01a0f1ae-2da4-7942-99ae-c3151dc137cc.jsonl";

struct Scratch {
    dir: PathBuf,
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("lw-sh-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch { dir }
}

fn config(dir: &std::path::Path) -> Config {
    let codex_home = dir.join("home");
    let state_dir = auth::state_dir(&codex_home);
    Config {
        codex_home,
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: dir.join("ling"), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        user_config: Some(dir.join("user/.config/dreamference/config.toml")),
        forge_url: None,
        state_dir,
        port: 3100,
        lan_names: Vec::new(),
        public_names: Vec::new(),
    }
}

/// A server over a scratch home, the hub on or off.
fn world(tag: &str, on: bool) -> (Scratch, Arc<Server>) {
    let scratch = scratch(tag);
    let server = Server::new(config(&scratch.dir)).unwrap();
    if on {
        sync_store::set_enabled(&server.config.codex_home, true).unwrap();
    }
    (scratch, server)
}

async fn call(server: &Arc<Server>, request: Request<Body>) -> (StatusCode, String) {
    let response = server::router(server.clone()).oneshot(request).await.unwrap();
    let status = response.status();
    let body = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, String::from_utf8_lossy(&body).into_owned())
}

/// A request with `credential`: `Bearer <secret>` for an agent, else a cookie header.
fn send(method: &str, uri: &str, credential: &str, body: &str) -> Request<Body> {
    let builder = Request::builder().method(method).uri(uri).header(header::HOST, HOST).header(header::ORIGIN, ORIGIN).header(header::CONTENT_TYPE, "application/json");
    let builder = if credential.starts_with("Bearer ") { builder.header(header::AUTHORIZATION, credential) } else { builder.header(header::COOKIE, credential) };
    builder.body(Body::from(body.to_string())).unwrap()
}

async fn ask(server: &Arc<Server>, method: &str, uri: &str, credential: &str, body: &str) -> (StatusCode, Value) {
    let (status, text) = call(server, send(method, uri, credential, body)).await;
    (status, serde_json::from_str(&text).unwrap_or(Value::String(text)))
}

async fn sign_in(server: &Arc<Server>) -> String {
    let code = auth::issue_login_code(&server.config.state_dir).unwrap();
    let login = Request::builder().uri(format!("/login?code={code}")).header(header::HOST, HOST);
    let response = server::router(server.clone()).oneshot(login.body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::SEE_OTHER);
    response.headers().get(header::SET_COOKIE).unwrap().to_str().unwrap().split(';').next().unwrap().to_string()
}

/// A paired sync agent: its id and its `Bearer` credential.
fn agent(server: &Arc<Server>, name: &str) -> (String, String) {
    let (id, secret) = auth::add_device_of(&server.config.state_dir, name, auth::DeviceKind::Sync).unwrap();
    (id, format!("Bearer {secret}"))
}

/// A paired browser: its id and its cookie header.
fn browser(server: &Arc<Server>, name: &str) -> (String, String) {
    let (id, token) = auth::add_device(&server.config.state_dir, name).unwrap();
    (id, format!("{}={token}", auth::DEVICE_COOKIE))
}

async fn command(server: &Arc<Server>, credential: &str, body: Value) -> (StatusCode, Value) {
    ask(server, "POST", "/api/sync/commands", credential, &body.to_string()).await
}

/// The hash an upload from the start names as what came before it.
fn empty() -> String {
    sync_store::prefix_hash(b"")
}

fn upload_uri(extra: &str) -> String {
    format!("/api/sync/rollouts/{THREAD}?{extra}")
}

#[tokio::test]
async fn every_sync_route_is_a_plain_404_while_the_hub_is_off_and_nothing_is_written() {
    let (scratch, server) = world("off", false);
    let owner = sign_in(&server).await;
    let (id, secret) = agent(&server, "laptop");
    let routes = [
        ("GET", "/api/sync/status".to_string()),
        ("POST", "/api/sync/pair".to_string()),
        ("GET", "/api/sync/manifest".to_string()),
        ("GET", format!("/api/sync/rollouts/{THREAD}")),
        ("PUT", upload_uri("offset=0")),
        ("GET", "/api/sync/threads".to_string()),
        ("GET", format!("/api/sync/threads/{THREAD}")),
        ("POST", format!("/api/sync/threads/{THREAD}/meta")),
        ("POST", format!("/api/sync/threads/{THREAD}/delete")),
        ("POST", format!("/api/sync/threads/{THREAD}/withdraw")),
        ("POST", "/api/sync/forget".to_string()),
        ("POST", "/api/sync/host".to_string()),
        ("GET", format!("/api/sync/live/{THREAD}")),
        ("POST", "/api/sync/commands".to_string()),
        ("GET", "/api/sync/commands/1".to_string()),
        ("GET", "/api/sync/notices".to_string()),
    ];
    for (method, uri) in &routes {
        for credential in [&owner, &secret] {
            let (status, body) = call(&server, send(method, uri, credential, "{}")).await;
            assert_eq!((status, body.as_str()), (StatusCode::NOT_FOUND, "Not found.\n"), "{method} {uri}");
        }
    }
    assert!(auth::load_devices(&server.config.state_dir).iter().any(|device| device.id == id), "a forget with the hub off revokes nothing");
    assert!(!sync_store::hub_dir(&server.config.codex_home).exists(), "nothing is opened or written while it is off");
    drop(scratch);
}

#[tokio::test]
async fn the_hub_going_off_stops_every_route_at_once() {
    let (_scratch, server) = world("toggle", true);
    let owner = sign_in(&server).await;
    for uri in ["/api/sync/notices", "/api/sync/status", "/api/sync/threads"] {
        let (status, _) = ask(&server, "GET", uri, &owner, "").await;
        assert_eq!(status, StatusCode::OK, "{uri}");
    }
    sync_store::set_enabled(&server.config.codex_home, false).unwrap();
    for uri in ["/api/sync/notices", "/api/sync/status", "/api/sync/threads"] {
        let (status, body) = ask(&server, "GET", uri, &owner, "").await;
        assert_eq!((status, body), (StatusCode::NOT_FOUND, Value::String("Not found.\n".to_string())), "{uri}");
    }
}

#[tokio::test]
async fn a_caller_is_an_agent_by_its_secret_and_a_browser_or_the_owner_by_cookie() {
    let (_scratch, server) = world("caller", true);
    let owner = sign_in(&server).await;
    let (agent_id, secret) = agent(&server, "laptop");
    let (_, control) = browser(&server, "tablet");
    let (watch_id, watch) = browser(&server, "phone");
    auth::set_scope(&server.config.state_dir, "phone", auth::Scope::Watch).unwrap();
    let caller = |body: &Value| (body["caller"]["agent"].as_bool().unwrap(), body["caller"]["scope"].as_str().unwrap().to_string(), body["caller"]["device"].as_str().unwrap().to_string());

    let (status, body) = ask(&server, "GET", "/api/sync/status", &secret, "").await;
    assert_eq!((status, caller(&body)), (StatusCode::OK, (true, "control".to_string(), agent_id)));
    let (status, body) = ask(&server, "GET", "/api/sync/status", &watch, "").await;
    assert_eq!((status, caller(&body)), (StatusCode::OK, (false, "watch".to_string(), watch_id)), "a browser is never an agent, and keeps its own scope");
    let (_, body) = ask(&server, "GET", "/api/sync/status", &control, "").await;
    assert_eq!((body["caller"]["agent"].as_bool(), body["caller"]["scope"].as_str()), (Some(false), Some("control")));
    let (status, body) = ask(&server, "GET", "/api/sync/status", &owner, "").await;
    assert_eq!((status, body["caller"]["agent"].as_bool(), body["caller"]["scope"].as_str()), (StatusCode::OK, Some(false), Some("control")), "this machine's own session is a caller");
}

#[tokio::test]
async fn ten_wrong_pairing_codes_withdraw_the_pending_one() {
    let (_scratch, server) = world("pair-fail", true);
    let code = auth::issue_pairing_code(&server.config.state_dir).unwrap();
    let wrong = |_| ask(&server, "POST", "/api/sync/pair", "", r#"{"code":"not-a-code","name":"x"}"#);
    for (status, _) in futures::future::join_all((0..auth::PAIRING_ATTEMPTS).map(wrong)).await {
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    let (status, _) = ask(&server, "POST", "/api/sync/pair", "", &json!({ "code": code, "name": "x" }).to_string()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "the code was withdrawn by the tenth failure");
}

#[tokio::test]
async fn a_good_code_starts_the_count_of_wrong_ones_again() {
    let (_scratch, server) = world("pair-reset", true);
    let wrong = |_| ask(&server, "POST", "/api/sync/pair", "", r#"{"code":"not-a-code","name":"x"}"#);
    let first = auth::issue_pairing_code(&server.config.state_dir).unwrap();
    futures::future::join_all((0..auth::PAIRING_ATTEMPTS - 1).map(wrong)).await;
    let (status, body) = ask(&server, "POST", "/api/sync/pair", "", &json!({ "code": first, "name": "laptop" }).to_string()).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["device"].is_string() && body["secret"].is_string());
    let second = auth::issue_pairing_code(&server.config.state_dir).unwrap();
    futures::future::join_all((0..auth::PAIRING_ATTEMPTS - 1).map(wrong)).await;
    let (status, _) = ask(&server, "POST", "/api/sync/pair", "", &json!({ "code": second, "name": "desktop" }).to_string()).await;
    assert_eq!(status, StatusCode::OK, "nine wrong, a good one, nine wrong again is still under ten in a row");
}

#[tokio::test]
async fn a_manifest_with_wait_holds_until_the_time_is_up_when_nothing_changed() {
    let (_scratch, server) = world("manifest-wait", true);
    let owner = sign_in(&server).await;
    let started = Instant::now();
    let (status, body) = ask(&server, "GET", "/api/sync/manifest?since=0&wait=1", &owner, "").await;
    assert_eq!(status, StatusCode::OK);
    assert!(started.elapsed() >= Duration::from_millis(900), "held for the wait");
    assert_eq!(body["cursor"], 0);
    let started = Instant::now();
    ask(&server, "GET", "/api/sync/manifest?since=0", &owner, "").await;
    assert!(started.elapsed() < Duration::from_millis(500), "no wait asked, none taken");
}

#[tokio::test]
async fn an_upload_carries_its_thread_and_archive_flag_and_a_read_starts_where_it_is_asked() {
    let (_scratch, server) = world("upload", true);
    let owner = sign_in(&server).await;
    let (_, secret) = agent(&server, "laptop");
    let (status, first) =
        ask(&server, "PUT", &upload_uri(&format!("offset=0&prefix={}&thread={THREAD}&file={FILE}&archived=1", empty())), &secret, "{}\n").await;
    assert_eq!(status, StatusCode::OK, "{first}");
    let (_, listed) = ask(&server, "GET", "/api/sync/threads", &owner, "").await;
    assert_eq!(listed["threads"][0]["archived"], true, "?archived=1 publishes it archived: {listed}");
    // A later rollout of the same thread names it with ?thread=.
    let later = "01a0f1ae-2da4-7942-99ae-c3151dc13700";
    let later_file = FILE.replace(THREAD, later);
    let uri = format!("/api/sync/rollouts/{later}?offset=0&prefix={}&thread={THREAD}&file={later_file}", empty());
    let (status, body) = ask(&server, "PUT", &uri, &secret, "{}\n").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let (_, listed) = ask(&server, "GET", "/api/sync/threads", &owner, "").await;
    assert_eq!((listed["threads"].as_array().map(Vec::len), listed["threads"][0]["rollouts"].as_u64()), (Some(1), Some(2)), "{listed}");
    let hash = first["hash"].as_str().unwrap();
    let (status, second) = ask(&server, "PUT", &upload_uri(&format!("offset=3&prefix={hash}")), &secret, "[1]\n").await;
    assert_eq!(status, StatusCode::OK, "{second}");

    let (status, whole) = call(&server, send("GET", &format!("/api/sync/rollouts/{THREAD}"), &owner, "")).await;
    assert_eq!((status, whole.as_str()), (StatusCode::OK, "{}\n[1]\n"));
    let (status, rest) = call(&server, send("GET", &format!("/api/sync/rollouts/{THREAD}?offset=3"), &owner, "")).await;
    assert_eq!((status, rest.as_str()), (StatusCode::OK, "[1]\n"), "from ?offset=");
}

#[tokio::test]
async fn only_an_agent_hosts_and_a_host_with_nothing_to_take_waits_for_a_command() {
    let (_scratch, server) = world("host", true);
    let (_, secret) = agent(&server, "laptop");
    let (_, control) = browser(&server, "tablet");
    let owner = sign_in(&server).await;
    for credential in [&control, &owner] {
        let (status, body) = ask(&server, "POST", "/api/sync/host", credential, r#"{"host":"app"}"#).await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{body}");
    }
    let started = Instant::now();
    let (status, body) = ask(&server, "POST", "/api/sync/host?wait=1", &secret, r#"{"host":"app"}"#).await;
    assert_eq!((status, body), (StatusCode::OK, json!({ "commands": [] })));
    assert!(started.elapsed() >= Duration::from_millis(900), "held until the time was up");
}

#[tokio::test]
async fn a_followed_thread_waits_for_news_of_its_own_past_what_the_hub_had_already_seen() {
    let (_scratch, server) = world("follow", true);
    let owner = sign_in(&server).await;
    let (_, secret) = agent(&server, "laptop");
    let (status, _) = ask(&server, "PUT", &upload_uri(&format!("offset=0&prefix={}&thread={THREAD}&file={FILE}", empty())), &secret, "{}\n").await;
    assert_eq!(status, StatusCode::OK);
    let started = Instant::now();
    let (status, body) = ask(&server, "GET", &format!("/api/sync/live/{THREAD}?after=0&wait=1"), &owner, "").await;
    assert_eq!((status, body["events"].as_array().map(Vec::len)), (StatusCode::OK, Some(0)));
    assert!(started.elapsed() >= Duration::from_millis(900), "nothing yet: held");

    // Another thread's event moves the hub's number on, which says nothing about this thread.
    let elsewhere = json!({ "host": "app", "kind": "desktop", "events": [{ "thread": "other", "message": { "method": "item/completed", "params": {} } }] });
    ask(&server, "POST", "/api/sync/host", &secret, &elsewhere.to_string()).await;
    let started = Instant::now();
    let (_, body) = ask(&server, "GET", &format!("/api/sync/live/{THREAD}?after=0&wait=1"), &owner, "").await;
    assert_eq!(body["events"].as_array().map(Vec::len), Some(0));
    assert!(started.elapsed() >= Duration::from_millis(900), "what happened before the request is not news");
    let (status, _) = ask(&server, "GET", "/api/sync/live/00000000-0000-4000-8000-000000000000", &owner, "").await;
    assert_eq!(status, StatusCode::NOT_FOUND, "a thread the store does not hold is not followed");
}

#[tokio::test]
async fn a_command_goes_to_the_threads_owner_or_for_a_new_thread_to_the_sync_device_named() {
    let (_scratch, server) = world("commands", true);
    let (laptop, laptop_secret) = agent(&server, "laptop");
    let (desk, desk_secret) = agent(&server, "desk");
    let (tablet, control) = browser(&server, "tablet");
    let (status, _) = ask(&server, "PUT", &upload_uri(&format!("offset=0&prefix={}&thread={THREAD}&file={FILE}", empty())), &laptop_secret, "{}\n").await;
    assert_eq!(status, StatusCode::OK);
    for (secret, project) in [(&laptop_secret, "/lap/project"), (&desk_secret, "/home/user/app")] {
        let host = json!({ "host": "app", "kind": "desktop", "projects": [project] });
        let (status, _) = ask(&server, "POST", "/api/sync/host", secret, &host.to_string()).await;
        assert_eq!(status, StatusCode::OK);
    }

    let (status, body) = command(&server, &control, json!({ "kind": "send", "thread": THREAD, "text": "go on" })).await;
    assert_eq!((status, body["command"]["device"].as_str()), (StatusCode::OK, Some(laptop.as_str())), "to the thread's owner: {body}");
    let (status, body) = command(&server, &control, json!({ "kind": "start", "thread": THREAD, "device": desk, "cwd": "/home/user/app", "text": "new" })).await;
    assert_eq!((status, body["command"]["device"].as_str()), (StatusCode::OK, Some(desk.as_str())), "a start goes to the device named, not the thread's owner: {body}");
    let (status, body) = command(&server, &control, json!({ "kind": "send", "device": desk, "text": "go on" })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body["error"].as_str().unwrap().contains("name the thread"), "a message is not routed by device: {body}");
    let (status, _) = command(&server, &control, json!({ "kind": "start", "device": tablet, "cwd": "/home/user/app", "text": "new" })).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "a browser is not a device that hosts threads");
    let (status, _) = command(&server, &control, json!({ "kind": "start", "device": "no-such-device", "cwd": "/home/user/app", "text": "new" })).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "nor is a device nobody paired");
    let (status, _) = command(&server, &control, json!({ "kind": "send", "thread": "00000000-0000-4000-8000-000000000000", "text": "hi" })).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "an unknown thread has no owner");
}
