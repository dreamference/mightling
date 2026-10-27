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

/// Generates a unique request ID in ling web's internal range.
pub fn next_internal_id() -> i64 {
    NEXT_INTERNAL_ID.fetch_add(1, Ordering::Relaxed)
}

/// Whether `value` is a response to one of ling web's own internal requests.
pub fn is_internal_id(value: &Value) -> bool {
    let id_val = value.get("id");
    if let Some(n) = id_val.and_then(Value::as_i64) {
        if n >= INTERNAL_REQUEST_ID_BASE {
            return true;
        }
    }
    if let Some(s) = id_val.and_then(Value::as_str) {
        if let Ok(n) = s.parse::<i64>() {
            if n >= INTERNAL_REQUEST_ID_BASE {
                return true;
            }
        }
        if s.starts_with("lw-internal-") {
            return true;
        }
    }
    false
}

/// An active turn watch on a `chat-work` thread.
#[derive(Debug)]
pub struct ActiveTurnWatch {
    pub turn_id: String,
    pub thread_id: String,
    pub chat_key: String,
    pub repo: String,
    pub watch: RepoWatch,
}

/// What a tab's connection shares with the task reading the app-server.
#[derive(Default)]
pub struct Shared {
    /// Server requests waiting for this tab's answer.
    pub pending: Mutex<HashSet<String>>,
    /// Thread openers whose new thread works in an Ask folder, by request id: answered, the folder
    /// is named after the thread.
    pub openers: Mutex<HashMap<String, PathBuf>>,
    /// Thread openers for `chat-work` threads in a repository checkout, by request id: answered,
    /// the checkout's `app_thread` is recorded in `$CODEX_HOME/chat-repos/index.json`.
    pub repo_openers: Mutex<HashMap<String, PathBuf>>,
    /// The folder each thread works in, as the server named it (`thread_place`).
    pub places: Mutex<HashMap<String, PathBuf>>,
    /// Active turn watches on chat-work threads for bounds and budget enforcement.
    pub repo_watches: Mutex<HashMap<String, ActiveTurnWatch>>,
    /// Turn starters by request id, so failed turn/start responses can record a busy notice.
    pub turn_start_threads: Mutex<HashMap<String, String>>,
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
}

fn event(channel: &str, payload: Value) -> Value {
    json!({ "event": { "channel": channel, "payload": payload } })
}

pub async fn run_tab(socket: WebSocket, server: Arc<Server>) {
    run_tab_for_device(socket, server, None).await;
}

pub async fn run_tab_for_device(socket: WebSocket, server: Arc<Server>, device_name: Option<String>) {
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
    let bridge = device_name.as_deref().is_some_and(ask::is_bridge_device);
    let mut tab = Tab {
        id: server.new_tab_id(),
        server,
        out: out.clone(),
        upstream_tx: None,
        upstream_writer: None,
        timer_task: None,
        reader: None,
        shared: Arc::default(),
        bridge,
    };
    while let Some(Ok(frame)) = from_browser.next().await {
        let text = match frame {
            Message::Text(text) => text,
            Message::Close(_) => break,
            _ => continue,
        };
        let Ok(call) = serde_json::from_str::<Value>(text.as_str()) else { continue };
        let id = call.get("call").cloned().unwrap_or(Value::Null);
        let answer = match tab.handle(call.get("message").cloned().unwrap_or(Value::Null)).await {
            Ok(result) => json!({ "answer": id, "result": result }),
            Err(error) => json!({ "answer": id, "error": error }),
        };
        let _ = out.send(answer);
    }
    tab.close(false).await;
    stderr_task.abort();
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
            shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner()).clear();
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
            .filter(|cwd| crate::chat_repos::is_repo_checkout(&self.server.config.codex_home, cwd))
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
        .with_bridge(self.bridge);
        let vetted = {
            let mut pending = self.shared.pending.lock().unwrap_or_else(|p| p.into_inner());
            policy.vet_outgoing(&message, &mut pending, &prompts)?
        };
        // A thread that will work in an Ask folder: once the server names it, so is the folder.
        let opener = vetted.get("method").and_then(Value::as_str).is_some_and(|method| policy.thread_openers.contains(method));
        if opener
            && let (Some(id), Some(cwd)) = (vetted.get("id"), vetted.get("params").and_then(|p| p.get("cwd")).and_then(Value::as_str))
        {
            if ask::is_folder(&ask_root, std::path::Path::new(cwd)) {
                self.shared.openers.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), PathBuf::from(cwd));
            } else if is_repo_prompt {
                self.shared.repo_openers.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), PathBuf::from(cwd));
            }
        }
        if vetted.get("method").and_then(Value::as_str) == Some("turn/start") {
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
            self.shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner()).clear();
            self.server.forget_tab(self.id);
            if announce {
                let _ = self.out.send(event("work://exit", Value::Null));
            }
        }
    }
}

