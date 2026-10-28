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

use crate::attachments::Media;
use crate::blocks;
use crate::hub::HELP;
use crate::hub::Inbound;
use crate::hub::Outbound;
use crate::render;
use crate::repo_notice::AppThreadId;
use crate::repo_notice::RepoName;
use crate::store;
use crate::store::TelegramConfig;

pub const API_ROOT: &str = "https://api.telegram.org";
/// Telegram's limit is 4,096 characters after entities are parsed; this leaves room.
pub const MESSAGE_LIMIT: usize = 4000;
const DRAFT_LIMIT: usize = 4096;
/// Drafts to one chat, every topic together, at most this often (Telegram: one message a second).
const DRAFT_GAP_MS: u64 = 1_000;
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

impl ApiError {
    /// Whether Telegram refused the call because the topic was deleted, closed or does not exist.
    pub fn is_topic_gone(&self) -> bool {
        let lower = self.description.to_ascii_lowercase();
        lower.contains("topic_deleted")
            || lower.contains("topic_closed")
            || lower.contains("message thread not found")
            || lower.contains("topic not found")
            || lower.contains("thread not found")
            || lower.contains("message_thread_invalid")
    }
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.description)
    }
}

#[derive(Clone)]
pub struct TelegramApi {
    base: String,
    /// Where files are downloaded from: `<root>/file/bot<token>/<file_path>`.
    files: String,
    http: reqwest::Client,
}

/// The largest file the Bot API lets a bot download.
pub const DOWNLOAD_LIMIT: u64 = 20 * 1024 * 1024;

impl TelegramApi {
    /// `root` is `https://api.telegram.org`, or a test's stand-in.
    pub fn new(root: &str, token: &str) -> TelegramApi {
        let root = root.trim_end_matches('/');
        TelegramApi { base: format!("{root}/bot{token}"), files: format!("{root}/file/bot{token}"), http: reqwest::Client::new() }
    }

    /// Sends a file into a chat (`sendDocument`, multipart; MOONSHOT §3.7).
    pub async fn send_document(&self, chat_id: i64, topic: Option<i64>, name: &str, bytes: &[u8]) -> Result<Value, ApiError> {
        let failed = |description: String| ApiError { description, retry_after: None, missing_method: false };
        let boundary = format!("mightling{:016x}", rand_boundary());
        let safe: String = name.chars().map(|c| if c == '"' || c.is_control() { '_' } else { c }).collect();
        let mut body = Vec::with_capacity(bytes.len() + 512);
        let mut field = |key: &str, value: &str| body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").as_bytes());
        field("chat_id", &chat_id.to_string());
        if let Some(topic) = topic {
            field("message_thread_id", &topic.to_string());
        }
        body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{safe}\"\r\nContent-Type: application/octet-stream\r\n\r\n").as_bytes());
        body.extend_from_slice(bytes);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        let response = self
            .http
            .post(format!("{}/sendDocument", self.base))
            .header("Content-Type", format!("multipart/form-data; boundary={boundary}"))
            .body(body)
            .timeout(Duration::from_secs(300))
            .send()
            .await
            .map_err(|err| failed(err.without_url().to_string()))?;
        let value: Value = response.json().await.map_err(|err| failed(err.without_url().to_string()))?;
        if value.get("ok") == Some(&Value::Bool(true)) {
            return Ok(value.get("result").cloned().unwrap_or(Value::Null));
        }
        Err(failed(value.get("description").and_then(Value::as_str).unwrap_or("Telegram refused the file").to_string()))
    }

    /// Downloads a file the user sent: `getFile` for its path, then the file itself.
    pub async fn download(&self, file_id: &str) -> Result<Vec<u8>, ApiError> {
        let failed = |description: String| ApiError { description, retry_after: None, missing_method: false };
        let file = self.call("getFile", json!({ "file_id": file_id })).await?;
        let path = file.get("file_path").and_then(Value::as_str).ok_or_else(|| failed("Telegram named no file path".to_string()))?;
        let response = self
            .http
            .get(format!("{}/{path}", self.files))
            .timeout(Duration::from_secs(120))
            .send()
            .await
            .map_err(|err| failed(err.without_url().to_string()))?;
        if !response.status().is_success() {
            return Err(failed(format!("Telegram refused the download (HTTP {})", response.status().as_u16())));
        }
        let bytes = response.bytes().await.map_err(|err| failed(err.without_url().to_string()))?;
        if bytes.len() as u64 > DOWNLOAD_LIMIT {
            return Err(failed("the file is larger than a bot may download".to_string()));
        }
        Ok(bytes.to_vec())
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

/// An approval button's data, `a:<nonce>:<choice>`: the nonce is the hub's, never a counter.
pub fn approval_callback(data: &str) -> Option<(String, usize)> {
    let mut parts = data.split(':');
    if parts.next() != Some("a") {
        return None;
    }
    let nonce = parts.next().filter(|n| !n.is_empty() && n.len() <= 32 && n.chars().all(|c| c.is_ascii_hexdigit()))?;
    let choice = parts.next()?.parse().ok()?;
    parts.next().is_none().then(|| (nonce.to_string(), choice))
}

/// A file in a message, as the Bot API describes it: its id, a name and a type.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Attached {
    pub file_id: String,
    pub name: String,
    pub mime: String,
}

