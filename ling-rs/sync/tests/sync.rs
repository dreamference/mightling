//! `ling sync` end to end (specs/DREAMFERENCE_MIGHTLING_SYNC.md §13): a real `ling web` hub on an
//! ephemeral loopback port, sync agents over HTTP, scratch homes, and a stand-in for `ling` that
//! records Codex's commands instead of running them. No real network, no real `ling`, no real
//! `~/.mightling`.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use ling_sync::agent::Agent;
use ling_sync::client::HubClient;
use ling_sync::config::DeviceFile;
use ling_sync::config::Settings;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use ling_web_server::sync_store;
use serde_json::Value;

const THREAD: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";
const EXEC: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1eeee";
const SECRET_PROJECT: &str = "01a0f1ae-2da4-7942-99ae-c3151dc15ec7";

struct Scratch {
    dir: PathBuf,
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("ls-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch { dir }
}

/// A stand-in for `ling`: records every command line in `calls`, and does what Codex's `delete` and
/// `archive` do to the files (remove the thread's rollouts, move them), so the agent sees the result.
fn fake_ling(dir: &Path, codex_home: &Path) -> PathBuf {
    let path = dir.join("ling");
    std::fs::write(
        &path,
        format!(
            "#!/bin/sh\necho \"$*\" >> '{calls}'\n\
             if [ \"$1\" = delete ]; then find '{home}' -name \"rollout-*$3*\" -delete; fi\n\
             if [ \"$1\" = archive ]; then mkdir -p '{home}/archived_sessions'; find '{home}/sessions' -name \"rollout-*$2*\" -exec mv {{}} '{home}/archived_sessions/' \\;; fi\n\
             exit 0\n",
            calls = dir.join("calls").display(),
            home = codex_home.display()
        ),
    )
    .unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

fn calls(dir: &Path) -> Vec<String> {
    std::fs::read_to_string(dir.join("calls")).unwrap_or_default().lines().map(str::to_string).collect()
}

struct Hub {
    server: Arc<Server>,
    base: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
}

impl Drop for Hub {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
    }
}

/// A `ling web` on loopback with the hub on, as `ling sync enable` leaves the node.
async fn hub(dir: &Path, enabled: bool) -> Hub {
    let codex_home = dir.join("node");
    if enabled {
        sync_store::set_enabled(&codex_home, true).unwrap();
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let state_dir = auth::state_dir(&codex_home);
    let config = Config {
        codex_home: codex_home.clone(),
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: dir.join("no-ling"), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        // Never the real ~/.config/dreamference/config.toml: a scratch one, absent unless a test writes it.
        user_config: Some(dir.join("user/.config/dreamference/config.toml")),
        forge_url: None,
        state_dir,
        port,
        lan_names: Vec::new(),
        public_names: Vec::new(),
    };
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    Hub { server, base: format!("http://127.0.0.1:{port}"), stop: Some(stop) }
}

/// A device paired with the hub: its own home with a device file, as `ling sync enable` leaves it.
fn device(dir: &Path, hub: &Hub, name: &str) -> (PathBuf, PathBuf) {
    let codex_home = dir.join(name);
    std::fs::create_dir_all(&codex_home).unwrap();
    let (id, secret) = auth::add_device_of(&hub.server.config.state_dir, name, auth::DeviceKind::Sync).unwrap();
    DeviceFile { device: id, name: name.to_string(), secret, port: 0, local: false }.save(&codex_home).unwrap();
    let ling = fake_ling(&dir.join(format!("{name}-bin")).tap_mkdir(), &codex_home);
    (codex_home, ling)
}

trait TapMkdir {
    fn tap_mkdir(self) -> Self;
}

impl TapMkdir for PathBuf {
    fn tap_mkdir(self) -> Self {
        std::fs::create_dir_all(&self).unwrap();
        self
    }
}

fn rollout_path(codex_home: &Path, id: &str) -> PathBuf {
    codex_home.join("sessions/2026/10/22").join(format!("rollout-2026-10-22T10-00-00-{id}.jsonl"))
}

fn meta_line(id: &str, source: &str, cwd: &str) -> String {
    format!("{{\"type\":\"session_meta\",\"payload\":{{\"id\":\"{id}\",\"cwd\":\"{cwd}\",\"source\":\"{source}\"}}}}\n")
}

fn said(who: &str, text: &str) -> String {
    format!("{{\"type\":\"event_msg\",\"payload\":{{\"type\":\"item_completed\",\"item\":{{\"type\":\"{who}\",\"content\":[{{\"type\":\"text\",\"text\":\"{text}\"}}]}}}}}}\n")
}

fn write(path: &Path, text: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

fn append(path: &Path, text: &str) {
    use std::io::Write;
    std::fs::OpenOptions::new().append(true).open(path).unwrap().write_all(text.as_bytes()).unwrap();
}

async fn get(hub: &Hub, path: &str, cookie: Option<&str>) -> (u16, Value) {
    let mut request = reqwest::Client::new().get(format!("{}{path}", hub.base));
    if let Some(cookie) = cookie {
        request = request.header("Cookie", cookie);
    }
    let response = request.send().await.unwrap();
    let status = response.status().as_u16();
    (status, response.json().await.unwrap_or(Value::Null))
}

async fn post(hub: &Hub, path: &str, cookie: &str) -> u16 {
    reqwest::Client::new()
        .post(format!("{}{path}", hub.base))
        .header("Cookie", cookie)
        .header("Origin", &hub.base)
        .json(&serde_json::json!({}))
        .send()
        .await
        .unwrap()
        .status()
        .as_u16()
}

/// A paired phone's cookie, with its scope.
fn phone(hub: &Hub, scope: auth::Scope) -> String {
    let state = &hub.server.config.state_dir;
    let (id, token) = auth::add_device(state, "phone").unwrap();
    auth::set_scope(state, &id, scope).unwrap();
    format!("{}={token}", auth::DEVICE_COOKIE)
}

fn agent(codex_home: &Path, ling: &Path, settings: Settings) -> Agent {
    Agent::load(codex_home, ling, settings).expect("a paired device")
}

fn client(hub: &Hub, codex_home: &Path) -> HubClient {
    HubClient::new(&hub.base, &DeviceFile::load(codex_home).unwrap().secret).unwrap()
}

fn files_under(dir: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
            if entry.file_type().unwrap().is_dir() {
                stack.push(entry.path());
            } else {
                found.push(entry.path());
            }
        }
    }
    found.sort();
    found
}

#[tokio::test]
async fn a_thread_started_on_the_laptop_is_on_the_hub_within_seconds_and_grows_there() {
    let scratch = scratch("publish");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    let path = rollout_path(&laptop, THREAD);
    write(&path, &format!("{}{}", meta_line(THREAD, "vscode", "/home/u/app"), said("UserMessage", "Fix the login redirect")));
    let mut agent = agent(&laptop, &ling, Settings::default());
    let hub_client = client(&hub, &laptop);
    let started = Instant::now();
    let report = agent.cycle(&hub_client).await;
    assert!(report.errors.is_empty(), "{:?}", report.errors);
    let browser = phone(&hub, auth::Scope::Control);
    let (status, listed) = get(&hub, "/api/sync/threads", Some(&browser)).await;
    assert!(started.elapsed() < Duration::from_secs(5), "the acceptance bar is five seconds");
    assert_eq!(status, 200);
    let thread = &listed["threads"][0];
    assert_eq!((thread["thread"].as_str(), thread["title"].as_str(), thread["owner_name"].as_str()), (Some(THREAD), Some("Fix the login redirect"), Some("laptop")));
    // A partial line waits for its end; whole lines go up on the next cycle.
    append(&path, &said("AgentMessage", "Done: the redirect keeps the query."));
    append(&path, "{\"type\":\"event_msg\",\"pay");
    agent.cycle(&hub_client).await;
    let (_, shown) = get(&hub, &format!("/api/sync/threads/{THREAD}"), Some(&browser)).await;
    let texts: Vec<&str> = shown["items"].as_array().unwrap().iter().map(|item| item["text"].as_str().unwrap()).collect();
    assert_eq!(texts, ["Fix the login redirect", "Done: the redirect keeps the query."]);
    let on_disk = std::fs::read(&path).unwrap();
    let held = hub.server.sync_store().unwrap().record(THREAD).unwrap();
    assert_eq!(held.length as usize, on_disk.len() - "{\"type\":\"event_msg\",\"pay".len());
    assert_eq!(held.hash, sync_store::prefix_hash(&on_disk[..held.length as usize]));
    // Nothing of the hub's was written into the laptop's store: only its own sync folder.
    let written: Vec<PathBuf> = files_under(&laptop).into_iter().filter(|file| !file.starts_with(laptop.join("sessions"))).collect();
    assert!(written.iter().all(|file| file.starts_with(laptop.join("sync"))), "{written:?}");
    assert!(calls(&scratch.dir.join("laptop-bin")).is_empty());
}

#[tokio::test]
async fn exec_sessions_and_excluded_folders_never_reach_the_hub() {
    let scratch = scratch("held");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "cli", "/home/u/app"), said("UserMessage", "hello")));
    write(&rollout_path(&laptop, EXEC), &format!("{}{}", meta_line(EXEC, "exec", "/home/u/app"), said("UserMessage", "a script")));
    write(&rollout_path(&laptop, SECRET_PROJECT), &format!("{}{}", meta_line(SECRET_PROJECT, "vscode", "/home/u/secret/x"), said("UserMessage", "secret")));
    let settings = Settings { exclude: vec![PathBuf::from("/home/u/secret")], ..Settings::default() };
    let mut agent = agent(&laptop, &ling, settings);
    agent.cycle(&client(&hub, &laptop)).await;
    let store = hub.server.sync_store().unwrap();
    let ids: Vec<String> = store.threads().iter().map(|view| view.current.thread.clone()).collect();
    assert_eq!(ids, [THREAD]);
    // On the wire too: the hub never received a byte of them.
    for held in [EXEC, SECRET_PROJECT] {
        assert!(store.record(held).is_none());
        assert!(!sync_store::hub_dir(&hub.server.config.codex_home).join("rollouts").join(format!("{held}.jsonl")).exists());
    }
    // `[sync] exec = true` publishes `ling exec` sessions too.
    agent.settings = Settings { exec: true, ..agent.settings.clone() };
    agent.cycle(&client(&hub, &laptop)).await;
    assert!(store.record(EXEC).is_some());
    // A folder excluded after its thread was published: taken off the hub, kept on the laptop.
    agent.settings.exclude.push(PathBuf::from("/home/u/app"));
    agent.cycle(&client(&hub, &laptop)).await;
    assert!(store.threads().is_empty());
    assert!(rollout_path(&laptop, THREAD).exists());
    assert!(calls(&scratch.dir.join("laptop-bin")).is_empty(), "a withdrawal deletes nothing on the device");
}

