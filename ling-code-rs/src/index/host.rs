//! The host: memory, cgroups and systemd, behind a trait so tests never create a real scope or
//! freeze a real unit (spec §12, the way `tests/conftest.py` refuses real mutating `docker`
//! commands), and host-wide admission on top of it (spec §6.4).

use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use anyhow::{bail, Context, Result};

/// The slice every index run joins; its limits hold for the sum of the runs.
pub const SLICE: &str = "mightling-index.slice";
const GIB: u64 = 1 << 30;
const MIB: u64 = 1 << 20;

/// A live index run: its unit, memory cap and current use, in bytes.
#[derive(Debug, Clone)]
pub struct LiveScope {
    pub unit: String,
    pub cap: u64,
    pub current: u64,
}

pub trait Host {
    fn mem_available(&self) -> u64;
    fn mem_total(&self) -> u64;
    /// earlyoom's `-m` percentage, if earlyoom runs.
    fn earlyoom_percent(&self) -> Option<f64>;
    /// The scopes currently in [`SLICE`].
    fn live_scopes(&self) -> Vec<LiveScope>;
    /// Sets the slice's aggregate limits (memory, and CPU on first use).
    fn set_slice_limits(&self, memory_max: u64) -> Result<()>;
    /// Whether this process can reach the user's systemd (false inside Codex's sandbox).
    fn can_create_scopes(&self) -> bool;
    /// Starts `argv` in a new scope of [`SLICE`] with its own memory cap.
    fn start_scope(&self, unit: &str, cap: u64, argv: &[String], log: &Path) -> Result<Child>;
    fn freeze(&self, unit: &str) -> Result<()>;
    fn thaw(&self, unit: &str) -> Result<()>;
    fn stop(&self, unit: &str) -> Result<()>;
    /// Where the host-wide locks live.
    fn lock_dir(&self) -> PathBuf;
}

/// The real host: `/proc`, `/sys/fs/cgroup` and `systemctl --user`.
pub struct SystemdHost;

fn meminfo(key: &str) -> u64 {
    std::fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|l| l.starts_with(key))
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|v| v.parse::<u64>().ok())
        })
        .map(|kb| kb * 1024)
        .unwrap_or(0)
}

fn slice_cgroup() -> PathBuf {
    #[cfg(unix)]
    // SAFETY: getuid cannot fail and has no side effects.
    let uid = unsafe { libc::getuid() };
    #[cfg(not(unix))]
    let uid = 0;
    PathBuf::from(format!(
        "/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/mightling.slice/{SLICE}"
    ))
}

fn read_u64(path: &Path) -> Option<u64> {
    let text = std::fs::read_to_string(path).ok()?;
    let text = text.trim();
    if text == "max" {
        return Some(u64::MAX);
    }
    text.parse().ok()
}

fn systemctl(args: &[&str]) -> Result<()> {
    let status = Command::new("systemctl").arg("--user").args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status()?;
    if !status.success() {
        bail!("systemctl --user {} failed", args.join(" "));
    }
    Ok(())
}

impl Host for SystemdHost {
    fn mem_available(&self) -> u64 {
        meminfo("MemAvailable:")
    }

    fn mem_total(&self) -> u64 {
        meminfo("MemTotal:")
    }

    fn earlyoom_percent(&self) -> Option<f64> {
        for entry in std::fs::read_dir("/proc").ok()?.flatten() {
            let Ok(cmdline) = std::fs::read(entry.path().join("cmdline")) else { continue };
            let args: Vec<String> = cmdline.split(|b| *b == 0).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
            if !args.first().map(|a| a.ends_with("earlyoom")).unwrap_or(false) {
                continue;
            }
            let value = args.iter().position(|a| a == "-m").and_then(|i| args.get(i + 1)).cloned();
            // `-m 5` or `-m 5,2` (SIGTERM, SIGKILL); earlyoom's default is 10.
            return Some(value.and_then(|v| v.split(',').next().and_then(|p| p.parse().ok())).unwrap_or(10.0));
        }
        None
    }

