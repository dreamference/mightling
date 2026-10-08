//! `ling-signal serve`: the daemon (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §2, §8). It runs
//! signal-cli as a child, keeps one connection to `ling web`, and carries out what the conversation
//! (`bridge.rs`) decides. Nothing here decides anything: every rule is in `gate.rs` and `bridge.rs`.

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

/// Waits between attempts to reach `ling web`.
const BACKOFF_S: [u64; 5] = [1, 2, 5, 10, 30];
/// The conversation's disappearing-message timer, set once the owner pairs: one week.
const DISAPPEARING_S: u64 = 7 * 24 * 3600;
/// How often the air-gap level is asked for.
const AIRGAP_EVERY: Duration = Duration::from_secs(5);

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

struct Agent {
    connection: Connection,
    incoming: UnboundedReceiver<Incoming>,
    /// The `work/start` call, until its answer arrives; then the protocol handshake is sent.
    starting: Option<u64>,
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
}

/// Runs until signal-cli exits or the process is stopped. Returns an error to make systemd restart it.
pub async fn serve(state_dir: &Path, runtime_dir: Option<&Path>) -> Result<(), String> {
    let config = Config::load(state_dir)?;
    let cookie = state::load_cookie(state_dir).ok_or("the bridge is not paired with `ling web` (run `ling signal setup`)")?;
    let conversation = Conversation::load(state_dir);
    let launch = Launch::json_rpc(
        config.signal_cli.clone(),
        &state_dir.join("signal-cli"),
        &config.account,
        matches!(config.mode, Mode::Linked { .. }),
        config.signal_cli_env.clone(),
    );
    let (signal, mut messages) = SignalCli::spawn(&launch).map_err(|err| format!("could not start signal-cli ({}): {err}", config.signal_cli.display()))?;
    let mut gate = Gate::new(config.owner.clone());
    gate.binding = config.binding.clone();
    gate.restore_seen(&conversation.seen);
    gate.ignored = conversation.ignored;
    gate.identity_refusals = conversation.identity_refusals;
    let info = Info { node: config.node.clone(), bridge_version: env!("CARGO_PKG_VERSION").to_string(), signal_cli_version: config.signal_cli_version.clone() };
    let bridge = Bridge::new(conversation.current.clone(), conversation.recent.clone(), info);
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
    };
    daemon.poll_airgap().await;
    eprintln!("ling-signal {}: started ({} mode)", env!("CARGO_PKG_VERSION"), match daemon.config.mode { Mode::Dedicated => "dedicated", Mode::Linked { .. } => "linked" });
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let mut airgap = tokio::time::interval(AIRGAP_EVERY);
    let mut stop = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).map_err(|err| err.to_string())?;
    loop {
        tokio::select! {
            message = messages.recv() => {
                let Some(params) = message else {
                    daemon.write_status(false);
                    return Err("signal-cli exited".to_string());
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
                let now = now_ms();
                if daemon.agent.is_none() && now >= daemon.next_attempt_ms {
                    daemon.connect().await;
                }
                let actions = daemon.bridge.on_tick(now);
                daemon.carry_out(actions).await;
                if now / 1000 % 5 == 0 {
                    daemon.write_status(true);
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
        let Some(envelope) = envelope::parse(&params, self.config.own_uuid.as_deref()) else { return };
        let sender = envelope.source_uuid.clone().unwrap_or_default();
        let fingerprint = if sender.is_empty() { None } else { self.fingerprint(&sender).await };
        let now = now_ms();
        match self.gate.check(&envelope, &self.config.mode, fingerprint.as_deref(), now) {
            Verdict::Accept(message) => {
                self.last_owner_message_ms = Some(now);
                let actions = self.bridge.on_message(message, now);
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
    }

    fn owner(&self) -> Option<String> {
        self.config.owner.as_ref().map(|o| o.aci.clone())
    }

    async fn send_text(&mut self, message: &Styled) {
        self.send_with(message, &[]).await;
    }

    async fn send_with(&mut self, message: &Styled, attachments: &[String]) {
        let Some(owner) = self.owner() else { return };
        if let Err(err) = self.signal.call("send", rpc::send_params(&owner, message, attachments)).await {
            self.note_error(format!("could not send to Signal: {err}"));
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
                for part in &reply.parts {
                    self.send_text(part).await;
                }
                if let Some(markdown) = reply.attachment {
                    let path = self.state_dir.join("outbox").join("answer.md");
                    if state::write_private(&path, markdown.as_bytes()).is_ok() {
                        self.send_with(&Styled::plain("answer.md"), &[path.to_string_lossy().into_owned()]).await;
                        let _ = std::fs::remove_file(path);
                    }
                }
            }
            Action::Notice(text) => self.send_text(&Styled::plain(&text)).await,
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
            Action::StartThread => self.request("thread/start", json!({ "prompt": "ask" }), Pending::OpenThread).await,
            Action::ResumeThread(thread) => self.request("thread/resume", json!({ "threadId": thread }), Pending::OpenThread).await,
            Action::StartTurn { thread, text, attachments } => {
                let (images, files) = self.upload(&thread, &attachments).await;
                let input = agent::turn_input(&text, &images, &files);
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

    /// Hands the owner's attachments to `ling web`, which writes them into the thread's Ask folder;
    /// returns the saved images (sent as images when the model takes them) and the other files.
    async fn upload(&mut self, thread: &str, attachments: &[Attachment]) -> (Vec<String>, Vec<String>) {
        let (mut images, mut files) = (Vec::new(), Vec::new());
        for attachment in attachments {
            let local = self.state_dir.join("signal-cli").join("attachments").join(&attachment.id);
            let bytes = match std::fs::read(&local) {
                Ok(bytes) => bytes,
                Err(err) => {
                    self.note_error(format!("an attachment was not found ({}): {err}", attachment.id));
                    continue;
                }
            };
            let name = attachment.filename.clone().unwrap_or_else(|| attachment.id.clone());
            match agent::upload(self.config.port, &self.cookie, thread, &name, attachment.is_image(), &bytes).await {
                Ok(path) if attachment.is_image() && self.config.vision => images.push(path),
                Ok(path) => files.push(path),
                Err(err) => {
                    self.send_text(&Styled::plain(&format!("I couldn't save {name}: {err}"))).await;
                }
            }
            // The copy in signal-cli's folder is not kept: the thread's folder has it now.
            let _ = std::fs::remove_file(&local);
        }
        (images, files)
    }

    fn save_conversation(&mut self) {
        let conversation = Conversation {
            current: self.bridge.current.clone(),
            recent: self.bridge.recent.clone(),
            seen: self.gate.seen(),
            ignored: self.gate.ignored,
            identity_refusals: self.gate.identity_refusals,
        };
        if let Err(err) = conversation.save(&self.state_dir) {
            self.note_error(format!("could not save conversation.json: {err}"));
        }
    }

    fn write_status(&self, running: bool) {
        let Some(runtime) = &self.runtime_dir else { return };
        let status = json!({
            "running": running,
            "mode": match self.config.mode { Mode::Dedicated => "dedicated", Mode::Linked { .. } => "linked" },
            "ownerPaired": self.config.owner.is_some(),
            "webConnected": self.agent.as_ref().is_some_and(|a| a.starting.is_none()),
            "airgapped": self.bridge.airgapped(),
            "busy": self.bridge.is_busy(),
            "lastOwnerMessageMs": self.last_owner_message_ms,
            "strangersIgnored": self.gate.ignored,
            "identityRefusals": self.gate.identity_refusals,
            "lastError": self.last_error,
            "bridgeVersion": env!("CARGO_PKG_VERSION"),
            "signalCliVersion": self.config.signal_cli_version,
            "updatedMs": now_ms(),
        });
        let _ = state::write_status(runtime, &status);
    }
}