/// What a message carries besides its text: a photo (its largest size) or a document, and the
/// kinds the bridge does not take, named for the user.
pub fn attachments_of(message: &Value) -> (Vec<Attached>, Vec<String>) {
    let mut files = Vec::new();
    if let Some(sizes) = message.get("photo").and_then(Value::as_array)
        && let Some(largest) = sizes.iter().max_by_key(|size| size.get("width").and_then(Value::as_u64).unwrap_or(0) * size.get("height").and_then(Value::as_u64).unwrap_or(0))
        && let Some(id) = largest.get("file_id").and_then(Value::as_str)
    {
        files.push(Attached { file_id: id.to_string(), name: "photo.jpg".to_string(), mime: "image/jpeg".to_string() });
    }
    if let Some(document) = message.get("document")
        && let Some(id) = document.get("file_id").and_then(Value::as_str)
    {
        files.push(Attached {
            file_id: id.to_string(),
            name: document.get("file_name").and_then(Value::as_str).unwrap_or("file").to_string(),
            mime: document.get("mime_type").and_then(Value::as_str).unwrap_or("application/octet-stream").to_string(),
        });
    }
    // A voice message or an audio file is transcribed on this machine (MOONSHOT §3.4).
    for (field, name) in [("voice", "voice.ogg"), ("audio", "audio")] {
        if let Some(recording) = message.get(field)
            && let Some(id) = recording.get("file_id").and_then(Value::as_str)
        {
            files.push(Attached {
                file_id: id.to_string(),
                name: recording.get("file_name").and_then(Value::as_str).unwrap_or(name).to_string(),
                mime: recording.get("mime_type").and_then(Value::as_str).unwrap_or("audio/ogg").to_string(),
            });
        }
    }
    let dropped = [
        ("video", "a video"),
        ("video_note", "a video message"),
        ("animation", "an animation"),
        ("sticker", "a sticker"),
        ("location", "a location"),
        ("contact", "a contact"),
        ("poll", "a poll"),
    ]
    .iter()
    .filter(|(field, _)| message.get(*field).is_some_and(|v| !v.is_null()))
    .map(|(_, named)| named.to_string())
    .collect();
    (files, dropped)
}

/// A multipart boundary that is not guessable from the file.
fn rand_boundary() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0) ^ (std::process::id() as u64).rotate_left(32)
}

fn chat_key(chat_id: i64) -> String {
    format!("telegram:{chat_id}")
}

/// A chat's key, with the topic when the message is in one (MOONSHOT §3.1): each topic is its own
/// conversation in the hub, with its own thread.
pub fn topic_key(chat_id: i64, topic: Option<i64>) -> String {
    match topic {
        Some(topic) => format!("telegram:{chat_id}/{topic}"),
        None => chat_key(chat_id),
    }
}

/// The chat and the topic a key names.
pub fn target(key: &str) -> Option<(i64, Option<i64>)> {
    let rest = key.strip_prefix("telegram:")?;
    match rest.split_once('/') {
        Some((chat, topic)) => Some((chat.parse().ok()?, Some(topic.parse().ok()?))),
        None => Some((rest.parse().ok()?, None)),
    }
}

/// The topic a message was sent in, if any.
fn topic_of_message(message: &Value) -> Option<i64> {
    (message.get("is_topic_message") == Some(&Value::Bool(true))).then(|| message.get("message_thread_id").and_then(Value::as_i64)).flatten()
}

