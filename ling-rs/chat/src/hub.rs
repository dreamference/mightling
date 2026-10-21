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
use crate::attachments;
use crate::attachments::Media;
use crate::attachments::Uploaded;
use crate::limits;
use crate::limits::Limits;
use crate::limits::Verdict;
use crate::limits::Watch;
use ling_web_server::chat_settings::ChatSettings;

use crate::store::ThreadRef;
use crate::store::Threads;

/// What an adapter hands the hub, from a paired user only.
#[derive(Clone, Debug, PartialEq)]
pub enum Inbound {
    /// A message, or a command (`/new`, …).
    Text { chat: String, text: String },
    /// A message with attachments (its caption as `text`), and what the adapter could not take,
    /// named for the user ("a sticker").
    Media { chat: String, text: String, attachments: Vec<Media>, dropped: Vec<String> },
    /// The messenger's own stop control (Telegram's button on a streaming answer).
    Stop { chat: String },
    /// A button pressed or a reaction on an approval: `choice` indexes its options.
    /// `approval` is the nonce the hub gave the request (never a counter, MOONSHOT §3.2).
    Answer { chat: String, approval: String, choice: usize },
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
    Approval { chat: String, id: String, text: String, options: Vec<String> },
    /// An approval was answered, declined for lack of an answer, or resolved elsewhere.
    ApprovalClosed { chat: String, id: String, outcome: String },
    /// A topic's thread got its name (from the first message): the topic is renamed after it.
    ThreadNamed { chat: String, name: String },
    /// A file the agent made, sent because the user said so (MOONSHOT §3.7).
    File { chat: String, name: String, bytes: Vec<u8> },
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
            | Outbound::ThreadNamed { chat, .. }
            | Outbound::File { chat, .. }
            | Outbound::TurnEnded { chat } => chat,
        }
    }
}

/// The adapter a chat key belongs to: the part before the first `:`.
pub fn adapter_of(chat: &str) -> &str {
    chat.split(':').next().unwrap_or_default()
}

/// A Telegram topic's id in a chat key (`telegram:<chat id>/<topic id>`, MOONSHOT §3.1): each topic
/// is its own conversation, with its own thread. Other keys have none.
pub fn topic_of(chat: &str) -> Option<&str> {
    let rest = chat.strip_prefix("telegram:")?;
    rest.split_once('/').map(|(_, topic)| topic).filter(|topic| !topic.is_empty())
}

/// A chat key without its topic.
pub fn base_of(chat: &str) -> &str {
    match topic_of(chat) {
        Some(topic) => &chat[..chat.len() - topic.len() - 1],
        None => chat,
    }
}

pub const HELP: &str = "Mightling, from your phone. Each conversation is an Ask thread on your machine.\n\
/new — the next message starts a new thread\n\
/stop — stop the running answer\n\
/threads — your recent threads\n\
/use <n> — continue thread n from /threads\n\
/status — what Mightling is doing\n\
/cavemode [off|lite|full|ultra] — how terse this thread's answers are; /cavemode default <level> for new threads\n\
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
    /// How often the air-gap level is read (and the chat settings, when `reload_settings`).
    pub airgap_interval: Duration,
    /// The `[chat]` settings of the user's configuration (`ling_web_server::chat_settings`).
    pub settings: ChatSettings,
    /// The user's configuration, read again for the settings with the air gap; None in tests.
    pub settings_file: Option<PathBuf>,
    /// After "answer now", how long before the interrupt; the final turn's time (limits.rs).
    pub grace: Duration,
    pub final_budget: Duration,
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
            settings: ChatSettings::default(),
            settings_file: None,
            grace: Duration::from_millis(limits::GRACE_MS),
            final_budget: Duration::from_millis(limits::FINAL_BUDGET_MS),
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
    /// Its bounds (limits.rs).
    watch: Watch,
    /// Why the bridge interrupted it, when it did.
    limit_reason: Option<String>,
    /// When it started (milliseconds since the epoch): files changed since are the agent's.
    started_ms: u64,
}

impl Turn {
    fn draft(&self) -> String {
        self.items.iter().map(|(_, text, _)| text.trim()).filter(|text| !text.is_empty()).collect::<Vec<_>>().join("\n\n")
    }

