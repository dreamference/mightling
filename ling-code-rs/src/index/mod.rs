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

#[cfg(unix)]
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
        let request = if exact {
            Request::IndexExact
        } else {
            Request::Index
        };
        requests::append(&repo.state_dir(), request)
            .context("queueing the request (this sandbox cannot write the repository)")?;
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
        let Some(_lock) = claim_supervisor(&plan.state_dir)? else {
            println!("{ANOTHER_SUPERVISOR}");
            return Ok(());
        };
        for (unit, outcome) in supervisor::execute(
            &plan,
            &host,
            &probe::HttpProbe::new(&plan.vllm_host),
            supervisor::POLL,
        ) {
            println!("{unit}: {}", outcome.status());
        }
        return Ok(());
    }
    let Some(pid) = spawn_supervisor_unless_running(repo, &plan)? else {
        println!("{ANOTHER_SUPERVISOR}");
        return Ok(());
    };
    println!(
        "indexing in the background (supervisor pid {pid}); `ling-code status` reports progress"
    );
    Ok(())
}

/// [`spawn_supervisor`], unless another supervisor holds the lock (its child would stand down at
/// once, after the caller had said indexing started); None then.
fn spawn_supervisor_unless_running(repo: &Repo, plan: &plan::Plan) -> Result<Option<u32>> {
    if claim_supervisor(&plan.state_dir)?.is_none() {
        return Ok(None);
    }
    spawn_supervisor(repo, plan).map(Some)
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
    // Fresh files: the names are predictable, and a symlink planted under them is not followed.
    std::io::Write::write_all(
        &mut crate::paths::create_fresh(&path)?,
        &serde_json::to_vec_pretty(plan)?,
    )?;
    let log =
        crate::paths::create_fresh(&dir.join(format!("supervisor-{}.log", std::process::id())))?;
    let exe = std::env::current_exe()?;
    let mut command = Command::new(exe);
    command
        .arg("supervise")
        .arg(&path)
        .current_dir(&repo.root)
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    // Its own session, so it outlives the process that started it.
    #[cfg(unix)]
    // SAFETY: setsid is async-signal-safe and touches nothing the parent shares.
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    Ok(command.spawn()?)
}

/// `ling-code supervise <plan>`: execute a written plan (the detached supervisor).
pub fn supervise(plan_path: &str) -> Result<()> {
    let plan: plan::Plan = serde_json::from_slice(&std::fs::read(plan_path)?)?;
    let Some(_lock) = claim_supervisor(&plan.state_dir)? else {
        println!("{ANOTHER_SUPERVISOR}");
        let _ = std::fs::remove_file(plan_path);
        return Ok(());
    };
    let marker = plan.state_dir.join("code_index.running");
    let _ = crate::paths::create_fresh(&marker).and_then(|mut f| {
        std::io::Write::write_all(&mut f, std::process::id().to_string().as_bytes())
    });
    for (unit, outcome) in supervisor::execute(
        &plan,
        &SystemdHost,
        &probe::HttpProbe::new(&plan.vllm_host),
        supervisor::POLL,
    ) {
        println!(
            "{} {unit}: {}",
            crate::manifest::now_rfc3339(),
            outcome.status()
        );
    }
    let _ = std::fs::remove_file(&marker);
    let _ = std::fs::remove_file(plan.state_dir.join("code_index.building"));
    let _ = std::fs::remove_file(plan_path);
    Ok(())
}

const ANOTHER_SUPERVISOR: &str =
    "not indexing: another supervisor is running for this repository; `ling-code status` reports it";

/// The lock that keeps one supervisor per repository, across sessions and `index --wait`
/// (spec: one run of each kind per repository); None while another holds it.
fn claim_supervisor(state_dir: &Path) -> Result<Option<std::fs::File>> {
    let lock = crate::paths::state_file_in(state_dir, "supervisor.lock")?;
    Ok(host::flock(&lock, false).is_ok().then_some(lock))
}

/// Whether a supervisor for this repository is still running.
pub fn running(repo: &Repo) -> bool {
    std::fs::read_to_string(repo.state_dir().join("code_index.running"))
        .ok()
        .and_then(|pid| pid.trim().parse::<i32>().ok())
        .map(crate::paths::process_alive)
        .unwrap_or(false)
}

/// Whether a supervisor is running or holds the lock: `index --wait` supervises in-process and
/// writes no running marker. It takes the lock for a moment, so a supervisor starting just then
/// would stand down: ask only before starting one. Creates the state directory.
pub fn supervisor_busy(repo: &Repo) -> bool {
    running(repo)
        || claim_supervisor(&repo.state_dir())
            .map(|lock| lock.is_none())
            .unwrap_or(false)
}

const CBMIGNORE_BEGIN: &str =
    "# BEGIN ling-code managed: submodules that are not indexed (see `ling-code submodules`)";
