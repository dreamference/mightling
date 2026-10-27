//! `ling-signal serve`: the daemon (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §2, §8). It runs
//! signal-cli as a child, keeps one connection to `ling web`, and carries out what the conversation
//! (`bridge.rs`) decides. Nothing here decides anything: every rule is in `gate.rs` and `bridge.rs`.

use std::collections::HashSet;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc::UnboundedReceiver;

use crate::agent;
use crate::agent::Connection;
use crate::agent::Incoming;
use crate::agent::Pending;
use crate::agent::Translated;
use crate::agent::Translator;
use crate::bridge::Action;
use crate::bridge::AgentEvent;
use crate::bridge::Bridge;
use crate::bridge::Info;
use crate::bridge::StatusLine;
use crate::bridge::Uploaded;
use crate::envelope;
use crate::envelope::Attachment;
use crate::format::Styled;
use crate::gate::Gate;
use crate::gate::Mode;
use crate::gate::Verdict;
use crate::rpc;
use crate::rpc::Launch;
use crate::rpc::SignalCli;
use crate::state;
use crate::state::Config;
use crate::state::Conversation;
use crate::timing;
use crate::timing::Timing;

/// Waits between attempts to reach `ling web`.
const BACKOFF_S: [u64; 5] = [1, 2, 5, 10, 30];
/// The conversation's disappearing-message timer, set once the owner pairs: one week.
const DISAPPEARING_S: u64 = 7 * 24 * 3600;
/// How often the air-gap level is asked for.
const AIRGAP_EVERY: Duration = Duration::from_secs(5);
/// Edits of one status line, the answer's included: Signal's clients keep at most ten versions of
/// a message (§19.8).
const STATUS_EDITS: u32 = 8;

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

struct Agent {
    connection: Connection,
    incoming: UnboundedReceiver<Incoming>,
    /// The `work/start` call, until its answer arrives; then the protocol handshake is sent.
    starting: Option<u64>,
}

/// The answer's message on the phone (§19.8): its timestamp, which an edit names.
struct OpenStatus {
    timestamp: u64,
    edits: u32,
    /// The question is done: the next message replaces it.
    closing: bool,
    /// An edit failed: no more are tried, and the answer comes whole, as a message of its own.
    broken: bool,
}

struct Daemon {
    state_dir: PathBuf,
    runtime_dir: Option<PathBuf>,
    config: Config,
    cookie: String,
    signal: SignalCli,
    gate: Gate,
    bridge: Bridge,
    translator: Translator,
    agent: Option<Agent>,
    attempts: usize,
    next_attempt_ms: u64,
    last_owner_message_ms: Option<u64>,
    last_error: Option<String>,
    /// Whether `ling web` has told the bridge the air-gap level yet. Until it has, messages are
    /// held, not acted on: at a user-level `on` the bridge must send nothing, not even a receipt,
    /// and it cannot know the level any other way (§11).
    airgap_known: bool,
    held: Vec<Value>,
    /// The owner's attachments waiting for their turn's upload; every other downloaded one is
    /// deleted on arrival (§19.1).
    held_attachments: HashSet<String>,
    /// The last Night Shift report sent (§19.7), and whether reports come here at all.
    night_seen: Option<String>,
    night_reports: bool,
    /// The last sync notice passed on (MIGHTLING_SYNC §18.3), and whether they come here at all;
    /// the first look after a start only notes the newest.
    sync_seen: Option<u64>,
    sync_notices: bool,
    status: Option<OpenStatus>,
    /// The question in hand's timing, for the journal (§19.8).
    timing: Option<Timing>,
}

/// Why the daemon stopped.
#[derive(Debug, PartialEq, Eq)]
pub enum Stop {
    /// Something went wrong that a restart may fix.
    Failed(String),
    /// Signal no longer accepts this device: unlinked from the phone, or the account registered
    /// again. A restart cannot fix it (exit 78, `RestartPreventExitStatus`).
    Unlinked,
}

impl From<String> for Stop {
    fn from(error: String) -> Stop {
        Stop::Failed(error)
    }
}

pub const UNLINKED_TEXT: &str = "Signal refused this device's credentials: it was probably unlinked from the phone (or the account was registered again). \
Its keys are still in the bridge's state: `ling signal remove` deletes them; `ling signal setup` links again.";

