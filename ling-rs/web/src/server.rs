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
use axum::routing::any;
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
use crate::devices;
use crate::documents;
use crate::policy::BusyTracker;
use crate::policy::Policy;
use crate::sidecars;

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
    /// Names served behind an HTTPS front on the user's overlay, exactly as the browser sends them
    /// in `Host` (the Telegram Mini App's address, telegram_app.rs); empty unless one is set.
    pub public_names: Vec<String>,
    /// The app-server's socket.
    pub socket: PathBuf,
    /// How to start the app-server, and how `--composed` prompts are read.
    pub launch: Launch,
    /// The image search service's store, served at `/images/` (sidecars.rs).
    pub images_dir: PathBuf,
    /// The speech-to-text service `/api/transcribe` passes recordings to (sidecars.rs).
    pub speech_addr: String,
    /// The user's own configuration file (`~/.config/dreamference/config.toml`), read for the
    /// `[chat]` settings and Night Shift's phone repositories; None when there is no home. Tests
    /// point it into their scratch folder, never at the real file.
    pub user_config: Option<PathBuf>,
    /// The node's Forgejo base URL (`http://127.0.0.1:3200` when `None`), overridden by tests.
    pub forge_url: Option<String>,
}

pub struct Server {
    pub config: Config,
    pub policy: Policy,
    pub addresses: Addresses,
    pub app_server: AppServer,
    pub chat_repos: crate::chat_repos::ChatRepos,
    owner_token: String,
    sessions: Mutex<HashSet<String>>,
    bridge_sessions: Mutex<HashSet<String>>,
    pairing_failures: AtomicU32,
    busy: Mutex<HashMap<u64, BusyTracker>>,
    next_tab: AtomicU64,
    /// The sync hub's store, opened on the first request after `ling sync enable` (sync_hub.rs).
    sync: Mutex<Option<Arc<crate::sync_store::Store>>>,
    /// The hub's live half: following and answering turns (sync_live.rs).
    live: Mutex<Option<Arc<crate::sync_live::Live>>>,
    /// The forge's signed-in cookies, for `/forge/` (forge.rs).
    pub forge: crate::forge::ForgeProxy,
}

impl Server {
    pub fn new(config: Config) -> std::io::Result<Arc<Server>> {
        auth::private_dir(&config.state_dir)?;
        let owner_token = auth::owner_token(&config.state_dir)?;
        let addresses = Addresses::new(config.port, &config.lan_names).with_public(&config.public_names);
        let app_server = AppServer::new(config.socket.clone(), config.launch.clone());
        Ok(Arc::new(Server {
            policy: Policy::embedded(),
            addresses,
            app_server,
            chat_repos: crate::chat_repos::ChatRepos::new(),
            owner_token,
            sessions: Mutex::new(HashSet::new()),
            bridge_sessions: Mutex::new(HashSet::new()),
            pairing_failures: AtomicU32::new(0),
            busy: Mutex::new(HashMap::new()),
            next_tab: AtomicU64::new(1),
            sync: Mutex::new(None),
            live: Mutex::new(None),
            forge: crate::forge::ForgeProxy::new(),
            config,
        }))
    }

    pub fn ask_root(&self) -> PathBuf {
        ask::root(&self.config.codex_home)
    }

    /// The sync hub's store while the hub is on (specs/DREAMFERENCE_MIGHTLING_SYNC.md): asked on
    /// every request, so `ling sync enable` and `disable` need no restart, and nothing is opened
    /// or written while it is off.
    pub fn sync_store(&self) -> Option<Arc<crate::sync_store::Store>> {
        let mut cached = self.sync.lock().unwrap_or_else(|p| p.into_inner());
        if !crate::sync_store::enabled(&self.config.codex_home) {
            *cached = None;
            return None;
        }
        if cached.is_none() {
            *cached = crate::sync_store::Store::open(&self.config.codex_home).ok().map(Arc::new);
        }
        cached.clone()
    }

    /// The hub's live half while the hub is on: in memory, but for the queue of messages waiting
    /// for an offline device.
    pub fn sync_live(&self) -> Option<Arc<crate::sync_live::Live>> {
        let mut cached = self.live.lock().unwrap_or_else(|p| p.into_inner());
        if !crate::sync_store::enabled(&self.config.codex_home) {
            *cached = None;
            return None;
        }
        Some(cached.get_or_insert_with(|| Arc::new(crate::sync_live::Live::new(crate::sync_store::hub_dir(&self.config.codex_home)))).clone())
    }

    /// A wrong pairing code; ten withdraw every pending one (shared by `/pair` and `/api/sync/pair`).
    pub fn pairing_failed(&self) {
        if self.pairing_failures.fetch_add(1, Ordering::Relaxed) + 1 >= auth::PAIRING_ATTEMPTS {
            auth::withdraw_pairing_codes(&self.config.state_dir);
            self.pairing_failures.store(0, Ordering::Relaxed);
        }
    }

    pub fn pairing_succeeded(&self) {
        self.pairing_failures.store(0, Ordering::Relaxed);
    }

    pub fn new_tab_id(&self) -> u64 {
        self.next_tab.fetch_add(1, Ordering::Relaxed)
    }

    /// Whether a request carries a valid credential: this machine's session cookie or a paired
    /// device's cookie. The owner token counts only on `/healthz`: a sandboxed command can read
    /// the token file, so it must open nothing else (auth.rs).
    pub fn authenticated(&self, path: &str, headers: &HeaderMap) -> bool {
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
        // A sync agent's secret opens the hub's routes and nothing else (sync_hub.rs).
        if path.starts_with("/api/sync/")
            && let Some(secret) = header_str(headers, header::AUTHORIZATION).and_then(|value| value.strip_prefix("Bearer "))
            && auth::sync_device_for_token(&self.config.state_dir, secret.trim()).is_some()
        {
            return true;
        }
        path == "/healthz"
            && header_str(headers, header::AUTHORIZATION)
                .and_then(|value| value.strip_prefix("Bearer "))
                .is_some_and(|token| auth::same_secret(token.trim(), &self.owner_token))
    }

