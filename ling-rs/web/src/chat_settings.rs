//! How the phone bridges behave (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16), from the `[chat]`
//! table of the user's own configuration, `~/.config/dreamference/config.toml`: never a
//! repository's file, which a clone or the agent could write.
//!
//! ```toml
//! [chat]
//! new_thread_after_hours = 4   # a message after this long a silence starts a new thread; 0: never
//! answer_seconds = 180         # an answer's time, then the agent is told to answer now; 0: no limit
//! tool_calls = 25              # an answer's tool calls, the same; 0: no limit
//! command_seconds = 60         # one command's time in a chat session, the same; 0: no limit
//! telegram_mini_app_url = "https://…"  # ling web over HTTPS on your own network, as Telegram's app (telegram_app.rs)
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
/// An answer's time before the agent is told to answer with what it has.
pub const DEFAULT_ANSWER_BUDGET: Duration = Duration::from_secs(180);
/// An answer's tool calls before the same.
pub const DEFAULT_TOOL_CALLS: u32 = 25;
/// One command's time in a chat session before the same.
pub const DEFAULT_COMMAND_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatSettings {
    /// None: a conversation never moves to a new thread on its own.
    pub new_thread_after: Option<Duration>,
    /// None: no limit on an answer's time.
    pub answer_budget: Option<Duration>,
    /// None: no limit on an answer's tool calls.
    pub tool_calls: Option<u32>,
    /// None: no limit on one command's time.
    pub command_timeout: Option<Duration>,
}

impl Default for ChatSettings {
    fn default() -> ChatSettings {
        ChatSettings {
            new_thread_after: Some(DEFAULT_NEW_THREAD_AFTER),
            answer_budget: Some(DEFAULT_ANSWER_BUDGET),
            tool_calls: Some(DEFAULT_TOOL_CALLS),
            command_timeout: Some(DEFAULT_COMMAND_TIMEOUT),
        }
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
        if let Some(budget) = seconds(chat, "answer_seconds", 1.0) {
            settings.answer_budget = budget;
        }
        if let Some(timeout) = seconds(chat, "command_seconds", 1.0) {
            settings.command_timeout = timeout;
        }
        if let Some(calls) = chat.get("tool_calls").and_then(toml::Value::as_integer).filter(|n| (0..=10_000).contains(n)) {
            settings.tool_calls = (calls > 0).then_some(calls as u32);
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
        json!({
            "newThreadAfterSeconds": self.new_thread_after.map_or(0, |gap| gap.as_secs()),
            "answerSeconds": self.answer_budget.map_or(0, |budget| budget.as_secs()),
            "toolCalls": self.tool_calls.unwrap_or(0),
            "commandSeconds": self.command_timeout.map_or(0, |timeout| timeout.as_secs()),
        })
    }

    /// From `/api/chat/settings`; a field that is missing (an older `ling web`) is the default.
    pub fn from_json(value: &Value) -> ChatSettings {
        let mut settings = ChatSettings::default();
        let duration = |key: &str| value.get(key).and_then(Value::as_u64).map(|n| (n > 0).then(|| Duration::from_secs(n)));
        if let Some(gap) = duration("newThreadAfterSeconds") {
            settings.new_thread_after = gap;
        }
        if let Some(budget) = duration("answerSeconds") {
            settings.answer_budget = budget;
        }
        if let Some(timeout) = duration("commandSeconds") {
            settings.command_timeout = timeout;
        }
        if let Some(calls) = value.get("toolCalls").and_then(Value::as_u64) {
            settings.tool_calls = (calls > 0).then(|| calls.min(10_000) as u32);
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
        let bounds = ChatSettings::from_toml("[chat]\nanswer_seconds = 120\ntool_calls = 10\ncommand_seconds = 0\n");
        assert_eq!(bounds.answer_budget, Some(Duration::from_secs(120)));
        assert_eq!(bounds.tool_calls, Some(10));
        assert_eq!(bounds.command_timeout, None);
        assert_eq!(ChatSettings::from_toml("[chat]\ntool_calls = 0\n").tool_calls, None);
    }

    #[test]
    fn the_settings_survive_the_trip_through_ling_web() {
        let off = ChatSettings { new_thread_after: None, answer_budget: None, tool_calls: None, command_timeout: None };
        let short = ChatSettings { new_thread_after: Some(Duration::from_secs(60)), answer_budget: Some(Duration::from_secs(30)), tool_calls: Some(3), command_timeout: Some(Duration::from_secs(5)) };
        for settings in [ChatSettings::default(), off, short] {
            assert_eq!(ChatSettings::from_json(&settings.to_json()), settings);
        }
        assert_eq!(ChatSettings::from_json(&json!({})), ChatSettings::default());
    }
}
