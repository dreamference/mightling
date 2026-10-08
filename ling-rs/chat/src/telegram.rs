//! The Telegram adapter (MIGHTLING_CHAT §4): the less private messenger, for users who typed `yes`
//! to everything passing through Telegram's servers unencrypted.
//!
//! It long-polls `getUpdates` (no webhook, no open port), accepts private chats with paired users
//! only (pairing is `/pair <code>` with a code `ling chat telegram pair` wrote), leaves groups, and
//! renders the hub's output: live answers through `sendMessageDraft` (a 30-second preview that is
//! refreshed, with Telegram's stop button), the final answer as HTML split under 4,096 characters
//! with a plain-text fallback, approvals as inline buttons. The token is in every request URL, so
//! no error is ever shown with its URL (`without_url`).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use serde_json::Value;
use serde_json::json;
use tokio::sync::Mutex;
use tokio::sync::mpsc;

use crate::hub::HELP;
use crate::hub::Inbound;
use crate::hub::Outbound;
use crate::render;
use crate::store;
use crate::store::TelegramConfig;

pub const API_ROOT: &str = "https://api.telegram.org";
/// Telegram's limit is 4,096 characters after entities are parsed; this leaves room.
pub const MESSAGE_LIMIT: usize = 4000;
const DRAFT_LIMIT: usize = 4096;
const POLL_TIMEOUT_S: u64 = 50;
/// Wrong pairing codes tolerated before every pending code is withdrawn.
const PAIRING_ATTEMPTS: u32 = 10;
const UNPAIRED_REPLY_EVERY: Duration = Duration::from_secs(24 * 60 * 60);
pub const UNPAIRED_REPLY: &str = "This is a private Mightling. Ask its owner to pair you.";

/// A Bot API error, without the request's URL (it carries the token).
#[derive(Clone, Debug, PartialEq)]
pub struct ApiError {
    pub description: String,
    pub retry_after: Option<u64>,
    /// The method does not exist on this server (an older Bot API).
    pub missing_method: bool,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.description)
    }
}

#[derive(Clone)]
pub struct TelegramApi {
    base: String,
    http: reqwest::Client,
}

impl TelegramApi {
    /// `root` is `https://api.telegram.org`, or a test's stand-in.
    pub fn new(root: &str, token: &str) -> TelegramApi {
        TelegramApi { base: format!("{}/bot{token}", root.trim_end_matches('/')), http: reqwest::Client::new() }
    }

    /// Calls a method. A `retry_after` answer is waited out and retried, up to three times.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, ApiError> {
        let timeout = Duration::from_secs(params.get("timeout").and_then(Value::as_u64).unwrap_or(0) + 30);
        for _ in 0..3 {
            match self.once(method, &params, timeout).await {
                Err(ApiError { retry_after: Some(seconds), .. }) if seconds <= 120 => {
                    tokio::time::sleep(Duration::from_secs(seconds)).await;
                }
                other => return other,
            }
        }
        self.once(method, &params, timeout).await
    }

    async fn once(&self, method: &str, params: &Value, timeout: Duration) -> Result<Value, ApiError> {
        let failed = |err: reqwest::Error| ApiError { description: err.without_url().to_string(), retry_after: None, missing_method: false };
        let response = self.http.post(format!("{}/{method}", self.base)).json(params).timeout(timeout).send().await.map_err(failed)?;
        let status = response.status();
        let body: Value = response.json().await.map_err(failed)?;
        if body.get("ok") == Some(&Value::Bool(true)) {
            return Ok(body.get("result").cloned().unwrap_or(Value::Null));
        }
        Err(ApiError {
            description: body.get("description").and_then(Value::as_str).unwrap_or("Telegram refused the request").to_string(),
            retry_after: body.pointer("/parameters/retry_after").and_then(Value::as_u64),
            missing_method: status.as_u16() == 404,
        })
    }
}

fn chat_key(chat_id: i64) -> String {
    format!("telegram:{chat_id}")
}

fn chat_id(key: &str) -> Option<i64> {
    key.strip_prefix("telegram:")?.parse().ok()
}

