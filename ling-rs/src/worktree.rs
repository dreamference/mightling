//! Where a session's edits land (specs/DREAMFERENCE_MIGHTLING_WORKTREE.md).
//!
//! By default a session edits the user's checkout in place, as Codex's CLI and app do. Worktree
//! mode is the opt-in: a new interactive session in a git repository then starts in a fresh git
//! worktree on a branch of its own, and the user's checkout is not touched until they bring the
//! changes back with `/worktree apply` (or `ling worktree apply`).
//!
//! The choice, first match wins:
//!
//! 1. `DREAMFERENCE_MIGHTLING_WORKTREE=on|off`, for one session or a script.
//! 2. `mightling-worktree-mode = "worktree"|"local"` in `[desktop]` of `$CODEX_HOME/config.toml`,
//!    which `/worktree on|off` and `ling worktree on|off` write. The desktop app reads the same key
//!    for the default of its Local/Worktree toggle, so the two agree. The launcher reads the file
//!    itself, never a repository's `.codex/config.toml`: a clone cannot move a user's edits.
//! 3. Local (in place).
//!
//! No patch is involved. The launcher runs in Codex's own process before Codex parses its
//! arguments, so it creates the worktree, moves the process into it and rewrites `-C`; Codex then
//! starts there as if the user had started it there (its trust check follows a linked worktree to
//! the main checkout, so no trust prompt). The sub-commands of `/worktree` are answered by the
//! composer's `Enter` hook (patch 0029, `compat_keys::enter`) in every mode; `/worktree` alone is
//! still Codex's own picker. Worktree mode applies to a new interactive session only: `exec`,
//! `resume`, `fork`, `review` and `app-server` are never moved, and Codex's own `--worktree` wins.
//!
//! ```text
//! $CODEX_HOME/session-worktrees/<id>/<repository>   the checkout, on branch ling/<id>
//! $CODEX_HOME/session-worktrees/<id>/session.json   source, base, branch, pid, what was applied
//! ```
//!
//! Outside Codex's managed pool (`$CODEX_HOME/worktrees`), whose cleanup the desktop app runs.

use std::ffi::OsString;
use std::path::Path;
use std::path::PathBuf;
use std::process::Output;
use std::process::Stdio;

use serde_json::Value;
use serde_json::json;

/// The variable that overrides the setting for one session.
pub const ENV_VAR: &str = "DREAMFERENCE_MIGHTLING_WORKTREE";

/// The key in `[desktop]` of `$CODEX_HOME/config.toml`, shared with the desktop app.
pub const KEY: &str = "mightling-worktree-mode";

/// Where the session worktrees live, under `$CODEX_HOME`.
pub const DIR: &str = "session-worktrees";

/// The record beside each checkout.
const RECORD: &str = "session.json";

/// Where a removed or discarded session's last state is kept, as the desktop app keeps its own.
const SNAPSHOTS: &str = "refs/mightling/snapshots";

/// The status line the launcher gives a TUI session when the user has chosen none: Codex's default
/// with the git branch added, so the footer always shows the folder and branch edits land in.
pub const STATUS_LINE: &[&str] = &["model-with-reasoning", "current-dir", "git-branch", "thread-name"];

const USAGE: &str = "Usage: /worktree on | off | status | apply | discard   (/worktree alone opens Codex's worktree picker)";
const CLI_USAGE: &str = "Usage: ling worktree [status | on | off | list | apply [<id>] | discard [<id>]]";

/// Where new sessions edit.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// In place, in the user's checkout: the default.
    Local,
    /// In a fresh worktree on a branch of its own.
    Worktree,
}

impl Mode {
    /// The value written in the config file, as the desktop app's toggle names it.
    pub fn value(self) -> &'static str {
        match self {
            Mode::Local => "local",
            Mode::Worktree => "worktree",
        }
    }

    /// Reads a value of the variable or the key; None for one the launcher does not know.
    pub fn parse(value: &str) -> Option<Mode> {
        match value.trim().to_ascii_lowercase().as_str() {
            "on" | "worktree" => Some(Mode::Worktree),
            "off" | "local" | "" => Some(Mode::Local),
            _ => None,
        }
    }
}

/// The setting in force for a new session, and where it came from.
pub fn mode_now(codex_home: &Path) -> (Mode, String) {
    if let Ok(value) = std::env::var(ENV_VAR)
        && let Some(mode) = Mode::parse(&value)
    {
        return (mode, ENV_VAR.to_string());
    }
    match configured(codex_home) {
        Some(mode) => (mode, format!("{KEY} in {}", config_path(codex_home).display())),
        None => (Mode::Local, "the default".to_string()),
    }
}

/// The key in `$CODEX_HOME/config.toml`, if set to a known value.
pub fn configured(codex_home: &Path) -> Option<Mode> {
    let text = std::fs::read_to_string(config_path(codex_home)).ok()?;
    let document: toml::Table = text.parse().ok()?;
    document.get("desktop")?.get(KEY)?.as_str().and_then(Mode::parse)
}

fn config_path(codex_home: &Path) -> PathBuf {
    codex_home.join("config.toml")
}

/// Writes the setting, keeping everything else in the file as it was.
pub fn write_mode(codex_home: &Path, mode: Mode) -> Result<PathBuf, String> {
    let path = config_path(codex_home);
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let mut document: toml_edit::DocumentMut =
        existing.parse().map_err(|err| format!("{} is not valid TOML: {err}", path.display()))?;
    if !document.get("desktop").is_some_and(toml_edit::Item::is_table) {
        document.insert("desktop", toml_edit::Item::Table(toml_edit::Table::new()));
    }
    document["desktop"][KEY] = toml_edit::value(mode.value());
    std::fs::create_dir_all(codex_home).map_err(|err| format!("could not create {}: {err}", codex_home.display()))?;
    std::fs::write(&path, document.to_string()).map_err(|err| format!("could not write {}: {err}", path.display()))?;
    Ok(path)
}

/// One session worktree, as its record says.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    pub id: String,
    /// The folder holding the checkout and the record.
    pub home: PathBuf,
    pub checkout: PathBuf,
    /// The top level of the checkout it was made from: where `apply` brings the changes.
    pub source: PathBuf,
    pub base: String,
    pub branch: String,
    pub pid: u32,
    pub created: String,
    /// The tree last applied to the source, so a second `apply` brings only what is new.
    pub applied: Option<String>,
}

impl Session {
    fn read(home: &Path) -> Option<Session> {
        let record: Value = serde_json::from_str(&std::fs::read_to_string(home.join(RECORD)).ok()?).ok()?;
        let text = |key: &str| record[key].as_str().map(str::to_string);
        Some(Session {
            id: text("id")?,
            home: home.to_path_buf(),
            checkout: PathBuf::from(text("checkout")?),
            source: PathBuf::from(text("source")?),
            base: text("base")?,
            branch: text("branch")?,
            pid: record["pid"].as_u64().and_then(|pid| u32::try_from(pid).ok()).unwrap_or(0),
            created: text("created").unwrap_or_default(),
            applied: text("applied"),
        })
    }

    fn write(&self) -> std::io::Result<()> {
        let record = json!({
            "version": 1,
            "id": self.id,
            "checkout": self.checkout.to_string_lossy(),
            "source": self.source.to_string_lossy(),
            "base": self.base,
            "branch": self.branch,
            "pid": self.pid,
            "created": self.created,
            "applied": self.applied,
        });
        let path = self.home.join(RECORD);
        let staging = self.home.join(format!(".{RECORD}.{}.tmp", std::process::id()));
        std::fs::write(&staging, serde_json::to_string_pretty(&record).unwrap_or_default())?;
        std::fs::rename(&staging, path)
    }

    /// Whether the session that made it is still running (and is not this process).
    fn in_use_elsewhere(&self) -> bool {
        self.pid != 0 && self.pid != std::process::id() && process_alive(self.pid)
    }
}

/// Every session worktree under `$CODEX_HOME`.
pub fn sessions(codex_home: &Path) -> Vec<Session> {
    let Ok(entries) = std::fs::read_dir(codex_home.join(DIR)) else { return Vec::new() };
    let mut found: Vec<Session> = entries.flatten().filter_map(|entry| Session::read(&entry.path())).collect();
    found.sort_by(|a, b| a.id.cmp(&b.id));
    found
}

/// The session worktree `dir` is in, if any.
pub fn session_at(codex_home: &Path, dir: &Path) -> Option<Session> {
    let root = canonical(&codex_home.join(DIR));
    let dir = canonical(dir);
    let relative = dir.strip_prefix(&root).ok()?;
    let id = relative.components().next()?;
    Session::read(&root.join(id))
}

fn canonical(path: &Path) -> PathBuf {
    plain(std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf()))
}

