//! The conversation (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §1, §5.2, §8, §11): what the bridge
//! does with the owner's messages and with what the agent sends back.
//!
//! A state machine with no I/O. The driver (`serve.rs`) feeds it the owner's messages, the agent's
//! events and a clock tick, and carries out the [`Action`]s it returns: messages to the owner,
//! typing indicators and receipts on the Signal side; thread and turn requests, interrupts and
//! approval answers on the agent side. That keeps every rule of the conversation testable without
//! Signal, a network or `ling web`.

use std::collections::HashSet;
use std::collections::VecDeque;

use serde_json::Value;
use serde_json::json;

use crate::command;
use crate::command::Input;
use crate::envelope::Attachment;
use crate::format;
use crate::format::Reply;
use crate::gate::OwnerMessage;
use crate::limits;
use crate::limits::Limits;
use crate::limits::Verdict;
use crate::limits::Watch;

/// When no repository can take a phone's Night Shift task.
pub const NO_NIGHT_REPOS: &str = "Night Shift has no repository to queue in from the phone yet. Queue one task from a checkout on the node (`ling`, then /night add), or list it as night_repos = [\"~/path/to/repo\"] under [chat] in ~/.config/dreamference/config.toml.";

/// A phone's Night Shift task with the thread's last exchange after it (the same text as `ling
/// chat`'s `hub::with_context`).
pub fn with_context(task: &str, exchange: Option<&(String, String)>) -> String {
    let cut = |text: &str, limit: usize| -> String {
        let mut out: String = text.chars().take(limit).collect();
        if text.chars().count() > limit {
            out.push('…');
        }
        out
    };
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

/// `/api/night/status` as lines for the phone (as `ling chat`'s).
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

/// Files offered back after one answer, at most, and the largest Signal takes (100 MB, with room).
pub const MAX_OFFERS: usize = 3;
pub const FILE_LIMIT: u64 = 95 * 1024 * 1024;

/// "12 KB", "3.4 MB".
pub fn size(bytes: u64) -> String {
    if bytes < 1024 * 1024 {
        format!("{} KB", bytes.div_ceil(1024).max(1))
    } else {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    }
}

/// Questions held while another is answered.
pub const QUEUE_LIMIT: usize = 5;
/// Threads `/threads` lists.
pub const RECENT_LIMIT: usize = 8;
/// The typing indicator is renewed this often while a turn runs (Signal's expires after ~15 s).
pub const TYPING_EVERY_MS: u64 = 10_000;
/// The first progress note, then the interval between later ones.
pub const FIRST_PROGRESS_MS: u64 = 60_000;
pub const PROGRESS_EVERY_MS: u64 = 300_000;
/// An approval nobody answers in this long is declined.
pub const APPROVAL_MS: u64 = 10 * 60_000;
/// The owner is told the web server is down once it has been down this long.
pub const DOWN_NOTICE_MS: u64 = 10_000;
/// A question after this long a silence starts a new thread, unless `[chat]
/// new_thread_after_hours` says otherwise (§19.2).
pub const NEW_THREAD_AFTER_MS: u64 = 4 * 3600 * 1000;
/// The answer's message (§19.8): what the status line says at once (linked mode), how often the
/// message may change, and how many times. Signal lets a message be edited ten times, and the
/// answer that replaces it is one more: at most three of the changes say what the agent is doing,
/// and the rest stream the answer in as it is written.
pub const WORKING: &str = "Working on it…";
pub const STATUS_EVERY_MS: u64 = 4_000;
pub const STREAM_EVERY_MS: u64 = 3_000;
pub const ACTIVITY_UPDATES: u32 = 3;
pub const STATUS_UPDATES: u32 = 8;

/// The one message an answer grows in (§19.8, the owner's decision of 2026-10-26: answers stream
/// in by edits, as Telegram's do). In linked mode it opens at once as a status line, since Note to
/// Self shows no typing; in dedicated mode typing shows the work and the message opens with the
/// answer's first words. The driver sends `Open` and the first `Stream` (with nothing open) as a
/// message and every later change as an edit of it; after `Close` it sends the next message (the
/// whole answer, or why there is none) as an edit of it too. An edit that fails ends the edits, and
/// the answer then comes whole, as a message of its own.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StatusLine {
    /// The status line, plain text.
    Open(String),
    /// What the agent is doing now, plain text.
    Update(String),
    /// The answer so far, Markdown.
    Stream(String),
    Close,
}

/// The open answer message, as the conversation knows it.
#[derive(Clone, Debug)]
struct Status {
    last_ms: u64,
    shown: String,
    updates: u32,
    /// How much of the answer the message shows.
    streamed: usize,
}

/// What the agent side reports, read from `ling web`'s relay.
#[derive(Clone, Debug, PartialEq)]
pub enum AgentEvent {
    Connected,
    Disconnected,
    /// The answer to a `thread/start` or `thread/resume`.
    ThreadReady { thread: String },
    ThreadFailed { error: String },
    TurnStarted { thread: String, turn: String },
    /// What the agent is doing now, e.g. a command it started.
    Activity { thread: String, text: String },
    Delta { thread: String, text: String },
    TurnCompleted { thread: String, error: Option<String> },
    Approval { id: Value, thread: String, kind: ApprovalKind, command: Option<String>, cwd: Option<String>, reason: Option<String> },
    /// The owner's attachments for this thread's next turn, after [`Action::Upload`].
    Uploaded { thread: String, uploads: Vec<Uploaded> },
    /// Where a `/night add` task may go: (path, name).
    NightRepos { task: String, repos: Vec<(String, String)> },
    /// The files the agent changed during a turn (name, bytes), and the answer that may name them.
    Made { thread: String, files: Vec<(String, u64)>, answer: String },
    /// A follow-up the bridge steered into the running turn was refused (the turn was ending): it
    /// is asked as the next question instead.
    SteerFailed { thread: String, text: String },
    /// A tool call started (a command's text with it) or ended, for the answer's bounds (§19.3).
    ToolStarted { thread: String, item: String, command: Option<String> },
    ToolCompleted { thread: String, item: String },
    /// The air-gap level of the thread `/use` asked for (§11): `Some(true)` at `on`, `None` when
    /// `ling web` did not answer it.
    UseChecked { thread: String, title: String, on: Option<bool> },
    /// The thread an action was about to reach is at `/airgapped on` (`known`), or its level could
    /// not be read: the action was not carried out (§11).
    ThreadSealed { thread: String, known: bool },
}

/// One attachment after the driver handed it to `ling web` (or failed to).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Uploaded {
    pub name: String,
    pub image: bool,
    /// Where `ling web` saved it in the thread's folder; None when it never got there.
    pub path: Option<String>,
    /// The text read off an image (`ling web`'s offline reader), if any.
    pub text: Option<String>,
    /// Whether the served model takes images, as `ling web` reported with the upload.
    pub vision: bool,
    /// A voice note: what the speech-to-text service heard, or why it could not (§19.5).
    pub heard: Option<Result<String, String>>,
}

/// What a message with attachments becomes (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19.1).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Composed {
    /// A turn: its text, and the images the model is shown.
    Turn { text: String, images: Vec<String> },
    /// Nothing the model could use: the bridge answers itself with this, and no turn starts. An
    /// empty message never reaches the model.
    Refused(String),
}

pub const UNREADABLE_IMAGE: &str = "I can't read this image; describe it or paste the text.";
pub const NOT_RECEIVED: &str = "I couldn't receive that attachment; send it again, or describe it or paste the text.";

/// Builds a turn from the owner's text and their uploaded attachments: an image goes to the model
/// as an image when the model takes images, and its text as `[Screenshot text: …]` whenever any was
/// read; another file is named, saved in the thread's folder. With nothing usable, the bridge
/// answers itself instead ([`Composed::Refused`]).
pub fn compose(text: &str, uploads: &[Uploaded]) -> Composed {
    let mut notes: Vec<String> = Vec::new();
    let mut images = Vec::new();
    let mut files = Vec::new();
    let (mut unreadable, mut missing) = (0, 0);
    for upload in uploads {
        let Some(path) = &upload.path else {
            missing += 1;
            continue;
        };
        if !upload.image {
            files.push(std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| upload.name.clone()));
            continue;
        }
        if upload.vision {
            images.push(path.clone());
        }
        match upload.text.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
            Some(read) => notes.push(format!("[Screenshot text: {read}]")),
            None if !upload.vision => unreadable += 1,
            None => {}
        }
    }
    if !files.is_empty() {
        notes.push(format!("[Attached and saved in this thread's folder: {}]", files.join(", ")));
    }
    let asked = text.trim();
    if asked.is_empty() && notes.is_empty() && images.is_empty() {
        return Composed::Refused(if unreadable > 0 || missing == 0 { UNREADABLE_IMAGE } else { NOT_RECEIVED }.to_string());
    }
    if unreadable > 0 {
        notes.push("[An attached image could not be read, and this model does not take images.]".to_string());
    }
    if missing > 0 {
        notes.push(format!("[{missing} attachment(s) could not be received.]"));
    }
    let mut turn = asked.to_string();
    for note in notes {
        if !turn.is_empty() {
            turn.push_str("\n\n");
        }
        turn.push_str(&note);
    }
    Composed::Turn { text: turn, images }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ApprovalKind {
    Command,
    FileChange,
    /// Wider permissions: never granted from a phone.
    Permissions,
}

/// What the driver should do.
#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    /// An answer, formatted.
    Reply(Reply),
    /// A short message of the bridge's own.
    Notice(String),
    Typing(bool),
    /// A read receipt for the owner's message with this timestamp.
    Receipt(u64),
    /// `thread/start` with the `chat` prompt (Ask with the phone block, §19.3).
    StartThread,
    /// The same with `ask`, for a `ling web` that does not know `chat`.
    StartAskThread,
    ResumeThread(String),
    /// Tell the running turn something (`turn/steer`): the bounds' "answer now".
    Steer { thread: String, turn: String, text: String },
    /// Night Shift (§19.7): ask `ling web` where `task` may go, and report [`AgentEvent::NightRepos`].
    NightRepos { task: String },
    /// Queue `task` in `repo`; the owner said YES (or chose it).
    NightAdd { repo: String, task: String },
    /// Show the queue.
    NightStatus,
    /// Look for the files the agent changed in `thread` since `since_ms`; report them as
    /// [`AgentEvent::Made`] (§19.6).
    FindFiles { thread: String, since_ms: u64, answer: String },
    /// Send one of them to the owner, who said YES.
    SendFile { thread: String, name: String },
    /// The owner's follow-up into the running turn (`turn/steer`); refused, it comes back as
    /// [`AgentEvent::SteerFailed`] (§19.4).
    AddToTurn { thread: String, turn: String, text: String },
    /// Upload the attachments into the thread's Ask folder and report [`AgentEvent::Uploaded`].
    Upload { thread: String, attachments: Vec<Attachment> },
    /// Start a turn: the text, and each image the model is shown (`localImage`).
    StartTurn { thread: String, text: String, images: Vec<String> },
    Interrupt { thread: String, turn: String },
    /// A JSON-RPC response to a server request: `{decision: accept|decline}`.
    AnswerApproval { id: Value, result: Value },
    /// The persistent part of the state changed.
    Save,
    /// The status line (linked mode only, §19.8).
    Status(StatusLine),
    /// `/use`: ask `ling web` the thread's air-gap level and report [`AgentEvent::UseChecked`] (§11).
    CheckUse { thread: String, title: String },
}

