//! The sync agent as a host (specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.1): one more client of every
//! app-server that listens on a Unix socket on this machine, so a turn running there is followed
//! live from other devices and answered from them.
//!
//! - **Which servers.** Codex's control socket (`$CODEX_HOME/app-server-control/app-server-control.sock`),
//!   which a TUI started while a server listens there runs on (Phase 0, §16.3), and `ling web`'s
//!   socket. The agent never starts a server: with neither listening it hosts nothing, and the
//!   device is online only through another host (the desktop app).
//! - **Following.** Every few seconds it asks each server which threads are loaded and attaches to
//!   each with `thread/resume` and no history, which subscribes without a second writer. What the
//!   server says about them (`sync_live::FOLLOWED`, and the server requests that name a thread)
//!   goes to the hub, batched every 100 ms.
//! - **Answering.** A command from the hub becomes the request the user would have made here, and
//!   passes this machine's own copy of `policy.json` first (`Policy::vet_outgoing`, with the server
//!   requests still waiting): an approval only for one that waits, a new thread under the same
//!   rules as any other. How it went goes back to the hub.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::HashMap;
use std::collections::HashSet;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use ling_web_server::app_server::AppServer;
use ling_web_server::app_server::Launch;
use ling_web_server::ask;
use ling_web_server::policy::Policy;
use ling_web_server::sync_live::ANSWERABLE;
use ling_web_server::sync_live::FOLLOWED;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

use crate::client::HubClient;
use crate::config::Settings;

/// How often the servers are asked which threads are loaded, and the sockets tried again.
const LOOK_EVERY: Duration = Duration::from_secs(5);
/// How long a request to a server may take.
const ANSWER_WITHIN: Duration = Duration::from_secs(60);
/// The largest event passed on.
const EVENT_CAP: usize = 256 << 10;

/// The sockets an app-server of this machine may listen on.
pub fn sockets(codex_home: &Path) -> Vec<PathBuf> {
    vec![
        codex_home.join("app-server-control").join("app-server-control.sock"),
        ling_web_server::app_server::default_socket(&ling_web_server::auth::state_dir(codex_home)),
    ]
}

/// What a server connection shares with its reader.
#[derive(Default)]
struct Shared {
    /// Server requests waiting for an answer, by id as JSON text (what the policy checks).
    pending: Mutex<HashSet<String>>,
    /// The same requests, whole, with their thread: what an approval is checked against.
    requests: Mutex<HashMap<String, (String, Value)>>,
    /// Answers awaited, by request id.
    waiting: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    /// Threads attached, with their folder.
    attached: Mutex<BTreeMap<String, String>>,
    /// The turn running in each thread.
    turns: Mutex<HashMap<String, String>>,
    closed: Mutex<bool>,
}

/// One connection to one app-server.
pub struct Connection {
    socket: PathBuf,
    out: mpsc::UnboundedSender<Message>,
    shared: Arc<Shared>,
    next: Mutex<u64>,
}

impl Connection {
    /// Connects and initializes; what the server says goes to `events` (thread, message).
    pub async fn open(socket: &Path, events: mpsc::UnboundedSender<(String, Value)>) -> Result<Arc<Connection>, String> {
        let server = AppServer::new(socket.to_path_buf(), Launch { ling: PathBuf::new(), env: Vec::new(), log: PathBuf::new() });
        let upstream = server.connect_once().await.map_err(|err| err.to_string())?;
        let (mut sink, mut stream) = upstream.split();
        let (out, mut outgoing) = mpsc::unbounded_channel::<Message>();
        tokio::spawn(async move {
            while let Some(message) = outgoing.recv().await {
                if sink.send(message).await.is_err() {
                    break;
                }
            }
        });
        let shared = Arc::new(Shared::default());
        let reader = shared.clone();
        tokio::spawn(async move {
            while let Some(Ok(frame)) = stream.next().await {
                let Message::Text(text) = frame else { continue };
                let Ok(value) = serde_json::from_str::<Value>(&text) else { continue };
                Connection::incoming(&reader, value, &events);
            }
            *reader.closed.lock().unwrap_or_else(|p| p.into_inner()) = true;
            for (_, waiter) in reader.waiting.lock().unwrap_or_else(|p| p.into_inner()).drain() {
                let _ = waiter.send(json!({ "error": { "message": "the server closed the connection" } }));
            }
        });
        let connection = Arc::new(Connection { socket: socket.to_path_buf(), out, shared, next: Mutex::new(0) });
        let info = json!({ "clientInfo": { "name": "ling-sync", "title": null, "version": env!("CARGO_PKG_VERSION") } });
        connection.raw_request("initialize", info).await?;
        connection.send_raw(json!({ "method": "initialized" }))?;
        Ok(connection)
    }