/// A canonical path in the form other programs take. Windows canonicalizes to `\\?\C:\…`, which
/// `git worktree add` cannot create ("could not create leading directories of '//?/C:/…'"), so
/// every worktree session failed there; a drive path loses the prefix (a UNC one keeps it).
fn plain(path: PathBuf) -> PathBuf {
    match path.to_str().and_then(|text| text.strip_prefix(r"\\?\")) {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path,
    }
}

#[cfg(unix)]
fn process_alive(pid: u32) -> bool {
    let Ok(pid) = libc::pid_t::try_from(pid) else { return false };
    // SAFETY: signal 0 only asks whether the process exists; nothing is sent.
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(windows)]
fn process_alive(pid: u32) -> bool {
    crate::airgapped::process_alive(pid)
}

#[cfg(not(any(unix, windows)))]
fn process_alive(_pid: u32) -> bool {
    // Without a cheap check, a session worktree is treated as in use and never pruned.
    true
}

// -- start-up ---------------------------------------------------------------------------------

/// What [`prepare`] decided for a session.
pub struct Prepared {
    pub args: Vec<OsString>,
    pub user_args: Vec<String>,
    /// For the notice.
    pub lines: Vec<String>,
    /// The folder the session starts in, inside its new worktree: the launcher moves there.
    pub cwd: Option<PathBuf>,
}

/// What the launcher does for a session, before Codex parses its arguments: prunes the clean
/// leftovers of finished sessions and, in worktree mode, makes a fresh worktree for a new
/// interactive session started in `session_dir`. `-C`, when given, is rewritten to point into it.
pub fn prepare(args: Vec<OsString>, user_args: Vec<String>, codex_home: &Path, new_interactive: bool, session_dir: &Path) -> Prepared {
    let mut prepared = Prepared { args, user_args, lines: Vec::new(), cwd: None };
    if !new_interactive {
        return prepared;
    }
    let kept = prune(codex_home, session_dir);
    let (mode, _) = mode_now(codex_home);
    if mode == Mode::Local || prepared.user_args.iter().any(|arg| matches!(arg.as_str(), "--worktree" | "--ignore-user-config")) {
        prepared.lines.extend(kept_line(kept));
        return prepared;
    }
    if let Some(session) = session_at(codex_home, session_dir) {
        prepared.lines.push(format!(
            "Worktree mode: this folder is already session {}'s worktree (branch {}); this session works here too.",
            session.id, session.branch
        ));
        return prepared;
    }
    match create(codex_home, session_dir) {
        Ok((session, cwd, dirty)) => {
            prepared.lines.push(format!(
                "Worktree mode: this session works in {} on branch {}, made from {} at {}. Your checkout is not changed: /worktree apply brings the changes there, /worktree discard drops them.",
                session.checkout.display(),
                session.branch,
                session.source.display(),
                short(&session.base),
            ));
            if dirty {
                prepared.lines.push(format!("Your uncommitted changes in {} are not in the worktree.", session.source.display()));
            }
            prepared.lines.extend(kept_line(kept));
            let text = cwd.to_string_lossy().into_owned();
            prepared.args = with_cd(std::mem::take(&mut prepared.args).into_iter(), |arg| arg.to_str().map(str::to_string), OsString::from, &text);
            prepared.user_args = with_cd(std::mem::take(&mut prepared.user_args).into_iter(), |arg| Some(arg.clone()), |arg| arg, &text);
            prepared.cwd = Some(cwd);
        }
        Err(reason) => {
            prepared.lines.push(format!("Worktree mode is on, but {reason}: this session edits {} in place.", session_dir.display()));
        }
    }
    prepared
}

fn kept_line(kept: usize) -> Option<String> {
    (kept > 0).then(|| {
        format!(
            "{kept} earlier worktree session{} of this repository {} changes not applied: ling worktree list.",
            if kept == 1 { "" } else { "s" },
            if kept == 1 { "has" } else { "have" }
        )
    })
}

/// `args` with the value of `-C`/`--cd` replaced by `cwd`; unchanged when there is none (the
/// process has moved into the worktree already).
fn with_cd<T>(args: impl Iterator<Item = T>, text: impl Fn(&T) -> Option<String>, make: impl Fn(String) -> T, cwd: &str) -> Vec<T> {
    let mut out = Vec::new();
    let mut replace_next = false;
    for arg in args {
        if replace_next {
            out.push(make(cwd.to_string()));
            replace_next = false;
            continue;
        }
        match text(&arg).as_deref() {
            Some("-C" | "--cd") => {
                replace_next = true;
                out.push(arg);
            }
            Some(value) if value.starts_with("--cd=") => out.push(make(format!("--cd={cwd}"))),
            Some(value) if value.starts_with("-C") && value.len() > 2 && !value.starts_with("--") => out.push(make(format!("-C{cwd}"))),
            _ => out.push(arg),
        }
    }
    out
}

/// Makes a session worktree for the repository `dir` is in: the session's folder inside it, and
/// whether the source had uncommitted changes.
fn create(codex_home: &Path, dir: &Path) -> Result<(Session, PathBuf, bool), String> {
    let source = git_text(dir, &["rev-parse", "--show-toplevel"]).ok_or_else(|| format!("{} is not in a git repository", dir.display()))?;
    let source = canonical(Path::new(&source));
    let base = git_text(&source, &["rev-parse", "--verify", "HEAD^{commit}"]).ok_or("the repository has no commit yet")?;
    let relative = canonical(dir).strip_prefix(&source).map(Path::to_path_buf).unwrap_or_default();
    let name = source.file_name().map_or_else(|| "checkout".into(), |name| name.to_os_string());
    let root = codex_home.join(DIR);
    std::fs::create_dir_all(&root).map_err(|err| format!("{} could not be created ({err})", root.display()))?;
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M").to_string();
    let (id, home) = (0..64)
        .map(|_| format!("{stamp}-{:04x}", rand::random::<u16>()))
        .find_map(|id| {
            let home = root.join(&id);
            std::fs::create_dir(&home).ok().map(|()| (id, home))
        })
        .ok_or("no free session id")?;
    let checkout = home.join(name);
    let branch = format!("ling/{id}");
    let added = git(&source, &["worktree", "add", "-q", "-b", &branch, &checkout.to_string_lossy(), &base], None);
    if !added.as_ref().is_some_and(|output| output.status.success()) {
        let _ = std::fs::remove_dir_all(&home);
        let why = added.map(|output| String::from_utf8_lossy(&output.stderr).trim().to_string()).unwrap_or_default();
        return Err(format!("git worktree add failed ({why})"));
    }
    let session = Session {
        id,
        home,
        checkout: checkout.clone(),
        source: source.clone(),
        base,
        branch,
        pid: std::process::id(),
        created: chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false),
        applied: None,
    };
    session.write().map_err(|err| format!("the session record could not be written ({err})"))?;
    let dirty = git_text(&source, &["status", "--porcelain"]).is_some_and(|status| !status.is_empty());
    let cwd = checkout.join(relative);
    Ok((session, if cwd.is_dir() { cwd } else { checkout }, dirty))
}

/// Removes the worktrees of finished sessions that hold nothing: no change since their base (or
/// since what was applied). Returns how many of `dir`'s repository are kept with changes.
pub fn prune(codex_home: &Path, dir: &Path) -> usize {
    let here = git_text(dir, &["rev-parse", "--show-toplevel"]).map(|top| canonical(Path::new(&top)));
    let mut kept = 0;
    for session in sessions(codex_home) {
        if session.in_use_elsewhere() || session.pid == std::process::id() {
            continue;
        }
        if !session.checkout.is_dir() {
            remove(&session);
            continue;
        }
        if holds_nothing(&session) {
            remove(&session);
        } else if here.as_deref() == Some(session.source.as_path()) {
            kept += 1;
        }
    }
    kept
}

/// Whether everything in the checkout is its base or what was last applied.
fn holds_nothing(session: &Session) -> bool {
    let Ok(tree) = current_tree(&session.checkout) else { return false };
    let base_tree = git_text(&session.checkout, &["rev-parse", &format!("{}^{{tree}}", session.base)]);
    base_tree.as_deref() == Some(tree.as_str()) || session.applied.as_deref() == Some(tree.as_str())
}

/// Removes a session's worktree, its branch and its folder.
fn remove(session: &Session) {
    if session.checkout.is_dir() {
        let _ = git(&session.source, &["worktree", "remove", "--force", &session.checkout.to_string_lossy()], None);
    } else {
        let _ = git(&session.source, &["worktree", "prune"], None);
    }
    let _ = git(&session.source, &["branch", "-D", &session.branch], None);
    let _ = std::fs::remove_dir_all(&session.home);
}

