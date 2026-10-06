//! How much of the internet a `puffin` session may use (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md).
//!
//! Two levels: `off` (everything, the default) and `on` (no network for anything the agent runs,
//! enforced by the command sandbox). A third, `duckduckgo` (search through DuckDuckGo only), was
//! removed on 2026-10-03: DuckDuckGo answered SearXNG with a CAPTCHA, so the level searched nothing.
//! A stored `duckduckgo` is now an invalid value, named and passed over like any other. Resolved
//! before every command, first match wins:
//!
//! 1. the session's file, `$CODEX_HOME/airgapped/<id>`, written by `/airgapped <level>`;
//! 2. `DREAMFERENCE_PUFFIN_AIRGAPPED`;
//! 3. `puffin_airgapped` in the configuration files, **strictest wins**: the repository's file
//!    can be written by the agent, so it may tighten the user-level file's level, never loosen it;
//! 4. `off`.
//!
//! A session seen at `on` is also **held** there by a seal: a file under the user's runtime
//! directory (`$XDG_RUNTIME_DIR/puffin-airgapped/<id>`), which the command sandbox mounts
//! read-only. The files of tiers 1 and 3 are not always out of a command's reach: `puffin` started
//! in the home directory makes `~/.puffin` and `~/.config` writable, so a command could rewrite its
//! own level. While a seal exists the level is `on` whatever those files say; only `/airgapped`
//! typed by the user, or a restart of `puffin`, removes it.
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

/// The folder under the user's runtime directory that holds one seal per session held at `on`.
pub const SEAL_DIR: &str = "puffin-airgapped";

/// Set by Codex for a command it runs without a network.
pub const SANDBOX_NETWORK_DISABLED_ENV_VAR: &str = "CODEX_SANDBOX_NETWORK_DISABLED";

/// A level, ordered by strictness: `Off < On`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Off,
    On,
}

impl Level {
    pub const ALL: [Level; 2] = [Level::Off, Level::On];

    /// Parses a level name, ignoring case and surrounding space.
    pub fn parse(name: &str) -> Option<Level> {
        match name.trim().to_ascii_lowercase().as_str() {
            "off" => Some(Level::Off),
            "on" => Some(Level::On),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Level::Off => "off",
            Level::On => "on",
        }
    }
}

/// Where the level in force came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Session,
    /// A seal holds the session at `on`, whatever the files say now.
    Sealed,
    Environment,
    ConfigFile(PathBuf),
    Default,
}

impl Source {
    pub fn label(&self) -> String {
        match self {
            Source::Session => "this session".to_string(),
            Source::Sealed => "this session, held until /airgapped lifts it or puffin restarts".to_string(),
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

/// The folders a sandboxed command can write under the workspace-write sandbox: the working
/// directory, `/tmp` and `$TMPDIR`. (Folders the user adds as `writable_roots` are not known here.)
pub fn writable_roots(cwd: &Path, tmpdir: Option<&Path>) -> Vec<PathBuf> {
    let mut roots = vec![cwd.to_path_buf(), PathBuf::from("/tmp")];
    if let Some(tmpdir) = tmpdir
        && !tmpdir.as_os_str().is_empty()
    {
        roots.push(tmpdir.to_path_buf());
    }
    roots
}

/// Whether `path` lies in (or is) one of `roots`, comparing real paths where they exist.
pub fn within(path: &Path, roots: &[PathBuf]) -> bool {
    let real = |path: &Path| std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let path = real(path);
    roots.iter().any(|root| path.starts_with(real(root)))
}

/// [`writable_roots`] for this process: its working directory and its `$TMPDIR`.
fn writable_roots_here() -> Vec<PathBuf> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    writable_roots(&cwd, std::env::var_os("TMPDIR").map(PathBuf::from).as_deref())
}

/// Whether a sandboxed command started here could rewrite the files the level is read from:
/// `$CODEX_HOME` (the session's file) or the user-level configuration file lies in a folder
/// commands can write. True for `puffin` started in the home directory.
pub fn level_files_exposed() -> bool {
    let roots = writable_roots_here();
    codex_home().is_some_and(|home| within(&home, &roots)) || user_config_file().is_some_and(|file| within(&file, &roots))
}

/// The user's runtime directory: `$XDG_RUNTIME_DIR`, or `/run/user/<uid>` when a shell-environment
/// policy stripped the variable. systemd creates it per login, owned by the user, and the command
/// sandbox mounts it read-only (measured 2026-10-02: a sandboxed `touch` there fails with
/// "Read-only file system", from the home directory too).
fn runtime_dir() -> Option<PathBuf> {
    if let Some(dir) = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from)
        && dir.is_absolute()
        && dir.is_dir()
    {
        return Some(dir);
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::MetadataExt;
        let dir = PathBuf::from(format!("/run/user/{}", std::fs::metadata("/proc/self").ok()?.uid()));
        if dir.is_dir() {
            return Some(dir);
        }
    }
    None
}

/// Where seals are kept, if there is a place for them a sandboxed command cannot write: the
/// runtime directory, unless it lies in a writable folder itself (`puffin` started in `/`).
pub fn seal_dir() -> Option<PathBuf> {
    let dir = runtime_dir()?;
    (!within(&dir, &writable_roots_here())).then(|| dir.join(SEAL_DIR))
}

/// A session's seal. Ids are checked as for [`session_file`].
pub fn seal_file(id: &str) -> Option<PathBuf> {
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return None;
    }
    Some(seal_dir()?.join(id))
}