    /// The answer: the items marked final, or else the last message. A short last word after a
    /// longer earlier message ("The answer above stands.") brings that message with it: the phone
    /// shows only this, and in 2 of 56 Ask turns on 2026-10-20 the model gave its answer before a
    /// tool call and then only referred to it.
    fn answer(&self) -> String {
        let finals: Vec<&str> = self
            .items
            .iter()
            .filter(|(_, _, phase)| phase.as_deref() == Some("final_answer"))
            .map(|(_, text, _)| text.trim())
            .filter(|text| !text.is_empty())
            .collect();
        let last = if finals.is_empty() {
            self.items.iter().rev().map(|(_, text, _)| text.trim()).find(|text| !text.is_empty()).unwrap_or_default().to_string()
        } else {
            finals.join("\n\n")
        };
        if last.chars().count() >= SUBSTANTIVE {
            return last;
        }
        // Never the model's commentary between tool calls, and only a message long enough to be an
        // answer: a short "I'll check two pages" before a tool call is not one.
        let earlier = self
            .items
            .iter()
            .filter(|(_, _, phase)| phase.as_deref() != Some("commentary"))
            .map(|(_, text, _)| text.trim())
            .filter(|text| !text.is_empty() && *text != last && !last.contains(*text))
            .max_by_key(|text| text.chars().count());
        match earlier {
            Some(earlier) if earlier.chars().count() >= EARLIER_ANSWER => format!("{earlier}\n\n{last}"),
            _ => last,
        }
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

/// Files offered back after one answer, at most.
const MAX_OFFERS: usize = 3;

/// The largest file each messenger takes from the bridge: Telegram's 50 MB for a bot, and the
/// homeserver's usual 20 MB for Matrix.
pub fn file_limit(adapter: &str) -> u64 {
    match adapter {
        "telegram" => 50 * 1024 * 1024,
        _ => 20 * 1024 * 1024,
    }
}

/// "12 KB", "3.4 MB".
pub fn size(bytes: u64) -> String {
    if bytes < 1024 * 1024 {
        format!("{} KB", bytes.div_ceil(1024).max(1))
    } else {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    }
}

/// A final answer shorter than this is taken as a pointer to an earlier, longer message.
const SUBSTANTIVE: usize = 200;
/// An earlier message at least this long is taken as the answer such a last word points at.
const EARLIER_ANSWER: usize = 400;

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
    /// When it was asked, in order.
    seq: u64,
    /// The bridge's own question, answered here rather than by the agent: a file to send back.
    local: Option<Local>,
}

/// What the bridge itself asks the user to confirm.
#[derive(Clone, Debug)]
enum Local {
    /// A file the agent made, to send into the chat (MOONSHOT §3.7).
    SendFile { thread: String, name: String },
}

pub struct Hub {
    config: HubConfig,
    agent: AgentHandle,
    threads: Threads,
    adapters: HashMap<String, mpsc::UnboundedSender<Outbound>>,
    turns: HashMap<String, Turn>,
    /// Messages that arrived while a turn ran and could not steer it, as turn inputs.
    queued: HashMap<String, Vec<Value>>,
    /// The names the user's own attachments were saved under, per thread: never offered back.
    uploaded: HashMap<String, HashSet<String>>,
    /// Pending approvals by their nonce.
    approvals: HashMap<String, Pending>,
    /// The order approvals were asked in, for a typed "yes" (the newest open one).
    next_approval: u64,
    /// Nonces of approvals that were answered or closed, so a late press is told so; a nonce the
    /// hub never issued is ignored.
    closed: std::collections::VecDeque<String>,
    /// Threads this connection is subscribed to (started or resumed since it was made).
    subscribed: HashSet<String>,
    airgap: Box<dyn Fn() -> bool + Send>,
    airgapped: bool,
    airgap_read: Instant,
}

fn text_input(text: &str) -> Value {
    json!([{ "type": "text", "text": text, "text_elements": [] }])
}

/// Several queued inputs as one turn's: their texts joined by blank lines, then every image.
fn merged(inputs: Vec<Value>) -> Value {
    let mut texts = Vec::new();
    let mut rest = Vec::new();
    for item in inputs.into_iter().flat_map(|input| input.as_array().cloned().unwrap_or_default()) {
        match item.get("type").and_then(Value::as_str) {
            Some("text") => texts.push(item.get("text").and_then(Value::as_str).unwrap_or_default().to_string()),
            _ => rest.push(item),
        }
    }
    let mut input = Vec::new();
    let text = texts.into_iter().filter(|t| !t.trim().is_empty()).collect::<Vec<_>>().join("\n\n");
    if !text.is_empty() {
        input.push(json!({ "type": "text", "text": text, "text_elements": [] }));
    }
    input.extend(rest);
    Value::Array(input)
}

fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// "45s", "2m10s", "16h00m".
fn duration(ms: u64) -> String {
    let s = ms / 1000;
    if s < 60 {
        format!("{s}s")
    } else if s < 3600 {
        format!("{}m{:02}s", s / 60, s % 60)
    } else {
        format!("{}h{:02}m", s / 3600, (s % 3600) / 60)
    }
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
            uploaded: HashMap::new(),
            closed: std::collections::VecDeque::new(),
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

    /// An approval is over: the adapter shows the outcome, and its nonce is remembered as closed.
    fn close(&mut self, chat: String, id: String, outcome: &str) {
        self.closed.push_back(id.clone());
        while self.closed.len() > 200 {
            self.closed.pop_front();
        }
        self.send(Outbound::ApprovalClosed { chat, id, outcome: outcome.to_string() });
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
            Inbound::Media { chat, text, attachments, dropped } => {
                if adapter_of(&chat) == "telegram" && self.airgapped {
                    self.notice(
                        &chat,
                        "The air gap is on, so Telegram is paused: it would carry this conversation off your machine. Use Matrix, or turn the air gap off on the machine.",
                    );
                    return;
                }
                if let Some(line) = attachments::dropped_notice(&dropped) {
                    self.notice(&chat, line);
                }
                if !attachments.is_empty() {
                    self.media(&chat, &text, attachments).await;
                } else if !text.trim().is_empty() {
                    self.message(&chat, text.trim()).await;
                }
            }
            Inbound::Stop { chat } => self.stop(&chat).await,
            Inbound::Answer { chat, approval, choice } => self.answer(&chat, &approval, choice),
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
            "/threads" | "/use" if topic_of(chat).is_some() => {
                // Each topic is one thread: the list is the topics, and switching is tapping one.
                let base = base_of(chat);
                let mut names: Vec<String> = self
                    .threads
                    .chats
                    .iter()
                    .filter(|(key, _)| base_of(key) == base && topic_of(key).is_some())
                    .filter_map(|(key, c)| {
                        let id = c.current.as_ref()?;
                        let name = c.recent.iter().find(|t| &t.id == id).map(|t| t.name.clone()).unwrap_or_default();
                        let marker = if key == chat { " (this topic)" } else { "" };
                        Some(format!("• {}{marker}", if name.is_empty() { "(unnamed)" } else { name.as_str() }))
                    })
                    .collect();
                names.sort();
                let list = if names.is_empty() { String::new() } else { format!("{}\n", names.join("\n")) };
                self.notice(chat, format!("{list}Each topic is one thread: open a topic to continue it, /new for another."));
            }
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
                        let entry = self.threads.chats.entry(chat.to_string()).or_default();
                        entry.current = Some(thread.id.clone());
                        entry.last_ms = Some(now_ms());
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
            "/cavemode" => {
                let rest = words.collect::<Vec<_>>().join(" ");
                let answer = match self.agent.cavemode(self.current(chat), rest).await {
                    Ok(said) | Err(said) => said,
                };
                self.notice(chat, answer);
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

    /// A message after a long silence goes to a new thread, said in one line (§16.2). Then the
    /// conversation has moved now.
    fn fresh_after_silence(&mut self, chat: &str) {
        let now = now_ms();
        // A topic is the user saying which thread a message belongs to: no gap rule (MOONSHOT D2).
        if topic_of(chat).is_some() {
            self.threads.chats.entry(chat.to_string()).or_default().last_ms = Some(now);
            return;
        }
        let gap = self.config.settings.new_thread_after.map(|gap| gap.as_millis() as u64);
        let running = self.current(chat).is_some_and(|thread| self.thread_running(&thread) || self.queued.contains_key(&thread));
        let entry = self.threads.chats.entry(chat.to_string()).or_default();
        let silence = entry.last_ms.map(|last| now.saturating_sub(last));
        entry.last_ms = Some(now);
        let Some(gap) = gap else { return };
        if entry.current.is_none() || running || silence.is_some_and(|silence| silence < gap) {
            return;
        }
        entry.current = None;
        let _ = self.threads.save(&self.config.dir);
        let since = silence.map(|ms| format!(" (the last message was {} ago)", duration(ms))).unwrap_or_default();
        self.notice(chat, format!("New thread{since}; /use 2 to go back."));
    }

    async fn ensure_thread(&mut self, chat: &str, first: &str) -> Result<String, String> {
        if let Some(thread) = self.current(chat) {
            if !self.subscribed.contains(&thread) {
                self.agent.request("thread/resume", json!({ "threadId": thread })).await?;
                self.subscribed.insert(thread.clone());
            }
            return Ok(thread);
        }
        // `chat`: Ask with the phone block (MIGHTLING_CHAT §16.3); `ask` from a `ling web` without it.
        let started = match self.agent.request("thread/start", json!({ "prompt": "chat" })).await {
            Err(err) if err.contains("no prompt named") => self.agent.request("thread/start", json!({ "prompt": "ask" })).await?,
            other => other?,
        };
        let thread = started.pointer("/thread/id").and_then(Value::as_str).ok_or("thread/start named no thread")?.to_string();
        self.subscribed.insert(thread.clone());
        let name = thread_name(first);
        let _ = self.agent.request("thread/name/set", json!({ "threadId": thread, "name": name })).await;
        if topic_of(chat).is_some() {
            self.send(Outbound::ThreadNamed { chat: chat.to_string(), name: name.clone() });
        }
        self.threads.started(chat, ThreadRef { id: thread.clone(), name }, self.config.max_threads);
        let _ = self.threads.save(&self.config.dir);
        Ok(thread)
    }

    async fn message(&mut self, chat: &str, text: &str) {
        self.fresh_after_silence(chat);
        let thread = match self.ensure_thread(chat, text).await {
            Ok(thread) => thread,
            Err(err) => {
                self.notice(chat, format!("Mightling is not reachable right now: {err}"));
                return;
            }
        };
        self.deliver(chat, &thread, text_input(text)).await;
    }

    /// A message with attachments: each goes into the thread's folder through `ling web`, then the
    /// turn is built from what came back (attachments.rs). Nothing usable: the bridge says so.
    async fn media(&mut self, chat: &str, text: &str, files: Vec<Media>) {
        // Recordings first: each is transcribed on this machine and its text joins the question,
        // with a line saying what was heard (MOONSHOT §3.4).
        let (recordings, files): (Vec<Media>, Vec<Media>) = files.into_iter().partition(Media::is_audio);
        let mut text = text.trim().to_string();
        for recording in recordings {
            match self.agent.transcribe(&recording.mime, recording.bytes).await {
                Ok(heard) => {
                    self.notice(chat, attachments::heard_notice(&heard));
                    if !text.is_empty() {
                        text.push_str("\n\n");
                    }
                    text.push_str(&heard);
                }
                Err(reason) => self.notice(chat, attachments::not_heard_notice(&reason)),
            }
        }
        if files.is_empty() {
            if !text.is_empty() {
                self.message(chat, &text).await;
            }
            return;
        }
        let text = text.as_str();
        let first = if text.trim().is_empty() { "(image)" } else { text.trim() };
        self.fresh_after_silence(chat);
        let thread = match self.ensure_thread(chat, first).await {
            Ok(thread) => thread,
            Err(err) => {
                self.notice(chat, format!("Mightling is not reachable right now: {err}"));
                return;
            }
        };
        let mut uploads = Vec::new();
        for file in files {
            let image = file.is_image();
            let saved = self.agent.upload(&thread, &file.name, image, file.bytes).await;
            match &saved {
                Ok(saved) => {
                    let name = std::path::Path::new(&saved.path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                    self.uploaded.entry(thread.clone()).or_default().insert(name);
                }
                Err(err) => eprintln!("ling chat: could not save {} in the thread's folder: {err}", file.name),
            }
            uploads.push(Uploaded { name: file.name, image, saved });
        }
        match attachments::compose(text, &uploads) {
            Ok(input) => self.deliver(chat, &thread, input).await,
            Err(reply) => self.notice(chat, reply),
        }
    }

    /// Steers the running turn with `input`, queues it if it cannot, or starts a turn with it.
    async fn deliver(&mut self, chat: &str, thread: &str, input: Value) {
        if let Some(turn) = self.turns.get(thread) {
            // The final turn after a time-out is not added to: the message waits for the next one.
            let expected = turn.id.clone().filter(|_| !turn.watch.final_turn);
            let steered = match expected {
                Some(turn_id) => self
                    .agent
                    .request("turn/steer", json!({ "threadId": thread, "input": input, "expectedTurnId": turn_id }))
                    .await
                    .is_ok(),
                None => false,
            };
            if !steered {
                self.queued.entry(thread.to_string()).or_default().push(input);
                self.notice(chat, "Noted: this goes in as soon as the current answer is done.");
            }
            return;
        }
        self.start_turn(chat, thread, input).await;
    }

    async fn start_turn(&mut self, chat: &str, thread: &str, input: Value) {
        self.turns.insert(
            thread.to_string(),
            Turn {
                chat: chat.to_string(),
                id: None,
                items: Vec::new(),
                error: None,
                dirty: false,
                last_draft: Instant::now(),
                watch: Watch::new(now_ms()),
                limit_reason: None,
                started_ms: now_ms(),
            },
        );
        self.send(Outbound::Draft { chat: chat.to_string(), text: String::new() });
        match self.agent.request("turn/start", json!({ "threadId": thread, "input": input })).await {
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
            "item/started" => {
                if let Some(turn) = self.turns.get_mut(&thread)
                    && let Some(kind) = params.pointer("/item/type").and_then(Value::as_str)
                    && limits::is_tool(kind)
                {
                    let item = params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default();
                    let command = (kind == "commandExecution").then(|| params.pointer("/item/command").and_then(Value::as_str).unwrap_or("a command"));
                    turn.watch.tool_started(item, command, now_ms());
                }
            }
            "item/completed" => {
                if let Some(turn) = self.turns.get_mut(&thread) {
                    if params.pointer("/item/type").and_then(Value::as_str) == Some("agentMessage") {
                        turn.completed_item(&params["item"]);
                    }
                    turn.watch.tool_completed(params.pointer("/item/id").and_then(Value::as_str).unwrap_or_default());
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
                let resolved: Vec<String> = self.approvals.iter().filter(|(_, p)| p.local.is_none() && p.request == request).map(|(id, _)| id.clone()).collect();
                for id in resolved {
                    if let Some(pending) = self.approvals.remove(&id) {
                        self.close(pending.chat, id, "Answered elsewhere.");
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
        self.threads.chats.entry(chat.clone()).or_default().last_ms = Some(now_ms());
        let _ = self.threads.save(&self.config.dir);
        let answer = turn.answer();
        let status = turn_value.get("status").and_then(Value::as_str).unwrap_or("completed");
        // Interrupted by its bounds (limits.rs): a final turn asks for the answer from what was found;
        // a final turn out of time too ends with what it wrote.
        if let Some(reason) = turn.limit_reason.clone().filter(|_| status != "completed") {
            if !turn.watch.final_turn {
                // What it wrote so far is kept as a message, not left in a draft that vanishes (D9).
                if !answer.is_empty() {
                    self.send(Outbound::TurnEnded { chat: chat.clone() });
                    self.send(Outbound::Message { chat: chat.clone(), markdown: format!("{answer}\n\n_(cut off: {reason})_") });
                }
                self.start_turn(&chat, thread, text_input(&limits::final_text(&reason))).await;
                if let Some(next) = self.turns.get_mut(thread) {
                    next.watch = Watch::final_turn(now_ms());
                }
                return;
            }
            self.send(Outbound::TurnEnded { chat: chat.clone() });
            if !answer.is_empty() {
                self.send(Outbound::Message { chat: chat.clone(), markdown: answer });
            }
            self.notice(&chat, limits::GAVE_UP);
            self.after_turn(&chat, thread).await;
            return;
        }
        self.send(Outbound::TurnEnded { chat: chat.clone() });
        let wrote = !answer.is_empty();
        match status {
            // Stopped (the stop button, /stop): what it wrote so far is kept, marked (MOONSHOT D9).
            "interrupted" if wrote => self.send(Outbound::Message { chat: chat.clone(), markdown: format!("{answer}\n\n_(stopped)_") }),
            _ if wrote => self.send(Outbound::Message { chat: chat.clone(), markdown: answer.clone() }),
            _ => {}
        }
        match status {
            "interrupted" if wrote => {}
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
        if status == "completed" && wrote {
            self.offer_files(&chat, thread, turn.started_ms, &answer).await;
        }
        self.after_turn(&chat, thread).await;
    }

    /// Files the agent made during the turn and named in its answer are offered, each sent only
    /// when the user says so (MOONSHOT §3.7). Never at `/airgapped on`; never the user's own
    /// uploads; one too large for the messenger is named and kept.
    async fn offer_files(&mut self, chat: &str, thread: &str, since_ms: u64, answer: &str) {
        if self.airgapped {
            return;
        }
        let Ok(files) = self.agent.made_files(thread, since_ms.saturating_sub(1_000)).await else { return };
        let uploaded = self.uploaded.get(thread).cloned().unwrap_or_default();
        let limit = file_limit(adapter_of(chat));
        let mut offered = 0;
        for file in files {
            let base = file.name.rsplit('/').next().unwrap_or(&file.name).to_string();
            if uploaded.contains(&file.name) || !answer.contains(&base) || offered >= MAX_OFFERS {
                continue;
            }
            offered += 1;
            if file.bytes > limit {
                self.notice(chat, format!("Mightling made {} ({}), larger than the {} this messenger takes, so it stays in the thread's folder.", file.name, size(file.bytes), size(limit)));
                continue;
            }
            self.next_approval += 1;
            let id = ling_web_server::auth::random_hex(8);
            let text = format!("Mightling made {} ({}). Send it here?", file.name, size(file.bytes));
            self.approvals.insert(
                id.clone(),
                Pending {
                    local: Some(Local::SendFile { thread: thread.to_string(), name: file.name.clone() }),
                    chat: chat.to_string(),
                    thread: thread.to_string(),
                    request: Value::Null,
                    method: "local/sendFile".to_string(),
                    params: Value::Null,
                    replies: vec![Reply::Approve, Reply::Decline],
                    free_text: false,
                    deadline: Instant::now() + self.config.approval_timeout,
                    seq: self.next_approval,
                },
            );
            self.send(Outbound::Approval { chat: chat.to_string(), id, text, options: vec!["Send it".to_string(), "No".to_string()] });
        }
    }

    /// Fetches a file from the thread's folder and hands it to the chat's adapter, off the hub's
    /// loop (a large file must not hold the conversation up).
    fn send_file(&self, chat: &str, thread: &str, name: &str) {
        let Some(adapter) = self.adapters.get(adapter_of(chat)).cloned() else { return };
        let (agent, chat, thread, name) = (self.agent.clone(), chat.to_string(), thread.to_string(), name.to_string());
        tokio::spawn(async move {
            match agent.made_file(&thread, &name).await {
                Ok(bytes) => {
                    let _ = adapter.send(Outbound::File { chat, name, bytes });
                }
                Err(err) => {
                    let _ = adapter.send(Outbound::Notice { chat, text: format!("I couldn't send {name}: {err}") });
                }
            }
        });
    }

    /// Each running turn against its bounds: told to answer now, then interrupted (limits.rs).
    fn check_limits(&mut self) {
        let limits = Limits {
            grace_ms: self.config.grace.as_millis() as u64,
            final_ms: self.config.final_budget.as_millis() as u64,
            ..Limits::from_settings(&self.config.settings)
        };
        let now = now_ms();
        let mut actions = Vec::new();
        for (thread, turn) in self.turns.iter_mut() {
            let Some(turn_id) = turn.id.clone() else { continue };
            match turn.watch.check(now, &limits) {
                Some(Verdict::Steer(text)) => actions.push((turn.chat.clone(), thread.clone(), turn_id, Some(text), None)),
                Some(Verdict::Interrupt(reason)) => {
                    turn.limit_reason = Some(reason.clone());
                    let notice = (!turn.watch.final_turn).then(|| limits::interrupted_notice(&reason));
                    actions.push((turn.chat.clone(), thread.clone(), turn_id, None, notice));
                }
                None => {}
            }
        }
        for (chat, thread, turn_id, steer, notice) in actions {
            let agent = self.agent.clone();
            match steer {
                Some(text) => {
                    tokio::spawn(async move {
                        let _ = agent.request("turn/steer", json!({ "threadId": thread, "input": text_input(&text), "expectedTurnId": turn_id })).await;
                    });
                }
                None => {
                    if let Some(line) = notice {
                        self.notice(&chat, line);
                    }
                    tokio::spawn(async move {
                        let _ = agent.request("turn/interrupt", json!({ "threadId": thread, "turnId": turn_id })).await;
                    });
                }
            }
        }
    }

    /// After a turn's answer: its approvals close, and messages that waited start the next turn.
    async fn after_turn(&mut self, chat: &str, thread: &str) {
        let chat = chat.to_string();
        // Approvals of a finished turn can no longer be answered.
        let stale: Vec<String> = self.approvals.iter().filter(|(_, p)| p.local.is_none() && p.thread == thread).map(|(id, _)| id.clone()).collect();
        for id in stale {
            if let Some(pending) = self.approvals.remove(&id) {
                self.close(pending.chat, id, "The turn ended.");
            }
        }
        if let Some(queued) = self.queued.remove(thread)
            && !queued.is_empty()
        {
            self.start_turn(&chat, thread, merged(queued)).await;
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
        // An unguessable id: a callback naming one the hub never issued is ignored (MOONSHOT §3.2).
        let approval = ling_web_server::auth::random_hex(8);
        self.approvals.insert(
            approval.clone(),
            Pending {
                local: None,
                chat: chat.clone(),
                thread,
                request: id,
                method: method.to_string(),
                params,
                replies,
                free_text,
                deadline: Instant::now() + self.config.approval_timeout,
                seq: self.next_approval,
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

    fn resolve(&mut self, approval: &str, reply: Reply) {
        let Some(pending) = self.approvals.remove(approval) else { return };
        if let Some(local) = pending.local.clone() {
            let outcome = match (&local, &reply) {
                (Local::SendFile { thread, name }, Reply::Approve) => {
                    self.send_file(&pending.chat, thread, name);
                    "Sending it."
                }
                (Local::SendFile { .. }, _) => "Not sent; it stays in the thread's folder.",
            };
            self.close(pending.chat, approval.to_string(), outcome);
            return;
        }
        let payload = Self::payload(&pending, &reply);
        self.agent.respond(pending.request.clone(), payload);
        let outcome = match &reply {
            Reply::Approve => "Approved.".to_string(),
            Reply::Decline => "Declined.".to_string(),
            Reply::Answer(answer) => format!("Answered: {}", cut(answer, 100)),
        };
        self.close(pending.chat, approval.to_string(), &outcome);
    }

    fn answer(&mut self, chat: &str, approval: &str, choice: usize) {
        let Some(pending) = self.approvals.get(approval) else {
            if self.closed.iter().any(|closed| closed == approval) {
                self.notice(chat, "That request is no longer open.");
            }
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
        let Some((approval, pending)) = self.approvals.iter().filter(|(_, p)| p.chat == chat).max_by_key(|(_, p)| p.seq) else {
            return false;
        };
        let approval = approval.clone();
        let word = text.trim().to_ascii_lowercase();
        if pending.free_text && !word.starts_with('/') {
            let reply = if matches!(word.as_str(), "no" | "decline") { Reply::Decline } else { Reply::Answer(text.trim().to_string()) };
            self.resolve(&approval, reply);
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
                self.resolve(&approval, reply);
                true
            }
            None => false,
        }
    }

    pub fn on_tick(&mut self) {
        let now = Instant::now();
        self.check_limits();
        if now.duration_since(self.airgap_read) >= self.config.airgap_interval {
            self.airgapped = (self.airgap)();
            self.airgap_read = now;
            if let Some(file) = &self.config.settings_file {
                self.config.settings = ChatSettings::load_from(Some(file));
            }
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
        let expired: Vec<String> = self.approvals.iter().filter(|(_, p)| p.deadline <= now).map(|(id, _)| id.clone()).collect();
        for approval in expired {
            if let Some(pending) = self.approvals.remove(&approval) {
                if pending.local.is_some() {
                    self.close(pending.chat, approval, "No answer in time; the file stays in the thread's folder.");
                    continue;
                }
                self.agent.respond(pending.request.clone(), Self::payload(&pending, &Reply::Decline));
                self.close(pending.chat, approval, "Declined: no answer in time.");
            }
        }
    }
}
