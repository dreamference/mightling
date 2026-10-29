//! The sync hub's live half (specs/DREAMFERENCE_MIGHTLING_SYNC.md §6, Phase 2): following a turn
//! that runs on another device, and answering it. Plain state and functions, so it is tested
//! without a server; `sync_hub.rs` puts it behind `/api/sync/host`, `/api/sync/live/…`,
//! `/api/sync/commands` and `/api/sync/notices`.
//!
//! - **Hosts.** A host is a process on a device that holds an app-server connection: the desktop
//!   app's main process, or the device's sync agent attached to an app-server that listens on a
//!   socket (the TUI's control socket, `ling web`'s). It posts what its app-server says about the
//!   device's own threads (turns, items, deltas, approvals waiting) and takes the commands meant
//!   for them. A device is **online** while one of its hosts has posted in the last
//!   [`ONLINE_FOR`].
//! - **Following.** Each thread keeps its last [`BUFFER`] events, numbered, so a phone that opens
//!   late catches up, and the hub derives whether a turn runs and which approvals wait.
//! - **Answering.** A device with `control` sends a message, answers an approval, stops a turn or
//!   starts a thread on an online device. The hub hands the command to the host that holds the
//!   thread; that host vets it with its own copy of `policy.json`, exactly as if the user had acted
//!   there, and says how it went.
//! - **Offline.** A message to an offline device waits on the hub, at most [`QUEUE_PER_THREAD`]
//!   per thread for [`QUEUE_FOR`], and is delivered in order when a host appears; the queue is a
//!   file, so a restart keeps it. An approval, a stop or a new thread is never queued: decided
//!   hours later, on a turn that has moved on, it is not what the user meant.
//! - **Notices.** A finished turn or a waiting approval becomes a short notice, at most one per
//!   thread per [`NOTICE_EVERY`], which the phone bridges read and pass on.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::HashMap;
use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;

use crate::auth;
use crate::sync_store::Caller;

/// Events kept per thread.
pub const BUFFER: usize = 2000;
/// A host that has not posted for this long is gone, and its device offline.
pub const ONLINE_FOR: Duration = Duration::from_secs(40);
/// Messages kept for an offline device, per thread.
pub const QUEUE_PER_THREAD: usize = 10;
/// How long a message waits for an offline device.
pub const QUEUE_FOR: Duration = Duration::from_secs(24 * 60 * 60);
/// How long a host has to say how a command went.
pub const ANSWER_WITHIN: Duration = Duration::from_secs(120);
/// At most one notice per thread this often.
pub const NOTICE_EVERY: Duration = Duration::from_secs(5 * 60);
/// The largest event kept; a bigger one (a huge command output) is kept as a stub.
pub const EVENT_CAP: usize = 256 << 10;
/// The longest message a phone may send.
pub const TEXT_CAP: usize = 100_000;
/// Notices kept.
const NOTICES: usize = 200;
/// Commands remembered after they were answered, for their senders to read.
const DONE_KEPT: usize = 500;

/// The app-server's notifications a host passes on: what a following device draws.
pub const FOLLOWED: &[&str] = &[
    "turn/started",
    "turn/completed",
    "item/started",
    "item/completed",
    "item/agentMessage/delta",
    "serverRequest/resolved",
    "thread/status/changed",
    "thread/name/updated",
    "error",
];

/// The server requests a following device may answer: the two approvals. Any other question (a
/// tool asking for input, an MCP elicitation) is answered on the device itself.
pub const ANSWERABLE: &[&str] = &["item/commandExecution/requestApproval", "item/fileChange/requestApproval"];

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// One host's post.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct HostPost {
    pub host: String,
    pub kind: String,
    pub holding: Vec<String>,
    pub models: Vec<String>,
    pub projects: Vec<String>,
    pub events: Vec<HostEvent>,
    pub acks: Vec<Ack>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct HostEvent {
    pub thread: String,
    pub message: Value,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Ack {
    pub id: u64,
    pub ok: bool,
    pub error: Option<String>,
    pub thread: Option<String>,
}

/// What a following device asks a device to do.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Command {
    pub id: u64,
    pub kind: String,
    pub device: String,
    pub thread: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    /// Who sent it, for the owner's screen ("approved from the phone").
    pub by: String,
    pub at: u64,
    pub state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip)]
    delivered: Option<(String, SystemTime)>,
}

/// What a following device asked, before the hub accepted it.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Ask {
    pub kind: String,
    pub thread: Option<String>,
    pub device: Option<String>,
    pub text: Option<String>,
    pub model: Option<String>,
    pub request: Option<Value>,
    pub decision: Option<String>,
    pub cwd: Option<String>,
}