/// `-c tui.status_line=[…]` ahead of the user's own arguments, for a TUI session whose user has
/// chosen no status line: Codex's default plus the git branch.
pub fn with_status_line(args: Vec<OsString>, user_args: &[String], codex_home: &Path, tui: bool) -> Vec<OsString> {
    let chosen_on_command_line = user_args.windows(2).any(|pair| matches!(pair[0].as_str(), "-c" | "--config") && pair[1].trim_start().starts_with("tui.status_line"))
        || user_args.iter().any(|arg| arg.starts_with("--config=tui.status_line") || arg.starts_with("-ctui.status_line"));
    if !tui || chosen_on_command_line || user_status_line(codex_home) {
        return args;
    }
    let items: Vec<String> = STATUS_LINE.iter().map(|item| format!("\"{item}\"")).collect();
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), format!("tui.status_line=[{}]", items.join(",")).into()]);
    out.extend(args);
    out
}

fn user_status_line(codex_home: &Path) -> bool {
    std::fs::read_to_string(config_path(codex_home))
        .ok()
        .and_then(|text| text.parse::<toml::Table>().ok())
        .is_some_and(|document| document.get("tui").and_then(|tui| tui.get("status_line")).is_some())
}

// -- the command ------------------------------------------------------------------------------

/// `Enter` on `/worktree <sub-command>` in the composer: the lines to show. None for anything
/// else, `/worktree` alone included, which stays Codex's picker.
pub fn enter(text: &str) -> Option<String> {
    let rest = text.trim().strip_prefix("/worktree")?;
    if !rest.starts_with(char::is_whitespace) || rest.trim().is_empty() {
        return None;
    }
    let codex_home = codex_utils_home_dir::find_codex_home().ok()?.as_path().to_path_buf();
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    Some(command(&codex_home, &cwd, rest.trim(), true).0.join("\n"))
}

/// `ling worktree …` from a shell.
pub fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("ling worktree: could not resolve CODEX_HOME");
        return 2;
    };
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let (lines, code) = command(&codex_home, &cwd, &args.join(" "), false);
    for line in lines {
        if code == 0 {
            println!("{line}");
        } else {
            eprintln!("{line}");
        }
    }
    code
}

/// One `/worktree` or `ling worktree` request, worked in `cwd`. `in_session`: asked from inside a
/// running session, which is this process.
pub fn command(codex_home: &Path, cwd: &Path, line: &str, in_session: bool) -> (Vec<String>, i32) {
    let words: Vec<&str> = line.split_whitespace().collect();
    let usage = if in_session { USAGE } else { CLI_USAGE };
    match words.as_slice() {
        [] | ["status"] => (status(codex_home, cwd, in_session), 0),
        ["on"] => set(codex_home, Mode::Worktree, in_session),
        ["off"] => set(codex_home, Mode::Local, in_session),
        ["list"] => (list(codex_home, cwd), 0),
        ["apply"] => match session_at(codex_home, cwd) {
            Some(session) => {
                let into = session.source.clone();
                apply_session(session, &into)
            }
            None => (vec![format!("{} is edited in place: there is nothing to apply. (ling worktree list names the worktree sessions of this repository.)", cwd.display())], 1),
        },
        ["apply", id] => match find(codex_home, cwd, id) {
            Ok(session) => {
                let into = session_source(cwd, &session);
                apply_session(session, &into)
            }
            Err(message) => (vec![message], 1),
        },
        ["discard"] => match session_at(codex_home, cwd) {
            Some(session) => discard(&session, in_session),
            None => (vec![format!("{} is edited in place: there is no worktree to discard.", cwd.display())], 1),
        },
        ["discard", id] => match find(codex_home, cwd, id) {
            Ok(session) => {
                // Named by its id, the session this process runs in is still reset in place.
                let own = in_session && session_at(codex_home, cwd).is_some_and(|here| here.id == session.id);
                discard(&session, own)
            }
            Err(message) => (vec![message], 1),
        },
        ["help" | "-h" | "--help"] => (vec![usage.to_string()], 0),
        _ => (vec![format!("Unknown: {line}"), usage.to_string()], 2),
    }
}

/// The checkout changes are applied to: the user's current checkout of the session's repository
/// when asked from one, else the checkout the session was made from.
fn session_source(cwd: &Path, session: &Session) -> PathBuf {
    let here = git_text(cwd, &["rev-parse", "--show-toplevel"]).map(|top| canonical(Path::new(&top)));
    match here {
        Some(here) if here != session.checkout && same_repository(&here, &session.source) => here,
        _ => session.source.clone(),
    }
}

fn same_repository(a: &Path, b: &Path) -> bool {
    let common = |dir: &Path| git_text(dir, &["rev-parse", "--path-format=absolute", "--git-common-dir"]).map(|path| canonical(Path::new(&path)));
    common(a).is_some() && common(a) == common(b)
}

fn find(codex_home: &Path, cwd: &Path, id: &str) -> Result<Session, String> {
    let mut matches: Vec<Session> = sessions(codex_home).into_iter().filter(|session| session.id.starts_with(id) || session.branch == id).collect();
    match matches.len() {
        1 => Ok(matches.remove(0)),
        0 => Err(format!("No worktree session {id} (ling worktree list, from {}).", cwd.display())),
        _ => Err(format!("{id} names several worktree sessions; give more of the id.")),
    }
}

fn set(codex_home: &Path, mode: Mode, in_session: bool) -> (Vec<String>, i32) {
    let path = match write_mode(codex_home, mode) {
        Ok(path) => path,
        Err(err) => return (vec![format!("Could not save the setting: {err}")], 1),
    };
    let mut lines = vec![match mode {
        Mode::Worktree => format!(
            "Worktree mode on ({KEY} = \"worktree\" in {}): new sessions in a git repository start in a fresh worktree on a branch of their own, and your checkout changes only when you apply. The desktop app's new threads default to Worktree too.",
            path.display()
        ),
        Mode::Local => format!(
            "Worktree mode off ({KEY} = \"local\" in {}): new sessions edit your checkout in place, as Codex does.",
            path.display()
        ),
    }];
    if in_session {
        lines.push("This session stays where it is; the footer shows the folder and branch it edits. Codex's /worktree picker can move this conversation into a worktree now.".to_string());
    }
    if let Ok(value) = std::env::var(ENV_VAR)
        && !value.trim().is_empty()
    {
        lines.push(format!("{ENV_VAR}={value} is set, and it still wins for sessions started from this environment."));
    }
    (lines, 0)
}

fn status(codex_home: &Path, cwd: &Path, in_session: bool) -> Vec<String> {
    let (mode, source) = mode_now(codex_home);
    let setting = match mode {
        Mode::Local => "off: new sessions edit in place",
        Mode::Worktree => "on: new sessions start in a fresh worktree",
    };
    let mut lines = vec![format!("Worktree mode {setting} ({source}).")];
    let what = if in_session { "This session started in" } else { "This folder is" };
    match session_at(codex_home, cwd) {
        Some(session) => {
            lines.push(format!("{what} worktree session {}: {} on branch {}.", session.id, session.checkout.display(), session.branch));
            lines.push(format!("Made from {} at {}{}.", session.source.display(), short(&session.base), session.applied.as_ref().map(|_| ", applied there before").unwrap_or_default()));
            match pending(&session) {
                Ok(stat) if stat.is_empty() => lines.push("Nothing to apply.".to_string()),
                Ok(stat) => {
                    lines.push("Not yet applied:".to_string());
                    lines.extend(stat.lines().map(|line| format!("  {}", line.trim())));
                    lines.push("/worktree apply brings it to your checkout; /worktree discard drops it.".to_string());
                }
                Err(err) => lines.push(err),
            }
        }
        None => match git_text(cwd, &["rev-parse", "--show-toplevel"]) {
            Some(top) => {
                let branch = git_text(cwd, &["branch", "--show-current"]).filter(|name| !name.is_empty()).unwrap_or_else(|| "detached HEAD".to_string());
                lines.push(if in_session { format!("This session edits {top} in place, on {branch}.") } else { format!("This folder is edited in place: {top} on {branch}.") });
            }
            None => lines.push(if in_session {
                format!("This session edits {} in place (not a git repository).", cwd.display())
            } else {
                format!("This folder is edited in place: {} (not a git repository).", cwd.display())
            }),
        },
    }
    if in_session {
        lines.push("The footer's folder is where edits land, should Codex's /worktree picker have moved this conversation since.".to_string());
    }
    lines
}

fn list(codex_home: &Path, cwd: &Path) -> Vec<String> {
    let here = git_text(cwd, &["rev-parse", "--show-toplevel"]).map(|top| canonical(Path::new(&top)));
    let found: Vec<Session> = sessions(codex_home)
        .into_iter()
        .filter(|session| here.as_ref().is_none_or(|here| same_repository(here, &session.source) || *here == session.checkout))
        .collect();
    if found.is_empty() {
        return vec!["No worktree sessions for this repository.".to_string()];
    }
    let mut lines = Vec::new();
    for session in found {
        let state = if session.in_use_elsewhere() { "running" } else { "finished" };
        let pending = match pending(&session) {
            Ok(stat) if stat.is_empty() => "nothing to apply".to_string(),
            Ok(stat) => stat.lines().last().unwrap_or_default().trim().to_string(),
            Err(err) => err,
        };
        lines.push(format!("{}  {}  {state}, {pending}", session.id, session.branch));
        lines.push(format!("    {}", session.checkout.display()));
    }
    lines.push("ling worktree apply <id> brings one's changes here; ling worktree discard <id> removes it.".to_string());
    lines
}