/// Whether any of `ids` is held at `on` by a seal.
pub fn sealed(ids: &[&str]) -> bool {
    ids.iter().filter_map(|id| seal_file(id)).any(|path| path.is_file())
}

/// What the files say, corrected for what a command could have done to them. `sealed`: a seal
/// holds the session at `on`. `exposed`: with no seal and no place to keep one, a session file a
/// command could have rewritten may tighten the configured level, never loosen it (spec §5.3).
pub fn resolve_guarded(
    session: Option<&str>,
    environment: Option<&str>,
    configs: &[(PathBuf, String)],
    sealed: bool,
    exposed: bool,
) -> Resolved {
    let mut resolved = resolve_from(session, environment, configs);
    if sealed {
        if resolved.level != Level::On {
            resolved.invalid.push(format!(
                "ignored \"{}\" from {}: the session is held at on",
                resolved.level.name(),
                resolved.source.label()
            ));
        }
        resolved.level = Level::On;
        resolved.source = Source::Sealed;
    } else if exposed && resolved.source == Source::Session {
        let configured = resolve_from(None, environment, configs);
        if resolved.level < configured.level {
            let note = format!(
                "ignored \"{}\" from this session: commands can write the level file here, so it may not loosen {}",
                resolved.level.name(),
                configured.source.label()
            );
            resolved = configured;
            resolved.invalid.push(note);
        }
    }
    resolved
}

/// Resolves the level for the first of `ids` that has a session file, reading every tier and the
/// seals.
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
    let exposed = seal_dir().is_none() && level_files_exposed();
    resolve_guarded(session.as_deref(), environment.as_deref(), &configs, sealed(ids), exposed)
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

/// What Codex is told when it may not take a permission profile: the allowed set of its error.
pub const FULL_ACCESS_ALLOWED: &str = "[read-only, workspace-write]: airgapped is on, and Full Access runs commands with no sandbox, which is what takes their network away";
pub const SEAL_ROOT_ALLOWED: &str = "[folders that do not hold the air-gap seals]: a command that could write there could lift them";

/// Why Codex may not take a permission profile, if it may not, as the `(field, candidate, allowed)`
/// of the error it reports (specs/DREAMFERENCE_PUFFIN_DESKTOP.md §8.2). Asked by Codex's config
/// every time a profile is set (patch 0023), so it binds every client of the app server, not only
/// the TUI's pickers and the launcher's argument check. The level here is the configured one: the
/// config is resolved before a thread has an id, so a thread's own `/airgapped on` is not seen.
pub fn permission_refusal(full_access: bool, writable_roots: &[PathBuf]) -> Option<(&'static str, String, String)> {
    let seals = seal_dir();
    let any_sealed = seals
        .as_deref()
        .and_then(|dir| std::fs::read_dir(dir).ok())
        .is_some_and(|mut entries| entries.next().is_some());
    refusal(full_access, writable_roots, resolve(&[]).level == Level::On, seals.as_deref(), any_sealed)
}

