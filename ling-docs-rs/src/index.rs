//! Indexing a collection (spec §6, §7, §9): outside the agent's sandbox only.
//!
//! 1. **Discovery and the change scan**, here: the walk reads metadata only (§15.8); a file whose
//!    size and modification time are unchanged is not opened; a new or changed file is sniffed
//!    and hashed, and a copy of a file already indexed is recorded as its duplicate. Files gone
//!    from disk lose their chunks in the same pass.
//! 2. **Extraction**, in a scope of `mightling-index.slice` admitted against the host-wide budget,
//!    capped at 1 GiB, under `choom -n 1000`, `nice` and `ionice`, inside bwrap with no network,
//!    the home folder hidden and the collection read-only. The worker reports each file before
//!    reading it; a file that takes longer than `docs_extract_timeout_s` or kills the worker is
//!    recorded `failed: …` and a new worker goes on with the rest.
//! 3. **Chunking and embedding**, in a second admitted scope with no network: chunks are written
//!    first (keyword search works at once), then embedded one at a time.
//!
//! A run that cannot be admitted, or meets a Night Shift or benchmark run, is deferred and says
//! why; `server start` stops these scopes before a model load, and the run is retried later.

use std::collections::HashMap;
use std::io::{BufRead, Seek};
use std::path::{Path, PathBuf};
use std::process::Child;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::collections::Collection;
use crate::config::{self, Settings};
use crate::discover;
use crate::extract::{Event, Job};
use crate::host::{self, Host, Kind};
use crate::sandbox::{self, Spec};
use crate::store;

/// How a collection's run ended.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Outcome {
    Ok,
    /// Not run now, and why: memory, a Night Shift run, the size cap, a missing model.
    Deferred(String),
    Failed(String),
}

impl Outcome {
    pub fn status(&self) -> String {
        match self {
            Outcome::Ok => "ok".into(),
            Outcome::Deferred(why) => format!("deferred: {why}"),
            Outcome::Failed(why) => format!("failed: {why}"),
        }
    }
}

/// What a run needs from its surroundings; tests swap the host and run without bwrap.
pub struct Env<'a> {
    pub host: &'a dyn Host,
    pub settings: Settings,
    pub user_home: PathBuf,
    /// This executable, which the scopes run as their worker.
    pub exe: PathBuf,
    pub lib_dir: PathBuf,
    pub model_dir: PathBuf,
    /// The OCR models (§20).
    pub ocr_dir: PathBuf,
    /// Wrap the workers in choom, nice, ionice and bwrap (false only in tests).
    pub sandbox: bool,
    /// Extra arguments for the index worker (tests: `--test-embedder`).
    pub worker_args: Vec<String>,
    /// How often a scope is polled.
    pub poll: Duration,
}

impl Env<'_> {
    pub fn real(host: &dyn Host) -> Result<Env<'_>> {
        Ok(Env {
            host,
            settings: Settings::load(),
            user_home: config::user_home(),
            exe: std::env::current_exe()?.canonicalize()?,
            lib_dir: config::lib_dir(),
            model_dir: config::model_dir(),
            ocr_dir: config::ocr_dir(),
            sandbox: true,
            worker_args: Vec::new(),
            poll: Duration::from_millis(200),
        })
    }
}

/// Whether a Night Shift or SWE-bench run holds `runner.lock` (§9). Read from `/proc/locks`
/// rather than by trying the lock, which for that instant would keep a starting run from taking it.
pub fn night_shift_active(night_dir: &Path) -> bool {
    lock_held(&night_dir.join("runner.lock"))
}

/// Whether any process holds a lock on `path`, from `/proc/locks`, without touching the lock.
pub fn lock_held(path: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    let Ok(meta) = std::fs::metadata(path) else { return false };
    let inode = meta.ino().to_string();
    std::fs::read_to_string("/proc/locks").unwrap_or_default().lines().any(|line| {
        // `1: FLOCK  ADVISORY  WRITE 1234 fd:01:5678 0 EOF`
        line.split_whitespace().nth(5).and_then(|id| id.rsplit(':').next()).is_some_and(|ino| ino == inode)
    })
}

/// Indexes every enabled collection, or one. Holds `docs/index.lock` for the whole run, so two
/// runs never write one database at once.
pub fn run(env: &Env, docs: &crate::collections::DocsToml, only: Option<&str>, rebuild: bool) -> Result<Vec<(String, Outcome)>> {
    store::create_private_dir(&config::docs_dir())?;
    let lock = std::fs::File::create(config::docs_dir().join("index.lock"))?;
    if host::flock(&lock, false).is_err() {
        bail!("another ling-docs index run is in progress");
    }
    let mut out = Vec::new();
    for collection in docs.enabled().filter(|c| only.is_none_or(|n| n == c.name)) {
        if rebuild {
            remove_database(&collection.name)?;
        }
        let outcome = index_collection(env, collection).unwrap_or_else(|e| Outcome::Failed(format!("{e:#}")));
        if let Ok(conn) = store::open_rw(&config::db_path(&collection.name)) {
            let _ = store::set_meta(&conn, "last_status", &outcome.status());
            let _ = store::set_meta(&conn, "last_run", &now().to_string());
        }
        out.push((collection.name.clone(), outcome));
    }
    if let Some(name) = only {
        if docs.get(name).is_none() {
            bail!("no collection named `{name}`");
        }
    }
    Ok(out)
}

/// Deletes a collection's database and its journal.
pub fn remove_database(name: &str) -> Result<()> {
    for suffix in ["", "-journal", "-wal", "-shm"] {
        let path = PathBuf::from(format!("{}{suffix}", config::db_path(name).display()));
        match std::fs::remove_file(&path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e).with_context(|| format!("removing {}", path.display())),
        }
    }
    Ok(())
}

pub fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// What the change scan decided.
#[derive(Debug, Default)]
pub struct Scan {
    pub new: usize,
    pub changed: usize,
    pub unchanged: usize,
    pub removed: usize,
    pub duplicates: usize,
    pub skipped: usize,
    /// Documents to extract: id, file to read, kind.
    pub jobs: Vec<Job>,
}

