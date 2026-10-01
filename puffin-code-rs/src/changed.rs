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
    /// The submodules that are indexed (§4.3). The superproject's git sees each as one entry, so
    /// their files are listed, and their changes found, by asking git inside each of them.
    included: Vec<String>,
    status: Option<BTreeSet<String>>,
    diffs: HashMap<String, Option<BTreeSet<String>>>,
    all: Option<BTreeSet<String>>,
}

impl<'a> GitView<'a> {
    /// The view for `repo`, with the submodules §4.3 includes (decided here, from the settings).
    pub fn new(repo: &'a Repo) -> Self {
        let settings = crate::config::Settings::load(&repo.root);
        let included = crate::submodules::included(&crate::submodules::evaluate(repo, &settings));
        GitView::with_included(repo, included)
    }

    /// The view when the caller has already decided which submodules are indexed.
    pub fn with_included(repo: &'a Repo, included: Vec<String>) -> Self {
        GitView { repo, included, status: None, diffs: HashMap::new(), all: None }
    }

    /// Every file of an included submodule, with the submodule's path as its prefix.
    fn submodule_files(&self, submodule: &str) -> Vec<String> {
        git_z(&self.repo.root.join(submodule), &["ls-files", "-co", "--exclude-standard", "-z"])
            .unwrap_or_default()
            .into_iter()
            .map(|file| format!("{submodule}/{file}"))
            .collect()
    }

    /// Adds what an included submodule contributes to a set of superproject paths. Where the set
    /// names the submodule itself (its commit moved, or its checkout is dirty) or a submodule it
    /// is nested in, every file of it becomes a candidate: each is then confirmed against the
    /// layer's hashes, so this over-asks and never under-reports.
    fn expand_submodules(&self, paths: &mut BTreeSet<String>) {
        for submodule in &self.included {
            let moved = paths.iter().any(|path| path == submodule || submodule.starts_with(&format!("{path}/")));
            if moved {
                paths.extend(self.submodule_files(submodule));
            }
        }
        for submodule in &self.included {
            paths.remove(submodule);
        }
    }

    /// Paths `git status` reports: modified, staged, deleted, renamed (both sides) and untracked.
    pub fn status(&mut self) -> &BTreeSet<String> {
        if self.status.is_none() {
            let mut paths = BTreeSet::new();
            if self.repo.is_git {
                paths.extend(status_paths(&self.repo.root));
                self.expand_submodules(&mut paths);
                // The superproject can be told to ignore a submodule's dirt (`ignore = dirty`), so
                // each included submodule is asked itself as well.
                for submodule in &self.included {
                    paths.extend(status_paths(&self.repo.root.join(submodule)).into_iter().map(|path| format!("{submodule}/{path}")));
                }
                for submodule in &self.included {
                    paths.remove(submodule);
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
                git_z(&self.repo.root, &["diff", "--name-only", "-z", "--no-renames", commit, "HEAD", "--"]).ok().map(|v| {
                    let mut paths: BTreeSet<String> = v.into_iter().collect();
                    self.expand_submodules(&mut paths);
                    paths
                })
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
            let mut files: BTreeSet<String> = if self.repo.is_git {
                git_z(&self.repo.root, &["ls-files", "-co", "--exclude-standard", "-z"]).map(|v| v.into_iter().collect()).unwrap_or_default()
            } else {
                BTreeSet::new()
            };
            for submodule in &self.included {
                files.remove(submodule);
                files.extend(self.submodule_files(submodule));
            }
            self.all = Some(files);
        }
        self.all.as_ref().unwrap()
    }
}

/// The paths `git status` reports in `dir`: modified, staged, deleted, renamed (both sides) and
/// untracked.
fn status_paths(dir: &Path) -> BTreeSet<String> {
    let mut paths = BTreeSet::new();
    if let Ok(entries) = git_z(dir, &["status", "--porcelain", "-z", "-uall"]) {
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
    paths
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
