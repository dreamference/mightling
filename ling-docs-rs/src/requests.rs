//! The re-index request queue: `docs/requests` (spec §6).
//!
//! A query inside the agent's sandbox never indexes; `ling docs reindex` and `add` (and a query
//! that finds a collection with no index yet, where it can write) append a line here, and the
//! session process outside the sandbox drains it. The file may be written from inside the
//! sandbox, so its content is untrusted: a line is `index`, `index <name>` or `rebuild <name>`
//! with a valid collection name, and anything else is ignored.

use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::Result;

/// What a line asks for.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Request {
    /// Every collection's change scan.
    All,
    /// One collection's.
    One(String),
    /// One collection's database dropped and built again.
    Rebuild(String),
}

impl Request {
    fn line(&self) -> String {
        match self {
            Request::All => "index".into(),
            Request::One(name) => format!("index {name}"),
            Request::Rebuild(name) => format!("rebuild {name}"),
        }
    }

    fn parse(line: &str) -> Option<Request> {
        let mut words = line.split(' ');
        let verb = words.next()?;
        let name = words.next();
        if words.next().is_some() {
            return None;
        }
        let valid = |n: &str| crate::collections::validate_name(n).is_ok();
        match (verb, name) {
            ("index", None) => Some(Request::All),
            ("index", Some(n)) if valid(n) => Some(Request::One(n.to_string())),
            ("rebuild", Some(n)) if valid(n) => Some(Request::Rebuild(n.to_string())),
            _ => None,
        }
    }
}

pub fn path(docs_dir: &Path) -> PathBuf {
    docs_dir.join("requests")
}

/// Appends a request. Fails where the directory is read-only (the agent's read-only sandbox).
pub fn append(docs_dir: &Path, request: &Request) -> Result<()> {
    crate::store::create_private_dir(docs_dir)?;
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path(docs_dir))?;
    writeln!(file, "{}", request.line())?;
    Ok(())
}

/// Takes every pending request, without duplicates, and empties the queue. A file that is not a
/// regular file (a symlink planted to make the session read elsewhere) or is over 1 MiB yields
/// nothing.
pub fn drain(docs_dir: &Path) -> Vec<Request> {
    let file = path(docs_dir);
    let Ok(meta) = std::fs::symlink_metadata(&file) else { return Vec::new() };
    if !meta.file_type().is_file() || meta.len() > 1 << 20 {
        let _ = std::fs::remove_file(&file);
        return Vec::new();
    }
    let taken = docs_dir.join(format!("requests.{}", std::process::id()));
    if std::fs::rename(&file, &taken).is_err() {
        return Vec::new();
    }
    let text = std::fs::read_to_string(&taken).unwrap_or_default();
    let _ = std::fs::remove_file(&taken);
    let mut out: Vec<Request> = text.lines().filter_map(Request::parse).collect();
    out.sort();
    out.dedup();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_fixed_words_and_valid_names_count() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(path(dir.path()), "rm -rf /\nindex ../../etc\nindex; curl x\nrebuild\n").unwrap();
        assert!(drain(dir.path()).is_empty());
        append(dir.path(), &Request::One("documents".into())).unwrap();
        append(dir.path(), &Request::One("documents".into())).unwrap();
        append(dir.path(), &Request::Rebuild("notes".into())).unwrap();
        append(dir.path(), &Request::All).unwrap();
        assert_eq!(drain(dir.path()), vec![Request::All, Request::One("documents".into()), Request::Rebuild("notes".into())]);
        assert!(drain(dir.path()).is_empty());
    }

    #[test]
    fn a_planted_symlink_is_not_followed() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("elsewhere"), "index\n").unwrap();
        std::os::unix::fs::symlink(dir.path().join("elsewhere"), path(dir.path())).unwrap();
        assert!(drain(dir.path()).is_empty());
        assert!(dir.path().join("elsewhere").exists());
    }
}