/// Runs until signal-cli exits or the process is stopped. An error makes systemd restart it,
/// except [`Stop::Unlinked`].
pub async fn serve(state_dir: &Path, runtime_dir: Option<&Path>) -> Result<(), Stop> {
    let config = Config::load(state_dir)?;
    let cookie = state::load_cookie(state_dir).ok_or_else(|| "the bridge is not paired with `ling web` (run `ling signal setup`)".to_string())?;
    let conversation = Conversation::load(state_dir);
    let launch = Launch::json_rpc(
        config.signal_cli.clone(),
        &state_dir.join("signal-cli"),
        &config.account,
        config.signal_cli_env.clone(),
    );
    let (signal, mut messages) = SignalCli::spawn(&launch).map_err(|err| Stop::Failed(format!("could not start signal-cli ({}): {err}", config.signal_cli.display())))?;
    let mut gate = Gate::new(config.owner.clone());
    gate.binding = config.binding.clone();
    gate.restore_seen(&conversation.seen);
    gate.ignored = conversation.ignored;
    gate.identity_refusals = conversation.identity_refusals;
    let info = Info { node: config.node.clone(), bridge_version: env!("CARGO_PKG_VERSION").to_string(), signal_cli_version: config.signal_cli_version.clone() };
    let mut bridge = Bridge::new(conversation.current.clone(), conversation.recent.clone(), info);
    bridge.last_activity_ms = conversation.last_activity_ms;
    // Note to Self shows no typing: a status line shows the work instead (§19.8).
    bridge.status_line = matches!(config.mode, Mode::Linked { .. });
    let mut daemon = Daemon {
        state_dir: state_dir.to_path_buf(),
        runtime_dir: runtime_dir.map(Path::to_path_buf),
        config,
        cookie,
        signal,
        gate,
        bridge,
        translator: Translator::default(),
        agent: None,
        attempts: 0,
        next_attempt_ms: 0,
        last_owner_message_ms: None,
        last_error: None,
        airgap_known: false,
        held: Vec::new(),
        held_attachments: HashSet::new(),
        night_seen: conversation.night_seen.clone(),
        night_reports: true,
        sync_seen: None,
        sync_notices: true,
        status: None,
        timing: None,
    };
    daemon.sweep_attachments(SystemTime::now());
    daemon.poll_airgap().await;
    daemon.poll_settings().await;
    eprintln!("ling-signal {}: started ({} mode)", env!("CARGO_PKG_VERSION"), match daemon.config.mode { Mode::Dedicated => "dedicated", Mode::Linked { .. } => "linked" });
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let mut airgap = tokio::time::interval(AIRGAP_EVERY);
    let mut stop = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).map_err(|err| Stop::Failed(err.to_string()))?;
    loop {
        tokio::select! {
            message = messages.recv() => {
                let Some(params) = message else {
                    if daemon.unlinked() {
                        return Err(Stop::Unlinked);
                    }
                    daemon.write_status(false);
                    return Err(Stop::Failed("signal-cli exited".to_string()));
                };
                if daemon.airgap_known {
                    daemon.on_signal(params).await;
                } else {
                    daemon.held.push(params);
                }
            }
            incoming = async {
                match daemon.agent.as_mut() {
                    Some(agent) => agent.incoming.recv().await,
                    None => std::future::pending().await,
                }
            } => {
                daemon.on_incoming(incoming.unwrap_or(Incoming::Closed)).await;
            }
            _ = tick.tick() => {
                if daemon.unlinked() {
                    daemon.signal.kill().await;
                    return Err(Stop::Unlinked);
                }
                let now = now_ms();
                if daemon.agent.is_none() && now >= daemon.next_attempt_ms {
                    daemon.connect().await;
                }
                let actions = daemon.bridge.on_tick(now);
                daemon.carry_out(actions).await;
                if now / 1000 % 5 == 0 {
                    daemon.write_status(true);
                }
                if now / 1000 % 60 == 0 {
                    daemon.sweep_attachments(SystemTime::now());
                    daemon.poll_settings().await;
                }
                if now / 1000 % 300 == 150 {
                    daemon.push_reports().await;
                }
                if now / 1000 % 30 == 20 {
                    daemon.push_sync_notices().await;
                }
            }
            _ = airgap.tick() => daemon.poll_airgap().await,
            _ = stop.recv() => {
                daemon.save_conversation();
                daemon.write_status(false);
                daemon.signal.kill().await;
                return Ok(());
            }
        }
    }
}

impl Daemon {
    fn linked(&self) -> bool {
        matches!(self.config.mode, Mode::Linked { .. })
    }

