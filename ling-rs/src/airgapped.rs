//! `/airgapped`: how much of the internet a session may use (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md).
//!
//! The levels and their resolution live in the leaf crate `ling-airgapped` (`airgapped/`), because
//! Codex's sandbox helper asks the same question for every command it starts (patch 0019) and must
//! not depend on the launcher. This module is what the user sees: the command, the status, the
//! `default` writer, and the World State section that tells the model when the level changes.
//!
//! It also keeps the **seals**: a session seen at `on` gets a file under the user's runtime directory,
//! which the command sandbox cannot write, and the leaf crate holds the level at `on` while that
//! file exists. Without it `ling` started in the home directory let a command rewrite its own
//! level file (`~/.mightling` is inside the working directory there). The seal is written by this
//! process (when the section below sees `on`, and by `/airgapped on`), removed by `/airgapped off`,
//! and pruned at the next launch once the process that wrote it is gone.
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
pub use ling_airgapped::ENV_VAR;
pub use ling_airgapped::Level;
pub use ling_airgapped::Resolved;
pub use ling_airgapped::TOML_KEY;
use serde_json::Value;
use serde_json::json;

const SECTION_ID: &str = "airgapped";
const START_MARKER: &str = "<airgapped>";
const END_MARKER: &str = "</airgapped>";
const SESSION_FILE_MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const USAGE: &str = "Usage: /airgapped [off|on] or /airgapped default <off|on>";

/// What the model is told when the level changes to `on`.
pub const ON_TEXT: &str = "This session has no network. ling-search, ling-fetch, ling-admin gmail, curl, git fetch and push, and package installs fail by design. Do not try them, and do not ask to run a command outside the sandbox to get around it. Work from the files here; when an answer needs something you cannot look up, say which part is from memory and may be out of date.";

/// What the model is told when the level goes back to `off`.
/// Said plainly and with the commands named: the first wording ("the earlier restriction no longer
/// applies") was delivered and the model still refused to run `curl`, quoting the `on` message.
pub const OFF_TEXT: &str = "The user has lifted this session's air-gap restriction. The earlier message about it no longer applies: commands have the network again, and curl, git fetch and push, package installs, ling-search and ling-fetch work as the system prompt describes. Run them when the task calls for it.";

fn summary(level: Level) -> &'static str {
    match level {
        Level::Off => "search through every engine SearXNG has enabled; pages fetched directly",
        Level::On => "no network for anything the agent runs: no search, no fetch, no Gmail",
    }
}

fn text(level: Level) -> &'static str {
    match level {
        Level::Off => OFF_TEXT,
        Level::On => ON_TEXT,
    }
}

/// Resolves the level for a thread (or, with none, the configured level), reading every tier.
pub fn resolve(thread_id: Option<&str>) -> Resolved {
    ling_airgapped::resolve(&[thread_id.unwrap_or_default()])
}

/// Deletes session files older than thirty days, and the seals of `ling` processes that have
/// exited. Called at launch; errors are ignored.
pub fn prune_session_files() {
    if let Some(dir) = ling_airgapped::codex_home().map(|home| home.join(ling_airgapped::SESSION_DIR)) {
        prune_older_than(&dir, SESSION_FILE_MAX_AGE, SystemTime::now());
    }
    // Without /proc (not Linux) nothing can be told about a process, and nothing is pruned; on
    // Windows the process table answers instead.
    if let Some(dir) = ling_airgapped::seal_dir() {
        if cfg!(windows) {
            prune_seals(&dir, process_alive);
        } else if Path::new("/proc/self").exists() {
            prune_seals(&dir, |pid| Path::new(&format!("/proc/{pid}")).exists());
        }
    }
}

/// Whether process `pid` is running, from the Windows process table.
#[cfg(windows)]
fn process_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::Foundation::STILL_ACTIVE;
    use windows_sys::Win32::System::Threading::GetExitCodeProcess;
    use windows_sys::Win32::System::Threading::OpenProcess;
    use windows_sys::Win32::System::Threading::PROCESS_QUERY_LIMITED_INFORMATION;
    // SAFETY: the handle is checked before use and closed once; the exit code is written into a
    // local the call owns for its duration.
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle == 0 {
            return false;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code) != 0;
        CloseHandle(handle);
        ok && code == STILL_ACTIVE as u32
    }
}

#[cfg(not(windows))]
fn process_alive(_pid: u32) -> bool {
    false
}

/// Removes the seals whose writer is no longer running: "sealed until `ling` is restarted".
fn prune_seals(dir: &Path, alive: impl Fn(u32) -> bool) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let writer = std::fs::read_to_string(entry.path()).ok().and_then(|text| text.trim().parse::<u32>().ok());
        if !writer.is_some_and(&alive) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Holds a session at `on`: writes its seal, naming this process. Returns the seal's path, or None
/// when there is no place a command cannot write (no runtime directory).
fn seal(thread_id: &str) -> Option<std::path::PathBuf> {
    let path = ling_airgapped::seal_file(thread_id)?;
    let own = format!("{}\n", std::process::id());
    if std::fs::read_to_string(&path).is_ok_and(|text| text == own) {
        return Some(path);
    }
    std::fs::create_dir_all(path.parent()?).ok()?;
    std::fs::write(&path, own).ok()?;
    Some(path)
}

/// Lifts the hold. Only `/airgapped off`, typed by the user, calls this.
fn unseal(thread_id: &str) {
    if let Some(path) = ling_airgapped::seal_file(thread_id) {
        let _ = std::fs::remove_file(path);
    }
}