/// [`permission_refusal`] on given facts. Full Access is refused at a configured `on`. A writable
/// root holding the seals is refused while the level is `on` or any session is sealed; Full Access
/// is not judged by that rule, since it has no roots and another session's choice is its own.
pub fn refusal(
    full_access: bool,
    writable_roots: &[PathBuf],
    configured_on: bool,
    seals: Option<&Path>,
    any_sealed: bool,
) -> Option<(&'static str, String, String)> {
    if full_access {
        return configured_on.then(|| ("sandbox_mode", "danger-full-access".to_string(), FULL_ACCESS_ALLOWED.to_string()));
    }
    let seals = seals.filter(|_| configured_on || any_sealed)?;
    writable_roots
        .iter()
        .find(|root| within(seals, std::slice::from_ref(*root)))
        .map(|root| ("writable_roots", root.display().to_string(), SEAL_ROOT_ALLOWED.to_string()))
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
        assert_eq!(Level::parse("off"), Some(Level::Off));
        assert_eq!(Level::parse("airgapped"), None);
        // The removed level, and its alias, are unknown names now.
        assert_eq!(Level::parse("duckduckgo"), None);
        assert_eq!(Level::parse("ddg"), None);
        assert!(Level::Off < Level::On);
        assert_eq!(Level::parse(DEFAULT_PUFFIN_AIRGAPPED), Some(Level::Off));
    }

    #[test]
    fn the_key_is_read_only_at_the_top_level() {
        assert_eq!(toml_value("puffin_airgapped = \"on\"\n").as_deref(), Some("on"));
        assert_eq!(toml_value("model = \"x\"\npuffin_airgapped='on' # no network\n").as_deref(), Some("on"));
        assert_eq!(toml_value("[night]\npuffin_airgapped = \"on\"\n"), None);
        assert_eq!(toml_value("puffin_airgapped_other = \"on\"\n"), None);
        assert_eq!(toml_value(""), None);
    }

    #[test]
    fn the_first_tier_that_has_a_valid_value_wins() {
        let configs = [config("/repo/dreamference.toml", "puffin_airgapped = \"on\"\n")];
        let resolved = resolve_from(Some("off\n"), Some("on"), &configs);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Session));
        let resolved = resolve_from(None, Some("off"), &configs);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Environment));
        let resolved = resolve_from(None, None, &[]);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Default));
    }

    #[test]
    fn an_invalid_value_is_named_and_skipped() {
        let resolved = resolve_from(Some("sealed"), Some("on"), &[]);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.invalid, vec!["ignored \"sealed\" from this session".to_string()]);
        // A session or a file still set to the removed `duckduckgo` level falls through the same way.
        let user_on = config("/home/u/.config/dreamference/config.toml", "puffin_airgapped = \"on\"\n");
        let resolved = resolve_from(Some("duckduckgo"), None, &[user_on]);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.invalid, vec!["ignored \"duckduckgo\" from this session".to_string()]);
        let repo_ddg = config("/repo/dreamference.toml", "puffin_airgapped = \"duckduckgo\"\n");
        assert_eq!(resolve_from(None, None, &[repo_ddg]).level, Level::Off);
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
        let user_off = config("/home/u/.config/dreamference/config.toml", "puffin_airgapped = \"off\"\n");
        let resolved = resolve_from(None, None, &[repo_on.clone(), user_off]);
        assert_eq!(resolved.source, Source::ConfigFile(repo_on.0));
        // A file without the key does not count.
        let resolved = resolve_from(None, None, &[config("/repo/dreamference.toml", "model = \"x\"\n"), repo_off]);
        assert_eq!(resolved.level, Level::Off);
    }

    #[test]
    fn session_ids_that_are_not_ids_are_refused() {
        assert_eq!(session_file("../../etc/passwd"), None);
        assert_eq!(session_file(""), None);
        assert_eq!(seal_file("../../etc/passwd"), None);
        assert_eq!(seal_file(""), None);
        assert!(!sealed(&["", "../x"]));
    }

    #[test]
    fn a_seal_holds_the_session_at_on_whatever_the_files_say() {
        // A command rewrote the session file to `off`, or deleted the key from a config file.
        let configs = [config("/repo/dreamference.toml", "puffin_airgapped = \"off\"\n")];
        let resolved = resolve_guarded(Some("off\n"), Some("off"), &configs, true, true);
        assert_eq!((resolved.level, resolved.source.clone()), (Level::On, Source::Sealed));
        assert_eq!(resolved.invalid, vec!["ignored \"off\" from this session: the session is held at on".to_string()]);
        // A seal on a session the files still put at `on` says nothing extra.
        let resolved = resolve_guarded(Some("on"), None, &[], true, false);
        assert_eq!((resolved.level, resolved.source, resolved.invalid.len()), (Level::On, Source::Sealed, 0));
        // Without a seal the files decide, as before.
        let resolved = resolve_guarded(Some("off"), Some("on"), &[], false, false);
        assert_eq!((resolved.level, resolved.source), (Level::Off, Source::Session));
    }

    #[test]
    fn with_no_place_for_a_seal_an_exposed_session_file_may_only_tighten() {
        let user_on = [config("/home/u/.config/dreamference/config.toml", "puffin_airgapped = \"on\"\n")];
        let resolved = resolve_guarded(Some("off"), None, &user_on, false, true);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.source, Source::ConfigFile(user_on[0].0.clone()));
        assert!(resolved.invalid[0].starts_with("ignored \"off\" from this session: commands can write the level file here"));
        let resolved = resolve_guarded(Some("off"), Some("on"), &[], false, true);
        assert_eq!((resolved.level, resolved.source), (Level::On, Source::Environment));
        // Tightening is still the session's to do, and nothing configured means nothing to loosen.
        let resolved = resolve_guarded(Some("on"), Some("off"), &[], false, true);
        assert_eq!((resolved.level, resolved.source), (Level::On, Source::Session));
        let resolved = resolve_guarded(Some("off"), None, &[], false, true);
        assert_eq!((resolved.level, resolved.source, resolved.invalid.len()), (Level::Off, Source::Session, 0));
    }

    #[test]
    fn full_access_is_refused_only_at_a_configured_on() {
        let refused = refusal(true, &[], true, None, false).unwrap_or_default();
        assert_eq!((refused.0, refused.1.as_str()), ("sandbox_mode", "danger-full-access"));
        assert_eq!(refusal(true, &[], false, None, true), None);
        // Full Access is not judged by the seal rule: it has no roots to judge.
        assert_eq!(refusal(true, &[PathBuf::from("/")], false, Some(Path::new("/run/user/1/puffin-airgapped")), true), None);
    }

    #[test]
    fn a_writable_root_holding_the_seals_is_refused_while_anything_is_on() {
        let seals = Path::new("/run/user/1/puffin-airgapped");
        let run = [PathBuf::from("/run")];
        let project = [PathBuf::from("/home/u/project"), PathBuf::from("/tmp")];
        assert_eq!(refusal(false, &run, true, Some(seals), false).map(|r| r.1), Some("/run".to_string()));
        assert_eq!(refusal(false, &run, false, Some(seals), true).map(|r| r.0), Some("writable_roots"));
        assert_eq!(refusal(false, &[seals.to_path_buf()], true, Some(seals), false).map(|r| r.0), Some("writable_roots"));
        // Nothing on, nothing sealed, or ordinary roots: allowed.
        assert_eq!(refusal(false, &run, false, Some(seals), false), None);
        assert_eq!(refusal(false, &project, true, Some(seals), true), None);
        assert_eq!(refusal(false, &run, true, None, true), None);
    }

    #[test]
    fn a_path_is_within_a_writable_root_by_its_real_location() {
        let base = std::env::temp_dir().join(format!("puffin-airgapped-roots-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let home = base.join("home");
        std::fs::create_dir_all(home.join(".puffin")).unwrap_or_default();
        std::fs::create_dir_all(base.join("elsewhere")).unwrap_or_default();
        // `puffin` started in the home directory: `~/.puffin` is inside the working directory.
        let roots = writable_roots(&home, None);
        assert_eq!(roots, vec![home.clone(), PathBuf::from("/tmp")]);
        assert!(within(&home.join(".puffin"), &[home.clone()]));
        assert!(within(&home.join(".config/dreamference/config.toml"), &[home.clone()]));
        // Started in a project folder: it is not.
        assert!(!within(&home.join(".puffin"), &[home.join("project")]));
        assert!(!within(&base.join("elsewhere"), &[home.clone()]));
        // A name that only shares a prefix is not inside, and a link is followed to where it leads.
        assert!(!within(&base.join("home2"), &[home.clone()]));
        #[cfg(unix)]
        {
            let _ = std::os::unix::fs::symlink(&home, base.join("link"));
            assert!(within(&base.join("link").join(".puffin"), &[home.clone()]));
        }
        assert_eq!(writable_roots(&home, Some(Path::new("/var/tmp/x"))).len(), 3);
        assert_eq!(writable_roots(&home, Some(Path::new(""))).len(), 2);
        let _ = std::fs::remove_dir_all(&base);
    }
}