#[derive(Debug, PartialEq)]
pub enum Refused {
    Forbidden(&'static str),
    BadRequest(String),
    Offline(String),
    QueueFull,
}

#[derive(Debug)]
struct Host {
    device: String,
    kind: String,
    seen: SystemTime,
    holding: BTreeSet<String>,
    models: Vec<String>,
    projects: Vec<String>,
}

#[derive(Debug, Default)]
struct ThreadLive {
    owner: String,
    events: VecDeque<(u64, Value)>,
    /// The number of the newest event dropped from the buffer: a follower behind it missed some.
    dropped: u64,
    turn: Option<String>,
    pending: BTreeMap<String, Value>,
    title: String,
    last_notice: Option<SystemTime>,
}

#[derive(Clone, Debug, Serialize)]
pub struct Notice {
    pub seq: u64,
    pub thread: String,
    pub device: String,
    pub kind: String,
    pub text: String,
}

#[derive(Debug, Default)]
struct Inner {
    seq: u64,
    next_command: u64,
    threads: HashMap<String, ThreadLive>,
    hosts: HashMap<String, Host>,
    commands: Vec<Command>,
    notices: VecDeque<Notice>,
}

/// What a following device sees of one thread.
#[derive(Debug, Default)]
pub struct Following {
    pub events: Vec<(u64, Value)>,
    pub next: u64,
    pub reset: bool,
    pub running: bool,
    pub turn: Option<String>,
    pub pending: Vec<Value>,
    pub queued: Vec<Command>,
}

/// A device as the commands see it.
#[derive(Debug, Default, Serialize)]
pub struct Presence {
    pub online: bool,
    pub last_seen: u64,
    pub models: Vec<String>,
    pub projects: Vec<String>,
}

/// The hub's live state. In memory but for the offline queue, which is `queue.json` in the hub's
/// folder.
#[derive(Debug)]
pub struct Live {
    queue_file: PathBuf,
    inner: Mutex<Inner>,
    changed: tokio::sync::watch::Sender<u64>,
}

impl Live {
    pub fn new(hub_dir: PathBuf) -> Live {
        let queue_file = hub_dir.join("queue.json");
        let mut inner = Inner::default();
        if let Some(queued) = std::fs::read_to_string(&queue_file).ok().and_then(|text| serde_json::from_str::<Vec<Command>>(&text).ok()) {
            inner.next_command = queued.iter().map(|command| command.id).max().unwrap_or(0);
            inner.commands = queued;
        }
        let (changed, _) = tokio::sync::watch::channel(0);
        Live { queue_file, inner: Mutex::new(inner), changed }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn bump(&self, inner: &mut Inner) {
        inner.seq += 1;
        let _ = self.changed.send(inner.seq);
    }

    /// Waits until something changes after `seen` (a [`Live::seq`] reading), or `wait` runs out.
    pub async fn wait_past(&self, seen: u64, wait: Duration) {
        let mut receiver = self.changed.subscribe();
        if *receiver.borrow_and_update() > seen || self.seq() > seen {
            return;
        }
        let _ = tokio::time::timeout(wait, async {
            while receiver.changed().await.is_ok() {
                if *receiver.borrow() > seen {
                    return;
                }
            }
        })
        .await;
    }

    fn save_queue(&self, inner: &Inner) {
        let waiting: Vec<&Command> = inner.commands.iter().filter(|command| command.state == "waiting").collect();
        if waiting.is_empty() && !self.queue_file.exists() {
            return;
        }
        let _ = auth::write_private(&self.queue_file, &serde_json::to_string(&waiting).unwrap_or_default());
    }

    fn online_hosts<'a>(inner: &'a Inner, device: &str, now: SystemTime) -> impl Iterator<Item = (&'a String, &'a Host)> {
        inner.hosts.iter().filter(move |(_, host)| host.device == device && now.duration_since(host.seen).unwrap_or_default() < ONLINE_FOR)
    }

    /// Whether a device has a host now, when one was last seen, and what its hosts offer.
    pub fn presence(&self, device: &str) -> Presence {
        let inner = self.lock();
        let now = SystemTime::now();
        let mut presence = Presence::default();
        for host in inner.hosts.values().filter(|host| host.device == device) {
            let seen = host.seen.duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            presence.last_seen = presence.last_seen.max(seen);
            if now.duration_since(host.seen).unwrap_or_default() < ONLINE_FOR {
                presence.online = true;
                presence.models.extend(host.models.iter().cloned());
                presence.projects.extend(host.projects.iter().cloned());
            }
        }
        presence.models.sort();
        presence.models.dedup();
        presence.projects.sort();
        presence.projects.dedup();
        presence
    }

