//! How much of the internet a `ling` session may use (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md).
//!
//! Two levels: `off` (everything, the default) and `on` (no network for anything the agent runs,
//! enforced by the command sandbox). A third, `duckduckgo` (search through DuckDuckGo only), was
//! removed on 2026-10-03: DuckDuckGo answered SearXNG with a CAPTCHA, so the level searched nothing.
//! A stored `duckduckgo` is now an invalid value, named and passed over like any other. Resolved
//! before every command, first match wins:
//!
//! 1. the session's file, `$CODEX_HOME/airgapped/<id>`, written by `/airgapped <level>`;
//! 2. `DREAMFERENCE_MIGHTLING_AIRGAPPED`;
//! 3. `mightling_airgapped` in the configuration files, **strictest wins**: the repository's file
//!    can be written by the agent, so it may tighten the user-level file's level, never loosen it;
//! 4. `off`.
//!
//! A session seen at `on` is also **held** there by a seal: a file under the user's runtime
//! directory (`$XDG_RUNTIME_DIR/ling-airgapped/<id>`), which the command sandbox mounts
//! read-only. The files of tiers 1 and 3 are not always out of a command's reach: `ling` started
//! in the home directory makes `~/.mightling` and `~/.config` writable, so a command could rewrite its
//! own level. While a seal exists the level is `on` whatever those files say; only `/airgapped`
//! typed by the user, or a restart of `ling`, removes it.
//!
//! Standard library only: this file is compiled into Codex's sandbox helper, the launcher and the
//! web commands.

use std::path::Path;
use std::path::PathBuf;

/// The level when nothing is configured. Mirrored by `DEFAULT_MIGHTLING_AIRGAPPED` in Dreamference's
/// Python config, which a test compares with this line.
pub const DEFAULT_MIGHTLING_AIRGAPPED: &str = "off";

/// The environment variable that overrides the configuration files.
pub const ENV_VAR: &str = "DREAMFERENCE_MIGHTLING_AIRGAPPED";

/// The TOML key `/airgapped default` writes.
pub const TOML_KEY: &str = "mightling_airgapped";

/// The folder under `$CODEX_HOME` that holds one file per session.
pub const SESSION_DIR: &str = "airgapped";

/// The folder under the user's runtime directory that holds one seal per session held at `on`.
pub const SEAL_DIR: &str = "ling-airgapped";

/// Set by Codex for a command it runs without a network.
pub const SANDBOX_NETWORK_DISABLED_ENV_VAR: &str = "CODEX_SANDBOX_NETWORK_DISABLED";

/// `off` keeps the read-only sandbox's commands off the network at `off` too ([`read_only_network`]).
/// The launcher sets it in `ling`'s own environment when the user chose
/// `sandbox_workspace_write.network_access = false`, in `config.toml` or with `-c`.
pub const READ_ONLY_NETWORK_ENV_VAR: &str = "DREAMFERENCE_MIGHTLING_READ_ONLY_NETWORK";

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
            Source::Sealed => {
                "this session, held until /airgapped lifts it or ling restarts".to_string()
            }
            Source::Environment => ENV_VAR.to_string(),
            Source::ConfigFile(path) => format!("{TOML_KEY} in {}", path.display()),
            Source::Default => "default".to_string(),
        }
    }

    /// The tier's name in `ling airgapped --json`: `session`, `sealed`, `environment`, `config`
    /// or `default`.
    pub fn tier(&self) -> &'static str {
        match self {
            Source::Session => "session",
            Source::Sealed => "sealed",
            Source::Environment => "environment",
            Source::ConfigFile(_) => "config",
            Source::Default => "default",
        }
    }
}

/// A file as read: its text, or why it exists and could not be read.
pub type FileRead = Result<String, String>;

/// The level in force, where it came from, and any invalid values passed over on the way.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub level: Level,
    pub source: Source,
    pub invalid: Vec<String>,
}

/// The configuration file's value of the key, as [`resolve_read`] takes it: `Some(None)` when the top
/// level does not set it, `None` when the text is not TOML the resolver can follow up to the key or
/// the first table header (AIRGAPPED §6.5: it then counts as `on`). [`toml_value`] reads such text
/// line by line instead.
pub fn config_value(text: &str) -> Option<Option<String>> {
    top_level_value(text.trim_start_matches('\u{feff}'))
}

/// The value of the top-level `mightling_airgapped` key in TOML text, before the first table
/// header, as a TOML parser reads it: the key bare or quoted, the value a string of any of the
/// four kinds with its escapes read, or a boolean, read as Python's configuration reads it (`true`
/// is `on`). What comes before the key is followed as TOML, so a string or an array spanning
/// lines neither hides the key nor lends it a line of its own. Text this cannot follow is not
/// TOML, and a plain `mightling_airgapped = <value>` line is then still read. This keeps a TOML
/// parser out of the sandbox helper. A leading byte order mark is skipped: Windows PowerShell's
/// `-Encoding UTF8` writes one, and the key must not be missed behind it.
pub fn toml_value(text: &str) -> Option<String> {
    let text = text.trim_start_matches('\u{feff}');
    top_level_value(text).unwrap_or_else(|| line_value(text))
}

/// The key's value, `Some(None)` when the top level does not set it, `None` when the text is not
/// TOML this follows.
fn top_level_value(text: &str) -> Option<Option<String>> {
    let mut rest = text;
    loop {
        rest = skip_blank(rest);
        if rest.is_empty() || rest.starts_with('[') {
            return Some(None);
        }
        let (key, after) = toml_key(rest)?;
        let after = skip_space(skip_space(after).strip_prefix('=')?);
        if key == [TOML_KEY] {
            return Some(Some(match toml_string(after) {
                Some((value, _)) => value,
                None => bare_value(after),
            }));
        }
        rest = end_of_statement(skip_value(after)?)?;
    }
}

/// The first line `mightling_airgapped = <value>` before a line starting with `[`, for text that
/// is not TOML.
fn line_value(text: &str) -> Option<String> {
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            return None;
        }
        let Some(rest) = line.strip_prefix(TOML_KEY) else {
            continue;
        };
        let Some(value) = rest.trim_start().strip_prefix('=') else {
            continue;
        };
        return Some(bare_value(value));
    }
    None
}

/// A value that is not a string, read up to its comment, quotes trimmed; a boolean as a level.
fn bare_value(value: &str) -> String {
    let value = value
        .lines()
        .next()
        .unwrap_or("")
        .split('#')
        .next()
        .unwrap_or("")
        .trim();
    let value = value.trim_matches(|quote| quote == '"' || quote == '\'');
    match value {
        "true" => Level::On.name().to_string(),
        "false" => Level::Off.name().to_string(),
        _ => value.to_string(),
    }
}

fn skip_space(text: &str) -> &str {
    text.trim_start_matches([' ', '\t'])
}

/// Past whitespace, line ends and comments.
fn skip_blank(mut text: &str) -> &str {
    loop {
        text = text.trim_start_matches([' ', '\t', '\r', '\n']);
        match text.strip_prefix('#') {
            Some(comment) => text = comment.find('\n').map_or("", |end| &comment[end..]),
            None => return text,
        }
    }
}

/// Past the end of a key-value line: spaces, a comment, the line end.
fn end_of_statement(text: &str) -> Option<&str> {
    let text = skip_space(text);
    let text = match text.strip_prefix('#') {
        Some(comment) => comment.find('\n').map_or("", |end| &comment[end..]),
        None => text,
    };
    if text.is_empty() {
        return Some(text);
    }
    text.strip_prefix('\n')
        .or_else(|| text.strip_prefix("\r\n"))
}