/// The `--stat` of what `apply` would bring, empty when nothing.
fn pending(session: &Session) -> Result<String, String> {
    if !session.checkout.is_dir() {
        return Err(format!("its folder {} is gone", session.checkout.display()));
    }
    let tree = current_tree(&session.checkout)?;
    let from = session.applied.as_deref().unwrap_or(&session.base);
    Ok(git_text(&session.checkout, &["diff", "--stat", from, &tree]).unwrap_or_default())
}

/// The tree of everything in the checkout now, committed or not, untracked files included and
/// ignored ones left out, written without touching the checkout's own index.
fn current_tree(checkout: &Path) -> Result<String, String> {
    let index = std::env::temp_dir().join(format!("ling-worktree-index-{}-{}", std::process::id(), rand::random::<u32>()));
    let own = git_text(checkout, &["rev-parse", "--path-format=absolute", "--git-path", "index"]);
    let copied = own.is_some_and(|own| std::fs::copy(own, &index).is_ok());
    let env = Some(index.as_path());
    let ready = copied || git(checkout, &["read-tree", "HEAD"], env).is_some_and(|output| output.status.success());
    let added = ready && git(checkout, &["add", "-A"], env).is_some_and(|output| output.status.success());
    let tree = if added { git(checkout, &["write-tree"], env).filter(|output| output.status.success()) } else { None };
    let _ = std::fs::remove_file(&index);
    tree.map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .ok_or_else(|| format!("could not read the changes in {}", checkout.display()))
}

fn apply_session(mut session: Session, into: &Path) -> (Vec<String>, i32) {
    let tree = match current_tree(&session.checkout) {
        Ok(tree) => tree,
        Err(err) => return (vec![err], 1),
    };
    let from = session.applied.clone().unwrap_or_else(|| session.base.clone());
    match apply_between(into, &from, &tree, &session.branch) {
        Ok(lines) => {
            session.applied = Some(tree);
            let mut lines = lines;
            if let Err(err) = session.write() {
                lines.push(format!("(Could not note what was applied in the session record: {err}.)"));
            }
            (lines, 0)
        }
        Err(lines) => (lines, 1),
    }
}

/// Applies the difference between two commits or trees of the same repository to the checkout
/// `into`, as uncommitted changes, all or nothing: refused, with the files that conflict, when it
/// does not apply cleanly. `branch` names where the changes are, for the refusal's advice.
pub fn apply_between(into: &Path, from: &str, to: &str, branch: &str) -> Result<Vec<String>, Vec<String>> {
    let into = git_text(into, &["rev-parse", "--show-toplevel"]).map_or_else(|| into.to_path_buf(), PathBuf::from);
    let diff = git(&into, &["diff", "--binary", "--full-index", from, to], None)
        .filter(|output| output.status.success())
        .ok_or_else(|| vec![format!("Could not read the changes on {branch}.")])?;
    if diff.stdout.is_empty() {
        return Ok(vec![format!("Nothing to apply: {branch} has no change that is not in {} already.", into.display())]);
    }
    let check = git_with_input(&into, &["apply", "--check", "--whitespace=nowarn", "-"], &diff.stdout);
    if !check.as_ref().is_some_and(|output| output.status.success()) {
        let why = check.map(|output| String::from_utf8_lossy(&output.stderr).into_owned()).unwrap_or_default();
        let mut lines = vec![format!("Not applied: the changes on {branch} conflict with {}. Nothing was changed.", into.display())];
        lines.extend(why.lines().filter(|line| !line.trim().is_empty()).take(12).map(|line| format!("  {}", line.trim())));
        lines.push(format!(
            "Commit or stash your own changes to those files and apply again, or merge the branch yourself: git merge {branch}"
        ));
        return Err(lines);
    }
    let applied = git_with_input(&into, &["apply", "--whitespace=nowarn", "-"], &diff.stdout);
    if !applied.as_ref().is_some_and(|output| output.status.success()) {
        let why = applied.map(|output| String::from_utf8_lossy(&output.stderr).trim().to_string()).unwrap_or_default();
        return Err(vec![format!("Not applied: git apply failed in {} ({why}).", into.display())]);
    }
    let stat = git_text(&into, &["diff", "--stat", from, to]).unwrap_or_default();
    let mut lines = vec![format!("Applied {branch} to {} as uncommitted changes: review them with git diff, then commit.", into.display())];
    lines.extend(stat.lines().map(|line| format!("  {}", line.trim())));
    Ok(lines)
}

/// Drops a session's changes, keeping a snapshot commit under `refs/mightling/snapshots/<id>`.
/// From inside the running session the worktree goes back to its base (the session's folder must
/// stay); otherwise the worktree, its branch and its folder are removed.
fn discard(session: &Session, in_session: bool) -> (Vec<String>, i32) {
    if !in_session && session.in_use_elsewhere() {
        return (vec![format!("Session {} is still running (pid {}): discard it from that session, with /worktree discard.", session.id, session.pid)], 1);
    }
    let mut lines = Vec::new();
    if session.checkout.is_dir() {
        match snapshot(session) {
            Ok(Some(reference)) => lines.push(format!("Its last state is kept as {reference} (git log -p {reference} to see it)." )),
            Ok(None) => {}
            Err(err) => return (vec![format!("Not discarded: {err}.")], 1),
        }
    }
    if in_session && session.checkout.is_dir() {
        let reset = git(&session.checkout, &["reset", "-q", "--hard", &session.base], None).is_some_and(|output| output.status.success());
        let cleaned = git(&session.checkout, &["clean", "-fdq"], None).is_some_and(|output| output.status.success());
        if !(reset && cleaned) {
            return (vec![format!("Could not reset {} to {}.", session.checkout.display(), short(&session.base))], 1);
        }
        lines.insert(0, format!("Discarded: {} is back at {}. Quit and start ling again; the empty worktree and branch {} are removed then.", session.checkout.display(), short(&session.base), session.branch));
    } else {
        remove(session);
        lines.insert(0, format!("Discarded session {}: its worktree and branch {} are removed.", session.id, session.branch));
    }
    (lines, 0)
}

fn snapshot(session: &Session) -> Result<Option<String>, String> {
    let tree = current_tree(&session.checkout)?;
    let base_tree = git_text(&session.checkout, &["rev-parse", &format!("{}^{{tree}}", session.base)]);
    if base_tree.as_deref() == Some(tree.as_str()) {
        return Ok(None);
    }
    let message = format!("ling worktree session {} (discarded)", session.id);
    let commit = ling_safegit::command(&session.checkout, &["commit-tree", &tree, "-p", &session.base, "-m", &message])
        .env("GIT_AUTHOR_NAME", "Mightling")
        .env("GIT_AUTHOR_EMAIL", "mightling@localhost")
        .env("GIT_COMMITTER_NAME", "Mightling")
        .env("GIT_COMMITTER_EMAIL", "mightling@localhost")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .ok_or("the snapshot commit could not be made")?;
    let reference = format!("{SNAPSHOTS}/{}", session.id);
    git(&session.checkout, &["update-ref", &reference, &commit], None)
        .filter(|output| output.status.success())
        .ok_or("the snapshot could not be kept")?;
    Ok(Some(reference))
}

fn short(sha: &str) -> &str {
    &sha[..sha.len().min(10)]
}

// -- git --------------------------------------------------------------------------------------

fn git(dir: &Path, args: &[&str], index: Option<&Path>) -> Option<Output> {
    let mut command = ling_safegit::command(dir, args);
    if let Some(index) = index {
        command.env("GIT_INDEX_FILE", index);
    }
    ling_safegit::run(command, std::time::Duration::from_secs(60)).ok()
}

fn git_text(dir: &Path, args: &[&str]) -> Option<String> {
    let mut all = vec!["--no-optional-locks"];
    all.extend_from_slice(args);
    git(dir, &all, None)
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim_end().to_string())
}

