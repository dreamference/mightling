//! The launcher's `[[skills.config]]` entries in Codex's `config.toml` (spec §4, §7).
//!
//! A skill outside the `from-*` folders that must not be offered (it needs a program that is not
//! installed, its author marked it manual-only, a skill of higher precedence has its name, or the
//! user ran `mling skill disable`) is switched off the only way Codex offers: an entry selecting
//! it by the path of its `SKILL.md`.
//!
//! Each entry the launcher writes sits under a comment line of its own, and those entries are the
//! only thing it ever removes: they are rewritten at every start, so a skill whose missing program
//! is later installed comes back by itself, and an entry the user wrote is never touched. A
//! comment, not a key, because Codex refuses a `config.toml` with keys it does not know.

use std::path::Path;
use std::path::PathBuf;

/// The line above every launcher-owned entry; the reason follows it.
pub const MARKER: &str = "# mling skill, switched off by the launcher (rewritten at every start):";

/// `text` without the launcher's entries.
pub fn without_managed(text: &str) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let mut kept = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        if lines[index].starts_with(MARKER) {
            // The comment, the header and the two keys the launcher wrote, then one blank line.
            index += 1;
            if lines.get(index).is_some_and(|line| line.trim() == "[[skills.config]]") {
                index += 1;
                while lines.get(index).is_some_and(|line| is_entry_key(line)) {
                    index += 1;
                }
            }
            if lines.get(index).is_some_and(|line| line.trim().is_empty()) {
                index += 1;
            }
            continue;
        }
        kept.push(lines[index]);
        index += 1;
    }
    let mut out = kept.join("\n");
    while out.ends_with("\n\n") {
        out.pop();
    }
    if !out.is_empty() && !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

fn is_entry_key(line: &str) -> bool {
    let line = line.trim_start();
    line.starts_with("path = ") || line.starts_with("enabled = ")
}

/// The `SKILL.md` paths the user's own entries select, on or off: the launcher leaves those
/// skills to the user.
pub fn user_paths(text: &str) -> Vec<PathBuf> {
    let Ok(table) = without_managed(text).parse::<toml::Table>() else { return Vec::new() };
    table
        .get("skills")
        .and_then(|skills| skills.get("config"))
        .and_then(toml::Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| entry.get("path").and_then(toml::Value::as_str).map(PathBuf::from))
                .collect()
        })
        .unwrap_or_default()
}

/// `text` with the launcher's entries replaced by `entries` (the `SKILL.md` to switch off, and
/// why). A path the user has an entry for is left to them.
///
/// Returns the text unchanged, apart from the old entries being gone, when adding entries would
/// not parse: a `[skills]` table the user wrote with an inline `config = […]` cannot also take
/// `[[skills.config]]` tables, and a `config.toml` that does not parse stops Codex at start.
pub fn with_managed(text: &str, entries: &[(PathBuf, String)]) -> Result<String, String> {
    let base = without_managed(text);
    let user = user_paths(&base);
    let entries: Vec<&(PathBuf, String)> = entries.iter().filter(|(path, _)| !user.contains(path)).collect();
    if entries.is_empty() {
        return Ok(base);
    }
    let mut out = base.clone();
    if !out.is_empty() {
        out.push('\n');
    }
    for (index, (path, reason)) in entries.iter().enumerate() {
        if index > 0 {
            out.push('\n');
        }
        out.push_str(&entry_text(path, reason));
    }
    match out.parse::<toml::Table>() {
        Ok(_) => Ok(out),
        Err(error) => Err(format!(
            "could not switch skills off in config.toml ({}); they stay offered",
            error.message()
        )),
    }
}