/// A key, dotted or not, its parts bare or quoted, and the text after it.
fn toml_key(mut text: &str) -> Option<(Vec<String>, &str)> {
    let mut parts = Vec::new();
    loop {
        let (part, after) = if text.starts_with("\"\"\"") || text.starts_with("'''") {
            return None;
        } else if text.starts_with(['"', '\'']) {
            toml_string(text)?
        } else {
            let end = text
                .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '-'))
                .unwrap_or(text.len());
            if end == 0 {
                return None;
            }
            (text[..end].to_string(), &text[end..])
        };
        parts.push(part);
        match skip_space(after).strip_prefix('.') {
            Some(next) => text = skip_space(next),
            None => return Some((parts, after)),
        }
    }
}

/// A string of any of the four kinds, read, and the text after it.
fn toml_string(text: &str) -> Option<(String, &str)> {
    if let Some(body) = text.strip_prefix("\"\"\"") {
        basic_string(first_line_end(body), true)
    } else if let Some(body) = text.strip_prefix("'''") {
        let body = first_line_end(body);
        let end = body.find("'''")?;
        // Up to two more quotes end the string's content, not the string.
        let extra = body[end + 3..]
            .chars()
            .take_while(|&c| c == '\'')
            .count()
            .min(2);
        Some((body[..end + extra].to_string(), &body[end + 3 + extra..]))
    } else if let Some(body) = text.strip_prefix('"') {
        basic_string(body, false)
    } else {
        let body = text.strip_prefix('\'')?;
        let end = body.find(['\'', '\n'])?;
        body[end..]
            .starts_with('\'')
            .then(|| (body[..end].to_string(), &body[end + 1..]))
    }
}

/// A multi-line string's content: a line end right after the opening quotes is not part of it.
fn first_line_end(body: &str) -> &str {
    body.strip_prefix('\n')
        .or_else(|| body.strip_prefix("\r\n"))
        .unwrap_or(body)
}

/// A basic string's content after its opening quotes, read up to its closing ones.
fn basic_string(body: &str, multi_line: bool) -> Option<(String, &str)> {
    let mut out = String::new();
    let mut at = 0;
    while let Some(c) = body[at..].chars().next() {
        let next = at + c.len_utf8();
        match c {
            '"' if !multi_line => return Some((out, &body[next..])),
            '"' if body[at..].starts_with("\"\"\"") => {
                let extra = body[at + 3..]
                    .chars()
                    .take_while(|&c| c == '"')
                    .count()
                    .min(2);
                out.extend(std::iter::repeat_n('"', extra));
                return Some((out, &body[at + 3 + extra..]));
            }
            '\n' if !multi_line => return None,
            '\\' => {
                let escape = body[next..].chars().next()?;
                at = next + escape.len_utf8();
                match escape {
                    'b' => out.push('\u{8}'),
                    't' => out.push('\t'),
                    'n' => out.push('\n'),
                    'f' => out.push('\u{c}'),
                    'r' => out.push('\r'),
                    '"' => out.push('"'),
                    '\\' => out.push('\\'),
                    'u' | 'U' => {
                        let digits = if escape == 'u' { 4 } else { 8 };
                        let hex = body.get(at..at + digits)?;
                        if !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
                            return None;
                        }
                        out.push(char::from_u32(u32::from_str_radix(hex, 16).ok()?)?);
                        at += digits;
                    }
                    // A backslash ending a line drops the line end and the whitespace after it.
                    ' ' | '\t' | '\r' | '\n' if multi_line => {
                        let tail = &body[next..];
                        let kept = tail.trim_start_matches([' ', '\t', '\r', '\n']);
                        if !tail[..tail.len() - kept.len()].contains('\n') {
                            return None;
                        }
                        at = body.len() - kept.len();
                    }
                    _ => return None,
                }
                continue;
            }
            c => out.push(c),
        }
        at = next;
    }
    None
}

/// Past a value: a string, an array or an inline table, nested, or a number, boolean or date.
fn skip_value(text: &str) -> Option<&str> {
    if text.starts_with(['"', '\'']) {
        return toml_string(text).map(|(_, rest)| rest);
    }
    if let Some(mut rest) = text.strip_prefix('[') {
        loop {
            rest = skip_blank(rest);
            if let Some(after) = rest.strip_prefix(']') {
                return Some(after);
            }
            rest = skip_blank(skip_value(rest)?);
            match rest.strip_prefix(',') {
                Some(after) => rest = after,
                None => return rest.strip_prefix(']'),
            }
        }
    }
    if let Some(mut rest) = text.strip_prefix('{') {
        loop {
            rest = skip_blank(rest);
            if let Some(after) = rest.strip_prefix('}') {
                return Some(after);
            }
            let (_, after) = toml_key(rest)?;
            rest = skip_blank(skip_value(skip_space(
                skip_space(after).strip_prefix('=')?,
            ))?);
            match rest.strip_prefix(',') {
                Some(after) => rest = after,
                None => return rest.strip_prefix('}'),
            }
        }
    }
    let scalar = |text: &str| {
        text.find(|c: char| !(c.is_ascii_alphanumeric() || "_+-.:".contains(c)))
            .unwrap_or(text.len())
    };
    let end = scalar(text);
    if end == 0 {
        return None;
    }
    let (token, rest) = text.split_at(end);
    // A date and a time may be parted by a space: `1979-05-27 07:32:00`.
    let bytes = token.as_bytes();
    if bytes.len() == 10
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && let Some(time) = rest.strip_prefix(' ')
        && time.starts_with(|c: char| c.is_ascii_digit())
    {
        return Some(&time[scalar(time)..]);
    }
    Some(rest)
}

/// Resolves the level from already-read tiers; [`resolve`] supplies the real ones. `configs` are
/// the configuration files that exist, as (path, text); the strictest level among them wins.
pub fn resolve_from(
    session: Option<&str>,
    environment: Option<&str>,
    configs: &[(PathBuf, String)],
) -> Resolved {
    let session = session.map(|text| Ok(text.to_string()));
    let configs: Vec<(PathBuf, FileRead)> = configs
        .iter()
        .map(|(path, text)| (path.clone(), Ok(text.clone())))
        .collect();
    resolve_read(session.as_ref(), environment, &configs)
}

/// [`resolve_from`] with files that may have failed to read. First match wins, and what cannot be
/// read counts as `on`, named in `invalid` (AIRGAPPED §6.5): a value that is not a level, an empty
/// session file or value, a file that exists and could not be read, and a configuration file that
/// is not TOML the resolver can follow. An empty environment variable is not a tier, and the files
/// of a tier that is not reached are not judged.
pub fn resolve_read(
    session: Option<&FileRead>,
    environment: Option<&str>,
    configs: &[(PathBuf, FileRead)],
) -> Resolved {
    fn level_of(value: &str, source: Source, invalid: &mut Vec<String>) -> (Level, Source) {
        let value = value.trim();
        match Level::parse(value) {
            Some(level) => (level, source),
            None => {
                invalid.push(format!(
                    "\"{value}\" from {} is not a level: taken as on",
                    source.label()
                ));
                (Level::On, source)
            }
        }
    }
    let mut invalid = Vec::new();
    let mut found = match session {
        Some(Ok(text)) => Some(level_of(text, Source::Session, &mut invalid)),
        Some(Err(why)) => {
            invalid.push(format!(
                "could not read this session's level file ({why}): taken as on"
            ));
            Some((Level::On, Source::Session))
        }
        None => None,
    };
    if found.is_none()
        && let Some(value) = environment.filter(|value| !value.trim().is_empty())
    {
        found = Some(level_of(value, Source::Environment, &mut invalid));
    }
    if found.is_none() {
        for (path, text) in configs {
            let source = Source::ConfigFile(path.clone());
            let level = match text.as_deref().map(config_value) {
                Ok(Some(None)) => continue,
                Ok(Some(Some(value))) => level_of(&value, source, &mut invalid),
                Ok(None) => {
                    invalid.push(format!(
                        "could not read {} as TOML up to {TOML_KEY}: taken as on",
                        path.display()
                    ));
                    (Level::On, source)
                }
                Err(why) => {
                    invalid.push(format!(
                        "could not read {} ({why}): taken as on",
                        path.display()
                    ));
                    (Level::On, source)
                }
            };
            if found
                .as_ref()
                .is_none_or(|(strictest, _)| level.0 > *strictest)
            {
                found = Some(level);
            }
        }
    }
    let (level, source) = found.unwrap_or((Level::Off, Source::Default));
    Resolved {
        level,
        source,
        invalid,
    }
}

