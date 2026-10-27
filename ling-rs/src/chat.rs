//! `ling chat …`: Mightling from a phone messenger (specs/DREAMFERENCE_MIGHTLING_CHAT.md). The bridge is
//! the `ling-chat` crate (`chat/`); this module only tells it where things are: the home folder, the
//! user's home (the user unit, the user-level config) and this binary (the unit runs it, and the
//! bridge starts `ling web` with it when nothing answers).

use std::io::Write;
use std::path::Path;
use std::path::PathBuf;

pub const FULL_ACCESS_PROMPT: &str = "Phone threads may then run commands with no sandbox after the owner types FULL ACCESS <repo> in the page. Type yes to allow:";

const REPOS_USAGE: &str = "Usage: ling chat repos full-access on | off";

#[cfg(unix)]
fn stdin_is_terminal() -> bool {
    // SAFETY: `isatty(0)` only inspects file descriptor 0 (stdin).
    unsafe { libc::isatty(0) == 1 }
}

#[cfg(not(unix))]
fn stdin_is_terminal() -> bool {
    false
}

fn read_terminal_line(prompt: &str) -> Option<String> {
    print!("{prompt} ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    std::io::stdin()
        .read_line(&mut line)
        .ok()
        .filter(|&n| n > 0)
        .map(|_| line.trim().to_string())
}

/// Writes `[chat_repos] full_access = <enabled>` into `path` using `toml_edit::DocumentMut`
/// so every other table, key, and comment in the file is preserved, creating the parent
/// folder, file, and `[chat_repos]` table when missing.
pub fn write_full_access(path: &Path, enabled: bool) -> Result<(), String> {
    let existing = std::fs::read_to_string(path).unwrap_or_default();
    let mut document: toml_edit::DocumentMut = existing
        .parse()
        .map_err(|err| format!("{} is not valid TOML: {err}", path.display()))?;
    if !document.get("chat_repos").is_some_and(toml_edit::Item::is_table) {
        document.insert("chat_repos", toml_edit::Item::Table(toml_edit::Table::new()));
    }
    document["chat_repos"]["full_access"] = toml_edit::value(enabled);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|err| format!("could not create {}: {err}", parent.display()))?;
    }
    std::fs::write(path, document.to_string())
        .map_err(|err| format!("could not write {}: {err}", path.display()))
}

fn run_repos_cli(args: &[String]) -> i32 {
    if args.len() != 3 || args.get(1).map(String::as_str) != Some("full-access") {
        eprintln!("{REPOS_USAGE}");
        return 2;
    }
    let enabled = match args.get(2).map(String::as_str) {
        Some("on") => true,
        Some("off") => false,
        _ => {
            eprintln!("{REPOS_USAGE}");
            return 2;
        }
    };
    let Some(path) = ling_airgapped::user_config_file() else {
        eprintln!("HOME is not set.");
        return 1;
    };
    if enabled {
        if !stdin_is_terminal() {
            eprintln!("`ling chat repos full-access on` asks for confirmation at a terminal; run it in one.");
            return 1;
        }
        if read_terminal_line(FULL_ACCESS_PROMPT).as_deref() != Some("yes") {
            println!("Full Access for phone threads stays off.");
            return 1;
        }
    }
    match write_full_access(&path, enabled) {
        Ok(()) => {
            println!("Set [chat_repos] full_access = {enabled} in {}.", path.display());
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

/// `ling chat …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("repos") {
        return run_repos_cli(args);
    }
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("Could not resolve CODEX_HOME.");
        return 1;
    };
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        eprintln!("HOME is not set.");
        return 1;
    };
    let ling = match std::env::current_exe() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("Could not find this program's path: {err}");
            return 1;
        }
    };
    ling_chat::run_cli(args, ling_chat::Environment { codex_home, home, ling }).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn write_full_access_creates_table_and_preserves_existing_settings_and_comments() {
        let dir = std::env::temp_dir().join(format!("ling-chat-repos-toml-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let config = dir.join("nested/config.toml");

        // 1. Creates parent directory, file, and [chat_repos] table when missing.
        write_full_access(&config, true).unwrap();
        let created = std::fs::read_to_string(&config).unwrap();
        assert!(created.contains("[chat_repos]"), "{created}");
        assert!(created.contains("full_access = true"), "{created}");

        // 2. Preserves comments, other tables, and other keys in [chat_repos] when editing.
        let initial = "# Top comment\nmightling_airgapped = \"off\"\n\n[chat_repos]\n# Keep this comment\nallow_main = [\"org/widget\"]\nmax_checkouts = 15\n\n[chat]\napproval_timeout_s = 120\n";
        std::fs::write(&config, initial).unwrap();

        write_full_access(&config, true).unwrap();
        let after_on = std::fs::read_to_string(&config).unwrap();
        assert!(after_on.contains("# Top comment"), "{after_on}");
        assert!(after_on.contains("# Keep this comment"), "{after_on}");
        assert!(after_on.contains("allow_main = [\"org/widget\"]"), "{after_on}");
        assert!(after_on.contains("max_checkouts = 15"), "{after_on}");
        assert!(after_on.contains("approval_timeout_s = 120"), "{after_on}");
        assert!(after_on.contains("full_access = true"), "{after_on}");

        write_full_access(&config, false).unwrap();
        let after_off = std::fs::read_to_string(&config).unwrap();
        assert!(after_off.contains("# Top comment"), "{after_off}");
        assert!(after_off.contains("# Keep this comment"), "{after_off}");
        assert!(after_off.contains("allow_main = [\"org/widget\"]"), "{after_off}");
        assert!(after_off.contains("full_access = false"), "{after_off}");
        assert!(!after_off.contains("full_access = true"), "{after_off}");

        let _ = std::fs::remove_dir_all(&dir);
    }
}
