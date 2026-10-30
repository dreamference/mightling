//! One browser tab: the desktop app's message interface over a WebSocket
//! (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.2).
//!
//! The page runs unchanged in both hosts. In the desktop app the preload exposes
//! `window.electronBridge.sendMessageFromView(message)` and re-dispatches what the main process
//! sends as window `MessageEvent`s; in a browser `/bridge.js` defines the same object over this
//! socket. Frames, as JSON:
//!
//! - page → server: `{"call": n, "message": <FromView>}`, one per `sendMessageFromView`;
//! - server → page: `{"answer": n, "result": …}` or `{"answer": n, "error": "…"}`, and
//!   `{"event": <ForView>}` for what the desktop app delivers on its for-view channel
//!   (`work://message`, `work://stderr`, `work://protocol-error`, `work://exit`).
//!
//! `work/start` opens this tab's own connection to the app-server; `work/send` vets one protocol
//! message through the policy and forwards it. The window commands are no-ops in a browser.

use std::collections::HashMap;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicI64;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering;
use std::time::Duration;

use axum::extract::ws::Message;
use axum::extract::ws::WebSocket;
use futures::SinkExt;
use futures::StreamExt;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_tungstenite::tungstenite::Message as UpstreamMessage;

use crate::ask;
use crate::chat_repos::ChatReposSettings;
use crate::policy::Incoming;
use crate::policy::classify;
use crate::policy::thread_place;
use crate::repo_limits::RepoLimits;
use crate::repo_limits::RepoVerdict;
use crate::repo_limits::RepoWatch;
use crate::server::Server;

/// Base of request ID range reserved for ling web internal requests (steer, interrupt).
pub const INTERNAL_REQUEST_ID_BASE: i64 = 9_000_000_000;

static NEXT_INTERNAL_ID: AtomicI64 = AtomicI64::new(INTERNAL_REQUEST_ID_BASE);
static NEXT_SHARED_TAB_ID: AtomicU64 = AtomicU64::new(1_000_000);

/// Generates a unique request ID in ling web's internal range.
pub fn next_internal_id() -> i64 {
    NEXT_INTERNAL_ID.fetch_add(1, Ordering::Relaxed)
}

/// Checks whether `value` is a response to an internal request issued on `shared`, removing the
/// request ID from `shared.internal_ids` on match.
pub fn take_internal_id(shared: &Shared, value: &Value) -> bool {
    if value.get("method").is_some() {
        return false;
    }
    let Some(id) = value.get("id").and_then(Value::as_i64) else {
        return false;
    };
    shared
        .internal_ids
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .remove(&id)
}

/// An active turn watch on a `chat-work` thread.
#[derive(Debug)]
pub struct ActiveTurnWatch {
    pub turn_id: String,
    pub thread_id: String,
    pub chat_key: String,
    pub repo: String,
    pub owner_tab: u64,
    pub watch: RepoWatch,
}

/// What a tab's connection shares with the task reading the app-server.
pub struct Shared {
    /// Identifier of this relay tab, used to own and hand over turn watches in `ChatRepos`.
    pub tab_id: u64,
    /// Server requests waiting for this tab's answer.
    pub pending: Mutex<HashSet<String>>,
    /// Internal request IDs (`turn/steer`, `turn/interrupt`) issued by this tab and awaiting a response.
    pub internal_ids: Mutex<HashSet<i64>>,
    /// Thread openers whose new thread works in an Ask folder, by request id: answered, the folder
    /// is named after the thread.
    pub openers: Mutex<HashMap<String, PathBuf>>,
    /// Thread openers for `chat-work` threads (and forks of `chat-work` threads) in a repository
    /// checkout, by request id: answered, the checkout's `app_threads` list is updated in
    /// `$CODEX_HOME/chat-repos/index.json`.
    pub repo_openers: Mutex<HashMap<String, PathBuf>>,
    /// The folder each thread works in, as the server named it (`thread_place`).
    pub places: Mutex<HashMap<String, PathBuf>>,
    /// Turn starters by request id, so failed turn/start responses can record a busy notice.
    pub turn_start_threads: Mutex<HashMap<String, String>>,
}

impl Default for Shared {
    fn default() -> Self {
        Self::for_tab(NEXT_SHARED_TAB_ID.fetch_add(1, Ordering::Relaxed))
    }
}

