//! Puffin and Mightling became Ragon (specs/DREAMFERENCE_RENAME_RAGON.md §4).
//!
//! A 1.4.x installation updates itself with `puffin update`, a 1.5 to 1.8 one with `ling update`;
//! each installs this binary under its own name in its own folder. The first time it runs, it moves
//! that installation to the new names once: the install folder and the binaries in it, the agent's
//! home (`~/.mightling` is moved whole and leaves a marker; `~/.puffin` is copied by an allow-list,
//! never the login), the configuration keys and values, the command links, the pairing key and the
//! desktop app's data. Every step checks before it acts, so a second run finds nothing to do and says
//! nothing. The old names are not kept as aliases: the old links are removed, not redirected.
//!
//! Std only, and called first thing in `main()` (from `home::use_ragon_home`), before any thread
//! exists.
//!
//! Only an installed binary migrates ([`allowed`]): one that runs from an install folder, an old one
//! (what `puffin update` or `ling update` replaced) or the new one (a release, or `ragon-admin codex
//! build`, which copies the binary it built there). A binary run from a Cargo target directory, a
//! scratch export or a copy elsewhere leaves the machine alone, because the migration moves the
//! install folder and the links of whatever installation the machine runs now. `RAGON_LEGACY_MIGRATION`
//! (`1` always, `0` never) is the switch; the old `MIGHTLING_LEGACY_MIGRATION=0` still blocks it.

use std::ffi::OsStr;
use std::io;
use std::path::Path;
use std::path::PathBuf;

/// The agent's home of each earlier name, newest first, relative to `$HOME`.
pub const LEGACY_HOME_DIRS: &[&str] = &[".mightling", ".puffin"];
/// The home of the Mightling generation: moved whole.
pub const MIGHTLING_HOME_DIR: &str = ".mightling";
/// Where release binaries lived under each earlier name, and where they live now, relative to `$HOME`.
pub const LEGACY_INSTALL_DIRS: &[&str] = &[
    ".local/share/dreamference/mightling",
    ".local/share/dreamference/puffin",
];
pub const INSTALL_DIR: &str = ".local/share/dreamference/ragon";
/// Written into the new home after a copy from `~/.puffin`, listing what came across.
pub const MARKER: &str = ".migrated-from-puffin";
/// Left in `~/.mightling` after the move, so nothing mistakes the empty place for a fresh start.
pub const MOVED_MARKER: &str = ".moved-to-ragon";
/// Added to the name of a `~/.ragon` found beside an unmoved `~/.mightling`, which is moved aside.
pub const PRE_MIGRATION_SUFFIX: &str = ".pre-migration";
/// `1` migrates whichever binary runs (to test the migration on purpose, in a scratch HOME), `0`
/// never migrates; unset, only an installed binary does. `ragon-admin` reads the same variable.
pub const OPT_IN_ENV: &str = "RAGON_LEGACY_MIGRATION";
/// The earlier name of the switch: honoured as `0` only.
pub const OLD_OPT_IN_ENV: &str = "MIGHTLING_LEGACY_MIGRATION";

/// Whether this process may migrate: [`OPT_IN_ENV`] when it is `1` or `0`, otherwise whether `exe`
/// is an installed binary ([`is_installed_binary`]). No executable path means no migration. The old
/// switch set to `0` blocks it when the new one is unset.
pub fn allowed(home: &Path, exe: Option<&Path>, choice: Option<&OsStr>) -> bool {
    match choice.and_then(OsStr::to_str).map(str::trim) {
        Some("1") => true,
        Some("0") => false,
        _ => {
            let old_off = std::env::var_os(OLD_OPT_IN_ENV)
                .and_then(|value| value.to_str().map(|text| text.trim() == "0"))
                .unwrap_or(false);
            !old_off && exe.is_some_and(|exe| is_installed_binary(home, exe))
        }
    }
}

/// Whether `exe` sits directly in the `bin` folder of an old or the new install folder under
/// `home`. Both sides are resolved first, so a link to the binary (`~/.local/bin/ragon`) counts as
/// the binary and a home reached through a symbolic link still matches.
pub fn is_installed_binary(home: &Path, exe: &Path) -> bool {
    let resolve = |path: &Path| path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let exe = resolve(exe);
    let Some(dir) = exe.parent() else {
        return false;
    };
    LEGACY_INSTALL_DIRS
        .iter()
        .chain(std::iter::once(&INSTALL_DIR))
        .any(|install| resolve(&home.join(install).join("bin")) == dir)
}

/// Binaries in the install folder, by their old and new names (both generations).
const BINARIES: &[(&str, &str)] = &[
    ("puffin", "ragon"),
    ("puffin-search", "ragon-search"),
    ("puffin-fetch", "ragon-fetch"),
    ("puffin-code", "ragon-code"),
    ("ling", "ragon"),
    ("ling-search", "ragon-search"),
    ("ling-fetch", "ragon-fetch"),
    ("ling-code", "ragon-code"),
    ("ling-docs", "ragon-docs"),
    ("ling-app", "ragon-app"),
    ("ling-delegate", "ragon-delegate"),
    ("ling-signal", "ragon-signal"),
];
/// Command links the old installations put in `~/.local/bin`, removed when they are links.
const LEGACY_LINKS: &[&str] = &[
    "puffin",
    "puffin-search",
    "puffin-fetch",
    "puffin-code",
    "puffin-app",
    "puffin-admin",
    "ling",
    "ling-search",
    "ling-fetch",
    "ling-code",
    "ling-docs",
    "ling-app",
    "ling-delegate",
    "ling-admin",
];
/// Entries of an old home carried into the new one by a copy: Codex's own state (the same
/// allow-list as the first-run copy from `~/.codex`, `home::should_carry_over`) plus the launcher's.
const CARRY_OVER: &[&str] = &[
    "session_index.jsonl",
    "airgapped",
    "audit",
    "night",
    "system-prompts",
    "node.json",
];
/// Files of an old home whose names carried the old product name.
const RENAMED_FILES: &[(&str, &str)] = &[
    ("puffin-code.toml", "ragon-code.toml"),
    ("puffin-skills.toml", "ragon-skills.toml"),
    ("ling-code.toml", "ragon-code.toml"),
    ("ling-skills.toml", "ragon-skills.toml"),
];
/// Each installed skill records where it came from in a file named after the product.
const ORIGIN_FILES: &[&str] = &[".puffin-origin.toml", ".mightling-origin.toml"];
const ORIGIN_FILE: &str = ".ragon-origin.toml";
/// Configuration keys took the product's name as a prefix.
const LEGACY_KEY_PREFIXES: &[&str] = &["puffin_", "mightling_"];
const KEY_PREFIX: &str = "ragon_";
/// Text rewritten inside the moved home's `config.toml` (paths into the old home and install folder,
/// and the MCP server tables of the code and file indexes).
const CONFIG_TEXT: &[(&str, &str)] = &[
    ("/.mightling/", "/.ragon/"),
    ("/dreamference/mightling/", "/dreamference/ragon/"),
    ("[mcp_servers.ling_code]", "[mcp_servers.ragon_code]"),
    ("[mcp_servers.ling_docs]", "[mcp_servers.ragon_docs]"),
    ("/ling-code\"", "/ragon-code\""),
    ("/ling-docs\"", "/ragon-docs\""),
];
/// Desktop app data folders of the earlier names, relative to `$HOME`, and the new one.
const APP_DATA: &[(&str, &str)] = &[
    (".local/share/dev.dreamference.puffin", ".local/share/dev.dreamference.ragon"),
    (".local/share/dev.dreamference.mightling", ".local/share/dev.dreamference.ragon"),
    (
        "Library/Application Support/dev.dreamference.mightling",
        "Library/Application Support/dev.dreamference.ragon",
    ),
    ("AppData/Local/dev.dreamference.mightling", "AppData/Local/dev.dreamference.ragon"),
];
/// On Windows the install folders of the earlier names and the new one, relative to the profile folder.
/// A running program cannot be moved there, so the folder is copied and the old one left (the
/// installer, run again, removes it).
const WINDOWS_PROGRAMS: &[(&str, &str)] = &[
    ("AppData/Local/Programs/Mightling", "AppData/Local/Programs/Ragon"),
    ("AppData/Local/Programs/Puffin", "AppData/Local/Programs/Ragon"),
];
/// Folders of the moved home that hold git worktrees, repaired after the move.
const WORKTREE_FOLDERS: &[&str] = &["worktrees", "chat-repos", "night", "session-worktrees"];
/// Records of the moved home that name worktrees by their absolute paths: the phone checkouts'
/// index and each `/worktree` session's record (`<id>/session.json`).
const CHAT_REPOS_INDEX: &str = "chat-repos/index.json";
const SESSION_WORKTREES: &str = "session-worktrees";
const SESSION_RECORD: &str = "session.json";
/// User units of a SWE-bench run, a Night Shift run or a node job: nothing moves under them
/// (specs/DREAMFERENCE_RENAME_RAGON.md §7). `ragon-admin` waits for the same units.
#[cfg(target_os = "linux")]
const BUSY_UNIT_PATTERNS: &[&str] = &[
    "ling-swe-*",
    "mightling-swe-*",
    "ragon-swe-*",
    "mightling-night*",
    "ragon-night*",
    "puffin-night*",
    "mightling-job-*",
    "ragon-job-*",
];
/// The lock a Night Shift run holds, under the old home.
#[cfg(target_os = "linux")]
const NIGHT_RUNNER_LOCK: &str = "night/runner.lock";
/// How long `systemctl` may take to answer before the units count as not running.
#[cfg(target_os = "linux")]
const SYSTEMCTL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

