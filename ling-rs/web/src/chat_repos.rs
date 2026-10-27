//! The phone repository service (`specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md` §3, §4, §5.4, §9, §14):
//! settings from `[chat_repos]`, the node's Forgejo client, bare clones and per-thread worktrees
//! under `$CODEX_HOME/chat-repos/`, and the `/api/repos` read and checkout routes.

use std::collections::HashMap;
use std::path::Component;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;
use std::time::Instant;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use axum::Router;
use axum::body::Bytes;
use axum::extract::Query;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::http::StatusCode;
use axum::http::header;
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::get;
use axum::routing::post;
use serde_json::Value;
use serde_json::json;
use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;
use tokio::process::Command;

use crate::auth;
use crate::server::Server;
use crate::server::header_str;

/// Default base URL of the node's Forgejo service when `server::Config::forge_url` is `None`.
pub const DEFAULT_FORGE_URL: &str = "http://127.0.0.1:3200";
/// File in `state_dir` holding the `mightling-phone` account's token (`0600`).
pub const TOKEN_FILE: &str = "forge-phone-token";
/// Helper script written once into `state_dir` (`0700`) for `GIT_ASKPASS`.
pub const ASKPASS_SCRIPT: &str = "forge-askpass";
/// Empty directory in `state_dir` passed as `core.hooksPath` to every `git` command.
pub const EMPTY_HOOKS_DIR: &str = "empty-hooks";
/// How long the repository listing is cached in memory.
pub const LISTING_TTL: Duration = Duration::from_secs(60);
/// Timeout for the forge readiness probe (`GET /api/v1/version`).
pub const VERSION_TIMEOUT: Duration = Duration::from_secs(3);
/// Timeout for forge API requests (`GET /api/v1/repos/search`).
pub const FORGE_TIMEOUT: Duration = Duration::from_secs(10);
/// Timeout for the `systemctl --user` SWE-bench unit check.
pub const SYSTEMCTL_TIMEOUT: Duration = Duration::from_secs(2);

/// One binary gigabyte (`1024^3` bytes).
pub const GB: u64 = 1024 * 1024 * 1024;
/// Normal free-space floor on the filesystem holding `chat-repos/` (150 GB).
pub const DISK_FLOOR_BYTES: u64 = 150 * GB;
/// Free-space floor while a SWE-bench night run is active (175 GB).
pub const DISK_FLOOR_NIGHT_BYTES: u64 = 175 * GB;
/// Required headroom above the disk floor for opening a new checkout (5 GB).
pub const DISK_HEADROOM_BYTES: u64 = 5 * GB;

/// Default bridges permitted to use `/repo`.
pub const DEFAULT_BRIDGES: &[&str] = &["signal", "matrix", "telegram", "discord"];
/// Bridges recognised by `POST /api/repos/open`.
pub const KNOWN_BRIDGES: &[&str] = &["signal", "matrix", "telegram", "discord", "web"];

/// Settings from the `[chat_repos]` table of `~/.config/dreamference/config.toml`
/// (`specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md` §14).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatReposSettings {
    pub enabled: bool,
    pub bridges: Vec<String>,
    pub allow_main: Vec<String>,
    pub full_access: bool,
    pub max_checkouts: usize,
    pub idle_days: u64,
    pub tokens_per_day: u64,
    pub work_answer_seconds: u64,
    pub work_tool_calls: u64,
    pub work_command_seconds: u64,
}

impl Default for ChatReposSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            bridges: DEFAULT_BRIDGES.iter().map(|s| (*s).to_string()).collect(),
            allow_main: Vec::new(),
            full_access: false,
            max_checkouts: 30,
            idle_days: 14,
            tokens_per_day: 3_000_000,
            work_answer_seconds: 1800,
            work_tool_calls: 200,
            work_command_seconds: 600,
        }
    }
}

impl ChatReposSettings {
    /// Reads `[chat_repos]` from the user's configuration file, or returns defaults when absent.
    pub fn load() -> Self {
        Self::load_from(ling_airgapped::user_config_file().as_deref())
    }

    /// Reads `[chat_repos]` from `path`, or returns defaults when `path` is `None` or unreadable.
    pub fn load_from(path: Option<&Path>) -> Self {
        let Some(path) = path else { return Self::default() };
        let Ok(text) = std::fs::read_to_string(path) else { return Self::default() };
        Self::from_toml(&text)
    }

    /// Parses `[chat_repos]` from TOML text; missing or ill-typed keys fall back to defaults.
    pub fn from_toml(text: &str) -> Self {
        let mut settings = Self::default();
        let Ok(root) = text.parse::<toml::Table>() else { return settings };
        let Some(table) = root.get("chat_repos").and_then(toml::Value::as_table) else { return settings };

        if let Some(enabled) = table.get("enabled").and_then(toml::Value::as_bool) {
            settings.enabled = enabled;
        }
        if let Some(items) = table.get("bridges").and_then(toml::Value::as_array) {
            let parsed: Option<Vec<String>> = items
                .iter()
                .map(|v| v.as_str().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()))
                .collect();
            if let Some(bridges) = parsed {
                settings.bridges = bridges;
            }
        }
        if let Some(items) = table.get("allow_main").and_then(toml::Value::as_array) {
            let parsed: Option<Vec<String>> = items
                .iter()
                .map(|v| v.as_str().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()))
                .collect();
            if let Some(allow_main) = parsed {
                settings.allow_main = allow_main;
            }
        }
        if let Some(full_access) = table.get("full_access").and_then(toml::Value::as_bool) {
            settings.full_access = full_access;
        }
        if let Some(max_checkouts) = table.get("max_checkouts").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.max_checkouts = max_checkouts as usize;
        }
        if let Some(idle_days) = table.get("idle_days").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.idle_days = idle_days as u64;
        }
        if let Some(tokens_per_day) = table.get("tokens_per_day").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.tokens_per_day = tokens_per_day as u64;
        }
        if let Some(work_answer_seconds) = table.get("work_answer_seconds").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.work_answer_seconds = work_answer_seconds as u64;
        }
        if let Some(work_tool_calls) = table.get("work_tool_calls").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.work_tool_calls = work_tool_calls as u64;
        }
        if let Some(work_command_seconds) = table.get("work_command_seconds").and_then(toml::Value::as_integer).filter(|&n| n >= 0) {
            settings.work_command_seconds = work_command_seconds as u64;
        }
        settings
    }
}

/// Host readings needed before opening a new worktree (`libc::statvfs` free bytes and whether a
/// SWE-bench night unit is active), placed behind a trait so tests stub them without touching the
/// machine or running `systemctl`.
pub trait HostProbe: Send + Sync {
    /// Available bytes on the filesystem containing `path`.
    fn free_bytes(&self, path: &Path) -> Result<u64, String>;
    /// Whether any `ling-swe-*` user unit is currently active.
    fn swe_night_running(&self) -> bool;
}

/// Production host probe using `libc::statvfs` and `systemctl --user`.
pub struct SystemHostProbe;

impl HostProbe for SystemHostProbe {
    fn free_bytes(&self, path: &Path) -> Result<u64, String> {
        statvfs_free_bytes(path)
    }

    fn swe_night_running(&self) -> bool {
        systemctl_swe_active()
    }
}

/// Free bytes available to an unprivileged process on the filesystem holding `path`.
pub fn statvfs_free_bytes(path: &Path) -> Result<u64, String> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let mut check = path.to_path_buf();
    while !check.exists() {
        let Some(parent) = check.parent() else { break };
        if parent.as_os_str().is_empty() {
            break;
        }
        check = parent.to_path_buf();
    }
    let c_path = CString::new(check.as_os_str().as_bytes()).map_err(|_| "invalid path".to_string())?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) };
    if rc != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let block_size = if stat.f_frsize > 0 { stat.f_frsize as u64 } else { stat.f_bsize as u64 };
    Ok((stat.f_bavail as u64).saturating_mul(block_size))
}

fn systemctl_swe_active() -> bool {
    let mut child = match std::process::Command::new("systemctl")
        .args(["--user", "list-units", "--state=active", "--no-legend", "ling-swe-*"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return false,
    };
    let deadline = Instant::now() + SYSTEMCTL_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return false;
                }
                let Some(mut stdout) = child.stdout.take() else { return false };
                let mut buf = String::new();
                if std::io::Read::read_to_string(&mut stdout, &mut buf).is_err() {
                    return false;
                }
                return buf.lines().any(|line| !line.trim().is_empty());
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return false;
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(_) => return false,
        }
    }
}

/// One repository returned by the forge's search endpoint.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ForgeRepo {
    pub repo: String,
    pub default_branch: String,
    pub pushed_at: String,
    pub private: bool,
    pub archived: bool,
    pub mirror: bool,
    pub read_only: bool,
    pub clone_url: String,
}

/// One checkout entry stored in `$CODEX_HOME/chat-repos/index.json`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CheckoutRecord {
    pub thread: String,
    pub repo: String,
    pub bridge: String,
    pub chat: String,
    pub device: String,
    pub dir: String,
    pub branch: Option<String>,
    pub base: String,
    pub app_thread: Option<String>,
    pub read_only: bool,
    pub created: u64,
    pub last_used: u64,
}

impl CheckoutRecord {
    pub fn to_json(&self) -> Value {
        json!({
            "thread": self.thread,
            "repo": self.repo,
            "bridge": self.bridge,
            "chat": self.chat,
            "device": self.device,
            "dir": self.dir,
            "branch": self.branch,
            "base": self.base,
            "app_thread": self.app_thread,
            "read_only": self.read_only,
            "created": self.created,
            "last_used": self.last_used,
        })
    }