impl Shared {
    pub fn for_tab(tab_id: u64) -> Self {
        Self {
            tab_id,
            pending: Mutex::new(HashSet::new()),
            internal_ids: Mutex::new(HashSet::new()),
            openers: Mutex::new(HashMap::new()),
            repo_openers: Mutex::new(HashMap::new()),
            places: Mutex::new(HashMap::new()),
            turn_start_threads: Mutex::new(HashMap::new()),
        }
    }
}

struct Tab {
    id: u64,
    server: Arc<Server>,
    out: mpsc::UnboundedSender<Value>,
    upstream_tx: Option<mpsc::UnboundedSender<UpstreamMessage>>,
    upstream_writer: Option<JoinHandle<()>>,
    timer_task: Option<JoinHandle<()>>,
    reader: Option<JoinHandle<()>>,
    shared: Arc<Shared>,
    bridge: bool,
    watch: bool,
}

fn event(channel: &str, payload: Value) -> Value {
    json!({ "event": { "channel": channel, "payload": payload } })
}

pub async fn run_tab(socket: WebSocket, server: Arc<Server>) {
    run_tab_with_bridge(socket, server, None, false, false).await;
}

pub async fn run_tab_for_device(socket: WebSocket, server: Arc<Server>, device_name: Option<String>) {
    let bridge = device_name.as_deref().is_some_and(ask::is_bridge_device);
    run_tab_with_bridge(socket, server, None, bridge, false).await;
}

