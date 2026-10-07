//! A fixture repository for the router's tests: the sources of tests/fixtures/code_index/src in a
//! fresh git repository, with the recorded stores installed as a real index run would install
//! them. Nothing here needs codebase-memory, scip-python, rust-analyzer or the scip CLI.
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;

use ling_code::index::store;
use ling_code::manifest::{GraphSnapshot, RunEntry};
use ling_code::paths::Repo;

pub fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/code_index")
}

pub struct Fixture {
    pub dir: tempfile::TempDir,
    pub repo: Repo,
    pub graph_db: PathBuf,
    pub project: String,
    pub home: PathBuf,
}

pub fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(["-c", "user.name=t", "-c", "user.email=t@localhost", "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn copy_tree(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap().flatten() {
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Builds the fixture repository with both layers installed at its first commit.
pub fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    copy_tree(&fixture_dir().join("src"), &root);
    git(&root, &["init", "-q"]);
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-qm", "fixture"]);
    let repo = Repo { root: root.clone(), main_root: root.clone(), is_git: true };
    let stores = fixture_dir().join("stores");
    let graph_db = dir.path().join("graph.db");
    std::fs::copy(stores.join("graph.db"), &graph_db).unwrap();
    let project = rusqlite::Connection::open(&graph_db)
        .unwrap()
        .query_row("SELECT name FROM projects WHERE name NOT LIKE '%::%'", [], |r| r.get::<_, String>(0))
        .unwrap();
    let commit = git(&root, &["rev-parse", "HEAD"]);
    for (indexer, root_dir, name) in [("scip-python", "shapes", "shapes"), ("rust-analyzer", "geom", "geom"), ("scip-typescript", "tsgeom", "tsgeom")] {
        let work = dir.path().join(format!("{name}.db"));
        std::fs::copy(stores.join(format!("{name}.db")), &work).unwrap();
        let prefix = format!("{root_dir}/");
        let entry = RunEntry {
            indexer: indexer.to_string(),
            root: root_dir.to_string(),
            path_prefix: prefix.clone(),
            version: "fixture".to_string(),
            commit: Some(commit.clone()),
            file_hashes: store::stamp_root(&repo, ""),
            ..RunEntry::default()
        };
        store::install(&repo, entry, &work, &stores.join(format!("{name}.scip"))).unwrap();
    }
    GraphSnapshot { commit: Some(commit), status: "ok".into(), ..GraphSnapshot::default() }.save(&repo.scip_dir()).unwrap();
    let home = dir.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    Fixture { dir, repo, graph_db, project, home }
}

impl Fixture {
    /// Runs the built `ling-code` in the fixture repository, with the recorded graph.
    pub fn run(&self, args: &[&str]) -> (i32, String) {
        self.run_in(&self.repo.root, args, &[])
    }

    pub fn run_env(&self, args: &[&str], env: &[(&str, &str)]) -> (i32, String) {
        self.run_in(&self.repo.root, args, env)
    }

    pub fn run_in(&self, cwd: &Path, args: &[&str], env: &[(&str, &str)]) -> (i32, String) {
        let mut command = self.command_in(cwd, args);
        for (key, value) in env {
            command.env(key, value);
        }
        let out = command.output().unwrap();
        let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        (out.status.code().unwrap_or(-1), text)
    }

    /// The command, isolated from the machine, for callers that spawn it themselves.
    pub fn command(&self, args: &[&str]) -> Command {
        self.command_in(&self.repo.root, args)
    }

    fn command_in(&self, cwd: &Path, args: &[&str]) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_ling-code"));
        command
            .args(args)
            .current_dir(cwd)
            .env("MIGHTLING_CODE_GRAPH_DB", &self.graph_db)
            .env("MIGHTLING_CODE_PROJECT", &self.project)
            .env("HOME", &self.home)
            .env_remove("DREAMFERENCE_CONFIG_PATH")
            .env_remove("CODEX_HOME")
            .env_remove("MIGHTLING_CODE_STATE_DIR")
            .env_remove("MIGHTLING_CODE_ROOT")
            // Nothing a test runs may reach the machine's own runtime directory, tools or caches.
            .env("XDG_RUNTIME_DIR", self.dir.path().join("run"))
            // No systemd bus: as inside Codex's sandbox, and so no test can create a real scope.
            .env("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
            .env("MIGHTLING_CODE_TOOLS_DIR", self.dir.path().join("no-tools"))
            .env("MIGHTLING_CODE_INDEXERS_DIR", self.dir.path().join("no-indexers"))
            .env("MIGHTLING_CODE_SCRATCH_DIR", self.dir.path().join("scratch"))
            .env("CBM_CACHE_DIR", self.dir.path().join("cbm-cache"));
        command
    }

    pub fn write(&self, rel: &str, content: &str) {
        let path = self.repo.root.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }

    pub fn append(&self, rel: &str, content: &str) {
        let mut text = std::fs::read_to_string(self.repo.root.join(rel)).unwrap();
        text.push_str(content);
        self.write(rel, &text);
    }

    pub fn commit(&self, message: &str) {
        git(&self.repo.root, &["add", "-A"]);
        git(&self.repo.root, &["commit", "-qm", message]);
    }
}