    /// Whether a request carries this machine's own session (from `ling web open`, whose link is
    /// for loopback), not a paired device's cookie: what pairing and revoking devices need.
    pub fn owner_session(&self, headers: &HeaderMap) -> bool {
        auth::cookie(header_str(headers, header::COOKIE), auth::SESSION_COOKIE)
            .is_some_and(|token| self.sessions.lock().unwrap_or_else(|p| p.into_inner()).contains(&auth::sha256_hex(token)))
    }

    /// The paired device name when the request is authenticated by a paired device cookie and not
    /// by the owner's own session cookie.
    pub fn paired_device_name(&self, headers: &HeaderMap) -> Option<String> {
        if self.owner_session(headers) {
            return None;
        }
        let token = auth::cookie(header_str(headers, header::COOKIE), auth::DEVICE_COOKIE)?;
        auth::device_for_token(&self.config.state_dir, token).map(|device| device.name)
    }

    /// Whether the request carries a session cookie issued with `bridge=1` (`ling chat`).
    pub fn bridge_session(&self, headers: &HeaderMap) -> bool {
        auth::cookie(header_str(headers, header::COOKIE), auth::SESSION_COOKIE)
            .is_some_and(|token| self.bridge_sessions.lock().unwrap_or_else(|p| p.into_inner()).contains(&auth::sha256_hex(token)))
    }

    /// Whether the caller is a phone bridge: a `bridge=1` session (`ling chat`) or a Signal bridge device.
    pub fn is_bridge_caller(&self, headers: &HeaderMap) -> bool {
        self.bridge_session(headers)
            || self.paired_device_name(headers).as_deref().is_some_and(ask::is_bridge_device)
    }

    /// Whether `Host` names the configured HTTPS front (`telegram_mini_app_url` / `public_names`)
    /// and not loopback, the LAN or another address (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §12.1 G2).
    pub fn front_host_ok(&self, headers: &HeaderMap) -> bool {
        let Some(host) = header_str(headers, header::HOST) else {
            return false;
        };
        let host = host.trim().to_ascii_lowercase();
        if host.is_empty() {
            return false;
        }
        if let Some(configured) = self
            .config
            .user_config
            .as_deref()
            .and_then(crate::telegram_app::mini_app_host)
        {
            return host == configured;
        }
        !self.config.public_names.is_empty()
            && self
                .config
                .public_names
                .iter()
                .any(|name| name.trim().eq_ignore_ascii_case(&host))
    }

    /// Whether a `Host` header is one of the overlay front's names (`public_names`), not the LAN's.
    pub fn public_host(&self, host: Option<&str>) -> bool {
        host.is_some_and(|host| {
            let host = host.trim().to_ascii_lowercase();
            self.config.public_names.iter().any(|name| name.trim().to_ascii_lowercase() == host)
        })
    }

