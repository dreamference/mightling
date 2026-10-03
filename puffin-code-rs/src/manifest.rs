//! The records of each snapshot: `scip/manifest.json` for the exact layer and `scip/graph.json` for
//! the universal one (spec §6.2). A snapshot's commit, dirty files and file hashes are what §7.3's
//! changed set is computed against.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// A file as it was when a snapshot was taken.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileStamp {
    pub sha256: String,
    pub mtime_ns: i64,
    pub size: u64,
}

impl FileStamp {
    /// Stamps a file on disk.
    pub fn of(path: &Path) -> Option<FileStamp> {
        let meta = std::fs::metadata(path).ok()?;
        Some(FileStamp { sha256: sha256_file(path)?, mtime_ns: mtime_ns(&meta), size: meta.len() })
    }
}

/// Nanoseconds since the epoch of a file's modification time.
pub fn mtime_ns(meta: &std::fs::Metadata) -> i64 {
    use std::os::unix::fs::MetadataExt;
    meta.mtime() * 1_000_000_000 + meta.mtime_nsec()
}

/// The hex SHA-256 of a file's content.
pub fn sha256_file(path: &Path) -> Option<String> {
    let mut file = std::fs::File::open(path).ok()?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 64 << 10];
    loop {
        let n = file.read(&mut buffer).ok()?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
    }
    Some(hex(&hasher.finalize()))
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// One exact-layer snapshot: an indexer run over one root.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RunEntry {
    /// `scip-python`, `rust-analyzer`, …
    pub indexer: String,
    /// The indexed root, relative to the repository (`dreamference`, `puffin-code-rs`).
    pub root: String,
    /// Prepended to the store's document paths to make them repository-relative.
    pub path_prefix: String,
    /// The indexer's version.
    #[serde(default)]
    pub version: String,
    /// HEAD when the run started.
    #[serde(default)]
    pub commit: Option<String>,
    /// Files that differed from `commit` when the run started.
    #[serde(default)]
    pub dirty_files: Vec<String>,
    /// Every file of the root as the run saw it, repository-relative.
    #[serde(default)]
    pub file_hashes: BTreeMap<String, FileStamp>,
    #[serde(default)]
    pub started: String,
    #[serde(default)]
    pub duration_s: f64,
    #[serde(default)]
    pub peak_rss_mb: u64,
    #[serde(default)]
    pub peak_cap_bounded: bool,
    #[serde(default)]
    pub cap_mb: u64,
    /// `ok`, `failed: <reason>`, `deferred: memory`, `deferred: busy`, `deferred: model-start`, `deferred: stopped`.
    pub status: String,
    /// The query store's file name in the scip directory, when `status` is `ok`.
    #[serde(default)]
    pub store: String,
    /// Changes on every successful run; pages of one answer must share it (§7.2's cursor).
    #[serde(default)]
    pub run_id: String,
}

/// `scip/manifest.json`: every exact-layer run, keyed by `<indexer>:<root>`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Manifest {
    pub runs: BTreeMap<String, RunEntry>,
}

impl Manifest {
    pub fn load(scip_dir: &Path) -> Manifest {
        std::fs::read_to_string(scip_dir.join("manifest.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    /// Writes the manifest atomically (temporary file, then rename).
    pub fn save(&self, scip_dir: &Path) -> Result<()> {
        write_atomic(&scip_dir.join("manifest.json"), &serde_json::to_vec_pretty(self)?)
    }
}

/// `scip/graph.json`: the universal layer's snapshot. codebase-memory records file hashes but no
/// commit, so the session process writes one after every run (spec §6.2).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GraphSnapshot {
    pub commit: Option<String>,
    #[serde(default)]
    pub dirty_files: Vec<String>,
    #[serde(default)]
    pub finished: String,
    /// `ok` or `failed: <reason>` / `deferred: …`.
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub peak_rss_mb: u64,
    #[serde(default)]
    pub peak_cap_bounded: bool,
}

impl GraphSnapshot {
    pub fn load(scip_dir: &Path) -> Option<GraphSnapshot> {
        let text = std::fs::read_to_string(scip_dir.join("graph.json")).ok()?;
        serde_json::from_str(&text).ok()
    }

    pub fn save(&self, scip_dir: &Path) -> Result<()> {
        write_atomic(&scip_dir.join("graph.json"), &serde_json::to_vec_pretty(self)?)
    }
}

/// Writes `bytes` to `path` through a temporary file and a rename, so readers never see half.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let dir = path.parent().context("path has no parent")?;
    std::fs::create_dir_all(dir)?;
    let staging = dir.join(format!(".{}.{}", path.file_name().unwrap().to_string_lossy(), std::process::id()));
    std::fs::write(&staging, bytes)?;
    std::fs::rename(&staging, path)?;
    Ok(())
}

/// The current UTC time as RFC 3339, without a date library.
pub fn now_rfc3339() -> String {
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let days = secs / 86_400;
    let (h, m, s) = ((secs % 86_400) / 3600, (secs % 3600) / 60, secs % 60);
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(mo <= 2);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}
