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

use crate::repo_notice::BranchName;
use crate::repo_notice::RefusedAction;
use crate::repo_notice::RepoName;
use crate::repo_notice::RepoNotice;
use crate::repo_notice::RepoNoticeKind;
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
    /// A fixed-template repository notice for Telegram or Discord (CHAT_REPOS §5.3, §12.2): never
    /// free text.
    RepoNotice { chat: String, notice: RepoNotice },
    /// Fixed refusal when typed text arrives in a repository's topic or while a repository is
    /// current for the chat (`Repo work happens in the app.` with the Open button, CHAT_REPOS §2).
    RepoTypedRefusal { chat: String, repo: RepoName },
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
            | Outbound::RepoNotice { chat, .. }
            | Outbound::RepoTypedRefusal { chat, .. }
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

/// Whether `adapter`'s messenger carries the conversation off this machine (Telegram, Discord): it
/// is paused at `/airgapped on`, and typed text there is refused while a repository is current.
pub fn off_node(adapter: &str) -> bool {
    matches!(adapter, "telegram" | "discord")
}

/// The one line an off-node messenger gets at `/airgapped on` instead of an answer.
pub fn airgap_pause(adapter: &str) -> String {
    let name = if adapter == "discord" { "Discord" } else { "Telegram" };
    format!("The air gap is on, so {name} is paused: it would carry this conversation off your machine. Use Matrix, or turn the air gap off on the machine.")
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
/night add <task> — queue a task for Night Shift tonight, with this thread's last exchange; /night status — the queue\n\
/push — push this chat's repository branch to the forge, after you confirm\n\
/merge — merge its branch into the default branch, after you confirm\n\
/help — this list";

/// How long a `/push` or `/merge` confirmation stays open in the chat: inside the ten minutes
/// `ling web`'s confirmation nonce lives (CHAT_REPOS §4.7).
pub const REPO_CONFIRM_TIMEOUT: Duration = Duration::from_secs(300);

/// A query parameter, percent-encoded (chat keys carry `:`, `!`, `/` and `-`).
fn query_escape(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// A checkout's thread id as `ling web` names it: short, and only letters, digits, `-` and `_`.
fn valid_thread(thread: &str) -> bool {
    !thread.is_empty() && thread.len() <= 64 && thread.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

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
    /// How often Night Shift's reports are looked for.
    pub report_interval: Duration,
    /// How often pending Mini App launch-pair requests are looked for.
    pub pair_interval: Duration,
    /// How often repository notices (`GET /api/repos/notices`) are looked for (default 2 s).
    pub repo_notice_interval: Duration,
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
            report_interval: Duration::from_secs(300),
            pair_interval: Duration::from_secs(300),
            repo_notice_interval: Duration::from_secs(2),
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

/// When no repository can take a phone's Night Shift task.
pub const NO_NIGHT_REPOS: &str = "Night Shift has no repository to queue in from the phone yet. Queue one task from a checkout on the node (`ling`, then /night add), or list it as night_repos = [\"~/path/to/repo\"] under [chat] in ~/.config/dreamference/config.toml.";

/// A phone's Night Shift task with the thread's last exchange after it, so the night knows what
/// "this" was.
pub fn with_context(task: &str, exchange: Option<&(String, String)>) -> String {
    let Some((question, answer)) = exchange else { return task.trim().to_string() };
    let mut text = format!("{}\n\nContext, from the phone conversation this was asked in:", task.trim());
    if !question.trim().is_empty() {
        text.push_str(&format!("\n\nQuestion: {}", cut(question.trim(), 1_500)));
    }
    if !answer.trim().is_empty() {
        text.push_str(&format!("\n\nAnswer: {}", cut(answer.trim(), 3_000)));
    }
    text
}

/// `/api/night/status` as lines for a phone.
pub fn night_status_text(value: &Value) -> String {
    let tasks = value.get("tasks").and_then(Value::as_array).cloned().unwrap_or_default();
    if tasks.is_empty() {
        return "Night Shift's queue is empty. /night add <task> queues one.".to_string();
    }
    let lines: Vec<String> = tasks
        .iter()
        .map(|t| {
            let field = |key: &str| t.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
            format!("• {} — {} [{}] {}", field("id"), field("status"), field("repo"), field("task"))
        })
        .collect();
    format!("Night Shift:\n{}", lines.join("\n"))
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
    /// A Night Shift task to queue in the repository the user picks (MOONSHOT §3.5).
    NightAdd { task: String },
    /// A Telegram Mini App first-launch device pairing request (CHAT_REPOS §12.1 G2).
    LaunchPair { id: String },
    /// `/push` or `/merge` (CHAT_REPOS §4.7): `confirm` is `ling web`'s single-use nonce, bound
    /// to this bridge's session, the thread, the intent and the commit and target it was issued
    /// for; it is sent only when the user presses the button.
    RepoConfirm { action: RefusedAction, thread: String, confirm: String, repo: RepoName, notified: bool },
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
    /// Pending Mini App launch-pair request ids already prompted to Telegram.
    seen_pairs: HashSet<String>,
    /// 2-second cache of `/api/repos/current` per chat key, refreshed on Switched / RepoOff notices.
    current_repo_cache: HashMap<String, (Instant, Option<RepoName>)>,
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
            seen_pairs: HashSet::new(),
            current_repo_cache: HashMap::new(),
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
        let mut reports = tokio::time::interval_at(tokio::time::Instant::now() + self.config.report_interval, self.config.report_interval);
        reports.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut pairs = tokio::time::interval_at(tokio::time::Instant::now() + self.config.pair_interval, self.config.pair_interval);
        pairs.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut repo_notices = tokio::time::interval_at(
            tokio::time::Instant::now() + self.config.repo_notice_interval,
            self.config.repo_notice_interval,
        );
        repo_notices.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
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
                _ = pairs.tick() => self.push_pending_pairs().await,
                _ = repo_notices.tick() => self.push_repo_notices().await,
                _ = reports.tick() => {
                    self.push_reports().await;
                    self.push_sync_notices().await;
                }
            }
        }
    }

    fn thread_running(&self, thread: &str) -> bool {
        self.turns.contains_key(thread)
    }

    fn current(&self, chat: &str) -> Option<String> {
        self.threads.chats.get(chat).and_then(|c| c.current.clone())
    }

    /// Saves `threads.json`, keeping any `last_topic` the Telegram adapter wrote and making
    /// `touched_chat` the last topic when it is a Telegram topic (`Threads::save_keeping_topics`).
    fn save_threads(&mut self, touched_chat: Option<&str>) {
        let _ = self.threads.save_keeping_topics(&self.config.dir, touched_chat);
    }

    pub async fn on_inbound(&mut self, message: Inbound) {
        // CHAT §3: read every minute and at every message. `airgap_read` stays the tick's clock.
        self.airgapped = (self.airgap)();
        match message {
            Inbound::Text { chat, text } => {
                if off_node(adapter_of(&chat)) && self.airgapped {
                    self.notice(&chat, &airgap_pause(adapter_of(&chat)));
                    return;
                }
                let trimmed = text.trim();
                if off_node(adapter_of(&chat))
                    && !trimmed.is_empty()
                    && !trimmed.starts_with('/')
                    && let Some(repo) = self.active_repo_for_chat(&chat).await
                {
                    self.send(Outbound::RepoTypedRefusal { chat, repo });
                    return;
                }
                if self.answer_by_text(&chat, &text) {
                    return;
                }
                if trimmed.starts_with('/') {
                    self.command(&chat, trimmed).await;
                } else if !trimmed.is_empty() {
                    self.message(&chat, trimmed).await;
                }
            }
            Inbound::Media { chat, text, attachments, dropped } => {
                if off_node(adapter_of(&chat)) && self.airgapped {
                    self.notice(&chat, &airgap_pause(adapter_of(&chat)));
                    return;
                }
                if off_node(adapter_of(&chat))
                    && let Some(repo) = self.active_repo_for_chat(&chat).await
                {
                    self.send(Outbound::RepoTypedRefusal { chat, repo });
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
                self.save_threads(Some(chat));
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
                        self.save_threads(Some(chat));
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
            "/push" => self.repo_confirm(chat, RefusedAction::Push).await,
            "/merge" => self.repo_confirm(chat, RefusedAction::Merge).await,
            "/night" => {
                let rest = text.trim()["/night".len()..].trim().to_string();
                self.night(chat, &rest).await;
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
            self.save_threads(Some(chat));
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
        self.save_threads(None);
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
        self.save_threads(Some(chat));
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
        self.save_threads(Some(&chat));
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

    /// `/night add <task>`, `/night status` (MOONSHOT §3.5, CHAT §16.8).
    async fn night(&mut self, chat: &str, rest: &str) {
        let (verb, task) = rest.split_once(char::is_whitespace).map(|(v, t)| (v, t.trim())).unwrap_or((rest, ""));
        match verb {
            "add" if !task.is_empty() => self.night_add(chat, task).await,
            "status" | "list" => {
                let answer = match self.agent.web("/api/night/status", None).await {
                    Ok(value) => night_status_text(&value),
                    Err(err) => format!("Night Shift's queue could not be read: {err}"),
                };
                self.notice(chat, answer);
            }
            _ => self.notice(chat, "/night add <task> queues a task for Night Shift tonight; /night status shows the queue."),
        }
    }

    /// Asks which repository and confirms before anything is queued: the bridge's own question,
    /// answered by a press or a reply. The task carries the thread's last exchange.
    async fn night_add(&mut self, chat: &str, task: &str) {
        let repos = match self.agent.web("/api/night/repos", None).await {
            Ok(value) => value.get("repos").and_then(Value::as_array).cloned().unwrap_or_default(),
            Err(err) => {
                self.notice(chat, format!("Night Shift's repositories could not be read: {err}"));
                return;
            }
        };
        let repos: Vec<(String, String)> = repos
            .iter()
            .filter_map(|r| Some((r.get("path")?.as_str()?.to_string(), r.get("name")?.as_str()?.to_string())))
            .take(8)
            .collect();
        if repos.is_empty() {
            self.notice(chat, NO_NIGHT_REPOS);
            return;
        }
        let task = with_context(task, last_exchange(self.agent.clone(), self.current(chat)).await.as_ref());
        let preview = cut(&task, 400);
        let (text, options, replies) = if repos.len() == 1 {
            let (path, name) = &repos[0];
            (
                format!("Queue this for Night Shift tonight, in {name}?\n\n{preview}"),
                vec!["Queue it".to_string(), "Cancel".to_string()],
                vec![Reply::Answer(path.clone()), Reply::Decline],
            )
        } else {
            let mut options: Vec<String> = repos.iter().map(|(_, name)| name.clone()).collect();
            options.push("Cancel".to_string());
            let mut replies: Vec<Reply> = repos.iter().map(|(path, _)| Reply::Answer(path.clone())).collect();
            replies.push(Reply::Decline);
            (format!("Queue this for Night Shift tonight? Choose the repository.\n\n{preview}"), options, replies)
        };
        self.next_approval += 1;
        let id = ling_web_server::auth::random_hex(8);
        let thread = self.current(chat).unwrap_or_default();
        self.approvals.insert(
            id.clone(),
            Pending {
                local: Some(Local::NightAdd { task }),
                chat: chat.to_string(),
                thread,
                request: Value::Null,
                method: "local/nightAdd".to_string(),
                params: Value::Null,
                replies,
                free_text: false,
                deadline: Instant::now() + self.config.approval_timeout,
                seq: self.next_approval,
            },
        );
        self.send(Outbound::Approval { chat: chat.to_string(), id, text, options });
    }

}

/// A thread's last question and answer, for a Night Shift task's context.
async fn last_exchange(agent: AgentHandle, thread: Option<String>) -> Option<(String, String)> {
    {
        let thread = thread?;
        let listed = agent.request("thread/turns/list", json!({ "threadId": thread, "limit": 1, "sortDirection": "desc" })).await.ok()?;
        let items = listed.pointer("/data/0/items")?.as_array()?.clone();
        let question = items
            .iter()
            .find(|i| i.get("type").and_then(Value::as_str) == Some("userMessage"))
            .and_then(|i| i.get("content")?.as_array().cloned())
            .map(|parts| parts.iter().filter_map(|p| p.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n"))
            .unwrap_or_default();
        let answer = items.iter().rev().filter(|i| i.get("type").and_then(Value::as_str) == Some("agentMessage")).find_map(|i| i.get("text").and_then(Value::as_str).map(str::to_string)).unwrap_or_default();
        (!question.trim().is_empty() || !answer.trim().is_empty()).then_some((question, answer))
    }
}

impl Hub {
    /// Queues a confirmed task through `ling web` and says what Night Shift said.
    fn night_queue(&self, chat: &str, repo: &str, task: &str) {
        let Some(adapter) = self.adapters.get(adapter_of(chat)).cloned() else { return };
        let (agent, chat, body) = (self.agent.clone(), chat.to_string(), json!({ "repo": repo, "task": task }));
        tokio::spawn(async move {
            let text = match agent.web("/api/night/add", Some(body)).await {
                Ok(value) => {
                    let lines: Vec<String> = value.get("lines").and_then(Value::as_array).map(|l| l.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
                    if value.get("queued") == Some(&Value::Bool(true)) { lines.join("\n") } else { format!("Night Shift did not queue it: {}", lines.join(" ")) }
                }
                Err(err) => format!("Night Shift did not queue it: {err}"),
            };
            let _ = adapter.send(Outbound::Notice { chat, text });
        });
    }

    /// The morning reports since the last one pushed, to every chat of each messenger the user
    /// named (`[chat] night_report_to`; Signal and Matrix by default, MOONSHOT §3.5). The first
    /// look only notes the newest report, so a bridge started fresh repeats no old one.
    pub async fn push_reports(&mut self) {
        let seen_file = self.config.dir.join("night-report-seen");
        let seen = std::fs::read_to_string(&seen_file).ok().map(|s| s.trim().to_string());
        let after = seen.clone().unwrap_or_default();
        let Ok(value) = self.agent.web(&format!("/api/night/reports?after={after}"), None).await else { return };
        let latest = value.get("latest").and_then(Value::as_str).map(str::to_string);
        if seen.is_none() {
            // No report yet is noted too ("" is before any), or the first one would only be noted.
            let _ = crate::store::write_private(&seen_file, latest.as_deref().unwrap_or_default());
            return;
        }
        let reports = value.get("reports").and_then(Value::as_array).cloned().unwrap_or_default();
        let targets: Vec<String> = {
            let mut keys: Vec<String> = self
                .threads
                .chats
                .keys()
                .map(|key| base_of(key).to_string())
                .filter(|key| self.config.settings.night_report_to.iter().any(|m| m == adapter_of(key)))
                .collect();
            keys.sort();
            keys.dedup();
            keys
        };
        for report in &reports {
            let (Some(name), Some(text)) = (report.get("name").and_then(Value::as_str), report.get("text").and_then(Value::as_str)) else { continue };
            for chat in &targets {
                if off_node(adapter_of(chat)) && self.airgapped {
                    continue;
                }
                self.send(Outbound::Message { chat: chat.clone(), markdown: format!("**Night Shift's report** ({})\n\n{text}", name.trim_end_matches(".md")) });
            }
            let _ = crate::store::write_private(&seen_file, name);
        }
    }

    /// Sync's notices since the last one passed on (specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.3): a
    /// turn on another device finished, or an approval waits there, to every chat of each
    /// messenger `[chat] sync_notices_to` names. Nothing while the hub is off (its route answers
    /// 404), never to Telegram at `/airgapped on`, and the first look only notes the newest.
    pub async fn push_sync_notices(&mut self) {
        let seen_file = self.config.dir.join("sync-notice-seen");
        let seen = std::fs::read_to_string(&seen_file).ok().and_then(|s| s.trim().parse::<u64>().ok());
        let Ok(value) = self.agent.web(&format!("/api/sync/notices?after={}", seen.unwrap_or(0)), None).await else { return };
        let latest = value.get("latest").and_then(Value::as_u64).unwrap_or(0);
        if seen.is_none() {
            let _ = crate::store::write_private(&seen_file, &latest.to_string());
            return;
        }
        let targets: Vec<String> = {
            let mut keys: Vec<String> = self
                .threads
                .chats
                .keys()
                .map(|key| base_of(key).to_string())
                .filter(|key| self.config.settings.sync_notices_to.iter().any(|m| m == adapter_of(key)))
                .collect();
            keys.sort();
            keys.dedup();
            keys
        };
        for notice in value.get("notices").and_then(Value::as_array).cloned().unwrap_or_default() {
            let (Some(seq), Some(text)) = (notice.get("seq").and_then(Value::as_u64), notice.get("text").and_then(Value::as_str)) else { continue };
            for chat in &targets {
                if off_node(adapter_of(chat)) && self.airgapped {
                    continue;
                }
                self.send(Outbound::Notice { chat: chat.clone(), text: format!("{text} (open Other devices in Mightling)") });
            }
            let _ = crate::store::write_private(&seen_file, &seq.to_string());
        }
    }

    /// Repository notices (`GET /api/repos/notices?after=<seq>`, CHAT_REPOS §5.3, §12.2 G14):
    /// last seq kept in `repo-notice-seen`, unparsable notices dropped, each parsed notice sent as
    /// `Outbound::RepoNotice` (never `Outbound::Notice`). Refreshes the 2 s current-repo cache on
    /// `Switched` and `RepoOff`.
    pub async fn push_repo_notices(&mut self) {
        let seen_file = self.config.dir.join("repo-notice-seen");
        let seen = std::fs::read_to_string(&seen_file)
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
            .unwrap_or(0);
        let Ok(value) = self.agent.web(&format!("/api/repos/notices?after={seen}"), None).await else { return };
        let server_seq = value.get("seq").and_then(Value::as_u64);
        let (list, mut highest) = if let Some(s) = server_seq
            && seen > 0
            && s < seen
        {
            // `ling web` restarted and its in-memory sequence reset: drain from 0.
            let Ok(reset_val) = self.agent.web("/api/repos/notices?after=0", None).await else { return };
            let s2 = reset_val.get("seq").and_then(Value::as_u64).unwrap_or(0);
            let items = reset_val.get("notices").and_then(Value::as_array).cloned().unwrap_or_default();
            (items, s2)
        } else {
            let items = value.get("notices").and_then(Value::as_array).cloned().unwrap_or_default();
            (items, server_seq.unwrap_or(seen))
        };

        for raw in &list {
            if let Some(item_seq) = raw.get("seq").and_then(Value::as_u64) {
                highest = highest.max(item_seq);
            }
            let Some(notice) = RepoNotice::parse(raw) else { continue };
            highest = highest.max(notice.seq);
            match &notice.kind {
                RepoNoticeKind::Switched { .. } => {
                    self.current_repo_cache
                        .insert(notice.chat_key.clone(), (Instant::now(), Some(notice.repo.clone())));
                }
                RepoNoticeKind::RepoOff { .. } => {
                    self.current_repo_cache
                        .insert(notice.chat_key.clone(), (Instant::now(), None));
                }
                _ => {}
            }
            if off_node(adapter_of(&notice.chat_key)) && self.airgapped {
                continue;
            }
            let chat = notice.chat_key.clone();
            self.send(Outbound::RepoNotice { chat, notice });
        }

        if highest != seen || !list.is_empty() {
            let _ = crate::store::write_private(&seen_file, &highest.to_string());
        }
    }

    /// Returns the active `RepoName` for `chat` when typed text in Telegram or Discord must be refused
    /// (`Repo work happens in the app.`):
    /// - when the message is in a repository's topic (`repo-topics.json`), or
    /// - when `/api/repos/current` names a repository for `chat` (cached for 2 s and refreshed on
    ///   `Switched` / `RepoOff`). Ask in a non-repository topic works as before.
    async fn active_repo_for_chat(&mut self, chat: &str) -> Option<RepoName> {
        if adapter_of(chat) == "discord" {
            return self.cached_current_repo(chat).await;
        }
        let (chat_id, topic) = crate::telegram::target(chat)?;
        if let Some(topic_id) = topic {
            if let Some(repo_str) = crate::store::repo_for_topic(&self.config.dir, chat_id, topic_id)
                && let Some(repo) = RepoName::parse(&repo_str)
            {
                return Some(repo);
            }
            return self.cached_current_repo(chat).await;
        }
        self.cached_current_repo(chat).await
    }

    async fn cached_current_repo(&mut self, chat_key: &str) -> Option<RepoName> {
        if let Some((at, cached)) = self.current_repo_cache.get(chat_key)
            && at.elapsed() < Duration::from_secs(2)
        {
            return cached.clone();
        }
        let (bridge, chat_param) = chat_key.split_once(':').filter(|(bridge, _)| off_node(bridge))?;
        let path = format!("/api/repos/current?bridge={bridge}&chat={chat_param}");
        let repo = self
            .agent
            .web(&path, None)
            .await
            .ok()
            .and_then(|v| v.get("repo").and_then(Value::as_str).and_then(RepoName::parse));
        self.current_repo_cache.insert(chat_key.to_string(), (Instant::now(), repo.clone()));
        repo
    }

    /// `/push` and `/merge` (CHAT_REPOS §4.7): finds the checkout this chat works in, asks
    /// `ling web` for a confirmation bound to its current commit and target, and asks the user with
    /// counts and validated names only. Nothing is pushed or merged until the button is pressed.
    async fn repo_confirm(&mut self, chat: &str, action: RefusedAction) {
        let no_repo = "No repository is open in this chat; open the app.";
        let Some((adapter, rest)) = chat.split_once(':') else { return };
        let mut path = format!("/api/repos/current?bridge={}&chat={}", query_escape(adapter), query_escape(rest));
        if adapter == "telegram"
            && let Some(repo) = self.active_repo_for_chat(chat).await
        {
            path.push_str(&format!("&repo={}", query_escape(&repo.to_string())));
        }
        let checkout = self.agent.web(&path, None).await.ok().and_then(|v| v.get("checkout").cloned()).unwrap_or(Value::Null);
        let (Some(thread), Some(repo), Some(branch), Some(base)) = (
            checkout.get("thread").and_then(Value::as_str).filter(|t| valid_thread(t)),
            checkout.get("repo").and_then(Value::as_str).and_then(RepoName::parse),
            checkout.get("branch").and_then(Value::as_str).and_then(BranchName::parse),
            checkout.get("base").and_then(Value::as_str).and_then(BranchName::parse),
        ) else {
            self.notice(chat, no_repo);
            return;
        };
        let notified = checkout.get("notified").and_then(Value::as_bool) == Some(true);
        let intent = if action == RefusedAction::Merge { "merge" } else { "push" };
        let state = self.agent.web(&format!("/api/repos/state?thread={}&intent={intent}", query_escape(thread)), None).await;
        let state = state.unwrap_or(Value::Null);
        let confirm = state
            .get("confirm")
            .and_then(Value::as_str)
            .filter(|c| c.len() == 32 && c.bytes().all(|b| b.is_ascii_hexdigit()));
        let count = state.get(if action == RefusedAction::Merge { "ahead" } else { "unpushed" }).and_then(Value::as_u64);
        let (Some(confirm), Some(count)) = (confirm, count) else {
            self.notice(chat, format!("{action} refused in {repo}; open the app."));
            return;
        };
        if count == 0 {
            let line = if action == RefusedAction::Merge { format!("Nothing to merge in {repo}.") } else { format!("Nothing to push in {repo}.") };
            self.notice(chat, line);
            return;
        }
        let (text, option) = if action == RefusedAction::Merge {
            (format!("Merge {count} commits of {repo} ({branch}) into {base}?"), "Merge")
        } else {
            (format!("Push {count} commits of {repo} ({branch}) to the forge?"), "Push")
        };
        self.next_approval += 1;
        let nonce = ling_web_server::auth::random_hex(8);
        self.approvals.insert(
            nonce.clone(),
            Pending {
                local: Some(Local::RepoConfirm { action, thread: thread.to_string(), confirm: confirm.to_string(), repo, notified }),
                chat: chat.to_string(),
                thread: String::new(),
                request: Value::Null,
                method: "local/repoConfirm".to_string(),
                params: Value::Null,
                replies: vec![Reply::Approve, Reply::Decline],
                free_text: false,
                deadline: Instant::now() + self.config.approval_timeout.min(REPO_CONFIRM_TIMEOUT),
                seq: self.next_approval,
            },
        );
        self.send(Outbound::Approval { chat: chat.to_string(), id: nonce, text, options: vec![option.to_string(), "Cancel".to_string()] });
    }

    /// Sends a confirmed `/push` or `/merge` to `ling web` with its nonce, off the hub's loop, and
    /// says how it went in fixed words: the server's own error text never reaches the chat. A
    /// success is said only when the checkout's own notice does not reach this chat.
    fn repo_confirmed(&self, chat: &str, action: RefusedAction, thread: &str, confirm: &str, repo: &RepoName, notified: bool) {
        let Some(adapter) = self.adapters.get(adapter_of(chat)).cloned() else { return };
        let path = if action == RefusedAction::Merge { "/api/repos/merge" } else { "/api/repos/push" };
        let body = json!({ "thread": thread, "confirm": confirm });
        let (agent, chat, repo) = (self.agent.clone(), chat.to_string(), repo.clone());
        let command = if action == RefusedAction::Merge { "/merge" } else { "/push" };
        tokio::spawn(async move {
            let text = match agent.web(path, Some(body)).await {
                Ok(_) if notified => return,
                Ok(value) if action == RefusedAction::Merge => {
                    match (
                        value.get("branch").and_then(Value::as_str).and_then(BranchName::parse),
                        value.get("into").and_then(Value::as_str).and_then(BranchName::parse),
                    ) {
                        (Some(branch), Some(into)) => format!("Merged {branch} into {into} in {repo}."),
                        _ => format!("Merged in {repo}."),
                    }
                }
                Ok(value) => match value.get("pushed").and_then(Value::as_u64) {
                    Some(commits) => format!("Pushed {commits} commits of {repo}."),
                    None => format!("Pushed {repo}."),
                },
                Err(err) if err.starts_with("the branch moved since you confirmed") => {
                    format!("The branch moved since you confirmed; send {command} again.")
                }
                Err(err) if err == "confirmation missing or expired" => {
                    format!("That confirmation expired; send {command} again.")
                }
                Err(_) => format!("{action} refused in {repo}; open the app."),
            };
            let _ = adapter.send(Outbound::Notice { chat, text });
        });
    }

    /// Pending Telegram Mini App first-launch device pairing requests (`GET /api/telegram/pending-pairs`,
    /// specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §12.1 G2): prompts the paired Telegram user once per
    /// request id with a fresh random approval nonce and `Approve` / `Decline` buttons.
    pub async fn push_pending_pairs(&mut self) {
        if self.airgapped || !self.adapters.contains_key("telegram") {
            return;
        }
        let Ok(value) = self.agent.web("/api/telegram/pending-pairs", None).await else { return };
        let list = value.get("pending").and_then(Value::as_array).cloned().unwrap_or_default();
        let active: HashSet<String> = list
            .iter()
            .filter_map(|entry| entry.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();
        self.seen_pairs.retain(|id| active.contains(id));
        let stale: Vec<String> = self
            .approvals
            .iter()
            .filter_map(|(nonce, pending)| match &pending.local {
                Some(Local::LaunchPair { id }) if !active.contains(id) => Some(nonce.clone()),
                _ => None,
            })
            .collect();
        for nonce in stale {
            if let Some(pending) = self.approvals.remove(&nonce) {
                self.close(pending.chat, nonce, "Declined.");
            }
        }
        for entry in list {
            let (Some(id), Some(code), Some(user)) = (
                entry.get("id").and_then(Value::as_str),
                entry.get("code").and_then(Value::as_str),
                entry.get("user").and_then(Value::as_i64),
            ) else {
                continue;
            };
            if !self.seen_pairs.insert(id.to_string()) {
                continue;
            }
            let chat = format!("telegram:{user}");
            let prior: Vec<String> = self
                .approvals
                .iter()
                .filter_map(|(nonce, pending)| match &pending.local {
                    Some(Local::LaunchPair { .. }) if base_of(&pending.chat) == chat => Some(nonce.clone()),
                    _ => None,
                })
                .collect();
            for nonce in prior {
                if let Some(pending) = self.approvals.remove(&nonce) {
                    self.close(pending.chat, nonce, "Declined.");
                }
            }
            self.next_approval += 1;
            let nonce = ling_web_server::auth::random_hex(8);
            self.approvals.insert(
                nonce.clone(),
                Pending {
                    local: Some(Local::LaunchPair { id: id.to_string() }),
                    chat: chat.clone(),
                    thread: String::new(),
                    request: Value::Null,
                    method: "local/launchPair".to_string(),
                    params: Value::Null,
                    replies: vec![Reply::Approve, Reply::Decline],
                    free_text: false,
                    deadline: Instant::now() + self.config.approval_timeout,
                    seq: self.next_approval,
                },
            );
            self.send(Outbound::Approval {
                chat,
                id: nonce,
                text: format!("A Mini App on a new device asks to pair (code {code})."),
                options: vec!["Approve".to_string(), "Decline".to_string()],
            });
        }
    }

    /// Tells `ling web` whether the paired Telegram user approved or declined a pending Mini App
    /// launch-pair request (`/api/telegram/pending-pairs/approve` or `/decline`).
    fn launch_pair_decide(&self, id: &str, approve: bool) {
        let agent = self.agent.clone();
        let path = if approve {
            "/api/telegram/pending-pairs/approve"
        } else {
            "/api/telegram/pending-pairs/decline"
        };
        let body = json!({ "id": id });
        tokio::spawn(async move {
            let _ = agent.web(path, Some(body)).await;
        });
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
                (Local::SendFile { .. }, Reply::Approve) if off_node(adapter_of(&pending.chat)) && self.airgapped => {
                    "Not sent: the air gap is on."
                }
                (Local::SendFile { thread, name }, Reply::Approve) => {
                    self.send_file(&pending.chat, thread, name);
                    "Sending it."
                }
                (Local::SendFile { .. }, _) => "Not sent; it stays in the thread's folder.",
                (Local::NightAdd { task }, Reply::Answer(repo)) => {
                    self.night_queue(&pending.chat, repo, task);
                    "Queueing it."
                }
                (Local::NightAdd { .. }, _) => "Not queued.",
                (Local::LaunchPair { id }, Reply::Approve) => {
                    self.launch_pair_decide(id, true);
                    "Approved."
                }
                (Local::LaunchPair { id }, _) => {
                    self.launch_pair_decide(id, false);
                    "Declined."
                }
                (Local::RepoConfirm { .. }, Reply::Approve) if off_node(adapter_of(&pending.chat)) && self.airgapped => {
                    "Not sent: the air gap is on."
                }
                (Local::RepoConfirm { action, thread, confirm, repo, notified }, Reply::Approve) => {
                    self.repo_confirmed(&pending.chat, *action, thread, confirm, repo, *notified);
                    if *action == RefusedAction::Merge { "Merging." } else { "Pushing." }
                }
                (Local::RepoConfirm { .. }, _) => "Cancelled.",
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
        let same_chat = match pending.local {
            Some(Local::LaunchPair { .. }) => base_of(&pending.chat) == base_of(chat),
            _ => pending.chat == chat,
        };
        if !same_chat {
            return;
        }
        let Some(reply) = pending.replies.get(choice).cloned() else { return };
        self.resolve(approval, reply);
    }

    /// `yes`/`no`/a number/free text, when the chat has an open approval. Returns true when the
    /// message was taken as the answer. `Local::LaunchPair` is answered only by the inline button's
    /// callback nonce, never by chat text (CHAT_REPOS §12.1 G2).
    fn answer_by_text(&mut self, chat: &str, text: &str) -> bool {
        let Some((approval, pending)) = self
            .approvals
            .iter()
            .filter(|(_, p)| p.chat == chat && !matches!(p.local, Some(Local::LaunchPair { .. })))
            .max_by_key(|(_, p)| p.seq)
        else {
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
                if let Some(local) = &pending.local {
                    let outcome = match local {
                        Local::SendFile { .. } => "No answer in time; the file stays in the thread's folder.",
                        Local::NightAdd { .. } => "No answer in time; not queued.",
                        Local::LaunchPair { id } => {
                            self.launch_pair_decide(id, false);
                            "Declined: no answer in time."
                        }
                        Local::RepoConfirm { .. } => "No answer in time; nothing was sent.",
                    };
                    self.close(pending.chat, approval, outcome);
                    continue;
                }
                self.agent.respond(pending.request.clone(), Self::payload(&pending, &Reply::Decline));
                self.close(pending.chat, approval, "Declined: no answer in time.");
            }
        }
    }
}