fn entry_text(path: &Path, reason: &str) -> String {
    let reason: String = reason.chars().filter(|ch| !ch.is_control()).collect();
    format!(
        "{MARKER} {reason}\n[[skills.config]]\npath = {}\nenabled = false\n",
        toml::Value::String(path.to_string_lossy().into_owned())
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const USER: &str = "model = \"m\"\n\n[features]\ncode_mode = true\n\n[[skills.config]]\nname = \"mine\"\nenabled = false\n";

    fn entries(paths: &[&str]) -> Vec<(PathBuf, String)> {
        paths.iter().map(|path| (PathBuf::from(path), "needs gh".to_string())).collect()
    }

    #[test]
    fn entries_are_added_under_their_marker_and_parse() {
        let out = with_managed(USER, &entries(&["/h/.agents/skills/a/SKILL.md"])).unwrap_or_default();
        assert!(out.starts_with(USER));
        assert!(out.contains(&format!("{MARKER} needs gh\n[[skills.config]]\npath = \"/h/.agents/skills/a/SKILL.md\"\nenabled = false\n")));
        let table: toml::Table = out.parse().unwrap_or_default();
        let config = table["skills"]["config"].as_array().map(Vec::len);
        assert_eq!(config, Some(2));
    }

    #[test]
    fn they_are_rewritten_whole_and_the_users_are_untouched() {
        let first = with_managed(USER, &entries(&["/a/SKILL.md", "/b/SKILL.md"])).unwrap_or_default();
        // The next start wants a different set: the old ones go, the user's stays, and writing
        // the same set twice changes nothing.
        let second = with_managed(&first, &entries(&["/b/SKILL.md"])).unwrap_or_default();
        assert!(!second.contains("/a/SKILL.md"));
        assert!(second.contains("/b/SKILL.md") && second.contains("name = \"mine\""));
        assert_eq!(with_managed(&second, &entries(&["/b/SKILL.md"])).as_deref(), Ok(second.as_str()));
        assert_eq!(with_managed(&second, &[]).as_deref(), Ok(USER));
        assert_eq!(without_managed(USER), USER);
    }

    #[test]
    fn a_table_added_after_the_entries_is_kept_when_they_are_rewritten() {
        // toml_edit appends a table it creates at the end of the file, i.e. after the entries.
        let first = with_managed(USER, &entries(&["/a/SKILL.md"])).unwrap_or_default();
        let grown = format!("{first}\n[sandbox_workspace_write]\nnetwork_access = true\n");
        let second = with_managed(&grown, &entries(&["/c/SKILL.md"])).unwrap_or_default();
        assert!(second.contains("[sandbox_workspace_write]\nnetwork_access = true\n"));
        assert!(!second.contains("/a/SKILL.md") && second.contains("/c/SKILL.md"));
        assert!(second.parse::<toml::Table>().is_ok());
    }

    #[test]
    fn a_path_the_user_has_an_entry_for_is_left_to_them() {
        let user = "[[skills.config]]\npath = \"/a/SKILL.md\"\nenabled = true\n";
        assert_eq!(user_paths(user), vec![PathBuf::from("/a/SKILL.md")]);
        let out = with_managed(user, &entries(&["/a/SKILL.md", "/b/SKILL.md"])).unwrap_or_default();
        assert_eq!(out.matches("/a/SKILL.md").count(), 1);
        assert!(out.contains("/b/SKILL.md"));
    }

    #[test]
    fn a_config_that_cannot_take_the_entries_is_left_without_them() {
        let inline = "[skills]\nconfig = [{ name = \"x\", enabled = false }]\n";
        let result = with_managed(inline, &entries(&["/a/SKILL.md"]));
        assert!(result.as_ref().is_err_and(|error| error.contains("stay offered")), "{result:?}");
    }

    #[test]
    fn a_path_with_quotes_or_backslashes_is_escaped() {
        let out = with_managed("", &entries(&["/a/we\"ird\\name/SKILL.md"])).unwrap_or_default();
        assert_eq!(user_paths(&out.replace(MARKER, "# mine:")), vec![PathBuf::from("/a/we\"ird\\name/SKILL.md")]);
    }
}
