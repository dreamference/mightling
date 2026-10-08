//! The whole path on this side of the messenger: the hub, the link signing in to a real `ling web`
//! (the `ling-web-server` crate, serving on loopback) and its bridge policy, down to a stand-in
//! app-server on a Unix socket. No real `ling`, no model, no messenger.

use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use ling_chat::agent::AgentHandle;
use ling_chat::agent::WebTarget;
use ling_chat::hub::Hub;
use ling_chat::hub::HubConfig;
use ling_chat::hub::Inbound;
use ling_chat::hub::Outbound;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

struct Scratch {
    dir: PathBuf,
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("lc-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch { dir }
}

/// `ling prompt show <name> --composed` prints a marker; anything else fails.
fn fake_ling(dir: &Path) -> PathBuf {
    let path = dir.join("ling");
    std::fs::write(
        &path,
        "#!/bin/sh\nif [ \"$1\" = prompt ] && [ \"$2\" = show ] && [ \"$4\" = --composed ]; then printf 'COMPOSED:%s' \"$3\"; exit 0; fi\necho \"unexpected: $*\" >&2\nexit 7\n",
    )
    .unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

type Frames = mpsc::UnboundedReceiver<(Value, mpsc::UnboundedSender<Value>)>;

async fn fake_app_server(socket: &Path) -> Frames {
    ling_web_server::app_server::prepare_socket_dir(socket).unwrap();
    let listener = tokio::net::UnixListener::bind(socket).unwrap();
    let (frames, received) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let frames = frames.clone();
            tokio::spawn(async move {
                let websocket = tokio_tungstenite::accept_async(stream).await.unwrap();
                let (mut sink, mut stream) = websocket.split();
                let (reply, mut replies) = mpsc::unbounded_channel::<Value>();
                tokio::spawn(async move {
                    while let Some(value) = replies.recv().await {
                        // The test's way to end this connection, as a stopping server would.
                        if value == json!("close") {
                            let _ = sink.close().await;
                            break;
                        }
                        if sink.send(Message::Text(value.to_string().into())).await.is_err() {
                            break;
                        }
                    }
                });
                while let Some(Ok(Message::Text(text))) = stream.next().await {
                    let _ = frames.send((serde_json::from_str::<Value>(text.as_str()).unwrap(), reply.clone()));
                }
            });
        }
    });
    received
}

async fn next_frame(frames: &mut Frames) -> (Value, mpsc::UnboundedSender<Value>) {
    tokio::time::timeout(Duration::from_secs(10), frames.recv()).await.expect("the app-server heard nothing").unwrap()
}