    pub fn from_json(value: &Value) -> Option<Self> {
        Some(Self {
            thread: value.get("thread")?.as_str()?.to_string(),
            repo: value.get("repo")?.as_str()?.to_string(),
            bridge: value.get("bridge")?.as_str()?.to_string(),
            chat: value.get("chat")?.as_str()?.to_string(),
            device: value.get("device")?.as_str()?.to_string(),
            dir: value.get("dir")?.as_str()?.to_string(),
            branch: value.get("branch").and_then(Value::as_str).map(str::to_string),
            base: value.get("base")?.as_str()?.to_string(),
            app_thread: value.get("app_thread").and_then(Value::as_str).map(str::to_string),
            read_only: value.get("read_only")?.as_bool()?,
            created: value.get("created")?.as_u64()?,
            last_used: value.get("last_used")?.as_u64()?,
        })
    }
}

struct CachedListing {
    at: Instant,
    forge_url: String,
    repos: Vec<ForgeRepo>,
}

/// Runtime state held by `Server` for `/api/repos`: the 60-second listing cache, per-repository
/// git locks, `index.json` lock, and host probe.
pub struct ChatRepos {
    listing: tokio::sync::Mutex<Option<CachedListing>>,
    repo_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    index_lock: Mutex<()>,
    probe: Mutex<Arc<dyn HostProbe>>,
    allow_file_protocol: std::sync::atomic::AtomicBool,
}

impl Default for ChatRepos {
    fn default() -> Self {
        Self::new()
    }
}

impl ChatRepos {
    /// Creates a new repository service state with the default system probe.
    pub fn new() -> Self {
        Self {
            listing: tokio::sync::Mutex::new(None),
            repo_locks: Mutex::new(HashMap::new()),
            index_lock: Mutex::new(()),
            probe: Mutex::new(Arc::new(SystemHostProbe)),
            allow_file_protocol: std::sync::atomic::AtomicBool::new(cfg!(test)),
        }
    }

    /// Replaces the host probe (used by tests to stub free disk space and SWE-bench activity).
    pub fn set_probe(&self, probe: Arc<dyn HostProbe>) {
        *self.probe.lock().unwrap_or_else(|p| p.into_inner()) = probe;
    }

    /// Enables or disables `-c protocol.file.allow=always` on git commands (for local bare repo clones in tests).
    pub fn set_allow_file_protocol(&self, allow: bool) {
        self.allow_file_protocol.store(allow, std::sync::atomic::Ordering::Relaxed);
    }

    fn probe(&self) -> Arc<dyn HostProbe> {
        self.probe.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    fn repo_mutex(&self, repo: &str) -> Arc<tokio::sync::Mutex<()>> {
        let mut locks = self.repo_locks.lock().unwrap_or_else(|p| p.into_inner());
        locks.entry(repo.to_string()).or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))).clone()
    }

    /// Clears the in-memory listing cache.
    pub async fn invalidate_listing_cache(&self) {
        *self.listing.lock().await = None;
    }
}

/// Router for `/api/repos*`, merged into `server::router`.
pub fn routes() -> Router<Arc<Server>> {
    Router::new()
        .route("/api/repos", get(list_repos))
        .route("/api/repos/open", post(open_repo))
        .route("/api/repos/state", get(repo_state))
        .route("/api/repos/diff", get(repo_diff))
        .route("/api/repos/log", get(repo_log))
}

fn json_response(status: StatusCode, value: Value) -> Response {
    (status, [(header::CONTENT_TYPE, "application/json")], value.to_string()).into_response()
}

fn service_off_response() -> Response {
    json_response(
        StatusCode::NOT_FOUND,
        json!({ "error": "The repository service is off: run `ling-admin forge on`." }),
    )
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Root folder for phone checkouts (`$CODEX_HOME/chat-repos`).
pub fn root_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("chat-repos")
}

/// Path of `index.json` under `$CODEX_HOME/chat-repos`.
pub fn index_path(codex_home: &Path) -> PathBuf {
    root_dir(codex_home).join("index.json")
}

/// Reads `$CODEX_HOME/chat-repos/index.json` (empty list when missing).
pub fn load_index(codex_home: &Path) -> Vec<CheckoutRecord> {
    let Ok(text) = std::fs::read_to_string(index_path(codex_home)) else { return Vec::new() };
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&text) else { return Vec::new() };
    items.iter().filter_map(CheckoutRecord::from_json).collect()
}

/// Atomically writes `$CODEX_HOME/chat-repos/index.json` via a temp file + rename (`0600`).
pub fn save_index(codex_home: &Path, records: &[CheckoutRecord]) -> std::io::Result<()> {
    let root = root_dir(codex_home);
    auth::private_dir(&root)?;
    let array: Vec<Value> = records.iter().map(CheckoutRecord::to_json).collect();
    let text = serde_json::to_string_pretty(&array).map_err(std::io::Error::other)?;
    auth::write_private(&index_path(codex_home), &text)
}

/// Checks whether `name` has the syntax `<org>/<repo>` and is not a URL or filesystem path.
pub fn valid_repo_name(name: &str) -> bool {
    if name.is_empty()
        || name.contains("://")
        || name.starts_with('/')
        || name.starts_with('~')
        || name.contains("..")
        || name.contains('\\')
        || name.contains('\0')
    {
        return false;
    }
    let mut parts = name.split('/');
    let (Some(org), Some(repo), None) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    let valid_part = |seg: &str| {
        !seg.is_empty()
            && seg != "."
            && seg != ".."
            && seg.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
    };
    valid_part(org) && valid_part(repo)
}

/// Checks whether `thread` is an 8-character lowercase hexadecimal identifier.
pub fn valid_thread_id(thread: &str) -> bool {
    thread.len() == 8 && thread.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Checks whether `rel` is a safe relative path inside a worktree (not absolute, no `..`).
pub fn valid_worktree_rel_path(rel: &str) -> bool {
    if rel.is_empty() || rel.starts_with('/') || rel.starts_with('~') || rel.contains('\0') {
        return false;
    }
    let path = Path::new(rel);
    if path.is_absolute() {
        return false;
    }
    for comp in path.components() {
        match comp {
            Component::Normal(part) => {
                if part.is_empty() {
                    return false;
                }
            }
            _ => return false,
        }
    }
    true
}

/// Resolves the caller's device identifier and refuses any `watch`-scoped device with 403.
fn require_control_caller(server: &Server, headers: &HeaderMap) -> Result<String, Response> {
    if server.owner_session(headers) {
        return Ok("owner".to_string());
    }
    let Some(cookie) = auth::cookie(header_str(headers, header::COOKIE), auth::DEVICE_COOKIE) else {
        return Err(json_response(StatusCode::UNAUTHORIZED, json!({ "error": "not signed in" })));
    };
    let Some(device) = auth::device_for_token(&server.config.state_dir, cookie) else {
        return Err(json_response(StatusCode::UNAUTHORIZED, json!({ "error": "not signed in" })));
    };
    if device.scope == auth::Scope::Watch {
        return Err(json_response(
            StatusCode::FORBIDDEN,
            json!({ "error": "This device has watch scope only; repository access requires control scope." }),
        ));
    }
    Ok(device.id)
}

fn forge_base_url(server: &Server) -> String {
    server
        .config
        .forge_url
        .as_deref()
        .unwrap_or(DEFAULT_FORGE_URL)
        .trim_end_matches('/')
        .to_string()
}

fn read_phone_token(state_dir: &Path) -> Option<String> {
    let text = std::fs::read_to_string(state_dir.join(TOKEN_FILE)).ok()?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// Parses an `http://host:port` base URL into `(connect_addr, host_header)`.
/// Refuses `https://` and any other scheme or path suffix.
pub fn parse_http_base_url(base_url: &str) -> Result<(String, String), String> {
    let trimmed = base_url.trim().trim_end_matches('/');
    let Some(rest) = trimmed.strip_prefix("http://") else {
        return Err("forge URL must use http://".to_string());
    };
    if rest.is_empty() || rest.contains('/') || rest.contains('@') || rest.contains('?') || rest.contains('#') {
        return Err("forge URL must be http://host:port with no path or credentials".to_string());
    }
    let host_header = rest.to_string();
    let connect_addr = if rest.starts_with('[') {
        if rest.contains("]:") {
            rest.to_string()
        } else {
            format!("{rest}:80")
        }
    } else if rest.contains(':') {
        rest.to_string()
    } else {
        format!("{rest}:80")
    };
    Ok((connect_addr, host_header))
}

/// Performs an HTTP/1.1 `GET` to the forge with `Authorization: token <token>` and `Connection: close`.
/// Never includes `token` in any error message.
pub async fn forge_get(base_url: &str, path_and_query: &str, token: &str, timeout: Duration) -> Result<(u16, Vec<u8>), String> {
    let (connect_addr, host_header) = parse_http_base_url(base_url)?;
    let path = if path_and_query.starts_with('/') {
        path_and_query.to_string()
    } else {
        format!("/{path_and_query}")
    };
    tokio::time::timeout(timeout, async move {
        let mut stream = TcpStream::connect(&connect_addr)
            .await
            .map_err(|_| format!("could not connect to forge at {connect_addr}"))?;
        let request = format!(
            "GET {path} HTTP/1.1\r\nHost: {host_header}\r\nAuthorization: token {token}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
        );
        stream
            .write_all(request.as_bytes())
            .await
            .map_err(|_| "failed to send request to forge".to_string())?;
        let mut raw = Vec::new();
        stream
            .read_to_end(&mut raw)
            .await
            .map_err(|_| "failed to read response from forge".to_string())?;
        parse_http_response(&raw)
    })
    .await
    .map_err(|_| "forge request timed out".to_string())?
}

/// Parses a raw HTTP/1.1 response supporting both `Content-Length` and `Transfer-Encoding: chunked`.
pub fn parse_http_response(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let Some(split) = raw.windows(4).position(|w| w == b"\r\n\r\n") else {
        return Err("incomplete HTTP header from forge".to_string());
    };
    let head = String::from_utf8_lossy(&raw[..split]);
    let body = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status_line = lines.next().ok_or_else(|| "missing status line".to_string())?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or_else(|| "invalid status code from forge".to_string())?;

    let mut chunked = false;
    let mut content_length: Option<usize> = None;
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            if name.trim().eq_ignore_ascii_case("transfer-encoding")
                && value.to_ascii_lowercase().contains("chunked")
            {
                chunked = true;
            } else if name.trim().eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse::<usize>().ok();
            }
        }
    }

    if chunked {
        Ok((status, decode_chunked(body)))
    } else if let Some(len) = content_length {
        if body.len() < len {
            return Err("truncated HTTP body from forge".to_string());
        }
        Ok((status, body[..len].to_vec()))
    } else {
        Ok((status, body.to_vec()))
    }
}

