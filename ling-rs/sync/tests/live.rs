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
use ling_sync::live::Connection;
use ling_sync::live::Host;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::policy::Policy;
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
/// A throwaway thread, loaded: kept on this device like an excluded one.
const EPHEMERAL: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1e9e9";
/// Threads not loaded, which the server describes on `thread/read`: one in an excluded folder, one not.
const UNLOADED_HIDDEN: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1b0b0";
const UNLOADED: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1c0c0";
/// Not loaded either: a subagent's thread, one the server answers about with another thread, an
/// Ask thread, and one with no folder.
const SUBAGENT: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1d1d1";
const MISLABELED: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1d2d2";
const ASKED: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1d3d3";
const NO_FOLDER: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1d4d4";

/// What the stand-in server says about a thread, as `thread/resume` and `thread/read` answer it.
fn described(thread: &str) -> Option<Value> {
    match thread {
        T | NEW | UNLOADED => Some(json!({ "id": thread, "cwd": "/home/user/app" })),
        HIDDEN => Some(json!({ "id": thread, "cwd": "/home/user/secret/x" })),
        UNLOADED_HIDDEN => Some(json!({ "id": thread, "cwd": "/home/user/secret/y" })),
        EPHEMERAL => Some(json!({ "id": thread, "cwd": "/home/user/app", "ephemeral": true })),
        SUBAGENT => Some(json!({ "id": thread, "cwd": "/home/user/app", "parentThreadId": T })),
        MISLABELED => Some(json!({ "id": T, "cwd": "/home/user/app" })),
        ASKED => Some(json!({ "id": thread, "cwd": "/home/user/ask/q" })),
        NO_FOLDER => Some(json!({ "id": thread })),
        _ => None,
    }
}

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
                    let thread = value.pointer("/params/threadId").and_then(Value::as_str).unwrap_or_default();
                    let result = match method {
                        "thread/loaded/list" => Some(json!({ "data": [T, HIDDEN, EPHEMERAL], "nextCursor": null })),
                        "thread/resume" | "thread/read" => described(thread).map(|thread| json!({ "thread": thread })),
                        "thread/start" => Some(json!({ "thread": { "id": NEW, "cwd": value.pointer("/params/cwd").cloned().unwrap_or(Value::Null) } })),
                        _ => Some(json!({})),
                    };
                    let answer = match result {
                        Some(result) => json!({ "id": id, "result": result }),
                        None => json!({ "id": id, "error": { "code": -32600, "message": "no such thread" } }),
                    }
                    .to_string();
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

/// What the stand-in server has heard since the last look, without waiting.
fn heard_so_far(heard: &mut mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
    std::iter::from_fn(|| heard.try_recv().ok()).collect()
}