    fn live_scopes(&self) -> Vec<LiveScope> {
        let Ok(entries) = std::fs::read_dir(slice_cgroup()) else { return Vec::new() };
        entries
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().ends_with(".scope"))
            .filter_map(|e| {
                let current = read_u64(&e.path().join("memory.current"))?;
                let cap = read_u64(&e.path().join("memory.max"))?;
                Some(LiveScope { unit: e.file_name().to_string_lossy().into_owned(), cap, current })
            })
            .collect()
    }

    fn set_slice_limits(&self, memory_max: u64) -> Result<()> {
        let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4);
        let allowed = if cpus > 4 { format!("{}-{}", cpus - 4, cpus - 1) } else { format!("0-{}", cpus - 1) };
        systemctl(&[
            "set-property",
            "--runtime",
            SLICE,
            &format!("MemoryMax={memory_max}"),
            "MemorySwapMax=0",
            "CPUQuota=400%",
            &format!("AllowedCPUs={allowed}"),
        ])
    }

    fn can_create_scopes(&self) -> bool {
        systemctl(&["show-environment"]).is_ok()
    }

    fn start_scope(&self, unit: &str, cap: u64, argv: &[String], log: &Path) -> Result<Child> {
        let log = File::create(log)?;
        Command::new("systemd-run")
            .args(["--user", "--scope", "--quiet", "--collect"])
            .arg(format!("--unit={unit}"))
            .arg(format!("--slice={SLICE}"))
            .args(["-p", &format!("MemoryMax={cap}"), "-p", "MemorySwapMax=0", "--"])
            .args(argv)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .spawn()
            .context("starting systemd-run")
    }

    fn freeze(&self, unit: &str) -> Result<()> {
        systemctl(&["freeze", &format!("{unit}.scope")])
    }

    fn thaw(&self, unit: &str) -> Result<()> {
        systemctl(&["thaw", &format!("{unit}.scope")])
    }

    fn stop(&self, unit: &str) -> Result<()> {
        systemctl(&["stop", &format!("{unit}.scope")])
    }

    fn lock_dir(&self) -> PathBuf {
        crate::paths::runtime_dir().join("mightling-index")
    }
}

/// The kinds of run, which set the ceiling and the first-run floor (spec §6.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Kind {
    Universal,
    Static,
    Executing,
}

impl Kind {
    /// The need of a run with no recorded peak.
    pub fn floor(self) -> u64 {
        match self {
            Kind::Universal => 512 * MIB,
            Kind::Static => 3 * GIB,
            Kind::Executing => 8 * GIB,
        }
    }
}

/// `earlyoom's SIGTERM line + 4 GiB`: what admission never hands out.
pub fn reserve(host: &dyn Host) -> u64 {
    let percent = host.earlyoom_percent().unwrap_or(5.0);
    (host.mem_total() as f64 * percent / 100.0) as u64 + 4 * GIB
}

/// What a run needs: 1.2 × its recorded peak, or its kind's floor.
pub fn need(kind: Kind, recorded_peak_mb: u64) -> u64 {
    if recorded_peak_mb == 0 {
        kind.floor()
    } else {
        (recorded_peak_mb * MIB) * 6 / 5
    }
}

/// An admitted run: its cap, and the ledger lock held until its scope exists.
pub struct Admitted {
    pub cap: u64,
    _lock: File,
}

impl Admitted {
    /// Releases the ledger once the scope exists (its cap is then counted by the next admission).
    pub fn release(self) {}
}

/// Why a run was not admitted.
#[derive(Debug, Clone)]
pub struct Deferred {
    pub budget: u64,
    pub need: u64,
}