    /// Commands nobody answered in time and those that waited too long, wherever a command is
    /// looked at, so one handed to a host that went away does not read as delivered for ever.
    /// Whether any changed; an expired waiting message also leaves `queue.json`.
    fn expire(&self, inner: &mut Inner, now: SystemTime) -> bool {
        let mut changed = false;
        let mut unqueued = false;
        for command in inner.commands.iter_mut() {
            if command.state == "delivered" && command.delivered.as_ref().is_some_and(|(_, at)| now.duration_since(*at).unwrap_or_default() > ANSWER_WITHIN) {
                command.state = "failed".to_string();
                command.error = Some("the device did not say how it went".to_string());
                changed = true;
            }
            // A command handed to an online host that went away before taking it: an approval, a
            // stop or a new thread is never kept for later, a message no longer than one waiting.
            let age = now.duration_since(UNIX_EPOCH + Duration::from_secs(command.at)).unwrap_or_default();
            if command.state == "queued" && age > if command.kind == "send" { QUEUE_FOR } else { ANSWER_WITHIN } {
                command.state = "expired".to_string();
                changed = true;
            }
            if command.state == "waiting" && age > QUEUE_FOR {
                command.state = "expired".to_string();
                changed = true;
                unqueued = true;
            }
        }
        if unqueued {
            self.save_queue(inner);
        }
        changed
    }

    /// A host's post: its events and answers in, the commands meant for it out. `owner_of` names a
    /// thread's owner as the store has it (None for a thread not published yet).
    pub fn host_post(&self, caller: &Caller, post: HostPost, owner_of: &dyn Fn(&str) -> Option<String>, title_of: &dyn Fn(&str) -> Option<String>) -> Vec<Command> {
        let now = SystemTime::now();
        let mut inner = self.lock();
        let host_id = if post.host.trim().is_empty() { caller.device.clone() } else { post.host.chars().take(64).collect() };
        let holding: BTreeSet<String> = post
            .holding
            .into_iter()
            .filter(|thread| owner_of(thread).is_none_or(|owner| owner == caller.device))
            .take(1000)
            .collect();
        inner.hosts.insert(
            host_id.clone(),
            Host {
                device: caller.device.clone(),
                kind: post.kind.chars().take(16).collect(),
                seen: now,
                holding: holding.clone(),
                models: post.models.into_iter().take(50).collect(),
                projects: post.projects.into_iter().take(50).collect(),
            },
        );
        let mut changed = false;
        for event in post.events {
            if event.thread.is_empty() || event.thread.len() > 128 || owner_of(&event.thread).is_some_and(|owner| owner != caller.device) {
                continue;
            }
            // A thread the store does not hold yet (its rollout not uploaded, or kept on the
            // device by `[sync] exclude`) is buffered but shown to nobody and makes no notice.
            let title = title_of(&event.thread);
            self.ingest(&mut inner, &caller.device, &event.thread, event.message, title.as_deref(), now);
            changed = true;
        }
        for ack in post.acks {
            if let Some(command) = inner.commands.iter_mut().find(|command| command.id == ack.id && command.device == caller.device) {
                command.state = if ack.ok { "done" } else { "failed" }.to_string();
                command.error = ack.error.map(|error| error.chars().take(500).collect());
                if command.thread.is_none() {
                    command.thread = ack.thread;
                }
                changed = true;
            }
        }
        changed |= self.expire(&mut inner, now);
        // Commands for this host: its device's, for a thread it holds, or that no host of the
        // device holds (a host can resume a thread), oldest first.
        let held_elsewhere: BTreeSet<String> = Self::online_hosts(&inner, &caller.device, now)
            .filter(|(id, _)| **id != host_id)
            .flat_map(|(_, host)| host.holding.iter().cloned())
            .collect();
        // A thread no host holds is resumed by the desktop app when it is open, not by an agent
        // attached to another server, where the owner would not see it.
        let desktop_elsewhere = Self::online_hosts(&inner, &caller.device, now).any(|(id, host)| *id != host_id && host.kind == "desktop");
        let this_is_agent = inner.hosts.get(&host_id).is_some_and(|host| host.kind != "desktop");
        let mut out = Vec::new();
        let mut requeue = false;
        for command in inner.commands.iter_mut() {
            let mine = command.device == caller.device
                && (command.state == "queued" || command.state == "waiting")
                && match &command.thread {
                    Some(thread) if holding.contains(thread) => true,
                    Some(thread) if held_elsewhere.contains(thread) => false,
                    _ => !(this_is_agent && desktop_elsewhere),
                };
            if mine {
                requeue |= command.state == "waiting";
                command.state = "delivered".to_string();
                command.delivered = Some((host_id.clone(), now));
                out.push(command.clone());
            }
        }
        if requeue {
            self.save_queue(&inner);
        }
        let done = inner.commands.iter().filter(|command| matches!(command.state.as_str(), "done" | "failed" | "expired")).count();
        if done > DONE_KEPT {
            let mut drop = done - DONE_KEPT;
            inner.commands.retain(|command| {
                let old = matches!(command.state.as_str(), "done" | "failed" | "expired");
                if old && drop > 0 {
                    drop -= 1;
                    false
                } else {
                    true
                }
            });
        }
        if changed || !out.is_empty() {
            self.bump(&mut inner);
        }
        out
    }