/// The commands Telegram offers on `/`; `/app` only when a Mini App address is set.
pub fn commands(mini_app: bool) -> Value {
    let mut list = vec![
        json!({ "command": "new", "description": "Start a new thread" }),
        json!({ "command": "stop", "description": "Stop the running answer" }),
        json!({ "command": "threads", "description": "Your recent threads" }),
        json!({ "command": "use", "description": "Continue a thread: /use <n>" }),
        json!({ "command": "status", "description": "What Mightling is doing" }),
        json!({ "command": "night", "description": "Night Shift: /night add <task>, /night status" }),
    ];
    if mini_app {
        list.push(json!({ "command": "app", "description": "Open Mightling's app, over your own network" }));
    }
    list.push(json!({ "command": "help", "description": "What this bot does" }));
    json!({ "commands": list })
}

/// The menu button of a paired user's chat (MOONSHOT §15): Mightling's own page, which the phone
/// loads straight from the user's HTTPS front (never through Telegram), or Telegram's default.
pub fn menu_button(chat_id: i64, mini_app_url: Option<&str>) -> Value {
    let button = match mini_app_url {
        Some(url) => json!({ "type": "web_app", "text": "Mightling", "web_app": { "url": url } }),
        None => json!({ "type": "default" }),
    };
    json!({ "chat_id": chat_id, "menu_button": button })
}

/// The answer to `/app`: a button that opens the Mini App, or how to set one up.
pub fn app_reply(chat_id: i64, mini_app_url: Option<&str>) -> Value {
    match mini_app_url {
        Some(url) => json!({
            "chat_id": chat_id,
            "text": "Opens Mightling's own app, loaded from your machine over your own network: what you type in it never passes through Telegram.",
            "reply_markup": { "inline_keyboard": [[{ "text": "Open Mightling", "web_app": { "url": url } }]] },
        }),
        None => json!({
            "chat_id": chat_id,
            "text": "No app is set up. On the machine, serve `ling web` over HTTPS on your own network (for example `tailscale serve`) and set `telegram_mini_app_url` in the [chat] table of ~/.config/dreamference/config.toml.",
        }),
    }
}

/// Reads updates and hands paired users' messages to the hub.
pub struct Poller {
    pub api: TelegramApi,
    pub dir: PathBuf,
    pub hub: mpsc::UnboundedSender<Inbound>,
    /// Whether the bot has topics in private chats (`getMe`'s `has_topics_enabled`) and the user's
    /// `[chat] telegram_topics` does not turn them off: then `/new` makes a topic (MOONSHOT §3.1).
    pub topics: bool,
    /// The Mini App's address (`[chat] telegram_mini_app_url`), when set.
    pub mini_app_url: Option<String>,
    wrong_codes: u32,
    unpaired_replied: HashMap<i64, Instant>,
}

/// A new topic's name until the thread's first message names it.
pub const NEW_TOPIC_NAME: &str = "New thread";

impl Poller {
    pub fn new(api: TelegramApi, dir: PathBuf, hub: mpsc::UnboundedSender<Inbound>) -> Poller {
        Poller { api, dir, hub, topics: false, mini_app_url: None, wrong_codes: 0, unpaired_replied: HashMap::new() }
    }

    /// Opens a fresh topic in `chat_id` and remembers it as the chat's last used topic.
    async fn open_topic(&self, chat_id: i64) -> Option<i64> {
        let topic = self.api.call("createForumTopic", json!({ "chat_id": chat_id, "name": NEW_TOPIC_NAME })).await.ok()?;
        let thread = topic.get("message_thread_id").and_then(Value::as_i64)?;
        store::set_last_topic(&self.dir, chat_id, Some(thread));
        Some(thread)
    }

    /// `/new` in a chat with topics: a topic, named when its first message arrives, and one line in
    /// it. False when Telegram refused, so the hub's `/new` happens instead.
    async fn new_topic(&self, chat_id: i64) -> bool {
        let Some(thread) = self.open_topic(chat_id).await else { return false };
        let line = json!({ "chat_id": chat_id, "message_thread_id": thread, "text": "A new thread: ask here." });
        let _ = self.api.call("sendMessage", line).await;
        true
    }

