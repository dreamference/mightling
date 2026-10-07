//! Cave mode: how tersely Puffin answers, set with `/cavemode` (specs/DREAMFERENCE_PUFFIN_CAVE_MODE.md).
//!
//! The level reaches the model as a World State section, the mechanism Codex's own `git-attribution`
//! extension uses: a developer message is added only when the section's value changes. The value is
//! the level plus, for every level but `off`, the current turn's id, so a session gets the level's
//! full text once, a one-line reminder at the start of each later turn, nothing between tool calls,
//! and the new full text on a switch. The system prompt is never touched, so `off` is Codex's own
//! behaviour and the prefix cache survives a switch.
//!
//! The level is resolved before every model request, first match wins: this session's file
//! (`$CODEX_HOME/cave_mode/<thread-id>`, written by `/cavemode <level>`), then
//! `DREAMFERENCE_PUFFIN_CAVE_MODE`, then `puffin_cave_mode` in the Dreamference TOML file, then
//! [`DEFAULT_PUFFIN_CAVE_MODE`]. A file keyed by thread id survives `puffin resume` and needs no
//! channel between the TUI and the extension, which may run in another process.
//!
//! The texts in `cave/` are byte-for-byte the ones `scripts/cave_mode_bench/levels/` measured; a
//! Python test keeps them equal, because the benchmark's verdict is only about that text.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::SystemTime;

use codex_extension_api::ContextContributor;
use codex_extension_api::ExtensionFuture;
use codex_extension_api::ExtensionRegistryBuilder;
use codex_extension_api::PreviousWorldStateSection;
use codex_extension_api::RenderedWorldStateFragment;
use codex_extension_api::WorldStateContributionInput;
use codex_extension_api::WorldStateSectionContribution;
use serde_json::Value;
use serde_json::json;

/// The level a session starts at when nothing else is configured. Mirrored by
/// `DEFAULT_PUFFIN_CAVE_MODE` in Dreamference's Python config, which a test compares with this line.
pub const DEFAULT_PUFFIN_CAVE_MODE: &str = "ultra";

/// The environment variable that overrides the TOML file for every session.
pub const ENV_VAR: &str = "DREAMFERENCE_PUFFIN_CAVE_MODE";

/// The TOML key `/cavemode default` writes.
pub const TOML_KEY: &str = "puffin_cave_mode";

const SECTION_ID: &str = "cave_mode";
const START_MARKER: &str = "<cave_mode>";
const END_MARKER: &str = "</cave_mode>";
const SESSION_DIR: &str = "cave_mode";
const SESSION_FILE_MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const USAGE: &str = "Usage: /cavemode [off|lite|full|ultra] or /cavemode default <level>";

const LITE: &str = include_str!("../cave/lite.txt");
const FULL: &str = include_str!("../cave/full.txt");
const ULTRA: &str = include_str!("../cave/ultra.txt");
const LITE_REMINDER: &str = include_str!("../cave/lite.reminder.txt");
const FULL_REMINDER: &str = include_str!("../cave/full.reminder.txt");
const ULTRA_REMINDER: &str = include_str!("../cave/ultra.reminder.txt");
const OFF: &str = include_str!("../cave/off.txt");

/// How terse the answers are.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Level {
    Off,
    Lite,
    Full,
    Ultra,
}

impl Level {
    pub const ALL: [Level; 4] = [Level::Off, Level::Lite, Level::Full, Level::Ultra];

    /// Parses a level name, ignoring case and surrounding space.
    pub fn parse(name: &str) -> Option<Level> {
        match name.trim().to_ascii_lowercase().as_str() {
            "off" => Some(Level::Off),
            "lite" => Some(Level::Lite),
            "full" => Some(Level::Full),
            "ultra" => Some(Level::Ultra),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Level::Off => "off",
            Level::Lite => "lite",
            Level::Full => "full",
            Level::Ultra => "ultra",
        }
    }