    fn new_session_with(&self, bridge: bool) -> String {
        let token = auth::random_hex(32);
        let hash = auth::sha256_hex(&token);
        self.sessions.lock().unwrap_or_else(|p| p.into_inner()).insert(hash.clone());
        if bridge {
            self.bridge_sessions.lock().unwrap_or_else(|p| p.into_inner()).insert(hash);
        }
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
        self.chat_repos.forget_tab(tab);
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
        self.chat_repos.sync_running_threads(&threads);
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

    /// Whether the served model takes images: true only when the launcher wrote `image` into the
    /// catalog's `input_modalities`, which it does after asking the model server (ling-rs
    /// `vision.rs`). A catalog that does not say is not taken as a yes.
    pub fn served_model_takes_images(&self) -> bool {
        let Ok(text) = std::fs::read_to_string(self.config.codex_home.join("model_catalog.json")) else { return false };
        let Ok(catalog) = serde_json::from_str::<serde_json::Value>(&text) else { return false };
        catalog
            .pointer("/models/0/input_modalities")
            .and_then(|modalities| modalities.as_array())
            .is_some_and(|modalities| modalities.iter().any(|m| m.as_str() == Some("image")))
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
        .route("/api/transcribe", post(transcribe).layer(DefaultBodyLimit::disable()))
        .route("/api/apps", get(not_built))
        .route("/images/{*path}", get(image))
        .route("/docs/{collection}/{id}", get(document))
        .route("/api/docs", get(docs_status))
        .route("/api/docs/{action}", post(docs_change))
        .route("/healthz", get(healthz))
        .route("/api/airgapped", get(airgapped))
        .route("/api/chat/settings", get(chat_settings))
        .route("/api/ask/files", get(ask_files))
        .route("/api/night/repos", get(night_repos))
        .route("/api/night/add", post(night_add))
        .route("/api/night/status", get(night_status))
        .route("/api/night/reports", get(night_reports))
        .route("/api/ask/file", get(ask_file))
        .route("/api/telegram/check", post(telegram_check))
        .route("/api/telegram/launch-pair", get(telegram_launch_pair_get).post(telegram_launch_pair_post))
        .route("/api/telegram/pending-pairs", get(telegram_pending_pairs))
        .route("/api/telegram/pending-pairs/approve", post(telegram_pending_pairs_approve))
        .route("/api/telegram/pending-pairs/decline", post(telegram_pending_pairs_decline))
        .route("/api/forge", get(forge_status))
        .route("/forge", any(forge_proxy))
        .route("/forge/", any(forge_proxy))
        .route("/forge/{*path}", any(forge_proxy).layer(DefaultBodyLimit::disable()))
        .route("/login", get(login))
        .route("/pair", get(pair_page).post(pair))
        .route("/pair.css", get(pair_css))
        .route("/devices", get(devices_page))
        .route("/devices/pair", post(devices_pair))
        .route("/devices/revoke", post(devices_revoke))
        .route("/devices/scope", post(devices_scope))
        .merge(crate::sync_hub::routes())
        .merge(crate::chat_repos::routes())
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
    // Git over HTTP to the forge carries the forge's own credentials, as on the forge's own port,
    // and is passed on only on the LAN and loopback names (forge.rs).
    if crate::forge::is_git(request.uri().path()) && !server.public_host(header_str(headers, header::HOST)) {
        return secured(next.run(request).await);
    }
    let upgrade = header_str(headers, header::UPGRADE).is_some_and(|value| value.eq_ignore_ascii_case("websocket"));
    let changes = !matches!(*request.method(), Method::GET | Method::HEAD);
    if (upgrade || changes) && !server.addresses.origin_ok(header_str(headers, header::ORIGIN)) {
        return secured(text(StatusCode::FORBIDDEN, "Requests from other sites are refused.\n"));
    }
    let path = request.uri().path();
    // `/api/sync/pair` answers 404 while the hub is off, and pairs an agent only with a code.
    let public = matches!(
        path,
        "/login" | "/pair" | "/pair.css" | "/api/sync/pair" | "/api/telegram/launch-pair"
    ) || (path == "/bridge.js" && server.front_host_ok(headers));
    if !public && !server.authenticated(path, headers) {
        let response = if path == "/" || path == "/index.html" {
            html(StatusCode::UNAUTHORIZED, &assets::locked_html(server.front_host_ok(headers)))
        } else {
            text(StatusCode::UNAUTHORIZED, "Sign in first: `ling web open` on this machine, or pair this device.\n")
        };
        return secured(response);
    }
    secured(next.run(request).await)
}

fn secured(mut response: Response) -> Response {
    let headers = response.headers_mut();
    // A served document carries a stricter policy of its own (documents.rs).
    headers.entry(header::CONTENT_SECURITY_POLICY).or_insert(HeaderValue::from_static(CSP));
    headers.insert(header::X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"));
    headers.insert(header::REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    // The forge's pages may be framed by this server's own (forge.rs); nothing else may be.
    headers.entry(header::X_FRAME_OPTIONS).or_insert(HeaderValue::from_static("DENY"));
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

/// Whether the page offers its Code view: the node has a forge, and this device may see it.
async fn forge_status(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    let available = crate::forge::password_file(server.config.user_config.as_deref()).is_some_and(|file| file.is_file());
    let control = crate::forge::may_sign_in(&server.config.state_dir, server.owner_session(&headers), &headers);
    json_response(StatusCode::OK, json!({ "available": available, "control": control, "path": "/forge/" }))
}

/// `/forge/`: the node's forge, signed in for this machine's session and `control` devices.
async fn forge_proxy(State(server): State<Arc<Server>>, request: Request) -> Response {
    let git = crate::forge::is_git(request.uri().path());
    if !git && !crate::forge::may_sign_in(&server.config.state_dir, server.owner_session(request.headers()), request.headers()) {
        return crate::forge::watch_only();
    }
    server.forge.handle(server.config.forge_url.as_deref(), server.config.user_config.as_deref(), request).await
}

async fn not_found() -> Response {
    text(StatusCode::NOT_FOUND, "Not found.\n")
}

/// Routes the spec names whose services are not built yet in this release.
async fn not_built() -> Response {
    json_response(StatusCode::NOT_IMPLEMENTED, json!({ "error": "not available in this release of ling web" }))
}

/// A picture the image search service kept (specs/DREAMFERENCE_MIGHTLING_ASK.md §6): read-only,
/// from its store, by the only names it writes.
async fn image(State(server): State<Arc<Server>>, axum::extract::Path(path): axum::extract::Path<String>) -> Response {
    match sidecars::read_image(&server.config.images_dir, &path).await {
        Some(bytes) => {
            let mut response = (StatusCode::OK, [(header::CONTENT_TYPE, "image/jpeg")], bytes).into_response();
            // A stored image never changes under its name.
            response.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("private, max-age=604800, immutable"));
            response
        }
        None => not_found().await,
    }
}

/// A document a citation names (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §8.3), found by the
/// local file index inside its collection, never by a path from the request (documents.rs).
async fn document(State(server): State<Arc<Server>>, axum::extract::Path((collection, id)): axum::extract::Path<(String, String)>) -> Response {
    if !documents::valid(&collection, &id) {
        return not_found().await;
    }
    let located = match documents::locate(&server.config.launch, &collection, &id).await {
        Ok(located) => located,
        Err(reason) => return text(StatusCode::NOT_FOUND, &format!("{reason}\n")),
    };
    if located.size > documents::DOCUMENT_CAP {
        return text(StatusCode::PAYLOAD_TOO_LARGE, "This document is too large to open here.\n");
    }
    let bytes = match tokio::fs::read(&located.path).await {
        Ok(bytes) => bytes,
        Err(_) => return not_found().await,
    };
    let (content_type, inline, policy) = documents::presentation(&located.kind);
    let mut response = (StatusCode::OK, [(header::CONTENT_TYPE, content_type)], bytes).into_response();
    let headers = response.headers_mut();
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static(policy));
    if let Ok(value) = HeaderValue::from_str(&documents::disposition(inline, &located.rel)) {
        headers.insert(header::CONTENT_DISPOSITION, value);
    }
    response
}

/// Settings › Your documents: the collections and their state (`ling docs status --json`).
async fn docs_status(State(server): State<Arc<Server>>) -> Response {
    match documents::docs_cli(&server.config.launch, &["status", "--json"]).await {
        Ok(said) => match serde_json::from_str::<serde_json::Value>(&said) {
            Ok(value) => json_response(StatusCode::OK, value),
            Err(_) => json_response(StatusCode::BAD_GATEWAY, json!({ "error": "the local file index gave no answer" })),
        },
        Err(reason) => json_response(StatusCode::BAD_GATEWAY, json!({ "error": reason })),
    }
}

/// Settings › Your documents: add a folder (`{"folder": "/abs/path"}`), remove or rebuild a
/// collection (`{"name": "notes"}`), each through `ling docs`.
async fn docs_change(State(server): State<Arc<Server>>, axum::extract::Path(action): axum::extract::Path<String>, body: axum::body::Bytes) -> Response {
    let bad = |why: &str| json_response(StatusCode::BAD_REQUEST, json!({ "error": why }));
    let Ok(request) = serde_json::from_slice::<serde_json::Value>(&body) else { return bad("send JSON") };
    let field = |key: &str| request.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let (args, value) = match action.as_str() {
        "add" => {
            let folder = field("folder");
            if !folder.starts_with('/') || folder.len() > 4096 {
                return bad("name a folder by its full path");
            }
            ("add", folder)
        }
        "remove" | "reindex" => {
            let name = field("name");
            if !documents::valid_collection(&name) {
                return bad("not a collection");
            }
            (if action == "remove" { "remove" } else { "reindex" }, name)
        }
        _ => return not_found().await,
    };
    match documents::docs_cli(&server.config.launch, &[args, &value]).await {
        Ok(said) => json_response(StatusCode::OK, json!({ "said": said })),
        Err(reason) => json_response(StatusCode::UNPROCESSABLE_ENTITY, json!({ "error": reason })),
    }
}

/// The microphone's recording, turned into text by the speech-to-text service on this machine
/// (§7). The text goes back to the page, which puts it in the composer; nothing is sent to the model.
async fn transcribe(State(server): State<Arc<Server>>, request: Request) -> Response {
    let Some(media) = sidecars::audio_type(header_str(request.headers(), header::CONTENT_TYPE)) else {
        return json_response(StatusCode::UNSUPPORTED_MEDIA_TYPE, json!({ "error": "send the recording as audio/*" }));
    };
    let too_large = || json_response(StatusCode::PAYLOAD_TOO_LARGE, json!({ "error": format!("The limit is {} MB.", sidecars::AUDIO_CAP >> 20) }));
    let declared = header_str(request.headers(), header::CONTENT_LENGTH).and_then(|value| value.parse::<u64>().ok());
    if declared.is_some_and(|length| length > sidecars::AUDIO_CAP) {
        return too_large();
    }
    let mut audio = Vec::new();
    let mut stream = request.into_body().into_data_stream();
    while let Some(chunk) = stream.next().await {
        let Ok(chunk) = chunk else {
            return json_response(StatusCode::BAD_REQUEST, json!({ "error": "the recording did not arrive whole" }));
        };
        if (audio.len() + chunk.len()) as u64 > sidecars::AUDIO_CAP {
            return too_large();
        }
        audio.extend_from_slice(&chunk);
    }
    if audio.is_empty() {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "the recording is empty" }));
    }
    match sidecars::transcribe(&server.config.speech_addr, &audio, &media).await {
        Ok(text) => json_response(StatusCode::OK, json!({ "text": text })),
        Err(sidecars::SpeechError::Unavailable) => json_response(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({ "error": "Speech-to-text is not running on this machine: start it with `ling-admin voice start`." }),
        ),
        Err(sidecars::SpeechError::Failed(reason)) => {
            json_response(StatusCode::BAD_GATEWAY, json!({ "error": format!("Speech-to-text failed: {reason}") }))
        }
    }
}