/// The commands Telegram offers on `/`.
pub fn commands() -> Value {
    json!({ "commands": [
        { "command": "new", "description": "Start a new thread" },
        { "command": "stop", "description": "Stop the running answer" },
        { "command": "threads", "description": "Your recent threads" },
        { "command": "use", "description": "Continue a thread: /use <n>" },
        { "command": "status", "description": "What Mightling is doing" },
        { "command": "help", "description": "What this bot does" },
    ]})
}

/// Reads updates and hands paired users' messages to the hub.
pub struct Poller {
    pub api: TelegramApi,
    pub dir: PathBuf,
    pub hub: mpsc::UnboundedSender<Inbound>,
    wrong_codes: u32,
    unpaired_replied: HashMap<i64, Instant>,
}

impl Poller {
    pub fn new(api: TelegramApi, dir: PathBuf, hub: mpsc::UnboundedSender<Inbound>) -> Poller {
        Poller { api, dir, hub, wrong_codes: 0, unpaired_replied: HashMap::new() }
    }

    async fn reply(&self, chat_id: i64, text: &str) {
        let _ = self.api.call("sendMessage", json!({ "chat_id": chat_id, "text": text })).await;
    }

    /// Polls until Telegram is turned off (`telegram.json` gone).
    pub async fn run(mut self) {
        let mut offset: i64 = 0;
        let mut backoff = Duration::from_secs(1);
        loop {
            if TelegramConfig::load(&self.dir).is_none() {
                return;
            }
            let params = json!({
                "offset": offset,
                "timeout": POLL_TIMEOUT_S,
                "allowed_updates": ["message", "callback_query", "stopped_message_generation"],
            });
            match self.api.call("getUpdates", params).await {
                Ok(updates) => {
                    backoff = Duration::from_secs(1);
                    for update in updates.as_array().cloned().unwrap_or_default() {
                        if let Some(id) = update.get("update_id").and_then(Value::as_i64) {
                            offset = offset.max(id + 1);
                        }
                        self.handle(&update).await;
                    }
                }
                Err(err) => {
                    eprintln!("ling chat: Telegram: {err}; retrying in {}s", backoff.as_secs());
                    tokio::time::sleep(backoff).await;
                    backoff = (backoff * 2).min(Duration::from_secs(300));
                }
            }
        }
    }

    /// One update.
    pub async fn handle(&mut self, update: &Value) {
        let paired = TelegramConfig::load(&self.dir).map(|config| config.users).unwrap_or_default();
        if let Some(message) = update.get("message") {
            let Some(chat_id) = message.pointer("/chat/id").and_then(Value::as_i64) else { return };
            if message.pointer("/chat/type").and_then(Value::as_str) != Some("private") {
                let _ = self.api.call("leaveChat", json!({ "chat_id": chat_id })).await;
                return;
            }
            let Some(user) = message.pointer("/from/id").and_then(Value::as_i64) else { return };
            let text = message.get("text").and_then(Value::as_str).unwrap_or_default().trim().to_string();
            if let Some(code) = text.strip_prefix("/pair") {
                self.pair(chat_id, user, code.trim(), &paired).await;
                return;
            }
            if !paired.contains(&user) {
                let due = self.unpaired_replied.get(&user).is_none_or(|at| at.elapsed() >= UNPAIRED_REPLY_EVERY);
                if due {
                    self.unpaired_replied.insert(user, Instant::now());
                    self.reply(chat_id, UNPAIRED_REPLY).await;
                }
                return;
            }
            if text.is_empty() {
                self.reply(chat_id, "Only text messages for now.").await;
                return;
            }
            let _ = self.hub.send(Inbound::Text { chat: chat_key(chat_id), text });
        } else if let Some(query) = update.get("callback_query") {
            let Some(user) = query.pointer("/from/id").and_then(Value::as_i64) else { return };
            if let Some(id) = query.get("id").and_then(Value::as_str) {
                let _ = self.api.call("answerCallbackQuery", json!({ "callback_query_id": id })).await;
            }
            if !paired.contains(&user) {
                return;
            }
            let Some(chat_id) = query.pointer("/message/chat/id").and_then(Value::as_i64) else { return };
            let data = query.get("data").and_then(Value::as_str).unwrap_or_default();
            let mut parts = data.split(':');
            if parts.next() != Some("a") {
                return;
            }
            let (Some(approval), Some(choice)) =
                (parts.next().and_then(|n| n.parse::<u64>().ok()), parts.next().and_then(|n| n.parse::<usize>().ok()))
            else {
                return;
            };
            let _ = self.hub.send(Inbound::Answer { chat: chat_key(chat_id), approval, choice });
        } else if let Some(stopped) = update.get("stopped_message_generation") {
            let Some(chat_id) = stopped.pointer("/chat/id").and_then(Value::as_i64) else { return };
            // A private chat's id is its user's id.
            if paired.contains(&chat_id) {
                let _ = self.hub.send(Inbound::Stop { chat: chat_key(chat_id) });
            }
        }
    }