/// `device_token`: the cookie of the paired device the tab was opened with, looked up again for
/// every message, so a device revoked or narrowed to `watch` while its tab is open is held to it
/// at once, as its next HTTP request is.
pub async fn run_tab_with_bridge(
    socket: WebSocket,
    server: Arc<Server>,
    device_token: Option<String>,
    bridge: bool,
    watch: bool,
) {
    let (mut to_browser, mut from_browser) = socket.split();
    let (out, mut outgoing) = mpsc::unbounded_channel::<Value>();
    let writer = tokio::spawn(async move {
        while let Some(value) = outgoing.recv().await {
            if to_browser.send(Message::Text(value.to_string().into())).await.is_err() {
                break;
            }
        }
    });
    // The launcher's lines while a server this process started gets going.
    let mut stderr = server.app_server.stderr();
    let stderr_out = out.clone();
    let stderr_task = tokio::spawn(async move {
        loop {
            match stderr.recv().await {
                Ok(line) => {
                    let _ = stderr_out.send(event("work://stderr", Value::String(line)));
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => break,
            }
        }
    });
    let tab_id = server.new_tab_id();
    let mut tab = Tab {
        id: tab_id,
        server,
        out: out.clone(),
        upstream_tx: None,
        upstream_writer: None,
        timer_task: None,
        reader: None,
        shared: Arc::new(Shared::for_tab(tab_id)),
        bridge,
        watch,
    };
    while let Some(Ok(frame)) = from_browser.next().await {
        let text = match frame {
            Message::Text(text) => text,
            Message::Close(_) => break,
            _ => continue,
        };
        let Ok(call) = serde_json::from_str::<Value>(text.as_str()) else { continue };
        let id = call.get("call").cloned().unwrap_or(Value::Null);
        if let Some(token) = device_token.as_deref() {
            let Some(device) = crate::auth::device_for_token(&tab.server.config.state_dir, token) else {
                let _ = out.send(json!({ "answer": id, "error": "This device's pairing was revoked." }));
                break;
            };
            tab.watch = device.scope == crate::auth::Scope::Watch;
        }
        let answer = match tab.handle(call.get("message").cloned().unwrap_or(Value::Null)).await {
            Ok(result) => json!({ "answer": id, "result": result }),
            Err(error) => json!({ "answer": id, "error": error }),
        };
        let _ = out.send(answer);
    }
    tab.close(false).await;
    stderr_task.abort();
    // The writer ends when the last sender is gone, and the tab holds one.
    drop(tab);
    drop(out);
    let _ = writer.await;
}

impl Tab {
    async fn handle(&mut self, message: Value) -> Result<Value, String> {
        let kind = message.get("type").and_then(Value::as_str).unwrap_or_default().to_string();
        match kind.as_str() {
            "work/start" => self.start().await,
            "work/send" => self.send(message.get("message").cloned().unwrap_or(Value::Null)).await.map(|()| Value::Null),
            "work/stop" => {
                self.close(true).await;
                Ok(Value::Null)
            }
            "work/airgapped" => {
                let thread = message.get("thread").and_then(Value::as_str).filter(|id| ask::valid_thread_id(id));
                let resolved = ling_airgapped::resolve(&[thread.unwrap_or_default()]);
                Ok(json!({ "level": resolved.level.name(), "source": resolved.source.label() }))
            }
            // A browser has no chat window to open, no launch target, and its own window controls.
            "work/open-chat" | "work/target" | "context-menu" | "window/minimize" | "window/maximize" | "window/close" => {
                Ok(Value::Null)
            }
            other => Err(format!("the Mightling UI does not send {other}")),
        }
    }

    /// Forgets a connection the server closed, so the next `work/start` opens a new one.
    fn drop_finished(&mut self) {
        if self.reader.as_ref().is_some_and(JoinHandle::is_finished) {
            if let Some(task) = self.timer_task.take() {
                task.abort();
            }
            if let Some(task) = self.upstream_writer.take() {
                task.abort();
            }
            self.upstream_tx = None;
            self.reader = None;
        }
    }

    async fn start(&mut self) -> Result<Value, String> {
        self.drop_finished();
        if self.upstream_tx.is_some() {
            return Ok(json!({ "served_model": self.server.served_model(), "started": false, "ask_root": self.ask_root() }));
        }
        let (upstream, started) = self.server.app_server.connect().await?;
        let (mut sink, mut stream) = upstream.split();
        let (upstream_tx, mut upstream_rx) = mpsc::unbounded_channel::<UpstreamMessage>();
        let upstream_writer = tokio::spawn(async move {
            while let Some(msg) = upstream_rx.recv().await {
                if sink.send(msg).await.is_err() {
                    break;
                }
            }
        });
        self.upstream_tx = Some(upstream_tx.clone());
        self.upstream_writer = Some(upstream_writer);

        let (server_timer, shared_timer, tx_timer) = (self.server.clone(), self.shared.clone(), upstream_tx.clone());
        let timer_task = tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(1));
            loop {
                interval.tick().await;
                check_repo_watches(&server_timer, &shared_timer, &tx_timer);
            }
        });
        self.timer_task = Some(timer_task);

        let (server, shared, out, tab, tx_deliver) = (self.server.clone(), self.shared.clone(), self.out.clone(), self.id, upstream_tx);
        self.reader = Some(tokio::spawn(async move {
            while let Some(Ok(frame)) = stream.next().await {
                let text = match frame {
                    UpstreamMessage::Text(text) => text.as_str().to_string(),
                    UpstreamMessage::Close(_) => break,
                    _ => continue,
                };
                deliver(&server, &shared, &out, tab, &text, &tx_deliver);
            }
            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).clear();
            shared.internal_ids.lock().unwrap_or_else(|p| p.into_inner()).clear();
            server.forget_tab(tab);
            let _ = out.send(event("work://exit", Value::Null));
        }));
        Ok(json!({ "served_model": self.server.served_model(), "started": started, "ask_root": self.ask_root() }))
    }

    /// Where Ask threads' folders are, as the server will name their `cwd` (canonical), so the UI
    /// can tell Ask threads from Work's projects.
    fn ask_root(&self) -> String {
        let root = self.server.ask_root();
        let _ = crate::auth::private_dir(&root);
        root.canonicalize().unwrap_or(root).to_string_lossy().into_owned()
    }

    async fn send(&mut self, message: Value) -> Result<(), String> {
        self.drop_finished();
        let Some(upstream_tx) = self.upstream_tx.as_ref() else {
            return Err("the agent's server is not running".to_string());
        };
        let message = watch_message(self.watch, message)?;
        let policy = &self.server.policy;
        // A named prompt's text is read before vetting: composing it may take a moment.
        let named = message
            .get("method")
            .and_then(Value::as_str)
            .filter(|method| policy.prompt_openers.contains(*method))
            .and_then(|_| message.get("params")?.get(&policy.prompt_field)?.as_str())
            .filter(|name| *name != "default" && policy.named_prompts.contains(*name))
            .map(str::to_string);
        let ask_root = self.server.ask_root();
        let compose_cwd = message
            .pointer("/params/cwd")
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .filter(|cwd| crate::chat_repos::repo_checkout(&self.server.config.codex_home, cwd).is_some())
            .unwrap_or_else(|| ask_root.clone());
        let is_repo_prompt = named.as_ref().is_some_and(|name| policy.repo_prompts.contains(name));
        let composed = match named {
            Some(name) => {
                let text = crate::prompts::composed(&self.server.config.launch, &name, &compose_cwd).await?;
                Some((name, text))
            }
            None => None,
        };
        // The configured level, as `ling airgapped` prints it: installs and plugins follow it.
        let airgapped_on = ling_airgapped::resolve(&[]).level == ling_airgapped::Level::On;
        let thread_folder = message
            .pointer("/params/threadId")
            .and_then(Value::as_str)
            .and_then(|thread| self.shared.places.lock().unwrap_or_else(|p| p.into_inner()).get(thread).cloned());
        let prompts = ask::MessagePrompts {
            root: &ask_root,
            composed,
            created: Mutex::new(None),
            airgapped_on,
            thread_folder,
        }
        .with_bridge(self.bridge)
        .with_server(&self.server);
        let vetted = {
            let mut pending = self.shared.pending.lock().unwrap_or_else(|p| p.into_inner());
            policy.vet_outgoing(&message, &mut pending, &prompts)?
        };
        if vetted.get("method").is_none()
            && let Some(id) = vetted.get("id")
        {
            self.server.chat_repos.resolve_pending_approval(&id.to_string());
        }
        check_bridge_repo_turn(&self.server, self.bridge, &vetted)?;
        if let Some(thread_id) = vetted.pointer("/params/threadId").and_then(Value::as_str) {
            self.server.chat_repos.note_tab_thread(self.shared.tab_id, thread_id);
        }
        // A thread that will work in an Ask folder: once the server names it, so is the folder.
        let method = vetted.get("method").and_then(Value::as_str);
        let opener = method.is_some_and(|m| policy.thread_openers.contains(m));
        let is_repo_fork = method == Some("thread/fork")
            && vetted
                .pointer("/params/threadId")
                .and_then(Value::as_str)
                .is_some_and(|id| crate::chat_repos::repo_thread_cwd(&self.server.config.codex_home, id).is_some());
        if opener
            && let (Some(id), Some(cwd)) = (vetted.get("id"), vetted.get("params").and_then(|p| p.get("cwd")).and_then(Value::as_str))
        {
            if ask::is_folder(&ask_root, std::path::Path::new(cwd)) {
                self.shared.openers.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), PathBuf::from(cwd));
            } else if is_repo_prompt || is_repo_fork {
                self.shared.repo_openers.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), PathBuf::from(cwd));
            }
        }
        if method == Some("turn/start") {
            if let Some(thread_id) = vetted.pointer("/params/threadId").and_then(Value::as_str) {
                if let Some(record) = self.server.chat_repos.find_by_app_thread(&self.server.config.codex_home, thread_id) {
                    let settings = ChatReposSettings::load_from(self.server.config.user_config.as_deref());
                    if let Err(err) = self.server.chat_repos.check_budget_before_turn(&record, settings.tokens_per_day) {
                        return Err(err);
                    }
                }
                if let Some(id) = vetted.get("id") {
                    self.shared.turn_start_threads.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), thread_id.to_string());
                }
            }
        }
        upstream_tx
            .send(UpstreamMessage::Text(vetted.to_string().into()))
            .map_err(|err| format!("could not reach the agent's server: {err}"))
    }

    /// Closes this tab's connection; the server itself goes on for other tabs and other clients.
    async fn close(&mut self, announce: bool) {
        if let Some(tx) = self.upstream_tx.take() {
            let _ = tx.send(UpstreamMessage::Close(None));
        }
        if let Some(task) = self.timer_task.take() {
            task.abort();
        }
        if let Some(task) = self.upstream_writer.take() {
            task.abort();
        }
        if let Some(reader) = self.reader.take() {
            reader.abort();
            let _ = reader.await;
            self.shared.pending.lock().unwrap_or_else(|p| p.into_inner()).clear();
            self.shared.internal_ids.lock().unwrap_or_else(|p| p.into_inner()).clear();
            self.server.forget_tab(self.id);
            if announce {
                let _ = self.out.send(event("work://exit", Value::Null));
            }
        }
    }
}