    fn summary(self) -> &'static str {
        match self {
            Level::Off => "the normal voice; no cave rules are sent",
            Level::Lite => "full sentences; answer first, one option, no filler",
            Level::Full => "terse; about five sentences outside code",
            Level::Ultra => "at most three sentences outside code",
        }
    }

    /// The level's full text as the model sees it, markers included. `off`'s is the message that
    /// cancels an earlier level; a session that never left `off` is sent nothing.
    pub fn text(self) -> &'static str {
        match self {
            Level::Off => OFF,
            Level::Lite => LITE,
            Level::Full => FULL,
            Level::Ultra => ULTRA,
        }
    }

    /// The one-line reminder sent at the start of each later turn (none for `off`).
    pub fn reminder(self) -> Option<&'static str> {
        match self {
            Level::Off => None,
            Level::Lite => Some(LITE_REMINDER),
            Level::Full => Some(FULL_REMINDER),
            Level::Ultra => Some(ULTRA_REMINDER),
        }
    }

    /// Whether `text` is this level's full text: not its reminder, not another level's text.
    fn is_its_full_text(self, text: &str) -> bool {
        self != Level::Off && text.trim() == self.text().trim()
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
    fn label(&self) -> String {
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

/// Resolves the level from already-read tiers; [`resolve`] supplies the real ones.
pub fn resolve_from(
    session: Option<&str>,
    environment: Option<&str>,
    config: Option<(&Path, &str)>,
) -> Resolved {
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
    let found = tier(session, Source::Session)
        .or_else(|| tier(environment, Source::Environment))
        .or_else(|| {
            let (path, text) = config?;
            let value = toml::from_str::<toml::Table>(text).ok()?.get(TOML_KEY)?.as_str()?.to_string();
            tier(Some(&value), Source::ConfigFile(path.to_path_buf()))
        });
    let (level, source) = found.unwrap_or((Level::Ultra, Source::Default));
    Resolved { level, source, invalid }
}

/// Resolves the level in force for a thread, reading every tier.
pub fn resolve(thread_id: Option<&str>) -> Resolved {
    let session = thread_id.and_then(session_file).and_then(|path| std::fs::read_to_string(path).ok());
    let environment = std::env::var(ENV_VAR).ok();
    let config = crate::config_file().and_then(|path| std::fs::read_to_string(&path).ok().map(|text| (path, text)));
    resolve_from(
        session.as_deref(),
        environment.as_deref(),
        config.as_ref().map(|(path, text)| (path.as_path(), text.as_str())),
    )
}

fn session_dir() -> Option<PathBuf> {
    let home = codex_utils_home_dir::find_codex_home().ok()?;
    Some(home.as_path().join(SESSION_DIR))
}

/// This thread's session file. Thread ids are UUIDs; anything else is refused rather than joined.
fn session_file(thread_id: &str) -> Option<PathBuf> {
    if thread_id.is_empty() || !thread_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return None;
    }
    Some(session_dir()?.join(thread_id))
}

/// Deletes session files older than thirty days. Called at launch; errors are ignored.
pub fn prune_session_files() {
    let Some(dir) = session_dir() else { return };
    prune_older_than(&dir, SESSION_FILE_MAX_AGE, SystemTime::now());
}

fn prune_older_than(dir: &Path, max_age: Duration, now: SystemTime) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let stale = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > max_age);
        if stale {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Runs `/cavemode [args]` and returns the lines to print. It never calls the model. Generic over
/// the thread id so the TUI's hook can pass its `Option<ThreadId>` as it is.
pub fn command<T: std::fmt::Display>(thread_id: Option<T>, args: &str) -> Vec<String> {
    let thread_id = thread_id.map(|id| id.to_string());
    let thread_id = thread_id.as_deref();
    let words: Vec<&str> = args.split_whitespace().collect();
    match words.as_slice() {
        [] => status(thread_id),
        [name] => match Level::parse(name) {
            Some(level) => set_session(thread_id, level),
            None => vec![USAGE.to_string()],
        },
        [default, name] if default.eq_ignore_ascii_case("default") => match Level::parse(name) {
            Some(level) => set_default(thread_id, level),
            None => vec![USAGE.to_string()],
        },
        _ => vec![USAGE.to_string()],
    }
}

fn status(thread_id: Option<&str>) -> Vec<String> {
    let resolved = resolve(thread_id);
    status_lines(&resolved)
}

fn status_lines(resolved: &Resolved) -> Vec<String> {
    let mut lines = vec![format!("Cave mode: {} ({})", resolved.level.name(), resolved.source.label())];
    for level in Level::ALL {
        let marker = if level == resolved.level { "   ← in force" } else { "" };
        lines.push(format!("  {:<6} {}{marker}", level.name(), level.summary()));
    }
    lines.extend(resolved.invalid.iter().map(|note| format!("Note: {note}.")));
    lines.push("More room: /cavemode full (this session) or /cavemode default full (new sessions).".to_string());
    lines
}

fn set_session(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let Some(path) = thread_id.and_then(session_file) else {
        return vec!["No session yet: send a message first, or use /cavemode default <level>.".to_string()];
    };
    match write_session_file(&path, level) {
        Ok(()) => vec![format!(
            "Cave mode: {} for this session, from the next model request.",
            level.name()
        )],
        Err(err) => vec![format!("Could not save the level to {}: {err}", path.display())],
    }
}

fn write_session_file(path: &Path, level: Level) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, format!("{}\n", level.name()))
}

