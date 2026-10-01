//! How much of the internet a `puffin` session may use (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md).
//!
//! Three levels: `off` (everything, the default), `duckduckgo` (search through DuckDuckGo only, a
//! preference the web commands follow) and `on` (no network for anything the agent runs, enforced
//! by the command sandbox). Resolved before every command, first match wins:
//!
//! 1. the session's file, `$CODEX_HOME/airgapped/<id>`, written by `/airgapped <level>`;
//! 2. `DREAMFERENCE_PUFFIN_AIRGAPPED`;
//! 3. `puffin_airgapped` in the configuration files, **strictest wins**: the repository's file
//!    can be written by the agent, so it may tighten the user-level file's level, never loosen it;
//! 4. `off`.
//!
//! Standard library only: this file is compiled into Codex's sandbox helper, the launcher and the
//! web commands.

use std::path::Path;
use std::path::PathBuf;

/// The level when nothing is configured. Mirrored by `DEFAULT_PUFFIN_AIRGAPPED` in Dreamference's
/// Python config, which a test compares with this line.
pub const DEFAULT_PUFFIN_AIRGAPPED: &str = "off";

/// The environment variable that overrides the configuration files.
pub const ENV_VAR: &str = "DREAMFERENCE_PUFFIN_AIRGAPPED";

/// The TOML key `/airgapped default` writes.
pub const TOML_KEY: &str = "puffin_airgapped";

/// The folder under `$CODEX_HOME` that holds one file per session.
pub const SESSION_DIR: &str = "airgapped";

/// Set by Codex for a command it runs without a network.
pub const SANDBOX_NETWORK_DISABLED_ENV_VAR: &str = "CODEX_SANDBOX_NETWORK_DISABLED";

/// A level, ordered by strictness: `Off < DuckDuckGo < On`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Off,
    DuckDuckGo,
    On,
}

impl Level {
    pub const ALL: [Level; 3] = [Level::Off, Level::DuckDuckGo, Level::On];

    /// Parses a level name, ignoring case and surrounding space. `ddg` is `duckduckgo`.
    pub fn parse(name: &str) -> Option<Level> {
        match name.trim().to_ascii_lowercase().as_str() {
            "off" => Some(Level::Off),
            "duckduckgo" | "ddg" => Some(Level::DuckDuckGo),
            "on" => Some(Level::On),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Level::Off => "off",
            Level::DuckDuckGo => "duckduckgo",
            Level::On => "on",
        }
    }
}

/// Where the level in force came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Session,
    Environment,
    ConfigFile(PathBuf),
    Default,
}

impl Source {
    pub fn label(&self) -> String {
        match self {
            Source::Session => "this session".to_string(),
            Source::Environment => ENV_VAR.to_string(),
            Source::ConfigFile(path) => format!("{TOML_KEY} in {}", path.display()),
            Source::Default => "default".to_string(),
        }
    }
}

/// The level in force, where it came from, and any invalid values passed over on the way.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub level: Level,
    pub source: Source,
    pub invalid: Vec<String>,
}

/// The value of the top-level `puffin_airgapped` key in TOML text: a line `puffin_airgapped =
/// "<value>"` before the first table header. Enough for a key this crate writes itself, and it
/// keeps a TOML parser out of the sandbox helper.
pub fn toml_value(text: &str) -> Option<String> {
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            return None;
        }
        let Some(rest) = line.strip_prefix(TOML_KEY) else { continue };
        let Some(value) = rest.trim_start().strip_prefix('=') else { continue };
        let value = value.split('#').next().unwrap_or("").trim();
        let value = value.trim_matches(|quote| quote == '"' || quote == '\'');
        return Some(value.to_string());
    }
    None
}

/// Resolves the level from already-read tiers; [`resolve`] supplies the real ones. `configs` are
/// the configuration files that exist, as (path, text); the strictest valid level among them wins.
pub fn resolve_from(session: Option<&str>, environment: Option<&str>, configs: &[(PathBuf, String)]) -> Resolved {
    let mut invalid = Vec::new();
    let mut tier = |value: Option<&str>, source: Source| -> Option<(Level, Source)> {
        let value = value?.trim();
        if value.is_empty() {
            return None;
        }
        match Level::parse(value) {
            Some(level) => Some((level, source)),
            None => {
                invalid.push(format!("ignored \"{value}\" from {}", source.label()));
                None
            }
        }
    };
    let mut found = tier(session, Source::Session).or_else(|| tier(environment, Source::Environment));
    if found.is_none() {
        for (path, text) in configs {
            let value = toml_value(text);
            if let Some((level, source)) = tier(value.as_deref(), Source::ConfigFile(path.clone()))
                && found.as_ref().is_none_or(|(strictest, _)| level > *strictest)
            {
                found = Some((level, source));
            }
        }
    }
    let (level, source) = found.unwrap_or((Level::Off, Source::Default));
    Resolved { level, source, invalid }
}

/// `$CODEX_HOME`, or `~/.puffin` when a shell-environment policy stripped the variable.
pub fn codex_home() -> Option<PathBuf> {
    match std::env::var_os("CODEX_HOME") {
        Some(home) if !home.is_empty() => Some(PathBuf::from(home)),
        _ => Some(PathBuf::from(std::env::var_os("HOME")?).join(".puffin")),
    }
}

/// A session's level file. Ids are UUIDs; anything else is refused rather than joined to a path.
pub fn session_file(id: &str) -> Option<PathBuf> {
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return None;
    }
    Some(codex_home()?.join(SESSION_DIR).join(id))
}

