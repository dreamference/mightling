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
const WORKTREE_FOLDERS: &[&str] = &["worktrees", "chat-repos", "night"];

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
pub fn migrate(home: &Path, cwd: &Path, config_path: Option<&Path>) -> Report {
    let mut report = Report::default();
    move_install_dir(home, &mut report);
    copy_windows_install(home, &mut report);
    relink(home, &mut report);
    move_home(home, &mut report);
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
    if removed.is_empty() {
        return;
    }
    for (old, new) in BINARIES {
        if removed.contains(old) && bin.join(new).is_file() {
            let _ = link(&bin.join(new), &links.join(new));
        }
    }
    report.lines.push(format!(
        "replaced the links {} in {}",
        removed.join(", "),
        links.display()
    ));
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
    fix_home(&new, report);
}

/// Rewrites the inside of a home that came from `~/.mightling`.
fn fix_home(new: &Path, report: &mut Report) {
    rename_files(new);
    rename_origin_files(&new.join("skills"));
    rewrite_config(&new.join("config.toml"));
    for folder in WORKTREE_FOLDERS {
        repair_worktrees(&new.join(folder), 0, report);
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

/// Runs `git worktree repair` in every worktree under `dir` (a folder with a `.git` file), at most
/// three levels down. Best effort: a missing git or a broken repository is skipped.
fn repair_worktrees(dir: &Path, depth: usize, report: &mut Report) {
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
            let repaired = std::process::Command::new("git")
                .arg("-C")
                .arg(&path)
                .args(["worktree", "repair"])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .is_ok_and(|status| status.success());
            if repaired {
                report.lines.push(format!("repaired the worktree {}", path.display()));
            }
        } else if depth < 3 {
            repair_worktrees(&path, depth + 1, report);
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
    fn nothing_old_means_nothing_to_do() {
        let home = scratch("clean");
        assert!(migrate(&home, &home, None).is_empty());
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
