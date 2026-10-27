//! The agent as a host (specs/DREAMFERENCE_MIGHTLING_SYNC.md §18), end to end: a real `ling web`
//! hub on loopback, a stand-in app-server on a scratch Unix socket that this test answers itself,
//! and a phone that follows a turn and answers it. No real `ling`, no real network.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use ling_sync::client::HubClient;
use ling_sync::config::Settings;
use ling_sync::live::Host;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use ling_web_server::sync_store;
use ling_web_server::sync_store::Caller;
use ling_web_server::sync_store::Upload;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

const T: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";
const HIDDEN: &str = "01a0f1ae-2da4-7942-99ae-c3151dc15ec7";
const NEW: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1a0e0";

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("lh-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch(dir)
}

/// A stand-in app-server: answers what the host asks, records every message it gets, and sends
/// what the test hands it.
async fn fake_app_server(socket: &Path) -> (mpsc::UnboundedReceiver<Value>, mpsc::UnboundedSender<Value>) {
    let listener = tokio::net::UnixListener::bind(socket).unwrap();
    let (heard_tx, heard) = mpsc::unbounded_channel::<Value>();
    let (say, mut to_say) = mpsc::unbounded_channel::<Value>();
    tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        loop {
            tokio::select! {
                frame = socket.next() => {
                    let Some(Ok(Message::Text(text))) = frame else { break };
                    let value: Value = serde_json::from_str(&text).unwrap();
                    let _ = heard_tx.send(value.clone());
                    let (Some(id), Some(method)) = (value.get("id").cloned(), value.get("method").and_then(Value::as_str)) else { continue };
                    let result = match method {
                        "thread/loaded/list" => json!({ "data": [T, HIDDEN], "nextCursor": null }),
                        "thread/resume" => {
                            let thread = value.pointer("/params/threadId").and_then(Value::as_str).unwrap_or_default();
                            let cwd = if thread == HIDDEN { "/home/user/secret/x" } else { "/home/user/app" };
                            json!({ "thread": { "id": thread, "cwd": cwd } })
                        }
                        "thread/start" => json!({ "thread": { "id": NEW, "cwd": value.pointer("/params/cwd").cloned().unwrap_or(Value::Null) } }),
                        _ => json!({}),
                    };
                    let answer = json!({ "id": id, "result": result }).to_string();
                    if socket.send(Message::Text(answer.into())).await.is_err() {
                        break;
                    }
                }
                said = to_say.recv() => {
                    let Some(said) = said else { break };
                    if socket.send(Message::Text(said.to_string().into())).await.is_err() {
                        break;
                    }
                }
            }
        }
    });
    (heard, say)
}

async fn hub(dir: &Path) -> (Arc<Server>, String) {
    let codex_home = dir.join("node");
    sync_store::set_enabled(&codex_home, true).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let state_dir = auth::state_dir(&codex_home);
    let config = Config {
        codex_home,
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: dir.join("no-ling"), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        state_dir,
        port,
        lan_names: Vec::new(),
        public_names: Vec::new(),
        user_config: None,
        forge_url: None,
    };
    let server = Server::new(config).unwrap();
    tokio::spawn(server::serve(server.clone(), vec![listener], std::future::pending::<()>()));
    (server, format!("http://127.0.0.1:{port}"))
}

/// Waits until the stand-in server has heard a message `matches` accepts.
async fn heard(heard: &mut mpsc::UnboundedReceiver<Value>, matches: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let value = heard.recv().await.expect("the server is still there");
            if matches(&value) {
                return value;
            }
        }
    })
    .await
    .expect("heard in time")
}

async fn follow(base: &str, cookie: &str, after: u64) -> Value {
    reqwest::Client::new().get(format!("{base}/api/sync/live/{T}?after={after}&wait=5")).header("Cookie", cookie).send().await.unwrap().json().await.unwrap()
}