/// The user-level configuration file, the one `/airgapped default` writes.
pub fn user_config_file() -> Option<PathBuf> {
    Some(PathBuf::from(std::env::var_os("HOME")?).join(".config/dreamference/config.toml"))
}

/// The configuration files to read, in the order they are named when two agree: the one
/// `DREAMFERENCE_CONFIG_PATH` names or `./dreamference.toml`, then the user-level file.
pub fn config_files(cwd: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    match std::env::var_os("DREAMFERENCE_CONFIG_PATH") {
        Some(path) if !path.is_empty() => files.push(PathBuf::from(path)),
        _ => files.push(cwd.join("dreamference.toml")),
    }
    if let Some(user) = user_config_file()
        && !files.contains(&user)
    {
        files.push(user);
    }
    files
}

/// Resolves the level for the first of `ids` that has a session file, reading every tier.
pub fn resolve(ids: &[&str]) -> Resolved {
    let session = ids
        .iter()
        .filter_map(|id| session_file(id))
        .find_map(|path| std::fs::read_to_string(path).ok());
    let environment = std::env::var(ENV_VAR).ok();
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let configs: Vec<(PathBuf, String)> = config_files(&cwd)
        .into_iter()
        .filter_map(|path| std::fs::read_to_string(&path).ok().map(|text| (path, text)))
        .collect();
    resolve_from(session.as_deref(), environment.as_deref(), &configs)
}

/// Resolves the level for a command the agent runs, from the ids Codex puts in its environment:
/// the thread's own, then the root session's, so a subagent without a file of its own takes its
/// parent's.
pub fn resolve_for_command() -> Resolved {
    let thread = std::env::var("CODEX_THREAD_ID").unwrap_or_default();
    let session = std::env::var("CODEX_SESSION_ID").unwrap_or_default();
    resolve(&[thread.as_str(), session.as_str()])
}

/// Whether the command being started must have no network. Asked by Codex's sandbox helper, in
/// the command's own environment, once per command.
pub fn sealed_for_command() -> bool {
    resolve_for_command().level == Level::On
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(path: &str, text: &str) -> (PathBuf, String) {
        (PathBuf::from(path), text.to_string())
    }

    #[test]
    fn levels_parse_with_the_alias_and_reject_unknown_names() {
        assert_eq!(Level::parse(" ON "), Some(Level::On));
        assert_eq!(Level::parse("ddg"), Some(Level::DuckDuckGo));
        assert_eq!(Level::parse("DuckDuckGo"), Some(Level::DuckDuckGo));
        assert_eq!(Level::parse("off"), Some(Level::Off));
        assert_eq!(Level::parse("airgapped"), None);
        assert!(Level::Off < Level::DuckDuckGo && Level::DuckDuckGo < Level::On);
        assert_eq!(Level::parse(DEFAULT_PUFFIN_AIRGAPPED), Some(Level::Off));
    }

    #[test]
    fn the_key_is_read_only_at_the_top_level() {
        assert_eq!(toml_value("puffin_airgapped = \"on\"\n").as_deref(), Some("on"));
        assert_eq!(toml_value("model = \"x\"\npuffin_airgapped='ddg' # search\n").as_deref(), Some("ddg"));
        assert_eq!(toml_value("[night]\npuffin_airgapped = \"on\"\n"), None);
        assert_eq!(toml_value("puffin_airgapped_other = \"on\"\n"), None);
        assert_eq!(toml_value(""), None);
    }

    #[test]
    fn the_first_tier_that_has_a_valid_value_wins() {
        let configs = [config("/repo/dreamference.toml", "puffin_airgapped = \"on\"\n")];
        let resolved = resolve_from(Some("off\n"), Some("duckduckgo"), &configs);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Session));
        let resolved = resolve_from(None, Some("duckduckgo"), &configs);
        assert_eq!((resolved.level, resolved.source), (Level::DuckDuckGo, Source::Environment));
        let resolved = resolve_from(None, None, &[]);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Default));
    }

    #[test]
    fn an_invalid_value_is_named_and_skipped() {
        let resolved = resolve_from(Some("sealed"), Some("on"), &[]);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.invalid, vec!["ignored \"sealed\" from this session".to_string()]);
    }

    #[test]
    fn between_the_two_files_the_strictest_wins() {
        let repo_off = config("/repo/dreamference.toml", "puffin_airgapped = \"off\"\n");
        let user_on = config("/home/u/.config/dreamference/config.toml", "puffin_airgapped = \"on\"\n");
        // A repository file the agent can write does not loosen the user's level.
        let resolved = resolve_from(None, None, &[repo_off.clone(), user_on.clone()]);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.source, Source::ConfigFile(user_on.0.clone()));
        // It may tighten it.
        let repo_on = config("/repo/dreamference.toml", "puffin_airgapped = \"on\"\n");
        let user_ddg = config("/home/u/.config/dreamference/config.toml", "puffin_airgapped = \"ddg\"\n");
        let resolved = resolve_from(None, None, &[repo_on.clone(), user_ddg]);
        assert_eq!(resolved.source, Source::ConfigFile(repo_on.0));
        // A file without the key does not count.
        let resolved = resolve_from(None, None, &[config("/repo/dreamference.toml", "model = \"x\"\n"), repo_off]);
        assert_eq!(resolved.level, Level::Off);
    }

    #[test]
    fn session_ids_that_are_not_ids_are_refused() {
        assert_eq!(session_file("../../etc/passwd"), None);
        assert_eq!(session_file(""), None);
    }
}
