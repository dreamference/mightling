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
/// Lifetime of a single-use confirmation nonce issued by `GET /api/repos/state?intent=…` (10 minutes).
pub const CONFIRM_TTL: Duration = Duration::from_secs(10 * 60);
/// Valid `intent` values for confirmation nonces.
pub const VALID_INTENTS: &[&str] = &[
    "commit", "push", "update", "merge", "pr", "main", "budget", "access",
];
/// Maximum length (in Unicode scalar values) of a commit message in `POST /api/repos/commit`.
pub const MAX_COMMIT_MESSAGE_CHARS: usize = 5_000;
/// Maximum size in bytes of an untracked regular file for line counting or diffing (8 MiB).
pub const MAX_UNTRACKED_STAT_BYTES: u64 = 8 * 1024 * 1024;
/// Maximum diff output size in bytes before truncation (1 MiB).
pub const MAX_DIFF_OUTPUT_BYTES: usize = 1024 * 1024;
/// Suffix appended when a diff output is truncated at `MAX_DIFF_OUTPUT_BYTES`.
pub const DIFF_CUT_SUFFIX: &str = "\n(cut: open the file to see all)";
/// Timeout for network-bound `git` commands (`clone`, `fetch`, `push`).
pub const GIT_NET_TIMEOUT: Duration = Duration::from_secs(300);
/// Timeout for local `git` commands.
pub const GIT_LOCAL_TIMEOUT: Duration = Duration::from_secs(60);

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
    /// Current Unix timestamp in seconds (overridable by tests).
    fn now_secs(&self) -> u64 {
        now_secs()
    }
    /// Current clock in milliseconds for turn watches (overridable by tests).
    fn now_ms(&self) -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }
    /// Optional fixed UTC offset in seconds for local midnight calculation (`None` uses `libc::localtime_r`).
    fn utc_offset_seconds(&self) -> Option<i64> {
        None
    }
    /// Timeout for `run_git` on `subcommand` (`300s` for `clone`/`fetch`/`push`, `60s` otherwise; overridable by tests).
    fn git_timeout(&self, subcommand: &str) -> Duration {
        match subcommand {
            "clone" | "fetch" | "push" => GIT_NET_TIMEOUT,
            _ => GIT_LOCAL_TIMEOUT,
        }
    }
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
    pub app_threads: Vec<String>,
    pub read_only: bool,
    pub created: u64,
    pub last_used: u64,
}

impl CheckoutRecord {
    pub fn has_app_thread(&self, thread_id: &str) -> bool {
        !thread_id.is_empty() && self.app_threads.iter().any(|t| t == thread_id)
    }

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
            "app_threads": self.app_threads,
            "app_thread": self.app_threads.last(),
            "read_only": self.read_only,
            "created": self.created,
            "last_used": self.last_used,
        })
    }

    pub fn from_json(value: &Value) -> Option<Self> {
        let mut app_threads: Vec<String> = value
            .get("app_threads")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        if let Some(old) = value.get("app_thread").and_then(Value::as_str).filter(|s| !s.is_empty())
            && !app_threads.iter().any(|t| t == old)
        {
            app_threads.push(old.to_string());
        }
        Some(Self {
            thread: value.get("thread")?.as_str()?.to_string(),
            repo: value.get("repo")?.as_str()?.to_string(),
            bridge: value.get("bridge")?.as_str()?.to_string(),
            chat: value.get("chat")?.as_str()?.to_string(),
            device: value.get("device")?.as_str()?.to_string(),
            dir: value.get("dir")?.as_str()?.to_string(),
            branch: value.get("branch").and_then(Value::as_str).map(str::to_string),
            base: value.get("base")?.as_str()?.to_string(),
            app_threads,
            read_only: value.get("read_only")?.as_bool()?,
            created: value.get("created")?.as_u64()?,
            last_used: value.get("last_used")?.as_u64()?,
        })
    }

    /// Canonical `<bridge>:<chat>` key for daily token budgets and pending notices.
    pub fn chat_key(&self) -> String {
        let prefix = format!("{}:", self.bridge);
        if self.chat.starts_with(&prefix) {
            self.chat.clone()
        } else {
            format!("{}:{}", self.bridge, self.chat)
        }
    }
}

/// A pending notice queued by `ChatRepos` for phone bridges to drain (`budget80`, `busy`, or `unbounded`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PendingNotice {
    pub chat: String,
    pub repo: String,
    pub kind: String,
}

impl PendingNotice {
    pub fn to_json(&self) -> Value {
        json!({
            "chat": self.chat,
            "repo": self.repo,
            "kind": self.kind,
        })
    }
}

struct CachedListing {
    at: Instant,
    forge_url: String,
    repos: Vec<ForgeRepo>,
}

#[derive(Clone, Debug)]
struct ConfirmNonce {
    caller: String,
    thread: String,
    intent: String,
    issued_at: Instant,
}

/// Runtime state held by `Server` for `/api/repos`: the 60-second listing cache, per-repository
/// git locks, `index.json` lock, single-use confirmation nonces, host probe, daily per-chat token
/// budget, pending notices, server-wide turn watches, and running-thread tracking for concurrency warnings.
pub struct ChatRepos {
    listing: tokio::sync::Mutex<Option<CachedListing>>,
    repo_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    index_lock: tokio::sync::Mutex<()>,
    index_io_lock: Mutex<()>,
    nonces: Mutex<HashMap<String, ConfirmNonce>>,
    probe: Mutex<Arc<dyn HostProbe>>,
    allow_file_protocol: std::sync::atomic::AtomicBool,
    budget: Mutex<crate::repo_limits::Budget>,
    notices: Mutex<Vec<PendingNotice>>,
    watches: Mutex<HashMap<String, crate::relay::ActiveTurnWatch>>,
    tab_threads: Mutex<HashMap<u64, std::collections::HashSet<String>>>,
    running_threads: Mutex<HashMap<String, bool>>,
    thread_places: Mutex<HashMap<String, PathBuf>>,
    turn_warnings: Mutex<HashMap<String, Vec<String>>>,
    full_access_grants: Mutex<std::collections::HashSet<String>>,
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
            index_lock: tokio::sync::Mutex::new(()),
            index_io_lock: Mutex::new(()),
            nonces: Mutex::new(HashMap::new()),
            probe: Mutex::new(Arc::new(SystemHostProbe)),
            allow_file_protocol: std::sync::atomic::AtomicBool::new(cfg!(test)),
            budget: Mutex::new(crate::repo_limits::Budget::default()),
            notices: Mutex::new(Vec::new()),
            watches: Mutex::new(HashMap::new()),
            tab_threads: Mutex::new(HashMap::new()),
            running_threads: Mutex::new(HashMap::new()),
            thread_places: Mutex::new(HashMap::new()),
            turn_warnings: Mutex::new(HashMap::new()),
            full_access_grants: Mutex::new(std::collections::HashSet::new()),
        }
    }

    /// Replaces the host probe (used by tests to stub free disk space, SWE-bench activity, and clock).
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

    /// Current Unix timestamp in seconds from the configured host probe.
    pub fn now_secs(&self) -> u64 {
        self.probe().now_secs()
    }

    /// Current clock in milliseconds from the configured host probe.
    pub fn now_ms(&self) -> u64 {
        self.probe().now_ms()
    }

    fn repo_mutex(&self, repo: &str) -> Arc<tokio::sync::Mutex<()>> {
        let mut locks = self.repo_locks.lock().unwrap_or_else(|p| p.into_inner());
        locks.entry(repo.to_string()).or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))).clone()
    }

    /// Clears the in-memory listing cache.
    pub async fn invalidate_listing_cache(&self) {
        *self.listing.lock().await = None;
    }

    /// Issues a 32-hex-character single-use confirmation nonce bound to `(caller, thread, intent)`.
    pub fn issue_nonce(&self, caller: &str, thread: &str, intent: &str) -> String {
        let mut nonces = self.nonces.lock().unwrap_or_else(|p| p.into_inner());
        nonces.retain(|_, v| v.issued_at.elapsed() < CONFIRM_TTL);
        let nonce = auth::random_hex(16);
        nonces.insert(
            nonce.clone(),
            ConfirmNonce {
                caller: caller.to_string(),
                thread: thread.to_string(),
                intent: intent.to_string(),
                issued_at: Instant::now(),
            },
        );
        nonce
    }

    /// Consumes a confirmation nonce if and only if it is unexpired and matches `(caller, thread, intent)`.
    /// Removes the nonce before returning `true`.
    pub fn consume_nonce(&self, caller: &str, thread: &str, intent: &str, nonce: &str) -> bool {
        if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return false;
        }
        let mut nonces = self.nonces.lock().unwrap_or_else(|p| p.into_inner());
        nonces.retain(|_, v| v.issued_at.elapsed() < CONFIRM_TTL);
        let matches = nonces
            .get(nonce)
            .is_some_and(|entry| entry.caller == caller && entry.thread == thread && entry.intent == intent);
        if matches {
            nonces.remove(nonce);
            true
        } else {
            false
        }
    }

    /// Appends `thread_id` to `app_threads` on the checkout record matching `cwd` under the index lock.
    pub fn record_app_thread(&self, codex_home: &Path, cwd: &Path, thread_id: &str) -> std::io::Result<bool> {
        let _async_try = self.index_lock.try_lock();
        let _io_guard = self.index_io_lock.lock().unwrap_or_else(|p| p.into_inner());
        let ok = record_app_thread(codex_home, cwd, thread_id)?;
        if ok {
            self.record_thread_place(thread_id, cwd.to_path_buf());
        }
        Ok(ok)
    }

    /// Async counterpart to `record_app_thread` that awaits the global `index_lock`.
    pub async fn record_app_thread_async(
        &self,
        codex_home: &Path,
        cwd: &Path,
        thread_id: &str,
    ) -> std::io::Result<bool> {
        let _idx_guard = self.index_lock.lock().await;
        let _io_guard = self.index_io_lock.lock().unwrap_or_else(|p| p.into_inner());
        let ok = record_app_thread(codex_home, cwd, thread_id)?;
        if ok {
            self.record_thread_place(thread_id, cwd.to_path_buf());
        }
        Ok(ok)
    }

    /// Looks up the checkout record in `$CODEX_HOME/chat-repos/index.json` whose `app_threads`
    /// contains `app_thread` (or whose `thread` equals `app_thread`) and whose worktree directory
    /// is a valid checkout under `chat-repos/`.
    pub fn find_by_app_thread(&self, codex_home: &Path, app_thread: &str) -> Option<CheckoutRecord> {
        if app_thread.is_empty() {
            return None;
        }
        load_index(codex_home).into_iter().find(|r| {
            (r.has_app_thread(app_thread) || r.thread == app_thread)
                && is_repo_checkout(codex_home, Path::new(&r.dir))
        })
    }

    /// Grants in-memory Full Access to the checkout identified by its 8-hex `thread` id.
    pub fn grant_full_access(&self, thread: &str) {
        if !thread.is_empty() {
            self.full_access_grants
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .insert(thread.to_string());
        }
    }

    /// Removes the in-memory Full Access grant for the checkout identified by its 8-hex `thread` id.
    pub fn revoke_full_access(&self, thread: &str) {
        self.full_access_grants
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .remove(thread);
    }

    /// Returns whether the checkout identified by its 8-hex `thread` id currently holds an
    /// in-memory Full Access grant.
    pub fn is_full_access_granted(&self, thread: &str) -> bool {
        self.full_access_grants
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .contains(thread)
    }

    /// Drops every Full Access grant: the node's `[chat_repos] full_access` was seen off.
    pub fn clear_full_access(&self) {
        self.full_access_grants.lock().unwrap_or_else(|p| p.into_inner()).clear();
    }

    /// Whether `record`'s checkout has Full Access now: a grant made with the typed phrase, the
    /// node's `[chat_repos] full_access` still on, the checkout writable and its air gap off. A
    /// grant that no longer stands is dropped (every grant when the setting is off), so turning the
    /// setting or the air gap back never brings one back without the phrase typed again.
    pub fn effective_full_access(&self, settings: &ChatReposSettings, record: &CheckoutRecord) -> bool {
        if !settings.full_access {
            self.clear_full_access();
            return false;
        }
        if !self.is_full_access_granted(&record.thread) {
            return false;
        }
        if record.read_only || record_airgapped_on(record, Path::new(&record.dir)) {
            self.revoke_full_access(&record.thread);
            return false;
        }
        true
    }

    /// Queues a pending bot notice (`{chat, repo, kind}`).
    pub fn record_notice(&self, chat: &str, repo: &str, kind: &str) {
        self.notices
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .push(PendingNotice {
                chat: chat.to_string(),
                repo: repo.to_string(),
                kind: kind.to_string(),
            });
    }

    /// Drains and returns all pending notices (`budget80`, `busy`, `unbounded`).
    pub fn take_notices(&self) -> Vec<PendingNotice> {
        let mut notices = self.notices.lock().unwrap_or_else(|p| p.into_inner());
        std::mem::take(&mut *notices)
    }

    /// Records that relay tab `tab` is connected to `thread_id`.
    pub fn note_tab_thread(&self, tab: u64, thread_id: &str) {
        if !thread_id.is_empty() {
            self.tab_threads
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .entry(tab)
                .or_default()
                .insert(thread_id.to_string());
        }
    }

    /// Registers a server-wide turn watch keyed by `turn_id` and owned by `tab` if none exists yet
    /// for `turn_id`. Returns `true` when `tab` became the owner of the new watch.
    pub fn register_turn_watch(
        &self,
        codex_home: &Path,
        record: &CheckoutRecord,
        turn_id: &str,
        thread_id: &str,
        tab: u64,
        watch: crate::repo_limits::RepoWatch,
    ) -> bool {
        if turn_id.is_empty() || thread_id.is_empty() {
            return false;
        }
        self.note_tab_thread(tab, thread_id);
        {
            let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
            if watches.contains_key(turn_id) {
                return false;
            }
            watches.insert(
                turn_id.to_string(),
                crate::relay::ActiveTurnWatch {
                    turn_id: turn_id.to_string(),
                    thread_id: thread_id.to_string(),
                    chat_key: record.chat_key(),
                    repo: record.repo.clone(),
                    owner_tab: tab,
                    watch,
                },
            );
        }
        self.note_turn_started(codex_home, record);
        true
    }

    /// Evaluates all active turn watches owned by `tab` at `now_ms()`.
    pub fn check_watches_for_tab(&self, tab: u64) -> Vec<(String, String, crate::repo_limits::RepoVerdict)> {
        let now_ms = self.now_ms();
        let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
        let mut out = Vec::new();
        for entry in watches.values_mut() {
            if entry.owner_tab == tab
                && let Some(verdict) = entry.watch.check(now_ms)
            {
                out.push((entry.thread_id.clone(), entry.turn_id.clone(), verdict));
            }
        }
        out
    }

    /// Updates the turn watch when `tab` is its owner and a tool/command item starts; returns `true` if updated.
    pub fn on_item_started_for_tab(
        &self,
        tab: u64,
        thread_id: Option<&str>,
        turn_id: Option<&str>,
        kind: &str,
        item_id: &str,
    ) -> bool {
        let now_ms = self.now_ms();
        let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
        let single_turn = (watches.len() == 1).then(|| watches.keys().next().cloned()).flatten();
        let entry = if let Some(t_id) = turn_id {
            watches.get_mut(t_id)
        } else if let Some(th_id) = thread_id {
            watches.values_mut().find(|w| w.thread_id == th_id)
        } else if let Some(t_id) = single_turn {
            watches.get_mut(&t_id)
        } else {
            None
        };
        let Some(entry) = entry else { return false };
        if entry.owner_tab != tab {
            return false;
        }
        if crate::repo_limits::is_tool(kind) {
            entry.watch.on_tool_started();
        }
        if kind == "commandExecution" && !item_id.is_empty() {
            entry.watch.on_command_started_at(item_id, now_ms);
        }
        true
    }

    /// Updates the turn watch when `tab` is its owner and a command item completes; returns `true` if updated.
    pub fn on_item_completed_for_tab(
        &self,
        tab: u64,
        thread_id: Option<&str>,
        turn_id: Option<&str>,
        item_id: &str,
    ) -> bool {
        if item_id.is_empty() {
            return false;
        }
        let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
        let single_turn = (watches.len() == 1).then(|| watches.keys().next().cloned()).flatten();
        let entry = if let Some(t_id) = turn_id {
            watches.get_mut(t_id)
        } else if let Some(th_id) = thread_id {
            watches.values_mut().find(|w| w.thread_id == th_id)
        } else if let Some(t_id) = single_turn {
            watches.get_mut(&t_id)
        } else {
            None
        };
        let Some(entry) = entry else { return false };
        if entry.owner_tab != tab {
            return false;
        }
        entry.watch.on_command_finished(item_id);
        true
    }

    /// Feeds cumulative token usage from `tab`; if a watch is active on `thread_id`, only its
    /// `owner_tab` processes the notification and receives any resulting budget steer.
    pub fn on_token_usage_for_tab(
        &self,
        tab: u64,
        record: &CheckoutRecord,
        thread_id: &str,
        tokens_per_day: u64,
        cumulative: u64,
    ) -> Option<(String, String, String)> {
        {
            let watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
            if let Some(w) = watches.values().find(|w| w.thread_id == thread_id)
                && w.owner_tab != tab
            {
                return None;
            }
        }
        let exhausted = self.add_token_usage_for_thread(record, thread_id, tokens_per_day, cumulative);
        if !exhausted {
            return None;
        }
        let now_ms = self.now_ms();
        let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(entry) = watches.values_mut().find(|w| w.thread_id == thread_id && w.owner_tab == tab)
            && let Some(crate::repo_limits::RepoVerdict::Steer(text)) = entry.watch.steer_for_budget(now_ms)
        {
            return Some((entry.thread_id.clone(), entry.turn_id.clone(), text));
        }
        None
    }

    /// Removes the watch for `turn_id` (or all watches for `thread_id` if `turn_id` is `None`).
    pub fn remove_turn_watch(&self, turn_id: Option<&str>, thread_id: &str) {
        let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(t_id) = turn_id {
            watches.remove(t_id);
        } else {
            watches.retain(|_, w| w.thread_id != thread_id);
        }
    }

    /// Removes all watches and tab associations for `thread_id`.
    pub fn remove_thread_watches(&self, thread_id: &str) {
        self.watches
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .retain(|_, w| w.thread_id != thread_id);
        let mut tabs = self.tab_threads.lock().unwrap_or_else(|p| p.into_inner());
        for set in tabs.values_mut() {
            set.remove(thread_id);
        }
    }

    /// Handles a relay tab closing: hands any turn watch owned by `tab` to another open tab
    /// connected to the same thread, or drops the watch and records an `"unbounded"` notice.
    pub fn forget_tab(&self, tab: u64) {
        let other_tabs: Vec<(u64, std::collections::HashSet<String>)> = {
            let mut tabs = self.tab_threads.lock().unwrap_or_else(|p| p.into_inner());
            tabs.remove(&tab);
            let mut list: Vec<_> = tabs.iter().map(|(&id, set)| (id, set.clone())).collect();
            list.sort_by_key(|(id, _)| *id);
            list
        };
        let mut unbounded = Vec::new();
        {
            let mut watches = self.watches.lock().unwrap_or_else(|p| p.into_inner());
            let mut to_remove = Vec::new();
            for (turn_id, entry) in watches.iter_mut() {
                if entry.owner_tab == tab {
                    if let Some((next_tab, _)) = other_tabs.iter().find(|(_, set)| set.contains(&entry.thread_id)) {
                        entry.owner_tab = *next_tab;
                    } else {
                        to_remove.push(turn_id.clone());
                        unbounded.push((entry.chat_key.clone(), entry.repo.clone()));
                    }
                }
            }
            for turn_id in to_remove {
                watches.remove(&turn_id);
            }
        }
        for (chat, repo) in unbounded {
            self.record_notice(&chat, &repo, "unbounded");
        }
    }

    /// Returns `(used, limit, resets_at)` for `record`'s chat at the current node time.
    pub fn budget_snapshot(&self, record: &CheckoutRecord, tokens_per_day: u64) -> (u64, u64, u64) {
        let probe = self.probe();
        let now = probe.now_secs();
        let chat = record.chat_key();
        let mut budget = self.budget.lock().unwrap_or_else(|p| p.into_inner());
        budget.set_tokens_per_day(tokens_per_day);
        budget.set_utc_offset(probe.utc_offset_seconds());
        (
            budget.used(&chat, now),
            budget.limit(&chat, now),
            budget.resets_at(now),
        )
    }

    /// Raises `record`'s chat budget by `add` tokens until local midnight; returns `(used, limit, resets_at)`.
    pub fn raise_budget(&self, record: &CheckoutRecord, tokens_per_day: u64, add: u64) -> (u64, u64, u64) {
        let probe = self.probe();
        let now = probe.now_secs();
        let chat = record.chat_key();
        let mut budget = self.budget.lock().unwrap_or_else(|p| p.into_inner());
        budget.set_tokens_per_day(tokens_per_day);
        budget.set_utc_offset(probe.utc_offset_seconds());
        let limit = budget.raise(&chat, add, now);
        (budget.used(&chat, now), limit, budget.resets_at(now))
    }

    /// Checks whether `record`'s chat still has model budget before starting a new `turn/start`.
    pub fn check_budget_before_turn(&self, record: &CheckoutRecord, tokens_per_day: u64) -> Result<(), String> {
        let probe = self.probe();
        let now = probe.now_secs();
        let chat = record.chat_key();
        let mut budget = self.budget.lock().unwrap_or_else(|p| p.into_inner());
        budget.set_tokens_per_day(tokens_per_day);
        budget.set_utc_offset(probe.utc_offset_seconds());
        if budget.exhausted(&chat, now) {
            Err(budget.refusal_message(now))
        } else {
            Ok(())
        }
    }

    /// Feeds a cumulative thread token-usage total into `record`'s chat budget. Records a
    /// `budget80` notice when 80% is crossed for the first time today, and returns `true` when the
    /// chat's budget is now exhausted (100%).
    pub fn add_token_usage(&self, record: &CheckoutRecord, tokens_per_day: u64, cumulative_total: u64) -> bool {
        let thread = record.app_threads.last().map(String::as_str).unwrap_or(&record.thread);
        self.add_token_usage_for_thread(record, thread, tokens_per_day, cumulative_total)
    }

    /// Feeds a cumulative token-usage total for a specific `thread_id` into `record`'s chat budget.
    pub fn add_token_usage_for_thread(
        &self,
        record: &CheckoutRecord,
        thread_id: &str,
        tokens_per_day: u64,
        cumulative_total: u64,
    ) -> bool {
        let probe = self.probe();
        let now = probe.now_secs();
        let chat = record.chat_key();
        let thread = if thread_id.is_empty() { &record.thread } else { thread_id };
        let (crossed_80, exhausted) = {
            let mut budget = self.budget.lock().unwrap_or_else(|p| p.into_inner());
            budget.set_tokens_per_day(tokens_per_day);
            budget.set_utc_offset(probe.utc_offset_seconds());
            budget.add(&chat, thread, cumulative_total, now);
            (budget.crossed_80(&chat), budget.exhausted(&chat, now))
        };
        if crossed_80 {
            self.record_notice(&chat, &record.repo, "budget80");
        }
        exhausted
    }

    /// Synchronises the shared `thread_id -> running` map from `Server`'s `BusyTracker` union across tabs.
    pub fn sync_running_threads(&self, busy_threads: &std::collections::BTreeSet<String>) {
        let mut map = self.running_threads.lock().unwrap_or_else(|p| p.into_inner());
        for val in map.values_mut() {
            *val = false;
        }
        for tid in busy_threads {
            map.insert(tid.clone(), true);
        }
    }

    /// Sets whether `thread_id` currently has a turn running.
    pub fn set_thread_running(&self, thread_id: &str, running: bool) {
        self.running_threads
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(thread_id.to_string(), running);
    }

    /// Whether `thread_id` currently has a turn running.
    pub fn is_thread_running(&self, thread_id: &str) -> bool {
        self.running_threads
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .get(thread_id)
            .copied()
            .unwrap_or(false)
    }

    /// Records the working directory `cwd` of an app-server thread `thread_id`.
    pub fn record_thread_place(&self, thread_id: &str, cwd: PathBuf) {
        if !thread_id.is_empty() {
            self.thread_places
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .insert(thread_id.to_string(), cwd);
        }
    }

    /// Computes and stores the concurrency warnings (§8.1) when a turn starts on `record`.
    pub fn note_turn_started(&self, codex_home: &Path, record: &CheckoutRecord) -> Vec<String> {
        let warnings = self.compute_live_warnings(codex_home, record);
        self.turn_warnings
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(record.thread.clone(), warnings.clone());
        warnings
    }

    /// Returns the concurrency warnings for `record` (combining warnings recorded when the current
    /// turn started with any live concurrent turns running now).
    pub fn warnings_for(&self, codex_home: &Path, record: &CheckoutRecord) -> Vec<String> {
        let mut warnings = self
            .turn_warnings
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .get(&record.thread)
            .cloned()
            .unwrap_or_default();
        for w in self.compute_live_warnings(codex_home, record) {
            if !warnings.contains(&w) {
                warnings.push(w);
            }
        }
        warnings
    }

    /// Computes live concurrency warnings (§8.1) for `record` against currently running threads.
    pub fn compute_live_warnings(&self, codex_home: &Path, record: &CheckoutRecord) -> Vec<String> {
        let running = self
            .running_threads
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone();
        let places = self
            .thread_places
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone();
        let record_dir = Path::new(&record.dir);

        let same_checkout = running.iter().any(|(tid, &is_running)| {
            if !is_running || record.has_app_thread(tid) || tid.as_str() == record.thread.as_str() {
                return false;
            }
            if places.get(tid).is_some_and(|cwd| same_worktree_dir(cwd, record_dir)) {
                return true;
            }
            load_index(codex_home).iter().any(|r| {
                (r.has_app_thread(tid) || r.thread == *tid)
                    && same_worktree_dir(Path::new(&r.dir), record_dir)
            })
        });

        let mut other_checkout_warnings = Vec::new();
        let this_branch = record.branch.as_deref().unwrap_or(&record.base);
        for other in load_index(codex_home) {
            if other.repo == record.repo
                && other.thread != record.thread
                && !same_worktree_dir(Path::new(&other.dir), record_dir)
            {
                let is_other_running = other
                    .app_threads
                    .iter()
                    .any(|id| running.get(id).copied().unwrap_or(false))
                    || running.get(&other.thread).copied().unwrap_or(false);
                if is_other_running {
                    let other_branch = other.branch.as_deref().unwrap_or(&other.base);
                    let msg = format!(
                        "{} is also being worked on in another checkout (branch {other_branch}, running). Phone edits stay on {this_branch}.",
                        record.repo
                    );
                    if !other_checkout_warnings.contains(&msg) {
                        other_checkout_warnings.push(msg);
                    }
                }
            }
        }

        let mut warnings = Vec::new();
        if same_checkout {
            warnings.push("Another thread is running in this checkout.".to_string());
        }
        warnings.extend(other_checkout_warnings);
        let on_default = record.branch.as_deref() == Some(record.base.as_str()) || this_branch == record.base;
        if !warnings.is_empty() && on_default {
            warnings.push("Both may push main; /push refuses a non-fast-forward.".to_string());
        }
        warnings
    }
}