async fn command(base: &str, cookie: &str, body: Value) -> (u16, Value) {
    let response = reqwest::Client::new().post(format!("{base}/api/sync/commands")).header("Cookie", cookie).header("Origin", base).json(&body).send().await.unwrap();
    (response.status().as_u16(), response.json().await.unwrap_or(Value::Null))
}

async fn state_of(base: &str, cookie: &str, id: u64) -> Value {
    for _ in 0..100 {
        let answer: Value = reqwest::Client::new().get(format!("{base}/api/sync/commands/{id}")).header("Cookie", cookie).send().await.unwrap().json().await.unwrap();
        let state = answer["command"]["state"].as_str().unwrap_or_default().to_string();
        if matches!(state.as_str(), "done" | "failed" | "expired") {
            return answer["command"].clone();
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("command {id} never finished");
}

#[tokio::test]
async fn a_phone_follows_a_turn_on_the_laptop_answers_its_approval_steers_and_stops_it() {
    let scratch = scratch("follow");
    let (server, base) = hub(&scratch.0).await;
    // The laptop's agent, paired, with its thread published.
    let state = &server.config.state_dir;
    let (laptop, secret) = auth::add_device_of(state, "laptop", auth::DeviceKind::Sync).unwrap();
    let owner = Caller { device: laptop.clone(), name: "laptop".to_string(), agent: true, scope: auth::Scope::Control };
    let file = format!("rollout-2026-10-26T10-00-00-{T}.jsonl");
    server.sync_store().unwrap().append(T, &owner, &Upload::first(&file), b"{\"type\":\"session_meta\",\"payload\":{\"cwd\":\"/home/user/app\"}}\n").unwrap();
    let (phone_id, phone_token) = auth::add_device(state, "phone").unwrap();
    let phone = format!("{}={phone_token}", auth::DEVICE_COOKIE);
    let _ = phone_id;
    // A server on the laptop that the agent attaches to (the TUI's control socket, say).
    let laptop_home = scratch.0.join("laptop");
    let socket = laptop_home.join("app-server-control").join("app-server-control.sock");
    std::fs::create_dir_all(socket.parent().unwrap()).unwrap();
    let (mut heard_rx, say) = fake_app_server(&socket).await;
    let (host, mut incoming) = Host::new(&laptop_home);
    let settings = Settings { exclude: vec![PathBuf::from("/home/user/secret")], ..Settings::default() };
    host.look(std::slice::from_ref(&socket), &settings).await;
    assert!(host.attached());
    heard(&mut heard_rx, |v| v["method"] == "initialize").await;
    let hub_client = Arc::new(HubClient::new(&base, &secret).unwrap());
    {
        let (host, hub_client) = (host.clone(), hub_client.clone());
        tokio::spawn(async move {
            loop {
                let _ = host.publish(&hub_client, &mut incoming).await;
            }
        });
    }
    {
        let (host, hub_client) = (host.clone(), hub_client.clone());
        tokio::spawn(async move {
            loop {
                if host.wait_for_commands(&hub_client, 2).await.is_err() {
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
            }
        });
    }
    // A turn starts on the laptop and asks for an approval; the phone sees both.
    say.send(json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "turn-1" } } })).unwrap();
    say.send(json!({ "method": "item/agentMessage/delta", "params": { "threadId": T, "itemId": "m1", "delta": "Cleaning up" } })).unwrap();
    say.send(json!({ "id": 41, "method": "item/commandExecution/requestApproval", "params": { "threadId": T, "command": "rm -rf build" } })).unwrap();
    // The excluded thread's events never reach the hub.
    say.send(json!({ "method": "turn/started", "params": { "threadId": HIDDEN, "turn": { "id": "turn-x" } } })).unwrap();
    let mut seen = json!({});
    for _ in 0..50 {
        seen = follow(&base, &phone, 0).await;
        if seen["pending"].as_array().is_some_and(|p| !p.is_empty()) {
            break;
        }
    }
    assert_eq!((seen["running"].as_bool(), seen["turn"].as_str(), seen["online"].as_bool()), (Some(true), Some("turn-1"), Some(true)));
    assert_eq!(seen["pending"][0]["id"], 41);
    assert!(seen["events"].as_array().unwrap().iter().any(|e| e["message"]["params"]["delta"] == "Cleaning up"));
    let hidden: Value = reqwest::Client::new().get(format!("{base}/api/sync/live/{HIDDEN}")).header("Cookie", &phone).send().await.unwrap().json().await.unwrap_or(Value::Null);
    assert!(hidden.get("events").is_none(), "a thread kept on its device is followed by nobody: {hidden}");
    // The phone approves: the laptop's server gets the answer, through the policy.
    let (status, sent) = command(&base, &phone, json!({ "kind": "approve", "thread": T, "request": 41, "decision": "accept" })).await;
    assert_eq!(status, 200, "{sent}");
    let answer = heard(&mut heard_rx, |v| v.get("id") == Some(&json!(41)) && v.get("result").is_some()).await;
    assert_eq!(answer["result"]["decision"], "accept");
    assert_eq!(state_of(&base, &phone, sent["command"]["id"].as_u64().unwrap()).await["state"], "done");
    say.send(json!({ "method": "serverRequest/resolved", "params": { "threadId": T, "requestId": 41 } })).unwrap();
    // A message while the turn runs steers it; Stop interrupts it.
    let (_, sent) = command(&base, &phone, json!({ "kind": "send", "thread": T, "text": "keep the logs" })).await;
    let steer = heard(&mut heard_rx, |v| v["method"] == "turn/steer").await;
    assert_eq!((steer["params"]["expectedTurnId"].as_str(), steer["params"]["input"][0]["text"].as_str()), (Some("turn-1"), Some("keep the logs")));
    assert_eq!(state_of(&base, &phone, sent["command"]["id"].as_u64().unwrap()).await["state"], "done");
    let (_, sent) = command(&base, &phone, json!({ "kind": "stop", "thread": T })).await;
    let stop = heard(&mut heard_rx, |v| v["method"] == "turn/interrupt").await;
    assert_eq!(stop["params"]["turnId"], "turn-1");
    assert_eq!(state_of(&base, &phone, sent["command"]["id"].as_u64().unwrap()).await["state"], "done");
    // When the turn ends, the next message starts a turn, with the model chosen.
    say.send(json!({ "method": "turn/completed", "params": { "threadId": T, "turn": { "id": "turn-1" } } })).unwrap();
    for _ in 0..50 {
        if follow(&base, &phone, 0).await["running"] == false {
            break;
        }
    }
    let (_, sent) = command(&base, &phone, json!({ "kind": "send", "thread": T, "text": "now the docs", "model": "fake-model" })).await;
    let start = heard(&mut heard_rx, |v| v["method"] == "turn/start").await;
    assert_eq!((start["params"]["model"].as_str(), start["params"]["threadId"].as_str()), (Some("fake-model"), Some(T)));
    assert_eq!(state_of(&base, &phone, sent["command"]["id"].as_u64().unwrap()).await["state"], "done");
    // A new thread in one of the laptop's folders.
    let (status, sent) = command(&base, &phone, json!({ "kind": "start", "device": laptop, "cwd": "/home/user/app", "text": "a new task" })).await;
    assert_eq!(status, 200, "{sent}");
    let opened = heard(&mut heard_rx, |v| v["method"] == "thread/start").await;
    assert_eq!(opened["params"]["cwd"], "/home/user/app");
    let done = state_of(&base, &phone, sent["command"]["id"].as_u64().unwrap()).await;
    assert_eq!((done["state"].as_str(), done["thread"].as_str()), (Some("done"), Some(NEW)));
    // A folder the laptop did not offer is refused by the hub.
    let (status, _) = command(&base, &phone, json!({ "kind": "start", "device": laptop, "cwd": "/home/user/secret/x", "text": "no" })).await;
    assert_eq!(status, 400);
}