fn decode_chunked(mut input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    while let Some(end) = input.windows(2).position(|w| w == b"\r\n") {
        let header = String::from_utf8_lossy(&input[..end]);
        let size_hex = header.split(';').next().unwrap_or("").trim();
        let Ok(size) = usize::from_str_radix(size_hex, 16) else { break };
        input = &input[end + 2..];
        if size == 0 || input.len() < size {
            break;
        }
        out.extend_from_slice(&input[..size]);
        input = &input[size..];
        if input.starts_with(b"\r\n") {
            input = &input[2..];
        }
    }
    out
}

/// Checks that `[chat_repos] enabled` is true, the phone token exists, and `GET /api/v1/version`
/// answers HTTP 200 within 3 seconds. Returns `(settings, base_url, token)` or a 404 response.
async fn ensure_service_ready(server: &Server) -> Result<(ChatReposSettings, String, String), Response> {
    let settings = ChatReposSettings::load_from(server.config.user_config.as_deref());
    if !settings.enabled {
        return Err(service_off_response());
    }
    let Some(token) = read_phone_token(&server.config.state_dir) else {
        return Err(service_off_response());
    };
    let base_url = forge_base_url(server);
    match forge_get(&base_url, "/api/v1/version", &token, VERSION_TIMEOUT).await {
        Ok((200, _)) => Ok((settings, base_url, token)),
        _ => Err(service_off_response()),
    }
}

/// Fetches all pages from `GET /api/v1/repos/search?limit=50&page=N`.
pub async fn fetch_forge_repos(base_url: &str, token: &str) -> Result<Vec<ForgeRepo>, String> {
    let mut all = Vec::new();
    let mut page = 1usize;
    loop {
        let path = format!("/api/v1/repos/search?limit=50&page={page}");
        let (status, body) = forge_get(base_url, &path, token, FORGE_TIMEOUT).await?;
        if status != 200 {
            return Err(format!("forge returned HTTP {status} for repository search"));
        }
        let parsed: Value = serde_json::from_slice(&body).map_err(|_| "forge returned invalid JSON".to_string())?;
        let items = parsed
            .get("data")
            .and_then(Value::as_array)
            .or_else(|| parsed.as_array())
            .ok_or_else(|| "forge search response missing data array".to_string())?;
        let count = items.len();
        for item in items {
            if let Some(repo) = parse_forge_repo(base_url, item) {
                all.push(repo);
            }
        }
        if count < 50 {
            break;
        }
        page += 1;
        if page > 200 {
            break;
        }
    }
    Ok(all)
}

