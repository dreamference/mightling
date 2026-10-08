//! The Matrix adapter (MIGHTLING_CHAT §5): the private messenger. The homeserver runs on this
//! machine with no route out (`ling-admin matrix`), and the phone reaches it over Tailscale.
//!
//! The adapter is the bot account `@mightling:<server name>`. It opens an unencrypted direct room
//! with each allowed user (everything is on this machine, so end-to-end encryption would protect
//! only against the machine that reads every message to answer it: §5.4), accepts invites from
//! allowed users only, leaves encrypted rooms and rooms with a third member, long-polls `/sync`,
//! shows "typing" while a turn runs, and answers approvals from ✅/❌ reactions or a `yes`/`no`
//! reply (the hub reads the replies).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use serde_json::Value;
use serde_json::json;
use tokio::sync::Mutex;
use tokio::sync::mpsc;

use crate::hub::Inbound;
use crate::hub::Outbound;
use crate::render;
use crate::store::MatrixConfig;
use crate::store::MatrixState;

/// The bridge splits Matrix messages here (an event may be 64 KB in all).
pub const MESSAGE_LIMIT: usize = 16_000;
const SYNC_TIMEOUT_MS: u64 = 30_000;
const TYPING_RENEW: Duration = Duration::from_secs(20);

/// A path segment: everything but the unreserved characters percent-encoded.
pub fn encode(segment: &str) -> String {
    let mut out = String::new();
    for byte in segment.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

#[derive(Clone, Debug, PartialEq)]
pub struct MatrixError {
    pub errcode: String,
    pub error: String,
}

impl std::fmt::Display for MatrixError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} ({})", self.error, self.errcode)
    }
}

#[derive(Clone)]
pub struct MatrixApi {
    base: String,
    token: String,
    http: reqwest::Client,
    txn: Arc<std::sync::atomic::AtomicU64>,
}

impl MatrixApi {
    pub fn new(homeserver: &str, token: &str) -> MatrixApi {
        MatrixApi {
            base: homeserver.trim_end_matches('/').to_string(),
            token: token.to_string(),
            http: reqwest::Client::new(),
            txn: Arc::default(),
        }
    }

    /// One request; `M_LIMIT_EXCEEDED` is waited out and retried, up to three times.
    pub async fn request(&self, method: reqwest::Method, path: &str, body: Option<Value>, timeout: Duration) -> Result<Value, MatrixError> {
        for _ in 0..4 {
            let mut request = self.http.request(method.clone(), format!("{}{path}", self.base)).bearer_auth(&self.token).timeout(timeout);
            if let Some(body) = &body {
                request = request.json(body);
            }
            let failed = |err: reqwest::Error| MatrixError { errcode: "M_UNREACHABLE".to_string(), error: err.without_url().to_string() };
            let response = request.send().await.map_err(failed)?;
            let status = response.status();
            let value: Value = response.json().await.unwrap_or(Value::Null);
            if status.is_success() {
                return Ok(value);
            }
            let errcode = value.get("errcode").and_then(Value::as_str).unwrap_or("M_UNKNOWN").to_string();
            if errcode == "M_LIMIT_EXCEEDED" {
                let wait = value.get("retry_after_ms").and_then(Value::as_u64).unwrap_or(1000).min(60_000);
                tokio::time::sleep(Duration::from_millis(wait)).await;
                continue;
            }
            return Err(MatrixError { errcode, error: value.get("error").and_then(Value::as_str).unwrap_or_default().to_string() });
        }
        Err(MatrixError { errcode: "M_LIMIT_EXCEEDED".to_string(), error: "rate limited".to_string() })
    }

    fn short() -> Duration {
        Duration::from_secs(30)
    }

    pub async fn send(&self, room: &str, content: Value) -> Result<String, MatrixError> {
        let txn = format!("lc{}-{}", std::process::id(), self.txn.fetch_add(1, std::sync::atomic::Ordering::Relaxed));
        let path = format!("/_matrix/client/v3/rooms/{}/send/m.room.message/{txn}", encode(room));
        let answer = self.request(reqwest::Method::PUT, &path, Some(content), Self::short()).await?;
        Ok(answer.get("event_id").and_then(Value::as_str).unwrap_or_default().to_string())
    }

    pub async fn typing(&self, room: &str, user: &str, typing: bool) {
        let path = format!("/_matrix/client/v3/rooms/{}/typing/{}", encode(room), encode(user));
        let body = if typing { json!({ "typing": true, "timeout": 30_000 }) } else { json!({ "typing": false }) };
        let _ = self.request(reqwest::Method::PUT, &path, Some(body), Self::short()).await;
    }

