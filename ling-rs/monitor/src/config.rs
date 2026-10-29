//! The monitor's settings: `[monitor]`, `[monitor.waits]` and `[monitor.rules]` of the user's
//! config (`~/.config/dreamference/config.toml`), never a repository's. The owner's rules (the commit
//! identity, the forbidden trailers) live there only; with none set, check C5(a) is off.

use std::path::Path;
use std::path::PathBuf;

/// Expected durations of the waits check C2 compares against (§0.1, question 4), in minutes.
#[derive(Debug, Clone, PartialEq)]
pub struct Waits {
    pub ci: i64,
    pub shell: i64,
    pub subagent: i64,
    /// Any other tool call left without a result.
    pub tool: i64,
    /// The hour (UTC) a Night Shift or benchmark window ends.
    pub night_end_hour_utc: u32,
}

/// Everything a check needs to know about where it runs. Tests build one over a temporary folder.
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// The user's home folder, for C5(c)'s watch over `~/.local/bin` and the home's own entries.
    pub home: PathBuf,
    /// Claude Code's transcripts: `<projects>/<project>/<session>.jsonl` and `.../subagents/`.
    pub projects_dir: PathBuf,
    /// Where the monitor keeps its state and writes `summary.md` and `summary.json`.
    pub state_dir: PathBuf,
    /// The command an alert is sent through, with the message as its one argument.
    pub notify_command: Option<String>,
    pub warn_context: u64,
    pub handoff_context: u64,
    pub waits: Waits,
    /// Quiet hours, local time: only urgent alerts are sent from `quiet_start` to `quiet_end`.
    pub quiet_start: u32,
    pub quiet_end: u32,
    pub max_alerts_per_hour: usize,
    /// C5(a): the `Name <email>` every commit must carry, when set.
    pub commit_author: Option<String>,
    /// C5(a): trailer prefixes no commit may carry (compared without case).
    pub forbidden_trailers: Vec<String>,
    /// C5(a): repositories to watch; empty means those the transcripts' `cwd` names.
    pub repos: Vec<PathBuf>,
}

impl Config {
    /// The defaults of §7.3 under a home folder.
    pub fn defaults(home: &Path) -> Config {
        Config {
            home: home.to_path_buf(),
            projects_dir: home.join(".claude/projects"),
            state_dir: home.join(".local/share/dreamference/monitor"),
            notify_command: None,
            warn_context: 250_000,
            handoff_context: 300_000,
            waits: Waits { ci: 30, shell: 15, subagent: 60, tool: 15, night_end_hour_utc: 7 },
            quiet_start: 23,
            quiet_end: 7,
            max_alerts_per_hour: 6,
            commit_author: None,
            forbidden_trailers: Vec::new(),
            repos: Vec::new(),
        }
    }

    /// The defaults with the user's `[monitor]` tables applied. A missing or broken file leaves the
    /// defaults: the monitor must run on a machine with no config at all.
    pub fn load(home: &Path, config_file: &Path) -> Config {
        let mut config = Config::defaults(home);
        let Ok(text) = std::fs::read_to_string(config_file) else { return config };
        let Ok(table) = text.parse::<toml::Table>() else { return config };
        let Some(monitor) = table.get("monitor").and_then(toml::Value::as_table) else { return config };
        let expand = |text: &str| match text.strip_prefix("~/") {
            Some(rest) => home.join(rest),
            None => PathBuf::from(text),
        };
        let int = |t: &toml::Table, key: &str| t.get(key).and_then(toml::Value::as_integer);
        if let Some(dir) = monitor.get("state_dir").and_then(toml::Value::as_str) {
            config.state_dir = expand(dir);
        }
        if let Some(dir) = monitor.get("projects_dir").and_then(toml::Value::as_str) {
            config.projects_dir = expand(dir);
        }
        if let Some(command) = monitor.get("notify_command").and_then(toml::Value::as_str) {
            let command = command.trim();
            config.notify_command = (!command.is_empty()).then(|| expand(command).to_string_lossy().into_owned());
        }
        if let Some(value) = int(monitor, "warn_context") {
            config.warn_context = value.max(0) as u64;
        }
        if let Some(value) = int(monitor, "handoff_context") {
            config.handoff_context = value.max(0) as u64;
        }
        if let Some(value) = int(monitor, "quiet_start") {
            config.quiet_start = value.clamp(0, 23) as u32;
        }
        if let Some(value) = int(monitor, "quiet_end") {
            config.quiet_end = value.clamp(0, 23) as u32;
        }
        if let Some(value) = int(monitor, "max_alerts_per_hour") {
            config.max_alerts_per_hour = value.max(1) as usize;
        }
        if let Some(waits) = monitor.get("waits").and_then(toml::Value::as_table) {
            let minutes = |key: &str, default: i64| int(waits, key).filter(|m| *m > 0).unwrap_or(default);
            config.waits.ci = minutes("ci", config.waits.ci);
            config.waits.shell = minutes("shell", config.waits.shell);
            config.waits.subagent = minutes("subagent", config.waits.subagent);
            config.waits.tool = minutes("tool", config.waits.tool);
            if let Some(hour) = int(waits, "night_end_hour_utc") {
                config.waits.night_end_hour_utc = hour.clamp(0, 23) as u32;
            }
        }
        if let Some(rules) = monitor.get("rules").and_then(toml::Value::as_table) {
            config.commit_author =
                rules.get("commit_author").and_then(toml::Value::as_str).map(str::trim).filter(|a| !a.is_empty()).map(str::to_string);
            config.forbidden_trailers = strings(rules.get("forbidden_trailers"));
            config.repos = strings(rules.get("repos")).iter().map(|r| expand(r)).collect();
        }
        config
    }

