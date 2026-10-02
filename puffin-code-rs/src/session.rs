//! `puffin-code session`: the process that owns indexing for one repository while a `puffin`
//! session lives (spec §4.2). The launcher starts it outside Codex's sandbox and continues.
//!
//! It takes a lock under the state directory (a second session for the same repository exits at
//! once), keeps `.cbmignore`'s managed block, starts the launch-time run, then every few seconds
//! drains the request queue, coalesces what it finds, and starts at most one supervisor at a time.
//! It exits when its parent `puffin` does; a supervisor in flight finishes on its own.

use std::fs::File;
use std::time::{Duration, Instant};

use anyhow::Result;

use crate::config::Settings;
use crate::index::{self, host::flock, plan};
use crate::paths::Repo;
use crate::requests::{self, Request};

/// How often the queue is drained.
const TICK: Duration = Duration::from_secs(3);

pub fn run(repo: Repo, settings: Settings, parent: Option<i32>) -> Result<()> {
    if !settings.enabled || !repo.is_git {
        return Ok(());
    }
    let state = repo.state_dir();
    std::fs::create_dir_all(&state)?;
    let lock = File::create(state.join("session.lock"))?;
    if flock(&lock, false).is_err() {
        return Ok(()); // another session owns this repository
    }
    let has_index = crate::prompt::ready(&repo);
    if !has_index {
        let _ = std::fs::write(state.join("code_index.building"), "");
    }
    // The launch-time run: universal and static, and executing when trusted and stale.
    let mut pending: Option<Request> = Some(if wants_exact(&repo, &settings) { Request::IndexExact } else { Request::Index });
    let mut last_start: Option<Instant> = None;
    let mut child: Option<std::process::Child> = None;
    loop {
        if let Some(pid) = parent {
            if unsafe { libc::kill(pid, 0) } != 0 {
                return Ok(());
            }
        }
        if let Some(request) = requests::drain(&state) {
            pending = pending.max(Some(request));
        }
        let busy = match child.as_mut() {
            Some(c) => c.try_wait()?.is_none(),
            None => index::running(&repo),
        };
        if !busy {
            child = None;
            let interval_passed = last_start.map(|t| t.elapsed() >= Duration::from_secs(settings.min_interval_s)).unwrap_or(true);
            if let Some(request) = pending {
                if interval_passed || request == Request::IndexExact {
                    pending = None;
                    last_start = Some(Instant::now());
                    let exact = request == Request::IndexExact || wants_exact(&repo, &settings);
                    child = start(&repo, &settings, exact)?;
                    if child.is_none() {
                        // Nothing to run (no tools installed): nothing is being built either.
                        let _ = std::fs::remove_file(state.join("code_index.building"));
                    }
                }
            }
        }
        std::thread::sleep(TICK);
    }
}

/// Whether the executing indexers are due: a trusted repository, or a trusted submodule of it
/// (§4.3), whose exact index is missing or more than `code_index_stale_commits` behind HEAD
/// (spec §6.3).
fn wants_exact(repo: &Repo, settings: &Settings) -> bool {
    let trusted = crate::config::is_trusted(&repo.main_root);
    let decisions = crate::submodules::evaluate(repo, settings);
    // Nothing to weigh in the common case: an untrusted repository without submodules.
    if !trusted && decisions.iter().all(|s| !s.indexed) {
        return false;
    }
    let manifest = crate::manifest::Manifest::load(&repo.scip_dir());
    let targets: Vec<plan::Target> = plan::detect(repo).into_iter().chain(plan::detect_in_submodules(repo, &decisions).0).collect();
    targets.iter().filter(|t| t.kind == index::host::Kind::Executing && plan::untrusted_reason(repo, trusted, &decisions, t).is_none()).any(|target| {
        match manifest.runs.get(&index::store::key(target.indexer, &target.root)).and_then(|e| e.commit.clone()) {
            None => true,
            Some(commit) => crate::paths::git(&repo.root, &["rev-list", "--count", &format!("{commit}..HEAD")])
                .ok()
                .and_then(|n| n.trim().parse::<u64>().ok())
                .map(|n| n > settings.stale_commits)
                .unwrap_or(true),
        }
    })
}

fn start(repo: &Repo, settings: &Settings, exact: bool) -> Result<Option<std::process::Child>> {
    index::write_cbmignore(repo, settings)?;
    let (plan, _) = plan::build(repo, settings, exact);
    if plan.runs.is_empty() {
        return Ok(None);
    }
    index::spawn_supervisor_child(repo, &plan).map(Some)
}
