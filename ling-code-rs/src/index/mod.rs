//! Indexing: the universal layer (codebase-memory), the static exact layers (scip-python,
//! scip-typescript, scip-go, scip-clang) and the executing ones (rust-analyzer, scip-java,
//! scip-dotnet), each admitted against one host-wide budget and run in a
//! network-less bwrap sandbox inside `mightling-index.slice` (spec §6, §9).
//!
//! Only a process outside Codex's sandbox can index: inside it there is no systemd bus. There,
//! `ling-code index` appends a request for the session process instead (spec §4).

pub mod host;
pub mod plan;
pub mod probe;
pub mod sandbox;
pub mod store;
pub mod supervisor;

use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result};

use crate::config::Settings;
use crate::paths::Repo;
use crate::requests::{self, Request};
use host::{Host, SystemdHost};

/// `ling-code index`: run the plan now when this process can create scopes, else queue it.
pub fn request_or_run(repo: &Repo, settings: &Settings, exact: bool, wait: bool) -> Result<()> {
    let host = SystemdHost;
    if !host.can_create_scopes() {
        let request = if exact { Request::IndexExact } else { Request::Index };
        requests::append(&repo.state_dir(), request).context("queueing the request (this sandbox cannot write the repository)")?;
        println!("queued: the Mightling session outside the sandbox will re-index when the model is idle");
        return Ok(());
    }
    write_cbmignore(repo, settings)?;
    // Typed by the user: the on-demand indexers (scip-clang, scip-go) run too.
    let (plan, skipped) = plan::build_with(repo, settings, exact, true);
    for why in &skipped {
        println!("skipped: {why}");
    }
    if plan.runs.is_empty() {
        return Ok(());
    }
    if wait {
        for (unit, outcome) in supervisor::execute(&plan, &host, &probe::HttpProbe::new(&plan.vllm_host), supervisor::POLL) {
            println!("{unit}: {}", outcome.status());
        }
        return Ok(());
    }
    let pid = spawn_supervisor(repo, &plan)?;
    println!("indexing in the background (supervisor pid {pid}); `ling-code status` reports progress");
    Ok(())
}

/// Writes the plan and starts `ling-code supervise <plan>` detached: its own session, stdio to a
/// log, so it outlives the process that started it and can install a long run's output.
pub fn spawn_supervisor(repo: &Repo, plan: &plan::Plan) -> Result<u32> {
    Ok(spawn_supervisor_child(repo, plan)?.id())
}

/// Like [`spawn_supervisor`], returning the child so the session can tell when it has finished.
pub fn spawn_supervisor_child(repo: &Repo, plan: &plan::Plan) -> Result<std::process::Child> {
    let dir = repo.state_dir().join("plans");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("plan-{}.json", std::process::id()));
    std::fs::write(&path, serde_json::to_vec_pretty(plan)?)?;
    let log = std::fs::File::create(dir.join(format!("supervisor-{}.log", std::process::id())))?;
    let exe = std::env::current_exe()?;
    let child = unsafe {
        Command::new(exe)
            .arg("supervise")
            .arg(&path)
            .current_dir(&repo.root)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .pre_exec(|| {
                libc::setsid();
                Ok(())
            })
            .spawn()?
    };
    Ok(child)
}

/// `ling-code supervise <plan>`: execute a written plan (the detached supervisor).
pub fn supervise(plan_path: &str) -> Result<()> {
    let plan: plan::Plan = serde_json::from_slice(&std::fs::read(plan_path)?)?;
    let marker = plan.state_dir.join("code_index.running");
    let _ = std::fs::write(&marker, std::process::id().to_string());
    for (unit, outcome) in supervisor::execute(&plan, &SystemdHost, &probe::HttpProbe::new(&plan.vllm_host), supervisor::POLL) {
        println!("{} {unit}: {}", crate::manifest::now_rfc3339(), outcome.status());
    }
    let _ = std::fs::remove_file(&marker);
    let _ = std::fs::remove_file(plan.state_dir.join("code_index.building"));
    let _ = std::fs::remove_file(plan_path);
    Ok(())
}