/// Brings the `documents` table in line with the disk (step 1).
pub fn scan(conn: &mut Connection, found: &[discover::Candidate]) -> Result<Scan> {
    let known = store::documents(conn)?;
    let mut out = Scan::default();
    let tx = conn.transaction()?;
    let on_disk: HashMap<&str, &discover::Candidate> = found.iter().map(|c| (c.rel.as_str(), c)).collect();
    for (path, row) in &known {
        if !on_disk.contains_key(path.as_str()) {
            store::delete_document(&tx, row.id)?;
            out.removed += 1;
        }
    }
    // Hashes already present, for duplicates: originals only.
    let mut by_hash: HashMap<String, i64> = HashMap::new();
    {
        let mut stmt = tx.prepare("SELECT sha256, id FROM documents WHERE sha256 IS NOT NULL AND dup_of IS NULL AND status IN ('ok', 'pending') ORDER BY id")?;
        for row in stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?.flatten() {
            by_hash.entry(row.0).or_insert(row.1);
        }
    }
    // Originals before their copies: a browser's `x (1).pdf` or a `Copy of x` is the duplicate,
    // whichever sorts first.
    let mut order: Vec<&discover::Candidate> = found.iter().collect();
    order.sort_by_key(|c| (looks_like_a_copy(&c.rel), c.rel.len(), c.rel.clone()));
    for candidate in order {
        let previous = known.get(&candidate.rel);
        // Re-read the row: a deletion above may have turned it pending.
        let current: Option<(i64, i64, i64, Option<String>, String)> = tx
            .query_row("SELECT id, size, mtime_ns, sha256, status FROM documents WHERE path = ?", [&candidate.rel], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))
            .ok();
        if let Some((id, size, mtime, _, status)) = &current {
            if *size == candidate.size as i64 && *mtime == candidate.mtime_ns && status != "pending" {
                out.unchanged += 1;
                continue;
            }
            if *size == candidate.size as i64 && *mtime == candidate.mtime_ns && status == "pending" {
                out.jobs.push(Job { id: *id, path: candidate.abs.clone(), kind: candidate.kind, max_pages: None });
                continue;
            }
        }
        if let Err(why) = discover::sniff(&candidate.abs, candidate.kind) {
            upsert(&tx, candidate, None, &format!("skipped: {why}"), None)?;
            out.skipped += 1;
            continue;
        }
        let sha = discover::hash(&candidate.abs)?;
        if let Some((id, _, _, Some(old_sha), status)) = &current {
            if *old_sha == sha && status == "ok" {
                tx.execute("UPDATE documents SET size = ?, mtime_ns = ? WHERE id = ?", params![candidate.size as i64, candidate.mtime_ns, id])?;
                out.unchanged += 1;
                continue;
            }
        }
        let own_id = current.as_ref().map(|c| c.0);
        if let Some(original) = by_hash.get(&sha).copied().filter(|o| Some(*o) != own_id) {
            if let Some(id) = own_id {
                store::clear_document(&tx, id)?;
            }
            upsert(&tx, candidate, Some(&sha), "duplicate", Some(original))?;
            out.duplicates += 1;
            continue;
        }
        let id = upsert(&tx, candidate, Some(&sha), "pending", None)?;
        by_hash.entry(sha).or_insert(id);
        if previous.is_some() { out.changed += 1 } else { out.new += 1 }
        out.jobs.push(Job { id, path: candidate.abs.clone(), kind: candidate.kind, max_pages: None });
    }
    tx.commit()?;
    Ok(out)
}

/// `x (1).pdf`, `x(2).md`, `x - Copy.txt`, `Copy of x.pdf`.
fn looks_like_a_copy(rel: &str) -> bool {
    let name = Path::new(rel).file_stem().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default();
    let numbered = name.ends_with(')') && name.rfind('(').is_some_and(|i| name[i + 1..name.len() - 1].chars().all(|c| c.is_ascii_digit()) && i + 2 < name.len());
    numbered || name.ends_with(" - copy") || name.ends_with(" copy") || name.starts_with("copy of ")
}

fn upsert(conn: &Connection, c: &discover::Candidate, sha: Option<&str>, status: &str, dup_of: Option<i64>) -> Result<i64> {
    conn.execute(
        "INSERT INTO documents(path, kind, size, mtime_ns, sha256, status, dup_of) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(path) DO UPDATE SET kind = ?2, size = ?3, mtime_ns = ?4, sha256 = ?5, status = ?6, dup_of = ?7",
        params![c.rel, c.kind.as_str(), c.size as i64, c.mtime_ns, sha, status, dup_of],
    )?;
    Ok(conn.query_row("SELECT id FROM documents WHERE path = ?", [&c.rel], |r| r.get(0))?)
}

