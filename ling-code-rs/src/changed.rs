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
use crate::paths::{git, git_z, Repo};

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

    /// The files of an included submodule that differ between the commit its parent recorded at
    /// `since` and what is checked out now, or None when that cannot be told (the old commit is
    /// not in the submodule's clone, or the submodule is nested and `since` names a commit of the
    /// superproject, whose parent's commit at that point is not known here).
    fn submodule_diff(&self, submodule: &str, since: &str) -> Option<Vec<String>> {
        let parent = self.included.iter().filter(|p| submodule.starts_with(&format!("{p}/"))).max_by_key(|p| p.len());
        let (parent_dir, rel) = match parent {
            Some(parent) => (self.repo.root.join(parent), &submodule[parent.len() + 1..]),
            None => (self.repo.root.clone(), submodule),
        };
        if parent.is_some() && since != "HEAD" {
            return None;
        }
        // `160000 commit <sha>\t<path>`
        let tree = git(&parent_dir, &["ls-tree", since, "--", rel]).ok()?;
        let dir = self.repo.root.join(submodule);
        let head = git(&dir, &["rev-parse", "HEAD"]).ok()?;
        let Some(old) = tree.split_whitespace().nth(2) else {
            // Not a submodule at that commit: everything in it is new since.
            return Some(self.submodule_files(submodule));
        };
        if head.trim() == old {
            return Some(Vec::new());
        }
        let files = git_z(&dir, &["diff", "--name-only", "-z", "--no-renames", old, "HEAD", "--"]).ok()?;
        Some(files.into_iter().map(|file| format!("{submodule}/{file}")).collect())
    }

    /// Replaces each included submodule's own entry in a set of superproject paths (a directory,
    /// which names no file) with the files that changed inside it since `since`. Where that
    /// cannot be told, every file of the submodule becomes a candidate: each is then confirmed
    /// against the layer's hashes, so this over-asks and never under-reports.
    fn expand_submodules(&self, paths: &mut BTreeSet<String>, since: &str) {
        let mut unchanged: Vec<&String> = Vec::new();
        for submodule in &self.included {
            let nested_in_unchanged = unchanged.iter().any(|p| submodule.starts_with(&format!("{p}/")));
            // Under a parent that has not moved, a nested submodule's recorded commit has not
            // either: it is compared with what its parent records now.
            let changed = self.submodule_diff(submodule, if nested_in_unchanged { "HEAD" } else { since });
            match changed {
                Some(files) => {
                    if files.is_empty() {
                        unchanged.push(submodule);
                    }
                    paths.extend(files);
                }
                None => paths.extend(self.submodule_files(submodule)),
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
                // A checkout moved off the commit its parent records, and each submodule's own
                // dirt: the superproject reports both as one entry, or (with `ignore = dirty`)
                // not at all, so each included submodule is asked itself.
                self.expand_submodules(&mut paths, "HEAD");
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
                    self.expand_submodules(&mut paths, commit);
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

/// Whether `path` is `prefix` itself or lies under it, at a directory boundary (`src` holds
/// `src/a.rs`, not `src2/a.rs`); an empty prefix holds everything.
fn in_scope(path: &str, prefix: &str) -> bool {
    let dir = prefix.trim_end_matches('/');
    dir.is_empty() || path == dir || path.strip_prefix(dir).is_some_and(|rest| rest.starts_with('/'))
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
        if scope.map(|prefix| !in_scope(&path, prefix)).unwrap_or(false) || exclude(&path) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_scope_without_a_trailing_slash_stops_at_the_directory_boundary() {
        let dir = tempfile::tempdir().unwrap();
        let repo = test_repo(dir.path(), false);
        for rel in ["src/a.rs", "src2/b.rs"] {
            std::fs::create_dir_all(dir.path().join(rel).parent().unwrap()).unwrap();
            std::fs::write(dir.path().join(rel), "fn a() {}\n").unwrap();
        }
        let mut stamps = HashMap::new();
        for rel in ["src/a.rs", "src2/b.rs"] {
            stamps.insert(rel.to_string(), FileStamp::of(&repo.abs(rel)).unwrap());
            std::fs::write(repo.abs(rel), "fn changed() { 1 }\n").unwrap();
        }
        let mut git = GitView::new(&repo);
        let cs = layer_changes(&repo, &mut git, None, &[], &stamps, Some("src"), &|_| false);
        assert_eq!(cs.changed, BTreeSet::from(["src/a.rs".to_string()]));
    }

    fn test_repo(dir: &Path, is_git: bool) -> Repo {
        Repo {
            root: dir.to_path_buf(),
            main_root: dir.to_path_buf(),
            is_git,
        }
    }

    #[test]
    fn stat_layer_changes_detects_modified_deleted_scoped_and_excluded_files() {
        let dir = tempfile::tempdir().unwrap();
        let repo = test_repo(dir.path(), false);
        std::fs::create_dir_all(dir.path().join("src")).unwrap();
        std::fs::create_dir_all(dir.path().join("other")).unwrap();
        let keep = dir.path().join("src/keep.rs");
        let edit = dir.path().join("src/edit.rs");
        let gone = dir.path().join("src/gone.rs");
        let out = dir.path().join("other/out.rs");
        for (p, body) in [(&keep, "fn a() {}\n"), (&edit, "fn b() {}\n"), (&gone, "fn c() {}\n"), (&out, "fn d() {}\n")] {
            std::fs::write(p, body).unwrap();
        }
        let mut stamps = HashMap::new();
        for rel in ["src/keep.rs", "src/edit.rs", "src/gone.rs", "other/out.rs"] {
            stamps.insert(rel.to_string(), FileStamp::of(&repo.abs(rel)).unwrap());
        }
        std::fs::write(&edit, "fn b() { 1 }\n").unwrap();
        std::fs::remove_file(&gone).unwrap();
        std::fs::write(&out, "fn d() { 2 }\n").unwrap();

        let mut git = GitView::new(&repo);
        let cs = layer_changes(&repo, &mut git, None, &[], &stamps, Some("src/"), &|p| p.ends_with("ignored.rs"));
        assert_eq!(cs.method, Method::Stat);
        assert!(cs.is_fresh("src/keep.rs"));
        assert!(!cs.is_fresh("src/edit.rs"));
        assert!(!cs.is_fresh("src/gone.rs"));
        assert_eq!(cs.changed, BTreeSet::from(["src/edit.rs".to_string()]));
        assert_eq!(cs.deleted, BTreeSet::from(["src/gone.rs".to_string()]));
    }

    #[test]
    fn is_same_falls_back_to_sha256_when_mtime_differs_but_size_matches() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("a.rs");
        std::fs::write(&file, "const X: i32 = 1;\n").unwrap();
        let mut stamp = FileStamp::of(&file).unwrap();
        let meta = std::fs::metadata(&file).unwrap();
        assert!(is_same(&file, &meta, Some(&stamp)));

        // Different mtime, identical content and size: still matches via SHA-256 fallback.
        stamp.mtime_ns = stamp.mtime_ns.wrapping_add(1_000_000);
        assert!(is_same(&file, &meta, Some(&stamp)));

        // Different content of the same byte length with shifted mtime: detected by SHA-256.
        std::fs::write(&file, "const X: i32 = 2;\n").unwrap();
        let meta2 = std::fs::metadata(&file).unwrap();
        stamp.mtime_ns = mtime_ns(&meta2).wrapping_add(1_000_000);
        assert!(!is_same(&file, &meta2, Some(&stamp)));
        assert!(!is_same(&file, &meta2, None));
    }

    #[test]
    fn git_layer_changes_uses_git_diff_status_and_dirty_list() {
        let dir = tempfile::tempdir().unwrap();
        let run = |args: &[&str]| {
            assert!(std::process::Command::new("git").current_dir(dir.path()).args(args).status().unwrap().success());
        };
        run(&["init", "-q"]);
        run(&["config", "user.email", "test@example.local"]);
        run(&["config", "user.name", "test"]);
        std::fs::write(dir.path().join("a.rs"), "fn a() {}\n").unwrap();
        std::fs::write(dir.path().join("b.rs"), "fn b() {}\n").unwrap();
        run(&["add", "a.rs", "b.rs"]);
        run(&["commit", "-q", "-m", "init"]);

        let repo = Repo { root: dir.path().to_path_buf(), main_root: dir.path().to_path_buf(), is_git: true };
        let mut stamps = HashMap::new();
        stamps.insert("a.rs".into(), FileStamp::of(&repo.abs("a.rs")).unwrap());
        stamps.insert("b.rs".into(), FileStamp::of(&repo.abs("b.rs")).unwrap());

        let commit = git(&repo.root, &["rev-parse", "HEAD"]).unwrap().trim().to_string();
        let mut git_view = GitView::with_included(&repo, Vec::new());
        let fresh = layer_changes(&repo, &mut git_view, Some(&commit), &[], &stamps, None, &|_| false);
        assert_eq!(fresh.method, Method::Git);
        assert!(fresh.is_fresh("a.rs") && fresh.is_fresh("b.rs"));

        // Modify a tracked file and add an untracked file.
        std::fs::write(dir.path().join("a.rs"), "fn a() { 42 }\n").unwrap();
        std::fs::write(dir.path().join("new.rs"), "fn n() {}\n").unwrap();
        let mut git_view = GitView::with_included(&repo, Vec::new());
        let dirty = layer_changes(&repo, &mut git_view, Some(&commit), &[], &stamps, None, &|_| false);
        assert_eq!(dirty.method, Method::Git);
        assert_eq!(dirty.changed, BTreeSet::from(["a.rs".into(), "new.rs".into()]));
        assert!(dirty.deleted.is_empty());

        // Unknown commit falls back to Method::Stat.
        let fallback = layer_changes(&repo, &mut git_view, Some("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"), &[], &stamps, None, &|_| false);
        assert_eq!(fallback.method, Method::Stat);
    }
}