fn parse_forge_repo(base_url: &str, item: &Value) -> Option<ForgeRepo> {
    let full_name = item
        .get("full_name")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            let owner = item.get("owner")?.get("login")?.as_str()?;
            let name = item.get("name")?.as_str()?;
            Some(format!("{owner}/{name}"))
        })?;
    if !valid_repo_name(&full_name) {
        return None;
    }
    let default_branch = item
        .get("default_branch")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("main")
        .to_string();
    let pushed_at = item
        .get("updated_at")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let private = item.get("private").and_then(Value::as_bool).unwrap_or(false);
    let archived = item.get("archived").and_then(Value::as_bool).unwrap_or(false);
    let mirror = item.get("mirror").and_then(Value::as_bool).unwrap_or(false);
    let can_push = item
        .get("permissions")
        .and_then(|p| p.get("push"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let read_only = archived || !can_push;
    let clone_url = item
        .get("clone_url")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(strip_url_credentials)
        .unwrap_or_else(|| format!("{}/{full_name}.git", base_url.trim_end_matches('/')));
    Some(ForgeRepo {
        repo: full_name,
        default_branch,
        pushed_at,
        private,
        archived,
        mirror,
        read_only,
        clone_url,
    })
}

/// Strips any `user:pass@` userinfo from an `http://` or `https://` URL so credentials are never
/// stored in `remote.origin.url`.
pub fn strip_url_credentials(url: &str) -> String {
    for prefix in ["http://", "https://"] {
        if let Some(rest) = url.strip_prefix(prefix) {
            let authority_end = rest.find('/').unwrap_or(rest.len());
            let authority = &rest[..authority_end];
            if let Some(at) = authority.rfind('@') {
                return format!("{prefix}{}{}", &authority[at + 1..], &rest[authority_end..]);
            }
        }
    }
    url.to_string()
}

/// Returns the cached repository listing (up to 60s old) or fetches a fresh one from the forge.
async fn get_or_fetch_listing(server: &Server, base_url: &str, token: &str) -> Result<Vec<ForgeRepo>, String> {
    let mut guard = server.chat_repos.listing.lock().await;
    if let Some(cached) = guard.as_ref()
        && cached.forge_url == base_url
        && cached.at.elapsed() < LISTING_TTL
    {
        return Ok(cached.repos.clone());
    }
    let repos = fetch_forge_repos(base_url, token).await?;
    *guard = Some(CachedListing {
        at: Instant::now(),
        forge_url: base_url.to_string(),
        repos: repos.clone(),
    });
    Ok(repos)
}

/// Prepares the `0700` `GIT_ASKPASS` script and empty hooks folder in `state_dir`.
/// The script reads `<state_dir>/forge-phone-token` at runtime and never embeds the token.
pub fn ensure_git_helpers(state_dir: &Path) -> std::io::Result<(PathBuf, PathBuf)> {
    auth::private_dir(state_dir)?;
    let hooks_dir = state_dir.join(EMPTY_HOOKS_DIR);
    auth::private_dir(&hooks_dir)?;

    let askpass_path = state_dir.join(ASKPASS_SCRIPT);
    let script = format!(
        "#!/bin/sh\ncase \"$1\" in\nUsername*) printf 'mightling-phone\\n' ;;\n*) tr -d '\\r\\n' < \"$(dirname \"$0\")/{TOKEN_FILE}\"; printf '\\n' ;;\nesac\n"
    );
    if std::fs::read_to_string(&askpass_path).ok().as_deref() != Some(script.as_str()) {
        auth::write_private(&askpass_path, &script)?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&askpass_path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok((askpass_path, hooks_dir))
}

/// Runs `git` with a clean environment (`PATH`, `HOME`, `LANG=C`, `GIT_TERMINAL_PROMPT=0`,
/// `GIT_ASKPASS`), `-c core.hooksPath=<empty>`, and no inherited `GIT_*` variables.
async fn run_git(
    server: &Server,
    cwd: Option<&Path>,
    args: &[&str],
    with_askpass: bool,
) -> Result<std::process::Output, String> {
    let (askpass, hooks_dir) = ensure_git_helpers(&server.config.state_dir).map_err(|e| e.to_string())?;
    let hooks_arg = format!("core.hooksPath={}", hooks_dir.display());

    let mut cmd = Command::new("git");
    cmd.env_clear();
    if let Some(path) = std::env::var_os("PATH") {
        cmd.env("PATH", path);
    }
    cmd.env("HOME", &server.config.codex_home);
    cmd.env("LANG", "C");
    cmd.env("LC_ALL", "C");
    cmd.env("GIT_TERMINAL_PROMPT", "0");
    cmd.env("GIT_CONFIG_NOSYSTEM", "1");
    if with_askpass {
        cmd.env("GIT_ASKPASS", &askpass);
    }
    cmd.arg("-c").arg(&hooks_arg);
    if server.chat_repos.allow_file_protocol.load(std::sync::atomic::Ordering::Relaxed) {
        cmd.arg("-c").arg("protocol.file.allow=always");
    }
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    cmd.args(args);
    cmd.stdin(std::process::Stdio::null());
    let output = cmd.output().await.map_err(|e| format!("failed to run git: {e}"))?;
    Ok(output)
}

fn sanitize_error(text: &str, state_dir: &Path) -> String {
    if let Some(token) = read_phone_token(state_dir)
        && !token.is_empty()
    {
        return text.replace(&token, "[redacted]");
    }
    text.to_string()
}

async fn run_git_checked(
    server: &Server,
    cwd: Option<&Path>,
    args: &[&str],
    with_askpass: bool,
) -> Result<String, String> {
    let output = run_git(server, cwd, args, with_askpass).await?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let detail = if !stderr.trim().is_empty() { stderr.trim() } else { stdout.trim() };
        return Err(sanitize_error(detail, &server.config.state_dir));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// `GET /api/repos`: the forge's repositories visible to the phone token, cached for 60 seconds.
async fn list_repos(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    let caller_device = match require_control_caller(&server, &headers) {
        Ok(device) => device,
        Err(resp) => return resp,
    };
    let (_settings, base_url, token) = match ensure_service_ready(&server).await {
        Ok(ready) => ready,
        Err(resp) => return resp,
    };
    let repos = match get_or_fetch_listing(&server, &base_url, &token).await {
        Ok(repos) => repos,
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    };
    let index = load_index(&server.config.codex_home);
    let items: Vec<Value> = repos
        .iter()
        .map(|r| {
            let checkouts: Vec<String> = index
                .iter()
                .filter(|c| c.repo == r.repo && c.device == caller_device)
                .map(|c| c.thread.clone())
                .collect();
            json!({
                "repo": r.repo,
                "default_branch": r.default_branch,
                "pushed_at": r.pushed_at,
                "private": r.private,
                "archived": r.archived,
                "mirror": r.mirror,
                "read_only": r.read_only,
                "checkouts": checkouts,
            })
        })
        .collect();
    json_response(StatusCode::OK, Value::Array(items))
}

/// `POST /api/repos/open`: opens or reuses a worktree for `{repo, bridge, chat, thread?}`.
async fn open_repo(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let caller_device = match require_control_caller(&server, &headers) {
        Ok(device) => device,
        Err(resp) => return resp,
    };
    let (settings, base_url, token) = match ensure_service_ready(&server).await {
        Ok(ready) => ready,
        Err(resp) => return resp,
    };
    let Ok(request) = serde_json::from_slice::<Value>(&body) else {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "send JSON {repo, bridge, chat, thread?}" }));
    };
    let repo_name = request.get("repo").and_then(Value::as_str).unwrap_or("").trim();
    let bridge = request.get("bridge").and_then(Value::as_str).unwrap_or("").trim();
    let chat = request.get("chat").and_then(Value::as_str).unwrap_or("").trim();
    let requested_thread = request
        .get("thread")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    if !valid_repo_name(repo_name) {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "Invalid repository name: expected <org>/<name>, never a URL or path." }),
        );
    }
    if !KNOWN_BRIDGES.contains(&bridge) {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "Unknown bridge: must be signal, matrix, telegram, discord, or web." }),
        );
    }
    if bridge != "web" && !settings.bridges.iter().any(|b| b == bridge) {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({ "error": format!("Bridge `{bridge}` is not enabled in [chat_repos] bridges.") }),
        );
    }
    if chat.is_empty() {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "chat is required" }));
    }
    if let Some(tid) = requested_thread
        && !valid_thread_id(tid)
    {
        return json_response(StatusCode::BAD_REQUEST, json!({ "error": "invalid thread id" }));
    }

    let listing = match get_or_fetch_listing(&server, &base_url, &token).await {
        Ok(repos) => repos,
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    };
    let Some(forge_repo) = listing.into_iter().find(|r| r.repo == repo_name) else {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": format!("Repository `{repo_name}` is not in the forge listing.") }),
        );
    };

    let repo_lock = server.chat_repos.repo_mutex(&forge_repo.repo);
    let _repo_guard = repo_lock.lock().await;

    let chat_repos_root = root_dir(&server.config.codex_home);
    if let Err(err) = auth::private_dir(&chat_repos_root) {
        return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err.to_string() }));
    }

    let mut records = load_index(&server.config.codex_home);
    let existing_idx = if let Some(tid) = requested_thread {
        let Some(idx) = records.iter().position(|r| r.thread == tid && r.repo == forge_repo.repo) else {
            return json_response(
                StatusCode::NOT_FOUND,
                json!({ "error": format!("No checkout `{tid}` for `{}`.", forge_repo.repo) }),
            );
        };
        Some(idx)
    } else {
        records
            .iter()
            .position(|r| r.repo == forge_repo.repo && r.bridge == bridge && r.chat == chat)
    };

    if existing_idx.is_none() {
        if records.len() >= settings.max_checkouts {
            return json_response(
                StatusCode::CONFLICT,
                json!({ "error": format!("Maximum checkouts ({}) reached on this node.", settings.max_checkouts) }),
            );
        }
        let probe = server.chat_repos.probe();
        let night_active = probe.swe_night_running();
        let floor = if night_active { DISK_FLOOR_NIGHT_BYTES } else { DISK_FLOOR_BYTES };
        let required = floor + DISK_HEADROOM_BYTES;
        match probe.free_bytes(&chat_repos_root) {
            Ok(free) if free < required => {
                return json_response(
                    StatusCode::INSUFFICIENT_STORAGE,
                    json!({
                        "error": format!(
                            "Free disk space is below the repository floor ({} GB required{}).",
                            required / GB,
                            if night_active { " while a SWE-bench night runs" } else { "" }
                        )
                    }),
                );
            }
            Err(err) => {
                return json_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    json!({ "error": format!("Could not check free disk space: {err}") }),
                );
            }
            Ok(_) => {}
        }
    }

    let base_git = chat_repos_root.join(&forge_repo.repo).join("base.git");
    if let Err(err) = sync_base_clone(&server, &base_git, &forge_repo).await {
        return json_response(StatusCode::BAD_GATEWAY, json!({ "error": err }));
    }

    let now = now_secs();
    let record = if let Some(idx) = existing_idx {
        let mut rec = records[idx].clone();
        rec.last_used = now;
        rec.read_only = forge_repo.read_only;
        rec.base = forge_repo.default_branch.clone();
        if forge_repo.read_only {
            rec.branch = None;
        }
        let worktree_dir = PathBuf::from(&rec.dir);
        if !worktree_dir.join(".git").exists() {
            if let Err(err) = ensure_worktree(&server, &base_git, &worktree_dir, &rec.thread, &forge_repo, rec.branch.as_deref()).await {
                return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
            }
        }
        records[idx] = rec.clone();
        rec
    } else {
        let thread = unique_thread_id(&records);
        let worktree_dir = chat_repos_root
            .join(&forge_repo.repo)
            .join(format!("{bridge}-{thread}"));
        let branch = if forge_repo.read_only {
            None
        } else {
            Some(format!("phone/{thread}"))
        };
        if let Err(err) = ensure_worktree(&server, &base_git, &worktree_dir, &thread, &forge_repo, branch.as_deref()).await {
            return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
        }
        let rec = CheckoutRecord {
            thread,
            repo: forge_repo.repo.clone(),
            bridge: bridge.to_string(),
            chat: chat.to_string(),
            device: caller_device,
            dir: worktree_dir.to_string_lossy().into_owned(),
            branch,
            base: forge_repo.default_branch.clone(),
            app_thread: None,
            read_only: forge_repo.read_only,
            created: now,
            last_used: now,
        };
        records.push(rec.clone());
        rec
    };

    let worktree_path = PathBuf::from(&record.dir);
    let notes = inspect_and_init_submodules_and_lfs(&server, &worktree_path, &base_url, &forge_repo).await;

    {
        let _idx_guard = server.chat_repos.index_lock.lock().unwrap_or_else(|p| p.into_inner());
        if let Err(err) = save_index(&server.config.codex_home, &records) {
            return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err.to_string() }));
        }
    }

    json_response(
        StatusCode::OK,
        json!({
            "thread": record.thread,
            "cwd": record.dir,
            "branch": record.branch,
            "read_only": record.read_only,
            "notes": notes,
        }),
    )
}

fn unique_thread_id(existing: &[CheckoutRecord]) -> String {
    for _ in 0..64 {
        let candidate = auth::random_hex(4);
        if !existing.iter().any(|r| r.thread == candidate) {
            return candidate;
        }
    }
    auth::random_hex(4)
}

/// Creates `<org>/<repo>/base.git` with `git clone --bare --no-recurse-submodules` on first open,
/// sets `remote.origin.url` to the forge's plain clone URL, and runs `git fetch` on every open.
async fn sync_base_clone(server: &Server, base_git: &Path, forge_repo: &ForgeRepo) -> Result<(), String> {
    if let Some(parent) = base_git.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let plain_url = strip_url_credentials(&forge_repo.clone_url);
    if !base_git.join("HEAD").exists() {
        let base_str = base_git.to_string_lossy().into_owned();
        run_git_checked(
            server,
            None,
            &["clone", "--bare", "--no-recurse-submodules", "--", &plain_url, &base_str],
            true,
        )
        .await?;
    }
    run_git_checked(server, Some(base_git), &["config", "remote.origin.url", &plain_url], false).await?;
    let refspec_heads = format!("+refs/heads/{0}:refs/heads/{0}", forge_repo.default_branch);
    let _ = run_git_checked(
        server,
        Some(base_git),
        &["fetch", "--prune", "--update-head-ok", "origin", &refspec_heads, "+refs/heads/*:refs/remotes/origin/*"],
        true,
    )
    .await?;
    Ok(())
}

/// Adds or restores the linked worktree from `base.git`.
async fn ensure_worktree(
    server: &Server,
    base_git: &Path,
    worktree_dir: &Path,
    thread: &str,
    forge_repo: &ForgeRepo,
    branch: Option<&str>,
) -> Result<(), String> {
    let _ = run_git(server, Some(base_git), &["worktree", "prune"], false).await;
    if worktree_dir.exists() {
        let _ = std::fs::remove_dir_all(worktree_dir);
    }
    if let Some(parent) = worktree_dir.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let dir_str = worktree_dir.to_string_lossy().into_owned();
    let tip = format!("refs/heads/{}", forge_repo.default_branch);
    if forge_repo.read_only || branch.is_none() {
        run_git_checked(
            server,
            Some(base_git),
            &["worktree", "add", "--detach", &dir_str, &tip],
            false,
        )
        .await?;
    } else {
        let branch_name = branch.map(str::to_string).unwrap_or_else(|| format!("phone/{thread}"));
        let branch_ref = format!("refs/heads/{branch_name}");
        let exists = run_git(server, Some(base_git), &["show-ref", "--verify", "--quiet", &branch_ref], false)
            .await
            .map(|o| o.status.success())
            .unwrap_or(false);
        if exists {
            run_git_checked(
                server,
                Some(base_git),
                &["worktree", "add", &dir_str, &branch_name],
                false,
            )
            .await?;
        } else {
            run_git_checked(
                server,
                Some(base_git),
                &["worktree", "add", "-b", &branch_name, &dir_str, &tip],
                false,
            )
            .await?;
        }
    }
    Ok(())
}

