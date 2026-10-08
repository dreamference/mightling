//! The conversation model both adapters share (MIGHTLING_CHAT §3): which thread a chat is on,
//! commands, turns and their streamed answers, steering, approvals, and the air gap.
//!
//! Adapters turn what their messenger delivers into [`Inbound`] and render [`Outbound`]; they
//! never talk to the agent. A chat is named by a key, `telegram:<chat id>` or `matrix:<room id>`,
//! whose prefix picks the adapter that renders it. The hub talks to the agent only through an
//! [`AgentHandle`], so the tests drive it with a scripted stand-in.

use std::collections::HashMap;
use std::collections::HashSet;
use std::path::PathBuf;
use std::time::Duration;
use std::time::Instant;

use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;

use crate::agent::AgentHandle;
use crate::agent::CONNECTED;
use crate::store::ThreadRef;
use crate::store::Threads;

/// What an adapter hands the hub, from a paired user only.
#[derive(Clone, Debug, PartialEq)]
pub enum Inbound {
    /// A message, or a command (`/new`, …).
    Text { chat: String, text: String },
    /// The messenger's own stop control (Telegram's button on a streaming answer).
    Stop { chat: String },
    /// A button pressed or a reaction on an approval: `choice` indexes its options.
    Answer { chat: String, approval: u64, choice: usize },
}

/// What the hub asks an adapter to show.
#[derive(Clone, Debug, PartialEq)]
pub enum Outbound {
    /// A turn is running; the answer so far (empty before the first word).
    Draft { chat: String, text: String },
    /// The final answer, in Markdown.
    Message { chat: String, markdown: String },
    /// One line from the bridge itself, plain text.
    Notice { chat: String, text: String },
    /// A request from the agent, with its options; the last option always declines.
    Approval { chat: String, id: u64, text: String, options: Vec<String> },
    /// An approval was answered, declined for lack of an answer, or resolved elsewhere.
    ApprovalClosed { chat: String, id: u64, outcome: String },
    /// The turn is over: drop the draft, stop "typing".
    TurnEnded { chat: String },
}

impl Outbound {
    pub fn chat(&self) -> &str {
        match self {
            Outbound::Draft { chat, .. }
            | Outbound::Message { chat, .. }
            | Outbound::Notice { chat, .. }
            | Outbound::Approval { chat, .. }
            | Outbound::ApprovalClosed { chat, .. }
            | Outbound::TurnEnded { chat } => chat,
        }
    }
}

/// The adapter a chat key belongs to: the part before the first `:`.
pub fn adapter_of(chat: &str) -> &str {
    chat.split(':').next().unwrap_or_default()
}

pub const HELP: &str = "Mightling, from your phone. Each conversation is an Ask thread on your machine.\n\
/new — the next message starts a new thread\n\
/stop — stop the running answer\n\
/threads — your recent threads\n\
/use <n> — continue thread n from /threads\n\
/status — what Mightling is doing\n\
/help — this list";

/// Settings (MIGHTLING_CHAT §10).
#[derive(Clone, Debug)]
pub struct HubConfig {
    /// `~/.mightling/chat`.
    pub dir: PathBuf,
    pub approval_timeout: Duration,
    pub draft_interval: Duration,
    /// A draft is resent at least this often while a turn runs (Telegram keeps one 30 s).
    pub draft_refresh: Duration,
    pub max_threads: usize,
    /// How often the air-gap level is read.
    pub airgap_interval: Duration,
}

impl HubConfig {
    pub fn new(dir: PathBuf) -> HubConfig {
        HubConfig {
            dir,
            approval_timeout: Duration::from_secs(600),
            draft_interval: Duration::from_millis(1000),
            draft_refresh: Duration::from_secs(20),
            max_threads: 10,
            airgap_interval: Duration::from_secs(60),
        }
    }
}

/// One turn the bridge started, until it completes.
struct Turn {
    chat: String,
    id: Option<String>,
    /// Agent messages in order: (item id, text, phase).
    items: Vec<(String, String, Option<String>)>,
    error: Option<String>,
    dirty: bool,
    last_draft: Instant,
}

impl Turn {
    fn draft(&self) -> String {
        self.items.iter().map(|(_, text, _)| text.trim()).filter(|text| !text.is_empty()).collect::<Vec<_>>().join("\n\n")
    }