#[tokio::test]
async fn the_host_refuses_what_concerns_a_thread_kept_on_this_device_and_answers_an_approval_in_its_own_thread() {
    let scratch = scratch("kept");
    let socket = scratch.0.join("s.sock");
    let (mut heard_rx, say) = fake_app_server(&socket).await;
    let (events, mut asked) = mpsc::unbounded_channel();
    let connection = Connection::open(&socket, events).await.unwrap();
    let settings = Settings { exclude: vec![PathBuf::from("/home/user/secret")], ..Settings::default() };
    // Neither the excluded thread nor the throwaway is attached.
    assert_eq!(connection.attach_loaded(&settings).await.into_iter().collect::<Vec<_>>(), vec![T.to_string()]);
    let (policy, home) = (Policy::embedded(), scratch.0.join("home"));
    let carry = |command: Value| {
        let (connection, policy, home) = (connection.clone(), &policy, &home);
        async move { connection.carry_out(policy, home, &command).await }
    };
    heard_so_far(&mut heard_rx);
    let kept: Result<Option<String>, String> = Err("this thread is kept on this device".to_string());
    // Send and stop leave a kept thread alone, loaded or not, and one the server does not know.
    say.send(json!({ "method": "turn/started", "params": { "threadId": HIDDEN, "turn": { "id": "turn-h" } } })).unwrap();
    say.send(json!({ "method": "turn/started", "params": { "threadId": EPHEMERAL, "turn": { "id": "turn-e" } } })).unwrap();
    for thread in [HIDDEN, EPHEMERAL, UNLOADED_HIDDEN, "01a0f1ae-2da4-7942-99ae-c3151dc1ffff"] {
        assert_eq!(carry(json!({ "kind": "send", "thread": thread, "text": "hi" })).await, kept, "send to {thread}");
        assert_eq!(carry(json!({ "kind": "stop", "thread": thread })).await, kept, "stop on {thread}");
    }
    let methods: Vec<Value> = heard_so_far(&mut heard_rx).iter().map(|v| v["method"].clone()).collect();
    assert!(methods.iter().all(|m| m == "thread/read"), "only reads, never a resume, a turn or an interrupt: {methods:?}");
    // A published thread not loaded yet is resumed, then gets its turn.
    assert_eq!(carry(json!({ "kind": "send", "thread": UNLOADED, "text": "hi" })).await, Ok(Some(UNLOADED.to_string())));
    let methods: Vec<Value> = heard_so_far(&mut heard_rx).iter().map(|v| v["method"].clone()).collect();
    assert_eq!(methods, vec![json!("thread/read"), json!("thread/resume"), json!("turn/start")]);
    // Approvals: one not waiting, ones asked in kept threads, and one whose thread the hub names wrongly.
    assert_eq!(carry(json!({ "kind": "approve", "thread": T, "request": 50, "decision": "accept" })).await, Err("that approval is not waiting any more".to_string()));
    for (id, thread) in [(51, HIDDEN), (52, EPHEMERAL), (53, T)] {
        say.send(json!({ "id": id, "method": "item/commandExecution/requestApproval", "params": { "threadId": thread, "command": "rm -rf build" } })).unwrap();
    }
    let mut requests = 0;
    while requests < 3 {
        let (_, message) = tokio::time::timeout(Duration::from_secs(10), asked.recv()).await.expect("asked in time").unwrap();
        requests += usize::from(message.get("id").is_some());
    }
    assert_eq!(carry(json!({ "kind": "approve", "thread": T, "request": 51, "decision": "accept" })).await, kept);
    assert_eq!(carry(json!({ "kind": "approve", "thread": T, "request": 52, "decision": "accept" })).await, kept);
    assert_eq!(carry(json!({ "kind": "approve", "thread": HIDDEN, "request": 53, "decision": "accept" })).await, Ok(Some(T.to_string())));
    // An answer is sent without waiting for one back: everything heard up to it is in order.
    let mut answered = Vec::new();
    while answered.last() != Some(&json!(53)) {
        let value = heard(&mut heard_rx, |v| v.get("result").is_some()).await;
        answered.push(value["id"].clone());
    }
    assert_eq!(answered, vec![json!(53)], "only the approval in a published thread is answered");
    // A new thread starts only in a folder this device offers.
    for cwd in ["/home/user/secret/x", "/home/user/elsewhere"] {
        assert_eq!(carry(json!({ "kind": "start", "cwd": cwd, "text": "no" })).await, Err("a new thread starts in one of the folders this device offers".to_string()));
    }
    assert!(heard_so_far(&mut heard_rx).iter().all(|v| v["method"] != "thread/start"));
}

#[tokio::test]
async fn a_folder_excluded_while_its_thread_is_followed_is_let_go() {
    let scratch = scratch("let-go");
    let socket = scratch.0.join("s.sock");
    let (_heard, _say) = fake_app_server(&socket).await;
    let (events, _asked) = mpsc::unbounded_channel();
    let connection = Connection::open(&socket, events).await.unwrap();
    let followed: Vec<String> = connection.attach_loaded(&Settings::default()).await.into_iter().collect();
    assert_eq!(followed, vec![T.to_string(), HIDDEN.to_string()]);
    // `ling sync exclude /home/user/secret` while the thread is loaded: the next look lets it go.
    let settings = Settings { exclude: vec![PathBuf::from("/home/user/secret")], ..Settings::default() };
    assert_eq!(connection.attach_loaded(&settings).await.into_iter().collect::<Vec<_>>(), vec![T.to_string()]);
    assert!(!connection.holds(HIDDEN), "its events no longer reach the hub");
    let refused = connection.carry_out(&Policy::embedded(), &scratch.0.join("home"), &json!({ "kind": "send", "thread": HIDDEN, "text": "hi" })).await;
    assert_eq!(refused, Err("this thread is kept on this device".to_string()));
}

#[tokio::test]
async fn a_subagents_thread_and_one_the_server_answers_about_as_another_are_kept() {
    let scratch = scratch("subagent");
    let socket = scratch.0.join("s.sock");
    let (mut heard_rx, _say) = fake_app_server(&socket).await;
    let (events, _asked) = mpsc::unbounded_channel();
    let connection = Connection::open(&socket, events).await.unwrap();
    // With a folder excluded, a thread whose folder is unknown is kept: an answer about another
    // thread says nothing of this one's.
    let settings = Settings { exclude: vec![PathBuf::from("/home/user/secret")], ..Settings::default() };
    connection.attach_loaded(&settings).await;
    heard_so_far(&mut heard_rx);
    for thread in [SUBAGENT, MISLABELED] {
        let refused = connection.carry_out(&Policy::embedded(), &scratch.0.join("home"), &json!({ "kind": "send", "thread": thread, "text": "hi" })).await;
        assert_eq!(refused, Err("this thread is kept on this device".to_string()), "send to {thread}");
    }
    let methods: Vec<Value> = heard_so_far(&mut heard_rx).iter().map(|v| v["method"].clone()).collect();
    assert!(methods.iter().all(|m| m == "thread/read"), "only reads: {methods:?}");
}