fn set_default(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let path = crate::config_file().or_else(|| {
        Some(puffin_node_locator::home_dir()?.join(".config").join("dreamference").join("config.toml"))
    });
    let Some(path) = path else {
        return vec!["Could not find a configuration file to write: HOME is not set.".to_string()];
    };
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let updated = match with_default(&existing, level) {
        Ok(updated) => updated,
        Err(err) => return vec![format!("Could not update {}: {err}", path.display())],
    };
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| std::fs::write(&path, updated));
    if let Err(err) = written {
        return vec![format!("Could not write {}: {err}", path.display())];
    }
    let mut lines = vec![format!(
        "Cave mode: {} for new sessions ({TOML_KEY} in {}).",
        level.name(),
        path.display()
    )];
    if thread_id.is_some() {
        lines.extend(set_session(thread_id, level));
    }
    if std::env::var(ENV_VAR).is_ok_and(|value| !value.trim().is_empty()) {
        lines.push(format!("{ENV_VAR} is set, and it still wins for new sessions."));
    }
    lines
}

/// The TOML text with `puffin_cave_mode` set to `level`, everything else kept as written.
fn with_default(existing: &str, level: Level) -> Result<String, toml_edit::TomlError> {
    let mut document: toml_edit::DocumentMut = existing.parse()?;
    document[TOML_KEY] = toml_edit::value(level.name());
    Ok(document.to_string())
}

/// What the section sends, given the level in force, this turn, and what the model last saw.
fn render(level: Level, turn: &str, previous: PreviousWorldStateSection<'_>) -> Option<&'static str> {
    let value = snapshot(level, turn);
    if matches!(previous, PreviousWorldStateSection::Known(known) if *known == value) {
        // The same level in the same turn: a later step, so nothing between tool calls.
        return None;
    }
    let before = match previous {
        PreviousWorldStateSection::Known(known) => known.get("level").and_then(Value::as_str),
        PreviousWorldStateSection::Absent | PreviousWorldStateSection::Unknown => None,
    };
    match (level, before) {
        // Never on, or still off: a session at `off` is exactly upstream Codex.
        (Level::Off, None | Some("off")) => None,
        (Level::Off, Some(_)) => Some(Level::Off.text()),
        // Same level, new turn.
        (_, Some(name)) if name == level.name() => level.reminder(),
        // First use, a switch, or the full text gone from history after a compaction.
        _ => Some(level.text()),
    }
}

fn snapshot(level: Level, turn: &str) -> Value {
    match level {
        Level::Off => json!({ "level": "off" }),
        _ => json!({ "level": level.name(), "turn": turn }),
    }
}

/// The text between the markers, which the harness puts back when it renders the fragment.
fn body(text: &'static str) -> &'static str {
    let text = text.trim_end();
    text.strip_prefix(START_MARKER)
        .and_then(|rest| rest.strip_suffix(END_MARKER))
        .unwrap_or(text)
}

fn section(level: Level, turn: &str) -> WorldStateSectionContribution {
    let turn = turn.to_string();
    let contribution = WorldStateSectionContribution::new(SECTION_ID, snapshot(level, &turn), move |previous| {
        render(level, &turn, previous)
            .map(|text| RenderedWorldStateFragment::new("developer", (START_MARKER, END_MARKER), body(text)))
    });
    if level == Level::Off {
        // Nothing has to stay in history for `off`; a matcher would turn a switch to `off` into
        // "absent" on the history path and swallow the message that cancels the old level.
        contribution
    } else {
        contribution.with_retained_fragment_matcher(move |role, text| role == "developer" && level.is_its_full_text(text))
    }
}

struct CaveMode;

impl ContextContributor for CaveMode {
    fn contribute_world_state<'a>(
        &'a self,
        input: WorldStateContributionInput<'a>,
    ) -> ExtensionFuture<'a, Vec<WorldStateSectionContribution>> {
        Box::pin(async move {
            let level = resolve(Some(&input.thread_id.to_string())).level;
            vec![section(level, input.turn_id)]
        })
    }
}

/// Registers the cave-mode section. Called beside `codex_git_attribution::install` in the app
/// server's registry and in the one `puffin debug prompt-input` builds.
pub fn install<C: Sync>(registry: &mut ExtensionRegistryBuilder<C>) {
    registry.prompt_contributor(Arc::new(CaveMode));
}

#[cfg(test)]
mod tests {
    use super::*;

    const KNOWN_OFF: &str = r#"{"level":"off"}"#;

