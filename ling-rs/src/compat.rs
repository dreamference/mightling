//! The compatibility mode: `ling` working the way Claude Code does, reading its files
//! (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md, §0.4 for the switch).
//!
//! The mode is a launch-time setting of the same binary, not a second program. The first of these
//! that is set wins:
//!
//! 1. `--compat claude` or `--compat off` (also `--compat=claude`) on the command line. It is
//!    turned into [`ENV`] first thing in `main()` ([`export_flag`], from `home::use_mightling_home`),
//!    so every process below this one (hooks, the subcommands the mode adds) reads one setting, and
//!    removed from the arguments before Codex parses them ([`without_flag`]).
//! 2. `DREAMFERENCE_MIGHTLING_COMPAT=claude|off`.
//! 3. `mightling_compat = "claude"|"off"` in the user's own `~/.config/dreamference/config.toml`,
//!    which `ling compat use` writes.
//! 4. Off.
//!
//! A repository can never turn the mode on. The key is read from the user's file only, never from
//! a `dreamference.toml` in the working folder or a file named by `DREAMFERENCE_CONFIG_PATH`, and
//! one found there is reported as ignored. AGENTS.md's rule: the user's choices are never taken
//! from where a clone could write them. A value the launcher does not know is refused at start
//! when it comes from the flag or the variable; from the file it is reported and the mode stays off.
//!
//! The value names the file format the mode reads, as Claude Code's own importer names the agents
//! it imports from. Everything the mode does is gated on [`Mode::Claude`], so a plain `ling` start
//! is unchanged.

use std::ffi::OsString;
use std::path::Path;
use std::path::PathBuf;

/// The variable the flag becomes, and that a script may set.
pub const ENV: &str = "DREAMFERENCE_MIGHTLING_COMPAT";

/// The key `ling compat use` writes in the user's Dreamference config file.
pub const KEY: &str = "mightling_compat";

/// The command-line flag, consumed by the launcher.
pub const FLAG: &str = "--compat";

/// The values the setting takes.
pub const VALUES: &[&str] = &["claude", "off"];

/// Which compatibility mode a session runs in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Off,
    /// Claude Code's way of working, its files read beside Mightling's own.
    Claude,
}

impl Mode {
    /// The mode a value names, or None for a value the launcher does not know. Case and spaces
    /// around it do not matter.
    pub fn parse(value: &str) -> Option<Mode> {
        match value.trim().to_ascii_lowercase().as_str() {
            "claude" => Some(Mode::Claude),
            "off" => Some(Mode::Off),
            _ => None,
        }
    }

    /// The value as the user types it.
    pub fn name(self) -> &'static str {
        match self {
            Mode::Off => "off",
            Mode::Claude => "claude",
        }
    }

    pub fn is_on(self) -> bool {
        self != Mode::Off
    }
}

/// Where the setting in force came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    /// `--compat` on this command line, or a parent's, passed down as the variable.
    Variable,
    /// `mightling_compat` in the user's config file.
    UserFile(PathBuf),
    /// Nothing set.
    Default,
}

/// The setting, where it came from, and what was found and not used.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub mode: Mode,
    pub source: Source,
    /// One line per setting found and not used: a value the launcher does not know in the user's
    /// file, or the key in a file a repository could have written.
    pub ignored: Vec<String>,
}

impl Resolved {
    /// The source as a status line names it.
    pub fn source_label(&self) -> String {
        match &self.source {
            Source::Variable => format!("{ENV} (or {FLAG})"),
            Source::UserFile(path) => format!("{KEY} in {}", path.display()),
            Source::Default => "the default".to_string(),
        }
    }
}

/// The user's own Dreamference config file, the only one `mightling_compat` is read from or
/// written to: `~/.config/dreamference/config.toml`.
pub fn user_config_file() -> Option<PathBuf> {
    Some(ling_node_locator::home_dir()?.join(".config").join("dreamference").join("config.toml"))
}

/// The value of `mightling_compat` in a config file's text, if the key is there; `Err` holds what
/// it says when that is not a string.
fn key_in(text: &str) -> Option<Result<String, String>> {
    let table: toml::Table = text.parse().ok()?;
    let value = table.get(KEY)?;
    Some(value.as_str().map(str::to_string).ok_or_else(|| value.to_string()))
}