#[tokio::test]
async fn a_deletion_anywhere_is_a_deletion_everywhere() {
    let scratch = scratch("delete");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    let other = "01a0f1ae-2da4-7942-99ae-c3151dc10002";
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "vscode", "/w"), said("UserMessage", "one")));
    write(&rollout_path(&laptop, other), &format!("{}{}", meta_line(other, "cli", "/w"), said("UserMessage", "two")));
    let mut agent = agent(&laptop, &ling, Settings::default());
    let hub_client = client(&hub, &laptop);
    agent.cycle(&hub_client).await;
    let store = hub.server.sync_store().unwrap();
    assert_eq!(store.threads().len(), 2);
    // From the phone: the hub's copy goes now, the laptop's on the agent's next cycle, by Codex's
    // own `delete`.
    let browser = phone(&hub, auth::Scope::Control);
    assert_eq!(post(&hub, &format!("/api/sync/threads/{THREAD}/delete"), &browser).await, 200);
    assert!(store.record(THREAD).is_none());
    let report = agent.cycle(&hub_client).await;
    assert_eq!(calls(&scratch.dir.join("laptop-bin")), [format!("delete --force {THREAD}")]);
    assert!(report.applied.iter().any(|line| line.contains(THREAD)));
    assert!(!rollout_path(&laptop, THREAD).exists());
    // On the laptop (`ling delete` there): gone from the hub after two scans without it.
    std::fs::remove_file(rollout_path(&laptop, other)).unwrap();
    agent.cycle(&hub_client).await;
    assert!(store.record(other).is_some(), "one scan without it is not yet a deletion");
    let report = agent.cycle(&hub_client).await;
    assert_eq!(report.deleted_on_hub, [other]);
    assert!(store.threads().is_empty());
    assert_eq!(store.manifest(0).tombstones.len(), 2);
    // The deleted thread is never published again.
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "vscode", "/w"), said("UserMessage", "one")));
    agent.cycle(&hub_client).await;
    assert!(store.record(THREAD).is_none());
}