    fn ingest(&self, inner: &mut Inner, device: &str, thread: &str, message: Value, title: Option<&str>, now: SystemTime) {
        let text = message.to_string();
        let message = if text.len() > EVENT_CAP {
            json!({ "method": message.get("method").cloned().unwrap_or(Value::Null), "params": { "threadId": thread, "truncated": text.len() } })
        } else {
            message
        };
        inner.seq += 1;
        let seq = inner.seq;
        let live = inner.threads.entry(thread.to_string()).or_default();
        live.owner = device.to_string();
        if let Some(title) = title {
            live.title = title.to_string();
        }
        let method = message.get("method").and_then(Value::as_str).unwrap_or_default().to_string();
        let mut notice = None;
        match method.as_str() {
            "turn/started" => live.turn = message.pointer("/params/turn/id").and_then(Value::as_str).map(str::to_string).or(Some(String::new())),
            "turn/completed" => {
                live.turn = None;
                live.pending.clear();
                notice = Some(("finished", format!("Turn finished: {}", live.title)));
            }
            "serverRequest/resolved" => {
                if let Some(id) = message.pointer("/params/requestId") {
                    live.pending.remove(&id.to_string());
                }
            }
            _ => {}
        }
        if let Some(id) = message.get("id").filter(|_| !method.is_empty()) {
            live.pending.insert(id.to_string(), message.clone());
            notice = Some(("approval", format!("Approval needed: {}", live.title)));
        }
        live.events.push_back((seq, message));
        while live.events.len() > BUFFER {
            if let Some((seq, _)) = live.events.pop_front() {
                live.dropped = seq;
            }
        }
        if let Some((kind, text)) = notice.filter(|_| title.is_some()) {
            let due = live.last_notice.is_none_or(|at| now.duration_since(at).unwrap_or_default() >= NOTICE_EVERY);
            if due {
                live.last_notice = Some(now);
                inner.notices.push_back(Notice { seq, thread: thread.to_string(), device: device.to_string(), kind: kind.to_string(), text });
                while inner.notices.len() > NOTICES {
                    inner.notices.pop_front();
                }
            }
        }
    }

    /// The events of a thread after `after`, and its state.
    pub fn follow(&self, thread: &str, after: u64) -> Following {
        let mut inner = self.lock();
        if self.expire(&mut inner, SystemTime::now()) {
            self.bump(&mut inner);
        }
        // Numbers start again with every run: a cursor past them was given out before a restart.
        let restarted = after > inner.seq;
        let after = if restarted { 0 } else { after };
        let mut following = Following { next: after.max(inner.seq), reset: restarted, ..Following::default() };
        if let Some(live) = inner.threads.get(thread) {
            // Numbers are shared by every thread, so a gap is no loss; only a dropped event is.
            following.reset = restarted || (after > 0 && live.dropped > after);
            following.events = live.events.iter().filter(|(seq, _)| *seq > after).cloned().collect();
            following.running = live.turn.is_some();
            following.turn = live.turn.clone().filter(|turn| !turn.is_empty());
            following.pending = live.pending.values().cloned().collect();
        }
        following.queued = inner.commands.iter().filter(|command| command.thread.as_deref() == Some(thread) && matches!(command.state.as_str(), "waiting" | "queued" | "delivered")).cloned().collect();
        following
    }

    /// Whether anything happened after `seq`.
    pub fn seq(&self) -> u64 {
        self.lock().seq
    }

