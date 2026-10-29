//! Repository discovery and the paths derived from it: a linked worktree, a submodule, a
//! directory that is no repository, and the environment overrides.

mod common;

use std::path::Path;
use std::sync::Mutex;

use ling_code::paths::Repo;

// The overrides are process-wide, so the tests that read them take turns.
static ENV: Mutex<()> = Mutex::new(());

fn locked() -> std::sync::MutexGuard<'static, ()> {
    let guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
    std::env::remove_var("MIGHTLING_CODE_ROOT");
    std::env::remove_var("MIGHTLING_CODE_STATE_DIR");
    guard
}

fn committed_repo(dir: &Path, name: &str) -> std::path::PathBuf {
    let root = dir.join(name);
    std::fs::create_dir_all(&root).unwrap();
    common::git(&root, &["init", "-q"]);
    std::fs::write(root.join("a.txt"), "a\n").unwrap();
    common::git(&root, &["add", "-A"]);
    common::git(&root, &["commit", "-qm", "init"]);
    root.canonicalize().unwrap()
}

#[test]
fn a_subdirectory_finds_the_repository_root() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let root = committed_repo(dir.path(), "repo");
    std::fs::create_dir_all(root.join("deep/er")).unwrap();
    let repo = Repo::discover(&root.join("deep/er")).unwrap();
    assert!(repo.is_git);
    assert_eq!(repo.root, root);
    assert_eq!(repo.main_root, root);
    assert_eq!(repo.state_dir(), root.join(".dreamference"));
    assert_eq!(repo.scip_dir(), root.join(".dreamference/scip"));
}

#[test]
fn a_linked_worktree_keeps_its_state_in_the_main_checkout() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let main = committed_repo(dir.path(), "main");
    let linked = dir.path().join("linked");
    common::git(&main, &["worktree", "add", "-q", linked.to_str().unwrap()]);
    let linked = linked.canonicalize().unwrap();
    let repo = Repo::discover(&linked).unwrap();
    assert_eq!(repo.root, linked);
    assert_eq!(repo.main_root, main);
    assert_eq!(repo.state_dir(), main.join(".dreamference"));
}

#[test]
fn a_submodule_is_its_own_repository_with_its_own_state() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let inner = committed_repo(dir.path(), "inner");
    let outer = committed_repo(dir.path(), "outer");
    common::git(
        &outer,
        &[
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            inner.to_str().unwrap(),
            "vendor/inner",
        ],
    );
    let sub = outer.join("vendor/inner");
    let repo = Repo::discover(&sub).unwrap();
    assert_eq!(repo.root, sub.canonicalize().unwrap());
    assert_eq!(repo.main_root, repo.root, "not the superproject");
    let outer_repo = Repo::discover(&outer).unwrap();
    assert_eq!(outer_repo.submodules(), vec!["vendor/inner".to_string()]);
    assert!(repo.submodules().is_empty());
}

#[test]
fn a_directory_that_is_no_repository_is_its_own_root_and_has_no_submodules() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let repo = Repo::discover(dir.path()).unwrap();
    assert!(!repo.is_git);
    assert_eq!(repo.root, dir.path());
    assert_eq!(repo.main_root, dir.path());
    assert!(repo.submodules().is_empty());
    assert_eq!(repo.head(), None);
}

#[test]
fn the_environment_overrides_name_the_root_and_the_state_directory() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let root = committed_repo(dir.path(), "repo");
    let elsewhere = tempfile::tempdir().unwrap();
    std::env::set_var("MIGHTLING_CODE_ROOT", &root);
    std::env::set_var("MIGHTLING_CODE_STATE_DIR", elsewhere.path().join("state"));
    let repo = Repo::discover(elsewhere.path()).unwrap();
    assert!(repo.is_git);
    assert_eq!(repo.root, root);
    assert_eq!(repo.state_dir(), elsewhere.path().join("state"));
    assert_eq!(repo.scip_dir(), elsewhere.path().join("state/scip"));
    std::env::remove_var("MIGHTLING_CODE_ROOT");
    std::env::remove_var("MIGHTLING_CODE_STATE_DIR");
}

#[test]
fn a_path_outside_the_repository_is_not_made_relative_to_it() {
    let _g = locked();
    let dir = tempfile::tempdir().unwrap();
    let root = committed_repo(dir.path(), "repo");
    let repo = Repo::discover(&root).unwrap();
    assert_eq!(repo.abs("src/a.rs"), root.join("src/a.rs"));
    // `abs` only joins: callers are what keep `..` and absolute paths out.
    assert_eq!(repo.abs("/etc/passwd"), Path::new("/etc/passwd"));
}
