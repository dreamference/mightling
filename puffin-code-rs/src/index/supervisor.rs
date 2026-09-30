//! The supervisor: runs a plan's indexers one after another, outside the sandbox, and keeps them
//! from competing with the model (spec §9.2) or the host (spec §6.4).
//!
//! For each run it waits for the model to be idle, admits the run against the host-wide budget,
//! records the snapshot (commit, dirty files, hashes) the run is about to see, starts it in a scope
//! of `puffin-index.slice` through `choom -n 1000` and bwrap, polls the model every 2 s to freeze,
//! thaw or stop the scope, reads the cgroup's peak (written by the run itself before it exits,
//! because the cgroup is gone once it has), and installs the output or records why there is none.

use std::path::Path;
use std::time::{Duration, Instant};

use anyhow::Result;

use super::host::{self, Host, Kind};
use super::plan::{sh_quote, Plan, Run};
use super::probe::{ModelState, Probe};
use super::sandbox;
use super::store;
use crate::manifest::{now_rfc3339, GraphSnapshot, RunEntry};
use crate::paths::Repo;

/// How often the model is polled while a run is in flight.
pub const POLL: Duration = Duration::from_secs(2);
/// A frozen run is resumed after the model has been idle this long.
const THAW_AFTER: Duration = Duration::from_secs(10);
/// A run frozen longer than this is stopped (`deferred: busy`).
const FROZEN_LIMIT: Duration = Duration::from_secs(30 * 60);

/// The outcome of one run, as recorded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Ok,
    Failed(String),
    Deferred(&'static str),
}

impl Outcome {
    pub fn status(&self) -> String {
        match self {
            Outcome::Ok => "ok".into(),
            Outcome::Failed(why) => format!("failed: {why}"),
            Outcome::Deferred(why) => format!("deferred: {why}"),
        }
    }
}

/// Runs every run of a plan, in order.
pub fn execute(plan: &Plan, host: &dyn Host, probe: &dyn Probe, poll: Duration) -> Vec<(String, Outcome)> {
    let repo = Repo { root: plan.repo_root.clone(), main_root: plan.main_root.clone(), is_git: plan.repo_root.join(".git").exists() };
    let mut outcomes = Vec::new();
    for run in &plan.runs {
        let outcome = execute_one(&repo, plan, run, host, probe, poll).unwrap_or_else(|e| Outcome::Failed(format!("{e:#}")));
        let _ = record(&repo, run, &outcome);
        outcomes.push((run.unit.clone(), outcome));
    }
    outcomes
}