    /// A following device's command. `owner` is the thread's owner (or, for `start`, the device
    /// asked), as the store has it.
    pub fn command(&self, caller: &Caller, ask: Ask, owner: &str) -> Result<Command, Refused> {
        if !caller.can_control() {
            return Err(Refused::Forbidden("this device may watch threads, not answer them"));
        }
        let now = SystemTime::now();
        let mut inner = self.lock();
        if self.expire(&mut inner, now) {
            self.bump(&mut inner);
        }
        let online = Self::online_hosts(&inner, owner, now).next().is_some();
        let text = ask.text.as_ref().map(|text| text.trim().to_string()).filter(|text| !text.is_empty());
        if text.as_ref().is_some_and(|text| text.len() > TEXT_CAP) {
            return Err(Refused::BadRequest("the message is too long".to_string()));
        }
        let live = ask.thread.as_ref().and_then(|thread| inner.threads.get(thread));
        match ask.kind.as_str() {
            "send" => {
                if ask.thread.is_none() || text.is_none() {
                    return Err(Refused::BadRequest("a message names its thread and has words".to_string()));
                }
            }
            "approve" => {
                let Some(request) = ask.request.as_ref() else { return Err(Refused::BadRequest("name the approval".to_string())) };
                if !matches!(ask.decision.as_deref(), Some("accept" | "decline")) {
                    return Err(Refused::BadRequest("an approval is accept or decline".to_string()));
                }
                let waiting = live.and_then(|live| live.pending.get(&request.to_string()));
                let Some(waiting) = waiting else { return Err(Refused::BadRequest("that approval is not waiting any more".to_string())) };
                let method = waiting.get("method").and_then(Value::as_str).unwrap_or_default();
                if !ANSWERABLE.contains(&method) {
                    return Err(Refused::BadRequest("this question is answered on the device itself".to_string()));
                }
            }
            "stop" => {
                if !live.is_some_and(|live| live.turn.is_some()) {
                    return Err(Refused::BadRequest("no turn is running".to_string()));
                }
            }
            "start" => {
                let cwd = ask.cwd.as_deref().unwrap_or_default();
                let offered = Self::online_hosts(&inner, owner, now).any(|(_, host)| host.projects.iter().any(|project| project == cwd));
                if text.is_none() || !offered {
                    return Err(Refused::BadRequest("a new thread starts in one of the device's projects, with words".to_string()));
                }
            }
            _ => return Err(Refused::BadRequest("a command is send, approve, stop or start".to_string())),
        }
        let state = if online {
            "queued"
        } else if ask.kind == "send" {
            let thread = ask.thread.as_deref();
            let waiting = inner.commands.iter().filter(|command| command.state == "waiting" && command.thread.as_deref() == thread).count();
            if waiting >= QUEUE_PER_THREAD {
                return Err(Refused::QueueFull);
            }
            "waiting"
        } else {
            return Err(Refused::Offline("the device is offline: only a message waits for it".to_string()));
        };
        inner.next_command += 1;
        let command = Command {
            id: inner.next_command,
            kind: ask.kind,
            device: owner.to_string(),
            thread: ask.thread,
            text,
            model: ask.model.filter(|model| !model.is_empty() && model.len() <= 200),
            request: ask.request,
            decision: ask.decision,
            cwd: ask.cwd,
            by: caller.name.clone(),
            at: now_secs(),
            state: state.to_string(),
            error: None,
            delivered: None,
        };
        inner.commands.push(command.clone());
        if state == "waiting" {
            self.save_queue(&inner);
        }
        self.bump(&mut inner);
        Ok(command)
    }

    pub fn command_state(&self, id: u64) -> Option<Command> {
        let mut inner = self.lock();
        if self.expire(&mut inner, SystemTime::now()) {
            self.bump(&mut inner);
        }
        inner.commands.iter().find(|command| command.id == id).cloned()
    }

    /// Notices after `after`, and the newest number.
    pub fn notices(&self, after: u64) -> (Vec<Notice>, u64) {
        let inner = self.lock();
        let latest = inner.notices.back().map_or(0, |notice| notice.seq);
        // A cursor past this run's numbers is from before a restart: every notice since is new.
        let after = if after > inner.seq { 0 } else { after };
        (inner.notices.iter().filter(|notice| notice.seq > after).cloned().collect(), latest)
    }