#[tokio::test]
async fn a_watch_device_may_read_but_not_delete_and_archiving_reaches_the_owner() {
    let scratch = scratch("scopes");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "vscode", "/w"), said("UserMessage", "one")));
    let mut agent = agent(&laptop, &ling, Settings::default());
    let hub_client = client(&hub, &laptop);
    agent.cycle(&hub_client).await;
    let watcher = phone(&hub, auth::Scope::Watch);
    assert_eq!(get(&hub, &format!("/api/sync/threads/{THREAD}"), Some(&watcher)).await.0, 200);
    assert_eq!(post(&hub, &format!("/api/sync/threads/{THREAD}/delete"), &watcher).await, 403);
    // An agent narrowed to watching may publish its own threads, but not delete another's.
    let (desktop, _) = device(&scratch.dir, &hub, "desktop");
    let desktop_id = DeviceFile::load(&desktop).unwrap().device;
    auth::set_scope(&hub.server.config.state_dir, &desktop_id, auth::Scope::Watch).unwrap();
    assert!(client(&hub, &desktop).delete(THREAD).await.unwrap_err().contains("403"));
    assert!(hub.server.sync_store().unwrap().record(THREAD).is_some());
    let controller = phone(&hub, auth::Scope::Control);
    let archive = reqwest::Client::new()
        .post(format!("{}/api/sync/threads/{THREAD}/meta", hub.base))
        .header("Cookie", &controller)
        .header("Origin", &hub.base)
        .json(&serde_json::json!({ "archived": true }))
        .send()
        .await
        .unwrap();
    assert_eq!(archive.status().as_u16(), 200);
    agent.cycle(&hub_client).await;
    assert_eq!(calls(&scratch.dir.join("laptop-bin")), [format!("archive {THREAD}")]);
    // Applied once, and it holds: the next cycle sees the rollout archived, as the hub says.
    agent.cycle(&hub_client).await;
    assert_eq!(calls(&scratch.dir.join("laptop-bin")).len(), 1);
    assert!(hub.server.sync_store().unwrap().threads()[0].current.archived);
}