fn execute_one(repo: &Repo, plan: &Plan, run: &Run, host: &dyn Host, probe: &dyn Probe, poll: Duration) -> Result<Outcome> {
    // Start only when the model is idle, never alongside a load.
    let waited = Instant::now();
    loop {
        match probe.state() {
            ModelState::Idle | ModelState::Absent => break,
            ModelState::Loading if waited.elapsed() >= Duration::from_secs(plan.wait_idle_s) => return Ok(Outcome::Deferred("model-start")),
            ModelState::Busy if waited.elapsed() >= Duration::from_secs(plan.wait_idle_s) => return Ok(Outcome::Deferred("busy")),
            _ => std::thread::sleep(poll),
        }
    }
    let _executing = if run.kind == Kind::Executing {
        match host::executing_lock(host)? {
            Some(lock) => Some(lock),
            None => return Ok(Outcome::Deferred("busy")),
        }
    } else {
        None
    };
    let admitted = match host::admit(host, run.need, run.ceiling)? {
        Ok(admitted) => admitted,
        Err(_) => return Ok(Outcome::Deferred("memory")),
    };
    let cap = admitted.cap;

    // The snapshot is what the run is about to see.
    let commit = repo.head();
    let dirty = store::dirty_files(repo);
    // Every file, not only the root's: the store will hold documents for what the root imports.
    let hashes = if run.kind == Kind::Universal { Default::default() } else { store::stamp_root(repo, "") };
    let started_at = now_rfc3339();

    std::fs::create_dir_all(&run.spec.scratch)?;
    let peak_file = run.spec.scratch.join("peak");
    let _ = std::fs::remove_file(&peak_file);
    let log = run.spec.scratch.join("run.log");
    let argv = scope_argv(run, &peak_file);
    let clock = Instant::now();
    let mut child = host.start_scope(&run.unit, cap, &argv, &log)?;
    admitted.release();

    let mut frozen_since: Option<Instant> = None;
    let mut idle_since: Option<Instant> = None;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        match probe.state() {
            ModelState::Loading => {
                // Frozen memory stays resident: a load needs it gone, so the run is stopped.
                let _ = host.stop(&run.unit);
                let _ = child.wait();
                return Ok(Outcome::Deferred("model-start"));
            }
            // Only the long, executing runs are frozen. A frozen codebase-memory missed its own
            // daemon's 30 s start-up deadline and failed (2026-09-30); the universal and static
            // runs take seconds, and the slice's CPU limit already bounds them.
            ModelState::Busy if run.kind != Kind::Executing => idle_since = None,
            ModelState::Busy => {
                idle_since = None;
                if frozen_since.is_none() && host.freeze(&run.unit).is_ok() {
                    frozen_since = Some(Instant::now());
                }
            }
            ModelState::Idle | ModelState::Absent => {
                let since = *idle_since.get_or_insert_with(Instant::now);
                if frozen_since.is_some() && since.elapsed() >= THAW_AFTER.min(poll * 5) && host.thaw(&run.unit).is_ok() {
                    frozen_since = None;
                }
            }
        }
        if frozen_since.map(|t| t.elapsed() >= FROZEN_LIMIT).unwrap_or(false) {
            let _ = host.stop(&run.unit);
            let _ = child.wait();
            return Ok(Outcome::Deferred("busy"));
        }
        std::thread::sleep(poll);
    };
    let duration = clock.elapsed().as_secs_f64();
    let peak = std::fs::read_to_string(&peak_file).ok().and_then(|t| t.trim().parse::<u64>().ok()).unwrap_or(0);
    let bounded = peak > 0 && peak >= cap / 10 * 9;
    // Only a run killed by the kernel records its cap as a lower bound on its peak (§6.4); a run
    // that failed for any other reason keeps what it measured, or the next admission would ask
    // for more than its ceiling and never start it again.
    let killed = matches!(status.code(), Some(137) | None);
    let peak_mb = if killed { peak.max(cap) >> 20 } else { peak >> 20 };
    keep_log(repo, run, &log);
    if !status.success() {
        let log_text = std::fs::read_to_string(&log).unwrap_or_default();
        let why = match status.code() {
            Some(137) | None => "killed (memory cap reached?)".to_string(),
            // Nothing is fetched on an indexer's behalf (§9.1): a missing dependency is the
            // accepted outcome, recorded as such rather than retried.
            _ if is_offline_failure(&log_text) => "offline".to_string(),
            _ if log_text.contains("registry/cache") && log_text.contains("Read-only file system") => {
                "dependencies not unpacked (run `cargo fetch` in the crate once)".to_string()
            }
            Some(code) => format!("exit {code}; see {}", repo.state_dir().join("logs").join(format!("{}.log", run.unit)).display()),
        };
        // A killed run records its cap as the lower bound, so the next attempt is not doomed the
        // same way (§6.4).
        let status_text = Outcome::Failed(why.clone()).status();
        if run.kind == Kind::Universal {
            let mut snapshot = GraphSnapshot::load(&repo.scip_dir()).unwrap_or_default();
            snapshot.status = status_text;
            if killed {
                snapshot.peak_rss_mb = peak_mb;
                snapshot.peak_cap_bounded = true;
            }
            snapshot.save(&repo.scip_dir())?;
        } else {
            store::record_outcome(repo, &run.indexer, &run.root, &status_text, |e| {
                if killed {
                    e.peak_rss_mb = peak_mb;
                    e.peak_cap_bounded = true;
                }
                e.cap_mb = cap >> 20;
            })?;
        }
        return Ok(Outcome::Failed(why));
    }
    match run.kind {
        Kind::Universal => {
            GraphSnapshot {
                commit,
                dirty_files: dirty,
                finished: now_rfc3339(),
                status: "ok".into(),
                peak_rss_mb: peak_mb,
                peak_cap_bounded: bounded,
            }
            .save(&repo.scip_dir())?;
        }
        _ => {
            let (scip_file, converted) = (run.scip_output.as_ref().unwrap(), run.converted.as_ref().unwrap());
            if !converted.is_file() || !scip_file.is_file() {
                return Ok(Outcome::Failed("the indexer wrote no index".into()));
            }
            let entry = RunEntry {
                indexer: run.indexer.clone(),
                root: run.root.clone(),
                path_prefix: run.path_prefix.clone(),
                version: run.version.clone(),
                commit,
                dirty_files: dirty,
                file_hashes: hashes,
                started: started_at,
                duration_s: duration,
                peak_rss_mb: peak_mb,
                peak_cap_bounded: bounded,
                cap_mb: cap >> 20,
                status: "ok".into(),
                ..RunEntry::default()
            };
            // Post-processing reads the .scip outside the sandbox; a malformed file fails here and
            // the last good store stays in place.
            store::install(repo, entry, converted, scip_file)?;
        }
    }
    Ok(Outcome::Ok)
}