/// The user units' folder, relative to `$HOME`.
const USER_UNIT_DIR: &str = ".config/systemd/user";
/// The old user units of every subsystem (web, chat, sync, the monitor, the Matrix proxy, remote
/// access…), moved to their new names with their text rewritten, here and by `ragon-admin`, whichever
/// runs first (specs/DREAMFERENCE_RENAME_RAGON.md §2.3, A2). The Night Shift and forge units are left
/// to `ragon-admin`, whose steps keep the timer's window and the forge's state.
const LEGACY_UNIT_PREFIXES: &[&str] = &["mightling-", "puffin-"];
const LEGACY_SLICES: &[&str] = &["mightling.slice", "puffin.slice"];
const UNITS_WITH_OWN_STEPS: &[&str] = &["mightling-night", "puffin-night", "mightling-forge"];
const UNIT_SUFFIXES: &[&str] = &[".service", ".timer", ".socket", ".path", ".slice", ".target"];
/// The folders an old unit's program may sit in, and the new install folder's.
const LEGACY_UNIT_BIN_DIRS: &[&str] = &["/dreamference/mightling/bin/", "/dreamference/puffin/bin/"];
const UNIT_BIN_DIR: &str = "/dreamference/ragon/bin/";
const LINK_DIR_SUFFIX: &str = "/.local/bin/";
/// Programs an old unit may name besides [`BINARIES`]: the admin command and the 1.4 app.
const UNIT_PROGRAMS: &[(&str, &str)] = &[("puffin-app", "ragon-app"), ("puffin-admin", "ragon-admin"), ("ling-admin", "ragon-admin")];
const LEGACY_PRODUCT_WORDS: &[&str] = &["Mightling", "Puffin"];

/// What a migration did, one line per step; empty when there was nothing to do.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub lines: Vec<String>,
    /// An old `puffin-admin` or `ling-admin` link was removed and its virtualenv has no `ragon-admin` yet.
    pub admin_missing: bool,
}

impl Report {
    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// The notice printed once, after a migration that did something.
    pub fn notice(&self) -> String {
        let mut text = String::from("Mightling is now Ragon: run `ragon`.\n");
        for line in &self.lines {
            text.push_str("   ");
            text.push_str(line);
            text.push('\n');
        }
        if self.admin_missing {
            text.push_str(
                "   `ling-admin` is now `ragon-admin`: run the installer again to update it \
                 (curl -fsSL https://github.com/dreamference/ragon/releases/latest/download/install.sh | bash).\n",
            );
        }
        text
    }
}

/// Moves what the old installations left under `home` (and the configuration in `cwd`).
///
/// The `~/.puffin` home is copied by [`carry_over_home`], which `use_ragon_home` calls when it
/// creates the new home; `~/.mightling` is moved here, because a move is what keeps the sessions,
/// worktrees and queues whole.
///
/// While anything old is left and a SWE-bench run, a Night Shift run or a node job is active
/// ([`busy`]), nothing moves: the report says what the migration waits for and the next start tries
/// again. Once nothing old is left, `systemctl` is not asked at all (no process on every start).
pub fn migrate(home: &Path, cwd: &Path, config_path: Option<&Path>) -> Report {
    let mut report = Report::default();
    if anything_old(home) {
        let busy = busy(home);
        if !busy.is_empty() {
            report
                .lines
                .push(format!("the migration waits for {} to finish", busy.join(", ")));
            return report;
        }
    }
    // The old units stop before anything they run from moves, and start again under their new names
    // only once it has moved.
    let units = move_user_units(home);
    move_install_dir(home, &mut report);
    copy_windows_install(home, &mut report);
    relink(home, &mut report);
    move_home(home, &mut report);
    start_user_units(&units, &mut report);
    let mut configs = vec![
        home.join(".config/dreamference/config.toml"),
        cwd.join("dreamference.toml"),
    ];
    if let Some(path) = config_path {
        configs.push(path.to_path_buf());
    }
    configs.sort();
    configs.dedup();
    for path in configs {
        if let Ok(true) = rewrite_keys(&path) {
            report
                .lines
                .push(format!("renamed the puffin_* and mightling_* settings in {}", path.display()));
        }
    }
    for suffix in ["", ".pub"] {
        for old in ["puffin", "mightling"] {
            rename_if_free(
                &home.join(format!(".ssh/{old}-node_ed25519{suffix}")),
                &home.join(format!(".ssh/ragon-node_ed25519{suffix}")),
                &mut report,
            );
        }
    }
    for (old, new) in APP_DATA {
        rename_if_free(&home.join(old), &home.join(new), &mut report);
    }
    report
}

/// What must finish before anything moves: the running units of [`BUSY_UNIT_PATTERNS`] and a Night
/// Shift run holding its lock in `~/.mightling`. Linux only; any failure to ask counts as not busy.
pub fn busy(home: &Path) -> Vec<String> {
    #[cfg(target_os = "linux")]
    {
        let mut found = busy_units();
        if night_runner_holds(&home.join(MIGHTLING_HOME_DIR).join(NIGHT_RUNNER_LOCK)) {
            found.push("the Night Shift run".to_string());
        }
        found
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = home;
        Vec::new()
    }
}

/// The running user units that match [`BUSY_UNIT_PATTERNS`]. Never asked under `cargo test`, where
/// the answer would be the developer's machine's.
#[cfg(all(target_os = "linux", not(test)))]
fn busy_units() -> Vec<String> {
    use std::io::Read;
    use std::process::Command;
    use std::process::Stdio;
    let Ok(mut child) = Command::new("systemctl")
        .args(["--user", "list-units", "--state=running", "--no-legend", "--plain"])
        .args(BUSY_UNIT_PATTERNS)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return Vec::new();
    };
    let started = std::time::Instant::now();
    let succeeded = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.success(),
            Ok(None) if started.elapsed() < SYSTEMCTL_TIMEOUT => {
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break false;
            }
        }
    };
    let mut text = String::new();
    if !succeeded || child.stdout.take().is_none_or(|mut out| out.read_to_string(&mut text).is_err()) {
        return Vec::new();
    }
    text.lines()
        .filter_map(|line| line.split_whitespace().next())
        .map(str::to_string)
        .collect()
}

#[cfg(all(target_os = "linux", test))]
fn busy_units() -> Vec<String> {
    let _ = (SYSTEMCTL_TIMEOUT, BUSY_UNIT_PATTERNS);
    Vec::new()
}

/// Whether another process holds the Night Shift runner's lock (`flock`, as the runner takes it).
/// A missing file, or one that cannot be opened, is not held.
#[cfg(target_os = "linux")]
fn night_runner_holds(lock: &Path) -> bool {
    let Ok(file) = std::fs::File::open(lock) else {
        return false;
    };
    matches!(file.try_lock_shared(), Err(std::fs::TryLockError::WouldBlock))
}

/// Whether anything of an old installation is left to move: an old install folder, an old command
/// link, an unmoved `~/.mightling` or an old user unit.
pub fn anything_old(home: &Path) -> bool {
    LEGACY_INSTALL_DIRS.iter().any(|dir| home.join(dir).is_dir())
        || LEGACY_LINKS.iter().any(|name| {
            std::fs::symlink_metadata(home.join(".local/bin").join(name))
                .is_ok_and(|metadata| metadata.file_type().is_symlink())
        })
        || unmoved_home(home).is_some()
        || !old_user_units(home).is_empty()
}

/// The new name of an old user unit. `own_steps` renames the Night Shift and forge units too, as where
/// another unit names them; the generic step leaves those files to `ragon-admin`.
pub fn new_unit_name(name: &str, own_steps: bool) -> Option<String> {
    if !UNIT_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
        || (!own_steps && UNITS_WITH_OWN_STEPS.iter().any(|prefix| name.starts_with(prefix)))
    {
        return None;
    }
    if LEGACY_SLICES.contains(&name) {
        return Some("ragon.slice".to_string());
    }
    LEGACY_UNIT_PREFIXES
        .iter()
        .find_map(|prefix| name.strip_prefix(prefix).filter(|rest| !rest.is_empty()))
        .map(|rest| format!("ragon-{rest}"))
}

/// The new path of a program an old unit runs: in the new install folder, or the new link.
fn new_program_path(word: &str) -> Option<String> {
    let slash = word.rfind('/')?;
    let (folder, program) = (&word[..=slash], &word[slash + 1..]);
    let renamed = BINARIES
        .iter()
        .chain(UNIT_PROGRAMS)
        .find(|(old, _)| *old == program)
        .map(|(_, new)| *new);
    if let Some(legacy) = LEGACY_UNIT_BIN_DIRS.iter().find(|legacy| folder.ends_with(*legacy)) {
        let base = &folder[..folder.len() - legacy.len()];
        return Some(format!("{base}{UNIT_BIN_DIR}{}", renamed.unwrap_or(program)));
    }
    (folder.ends_with(LINK_DIR_SUFFIX) && renamed.is_some()).then(|| format!("{folder}{}", renamed.unwrap_or(program)))
}