/// Whether the page was opened from this node's bot by a paired Telegram user (telegram_app.rs).
/// Behind the credential like every route: the answer lets nothing in on its own.
async fn telegram_check(State(server): State<Arc<Server>>, body: axum::body::Bytes) -> Response {
    let request: serde_json::Value = serde_json::from_slice(&body).unwrap_or_default();
    let init_data = request.get("initData").and_then(|value| value.as_str()).unwrap_or_default();
    let verdict = match crate::telegram_app::bot(&server.config.codex_home) {
        None => Err(crate::telegram_app::Refusal::NoBot),
        Some((token, users)) => crate::telegram_app::check_launch(init_data, &token, &users, now_secs()),
    };
    match verdict {
        Ok(user) => json_response(StatusCode::OK, json!({ "ok": true, "user": { "id": user } })),
        Err(refusal) => json_response(StatusCode::OK, json!({ "ok": false, "reason": refusal.reason() })),
    }
}

/// First launch of the Mini App with no device credential (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md
/// §12.1 G2): requires the configured HTTPS front (`Host` and `Origin`), verifies `initData`
/// against `ling chat`'s bot and paired users, and records a pending request that only the bot's
/// `Approve` callback can turn into a `telegram-app` device credential.
async fn telegram_launch_pair_post(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if !server.front_host_ok(&headers) {
        return text(
            StatusCode::MISDIRECTED_REQUEST,
            "Telegram launch pairing is only served on the configured HTTPS front.\n",
        );
    }
    let host = header_str(&headers, header::HOST).unwrap_or_default().trim().to_ascii_lowercase();
    let expected_origin = format!("https://{host}");
    if header_str(&headers, header::ORIGIN).is_none_or(|origin| !origin.trim().eq_ignore_ascii_case(&expected_origin)) {
        return text(StatusCode::FORBIDDEN, "Requests from other sites are refused.\n");
    }
    let request: serde_json::Value = serde_json::from_slice(&body).unwrap_or_default();
    let init_data = request.get("initData").and_then(|value| value.as_str()).unwrap_or_default();
    let now = now_secs();
    let user = match crate::telegram_app::bot(&server.config.codex_home) {
        None => {
            return json_response(
                StatusCode::FORBIDDEN,
                json!({ "ok": false, "reason": crate::telegram_app::Refusal::NoBot.reason() }),
            );
        }
        Some((token, users)) => match crate::telegram_app::check_launch(init_data, &token, &users, now) {
            Ok(user) => user,
            Err(refusal) => {
                return json_response(StatusCode::FORBIDDEN, json!({ "ok": false, "reason": refusal.reason() }));
            }
        },
    };
    match crate::telegram_app::create_pending_pair(&server.config.state_dir, user, init_data, now) {
        Ok(pending) => json_response(StatusCode::OK, json!({ "pending": pending.id, "code": pending.code })),
        Err(err) if err.kind() == std::io::ErrorKind::InvalidInput => json_response(
            StatusCode::FORBIDDEN,
            json!({ "ok": false, "reason": err.to_string() }),
        ),
        Err(err) => json_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({ "ok": false, "reason": format!("Could not record the pairing request: {err}") }),
        ),
    }
}

