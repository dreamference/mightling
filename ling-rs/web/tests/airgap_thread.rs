//! `/api/airgapped?thread=<id>`: one thread's air-gap level, for the phone bridges, which may not
//! use a thread at `on` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §11). A test binary of its own:
//! it points `HOME` and `CODEX_HOME` at a scratch folder and sets the air-gap variables, which the
//! tests in server.rs would see if they shared the process.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;

use axum::body::Body;
use axum::http::Request;
use axum::http::StatusCode;
use axum::http::header;
use ling_web_server::app_server::Launch;
use ling_web_server::auth;
use ling_web_server::server;
use ling_web_server::server::Config;
use ling_web_server::server::Server;
use serde_json::Value;
use tower::ServiceExt;

const HOST: &str = "127.0.0.1:3100";

fn config(dir: &Path) -> Config {
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
        port: 3100,
        lan_names: Vec::new(),
        public_names: Vec::new(),
    }
}

async fn call(server: &Arc<Server>, request: Request<Body>) -> (StatusCode, String) {
    let response = server::router(server.clone()).oneshot(request).await.unwrap();
    let status = response.status();
    let body = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, String::from_utf8_lossy(&body).into_owned())
}

async fn sign_in(server: &Arc<Server>) -> String {
    let code = auth::issue_login_code(&server.config.state_dir).unwrap();
    let login = Request::builder().uri(format!("/login?code={code}")).header(header::HOST, HOST);
    let response = server::router(server.clone()).oneshot(login.body(Body::empty()).unwrap()).await.unwrap();
    let cookie = response.headers().get(header::SET_COOKIE).unwrap().to_str().unwrap().to_string();
    cookie.split(';').next().unwrap().to_string()
}

/// A thread is `on` when its own level, its folder's `dreamference.toml` or the user's level says
/// so, the strictest winning; the answer names the thread, so a client can tell it from the
/// user-level answer an older `ling web` would give; a malformed id gets no level at all.
#[tokio::test]
async fn one_threads_air_gap_is_the_strictest_of_its_own_its_folders_and_the_users() {
    let dir = PathBuf::from("/tmp").join(format!("lw-airgap-thread-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    // SAFETY: this binary holds this one test, so nothing else reads the environment meanwhile.
    unsafe {
        std::env::set_var("HOME", dir.join("home"));
        std::env::set_var("CODEX_HOME", dir.join("home/.mightling"));
        std::env::remove_var(ling_airgapped::ENV_VAR);
        std::env::remove_var("DREAMFERENCE_CONFIG_PATH");
    }
    let server = Server::new(config(&dir)).unwrap();
    let cookie = sign_in(&server).await;
    let level = |thread: &str| {
        let request = Request::builder()
            .uri(format!("/api/airgapped?thread={thread}"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let server = server.clone();
        async move {
            let (status, body) = call(&server, request).await;
            (status, serde_json::from_str::<Value>(&body).unwrap_or(Value::Null))
        }
    };

    // Nothing set anywhere: off, and the answer names the thread.
    let (status, answer) = level("thr-plain").await;
    assert_eq!(status, StatusCode::OK, "{answer}");
    assert_eq!((answer["level"].as_str(), answer["thread"].as_str()), (Some("off"), Some("thr-plain")));

    // `/airgapped on` in the app writes the thread's own level file.
    let sessions = dir.join("home/.mightling").join(ling_airgapped::SESSION_DIR);
    std::fs::create_dir_all(&sessions).unwrap();
    std::fs::write(sessions.join("thr-sealed"), "on\n").unwrap();
    assert_eq!(level("thr-sealed").await.1["level"], "on");
    assert_eq!(level("thr-plain").await.1["level"], "off");

    // An Ask thread whose folder asks for `on` in its dreamference.toml.
    let root = server.ask_root();
    let folder = ling_web_server::ask::new_folder(&root).unwrap();
    ling_web_server::ask::link_thread(&root, "thr-folder", &folder).unwrap();
    std::fs::write(folder.join("dreamference.toml"), "mightling_airgapped = \"on\"\n").unwrap();
    assert_eq!(level("thr-folder").await.1["level"], "on");

    // The user's level is `on`: every thread is, even one whose own file says `off`.
    std::fs::write(sessions.join("thr-open"), "off\n").unwrap();
    let user = dir.join("home/.config/dreamference/config.toml");
    std::fs::create_dir_all(user.parent().unwrap()).unwrap();
    std::fs::write(&user, "mightling_airgapped = \"on\"\n").unwrap();
    assert_eq!(level("thr-open").await.1["level"], "on");
    std::fs::remove_file(&user).unwrap();
    assert_eq!(level("thr-open").await.1["level"], "off");

    // A malformed id is refused, not resolved as no thread.
    assert_eq!(level("..%2Fx").await.0, StatusCode::BAD_REQUEST);
    assert_eq!(level("").await.0, StatusCode::BAD_REQUEST);
    let _ = std::fs::remove_dir_all(&dir);
}
