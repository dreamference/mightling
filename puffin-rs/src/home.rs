//! Puffin's own configuration folder, `~/.puffin`.
//!
//! Codex keeps its login, settings and session history in `$CODEX_HOME`, which defaults to
//! `~/.codex`, and upstream Codex writes a ChatGPT login there (`auth.json`). A Puffin sharing that
//! folder inherited the login, and with it every channel Codex opens to OpenAI when one is present:
//! usage analytics, token refresh, the hosted apps connectors, the remote plugin catalogue. Puffin
//! uses its own folder instead, so none of them has anything to authenticate with. The keyring is
//! covered too: Codex keys stored credentials by a hash of the home folder's path, so a new folder
//! has no entry.
//!
//! [`use_puffin_home`] runs first thing in Codex's `main()` (patch 0014). It has to: `arg0` reads
//! `.env` from the home folder and creates helper files there before the CLI parses anything, and
//! setting an environment variable is only sound before any other thread exists.

use std::io;
use std::path::Path;
use std::path::PathBuf;

/// Puffin's configuration folder, relative to `$HOME`.
pub const PUFFIN_HOME_DIR: &str = ".puffin";

/// Written into `~/.puffin` after the first-run copy, listing what was carried over.
pub const MIGRATION_MARKER: &str = ".migrated-from-codex";

/// Entries carried over from `~/.codex` on first run. An allow-list, so that `auth.json`, and
/// anything a later Codex adds, is left behind unless named here.
const CARRY_OVER: &[&str] = &[
    "sessions",
    "archived_sessions",
    "history.jsonl",
    "config.toml",
    "AGENTS.md",
    "rules",
    "skills",
    "prompts",
    "plugins",
];

/// Session-state databases, matched by prefix so a Codex schema bump (`state_5` to `state_6`)
/// still carries over; each is copied with its `-wal` and `-shm` files. Debug logs (`logs_*`) are
/// not session state and stay behind.
const CARRY_OVER_DB_PREFIXES: &[&str] =
    &["state_", "thread_history_", "memories_", "goals_", "queue_"];

/// Points Codex at `~/.puffin`, creating it and carrying over session history on first run.
///
/// An explicit `CODEX_HOME` is respected, so tests and users can still choose a folder.
pub fn use_puffin_home() {
    // Refine mode's `--refine`/`--no-refine` become its variable here, for the same reason.
    crate::refine::export_flag();
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        return;
    };
    let Some(puffin_home) = resolve(std::env::var_os("CODEX_HOME").as_deref(), &home) else {
        return;
    };
    if !puffin_home.exists() && create_private_dir(&puffin_home).is_ok() {
        let upstream = home.join(".codex");
        if upstream.is_dir()
            && let Ok(carried) = migrate(&upstream, &puffin_home)
            && carried > 0
        {
            eprintln!(
                "puffin: copied your session history from {} to {} ({carried} items). \
                 No cloud sign-in was copied: Puffin has no account to sign in to.",
                upstream.display(),
                puffin_home.display()
            );
        }
    }
    // SAFETY: called first thing in Codex's `main()`, before the async runtime or any other thread
    // exists, so nothing can be reading the environment concurrently.
    unsafe {
        std::env::set_var("CODEX_HOME", &puffin_home);
    }
}

/// The folder Codex should use: `None` when `CODEX_HOME` is already set (leave it alone).
pub fn resolve(codex_home_env: Option<&std::ffi::OsStr>, home: &Path) -> Option<PathBuf> {
    match codex_home_env {
        Some(value) if !value.is_empty() => None,
        _ => Some(home.join(PUFFIN_HOME_DIR)),
    }
}

fn create_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

/// Copies the allow-listed entries of `from` into `to` and records them in the marker file.
///
/// Returns how many top-level entries were copied. A failure on one entry skips it, not the rest.
pub fn migrate(from: &Path, to: &Path) -> io::Result<usize> {
    let mut carried = Vec::new();
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if !should_carry_over(&name) {
            continue;
        }
        if copy_tree(&entry.path(), &to.join(&name)).is_ok() {
            carried.push(name);
        }
    }
    carried.sort();
    std::fs::write(
        to.join(MIGRATION_MARKER),
        format!("from {}\n{}\n", from.display(), carried.join("\n")),
    )?;
    Ok(carried.len())
}

/// Whether a top-level entry of `~/.codex` is carried over.
pub fn should_carry_over(name: &str) -> bool {
    if CARRY_OVER.contains(&name) {
        return true;
    }
    let base = name
        .strip_suffix("-wal")
        .or_else(|| name.strip_suffix("-shm"))
        .unwrap_or(name);
    base.ends_with(".sqlite")
        && CARRY_OVER_DB_PREFIXES
            .iter()
            .any(|prefix| base.starts_with(prefix))
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    let metadata = std::fs::symlink_metadata(from)?;
    if metadata.file_type().is_symlink() {
        std::os::unix::fs::symlink(std::fs::read_link(from)?, to)
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

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("puffin-home-test-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        dir
    }

    #[test]
    fn an_explicit_codex_home_is_left_alone() {
        let home = Path::new("/home/u");
        assert_eq!(resolve(Some(std::ffi::OsStr::new("/elsewhere")), home), None);
        assert_eq!(resolve(None, home), Some(PathBuf::from("/home/u/.puffin")));
        assert_eq!(resolve(Some(std::ffi::OsStr::new("")), home), Some(PathBuf::from("/home/u/.puffin")));
    }

    #[test]
    fn the_chatgpt_login_and_openai_state_are_never_carried_over() {
        for name in ["auth.json", "installation_id", "packages", "cache", "logs_2.sqlite", "logs_2.sqlite-wal", "version.json", "log"] {
            assert!(!should_carry_over(name), "{name} must stay behind");
        }
        for name in ["sessions", "history.jsonl", "config.toml", "state_5.sqlite", "state_5.sqlite-wal", "thread_history_1.sqlite-shm", "memories_1.sqlite"] {
            assert!(should_carry_over(name), "{name} should be carried over");
        }
    }

    #[test]
    fn migration_copies_history_but_not_the_login() {
        let from = scratch("from");
        let to = scratch("to");
        std::fs::write(from.join("auth.json"), "{\"tokens\":\"secret\"}").unwrap_or_default();
        std::fs::write(from.join("history.jsonl"), "{}\n").unwrap_or_default();
        std::fs::create_dir_all(from.join("sessions/2026/09")).unwrap_or_default();
        std::fs::write(from.join("sessions/2026/09/a.jsonl"), "x").unwrap_or_default();

        assert_eq!(migrate(&from, &to).ok(), Some(2));
        assert!(to.join("history.jsonl").is_file());
        assert!(to.join("sessions/2026/09/a.jsonl").is_file());
        assert!(!to.join("auth.json").exists());
        let marker = std::fs::read_to_string(to.join(MIGRATION_MARKER)).unwrap_or_default();
        assert!(marker.contains("sessions") && !marker.contains("auth.json"));

        let _ = std::fs::remove_dir_all(&from);
        let _ = std::fs::remove_dir_all(&to);
    }
}
