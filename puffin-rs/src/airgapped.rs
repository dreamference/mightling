//! `/airgapped`: how much of the internet a session may use (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md).
//!
//! The levels and their resolution live in the leaf crate `puffin-airgapped` (`airgapped/`), because
//! Codex's sandbox helper asks the same question for every command it starts (patch 0019) and must
//! not depend on the launcher. This module is what the user sees: the command, the status, the
//! `default` writer, and the World State section that tells the model when the level changes.
//!
//! The level file is keyed by thread id, the only id the TUI's hook and the World State input
//! carry; the sandbox helper also looks under the root session's id, so a subagent with no file of
//! its own takes its parent's.

use std::path::Path;
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
pub use puffin_airgapped::ENV_VAR;
pub use puffin_airgapped::Level;
pub use puffin_airgapped::Resolved;
pub use puffin_airgapped::TOML_KEY;
use serde_json::Value;
use serde_json::json;

const SECTION_ID: &str = "airgapped";
const START_MARKER: &str = "<airgapped>";
const END_MARKER: &str = "</airgapped>";
const SESSION_FILE_MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const USAGE: &str = "Usage: /airgapped [off|duckduckgo|on] or /airgapped default <level>";

/// What the model is told when the level changes to `duckduckgo`.
pub const DUCKDUCKGO_TEXT: &str = "Web search in this session goes through DuckDuckGo only. If a search does not answer, say so: there is no other engine at this level and nothing to restart.";

/// What the model is told when the level changes to `on`.
pub const ON_TEXT: &str = "This session has no network. puffin-search, puffin-fetch, puffin-admin gmail, curl, git fetch and push, and package installs fail by design. Do not try them, and do not ask to run a command outside the sandbox to get around it. Work from the files here; when an answer needs something you cannot look up, say which part is from memory and may be out of date.";

/// What the model is told when the level goes back to `off`.
/// Said plainly and with the commands named: the first wording ("the earlier restriction no longer
/// applies") was delivered and the model still refused to run `curl`, quoting the `on` message.
pub const OFF_TEXT: &str = "The user has lifted this session's air-gap restriction. The earlier message about it no longer applies: commands have the network again, and curl, git fetch and push, package installs, puffin-search and puffin-fetch work as the system prompt describes. Run them when the task calls for it.";

fn summary(level: Level) -> &'static str {
    match level {
        Level::Off => "search through every engine SearXNG has enabled; pages fetched directly",
        Level::DuckDuckGo => "search through DuckDuckGo only (a preference, not a barrier); pages fetched directly",
        Level::On => "no network for anything the agent runs: no search, no fetch, no Gmail",
    }
}

fn text(level: Level) -> &'static str {
    match level {
        Level::Off => OFF_TEXT,
        Level::DuckDuckGo => DUCKDUCKGO_TEXT,
        Level::On => ON_TEXT,
    }
}

/// Resolves the level for a thread (or, with none, the configured level), reading every tier.
pub fn resolve(thread_id: Option<&str>) -> Resolved {
    puffin_airgapped::resolve(&[thread_id.unwrap_or_default()])
}

/// Deletes session files older than thirty days. Called at launch; errors are ignored.
pub fn prune_session_files() {
    let Some(dir) = puffin_airgapped::codex_home().map(|home| home.join(puffin_airgapped::SESSION_DIR)) else {
        return;
    };
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

/// Runs `/airgapped [args]` and returns the lines to print. It never calls the model. Generic over
/// the thread id so the TUI's hook can pass its `Option<ThreadId>` as it is.
pub fn command<T: std::fmt::Display>(thread_id: Option<T>, args: &str) -> Vec<String> {
    let thread_id = thread_id.map(|id| id.to_string());
    let thread_id = thread_id.as_deref();
    let words: Vec<&str> = args.split_whitespace().collect();
    match words.as_slice() {
        [] => status_lines(&resolve(thread_id), thread_id.is_some()),
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

/// Runs `puffin airgapped …` from a shell: the status of the configured level, or `default
/// <level>`. Returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let lines = match args.first().map(String::as_str) {
        None => command(None::<String>, ""),
        Some("default") => command(None::<String>, &args.join(" ")),
        Some(_) => vec![
            "Usage: puffin airgapped [default <off|duckduckgo|on>]".to_string(),
            format!("For one run: {ENV_VAR}=on puffin exec …; inside a session: /airgapped <level>."),
        ],
    };
    let failed = lines.first().is_some_and(|line| line.starts_with("Usage:"));
    for line in lines {
        println!("{line}");
    }
    i32::from(failed) * 2
}

fn status_lines(resolved: &Resolved, in_session: bool) -> Vec<String> {
    let mut lines = vec![format!("Airgapped: {} ({})", resolved.level.name(), resolved.source.label())];
    for level in Level::ALL {
        let marker = if level == resolved.level { "   ← in force" } else { "" };
        lines.push(format!("  {:<11} {}{marker}", level.name(), summary(level)));
    }
    if resolved.level == Level::On {
        lines.push("Enforced: sandboxed commands run with no network (bwrap --unshare-net).".to_string());
        lines.push("NOT ENFORCED for: Full Access, a command you approve to run outside the sandbox, MCP servers, and puffin's own connection to the model server.".to_string());
    }
    lines.extend(resolved.invalid.iter().map(|note| format!("Note: {note}.")));
    lines.push("Not covered at any level: the web chat, MCP servers you configured.".to_string());
    lines.push(if in_session {
        "Change: /airgapped on (this session) or /airgapped default on (new sessions).".to_string()
    } else {
        "Change: puffin airgapped default <level> (new sessions), or /airgapped <level> inside a session.".to_string()
    });
    lines
}

fn set_session(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let Some(path) = thread_id.and_then(puffin_airgapped::session_file) else {
        return vec!["No session yet: send a message first, or use /airgapped default <level>.".to_string()];
    };
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| std::fs::write(&path, format!("{}\n", level.name())));
    if let Err(err) = written {
        return vec![format!("Could not save the level to {}: {err}", path.display())];
    }
    let mut lines = vec![format!(
        "Airgapped: {} for this session, from the next command the agent starts.",
        level.name()
    )];
    match level {
        Level::On => lines.push(
            "Sandboxed commands now run with no network. Not covered: Full Access, commands you approve to run outside the sandbox, MCP servers.".to_string(),
        ),
        Level::DuckDuckGo => lines.push(
            "puffin-search now asks DuckDuckGo only. This is a preference the web commands follow, not a barrier.".to_string(),
        ),
        Level::Off => {}
    }
    lines
}