/// Whether a supervisor for this repository is still running.
pub fn running(repo: &Repo) -> bool {
    std::fs::read_to_string(repo.state_dir().join("code_index.running"))
        .ok()
        .and_then(|pid| pid.trim().parse::<i32>().ok())
        .map(|pid| unsafe { libc::kill(pid, 0) } == 0)
        .unwrap_or(false)
}

const CBMIGNORE_BEGIN: &str = "# BEGIN ling-code managed: submodules that are not indexed (see `ling-code submodules`)";
const CBMIGNORE_BEGIN_PREFIX: &str = "# BEGIN ling-code managed";
const CBMIGNORE_END: &str = "# END ling-code managed";

/// Keeps the managed block of `.cbmignore` listing the submodules §4.3 leaves out (spec §4.1),
/// and keeps the file out of `git status` through `.git/info/exclude`.
///
/// The block is output, rewritten from the policy before every run: editing it changes nothing
/// for longer than one run. The policy is decided for the main worktree, whose index this is.
pub fn write_cbmignore(repo: &Repo, settings: &Settings) -> Result<()> {
    let path = repo.main_root.join(".cbmignore");
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let main = Repo { root: repo.main_root.clone(), main_root: repo.main_root.clone(), is_git: repo.is_git };
    let excluded: Vec<String> = crate::submodules::evaluate(&main, settings).into_iter().filter(|s| !s.indexed).map(|s| s.path).collect();
    let mut kept: Vec<&str> = Vec::new();
    let mut inside = false;
    for line in existing.lines() {
        if line.starts_with(CBMIGNORE_BEGIN_PREFIX) {
            inside = true;
        } else if line == CBMIGNORE_END {
            inside = false;
        } else if !inside {
            kept.push(line);
        }
    }
    let mut text = kept.join("\n");
    if !excluded.is_empty() {
        if !text.is_empty() && !text.ends_with('\n') {
            text.push('\n');
        }
        text.push_str(CBMIGNORE_BEGIN);
        text.push('\n');
        for submodule in &excluded {
            text.push_str(&format!("{submodule}/\n"));
        }
        text.push_str(CBMIGNORE_END);
        text.push('\n');
    }
    if text.trim().is_empty() {
        let _ = std::fs::remove_file(&path);
    } else if text != existing {
        std::fs::write(&path, &text)?;
    }
    if repo.is_git {
        exclude_from_git(repo, ".cbmignore")?;
        // The state directory holds stores, logs and plans; a repository that does not ignore it
        // would otherwise list every one of them in `git status` on each query.
        exclude_from_git(repo, ".dreamference/")?;
    }
    Ok(())
}

fn exclude_from_git(repo: &Repo, entry: &str) -> Result<()> {
    let common = crate::paths::git(&repo.root, &["rev-parse", "--path-format=absolute", "--git-common-dir"])?;
    let exclude = PathBuf::from(common.trim()).join("info/exclude");
    let text = std::fs::read_to_string(&exclude).unwrap_or_default();
    if !text.lines().any(|l| l.trim() == entry) {
        std::fs::create_dir_all(exclude.parent().unwrap())?;
        let mut text = text;
        if !text.is_empty() && !text.ends_with('\n') {
            text.push('\n');
        }
        text.push_str(entry);
        text.push('\n');
        std::fs::write(exclude, text)?;
    }
    Ok(())
}

/// `ling-code forget`: deletes this repository's graph and SCIP stores.
pub fn forget(repo: &Repo) -> Result<()> {
    let (graph, _) = crate::graph::locate(repo);
    for path in [graph.clone(), PathBuf::from(format!("{}-wal", graph.display())), PathBuf::from(format!("{}-journal", graph.display()))] {
        let _ = std::fs::remove_file(path);
    }
    let scip = repo.scip_dir();
    if scip.is_dir() {
        std::fs::remove_dir_all(&scip)?;
    }
    println!("forgot the index of {}", repo.main_root.display());
    Ok(())
}

/// Disk used by a file or directory tree.
pub fn disk_use(path: &Path) -> u64 {
    match std::fs::symlink_metadata(path) {
        Ok(meta) if meta.is_dir() => std::fs::read_dir(path).map(|e| e.flatten().map(|e| disk_use(&e.path())).sum()).unwrap_or(0),
        Ok(meta) => meta.len(),
        Err(_) => 0,
    }
}
