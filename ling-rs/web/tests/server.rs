//! `ling web` end to end, with no real network, no real `ling` and no real app-server: requests go
//! to the router in memory, the WebSocket test talks to a server on an ephemeral loopback port, and
//! the app-server is a WebSocket on a scratch Unix socket that this test answers itself.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::Request;
use axum::http::StatusCode;
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
use tokio_tungstenite::tungstenite::Message;
use tower::ServiceExt;

struct Scratch {
    dir: PathBuf,
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

/// A scratch folder short enough for a Unix socket path.
fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("lw-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch { dir }
}

/// A stand-in for `ling`: `prompt show <name> --composed` prints a marker naming the prompt and the
/// folder it ran in; anything else fails, so an attempt to start an app-server is visible.
fn fake_ling(dir: &Path) -> PathBuf {
    let path = dir.join("ling");
    std::fs::write(
        &path,
        "#!/bin/sh\nif [ \"$1\" = prompt ] && [ \"$2\" = show ] && [ \"$4\" = --composed ]; then printf 'COMPOSED:%s:%s' \"$3\" \"$(pwd)\"; exit 0; fi\necho \"unexpected: $*\" >&2\nexit 7\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    path
}

fn config(dir: &Path, port: u16, lan_names: Vec<String>) -> Config {
    let codex_home = dir.join("home");
    let state_dir = auth::state_dir(&codex_home);
    Config {
        codex_home,
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: fake_ling(dir), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        // Nothing listens on port 9 (discard): a test that needs the service starts its own.
        speech_addr: "127.0.0.1:9".to_string(),
        state_dir,
        port,
        lan_names,
    }
}

const HOST: &str = "127.0.0.1:3100";
const ORIGIN: &str = "http://127.0.0.1:3100";

fn request(method: &str, uri: &str) -> axum::http::request::Builder {
    Request::builder().method(method).uri(uri).header(header::HOST, HOST)
}

async fn call(server: &Arc<Server>, request: Request<Body>) -> (StatusCode, axum::http::HeaderMap, String) {
    let response = server::router(server.clone()).oneshot(request).await.unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, headers, String::from_utf8_lossy(&body).into_owned())
}

/// Signs in the way `ling web open` does and returns the cookie header to send.
async fn sign_in(server: &Arc<Server>) -> String {
    let code = auth::issue_login_code(&server.config.state_dir).unwrap();
    let login = Request::builder()
        .uri(format!("/login?code={code}"))
        .header(header::HOST, format!("127.0.0.1:{}", server.config.port));
    let (status, headers, _) = call(server, login.body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    let cookie = headers.get(header::SET_COOKIE).unwrap().to_str().unwrap().to_string();
    assert!(cookie.contains("HttpOnly") && cookie.contains("SameSite=Strict") && cookie.contains("Path=/"), "{cookie}");
    cookie.split(';').next().unwrap().to_string()
}

const PROTECTED: &[(&str, &str)] = &[
    ("GET", "/"),
    ("GET", "/index.html"),
    ("GET", "/assets/index.js"),
    ("GET", "/bridge.js"),
    ("GET", "/ws"),
    ("GET", "/healthz"),
    ("POST", "/api/upload?thread=t&kind=file"),
    ("POST", "/api/transcribe"),
    ("GET", "/api/apps"),
    ("GET", "/api/airgapped"),
    ("GET", "/images/0123456789abcdef.jpg"),
    ("GET", "/devices"),
    ("POST", "/devices/pair"),
    ("POST", "/devices/revoke"),
    ("GET", "/anything-else"),
];

#[tokio::test]
async fn every_route_but_the_sign_in_pages_needs_a_credential_on_loopback_too() {
    let scratch = scratch("auth");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    for (method, uri) in PROTECTED {
        let (status, headers, _) = call(&server, request(method, uri).header(header::ORIGIN, ORIGIN).body(Body::empty()).unwrap()).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{method} {uri}");
        assert!(headers.get(header::CONTENT_SECURITY_POLICY).unwrap().to_str().unwrap().starts_with("default-src 'none'"));
    }
    for uri in ["/pair", "/pair.css"] {
        let (status, _, _) = call(&server, request("GET", uri).body(Body::empty()).unwrap()).await;
        assert_eq!(status, StatusCode::OK, "{uri}");
    }
    // Signed in, the same routes answer.
    let cookie = sign_in(&server).await;
    let (status, _, body) = call(&server, request("GET", "/healthz").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["ok"], true);
    let (status, _, body) = call(&server, request("GET", "/").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Mightling"));
    let (status, headers, body) = call(&server, request("GET", "/bridge.js").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert!(headers[header::CONTENT_TYPE].to_str().unwrap().starts_with("text/javascript"));
    assert!(body.contains("window.electronBridge") && body.contains("mightlingWindowType = \"web\""));
    // The machine's air-gap level, for the Signal bridge, which cannot read the user's files.
    let (status, _, body) = call(&server, request("GET", "/api/airgapped").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    let level = serde_json::from_str::<Value>(&body).unwrap()["level"].as_str().unwrap().to_string();
    assert!(level == "on" || level == "off", "{body}");
    // A made-up session cookie is nothing.
    let (status, _, _) =
        call(&server, request("GET", "/healthz").header(header::COOKIE, "mightling_session=forged").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn the_owner_token_opens_the_health_check_and_nothing_else() {
    let scratch = scratch("bearer");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let token = std::fs::read_to_string(server.config.state_dir.join("token")).unwrap();
    let (status, _, _) =
        call(&server, request("GET", "/healthz").header(header::AUTHORIZATION, format!("Bearer {token}")).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) =
        call(&server, request("GET", "/healthz").header(header::AUTHORIZATION, "Bearer wrong").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    // A command inside the agent's sandbox can read the token file: it must not open the bridge,
    // the page or an upload with it.
    for (method, uri) in [("GET", "/ws"), ("GET", "/"), ("POST", "/api/upload?thread=t&kind=file")] {
        let bearer = request(method, uri)
            .header(header::AUTHORIZATION, format!("Bearer {token}"))
            .header(header::ORIGIN, ORIGIN)
            .header(header::UPGRADE, "websocket");
        assert_eq!(call(&server, bearer.body(Body::empty()).unwrap()).await.0, StatusCode::UNAUTHORIZED, "{method} {uri}");
    }
}

#[tokio::test]
async fn a_login_link_works_once() {
    let scratch = scratch("login");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let code = auth::issue_login_code(&server.config.state_dir).unwrap();
    let (status, _, _) = call(&server, request("GET", &format!("/login?code={code}")).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    let (status, headers, _) = call(&server, request("GET", &format!("/login?code={code}")).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(headers.get(header::SET_COOKIE).is_none());
}

/// A build with the UI embedded (`LING_WEB_UI_DIST`, as every `ling` build sets it) serves the
/// UI's page with the bridge first, and every asset the page names. Without it there is nothing
/// to check but the placeholder.
#[tokio::test]
async fn the_embedded_ui_is_served_with_the_bridge_first() {
    let scratch = scratch("ui");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let (status, _, page) = call(&server, request("GET", "/").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    if ling_web_server::assets::ui_asset("index.html").is_none() {
        assert!(page.contains("LING_WEB_UI_DIST"), "the placeholder says how to embed the UI");
        return;
    }
    let bridge = page.find(ling_web_server::assets::BRIDGE_TAG).expect("the bridge tag");
    let module = page.find("type=\"module\"").expect("the UI's module script");
    assert!(bridge < module, "{page}");
    let assets: Vec<&str> = page.split('"').filter(|part| part.starts_with("./assets/") || part.starts_with("/assets/")).collect();
    assert!(!assets.is_empty(), "{page}");
    for asset in assets {
        let path = asset.trim_start_matches('.');
        let (status, headers, _) = call(&server, request("GET", path).header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
        assert_eq!(status, StatusCode::OK, "{path}");
        let kind = headers[header::CONTENT_TYPE].to_str().unwrap();
        assert!(kind.starts_with("text/javascript") || kind.starts_with("text/css") || kind == "image/png", "{path}: {kind}");
    }
}

#[tokio::test]
async fn host_and_origin_must_be_the_servers_own_even_with_a_credential() {
    let scratch = scratch("origin");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    // DNS rebinding: a name that resolves here but is not this server's.
    let rebound = Request::builder().uri("/healthz").header(header::HOST, "evil.example:3100").header(header::COOKIE, &cookie);
    assert_eq!(call(&server, rebound.body(Body::empty()).unwrap()).await.0, StatusCode::MISDIRECTED_REQUEST);
    // The LAN address is not served by a loopback-only server.
    let lan = Request::builder().uri("/healthz").header(header::HOST, "192.168.0.105:3100").header(header::COOKIE, &cookie);
    assert_eq!(call(&server, lan.body(Body::empty()).unwrap()).await.0, StatusCode::MISDIRECTED_REQUEST);
    // A POST or an upgrade from another site, or with no Origin at all.
    for origin in [Some("http://evil.example"), Some("null"), None] {
        let mut post = request("POST", "/api/upload?thread=t&kind=file").header(header::COOKIE, &cookie);
        let mut upgrade = request("GET", "/ws").header(header::COOKIE, &cookie).header(header::UPGRADE, "websocket");
        if let Some(origin) = origin {
            post = post.header(header::ORIGIN, origin);
            upgrade = upgrade.header(header::ORIGIN, origin);
        }
        assert_eq!(call(&server, post.body(Body::empty()).unwrap()).await.0, StatusCode::FORBIDDEN, "{origin:?}");
        assert_eq!(call(&server, upgrade.body(Body::empty()).unwrap()).await.0, StatusCode::FORBIDDEN, "{origin:?}");
    }
}

#[tokio::test]
async fn an_advertised_node_answers_to_its_lan_address() {
    let scratch = scratch("lan");
    let server = Server::new(config(&scratch.dir, 3100, vec!["192.168.0.105".to_string()])).unwrap();
    let lan = Request::builder().uri("/healthz").header(header::HOST, "192.168.0.105:3100");
    // Reached, and still refused without a credential: the LAN gets no more than loopback.
    assert_eq!(call(&server, lan.body(Body::empty()).unwrap()).await.0, StatusCode::UNAUTHORIZED);
}

fn pair_form(code: &str, name: &str) -> Request<Body> {
    request("POST", "/pair")
        .header(header::ORIGIN, ORIGIN)
        .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
        .body(Body::from(format!("code={code}&name={name}")))
        .unwrap()
}

#[tokio::test]
async fn a_pairing_code_pairs_one_device_once() {
    let scratch = scratch("pair");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let code = auth::issue_pairing_code(&server.config.state_dir).unwrap();
    let (status, headers, _) = call(&server, pair_form(&code, "phone")).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    let cookie = headers[header::SET_COOKIE].to_str().unwrap().to_string();
    assert!(cookie.starts_with("mightling_device=") && cookie.contains("Max-Age=") && cookie.contains("HttpOnly"));
    let device = cookie.split(';').next().unwrap().to_string();
    assert_eq!(call(&server, request("GET", "/healthz").header(header::COOKIE, &device).body(Body::empty()).unwrap()).await.0, StatusCode::OK);
    // The code is spent.
    assert_eq!(call(&server, pair_form(&code, "laptop")).await.0, StatusCode::UNAUTHORIZED);
    assert_eq!(auth::load_devices(&server.config.state_dir).len(), 1);
    // Revoked, the device's next request is refused.
    let id = auth::load_devices(&server.config.state_dir)[0].id.clone();
    auth::revoke_device(&server.config.state_dir, &id).unwrap();
    assert_eq!(
        call(&server, request("GET", "/healthz").header(header::COOKIE, &device).body(Body::empty()).unwrap()).await.0,
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn ten_wrong_codes_withdraw_the_pending_one() {
    let scratch = scratch("guess");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let code = auth::issue_pairing_code(&server.config.state_dir).unwrap();
    let wrong = if code == "00000000" { "11111111" } else { "00000000" };
    for _ in 0..auth::PAIRING_ATTEMPTS {
        assert_eq!(call(&server, pair_form(wrong, "x")).await.0, StatusCode::UNAUTHORIZED);
    }
    assert_eq!(call(&server, pair_form(&code, "x")).await.0, StatusCode::UNAUTHORIZED, "the right code no longer works");
    assert!(auth::load_devices(&server.config.state_dir).is_empty());
}

/// The eight digits the Devices page shows, from its `class="code"` paragraph.
fn shown_code(page: &str) -> String {
    let start = page.find("class=\"code\">").expect("the page shows a code") + "class=\"code\">".len();
    page[start..].chars().take_while(|c| *c != '<').filter(char::is_ascii_digit).collect()
}

fn devices_post(uri: &str, cookie: &str, body: &str) -> Request<Body> {
    request("POST", uri)
        .header(header::ORIGIN, ORIGIN)
        .header(header::COOKIE, cookie)
        .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
        .body(Body::from(body.to_string()))
        .unwrap()
}

#[tokio::test]
async fn the_devices_page_pairs_a_phone_by_qr_code_and_only_this_machine_may_use_it() {
    let scratch = scratch("devices");
    let lan = vec!["172.17.0.1".to_string(), "192.168.0.105".to_string()];
    let server = Server::new(config(&scratch.dir, 3100, lan)).unwrap();
    let get = |cookie: Option<&str>| {
        let builder = request("GET", "/devices");
        let builder = match cookie {
            Some(cookie) => builder.header(header::COOKIE, cookie),
            None => builder,
        };
        builder.body(Body::empty()).unwrap()
    };
    assert_eq!(call(&server, get(None)).await.0, StatusCode::UNAUTHORIZED);
    let owner = sign_in(&server).await;
    let (status, _, page) = call(&server, get(Some(&owner))).await;
    assert_eq!(status, StatusCode::OK);
    assert!(page.contains("action=\"/devices/pair\"") && !page.contains("class=\"code\""), "no code until asked for");

    // Asked for, a code and its QR codes, the LAN address first; nothing longer-lived is in them.
    let (status, _, page) = call(&server, devices_post("/devices/pair", &owner, "")).await;
    assert_eq!(status, StatusCode::OK);
    let code = shown_code(&page);
    assert_eq!(code.len(), 8);
    assert!(page.contains("<svg") && page.contains("This code is for 192.168.0.105."));
    let token = std::fs::read_to_string(server.config.state_dir.join("token")).unwrap();
    assert!(!page.contains(token.trim()) && !page.contains(owner.split('=').nth(1).unwrap()));
    // Changing something still needs the server's own origin.
    let foreign = request("POST", "/devices/pair").header(header::ORIGIN, "http://evil.example").header(header::COOKIE, &owner);
    assert_eq!(call(&server, foreign.body(Body::empty()).unwrap()).await.0, StatusCode::FORBIDDEN);

    // The QR code's link opens the pairing form with the code filled in, and pairs nothing by itself.
    let link = request("GET", &format!("/pair?code={code}")).header(header::HOST, "192.168.0.105:3100");
    let (status, _, form) = call(&server, link.body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert!(form.contains(&format!("value=\"{code}\"")));
    assert!(auth::load_devices(&server.config.state_dir).is_empty());
    let (_, _, form) = call(&server, request("GET", "/pair?code=%22%3E%3Cb%3E").body(Body::empty()).unwrap()).await;
    assert!(form.contains("value=\"\"") && !form.contains("<b>"), "only eight digits are shown back");
    let (status, headers, _) = call(&server, pair_form(&code, "phone")).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    let device = headers[header::SET_COOKIE].to_str().unwrap().split(';').next().unwrap().to_string();

    // A paired device may use Mightling, but neither pair another device nor revoke one.
    assert_eq!(call(&server, get(Some(&device))).await.0, StatusCode::FORBIDDEN);
    let (status, _, page) = call(&server, devices_post("/devices/pair", &device, "")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(!page.contains("class=\"code\""));
    let id = auth::load_devices(&server.config.state_dir)[0].id.clone();
    assert_eq!(call(&server, devices_post("/devices/revoke", &device, &format!("device={id}"))).await.0, StatusCode::FORBIDDEN);

    // This machine lists it and revokes it; its next request is refused.
    let (_, _, page) = call(&server, get(Some(&owner))).await;
    assert!(page.contains("phone") && page.contains(&format!("value=\"{id}\"")));
    let (status, headers, _) = call(&server, devices_post("/devices/revoke", &owner, &format!("device={id}"))).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    assert_eq!(headers[header::LOCATION], "/devices");
    assert!(auth::load_devices(&server.config.state_dir).is_empty());
    assert_eq!(call(&server, request("GET", "/healthz").header(header::COOKIE, &device).body(Body::empty()).unwrap()).await.0, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_loopback_server_issues_no_pairing_code_from_the_devices_page() {
    let scratch = scratch("devlo");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let owner = sign_in(&server).await;
    let (status, _, page) = call(&server, devices_post("/devices/pair", &owner, "")).await;
    assert_eq!(status, StatusCode::OK);
    assert!(page.contains("loopback only") && !page.contains("class=\"code\""));
    assert!(!server.config.state_dir.join("pairing").exists() || std::fs::read_dir(server.config.state_dir.join("pairing")).unwrap().next().is_none());
}

#[tokio::test]
async fn uploads_go_to_an_ask_threads_folder_within_the_caps() {
    let scratch = scratch("upload");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let root = server.ask_root();
    let folder = ling_web_server::ask::new_folder(&root).unwrap();
    ling_web_server::ask::link_thread(&root, "thr-1", &folder).unwrap();
    let upload = |uri: String, body: Vec<u8>| {
        request("POST", &uri).header(header::ORIGIN, ORIGIN).header(header::COOKIE, &cookie).body(Body::from(body)).unwrap()
    };
    let (status, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=../../notes.txt&kind=file".into(), b"hello".to_vec())).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let path = PathBuf::from(serde_json::from_str::<Value>(&body).unwrap()["path"].as_str().unwrap());
    assert_eq!(path, folder.join("notes.txt"));
    assert_eq!(std::fs::read(&path).unwrap(), b"hello");
    // The same name again gets a new file, not an overwrite.
    let (_, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=notes.txt&kind=file".into(), b"again".to_vec())).await;
    assert!(body.contains("notes-1.txt"), "{body}");
    // Over the image cap.
    let big = vec![0u8; (server::IMAGE_CAP + 1) as usize];
    let (status, _, _) = call(&server, upload("/api/upload?thread=thr-1&name=a.png&kind=image".into(), big)).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert!(!folder.join("a.png").exists());
    // Not an Ask thread: no folder of the user's is ever written.
    let (status, _, _) = call(&server, upload("/api/upload?thread=thr-other&name=a.txt&kind=file".into(), b"x".to_vec())).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

/// The stand-in app-server: one WebSocket connection on the Unix socket, its frames handed to the test.
async fn fake_app_server(socket: &Path) -> tokio::sync::mpsc::UnboundedReceiver<(Value, tokio::sync::mpsc::UnboundedSender<Value>)> {
    ling_web_server::app_server::prepare_socket_dir(socket).unwrap();
    let listener = tokio::net::UnixListener::bind(socket).unwrap();
    let (frames, received) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let frames = frames.clone();
            tokio::spawn(async move {
                let websocket = tokio_tungstenite::accept_async(stream).await.unwrap();
                let (mut sink, mut stream) = websocket.split();
                let (reply, mut replies) = tokio::sync::mpsc::unbounded_channel::<Value>();
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

struct Browser {
    sink: futures::stream::SplitSink<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>, Message>,
    stream: futures::stream::SplitStream<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>>,
    next: u64,
}

impl Browser {
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

async fn next_frame(
    received: &mut tokio::sync::mpsc::UnboundedReceiver<(Value, tokio::sync::mpsc::UnboundedSender<Value>)>,
) -> (Value, tokio::sync::mpsc::UnboundedSender<Value>) {
    tokio::time::timeout(Duration::from_secs(10), received.recv()).await.unwrap().unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_tab_reaches_the_app_server_through_the_policy_and_ask_threads_get_their_prompt_and_folder() {
    let scratch = scratch("ws");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&scratch.dir, port, Vec::new());
    let mut app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));

    // Without a credential the upgrade is refused.
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut refused = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    refused.headers_mut().remove(header::COOKIE);
    assert!(tokio_tungstenite::client_async(refused, tcp).await.is_err());

    let cookie = sign_in(&server).await;
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    upgrade.headers_mut().insert(header::COOKIE, cookie.parse().unwrap());
    let (websocket, _) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap();
    let (sink, stream) = websocket.split();
    let mut browser = Browser { sink, stream, next: 0 };

    // Sending before starting is refused; starting connects to the server already listening.
    let early = browser.call(json!({ "type": "work/send", "message": { "id": 0, "method": "thread/list" } })).await;
    assert!(early["error"].as_str().unwrap().contains("not running"));
    let started = browser.call(json!({ "type": "work/start" })).await;
    assert_eq!(started["result"]["started"], false, "{started}");
    // The UI tells Ask threads from projects by this folder, named as their `cwd` will be.
    let ask_root = started["result"]["ask_root"].as_str().unwrap();
    assert_eq!(Path::new(ask_root), server.ask_root().canonicalize().unwrap().as_path(), "{started}");

    // An ordinary request passes; one outside the allow-list never reaches the server.
    let refused = browser.call(json!({ "type": "work/send", "message": { "id": 1, "method": "account/login/start", "params": {} } })).await;
    assert!(refused["error"].as_str().unwrap().contains("account/login/start"));
    browser.call(json!({ "type": "work/send", "message": { "jsonrpc": "2.0", "id": 2, "method": "thread/list", "params": {} } })).await;
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame, json!({ "id": 2, "method": "thread/list", "params": {} }));

    // An Ask thread: the prompt comes from `ling prompt show ask --composed`, run in the Ask root;
    // the folder is new, and the UI's cwd, sandbox and instructions are gone.
    let ask = browser
        .call(json!({ "type": "work/send", "message": { "id": 3, "method": "thread/start", "params": {
            "prompt": "ask", "cwd": "/home/user", "sandbox": "danger-full-access", "baseInstructions": "the page's own" } } }))
        .await;
    assert_eq!(ask["result"], Value::Null, "{ask}");
    let (frame, reply) = next_frame(&mut app_server).await;
    let root = server.ask_root().canonicalize().unwrap();
    let params = &frame["params"];
    assert_eq!(params["baseInstructions"], json!(format!("COMPOSED:ask:{}", root.display())));
    assert_eq!(params["sandbox"], "workspace-write");
    let folder = PathBuf::from(params["cwd"].as_str().unwrap());
    assert_eq!(folder.parent().unwrap(), root);
    assert!(params.get("prompt").is_none());
    // The server's answer names the thread, and the folder is named after it.
    reply.send(json!({ "id": 3, "result": { "thread": { "id": "thr-ask-1" } } })).unwrap();
    assert_eq!(browser.message().await["result"]["thread"]["id"], "thr-ask-1");
    assert_eq!(ling_web_server::ask::folder_of(&root, "thr-ask-1"), Some(folder.clone()));

    // Resumed elsewhere, it stays in its folder.
    browser
        .call(json!({ "type": "work/send", "message": { "id": 4, "method": "thread/resume", "params": { "threadId": "thr-ask-1", "cwd": "/home/user" } } }))
        .await;
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["params"]["cwd"], json!(folder.to_string_lossy()));

    // An unknown prompt name is refused.
    let unknown = browser
        .call(json!({ "type": "work/send", "message": { "id": 5, "method": "thread/start", "params": { "prompt": "mine" } } }))
        .await;
    assert!(unknown["error"].as_str().unwrap().contains("mine"));

    // An approval the server asked for can be answered once; one it never asked for, never.
    reply.send(json!({ "id": "ap-1", "method": "item/commandExecution/requestApproval", "params": {} })).unwrap();
    assert_eq!(browser.message().await["id"], "ap-1");
    let invented = browser.call(json!({ "type": "work/send", "message": { "id": "ap-2", "result": { "decision": "approved" } } })).await;
    assert!(invented.get("error").is_some());
    let answered = browser.call(json!({ "type": "work/send", "message": { "id": "ap-1", "result": { "decision": "approved" } } })).await;
    assert!(answered.get("error").is_none(), "{answered}");
    assert_eq!(next_frame(&mut app_server).await.0["id"], "ap-1");
    let twice = browser.call(json!({ "type": "work/send", "message": { "id": "ap-1", "result": { "decision": "approved" } } })).await;
    assert!(twice.get("error").is_some());

    // A running turn is Night Shift's cue to wait.
    reply.send(json!({ "method": "turn/started", "params": { "threadId": "thr-ask-1" } })).unwrap();
    browser.message().await;
    assert!(server.marker_path().exists());
    reply.send(json!({ "method": "turn/completed", "params": { "threadId": "thr-ask-1" } })).unwrap();
    browser.message().await;
    assert!(!server.marker_path().exists());

    // Something on the protocol channel that is not protocol is reported, not delivered.
    reply.send(json!("a bare string")).unwrap();
    loop {
        let frame = browser.frame().await;
        if frame.pointer("/event/channel") == Some(&json!("work://protocol-error")) {
            break;
        }
    }

    // The air-gap level answers from the shared resolver; the window commands are no-ops.
    let level = browser.call(json!({ "type": "work/airgapped", "thread": "thr-ask-1" })).await;
    assert!(["off", "on"].contains(&level["result"]["level"].as_str().unwrap()), "{level}");
    assert_eq!(browser.call(json!({ "type": "window/close" })).await["result"], Value::Null);
    assert!(browser.call(json!({ "type": "shell/run" })).await.get("error").is_some());

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}

/// A client request for `/ws` with the server's own Origin and Host.
trait OriginRequest {
    fn into_client_request_with_origin(self, port: u16) -> tokio_tungstenite::tungstenite::handshake::client::Request;
}

impl OriginRequest for String {
    fn into_client_request_with_origin(self, port: u16) -> tokio_tungstenite::tungstenite::handshake::client::Request {
        use tokio_tungstenite::tungstenite::client::IntoClientRequest;
        let mut request = self.into_client_request().unwrap();
        request.headers_mut().insert(header::ORIGIN, format!("http://127.0.0.1:{port}").parse().unwrap());
        request
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn with_nothing_listening_the_server_starts_the_app_server_and_reports_why_it_failed() {
    let scratch = scratch("spawn");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = Server::new(config(&scratch.dir, port, Vec::new())).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    let cookie = sign_in(&server).await;
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    upgrade.headers_mut().insert(header::COOKIE, cookie.parse().unwrap());
    let (websocket, _) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap();
    let (sink, stream) = websocket.split();
    let mut browser = Browser { sink, stream, next: 0 };
    let started = browser.call(json!({ "type": "work/start" })).await;
    let error = started["error"].as_str().unwrap();
    assert!(error.contains("exited") && error.contains("unexpected: -c features.code_mode_host=true"), "{started}");
    // The stand-in was asked for exactly the desktop app's command, on the private socket.
    let log = std::fs::read_to_string(&server.config.launch.log).unwrap();
    let socket = server.config.socket.display().to_string();
    assert!(log.contains(&format!("unexpected: -c features.code_mode_host=true app-server --listen unix://{socket}")), "{log}");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(server.config.socket.parent().unwrap()).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o700);
    }
    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn ling_web_ask_signs_in_like_a_browser_and_runs_one_ask_thread() {
    let scratch = scratch("askcli");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&scratch.dir, port, Vec::new());
    let mut app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    // The stand-in answers like the app-server: a thread, then a streamed reply.
    let seen = tokio::spawn(async move {
        let mut seen = Vec::new();
        loop {
            let (frame, reply) = next_frame(&mut app_server).await;
            seen.push(frame.clone());
            let id = frame.get("id").cloned();
            match frame.get("method").and_then(Value::as_str) {
                Some("initialize") => reply.send(json!({ "id": id, "result": {} })).unwrap(),
                Some("thread/start") => reply.send(json!({ "id": id, "result": { "thread": { "id": "thr-cli" } } })).unwrap(),
                Some("turn/start") => {
                    reply.send(json!({ "id": id, "result": {} })).unwrap();
                    for delta in ["o", "k"] {
                        reply.send(json!({ "method": "item/agentMessage/delta", "params": { "threadId": "thr-cli", "delta": delta } })).unwrap();
                    }
                    reply.send(json!({ "method": "turn/completed", "params": { "threadId": "thr-cli", "turn": {} } })).unwrap();
                    return seen;
                }
                _ => {}
            }
        }
    });
    let answer = ling_web_server::ask_client::ask(&server.config.state_dir, port, "Say ok", Duration::from_secs(10)).await.unwrap();
    assert_eq!(answer, "ok");
    let seen = seen.await.unwrap();
    let start = seen.iter().find(|frame| frame["method"] == "thread/start").unwrap();
    assert!(start["params"]["baseInstructions"].as_str().unwrap().starts_with("COMPOSED:ask:"));
    assert_eq!(seen.iter().find(|frame| frame["method"] == "turn/start").unwrap()["params"]["input"][0]["text"], "Say ok");
    assert!(seen.iter().any(|frame| frame["method"] == "initialized"));
    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}

#[tokio::test]
async fn stored_images_are_served_by_their_names_and_nothing_else() {
    let scratch = scratch("images");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let store = server.config.images_dir.clone();
    std::fs::create_dir_all(&store).unwrap();
    std::fs::write(store.join("0123456789abcdef.jpg"), b"\xff\xd8\xffjpeg").unwrap();
    std::fs::write(scratch.dir.join("secret.jpg"), b"outside").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(scratch.dir.join("secret.jpg"), store.join("fedcba9876543210.jpg")).unwrap();
    let cookie = sign_in(&server).await;
    let get = |uri: &str| request("GET", uri).header(header::COOKIE, &cookie).body(Body::empty()).unwrap();
    let (status, headers, body) = call(&server, get("/images/0123456789abcdef.jpg")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers[header::CONTENT_TYPE], "image/jpeg");
    assert!(headers[header::CACHE_CONTROL].to_str().unwrap().contains("immutable"));
    assert!(body.ends_with("jpeg"));
    for uri in ["/images/fedcba9876543210.jpg", "/images/../secret.jpg", "/images/%2e%2e%2fsecret.jpg", "/images/0123456789abcdef.png", "/images/aaaaaaaaaaaaaaaa.jpg"] {
        assert_eq!(call(&server, get(uri)).await.0, StatusCode::NOT_FOUND, "{uri}");
    }
}

#[tokio::test]
async fn a_recording_is_passed_to_speech_to_text_and_the_text_comes_back() {
    let scratch = scratch("speech");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut settings = config(&scratch.dir, 3100, Vec::new());
    settings.speech_addr = listener.local_addr().unwrap().to_string();
    let server = Server::new(settings).unwrap();
    // The stand-in service: reads the whole request, checks the form, answers with text.
    let service = tokio::spawn(async move {
        use tokio::io::AsyncReadExt;
        use tokio::io::AsyncWriteExt;
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut raw = Vec::new();
        let mut buffer = [0u8; 4096];
        loop {
            let read = stream.read(&mut buffer).await.unwrap();
            raw.extend_from_slice(&buffer[..read]);
            if read == 0 || raw.ends_with(b"--\r\n") {
                break;
            }
        }
        stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 22\r\n\r\n{\"text\":\" Hello Ask \"}").await.unwrap();
        String::from_utf8_lossy(&raw).into_owned()
    });
    let cookie = sign_in(&server).await;
    let post = |content_type: &str, body: &'static [u8]| {
        request("POST", "/api/transcribe")
            .header(header::COOKIE, &cookie)
            .header(header::ORIGIN, ORIGIN)
            .header(header::CONTENT_TYPE, content_type)
            .body(Body::from(body))
            .unwrap()
    };
    let (status, _, body) = call(&server, post("audio/webm;codecs=opus", b"OPUSDATA")).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["text"], "Hello Ask");
    let sent = service.await.unwrap();
    assert!(sent.starts_with("POST /v1/audio/transcriptions HTTP/1.1\r\n"), "{sent}");
    assert!(sent.contains("Systran/faster-whisper-small") && sent.contains("filename=\"speech.webm\"") && sent.contains("OPUSDATA"));
    // Not audio, empty, or no service: refused, each saying why.
    assert_eq!(call(&server, post("text/plain", b"hi")).await.0, StatusCode::UNSUPPORTED_MEDIA_TYPE);
    assert_eq!(call(&server, post("audio/webm", b"")).await.0, StatusCode::BAD_REQUEST);
    let (status, _, body) = call(&server, post("audio/webm", b"again")).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{body}");
    assert!(body.contains("ling-admin voice start"));
}
