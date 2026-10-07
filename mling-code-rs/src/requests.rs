//! The re-index request queue: `<state>/code_index.requests` (spec §4).
//!
//! A query inside Codex's sandbox cannot start an indexer, so it appends one line here when it can
//! write the directory, and the session process, outside the sandbox, drains it. The file is
//! written from inside the sandbox, so its content is untrusted: a line is one of two fixed words
//! and anything else is ignored.

use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::Result;

/// What a line may ask for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Request {
    /// The universal layer and the static exact layers.
    Index,
    /// Also the executing exact layers, where the repository is trusted and admission allows.
    IndexExact,
}

impl Request {
    fn word(self) -> &'static str {
        match self {
            Request::Index => "index",
            Request::IndexExact => "index-exact",
        }
    }
}

pub fn path(state_dir: &Path) -> PathBuf {
    state_dir.join("code_index.requests")
}

/// Appends a request line. Fails where the directory is read-only (the `read-only` sandbox).
pub fn append(state_dir: &Path, request: Request) -> Result<()> {
    std::fs::create_dir_all(state_dir)?;
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path(state_dir))?;
    writeln!(file, "{}", request.word())?;
    Ok(())
}

/// Takes every pending request, coalesced to the strongest one, and empties the queue.
///
/// Only the exact words count; a file of any other content, or one that is not a regular file
/// (a symlink planted to make the session read elsewhere), yields nothing.
pub fn drain(state_dir: &Path) -> Option<Request> {
    let file = path(state_dir);
    let meta = std::fs::symlink_metadata(&file).ok()?;
    if !meta.file_type().is_file() || meta.len() > 1 << 20 {
        let _ = std::fs::remove_file(&file);
        return None;
    }
    let taken = state_dir.join(format!("code_index.requests.{}", std::process::id()));
    std::fs::rename(&file, &taken).ok()?;
    let text = std::fs::read_to_string(&taken).unwrap_or_default();
    let _ = std::fs::remove_file(&taken);
    text.lines()
        .filter_map(|line| match line {
            "index" => Some(Request::Index),
            "index-exact" => Some(Request::IndexExact),
            _ => None,
        })
        .max()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_fixed_words_count() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(path(dir.path()), "rm -rf /\nindex; curl x\n").unwrap();
        assert_eq!(drain(dir.path()), None);
        for _ in 0..10 {
            append(dir.path(), Request::Index).unwrap();
        }
        assert_eq!(drain(dir.path()), Some(Request::Index));
        assert_eq!(drain(dir.path()), None);
        append(dir.path(), Request::Index).unwrap();
        append(dir.path(), Request::IndexExact).unwrap();
        assert_eq!(drain(dir.path()), Some(Request::IndexExact));
    }
}