    fn incoming(shared: &Shared, value: Value, events: &mpsc::UnboundedSender<(String, Value)>) {
        let method = value.get("method").and_then(Value::as_str).map(str::to_string);
        let id = value.get("id").cloned();
        let thread = value.pointer("/params/threadId").and_then(Value::as_str).map(str::to_string);
        match (method, id) {
            (None, Some(id)) => {
                if let Some(waiter) = shared.waiting.lock().unwrap_or_else(|p| p.into_inner()).remove(&id.to_string()) {
                    let _ = waiter.send(value);
                }
            }
            (Some(method), Some(id)) => {
                shared.pending.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string());
                if let Some(thread) = thread {
                    shared.requests.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), (thread.clone(), value.clone()));
                    let _ = method;
                    let _ = events.send((thread, value));
                }
            }
            (Some(method), None) => {
                match method.as_str() {
                    "turn/started" => {
                        if let (Some(thread), Some(turn)) = (&thread, value.pointer("/params/turn/id").and_then(Value::as_str)) {
                            shared.turns.lock().unwrap_or_else(|p| p.into_inner()).insert(thread.clone(), turn.to_string());
                        }
                    }
                    "turn/completed" => {
                        if let Some(thread) = &thread {
                            shared.turns.lock().unwrap_or_else(|p| p.into_inner()).remove(thread);
                        }
                    }
                    "serverRequest/resolved" => {
                        if let Some(id) = value.pointer("/params/requestId") {
                            shared.pending.lock().unwrap_or_else(|p| p.into_inner()).remove(&id.to_string());
                            shared.requests.lock().unwrap_or_else(|p| p.into_inner()).remove(&id.to_string());
                        }
                    }
                    _ => {}
                }
                if FOLLOWED.contains(&method.as_str())
                    && let Some(thread) = thread
                    && value.to_string().len() <= EVENT_CAP
                {
                    let _ = events.send((thread, value));
                }
            }
            _ => {}
        }
    }

    pub fn closed(&self) -> bool {
        *self.shared.closed.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn send_raw(&self, value: Value) -> Result<(), String> {
        self.out.send(Message::Text(value.to_string().into())).map_err(|_| "the server closed the connection".to_string())
    }

    fn next_id(&self) -> Value {
        let mut next = self.next.lock().unwrap_or_else(|p| p.into_inner());
        *next += 1;
        json!(format!("sync-{}", *next))
    }

    async fn await_answer(&self, id: &Value, receiver: oneshot::Receiver<Value>) -> Result<Value, String> {
        let answer = tokio::time::timeout(ANSWER_WITHIN, receiver)
            .await
            .map_err(|_| {
                self.shared.waiting.lock().unwrap_or_else(|p| p.into_inner()).remove(&id.to_string());
                "the server did not answer in time".to_string()
            })?
            .map_err(|_| "the server closed the connection".to_string())?;
        match answer.get("error") {
            Some(error) => Err(error.get("message").and_then(Value::as_str).unwrap_or("the server refused it").to_string()),
            None => Ok(answer.get("result").cloned().unwrap_or(Value::Null)),
        }
    }

    /// A request the agent makes for itself (initialize, listing and attaching): not a user's act.
    async fn raw_request(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id();
        let (sender, receiver) = oneshot::channel();
        self.shared.waiting.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), sender);
        self.send_raw(json!({ "id": id, "method": method, "params": params }))?;
        self.await_answer(&id, receiver).await
    }

    /// A request on behalf of a user on another device: through the policy first, as if typed here.
    async fn user_request(&self, policy: &Policy, codex_home: &Path, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id();
        let vetted = self.vet(policy, codex_home, json!({ "id": id, "method": method, "params": params }))?;
        let (sender, receiver) = oneshot::channel();
        self.shared.waiting.lock().unwrap_or_else(|p| p.into_inner()).insert(id.to_string(), sender);
        self.send_raw(vetted)?;
        self.await_answer(&id, receiver).await
    }

    fn vet(&self, policy: &Policy, codex_home: &Path, message: Value) -> Result<Value, String> {
        let root = ask::root(codex_home);
        let prompts = ask::MessagePrompts {
            root: &root,
            composed: None,
            created: Mutex::new(None),
            airgapped_on: ling_web_server::server::user_airgap_level().level == ling_airgapped::Level::On,
            thread_folder: None,
        };
        let mut pending = self.shared.pending.lock().unwrap_or_else(|p| p.into_inner());
        policy.vet_outgoing(&message, &mut pending, &prompts)
    }

    /// Asks which threads are loaded and attaches to those not attached yet. Returns them.
    pub async fn attach_loaded(&self, settings: &Settings) -> BTreeSet<String> {
        let loaded = self.raw_request("thread/loaded/list", json!({})).await.unwrap_or(Value::Null);
        let ids: Vec<String> = loaded.get("data").and_then(Value::as_array).map(|ids| ids.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default();
        for id in &ids {
            if self.shared.attached.lock().unwrap_or_else(|p| p.into_inner()).contains_key(id) {
                continue;
            }
            if let Ok(answer) = self.raw_request("thread/resume", json!({ "threadId": id })).await {
                let cwd = answer.pointer("/thread/cwd").and_then(Value::as_str).unwrap_or_default().to_string();
                // A thread kept on this device (`[sync] exclude`) is not followed from elsewhere.
                if !settings.excluded(&cwd) {
                    self.shared.attached.lock().unwrap_or_else(|p| p.into_inner()).insert(id.clone(), cwd);
                }
            }
        }
        let loaded: BTreeSet<String> = ids.into_iter().collect();
        let mut attached = self.shared.attached.lock().unwrap_or_else(|p| p.into_inner());
        attached.retain(|id, _| loaded.contains(id));
        attached.keys().cloned().collect()
    }

    pub fn holds(&self, thread: &str) -> bool {
        self.shared.attached.lock().unwrap_or_else(|p| p.into_inner()).contains_key(thread)
    }

    pub fn projects(&self) -> Vec<String> {
        let ask_marker = format!("{}ask{}", std::path::MAIN_SEPARATOR, std::path::MAIN_SEPARATOR);
        let mut folders: Vec<String> =
            self.shared.attached.lock().unwrap_or_else(|p| p.into_inner()).values().filter(|cwd| !cwd.is_empty() && !cwd.contains(&ask_marker)).cloned().collect();
        folders.sort();
        folders.dedup();
        folders
    }

    /// Carries out one command from the hub. Returns the thread it concerned (a new one for `start`).
    pub async fn carry_out(&self, policy: &Policy, codex_home: &Path, command: &Value) -> Result<Option<String>, String> {
        let kind = command.get("kind").and_then(Value::as_str).unwrap_or_default();
        let thread = command.get("thread").and_then(Value::as_str).map(str::to_string);
        let text = command.get("text").and_then(Value::as_str).unwrap_or_default();
        let model = command.get("model").and_then(Value::as_str).filter(|model| !model.is_empty());
        let input = json!([{ "type": "text", "text": text, "text_elements": [] }]);
        match kind {
            "send" => {
                let thread = thread.ok_or("a message names its thread")?;
                if !self.holds(&thread) {
                    self.user_request(policy, codex_home, "thread/resume", json!({ "threadId": thread })).await?;
                }
                let running = self.shared.turns.lock().unwrap_or_else(|p| p.into_inner()).get(&thread).cloned();
                match running {
                    Some(turn) => self.user_request(policy, codex_home, "turn/steer", json!({ "threadId": thread, "input": input, "expectedTurnId": turn })).await?,
                    None => {
                        let mut params = json!({ "threadId": thread, "input": input });
                        if let Some(model) = model {
                            params["model"] = json!(model);
                        }
                        self.user_request(policy, codex_home, "turn/start", params).await?
                    }
                };
                Ok(Some(thread))
            }
            "approve" => {
                let request = command.get("request").cloned().ok_or("name the approval")?;
                let decision = command.get("decision").and_then(Value::as_str).filter(|d| matches!(*d, "accept" | "decline")).ok_or("an approval is accept or decline")?;
                let waiting = self.shared.requests.lock().unwrap_or_else(|p| p.into_inner()).get(&request.to_string()).cloned();
                let Some((of_thread, asked)) = waiting else { return Err("that approval is not waiting any more".to_string()) };
                if !ANSWERABLE.contains(&asked.get("method").and_then(Value::as_str).unwrap_or_default()) {
                    return Err("this question is answered on the device itself".to_string());
                }
                let answer = self.vet(policy, codex_home, json!({ "id": request, "result": { "decision": decision } }))?;
                self.send_raw(answer)?;
                Ok(Some(of_thread))
            }
            "stop" => {
                let thread = thread.ok_or("name the thread")?;
                let turn = self.shared.turns.lock().unwrap_or_else(|p| p.into_inner()).get(&thread).cloned().ok_or("no turn is running")?;
                self.user_request(policy, codex_home, "turn/interrupt", json!({ "threadId": thread, "turnId": turn })).await?;
                Ok(Some(thread))
            }
            "start" => {
                let cwd = command.get("cwd").and_then(Value::as_str).ok_or("name the folder")?;
                let started = self.user_request(policy, codex_home, "thread/start", json!({ "cwd": cwd })).await?;
                let thread = started.pointer("/thread/id").and_then(Value::as_str).ok_or("the server named no thread")?.to_string();
                self.shared.attached.lock().unwrap_or_else(|p| p.into_inner()).insert(thread.clone(), cwd.to_string());
                let mut params = json!({ "threadId": thread, "input": input });
                if let Some(model) = model {
                    params["model"] = json!(model);
                }
                self.user_request(policy, codex_home, "turn/start", params).await?;
                Ok(Some(thread))
            }
            _ => Err(format!("no such command: {kind}")),
        }
    }
}