/// What `/airgapped` says about whether a command could change the level it runs under: checked
/// for this session, not fixed text.
fn guard_lines(level: Level, seal: Option<&Path>, exposed: bool, can_seal: bool) -> Vec<String> {
    if let Some(seal) = seal {
        return vec![format!(
            "Held: commands cannot change this level. It is kept outside the folders they can write ({}); /airgapped off lifts it.",
            seal.display()
        )];
    }
    if level == Level::On && exposed && !can_seal {
        return vec![
            "NOT ENFORCED against a command rewriting the level: ling was started in a folder that contains its own settings, and there is no runtime directory to hold the level outside it. Start ling in a project folder, or set DREAMFERENCE_MIGHTLING_AIRGAPPED=on.".to_string(),
        ];
    }
    Vec::new()
}

/// [`guard_lines`] for a thread, read from the machine.
fn guard_lines_now(level: Level, thread_id: Option<&str>) -> Vec<String> {
    let seal = thread_id.and_then(ling_airgapped::seal_file).filter(|path| path.is_file());
    guard_lines(
        level,
        seal.as_deref(),
        ling_airgapped::level_files_exposed(),
        ling_airgapped::seal_dir().is_some(),
    )
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
    // On Windows only the elevated sandbox, set up, takes a command's network away (§7.4).
    let wanted = match words.as_slice() {
        [name] => Level::parse(name),
        [default, name] if default.eq_ignore_ascii_case("default") => Level::parse(name),
        _ => None,
    };
    if cfg!(windows)
        && wanted == Some(Level::On)
        && let Some(lines) = windows_on_refusal(windows_sandbox_of(&[], &config_layers()), windows_sandbox_set_up_now())
    {
        return lines;
    }
    match words.as_slice() {
        [] => {
            let resolved = resolve(thread_id);
            let guard = guard_lines_now(resolved.level, thread_id);
            status_lines(&resolved, thread_id.is_some(), &guard)
        }
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

/// Runs `ling airgapped …` from a shell: the status of the configured level, or `default
/// <level>`. Returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let lines = match args.first().map(String::as_str) {
        None => command(None::<String>, ""),
        Some("default") => command(None::<String>, &args.join(" ")),
        // The level a thread is at, for a client that is not inside the session: the desktop
        // app's Work window shows it in its title bar (desktop/electron/src/airgapped.ts).
        Some("--thread") if args.len() == 2 => command(args.get(1), ""),
        Some(_) => vec![
            "Usage: ling airgapped [default <off|on>] [--thread <id>]".to_string(),
            format!("For one run: {ENV_VAR}=on ling exec …; inside a session: /airgapped <level>."),
        ],
    };
    let failed = lines.first().is_some_and(|line| line.starts_with("Usage:"));
    for line in lines {
        println!("{line}");
    }
    i32::from(failed) * 2
}

/// What takes the network away from a sandboxed command at `on`.
const ENFORCED_BY: &str = if cfg!(windows) { "the Windows sandbox's offline account" } else { "bwrap --unshare-net" };

fn status_lines(resolved: &Resolved, in_session: bool, guard: &[String]) -> Vec<String> {
    let mut lines = vec![format!("Airgapped: {} ({})", resolved.level.name(), resolved.source.label())];
    for level in Level::ALL {
        let marker = if level == resolved.level { "   ← in force" } else { "" };
        lines.push(format!("  {:<11} {}{marker}", level.name(), summary(level)));
    }
    if resolved.level == Level::On {
        lines.push(format!("Enforced: sandboxed commands run with no network ({}).", ENFORCED_BY));
        lines.push("NOT ENFORCED for: Full Access, a command you approve to run outside the sandbox, MCP servers, and ling's own connection to the model server.".to_string());
    }
    lines.extend(guard.iter().cloned());
    lines.extend(resolved.invalid.iter().map(|note| format!("Note: {note}.")));
    lines.push("Not covered at any level: the web chat, MCP servers you configured.".to_string());
    lines.push(if in_session {
        "Change: /airgapped on (this session) or /airgapped default on (new sessions).".to_string()
    } else {
        "Change: ling airgapped default <level> (new sessions), or /airgapped <level> inside a session.".to_string()
    });
    lines
}

fn set_session(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let Some(path) = thread_id.and_then(ling_airgapped::session_file) else {
        return vec!["No session yet: send a message first, or use /airgapped default <level>.".to_string()];
    };
    // The user typed this, in a process no command controls: the one place a hold may be lifted.
    // Sealed before the file is written, lifted before it is, so no command starts in between
    // under a looser level than the user asked for.
    let thread = thread_id.unwrap_or_default();
    if level == Level::On {
        seal(thread);
    } else {
        unseal(thread);
    }
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| std::fs::write(&path, format!("{}\n", level.name())));
    if let Err(err) = written {
        return vec![format!("Could not save the level to {}: {err}", path.display())];
    }
    // With no place for a seal, an exposed level file may tighten but not loosen (leaf crate).
    let now = resolve(thread_id);
    if now.level != level {
        let mut lines = vec![format!(
            "Airgapped stays at {} ({}): ling was started in a folder that contains its own settings, so commands could have written this session's level and it is not trusted to loosen.",
            now.level.name(),
            now.source.label()
        )];
        lines.push(format!(
            "To change it: ling airgapped default {0}, or {ENV_VAR}={0}, then restart ling.",
            level.name()
        ));
        return lines;
    }
    let mut lines = vec![format!(
        "Airgapped: {} for this session, from the next command the agent starts.",
        level.name()
    )];
    lines.extend(guard_lines_now(level, thread_id));
    match level {
        Level::On => lines.push(
            "Sandboxed commands now run with no network. Not covered: Full Access, commands you approve to run outside the sandbox, MCP servers.".to_string(),
        ),
        Level::Off => {}
    }
    lines
}