fn same_worktree_dir(a: &Path, b: &Path) -> bool {
    if a == b {
        return true;
    }
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(ca), Ok(cb)) => ca == cb,
        _ => false,
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
        .route("/api/repos/commit", post(repo_commit))
        .route("/api/repos/push", post(repo_push))
        .route("/api/repos/update", post(repo_update))
        .route("/api/repos/merge", post(repo_merge))
        .route("/api/repos/pr", post(repo_pr))
        .route("/api/repos/main", post(repo_main))
        .route("/api/repos/budget", post(repo_budget))
        .route("/api/repos/access", post(repo_access))
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

/// Checks whether `cwd` is an existing checkout recorded in `$CODEX_HOME/chat-repos/index.json`:
/// its canonical path must lie strictly under canonical `$CODEX_HOME/chat-repos/` and equal the
/// canonical `dir` of a record in `index.json`. Returns `Some(read_only)` when matched.
pub fn repo_checkout(codex_home: &Path, cwd: &Path) -> Option<bool> {
    let (Ok(root), Ok(cwd)) = (root_dir(codex_home).canonicalize(), cwd.canonicalize()) else {
        return None;
    };
    if !cwd.is_dir() || !cwd.starts_with(&root) || cwd == root {
        return None;
    }
    load_index(codex_home)
        .into_iter()
        .find(|record| Path::new(&record.dir).canonicalize().ok().as_ref() == Some(&cwd))
        .map(|record| record.read_only)
}

/// Whether `cwd` is an existing checkout recorded in `$CODEX_HOME/chat-repos/index.json`.
pub fn is_repo_checkout(codex_home: &Path, cwd: &Path) -> bool {
    repo_checkout(codex_home, cwd).is_some()
}

/// Returns the recorded checkout `dir` for `thread_id` when `thread_id` is in `app_threads` of a
/// valid checkout under `$CODEX_HOME/chat-repos/`.
pub fn repo_thread_cwd(codex_home: &Path, thread_id: &str) -> Option<String> {
    if thread_id.is_empty() {
        return None;
    }
    let root = root_dir(codex_home).canonicalize().ok()?;
    for record in load_index(codex_home) {
        if record.has_app_thread(thread_id) {
            let dir = Path::new(&record.dir);
            if let Ok(canon) = dir.canonicalize()
                && canon.is_dir()
                && canon.starts_with(&root)
                && canon != root
            {
                return Some(record.dir);
            }
        }
    }
    None
}

/// Whether `thread_id` is recorded in `app_threads` on a checkout in `$CODEX_HOME/chat-repos/index.json`.
pub fn is_repo_thread(codex_home: &Path, thread_id: &str) -> bool {
    repo_thread_cwd(codex_home, thread_id).is_some()
        || (!thread_id.is_empty() && load_index(codex_home).iter().any(|record| record.has_app_thread(thread_id)))
}