async fn next_out(rx: &mut mpsc::UnboundedReceiver<Outbound>) -> Outbound {
    loop {
        let out = tokio::time::timeout(Duration::from_secs(10), rx.recv()).await.expect("no output").unwrap();
        if !matches!(out, Outbound::Draft { .. }) {
            return out;
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_chat_message_reaches_the_agent_through_ling_web_an_approval_comes_back_and_a_restart_is_survived() {
    let scratch = scratch("e2e");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let codex_home = scratch.dir.join("home");
    let state_dir = auth::state_dir(&codex_home);
    let config = Config {
        codex_home: codex_home.clone(),
        socket: scratch.dir.join("run").join("s.sock"),
        launch: Launch { ling: fake_ling(&scratch.dir), env: Vec::new(), log: state_dir.join("app-server.log") },
        state_dir: state_dir.clone(),
        port,
        lan_names: Vec::new(),
    };
    let mut app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    // What `ling web serve` writes, so clients find the port.
    std::fs::write(state_dir.join("server.json"), json!({ "port": port }).to_string()).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));

    let (agent, commands) = AgentHandle::channel();
    let (events_tx, events) = mpsc::unbounded_channel();
    tokio::spawn(ling_chat::agent::run_link(WebTarget { state: state_dir.clone(), ling: None }, commands, events_tx));
    let mut hub = Hub::new(HubConfig::new(scratch.dir.join("chat")), agent, Box::new(|| false));
    let (out_tx, mut out) = mpsc::unbounded_channel();
    hub.add_adapter("telegram", out_tx);
    let (inbound, inbound_rx) = mpsc::unbounded_channel();
    tokio::spawn(hub.run(inbound_rx, events));

    // The link signs in and initializes.
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialize");
    assert_eq!(frame["params"]["clientInfo"]["name"], "ling-chat");
    reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialized");

    inbound.send(Inbound::Text { chat: "telegram:7".into(), text: "Clean the build folder".into() }).unwrap();
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "thread/start");
    // The policy, not the bridge, turned `prompt: ask` into the instructions and an Ask folder.
    assert_eq!(frame["params"]["baseInstructions"], "COMPOSED:ask");
    assert!(frame["params"]["cwd"].as_str().unwrap().contains("/ask/"));
    reply.send(json!({ "id": frame["id"], "result": { "thread": { "id": "thr-1" } } })).unwrap();
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "thread/name/set");
    reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "turn/start");
    assert_eq!(frame["params"]["input"][0]["text"], "Clean the build folder");
    reply.send(json!({ "id": frame["id"], "result": { "turn": { "id": "turn-1" } } })).unwrap();

    // The agent asks; the phone approves; the answer passes the policy's pending check.
    reply
        .send(json!({ "id": 900, "method": "item/commandExecution/requestApproval", "params": { "threadId": "thr-1", "turnId": "turn-1", "itemId": "i", "startedAtMs": 0, "command": "rm -rf build" } }))
        .unwrap();
    let approval = match next_out(&mut out).await {
        Outbound::Approval { id, text, .. } => {
            assert!(text.contains("rm -rf build"));
            id
        }
        other => panic!("expected an approval, got {other:?}"),
    };
    inbound.send(Inbound::Answer { chat: "telegram:7".into(), approval, choice: 0 }).unwrap();
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame, json!({ "id": 900, "result": { "decision": "accept" } }));
    assert!(matches!(next_out(&mut out).await, Outbound::ApprovalClosed { .. }));

    for delta in ["Done", "."] {
        reply.send(json!({ "method": "item/agentMessage/delta", "params": { "threadId": "thr-1", "turnId": "turn-1", "itemId": "m", "delta": delta } })).unwrap();
    }
    reply.send(json!({ "method": "turn/completed", "params": { "threadId": "thr-1", "turn": { "id": "turn-1", "status": "completed" } } })).unwrap();
    assert_eq!(next_out(&mut out).await, Outbound::TurnEnded { chat: "telegram:7".into() });
    assert_eq!(next_out(&mut out).await, Outbound::Message { chat: "telegram:7".into(), markdown: "Done.".into() });

    // `ling web` restarts: its sessions were in memory, so the bridge must sign in again.
    let _ = stop.send(());
    serving.await.unwrap().unwrap();
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.unwrap();
    let config = Config {
        codex_home: codex_home.clone(),
        socket: scratch.dir.join("run").join("s.sock"),
        launch: Launch { ling: fake_ling(&scratch.dir), env: Vec::new(), log: state_dir.join("app-server.log") },
        state_dir: state_dir.clone(),
        port,
        lan_names: Vec::new(),
    };
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    // In-process, the old server's tab outlives its listener; a real restart ends the process.
    // Closing the agent's side ends the tab, and the bridge must find the new server.
    reply.send(json!("close")).unwrap();
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialize");
    reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialized");
    // The same chat continues its thread: resubscribed first, on the new connection.
    tokio::time::sleep(Duration::from_millis(200)).await;
    inbound.send(Inbound::Text { chat: "telegram:7".into(), text: "And the cache?".into() }).unwrap();
    let (frame, reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "thread/resume");
    assert_eq!(frame["params"]["threadId"], "thr-1");
    reply.send(json!({ "id": frame["id"], "result": { "thread": { "id": "thr-1" } } })).unwrap();
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "turn/start");
    assert_eq!(frame["params"]["input"][0]["text"], "And the cache?");

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}