    /// Whether signal-cli has logged that Signal refuses this device; if so, says so in the status.
    fn unlinked(&mut self) -> bool {
        if !self.signal.deauthorized.load(std::sync::atomic::Ordering::Relaxed) {
            return false;
        }
        eprintln!("ling-signal: {UNLINKED_TEXT}");
        self.last_error = Some(UNLINKED_TEXT.to_string());
        self.save_conversation();
        self.write_status(false);
        true
    }

    /// Asks `ling web` for the chat settings of the user's configuration (§19.2); an older `ling
    /// web` without them leaves the defaults.
    async fn poll_settings(&mut self) {
        if let Ok(settings) = agent::chat_settings(self.config.port, &self.cookie).await {
            self.bridge.new_thread_after_ms = settings.new_thread_after_ms;
            self.bridge.limits = crate::limits::Limits::from_settings(&settings);
            self.night_reports = settings.night_reports;
            self.sync_notices = settings.sync_notices;
        }
    }

    /// Night Shift's morning reports since the last one sent, into the conversation (§19.7); the
    /// first look after a fresh start only notes the newest, so nothing old is repeated. Not at
    /// `/airgapped on`, and not when `[chat] night_report_to` leaves Signal out.
    async fn push_reports(&mut self) {
        if !self.night_reports || self.bridge.airgapped() || self.config.owner.is_none() {
            return;
        }
        let after = self.night_seen.clone().unwrap_or_default();
        let Ok(value) = agent::web_json(self.config.port, &self.cookie, &format!("/api/night/reports?after={after}"), None).await else { return };
        if self.night_seen.is_none() {
            self.night_seen = value.get("latest").and_then(Value::as_str).map(str::to_string).or_else(|| Some(String::new()));
            self.save_conversation();
            return;
        }
        for report in value.get("reports").and_then(Value::as_array).cloned().unwrap_or_default() {
            let (Some(name), Some(text)) = (report.get("name").and_then(Value::as_str), report.get("text").and_then(Value::as_str)) else { continue };
            let reply = crate::format::reply(&format!("**Night Shift's report** ({})\n\n{text}", name.trim_end_matches(".md")));
            self.carry_out(vec![Action::Reply(reply)]).await;
            self.night_seen = Some(name.to_string());
            self.save_conversation();
        }
    }

    /// Sync's notices since the last one sent (a turn on another device finished, an approval
    /// waits there), into the conversation. Nothing while the hub is off (its route answers 404),
    /// not at `/airgapped on`, and not when `[chat] sync_notices_to` leaves Signal out.
    async fn push_sync_notices(&mut self) {
        if !self.sync_notices || self.bridge.airgapped() || self.config.owner.is_none() {
            return;
        }
        let after = self.sync_seen.unwrap_or(0);
        let Ok(value) = agent::web_json(self.config.port, &self.cookie, &format!("/api/sync/notices?after={after}"), None).await else { return };
        if self.sync_seen.is_none() {
            self.sync_seen = Some(value.get("latest").and_then(Value::as_u64).unwrap_or(0));
            return;
        }
        for notice in value.get("notices").and_then(Value::as_array).cloned().unwrap_or_default() {
            let (Some(seq), Some(text)) = (notice.get("seq").and_then(Value::as_u64), notice.get("text").and_then(Value::as_str)) else { continue };
            let reply = crate::format::reply(&format!("{text} (open Other devices in Mightling)"));
            self.carry_out(vec![Action::Reply(reply)]).await;
            self.sync_seen = Some(seq);
        }
    }

    /// Asks `ling web` for the air-gap level; the first answer also releases held messages.
    async fn poll_airgap(&mut self) {
        let Ok(on) = agent::airgap_level(self.config.port, &self.cookie).await else { return };
        let actions = self.bridge.on_airgap(on, now_ms());
        self.carry_out(actions).await;
        if !self.airgap_known {
            self.airgap_known = true;
            for params in std::mem::take(&mut self.held) {
                self.on_signal(params).await;
            }
        }
    }

    async fn connect(&mut self) {
        match Connection::open(self.config.port, &self.cookie).await {
            Ok((mut connection, incoming)) => {
                let starting = connection.call(json!({ "type": "work/start" })).await.ok();
                self.translator = Translator::default();
                self.agent = Some(Agent { connection, incoming, starting });
                self.attempts = 0;
            }
            Err(err) => {
                self.note_error(err);
                let wait = BACKOFF_S[self.attempts.min(BACKOFF_S.len() - 1)];
                self.attempts += 1;
                self.next_attempt_ms = now_ms() + wait * 1000;
            }
        }
    }

    fn note_error(&mut self, error: String) {
        if self.last_error.as_deref() != Some(error.as_str()) {
            eprintln!("ling-signal: {error}");
        }
        self.last_error = Some(error);
    }