    pub async fn join(&self, room: &str) -> Result<Value, MatrixError> {
        self.request(reqwest::Method::POST, &format!("/_matrix/client/v3/join/{}", encode(room)), Some(json!({})), Self::short()).await
    }

    pub async fn leave(&self, room: &str) {
        let _ = self.request(reqwest::Method::POST, &format!("/_matrix/client/v3/rooms/{}/leave", encode(room)), Some(json!({})), Self::short()).await;
    }

    /// A direct room with `user`, without encryption.
    pub async fn create_direct(&self, user: &str) -> Result<String, MatrixError> {
        let body = json!({ "is_direct": true, "preset": "private_chat", "invite": [user], "name": "Mightling" });
        let answer = self.request(reqwest::Method::POST, "/_matrix/client/v3/createRoom", Some(body), Self::short()).await?;
        answer.get("room_id").and_then(Value::as_str).map(str::to_string).ok_or(MatrixError { errcode: "M_UNKNOWN".to_string(), error: "no room id".to_string() })
    }

    pub async fn sync(&self, since: Option<&str>) -> Result<Value, MatrixError> {
        let filter = json!({
            "presence": { "not_types": ["*"] },
            "account_data": { "not_types": ["*"] },
            "room": {
                "timeline": { "types": ["m.room.message", "m.reaction", "m.room.encryption", "m.room.member"], "limit": 50 },
                "state": { "types": ["m.room.member", "m.room.encryption"] },
                "ephemeral": { "not_types": ["*"] },
                "account_data": { "not_types": ["*"] },
            },
        });
        let mut path = format!("/_matrix/client/v3/sync?timeout={SYNC_TIMEOUT_MS}&filter={}", encode(&filter.to_string()));
        if let Some(since) = since {
            path.push_str(&format!("&since={}", encode(since)));
        }
        self.request(reqwest::Method::GET, &path, None, Duration::from_millis(SYNC_TIMEOUT_MS + 30_000)).await
    }
}

fn chat_key(room: &str) -> String {
    format!("matrix:{room}")
}

fn room_of(key: &str) -> Option<&str> {
    key.strip_prefix("matrix:")
}

/// Approvals the renderer posted, by event id, so a reaction finds its approval: (id, options).
type Posted = Arc<Mutex<HashMap<String, (u64, usize)>>>;

/// A reply's body without the quoted fallback older clients put above it.
fn without_reply_fallback(body: &str) -> String {
    if !body.starts_with("> ") {
        return body.to_string();
    }
    body.split_once("\n\n").map(|(_, rest)| rest.to_string()).unwrap_or_default()
}

/// The approval a reaction answers: ✅ or 👍 approves (the first option), ❌ or 👎 declines (the last).
fn reaction_choice(key: &str, options: usize) -> Option<usize> {
    let key = key.trim_end_matches('\u{fe0f}');
    match key {
        "✅" | "👍" | "✔" => Some(0),
        "❌" | "👎" => Some(options.saturating_sub(1)),
        _ => None,
    }
}

pub struct Syncer {
    pub api: MatrixApi,
    pub dir: PathBuf,
    pub hub: mpsc::UnboundedSender<Inbound>,
    posted: Posted,
}

impl Syncer {
    async fn ensure_rooms(&self, config: &MatrixConfig, state: &mut MatrixState) {
        let mut changed = false;
        for user in &config.allowed {
            if state.rooms.contains_key(user) {
                continue;
            }
            match self.api.create_direct(user).await {
                Ok(room) => {
                    state.rooms.insert(user.clone(), room);
                    changed = true;
                }
                Err(err) => eprintln!("ling chat: Matrix: could not open a room with {user}: {err}"),
            }
        }
        state.rooms.retain(|user, _| config.allowed.contains(user));
        if changed {
            let _ = state.save(&self.dir);
        }
    }

    /// Syncs until Matrix is turned off (`matrix.json` gone).
    pub async fn run(self) {
        let mut backoff = Duration::from_secs(1);
        loop {
            let Some(config) = MatrixConfig::load(&self.dir) else { return };
            let mut state = MatrixState::load(&self.dir);
            self.ensure_rooms(&config, &mut state).await;
            match self.api.sync(state.next_batch.as_deref()).await {
                Ok(body) => {
                    backoff = Duration::from_secs(1);
                    let first = state.next_batch.is_none();
                    self.handle(&config, &body, first).await;
                    if let Some(next) = body.get("next_batch").and_then(Value::as_str) {
                        let mut state = MatrixState::load(&self.dir);
                        state.next_batch = Some(next.to_string());
                        let _ = state.save(&self.dir);
                    }
                }
                Err(err) => {
                    eprintln!("ling chat: Matrix: {err}; retrying in {}s", backoff.as_secs());
                    tokio::time::sleep(backoff).await;
                    backoff = (backoff * 2).min(Duration::from_secs(300));
                }
            }
        }
    }