impl Action {
    /// The thread this action sends something to or reads something from, for the driver's air-gap
    /// check before it (§11): a thread at `/airgapped on` is not used from the phone, and its level
    /// may change after `/use`. `Interrupt` and approvals' answers only stop work and need none.
    pub fn relays_to(&self) -> Option<&str> {
        match self {
            Action::ResumeThread(thread)
            | Action::StartTurn { thread, .. }
            | Action::AddToTurn { thread, .. }
            | Action::Steer { thread, .. }
            | Action::Upload { thread, .. }
            | Action::SendFile { thread, .. }
            | Action::FindFiles { thread, .. } => Some(thread),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ThreadRef {
    pub id: String,
    pub title: String,
}

#[derive(Clone, Debug)]
struct Approval {
    id: Value,
    text: String,
    deadline_ms: u64,
    shown: bool,
    /// The bridge's own question, not the agent's: a file to send back (§19.6) or a Night Shift
    /// task to queue (§19.7).
    local: Option<Local>,
}

#[derive(Clone, Debug)]
enum Local {
    File { thread: String, name: String },
    /// The task, and the repositories it may go to (path, name); one is chosen by YES or a number.
    Night { task: String, repos: Vec<(String, String)> },
}

#[derive(Clone, Debug)]
enum Phase {
    Idle,
    /// A thread is being started or resumed for this message.
    Opening { message: OwnerMessage, fresh: bool },
    Running {
        thread: String,
        turn: Option<String>,
        started_ms: u64,
        last_typing_ms: u64,
        next_progress_ms: u64,
        answer: String,
        activity: Option<String>,
        stopping: bool,
        /// The owner's text while their attachments are uploaded; the turn starts after.
        uploading: Option<String>,
        /// The answer's bounds (limits.rs), and why the bridge interrupted it, when it did.
        watch: Watch,
        limit_reason: Option<String>,
        /// The owner's quick follow-ups before the turn's id is known: steered in once it is (§19.4).
        added: Vec<String>,
        /// The question, for a later `/night add`'s context.
        asked: String,
    },
}

/// Facts `/status` reports that the bridge does not track itself.
#[derive(Clone, Debug, Default)]
pub struct Info {
    pub node: String,
    pub bridge_version: String,
    pub signal_cli_version: String,
}

pub struct Bridge {
    pub current: Option<String>,
    pub recent: Vec<ThreadRef>,
    pub info: Info,
    /// The current thread's last question and answer, for a `/night add` task's context.
    last_exchange: Option<(String, String)>,
    /// The names the owner's own attachments were saved under, per thread: never offered back.
    uploaded: std::collections::HashMap<String, HashSet<String>>,
    /// When the conversation last moved: the owner's last question or the last answer. None for a
    /// conversation saved before this was kept, taken as long ago.
    pub last_activity_ms: Option<u64>,
    /// A question after this long a silence starts a new thread; None: never.
    pub new_thread_after_ms: Option<u64>,
    /// An answer's bounds, from the user's `[chat]` settings (§19.3).
    pub limits: Limits,
    /// Whether work is shown by a status line (linked mode: Note to Self has no typing indicator);
    /// otherwise by typing (§19.8).
    pub status_line: bool,
    /// Whether the answer streams into its message by edits (§19.8).
    pub stream: bool,
    status: Option<Status>,
    loaded: HashSet<String>,
    connected: bool,
    phase: Phase,
    queue: VecDeque<OwnerMessage>,
    approvals: VecDeque<Approval>,
    airgapped_since: Option<u64>,
    missed_while_airgapped: u64,
    down_since: Option<u64>,
    down_notified: bool,
    lost_answer: bool,
}

/// Appends a follow-up's text to a question's, a blank line between.
fn join(text: &mut String, more: &str) {
    let more = more.trim();
    if more.is_empty() {
        return;
    }
    if !text.trim().is_empty() {
        text.push_str("\n\n");
    }
    text.push_str(more);
}

fn title_of(text: &str) -> String {
    let line = text.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or("(attachment)");
    let mut title: String = line.chars().take(60).collect();
    if line.chars().count() > 60 {
        title.push('…');
    }
    title
}

/// "2m10s", "45s", "1h05m".
pub fn duration(ms: u64) -> String {
    let s = ms / 1000;
    if s < 60 {
        format!("{s}s")
    } else if s < 3600 {
        format!("{}m{:02}s", s / 60, s % 60)
    } else {
        format!("{}h{:02}m", s / 3600, (s % 3600) / 60)
    }
}

/// The model gate's refusal while a SWE-bench run holds the model
/// (`dreamference/vllm_server/model_gate_service.py`, `message`): "The model is running a benchmark
/// (night 4, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`." Codex passes
/// it on inside its own error ("unexpected status 503 Service Unavailable: …, url: …"). Returns what
/// is in the parentheses: the run, its progress and the time left, as the gate words them.
fn benchmark_holds_model(error: &str) -> Option<String> {
    const MARK: &str = "The model is running a benchmark (";
    let rest = &error[error.find(MARK)? + MARK.len()..];
    let end = rest.find(").").or_else(|| rest.rfind(')'))?;
    let details = rest[..end].trim();
    (!details.is_empty()).then(|| details.to_string())
}

fn model_server_down(error: &str) -> bool {
    let lower = error.to_ascii_lowercase();
    ["connection refused", "error sending request", "failed to connect", "connection reset", "stream disconnected"]
        .iter()
        .any(|needle| lower.contains(needle))
}

impl Bridge {
    pub fn new(current: Option<String>, recent: Vec<ThreadRef>, info: Info) -> Bridge {
        Bridge {
            current,
            recent,
            info,
            uploaded: std::collections::HashMap::new(),
            last_exchange: None,
            last_activity_ms: None,
            new_thread_after_ms: Some(NEW_THREAD_AFTER_MS),
            limits: Limits::default(),
            status_line: false,
            stream: true,
            status: None,
            loaded: HashSet::new(),
            connected: false,
            phase: Phase::Idle,
            queue: VecDeque::new(),
            approvals: VecDeque::new(),
            airgapped_since: None,
            missed_while_airgapped: 0,
            down_since: None,
            down_notified: false,
            lost_answer: false,
        }
    }

    pub fn is_busy(&self) -> bool {
        !matches!(self.phase, Phase::Idle)
    }

    pub fn airgapped(&self) -> bool {
        self.airgapped_since.is_some()
    }

    /// The thread a turn is running in, for the driver's poll of its air-gap level (§11).
    pub fn running_thread(&self) -> Option<&str> {
        match &self.phase {
            Phase::Running { thread, stopping: false, .. } => Some(thread),
            _ => None,
        }
    }

    fn approval_pending(&self) -> bool {
        !self.approvals.is_empty()
    }

    /// One message from the owner, already through the gate.
    pub fn on_message(&mut self, message: OwnerMessage, now: u64) -> Vec<Action> {
        if self.airgapped() {
            // Air-gapped: nothing is sent, not even a receipt (§11).
            self.missed_while_airgapped += 1;
            return Vec::new();
        }
        let mut actions = vec![Action::Receipt(message.timestamp)];
        let input = if message.attachments.is_empty() {
            command::parse(&message.text, self.approval_pending())
        } else {
            Input::Question(message.text.clone())
        };
        match input {
            Input::Yes | Input::No => {
                if let Some(approval) = self.approvals.pop_front() {
                    match approval.local {
                        Some(Local::File { thread, name }) if input == Input::Yes => actions.push(Action::SendFile { thread, name }),
                        Some(Local::File { .. }) => actions.push(Action::Notice("Not sent; it stays in the thread's folder.".to_string())),
                        Some(Local::Night { task, repos }) if input == Input::Yes && repos.len() == 1 => {
                            actions.push(Action::NightAdd { repo: repos[0].0.clone(), task });
                        }
                        Some(Local::Night { task, repos }) if input == Input::Yes => {
                            // Several repositories: YES names none, so the question stays.
                            actions.push(Action::Notice(format!("Reply with the repository's number (1–{}), or NO.", repos.len())));
                            self.approvals.push_front(Approval { id: Value::Null, text: String::new(), deadline_ms: approval.deadline_ms, shown: true, local: Some(Local::Night { task, repos }) });
                            return actions;
                        }
                        Some(Local::Night { .. }) => actions.push(Action::Notice("Not queued.".to_string())),
                        None => {
                            let decision = if input == Input::Yes { "accept" } else { "decline" };
                            actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": decision }) });
                        }
                    }
                    self.show_next_approval(&mut actions);
                }
            }
            Input::Stop => actions.extend(self.stop()),
            Input::New => {
                self.current = None;
                actions.push(Action::Notice("New thread. (The last one stays in your history.)".to_string()));
                actions.push(Action::Save);
            }
            Input::Status => actions.push(Action::Notice(self.status(now))),
            Input::Threads => actions.push(Action::Notice(self.threads_text())),
            // Switched to only once `ling web` says the thread is not air-gapped (§11).
            Input::Use(n) => match self.recent.get(n - 1).cloned() {
                Some(thread) => actions.push(Action::CheckUse { thread: thread.id, title: thread.title }),
                None => actions.push(Action::Notice(format!("There is no thread {n}. /threads lists them."))),
            },
            Input::Help => actions.push(Action::Notice(command::HELP.to_string())),
            Input::Night(rest) => {
                let (verb, task) = rest.split_once(char::is_whitespace).map(|(v, t)| (v, t.trim())).unwrap_or((rest.as_str(), ""));
                match verb {
                    "add" if !task.is_empty() => {
                        let task = with_context(task, self.last_exchange.as_ref());
                        actions.push(Action::NightRepos { task });
                    }
                    "status" | "list" => actions.push(Action::NightStatus),
                    _ => actions.push(Action::Notice("/night add <task> queues a task for Night Shift tonight; /night status shows the queue.".to_string())),
                }
            }
            Input::Choice(n) => {
                // A number answers a pending choice of repository; a pending YES/NO question
                // takes YES or NO, so the number is asked as a question there.
                let choosing = matches!(self.approvals.front(), Some(Approval { local: Some(Local::Night { repos, .. }), .. }) if repos.len() > 1);
                if choosing && let Some(Approval { local: Some(Local::Night { task, repos }), .. }) = self.approvals.pop_front() {
                    match repos.get(n - 1) {
                        Some((repo, _)) => actions.push(Action::NightAdd { repo: repo.clone(), task }),
                        None => actions.push(Action::Notice(format!("There is no repository {n}; nothing was queued."))),
                    }
                    self.show_next_approval(&mut actions);
                } else {
                    let again = OwnerMessage { text: message.text.clone(), attachments: Vec::new(), timestamp: message.timestamp };
                    actions.extend(self.question(again, now));
                }
            }
            Input::Question(_) => actions.extend(self.question(message, now)),
        }
        actions
    }

    /// A question after a long silence goes to a new thread, said in one line (§19.2): the morning
    /// of 2026-10-20, a screenshot was read as part of a two-day-old thread about songs.
    fn fresh_after_silence(&mut self, now: u64) -> Vec<Action> {
        let (Some(_), Some(gap)) = (&self.current, self.new_thread_after_ms) else { return Vec::new() };
        let silence = self.last_activity_ms.map(|last| now.saturating_sub(last));
        if silence.is_some_and(|silence| silence < gap) {
            return Vec::new();
        }
        self.current = None;
        let since = silence.map(|ms| format!(" (the last message was {} ago)", duration(ms))).unwrap_or_default();
        vec![Action::Notice(format!("New thread{since}; /use 2 to go back.")), Action::Save]
    }

    /// Starts work on one question: opens its thread if needed, else starts the turn.
    /// The owner sees at once that it is taken: the status line opens, or typing starts while the
    /// thread opens (§19.8); before 2026-10-26 nothing showed until the thread was open.
    fn begin(&mut self, message: OwnerMessage, now: u64) -> Vec<Action> {
        let mut actions = Vec::new();
        if self.status_line && self.status.is_none() {
            self.status = Some(Status { last_ms: now, shown: String::new(), updates: 0, streamed: 0 });
            actions.push(Action::Status(StatusLine::Open(WORKING.to_string())));
        }
        let typing = if self.status_line { None } else { Some(Action::Typing(true)) };
        match self.current.clone() {
            None => {
                self.phase = Phase::Opening { message, fresh: true };
                actions.extend(typing);
                actions.push(Action::StartThread);
            }
            Some(thread) if !self.loaded.contains(&thread) => {
                self.phase = Phase::Opening { message, fresh: false };
                actions.extend(typing);
                actions.push(Action::ResumeThread(thread));
            }
            Some(thread) => actions.extend(self.run(thread, message, now)),
        }
        actions
    }

    /// The question in hand is done: its status line gives way to the next message.
    fn close_status(&mut self, actions: &mut Vec<Action>) {
        if self.status.take().is_some() {
            actions.push(Action::Status(StatusLine::Close));
        }
    }

    fn run(&mut self, thread: String, message: OwnerMessage, now: u64) -> Vec<Action> {
        if message.text.trim().is_empty() && message.attachments.is_empty() {
            // Never an empty turn: the model would answer something it was not asked.
            self.phase = Phase::Idle;
            return self.next(now);
        }
        let with_attachments = !message.attachments.is_empty();
        self.phase = Phase::Running {
            thread: thread.clone(),
            turn: None,
            started_ms: now,
            last_typing_ms: now,
            next_progress_ms: now + FIRST_PROGRESS_MS,
            answer: String::new(),
            activity: None,
            stopping: false,
            uploading: with_attachments.then(|| message.text.clone()),
            watch: Watch::new(now),
            limit_reason: None,
            added: Vec::new(),
            asked: message.text.clone(),
        };
        if with_attachments {
            vec![Action::Typing(true), Action::Upload { thread, attachments: message.attachments }]
        } else {
            vec![Action::Typing(true), Action::StartTurn { thread, text: message.text, images: Vec::new() }]
        }
    }

    /// A question: to a new thread after a silence, into the answer in hand, queued, or begun.
    fn question(&mut self, message: OwnerMessage, now: u64) -> Vec<Action> {
        let mut actions = Vec::new();
        if !self.is_busy() && self.queue.is_empty() {
            actions.extend(self.fresh_after_silence(now));
        }
        self.last_activity_ms = Some(now);
        if self.connected && self.is_busy() && let Some(merged) = self.merge(message.clone()) {
            actions.extend(merged);
        } else if self.is_busy() || !self.connected {
            if self.queue.len() >= QUEUE_LIMIT {
                actions.push(Action::Notice(format!("{QUEUE_LIMIT} questions are already waiting; send this again once they're answered.")));
            } else {
                self.queue.push_back(message);
                let why = if self.connected { "I'll take it next. /stop to interrupt." } else { "Mightling's web server isn't answering yet; I'll ask as soon as it is." };
                actions.push(Action::Notice(format!("Queued; {why}")));
            }
        } else {
            actions.extend(self.begin(message, now));
        }
        actions
    }

    /// A quick follow-up joins the question in hand instead of waiting as a turn of its own
    /// (§19.4): while its thread opens or its attachments upload, its text is added to the
    /// question; while the answer runs, it is steered in (`turn/steer`), at once or as soon as the
    /// turn's id is known. Returns None when it cannot join: an attachment after the question has
    /// gone, an approval waiting, a stop under way, or the final turn after a time-out, so it waits
    /// as the next question.
    fn merge(&mut self, message: OwnerMessage) -> Option<Vec<Action>> {
        if self.approval_pending() {
            return None;
        }
        match &mut self.phase {
            Phase::Idle => None,
            Phase::Opening { message: opening, .. } => {
                join(&mut opening.text, &message.text);
                opening.attachments.extend(message.attachments);
                Some(Vec::new())
            }
            Phase::Running { stopping: true, .. } => None,
            Phase::Running { watch, .. } if watch.final_turn => None,
            Phase::Running { .. } if !message.attachments.is_empty() => None,
            Phase::Running { uploading: Some(text), .. } => {
                join(text, &message.text);
                Some(Vec::new())
            }
            Phase::Running { thread, turn: Some(turn), .. } => Some(vec![Action::AddToTurn { thread: thread.clone(), turn: turn.clone(), text: message.text }]),
            Phase::Running { added, .. } => {
                added.push(message.text);
                Some(Vec::new())
            }
        }
    }

    /// Takes the queued questions, if the bridge is free: all of them, as one question (§19.4).
    fn next(&mut self, now: u64) -> Vec<Action> {
        if self.is_busy() || !self.connected || self.airgapped() {
            return Vec::new();
        }
        let Some(mut first) = self.queue.pop_front() else { return Vec::new() };
        while let Some(more) = self.queue.pop_front() {
            join(&mut first.text, &more.text);
            first.attachments.extend(more.attachments);
        }
        self.begin(first, now)
    }

    fn stop(&mut self) -> Vec<Action> {
        let mut actions = Vec::new();
        let dropped = self.queue.len();
        self.queue.clear();
        while let Some(approval) = self.approvals.pop_front() {
            if approval.local.is_none() {
                actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "cancel" }) });
            }
        }
        match &mut self.phase {
            Phase::Running { thread, turn, stopping, .. } => {
                *stopping = true;
                if let Some(turn) = turn {
                    actions.push(Action::Interrupt { thread: thread.clone(), turn: turn.clone() });
                }
                actions.push(Action::Typing(false));
                self.close_status(&mut actions);
                actions.push(Action::Notice("Stopped.".to_string()));
            }
            Phase::Opening { .. } => {
                self.phase = Phase::Idle;
                self.close_status(&mut actions);
                actions.push(Action::Notice("Stopped.".to_string()));
            }
            Phase::Idle if dropped > 0 => actions.push(Action::Notice("Stopped; the queue is empty.".to_string())),
            Phase::Idle => actions.push(Action::Notice("Nothing is running.".to_string())),
        }
        actions
    }

    /// The thread an action was about to reach is air-gapped, or its level unknown (§11): the work
    /// in hand on it stops, unanswered and unrelayed, and what waited for it is dropped. A thread
    /// known to be at `on` is left, so the next question starts a new one; one whose level could
    /// not be read stays current, to be asked again with the next message.
    fn sealed(&mut self, thread: String, known: bool) -> Vec<Action> {
        let mut actions = Vec::new();
        let ours = match &self.phase {
            Phase::Opening { fresh: false, .. } => self.current.as_deref() == Some(thread.as_str()),
            Phase::Running { thread: running, .. } => *running == thread,
            _ => false,
        };
        if ours {
            if let Phase::Running { turn: Some(turn), stopping: false, .. } = &self.phase {
                actions.push(Action::Interrupt { thread: thread.clone(), turn: turn.clone() });
            }
            self.phase = Phase::Idle;
            self.queue.clear();
            while let Some(approval) = self.approvals.pop_front() {
                if approval.local.is_none() {
                    actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "cancel" }) });
                }
            }
            actions.push(Action::Typing(false));
            self.close_status(&mut actions);
        }
        if known {
            self.loaded.remove(&thread);
            if self.current.as_deref() == Some(thread.as_str()) {
                self.current = None;
                actions.push(Action::Save);
            }
            actions.push(Action::Notice("This thread is air-gapped (/airgapped on in Mightling), so it isn't used from the phone; nothing was sent. Your next message starts a new thread.".to_string()));
        } else {
            actions.push(Action::Notice("Mightling's web server did not say whether this thread is air-gapped, so nothing was sent. Try again in a moment.".to_string()));
        }
        actions
    }

    fn show_next_approval(&mut self, actions: &mut Vec<Action>) {
        if let Some(next) = self.approvals.front_mut()
            && !next.shown
        {
            next.shown = true;
            actions.push(Action::Notice(next.text.clone()));
        }
    }

    /// One event from the agent side.
    pub fn on_agent(&mut self, event: AgentEvent, now: u64) -> Vec<Action> {
        let mut actions = Vec::new();
        match event {
            AgentEvent::Connected => {
                self.connected = true;
                self.down_since = None;
                if std::mem::take(&mut self.down_notified) && !self.airgapped() {
                    actions.push(Action::Notice("Mightling's web server is answering again.".to_string()));
                }
                actions.extend(self.next(now));
            }
            AgentEvent::Disconnected => {
                if self.connected {
                    self.down_since = Some(now);
                }
                self.connected = false;
                self.loaded.clear();
                self.approvals.clear();
                match std::mem::replace(&mut self.phase, Phase::Idle) {
                    // Not started yet: ask again once the server is back.
                    Phase::Opening { message, .. } => self.queue.push_front(message),
                    Phase::Running { stopping: false, .. } => {
                        self.lost_answer = true;
                        // The notice that the server is down says so in the status line's place.
                        self.close_status(&mut actions);
                    }
                    _ => {}
                }
            }
            AgentEvent::ThreadReady { thread } => {
                if let Phase::Opening { message, fresh } = std::mem::replace(&mut self.phase, Phase::Idle) {
                    self.loaded.insert(thread.clone());
                    if fresh {
                        self.current = Some(thread.clone());
                        self.recent.retain(|t| t.id != thread);
                        self.recent.insert(0, ThreadRef { id: thread.clone(), title: title_of(&message.text) });
                        self.recent.truncate(RECENT_LIMIT);
                        actions.push(Action::Save);
                    }
                    actions.extend(self.run(thread, message, now));
                }
            }
            AgentEvent::ThreadFailed { error } if error.contains("no prompt named \"chat\"") && matches!(self.phase, Phase::Opening { fresh: true, .. }) => {
                // A `ling web` from before the phone prompt: an Ask thread, as before.
                actions.push(Action::StartAskThread);
            }
            AgentEvent::ThreadFailed { error } => {
                if let Phase::Opening { fresh, .. } = std::mem::replace(&mut self.phase, Phase::Idle) {
                    if !fresh {
                        // A thread that will not resume is left; the next question starts a new one.
                        self.current = None;
                        actions.push(Action::Save);
                    }
                    self.close_status(&mut actions);
                    actions.push(Action::Notice(format!("Couldn't open the thread: {error}")));
                    actions.extend(self.next(now));
                }
            }
            AgentEvent::TurnStarted { thread, turn } => {
                if let Phase::Running { thread: running, turn: slot, stopping, added, .. } = &mut self.phase
                    && *running == thread
                {
                    *slot = Some(turn.clone());
                    if *stopping {
                        actions.push(Action::Interrupt { thread, turn });
                    } else if !added.is_empty() {
                        let text = std::mem::take(added).join("\n\n");
                        actions.push(Action::AddToTurn { thread, turn, text });
                    }
                }
            }
            AgentEvent::Activity { thread, text } => {
                if let Phase::Running { thread: running, activity, .. } = &mut self.phase
                    && *running == thread
                {
                    *activity = Some(text);
                }
            }
            AgentEvent::Delta { thread, text } => {
                if let Phase::Running { thread: running, answer, .. } = &mut self.phase
                    && *running == thread
                {
                    answer.push_str(&text);
                }
            }
            AgentEvent::TurnCompleted { thread, error } => {
                let running = matches!(&self.phase, Phase::Running { thread: t, .. } if *t == thread);
                if running && let Phase::Running { answer, stopping, watch, limit_reason, started_ms, asked, .. } = std::mem::replace(&mut self.phase, Phase::Idle) {
                    // The agent's requests end with its turn; the bridge's own offers stay.
                    self.approvals.retain(|a| a.local.is_some());
                    self.last_activity_ms = Some(now);
                    // Interrupted by its bounds (limits.rs): a final turn asks for the answer from what
                    // was found; the final turn out of time too ends with what it wrote.
                    if let Some(reason) = limit_reason.filter(|_| error.is_some() && !stopping && !self.airgapped()) {
                        if !watch.final_turn {
                            // What it wrote so far is kept, marked, before the final turn (MOONSHOT D9).
                            if !answer.trim().is_empty() {
                                actions.push(Action::Reply(format::reply(&format!("{}\n\n(cut off: {reason})", answer.trim()))));
                            }
                            self.phase = Phase::Running {
                                thread: thread.clone(),
                                turn: None,
                                started_ms: now,
                                last_typing_ms: now,
                                next_progress_ms: now + FIRST_PROGRESS_MS,
                                answer: String::new(),
                                activity: None,
                                stopping: false,
                                uploading: None,
                                watch: Watch::final_turn(now),
                                limit_reason: None,
                                added: Vec::new(),
                                asked: asked.clone(),
                            };
                            actions.push(Action::StartTurn { thread, text: limits::final_text(&reason), images: Vec::new() });
                            return actions;
                        }
                        actions.push(Action::Typing(false));
                        self.close_status(&mut actions);
                        if !answer.trim().is_empty() {
                            actions.push(Action::Reply(format::reply(answer.trim())));
                        }
                        actions.push(Action::Notice(limits::GAVE_UP.to_string()));
                        actions.extend(self.next(now));
                        return actions;
                    }
                    // The owner's /stop keeps what was written, marked (MOONSHOT D9).
                    if stopping && !self.airgapped() && !answer.trim().is_empty() {
                        actions.push(Action::Reply(format::reply(&format!("{}\n\n(stopped)", answer.trim()))));
                    }
                    if !self.airgapped() && !stopping {
                        actions.push(Action::Typing(false));
                        self.close_status(&mut actions);
                        match error {
                            // A benchmark run holds the model: the question is refused, not let through
                            // the gate (the owner's decision, 2026-10-12), and the owner is told when to
                            // ask again. The gate's own hint (`night pause`) is not passed on.
                            Some(error) if let Some(details) = benchmark_holds_model(&error) => actions.push(Action::Notice(format!(
                                "The model on {} is busy with a benchmark run ({details}), so this question was not answered. Ask again when the run has finished.",
                                self.node()
                            ))),
                            Some(error) if model_server_down(&error) => actions.push(Action::Notice(format!(
                                "The model server on {} isn't answering (`ling-admin server start` on the node).",
                                self.node()
                            ))),
                            Some(error) => actions.push(Action::Notice(format!("The answer failed: {error}"))),
                            None if answer.trim().is_empty() => actions.push(Action::Notice("(Mightling finished without writing an answer.)".to_string())),
                            None => {
                                self.last_exchange = Some((asked.clone(), answer.trim().to_string()));
                                actions.push(Action::Reply(format::reply(answer.trim())));
                                // Files it made and named are looked for, to offer back (§19.6).
                                actions.push(Action::FindFiles { thread: thread.clone(), since_ms: started_ms, answer: answer.trim().to_string() });
                            }
                        }
                    }
                    actions.extend(self.next(now));
                }
            }
            AgentEvent::UseChecked { thread, title, on } => match on {
                Some(false) => {
                    self.current = Some(thread);
                    self.last_activity_ms = Some(now);
                    actions.push(Action::Notice(format!("Continuing: {title}")));
                    actions.push(Action::Save);
                }
                Some(true) => actions.push(Action::Notice(format!("Not switched: “{title}” is air-gapped (/airgapped on in Mightling), so it isn't used from the phone."))),
                None => actions.push(Action::Notice("Not switched: Mightling's web server did not say whether that thread is air-gapped. Try again in a moment.".to_string())),
            },
            AgentEvent::ThreadSealed { thread, known } => actions.extend(self.sealed(thread, known)),
            AgentEvent::NightRepos { task, repos } => {
                if repos.is_empty() {
                    actions.push(Action::Notice(NO_NIGHT_REPOS.to_string()));
                    return actions;
                }
                let preview: String = task.chars().take(400).collect();
                let text = if repos.len() == 1 {
                    format!("Queue this for Night Shift tonight, in {}?\n\n{preview}\n\nReply YES or NO.", repos[0].1)
                } else {
                    let list: Vec<String> = repos.iter().enumerate().map(|(n, (_, name))| format!("{}. {name}", n + 1)).collect();
                    format!("Queue this for Night Shift tonight?\n\n{preview}\n\nReply with the repository's number, or NO:\n{}", list.join("\n"))
                };
                self.approvals.push_back(Approval { id: Value::Null, text, deadline_ms: now + APPROVAL_MS, shown: false, local: Some(Local::Night { task, repos }) });
                self.show_next_approval(&mut actions);
            }
            AgentEvent::Made { thread, files, answer } => {
                if self.airgapped() {
                    return actions;
                }
                let uploaded = self.uploaded.get(&thread).cloned().unwrap_or_default();
                let mut offered = 0;
                for (name, bytes) in files {
                    let base = name.rsplit('/').next().unwrap_or(&name).to_string();
                    if uploaded.contains(&name) || !answer.contains(&base) || offered >= MAX_OFFERS {
                        continue;
                    }
                    offered += 1;
                    if bytes > FILE_LIMIT {
                        actions.push(Action::Notice(format!("Mightling made {name} ({}), larger than Signal takes, so it stays in the thread's folder.", size(bytes))));
                        continue;
                    }
                    let text = format!("Mightling made {name} ({}). Reply YES to get it here, or NO.", size(bytes));
                    self.approvals.push_back(Approval { id: Value::Null, text, deadline_ms: now + APPROVAL_MS, shown: false, local: Some(Local::File { thread: thread.clone(), name }) });
                }
                self.show_next_approval(&mut actions);
            }
            AgentEvent::SteerFailed { thread: _, text } => {
                // The turn was ending: the follow-up is the next question, taken when it ends.
                self.queue.push_front(OwnerMessage { text, attachments: Vec::new(), timestamp: now });
                actions.extend(self.next(now));
            }
            AgentEvent::ToolStarted { thread, item, command } => {
                if let Phase::Running { thread: running, watch, .. } = &mut self.phase
                    && *running == thread
                {
                    watch.tool_started(&item, command.as_deref(), now);
                }
            }
            AgentEvent::ToolCompleted { thread, item } => {
                if let Phase::Running { thread: running, watch, .. } = &mut self.phase
                    && *running == thread
                {
                    watch.tool_completed(&item);
                }
            }
            AgentEvent::Uploaded { thread, uploads } => {
                let waiting = match &mut self.phase {
                    Phase::Running { thread: running, uploading, stopping, .. } if *running == thread => uploading.take().map(|text| (text, *stopping)),
                    _ => None,
                };
                for upload in &uploads {
                    if let Some(path) = &upload.path
                        && let Some(name) = std::path::Path::new(path).file_name()
                    {
                        self.uploaded.entry(thread.clone()).or_default().insert(name.to_string_lossy().into_owned());
                    }
                }
                if let Some((text, stopping)) = waiting {
                    // Voice notes first: their text joins the question, said back so a mis-hearing
                    // is seen (§19.5); a failure is said, and a voice note alone then asks nothing.
                    let (recordings, uploads): (Vec<Uploaded>, Vec<Uploaded>) = uploads.into_iter().partition(|u| u.heard.is_some());
                    let mut text = text;
                    for recording in &recordings {
                        match &recording.heard {
                            Some(Ok(heard)) => {
                                actions.push(Action::Notice(format!("Heard: {heard}")));
                                join(&mut text, heard);
                            }
                            Some(Err(reason)) => actions.push(Action::Notice(format!("I couldn't transcribe the voice message ({reason}); type the question instead."))),
                            None => {}
                        }
                    }
                    if !recordings.is_empty() && uploads.is_empty() && text.trim().is_empty() {
                        self.phase = Phase::Idle;
                        actions.push(Action::Typing(false));
                        self.close_status(&mut actions);
                        actions.extend(self.next(now));
                        return actions;
                    }
                    match compose(&text, &uploads) {
                        _ if stopping => {
                            self.phase = Phase::Idle;
                            actions.extend(self.next(now));
                        }
                        Composed::Turn { text, images } => actions.push(Action::StartTurn { thread, text, images }),
                        Composed::Refused(reply) => {
                            self.phase = Phase::Idle;
                            actions.push(Action::Typing(false));
                            self.close_status(&mut actions);
                            actions.push(Action::Notice(reply));
                            actions.extend(self.next(now));
                        }
                    }
                }
            }
            AgentEvent::Approval { id, thread: _, kind, command, cwd, reason } => {
                if kind == ApprovalKind::Permissions || self.airgapped() {
                    actions.push(Action::AnswerApproval { id, result: json!({ "decision": "decline" }) });
                    if !self.airgapped() {
                        actions.push(Action::Notice("Mightling asked for wider permissions; that is answered at a keyboard, not from a phone, so I declined.".to_string()));
                    }
                } else {
                    let what = match (kind, command) {
                        (ApprovalKind::Command, Some(command)) => format!("⚠️ Mightling wants to run:\n{command}"),
                        (ApprovalKind::Command, None) => "⚠️ Mightling wants to run a command.".to_string(),
                        _ => "⚠️ Mightling wants to change files.".to_string(),
                    };
                    let mut text = what;
                    if let Some(cwd) = cwd {
                        text.push_str(&format!("\nin {cwd}"));
                    }
                    if let Some(reason) = reason.filter(|r| !r.trim().is_empty()) {
                        text.push_str(&format!("\n({})", reason.trim()));
                    }
                    text.push_str("\nReply YES or NO.");
                    self.approvals.push_back(Approval { id, text, deadline_ms: now + APPROVAL_MS, shown: false, local: None });
                    self.show_next_approval(&mut actions);
                }
            }
        }
        actions
    }

    /// The air-gap level at user level, as `ling web` reports it (§11).
    pub fn on_airgap(&mut self, on: bool, now: u64) -> Vec<Action> {
        let mut actions = Vec::new();
        match (on, self.airgapped_since) {
            (true, None) => {
                self.airgapped_since = Some(now);
                self.queue.clear();
                // Nothing is sent at `on`, not even the line's last edit; the next question opens a new one.
                self.status = None;
                while let Some(approval) = self.approvals.pop_front() {
                    if approval.local.is_none() {
                        actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "cancel" }) });
                    }
                }
                if let Phase::Running { thread, turn, stopping, .. } = &mut self.phase {
                    *stopping = true;
                    if let Some(turn) = turn {
                        actions.push(Action::Interrupt { thread: thread.clone(), turn: turn.clone() });
                    }
                }
                if matches!(self.phase, Phase::Opening { .. }) {
                    self.phase = Phase::Idle;
                }
            }
            (false, Some(since)) => {
                self.airgapped_since = None;
                let missed = std::mem::take(&mut self.missed_while_airgapped);
                let mut text = format!("Mightling was air-gapped for {}; nothing was sent or answered in that time.", duration(now.saturating_sub(since)));
                if missed > 0 {
                    text.push_str(&format!(" {missed} message(s) from then were not read; send again what you need."));
                }
                actions.push(Action::Notice(text));
                actions.extend(self.next(now));
            }
            _ => {}
        }
        actions
    }

    /// The clock: typing renewal, progress notes, approval timeouts, the web server's absence.
    pub fn on_tick(&mut self, now: u64) -> Vec<Action> {
        let mut actions = Vec::new();
        if self.airgapped() {
            return actions;
        }
        if let Phase::Running { thread, turn: Some(turn), watch, limit_reason, stopping: false, .. } = &mut self.phase {
            match watch.check(now, &self.limits) {
                Some(Verdict::Steer(text)) => actions.push(Action::Steer { thread: thread.clone(), turn: turn.clone(), text }),
                Some(Verdict::Interrupt(reason)) => {
                    if !watch.final_turn {
                        actions.push(Action::Notice(limits::interrupted_notice(&reason)));
                    }
                    *limit_reason = Some(reason);
                    actions.push(Action::Interrupt { thread: thread.clone(), turn: turn.clone() });
                }
                None => {}
            }
        }
        if let Phase::Running { started_ms, last_typing_ms, next_progress_ms, activity, answer, stopping: false, .. } = &mut self.phase {
            // Typing until the answer's message is open (dedicated mode; Note to Self shows none).
            if self.status.is_none() && now >= *last_typing_ms + TYPING_EVERY_MS {
                *last_typing_ms = now;
                actions.push(Action::Typing(true));
            }
            let elapsed = duration(now - *started_ms);
            let progress = now >= *next_progress_ms;
            if progress {
                *next_progress_ms = now + PROGRESS_EVERY_MS;
            }
            let written = answer.trim();
            match &mut self.status {
                // The answer streams in: its first words open the message (dedicated mode), and it
                // changes every few seconds while edits are left (§19.8).
                None if self.stream && !written.is_empty() => {
                    self.status = Some(Status { last_ms: now, shown: String::new(), updates: 0, streamed: written.len() });
                    actions.push(Action::Typing(false));
                    actions.push(Action::Status(StatusLine::Stream(written.to_string())));
                }
                Some(status) if self.stream && !written.is_empty() => {
                    if status.updates < STATUS_UPDATES && status.streamed != written.len() && now >= status.last_ms + STREAM_EVERY_MS {
                        status.streamed = written.len();
                        status.updates += 1;
                        status.last_ms = now;
                        actions.push(Action::Status(StatusLine::Stream(written.to_string())));
                    }
                }
                // Before the answer, the status line says what the agent is doing, as it changes,
                // and stands for the progress notes while it may (§19.8).
                Some(status) if status.updates < ACTIVITY_UPDATES => {
                    let doing = activity.clone();
                    let changed = doing.as_ref().is_some_and(|doing| *doing != status.shown) && now >= status.last_ms + STATUS_EVERY_MS;
                    if changed || progress {
                        let doing = doing.unwrap_or_default();
                        status.shown = doing.clone();
                        status.updates += 1;
                        status.last_ms = now;
                        let text = if doing.is_empty() { format!("Working ({elapsed})") } else { format!("Working ({elapsed}): {doing}") };
                        actions.push(Action::Status(StatusLine::Update(text)));
                    }
                }
                _ if progress => {
                    let mut text = format!("Still working ({elapsed})");
                    if let Some(activity) = activity {
                        text.push_str(&format!(": {activity}"));
                    }
                    actions.push(Action::Notice(text));
                }
                _ => {}
            }
        }
        while self.approvals.front().is_some_and(|a| now >= a.deadline_ms) {
            let approval = self.approvals.pop_front().unwrap();
            if let Some(local) = &approval.local {
                let line = match local {
                    Local::File { .. } => "No reply in 10 minutes; the file stays in the thread's folder.",
                    Local::Night { .. } => "No reply in 10 minutes; not queued.",
                };
                actions.push(Action::Notice(line.to_string()));
            } else {
                actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "decline" }) });
                actions.push(Action::Notice("No reply in 10 minutes, so I declined it.".to_string()));
            }
            self.show_next_approval(&mut actions);
        }
        if let Some(since) = self.down_since
            && !self.down_notified
            && now >= since + DOWN_NOTICE_MS
        {
            self.down_notified = true;
            let mut text = format!("Mightling's web server on {} isn't answering; I'll keep trying.", self.node());
            if std::mem::take(&mut self.lost_answer) {
                text.push_str(" The answer in progress was lost; send the question again once I'm back.");
            }
            actions.push(Action::Notice(text));
        }
        actions
    }

    fn node(&self) -> &str {
        if self.info.node.is_empty() { "the node" } else { &self.info.node }
    }

    pub fn status(&self, now: u64) -> String {
        let mut lines = Vec::new();
        match &self.phase {
            Phase::Idle => lines.push("Idle.".to_string()),
            Phase::Opening { .. } => lines.push("Opening a thread.".to_string()),
            Phase::Running { started_ms, activity, .. } => {
                let mut line = format!("Answering for {}", duration(now.saturating_sub(*started_ms)));
                if let Some(activity) = activity {
                    line.push_str(&format!(": {activity}"));
                }
                lines.push(line);
            }
        }
        if !self.queue.is_empty() {
            lines.push(format!("{} question(s) queued.", self.queue.len()));
        }
        if self.approval_pending() {
            lines.push("Waiting for your YES or NO.".to_string());
        }
        let thread = self.current.as_ref().and_then(|id| self.recent.iter().find(|t| &t.id == id)).map(|t| t.title.clone());
        lines.push(format!("Thread: {}", thread.unwrap_or_else(|| "new on your next message".to_string())));
        lines.push(format!("Web server on {}: {}", self.node(), if self.connected { "connected" } else { "not answering" }));
        lines.push(format!("Air gap: {}", if self.airgapped() { "on" } else { "off" }));
        if !self.info.bridge_version.is_empty() {
            lines.push(format!("Bridge {}, signal-cli {}", self.info.bridge_version, self.info.signal_cli_version));
        }
        lines.join("\n")
    }

    fn threads_text(&self) -> String {
        if self.recent.is_empty() {
            return "No threads from Signal yet.".to_string();
        }
        let mut lines = vec!["Your recent threads (/use N to continue one):".to_string()];
        for (n, thread) in self.recent.iter().enumerate() {
            let marker = if self.current.as_deref() == Some(thread.id.as_str()) { " ←" } else { "" };
            lines.push(format!("{}. {}{marker}", n + 1, thread.title));
        }
        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(text: &str, ts: u64) -> OwnerMessage {
        OwnerMessage { text: text.to_string(), attachments: vec![], timestamp: ts }
    }

    fn connected() -> Bridge {
        let mut b = Bridge::new(None, vec![], Info { node: "gx10".to_string(), ..Info::default() });
        assert!(b.on_agent(AgentEvent::Connected, 0).is_empty());
        b
    }

    fn notices(actions: &[Action]) -> Vec<String> {
        actions.iter().filter_map(|a| if let Action::Notice(t) = a { Some(t.clone()) } else { None }).collect()
    }

    fn replies(actions: &[Action]) -> Vec<String> {
        actions.iter().filter_map(|a| if let Action::Reply(r) = a { Some(r.parts.iter().map(|p| p.text.clone()).collect::<Vec<_>>().join("|")) } else { None }).collect()
    }

    /// Runs one question to its answer on a fresh thread "t1".
    fn answered(b: &mut Bridge, question: &str, answer: &str, ts: u64) -> Vec<Action> {
        let mut all = b.on_message(msg(question, ts), ts);
        all.extend(b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, ts));
        all.extend(b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, ts));
        all.extend(b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: answer.to_string() }, ts));
        all.extend(b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, ts));
        all
    }

    #[test]
    fn a_question_opens_a_thread_runs_a_turn_and_is_answered() {
        let mut b = connected();
        let first = b.on_message(msg("What is **2+2**?", 7), 0);
        // Typing starts at once, before the thread is open (§19.8).
        assert_eq!(first, vec![Action::Receipt(7), Action::Typing(true), Action::StartThread]);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(ready.contains(&Action::Save));
        assert!(ready.contains(&Action::StartTurn { thread: "t1".to_string(), text: "What is **2+2**?".to_string(), images: vec![] }));
        assert_eq!(b.current.as_deref(), Some("t1"));
        assert_eq!(b.recent[0].title, "What is **2+2**?");
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "It is ".to_string() }, 0);
        b.on_agent(AgentEvent::Delta { thread: "other".to_string(), text: "noise".to_string() }, 0);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "**4**.".to_string() }, 0);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 0);
        assert_eq!(replies(&done), vec!["It is 4."]);
        assert!(done.contains(&Action::Typing(false)));
        assert!(!b.is_busy());
        // The next question goes straight to a turn on the loaded thread.
        let again = b.on_message(msg("and 3+3?", 8), 1);
        assert_eq!(again[1], Action::Typing(true));
        assert!(matches!(&again[2], Action::StartTurn { thread, .. } if thread == "t1"));
    }

    /// The morning of 2026-10-20: a quick second message became a separate turn of its own.
    /// Now it joins the answer in hand: added to the question while it opens, steered into the
    /// turn while it runs (at once, or once the turn's id is known).
    #[test]
    fn quick_follow_ups_join_the_question_in_hand() {
        let mut b = connected();
        b.on_message(msg("compare it with a Dyson", 1), 0);
        // While the thread opens: added to the question, no notice.
        let opening = b.on_message(msg("which is quieter?", 2), 1_000);
        assert_eq!(opening, vec![Action::Receipt(2)]);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 1_000);
        assert!(ready.iter().any(|a| matches!(a, Action::StartTurn { text, .. } if text == "compare it with a Dyson\n\nwhich is quieter?")), "{ready:?}");
        // Before the turn's id: kept, and steered in once it comes.
        assert_eq!(b.on_message(msg("and more reliable 24/7?", 3), 2_000), vec![Action::Receipt(3)]);
        let started = b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 2_000);
        assert_eq!(started, vec![Action::AddToTurn { thread: "t1".to_string(), turn: "u1".to_string(), text: "and more reliable 24/7?".to_string() }]);
        // While it runs: steered in at once.
        let running = b.on_message(msg("in Portugal", 4), 3_000);
        assert_eq!(running, vec![Action::Receipt(4), Action::AddToTurn { thread: "t1".to_string(), turn: "u1".to_string(), text: "in Portugal".to_string() }]);
        // Refused (the turn was ending): it is the next question.
        assert!(b.on_agent(AgentEvent::SteerFailed { thread: "t1".to_string(), text: "in Portugal".to_string() }, 3_100).is_empty());
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 4_000);
        assert!(done.iter().any(|a| matches!(a, Action::StartTurn { text, .. } if text == "in Portugal")), "{done:?}");
    }

    #[test]
    fn what_cannot_join_waits_and_the_waiting_are_asked_as_one() {
        let mut b = connected();
        b.on_message(msg("first", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        // An image after the question has gone, or anything while an approval waits: queued.
        let image = Attachment { id: "x.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        let queued = b.on_message(OwnerMessage { text: "this one".to_string(), attachments: vec![image], timestamp: 2 }, 0);
        assert_eq!(notices(&queued), vec!["Queued; I'll take it next. /stop to interrupt."]);
        let approval = AgentEvent::Approval { id: json!(1), thread: "t1".to_string(), kind: ApprovalKind::Command, command: Some("ls".to_string()), cwd: None, reason: None };
        b.on_agent(approval, 0);
        assert_eq!(notices(&b.on_message(msg("second", 3), 0)), vec!["Queued; I'll take it next. /stop to interrupt."]);
        b.on_message(msg("yes", 4), 0);
        for i in 0..QUEUE_LIMIT - 2 {
            b.on_agent(AgentEvent::Approval { id: json!(10 + i), thread: "t1".to_string(), kind: ApprovalKind::Command, command: None, cwd: None, reason: None }, 0);
            b.on_message(msg("more", 10 + i as u64), 0);
            b.on_message(msg("no", 20 + i as u64), 0);
        }
        b.on_agent(AgentEvent::Approval { id: json!(99), thread: "t1".to_string(), kind: ApprovalKind::Command, command: None, cwd: None, reason: None }, 0);
        let full = b.on_message(msg("too many", 99), 0);
        assert!(notices(&full)[0].contains("already waiting"), "{full:?}");
        // The answer ends: everything that waited is one question.
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 0);
        let Some(Action::Upload { .. }) = done.iter().find(|a| matches!(a, Action::Upload { .. })) else { panic!("{done:?}") };
        let uploaded = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![] }, 0);
        let Some(Action::StartTurn { text, .. }) = uploaded.iter().find(|a| matches!(a, Action::StartTurn { .. })) else { panic!("{uploaded:?}") };
        assert!(text.starts_with("this one\n\nsecond\n\nmore"), "{text}");
    }

    #[test]
    fn stop_interrupts_the_turn_and_empties_the_queue() {
        let mut b = connected();
        b.on_message(msg("long job", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_message(msg("queued", 2), 0);
        // Before the turn id is known, /stop is remembered and sent once it is.
        let stop = b.on_message(msg("/stop", 3), 0);
        assert_eq!(notices(&stop), vec!["Stopped."]);
        assert!(!stop.iter().any(|a| matches!(a, Action::Interrupt { .. })));
        let started = b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        assert_eq!(started, vec![Action::Interrupt { thread: "t1".to_string(), turn: "u1".to_string() }]);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "partial".to_string() }, 0);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some("interrupted".to_string()) }, 0);
        // What was written is kept, marked (MOONSHOT D9); no error line.
        assert_eq!(replies(&done), vec!["partial\n\n(stopped)"]);
        assert!(notices(&done).is_empty(), "{done:?}");
        assert!(!done.iter().any(|a| matches!(a, Action::StartTurn { .. })), "the queue was emptied");
        assert_eq!(notices(&b.on_message(msg("/stop", 4), 0)), vec!["Nothing is running."]);
    }

    #[test]
    fn new_and_use_choose_the_thread() {
        let mut b = connected();
        answered(&mut b, "first question", "a", 1);
        let new = b.on_message(msg("/new", 2), 0);
        assert!(new.contains(&Action::Save));
        assert!(b.current.is_none());
        assert_eq!(b.on_message(msg("second", 3), 0)[2], Action::StartThread);
        b.on_agent(AgentEvent::ThreadReady { thread: "t2".to_string() }, 0);
        b.on_agent(AgentEvent::TurnCompleted { thread: "t2".to_string(), error: None }, 0);
        let list = notices(&b.on_message(msg("/threads", 4), 0)).remove(0);
        assert!(list.contains("1. second ←") && list.contains("2. first question"), "{list}");
        let using = b.on_message(msg("/use 2", 5), 0);
        assert!(using.contains(&Action::CheckUse { thread: "t1".to_string(), title: "first question".to_string() }), "{using:?}");
        let used = b.on_agent(AgentEvent::UseChecked { thread: "t1".to_string(), title: "first question".to_string(), on: Some(false) }, 0);
        assert_eq!(notices(&used), vec!["Continuing: first question"]);
        assert!(used.contains(&Action::Save));
        assert_eq!(b.current.as_deref(), Some("t1"));
        assert_eq!(notices(&b.on_message(msg("/use 9", 6), 0)), vec!["There is no thread 9. /threads lists them."]);
    }

    /// §11: a thread at `/airgapped on` is not used from the phone; `/use` asks its level first and
    /// switches only on a plain `off`, an unanswered question counting as `on`.
    #[test]
    fn use_refuses_a_thread_at_airgapped_on_and_one_whose_level_is_unknown() {
        let mut b = connected();
        answered(&mut b, "first question", "a", 1);
        b.on_message(msg("/new", 2), 0);
        b.on_message(msg("second", 3), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t2".to_string() }, 0);
        b.on_agent(AgentEvent::TurnCompleted { thread: "t2".to_string(), error: None }, 0);
        let using = b.on_message(msg("/use 2", 5), 0);
        assert!(notices(&using).is_empty(), "nothing is said before the level is known: {using:?}");
        assert_eq!(b.current.as_deref(), Some("t2"));
        let sealed = b.on_agent(AgentEvent::UseChecked { thread: "t1".to_string(), title: "first question".to_string(), on: Some(true) }, 0);
        let said = notices(&sealed);
        assert!(said.len() == 1 && said[0].contains("air-gapped") && said[0].contains("first question"), "{said:?}");
        assert!(!sealed.contains(&Action::Save));
        assert_eq!(b.current.as_deref(), Some("t2"));
        let unknown = b.on_agent(AgentEvent::UseChecked { thread: "t1".to_string(), title: "first question".to_string(), on: None }, 0);
        assert!(notices(&unknown)[0].starts_with("Not switched"), "{unknown:?}");
        assert_eq!(b.current.as_deref(), Some("t2"));
    }

    /// §11: the level is asked again before each message goes to the thread, since it may have
    /// changed after `/use`; a thread found at `on` is left, and nothing of the message is sent.
    #[test]
    fn a_thread_found_air_gapped_before_a_message_is_left_and_the_message_not_sent() {
        let mut b = connected();
        answered(&mut b, "first question", "a", 1);
        let asked = b.on_message(msg("more", 2), 0);
        assert!(asked.iter().any(|a| a.relays_to() == Some("t1")), "{asked:?}");
        b.on_message(msg("and this", 3), 0);
        let sealed = b.on_agent(AgentEvent::ThreadSealed { thread: "t1".to_string(), known: true }, 0);
        assert!(sealed.contains(&Action::Typing(false)) && sealed.contains(&Action::Save), "{sealed:?}");
        let said = notices(&sealed);
        assert!(said.len() == 1 && said[0].contains("air-gapped") && said[0].contains("nothing was sent"), "{said:?}");
        assert!(!b.is_busy());
        assert!(b.current.is_none());
        let next = b.on_message(msg("hello", 4), 0);
        assert!(next.contains(&Action::StartThread), "the next question starts a new thread: {next:?}");
    }

    /// A level that cannot be read while a turn runs stops the turn, and its answer is not relayed;
    /// the thread stays current, so the owner can try again once `ling web` answers.
    #[test]
    fn a_level_that_cannot_be_read_stops_the_turn_and_keeps_the_thread() {
        let mut b = connected();
        answered(&mut b, "first question", "a", 1);
        b.on_message(msg("more", 2), 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u2".to_string() }, 0);
        assert_eq!(b.running_thread(), Some("t1"));
        let sealed = b.on_agent(AgentEvent::ThreadSealed { thread: "t1".to_string(), known: false }, 0);
        assert!(sealed.contains(&Action::Interrupt { thread: "t1".to_string(), turn: "u2".to_string() }), "{sealed:?}");
        assert!(notices(&sealed)[0].contains("nothing was sent"), "{sealed:?}");
        assert_eq!(b.running_thread(), None);
        assert_eq!(b.current.as_deref(), Some("t1"));
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "secret".to_string() }, 0);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 0);
        assert!(replies(&done).is_empty() && notices(&done).is_empty(), "{done:?}");
    }

    #[test]
    fn every_action_that_reaches_a_thread_names_it_for_the_air_gap_check() {
        let t = || "t".to_string();
        for action in [
            Action::ResumeThread(t()),
            Action::StartTurn { thread: t(), text: t(), images: Vec::new() },
            Action::AddToTurn { thread: t(), turn: t(), text: t() },
            Action::Steer { thread: t(), turn: t(), text: t() },
            Action::Upload { thread: t(), attachments: Vec::new() },
            Action::SendFile { thread: t(), name: t() },
            Action::FindFiles { thread: t(), since_ms: 0, answer: t() },
        ] {
            assert_eq!(action.relays_to(), Some("t"), "{action:?}");
        }
        for action in [Action::StartThread, Action::StartAskThread, Action::Interrupt { thread: t(), turn: t() }, Action::Save] {
            assert_eq!(action.relays_to(), None, "{action:?}");
        }
    }

    #[test]
    fn a_question_after_a_long_silence_starts_a_new_thread_and_says_how_to_go_back() {
        let mut b = connected();
        let hour = 3_600_000;
        answered(&mut b, "a song", "about falling", 0);
        assert_eq!(b.current.as_deref(), Some("t1"));
        // Within the gap: the same thread.
        let soon = b.on_message(msg("and the chorus?", 2), 3 * hour);
        assert!(notices(&soon).is_empty() && soon.iter().any(|a| matches!(a, Action::StartTurn { thread, .. } if thread == "t1")), "{soon:?}");
        b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 3 * hour);
        // Sixteen hours later: a new thread, in one line.
        let later = b.on_message(msg("compare it with a Dyson", 3), 19 * hour);
        assert_eq!(notices(&later), vec!["New thread (the last message was 16h00m ago); /use 2 to go back."]);
        assert!(later.contains(&Action::StartThread) && later.contains(&Action::Save), "{later:?}");
        b.on_agent(AgentEvent::ThreadReady { thread: "t2".to_string() }, 19 * hour);
        b.on_agent(AgentEvent::TurnCompleted { thread: "t2".to_string(), error: None }, 19 * hour);
        // /use 2 goes back, and is not undone by the next question.
        b.on_message(msg("/use 2", 4), 19 * hour + 1);
        b.on_agent(AgentEvent::UseChecked { thread: "t1".to_string(), title: "a song".to_string(), on: Some(false) }, 19 * hour + 1);
        assert_eq!(b.current.as_deref(), Some("t1"));
        let back = b.on_message(msg("more about the song", 5), 19 * hour + 2);
        assert!(notices(&back).is_empty() && back.iter().any(|a| matches!(a, Action::StartTurn { thread, .. } if thread == "t1")), "{back:?}");
        // Switched off: never.
        b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 19 * hour + 2);
        b.new_thread_after_ms = None;
        assert!(notices(&b.on_message(msg("a week later", 6), 200 * hour)).is_empty());
    }

    #[test]
    fn a_conversation_saved_before_activity_was_kept_starts_fresh() {
        let mut b = Bridge::new(Some("t9".to_string()), vec![ThreadRef { id: "t9".to_string(), title: "old".to_string() }], Info::default());
        b.on_agent(AgentEvent::Connected, 0);
        let first = b.on_message(msg("hello", 1), 5);
        assert_eq!(notices(&first), vec!["New thread; /use 2 to go back."]);
        assert!(first.contains(&Action::StartThread));
    }

    #[test]
    fn a_saved_thread_is_resumed_after_a_restart() {
        let mut b = Bridge::new(Some("t9".to_string()), vec![ThreadRef { id: "t9".to_string(), title: "old".to_string() }], Info::default());
        b.last_activity_ms = Some(0);
        b.on_agent(AgentEvent::Connected, 0);
        assert_eq!(b.on_message(msg("continue", 1), 0)[2], Action::ResumeThread("t9".to_string()));
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t9".to_string() }, 0);
        assert!(!ready.contains(&Action::Save), "a resumed thread is already recorded");
        assert!(ready.iter().any(|a| matches!(a, Action::StartTurn { thread, .. } if thread == "t9")));
    }

    #[test]
    fn a_thread_that_will_not_resume_is_left_for_a_new_one() {
        let mut b = Bridge::new(Some("gone".to_string()), vec![], Info::default());
        b.last_activity_ms = Some(0);
        b.on_agent(AgentEvent::Connected, 0);
        b.on_message(msg("hello", 1), 0);
        let failed = b.on_agent(AgentEvent::ThreadFailed { error: "no such thread".to_string() }, 0);
        assert!(notices(&failed)[0].contains("no such thread"));
        assert!(b.current.is_none());
        assert_eq!(b.on_message(msg("again", 2), 0)[2], Action::StartThread);
    }

    #[test]
    fn approvals_are_relayed_answered_and_timed_out() {
        let mut b = connected();
        b.on_message(msg("install it", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        let ask = |id: i64| AgentEvent::Approval {
            id: json!(id),
            thread: "t1".to_string(),
            kind: ApprovalKind::Command,
            command: Some(format!("pip install x{id}")),
            cwd: Some("/home/u/.mightling/ask/q-1".to_string()),
            reason: None,
        };
        let shown = b.on_agent(ask(5), 0);
        assert_eq!(notices(&shown).len(), 1);
        assert!(notices(&shown)[0].contains("pip install x5") && notices(&shown)[0].ends_with("Reply YES or NO."));
        // A second one waits behind the first.
        assert!(notices(&b.on_agent(ask(6), 0)).is_empty());
        let yes = b.on_message(msg("yes", 2), 1);
        assert!(yes.contains(&Action::AnswerApproval { id: json!(5), result: json!({"decision": "accept"}) }));
        assert!(notices(&yes)[0].contains("pip install x6"));
        let late = b.on_tick(1 + APPROVAL_MS);
        assert!(late.contains(&Action::AnswerApproval { id: json!(6), result: json!({"decision": "decline"}) }));
        assert!(notices(&late).iter().any(|n| n.contains("declined")));
        // With nothing pending, "yes" is a question again (added to the running answer, §19.4).
        assert_eq!(b.on_message(msg("yes", 3), 2), vec![Action::Receipt(3)]);
    }

    #[test]
    fn wider_permissions_are_never_granted_from_the_phone() {
        let mut b = connected();
        let event = AgentEvent::Approval { id: json!("p1"), thread: "t1".to_string(), kind: ApprovalKind::Permissions, command: None, cwd: None, reason: None };
        let actions = b.on_agent(event, 0);
        assert!(actions.contains(&Action::AnswerApproval { id: json!("p1"), result: json!({"decision": "decline"}) }));
    }

    fn statuses(actions: &[Action]) -> Vec<StatusLine> {
        actions.iter().filter_map(|a| if let Action::Status(line) = a { Some(line.clone()) } else { None }).collect()
    }

    fn update(text: &str) -> Vec<StatusLine> {
        vec![StatusLine::Update(text.to_string())]
    }

    #[test]
    fn in_note_to_self_a_status_line_shows_the_work_and_becomes_the_answer() {
        let mut b = connected();
        b.status_line = true;
        // On 2026-10-25 the owner saw nothing for the 40 s of an answer, and asked again elsewhere.
        let first = b.on_message(msg("birthplace of portugal?", 7), 0);
        assert_eq!(first, vec![Action::Receipt(7), Action::Status(StatusLine::Open(WORKING.to_string())), Action::StartThread]);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(statuses(&ready).is_empty(), "one line per question");
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 200);
        b.on_agent(AgentEvent::Activity { thread: "t1".to_string(), text: "searching the web".to_string() }, 300);
        assert!(statuses(&b.on_tick(1_000)).is_empty(), "no faster than one change in {STATUS_EVERY_MS} ms");
        assert_eq!(statuses(&b.on_tick(4_000)), update("Working (4s): searching the web"));
        assert!(statuses(&b.on_tick(9_000)).is_empty(), "nothing new, no edit");
        b.on_agent(AgentEvent::Activity { thread: "t1".to_string(), text: "reading a page".to_string() }, 9_500);
        assert_eq!(statuses(&b.on_tick(10_000)), update("Working (10s): reading a page"));
        // The answer streams into the same message as it is written.
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "Guimarães.".to_string() }, 12_000);
        assert_eq!(statuses(&b.on_tick(14_000)), vec![StatusLine::Stream("Guimarães.".to_string())]);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 15_000);
        let close = done.iter().position(|a| *a == Action::Status(StatusLine::Close)).expect("the line closes");
        let reply = done.iter().position(|a| matches!(a, Action::Reply(_))).expect("an answer");
        assert!(close < reply, "the answer takes the line's place: {done:?}");
        assert!(statuses(&b.on_tick(20_000)).is_empty());
        // The next question gets a line of its own.
        let again = b.on_message(msg("and its castle?", 8), 30_000);
        assert_eq!(again[..2], [Action::Receipt(8), Action::Status(StatusLine::Open(WORKING.to_string()))]);
    }

    #[test]
    fn the_status_line_stands_for_progress_notes_until_it_may_change_no_more() {
        let mut b = connected();
        b.status_line = true;
        b.on_message(msg("a long one", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        // A minute of one command: the line says so instead of a note.
        b.on_agent(AgentEvent::Activity { thread: "t1".to_string(), text: "running `make`".to_string() }, 0);
        assert_eq!(statuses(&b.on_tick(4_000)), update("Working (4s): running `make`"));
        let minute = b.on_tick(FIRST_PROGRESS_MS);
        assert!(notices(&minute).is_empty(), "{minute:?}");
        assert_eq!(statuses(&minute), update("Working (1m00s): running `make`"));
        let mut now = FIRST_PROGRESS_MS;
        for n in 0..10 {
            now += STATUS_EVERY_MS;
            b.on_agent(AgentEvent::Activity { thread: "t1".to_string(), text: format!("step {n}") }, now);
            b.on_tick(now);
        }
        let later = b.on_tick(FIRST_PROGRESS_MS + PROGRESS_EVERY_MS);
        assert!(statuses(&later).is_empty(), "{ACTIVITY_UPDATES} changes at most before the answer");
        assert_eq!(notices(&later).len(), 1, "then the notes come back");
    }

    #[test]
    fn an_answer_streams_into_one_message_after_typing() {
        let mut b = connected();
        b.on_message(msg("birthplace of portugal?", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        assert_eq!(b.on_tick(10_000), vec![Action::Typing(true)], "typing until the first words");
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "The birthplace".to_string() }, 11_000);
        assert_eq!(b.on_tick(11_000), vec![Action::Typing(false), Action::Status(StatusLine::Stream("The birthplace".to_string()))]);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: " is **Guimarães**".to_string() }, 12_000);
        assert!(statuses(&b.on_tick(12_000)).is_empty(), "one edit in {STREAM_EVERY_MS} ms at most");
        assert_eq!(statuses(&b.on_tick(14_000)), vec![StatusLine::Stream("The birthplace is **Guimarães**".to_string())]);
        assert!(b.on_tick(20_000).is_empty(), "nothing new: no edit, and no typing over the words");
        // Signal allows ten edits: the stream stops short of them, and the whole answer is the last.
        let mut now = 20_000;
        let mut edits = 0;
        for n in 0..20 {
            now += STREAM_EVERY_MS;
            b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: format!(" {n}") }, now);
            edits += statuses(&b.on_tick(now)).len();
        }
        assert_eq!(edits as u32, STATUS_UPDATES - 1, "{STATUS_UPDATES} edits in all");
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, now + 1);
        let close = done.iter().position(|a| *a == Action::Status(StatusLine::Close)).expect("the message closes");
        let reply = done.iter().position(|a| matches!(a, Action::Reply(_))).expect("the whole answer");
        assert!(close < reply, "{done:?}");
        // The next answer gets a message of its own.
        b.on_message(msg("and its castle?", 2), now + 10);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u2".to_string() }, now + 10);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "Yes".to_string() }, now + 11);
        assert_eq!(statuses(&b.on_tick(now + 12)), vec![StatusLine::Stream("Yes".to_string())]);
    }

    #[test]
    fn without_streaming_the_answer_comes_whole() {
        let mut b = connected();
        b.stream = false;
        b.on_message(msg("q", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "partial".to_string() }, 1_000);
        assert!(statuses(&b.on_tick(5_000)).is_empty());
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 6_000);
        assert!(statuses(&done).is_empty() && replies(&done) == vec!["partial"], "{done:?}");
    }

    #[test]
    fn stop_closes_the_status_line_with_its_answer() {
        let mut b = connected();
        b.status_line = true;
        b.on_message(msg("long", 1), 0);
        let stopped = b.on_message(msg("/stop", 2), 10);
        assert_eq!(stopped, vec![Action::Receipt(2), Action::Status(StatusLine::Close), Action::Notice("Stopped.".to_string())]);
        // Linked mode never asks for typing it cannot show while a thread opens.
        let next = b.on_message(msg("again", 3), 20);
        assert!(!next.contains(&Action::Typing(true)), "{next:?}");
    }

    #[test]
    fn typing_progress_and_activity_while_a_turn_runs() {
        let mut b = connected();
        b.on_message(msg("run the tests", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::Activity { thread: "t1".to_string(), text: "running `pytest -q`".to_string() }, 0);
        assert_eq!(b.on_tick(5_000), vec![]);
        assert_eq!(b.on_tick(10_000), vec![Action::Typing(true)]);
        let progress = b.on_tick(FIRST_PROGRESS_MS);
        assert_eq!(notices(&progress), vec!["Still working (1m00s): running `pytest -q`"]);
        assert!(notices(&b.on_tick(FIRST_PROGRESS_MS + 1_000)).is_empty());
        assert_eq!(notices(&b.on_tick(FIRST_PROGRESS_MS + PROGRESS_EVERY_MS)).len(), 1);
        assert!(b.status(FIRST_PROGRESS_MS).starts_with("Answering for 1m00s: running"));
    }

    #[test]
    fn a_model_server_failure_says_what_to_do() {
        let mut b = connected();
        b.on_message(msg("hi", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some("error sending request for url (http://127.0.0.1:8000/v1/responses)".to_string()) }, 0);
        assert!(notices(&done)[0].contains("model server on gx10 isn't answering"));
    }

    #[test]
    fn a_benchmark_holding_the_model_says_when_to_ask_again() {
        let mut b = connected();
        b.on_message(msg("hi", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        // What Codex reports after its retries when the model gate answers 503 (`benchmark_running`).
        let gate = "unexpected status 503 Service Unavailable: The model is running a benchmark (night 4, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`., url: http://127.0.0.1:8000/v1/responses";
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some(gate.to_string()) }, 0);
        let notice = &notices(&done)[0];
        assert_eq!(
            notice,
            "The model on gx10 is busy with a benchmark run (night 4, 37/100 done, about 9 h left), so this question was not answered. Ask again when the run has finished."
        );
        assert!(!notice.contains("night pause") && !notice.contains("http") && !notice.contains("503"), "{notice}");
        assert_eq!(benchmark_holds_model("The model is running a benchmark (SWE-bench run r1). Try later or run `ling-admin night pause`.").as_deref(), Some("SWE-bench run r1"));
        assert_eq!(benchmark_holds_model("unexpected status 500 Internal Server Error: boom"), None);
        assert_eq!(benchmark_holds_model("The model is running a benchmark ()."), None);
        // The next question is taken as usual.
        assert!(b.on_message(msg("again", 2), 1_000).iter().any(|a| matches!(a, Action::StartTurn { .. } | Action::StartThread | Action::ResumeThread(_))));
    }

    #[test]
    fn air_gap_on_sends_nothing_and_off_says_what_was_missed() {
        let mut b = connected();
        b.on_message(msg("working", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        let on = b.on_airgap(true, 1_000);
        assert_eq!(on, vec![Action::Interrupt { thread: "t1".to_string(), turn: "u1".to_string() }]);
        assert!(b.on_message(msg("hello?", 2), 2_000).is_empty(), "not even a receipt");
        assert!(b.on_tick(100_000).is_empty());
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 3_000);
        assert!(done.is_empty(), "{done:?}");
        let off = b.on_airgap(false, 1_000 + 3_600_000);
        let text = notices(&off).remove(0);
        assert!(text.contains("air-gapped for 1h00m") && text.contains("1 message(s)"), "{text}");
        assert!(b.on_airgap(false, 0).is_empty());
    }

    #[test]
    fn a_lost_connection_is_reported_once_and_questions_wait_for_it() {
        let mut b = connected();
        b.on_message(msg("q", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::Disconnected, 1_000);
        assert!(!b.is_busy());
        assert!(b.on_tick(5_000).is_empty());
        let notice = notices(&b.on_tick(11_000)).remove(0);
        assert!(notice.contains("isn't answering") && notice.contains("was lost"), "{notice}");
        assert!(b.on_tick(60_000).is_empty(), "only once");
        let waiting = b.on_message(msg("later", 2), 61_000);
        assert!(notices(&waiting)[0].contains("as soon as it is"));
        let back = b.on_agent(AgentEvent::Connected, 70_000);
        assert!(notices(&back)[0].contains("answering again"));
        // The thread must be resumed on the new connection before the queued question runs.
        assert!(back.contains(&Action::ResumeThread("t1".to_string())), "{back:?}");
    }

    #[test]
    fn a_question_opening_its_thread_when_the_connection_drops_is_asked_again() {
        let mut b = connected();
        b.on_message(msg("q", 1), 0);
        b.on_agent(AgentEvent::Disconnected, 1);
        let back = b.on_agent(AgentEvent::Connected, 2);
        assert!(back.contains(&Action::StartThread), "{back:?}");
    }

    #[test]
    fn an_attachment_makes_any_text_a_question() {
        let mut b = connected();
        let image = Attachment { id: "a.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        let actions = b.on_message(OwnerMessage { text: "/new".to_string(), attachments: vec![image.clone()], timestamp: 1 }, 0);
        assert_eq!(actions[2], Action::StartThread);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(ready.iter().any(|a| matches!(a, Action::Upload { attachments, .. } if attachments == &vec![image.clone()])), "{ready:?}");
        assert!(!ready.iter().any(|a| matches!(a, Action::StartTurn { .. })), "the turn waits for the upload");
    }

    fn shot(path: Option<&str>, text: Option<&str>, vision: bool) -> Uploaded {
        Uploaded { name: "shot.jpg".to_string(), image: true, path: path.map(str::to_string), text: text.map(str::to_string), vision, heard: None }
    }

    fn voice_note(heard: Result<&str, &str>) -> Uploaded {
        Uploaded { name: "voice.m4a".to_string(), image: false, path: None, text: None, vision: false, heard: Some(heard.map(str::to_string).map_err(str::to_string)) }
    }

    /// A voice note is transcribed on the node, said back ("Heard: …") and asked (§19.5); a
    /// failure is said and, alone, asks nothing.
    #[test]
    fn a_voice_note_is_heard_echoed_and_asked_and_a_failure_is_said() {
        let mut b = connected();
        let note = Attachment { id: "v.m4a".to_string(), content_type: "audio/aac".to_string(), filename: None, size: None };
        b.on_message(OwnerMessage { text: String::new(), attachments: vec![note.clone()], timestamp: 1 }, 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        let done = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![voice_note(Ok("which one is quieter"))] }, 0);
        assert_eq!(notices(&done), vec!["Heard: which one is quieter"]);
        assert!(done.contains(&Action::StartTurn { thread: "t1".to_string(), text: "which one is quieter".to_string(), images: vec![] }), "{done:?}");
        b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 0);
        b.on_message(OwnerMessage { text: String::new(), attachments: vec![note], timestamp: 2 }, 0);
        let failed = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![voice_note(Err("Speech-to-text is not running"))] }, 0);
        assert!(notices(&failed)[0].starts_with("I couldn't transcribe the voice message (Speech-to-text is not running)"), "{failed:?}");
        assert!(!failed.iter().any(|a| matches!(a, Action::StartTurn { .. })));
        assert!(!b.is_busy());
    }

    /// The morning of 2026-10-20: a screenshot alone in Note to Self reached the model as an empty
    /// message, and the model answered about the thread's previous topic. Now an image with nothing
    /// readable is answered by the bridge, and no turn starts.
    #[test]
    fn an_image_alone_that_cannot_be_read_never_becomes_an_empty_turn() {
        let mut b = connected();
        let image = Attachment { id: "x.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        b.on_message(OwnerMessage { text: String::new(), attachments: vec![image], timestamp: 1 }, 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        // No text read, a model without images: nothing to ask.
        let done = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![shot(Some("/ask/q/shot.jpg"), None, false)] }, 0);
        assert!(!done.iter().any(|a| matches!(a, Action::StartTurn { .. })), "{done:?}");
        assert_eq!(notices(&done), vec![UNREADABLE_IMAGE]);
        assert!(done.contains(&Action::Typing(false)));
        assert!(!b.is_busy());
        // The attachment never arrived (signal-cli had not downloaded it): said so, still no turn.
        let image = Attachment { id: "y.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        b.on_message(OwnerMessage { text: String::new(), attachments: vec![image], timestamp: 2 }, 0);
        let done = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![shot(None, None, true)] }, 0);
        assert_eq!(notices(&done), vec![NOT_RECEIVED]);
        assert!(!done.iter().any(|a| matches!(a, Action::StartTurn { .. })));
    }

    #[test]
    fn a_screenshot_reaches_the_model_as_its_text_and_as_an_image_when_the_model_takes_images() {
        let mut b = connected();
        let image = Attachment { id: "x.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        b.on_message(OwnerMessage { text: String::new(), attachments: vec![image], timestamp: 1 }, 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        let uploads = vec![shot(Some("/ask/q/shot.jpg"), Some("Air purifier HEPA 13"), true)];
        let done = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads }, 0);
        assert_eq!(
            done,
            vec![Action::StartTurn { thread: "t1".to_string(), text: "[Screenshot text: Air purifier HEPA 13]".to_string(), images: vec!["/ask/q/shot.jpg".to_string()] }]
        );
        // Text-only model: the text alone; the question keeps its words first.
        assert_eq!(
            compose("compare it", &[shot(Some("/p.jpg"), Some("Model X"), false)]),
            Composed::Turn { text: "compare it\n\n[Screenshot text: Model X]".to_string(), images: vec![] }
        );
        // An image the model takes but with no text on it: the image alone is a question.
        assert_eq!(compose("", &[shot(Some("/p.jpg"), None, true)]), Composed::Turn { text: String::new(), images: vec!["/p.jpg".to_string()] });
        // Words with an image nobody can read: the words go on, with a note.
        let Composed::Turn { text, images } = compose("what is this?", &[shot(Some("/p.jpg"), None, false)]) else { panic!() };
        assert!(text.starts_with("what is this?") && text.contains("could not be read") && images.is_empty(), "{text}");
        // A file is saved and named.
        let pdf = Uploaded { name: "r.pdf".to_string(), image: false, path: Some("/ask/q/r.pdf".to_string()), text: None, vision: true, heard: None };
        assert_eq!(compose("", &[pdf]), Composed::Turn { text: "[Attached and saved in this thread's folder: r.pdf]".to_string(), images: vec![] });
    }

    #[test]
    fn stop_while_uploading_starts_no_turn() {
        let mut b = connected();
        let image = Attachment { id: "x.jpg".to_string(), content_type: "image/jpeg".to_string(), filename: None, size: None };
        b.on_message(OwnerMessage { text: "look".to_string(), attachments: vec![image], timestamp: 1 }, 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_message(msg("/stop", 2), 0);
        let done = b.on_agent(AgentEvent::Uploaded { thread: "t1".to_string(), uploads: vec![shot(Some("/p.jpg"), Some("text"), true)] }, 0);
        assert!(!done.iter().any(|a| matches!(a, Action::StartTurn { .. })), "{done:?}");
        assert!(!b.is_busy());
    }

    /// The morning of 2026-10-20: one answer ran sixteen minutes. Past its time the agent is told
    /// to answer now; still running, interrupted, and a final turn asks for the answer with no
    /// tools; that one out of time too, the owner gets what it wrote and a line saying so.
    #[test]
    fn an_answer_past_its_time_is_steered_then_interrupted_then_given_a_final_turn() {
        let mut b = connected();
        b.on_message(msg("compare these two purifiers", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        let budget = b.limits.answer_ms.unwrap();
        let steer = b.on_tick(budget);
        assert!(steer.iter().any(|a| matches!(a, Action::Steer { text, .. } if text.contains("Stop now: run no more tools"))), "{steer:?}");
        let interrupt = b.on_tick(budget + limits::GRACE_MS);
        assert!(interrupt.contains(&Action::Interrupt { thread: "t1".to_string(), turn: "u1".to_string() }));
        assert!(notices(&interrupt)[0].starts_with("This is taking too long (this answer has taken 3 min)"), "{interrupt:?}");
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "half a sent".to_string() }, budget + limits::GRACE_MS);
        let ended = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some("interrupted".to_string()) }, budget + limits::GRACE_MS + 1);
        assert_eq!(replies(&ended), vec!["half a sent\n\n(cut off: this answer has taken 3 min)"], "the partial is kept: {ended:?}");
        assert!(notices(&ended).is_empty(), "no error: {ended:?}");
        let Some(Action::StartTurn { text, .. }) = ended.iter().find(|a| matches!(a, Action::StartTurn { .. })) else { panic!("{ended:?}") };
        assert!(text.contains("without running any tool"));
        let start = budget + limits::GRACE_MS + 1;
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u2".to_string() }, start);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "The first one is quieter".to_string() }, start);
        let late = b.on_tick(start + limits::FINAL_BUDGET_MS);
        assert!(late.contains(&Action::Interrupt { thread: "t1".to_string(), turn: "u2".to_string() }), "{late:?}");
        assert!(!notices(&late).iter().any(|n| n.contains("taking too long")), "said once: {late:?}");
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some("interrupted".to_string()) }, start + limits::FINAL_BUDGET_MS);
        assert_eq!(replies(&done), vec!["The first one is quieter"]);
        assert_eq!(notices(&done), vec![limits::GAVE_UP]);
        assert!(!b.is_busy());
    }

    #[test]
    fn the_tool_cap_and_a_long_command_steer_too_and_the_owners_stop_wins() {
        let mut b = connected();
        b.on_message(msg("look it up", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        b.on_agent(AgentEvent::TurnStarted { thread: "t1".to_string(), turn: "u1".to_string() }, 0);
        b.on_agent(AgentEvent::ToolStarted { thread: "t1".to_string(), item: "c1".to_string(), command: Some("sleep 60; curl https://www.google.com/search?q=x".to_string()) }, 1_000);
        let steer = b.on_tick(61_000);
        assert!(steer.iter().any(|a| matches!(a, Action::Steer { text, .. } if text.contains("the command `sleep 60; curl"))), "{steer:?}");
        // The owner's /stop: no final turn.
        b.on_message(msg("/stop", 2), 62_000);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: Some("interrupted".to_string()) }, 63_000);
        assert!(!done.iter().any(|a| matches!(a, Action::StartTurn { .. })), "{done:?}");
    }

    #[test]
    fn a_ling_web_without_the_chat_prompt_gets_an_ask_thread() {
        let mut b = connected();
        assert_eq!(b.on_message(msg("hi", 1), 0)[2], Action::StartThread);
        let fallback = b.on_agent(AgentEvent::ThreadFailed { error: "no prompt named \"chat\" may be chosen here; named prompts: ask, default".to_string() }, 0);
        assert_eq!(fallback, vec![Action::StartAskThread]);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(ready.iter().any(|a| matches!(a, Action::StartTurn { .. })));
    }

    /// Files back (§19.6): a file the agent made and named in its answer is offered after it, and
    /// sent only on YES; one not named, or the owner's own upload, is not offered; none at air gap on.
    #[test]
    fn a_file_the_agent_made_and_named_is_offered_and_sent_only_on_yes() {
        let mut b = connected();
        b.on_message(msg("compare them in a table", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 5);
        b.on_agent(AgentEvent::Delta { thread: "t1".to_string(), text: "The table is in report.md.".to_string() }, 5);
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 9);
        assert!(done.contains(&Action::FindFiles { thread: "t1".to_string(), since_ms: 5, answer: "The table is in report.md.".to_string() }), "{done:?}");
        let files = vec![("report.md".to_string(), 2048), ("scratch.py".to_string(), 10), ("huge.bin".to_string(), FILE_LIMIT + 1)];
        let offered = b.on_agent(AgentEvent::Made { thread: "t1".to_string(), files, answer: "The table is in report.md.".to_string() }, 10);
        assert_eq!(notices(&offered), vec!["Mightling made report.md (2 KB). Reply YES to get it here, or NO."]);
        let yes = b.on_message(msg("yes", 2), 11);
        assert!(yes.contains(&Action::SendFile { thread: "t1".to_string(), name: "report.md".to_string() }), "{yes:?}");
        assert!(!yes.iter().any(|a| matches!(a, Action::AnswerApproval { .. })), "the agent is not answered");
        // NO keeps it; at air gap on nothing is offered.
        b.on_agent(AgentEvent::Made { thread: "t1".to_string(), files: vec![("report.md".to_string(), 1)], answer: "report.md".to_string() }, 12);
        assert_eq!(notices(&b.on_message(msg("no", 3), 13)), vec!["Not sent; it stays in the thread's folder."]);
        b.on_airgap(true, 14);
        assert!(b.on_agent(AgentEvent::Made { thread: "t1".to_string(), files: vec![("report.md".to_string(), 1)], answer: "report.md".to_string() }, 15).is_empty());
    }

    /// Night Shift from the phone (§19.7): `/night add` carries the last exchange, is confirmed
    /// before anything is queued (YES for one repository, a number for several), and NO queues nothing.
    #[test]
    fn night_add_is_confirmed_and_carries_the_last_exchange() {
        let mut b = connected();
        answered(&mut b, "why does the parser drop tabs?", "Because split_whitespace eats them.", 1);
        let asked = b.on_message(msg("/night add fix it so tabs survive", 2), 10);
        let Some(Action::NightRepos { task }) = asked.iter().find(|a| matches!(a, Action::NightRepos { .. })).cloned() else { panic!("{asked:?}") };
        assert!(task.starts_with("fix it so tabs survive\n\nContext, from the phone conversation") && task.contains("Question: why does the parser drop tabs?") && task.contains("Answer: Because split_whitespace"), "{task}");
        let one = vec![("/w/app".to_string(), "app".to_string())];
        let confirm = b.on_agent(AgentEvent::NightRepos { task: task.clone(), repos: one }, 11);
        assert!(notices(&confirm)[0].starts_with("Queue this for Night Shift tonight, in app?") && notices(&confirm)[0].ends_with("Reply YES or NO."));
        let yes = b.on_message(msg("yes", 3), 12);
        assert!(yes.contains(&Action::NightAdd { repo: "/w/app".to_string(), task: task.clone() }), "{yes:?}");
        // Several: a number chooses; YES alone asks again; NO queues nothing.
        let two = vec![("/w/app".to_string(), "app".to_string()), ("/w/lib".to_string(), "lib".to_string())];
        let listed = b.on_agent(AgentEvent::NightRepos { task: "t".to_string(), repos: two.clone() }, 13);
        assert!(notices(&listed)[0].contains("1. app\n2. lib"), "{listed:?}");
        assert_eq!(notices(&b.on_message(msg("yes", 4), 14)), vec!["Reply with the repository's number (1–2), or NO."]);
        assert!(b.on_message(msg("2", 5), 15).contains(&Action::NightAdd { repo: "/w/lib".to_string(), task: "t".to_string() }));
        b.on_agent(AgentEvent::NightRepos { task: "t".to_string(), repos: two }, 16);
        assert_eq!(notices(&b.on_message(msg("no", 6), 17)), vec!["Not queued."]);
        // None set up: says how.
        assert_eq!(notices(&b.on_agent(AgentEvent::NightRepos { task: "t".to_string(), repos: vec![] }, 18)), vec![NO_NIGHT_REPOS]);
        assert!(b.on_message(msg("/night status", 7), 19).contains(&Action::NightStatus));
        assert!(notices(&b.on_message(msg("/night", 8), 20))[0].starts_with("/night add <task>"));
    }

    #[test]
    fn durations_read_naturally() {
        assert_eq!(duration(45_000), "45s");
        assert_eq!(duration(130_000), "2m10s");
        assert_eq!(duration(3_900_000), "1h05m");
    }
}
