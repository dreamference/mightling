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
    keep_log(repo, run, &log);
    // A scope stopped from outside (`systemctl --user stop`, which `server start` sends before its
    // pre-flight) ends on SIGTERM: the run did nothing wrong and is retried, with no peak recorded.
    if stopped_from_outside(&status) {
        return Ok(Outcome::Deferred("stopped"));
    }
    // Only a run killed by the kernel records its cap as a lower bound on its peak (§6.4); a run
    // that failed for any other reason keeps what it measured, or the next admission would ask
    // for more than its ceiling and never start it again. A SIGKILL whose measured peak stayed
    // well under the cap was not the cap's doing either.
    let killed = matches!(status.code(), Some(137) | None) && (peak == 0 || bounded);
    let peak_mb = if killed { peak.max(cap) >> 20 } else { peak >> 20 };
    if !status.success() {
        let log_text = std::fs::read_to_string(&log).unwrap_or_default();
        let why = match status.code() {
            _ if killed => "killed (memory cap reached?)".to_string(),
            Some(137) | None => "killed".to_string(),
            // scip-java runs the project's build tool: the wrapper's, or the installed one.
            _ if missing_build_tool(&log_text).is_some() => missing_build_tool(&log_text).unwrap_or_default(),
            // Nothing is fetched on an indexer's behalf (§9.1): a missing dependency is the
            // accepted outcome, recorded as such rather than retried.
            _ if is_offline_failure(&log_text) => "offline".to_string(),
            // GOTOOLCHAIN=local refuses to download the Go a go.mod asks for.
            _ if log_text.contains("requires go >=") && log_text.contains("GOTOOLCHAIN=local") => {
                "go.mod needs a newer Go than the one recorded (upgrade Go, then `puffin-admin code setup`)".to_string()
            }
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
            let (Some(scip_file), Some(converted)) = (run.scip_output.as_ref(), run.converted.as_ref()) else {
                return Ok(Outcome::Failed("the plan names no index to install".into()));
            };
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

/// The build tool scip-java could not start, as a reason with its remedy: the project has no
/// wrapper whose distribution is on disk, and the tool itself is not installed.
pub fn missing_build_tool(log: &str) -> Option<String> {
    [("mvn", "Maven"), ("gradle", "Gradle")].iter().find(|(program, _)| log.contains(&format!("Cannot run program \"{program}\""))).map(|(program, name)| {
        format!("{name} is not installed, and the project has no wrapper with its distribution on disk (install {name} so `{program}` is on PATH, then `puffin-admin code setup`)")
    })
}

/// Whether a run's log says it needed the network.
pub fn is_offline_failure(log: &str) -> bool {
    [
        // Cargo: "attempting to make an HTTP request, but --offline was specified". Not the bare
        // flag: the Maven command line scip-java echoes carries `--offline` on every run, and a
        // compile error there is not a missing dependency.
        "--offline was specified",
        "failed to download",
        "network",
        "Could not resolve host",
        "ENOTFOUND",
        "Temporary failure in name resolution",
        "Name or service not known",
        "offline mode",
        // Go with GOPROXY=off.
        "module lookup disabled by GOPROXY=off",
        // Gradle's wrapper and Maven or Gradle reaching for a repository.
        "UnknownHostException",
        "Could not install Gradle distribution",
        // NuGet: a package or a version that is not in the local folder, or a feed it cannot reach.
        "NU1100",
        "NU1101",
        "NU1102",
        "NU1301",
    ]
        .iter()
        .any(|needle| log.contains(needle))
}

/// Whether a run ended because its scope was stopped from outside, not by the kernel or by
/// itself: SIGTERM, seen directly or as the 143 a shell or bwrap reports for a child it took.
#[cfg(unix)]
fn stopped_from_outside(status: &std::process::ExitStatus) -> bool {
    use std::os::unix::process::ExitStatusExt;
    status.code() == Some(143) || status.signal() == Some(15)
}

#[cfg(not(unix))]
fn stopped_from_outside(status: &std::process::ExitStatus) -> bool {
    status.code() == Some(143)
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

    /// A scripted sequence of states, then idle.
    struct Script(std::cell::RefCell<Vec<ModelState>>);
    impl Probe for Script {
        fn state(&self) -> ModelState {
            let mut states = self.0.borrow_mut();
            if states.is_empty() { ModelState::Idle } else { states.remove(0) }
        }
    }

    #[test]
    fn an_executing_run_is_frozen_while_the_model_works_and_thawed_after() {
        let dir = tempfile::tempdir().unwrap();
        let mut host = FakeHost::new(dir.path(), 100);
        host.stand_in = vec!["sleep".into(), "1".into()];
        let mut plan = repo_and_plan(dir.path(), Kind::Executing, 1);
        plan.runs[0].ceiling = 40 << 30;
        // Idle to start, then busy for three polls, then idle.
        let probe = Script(std::cell::RefCell::new(vec![ModelState::Idle, ModelState::Busy, ModelState::Busy, ModelState::Busy]));
        execute(&plan, &host, &probe, Duration::from_millis(40));
        let actions = host.actions.lock().unwrap().clone();
        assert_eq!(actions, vec!["freeze puffin-index-test".to_string(), "thaw puffin-index-test".to_string()], "{actions:?}");
    }

    #[test]
    fn a_short_run_is_never_frozen() {
        let dir = tempfile::tempdir().unwrap();
        let mut host = FakeHost::new(dir.path(), 100);
        host.stand_in = vec!["sleep".into(), "0.5".into()];
        let plan = repo_and_plan(dir.path(), Kind::Universal, 0);
        let probe = Script(std::cell::RefCell::new(vec![ModelState::Idle, ModelState::Busy, ModelState::Busy]));
        execute(&plan, &host, &probe, Duration::from_millis(40));
        assert!(host.actions.lock().unwrap().is_empty());
    }

    #[test]
    fn a_scope_stopped_from_outside_is_deferred_and_records_no_peak() {
        let dir = tempfile::tempdir().unwrap();
        for stand_in in ["exit 143", "kill -TERM $$"] {
            let mut host = FakeHost::new(dir.path(), 100);
            host.stand_in = vec!["sh".into(), "-c".into(), stand_in.into()];
            let mut plan = repo_and_plan(dir.path(), Kind::Static, 1);
            plan.runs[0].indexer = "scip-python".into();
            plan.runs[0].root = "pkg".into();
            let out = execute(&plan, &host, &FixedProbe(ModelState::Absent), Duration::from_millis(1));
            assert_eq!(out[0].1, Outcome::Deferred("stopped"), "{stand_in}");
            let repo = Repo { root: plan.repo_root.clone(), main_root: plan.main_root.clone(), is_git: false };
            let entry = &crate::manifest::Manifest::load(&repo.scip_dir()).runs["scip-python:pkg"];
            assert_eq!(entry.status, "deferred: stopped", "{stand_in}");
            assert_eq!((entry.peak_rss_mb, entry.peak_cap_bounded), (0, false), "{stand_in}");
        }
    }

    #[test]
    fn a_kernel_kill_still_records_the_cap_as_the_peak() {
        let dir = tempfile::tempdir().unwrap();
        let mut host = FakeHost::new(dir.path(), 100);
        host.stand_in = vec!["sh".into(), "-c".into(), "exit 137".into()];
        let mut plan = repo_and_plan(dir.path(), Kind::Static, 1);
        plan.runs[0].indexer = "scip-python".into();
        plan.runs[0].root = "pkg".into();
        let out = execute(&plan, &host, &FixedProbe(ModelState::Absent), Duration::from_millis(1));
        assert_eq!(out[0].1, Outcome::Failed("killed (memory cap reached?)".into()));
        let repo = Repo { root: plan.repo_root.clone(), main_root: plan.main_root.clone(), is_git: false };
        let entry = &crate::manifest::Manifest::load(&repo.scip_dir()).runs["scip-python:pkg"];
        assert!(entry.peak_cap_bounded && entry.peak_rss_mb >= 1024, "{entry:?}");
    }

    #[test]
    fn a_run_in_flight_is_stopped_when_a_model_starts_loading() {
        let dir = tempfile::tempdir().unwrap();
        let mut host = FakeHost::new(dir.path(), 100);
        host.stand_in = vec!["sleep".into(), "0.5".into()];
        let plan = repo_and_plan(dir.path(), Kind::Executing, 1);
        let probe = Script(std::cell::RefCell::new(vec![ModelState::Idle, ModelState::Loading]));
        let out = execute(&plan, &host, &probe, Duration::from_millis(40));
        assert_eq!(out[0].1, Outcome::Deferred("model-start"));
        assert_eq!(*host.actions.lock().unwrap(), vec!["stop puffin-index-test".to_string()]);
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

    #[test]
    fn a_maven_build_that_fails_for_another_reason_is_not_offline() {
        let log = "$ mvn -Dmaven.compiler.fork=true --batch-mode --offline -DskipTests clean verify\n[ERROR] COMPILATION ERROR : \n[ERROR] /src/A.java:[3,5] cannot find symbol\n[INFO] BUILD FAILURE";
        assert!(!super::is_offline_failure(log));
        assert_eq!(super::missing_build_tool(log), None);
    }

    #[test]
    fn a_missing_build_tool_is_named_with_its_remedy() {
        let log = "$ mvn --batch-mode --offline clean verify\nException in thread \"main\" java.io.IOException: Cannot run program \"mvn\" (in directory \"/s/src\"): Exec failed, error: 2 (No such file or directory)";
        let why = super::missing_build_tool(log).unwrap();
        assert!(why.starts_with("Maven is not installed") && why.contains("puffin-admin code setup"), "{why}");
        let log = "Exception in thread \"main\" java.io.IOException: Cannot run program \"gradle\" (in directory \"/s/src\")";
        assert!(super::missing_build_tool(log).unwrap().starts_with("Gradle is not installed"));
    }

    #[test]
    fn the_language_toolchains_offline_messages() {
        for log in [
            "go: github.com/google/uuid@v1.6.0: module lookup disabled by GOPROXY=off",
            "[ERROR] Cannot access central (https://repo.maven.apache.org/maven2) in offline mode",
            "Could not resolve all files: No cached version of org.slf4j:slf4j-api:2.0.9 available for offline mode.",
            "Exception in thread \"main\" java.net.UnknownHostException: services.gradle.org",
            "error NU1101: Unable to find package Newtonsoft.Json. No packages exist with this id in source(s): local",
            "error NU1100: Unable to resolve 'Microsoft.WindowsDesktop.App.Ref (= 8.0.31)' for 'net8.0'.",
            "[ERROR] Cannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact org.apache.maven.plugins:maven-clean-plugin:jar:3.2.0 has not been downloaded from it before.",
            "> Could not GET 'https://repo.maven.apache.org/maven2/org/apache/commons/commons-lang3/3.14.0/commons-lang3-3.14.0.pom'.\n   > repo.maven.apache.org: Temporary failure in name resolution",
        ] {
            assert!(super::is_offline_failure(log), "{log}");
        }
    }
}
