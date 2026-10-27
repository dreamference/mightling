//! `/forge/` end to end against a stand-in forge on an ephemeral loopback port, never the node's
//! forge (port 3200): the sign-in with the owner's password file, the cookies kept in the server,
//! the scope check, git's protocol passed through with its own credentials, and the forge's pages'
//! headers.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::http::Request;
use axum::http::StatusCode;
use axum::http::header;
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::any;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use tower::ServiceExt;

const PASSWORD: &str = "correct horse&battery=staple";
const HOST: &str = "127.0.0.1:3100";
const ORIGIN: &str = "http://127.0.0.1:3100";

struct Scratch {
    dir: PathBuf,
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn scratch(tag: &str) -> Scratch {
    let dir = PathBuf::from("/tmp").join(format!("lwf-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Scratch { dir }
}

/// What the stand-in saw.
#[derive(Default)]
struct Seen {
    sign_ins: usize,
    requests: Vec<(String, String, Option<String>, Option<String>)>,
    /// Ends the stand-in's session at the next request, as an expiry would.
    expire: bool,
}

type Shared = Arc<Mutex<Seen>>;

const SESSION: &str = "s3cr3t";

/// A forge that answers like Forgejo where it matters: a form sign-in that sets `session` and
/// redirects away, pages that redirect to the sign-in page without that cookie, and git's paths
/// that answer whatever credentials they carry.
async fn stand_in(State(seen): State<Shared>, request: axum::extract::Request) -> Response {
    let (parts, body) = request.into_parts();
    let path = parts.uri.path().to_string();
    let cookie = parts.headers.get(header::COOKIE).and_then(|v| v.to_str().ok()).map(str::to_string);
    let authorization = parts.headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()).map(str::to_string);
    let body = axum::body::to_bytes(body, usize::MAX).await.unwrap();
    let mut seen = seen.lock().unwrap();
    seen.requests.push((parts.method.to_string(), parts.uri.to_string(), cookie.clone(), authorization.clone()));
    if path.ends_with("/info/refs") || path.ends_with("/git-receive-pack") {
        let answer = format!("git {} bytes={} auth={}", parts.method, body.len(), authorization.unwrap_or_default());
        return (StatusCode::OK, answer).into_response();
    }
    if path == "/user/login" && parts.method == "GET" {
        return (StatusCode::OK, [(header::SET_COOKIE, "lang=en-US; Path=/forge")], "<form><input name=\"user_name\"></form>").into_response();
    }
    if path == "/user/login" {
        let form = String::from_utf8_lossy(&body).to_string();
        let expected = format!("user_name=owner&password={}&remember=on", "correct%20horse%26battery%3Dstaple");
        if form == expected && cookie.as_deref().is_some_and(|c| c.contains("lang=en-US")) {
            seen.sign_ins += 1;
            seen.expire = false;
            return (
                StatusCode::SEE_OTHER,
                [(header::LOCATION, "/forge/"), (header::SET_COOKIE, &format!("session={SESSION}; Path=/forge; HttpOnly"))],
            )
                .into_response();
        }
        return (StatusCode::OK, "<form>wrong password</form>").into_response();
    }
    let signed_in = cookie.as_deref().is_some_and(|c| c.contains(&format!("session={SESSION}")));
    if !signed_in || seen.expire {
        seen.expire = false;
        return (StatusCode::SEE_OTHER, [(header::LOCATION, "http://gx10-1a2b.local:3100/forge/user/login?redirect_to=x")]).into_response();
    }
    if path == "/redirect" {
        return (StatusCode::SEE_OTHER, [(header::LOCATION, "http://gx10-1a2b.local:3100/forge/dreamference/mightling")]).into_response();
    }
    if path == "/raw" {
        return (StatusCode::OK, [(header::CONTENT_SECURITY_POLICY, "default-src 'none'; sandbox")], "raw").into_response();
    }
    if parts.method == "POST" {
        return (StatusCode::OK, format!("posted {} bytes", body.len())).into_response();
    }
    (
        StatusCode::OK,
        [(header::SET_COOKIE, "_csrf=abc; Path=/forge; HttpOnly"), (header::X_FRAME_OPTIONS, "SAMEORIGIN")],
        format!("page {path} {}", parts.uri.query().unwrap_or("")),
    )
        .into_response()
}

async fn start_stand_in() -> (String, Shared) {
    let seen: Shared = Arc::new(Mutex::new(Seen::default()));
    let app = Router::new().fallback(any(stand_in)).with_state(seen.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (format!("http://{address}"), seen)
}

fn config(dir: &Path, forge_url: &str, public_names: Vec<String>) -> Config {
    let codex_home = dir.join("home");
    let state_dir = auth::state_dir(&codex_home);
    Config {
        codex_home,
        socket: dir.join("run").join("s.sock"),
        launch: Launch { ling: dir.join("no-ling"), env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        user_config: Some(dir.join("user/.config/dreamference/config.toml")),
        forge_url: Some(forge_url.to_string()),
        state_dir,
        port: 3100,
        lan_names: Vec::new(),
        public_names,
    }
}

fn write_password(dir: &Path) {
    let folder = dir.join("user/.config/dreamference/forge");
    std::fs::create_dir_all(&folder).unwrap();
    std::fs::write(folder.join("owner-password"), format!("{PASSWORD}\n")).unwrap();
}

async fn call(server: &Arc<Server>, request: Request<Body>) -> (StatusCode, HeaderMap, String) {
    let response = server::router(server.clone()).oneshot(request).await.unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, headers, String::from_utf8_lossy(&body).into_owned())
}

fn device(server: &Arc<Server>) -> (String, String) {
    let (id, token) = auth::add_device(&server.config.state_dir, "phone").unwrap();
    (id, format!("{}={token}", auth::DEVICE_COOKIE))
}

fn get(uri: &str, cookie: &str) -> Request<Body> {
    Request::builder().uri(uri).header(header::HOST, HOST).header(header::COOKIE, cookie).body(Body::empty()).unwrap()
}

#[tokio::test]
async fn a_control_device_sees_the_forge_signed_in_and_never_gets_its_cookies() {
    let scratch = scratch("signed-in");
    let (url, seen) = start_stand_in().await;
    write_password(&scratch.dir);
    let server = Server::new(config(&scratch.dir, &url, Vec::new())).unwrap();
    let (_, cookie) = device(&server);

    let (status, headers, body) = call(&server, get("/forge/dreamference/mightling?tab=1", &format!("{cookie}; other=1"))).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body, "page /dreamference/mightling tab=1");
    // The forge's cookies stay in the server; its framing rule and a page policy reach the browser.
    assert!(headers.get(header::SET_COOKIE).is_none());
    assert_eq!(headers[header::X_FRAME_OPTIONS], "SAMEORIGIN");
    assert!(headers[header::CONTENT_SECURITY_POLICY].to_str().unwrap().contains("frame-ancestors 'self'"));
    {
        let seen = seen.lock().unwrap();
        assert_eq!(seen.sign_ins, 1);
        let (_, uri, sent_cookie, authorization) = seen.requests.last().unwrap().clone();
        assert_eq!(uri, "/dreamference/mightling?tab=1");
        // The forge gets its own cookies and never this server's device cookie or the browser's others.
        let sent_cookie = sent_cookie.unwrap();
        assert!(sent_cookie.contains(&format!("session={SESSION}")) && !sent_cookie.contains(auth::DEVICE_COOKIE) && !sent_cookie.contains("other=1"));
        assert!(authorization.is_none());
    }

    // A second request reuses the session, with the cookie the page set.
    let (status, _, _) = call(&server, get("/forge/", &cookie)).await;
    assert_eq!(status, StatusCode::OK);
    {
        let seen = seen.lock().unwrap();
        assert_eq!(seen.sign_ins, 1);
        assert!(seen.requests.last().unwrap().2.as_deref().unwrap().contains("_csrf=abc"));
    }

    // An expired session is signed in again and the page asked for once more.
    seen.lock().unwrap().expire = true;
    let (status, _, body) = call(&server, get("/forge/x", &cookie)).await;
    assert_eq!((status, body.as_str()), (StatusCode::OK, "page /x "));
    assert_eq!(seen.lock().unwrap().sign_ins, 2);

    // A redirect to the forge's own name comes back relative, so a front's name keeps working.
    let (status, headers, _) = call(&server, get("/forge/redirect", &cookie)).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    assert_eq!(headers[header::LOCATION], "/forge/dreamference/mightling");
    // The forge's own policy (raw files) is kept.
    let (_, headers, _) = call(&server, get("/forge/raw", &cookie)).await;
    assert_eq!(headers[header::CONTENT_SECURITY_POLICY], "default-src 'none'; sandbox");

    // A form post is passed on whole, with the Origin check of every changing request.
    let post = |origin: &str| {
        Request::builder()
            .method("POST")
            .uri("/forge/dreamference/mightling/issues/new")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, origin)
            .header(header::COOKIE, &cookie)
            .body(Body::from(vec![b'x'; 3 * 1024 * 1024]))
            .unwrap()
    };
    let (status, _, body) = call(&server, post(ORIGIN)).await;
    assert_eq!((status, body.as_str()), (StatusCode::OK, "posted 3145728 bytes"));
    let (status, _, _) = call(&server, post("http://evil.example")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    // `/forge` goes to `/forge/`.
    let (status, headers, _) = call(&server, get("/forge?x=1", &cookie)).await;
    assert_eq!(status, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(headers[header::LOCATION], "/forge/?x=1");
}

#[tokio::test]
async fn the_page_learns_whether_to_offer_the_code_view() {
    let scratch = scratch("status");
    let (url, _) = start_stand_in().await;
    let server = Server::new(config(&scratch.dir, &url, Vec::new())).unwrap();
    let (id, cookie) = device(&server);
    let (status, _, body) = call(&server, get("/api/forge", &cookie)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, r#"{"available":false,"control":true,"path":"/forge/"}"#);
    write_password(&scratch.dir);
    auth::set_scope(&server.config.state_dir, &id, auth::Scope::Watch).unwrap();
    let (_, _, body) = call(&server, get("/api/forge", &cookie)).await;
    assert_eq!(body, r#"{"available":true,"control":false,"path":"/forge/"}"#);
    let (status, _, _) = call(&server, Request::builder().uri("/api/forge").header(header::HOST, HOST).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn watch_devices_and_strangers_get_no_forge() {
    let scratch = scratch("refused");
    let (url, seen) = start_stand_in().await;
    write_password(&scratch.dir);
    let server = Server::new(config(&scratch.dir, &url, Vec::new())).unwrap();
    let (id, cookie) = device(&server);
    auth::set_scope(&server.config.state_dir, &id, auth::Scope::Watch).unwrap();

    let (status, _, body) = call(&server, get("/forge/", &cookie)).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(body.contains("watch only"));
    let (status, _, _) = call(&server, get("/forge/", "mightling_device=forged")).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _, _) = call(&server, Request::builder().uri("/forge/").header(header::HOST, HOST).body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(seen.lock().unwrap().requests.is_empty(), "the forge was asked for a refused request");
}

#[tokio::test]
async fn git_passes_through_with_its_own_credentials_on_the_lan_only() {
    let scratch = scratch("git");
    let (url, seen) = start_stand_in().await;
    write_password(&scratch.dir);
    let server = Server::new(config(&scratch.dir, &url, vec!["front.example.ts.net".to_string()])).unwrap();
    let (_, cookie) = device(&server);

    // No credential of ours, no Origin: git's own Basic credentials reach the forge, nothing else.
    let push = Request::builder()
        .method("POST")
        .uri("/forge/dreamference/mightling.git/git-receive-pack")
        .header(header::HOST, HOST)
        .header(header::AUTHORIZATION, "Basic b3duZXI6eA==")
        .header(header::COOKIE, &cookie)
        .body(Body::from(vec![0u8; 5 * 1024 * 1024]))
        .unwrap();
    let (status, _, body) = call(&server, push).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, "git POST bytes=5242880 auth=Basic b3duZXI6eA==");
    {
        let seen = seen.lock().unwrap();
        assert_eq!(seen.sign_ins, 0, "git's protocol never uses the owner's session");
        assert!(seen.requests.last().unwrap().2.is_none(), "a cookie reached the forge with git's protocol");
    }
    // On the overlay front's name it needs this server's credential like everything else.
    let refs = Request::builder()
        .uri("/forge/dreamference/mightling.git/info/refs?service=git-upload-pack")
        .header(header::HOST, "front.example.ts.net")
        .body(Body::empty())
        .unwrap();
    let (status, _, _) = call(&server, refs).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_node_without_a_forge_or_with_a_wrong_password_says_so() {
    let scratch = scratch("missing");
    let (url, seen) = start_stand_in().await;
    let server = Server::new(config(&scratch.dir, &url, Vec::new())).unwrap();
    let (_, cookie) = device(&server);
    let (status, _, body) = call(&server, get("/forge/", &cookie)).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("ling-admin forge on"));

    let folder = scratch.dir.join("user/.config/dreamference/forge");
    std::fs::create_dir_all(&folder).unwrap();
    std::fs::write(folder.join("owner-password"), "wrong").unwrap();
    let (status, _, body) = call(&server, get("/forge/", &cookie)).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    assert!(body.contains("refused the owner's password") && !body.contains("wrong"), "{body}");
    assert_eq!(seen.lock().unwrap().sign_ins, 0);

    // A forge that is not running.
    let server = Server::new(config(&scratch.dir, "http://127.0.0.1:9", Vec::new())).unwrap();
    let (_, cookie) = device(&server);
    let (status, _, body) = call(&server, get("/forge/", &cookie)).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    assert!(body.contains("not answering"), "{body}");
}