#[tokio::test]
async fn names_and_archive_state_follow_the_owner() {
    let scratch = scratch("meta");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "vscode", "/w"), said("UserMessage", "one")));
    let mut agent = agent(&laptop, &ling, Settings::default());
    let hub_client = client(&hub, &laptop);
    agent.cycle(&hub_client).await;
    write(&laptop.join("session_index.jsonl"), &format!("{{\"id\":\"{THREAD}\",\"thread_name\":\"Login fix\",\"updated_at\":\"t\"}}\n"));
    // Archived on the laptop: Codex moves the rollout into `archived_sessions/`.
    let archived = laptop.join("archived_sessions").join(rollout_path(&laptop, THREAD).file_name().unwrap());
    std::fs::create_dir_all(archived.parent().unwrap()).unwrap();
    std::fs::rename(rollout_path(&laptop, THREAD), &archived).unwrap();
    agent.cycle(&hub_client).await;
    let store = hub.server.sync_store().unwrap();
    let view = &store.threads()[0];
    assert_eq!((view.title(), view.current.archived), ("Login fix".to_string(), true));
    assert!(calls(&scratch.dir.join("laptop-bin")).is_empty(), "its own change is not applied back");
}

#[tokio::test]
async fn a_second_device_publishing_the_same_thread_gets_a_continued_one() {
    let scratch = scratch("split");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, laptop_ling) = device(&scratch.dir, &hub, "laptop");
    let (desktop, desktop_ling) = device(&scratch.dir, &hub, "desktop");
    let opening = format!("{}{}", meta_line(THREAD, "cli", "/w"), said("UserMessage", "Plan the release"));
    write(&rollout_path(&laptop, THREAD), &opening);
    write(&rollout_path(&desktop, THREAD), &format!("{opening}{}", said("UserMessage", "and on the desktop")));
    agent(&laptop, &laptop_ling, Settings::default()).cycle(&client(&hub, &laptop)).await;
    let mut second = agent(&desktop, &desktop_ling, Settings::default());
    second.cycle(&client(&hub, &desktop)).await;
    second.cycle(&client(&hub, &desktop)).await;
    let store = hub.server.sync_store().unwrap();
    let threads = store.threads();
    assert_eq!(threads.len(), 2);
    let continued = threads.iter().find(|view| view.current.owner_name == "desktop").unwrap();
    assert_eq!(continued.current.fork_of.as_deref(), Some(THREAD));
    assert_eq!(continued.title(), "Plan the release (continued on desktop)");
    assert_eq!(store.record(THREAD).unwrap().length as usize, opening.len(), "the two transcripts are never merged");
}