    /// The answer: the items marked final, or else the last message.
    fn answer(&self) -> String {
        let finals: Vec<&str> = self
            .items
            .iter()
            .filter(|(_, _, phase)| phase.as_deref() == Some("final_answer"))
            .map(|(_, text, _)| text.trim())
            .filter(|text| !text.is_empty())
            .collect();
        if !finals.is_empty() {
            return finals.join("\n\n");
        }
        self.items.iter().rev().map(|(_, text, _)| text.trim()).find(|text| !text.is_empty()).unwrap_or_default().to_string()
    }

    fn delta(&mut self, item: &str, delta: &str) {
        match self.items.iter_mut().find(|(id, _, _)| id == item) {
            Some((_, text, _)) => text.push_str(delta),
            None => self.items.push((item.to_string(), delta.to_string(), None)),
        }
        self.dirty = true;
    }

    fn completed_item(&mut self, item: &Value) {
        let Some(id) = item.get("id").and_then(Value::as_str) else { return };
        let text = item.get("text").and_then(Value::as_str).unwrap_or_default().to_string();
        let phase = item.get("phase").and_then(Value::as_str).map(str::to_string);
        match self.items.iter_mut().find(|(existing, _, _)| existing == id) {
            Some(entry) => {
                entry.1 = text;
                entry.2 = phase;
            }
            None => self.items.push((id.to_string(), text, phase)),
        }
        self.dirty = true;
    }
}

/// What a pending approval answers with.
#[derive(Clone, Debug)]
enum Reply {
    /// Approve: the method's approve payload.
    Approve,
    /// Decline: the method's decline payload.
    Decline,
    /// `requestUserInput`: this answer to its one question.
    Answer(String),
}

struct Pending {
    chat: String,
    thread: String,
    request: Value,
    method: String,
    params: Value,
    replies: Vec<Reply>,
    /// A question with no options takes the next message as its answer.
    free_text: bool,
    deadline: Instant,
}

pub struct Hub {
    config: HubConfig,
    agent: AgentHandle,
    threads: Threads,
    adapters: HashMap<String, mpsc::UnboundedSender<Outbound>>,
    turns: HashMap<String, Turn>,
    /// Messages that arrived while a turn ran and could not steer it.
    queued: HashMap<String, Vec<String>>,
    approvals: HashMap<u64, Pending>,
    next_approval: u64,
    /// Threads this connection is subscribed to (started or resumed since it was made).
    subscribed: HashSet<String>,
    airgap: Box<dyn Fn() -> bool + Send>,
    airgapped: bool,
    airgap_read: Instant,
}

fn text_input(text: &str) -> Value {
    json!([{ "type": "text", "text": text, "text_elements": [] }])
}

fn cut(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let mut out: String = text.chars().take(limit).collect();
    out.push('…');
    out
}

fn thread_name(text: &str) -> String {
    let line = text.lines().find(|line| !line.trim().is_empty()).unwrap_or_default().trim();
    cut(line, 60)
}

impl Hub {
    /// `airgap` answers whether the user-level air gap is `on` (MIGHTLING_CHAT §3).
    pub fn new(config: HubConfig, agent: AgentHandle, airgap: Box<dyn Fn() -> bool + Send>) -> Hub {
        let threads = Threads::load(&config.dir);
        let airgapped = airgap();
        Hub {
            config,
            agent,
            threads,
            adapters: HashMap::new(),
            turns: HashMap::new(),
            queued: HashMap::new(),
            approvals: HashMap::new(),
            next_approval: 0,
            subscribed: HashSet::new(),
            airgap,
            airgapped,
            airgap_read: Instant::now(),
        }
    }

    /// Routes chats whose key starts with `<prefix>:` to `sender`.
    pub fn add_adapter(&mut self, prefix: &str, sender: mpsc::UnboundedSender<Outbound>) {
        self.adapters.insert(prefix.to_string(), sender);
    }

    fn send(&self, out: Outbound) {
        if let Some(adapter) = self.adapters.get(adapter_of(out.chat())) {
            let _ = adapter.send(out);
        }
    }

