//! `puffin-code session`: the process that owns indexing for one repository while a `puffin`
//! session lives (spec §4.2). The launcher starts it outside Codex's sandbox and continues.
//!
//! It takes a lock under the state directory (a second session for the same repository exits at
//! once), keeps `.cbmignore`'s managed block, starts the launch-time run, then every few seconds
//! drains the request queue, coalesces what it finds, and starts at most one supervisor at a time.
//! While nothing is pending it also watches the model server, and asks for the executing indexers
//! itself when the server is stopped or has served nothing for a while (spec §6.3).
//! It exits when its parent `puffin` does; a supervisor in flight finishes on its own.

use std::fs::File;
use std::time::{Duration, Instant};

use anyhow::Result;

use crate::config::Settings;
use crate::index::probe::{HttpProbe, ModelState, Probe};
use crate::index::{self, host::flock, plan};
use crate::paths::Repo;
use crate::requests::{self, Request};

/// How often the queue is drained.
const TICK: Duration = Duration::from_secs(3);

/// The model server is asked for its state every this many ticks. With nothing answering, the
/// probe asks docker for a loading container, a fork the session should not make every 3 s.
const PROBE_EVERY: u32 = 10;

/// Consecutive probes that must find no model server before it counts as stopped: `server start`
/// passes through a moment with nothing answering and no container yet.
const ABSENT_PROBES: u32 = 2;

/// How long a resident model must serve nothing before the executing indexers start beside it.
/// The supervisor waits as long for an idle model (`Plan::wait_idle_s`).
const IDLE_FOR: Duration = Duration::from_secs(600);

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
    let probe = HttpProbe::new(&settings.vllm_host);
    let mut trigger = IdleTrigger::default();
    let mut ticks: u32 = 0;
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
        // Probed during a run too, so the idle clock never spans a stretch nobody watched.
        let quiet = ticks % PROBE_EVERY == 0 && trigger.observe(probe.state(), Instant::now());
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
            } else if quiet {
                // Cheap checks first: HEAD, then the trust and staleness test, which runs git.
                let interval = Duration::from_secs(settings.min_interval_s);
                if let Some(head) = repo.head() {
                    if trigger.may_claim(&head, last_start, interval, Instant::now()) && exact_due(&repo, &settings, 0) {
                        trigger.claim(head);
                        pending = Some(Request::IndexExact);
                    }
                }
            }
        }
        ticks = ticks.wrapping_add(1);
        std::thread::sleep(TICK);
    }
}

/// When the executing indexers may run without being asked (spec §6.3): the model server has
/// been stopped for two probes, or has served nothing for [`IDLE_FOR`].
///
/// At most once per `HEAD` in a session: a run that fails or is deferred records its status but
/// not a commit (the commit is the snapshot of a run that finished), so its index stays "behind"
/// and would otherwise be asked for again on every probe.
#[derive(Debug, Default)]
struct IdleTrigger {
    absent_probes: u32,
    idle_since: Option<Instant>,
    claimed: Option<String>,
}

impl IdleTrigger {
    /// Records one probe; whether the model has been quiet long enough for a run.
    fn observe(&mut self, state: ModelState, now: Instant) -> bool {
        match state {
            ModelState::Absent => {
                self.absent_probes = self.absent_probes.saturating_add(1);
                self.idle_since = None;
                self.absent_probes >= ABSENT_PROBES
            }
            ModelState::Idle => {
                self.absent_probes = 0;
                now.duration_since(*self.idle_since.get_or_insert(now)) >= IDLE_FOR
            }
            ModelState::Busy | ModelState::Loading => {
                self.absent_probes = 0;
                self.idle_since = None;
                false
            }
        }
    }

    /// Whether a run may be asked for at `head`: not already asked for at that commit in this
    /// session, and no run started less than `interval` ago.
    fn may_claim(&self, head: &str, last_start: Option<Instant>, interval: Duration, now: Instant) -> bool {
        self.claimed.as_deref() != Some(head) && last_start.map(|t| now.duration_since(t) >= interval).unwrap_or(true)
    }

    fn claim(&mut self, head: String) {
        self.claimed = Some(head);
    }
}

/// Whether the executing indexers are due at launch: a trusted repository, or a trusted submodule
/// of it (§4.3), whose exact index is missing or more than `code_index_stale_commits` behind HEAD
/// (spec §6.3).
fn wants_exact(repo: &Repo, settings: &Settings) -> bool {
    exact_due(repo, settings, settings.stale_commits)
}

/// Whether an executing root this session may index has no exact index, or one more than
/// `behind` commits older than HEAD. The idle trigger passes 0: with the model quiet, any
/// commit since the snapshot is worth a run.
fn exact_due(repo: &Repo, settings: &Settings, behind: u64) -> bool {
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
                .map(|n| n > behind)
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

#[cfg(test)]
mod tests {
    use super::*;

    const HOUR: Duration = Duration::from_secs(3600);

    #[test]
    fn a_stopped_server_counts_after_two_probes() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        assert!(!trigger.observe(ModelState::Absent, now));
        assert!(trigger.observe(ModelState::Absent, now + Duration::from_secs(30)));
    }

    #[test]
    fn a_server_starting_between_two_absent_probes_resets_the_count() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        assert!(!trigger.observe(ModelState::Absent, now));
        assert!(!trigger.observe(ModelState::Loading, now));
        assert!(!trigger.observe(ModelState::Absent, now));
    }

    #[test]
    fn an_idle_model_counts_only_after_the_idle_period() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        assert!(!trigger.observe(ModelState::Idle, now));
        assert!(!trigger.observe(ModelState::Idle, now + IDLE_FOR - Duration::from_secs(1)));
        assert!(trigger.observe(ModelState::Idle, now + IDLE_FOR));
    }

    #[test]
    fn a_request_restarts_the_idle_clock() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        trigger.observe(ModelState::Idle, now);
        assert!(!trigger.observe(ModelState::Busy, now + IDLE_FOR / 2));
        assert!(!trigger.observe(ModelState::Idle, now + IDLE_FOR));
        assert!(trigger.observe(ModelState::Idle, now + IDLE_FOR / 2 + IDLE_FOR + IDLE_FOR / 2));
    }

    #[test]
    fn a_loading_model_never_counts() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        for i in 0..5 {
            assert!(!trigger.observe(ModelState::Loading, now + HOUR * i));
        }
    }

    #[test]
    fn one_run_per_head() {
        let mut trigger = IdleTrigger::default();
        let now = Instant::now();
        assert!(trigger.may_claim("a", None, HOUR, now));
        trigger.claim("a".into());
        assert!(!trigger.may_claim("a", None, HOUR, now + HOUR * 5));
        assert!(trigger.may_claim("b", None, HOUR, now + HOUR * 5));
    }

    #[test]
    fn no_run_within_the_interval_of_the_last() {
        let trigger = IdleTrigger::default();
        let now = Instant::now();
        assert!(!trigger.may_claim("a", Some(now), HOUR, now + HOUR / 2));
        assert!(trigger.may_claim("a", Some(now), HOUR, now + HOUR));
    }
}