#[tokio::test]
async fn an_agent_that_lost_its_state_resumes_where_the_hub_is() {
    let scratch = scratch("resume");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    let path = rollout_path(&laptop, THREAD);
    write(&path, &format!("{}{}", meta_line(THREAD, "cli", "/w"), said("UserMessage", "one")));
    agent(&laptop, &ling, Settings::default()).cycle(&client(&hub, &laptop)).await;
    std::fs::remove_file(laptop.join("sync/agent.json")).unwrap();
    append(&path, &said("AgentMessage", "two"));
    let mut fresh = agent(&laptop, &ling, Settings::default());
    for _ in 0..3 {
        let report = fresh.cycle(&client(&hub, &laptop)).await;
        assert!(report.errors.is_empty(), "{:?}", report.errors);
    }
    let store = hub.server.sync_store().unwrap();
    assert_eq!(store.record(THREAD).unwrap().length, std::fs::metadata(&path).unwrap().len());
    assert_eq!(store.threads().len(), 1);
}

#[tokio::test]
async fn compressed_rollouts_publish_their_bytes() {
    let scratch = scratch("zst");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, ling) = device(&scratch.dir, &hub, "laptop");
    let text = format!("{}{}", meta_line(THREAD, "cli", "/w"), said("UserMessage", "old thread"));
    let path = rollout_path(&laptop, THREAD);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path.with_extension("jsonl.zst"), zstd::encode_all(text.as_bytes(), 3).unwrap()).unwrap();
    agent(&laptop, &ling, Settings::default()).cycle(&client(&hub, &laptop)).await;
    let record = hub.server.sync_store().unwrap().record(THREAD).unwrap();
    assert_eq!(record.hash, sync_store::prefix_hash(text.as_bytes()));
}