    /// Forgets a deleted thread.
    pub fn forget_thread(&self, thread: &str) {
        let mut inner = self.lock();
        inner.threads.remove(thread);
        inner.commands.retain(|command| command.thread.as_deref() != Some(thread) || !matches!(command.state.as_str(), "waiting" | "queued"));
        self.save_queue(&inner);
        self.bump(&mut inner);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-sync-live-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn device(id: &str, scope: auth::Scope, agent: bool) -> Caller {
        Caller { device: id.to_string(), name: format!("{id}'s"), agent, scope }
    }

    const T: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";

    fn owners(thread: &str) -> Option<String> {
        (thread == T).then(|| "lap".to_string())
    }

    fn title(thread: &str) -> Option<String> {
        (thread == T).then(|| "Fix the login".to_string())
    }

    fn post(events: Vec<Value>, holding: &[&str]) -> HostPost {
        HostPost {
            host: "app-1".to_string(),
            kind: "desktop".to_string(),
            holding: holding.iter().map(|s| s.to_string()).collect(),
            projects: vec!["/home/user/app".to_string()],
            events: events.into_iter().map(|message| HostEvent { thread: T.to_string(), message }).collect(),
            ..HostPost::default()
        }
    }

    fn approval(id: u64) -> Value {
        json!({ "id": id, "method": "item/commandExecution/requestApproval", "params": { "threadId": T, "command": "rm -rf build" } })
    }

    #[test]
    fn a_following_device_sees_the_turn_and_the_approval_waiting() {
        let live = Live::new(scratch("follow"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "turn-1" } } }), approval(7)], &[T]), &owners, &title);
        let seen = live.follow(T, 0);
        assert_eq!((seen.events.len(), seen.running, seen.turn.as_deref()), (2, true, Some("turn-1")));
        assert_eq!(seen.pending, [approval(7)]);
        // Resolved, it waits no more; later events only after the cursor.
        live.host_post(&laptop, post(vec![json!({ "method": "serverRequest/resolved", "params": { "threadId": T, "requestId": 7 } })], &[T]), &owners, &title);
        let later = live.follow(T, seen.next);
        assert_eq!((later.events.len(), later.pending.len()), (1, 0));
        // Another device's thread is not this host's to publish.
        let other = device("desk", auth::Scope::Control, true);
        live.host_post(&other, post(vec![json!({ "method": "turn/completed", "params": { "threadId": T } })], &[]), &owners, &title);
        assert!(live.follow(T, 0).running);
    }

    #[test]
    fn a_cursor_from_before_a_restart_starts_over_and_misses_nothing_new() {
        let dir = scratch("restart");
        let laptop = device("lap", auth::Scope::Control, true);
        let live = Live::new(dir.clone());
        let turn = |n: u64| json!({ "method": "item/completed", "params": { "threadId": T, "n": n } });
        live.host_post(&laptop, post((0..5).map(turn).chain([approval(1)]).collect(), &[T]), &owners, &title);
        let cursor = live.follow(T, 0).next;
        let (_, seen) = live.notices(0);
        assert!(seen > 0);
        drop(live);

        // The numbers start again after a restart: a cursor past them is from the run before.
        let live = Live::new(dir);
        live.host_post(&laptop, post(vec![approval(2)], &[T]), &owners, &title);
        let following = live.follow(T, cursor);
        assert!(following.reset && following.events.len() == 1 && following.next == live.seq(), "{:?}", following.events);
        assert_eq!(live.notices(seen).0.len(), 1);
    }

    #[test]
    fn a_command_reaches_the_host_that_holds_the_thread_and_comes_back_answered() {
        let live = Live::new(scratch("command"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "turn-1" } } }), approval(7)], &[T]), &owners, &title);
        let phone = device("ph", auth::Scope::Control, false);
        let ask = Ask { kind: "approve".to_string(), thread: Some(T.to_string()), request: Some(json!(7)), decision: Some("accept".to_string()), ..Ask::default() };
        let sent = live.command(&phone, ask, "lap").unwrap();
        assert_eq!(sent.state, "queued");
        let delivered = live.host_post(&laptop, post(vec![], &[T]), &owners, &title);
        assert_eq!(delivered.len(), 1);
        assert_eq!((delivered[0].by.as_str(), delivered[0].decision.as_deref()), ("ph's", Some("accept")));
        assert!(live.host_post(&laptop, post(vec![], &[T]), &owners, &title).is_empty(), "delivered once");
        let mut answered = post(vec![], &[T]);
        answered.acks = vec![Ack { id: sent.id, ok: true, ..Ack::default() }];
        live.host_post(&laptop, answered, &owners, &title);
        assert_eq!(live.command_state(sent.id).unwrap().state, "done");
    }

    #[test]
    fn a_command_its_host_took_and_never_answered_fails_after_two_minutes_with_no_host_posting() {
        let live = Live::new(scratch("unanswered"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "turn-1" } } })], &[T]), &owners, &title);
        let phone = device("ph", auth::Scope::Control, false);
        let ask = Ask { kind: "send".to_string(), thread: Some(T.to_string()), text: Some("and the tests".to_string()), ..Ask::default() };
        let sent = live.command(&phone, ask, "lap").unwrap();
        assert_eq!(live.host_post(&laptop, post(vec![], &[T]), &owners, &title).len(), 1);
        assert_eq!(live.command_state(sent.id).unwrap().state, "delivered");
        // The laptop took it and was gone; no host posts again.
        for command in live.lock().commands.iter_mut() {
            if let Some((_, at)) = command.delivered.as_mut() {
                *at -= ANSWER_WITHIN + Duration::from_secs(1);
            }
        }
        let state = live.command_state(sent.id).unwrap();
        assert_eq!((state.state.as_str(), state.error.as_deref()), ("failed", Some("the device did not say how it went")));
        assert!(live.follow(T, 0).queued.is_empty(), "no longer shown as on its way");
    }

    #[test]
    fn an_approval_for_a_host_that_went_away_is_not_delivered_when_it_comes_back() {
        let live = Live::new(scratch("vanished"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "turn-1" } } }), approval(7)], &[T]), &owners, &title);
        let phone = device("ph", auth::Scope::Control, false);
        let ask = Ask { kind: "approve".to_string(), thread: Some(T.to_string()), request: Some(json!(7)), decision: Some("accept".to_string()), ..Ask::default() };
        let sent = live.command(&phone, ask, "lap").unwrap();
        assert_eq!(sent.state, "queued");
        // The host never came for it; hours later it is back.
        live.lock().commands.iter_mut().for_each(|command| command.at -= 3 * 60 * 60);
        assert!(live.host_post(&laptop, post(vec![], &[T]), &owners, &title).is_empty());
        assert_eq!(live.command_state(sent.id).unwrap().state, "expired");
    }

    #[test]
    fn a_thread_nobody_holds_goes_to_the_desktop_app_rather_than_an_agent() {
        let live = Live::new(scratch("prefer"));
        let laptop = device("lap", auth::Scope::Control, true);
        let mut desktop = post(vec![], &[]);
        desktop.host = "app".to_string();
        let mut agent = post(vec![], &[]);
        agent.host = "agent".to_string();
        agent.kind = "agent".to_string();
        live.host_post(&laptop, desktop.clone(), &owners, &title);
        live.host_post(&laptop, agent.clone(), &owners, &title);
        let phone = device("ph", auth::Scope::Control, false);
        let send = Ask { kind: "send".to_string(), thread: Some(T.to_string()), text: Some("go on".to_string()), ..Ask::default() };
        live.command(&phone, send, "lap").unwrap();
        assert!(live.host_post(&laptop, agent, &owners, &title).is_empty());
        assert_eq!(live.host_post(&laptop, desktop, &owners, &title).len(), 1);
    }

    #[test]
    fn what_may_be_asked_is_checked_and_a_watch_device_asks_nothing() {
        let live = Live::new(scratch("checks"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![json!({ "id": 9, "method": "item/tool/requestUserInput", "params": { "threadId": T } })], &[T]), &owners, &title);
        let phone = device("ph", auth::Scope::Control, false);
        let watcher = device("ph2", auth::Scope::Watch, false);
        let send = Ask { kind: "send".to_string(), thread: Some(T.to_string()), text: Some("go on".to_string()), ..Ask::default() };
        assert!(matches!(live.command(&watcher, send.clone(), "lap"), Err(Refused::Forbidden(_))));
        assert!(live.command(&phone, send, "lap").is_ok());
        let approve = |request: u64| Ask { kind: "approve".to_string(), thread: Some(T.to_string()), request: Some(json!(request)), decision: Some("accept".to_string()), ..Ask::default() };
        assert!(matches!(live.command(&phone, approve(3), "lap"), Err(Refused::BadRequest(_))), "not waiting");
        assert!(matches!(live.command(&phone, approve(9), "lap"), Err(Refused::BadRequest(_))), "not an approval");
        let stop = Ask { kind: "stop".to_string(), thread: Some(T.to_string()), ..Ask::default() };
        assert!(matches!(live.command(&phone, stop, "lap"), Err(Refused::BadRequest(_))), "nothing runs");
        let start = |cwd: &str| Ask { kind: "start".to_string(), cwd: Some(cwd.to_string()), text: Some("new".to_string()), ..Ask::default() };
        assert!(live.command(&phone, start("/home/user/app"), "lap").is_ok());
        assert!(matches!(live.command(&phone, start("/etc"), "lap"), Err(Refused::BadRequest(_))), "only the device's projects");
    }

    #[test]
    fn a_message_waits_for_an_offline_device_and_survives_a_restart_but_an_approval_never_waits() {
        let dir = scratch("offline");
        let phone = device("ph", auth::Scope::Control, false);
        let send = |text: &str| Ask { kind: "send".to_string(), thread: Some(T.to_string()), text: Some(text.to_string()), ..Ask::default() };
        {
            let live = Live::new(dir.clone());
            assert_eq!(live.command(&phone, send("first"), "lap").unwrap().state, "waiting");
            for n in 1..QUEUE_PER_THREAD {
                live.command(&phone, send(&format!("m{n}")), "lap").unwrap();
            }
            assert_eq!(live.command(&phone, send("one too many"), "lap"), Err(Refused::QueueFull));
            let stop = Ask { kind: "stop".to_string(), thread: Some(T.to_string()), ..Ask::default() };
            assert!(matches!(live.command(&phone, stop, "lap"), Err(Refused::BadRequest(_)) | Err(Refused::Offline(_))));
            assert_eq!(live.follow(T, 0).queued.len(), QUEUE_PER_THREAD);
        }
        let live = Live::new(dir);
        let laptop = device("lap", auth::Scope::Control, true);
        let delivered = live.host_post(&laptop, post(vec![], &[]), &owners, &title);
        assert_eq!(delivered.len(), QUEUE_PER_THREAD);
        assert_eq!(delivered[0].text.as_deref(), Some("first"), "in order");
        // An approval to a device that went offline is refused, not queued.
        let live = Live::new(scratch("offline-approval"));
        let mut inner_post = post(vec![approval(4)], &[T]);
        inner_post.host = "gone".to_string();
        live.host_post(&laptop, inner_post, &owners, &title);
        live.lock().hosts.values_mut().for_each(|host| host.seen = SystemTime::now() - ONLINE_FOR * 2);
        let approve = Ask { kind: "approve".to_string(), thread: Some(T.to_string()), request: Some(json!(4)), decision: Some("decline".to_string()), ..Ask::default() };
        assert!(matches!(live.command(&phone, approve, "lap"), Err(Refused::Offline(_))));
        assert!(!live.presence("lap").online && live.presence("lap").last_seen > 0);
    }

    #[test]
    fn a_late_follower_catches_up_from_the_buffer_and_a_very_late_one_is_told_to_reload() {
        let live = Live::new(scratch("buffer"));
        let laptop = device("lap", auth::Scope::Control, true);
        let deltas: Vec<Value> = (0..BUFFER + 50).map(|n| json!({ "method": "item/agentMessage/delta", "params": { "threadId": T, "delta": format!("{n} ") } })).collect();
        live.host_post(&laptop, post(deltas, &[T]), &owners, &title);
        let seen = live.follow(T, 0);
        assert_eq!(seen.events.len(), BUFFER);
        assert!(!seen.reset, "from the start, the buffer is all there is");
        assert!(live.follow(T, 10).reset);
        // A follower that saw every event of this thread is never told to reload, however far
        // other threads moved the numbers on.
        let next = live.follow(T, 0).next;
        live.host_post(&laptop, post(vec![json!({ "method": "turn/started", "params": { "threadId": T, "turn": { "id": "x" } } })], &[T]), &owners, &title);
        assert!(!live.follow(T, next).reset);
        let huge = json!({ "method": "item/completed", "params": { "threadId": T, "item": { "text": "x".repeat(EVENT_CAP) } } });
        live.host_post(&laptop, post(vec![huge], &[T]), &owners, &title);
        assert!(live.follow(T, 0).events.last().unwrap().1["params"]["truncated"].as_u64().unwrap() > EVENT_CAP as u64);
    }

    #[test]
    fn a_finished_turn_and_a_waiting_approval_make_notices_at_most_every_five_minutes() {
        let live = Live::new(scratch("notices"));
        let laptop = device("lap", auth::Scope::Control, true);
        live.host_post(&laptop, post(vec![approval(1), json!({ "method": "turn/completed", "params": { "threadId": T } })], &[T]), &owners, &title);
        let (notices, latest) = live.notices(0);
        assert_eq!(notices.len(), 1);
        assert_eq!((notices[0].kind.as_str(), notices[0].text.as_str()), ("approval", "Approval needed: Fix the login"));
        assert!(live.notices(latest).0.is_empty());
    }
}
