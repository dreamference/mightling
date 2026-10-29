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
        "#!/bin/sh\nif [ \"$1\" = prompt ] && [ \"$2\" = show ] && [ \"$4\" = --composed ]; then printf 'COMPOSED:%s:%s' \"$3\" \"$(pwd)\"; exit 0; fi\n\
         d=$(dirname \"$0\")/docs\n\
         if [ \"$1\" = docs ] && [ \"$2\" = locate ] && [ \"$4\" = --json ]; then case \"$3\" in\n\
         documents/1) printf '{\"path\":\"%s/report.pdf\",\"rel\":\"report.pdf\",\"kind\":\"pdf\",\"size\":9}' \"$d\" ;;\n\
         documents/2) printf '{\"path\":\"%s/notes.md\",\"rel\":\"sub/notes.md\",\"kind\":\"markdown\",\"size\":7}' \"$d\" ;;\n\
         documents/3) printf '{\"path\":\"%s/page.html\",\"rel\":\"page.html\",\"kind\":\"html\",\"size\":30}' \"$d\" ;;\n\
         *) echo 'ling-docs: no document' >&2; exit 1 ;; esac; exit 0; fi\n\
         if [ \"$1\" = docs ] && [ \"$2\" = status ]; then printf '{\"collections\":[{\"name\":\"documents\"}]}'; exit 0; fi\n\
         if [ \"$1\" = docs ]; then echo \"$2 $3\" >> \"$(dirname \"$0\")/docs-calls\"; if [ \"$3\" = /home/x/.ssh ]; then echo 'ling-docs: never indexed' >&2; exit 1; fi; echo done; exit 0; fi\n\
         echo \"unexpected: $*\" >&2\nexit 7\n",
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
        // Never the real ~/.config/dreamference/config.toml: a scratch one, absent unless a test writes it.
        user_config: Some(dir.join("user/.config/dreamference/config.toml")),
        forge_url: None,
        state_dir,
        port,
        lan_names,
        public_names: Vec::new(),
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
    ("GET", "/forge/"),
    ("GET", "/forge"),
    ("POST", "/forge/user/logout"),
    ("GET", "/healthz"),
    ("POST", "/api/upload?thread=t&kind=file"),
    ("POST", "/api/transcribe"),
    ("GET", "/api/apps"),
    ("GET", "/api/airgapped"),
    ("GET", "/api/chat/settings"),
    ("GET", "/api/ask/files?thread=t"),
    ("GET", "/api/night/repos"),
    ("POST", "/api/night/add"),
    ("GET", "/api/night/status"),
    ("GET", "/api/night/reports"),
    ("GET", "/api/ask/file?thread=t&name=a"),
    ("GET", "/images/0123456789abcdef.jpg"),
    ("GET", "/docs/documents/1"),
    ("GET", "/api/docs"),
    ("POST", "/api/docs/add"),
    ("POST", "/api/telegram/check"),
    ("GET", "/api/telegram/pending-pairs"),
    ("POST", "/api/telegram/pending-pairs/approve"),
    ("POST", "/api/telegram/pending-pairs/decline"),
    ("GET", "/devices"),
    ("POST", "/devices/pair"),
    ("POST", "/devices/revoke"),
    ("POST", "/devices/scope"),
    ("GET", "/api/sync/status"),
    ("GET", "/api/sync/threads"),
    ("GET", "/api/sync/manifest"),
    ("PUT", "/api/sync/rollouts/01a0f1ae-2da4-7942-99ae-c3151dc137cc?offset=0"),
    ("POST", "/api/sync/threads/01a0f1ae-2da4-7942-99ae-c3151dc137cc/delete"),
    ("GET", "/api/repos"),
    ("POST", "/api/repos/open"),
    ("GET", "/api/repos/state?thread=01234567"),
    ("GET", "/api/repos/diff?thread=01234567&scope=uncommitted"),
    ("GET", "/api/repos/log?thread=01234567"),
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
    let lan = Request::builder().uri("/healthz").header(header::HOST, "192.168.50.105:3100").header(header::COOKIE, &cookie);
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
    let server = Server::new(config(&scratch.dir, 3100, vec!["192.168.50.105".to_string()])).unwrap();
    let lan = Request::builder().uri("/healthz").header(header::HOST, "192.168.50.105:3100");
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
    let lan = vec!["172.17.0.1".to_string(), "192.168.50.105".to_string()];
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
    assert!(page.contains("<svg") && page.contains("This code is for 192.168.50.105."));
    let token = std::fs::read_to_string(server.config.state_dir.join("token")).unwrap();
    assert!(!page.contains(token.trim()) && !page.contains(owner.split('=').nth(1).unwrap()));
    // Changing something still needs the server's own origin.
    let foreign = request("POST", "/devices/pair").header(header::ORIGIN, "http://evil.example").header(header::COOKIE, &owner);
    assert_eq!(call(&server, foreign.body(Body::empty()).unwrap()).await.0, StatusCode::FORBIDDEN);

    // The QR code's link opens the pairing form with the code filled in, and pairs nothing by itself.
    let link = request("GET", &format!("/pair?code={code}")).header(header::HOST, "192.168.50.105:3100");
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