fn set_default(thread_id: Option<&str>, level: Level) -> Vec<String> {
    let Some(path) = ling_airgapped::user_config_file() else {
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

/// The TOML text with `mightling_airgapped` set to `level`, everything else kept as written. The key
/// is inserted before the first table, where a top-level key has to be.
fn with_default(existing: &str, level: Level) -> Result<String, toml_edit::TomlError> {
    let mut document: toml_edit::DocumentMut = existing.parse()?;
    document[TOML_KEY] = toml_edit::value(level.name());
    Ok(document.to_string())
}

/// What `ling` prints before a session that starts at `on` (§5.3), so the user sees whether the
/// level holds before typing anything; nothing at the other levels. `exposed` and `can_seal` are
/// as for [`guard_lines`]: the seal itself is written with the first message, so at start the only
/// hole that depends on this machine is a level file a command could rewrite with no seal to hold it.
fn startup_lines(resolved: &Resolved, exposed: bool, can_seal: bool) -> Vec<String> {
    if resolved.level != Level::On {
        return Vec::new();
    }
    let mut lines = vec![format!(
        "🔒 Airgapped: on ({}). Enforced: sandboxed commands run with no network.",
        resolved.source.label()
    )];
    lines.extend(guard_lines(Level::On, None, exposed, can_seal));
    lines.push(
        "NOT ENFORCED for: a command you approve to run outside the sandbox, MCP servers you configured.".to_string(),
    );
    lines.extend(resolved.invalid.iter().map(|note| format!("Note: {note}.")));
    lines
}

/// [`startup_lines`] for the configured level, read from the machine.
pub fn startup_lines_now(resolved: &Resolved) -> Vec<String> {
    startup_lines(resolved, ling_airgapped::level_files_exposed(), ling_airgapped::seal_dir().is_some())
}

/// Whether a session starting at this configured level is told about Gmail (§5.2): not at `on`,
/// where `ling-admin gmail` cannot reach the service, so the prompt does not offer what fails. A
/// session switched to `on` later keeps the section, and the `on` fragment says Gmail is unavailable.
pub fn offers_gmail(level: Level) -> bool {
    level != Level::On
}

/// Why `on` and Full Access are never had together; every refusal of one for the other says it.
const INCOMPATIBLE: &str = "Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible";

/// What the permissions picker says beside a disabled Full Access row.
pub const FULL_ACCESS_DISABLED: &str = "/airgapped is on, and Full Access has no sandbox to keep commands off the network; run /airgapped off first";

/// The id Codex gives its Full Access preset, and the permission profile and sandbox mode it selects.
const FULL_ACCESS_PRESET: &str = "full-access";
const FULL_ACCESS_PROFILE: &str = ":danger-full-access";
const FULL_ACCESS_SANDBOX: &str = "danger-full-access";

/// The reason `ling` must not start with these arguments at a configured `on`, if there is one.
/// Full Access is chosen by a flag, a `-c` override or a configuration file, so all three are read.
pub fn full_access_conflict(user_args: &[String], level: Level) -> Option<String> {
    if level != Level::On {
        return None;
    }
    let source = full_access_source(user_args, &config_layers())?;
    Some(format!(
        "airgapped is on, and {source} selects Full Access. {INCOMPATIBLE}. Choose one: another sandbox (-s workspace-write), or ling airgapped default off ({ENV_VAR}=off for one run)."
    ))
}

/// What selects Full Access for this launch, if anything does, following Codex's precedence: a
/// sandbox flag, then `-c`, then the configuration files (the selected profile's keys before the
/// top level's, a later file before an earlier one). `layers` are (name, table), lowest first.
fn full_access_source(user_args: &[String], layers: &[(String, toml::Table)]) -> Option<String> {
    let mut profile: Option<String> = None;
    let mut cli_override: Option<(String, bool)> = None;
    let mut args = user_args.iter().map(String::as_str);
    while let Some(arg) = args.next() {
        let sandbox = match arg {
            "--dangerously-bypass-approvals-and-sandbox" | "--yolo" => return Some(arg.to_string()),
            // A sandbox named on the command line overrides every configuration file.
            "--full-auto" => return None,
            "-s" | "--sandbox" => args.next(),
            "-p" | "--profile" => {
                profile = args.next().map(str::to_string);
                None
            }
            "-c" | "--config" => {
                if let Some(found) = args.next().and_then(config_override) {
                    cli_override = Some(found);
                }
                None
            }
            _ => arg.strip_prefix("--sandbox=").or_else(|| {
                arg.strip_prefix("-s")
                    .filter(|mode| matches!(*mode, "read-only" | "workspace-write" | FULL_ACCESS_SANDBOX))
            }),
        };
        if let Some(mode) = sandbox {
            return (mode == FULL_ACCESS_SANDBOX).then(|| format!("--sandbox {mode}"));
        }
        if let Some(name) = arg.strip_prefix("--profile=") {
            profile = Some(name.to_string());
        }
        if let Some(found) = arg.strip_prefix("--config=").and_then(config_override) {
            cli_override = Some(found);
        }
    }
    if let Some((text, full_access)) = cli_override {
        return full_access.then(|| format!("-c {text}"));
    }
    let profile = profile.or_else(|| {
        layers.iter().rev().find_map(|(_, table)| table.get("profile")?.as_str().map(str::to_string))
    });
    if let Some(name) = &profile {
        for (file, table) in layers.iter().rev() {
            let section = table.get("profiles").and_then(|profiles| profiles.get(name.as_str())).and_then(toml::Value::as_table);
            if let Some((key, full_access)) = section.and_then(table_mode) {
                return full_access.then(|| format!("{key} in profile \"{name}\" of {file}"));
            }
        }
    }
    for (file, table) in layers.iter().rev() {
        if let Some((key, full_access)) = table_mode(table) {
            return full_access.then(|| format!("{key} in {file}"));
        }
    }
    None
}

/// Whether a configuration table selects Full Access, and by which key, if it says either way.
fn table_mode(table: &toml::Table) -> Option<(&'static str, bool)> {
    if let Some(value) = table.get("default_permissions").and_then(toml::Value::as_str) {
        return Some(("default_permissions", value == FULL_ACCESS_PROFILE));
    }
    let value = table.get("sandbox_mode").and_then(toml::Value::as_str)?;
    Some(("sandbox_mode", value == FULL_ACCESS_SANDBOX))
}

/// A `-c key=value` that decides the sandbox: the text and whether it selects Full Access. The value
/// is read as TOML and, failing that, as a bare string, as Codex reads it.
fn config_override(text: &str) -> Option<(String, bool)> {
    let (key, value) = text.split_once('=')?;
    let key = key.trim();
    if key != "sandbox_mode" && key != "default_permissions" {
        return None;
    }
    let parsed = toml::from_str::<toml::Table>(&format!("v = {value}"))
        .ok()
        .and_then(|table| table.get("v")?.as_str().map(str::to_string))
        .unwrap_or_else(|| value.trim().to_string());
    let mut table = toml::Table::new();
    table.insert(key.to_string(), toml::Value::String(parsed));
    table_mode(&table).map(|(_, full_access)| (text.to_string(), full_access))
}

/// The configuration files Codex layers, lowest first: the system file, the user's, and the
/// project's `.codex/config.toml` from the repository root down to the working directory. A project
/// file counts even where Codex would not trust it: refusing is the safe side to be wrong on.
fn config_layers() -> Vec<(String, toml::Table)> {
    let mut files = vec![std::path::PathBuf::from("/etc/codex/config.toml")];
    files.extend(ling_airgapped::codex_home().map(|home| home.join("config.toml")));
    if let Ok(cwd) = std::env::current_dir() {
        let mut project = Vec::new();
        for dir in cwd.ancestors() {
            project.push(dir.join(".codex").join("config.toml"));
            if dir.join(".git").exists() {
                break;
            }
        }
        files.extend(project.into_iter().rev());
    }
    files
        .into_iter()
        .filter_map(|path| {
            let table = std::fs::read_to_string(&path).ok()?.parse::<toml::Table>().ok()?;
            Some((path.display().to_string(), table))
        })
        .collect()
}

/// The sandbox Codex runs commands in on Windows, as it resolves it (`resolve_windows_sandbox_mode`
/// in core/src/windows_sandbox.rs). Only the elevated one can take a command's network away: it
/// runs the command as a separate offline account that the firewall blocks (patch 0024;
/// specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §7.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowsSandbox {
    Elevated,
    Unelevated,
    Mxc,
    None,
}

/// The legacy features that chose the Windows sandbox before `[windows] sandbox`.
const ELEVATED_FEATURE: &str = "elevated_windows_sandbox";
const UNELEVATED_FEATURES: [&str; 2] = ["experimental_windows_sandbox", "enable_experimental_windows_sandbox"];

/// What to do about a Windows sandbox that cannot enforce `on`.
const WINDOWS_SETUP: &str = "Set the Windows sandbox up: run install.ps1 again and accept the Administrator prompt";
const WINDOWS_SELECT: &str = "Choose the elevated sandbox: [windows] sandbox = \"elevated\" in config.toml";

/// The Windows sandbox these arguments and configuration files select: `[windows] sandbox`, else
/// the legacy features, each from the last layer that sets it, with `-c` overrides last.
pub fn windows_sandbox_of(user_args: &[String], layers: &[(String, toml::Table)]) -> WindowsSandbox {
    let overrides = crate::option_values(user_args, &["-c", "--config"]).filter_map(override_table);
    let tables: Vec<toml::Table> = layers.iter().map(|(_, table)| table.clone()).chain(overrides).collect();
    let mut mode = None;
    let mut features = std::collections::BTreeMap::new();
    for table in &tables {
        if let Some(chosen) = table.get("windows").and_then(|windows| windows.get("sandbox")).and_then(toml::Value::as_str) {
            mode = Some(chosen.to_string());
        }
        for (key, value) in table.get("features").and_then(toml::Value::as_table).into_iter().flatten() {
            if let Some(on) = value.as_bool() {
                features.insert(key.clone(), on);
            }
        }
    }
    let enabled = |key: &str| features.get(key).copied().unwrap_or(false);
    match mode.as_deref() {
        Some("elevated") => WindowsSandbox::Elevated,
        Some("unelevated") => WindowsSandbox::Unelevated,
        Some("mxc") => WindowsSandbox::Mxc,
        _ if enabled(ELEVATED_FEATURE) => WindowsSandbox::Elevated,
        _ if UNELEVATED_FEATURES.iter().any(|key| enabled(key)) => WindowsSandbox::Unelevated,
        _ => WindowsSandbox::None,
    }
}

/// A `-c key.path=value` as the table it stands for. The value is read as TOML and, failing that,
/// as a bare string, as Codex reads it.
fn override_table(text: &str) -> Option<toml::Table> {
    let (key, value) = text.split_once('=')?;
    let mut node = toml::from_str::<toml::Table>(&format!("v = {value}"))
        .ok()
        .and_then(|table| table.get("v").cloned())
        .unwrap_or_else(|| toml::Value::String(value.trim().to_string()));
    for part in key.trim().split('.').rev() {
        let mut table = toml::Table::new();
        table.insert(part.trim().to_string(), node);
        node = toml::Value::Table(table);
    }
    match node {
        toml::Value::Table(table) => Some(table),
        _ => None,
    }
}

/// Why a command could keep the network at `on` with this Windows sandbox, and what to do, if it
/// could. `set_up` is whether the elevated sandbox's accounts and firewall rules exist.
fn windows_sandbox_gap(sandbox: WindowsSandbox, set_up: bool) -> Option<(&'static str, &'static str)> {
    match sandbox {
        WindowsSandbox::Elevated if set_up => None,
        WindowsSandbox::Elevated => Some(("the Windows sandbox is not set up on this machine", WINDOWS_SETUP)),
        WindowsSandbox::Unelevated => Some(("the non-admin sandbox cannot take a command's network away", WINDOWS_SELECT)),
        WindowsSandbox::Mxc => Some(("the MXC sandbox is not a security boundary yet", WINDOWS_SELECT)),
        WindowsSandbox::None if set_up => Some(("no Windows sandbox is selected, so commands run without one", WINDOWS_SELECT)),
        WindowsSandbox::None => Some(("no Windows sandbox is set up, so commands run without one", WINDOWS_SETUP)),
    }
}

/// Whether the elevated Windows sandbox is set up for this `CODEX_HOME`, as Codex itself checks.
#[cfg(windows)]
pub fn windows_sandbox_set_up(codex_home: &Path) -> bool {
    codex_windows_sandbox::sandbox_setup_is_complete(codex_home)
}

#[cfg(not(windows))]
pub fn windows_sandbox_set_up(_codex_home: &Path) -> bool {
    false
}

fn windows_sandbox_set_up_now() -> bool {
    ling_airgapped::codex_home().is_some_and(|home| windows_sandbox_set_up(&home))
}

/// The reason `ling` must not start with these arguments at a configured `on` on Windows, if
/// there is one: the sandbox they select would leave commands their network (§7.4).
pub fn windows_sandbox_conflict(user_args: &[String], level: Level) -> Option<String> {
    if !cfg!(windows) || level != Level::On {
        return None;
    }
    let sandbox = windows_sandbox_of(user_args, &config_layers());
    let (gap, remedy) = windows_sandbox_gap(sandbox, windows_sandbox_set_up_now())?;
    Some(format!(
        "airgapped is on, and {gap}, so commands would keep the network. {remedy}, or ling airgapped default off ({ENV_VAR}=off for one run)."
    ))
}

/// What `ling` says at every start on Windows when commands run without a sandbox (§7.1).
pub fn windows_sandbox_lines(user_args: &[String]) -> Vec<String> {
    if !cfg!(windows) {
        return Vec::new();
    }
    let sandbox = windows_sandbox_of(user_args, &config_layers());
    windows_sandbox_notice(sandbox, windows_sandbox_set_up_now())
}

fn windows_sandbox_notice(sandbox: WindowsSandbox, set_up: bool) -> Vec<String> {
    match (sandbox, windows_sandbox_gap(sandbox, set_up)) {
        (WindowsSandbox::Elevated | WindowsSandbox::None, Some((gap, remedy))) => {
            vec![format!("⚠️ {}{}; /airgapped on is refused until it is. {remedy}.", gap[..1].to_uppercase(), &gap[1..])]
        }
        // The non-admin and MXC sandboxes are the user's choice; only `on` is refused with them.
        _ => Vec::new(),
    }
}

/// `/airgapped on` refused on Windows when the configured sandbox cannot enforce it. Read from the
/// configuration files: a `-c` given when the session started is not visible here.
fn windows_on_refusal(sandbox: WindowsSandbox, set_up: bool) -> Option<Vec<String>> {
    let (gap, remedy) = windows_sandbox_gap(sandbox, set_up)?;
    Some(vec![
        format!("Airgapped not changed: {gap}, so commands would keep the network."),
        format!("{remedy}, then restart ling."),
    ])
}

/// Selects the elevated Windows sandbox in a `config.toml` that chooses none (§7.1), once setup
/// has made it usable. A choice already in the file, new or legacy, is left alone.
pub fn select_elevated_sandbox(doc: &mut toml_edit::DocumentMut) {
    let chosen = doc.get("windows").and_then(|windows| windows.get("sandbox")).is_some()
        || doc.get("features").is_some_and(|features| {
            std::iter::once(ELEVATED_FEATURE).chain(UNELEVATED_FEATURES).any(|key| features.get(key).is_some())
        });
    if chosen {
        return;
    }
    if !doc.get("windows").is_some_and(toml_edit::Item::is_table) {
        doc.insert("windows", toml_edit::Item::Table(toml_edit::Table::new()));
    }
    doc["windows"]["sandbox"] = toml_edit::value("elevated");
}

/// Why the permissions picker offers Full Access disabled, if it does: this session is at `on`.
/// The TUI asks for each built-in preset, by its id (patch 0019).
pub fn full_access_refusal<T: std::fmt::Display>(thread_id: Option<T>, preset: &str) -> Option<String> {
    if preset != FULL_ACCESS_PRESET {
        return None;
    }
    let thread_id = thread_id.map(|id| id.to_string());
    (resolve(thread_id.as_deref()).level == Level::On).then(|| FULL_ACCESS_DISABLED.to_string())
}

/// [`command`] inside a TUI session, which knows whether it runs in Full Access (`full_access`).
/// There `/airgapped on` is refused, not claimed: the sandbox is what enforces it, and Full Access
/// has none.
pub fn session_command<T: std::fmt::Display>(thread_id: Option<T>, args: &str, full_access: bool) -> Vec<String> {
    let thread_id = thread_id.map(|id| id.to_string());
    if !full_access {
        return command(thread_id, args);
    }
    let words: Vec<&str> = args.split_whitespace().collect();
    let wanted = match words.as_slice() {
        [name] => Level::parse(name),
        [default, name] if default.eq_ignore_ascii_case("default") => Level::parse(name),
        _ => None,
    };
    if wanted == Some(Level::On) {
        return full_access_lines("Airgapped not changed: this session runs in Full Access.");
    }
    let mut lines = command(thread_id.as_deref(), args);
    if words.is_empty() && resolve(thread_id.as_deref()).level == Level::On {
        lines.extend(full_access_lines("NOT ENFORCED in this session: it runs in Full Access."));
    }
    lines
}

fn full_access_lines(first: &str) -> Vec<String> {
    vec![
        format!("{first} {INCOMPATIBLE}."),
        "To air-gap it, choose another mode with /permissions first, then /airgapped on.".to_string(),
    ]
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
            let thread_id = input.thread_id.to_string();
            let level = resolve(Some(&thread_id)).level;
            // Before the turn's first command: from here on a command that rewrites the level's
            // files changes nothing.
            if level == Level::On {
                seal(&thread_id);
            }
            vec![section(level)]
        })
    }
}