    async fn pair(&mut self, chat_id: i64, user: i64, code: &str, paired: &[i64]) {
        if paired.contains(&user) {
            self.reply(chat_id, "This Telegram account is already paired.").await;
            return;
        }
        if store::take_pairing_code(&self.dir, code) {
            self.wrong_codes = 0;
            let Some(mut config) = TelegramConfig::load(&self.dir) else { return };
            config.users.push(user);
            if config.save(&self.dir).is_err() {
                self.reply(chat_id, "Pairing failed: the bridge could not save it.").await;
                return;
            }
            self.reply(chat_id, &format!("Paired. Send a message to start.\n\n{HELP}")).await;
        } else {
            self.wrong_codes += 1;
            if self.wrong_codes >= PAIRING_ATTEMPTS {
                store::withdraw_pairing_codes(&self.dir);
                self.wrong_codes = 0;
            }
            self.reply(chat_id, "That code is not valid. Run `ling chat telegram pair` on the machine for a new one.").await;
        }
    }
}

/// What the renderer remembers per chat and per approval.
#[derive(Default)]
struct RenderState {
    drafts: HashMap<i64, i64>,
    next_draft: i64,
    drafts_unsupported: bool,
    last_typing: HashMap<i64, Instant>,
    approvals: HashMap<u64, (i64, i64, String)>,
}

/// Renders the hub's output into Telegram.
pub struct Renderer {
    pub api: TelegramApi,
    state: Arc<Mutex<RenderState>>,
}

fn tail(text: &str, limit: usize) -> String {
    let count = text.chars().count();
    if count <= limit { text.to_string() } else { text.chars().skip(count - limit).collect() }
}

impl Renderer {
    pub fn new(api: TelegramApi) -> Renderer {
        Renderer { api, state: Arc::default() }
    }

    pub async fn run(self, mut outbound: mpsc::UnboundedReceiver<Outbound>) {
        while let Some(out) = outbound.recv().await {
            self.render(out).await;
        }
    }

    /// Sends Markdown as HTML, or as plain text when Telegram cannot parse the HTML.
    async fn send_markdown(&self, chat_id: i64, markdown: &str) -> Result<Value, ApiError> {
        let html = json!({ "chat_id": chat_id, "text": render::to_html(markdown), "parse_mode": "HTML", "link_preview_options": { "is_disabled": true } });
        match self.api.call("sendMessage", html).await {
            Err(err) if err.description.contains("can't parse entities") => {
                self.api.call("sendMessage", json!({ "chat_id": chat_id, "text": markdown })).await
            }
            other => other,
        }
    }