/// The user's home folder: `HOME`, or `USERPROFILE` (Windows, where `HOME` is usually unset).
/// The same rule as the node locator's, so the copies cannot disagree.
pub fn home_dir() -> Option<PathBuf> {
    home_from(std::env::var_os("HOME"), std::env::var_os("USERPROFILE"))
}

/// [`home_dir`] on given values: the first that is set and not empty.
pub fn home_from(
    home: Option<std::ffi::OsString>,
    userprofile: Option<std::ffi::OsString>,
) -> Option<PathBuf> {
    home.filter(|home| !home.is_empty())
        .or_else(|| userprofile.filter(|home| !home.is_empty()))
        .map(PathBuf::from)
}

/// `$CODEX_HOME`, or `~/.mightling` when a shell-environment policy stripped the variable.
pub fn codex_home() -> Option<PathBuf> {
    match std::env::var_os("CODEX_HOME") {
        Some(home) if !home.is_empty() => Some(PathBuf::from(home)),
        _ => Some(home_dir()?.join(".mightling")),
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
    Some(
        home_dir()?
            .join(".config")
            .join("dreamference")
            .join("config.toml"),
    )
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
/// directory, `/tmp` (not on Windows) and the temporary folder `tmpdir`. (Folders the user adds as
/// `writable_roots` are not known here.)
pub fn writable_roots(cwd: &Path, tmpdir: Option<&Path>) -> Vec<PathBuf> {
    let mut roots = vec![cwd.to_path_buf()];
    if !cfg!(windows) {
        roots.push(PathBuf::from("/tmp"));
    }
    if let Some(tmpdir) = tmpdir
        && !tmpdir.as_os_str().is_empty()
    {
        roots.push(tmpdir.to_path_buf());
    }
    roots
}

/// The variables naming the temporary folders a command can write: `TMPDIR`, or on Windows `TEMP`
/// and `TMP`.
pub const TEMP_VARS: &[&str] = if cfg!(windows) {
    &["TEMP", "TMP"]
} else {
    &["TMPDIR"]
};

/// Whether `path` lies in (or is) one of `roots`, comparing real paths where they exist.
pub fn within(path: &Path, roots: &[PathBuf]) -> bool {
    let path = real_path(path);
    roots.iter().any(|root| path.starts_with(real_path(root)))
}

/// `path` with its deepest existing ancestor resolved, links followed, and the rest appended as
/// written: a file not created yet still compares with the folder it would be created in. On
/// Windows, `canonicalize` gives the resolved part the verbatim prefix (`\\?\C:\…`) and nothing else
/// has it, so it is removed.
pub fn real_path(path: &Path) -> PathBuf {
    let mut rest = Vec::new();
    let mut existing = path;
    loop {
        if let Ok(resolved) = std::fs::canonicalize(existing) {
            let mut real = without_verbatim_prefix(resolved);
            for part in rest.iter().rev() {
                real.push(part);
            }
            return real;
        }
        match (existing.parent(), existing.file_name()) {
            (Some(parent), Some(name)) => {
                rest.push(name.to_os_string());
                existing = parent;
            }
            _ => return path.to_path_buf(),
        }
    }
}

/// `C:\…` for `\\?\C:\…`; any other path, a verbatim UNC one included, as it is.
fn without_verbatim_prefix(path: PathBuf) -> PathBuf {
    match path.to_str().and_then(|text| text.strip_prefix(r"\\?\")) {
        Some(rest) if !rest.starts_with(r"UNC\") => PathBuf::from(rest),
        _ => path,
    }
}

/// [`writable_roots`] for this process: its working directory and its temporary folders.
fn writable_roots_here() -> Vec<PathBuf> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut roots = writable_roots(&cwd, None);
    for var in TEMP_VARS {
        if let Some(dir) = std::env::var_os(var)
            .filter(|dir| !dir.is_empty())
            .map(PathBuf::from)
            && !roots.contains(&dir)
        {
            roots.push(dir);
        }
    }
    roots
}

/// Whether a sandboxed command started here could rewrite the files the level is read from:
/// `$CODEX_HOME` (the session's file) or the user-level configuration file lies in a folder
/// commands can write. True for `ling` started in the home directory.
pub fn level_files_exposed() -> bool {
    let roots = writable_roots_here();
    codex_home().is_some_and(|home| within(&home, &roots))
        || user_config_file().is_some_and(|file| within(&file, &roots))
}

/// The user's runtime directory: `$XDG_RUNTIME_DIR`, or `/run/user/<uid>` when a shell-environment
/// policy stripped the variable. systemd creates it per login, owned by the user, and the command
/// sandbox mounts it read-only (measured 2026-10-02: a sandboxed `touch` there fails with
/// "Read-only file system", from the home directory too). On Windows, `%LOCALAPPDATA%\Mightling`:
/// the sandbox runs commands as its own local accounts, which cannot write the user's profile
/// (specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §7.3).
#[cfg(windows)]
fn runtime_dir() -> Option<PathBuf> {
    let dir = windows_runtime_dir_from(std::env::var_os("LOCALAPPDATA"))?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

/// The Windows runtime directory for a given `LOCALAPPDATA`: `<it>\Mightling`, none when it is unset
/// or empty. Apart from [`runtime_dir`] so that any machine's tests check it.
#[cfg_attr(not(windows), allow(dead_code))]
fn windows_runtime_dir_from(local_app_data: Option<std::ffi::OsString>) -> Option<PathBuf> {
    let base = PathBuf::from(local_app_data.filter(|dir| !dir.is_empty())?);
    Some(base.join("Mightling"))
}

#[cfg(not(windows))]
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
        let dir = PathBuf::from(format!(
            "/run/user/{}",
            std::fs::metadata("/proc/self").ok()?.uid()
        ));
        if dir.is_dir() {
            return Some(dir);
        }
    }
    None
}

/// Where seals are kept, if there is a place for them a sandboxed command cannot write: the
/// runtime directory, unless it lies in a writable folder itself (`ling` started in `/`).
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

/// Whether any of `ids` is held at `on` by a seal. Seals are read in the runtime directory
/// wherever this process works: [`seal_dir`] judges by the working directory where seals may be
/// written, and Codex's sandbox helper runs in each command's own folder, which the model
/// chooses, so a command started in `/` would not have seen the seal `ling` wrote. A seal only
/// ever tightens the level, so one is honoured wherever it could have come from.
pub fn sealed(ids: &[&str]) -> bool {
    let Some(seals) = runtime_dir().map(|dir| dir.join(SEAL_DIR)) else {
        return false;
    };
    ids.iter()
        .filter(|id| seal_file_name(id))
        .any(|id| seals.join(id).is_file())
}

/// Whether a seal written by process `pid` holds a session at `on`. A seal's content is its
/// writer's pid (the launcher's airgapped module), so the process that runs a session, and an MCP
/// server whose parent is that process, can find it without knowing the thread id. Read wherever
/// seals may be, as [`sealed`] reads them.
pub fn sealed_by(pid: u32) -> bool {
    runtime_dir().is_some_and(|dir| sealed_in(&dir.join(SEAL_DIR), pid))
}

/// [`sealed_by`] for the seals in `dir`.
pub fn sealed_in(dir: &Path, pid: u32) -> bool {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return false;
    };
    let own = pid.to_string();
    entries
        .flatten()
        .any(|entry| std::fs::read_to_string(entry.path()).is_ok_and(|text| text.trim() == own))
}

/// Whether `id` may name a seal: ids are UUIDs, as for [`session_file`].
fn seal_file_name(id: &str) -> bool {
    !id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
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
    let session = session.map(|text| Ok(text.to_string()));
    let configs: Vec<(PathBuf, FileRead)> = configs
        .iter()
        .map(|(path, text)| (path.clone(), Ok(text.clone())))
        .collect();
    resolve_guarded_read(session.as_ref(), environment, &configs, sealed, exposed)
}

/// [`resolve_guarded`] with files that may have failed to read, as [`resolve_read`] takes them.
pub fn resolve_guarded_read(
    session: Option<&FileRead>,
    environment: Option<&str>,
    configs: &[(PathBuf, FileRead)],
    sealed: bool,
    exposed: bool,
) -> Resolved {
    let mut resolved = resolve_read(session, environment, configs);
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
        let configured = resolve_read(None, environment, configs);
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

/// Reads a tier's file: `None` when it does not exist, its text, or why it could not be read.
pub fn read_file(path: &Path) -> Option<FileRead> {
    match std::fs::read_to_string(path) {
        Ok(text) => Some(Ok(text)),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
        Err(err) => Some(Err(err.to_string())),
    }
}

/// Resolves the level for the first of `ids` that has a session file, reading every tier and the
/// seals.
pub fn resolve(ids: &[&str]) -> Resolved {
    resolve_with(ids, std::env::var(ENV_VAR).ok())
}

/// [`resolve`] with the environment tier's value given, for a command whose environment is not
/// this process's.
pub fn resolve_with(ids: &[&str], environment: Option<String>) -> Resolved {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    resolve_in(ids, environment, &cwd)
}

/// [`resolve_with`] for a session that works in `cwd`, not in this process's folder: the
/// repository's configuration file is `cwd`'s. The app's policy layer judges a thread this way,
/// from the folder the thread works in, as the sandbox helper judges its commands.
pub fn resolve_in(ids: &[&str], environment: Option<String>, cwd: &Path) -> Resolved {
    let session = ids
        .iter()
        .filter_map(|id| session_file(id))
        .find_map(|path| read_file(&path));
    let configs: Vec<(PathBuf, FileRead)> = config_files(cwd)
        .into_iter()
        .filter_map(|path| read_file(&path).map(|text| (path, text)))
        .collect();
    let exposed = seal_dir().is_none() && level_files_exposed();
    resolve_guarded_read(
        session.as_ref(),
        environment.as_deref(),
        &configs,
        sealed(ids),
        exposed,
    )
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

/// [`sealed_for_command`] for a command whose environment is in hand rather than inherited. Codex's
/// Windows sandbox resolves a command's permissions in its own process before the command starts,
/// with the command's environment as a map (patch 0024; specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md
/// §7.3). `get` looks a variable up in that map; the level variable falls back to this process's,
/// which the command inherits.
pub fn sealed_for_env(get: impl Fn(&str) -> Option<String>) -> bool {
    let thread = get("CODEX_THREAD_ID").unwrap_or_default();
    let session = get("CODEX_SESSION_ID").unwrap_or_default();
    let environment = get(ENV_VAR).or_else(|| std::env::var(ENV_VAR).ok());
    resolve_with(&[thread.as_str(), session.as_str()], environment).level == Level::On
}

/// Whether a command Codex would run in its read-only sandbox gets the network. Sandboxed commands
/// have the network whenever `/airgapped` is off (owner decision, 2026-10-22), and Codex's read-only
/// sandbox has no network setting of its own, so it is widened here: asked by Codex's sandbox
/// manager for every command whose permissions are exactly the read-only ones (patch 0031), with
/// the command's environment as `get`, as [`sealed_for_env`] takes it. Not at `on`, and not when the
/// user said no network ([`READ_ONLY_NETWORK_ENV_VAR`] in this process's environment).
pub fn read_only_network(get: impl Fn(&str) -> Option<String>) -> bool {
    read_only_network_allowed(std::env::var(READ_ONLY_NETWORK_ENV_VAR).ok().as_deref())
        && !sealed_for_env(get)
}

/// Whether [`READ_ONLY_NETWORK_ENV_VAR`]'s value leaves the read-only sandbox its network: anything
/// but `off`, `false`, `0` or `no`.
pub fn read_only_network_allowed(setting: Option<&str>) -> bool {
    !setting.is_some_and(|value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "off" | "false" | "0" | "no"
        )
    })
}

/// What Codex is told when it may not take a permission profile: the allowed set of its error.
pub const FULL_ACCESS_ALLOWED: &str = "[read-only, workspace-write]: airgapped is on, and Full Access runs commands with no sandbox, which is what takes their network away";
pub const SEAL_ROOT_ALLOWED: &str = "[folders that do not hold the air-gap seals]: a command that could write there could lift them";

/// Why Codex may not take a permission profile, if it may not, as the `(field, candidate, allowed)`
/// of the error it reports (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.2). Asked by Codex's config
/// every time a profile is set (patch 0023), so it binds every client of the app server, not only
/// the TUI's pickers and the launcher's argument check. The level here is the configured one: the
/// config is resolved before a thread has an id, so a thread's own `/airgapped on` is not seen.
pub fn permission_refusal(
    full_access: bool,
    writable_roots: &[PathBuf],
) -> Option<(&'static str, String, String)> {
    let seals = seal_dir();
    let any_sealed = seals
        .as_deref()
        .and_then(|dir| std::fs::read_dir(dir).ok())
        .is_some_and(|mut entries| entries.next().is_some());
    refusal(
        full_access,
        writable_roots,
        resolve(&[]).level == Level::On,
        seals.as_deref(),
        any_sealed,
    )
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
        return configured_on.then(|| {
            (
                "sandbox_mode",
                "danger-full-access".to_string(),
                FULL_ACCESS_ALLOWED.to_string(),
            )
        });
    }
    let seals = seals.filter(|_| configured_on || any_sealed)?;
    writable_roots
        .iter()
        .find(|root| within(seals, std::slice::from_ref(*root)))
        .map(|root| {
            (
                "writable_roots",
                root.display().to_string(),
                SEAL_ROOT_ALLOWED.to_string(),
            )
        })
}