/// The requests that put input into an existing thread, each naming it in `params.threadId`.
const BRIDGE_REPO_INPUT_REQUESTS: [&str; 8] = [
    "turn/start",
    "turn/steer",
    "thread/queue/add",
    "thread/queue/update",
    "thread/queue/start",
    "thread/goal/set",
    "thread/shellCommand",
    "review/start",
];

/// What a device with the `watch` scope may send: reading and following threads, nothing that
/// starts, steers, answers or changes anything (SYNC §5.1, O4). Files, git and search outside a
/// thread are not reading a thread either.
const WATCH_REQUESTS: &[&str] = &[
    "initialize",
    "account/read",
    "config/read",
    "configRequirements/read",
    "model/list",
    "permissionProfile/list",
    "collaborationMode/list",
    "project/list",
    "project/read",
    "thread/resume",
    "thread/list",
    "thread/read",
    "thread/loaded/list",
    "thread/turns/list",
    "thread/items/list",
    "thread/search",
    "thread/unsubscribe",
    "thread/queue/list",
    "thread/goal/get",
    "memory/status",
    "skills/list",
    "plugin/list",
    "plugin/read",
    "plugin/installed",
    "hooks/list",
    "mcpServerStatus/list",
    "app/list",
];

/// A `watch` device's message, refused unless it only reads or follows: an answer to the
/// server's request (an approval) is refused, and `thread/resume` keeps only the thread it
/// follows, so it cannot change the thread's model, folder or sandbox on the way.
pub fn watch_message(watch: bool, message: Value) -> Result<Value, String> {
    if !watch {
        return Ok(message);
    }
    let refused = || "This device may watch threads only; ask for control in Settings › Devices on the Mightling machine.".to_string();
    let method = message.get("method").and_then(Value::as_str);
    let request = message.get("id").is_some();
    match method {
        Some("initialized") if !request => Ok(message),
        Some("thread/resume") if request => {
            let thread = message.pointer("/params/threadId").cloned().ok_or_else(refused)?;
            Ok(json!({ "id": message["id"], "method": "thread/resume", "params": { "threadId": thread } }))
        }
        Some(method) if request && WATCH_REQUESTS.contains(&method) => Ok(message),
        _ => Err(refused()),
    }
}