/// Admits one run against the host-wide budget (spec §6.4), under an exclusive lock on the ledger.
///
/// `budget = MemAvailable − reserve − Σ (cap − use)` over the live scopes; the run gets
/// `min(ceiling, budget)` and starts only if that covers its need. The slice's `MemoryMax` is set
/// to the sum of the live caps plus this one. Keep the returned [`Admitted`] until the scope has
/// been started, so a racing admission sees it.
pub fn admit(host: &dyn Host, need: u64, ceiling: u64) -> Result<std::result::Result<Admitted, Deferred>> {
    let dir = host.lock_dir();
    std::fs::create_dir_all(&dir)?;
    let lock = File::create(dir.join("admission.lock"))?;
    flock(&lock, true)?;
    let scopes = host.live_scopes();
    let promised: u64 = scopes.iter().map(|s| s.cap.min(host.mem_total()).saturating_sub(s.current)).sum();
    let budget = host.mem_available().saturating_sub(reserve(host)).saturating_sub(promised);
    let cap = ceiling.min(budget);
    if cap < need {
        return Ok(Err(Deferred { budget, need }));
    }
    let live: u64 = scopes.iter().map(|s| s.cap.min(host.mem_total())).sum();
    host.set_slice_limits(live + cap)?;
    Ok(Ok(Admitted { cap, _lock: lock }))
}

/// The host-wide lock that allows one executing run at a time; held for the run's life.
pub fn executing_lock(host: &dyn Host) -> Result<Option<File>> {
    let dir = host.lock_dir();
    std::fs::create_dir_all(&dir)?;
    let lock = File::create(dir.join("executing.lock"))?;
    Ok(flock(&lock, false).ok().map(|_| lock))
}

/// An exclusive lock on `file`, blocking or not: `flock(LOCK_EX)` on Linux, `LockFileEx` on
/// Windows, through the standard library.
pub fn flock(file: &File, block: bool) -> Result<()> {
    if block {
        if file.lock().is_err() {
            bail!("lock is held");
        }
    } else if file.try_lock().is_err() {
        bail!("lock is held");
    }
    Ok(())
}

/// A host whose memory, scopes and systemd are all in memory, for tests (here and in the crate's
/// integration tests): nothing it does reaches the real systemd.
pub mod fake {
    use super::*;
    use std::sync::Mutex;

    pub struct FakeHost {
        pub available: u64,
        pub total: u64,
        pub scopes: Mutex<Vec<LiveScope>>,
        pub slice_max: Mutex<Option<u64>>,
        pub started: Mutex<Vec<(String, u64, Vec<String>)>>,
        /// `freeze`, `thaw` and `stop` calls, in order.
        pub actions: Mutex<Vec<String>>,
        /// What a started scope runs instead of its command (`true` by default).
        pub stand_in: Vec<String>,
        pub dir: PathBuf,
    }

    impl FakeHost {
        pub fn new(dir: &Path, available_gib: u64) -> Self {
            FakeHost {
                available: available_gib * GIB,
                total: 121 * GIB,
                scopes: Mutex::new(Vec::new()),
                slice_max: Mutex::new(None),
                started: Mutex::new(Vec::new()),
                actions: Mutex::new(Vec::new()),
                stand_in: vec!["true".to_string()],
                dir: dir.to_path_buf(),
            }
        }
    }

    impl Host for FakeHost {
        fn mem_available(&self) -> u64 { self.available }
        fn mem_total(&self) -> u64 { self.total }
        fn earlyoom_percent(&self) -> Option<f64> { Some(5.0) }
        fn live_scopes(&self) -> Vec<LiveScope> { self.scopes.lock().unwrap().clone() }
        fn set_slice_limits(&self, memory_max: u64) -> Result<()> {
            *self.slice_max.lock().unwrap() = Some(memory_max);
            Ok(())
        }
        fn can_create_scopes(&self) -> bool { true }
        fn start_scope(&self, unit: &str, cap: u64, argv: &[String], _log: &Path) -> Result<Child> {
            self.started.lock().unwrap().push((unit.to_string(), cap, argv.to_vec()));
            self.scopes.lock().unwrap().push(LiveScope { unit: unit.to_string(), cap, current: 0 });
            Ok(Command::new(&self.stand_in[0]).args(&self.stand_in[1..]).spawn()?)
        }
        fn freeze(&self, unit: &str) -> Result<()> {
            self.actions.lock().unwrap().push(format!("freeze {unit}"));
            Ok(())
        }
        fn thaw(&self, unit: &str) -> Result<()> {
            self.actions.lock().unwrap().push(format!("thaw {unit}"));
            Ok(())
        }
        fn stop(&self, unit: &str) -> Result<()> {
            self.actions.lock().unwrap().push(format!("stop {unit}"));
            Ok(())
        }
        fn lock_dir(&self) -> PathBuf { self.dir.clone() }
    }
}