/// Indexes one collection.
pub fn index_collection(env: &Env, collection: &Collection) -> Result<Outcome> {
    if night_shift_active(&config::night_dir()) {
        return Ok(Outcome::Deferred("a Night Shift or benchmark run holds the runner lock".into()));
    }
    let db = config::db_path(&collection.name);
    let size_gb = std::fs::metadata(&db).map(|m| m.len() as f64 / (1u64 << 30) as f64).unwrap_or(0.0);
    if size_gb > env.settings.max_index_gb {
        return Ok(Outcome::Deferred(format!("the index is {size_gb:.1} GB, over docs_max_index_gb ({})", env.settings.max_index_gb)));
    }
    let missing = if env.worker_args.iter().any(|a| a == "--test-embedder") { None } else { config::missing_runtime_at(&env.lib_dir, &env.model_dir) };
    if let Some(why) = missing {
        return Ok(Outcome::Deferred(why));
    }
    if !collection.root.is_dir() {
        return Ok(Outcome::Failed(format!("{} is not there", collection.root.display())));
    }
    let mut conn = store::open_rw(&db)?;
    // A kind whose extractor changed is read again; nothing else is (§7.5).
    store::mark_stale_extractors(&conn, &crate::extract::extractor_versions())?;
    let found = discover::walk(collection, &env.user_home, env.settings.ocr);
    let scan = scan(&mut conn, &found.candidates)?;
    store::set_meta(&conn, "skipped_unopened", &serde_json::to_string(&found.skipped)?)?;
    let missing_vectors: i64 = conn.query_row("SELECT count(*) FROM chunks WHERE vector IS NULL", [], |r| r.get(0))?;
    let pending: i64 = conn.query_row("SELECT count(*) FROM documents WHERE status = 'pending'", [], |r| r.get(0))?;
    let ocr_due = ocr_ready(env).is_none() && !store::awaiting_ocr(&conn)?.is_empty();
    // The index worker checks the staleness key (§7.5); it is started for that only when the
    // chunking changed or the model's files did (their size or time: the worker hashes them).
    let recorded = |key: &str| store::get_meta(&conn, key).ok().flatten();
    let key_due = recorded("chunking").is_some_and(|c| c != config::chunking_key())
        || recorded("model_stat").is_some_and(|s| s != crate::embed::files_stat(&crate::embed::model_files_in(&env.model_dir)));
    drop(conn);
    if scan.jobs.is_empty() && missing_vectors == 0 && pending == 0 && !ocr_due && !key_due {
        return Ok(Outcome::Ok);
    }
    if !env.host.can_create_scopes() {
        return Ok(Outcome::Deferred("no user systemd to cap the run's memory (indexing runs only where it can be capped)".into()));
    }
    let scratch = config::docs_dir().join(format!("run-{}-{}", collection.name, std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    store::create_private_dir(&scratch)?;
    let result = extract_and_embed(env, collection, &db, &scratch, &scan.jobs);
    // The workers' logs are kept (the last run's), for `status` to point at when a run fails.
    let logs = config::docs_dir().join("logs");
    if std::fs::create_dir_all(&logs).is_ok() {
        for entry in std::fs::read_dir(&scratch).into_iter().flatten().flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.ends_with(".log") {
                let _ = std::fs::copy(entry.path(), logs.join(format!("{}-{name}", collection.name)));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

fn extract_and_embed(env: &Env, collection: &Collection, db: &Path, scratch: &Path, jobs: &[Job]) -> Result<Outcome> {
    let events = scratch.join("events.jsonl");
    std::fs::write(&events, b"")?;
    let conn = store::open_rw(db)?;
    let recorded = |key: &str| store::get_meta(&conn, key).ok().flatten().and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
    let (extract_peak, embed_peak) = (recorded("extract_peak_mb"), recorded("embed_peak_mb"));
    drop(conn);
    if !jobs.is_empty() {
        match extract_all(env, &collection.name, &[collection.root.clone()], scratch, jobs, &events, extract_peak)? {
            Ok((peak, cap)) => record_peak(db, "extract", peak, cap)?,
            Err(outcome) => return Ok(outcome),
        }
    }
    match embed(env, &collection.name, db, scratch, &events, embed_peak)? {
        Ok((peak, cap)) => record_peak(db, "embed", peak, cap)?,
        Err(outcome) => return Ok(outcome),
    }
    // Then OCR, once the text layers are searchable (§20): a stopped OCR scope loses only its
    // own progress, and what it read is written and embedded like any other text.
    ocr_stage(env, collection, db, scratch)?;
    Ok(Outcome::Ok)
}

/// Why OCR cannot run here, if it cannot: switched off, or something not installed (tests use a
/// stand-in worker under `MIGHTLING_DOCS_TEST_OCR=fake`).
pub fn ocr_ready(env: &Env) -> Option<String> {
    if !env.settings.ocr {
        return Some("OCR is switched off (docs_ocr = false)".into());
    }
    if std::env::var("MIGHTLING_DOCS_TEST_OCR").as_deref() == Ok("fake") {
        return None;
    }
    crate::ocr::missing(&env.lib_dir, &env.ocr_dir)
}

/// The OCR stage: every document with pages OCR has not read, in an admitted OCR scope, then its
/// pages written into the document and embedded. A stage that cannot run, or is stopped, leaves the
/// rest for the next run; documents it read or could not read are not read again until they change.
fn ocr_stage(env: &Env, collection: &Collection, db: &Path, scratch: &Path) -> Result<()> {
    if ocr_ready(env).is_some() {
        return Ok(());
    }
    let conn = store::open_rw(db)?;
    let waiting = store::awaiting_ocr(&conn)?;
    let recorded = store::get_meta(&conn, "ocr_peak_mb").ok().flatten().and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
    drop(conn);
    let mut jobs: Vec<(i64, String)> = Vec::new();
    for (id, rel, kind, mut pages) in waiting {
        let Some(kind) = discover::Kind::parse(&kind).filter(|k| matches!(k, discover::Kind::Pdf | discover::Kind::Image)) else { continue };
        pages.truncate(env.settings.ocr_max_pages as usize);
        if pages.is_empty() {
            continue;
        }
        let job = crate::ocr::worker::OcrJob { id, path: collection.root.join(&rel), kind, pages };
        jobs.push((id, serde_json::to_string(&job)?));
    }
    if jobs.is_empty() {
        return Ok(());
    }
    let events = scratch.join("ocr-events.jsonl");
    std::fs::write(&events, b"")?;
    let ocr_dir = env.ocr_dir.canonicalize().unwrap_or_else(|_| env.ocr_dir.clone());
    let phase = Phase {
        worker: vec!["ocr-worker".into()],
        extra: vec!["--ocr-dir".into(), ocr_dir.to_string_lossy().into_owned(), "--threads".into(), env.settings.embed_threads.to_string()],
        kind: Kind::Ocr,
        letter: "o",
        what: "OCR",
        timeout: Duration::from_secs(env.settings.ocr_timeout_s),
        read_only: vec![ocr_dir.clone()],
        failed: |id, error| Event::OcrFailed { id, error },
    };
    let stopped = match run_phase(env, &phase, &collection.name, &[collection.root.clone()], scratch, &jobs, &events, recorded)? {
        Ok((peak, cap)) => {
            record_peak(db, "ocr", peak, cap)?;
            None
        }
        Err(outcome) => Some(outcome),
    };
    // What was read is written, whether or not the stage ran to its end.
    let read = std::fs::metadata(&events).map(|m| m.len()).unwrap_or(0) > 0;
    if read {
        let embed_peak = store::open_ro(db)?.and_then(|c| store::get_meta(&c, "embed_peak_mb").ok().flatten()).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
        match embed(env, &collection.name, db, scratch, &events, embed_peak)? {
            Ok((peak, cap)) => record_peak(db, "embed", peak, cap)?,
            // Not written: the documents keep waiting for OCR, and the next run reads them again.
            Err(outcome) => {
                let conn = store::open_rw(db)?;
                store::set_meta(&conn, "ocr_last_status", &format!("the OCR's text was not written: {}", outcome.status()))?;
                return Ok(());
            }
        }
    }
    let conn = store::open_rw(db)?;
    store::set_meta(&conn, "ocr_last_status", &stopped.map(|o| o.status()).unwrap_or_else(|| "ok".into()))?;
    Ok(())
}

fn record_peak(db: &Path, phase: &str, peak: u64, cap: u64) -> Result<()> {
    let conn = store::open_rw(db)?;
    if peak > 0 {
        store::set_meta(&conn, &format!("{phase}_peak_mb"), &(peak >> 20).to_string())?;
    }
    store::set_meta(&conn, &format!("{phase}_cap_mb"), &(cap >> 20).to_string())?;
    Ok(())
}

/// Read-only paths a worker needs: the executable and the libraries, with the targets of any
/// symlinks among them (an installed tree may link into a shared cache).
fn runtime_paths(env: &Env, with_model: bool) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut add = |p: &Path| {
        if let Ok(c) = p.canonicalize() {
            if !dirs.iter().any(|d| c.starts_with(d)) {
                dirs.push(c);
            }
        }
    };
    if let Some(dir) = env.exe.parent() {
        add(dir);
    }
    let mut roots = vec![env.lib_dir.clone()];
    if with_model {
        roots.push(env.model_dir.clone());
    }
    for root in roots {
        add(&root);
        // Every directory a link passes through (a Hugging Face cache links twice: the snapshot,
        // then the blob), so the chain resolves inside the sandbox too.
        for entry in std::fs::read_dir(&root).into_iter().flatten().flatten() {
            let mut hop = entry.path();
            for _ in 0..8 {
                let Ok(target) = std::fs::read_link(&hop) else { break };
                let target = if target.is_absolute() { target } else { hop.parent().unwrap_or(Path::new("/")).join(target) };
                if let Some(parent) = target.parent() {
                    add(parent);
                }
                hop = target;
            }
        }
    }
    dirs
}

/// The command a scope runs: the worker, inside bwrap, under choom, nice and ionice, writing the
/// cgroup's peak itself before it exits (the cgroup is gone once it has).
fn scope_argv(env: &Env, spec: Spec, peak_file: &Path) -> Vec<String> {
    if !env.sandbox {
        return spec.argv;
    }
    let mut spec = spec;
    let quoted: Vec<String> = spec.argv.iter().map(|a| sh_quote(a)).collect();
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

pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn base_env() -> Vec<(String, String)> {
    vec![("PATH".into(), "/usr/bin:/bin".into()), ("LANG".into(), "C.UTF-8".into()), ("OMP_NUM_THREADS".into(), "4".into())]
}

fn read_peak(path: &Path) -> u64 {
    std::fs::read_to_string(path).ok().and_then(|t| t.trim().parse().ok()).unwrap_or(0)
}

fn unit_name(collection: &str, phase: &str, n: usize) -> String {
    format!("mightling-index-docs-{collection}-{phase}{n}-{}", std::process::id())
}

/// Step 2: every job's file through the extractor, restarted after a file that hangs or kills it.
/// Returns the highest peak and the cap, or the outcome that stopped the run.
fn extract_all(env: &Env, name: &str, roots: &[PathBuf], scratch: &Path, jobs: &[Job], events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<(u64, u64), Outcome>> {
    let phase = Phase {
        worker: vec!["extract-worker".into()],
        extra: Vec::new(),
        kind: Kind::Extract,
        letter: "x",
        what: "the extractor",
        timeout: Duration::from_secs(env.settings.extract_timeout_s),
        read_only: Vec::new(),
        failed: |id, error| Event::Failed { id, error },
    };
    let ids: Vec<(i64, String)> = jobs.iter().map(|j| Ok((j.id, serde_json::to_string(j)?))).collect::<Result<_>>()?;
    run_phase(env, &phase, name, roots, scratch, &ids, events, recorded_peak_mb)
}

/// A worker run over files in admitted scopes, restarted after a file that hangs or kills it:
/// the extractor, or OCR (§20).
struct Phase {
    /// The worker's subcommand, then arguments after the jobs and events files.
    worker: Vec<String>,
    extra: Vec<String>,
    kind: Kind,
    letter: &'static str,
    what: &'static str,
    /// One file's time limit (OCR: one page's, each page's progress starting it again).
    timeout: Duration,
    read_only: Vec<PathBuf>,
    failed: fn(i64, String) -> Event,
}

/// Runs the phase's worker over `jobs` (id and JSON line each). Returns the highest peak and the
/// cap, or the outcome that stopped the run.
#[allow(clippy::too_many_arguments)]
fn run_phase(env: &Env, phase: &Phase, name: &str, roots: &[PathBuf], scratch: &Path, jobs: &[(i64, String)], events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<(u64, u64), Outcome>> {
    let mut remaining: Vec<(i64, String)> = jobs.to_vec();
    let mut peak_max = 0u64;
    let mut cap_used = 0u64;
    let mut attempt = 0usize;
    let mut offset = std::fs::metadata(events).map(|m| m.len()).unwrap_or(0);
    let timeout = phase.timeout;
    let kind = phase.kind;
    while !remaining.is_empty() {
        attempt += 1;
        let admitted = match host::admit(env.host, host::need(kind, recorded_peak_mb.max(peak_max >> 20)), kind.ceiling())? {
            Ok(admitted) => admitted,
            Err(_) => return Ok(Err(Outcome::Deferred(format!("memory: the host-wide budget cannot cover {} now", phase.what)))),
        };
        cap_used = admitted.cap;
        let jobs_file = scratch.join(format!("jobs-{}{attempt}.jsonl", phase.letter));
        let lines: Vec<&str> = remaining.iter().map(|(_, line)| line.as_str()).collect();
        std::fs::write(&jobs_file, lines.join("\n"))?;
        let peak_file = scratch.join(format!("peak-{}{attempt}", phase.letter));
        let mut argv = vec![env.exe.to_string_lossy().into_owned()];
        argv.extend(phase.worker.iter().cloned());
        argv.extend([jobs_file.to_string_lossy().into_owned(), events.to_string_lossy().into_owned(), "--lib-dir".into(), env.lib_dir.to_string_lossy().into_owned()]);
        argv.extend(phase.extra.iter().cloned());
        let spec = Spec {
            home: env.user_home.clone(),
            scratch: scratch.to_path_buf(),
            read_only: runtime_paths(env, false).into_iter().chain(phase.read_only.iter().cloned()).chain(roots.iter().cloned()).collect(),
            writable: Vec::new(),
            writable_outside_scratch: Vec::new(),
            env: base_env(),
            cwd: scratch.to_path_buf(),
            argv,
        };
        let unit = unit_name(name, phase.letter, attempt);
        let argv = scope_argv(env, spec, &peak_file);
        let log = scratch.join(format!("{}-{attempt}.log", if kind == Kind::Ocr { "ocr" } else { "extract" }));
        if std::env::var_os("MIGHTLING_DOCS_TRACE_SCOPES").is_some() {
            eprintln!("scope {unit}: {argv:?}");
        }
        let mut child = env.host.start_scope(&unit, admitted.cap, &argv, &log)?;
        admitted.release();
        let (finished, current, status) = watch(env, &mut child, &unit, events, &mut offset, timeout, phase.what)?;
        peak_max = peak_max.max(read_peak(&peak_file));
        remaining.retain(|j| !finished.contains(&j.0));
        if remaining.is_empty() {
            break;
        }
        // The worker stopped with files left: the one it was reading is the cause.
        match current {
            Some((id, why)) => {
                append_event(events, &(phase.failed)(id, why))?;
                remaining.retain(|j| j.0 != id);
            }
            None => {
                // It stopped before reading anything: the sandbox itself failed, or the scope
                // was stopped from outside (a model load). Nothing is blamed on a file.
                let tail = std::fs::read_to_string(&log).unwrap_or_default();
                let tail: String = tail.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" | ");
                return Ok(Err(match status {
                    Stop::Outside => Outcome::Deferred("stopped (a model load?)".into()),
                    _ => Outcome::Failed(format!("the {} sandbox did not start: {tail}", if kind == Kind::Ocr { "OCR" } else { "extraction" })),
                }));
            }
        }
        if matches!(status, Stop::Outside) {
            return Ok(Err(Outcome::Deferred("stopped (a model load?)".into())));
        }
    }
    Ok(Ok((peak_max, cap_used)))
}

/// How a worker ended.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Stop {
    Done,
    Killed,
    Crashed(i32),
    TimedOut,
    Outside,
}

/// Follows a worker's events until it exits or a file takes too long. Returns the ids it
/// finished, the id it was reading when it stopped (with why), and how it stopped.
fn watch(env: &Env, child: &mut Child, unit: &str, events: &Path, offset: &mut u64, timeout: Duration, what: &str) -> Result<(Vec<i64>, Option<(i64, String)>, Stop)> {
    let mut finished = Vec::new();
    let mut current: Option<i64> = None;
    let mut since = Instant::now();
    loop {
        for event in read_new(events, offset)? {
            if let Some(id) = event.finished() {
                finished.push(id);
                current = None;
            } else if let Event::Start { id } = event {
                current = Some(id);
            }
            // A start, an end or a page read: the time limit starts again.
            since = Instant::now();
        }
        if let Some(status) = child.try_wait()? {
            // Events written just before the exit.
            for event in read_new(events, offset)? {
                if let Some(id) = event.finished() {
                    finished.push(id);
                    current = None;
                } else if let Event::Start { id } = event {
                    current = Some(id);
                }
            }
            use std::os::unix::process::ExitStatusExt;
            if std::env::var_os("MIGHTLING_DOCS_TRACE_SCOPES").is_some() {
                eprintln!("scope {unit} ended: {status:?}, reading {current:?}, finished {}", finished.len());
            }
            let stop = match (status.code(), status.signal()) {
                (Some(0), _) => Stop::Done,
                (Some(143), _) | (_, Some(15)) => Stop::Outside,
                (Some(137), _) | (_, Some(9)) => Stop::Killed,
                (Some(code), _) => Stop::Crashed(code),
                (None, Some(sig)) => Stop::Crashed(128 + sig),
                (None, None) => Stop::Crashed(-1),
            };
            let why = match stop {
                Stop::Killed if what == "OCR" => "killed: over OCR's memory cap (2 GiB), or out of memory".to_string(),
                Stop::Killed => "killed: over the extractor's memory cap (1 GiB), or out of memory".to_string(),
                Stop::Crashed(code) => format!("{what} crashed on it (exit {code})"),
                _ => format!("{what} stopped while reading it"),
            };
            return Ok((finished, current.map(|id| (id, why)), stop));
        }
        if current.is_some() && since.elapsed() > timeout {
            let _ = env.host.stop(unit);
            let _ = child.kill();
            let _ = child.wait();
            for event in read_new(events, offset)? {
                if let Some(id) = event.finished() {
                    finished.push(id);
                    if current == Some(id) {
                        current = None;
                    }
                }
            }
            let why = if what == "OCR" { format!("timed out: a page took over {} s", timeout.as_secs()) } else { format!("timed out after {} s", timeout.as_secs()) };
            return Ok((finished, current.map(|id| (id, why)), Stop::TimedOut));
        }
        std::thread::sleep(env.poll);
    }
}

/// Events appended since `offset` (whole lines only).
fn read_new(path: &Path, offset: &mut u64) -> Result<Vec<Event>> {
    let mut file = std::fs::File::open(path)?;
    file.seek(std::io::SeekFrom::Start(*offset))?;
    let mut reader = std::io::BufReader::new(file);
    let mut out = Vec::new();
    loop {
        let mut line = String::new();
        let n = reader.read_line(&mut line)?;
        if n == 0 || !line.ends_with('\n') {
            break;
        }
        *offset += n as u64;
        if let Ok(event) = serde_json::from_str::<Event>(line.trim_end()) {
            out.push(event);
        }
    }
    Ok(out)
}

fn append_event(path: &Path, event: &Event) -> Result<()> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new().append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(event)?)?;
    Ok(())
}

/// Step 3: the index worker writes chunks and vectors.
fn embed(env: &Env, name: &str, db: &Path, scratch: &Path, events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<(u64, u64), Outcome>> {
    let mut started = match start_embed(env, name, db, scratch, events, recorded_peak_mb)? {
        Ok(started) => started,
        Err(outcome) => return Ok(Err(outcome)),
    };
    let status = loop {
        if let Some(status) = started.child.try_wait()? {
            break status;
        }
        std::thread::sleep(env.poll);
    };
    Ok(embed_ended(&started, status))
}

/// A running index worker, its cap and where it writes its peak and log.
pub struct Embedding {
    child: Child,
    cap: u64,
    peak_file: PathBuf,
    log: PathBuf,
}

/// Admits and starts the index worker (step 3) without waiting for it.
fn start_embed(env: &Env, name: &str, db: &Path, scratch: &Path, events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<Embedding, Outcome>> {
    let admitted = match host::admit(env.host, host::need(Kind::Embed, recorded_peak_mb), Kind::Embed.ceiling())? {
        Ok(admitted) => admitted,
        Err(_) => return Ok(Err(Outcome::Deferred("memory: the host-wide budget cannot cover the embedder now".into()))),
    };
    let peak_file = scratch.join("peak-e");
    let docs_dir = db.parent().unwrap_or(Path::new("/")).to_path_buf();
    let mut argv = vec![
        env.exe.to_string_lossy().into_owned(),
        "index-worker".into(),
        "--collection".into(),
        name.to_string(),
        "--db".into(),
        db.to_string_lossy().into_owned(),
        "--events".into(),
        events.to_string_lossy().into_owned(),
        "--threads".into(),
        env.settings.embed_threads.to_string(),
        "--lib-dir".into(),
        env.lib_dir.to_string_lossy().into_owned(),
        "--model-dir".into(),
        env.model_dir.to_string_lossy().into_owned(),
        "--send-below".into(),
        env.settings.ocr_send_below.to_string(),
    ];
    argv.extend(env.worker_args.iter().cloned());
    let spec = Spec {
        home: env.user_home.clone(),
        scratch: scratch.to_path_buf(),
        read_only: runtime_paths(env, true),
        writable: Vec::new(),
        writable_outside_scratch: vec![(docs_dir, "the collection's database".into())],
        env: base_env(),
        cwd: scratch.to_path_buf(),
        argv,
    };
    let unit = unit_name(name, "e", 1);
    let argv = scope_argv(env, spec, &peak_file);
    let log = scratch.join("embed.log");
    let cap = admitted.cap;
    let child = env.host.start_scope(&unit, cap, &argv, &log)?;
    admitted.release();
    Ok(Ok(Embedding { child, cap, peak_file, log }))
}

/// How an index worker's exit reads: its peak and cap, or the outcome that stopped it.
fn embed_ended(started: &Embedding, status: std::process::ExitStatus) -> std::result::Result<(u64, u64), Outcome> {
    let peak = read_peak(&started.peak_file);
    use std::os::unix::process::ExitStatusExt;
    if status.success() {
        return Ok((peak, started.cap));
    }
    let tail = std::fs::read_to_string(&started.log).unwrap_or_default();
    let tail: String = tail.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" | ");
    Err(match (status.code(), status.signal()) {
        (Some(143), _) | (_, Some(15)) => Outcome::Deferred("stopped (a model load?)".into()),
        (Some(137), _) | (_, Some(9)) => Outcome::Failed("the embedder was killed: over its memory cap, or out of memory".into()),
        _ => Outcome::Failed(format!("the embedder failed: {tail}")),
    })
}

/// An index worker still embedding when its caller answered (PDF_SEARCH §3.4): kept by the
/// caller, asked [`Background::finished`] now and then, stopped when the caller ends.
pub struct Background {
    embedding: Embedding,
    db: PathBuf,
    scratch: PathBuf,
    /// Held until the worker ends, so no other process indexes the same entry meanwhile.
    _lock: Option<std::fs::File>,
}

impl Background {
    /// Whether the worker has ended; once it has, its peak is recorded and its scratch removed.
    pub fn finished(&mut self) -> bool {
        match self.embedding.child.try_wait() {
            Ok(Some(status)) => {
                if let Ok((peak, cap)) = embed_ended(&self.embedding, status) {
                    let _ = record_peak(&self.db, "embed", peak, cap);
                }
                let _ = std::fs::remove_dir_all(&self.scratch);
                true
            }
            Ok(None) => false,
            Err(_) => true,
        }
    }

    /// Stops the worker (its vectors so far are kept: it commits every 32 chunks).
    pub fn stop(mut self) {
        let _ = self.embedding.child.kill();
        let _ = self.embedding.child.wait();
        let _ = std::fs::remove_dir_all(&self.scratch);
    }
}

/// What [`index_file`] left.
pub enum FileIndexed {
    /// The text is in the database; the embedding is complete, or still running in the
    /// background worker returned.
    Ready(Option<Background>),
    /// Nothing usable: deferred or failed, and why.
    Stopped(Outcome),
}

/// The document row of a one-document collection (PDF_SEARCH §3.4): its id, status and how many
/// chunks still lack a vector.
fn single_document(db: &Path) -> Result<Option<(i64, String, i64)>> {
    let Some(conn) = store::open_ro(db)? else { return Ok(None) };
    let row = conn.query_row("SELECT id, status FROM documents ORDER BY id LIMIT 1", [], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)));
    let Ok((id, status)) = row else { return Ok(None) };
    let missing: i64 = conn.query_row("SELECT count(*) FROM chunks WHERE vector IS NULL", [], |r| r.get(0))?;
    Ok(Some((id, status, missing)))
}

/// Indexes one file as the only document of the collection `name` (`pdf_search`'s ephemeral
/// index, PDF_SEARCH §3.4), through the same extractor scope, admission and index worker as a
/// collection. `label` is what citations name (a URL or a path); `file` is read once. Returns
/// as soon as the text is searchable and either the embedding has ended or `wait` has passed;
/// a worker still embedding comes back to the caller. An entry indexed before is reused: only
/// its missing vectors are embedded.
pub fn index_file(env: &Env, name: &str, label: &str, title: &str, file: &Path, max_pages: Option<u32>, wait: Duration) -> Result<FileIndexed> {
    let db = config::db_path(name);
    let dir = db.parent().unwrap_or(Path::new("/")).to_path_buf();
    store::create_private_dir(&dir)?;
    let missing = if env.worker_args.iter().any(|a| a == "--test-embedder") { None } else { config::missing_runtime_at(&env.lib_dir, &env.model_dir) };
    if let Some(why) = missing {
        return Ok(FileIndexed::Stopped(Outcome::Deferred(why)));
    }
    if !env.host.can_create_scopes() {
        return Ok(FileIndexed::Stopped(Outcome::Deferred("no user systemd to cap the run's memory".into())));
    }
    // One builder per entry across sessions: a second one waits for the first to have the text.
    let lock = std::fs::File::create(dir.join(format!("{name}.lock")))?;
    let started = Instant::now();
    let patience = Duration::from_secs(env.settings.extract_timeout_s + 60);
    while host::flock(&lock, false).is_err() {
        if single_document(&db)?.is_some_and(|(_, status, _)| status == "ok") {
            return Ok(FileIndexed::Ready(None)); // another session embeds it: search what is there
        }
        if started.elapsed() > patience {
            return Ok(FileIndexed::Stopped(Outcome::Deferred("another session is indexing this PDF".into())));
        }
        std::thread::sleep(env.poll);
    }
    let existing = single_document(&db)?;
    if let Some((_, status, 0)) = &existing {
        if status == "ok" {
            return Ok(FileIndexed::Ready(None));
        }
    }
    let scratch = dir.join(format!("run-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    store::create_private_dir(&scratch)?;
    let events = scratch.join("events.jsonl");
    std::fs::write(&events, b"")?;
    let recorded = |key: &str| -> u64 { store::open_ro(&db).ok().flatten().and_then(|c| store::get_meta(&c, key).ok().flatten()).and_then(|v| v.parse().ok()).unwrap_or(0) };
    let text_ready = matches!(&existing, Some((_, status, _)) if status == "ok");
    if !text_ready {
        let conn = store::open_rw(&db)?;
        let meta = std::fs::metadata(file)?;
        conn.execute(
            "INSERT INTO documents(path, kind, size, mtime_ns, status, title) VALUES (?1, 'pdf', ?2, 0, 'pending', ?3)
             ON CONFLICT(path) DO UPDATE SET status = 'pending', size = ?2, title = ?3",
            params![label, meta.len() as i64, title],
        )?;
        conn.execute("DELETE FROM documents WHERE path != ?", [label])?;
        let id: i64 = conn.query_row("SELECT id FROM documents WHERE path = ?", [label], |r| r.get(0))?;
        store::set_meta(&conn, "source", label)?;
        drop(conn);
        let job = Job { id, path: file.to_path_buf(), kind: discover::Kind::Pdf, max_pages };
        let root = file.parent().unwrap_or(Path::new("/")).to_path_buf();
        match extract_all(env, name, &[root], &scratch, &[job], &events, recorded("extract_peak_mb"))? {
            Ok((peak, cap)) => record_peak(&db, "extract", peak, cap)?,
            Err(outcome) => {
                let _ = std::fs::remove_dir_all(&scratch);
                return Ok(FileIndexed::Stopped(outcome));
            }
        }
        // A file the extractor could not read ends here, with its own reason.
        let mut offset = 0;
        for event in read_new(&events, &mut offset)? {
            if let Event::Failed { error, .. } = event {
                let conn = store::open_rw(&db)?;
                store::set_status(&conn, id, &format!("failed: {error}"))?;
                let _ = std::fs::remove_dir_all(&scratch);
                return Ok(FileIndexed::Stopped(Outcome::Failed(error)));
            }
        }
    }
    let mut embedding = match start_embed(env, name, &db, &scratch, &events, recorded("embed_peak_mb"))? {
        Ok(embedding) => embedding,
        Err(outcome) => {
            let _ = std::fs::remove_dir_all(&scratch);
            return Ok(FileIndexed::Stopped(outcome));
        }
    };
    let since = Instant::now();
    loop {
        if let Some(status) = embedding.child.try_wait()? {
            let _ = std::fs::remove_dir_all(&scratch);
            return Ok(match embed_ended(&embedding, status) {
                Ok((peak, cap)) => {
                    record_peak(&db, "embed", peak, cap)?;
                    FileIndexed::Ready(None)
                }
                // Stopped while embedding: what was written is still searchable by keyword.
                Err(outcome) => match single_document(&db)? {
                    Some((_, status, _)) if status == "ok" => FileIndexed::Ready(None),
                    _ => FileIndexed::Stopped(outcome),
                },
            });
        }
        let text_written = || single_document(&db).ok().flatten().is_some_and(|(_, status, _)| status == "ok");
        if since.elapsed() >= wait && text_written() {
            return Ok(FileIndexed::Ready(Some(Background { embedding, db, scratch, _lock: Some(lock) })));
        }
        // A worker that never writes the text (a stalled model load, a stuck database) must not
        // hold the caller, a single-threaded tool server, for the rest of the session.
        if since.elapsed() > Duration::from_secs(env.settings.extract_timeout_s) && !text_written() {
            let _ = embedding.child.kill();
            let _ = embedding.child.wait();
            let _ = std::fs::remove_dir_all(&scratch);
            return Ok(FileIndexed::Stopped(Outcome::Failed(format!("the index worker did not write the text within {} s", env.settings.extract_timeout_s))));
        }
        std::thread::sleep(env.poll);
    }
}

/// Reads one file through the extractor (scope, admission, sandbox and time limit as for
/// indexing) and stores nothing: `ling-docs extract` (PDF_SEARCH §4). The error is the
/// worker's one-line reason, or why the run could not start.
pub fn extract_one(env: &Env, file: &Path, max_pages: Option<u32>) -> Result<std::result::Result<crate::extract::Extracted, String>> {
    let scratch = std::env::temp_dir().join(format!("ling-docs-extract-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    store::create_private_dir(&scratch)?;
    let events = scratch.join("events.jsonl");
    std::fs::write(&events, b"")?;
    let file = file.canonicalize().with_context(|| format!("{} does not exist", file.display()))?;
    let kind = discover::Kind::of(&file.to_string_lossy()).unwrap_or(discover::Kind::Text);
    let job = Job { id: 1, path: file.clone(), kind, max_pages };
    let root = file.parent().unwrap_or(Path::new("/")).to_path_buf();
    let result = extract_all(env, "extract", &[root], &scratch, &[job], &events, 0);
    let mut offset = 0;
    let events = read_new(&events, &mut offset).unwrap_or_default();
    let _ = std::fs::remove_dir_all(&scratch);
    if let Err(outcome) = result? {
        return Ok(Err(outcome.status()));
    }
    for event in events {
        match event {
            Event::Done { result, .. } => return Ok(Ok(result)),
            Event::Failed { error, .. } => return Ok(Err(format!("failed: {error}"))),
            _ => {}
        }
    }
    Ok(Err("failed: the extractor gave no result".into()))
}

/// `ling-docs index-worker`: inside the embedder's sandbox. Writes each extracted document's
/// units and chunks, records failures, then embeds every chunk that has no vector yet, one at a
/// time, committing every few chunks so a stopped run keeps what it did.
pub fn index_worker(collection: &str, db: &Path, events: &Path, embedder: &dyn crate::embed::Embed, counter: &dyn crate::chunk::TokenCount, log: &mut dyn std::io::Write) -> Result<(usize, usize)> {
    index_worker_with(collection, db, events, embedder, counter, crate::ocr::SEND_BELOW as f64, log)
}

/// [`index_worker`] with the confidence under which an OCR page counts as poorly read.
pub fn index_worker_with(collection: &str, db: &Path, events: &Path, embedder: &dyn crate::embed::Embed, counter: &dyn crate::chunk::TokenCount, send_below: f64, log: &mut dyn std::io::Write) -> Result<(usize, usize)> {
    let mut conn = store::open_rw(db)?;
    let now = now();
    let mut written = 0usize;
    let file = std::fs::File::open(events)?;
    for line in std::io::BufReader::new(file).lines() {
        let Ok(event) = serde_json::from_str::<Event>(&line?) else { continue };
        match event {
            Event::Start { .. } | Event::Progress { .. } => {}
            Event::Failed { id, error } => store::set_status(&conn, id, &format!("failed: {error}"))?,
            Event::OcrFailed { id, error } => {
                // The text layer stays; the pages stay unread until the file changes.
                let record = serde_json::json!({ "read": 0, "low": 0, "failed": error }).to_string();
                conn.execute("UPDATE documents SET ocr = ? WHERE id = ?", params![record, id])?;
            }
            Event::Ocr { id, result } => {
                let Ok((title, needs)) = conn.query_row("SELECT title, needs_ocr FROM documents WHERE id = ? AND status = 'ok'", [id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))) else { continue };
                let read: std::collections::HashSet<u32> = result.pages.iter().map(|p| p.page).collect();
                let remaining: Vec<u32> = needs.split(',').filter_map(|p| p.parse().ok()).filter(|p| !read.contains(p)).collect();
                // The read pages' units take their places among the text layer's, by page.
                let mut units = store::units(&conn, id)?;
                units.retain(|u| u.page.is_none_or(|p| !read.contains(&p)));
                units.extend(result.units.iter().cloned());
                units.sort_by_key(|u| u.page.unwrap_or(0));
                let low = result.pages.iter().filter(|p| (p.confidence as f64) < send_below).count();
                let routes: std::collections::BTreeMap<String, usize> = result.pages.iter().fold(Default::default(), |mut m, p| {
                    *m.entry(p.route.clone()).or_default() += 1;
                    m
                });
                let record = serde_json::json!({ "read": result.pages.len(), "low": low, "routes": routes, "send_below": send_below,
                    "pages": result.pages.iter().map(|p| (p.page, (p.confidence * 1000.0).round() / 1000.0)).collect::<Vec<_>>() }).to_string();
                let chunks = crate::chunk::chunk(&units, counter, config::CHUNK_TOKENS, config::CHUNK_OVERLAP);
                store::write_ocr(&mut conn, id, &title, &units, &chunks, &remaining, &record)?;
                written += 1;
            }
            Event::Done { id, result } => {
                let Ok(path) = conn.query_row("SELECT path FROM documents WHERE id = ?", [id], |r| r.get::<_, String>(0)) else { continue };
                let title = if config::is_pdf_cache_name(collection) {
                    // A PDF of pdf_search: its own title, else the one its source gave.
                    let given: String = conn.query_row("SELECT title FROM documents WHERE id = ?", [id], |r| r.get(0)).unwrap_or_default();
                    if let Some(read) = result.pages_read {
                        store::set_meta(&conn, "pages_read", &read.to_string())?;
                    }
                    result.title.clone().filter(|t| !t.is_empty()).unwrap_or(given)
                } else {
                    Path::new(&path).file_stem().map(|s| s.to_string_lossy().replace('_', " ")).unwrap_or_default()
                };
                let chunks = crate::chunk::chunk(&result.units, counter, config::CHUNK_TOKENS, config::CHUNK_OVERLAP);
                store::write_document(&mut conn, id, &title, result.pages, &result.needs_ocr, &result.units, &chunks, now)?;
                written += 1;
            }
        }
    }
    writeln!(log, "{collection}: {written} documents written")?;
    reconcile(&mut conn, embedder, counter, log)?;
    let todo: Vec<(i64, String, String, String)> = conn
        .prepare("SELECT c.id, d.title, c.heading, c.text FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.vector IS NULL ORDER BY c.id")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let started = Instant::now();
    let mut done = 0usize;
    let mut tx = conn.transaction()?;
    for (id, title, heading, text) in &todo {
        let chunk = crate::chunk::Chunk { loc: String::new(), page_first: None, page_last: None, line_first: None, line_last: None, heading: heading.clone(), text: text.clone() };
        let vector = embedder.embed(&crate::chunk::embed_text(title, &chunk))?;
        tx.execute("UPDATE chunks SET vector = ? WHERE id = ?", params![crate::embed::to_blob(&vector), id])?;
        done += 1;
        if done % 32 == 0 {
            tx.commit()?;
            tx = conn.transaction()?;
        }
    }
    tx.commit()?;
    let seconds = started.elapsed().as_secs_f64();
    if done > 0 {
        store::set_meta(&conn, "embed_rate", &format!("{:.1}", done as f64 / seconds.max(1e-6)))?;
    }
    writeln!(log, "{collection}: {done} chunks embedded in {seconds:.1} s")?;
    Ok((written, done))
}

/// The staleness key (§7.5, the owner's decision of 2026-10-26): every chunk is embedded again
/// only when the embedding model changes (its name, its files' SHA-256, its dimension), and the
/// documents are chunked again, keeping the vectors of chunks whose text is unchanged, only when
/// the chunking changes. A new ling-docs binary changes neither. A part of the key the database
/// has not recorded (an older ling-docs wrote it) is taken as the current one, so recording a new
/// part never embeds anything again.
pub fn reconcile(conn: &mut Connection, embedder: &dyn crate::embed::Embed, counter: &dyn crate::chunk::TokenCount, log: &mut dyn std::io::Write) -> Result<()> {
    let recorded = |conn: &Connection, key: &str| store::get_meta(conn, key);
    let files = embedder.model_files();
    let stat = crate::embed::files_stat(&files);
    let sha = if files.is_empty() {
        None
    } else {
        match (recorded(conn, "model_stat")?, recorded(conn, "model_sha")?) {
            (Some(s), Some(sha)) if s == stat => Some(sha),
            _ => Some(crate::embed::files_sha256(&files)?),
        }
    };
    let differs = |conn: &Connection, key: &str, now: &str| -> Result<bool> { Ok(recorded(conn, key)?.is_some_and(|v| v != now)) };
    let mut why = Vec::new();
    if differs(conn, "model", embedder.name())? {
        why.push("the embedding model");
    }
    if differs(conn, "dim", &embedder.dim().to_string())? {
        why.push("the embedding dimension");
    }
    if let Some(sha) = &sha {
        if differs(conn, "model_sha", sha)? {
            why.push("the model's files");
        }
    }
    let model_changed = !why.is_empty();
    let chunking = config::chunking_key();
    if differs(conn, "chunking", &chunking)? {
        why.push("the chunking");
    }
    if !why.is_empty() {
        // Chunked again from the stored text (the tokenizer counts the tokens, so a new model
        // may move boundaries too); chunks whose text is unchanged keep their vectors.
        let documents: Vec<(i64, String)> = conn.prepare("SELECT id, title FROM documents WHERE status = 'ok' ORDER BY id")?.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        for (id, title) in &documents {
            let units = store::units(conn, *id)?;
            let chunks = crate::chunk::chunk(&units, counter, config::CHUNK_TOKENS, config::CHUNK_OVERLAP);
            store::replace_chunks(conn, *id, title, &chunks)?;
        }
        if model_changed {
            // Vectors of another model are never mixed with this one's.
            conn.execute("UPDATE chunks SET vector = NULL", [])?;
        }
        writeln!(log, "{} changed: {} documents chunked again{}", why.join(", "), documents.len(), if model_changed { ", every chunk embedded again" } else { "" })?;
    }
    store::set_meta(conn, "model", embedder.name())?;
    store::set_meta(conn, "dim", &embedder.dim().to_string())?;
    store::set_meta(conn, "chunking", &chunking)?;
    if let Some(sha) = sha {
        store::set_meta(conn, "model_stat", &stat)?;
        store::set_meta(conn, "model_sha", &sha)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::discover::{Candidate, Kind as FileKind};

    fn candidate(dir: &Path, rel: &str, body: &[u8]) -> Candidate {
        let abs = dir.join(rel);
        std::fs::write(&abs, body).unwrap();
        use std::os::unix::fs::MetadataExt;
        let meta = std::fs::metadata(&abs).unwrap();
        Candidate { rel: rel.into(), abs, size: meta.len(), mtime_ns: meta.mtime() * 1_000_000_000 + meta.mtime_nsec(), kind: FileKind::of(rel).unwrap() }
    }

    #[test]
    fn the_scan_finds_new_changed_removed_duplicate_and_lying_files() {
        let dir = tempfile::tempdir().unwrap();
        let mut conn = store::open_rw(&dir.path().join("db/c.db")).unwrap();
        let a = candidate(dir.path(), "a.md", b"# A\nalpha\n");
        let copy = candidate(dir.path(), "a (1).md", b"# A\nalpha\n");
        let fake = candidate(dir.path(), "movie.pdf", b"\x00\x00\x00ftyp");
        let s = scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap();
        assert_eq!((s.new, s.duplicates, s.skipped, s.jobs.len()), (1, 1, 1, 1));
        // A pending document is retried by the next scan; one marked ok is left alone.
        let id = s.jobs[0].id;
        assert_eq!(scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap().jobs.len(), 1);
        store::set_status(&conn, id, "ok").unwrap();
        let s = scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap();
        assert_eq!((s.unchanged, s.jobs.len()), (3, 0));
        // The original goes: its copy becomes the document and is read.
        let s = scan(&mut conn, &[copy.clone(), fake.clone()]).unwrap();
        assert_eq!(s.removed, 1);
        assert_eq!(s.jobs.len(), 1);
        assert_eq!(s.jobs[0].path, copy.abs);
        // A changed file is read again.
        store::set_status(&conn, s.jobs[0].id, "ok").unwrap();
        std::thread::sleep(Duration::from_millis(20));
        let changed = candidate(dir.path(), "a (1).md", b"# A\nalpha beta\n");
        let s = scan(&mut conn, &[changed, fake]).unwrap();
        assert_eq!((s.changed, s.jobs.len()), (1, 1));
    }

    #[test]
    fn the_worker_argv_is_sandboxed_without_network_and_writes_its_peak() {
        let dir = tempfile::tempdir().unwrap();
        let host = crate::host::fake::FakeHost::new(dir.path(), 100);
        let env = Env {
            host: &host,
            settings: Settings::default(),
            user_home: dir.path().join("home"),
            exe: PathBuf::from("/usr/bin/true"),
            lib_dir: dir.path().join("lib"),
            model_dir: dir.path().join("model"),
            ocr_dir: dir.path().join("ocr"),
            sandbox: true,
            worker_args: Vec::new(),
            poll: Duration::from_millis(1),
        };
        let spec = Spec { home: env.user_home.clone(), scratch: dir.path().join("s"), cwd: dir.path().join("s"), argv: vec!["/usr/bin/true".into(), "extract-worker".into()], ..Spec::default() };
        let argv = scope_argv(&env, spec.clone(), &dir.path().join("s/peak"));
        assert_eq!(&argv[..4], ["choom", "-n", "1000", "--"]);
        assert!(argv.contains(&"--unshare-net".to_string()));
        assert!(argv.last().unwrap().contains("memory.peak"));
        assert!(sandbox::unexpected_writable(&argv, &spec).is_empty());
        assert!(unit_name("documents", "x", 1).starts_with("mightling-index-docs-documents-x1-"), "server start stops mightling-index-*");
    }

    #[test]
    fn a_night_shift_lock_is_seen_without_taking_it() {
        let dir = tempfile::tempdir().unwrap();
        assert!(!night_shift_active(dir.path()));
        let file = std::fs::File::create(dir.path().join("runner.lock")).unwrap();
        assert!(!night_shift_active(dir.path()));
        host::flock(&file, false).unwrap();
        assert!(night_shift_active(dir.path()));
        drop(file);
        assert!(!night_shift_active(dir.path()));
    }
}