fn set_default(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let Some(path) = puffin_airgapped::user_config_file() else {
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
        "Airgapped: {} for new sessions ({TOML_KEY} in {}).",
        level.name(),
        path.display()
    )];
    if thread_id.is_some() {
        lines.extend(set_session(thread_id, level));
    }
    // Say so when another tier still decides differently for a new session.
    let configured = resolve(None);
    if configured.level != level {
        lines.push(format!(
            "New sessions still start at {}: {} says so.",
            configured.level.name(),
            configured.source.label()
        ));
    }
    lines
}

/// The TOML text with `puffin_airgapped` set to `level`, everything else kept as written. The key
/// is inserted before the first table, where a top-level key has to be.
fn with_default(existing: &str, level: Level) -> Result<String, toml_edit::TomlError> {
    let mut document: toml_edit::DocumentMut = existing.parse()?;
    document[TOML_KEY] = toml_edit::value(level.name());
    Ok(document.to_string())
}

/// The reason `puffin` must not start with these arguments at a configured `on`, if there is one:
/// Full Access has no sandbox to take the network away, so the two contradict each other.
pub fn full_access_conflict(user_args: &[String], level: Level) -> Option<String> {
    if level != Level::On {
        return None;
    }
    let mut previous = "";
    for arg in user_args {
        let full_access = arg == "--dangerously-bypass-approvals-and-sandbox"
            || arg == "--yolo"
            || ((previous == "-s" || previous == "--sandbox") && arg == "danger-full-access")
            || arg == "--sandbox=danger-full-access"
            || arg == "-sdanger-full-access";
        if full_access {
            return Some(format!(
                "airgapped is on, and {arg} runs commands with no sandbox, so nothing would keep them off the network. Drop one of the two (puffin airgapped default off, or {ENV_VAR}=off for one run)."
            ));
        }
        previous = arg;
    }
    None
}

/// What the section sends, given the level in force and what the model last saw.
fn render(level: Level, previous: PreviousWorldStateSection<'_>) -> Option<&'static str> {
    let before = match previous {
        PreviousWorldStateSection::Known(known) => known.get("level").and_then(Value::as_str),
        PreviousWorldStateSection::Absent | PreviousWorldStateSection::Unknown => None,
    };
    match (level, before) {
        // Never restricted, or still off: a session at `off` is exactly today's.
        (Level::Off, None | Some("off")) => None,
        (_, Some(name)) if name == level.name() => None,
        // A switch, the first request of a restricted session, or the text gone after a compaction.
        _ => Some(text(level)),
    }
}

fn section(level: Level) -> WorldStateSectionContribution {
    let contribution = WorldStateSectionContribution::new(SECTION_ID, json!({ "level": level.name() }), move |previous| {
        render(level, previous).map(|text| RenderedWorldStateFragment::new("developer", (START_MARKER, END_MARKER), text))
    });
    if level == Level::Off {
        // Nothing has to stay in history for `off`; a matcher would turn a switch to `off` into
        // "absent" on the history path and swallow the message that lifts the restriction.
        contribution
    } else {
        contribution.with_retained_fragment_matcher(move |role, fragment| role == "developer" && fragment.contains(text(level)))
    }
}

struct Airgapped;

impl ContextContributor for Airgapped {
    fn contribute_world_state<'a>(
        &'a self,
        input: WorldStateContributionInput<'a>,
    ) -> ExtensionFuture<'a, Vec<WorldStateSectionContribution>> {
        Box::pin(async move {
            let level = resolve(Some(&input.thread_id.to_string())).level;
            vec![section(level)]
        })
    }
}