#[cfg(test)]
mod tests {
    use super::fake::FakeHost;
    use super::*;

    #[test]
    fn a_budget_below_the_floor_defers() {
        let dir = tempfile::tempdir().unwrap();
        // 121 GiB total, 5% earlyoom line: reserve is 6.05 + 4 = ~10.05 GiB.
        let host = FakeHost::new(dir.path(), 12);
        assert!(admit(&host, need(Kind::Static, 0), 4 * GIB).unwrap().is_err());
        assert!(admit(&host, need(Kind::Universal, 0), 4 * GIB).unwrap().is_ok());
    }

    #[test]
    fn live_scopes_take_what_they_may_still_grow_into() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 40);
        host.scopes.lock().unwrap().push(LiveScope { unit: "a".into(), cap: 20 * GIB, current: GIB });
        let admitted = admit(&host, need(Kind::Executing, 0), 40 * GIB).unwrap().unwrap();
        // 40 − 10.05 − 19 ≈ 10.95 GiB.
        assert!(admitted.cap > 10 * GIB && admitted.cap < 11 * GIB, "{}", admitted.cap);
        assert_eq!(*host.slice_max.lock().unwrap(), Some(20 * GIB + admitted.cap));
    }

    #[test]
    fn two_admissions_that_fit_alone_but_not_together_start_one() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 36);
        let first = admit(&host, need(Kind::Executing, 21_500), 40 * GIB).unwrap().unwrap();
        host.start_scope("mightling-index-a", first.cap, &[], Path::new("/dev/null")).unwrap();
        first.release();
        assert!(admit(&host, need(Kind::Executing, 21_500), 40 * GIB).unwrap().is_err());
    }

    #[test]
    fn admissions_raced_from_two_processes_start_exactly_one() {
        // Two threads stand in for two processes: the ledger lock is a real flock on a real file.
        let dir = tempfile::tempdir().unwrap();
        let host = std::sync::Arc::new(FakeHost::new(dir.path(), 36));
        let handles: Vec<_> = (0..2)
            .map(|i| {
                let host = host.clone();
                std::thread::spawn(move || match admit(host.as_ref(), need(Kind::Executing, 21_500), 40 * GIB).unwrap() {
                    Ok(admitted) => {
                        std::thread::sleep(std::time::Duration::from_millis(50));
                        host.start_scope(&format!("u{i}"), admitted.cap, &[], Path::new("/dev/null")).unwrap();
                        admitted.release();
                        true
                    }
                    Err(_) => false,
                })
            })
            .collect();
        let started: usize = handles.into_iter().map(|h| h.join().unwrap() as usize).sum();
        assert_eq!(started, 1);
    }

    #[test]
    fn first_runs_use_the_floor() {
        assert_eq!(need(Kind::Universal, 0), 512 * MIB);
        assert_eq!(need(Kind::Static, 0), 3 * GIB);
        assert_eq!(need(Kind::Executing, 0), 8 * GIB);
        assert_eq!(need(Kind::Executing, 1000), 1200 * MIB);
    }

    #[test]
    fn one_executing_run_on_the_host() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 100);
        let first = executing_lock(&host).unwrap();
        assert!(first.is_some());
        assert!(executing_lock(&host).unwrap().is_none());
        drop(first);
        // Another test forking at this moment holds a copy of the descriptor until its child
        // execs (the files are close-on-exec), so the release is observed within a moment.
        let released = (0..200).any(|_| {
            let got = executing_lock(&host).unwrap().is_some();
            if !got {
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
            got
        });
        assert!(released);
    }
}