/// Registers the section. Called beside `cave::install` in the app server's registry and in the
/// one `ling debug prompt-input` builds.
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
        assert_eq!(render(Level::On, PreviousWorldStateSection::Known(&off)), Some(ON_TEXT));
        assert_eq!(render(Level::On, PreviousWorldStateSection::Absent), Some(ON_TEXT));
        assert_eq!(render(Level::On, PreviousWorldStateSection::Known(&on)), None);
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Known(&on)), Some(OFF_TEXT));
        // A session resumed from before the `duckduckgo` level was removed is told search is back.
        let ddg = known(r#"{"level":"duckduckgo"}"#);
        assert_eq!(render(Level::Off, PreviousWorldStateSection::Known(&ddg)), Some(OFF_TEXT));
    }

    #[test]
    fn only_on_is_called_a_session_without_network() {
        // MIGHTLING_EGRESS: no text may call `off` air-gapped.
        assert!(ON_TEXT.contains("no network"));
        assert!(!OFF_TEXT.contains("no network"));
    }

    #[test]
    fn every_form_of_the_command_parses() {
        assert_eq!(command(None::<String>, "loud"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "default"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "default loud"), vec![USAGE.to_string()]);
        assert_eq!(command(None::<String>, "on off"), vec![USAGE.to_string()]);
        assert!(command(None::<String>, "on")[0].starts_with(&first_line_of_on_without_a_session()));
        assert!(command(None::<String>, "OFF")[0].starts_with("No session yet"));
        // The removed level is an unknown word.
        assert_eq!(command(None::<String>, "duckduckgo"), vec![USAGE.to_string()]);
    }

    #[test]
    fn the_status_names_the_level_its_source_and_what_is_not_enforced() {
        let resolved = Resolved { level: Level::On, source: ling_airgapped::Source::Environment, invalid: vec![] };
        let lines = status_lines(&resolved, true, &[]);
        assert_eq!(lines[0], format!("Airgapped: on ({ENV_VAR})"));
        assert!(lines.iter().any(|line| line.starts_with("  on ") && line.ends_with("← in force")));
        assert!(lines.iter().any(|line| line.starts_with("NOT ENFORCED for: Full Access")));
        let resolved = Resolved { level: Level::Off, source: ling_airgapped::Source::Default, invalid: vec!["ignored \"x\" from this session".into()] };
        let lines = status_lines(&resolved, false, &["Held: x".to_string()]);
        assert!(lines.iter().any(|line| line == "Held: x"));
        assert_eq!(lines[0], "Airgapped: off (default)");
        assert!(!lines.iter().any(|line| line.contains("ENFORCED")));
        assert!(lines.iter().any(|line| line == "Note: ignored \"x\" from this session."));
    }

    #[test]
    fn a_session_starting_at_on_is_told_what_holds_and_what_does_not() {
        let on = Resolved { level: Level::On, source: ling_airgapped::Source::Environment, invalid: vec![] };
        let lines = startup_lines(&on, false, true);
        assert_eq!(lines[0], format!("🔒 Airgapped: on ({ENV_VAR}). Enforced: sandboxed commands run with no network."));
        assert!(lines[1].starts_with("NOT ENFORCED for: a command you approve to run outside the sandbox"));
        assert_eq!(lines.len(), 2);
        // The machine's own hole is named between the two when there is no place for a seal.
        let exposed = startup_lines(&on, true, false);
        assert!(exposed[1].starts_with("NOT ENFORCED against a command rewriting the level"));
        assert_eq!(exposed.len(), 3);
        // Nothing at `off`.
        let off = Resolved { level: Level::Off, source: ling_airgapped::Source::Default, invalid: vec![] };
        assert!(startup_lines(&off, true, false).is_empty());
    }

    #[test]
    fn a_session_starting_at_on_is_not_offered_gmail() {
        assert!(!offers_gmail(Level::On));
        assert!(offers_gmail(Level::Off));
    }

    #[test]
    fn default_keeps_the_other_keys_and_stays_above_the_tables() {
        let updated = with_default("vllm_host = \"http://x\"\n\n[night]\nwindow = \"01:00-07:00\"\n", Level::On).unwrap_or_default();
        assert_eq!(ling_airgapped::toml_value(&updated).as_deref(), Some("on"));
        assert!(updated.contains("vllm_host = \"http://x\"") && updated.contains("[night]\nwindow"));
        let again = with_default(&updated, Level::Off).unwrap_or_default();
        assert_eq!(ling_airgapped::toml_value(&again).as_deref(), Some("off"));
        assert_eq!(again.matches(TOML_KEY).count(), 1);
    }

    #[test]
    fn full_access_is_refused_only_at_on() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        assert!(full_access_conflict(&args(&["-s", "danger-full-access"]), Level::On).is_some());
        assert!(full_access_conflict(&args(&["exec", "--dangerously-bypass-approvals-and-sandbox", "x"]), Level::On).is_some());
        assert!(full_access_conflict(&args(&["--sandbox=danger-full-access"]), Level::On).is_some());
        assert_eq!(full_access_conflict(&args(&["-s", "workspace-write"]), Level::On), None);
        assert_eq!(full_access_conflict(&args(&["-s", "danger-full-access"]), Level::Off), None);
        let refusal = full_access_conflict(&args(&["--yolo"]), Level::On).unwrap_or_default();
        assert!(refusal.contains("--yolo selects Full Access") && refusal.contains("incompatible"));
    }

    fn layers(files: &[(&str, &str)]) -> Vec<(String, toml::Table)> {
        files.iter().map(|(name, text)| (name.to_string(), text.parse().unwrap_or_default())).collect()
    }

    #[test]
    fn the_windows_sandbox_is_read_as_codex_reads_it() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        assert_eq!(windows_sandbox_of(&[], &layers(&[])), WindowsSandbox::None);
        let elevated = layers(&[("config.toml", "[windows]\nsandbox = \"elevated\"\n")]);
        assert_eq!(windows_sandbox_of(&[], &elevated), WindowsSandbox::Elevated);
        // `-c` comes last, as a key path or an inline table.
        assert_eq!(windows_sandbox_of(&args(&["-c", "windows.sandbox=unelevated"]), &elevated), WindowsSandbox::Unelevated);
        assert_eq!(windows_sandbox_of(&args(&["--config=windows={sandbox=\"mxc\"}"]), &elevated), WindowsSandbox::Mxc);
        // The legacy features, when `[windows] sandbox` says nothing; it wins when it does.
        let legacy = layers(&[("config.toml", "[features]\nexperimental_windows_sandbox = true\n")]);
        assert_eq!(windows_sandbox_of(&[], &legacy), WindowsSandbox::Unelevated);
        assert_eq!(windows_sandbox_of(&args(&["-c", "features.elevated_windows_sandbox=true"]), &legacy), WindowsSandbox::Elevated);
        let both = layers(&[("a.toml", "[features]\nelevated_windows_sandbox = true\n"), ("b.toml", "[windows]\nsandbox = \"unelevated\"\n")]);
        assert_eq!(windows_sandbox_of(&[], &both), WindowsSandbox::Unelevated);
        // A later file overrides an earlier one.
        let later = layers(&[("a.toml", "[windows]\nsandbox = \"unelevated\"\n"), ("b.toml", "[windows]\nsandbox = \"elevated\"\n")]);
        assert_eq!(windows_sandbox_of(&[], &later), WindowsSandbox::Elevated);
    }

    #[test]
    fn at_on_only_the_elevated_sandbox_set_up_is_accepted() {
        assert_eq!(windows_sandbox_gap(WindowsSandbox::Elevated, true), None);
        let refused = [
            (WindowsSandbox::Elevated, false, "not set up", WINDOWS_SETUP),
            (WindowsSandbox::Unelevated, true, "the non-admin sandbox cannot take a command's network away", WINDOWS_SELECT),
            (WindowsSandbox::Mxc, true, "MXC", WINDOWS_SELECT),
            (WindowsSandbox::None, false, "no Windows sandbox is set up", WINDOWS_SETUP),
            (WindowsSandbox::None, true, "no Windows sandbox is selected", WINDOWS_SELECT),
        ];
        for (sandbox, set_up, reason, remedy) in refused {
            let (gap, said) = windows_sandbox_gap(sandbox, set_up).unwrap_or_default();
            assert!(gap.contains(reason) && said == remedy, "{sandbox:?} {set_up}: {gap}");
            let lines = windows_on_refusal(sandbox, set_up).unwrap_or_default();
            assert!(lines[0].starts_with("Airgapped not changed: ") && lines[1].starts_with(remedy));
        }
        assert_eq!(windows_on_refusal(WindowsSandbox::Elevated, true), None);
    }

    #[test]
    fn every_start_says_when_commands_run_without_a_sandbox() {
        assert_eq!(
            windows_sandbox_notice(WindowsSandbox::None, false),
            vec![format!("⚠️ No Windows sandbox is set up, so commands run without one; /airgapped on is refused until it is. {WINDOWS_SETUP}.")]
        );
        assert_eq!(windows_sandbox_notice(WindowsSandbox::Elevated, false).len(), 1);
        // A sandbox the user chose, or the elevated one set up, says nothing at start.
        assert!(windows_sandbox_notice(WindowsSandbox::Unelevated, true).is_empty());
        assert!(windows_sandbox_notice(WindowsSandbox::Elevated, true).is_empty());
        // Off Windows there is nothing to say and nothing to refuse.
        if !cfg!(windows) {
            assert!(windows_sandbox_lines(&[]).is_empty());
            assert_eq!(windows_sandbox_conflict(&[], Level::On), None);
        }
    }

    #[test]
    fn the_elevated_sandbox_is_selected_only_where_nothing_is_chosen() {
        let select = |text: &str| {
            let mut doc: toml_edit::DocumentMut = text.parse().unwrap_or_default();
            select_elevated_sandbox(&mut doc);
            doc.to_string()
        };
        let written = select("model = \"x\"\n");
        assert_eq!(written.parse::<toml::Table>().ok().and_then(|t| t["windows"]["sandbox"].as_str().map(str::to_string)).as_deref(), Some("elevated"));
        assert!(written.starts_with("model = \"x\"\n"));
        for chosen in ["[windows]\nsandbox = \"unelevated\"\n", "[features]\nexperimental_windows_sandbox = true\n", "[features]\nelevated_windows_sandbox = false\n"] {
            assert_eq!(select(chosen), chosen);
        }
    }

    #[test]
    fn full_access_is_found_in_flags_overrides_and_configuration_files() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        let none = layers(&[]);
        assert_eq!(full_access_source(&args(&["exec", "explain danger-full-access"]), &none), None);
        assert_eq!(full_access_source(&args(&["-sdanger-full-access"]), &none).as_deref(), Some("--sandbox danger-full-access"));
        assert_eq!(full_access_source(&args(&["-c", "sandbox_mode=danger-full-access"]), &none).as_deref(), Some("-c sandbox_mode=danger-full-access"));
        assert_eq!(full_access_source(&args(&["--config=default_permissions=\":danger-full-access\""]), &none).as_deref(), Some("-c default_permissions=\":danger-full-access\""));
        assert_eq!(full_access_source(&args(&["-c", "model=x"]), &none), None);

        let user = layers(&[("user.toml", "sandbox_mode = \"danger-full-access\"\n")]);
        assert_eq!(full_access_source(&args(&[]), &user).as_deref(), Some("sandbox_mode in user.toml"));
        // A sandbox named on the command line, or by -c, overrides the file.
        assert_eq!(full_access_source(&args(&["-s", "workspace-write"]), &user), None);
        assert_eq!(full_access_source(&args(&["--full-auto"]), &user), None);
        assert_eq!(full_access_source(&args(&["-c", "sandbox_mode=\"workspace-write\""]), &user), None);
        // A later file overrides an earlier one.
        let project = layers(&[
            ("user.toml", "sandbox_mode = \"danger-full-access\"\n"),
            ("project.toml", "default_permissions = \":workspace\"\n"),
        ]);
        assert_eq!(full_access_source(&args(&[]), &project), None);

        // The selected profile's keys come before the top level's, whichever file selects it.
        let profiled = layers(&[
            ("user.toml", "sandbox_mode = \"workspace-write\"\n[profiles.wild]\nsandbox_mode = \"danger-full-access\"\n"),
            ("project.toml", "profile = \"wild\"\n"),
        ]);
        assert_eq!(full_access_source(&args(&[]), &profiled).as_deref(), Some("sandbox_mode in profile \"wild\" of user.toml"));
        assert_eq!(full_access_source(&args(&["-p", "tame"]), &profiled), None);
        assert!(full_access_source(&args(&["--profile=wild"]), &profiled).is_some());
    }

    #[test]
    fn full_access_and_on_are_refused_inside_a_session() {
        assert_eq!(full_access_refusal(None::<String>, "auto"), None);
        assert_eq!(full_access_refusal(None::<String>, "read-only"), None);
        // In Full Access, `on` is refused for the session and as the default, with the reason.
        for args in ["on", "ON", "default on"] {
            let lines = session_command(None::<String>, args, true);
            assert!(lines[0].starts_with("Airgapped not changed: this session runs in Full Access."), "{args}");
            assert!(lines[0].contains("incompatible") && lines[1].contains("/permissions"), "{args}");
        }
        // Everything else is the ordinary command.
        assert_eq!(session_command(None::<String>, "loud", true), vec![USAGE.to_string()]);
        assert!(session_command(None::<String>, "off", true)[0].starts_with("No session yet"));
        assert!(session_command(None::<String>, "on", false)[0].starts_with(&first_line_of_on_without_a_session()));
    }

    /// What `/airgapped on` says first with no session yet. On Windows, without the elevated
    /// sandbox selected and set up on the machine running the tests, the refusal comes first
    /// (§7.4); `at_on_only_the_elevated_sandbox_set_up_is_accepted` covers its wording.
    fn first_line_of_on_without_a_session() -> String {
        let refusal = if cfg!(windows) { windows_on_refusal(windows_sandbox_of(&[], &config_layers()), windows_sandbox_set_up_now()) } else { None };
        refusal.map_or_else(|| "No session yet".to_string(), |lines| lines[0].clone())
    }

    #[test]
    fn the_status_says_whether_a_command_could_change_the_level() {
        let seal = Path::new("/run/user/1000/ling-airgapped/abc");
        // Held: said at any exposure, and it names where.
        let held = guard_lines(Level::On, Some(seal), true, true);
        assert!(held[0].starts_with("Held: commands cannot change this level") && held[0].contains("/run/user/1000/ling-airgapped/abc"));
        // The hole, when nothing can close it: `on`, exposed, and no place for a seal.
        let open = guard_lines(Level::On, None, true, false);
        assert!(open[0].starts_with("NOT ENFORCED against a command rewriting the level"));
        // Nothing to say otherwise: not exposed, or not at `on`, or a seal will be written.
        assert!(guard_lines(Level::On, None, false, false).is_empty());
        assert!(guard_lines(Level::On, None, true, true).is_empty());
        assert!(guard_lines(Level::Off, None, true, false).is_empty());
    }

    #[test]
    fn seals_of_processes_that_exited_are_pruned() {
        let dir = std::env::temp_dir().join(format!("ling-airgapped-seals-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        std::fs::write(dir.join("running"), "100\n").unwrap_or_default();
        std::fs::write(dir.join("exited"), "200\n").unwrap_or_default();
        std::fs::write(dir.join("garbage"), "not a pid").unwrap_or_default();
        prune_seals(&dir, |pid| pid == 100);
        assert!(dir.join("running").exists());
        assert!(!dir.join("exited").exists() && !dir.join("garbage").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn old_session_files_are_pruned() {
        let dir = std::env::temp_dir().join(format!("ling-airgapped-prune-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        std::fs::write(dir.join("a"), "on\n").unwrap_or_default();
        prune_older_than(&dir, Duration::from_secs(3600), SystemTime::now());
        assert!(dir.join("a").exists());
        prune_older_than(&dir, Duration::ZERO, SystemTime::now() + Duration::from_secs(5));
        assert!(!dir.join("a").exists());
    }
}