const CBMIGNORE_BEGIN_PREFIX: &str = "# BEGIN ling-code managed";
const CBMIGNORE_END: &str = "# END ling-code managed";

/// Keeps the managed block of `.cbmignore` listing the submodules §4.3 leaves out (spec §4.1),
/// and keeps the file out of `git status` through `.git/info/exclude`.
///
/// The block is output, rewritten from the policy before every run: editing it changes nothing
/// for longer than one run. The policy is decided for the main worktree, whose index this is.
pub fn write_cbmignore(repo: &Repo, settings: &Settings) -> Result<()> {
    let path = repo.main_root.join(".cbmignore");
    // The session runs outside the sandbox, and a clone or the agent can plant `.cbmignore` as a
    // symlink: its target is never read, and the rename below replaces the link.
    let existing = crate::paths::read_regular(&path).unwrap_or_default();
    let main = Repo {
        root: repo.main_root.clone(),
        main_root: repo.main_root.clone(),
        is_git: repo.is_git,
    };
    let excluded: Vec<String> = crate::submodules::evaluate(&main, settings)
        .into_iter()
        .filter(|s| !s.indexed)
        .map(|s| s.path)
        .collect();
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
        crate::manifest::write_atomic(&path, text.as_bytes())?;
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
    let common = crate::paths::git(
        &repo.root,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )?;
    let exclude = PathBuf::from(common.trim()).join("info/exclude");
    let Some(text) = crate::paths::read_regular(&exclude).or_else(|| {
        // Missing is fine; a symlink or anything else is not this process's to touch.
        std::fs::symlink_metadata(&exclude)
            .is_err()
            .then(String::new)
    }) else {
        return Ok(());
    };
    if !text.lines().any(|l| l.trim() == entry) {
        let mut text = text;
        if !text.is_empty() && !text.ends_with('\n') {
            text.push('\n');
        }
        text.push_str(entry);
        text.push('\n');
        crate::manifest::write_atomic(&exclude, text.as_bytes())?;
    }
    Ok(())
}