    fn known(text: &str) -> Value {
        serde_json::from_str(text).unwrap()
    }

    fn rendered(level: Level, turn: &str, previous: PreviousWorldStateSection<'_>) -> Option<&'static str> {
        render(level, turn, previous)
    }

    #[test]
    fn levels_parse_ignoring_case_and_reject_unknown_names() {
        assert_eq!(Level::parse(" ULTRA "), Some(Level::Ultra));
        assert_eq!(Level::parse("Lite"), Some(Level::Lite));
        assert_eq!(Level::parse("loud"), None);
        assert_eq!(Level::parse(""), None);
        for level in Level::ALL {
            assert_eq!(Level::parse(level.name()), Some(level));
        }
    }

    #[test]
    fn the_default_is_ultra() {
        assert_eq!(Level::parse(DEFAULT_PUFFIN_CAVE_MODE), Some(Level::Ultra));
        assert_eq!(resolve_from(None, None, None), Resolved { level: Level::Ultra, source: Source::Default, invalid: vec![] });
    }

    #[test]
    fn each_tier_shadows_the_next() {
        let path = Path::new("/tmp/dreamference.toml");
        let toml = "puffin_cave_mode = \"lite\"\n";
        assert_eq!(resolve_from(Some("off\n"), Some("full"), Some((path, toml))).source, Source::Session);
        assert_eq!(resolve_from(Some("off\n"), Some("full"), Some((path, toml))).level, Level::Off);
        assert_eq!(resolve_from(None, Some("full"), Some((path, toml))).level, Level::Full);
        let from_file = resolve_from(None, None, Some((path, toml)));
        assert_eq!((from_file.level, from_file.source), (Level::Lite, Source::ConfigFile(path.to_path_buf())));
        assert_eq!(resolve_from(None, Some(""), Some((path, "vllm_host = \"x\"\n"))).source, Source::Default);
    }

    #[test]
    fn an_invalid_value_is_skipped_and_named() {
        let resolved = resolve_from(Some("loud"), Some("full"), None);
        assert_eq!(resolved.level, Level::Full);
        assert_eq!(resolved.invalid, vec!["ignored \"loud\" from this session".to_string()]);
        let path = Path::new("/etc/x.toml");
        let resolved = resolve_from(None, Some("max"), Some((path, "puffin_cave_mode = \"tiny\"\n")));
        assert_eq!((resolved.level, resolved.source.clone()), (Level::Ultra, Source::Default));
        assert_eq!(resolved.invalid.len(), 2);
        assert!(status_lines(&resolved).iter().any(|line| line.contains("\"tiny\" from puffin_cave_mode in /etc/x.toml")));
    }

    #[test]
    fn the_render_table_matches_the_spec() {
        use PreviousWorldStateSection::Absent;
        use PreviousWorldStateSection::Known;
        use PreviousWorldStateSection::Unknown;
        let ultra_t1 = known(r#"{"level":"ultra","turn":"t1"}"#);
        let off = known(KNOWN_OFF);
        // Absent: the full text for a level, nothing for off.
        assert_eq!(rendered(Level::Ultra, "t1", Absent), Some(ULTRA));
        assert_eq!(rendered(Level::Off, "t1", Absent), None);
        assert_eq!(rendered(Level::Lite, "t1", Unknown), Some(LITE));
        // Same level, same turn: a later step says nothing.
        assert_eq!(rendered(Level::Ultra, "t1", Known(&ultra_t1)), None);
        // Same level, new turn: the reminder.
        assert_eq!(rendered(Level::Ultra, "t2", Known(&ultra_t1)), Some(ULTRA_REMINDER));
        // A switch: the new full text, even mid-turn.
        assert_eq!(rendered(Level::Full, "t1", Known(&ultra_t1)), Some(FULL));
        assert_eq!(rendered(Level::Lite, "t1", Known(&off)), Some(LITE));
        // To off: the cancelling text once, then nothing.
        assert_eq!(rendered(Level::Off, "t1", Known(&ultra_t1)), Some(OFF));
        assert_eq!(rendered(Level::Off, "t2", Known(&off)), None);
    }

    #[test]
    fn the_section_renders_its_body_between_the_markers() {
        let section = section(Level::Ultra, "t1");
        let fragment = section.render_diff(PreviousWorldStateSection::Absent).unwrap();
        assert_eq!(fragment.role(), "developer");
        assert_eq!(fragment.markers(), (START_MARKER, END_MARKER));
        assert_eq!(format!("{START_MARKER}{}{END_MARKER}", fragment.body()), ULTRA.trim_end());
        assert_eq!(section.snapshot(), &known(r#"{"level":"ultra","turn":"t1"}"#));
        assert_eq!(super::section(Level::Off, "t9").snapshot(), &known(KNOWN_OFF));
    }

    #[test]
    fn the_retained_matcher_accepts_only_the_current_full_text() {
        let ultra = section(Level::Ultra, "t1");
        assert!(ultra.has_retained_fragment_matcher());
        assert!(ultra.matches_retained_fragment("developer", ULTRA.trim_end()));
        assert!(!ultra.matches_retained_fragment("user", ULTRA));
        assert!(!ultra.matches_retained_fragment("developer", ULTRA_REMINDER));
        assert!(!ultra.matches_retained_fragment("developer", FULL));
        assert!(!section(Level::Off, "t1").has_retained_fragment_matcher());
    }

    #[test]
    fn texts_stay_within_their_budgets() {
        // Characters / 4 overestimates Qwen3.8's tokens for these texts: full is 1,836 bytes, or
        // 459 by the estimate, and 432 tokens measured with the tokenizer (spec Appendix A). The
        // bound catches a text growing well past what was measured, not a few words.
        for level in [Level::Lite, Level::Full, Level::Ultra] {
            assert!(level.text().len() / 4 < 500, "{} text too long", level.name());
            assert!(level.reminder().unwrap().len() / 4 < 60, "{} reminder too long", level.name());
            assert!(level.text().starts_with(START_MARKER) && level.text().trim_end().ends_with(END_MARKER));
            assert!(level.text().contains(&format!("Cave mode is {}.", level.name())));
        }
        assert!(OFF.contains("Cave mode is off."));
    }

    #[test]
    fn status_marks_the_level_in_force_and_its_source() {
        let lines = status_lines(&resolve_from(None, Some("full"), None));
        assert_eq!(lines[0], format!("Cave mode: full ({ENV_VAR})"));
        assert_eq!(lines.iter().filter(|line| line.contains("← in force")).count(), 1);
        assert!(lines.iter().any(|line| line.starts_with("  full ") && line.contains("← in force")));
        assert!(lines.iter().all(|line| !line.contains("Codex")));
        assert!(lines.last().unwrap().starts_with("More room:"));
    }

    #[test]
    fn bad_arguments_print_the_usage_and_change_nothing() {
        assert_eq!(command(Some("t"), "loud"), vec![USAGE.to_string()]);
        assert_eq!(command(Some("t"), "default"), vec![USAGE.to_string()]);
        assert_eq!(command(Some("t"), "default loud"), vec![USAGE.to_string()]);
        assert_eq!(command(Some("t"), "full now"), vec![USAGE.to_string()]);
    }

    #[test]
    fn a_session_level_needs_a_thread() {
        assert!(command(None::<&str>, "full")[0].starts_with("No session yet"));
        assert!(session_file("../../etc/passwd").is_none());
        assert!(session_file("").is_none());
    }

    #[test]
    fn the_default_is_written_without_disturbing_the_file() {
        let updated = with_default("# mine\nvllm_host = \"http://h:8000\"\n\n[table]\nkey = 1\n", Level::Full).unwrap();
        assert!(updated.starts_with("# mine\nvllm_host = \"http://h:8000\"\n"));
        assert!(updated.contains("[table]\nkey = 1"));
        let parsed: toml::Table = toml::from_str(&updated).unwrap();
        assert_eq!(parsed["puffin_cave_mode"].as_str(), Some("full"));
        assert!(parsed.get("table").unwrap().get("puffin_cave_mode").is_none());
        let again = with_default(&updated, Level::Off).unwrap();
        assert_eq!(toml::from_str::<toml::Table>(&again).unwrap()["puffin_cave_mode"].as_str(), Some("off"));
        assert_eq!(again.matches("puffin_cave_mode").count(), 1);
    }

    #[test]
    fn session_files_round_trip_and_old_ones_are_pruned() {
        let dir = std::env::temp_dir().join(format!("puffin-cave-test-{}", std::process::id()));
        let file = dir.join("019a-thread");
        write_session_file(&file, Level::Lite).unwrap();
        let text = std::fs::read_to_string(&file).unwrap();
        assert_eq!(resolve_from(Some(&text), None, None).level, Level::Lite);
        prune_older_than(&dir, SESSION_FILE_MAX_AGE, SystemTime::now());
        assert!(file.is_file(), "a fresh file is kept");
        prune_older_than(&dir, SESSION_FILE_MAX_AGE, SystemTime::now() + SESSION_FILE_MAX_AGE * 2);
        assert!(!file.exists(), "a file older than thirty days is deleted");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