#[derive(Clone, Debug)]
struct SubmoduleSpec {
    name: String,
    path: String,
    url: String,
}

/// Parses `.gitmodules` entries into `(name, path, url)`.
fn parse_gitmodules(text: &str) -> Vec<SubmoduleSpec> {
    let mut out = Vec::new();
    let mut current_name: Option<String> = None;
    let mut current_path: Option<String> = None;
    let mut current_url: Option<String> = None;

    let flush = |out: &mut Vec<SubmoduleSpec>, name: &mut Option<String>, path: &mut Option<String>, url: &mut Option<String>| {
        if let (Some(n), Some(p), Some(u)) = (name.take(), path.take(), url.take()) {
            out.push(SubmoduleSpec { name: n, path: p, url: u });
        } else {
            *name = None;
            *path = None;
            *url = None;
        }
    };

    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            flush(&mut out, &mut current_name, &mut current_path, &mut current_url);
            let inner = &line[1..line.len() - 1].trim();
            if let Some(rest) = inner.strip_prefix("submodule") {
                let name = rest.trim().trim_matches('"').trim();
                if !name.is_empty() {
                    current_name = Some(name.to_string());
                }
            }
            continue;
        }
        if current_name.is_some()
            && let Some((k, v)) = line.split_once('=')
        {
            let key = k.trim();
            let val = v.trim().trim_matches('"').to_string();
            if key.eq_ignore_ascii_case("path") {
                current_path = Some(val);
            } else if key.eq_ignore_ascii_case("url") {
                current_url = Some(val);
            }
        }
    }
    flush(&mut out, &mut current_name, &mut current_path, &mut current_url);
    out
}

fn rewrite_forge_submodule_url(sub_url: &str, base_url: &str, parent_repo: &ForgeRepo) -> Option<String> {
    let base = base_url.trim_end_matches('/');
    let trimmed = sub_url.trim();
    if trimmed.starts_with(base) {
        let suffix = trimmed.strip_prefix(base).unwrap_or("");
        if suffix.is_empty() || suffix.starts_with('/') {
            return Some(strip_url_credentials(trimmed));
        }
    }
    if trimmed.starts_with("./") || trimmed.starts_with("../") {
        let parent_clone = strip_url_credentials(&parent_repo.clone_url);
        if let Some((parent_dir, _)) = parent_clone.trim_end_matches('/').rsplit_once('/') {
            let mut parts: Vec<&str> = parent_dir.split('/').collect();
            for seg in trimmed.split('/') {
                match seg {
                    "." | "" => {}
                    ".." => {
                        if parts.len() > 3 {
                            parts.pop();
                        }
                    }
                    other => parts.push(other),
                }
            }
            return Some(parts.join("/"));
        }
    }
    None
}

async fn inspect_and_init_submodules_and_lfs(
    server: &Server,
    worktree_dir: &Path,
    base_url: &str,
    parent_repo: &ForgeRepo,
) -> Vec<String> {
    let mut notes = Vec::new();
    let gitmodules_path = worktree_dir.join(".gitmodules");
    if let Ok(text) = std::fs::read_to_string(&gitmodules_path) {
        for sub in parse_gitmodules(&text) {
            if !valid_worktree_rel_path(&sub.path) {
                notes.push(format!("submodule {} has an invalid path and was skipped", sub.path));
                continue;
            }
            if let Some(rewritten_url) = rewrite_forge_submodule_url(&sub.url, base_url, parent_repo) {
                let _ = run_git_checked(server, Some(worktree_dir), &["submodule", "init", "--", &sub.path], false).await;
                let config_key = format!("submodule.{}.url", sub.name);
                let _ = run_git_checked(server, Some(worktree_dir), &["config", &config_key, &rewritten_url], false).await;
                if let Err(err) = run_git_checked(
                    server,
                    Some(worktree_dir),
                    &["submodule", "update", "--no-fetch", "--", &sub.path],
                    true,
                )
                .await
                {
                    if let Err(err2) = run_git_checked(
                        server,
                        Some(worktree_dir),
                        &["submodule", "update", "--", &sub.path],
                        true,
                    )
                    .await
                    {
                        notes.push(format!("submodule {} could not be updated: {err}; {err2}", sub.path));
                    }
                }
            } else {
                notes.push(format!("submodule {} points outside the forge and was left empty", sub.path));
            }
        }
    }

    let gitattributes_path = worktree_dir.join(".gitattributes");
    if let Ok(attrs) = std::fs::read_to_string(&gitattributes_path)
        && attrs.contains("filter=lfs")
    {
        notes.push("Git LFS objects are not fetched on open (.gitattributes mentions filter=lfs).".to_string());
    }
    notes
}

fn find_checked_out_thread(server: &Server, thread: &str) -> Result<(CheckoutRecord, PathBuf), Response> {
    if !valid_thread_id(thread) {
        return Err(json_response(StatusCode::NOT_FOUND, json!({ "error": "no such checkout thread" })));
    }
    let root = root_dir(&server.config.codex_home);
    let records = load_index(&server.config.codex_home);
    let Some(record) = records.into_iter().find(|r| r.thread == thread) else {
        return Err(json_response(StatusCode::NOT_FOUND, json!({ "error": "no such checkout thread" })));
    };
    let dir = PathBuf::from(&record.dir);
    if !dir.starts_with(&root) || !dir.exists() {
        return Err(json_response(StatusCode::NOT_FOUND, json!({ "error": "checkout directory is missing" })));
    }
    Ok((record, dir))
}

/// `GET /api/repos/state?thread=`: branch, ahead/behind against `base.git`'s default branch tip,
/// uncommitted file stat list, `read_only`, and `private`.
async fn repo_state(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    if let Err(resp) = require_control_caller(&server, &headers) {
        return resp;
    }
    let (_settings, base_url, token) = match ensure_service_ready(&server).await {
        Ok(ready) => ready,
        Err(resp) => return resp,
    };
    let thread = query.get("thread").map(String::as_str).unwrap_or("");
    let (record, worktree_dir) = match find_checked_out_thread(&server, thread) {
        Ok(found) => found,
        Err(resp) => return resp,
    };

    let private = get_or_fetch_listing(&server, &base_url, &token)
        .await
        .ok()
        .and_then(|list| list.into_iter().find(|r| r.repo == record.repo).map(|r| r.private))
        .unwrap_or(false);

    let (ahead, behind) = ahead_behind(&server, &worktree_dir, &record.base).await;
    let uncommitted = match uncommitted_stats(&server, &worktree_dir).await {
        Ok(stats) => stats,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };

    json_response(
        StatusCode::OK,
        json!({
            "repo": record.repo,
            "branch": record.branch,
            "base": record.base,
            "ahead": ahead,
            "behind": behind,
            "uncommitted": uncommitted,
            "read_only": record.read_only,
            "private": private,
        }),
    )
}

async fn ahead_behind(server: &Server, worktree_dir: &Path, base_branch: &str) -> (u64, u64) {
    let range = format!("refs/heads/{base_branch}...HEAD");
    let Ok(out) = run_git_checked(server, Some(worktree_dir), &["rev-list", "--left-right", "--count", &range], false).await else {
        return (0, 0);
    };
    let mut parts = out.split_whitespace();
    let behind = parts.next().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
    let ahead = parts.next().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
    (ahead, behind)
}

async fn uncommitted_stats(server: &Server, worktree_dir: &Path) -> Result<Vec<Value>, String> {
    let numstat = run_git_checked(server, Some(worktree_dir), &["diff", "--numstat", "HEAD", "--"], false).await?;
    let mut entries = parse_numstat(&numstat);
    let mut seen: std::collections::HashSet<String> = entries
        .iter()
        .filter_map(|v| v.get("path").and_then(Value::as_str).map(str::to_string))
        .collect();

    let untracked = run_git_checked(
        server,
        Some(worktree_dir),
        &["ls-files", "--others", "--exclude-standard"],
        false,
    )
    .await?;
    for line in untracked.lines() {
        let rel = line.trim();
        if rel.is_empty() || seen.contains(rel) || !valid_worktree_rel_path(rel) {
            continue;
        }
        let added = count_file_lines(&worktree_dir.join(rel));
        seen.insert(rel.to_string());
        entries.push(json!({
            "path": rel,
            "added": added,
            "removed": 0u64,
        }));
    }
    Ok(entries)
}

fn parse_numstat(output: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for line in output.lines() {
        let mut parts = line.splitn(3, '\t');
        let (Some(add_str), Some(rem_str), Some(path)) = (parts.next(), parts.next(), parts.next()) else {
            continue;
        };
        let added = add_str.trim().parse::<u64>().unwrap_or(0);
        let removed = rem_str.trim().parse::<u64>().unwrap_or(0);
        let clean_path = path.trim();
        if !clean_path.is_empty() {
            out.push(json!({
                "path": clean_path,
                "added": added,
                "removed": removed,
            }));
        }
    }
    out
}

fn count_file_lines(path: &Path) -> u64 {
    let Ok(bytes) = std::fs::read(path) else { return 0 };
    if bytes.is_empty() {
        return 0;
    }
    let newlines = bytes.iter().filter(|&&b| b == b'\n').count() as u64;
    if bytes.last() == Some(&b'\n') {
        newlines
    } else {
        newlines + 1
    }
}

async fn merge_base(server: &Server, worktree_dir: &Path, base_branch: &str) -> Result<String, String> {
    let base_ref = format!("refs/heads/{base_branch}");
    let out = run_git_checked(server, Some(worktree_dir), &["merge-base", &base_ref, "HEAD"], false).await?;
    Ok(out.trim().to_string())
}