/// `ling-code forget`: deletes this repository's graph and SCIP stores.
pub fn forget(repo: &Repo) -> Result<()> {
    let (graph, _) = crate::graph::locate(repo);
    for path in [
        graph.clone(),
        PathBuf::from(format!("{}-wal", graph.display())),
        PathBuf::from(format!("{}-journal", graph.display())),
    ] {
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
        Ok(meta) if meta.is_dir() => std::fs::read_dir(path)
            .map(|e| e.flatten().map(|e| disk_use(&e.path())).sum())
            .unwrap_or(0),
        Ok(meta) => meta.len(),
        Err(_) => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `.git/info/exclude` is git's own file: a symlink there is left as it is, and so is its target.
    #[cfg(unix)]
    #[test]
    fn a_linked_git_exclude_file_is_left_alone() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("repo");
        std::fs::create_dir(&root).unwrap();
        crate::paths::git(&root, &["init", "-q"]).unwrap();
        let outside = dir.path().join("outside");
        std::fs::write(&outside, "keep\n").unwrap();
        let exclude = root.join(".git/info/exclude");
        let _ = std::fs::remove_file(&exclude);
        std::fs::create_dir_all(exclude.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&outside, &exclude).unwrap();
        let repo = Repo {
            root: root.clone(),
            main_root: root,
            is_git: true,
        };
        exclude_from_git(&repo, ".cbmignore").unwrap();
        assert!(std::fs::symlink_metadata(&exclude)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "keep\n");
    }

    #[test]
    fn a_second_supervisor_finds_the_lock_held() {
        let dir = tempfile::tempdir().unwrap();
        let first = claim_supervisor(dir.path()).unwrap();
        assert!(first.is_some());
        assert!(claim_supervisor(dir.path()).unwrap().is_none());
        drop(first);
        // A process another test thread is starting holds a copy of the lock's descriptor
        // until it execs, so the lock can outlive the drop by that moment (1 run in 7 at
        // --test-threads=64). It must still come free.
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut again = claim_supervisor(dir.path()).unwrap();
        while again.is_none() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(10));
            again = claim_supervisor(dir.path()).unwrap();
        }
        assert!(again.is_some());
    }

    /// `ling-code index` must not report a background run that would stand down at once.
    #[test]
    fn no_supervisor_is_spawned_while_another_holds_the_lock() {
        let dir = tempfile::tempdir().unwrap();
        let repo = Repo {
            root: dir.path().to_path_buf(),
            main_root: dir.path().to_path_buf(),
            is_git: false,
        };
        let plan = plan::Plan {
            repo_root: repo.root.clone(),
            main_root: repo.root.clone(),
            state_dir: repo.state_dir(),
            vllm_host: "http://127.0.0.1:9".into(),
            wait_idle_s: 0,
            runs: Vec::new(),
        };
        let _held = claim_supervisor(&plan.state_dir).unwrap().unwrap();
        assert_eq!(spawn_supervisor_unless_running(&repo, &plan).unwrap(), None);
        assert!(!repo.state_dir().join("plans").exists(), "no plan written");
    }

    #[cfg(unix)]
    #[test]
    fn disk_use_and_running_reflect_filesystem_and_process_state() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(disk_use(&dir.path().join("missing")), 0);
        std::fs::create_dir_all(dir.path().join("sub")).unwrap();
        std::fs::write(dir.path().join("a"), b"1234").unwrap();
        std::fs::write(dir.path().join("sub/b"), b"123456").unwrap();
        assert_eq!(disk_use(dir.path()), 10);

        let repo = Repo {
            root: dir.path().to_path_buf(),
            main_root: dir.path().to_path_buf(),
            is_git: false,
        };
        assert!(!running(&repo));
        std::fs::create_dir_all(repo.state_dir()).unwrap();
        std::fs::write(
            repo.state_dir().join("code_index.running"),
            std::process::id().to_string(),
        )
        .unwrap();
        assert!(running(&repo));
        std::fs::write(repo.state_dir().join("code_index.running"), "999999999").unwrap();
        assert!(!running(&repo));
    }

    #[test]
    fn write_cbmignore_preserves_user_lines_and_strips_empty_managed_file() {
        let dir = tempfile::tempdir().unwrap();
        let repo = Repo {
            root: dir.path().to_path_buf(),
            main_root: dir.path().to_path_buf(),
            is_git: false,
        };
        let cbm = dir.path().join(".cbmignore");
        std::fs::write(
            &cbm,
            format!("custom_dir/\n{CBMIGNORE_BEGIN}\nold_sub/\n{CBMIGNORE_END}\n"),
        )
        .unwrap();
        write_cbmignore(&repo, &Settings::default()).unwrap();
        assert_eq!(std::fs::read_to_string(&cbm).unwrap(), "custom_dir/");

        // When only the managed block was present and no submodules are excluded, the file is removed.
        std::fs::write(
            &cbm,
            format!("{CBMIGNORE_BEGIN}\nold_sub/\n{CBMIGNORE_END}\n"),
        )
        .unwrap();
        write_cbmignore(&repo, &Settings::default()).unwrap();
        assert!(!cbm.exists());
    }

    /// The session writes `.cbmignore` outside the sandbox, and a clone or the agent can plant it
    /// as a symlink: its target is neither read into the file nor written.
    #[cfg(unix)]
    #[test]
    fn a_symlinked_cbmignore_is_replaced_not_written_through() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("repo");
        std::fs::create_dir_all(&root).unwrap();
        let repo = Repo {
            root: root.clone(),
            main_root: root.clone(),
            is_git: false,
        };
        let target = dir.path().join("outside");
        std::fs::write(&target, "keep\n").unwrap();
        std::os::unix::fs::symlink(&target, root.join(".cbmignore")).unwrap();
        write_cbmignore(&repo, &Settings::default()).unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "keep\n");
        assert!(std::fs::symlink_metadata(root.join(".cbmignore")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn a_symlinked_git_exclude_is_left_alone() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("repo");
        std::fs::create_dir_all(&root).unwrap();
        let ok = Command::new("git")
            .args(["init", "-q"])
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(ok.success());
        let exclude = root.join(".git/info/exclude");
        let _ = std::fs::remove_file(&exclude);
        std::fs::create_dir_all(exclude.parent().unwrap()).unwrap();
        let target = dir.path().join("outside");
        std::fs::write(&target, "keep\n").unwrap();
        std::os::unix::fs::symlink(&target, &exclude).unwrap();
        let repo = Repo {
            root: root.clone(),
            main_root: root,
            is_git: true,
        };
        exclude_from_git(&repo, ".cbmignore").unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "keep\n");
    }

    /// `index --wait` supervises in-process and writes no running marker; the session must still
    /// see it, or the child it spawns stands down and the request it took is lost.
    #[test]
    fn a_supervisor_holding_the_lock_counts_as_busy() {
        let dir = tempfile::tempdir().unwrap();
        let repo = Repo {
            root: dir.path().to_path_buf(),
            main_root: dir.path().to_path_buf(),
            is_git: false,
        };
        assert!(!supervisor_busy(&repo));
        // A process another test spawns at that moment shares the lock `supervisor_busy` took
        // until it execs; the claim waits that out.
        let held = (0..100)
            .find_map(|_| {
                let lock = claim_supervisor(&repo.state_dir()).unwrap();
                if lock.is_none() {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                lock
            })
            .expect("the lock is free again");
        assert!(supervisor_busy(&repo));
        drop(held);
        assert!((0..100).any(|_| {
            let busy = supervisor_busy(&repo);
            if busy {
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            !busy
        }));
    }
}