/// Appends `thread_id` to `app_threads` on the checkout record in `$CODEX_HOME/chat-repos/index.json`
/// whose `dir` matches `cwd` (after canonicalisation when the folder exists on disk), writing the index whole.
pub fn record_app_thread(codex_home: &Path, cwd: &Path, thread_id: &str) -> std::io::Result<bool> {
    if thread_id.is_empty() || !is_repo_checkout(codex_home, cwd) {
        return Ok(false);
    }
    let canonical = cwd.canonicalize().ok();
    let mut records = load_index(codex_home);
    let mut matched = false;
    let mut changed = false;
    for record in &mut records {
        let matches = Path::new(&record.dir) == cwd
            || (canonical.is_some() && Path::new(&record.dir).canonicalize().ok() == canonical);
        if matches {
            matched = true;
            if !record.has_app_thread(thread_id) {
                record.app_threads.push(thread_id.to_string());
                changed = true;
            }
        }
    }
    if changed {
        save_index(codex_home, &records)?;
    }
    Ok(matched)
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

/// Resolves `(device_label, nonce_caller_id)` and refuses any `watch`-scoped device with 403.
fn require_control_caller_with_session(server: &Server, headers: &HeaderMap) -> Result<(String, String), Response> {
    if server.owner_session(headers) {
        let session_tok = auth::cookie(header_str(headers, header::COOKIE), auth::SESSION_COOKIE).unwrap_or("");
        let session_id = format!("session:{}", auth::sha256_hex(session_tok));
        return Ok(("owner".to_string(), session_id));
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
    let nonce_id = format!("device:{}", device.id);
    Ok((device.id, nonce_id))
}

/// Resolves the caller's device identifier and refuses any `watch`-scoped device with 403.
fn require_control_caller(server: &Server, headers: &HeaderMap) -> Result<String, Response> {
    require_control_caller_with_session(server, headers).map(|(device, _)| device)
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

/// Performs an HTTP/1.1 `POST` with a JSON body to the forge with `Authorization: token <token>`
/// and `Connection: close`. Never includes `token` in any error message.
pub async fn forge_post(
    base_url: &str,
    path_and_query: &str,
    token: &str,
    body_json: &Value,
    timeout: Duration,
) -> Result<(u16, Vec<u8>), String> {
    let (connect_addr, host_header) = parse_http_base_url(base_url)?;
    let path = if path_and_query.starts_with('/') {
        path_and_query.to_string()
    } else {
        format!("/{path_and_query}")
    };
    let payload = body_json.to_string();
    tokio::time::timeout(timeout, async move {
        let mut stream = TcpStream::connect(&connect_addr)
            .await
            .map_err(|_| format!("could not connect to forge at {connect_addr}"))?;
        let request = format!(
            "POST {path} HTTP/1.1\r\nHost: {host_header}\r\nAuthorization: token {token}\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
            payload.len()
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
/// `allow_file` lets a stand-in forge's `file://` clone URLs through (tests only, `set_allow_file_protocol`).
pub async fn fetch_forge_repos(base_url: &str, token: &str, allow_file: bool) -> Result<Vec<ForgeRepo>, String> {
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
            if let Some(repo) = parse_forge_repo(base_url, item, allow_file) {
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

fn parse_forge_repo(base_url: &str, item: &Value, allow_file: bool) -> Option<ForgeRepo> {
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
    // The clone URL is always the forge's own address that this service talks to: the one the API
    // reports follows the forge's ROOT_URL (the node's name, through `ling web`'s `/forge/`), and a
    // listing must never send a clone anywhere else (§0 decision 2). A stand-in's `file://` URL is
    // taken only when tests allowed the file protocol.
    let clone_url = match item.get("clone_url").and_then(Value::as_str) {
        Some(url) if allow_file && url.starts_with("file://") => url.to_string(),
        _ => format!("{}/{full_name}.git", base_url.trim_end_matches('/')),
    };
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
    let allow_file = server.chat_repos.allow_file_protocol.load(std::sync::atomic::Ordering::Relaxed);
    let repos = fetch_forge_repos(base_url, token, allow_file).await?;
    *guard = Some(CachedListing {
        at: Instant::now(),
        forge_url: base_url.to_string(),
        repos: repos.clone(),
    });
    Ok(repos)
}

/// Prepares the `0700` `GIT_ASKPASS` script and empty hooks folder in `state_dir` for `DEFAULT_FORGE_URL`.
pub fn ensure_git_helpers(state_dir: &Path) -> std::io::Result<(PathBuf, PathBuf)> {
    ensure_git_helpers_for_forge(state_dir, DEFAULT_FORGE_URL)
}

/// Prepares the `0700` `GIT_ASKPASS` script and empty hooks folder in `state_dir` restricted to `forge_url`.
/// The script reads `<state_dir>/forge-phone-token` at runtime, never embeds the token, and answers
/// only prompts that contain the forge's exact `http://host:port`.
pub fn ensure_git_helpers_for_forge(state_dir: &Path, forge_url: &str) -> std::io::Result<(PathBuf, PathBuf)> {
    auth::private_dir(state_dir)?;
    let hooks_dir = state_dir.join(EMPTY_HOOKS_DIR);
    auth::private_dir(&hooks_dir)?;

    let forge_base = forge_url.trim().trim_end_matches('/');
    let askpass_path = state_dir.join(ASKPASS_SCRIPT);
    let script = format!(
        "#!/bin/sh\ncase \"$1\" in\n*\"{forge_base}\"*) ;;\n*) exit 1 ;;\nesac\ncase \"$1\" in\nUsername*) printf 'mightling-phone\\n' ;;\n*) tr -d '\\r\\n' < \"$(dirname \"$0\")/{TOKEN_FILE}\"; printf '\\n' ;;\nesac\n"
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

fn git_subcommand_name<'a>(args: &'a [&'a str]) -> &'a str {
    let mut i = 0;
    while i < args.len() {
        let arg = args[i];
        if matches!(
            arg,
            "-c" | "-C" | "--git-dir" | "--work-tree" | "--namespace" | "--super-prefix" | "--config-env"
        ) {
            i += 2;
            continue;
        }
        if arg.starts_with('-') {
            i += 1;
            continue;
        }
        return arg;
    }
    "git"
}

fn build_git_command(
    server: &Server,
    cwd: Option<&Path>,
    args: &[&str],
    with_askpass: bool,
) -> Result<Command, String> {
    let base_url = forge_base_url(server);
    let (askpass, hooks_dir) =
        ensure_git_helpers_for_forge(&server.config.state_dir, &base_url).map_err(|e| e.to_string())?;
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
    for (name, value) in ling_safegit::FIXED_ENV {
        cmd.env(name, value);
    }
    if with_askpass {
        cmd.env("GIT_ASKPASS", &askpass);
    }
    // A checkout's config is the agent's to write and this runs outside the sandbox: pin every
    // key that executes a program (ling_safegit), then the empty hooks folder.
    cmd.args(ling_safegit::config_args(cwd));
    cmd.arg("-c").arg(&hooks_arg);
    if server.chat_repos.allow_file_protocol.load(std::sync::atomic::Ordering::Relaxed) {
        cmd.arg("-c").arg("protocol.file.allow=always");
    }
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    cmd.args(ling_safegit::safe_flags(args));
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd.kill_on_drop(true);
    Ok(cmd)
}

/// Runs `git` with a clean environment (`PATH`, `HOME`, `LANG=C`, `GIT_TERMINAL_PROMPT=0`,
/// `GIT_ASKPASS`), `-c core.hooksPath=<empty>`, no inherited `GIT_*` variables, and a per-command
/// timeout (300 s for `clone`/`fetch`/`push`, 60 s otherwise; killed on expiry).
async fn run_git(
    server: &Server,
    cwd: Option<&Path>,
    args: &[&str],
    with_askpass: bool,
) -> Result<std::process::Output, String> {
    let subcommand = git_subcommand_name(args);
    let timeout_dur = server.chat_repos.probe().git_timeout(subcommand);
    let mut cmd = build_git_command(server, cwd, args, with_askpass)?;
    let child = cmd.spawn().map_err(|e| format!("failed to run git {subcommand}: {e}"))?;
    match tokio::time::timeout(timeout_dur, child.wait_with_output()).await {
        Ok(Ok(output)) => Ok(output),
        Ok(Err(e)) => Err(format!("failed to run git {subcommand}: {e}")),
        Err(_) => Err(format!("git {subcommand} timed out after {}s", timeout_dur.as_secs())),
    }
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

/// Runs a `git diff` command while reading at most `MAX_DIFF_OUTPUT_BYTES` (1 MiB) of stdout.
/// If the output exceeds 1 MiB, stops reading, kills the child process, and appends
/// `"(cut: open the file to see all)"`.
async fn run_git_diff_capped(
    server: &Server,
    cwd: &Path,
    args: &[&str],
    require_zero_exit: bool,
) -> Result<String, String> {
    let subcommand = git_subcommand_name(args);
    let timeout_dur = server.chat_repos.probe().git_timeout(subcommand);
    let mut cmd = build_git_command(server, Some(cwd), args, false)?;
    let mut child = cmd.spawn().map_err(|e| format!("failed to run git {subcommand}: {e}"))?;
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| format!("missing stdout from git {subcommand}"))?;
    let mut stderr = child
        .stderr
        .take()
        .ok_or_else(|| format!("missing stderr from git {subcommand}"))?;

    let read_res = tokio::time::timeout(timeout_dur, async {
        let mut buf = Vec::new();
        (&mut stdout)
            .take((MAX_DIFF_OUTPUT_BYTES + 1) as u64)
            .read_to_end(&mut buf)
            .await
            .map_err(|e| format!("failed to read git {subcommand} output: {e}"))?;
        if buf.len() > MAX_DIFF_OUTPUT_BYTES {
            let _ = child.kill().await;
            let _ = child.wait().await;
            buf.truncate(MAX_DIFF_OUTPUT_BYTES);
            while !buf.is_empty() && std::str::from_utf8(&buf).is_err() {
                buf.pop();
            }
            let mut text = String::from_utf8_lossy(&buf).into_owned();
            text.push_str(DIFF_CUT_SUFFIX);
            return Ok(text);
        }
        let mut err_buf = Vec::new();
        let _ = (&mut stderr).take(65_536).read_to_end(&mut err_buf).await;
        let status = child
            .wait()
            .await
            .map_err(|e| format!("failed to wait for git {subcommand}: {e}"))?;
        if require_zero_exit && !status.success() {
            let stderr_s = String::from_utf8_lossy(&err_buf);
            let stdout_s = String::from_utf8_lossy(&buf);
            let detail = if !stderr_s.trim().is_empty() {
                stderr_s.trim()
            } else {
                stdout_s.trim()
            };
            return Err(sanitize_error(detail, &server.config.state_dir));
        }
        Ok(String::from_utf8_lossy(&buf).into_owned())
    })
    .await;

    match read_res {
        Ok(res) => res,
        Err(_) => {
            let _ = child.kill().await;
            Err(format!("git {subcommand} timed out after {}s", timeout_dur.as_secs()))
        }
    }
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

    // Lock order is always per-repo mutex first, then `index_lock` (never holding `index_lock` across clone/fetch).
    let repo_lock = server.chat_repos.repo_mutex(&forge_repo.repo);
    let _repo_guard = repo_lock.lock().await;

    let chat_repos_root = root_dir(&server.config.codex_home);
    if let Err(err) = auth::private_dir(&chat_repos_root) {
        return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err.to_string() }));
    }

    let snapshot = load_index(&server.config.codex_home);
    let existing_snapshot = if let Some(tid) = requested_thread {
        let Some(rec) = snapshot.iter().find(|r| r.thread == tid && r.repo == forge_repo.repo).cloned() else {
            return json_response(
                StatusCode::NOT_FOUND,
                json!({ "error": format!("No checkout `{tid}` for `{}`.", forge_repo.repo) }),
            );
        };
        Some(rec)
    } else {
        snapshot
            .iter()
            .find(|r| r.repo == forge_repo.repo && r.bridge == bridge && r.chat == chat)
            .cloned()
    };

    if existing_snapshot.is_none() {
        if snapshot.len() >= settings.max_checkouts {
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

    let (created_new_worktree, candidate_thread, candidate_worktree_dir, candidate_branch) =
        if let Some(existing) = &existing_snapshot {
            let branch = if forge_repo.read_only {
                None
            } else {
                existing.branch.clone()
            };
            let wt_dir = PathBuf::from(&existing.dir);
            if !wt_dir.join(".git").exists() {
                if let Err(err) = ensure_worktree(
                    &server,
                    &base_git,
                    &wt_dir,
                    &existing.thread,
                    &forge_repo,
                    branch.as_deref(),
                )
                .await
                {
                    return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
                }
            }
            (false, existing.thread.clone(), wt_dir, branch)
        } else {
            let thread = unique_thread_id(&snapshot);
            let wt_dir = chat_repos_root
                .join(&forge_repo.repo)
                .join(format!("{bridge}-{thread}"));
            let branch = if forge_repo.read_only {
                None
            } else {
                Some(format!("phone/{thread}"))
            };
            if let Err(err) = ensure_worktree(&server, &base_git, &wt_dir, &thread, &forge_repo, branch.as_deref()).await
            {
                return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
            }
            (true, thread, wt_dir, branch)
        };

    let candidate_dir_str = candidate_worktree_dir.to_string_lossy().into_owned();
    let now = now_secs();

    enum OpenIndexOutcome {
        Saved(CheckoutRecord),
        LostRace {
            winner: CheckoutRecord,
            remove_dir: PathBuf,
            remove_branch: Option<String>,
        },
        MaxCheckouts {
            remove_dir: PathBuf,
            remove_branch: Option<String>,
        },
        NotFound(String),
        SaveErr(String),
    }

    let outcome = {
        let _idx_guard = server.chat_repos.index_lock.lock().await;
        let _io_guard = server.chat_repos.index_io_lock.lock().unwrap_or_else(|p| p.into_inner());
        let mut records = load_index(&server.config.codex_home);
        if let Some(tid) = requested_thread {
            if let Some(item) = records.iter_mut().find(|r| r.thread == tid && r.repo == forge_repo.repo) {
                item.last_used = now;
                item.read_only = forge_repo.read_only;
                item.base = forge_repo.default_branch.clone();
                if forge_repo.read_only {
                    item.branch = None;
                }
                let rec = item.clone();
                match save_index(&server.config.codex_home, &records) {
                    Ok(()) => OpenIndexOutcome::Saved(rec),
                    Err(e) => OpenIndexOutcome::SaveErr(e.to_string()),
                }
            } else {
                OpenIndexOutcome::NotFound(format!("No checkout `{tid}` for `{}`.", forge_repo.repo))
            }
        } else if let Some(item) = records
            .iter_mut()
            .find(|r| r.repo == forge_repo.repo && r.bridge == bridge && r.chat == chat)
        {
            item.last_used = now;
            item.read_only = forge_repo.read_only;
            item.base = forge_repo.default_branch.clone();
            if forge_repo.read_only {
                item.branch = None;
            }
            let winner = item.clone();
            match save_index(&server.config.codex_home, &records) {
                Ok(()) => {
                    if created_new_worktree && winner.dir != candidate_dir_str {
                        OpenIndexOutcome::LostRace {
                            winner,
                            remove_dir: candidate_worktree_dir.clone(),
                            remove_branch: candidate_branch.clone(),
                        }
                    } else {
                        OpenIndexOutcome::Saved(winner)
                    }
                }
                Err(e) => OpenIndexOutcome::SaveErr(e.to_string()),
            }
        } else if records.len() >= settings.max_checkouts {
            OpenIndexOutcome::MaxCheckouts {
                remove_dir: candidate_worktree_dir.clone(),
                remove_branch: candidate_branch.clone(),
            }
        } else {
            let rec = CheckoutRecord {
                thread: candidate_thread,
                repo: forge_repo.repo.clone(),
                bridge: bridge.to_string(),
                chat: chat.to_string(),
                device: caller_device,
                dir: candidate_dir_str,
                branch: candidate_branch,
                base: forge_repo.default_branch.clone(),
                app_threads: Vec::new(),
                read_only: forge_repo.read_only,
                created: now,
                last_used: now,
            };
            records.push(rec.clone());
            match save_index(&server.config.codex_home, &records) {
                Ok(()) => OpenIndexOutcome::Saved(rec),
                Err(e) => OpenIndexOutcome::SaveErr(e.to_string()),
            }
        }
    };

    let record = match outcome {
        OpenIndexOutcome::Saved(rec) => rec,
        OpenIndexOutcome::LostRace {
            winner,
            remove_dir,
            remove_branch,
        } => {
            cleanup_losing_worktree(&server, &base_git, &remove_dir, remove_branch.as_deref()).await;
            winner
        }
        OpenIndexOutcome::MaxCheckouts {
            remove_dir,
            remove_branch,
        } => {
            if created_new_worktree {
                cleanup_losing_worktree(&server, &base_git, &remove_dir, remove_branch.as_deref()).await;
            }
            return json_response(
                StatusCode::CONFLICT,
                json!({ "error": format!("Maximum checkouts ({}) reached on this node.", settings.max_checkouts) }),
            );
        }
        OpenIndexOutcome::NotFound(msg) => {
            return json_response(StatusCode::NOT_FOUND, json!({ "error": msg }));
        }
        OpenIndexOutcome::SaveErr(err) => {
            return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
        }
    };

    let worktree_path = PathBuf::from(&record.dir);
    let notes = inspect_and_init_submodules_and_lfs(&server, &worktree_path, &base_url, &forge_repo).await;

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

async fn cleanup_losing_worktree(server: &Server, base_git: &Path, worktree_dir: &Path, branch: Option<&str>) {
    let dir_str = worktree_dir.to_string_lossy().into_owned();
    let _ = run_git(server, Some(base_git), &["worktree", "remove", "--force", &dir_str], false).await;
    let _ = std::fs::remove_dir_all(worktree_dir);
    let _ = run_git(server, Some(base_git), &["worktree", "prune"], false).await;
    if let Some(branch_name) = branch {
        let _ = run_git(server, Some(base_git), &["branch", "-D", branch_name], false).await;
    }
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
    fetch_base_origin(server, base_git, &forge_repo.default_branch).await?;
    Ok(())
}

/// Fetches `origin`'s heads into `refs/remotes/origin/*` in `base.git` and detaches `base.git`'s
/// `HEAD` at `refs/remotes/origin/<default>`'s tip so no `phone/*` or `main` worktree branch is ever
/// overwritten or locked by `base.git`.
async fn fetch_base_origin(server: &Server, base_git: &Path, default_branch: &str) -> Result<(), String> {
    let _ = run_git_checked(
        server,
        Some(base_git),
        &["fetch", "--prune", "--update-head-ok", "origin", "+refs/heads/*:refs/remotes/origin/*"],
        true,
    )
    .await?;
    // `HEAD` is detached at the default branch's tip: git refuses a symbolic `HEAD` outside
    // `refs/heads/`, and a detached one locks no branch a worktree may want.
    let remote_default = format!("refs/remotes/origin/{default_branch}");
    let tip = run_git_checked(server, Some(base_git), &["rev-parse", "--verify", &format!("{remote_default}^{{commit}}")], false).await?;
    run_git_checked(server, Some(base_git), &["update-ref", "--no-deref", "HEAD", tip.trim()], false).await?;
    Ok(())
}

/// Adds or restores the linked worktree from `base.git`. Never deletes an existing folder whose
/// `.git` is missing; refuses with an explicit error so uncommitted files are never destroyed.
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
        return Err("the checkout folder exists but is not a worktree; remove it at the node".to_string());
    }
    if let Some(parent) = worktree_dir.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let dir_str = worktree_dir.to_string_lossy().into_owned();
    let tip = format!("refs/remotes/origin/{}", forge_repo.default_branch);
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

#[derive(Clone, Debug, PartialEq, Eq)]
struct SubmoduleSpec {
    name: String,
    path: String,
    urls: Vec<String>,
}

/// Parses the NUL-terminated output of `git config -f .gitmodules -z --get-regexp '^submodule\..*\.(url|path)$'`.
fn parse_gitmodules_config_z(output: &str) -> Vec<SubmoduleSpec> {
    let mut specs: Vec<(String, Option<String>, Vec<String>)> = Vec::new();
    for entry in output.split('\0') {
        if entry.is_empty() {
            continue;
        }
        let Some((raw_key, raw_val)) = entry.split_once('\n') else {
            continue;
        };
        let key = raw_key.trim();
        let val = raw_val.trim().to_string();
        let Some(rest) = key.strip_prefix("submodule.") else {
            continue;
        };
        let (name, is_path) = if let Some(n) = rest.strip_suffix(".path") {
            (n, true)
        } else if let Some(n) = rest.strip_suffix(".url") {
            (n, false)
        } else {
            continue;
        };
        if name.is_empty() {
            continue;
        }
        let slot = if let Some(existing) = specs.iter_mut().find(|(n, _, _)| n == name) {
            existing
        } else {
            specs.push((name.to_string(), None, Vec::new()));
            specs.last_mut().unwrap()
        };
        if is_path {
            slot.1 = Some(val);
        } else {
            slot.2.push(val);
        }
    }
    specs
        .into_iter()
        .filter(|(_, _, urls)| !urls.is_empty())
        .map(|(name, path, urls)| SubmoduleSpec {
            path: path.unwrap_or_else(|| name.clone()),
            name,
            urls,
        })
        .collect()
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
    if gitmodules_path.is_file() {
        let gm_out = run_git(
            server,
            Some(worktree_dir),
            &[
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.http.allow=always",
                "config",
                "-f",
                ".gitmodules",
                "-z",
                "--get-regexp",
                "^submodule\\..*\\.(url|path)$",
            ],
            true,
        )
        .await;
        if let Ok(out) = gm_out {
            let text = String::from_utf8_lossy(&out.stdout);
            for sub in parse_gitmodules_config_z(&text) {
                if !valid_worktree_rel_path(&sub.path) {
                    notes.push(format!("submodule {} has an invalid path and was skipped", sub.path));
                    continue;
                }
                let config_key = format!("submodule.{}.url", sub.name);
                let _ = run_git(
                    server,
                    Some(worktree_dir),
                    &[
                        "-c",
                        "protocol.allow=never",
                        "-c",
                        "protocol.http.allow=always",
                        "submodule",
                        "init",
                        "--",
                        &sub.path,
                    ],
                    true,
                )
                .await;

                let effective_url = run_git(
                    server,
                    Some(worktree_dir),
                    &[
                        "-c",
                        "protocol.allow=never",
                        "-c",
                        "protocol.http.allow=always",
                        "config",
                        "--get",
                        &config_key,
                    ],
                    true,
                )
                .await
                .ok()
                .filter(|o| o.status.success())
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| sub.urls.last().cloned().unwrap_or_default());

                let all_urls_on_forge = sub
                    .urls
                    .iter()
                    .all(|u| rewrite_forge_submodule_url(u, base_url, parent_repo).is_some());
                let rewritten_effective =
                    rewrite_forge_submodule_url(&effective_url, base_url, parent_repo).filter(|_| all_urls_on_forge);

                if let Some(rewritten_url) = rewritten_effective {
                    let _ = run_git_checked(
                        server,
                        Some(worktree_dir),
                        &[
                            "-c",
                            "protocol.allow=never",
                            "-c",
                            "protocol.http.allow=always",
                            "config",
                            &config_key,
                            &rewritten_url,
                        ],
                        true,
                    )
                    .await;
                    if let Err(err) = run_git_checked(
                        server,
                        Some(worktree_dir),
                        &[
                            "-c",
                            "protocol.allow=never",
                            "-c",
                            "protocol.http.allow=always",
                            "submodule",
                            "update",
                            "--no-fetch",
                            "--",
                            &sub.path,
                        ],
                        true,
                    )
                    .await
                    {
                        if let Err(err2) = run_git_checked(
                            server,
                            Some(worktree_dir),
                            &[
                                "-c",
                                "protocol.allow=never",
                                "-c",
                                "protocol.http.allow=always",
                                "submodule",
                                "update",
                                "--",
                                &sub.path,
                            ],
                            true,
                        )
                        .await
                        {
                            notes.push(format!("submodule {} could not be updated: {err}; {err2}", sub.path));
                        }
                    }
                } else {
                    let _ = run_git(
                        server,
                        Some(worktree_dir),
                        &[
                            "-c",
                            "protocol.allow=never",
                            "-c",
                            "protocol.http.allow=always",
                            "submodule",
                            "deinit",
                            "-f",
                            "--",
                            &sub.path,
                        ],
                        true,
                    )
                    .await;
                    let _ = run_git(
                        server,
                        Some(worktree_dir),
                        &[
                            "-c",
                            "protocol.allow=never",
                            "-c",
                            "protocol.http.allow=always",
                            "config",
                            "--unset-all",
                            &config_key,
                        ],
                        true,
                    )
                    .await;
                    notes.push(format!("submodule {} points outside the forge and was left empty", sub.path));
                }
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

/// `GET /api/repos/state?thread=&intent=`: branch, ahead/behind against `base.git`'s default branch
/// tip, uncommitted file stat list, `read_only`, `private`, `budget`, `warnings`, `allow_main`,
/// `merge_in_progress`, and (when `intent` is given) a single-use 32-hex confirmation nonce `confirm`.
async fn repo_state(
    State(server): State<Arc<Server>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let (_caller_device, caller_nonce_id) = match require_control_caller_with_session(&server, &headers) {
        Ok(ids) => ids,
        Err(resp) => return resp,
    };
    let (settings, base_url, token) = match ensure_service_ready(&server).await {
        Ok(ready) => ready,
        Err(resp) => return resp,
    };
    let thread = query.get("thread").map(String::as_str).unwrap_or("");
    let (record, worktree_dir) = match find_checked_out_thread(&server, thread) {
        Ok(found) => found,
        Err(resp) => return resp,
    };

    let intent_opt = query.get("intent").map(|s| s.trim()).filter(|s| !s.is_empty());
    if let Some(intent) = intent_opt
        && !VALID_INTENTS.contains(&intent)
    {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "intent must be commit, push, update, merge, pr, main, budget, or access" }),
        );
    }
    if intent_opt == Some("access") && server.is_bridge_caller(&headers) {
        return full_access_bridge_refusal();
    }

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

    let (used, limit, resets_at) = server.chat_repos.budget_snapshot(&record, settings.tokens_per_day);
    let warnings = server.chat_repos.warnings_for(&server.config.codex_home, &record);
    let allow_main = settings.allow_main.iter().any(|r| r == &record.repo);
    let full_access = server.chat_repos.effective_full_access(&settings, &record);
    let merge_in_progress = run_git(&server, Some(&worktree_dir), &["rev-parse", "-q", "--verify", "MERGE_HEAD"], false)
        .await
        .is_ok_and(|output| output.status.success());

    let mut payload = json!({
        "repo": record.repo,
        "branch": record.branch,
        "base": record.base,
        "ahead": ahead,
        "behind": behind,
        "uncommitted": uncommitted,
        "read_only": record.read_only,
        "private": private,
        "budget": {
            "used": used,
            "limit": limit,
            "resets_at": resets_at,
        },
        "warnings": warnings,
        "allow_main": allow_main,
        "full_access_allowed": settings.full_access,
        "full_access": full_access,
        "merge_in_progress": merge_in_progress,
    });
    if let Some(intent) = intent_opt {
        let nonce = server.chat_repos.issue_nonce(&caller_nonce_id, &record.thread, intent);
        payload["confirm"] = json!(nonce);
    }

    json_response(StatusCode::OK, payload)
}

async fn ahead_behind(server: &Server, worktree_dir: &Path, base_branch: &str) -> (u64, u64) {
    let range = format!("refs/remotes/origin/{base_branch}...HEAD");
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
    let mut untracked_rels = Vec::new();
    for line in untracked.lines() {
        let rel = line.trim();
        if rel.is_empty() || seen.contains(rel) || !valid_worktree_rel_path(rel) {
            continue;
        }
        seen.insert(rel.to_string());
        untracked_rels.push(rel.to_string());
    }
    if !untracked_rels.is_empty() {
        let wt = worktree_dir.to_path_buf();
        let counted = tokio::task::spawn_blocking(move || {
            untracked_rels
                .into_iter()
                .map(|rel| {
                    let added = count_file_lines(&wt.join(&rel));
                    json!({
                        "path": rel,
                        "added": added,
                        "removed": 0u64,
                    })
                })
                .collect::<Vec<Value>>()
        })
        .await
        .map_err(|e| format!("failed to count untracked file lines: {e}"))?;
        entries.extend(counted);
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

/// Counts lines of a regular file (never following symlinks) of at most `MAX_UNTRACKED_STAT_BYTES`
/// (8 MiB) in a streaming 8 KiB buffer. Returns `None` for symlinks, special files, or files > 8 MiB.
fn count_file_lines(path: &Path) -> Option<u64> {
    use std::io::Read;

    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.file_type().is_file() || meta.len() > MAX_UNTRACKED_STAT_BYTES {
        return None;
    }
    let mut file = std::fs::File::open(path).ok()?;
    let mut buf = [0u8; 8192];
    let mut total_bytes = 0u64;
    let mut newlines = 0u64;
    let mut last_byte = None;
    loop {
        let n = file.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        total_bytes = total_bytes.saturating_add(n as u64);
        if total_bytes > MAX_UNTRACKED_STAT_BYTES {
            return None;
        }
        newlines += buf[..n].iter().filter(|&&b| b == b'\n').count() as u64;
        last_byte = Some(buf[n - 1]);
    }
    match last_byte {
        None => Some(0),
        Some(b'\n') => Some(newlines),
        Some(_) => Some(newlines + 1),
    }
}

async fn merge_base(server: &Server, worktree_dir: &Path, base_branch: &str) -> Result<String, String> {
    let base_ref = format!("refs/remotes/origin/{base_branch}");
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
            match run_git_diff_capped(&server, &worktree_dir, &["diff", &range, "--", rel], true).await {
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
    let tracked_diff = run_git_diff_capped(server, worktree_dir, &["diff", "HEAD", "--", rel], true).await?;
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
        let full = worktree_dir.join(rel);
        let Ok(meta) = std::fs::symlink_metadata(&full) else {
            return Ok(String::new());
        };
        if !meta.file_type().is_file() || meta.len() > MAX_UNTRACKED_STAT_BYTES {
            return Ok("(cut: open the file to see all)".to_string());
        }
        return run_git_diff_capped(server, worktree_dir, &["diff", "--no-index", "--", "/dev/null", rel], false).await;
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

/// Derives the user's home directory as three levels above `server.config.user_config`
/// (`<home>/.config/dreamference/config.toml`), matching `server.rs`'s `night_candidates`.
fn user_home_dir(server: &Server) -> Option<PathBuf> {
    let cfg = server.config.user_config.as_deref()?;
    Some(cfg.parent()?.parent()?.parent()?.to_path_buf())
}

/// Reads `user.name` and `user.email` from the user's `.gitconfig` (or `.config/git/config`)
/// without exposing the user's global git configuration to forge commands.
async fn read_owner_git_identity(server: &Server) -> Result<(String, String), String> {
    let mut candidates = Vec::new();
    if let Some(home) = user_home_dir(server) {
        candidates.push(home.join(".gitconfig"));
        candidates.push(home.join(".config/git/config"));
    }
    if !candidates.iter().any(|p| p.exists()) {
        candidates.push(server.config.codex_home.join(".gitconfig"));
        candidates.push(server.config.codex_home.join(".config/git/config"));
    }

    let mut name: Option<String> = None;
    let mut email: Option<String> = None;
    for file in candidates {
        if !file.exists() {
            continue;
        }
        let file_str = file.to_string_lossy().into_owned();
        if name.is_none()
            && let Ok(out) = run_git(server, None, &["config", "--file", &file_str, "--get", "user.name"], false).await
            && out.status.success()
        {
            let val = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !val.is_empty() {
                name = Some(val);
            }
        }
        if email.is_none()
            && let Ok(out) = run_git(server, None, &["config", "--file", &file_str, "--get", "user.email"], false).await
            && out.status.success()
        {
            let val = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !val.is_empty() {
                email = Some(val);
            }
        }
    }

    let Some(user_name) = name else {
        return Err("git user.name is not set in ~/.gitconfig".to_string());
    };
    let Some(user_email) = email else {
        return Err("git user.email is not set in ~/.gitconfig".to_string());
    };
    Ok((user_name, user_email))
}

struct MutationRequest {
    caller_nonce_id: String,
    confirm: String,
    settings: ChatReposSettings,
    base_url: String,
    token: String,
    request: Value,
    record: CheckoutRecord,
    worktree_dir: PathBuf,
}

impl ChatRepos {
    /// Checks whether `nonce` is a valid unexpired confirmation nonce for `(caller, thread, intent)`
    /// without consuming it yet.
    pub fn check_nonce(&self, caller: &str, thread: &str, intent: &str, nonce: &str) -> bool {
        if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return false;
        }
        let mut nonces = self.nonces.lock().unwrap_or_else(|p| p.into_inner());
        nonces.retain(|_, v| v.issued_at.elapsed() < CONFIRM_TTL);
        nonces
            .get(nonce)
            .is_some_and(|entry| entry.caller == caller && entry.thread == thread && entry.intent == intent)
    }
}

/// Common pre-flight for every mutating `/api/repos/*` POST route: checks caller control scope,
/// forge readiness, JSON body, checked-out `thread`, and verifies that a valid `confirm` nonce is
/// present without consuming it until body and writability validation succeed.
async fn prepare_mutation_request(
    server: &Server,
    headers: &HeaderMap,
    body: &Bytes,
    intent: &str,
) -> Result<MutationRequest, Response> {
    let (_caller_device, caller_nonce_id) = require_control_caller_with_session(server, headers)?;
    let (settings, base_url, token) = ensure_service_ready(server).await?;
    let request: Value = serde_json::from_slice(body)
        .map_err(|_| json_response(StatusCode::BAD_REQUEST, json!({ "error": "send JSON" })))?;
    let thread = request.get("thread").and_then(Value::as_str).unwrap_or("").trim();
    let confirm = request
        .get("confirm")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let (record, worktree_dir) = find_checked_out_thread(server, thread)?;
    if !server
        .chat_repos
        .check_nonce(&caller_nonce_id, &record.thread, intent, &confirm)
    {
        return Err(json_response(
            StatusCode::CONFLICT,
            json!({ "error": "confirmation missing or expired" }),
        ));
    }
    Ok(MutationRequest {
        caller_nonce_id,
        confirm,
        settings,
        base_url,
        token,
        request,
        record,
        worktree_dir,
    })
}

fn consume_mutation_nonce(server: &Server, req: &MutationRequest, intent: &str) -> Result<(), Response> {
    if !server
        .chat_repos
        .consume_nonce(&req.caller_nonce_id, &req.record.thread, intent, &req.confirm)
    {
        return Err(json_response(
            StatusCode::CONFLICT,
            json!({ "error": "confirmation missing or expired" }),
        ));
    }
    Ok(())
}

fn read_only_response() -> Response {
    json_response(
        StatusCode::FORBIDDEN,
        json!({ "error": "this repository is read-only on the forge" }),
    )
}

/// `POST /api/repos/commit {thread, message, confirm}` (§4.4): stages all changes and commits with
/// the node owner's git identity, never adding a trailer or messenger name.
async fn repo_commit(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "commit").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    let message = mreq.request.get("message").and_then(Value::as_str).unwrap_or("").trim();
    if message.is_empty() || message.chars().count() > MAX_COMMIT_MESSAGE_CHARS {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "commit message must be non-empty and at most 5,000 characters" }),
        );
    }
    let (user_name, user_email) = match read_owner_git_identity(&server).await {
        Ok(id) => id,
        Err(reason) => return json_response(StatusCode::CONFLICT, json!({ "error": reason })),
    };
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "commit") {
        return resp;
    }
    let record = mreq.record;
    let worktree_dir = mreq.worktree_dir;

    let repo_lock = server.chat_repos.repo_mutex(&record.repo);
    let _repo_guard = repo_lock.lock().await;

    if let Err(err) = run_git_checked(&server, Some(&worktree_dir), &["add", "-A"], false).await {
        return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
    }

    let staged_clean = run_git(&server, Some(&worktree_dir), &["diff", "--cached", "--quiet"], false)
        .await
        .map(|o| o.status.success())
        .unwrap_or(false);
    let merge_in_progress = run_git(&server, Some(&worktree_dir), &["rev-parse", "-q", "--verify", "MERGE_HEAD"], false)
        .await
        .map(|o| o.status.success())
        .unwrap_or(false);
    if staged_clean && !merge_in_progress {
        return json_response(StatusCode::CONFLICT, json!({ "error": "nothing to commit" }));
    }

    let name_cfg = format!("user.name={user_name}");
    let email_cfg = format!("user.email={user_email}");
    let commit_out = match run_git(
        &server,
        Some(&worktree_dir),
        &["-c", &name_cfg, "-c", &email_cfg, "commit", "-m", message],
        false,
    )
    .await
    {
        Ok(out) => out,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };
    if !commit_out.status.success() {
        let combined = format!(
            "{}\n{}",
            String::from_utf8_lossy(&commit_out.stderr),
            String::from_utf8_lossy(&commit_out.stdout)
        );
        let lower = combined.to_ascii_lowercase();
        if lower.contains("nothing to commit") || lower.contains("nothing added to commit") {
            return json_response(StatusCode::CONFLICT, json!({ "error": "nothing to commit" }));
        }
        return json_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({ "error": sanitize_error(combined.trim(), &server.config.state_dir) }),
        );
    }

    let commit_sha = match run_git_checked(&server, Some(&worktree_dir), &["rev-parse", "HEAD"], false).await {
        Ok(s) => s.trim().to_string(),
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };
    let numstat = match run_git_checked(
        &server,
        Some(&worktree_dir),
        &["show", "--first-parent", "--numstat", "--format=", "HEAD"],
        false,
    )
    .await
    {
        Ok(s) => s,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };
    let files = parse_numstat(&numstat);
    let total_added: u64 = files.iter().filter_map(|f| f.get("added").and_then(Value::as_u64)).sum();
    let total_removed: u64 = files.iter().filter_map(|f| f.get("removed").and_then(Value::as_u64)).sum();

    json_response(
        StatusCode::OK,
        json!({
            "commit": commit_sha,
            "files": files,
            "added": total_added,
            "removed": total_removed,
        }),
    )
}

async fn lookup_forge_repo(server: &Server, base_url: &str, token: &str, repo_name: &str) -> Result<ForgeRepo, Response> {
    let listing = get_or_fetch_listing(server, base_url, token).await.map_err(|err| {
        json_response(
            StatusCode::BAD_GATEWAY,
            json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
        )
    })?;
    listing.into_iter().find(|r| r.repo == repo_name).ok_or_else(|| {
        json_response(
            StatusCode::NOT_FOUND,
            json!({ "error": format!("Repository `{repo_name}` is not in the forge listing.") }),
        )
    })
}

fn anonymity_error_response() -> Response {
    json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        json!({
            "refused": "anonymity",
            "error": "the anonymity check could not run",
            "reason": "the anonymity check could not run",
        }),
    )
}

/// Runs the pre-push anonymity check (when `!forge_repo.private`) and pushes `refs/heads/<branch>:refs/heads/<branch>`
/// without `--force`. Caller must hold the repository's mutex.
async fn push_worktree_branch(
    server: &Server,
    settings: &ChatReposSettings,
    record: &CheckoutRecord,
    worktree_dir: &Path,
    forge_repo: &ForgeRepo,
) -> Result<Value, Response> {
    if record.read_only || forge_repo.read_only || forge_repo.archived {
        return Err(read_only_response());
    }
    let Some(branch) = record.branch.as_deref() else {
        return Err(read_only_response());
    };
    let expected_phone = format!("phone/{}", record.thread);
    if branch != expected_phone && branch != record.base {
        return Err(json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "refusing to push an unrecognized branch" }),
        ));
    }
    if branch == record.base && !settings.allow_main.iter().any(|r| r == &record.repo) {
        return Err(json_response(
            StatusCode::FORBIDDEN,
            json!({
                "error": format!(
                    "Working on {1} is not allowed for `{0}`: run `ling chat repos allow-main {0}` at the node.",
                    record.repo, record.base
                )
            }),
        ));
    }

    let remote_branch_ref = format!("refs/remotes/origin/{branch}");
    let remote_is_ancestor = run_git(
        server,
        Some(worktree_dir),
        &["merge-base", "--is-ancestor", &remote_branch_ref, "HEAD"],
        false,
    )
    .await
    .map(|o| o.status.success())
    .unwrap_or(false);

    let scan_base = if remote_is_ancestor {
        remote_branch_ref.clone()
    } else {
        merge_base(server, worktree_dir, &record.base).await.map_err(|err| {
            if !forge_repo.private {
                anonymity_error_response()
            } else {
                json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }))
            }
        })?
    };

    let range = format!("{scan_base}..HEAD");
    let rev_list_out = run_git_checked(server, Some(worktree_dir), &["rev-list", "--reverse", &range], false)
        .await
        .map_err(|err| {
            if !forge_repo.private {
                anonymity_error_response()
            } else {
                json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }))
            }
        })?;
    let commits: Vec<&str> = rev_list_out.lines().map(str::trim).filter(|s| !s.is_empty()).collect();

    let mut anonymity_note: Option<&'static str> = None;
    if !forge_repo.private {
        let denylist_res = match user_home_dir(server) {
            Some(home) => match crate::anonymity::load_denylist(&home) {
                Ok(Some(text)) => Ok(Some(text)),
                Ok(None) => crate::anonymity::load_denylist(&server.config.codex_home),
                Err(err) => Err(err),
            },
            None => crate::anonymity::load_denylist(&server.config.codex_home),
        };
        let denylist_text = denylist_res.map_err(|_| anonymity_error_response())?;
        match denylist_text {
            None => {
                anonymity_note = Some("no denylist on this node");
            }
            Some(text) => {
                let patterns = crate::anonymity::denylist_patterns(&text);
                if !patterns.is_empty() {
                    let mut findings = Vec::new();
                    for sha in &commits {
                        let meta = run_git_checked(
                            server,
                            Some(worktree_dir),
                            &[
                                "-c",
                                "core.quotePath=false",
                                "show",
                                "-s",
                                "--format=%an%x00%ae%x00%cn%x00%ce%x00%B",
                                sha,
                            ],
                            false,
                        )
                        .await
                        .map_err(|_| anonymity_error_response())?;
                        {
                            let mut parts = meta.splitn(5, '\0');
                            let an = parts.next().unwrap_or("");
                            let ae = parts.next().unwrap_or("");
                            let cn = parts.next().unwrap_or("");
                            let ce = parts.next().unwrap_or("");
                            let msg = parts.next().unwrap_or("");
                            let author_blob = format!("{an}\n{ae}\n{cn}\n{ce}");
                            crate::anonymity::scan_metadata(&patterns, "author", &author_blob, &mut findings);
                            crate::anonymity::scan_metadata(&patterns, "commit message", msg, &mut findings);
                        }
                        let names_out = run_git_checked(
                            server,
                            Some(worktree_dir),
                            &[
                                "-c",
                                "core.quotePath=false",
                                "diff-tree",
                                "-m",
                                "--no-commit-id",
                                "--name-only",
                                "-r",
                                "--root",
                                sha,
                            ],
                            false,
                        )
                        .await
                        .map_err(|_| anonymity_error_response())?;
                        {
                            let paths: Vec<String> = names_out
                                .lines()
                                .map(str::trim)
                                .filter(|s| !s.is_empty())
                                .map(str::to_string)
                                .collect();
                            crate::anonymity::scan_paths(&patterns, &paths, &mut findings);
                        }
                        let patch = run_git_checked(
                            server,
                            Some(worktree_dir),
                            &[
                                "-c",
                                "core.quotePath=false",
                                "diff-tree",
                                "-m",
                                "-r",
                                "-p",
                                "-U0",
                                "--text",
                                "--no-renames",
                                "--no-textconv",
                                "--no-ext-diff",
                                "--root",
                                sha,
                            ],
                            false,
                        )
                        .await
                        .map_err(|_| anonymity_error_response())?;
                        crate::anonymity::scan_unified_patch(&patterns, &patch, &mut findings);
                    }
                    if !findings.is_empty() {
                        let findings_json: Vec<Value> =
                            findings.iter().map(crate::anonymity::Finding::to_json).collect();
                        return Err(json_response(
                            StatusCode::UNPROCESSABLE_ENTITY,
                            json!({
                                "refused": "anonymity",
                                "findings": findings_json,
                            }),
                        ));
                    }
                }
            }
        }
    }

    let base_git = root_dir(&server.config.codex_home).join(&record.repo).join("base.git");
    let plain_url = strip_url_credentials(&forge_repo.clone_url);
    let _ = run_git(server, Some(&base_git), &["config", "remote.origin.url", &plain_url], false).await;

    let refspec = format!("refs/heads/{branch}:refs/heads/{branch}");
    let push_out = run_git(server, Some(worktree_dir), &["push", "origin", &refspec], true)
        .await
        .map_err(|err| json_response(StatusCode::BAD_GATEWAY, json!({ "refused": "forge", "reason": err })))?;

    if !push_out.status.success() {
        let combined = format!(
            "{}\n{}",
            String::from_utf8_lossy(&push_out.stderr),
            String::from_utf8_lossy(&push_out.stdout)
        )
        .to_ascii_lowercase();
        let non_ff = combined.contains("non-fast-forward")
            || combined.contains("fetch first")
            || combined.contains("[rejected]");
        let reason = if non_ff {
            if branch == record.base {
                "the forge refused the push: the forge's main moved; /update first (the forge's branch moved; /update first)"
            } else {
                "the forge refused the push: the forge's branch moved; /update first"
            }
        } else {
            "the forge refused the push"
        };
        return Err(json_response(
            StatusCode::BAD_GATEWAY,
            json!({
                "refused": "forge",
                "reason": reason,
                "error": reason,
            }),
        ));
    }

    let _ = run_git(server, Some(worktree_dir), &["update-ref", &remote_branch_ref, "HEAD"], false).await;

    let mut payload = json!({
        "pushed": commits.len(),
        "branch": branch,
    });
    if let Some(note) = anonymity_note {
        payload["anonymity_check"] = json!(note);
    }
    Ok(payload)
}