    async fn disconnect(&mut self) {
        if self.agent.take().is_some() {
            self.next_attempt_ms = now_ms() + 1000;
            let actions = self.bridge.on_agent(AgentEvent::Disconnected, now_ms());
            Box::pin(self.carry_out(actions)).await;
        }
    }

    async fn on_incoming(&mut self, incoming: Incoming) {
        match incoming {
            Incoming::Closed => self.disconnect().await,
            Incoming::Answer { call, error } => {
                let starting = self.agent.as_ref().and_then(|a| a.starting);
                if starting == Some(call) {
                    if let Some(error) = error {
                        self.note_error(format!("ling web could not reach the agent's server: {error}"));
                        self.disconnect().await;
                        return;
                    }
                    let Some(agent) = self.agent.as_mut() else { return };
                    agent.starting = None;
                    let mut sent = true;
                    for message in agent::handshake().into_iter().skip(1) {
                        sent &= agent.connection.call(message).await.is_ok();
                    }
                    if !sent {
                        self.disconnect().await;
                        return;
                    }
                    let actions = self.bridge.on_agent(AgentEvent::Connected, now_ms());
                    self.carry_out(actions).await;
                } else if let Some(error) = error {
                    // A message the policy refused: say so in the journal; the conversation goes on.
                    self.note_error(format!("ling web refused a message: {error}"));
                }
            }
            Incoming::Message(message) => {
                for translated in self.translator.read(&message) {
                    match translated {
                        Translated::Event(event) => {
                            self.time_event(&event);
                            let actions = self.bridge.on_agent(event, now_ms());
                            self.carry_out(actions).await;
                        }
                        Translated::Respond(response) => {
                            if let Some(agent) = self.agent.as_mut() {
                                let _ = agent.connection.send(response).await;
                            }
                        }
                    }
                }
            }
        }
    }

    /// The owner's current identity fingerprint, if signal-cli trusts it.
    async fn fingerprint(&self, aci: &str) -> Option<String> {
        let identities = self.signal.call("listIdentities", json!({})).await.ok()?;
        rpc::trusted_fingerprint(&identities, aci)
    }

    async fn on_signal(&mut self, params: Value) {
        let own = envelope::OwnAccount { uuid: self.config.own_uuid.as_deref(), number: Some(self.config.account.as_str()) };
        // Every attachment signal-cli downloaded for this message; only the owner's are kept.
        let downloaded = envelope::attachment_ids(&params);
        let Some(envelope) = envelope::parse(&params, own) else {
            self.forget(&downloaded);
            return;
        };
        let sender = envelope.source_uuid.clone().unwrap_or_default();
        // Linked mode needs no identity key (gate.rs, `check_linked`), and asking for one for every
        // message of every conversation on the owner's account would be work done on their mail.
        let fingerprint = if sender.is_empty() || self.linked() { None } else { self.fingerprint(&sender).await };
        let now = now_ms();
        match self.gate.check(&envelope, &self.config.mode, fingerprint.as_deref(), now) {
            Verdict::Accept(message) => {
                self.last_owner_message_ms = Some(now);
                let (server_received, server_delivered) = envelope::server_times(&params);
                eprintln!("{}", timing::received_line(message.timestamp, server_received, server_delivered, now));
                let was_busy = self.bridge.is_busy();
                self.held_attachments.extend(message.attachments.iter().map(|a| a.id.clone()));
                let id = message.timestamp;
                let actions = self.bridge.on_message(message, now);
                // Timed from here when it starts work now; one that joins or waits is timed with
                // the question it joins, or from when it is taken from the queue.
                if !was_busy && self.bridge.is_busy() {
                    self.timing = Some(Timing::new(id, now));
                }
                self.carry_out(actions).await;
                self.save_conversation();
            }
            Verdict::Bound(owner) => {
                eprintln!("ling-signal: the owner is paired");
                self.config.owner = Some(owner);
                self.config.binding = None;
                if let Err(err) = self.config.save(&self.state_dir) {
                    self.note_error(format!("could not save bridge.json: {err}"));
                }
                // Messages here disappear after a week, so code and mail shown on the phone do not
                // stay there for good (§4.1 step 5).
                if let Some(aci) = self.owner()
                    && let Err(err) = self.signal.call("updateContact", json!({ "recipient": aci, "expiration": DISAPPEARING_S })).await
                {
                    self.note_error(format!("could not set disappearing messages: {err}"));
                }
                self.send_text(&Styled::plain("Paired. Ask me anything; /help lists what else I understand.")).await;
                self.save_conversation();
            }
            Verdict::Late { hours } => {
                if !self.bridge.airgapped() {
                    self.send_text(&Styled::plain(&format!("Your message arrived {hours} hours late, so I didn't act on it; send it again if you still want it."))).await;
                }
            }
            Verdict::IdentityChanged => {
                if self.gate.identity_refusals == 1 || self.gate.identity_refusals % 50 == 0 {
                    eprintln!("ling-signal: the owner's safety number changed; messages are refused until `ling signal trust` on the node");
                }
                self.last_error = Some("the owner's safety number changed; run `ling signal trust` on the node".to_string());
                self.save_conversation();
            }
            Verdict::Ignored => {}
        }
        self.forget(&downloaded);
    }