/// Defense in depth: refuses a `bridge=1` session's request that puts input into a repository
/// thread (a turn, a steer, a queued message, a goal, a shell command or a review) when the
/// thread's record's `bridge` is `telegram` or `discord` (CHAT_REPOS §2, §5.3).
pub fn check_bridge_repo_turn(server: &Server, bridge: bool, vetted: &Value) -> Result<(), String> {
    if !bridge {
        return Ok(());
    }
    let method = vetted.get("method").and_then(Value::as_str);
    if !method.is_some_and(|m| BRIDGE_REPO_INPUT_REQUESTS.contains(&m)) {
        return Ok(());
    }
    let Some(thread_id) = vetted.pointer("/params/threadId").and_then(Value::as_str) else {
        return Ok(());
    };
    if let Some(record) = server
        .chat_repos
        .find_by_app_thread(&server.config.codex_home, thread_id)
        && matches!(record.bridge.as_str(), "telegram" | "discord")
    {
        return Err("Repo work happens in the app.".to_string());
    }
    Ok(())
}

fn send_steer(
    shared: &Shared,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
    thread_id: &str,
    turn_id: &str,
    text: &str,
) {
    let id = next_internal_id();
    shared.internal_ids.lock().unwrap_or_else(|p| p.into_inner()).insert(id);
    let msg = json!({
        "id": id,
        "method": "turn/steer",
        "params": {
            "threadId": thread_id,
            "expectedTurnId": turn_id,
            "input": [
                {
                    "type": "text",
                    "text": text,
                    "text_elements": []
                }
            ]
        }
    });
    let _ = upstream_tx.send(UpstreamMessage::Text(msg.to_string().into()));
}

fn send_interrupt(
    shared: &Shared,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
    thread_id: &str,
    turn_id: &str,
) {
    let id = next_internal_id();
    shared.internal_ids.lock().unwrap_or_else(|p| p.into_inner()).insert(id);
    let msg = json!({
        "id": id,
        "method": "turn/interrupt",
        "params": {
            "threadId": thread_id,
            "turnId": turn_id
        }
    });
    let _ = upstream_tx.send(UpstreamMessage::Text(msg.to_string().into()));
}

/// Evaluates live turn watches owned by `shared.tab_id` and issues `turn/steer` or `turn/interrupt`
/// to the app-server as needed.
pub fn check_repo_watches(
    server: &Server,
    shared: &Shared,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
) {
    let actions = server.chat_repos.check_watches_for_tab(shared.tab_id);
    for (thread_id, turn_id, verdict) in actions {
        match verdict {
            RepoVerdict::Steer(text) => {
                send_steer(shared, upstream_tx, &thread_id, &turn_id, &text);
            }
            RepoVerdict::Interrupt(_) => {
                send_interrupt(shared, upstream_tx, &thread_id, &turn_id);
            }
        }
    }
}

