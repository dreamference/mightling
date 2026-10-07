//! The HTTP side of `ling web` (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.1, §4.3): routes, the
//! credential check every request passes, and the pages a person sees before they have one.

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::collections::HashSet;
use std::net::SocketAddr;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicU32;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering;
use std::time::Duration;

use axum::Form;
use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::extract::Query;
use axum::extract::Request;
use axum::extract::State;
use axum::extract::ws::WebSocketUpgrade;
use axum::http::HeaderMap;
use axum::http::HeaderValue;
use axum::http::Method;
use axum::http::StatusCode;
use axum::http::header;
use axum::middleware::Next;
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::get;
use axum::routing::post;
use futures::StreamExt;
use serde_json::json;
use tokio::io::AsyncWriteExt;

use crate::app_server::AppServer;
use crate::app_server::Launch;
use crate::assets;
use crate::ask;
use crate::auth;
use crate::auth::Addresses;
use crate::policy::BusyTracker;
use crate::policy::Policy;

/// The port `ling web` listens on: clear of Onyx's 3000 and 80, and kept so bookmarks never move.
pub const DEFAULT_PORT: u16 = 3100;
/// The largest image `/api/upload` takes.
pub const IMAGE_CAP: u64 = 20 * 1024 * 1024;
/// The largest other file `/api/upload` takes.
pub const FILE_CAP: u64 = 100 * 1024 * 1024;
/// How long a device cookie lasts: as long as browsers allow.
const DEVICE_COOKIE_MAX_AGE: u64 = 400 * 24 * 60 * 60;

/// Sent with every response. The UI build carries its own copy as a `<meta>` tag, whose rules this
/// header repeats, so the two together allow nothing either refuses.
pub const CSP: &str = "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; \
font-src 'self' data:; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; \
base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

/// Everything a running `ling web` needs to know.
#[derive(Clone, Debug)]
pub struct Config {
    /// `ling`'s home folder (`~/.mightling`): Ask folders, the served model, Night Shift's marker.
    pub codex_home: PathBuf,
    /// Tokens, devices and codes (`~/.mightling/web`).
    pub state_dir: PathBuf,
    /// The port, for the `Host` and `Origin` checks.
    pub port: u16,
    /// The LAN addresses and names served besides loopback; empty on loopback only.
    pub lan_names: Vec<String>,
    /// The app-server's socket.
    pub socket: PathBuf,
    /// How to start the app-server, and how `--composed` prompts are read.
    pub launch: Launch,
}

pub struct Server {
    pub config: Config,
    pub policy: Policy,
    pub addresses: Addresses,
    pub app_server: AppServer,
    owner_token: String,
    sessions: Mutex<HashSet<String>>,
    pairing_failures: AtomicU32,
    busy: Mutex<HashMap<u64, BusyTracker>>,
    next_tab: AtomicU64,
}

impl Server {
    pub fn new(config: Config) -> std::io::Result<Arc<Server>> {
        auth::private_dir(&config.state_dir)?;
        let owner_token = auth::owner_token(&config.state_dir)?;
        let addresses = Addresses::new(config.port, &config.lan_names);
        let app_server = AppServer::new(config.socket.clone(), config.launch.clone());
        Ok(Arc::new(Server {
            policy: Policy::embedded(),
            addresses,
            app_server,
            owner_token,
            sessions: Mutex::new(HashSet::new()),
            pairing_failures: AtomicU32::new(0),
            busy: Mutex::new(HashMap::new()),
            next_tab: AtomicU64::new(1),
            config,
        }))
    }

    pub fn ask_root(&self) -> PathBuf {
        ask::root(&self.config.codex_home)
    }

    pub fn new_tab_id(&self) -> u64 {
        self.next_tab.fetch_add(1, Ordering::Relaxed)
    }

    /// Whether a request carries a valid credential: this machine's session cookie, a paired
    /// device's cookie, or the owner token as a bearer token.
    pub fn authenticated(&self, headers: &HeaderMap) -> bool {
        let cookies = header_str(headers, header::COOKIE);
        if let Some(token) = auth::cookie(cookies, auth::SESSION_COOKIE)
            && self.sessions.lock().unwrap_or_else(|p| p.into_inner()).contains(&auth::sha256_hex(token))
        {
            return true;
        }
        if let Some(token) = auth::cookie(cookies, auth::DEVICE_COOKIE)
            && auth::device_for_token(&self.config.state_dir, token).is_some()
        {
            return true;
        }
        header_str(headers, header::AUTHORIZATION)
            .and_then(|value| value.strip_prefix("Bearer "))
            .is_some_and(|token| auth::same_secret(token.trim(), &self.owner_token))
    }