/// Polled by the launching webview (`GET /api/telegram/launch-pair?pending=<id>`) with the same
/// `initData` in `X-Telegram-Init-Data`: hands out the `telegram-app` device cookie once after
/// the bot approves, and never before.
async fn telegram_launch_pair_get(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    if !server.front_host_ok(&headers) {
        return text(
            StatusCode::MISDIRECTED_REQUEST,
            "Telegram launch pairing is only served on the configured HTTPS front.\n",
        );
    }
    let id = query.get("pending").map(String::as_str).unwrap_or_default();
    let init_data = headers
        .get(crate::telegram_app::INIT_DATA_HEADER)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    let now = now_secs();
    let user = match crate::telegram_app::bot(&server.config.codex_home) {
        None => {
            return json_response(
                StatusCode::FORBIDDEN,
                json!({ "ok": false, "reason": crate::telegram_app::Refusal::NoBot.reason() }),
            );
        }
        Some((token, users)) => match crate::telegram_app::check_launch(init_data, &token, &users, now) {
            Ok(user) => user,
            Err(refusal) => {
                return json_response(StatusCode::FORBIDDEN, json!({ "ok": false, "reason": refusal.reason() }));
            }
        },
    };
    match crate::telegram_app::claim_pending_pair(&server.config.state_dir, id, user, init_data, now) {
        crate::telegram_app::ClaimOutcome::Pending => {
            json_response(StatusCode::OK, json!({ "status": "pending", "ok": false }))
        }
        crate::telegram_app::ClaimOutcome::Approved(token) => {
            let cookie = format!(
                "{}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={DEVICE_COOKIE_MAX_AGE}",
                auth::DEVICE_COOKIE
            );
            let mut response = json_response(StatusCode::OK, json!({ "status": "approved", "ok": true }));
            if let Ok(value) = HeaderValue::from_str(&cookie) {
                response.headers_mut().insert(header::SET_COOKIE, value);
            }
            response
        }
        crate::telegram_app::ClaimOutcome::Forbidden => json_response(
            StatusCode::FORBIDDEN,
            json!({ "ok": false, "reason": "This pairing request belongs to another launch." }),
        ),
        crate::telegram_app::ClaimOutcome::NotFound => json_response(
            StatusCode::NOT_FOUND,
            json!({ "ok": false, "status": "expired", "reason": "That pairing request has expired or was declined." }),
        ),
    }
}

/// Pending launch-pair requests waiting for the bot's prompt (`GET /api/telegram/pending-pairs`):
/// owner session or `ling chat` (`bridge=1`) session only.
async fn telegram_pending_pairs(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    if !server.owner_session(&headers) && !server.bridge_session(&headers) {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({ "error": "only the owner session or ling chat may list pending Telegram pairs" }),
        );
    }
    let pending = crate::telegram_app::list_pending_pairs(&server.config.state_dir, now_secs());
    json_response(StatusCode::OK, json!({ "pending": pending }))
}

/// Approves a pending launch-pair request (`POST /api/telegram/pending-pairs/approve {id}`):
/// marks the request approved so the launching webview can claim a `telegram-app` device once.
async fn telegram_pending_pairs_approve(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if !server.owner_session(&headers) && !server.bridge_session(&headers) {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({ "error": "only the owner session or ling chat may approve a Telegram pair" }),
        );
    }
    let request: serde_json::Value = serde_json::from_slice(&body).unwrap_or_default();
    let id = request.get("id").and_then(|value| value.as_str()).unwrap_or_default();
    match crate::telegram_app::approve_pending_pair(&server.config.state_dir, id, now_secs()) {
        Ok(true) => json_response(StatusCode::OK, json!({ "ok": true })),
        Ok(false) => json_response(StatusCode::NOT_FOUND, json!({ "ok": false, "error": "no such pending pair" })),
        Err(err) => json_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({ "ok": false, "error": format!("Could not approve the pairing request: {err}") }),
        ),
    }
}

/// Declines and removes a pending launch-pair request (`POST /api/telegram/pending-pairs/decline {id}`).
async fn telegram_pending_pairs_decline(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if !server.owner_session(&headers) && !server.bridge_session(&headers) {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({ "error": "only the owner session or ling chat may decline a Telegram pair" }),
        );
    }
    let request: serde_json::Value = serde_json::from_slice(&body).unwrap_or_default();
    let id = request.get("id").and_then(|value| value.as_str()).unwrap_or_default();
    match crate::telegram_app::decline_pending_pair(&server.config.state_dir, id, now_secs()) {
        Ok(true) => json_response(StatusCode::OK, json!({ "ok": true })),
        Ok(false) => json_response(StatusCode::NOT_FOUND, json!({ "ok": false, "error": "no such pending pair" })),
        Err(err) => json_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({ "ok": false, "error": format!("Could not decline the pairing request: {err}") }),
        ),
    }
}

async fn healthz(State(server): State<Arc<Server>>) -> Response {
    json_response(
        StatusCode::OK,
        json!({ "ok": true, "pid": std::process::id(), "lan": !server.config.lan_names.is_empty() }),
    )
}