/// Handles item, turn, and token-usage notifications for repository bounds and daily budgets.
pub fn handle_notification_repo_limits(
    server: &Server,
    shared: &Shared,
    method: &str,
    params: Option<&Value>,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
) {
    let Some(params) = params else { return };
    if let Some(thread_id) = params.get("threadId").and_then(Value::as_str) {
        server.chat_repos.note_tab_thread(shared.tab_id, thread_id);
    }
    match method {
        "turn/started" => {
            if let (Some(thread_id), Some(turn_id)) = (
                params.get("threadId").and_then(Value::as_str),
                params.pointer("/turn/id").and_then(Value::as_str),
            ) {
                if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                    let settings = ChatReposSettings::load_from(server.config.user_config.as_deref());
                    let limits = RepoLimits::from_settings(&settings);
                    let now_ms = server.chat_repos.now_ms();
                    let watch = RepoWatch::new(now_ms, limits);
                    if server.chat_repos.register_turn_watch(
                        &server.config.codex_home,
                        &record,
                        turn_id,
                        thread_id,
                        shared.tab_id,
                        watch,
                    ) {
                        check_repo_watches(server, shared, upstream_tx);
                    }
                }
            }
        }
        "item/started" => {
            let thread_id = params.get("threadId").and_then(Value::as_str);
            let turn_id = params.get("turnId").and_then(Value::as_str);
            let kind = params.pointer("/item/type").and_then(Value::as_str).unwrap_or_default();
            let item_id = params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default();
            if server.chat_repos.on_item_started_for_tab(shared.tab_id, thread_id, turn_id, kind, item_id) {
                check_repo_watches(server, shared, upstream_tx);
            }
        }
        "item/completed" => {
            let thread_id = params.get("threadId").and_then(Value::as_str);
            let turn_id = params.get("turnId").and_then(Value::as_str);
            let item_id = params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default();
            if server.chat_repos.on_item_completed_for_tab(shared.tab_id, thread_id, turn_id, item_id) {
                check_repo_watches(server, shared, upstream_tx);
            }
        }
        "thread/tokenUsage/updated" => {
            let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
            if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                let cumulative = params
                    .pointer("/tokenUsage/total/totalTokens")
                    .or_else(|| params.pointer("/total/totalTokens"))
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                let settings = ChatReposSettings::load_from(server.config.user_config.as_deref());
                if let Some((t_id, turn_id, text)) = server.chat_repos.on_token_usage_for_tab(
                    shared.tab_id,
                    &record,
                    thread_id,
                    settings.tokens_per_day,
                    cumulative,
                ) {
                    send_steer(shared, upstream_tx, &t_id, &turn_id, &text);
                }
                check_repo_watches(server, shared, upstream_tx);
            }
        }
        "turn/completed" => {
            let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
            let turn_id = params.pointer("/turn/id").and_then(Value::as_str);
            server.chat_repos.remove_turn_watch(turn_id, thread_id);
            server.chat_repos.clear_pending_approvals_for_thread(thread_id);
            let err_msg = params.pointer("/turn/error/message").and_then(Value::as_str).unwrap_or("");
            let details = params.pointer("/turn/error/additionalDetails").and_then(Value::as_str).unwrap_or("");
            let err_str = params.pointer("/turn/error").and_then(Value::as_str).unwrap_or("");
            let is_busy = err_msg.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK)
                || details.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK)
                || err_str.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK);
            if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                let dedup_key = turn_id.unwrap_or("turn");
                if is_busy {
                    server.chat_repos.record_event_for_record(
                        &record,
                        Some(thread_id),
                        Some(dedup_key),
                        crate::chat_repos::RepoEventKind::Busy,
                    );
                } else {
                    let status = params
                        .pointer("/turn/status")
                        .and_then(Value::as_str)
                        .unwrap_or("completed");
                    match status {
                        "interrupted" => {
                            server.chat_repos.record_event_for_record(
                                &record,
                                Some(thread_id),
                                Some(dedup_key),
                                crate::chat_repos::RepoEventKind::TurnStopped,
                            );
                        }
                        "failed" => {
                            server.chat_repos.record_event_for_record(
                                &record,
                                Some(thread_id),
                                Some(dedup_key),
                                crate::chat_repos::RepoEventKind::TurnFailed,
                            );
                        }
                        "completed" => {
                            if !server
                                .chat_repos
                                .has_event_dedup(thread_id, dedup_key, "turn_finished")
                            {
                                let files = crate::chat_repos::count_porcelain_lines_sync(
                                    server,
                                    std::path::Path::new(&record.dir),
                                );
                                server.chat_repos.record_event_for_record(
                                    &record,
                                    Some(thread_id),
                                    Some(dedup_key),
                                    crate::chat_repos::RepoEventKind::TurnFinished { files },
                                );
                            }
                        }
                        _ => {}
                    }
                }
            }
        }
        "error" => {
            let err_msg = params
                .pointer("/error/message")
                .and_then(Value::as_str)
                .or_else(|| params.get("message").and_then(Value::as_str))
                .unwrap_or("");
            if err_msg.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK) {
                let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
                if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                    let turn_id = params.get("turnId").and_then(Value::as_str).unwrap_or("error");
                    server.chat_repos.record_event_for_record(
                        &record,
                        Some(thread_id),
                        Some(turn_id),
                        crate::chat_repos::RepoEventKind::Busy,
                    );
                }
            }
        }
        "thread/closed" => {
            let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
            server.chat_repos.remove_thread_watches(thread_id);
            server.chat_repos.clear_pending_approvals_for_thread(thread_id);
        }
        _ => {}
    }
}