    fn new_session(&self) -> String {
        let token = auth::random_hex(32);
        self.sessions.lock().unwrap_or_else(|p| p.into_inner()).insert(auth::sha256_hex(&token));
        token
    }

    /// Notes a notification a tab's connection carried; keeps Night Shift's marker in step.
    pub fn observe_busy(&self, tab: u64, method: &str, params: Option<&serde_json::Value>) {
        let mut busy = self.busy.lock().unwrap_or_else(|p| p.into_inner());
        if busy.entry(tab).or_default().observe(method, params) {
            self.sync_marker(&busy);
        }
    }

    /// Forgets a tab that closed: its turns are no longer anyone's to wait for here.
    pub fn forget_tab(&self, tab: u64) {
        let mut busy = self.busy.lock().unwrap_or_else(|p| p.into_inner());
        if busy.remove(&tab).is_some() {
            self.sync_marker(&busy);
        }
    }

    pub fn marker_path(&self) -> PathBuf {
        self.config.codex_home.join("night").join("busy").join(std::process::id().to_string())
    }

    fn sync_marker(&self, busy: &HashMap<u64, BusyTracker>) {
        let threads: BTreeSet<String> = busy.values().flat_map(|tracker| tracker.threads().cloned()).collect();
        let path = self.marker_path();
        if threads.is_empty() {
            let _ = std::fs::remove_file(&path);
        } else {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&path, json!({ "threads": threads }).to_string());
        }
    }

    /// The model the launcher last wrote into the catalog, for the UI's start-up screen.
    pub fn served_model(&self) -> Option<String> {
        let text = std::fs::read_to_string(self.config.codex_home.join("model_catalog.json")).ok()?;
        let catalog: serde_json::Value = serde_json::from_str(&text).ok()?;
        let first = catalog.get("models")?.get(0)?;
        first.get("slug").or_else(|| first.get("id")).and_then(|name| name.as_str()).map(str::to_string)
    }
}

pub fn header_str(headers: &HeaderMap, name: header::HeaderName) -> Option<&str> {
    headers.get(name).and_then(|value| value.to_str().ok())
}

/// The application, every route behind [`guard`].
pub fn router(server: Arc<Server>) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/index.html", get(index))
        .route("/assets/{*path}", get(asset))
        .route("/bridge.js", get(bridge_js))
        .route("/ws", get(websocket))
        .route("/api/upload", post(upload).layer(DefaultBodyLimit::disable()))
        .route("/api/transcribe", post(not_built))
        .route("/api/apps", get(not_built))
        .route("/images/{*path}", get(not_built))
        .route("/healthz", get(healthz))
        .route("/login", get(login))
        .route("/pair", get(pair_page).post(pair))
        .route("/pair.css", get(pair_css))
        .fallback(not_found)
        .layer(axum::middleware::from_fn_with_state(server.clone(), guard))
        .with_state(server)
}

/// What every request passes first: the `Host` it names, its `Origin` when it changes something
/// or opens the bridge, and a credential everywhere but the three pages that hand one out.
async fn guard(State(server): State<Arc<Server>>, request: Request, next: Next) -> Response {
    let headers = request.headers();
    if !server.addresses.host_ok(header_str(headers, header::HOST)) {
        return secured(text(StatusCode::MISDIRECTED_REQUEST, "This server does not answer to that name.\n"));
    }
    let upgrade = header_str(headers, header::UPGRADE).is_some_and(|value| value.eq_ignore_ascii_case("websocket"));
    let changes = !matches!(*request.method(), Method::GET | Method::HEAD);
    if (upgrade || changes) && !server.addresses.origin_ok(header_str(headers, header::ORIGIN)) {
        return secured(text(StatusCode::FORBIDDEN, "Requests from other sites are refused.\n"));
    }
    let path = request.uri().path();
    let public = matches!(path, "/login" | "/pair" | "/pair.css");
    if !public && !server.authenticated(headers) {
        let response = if path == "/" || path == "/index.html" {
            html(StatusCode::UNAUTHORIZED, assets::LOCKED_HTML)
        } else {
            text(StatusCode::UNAUTHORIZED, "Sign in first: `ling web open` on this machine, or pair this device.\n")
        };
        return secured(response);
    }
    secured(next.run(request).await)
}

fn secured(mut response: Response) -> Response {
    let headers = response.headers_mut();
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static(CSP));
    headers.insert(header::X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"));
    headers.insert(header::REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    headers.insert(header::X_FRAME_OPTIONS, HeaderValue::from_static("DENY"));
    headers.entry(header::CACHE_CONTROL).or_insert(HeaderValue::from_static("no-store"));
    response
}

fn text(status: StatusCode, body: &str) -> Response {
    (status, [(header::CONTENT_TYPE, "text/plain; charset=utf-8")], body.to_string()).into_response()
}

fn html(status: StatusCode, body: &str) -> Response {
    (status, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], body.to_string()).into_response()
}

