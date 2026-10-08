//! What the bridge keeps (MIGHTLING_CHAT §6), in `~/.mightling/chat/` (0700):
//!
//! - `telegram.json`: the bot token, when the user consented, and the paired Telegram user ids;
//! - `matrix.json`: written by `ling-admin matrix` (the homeserver, the bot's account and
//!   token, the allow-list); the bridge only reads it;
//! - `matrix-state.json`: the bridge's own Matrix state (`next_batch`, the room per user);
//! - `threads.json`: which thread each chat is on, and its recent threads;
//! - `pairing/`: pending Telegram pairing codes, under their SHA-256 (the web crate's functions).
//!
//! Every file is replaced whole through a temporary file (`write_private`). Everything here is
//! readable by a command the agent runs; §6 says what that means for each.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;
use serde_json::json;

pub use ling_web_server::auth::private_dir;
pub use ling_web_server::auth::write_private;

/// The folder: `<codex home>/chat`.
pub fn chat_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("chat")
}

fn read_json(path: &Path) -> Value {
    std::fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or(Value::Null)
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// `telegram.json`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct TelegramConfig {
    pub token: String,
    /// When the user typed `yes` to the privacy warning (seconds since the epoch).
    pub consented: u64,
    /// Paired Telegram user ids.
    pub users: Vec<i64>,
}

impl TelegramConfig {
    pub fn path(dir: &Path) -> PathBuf {
        dir.join("telegram.json")
    }

    /// The configuration, when Telegram is set up.
    pub fn load(dir: &Path) -> Option<TelegramConfig> {
        let value = read_json(&Self::path(dir));
        let token = value.get("token")?.as_str()?.to_string();
        if token.is_empty() {
            return None;
        }
        Some(TelegramConfig {
            token,
            consented: value.get("consented").and_then(Value::as_u64).unwrap_or(0),
            users: value.get("users").and_then(Value::as_array).map(|users| users.iter().filter_map(Value::as_i64).collect()).unwrap_or_default(),
        })
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        let text = json!({ "token": self.token, "consented": self.consented, "users": self.users });
        write_private(&Self::path(dir), &serde_json::to_string_pretty(&text).unwrap_or_default())
    }

    pub fn new(token: String) -> TelegramConfig {
        TelegramConfig { token, consented: now_secs(), users: Vec::new() }
    }
}

/// `matrix.json`, as `ling-admin matrix` writes it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct MatrixConfig {
    /// Where the bridge reaches the homeserver: the loopback proxy, `http://127.0.0.1:6167`.
    pub homeserver: String,
    pub server_name: String,
    /// The bot's account, `@mightling:<server name>`.
    pub user_id: String,
    pub access_token: String,
    /// The users who may talk to the bot.
    pub allowed: Vec<String>,
}

impl MatrixConfig {
    pub fn path(dir: &Path) -> PathBuf {
        dir.join("matrix.json")
    }

    pub fn load(dir: &Path) -> Option<MatrixConfig> {
        let value = read_json(&Self::path(dir));
        let field = |name: &str| value.get(name).and_then(Value::as_str).unwrap_or_default().to_string();
        let config = MatrixConfig {
            homeserver: field("homeserver"),
            server_name: field("server_name"),
            user_id: field("user_id"),
            access_token: field("access_token"),
            allowed: value
                .get("allowed")
                .and_then(Value::as_array)
                .map(|users| users.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default(),
        };
        (!config.homeserver.is_empty() && !config.access_token.is_empty() && !config.user_id.is_empty()).then_some(config)
    }
}

/// `matrix-state.json`: the bridge's own Matrix state.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct MatrixState {
    pub next_batch: Option<String>,
    /// The direct room the bridge opened for each allowed user.
    pub rooms: BTreeMap<String, String>,
}

impl MatrixState {
    pub fn path(dir: &Path) -> PathBuf {
        dir.join("matrix-state.json")
    }

    pub fn load(dir: &Path) -> MatrixState {
        let value = read_json(&Self::path(dir));
        MatrixState {
            next_batch: value.get("next_batch").and_then(Value::as_str).map(str::to_string),
            rooms: value
                .get("rooms")
                .and_then(Value::as_object)
                .map(|rooms| rooms.iter().filter_map(|(user, room)| Some((user.clone(), room.as_str()?.to_string()))).collect())
                .unwrap_or_default(),
        }
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        write_private(&Self::path(dir), &json!({ "next_batch": self.next_batch, "rooms": self.rooms }).to_string())
    }
}

/// A thread a chat started.
#[derive(Clone, Debug, PartialEq)]
pub struct ThreadRef {
    pub id: String,
    pub name: String,
}

/// One chat's threads.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ChatThreads {
    pub current: Option<String>,
    /// Newest first.
    pub recent: Vec<ThreadRef>,
}

/// `threads.json`: chat key (`telegram:<chat id>`, `matrix:<room id>`) → its threads.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Threads {
    pub chats: BTreeMap<String, ChatThreads>,
}

impl Threads {
    pub fn path(dir: &Path) -> PathBuf {
        dir.join("threads.json")
    }