/// One message from the app-server, on its way to the page.
pub fn deliver(
    server: &Server,
    shared: &Shared,
    out: &mpsc::UnboundedSender<Value>,
    tab: u64,
    text: &str,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
) {
    if text.trim().is_empty() {
        return;
    }
    let (kind, value) = classify(text);
    let Some(value) = value.filter(|_| kind != Incoming::NotProtocol) else {
        let _ = out.send(event("work://protocol-error", Value::String(text.to_string())));
        return;
    };
    if take_internal_id(shared, &value) {
        return;
    }
    if let Some((thread, cwd)) = thread_place(&value) {
        shared.places.lock().unwrap_or_else(|p| p.into_inner()).insert(thread.clone(), PathBuf::from(&cwd));
        server.chat_repos.record_thread_place(&thread, PathBuf::from(cwd));
        server.chat_repos.note_tab_thread(shared.tab_id, &thread);
    }
    match kind {
        Incoming::ServerRequest { key, method } => {
            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).insert(key.clone());
            if matches!(
                method.as_str(),
                "item/commandExecution/requestApproval"
                    | "item/fileChange/requestApproval"
                    | "item/permissions/requestApproval"
            ) && let Some(thread_id) = value.pointer("/params/threadId").and_then(Value::as_str)
                && let Some(record) = server
                    .chat_repos
                    .find_by_app_thread(&server.config.codex_home, thread_id)
            {
                server
                    .chat_repos
                    .record_approval_waiting(&record, thread_id, &key);
            }
        }
        Incoming::Notification { method } => {
            server.observe_busy(tab, &method, value.get("params"));
            handle_notification_repo_limits(server, shared, &method, value.get("params"), upstream_tx);
        }
        Incoming::Response => {
            let key = value.get("id").map(Value::to_string).unwrap_or_default();
            let folder = shared.openers.lock().unwrap_or_else(|p| p.into_inner()).remove(&key);
            if let Some(folder) = folder
                && let Some(thread) = value.pointer("/result/thread/id").and_then(Value::as_str)
            {
                let _ = ask::link_thread(&server.ask_root(), thread, &folder);
            }
            let repo_folder = shared
                .repo_openers
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .remove(&key)
                .or_else(|| {
                    value
                        .pointer("/result/thread/parentThreadId")
                        .and_then(Value::as_str)
                        .and_then(|parent| crate::chat_repos::repo_thread_cwd(&server.config.codex_home, parent))
                        .map(PathBuf::from)
                });
            if let Some(folder) = repo_folder {
                if let Some((thread, _)) = thread_place(&value) {
                    let _ = server.chat_repos.record_app_thread(&server.config.codex_home, &folder, &thread);
                } else if let Some(thread) = value.pointer("/result/thread/id").and_then(Value::as_str) {
                    let _ = server.chat_repos.record_app_thread(&server.config.codex_home, &folder, thread);
                }
            }
            let turn_thread = shared.turn_start_threads.lock().unwrap_or_else(|p| p.into_inner()).remove(&key);
            if let Some(err_msg) = value.pointer("/error/message").and_then(Value::as_str) {
                if err_msg.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK) {
                    if let Some(thread_id) = turn_thread {
                        if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, &thread_id) {
                            server.chat_repos.record_event_for_record(
                                &record,
                                Some(&thread_id),
                                Some(&key),
                                crate::chat_repos::RepoEventKind::Busy,
                            );
                        }
                    }
                }
            }
        }
        Incoming::NotProtocol => {}
    }
    let _ = out.send(event("work://message", value));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_ids_match_only_issued_set_and_do_not_drop_client_timestamps() {
        let shared = Shared::default();
        // A client response with a timestamp ID >= 9e9 or "lw-internal-..." that was NOT issued by
        // this relay must not be dropped.
        assert!(!take_internal_id(&shared, &json!({ "id": 9_500_000_000i64, "result": {} })));
        assert!(!take_internal_id(&shared, &json!({ "id": "9500000000", "result": {} })));
        assert!(!take_internal_id(&shared, &json!({ "id": "lw-internal-1", "result": {} })));

        // An ID issued via send_steer is recorded in `shared.internal_ids`, matched once, and removed.
        let (tx, mut rx) = mpsc::unbounded_channel();
        send_steer(&shared, &tx, "t1", "turn-1", "stop");
        let sent = rx.try_recv().unwrap();
        let UpstreamMessage::Text(text) = sent else { panic!("expected text") };
        let parsed: Value = serde_json::from_str(text.as_str()).unwrap();
        let issued = parsed["id"].as_i64().unwrap();
        assert!(issued >= INTERNAL_REQUEST_ID_BASE);
        assert!(take_internal_id(&shared, &json!({ "id": issued, "result": {} })));
        // Once removed, a second response with the same ID is no longer in the set.
        assert!(!take_internal_id(&shared, &json!({ "id": issued, "result": {} })));
    }

    #[test]
    fn a_server_request_carrying_an_issued_id_is_not_taken_for_its_answer() {
        let shared = Shared::default();
        let (tx, mut rx) = mpsc::unbounded_channel();
        send_steer(&shared, &tx, "t1", "turn-1", "stop");
        let UpstreamMessage::Text(text) = rx.try_recv().unwrap() else { panic!("expected text") };
        let issued = serde_json::from_str::<Value>(text.as_str()).unwrap()["id"].as_i64().unwrap();
        // The server numbers its own requests: one with the same id is a request, not the answer.
        let request = json!({ "id": issued, "method": "item/commandExecution/requestApproval", "params": {} });
        assert!(!take_internal_id(&shared, &request));
        // The id stays issued until the answer comes.
        assert!(take_internal_id(&shared, &json!({ "id": issued, "result": {} })));
    }

    #[test]
    fn a_watch_device_only_reads_and_follows() {
        let watch = |message: Value| watch_message(true, message);
        // The handshake's notification passes; `initialized` sent as a request does not.
        assert!(watch(json!({ "method": "initialized" })).is_ok());
        assert!(watch(json!({ "id": 1, "method": "initialized" })).is_err());
        // Every read is a request; the same method as a notification is refused.
        for method in ["thread/read", "thread/list", "thread/turns/list", "config/read", "model/list"] {
            assert!(watch(json!({ "id": 1, "method": method, "params": { "threadId": "t1" } })).is_ok(), "{method}");
            assert!(watch(json!({ "method": method, "params": { "threadId": "t1" } })).is_err(), "{method} as a notification");
        }
        for method in ["turn/start", "turn/steer", "turn/interrupt", "thread/start", "thread/shellCommand", "thread/queue/add"] {
            assert!(watch(json!({ "id": 1, "method": method, "params": { "threadId": "t1" } })).is_err(), "{method}");
        }
        // An answer to the server's request (an approval) is refused.
        assert!(watch(json!({ "id": 0, "result": { "decision": "accept" } })).is_err());
        // `thread/resume` keeps only the thread it follows, and needs one.
        let resumed = watch(json!({ "id": 2, "method": "thread/resume", "params": { "threadId": "t1", "cwd": "/", "sandbox": "danger-full-access" } }));
        assert_eq!(resumed, Ok(json!({ "id": 2, "method": "thread/resume", "params": { "threadId": "t1" } })));
        assert!(watch(json!({ "id": 2, "method": "thread/resume", "params": { "cwd": "/" } })).is_err());
        assert!(watch(json!({ "method": "thread/resume", "params": { "threadId": "t1" } })).is_err());
        // A device with control passes everything on unchanged.
        let turn = json!({ "id": 3, "method": "turn/start", "params": { "threadId": "t1" } });
        assert_eq!(watch_message(false, turn.clone()), Ok(turn));
    }
}