    fn owner(&self) -> Option<String> {
        self.config.owner.as_ref().map(|o| o.aci.clone())
    }

    async fn send_text(&mut self, message: &Styled) {
        self.send_with(message, &[]).await;
    }

    /// Sends one message, and returns its timestamp once signal-cli has sent it. The first message
    /// after a question's status line closed is sent as an edit of the line, so the line becomes the
    /// answer; should the edit fail, it is sent as a message of its own (§19.8).
    /// `send`'s parameters. Linked: into the owner's Note to Self, marked as the bridge's (§4.2).
    /// Dedicated: to the owner.
    fn send_params(&self, message: &Styled, attachments: &[String]) -> Option<Value> {
        let owner = self.owner()?;
        Some(if self.linked() {
            rpc::note_to_self_params(&crate::format::prefixed(message, crate::format::LINKED_MARKER), attachments)
        } else {
            rpc::send_params(&owner, message, attachments)
        })
    }

    async fn send_with(&mut self, message: &Styled, attachments: &[String]) -> Option<u64> {
        let params = self.send_params(message, attachments)?;
        let replaces = self.status.take_if(|line| line.closing);
        if let Some(line) = replaces.filter(|line| attachments.is_empty() && !line.broken && line.edits <= STATUS_EDITS) {
            match self.signal.call("send", rpc::as_edit(params.clone(), line.timestamp)).await {
                Ok(result) => return rpc::sent_timestamp(&result).or(Some(line.timestamp)),
                Err(err) => self.note_error(format!("could not replace the status line, sending the message instead: {err}")),
            }
        }
        match self.signal.call("send", params).await {
            Ok(result) => Some(rpc::sent_timestamp(&result).unwrap_or_else(now_ms)),
            Err(err) => {
                self.note_error(format!("could not send to Signal: {err}"));
                None
            }
        }
    }

    /// The answer's message (§19.8): opened as a message (the status line, or the answer's first
    /// words), changed by edits while there are edits to spare and they work.
    async fn status_line(&mut self, line: StatusLine) {
        let message = match &line {
            StatusLine::Open(text) | StatusLine::Update(text) => Styled::plain(text),
            StatusLine::Stream(markdown) => {
                // The first part of the answer so far, marked as unfinished; the rest comes with
                // the whole answer.
                let mut part = crate::format::reply(markdown).parts.into_iter().next().unwrap_or_default();
                part.text.push_str(" …");
                part
            }
            StatusLine::Close => {
                if let Some(line) = self.status.as_mut() {
                    line.closing = true;
                }
                return;
            }
        };
        let open = matches!(line, StatusLine::Open(_)) || (matches!(line, StatusLine::Stream(_)) && self.status.as_ref().is_none_or(|line| line.closing));
        if open {
            self.status = None;
            let sent = self.send_with(&message, &[]).await;
            if let Some(timestamp) = sent {
                self.status = Some(OpenStatus { timestamp, edits: 0, closing: false, broken: false });
                if let Some(timing) = self.timing.as_mut().filter(|t| t.status_ms.is_none()) {
                    timing.status_ms = Some(now_ms());
                }
            }
            return;
        }
        let Some(params) = self.send_params(&message, &[]) else { return };
        let Some(line) = self.status.as_mut().filter(|line| !line.closing && !line.broken && line.edits < STATUS_EDITS) else { return };
        line.edits += 1;
        let timestamp = line.timestamp;
        if let Err(err) = self.signal.call("send", rpc::as_edit(params, timestamp)).await {
            self.note_error(format!("could not edit the answer's message; the answer will come whole: {err}"));
            if let Some(line) = self.status.as_mut() {
                line.broken = true;
            }
        }
    }