    pub async fn render(&self, out: Outbound) {
        let Some(chat_id) = chat_id(out.chat()) else { return };
        match out {
            Outbound::Draft { text, .. } => {
                let mut state = self.state.lock().await;
                if !state.drafts_unsupported {
                    let draft = match state.drafts.get(&chat_id) {
                        Some(id) => *id,
                        None => {
                            state.next_draft += 1;
                            let id = state.next_draft;
                            state.drafts.insert(chat_id, id);
                            id
                        }
                    };
                    let params = json!({ "chat_id": chat_id, "draft_id": draft, "text": tail(&text, DRAFT_LIMIT), "can_stop": true });
                    match self.api.call("sendMessageDraft", params).await {
                        Err(err) if err.missing_method => state.drafts_unsupported = true,
                        _ => return,
                    }
                }
                let due = state.last_typing.get(&chat_id).is_none_or(|at| at.elapsed() >= Duration::from_secs(4));
                if due {
                    state.last_typing.insert(chat_id, Instant::now());
                    let _ = self.api.call("sendChatAction", json!({ "chat_id": chat_id, "action": "typing" })).await;
                }
            }
            Outbound::Message { markdown, .. } => {
                for part in render::split(&markdown, MESSAGE_LIMIT) {
                    if let Err(err) = self.send_markdown(chat_id, &part).await {
                        eprintln!("ling chat: Telegram refused an answer: {err}");
                    }
                }
            }
            Outbound::Notice { text, .. } => {
                for part in render::split(&text, MESSAGE_LIMIT) {
                    let _ = self.api.call("sendMessage", json!({ "chat_id": chat_id, "text": part })).await;
                }
            }
            Outbound::Approval { id, text, options, .. } => {
                let keyboard: Vec<Value> =
                    options.iter().enumerate().map(|(n, label)| json!([{ "text": label, "callback_data": format!("a:{id}:{n}") }])).collect();
                let params = json!({
                    "chat_id": chat_id,
                    "text": render::to_html(&text),
                    "parse_mode": "HTML",
                    "reply_markup": { "inline_keyboard": keyboard },
                });
                // A refused render must not hide the buttons: the request would be declined unseen.
                let sent = match self.api.call("sendMessage", params).await {
                    Err(err) if err.description.contains("can't parse entities") => {
                        let plain = json!({ "chat_id": chat_id, "text": text, "reply_markup": { "inline_keyboard": keyboard } });
                        self.api.call("sendMessage", plain).await
                    }
                    other => other,
                };
                if let Ok(sent) = sent
                    && let Some(message_id) = sent.get("message_id").and_then(Value::as_i64)
                {
                    self.state.lock().await.approvals.insert(id, (chat_id, message_id, text));
                }
            }
            Outbound::ApprovalClosed { id, outcome, .. } => {
                let remembered = self.state.lock().await.approvals.remove(&id);
                match remembered {
                    Some((chat_id, message_id, text)) => {
                        let html = format!("{}\n\n<i>{}</i>", render::to_html(&text), render::escape(&outcome));
                        let params = json!({ "chat_id": chat_id, "message_id": message_id, "text": html, "parse_mode": "HTML" });
                        let _ = self.api.call("editMessageText", params).await;
                    }
                    None => {
                        let _ = self.api.call("sendMessage", json!({ "chat_id": chat_id, "text": outcome })).await;
                    }
                }
            }
            Outbound::TurnEnded { .. } => {
                let mut state = self.state.lock().await;
                state.drafts.remove(&chat_id);
                state.last_typing.remove(&chat_id);
            }
        }
    }
}

/// Starts both halves, whether or not Telegram answers now: the poller backs off and retries, so a
/// node that boots before its network still comes up. Returns the bot's username when `getMe`
/// answered, or why it did not.
pub async fn start(
    api: TelegramApi,
    dir: PathBuf,
    hub: mpsc::UnboundedSender<Inbound>,
    outbound: mpsc::UnboundedReceiver<Outbound>,
) -> Result<String, ApiError> {
    let me = api.call("getMe", json!({})).await;
    if me.is_ok() {
        let _ = api.call("setMyCommands", commands()).await;
    }
    tokio::spawn(Renderer::new(api.clone()).run(outbound));
    tokio::spawn(Poller::new(api, dir, hub).run());
    me.map(|me| me.get("username").and_then(Value::as_str).unwrap_or_default().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_keys_round_trip() {
        assert_eq!(chat_id(&chat_key(-42)), Some(-42));
        assert_eq!(chat_id("matrix:!a:b"), None);
    }

    #[test]
    fn a_draft_keeps_the_end_of_a_long_answer() {
        let text = format!("{}END", "x".repeat(5000));
        let draft = tail(&text, DRAFT_LIMIT);
        assert_eq!(draft.chars().count(), DRAFT_LIMIT);
        assert!(draft.ends_with("END"));
    }
}