/// Whether a run's log says it needed the network.
pub fn is_offline_failure(log: &str) -> bool {
    ["--offline", "failed to download", "network", "Could not resolve host", "ENOTFOUND", "offline mode"]
        .iter()
        .any(|needle| log.contains(needle))
}

/// `choom -n 1000 -- nice -n 10 ionice -c3 bwrap … -- sh -c '<run>; write the cgroup's peak'`.
///
/// The peak is written by the run itself, inside its cgroup, because the cgroup is removed with
/// the scope and a supervisor polling from outside would record a lower bound.
pub fn scope_argv(run: &Run, peak_file: &Path) -> Vec<String> {
    let mut spec = run.spec.clone();
    let inner = spec.argv.clone();
    let quoted: Vec<String> = inner.iter().map(|a| sh_quote(a)).collect();
    spec.argv = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!(
            "{}; rc=$?; cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.peak > {} 2>/dev/null; exit $rc",
            quoted.join(" "),
            sh_quote(&peak_file.to_string_lossy())
        ),
    ];
    let mut argv: Vec<String> = ["choom", "-n", "1000", "--", "nice", "-n", "10", "ionice", "-c3"].iter().map(|s| s.to_string()).collect();
    argv.extend(sandbox::bwrap_args(&spec));
    argv
}

fn keep_log(repo: &Repo, run: &Run, log: &Path) {
    let dir = repo.state_dir().join("logs");
    if std::fs::create_dir_all(&dir).is_ok() {
        let _ = std::fs::copy(log, dir.join(format!("{}.log", run.unit)));
    }
}