/// Renames one word of a unit file (a unit's name, a program's path, a path into the old home, an
/// old variable's name or the product's name); a word with no old name in it is kept.
fn rewrite_unit_word(word: &str) -> String {
    if LEGACY_PRODUCT_WORDS.contains(&word) {
        return "Ragon".to_string();
    }
    if let Some(rest) = word.strip_prefix("MIGHTLING_") {
        return format!("RAGON_{rest}");
    }
    if let Some(renamed) = new_unit_name(word, true).or_else(|| new_program_path(word)) {
        return renamed;
    }
    if let Some(base) = word.strip_suffix("/.mightling") {
        return format!("{}/.ragon", base.replace("/.mightling/", "/.ragon/"));
    }
    word.replace("/.mightling/", "/.ragon/")
}

/// Rewrites an old unit file's text for its new name, word by word (`ragon-admin` does the same).
/// Only names the migration moves change: a name it keeps (the relay box's tunnel account in an
/// `ssh` command, an overlay peer's DNS name) does not end like a unit and is left as it was.
pub fn rewrite_unit_text(text: &str) -> String {
    text.split('\n')
        .map(|line| {
            line.split(' ')
                .map(|word| word.split('=').map(rewrite_unit_word).collect::<Vec<_>>().join("="))
                .collect::<Vec<_>>()
                .join(" ")
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// The old user unit files the generic step moves: (old name, new name), sorted.
fn old_user_units(home: &Path) -> Vec<(String, String)> {
    let Ok(entries) = std::fs::read_dir(home.join(USER_UNIT_DIR)) else {
        return Vec::new();
    };
    let mut units: Vec<(String, String)> = entries
        .flatten()
        .filter(|entry| entry.path().is_file())
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            new_unit_name(&name, false).map(|new| (name, new))
        })
        .collect();
    units.sort();
    units
}

/// The old units [`move_user_units`] moved, and the new names of those that ran.
#[derive(Debug, Default)]
struct MovedUnits {
    moved: Vec<String>,
    start: Vec<String>,
}

/// Stops the old user units and moves them to their new names, before anything they run from
/// moves. Each unit file is written again under its new name with its text rewritten, and the links
/// `systemctl enable` made follow it, so an enabled unit stays enabled. A unit already written under
/// its new name is kept as it is.
fn move_user_units(home: &Path) -> MovedUnits {
    let mut result = MovedUnits::default();
    let units = old_user_units(home);
    if units.is_empty() {
        return result;
    }
    let dir = home.join(USER_UNIT_DIR);
    let running: Vec<&str> = units
        .iter()
        .map(|(old, _)| old.as_str())
        .filter(|old| user_systemctl(&["is-active", "--quiet", old]))
        .collect();
    if !running.is_empty() {
        let mut stop = vec!["stop"];
        stop.extend(&running);
        user_systemctl(&stop);
    }
    for (old, new) in &units {
        if std::fs::symlink_metadata(dir.join(new)).is_err() {
            let Ok(text) = std::fs::read_to_string(dir.join(old)) else {
                continue;
            };
            if std::fs::write(dir.join(new), rewrite_unit_text(&text)).is_err() {
                continue;
            }
        }
        if std::fs::remove_file(dir.join(old)).is_err() {
            continue;
        }
        move_enable_links(&dir, old, new);
        result.moved.push(old.clone());
        if running.contains(&old.as_str()) {
            result.start.push(new.clone());
        }
    }
    user_systemctl(&["daemon-reload"]);
    result
}

/// Moves the links `systemctl enable` made for an old unit (`*.wants/`, `*.requires/`) to the new one.
fn move_enable_links(dir: &Path, old: &str, new: &str) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for folder in entries.flatten().map(|entry| entry.path()) {
        let enabling = folder.extension().is_some_and(|ext| ext == "wants" || ext == "requires");
        let link = folder.join(old);
        if !enabling || !std::fs::symlink_metadata(&link).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            continue;
        }
        let _ = std::fs::remove_file(&link);
        if std::fs::symlink_metadata(folder.join(new)).is_err() {
            let _ = make_symlink(dir.join(new), folder.join(new));
        }
    }
}

/// Starts again, under their new names, the old units that ran, and says what moved.
fn start_user_units(units: &MovedUnits, report: &mut Report) {
    if units.moved.is_empty() {
        return;
    }
    let mut line = format!("moved the user units {} to their new names", units.moved.join(", "));
    if !units.start.is_empty() {
        let mut start = vec!["start"];
        start.extend(units.start.iter().map(String::as_str));
        user_systemctl(&start);
        line.push_str(&format!("; started {} again", units.start.join(", ")));
    }
    report.lines.push(line);
}

/// Runs `systemctl --user` with `args`; true when it succeeded. Any failure (no user manager, a
/// timeout) is false.
#[cfg(all(target_os = "linux", not(test)))]
fn user_systemctl(args: &[&str]) -> bool {
    use std::process::Command;
    use std::process::Stdio;
    let Ok(mut child) = Command::new("systemctl")
        .arg("--user")
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return false;
    };
    let started = std::time::Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if started.elapsed() < UNIT_SYSTEMCTL_TIMEOUT => {
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// How long one `systemctl --user` call on a unit may take (a stop waits for the unit to end).
#[cfg(all(target_os = "linux", not(test)))]
const UNIT_SYSTEMCTL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

#[cfg(all(not(target_os = "linux"), not(test)))]
fn user_systemctl(_args: &[&str]) -> bool {
    false
}

#[cfg(test)]
thread_local! {
    /// Under `cargo test`: every `systemctl --user` call, and the units `is-active` answers yes for.
    static SYSTEMCTL_CALLS: std::cell::RefCell<Vec<String>> = const { std::cell::RefCell::new(Vec::new()) };
    static ACTIVE_UNITS: std::cell::RefCell<Vec<String>> = const { std::cell::RefCell::new(Vec::new()) };
}

#[cfg(test)]
fn user_systemctl(args: &[&str]) -> bool {
    SYSTEMCTL_CALLS.with(|calls| calls.borrow_mut().push(args.join(" ")));
    match args {
        ["is-active", "--quiet", unit] => ACTIVE_UNITS.with(|active| active.borrow().iter().any(|name| name == unit)),
        _ => true,
    }
}

fn rename_if_free(from: &Path, to: &Path, report: &mut Report) {
    if std::fs::symlink_metadata(from).is_ok() && std::fs::symlink_metadata(to).is_err() {
        if let Some(parent) = to.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if std::fs::rename(from, to).is_ok() {
            report
                .lines
                .push(format!("moved {} to {}", from.display(), to.display()));
        }
    }
}

fn move_install_dir(home: &Path, report: &mut Report) {
    let current = home.join(INSTALL_DIR);
    for legacy_dir in LEGACY_INSTALL_DIRS {
        let legacy = home.join(legacy_dir);
        if legacy.is_dir() && std::fs::symlink_metadata(&current).is_err() {
            if let Some(parent) = current.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if std::fs::rename(&legacy, &current).is_ok() {
                report.lines.push(format!(
                    "moved {} to {}",
                    legacy.display(),
                    current.display()
                ));
            }
        }
    }
    let bin = current.join("bin");
    for (old, new) in BINARIES {
        let from = bin.join(old);
        let to = bin.join(new);
        if from.is_file() && std::fs::symlink_metadata(&to).is_err() && std::fs::rename(&from, &to).is_ok() {
            report.lines.push(format!("renamed {old} to {new}"));
        }
    }
}

/// The Windows install folder (`Programs\Mightling`) copied to `Programs\Ragon`, its programs under
/// their new names (`ling.exe` becomes `ragon.exe`). Nothing happens where the old folder does not exist,
/// which is everywhere but a Windows machine that had Mightling or Puffin.
fn copy_windows_install(home: &Path, report: &mut Report) {
    for (old, new) in WINDOWS_PROGRAMS {
        let (legacy, current) = (home.join(old), home.join(new));
        if !legacy.is_dir() || std::fs::symlink_metadata(&current).is_ok() {
            continue;
        }
        if copy_tree(&legacy, &current).is_err() {
            let _ = std::fs::remove_dir_all(&current);
            continue;
        }
        let bin = current.join("bin");
        for (from_name, to_name) in BINARIES {
            let (from, to) = (bin.join(format!("{from_name}.exe")), bin.join(format!("{to_name}.exe")));
            if from.is_file() && std::fs::symlink_metadata(&to).is_err() {
                let _ = std::fs::rename(&from, &to);
            }
        }
        report.lines.push(format!(
            "copied {} to {}; run the installer again to put the new folder on PATH and remove the old one",
            legacy.display(),
            current.display()
        ));
    }
}

/// Removes the old command links and links the new names. A new link is made wherever it is missing
/// and its binary is in the install folder, whether or not this run removed the old one: `ragon-admin`
/// may have run first and removed the old links already. `ragon-signal` never gets one (its commands
/// are `ragon signal …`). Says something only when it removed or linked something.
fn relink(home: &Path, report: &mut Report) {
    let links = home.join(".local/bin");
    let bin = home.join(INSTALL_DIR).join("bin");
    let mut removed = Vec::new();
    for name in LEGACY_LINKS {
        let path = links.join(name);
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        // A real file of that name belongs to something else.
        if !metadata.file_type().is_symlink() {
            continue;
        }
        let target = std::fs::read_link(&path).ok();
        if std::fs::remove_file(&path).is_ok() {
            removed.push(*name);
            if name.ends_with("-admin") {
                let admin = target
                    .as_deref()
                    .and_then(Path::parent)
                    .map(|venv_bin| venv_bin.join("ragon-admin"));
                match admin {
                    Some(admin) if admin.is_file() => {
                        let _ = link(&admin, &links.join("ragon-admin"));
                    }
                    _ => report.admin_missing = true,
                }
            }
        }
    }
    let mut linked: Vec<&str> = Vec::new();
    for new in linked_names() {
        let target = bin.join(new);
        let path = links.join(new);
        if target.is_file() && std::fs::symlink_metadata(&path).is_err() && link(&target, &path).is_ok() {
            linked.push(new);
        }
    }
    let mut parts = Vec::new();
    if !removed.is_empty() {
        parts.push(format!("removed the links {}", removed.join(", ")));
    }
    if !linked.is_empty() {
        parts.push(format!("linked {}", linked.join(", ")));
    }
    if !parts.is_empty() {
        report.lines.push(format!("{} in {}", parts.join("; "), links.display()));
    }
}

/// The new names that get a command link: those of the binaries whose old name had one.
fn linked_names() -> Vec<&'static str> {
    let mut names: Vec<&'static str> = Vec::new();
    for (old, new) in BINARIES {
        if LEGACY_LINKS.contains(old) && !names.contains(new) {
            names.push(new);
        }
    }
    names
}