    pub fn load(dir: &Path) -> Threads {
        let value = read_json(&Self::path(dir));
        let chats = value
            .get("chats")
            .and_then(Value::as_object)
            .map(|chats| {
                chats
                    .iter()
                    .map(|(key, chat)| {
                        let recent = chat
                            .get("recent")
                            .and_then(Value::as_array)
                            .map(|list| {
                                list.iter()
                                    .filter_map(|entry| {
                                        Some(ThreadRef {
                                            id: entry.get("id")?.as_str()?.to_string(),
                                            name: entry.get("name").and_then(Value::as_str).unwrap_or_default().to_string(),
                                        })
                                    })
                                    .collect()
                            })
                            .unwrap_or_default();
                        let current = chat.get("current").and_then(Value::as_str).map(str::to_string);
                        (key.clone(), ChatThreads { current, recent })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Threads { chats }
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        let chats: serde_json::Map<String, Value> = self
            .chats
            .iter()
            .map(|(key, chat)| {
                let recent: Vec<Value> = chat.recent.iter().map(|t| json!({ "id": t.id, "name": t.name })).collect();
                (key.clone(), json!({ "current": chat.current, "recent": recent }))
            })
            .collect();
        write_private(&Self::path(dir), &serde_json::to_string_pretty(&json!({ "chats": chats })).unwrap_or_default())
    }

    /// Records a new thread as the chat's current one, keeping the newest `keep`.
    pub fn started(&mut self, chat: &str, thread: ThreadRef, keep: usize) {
        let entry = self.chats.entry(chat.to_string()).or_default();
        entry.current = Some(thread.id.clone());
        entry.recent.retain(|t| t.id != thread.id);
        entry.recent.insert(0, thread);
        entry.recent.truncate(keep);
    }

    /// The chat that started `thread`, if any.
    pub fn chat_of(&self, thread: &str) -> Option<&str> {
        self.chats.iter().find(|(_, chat)| chat.recent.iter().any(|t| t.id == thread)).map(|(key, _)| key.as_str())
    }
}

/// Pending Telegram pairing codes live in `<chat dir>/pairing/`, under their hash, ten minutes, one
/// use: the web crate's own functions, pointed at this folder.
pub fn issue_pairing_code(dir: &Path) -> std::io::Result<String> {
    ling_web_server::auth::issue_pairing_code(dir)
}

pub fn take_pairing_code(dir: &Path, code: &str) -> bool {
    ling_web_server::auth::take_pairing_code(dir, code)
}

pub fn withdraw_pairing_codes(dir: &Path) {
    ling_web_server::auth::withdraw_pairing_codes(dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-chat-store-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn telegram_round_trips_privately() {
        let dir = scratch("telegram");
        assert_eq!(TelegramConfig::load(&dir), None);
        let mut config = TelegramConfig::new("123:abc".to_string());
        config.users.push(42);
        config.save(&dir).unwrap();
        assert_eq!(TelegramConfig::load(&dir), Some(config));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(TelegramConfig::path(&dir)).unwrap().permissions().mode() & 0o777, 0o600);
            assert_eq!(std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
        }
    }

    #[test]
    fn threads_keep_the_newest_and_know_their_chat() {
        let dir = scratch("threads");
        let mut threads = Threads::load(&dir);
        for n in 0..12 {
            threads.started("telegram:7", ThreadRef { id: format!("t{n}"), name: format!("q{n}") }, 10);
        }
        threads.save(&dir).unwrap();
        let threads = Threads::load(&dir);
        let chat = &threads.chats["telegram:7"];
        assert_eq!(chat.current.as_deref(), Some("t11"));
        assert_eq!(chat.recent.len(), 10);
        assert_eq!(chat.recent[0].id, "t11");
        assert_eq!(threads.chat_of("t5"), Some("telegram:7"));
        assert_eq!(threads.chat_of("t0"), None);
    }

    #[test]
    fn pairing_codes_are_one_use_and_kept_as_hashes() {
        let dir = scratch("pairing");
        let code = issue_pairing_code(&dir).unwrap();
        let listing: Vec<String> =
            std::fs::read_dir(dir.join("pairing")).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        assert!(listing.iter().all(|name| !name.contains(&code)));
        assert!(take_pairing_code(&dir, &code));
        assert!(!take_pairing_code(&dir, &code));
    }

    #[test]
    fn matrix_needs_a_homeserver_a_user_and_a_token() {
        let dir = scratch("matrix");
        write_private(&MatrixConfig::path(&dir), r#"{"homeserver":"http://127.0.0.1:6167","user_id":"@mightling:x","access_token":"t","allowed":["@owner:x"]}"#).unwrap();
        let config = MatrixConfig::load(&dir).unwrap();
        assert_eq!(config.allowed, vec!["@owner:x".to_string()]);
        write_private(&MatrixConfig::path(&dir), r#"{"homeserver":"http://127.0.0.1:6167"}"#).unwrap();
        assert_eq!(MatrixConfig::load(&dir), None);
        let mut state = MatrixState { next_batch: Some("s1".to_string()), ..Default::default() };
        state.rooms.insert("@owner:x".to_string(), "!r:x".to_string());
        state.save(&dir).unwrap();
        assert_eq!(MatrixState::load(&dir), state);
    }
}
