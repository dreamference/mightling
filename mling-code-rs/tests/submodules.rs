//! The submodule policy (spec §4.3) on real git repositories: yours is indexed, a fork under your
//! namespace is not, the user's choice wins and lives outside the workspace, and every answer
//! says what was left out. Nothing here touches the network: remotes are only names.

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;

use common::{fixture, git, Fixture};
use mling_code::changed::GitView;
use mling_code::config::Settings;
use mling_code::index::host::Kind;
use mling_code::index::plan;
use mling_code::paths::Repo;
use mling_code::submodules::{self, Choice, Namespace, Reason};

/// Commits as someone else (an upstream author).
fn git_as(dir: &Path, email: &str, args: &[&str]) {
    let out = Command::new("git")
        .args(["-c", "user.name=u", "-c", &format!("user.email={email}"), "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
}

/// A repository of its own, to be added as a submodule: `commits` commits by `email`.
fn library(base: &Path, name: &str, email: &str, files: &[(&str, &str)], commits: usize) -> PathBuf {
    let dir = base.join(format!("origin-{name}"));
    for (path, content) in files {
        let path = dir.join(path);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }
    git_as(&dir, email, &["init", "-q"]);
    git_as(&dir, email, &["add", "-A"]);
    git_as(&dir, email, &["commit", "-qm", "first"]);
    for n in 1..commits {
        git_as(&dir, email, &["commit", "-q", "--allow-empty", "-m", &format!("commit {n}")]);
    }
    dir
}

/// Adds `source` as the submodule `path` of `root`, recording `url` as its remote.
fn add_submodule(root: &Path, source: &Path, path: &str, url: Option<&str>) {
    git(root, &["-c", "protocol.file.allow=always", "submodule", "add", "-q", &source.to_string_lossy(), path]);
    if let Some(url) = url {
        // What `git submodule init` writes, and what a user's own override looks like.
        git(root, &["config", &format!("submodule.{path}.url"), url]);
    }
}

/// The fixture repository as `acme/super`, with one submodule of ours and one vendored fork.
/// The fixture's own commits are by `t@localhost`.
fn with_submodules() -> Fixture {
    let f = fixture();
    let root = f.repo.root.clone();
    git(&root, &["remote", "add", "origin", "https://example.com/acme/super.git"]);
    let ours = library(f.dir.path(), "ours", "t@localhost", &[("tools/helper.py", "def helper():\n    return 1\n"), ("notes.txt", "notes\n")], 3);
    let vendored = library(f.dir.path(), "vendored", "dev@upstream.example", &[("lib/vendored.py", "def vendored():\n    return 2\n")], 3);
    add_submodule(&root, &ours, "ours", Some("git@example.com:Acme/ours.git"));
    add_submodule(&root, &vendored, "vendored", Some("https://example.com/acme/vendored-fork.git"));
    f
}

fn decisions(repo: &Repo) -> Vec<submodules::Submodule> {
    submodules::evaluate_with(repo, 5000, &[])
}

#[test]
fn yours_is_indexed_and_a_fork_under_your_namespace_is_not() {
    let f = with_submodules();
    let all = decisions(&f.repo);
    let by_path = |path: &str| all.iter().find(|s| s.path == path).unwrap();
    let ours = by_path("ours");
    assert!(ours.indexed, "{ours:?}");
    assert_eq!(ours.reason, Reason::Yours);
    assert_eq!((ours.namespace, ours.ours, ours.commits, ours.files), (Namespace::Same, 3, 3, 2));
    assert!(ours.inherits_trust);
    // Same namespace, upstream's authors: the URL alone would have indexed it.
    let vendored = by_path("vendored");
    assert!(!vendored.indexed);
    assert_eq!(vendored.reason, Reason::ThirdParty);
    assert_eq!((vendored.namespace, vendored.ours, vendored.commits), (Namespace::Same, 0, 3));
    assert!(!vendored.inherits_trust);
    assert_eq!(submodules::not_indexed_line(&all).as_deref(), Some("vendored/ (third-party)"));
}

#[test]
fn another_organisation_is_not_indexed_whoever_wrote_it() {
    let f = fixture();
    let root = f.repo.root.clone();
    git(&root, &["remote", "add", "origin", "https://example.com/acme/super.git"]);
    let lib = library(f.dir.path(), "elsewhere", "t@localhost", &[("a.py", "x = 1\n")], 2);
    add_submodule(&root, &lib, "elsewhere", Some("https://example.com/other-org/elsewhere.git"));
    let all = decisions(&f.repo);
    assert_eq!((all[0].indexed, all[0].reason.clone(), all[0].namespace), (false, Reason::OtherOrganisation, Namespace::Different));
    assert!(all[0].evidence().contains("namespace example.com/other-org, yours is example.com/acme"), "{}", all[0].evidence());
}

#[test]
fn without_a_remote_authorship_alone_decides_and_trust_is_not_inherited() {
    let f = fixture();
    let root = f.repo.root.clone();
    // No remote on the superproject: the namespace test is unavailable.
    let mine = library(f.dir.path(), "mine", "t@localhost", &[("a.py", "x = 1\n")], 2);
    let theirs = library(f.dir.path(), "theirs", "dev@upstream.example", &[("b.py", "y = 1\n")], 2);
    add_submodule(&root, &mine, "mine", None);
    add_submodule(&root, &theirs, "theirs", None);
    let all = decisions(&f.repo);
    let by_path = |path: &str| all.iter().find(|s| s.path == path).unwrap();
    assert_eq!((by_path("mine").indexed, by_path("mine").reason.clone()), (true, Reason::YoursByAuthorship));
    assert!(!by_path("mine").inherits_trust, "commit authorship is not authentication");
    assert_eq!((by_path("theirs").indexed, by_path("theirs").reason.clone()), (false, Reason::ThirdParty));
}

#[test]
fn the_size_guard_stops_automatic_inclusion_only() {
    let f = with_submodules();
    let all = submodules::evaluate_with(&f.repo, 1, &[]);
    let ours = all.iter().find(|s| s.path == "ours").unwrap();
    assert_eq!((ours.indexed, ours.reason.clone()), (false, Reason::TooLarge(2)));
    // Asked for by name, the guard does not apply.
    let all = submodules::evaluate_with(&f.repo, 1, &[("ours".to_string(), Choice::Include)]);
    let ours = all.iter().find(|s| s.path == "ours").unwrap();
    assert_eq!((ours.indexed, ours.reason.clone()), (true, Reason::IncludedByYou));
}

#[test]
fn the_size_guard_is_not_read_from_a_file_in_the_repository() {
    let f = with_submodules();
    f.write("dreamference.toml", "code_index_submodule_max_files = 1\n");
    let inside = Settings::load(&f.repo.root);
    assert_eq!(inside.submodule_max_files, 1, "the key is read");
    let ours = |settings: &Settings| submodules::evaluate(&f.repo, settings).into_iter().find(|s| s.path == "ours").unwrap();
    assert!(ours(&inside).indexed, "but a clone or the agent could have written it, so it is not honoured");
    // The same key from a file outside the repository is.
    let outside = f.dir.path().join("config.toml");
    std::fs::write(&outside, "code_index_submodule_max_files = 1\n").unwrap();
    let settings = Settings { config_path: Some(outside), ..inside };
    assert_eq!(ours(&settings).reason, Reason::TooLarge(2));
}

#[test]
fn a_nested_submodule_is_examined_only_when_its_parent_is_indexed() {
    let f = fixture();
    let root = f.repo.root.clone();
    git(&root, &["remote", "add", "origin", "https://example.com/acme/super.git"]);
    let inner = library(f.dir.path(), "inner", "t@localhost", &[("i.py", "i = 1\n")], 2);
    let outer = library(f.dir.path(), "outer", "dev@upstream.example", &[("o.py", "o = 1\n")], 2);
    git_as(&outer, "dev@upstream.example", &["-c", "protocol.file.allow=always", "submodule", "add", "-q", &inner.to_string_lossy(), "deps/inner"]);
    git_as(&outer, "dev@upstream.example", &["commit", "-qm", "add inner"]);
    add_submodule(&root, &outer, "outer", Some("https://example.com/acme/outer.git"));
    git(&root, &["-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", "--recursive"]);
    let all = decisions(&f.repo);
    let paths: Vec<&str> = all.iter().map(|s| s.path.as_str()).collect();
    assert_eq!(paths, ["outer", "outer/deps/inner"]);
    assert_eq!(all[0].reason, Reason::ThirdParty);
    // Ours by authorship, but inside a third-party library: never examined.
    assert_eq!((all[1].indexed, all[1].reason.clone()), (false, Reason::ParentNotIndexed));
    // With the parent included by the user, the nested one is tested against the superproject.
    let all = submodules::evaluate_with(&f.repo, 5000, &[("outer".to_string(), Choice::Include)]);
    assert_eq!(all[0].reason, Reason::IncludedByYou);
    assert!(all[1].reason != Reason::ParentNotIndexed, "{:?}", all[1]);
}

#[test]
fn a_submodule_that_is_not_checked_out_is_not_indexed_whatever_the_policy_says() {
    let f = with_submodules();
    git(&f.repo.root, &["add", "-A"]);
    git(&f.repo.root, &["commit", "-qm", "add submodules"]);
    // A fresh clone without `--recurse-submodules`: the submodules are declared, and empty.
    let clone = f.dir.path().join("clone");
    git(f.dir.path(), &["clone", "-q", &f.repo.root.to_string_lossy(), "clone"]);
    git(&clone, &["remote", "set-url", "origin", "https://example.com/acme/super.git"]);
    let repo = Repo { root: clone.clone(), main_root: clone, is_git: true };
    let all = submodules::evaluate_with(&repo, 5000, &[("ours".to_string(), Choice::Include)]);
    assert_eq!(all.len(), 2);
    for submodule in &all {
        assert_eq!((submodule.indexed, submodule.reason.clone()), (false, Reason::NotCheckedOut), "{}", submodule.path);
    }
    assert_eq!(submodules::not_indexed_line(&all).as_deref(), Some("ours/ (not checked out), vendored/ (not checked out)"));
}

#[test]
fn the_user_decides_and_the_choice_lives_outside_the_workspace() {
    let f = with_submodules();
    let (code, out) = f.run(&["submodules"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("ours  indexed (yours)  namespace example.com/acme; 3 of 3 commits yours; 2 files"), "{out}");
    assert!(out.contains("vendored  not indexed (third-party)"), "{out}");

    let (code, out) = f.run(&["submodules", "include", "vendored"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("vendored  indexed (included by you)"), "{out}");
    let file = f.home.join(".mightling/mling-code.toml");
    let text = std::fs::read_to_string(&file).unwrap();
    assert!(text.contains("vendored = \"include\""), "{text}");
    assert!(text.contains(&f.repo.root.canonicalize().unwrap().to_string_lossy().to_string()), "keyed by the main worktree: {text}");
    // Nothing was written into the repository but the re-index request.
    assert!(!f.repo.root.join("mling-code.toml").exists());
    let requests = std::fs::read_to_string(f.repo.state_dir().join("code_index.requests")).unwrap();
    assert!(requests.lines().all(|l| l == "index"), "{requests}");

    let (_, out) = f.run(&["submodules", "exclude", "ours"]);
    assert!(out.contains("ours  not indexed (excluded by you)"), "{out}");
    let (_, out) = f.run(&["submodules"]);
    assert!(out.contains("vendored  indexed (included by you)") && out.contains("ours  not indexed (excluded by you)"), "{out}");

    // `auto` removes the override: the tests decide again.
    let (_, out) = f.run(&["submodules", "auto", "vendored"]);
    assert!(out.contains("vendored  not indexed (third-party)"), "{out}");
    f.run(&["submodules", "auto", "ours"]);
    let text = std::fs::read_to_string(&file).unwrap();
    assert!(!text.contains("include") && !text.contains("exclude"), "{text}");

    // An unknown one is an error that lists the known ones.
    let (code, out) = f.run(&["submodules", "include", "nope"]);
    assert_ne!(code, 0);
    assert!(out.contains("no submodule `nope`") && out.contains("ours, vendored"), "{out}");
}

#[test]
fn where_codex_home_cannot_be_written_the_choice_is_refused() {
    // Inside Codex's sandbox `$CODEX_HOME` is read-only: the agent cannot change what is indexed.
    use std::os::unix::fs::PermissionsExt;
    let f = with_submodules();
    let locked = f.dir.path().join("locked-home");
    std::fs::create_dir_all(&locked).unwrap();
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o555)).unwrap();
    let (code, out) = f.run_env(&["submodules", "include", "vendored"], &[("CODEX_HOME", &locked.to_string_lossy())]);
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_ne!(code, 0, "{out}");
    assert!(out.contains("what is indexed is your decision: run `mling-code submodules include vendored` in your own shell"), "{out}");
    // Listing works everywhere.
    let (code, out) = f.run_env(&["submodules"], &[("CODEX_HOME", &locked.to_string_lossy())]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("vendored  not indexed (third-party)"), "{out}");
}

#[test]
fn a_choice_recorded_inside_the_repository_changes_nothing() {
    let f = with_submodules();
    // What a clone could ship, or the agent could write from the workspace-write sandbox.
    let key = f.repo.root.canonicalize().unwrap();
    f.write("mling-code.toml", &format!("[projects.\"{}\".submodules]\nvendored = \"include\"\n", key.display()));
    f.write(".dreamference/mling-code.toml", &format!("[projects.\"{}\".submodules]\nvendored = \"include\"\n", key.display()));
    let (_, out) = f.run(&["submodules"]);
    assert!(out.contains("vendored  not indexed (third-party)"), "{out}");
}

#[test]
fn every_answer_and_the_prompt_block_say_what_is_left_out() {
    let f = with_submodules();
    for args in [&["refs", "make_circle"][..], &["def", "make_circle"], &["search", "circle"], &["outline", "shapes/geometry.py"], &["show", "make_circle"]] {
        let (_, out) = f.run(args);
        assert!(out.contains("submodules not indexed: vendored/ (third-party)"), "{args:?}: {out}");
    }
    let (_, out) = f.run(&["prompt-block"]);
    assert!(out.contains("Submodules not indexed: vendored/ (third-party); use `rg` there."), "{out}");
    let (_, out) = f.run(&["status"]);
    assert!(out.contains("submodule ours: indexed (yours)") && out.contains("submodule vendored: not indexed (third-party)"), "{out}");
    // With nothing left out there is no line.
    f.run(&["submodules", "include", "vendored"]);
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(!out.contains("submodules not indexed"), "{out}");
}

#[test]
fn the_ignore_file_lists_exactly_what_the_policy_leaves_out() {
    let f = with_submodules();
    mling_code::index::write_cbmignore(&f.repo, &Settings::default()).unwrap();
    let text = std::fs::read_to_string(f.repo.root.join(".cbmignore")).unwrap();
    assert!(text.contains("vendored/\n") && !text.contains("ours/"), "{text}");
    // The block is output: an edit holds for one run at most.
    std::fs::write(f.repo.root.join(".cbmignore"), text.replace("vendored/\n", "") + "my-own-rule/\n").unwrap();
    mling_code::index::write_cbmignore(&f.repo, &Settings::default()).unwrap();
    let text = std::fs::read_to_string(f.repo.root.join(".cbmignore")).unwrap();
    assert!(text.contains("vendored/\n") && text.contains("my-own-rule/\n"), "{text}");
    assert_eq!(text.matches("BEGIN mling-code managed").count(), 1, "{text}");
    // A block written by the previous release (another header) is replaced, not kept beside it.
    let old = "# BEGIN mling-code managed: submodules are not indexed (mling-code index --include-submodules)\nours/\nvendored/\n# END mling-code managed\n";
    std::fs::write(f.repo.root.join(".cbmignore"), old).unwrap();
    mling_code::index::write_cbmignore(&f.repo, &Settings::default()).unwrap();
    let text = std::fs::read_to_string(f.repo.root.join(".cbmignore")).unwrap();
    assert!(!text.contains("ours/") && text.matches("BEGIN mling-code managed").count() == 1, "{text}");
}

#[test]
fn the_removed_flag_names_its_replacement() {
    let f = with_submodules();
    let (code, out) = f.run(&["index", "--include-submodules"]);
    assert_ne!(code, 0);
    assert!(out.contains("mling-code submodules include"), "{out}");
}

#[test]
fn an_included_submodules_files_are_seen_by_git_and_by_freshness() {
    let f = with_submodules();
    git(&f.repo.root, &["add", "-A"]);
    git(&f.repo.root, &["commit", "-qm", "add submodules"]);
    let snapshot = git(&f.repo.root, &["rev-parse", "HEAD"]);
    let included = submodules::included(&decisions(&f.repo));
    assert_eq!(included, ["ours"]);
    let view = || GitView::with_included(&f.repo, included.clone());

    // Its files are listed with its path as their prefix; the left-out one stays one entry.
    let all = view().all_files().clone();
    assert!(all.contains("ours/tools/helper.py") && all.contains("ours/notes.txt"), "{all:?}");
    assert!(!all.contains("ours") && !all.iter().any(|p| p.starts_with("vendored/")), "{all:?}");

    // Clean: nothing changed since the snapshot.
    assert!(view().status().is_empty(), "{:?}", view().status());
    assert!(view().diff(&snapshot).unwrap().is_empty());

    // An edit and a new file inside the submodule are found, by name.
    f.append("ours/tools/helper.py", "\ndef second():\n    return helper()\n");
    f.write("ours/tools/new.py", "from helper import helper\n");
    let status = view().status().clone();
    assert!(status.contains("ours/tools/helper.py") && status.contains("ours/tools/new.py"), "{status:?}");
    assert!(!status.contains("ours"), "the submodule's own entry is a directory, not a file: {status:?}");

    // Only those: the files the edit did not touch are not candidates.
    assert!(!status.contains("ours/notes.txt"), "{status:?}");

    // A commit inside it that the superproject has not recorded yet: the checkout has moved off
    // the recorded commit, and the files that differ are found.
    git(&f.repo.root.join("ours"), &["add", "-A"]);
    git(&f.repo.root.join("ours"), &["commit", "-qm", "more"]);
    let status = view().status().clone();
    assert!(status.contains("ours/tools/helper.py") && status.contains("ours/tools/new.py") && !status.contains("ours/notes.txt"), "{status:?}");

    // Recorded by the superproject, the same files are what changed since the snapshot's commit.
    git(&f.repo.root, &["add", "ours"]);
    git(&f.repo.root, &["commit", "-qm", "bump ours"]);
    assert!(view().status().is_empty(), "{:?}", view().status());
    let diff = view().diff(&snapshot).unwrap().clone();
    assert!(diff.contains("ours/tools/helper.py") && diff.contains("ours/tools/new.py"), "{diff:?}");
    assert!(!diff.contains("ours") && !diff.contains("ours/notes.txt"), "{diff:?}");

    // A snapshot from before the submodule existed: every file of it is new since.
    let before = git(&f.repo.root, &["rev-list", "--max-parents=0", "HEAD"]);
    let diff = view().diff(&before).unwrap().clone();
    assert!(diff.contains("ours/notes.txt") && diff.contains("ours/tools/helper.py"), "{diff:?}");

    // Without the submodule included, git reports only its entry, which names no file.
    let plain = GitView::with_included(&f.repo, Vec::new()).diff(&snapshot).unwrap().clone();
    assert!(plain.contains("ours") && !plain.iter().any(|p| p.starts_with("ours/")), "{plain:?}");
}

/// The fixture as `acme/super` with the submodule `mixed`: Python, TypeScript and a Rust crate.
fn with_a_mixed_submodule(author: &str, url: Option<&str>) -> Fixture {
    let f = fixture();
    let root = f.repo.root.clone();
    git(&root, &["remote", "add", "origin", "https://example.com/acme/super.git"]);
    let lib = library(
        f.dir.path(),
        "mixed",
        author,
        &[
            ("pytools/a.py", "def a():\n    return 1\n"),
            ("web/tsconfig.json", "{}"),
            ("web/x.ts", "export const x = 1;\n"),
            ("crate/Cargo.toml", "[package]\nname = \"c\"\nversion = \"0.1.0\"\n"),
            ("crate/src/lib.rs", "pub fn c() {}\n"),
        ],
        2,
    );
    add_submodule(&root, &lib, "mixed", url);
    f
}

/// Writes `$CODEX_HOME/config.toml` of the fixture's home, trusting `paths`.
fn trust(f: &Fixture, paths: &[&Path]) -> PathBuf {
    let codex_home = f.home.join(".mightling");
    std::fs::create_dir_all(&codex_home).unwrap();
    let text: String = paths.iter().map(|p| format!("[projects.\"{}\"]\ntrust_level = \"trusted\"\n\n", p.display())).collect();
    std::fs::write(codex_home.join("config.toml"), text).unwrap();
    codex_home
}

#[test]
fn every_indexer_is_detected_inside_an_included_submodule() {
    let f = with_a_mixed_submodule("t@localhost", Some("https://example.com/acme/mixed.git"));
    let all = decisions(&f.repo);
    assert!(all[0].indexed);
    let (targets, notes) = plan::detect_in_submodules(&f.repo, &all);
    let mut found: Vec<(&str, &str, bool)> = targets.iter().map(|t| (t.indexer, t.root.as_str(), t.kind == Kind::Static)).collect();
    found.sort();
    assert_eq!(found, [("rust-analyzer", "mixed/crate", false), ("scip-python", "mixed/pytools", true), ("scip-typescript", "mixed/web", true)]);
    assert!(targets.iter().all(|t| t.submodule == "mixed"), "{targets:?}");
    assert!(notes.is_empty(), "{notes:?}");
    // The superproject's own detection still leaves submodule directories alone.
    assert!(plan::detect(&f.repo).iter().all(|t| !t.root.starts_with("mixed") && t.submodule.is_empty()));
    // A submodule that is left out contributes nothing.
    let none = submodules::evaluate_with(&f.repo, 5000, &[("mixed".to_string(), Choice::Exclude)]);
    assert_eq!(plan::detect_in_submodules(&f.repo, &none), (Vec::new(), Vec::new()));
}

#[test]
fn an_executing_indexer_runs_in_a_submodule_only_on_the_submodules_own_trust() {
    // Yours, with a namespace to compare: it inherits the superproject's trust, and only that.
    let f = with_a_mixed_submodule("t@localhost", Some("https://example.com/acme/mixed.git"));
    let all = decisions(&f.repo);
    let (targets, _) = plan::detect_in_submodules(&f.repo, &all);
    let rust = targets.iter().find(|t| t.indexer == "rust-analyzer").unwrap();
    let nowhere = f.dir.path().join("no-codex-home");
    assert_eq!(plan::untrusted_reason_in(&nowhere, &f.repo, true, &all, rust), None);
    let why = plan::untrusted_reason_in(&nowhere, &f.repo, false, &all, rust).unwrap();
    assert!(why.contains("the repository is not trusted"), "{why}");
    let (_, out) = f.run(&["submodules"]);
    assert!(out.contains("rust-analyzer: on a scratch copy of the submodule") && out.contains("it would inherit this repository's trust, which is not given"), "{out}");
    trust(&f, &[&f.repo.root]);
    let (_, out) = f.run(&["submodules"]);
    assert!(out.contains("it inherits this repository's trust"), "{out}");
    let (_, out) = f.run(&["status"]);
    assert!(out.contains("exact: rust-analyzer for mixed/crate: "), "{out}");
    assert!(!out.contains("mixed/crate: the submodule"), "{out}");

    // Included by the user, written by someone else: including is not trusting.
    let f = with_a_mixed_submodule("dev@upstream.example", Some("https://example.com/acme/mixed.git"));
    let all = submodules::evaluate_with(&f.repo, 5000, &[("mixed".to_string(), Choice::Include)]);
    assert!(all[0].indexed && !all[0].inherits_trust, "{:?}", all[0]);
    let (targets, _) = plan::detect_in_submodules(&f.repo, &all);
    let rust = targets.iter().find(|t| t.indexer == "rust-analyzer").unwrap();
    let codex_home = trust(&f, &[&f.repo.root]);
    let why = plan::untrusted_reason_in(&codex_home, &f.repo, true, &all, rust).expect("the superproject's trust is not the submodule's");
    assert!(why.contains("the submodule mixed is not trusted") && why.contains(&format!("[projects.\"{}\"]", f.repo.root.join("mixed").display())), "{why}");
    // The static indexers are not gated: they run nothing from the submodule.
    let python = targets.iter().find(|t| t.indexer == "scip-python").unwrap();
    assert_eq!(python.kind, Kind::Static);
    // Its own entry in Codex's trust table is what lets the build run.
    let codex_home = trust(&f, &[&f.repo.root, &f.repo.root.join("mixed")]);
    assert_eq!(plan::untrusted_reason_in(&codex_home, &f.repo, true, &all, rust), None);
    // And an entry for the submodule alone is enough: it is trusted on its own terms.
    let codex_home = trust(&f, &[&f.repo.root.join("mixed")]);
    assert_eq!(plan::untrusted_reason_in(&codex_home, &f.repo, false, &all, rust), None);
}

#[test]
fn a_submodule_decided_by_authorship_alone_never_inherits_trust() {
    // No remote on the superproject's side to compare: yours by authorship, indexed, not trusted.
    let f = fixture();
    let lib = library(f.dir.path(), "mine", "t@localhost", &[("crate/Cargo.toml", "[package]\nname = \"c\"\nversion = \"0.1.0\"\n"), ("crate/src/lib.rs", "pub fn c() {}\n")], 2);
    add_submodule(&f.repo.root, &lib, "mine", None);
    let all = decisions(&f.repo);
    assert_eq!((all[0].indexed, all[0].reason.clone(), all[0].inherits_trust), (true, Reason::YoursByAuthorship, false));
    let (targets, _) = plan::detect_in_submodules(&f.repo, &all);
    let nowhere = f.dir.path().join("no-codex-home");
    let why = plan::untrusted_reason_in(&nowhere, &f.repo, true, &all, &targets[0]).unwrap();
    assert!(why.contains("the submodule mine is not trusted"), "{why}");
}

#[test]
fn a_repository_without_submodules_costs_nothing_and_prints_nothing() {
    let f = fixture();
    assert!(decisions(&f.repo).is_empty());
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(!out.contains("submodules"), "{out}");
    let (_, out) = f.run(&["submodules"]);
    assert!(out.contains("no submodules"), "{out}");
}