async fn branch_is_ahead_of_remote(server: &Server, worktree_dir: &Path, branch: &str) -> bool {
    let remote_ref = format!("refs/remotes/origin/{branch}");
    let Ok(remote_out) = run_git(server, Some(worktree_dir), &["rev-parse", "--verify", "--quiet", &remote_ref], false).await else {
        return true;
    };
    if !remote_out.status.success() {
        return true;
    }
    let range = format!("{remote_ref}..HEAD");
    let Ok(count_str) = run_git_checked(server, Some(worktree_dir), &["rev-list", "--count", &range], false).await else {
        return true;
    };
    count_str.trim().parse::<u64>().unwrap_or(1) > 0
}

/// `POST /api/repos/push {thread, confirm}` (§4.5): runs the anonymity check on non-private
/// repositories and pushes `phone/<thread>` (or the default branch when allowed) to the forge without force.
async fn repo_push(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "push").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    let forge_repo = match lookup_forge_repo(&server, &mreq.base_url, &mreq.token, &mreq.record.repo).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    if forge_repo.read_only || forge_repo.archived {
        return read_only_response();
    }
    if mreq.record.branch.as_deref() == Some(mreq.record.base.as_str())
        && !mreq.settings.allow_main.iter().any(|r| r == &mreq.record.repo)
    {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({
                "error": format!(
                    "Working on {1} is not allowed for `{0}`: run `ling chat repos allow-main {0}` at the node.",
                    mreq.record.repo, mreq.record.base
                )
            }),
        );
    }
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "push") {
        return resp;
    }

    let repo_lock = server.chat_repos.repo_mutex(&mreq.record.repo);
    let _repo_guard = repo_lock.lock().await;

    match push_worktree_branch(&server, &mreq.settings, &mreq.record, &mreq.worktree_dir, &forge_repo).await {
        Ok(payload) => json_response(StatusCode::OK, payload),
        Err(resp) => resp,
    }
}

/// `POST /api/repos/update {thread, confirm, abort?}` (§4.6): fetches `base.git` and merges
/// `origin/<default>` into the worktree, or aborts an in-progress merge when `abort: true`.
async fn repo_update(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "update").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    let forge_repo = match lookup_forge_repo(&server, &mreq.base_url, &mreq.token, &mreq.record.repo).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    if forge_repo.read_only || forge_repo.archived {
        return read_only_response();
    }
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "update") {
        return resp;
    }
    let record = mreq.record;
    let worktree_dir = mreq.worktree_dir;

    let repo_lock = server.chat_repos.repo_mutex(&record.repo);
    let _repo_guard = repo_lock.lock().await;

    let abort = mreq.request.get("abort").and_then(Value::as_bool).unwrap_or(false);
    if abort {
        return match run_git_checked(&server, Some(&worktree_dir), &["merge", "--abort"], false).await {
            Ok(_) => json_response(StatusCode::OK, json!({ "aborted": true })),
            Err(err) => json_response(StatusCode::CONFLICT, json!({ "error": err })),
        };
    }

    let base_git = root_dir(&server.config.codex_home).join(&record.repo).join("base.git");
    if let Err(err) = sync_base_clone(&server, &base_git, &forge_repo).await {
        return json_response(StatusCode::BAD_GATEWAY, json!({ "error": err }));
    }

    // The merge commit is the owner's, as a commit is (§4.4).
    let (user_name, user_email) = match read_owner_git_identity(&server).await {
        Ok(identity) => identity,
        Err(err) => return json_response(StatusCode::CONFLICT, json!({ "error": err })),
    };
    let name_cfg = format!("user.name={user_name}");
    let email_cfg = format!("user.email={user_email}");
    let target_ref = format!("refs/remotes/origin/{}", record.base);
    let merge_args = ["-c", name_cfg.as_str(), "-c", email_cfg.as_str(), "merge", "--no-edit", target_ref.as_str()];
    let merge_out = match run_git(&server, Some(&worktree_dir), &merge_args, false).await {
        Ok(out) => out,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };

    if merge_out.status.success() {
        return json_response(StatusCode::OK, json!({ "updated": true, "conflicts": [] }));
    }

    let unmerged_out = run_git_checked(
        &server,
        Some(&worktree_dir),
        &["diff", "--name-only", "--diff-filter=U"],
        false,
    )
    .await
    .unwrap_or_default();
    let conflicts: Vec<String> = unmerged_out
        .lines()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect();
    if !conflicts.is_empty() {
        return json_response(StatusCode::CONFLICT, json!({ "conflicts": conflicts }));
    }

    let detail = String::from_utf8_lossy(&merge_out.stderr);
    json_response(
        StatusCode::CONFLICT,
        json!({
            "error": sanitize_error(detail.trim(), &server.config.state_dir),
            "conflicts": [],
        }),
    )
}

/// Finds an open pull request on the forge whose `head.ref` is `branch`, or creates one titled
/// `"phone/<thread> from <repo>"` (never using the thread's name or any message text).
async fn find_or_create_open_pr(
    base_url: &str,
    token: &str,
    repo_name: &str,
    branch: &str,
    base_branch: &str,
    thread: &str,
) -> Result<u64, String> {
    let list_path = format!("/api/v1/repos/{repo_name}/pulls?state=open&limit=50");
    let (status, body) = forge_get(base_url, &list_path, token, FORGE_TIMEOUT).await?;
    if status != 200 {
        return Err(format!("forge returned HTTP {status} when listing pull requests"));
    }
    let parsed: Value = serde_json::from_slice(&body).map_err(|_| "forge returned invalid JSON for pulls".to_string())?;
    let items = parsed
        .as_array()
        .or_else(|| parsed.get("data").and_then(Value::as_array))
        .ok_or_else(|| "forge pulls response was not a list".to_string())?;

    for item in items {
        let head_ref = item
            .get("head")
            .and_then(|h| h.get("ref").and_then(Value::as_str).or_else(|| h.as_str()))
            .unwrap_or("");
        if head_ref == branch
            && let Some(num) = item.get("number").or_else(|| item.get("index")).and_then(Value::as_u64)
        {
            return Ok(num);
        }
    }

    let create_path = format!("/api/v1/repos/{repo_name}/pulls");
    let title = format!("phone/{thread} from {repo_name}");
    let create_body = json!({
        "head": branch,
        "base": base_branch,
        "title": title,
    });
    let (c_status, c_body) = forge_post(base_url, &create_path, token, &create_body, FORGE_TIMEOUT).await?;
    if c_status != 200 && c_status != 201 {
        return Err(format!("forge returned HTTP {c_status} when creating pull request"));
    }
    let created: Value = serde_json::from_slice(&c_body).map_err(|_| "forge returned invalid JSON for created pull request".to_string())?;
    created
        .get("number")
        .or_else(|| created.get("index"))
        .and_then(Value::as_u64)
        .ok_or_else(|| "forge pull request response missing number".to_string())
}

/// `POST /api/repos/pr {thread, confirm}` (§4.6): pushes if the local branch is ahead of the
/// forge's, then finds or opens a pull request and answers `{number, url}` with the relative
/// `/forge/<owner>/<repo>/pulls/<number>` proxy path.
async fn repo_pr(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "pr").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    let forge_repo = match lookup_forge_repo(&server, &mreq.base_url, &mreq.token, &mreq.record.repo).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    if forge_repo.read_only || forge_repo.archived {
        return read_only_response();
    }
    let Some(branch) = mreq.record.branch.clone() else {
        return read_only_response();
    };
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "pr") {
        return resp;
    }

    let repo_lock = server.chat_repos.repo_mutex(&mreq.record.repo);
    let _repo_guard = repo_lock.lock().await;

    if branch_is_ahead_of_remote(&server, &mreq.worktree_dir, &branch).await
        && let Err(resp) = push_worktree_branch(&server, &mreq.settings, &mreq.record, &mreq.worktree_dir, &forge_repo).await
    {
        return resp;
    }

    let number = match find_or_create_open_pr(
        &mreq.base_url,
        &mreq.token,
        &mreq.record.repo,
        &branch,
        &mreq.record.base,
        &mreq.record.thread,
    )
    .await
    {
        Ok(n) => n,
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    };
    let url = format!("/forge/{}/pulls/{number}", mreq.record.repo);
    json_response(StatusCode::OK, json!({ "number": number, "url": url }))
}

/// `POST /api/repos/merge {thread, confirm}` (§4.6): pushes if ahead, opens a pull request if none
/// exists, checks combined commit status and PR mergeability, and merges via the forge API.
async fn repo_merge(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "merge").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    let forge_repo = match lookup_forge_repo(&server, &mreq.base_url, &mreq.token, &mreq.record.repo).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    if forge_repo.read_only || forge_repo.archived {
        return read_only_response();
    }
    let Some(branch) = mreq.record.branch.clone() else {
        return read_only_response();
    };
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "merge") {
        return resp;
    }
    let base_url = mreq.base_url;
    let token = mreq.token;
    let record = mreq.record;
    let worktree_dir = mreq.worktree_dir;

    let repo_lock = server.chat_repos.repo_mutex(&record.repo);
    let _repo_guard = repo_lock.lock().await;

    if branch_is_ahead_of_remote(&server, &worktree_dir, &branch).await
        && let Err(resp) = push_worktree_branch(&server, &mreq.settings, &record, &worktree_dir, &forge_repo).await
    {
        return resp;
    }

    let pr_number = match find_or_create_open_pr(&base_url, &token, &record.repo, &branch, &record.base, &record.thread).await {
        Ok(n) => n,
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    };

    let head_sha = match run_git_checked(&server, Some(&worktree_dir), &["rev-parse", "HEAD"], false).await {
        Ok(s) => s.trim().to_string(),
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };

    let status_path = format!("/api/v1/repos/{}/commits/{head_sha}/status", record.repo);
    match forge_get(&base_url, &status_path, &token, FORGE_TIMEOUT).await {
        Ok((200, s_body)) => {
            if let Ok(status_json) = serde_json::from_slice::<Value>(&s_body) {
                let state = status_json.get("state").and_then(Value::as_str).unwrap_or("");
                let total_count = status_json
                    .get("total_count")
                    .and_then(Value::as_u64)
                    .or_else(|| status_json.get("statuses").and_then(Value::as_array).map(|a| a.len() as u64))
                    .unwrap_or(0);
                if state == "failure" || state == "error" {
                    return json_response(StatusCode::CONFLICT, json!({ "error": "a required check is red" }));
                }
                if state == "pending" && total_count > 0 {
                    return json_response(StatusCode::CONFLICT, json!({ "error": "checks are still running" }));
                }
            }
        }
        Ok((code, _)) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": format!("forge returned HTTP {code} for commit status") }),
            );
        }
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    }

    let pr_path = format!("/api/v1/repos/{}/pulls/{pr_number}", record.repo);
    match forge_get(&base_url, &pr_path, &token, FORGE_TIMEOUT).await {
        Ok((200, p_body)) => {
            if let Ok(pr_json) = serde_json::from_slice::<Value>(&p_body)
                && pr_json.get("mergeable").and_then(Value::as_bool) == Some(false)
            {
                return json_response(
                    StatusCode::CONFLICT,
                    json!({ "error": format!("conflicts with {}: /update, then /merge", record.base) }),
                );
            }
        }
        Ok((code, _)) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": format!("forge returned HTTP {code} for pull request details") }),
            );
        }
        Err(err) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
            );
        }
    }

    let merge_path = format!("/api/v1/repos/{}/pulls/{pr_number}/merge", record.repo);
    match forge_post(&base_url, &merge_path, &token, &json!({ "Do": "merge" }), FORGE_TIMEOUT).await {
        Ok((200 | 204, _)) => json_response(
            StatusCode::OK,
            json!({
                "merged": true,
                "branch": branch,
                "into": record.base,
            }),
        ),
        Ok((405 | 409, _)) => json_response(
            StatusCode::CONFLICT,
            json!({ "error": format!("conflicts with {}: /update, then /merge", record.base) }),
        ),
        Ok((code, _)) => json_response(
            StatusCode::BAD_GATEWAY,
            json!({ "error": format!("forge returned HTTP {code} when merging pull request") }),
        ),
        Err(err) => json_response(
            StatusCode::BAD_GATEWAY,
            json!({ "error": sanitize_error(&err, &server.config.state_dir) }),
        ),
    }
}

async fn is_branch_checked_out_in_other_worktree(
    server: &Server,
    base_git: &Path,
    current_worktree: &Path,
    branch: &str,
) -> Result<bool, String> {
    let _ = run_git(server, Some(base_git), &["worktree", "prune"], false).await;
    let out = run_git_checked(server, Some(base_git), &["worktree", "list", "--porcelain"], false).await?;
    let target_ref = format!("refs/heads/{branch}");
    let mut wt_path: Option<PathBuf> = None;
    let mut wt_branch: Option<String> = None;

    let matches_other = |path: &Option<PathBuf>, br: &Option<String>| -> bool {
        if let (Some(p), Some(b)) = (path, br)
            && b == &target_ref
            && !same_worktree_dir(p, current_worktree)
            && !same_worktree_dir(p, base_git)
        {
            return true;
        }
        false
    };

    for line in out.lines() {
        if line.trim().is_empty() {
            if matches_other(&wt_path, &wt_branch) {
                return Ok(true);
            }
            wt_path = None;
            wt_branch = None;
        } else if let Some(rest) = line.strip_prefix("worktree ") {
            wt_path = Some(PathBuf::from(rest.trim()));
        } else if let Some(rest) = line.strip_prefix("branch ") {
            wt_branch = Some(rest.trim().to_string());
        }
    }
    if matches_other(&wt_path, &wt_branch) {
        return Ok(true);
    }
    Ok(false)
}

/// `POST /api/repos/main {thread, confirm}` (§3.3): switches the thread's checkout to the
/// repository's default branch (`record.base`) when the repository is listed in
/// `[chat_repos] allow_main`, the branch is not checked out in another worktree, and the worktree
/// has no uncommitted changes.
async fn repo_main(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "main").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    if mreq.record.read_only {
        return read_only_response();
    }
    if !mreq.settings.allow_main.iter().any(|r| r == &mreq.record.repo) {
        return json_response(
            StatusCode::FORBIDDEN,
            json!({
                "error": format!(
                    "Working on main is not allowed for `{0}`: run `ling chat repos allow-main {0}` at the node.",
                    mreq.record.repo
                )
            }),
        );
    }

    let forge_repo = match lookup_forge_repo(&server, &mreq.base_url, &mreq.token, &mreq.record.repo).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    if forge_repo.read_only || forge_repo.archived {
        return read_only_response();
    }
    let record = mreq.record.clone();
    let worktree_dir = mreq.worktree_dir.clone();
    let default_branch = record.base.clone();

    let repo_lock = server.chat_repos.repo_mutex(&record.repo);
    let _repo_guard = repo_lock.lock().await;

    let uncommitted = match uncommitted_stats(&server, &worktree_dir).await {
        Ok(u) => u,
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
    };
    if !uncommitted.is_empty() {
        return json_response(
            StatusCode::CONFLICT,
            json!({ "error": "worktree has uncommitted changes; commit or discard them before switching to main" }),
        );
    }

    let base_git = root_dir(&server.config.codex_home).join(&record.repo).join("base.git");
    if let Err(err) = sync_base_clone(&server, &base_git, &forge_repo).await {
        return json_response(StatusCode::BAD_GATEWAY, json!({ "error": err }));
    }

    match is_branch_checked_out_in_other_worktree(&server, &base_git, &worktree_dir, &default_branch).await {
        Ok(true) => {
            return json_response(
                StatusCode::CONFLICT,
                json!({
                    "error": format!(
                        "`{default_branch}` is already checked out in another worktree of `{}`.",
                        record.repo
                    )
                }),
            );
        }
        Err(err) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err })),
        Ok(false) => {}
    }

    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "main") {
        return resp;
    }

    let remote_default = format!("refs/remotes/origin/{default_branch}");
    if let Err(err) = run_git_checked(
        &server,
        Some(&worktree_dir),
        &["checkout", "-B", &default_branch, "--no-track", &remote_default],
        false,
    )
    .await
    {
        return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err }));
    }

    {
        let _idx_guard = server.chat_repos.index_lock.lock().await;
        let _io_guard = server.chat_repos.index_io_lock.lock().unwrap_or_else(|p| p.into_inner());
        let mut records = load_index(&server.config.codex_home);
        if let Some(item) = records.iter_mut().find(|r| r.thread == record.thread) {
            item.branch = Some(default_branch.clone());
            item.last_used = now_secs();
        }
        if let Err(err) = save_index(&server.config.codex_home, &records) {
            return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": err.to_string() }));
        }
    }

    json_response(
        StatusCode::OK,
        json!({
            "repo": record.repo,
            "branch": default_branch,
        }),
    )
}

/// `POST /api/repos/budget {thread, add, confirm}` (§7.2, §12.1): raises the chat's daily token
/// budget by `add` (`1..=100,000,000`) until local midnight on the node.
async fn repo_budget(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let mreq = match prepare_mutation_request(&server, &headers, &body, "budget").await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    let Some(add) = mreq
        .request
        .get("add")
        .and_then(Value::as_u64)
        .filter(|&n| (1..=crate::repo_limits::MAX_BUDGET_RAISE).contains(&n))
    else {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "add must be between 1 and 100,000,000" }),
        );
    };
    if let Err(resp) = consume_mutation_nonce(&server, &mreq, "budget") {
        return resp;
    }
    let (used, limit, resets_at) = server
        .chat_repos
        .raise_budget(&mreq.record, mreq.settings.tokens_per_day, add);
    json_response(
        StatusCode::OK,
        json!({
            "used": used,
            "limit": limit,
            "resets_at": resets_at,
        }),
    )
}

fn record_airgapped_on(record: &CheckoutRecord, worktree_dir: &Path) -> bool {
    if crate::server::thread_airgapped_on(Some(&record.thread), Some(worktree_dir)) {
        return true;
    }
    record
        .app_threads
        .iter()
        .any(|tid| crate::server::thread_airgapped_on(Some(tid), Some(worktree_dir)))
}

/// The answer to a messenger bridge asking to change Full Access: only the page does (§6.3).
fn full_access_bridge_refusal() -> Response {
    json_response(
        StatusCode::FORBIDDEN,
        json!({ "error": "Full Access is changed only in the page, never through a messenger bridge" }),
    )
}