/// Resolves the setting from its tiers, given as values so it can be tested without a process
/// environment: the variable, the user's file (path and text), and the other config files the
/// launcher reads for other settings (a repository's `dreamference.toml`, `DREAMFERENCE_CONFIG_PATH`),
/// whose `mightling_compat` is never used. An unknown value in the variable is an error.
pub fn resolve_from(
    env: Option<&str>,
    user_file: Option<(&Path, &str)>,
    other_files: &[(&Path, &str)],
) -> Result<Resolved, String> {
    let mut ignored = Vec::new();
    for (path, text) in other_files {
        if key_in(text).is_some() {
            ignored.push(format!(
                "{KEY} in {} is ignored: only {} can turn the compatibility mode on",
                path.display(),
                user_config_file().map_or_else(|| "the user's config file".to_string(), |p| p.display().to_string())
            ));
        }
    }
    if let Some(value) = env.map(str::trim).filter(|value| !value.is_empty()) {
        let mode = Mode::parse(value).ok_or_else(|| unknown_value(&format!("{ENV}={value}")))?;
        return Ok(Resolved { mode, source: Source::Variable, ignored });
    }
    if let Some((path, text)) = user_file
        && let Some(found) = key_in(text)
    {
        match found.as_deref().ok().and_then(Mode::parse) {
            Some(mode) => return Ok(Resolved { mode, source: Source::UserFile(path.to_path_buf()), ignored }),
            None => {
                let shown = found.unwrap_or_else(|raw| raw);
                ignored.push(format!(
                    "{KEY} = {shown:?} in {} is not a mode ({}); the mode is off",
                    path.display(),
                    VALUES.join(", ")
                ));
            }
        }
    }
    Ok(Resolved { mode: Mode::Off, source: Source::Default, ignored })
}

fn unknown_value(what: &str) -> String {
    format!("{what} is not a compatibility mode; use {} (or unset it).", VALUES.join(" or "))
}