/// An attachment the disk took only part of is an error, not a saved file: run in a child process
/// of this test binary whose files may not grow past 1 MiB, as a full disk refuses a write.
#[test]
fn an_upload_the_disk_could_not_hold_is_refused_and_removed() {
    let child = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "an_upload_into_a_full_disk", "--ignored", "--nocapture"])
        .env("LW_UPLOAD_FULL_DISK", "1")
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&child.stdout);
    assert!(child.status.success() && out.contains("1 passed"), "{out}{}", String::from_utf8_lossy(&child.stderr));
}

#[tokio::test]
#[ignore = "run by an_upload_the_disk_could_not_hold_is_refused_and_removed"]
async fn an_upload_into_a_full_disk() {
    if std::env::var_os("LW_UPLOAD_FULL_DISK").is_none() {
        return;
    }
    let scratch = scratch("upload-full");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let root = server.ask_root();
    let folder = ling_web_server::ask::new_folder(&root).unwrap();
    ling_web_server::ask::link_thread(&root, "thr-1", &folder).unwrap();
    // SAFETY: this process runs this one test; past the limit a write fails with EFBIG.
    unsafe {
        libc::signal(libc::SIGXFSZ, libc::SIG_IGN);
        let limit = libc::rlimit { rlim_cur: 1 << 20, rlim_max: libc::RLIM_INFINITY };
        assert_eq!(libc::setrlimit(libc::RLIMIT_FSIZE, &limit), 0);
    }
    let upload = request("POST", "/api/upload?thread=thr-1&name=big.bin&kind=file")
        .header(header::ORIGIN, ORIGIN)
        .header(header::COOKIE, &cookie)
        .body(Body::from(vec![0u8; 2 << 20]))
        .unwrap();
    let (status, _, body) = call(&server, upload).await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{body}");
    assert!(!folder.join("big.bin").exists());
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

/// A phone bridge's image (`read=1`): its text is read by the offline reader, here a stand-in with
/// Tesseract's command line, and the answer says whether the served model takes images, as the
/// launcher's catalog records it; a plain upload's answer is unchanged.
#[tokio::test]
async fn a_bridge_upload_reads_an_images_text_and_says_whether_the_model_takes_images() {
    let scratch = scratch("ocr");
    let reader = scratch.dir.join("tesseract");
    std::fs::write(
        &reader,
        "#!/bin/sh\nif [ \"$1\" = --list-langs ]; then printf 'List of available languages (2):\\neng\\nosd\\n'; exit 0; fi\n[ \"$2\" = stdout ] && [ \"$3\" = -l ] && [ \"$4\" = eng ] || exit 3\nprintf 'Air purifier   HEPA 13\\n\\n\\n42 dB night mode\\n'\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&reader, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    // SAFETY: no other test in this binary reads this variable.
    unsafe { std::env::set_var(ling_web_server::ocr::OCR_ENV, &reader) };
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let root = server.ask_root();
    let folder = ling_web_server::ask::new_folder(&root).unwrap();
    ling_web_server::ask::link_thread(&root, "thr-1", &folder).unwrap();
    let upload = |uri: &str| request("POST", uri).header(header::ORIGIN, ORIGIN).header(header::COOKIE, &cookie).body(Body::from(b"\xff\xd8jpeg".to_vec())).unwrap();
    let (status, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=shot.jpg&kind=image&read=1")).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let answer: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(answer["text"], json!("Air purifier HEPA 13\n\n42 dB night mode"));
    assert_eq!(answer["vision"], json!(false), "no catalog: not taken as a yes");
    std::fs::write(server.config.codex_home.join("model_catalog.json"), r#"{"models":[{"slug":"m","input_modalities":["text","image"]}]}"#).unwrap();
    let (_, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=shot.jpg&kind=image&read=1")).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["vision"], json!(true));
    // A file is never read; a plain upload's answer carries neither field.
    let (_, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=a.pdf&kind=file&read=1")).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["text"], Value::Null);
    let (_, _, body) = call(&server, upload("/api/upload?thread=thr-1&name=shot.jpg&kind=image")).await;
    let plain: Value = serde_json::from_str(&body).unwrap();
    assert!(plain.get("text").is_none() && plain.get("vision").is_none(), "{plain}");
}

/// Files back to a phone: the thread's changed files are listed and one is given, only from its
/// folder, and never at a user-level `/airgapped on`.
#[tokio::test]
async fn a_threads_files_are_listed_and_given_and_never_at_air_gap_on() {
    let scratch = scratch("files");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let root = server.ask_root();
    let folder = ling_web_server::ask::new_folder(&root).unwrap();
    ling_web_server::ask::link_thread(&root, "thr-1", &folder).unwrap();
    std::fs::write(folder.join("report.md"), "# Purifiers").unwrap();
    // SAFETY: the other tests here accept either level.
    unsafe { std::env::set_var(ling_airgapped::ENV_VAR, "off") };
    let get = |uri: &str| request("GET", uri).header(header::COOKIE, &cookie).body(Body::empty()).unwrap();
    let (status, _, body) = call(&server, get("/api/ask/files?thread=thr-1&since=0")).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["files"][0]["name"], "report.md");
    let (status, _, body) = call(&server, get("/api/ask/file?thread=thr-1&name=report.md")).await;
    assert_eq!((status, body.as_str()), (StatusCode::OK, "# Purifiers"));
    assert_eq!(call(&server, get("/api/ask/file?thread=thr-1&name=../../etc/passwd")).await.0, StatusCode::NOT_FOUND);
    assert_eq!(call(&server, get("/api/ask/file?thread=thr-other&name=report.md")).await.0, StatusCode::NOT_FOUND);
    // SAFETY: as above.
    unsafe { std::env::set_var(ling_airgapped::ENV_VAR, "on") };
    let (status, _, body) = call(&server, get("/api/ask/file?thread=thr-1&name=report.md")).await;
    unsafe { std::env::remove_var(ling_airgapped::ENV_VAR) };
    assert_eq!(status, StatusCode::FORBIDDEN, "{body}");
}

/// Night Shift from a phone: the repositories a task may go to (a checkout already queued in),
/// queueing through `ling night add` there and nowhere else, the queue, and the reports after a name.
#[tokio::test]
async fn night_shift_is_queued_and_read_for_a_phone_bridge() {
    let scratch = scratch("night");
    let mut config = config(&scratch.dir, 3100, Vec::new());
    let repo = scratch.dir.join("app");
    std::fs::create_dir_all(repo.join(".git")).unwrap();
    let ling = scratch.dir.join("ling-night");
    std::fs::write(&ling, "#!/bin/sh\n[ \"$1 $2\" = \"night add\" ] || exit 9\necho \"Queued 20261021-1300-abc on branch night/20261021-1300-abc, from 0123456789.\"\necho \"cwd $(pwd) task $3\"\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&ling, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    config.launch.ling = ling;
    let night = config.codex_home.join("night");
    std::fs::create_dir_all(night.join("tasks")).unwrap();
    std::fs::create_dir_all(night.join("reports")).unwrap();
    std::fs::write(night.join("tasks/old.json"), json!({ "id": "old", "repo": repo.canonicalize().unwrap(), "status": "queued", "task": "Earlier task" }).to_string()).unwrap();
    std::fs::write(night.join("reports/2026-10-21.md"), "# Night Shift, 2026-10-21\n\n## app\n\n- old: done").unwrap();
    let server = Server::new(config).unwrap();
    let cookie = sign_in(&server).await;
    let get = |uri: &str| request("GET", uri).header(header::COOKIE, &cookie).body(Body::empty()).unwrap();
    let post = |body: Value| request("POST", "/api/night/add").header(header::ORIGIN, ORIGIN).header(header::COOKIE, &cookie).body(Body::from(body.to_string())).unwrap();
    let (_, _, body) = call(&server, get("/api/night/repos")).await;
    let repos: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(repos["repos"][0]["name"], "app", "{body}");
    let path = repos["repos"][0]["path"].as_str().unwrap().to_string();
    let (status, _, body) = call(&server, post(json!({ "repo": path, "task": "Fix the parser" }))).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let added: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(added["queued"], true, "{body}");
    assert!(added["lines"][1].as_str().unwrap().ends_with("task Fix the parser"), "{body}");
    // Anywhere else is refused.
    assert_eq!(call(&server, post(json!({ "repo": "/etc", "task": "x" }))).await.0, StatusCode::FORBIDDEN);
    let (_, _, body) = call(&server, get("/api/night/status")).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["tasks"][0]["task"], "Earlier task");
    let (_, _, body) = call(&server, get("/api/night/reports?after=2026-10-20.md")).await;
    let reports: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(reports["latest"], "2026-10-21.md");
    assert!(reports["reports"][0]["text"].as_str().unwrap().contains("old: done"));
    let (_, _, body) = call(&server, get("/api/night/reports?after=2026-10-21.md")).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["reports"], json!([]));
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
async fn a_watch_device_reads_and_follows_threads_and_changes_nothing() {
    let scratch = scratch("ws-watch");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&scratch.dir, port, Vec::new());
    let mut app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));

    let (_, token) = auth::add_device(&server.config.state_dir, "phone").unwrap();
    assert_eq!(auth::set_scope(&server.config.state_dir, "phone", auth::Scope::Watch).unwrap().len(), 1);
    let cookie = format!("{}={token}", auth::DEVICE_COOKIE);

    // The routes that start or feed work answer 403 (SYNC §5.1, O4).
    let folder = scratch.dir.display().to_string();
    for (uri, body) in [
        ("/api/night/add", json!({ "repo": folder, "task": "Fix the parser" })),
        ("/api/docs/add", json!({ "collection": "notes", "folder": folder })),
        ("/api/upload?kind=file&name=a.txt", json!("text")),
    ] {
        let post = Request::builder()
            .method("POST")
            .uri(uri)
            .header(header::HOST, format!("127.0.0.1:{port}"))
            .header(header::ORIGIN, format!("http://127.0.0.1:{port}"))
            .header(header::COOKIE, &cookie)
            .body(Body::from(body.to_string()))
            .unwrap();
        assert_eq!(call(&server, post).await.0, StatusCode::FORBIDDEN, "{uri}");
    }

    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    upgrade.headers_mut().insert(header::COOKIE, cookie.parse().unwrap());
    let (websocket, _) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap();
    let (sink, stream) = websocket.split();
    let mut browser = Browser { sink, stream, next: 0 };
    let started = browser.call(json!({ "type": "work/start" })).await;
    assert!(started.get("error").is_none(), "{started}");

    // Reading passes; following a thread keeps only the thread, not the page's model or sandbox.
    let listed = browser.call(json!({ "type": "work/send", "message": { "id": 1, "method": "thread/list", "params": {} } })).await;
    assert_eq!(listed["result"], Value::Null, "{listed}");
    assert_eq!(next_frame(&mut app_server).await.0["method"], "thread/list");
    let resume = json!({ "type": "work/send", "message": { "id": 2, "method": "thread/resume", "params": {
        "threadId": "thr-1", "model": "other", "sandbox": "danger-full-access", "approvalPolicy": "never" } } });
    let resumed = browser.call(resume).await;
    assert_eq!(resumed["result"], Value::Null, "{resumed}");
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["params"]["threadId"], "thr-1", "{frame}");
    for field in ["model", "sandbox", "approvalPolicy"] {
        assert!(frame["params"].get(field).is_none(), "{field}: {frame}");
    }

    // Everything that starts, steers or changes work is refused and never reaches the server,
    // and so is an answer to the server's approval request.
    for (id, method) in ["turn/start", "turn/steer", "thread/start", "thread/fork", "command/exec", "thread/shellCommand", "config/value/write", "fs/readFile"]
        .into_iter()
        .enumerate()
    {
        let message = json!({ "id": 10 + id, "method": method, "params": { "threadId": "thr-1" } });
        let refused = browser.call(json!({ "type": "work/send", "message": message })).await;
        assert!(refused["error"].as_str().is_some_and(|e| e.contains("watch")), "{method}: {refused}");
    }
    let approval = browser.call(json!({ "type": "work/send", "message": { "id": 0, "result": { "decision": "accept" } } })).await;
    assert!(approval["error"].as_str().is_some_and(|e| e.contains("watch")), "{approval}");
    assert!(tokio::time::timeout(Duration::from_millis(500), app_server.recv()).await.is_err());

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn a_closed_tab_lets_go_of_the_server() {
    let scratch = scratch("ws-closed");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&scratch.dir, port, Vec::new());
    let _app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));
    tokio::time::sleep(Duration::from_millis(200)).await;
    let before = Arc::strong_count(&server);

    let (_, token) = auth::add_device(&server.config.state_dir, "phone").unwrap();
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    upgrade.headers_mut().insert(header::COOKIE, format!("{}={token}", auth::DEVICE_COOKIE).parse().unwrap());
    let (websocket, _) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap();
    let (mut sink, stream) = websocket.split();
    sink.send(Message::Close(None)).await.unwrap();
    drop((sink, stream));

    // The tab's task ends with its socket: nothing of it is left holding the server.
    let mut left = Arc::strong_count(&server);
    for _ in 0..100 {
        if left == before {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
        left = Arc::strong_count(&server);
    }
    assert_eq!(left, before);

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn a_device_narrowed_or_revoked_with_its_tab_open_is_held_to_it_on_the_next_message() {
    let scratch = scratch("ws-revoke");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = config(&scratch.dir, port, Vec::new());
    let mut app_server = fake_app_server(&config.socket).await;
    let server = Server::new(config).unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let serving = tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));

    let state = server.config.state_dir.clone();
    let (_, token) = auth::add_device(&state, "phone").unwrap();
    let cookie = format!("{}={token}", auth::DEVICE_COOKIE);
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut upgrade = format!("ws://127.0.0.1:{port}/ws").into_client_request_with_origin(port);
    upgrade.headers_mut().insert(header::COOKIE, cookie.parse().unwrap());
    let (websocket, _) = tokio_tungstenite::client_async(upgrade, tcp).await.unwrap();
    let (sink, stream) = websocket.split();
    let mut browser = Browser { sink, stream, next: 0 };
    let started = browser.call(json!({ "type": "work/start" })).await;
    assert!(started.get("error").is_none(), "{started}");
    let turn = |id: u64| json!({ "type": "work/send", "message": { "id": id, "method": "turn/start", "params": { "threadId": "thr-1", "input": [] } } });
    let sent = browser.call(turn(1)).await;
    assert!(sent.get("error").is_none(), "{sent}");
    assert_eq!(next_frame(&mut app_server).await.0["method"], "turn/start");

    // Narrowed to watch: the open tab may no longer start work (SYNC §5.1, O4).
    auth::set_scope(&state, "phone", auth::Scope::Watch).unwrap();
    let refused = browser.call(turn(2)).await;
    assert!(refused["error"].as_str().is_some_and(|e| e.contains("watch")), "{refused}");

    // Revoked: the open tab is refused and closed, as its next request over HTTP would be.
    auth::set_scope(&state, "phone", auth::Scope::Control).unwrap();
    auth::revoke_device(&state, "phone").unwrap();
    let revoked = browser.call(turn(3)).await;
    assert!(revoked["error"].as_str().is_some_and(|e| e.contains("revoked")), "{revoked}");
    assert!(tokio::time::timeout(Duration::from_millis(500), app_server.recv()).await.is_err());
    let closed = tokio::time::timeout(Duration::from_secs(10), browser.stream.next()).await.unwrap();
    assert!(!matches!(closed, Some(Ok(Message::Text(_)))), "{closed:?}");

    let _ = stop.send(());
    serving.await.unwrap().unwrap();
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
async fn a_cited_document_is_served_by_its_id_and_never_runs_on_this_origin() {
    let scratch = scratch("docs");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let docs = scratch.dir.join("docs");
    std::fs::create_dir_all(&docs).unwrap();
    std::fs::write(docs.join("report.pdf"), b"%PDF-1.4\n").unwrap();
    std::fs::write(docs.join("notes.md"), b"# Notes").unwrap();
    std::fs::write(docs.join("page.html"), b"<script>steal()</script>hello").unwrap();
    let cookie = sign_in(&server).await;
    let get = |uri: &str| request("GET", uri).header(header::COOKIE, &cookie).body(Body::empty()).unwrap();
    let (status, headers, body) = call(&server, get("/docs/documents/1")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers[header::CONTENT_TYPE], "application/pdf");
    assert!(headers[header::CONTENT_DISPOSITION].to_str().unwrap().starts_with("inline"));
    assert!(!headers[header::CONTENT_SECURITY_POLICY].to_str().unwrap().contains("script-src"), "no scripts of this origin");
    assert!(body.starts_with("%PDF"));
    let (status, headers, body) = call(&server, get("/docs/documents/2")).await;
    assert_eq!((status, body.as_str()), (StatusCode::OK, "# Notes"));
    assert_eq!(headers[header::CONTENT_TYPE], "text/plain; charset=utf-8");
    assert!(headers[header::CONTENT_SECURITY_POLICY].to_str().unwrap().starts_with("sandbox"));
    assert!(headers[header::CONTENT_DISPOSITION].to_str().unwrap().contains("filename=\"notes.md\""));
    // A saved page is a download, never a page of this origin.
    let (status, headers, _) = call(&server, get("/docs/documents/3")).await;
    assert_eq!(status, StatusCode::OK);
    assert!(headers[header::CONTENT_DISPOSITION].to_str().unwrap().starts_with("attachment"));
    assert_eq!(headers[header::CONTENT_TYPE], "application/octet-stream");
    for uri in ["/docs/documents/9", "/docs/../1", "/docs/documents/1.pdf", "/docs/%2e%2e/1", "/docs/documents"] {
        assert_eq!(call(&server, get(uri)).await.0, StatusCode::NOT_FOUND, "{uri}");
    }
}