fn send_steer(upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>, thread_id: &str, turn_id: &str, text: &str) {
    let id = next_internal_id();
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

fn send_interrupt(upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>, thread_id: &str, turn_id: &str) {
    let id = next_internal_id();
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

/// Evaluates live turn watches and issues `turn/steer` or `turn/interrupt` to the app-server as needed.
pub fn check_repo_watches(
    server: &Server,
    shared: &Shared,
    upstream_tx: &mpsc::UnboundedSender<UpstreamMessage>,
) {
    let now_ms = server.chat_repos.now_ms();
    let mut actions = Vec::new();
    {
        let mut watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
        if watches.is_empty() {
            return;
        }
        for entry in watches.values_mut() {
            if let Some(verdict) = entry.watch.check(now_ms) {
                actions.push((entry.thread_id.clone(), entry.turn_id.clone(), verdict));
            }
        }
    }
    for (thread_id, turn_id, verdict) in actions {
        match verdict {
            RepoVerdict::Steer(text) => {
                send_steer(upstream_tx, &thread_id, &turn_id, &text);
            }
            RepoVerdict::Interrupt(_) => {
                send_interrupt(upstream_tx, &thread_id, &turn_id);
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
    match method {
        "turn/started" => {
            if let (Some(thread_id), Some(turn_id)) = (
                params.get("threadId").and_then(Value::as_str),
                params.pointer("/turn/id").and_then(Value::as_str),
            ) {
                if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                    server.chat_repos.note_turn_started(&server.config.codex_home, &record);
                    let settings = ChatReposSettings::load_from(server.config.user_config.as_deref());
                    let limits = RepoLimits::from_settings(&settings);
                    let now_ms = server.chat_repos.now_ms();
                    let watch = RepoWatch::new(now_ms, limits);
                    shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner()).insert(
                        thread_id.to_string(),
                        ActiveTurnWatch {
                            turn_id: turn_id.to_string(),
                            thread_id: thread_id.to_string(),
                            chat_key: record.chat_key(),
                            repo: record.repo.clone(),
                            watch,
                        },
                    );
                    check_repo_watches(server, shared, upstream_tx);
                }
            }
        }
        "item/started" => {
            let thread_id = params
                .get("threadId")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| {
                    let watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
                    if watches.len() == 1 {
                        watches.keys().next().cloned().unwrap_or_default()
                    } else {
                        String::new()
                    }
                });
            let kind = params.pointer("/item/type").and_then(Value::as_str).unwrap_or_default();
            let item_id = params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default();
            let now_ms = server.chat_repos.now_ms();
            let has_watch = {
                let mut watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
                if let Some(entry) = watches.get_mut(&thread_id) {
                    if crate::repo_limits::is_tool(kind) {
                        entry.watch.on_tool_started();
                    }
                    if kind == "commandExecution" {
                        entry.watch.on_command_started_at(item_id, now_ms);
                    }
                    true
                } else {
                    false
                }
            };
            if has_watch {
                check_repo_watches(server, shared, upstream_tx);
            }
        }
        "item/completed" => {
            let thread_id = params
                .get("threadId")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| {
                    let watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
                    if watches.len() == 1 {
                        watches.keys().next().cloned().unwrap_or_default()
                    } else {
                        String::new()
                    }
                });
            let item_id = params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default();
            let has_watch = {
                let mut watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
                if let Some(entry) = watches.get_mut(&thread_id) {
                    entry.watch.on_command_finished(item_id);
                    true
                } else {
                    false
                }
            };
            if has_watch {
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
                let exhausted = server.chat_repos.add_token_usage(&record, settings.tokens_per_day, cumulative);
                if exhausted {
                    let now_ms = server.chat_repos.now_ms();
                    let mut steer_action = None;
                    {
                        let mut watches = shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner());
                        if let Some(entry) = watches.get_mut(thread_id) {
                            if let Some(RepoVerdict::Steer(text)) = entry.watch.steer_for_budget(now_ms) {
                                steer_action = Some((entry.thread_id.clone(), entry.turn_id.clone(), text));
                            }
                        }
                    }
                    if let Some((t_id, turn_id, text)) = steer_action {
                        send_steer(upstream_tx, &t_id, &turn_id, &text);
                    }
                }
                check_repo_watches(server, shared, upstream_tx);
            }
        }
        "turn/completed" => {
            let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
            shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner()).remove(thread_id);
            let err_msg = params.pointer("/turn/error/message").and_then(Value::as_str).unwrap_or("");
            let details = params.pointer("/turn/error/additionalDetails").and_then(Value::as_str).unwrap_or("");
            let err_str = params.pointer("/turn/error").and_then(Value::as_str).unwrap_or("");
            if err_msg.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK)
                || details.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK)
                || err_str.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK)
            {
                if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, thread_id) {
                    server.chat_repos.record_notice(&record.chat_key(), &record.repo, "busy");
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
                    server.chat_repos.record_notice(&record.chat_key(), &record.repo, "busy");
                }
            }
        }
        "thread/closed" => {
            let thread_id = params.get("threadId").and_then(Value::as_str).unwrap_or_default();
            shared.repo_watches.lock().unwrap_or_else(|p| p.into_inner()).remove(thread_id);
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
    if is_internal_id(&value) {
        return;
    }
    if let Some((thread, cwd)) = thread_place(&value) {
        shared.places.lock().unwrap_or_else(|p| p.into_inner()).insert(thread.clone(), PathBuf::from(&cwd));
        server.chat_repos.record_thread_place(&thread, PathBuf::from(cwd));
    }
    match kind {
        Incoming::ServerRequest { key, .. } => {
            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).insert(key);
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
            let repo_folder = shared.repo_openers.lock().unwrap_or_else(|p| p.into_inner()).remove(&key);
            if let Some(folder) = repo_folder
                && let Some(thread) = value.pointer("/result/thread/id").and_then(Value::as_str)
            {
                let _ = server.chat_repos.record_app_thread(&server.config.codex_home, &folder, thread);
            }
            let turn_thread = shared.turn_start_threads.lock().unwrap_or_else(|p| p.into_inner()).remove(&key);
            if let Some(err_msg) = value.pointer("/error/message").and_then(Value::as_str) {
                if err_msg.contains(crate::repo_limits::BENCHMARK_REFUSAL_MARK) {
                    if let Some(thread_id) = turn_thread {
                        if let Some(record) = server.chat_repos.find_by_app_thread(&server.config.codex_home, &thread_id) {
                            server.chat_repos.record_notice(&record.chat_key(), &record.repo, "busy");
                        }
                    }
                }
            }
        }
        Incoming::NotProtocol => {}
    }
    let _ = out.send(event("work://message", value));
}