async fn websocket(State(server): State<Arc<Server>>, headers: HeaderMap, upgrade: WebSocketUpgrade) -> Response {
    let device_name = server.paired_device_name(&headers);
    let bridge = server.is_bridge_caller(&headers);
    upgrade
        .max_message_size(64 << 20)
        .on_upgrade(move |socket| crate::relay::run_tab_with_bridge(socket, server, device_name, bridge))
}

/// The user-level air-gap level, for clients that cannot read the user's configuration: the Signal
/// bridge runs as its own system account with no access to the home folder, and must send nothing
/// at `on` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §11). No session's level is consulted: a client
/// asks about the machine, not about a thread.
async fn airgapped(State(_server): State<Arc<Server>>) -> Response {
    let resolved = user_airgap_level();
    json_response(StatusCode::OK, json!({ "level": resolved.level.name(), "source": resolved.source.label() }))
}

/// How the phone bridges behave, from the `[chat]` table of the user's configuration
/// (chat_settings.rs), for the Signal bridge, which cannot read the home folder.
async fn chat_settings(State(server): State<Arc<Server>>) -> Response {
    json_response(StatusCode::OK, crate::chat_settings::ChatSettings::load_from(server.config.user_config.as_deref()).to_json())
}

/// The files an Ask thread's agent changed since a time (`?thread=<id>&since=<ms>`), for a phone
/// bridge to offer back (ask_files.rs).
async fn ask_files(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let thread = query.get("thread").map(String::as_str).unwrap_or_default();
    let Some(folder) = ask::folder_of(&server.ask_root(), thread) else {
        return json_response(StatusCode::NOT_FOUND, json!({ "error": "not an Ask thread" }));
    };
    let since = query.get("since").and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
    json_response(StatusCode::OK, json!({ "files": crate::ask_files::changed_since(&folder, since) }))
}

/// One file of an Ask thread's folder (`?thread=<id>&name=<relative name>`), never at a user-level
/// `/airgapped on`: sent back, it would leave the machine (ask_files.rs).
async fn ask_file(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    if user_airgap_level().level == ling_airgapped::Level::On {
        return json_response(StatusCode::FORBIDDEN, json!({ "error": "The air gap is on, so no file is sent off this machine." }));
    }
    let thread = query.get("thread").map(String::as_str).unwrap_or_default();
    let Some(folder) = ask::folder_of(&server.ask_root(), thread) else {
        return json_response(StatusCode::NOT_FOUND, json!({ "error": "not an Ask thread" }));
    };
    let name = query.get("name").map(String::as_str).unwrap_or_default();
    let Some(path) = crate::ask_files::file_in(&folder, name) else {
        return json_response(StatusCode::NOT_FOUND, json!({ "error": "no such file in the thread's folder" }));
    };
    match tokio::fs::metadata(&path).await {
        Ok(metadata) if metadata.len() > crate::ask_files::MAX_FILE_BYTES => {
            return json_response(StatusCode::PAYLOAD_TOO_LARGE, json!({ "error": "the file is too large to send" }));
        }
        Err(err) => return json_response(StatusCode::NOT_FOUND, json!({ "error": err.to_string() })),
        _ => {}
    }
    match tokio::fs::read(&path).await {
        Ok(bytes) => ([(header::CONTENT_TYPE, "application/octet-stream")], bytes).into_response(),
        Err(err) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err.to_string() })),
    }
}

fn night_dir(server: &Server) -> PathBuf {
    server.config.codex_home.join("night")
}

fn night_candidates(server: &Server) -> Vec<PathBuf> {
    let user_config = server.config.user_config.as_deref();
    let settings = crate::chat_settings::ChatSettings::load_from(user_config);
    // `~` in `night_repos` is the home the file is under (`<home>/.config/dreamference/config.toml`).
    let home = user_config.and_then(|file| Some(file.parent()?.parent()?.parent()?.to_path_buf()));
    crate::night_phone::repos(&night_dir(server), &settings.night_repos, home.as_deref())
}

/// Where a phone's `/night add` may queue (night_phone.rs).
async fn night_repos(State(server): State<Arc<Server>>) -> Response {
    let repos: Vec<serde_json::Value> = night_candidates(&server)
        .iter()
        .map(|repo| json!({ "path": repo.to_string_lossy(), "name": crate::night_phone::name_of(repo) }))
        .collect();
    json_response(StatusCode::OK, json!({ "repos": repos }))
}

/// Queues a phone's task (`{repo, task}`), in one of the repositories `/api/night/repos` names,
/// with `ling night add` there; the bridge asked the owner first.
async fn night_add(State(server): State<Arc<Server>>, body: axum::body::Bytes) -> Response {
    let Ok(request) = serde_json::from_slice::<serde_json::Value>(&body) else {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "send {repo, task}" }));
    };
    let (Some(repo), Some(task)) = (request.get("repo").and_then(|v| v.as_str()), request.get("task").and_then(|v| v.as_str())) else {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "send {repo, task}" }));
    };
    if task.trim().is_empty() {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "the task is empty" }));
    }
    let repo = PathBuf::from(repo);
    if !night_candidates(&server).contains(&repo) {
        return json_response(StatusCode::FORBIDDEN, json!({ "error": "not a repository Night Shift may queue in from the phone" }));
    }
    let (queued, lines) = crate::night_phone::add(&server.config.launch, &repo, task).await;
    json_response(StatusCode::OK, json!({ "queued": queued, "lines": lines }))
}

/// The queue, for `/night status` on a phone.
async fn night_status(State(server): State<Arc<Server>>) -> Response {
    json_response(StatusCode::OK, json!({ "tasks": crate::night_phone::status(&night_dir(&server), std::time::SystemTime::now()) }))
}