    /// Marks the agent's side of the question in hand's timing.
    fn time_event(&mut self, event: &AgentEvent) {
        let Some(timing) = self.timing.as_mut() else { return };
        let now = now_ms();
        match event {
            AgentEvent::Delta { .. } | AgentEvent::ToolStarted { .. } if timing.first_output_ms.is_none() => timing.first_output_ms = Some(now),
            AgentEvent::TurnCompleted { .. } => timing.answered_ms = Some(now),
            _ => {}
        }
    }

    /// After the question in hand's answer (or why there is none) was sent: its timing line.
    fn timed_send(&mut self, acked: Option<u64>) {
        if self.timing.as_ref().is_some_and(|t| t.answered_ms.is_some())
            && acked.is_some()
            && let Some(timing) = self.timing.take()
        {
            eprintln!("{}", timing::answered_line(&timing, now_ms()));
        }
    }

    async fn carry_out(&mut self, actions: Vec<Action>) {
        for action in actions {
            self.carry_out_one(action).await;
        }
    }

    async fn carry_out_one(&mut self, action: Action) {
        let owner = self.owner();
        match action {
            Action::Reply(reply) => {
                let mut acked = None;
                for part in &reply.parts {
                    let sent = self.send_with(part, &[]).await;
                    acked = acked.or(sent);
                }
                self.timed_send(acked);
                if let Some(markdown) = reply.attachment {
                    let path = self.state_dir.join("outbox").join("answer.md");
                    if state::write_private(&path, markdown.as_bytes()).is_ok() {
                        self.send_with(&Styled::plain("answer.md"), &[path.to_string_lossy().into_owned()]).await;
                        let _ = std::fs::remove_file(path);
                    }
                }
            }
            Action::Notice(text) => {
                let acked = self.send_with(&Styled::plain(&text), &[]).await;
                self.timed_send(acked);
            }
            Action::Status(line) => self.status_line(line).await,
            Action::Cavemode { thread, text } => {
                let said = match agent::cavemode(self.config.port, &self.cookie, thread.as_deref(), &text).await {
                    Ok(said) | Err(said) => said,
                };
                self.send_text(&Styled::plain(&said)).await;
            }
            // Note to Self shows neither typing nor receipts, and a receipt there would mark the
            // owner's note read on every device; linked mode sends neither.
            Action::Typing(_) | Action::Receipt(_) if self.linked() => {}
            Action::Typing(on) => {
                if let Some(owner) = owner {
                    let _ = self.signal.call("sendTyping", rpc::typing_params(&owner, on)).await;
                }
            }
            Action::Receipt(timestamp) => {
                if let Some(owner) = owner {
                    let _ = self.signal.call("sendReceipt", rpc::receipt_params(&owner, timestamp)).await;
                }
            }
            Action::StartThread => self.request("thread/start", json!({ "prompt": "chat" }), Pending::OpenThread).await,
            Action::StartAskThread => self.request("thread/start", json!({ "prompt": "ask" }), Pending::OpenThread).await,
            Action::NightRepos { task } => {
                let repos = match agent::web_json(self.config.port, &self.cookie, "/api/night/repos", None).await {
                    Ok(value) => value
                        .get("repos")
                        .and_then(Value::as_array)
                        .map(|repos| repos.iter().filter_map(|r| Some((r.get("path")?.as_str()?.to_string(), r.get("name")?.as_str()?.to_string()))).take(9).collect())
                        .unwrap_or_default(),
                    Err(err) => {
                        self.send_text(&Styled::plain(&format!("Night Shift's repositories could not be read: {err}"))).await;
                        return;
                    }
                };
                let actions = self.bridge.on_agent(AgentEvent::NightRepos { task, repos }, now_ms());
                Box::pin(self.carry_out(actions)).await;
            }
            Action::NightAdd { repo, task } => {
                let text = match agent::web_json(self.config.port, &self.cookie, "/api/night/add", Some(&json!({ "repo": repo, "task": task }))).await {
                    Ok(value) => {
                        let lines: Vec<String> = value.get("lines").and_then(Value::as_array).map(|l| l.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
                        if value.get("queued") == Some(&Value::Bool(true)) { lines.join("\n") } else { format!("Night Shift did not queue it: {}", lines.join(" ")) }
                    }
                    Err(err) => format!("Night Shift did not queue it: {err}"),
                };
                self.send_text(&Styled::plain(&text)).await;
            }
            Action::NightStatus => {
                let text = match agent::web_json(self.config.port, &self.cookie, "/api/night/status", None).await {
                    Ok(value) => crate::bridge::night_status_text(&value),
                    Err(err) => format!("Night Shift's queue could not be read: {err}"),
                };
                self.send_text(&Styled::plain(&text)).await;
            }
            Action::FindFiles { thread, since_ms, answer } => {
                if let Ok(files) = agent::made_files(self.config.port, &self.cookie, &thread, since_ms.saturating_sub(1_000)).await
                    && !files.is_empty()
                {
                    let actions = self.bridge.on_agent(AgentEvent::Made { thread, files, answer }, now_ms());
                    Box::pin(self.carry_out(actions)).await;
                }
            }
            Action::SendFile { thread, name } => match agent::made_file(self.config.port, &self.cookie, &thread, &name).await {
                Ok(bytes) => {
                    let base = name.rsplit('/').next().unwrap_or(&name).to_string();
                    let path = self.state_dir.join("outbox").join(&base);
                    if state::write_private(&path, &bytes).is_ok() {
                        self.send_with(&Styled::plain(&base), &[path.to_string_lossy().into_owned()]).await;
                    }
                    let _ = std::fs::remove_file(path);
                }
                Err(err) => self.send_text(&Styled::plain(&format!("I couldn't send {name}: {err}"))).await,
            },
            Action::AddToTurn { thread, turn, text } => {
                let input = agent::turn_input(&text, &[]);
                let pending = Pending::Steer { thread: thread.clone(), text };
                self.request("turn/steer", json!({ "threadId": thread, "input": input, "expectedTurnId": turn }), pending).await;
            }
            Action::Steer { thread, turn, text } => {
                let input = agent::turn_input(&text, &[]);
                self.request("turn/steer", json!({ "threadId": thread, "input": input, "expectedTurnId": turn }), Pending::Other).await;
            }
            Action::ResumeThread(thread) => self.request("thread/resume", json!({ "threadId": thread }), Pending::OpenThread).await,
            Action::Upload { thread, attachments } => {
                let uploads = self.upload(&thread, &attachments).await;
                let actions = self.bridge.on_agent(AgentEvent::Uploaded { thread, uploads }, now_ms());
                Box::pin(self.carry_out(actions)).await;
            }
            Action::StartTurn { thread, text, images } => {
                let timing = self.timing.get_or_insert_with(|| Timing::new(0, now_ms()));
                if timing.handed_ms.is_none() {
                    timing.handed_ms = Some(now_ms());
                }
                let input = agent::turn_input(&text, &images);
                self.request("turn/start", json!({ "threadId": thread, "input": input }), Pending::StartTurn { thread: thread.clone() }).await;
            }
            Action::Interrupt { thread, turn } => self.request("turn/interrupt", json!({ "threadId": thread, "turnId": turn }), Pending::Other).await,
            Action::AnswerApproval { id, result } => {
                if let Some(agent) = self.agent.as_mut()
                    && agent.connection.send(json!({ "id": id, "result": result })).await.is_err()
                {
                    self.disconnect().await;
                }
            }
            Action::Save => self.save_conversation(),
        }
    }

    async fn request(&mut self, method: &str, params: Value, pending: Pending) {
        let Some(agent) = self.agent.as_mut() else {
            // Lost between the decision and now: the conversation hears it as a disconnection.
            self.disconnect_now().await;
            return;
        };
        match agent.connection.request(method, params).await {
            Ok(id) => self.translator.expect(&id, pending),
            Err(_) => self.disconnect().await,
        }
    }

    async fn disconnect_now(&mut self) {
        let actions = self.bridge.on_agent(AgentEvent::Disconnected, now_ms());
        Box::pin(self.carry_out(actions)).await;
    }

    /// Hands the owner's attachments to `ling web`, which writes them into the thread's Ask folder
    /// and reads an image's text; reports each one, received or not, for the conversation to
    /// decide what the model gets (`bridge::compose`).
    async fn upload(&mut self, thread: &str, attachments: &[Attachment]) -> Vec<Uploaded> {
        let mut uploads = Vec::new();
        for attachment in attachments {
            let local = self.attachments_dir().join(&attachment.id);
            let name = attachment.filename.clone().unwrap_or_else(|| attachment.id.clone());
            let mut uploaded = Uploaded { name: name.clone(), image: attachment.is_image(), path: None, text: None, vision: false, heard: None };
            // signal-cli has downloaded it into its folder (it is started without
            // --ignore-attachments, §19.1); `getAttachment` reads that same folder, base64.
            let fetched = match std::fs::read(&local) {
                Ok(bytes) => Ok(bytes),
                Err(_) => match self.signal.call("getAttachment", rpc::get_attachment_params(&attachment.id, &self.config.account)).await {
                    Ok(value) => value.get("data").and_then(Value::as_str).and_then(rpc::base64_decode).ok_or_else(|| "signal-cli sent no data".to_string()),
                    Err(err) => Err(err),
                },
            };
            self.held_attachments.remove(&attachment.id);
            let _ = std::fs::remove_file(&local);
            let bytes = match fetched {
                Ok(bytes) => bytes,
                Err(err) => {
                    self.note_error(format!("an attachment could not be read ({}): {err}", attachment.id));
                    uploads.push(uploaded);
                    continue;
                }
            };
            // A voice note is transcribed on this machine through `ling web`, not uploaded (§19.5).
            if attachment.content_type.starts_with("audio/") {
                uploaded.heard = Some(agent::transcribe(self.config.port, &self.cookie, &attachment.content_type, &bytes).await);
                uploads.push(uploaded);
                continue;
            }
            match agent::upload(self.config.port, &self.cookie, thread, &name, attachment.is_image(), &bytes).await {
                Ok(saved) => {
                    uploaded.vision = saved.vision || self.config.vision;
                    uploaded.text = saved.text;
                    uploaded.path = Some(saved.path);
                }
                Err(err) => self.note_error(format!("could not save {name} in the thread's folder: {err}")),
            }
            uploads.push(uploaded);
        }
        uploads
    }

    fn attachments_dir(&self) -> PathBuf {
        self.state_dir.join("signal-cli").join("attachments")
    }

    /// signal-cli downloads the attachments of every message the account receives, and in linked
    /// mode that is all of the owner's conversations. Only the owner's Note to Self attachments
    /// are kept, until they are uploaded; the rest are deleted as they arrive (`forget`), and this
    /// sweep removes whatever is left: anything not held after two minutes, anything held after
    /// an hour (§19.1).
    fn sweep_attachments(&mut self, now: SystemTime) {
        let Ok(entries) = std::fs::read_dir(self.attachments_dir()) else { return };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let age = entry.metadata().ok().and_then(|m| m.modified().ok()).and_then(|t| now.duration_since(t).ok()).unwrap_or_default();
            let held = self.held_attachments.contains(&name);
            if (!held && age >= Duration::from_secs(120)) || age >= Duration::from_secs(3600) {
                let _ = std::fs::remove_file(entry.path());
                self.held_attachments.remove(&name);
            }
        }
    }