/// `GET /api/repos/diff?thread=&scope=uncommitted|branch&path=`: without `path`, a stat list
/// `[{path, added, removed}]`; with `path`, that file's unified diff text (`{path, diff}`).
async fn repo_diff(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    if let Err(resp) = require_control_caller(&server, &headers) {
        return resp;
    }
    if let Err(resp) = ensure_service_ready(&server).await {
        return resp;
    }
    let thread = query.get("thread").map(String::as_str).unwrap_or("");
    let scope = query.get("scope").map(String::as_str).unwrap_or("uncommitted");
    if scope == "turn" {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "the turn scope comes from the app-server" }),
        );
    }
    if scope != "uncommitted" && scope != "branch" {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "scope must be uncommitted, branch, or turn" }),
        );
    }
    let (record, worktree_dir) = match find_checked_out_thread(&server, thread) {
        Ok(found) => found,
        Err(resp) => return resp,
    };

    let file_path = query.get("path").map(String::as_str).filter(|s| !s.is_empty());
    if let Some(rel) = file_path {
        if !valid_worktree_rel_path(rel) {
            return json_response(
                StatusCode::BAD_REQUEST,
                json!({ "error": "path must be a relative path inside the worktree" }),
            );
        }
        let candidate = worktree_dir.join(rel);
        if let Ok(canon_wt) = worktree_dir.canonicalize()
            && let Ok(canon_file) = candidate.canonicalize()
            && !canon_file.starts_with(&canon_wt)
        {
            return json_response(
                StatusCode::BAD_REQUEST,
                json!({ "error": "path escapes the worktree" }),
            );
        }

        let diff_text = if scope == "uncommitted" {
            match uncommitted_file_diff(&server, &worktree_dir, rel).await {
                Ok(text) => text,
                Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
            }
        } else {
            let mb = match merge_base(&server, &worktree_dir, &record.base).await {
                Ok(mb) => mb,
                Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
            };
            let range = format!("{mb}..HEAD");
            match run_git_checked(&server, Some(&worktree_dir), &["diff", &range, "--", rel], false).await {
                Ok(text) => text,
                Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
            }
        };
        return json_response(StatusCode::OK, json!({ "path": rel, "diff": diff_text }));
    }

    if scope == "uncommitted" {
        match uncommitted_stats(&server, &worktree_dir).await {
            Ok(stats) => json_response(StatusCode::OK, Value::Array(stats)),
            Err(err) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
        }
    } else {
        let mb = match merge_base(&server, &worktree_dir, &record.base).await {
            Ok(mb) => mb,
            Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
        };
        let range = format!("{mb}..HEAD");
        match run_git_checked(&server, Some(&worktree_dir), &["diff", "--numstat", &range, "--"], false).await {
            Ok(out) => json_response(StatusCode::OK, Value::Array(parse_numstat(&out))),
            Err(err) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
        }
    }
}

async fn uncommitted_file_diff(server: &Server, worktree_dir: &Path, rel: &str) -> Result<String, String> {
    let tracked_diff = run_git_checked(server, Some(worktree_dir), &["diff", "HEAD", "--", rel], false).await?;
    if !tracked_diff.is_empty() {
        return Ok(tracked_diff);
    }
    let untracked = run_git_checked(
        server,
        Some(worktree_dir),
        &["ls-files", "--others", "--exclude-standard", "--", rel],
        false,
    )
    .await?;
    if !untracked.trim().is_empty() {
        let output = run_git(server, Some(worktree_dir), &["diff", "--no-index", "--", "/dev/null", rel], false).await?;
        return Ok(String::from_utf8_lossy(&output.stdout).into_owned());
    }
    Ok(String::new())
}