/// The morning reports after `?after=<name>`, and the newest name, for the bridges' push.
async fn night_reports(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let after = query.get("after").map(String::as_str).unwrap_or_default();
    let (reports, latest) = crate::night_phone::reports_after(&night_dir(&server), after);
    json_response(StatusCode::OK, json!({ "reports": reports, "latest": latest }))
}

/// The air-gap level without a session: `DREAMFERENCE_MIGHTLING_AIRGAPPED`, then the strictest of
/// the file `DREAMFERENCE_CONFIG_PATH` names and the user-level file.
pub fn user_airgap_level() -> ling_airgapped::Resolved {
    let environment = std::env::var(ling_airgapped::ENV_VAR).ok();
    let mut files = Vec::new();
    if let Some(path) = std::env::var_os("DREAMFERENCE_CONFIG_PATH").filter(|path| !path.is_empty()) {
        files.push(PathBuf::from(path));
    }
    if let Some(user) = ling_airgapped::user_config_file()
        && !files.contains(&user)
    {
        files.push(user);
    }
    let configs: Vec<(PathBuf, String)> = files.into_iter().filter_map(|path| std::fs::read_to_string(&path).ok().map(|text| (path, text))).collect();
    ling_airgapped::resolve_from(None, environment.as_deref(), &configs)
}

/// Whether `/airgapped` is `on` for a thread (the configured level for none), as the launcher
/// resolves it (`ling_airgapped::resolve`, what `ling airgapped --thread` prints): read in this
/// process's folder and, when the thread's own folder `cwd` is known, in that one too, the stricter
/// winning, so a repository whose `dreamference.toml` asks for `on` is not missed.
pub fn thread_airgapped_on(thread: Option<&str>, cwd: Option<&std::path::Path>) -> bool {
    let ids = [thread.unwrap_or_default()];
    ling_airgapped::resolve(&ids).level == ling_airgapped::Level::On
        || cwd.is_some_and(|cwd| {
            ling_airgapped::resolve_in(&ids, std::env::var(ling_airgapped::ENV_VAR).ok(), cwd).level == ling_airgapped::Level::On
        })
}

/// `ling web open`'s link: trades the one-time code for a session cookie.
async fn login(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let code = query.get("code").map(String::as_str).unwrap_or_default();
    if !auth::take_login_code(&server.config.state_dir, code) {
        return html(StatusCode::UNAUTHORIZED, assets::LOGIN_FAILED_HTML);
    }
    let bridge = query.get("bridge").map(String::as_str) == Some("1");
    let token = server.new_session_with(bridge);
    redirect_with_cookie(format!("{}={token}; HttpOnly; SameSite=Strict; Path=/", auth::SESSION_COOKIE))
}

fn redirect_with_cookie(cookie: String) -> Response {
    let mut response = (StatusCode::SEE_OTHER, [(header::LOCATION, "/")]).into_response();
    if let Ok(value) = HeaderValue::from_str(&cookie) {
        response.headers_mut().insert(header::SET_COOKIE, value);
    }
    response
}

