//! The host: memory, cgroups and systemd, behind a trait so tests never create a real scope, and
//! host-wide admission on top of it (spec §9).
//!
//! **The same budget as the code index, literally.** This is `puffin-code-rs/src/index/host.rs`
//! with ling-docs' kinds of run: the same slice (`puffin-index.slice`, whose limits hold for the
//! sum of every index run on the machine), the same ledger lock (`$XDG_RUNTIME_DIR/puffin-index/
//! admission.lock`), the same reserve (earlyoom's line plus 4 GiB) and the same formula, so a
//! code-index run and a docs run admit against one budget and never both take the last of it.
//! Unit names start with `puffin-index-`, so `server start`'s `systemctl --user stop
//! 'puffin-index-*'` before a model load and Night Shift's host check see them too.

use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use anyhow::{bail, Context, Result};

/// The slice every index run joins; its limits hold for the sum of the runs.
pub const SLICE: &str = "puffin-index.slice";
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
    let uid = unsafe { libc::getuid() };
    PathBuf::from(format!(
        "/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/puffin.slice/{SLICE}"
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
        crate::config::runtime_dir().join("puffin-index")
    }
}

/// The kinds of run, which set the ceiling and the first-run floor.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Kind {
    /// The extractor: PDFium on one file at a time. Its cap is the PDF cap of §7.1 (1 GiB):
    /// every corpus PDF peaked under 100 MB, and the two hostile files that pass it end `failed`.
    Extract,
    /// Chunking and embedding, one chunk at a time: 0.9 GB measured with the arctic model (§15.3).
    Embed,
}

impl Kind {
    /// The need of a run with no recorded peak.
    pub fn floor(self) -> u64 {
        match self {
            Kind::Extract => 256 * MIB,
            Kind::Embed => 1536 * MIB,
        }
    }

    /// The most a run of this kind is given.
    pub fn ceiling(self) -> u64 {
        match self {
            Kind::Extract => GIB,
            Kind::Embed => 2 * GIB,
        }
    }
}

/// `earlyoom's SIGTERM line + 4 GiB`: what admission never hands out.
pub fn reserve(host: &dyn Host) -> u64 {
    let percent = host.earlyoom_percent().unwrap_or(5.0);
    (host.mem_total() as f64 * percent / 100.0) as u64 + 4 * GIB
}

/// What a run needs: 1.2 × its recorded peak, or its kind's floor; never above its ceiling.
pub fn need(kind: Kind, recorded_peak_mb: u64) -> u64 {
    let need = if recorded_peak_mb == 0 { kind.floor() } else { (recorded_peak_mb * MIB) * 6 / 5 };
    need.min(kind.ceiling())
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

/// `flock(LOCK_EX)`, blocking or not.
pub fn flock(file: &File, block: bool) -> Result<()> {
    use std::os::unix::io::AsRawFd;
    let flags = libc::LOCK_EX | if block { 0 } else { libc::LOCK_NB };
    if unsafe { libc::flock(file.as_raw_fd(), flags) } != 0 {
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
        // 121 GiB total, 5% earlyoom line: reserve is 6.05 + 4 = ~10.05 GiB; 11 GiB leaves ~0.95.
        let host = FakeHost::new(dir.path(), 11);
        assert!(admit(&host, need(Kind::Embed, 0), Kind::Embed.ceiling()).unwrap().is_err());
        let admitted = admit(&host, need(Kind::Extract, 0), Kind::Extract.ceiling()).unwrap().unwrap();
        assert!(admitted.cap < GIB, "the cap is what the budget allows: {}", admitted.cap);
    }

    #[test]
    fn the_extractor_never_gets_more_than_the_pdf_cap() {
        let dir = tempfile::tempdir().unwrap();
        let host = FakeHost::new(dir.path(), 100);
        let admitted = admit(&host, need(Kind::Extract, 5000), Kind::Extract.ceiling()).unwrap().unwrap();
        assert_eq!(admitted.cap, GIB);
        assert_eq!(need(Kind::Extract, 5000), GIB, "a recorded peak never asks above the ceiling");
        assert_eq!(need(Kind::Embed, 900), 1080 * MIB);
    }

    #[test]
    fn live_scopes_of_any_index_take_what_they_may_still_grow_into() {
        let dir = tempfile::tempdir().unwrap();
        // A code-index run holds 20 GiB of cap and uses 1: the docs run sees ~40 − 10.05 − 19.
        let host = FakeHost::new(dir.path(), 40);
        host.scopes.lock().unwrap().push(LiveScope { unit: "puffin-index-code".into(), cap: 20 * GIB, current: GIB });
        let admitted = admit(&host, need(Kind::Embed, 0), Kind::Embed.ceiling()).unwrap().unwrap();
        assert_eq!(admitted.cap, 2 * GIB);
        assert_eq!(*host.slice_max.lock().unwrap(), Some(22 * GIB));
        drop(admitted); // releases the ledger lock
        let crowded = FakeHost::new(dir.path(), 30);
        crowded.scopes.lock().unwrap().push(LiveScope { unit: "puffin-index-code".into(), cap: 20 * GIB, current: GIB });
        assert!(admit(&crowded, need(Kind::Embed, 0), Kind::Embed.ceiling()).unwrap().is_err());
    }

    #[test]
    fn admissions_raced_from_two_processes_start_exactly_one() {
        let dir = tempfile::tempdir().unwrap();
        // Room for one embedder (≥1.5 GiB above the reserve) but not two.
        let host = std::sync::Arc::new(FakeHost::new(dir.path(), 13));
        let handles: Vec<_> = (0..2)
            .map(|i| {
                let host = host.clone();
                std::thread::spawn(move || match admit(host.as_ref(), need(Kind::Embed, 0), Kind::Embed.ceiling()).unwrap() {
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
    fn the_ledger_is_the_code_index_one() {
        assert_eq!(SLICE, "puffin-index.slice");
        assert!(SystemdHost.lock_dir().ends_with("puffin-index"));
    }
}