#[tokio::test]
async fn the_collections_are_managed_through_ling_docs_with_names_and_full_paths_only() {
    let scratch = scratch("docset");
    let server = Server::new(config(&scratch.dir, 3100, Vec::new())).unwrap();
    let cookie = sign_in(&server).await;
    let (status, _, body) = call(&server, request("GET", "/api/docs").header(header::COOKIE, &cookie).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["collections"][0]["name"], "documents");
    let post = |uri: &str, body: &str| request("POST", uri).header(header::COOKIE, &cookie).header(header::ORIGIN, ORIGIN).body(Body::from(body.to_string())).unwrap();
    assert_eq!(call(&server, post("/api/docs/add", r#"{"folder":"/home/x/Notes"}"#)).await.0, StatusCode::OK);
    let (status, _, body) = call(&server, post("/api/docs/add", r#"{"folder":"/home/x/.ssh"}"#)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert!(body.contains("never indexed"), "{body}");
    assert_eq!(call(&server, post("/api/docs/remove", r#"{"name":"notes"}"#)).await.0, StatusCode::OK);
    assert_eq!(call(&server, post("/api/docs/reindex", r#"{"name":"documents"}"#)).await.0, StatusCode::OK);
    for (uri, body) in [("/api/docs/add", r#"{"folder":"relative"}"#), ("/api/docs/remove", r#"{"name":"../x"}"#), ("/api/docs/add", "not json")] {
        assert_eq!(call(&server, post(uri, body)).await.0, StatusCode::BAD_REQUEST, "{uri} {body}");
    }
    assert_eq!(call(&server, post("/api/docs/delete-everything", "{}")).await.0, StatusCode::NOT_FOUND);
    // From another site, nothing changes.
    let foreign = request("POST", "/api/docs/remove").header(header::COOKIE, &cookie).header(header::ORIGIN, "http://evil.example").body(Body::from(r#"{"name":"documents"}"#)).unwrap();
    assert_eq!(call(&server, foreign).await.0, StatusCode::FORBIDDEN);
    let calls = std::fs::read_to_string(scratch.dir.join("docs-calls")).unwrap();
    assert_eq!(calls.lines().collect::<Vec<_>>(), ["add /home/x/Notes", "add /home/x/.ssh", "remove notes", "reindex documents"]);
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
    // Not audio, or empty: refused, each saying why.
    assert_eq!(call(&server, post("text/plain", b"hi")).await.0, StatusCode::UNSUPPORTED_MEDIA_TYPE);
    assert_eq!(call(&server, post("audio/webm", b"")).await.0, StatusCode::BAD_REQUEST);
    // No service: an address held bound but not listening, so it refuses and no other test can
    // take it. The stand-in's own port, once dropped, was sometimes bound by a test running beside
    // this one, which then answered (about 1 run in 4 of the whole file).
    let closed = tokio::net::TcpSocket::new_v4().unwrap();
    closed.bind("127.0.0.1:0".parse().unwrap()).unwrap();
    let mut settings = config(&scratch.dir, 3100, Vec::new());
    settings.speech_addr = closed.local_addr().unwrap().to_string();
    let no_service = Server::new(settings).unwrap();
    let cookie = sign_in(&no_service).await;
    let again = request("POST", "/api/transcribe")
        .header(header::COOKIE, &cookie)
        .header(header::ORIGIN, ORIGIN)
        .header(header::CONTENT_TYPE, "audio/webm")
        .body(Body::from(&b"again"[..]))
        .unwrap();
    let (status, _, body) = call(&no_service, again).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{body}");
    assert!(body.contains("ling-admin voice start"));
    drop(closed);
}

/// Telegram's launch parameters for `user`, signed with `token` the way Telegram signs them.
fn telegram_launch(token: &str, user: i64, auth_date: u64) -> String {
    use ling_web_server::telegram_app::hmac_sha256;
    let user_json = format!("{{\"id\":{user}}}");
    let check = format!("auth_date={auth_date}\nuser={user_json}");
    let secret = hmac_sha256(b"WebAppData", token.as_bytes());
    let hash: String = hmac_sha256(&secret, check.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
    format!("auth_date={auth_date}&user=%7B%22id%22%3A{user}%7D&hash={hash}")
}

/// The Mini App (TELEGRAM_MOONSHOT §15): its HTTPS front's name is served as given, and the launch
/// check answers whether the page was opened from this node's bot by a paired user, behind the
/// credential like every route.
#[tokio::test]
async fn a_mini_app_launch_is_checked_against_ling_chats_bot_behind_the_credential() {
    let scratch = scratch("tgapp");
    let mut config = config(&scratch.dir, 3100, Vec::new());
    config.public_names = vec!["node.example.ts.net".to_string()];
    let chat = config.codex_home.join("chat");
    std::fs::create_dir_all(&chat).unwrap();
    std::fs::write(chat.join("telegram.json"), r#"{"token":"7:tok","consented":1,"users":[42]}"#).unwrap();
    let server = Server::new(config).unwrap();
    let cookie = sign_in(&server).await;
    let check = |init_data: String, host: &'static str, origin: &'static str| {
        Request::builder()
            .method("POST")
            .uri("/api/telegram/check")
            .header(header::HOST, host)
            .header(header::ORIGIN, origin)
            .header(header::COOKIE, &cookie)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(json!({ "initData": init_data }).to_string()))
            .unwrap()
    };
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    let front = ("node.example.ts.net", "https://node.example.ts.net");
    let (status, _, body) = call(&server, check(telegram_launch("7:tok", 42, now), front.0, front.1)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["ok"], true);
    for (init_data, why) in [
        (telegram_launch("7:tok", 43, now), "not paired"),
        (telegram_launch("8:other", 42, now), "this Mightling's bot"),
        (telegram_launch("7:tok", 42, now - 3 * 24 * 3600), "too old"),
        (String::new(), "this Mightling's bot"),
    ] {
        let (_, _, body) = call(&server, check(init_data, front.0, front.1)).await;
        let answer = serde_json::from_str::<Value>(&body).unwrap();
        assert_eq!(answer["ok"], false);
        assert!(answer["reason"].as_str().unwrap().contains(why), "{answer}");
    }
    // Another name is still refused, and the front's name gets no more than loopback.
    let (status, _, _) = call(&server, check(telegram_launch("7:tok", 42, now), "evil.example", "https://evil.example")).await;
    assert_eq!(status, StatusCode::MISDIRECTED_REQUEST);
    let bare = Request::builder().uri("/healthz").header(header::HOST, front.0);
    assert_eq!(call(&server, bare.body(Body::empty()).unwrap()).await.0, StatusCode::UNAUTHORIZED);
}

/// First launch of the Mini App with no credential (CHAT_REPOS §12.1 G2): requires both Telegram's
/// signed launch data for a paired user and the configured HTTPS front (`Host` and `Origin`), waits
/// for the bot's approval, issues a `Browser` device labelled `telegram-app` with `control` scope,
/// hands out the device cookie once, and can be revoked like any device.
#[tokio::test]
async fn discords_front_is_a_public_name_but_never_serves_telegrams_launch_pairing() {
    // DISCORD §3.11: Discord's launch carries nothing signed, so its front gets the deep links and
    // the device sign-in, never `/bridge.js` or Telegram's launch pairing.
    let scratch = scratch("dcfront");
    let mut config = config(&scratch.dir, 3100, vec!["[addr]".to_string()]);
    config.public_names = vec!["dc.example.ts.net".to_string()];
    let user_config = config.user_config.clone().unwrap();
    std::fs::create_dir_all(user_config.parent().unwrap()).unwrap();
    std::fs::write(&user_config, "[chat]\ndiscord_web_url = \"https://dc.example.ts.net/\"\n").unwrap();
    let server = Server::new(config).unwrap();
    assert!(server.public_host(Some("dc.example.ts.net")));
    let (status, _, locked) = call(
        &server,
        Request::builder().uri("/").header(header::HOST, "DC.example.ts.net").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(!locked.contains("/bridge.js"), "{locked}");
    let (status, _, _) = call(
        &server,
        Request::builder().uri("/bridge.js").header(header::HOST, "dc.example.ts.net").body(Body::empty()).unwrap(),
    )
    .await;
    assert_ne!(status, StatusCode::OK);
}

#[tokio::test]
async fn git_through_the_front_is_not_passed_on_whatever_host_the_front_forwards() {
    let scratch = scratch("gitfront");
    // A stand-in forge that answers every connection, never the machine's own.
    let forge = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let forge_url = format!("http://{}", forge.local_addr().unwrap());
    tokio::spawn(async move {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        while let Ok((mut stream, _)) = forge.accept().await {
            let mut buffer = [0u8; 4096];
            let _ = stream.read(&mut buffer).await;
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nfake forge").await;
        }
    });
    let mut config = config(&scratch.dir, 3100, Vec::new());
    config.public_names = vec!["node.example.ts.net".to_string()];
    config.forge_url = Some(forge_url);
    let server = Server::new(config).unwrap();
    let git = "/forge/o/r.git/info/refs?service=git-upload-pack";
    // `tailscale serve` passes the client's own Host on and writes the forwarding headers itself.
    let forwarded = request("GET", git).header("x-forwarded-for", "192.0.2.9").header("x-forwarded-host", "node.example.ts.net").body(Body::empty()).unwrap();
    let (status, _, body) = call(&server, forwarded).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "{body}");
    let front = Request::builder().method("GET").uri(git).header(header::HOST, "node.example.ts.net").body(Body::empty()).unwrap();
    assert_eq!(call(&server, front).await.0, StatusCode::UNAUTHORIZED);
    // On the LAN and loopback it is passed on to the forge (none here) without a sign-in.
    let (status, _, body) = call(&server, request("GET", git).body(Body::empty()).unwrap()).await;
    assert_eq!((status, body.as_str()), (StatusCode::OK, "fake forge"));
}

#[tokio::test]
async fn a_mini_app_first_launch_pairs_only_on_the_front_and_only_after_the_bot_approves() {
    let scratch = scratch("tgpair");
    let mut config = config(&scratch.dir, 3100, vec!["192.168.50.105".to_string()]);
    config.public_names = vec!["node.example.ts.net".to_string()];
    let chat = config.codex_home.join("chat");
    std::fs::create_dir_all(&chat).unwrap();
    std::fs::write(chat.join("telegram.json"), r#"{"token":"7:tok","consented":1,"users":[42]}"#).unwrap();
    let server = Server::new(config).unwrap();
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    let front_host = "node.example.ts.net";
    let front_origin = "https://node.example.ts.net";

    let post_pair = |init_data: &str, host: &str, origin: &str| {
        Request::builder()
            .method("POST")
            .uri("/api/telegram/launch-pair")
            .header(header::HOST, host)
            .header(header::ORIGIN, origin)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(json!({ "initData": init_data }).to_string()))
            .unwrap()
    };
    let poll_pair = |pending: &str, init_data: &str, host: &str| {
        Request::builder()
            .method("GET")
            .uri(format!("/api/telegram/launch-pair?pending={pending}"))
            .header(header::HOST, host)
            .header(ling_web_server::telegram_app::INIT_DATA_HEADER, init_data)
            .body(Body::empty())
            .unwrap()
    };

    let valid_init = telegram_launch("7:tok", 42, now);

    // A host other than the configured front (loopback, LAN, or an unknown name) is refused with 421.
    for (host, origin) in [
        ("127.0.0.1:3100", "http://127.0.0.1:3100"),
        ("192.168.50.105:3100", "http://192.168.50.105:3100"),
        ("evil.example", "https://evil.example"),
    ] {
        let (status, _, _) = call(&server, post_pair(&valid_init, host, origin)).await;
        assert_eq!(status, StatusCode::MISDIRECTED_REQUEST, "host {host} must be refused");
    }
    // A mismatched Origin on the front host is refused with 403.
    let (status, _, _) = call(&server, post_pair(&valid_init, front_host, "http://127.0.0.1:3100")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    // Refused for an unpaired Telegram user, a stale launch, or a forged signature.
    for (bad_init, why) in [
        (telegram_launch("7:tok", 43, now), "not paired"),
        (telegram_launch("7:tok", 42, now - 2 * 24 * 3600), "too old"),
        (telegram_launch("8:forged", 42, now), "this Mightling's bot"),
        (String::new(), "this Mightling's bot"),
    ] {
        let (status, headers, body) = call(&server, post_pair(&bad_init, front_host, front_origin)).await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{body}");
        assert!(headers.get(header::SET_COOKIE).is_none());
        let answer: Value = serde_json::from_str(&body).unwrap();
        assert_eq!(answer["ok"], false);
        assert!(answer["reason"].as_str().unwrap().contains(why), "{answer}");
    }

    // Valid first launch on the front creates a pending request and sets no cookie yet.
    let (status, headers, body) = call(&server, post_pair(&valid_init, front_host, front_origin)).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(headers.get(header::SET_COOKIE).is_none());
    let created: Value = serde_json::from_str(&body).unwrap();
    let pending_id = created["pending"].as_str().unwrap().to_string();
    let code = created["code"].as_str().unwrap().to_string();
    assert_eq!(code.len(), 7);

    // Without the bot's Approve, polling answers pending and hands out no cookie.
    let (status, headers, body) = call(&server, poll_pair(&pending_id, &valid_init, front_host)).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(headers.get(header::SET_COOKIE).is_none());
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["status"], "pending");

    // Polling from loopback or with a different launch's initData is refused.
    assert_eq!(
        call(&server, poll_pair(&pending_id, &valid_init, "127.0.0.1:3100")).await.0,
        StatusCode::MISDIRECTED_REQUEST
    );
    let other_init = telegram_launch("7:tok", 42, now.saturating_sub(5));
    assert_eq!(
        call(&server, poll_pair(&pending_id, &other_init, front_host)).await.0,
        StatusCode::FORBIDDEN
    );

    // Unauthenticated `/` includes `/bridge.js` only on the configured front host, never on loopback or LAN.
    let (status, _, locked_loopback) = call(&server, request("GET", "/").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(!locked_loopback.contains("/bridge.js"), "{locked_loopback}");
    let (status, _, locked_lan) = call(
        &server,
        Request::builder().uri("/").header(header::HOST, "192.168.50.105:3100").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(!locked_lan.contains("/bridge.js"), "{locked_lan}");
    let (status, _, locked_front) = call(
        &server,
        Request::builder().uri("/").header(header::HOST, front_host).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(locked_front.contains("/bridge.js"), "{locked_front}");

    // Only the owner session or a `bridge=1` session (`ling chat`) may list, approve or decline
    // pending requests; a normal paired device or a Signal bridge device (`signal-bridge`) may not.
    for forbidden_name in ["laptop", "signal-bridge", "signal-1"] {
        let (dev_id, dev_token) = auth::add_device(&server.config.state_dir, forbidden_name).unwrap();
        let dev_cookie = format!("{}={dev_token}", auth::DEVICE_COOKIE);
        let list_req = request("GET", "/api/telegram/pending-pairs")
            .header(header::COOKIE, &dev_cookie)
            .body(Body::empty())
            .unwrap();
        assert_eq!(call(&server, list_req).await.0, StatusCode::FORBIDDEN, "{forbidden_name} list");
        for uri in ["/api/telegram/pending-pairs/approve", "/api/telegram/pending-pairs/decline"] {
            let post_req = request("POST", uri)
                .header(header::ORIGIN, ORIGIN)
                .header(header::COOKIE, &dev_cookie)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({ "id": pending_id }).to_string()))
                .unwrap();
            assert_eq!(call(&server, post_req).await.0, StatusCode::FORBIDDEN, "{forbidden_name} {uri}");
        }
        auth::revoke_device(&server.config.state_dir, &dev_id).unwrap();
    }
    let list_as = |cookie: &str| {
        request("GET", "/api/telegram/pending-pairs")
            .header(header::COOKIE, cookie)
            .body(Body::empty())
            .unwrap()
    };

    // Sign in as `ling chat`'s bridge session (`bridge=1`).
    let login_code = auth::issue_login_code(&server.config.state_dir).unwrap();
    let (_, login_headers, _) = call(
        &server,
        request("GET", &format!("/login?code={login_code}&bridge=1")).body(Body::empty()).unwrap(),
    )
    .await;
    let bridge_cookie = login_headers[header::SET_COOKIE].to_str().unwrap().split(';').next().unwrap().to_string();

    let (status, _, body) = call(&server, list_as(&bridge_cookie)).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let listed: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(listed["pending"].as_array().unwrap().len(), 1);
    assert_eq!(listed["pending"][0]["id"], pending_id);
    assert_eq!(listed["pending"][0]["code"], code);
    assert_eq!(listed["pending"][0]["user"], 42);

    // Approve via the bridge session: no device is minted on disk before the webview claims it.
    let approve_req = request("POST", "/api/telegram/pending-pairs/approve")
        .header(header::ORIGIN, ORIGIN)
        .header(header::COOKIE, &bridge_cookie)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(json!({ "id": pending_id }).to_string()))
        .unwrap();
    let (status, _, body) = call(&server, approve_req).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(auth::load_devices(&server.config.state_dir).is_empty(), "device minted only on claim");

    // The launching webview collects the device cookie exactly once.
    let (status, headers, body) = call(&server, poll_pair(&pending_id, &valid_init, front_host)).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["ok"], true);
    let set_cookie = headers[header::SET_COOKIE].to_str().unwrap().to_string();
    assert!(
        set_cookie.starts_with("mightling_device=")
            && set_cookie.contains("HttpOnly")
            && set_cookie.contains("SameSite=Strict")
            && set_cookie.contains("Max-Age="),
        "{set_cookie}"
    );
    let device_cookie = set_cookie.split(';').next().unwrap().to_string();

    // A second poll gets 404 and no Set-Cookie, and the same initData cannot pair a second device.
    let (status, headers, _) = call(&server, poll_pair(&pending_id, &valid_init, front_host)).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(headers.get(header::SET_COOKIE).is_none());
    let (status, _, _) = call(&server, post_pair(&valid_init, front_host, front_origin)).await;
    assert_eq!(status, StatusCode::FORBIDDEN, "claimed initData must not pair twice");

    // The approved device is listed as `telegram-app` with Browser kind and Control scope.
    let devices = auth::load_devices(&server.config.state_dir);
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].name, "telegram-app");
    assert_eq!(devices[0].kind, auth::DeviceKind::Browser);
    assert_eq!(devices[0].scope, auth::Scope::Control);

    // With the device cookie, the normal launch check passes.
    let check_req = Request::builder()
        .method("POST")
        .uri("/api/telegram/check")
        .header(header::HOST, front_host)
        .header(header::ORIGIN, front_origin)
        .header(header::COOKIE, &device_cookie)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(json!({ "initData": valid_init }).to_string()))
        .unwrap();
    let (status, _, body) = call(&server, check_req).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["ok"], true);

    // Revoking the device on `/devices` (or `ling web revoke`) immediately locks it out.
    let owner = sign_in(&server).await;
    let (status, _, _) = call(&server, devices_post("/devices/revoke", &owner, &format!("device={}", devices[0].id))).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    assert!(auth::load_devices(&server.config.state_dir).is_empty());
    let after_revoke = Request::builder()
        .uri("/healthz")
        .header(header::HOST, front_host)
        .header(header::COOKIE, &device_cookie)
        .body(Body::empty())
        .unwrap();
    assert_eq!(call(&server, after_revoke).await.0, StatusCode::UNAUTHORIZED);
}