/// The setting for this process.
pub fn resolve() -> Result<Resolved, String> {
    let env = std::env::var(ENV).ok();
    let user_path = user_config_file();
    let user_text = user_path.as_ref().and_then(|path| std::fs::read_to_string(path).ok());
    let user_file = user_path.as_deref().zip(user_text.as_deref());
    let mut others: Vec<(PathBuf, String)> = Vec::new();
    if let Some(path) = crate::config_file()
        && user_path.as_ref().is_none_or(|user| !same_file(user, &path))
        && let Ok(text) = std::fs::read_to_string(&path)
    {
        others.push((path, text));
    }
    let others: Vec<(&Path, &str)> = others.iter().map(|(path, text)| (path.as_path(), text.as_str())).collect();
    resolve_from(env.as_deref(), user_file, &others)
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Whether the mode is on for this process. An unknown value counts as off here; the start of a
/// session refuses it first ([`check_at_start`]).
pub fn mode_now() -> Mode {
    resolve().map(|resolved| resolved.mode).unwrap_or(Mode::Off)
}

/// The value of the last `--compat <v>` or `--compat=<v>` before a `--`, if any. A `--compat` with
/// nothing after it gives the empty string, which is refused like any other unknown value.
pub fn flag_value(args: &[OsString]) -> Option<String> {
    let mut found = None;
    let mut index = 1;
    while index < args.len() {
        match args[index].to_str() {
            Some("--") => break,
            Some(FLAG) => {
                found = Some(args.get(index + 1).map(|value| value.to_string_lossy().into_owned()).unwrap_or_default());
                index += 1;
            }
            Some(arg) => {
                if let Some(value) = arg.strip_prefix(FLAG).and_then(|rest| rest.strip_prefix('=')) {
                    found = Some(value.to_string());
                }
            }
            None => {}
        }
        index += 1;
    }
    found
}

/// The command line without `--compat` and its value, which Codex does not know.
pub fn without_flag(args: Vec<OsString>) -> Vec<OsString> {
    let mut out = Vec::with_capacity(args.len());
    let mut after_dashes = false;
    let mut skip_value = false;
    for (index, arg) in args.into_iter().enumerate() {
        if skip_value {
            skip_value = false;
            continue;
        }
        if index > 0 && !after_dashes {
            match arg.to_str() {
                Some("--") => after_dashes = true,
                Some(FLAG) => {
                    skip_value = true;
                    continue;
                }
                Some(other) if other.strip_prefix(FLAG).is_some_and(|rest| rest.starts_with('=')) => continue,
                _ => {}
            }
        }
        out.push(arg);
    }
    out
}

/// Turns the command line's `--compat` into [`ENV`] for this process and everything it starts.
///
/// Called from `home::use_mightling_home`, first thing in Codex's `main()`, before any other thread
/// exists: the only point at which setting a variable is sound. An unknown value is passed on as
/// it was given, so the start refuses it with the flag's own words ([`check_at_start`]).
pub fn export_flag() {
    let args: Vec<OsString> = std::env::args_os().collect();
    if let Some(value) = flag_value(&args) {
        // SAFETY: see above; no other thread can be reading the environment yet.
        unsafe { std::env::set_var(ENV, value) };
    }
}

/// Refuses a session whose setting names no mode, with the words the user typed. Returns the
/// setting otherwise.
pub fn check_at_start(original_args: &[OsString]) -> anyhow::Result<Resolved> {
    if let Some(value) = flag_value(original_args)
        && Mode::parse(&value).is_none()
    {
        anyhow::bail!("{}", unknown_value(&format!("`{FLAG} {value}`")));
    }
    resolve().map_err(|err| anyhow::anyhow!(err))
}

/// The lines a session's start shows about the mode: what was ignored, and, for a session someone
/// reads, that the mode is on.
pub fn startup_lines(resolved: &Resolved, interactive: bool) -> Vec<String> {
    let mut lines: Vec<String> = resolved.ignored.iter().map(|note| format!("⚠️  {note}.")).collect();
    if interactive && resolved.mode == Mode::Claude {
        lines.push(format!(
            "Compatibility mode: claude, reading Claude Code's project files and keys ({}). `ling compat doctor` shows what it reads.",
            resolved.source_label()
        ));
    }
    lines
}

/// The TOML text with `mightling_compat` set to `mode`, everything else kept as written.
fn with_choice(existing: &str, mode: Mode) -> Result<String, toml_edit::TomlError> {
    let mut document: toml_edit::DocumentMut = existing.parse()?;
    document[KEY] = toml_edit::value(mode.name());
    Ok(document.to_string())
}

/// `ling compat use <mode>`: writes the choice into the user's config file.
fn use_mode(value: &str) -> (Vec<String>, i32) {
    let Some(mode) = Mode::parse(value) else {
        return (vec![unknown_value(&format!("`{value}`"))], 2);
    };
    let Some(path) = user_config_file() else {
        return (vec!["Could not find the configuration file to write: HOME is not set.".to_string()], 1);
    };
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let updated = match with_choice(&existing, mode) {
        Ok(updated) => updated,
        Err(err) => return (vec![format!("Could not update {}: {err}", path.display())], 1),
    };
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .map_err(anyhow::Error::from)
        .and_then(|()| crate::write_atomically(&path, updated.as_bytes()));
    if let Err(err) = written {
        return (vec![format!("Could not write {}: {err}", path.display())], 1);
    }
    let mut lines = vec![format!(
        "Compatibility mode: {} for new sessions ({KEY} in {}). Sessions already started keep theirs.",
        mode.name(),
        path.display()
    )];
    if std::env::var(ENV).is_ok_and(|value| !value.trim().is_empty()) {
        lines.push(format!("{ENV} is set, and it still wins for new sessions."));
    }
    (lines, 0)
}

/// What `ling compat` and `ling compat status` print.
fn status_lines(resolved: &Resolved) -> Vec<String> {
    let mut lines = vec![format!("Compatibility mode: {} (from {})", resolved.mode.name(), resolved.source_label())];
    lines.extend(resolved.ignored.iter().map(|note| format!("⚠️  {note}.")));
    lines.push(format!(
        "With `claude`, ling reads Claude Code's project files and works with its keys and command names, \
         on Mightling's model, sandbox and /airgapped. Set it with {FLAG} claude|off, {ENV}=claude|off, \
         or `ling compat use claude|off` ({KEY} in your own ~/.config/dreamference/config.toml; a repository's \
         dreamference.toml cannot set it). It takes effect when a session starts."
    ));
    lines
}

/// What `ling compat doctor` prints: the setting, then what the mode does in this build.
fn doctor_lines(resolved: &Resolved) -> Vec<String> {
    let mut lines = status_lines(resolved);
    lines.push(String::new());
    let dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let codex_home = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf());
    lines.extend(interface_lines(&dir, codex_home.as_deref()));
    lines.push(String::new());
    lines.push("Not built yet: CLAUDE.md, .claude/ settings, commands, agents and hooks follow phase by phase \
                (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §12)."
        .to_string());
    lines
}