#[tokio::test]
async fn with_the_hub_off_no_sync_route_answers_and_nothing_is_written() {
    let scratch = scratch("off");
    let hub = hub(&scratch.dir, false).await;
    let browser = phone(&hub, auth::Scope::Control);
    for path in ["/api/sync/status", "/api/sync/threads", "/api/sync/manifest", &format!("/api/sync/threads/{THREAD}")] {
        assert_eq!(get(&hub, path, Some(&browser)).await.0, 404, "{path}");
    }
    let pair = reqwest::Client::new()
        .post(format!("{}/api/sync/pair", hub.base))
        .header("Origin", &hub.base)
        .json(&serde_json::json!({ "code": "12345678", "name": "x" }))
        .send()
        .await
        .unwrap();
    assert_eq!(pair.status().as_u16(), 404);
    assert!(!sync_store::hub_dir(&hub.server.config.codex_home).exists());
    // And a device that never paired has no agent.
    let home = scratch.dir.join("never");
    std::fs::create_dir_all(&home).unwrap();
    assert!(Agent::load(&home, Path::new("/bin/false"), Settings::default()).is_none());
    assert!(!home.join("sync").exists());
}

#[tokio::test]
async fn an_agent_pairs_with_a_code_and_its_secret_opens_only_the_sync_routes() {
    let scratch = scratch("pair");
    let hub = hub(&scratch.dir, true).await;
    assert!(ling_sync::client::pair(&hub.base, "00000000", "laptop").await.unwrap_err().contains("wrong or has expired"));
    let code = auth::issue_pairing_code(&hub.server.config.state_dir).unwrap();
    let (device, secret) = ling_sync::client::pair(&hub.base, &code, "laptop").await.unwrap();
    assert!(ling_sync::client::pair(&hub.base, &code, "again").await.is_err(), "a code works once");
    let paired = auth::load_devices(&hub.server.config.state_dir).into_iter().find(|d| d.id == device).unwrap();
    assert_eq!((paired.kind, paired.scope), (auth::DeviceKind::Sync, auth::Scope::Control));
    let hub_client = HubClient::new(&hub.base, &secret).unwrap();
    assert_eq!(hub_client.status().await.unwrap()["caller"]["device"], device.as_str());
    // As a bearer credential anywhere else, or as a cookie, it opens nothing.
    let http = reqwest::Client::new();
    for path in ["/", "/api/night/status", "/devices", "/healthz"] {
        let status = http.get(format!("{}{path}", hub.base)).bearer_auth(&secret).send().await.unwrap().status();
        assert_eq!(status.as_u16(), 401, "{path}");
    }
    let as_cookie = format!("{}={secret}", auth::DEVICE_COOKIE);
    assert_eq!(get(&hub, "/api/sync/threads", Some(&as_cookie)).await.0, 401);
    // `ling sync forget` ends it.
    hub_client.forget().await.unwrap();
    assert!(hub_client.status().await.is_err());
}

#[tokio::test]
async fn an_archive_codex_did_not_carry_out_is_not_published_back_over_the_hubs() {
    let scratch = scratch("stuck");
    let hub = hub(&scratch.dir, true).await;
    let (laptop, _) = device(&scratch.dir, &hub, "laptop");
    // A `ling` whose `archive` succeeds and moves nothing (a loaded thread, say).
    let ling = scratch.dir.join("still-ling");
    std::fs::write(&ling, "#!/bin/sh\nexit 0\n").unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&ling, std::fs::Permissions::from_mode(0o755)).unwrap();
    write(&rollout_path(&laptop, THREAD), &format!("{}{}", meta_line(THREAD, "vscode", "/w"), said("UserMessage", "one")));
    let mut agent = agent(&laptop, &ling, Settings::default());
    let hub_client = client(&hub, &laptop);
    agent.cycle(&hub_client).await;
    let controller = phone(&hub, auth::Scope::Control);
    let archive = reqwest::Client::new()
        .post(format!("{}/api/sync/threads/{THREAD}/meta", hub.base))
        .header("Cookie", &controller)
        .header("Origin", &hub.base)
        .json(&serde_json::json!({ "archived": true }))
        .send()
        .await
        .unwrap();
    assert_eq!(archive.status().as_u16(), 200);
    for _ in 0..3 {
        agent.cycle(&hub_client).await;
    }
    assert!(hub.server.sync_store().unwrap().threads()[0].current.archived, "the phone's choice stands");
}