/// `GET /api/repos/log?thread=`: the branch's commits since the merge-base with the default branch.
async fn repo_log(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    if let Err(resp) = require_control_caller(&server, &headers) {
        return resp;
    }
    if let Err(resp) = ensure_service_ready(&server).await {
        return resp;
    }
    let thread = query.get("thread").map(String::as_str).unwrap_or("");
    let (record, worktree_dir) = match find_checked_out_thread(&server, thread) {
        Ok(found) => found,
        Err(resp) => return resp,
    };
    let mb = match merge_base(&server, &worktree_dir, &record.base).await {
        Ok(mb) => mb,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };
    let range = format!("{mb}..HEAD");
    let out = match run_git_checked(
        &server,
        Some(&worktree_dir),
        &["log", "--format=%H%x1f%s%x1f%an%x1f%ct", &range],
        false,
    )
    .await
    {
        Ok(out) => out,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };

    let mut commits = Vec::new();
    for line in out.lines() {
        let mut fields = line.splitn(4, '\x1f');
        let (Some(id), Some(subject), Some(author), Some(time_str)) =
            (fields.next(), fields.next(), fields.next(), fields.next())
        else {
            continue;
        };
        let time = time_str.trim().parse::<u64>().unwrap_or(0);
        commits.push(json!({
            "id": id,
            "subject": subject,
            "author": author,
            "time": time,
        }));
    }
    json_response(StatusCode::OK, Value::Array(commits))
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::AtomicBool;
    use std::sync::atomic::AtomicU64;
    use std::sync::atomic::Ordering;

    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;

    use super::*;
    use crate::app_server::Launch;
    use crate::server;
    use crate::server::Config;

    const HOST: &str = "127.0.0.1:3100";
    const ORIGIN: &str = "http://127.0.0.1:3100";
    const TEST_TOKEN: &str = "forge-secret-token-99887766";

    struct Scratch {
        dir: PathBuf,
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn scratch(tag: &str) -> Scratch {
        let dir = PathBuf::from("/tmp").join(format!("lw-repos-{tag}-{}-{}", std::process::id(), auth::random_hex(3)));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Scratch { dir }
    }

    struct StubProbe {
        free: AtomicU64,
        night: AtomicBool,
    }

    impl StubProbe {
        fn new(free: u64, night: bool) -> Arc<Self> {
            Arc::new(Self {
                free: AtomicU64::new(free),
                night: AtomicBool::new(night),
            })
        }
    }

    impl HostProbe for StubProbe {
        fn free_bytes(&self, _path: &Path) -> Result<u64, String> {
            Ok(self.free.load(Ordering::Relaxed))
        }

        fn swe_night_running(&self) -> bool {
            self.night.load(Ordering::Relaxed)
        }
    }

    fn git_sync(cwd: &Path, args: &[&str]) {
        let status = std::process::Command::new("git")
            .env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("HOME", cwd)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .args(["-c", "protocol.file.allow=always"])
            .current_dir(cwd)
            .args(args)
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?} failed in {}", cwd.display());
    }

    /// Creates a bare repository populated with an initial commit on `main`.
    fn make_bare_repo(root: &Path, org_repo: &str) -> PathBuf {
        let bare = root.join("forge-bares").join(format!("{org_repo}.git"));
        std::fs::create_dir_all(&bare).unwrap();
        git_sync(&bare, &["init", "--bare", "--initial-branch=main"]);

        let work = root.join("seed-work").join(org_repo.replace('/', "_"));
        std::fs::create_dir_all(&work).unwrap();
        git_sync(&work, &["init", "--initial-branch=main"]);
        git_sync(&work, &["config", "user.email", "dev@127.0.0.1"]);
        git_sync(&work, &["config", "user.name", "dev"]);
        std::fs::write(work.join("README.md"), "# Demo\nline two\n").unwrap();
        git_sync(&work, &["add", "README.md"]);
        git_sync(&work, &["commit", "-m", "initial commit"]);
        git_sync(&work, &["remote", "add", "origin", bare.to_str().unwrap()]);
        git_sync(&work, &["push", "origin", "main"]);
        bare
    }

    /// Starts an in-process Forgejo API stand-in on `127.0.0.1:0` serving `/api/v1/version`
    /// and `/api/v1/repos/search`.
    async fn start_fake_forge(repos_json: Value) -> (String, tokio::task::JoinHandle<()>) {
        let app = Router::new()
            .route(
                "/api/v1/version",
                get(|headers: HeaderMap| async move {
                    let auth_hdr = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()).unwrap_or("");
                    if auth_hdr != format!("token {TEST_TOKEN}") {
                        return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                    }
                    (StatusCode::OK, [("content-type", "application/json")], "{\"version\":\"1.22.0\"}").into_response()
                }),
            )
            .route(
                "/api/v1/repos/search",
                get(move |headers: HeaderMap, Query(q): Query<HashMap<String, String>>| {
                    let data = repos_json.clone();
                    async move {
                        let auth_hdr = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()).unwrap_or("");
                        if auth_hdr != format!("token {TEST_TOKEN}") {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let page = q.get("page").and_then(|p| p.parse::<usize>().ok()).unwrap_or(1);
                        let body = if page == 1 {
                            json!({ "ok": true, "data": data })
                        } else {
                            json!({ "ok": true, "data": [] })
                        };
                        (StatusCode::OK, [("content-type", "application/json")], body.to_string()).into_response()
                    }
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        (format!("http://127.0.0.1:{}", addr.port()), handle)
    }

    fn test_server(dir: &Path, forge_url: Option<String>, write_token: bool) -> (Arc<Server>, Arc<StubProbe>) {
        let codex_home = dir.join("home");
        let state_dir = auth::state_dir(&codex_home);
        std::fs::create_dir_all(&state_dir).unwrap();
        if write_token {
            auth::write_private(&state_dir.join(TOKEN_FILE), &format!("{TEST_TOKEN}\n")).unwrap();
        }
        let user_config = dir.join("user/.config/dreamference/config.toml");
        if let Some(parent) = user_config.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        let cfg = Config {
            codex_home,
            state_dir: state_dir.clone(),
            port: 3100,
            lan_names: Vec::new(),
            public_names: Vec::new(),
            socket: dir.join("s.sock"),
            launch: Launch {
                ling: dir.join("ling"),
                env: Vec::new(),
                log: state_dir.join("app-server.log"),
            },
            images_dir: dir.join("images"),
            speech_addr: "127.0.0.1:9".to_string(),
            user_config: Some(user_config),
            forge_url,
        };
        let srv = Server::new(cfg).unwrap();
        let probe = StubProbe::new(500 * GB, false);
        srv.chat_repos.set_probe(probe.clone());
        (srv, probe)
    }

    async fn call(server: &Arc<Server>, req: Request<Body>) -> (StatusCode, String) {
        let response = server::router(server.clone()).oneshot(req).await.unwrap();
        let status = response.status();
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
        (status, String::from_utf8_lossy(&bytes).into_owned())
    }

    async fn owner_cookie(server: &Arc<Server>) -> String {
        let code = auth::issue_login_code(&server.config.state_dir).unwrap();
        let req = Request::builder()
            .uri(format!("/login?code={code}"))
            .header(header::HOST, HOST)
            .body(Body::empty())
            .unwrap();
        let response = server::router(server.clone()).oneshot(req).await.unwrap();
        assert_eq!(response.status(), StatusCode::SEE_OTHER);
        response
            .headers()
            .get(header::SET_COOKIE)
            .unwrap()
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_string()
    }

    #[test]
    fn settings_defaults_and_parsing() {
        let defaults = ChatReposSettings::default();
        assert!(defaults.enabled);
        assert_eq!(defaults.bridges, vec!["signal", "matrix", "telegram", "discord"]);
        assert!(defaults.allow_main.is_empty());
        assert!(!defaults.full_access);
        assert_eq!(defaults.max_checkouts, 30);
        assert_eq!(defaults.idle_days, 14);
        assert_eq!(defaults.tokens_per_day, 3_000_000);
        assert_eq!(defaults.work_answer_seconds, 1800);
        assert_eq!(defaults.work_tool_calls, 200);
        assert_eq!(defaults.work_command_seconds, 600);

        let custom = ChatReposSettings::from_toml(
            r#"
            [chat_repos]
            enabled = false
            bridges = ["signal", "telegram"]
            allow_main = ["org/app"]
            full_access = true
            max_checkouts = 12
            idle_days = 7
            tokens_per_day = 500000
            work_answer_seconds = 600
            work_tool_calls = 50
            work_command_seconds = 120
            "#,
        );
        assert!(!custom.enabled);
        assert_eq!(custom.bridges, vec!["signal", "telegram"]);
        assert_eq!(custom.allow_main, vec!["org/app"]);
        assert!(custom.full_access);
        assert_eq!(custom.max_checkouts, 12);
        assert_eq!(custom.idle_days, 7);
        assert_eq!(custom.tokens_per_day, 500_000);
        assert_eq!(custom.work_answer_seconds, 600);
        assert_eq!(custom.work_tool_calls, 50);
        assert_eq!(custom.work_command_seconds, 120);
    }

    #[tokio::test]
    async fn checkouts_reuse_askpass_submodules_hooks_and_readonly() {
        let s = scratch("checkouts");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let ro_bare = make_bare_repo(&s.dir, "org/readonly");

        // Place a post-checkout hook in the source bare repo and verify it never executes.
        let marker = s.dir.join("hook-ran");
        let hook_path = bare.join("hooks").join("post-checkout");
        std::fs::write(&hook_path, format!("#!/bin/sh\necho ran > {}\n", marker.display())).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&hook_path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }

        // Add an external submodule in `.gitmodules` and `.gitattributes` with `filter=lfs` to `org/widget`.
        let seed_work = s.dir.join("seed-work").join("org_widget");
        std::fs::write(
            seed_work.join(".gitmodules"),
            "[submodule \"ext\"]\n\tpath = vendor/ext\n\turl = https://127.0.0.1:9443/outside/ext.git\n",
        )
        .unwrap();
        std::fs::write(seed_work.join(".gitattributes"), "*.bin filter=lfs diff=lfs merge=lfs -text\n").unwrap();
        git_sync(&seed_work, &["add", ".gitmodules", ".gitattributes"]);
        git_sync(&seed_work, &["commit", "-m", "add submodule and lfs"]);
        git_sync(&seed_work, &["push", "origin", "main"]);

        let (forge_url, _forge_task) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "private": true,
                "archived": false,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": true },
                "clone_url": format!("file://{}", bare.display())
            },
            {
                "full_name": "org/readonly",
                "default_branch": "main",
                "private": false,
                "archived": true,
                "mirror": false,
                "updated_at": "2026-10-19T12:00:00Z",
                "permissions": { "push": false },
                "clone_url": format!("file://{}", ro_bare.display())
            }
        ]))
        .await;

        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        // 1. Open org/widget for (signal, chat-1).
        let open_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "chat-1" }).to_string(),
            ))
            .unwrap();
        let (status, body) = call(&server, open_req).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        assert!(!body.contains(TEST_TOKEN));
        let opened: Value = serde_json::from_str(&body).unwrap();
        let thread1 = opened["thread"].as_str().unwrap().to_string();
        assert!(valid_thread_id(&thread1));
        assert_eq!(opened["branch"], json!(format!("phone/{thread1}")));
        assert_eq!(opened["read_only"], json!(false));
        let notes = opened["notes"].as_array().unwrap();
        assert!(notes.iter().any(|n| n.as_str().unwrap().contains("vendor/ext")));
        assert!(notes.iter().any(|n| n.as_str().unwrap().contains("filter=lfs")));
        assert!(!PathBuf::from(opened["cwd"].as_str().unwrap()).join("vendor/ext").exists());
        assert!(!marker.exists(), "post-checkout hook must not run");

        // 2. Re-open for the same (bridge, chat, repo) reuses the same checkout.
        let reopen_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "chat-1" }).to_string(),
            ))
            .unwrap();
        let (status, body2) = call(&server, reopen_req).await;
        assert_eq!(status, StatusCode::OK);
        let reopened: Value = serde_json::from_str(&body2).unwrap();
        assert_eq!(reopened["thread"], json!(thread1));

        // 3. Opening for a second chat reuses base.git and creates a second worktree.
        let open2_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "matrix", "chat": "room-2" }).to_string(),
            ))
            .unwrap();
        let (status, body3) = call(&server, open2_req).await;
        assert_eq!(status, StatusCode::OK, "{body3}");
        let opened2: Value = serde_json::from_str(&body3).unwrap();
        let thread2 = opened2["thread"].as_str().unwrap().to_string();
        assert_ne!(thread1, thread2);
        assert_eq!(opened2["branch"], json!(format!("phone/{thread2}")));

        let base_git = root_dir(&server.config.codex_home).join("org/widget/base.git");
        assert!(base_git.join("HEAD").exists());
        let git_config = std::fs::read_to_string(base_git.join("config")).unwrap();
        assert!(!git_config.contains(TEST_TOKEN));
        let wt_git_file = std::fs::read_to_string(PathBuf::from(opened["cwd"].as_str().unwrap()).join(".git")).unwrap();
        assert!(!wt_git_file.contains(TEST_TOKEN));
        let idx_text = std::fs::read_to_string(index_path(&server.config.codex_home)).unwrap();
        assert!(!idx_text.contains(TEST_TOKEN));

        // Verify the GIT_ASKPASS script in state_dir is 0700, does not contain the token literal,
        // and outputs the token when invoked.
        let askpass = server.config.state_dir.join(ASKPASS_SCRIPT);
        let askpass_text = std::fs::read_to_string(&askpass).unwrap();
        assert!(!askpass_text.contains(TEST_TOKEN));
        let askpass_out = std::process::Command::new(&askpass)
            .arg("Password for 'http://127.0.0.1:3200': ")
            .output()
            .unwrap();
        assert!(askpass_out.status.success());
        assert_eq!(String::from_utf8_lossy(&askpass_out.stdout).trim(), TEST_TOKEN);

        // 4. Read-only repository opens detached with read_only=true and no phone/<id> branch.
        let ro_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/readonly", "bridge": "signal", "chat": "chat-ro" }).to_string(),
            ))
            .unwrap();
        let (status, ro_body) = call(&server, ro_req).await;
        assert_eq!(status, StatusCode::OK, "{ro_body}");
        let ro_opened: Value = serde_json::from_str(&ro_body).unwrap();
        let ro_thread = ro_opened["thread"].as_str().unwrap();
        assert_eq!(ro_opened["read_only"], json!(true));
        assert!(ro_opened["branch"].is_null());
        let ro_base = root_dir(&server.config.codex_home).join("org/readonly/base.git");
        let branches = std::process::Command::new("git")
            .current_dir(&ro_base)
            .args(["branch", "--list", &format!("phone/{ro_thread}")])
            .output()
            .unwrap();
        assert!(String::from_utf8_lossy(&branches.stdout).trim().is_empty());

        // 5. GET /api/repos lists both repositories and includes this device's open checkouts.
        let list_req = Request::builder()
            .method("GET")
            .uri("/api/repos")
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, list_body) = call(&server, list_req).await;
        assert_eq!(status, StatusCode::OK, "{list_body}");
        assert!(!list_body.contains(TEST_TOKEN));
        let listed: Value = serde_json::from_str(&list_body).unwrap();
        let arr = listed.as_array().unwrap();
        assert_eq!(arr.len(), 2);
        let widget_entry = arr.iter().find(|e| e["repo"] == "org/widget").unwrap();
        let checkouts = widget_entry["checkouts"].as_array().unwrap();
        assert_eq!(checkouts.len(), 2);
    }

    #[tokio::test]
    async fn refusals_cover_names_listing_forge_off_max_checkouts_disk_bridges_and_watch_scope() {
        let s = scratch("refusals");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_task) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "private": false,
                "archived": false,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": true },
                "clone_url": format!("file://{}", bare.display())
            }
        ]))
        .await;

        let (server, probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        // 1. Refuse URLs, paths, .., and names not in listing.
        for bad_name in [
            "https://127.0.0.1/org/widget.git",
            "file:///tmp/repo",
            "/etc/passwd",
            "~/repo",
            "../org/widget",
            "org/../widget",
            "single",
            "a/b/c",
            "org/not-in-listing",
        ] {
            let req = Request::builder()
                .method("POST")
                .uri("/api/repos/open")
                .header(header::HOST, HOST)
                .header(header::ORIGIN, ORIGIN)
                .header(header::COOKIE, &cookie)
                .body(Body::from(
                    json!({ "repo": bad_name, "bridge": "signal", "chat": "c1" }).to_string(),
                ))
                .unwrap();
            let (status, body) = call(&server, req).await;
            assert_eq!(status, StatusCode::BAD_REQUEST, "expected 400 for {bad_name}: {body}");
        }

        // 2. Refuse bridge not in [chat_repos] bridges.
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nbridges = [\"signal\"]\nmax_checkouts = 1\n",
        )
        .unwrap();
        let bridge_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "telegram", "chat": "c1" }).to_string(),
            ))
            .unwrap();
        let (status, _) = call(&server, bridge_req).await;
        assert_eq!(status, StatusCode::FORBIDDEN);

        // 3. Disk floor refusal: 152 GB < 150 + 5 GB; and 176 GB < 175 + 5 GB during SWE-bench night.
        probe.free.store(152 * GB, Ordering::Relaxed);
        probe.night.store(false, Ordering::Relaxed);
        let low_disk_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }).to_string(),
            ))
            .unwrap();
        let (status, body) = call(&server, low_disk_req).await;
        assert_eq!(status, StatusCode::INSUFFICIENT_STORAGE, "{body}");

        probe.free.store(176 * GB, Ordering::Relaxed);
        probe.night.store(true, Ordering::Relaxed);
        let night_disk_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }).to_string(),
            ))
            .unwrap();
        let (status, body) = call(&server, night_disk_req).await;
        assert_eq!(status, StatusCode::INSUFFICIENT_STORAGE, "{body}");

        // Restore free disk above floor; open 1st checkout (max_checkouts = 1), then refuse 2nd.
        probe.free.store(300 * GB, Ordering::Relaxed);
        probe.night.store(false, Ordering::Relaxed);
        let ok_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }).to_string(),
            ))
            .unwrap();
        let (status, _) = call(&server, ok_req).await;
        assert_eq!(status, StatusCode::OK);

        let max_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "c2" }).to_string(),
            ))
            .unwrap();
        let (status, body) = call(&server, max_req).await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");

        // 4. Device with watch scope is refused with 403.
        let (dev_id, dev_token) = auth::add_device(&server.config.state_dir, "phone").unwrap();
        auth::set_scope(&server.config.state_dir, &dev_id, auth::Scope::Watch).unwrap();
        let watch_cookie = format!("{}={dev_token}", auth::DEVICE_COOKIE);
        let watch_req = Request::builder()
            .method("GET")
            .uri("/api/repos")
            .header(header::HOST, HOST)
            .header(header::COOKIE, &watch_cookie)
            .body(Body::empty())
            .unwrap();
        let (status, _) = call(&server, watch_req).await;
        assert_eq!(status, StatusCode::FORBIDDEN);

        // 5. Service OFF (404 naming `ling-admin forge on`) when enabled=false, token missing, or forge unreachable.
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nenabled = false\n",
        )
        .unwrap();
        let off_req = Request::builder()
            .method("GET")
            .uri("/api/repos")
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, body) = call(&server, off_req).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert!(body.contains("ling-admin forge on"), "{body}");

        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nenabled = true\n",
        )
        .unwrap();
        std::fs::remove_file(server.config.state_dir.join(TOKEN_FILE)).unwrap();
        let no_tok_req = Request::builder()
            .method("GET")
            .uri("/api/repos")
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, body) = call(&server, no_tok_req).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert!(body.contains("ling-admin forge on"), "{body}");

        let (unreachable_srv, _) = test_server(&s.dir.join("unreach"), Some("http://127.0.0.1:9".to_string()), true);
        let u_cookie = owner_cookie(&unreachable_srv).await;
        let down_req = Request::builder()
            .method("GET")
            .uri("/api/repos")
            .header(header::HOST, HOST)
            .header(header::COOKIE, &u_cookie)
            .body(Body::empty())
            .unwrap();
        let (status, body) = call(&unreachable_srv, down_req).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert!(body.contains("ling-admin forge on"), "{body}");
    }

    #[tokio::test]
    async fn state_diff_and_log_routes_and_unknown_thread_refusal() {
        let s = scratch("state-diff-log");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_task) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "private": true,
                "archived": false,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": true },
                "clone_url": format!("file://{}", bare.display())
            }
        ]))
        .await;

        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        let open_req = Request::builder()
            .method("POST")
            .uri("/api/repos/open")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }).to_string(),
            ))
            .unwrap();
        let (status, body) = call(&server, open_req).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        let opened: Value = serde_json::from_str(&body).unwrap();
        let thread = opened["thread"].as_str().unwrap();
        let wt = PathBuf::from(opened["cwd"].as_str().unwrap());

        // Commit a change on the phone/<thread> branch, then make an uncommitted edit and an untracked file.
        git_sync(&wt, &["config", "user.email", "dev@127.0.0.1"]);
        git_sync(&wt, &["config", "user.name", "dev"]);
        std::fs::write(wt.join("committed.txt"), "one\ntwo\n").unwrap();
        git_sync(&wt, &["add", "committed.txt"]);
        git_sync(&wt, &["commit", "-m", "add committed file"]);

        std::fs::write(wt.join("README.md"), "# Demo\nchanged line\nthird line\n").unwrap();
        std::fs::write(wt.join("untracked.txt"), "alpha\nbeta\n").unwrap();

        // 1. GET /api/repos/state
        let state_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/state?thread={thread}"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, state_body) = call(&server, state_req).await;
        assert_eq!(status, StatusCode::OK, "{state_body}");
        let state: Value = serde_json::from_str(&state_body).unwrap();
        assert_eq!(state["repo"], "org/widget");
        assert_eq!(state["branch"], format!("phone/{thread}"));
        assert_eq!(state["base"], "main");
        assert_eq!(state["ahead"], 1);
        assert_eq!(state["behind"], 0);
        assert_eq!(state["read_only"], false);
        assert_eq!(state["private"], true);
        let uncommitted = state["uncommitted"].as_array().unwrap();
        assert!(uncommitted.iter().any(|u| u["path"] == "README.md" && u["added"] == 2 && u["removed"] == 1));
        assert!(uncommitted.iter().any(|u| u["path"] == "untracked.txt" && u["added"] == 2 && u["removed"] == 0));

        // 2. GET /api/repos/diff (uncommitted stat list & single-file diff, branch stat list & single-file diff, turn -> 400)
        let diff_stat_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/diff?thread={thread}&scope=uncommitted"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, diff_stat_body) = call(&server, diff_stat_req).await;
        assert_eq!(status, StatusCode::OK);
        let diff_stat: Value = serde_json::from_str(&diff_stat_body).unwrap();
        assert_eq!(diff_stat.as_array().unwrap().len(), 2);

        let diff_file_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/diff?thread={thread}&scope=uncommitted&path=README.md"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, diff_file_body) = call(&server, diff_file_req).await;
        assert_eq!(status, StatusCode::OK);
        let diff_file: Value = serde_json::from_str(&diff_file_body).unwrap();
        assert!(diff_file["diff"].as_str().unwrap().contains("+changed line"));

        let branch_diff_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/diff?thread={thread}&scope=branch&path=committed.txt"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, branch_diff_body) = call(&server, branch_diff_req).await;
        assert_eq!(status, StatusCode::OK);
        let branch_diff: Value = serde_json::from_str(&branch_diff_body).unwrap();
        assert!(branch_diff["diff"].as_str().unwrap().contains("+one"));

        let turn_diff_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/diff?thread={thread}&scope=turn"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, turn_body) = call(&server, turn_diff_req).await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert!(turn_body.contains("the turn scope comes from the app-server"));

        // Refuse traversal in path=
        for bad_path in ["/etc/passwd", "../secret", "a/../b"] {
            let bad_req = Request::builder()
                .method("GET")
                .uri(format!("/api/repos/diff?thread={thread}&scope=uncommitted&path={bad_path}"))
                .header(header::HOST, HOST)
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap();
            let (status, _) = call(&server, bad_req).await;
            assert_eq!(status, StatusCode::BAD_REQUEST);
        }

        // 3. GET /api/repos/log
        let log_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/log?thread={thread}"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (status, log_body) = call(&server, log_req).await;
        assert_eq!(status, StatusCode::OK, "{log_body}");
        let commits: Value = serde_json::from_str(&log_body).unwrap();
        let commits_arr = commits.as_array().unwrap();
        assert_eq!(commits_arr.len(), 1);
        assert_eq!(commits_arr[0]["subject"], "add committed file");
        assert_eq!(commits_arr[0]["author"], "dev");
        assert!(commits_arr[0]["id"].as_str().unwrap().len() >= 40);

        // 4. Unknown thread id is refused on state, diff, and log.
        for uri in [
            "/api/repos/state?thread=deadbeef",
            "/api/repos/diff?thread=deadbeef&scope=uncommitted",
            "/api/repos/log?thread=deadbeef",
        ] {
            let req = Request::builder()
                .method("GET")
                .uri(uri)
                .header(header::HOST, HOST)
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap();
            let (status, _) = call(&server, req).await;
            assert_eq!(status, StatusCode::NOT_FOUND, "expected 404 for {uri}");
        }
    }
}