/// The interface part of `ling compat doctor`: the renderer a TUI session here would start in, the
/// key bindings written, and what behaves differently from Claude Code.
fn interface_lines(dir: &Path, codex_home: Option<&Path>) -> Vec<String> {
    use crate::compat_tui;
    let config_text = codex_home.and_then(|home| std::fs::read_to_string(home.join("config.toml")).ok()).unwrap_or_default();
    let home = ling_node_locator::home_dir();
    let inputs = compat_tui::RendererInputs {
        no_alt_screen_flag: false,
        disable_alternate_screen: std::env::var(compat_tui::DISABLE_ALTERNATE_SCREEN_ENV).ok(),
        no_flicker: std::env::var(compat_tui::NO_FLICKER_ENV).ok(),
        codex_config_sets_screen: compat_tui::config_sets_screen(&config_text),
        claude_tui: compat_tui::claude_tui_setting(&compat_tui::project_root(dir), home.as_deref()),
    };
    let renderer = match compat_tui::choose_renderer(&inputs) {
        Some(compat_tui::Renderer::Classic) => "classic (inline, the conversation in the terminal's scrollback)".to_string(),
        Some(compat_tui::Renderer::Fullscreen) => "fullscreen (the alternate screen)".to_string(),
        None => "as tui.alternate_screen / tui.fullscreen_transcript in config.toml say (Mightling's own /tui choice)".to_string(),
    };
    let mut lines = vec![format!("Interface: {renderer}.")];
    let keys = compat_tui::keymap_settings(&compat_tui::user_keymap(&config_text));
    lines.push(format!(
        "Keys: {} binding(s) written for the session (Ctrl+O opens and closes the transcript; g, G and b in the pager); \
         your own [tui.keymap] wins over them.",
        keys.len()
    ));
    lines.push(
        "Keys and commands: Shift+Tab cycles the approval modes (Read Only and the default, never Full Access), \
         Ctrl+C or Ctrl+D twice exits, \\ then Enter adds a newline, and [ or v in the transcript writes it to the \
         scrollback or opens it in your editor. Claude Code's command names run Codex's (/branch, /tasks, /context, ...) \
         or are answered (/help, /memory, /config, /rewind)."
            .to_string(),
    );
    lines.push(
        "Different from Claude Code: Ctrl+L clears the terminal's scrollback in the classic renderer; \
         CLAUDE_CODE_DISABLE_MOUSE is not honoured (the mouse is captured only in fullscreen and the transcript); \
         the plan mode is reached with /plan, not Shift+Tab; /rewind restores the conversation, not files."
            .to_string(),
    );
    lines
}

/// The `ling compat doctor` report, for `/config` and `/doctor` inside a session.
pub fn report_lines() -> Vec<String> {
    match resolve() {
        Ok(resolved) => doctor_lines(&resolved),
        Err(err) => vec![err],
    }
}

const USAGE: &str = "usage: ling compat [status | use claude|off | doctor]";