    fn notice(&self, chat: &str, text: impl Into<String>) {
        self.send(Outbound::Notice { chat: chat.to_string(), text: text.into() });
    }

    /// Runs until both channels close.
    pub async fn run(mut self, mut inbound: mpsc::UnboundedReceiver<Inbound>, mut events: mpsc::UnboundedReceiver<Value>) {
        let mut tick = tokio::time::interval(Duration::from_millis(250));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                message = inbound.recv() => match message {
                    Some(message) => self.on_inbound(message).await,
                    None => break,
                },
                event = events.recv() => match event {
                    Some(event) => self.on_event(event).await,
                    None => break,
                },
                _ = tick.tick() => self.on_tick(),
            }
        }
    }

    fn thread_running(&self, thread: &str) -> bool {
        self.turns.contains_key(thread)
    }

    fn current(&self, chat: &str) -> Option<String> {
        self.threads.chats.get(chat).and_then(|c| c.current.clone())
    }

    pub async fn on_inbound(&mut self, message: Inbound) {
        match message {
            Inbound::Text { chat, text } => {
                if adapter_of(&chat) == "telegram" && self.airgapped {
                    self.notice(
                        &chat,
                        "The air gap is on, so Telegram is paused: it would carry this conversation off your machine. Use Matrix, or turn the air gap off on the machine.",
                    );
                    return;
                }
                if self.answer_by_text(&chat, &text) {
                    return;
                }
                let trimmed = text.trim();
                if trimmed.starts_with('/') {
                    self.command(&chat, trimmed).await;
                } else if !trimmed.is_empty() {
                    self.message(&chat, trimmed).await;
                }
            }
            Inbound::Stop { chat } => self.stop(&chat).await,
            Inbound::Answer { chat, approval, choice } => self.answer(&chat, approval, choice),
        }
    }

    async fn command(&mut self, chat: &str, text: &str) {
        let mut words = text.split_whitespace();
        let name = words.next().unwrap_or_default().split('@').next().unwrap_or_default().to_ascii_lowercase();
        match name.as_str() {
            "/new" => {
                self.threads.chats.entry(chat.to_string()).or_default().current = None;
                let _ = self.threads.save(&self.config.dir);
                self.notice(chat, "The next message starts a new thread.");
            }
            "/stop" => self.stop(chat).await,
            "/threads" => {
                let recent = self.threads.chats.get(chat).map(|c| c.recent.clone()).unwrap_or_default();
                if recent.is_empty() {
                    self.notice(chat, "No threads yet: send a message to start one.");
                    return;
                }
                let current = self.current(chat);
                let lines: Vec<String> = recent
                    .iter()
                    .enumerate()
                    .map(|(n, t)| {
                        let marker = if current.as_deref() == Some(t.id.as_str()) { " (current)" } else { "" };
                        format!("{}. {}{marker}", n + 1, if t.name.is_empty() { "(unnamed)" } else { t.name.as_str() })
                    })
                    .collect();
                self.notice(chat, format!("{}\nContinue one with /use <n>.", lines.join("\n")));
            }
            "/use" => {
                let recent = self.threads.chats.get(chat).map(|c| c.recent.clone()).unwrap_or_default();
                let chosen = words.next().and_then(|n| n.parse::<usize>().ok()).and_then(|n| n.checked_sub(1)).and_then(|n| recent.get(n).cloned());
                match chosen {
                    Some(thread) => {
                        self.threads.chats.entry(chat.to_string()).or_default().current = Some(thread.id.clone());
                        let _ = self.threads.save(&self.config.dir);
                        self.notice(chat, format!("Continuing \"{}\".", thread.name));
                    }
                    None => self.notice(chat, "No such thread: /threads lists them."),
                }
            }
            "/status" => {
                let current = self.current(chat);
                let name = current
                    .as_ref()
                    .and_then(|id| self.threads.chats.get(chat)?.recent.iter().find(|t| &t.id == id).map(|t| t.name.clone()))
                    .unwrap_or_else(|| "none (the next message starts one)".to_string());
                let running = current.as_deref().is_some_and(|id| self.thread_running(id));
                self.notice(
                    chat,
                    format!(
                        "Thread: {name}\nAnswering: {}\nAir gap: {}",
                        if running { "yes" } else { "no" },
                        if self.airgapped { "on" } else { "off" }
                    ),
                );
            }
            "/help" | "/start" => self.notice(chat, HELP),
            "/pair" => self.notice(chat, "This chat is already paired."),
            _ => self.notice(chat, format!("Unknown command {name}. /help lists them.")),
        }
    }

    async fn stop(&mut self, chat: &str) {
        let Some(thread) = self.current(chat) else { return };
        let Some(turn) = self.turns.get(&thread).and_then(|t| t.id.clone()) else {
            self.notice(chat, "Nothing is running.");
            return;
        };
        self.queued.remove(&thread);
        if let Err(err) = self.agent.request("turn/interrupt", json!({ "threadId": thread, "turnId": turn })).await {
            self.notice(chat, format!("Could not stop it: {err}"));
        }
    }

    async fn ensure_thread(&mut self, chat: &str, first: &str) -> Result<String, String> {
        if let Some(thread) = self.current(chat) {
            if !self.subscribed.contains(&thread) {
                self.agent.request("thread/resume", json!({ "threadId": thread })).await?;
                self.subscribed.insert(thread.clone());
            }
            return Ok(thread);
        }
        let started = self.agent.request("thread/start", json!({ "prompt": "ask" })).await?;
        let thread = started.pointer("/thread/id").and_then(Value::as_str).ok_or("thread/start named no thread")?.to_string();
        self.subscribed.insert(thread.clone());
        let name = thread_name(first);
        let _ = self.agent.request("thread/name/set", json!({ "threadId": thread, "name": name })).await;
        self.threads.started(chat, ThreadRef { id: thread.clone(), name }, self.config.max_threads);
        let _ = self.threads.save(&self.config.dir);
        Ok(thread)
    }

    async fn message(&mut self, chat: &str, text: &str) {
        let thread = match self.ensure_thread(chat, text).await {
            Ok(thread) => thread,
            Err(err) => {
                self.notice(chat, format!("Mightling is not reachable right now: {err}"));
                return;
            }
        };
        if let Some(turn) = self.turns.get(&thread) {
            let expected = turn.id.clone();
            let steered = match expected {
                Some(turn_id) => self
                    .agent
                    .request("turn/steer", json!({ "threadId": thread, "input": text_input(text), "expectedTurnId": turn_id }))
                    .await
                    .is_ok(),
                None => false,
            };
            if !steered {
                self.queued.entry(thread).or_default().push(text.to_string());
                self.notice(chat, "Noted: this goes in as soon as the current answer is done.");
            }
            return;
        }
        self.start_turn(chat, &thread, text).await;
    }

    async fn start_turn(&mut self, chat: &str, thread: &str, text: &str) {
        self.turns.insert(
            thread.to_string(),
            Turn { chat: chat.to_string(), id: None, items: Vec::new(), error: None, dirty: false, last_draft: Instant::now() },
        );
        self.send(Outbound::Draft { chat: chat.to_string(), text: String::new() });
        match self.agent.request("turn/start", json!({ "threadId": thread, "input": text_input(text) })).await {
            Ok(result) => {
                if let (Some(turn), Some(id)) = (self.turns.get_mut(thread), result.pointer("/turn/id").and_then(Value::as_str)) {
                    turn.id.get_or_insert_with(|| id.to_string());
                }
            }
            Err(err) => {
                self.turns.remove(thread);
                self.send(Outbound::TurnEnded { chat: chat.to_string() });
                self.notice(chat, format!("Could not start the answer: {err}"));
            }
        }
    }

    pub async fn on_event(&mut self, event: Value) {
        let method = event.get("method").and_then(Value::as_str).unwrap_or_default().to_string();
        let params = event.get("params").cloned().unwrap_or(Value::Null);
        if method == CONNECTED {
            self.reconnected().await;
            return;
        }
        if let Some(id) = event.get("id").cloned() {
            self.server_request(id, &method, params);
            return;
        }
        let thread = params.get("threadId").and_then(Value::as_str).unwrap_or_default().to_string();
        match method.as_str() {
            "turn/started" => {
                if let (Some(turn), Some(id)) = (self.turns.get_mut(&thread), params.pointer("/turn/id").and_then(Value::as_str)) {
                    turn.id = Some(id.to_string());
                }
            }
            "item/agentMessage/delta" => {
                if let Some(turn) = self.turns.get_mut(&thread) {
                    let item = params.get("itemId").and_then(Value::as_str).unwrap_or_default();
                    turn.delta(item, params.get("delta").and_then(Value::as_str).unwrap_or_default());
                }
            }
            "item/completed" => {
                if let Some(turn) = self.turns.get_mut(&thread)
                    && params.pointer("/item/type").and_then(Value::as_str) == Some("agentMessage")
                {
                    turn.completed_item(&params["item"]);
                }
            }
            "error" => {
                if params.get("willRetry") != Some(&Value::Bool(true))
                    && let Some(turn) = self.turns.get_mut(&thread)
                {
                    turn.error = params.pointer("/error/message").and_then(Value::as_str).map(str::to_string);
                }
            }
            "turn/completed" => self.finish(&thread, &params["turn"]).await,
            "serverRequest/resolved" => {
                let request = params.get("requestId").cloned().unwrap_or(Value::Null);
                let resolved: Vec<u64> = self.approvals.iter().filter(|(_, p)| p.request == request).map(|(id, _)| *id).collect();
                for id in resolved {
                    if let Some(pending) = self.approvals.remove(&id) {
                        self.send(Outbound::ApprovalClosed { chat: pending.chat, id, outcome: "Answered elsewhere.".to_string() });
                    }
                }
            }
            _ => {}
        }
    }

    async fn finish(&mut self, thread: &str, turn_value: &Value) {
        let Some(mut turn) = self.turns.remove(thread) else { return };
        // A turn read back after a reconnection carries its items itself.
        if let Some(items) = turn_value.get("items").and_then(Value::as_array) {
            for item in items.iter().filter(|item| item.get("type").and_then(Value::as_str) == Some("agentMessage")) {
                turn.completed_item(item);
            }
        }
        let chat = turn.chat.clone();
        let answer = turn.answer();
        let status = turn_value.get("status").and_then(Value::as_str).unwrap_or("completed");
        self.send(Outbound::TurnEnded { chat: chat.clone() });
        if !answer.is_empty() {
            self.send(Outbound::Message { chat: chat.clone(), markdown: answer });
        }
        match status {
            "interrupted" => self.notice(&chat, "Stopped."),
            "failed" => {
                let error = turn
                    .error
                    .or_else(|| turn_value.pointer("/error/message").and_then(Value::as_str).map(str::to_string))
                    .unwrap_or_else(|| "no reason given".to_string());
                self.notice(&chat, format!("The answer failed: {error}"));
            }
            _ => {}
        }
        // Approvals of a finished turn can no longer be answered.
        let stale: Vec<u64> = self.approvals.iter().filter(|(_, p)| p.thread == thread).map(|(id, _)| *id).collect();
        for id in stale {
            if let Some(pending) = self.approvals.remove(&id) {
                self.send(Outbound::ApprovalClosed { chat: pending.chat, id, outcome: "The turn ended.".to_string() });
            }
        }
        if let Some(queued) = self.queued.remove(thread)
            && !queued.is_empty()
        {
            self.start_turn(&chat, thread, &queued.join("\n\n")).await;
        }
    }

    /// A new connection: subscriptions are gone. Resubscribe to running turns and deliver any
    /// that ended while the bridge was away.
    async fn reconnected(&mut self) {
        self.subscribed.clear();
        let running: Vec<String> = self.turns.keys().cloned().collect();
        for thread in running {
            if self.agent.request("thread/resume", json!({ "threadId": thread })).await.is_ok() {
                self.subscribed.insert(thread.clone());
            }
            let listed = self.agent.request("thread/turns/list", json!({ "threadId": thread, "limit": 1, "sortDirection": "desc" })).await;
            if let Ok(listed) = listed
                && let Some(last) = listed.pointer("/data/0")
                && last.get("status").and_then(Value::as_str).is_some_and(|status| status != "inProgress")
            {
                let last = last.clone();
                self.finish(&thread, &last).await;
            }
        }
    }

    fn server_request(&mut self, id: Value, method: &str, params: Value) {
        let thread = params.get("threadId").and_then(Value::as_str).unwrap_or_default().to_string();
        // Only the threads this bridge is answering on; another client answers the rest.
        let Some(chat) = self.turns.get(&thread).map(|t| t.chat.clone()) else { return };
        let (text, options, replies, free_text) = match method {
            "item/commandExecution/requestApproval" => {
                let mut text = "Mightling wants to run a command".to_string();
                if let Some(command) = params.get("command").and_then(Value::as_str) {
                    text.push_str(&format!(":\n```\n{}\n```", cut(command, 1000)));
                }
                if let Some(reason) = params.get("reason").and_then(Value::as_str) {
                    text.push_str(&format!("\nWhy: {}", cut(reason, 300)));
                }
                if let Some(cwd) = params.get("cwd").and_then(Value::as_str) {
                    text.push_str(&format!("\nIn: {cwd}"));
                }
                (text, vec!["Approve once".to_string(), "Decline".to_string()], vec![Reply::Approve, Reply::Decline], false)
            }
            "item/fileChange/requestApproval" => {
                let mut text = "Mightling wants to change files".to_string();
                if let Some(reason) = params.get("reason").and_then(Value::as_str) {
                    text.push_str(&format!(": {}", cut(reason, 500)));
                }
                if let Some(root) = params.get("grantRoot").and_then(Value::as_str) {
                    text.push_str(&format!("\nUnder: {root}"));
                }
                (text, vec!["Approve once".to_string(), "Decline".to_string()], vec![Reply::Approve, Reply::Decline], false)
            }
            "item/permissions/requestApproval" => {
                let mut text = "Mightling asks for more permissions".to_string();
                if let Some(reason) = params.get("reason").and_then(Value::as_str) {
                    text.push_str(&format!(": {}", cut(reason, 500)));
                }
                text.push_str(&format!("\n{}", cut(&params.get("permissions").cloned().unwrap_or(Value::Null).to_string(), 600)));
                (text, vec!["Approve for this answer".to_string(), "Decline".to_string()], vec![Reply::Approve, Reply::Decline], false)
            }
            "item/tool/requestUserInput" => {
                let questions = params.get("questions").and_then(Value::as_array).cloned().unwrap_or_default();
                let secret = questions.iter().any(|q| q.get("isSecret") == Some(&Value::Bool(true)));
                if questions.len() != 1 || secret {
                    self.agent.respond(id, json!({ "answers": {} }));
                    self.notice(
                        &chat,
                        if secret {
                            "Mightling asked for something secret. A secret typed here stays in the chat's history, so answer it at a computer."
                        } else {
                            "Mightling asked several questions at once; answer them at a computer."
                        },
                    );
                    return;
                }
                let question = &questions[0];
                let mut text = question.get("header").and_then(Value::as_str).unwrap_or_default().to_string();
                let body = question.get("question").and_then(Value::as_str).unwrap_or_default();
                if !text.is_empty() {
                    text.push('\n');
                }
                text.push_str(body);
                let labels: Vec<String> = question
                    .get("options")
                    .and_then(Value::as_array)
                    .map(|options| options.iter().filter_map(|o| o.get("label").and_then(Value::as_str).map(str::to_string)).collect())
                    .unwrap_or_default();
                if labels.is_empty() {
                    text.push_str("\n(Reply with your answer.)");
                    (text, vec!["Decline".to_string()], vec![Reply::Decline], true)
                } else {
                    let mut options = labels.clone();
                    options.push("Decline".to_string());
                    let mut replies: Vec<Reply> = labels.into_iter().map(Reply::Answer).collect();
                    replies.push(Reply::Decline);
                    (text, options, replies, false)
                }
            }
            _ => return,
        };
        self.next_approval += 1;
        let approval = self.next_approval;
        self.approvals.insert(
            approval,
            Pending {
                chat: chat.clone(),
                thread,
                request: id,
                method: method.to_string(),
                params,
                replies,
                free_text,
                deadline: Instant::now() + self.config.approval_timeout,
            },
        );
        self.send(Outbound::Approval { chat, id: approval, text, options });
    }

    fn payload(pending: &Pending, reply: &Reply) -> Value {
        match (pending.method.as_str(), reply) {
            ("item/permissions/requestApproval", Reply::Approve) => {
                json!({ "permissions": pending.params.get("permissions").cloned().unwrap_or_else(|| json!({})), "scope": "turn" })
            }
            ("item/permissions/requestApproval", _) => json!({ "permissions": {}, "scope": "turn" }),
            ("item/tool/requestUserInput", Reply::Answer(answer)) => {
                let question = pending.params.pointer("/questions/0/id").and_then(Value::as_str).unwrap_or_default();
                json!({ "answers": { question: { "answers": [answer] } } })
            }
            ("item/tool/requestUserInput", _) => {
                let question = pending.params.pointer("/questions/0/id").and_then(Value::as_str).unwrap_or_default();
                json!({ "answers": { question: { "answers": [] } } })
            }
            (_, Reply::Approve) => json!({ "decision": "accept" }),
            (_, _) => json!({ "decision": "decline" }),
        }
    }

    fn resolve(&mut self, approval: u64, reply: Reply) {
        let Some(pending) = self.approvals.remove(&approval) else { return };
        let payload = Self::payload(&pending, &reply);
        self.agent.respond(pending.request.clone(), payload);
        let outcome = match &reply {
            Reply::Approve => "Approved.".to_string(),
            Reply::Decline => "Declined.".to_string(),
            Reply::Answer(answer) => format!("Answered: {}", cut(answer, 100)),
        };
        self.send(Outbound::ApprovalClosed { chat: pending.chat, id: approval, outcome });
    }

    fn answer(&mut self, chat: &str, approval: u64, choice: usize) {
        let Some(pending) = self.approvals.get(&approval) else {
            self.notice(chat, "That request is no longer open.");
            return;
        };
        if pending.chat != chat {
            return;
        }
        let Some(reply) = pending.replies.get(choice).cloned() else { return };
        self.resolve(approval, reply);
    }

    /// `yes`/`no`/a number/free text, when the chat has an open approval. Returns true when the
    /// message was taken as the answer.
    fn answer_by_text(&mut self, chat: &str, text: &str) -> bool {
        let Some((&approval, pending)) = self.approvals.iter().filter(|(_, p)| p.chat == chat).max_by_key(|(id, _)| **id) else {
            return false;
        };
        let word = text.trim().to_ascii_lowercase();
        if pending.free_text && !word.starts_with('/') {
            let reply = if matches!(word.as_str(), "no" | "decline") { Reply::Decline } else { Reply::Answer(text.trim().to_string()) };
            self.resolve(approval, reply);
            return true;
        }
        let decline = pending.replies.len() - 1;
        let choice = match word.as_str() {
            "yes" | "y" | "approve" | "ok" | "✅" if pending.replies.len() == 2 => Some(0),
            "no" | "n" | "decline" | "❌" => Some(decline),
            number => number.parse::<usize>().ok().and_then(|n| n.checked_sub(1)).filter(|n| *n < pending.replies.len()),
        };
        match choice {
            Some(choice) => {
                let reply = pending.replies[choice].clone();
                self.resolve(approval, reply);
                true
            }
            None => false,
        }
    }

    pub fn on_tick(&mut self) {
        let now = Instant::now();
        if now.duration_since(self.airgap_read) >= self.config.airgap_interval {
            self.airgapped = (self.airgap)();
            self.airgap_read = now;
        }
        let mut drafts = Vec::new();
        for turn in self.turns.values_mut() {
            let since = now.duration_since(turn.last_draft);
            if (turn.dirty && since >= self.config.draft_interval) || since >= self.config.draft_refresh {
                turn.dirty = false;
                turn.last_draft = now;
                drafts.push(Outbound::Draft { chat: turn.chat.clone(), text: turn.draft() });
            }
        }
        for draft in drafts {
            self.send(draft);
        }
        let expired: Vec<u64> = self.approvals.iter().filter(|(_, p)| p.deadline <= now).map(|(id, _)| *id).collect();
        for approval in expired {
            if let Some(pending) = self.approvals.remove(&approval) {
                self.agent.respond(pending.request.clone(), Self::payload(&pending, &Reply::Decline));
                self.send(Outbound::ApprovalClosed {
                    chat: pending.chat,
                    id: approval,
                    outcome: "Declined: no answer in time.".to_string(),
                });
            }
        }
    }
}
