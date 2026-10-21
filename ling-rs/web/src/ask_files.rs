//! Files the agent made in an Ask thread's folder, for a phone bridge to offer and send back
//! (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16.7; MOONSHOT §3.7): `/api/ask/files` lists what changed
//! since a time, `/api/ask/file` gives one file's bytes.
//!
//! Only the thread's own folder is read: names are relative, `..`, absolute paths and hidden
//! entries are refused, symbolic links are never followed, and only regular files are listed or
//! read. At a user-level `/airgapped on` nothing is given out: a file sent back would leave the
//! machine through the messenger.

use std::path::Path;
use std::path::PathBuf;
use std::time::UNIX_EPOCH;

use serde_json::Value;
use serde_json::json;

/// The most entries a listing returns, and how deep it looks.
pub const MAX_LISTED: usize = 50;
const MAX_DEPTH: usize = 3;
/// The largest file given out: the largest any messenger here takes (Signal, 100 MB).
pub const MAX_FILE_BYTES: u64 = 100 * 1024 * 1024;

fn modified_ms(metadata: &std::fs::Metadata) -> u64 {
    metadata.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// The regular files under `folder` changed at or after `since_ms`, newest first:
/// `[{name, bytes, modifiedMs}]`, `name` relative with `/` between parts.
pub fn changed_since(folder: &Path, since_ms: u64) -> Vec<Value> {
    let mut found = Vec::new();
    let mut pending = vec![(folder.to_path_buf(), String::new(), 0usize)];
    while let Some((dir, prefix, depth)) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                continue;
            }
            let Ok(metadata) = entry.path().symlink_metadata() else { continue };
            let relative = if prefix.is_empty() { name.clone() } else { format!("{prefix}/{name}") };
            if metadata.is_dir() && depth + 1 < MAX_DEPTH {
                pending.push((entry.path(), relative, depth + 1));
            } else if metadata.is_file() && modified_ms(&metadata) >= since_ms {
                found.push((modified_ms(&metadata), relative, metadata.len()));
            }
        }
    }
    found.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
    found.truncate(MAX_LISTED);
    found.into_iter().map(|(at, name, bytes)| json!({ "name": name, "bytes": bytes, "modifiedMs": at })).collect()
}

/// The path of `name` inside `folder`, if it is a regular file there reached without a link.
pub fn file_in(folder: &Path, name: &str) -> Option<PathBuf> {
    if name.is_empty() || name.starts_with('/') || name.contains('\\') || name.contains('\0') {
        return None;
    }
    let mut path = folder.to_path_buf();
    for part in name.split('/') {
        if part.is_empty() || part == "." || part == ".." || part.starts_with('.') {
            return None;
        }
        path.push(part);
        let metadata = path.symlink_metadata().ok()?;
        if metadata.file_type().is_symlink() {
            return None;
        }
    }
    path.symlink_metadata().ok().filter(|m| m.is_file()).map(|_| path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_folders_own_regular_files_are_listed_and_given() {
        let dir = std::env::temp_dir().join(format!("ling-web-ask-files-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("out/deeper/deepest")).unwrap();
        std::fs::write(dir.join("report.md"), "# r").unwrap();
        std::fs::write(dir.join("out/table.csv"), "a,b").unwrap();
        std::fs::write(dir.join("out/deeper/deepest/far.txt"), "x").unwrap();
        std::fs::write(dir.join(".hidden"), "x").unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink("/etc/passwd", dir.join("passwd")).unwrap();
        let names: Vec<String> = changed_since(&dir, 0).iter().map(|f| f["name"].as_str().unwrap().to_string()).collect();
        assert!(names.contains(&"report.md".to_string()) && names.contains(&"out/table.csv".to_string()), "{names:?}");
        assert!(!names.iter().any(|n| n.contains("hidden") || n == "passwd" || n.contains("far.txt")), "{names:?}");
        assert!(changed_since(&dir, u64::MAX).is_empty());
        assert_eq!(file_in(&dir, "out/table.csv"), Some(dir.join("out/table.csv")));
        for refused in ["passwd", "../etc/passwd", "/etc/passwd", "out/../report.md", ".hidden", "out", "", "a\\b"] {
            assert_eq!(file_in(&dir, refused), None, "{refused}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