/// The host's control sockets that a sandboxed command at `on` must not reach, under `/`: talking
/// to one is acting on the host outside the sandbox. A command ran `docker restart` on the host
/// from `ling exec`'s workspace-write sandbox (2026-10-20): the user is in the `docker` group,
/// and a read-only bind mount does not stop a `connect()`. At `off` they stay reachable, as in
/// Codex (owner's decision, 2026-10-22; [`host_sockets_at`]). Docker and containerd (root and
/// rootless), Podman, the D-Bus system and session buses (systemd's API, which starts units),
/// the systemd user manager's own sockets, snapd, and Mightling's runtime folder (`ling web`'s
/// app-server socket, which starts threads with any permissions). Whatever exists and this user
/// can reach is returned, canonical; Codex's sandbox helper denies each one (patch 0019), so
/// bubblewrap masks it. The air-gap seals' folder is not one of them: commands read the seals.
pub const HOST_SOCKETS: [&str; 7] = [
    "run/docker.sock",
    "run/containerd/containerd.sock",
    "run/podman",
    "run/dbus/system_bus_socket",
    "var/run/dbus/system_bus_socket",
    "run/snapd.socket",
    "run/snapd-snap.socket",
];

/// The same, under the user's runtime folder (`$XDG_RUNTIME_DIR`, else `/run/user/<uid>`).
pub const HOST_SOCKETS_IN_RUNTIME_DIR: [&str; 7] = [
    "bus",
    "systemd",
    "docker.sock",
    "docker",
    "podman",
    "containerd",
    "mightling",
];

/// [`HOST_SOCKETS`] and [`HOST_SOCKETS_IN_RUNTIME_DIR`] that exist here.
#[cfg(not(windows))]
pub fn host_sockets() -> Vec<PathBuf> {
    host_sockets_in(Path::new("/"), runtime_dir().as_deref())
}

