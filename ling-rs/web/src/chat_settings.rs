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
//! night_repos = ["~/code/app"]  # where /night add may queue, besides every repository already queued in
//! night_report_to = ["signal", "matrix"]  # which messengers get the morning report; [] for none
//! sync_notices_to = ["signal", "matrix"]  # which get sync's notices (a turn finished, an approval waits); [] for none
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
    /// Repositories `/night add` may queue in, besides those already queued in (night_phone.rs).
    pub night_repos: Vec<String>,
    /// Which messengers get Night Shift's morning report: Signal and Matrix unless the user says;
    /// Telegram only when named, because the report's repository and task names would then be on
    /// Telegram's servers (MOONSHOT §3.5).
    pub night_report_to: Vec<String>,
    /// Which messengers pass on sync's notices (specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.3): a turn
    /// on another device finished, or an approval waits there. The same default as the report, for
    /// the same reason: a notice names the thread's title.
    pub sync_notices_to: Vec<String>,
}

impl Default for ChatSettings {
    fn default() -> ChatSettings {
        ChatSettings {
            new_thread_after: Some(DEFAULT_NEW_THREAD_AFTER),
            answer_budget: Some(DEFAULT_ANSWER_BUDGET),
            tool_calls: Some(DEFAULT_TOOL_CALLS),
            command_timeout: Some(DEFAULT_COMMAND_TIMEOUT),
            night_repos: Vec::new(),
            night_report_to: vec!["signal".to_string(), "matrix".to_string()],
            sync_notices_to: vec!["signal".to_string(), "matrix".to_string()],
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
        let strings = |key: &str| -> Option<Vec<String>> {
            chat.get(key)?.as_array().map(|items| items.iter().filter_map(toml::Value::as_str).map(str::to_string).collect())
        };
        if let Some(repos) = strings("night_repos") {
            settings.night_repos = repos;
        }
        if let Some(to) = strings("night_report_to") {
            settings.night_report_to = to.into_iter().map(|m| m.to_ascii_lowercase()).collect();
        }
        if let Some(to) = strings("sync_notices_to") {
            settings.sync_notices_to = to.into_iter().map(|m| m.to_ascii_lowercase()).collect();
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
            "nightReportTo": self.night_report_to,
            "syncNoticesTo": self.sync_notices_to,
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
        if let Some(to) = value.get("nightReportTo").and_then(Value::as_array) {
            settings.night_report_to = to.iter().filter_map(Value::as_str).map(str::to_string).collect();
        }
        if let Some(to) = value.get("syncNoticesTo").and_then(Value::as_array) {
            settings.sync_notices_to = to.iter().filter_map(Value::as_str).map(str::to_string).collect();
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
        let night = ChatSettings::from_toml("[chat]\nnight_repos = [\"~/code/app\"]\nnight_report_to = [\"Telegram\", \"signal\"]\n");
        assert_eq!(night.night_repos, ["~/code/app"]);
        assert_eq!(night.night_report_to, ["telegram", "signal"]);
        assert_eq!(ChatSettings::default().night_report_to, ["signal", "matrix"], "Telegram only when named");
        assert_eq!(ChatSettings::default().sync_notices_to, ["signal", "matrix"], "Telegram only when named");
        assert_eq!(ChatSettings::from_toml("[chat]\nsync_notices_to = [\"Telegram\"]\n").sync_notices_to, ["telegram"]);
        let settings = ChatSettings::from_toml("[chat]\nsync_notices_to = []\n");
        assert!(ChatSettings::from_json(&settings.to_json()).sync_notices_to.is_empty());
    }

    #[test]
    fn the_settings_survive_the_trip_through_ling_web() {
        let off = ChatSettings { new_thread_after: None, answer_budget: None, tool_calls: None, command_timeout: None, night_report_to: vec![], ..ChatSettings::default() };
        let short = ChatSettings {
            new_thread_after: Some(Duration::from_secs(60)),
            answer_budget: Some(Duration::from_secs(30)),
            tool_calls: Some(3),
            command_timeout: Some(Duration::from_secs(5)),
            night_report_to: vec!["telegram".to_string()],
            ..ChatSettings::default()
        };
        for settings in [ChatSettings::default(), off, short] {
            assert_eq!(ChatSettings::from_json(&settings.to_json()), settings);
        }
        assert_eq!(ChatSettings::from_json(&json!({})), ChatSettings::default());
    }
}