    /// Whether a local hour falls in the quiet hours (which may wrap past midnight).
    pub fn is_quiet(&self, local_hour: u32) -> bool {
        if self.quiet_start == self.quiet_end {
            false
        } else if self.quiet_start < self.quiet_end {
            (self.quiet_start..self.quiet_end).contains(&local_hour)
        } else {
            local_hour >= self.quiet_start || local_hour < self.quiet_end
        }
    }
}

fn strings(value: Option<&toml::Value>) -> Vec<String> {
    value
        .and_then(toml::Value::as_array)
        .map(|items| items.iter().filter_map(toml::Value::as_str).map(str::to_string).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_users_tables_override_the_defaults() {
        let dir = std::env::temp_dir().join(format!("ling-monitor-config-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("config.toml");
        std::fs::write(
            &file,
            "[chat]\nx = 1\n[monitor]\nnotify_command = \"~/bin/notify\"\nquiet_start = 22\n[monitor.waits]\nci = 45\n\
             [monitor.rules]\ncommit_author = \"someone <someone@example.invalid>\"\nforbidden_trailers = [\"Made-By:\"]\n",
        )
        .unwrap();
        let config = Config::load(Path::new("/home/u"), &file);
        assert_eq!(config.notify_command.as_deref(), Some("/home/u/bin/notify"));
        assert_eq!((config.quiet_start, config.waits.ci, config.waits.shell), (22, 45, 15));
        assert_eq!(config.commit_author.as_deref(), Some("someone <someone@example.invalid>"));
        assert_eq!(config.forbidden_trailers, vec!["Made-By:".to_string()]);
        assert_eq!(config.state_dir, PathBuf::from("/home/u/.local/share/dreamference/monitor"));
        assert_eq!(Config::load(Path::new("/home/u"), &dir.join("missing.toml")), Config::defaults(Path::new("/home/u")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn quiet_hours_wrap_past_midnight() {
        let config = Config::defaults(Path::new("/home/u"));
        assert!(config.is_quiet(23) && config.is_quiet(0) && config.is_quiet(6));
        assert!(!config.is_quiet(7) && !config.is_quiet(22) && !config.is_quiet(12));
    }

    #[test]
    fn the_defaults_are_those_of_the_spec_and_no_rules_are_set() {
        let config = Config::defaults(Path::new("/home/u"));
        assert_eq!(
            (config.warn_context, config.handoff_context),
            (250_000, 300_000)
        );
        assert_eq!(
            (config.waits.ci, config.waits.shell, config.waits.subagent),
            (30, 15, 60)
        );
        assert_eq!(
            (
                config.quiet_start,
                config.quiet_end,
                config.max_alerts_per_hour
            ),
            (23, 7, 6)
        );
        assert_eq!(
            config.projects_dir,
            PathBuf::from("/home/u/.claude/projects")
        );
        assert_eq!(
            config.state_dir,
            PathBuf::from("/home/u/.local/share/dreamference/monitor")
        );
        // No rules in the build: C5(a) is off until the user sets them.
        assert!(
            config.commit_author.is_none()
                && config.forbidden_trailers.is_empty()
                && config.repos.is_empty()
        );
        assert!(config.notify_command.is_none());
    }
}