    /// Deletes the downloaded attachments of a message the bridge does not act on.
    fn forget(&self, ids: &[String]) {
        for id in ids.iter().filter(|id| !self.held_attachments.contains(*id)) {
            if !id.contains('/') && !id.starts_with('.') {
                let _ = std::fs::remove_file(self.attachments_dir().join(id));
            }
        }
    }

    fn save_conversation(&mut self) {
        let conversation = Conversation {
            current: self.bridge.current.clone(),
            recent: self.bridge.recent.clone(),
            seen: self.gate.seen(),
            ignored: self.gate.ignored,
            identity_refusals: self.gate.identity_refusals,
            last_activity_ms: self.bridge.last_activity_ms,
            night_seen: self.night_seen.clone(),
        };
        if let Err(err) = conversation.save(&self.state_dir) {
            self.note_error(format!("could not save conversation.json: {err}"));
        }
    }

    fn write_status(&self, running: bool) {
        let Some(runtime) = &self.runtime_dir else { return };
        let status = json!({
            "running": running,
            // Which bridge this is and whose agent it answers with (§18.3), for `ling signal status`.
            "instance": self.config.instance.as_deref().unwrap_or(crate::unit::DEFAULT_INSTANCE),
            "user": self.config.user,
            "mode": match self.config.mode { Mode::Dedicated => "dedicated", Mode::Linked { .. } => "linked" },
            "ownerPaired": self.config.owner.is_some(),
            "webConnected": self.agent.as_ref().is_some_and(|a| a.starting.is_none()),
            "airgapped": self.bridge.airgapped(),
            "busy": self.bridge.is_busy(),
            "lastOwnerMessageMs": self.last_owner_message_ms,
            // Linked: not counted at all (other conversations are the owner's, not the bridge's).
            "strangersIgnored": if self.linked() { Value::Null } else { json!(self.gate.ignored) },
            "identityRefusals": self.gate.identity_refusals,
            "lastError": self.last_error,
            "bridgeVersion": env!("CARGO_PKG_VERSION"),
            "signalCliVersion": self.config.signal_cli_version,
            "updatedMs": now_ms(),
        });
        let _ = state::write_status(runtime, &status);
    }
}