/// The control sockets Codex's sandbox helper denies to one command (patch 0019): every one of
/// [`host_sockets`] when the command's level is `on`, none at `off`. At `off` the workspace-write
/// sandbox is Codex's own and lets a command use Docker, which is root on the host; at `on` the
/// sockets are masked, so nothing reaches the network or the host through a daemon.
#[cfg(not(windows))]
pub fn host_sockets_at(on: bool) -> Vec<PathBuf> {
    if on { host_sockets() } else { Vec::new() }
}

/// [`host_sockets`] under another root and runtime folder, for tests.
pub fn host_sockets_in(root: &Path, runtime: Option<&Path>) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = HOST_SOCKETS
        .iter()
        .map(|path| root.join(path))
        .chain(runtime.into_iter().flat_map(|dir| {
            HOST_SOCKETS_IN_RUNTIME_DIR
                .iter()
                .map(move |name| dir.join(name))
        }))
        // A folder this user cannot open (root's /run/containerd) holds nothing it can reach.
        .filter(|path| path.exists() && (!path.is_dir() || std::fs::read_dir(path).is_ok()))
        .filter_map(|path| std::fs::canonicalize(path).ok())
        .collect();
    found.sort();
    found.dedup();
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(path: &str, text: &str) -> (PathBuf, String) {
        (PathBuf::from(path), text.to_string())
    }

    #[test]
    fn a_session_is_judged_by_the_repository_it_works_in() {
        if std::env::var_os("DREAMFERENCE_CONFIG_PATH").is_some() {
            return; // That file replaces the repository's.
        }
        let repo = std::env::temp_dir().join(format!("ling-airgapped-repo-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        std::fs::write(
            repo.join("dreamference.toml"),
            "mightling_airgapped = \"on\"\n",
        )
        .unwrap();
        let resolved = resolve_in(&[], None, &repo);
        let _ = std::fs::remove_dir_all(&repo);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(
            resolved.source,
            Source::ConfigFile(repo.join("dreamference.toml"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_host_control_sockets_are_masked_only_at_on() {
        assert_eq!(host_sockets_at(true), host_sockets());
        assert!(host_sockets_at(false).is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn the_host_control_sockets_that_exist_are_found_canonical() {
        let root =
            std::env::temp_dir().join(format!("ling-airgapped-sockets-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let runtime = root.join("run/user/1000");
        for dir in [
            "run/dbus",
            "run/user/1000/systemd",
            "run/user/1000/ling-airgapped",
            "var",
        ] {
            std::fs::create_dir_all(root.join(dir)).unwrap();
        }
        // A listening socket stands in for docker's; files for the rest.
        let docker = std::os::unix::net::UnixListener::bind(root.join("run/docker.sock")).unwrap();
        for file in [
            "run/dbus/system_bus_socket",
            "run/user/1000/bus",
            "run/user/1000/systemd/private",
        ] {
            std::fs::write(root.join(file), "").unwrap();
        }
        // /var/run is a link to /run on most systems: one entry, at its canonical path.
        std::os::unix::fs::symlink(root.join("run"), root.join("var/run")).unwrap();
        let found = host_sockets_in(&root, Some(&runtime));
        let canonical = |path: &str| std::fs::canonicalize(root.join(path)).unwrap();
        assert_eq!(
            found,
            vec![
                canonical("run/dbus/system_bus_socket"),
                canonical("run/docker.sock"),
                canonical("run/user/1000/bus"),
                canonical("run/user/1000/systemd"),
            ]
        );
        // The seals' folder stays visible: commands read the seals.
        assert!(!found.iter().any(|path| path.ends_with("ling-airgapped")));
        assert!(host_sockets_in(&root.join("none"), None).is_empty());
        drop(docker);
        let _ = std::fs::remove_dir_all(&root);
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
        assert_eq!(Level::parse(DEFAULT_MIGHTLING_AIRGAPPED), Some(Level::Off));
    }

    #[test]
    fn the_key_is_read_only_at_the_top_level() {
        assert_eq!(
            toml_value("mightling_airgapped = \"on\"\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("model = \"x\"\nmightling_airgapped='on' # no network\n").as_deref(),
            Some("on")
        );
        assert_eq!(toml_value("[night]\nmightling_airgapped = \"on\"\n"), None);
        assert_eq!(toml_value("mightling_airgapped_other = \"on\"\n"), None);
        assert_eq!(toml_value(""), None);
        // Windows line ends, tabs, a commented-out line and a comment holding another value.
        assert_eq!(
            toml_value("model = \"x\"\r\nmightling_airgapped = \"on\"\r\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("mightling_airgapped\t=\t\"on\"\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("# mightling_airgapped = \"off\"\nmightling_airgapped = \"on\"\n")
                .as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("mightling_airgapped = \"on\" # was \"off\"\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("  [ night ]\nmightling_airgapped = \"on\"\n"),
            None
        );
    }

    #[test]
    fn what_comes_before_the_key_is_read_as_toml_not_as_lines() {
        // Each of these is `on` to a TOML parser, and was missed or read as something else.
        for text in [
            // A string or an array spanning lines, with a line that looks like a header.
            "note = \"\"\"\n[draft]\n\"\"\"\nmightling_airgapped = \"on\"\n",
            "note = '''\n[x]\n'''\nmightling_airgapped = 'on'\n",
            "x = [\n  [1, 2],\n]\nmightling_airgapped = \"on\"\n",
            // A string spanning lines, with a line that looks like the key.
            "note = \"\"\"\nmightling_airgapped = \"off\"\n\"\"\"\nmightling_airgapped = \"on\"\n",
            // The key quoted, and the value escaped or spanning lines.
            "\"mightling_airgapped\" = \"on\"\n",
            "'mightling_airgapped' = \"on\"\n",
            "mightling_airgapped = \"\\u006fn\"\n",
            "mightling_airgapped = \"\"\"\non\"\"\"\n",
        ] {
            assert_eq!(toml_value(text).as_deref(), Some("on"), "{text:?}");
        }
        // Under another key, or after a header, it is still not the top-level key.
        for text in [
            "night.mightling_airgapped = \"on\"\n",
            "t = { mightling_airgapped = \"on\" }\n",
            "\"mightling_airgapped \" = \"on\"\n",
            "x = [\n  1,\n]\n[night]\nmightling_airgapped = \"on\"\n",
        ] {
            assert_eq!(toml_value(text), None, "{text:?}");
        }
    }

    #[test]
    fn text_that_is_not_toml_is_still_read_line_by_line() {
        assert_eq!(
            toml_value("mightling_airgapped = on\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("x = [\nmightling_airgapped = \"on\"\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("mightling_airgapped = \"on\n").as_deref(),
            Some("on")
        );
    }

    #[test]
    fn a_boolean_is_read_as_pythons_configuration_reads_it() {
        // Python takes `true` for `on`; the launcher took it for no level and stayed off.
        assert_eq!(
            toml_value("mightling_airgapped = true\n").as_deref(),
            Some("on")
        );
        assert_eq!(
            toml_value("mightling_airgapped = false # x\n").as_deref(),
            Some("off")
        );
        let resolved = resolve_from(
            None,
            None,
            &[config(
                "/repo/dreamference.toml",
                "mightling_airgapped = true\n",
            )],
        );
        assert_eq!(resolved.level, Level::On);
    }

    #[test]
    fn a_byte_order_mark_does_not_hide_the_key() {
        // Windows PowerShell 5's `Out-File -Encoding UTF8` starts the file with one.
        let bom = "\u{feff}mightling_airgapped = \"on\"\r\n";
        assert_eq!(toml_value(bom).as_deref(), Some("on"));
        assert_eq!(
            toml_value("\u{feff}[night]\nmightling_airgapped = \"on\"\n"),
            None
        );
        // The user's `on`, so written, still beats the repository's `off`.
        let user_on = config("/home/u/.config/dreamference/config.toml", bom);
        let repo_off = config("/repo/dreamference.toml", "mightling_airgapped = \"off\"\n");
        let resolved = resolve_from(None, None, &[repo_off, user_on.clone()]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::ConfigFile(user_on.0))
        );
    }

    #[test]
    fn two_files_at_the_same_level_name_the_first() {
        let repo_on = config("/repo/dreamference.toml", "mightling_airgapped = \"on\"\n");
        let user_on = config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"ON\"\n",
        );
        let resolved = resolve_from(None, None, &[repo_on.clone(), user_on]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::ConfigFile(repo_on.0))
        );
    }

    #[test]
    fn an_invalid_value_in_one_file_counts_as_on() {
        let user_off = config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"off\"\n",
        );
        // The agent writes a value no level has into the repository's file: it cannot loosen.
        let repo_bad = config(
            "/repo/dreamference.toml",
            "mightling_airgapped = \"none\"\n",
        );
        let resolved = resolve_from(None, None, &[repo_bad.clone(), user_off]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::ConfigFile(repo_bad.0))
        );
        assert_eq!(
            resolved.invalid,
            vec![
                "\"none\" from mightling_airgapped in /repo/dreamference.toml is not a level: taken as on"
                    .to_string()
            ]
        );
    }

    #[test]
    fn an_exposed_session_file_cannot_loosen_a_sealed_or_configured_on_from_any_tier() {
        let user_on = [config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"on\"\n",
        )];
        // An invalid session value falls through to the configured level, exposed or not.
        let resolved = resolve_guarded(Some("of"), None, &user_on, false, true);
        assert_eq!(resolved.level, Level::On);
        // The environment is ling's own, not a file a command could write: it decides as usual.
        let resolved = resolve_guarded(None, Some("off"), &user_on, false, true);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::Off, Source::Environment)
        );
        // A seal wins over everything, including an exposed session file saying `off`.
        let resolved = resolve_guarded(Some("off"), Some("off"), &[], true, true);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::Sealed)
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_writable_root_that_links_to_the_seals_is_refused() {
        let base =
            std::env::temp_dir().join(format!("ling-airgapped-seal-link-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let seals = base.join("run/user/1/ling-airgapped");
        std::fs::create_dir_all(&seals).unwrap();
        std::os::unix::fs::symlink(base.join("run/user/1"), base.join("runtime")).unwrap();
        // The root is named through a link; it still holds the seals.
        let root = [base.join("runtime")];
        let refused = refusal(false, &root, true, Some(&seals), false);
        // A root beside the seals' folder, sharing a prefix, does not.
        std::fs::create_dir_all(base.join("run/user/1/ling-airgapped-not")).unwrap();
        let beside = [base.join("run/user/1/ling-airgapped-not")];
        let allowed = refusal(false, &beside, true, Some(&seals), false);
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(refused.map(|r| r.0), Some("writable_roots"));
        assert_eq!(allowed, None);
    }

    #[test]
    fn the_first_tier_that_has_a_valid_value_wins() {
        let configs = [config(
            "/repo/dreamference.toml",
            "mightling_airgapped = \"on\"\n",
        )];
        let resolved = resolve_from(Some("off\n"), Some("on"), &configs);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::Off, Source::Session)
        );
        let resolved = resolve_from(None, Some("off"), &configs);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::Off, Source::Environment)
        );
        let resolved = resolve_from(None, None, &[]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::Off, Source::Default)
        );
    }

    #[test]
    fn an_invalid_value_is_named_and_counts_as_on() {
        let resolved = resolve_from(Some("sealed"), Some("off"), &[]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::Session)
        );
        assert_eq!(
            resolved.invalid,
            vec!["\"sealed\" from this session is not a level: taken as on".to_string()]
        );
        // A session, the environment or a file still set to the removed `duckduckgo` level too.
        let resolved = resolve_from(None, Some("duckduckgo"), &[]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::Environment)
        );
        let repo_ddg = config(
            "/repo/dreamference.toml",
            "mightling_airgapped = \"duckduckgo\"\n",
        );
        assert_eq!(resolve_from(None, None, &[repo_ddg]).level, Level::On);
    }

    #[test]
    fn a_seal_is_found_by_its_writers_pid() {
        let dir = std::env::temp_dir().join(format!("ling-airgapped-seal-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("0c4f-thread"), "4242\n").unwrap();
        assert!(sealed_in(&dir, 4242));
        assert!(!sealed_in(&dir, 4243));
        assert!(!sealed_in(&dir.join("missing"), 4242));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_file_that_cannot_be_read_counts_as_on_and_one_that_is_missing_does_not() {
        let dir = std::env::temp_dir().join(format!("ling-airgapped-read-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let missing = dir.join("missing.toml");
        assert_eq!(read_file(&missing), None);
        let binary = dir.join("binary.toml");
        std::fs::write(&binary, [0xff, 0xfe, 0x00, 0x41]).unwrap();
        let read = read_file(&binary).expect("the file exists");
        assert!(read.is_err(), "{read:?}");
        let resolved = resolve_read(None, None, &[(binary.clone(), read)]);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::ConfigFile(binary))
        );
        assert_eq!(resolved.invalid.len(), 1);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn between_the_two_files_the_strictest_wins() {
        let repo_off = config("/repo/dreamference.toml", "mightling_airgapped = \"off\"\n");
        let user_on = config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"on\"\n",
        );
        // A repository file the agent can write does not loosen the user's level.
        let resolved = resolve_from(None, None, &[repo_off.clone(), user_on.clone()]);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.source, Source::ConfigFile(user_on.0.clone()));
        // It may tighten it.
        let repo_on = config("/repo/dreamference.toml", "mightling_airgapped = \"on\"\n");
        let user_off = config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"off\"\n",
        );
        let resolved = resolve_from(None, None, &[repo_on.clone(), user_off]);
        assert_eq!(resolved.source, Source::ConfigFile(repo_on.0));
        // A file without the key does not count.
        let resolved = resolve_from(
            None,
            None,
            &[
                config("/repo/dreamference.toml", "model = \"x\"\n"),
                repo_off,
            ],
        );
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

    /// Run by [`a_seal_is_seen_from_a_command_working_in_any_folder`] in a child process, whose
    /// working directory and runtime directory it sets; does nothing in a normal run.
    #[test]
    fn sealed_probe() {
        if let Some(id) = std::env::var_os("LING_AIRGAPPED_SEAL_PROBE") {
            let id = id.to_string_lossy().into_owned();
            println!("sealed={}", sealed(&[id.as_str()]));
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_seal_is_seen_from_a_command_working_in_any_folder() {
        // Codex's sandbox helper runs in the command's own folder, which the model chooses. From
        // `/` (or any folder above the runtime directory) the runtime directory lies in the working
        // directory, and the seal `ling` wrote there must still hold the session at `on`.
        let runtime =
            std::env::temp_dir().join(format!("ling-airgapped-probe-{}", std::process::id()));
        let id = "0199a7c2-probe";
        std::fs::create_dir_all(runtime.join(SEAL_DIR)).unwrap();
        std::fs::write(runtime.join(SEAL_DIR).join(id), "").unwrap();
        let test = module_path!()
            .split_once("::")
            .map_or("sealed_probe".to_string(), |(_, path)| {
                format!("{path}::sealed_probe")
            });
        let sealed_from = |cwd: &Path| {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args([test.as_str(), "--exact", "--nocapture", "--test-threads=1"])
                .current_dir(cwd)
                .env("XDG_RUNTIME_DIR", &runtime)
                .env("LING_AIRGAPPED_SEAL_PROBE", id)
                .output()
                .unwrap();
            String::from_utf8_lossy(&output.stdout).contains("sealed=true")
        };
        let from_root = sealed_from(Path::new("/"));
        let from_runtime = sealed_from(&runtime);
        let _ = std::fs::remove_dir_all(&runtime);
        assert!(from_root, "a command working in / must see the seal");
        assert!(
            from_runtime,
            "a command working in the runtime directory must see the seal"
        );
    }

    /// Run by [`a_stripped_environment_still_finds_the_session_files_and_the_runtime_folder`] in a
    /// child process, whose environment it sets; does nothing in a normal run.
    #[test]
    fn paths_probe() {
        if std::env::var_os("LING_AIRGAPPED_PATHS_PROBE").is_some() {
            println!("session_file={:?}", session_file("0199a7c2-probe"));
            println!("runtime_dir={:?}", runtime_dir());
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_stripped_environment_still_finds_the_session_files_and_the_runtime_folder() {
        // A user's `shell_environment_policy` can strip `CODEX_HOME` and `XDG_RUNTIME_DIR` from a
        // command's environment: the web commands must still find the session's file and the seals.
        let base =
            std::env::temp_dir().join(format!("ling-airgapped-paths-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let runtime = base.join("runtime");
        std::fs::create_dir_all(&runtime).unwrap();
        let test = module_path!()
            .split_once("::")
            .map_or("paths_probe".to_string(), |(_, path)| {
                format!("{path}::paths_probe")
            });
        let probe = |env: &[(&str, Option<&Path>)]| {
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command
                .args([test.as_str(), "--exact", "--nocapture", "--test-threads=1"])
                .env("LING_AIRGAPPED_PATHS_PROBE", "1")
                .env("HOME", base.join("home"));
            for (name, value) in env {
                match value {
                    Some(value) => command.env(name, value),
                    None => command.env_remove(name),
                };
            }
            String::from_utf8_lossy(&command.output().unwrap().stdout).into_owned()
        };
        let session_file = |path: PathBuf| {
            format!(
                "session_file={:?}",
                Some(path.join(SESSION_DIR).join("0199a7c2-probe"))
            )
        };
        let mightling = base.join("home/.mightling");
        for codex_home in [None, Some(Path::new(""))] {
            let printed = probe(&[("CODEX_HOME", codex_home)]);
            assert!(
                printed.contains(&session_file(mightling.clone())),
                "{printed}"
            );
        }
        let printed = probe(&[("CODEX_HOME", Some(&base.join("codex-home")))]);
        assert!(
            printed.contains(&session_file(base.join("codex-home"))),
            "{printed}"
        );

        let printed = probe(&[("XDG_RUNTIME_DIR", Some(&runtime))]);
        assert!(
            printed.contains(&format!("runtime_dir={:?}", Some(&runtime))),
            "{printed}"
        );
        // Unset, relative or missing, the variable gives way to the folder systemd makes per login.
        #[cfg(target_os = "linux")]
        let fallback = {
            use std::os::unix::fs::MetadataExt;
            let uid = std::fs::metadata(&runtime).unwrap().uid();
            Some(PathBuf::from(format!("/run/user/{uid}"))).filter(|dir| dir.is_dir())
        };
        #[cfg(not(target_os = "linux"))]
        let fallback: Option<PathBuf> = None;
        let missing = base.join("missing");
        for value in [None, Some(Path::new("relative")), Some(missing.as_path())] {
            let printed = probe(&[("XDG_RUNTIME_DIR", value)]);
            assert!(
                printed.contains(&format!("runtime_dir={fallback:?}")),
                "{printed}"
            );
        }
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_seal_holds_the_session_at_on_whatever_the_files_say() {
        // A command rewrote the session file to `off`, or deleted the key from a config file.
        let configs = [config(
            "/repo/dreamference.toml",
            "mightling_airgapped = \"off\"\n",
        )];
        let resolved = resolve_guarded(Some("off\n"), Some("off"), &configs, true, true);
        assert_eq!(
            (resolved.level, resolved.source.clone()),
            (Level::On, Source::Sealed)
        );
        assert_eq!(
            resolved.invalid,
            vec!["ignored \"off\" from this session: the session is held at on".to_string()]
        );
        // A seal on a session the files still put at `on` says nothing extra.
        let resolved = resolve_guarded(Some("on"), None, &[], true, false);
        assert_eq!(
            (resolved.level, resolved.source, resolved.invalid.len()),
            (Level::On, Source::Sealed, 0)
        );
        // Without a seal the files decide, as before.
        let resolved = resolve_guarded(Some("off"), Some("on"), &[], false, false);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::Off, Source::Session)
        );
    }

    #[test]
    fn with_no_place_for_a_seal_an_exposed_session_file_may_only_tighten() {
        let user_on = [config(
            "/home/u/.config/dreamference/config.toml",
            "mightling_airgapped = \"on\"\n",
        )];
        let resolved = resolve_guarded(Some("off"), None, &user_on, false, true);
        assert_eq!(resolved.level, Level::On);
        assert_eq!(resolved.source, Source::ConfigFile(user_on[0].0.clone()));
        assert!(resolved.invalid[0].starts_with(
            "ignored \"off\" from this session: commands can write the level file here"
        ));
        let resolved = resolve_guarded(Some("off"), Some("on"), &[], false, true);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::Environment)
        );
        // Tightening is still the session's to do, and nothing configured means nothing to loosen.
        let resolved = resolve_guarded(Some("on"), Some("off"), &[], false, true);
        assert_eq!(
            (resolved.level, resolved.source),
            (Level::On, Source::Session)
        );
        let resolved = resolve_guarded(Some("off"), None, &[], false, true);
        assert_eq!(
            (resolved.level, resolved.source, resolved.invalid.len()),
            (Level::Off, Source::Session, 0)
        );
    }

    #[test]
    fn full_access_is_refused_only_at_a_configured_on() {
        let refused = refusal(true, &[], true, None, false).unwrap_or_default();
        assert_eq!(
            (refused.0, refused.1.as_str()),
            ("sandbox_mode", "danger-full-access")
        );
        assert_eq!(refusal(true, &[], false, None, true), None);
        // Full Access is not judged by the seal rule: it has no roots to judge.
        assert_eq!(
            refusal(
                true,
                &[PathBuf::from("/")],
                false,
                Some(Path::new("/run/user/1/ling-airgapped")),
                true
            ),
            None
        );
    }

    #[test]
    fn a_writable_root_holding_the_seals_is_refused_while_anything_is_on() {
        let seals = Path::new("/run/user/1/ling-airgapped");
        let run = [PathBuf::from("/run")];
        let project = [PathBuf::from("/home/u/project"), PathBuf::from("/tmp")];
        assert_eq!(
            refusal(false, &run, true, Some(seals), false).map(|r| r.1),
            Some("/run".to_string())
        );
        assert_eq!(
            refusal(false, &run, false, Some(seals), true).map(|r| r.0),
            Some("writable_roots")
        );
        assert_eq!(
            refusal(false, &[seals.to_path_buf()], true, Some(seals), false).map(|r| r.0),
            Some("writable_roots")
        );
        // Nothing on, nothing sealed, or ordinary roots: allowed.
        assert_eq!(refusal(false, &run, false, Some(seals), false), None);
        assert_eq!(refusal(false, &project, true, Some(seals), true), None);
        assert_eq!(refusal(false, &run, true, None, true), None);
    }

    #[test]
    fn a_commands_environment_map_decides_its_level() {
        // The level variable in the command's map wins over this process's and the files'.
        let map = |var: &'static str, value: &'static str| {
            move |key: &str| (key == var).then(|| value.to_string())
        };
        assert!(sealed_for_env(map(ENV_VAR, "on")));
        assert!(!sealed_for_env(map(ENV_VAR, "off")));
        // Ids that are not ids name no session file and no seal.
        assert!(!sealed_for_env(|key: &str| match key {
            "CODEX_THREAD_ID" => Some("../x".to_string()),
            ENV_VAR => Some("off".to_string()),
            _ => None,
        }));
    }

    #[test]
    fn the_read_only_sandbox_has_the_network_unless_on_or_switched_off() {
        let map = |var: &'static str, value: &'static str| {
            move |key: &str| (key == var).then(|| value.to_string())
        };
        for value in [None, Some(""), Some("on"), Some("true"), Some("1")] {
            assert!(read_only_network_allowed(value), "{value:?}");
        }
        for value in ["off", "OFF", " false ", "0", "no"] {
            assert!(!read_only_network_allowed(Some(value)), "{value}");
        }
        if read_only_network_allowed(std::env::var(READ_ONLY_NETWORK_ENV_VAR).ok().as_deref()) {
            assert!(read_only_network(map(ENV_VAR, "off")));
        }
        assert!(!read_only_network(map(ENV_VAR, "on")));
    }

    #[test]
    fn the_verbatim_prefix_is_removed_from_drive_paths_only() {
        assert_eq!(
            without_verbatim_prefix(PathBuf::from(r"\\?\C:\Users\j")),
            PathBuf::from(r"C:\Users\j")
        );
        assert_eq!(
            without_verbatim_prefix(PathBuf::from(r"\\?\UNC\server\share")),
            PathBuf::from(r"\\?\UNC\server\share")
        );
        assert_eq!(
            without_verbatim_prefix(PathBuf::from("/home/u")),
            PathBuf::from("/home/u")
        );
    }

    #[test]
    fn the_home_folder_falls_back_to_userprofile() {
        use std::ffi::OsString;
        let windows = Some(OsString::from(r"C:\Users\Jane Doe"));
        // Windows: `HOME` is usually unset, and a user-level `mightling_airgapped` must still be read.
        assert_eq!(
            home_from(None, windows.clone()),
            Some(PathBuf::from(r"C:\Users\Jane Doe"))
        );
        assert_eq!(
            home_from(Some(OsString::new()), windows.clone()),
            Some(PathBuf::from(r"C:\Users\Jane Doe"))
        );
        assert_eq!(
            home_from(Some(OsString::from("/home/u")), windows),
            Some(PathBuf::from("/home/u"))
        );
        assert_eq!(home_from(None, Some(OsString::new())), None);
        assert_eq!(home_from(None, None), None);
    }

    #[test]
    fn windows_seals_are_kept_under_local_app_data() {
        use std::ffi::OsString;
        // The sandbox's own accounts cannot write the user's profile (WINDOWS_ARM §7.3).
        let local = r"C:\Users\Jane Doe\AppData\Local";
        let dir = windows_runtime_dir_from(Some(OsString::from(local))).unwrap();
        assert_eq!(dir, Path::new(local).join("Mightling"));
        assert_eq!(
            dir.join(SEAL_DIR),
            Path::new(local).join("Mightling").join("ling-airgapped")
        );
        assert_eq!(windows_runtime_dir_from(Some(OsString::new())), None);
        assert_eq!(windows_runtime_dir_from(None), None);
    }

    #[test]
    fn a_path_is_within_a_writable_root_by_its_real_location() {
        let base =
            std::env::temp_dir().join(format!("ling-airgapped-roots-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let home = base.join("home");
        std::fs::create_dir_all(home.join(".mightling")).unwrap_or_default();
        std::fs::create_dir_all(base.join("elsewhere")).unwrap_or_default();
        // `ling` started in the home directory: `~/.mightling` is inside the working directory.
        let roots = writable_roots(&home, None);
        if cfg!(windows) {
            assert_eq!(roots, vec![home.clone()]);
        } else {
            assert_eq!(roots, vec![home.clone(), PathBuf::from("/tmp")]);
        }
        assert!(within(
            &home.join(".mightling"),
            std::slice::from_ref(&home)
        ));
        assert!(within(
            &home.join(".config/dreamference/config.toml"),
            std::slice::from_ref(&home)
        ));
        // Started in a project folder: it is not.
        assert!(!within(&home.join(".mightling"), &[home.join("project")]));
        assert!(!within(
            &base.join("elsewhere"),
            std::slice::from_ref(&home)
        ));
        // A file not created yet, under a folder not created yet, is inside the folder it would be in.
        assert!(within(
            &home
                .join(".config")
                .join("dreamference")
                .join("config.toml"),
            std::slice::from_ref(&home)
        ));
        assert!(!within(
            &base.join("elsewhere").join("not-yet"),
            std::slice::from_ref(&home)
        ));
        // A name that only shares a prefix is not inside, and a link is followed to where it leads.
        assert!(!within(&base.join("home2"), std::slice::from_ref(&home)));
        #[cfg(unix)]
        {
            let _ = std::os::unix::fs::symlink(&home, base.join("link"));
            assert!(within(
                &base.join("link").join(".mightling"),
                std::slice::from_ref(&home)
            ));
            // A file not yet written, reached through the link: still inside (macOS's /tmp and
            // /var are such links, and a user config file often does not exist yet).
            assert!(within(
                &base.join("link").join(".config/dreamference/config.toml"),
                std::slice::from_ref(&home)
            ));
            assert!(within(
                &home.join(".config/dreamference/config.toml"),
                &[base.join("link")]
            ));
        }
        let plain = writable_roots(&home, None).len();
        assert_eq!(
            writable_roots(&home, Some(Path::new("/var/tmp/x"))).len(),
            plain + 1
        );
        assert_eq!(writable_roots(&home, Some(Path::new(""))).len(), plain);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_string_or_a_statement_toml_refuses_does_not_lend_a_later_key_its_place() {
        // A basic or a literal string may not span lines, and a statement ends its line: text
        // that breaks either is not TOML, and its first key line is the one read.
        for text in [
            "note = \"x\nmightling_airgapped = 'on'\"\nmightling_airgapped = \"off\"\n",
            "note = 'x\nmightling_airgapped = \"on\"'\nmightling_airgapped = \"off\"\n",
            "note = 1 mightling_airgapped = \"off\"\nmightling_airgapped = \"on\"\n",
        ] {
            assert_eq!(toml_value(text).as_deref(), Some("on"), "{text:?}");
        }
        // A multi-line string is no key, and no line starts with the key.
        assert_eq!(toml_value("'''mightling_airgapped''' = \"on\"\n"), None);
    }

    #[test]
    fn text_that_is_not_toml_is_read_only_up_to_its_first_table() {
        // `= =` is not TOML; the key under `[other]` is that table's, not the top level's.
        assert_eq!(
            toml_value("note = = 1\n[other]\nmightling_airgapped = \"off\"\n"),
            None
        );
    }

    #[test]
    fn a_path_lies_within_any_of_the_roots() {
        let roots = [PathBuf::from("/nowhere-a"), PathBuf::from("/nowhere-b")];
        assert!(within(Path::new("/nowhere-b/repo"), &roots));
        assert!(!within(Path::new("/nowhere-c/repo"), &roots));
    }

    #[cfg(unix)]
    #[test]
    fn every_daemon_socket_in_the_runtime_folder_is_found() {
        // `ling web`'s app-server socket under `mightling` starts threads with any permissions.
        let runtime =
            std::env::temp_dir().join(format!("ling-airgapped-runtime-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&runtime);
        let names = [
            "bus",
            "systemd",
            "docker.sock",
            "docker",
            "podman",
            "containerd",
            "mightling",
        ];
        std::fs::create_dir_all(&runtime).unwrap();
        let runtime = std::fs::canonicalize(&runtime).unwrap();
        for name in names {
            std::fs::write(runtime.join(name), "").unwrap();
        }
        let found = host_sockets_in(&runtime.join("none"), Some(&runtime));
        let _ = std::fs::remove_dir_all(&runtime);
        let mut expected: Vec<PathBuf> = names.iter().map(|name| runtime.join(name)).collect();
        expected.sort();
        assert_eq!(found, expected);
    }

    #[test]
    fn a_seal_is_read_only_under_a_session_id() {
        assert!(seal_file_name("0b6e5c1a-7f43-4d1e-9c2a-5d8e3f0a1b2c"));
        for id in ["", "..", "../../etc/passwd", "a/b", "a.b"] {
            assert!(!seal_file_name(id), "{id:?}");
        }
    }
}
