//! `ling chat …`: Mightling from a phone messenger (specs/DREAMFERENCE_MIGHTLING_CHAT.md). The bridge is
//! the `ling-chat` crate (`chat/`); this module only tells it where things are: the home folder, the
//! user's home (the user unit, the user-level config) and this binary (the unit runs it, and the
//! bridge starts `ling web` with it when nothing answers).

use std::io::Write;
use std::path::Path;
use std::path::PathBuf;

pub const FULL_ACCESS_PROMPT: &str = "Phone threads may then run commands with no sandbox after the owner types FULL ACCESS <repo> in the page. Type yes to allow:";

const REPOS_USAGE: &str = "Usage: ling chat repos full-access on | off";

/// One line from the terminal after `prompt`, without its line ending (only that: ` yes` is not
/// `yes`), or None at the end of input.
fn read_terminal_line(prompt: &str) -> Option<String> {
    print!("{prompt} ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    std::io::stdin()
        .read_line(&mut line)
        .ok()
        .filter(|&n| n > 0)
        .map(|_| line.trim_end_matches(['\r', '\n']).to_string())
}

/// Writes `[chat_repos] full_access = <enabled>` into `path` with `toml_edit`, so every other
/// table, key and comment in the file is kept (an inline `chat_repos = { … }` is edited, not
/// replaced), creating the folder, the file and the table when missing. A file that cannot be read
/// for another reason than not existing is left alone, and the new text replaces the old in one
/// rename, so the air-gap check never reads a half-written file.
pub fn write_full_access(path: &Path, enabled: bool) -> Result<(), String> {
    let existing = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(err) => return Err(format!("could not read {}: {err}", path.display())),
    };
    let mut document: toml_edit::DocumentMut = existing
        .parse()
        .map_err(|err| format!("{} is not valid TOML: {err}", path.display()))?;
    if !document.get("chat_repos").is_some_and(|item| item.is_table_like()) {
        document.insert("chat_repos", toml_edit::Item::Table(toml_edit::Table::new()));
    }
    let Some(table) = document.get_mut("chat_repos").and_then(toml_edit::Item::as_table_like_mut) else {
        return Err(format!("{}: [chat_repos] is not a table", path.display()));
    };
    table.insert("full_access", toml_edit::value(enabled));
    let parent = path.parent().filter(|parent| !parent.as_os_str().is_empty()).unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent).map_err(|err| format!("could not create {}: {err}", parent.display()))?;
    let staged = parent.join(format!(".config.toml.{}.tmp", std::process::id()));
    std::fs::write(&staged, document.to_string()).map_err(|err| format!("could not write {}: {err}", staged.display()))?;
    std::fs::rename(&staged, path).map_err(|err| {
        let _ = std::fs::remove_file(&staged);
        format!("could not write {}: {err}", path.display())
    })
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
        if !std::io::IsTerminal::is_terminal(&std::io::stdin()) {
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
        // An inline table is edited where it stands: `allow_main` stays.
        std::fs::write(&config, "chat_repos = { allow_main = [\"org/widget\"] }\n").unwrap();
        write_full_access(&config, true).unwrap();
        let inline = std::fs::read_to_string(&config).unwrap();
        let parsed: toml::Table = inline.parse().unwrap();
        assert_eq!(parsed["chat_repos"]["full_access"].as_bool(), Some(true), "{inline}");
        assert_eq!(parsed["chat_repos"]["allow_main"][0].as_str(), Some("org/widget"), "{inline}");

        // A file that is not UTF-8 is left alone rather than replaced.
        std::fs::write(&config, [0xff_u8, 0xfe, 0x00]).unwrap();
        assert!(write_full_access(&config, true).is_err());
        assert_eq!(std::fs::read(&config).unwrap(), vec![0xff_u8, 0xfe, 0x00]);

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

    #[test]
    fn repos_cli_refuses_any_other_shape_before_it_reads_or_writes_anything() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        for words in [
            &["repos"][..],
            &["repos", "full-access"],
            &["repos", "full-access", "yes"],
            &["repos", "full-access", "ON"],
            &["repos", "full-access", "on", "now"],
            &["repos", "full_access", "on"],
            &["repos", "list", "off"],
        ] {
            assert_eq!(run_repos_cli(&args(words)), 2, "{words:?}");
        }
    }

    #[test]
    fn write_full_access_leaves_a_file_that_is_not_toml_as_it_was() {
        let dir = std::env::temp_dir().join(format!("ling-chat-repos-bad-toml-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let config = dir.join("config.toml");
        std::fs::write(&config, "[chat_repos\nfull_access = true\n").unwrap();
        assert!(write_full_access(&config, false).unwrap_err().contains("not valid TOML"));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), "[chat_repos\nfull_access = true\n");
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1, "no staged file is left behind");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