/// `POST /api/repos/access {thread, level, typed?, confirm?}` (§6.3, §4.7): grants or ends in-memory
/// Full Access for a phone repository checkout. Never from a messenger bridge (a `bridge=1`
/// session or a Signal bridge device).
/// - `level: "full"` needs the node's `[chat_repos] full_access` on, a writable checkout, its air
///   gap off, `typed` exactly `FULL ACCESS <org>/<repo>` and a single-use `access` nonce, which is
///   spent only once every other check passed.
/// - `level: "workspace"` needs no nonce and no forge: ending Full Access must work when anything
///   else is down. The checkout's threads are taken back to their sandbox at once
///   (`reset_checkout_sandbox`), not only at their next turn.
async fn repo_access(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    if server.is_bridge_caller(&headers) {
        return full_access_bridge_refusal();
    }
    let level = serde_json::from_slice::<Value>(&body)
        .ok()
        .and_then(|request| request.get("level").and_then(Value::as_str).map(str::to_string))
        .unwrap_or_default();
    match level.as_str() {
        "workspace" => {
            if let Err(resp) = require_control_caller_with_session(&server, &headers) {
                return resp;
            }
            let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
            let thread = request.get("thread").and_then(Value::as_str).unwrap_or("").trim();
            let (record, worktree_dir) = match find_checked_out_thread(&server, thread) {
                Ok(found) => found,
                Err(resp) => return resp,
            };
            server.chat_repos.revoke_full_access(&record.thread);
            reset_checkout_sandbox(&server, &record, &worktree_dir).await;
            json_response(StatusCode::OK, json!({ "level": "workspace" }))
        }
        "full" => {
            let mreq = match prepare_mutation_request(&server, &headers, &body, "access").await {
                Ok(v) => v,
                Err(resp) => return resp,
            };
            if !mreq.settings.full_access {
                server.chat_repos.clear_full_access();
                return json_response(
                    StatusCode::FORBIDDEN,
                    json!({
                        "error": "Full Access is off for phone threads; turn it on at the node with `ling chat repos full-access on`"
                    }),
                );
            }
            if mreq.record.read_only {
                return read_only_response();
            }
            if record_airgapped_on(&mreq.record, &mreq.worktree_dir) {
                return json_response(
                    StatusCode::FORBIDDEN,
                    json!({
                        "error": "Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible"
                    }),
                );
            }
            let expected = format!("FULL ACCESS {}", mreq.record.repo);
            let typed = mreq.request.get("typed").and_then(Value::as_str).unwrap_or("");
            if typed != expected {
                return json_response(
                    StatusCode::BAD_REQUEST,
                    json!({ "error": format!("type {expected} exactly") }),
                );
            }
            if let Err(resp) = consume_mutation_nonce(&server, &mreq, "access") {
                return resp;
            }
            server.chat_repos.grant_full_access(&mreq.record.thread);
            json_response(StatusCode::OK, json!({ "level": "full" }))
        }
        _ => json_response(
            StatusCode::BAD_REQUEST,
            json!({ "error": "level must be workspace or full" }),
        ),
    }
}

/// How long ending Full Access waits for each answer of the app-server.
const RESET_ANSWER_TIMEOUT: Duration = Duration::from_secs(5);

/// Takes a checkout's app-server threads back to their sandbox now that its Full Access ended.
/// A turn's `sandboxPolicy` holds for the turns after it, so without this a thread would keep
/// running with no sandbox until its next `turn/start`, through a review, a queued message or a
/// goal's own turns. Over a connection of its own: `thread/settings/update` with the sandbox the
/// policy writes on a repository turn, and `turn/interrupt` for a turn still running. With no
/// app-server listening no thread is loaded, and the next resume pins the sandbox anyway.
async fn reset_checkout_sandbox(server: &Server, record: &CheckoutRecord, worktree_dir: &Path) {
    use futures::SinkExt;
    use tokio_tungstenite::tungstenite::Message;

    if record.app_threads.is_empty() {
        return;
    }
    let Ok(mut upstream) = server.app_server.connect_once().await else {
        return;
    };
    let mut next_id: u64 = 0;
    let initialize = json!({
        "clientInfo": { "name": "mightling-web-reset", "title": null, "version": env!("CARGO_PKG_VERSION") },
        "capabilities": { "experimentalApi": true },
    });
    if reset_ask(&mut upstream, &mut next_id, "initialize", initialize).await.is_none() {
        return;
    }
    let _ = upstream
        .send(Message::Text(json!({ "method": "initialized" }).to_string().into()))
        .await;
    for thread in &record.app_threads {
        let sandbox = repo_turn_sandbox(server, record, worktree_dir, thread);
        reset_ask(&mut upstream, &mut next_id, "thread/settings/update", json!({ "threadId": thread, "sandboxPolicy": sandbox })).await;
        let turns = reset_ask(&mut upstream, &mut next_id, "thread/turns/list", json!({ "threadId": thread, "limit": 1, "sortDirection": "desc" })).await;
        let running = turns
            .as_ref()
            .and_then(|answer| answer.pointer("/result/data/0"))
            .filter(|turn| turn.get("status").and_then(Value::as_str) == Some("inProgress"))
            .and_then(|turn| turn.get("id").and_then(Value::as_str))
            .map(str::to_string);
        if let Some(turn) = running {
            reset_ask(&mut upstream, &mut next_id, "turn/interrupt", json!({ "threadId": thread, "turnId": turn })).await;
        }
    }
    let _ = upstream.close(None).await;
}

/// One request of `reset_checkout_sandbox` and its answer, skipping the notifications between.
async fn reset_ask(upstream: &mut crate::app_server::Upstream, next_id: &mut u64, method: &str, params: Value) -> Option<Value> {
    use futures::SinkExt;
    use futures::StreamExt;
    use tokio_tungstenite::tungstenite::Message;

    *next_id += 1;
    let id = format!("ling-web-reset-{next_id}");
    let message = json!({ "id": id, "method": method, "params": params });
    upstream.send(Message::Text(message.to_string().into())).await.ok()?;
    tokio::time::timeout(RESET_ANSWER_TIMEOUT, async {
        while let Some(Ok(frame)) = upstream.next().await {
            let Message::Text(text) = frame else { continue };
            let Ok(value) = serde_json::from_str::<Value>(text.as_str()) else { continue };
            if value.get("id").and_then(Value::as_str) == Some(id.as_str()) && value.get("method").is_none() {
                return Some(value);
            }
        }
        None
    })
    .await
    .ok()
    .flatten()
}