/// Registers the section. Called beside `cave::install` in the app server's registry and in the
/// one `puffin debug prompt-input` builds.
pub fn install<C: Sync>(registry: &mut ExtensionRegistryBuilder<C>) {
    registry.prompt_contributor(Arc::new(Airgapped));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn known(text: &str) -> Value {
        serde_json::from_str(text).unwrap_or_default()
    }

    #[test]
    fn a_session_at_off_is_told_nothing() {
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Absent), None);
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Unknown), None);
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Known(&known(r#"{"level":"off"}"#))), None);
    }

    #[test]
    fn each_change_of_level_sends_its_fragment_once() {
        let off = known(r#"{"level":"off"}"#);
        let on = known(r#"{"level":"on"}"#);
        let ddg = known(r#"{"level":"duckduckgo"}"#);
        assert_eq!(render(Level::On, PreviousWorldStateSection::Known(&off)), Some(ON_TEXT));
        assert_eq!(render(Level::On, PreviousWorldStateSection::Absent), Some(ON_TEXT));
        assert_eq!(render(Level::On, PreviousWorldStateSection::Known(&on)), None);
        assert_eq!(render(Level::DuckDuckGo, PreviousWorldStateSection::Known(&on)), Some(DUCKDUCKGO_TEXT));
        assert_eq!(render(Level::DuckDuckGo, PreviousWorldStateSection::Known(&ddg)), None);
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Known(&on)), Some(OFF_TEXT));
    }

    #[test]
    fn only_on_is_called_a_session_without_network() {
        // PUFFIN_EGRESS: no text may call `off` or `duckduckgo` air-gapped.
        assert!(ON_TEXT.contains("no network"));
        assert!(!DUCKDUCKGO_TEXT.contains("no network") && !OFF_TEXT.contains("no network"));
        assert!(summary(Level::DuckDuckGo).contains("a preference, not a barrier"));
    }

    #[test]
    fn every_form_of_the_command_parses() {
        assert_eq!(command(None::<String>, "loud"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "default"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "default loud"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "on off"), vec![USAGE.to_string()]);
        assert!(command(None::<String>, "on")[0].starts_with("No session yet"));
        assert!(command(None::<String>, "DDG")[0].starts_with("No session yet"));
    }

    #[test]
    fn the_status_names_the_level_its_source_and_what_is_not_enforced() {
        let resolved = Resolved { level: Level::On, source: puffin_airgapped::Source::Environment, invalid: vec![] };
        let lines = status_lines(&resolved, true);
        assert_eq!(lines[0], format!("Airgapped: on ({ENV_VAR})"));
        assert!(lines.iter().any(|line| line.starts_with("  on ") && line.ends_with("← in force")));
        assert!(lines.iter().any(|line| line.starts_with("NOT ENFORCED for: Full Access")));
        let resolved = Resolved { level: Level::Off, source: puffin_airgapped::Source::Default, invalid: vec!["ignored \"x\" from this session".into()] };
        let lines = status_lines(&resolved, false);
        assert_eq!(lines[0], "Airgapped: off (default)");
        assert!(!lines.iter().any(|line| line.contains("ENFORCED")));
        assert!(lines.iter().any(|line| line == "Note: ignored \"x\" from this session."));
    }

    #[test]
    fn default_keeps_the_other_keys_and_stays_above_the_tables() {
        let updated = with_default("vllm_host = \"http://x\"\n\n[night]\nwindow = \"01:00-07:00\"\n", Level::On).unwrap_or_default();
        assert_eq!(puffin_airgapped::toml_value(&updated).as_deref(), Some("on"));
        assert!(updated.contains("vllm_host = \"http://x\"") && updated.contains("[night]\nwindow"));
        let again = with_default(&updated, Level::DuckDuckGo).unwrap_or_default();
        assert_eq!(puffin_airgapped::toml_value(&again).as_deref(), Some("duckduckgo"));
        assert_eq!(again.matches(TOML_KEY).count(), 1);
    }

    #[test]
    fn full_access_is_refused_only_at_on() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        assert!(full_access_conflict(&args(&["-s", "danger-full-access"]), Level::On).is_some());
        assert!(full_access_conflict(&args(&["exec", "--dangerously-bypass-approvals-and-sandbox", "x"]), Level::On).is_some());
        assert!(full_access_conflict(&args(&["--sandbox=danger-full-access"]), Level::On).is_some());
        assert_eq!(full_access_conflict(&args(&["-s", "workspace-write"]), Level::On), None);
        assert_eq!(full_access_conflict(&args(&["-s", "danger-full-access"]), Level::DuckDuckGo), None);
        assert_eq!(full_access_conflict(&args(&["exec", "explain danger-full-access"]), Level::On), None);
    }

    #[test]
    fn old_session_files_are_pruned() {
        let dir = std::env::temp_dir().join(format!("puffin-airgapped-prune-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        std::fs::write(dir.join("a"), "on\n").unwrap_or_default();
        prune_older_than(&dir, Duration::from_secs(3600), SystemTime::now());
        assert!(dir.join("a").exists());
        prune_older_than(&dir, Duration::ZERO, SystemTime::now() + Duration::from_secs(5));
        assert!(!dir.join("a").exists());
    }
}