fn git_with_input(dir: &Path, args: &[&str], input: &[u8]) -> Option<Output> {
    use std::io::Write;
    let mut child = ling_safegit::command(dir, args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .ok()?;
    let mut stdin = child.stdin.take()?;
    let written = stdin.write_all(input);
    drop(stdin);
    let output = child.wait_with_output().ok()?;
    written.ok().map(|()| output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_paths_lose_windows_verbatim_prefix_for_git() {
        assert_eq!(plain(PathBuf::from(r"\\?\C:\Users\a\repo")), PathBuf::from(r"C:\Users\a\repo"));
        assert_eq!(plain(PathBuf::from(r"\\?\UNC\server\share")), PathBuf::from(r"\\?\UNC\server\share"));
        assert_eq!(plain(PathBuf::from("/home/a/repo")), PathBuf::from("/home/a/repo"));
    }

    struct Fixture {
        root: PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Fixture {
            let root = std::env::temp_dir().join(format!("ling-worktree-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("home")).unwrap_or_default();
            std::fs::create_dir_all(root.join("repo/sub")).unwrap_or_default();
            let repo = root.join("repo");
            for args in [vec!["init", "-q", "-b", "main"], vec!["config", "user.name", "t"], vec!["config", "user.email", "t@t"]] {
                git(&repo, &args, None);
            }
            std::fs::write(repo.join("auth.py"), "def login():\n    return False\n").unwrap_or_default();
            std::fs::write(repo.join("sub/keep.txt"), "keep\n").unwrap_or_default();
            git(&repo, &["add", "-A"], None);
            git(&repo, &["commit", "-qm", "init"], None);
            Fixture { root: canonical(&root) }
        }

        fn home(&self) -> PathBuf {
            self.root.join("home")
        }

        fn repo(&self) -> PathBuf {
            self.root.join("repo")
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|arg| arg.to_string()).collect()
    }

    #[test]
    fn the_default_is_in_place_and_the_setting_is_kept_in_desktop() {
        let fixture = Fixture::new("setting");
        let home = fixture.home();
        std::fs::write(home.join("config.toml"), "model = \"m\"\n\n[desktop]\ngit-worktree-root = \"/x\"\n").unwrap_or_default();
        assert_eq!(configured(&home), None);
        write_mode(&home, Mode::Worktree).unwrap_or_default();
        let text = std::fs::read_to_string(home.join("config.toml")).unwrap_or_default();
        assert!(text.contains("model = \"m\""), "{text}");
        assert!(text.contains("git-worktree-root = \"/x\""), "{text}");
        assert!(text.contains("mightling-worktree-mode = \"worktree\""), "{text}");
        assert_eq!(configured(&home), Some(Mode::Worktree));
        write_mode(&home, Mode::Local).unwrap_or_default();
        assert_eq!(configured(&home), Some(Mode::Local));
        assert_eq!(Mode::parse("on"), Some(Mode::Worktree));
        assert_eq!(Mode::parse("OFF"), Some(Mode::Local));
        assert_eq!(Mode::parse("maybe"), None);
    }

    #[test]
    fn a_local_session_is_left_where_it_is() {
        let fixture = Fixture::new("local");
        let user = args(&["hello"]);
        let prepared = prepare(vec!["ling".into(), "hello".into()], user.clone(), &fixture.home(), true, &fixture.repo());
        assert_eq!(prepared.args, vec![OsString::from("ling"), OsString::from("hello")]);
        assert_eq!(prepared.user_args, user);
        assert!(prepared.lines.is_empty());
        assert_eq!(prepared.cwd, None);
        assert!(sessions(&fixture.home()).is_empty());
    }

    #[test]
    fn a_session_worktree_is_made_applied_and_discarded() {
        let fixture = Fixture::new("cycle");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, dirty) = create(&home, &repo.join("sub")).unwrap_or_else(|err| panic!("{err}"));
        assert!(!dirty);
        assert!(cwd.ends_with("repo/sub"), "{}", cwd.display());
        assert!(session.branch.starts_with("ling/"));
        assert_eq!(git_text(&session.checkout, &["branch", "--show-current"]).as_deref(), Some(session.branch.as_str()));
        assert_eq!(session_at(&home, &cwd).map(|found| found.id), Some(session.id.clone()));
        assert_eq!(session_at(&home, &repo), None);

        // The agent edits and adds a file in the worktree; the user's checkout is untouched.
        std::fs::write(session.checkout.join("auth.py"), "def login():\n    return True\n").unwrap_or_default();
        std::fs::write(session.checkout.join("new.py"), "x = 1\n").unwrap_or_default();
        assert!(std::fs::read_to_string(repo.join("auth.py")).unwrap_or_default().contains("False"));
        let (lines, code) = command(&home, &cwd, "status", true);
        assert_eq!(code, 0);
        assert!(lines.iter().any(|line| line.contains("Not yet applied")), "{lines:?}");

        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(std::fs::read_to_string(repo.join("auth.py")).unwrap_or_default().contains("True"));
        assert!(repo.join("new.py").is_file());
        // A second apply brings only what is new: nothing.
        let (lines, _) = command(&home, &cwd, "apply", true);
        assert!(lines[0].starts_with("Nothing to apply"), "{lines:?}");

        // A later change in the worktree that conflicts with the user's own edit is refused whole.
        std::fs::write(session.checkout.join("auth.py"), "def login():\n    return 1\n").unwrap_or_default();
        std::fs::write(session.checkout.join("other.py"), "y = 2\n").unwrap_or_default();
        std::fs::write(repo.join("auth.py"), "def login():\n    return 'mine'\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 1);
        assert!(lines[0].starts_with("Not applied"), "{lines:?}");
        assert!(lines.iter().any(|line| line.contains("auth.py")), "{lines:?}");
        assert!(!repo.join("other.py").exists(), "all or nothing");

        // Discarding from inside the session resets to the base and keeps a snapshot.
        let (lines, code) = command(&home, &cwd, "discard", true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(lines.iter().any(|line| line.contains("refs/mightling/snapshots/")), "{lines:?}");
        assert!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default().contains("False"));
        assert!(!session.checkout.join("new.py").exists());
    }

    #[test]
    fn finished_clean_sessions_are_pruned_and_changed_ones_kept() {
        let fixture = Fixture::new("prune");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut clean, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        let (mut changed, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(changed.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        // Both sessions have ended: a pid that cannot be running.
        for session in [&mut clean, &mut changed] {
            session.pid = u32::MAX - 1;
            session.write().unwrap_or_default();
        }
        // A third whose changes were all applied holds nothing either.
        let (mut applied, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(applied.checkout.join("done.txt"), "applied\n").unwrap_or_default();
        applied.applied = current_tree(&applied.checkout).ok();
        applied.pid = u32::MAX - 1;
        applied.write().unwrap_or_default();
        assert_eq!(prune(&home, &repo), 1);
        assert!(!applied.home.exists());
        assert!(!clean.home.exists());
        assert!(git_text(&repo, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{}", clean.branch)]).is_none());
        assert!(changed.checkout.is_dir());
        let (lines, code) = command(&home, &repo, &format!("discard {}", changed.id), false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(!changed.home.exists());
    }

    #[test]
    fn worktree_mode_moves_a_new_session_and_rewrites_cd() {
        let fixture = Fixture::new("prepare");
        let (home, repo) = (fixture.home(), fixture.repo());
        write_mode(&home, Mode::Worktree).unwrap_or_default();
        let cd = repo.join("sub").to_string_lossy().into_owned();
        let user = args(&["-C", &cd, "fix it"]);
        let launch: Vec<OsString> = std::iter::once("ling".to_string()).chain(user.clone()).map(OsString::from).collect();
        let prepared = prepare(launch, user, &home, true, &repo.join("sub"));
        let lines = &prepared.lines;
        let session = sessions(&home).pop().unwrap_or_else(|| panic!("no session: {lines:?}"));
        let moved = session.checkout.join("sub");
        let text = moved.to_string_lossy().into_owned();
        assert_eq!(prepared.user_args, args(&["-C", &text, "fix it"]));
        assert_eq!(prepared.args[2], OsString::from(&text));
        assert_eq!(prepared.cwd, Some(moved));
        assert!(lines[0].starts_with("Worktree mode: this session works in"), "{lines:?}");
        // Codex's own --worktree, or not a new interactive session: nothing happens.
        let prepared = prepare(vec!["ling".into()], args(&["--worktree"]), &home, true, &repo);
        assert_eq!(prepared.cwd, None);
        let prepared = prepare(vec!["ling".into()], args(&["exec", "x"]), &home, false, &repo);
        assert!(prepared.lines.is_empty());
        assert_eq!(sessions(&home).len(), 1);
    }

    #[test]
    fn with_cd_rewrites_every_spelling() {
        let rewrite = |list: &[&str]| with_cd(args(list).into_iter(), |arg| Some(arg.clone()), |arg| arg, "/w");
        assert_eq!(rewrite(&["--cd", "x", "p"]), args(&["--cd", "/w", "p"]));
        assert_eq!(rewrite(&["--cd=x"]), args(&["--cd=/w"]));
        assert_eq!(rewrite(&["-Cx"]), args(&["-C/w"]));
        assert_eq!(rewrite(&["-m", "m", "hi"]), args(&["-m", "m", "hi"]));
    }

    #[test]
    fn the_status_line_names_the_branch_unless_the_user_chose_one() {
        let fixture = Fixture::new("status-line");
        let home = fixture.home();
        let out = with_status_line(vec!["ling".into()], &[], &home, true);
        assert_eq!(out[1], OsString::from("-c"));
        assert!(out[2].to_string_lossy().contains("\"current-dir\",\"git-branch\""));
        assert_eq!(with_status_line(vec!["ling".into()], &[], &home, false).len(), 1);
        assert_eq!(with_status_line(vec!["ling".into()], &args(&["-c", "tui.status_line=[]"]), &home, true).len(), 1);
        std::fs::write(home.join("config.toml"), "[tui]\nstatus_line = [\"model\"]\n").unwrap_or_default();
        assert_eq!(with_status_line(vec!["ling".into()], &[], &home, true).len(), 1);
    }

    #[test]
    fn enter_leaves_the_bare_command_to_codex() {
        assert_eq!(enter("/worktree"), None);
        assert_eq!(enter("  /worktree  "), None);
        assert_eq!(enter("/worktrees on"), None);
        assert_eq!(enter("fix /worktree on"), None);
    }

    #[test]
    fn a_new_session_is_not_nested_and_says_what_the_worktree_leaves_out() {
        let fixture = Fixture::new("nested");
        let (home, repo) = (fixture.home(), fixture.repo());
        write_mode(&home, Mode::Worktree).unwrap_or_default();
        // Uncommitted changes in the source are not carried over, and the notice says so.
        std::fs::write(repo.join("auth.py"), "def login():\n    return 'wip'\n").unwrap_or_default();
        let prepared = prepare(vec!["ling".into()], Vec::new(), &home, true, &repo);
        let lines = &prepared.lines;
        assert!(lines.iter().any(|line| line.starts_with("Your uncommitted changes in") && line.ends_with("are not in the worktree.")), "{lines:?}");
        let session = sessions(&home).pop().unwrap_or_else(|| panic!("no session: {lines:?}"));
        assert!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default().contains("False"));
        // A session started inside a session worktree works there.
        let prepared = prepare(vec!["ling".into()], Vec::new(), &home, true, &session.checkout);
        assert_eq!(prepared.cwd, None);
        assert!(prepared.lines[0].starts_with(&format!("Worktree mode: this folder is already session {}'s worktree", session.id)), "{:?}", prepared.lines);
        // Codex's --ignore-user-config leaves the session where it is.
        let prepared = prepare(vec!["ling".into()], args(&["--ignore-user-config"]), &home, true, &repo);
        assert_eq!(prepared.cwd, None);
        // Outside a git repository the session edits in place, and says why.
        let plain = fixture.root.join("plain");
        std::fs::create_dir_all(&plain).unwrap_or_default();
        let prepared = prepare(vec!["ling".into()], Vec::new(), &home, true, &plain);
        assert_eq!(prepared.cwd, None);
        assert!(prepared.lines[0].starts_with("Worktree mode is on, but") && prepared.lines[0].ends_with("in place."), "{:?}", prepared.lines);
        assert_eq!(sessions(&home).len(), 1);
    }

    #[test]
    fn ignored_files_are_left_out_of_apply() {
        let fixture = Fixture::new("ignored");
        let (home, repo) = (fixture.home(), fixture.repo());
        std::fs::write(repo.join(".gitignore"), "*.log\n").unwrap_or_default();
        git(&repo, &["add", "-A"], None);
        git(&repo, &["commit", "-qm", "ignore logs"], None);
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("run.log"), "noise\n").unwrap_or_default();
        std::fs::write(session.checkout.join("new.py"), "x = 1\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("new.py").is_file());
        assert!(!repo.join("run.log").exists(), "{lines:?}");
    }

    #[test]
    fn a_shell_lists_applies_by_id_and_hears_of_kept_sessions() {
        let fixture = Fixture::new("shell");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "def login():\n    return True\n").unwrap_or_default();
        session.pid = u32::MAX - 1;
        session.write().unwrap_or_default();
        // A new session in the repository is told of the finished one that holds changes.
        let prepared = prepare(vec!["ling".into()], Vec::new(), &home, true, &repo);
        assert_eq!(prepared.lines, vec!["1 earlier worktree session of this repository has changes not applied: ling worktree list.".to_string()]);
        assert_eq!(kept_line(2).as_deref(), Some("2 earlier worktree sessions of this repository have changes not applied: ling worktree list."));
        // `ling worktree list`: each session, whether it runs, and what it holds.
        let (lines, code) = command(&home, &repo, "list", false);
        assert_eq!(code, 0);
        assert!(lines[0].starts_with(&format!("{}  {}  finished, 1 file changed", session.id, session.branch)), "{lines:?}");
        assert_eq!(lines[1].trim(), session.checkout.display().to_string());
        // `ling worktree apply <id>` from the user's checkout brings it there.
        let (lines, code) = command(&home, &repo, &format!("apply {}", session.id), false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(lines[0].starts_with(&format!("Applied {} to", session.branch)), "{lines:?}");
        assert!(std::fs::read_to_string(repo.join("auth.py")).unwrap_or_default().contains("True"));
        // Status in place names the folder and branch, and leaves the footer the authority.
        let (lines, _) = command(&home, &repo, "status", true);
        assert_eq!(lines[0], "Worktree mode off: new sessions edit in place (the default).");
        // git names the folder with forward slashes on Windows too.
        assert_eq!(lines[1].replace('\\', "/"), format!("This session edits {} in place, on main.", repo.display()).replace('\\', "/"));
        assert!(lines[2].starts_with("The footer's folder is where edits land"), "{lines:?}");
    }

    #[test]
    fn on_and_off_save_the_setting_and_leave_the_running_session() {
        let fixture = Fixture::new("on-off");
        let home = fixture.home();
        let (lines, code) = command(&home, &fixture.repo(), "on", true);
        assert_eq!(code, 0);
        assert!(lines[0].starts_with("Worktree mode on (mightling-worktree-mode = \"worktree\""), "{lines:?}");
        assert!(lines[1].starts_with("This session stays where it is"), "{lines:?}");
        assert_eq!(configured(&home), Some(Mode::Worktree));
        let (lines, code) = command(&home, &fixture.repo(), "off", false);
        assert_eq!(code, 0);
        assert!(lines[0].starts_with("Worktree mode off"), "{lines:?}");
        assert!(!lines.iter().any(|line| line.starts_with("This session stays")), "{lines:?}");
        assert_eq!(configured(&home), Some(Mode::Local));
    }

    #[cfg(unix)]
    #[test]
    fn a_shell_does_not_discard_a_running_session() {
        let fixture = Fixture::new("running");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        // Another live process owns it: the test's parent.
        session.pid = std::os::unix::process::parent_id();
        session.write().unwrap_or_default();
        let (lines, code) = command(&home, &repo, &format!("discard {}", session.id), false);
        assert_eq!(code, 1);
        assert!(lines[0].contains("is still running"), "{lines:?}");
        assert!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default().contains("changed"));
        // Nor from a shell standing in its folder.
        let (lines, code) = command(&home, &session.checkout, "discard", false);
        assert_eq!(code, 1);
        assert!(lines[0].contains("is still running"), "{lines:?}");
        assert!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default().contains("changed"));
        assert_eq!(prune(&home, &repo), 0, "a running session is never pruned");
        assert!(session.checkout.is_dir());
    }

    #[test]
    fn a_setting_is_read_trimmed_and_an_empty_key_means_in_place() {
        let fixture = Fixture::new("parse");
        let home = fixture.home();
        assert_eq!(Mode::parse(" Worktree\n"), Some(Mode::Worktree));
        assert_eq!(Mode::parse(" off "), Some(Mode::Local));
        std::fs::write(home.join("config.toml"), "[desktop]\nmightling-worktree-mode = \"\"\n").unwrap_or_default();
        assert_eq!(configured(&home), Some(Mode::Local));
        assert_eq!(mode_now(&home).1, format!("{KEY} in {}", home.join("config.toml").display()));
    }

    /// The variable and `CODEX_HOME` come from the process environment, which the tests share, so
    /// the checks that set them run in a child process of their own.
    #[test]
    fn the_variable_wins_over_the_setting_for_one_session() {
        const CHILD: &str = "LING_WORKTREE_TEST_CHILD";
        if let Some(home) = std::env::var_os(CHILD) {
            let home = PathBuf::from(home);
            assert_eq!(mode_now(&home), (Mode::Worktree, ENV_VAR.to_string()));
            assert_eq!(enter("  /worktree help").as_deref(), Some(USAGE));
            return;
        }
        let fixture = Fixture::new("variable");
        let home = fixture.home();
        write_mode(&home, Mode::Local).unwrap_or_default();
        let exe = std::env::current_exe().unwrap_or_else(|err| panic!("{err}"));
        let output = std::process::Command::new(exe)
            .args(["--exact", "worktree::tests::the_variable_wins_over_the_setting_for_one_session", "--nocapture"])
            .env(CHILD, &home)
            .env("CODEX_HOME", &home)
            .env(ENV_VAR, " On ")
            .output()
            .unwrap_or_else(|err| panic!("{err}"));
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(output.status.success(), "{stdout}{}", String::from_utf8_lossy(&output.stderr));
        assert!(stdout.contains("1 passed"), "{stdout}");
    }

    #[test]
    fn the_setting_replaces_a_desktop_key_that_is_not_a_table_and_leaves_bad_toml_alone() {
        let fixture = Fixture::new("bad-config");
        let home = fixture.home();
        let path = home.join("config.toml");
        std::fs::write(&path, "model = \"m\"\ndesktop = 1\n").unwrap_or_default();
        write_mode(&home, Mode::Worktree).unwrap_or_else(|err| panic!("{err}"));
        assert_eq!(configured(&home), Some(Mode::Worktree));
        assert!(std::fs::read_to_string(&path).unwrap_or_default().contains("model = \"m\""));
        // A file that is not TOML is not overwritten, and the command says so.
        std::fs::write(&path, "model = [\n").unwrap_or_default();
        let (lines, code) = command(&home, &fixture.repo(), "on", false);
        assert_eq!(code, 1);
        assert!(lines[0].starts_with("Could not save the setting:") && lines[0].contains("is not valid TOML"), "{lines:?}");
        assert_eq!(std::fs::read_to_string(&path).unwrap_or_default(), "model = [\n");
    }

    #[cfg(unix)]
    #[test]
    fn a_record_without_a_pid_is_finished_and_a_process_of_another_user_is_running() {
        let fixture = Fixture::new("pid");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        // pid 1 always runs; a user who may not signal it hears EPERM, which still means running.
        session.pid = 1;
        session.write().unwrap_or_default();
        assert!(session.in_use_elsewhere());
        assert_eq!(prune(&home, &repo), 0);
        assert!(session.checkout.is_dir());
        // A record without a pid is of a finished session: a clean one is pruned.
        session.pid = 0;
        session.write().unwrap_or_default();
        assert!(!session.in_use_elsewhere());
        assert_eq!(prune(&home, &repo), 0);
        assert!(!session.home.exists());
    }

    #[test]
    fn a_session_is_found_from_a_path_that_is_not_canonical() {
        let fixture = Fixture::new("roundabout");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo.join("sub")).unwrap_or_else(|err| panic!("{err}"));
        let inside = cwd.strip_prefix(&fixture.root).unwrap_or_else(|err| panic!("{err}"));
        let roundabout = repo.join("..").join(inside);
        assert_eq!(session_at(&home, &roundabout).map(|found| found.id), Some(session.id));
    }

    #[test]
    fn worktree_mode_also_tells_of_kept_sessions() {
        let fixture = Fixture::new("kept");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut earlier, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(earlier.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        earlier.pid = u32::MAX - 1;
        earlier.write().unwrap_or_default();
        write_mode(&home, Mode::Worktree).unwrap_or_default();
        let prepared = prepare(vec!["ling".into()], Vec::new(), &home, true, &repo);
        assert!(prepared.cwd.is_some(), "{:?}", prepared.lines);
        assert_eq!(prepared.lines.last(), kept_line(1).as_ref(), "{:?}", prepared.lines);
    }

    #[test]
    fn with_cd_leaves_config_flags_alone() {
        let rewrite = |list: &[&str]| with_cd(args(list).into_iter(), |arg| Some(arg.clone()), |arg| arg, "/w");
        assert_eq!(rewrite(&["-c", "model=x", "-cmodel=y", "hi"]), args(&["-c", "model=x", "-cmodel=y", "hi"]));
    }

    #[test]
    fn a_folder_the_base_commit_lacks_starts_the_session_at_the_top() {
        let fixture = Fixture::new("untracked-dir");
        let (home, repo) = (fixture.home(), fixture.repo());
        std::fs::create_dir_all(repo.join("scratch")).unwrap_or_default();
        std::fs::write(repo.join("scratch/notes.txt"), "wip\n").unwrap_or_default();
        let (session, cwd, dirty) = create(&home, &repo.join("scratch")).unwrap_or_else(|err| panic!("{err}"));
        assert!(dirty);
        assert_eq!(cwd, session.checkout);
    }

    /// A second repository beside the fixture's, with one commit.
    fn other_repository(fixture: &Fixture, name: &str) -> PathBuf {
        let other = fixture.root.join(name);
        std::fs::create_dir_all(&other).unwrap_or_default();
        for args in [vec!["init", "-q", "-b", "main"], vec!["config", "user.name", "t"], vec!["config", "user.email", "t@t"]] {
            git(&other, &args, None);
        }
        std::fs::write(other.join("other.txt"), "other\n").unwrap_or_default();
        git(&other, &["add", "-A"], None);
        git(&other, &["commit", "-qm", "init"], None);
        other
    }

    #[test]
    fn prune_drops_a_session_whose_folder_is_gone_and_counts_only_this_repository() {
        let fixture = Fixture::new("gone");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut gone, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        gone.pid = u32::MAX - 1;
        gone.write().unwrap_or_default();
        std::fs::remove_dir_all(&gone.checkout).unwrap_or_default();
        // A finished session of another repository that holds changes.
        let other = other_repository(&fixture, "other");
        let (mut elsewhere, _, _) = create(&home, &other).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(elsewhere.checkout.join("other.txt"), "changed\n").unwrap_or_default();
        elsewhere.pid = u32::MAX - 1;
        elsewhere.write().unwrap_or_default();
        assert_eq!(prune(&home, &repo), 0);
        assert!(!gone.home.exists());
        assert!(git_text(&repo, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{}", gone.branch)]).is_none());
        assert!(elsewhere.checkout.is_dir());
        assert_eq!(prune(&home, &other), 1);
    }

    #[test]
    fn the_status_line_is_left_to_every_spelling_of_a_config_flag() {
        let fixture = Fixture::new("status-flags");
        let home = fixture.home();
        for user in [args(&["--config", "tui.status_line=[]"]), args(&["--config=tui.status_line=[]"]), args(&["-ctui.status_line=[]"])] {
            assert_eq!(with_status_line(vec!["ling".into()], &user, &home, true).len(), 1, "{user:?}");
        }
    }

    #[test]
    fn a_bare_command_is_status_and_apply_or_discard_in_place_fails() {
        let fixture = Fixture::new("in-place");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (lines, code) = command(&home, &repo, "", false);
        assert_eq!(code, 0);
        assert_eq!(lines[0], "Worktree mode off: new sessions edit in place (the default).");
        let (lines, code) = command(&home, &repo, "apply", false);
        assert_eq!(code, 1);
        assert!(lines[0].contains("is edited in place: there is nothing to apply"), "{lines:?}");
        let (lines, code) = command(&home, &repo, "discard", false);
        assert_eq!(code, 1);
        assert!(lines[0].contains("is edited in place: there is no worktree to discard"), "{lines:?}");
        let (lines, code) = command(&home, &repo, "apply nope", false);
        assert_eq!(code, 1);
        assert!(lines[0].starts_with("No worktree session nope"), "{lines:?}");
        let (lines, code) = command(&home, &repo, "frobnicate", false);
        assert_eq!(code, 2);
        assert_eq!(lines, vec!["Unknown: frobnicate".to_string(), CLI_USAGE.to_string()]);
    }

    #[test]
    fn apply_by_id_goes_to_the_checkout_of_the_repository_it_is_asked_from() {
        let fixture = Fixture::new("apply-into");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        let apply = format!("apply {}", session.id);
        // From another checkout of the same repository: there, not the source.
        let linked = fixture.root.join("linked");
        git(&repo, &["worktree", "add", "-q", "-b", "mine", &linked.to_string_lossy()], None);
        std::fs::write(session.checkout.join("one.py"), "1\n").unwrap_or_default();
        let (lines, code) = command(&home, &linked.join("sub"), &apply, false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(linked.join("one.py").is_file() && !repo.join("one.py").exists(), "{lines:?}");
        // From the session's own checkout: the source.
        std::fs::write(session.checkout.join("two.py"), "2\n").unwrap_or_default();
        let (lines, code) = command(&home, &session.checkout, &apply, false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("two.py").is_file(), "{lines:?}");
        // From another repository: the source.
        let other = other_repository(&fixture, "other");
        std::fs::write(session.checkout.join("three.py"), "3\n").unwrap_or_default();
        let (lines, code) = command(&home, &other, &apply, false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("three.py").is_file() && !other.join("three.py").exists(), "{lines:?}");
    }

    #[test]
    fn prune_keeps_a_session_that_git_cannot_read() {
        let fixture = Fixture::new("unreadable");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        session.pid = u32::MAX - 1;
        session.write().unwrap_or_default();
        // Its link to the repository is broken: what it holds cannot be read, so it is kept.
        std::fs::write(session.checkout.join(".git"), "gitdir: /nonexistent\n").unwrap_or_default();
        assert_eq!(prune(&home, &repo), 1);
        assert_eq!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default(), "changed\n");
    }

    #[test]
    fn a_session_is_named_by_a_prefix_of_its_id_or_its_branch_but_not_ambiguously() {
        let fixture = Fixture::new("names");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (first, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        let (second, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(first.checkout.join("one.py"), "1\n").unwrap_or_default();
        std::fs::write(second.checkout.join("two.py"), "2\n").unwrap_or_default();
        let shared = first.id.chars().zip(second.id.chars()).take_while(|(a, b)| a == b).count();
        let (lines, code) = command(&home, &repo, &format!("apply {}", &first.id[..shared]), false);
        assert_eq!(code, 1);
        assert!(lines[0].contains("names several worktree sessions"), "{lines:?}");
        assert!(!repo.join("one.py").exists() && !repo.join("two.py").exists());
        let (lines, code) = command(&home, &repo, &format!("apply {}", &first.id[..shared + 1]), false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("one.py").is_file());
        let (lines, code) = command(&home, &repo, &format!("apply {}", second.branch), false);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("two.py").is_file());
    }

    #[test]
    fn apply_leaves_the_worktree_index_alone_and_status_then_has_nothing_to_apply() {
        let fixture = Fixture::new("index");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("new.py"), "x = 1\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        // The worktree's own index was not touched: the new file is still untracked there.
        assert_eq!(git_text(&session.checkout, &["status", "--porcelain"]).as_deref(), Some("?? new.py"));
        let (lines, _) = command(&home, &cwd, "status", true);
        assert!(lines.iter().any(|line| line == "Nothing to apply."), "{lines:?}");
    }

    #[test]
    fn apply_keeps_what_the_user_committed_since_the_session_began() {
        let fixture = Fixture::new("user-commits");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "def login():\n    return True\n").unwrap_or_default();
        std::fs::write(repo.join("theirs.py"), "mine = 1\n").unwrap_or_default();
        git(&repo, &["add", "-A"], None);
        git(&repo, &["commit", "-qm", "the user's own work"], None);
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(repo.join("theirs.py").is_file(), "{lines:?}");
        assert_eq!(git_text(&repo, &["status", "--porcelain"]).as_deref(), Some(" M auth.py"));
    }

    #[test]
    fn apply_between_lands_at_the_top_from_a_subfolder_and_refuses_an_unknown_commit() {
        let fixture = Fixture::new("between");
        let repo = fixture.repo();
        let base = git_text(&repo, &["rev-parse", "HEAD"]).unwrap_or_default();
        git(&repo, &["checkout", "-q", "-b", "side"], None);
        std::fs::write(repo.join("auth.py"), "def login():\n    return True\n").unwrap_or_default();
        git(&repo, &["commit", "-qam", "side"], None);
        let side = git_text(&repo, &["rev-parse", "HEAD"]).unwrap_or_default();
        git(&repo, &["checkout", "-q", "main"], None);
        let lines = apply_between(&repo.join("sub"), &base, &side, "side").unwrap_or_else(|lines| panic!("{lines:?}"));
        assert!(lines[0].starts_with(&format!("Applied side to {} as", repo.display())), "{lines:?}");
        assert!(std::fs::read_to_string(repo.join("auth.py")).unwrap_or_default().contains("True"));
        assert_eq!(apply_between(&repo, "no-such-commit", &side, "side"), Err(vec!["Could not read the changes on side.".to_string()]));
    }

    #[test]
    fn a_refused_apply_keeps_the_users_file_and_advises_merging_the_branch() {
        let fixture = Fixture::new("refused");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("new.py"), "theirs\n").unwrap_or_default();
        std::fs::write(repo.join("new.py"), "mine\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 1);
        assert!(lines[0].starts_with(&format!("Not applied: the changes on {} conflict with", session.branch)), "{lines:?}");
        assert!(lines.iter().any(|line| line.contains("new.py")), "{lines:?}");
        assert!(lines.last().is_some_and(|line| line.ends_with(&format!("git merge {}", session.branch))), "{lines:?}");
        assert_eq!(std::fs::read_to_string(repo.join("new.py")).unwrap_or_default(), "mine\n");
    }

    #[test]
    fn binary_files_and_trailing_whitespace_are_applied_as_they_are() {
        let fixture = Fixture::new("bytes");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        let bytes: Vec<u8> = (0..=255).collect();
        std::fs::write(session.checkout.join("blob.bin"), &bytes).unwrap_or_default();
        std::fs::write(session.checkout.join("spaces.txt"), "trailing  \n\tmixed \t\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        assert_eq!(std::fs::read(repo.join("blob.bin")).unwrap_or_default(), bytes);
        assert_eq!(std::fs::read_to_string(repo.join("spaces.txt")).unwrap_or_default(), "trailing  \n\tmixed \t\n");
    }

    #[test]
    fn commits_in_the_worktree_are_applied_and_discarded_too() {
        let fixture = Fixture::new("agent-commits");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "def login():\n    return True\n").unwrap_or_default();
        git(&session.checkout, &["commit", "-qam", "the agent's commit"], None);
        std::fs::write(session.checkout.join("later.py"), "y = 2\n").unwrap_or_default();
        let (lines, code) = command(&home, &cwd, "apply", true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(std::fs::read_to_string(repo.join("auth.py")).unwrap_or_default().contains("True"));
        assert!(repo.join("later.py").is_file());
        let (lines, code) = command(&home, &cwd, "discard", true);
        assert_eq!(code, 0, "{lines:?}");
        assert_eq!(git_text(&session.checkout, &["rev-parse", "HEAD"]), Some(session.base.clone()));
        assert!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default().contains("False"));
        assert!(!session.checkout.join("later.py").exists());
    }

    #[test]
    fn a_clean_discard_keeps_no_snapshot() {
        let fixture = Fixture::new("clean-discard");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        let (lines, code) = command(&home, &cwd, "discard", true);
        assert_eq!(code, 0, "{lines:?}");
        assert_eq!(lines.len(), 1, "{lines:?}");
        assert_eq!(git_text(&repo, &["for-each-ref", SNAPSHOTS]).as_deref(), Some(""));
        assert!(session.checkout.is_dir());
    }

    #[test]
    fn the_list_leaves_out_the_sessions_of_another_repository() {
        let fixture = Fixture::new("list-other");
        let (home, repo) = (fixture.home(), fixture.repo());
        let other = other_repository(&fixture, "other");
        create(&home, &other).unwrap_or_else(|err| panic!("{err}"));
        assert_eq!(list(&home, &repo), vec!["No worktree sessions for this repository.".to_string()]);
        assert_eq!(list(&home, &other).len(), 3);
    }

    #[test]
    fn nothing_to_apply_is_not_a_failure() {
        let fixture = Fixture::new("nothing");
        let repo = fixture.repo();
        let head = git_text(&repo, &["rev-parse", "HEAD"]).unwrap_or_default();
        let lines = apply_between(&repo, &head, &head, "b").unwrap_or_else(|lines| panic!("{lines:?}"));
        assert!(lines[0].starts_with("Nothing to apply"), "{lines:?}");
    }

    #[cfg(unix)]
    #[test]
    fn discard_from_inside_the_session_goes_on_while_its_record_names_another_process() {
        let fixture = Fixture::new("discard-inside");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        // pid 1 always runs: from any other folder the session counts as running.
        session.pid = 1;
        assert_eq!(discard(&session, false).1, 1);
        let (lines, code) = discard(&session, true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(lines[0].starts_with("Discarded:"), "{lines:?}");
        assert_ne!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default(), "changed\n");
    }

    #[test]
    fn a_discard_that_cannot_keep_a_snapshot_removes_nothing() {
        let fixture = Fixture::new("no-snapshot");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (session, _, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        // Its link to the repository is broken: its changes cannot be read, so nothing is dropped.
        std::fs::write(session.checkout.join(".git"), "gitdir: /nonexistent\n").unwrap_or_default();
        let (lines, code) = discard(&session, false);
        assert_eq!(code, 1, "{lines:?}");
        assert!(lines[0].starts_with("Not discarded"), "{lines:?}");
        assert_eq!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default(), "changed\n");
    }

    #[test]
    fn discard_by_its_own_id_from_inside_the_session_keeps_its_folder() {
        let fixture = Fixture::new("discard-own-id");
        let (home, repo) = (fixture.home(), fixture.repo());
        let (mut session, cwd, _) = create(&home, &repo).unwrap_or_else(|err| panic!("{err}"));
        std::fs::write(session.checkout.join("auth.py"), "changed\n").unwrap_or_default();
        session.pid = std::process::id();
        session.write().unwrap_or_default();
        // Named by its id, the session this process runs in is still reset in place, as with a bare discard.
        let (lines, code) = command(&home, &cwd, &format!("discard {}", session.id), true);
        assert_eq!(code, 0, "{lines:?}");
        assert!(lines[0].starts_with("Discarded:"), "{lines:?}");
        assert!(session.checkout.is_dir());
        assert_ne!(std::fs::read_to_string(session.checkout.join("auth.py")).unwrap_or_default(), "changed\n");
    }
}