#[tokio::test]
async fn the_host_offers_no_ask_folder_or_none_and_sends_as_the_policy_vets() {
    let scratch = scratch("offers");
    let socket = scratch.0.join("s.sock");
    let (mut heard_rx, _say) = fake_app_server(&socket).await;
    let (events, _asked) = mpsc::unbounded_channel();
    let connection = Connection::open(&socket, events).await.unwrap();
    connection.attach_loaded(&Settings::default()).await;
    let (policy, home) = (Policy::embedded(), scratch.0.join("home"));
    let folder = home.join("ask").join(ASKED);
    std::fs::create_dir_all(&folder).unwrap();
    heard_so_far(&mut heard_rx);
    for thread in [ASKED, NO_FOLDER] {
        assert_eq!(connection.carry_out(&policy, &home, &json!({ "kind": "send", "thread": thread, "text": "hi" })).await, Ok(Some(thread.to_string())));
    }
    // The resume went through the policy as the page's would: an Ask thread stays in its folder.
    let resumed = heard_so_far(&mut heard_rx).into_iter().find(|v| v["method"] == "thread/resume" && v["params"]["threadId"] == ASKED).unwrap();
    assert_eq!(resumed["params"]["cwd"], json!(std::fs::canonicalize(&folder).unwrap().to_string_lossy()));
    assert_eq!(resumed["params"]["sandbox"], "workspace-write");
    // Both are followed now, yet neither folder is offered for a new thread.
    assert!(connection.holds(ASKED) && connection.holds(NO_FOLDER));
    assert_eq!(connection.projects(), vec!["/home/user/app".to_string(), "/home/user/secret/x".to_string()]);
    for cwd in ["/home/user/ask/q", ""] {
        assert_eq!(
            connection.carry_out(&policy, &home, &json!({ "kind": "start", "cwd": cwd, "text": "no" })).await,
            Err("a new thread starts in one of the folders this device offers".to_string())
        );
    }
}

#[tokio::test]
async fn an_approval_is_accept_or_decline_of_a_command_or_a_file_change_and_answered_once() {
    let scratch = scratch("answer");
    let socket = scratch.0.join("s.sock");
    let (mut heard_rx, say) = fake_app_server(&socket).await;
    let (events, mut asked) = mpsc::unbounded_channel();
    let connection = Connection::open(&socket, events).await.unwrap();
    connection.attach_loaded(&Settings::default()).await;
    let (policy, home) = (Policy::embedded(), scratch.0.join("home"));
    say.send(json!({ "id": 60, "method": "item/commandExecution/requestApproval", "params": { "threadId": T, "command": "make" } })).unwrap();
    say.send(json!({ "id": 61, "method": "item/tool/requestUserInput", "params": { "threadId": T, "questions": [] } })).unwrap();
    for _ in 0..2 {
        tokio::time::timeout(Duration::from_secs(10), asked.recv()).await.expect("asked in time").unwrap();
    }
    let carry = |command: Value| {
        let (connection, policy, home) = (connection.clone(), &policy, &home);
        async move { connection.carry_out(policy, home, &command).await }
    };
    for decision in ["acceptForSession", "cancel"] {
        assert_eq!(carry(json!({ "kind": "approve", "request": 60, "decision": decision })).await, Err("an approval is accept or decline".to_string()));
    }
    assert_eq!(carry(json!({ "kind": "approve", "request": 61, "decision": "accept" })).await, Err("this question is answered on the device itself".to_string()));
    assert_eq!(carry(json!({ "kind": "approve", "request": 60, "decision": "decline" })).await, Ok(Some(T.to_string())));
    // A second answer before the server says the request is resolved is refused, not sent.
    assert!(carry(json!({ "kind": "approve", "request": 60, "decision": "accept" })).await.unwrap_err().contains("no server request 60"));
    let answer = heard(&mut heard_rx, |v| v.get("result").is_some()).await;
    assert_eq!(answer, json!({ "id": 60, "result": { "decision": "decline" } }));
    // A message after it: once its turn is answered, the server has heard everything sent before.
    assert_eq!(carry(json!({ "kind": "send", "thread": T, "text": "go on" })).await, Ok(Some(T.to_string())));
    let after: Vec<Value> = heard_so_far(&mut heard_rx);
    assert!(after.iter().all(|v| v.get("result").is_none()), "answered once: {after:?}");
}