/// `ling compat …` from a shell. Returns the exit code. It never waits for a model server.
pub fn run_cli(args: &[String]) -> i32 {
    let resolved = match resolve() {
        Ok(resolved) => resolved,
        Err(err) => {
            eprintln!("{err}");
            return 2;
        }
    };
    let (lines, code) = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        [] | ["status"] => (status_lines(&resolved), 0),
        ["doctor"] => (doctor_lines(&resolved), 0),
        ["use", value] => use_mode(value),
        ["-h"] | ["--help"] | ["help"] => (vec![USAGE.to_string()], 0),
        _ => (vec![format!("ling compat: unexpected arguments `{}`", args.join(" ")), USAGE.to_string()], 2),
    };
    for line in lines {
        if code == 0 {
            println!("{line}");
        } else {
            eprintln!("{line}");
        }
    }
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    #[test]
    fn the_flag_wins_through_the_variable_then_the_users_file_then_off() {
        let user = Path::new("/h/.config/dreamference/config.toml");
        let on = format!("{KEY} = \"claude\"\n");
        let off = format!("{KEY} = \"off\"\n");
        let r = resolve_from(Some("off"), Some((user, &on)), &[]).unwrap();
        assert_eq!((r.mode, r.source), (Mode::Off, Source::Variable));
        let r = resolve_from(Some(" Claude "), Some((user, &off)), &[]).unwrap();
        assert_eq!((r.mode, r.source), (Mode::Claude, Source::Variable));
        let r = resolve_from(Some(""), Some((user, &on)), &[]).unwrap();
        assert_eq!((r.mode, r.source), (Mode::Claude, Source::UserFile(user.to_path_buf())));
        let r = resolve_from(None, Some((user, "other = 1\n")), &[]).unwrap();
        assert_eq!((r.mode, r.source, r.ignored.len()), (Mode::Off, Source::Default, 0));
        let r = resolve_from(None, None, &[]).unwrap();
        assert_eq!(r.mode, Mode::Off);
    }

    #[test]
    fn a_repository_cannot_turn_the_mode_on() {
        let repo = Path::new("dreamference.toml");
        let text = format!("{KEY} = \"claude\"\nvllm_host = \"http://x:8000\"\n");
        let r = resolve_from(None, None, &[(repo, &text)]).unwrap();
        assert_eq!(r.mode, Mode::Off);
        assert_eq!(r.ignored.len(), 1);
        assert!(r.ignored[0].contains("dreamference.toml is ignored"), "{:?}", r.ignored);
        // The line says so at start, even when nothing turns the mode on.
        assert!(startup_lines(&r, true)[0].contains("ignored"));
        assert_eq!(startup_lines(&r, true).len(), 1);
    }

    #[test]
    fn unknown_values_are_refused_from_the_variable_and_reported_from_the_file() {
        assert!(resolve_from(Some("cursor"), None, &[]).unwrap_err().contains("claude or off"));
        let user = Path::new("/u.toml");
        let r = resolve_from(None, Some((user, &format!("{KEY} = \"claud\"\n"))), &[]).unwrap();
        assert_eq!(r.mode, Mode::Off);
        assert!(r.ignored[0].contains("\"claud\"") && r.ignored[0].contains("is not a mode"), "{:?}", r.ignored);
        let r = resolve_from(None, Some((user, &format!("{KEY} = true\n"))), &[]).unwrap();
        assert_eq!(r.mode, Mode::Off);
        assert!(r.ignored[0].contains("true"), "{:?}", r.ignored);
    }

    #[test]
    fn the_flag_and_its_value_are_read_and_removed_before_codex_parses() {
        let args = os(&["ling", "--compat", "claude", "-m", "x", "fix it"]);
        assert_eq!(flag_value(&args).as_deref(), Some("claude"));
        assert_eq!(without_flag(args), os(&["ling", "-m", "x", "fix it"]));
        let args = os(&["ling", "exec", "--compat=off", "task"]);
        assert_eq!(flag_value(&args).as_deref(), Some("off"));
        assert_eq!(without_flag(args), os(&["ling", "exec", "task"]));
        // The last one counts; after `--` it is the prompt's.
        let args = os(&["ling", "--compat", "claude", "--compat", "off", "--", "--compat", "claude"]);
        assert_eq!(flag_value(&args).as_deref(), Some("off"));
        assert_eq!(without_flag(args), os(&["ling", "--", "--compat", "claude"]));
        // A bare flag at the end is an empty value, refused at start.
        let args = os(&["ling", "--compat"]);
        assert_eq!(flag_value(&args).as_deref(), Some(""));
        assert!(check_at_start(&args).is_err());
        assert!(check_at_start(&os(&["ling", "--compat", "vim"])).unwrap_err().to_string().contains("`--compat vim`"));
        // A word that only starts like the flag is left alone.
        assert_eq!(without_flag(os(&["ling", "--compatible"])), os(&["ling", "--compatible"]));
        assert_eq!(flag_value(&os(&["ling", "--compatible"])), None);
    }

    #[test]
    fn a_plain_start_says_nothing_and_the_mode_says_one_line() {
        let off = Resolved { mode: Mode::Off, source: Source::Default, ignored: vec![] };
        assert!(startup_lines(&off, true).is_empty());
        let on = Resolved { mode: Mode::Claude, source: Source::Variable, ignored: vec![] };
        let lines = startup_lines(&on, true);
        assert_eq!(lines.len(), 1);
        assert!(lines[0].starts_with("Compatibility mode: claude") && lines[0].contains(ENV), "{lines:?}");
        assert!(startup_lines(&on, false).is_empty(), "exec and the app server print no banner");
    }

    #[test]
    fn use_keeps_the_rest_of_the_file_and_writes_the_key_once() {
        let existing = "# mine\nvllm_host = \"http://x:8000\"\n\n[night]\nwindow = \"01:00-06:00\"\n";
        let once = with_choice(existing, Mode::Claude).unwrap();
        let twice = with_choice(&once, Mode::Off).unwrap();
        assert!(twice.starts_with("# mine\n"));
        assert_eq!(twice.matches(KEY).count(), 1);
        let parsed: toml::Table = twice.parse().unwrap();
        assert_eq!(parsed[KEY].as_str(), Some("off"));
        assert_eq!(parsed["night"]["window"].as_str(), Some("01:00-06:00"));
        assert!(parsed.get("night").and_then(|night| night.get(KEY)).is_none(), "the key stays at the top level");
    }

    #[test]
    fn names_round_trip_and_nothing_else_parses() {
        for value in VALUES {
            assert_eq!(Mode::parse(value).unwrap().name(), *value);
        }
        for bad in ["", "on", "true", "claude-code", "codex"] {
            assert_eq!(Mode::parse(bad), None, "{bad}");
        }
        assert!(Mode::Claude.is_on() && !Mode::Off.is_on());
    }
}