/// The host: connections to the servers that listen, what they hold, and the loops that post to
/// the hub. Shared by three loops: one looks for servers and loaded threads, one posts events as
/// they come (batched every 100 ms), one waits for commands; a command handed to either posting
/// loop is carried out by it.
pub struct Host {
    codex_home: PathBuf,
    id: String,
    policy: Policy,
    connections: Mutex<Vec<Arc<Connection>>>,
    holding: Mutex<BTreeSet<String>>,
    events: mpsc::UnboundedSender<(String, Value)>,
}

impl Host {
    pub fn new(codex_home: &Path) -> (Arc<Host>, mpsc::UnboundedReceiver<(String, Value)>) {
        let (events, incoming) = mpsc::unbounded_channel();
        let host = Host {
            codex_home: codex_home.to_path_buf(),
            id: format!("agent-{}", ling_web_server::auth::random_hex(6)),
            policy: Policy::embedded(),
            connections: Mutex::new(Vec::new()),
            holding: Mutex::new(BTreeSet::new()),
            events,
        };
        (Arc::new(host), incoming)
    }

    fn connections(&self) -> Vec<Arc<Connection>> {
        self.connections.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    /// Connects to the sockets that answer and refreshes what is attached.
    pub async fn look(&self, sockets: &[PathBuf], settings: &Settings) {
        self.connections.lock().unwrap_or_else(|p| p.into_inner()).retain(|connection| !connection.closed());
        for socket in sockets {
            let known = self.connections().iter().any(|connection| connection.socket == *socket);
            if known || !socket.exists() {
                continue;
            }
            if let Ok(connection) = Connection::open(socket, self.events.clone()).await {
                self.connections.lock().unwrap_or_else(|p| p.into_inner()).push(connection);
            }
        }
        let mut holding = BTreeSet::new();
        for connection in self.connections() {
            holding.extend(connection.attach_loaded(settings).await);
        }
        *self.holding.lock().unwrap_or_else(|p| p.into_inner()) = holding;
    }

    pub fn attached(&self) -> bool {
        !self.connections().is_empty()
    }

    fn holds(&self, thread: &str) -> bool {
        self.connections().iter().any(|connection| connection.holds(thread))
    }

    fn body(&self, events: Vec<Value>, acks: Vec<Value>) -> Value {
        let holding: Vec<String> = self.holding.lock().unwrap_or_else(|p| p.into_inner()).iter().cloned().collect();
        let mut projects: Vec<String> = self.connections().iter().flat_map(|connection| connection.projects()).collect();
        projects.sort();
        projects.dedup();
        json!({ "host": self.id, "kind": "agent", "holding": holding, "projects": projects, "models": [], "events": events, "acks": acks })
    }

    /// Carries out the commands a post brought back, and tells the hub how each went.
    async fn handle(&self, hub: &HubClient, answer: Value) -> Result<(), String> {
        let commands = answer.get("commands").and_then(Value::as_array).cloned().unwrap_or_default();
        if commands.is_empty() {
            return Ok(());
        }
        let mut acks = Vec::new();
        for command in &commands {
            let id = command.get("id").cloned().unwrap_or(Value::Null);
            let thread = command.get("thread").and_then(Value::as_str);
            let connections = self.connections();
            let connection = connections.iter().find(|connection| thread.is_some_and(|thread| connection.holds(thread))).or_else(|| connections.first()).cloned();
            let result = match connection {
                Some(connection) => connection.carry_out(&self.policy, &self.codex_home, command).await,
                None => Err("no app-server of this device is open".to_string()),
            };
            acks.push(match result {
                Ok(thread) => json!({ "id": id, "ok": true, "thread": thread }),
                Err(error) => json!({ "id": id, "ok": false, "error": error }),
            });
        }
        let answer = hub.host(self.body(Vec::new(), acks), 0).await?;
        Box::pin(self.handle(hub, answer)).await
    }

    /// Posts what the servers said, as it comes, a batch every 100 ms at most.
    pub async fn publish(&self, hub: &HubClient, incoming: &mut mpsc::UnboundedReceiver<(String, Value)>) -> Result<(), String> {
        let Some(first) = incoming.recv().await else { return Ok(()) };
        tokio::time::sleep(Duration::from_millis(100)).await;
        let mut batch = vec![first];
        while let Ok(more) = incoming.try_recv() {
            batch.push(more);
        }
        let events: Vec<Value> = batch.into_iter().filter(|(thread, _)| self.holds(thread)).map(|(thread, message)| json!({ "thread": thread, "message": message })).collect();
        if events.is_empty() {
            return Ok(());
        }
        let answer = hub.host(self.body(events, Vec::new()), 0).await?;
        self.handle(hub, answer).await
    }

    /// Waits for commands (at most `wait` seconds), saying meanwhile that this device is online.
    pub async fn wait_for_commands(&self, hub: &HubClient, wait: u64) -> Result<(), String> {
        let answer = hub.host(self.body(Vec::new(), Vec::new()), wait).await?;
        self.handle(hub, answer).await
    }
}

/// Runs the host until the process ends: looks for servers every few seconds and, while one is
/// open, posts and waits for commands.
pub async fn run(codex_home: PathBuf, hub: Arc<HubClient>) {
    let (host, mut incoming) = Host::new(&codex_home);
    let sockets = sockets(&codex_home);
    let looker = {
        let host = host.clone();
        async move {
            loop {
                host.look(&sockets, &Settings::load()).await;
                tokio::time::sleep(LOOK_EVERY).await;
            }
        }
    };
    let publisher = {
        let (host, hub) = (host.clone(), hub.clone());
        async move {
            loop {
                if host.publish(&hub, &mut incoming).await.is_err() {
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    };
    let poller = async move {
        loop {
            if !host.attached() {
                tokio::time::sleep(LOOK_EVERY).await;
                continue;
            }
            if host.wait_for_commands(&hub, 25).await.is_err() {
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        }
    };
    tokio::join!(looker, publisher, poller);
}