/// The sandbox the policy writes on the repository thread `thread`'s turn without Full Access
/// (`ask::MessagePrompts::workspace_sandbox`): the user's `[sandbox_workspace_write]`, with no
/// network while the thread's air gap is on (its own, the user's or the checkout's), read-only in a
/// read-only checkout. The same answer as the thread's next turn gets, so the two never disagree.
fn repo_turn_sandbox(server: &Server, record: &CheckoutRecord, worktree_dir: &Path, thread: &str) -> Value {
    let mut sandbox = crate::ask::configured_workspace_sandbox(&server.config.codex_home);
    if crate::server::thread_airgapped_on(Some(thread), Some(worktree_dir)) {
        sandbox.network_access = false;
    }
    sandbox.policy(record.read_only)
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
        now_secs: AtomicU64,
        now_ms: AtomicU64,
        git_timeout_ms: AtomicU64,
        utc_offset: Mutex<Option<i64>>,
    }

    impl StubProbe {
        fn new(free: u64, night: bool) -> Arc<Self> {
            Arc::new(Self {
                free: AtomicU64::new(free),
                night: AtomicBool::new(night),
                now_secs: AtomicU64::new(0),
                now_ms: AtomicU64::new(0),
                git_timeout_ms: AtomicU64::new(0),
                utc_offset: Mutex::new(None),
            })
        }

        fn set_now_secs(&self, secs: u64) {
            self.now_secs.store(secs, Ordering::Relaxed);
        }

        fn set_now_ms(&self, ms: u64) {
            self.now_ms.store(ms, Ordering::Relaxed);
        }

        fn set_git_timeout_ms(&self, ms: u64) {
            self.git_timeout_ms.store(ms, Ordering::Relaxed);
        }

        fn set_utc_offset(&self, offset: Option<i64>) {
            *self.utc_offset.lock().unwrap_or_else(|p| p.into_inner()) = offset;
        }
    }

    impl HostProbe for StubProbe {
        fn free_bytes(&self, _path: &Path) -> Result<u64, String> {
            Ok(self.free.load(Ordering::Relaxed))
        }

        fn swe_night_running(&self) -> bool {
            self.night.load(Ordering::Relaxed)
        }

        fn now_secs(&self) -> u64 {
            let s = self.now_secs.load(Ordering::Relaxed);
            if s > 0 {
                s
            } else {
                now_secs()
            }
        }

        fn now_ms(&self) -> u64 {
            let m = self.now_ms.load(Ordering::Relaxed);
            if m > 0 {
                m
            } else {
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_millis() as u64)
                    .unwrap_or(0)
            }
        }

        fn utc_offset_seconds(&self) -> Option<i64> {
            *self.utc_offset.lock().unwrap_or_else(|p| p.into_inner())
        }

        fn git_timeout(&self, subcommand: &str) -> Duration {
            let ms = self.git_timeout_ms.load(Ordering::Relaxed);
            if ms > 0 {
                Duration::from_millis(ms)
            } else {
                match subcommand {
                    "clone" | "fetch" | "push" => GIT_NET_TIMEOUT,
                    _ => GIT_LOCAL_TIMEOUT,
                }
            }
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

    #[derive(Clone, Debug)]
    struct FakePull {
        number: u64,
        repo: String,
        head_ref: String,
        base_ref: String,
        title: String,
        mergeable: bool,
        merged: bool,
    }

    struct FakeForge {
        repos: Value,
        pulls: Mutex<Vec<FakePull>>,
        statuses: Mutex<HashMap<String, (String, u64)>>,
        default_mergeable: AtomicBool,
        requests: Mutex<Vec<(String, String, Value)>>,
    }

    impl FakeForge {
        fn new(repos: Value) -> Arc<Self> {
            Arc::new(Self {
                repos,
                pulls: Mutex::new(Vec::new()),
                statuses: Mutex::new(HashMap::new()),
                default_mergeable: AtomicBool::new(true),
                requests: Mutex::new(Vec::new()),
            })
        }

        fn record(&self, method: &str, path: &str, body: Value) {
            self.requests
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .push((method.to_string(), path.to_string(), body));
        }

        fn set_status(&self, sha: &str, state: &str, total_count: u64) {
            self.statuses
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .insert(sha.to_string(), (state.to_string(), total_count));
        }

        fn set_mergeable(&self, number: u64, mergeable: bool) {
            let mut pulls = self.pulls.lock().unwrap_or_else(|p| p.into_inner());
            if let Some(p) = pulls.iter_mut().find(|p| p.number == number) {
                p.mergeable = mergeable;
            }
        }
    }

    fn check_fake_auth(headers: &HeaderMap) -> bool {
        let auth_hdr = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()).unwrap_or("");
        auth_hdr == format!("token {TEST_TOKEN}")
    }

    /// Starts an in-process Forgejo API stand-in on `127.0.0.1:0` serving version, search, pulls,
    /// commit status, and pull merge, while recording every request.
    async fn start_fake_forge_with_state(repos_json: Value) -> (String, Arc<FakeForge>, tokio::task::JoinHandle<()>) {
        let state = FakeForge::new(repos_json);
        let s_ver = state.clone();
        let s_search = state.clone();
        let s_pulls_list = state.clone();
        let s_pulls_create = state.clone();
        let s_pull_get = state.clone();
        let s_status_get = state.clone();
        let s_pull_merge = state.clone();

        let app = Router::new()
            .route(
                "/api/v1/version",
                get(move |headers: HeaderMap| {
                    let st = s_ver.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        st.record("GET", "/api/v1/version", Value::Null);
                        (StatusCode::OK, [("content-type", "application/json")], "{\"version\":\"1.22.0\"}").into_response()
                    }
                }),
            )
            .route(
                "/api/v1/repos/search",
                get(move |headers: HeaderMap, Query(q): Query<HashMap<String, String>>| {
                    let st = s_search.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let page = q.get("page").and_then(|p| p.parse::<usize>().ok()).unwrap_or(1);
                        st.record("GET", &format!("/api/v1/repos/search?page={page}"), Value::Null);
                        let body = if page == 1 {
                            json!({ "ok": true, "data": st.repos })
                        } else {
                            json!({ "ok": true, "data": [] })
                        };
                        (StatusCode::OK, [("content-type", "application/json")], body.to_string()).into_response()
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls",
                get(move |headers: HeaderMap, axum::extract::Path((owner, repo)): axum::extract::Path<(String, String)>| {
                    let st = s_pulls_list.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let full = format!("{owner}/{repo}");
                        st.record("GET", &format!("/api/v1/repos/{full}/pulls"), Value::Null);
                        let pulls = st.pulls.lock().unwrap_or_else(|p| p.into_inner());
                        let list: Vec<Value> = pulls
                            .iter()
                            .filter(|p| p.repo == full && !p.merged)
                            .map(|p| {
                                json!({
                                    "number": p.number,
                                    "mergeable": p.mergeable,
                                    "merged": p.merged,
                                    "html_url": format!("http://127.0.0.1:3200/{full}/pulls/{}", p.number),
                                    "title": p.title,
                                    "head": { "ref": p.head_ref },
                                    "base": { "ref": p.base_ref },
                                })
                            })
                            .collect();
                        (StatusCode::OK, [("content-type", "application/json")], Value::Array(list).to_string()).into_response()
                    }
                })
                .post(move |headers: HeaderMap, axum::extract::Path((owner, repo)): axum::extract::Path<(String, String)>, body: Bytes| {
                    let st = s_pulls_create.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let full = format!("{owner}/{repo}");
                        let payload: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                        st.record("POST", &format!("/api/v1/repos/{full}/pulls"), payload.clone());
                        let mut pulls = st.pulls.lock().unwrap_or_else(|p| p.into_inner());
                        let number = (pulls.len() as u64) + 1;
                        let head_ref = payload.get("head").and_then(Value::as_str).unwrap_or("").to_string();
                        let base_ref = payload.get("base").and_then(Value::as_str).unwrap_or("main").to_string();
                        let title = payload.get("title").and_then(Value::as_str).unwrap_or("").to_string();
                        let mergeable = st.default_mergeable.load(Ordering::Relaxed);
                        let pull = FakePull {
                            number,
                            repo: full.clone(),
                            head_ref: head_ref.clone(),
                            base_ref: base_ref.clone(),
                            title: title.clone(),
                            mergeable,
                            merged: false,
                        };
                        pulls.push(pull);
                        let resp = json!({
                            "number": number,
                            "mergeable": mergeable,
                            "merged": false,
                            "html_url": format!("http://127.0.0.1:3200/{full}/pulls/{number}"),
                            "title": title,
                            "head": { "ref": head_ref },
                            "base": { "ref": base_ref },
                        });
                        (StatusCode::CREATED, [("content-type", "application/json")], resp.to_string()).into_response()
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls/{index}",
                get(move |headers: HeaderMap, axum::extract::Path((owner, repo, index)): axum::extract::Path<(String, String, u64)>| {
                    let st = s_pull_get.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let full = format!("{owner}/{repo}");
                        st.record("GET", &format!("/api/v1/repos/{full}/pulls/{index}"), Value::Null);
                        let pulls = st.pulls.lock().unwrap_or_else(|p| p.into_inner());
                        let Some(p) = pulls.iter().find(|p| p.repo == full && p.number == index) else {
                            return (StatusCode::NOT_FOUND, "not found").into_response();
                        };
                        let resp = json!({
                            "number": p.number,
                            "mergeable": p.mergeable,
                            "merged": p.merged,
                            "html_url": format!("http://127.0.0.1:3200/{full}/pulls/{}", p.number),
                            "title": p.title,
                            "head": { "ref": p.head_ref },
                            "base": { "ref": p.base_ref },
                        });
                        (StatusCode::OK, [("content-type", "application/json")], resp.to_string()).into_response()
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/commits/{sha}/status",
                get(move |headers: HeaderMap, axum::extract::Path((owner, repo, sha)): axum::extract::Path<(String, String, String)>| {
                    let st = s_status_get.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let full = format!("{owner}/{repo}");
                        st.record("GET", &format!("/api/v1/repos/{full}/commits/{sha}/status"), Value::Null);
                        let statuses = st.statuses.lock().unwrap_or_else(|p| p.into_inner());
                        let (state_str, total_count) = statuses
                            .get(&sha)
                            .or_else(|| statuses.get("default"))
                            .cloned()
                            .unwrap_or_else(|| ("success".to_string(), 0));
                        let resp = json!({
                            "state": state_str,
                            "total_count": total_count,
                        });
                        (StatusCode::OK, [("content-type", "application/json")], resp.to_string()).into_response()
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls/{index}/merge",
                post(move |headers: HeaderMap, axum::extract::Path((owner, repo, index)): axum::extract::Path<(String, String, u64)>, body: Bytes| {
                    let st = s_pull_merge.clone();
                    async move {
                        if !check_fake_auth(&headers) {
                            return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
                        }
                        let full = format!("{owner}/{repo}");
                        let payload: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                        st.record("POST", &format!("/api/v1/repos/{full}/pulls/{index}/merge"), payload.clone());
                        if payload.get("Do").and_then(Value::as_str) != Some("merge") {
                            return (StatusCode::BAD_REQUEST, "expected Do=merge").into_response();
                        }
                        let mut pulls = st.pulls.lock().unwrap_or_else(|p| p.into_inner());
                        let Some(p) = pulls.iter_mut().find(|p| p.repo == full && p.number == index) else {
                            return (StatusCode::NOT_FOUND, "not found").into_response();
                        };
                        if !p.mergeable {
                            return (StatusCode::METHOD_NOT_ALLOWED, "not mergeable").into_response();
                        }
                        p.merged = true;
                        (StatusCode::OK, [("content-type", "application/json")], "{\"sha\":\"merged\"}").into_response()
                    }
                }),
            );

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        (format!("http://127.0.0.1:{}", addr.port()), state, handle)
    }

    async fn start_fake_forge(repos_json: Value) -> (String, tokio::task::JoinHandle<()>) {
        let (url, _state, handle) = start_fake_forge_with_state(repos_json).await;
        (url, handle)
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

    /// A checkout whose `.git/config` and `.gitattributes` name a program for every hook that
    /// executes during a read (the agent can write both). No program may run.
    #[cfg(unix)]
    #[tokio::test]
    async fn a_hostile_checkout_config_runs_no_program_when_the_server_reads_git() {
        use std::os::unix::fs::PermissionsExt;
        let s = scratch("hostile");
        let (server, _probe) = test_server(&s.dir, None, true);
        let repo = s.dir.join("checkout");
        std::fs::create_dir_all(&repo).unwrap();
        git_sync(&repo, &["init", "--initial-branch=main"]);
        git_sync(&repo, &["config", "user.email", "dev@127.0.0.1"]);
        git_sync(&repo, &["config", "user.name", "dev"]);
        std::fs::write(repo.join("a.txt"), "one\n").unwrap();
        std::fs::write(repo.join(".gitattributes"), "*.txt filter=evil diff=evil\n").unwrap();
        git_sync(&repo, &["add", "-A"]);
        git_sync(&repo, &["commit", "-m", "init"]);

        let marker = s.dir.join("ran");
        let tool = s.dir.join("tool.sh");
        std::fs::write(&tool, format!("#!/bin/sh\necho ran >> {}\ncat\n", marker.display())).unwrap();
        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).unwrap();
        let t = tool.display();
        let config = std::fs::read_to_string(repo.join(".git/config")).unwrap();
        std::fs::write(
            repo.join(".git/config"),
            format!(
                "{config}[core]\n\tfsmonitor = {t}\n\tpager = {t}\n\tsshCommand = {t}\n[diff]\n\texternal = {t}\n\
                 [diff \"evil\"]\n\ttextconv = {t}\n[filter \"evil\"]\n\tclean = {t}\n\tsmudge = {t}\n\
                 [credential]\n\thelper = {t}\n"
            ),
        )
        .unwrap();
        std::fs::write(repo.join("a.txt"), "one\ntwo, changed\n").unwrap();

        for args in [
            vec!["status", "--porcelain=v1"],
            vec!["diff", "HEAD"],
            vec!["diff", "--cached"],
            vec!["show", "HEAD"],
            vec!["log", "-p", "-1"],
            vec!["add", "-A"],
        ] {
            run_git_checked(&server, Some(&repo), &args, false).await.unwrap_or_else(|e| panic!("git {args:?}: {e}"));
        }
        let ran = std::fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a program named by the checkout's config ran: {ran}");
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

        let (server, _probe) = test_server(&s.dir, Some(forge_url.clone()), true);
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

        // Write executable hooks into the node's own `chat-repos/org/widget/base.git/hooks/`
        // (the hooks directory git would consult without core.hooksPath) before opening a second
        // thread and committing through `/api/repos/commit`.
        let base_git = root_dir(&server.config.codex_home).join("org/widget/base.git");
        let base_hooks_dir = base_git.join("hooks");
        std::fs::create_dir_all(&base_hooks_dir).unwrap();
        for hook_name in ["post-checkout", "pre-commit", "post-commit"] {
            let hp = base_hooks_dir.join(hook_name);
            std::fs::write(&hp, format!("#!/bin/sh\necho ran > {}\n", marker.display())).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&hp, std::fs::Permissions::from_mode(0o755)).unwrap();
            }
        }

        // 3. Opening for a second chat reuses base.git and creates a second worktree (`git worktree add`).
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
        assert!(!marker.exists(), "base.git post-checkout hook must not run on worktree add");

        // Also run a commit through POST /api/repos/commit and verify hooks in base.git/hooks never run.
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let wt1_path = PathBuf::from(opened["cwd"].as_str().unwrap());
        std::fs::write(wt1_path.join("hook-check.txt"), "hello\n").unwrap();
        let state_nonce_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/state?thread={thread1}&intent=commit"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie)
            .body(Body::empty())
            .unwrap();
        let (st_code, st_body) = call(&server, state_nonce_req).await;
        assert_eq!(st_code, StatusCode::OK, "{st_body}");
        let st_json: Value = serde_json::from_str(&st_body).unwrap();
        let commit_nonce = st_json["confirm"].as_str().unwrap();
        let commit_req = Request::builder()
            .method("POST")
            .uri("/api/repos/commit")
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, &cookie)
            .body(Body::from(
                json!({ "thread": thread1, "message": "hook test commit", "confirm": commit_nonce }).to_string(),
            ))
            .unwrap();
        let (c_code, c_body) = call(&server, commit_req).await;
        assert_eq!(c_code, StatusCode::OK, "{c_body}");
        assert!(!marker.exists(), "base.git hooks must not run during commit");

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
            .arg(format!("Password for '{forge_url}': "))
            .output()
            .unwrap();
        assert!(askpass_out.status.success());
        assert_eq!(String::from_utf8_lossy(&askpass_out.stdout).trim(), TEST_TOKEN);
        let askpass_bad = std::process::Command::new(&askpass)
            .arg("Password for 'http://127.0.0.1:9999': ")
            .output()
            .unwrap();
        assert!(!askpass_bad.status.success());
        assert!(askpass_bad.stdout.is_empty());

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

        let (server, _probe) = test_server(&s.dir, Some(forge_url.clone()), true);
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

    async fn get_nonce(server: &Arc<Server>, cookie: &str, thread: &str, intent: &str) -> String {
        let req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/state?thread={thread}&intent={intent}"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, cookie)
            .body(Body::empty())
            .unwrap();
        let (status, body) = call(server, req).await;
        assert_eq!(status, StatusCode::OK, "failed to get nonce for {intent}: {body}");
        let v: Value = serde_json::from_str(&body).unwrap();
        let nonce = v["confirm"].as_str().unwrap().to_string();
        assert_eq!(nonce.len(), 32);
        assert!(nonce.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
        nonce
    }

    async fn post_json(server: &Arc<Server>, cookie: &str, uri: &str, payload: Value) -> (StatusCode, Value, String) {
        let req = Request::builder()
            .method("POST")
            .uri(uri)
            .header(header::HOST, HOST)
            .header(header::ORIGIN, ORIGIN)
            .header(header::COOKIE, cookie)
            .body(Body::from(payload.to_string()))
            .unwrap();
        let (status, body) = call(server, req).await;
        let parsed = serde_json::from_str::<Value>(&body).unwrap_or(Value::Null);
        (status, parsed, body)
    }

    async fn get_json(server: &Arc<Server>, cookie: &str, uri: &str) -> (StatusCode, Value, String) {
        let req = Request::builder()
            .method("GET")
            .uri(uri)
            .header(header::HOST, HOST)
            .header(header::COOKIE, cookie)
            .body(Body::empty())
            .unwrap();
        let (status, body) = call(server, req).await;
        let parsed = serde_json::from_str::<Value>(&body).unwrap_or(Value::Null);
        (status, parsed, body)
    }

    fn git_stdout(cwd: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git")
            .env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("HOME", cwd)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .args(["-c", "protocol.file.allow=always"])
            .current_dir(cwd)
            .args(args)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {args:?} failed in {}: {}",
            cwd.display(),
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8_lossy(&out.stdout).into_owned()
    }

    #[tokio::test]
    async fn confirmation_nonces_enforce_presence_thread_intent_caller_and_single_use() {
        let s = scratch("nonces");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge, _forge_task) = start_fake_forge_with_state(json!([
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
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie1 = owner_cookie(&server).await;
        let (_dev_id, dev_token) = auth::add_device(&server.config.state_dir, "phone").unwrap();
        let cookie2 = format!("{}={dev_token}", auth::DEVICE_COOKIE);

        let (_, open1, _) = post_json(
            &server,
            &cookie1,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread1 = open1["thread"].as_str().unwrap().to_string();
        let wt1 = PathBuf::from(open1["cwd"].as_str().unwrap());

        let (_, open2, _) = post_json(
            &server,
            &cookie1,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c2" }),
        )
        .await;
        let thread2 = open2["thread"].as_str().unwrap().to_string();

        // Unknown intent in GET /api/repos/state is refused with 400.
        let bad_intent_req = Request::builder()
            .method("GET")
            .uri(format!("/api/repos/state?thread={thread1}&intent=bogus"))
            .header(header::HOST, HOST)
            .header(header::COOKIE, &cookie1)
            .body(Body::empty())
            .unwrap();
        let (st, _) = call(&server, bad_intent_req).await;
        assert_eq!(st, StatusCode::BAD_REQUEST);

        for (uri, intent, other_intent) in [
            ("/api/repos/commit", "commit", "push"),
            ("/api/repos/push", "push", "commit"),
            ("/api/repos/update", "update", "push"),
            ("/api/repos/merge", "merge", "pr"),
            ("/api/repos/pr", "pr", "merge"),
            ("/api/repos/main", "main", "push"),
        ] {
            // 1. Without a nonce
            let (status, _, body) = post_json(
                &server,
                &cookie1,
                uri,
                json!({ "thread": thread1, "message": "msg" }),
            )
            .await;
            assert_eq!(status, StatusCode::CONFLICT, "missing nonce on {uri}: {body}");
            assert!(body.contains("confirmation missing or expired"), "{body}");

            // 2. With another thread's nonce
            let nonce_other_thread = get_nonce(&server, &cookie1, &thread2, intent).await;
            let (status, _, body) = post_json(
                &server,
                &cookie1,
                uri,
                json!({ "thread": thread1, "message": "msg", "confirm": nonce_other_thread }),
            )
            .await;
            assert_eq!(status, StatusCode::CONFLICT, "other thread nonce on {uri}: {body}");
            assert!(body.contains("confirmation missing or expired"), "{body}");

            // 3. With another intent's nonce
            let nonce_other_intent = get_nonce(&server, &cookie1, &thread1, other_intent).await;
            let (status, _, body) = post_json(
                &server,
                &cookie1,
                uri,
                json!({ "thread": thread1, "message": "msg", "confirm": nonce_other_intent }),
            )
            .await;
            assert_eq!(status, StatusCode::CONFLICT, "other intent nonce on {uri}: {body}");
            assert!(body.contains("confirmation missing or expired"), "{body}");

            // 4. With another caller's nonce
            let nonce_other_caller = get_nonce(&server, &cookie2, &thread1, intent).await;
            let (status, _, body) = post_json(
                &server,
                &cookie1,
                uri,
                json!({ "thread": thread1, "message": "msg", "confirm": nonce_other_caller }),
            )
            .await;
            assert_eq!(status, StatusCode::CONFLICT, "other caller nonce on {uri}: {body}");
            assert!(body.contains("confirmation missing or expired"), "{body}");
        }

        // 5. A used nonce is single-use and refused on replay for every route.
        std::fs::write(wt1.join("file.txt"), "hello\n").unwrap();
        let commit_nonce = get_nonce(&server, &cookie1, &thread1, "commit").await;
        let (status, _, body) = post_json(
            &server,
            &cookie1,
            "/api/repos/commit",
            json!({ "thread": thread1, "message": "first commit", "confirm": commit_nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "{body}");

        // Replay the same commit_nonce: must be 409 "confirmation missing or expired".
        std::fs::write(wt1.join("file.txt"), "hello again\n").unwrap();
        let (status, _, body) = post_json(
            &server,
            &cookie1,
            "/api/repos/commit",
            json!({ "thread": thread1, "message": "replay commit", "confirm": commit_nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");
        assert!(body.contains("confirmation missing or expired"), "{body}");
    }

    #[tokio::test]
    async fn commit_uses_owner_identity_without_trailers_and_refuses_missing_identity_or_empty() {
        let s = scratch("commit");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge, _forge_task) = start_fake_forge_with_state(json!([
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

        let (server, _probe) = test_server(&s.dir, Some(forge_url.clone()), true);
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        std::fs::write(wt.join("new.txt"), "alpha\nbeta\n").unwrap();

        // 1. Refused when ~/.gitconfig has no user.name / user.email.
        let nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (status, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "try commit", "confirm": nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");
        assert!(body.contains("user.name"), "{body}");

        // 2. Write owner identity to scratch home's .gitconfig.
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = dgxcoder\n\temail = dgxcoder@127.0.0.1\n",
        )
        .unwrap();

        // Empty message and > 5000 char message are refused with 400.
        let nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (status, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "   ", "confirm": nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);

        let nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let long_msg = "x".repeat(5001);
        let (status, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": long_msg, "confirm": nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);

        // 3. Valid commit stages tracked and untracked files, uses .gitconfig identity, and adds no trailer.
        std::fs::write(wt.join("README.md"), "# Demo\nupdated line\n").unwrap();
        let nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (status, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "clean change", "confirm": nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "{body}");
        assert_eq!(res["commit"].as_str().unwrap().len(), 40);
        assert_eq!(res["added"], 3);
        assert_eq!(res["removed"], 1);
        let files = res["files"].as_array().unwrap();
        assert_eq!(files.len(), 2);

        let log_body = git_stdout(&wt, &["log", "-1", "--format=%B"]);
        assert_eq!(log_body.trim(), "clean change");
        assert!(!log_body.to_ascii_lowercase().contains("co-authored"));
        assert!(!log_body.to_ascii_lowercase().contains("signed-off"));
        let log_id = git_stdout(&wt, &["log", "-1", "--format=%an <%ae>|%cn <%ce>"]);
        assert_eq!(log_id.trim(), "dgxcoder <dgxcoder@127.0.0.1>|dgxcoder <dgxcoder@127.0.0.1>");

        // 4. Nothing to commit -> 409 "nothing to commit".
        let nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (status, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "empty commit", "confirm": nonce }),
        )
        .await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");
        assert!(body.contains("nothing to commit"), "{body}");
    }

    #[tokio::test]
    async fn push_sends_only_phone_branch_never_forces_and_checks_anonymity_and_readonly() {
        let s = scratch("push-anon");
        let pub_bare = make_bare_repo(&s.dir, "org/pubrepo");
        let priv_bare = make_bare_repo(&s.dir, "org/privrepo");
        let arch_bare = make_bare_repo(&s.dir, "org/archrepo");

        // Create a second branch `keep-me` on pub_bare to verify push touches only phone/<thread>.
        let seed_pub = s.dir.join("seed-work").join("org_pubrepo");
        git_sync(&seed_pub, &["checkout", "-b", "keep-me"]);
        std::fs::write(seed_pub.join("keep.txt"), "untouched\n").unwrap();
        git_sync(&seed_pub, &["add", "keep.txt"]);
        git_sync(&seed_pub, &["commit", "-m", "keep branch"]);
        git_sync(&seed_pub, &["push", "origin", "keep-me"]);
        git_sync(&seed_pub, &["checkout", "main"]);
        let before_refs = git_stdout(&pub_bare, &["show-ref"]);

        let (forge_url, _forge, _forge_task) = start_fake_forge_with_state(json!([
            {
                "full_name": "org/pubrepo",
                "default_branch": "main",
                "private": false,
                "archived": false,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": true },
                "clone_url": format!("file://{}", pub_bare.display())
            },
            {
                "full_name": "org/privrepo",
                "default_branch": "main",
                "private": true,
                "archived": false,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": true },
                "clone_url": format!("file://{}", priv_bare.display())
            },
            {
                "full_name": "org/archrepo",
                "default_branch": "main",
                "private": false,
                "archived": true,
                "mirror": false,
                "updated_at": "2026-10-20T12:00:00Z",
                "permissions": { "push": false },
                "clone_url": format!("file://{}", arch_bare.display())
            }
        ]))
        .await;

        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        // 1. Open pubrepo and push with no denylist file on node -> passes with "no denylist on this node".
        let (_, open_pub, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/pubrepo", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let pub_thread = open_pub["thread"].as_str().unwrap().to_string();
        let pub_wt = PathBuf::from(open_pub["cwd"].as_str().unwrap());

        std::fs::write(pub_wt.join("clean.txt"), "safe line\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &pub_thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": pub_thread, "message": "safe commit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &pub_thread, "push").await;
        let (st, push_res, push_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": pub_thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{push_body}");
        assert_eq!(push_res["pushed"], 1);
        assert_eq!(push_res["branch"], format!("phone/{pub_thread}"));
        assert_eq!(push_res["anonymity_check"], "no denylist on this node");

        // Inspect bare origin's refs: only refs/heads/phone/<pub_thread> was added; main and keep-me are unchanged.
        let after_refs = git_stdout(&pub_bare, &["show-ref"]);
        for line in before_refs.lines() {
            assert!(after_refs.lines().any(|l| l == line), "existing ref changed: {line}");
        }
        let added_refs: Vec<&str> = after_refs
            .lines()
            .filter(|l| !before_refs.lines().any(|b| b == *l))
            .collect();
        assert_eq!(added_refs.len(), 1);
        assert!(added_refs[0].ends_with(&format!("refs/heads/phone/{pub_thread}")));

        // 2. Write denylist file; a denylisted string in a pushed file is refused with 422 {file, line, entry}
        // and the response body does NOT contain the matched string or denylist entry text.
        std::fs::write(
            s.dir.join("user/.config/dreamference/anonymity-denylist.txt"),
            "# denylist comment\n\nbox-sentinel-alpha\n\\w*leakedword\\w*\n",
        )
        .unwrap();

        std::fs::write(pub_wt.join("src_file.txt"), "line one\nreach BOX-SENTINEL-ALPHA here\nline three\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &pub_thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": pub_thread, "message": "touch file", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &pub_thread, "push").await;
        let (st, anon_res, anon_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": pub_thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{anon_body}");
        assert_eq!(anon_res["refused"], "anonymity");
        assert!(!anon_body.to_ascii_lowercase().contains("box-sentinel-alpha"), "{anon_body}");
        assert!(!anon_body.to_ascii_lowercase().contains("leakedword"), "{anon_body}");
        let findings = anon_res["findings"].as_array().unwrap();
        assert!(
            findings.iter().any(|f| f["file"] == "src_file.txt" && f["line"] == 2 && f["entry"] == 1),
            "{anon_body}"
        );

        // 3. Reset that commit and test a denylisted string in a commit message.
        git_sync(&pub_wt, &["reset", "--hard", "HEAD~1"]);
        std::fs::write(pub_wt.join("clean2.txt"), "harmless text\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &pub_thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": pub_thread, "message": "fix my_leakedword_value in config", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &pub_thread, "push").await;
        let (st, msg_res, msg_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": pub_thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{msg_body}");
        assert_eq!(msg_res["refused"], "anonymity");
        assert!(!msg_body.to_ascii_lowercase().contains("leakedword"), "{msg_body}");
        let msg_findings = msg_res["findings"].as_array().unwrap();
        assert!(
            msg_findings.iter().any(|f| f["file"] == "commit message" && f["line"] == 0 && f["entry"] == 2),
            "{msg_body}"
        );

        // 4. Private repository skips the anonymity check.
        let (_, open_priv, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/privrepo", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let priv_thread = open_priv["thread"].as_str().unwrap().to_string();
        let priv_wt = PathBuf::from(open_priv["cwd"].as_str().unwrap());
        std::fs::write(priv_wt.join("private.txt"), "contains box-sentinel-alpha in private repo\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &priv_thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": priv_thread, "message": "private commit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &priv_thread, "push").await;
        let (st, priv_push_res, priv_push_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": priv_thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{priv_push_body}");
        assert_eq!(priv_push_res["pushed"], 1);

        // 5. Diverged remote branch on priv_bare refuses push rather than force-overwriting.
        let seed_priv = s.dir.join("seed-work").join("org_privrepo");
        git_sync(&seed_priv, &["fetch", "origin"]);
        git_sync(&seed_priv, &["checkout", "-B", &format!("phone/{priv_thread}"), "origin/main"]);
        std::fs::write(seed_priv.join("diverged.txt"), "remote diverged\n").unwrap();
        git_sync(&seed_priv, &["add", "diverged.txt"]);
        git_sync(&seed_priv, &["commit", "-m", "diverged remote commit"]);
        git_sync(&seed_priv, &["push", "--force", "origin", &format!("phone/{priv_thread}")]);
        let remote_sha_before = git_stdout(&priv_bare, &["rev-parse", &format!("refs/heads/phone/{priv_thread}")]);

        std::fs::write(priv_wt.join("local2.txt"), "local commit\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &priv_thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": priv_thread, "message": "local after diverge", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &priv_thread, "push").await;
        let (st, div_res, div_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": priv_thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_GATEWAY, "{div_body}");
        assert_eq!(div_res["refused"], "forge");
        assert!(
            div_res["reason"].as_str().unwrap().contains("the forge's branch moved; /update first"),
            "{div_body}"
        );
        let remote_sha_after = git_stdout(&priv_bare, &["rev-parse", &format!("refs/heads/phone/{priv_thread}")]);
        assert_eq!(remote_sha_before, remote_sha_after, "push must never overwrite diverged remote branch");

        // 6. Archived / read-only repository refuses push (and commit/update/pr/merge).
        let (_, open_arch, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/archrepo", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let arch_thread = open_arch["thread"].as_str().unwrap().to_string();
        for (uri, intent) in [
            ("/api/repos/commit", "commit"),
            ("/api/repos/push", "push"),
            ("/api/repos/update", "update"),
            ("/api/repos/pr", "pr"),
            ("/api/repos/merge", "merge"),
        ] {
            let nonce = get_nonce(&server, &cookie, &arch_thread, intent).await;
            let (st, _, body) = post_json(
                &server,
                &cookie,
                uri,
                json!({ "thread": arch_thread, "message": "nope", "confirm": nonce }),
            )
            .await;
            assert_eq!(st, StatusCode::FORBIDDEN, "expected 403 on {uri}: {body}");
            assert!(body.contains("this repository is read-only on the forge"), "{body}");
        }
    }

    #[tokio::test]
    async fn update_merges_default_reports_conflicts_and_aborts_cleanly() {
        let s = scratch("update");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge, _forge_task) = start_fake_forge_with_state(json!([
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
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        // Advance `main` on the forge bare repo with a conflicting edit to README.md.
        let seed_work = s.dir.join("seed-work").join("org_widget");
        std::fs::write(seed_work.join("README.md"), "# Demo\nmain branch change\n").unwrap();
        git_sync(&seed_work, &["add", "README.md"]);
        git_sync(&seed_work, &["commit", "-m", "main edit"]);
        git_sync(&seed_work, &["push", "origin", "main"]);

        // Make a conflicting commit on phone/<thread>.
        std::fs::write(wt.join("README.md"), "# Demo\nphone branch change\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "phone edit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        // 1. POST /api/repos/update leaves conflict markers in README.md and answers 409 {conflicts: ["README.md"]}.
        let u_nonce = get_nonce(&server, &cookie, &thread, "update").await;
        let (st, u_res, u_body) = post_json(
            &server,
            &cookie,
            "/api/repos/update",
            json!({ "thread": thread, "confirm": u_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{u_body}");
        assert_eq!(u_res["conflicts"], json!(["README.md"]));
        let readme_conflict = std::fs::read_to_string(wt.join("README.md")).unwrap();
        assert!(readme_conflict.contains("<<<<<<<"), "{readme_conflict}");
        let (_, mid_state, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(mid_state["merge_in_progress"], json!(true), "{mid_state}");
        assert_eq!(mid_state["allow_main"], json!(false), "{mid_state}");

        // 2. POST /api/repos/update with abort: true restores a clean working tree.
        let a_nonce = get_nonce(&server, &cookie, &thread, "update").await;
        let (st, a_res, a_body) = post_json(
            &server,
            &cookie,
            "/api/repos/update",
            json!({ "thread": thread, "confirm": a_nonce, "abort": true }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{a_body}");
        assert_eq!(a_res["aborted"], json!(true));
        let readme_restored = std::fs::read_to_string(wt.join("README.md")).unwrap();
        assert_eq!(readme_restored, "# Demo\nphone branch change\n");
        let status_porcelain = git_stdout(&wt, &["status", "--porcelain"]);
        assert!(status_porcelain.trim().is_empty(), "expected clean tree after abort: {status_porcelain}");
        let (_, after_state, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(after_state["merge_in_progress"], json!(false), "{after_state}");
    }

    #[tokio::test]
    async fn pr_and_merge_routes_drive_forge_api_and_respect_status_and_mergeable() {
        let s = scratch("pr-merge");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, forge, _forge_task) = start_fake_forge_with_state(json!([
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
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        std::fs::write(wt.join("feature.txt"), "new feature\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, c_res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "secret commit message", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);
        let head_sha = c_res["commit"].as_str().unwrap().to_string();

        // 1. POST /api/repos/pr pushes automatically and creates PR #1 with relative proxy URL.
        let pr_nonce = get_nonce(&server, &cookie, &thread, "pr").await;
        let (st, pr_res, pr_body) = post_json(
            &server,
            &cookie,
            "/api/repos/pr",
            json!({ "thread": thread, "confirm": pr_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{pr_body}");
        assert_eq!(pr_res["number"], 1);
        assert_eq!(pr_res["url"], "/forge/org/widget/pulls/1");

        // Verify PR title on the forge stand-in is "phone/<thread> from org/widget" and never commit text.
        {
            let pulls = forge.pulls.lock().unwrap();
            assert_eq!(pulls.len(), 1);
            assert_eq!(pulls[0].title, format!("phone/{thread} from org/widget"));
            assert!(!pulls[0].title.contains("secret commit message"));
        }

        // Calling /api/repos/pr again reuses the existing open PR without creating a second one.
        let pr_nonce2 = get_nonce(&server, &cookie, &thread, "pr").await;
        let (st, pr_res2, _) = post_json(
            &server,
            &cookie,
            "/api/repos/pr",
            json!({ "thread": thread, "confirm": pr_nonce2 }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);
        assert_eq!(pr_res2["number"], 1);
        assert_eq!(forge.pulls.lock().unwrap().len(), 1);

        // 2. Red commit status ("failure") refuses merge with 409 "a required check is red".
        forge.set_status(&head_sha, "failure", 1);
        let m_nonce = get_nonce(&server, &cookie, &thread, "merge").await;
        let (st, _, m_body) = post_json(
            &server,
            &cookie,
            "/api/repos/merge",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{m_body}");
        assert!(m_body.contains("a required check is red"), "{m_body}");

        // 3. Pending commit status with total_count > 0 refuses merge with 409 "checks are still running".
        forge.set_status(&head_sha, "pending", 2);
        let m_nonce = get_nonce(&server, &cookie, &thread, "merge").await;
        let (st, _, m_body) = post_json(
            &server,
            &cookie,
            "/api/repos/merge",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{m_body}");
        assert!(m_body.contains("checks are still running"), "{m_body}");

        // 4. Green status but mergeable = false refuses merge with 409 "conflicts with main: /update, then /merge".
        forge.set_status(&head_sha, "success", 1);
        forge.set_mergeable(1, false);
        let m_nonce = get_nonce(&server, &cookie, &thread, "merge").await;
        let (st, _, m_body) = post_json(
            &server,
            &cookie,
            "/api/repos/merge",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{m_body}");
        assert!(m_body.contains("conflicts with main: /update, then /merge"), "{m_body}");

        // 5. Green status and mergeable = true merges with {"Do": "merge"} and answers {merged: true, branch, into}.
        forge.set_mergeable(1, true);
        let m_nonce = get_nonce(&server, &cookie, &thread, "merge").await;
        let (st, m_res, m_body) = post_json(
            &server,
            &cookie,
            "/api/repos/merge",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{m_body}");
        assert_eq!(m_res["merged"], true);
        assert_eq!(m_res["branch"], format!("phone/{thread}"));
        assert_eq!(m_res["into"], "main");

        let recorded = forge.requests.lock().unwrap();
        assert!(recorded.iter().any(|(method, path, body)| {
            method == "POST" && path == "/api/v1/repos/org/widget/pulls/1/merge" && body["Do"] == "merge"
        }));
    }

    #[tokio::test]
    async fn main_route_requires_allow_main_clean_tree_and_refuses_non_fast_forward_push() {
        let s = scratch("main-branch");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge, _forge_task) = start_fake_forge_with_state(json!([
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
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        // 1. Refused 403 unless in [chat_repos] allow_main, naming `ling chat repos allow-main org/widget`.
        let nonce = get_nonce(&server, &cookie, &thread, "main").await;
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread, "confirm": nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN, "{body}");
        assert!(body.contains("ling chat repos allow-main org/widget"), "{body}");

        // 2. Enable allow_main for org/widget; refuse while worktree has uncommitted changes.
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nallow_main = [\"org/widget\"]\n",
        )
        .unwrap();
        std::fs::write(wt.join("dirty.txt"), "uncommitted\n").unwrap();
        let nonce = get_nonce(&server, &cookie, &thread, "main").await;
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread, "confirm": nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{body}");

        // 3. Clean worktree switches to main and records "main" in index.json.
        std::fs::remove_file(wt.join("dirty.txt")).unwrap();
        let nonce = get_nonce(&server, &cookie, &thread, "main").await;
        let (st, main_res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread, "confirm": nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(main_res["branch"], "main");
        let records = load_index(&server.config.codex_home);
        assert_eq!(records[0].branch.as_deref(), Some("main"));

        // 4. Commit and push on main succeeds when fast-forward.
        std::fs::write(wt.join("on-main.txt"), "direct on main\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "commit on main", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &thread, "push").await;
        let (st, p_res, p_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{p_body}");
        assert_eq!(p_res["branch"], "main");
        assert_eq!(p_res["pushed"], 1);

        // 5. Advance main on bare origin from outside so a subsequent push on main is non-fast-forward.
        let seed_work = s.dir.join("seed-work").join("org_widget");
        git_sync(&seed_work, &["pull", "--rebase", "origin", "main"]);
        std::fs::write(seed_work.join("other-main.txt"), "desk push\n").unwrap();
        git_sync(&seed_work, &["add", "other-main.txt"]);
        git_sync(&seed_work, &["commit", "-m", "desk commit on main"]);
        git_sync(&seed_work, &["push", "origin", "main"]);

        std::fs::write(wt.join("phone-main-2.txt"), "phone push behind\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "second main commit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &thread, "push").await;
        let (st, nf_res, nf_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_GATEWAY, "{nf_body}");
        assert!(
            nf_res["reason"].as_str().unwrap().contains("the forge's main moved; /update first"),
            "{nf_body}"
        );
    }

    #[test]
    fn repo_checkout_refuses_outside_dirs_and_symlinks_and_records_app_thread() {
        let s = scratch("repo-checkout-policy");
        let home = s.dir.join("codex-home");
        let chat_repos = root_dir(&home);
        let checkout = chat_repos.join("org_widget").join("deadbeef");
        let outside = s.dir.join("outside-repo");
        std::fs::create_dir_all(&checkout).unwrap();
        std::fs::create_dir_all(&outside).unwrap();

        let make_record = |thread: &str, dir: &Path| CheckoutRecord {
            thread: thread.to_string(),
            repo: "org/widget".to_string(),
            bridge: "signal".to_string(),
            chat: "note-to-self".to_string(),
            device: "signal-bridge".to_string(),
            dir: dir.to_string_lossy().into_owned(),
            branch: Some(format!("phone/{thread}")),
            base: "main".to_string(),
            app_threads: Vec::new(),
            read_only: false,
            created: 1_700_000_000,
            last_used: 1_700_000_000,
        };

        let symlink_escape = chat_repos.join("org_widget").join("feedface");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, &symlink_escape).unwrap();

        save_index(
            &home,
            &[
                make_record("deadbeef", &checkout),
                make_record("bad00001", &outside),
                make_record("feedface", &symlink_escape),
            ],
        )
        .unwrap();

        // Valid checkout inside chat-repos/ that is in index.json passes.
        assert!(is_repo_checkout(&home, &checkout));
        // Unlisted directory inside chat-repos/ is refused.
        let unlisted = chat_repos.join("org_widget").join("cafebabe");
        std::fs::create_dir_all(&unlisted).unwrap();
        assert!(!is_repo_checkout(&home, &unlisted));
        // Directory outside chat-repos/ that is present in index.json is still refused.
        assert!(!is_repo_checkout(&home, &outside));
        // Symlink inside chat-repos/ pointing outside chat-repos/ is refused after canonicalisation.
        #[cfg(unix)]
        assert!(!is_repo_checkout(&home, &symlink_escape));

        // record_app_thread records app_thread on the matching checkout and refuses outside dirs.
        assert!(!is_repo_thread(&home, "thr-99"));
        assert!(!record_app_thread(&home, &outside, "thr-outside").unwrap());
        assert!(!is_repo_thread(&home, "thr-outside"));
        assert!(record_app_thread(&home, &checkout, "thr-99").unwrap());
        assert!(is_repo_thread(&home, "thr-99"));
        assert!(record_app_thread(&home, &checkout, "thr-100").unwrap());
        assert!(is_repo_thread(&home, "thr-99"));
        assert!(is_repo_thread(&home, "thr-100"));
        let loaded = load_index(&home);
        assert_eq!(loaded[0].app_threads, vec!["thr-99".to_string(), "thr-100".to_string()]);
    }

    #[tokio::test]
    async fn concurrency_warnings_for_same_checkout_and_other_checkout_block_nothing() {
        let s = scratch("concurrency-warnings");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let repos_json = json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]);
        let (forge_url, _forge_handle) = start_fake_forge(repos_json).await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        // Open checkout 1 for signal:chat1
        let (_, open1, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "chat1" }),
        )
        .await;
        let thread1 = open1["thread"].as_str().unwrap().to_string();
        let wt1 = PathBuf::from(open1["cwd"].as_str().unwrap());

        // Open checkout 2 for signal:chat2 (same repo org/widget, different checkout)
        let (_, open2, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "chat2" }),
        )
        .await;
        let thread2 = open2["thread"].as_str().unwrap().to_string();
        let branch2 = format!("phone/{thread2}");

        // Initially, no other turns running -> warnings empty.
        let (_, state1, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread1}")).await;
        assert_eq!(state1["warnings"].as_array().unwrap().len(), 0);

        // Mark thread2 running in other checkout:
        server.chat_repos.set_thread_running(&thread2, true);
        let (_, state1, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread1}")).await;
        let warnings = state1["warnings"].as_array().unwrap();
        assert_eq!(warnings.len(), 1);
        let expected_other = format!(
            "org/widget is also being worked on in another checkout (branch {branch2}, running). Phone edits stay on phone/{thread1}."
        );
        assert_eq!(warnings[0].as_str().unwrap(), expected_other);

        // Mark another thread running in the same checkout:
        server.chat_repos.set_thread_running("desk-thread", true);
        server.chat_repos.record_thread_place("desk-thread", wt1.clone());
        let (_, state1, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread1}")).await;
        let warnings = state1["warnings"].as_array().unwrap();
        assert_eq!(warnings.len(), 2);
        assert!(warnings.iter().any(|w| w.as_str() == Some("Another thread is running in this checkout.")));
        assert!(warnings.iter().any(|w| w.as_str() == Some(&expected_other)));

        // Enable allow_main and switch thread1 to main:
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nallow_main = [\"org/widget\"]\n",
        )
        .unwrap();
        let nonce = get_nonce(&server, &cookie, &thread1, "main").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread1, "confirm": nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        // On main with concurrency warnings -> gains "Both may push main; /push refuses a non-fast-forward."
        let (_, state1, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread1}")).await;
        let warnings = state1["warnings"].as_array().unwrap();
        assert_eq!(warnings.len(), 3);
        assert!(warnings.iter().any(|w| w.as_str() == Some("Both may push main; /push refuses a non-fast-forward.")));
    }

    #[tokio::test]
    async fn budget_80_notice_refusal_at_100_and_raise_until_midnight() {
        let s = scratch("budget-lifecycle");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let repos_json = json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]);
        let (forge_url, _forge_handle) = start_fake_forge(repos_json).await;
        let (server, probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\ntokens_per_day = 1000000\n",
        )
        .unwrap();

        // Fix clock to 2026-10-20 08:00 UTC with +02:00 offset (midnight at 22:00 UTC).
        let morning = 1_792_483_200u64;
        probe.set_now_secs(morning);
        probe.set_utc_offset(Some(7200));

        let cookie = owner_cookie(&server).await;
        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let records = load_index(&server.config.codex_home);
        let record = records.iter().find(|r| r.thread == thread).unwrap().clone();

        // 1. Initially usage is 0, notices empty. First notification establishes baseline (0 charged).
        let (_, state, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(state["budget"]["used"], 0);
        assert_eq!(state["budget"]["limit"], 1_000_000);
        assert!(server.chat_repos.take_notices().is_empty());
        server.chat_repos.add_token_usage(&record, 1_000_000, 0);

        // 2. Add tokens up to 790,000 (< 80%). No notice.
        server.chat_repos.add_token_usage(&record, 1_000_000, 790_000);
        assert!(server.chat_repos.take_notices().is_empty());
        assert!(server.chat_repos.check_budget_before_turn(&record, 1_000_000).is_ok());

        // 3. Reach 800,000 (80%). Notice recorded once.
        server.chat_repos.add_token_usage(&record, 1_000_000, 800_000);
        let notices = server.chat_repos.take_notices();
        assert_eq!(notices.len(), 1);
        assert_eq!(notices[0].chat, "signal:c1");
        assert_eq!(notices[0].repo, "org/widget");
        assert_eq!(notices[0].kind, "budget80");

        // Further token usage does not re-fire 80% notice.
        server.chat_repos.add_token_usage(&record, 1_000_000, 850_000);
        assert!(server.chat_repos.take_notices().is_empty());

        // 4. Reach 1,000,000 (100%). Check before turn refuses.
        let exhausted = server.chat_repos.add_token_usage(&record, 1_000_000, 1_000_000);
        assert!(exhausted);
        let err = server.chat_repos.check_budget_before_turn(&record, 1_000_000).unwrap_err();
        assert_eq!(err, "today's model budget for this chat is used up; it resets at 00:00");

        // Git routes still work!
        let (st, state, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(st, StatusCode::OK);
        assert_eq!(state["budget"]["used"], 1_000_000);

        // 5. POST /api/repos/budget raises budget until midnight.
        let b_nonce = get_nonce(&server, &cookie, &thread, "budget").await;
        let (st, b_res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/budget",
            json!({ "thread": thread, "add": 500_000, "confirm": b_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(b_res["used"], 1_000_000);
        assert_eq!(b_res["limit"], 1_500_000);

        // Refusal is lifted!
        assert!(server.chat_repos.check_budget_before_turn(&record, 1_000_000).is_ok());

        // 6. After midnight, usage resets, raise is gone, limit is base 1,000,000.
        probe.set_now_secs(morning + 15 * 3600); // 23:00 UTC (01:00 next day)
        let (used, limit, _) = server.chat_repos.budget_snapshot(&record, 1_000_000);
        assert_eq!(used, 0);
        assert_eq!(limit, 1_000_000);
        assert!(server.chat_repos.check_budget_before_turn(&record, 1_000_000).is_ok());
    }

    #[tokio::test]
    async fn nights_turn_failure_with_benchmark_refusal_records_busy_notice() {
        let s = scratch("nights-busy");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let repos_json = json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]);
        let (forge_url, _forge_handle) = start_fake_forge(repos_json).await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url.clone()), true);
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        server.chat_repos.record_app_thread(&server.config.codex_home, &wt, "app-thr-busy").unwrap();

        // Simulate a turn failing with the benchmark refusal marker.
        let failed_turn = json!({
            "threadId": "app-thr-busy",
            "turn": {
                "id": "turn-1",
                "status": "failed",
                "error": {
                    "message": "unexpected status 503 Service Unavailable: The model is running a benchmark (night 4, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`."
                }
            }
        });

        let (upstream_tx, _upstream_rx) = tokio::sync::mpsc::unbounded_channel();
        crate::relay::handle_notification_repo_limits(
            &server,
            &Arc::default(),
            "turn/completed",
            Some(&failed_turn),
            &upstream_tx,
        );

        let notices = server.chat_repos.take_notices();
        assert_eq!(notices.len(), 1);
        assert_eq!(notices[0].chat, "signal:c1");
        assert_eq!(notices[0].repo, "org/widget");
        assert_eq!(notices[0].kind, "busy");

        // Git routes keep working (diff, state).
        let (st, _, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(st, StatusCode::OK);
    }

    #[tokio::test]
    async fn relay_chat_work_turn_tool_bound_steers_then_interrupts() {
        let s = scratch("relay-bounds");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let repos_json = json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]);
        let (forge_url, _forge_handle) = start_fake_forge(repos_json).await;
        let (server, probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nwork_tool_calls = 3\nwork_answer_seconds = 1800\nwork_command_seconds = 600\n",
        )
        .unwrap();

        probe.set_now_ms(1_000);
        let cookie = owner_cookie(&server).await;
        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        server.chat_repos.record_app_thread(&server.config.codex_home, &wt, "app-thr-work").unwrap();

        let shared = Arc::new(crate::relay::Shared::default());
        let (upstream_tx, mut upstream_rx) = tokio::sync::mpsc::unbounded_channel();

        // 1. Turn starts: RepoWatch is created.
        let turn_start_ev = json!({
            "threadId": "app-thr-work",
            "turn": { "id": "turn-bound-1" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "turn/started",
            Some(&turn_start_ev),
            &upstream_tx,
        );
        assert!(upstream_rx.try_recv().is_err());

        // 2. Feed 2 tool calls: below limit of 3, no steer sent.
        for i in 1..=2 {
            let item_ev = json!({
                "threadId": "app-thr-work",
                "item": { "id": format!("tool-{i}"), "type": "applyPatch" }
            });
            crate::relay::handle_notification_repo_limits(
                &server,
                &shared,
                "item/started",
                Some(&item_ev),
                &upstream_tx,
            );
        }
        assert!(upstream_rx.try_recv().is_err());

        // 3. 3rd tool call: hits work_tool_calls = 3 -> immediately sends turn/steer!
        let item3 = json!({
            "threadId": "app-thr-work",
            "item": { "id": "tool-3", "type": "applyPatch" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "item/started",
            Some(&item3),
            &upstream_tx,
        );
        let steer_msg = upstream_rx.try_recv().expect("expected turn/steer to upstream");
        let text = match steer_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let steer_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(steer_val["method"], "turn/steer");
        assert_eq!(steer_val["params"]["threadId"], "app-thr-work");
        assert_eq!(steer_val["params"]["expectedTurnId"], "turn-bound-1");
        assert_eq!(steer_val["params"]["input"][0]["text"], "Stop here and summarise what changed so far.");
        assert!(steer_val["id"].as_i64().unwrap() >= crate::relay::INTERNAL_REQUEST_ID_BASE);

        // Repeated checks during grace window send nothing further.
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        assert!(upstream_rx.try_recv().is_err());

        // 4. Advance clock by 60 s grace: check sends turn/interrupt!
        probe.set_now_ms(1_000 + 60_000);
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        let interrupt_msg = upstream_rx.try_recv().expect("expected turn/interrupt to upstream");
        let text = match interrupt_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let interrupt_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(interrupt_val["method"], "turn/interrupt");
        assert_eq!(interrupt_val["params"]["threadId"], "app-thr-work");
        assert_eq!(interrupt_val["params"]["turnId"], "turn-bound-1");
        assert!(interrupt_val["id"].as_i64().unwrap() >= crate::relay::INTERNAL_REQUEST_ID_BASE);

        // After interrupt, watch is done and no final turn is initiated.
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        assert!(upstream_rx.try_recv().is_err());
    }

    #[tokio::test]
    async fn relay_chat_work_turn_command_and_answer_bounds_steer_then_interrupt() {
        let s = scratch("relay-cmd-ans-bounds");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let repos_json = json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]);
        let (forge_url, _forge_handle) = start_fake_forge(repos_json).await;
        let (server, probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nwork_command_seconds = 10\nwork_answer_seconds = 30\n",
        )
        .unwrap();

        probe.set_now_ms(0);
        let cookie = owner_cookie(&server).await;
        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        server.chat_repos.record_app_thread(&server.config.codex_home, &wt, "app-thr-cmd").unwrap();

        let shared = Arc::new(crate::relay::Shared::default());
        let (upstream_tx, mut upstream_rx) = tokio::sync::mpsc::unbounded_channel();

        // 1. Turn starts at t=0
        let turn_start_ev = json!({
            "threadId": "app-thr-cmd",
            "turn": { "id": "turn-cmd-1" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "turn/started",
            Some(&turn_start_ev),
            &upstream_tx,
        );

        // Start a command at t=5,000 ms.
        probe.set_now_ms(5_000);
        let cmd_start = json!({
            "threadId": "app-thr-cmd",
            "item": { "id": "cmd-hung", "type": "commandExecution" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "item/started",
            Some(&cmd_start),
            &upstream_tx,
        );
        assert!(upstream_rx.try_recv().is_err());

        // Advance to t=15,000 ms (10 s of command): triggers Steer.
        probe.set_now_ms(15_000);
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        let steer_msg = upstream_rx.try_recv().expect("expected turn/steer on command bound");
        let text = match steer_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let steer_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(steer_val["method"], "turn/steer");

        // Advance 60 s grace to t=75,000 ms: triggers Interrupt.
        probe.set_now_ms(75_000);
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        let int_msg = upstream_rx.try_recv().expect("expected turn/interrupt on command bound");
        let text = match int_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let int_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(int_val["method"], "turn/interrupt");

        // Complete the first turn.
        let turn_end_ev = json!({
            "threadId": "app-thr-cmd",
            "turn": { "id": "turn-cmd-1", "status": "completed" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "turn/completed",
            Some(&turn_end_ev),
            &upstream_tx,
        );

        // 2. Answer-time bound test: start turn 2 at t=100,000 ms.
        probe.set_now_ms(100_000);
        let turn2_start_ev = json!({
            "threadId": "app-thr-cmd",
            "turn": { "id": "turn-ans-2" }
        });
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "turn/started",
            Some(&turn2_start_ev),
            &upstream_tx,
        );

        // Advance 30 s (work_answer_seconds = 30) to t=130,000 ms: triggers Steer.
        probe.set_now_ms(130_000);
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        let steer_msg = upstream_rx.try_recv().expect("expected turn/steer on answer bound");
        let text = match steer_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let steer_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(steer_val["method"], "turn/steer");
        assert_eq!(steer_val["params"]["expectedTurnId"], "turn-ans-2");

        // Advance 60 s grace to t=190,000 ms: triggers Interrupt.
        probe.set_now_ms(190_000);
        crate::relay::check_repo_watches(&server, &shared, &upstream_tx);
        let int_msg = upstream_rx.try_recv().expect("expected turn/interrupt on answer bound");
        let text = match int_msg {
            tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
            _ => panic!("expected text message"),
        };
        let int_val: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(int_val["method"], "turn/interrupt");
        assert_eq!(int_val["params"]["turnId"], "turn-ans-2");
    }

    #[tokio::test]
    async fn fork_of_repo_thread_and_legacy_app_thread_field_are_recorded_and_bounded() {
        let s = scratch("fork-and-legacy");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nwork_tool_calls = 1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        let wt_str = wt.to_string_lossy().into_owned();

        // Write a legacy index.json entry with only `"app_thread": "legacy-thr-1"`.
        let legacy_idx = json!([
            {
                "thread": open["thread"],
                "repo": "org/widget",
                "bridge": "signal",
                "chat": "c1",
                "device": "owner",
                "dir": wt_str,
                "branch": open["branch"],
                "base": "main",
                "app_thread": "legacy-thr-1",
                "read_only": false,
                "created": 1_700_000_000u64,
                "last_used": 1_700_000_000u64,
            }
        ]);
        std::fs::write(
            index_path(&server.config.codex_home),
            serde_json::to_string_pretty(&legacy_idx).unwrap(),
        )
        .unwrap();

        let loaded = load_index(&server.config.codex_home);
        assert_eq!(loaded[0].app_threads, vec!["legacy-thr-1".to_string()]);
        assert!(is_repo_thread(&server.config.codex_home, "legacy-thr-1"));

        // Deliver a fork response for a fork of `legacy-thr-1` and verify `forked-thr-2` is appended
        // to `app_threads` and gets per-turn bounds.
        let shared = Arc::new(crate::relay::Shared::default());
        shared
            .repo_openers
            .lock()
            .unwrap()
            .insert("77".to_string(), wt.clone());
        let (out_tx, _out_rx) = tokio::sync::mpsc::unbounded_channel();
        let (upstream_tx, mut upstream_rx) = tokio::sync::mpsc::unbounded_channel();
        let fork_resp = json!({
            "id": 77,
            "result": {
                "thread": {
                    "id": "forked-thr-2",
                    "cwd": wt_str,
                }
            }
        })
        .to_string();
        crate::relay::deliver(&server, &shared, &out_tx, shared.tab_id, &fork_resp, &upstream_tx);

        let updated = load_index(&server.config.codex_home);
        assert_eq!(
            updated[0].app_threads,
            vec!["legacy-thr-1".to_string(), "forked-thr-2".to_string()]
        );

        // Start a turn on `forked-thr-2` and feed 1 tool call -> steered immediately!
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "turn/started",
            Some(&json!({ "threadId": "forked-thr-2", "turn": { "id": "turn-fork-1" } })),
            &upstream_tx,
        );
        crate::relay::handle_notification_repo_limits(
            &server,
            &shared,
            "item/started",
            Some(&json!({ "threadId": "forked-thr-2", "item": { "id": "t1", "type": "applyPatch" } })),
            &upstream_tx,
        );
        let steer_msg = upstream_rx.try_recv().expect("expected turn/steer on forked repo thread");
        let tokio_tungstenite::tungstenite::Message::Text(text) = steer_msg else {
            panic!("expected text")
        };
        let steer_val: Value = serde_json::from_str(text.as_str()).unwrap();
        assert_eq!(steer_val["method"], "turn/steer");
        assert_eq!(steer_val["params"]["threadId"], "forked-thr-2");
    }

    #[tokio::test]
    async fn untracked_line_count_skips_symlinks_and_caps_over_8_mib_as_null() {
        let s = scratch("untracked-bounds");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        std::fs::write(wt.join("small.txt"), "one\ntwo\nthree\n").unwrap();
        let big = std::fs::File::create(wt.join("over8m.bin")).unwrap();
        big.set_len(MAX_UNTRACKED_STAT_BYTES + 1).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink("/dev/zero", wt.join("zero-link")).unwrap();

        let (st, state, body) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(st, StatusCode::OK, "{body}");
        let uncommitted = state["uncommitted"].as_array().unwrap();
        let small = uncommitted.iter().find(|e| e["path"] == "small.txt").unwrap();
        assert_eq!(small["added"], json!(3));
        let over = uncommitted.iter().find(|e| e["path"] == "over8m.bin").unwrap();
        assert!(over["added"].is_null(), "expected null line count for >8 MiB file: {over}");
        #[cfg(unix)]
        {
            let link = uncommitted.iter().find(|e| e["path"] == "zero-link").unwrap();
            assert!(link["added"].is_null(), "expected null line count for symlink: {link}");
        }
    }

    #[tokio::test]
    async fn anonymity_scans_merge_commits_binary_files_and_refuses_on_scan_error() {
        let s = scratch("anon-merge-bin-err");
        let pub_bare = make_bare_repo(&s.dir, "org/pubrepo");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/pubrepo",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", pub_bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let denylist_path = s.dir.join("user/.config/dreamference/anonymity-denylist.txt");
        std::fs::write(&denylist_path, "box-sentinel-merge\nbox-sentinel-bin\n").unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/pubrepo", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        // (a) Conflicted merge commit containing a denylisted string is caught by diff-tree -m.
        let seed_pub = s.dir.join("seed-work").join("org_pubrepo");
        std::fs::write(seed_pub.join("README.md"), "# Demo\nupstream line\n").unwrap();
        git_sync(&seed_pub, &["add", "README.md"]);
        git_sync(&seed_pub, &["commit", "-m", "upstream edit"]);
        git_sync(&seed_pub, &["push", "origin", "main"]);

        std::fs::write(wt.join("README.md"), "# Demo\nlocal branch line\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "local edit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let u_nonce = get_nonce(&server, &cookie, &thread, "update").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/update",
            json!({ "thread": thread, "confirm": u_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT);

        // Resolve conflict by introducing `box-sentinel-merge` in the merge commit itself.
        std::fs::write(wt.join("README.md"), "# Demo\nmerged with box-sentinel-merge\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "resolve merge", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");

        let p_nonce = get_nonce(&server, &cookie, &thread, "push").await;
        let (st, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
        assert_eq!(res["refused"], "anonymity");
        assert!(!body.contains("box-sentinel-merge"), "{body}");

        // (b) Binary / `-diff` file containing a denylisted string is caught by `--text`.
        git_sync(&wt, &["reset", "--hard", "refs/remotes/origin/main"]);
        std::fs::write(wt.join(".gitattributes"), "*.dat -diff\n").unwrap();
        std::fs::write(wt.join("payload.dat"), b"BIN\x00box-sentinel-bin\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "add binary", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        let p_nonce = get_nonce(&server, &cookie, &thread, "push").await;
        let (st, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
        assert_eq!(res["refused"], "anonymity");
        assert!(!body.contains("box-sentinel-bin"), "{body}");

        // (e) An unreadable denylist (e.g. a directory at the denylist path) refuses the push.
        git_sync(&wt, &["reset", "--hard", "refs/remotes/origin/main"]);
        std::fs::write(wt.join("clean.txt"), "clean line\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "clean commit", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        std::fs::remove_file(&denylist_path).unwrap();
        std::fs::create_dir_all(&denylist_path).unwrap();
        let p_nonce = get_nonce(&server, &cookie, &thread, "push").await;
        let (st, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
        assert_eq!(res["refused"], "anonymity");
        assert!(
            body.contains("the anonymity check could not run"),
            "expected scan error refusal: {body}"
        );
    }

    #[tokio::test]
    async fn concurrent_opens_and_record_app_thread_preserve_all_index_updates() {
        let s = scratch("concurrent-index");
        let bare_a = make_bare_repo(&s.dir, "org/alpha");
        let bare_b = make_bare_repo(&s.dir, "org/beta");
        let bare_c = make_bare_repo(&s.dir, "org/gamma");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/alpha",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare_a.display()),
            },
            {
                "full_name": "org/beta",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare_b.display()),
            },
            {
                "full_name": "org/gamma",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare_c.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        // Open org/alpha first so we can call record_app_thread on it concurrently with opening
        // org/beta and org/gamma.
        let (st, open_a, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/alpha", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);
        let wt_a = PathBuf::from(open_a["cwd"].as_str().unwrap());

        let srv_b = server.clone();
        let ck_b = cookie.clone();
        let srv_c = server.clone();
        let ck_c = cookie.clone();
        let srv_rec = server.clone();
        let wt_a_clone = wt_a.clone();

        let (res_b, res_c, rec_ok) = tokio::join!(
            async move {
                post_json(
                    &srv_b,
                    &ck_b,
                    "/api/repos/open",
                    json!({ "repo": "org/beta", "bridge": "signal", "chat": "c2" }),
                )
                .await
            },
            async move {
                post_json(
                    &srv_c,
                    &ck_c,
                    "/api/repos/open",
                    json!({ "repo": "org/gamma", "bridge": "signal", "chat": "c3" }),
                )
                .await
            },
            async move {
                srv_rec
                    .chat_repos
                    .record_app_thread(&srv_rec.config.codex_home, &wt_a_clone, "app-alpha-1")
                    .unwrap()
            }
        );

        assert_eq!(res_b.0, StatusCode::OK);
        assert_eq!(res_c.0, StatusCode::OK);
        assert!(rec_ok);

        let records = load_index(&server.config.codex_home);
        assert_eq!(records.len(), 3);
        assert!(records.iter().any(|r| r.repo == "org/alpha" && r.app_threads == vec!["app-alpha-1".to_string()]));
        assert!(records.iter().any(|r| r.repo == "org/beta"));
        assert!(records.iter().any(|r| r.repo == "org/gamma"));
    }

    #[tokio::test]
    async fn repo_main_uses_default_branch_refuses_other_worktree_and_push_rechecks_allow_main() {
        let s = scratch("main-default-branch");
        let bare = make_bare_repo(&s.dir, "org/trunkrepo");
        // Rename the bare repo's default branch from `main` to `trunk`.
        let seed_work = s.dir.join("seed-work").join("org_trunkrepo");
        git_sync(&seed_work, &["branch", "-M", "trunk"]);
        git_sync(&seed_work, &["push", "origin", "trunk"]);
        git_sync(&bare, &["symbolic-ref", "HEAD", "refs/heads/trunk"]);

        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/trunkrepo",
                "default_branch": "trunk",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": true,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nallow_main = [\"org/trunkrepo\"]\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open1, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/trunkrepo", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread1 = open1["thread"].as_str().unwrap().to_string();
        let wt1 = PathBuf::from(open1["cwd"].as_str().unwrap());

        let (_, open2, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/trunkrepo", "bridge": "signal", "chat": "c2" }),
        )
        .await;
        let thread2 = open2["thread"].as_str().unwrap().to_string();
        let (_, allowed_state, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread1}")).await;
        assert_eq!(allowed_state["allow_main"], json!(true), "{allowed_state}");
        assert_eq!(allowed_state["merge_in_progress"], json!(false), "{allowed_state}");

        // 1. Switching thread1 with `/api/repos/main` switches to `trunk` (record.base), not `main`.
        let m_nonce1 = get_nonce(&server, &cookie, &thread1, "main").await;
        let (st, m_res1, body1) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread1, "confirm": m_nonce1 }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body1}");
        assert_eq!(m_res1["branch"], "trunk");

        // 2. Switching thread2 to `trunk` while thread1 already has `trunk` checked out is refused with 409.
        let m_nonce2 = get_nonce(&server, &cookie, &thread2, "main").await;
        let (st, _, body2) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread2, "confirm": m_nonce2 }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT, "{body2}");
        assert!(body2.contains("already checked out in another worktree"), "{body2}");

        // 3. Commit on `trunk` in thread1, then remove `org/trunkrepo` from `allow_main` and verify
        // `/api/repos/push` re-checks `allow_main` and refuses with 403.
        std::fs::write(wt1.join("trunk.txt"), "on trunk\n").unwrap();
        let c_nonce = get_nonce(&server, &cookie, &thread1, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread1, "message": "commit on trunk", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK);

        std::fs::write(server.config.user_config.as_ref().unwrap(), "[chat_repos]\nallow_main = []\n").unwrap();
        let p_nonce = get_nonce(&server, &cookie, &thread1, "push").await;
        let (st, _, p_body) = post_json(
            &server,
            &cookie,
            "/api/repos/push",
            json!({ "thread": thread1, "confirm": p_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN, "{p_body}");
        assert!(p_body.contains("ling chat repos allow-main org/trunkrepo"), "{p_body}");
    }

    #[tokio::test]
    async fn submodule_one_line_off_forge_entry_is_left_empty() {
        let s = scratch("submodule-oneline");
        let bare = make_bare_repo(&s.dir, "org/subtest");
        let seed_work = s.dir.join("seed-work").join("org_subtest");
        // Single-line section header with url (`[submodule "sneaky"] url = ...`) that git accepts.
        std::fs::write(
            seed_work.join(".gitmodules"),
            "[submodule \"sneaky\"] url = https://127.0.0.1:9443/outside/sneaky.git\n\tpath = vendor/sneaky\n",
        )
        .unwrap();
        git_sync(&seed_work, &["add", ".gitmodules"]);
        git_sync(&seed_work, &["commit", "-m", "add one-line submodule"]);
        git_sync(&seed_work, &["push", "origin", "main"]);

        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/subtest",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": true,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        let (st, opened, body) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/subtest", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        let notes = opened["notes"].as_array().unwrap();
        assert!(
            notes.iter().any(|n| n.as_str().unwrap().contains("vendor/sneaky")),
            "expected off-forge note for one-line submodule entry: {notes:?}"
        );
        let wt = PathBuf::from(opened["cwd"].as_str().unwrap());
        assert!(!wt.join("vendor/sneaky/.git").exists());
    }

    #[tokio::test]
    async fn turn_watches_do_not_duplicate_steers_hand_over_on_tab_close_and_record_unbounded() {
        let s = scratch("watch-handover");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nwork_tool_calls = 2\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        server
            .chat_repos
            .record_app_thread(&server.config.codex_home, &wt, "thr-watch-1")
            .unwrap();

        let shared1 = Arc::new(crate::relay::Shared::for_tab(101));
        let shared2 = Arc::new(crate::relay::Shared::for_tab(102));
        let (tx1, mut rx1) = tokio::sync::mpsc::unbounded_channel();
        let (tx2, mut rx2) = tokio::sync::mpsc::unbounded_channel();

        // 1. Both tabs observe turn/started and 2 tool calls for turn-w1: only tab 1 sends steer.
        let start_w1 = json!({ "threadId": "thr-watch-1", "turn": { "id": "turn-w1" } });
        crate::relay::handle_notification_repo_limits(&server, &shared1, "turn/started", Some(&start_w1), &tx1);
        crate::relay::handle_notification_repo_limits(&server, &shared2, "turn/started", Some(&start_w1), &tx2);

        for i in 1..=2 {
            let item = json!({
                "threadId": "thr-watch-1",
                "turnId": "turn-w1",
                "item": { "id": format!("tool-{i}"), "type": "applyPatch" }
            });
            crate::relay::handle_notification_repo_limits(&server, &shared1, "item/started", Some(&item), &tx1);
            crate::relay::handle_notification_repo_limits(&server, &shared2, "item/started", Some(&item), &tx2);
        }
        assert!(rx1.try_recv().is_ok(), "owner tab 1 must send turn/steer");
        assert!(rx1.try_recv().is_err(), "owner tab 1 must not send a second steer");
        assert!(rx2.try_recv().is_err(), "observer tab 2 must not duplicate turn/steer");

        // Complete turn-w1.
        let end_w1 = json!({ "threadId": "thr-watch-1", "turn": { "id": "turn-w1", "status": "completed" } });
        crate::relay::handle_notification_repo_limits(&server, &shared1, "turn/completed", Some(&end_w1), &tx1);

        // 2. Start turn-w2 on tab 1 while tab 2 is also connected to thr-watch-1; close tab 1 ->
        // watch is handed over to tab 2, and when tab 2 closes with no other tab left, an
        // `unbounded` notice is recorded.
        let start_w2 = json!({ "threadId": "thr-watch-1", "turn": { "id": "turn-w2" } });
        crate::relay::handle_notification_repo_limits(&server, &shared1, "turn/started", Some(&start_w2), &tx1);
        crate::relay::handle_notification_repo_limits(&server, &shared2, "turn/started", Some(&start_w2), &tx2);

        server.chat_repos.forget_tab(shared1.tab_id);
        assert!(server.chat_repos.take_notices().is_empty(), "handover to tab 2 must not emit unbounded");

        // Tab 2 now owns the watch and steers after 2 tool calls.
        for i in 1..=2 {
            let item = json!({
                "threadId": "thr-watch-1",
                "turnId": "turn-w2",
                "item": { "id": format!("tool2-{i}"), "type": "applyPatch" }
            });
            crate::relay::handle_notification_repo_limits(&server, &shared2, "item/started", Some(&item), &tx2);
        }
        assert!(rx2.try_recv().is_ok(), "tab 2 must send turn/steer after handover");

        // Now close tab 2 while turn-w2 is still active and no tab remains -> records `unbounded`.
        server.chat_repos.forget_tab(shared2.tab_id);
        let notices = server.chat_repos.take_notices();
        assert_eq!(notices.len(), 1);
        assert_eq!(notices[0].kind, "unbounded");
        assert_eq!(notices[0].repo, "org/widget");
    }

    #[tokio::test]
    async fn run_git_times_out_kills_hung_command_and_names_subcommand_without_secrets() {
        assert_eq!(SystemHostProbe.git_timeout("clone"), Duration::from_secs(300));
        assert_eq!(SystemHostProbe.git_timeout("fetch"), Duration::from_secs(300));
        assert_eq!(SystemHostProbe.git_timeout("push"), Duration::from_secs(300));
        assert_eq!(SystemHostProbe.git_timeout("status"), Duration::from_secs(60));

        let s = scratch("git-timeout");
        let (server, probe) = test_server(&s.dir, None, true);
        probe.set_git_timeout_ms(100);

        // Start a TCP listener that accepts a connection and never replies so `git fetch` hangs.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let _hold = tokio::spawn(async move {
            if let Ok((_sock, _)) = listener.accept().await {
                tokio::time::sleep(Duration::from_secs(30)).await;
            }
        });

        let secret_url = format!("http://127.0.0.1:{port}/{TEST_TOKEN}/org/widget.git");
        // `ls-remote` needs no repository, so it reaches the silent listener and hangs there.
        let err = run_git(&server, None, &["ls-remote", &secret_url], false)
            .await
            .expect_err("expected hung git ls-remote to time out");
        assert!(err.contains("git ls-remote timed out"), "unexpected error: {err}");
        assert!(!err.contains(TEST_TOKEN), "timeout error must never contain argument secrets: {err}");
    }

    #[tokio::test]
    async fn diff_output_is_capped_at_1_mib_and_untracked_files_over_8_mib_are_not_diffed() {
        let s = scratch("diff-caps");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": false,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());

        // 1. Tracked file diff > 1 MiB is capped and ends with `(cut: open the file to see all)`.
        let line = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ\n";
        let big_tracked = line.repeat((MAX_DIFF_OUTPUT_BYTES / line.len()) + 500);
        std::fs::write(wt.join("README.md"), big_tracked).unwrap();

        let (st, diff_res, body) = get_json(
            &server,
            &cookie,
            &format!("/api/repos/diff?thread={thread}&scope=uncommitted&path=README.md"),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        let diff_str = diff_res["diff"].as_str().unwrap();
        assert!(diff_str.ends_with("(cut: open the file to see all)"));
        assert!(diff_str.len() <= MAX_DIFF_OUTPUT_BYTES + DIFF_CUT_SUFFIX.len());

        // 2. Untracked file > 8 MiB is not diffed and answers `(cut: open the file to see all)`.
        let giant = std::fs::File::create(wt.join("giant.txt")).unwrap();
        giant.set_len(MAX_UNTRACKED_STAT_BYTES + 10).unwrap();
        let (st, giant_res, body) = get_json(
            &server,
            &cookie,
            &format!("/api/repos/diff?thread={thread}&scope=uncommitted&path=giant.txt"),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(giant_res["diff"], "(cut: open the file to see all)");
    }

    #[tokio::test]
    async fn ensure_worktree_refuses_existing_non_worktree_folder_without_deleting_it() {
        let s = scratch("worktree-preserve");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (server, _probe) = test_server(&s.dir, None, true);

        let non_wt = s.dir.join("existing-folder");
        std::fs::create_dir_all(&non_wt).unwrap();
        let precious = non_wt.join("precious.txt");
        std::fs::write(&precious, "do not delete me\n").unwrap();

        let forge_repo = ForgeRepo {
            repo: "org/widget".to_string(),
            default_branch: "main".to_string(),
            pushed_at: String::new(),
            private: false,
            archived: false,
            mirror: false,
            read_only: false,
            clone_url: format!("file://{}", bare.display()),
        };
        let err = ensure_worktree(&server, &bare, &non_wt, "deadbeef", &forge_repo, None)
            .await
            .expect_err("expected refusal when checkout folder exists without .git");
        assert_eq!(
            err,
            "the checkout folder exists but is not a worktree; remove it at the node"
        );
        assert!(precious.exists(), "ensure_worktree must never delete an existing folder");
    }

    #[tokio::test]
    async fn refused_mutation_requests_do_not_consume_confirmation_nonce() {
        let s = scratch("nonce-preserved-on-refusal");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": true,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        std::fs::write(
            s.dir.join("user/.gitconfig"),
            "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
        )
        .unwrap();
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        std::fs::write(wt.join("edit.txt"), "hello\n").unwrap();

        // 1. Empty commit message is refused with 400 and does NOT consume `c_nonce`.
        let c_nonce = get_nonce(&server, &cookie, &thread, "commit").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "   ", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_REQUEST);

        // Using the same `c_nonce` with a valid message now succeeds!
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/commit",
            json!({ "thread": thread, "message": "valid message with same nonce", "confirm": c_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");

        // 2. Refused `/api/repos/main` (not in allow_main) does NOT consume `m_nonce`.
        let m_nonce = get_nonce(&server, &cookie, &thread, "main").await;
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN);

        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nallow_main = [\"org/widget\"]\n",
        )
        .unwrap();
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/main",
            json!({ "thread": thread, "confirm": m_nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
    }

    /// Ending Full Access takes the checkout's loaded threads back to their sandbox at once and
    /// interrupts a turn still running, over a connection of its own to the app-server.
    #[tokio::test]
    async fn ending_full_access_resets_the_threads_sandbox_and_interrupts_a_running_turn() {
        use futures::SinkExt;
        use futures::StreamExt;
        use tokio_tungstenite::tungstenite::Message;

        let s = scratch("full-access-reset");
        let (server, _probe) = test_server(&s.dir, None, true);
        crate::app_server::prepare_socket_dir(&server.config.socket).unwrap();
        let listener = tokio::net::UnixListener::bind(&server.config.socket).unwrap();
        let (seen_tx, mut seen) = tokio::sync::mpsc::unbounded_channel::<Value>();
        tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
            while let Some(Ok(Message::Text(text))) = ws.next().await {
                let request: Value = serde_json::from_str(text.as_str()).unwrap();
                let _ = seen_tx.send(request.clone());
                let Some(id) = request.get("id").cloned() else { continue };
                // A notification in between is skipped by the reader.
                ws.send(Message::Text(json!({ "method": "thread/status/changed", "params": {} }).to_string().into()))
                    .await
                    .unwrap();
                let result = match request["method"].as_str().unwrap() {
                    "thread/turns/list" if request["params"]["threadId"] == "app-run" => {
                        json!({ "data": [{ "id": "turn-9", "status": "inProgress" }] })
                    }
                    "thread/turns/list" => json!({ "data": [{ "id": "turn-1", "status": "completed" }] }),
                    _ => json!({}),
                };
                ws.send(Message::Text(json!({ "id": id, "result": result }).to_string().into())).await.unwrap();
            }
        });

        let record = CheckoutRecord {
            thread: "deadbeef".to_string(),
            repo: "org/widget".to_string(),
            bridge: "telegram".to_string(),
            chat: "c1".to_string(),
            device: "telegram-app".to_string(),
            dir: s.dir.join("checkout").to_string_lossy().into_owned(),
            branch: Some("phone/deadbeef".to_string()),
            base: "main".to_string(),
            app_threads: vec!["app-idle".to_string(), "app-run".to_string()],
            read_only: false,
            created: 1,
            last_used: 1,
        };
        let checkout = s.dir.join("checkout");
        std::fs::create_dir_all(&checkout).unwrap();
        reset_checkout_sandbox(&server, &record, &checkout).await;

        let mut methods = Vec::new();
        while let Ok(request) = seen.try_recv() {
            if request["method"] == "thread/settings/update" {
                // The user's workspace sandbox, as the next turn's: the network stays on (absent
                // from config.toml counts as on, as the launcher writes it).
                assert_eq!(
                    request["params"]["sandboxPolicy"],
                    json!({ "type": "workspaceWrite", "writableRoots": [], "networkAccess": true, "excludeTmpdirEnvVar": false, "excludeSlashTmp": false }),
                    "{request}"
                );
            }
            if request["method"] == "turn/interrupt" {
                assert_eq!(request["params"], json!({ "threadId": "app-run", "turnId": "turn-9" }));
            }
            methods.push(format!("{} {}", request["method"].as_str().unwrap(), request["params"]["threadId"].as_str().unwrap_or("")));
        }
        assert_eq!(
            methods,
            vec![
                "initialize ",
                "initialized ",
                "thread/settings/update app-idle",
                "thread/turns/list app-idle",
                "thread/settings/update app-run",
                "thread/turns/list app-run",
                "turn/interrupt app-run",
            ]
        );
    }

    #[tokio::test]
    async fn full_access_typed_confirmation_grant_refusals_and_workspace_reset() {
        let s = scratch("full-access-route");
        let bare = make_bare_repo(&s.dir, "org/widget");
        let bare_ro = make_bare_repo(&s.dir, "org/readonly");
        let (forge_url, _forge_handle) = start_fake_forge(json!([
            {
                "full_name": "org/widget",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": true,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": true, "admin": true },
                "clone_url": format!("file://{}", bare.display()),
            },
            {
                "full_name": "org/readonly",
                "default_branch": "main",
                "updated_at": "2026-10-20T10:00:00Z",
                "private": true,
                "archived": false,
                "mirror": false,
                "permissions": { "pull": true, "push": false, "admin": false },
                "clone_url": format!("file://{}", bare_ro.display()),
            }
        ]))
        .await;
        let (server, _probe) = test_server(&s.dir, Some(forge_url), true);
        let cookie = owner_cookie(&server).await;

        let (_, open, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/widget", "bridge": "signal", "chat": "c1" }),
        )
        .await;
        let thread = open["thread"].as_str().unwrap().to_string();
        let wt = PathBuf::from(open["cwd"].as_str().unwrap());
        server
            .chat_repos
            .record_app_thread_async(&server.config.codex_home, &wt, "app-t1")
            .await
            .unwrap();

        let (_, open_ro, _) = post_json(
            &server,
            &cookie,
            "/api/repos/open",
            json!({ "repo": "org/readonly", "bridge": "signal", "chat": "c2" }),
        )
        .await;
        let thread_ro = open_ro["thread"].as_str().unwrap().to_string();

        // Initial state shows full_access_allowed = false and full_access = false.
        let (st, state_initial, body) = get_json(
            &server,
            &cookie,
            &format!("/api/repos/state?thread={thread}"),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(state_initial["full_access_allowed"], false);
        assert_eq!(state_initial["full_access"], false);
        assert!(!server.chat_repos.is_full_access_granted(&thread));

        // 1. Refused while full_access is off in settings (and nonce is unconsumed).
        let nonce = get_nonce(&server, &cookie, &thread, "access").await;
        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "typed": "FULL ACCESS org/widget",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN);
        assert_eq!(
            res["error"],
            "Full Access is off for phone threads; turn it on at the node with `ling chat repos full-access on`"
        );

        // Enable full_access in settings.
        std::fs::write(
            server.config.user_config.as_ref().unwrap(),
            "[chat_repos]\nfull_access = true\n",
        )
        .unwrap();

        // 2. Refused on a read-only checkout.
        let ro_nonce = get_nonce(&server, &cookie, &thread_ro, "access").await;
        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread_ro,
                "level": "full",
                "typed": "FULL ACCESS org/readonly",
                "confirm": ro_nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN);
        assert_eq!(res["error"], "this repository is read-only on the forge");

        // 3. Refused when the air gap is on for this thread (via dreamference.toml in checkout folder).
        let wt_cfg = wt.join("dreamference.toml");
        std::fs::write(&wt_cfg, "mightling_airgapped = \"on\"\n").unwrap();
        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "typed": "FULL ACCESS org/widget",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::FORBIDDEN);
        assert_eq!(
            res["error"],
            "Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible"
        );
        std::fs::remove_file(&wt_cfg).unwrap();

        // 4. Refused with missing or wrong `typed`, and `nonce` is still unconsumed afterwards.
        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_REQUEST);
        assert_eq!(res["error"], "type FULL ACCESS org/widget exactly");

        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "typed": "full access org/widget",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_REQUEST);
        assert_eq!(res["error"], "type FULL ACCESS org/widget exactly");

        // Invalid level is refused with 400 and preserves the nonce.
        let (st, _, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "other",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::BAD_REQUEST);

        // 5. Granted with the exact text using the SAME unconsumed nonce, and shown in /state.
        let (st, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "typed": "FULL ACCESS org/widget",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(res["level"], "full");
        assert!(server.chat_repos.is_full_access_granted(&thread));

        let (st, state_granted, body) = get_json(
            &server,
            &cookie,
            &format!("/api/repos/state?thread={thread}"),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(state_granted["full_access_allowed"], true);
        assert_eq!(state_granted["full_access"], true);

        // 6. A used nonce is refused on replay with 409.
        let (st, res, _) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "full",
                "typed": "FULL ACCESS org/widget",
                "confirm": nonce,
            }),
        )
        .await;
        assert_eq!(st, StatusCode::CONFLICT);
        assert_eq!(res["error"], "confirmation missing or expired");

        // 7. A messenger bridge never changes Full Access, as a `bridge=1` session or a Signal
        // bridge device: no nonce for it, no grant and no revoke.
        let code = auth::issue_login_code(&server.config.state_dir).unwrap();
        let login = Request::builder()
            .uri(format!("/login?code={code}&bridge=1"))
            .header(header::HOST, HOST)
            .body(Body::empty())
            .unwrap();
        let response = server::router(server.clone()).oneshot(login).await.unwrap();
        let bridge_cookie = response.headers()[header::SET_COOKIE].to_str().unwrap().split(';').next().unwrap().to_string();
        let (_, signal_token) = auth::add_device(&server.config.state_dir, "signal-bridge").unwrap();
        let signal_cookie = format!("{}={signal_token}", auth::DEVICE_COOKIE);
        for bridge in [&bridge_cookie, &signal_cookie] {
            let (st, res, _) = get_json(&server, bridge, &format!("/api/repos/state?thread={thread}&intent=access")).await;
            assert_eq!(st, StatusCode::FORBIDDEN, "{res}");
            assert!(res.get("confirm").is_none());
            for level in ["full", "workspace"] {
                let (st, res, _) = post_json(
                    &server,
                    bridge,
                    "/api/repos/access",
                    json!({ "thread": thread, "level": level, "typed": "FULL ACCESS org/widget", "confirm": nonce }),
                )
                .await;
                assert_eq!(st, StatusCode::FORBIDDEN, "{level}: {res}");
                assert_eq!(res["error"], "Full Access is changed only in the page, never through a messenger bridge");
            }
        }
        assert!(server.chat_repos.is_full_access_granted(&thread));

        // 8. `workspace` removes the grant with no nonce: ending it must work with the forge down.
        let (st, res, body) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({
                "thread": thread,
                "level": "workspace",
            }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(res["level"], "workspace");
        assert!(!server.chat_repos.is_full_access_granted(&thread));

        let (st, state_after, body) = get_json(
            &server,
            &cookie,
            &format!("/api/repos/state?thread={thread}"),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        assert_eq!(state_after["full_access"], false);

        // 9. The node's setting turned off drops the grant; turned on again, it stays dropped.
        let nonce = get_nonce(&server, &cookie, &thread, "access").await;
        let (st, _, body) = post_json(
            &server,
            &cookie,
            "/api/repos/access",
            json!({ "thread": thread, "level": "full", "typed": "FULL ACCESS org/widget", "confirm": nonce }),
        )
        .await;
        assert_eq!(st, StatusCode::OK, "{body}");
        let user_config = server.config.user_config.clone().unwrap();
        std::fs::write(&user_config, "[chat_repos]\nfull_access = false\n").unwrap();
        let (_, state_off, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(state_off["full_access"], false);
        std::fs::write(&user_config, "[chat_repos]\nfull_access = true\n").unwrap();
        let (_, state_on, _) = get_json(&server, &cookie, &format!("/api/repos/state?thread={thread}")).await;
        assert_eq!(state_on["full_access"], false, "an old grant does not come back");
    }
}
