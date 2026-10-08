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
    StartThread,
    ResumeThread(String),
    /// Upload the attachments into the thread's Ask folder, then start the turn.
    StartTurn { thread: String, text: String, attachments: Vec<Attachment> },
    Interrupt { thread: String, turn: String },
    /// A JSON-RPC response to a server request: `{decision: accept|decline}`.
    AnswerApproval { id: Value, result: Value },
    /// The persistent part of the state changed.
    Save,
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
}

#[derive(Clone, Debug)]
enum Phase {
    Idle,
    /// A thread is being started or resumed for this message.
    Opening { message: OwnerMessage, fresh: bool },
    Running { thread: String, turn: Option<String>, started_ms: u64, last_typing_ms: u64, next_progress_ms: u64, answer: String, activity: Option<String>, stopping: bool },
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
                    let decision = if input == Input::Yes { "accept" } else { "decline" };
                    actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": decision }) });
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
            Input::Use(n) => match self.recent.get(n - 1).cloned() {
                Some(thread) => {
                    self.current = Some(thread.id.clone());
                    actions.push(Action::Notice(format!("Continuing: {}", thread.title)));
                    actions.push(Action::Save);
                }
                None => actions.push(Action::Notice(format!("There is no thread {n}. /threads lists them."))),
            },
            Input::Help => actions.push(Action::Notice(command::HELP.to_string())),
            Input::Question(_) => {
                if self.is_busy() || !self.connected {
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
            }
        }
        actions
    }

    /// Starts work on one question: opens its thread if needed, else starts the turn.
    fn begin(&mut self, message: OwnerMessage, now: u64) -> Vec<Action> {
        match self.current.clone() {
            None => {
                self.phase = Phase::Opening { message, fresh: true };
                vec![Action::StartThread]
            }
            Some(thread) if !self.loaded.contains(&thread) => {
                self.phase = Phase::Opening { message, fresh: false };
                vec![Action::ResumeThread(thread)]
            }
            Some(thread) => self.run(thread, message, now),
        }
    }

    fn run(&mut self, thread: String, message: OwnerMessage, now: u64) -> Vec<Action> {
        self.phase = Phase::Running {
            thread: thread.clone(),
            turn: None,
            started_ms: now,
            last_typing_ms: now,
            next_progress_ms: now + FIRST_PROGRESS_MS,
            answer: String::new(),
            activity: None,
            stopping: false,
        };
        vec![Action::Typing(true), Action::StartTurn { thread, text: message.text, attachments: message.attachments }]
    }

    /// Takes the next queued question, if the bridge is free.
    fn next(&mut self, now: u64) -> Vec<Action> {
        if self.is_busy() || !self.connected || self.airgapped() {
            return Vec::new();
        }
        match self.queue.pop_front() {
            Some(message) => self.begin(message, now),
            None => Vec::new(),
        }
    }

    fn stop(&mut self) -> Vec<Action> {
        let mut actions = Vec::new();
        let dropped = self.queue.len();
        self.queue.clear();
        while let Some(approval) = self.approvals.pop_front() {
            actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "cancel" }) });
        }
        match &mut self.phase {
            Phase::Running { thread, turn, stopping, .. } => {
                *stopping = true;
                if let Some(turn) = turn {
                    actions.push(Action::Interrupt { thread: thread.clone(), turn: turn.clone() });
                }
                actions.push(Action::Typing(false));
                actions.push(Action::Notice("Stopped.".to_string()));
            }
            Phase::Opening { .. } => {
                self.phase = Phase::Idle;
                actions.push(Action::Notice("Stopped.".to_string()));
            }
            Phase::Idle if dropped > 0 => actions.push(Action::Notice("Stopped; the queue is empty.".to_string())),
            Phase::Idle => actions.push(Action::Notice("Nothing is running.".to_string())),
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
                    Phase::Running { stopping: false, .. } => self.lost_answer = true,
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
            AgentEvent::ThreadFailed { error } => {
                if let Phase::Opening { fresh, .. } = std::mem::replace(&mut self.phase, Phase::Idle) {
                    if !fresh {
                        // A thread that will not resume is left; the next question starts a new one.
                        self.current = None;
                        actions.push(Action::Save);
                    }
                    actions.push(Action::Notice(format!("Couldn't open the thread: {error}")));
                    actions.extend(self.next(now));
                }
            }
            AgentEvent::TurnStarted { thread, turn } => {
                if let Phase::Running { thread: running, turn: slot, stopping, .. } = &mut self.phase
                    && *running == thread
                {
                    *slot = Some(turn.clone());
                    if *stopping {
                        actions.push(Action::Interrupt { thread, turn });
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
                if running && let Phase::Running { answer, stopping, .. } = std::mem::replace(&mut self.phase, Phase::Idle) {
                    self.approvals.clear();
                    if !self.airgapped() && !stopping {
                        actions.push(Action::Typing(false));
                        match error {
                            Some(error) if model_server_down(&error) => actions.push(Action::Notice(format!(
                                "The model server on {} isn't answering (`ling-admin server start` on the node).",
                                self.node()
                            ))),
                            Some(error) => actions.push(Action::Notice(format!("The answer failed: {error}"))),
                            None if answer.trim().is_empty() => actions.push(Action::Notice("(Mightling finished without writing an answer.)".to_string())),
                            None => actions.push(Action::Reply(format::reply(answer.trim()))),
                        }
                    }
                    actions.extend(self.next(now));
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
                    self.approvals.push_back(Approval { id, text, deadline_ms: now + APPROVAL_MS, shown: false });
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
                while let Some(approval) = self.approvals.pop_front() {
                    actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "cancel" }) });
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
        if let Phase::Running { started_ms, last_typing_ms, next_progress_ms, activity, stopping: false, .. } = &mut self.phase {
            if now >= *last_typing_ms + TYPING_EVERY_MS {
                *last_typing_ms = now;
                actions.push(Action::Typing(true));
            }
            if now >= *next_progress_ms {
                *next_progress_ms = now + PROGRESS_EVERY_MS;
                let mut text = format!("Still working ({})", duration(now - *started_ms));
                if let Some(activity) = activity {
                    text.push_str(&format!(": {activity}"));
                }
                actions.push(Action::Notice(text));
            }
        }
        while self.approvals.front().is_some_and(|a| now >= a.deadline_ms) {
            let approval = self.approvals.pop_front().unwrap();
            actions.push(Action::AnswerApproval { id: approval.id, result: json!({ "decision": "decline" }) });
            actions.push(Action::Notice("No reply in 10 minutes, so I declined it.".to_string()));
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
        assert_eq!(first, vec![Action::Receipt(7), Action::StartThread]);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(ready.contains(&Action::Save));
        assert!(ready.contains(&Action::StartTurn { thread: "t1".to_string(), text: "What is **2+2**?".to_string(), attachments: vec![] }));
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

    #[test]
    fn a_question_during_a_turn_is_queued_then_taken() {
        let mut b = connected();
        b.on_message(msg("first", 1), 0);
        b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        let queued = b.on_message(msg("second", 2), 0);
        assert_eq!(notices(&queued), vec!["Queued; I'll take it next. /stop to interrupt."]);
        for i in 0..QUEUE_LIMIT - 1 {
            b.on_message(msg("more", 10 + i as u64), 0);
        }
        let full = b.on_message(msg("too many", 99), 0);
        assert!(notices(&full)[0].contains("already waiting"));
        let done = b.on_agent(AgentEvent::TurnCompleted { thread: "t1".to_string(), error: None }, 0);
        assert!(done.iter().any(|a| matches!(a, Action::StartTurn { text, .. } if text == "second")));
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
        assert!(replies(&done).is_empty() && notices(&done).is_empty(), "{done:?}");
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
        assert_eq!(b.on_message(msg("second", 3), 0)[1], Action::StartThread);
        b.on_agent(AgentEvent::ThreadReady { thread: "t2".to_string() }, 0);
        b.on_agent(AgentEvent::TurnCompleted { thread: "t2".to_string(), error: None }, 0);
        let list = notices(&b.on_message(msg("/threads", 4), 0)).remove(0);
        assert!(list.contains("1. second ←") && list.contains("2. first question"), "{list}");
        let using = b.on_message(msg("/use 2", 5), 0);
        assert_eq!(notices(&using), vec!["Continuing: first question"]);
        assert_eq!(b.current.as_deref(), Some("t1"));
        assert_eq!(notices(&b.on_message(msg("/use 9", 6), 0)), vec!["There is no thread 9. /threads lists them."]);
    }

    #[test]
    fn a_saved_thread_is_resumed_after_a_restart() {
        let mut b = Bridge::new(Some("t9".to_string()), vec![ThreadRef { id: "t9".to_string(), title: "old".to_string() }], Info::default());
        b.on_agent(AgentEvent::Connected, 0);
        assert_eq!(b.on_message(msg("continue", 1), 0)[1], Action::ResumeThread("t9".to_string()));
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t9".to_string() }, 0);
        assert!(!ready.contains(&Action::Save), "a resumed thread is already recorded");
        assert!(ready.iter().any(|a| matches!(a, Action::StartTurn { thread, .. } if thread == "t9")));
    }

    #[test]
    fn a_thread_that_will_not_resume_is_left_for_a_new_one() {
        let mut b = Bridge::new(Some("gone".to_string()), vec![], Info::default());
        b.on_agent(AgentEvent::Connected, 0);
        b.on_message(msg("hello", 1), 0);
        let failed = b.on_agent(AgentEvent::ThreadFailed { error: "no such thread".to_string() }, 0);
        assert!(notices(&failed)[0].contains("no such thread"));
        assert!(b.current.is_none());
        assert_eq!(b.on_message(msg("again", 2), 0)[1], Action::StartThread);
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
        // With nothing pending, "yes" is a question again (queued behind the running turn).
        assert!(notices(&b.on_message(msg("yes", 3), 2))[0].starts_with("Queued"));
    }

    #[test]
    fn wider_permissions_are_never_granted_from_the_phone() {
        let mut b = connected();
        let event = AgentEvent::Approval { id: json!("p1"), thread: "t1".to_string(), kind: ApprovalKind::Permissions, command: None, cwd: None, reason: None };
        let actions = b.on_agent(event, 0);
        assert!(actions.contains(&Action::AnswerApproval { id: json!("p1"), result: json!({"decision": "decline"}) }));
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
        assert_eq!(actions[1], Action::StartThread);
        let ready = b.on_agent(AgentEvent::ThreadReady { thread: "t1".to_string() }, 0);
        assert!(ready.iter().any(|a| matches!(a, Action::StartTurn { attachments, .. } if attachments == &vec![image.clone()])));
    }

    #[test]
    fn durations_read_naturally() {
        assert_eq!(duration(45_000), "45s");
        assert_eq!(duration(130_000), "2m10s");
        assert_eq!(duration(3_900_000), "1h05m");
    }
}