/// Links `link_path` to `target`, unless a real file already has that name.
fn link(target: &Path, link_path: &Path) -> io::Result<()> {
    match std::fs::symlink_metadata(link_path) {
        Ok(metadata) if !metadata.file_type().is_symlink() => return Ok(()),
        Ok(_) => std::fs::remove_file(link_path)?,
        Err(_) => {}
    }
    if let Some(parent) = link_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    make_symlink(target, link_path)
}

/// Moves `~/.mightling` to the new home whole, leaves a marker folder at the old path, and then
/// fixes what the move leaves wrong inside: paths and server tables in `config.toml`, files named
/// after the product, skills' origin records and the worktrees' links to their repositories.
///
/// A new home that already exists beside an unmoved `~/.mightling` was never the user's home (while
/// `~/.mightling` waits to move, every session uses it: [`unmoved_home`]), so it is moved aside to
/// `~/.ragon.pre-migration` and kept, not merged and not deleted.
fn move_home(home: &Path, report: &mut Report) {
    let old = home.join(MIGHTLING_HOME_DIR);
    let new = home.join(crate::home::RAGON_HOME_DIR);
    if !old.is_dir() || old.join(MOVED_MARKER).exists() {
        return;
    }
    let mut aside = None;
    if std::fs::symlink_metadata(&new).is_ok() {
        let spare = home.join(format!("{}{PRE_MIGRATION_SUFFIX}", crate::home::RAGON_HOME_DIR));
        if std::fs::symlink_metadata(&spare).is_ok() {
            report.lines.push(format!(
                "could not move {} to {}: both {} and {} exist; move one of them away",
                old.display(),
                new.display(),
                new.display(),
                spare.display()
            ));
            return;
        }
        if let Err(error) = std::fs::rename(&new, &spare) {
            report.lines.push(format!(
                "could not move {} aside to {}: {error}",
                new.display(),
                spare.display()
            ));
            return;
        }
        aside = Some(spare);
    }
    if let Err(error) = std::fs::rename(&old, &new) {
        if let Some(spare) = &aside {
            let _ = std::fs::rename(spare, &new);
        }
        report.lines.push(format!(
            "could not move {} to {}: {error}",
            old.display(),
            new.display()
        ));
        return;
    }
    if let Some(spare) = &aside {
        report.lines.push(format!(
            "moved {} (created before the move) aside to {}",
            new.display(),
            spare.display()
        ));
    }
    if std::fs::create_dir_all(&old).is_ok() {
        let _ = std::fs::write(
            old.join(MOVED_MARKER),
            format!("moved to {}\n", new.display()),
        );
    }
    report
        .lines
        .push(format!("moved {} to {}", old.display(), new.display()));
    fix_home(home, &new, report);
}

/// Rewrites the inside of a home that came from `~/.mightling`.
fn fix_home(home: &Path, new: &Path, report: &mut Report) {
    rename_files(new);
    rename_origin_files(&new.join("skills"));
    rewrite_config(&new.join("config.toml"));
    let moves = home_moves(home);
    rewrite_recorded_paths(&new.join(CHAT_REPOS_INDEX), &moves);
    if let Ok(entries) = std::fs::read_dir(new.join(SESSION_WORKTREES)) {
        for entry in entries.flatten() {
            rewrite_recorded_paths(&entry.path().join(SESSION_RECORD), &moves);
        }
    }
    for folder in WORKTREE_FOLDERS {
        repair_worktrees(&new.join(folder), &moves, 0, report);
    }
}

/// The old home's path and the new one's, as given and as the file system resolves them (git
/// records the resolved path), the longer first.
fn home_moves(home: &Path) -> Vec<(String, String)> {
    let mut bases = vec![home.to_path_buf()];
    if let Ok(real) = std::fs::canonicalize(home)
        && real != home
    {
        bases.push(real);
    }
    let mut moves: Vec<(String, String)> = bases
        .iter()
        .map(|base| {
            (
                path_text(&base.join(MIGHTLING_HOME_DIR)),
                path_text(&base.join(crate::home::RAGON_HOME_DIR)),
            )
        })
        .collect();
    moves.sort_by_key(|(old, _)| std::cmp::Reverse(old.len()));
    moves
}

/// A path as git and the records write it: with `/` separators on Windows too.
fn path_text(path: &Path) -> String {
    let text = path.to_string_lossy().into_owned();
    if cfg!(windows) {
        text.replace('\\', "/")
    } else {
        text
    }
}

/// The same place in the new home when `target` is an absolute path inside the old one; `None`
/// for a relative path (it moved with the home) or a path elsewhere.
fn moved_path(target: &str, moves: &[(String, String)]) -> Option<String> {
    let unified = if cfg!(windows) { target.replace('\\', "/") } else { target.to_string() };
    moves.iter().find_map(|(old, new)| {
        let rest = unified.strip_prefix(old.as_str())?;
        (rest.is_empty() || rest.starts_with('/')).then(|| format!("{new}{rest}"))
    })
}

/// Rewrites the old home's path to the new one's in a JSON record of absolute paths. Best effort.
fn rewrite_recorded_paths(record: &Path, moves: &[(String, String)]) {
    let Ok(text) = std::fs::read_to_string(record) else {
        return;
    };
    let mut rewritten = text.clone();
    for (old, new) in moves {
        rewritten = rewritten.replace(&format!("{old}/"), &format!("{new}/"));
        rewritten = rewritten.replace(&format!("{old}\""), &format!("{new}\""));
    }
    if rewritten != text {
        let _ = std::fs::write(record, rewritten);
    }
}

/// Points one link between a worktree and its repository at the new home: a worktree's `.git`
/// file (`gitdir: <path>`) or the repository's `worktrees/<name>/gitdir` (`<path>`). Only a path
/// into the old home whose new place exists is rewritten. Returns the file's target afterwards.
fn repoint_link(file: &Path, moves: &[(String, String)]) -> Option<(PathBuf, bool)> {
    let text = std::fs::read_to_string(file).ok()?;
    let (lead, target) = match text.strip_prefix("gitdir:") {
        Some(rest) => ("gitdir: ", rest.trim()),
        None => ("", text.trim()),
    };
    let target_path = Path::new(target);
    if !target_path.is_absolute() {
        let parent = file.parent()?;
        return Some((parent.join(target_path), false));
    }
    match moved_path(target, moves) {
        Some(moved) if !target_path.exists() && Path::new(&moved).exists() => {
            let changed = std::fs::write(file, format!("{lead}{moved}\n")).is_ok();
            Some((PathBuf::from(moved), changed))
        }
        _ => Some((target_path.to_path_buf(), false)),
    }
}

/// Gives the files named after the product their new names.
fn rename_files(dir: &Path) {
    for (old, new) in RENAMED_FILES {
        let from = dir.join(old);
        let to = dir.join(new);
        if from.is_file() && std::fs::symlink_metadata(&to).is_err() {
            let _ = std::fs::rename(from, to);
        }
    }
}

fn rewrite_config(config: &Path) {
    let Ok(text) = std::fs::read_to_string(config) else {
        return;
    };
    let mut rewritten = text.clone();
    for (old, new) in CONFIG_TEXT {
        rewritten = rewritten.replace(old, new);
    }
    if rewritten != text {
        let _ = std::fs::write(config, rewritten);
    }
}