/// The pairing form, prefilled when the link carries a code (the Devices page's QR code does). A
/// GET never pairs anything: the person still presses Pair, so a link preview cannot spend a code.
async fn pair_page(Query(query): Query<HashMap<String, String>>) -> Response {
    let code = query.get("code").map(String::as_str).filter(|code| devices::is_pairing_code(code));
    html(StatusCode::OK, &assets::pair_html_with(None, code))
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// The Devices page: this machine's session sees the paired devices and can pair or revoke one;
/// a paired device is told to do that on the Mightling machine.
async fn devices_page(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    if !server.owner_session(&headers) {
        return html(StatusCode::FORBIDDEN, &devices::device_page());
    }
    let state = &server.config.state_dir;
    html(StatusCode::OK, &devices::owner_page(&auth::load_devices(state), &server.config.lan_names, None, None, now_secs()))
}

/// A new pairing code, shown with its QR codes. Answered with the page itself (not a redirect),
/// so the code is never in a URL on this machine.
async fn devices_pair(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    if !server.owner_session(&headers) {
        return html(StatusCode::FORBIDDEN, &devices::device_page());
    }
    let state = &server.config.state_dir;
    let lan_names = &server.config.lan_names;
    if lan_names.is_empty() {
        return html(StatusCode::OK, &devices::owner_page(&auth::load_devices(state), lan_names, None, None, now_secs()));
    }
    match auth::issue_pairing_code(state) {
        Ok(code) => {
            let pairing = devices::Pairing { code: &code, port: server.config.port, lan_names };
            html(StatusCode::OK, &devices::owner_page(&auth::load_devices(state), lan_names, Some(&pairing), None, now_secs()))
        }
        Err(err) => {
            let notice = format!("Could not write a pairing code: {err}");
            html(StatusCode::INTERNAL_SERVER_ERROR, &devices::owner_page(&auth::load_devices(state), lan_names, None, Some(&notice), now_secs()))
        }
    }
}

/// Revokes one device by its id; its next request is refused.
async fn devices_revoke(State(server): State<Arc<Server>>, headers: HeaderMap, Form(form): Form<HashMap<String, String>>) -> Response {
    if !server.owner_session(&headers) {
        return html(StatusCode::FORBIDDEN, &devices::device_page());
    }
    let state = &server.config.state_dir;
    let id = form.get("device").map(String::as_str).unwrap_or_default();
    let known = auth::load_devices(state).iter().any(|device| device.id == id);
    let notice = if !known {
        Some("No paired device has that id.".to_string())
    } else {
        match auth::revoke_device(state, id) {
            Ok(_) => None,
            Err(err) => Some(format!("Could not revoke it: {err}")),
        }
    };
    if notice.is_none() {
        return (StatusCode::SEE_OTHER, [(header::LOCATION, "/devices")]).into_response();
    }
    html(StatusCode::OK, &devices::owner_page(&auth::load_devices(state), &server.config.lan_names, None, notice.as_deref(), now_secs()))
}

/// Narrows a device to watching other devices' threads, or gives it control again (SYNC §5.1).
async fn devices_scope(State(server): State<Arc<Server>>, headers: HeaderMap, Form(form): Form<HashMap<String, String>>) -> Response {
    if !server.owner_session(&headers) {
        return html(StatusCode::FORBIDDEN, &devices::device_page());
    }
    let state = &server.config.state_dir;
    let id = form.get("device").map(String::as_str).unwrap_or_default();
    let notice = match form.get("scope").map(String::as_str).and_then(auth::Scope::parse) {
        None => Some("A scope is control or watch.".to_string()),
        Some(scope) => match auth::set_scope(state, id, scope) {
            Ok(changed) if changed.is_empty() => Some("No paired device has that id.".to_string()),
            Ok(_) => None,
            Err(err) => Some(format!("Could not change it: {err}")),
        },
    };
    if notice.is_none() {
        return (StatusCode::SEE_OTHER, [(header::LOCATION, "/devices")]).into_response();
    }
    html(StatusCode::OK, &devices::owner_page(&auth::load_devices(state), &server.config.lan_names, None, notice.as_deref(), now_secs()))
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
    server.pairing_failed();
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
/// With `read=1` (the phone bridges), an image's text is read too (ocr.rs) and the answer adds
/// `text` (null when none was read) and `vision`, whether the served model takes images, so a
/// bridge knows what the model can be given (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16).
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
    drop(file);
    let mut answer = json!({ "path": path.to_string_lossy(), "bytes": written });
    if query.get("read").map(String::as_str) == Some("1") {
        let text = if query.get("kind").map(String::as_str) == Some("image") { crate::ocr::read_text(&path).await } else { None };
        answer["text"] = json!(text);
        answer["vision"] = json!(server.served_model_takes_images());
    }
    json_response(StatusCode::OK, answer)
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
    // Full Access ends within one interval of the node taking it away, not at the thread's next turn.
    let mut stopped = stop.subscribe();
    let sweeper = server.clone();
    tasks.push(tokio::spawn(async move {
        let mut tick = tokio::time::interval(crate::chat_repos::FULL_ACCESS_SWEEP_INTERVAL);
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                _ = stopped.recv() => break,
                // Inside the branch, so stopping does not wait for a sweep's app-server answers.
                _ = async {
                    tick.tick().await;
                    crate::chat_repos::sweep_full_access(&sweeper).await;
                } => {}
            }
        }
        Ok(())
    }));
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

    #[tokio::test]
    async fn bridge_login_session_cannot_start_default_thread_while_owner_session_can() {
        let dir = std::env::temp_dir().join(format!("ling-web-server-bridge-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let codex_home = dir.join("codex");
        let state_dir = codex_home.join("web");
        std::fs::create_dir_all(&state_dir).unwrap();

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = Server::new(Config {
            codex_home: codex_home.clone(),
            state_dir: state_dir.clone(),
            port,
            lan_names: Vec::new(),
            public_names: Vec::new(),
            socket: dir.join("app.sock"),
            launch: Launch {
                ling: PathBuf::from("/bin/true"),
                env: Vec::new(),
                log: dir.join("app-server.log"),
            },
            images_dir: dir.join("images"),
            speech_addr: "127.0.0.1:9".to_string(),
            user_config: None,
            forge_url: None,
        })
        .unwrap();

        let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
        let server_task = {
            let server = server.clone();
            tokio::spawn(async move {
                let _ = serve(server, vec![listener], async move {
                    let _ = stop_rx.await;
                })
                .await;
            })
        };

        let bridge_cookie = crate::ask_client::sign_in(&state_dir, port).await.unwrap();
        let owner_cookie = crate::ask_client::sign_in_with(&state_dir, port, false).await.unwrap();

        let mut bridge_headers = HeaderMap::new();
        bridge_headers.insert(header::COOKIE, HeaderValue::from_str(&bridge_cookie).unwrap());
        assert!(server.owner_session(&bridge_headers));
        assert!(server.bridge_session(&bridge_headers));
        assert!(server.is_bridge_caller(&bridge_headers));

        let mut owner_headers = HeaderMap::new();
        owner_headers.insert(header::COOKIE, HeaderValue::from_str(&owner_cookie).unwrap());
        assert!(server.owner_session(&owner_headers));
        assert!(!server.bridge_session(&owner_headers));
        assert!(!server.is_bridge_caller(&owner_headers));

        let ask_root = server.ask_root();
        std::fs::create_dir_all(&ask_root).unwrap();
        let start_default = json!({
            "id": 1,
            "method": "thread/start",
            "params": { "prompt": "default", "cwd": "/work/repo" }
        });

        let bridge_prompts = ask::MessagePrompts {
            root: &ask_root,
            composed: None,
            created: Mutex::new(None),
            airgapped_on: false,
            thread_folder: None,
        }
        .with_bridge(server.is_bridge_caller(&bridge_headers));
        let bridge_res = server.policy.vet_outgoing(&start_default, &mut HashSet::new(), &bridge_prompts);
        assert!(bridge_res.is_err(), "bridge=1 session must not start a default thread: {bridge_res:?}");

        let owner_prompts = ask::MessagePrompts {
            root: &ask_root,
            composed: None,
            created: Mutex::new(None),
            airgapped_on: false,
            thread_folder: None,
        }
        .with_bridge(server.is_bridge_caller(&owner_headers));
        let owner_res = server.policy.vet_outgoing(&start_default, &mut HashSet::new(), &owner_prompts);
        assert!(owner_res.is_ok(), "session without bridge=1 must be able to start a default thread: {owner_res:?}");

        let _ = stop_tx.send(());
        let _ = server_task.await;
        let _ = std::fs::remove_dir_all(&dir);
    }
}