fn json_response(status: StatusCode, value: serde_json::Value) -> Response {
    (status, [(header::CONTENT_TYPE, "application/json")], value.to_string()).into_response()
}

async fn index() -> Response {
    html(StatusCode::OK, &assets::index_html())
}

async fn asset(axum::extract::Path(path): axum::extract::Path<String>) -> Response {
    match assets::ui_asset(&format!("assets/{path}")) {
        Some(bytes) => {
            let mut response = (StatusCode::OK, [(header::CONTENT_TYPE, assets::content_type(&path))], bytes).into_response();
            // Vite names every asset after its content.
            response.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("public, max-age=31536000, immutable"));
            response
        }
        None => not_found().await,
    }
}

async fn bridge_js() -> Response {
    (StatusCode::OK, [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")], assets::BRIDGE_JS).into_response()
}

async fn pair_css() -> Response {
    (StatusCode::OK, [(header::CONTENT_TYPE, "text/css; charset=utf-8")], assets::PAIR_CSS).into_response()
}

async fn not_found() -> Response {
    text(StatusCode::NOT_FOUND, "Not found.\n")
}

/// Routes the spec names whose services are not built yet in this release.
async fn not_built() -> Response {
    json_response(StatusCode::NOT_IMPLEMENTED, json!({ "error": "not available in this release of ling web" }))
}

async fn healthz(State(server): State<Arc<Server>>) -> Response {
    json_response(
        StatusCode::OK,
        json!({ "ok": true, "pid": std::process::id(), "lan": !server.config.lan_names.is_empty() }),
    )
}

async fn websocket(State(server): State<Arc<Server>>, upgrade: WebSocketUpgrade) -> Response {
    upgrade.max_message_size(64 << 20).on_upgrade(move |socket| crate::relay::run_tab(socket, server))
}

/// `ling web open`'s link: trades the one-time code for a session cookie.
async fn login(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let code = query.get("code").map(String::as_str).unwrap_or_default();
    if !auth::take_login_code(&server.config.state_dir, code) {
        return html(StatusCode::UNAUTHORIZED, assets::LOGIN_FAILED_HTML);
    }
    let token = server.new_session();
    redirect_with_cookie(format!("{}={token}; HttpOnly; SameSite=Strict; Path=/", auth::SESSION_COOKIE))
}

fn redirect_with_cookie(cookie: String) -> Response {
    let mut response = (StatusCode::SEE_OTHER, [(header::LOCATION, "/")]).into_response();
    if let Ok(value) = HeaderValue::from_str(&cookie) {
        response.headers_mut().insert(header::SET_COOKIE, value);
    }
    response
}

async fn pair_page() -> Response {
    html(StatusCode::OK, &assets::pair_html(None))
}

/// The pairing form: a code from `ling web pair` and a name for this device.
async fn pair(State(server): State<Arc<Server>>, Form(form): Form<HashMap<String, String>>) -> Response {
    let code = form.get("code").map(|code| code.trim().to_string()).unwrap_or_default();
    let name = form.get("name").map(String::as_str).unwrap_or_default();
    let state = &server.config.state_dir;
    if auth::take_pairing_code(state, &code) {
        server.pairing_failures.store(0, Ordering::Relaxed);
        return match auth::add_device(state, name) {
            Ok((_, token)) => redirect_with_cookie(format!(
                "{}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={DEVICE_COOKIE_MAX_AGE}",
                auth::DEVICE_COOKIE
            )),
            Err(err) => text(StatusCode::INTERNAL_SERVER_ERROR, &format!("Could not record the device: {err}\n")),
        };
    }
    // Ten wrong codes withdraw every pending one: guessing eight digits takes far more.
    if server.pairing_failures.fetch_add(1, Ordering::Relaxed) + 1 >= auth::PAIRING_ATTEMPTS {
        auth::withdraw_pairing_codes(state);
        server.pairing_failures.store(0, Ordering::Relaxed);
    }
    tokio::time::sleep(Duration::from_millis(500)).await;
    html(StatusCode::UNAUTHORIZED, &assets::pair_html(Some("That code is wrong or has expired. Run `ling web pair` for a new one.")))
}

/// A file name a browser sent, made safe to write: its last component, printable, not hidden.
pub fn clean_file_name(name: &str) -> String {
    let base = name.rsplit(['/', '\\']).next().unwrap_or_default();
    let cleaned: String = base.chars().filter(|c| !c.is_control()).take(128).collect();
    let cleaned = cleaned.trim().trim_start_matches('.').to_string();
    if cleaned.is_empty() { "attachment".to_string() } else { cleaned }
}

/// A path in `folder` for `name` that does not exist yet.
fn unused_path(folder: &Path, name: &str) -> PathBuf {
    let first = folder.join(name);
    if first.symlink_metadata().is_err() {
        return first;
    }
    let (stem, extension) = match name.rsplit_once('.') {
        Some((stem, extension)) if !stem.is_empty() => (stem.to_string(), format!(".{extension}")),
        _ => (name.to_string(), String::new()),
    };
    (1..).map(|n| folder.join(format!("{stem}-{n}{extension}"))).find(|path| path.symlink_metadata().is_err()).unwrap_or(first)
}

/// An attachment into an Ask thread's folder: `?thread=<id>&name=<file>&kind=image|file`, the
/// bytes as the body. Answers the path the UI then sends as `localImage` or names in the prompt.
async fn upload(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>, request: Request) -> Response {
    let cap = match query.get("kind").map(String::as_str) {
        Some("image") => IMAGE_CAP,
        Some("file") | None => FILE_CAP,
        Some(_) => return text(StatusCode::BAD_REQUEST, "kind is image or file\n"),
    };
    let thread = query.get("thread").map(String::as_str).unwrap_or_default();
    let Some(folder) = ask::folder_of(&server.ask_root(), thread) else {
        return text(StatusCode::NOT_FOUND, "Attachments go to an Ask thread, and that is not one.\n");
    };
    let declared = header_str(request.headers(), header::CONTENT_LENGTH).and_then(|value| value.parse::<u64>().ok());
    if declared.is_some_and(|length| length > cap) {
        return text(StatusCode::PAYLOAD_TOO_LARGE, &format!("The limit is {} MB.\n", cap >> 20));
    }
    let path = unused_path(&folder, &clean_file_name(query.get("name").map(String::as_str).unwrap_or_default()));
    let mut file = match tokio::fs::OpenOptions::new().write(true).create_new(true).open(&path).await {
        Ok(file) => file,
        Err(err) => return text(StatusCode::INTERNAL_SERVER_ERROR, &format!("Could not write the attachment: {err}\n")),
    };
    let mut stream = request.into_body().into_data_stream();
    let mut written: u64 = 0;
    while let Some(chunk) = stream.next().await {
        let chunk = match chunk {
            Ok(chunk) => chunk,
            Err(_) => {
                let _ = tokio::fs::remove_file(&path).await;
                return text(StatusCode::BAD_REQUEST, "The upload broke off.\n");
            }
        };
        written += chunk.len() as u64;
        if written > cap {
            drop(file);
            let _ = tokio::fs::remove_file(&path).await;
            return text(StatusCode::PAYLOAD_TOO_LARGE, &format!("The limit is {} MB.\n", cap >> 20));
        }
        if let Err(err) = file.write_all(&chunk).await {
            let _ = tokio::fs::remove_file(&path).await;
            return text(StatusCode::INTERNAL_SERVER_ERROR, &format!("Could not write the attachment: {err}\n"));
        }
    }
    let _ = file.flush().await;
    json_response(StatusCode::OK, json!({ "path": path.to_string_lossy(), "bytes": written }))
}

/// Serves until `shutdown` resolves, on every address in `binds`.
pub async fn serve(
    server: Arc<Server>,
    listeners: Vec<tokio::net::TcpListener>,
    shutdown: impl std::future::Future<Output = ()> + Send + 'static,
) -> std::io::Result<()> {
    let app = router(server.clone()).into_make_service_with_connect_info::<SocketAddr>();
    let (stop, _) = tokio::sync::broadcast::channel::<()>(1);
    let mut tasks = Vec::new();
    for listener in listeners {
        let mut stopped = stop.subscribe();
        let app = app.clone();
        tasks.push(tokio::spawn(async move {
            axum::serve(listener, app)
                .with_graceful_shutdown(async move {
                    let _ = stopped.recv().await;
                })
                .await
        }));
    }
    shutdown.await;
    let _ = stop.send(());
    for task in tasks {
        let _ = task.await;
    }
    server.app_server.stop_child().await;
    let _ = std::fs::remove_file(server.marker_path());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_names_are_made_safe() {
        assert_eq!(clean_file_name("../../etc/passwd"), "passwd");
        assert_eq!(clean_file_name("C:\\Users\\me\\photo.jpg"), "photo.jpg");
        assert_eq!(clean_file_name(".bashrc"), "bashrc");
        assert_eq!(clean_file_name(""), "attachment");
        assert_eq!(clean_file_name("a\u{0}b\nc.txt"), "abc.txt");
    }
}