/// Repairs every worktree under `dir` (a folder with a `.git` file), at most three levels down.
///
/// The links between the worktree and its repository that point into the old home are rewritten
/// first: when the repository moved with the home too (a phone checkout's `base.git`), git cannot
/// find it to repair anything. Then `git worktree repair` fixes what is left. Best effort: a
/// missing git or a broken repository is skipped.
fn repair_worktrees(dir: &Path, moves: &[(String, String)], depth: usize, report: &mut Report) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if !metadata.is_dir() {
            continue;
        }
        if path.join(".git").is_file() {
            let mut relinked = false;
            if let Some((gitdir, changed)) = repoint_link(&path.join(".git"), moves) {
                relinked = changed;
                if let Some((_, changed)) = repoint_link(&gitdir.join("gitdir"), moves) {
                    relinked |= changed;
                }
            }
            let repaired = std::process::Command::new("git")
                .arg("-C")
                .arg(&path)
                .args(["worktree", "repair"])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .is_ok_and(|status| status.success());
            if relinked || repaired {
                report.lines.push(format!("repaired the worktree {}", path.display()));
            }
        } else if depth < 3 {
            repair_worktrees(&path, moves, depth + 1, report);
        }
    }
}

/// Rewrites `puffin_<key> =` and `mightling_<key> =` lines to `ragon_<key> =` (whole-line key
/// matches only), and the value `server_engine = "ling"` to `"ragon"`.
///
/// Returns whether the file changed. A missing file is not an error.
pub fn rewrite_keys(path: &Path) -> io::Result<bool> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    let mut changed = false;
    let rewritten: Vec<String> = text
        .split_inclusive('\n')
        .map(|line| {
            let indent = line.len() - line.trim_start().len();
            let rest = &line[indent..];
            let prefix = LEGACY_KEY_PREFIXES.iter().find(|prefix| {
                rest.starts_with(**prefix)
                    && rest[prefix.len()..]
                        .split(|c: char| c == '=' || c.is_whitespace())
                        .next()
                        .is_some_and(|key| !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
                    && rest.contains('=')
            });
            if let Some(prefix) = prefix {
                changed = true;
                return format!("{}{KEY_PREFIX}{}", &line[..indent], &rest[prefix.len()..]);
            }
            let compact: String = rest.chars().filter(|c| !c.is_whitespace()).collect();
            if compact == "server_engine=\"ling\"" {
                changed = true;
                return line.replace("\"ling\"", "\"ragon\"");
            }
            line.to_string()
        })
        .collect();
    if changed {
        std::fs::write(path, rewritten.concat())?;
    }
    Ok(changed)
}

/// Fills a new, empty home from an old one by a copy: Codex's state (`home::should_carry_over`)
/// and the launcher's (`CARRY_OVER`), with the renamed files under their new names. `auth.json`,
/// `installation_id`, the logs and anything not named stay behind, and the old home is left as it
/// was, so the user can go back to it by hand. Paths into the old home in the copied `config.toml`
/// are rewritten.
pub fn carry_over_home(legacy: &Path, home: &Path) -> io::Result<usize> {
    let mut carried = Vec::new();
    for entry in std::fs::read_dir(legacy)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let destination = RENAMED_FILES
            .iter()
            .find(|(old, _)| *old == name)
            .map(|(_, new)| (*new).to_string())
            .or_else(|| {
                (crate::home::should_carry_over(&name) || CARRY_OVER.contains(&name.as_str()))
                    .then(|| name.clone())
            });
        let Some(destination) = destination else {
            continue;
        };
        if copy_tree(&entry.path(), &home.join(&destination)).is_ok() {
            carried.push(destination);
        }
    }
    rename_origin_files(&home.join("skills"));
    let config = home.join("config.toml");
    if let Ok(text) = std::fs::read_to_string(&config) {
        let mut rewritten = text.clone();
        for dir in LEGACY_HOME_DIRS {
            rewritten = rewritten.replace(&format!("/{dir}/"), &format!("/{}/", crate::home::RAGON_HOME_DIR));
        }
        if rewritten != text {
            std::fs::write(&config, rewritten)?;
        }
    }
    carried.sort();
    std::fs::write(
        home.join(MARKER),
        format!("from {}\n{}\n", legacy.display(), carried.join("\n")),
    )?;
    Ok(carried.len())
}

/// Gives every installed skill's origin record its new name. Links (the `from-*` skills of other
/// agents) are not followed: those folders are not the user's installations.
fn rename_origin_files(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if metadata.is_dir() {
            rename_origin_files(&path);
        } else if ORIGIN_FILES.iter().any(|name| entry.file_name() == *name) {
            let _ = std::fs::rename(&path, path.with_file_name(ORIGIN_FILE));
        }
    }
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    let metadata = std::fs::symlink_metadata(from)?;
    if metadata.file_type().is_symlink() {
        make_symlink(std::fs::read_link(from)?, to)
    } else if metadata.is_dir() {
        std::fs::create_dir_all(to)?;
        for entry in std::fs::read_dir(from)? {
            let entry = entry?;
            copy_tree(&entry.path(), &to.join(entry.file_name()))?;
        }
        Ok(())
    } else {
        std::fs::copy(from, to).map(|_| ())
    }
}

/// The old home under `home` to copy from: `~/.puffin`, when it is there and `~/.mightling` is not
/// waiting to move. An unmoved `~/.mightling` is never copied: it is moved whole by [`migrate`],
/// and until then it stays the home ([`unmoved_home`]), so a copy would only block the later move.
pub fn legacy_home(home: &Path) -> Option<PathBuf> {
    if unmoved_home(home).is_some() {
        return None;
    }
    LEGACY_HOME_DIRS
        .iter()
        .filter(|dir| **dir != MIGHTLING_HOME_DIR)
        .map(|dir| home.join(dir))
        .find(|path| path.is_dir() && !path.join(MOVED_MARKER).exists())
}