    /// Which topic a message in `chat_id` belongs to: the message's own topic when sent inside
    /// one, or, when `self.topics` is on, the topic most recently used in that chat when sent in
    /// the main chat. Opens a fresh topic when the chat has no last topic yet or Telegram says the
    /// last topic was deleted or closed.
    async fn resolve_topic(&self, chat_id: i64, message_topic: Option<i64>) -> Option<i64> {
        if let Some(topic) = message_topic {
            if !store::is_repo_topic(&self.dir, chat_id, topic) {
                store::set_last_topic(&self.dir, chat_id, Some(topic));
            }
            return Some(topic);
        }
        if !self.topics {
            return None;
        }
        if let Some(last) = store::last_topic(&self.dir, chat_id) {
            let probe = json!({ "chat_id": chat_id, "message_thread_id": last, "action": "typing" });
            match self.api.call("sendChatAction", probe).await {
                Err(err) if err.is_topic_gone() => return self.open_topic(chat_id).await,
                _ => {
                    store::set_last_topic(&self.dir, chat_id, Some(last));
                    return Some(last);
                }
            }
        }
        self.open_topic(chat_id).await
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
            let message_topic = topic_of_message(message);
            if message.get("forum_topic_closed").is_some()
                && message_topic.is_some()
                && store::last_topic(&self.dir, chat_id) == message_topic
            {
                store::set_last_topic(&self.dir, chat_id, None);
            }
            if message.get("forum_topic_reopened").is_some()
                && let Some(topic) = message_topic
            {
                store::set_last_topic(&self.dir, chat_id, Some(topic));
            }
            // A topic's service messages (created, renamed, closed) are not questions.
            if ["forum_topic_created", "forum_topic_edited", "forum_topic_closed", "forum_topic_reopened"].iter().any(|field| message.get(*field).is_some()) {
                return;
            }
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
            let (files, dropped) = attachments_of(message);
            if files.is_empty() && dropped.is_empty() {
                if text.is_empty() {
                    self.reply(chat_id, "I read text, images and files here.").await;
                    return;
                }
                let command = text.split_whitespace().next().unwrap_or_default().split('@').next().unwrap_or_default().to_ascii_lowercase();
                if self.topics && command == "/new" && self.new_topic(chat_id).await {
                    return;
                }
                if command == "/app" {
                    let _ = self.api.call("sendMessage", app_reply(chat_id, self.mini_app_url.as_deref())).await;
                    return;
                }
                let key = topic_key(chat_id, self.resolve_topic(chat_id, message_topic).await);
                let _ = self.hub.send(Inbound::Text { chat: key, text });
                return;
            }
            let key = topic_key(chat_id, self.resolve_topic(chat_id, message_topic).await);
            let caption = message.get("caption").and_then(Value::as_str).unwrap_or_default().trim().to_string();
            let mut attachments = Vec::new();
            for file in files {
                match self.api.download(&file.file_id).await {
                    Ok(bytes) => attachments.push(Media { name: file.name, mime: file.mime, bytes }),
                    Err(err) => self.reply(chat_id, &format!("I couldn't download {} from Telegram ({err}); send it again.", file.name)).await,
                }
            }
            let text = if text.is_empty() { caption } else { text };
            let _ = self.hub.send(Inbound::Media { chat: key, text, attachments, dropped });
        } else if let Some(query) = update.get("callback_query") {
            let Some(user) = query.pointer("/from/id").and_then(Value::as_i64) else { return };
            if let Some(id) = query.get("id").and_then(Value::as_str) {
                let _ = self.api.call("answerCallbackQuery", json!({ "callback_query_id": id })).await;
            }
            if !paired.contains(&user) {
                return;
            }
            let Some(chat_id) = query.pointer("/message/chat/id").and_then(Value::as_i64) else { return };
            let key = topic_key(chat_id, query.get("message").and_then(topic_of_message));
            let data = query.get("data").and_then(Value::as_str).unwrap_or_default();
            let Some((approval, choice)) = approval_callback(data) else { return };
            let _ = self.hub.send(Inbound::Answer { chat: key, approval, choice });
        } else if let Some(stopped) = update.get("stopped_message_generation") {
            let Some(chat_id) = stopped.pointer("/chat/id").and_then(Value::as_i64) else { return };
            // A private chat's id is its user's id.
            if paired.contains(&chat_id) {
                let topic = stopped.get("message_thread_id").and_then(Value::as_i64);
                let _ = self.hub.send(Inbound::Stop { chat: topic_key(chat_id, topic) });
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
            if self.mini_app_url.is_some() {
                let _ = self.api.call("setChatMenuButton", menu_button(chat_id, self.mini_app_url.as_deref())).await;
            }
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
    /// The draft id of each conversation (chat and topic) with a turn running.
    drafts: HashMap<String, i64>,
    next_draft: i64,
    drafts_unsupported: bool,
    /// Rich messages refused for good (an older Bot API): plain HTML from then on.
    rich_unsupported: bool,
    last_typing: HashMap<String, Instant>,
    /// The last draft sent to each chat, and for which conversation: every topic shares the
    /// chat's one message a second (one conversation is already paced by the hub).
    last_draft: HashMap<i64, (Instant, String)>,
    /// Approvals on screen, by nonce: (chat, topic, message id, text).
    approvals: HashMap<String, (i64, Option<i64>, i64, String)>,
    /// In-memory repository topics when `dir` is not set: `(chat_id, repo) -> topic_id`.
    repo_topics: HashMap<(i64, String), i64>,
}

/// How the renderer draws (`[chat] telegram_rich`; MOONSHOT §3.2).
#[derive(Clone, Copy, Debug, Default)]
pub struct Style {
    /// Drafts and answers as rich messages built from blocks; HTML when refused.
    pub rich: bool,
}

/// Renders the hub's output into Telegram.
pub struct Renderer {
    pub api: TelegramApi,
    pub style: Style,
    pub dir: Option<PathBuf>,
    pub topics: bool,
    pub mini_app_url: Option<String>,
    state: Arc<Mutex<RenderState>>,
}

fn tail(text: &str, limit: usize) -> String {
    let count = text.chars().count();
    if count <= limit { text.to_string() } else { text.chars().skip(count - limit).collect() }
}

/// Adds the topic to a request's parameters.
fn in_topic(mut params: Value, topic: Option<i64>) -> Value {
    if let Some(topic) = topic {
        params["message_thread_id"] = json!(topic);
    }
    params
}

/// An approval's keyboard: an "Approve…" first option green, a last "Decline" red (Bot API 9.4
/// `style`); a question's own options keep the app's style.
fn approval_keyboard(id: &str, options: &[String]) -> Value {
    let last = options.len().saturating_sub(1);
    let rows: Vec<Value> = options
        .iter()
        .enumerate()
        .map(|(n, label)| {
            let mut button = json!({ "text": label, "callback_data": format!("a:{id}:{n}") });
            if n == 0 && options.len() > 1 && label.starts_with("Approve") {
                button["style"] = json!("success");
            } else if n == last && label == "Decline" {
                button["style"] = json!("danger");
            }
            json!([button])
        })
        .collect();
    json!({ "inline_keyboard": rows })
}

/// The keyboard of a closed approval: its outcome as a button that does nothing (10.3 `disabled`).
fn closed_keyboard(outcome: &str) -> Value {
    json!({ "inline_keyboard": [[{ "text": outcome.trim_end_matches('.'), "disabled": {} }]] })
}

impl Renderer {
    pub fn new(api: TelegramApi) -> Renderer {
        Renderer::styled(api, Style::default())
    }

    pub fn styled(api: TelegramApi, style: Style) -> Renderer {
        Renderer {
            api,
            style,
            dir: None,
            topics: false,
            mini_app_url: None,
            state: Arc::default(),
        }
    }

    pub fn with_dir(mut self, dir: PathBuf) -> Renderer {
        self.dir = Some(dir);
        self
    }

    pub fn with_topics(mut self, topics: bool) -> Renderer {
        self.topics = topics;
        self
    }

    pub fn with_mini_app_url(mut self, mini_app_url: Option<String>) -> Renderer {
        self.mini_app_url = mini_app_url;
        self
    }

    fn open_repo_keyboard(&self, repo: &RepoName, app_thread: Option<&AppThreadId>, approval: bool) -> Option<Value> {
        let front = self.mini_app_url.as_deref()?;
        let url = match app_thread {
            Some(tid) => ling_web_server::telegram_app::open_thread_url(front, tid.as_str(), approval),
            None => ling_web_server::telegram_app::open_repo_url(front, repo.as_str()),
        }?;
        Some(json!({
            "inline_keyboard": [[{ "text": "Open", "web_app": { "url": url } }]]
        }))
    }

    async fn existing_repo_topic(&self, chat_id: i64, repo: &RepoName) -> Option<i64> {
        if let Some(dir) = &self.dir
            && let Some(id) = store::repo_topic(dir, chat_id, repo.as_str())
        {
            return Some(id);
        }
        self.state.lock().await.repo_topics.get(&(chat_id, repo.as_str().to_string())).copied()
    }

    async fn create_repo_topic(&self, chat_id: i64, repo: &RepoName) -> Option<i64> {
        let topic = self
            .api
            .call("createForumTopic", json!({ "chat_id": chat_id, "name": repo.as_str() }))
            .await
            .ok()?;
        let thread = topic.get("message_thread_id").and_then(Value::as_i64)?;
        if let Some(dir) = &self.dir {
            store::set_repo_topic(dir, chat_id, repo.as_str(), thread);
        }
        self.state.lock().await.repo_topics.insert((chat_id, repo.as_str().to_string()), thread);
        Some(thread)
    }

    async fn resolve_repo_topic(&self, chat_id: i64, repo: &RepoName) -> Option<i64> {
        if !self.topics {
            return None;
        }
        if let Some(existing) = self.existing_repo_topic(chat_id, repo).await {
            return Some(existing);
        }
        self.create_repo_topic(chat_id, repo).await
    }

    pub async fn run(self, mut outbound: mpsc::UnboundedReceiver<Outbound>) {
        while let Some(out) = outbound.recv().await {
            self.render(out).await;
        }
    }

    /// Sends Markdown as HTML, or as plain text when Telegram cannot parse the HTML.
    async fn send_markdown(&self, chat_id: i64, topic: Option<i64>, markdown: &str) -> Result<Value, ApiError> {
        let html = in_topic(json!({ "chat_id": chat_id, "text": render::to_html(markdown), "parse_mode": "HTML", "link_preview_options": { "is_disabled": true } }), topic);
        match self.api.call("sendMessage", html).await {
            Err(err) if err.description.contains("can't parse entities") => self.api.call("sendMessage", in_topic(json!({ "chat_id": chat_id, "text": markdown }), topic)).await,
            other => other,
        }
    }

    /// An answer as rich messages, split between blocks; false when Telegram refused the first, so
    /// the HTML path sends it instead (a failed render never loses an answer).
    async fn send_rich(&self, chat_id: i64, topic: Option<i64>, markdown: &str) -> bool {
        if !self.style.rich || self.state.lock().await.rich_unsupported {
            return false;
        }
        let parts = blocks::split(blocks::to_blocks(markdown), blocks::MESSAGE_CHARS, blocks::MESSAGE_BLOCKS);
        for (n, part) in parts.into_iter().enumerate() {
            let params = in_topic(json!({ "chat_id": chat_id, "rich_message": { "blocks": part } }), topic);
            match self.api.call("sendRichMessage", params).await {
                Ok(_) => {}
                Err(err) if n == 0 => {
                    if err.missing_method {
                        self.state.lock().await.rich_unsupported = true;
                    }
                    eprintln!("ling chat: Telegram refused a rich message ({err}); sending it as HTML");
                    return false;
                }
                Err(err) => eprintln!("ling chat: Telegram refused part of a rich answer: {err}"),
            }
        }
        true
    }

    async fn draft(&self, key: &str, chat_id: i64, topic: Option<i64>, text: &str) {
        let mut state = self.state.lock().await;
        // Two topics streaming share the chat's limit: a draft within a second of another topic's
        // waits for the next (the hub sends again on new text and every 20 s).
        if !text.is_empty() && state.last_draft.get(&chat_id).is_some_and(|(at, from)| from != key && at.elapsed() < Duration::from_millis(DRAFT_GAP_MS)) {
            return;
        }
        if !state.drafts_unsupported {
            let draft = match state.drafts.get(key) {
                Some(id) => *id,
                None => {
                    state.next_draft += 1;
                    let id = state.next_draft;
                    state.drafts.insert(key.to_string(), id);
                    id
                }
            };
            state.last_draft.insert(chat_id, (Instant::now(), key.to_string()));
            // The stop button keeps the partial on screen; the hub then sends it as a message (D9).
            if self.style.rich && !state.rich_unsupported {
                let content = if text.is_empty() { vec![blocks::thinking()] } else { blocks::to_blocks(&tail(text, blocks::MESSAGE_CHARS)) };
                let params = in_topic(json!({ "chat_id": chat_id, "draft_id": draft, "rich_message": { "blocks": content }, "can_stop": true, "keep_on_stop": true }), topic);
                match self.api.call("sendRichMessageDraft", params).await {
                    Ok(_) => return,
                    Err(err) if err.missing_method => state.rich_unsupported = true,
                    Err(_) => {}
                }
            }
            let params = in_topic(json!({ "chat_id": chat_id, "draft_id": draft, "text": tail(text, DRAFT_LIMIT), "can_stop": true, "keep_on_stop": true }), topic);
            match self.api.call("sendMessageDraft", params).await {
                Err(err) if err.missing_method => state.drafts_unsupported = true,
                _ => return,
            }
        }
        let due = state.last_typing.get(key).is_none_or(|at| at.elapsed() >= Duration::from_secs(4));
        if due {
            state.last_typing.insert(key.to_string(), Instant::now());
            let _ = self.api.call("sendChatAction", in_topic(json!({ "chat_id": chat_id, "action": "typing" }), topic)).await;
        }
    }

    pub async fn render(&self, out: Outbound) {
        let key = out.chat().to_string();
        let Some((chat_id, topic)) = target(&key) else { return };
        match out {
            Outbound::Draft { text, .. } => self.draft(&key, chat_id, topic, &text).await,
            Outbound::Message { markdown, .. } => {
                if let (Some(dir), Some(topic)) = (&self.dir, topic) {
                    store::set_last_topic(dir, chat_id, Some(topic));
                }
                if self.send_rich(chat_id, topic, &markdown).await {
                    return;
                }
                for part in render::split(&markdown, MESSAGE_LIMIT) {
                    if let Err(err) = self.send_markdown(chat_id, topic, &part).await {
                        eprintln!("ling chat: Telegram refused an answer: {err}");
                    }
                }
            }
            Outbound::Notice { text, .. } => {
                for part in render::split(&text, MESSAGE_LIMIT) {
                    let _ = self.api.call("sendMessage", in_topic(json!({ "chat_id": chat_id, "text": part }), topic)).await;
                }
            }
            Outbound::RepoNotice { notice, .. } => {
                let target_topic = if self.topics {
                    self.resolve_repo_topic(chat_id, &notice.repo).await.or(topic)
                } else {
                    topic
                };
                let text = notice.text();
                let markup = self.open_repo_keyboard(&notice.repo, notice.app_thread.as_ref(), notice.is_approval());
                let build_params = |t: Option<i64>| {
                    let mut params = in_topic(json!({ "chat_id": chat_id, "text": text }), t);
                    if let Some(kb) = &markup {
                        params["reply_markup"] = kb.clone();
                    }
                    params
                };
                match self.api.call("sendMessage", build_params(target_topic)).await {
                    Err(err) if self.topics && target_topic.is_some() && err.is_topic_gone() => {
                        if let Some(new_topic) = self.create_repo_topic(chat_id, &notice.repo).await {
                            let _ = self.api.call("sendMessage", build_params(Some(new_topic))).await;
                        }
                    }
                    _ => {}
                }
            }
            Outbound::RepoTypedRefusal { repo, .. } => {
                let target_topic = match topic {
                    Some(t) => Some(t),
                    None if self.topics => self.existing_repo_topic(chat_id, &repo).await,
                    None => None,
                };
                let mut params = in_topic(
                    json!({ "chat_id": chat_id, "text": "Repo work happens in the app." }),
                    target_topic,
                );
                if let Some(kb) = self.open_repo_keyboard(&repo, None, false) {
                    params["reply_markup"] = kb;
                }
                let _ = self.api.call("sendMessage", params).await;
            }
            Outbound::Approval { id, text, options, .. } => {
                let keyboard = approval_keyboard(&id, &options);
                let params = in_topic(json!({ "chat_id": chat_id, "text": render::to_html(&text), "parse_mode": "HTML", "reply_markup": keyboard }), topic);
                // A refused render must not hide the buttons: the request would be declined unseen.
                let sent = match self.api.call("sendMessage", params).await {
                    Err(err) if err.description.contains("can't parse entities") => {
                        self.api.call("sendMessage", in_topic(json!({ "chat_id": chat_id, "text": text, "reply_markup": keyboard }), topic)).await
                    }
                    other => other,
                };
                if let Ok(sent) = sent
                    && let Some(message_id) = sent.get("message_id").and_then(Value::as_i64)
                {
                    self.state.lock().await.approvals.insert(id, (chat_id, topic, message_id, text));
                }
            }
            Outbound::ApprovalClosed { id, outcome, .. } => {
                let remembered = self.state.lock().await.approvals.remove(&id);
                match remembered {
                    Some((chat_id, _, message_id, text)) => {
                        // The request stays readable; its outcome replaces the keyboard as a button
                        // that does nothing. An older Bot API refuses `disabled`: then no keyboard.
                        let html = format!("{}\n\n<i>{}</i>", render::to_html(&text), render::escape(&outcome));
                        let params = json!({ "chat_id": chat_id, "message_id": message_id, "text": html, "parse_mode": "HTML", "reply_markup": closed_keyboard(&outcome) });
                        if self.api.call("editMessageText", params).await.is_err() {
                            let bare = json!({ "chat_id": chat_id, "message_id": message_id, "text": html, "parse_mode": "HTML", "reply_markup": { "inline_keyboard": [] } });
                            let _ = self.api.call("editMessageText", bare).await;
                        }
                    }
                    None => {
                        let _ = self.api.call("sendMessage", in_topic(json!({ "chat_id": chat_id, "text": outcome }), topic)).await;
                    }
                }
            }
            Outbound::File { name, bytes, .. } => {
                if let Err(err) = self.api.send_document(chat_id, topic, &name, &bytes).await {
                    let line = format!("Telegram refused {name}: {err}");
                    let _ = self.api.call("sendMessage", in_topic(json!({ "chat_id": chat_id, "text": line }), topic)).await;
                }
            }
            Outbound::ThreadNamed { name, .. } => {
                if let Some(topic) = topic {
                    let name: String = name.chars().take(128).collect();
                    let _ = self.api.call("editForumTopic", json!({ "chat_id": chat_id, "message_thread_id": topic, "name": name })).await;
                }
            }
            Outbound::TurnEnded { .. } => {
                let mut state = self.state.lock().await;
                state.drafts.remove(&key);
                state.last_typing.remove(&key);
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
    options: Options,
) -> Result<String, ApiError> {
    let me = api.call("getMe", json!({})).await;
    if me.is_ok() {
        let _ = api.call("setMyCommands", commands(options.mini_app_url.is_some())).await;
        // Each paired user's menu button follows the setting, so removing the address removes it.
        let users = TelegramConfig::load(&dir).map(|config| config.users).unwrap_or_default();
        for user in users {
            let _ = api.call("setChatMenuButton", menu_button(user, options.mini_app_url.as_deref())).await;
        }
    }
    let has_topics = me.as_ref().is_ok_and(|me| me.get("has_topics_enabled") == Some(&Value::Bool(true)));
    let use_topics = has_topics && options.topics;
    tokio::spawn(
        Renderer::styled(api.clone(), Style { rich: options.rich })
            .with_dir(dir.clone())
            .with_topics(use_topics)
            .with_mini_app_url(options.mini_app_url.clone())
            .run(outbound),
    );
    let mut poller = Poller::new(api, dir, hub);
    poller.topics = use_topics;
    poller.mini_app_url = options.mini_app_url.clone();
    tokio::spawn(poller.run());
    me.map(|me| me.get("username").and_then(Value::as_str).unwrap_or_default().to_string())
}

/// The user's choices for Telegram (`[chat] telegram_topics`, `telegram_rich`,
/// `telegram_mini_app_url`).
#[derive(Clone, Debug)]
pub struct Options {
    /// Topics as threads, when the bot has them (default on).
    pub topics: bool,
    /// Rich messages (default off until Telegram's Phase 0 check on the owner's clients).
    pub rich: bool,
    /// The Mini App's `https://` address on the user's own network (MOONSHOT §15); none: no app.
    pub mini_app_url: Option<String>,
}

impl Default for Options {
    fn default() -> Options {
        Options { topics: true, rich: false, mini_app_url: None }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_keys_round_trip() {
        assert_eq!(target(&chat_key(-42)), Some((-42, None)));
        assert_eq!(target("matrix:!a:b"), None);
        // A topic is part of the key (MOONSHOT §3.1).
        assert_eq!(topic_key(111, Some(7)), "telegram:111/7");
        assert_eq!(target("telegram:111/7"), Some((111, Some(7))));
        assert_eq!(target("telegram:111/x"), None);
    }

    #[test]
    fn a_draft_keeps_the_end_of_a_long_answer() {
        let text = format!("{}END", "x".repeat(5000));
        let draft = tail(&text, DRAFT_LIMIT);
        assert_eq!(draft.chars().count(), DRAFT_LIMIT);
        assert!(draft.ends_with("END"));
    }

    #[test]
    fn topic_gone_errors_are_recognised() {
        let err = |description: &str| ApiError { description: description.to_string(), retry_after: None, missing_method: false };
        assert!(err("Bad Request: TOPIC_DELETED").is_topic_gone());
        assert!(err("Bad Request: TOPIC_CLOSED").is_topic_gone());
        assert!(err("Bad Request: message thread not found").is_topic_gone());
        assert!(!err("Bad Request: TOPIC_NOT_MODIFIED").is_topic_gone());
        assert!(!err("Bad Request: can't parse entities").is_topic_gone());
    }
}