    /// One `/sync` answer. On the first sync, old messages are not answered.
    pub async fn handle(&self, config: &MatrixConfig, body: &Value, first: bool) {
        if let Some(invites) = body.pointer("/rooms/invite").and_then(Value::as_object) {
            for (room, data) in invites {
                let inviter = data.pointer("/invite_state/events").and_then(Value::as_array).and_then(|events| {
                    events.iter().find_map(|event| {
                        let invite = event.get("type").and_then(Value::as_str) == Some("m.room.member")
                            && event.get("state_key").and_then(Value::as_str) == Some(config.user_id.as_str())
                            && event.pointer("/content/membership").and_then(Value::as_str) == Some("invite");
                        invite.then(|| event.get("sender").and_then(Value::as_str).unwrap_or_default().to_string())
                    })
                });
                let encrypted = data
                    .pointer("/invite_state/events")
                    .and_then(Value::as_array)
                    .is_some_and(|events| events.iter().any(|e| e.get("type").and_then(Value::as_str) == Some("m.room.encryption")));
                match inviter {
                    Some(inviter) if config.allowed.contains(&inviter) && !encrypted => {
                        if let Err(err) = self.api.join(room).await {
                            eprintln!("ling chat: Matrix: could not join {room}: {err}");
                        }
                    }
                    Some(inviter) if config.allowed.contains(&inviter) => {
                        // Join only to say why, then leave.
                        if self.api.join(room).await.is_ok() {
                            self.refuse_encrypted(room).await;
                        }
                    }
                    _ => self.api.leave(room).await,
                }
            }
        }
        let Some(joined) = body.pointer("/rooms/join").and_then(Value::as_object) else { return };
        for (room, data) in joined {
            let events: Vec<&Value> = ["state", "timeline"]
                .iter()
                .filter_map(|section| data.pointer(&format!("/{section}/events")).and_then(Value::as_array))
                .flatten()
                .collect();
            if events.iter().any(|e| e.get("type").and_then(Value::as_str) == Some("m.room.encryption")) {
                self.refuse_encrypted(room).await;
                continue;
            }
            if data.pointer("/summary/m.joined_member_count").and_then(Value::as_u64).is_some_and(|count| count > 2) {
                let _ = self
                    .api
                    .send(room, json!({ "msgtype": "m.notice", "body": "Mightling talks one to one only, so it is leaving this room." }))
                    .await;
                self.api.leave(room).await;
                continue;
            }
            if first {
                continue;
            }
            let Some(timeline) = data.pointer("/timeline/events").and_then(Value::as_array) else { continue };
            for event in timeline {
                let sender = event.get("sender").and_then(Value::as_str).unwrap_or_default();
                if sender == config.user_id || !config.allowed.iter().any(|user| user == sender) {
                    continue;
                }
                match event.get("type").and_then(Value::as_str) {
                    Some("m.room.message") => {
                        let msgtype = event.pointer("/content/msgtype").and_then(Value::as_str).unwrap_or_default();
                        let body = event.pointer("/content/body").and_then(Value::as_str).unwrap_or_default();
                        if msgtype == "m.text" {
                            let text = without_reply_fallback(body);
                            let _ = self.hub.send(Inbound::Text { chat: chat_key(room), text });
                        } else {
                            let _ = self.api.send(room, json!({ "msgtype": "m.notice", "body": "Only text messages for now." })).await;
                        }
                    }
                    Some("m.reaction") => {
                        let target = event.pointer("/content/m.relates_to/event_id").and_then(Value::as_str).unwrap_or_default();
                        let key = event.pointer("/content/m.relates_to/key").and_then(Value::as_str).unwrap_or_default();
                        let found = self.posted.lock().await.get(target).copied();
                        if let Some((approval, options)) = found
                            && let Some(choice) = reaction_choice(key, options)
                        {
                            let _ = self.hub.send(Inbound::Answer { chat: chat_key(room), approval, choice });
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    async fn refuse_encrypted(&self, room: &str) {
        let _ = self
            .api
            .send(
                room,
                json!({ "msgtype": "m.notice", "body": "This room is encrypted, and Mightling does not read encrypted rooms. Use the direct chat Mightling opened with you." }),
            )
            .await;
        self.api.leave(room).await;
    }
}

#[derive(Default)]
struct Typing {
    last: HashMap<String, Instant>,
}

pub struct Renderer {
    pub api: MatrixApi,
    pub user_id: String,
    posted: Posted,
    typing: Mutex<Typing>,
}

impl Renderer {
    pub async fn run(self, mut outbound: mpsc::UnboundedReceiver<Outbound>) {
        while let Some(out) = outbound.recv().await {
            self.render(out).await;
        }
    }

    fn formatted(markdown: &str, msgtype: &str) -> Value {
        json!({ "msgtype": msgtype, "body": markdown, "format": "org.matrix.custom.html", "formatted_body": render::to_html(markdown) })
    }

    pub async fn render(&self, out: Outbound) {
        let Some(room) = room_of(out.chat()).map(str::to_string) else { return };
        match out {
            Outbound::Draft { .. } => {
                let mut typing = self.typing.lock().await;
                if typing.last.get(&room).is_none_or(|at| at.elapsed() >= TYPING_RENEW) {
                    typing.last.insert(room.clone(), Instant::now());
                    self.api.typing(&room, &self.user_id, true).await;
                }
            }
            Outbound::TurnEnded { .. } => {
                self.typing.lock().await.last.remove(&room);
                self.api.typing(&room, &self.user_id, false).await;
            }
            Outbound::Message { markdown, .. } => {
                for part in render::split(&markdown, MESSAGE_LIMIT) {
                    if let Err(err) = self.api.send(&room, Self::formatted(&part, "m.text")).await {
                        eprintln!("ling chat: Matrix refused an answer: {err}");
                    }
                }
            }
            Outbound::Notice { text, .. } => {
                let _ = self.api.send(&room, json!({ "msgtype": "m.notice", "body": text })).await;
            }
            Outbound::Approval { id, text, options, .. } => {
                let mut body = text;
                if options.len() == 2 {
                    body.push_str(&format!("\n\nReact ✅ to {} or ❌ to decline, or reply yes or no.", options[0].to_lowercase()));
                } else {
                    let list: Vec<String> = options.iter().enumerate().map(|(n, o)| format!("{}. {o}", n + 1)).collect();
                    body.push_str(&format!("\n\nReply with a number:\n{}", list.join("\n")));
                }
                if let Ok(event) = self.api.send(&room, Self::formatted(&body, "m.text")).await {
                    self.posted.lock().await.insert(event, (id, options.len()));
                }
            }
            Outbound::ApprovalClosed { id, outcome, .. } => {
                self.posted.lock().await.retain(|_, (approval, _)| *approval != id);
                let _ = self.api.send(&room, json!({ "msgtype": "m.notice", "body": outcome })).await;
            }
        }
    }
}

/// Starts both halves. Fails when the homeserver does not know the bot's token (`whoami`).
pub async fn start(
    config: &MatrixConfig,
    dir: PathBuf,
    hub: mpsc::UnboundedSender<Inbound>,
    outbound: mpsc::UnboundedReceiver<Outbound>,
) -> Result<(), MatrixError> {
    let api = MatrixApi::new(&config.homeserver, &config.access_token);
    api.request(reqwest::Method::GET, "/_matrix/client/v3/account/whoami", None, Duration::from_secs(30)).await?;
    let posted: Posted = Arc::default();
    let renderer = Renderer { api: api.clone(), user_id: config.user_id.clone(), posted: posted.clone(), typing: Mutex::default() };
    tokio::spawn(renderer.run(outbound));
    tokio::spawn(Syncer { api, dir, hub, posted }.run());
    Ok(())
}

/// For tests: a syncer and a renderer sharing their approval map.
pub fn pair_for_tests(api: MatrixApi, dir: PathBuf, user_id: &str, hub: mpsc::UnboundedSender<Inbound>) -> (Syncer, Renderer) {
    let posted: Posted = Arc::default();
    let renderer = Renderer { api: api.clone(), user_id: user_id.to_string(), posted: posted.clone(), typing: Mutex::default() };
    (Syncer { api, dir, hub, posted }, renderer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_segments_are_encoded() {
        assert_eq!(encode("!abc:host.ts.net"), "%21abc%3Ahost.ts.net");
        assert_eq!(encode("@owner:x"), "%40owner%3Ax");
    }

    #[test]
    fn reply_fallbacks_are_dropped() {
        assert_eq!(without_reply_fallback("> <@a:b> earlier\n> more\n\nyes"), "yes");
        assert_eq!(without_reply_fallback("plain"), "plain");
    }

    #[test]
    fn reactions_map_to_approve_and_decline() {
        assert_eq!(reaction_choice("✅", 2), Some(0));
        assert_eq!(reaction_choice("👍\u{fe0f}", 2), Some(0));
        assert_eq!(reaction_choice("❌", 3), Some(2));
        assert_eq!(reaction_choice("🎉", 2), None);
    }
}
