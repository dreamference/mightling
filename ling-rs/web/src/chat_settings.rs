//! How the phone bridges behave (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16), from the `[chat]`
//! table of the user's own configuration, `~/.config/dreamference/config.toml`: never a
//! repository's file, which a clone or the agent could write.
//!
//! ```toml
//! [chat]
//! new_thread_after_hours = 4   # a message after this long a silence starts a new thread; 0: never
//! ```
//!
//! `ling chat` reads the file itself; the Signal bridge, whose system account cannot read the home
//! folder, asks `ling web` (`/api/chat/settings`). A missing file, table or key, or a value of the
//! wrong kind, is the default.

use std::path::Path;
use std::time::Duration;

use serde_json::Value;
use serde_json::json;

/// A message after this long a silence starts a new thread.
pub const DEFAULT_NEW_THREAD_AFTER: Duration = Duration::from_secs(4 * 3600);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatSettings {
    /// None: a conversation never moves to a new thread on its own.
    pub new_thread_after: Option<Duration>,
}

impl Default for ChatSettings {
    fn default() -> ChatSettings {
        ChatSettings { new_thread_after: Some(DEFAULT_NEW_THREAD_AFTER) }
    }
}

fn seconds(table: &toml::Table, key: &str, unit: f64) -> Option<Option<Duration>> {
    let value = table.get(key)?;
    let number = value.as_float().or_else(|| value.as_integer().map(|n| n as f64))?;
    if !number.is_finite() || number < 0.0 {
        return None;
    }
    Some((number > 0.0).then(|| Duration::from_secs_f64(number * unit)))
}

impl ChatSettings {
    /// The settings in a configuration file's text.
    pub fn from_toml(text: &str) -> ChatSettings {
        let mut settings = ChatSettings::default();
        let Ok(document) = toml::from_str::<toml::Table>(text) else { return settings };
        let Some(chat) = document.get("chat").and_then(toml::Value::as_table) else { return settings };
        if let Some(gap) = seconds(chat, "new_thread_after_hours", 3600.0) {
            settings.new_thread_after = gap;
        }
        settings
    }

    /// The user's settings, from `path` (`user_file()` in use).
    pub fn load_from(path: Option<&Path>) -> ChatSettings {
        path.and_then(|path| std::fs::read_to_string(path).ok()).map(|text| ChatSettings::from_toml(&text)).unwrap_or_default()
    }

    /// The user's settings.
    pub fn load() -> ChatSettings {
        ChatSettings::load_from(ling_airgapped::user_config_file().as_deref())
    }

    /// As `/api/chat/settings` answers them: seconds, 0 for never.
    pub fn to_json(&self) -> Value {
        json!({ "newThreadAfterSeconds": self.new_thread_after.map_or(0, |gap| gap.as_secs()) })
    }

    /// From `/api/chat/settings`; a field that is missing (an older `ling web`) is the default.
    pub fn from_json(value: &Value) -> ChatSettings {
        let mut settings = ChatSettings::default();
        if let Some(gap) = value.get("newThreadAfterSeconds").and_then(Value::as_u64) {
            settings.new_thread_after = (gap > 0).then(|| Duration::from_secs(gap));
        }
        settings
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_chat_table_sets_the_gap_and_anything_else_is_the_default() {
        assert_eq!(ChatSettings::from_toml(""), ChatSettings::default());
        assert_eq!(ChatSettings::from_toml("[chat]\nnew_thread_after_hours = 2\n").new_thread_after, Some(Duration::from_secs(7200)));
        assert_eq!(ChatSettings::from_toml("[chat]\nnew_thread_after_hours = 0.5\n").new_thread_after, Some(Duration::from_secs(1800)));
        assert_eq!(ChatSettings::from_toml("[chat]\nnew_thread_after_hours = 0\n").new_thread_after, None);
        assert_eq!(ChatSettings::from_toml("[chat]\nnew_thread_after_hours = \"soon\"\n"), ChatSettings::default());
        assert_eq!(ChatSettings::from_toml("[chat]\nnew_thread_after_hours = -1\n"), ChatSettings::default());
        assert_eq!(ChatSettings::from_toml("not toml ["), ChatSettings::default());
        assert_eq!(ChatSettings::from_toml("new_thread_after_hours = 1\n"), ChatSettings::default(), "only the [chat] table");
    }

    #[test]
    fn the_settings_survive_the_trip_through_ling_web() {
        for settings in [ChatSettings::default(), ChatSettings { new_thread_after: None }, ChatSettings { new_thread_after: Some(Duration::from_secs(60)) }] {
            assert_eq!(ChatSettings::from_json(&settings.to_json()), settings);
        }
        assert_eq!(ChatSettings::from_json(&json!({})), ChatSettings::default());
    }
}