fn record(repo: &Repo, run: &Run, outcome: &Outcome) -> Result<()> {
    match (run.kind, outcome) {
        (_, Outcome::Ok) | (_, Outcome::Failed(_)) => Ok(()), // recorded where it happened, with the peak
        (Kind::Universal, _) => {
            let mut snapshot = GraphSnapshot::load(&repo.scip_dir()).unwrap_or_default();
            snapshot.status = outcome.status();
            snapshot.save(&repo.scip_dir())
        }
        (_, Outcome::Deferred(_)) => store::record_outcome(repo, &run.indexer, &run.root, &outcome.status(), |_| {}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::host::fake::FakeHost;
    use crate::index::plan::{Plan, Run};
    use crate::index::sandbox::Spec;
    use std::cell::Cell;

    struct FixedProbe(ModelState);
    impl Probe for FixedProbe {
        fn state(&self) -> ModelState {
            self.0
        }
    }

    /// Busy for the first `n` polls, then idle.
    struct BusyThenIdle(Cell<u32>);
    impl Probe for BusyThenIdle {
        fn state(&self) -> ModelState {
            let left = self.0.get();
            if left == 0 {
                ModelState::Idle
            } else {
                self.0.set(left - 1);
                ModelState::Busy
            }
        }
    }

    fn repo_and_plan(dir: &Path, kind: Kind, need_gib: u64) -> Plan {
        let root = dir.join("repo");
        std::fs::create_dir_all(&root).unwrap();
        Plan {
            repo_root: root.clone(),
            main_root: root.clone(),
            state_dir: root.join(".dreamference"),
            vllm_host: "http://127.0.0.1:9".into(),
            wait_idle_s: 0,
            runs: vec![Run {
                kind,
                indexer: "codebase-memory".into(),
                version: "test".into(),
                root: String::new(),
                path_prefix: String::new(),
                spec: Spec { home: dir.join("home"), scratch: dir.join("scratch"), cwd: root, argv: vec!["true".into()], ..Spec::default() },
                scip_output: None,
                converted: None,
                need: need_gib << 30,
                ceiling: 4 << 30,
                unit: "puffin-index-test".into(),
            }],
        }
    }

    #[test]
    fn no_run_starts_while_the_model_is_busy() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 100);
        let plan = repo_and_plan(dir.path(), Kind::Universal, 0);
        let out = execute(&plan, &host, &FixedProbe(ModelState::Busy), Duration::from_millis(1));
        assert_eq!(out[0].1, Outcome::Deferred("busy"));
        assert!(host.started.lock().unwrap().is_empty());
    }

    #[test]
    fn no_run_starts_while_a_model_loads() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 100);
        let plan = repo_and_plan(dir.path(), Kind::Universal, 0);
        let out = execute(&plan, &host, &FixedProbe(ModelState::Loading), Duration::from_millis(1));
        assert_eq!(out[0].1, Outcome::Deferred("model-start"));
        assert!(host.started.lock().unwrap().is_empty());
    }

    #[test]
    fn a_run_waits_for_idle_then_starts_in_the_slice_through_choom() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 100);
        let mut plan = repo_and_plan(dir.path(), Kind::Universal, 0);
        plan.wait_idle_s = 60;
        execute(&plan, &host, &BusyThenIdle(Cell::new(3)), Duration::from_millis(1));
        let started = host.started.lock().unwrap();
        assert_eq!(started.len(), 1);
        let (unit, cap, argv) = &started[0];
        assert_eq!(unit, "puffin-index-test");
        assert_eq!(*cap, 4 << 30);
        assert_eq!(&argv[..4], ["choom", "-n", "1000", "--"]);
        assert!(argv.contains(&"bwrap".to_string()) && argv.contains(&"--unshare-net".to_string()));
        assert!(host.slice_max.lock().unwrap().is_some());
    }

    #[test]
    fn a_budget_below_the_need_defers_and_records_it() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 12); // ~2 GiB above the reserve
        let mut plan = repo_and_plan(dir.path(), Kind::Static, 3);
        plan.runs[0].indexer = "scip-python".into();
        plan.runs[0].root = "pkg".into();
        let out = execute(&plan, &host, &FixedProbe(ModelState::Absent), Duration::from_millis(1));
        assert_eq!(out[0].1, Outcome::Deferred("memory"));
        assert!(host.started.lock().unwrap().is_empty());
        let repo = Repo { root: plan.repo_root.clone(), main_root: plan.main_root.clone(), is_git: false };
        let manifest = crate::manifest::Manifest::load(&repo.scip_dir());
        assert_eq!(manifest.runs["scip-python:pkg"].status, "deferred: memory");
    }

    #[test]
    fn the_peak_is_written_by_the_run_itself() {
        let dir = tempfile::tempdir().unwrap();
        let plan = repo_and_plan(dir.path(), Kind::Universal, 0);
        let argv = scope_argv(&plan.runs[0], &dir.path().join("scratch/peak"));
        let script = argv.last().unwrap();
        assert!(script.contains("memory.peak") && script.contains("exit $rc"), "{script}");
    }
}

#[cfg(test)]
mod offline_tests {
    #[test]
    fn a_missing_dependency_reads_as_offline() {
        let log = "error: failed to get `serde` as a dependency of package `x`\n\nCaused by:\n  failed to download from registry\n  attempting to make an HTTP request, but --offline was specified";
        assert!(super::is_offline_failure(log));
        assert!(!super::is_offline_failure("error[E0308]: mismatched types"));
    }
}
