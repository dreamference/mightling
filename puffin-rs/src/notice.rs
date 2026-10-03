//! What the launcher says at start, shown inside the TUI as well.
//!
//! The launcher prints a few lines before Codex starts: the air-gap level at `on`, skills that
//! were skipped or quarantined, what a night run finished, a prompt other than `default`. They go
//! to stderr, which is right for `puffin exec`, but the TUI's first frame covers them and they are
//! not in its scrollback (measured 2026-10-03), so in the TUI they were never seen.
//!
//! No patch is needed to put them in the session. Codex shows a `SessionStart` hook's
//! `systemMessage` as a line in the history, so for an interactive session with something to say
//! the launcher writes the lines to `$CODEX_HOME/notice/<hash>.txt` and registers
//! `<this binary> notice <hash>` as a hook under matcher `startup|resume` (compaction.rs does the
//! registration, as for the ledger). Codex runs it at the start of the session's first turn, so
//! the lines appear above the first answer, not on the empty screen before it. Without lines, or
//! for `exec`, the hook is removed again: `exec` prints every hook run, and its lines are on
//! stderr already.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;
use std::time::SystemTime;

use serde_json::json;
use sha2::Digest;
use sha2::Sha256;

use crate::compaction::SessionHook;

/// The hook: `<this binary> notice <hash>`, at the start of a new or resumed session.
pub const NOTICE_HOOK: SessionHook = SessionHook { subcommand: "notice", matcher: "startup|resume", timeout: 5 };

/// Notice files older than this are deleted at the next launch.
const KEEP: Duration = Duration::from_secs(7 * 24 * 3600);

static LINES: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// Prints a start-up line to stderr and keeps it for the TUI.
pub fn say(line: &str) {
    eprintln!("{line}");
    if let Ok(mut lines) = LINES.lock() {
        lines.push(line.to_string());
    }
}

/// The lines said so far in this process.
pub fn said() -> Vec<String> {
    LINES.lock().map(|lines| lines.clone()).unwrap_or_default()
}

fn notice_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("notice")
}

/// The file name for `lines`: a short hash of their text, so the hook's command (and so its trust
/// hash) changes only when what it says does.
pub fn key(lines: &[String]) -> String {
    let digest = Sha256::digest(lines.join("\n").as_bytes());
    digest.iter().take(8).map(|byte| format!("{byte:02x}")).collect()
}

/// Writes what was said for the TUI and registers or removes the hook. `interactive` is the TUI.
/// Nothing here may stop a session from starting, so failures are ignored.
pub fn publish(codex_home: &Path, interactive: bool) {
    prune(&notice_dir(codex_home));
    let lines = said();
    let config_path = codex_home.join("config.toml");
    if !interactive || lines.is_empty() {
        let _ = crate::compaction::register_session_hook(&config_path, &NOTICE_HOOK, "", false);
        return;
    }
    let key = key(&lines);
    let dir = notice_dir(codex_home);
    if std::fs::create_dir_all(&dir).is_err()
        || crate::write_atomically(&dir.join(format!("{key}.txt")), lines.join("\n").as_bytes()).is_err()
    {
        return;
    }
    let _ = crate::compaction::register_session_hook(&config_path, &NOTICE_HOOK, &key, true);
}

/// Deletes notice files older than [`KEEP`].
fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let now = SystemTime::now();
    for entry in entries.flatten() {
        let old = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > KEEP);
        if old {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// The hook's answer for the notice called `key`: a `systemMessage`, or nothing.
pub fn answer(codex_home: &Path, key: &str) -> Option<String> {
    if key.is_empty() || !key.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let text = std::fs::read_to_string(notice_dir(codex_home).join(format!("{key}.txt"))).ok()?;
    let text = text.trim();
    (!text.is_empty()).then(|| json!({ "systemMessage": text }).to_string())
}

/// `puffin notice <key>`, run by Codex as the hook. Never an error: a failing `SessionStart` hook
/// would be reported in the session.
pub fn run_cli(args: &[String]) -> i32 {
    // Codex writes the hook's input to stdin; it is not needed, and an unread pipe is harmless.
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        return 0;
    };
    if let Some(answer) = args.first().and_then(|key| answer(&codex_home, key)) {
        println!("{answer}");
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("puffin-notice-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn the_key_follows_the_text() {
        let one = vec!["a".to_string()];
        let two = vec!["b".to_string()];
        assert_eq!(key(&one), key(&one.clone()));
        assert_ne!(key(&one), key(&two));
        assert_eq!(key(&one).len(), 16);
    }

    #[test]
    fn the_hook_answers_with_the_lines_as_a_system_message() {
        let home = scratch("answer");
        let lines = vec!["🔒 Airgapped: on".to_string(), "NOT ENFORCED for: x".to_string()];
        std::fs::create_dir_all(notice_dir(&home)).unwrap();
        std::fs::write(notice_dir(&home).join(format!("{}.txt", key(&lines))), lines.join("\n")).unwrap();
        let answer = answer(&home, &key(&lines)).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&answer).unwrap();
        assert_eq!(parsed["systemMessage"], json!("🔒 Airgapped: on\nNOT ENFORCED for: x"));
    }

    #[test]
    fn the_hook_says_nothing_for_an_unknown_or_unsafe_key() {
        let home = scratch("unknown");
        assert_eq!(answer(&home, "0011223344556677"), None);
        assert_eq!(answer(&home, "../config"), None);
        assert_eq!(answer(&home, ""), None);
    }

    #[test]
    fn the_hook_is_registered_beside_the_ledger_and_removed_alone() {
        use crate::compaction::LEDGER_HOOK;
        use crate::compaction::hook_hash_for;
        use crate::compaction::with_session_hook;
        let path = Path::new("/home/u/.puffin/config.toml");
        let ledger = with_session_hook("model = \"m\"\n", path, "/bin/puffin ledger", &LEDGER_HOOK, true).unwrap();
        let both = with_session_hook(&ledger, path, "/bin/puffin notice 00ff", &NOTICE_HOOK, true).unwrap();
        let parsed: toml::Table = both.parse().unwrap();
        let groups = parsed["hooks"]["SessionStart"].as_array().unwrap();
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[1]["matcher"].as_str(), Some("startup|resume"));
        let state = parsed["hooks"]["state"].as_table().unwrap();
        let key = format!("{}:session_start:1:0", path.display());
        assert_eq!(
            state[&key]["trusted_hash"].as_str(),
            Some(hook_hash_for("/bin/puffin notice 00ff", &NOTICE_HOOK).as_str())
        );
        // A new text replaces the command in place, and removing it leaves the ledger as it was.
        let changed = with_session_hook(&both, path, "/bin/puffin notice 11aa", &NOTICE_HOOK, true).unwrap();
        assert_eq!(changed.parse::<toml::Table>().unwrap()["hooks"]["SessionStart"].as_array().unwrap().len(), 2);
        let removed = with_session_hook(&changed, path, "/bin/puffin notice", &NOTICE_HOOK, false).unwrap();
        assert_eq!(removed, ledger);
    }
}
