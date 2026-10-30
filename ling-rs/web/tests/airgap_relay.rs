//! The air gap on a tab's WebSocket (`/ws`): a `!` command is judged by its thread's level, read
//! in the folder the server says the thread works in, and an install by the configured level. A
//! test binary of its own: it points `HOME` and `CODEX_HOME` at a scratch folder and sets the
//! air-gap variables, which the tests in server.rs would see if they shared the process.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::Request;
use axum::http::header;
use futures::SinkExt;
use futures::StreamExt;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tower::ServiceExt;

type Frames = mpsc::UnboundedReceiver<(Value, mpsc::UnboundedSender<Value>)>;

fn config(dir: &Path, port: u16) -> Config {
    let codex_home = dir.join("home/.mightling");
    let state_dir = auth::state_dir(&codex_home);
    Config {
        codex_home,
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: dir.join("no-ling"), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        user_config: Some(dir.join("home/.config/dreamference/config.toml")),
        forge_url: None,
        state_dir,
        port,
        lan_names: Vec::new(),
        public_names: Vec::new(),
    }
}

/// The stand-in app-server: WebSocket connections on the Unix socket, their frames handed to the test.
fn fake_app_server(socket: &Path) -> Frames {
    ling_web_server::app_server::prepare_socket_dir(socket).unwrap();
    let listener = tokio::net::UnixListener::bind(socket).unwrap();
    let (frames, received) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let frames = frames.clone();
            tokio::spawn(async move {
                let (mut sink, mut stream) = tokio_tungstenite::accept_async(stream).await.unwrap().split();
                let (reply, mut replies) = mpsc::unbounded_channel::<Value>();
                tokio::spawn(async move {
                    while let Some(value) = replies.recv().await {
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
    tokio::time::timeout(Duration::from_secs(10), frames.recv()).await.unwrap().unwrap()
}

struct Browser {
    sink: futures::stream::SplitSink<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>, Message>,
    stream: futures::stream::SplitStream<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>>,
    next: u64,
}

impl Browser {
    async fn connect(server: &Arc<Server>, port: u16) -> Self {
        let code = auth::issue_login_code(&server.config.state_dir).unwrap();
        let login = Request::builder().uri(format!("/login?code={code}")).header(header::HOST, format!("127.0.0.1:{port}"));
        let response = server::router(server.clone()).oneshot(login.body(Body::empty()).unwrap()).await.unwrap();
        let cookie = response.headers().get(header::SET_COOKIE).unwrap().to_str().unwrap().split(';').next().unwrap().to_string();
        let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request().unwrap();
        upgrade.headers_mut().insert(header::ORIGIN, format!("http://127.0.0.1:{port}").parse().unwrap());
        upgrade.headers_mut().insert(header::COOKIE, cookie.parse().unwrap());
        let (sink, stream) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap().0.split();
        Browser { sink, stream, next: 0 }
    }

    async fn call(&mut self, message: Value) -> Value {
        self.next += 1;
        let call = self.next;
        self.sink.send(Message::Text(json!({ "call": call, "message": message }).to_string().into())).await.unwrap();
        loop {
            let frame = self.frame().await;
            if frame.get("answer") == Some(&json!(call)) {
                return frame;
            }
        }
    }

    async fn send(&mut self, message: Value) -> Value {
        self.call(json!({ "type": "work/send", "message": message })).await
    }

    async fn frame(&mut self) -> Value {
        let frame = tokio::time::timeout(Duration::from_secs(10), self.stream.next()).await.unwrap().unwrap().unwrap();
        serde_json::from_str(frame.into_text().unwrap().as_str()).unwrap()
    }

    /// The next `work://message` event.
    async fn message(&mut self) -> Value {
        loop {
            let frame = self.frame().await;
            if frame.pointer("/event/channel") == Some(&json!("work://message")) {
                return frame["event"]["payload"].clone();
            }
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_tab_judges_a_command_by_its_threads_folder_and_an_install_by_the_configured_level() {
    let dir = PathBuf::from("/tmp").join(format!("lw-airgap-relay-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    // SAFETY: this binary holds this one test, so nothing else reads the environment meanwhile.
    unsafe {
        std::env::set_var("HOME", dir.join("home"));
        std::env::set_var("CODEX_HOME", dir.join("home/.mightling"));
        std::env::remove_var(ling_airgapped::ENV_VAR);
        std::env::remove_var("DREAMFERENCE_CONFIG_PATH");
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&dir, port);
    let mut app_server = fake_app_server(&config.socket);
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    let mut browser = Browser::connect(&server, port).await;
    browser.call(json!({ "type": "work/start" })).await;

    // Two threads: one works in a folder whose dreamference.toml asks for `on`, one in a plain folder.
    let gapped = dir.join("gapped");
    let plain = dir.join("plain");
    for folder in [&gapped, &plain] {
        std::fs::create_dir_all(folder).unwrap();
    }
    std::fs::write(gapped.join("dreamference.toml"), "mightling_airgapped = \"on\"\n").unwrap();
    for (id, thread, folder) in [(1, "thr-gapped", &gapped), (2, "thr-plain", &plain)] {
        let cwd = folder.to_string_lossy();
        browser.send(json!({ "id": id, "method": "thread/start", "params": { "cwd": cwd } })).await;
        let (_, reply) = next_frame(&mut app_server).await;
        reply.send(json!({ "id": id, "result": { "thread": { "id": thread, "cwd": cwd } } })).unwrap();
        assert_eq!(browser.message().await["result"]["thread"]["id"], thread);
    }

    // A `!` command in the first is refused by its folder's level; in the second it goes on.
    let refused = browser.send(json!({ "id": 3, "method": "thread/shellCommand", "params": { "threadId": "thr-gapped", "command": "ls" } })).await;
    assert!(refused["error"].as_str().unwrap_or_default().contains("airgapped is on"), "{refused}");
    let passed = browser.send(json!({ "id": 4, "method": "thread/shellCommand", "params": { "threadId": "thr-plain", "command": "ls" } })).await;
    assert!(passed.get("error").is_none(), "{passed}");
    assert_eq!(next_frame(&mut app_server).await.0["id"], 4);

    // A plugin installs at the configured `off`, and not at `on`.
    let install = |id: u64| json!({ "id": id, "method": "plugin/install", "params": {} });
    let passed = browser.send(install(5)).await;
    assert!(passed.get("error").is_none(), "{passed}");
    assert_eq!(next_frame(&mut app_server).await.0["id"], 5);
    // SAFETY: as above.
    unsafe { std::env::set_var(ling_airgapped::ENV_VAR, "on") };
    let refused = browser.send(install(6)).await;
    unsafe { std::env::remove_var(ling_airgapped::ENV_VAR) };
    assert!(refused["error"].as_str().unwrap_or_default().contains("airgapped is on"), "{refused}");

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
    let _ = std::fs::remove_dir_all(&dir);
}