/// `~/.mightling` when it is still there and was not moved (the move waits for a running Night
/// Shift, SWE-bench run or job, or the binary is not an installed one): the home to keep using
/// until the move happens, so no second home is started beside it.
pub fn unmoved_home(home: &Path) -> Option<PathBuf> {
    let old = home.join(MIGHTLING_HOME_DIR);
    (old.is_dir() && !old.join(MOVED_MARKER).exists()).then_some(old)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ragon-rename-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    /// The layout `install.sh` and `ling update` left on a 1.5 to 1.8 client and node.
    fn mightling_layout(home: &Path) {
        let install = home.join(LEGACY_INSTALL_DIRS[0]);
        let bin = install.join("bin");
        let names = ["ling", "ling-search", "ling-fetch", "ling-code", "ling-docs", "ling-app", "codex-code-mode-host"];
        for name in names {
            write(&bin.join(name), name);
        }
        write(&install.join("indexers/scip-python"), "tool");
        let links = home.join(".local/bin");
        std::fs::create_dir_all(&links).unwrap();
        for name in ["ling", "ling-search", "ling-fetch", "ling-code", "ling-docs", "ling-app"] {
            make_symlink(bin.join(name), links.join(name)).unwrap();
        }
        let venv_bin = home.join(".local/share/dreamference/venv/bin");
        write(&venv_bin.join("ling-admin"), "admin");
        make_symlink(venv_bin.join("ling-admin"), links.join("ling-admin")).unwrap();
        write(
            &home.join(".config/dreamference/config.toml"),
            "vllm_host = \"http://gb10:8000\"\nmightling_airgapped = \"on\"\n  mightling_gmail = false\n\
             # mightling_prompt = \"x\" stays a comment\nserver_engine = \"ling\"\n[night]\nindex = true\n",
        );
        write(&home.join(".ssh/mightling-node_ed25519"), "key");
        write(&home.join(".ssh/mightling-node_ed25519.pub"), "ssh-ed25519 AAAA mightling-node");
        write(&home.join(".local/share/dev.dreamference.mightling/Cookies"), "c");
        let old_home = home.join(MIGHTLING_HOME_DIR);
        write(&old_home.join("auth.json"), "{\"token\": \"secret\"}");
        write(&old_home.join("history.jsonl"), "{}\n");
        write(&old_home.join("night/tasks/a.json"), "{}");
        write(&old_home.join("ling-code.toml"), "[submodules]\n");
        write(&old_home.join("skills/pdf/.mightling-origin.toml"), "source = \"anthropic/pdf\"\n");
        write(
            &old_home.join("config.toml"),
            "[[skills.config]]\npath = \"/home/u/.mightling/skills/x/SKILL.md\"\n\
             [mcp_servers.ling_code]\ncommand = \"/home/u/.local/share/dreamference/mightling/bin/ling-code\"\n",
        );
    }

    /// The layout `puffin update` left on a 1.4.x client and node.
    fn puffin_layout(home: &Path) {
        let bin = home.join(LEGACY_INSTALL_DIRS[1]).join("bin");
        for name in ["puffin", "puffin-search", "puffin-fetch", "puffin-code", "codex-code-mode-host"] {
            write(&bin.join(name), name);
        }
        let links = home.join(".local/bin");
        std::fs::create_dir_all(&links).unwrap();
        for name in ["puffin", "puffin-search", "puffin-fetch", "puffin-code"] {
            make_symlink(bin.join(name), links.join(name)).unwrap();
        }
        let venv_bin = home.join(".local/share/dreamference/venv/bin");
        write(&venv_bin.join("puffin-admin"), "admin");
        make_symlink(venv_bin.join("puffin-admin"), links.join("puffin-admin")).unwrap();
        write(&links.join("puffin-app"), "a real file someone else put here");
        write(
            &home.join(".config/dreamference/config.toml"),
            "puffin_airgapped = \"on\"\n# puffin_prompt = \"x\" stays a comment\n",
        );
        write(&home.join(".ssh/puffin-node_ed25519"), "key");
        let old_home = home.join(".puffin");
        write(&old_home.join("auth.json"), "{\"token\": \"secret\"}");
        write(&old_home.join("installation_id"), "id");
        write(&old_home.join("logs_2.sqlite"), "log");
        write(&old_home.join("history.jsonl"), "{}\n");
        write(&old_home.join("system-prompts/mine.md"), "be brief");
        write(&old_home.join("puffin-code.toml"), "[submodules]\n");
        write(&old_home.join("skills/pdf/.puffin-origin.toml"), "source = \"anthropic/pdf\"\n");
        write(&old_home.join("config.toml"), "path = \"/home/u/.puffin/skills/x/SKILL.md\"\n");
    }

    #[test]
    fn a_18_layout_migrates_moves_the_home_and_a_second_run_does_nothing() {
        let home = scratch("full");
        mightling_layout(&home);
        let cwd = home.join("project");
        write(&cwd.join("dreamference.toml"), "mightling_night_hour = \"3\"\n");

        let report = migrate(&home, &cwd, None);
        assert!(!report.is_empty());
        let bin = home.join(INSTALL_DIR).join("bin");
        for name in ["ragon", "ragon-search", "ragon-fetch", "ragon-code", "ragon-docs", "ragon-app", "codex-code-mode-host"] {
            assert!(bin.join(name).is_file(), "{name} in the new install folder");
        }
        assert!(!home.join(LEGACY_INSTALL_DIRS[0]).exists());
        assert!(home.join(INSTALL_DIR).join("indexers/scip-python").is_file());
        let links = home.join(".local/bin");
        for name in ["ling", "ling-search", "ling-docs", "ling-app", "ling-admin"] {
            assert!(std::fs::symlink_metadata(links.join(name)).is_err(), "{name} is not kept as an alias");
        }
        for name in ["ragon", "ragon-search", "ragon-fetch", "ragon-code", "ragon-docs", "ragon-app"] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), bin.join(name));
        }
        assert!(report.admin_missing, "the venv has no ragon-admin yet");
        assert!(report.notice().contains("Mightling is now Ragon: run `ragon`"));
        let config = std::fs::read_to_string(home.join(".config/dreamference/config.toml")).unwrap();
        assert!(config.contains("ragon_airgapped = \"on\"") && config.contains("  ragon_gmail = false"));
        assert!(config.contains("# mightling_prompt") && config.contains("server_engine = \"ragon\""));
        assert_eq!(
            std::fs::read_to_string(cwd.join("dreamference.toml")).unwrap(),
            "ragon_night_hour = \"3\"\n"
        );
        assert!(home.join(".ssh/ragon-node_ed25519").is_file() && home.join(".ssh/ragon-node_ed25519.pub").is_file());
        assert!(home.join(".local/share/dev.dreamference.ragon/Cookies").is_file());

        // The home was moved whole, login included, and fixed inside.
        let new_home = home.join(crate::home::RAGON_HOME_DIR);
        for name in ["auth.json", "history.jsonl", "night/tasks/a.json", "ragon-code.toml", "skills/pdf/.ragon-origin.toml"] {
            assert!(new_home.join(name).exists(), "{name} moved");
        }
        assert!(!new_home.join("ling-code.toml").exists());
        let moved = std::fs::read_to_string(new_home.join("config.toml")).unwrap();
        assert!(moved.contains("/home/u/.ragon/skills/") && moved.contains("[mcp_servers.ragon_code]"));
        assert!(moved.contains("/dreamference/ragon/bin/ragon-code\""));
        assert!(home.join(MIGHTLING_HOME_DIR).join(MOVED_MARKER).is_file(), "a marker stays behind");
        assert!(!home.join(MIGHTLING_HOME_DIR).join("auth.json").exists());
        assert_eq!(legacy_home(&home), None, "a moved home is not copied again");

        assert_eq!(migrate(&home, &cwd, None), Report::default(), "a second run finds nothing to do");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_14_layout_migrates_and_its_home_is_copied_without_the_login() {
        let home = scratch("puffin");
        puffin_layout(&home);
        let report = migrate(&home, &home, None);
        let bin = home.join(INSTALL_DIR).join("bin");
        for name in ["ragon", "ragon-search", "ragon-fetch", "ragon-code", "codex-code-mode-host"] {
            assert!(bin.join(name).is_file(), "{name} in the new install folder");
        }
        let links = home.join(".local/bin");
        assert!(links.join("puffin-app").is_file(), "a real file is left alone");
        assert!(std::fs::symlink_metadata(links.join("puffin")).is_err());
        assert_eq!(std::fs::read_link(links.join("ragon")).unwrap(), bin.join("ragon"));
        assert!(report.admin_missing);
        assert!(home.join(".ssh/ragon-node_ed25519").is_file());
        assert!(home.join(".puffin").is_dir(), "the 1.4 home is copied, not moved");

        let new_home = home.join(crate::home::RAGON_HOME_DIR);
        std::fs::create_dir_all(&new_home).unwrap();
        let legacy = legacy_home(&home).unwrap();
        let carried = carry_over_home(&legacy, &new_home).unwrap();
        assert!(carried >= 3);
        for name in ["auth.json", "installation_id", "logs_2.sqlite", "puffin-code.toml"] {
            assert!(!new_home.join(name).exists(), "{name} is not carried over");
        }
        for name in ["history.jsonl", "system-prompts/mine.md", "ragon-code.toml", "skills/pdf/.ragon-origin.toml"] {
            assert!(new_home.join(name).exists(), "{name} is carried over");
        }
        let config = std::fs::read_to_string(new_home.join("config.toml")).unwrap();
        assert!(config.contains("/home/u/.ragon/skills/") && !config.contains("/.puffin/"));
        assert!(new_home.join(MARKER).is_file());
        assert!(home.join(".puffin/auth.json").is_file(), "the old home is left as it was");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn the_admin_link_follows_when_the_venv_already_has_ragon_admin() {
        let home = scratch("admin");
        mightling_layout(&home);
        let venv_bin = home.join(".local/share/dreamference/venv/bin");
        write(&venv_bin.join("ragon-admin"), "admin");
        let report = migrate(&home, &home, None);
        assert!(!report.admin_missing);
        assert_eq!(
            std::fs::read_link(home.join(".local/bin/ragon-admin")).unwrap(),
            venv_bin.join("ragon-admin")
        );
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_new_home_made_before_the_move_is_moved_aside_and_kept() {
        let home = scratch("existing");
        mightling_layout(&home);
        let new_home = home.join(crate::home::RAGON_HOME_DIR);
        write(&new_home.join("keep"), "made by a binary that could not migrate");
        assert_eq!(unmoved_home(&home), Some(home.join(MIGHTLING_HOME_DIR)));
        assert_eq!(legacy_home(&home), None, "an unmoved ~/.mightling is never copied");
        let report = migrate(&home, &home, None);
        let spare = home.join(format!("{}{PRE_MIGRATION_SUFFIX}", crate::home::RAGON_HOME_DIR));
        assert!(spare.join("keep").is_file(), "the early home is kept aside");
        assert!(new_home.join("auth.json").is_file(), "~/.mightling moved whole all the same");
        assert!(report.lines.iter().any(|line| line.contains("aside")));
        assert_eq!(unmoved_home(&home), None);
        assert_eq!(migrate(&home, &home, None), Report::default(), "a second run is empty");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_second_run_after_a_full_migration_with_delegate_and_app_is_empty() {
        let home = scratch("second-run");
        mightling_layout(&home);
        let old_bin = home.join(LEGACY_INSTALL_DIRS[0]).join("bin");
        write(&old_bin.join("ling-delegate"), "ling-delegate");
        write(&old_bin.join("ling-signal"), "ling-signal");
        make_symlink(old_bin.join("ling-delegate"), home.join(".local/bin/ling-delegate")).unwrap();

        let first = migrate(&home, &home, None);
        assert!(!first.is_empty());
        let new_bin = home.join(INSTALL_DIR).join("bin");
        for name in ["ragon-delegate", "ragon-app", "ragon-signal"] {
            assert!(new_bin.join(name).is_file(), "{name} in the new install folder");
        }
        let links = home.join(".local/bin");
        for name in ["ragon-delegate", "ragon-app"] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), new_bin.join(name));
        }
        assert!(std::fs::symlink_metadata(links.join("ragon-signal")).is_err());

        let second = migrate(&home, &home, None);
        assert_eq!(second, Report::default());
        for name in ["ragon-delegate", "ragon-app"] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), new_bin.join(name));
        }
        assert!(std::fs::symlink_metadata(links.join("ragon-signal")).is_err());
        assert!(home.join(crate::home::RAGON_HOME_DIR).join("auth.json").is_file());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn an_interrupted_run_with_the_install_folder_already_moved_finishes_the_links() {
        let home = scratch("half-done");
        let new_bin = home.join(INSTALL_DIR).join("bin");
        for name in [
            "ragon",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-app",
            "ragon-delegate",
            "ragon-signal",
        ] {
            write(&new_bin.join(name), name);
        }
        let old_bin = home.join(LEGACY_INSTALL_DIRS[0]).join("bin");
        let links = home.join(".local/bin");
        std::fs::create_dir_all(&links).unwrap();
        for name in [
            "ling",
            "ling-search",
            "ling-fetch",
            "ling-code",
            "ling-docs",
            "ling-app",
            "ling-delegate",
        ] {
            make_symlink(old_bin.join(name), links.join(name)).unwrap();
        }

        let report = migrate(&home, &home, None);
        assert!(!report.is_empty());
        for name in ["ling", "ling-delegate"] {
            assert!(std::fs::symlink_metadata(links.join(name)).is_err());
        }
        for name in [
            "ragon",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-app",
            "ragon-delegate",
        ] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), new_bin.join(name));
        }
        assert!(std::fs::symlink_metadata(links.join("ragon-signal")).is_err());
        assert_eq!(migrate(&home, &home, None), Report::default());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn when_ragon_admin_already_moved_the_binaries_and_links_migrate_adds_no_link_line() {
        let home = scratch("after-admin");
        let new_bin = home.join(INSTALL_DIR).join("bin");
        let links = home.join(".local/bin");
        std::fs::create_dir_all(&links).unwrap();
        for name in [
            "ragon",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-app",
            "ragon-delegate",
        ] {
            write(&new_bin.join(name), name);
            make_symlink(new_bin.join(name), links.join(name)).unwrap();
        }
        write(&new_bin.join("ragon-signal"), "ragon-signal");
        let venv_admin = home.join(".local/share/dreamference/venv/bin/ragon-admin");
        write(&venv_admin, "admin");
        make_symlink(&venv_admin, links.join("ragon-admin")).unwrap();
        write(&home.join(MIGHTLING_HOME_DIR).join("auth.json"), "{}");

        let report = migrate(&home, &home, None);
        assert!(!report.is_empty());
        assert!(!report.lines.iter().any(|line| line.contains("linked") || line.contains("links")));
        assert!(std::fs::symlink_metadata(links.join("ragon-signal")).is_err());
        assert!(home.join(crate::home::RAGON_HOME_DIR).join("auth.json").is_file());
        assert_eq!(migrate(&home, &home, None), Report::default());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn missing_new_links_are_created_when_old_links_are_already_gone_and_real_files_stay() {
        let home = scratch("missing-links");
        let new_bin = home.join(INSTALL_DIR).join("bin");
        for name in [
            "ragon",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-app",
            "ragon-delegate",
            "ragon-signal",
        ] {
            write(&new_bin.join(name), name);
        }
        let links = home.join(".local/bin");
        write(&links.join("ragon-app"), "real file");

        let report = migrate(&home, &home, None);
        assert_eq!(report.lines.len(), 1);
        assert!(report.lines[0].starts_with("linked ragon, ragon-search, ragon-fetch, ragon-code, ragon-docs, ragon-delegate in "));
        for name in [
            "ragon",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-delegate",
        ] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), new_bin.join(name));
        }
        assert!(std::fs::symlink_metadata(links.join("ragon-signal")).is_err());
        assert!(!std::fs::symlink_metadata(links.join("ragon-app")).unwrap().file_type().is_symlink());
        assert_eq!(std::fs::read_to_string(links.join("ragon-app")).unwrap(), "real file");
        assert_eq!(migrate(&home, &home, None), Report::default());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn a_held_night_runner_lock_waits_without_moving_anything_until_released() {
        let home = scratch("night-lock");
        mightling_layout(&home);
        let lock_path = home.join(MIGHTLING_HOME_DIR).join(NIGHT_RUNNER_LOCK);
        write(&lock_path, "locked");
        let lock_file = std::fs::File::open(&lock_path).unwrap();
        lock_file.try_lock().unwrap();

        let waiting = migrate(&home, &home, None);
        assert_eq!(waiting.lines.len(), 1);
        assert!(waiting.lines[0].contains("waits for"));
        assert!(home.join(LEGACY_INSTALL_DIRS[0]).join("bin/ling").is_file());
        assert!(!home.join(INSTALL_DIR).exists());
        assert!(std::fs::symlink_metadata(home.join(".local/bin/ling")).unwrap().file_type().is_symlink());
        assert!(home.join(MIGHTLING_HOME_DIR).join("auth.json").is_file());
        assert!(!home.join(crate::home::RAGON_HOME_DIR).exists());

        drop(lock_file);
        let migrated = migrate(&home, &home, None);
        assert!(!migrated.is_empty());
        assert!(!migrated.lines.iter().any(|line| line.contains("waits for")));
        assert!(home.join(INSTALL_DIR).join("bin/ragon").is_file());
        assert!(home.join(crate::home::RAGON_HOME_DIR).join("auth.json").is_file());

        write(&lock_path, "locked");
        let held_after = std::fs::File::open(&lock_path).unwrap();
        held_after.try_lock().unwrap();
        assert!(!anything_old(&home));
        assert_eq!(migrate(&home, &home, None), Report::default());
        drop(held_after);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn unmoved_home_is_kept_until_moved_and_legacy_home_falls_back_to_puffin_only_after() {
        let home = scratch("unmoved-home");
        let mightling = home.join(MIGHTLING_HOME_DIR);
        write(&mightling.join("auth.json"), "{}");
        assert_eq!(unmoved_home(&home), Some(mightling.clone()));
        assert_eq!(legacy_home(&home), None);

        let puffin = home.join(".puffin");
        write(&puffin.join("history.jsonl"), "{}\n");
        assert_eq!(unmoved_home(&home), Some(mightling));
        assert_eq!(legacy_home(&home), None);

        migrate(&home, &home, None);
        assert_eq!(unmoved_home(&home), None);
        assert_eq!(legacy_home(&home), Some(puffin));
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_taken_spare_name_leaves_both_homes_where_they_are() {
        let home = scratch("existing-spare");
        mightling_layout(&home);
        let new_home = home.join(crate::home::RAGON_HOME_DIR);
        write(&new_home.join("keep"), "early");
        let spare = home.join(format!("{}{PRE_MIGRATION_SUFFIX}", crate::home::RAGON_HOME_DIR));
        write(&spare.join("older"), "older");
        let report = migrate(&home, &home, None);
        assert!(home.join(MIGHTLING_HOME_DIR).join("auth.json").is_file(), "no move onto a home that exists");
        assert!(new_home.join("keep").is_file() && spare.join("older").is_file());
        assert!(report.lines.iter().any(|line| line.starts_with("could not move")));
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn only_a_binary_in_an_install_folder_may_migrate() {
        let home = scratch("guard");
        mightling_layout(&home);
        let old = home.join(LEGACY_INSTALL_DIRS[0]).join("bin/ling");
        assert!(is_installed_binary(&home, &old), "what `ling update` installed");
        assert!(is_installed_binary(&home, &home.join(".local/bin/ling")), "the command link resolves to it");
        let new = home.join(INSTALL_DIR).join("bin/ragon");
        write(&new, "ragon");
        assert!(is_installed_binary(&home, &new), "a release, or what `codex build` copied there");

        let built = home.join("src/codex-rs/target/release/codex");
        write(&built, "codex");
        assert!(!is_installed_binary(&home, &built));
        let elsewhere = home.join("opt/ragon/ragon");
        write(&elsewhere, "copy");
        assert!(!is_installed_binary(&home, &elsewhere));
        assert!(!is_installed_binary(&home, &home.join(INSTALL_DIR).join("bin/sub/ragon")), "only bin itself");
        assert!(!is_installed_binary(&home.join("other"), &old), "another home's install folder");

        assert!(!allowed(&home, Some(&built), None));
        assert!(!allowed(&home, Some(&built), Some(OsStr::new("yes"))), "only 1 and 0 are choices");
        assert!(allowed(&home, Some(&built), Some(OsStr::new("1"))));
        assert!(allowed(&home, Some(&old), None));
        assert!(!allowed(&home, Some(&old), Some(OsStr::new("0"))));
        assert!(!allowed(&home, None, None), "no executable path, no migration");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[cfg(unix)]
    #[test]
    fn a_home_reached_through_a_link_still_matches() {
        let real = scratch("linked-home");
        let link = real.with_file_name(format!("ragon-rename-linked-home-link-{}", std::process::id()));
        let _ = std::fs::remove_file(&link);
        make_symlink(&real, &link).unwrap();
        let exe = real.join(INSTALL_DIR).join("bin/ragon");
        write(&exe, "ragon");
        assert!(is_installed_binary(&link, &exe));
        let _ = std::fs::remove_file(&link);
        let _ = std::fs::remove_dir_all(&real);
    }

    #[test]
    fn a_windows_install_folder_is_copied_under_the_new_names() {
        let home = scratch("windows-programs");
        let bin = home.join("AppData/Local/Programs/Mightling/bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("ling.exe"), "binary").unwrap();
        std::fs::write(bin.join("ling-docs.exe"), "docs").unwrap();
        let mut report = Report::default();
        copy_windows_install(&home, &mut report);
        let new_bin = home.join("AppData/Local/Programs/Ragon/bin");
        assert_eq!(std::fs::read_to_string(new_bin.join("ragon.exe")).unwrap(), "binary");
        assert!(new_bin.join("ragon-docs.exe").is_file() && !new_bin.join("ling.exe").exists());
        assert!(bin.join("ling.exe").is_file(), "the old folder is left as it was");
        assert_eq!(report.lines.len(), 1);
        let mut again = Report::default();
        copy_windows_install(&home, &mut again);
        assert!(again.is_empty(), "a second run finds the new folder and does nothing");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn worktrees_and_their_records_follow_the_home_even_when_the_repository_moved_too() {
        let home = scratch("worktree-links");
        let old = home.join(MIGHTLING_HOME_DIR);
        let new = home.join(crate::home::RAGON_HOME_DIR);
        let (o, n, h) = (old.display().to_string(), new.display().to_string(), home.display().to_string());
        // A phone checkout: the worktree and its bare repository both inside the home.
        write(&old.join("chat-repos/o/r/base.git/worktrees/tg-1/gitdir"), &format!("{o}/chat-repos/o/r/tg-1/.git\n"));
        write(&old.join("chat-repos/o/r/tg-1/.git"), &format!("gitdir: {o}/chat-repos/o/r/base.git/worktrees/tg-1\n"));
        write(&old.join("chat-repos/index.json"), &format!("[{{\"dir\":\"{o}/chat-repos/o/r/tg-1\"}}]"));
        // A Night Shift worktree and a `/worktree` session of a repository outside the home.
        write(&home.join("proj/.git/worktrees/wt1/gitdir"), &format!("{o}/night/worktrees/wt1/.git\n"));
        write(&old.join("night/worktrees/wt1/.git"), &format!("gitdir: {h}/proj/.git/worktrees/wt1\n"));
        write(&home.join("proj/.git/worktrees/proj/gitdir"), &format!("{o}/session-worktrees/abc/proj/.git\n"));
        write(&old.join("session-worktrees/abc/proj/.git"), &format!("gitdir: {h}/proj/.git/worktrees/proj\n"));
        write(
            &old.join("session-worktrees/abc/session.json"),
            &format!("{{\"checkout\":\"{o}/session-worktrees/abc/proj\",\"source\":\"{h}/proj\"}}"),
        );
        // A relative link moved with the home and stays as it is.
        write(&old.join("worktrees/rel/.git"), "gitdir: ../../rel.git/worktrees/rel\n");

        let mut report = Report::default();
        move_home(&home, &mut report);

        let read = |path: PathBuf| std::fs::read_to_string(path).unwrap();
        assert_eq!(read(new.join("chat-repos/o/r/tg-1/.git")), format!("gitdir: {n}/chat-repos/o/r/base.git/worktrees/tg-1\n"));
        assert_eq!(read(new.join("chat-repos/o/r/base.git/worktrees/tg-1/gitdir")), format!("{n}/chat-repos/o/r/tg-1/.git\n"));
        assert_eq!(read(new.join("chat-repos/index.json")), format!("[{{\"dir\":\"{n}/chat-repos/o/r/tg-1\"}}]"));
        assert_eq!(read(new.join("night/worktrees/wt1/.git")), format!("gitdir: {h}/proj/.git/worktrees/wt1\n"));
        assert_eq!(read(home.join("proj/.git/worktrees/wt1/gitdir")), format!("{n}/night/worktrees/wt1/.git\n"));
        assert_eq!(read(home.join("proj/.git/worktrees/proj/gitdir")), format!("{n}/session-worktrees/abc/proj/.git\n"));
        assert_eq!(
            read(new.join("session-worktrees/abc/session.json")),
            format!("{{\"checkout\":\"{n}/session-worktrees/abc/proj\",\"source\":\"{h}/proj\"}}")
        );
        assert_eq!(read(new.join("worktrees/rel/.git")), "gitdir: ../../rel.git/worktrees/rel\n");
        for worktree in ["chat-repos/o/r/tg-1", "night/worktrees/wt1", "session-worktrees/abc/proj"] {
            let line = format!("repaired the worktree {}", new.join(worktree).display());
            assert!(report.lines.contains(&line), "{line} in {:?}", report.lines);
        }
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn nothing_old_means_nothing_to_do() {
        let home = scratch("clean");
        assert!(migrate(&home, &home, None).is_empty());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn old_user_units_move_with_their_text_links_and_running_state_and_a_second_run_is_empty() {
        let home = scratch("units");
        let dir = home.join(USER_UNIT_DIR);
        let h = home.display();
        write(
            &dir.join("mightling-web.service"),
            &format!("[Unit]\nDescription=Mightling web server (ling web)\n[Service]\nExecStart={h}/.local/share/dreamference/mightling/bin/ling web serve --lan --port 3101\n"),
        );
        write(&dir.join("mightling-chat.service"), "[Unit]\nWants=mightling-web.service\nAfter=mightling-web.service\n");
        write(
            &dir.join("mightling-remote-certificate.service"),
            "[Unit]\nOnFailure=mightling-remote-certificate-alert.service\n[Service]\nSlice=mightling-batch.slice\n\
             ExecStart=%h/.local/bin/ling-admin remote certificate renew\nEnvironment=MIGHTLING_NODE=1 CODEX_HOME=%h/.mightling\n",
        );
        write(&dir.join("mightling-remote-certificate.timer"), "[Timer]\nOnCalendar=daily\n");
        write(
            &dir.join("mightling-remote-tunnel.service"),
            "[Service]\nExecStart=/usr/bin/ssh -N -o UserKnownHostsFile=/k mightling-tunnel@relay.example.com\n",
        );
        write(&dir.join("mightling-sync.service"), "old");
        write(&dir.join("ragon-sync.service"), "written by the new version");
        write(&dir.join("mightling-night.timer"), "left to ragon-admin");
        std::fs::create_dir_all(dir.join("default.target.wants")).unwrap();
        std::fs::create_dir_all(dir.join("timers.target.wants")).unwrap();
        make_symlink(dir.join("mightling-web.service"), dir.join("default.target.wants/mightling-web.service")).unwrap();
        make_symlink(dir.join("mightling-chat.service"), dir.join("default.target.wants/mightling-chat.service")).unwrap();
        make_symlink(dir.join("mightling-remote-certificate.timer"), dir.join("timers.target.wants/mightling-remote-certificate.timer")).unwrap();
        ACTIVE_UNITS.with(|active| *active.borrow_mut() = vec!["mightling-web.service".into(), "mightling-remote-certificate.timer".into()]);
        SYSTEMCTL_CALLS.with(|calls| calls.borrow_mut().clear());

        assert!(anything_old(&home));
        let report = migrate(&home, &home, None);

        let read = |name: &str| std::fs::read_to_string(dir.join(name)).unwrap();
        assert_eq!(
            read("ragon-web.service"),
            format!("[Unit]\nDescription=Ragon web server (ling web)\n[Service]\nExecStart={h}/.local/share/dreamference/ragon/bin/ragon web serve --lan --port 3101\n")
        );
        assert_eq!(read("ragon-chat.service"), "[Unit]\nWants=ragon-web.service\nAfter=ragon-web.service\n");
        assert_eq!(
            read("ragon-remote-certificate.service"),
            "[Unit]\nOnFailure=ragon-remote-certificate-alert.service\n[Service]\nSlice=ragon-batch.slice\n\
             ExecStart=%h/.local/bin/ragon-admin remote certificate renew\nEnvironment=RAGON_NODE=1 CODEX_HOME=%h/.ragon\n"
        );
        assert!(read("ragon-remote-tunnel.service").contains(" mightling-tunnel@relay.example.com\n"), "the box's account keeps its name");
        assert_eq!(read("ragon-sync.service"), "written by the new version");
        assert_eq!(read("mightling-night.timer"), "left to ragon-admin");
        for old in ["mightling-web.service", "mightling-chat.service", "mightling-sync.service", "mightling-remote-certificate.timer"] {
            assert!(!dir.join(old).exists(), "{old}");
        }
        for (wants, unit) in [("default.target.wants", "web.service"), ("default.target.wants", "chat.service"), ("timers.target.wants", "remote-certificate.timer")] {
            assert!(std::fs::symlink_metadata(dir.join(wants).join(format!("mightling-{unit}"))).is_err());
            assert_eq!(std::fs::read_link(dir.join(wants).join(format!("ragon-{unit}"))).unwrap(), dir.join(format!("ragon-{unit}")));
        }
        let calls = SYSTEMCTL_CALLS.with(|calls| calls.borrow().clone());
        let stop = calls.iter().position(|call| call == "stop mightling-remote-certificate.timer mightling-web.service").expect("the running units stop");
        let start = calls.iter().position(|call| call == "start ragon-remote-certificate.timer ragon-web.service").expect("and start again");
        assert!(stop < start && calls.contains(&"daemon-reload".to_string()), "{calls:?}");
        assert!(report.lines.iter().any(|line| line.starts_with("moved the user units mightling-chat.service,") && line.ends_with("; started ragon-remote-certificate.timer, ragon-web.service again")), "{report:?}");

        assert!(!anything_old(&home), "the night units are ragon-admin's");
        assert_eq!(migrate(&home, &home, None), Report::default());
        let _ = std::fs::remove_dir_all(&home);
    }
}

/// A symbolic link at `link` to `target`. On Windows a file or directory link by what `target` is.
fn make_symlink(target: impl AsRef<Path>, link: impl AsRef<Path>) -> io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        let (target, link) = (target.as_ref(), link.as_ref());
        let resolved = link.parent().map_or_else(|| target.to_path_buf(), |parent| parent.join(target));
        if resolved.is_dir() {
            std::os::windows::fs::symlink_dir(target, link)
        } else {
            std::os::windows::fs::symlink_file(target, link)
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link);
        Err(io::Error::new(io::ErrorKind::Unsupported, "symbolic links are not supported here"))
    }
}
