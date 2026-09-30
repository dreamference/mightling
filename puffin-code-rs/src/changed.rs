//! Freshness, decided for the repository rather than for an answer (spec §7.3).
//!
//! A snapshot goes stale in two ways, and only one is visible from its own rows: a file the answer
//! names has changed, or a file the answer does not name now references the symbol. So each layer's
//! changed set is computed from git (the files that differ from the snapshot's commit, plus the
//! working tree's changes, plus the snapshot's own dirty files) and each candidate is confirmed
//! against the layer's recorded hashes, by `(mtime, size)` first and by content only if either
//! differs. When git cannot answer, every file is checked by `stat` instead.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use crate::manifest::{mtime_ns, sha256_file, FileStamp};
use crate::paths::{git_z, Repo};

/// How a changed set was found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    /// `git diff <snapshot> HEAD` plus `git status`, confirmed against the hashes.
    Git,
    /// Every file checked by `stat` against the hashes: the snapshot's commit is unknown.
    Stat,
}

/// The files that differ from one layer's snapshot.
#[derive(Debug, Clone)]
pub struct ChangeSet {
    /// Present on disk and different from the snapshot, or new since it.
    pub changed: BTreeSet<String>,
    /// In the snapshot, gone from disk.
    pub deleted: BTreeSet<String>,
    pub method: Method,
}

impl ChangeSet {
    pub fn is_fresh(&self, path: &str) -> bool {
        !self.changed.contains(path) && !self.deleted.contains(path)
    }
}

/// Git's view of the working tree, computed at most once per query.
pub struct GitView<'a> {
    repo: &'a Repo,
    status: Option<BTreeSet<String>>,
    diffs: HashMap<String, Option<BTreeSet<String>>>,
    all: Option<BTreeSet<String>>,
}

impl<'a> GitView<'a> {
    pub fn new(repo: &'a Repo) -> Self {
        GitView { repo, status: None, diffs: HashMap::new(), all: None }
    }

    /// Paths `git status` reports: modified, staged, deleted, renamed (both sides) and untracked.
    pub fn status(&mut self) -> &BTreeSet<String> {
        if self.status.is_none() {
            let mut paths = BTreeSet::new();
            if self.repo.is_git {
                if let Ok(entries) = git_z(&self.repo.root, &["status", "--porcelain", "-z", "-uall"]) {
                    let mut iter = entries.into_iter();
                    while let Some(entry) = iter.next() {
                        if entry.len() < 4 {
                            continue;
                        }
                        let code = &entry[..2];
                        paths.insert(entry[3..].to_string());
                        // A rename or copy is followed by its source path.
                        if code.contains('R') || code.contains('C') {
                            if let Some(source) = iter.next() {
                                paths.insert(source);
                            }
                        }
                    }
                }
            }
            self.status = Some(paths);
        }
        self.status.as_ref().unwrap()
    }

    /// Paths that differ between `commit` and HEAD, or `None` when the commit is unknown here (a
    /// rebase and a garbage collection can remove it).
    pub fn diff(&mut self, commit: &str) -> Option<&BTreeSet<String>> {
        if !self.diffs.contains_key(commit) {
            let paths = if self.repo.is_git {
                git_z(&self.repo.root, &["diff", "--name-only", "-z", "--no-renames", commit, "HEAD", "--"])
                    .ok()
                    .map(|v| v.into_iter().collect())
            } else {
                None
            };
            self.diffs.insert(commit.to_string(), paths);
        }
        self.diffs.get(commit).unwrap().as_ref()
    }

    /// Every tracked and untracked-but-not-ignored file (`git ls-files -co --exclude-standard`).
    pub fn all_files(&mut self) -> &BTreeSet<String> {
        if self.all.is_none() {
            let files = if self.repo.is_git {
                git_z(&self.repo.root, &["ls-files", "-co", "--exclude-standard", "-z"]).map(|v| v.into_iter().collect()).unwrap_or_default()
            } else {
                BTreeSet::new()
            };
            self.all = Some(files);
        }
        self.all.as_ref().unwrap()
    }
}

/// One layer's changed set.
///
/// Args:
/// - `commit` and `dirty`: the snapshot's commit and the files that were dirty when it was taken;
/// - `stamps`: the layer's recorded hashes, repository-relative;
/// - `scope`: when set, only paths under this prefix belong to the layer (a SCIP root);
/// - `exclude`: paths the layer deliberately does not cover (not indexed), which are not "changed".
pub fn layer_changes(
    repo: &Repo,
    git: &mut GitView<'_>,
    commit: Option<&str>,
    dirty: &[String],
    stamps: &HashMap<String, FileStamp>,
    scope: Option<&str>,
    exclude: &dyn Fn(&str) -> bool,
) -> ChangeSet {
    let mut candidates: BTreeSet<String> = BTreeSet::new();
    let method = match commit.and_then(|c| git.diff(c).cloned()) {
        Some(diff) => {
            candidates.extend(diff);
            candidates.extend(git.status().iter().cloned());
            candidates.extend(dirty.iter().cloned());
            Method::Git
        }
        None => {
            candidates.extend(git.all_files().iter().cloned());
            candidates.extend(stamps.keys().cloned());
            Method::Stat
        }
    };
    let mut changed = BTreeSet::new();
    let mut deleted = BTreeSet::new();
    for path in candidates {
        if scope.map(|prefix| !path.starts_with(prefix)).unwrap_or(false) || exclude(&path) {
            continue;
        }
        match std::fs::metadata(repo.abs(&path)) {
            Err(_) => {
                if stamps.contains_key(&path) {
                    deleted.insert(path);
                }
            }
            Ok(meta) if !meta.is_file() => {}
            Ok(meta) => {
                if !is_same(&repo.abs(&path), &meta, stamps.get(&path)) {
                    changed.insert(path);
                }
            }
        }
    }
    ChangeSet { changed, deleted, method }
}

/// Whether a file on disk still matches its stamp: `(mtime, size)` first, content only if either
/// differs (a checkout rewrites mtimes without changing content).
fn is_same(path: &Path, meta: &std::fs::Metadata, stamp: Option<&FileStamp>) -> bool {
    let Some(stamp) = stamp else { return false };
    if meta.len() != stamp.size {
        return false;
    }
    if mtime_ns(meta) == stamp.mtime_ns {
        return true;
    }
    sha256_file(path).as_deref() == Some(stamp.sha256.as_str())
}
