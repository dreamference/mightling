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

use axum::extract::ws::Message;
use axum::extract::ws::WebSocket;
use futures::SinkExt;
use futures::StreamExt;
use futures::stream::SplitSink;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_tungstenite::tungstenite::Message as UpstreamMessage;

use crate::app_server::Upstream;
use crate::ask;
use crate::policy::Incoming;
use crate::policy::classify;
use crate::server::Server;

/// What a tab's connection shares with the task reading the app-server.
#[derive(Default)]
struct Shared {
    /// Server requests waiting for this tab's answer.
    pending: Mutex<HashSet<String>>,
    /// Thread openers whose new thread works in an Ask folder, by request id: answered, the folder
    /// is named after the thread.
    openers: Mutex<HashMap<String, PathBuf>>,
}

struct Tab {
    id: u64,
    server: Arc<Server>,
    out: mpsc::UnboundedSender<Value>,
    upstream: Option<SplitSink<Upstream, UpstreamMessage>>,
    reader: Option<JoinHandle<()>>,
    shared: Arc<Shared>,
}

fn event(channel: &str, payload: Value) -> Value {
    json!({ "event": { "channel": channel, "payload": payload } })
}

pub async fn run_tab(socket: WebSocket, server: Arc<Server>) {
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
    let mut tab = Tab { id: server.new_tab_id(), server, out: out.clone(), upstream: None, reader: None, shared: Arc::default() };
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
            self.upstream = None;
            self.reader = None;
        }
    }

    async fn start(&mut self) -> Result<Value, String> {
        self.drop_finished();
        if self.upstream.is_some() {
            return Ok(json!({ "served_model": self.server.served_model(), "started": false, "ask_root": self.ask_root() }));
        }
        let (upstream, started) = self.server.app_server.connect().await?;
        let (sink, mut stream) = upstream.split();
        self.upstream = Some(sink);
        let (server, shared, out, tab) = (self.server.clone(), self.shared.clone(), self.out.clone(), self.id);
        self.reader = Some(tokio::spawn(async move {
            while let Some(Ok(frame)) = stream.next().await {
                let text = match frame {
                    UpstreamMessage::Text(text) => text.as_str().to_string(),
                    UpstreamMessage::Close(_) => break,
                    _ => continue,
                };
                deliver(&server, &shared, &out, tab, &text);
            }
            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).clear();
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
        if self.upstream.is_none() {
            return Err("the agent's server is not running".to_string());
        }
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
        let composed = match named {
            Some(name) => {
                let text = crate::prompts::composed(&self.server.config.launch, &name, &ask_root).await?;
                Some((name, text))
            }
            None => None,
        };
        let prompts = ask::MessagePrompts { root: &ask_root, composed, created: Mutex::new(None) };
        let vetted = {
            let mut pending = self.shared.pending.lock().unwrap_or_else(|p| p.into_inner());
            policy.vet_outgoing(&message, &mut pending, &prompts)?
        };
        // A thread that will work in an Ask folder: once the server names it, so is the folder.
        let opener = vetted.get("method").and_then(Value::as_str).is_some_and(|method| policy.thread_openers.contains(method));
        if opener
            && let (Some(id), Some(cwd)) = (vetted.get("id"), vetted.get("params").and_then(|p| p.get("cwd")).and_then(Value::as_str))
            && ask::is_folder(&ask_root, std::path::Path::new(cwd))
        {
            self.shared.openers.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), PathBuf::from(cwd));
        }
        let Some(upstream) = self.upstream.as_mut() else { return Err("the agent's server is not running".to_string()) };
        upstream.send(UpstreamMessage::Text(vetted.to_string().into())).await.map_err(|err| format!("could not reach the agent's server: {err}"))
    }

    /// Closes this tab's connection; the server itself goes on for other tabs and other clients.
    async fn close(&mut self, announce: bool) {
        if let Some(mut upstream) = self.upstream.take() {
            let _ = upstream.close().await;
        }
        if let Some(reader) = self.reader.take() {
            reader.abort();
            let _ = reader.await;
            self.shared.pending.lock().unwrap_or_else(|p| p.into_inner()).clear();
            self.server.forget_tab(self.id);
            if announce {
                let _ = self.out.send(event("work://exit", Value::Null));
            }
        }
    }
}

/// One message from the app-server, on its way to the page.
fn deliver(server: &Server, shared: &Shared, out: &mpsc::UnboundedSender<Value>, tab: u64, text: &str) {
    if text.trim().is_empty() {
        return;
    }
    let (kind, value) = classify(text);
    let Some(value) = value.filter(|_| kind != Incoming::NotProtocol) else {
        let _ = out.send(event("work://protocol-error", Value::String(text.to_string())));
        return;
    };
    match kind {
        Incoming::ServerRequest { key, .. } => {
            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).insert(key);
        }
        Incoming::Notification { method } => server.observe_busy(tab, &method, value.get("params")),
        Incoming::Response => {
            let key = value.get("id").map(Value::to_string).unwrap_or_default();
            let folder = shared.openers.lock().unwrap_or_else(|p| p.into_inner()).remove(&key);
            if let Some(folder) = folder
                && let Some(thread) = value.pointer("/result/thread/id").and_then(Value::as_str)
            {
                let _ = ask::link_thread(&server.ask_root(), thread, &folder);
            }
        }
        Incoming::NotProtocol => {}
    }
    let _ = out.send(event("work://message", value));
}
